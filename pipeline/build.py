"""収集 → 統合 → 検証 → 暗号化 → dist/ 生成。

  python -m pipeline.build                 直近14日を取り直してビルド
  python -m pipeline.build --since 2026-11-04   初回などの過去分取り込み
  python -m pipeline.build --sample        合成データで画面確認用にビルド（公開不可）

失敗した場合は dist/ を書き換えない。
"""

import argparse
from datetime import date, datetime
import json
import os
import shutil
import sys
import tempfile

from tqdm import tqdm

from . import collect_pc, collect_sleep, encrypt, sample
from .collect_pc import CollectError
from .config import (DAYS_DIR, DEFAULT_ACTIVITYWATCH_PROJECT, DIST_DIR, JST, MIN_PASSPHRASE_LENGTH,
                     RECOLLECT_DAYS, ROOT, SCHEMA_VERSION, SITE_DIR, TIMEZONE_NAME, load_env)
from .merge import build_record, previous_days


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


def collect_records(days, env):
    """実データを収集して data/days/ に保存し、保存済みの全日を返す。"""
    pc = collect_pc.collect(days, env.get("ACTIVITYWATCH_PROJECT") or DEFAULT_ACTIVITYWATCH_PROJECT)
    daily, sessions = collect_sleep.collect(env.get("NOTION_TOKEN"), env.get("NOTION_HEALTH_DATA_SOURCE_ID"),
                                            env.get("NOTION_SLEEP_SESSION_DATA_SOURCE_ID"))
    rebuilt = [build_record(day, pc.get(day.isoformat()), daily.get(day.isoformat()), sessions)
               for day in tqdm(days, desc="日別レコードを統合", unit="日", file=sys.stderr)]
    for record in rebuilt:
        atomic_write(DAYS_DIR / f"{record['date']}.json", json.dumps(record, ensure_ascii=False, indent=1) + "\n")
    records = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(DAYS_DIR.glob("*.json"))]
    return records, rebuilt


def write_dist(records, passphrase, is_sample):
    """site/ の静的ファイルと暗号化データを dist/ へ出力する。完成してから差し替える。"""
    dataset = {"schema": SCHEMA_VERSION, "timezone": TIMEZONE_NAME, "sample": is_sample,
               "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
               "days": sorted(records, key=lambda record: record["date"])}
    plaintext = json.dumps(dataset, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    envelope = encrypt.encrypt(plaintext, passphrase)
    envelope["sample"] = is_sample
    if encrypt.decrypt(envelope, passphrase) != plaintext:
        raise RuntimeError("暗号化の検証に失敗しました")
    staging = ROOT / "dist.tmp"
    if staging.exists():
        shutil.rmtree(staging)
    shutil.copytree(SITE_DIR, staging)
    (staging / ".nojekyll").write_text("", encoding="utf-8")
    atomic_write(staging / "data.enc.json", json.dumps(envelope, separators=(",", ":")) + "\n")
    publish_staging(staging, DIST_DIR)
    return len(plaintext)


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
            shutil.rmtree(path) if path.is_dir() else path.unlink()
    shutil.rmtree(staging, ignore_errors=True)


def describe(record):
    sleep, pc = record["sleep"], record["pc"]
    if sleep.get("upper") is None:
        sleep_text = {"none": "記録なし", "unsynced": "未同期"}.get(sleep["state"], "未取得")
    elif sleep["lower"] == sleep["upper"]:
        sleep_text = f"{sleep['upper']}分"
    else:
        sleep_text = f"{sleep['lower']}〜{sleep['upper']}分"
    pc_text = "未取得" if pc["state"] == "missing" else f"{pc['active'] // 60}分" + ("（参考値）" if pc["state"] == "caution" else "")
    return f"  {record['date']}  睡眠 {sleep_text} ／ PC {pc_text}"


def main(argv=None):
    parser = argparse.ArgumentParser(description="日々リズムのサイトをビルドする")
    parser.add_argument("--sample", action="store_true", help="合成データで画面確認用にビルドする（公開不可）")
    parser.add_argument("--since", type=date.fromisoformat, help="この日から前日までを取り直す")
    parser.add_argument("--days", type=int, default=RECOLLECT_DAYS, help="取り直す直近日数")
    args = parser.parse_args(argv)
    today = datetime.now(JST).date()
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
            records, rebuilt = collect_records(days, env)
        size = write_dist(records, passphrase, args.sample)
    except (CollectError, ValueError) as error:
        print(f"中断：{error}（dist/ は変更していません）", file=sys.stderr)
        return 1
    except OSError as error:
        print(f"中断：ファイルを書き込めませんでした：{error}（dist/ を確認してからビルドし直してください）", file=sys.stderr)
        return 1
    label = "合成データ" if args.sample else "実データ"
    print(f"ビルド完了（{label}）：{len(records)}日分、暗号化前 {size:,} バイト → dist/")
    for record in rebuilt:
        print(describe(record))
        for warning in record.get("warnings", []):
            print(f"    注意：{warning}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
