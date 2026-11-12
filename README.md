# 日々リズム（HibiRhythm）

睡眠とPC利用時間を1日単位で並べて見る、合言葉つきの共有サイトです。
「その日に何時間寝て、何時間PCを触ったか」を一目で確認し、見せたい相手と共有するために使います。

- 画面はサーバーを持たない静的サイト（HTML / CSS / JavaScript）で、GitHub Pages から配信します。
- 記録データは暗号化したファイルだけを公開します。合言葉を入力した人のブラウザ内で復号して表示します。
- このリポジトリの `main` ブランチにあるのはプログラムだけです。記録そのものは含みません。

## しくみ

```
ActivityWatch（このPC） ─ activitywatch_to_json.py ─┐
                                                    ├─ pipeline.build ─ 検証 ─ 暗号化 ─ dist/ ─ pipeline.deploy ─ gh-pages
Notion API（健康記録・睡眠セッション） ─────────────┘
```

| 場所 | 中身 | 公開 |
| --- | --- | --- |
| `main` ブランチ | プログラムと説明書 | 公開（記録は含まない） |
| `gh-pages` ブランチ | 画面のファイルと暗号化データ `data.enc.json` | 公開（合言葉がなければ読めない） |
| `data/`、`dist/`、`.env`、`.state/` | 平文の記録、ビルド結果、秘密情報、ログ | このPCの中だけ（Git管理外） |

## 初回の準備

1. 依存パッケージを入れる。

   ```powershell
   Set-Location "C:\Project\HibiRhythm"
   py -3.12 -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   ```

2. Notion で読み取り専用のインテグレーションを作り、「健康記録」と「睡眠セッション」のデータベースをそのインテグレーションへ共有する。
3. `.env.example` を `.env` にコピーし、インテグレーションのシークレット、2つのデータソースID、合言葉（12文字以上）を書く。`.env` はコミットしない。値をチャットやメモに貼らない。
4. GitHub に公開リポジトリを作り、`origin` として登録して `main` を push する。
5. 記録を取り込んでビルドし、公開する。

   ```powershell
   .\.venv\Scripts\python.exe -X utf8 -m pipeline.build --since 2026-11-06
   .\.venv\Scripts\python.exe -X utf8 -m pipeline.deploy --dry-run
   .\.venv\Scripts\python.exe -X utf8 -m pipeline.deploy
   ```

6. GitHub のリポジトリ設定で Pages の公開元を `gh-pages` ブランチのルートにする。
7. 毎日の自動更新を登録する（1回だけ）。

   ```powershell
   powershell -ExecutionPolicy Bypass -File "C:\Project\HibiRhythm\register_task.ps1"
   ```

## 日々の使い方

自動更新を登録したあとは、毎日 20:00 に前日までの記録を取り込み、公開データを更新します。手で動かす場合は次のとおりです。

| やりたいこと | コマンド（`C:\Project\HibiRhythm` で実行） |
| --- | --- |
| 直近14日を取り直してビルド | `.\.venv\Scripts\python.exe -X utf8 -m pipeline.build` |
| 過去分をまとめて取り込む | `.\.venv\Scripts\python.exe -X utf8 -m pipeline.build --since 2026-11-06` |
| 公開される内容だけ確認 | `.\.venv\Scripts\python.exe -X utf8 -m pipeline.deploy --dry-run` |
| 公開 | `.\.venv\Scripts\python.exe -X utf8 -m pipeline.deploy` |
| ビルドと公開をまとめて実行 | `.\.venv\Scripts\python.exe -X utf8 -m pipeline.daily` |
| 合成データで画面だけ確認（`dist-sample/` へ出力） | `.\.venv\Scripts\python.exe -X utf8 -m pipeline.build --sample` |
| テスト | `.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests` |

画面をこのPCで確認するときは、ビルド後に次を実行して `http://127.0.0.1:8765/` を開きます（合成データを見るときは `dist` を `dist-sample` に変えます）。

```powershell
.\.venv\Scripts\python.exe -m http.server 8765 --bind 127.0.0.1 --directory dist
```

合成データの合言葉は `pipeline/sample.py` の `PASSPHRASE` にあります。合成データは `dist-sample/` に出力され、公開用の `dist/` には入りません。

### 公開前の検査

`pipeline.deploy` は `dist/` を作業用フォルダへ写し、その写しを検査してから、同じ写しを送信します。次のどれかに当てはまると送信しません（`--dry-run` でも同じ検査をします）。

- ファイルが `index.html`、`app.js`、`style.css`、`robots.txt`、`.nojekyll`、`data.enc.json` の6つと一致しない（余分なファイルやフォルダがある、足りない）
- 画面のファイルが `site/` と違う（`site/` を直したあとビルドしていない）
- `data.enc.json` が所定の暗号形式でない（暗号化されていないJSONを含む）、または合成データである
- `.env` の合言葉で復号できない、または中の記録が検証を通らない（未来の日付、重複、範囲外の値など）

## 画面の見方

- **その日のサマリー**：睡眠とPCの時間、0〜24時のタイムライン、睡眠段階、歩数、使ったアプリ。
- **リズム表**：1行が1日。横軸は0〜24時で、睡眠とPC稼働を重ねて表示します。14日と30日を切り替えられます。
- **推移**：睡眠とPCの日別の棒グラフと期間平均。平均の分母は記録のある日だけです。
- **表で見る**：同じ内容の一覧表。印刷すると表も開いた状態で出力されます。

## 数値の決まり

- 1日は日本時間の0時から24時。日付をまたぐ睡眠は、その日に入る分だけを数えます。
- 睡眠時間は Notion「健康記録」の暦日集計（下限・上限）をそのまま使い、このプログラムでは計算し直しません。下限と上限が違う日は「◯時間〜◯時間」と幅で表示します。
- 「記録なし」「未同期」「未取得」「一部欠測」は0ではなく不明として表示します。推測で埋めません。睡眠の下限と上限の片方しかない日は「一部欠測」として、残っている数値を使いません。
- PCの時間は ActivityWatch が「操作中」と判定した時間（not-afk とウィンドウ記録の重なり）です。操作中の判定が6時間以上途切れなかった日は、機器の影響の可能性があるため「参考値」と表示します。操作中なのにアプリの記録が10分を超えて欠けている日は「一部欠測」として「◯時間以上」と表示し、平均とグラフには入れません。
- PCについて扱うのはアプリ名と時間だけです。ウィンドウタイトル、URL、入力内容は扱いません。

## 合言葉について

- 合言葉を知っている人は全員が記録を読めます。共有する相手にだけ、URLとは別の手段で伝えてください。
- 暗号文は公開されるため、短い合言葉は総当たりで破られる可能性があります。12文字以上の推測されにくい言葉にしてください。
- 画面で「この端末に合言葉を記憶する」を選ばなければ、合言葉は端末に保存されません。ページを再読み込みすると入力し直しになります。
- 合言葉を変えるときは、`.env` の `SITE_PASSPHRASE` を書き換えてから `pipeline.daily` を実行します。`gh-pages` は毎回履歴のない1コミットで置き換えるので、古い合言葉の暗号文はブランチに残りません。
- ただし、合言葉を変えても「すでに誰かが取得した古い暗号文」は古い合言葉で読めます。GitHub 側にも古いコミットがしばらく残ることがあります（[GitHub の説明](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository#about-sensitive-data-exposure)）。合言葉の変更で守れるのは、変更後に公開する記録だけです。

## 検索エンジンについて

- 検索に載せない設定として効いているのは、`index.html` の `noindex` です。
- `robots.txt` も置いていますが、このサイトは `https://<ユーザー名>.github.io/<リポジトリ名>/` という下の階層にあるため、検索エンジンは読みません（`robots.txt` はサイトの最上位にあるものだけが使われます。[Google の説明](https://developers.google.com/crawling/docs/robots-txt/robots-txt-spec)）。`noindex` を外さないでください。

## うまくいかないとき

- 自動更新の結果は `.state\daily.log`、`.state\daily-error.log`、`.state\history.log` に残ります。
- ビルドに失敗した日は公開を行いません。公開中のデータは前回のまま残ります。
- 「ActivityWatch の集計に失敗」：ActivityWatch が起動しているか確認します。
- 「Notion の読み取りに失敗」：`.env` のシークレットと、データベースがインテグレーションへ共有されているかを確認します。
- 「Notion「…」の列「…」を読めません」：Notion 側で列の名前か型が変わっています。空の値として取り込むと保存済みの記録を上書きしてしまうため、止めています。列を元に戻すか、`pipeline/collect_sleep.py` の列名を合わせます。
- 「保存済みレコード … 」「まだ終わっていない日のレコードです」など：`data\days\` のファイルに問題があります。表示されたファイルを確認します。
- 公開する最初の日は `.env` の `START_DATE` で決めます。それより前の日は、保存済みのレコードがあっても公開しません。
- PCの電源が入っていなかった日は更新されません。次に実行したとき、直近14日ぶんがまとめて補われます。
