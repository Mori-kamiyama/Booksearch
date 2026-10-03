# 通常検索の意味検索による補完

## 方針と実装

2026-10-03、通常検索と誤字補正の後で0件なら、ベクトル検索の上位最大5冊を「意味の近い本」として別枠表示する。通常結果がある場合は「関連する本も探す」を押したときだけ実行する。完全一致がないだけでは発動しない。ISBN指定、`exact=1`、空文字、記号のみ、200文字超は対象外。

通常検索は従来の件数・順位・ページ分割を維持する。補完は通常結果の描画後に読み込み、画面にある本を除外する。詳細リンクに検索語と絞り込みを保持する。戻ったときの手動補完結果は再取得が必要。別ページにある通常ヒット全件を除外する仕様ではない。

ローカルGoとAWS Goに `/api/books/semantic/status` と `/api/books/semantic` を追加。両方とも既存のSQL条件で著者・ジャンル・テーマ・ページ数・レベルを絞り込んでからコサイン順位を計算する。条件が適用できない場合はエラーとする。検索文だけをCohere Embed Multilingual v3へ送り、書籍のベクトルは事前生成する。Rerankは使わない。インスタンスごとに同時実行1、検索文キャッシュ最大128件、自動リトライなし、サーバー8秒・UI9秒で打ち切る。インスタンスをまたぐ課金上限や利用者単位の制限ではない。

生成物はモデル・次元・重複ID・非ゼロ有限値・カタログSHA256を検証する。異なる蔵書DBや未生成の索引なら無効化し、通常検索を続ける。ローカルも意味検索有効時は読み取り専用スナップショットで配信し、WALによるファイル変更を避ける。

## 準備と有効化

AWSを呼ばない準備:

```sh
uv run --no-project python scripts/build_semantic_index.py \
  --db aws/functions/go_api/library.db
```

SQLite backupでWALの内容を含む整合したスナップショットを `outputs/semantic-serving/library.db` に作り、元DBは変更しない。`prepared.json` に送信項目・文字数・3冊の入力例を保存する。今回の準備では4,202冊、紹介文3,380冊、合計1,252,896文字、1冊最大2,000文字。送信項目は書名、著者、テーマのラベル、確認済み紹介文。ISBNや棚位置・画像は送信しない。

生成は明示的な `--generate` が必要。以前の全蔵書送信の承認レビュー拒否を踏まえ、今回は準備まで実行。AWS送信・従量課金の確認後に以下を実行する。新規キャッシュならプローブ1回＋書籍4,202回。中断後は書籍テキスト・モデル・用途ごとの既存キャッシュを使う。生成失敗時は索引を公開せず、完了したベクトルキャッシュは残す。

```sh
uv run scripts/build_semantic_index.py \
  --db aws/functions/go_api/library.db --generate

SEMANTIC_INDEX_PATH="$PWD/outputs/semantic-serving/index.json" \
  go -C backend run . --library-db "$PWD/outputs/semantic-serving/library.db"
```

AWS用は `prepare_assets.sh` で通常のカタログ生成を終えてからベクトルを生成し、生成したDBと索引を必ず組で配置する。Makefileは索引があるときだけ同梱する。SAMの `SemanticSearchEnabled` は既定false。true時のみ索引パスと対象モデル1つのInvokeModel権限を付ける。本番デプロイは未実施。

```sh
cp outputs/semantic-serving/library.db aws/functions/go_api/library.db
cp outputs/semantic-serving/index.json aws/functions/go_api/semantic-index.json
```

## 品質に関する仮説と限界

短い関連語の0件を救う可能性は[30冊の実験](semantic_search_experiment.md)で確認済み。ただし全蔵書での目的適合性は未評価。該当本がない語にも最近傍は返るので、通常ヒットや所蔵確認とは分けて表示する。既存実験で正例・負例の類似度が逆転していたため、一律の類似度閾値を根拠なく追加しない。

次の評価は同じ4,202冊で、既知の書名、短い関連語、誤字、該当なしを含める。上位5冊の目的適合性と無関係候補の混入、待ち時間を確認して自動補完の公開を判断する。内容紹介がない本の検索品質と、入力が既知の書名かテーマかを区別しない0件補完は未解決。

## 検証

ローカル・AWSのGoテスト、Python10件、検索語判定2件、検索UI回帰25件、フロントエンドbuild、SAM lintが成功。UI回帰には自動補完、手動追加、条件維持、ISBN/完全一致除外、失敗時の再試行、遅い旧応答の排除、320px表示を含む。API/順位テストのベクトルとモデル応答は人工データであり、実モデルの精度評価ではない。
