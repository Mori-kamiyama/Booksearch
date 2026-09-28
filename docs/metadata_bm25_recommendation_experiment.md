# メタデータ収集とキーワード BM25 推薦（初期実験）

## 目的

「この本を読むあなたに」を、現在のランダム表示から、蔵書の書誌情報に基づく説明可能な関連本推薦へ置き換えるための初期実験です。

この段階では**貸出履歴・個人情報・閲覧履歴を使いません**。タイトル、著者、分類番号、出版社と、Google Books から補完できた内容紹介・カテゴリだけを使います。

## 追加したもの

- `scripts/enrich_book_metadata.py`
  - Google Books Volumes API から `description`、`categories`、`pageCount`、`language` を取得
  - ISBN の完全一致を最優先
  - ISBN がない場合はタイトル・著者の類似度で保守的に照合
  - 結果を `book_metadata` に保存
- `scripts/build_bm25_recommendations.py`
  - 書誌・メタデータをトークン化して Okapi BM25 を計算
  - 同一著者・近い分類番号に小さな加点
  - 推薦スコアと推薦理由を `book_recommendations` に保存
- `scripts/recommendation_utils.py`
  - 照合、トークナイズ、BM25 の依存なし実装

## DB テーブル

`book_metadata` は外部書誌の取得状態も保存します。

- `matched`: 十分な確度で照合できた
- `not_found`: 候補がなかった
- `ambiguous`: 候補はあるが誤照合を避けるため採用しなかった
- `temporary_error`: 429、5xx、タイムアウトなど。後で再試行可能
- `error`: 再試行しても改善しにくいエラー

`book_recommendations` の `strategy` は `bm25-keyword-v1` です。将来Embeddingや貸出統計を導入しても、方式別の結果を共存させられます。

## 実行方法

対象にするSQLite DBは `books` テーブルを持つカタログDBです。`backend/dev.db` が空の場合は実データDBを指定してください。

```bash
# まず10冊だけ照合し、DBを更新せずに件数を確認する
uv run python scripts/enrich_book_metadata.py \
  --db path/to/library.db \
  --limit 10 \
  --dry-run

# 問題なければ100冊を保存する。規約・レート制限に配慮して間隔を置く
uv run python scripts/enrich_book_metadata.py \
  --db path/to/library.db \
  --limit 100 \
  --delay 0.5

# 取得済み内容を含めてBM25を試算する。dry-runはDBを書き換えない
uv run python scripts/build_bm25_recommendations.py \
  --db path/to/library.db \
  --limit-books 100 \
  --top-k 6 \
  --dry-run

# 問題なければ推薦を保存する
uv run python scripts/build_bm25_recommendations.py \
  --db path/to/library.db \
  --top-k 6

# ネットワーク不要のテスト
uv run --no-project --with pytest pytest tests/test_book_recommendations.py tests/test_recommendation_pipeline.py -q
```

特定の本だけを調べる場合は、両スクリプトで `--book-id 123` を使えます。収集済みの `matched` 行を取り直す場合は `--refresh` を指定します。

## 推薦方式

依存パッケージを追加せず、次のトークンでBM25を計算します。

- 英数字の単語（例: `python`）
- 日本語の文字2-gram・3-gram
- タイトルは5倍、著者とカテゴリは3倍、分類番号は2倍に重み付け
- 内容紹介・出版社は1倍

BM25の後に、同一著者へ `+0.45`、分類番号の共通接頭辞へ最大 `+0.35` を加えます。返却時には「同じ著者の作品」「分類が近い本」「同じカテゴリの本」「内容のキーワードが近い本」のいずれかを理由として保存します。

## Google Books API の注意

- API呼び出しは収集スクリプトの明示実行時のみです。通常の書籍詳細画面では呼びません。
- APIキーはソースやDBへ保存せず、必要な場合だけ環境変数 `GOOGLE_BOOKS_API_KEY` を使います。
- 429 / 5xx / タイムアウトは `temporary_error` として保存し、後の実行で再試行できます。
- タイトルのみの弱い一致は採用しません。誤った説明文を推薦に使うより、欠損のままにする方を優先します。
- Google Booksの利用規約、APIのクォータ、書誌データの扱いを運用前に確認します。

## 評価

最初は100冊程度で以下を記録します。

1. ISBN保有率
2. `matched` / `not_found` / `ambiguous` / `temporary_error` の件数
3. `description`・カテゴリの取得率
4. 代表的な20冊について、上位6冊が関連しているかを人手で評価
5. 同一著者・同一大分類ばかりになっていないか

BM25はベースラインです。説明文取得率が十分高く、手動評価で不十分なら、次段階として日本語Embeddingや、取得可能性が確認できた貸出統計を別のスコアとして加えます。


## API・画面への接続（2026-09-21）

AWS/ローカルとも `GET /api/books/:id/related` で `bm25-keyword-v1` の上位6冊を読む。対象本自身・存在しない本・別方式を除外し、詳細画面で保存済み理由を表示する。データ未作成は空、取得失敗はエラー表示とし、無関係な週次おすすめへ置換しない。

外部メタデータが未取得でも既存の書名・著者・分類で計算できる。`--book-id` は計算する対象本だけを制限し、候補は全蔵書から選ぶ。`--limit-books` は実験用に候補集合自体を制限する。dry-runはDDLも残さない。

配信用DBのコピーで計算・整合性確認を終えてから、APIとlookupの同梱DBを同じ版へ揃えてビルドする。推薦を再生成してもbooks/表紙/棚候補の既存テーブルは変更しない。人による関連性・多様性の評価と、自然文検索・AI要約は別の残件。

配信時は `--conservative` を指定する。通常モードの文字片一致だけでは内容的に無関係な候補があり、書名だけの内容推定に使わない。保守モードは同一著者・分類3文字・共通カテゴリを条件にし、同一ISBN・同名同著者を除外する。ISBNは重複判定にのみ使い、関連性のスコアには入れない。
