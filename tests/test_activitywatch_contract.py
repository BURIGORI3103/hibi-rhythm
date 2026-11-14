"""隣接リポジトリのJSON出力を、合成イベントだけで受け取る。"""

from datetime import date
import os
from pathlib import Path
import subprocess
import sys
import unittest

from pipeline.collect_pc import parse_export
from pipeline.merge import build_record


EXPORT_SYNTHETIC_DAY = """\
import sys
from unittest.mock import patch
import activitywatch_to_json as exporter

def response(path, **params):
    if path == "info":
        return {"hostname": "Synthetic host"}
    if path == "buckets/":
        return {"window": {"type": "currentwindow", "hostname": "Synthetic host"},
                "afk": {"type": "afkstatus", "hostname": "Synthetic host"}}
    if path == "buckets/window/events":
        return [{"id": 1, "timestamp": "2001-01-01T14:59:00Z", "duration": 1860,
                 "data": {"app": "Synthetic game", "title": "synthetic private title"}}]
    if path == "buckets/afk/events":
        return [{"id": 2, "timestamp": "2001-01-01T14:59:00Z", "duration": 180,
                 "data": {"status": "not-afk"}},
                {"id": 3, "timestamp": "2001-01-01T15:02:00Z", "duration": 120,
                 "data": {"status": "afk"}}]
    raise AssertionError(path)

sys.argv = ["activitywatch_to_json.py", "--date", "2001-01-02"]
with (patch("activitywatch_to_json.get_json", side_effect=response),
      patch("activitywatch_to_obsidian.get_json", side_effect=response)):
    exporter.main()
"""


class ActivityWatchContractTests(unittest.TestCase):
    def test_synthetic_export_preserves_boundary_and_window_afk_gap(self):
        project = os.environ.get("ACTIVITYWARCH_TEST_PROJECT")
        if not project:
            self.skipTest("ACTIVITYWARCH_TEST_PROJECT に ActivityWarch のチェックアウトを指定してください")
        checkout = Path(project)
        self.assertTrue((checkout / "activitywatch_to_json.py").is_file())
        result = subprocess.run([sys.executable, "-X", "utf8", "-c", EXPORT_SYNTHETIC_DAY],
                                cwd=checkout, capture_output=True, text=True,
                                encoding="utf-8", timeout=30, check=True)
        self.assertNotIn("synthetic private title", result.stdout)
        self.assertNotIn("Synthetic host", result.stdout)
        day = date(2001, 1, 2)
        record = build_record(day, parse_export(result.stdout, [day])[day.isoformat()], None, [])
        self.assertEqual(record["pc"]["active"], 120)
        self.assertEqual(record["pc"]["afk"], 120)
        self.assertEqual(record["pc"]["unknown"], 86160)
        self.assertEqual(record["pc"]["window_unknown"], 1560)
        self.assertEqual(record["pc"]["segments"], [[0, 2, 2]])
        self.assertEqual(record["pc"]["apps"], [{"name": "Synthetic game", "seconds": 120}])
        self.assertEqual(record["pc"]["state"], "partial")
        self.assertEqual(record["sleep"]["state"], "missing")
