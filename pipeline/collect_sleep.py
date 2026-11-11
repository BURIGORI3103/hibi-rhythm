"""睡眠データの収集。Notion API から「健康記録」と「睡眠セッション」を読み取るだけで、書き込まない。"""

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .collect_pc import CollectError

API = "https://api.notion.com/v1/"
NOTION_VERSION = "2026-03-11"


def query_all(data_source_id, token):
    """データソースの全行をページングして取得する。"""
    rows, cursor = [], None
    while True:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        request = Request(API + f"data_sources/{data_source_id}/query",
                          data=json.dumps(body).encode("utf-8"), method="POST",
                          headers={"Authorization": f"Bearer {token}",
                                   "Notion-Version": NOTION_VERSION,
                                   "Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=30) as response:
                payload = json.load(response)
        except HTTPError as error:
            hints = {401: "トークンが無効です（.env の NOTION_TOKEN を確認）",
                     403: "インテグレーションに読み取り権限がありません",
                     404: "データソースが見つかりません（「健康記録」ページをインテグレーションへ共有したか確認）",
                     429: "Notion の利用制限に達しました。時間をおいて再実行してください"}
            raise CollectError(f"Notion の読み取りに失敗：{hints.get(error.code, f'HTTP {error.code}')}") from None
        except URLError as error:
            raise CollectError(f"Notion に接続できません：{error.reason}") from None
        rows.extend(page for page in payload["results"]
                    if not page.get("in_trash") and not page.get("archived"))
        if not payload.get("has_more"):
            return rows
        cursor = payload["next_cursor"]


def text(prop):
    """title / rich_text を素の文字列にする。空なら None。"""
    if not prop:
        return None
    parts = prop.get(prop.get("type", ""), [])
    if not isinstance(parts, list):
        return None
    value = "".join(part.get("plain_text", "") for part in parts).strip()
    return value or None


def number(prop):
    return prop.get("number") if prop else None


def select(prop):
    option = prop.get("select") if prop else None
    return option.get("name") if option else None


def parse_daily(pages):
    """「健康記録」の行を {日付: 辞書} にする。同じ日付が複数あれば曖昧なので停止する。"""
    result = {}
    for page in pages:
        props = page["properties"]
        day = text(props.get("日付"))
        if not day:
            continue
        if day in result:
            raise CollectError(f"健康記録に同じ日付の行が複数あります：{day}")
        result[day] = {
            "bed": text(props.get("就寝時刻")),
            "wake": text(props.get("起床時刻")),
            "night": number(props.get("睡眠時間（分）")),
            "deep": number(props.get("深い睡眠（分）")),
            "light": number(props.get("浅い睡眠（分）")),
            "rem": number(props.get("レム睡眠（分）")),
            "in_bed": number(props.get("就床経過時間（暦日）（分）")),
            "lower": number(props.get("睡眠時間（暦日・下限）（分）")),
            "upper": number(props.get("睡眠時間（暦日・上限）（分）")),
            "state": select(props.get("睡眠集計状態")),
            "note": text(props.get("睡眠集計メモ")),
            "steps": number(props.get("歩数")),
        }
    return result


def parse_sessions(pages):
    """「睡眠セッション」の行を開始・終了・判定の並びにする。"""
    result = []
    for page in pages:
        props = page["properties"]
        start, end = text(props.get("開始日時")), text(props.get("終了日時"))
        if not start or not end:
            continue
        result.append({"start": start, "end": end, "verdict": select(props.get("判定"))})
    return sorted(result, key=lambda item: item["start"])


def collect(token, health_id, session_id):
    """(日別の辞書, セッションの並び) を返す。"""
    if not token or not health_id or not session_id:
        raise CollectError(".env に NOTION_TOKEN と2つのデータソースIDを設定してください（README参照）")
    return parse_daily(query_all(health_id, token)), parse_sessions(query_all(session_id, token))
