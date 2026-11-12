"""睡眠データの収集。Notion API から「健康記録」と「睡眠セッション」を読み取るだけで、書き込まない。"""

from datetime import datetime, time, timedelta
import json
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .collect_pc import CollectError
from .config import JST
from .merge import parse_time

API = "https://api.notion.com/v1/"
NOTION_VERSION = "2026-03-11"


def query_all(data_source_id, token):
    """データソースの全行をページングして取得する。"""
    rows, cursor = [], None
    seen_cursors = set()
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
        except ValueError:
            raise CollectError("Notion の読み取り結果を JSON として読めません") from None
        if (not isinstance(payload, dict) or type(payload.get("has_more")) is not bool
                or not isinstance(payload.get("results"), list)
                or any(not isinstance(page, dict) for page in payload["results"])):
            raise CollectError("Notion の読み取り結果の形式が想定と違います")
        rows.extend(page for page in payload["results"]
                    if not page.get("in_trash") and not page.get("archived"))
        if not payload["has_more"]:
            return rows
        cursor = payload.get("next_cursor")
        if not isinstance(cursor, str) or not cursor or cursor in seen_cursors:
            raise CollectError("Notion のページ取得が進まないため中断しました")
        seen_cursors.add(cursor)


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


TEXT, NUMBER, SELECT = {"title", "rich_text"}, {"number"}, {"select"}
DAILY_COLUMNS = {
    "日付": TEXT, "就寝時刻": TEXT, "起床時刻": TEXT, "睡眠集計メモ": TEXT, "睡眠集計状態": SELECT,
    "睡眠時間（分）": NUMBER, "深い睡眠（分）": NUMBER, "浅い睡眠（分）": NUMBER, "レム睡眠（分）": NUMBER,
    "就床経過時間（暦日）（分）": NUMBER, "睡眠時間（暦日・下限）（分）": NUMBER,
    "睡眠時間（暦日・上限）（分）": NUMBER, "歩数": NUMBER,
}
SESSION_COLUMNS = {"開始日時": TEXT, "終了日時": TEXT, "判定": SELECT}


def require_columns(props, columns, table):
    """必須列がその型で存在することを確かめる。

    Notion 側で列の名前や型が変わると、値はエラーにならず「空」として読めてしまう。
    そのまま進むと、保存済みの記録を欠測で上書きするため、ここで止める。
    """
    for name, kinds in columns.items():
        prop = props.get(name)
        if not isinstance(prop, dict) or prop.get("type") not in kinds:
            found = "列がありません" if prop is None else f"型が {prop.get('type')!r} です"
            raise CollectError(f"Notion「{table}」の列「{name}」を読めません（{found}）。"
                               "列の名前や型を変えた場合は pipeline/collect_sleep.py を合わせて直してください")


def parse_daily(pages, days=None):
    """「健康記録」の行を {日付: 辞書} にする。days を渡すと、その日の行だけを対象にする。

    対象の日に同じ日付の行が複数あれば曖昧なので停止する。対象外の日の重複や列の欠けでは止めない。
    """
    wanted = None if days is None else {day.isoformat() for day in days}
    result = {}
    for page in pages:
        props = page["properties"]
        require_columns(props, {"日付": TEXT}, "健康記録")
        day = text(props.get("日付"))
        if not day or (wanted is not None and day not in wanted):
            continue
        if day in result:
            raise CollectError(f"健康記録に同じ日付の行が複数あります：{day}")
        require_columns(props, DAILY_COLUMNS, "健康記録")
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


def parse_sessions(pages, days=None):
    """「睡眠セッション」の行を開始・終了・判定の並びにする。days を渡すと、その期間に重なる行だけを返す。

    日時を読めない行は、対象期間かどうかを判断できない。更新全体は止めず、件数を数えて除外する
    （区間はタイムライン描画専用で、睡眠時間の数値には使わない）。戻り値は (並び, 除外した件数)。
    """
    period = None
    if days:
        begin = datetime.combine(min(days), time.min, JST)
        period = (begin, datetime.combine(max(days), time.min, JST) + timedelta(days=1))
    result, unreadable = [], 0
    for page in pages:
        props = page["properties"]
        require_columns(props, SESSION_COLUMNS, "睡眠セッション")
        start, end = text(props.get("開始日時")), text(props.get("終了日時"))
        if not start or not end:
            continue
        try:
            a, b = parse_time(start), parse_time(end)
        except ValueError:
            unreadable += 1
            continue
        if a >= b:
            unreadable += 1
            continue
        if period and (b <= period[0] or a >= period[1]):
            continue
        result.append({"start": start, "end": end, "verdict": select(props.get("判定"))})
    return sorted(result, key=lambda item: parse_time(item["start"])), unreadable


def collect(token, health_id, session_id, days=None):
    """(日別の辞書, セッションの並び) を返す。days を渡すと、その期間の行だけを解釈する。"""
    if not token or not health_id or not session_id:
        raise CollectError(".env に NOTION_TOKEN と2つのデータソースIDを設定してください（README参照）")
    daily = parse_daily(query_all(health_id, token), days)
    sessions, unreadable = parse_sessions(query_all(session_id, token), days)
    if unreadable:
        print(f"注意：開始・終了日時を読めない睡眠セッションが{unreadable}件あり、タイムラインから除外しました", file=sys.stderr)
    return daily, sessions


def check():
    """Notion に接続できるかだけを確かめる。件数と日付の範囲だけを表示し、記録の中身は出さない。"""
    from .config import load_env
    env = load_env()
    try:
        daily, sessions = collect(env.get("NOTION_TOKEN"), env.get("NOTION_HEALTH_DATA_SOURCE_ID"),
                                  env.get("NOTION_SLEEP_SESSION_DATA_SOURCE_ID"))
    except CollectError as error:
        print(f"NG：{error}", file=sys.stderr)
        return 1
    days = sorted(daily)
    span = f"{days[0]}〜{days[-1]}" if days else "なし"
    print(f"OK：健康記録 {len(daily)}日分（{span}）、睡眠セッション {len(sessions)}件を読み取れました")
    return 0


if __name__ == "__main__":
    raise SystemExit(check())
