"""収集 → 統合 → 検証 → 暗号化 → dist/ 生成。

  python -m pipeline.build                 直近14日を取り直してビルド
  python -m pipeline.build --since 2026-11-04   初回などの過去分取り込み
  python -m pipeline.build --sample        合成データで画面確認用にビルド（dist-sample/ へ出力。公開不可）

失敗した場合は出力先を書き換えない。
"""

import argparse
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


def collect_records(days, env):
    """実データを収集して data/days/ に保存し、保存済みの全日を返す。"""
    pc = collect_pc.collect(days, env.get("ACTIVITYWATCH_PROJECT") or DEFAULT_ACTIVITYWATCH_PROJECT)
    daily, sessions = collect_sleep.collect(env.get("NOTION_TOKEN"), env.get("NOTION_HEALTH_DATA_SOURCE_ID"),
                                            env.get("NOTION_SLEEP_SESSION_DATA_SOURCE_ID"), days)
    rebuilt = [build_record(day, pc.get(day.isoformat()), daily.get(day.isoformat()), sessions)
               for day in tqdm(days, desc="日別レコードを統合", unit="日", file=sys.stderr)]
    for record in rebuilt:
        atomic_write(DAYS_DIR / f"{record['date']}.json", json.dumps(record, ensure_ascii=False, indent=1) + "\n")
    return within(load_saved(), start_date(env)), rebuilt


def write_dist(records, passphrase, is_sample, today, start=None):
    """site/ の静的ファイルと暗号化データを出力する。完成してから差し替える。

    実データは dist/、合成データは dist-sample/ へ出す。公開処理が読むのは dist/ だけなので、
    画面確認用のビルドが公開用の一式を上書きすることはない。
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
    for name in STATIC_FILES:   # 公開してよいファイルだけを写す。site/ にほかのファイルがあっても持ち出さない
        shutil.copyfile(SITE_DIR / name, staging / name)
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


def publish_staging(staging, target):
    """staging の中身を target へ1ファイルずつ置き換える。

    target フォルダ自体は消さない。プレビュー用サーバーなどがフォルダを開いていると
    Windows では削除に失敗し、中身だけが消えた状態で止まるため。
    """
    target.mkdir(parents=True, exist_ok=True)
    names = sorted(path.name for path in staging.iterdir())
    for name in names:
        os.replace(staging / name, target / name)
    for path in target.iterdir():
        if path.name not in names:
            remove_tree(path) if path.is_dir() else path.unlink()
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
    target = "dist-sample/" if args.sample else "dist/"
    try:
        if args.sample:
            records, rebuilt, passphrase = sample.generate(today), [], sample.PASSPHRASE
        else:
            env = load_env()
            passphrase = env.get("SITE_PASSPHRASE", "")
            if len(encrypt.normalize(passphrase)) < MIN_PASSPHRASE_LENGTH:
                raise CollectError(f".env の SITE_PASSPHRASE を{MIN_PASSPHRASE_LENGTH}文字以上で設定してください")
            if args.since:
                if args.since >= today:
                    raise CollectError("--since には過去の日付を指定してください")
                days = previous_days(today, (today - args.since).days)
            else:
                days = previous_days(today, max(args.days, 1))
            start = start_date(env)
            days = [day for day in days if start is None or day >= start]
            records, rebuilt = collect_records(days, env)
        size = write_dist(records, passphrase, args.sample, today, start)
    except (CollectError, ValueError) as error:
        print(f"中断：{error}（{target} は変更していません）", file=sys.stderr)
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
