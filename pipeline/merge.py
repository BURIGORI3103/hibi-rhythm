"""睡眠とPC活動を1日1レコードへ統合し、値を検証する。

方針：不明は不明として残し、推測で補わない。睡眠時間は Notion の暦日集計（下限・上限）を
そのまま採用し、ここでは再計算しない。区間はタイムライン描画のためだけに使う。
"""

from datetime import datetime, time, timedelta

from .config import JST, MAX_APPS_PER_DAY, PC_GAP_MERGE_SECONDS

SLEEP_STATES = {"確定": "confirmed", "一部不明": "partial", "記録なし": "none", "未同期": "unsynced"}
ADOPTED = {"採用": "s", "採用（段階なし）": "u"}   # s=段階あり、u=実睡眠が不明


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
    """秒単位の稼働区間を、短い隙間を結合した分単位の [開始, 終了] にする（描画専用）。"""
    merged = []
    for a, b in spans:
        if merged and a - merged[-1][1] <= PC_GAP_MERGE_SECONDS:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return [[round(a / 60, 1), round(b / 60, 1)] for a, b in merged]


def build_sleep(day, daily, sessions):
    warnings = []
    segments = sleep_segments(day, sessions)
    if daily is None:
        return {"state": "missing", "segments": segments}, warnings
    state = SLEEP_STATES.get(daily.get("state"))
    if state is None:
        state = "partial" if daily.get("upper") is not None else "missing"
        warnings.append(f"睡眠集計状態が未知の値です：{daily.get('state')!r}")
    sleep = {"state": state, "segments": segments}
    for key in ("lower", "upper", "in_bed", "night", "deep", "light", "rem"):
        value = daily.get(key)
        sleep[key] = None if value is None else round(value)
    for key in ("bed", "wake", "note"):
        sleep[key] = daily.get(key)
    union = sum(b - a for a, b, _ in segments)
    if sleep["in_bed"] is not None and abs(union - sleep["in_bed"]) > 1:
        warnings.append(f"睡眠区間の合計（{union}分）がNotionの就床経過時間（{sleep['in_bed']}分）と一致しません")
    return sleep, warnings


def build_pc(pc):
    if pc is None:
        return {"state": "missing", "segments": []}
    if pc["unknown_seconds"] >= 86399 and pc["active_seconds"] == 0:
        state = "none"
    elif pc["long_not_afk"]:
        state = "caution"
    else:
        state = "ok"
    apps = [{"name": app["name"], "seconds": round(app["seconds"])} for app in pc["apps"]]
    shown, rest = apps[:MAX_APPS_PER_DAY], apps[MAX_APPS_PER_DAY:]
    return {
        "state": state,
        "active": round(pc["active_seconds"]),
        "not_afk": round(pc["not_afk_seconds"]),
        "afk": round(pc["afk_seconds"]),
        "unknown": round(pc["unknown_seconds"]),
        "longest_not_afk": round(pc["longest_not_afk_seconds"]),
        "segments": pc_segments(pc["spans"]),
        "hours": [round(value) for value in pc["hours"]],
        "apps": shown,
        "apps_other": {"count": len(rest), "seconds": sum(app["seconds"] for app in rest)},
    }


def build_record(day, pc, daily, sessions):
    """1日分のレコードを作る。pc / daily が None なら「未取得」として残す。"""
    sleep, warnings = build_sleep(day, daily, sessions)
    record = {"date": day.isoformat(), "sleep": sleep, "pc": build_pc(pc),
              "steps": None if daily is None or daily.get("steps") is None else round(daily["steps"])}
    validate(record)
    if warnings:
        record["warnings"] = warnings
    return record


def validate(record):
    """あり得ない値は公開せず停止する。"""
    day = record["date"]
    sleep, pc = record["sleep"], record["pc"]
    lower, upper, in_bed = sleep.get("lower"), sleep.get("upper"), sleep.get("in_bed")
    for name, value in (("下限", lower), ("上限", upper), ("就床経過", in_bed)):
        if value is not None and not 0 <= value <= 1440:
            raise ValueError(f"{day}：睡眠の{name}が0〜1440分の範囲外です（{value}）")
    if lower is not None and upper is not None and lower > upper:
        raise ValueError(f"{day}：睡眠の下限（{lower}）が上限（{upper}）を超えています")
    previous = 0
    for a, b, kind in sleep["segments"]:
        if not (previous <= a < b <= 1440) or kind not in ("s", "u"):
            raise ValueError(f"{day}：睡眠区間が不正です（{a}〜{b}）")
        previous = b
    if pc["state"] == "missing":
        return
    for name in ("active", "not_afk", "afk", "unknown"):
        if not 0 <= pc[name] <= 86400:
            raise ValueError(f"{day}：PCの{name}が0〜86400秒の範囲外です（{pc[name]}）")
    if pc["active"] > pc["not_afk"] + 1:
        raise ValueError(f"{day}：PC稼働判定がnot-afk時間を超えています")
    previous = 0.0
    for a, b in pc["segments"]:
        if not (previous <= a <= b <= 1440):
            raise ValueError(f"{day}：PC区間が不正です（{a}〜{b}）")
        previous = b


def previous_days(today, count):
    """今日を含まない直近 count 日（古い順）。"""
    return [today - timedelta(days=offset) for offset in range(count, 0, -1)]
