"""パスと設定値。秘密情報は .env から読み、ソースやログに出さない。"""

from datetime import timedelta, timezone
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE_DIR = ROOT / "site"
DATA_DIR = ROOT / "data"
DAYS_DIR = DATA_DIR / "days"
DIST_DIR = ROOT / "dist"
STATE_DIR = ROOT / ".state"
ENV_FILE = ROOT / ".env"

JST = timezone(timedelta(hours=9))
TIMEZONE_NAME = "Asia/Tokyo"
SCHEMA_VERSION = 1

DEFAULT_ACTIVITYWATCH_PROJECT = r"C:\Project\ActivityWarch"
RECOLLECT_DAYS = 14          # 毎回取り直す直近日数（Notion側の後追い修正を反映する）
MIN_PASSPHRASE_LENGTH = 12   # 暗号文は公開されるため、短い合言葉は総当たりされ得る
MAX_APPS_PER_DAY = 12        # これを超えるアプリは「その他」へまとめる
PC_GAP_MERGE_SECONDS = 60    # タイムライン描画用に、これ以下の隙間は結合する（合計値には影響しない）


ENV_KEYS = ("NOTION_TOKEN", "NOTION_HEALTH_DATA_SOURCE_ID", "NOTION_SLEEP_SESSION_DATA_SOURCE_ID",
            "SITE_PASSPHRASE", "ACTIVITYWATCH_PROJECT")


def load_env(path=ENV_FILE):
    """KEY=VALUE 形式の .env を読む。既存の環境変数を優先する。"""
    values = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            values[key.strip()] = value
    for key in ENV_KEYS:
        if os.environ.get(key):
            values[key] = os.environ[key]
    return values
