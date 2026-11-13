"""合成データを隔離したEdgeで表示し、操作と描画を確認する。"""

import argparse
from datetime import date, timedelta
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Thread
from time import sleep
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pipeline import build, sample
from pipeline.merge import build_record


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--edge", type=Path, default=Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"))
    args = parser.parse_args()
    with TemporaryDirectory() as directory:
        root = Path(directory)
        sample_dist = root / "dist-sample"
        today = date(2025, 1, 31)
        days = sample.generate(today)
        source = {"active_seconds": 0, "not_afk_seconds": 0, "afk_seconds": 0, "unknown_seconds": 86400,
                  "window_seconds": 1800, "window_unknown_seconds": 1800, "longest_not_afk_seconds": 0,
                  "long_not_afk": False, "hours": [0] * 24, "spans": [], "apps": []}
        days[-1] = build_record(today - timedelta(days=1), source, None, [])
        with patch.object(build, "SAMPLE_DIST_DIR", sample_dist):
            build.write_dist(days, sample.PASSPHRASE, True, today)
        server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=sample_dist))
        Thread(target=server.serve_forever, daemon=True).start()
        profile = root / "edge-profile"
        with subprocess.Popen([str(args.edge), "--headless=new", "--no-first-run", "--no-default-browser-check",
                               "--remote-debugging-port=0", f"--user-data-dir={profile}", "about:blank"],
                              creationflags=subprocess.CREATE_NO_WINDOW,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) as browser:
            try:
                port_file = profile / "DevToolsActivePort"
                for _ in range(100):
                    if port_file.exists():
                        break
                    sleep(0.1)
                debug_port = port_file.read_text(encoding="utf-8").splitlines()[0]
                subprocess.run(["node", str(Path(__file__).with_suffix(".mjs")), debug_port,
                                f"http://127.0.0.1:{server.server_port}/", sample.PASSPHRASE], check=True, timeout=30)
            finally:
                browser.terminate()
                browser.wait(timeout=10)
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    main()
