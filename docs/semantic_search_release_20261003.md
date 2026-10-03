# 意味検索索引の本番有効化

## 反映した状態

2026-10-03、ユーザーの索引生成・本番有効化の指示を受け、Cohere Embed Multilingual v3の4,202冊・1,024次元の索引を生成した。入力は書名・著者・テーマ・確認済み紹介文。モデル呼び出し4,014回、キャッシュ利用189回（プローブを含む）。DBのSQLiteスナップショットと索引を組にし、両者のSHA256一致を確認。本番の全ID・書名とも一致する。

本番APIにDBと索引を同梱し、`SEMANTIC_INDEX_PATH=/var/task/semantic-index.json` を設定。既存ロールに東京の `cohere.embed-multilingual-v3` だけを許可する `BooksearchSemanticModel` インラインポリシーを追加した。`/api/books/semantic/status` は `{"available":true}`。フロントエンドは前のリリースに実装済みで、追加更新は不要だった。

起動時最大メモリが512MB設定で474MBだったため、APIを768MBへ変更した。変更後の1回の実測は最大475MB、初期化3,225ms。長時間の性能保証ではない。SAMにも有効時768MBを記録したが、優先OCRなどの手動管理リソースがあるため、今回もAPIのみ直接更新した。

配信ZIPは66,508,169バイト、展開後159,366,233バイト。コードSHA256（Base64）は `ipPyk+NZramjj/ILWXJV99Y+rGoVmUSzMI0Ie9GWJkI=`。対応DB SHA256は `eaff593f50dfb2fc478c9829f1100ec6cbf23da4ee06a7f513030fa9176504e6`。

## 検証と品質の限界

ローカルと本番で8検索を実モデルに送り、ジャンル・ページ数条件、除外ID、候補なし条件を確認した。本番のChromium/mobile Safariで26件の読み取り専用E2Eが成功。0件時の自動補完、手動追加、通常結果との重複除外、詳細へ遷移、日本語補正、完全一致除外、ホームを含む。手動追加テストは初回に描画待ちが不足して失敗したため、蔵書カードの描画を待つよう修正して全体を再実行した。

通常検索0件の「データクレンジング」でpandas前処理本、「英文読解」で英文リーディング・解釈本、「使いやすいアプリの画面を設計したい」でUIデザインの教科書を取得した。一方、「離乳食」には一般栄養本、無意味文字列には無関係な本を返す。上位にも周辺分野が混ざるため、全体の検索精度改善率を示す評価ではない。候補は「意味の近い本」として通常結果から分離する。

弱い候補の除外は次の課題。Cohere Rerank 3.5で、事前に目的を定めた12検索・各20候補を比較した。0.15未満を除く暫定基準では、データクレンジングは前処理本1冊となり、離乳食・無意味文字列・ワープエンジン修理・ドラゴン飼育は0件になる。ただし採用の実務本のスコアが0.129で落ちるなど、正しい候補を失う例もあり、閾値を確定するには小標本すぎる。スコアは関連性の確率ではない。[Cohereの校正手順](https://docs.cohere.com/docs/reranking-best-practices)も30〜50の代表検索と境界事例での確認を推奨する。

東京のCohere Rerank適用上限は3回/分。米国西部・東部の `list-service-quotas` は250を返したが、西部の `get-service-quota` の実適用値は3で、実呼び出しも制限された。一覧を実適用値と取り違えない。**Cohere Rerankは本番へ追加していない。** ユーザーがモデルの重さ・回数制限を懸念したため、関連性判定方法を再検討する。試作コードはGit管理外の `outputs/deploy-20261003-semantic/cohere-draft/` に退避した。

比較スクリプトは `scripts/evaluate_semantic_relevance.py`。明示実行で検索語・上位候補の書誌をBedrockへ送信し、従量課金が発生する。出力は `outputs/semantic-relevance/comparison.json` と米国西部の比較用 `outputs/semantic-relevance-us/comparison.json`。キャッシュ・DB・ベクトルはGitに追加しない。

## 再配信と復旧

本番ZIPはS3の `s3://booksearch-277707097118-ap-northeast-1/releases/2026-10-03-semantic/api.zip`。後続のAPI更新ではこのZIPまたは `outputs/semantic-serving/` のDB・索引を組で再利用し、bootstrapだけを新しいコードへ置き換える。リポジトリの元DBは同じ蔵書でもファイルSHAが異なるため、生成済み索引と混ぜない。カタログを変更した場合は索引を再生成する。

索引・キャッシュは `outputs/semantic-serving/`、更新前API ZIP・環境変数・IAMポリシー、配信ZIP、評価結果は `outputs/deploy-20261003-semantic/` に保存。本番を無効化するだけならAPIの索引パスを削除し、通常検索に戻す。完全復旧では保存したAPI ZIP・環境変数・512MB設定を戻して追加ポリシーを削除する。フロントエンド・OCR・DynamoDBを戻す必要はない。
