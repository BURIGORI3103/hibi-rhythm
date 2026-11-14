"""公開処理のGitコミットと排他を隔離された合成データで確認する。"""

from contextlib import ExitStack, redirect_stderr, redirect_stdout
from datetime import datetime
from io import StringIO
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from pipeline import build, deploy, sample
from pipeline.config import JST, PUBLISH_FILES, SITE_DIR


@unittest.skipUnless(shutil.which("git"), "Git が必要")
class DeployCommitTests(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        root = Path(stack.enter_context(TemporaryDirectory()))
        self.dist = root / "dist"
        site = root / "source"
        shutil.copytree(SITE_DIR, site)
        page = site / "index.html"
        page.write_bytes(page.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
        today = datetime.now(JST).date()
        with patch.multiple(build, DIST_DIR=self.dist, SITE_DIR=site):
            build.write_dist(sample.generate(today), sample.PASSPHRASE, False, today)
        stack.enter_context(patch.object(deploy, "DIST_DIR", self.dist))
        stack.enter_context(patch.object(deploy, "load_env", return_value={"SITE_PASSPHRASE": sample.PASSPHRASE}))
        inspect = deploy.inspect
        stack.enter_context(patch.object(deploy, "inspect",
                                         side_effect=lambda *args, **kwargs: inspect(*args, **(kwargs | {"site": site}))))
        self.real_git = deploy.git
        self.pushed = False
        self.committed = {}
        self.push_result = None
        self.simulate_mismatch = False
        self.remote = "https://synthetic:placeholder@example.invalid/project"
        self.use_bare_remote = False
        self.before_push = None
        stack.enter_context(patch.object(deploy, "git", side_effect=self.git))
        stack.enter_context(patch.dict(os.environ, {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.autocrlf",
                                                      "GIT_CONFIG_VALUE_0": "true"}))

    def git(self, *args, cwd, **kwargs):
        if args[0] == "remote":
            return str(self.remote)
        if args[0] == "config" and args[-1] in ("user.name", "user.email"):
            return "synthetic@example.invalid" if args[-1] == "user.email" else "Synthetic"
        if args[0] == "cat-file" and self.simulate_mismatch:
            return b"synthetic mismatch"
        if args[0] == "ls-remote" and not self.use_bare_remote:
            return ""
        if args[0] == "push":
            self.pushed = True
            if self.use_bare_remote:
                if self.before_push:
                    self.before_push(cwd)
                return self.real_git(*args, cwd=cwd, **kwargs)
            script = ("import sys; from pathlib import Path; from pipeline.build import output_lock\n"
                      "try:\n    with output_lock(Path(sys.argv[1])): pass\n"
                      "except OSError:\n    sys.exit(23)\n")
            self.push_result = subprocess.run([sys.executable, "-c", script, str(self.dist)], timeout=30).returncode
            return ""
        result = self.real_git(*args, cwd=cwd, **kwargs)
        if "commit" in args:
            self.committed = {name: subprocess.check_output(["git", "cat-file", "blob", f"HEAD:{name}"], cwd=cwd)
                              for name in PUBLISH_FILES}
        return result

    def test_dry_run_preserves_verified_file_bytes_and_omits_remote_credentials(self):
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(deploy.main(["--dry-run"]), 0)
        self.assertFalse(self.pushed)
        self.assertNotIn("synthetic:placeholder", output.getvalue())
        self.assertEqual(self.committed, {name: (self.dist / name).read_bytes() for name in PUBLISH_FILES})

    def test_output_lock_is_held_until_mocked_push_finishes(self):
        with redirect_stdout(StringIO()):
            self.assertEqual(deploy.main([]), 0)
        self.assertTrue(self.pushed)
        self.assertEqual(self.push_result, 23)

    def test_mismatched_git_blob_stops_before_push(self):
        self.simulate_mismatch = True
        error = StringIO()
        with redirect_stderr(error):
            self.assertEqual(deploy.main([]), 1)
        self.assertIn("検査時の内容と一致しません", error.getvalue())
        self.assertFalse(self.pushed)

    def test_publish_rejects_concurrent_change_of_remote_ref(self):
        self.remote = self.dist.parent / "synthetic-remote.git"
        self.real_git("init", "--bare", "--quiet", str(self.remote), cwd=self.dist.parent)
        self.use_bare_remote = True
        branch = "synthetic-pages"
        ref = f"refs/heads/{branch}"
        with patch.object(deploy, "BRANCH", branch), redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            self.assertEqual(deploy.main([]), 0)
            initial = self.real_git("rev-parse", ref, cwd=self.remote)

            def publish_concurrent(work):
                self.real_git("fetch", str(self.remote), ref, cwd=work)
                tree = self.real_git("rev-parse", "HEAD^{tree}", cwd=work)
                new = self.real_git("-c", "user.name=Synthetic", "-c", "user.email=synthetic@example.invalid",
                                    "commit-tree", tree, "-p", initial, "-m", "並行処理の合成コミット", cwd=work)
                self.real_git("push", str(self.remote), f"{new}:{ref}", cwd=work)

            self.before_push = publish_concurrent
            self.assertEqual(deploy.main([]), 1)
            self.assertEqual(self.real_git("rev-parse", f"{ref}^", cwd=self.remote), initial)
