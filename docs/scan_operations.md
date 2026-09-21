# Scan運用診断

`aws/scripts/diagnose_scan.py` は、滞留しているスキャンを調べるためのread-only CLIです。JobsTableと、導入済みならScanTasksTableを全ページscanし、件数、状態別件数、最古更新からの経過秒数、leaseのactive/expired件数、scanの経過秒数、読み取りConsumedCapacity合計をJSONで出力します。DynamoDBには`status`、`state`、作成・更新日時、各leaseだけをProjectionして取得します。SQSはメッセージ本文を読まず、visible/in-flight/delayed件数とCloudWatchの`ApproximateAgeOfOldestMessage`だけを読みます。

```sh
uv run --no-project --with boto3 python aws/scripts/diagnose_scan.py \
  --jobs-table booksearch-jobs \
  --tasks-table booksearch-scan-tasks \
  --queue yolo="$YOLO_QUEUE_URL" \
  --queue ocr="$OCR_QUEUE_URL" \
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
