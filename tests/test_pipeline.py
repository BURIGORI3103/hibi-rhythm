"""統合・検証・暗号化・Notion行の解釈を、合成データだけで確認する。"""

from datetime import date
import json
import unittest

from pipeline import encrypt, sample
from pipeline.collect_pc import CollectError
from pipeline.collect_sleep import parse_daily, parse_sessions
from pipeline.merge import build_record, pc_segments, sleep_segments, validate

DAY = date(2026, 11, 10)


def session(start, end, verdict="採用"):
    return {"start": start, "end": end, "verdict": verdict}


def pc(**overrides):
    base = {"date": "2026-11-10", "active_seconds": 3600.4, "not_afk_seconds": 3700.0, "afk_seconds": 1000.0,
            "unknown_seconds": 81700.0, "window_seconds": 3600.4,
            "apps": [{"name": f"app{i}.exe", "seconds": 3600.4 / 15} for i in range(15)],
            "hours": [0.0] * 9 + [3600.4] + [0.0] * 14, "spans": [[32400.0, 34200.0], [34230.0, 36030.4]],
            "longest_not_afk_seconds": 3700.0, "long_not_afk": False}
    base.update(overrides)
    return base


class PublishStagingTests(unittest.TestCase):
    def test_files_are_replaced_without_removing_the_target_folder(self):
        import tempfile
        from pathlib import Path
        from pipeline.build import publish_staging
        with tempfile.TemporaryDirectory() as folder:
            staging, target = Path(folder) / "dist.tmp", Path(folder) / "dist"
            staging.mkdir()
            target.mkdir()
            (staging / "index.html").write_text("new", encoding="utf-8")
            (target / "index.html").write_text("old", encoding="utf-8")
            (target / "stale.txt").write_text("x", encoding="utf-8")
            marker = target.stat().st_ctime_ns
            publish_staging(staging, target)
            self.assertEqual(sorted(path.name for path in target.iterdir()), ["index.html"])
            self.assertEqual((target / "index.html").read_text(encoding="utf-8"), "new")
            self.assertEqual(target.stat().st_ctime_ns, marker)
            self.assertFalse(staging.exists())

    def test_read_only_folder_can_be_removed(self):
        import os
        import stat
        import tempfile
        from pathlib import Path
        from pipeline.build import remove_tree
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "dist.tmp"
            target.mkdir()
            (target / "a.txt").write_text("x", encoding="utf-8")
            os.chmod(target / "a.txt", stat.S_IREAD)
            os.chmod(target, stat.S_IREAD)
            remove_tree(target)
            self.assertFalse(target.exists())


class StartDateTests(unittest.TestCase):
    def test_records_before_the_start_date_are_dropped(self):
        from pipeline.build import start_date, within
        records = [{"date": "2026-11-05"}, {"date": "2026-11-06"}, {"date": "2026-11-07"}]
        self.assertEqual(within(records, start_date({"START_DATE": "2026-11-06"})), records[1:])
        self.assertEqual(within(records, start_date({})), records)

    def test_malformed_start_date_stops_the_build(self):
        from pipeline.build import start_date
        with self.assertRaises(CollectError):
            start_date({"START_DATE": "11/6"})


class SleepSegmentTests(unittest.TestCase):
    def test_sessions_are_clipped_to_the_calendar_day(self):
        sessions = [session("2026-11-09T22:32:00+09:00", "2026-11-10T07:16:00+09:00"),
                    session("2026-11-10T08:40:00+09:00", "2026-11-10T09:10:00+09:00", "採用（段階なし）"),
                    session("2026-11-10T21:48:00+09:00", "2026-11-11T07:14:00+09:00")]
        self.assertEqual(sleep_segments(DAY, sessions), [[0, 436, "s"], [520, 550, "u"], [1308, 1440, "s"]])

    def test_duplicates_and_unconfirmed_sessions_are_excluded(self):
        sessions = [session("2026-11-10T01:00:00+09:00", "2026-11-10T02:00:00+09:00", "重複（別記録）"),
                    session("2026-11-10T03:00:00+09:00", "2026-11-10T04:00:00+09:00", "要確認"),
                    session("2026-11-10T05:00:00+09:00", "2026-11-10T06:00:00+09:00", None)]
        self.assertEqual(sleep_segments(DAY, sessions), [])

    def test_staged_sleep_wins_where_sessions_overlap(self):
        sessions = [session("2026-11-10T01:00:00+09:00", "2026-11-10T03:00:00+09:00", "採用（段階なし）"),
                    session("2026-11-10T02:00:00+09:00", "2026-11-10T04:00:00+09:00")]
        self.assertEqual(sleep_segments(DAY, sessions), [[60, 120, "u"], [120, 240, "s"]])

    def test_timestamp_without_offset_is_read_as_japan_time(self):
        self.assertEqual(sleep_segments(DAY, [session("2026-11-10T01:00:00", "2026-11-10T02:00:00")]), [[60, 120, "s"]])


class RecordTests(unittest.TestCase):
    def daily(self, **overrides):
        base = {"bed": "22:32", "wake": "07:16", "night": 524, "deep": 77, "light": 354, "rem": 93,
                "in_bed": 60, "lower": 60, "upper": 60, "state": "確定", "note": None, "steps": 7971}
        base.update(overrides)
        return base

    def test_record_keeps_notion_totals_and_flags_mismatch(self):
        sessions = [session("2026-11-10T01:00:00+09:00", "2026-11-10T02:00:00+09:00")]
        record = build_record(DAY, pc(), self.daily(), sessions)
        self.assertEqual(record["sleep"]["state"], "confirmed")
        self.assertEqual((record["sleep"]["lower"], record["sleep"]["upper"]), (60, 60))
        self.assertEqual(record["steps"], 7971)
        self.assertNotIn("warnings", record)
        mismatched = build_record(DAY, pc(), self.daily(in_bed=200, upper=200), sessions)
        self.assertIn("一致しません", mismatched["warnings"][0])

    def test_missing_sources_are_not_turned_into_zero(self):
        record = build_record(DAY, None, None, [])
        self.assertEqual(record["sleep"], {"state": "missing", "segments": []})
        self.assertEqual(record["pc"], {"state": "missing", "segments": []})
        self.assertIsNone(record["steps"])

    def test_no_record_day_has_no_numbers(self):
        daily = {"state": "記録なし", "note": "元データなし", "steps": 3618}
        sleep = build_record(DAY, pc(), daily, [])["sleep"]
        self.assertEqual(sleep["state"], "none")
        self.assertIsNone(sleep["lower"])
        self.assertIsNone(sleep["upper"])

    def test_pc_states_and_app_folding(self):
        record = build_record(DAY, pc(), None, [])["pc"]
        self.assertEqual(record["state"], "ok")
        self.assertEqual(record["active"], 3600)
        self.assertEqual(len(record["apps"]), 12)
        self.assertEqual(record["apps_other"]["count"], 3)
        self.assertEqual(record["segments"], [[540.0, 600.5]])
        caution = build_record(DAY, pc(long_not_afk=True, longest_not_afk_seconds=30000.0), None, [])["pc"]
        self.assertEqual(caution["state"], "caution")
        empty = build_record(DAY, pc(active_seconds=0.0, not_afk_seconds=0.0, afk_seconds=0.0,
                                     unknown_seconds=86400.0, apps=[], spans=[], hours=[0.0] * 24), None, [])["pc"]
        self.assertEqual(empty["state"], "none")

    def test_gap_longer_than_a_minute_is_kept(self):
        self.assertEqual(pc_segments([[0, 600], [661, 1200]]), [[0.0, 10.0], [11.0, 20.0]])

    def test_impossible_values_stop_the_build(self):
        with self.assertRaises(ValueError):
            build_record(DAY, pc(), self.daily(lower=500, upper=400, in_bed=500), [])
        with self.assertRaises(ValueError):
            build_record(DAY, pc(), self.daily(upper=1500, in_bed=1500), [])
        with self.assertRaises(ValueError):
            build_record(DAY, pc(active_seconds=90000.0, not_afk_seconds=90000.0), None, [])
        with self.assertRaises(ValueError):
            validate({"date": "2026-11-10", "pc": {"state": "missing", "segments": []},
                      "sleep": {"state": "confirmed", "segments": [[100, 50, "s"]]}})


class EncryptionTests(unittest.TestCase):
    def test_round_trip_and_wrong_passphrase(self):
        plaintext = json.dumps({"days": [{"date": "2026-11-10", "名前": "テスト"}]}, ensure_ascii=False).encode("utf-8")
        envelope = encrypt.encrypt(plaintext, "正しい合言葉 １２３", iterations=1000)
        self.assertEqual(encrypt.decrypt(envelope, " 正しい合言葉 123 "), plaintext)
        with self.assertRaises(ValueError):
            encrypt.decrypt(envelope, "違う合言葉")
        serialized = json.dumps(envelope)
        self.assertNotIn("2026-11-10", serialized)
        self.assertNotIn("テスト", serialized)

    def test_each_encryption_uses_fresh_salt_and_iv(self):
        first = encrypt.encrypt(b"data", "passphrase", iterations=1000)
        second = encrypt.encrypt(b"data", "passphrase", iterations=1000)
        self.assertNotEqual((first["salt"], first["iv"], first["data"]), (second["salt"], second["iv"], second["data"]))


class NotionParsingTests(unittest.TestCase):
    def page(self, **props):
        return {"properties": props}

    def test_daily_rows(self):
        title = {"type": "title", "title": [{"plain_text": "2026-11-10"}]}
        rich = {"type": "rich_text", "rich_text": [{"plain_text": "22:"}, {"plain_text": "32"}]}
        pages = [self.page(**{"日付": title, "就寝時刻": rich,
                              "睡眠時間（暦日・下限）（分）": {"type": "number", "number": 568},
                              "睡眠時間（暦日・上限）（分）": {"type": "number", "number": 598},
                              "睡眠集計状態": {"type": "select", "select": {"name": "一部不明"}},
                              "睡眠集計メモ": {"type": "rich_text", "rich_text": []},
                              "歩数": {"type": "number", "number": None}})]
        row = parse_daily(pages)["2026-11-10"]
        self.assertEqual((row["bed"], row["lower"], row["upper"], row["state"]), ("22:32", 568, 598, "一部不明"))
        self.assertIsNone(row["note"])
        self.assertIsNone(row["steps"])
        with self.assertRaises(CollectError):
            parse_daily(pages * 2)

    def test_session_rows(self):
        def rich(value):
            return {"type": "rich_text", "rich_text": [{"plain_text": value}]}
        pages = [self.page(**{"開始日時": rich("2026-11-10T21:48:00+09:00"), "終了日時": rich("2026-11-11T07:14:00+09:00"),
                              "判定": {"type": "select", "select": {"name": "採用"}}}),
                 self.page(**{"開始日時": rich("2026-11-10T08:40:00+09:00"), "終了日時": rich("2026-11-10T09:10:00+09:00"),
                              "判定": {"type": "select", "select": None}}),
                 self.page(**{"開始日時": {"type": "rich_text", "rich_text": []}, "終了日時": rich("x")})]
        sessions = parse_sessions(pages)
        self.assertEqual([item["start"][11:16] for item in sessions], ["08:40", "21:48"])
        self.assertEqual([item["verdict"] for item in sessions], [None, "採用"])


class SampleTests(unittest.TestCase):
    def test_sample_is_deterministic_and_valid(self):
        first = sample.generate(date(2026, 11, 11))
        self.assertEqual(first, sample.generate(date(2026, 11, 11)))
        self.assertEqual(len(first), 30)
        self.assertEqual(first[-1]["date"], "2026-11-10")
        states = {record["pc"]["state"] for record in first}
        self.assertTrue({"ok", "caution", "missing"} <= states)


if __name__ == "__main__":
    unittest.main()
