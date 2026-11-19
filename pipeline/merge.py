"""睡眠とPC活動を1日1レコードへ統合し、値を検証する。

方針：判定できない値・記録のない値は記録しない（0 や推測で補わない）。睡眠は時間が分かる日だけ、
PC は ActivityWatch が実際に記録できた時間だけを残す。睡眠とPCの両方が分からない日はレコードを作らない。
睡眠時間は Notion の暦日集計（下限・上限）をそのまま採用し、ここでは再計算しない。区間はタイムライン描画のためだけに使う。

「値が分かっている」の判定（sleep_range と pc_state）は site/app.js の sleepRange / pcKind と
対になる。片方だけを変えない。
"""

from datetime import date, datetime, time, timedelta
import math

from .config import JST, LONG_NOT_AFK_SECONDS, MAX_APPS_PER_DAY, PC_GAP_MERGE_SECONDS

# 記録するのは時間が分かる状態だけ。「記録なし」「未同期」や未知の状態の日は睡眠を記録しない。
SLEEP_STATES = {"確定": "confirmed", "一部不明": "partial"}
ADOPTED = {"採用": "s", "採用（段階なし）": "u"}   # s=段階あり、u=実睡眠が不明

SLEEP_STATE_VALUES = set(SLEEP_STATES.values())
SLEEP_NUMBERS = ("lower", "upper", "in_bed", "night", "deep", "light", "rem")
SLEEP_TEXTS = ("bed", "wake", "note")
# ok=確定、caution=参考値（過大の可能性）。記録できなかった時間は持たない。
PC_STATE_VALUES = {"ok", "caution"}
PC_TOTALS = ("active", "not_afk", "afk", "longest_not_afk")
PC_KEYS = {"state", "segments", "hours", "apps", "apps_other", *PC_TOTALS}
RECORD_KEYS = {"date", "sleep", "pc", "steps", "warnings"}
# 旧版が残した値。保存済みの日を読むときに、いまの基準へ合わせるためだけに使う。
LEGACY_SLEEP_STATES = {"none", "unsynced", "missing"}
LEGACY_PC_STATES = {"none", "missing"}
LEGACY_PC_KEYS = ("unknown", "window_unknown")
LEGACY_WARNING = "不明として扱います"


def parse_time(value):
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=JST)


def sleep_segments(day, sessions):
    """採用セッションを暦日（0〜1440分）で切り取り、[開始分, 終了分, 種別] の並びにする。"""
    start = datetime.combine(day, time.min, JST)
    lane = [0] * 1440
    for session in sessions:
        kind = ADOPTED.get(session.get("verdict"))
        if not kind:
            continue
        a = round((parse_time(session["start"]) - start).total_seconds() / 60)
        b = round((parse_time(session["end"]) - start).total_seconds() / 60)
        rank = 2 if kind == "s" else 1
        for minute in range(max(a, 0), min(b, 1440)):
            lane[minute] = max(lane[minute], rank)
    segments, minute = [], 0
    while minute < 1440:
        if lane[minute]:
            begin, rank = minute, lane[minute]
            while minute < 1440 and lane[minute] == rank:
                minute += 1
            segments.append([begin, minute, "s" if rank == 2 else "u"])
        else:
            minute += 1
    return segments


def pc_segments(spans):
    """秒単位の稼働区間を、短い隙間を結合した分単位の [開始, 終了, うち稼働] にする（描画専用）。

    結合した隙間は稼働ではないので、区間の長さとは別に実際の稼働分を持たせる。
    """
    merged = []
    for a, b in spans:
        if merged and a - merged[-1][1] <= PC_GAP_MERGE_SECONDS:
            merged[-1][2] += max(b - max(a, merged[-1][1]), 0)
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b, b - a])
    return [[round(a / 60, 1), round(b / 60, 1), round(active / 60, 1)] for a, b, active in merged]


def sleep_range(sleep):
    """睡眠時間が分かっている日だけ (下限, 上限) を返す。それ以外は None（記録しない）。

    状態が「確定」「一部不明」で、下限と上限の両方がある場合に限る。片方だけの値や、
    未同期の行に残っている値を確定値として扱わない。
    """
    if not isinstance(sleep, dict) or sleep.get("state") not in ("confirmed", "partial"):
        return None
    lower, upper = sleep.get("lower"), sleep.get("upper")
    return None if lower is None or upper is None else (lower, upper)


def build_sleep(day, daily, sessions):
    """睡眠時間が分かる日だけ睡眠の項目を作る。分からない日は (None, []) を返し、何も記録しない。"""
    warnings = []
    state = None if daily is None else SLEEP_STATES.get(daily.get("state"))
    if state is None:
        return None, warnings
    segments = sleep_segments(day, sessions)
    sleep = {"state": state, "segments": segments}
    for key in SLEEP_NUMBERS:
        value = daily.get(key)
        sleep[key] = None if value is None else round(value)
    for key in SLEEP_TEXTS:
        sleep[key] = daily.get(key)
    if sleep_range(sleep) is None:
        return None, warnings
    union = sum(b - a for a, b, _ in segments)
    if sleep["in_bed"] is not None and abs(union - sleep["in_bed"]) > 1:
        warnings.append(f"睡眠区間の合計（{union}分）がNotionの就床経過時間（{sleep['in_bed']}分）と一致しません")
    return sleep, warnings


def pc_state(pc):
    """記録したPC時間の状態。not-afk が6時間以上途切れなかった日は「参考値」（過大の可能性）。

    pc は build_pc 後の項目名（longest_not_afk）で渡す。ActivityWatch が記録できなかった時間は
    どこにも数えず、記録できた時間だけをその日の値とする。
    """
    return "caution" if pc["longest_not_afk"] >= LONG_NOT_AFK_SECONDS else "ok"


def pc_recorded(pc):
    """ActivityWatch が操作中か離席かを1秒でも記録できた日か。できなかった日はPCを記録しない。"""
    return any(pc.get(key, 0) > 0 for key in ("active", "not_afk", "afk"))


def build_pc(pc):
    """PCの項目を作る。ActivityWatch が何も記録できなかった日は None（記録しない）。"""
    if pc is None:
        return None
    if "window_unknown_seconds" in pc:   # 受け取る値の検証だけ行い、記録はしない
        value = pc["window_unknown_seconds"]
        if not is_number(value) or not 0 <= value <= 86400:
            raise ValueError("ActivityWarch のAFK欠測秒数が不正です")
    apps = [{"name": app["name"], "seconds": round(app["seconds"])} for app in pc["apps"]]
    shown, rest = apps[:MAX_APPS_PER_DAY], apps[MAX_APPS_PER_DAY:]
    result = {
        "state": "caution" if pc["long_not_afk"] else "ok",
        "active": round(pc["active_seconds"]),
        "not_afk": round(pc["not_afk_seconds"]),
        "afk": round(pc["afk_seconds"]),
        "longest_not_afk": round(pc["longest_not_afk_seconds"]),
        "segments": pc_segments(pc["spans"]),
        "hours": [round(value) for value in pc["hours"]],
        "apps": shown,
        "apps_other": {"count": len(rest), "seconds": sum(app["seconds"] for app in rest)},
    }
    return result if pc_recorded(result) else None


def refresh(record):
    """保存済みレコードを、いまの基準（判定できない値は記録しない）に合わせる。

    分からない睡眠・記録できなかったPC・旧版の欠測項目と注意書きを取り除く。睡眠もPCも残らない日は
    None を返す（その日は記録しない）。数値そのものは変えない。形式の崩れは validate が止める。
    """
    if not isinstance(record, dict):
        return record
    record = dict(record)
    sleep, pc = record.get("sleep"), record.get("pc")
    if isinstance(sleep, dict) and (sleep.get("state") in LEGACY_SLEEP_STATES
                                    or (sleep.get("state") in SLEEP_STATE_VALUES and sleep_range(sleep) is None)):
        del record["sleep"]
        record.pop("warnings", None)   # 注意書きはすべて睡眠についてのもの
    if isinstance(pc, dict) and pc.get("state") in LEGACY_PC_STATES:
        del record["pc"]
    elif isinstance(pc, dict) and pc.get("state") in (*PC_STATE_VALUES, "partial"):
        pc = {key: value for key, value in pc.items() if key not in LEGACY_PC_KEYS}
        record["pc"] = pc
        if all(is_number(pc.get(key)) for key in PC_TOTALS):
            if not pc_recorded(pc):
                del record["pc"]
            elif pc["state"] == "partial":   # 旧版の「一部欠測」は、記録できた時間をそのまま値とする
                pc["state"] = pc_state(pc)
    if "steps" in record and record["steps"] is None:
        del record["steps"]
    if isinstance(record.get("warnings"), list):
        kept = [item for item in record["warnings"] if not (isinstance(item, str) and LEGACY_WARNING in item)]
        if kept:
            record["warnings"] = kept
        else:
            del record["warnings"]
    if "sleep" not in record and "pc" not in record:
        return None
    return record


def build_record(day, pc, daily, sessions):
    """1日分のレコードを作る。睡眠もPCも分からない日は None（その日は記録しない）。

    分からない側の項目は持たない。歩数は睡眠かPCを記録する日にだけ添える。
    """
    sleep, warnings = build_sleep(day, daily, sessions)
    pc = build_pc(pc)
    if sleep is None and pc is None:
        return None
    record = {"date": day.isoformat()}
    if sleep is not None:
        record["sleep"] = sleep
    if pc is not None:
        record["pc"] = pc
    if daily is not None and daily.get("steps") is not None:
        record["steps"] = round(daily["steps"])
    validate(record)
    if warnings:
        record["warnings"] = warnings
    return record


def is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def require(condition, day, message):
    if not condition:
        raise ValueError(f"{day}：{message}")


def validate(record):
    """構造と値を確かめ、あり得ないレコードは公開せず停止する。

    新しく作ったレコードにも、data/days/ に保存済みのレコードにも同じ検証を通す。
    想定外の項目も拒否する（アプリ名と時間以外がいつのまにか混ざるのを防ぐ）。
    """
    if not isinstance(record, dict) or not isinstance(record.get("date"), str):
        raise ValueError("日付のないレコードがあります")
    day = record["date"]
    try:
        valid_date = date.fromisoformat(day).isoformat() == day
    except ValueError:
        valid_date = False
    require(valid_date, day, "日付が YYYY-MM-DD 形式ではありません")
    require(set(record) <= RECORD_KEYS and ("sleep" in record or "pc" in record), day,
            "レコードの項目が想定と違います（睡眠もPCもない日は記録しません）")
    require("steps" not in record or (is_number(record["steps"]) and 0 <= record["steps"] <= 200000),
            day, f"歩数が不正です（{record.get('steps')!r}）")
    warnings = record.get("warnings", [])
    require(isinstance(warnings, list) and all(isinstance(item, str) for item in warnings), day, "注意書きの形式が不正です")
    if "sleep" in record:
        validate_sleep(day, record["sleep"])
    if "pc" in record:
        validate_pc(day, record["pc"])


def validate_sleep(day, sleep):
    """記録する睡眠は、時間（下限と上限）が分かっているものだけ。"""
    require(isinstance(sleep, dict), day, "睡眠の形式が不正です")
    require(set(sleep) <= {"state", "segments", *SLEEP_NUMBERS, *SLEEP_TEXTS}, day, "睡眠の項目が想定と違います")
    require(sleep.get("state") in SLEEP_STATE_VALUES, day, f"睡眠の状態が不正です（{sleep.get('state')!r}）")
    for key in SLEEP_NUMBERS:
        value = sleep.get(key)
        require(value is None or (is_number(value) and 0 <= value <= 1440), day,
                f"睡眠の {key} が0〜1440分の範囲外です（{value!r}）")
    for key in SLEEP_TEXTS:
        require(sleep.get(key) is None or isinstance(sleep[key], str), day, f"睡眠の {key} が文字列ではありません")
    lower, upper = sleep.get("lower"), sleep.get("upper")
    require(lower is not None and upper is not None, day, "睡眠の下限か上限がありません（分からない睡眠は記録しません）")
    require(lower <= upper, day, f"睡眠の下限（{lower}）が上限（{upper}）を超えています")
    require(isinstance(sleep.get("segments"), list), day, "睡眠区間の形式が不正です")
    previous = 0
    for segment in sleep["segments"]:
        require(isinstance(segment, list) and len(segment) == 3 and is_number(segment[0]) and is_number(segment[1])
                and previous <= segment[0] < segment[1] <= 1440 and segment[2] in ("s", "u"),
                day, f"睡眠区間が不正です（{segment!r}）")
        previous = segment[1]


def validate_pc(day, pc):
    """記録するPCは、ActivityWatch が記録できた時間だけ。記録できなかった時間の項目は持たない。"""
    require(isinstance(pc, dict), day, "PCの形式が不正です")
    require(pc.get("state") in PC_STATE_VALUES, day, f"PCの状態が不正です（{pc.get('state')!r}）")
    require(set(pc) == PC_KEYS, day, "PCの項目が想定と違います")
    for name in PC_TOTALS:
        # 最長連続は日をまたいで前後 LONG_NOT_AFK_SECONDS ずつ広げて数える（ActivityWarch）ため、24時間を超えうる
        limit = 86400 + 2 * LONG_NOT_AFK_SECONDS if name == "longest_not_afk" else 86400
        require(is_number(pc[name]) and 0 <= pc[name] <= limit, day, f"PCの{name}が0〜{limit}秒の範囲外です（{pc[name]!r}）")
    require(pc["active"] <= pc["not_afk"] + 1, day, "PC稼働判定がnot-afk時間を超えています")
    require(pc_recorded(pc), day, "記録できた時間のないPCレコードです")
    require(isinstance(pc["hours"], list) and len(pc["hours"]) == 24
            and all(is_number(value) and 0 <= value <= 3601 for value in pc["hours"]), day, "PCの時間帯別の値が不正です")
    apps, other = pc["apps"], pc["apps_other"]
    require(isinstance(apps, list) and len(apps) <= MAX_APPS_PER_DAY, day, "PCのアプリ一覧が不正です")
    for app in apps:
        require(isinstance(app, dict) and set(app) == {"name", "seconds"} and isinstance(app["name"], str)
                and is_number(app["seconds"]) and 0 <= app["seconds"] <= 86400, day, "PCのアプリの形式が不正です")
    require(isinstance(other, dict) and set(other) == {"count", "seconds"} and is_number(other["count"])
            and other["count"] >= 0 and is_number(other["seconds"]) and 0 <= other["seconds"] <= 86400,
            day, "PCの「ほか」の形式が不正です")
    require(isinstance(pc["segments"], list), day, "PC区間の形式が不正です")
    previous = 0.0
    for segment in pc["segments"]:   # 旧形式は [開始, 終了]、現行は [開始, 終了, うち稼働]
        require(isinstance(segment, list) and len(segment) in (2, 3) and all(is_number(value) for value in segment)
                and previous <= segment[0] <= segment[1] <= 1440, day, f"PC区間が不正です（{segment!r}）")
        require(len(segment) == 2 or 0 <= segment[2] <= segment[1] - segment[0] + 0.2, day,
                f"PC区間の稼働分が区間の長さを超えています（{segment!r}）")
        previous = segment[1]


def validate_dataset(records, today, start=None):
    """公開する全レコードを検証する。完了した日（今日より前）だけ、同じ日は1件だけ、開始日以降だけ。"""
    seen = set()
    for record in records:
        validate(record)
        day = record["date"]
        require(day not in seen, day, "同じ日付のレコードが複数あります")
        require(day < today.isoformat(), day, "まだ終わっていない日（今日以降）のレコードです")
        require(start is None or day >= start.isoformat(), day, "公開開始日より前のレコードです")
        seen.add(day)


def previous_days(today, count):
    """今日を含まない直近 count 日（古い順）。"""
    return [today - timedelta(days=offset) for offset in range(count, 0, -1)]
