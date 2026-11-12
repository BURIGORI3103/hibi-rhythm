"""収集 → 統合 → 検証 → 暗号化 → dist/ 生成。

  python -m pipeline.build                 直近14日を取り直してビルド
  python -m pipeline.build --since 2026-11-04   初回などの過去分取り込み
  python -m pipeline.build --sample        合成データで画面確認用にビルド（dist-sample/ へ出力。公開不可）

失敗した場合は出力先を書き換えない。
"""

import argparse
from contextlib import contextmanager
from datetime import date, datetime
import json
import os
import shutil
import stat
import sys
import tempfile

from tqdm import tqdm

from . import collect_pc, collect_sleep, encrypt, sample
from .collect_pc import CollectError
from .config import (DATA_FILE, DAYS_DIR, DEFAULT_ACTIVITYWATCH_PROJECT, DIST_DIR, JST, MIN_PASSPHRASE_LENGTH,
                     RECOLLECT_DAYS, SAMPLE_DIST_DIR, SCHEMA_VERSION, SITE_DIR, STATIC_FILES, TIMEZONE_NAME,
                     load_env)
from .merge import build_record, previous_days, refresh, sleep_range, validate_dataset


def atomic_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(prefix=".tmp-", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def start_date(env):
    """.env の START_DATE（YYYY-MM-DD）。これより前の日は取り込まず、公開もしない。未設定なら None。"""
    value = (env.get("START_DATE") or "").strip()
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        raise CollectError(f".env の START_DATE は YYYY-MM-DD 形式で書いてください：{value!r}") from None


def within(records, start):
    """開始日より前のレコードを除く。"""
    return [record for record in records if start is None or record["date"] >= start.isoformat()]


def load_saved(folder=DAYS_DIR):
    """data/days/ の保存済みレコードを読む。ファイル名と中の日付が食い違うものは受け付けない。"""
    records = []
    for path in sorted(folder.glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            raise ValueError(f"保存済みレコード {path.name} を読めません（JSONとして壊れています）") from None
        if not isinstance(record, dict) or record.get("date") != path.stem:
            raise ValueError(f"保存済みレコード {path.name} のファイル名と中の日付が一致しません")
        records.append(refresh(record))
    return records


def collect_records(days, env, today=None):
    """実データを収集して data/days/ に保存し、保存済みの全日を返す。"""
    today = today or datetime.now(JST).date()
    start = start_date(env)
    saved = within(load_saved(DAYS_DIR), start)
    validate_dataset(saved, today, start)
    pc = collect_pc.collect(days, env.get("ACTIVITYWATCH_PROJECT") or DEFAULT_ACTIVITYWATCH_PROJECT)
    daily, sessions = collect_sleep.collect(env.get("NOTION_TOKEN"), env.get("NOTION_HEALTH_DATA_SOURCE_ID"),
                                            env.get("NOTION_SLEEP_SESSION_DATA_SOURCE_ID"), days)
    rebuilt = [build_record(day, pc.get(day.isoformat()), daily.get(day.isoformat()), sessions)
               for day in tqdm(days, desc="日別レコードを統合", unit="日", file=sys.stderr)]
    validate_dataset(rebuilt, today, start)
    records = {record["date"]: record for record in saved}
    for record in rebuilt:
        atomic_write(DAYS_DIR / f"{record['date']}.json", json.dumps(record, ensure_ascii=False, indent=1) + "\n")
        records[record["date"]] = record
    return list(records.values()), rebuilt


def write_dist(records, passphrase, is_sample, today, start=None):
    """site/ の静的ファイルと暗号化データを出力する。完成してから差し替える。

    実データは dist/、合成データは dist-sample/ へ出す。公開処理が読むのは dist/ だけなので、
    画面確認用のビルドが公開用の一式を上書きすることはない。呼び出し元が出力先をロックする。
    """
    validate_dataset(records, today, start)   # 今回作った日だけでなく、保存済みの日も含めた全件
    dataset = {"schema": SCHEMA_VERSION, "timezone": TIMEZONE_NAME, "sample": is_sample,
               "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
               "days": sorted(records, key=lambda record: record["date"])}
    plaintext = json.dumps(dataset, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    envelope = encrypt.encrypt(plaintext, passphrase)
    envelope["sample"] = is_sample
    if encrypt.decrypt(envelope, passphrase) != plaintext:
        raise RuntimeError("暗号化の検証に失敗しました")
    target = SAMPLE_DIST_DIR if is_sample else DIST_DIR
    staging = target.with_name(target.name + ".tmp")
    remove_tree(staging)
    staging.mkdir()
    for name in STATIC_FILES:   # 公開してよいファイルだけを写す
        source = SITE_DIR / name
        if source.is_symlink() or not source.is_file():
            raise OSError(f"site/{name} が通常のファイルではありません")
        shutil.copyfile(source, staging / name)
    (staging / ".nojekyll").write_text("", encoding="utf-8")
    atomic_write(staging / DATA_FILE, json.dumps(envelope, separators=(",", ":")) + "\n")
    publish_staging(staging, target)
    return len(plaintext)


def remove_tree(path):
    """フォルダを削除する。Windows では読み取り専用属性が付いていると消せないため、外してから消す。"""
    def retry_writable(function, name, _error):
        os.chmod(name, stat.S_IWRITE)
        function(name)
    if path.exists():
        shutil.rmtree(path, onexc=retry_writable)


@contextmanager
def output_lock(target):
    """ビルドと公開用コピーを排他し、強制終了時はOSにロックを解放させる。"""
    lock_path = target.with_name(target.name + ".lock")
    with lock_path.open("a+b") as lock:
        try:
            if os.name == "nt":
                import msvcrt
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise OSError(f"{target.name}/ を更新または確認している処理があります") from error
        yield


def restore_previous(staging, target):
    """未完了の置き換えを元に戻す。復元自体が中断しても再試行できる。"""
    if any(path.is_symlink() or path.is_junction() for path in (staging, target)):
        raise OSError("出力先または一時フォルダがリンクです")
    pending = staging / ".pending"
    if not pending.exists():
        return
    manifest = json.loads(pending.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or any(
            not isinstance(manifest.get(key), list) or
            any(not isinstance(name, str) or not name or name in (".", "..") or
                any(char in name for char in "/\\:") for name in manifest[key])
            for key in ("replace", "existing")):
        raise ValueError("出力の復元情報が壊れています")
    previous = staging / ".previous"
    for name in manifest["replace"]:
        if (staging / name).exists():
            continue  # まだ置き換えていない
        saved = previous / name
        if saved.exists() or saved.is_symlink():
            os.replace(saved, target / name)
        elif name not in manifest["existing"]:
            (target / name).unlink(missing_ok=True)
    for saved in (staging / ".removed").iterdir():
        os.replace(saved, target / saved.name)
    pending.unlink()


def publish_staging(staging, target):
    """target フォルダを残し、置き換え途中で失敗した場合は旧版を復元する。"""
    target.mkdir(parents=True, exist_ok=True)
    names = sorted(path.name for path in staging.iterdir())
    existing = {path.name: path for path in target.iterdir()}
    previous, removed = staging / ".previous", staging / ".removed"
    previous.mkdir()
    removed.mkdir()
    for name in existing.keys() & set(names):
        shutil.copy2(existing[name], previous / name, follow_symlinks=False)
    pending = staging / ".pending"
    atomic_write(pending, json.dumps({"replace": names, "existing": list(existing)}) + "\n")
    try:
        for name in names:
            os.replace(staging / name, target / name)
        for name in existing.keys() - set(names):
            os.replace(existing[name], removed / name)
        pending.unlink()
    except (OSError, KeyboardInterrupt):
        restore_previous(staging, target)
        raise
    remove_tree(staging)


def describe(record):
    sleep, pc = record["sleep"], record["pc"]
    span = sleep_range(sleep)
    if span is None:
        sleep_text = {"none": "記録なし", "unsynced": "未同期", "missing": "未取得"}.get(sleep["state"], "一部欠測")
    elif span[0] == span[1]:
        sleep_text = f"{span[1]}分"
    else:
        sleep_text = f"{span[0]}〜{span[1]}分"
    if pc["state"] in ("missing", "none"):
        pc_text = "未取得" if pc["state"] == "missing" else "記録なし"
    elif pc["state"] == "partial" and pc["active"] == 0:
        pc_text = "一部欠測"
    else:
        pc_text = f"{pc['active'] // 60}分" + {"caution": "（参考値）", "partial": "以上（一部欠測）"}.get(pc["state"], "")
    return f"  {record['date']}  睡眠 {sleep_text} ／ PC {pc_text}"


def main(argv=None):
    parser = argparse.ArgumentParser(description="日々リズムのサイトをビルドする")
    parser.add_argument("--sample", action="store_true", help="合成データで画面確認用にビルドする（公開不可）")
    parser.add_argument("--since", type=date.fromisoformat, help="この日から前日までを取り直す")
    parser.add_argument("--days", type=int, default=RECOLLECT_DAYS, help="取り直す直近日数")
    args = parser.parse_args(argv)
    today = datetime.now(JST).date()
    start = None
    output = SAMPLE_DIST_DIR if args.sample else DIST_DIR
    target = output.name + "/"
    try:
        with output_lock(output):
            restore_previous(output.with_name(output.name + ".tmp"), output)
            if args.sample:
                records, rebuilt, passphrase = sample.generate(today), [], sample.PASSPHRASE
            else:
                env = load_env()
                passphrase = env.get("SITE_PASSPHRASE", "")
                if len(encrypt.normalize(passphrase)) < MIN_PASSPHRASE_LENGTH:
                    raise CollectError(f".env の SITE_PASSPHRASE を{MIN_PASSPHRASE_LENGTH}文字以上で設定してください")
                if args.since and args.since >= today:
                    raise CollectError("--since には過去の日付を指定してください")
                count = (today - args.since).days if args.since else max(args.days, 1)
                start = start_date(env)
                days = [day for day in previous_days(today, count) if start is None or day >= start]
                records, rebuilt = collect_records(days, env, today)
            size = write_dist(records, passphrase, args.sample, today, start)
    except (CollectError, ValueError) as error:
        print(f"中断：{error}（{target} に新版は出力していません）", file=sys.stderr)
        return 1
    except OSError as error:
        print(f"中断：ファイルを書き込めませんでした：{error}（{target} を確認してからビルドし直してください）", file=sys.stderr)
        return 1
    label = "合成データ" if args.sample else "実データ"
    print(f"ビルド完了（{label}）：{len(records)}日分、暗号化前 {size:,} バイト → {target}")
    for record in rebuilt:
        print(describe(record))
        for warning in record.get("warnings", []):
            print(f"    注意：{warning}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
