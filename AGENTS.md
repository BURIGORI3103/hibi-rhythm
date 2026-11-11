# `HibiRhythm`（日々リズム）プロジェクト専用 AGENTS.md

## 1. プロジェクト概要

1. **名称**：日々リズム（フォルダ名：`HibiRhythm`）
2. **目的**：睡眠（Notion「健康記録」「睡眠セッション」）とPC利用時間（ActivityWatch）を1日単位で統合し、濱田くんが選んだ相手へ共有できる合言葉つきの静的サイトとして GitHub Pages へ公開する。
   - リポジトリは公開されるため、共有相手の属性や健康状態など、個人の事情が分かる記述をリポジトリ内の文書・コメント・コミットメッセージへ書かないこと。
3. **正本仕様書**：`C:\Project\HibiRhythm\project_context.md`。作業前に必ず読むこと。

## 2. Git 管理

1. 独立した Git リポジトリ（ブランチ：`main`）。Git 操作・ビルド・テストは必ず `C:\Project\HibiRhythm` へ移動してから実行する。
2. GitHub のリポジトリは**公開**である。`main` に置いてよいのはプログラムと説明書だけ。
3. `gh-pages` ブランチは `pipeline/deploy.py` だけが書き換える。手作業でコミットや push をしない。

## 3. 絶対に守ること（個人の健康記録を扱うため）

1. `data/`、`dist/`、`dist.tmp/`、`.env`、`.state/` をコミットしない。`.gitignore` からこれらを外さない。
2. 実際の記録（日付ごとの睡眠・PC時間・アプリ名・Notionのメモ）を、テスト、フィクスチャ、ドキュメント、コミットメッセージ、チャットへの貼り付けに使わない。テストは合成データだけで書く。
3. 合言葉（`SITE_PASSPHRASE`）と Notion のシークレット（`NOTION_TOKEN`）を、ソース・ログ・ドキュメント・チャットへ書かない。表示もしない。
4. `pipeline.deploy`（`--dry-run` なし）と `pipeline.daily` は、公開中のサイトを即座に書き換える外部公開操作である。濱田くんの明示的な指示があるときだけ実行する。確認には `pipeline.deploy --dry-run` を使う。
5. 暗号形式は `pipeline/encrypt.py` と `site/app.js` の `decrypt` が対になっている。片方だけを変更しない。
6. 「不明は不明として出す・推測で補わない」を維持する。記録なし・未同期・未取得を0として表示・集計しない。睡眠時間は Notion の暦日集計をそのまま使い、再計算しない。
7. PC活動はアプリ名と時間だけを扱う。ウィンドウタイトル・URL・入力内容を取り込む変更をしない。
8. Notion へは読み取りだけを行う。書き込み処理を追加しない。
9. 画面（`site/`）に外部のスクリプト・フォント・解析タグを追加しない。`index.html` の Content-Security-Policy を緩めない。

## 4. 主要なファイル

- `pipeline/config.py`：パスと設定値、`.env` の読み込み
- `pipeline/collect_pc.py`：`C:\Project\ActivityWarch\activitywatch_to_json.py` をその venv で実行して PC 集計を受け取る
- `pipeline/collect_sleep.py`：Notion API から2つのデータソースを読み取る
- `pipeline/merge.py`：1日1レコードへの統合と値の検証
- `pipeline/encrypt.py`：PBKDF2-SHA256（60万回）→ AES-256-GCM
- `pipeline/build.py`：収集 → 統合 → 検証 → 暗号化 → `dist/` 生成
- `pipeline/deploy.py`：`dist/` を `gh-pages` へ履歴なしの単一コミットで force push
- `pipeline/daily.py`：自動実行の入口（ビルド成功時のみ公開）
- `pipeline/sample.py`：画面確認用の合成データ
- `site/`：画面（`index.html`、`app.js`、`style.css`、`robots.txt`）
- `tests/test_pipeline.py`：統合・検証・暗号化・Notion行の解釈のテスト
- `run_daily.ps1`、`register_task.ps1`：Windows タスク（`HibiRhythm-Daily`、毎日 13:30）用

## 5. 実行コマンド

Python は `C:\Project\HibiRhythm\.venv\Scripts\python.exe`（3.12）。依存は `cryptography` と `tqdm`。

1. テスト：
   `Set-Location "C:\Project\HibiRhythm"; & ".\.venv\Scripts\python.exe" -X utf8 -m unittest discover -s tests`
2. 合成データでビルド（画面確認用・公開不可）：
   `Set-Location "C:\Project\HibiRhythm"; & ".\.venv\Scripts\python.exe" -X utf8 -m pipeline.build --sample`
3. 実データでビルド（直近14日を取り直す。`--since YYYY-MM-DD` で過去分）：
   `Set-Location "C:\Project\HibiRhythm"; & ".\.venv\Scripts\python.exe" -X utf8 -m pipeline.build`
4. 公開内容の確認（送信しない）：
   `Set-Location "C:\Project\HibiRhythm"; & ".\.venv\Scripts\python.exe" -X utf8 -m pipeline.deploy --dry-run`
5. 画面のローカル確認：ビルド後に `dist/` を `http://127.0.0.1:8765/` で配信する（`C:\Project\.claude\launch.json` の `hibi-rhythm-preview`）。

## 6. 関連プロジェクトと同期対象

1. PC 集計の正本は `C:\Project\ActivityWarch` にある。集計ロジックを本プロジェクトへ複製しない。JSON の項目を変えるときは `activitywatch_to_json.py` と `pipeline/merge.py` を同時に直す。
2. 本プロジェクトは `add_update.py`、XO の仕様書同期、Gemini Book、一括 push バッチの登録対象外である。登録する場合は濱田くんの指示を受けてから行う。
