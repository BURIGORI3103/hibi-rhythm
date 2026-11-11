"""dist/ を gh-pages ブランチへ公開する。

毎回、履歴を持たない単一コミットを force push する。古い暗号文をリポジトリの履歴に
溜めないためで、合言葉を変更したあとに旧データが残らない。

  python -m pipeline.deploy --dry-run   送信せず、公開されるファイルだけ表示
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

from .config import DIST_DIR, JST, ROOT

BRANCH = "gh-pages"


def git(*args, cwd):
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8")
    if result.returncode != 0:
        raise RuntimeError(f"git {args[0]} に失敗：{result.stderr.strip().splitlines()[-1] if result.stderr.strip() else '原因不明'}")
    return result.stdout.strip()


def main(argv=None):
    parser = argparse.ArgumentParser(description="dist/ を gh-pages へ公開する")
    parser.add_argument("--dry-run", action="store_true", help="送信せず内容だけ確認する")
    args = parser.parse_args(argv)
    data_file = DIST_DIR / "data.enc.json"
    try:
        if not data_file.exists():
            raise RuntimeError("dist/data.enc.json がありません。先に python -m pipeline.build を実行してください")
        if json.loads(data_file.read_text(encoding="utf-8")).get("sample"):
            raise RuntimeError("dist/ は合成データです。公開できません（--sample なしでビルドし直してください）")
        remote = git("remote", "get-url", "origin", cwd=ROOT)
        name = git("config", "user.name", cwd=ROOT)
        email = git("config", "user.email", cwd=ROOT)
        with tempfile.TemporaryDirectory(prefix="hibi-deploy-") as folder:
            work = Path(folder) / "site"
            shutil.copytree(DIST_DIR, work)
            git("init", "--quiet", "--initial-branch", BRANCH, cwd=work)
            git("add", "--all", cwd=work)
            message = f"公開データを更新 {datetime.now(JST).isoformat(timespec='minutes')}"
            git("-c", f"user.name={name}", "-c", f"user.email={email}", "commit", "--quiet", "-m", message, cwd=work)
            files = git("ls-files", cwd=work).splitlines()
            if args.dry_run:
                print(f"確認のみ（未送信）：{len(files)}ファイル → {remote} の {BRANCH}")
                for file in files:
                    print(f"  {file}")
                return 0
            git("push", "--force", remote, f"{BRANCH}:{BRANCH}", cwd=work)
    except RuntimeError as error:
        print(f"中断：{error}", file=sys.stderr)
        return 1
    print(f"公開完了：{len(files)}ファイルを {BRANCH} へ送信しました")
    return 0


if __name__ == "__main__":
    sys.exit(main())
