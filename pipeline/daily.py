"""毎日の自動実行の入口。ビルドに成功したときだけ公開する。"""

from datetime import datetime
import sys

from . import build, deploy
from .config import JST


def main():
    print(f"=== {datetime.now(JST).isoformat(timespec='seconds')} 日次更新を開始 ===", flush=True)
    code = build.main([])
    if code != 0:
        print("ビルドに失敗したため公開を中止しました。公開中のデータはそのままです。", file=sys.stderr)
        return code
    return deploy.main([])


if __name__ == "__main__":
    sys.exit(main())
