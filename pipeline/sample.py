"""画面確認用の合成データ。実在の記録ではなく、公開（deploy）もできない。"""

from datetime import datetime, time, timedelta
import random

from .config import JST
from .merge import build_record, previous_days, sleep_segments

PASSPHRASE = "sample-passphrase-for-preview"
APPS = ["browser.exe", "editor.exe", "game.exe", "chat.exe", "music.exe", "viewer.exe"]


def make_sessions(days, rng):
    """夜の睡眠（段階あり）と、ときどき昼寝（段階なし）を作る。"""
    sessions = []
    for day in days:
        midnight = datetime.combine(day, time.min, JST)
        if rng.random() < 0.93:
            start = midnight + timedelta(minutes=rng.randint(22 * 60, 26 * 60 + 30))
            end = start + timedelta(minutes=rng.randint(330, 570))
            sessions.append({"start": start.isoformat(), "end": end.isoformat(), "verdict": "採用"})
        if rng.random() < 0.22:
            start = midnight + timedelta(minutes=rng.randint(13 * 60, 16 * 60))
            end = start + timedelta(minutes=rng.randint(25, 90))
            sessions.append({"start": start.isoformat(), "end": end.isoformat(), "verdict": "採用（段階なし）"})
    return sessions


def make_daily(day, sessions, rng):
    segments = sleep_segments(day, sessions)
    if not segments:
        return {"state": "記録なし", "note": "この日に触れる睡眠セッションが元データにない。", "steps": rng.randint(1500, 9000)}
    staged = sum(b - a for a, b, kind in segments if kind == "s")
    unstaged = sum(b - a for a, b, kind in segments if kind == "u")
    night = max((b - a for a, b, kind in segments if kind == "s"), default=0)
    deep = round(night * 0.17)
    rem = round(night * 0.21)
    first = next((seg for seg in segments if seg[2] == "s"), segments[0])
    return {
        "bed": f"{first[0] // 60:02d}:{first[0] % 60:02d}", "wake": f"{first[1] // 60 % 24:02d}:{first[1] % 60:02d}",
        "night": night, "deep": deep, "light": night - deep - rem, "rem": rem,
        "in_bed": staged + unstaged, "lower": staged, "upper": staged + unstaged,
        "state": "一部不明" if unstaged else "確定",
        "note": "昼寝は段階データがなく実睡眠は不明。" if unstaged else None,
        "steps": rng.randint(1500, 11000),
    }


def make_pc(day, daily_segments, rng, stuck=False):
    """起きている時間帯に操作ブロックを置く。stuck=True は離席判定が効かなかった日の再現。"""
    asleep = [False] * 1440
    for a, b, _ in daily_segments:
        for minute in range(a, b):
            asleep[minute] = True
    spans, minute = [], 0
    if stuck:
        spans = [[11 * 3600.0, 22.5 * 3600.0]]
    else:
        while minute < 1440:
            if not asleep[minute] and rng.random() < 0.035:
                length = rng.randint(15, 150)
                end = minute
                while end < min(minute + length, 1440) and not asleep[end]:
                    end += 1
                if end - minute >= 5:
                    spans.append([minute * 60.0, end * 60.0])
                minute = end + rng.randint(10, 60)
            else:
                minute += 1
    active = sum(b - a for a, b in spans)
    hours = [0.0] * 24
    for a, b in spans:
        cursor = a
        while cursor < b:
            edge = min(b, (cursor // 3600 + 1) * 3600)
            hours[int(cursor // 3600)] += edge - cursor
            cursor = edge
    weights = [rng.random() ** 2 for _ in APPS]
    apps = sorted(({"name": name, "seconds": active * weight / sum(weights)} for name, weight in zip(APPS, weights)),
                  key=lambda app: -app["seconds"]) if active else []
    afk = 0.0 if stuck else min(86400 - active, rng.randint(1800, 14400))
    longest = max((b - a for a, b in spans), default=0.0)
    return {"date": day.isoformat(), "active_seconds": active, "not_afk_seconds": active, "afk_seconds": afk,
            "unknown_seconds": 86400 - active - afk, "window_seconds": active, "apps": apps, "hours": hours,
            "spans": spans, "longest_not_afk_seconds": longest, "long_not_afk": longest >= 21600}


def generate(today, count=30, seed=20261111):
    """直近 count 日ぶんの合成レコードを返す（毎回同じ結果）。"""
    rng = random.Random(seed)
    days = previous_days(today, count)
    sessions = make_sessions([days[0] - timedelta(days=1), *days], rng)
    records = []
    for index, day in enumerate(days):
        daily = make_daily(day, sessions, rng)
        segments = sleep_segments(day, sessions)
        pc = None if index == 3 else make_pc(day, segments, rng, stuck=(index == count - 6))
        records.append(build_record(day, pc, daily, sessions))
    return records
