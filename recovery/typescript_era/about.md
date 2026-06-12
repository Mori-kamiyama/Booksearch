# Webプログラミング アクティブラーナー企画書

## 用件
授業で学べない高度な内容をもとに特別評価される取り組み。
＋v100プロジェクトに活用できるよう何らかのAI、シミュレーションが入っていること。

## 内容
本棚検索engine 「ホンノキ」
本棚の画像をWebカメラによってスキャン。それにより本棚のデジタルツインを作成し、本棚がある場所をARで教えるアプリ。
本を借りるやつのとなりにおくことでほしい本をすぐに検索できるようにする。

## 詳しい技術スタック

### フロントエンド
- AR kit
- next.js
- React

デプロイ先の検討　cloudflere firebase 自分のサーバー

### Webのバックエンド
- 本棚のOCR API
  - moondream
  - SAM 3
  - その他のOCRツール群
- 本のAPIとのマッチング
  - 楽天Book API
  - Google books API
- 本の完全一致＆ファジー検索
  - SQLか何かの一致検索
  - [Spanner](https://docs.cloud.google.com/spanner/docs/full-text-search/fuzzy-search?hl=ja)


DBの検討　firebase supabase ゴキブリDB 自分のサーバーでSQlite or Postgres
#### 中にあるべきデータ構造
- 書籍データ
  - この学校にある書籍のデータを保存しておく
  - ここからOCR時にマッチングさせる
- 本棚データ
  - 書籍と本棚の位置をマッチングさせる
