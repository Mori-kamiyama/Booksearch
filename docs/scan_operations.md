# Scan運用診断

## 検索対象棚の優先OCR（2026-10-01）

検索セッションの `priority_shelf_id` と一致するcropは `booksearch-ocr-priority-queue` に送り、専用の `booksearch-ocr-priority` Lambdaで処理する。予約同時実行は2、SQS mappingの最大同時実行も2、バッチは1。対象外のcropは従来のOCRキューで処理を続ける。両方のワーカーは同じOCRコードとDynamoDBの条件付きclaimを使うので、再配送による二重OCRはclaimで吸収する。対象棚が判別できないcropは通常キューへ送る。

今回は稼働中スタックへの影響を抑えるため、優先キューとLambda、IAM権限、dispatcherの設定とコードをAWS APIで直接反映した。`aws/template.yaml` に同じ構成を記録したが、**優先キューとLambdaは現時点でCloudFormation管理外**。次回のフル `sam deploy` 前に、これらをスタックへimportするか、運用中のメッセージがないことを確認して一旦削除しCloudFormationに再作成させる必要がある。同名リソースを残したままフルデプロイすると作成競合になる。

`aws/scripts/diagnose_scan.py` は、滞留しているスキャンを調べるためのread-only CLIです。JobsTableと、導入済みならScanTasksTableを全ページscanし、件数、状態別件数、最古更新からの経過秒数、leaseのactive/expired件数、scanの経過秒数、読み取りConsumedCapacity合計をJSONで出力します。DynamoDBには`status`、`state`、作成・更新日時、各leaseだけをProjectionして取得します。SQSはメッセージ本文を読まず、visible/in-flight/delayed件数とCloudWatchの`ApproximateAgeOfOldestMessage`だけを読みます。

```sh
uv run --no-project --with boto3 python aws/scripts/diagnose_scan.py \
  --jobs-table booksearch-jobs \
  --tasks-table booksearch-scan-tasks \
  --queue yolo="$YOLO_QUEUE_URL" \
  --queue ocr="$OCR_QUEUE_URL" \
  --queue ocr-priority="$OCR_PRIORITY_QUEUE_URL" \
  --queue lookup="$LOOKUP_QUEUE_URL" \
  --dlq yolo="$YOLO_DLQ_URL" \
  --dlq ocr="$OCR_DLQ_URL" \
  --dlq lookup="$LOOKUP_DLQ_URL"
```

旧版の環境では`--tasks-table`（または`SCAN_TASKS_TABLE`）を省略できます。その場合、`tables.scan_tasks.status`は`not_configured`となり、JobsTableとキューの診断は継続します。指定したScanTasksTableがまだデプロイされていない場合は`not_deployed`と表示します。権限エラーなど、それ以外のAWSエラーは終了して原因を確認します。

キューURLはCloudFormation Outputsまたは既存の安全な運用環境変数から渡してください。URL、job ID、task ID、メッセージ本文は診断結果に出ません。キューのRedrivePolicyからDLQ URLを解決できる場合は、DLQも自動的に診断します。権限や設定の都合で解決できない場合は`--dlq LABEL=QUEUE_URL`を追加してください。

判断の目安は次の通りです。

- `tables.jobs.status_counts` の`processing`、`ocr_pending`、`lookup_pending`は、`oldest_age_by_status_seconds`と合わせて確認する。
- `tables.scan_tasks.status_counts` の`pending`はYOLO配送待ち、`prepared`はmanifest作成後のtransactionまたは再配送待ち。`leases.*.expired`が増えていればworker停止や可視性期限切れを調べる。
- SQSの`visible`が増え、`in_flight`も高止まりする場合はworker実行時間、権限、外部OCR、S3/DynamoDBエラーを確認する。
- DLQの`visible > 0`またはCloudWatch age alarmがALARMなら、まず入力・Lambdaログ・失敗理由を確認してから個別の再送判断をする。

このCLIは削除、再送、visibility変更、DynamoDB更新を行いません。DLQからの復旧やleaseの変更は、原因と対象を確認した後の個別運用判断に限定します。

## CloudWatch通知

`aws/template.yaml` の `AlarmEmail` パラメータにメールアドレスを渡した場合だけ、6つのキュー/DLQ alarmからSNSトピックへ通知します。ALARMへの遷移とOKへの復帰を通知し、INSUFFICIENT_DATAは通知しません。空のままデプロイすれば、SNS購読もalarm通知先も作られません。

```sh
sam deploy --parameter-overrides "AlarmEmail=受信先のメールアドレス"
```

初回デプロイ後、SNSから届く購読確認メールのリンクを受信者が承認するまで通知は届きません。アドレスはリポジトリやこのドキュメントに保存せず、購読先を変更・解除するときはCloudFormationのパラメータ更新で管理します。

## 初回起動と表示速度の切り分け

YOLOのログ `model initialization seconds` はUltralyticsのimportとモデル構築の合計であり、Lambda全体のcold startや推論時間ではありません。LambdaのINIT_REPORT/REPORTと合わせて確認します。モデルは初回処理時に作成し、同じ実行環境で再利用します。設定ファイルは書き込み可能な `/tmp/matplotlib` と `/tmp/Ultralytics` に置きます。

おすすめ表示は `tests/` で次の読み取り専用測定を実行できます。

```sh
FRONTEND_URL=https://d2uel8nex1m4w7.cloudfront.net \
API_BASE=https://rx7ylpbzg6.execute-api.ap-northeast-1.amazonaws.com \
OUTPUT=/tmp/booksearch-featured-timing.json npm run measure:featured
```

20回を直列に測定し、毎回ブラウザcontextと保存領域を新しくします。APIを事前に取得するため、サーバーcold startの測定ではありません。最初のおすすめの文字が表示されるまでの時間、document受信、DOMContentLoaded、API要求、JS転送量を記録します。表紙画像の全件完了は待ちません。CPU/回線の制限はなく、測定中に他のブラウザテストを走らせない条件で比較します。

## ライブスキャン時の同時実行枠（2026-09-27）

東京リージョンのLambda上限は10。実動画スキャン時（21:13 JST）にYOLOの同時実行が10へ達し、同じ1分間でAPIのThrottlesが19、dispatcherが7発生した。503はこの枠競合によるものと判断した。

YOLO/OCR/lookupのSQS event source mappingそれぞれに `ScalingConfig.MaximumConcurrency: 2` を設定し、本番へ反映。3ワーカー合計最大6としてAPI・dispatcherの余地を作る。これはAPIの予約枠を保証するものではなく、他の処理・利用増加時は上限引き上げが必要。AWSの[MaximumConcurrency設定](https://docs.aws.amazon.com/lambda/latest/dg/services-sqs-scaling.html)を使用する。

Service Quotas `lambda / L-B99A9384` は現在10、Adjustable=true。増枠申請は未実施。まず50程度への増枠を検討し、承認後はAPI用の予約枠を確保してから各ワーカーの並列数を実測で調整する。現在のworkerはRecordsを順番に処理するため、BatchSizeを5にするだけでは5並列にはならない。OCRの外部API制限も別途守る。

フレーム初期化はsession IDとfilenameから一定のキーを生成し、条件付き書き込みで重複を防ぐ。PUTは同じ画像、commitは既存のdurable taskを用いて冪等に再試行。フロントは一時エラーに最大4回（待機0.5/1/2秒）試行し、失敗画像はセッション中保持する。保留送信は最大2つ、失敗後は新規キャプチャを止め、再確定時に失敗画像を再送する。キャンセル時は再試行を中断する。

存在しない任意のtags/detect endpointへの404/501後の呼び出しは停止。ブラウザ検知が動いている間もサーバーへの検知要求を抑制する。ブラウザ検知とサーバー検知が両方利用不可ならその旨を表示し、画像の非同期解析で確認する。

### 増枠申請の障害（2026-09-27）

ユーザーの依頼で10→50をService Quotas APIへ申請したが、IllegalArgumentException（標準値1000より大きい値が必要）で拒否され、申請IDは作成されなかった。Support APIもSubscriptionRequiredExceptionで利用不可。数値を1001以上に変えて迂回はしていない。FirefoxでSupport Centerを開いたがAWSサインインが必要。現時点の上限は10のまま。

ログイン後にサポートへ提出する内容:

- 対象: AWS Lambda / Concurrent executions / ap-northeast-1
- 現在10、希望50。Service Quotasの通常申請は標準1000未満として拒否されるため、低いアカウント上限の引き上げを依頼。
- 用途: 本棚スキャンの画像検出・OCR・書籍照合とWeb API。5人程度の同時利用を予定。
- 実測: 単一利用時でも画像処理が同時実行枠を占有し、APIがスロットリング。現在は各SQS workerの並列数を2へ制限し緩和済み。増枠後もworkerを制限し、APIの余力を確保する。

### サポートケース送信済み（2026-09-27 21:43 JST）

ログイン後、Basicサポートの「サービスの制限緩和 / 全般」から東京リージョンのLambda上限10→50を申請した。ケース番号は `179051301300395`。提出内容がケース詳細のやりとりに表示されたことを確認。連絡方法はウェブ（アカウントのメール/Support Center）、有料プランへの変更なし。申請受付と増枠承認は別で、まだ承認・適用は未確認。

[サポートケース](https://support.console.aws.amazon.com/support/home#/case/?displayId=179051301300395&language=ja)

次はAWSの回答と実際のquota値を確認し、承認後にAPI予約枠・worker並列数を調整して5人同時の動画スキャン負荷試験を行う。
