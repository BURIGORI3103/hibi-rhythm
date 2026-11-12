"""dist/ を gh-pages ブランチへ公開する。

毎回、履歴を持たない単一コミットを force push する。古い暗号文をブランチの履歴に溜めないため。

dist/ をまず作業用フォルダへ写し、その写しを検査して、同じ写しを送信する。検査のあとで
dist/ が書き換わっても、検査していない内容が送信されることはない。

  python -m pipeline.deploy --dry-run   送信せず、検査と公開されるファイルの表示だけ
  python -m pipeline.deploy             公開
"""

import argparse
from datetime import datetime
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from . import encrypt
from .build import start_date
from .config import (DATA_FILE, DIST_DIR, JST, PUBLISH_FILES, ROOT, SCHEMA_VERSION, SITE_DIR, STATIC_FILES,
                     TIMEZONE_NAME, load_env)
from .merge import validate_dataset

BRANCH = "gh-pages"


def git(*args, cwd):
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8")
    if result.returncode != 0:
        raise RuntimeError(f"git {args[0]} に失敗：{result.stderr.strip().splitlines()[-1] if result.stderr.strip() else '原因不明'}")
    return result.stdout.strip()


def inspect(folder, passphrase, today, start=None, site=SITE_DIR):
    """folder が「公開してよい一式」であることを確かめる。そうでなければ RuntimeError。

    1. ファイルは許可リストと完全に一致する（余分も不足も不可。フォルダやリンクも不可）
    2. 画面のファイルは site/ と1バイトも違わない
    3. データは所定の暗号形式で、合成データではない（平文のJSONはここで弾かれる）
    4. いまの合言葉で復号でき、中身の全レコードが検証を通る
    """
    entries = {path.name: path for path in folder.iterdir()}
    odd = sorted(name for name, path in entries.items() if path.is_symlink() or not path.is_file())
    extra, missing = sorted(set(entries) - PUBLISH_FILES), sorted(PUBLISH_FILES - set(entries))
    if odd or extra or missing:
        parts = [f"{label}：{'、'.join(names)}" for label, names in
                 (("ファイル以外", odd), ("想定外", extra), ("不足", missing)) if names]
        raise RuntimeError(f"公開してよいファイルの一覧と一致しません（{' ／ '.join(parts)}）")
    for name in STATIC_FILES:
        if entries[name].read_bytes() != (site / name).read_bytes():
            raise RuntimeError(f"{name} が site/ と一致しません。ビルドし直してください")
    if entries[".nojekyll"].stat().st_size:
        raise RuntimeError(".nojekyll に内容があります")
    try:
        envelope = json.loads(entries[DATA_FILE].read_text(encoding="utf-8"))
        encrypt.check_envelope(envelope)
    except ValueError as error:
        raise RuntimeError(f"{DATA_FILE} を公開できません：{error}") from None
    try:
        dataset = json.loads(encrypt.decrypt(envelope, passphrase))
    except ValueError:
        raise RuntimeError(f"{DATA_FILE} を .env の合言葉で復号できません。ビルドし直してください") from None
    if (not isinstance(dataset, dict) or dataset.get("sample") is not False
            or dataset.get("schema") != SCHEMA_VERSION or dataset.get("timezone") != TIMEZONE_NAME
            or not isinstance(dataset.get("days"), list)):
        raise RuntimeError(f"{DATA_FILE} の中身が公開用の形式ではありません（合成データの可能性があります）")
    try:
        validate_dataset(dataset["days"], today, start)
    except ValueError as error:
        raise RuntimeError(f"{DATA_FILE} の記録が検証を通りません：{error}") from None
    return len(dataset["days"])


def main(argv=None):
    parser = argparse.ArgumentParser(description="dist/ を gh-pages へ公開する")
    parser.add_argument("--dry-run", action="store_true", help="送信せず内容だけ確認する")
    args = parser.parse_args(argv)
    try:
        if not (DIST_DIR / DATA_FILE).exists():
            raise RuntimeError(f"dist/{DATA_FILE} がありません。先に python -m pipeline.build を実行してください")
        env = load_env()
        remote = git("remote", "get-url", "origin", cwd=ROOT)
        name = git("config", "user.name", cwd=ROOT)
        email = git("config", "user.email", cwd=ROOT)
        with tempfile.TemporaryDirectory(prefix="hibi-deploy-") as folder:
            work = Path(folder) / "site"
            shutil.copytree(DIST_DIR, work)
            count = inspect(work, env.get("SITE_PASSPHRASE", ""), datetime.now(JST).date(), start_date(env))
            git("init", "--quiet", "--initial-branch", BRANCH, cwd=work)
            git("add", "--all", cwd=work)
            message = f"公開データを更新 {datetime.now(JST).isoformat(timespec='minutes')}"
            git("-c", f"user.name={name}", "-c", f"user.email={email}", "commit", "--quiet", "-m", message, cwd=work)
            files = git("ls-files", cwd=work).splitlines()
            if set(files) != PUBLISH_FILES or len(files) != len(PUBLISH_FILES):
                raise RuntimeError("コミットされるファイルが、公開してよいファイルの一覧と一致しません")
            if args.dry_run:
                print(f"確認のみ（未送信）：検査を通過。{count}日分、{len(files)}ファイル → {remote} の {BRANCH}")
                for file in files:
                    print(f"  {file}")
                return 0
            git("push", "--force", remote, f"{BRANCH}:{BRANCH}", cwd=work)
    except (RuntimeError, OSError) as error:
        print(f"中断：{error}（公開中のサイトは変更していません）", file=sys.stderr)
        return 1
    print(f"公開完了：{count}日分、{len(files)}ファイルを {BRANCH} へ送信しました")
    return 0


if __name__ == "__main__":
    sys.exit(main())
