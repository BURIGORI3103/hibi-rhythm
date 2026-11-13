"""dist/ を gh-pages ブランチへ公開する。

古い暗号文をブランチの履歴に溜めないよう、履歴を持たない単一コミットに置き換える。
公開準備を始めた時点のリモート先端を指定し、並行更新があった場合は置き換えを中断する。

dist/ をまず作業用フォルダへ写し、その写しを検査して、同じ写しを送信する。検査のあとで
dist/ が書き換わっても、検査していない内容が送信されることはない。

  python -m pipeline.deploy --dry-run   送信せず、検査と公開されるファイルの表示だけ
  python -m pipeline.deploy             公開
"""

import argparse
from datetime import datetime
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

from . import encrypt
from .build import output_lock, start_date
from .config import (DATA_FILE, DIST_DIR, JST, PUBLISH_FILES, ROOT, SCHEMA_VERSION, SITE_DIR, STATIC_FILES,
                     TIMEZONE_NAME, load_env)
from .merge import validate_dataset

BRANCH = "gh-pages"


def git(*args, cwd, raw=False):
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True)
    if result.returncode != 0:
        if args[0] in ("ls-remote", "push"):
            raise RuntimeError(f"git {args[0]} に失敗しました。並行更新、認証、ネットワーク、リモートの設定を確認してください")
        reason = result.stderr.decode("utf-8", errors="replace").strip().splitlines()
        raise RuntimeError(f"git {args[0]} に失敗：{reason[-1] if reason else '原因不明'}")
    return result.stdout if raw else result.stdout.decode("utf-8").strip()


def publish_entries(folder):
    """公開対象の正規ファイルを列挙する。リンクや余分な項目を写さない。"""
    if folder.is_symlink() or folder.is_junction():
        raise RuntimeError("公開出力フォルダがリンクです")
    entries = {path.name: path for path in folder.iterdir()}
    odd = sorted(name for name, path in entries.items() if path.is_symlink() or not path.is_file())
    extra, missing = sorted(set(entries) - PUBLISH_FILES), sorted(PUBLISH_FILES - set(entries))
    if odd or extra or missing:
        parts = [f"{label}：{'、'.join(names)}" for label, names in
                 (("ファイル以外", odd), ("想定外", extra), ("不足", missing)) if names]
        raise RuntimeError(f"公開してよいファイルの一覧と一致しません（{' ／ '.join(parts)}）")
    return entries


def inspect(folder, passphrase, today, start=None, site=SITE_DIR):
    """許可されたファイルの内容と暗号化形式、復号後の全レコードを検査する。"""
    entries = publish_entries(folder)
    for name in STATIC_FILES:
        if entries[name].read_bytes() != (site / name).read_bytes():
            raise RuntimeError(f"{name} が site/ と一致しません。ビルドし直してください")
    if entries[".nojekyll"].stat().st_size:
        raise RuntimeError(".nojekyll に内容があります")
    try:
        envelope = json.loads(entries[DATA_FILE].read_text(encoding="utf-8"))
        encrypt.check_envelope(envelope)
    except ValueError as error:
        raise RuntimeError(f"{DATA_FILE} を公開できません：{error}") from None
    try:
        dataset = json.loads(encrypt.decrypt(envelope, passphrase))
    except ValueError:
        raise RuntimeError(f"{DATA_FILE} を .env の合言葉で復号できません。ビルドし直してください") from None
    if (not isinstance(dataset, dict) or dataset.get("sample") is not False
            or dataset.get("schema") != SCHEMA_VERSION or dataset.get("timezone") != TIMEZONE_NAME
            or not isinstance(dataset.get("days"), list)):
        raise RuntimeError(f"{DATA_FILE} の中身が公開用の形式ではありません（合成データの可能性があります）")
    try:
        validate_dataset(dataset["days"], today, start)
    except ValueError as error:
        raise RuntimeError(f"{DATA_FILE} の記録が検証を通りません：{error}") from None
    return len(dataset["days"])


def copy_dist(target, work):
    """呼び出し元がロックを保持した状態で、完了済みの出力だけを写す。"""
    pending = target.with_name(target.name + ".tmp") / ".pending"
    if pending.exists():
        raise RuntimeError("前回のビルドが途中で中断しました。先にビルドし直してください")
    if not (target / DATA_FILE).exists():
        raise RuntimeError(f"dist/{DATA_FILE} がありません。先に python -m pipeline.build を実行してください")
    publish_entries(target)
    shutil.copytree(target, work, symlinks=True)


def remote_head(remote, ref):
    """初回公開は空の期待値を使う。読み取り失敗や不明な応答では停止する。"""
    result = git("ls-remote", "--refs", "--", remote, ref, cwd=ROOT)
    if not result:
        return ""
    oid, separator, name = result.partition("\t")
    if not separator or name != ref or re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", oid) is None:
        raise RuntimeError("公開先ブランチのコミットを一意に確認できません")
    return oid


def main(argv=None):
    parser = argparse.ArgumentParser(description="dist/ を gh-pages へ公開する")
    parser.add_argument("--dry-run", action="store_true", help="送信せず内容だけ確認する")
    args = parser.parse_args(argv)
    attempted_push = False
    try:
        env = load_env()
        name = git("config", "user.name", cwd=ROOT)
        email = git("config", "user.email", cwd=ROOT)
        with output_lock(DIST_DIR), tempfile.TemporaryDirectory(prefix="hibi-deploy-") as folder:
            if not args.dry_run:
                remote = git("remote", "get-url", "--push", "origin", cwd=ROOT)
                ref = f"refs/heads/{BRANCH}"
                expected_head = remote_head(remote, ref)
            work = Path(folder) / "site"
            copy_dist(DIST_DIR, work)
            count = inspect(work, env.get("SITE_PASSPHRASE", ""), datetime.now(JST).date(), start_date(env))
            verified = {file: (work / file).read_bytes() for file in sorted(PUBLISH_FILES)}
            git("init", "--quiet", "--template=", "--initial-branch", BRANCH, cwd=work)
            git("config", "--local", "core.hooksPath", ".git/disabled-hooks", cwd=work)
            for file in verified:
                # Git の改行変換や属性フィルターを通さず、検査したファイルをステージする。
                object_id = git("hash-object", "-w", "--no-filters", "--", file, cwd=work)
                git("update-index", "--add", "--cacheinfo", f"100644,{object_id},{file}", cwd=work)
            message = f"公開データを更新 {datetime.now(JST).isoformat(timespec='minutes')}"
            git("-c", f"user.name={name}", "-c", f"user.email={email}", "commit", "--quiet", "-m", message, cwd=work)
            files = git("ls-tree", "-r", "--name-only", "HEAD", cwd=work).splitlines()
            if set(files) != PUBLISH_FILES or len(files) != len(PUBLISH_FILES):
                raise RuntimeError("コミットされるファイルが、公開してよいファイルの一覧と一致しません")
            for file, expected in verified.items():
                if git("cat-file", "blob", f"HEAD:{file}", cwd=work, raw=True) != expected:
                    raise RuntimeError(f"コミットの {file} が検査時の内容と一致しません")
            if args.dry_run:
                print(f"確認のみ（未送信）：検査を通過。{count}日分、{len(files)}ファイル → {BRANCH}")
                for file in files:
                    print(f"  {file}")
                return 0
            attempted_push = True
            git("push", f"--force-with-lease={ref}:{expected_head}", "--", remote, f"{ref}:{ref}", cwd=work)
    except (RuntimeError, OSError) as error:
        suffix = "公開先の状態を確認してください" if attempted_push else "送信していません"
        print(f"中断：{error}（{suffix}）", file=sys.stderr)
        return 1
    print(f"公開完了：{count}日分、{len(files)}ファイルを {BRANCH} へ送信しました")
    return 0


if __name__ == "__main__":
    sys.exit(main())
