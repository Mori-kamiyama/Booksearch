# スキャン確定処理の配送と復旧

後続の受付→YOLO→OCRの耐久性対応と最新の検証結果は [scan_delivery_and_featured_cache.md](scan_delivery_and_featured_cache.md) を参照。本書の対象外・検証件数はA22実施時の記録。

## 防ぐ障害

ライブスキャン確定時に、DynamoDB の `final_lookup_queued` だけが保存され、SQS への送信前に Lambda が終了すると検索結果が完成しなかった。送信エラー時のフラグ巻き戻しも別の書き込みなので、停止への保証にならない。

API・OCR・YOLO は、最終照合に進めるジョブに `status=lookup_pending` と `final_lookup_outbox_version=1` を単一の条件付き更新で保存する。送信予定そのものを JobsTable に残し、API はその永続化を受理の境界とする。`final_lookup_queued` は旧コードとの互換用であり、SQS 配送完了の証拠ではない。

## 配送

- DynamoDB Streams の新旧画像から outbox version が新設されたイベントを検出し、LookupQueue に送信する。同じ version を持つ後続更新は無視する。
- SQS 送信に失敗したレコードは部分バッチ失敗として再試行する。
- 5 分間隔の再照合処理が JobsTable を全ページ読み、`lookup_pending` かつ version 1 のジョブを再送する。Streams の保持期間を越えた停止にも、DB の送信予定が残っている限り対応する。
- カウンタ更新後、送信予定の保存前に停止したジョブも再照合する。閉じたライブジョブのフレーム/OCR 完了、または単発ジョブの OCR 完了を DB の条件式でも確認し、送信予定を補う。
- 送信済みフラグを追加して再送を止めない。受信完了の `done` で再送対象から外れる。送信後に応答を失うケースも想定して重複配送を許す。

Streams の保持期間は 24 時間。SQS/SNS への失敗通知だけでは元のイベント全体を保存できないため、今回は DB の送信予定を再照合の基準にした（[AWS Lambda の DynamoDB エラー処理](https://docs.aws.amazon.com/lambda/latest/dg/services-dynamodb-errors.html)）。

## 受信と結果の確定

最終 lookup は期限付き所有権を条件付きで取得する。実行中の二重配送は再試行し、期限切れなら別の実行が引き継ぐ。完了・キャンセル済みのジョブは処理しない。失敗時も送信予定を残す。

S3 のカタログは実行ごとのキーに保存し、DynamoDB のカタログ参照の変更を条件付きで行う。途中結果が遅れて終了しても、確定結果のファイルや参照を上書きしない。棚観測は従来の `job_id:crop_id:book_id:shelf_id` による重複抑止を使用する。

## 運用上の範囲

- 対象はライブスキャンと単発スキャンの **最終 lookup**。単発スキャンも同じ受信処理を使うため、OCR 完了・OCR 対象なしの最終配送を統一する。フレーム受付から YOLO、YOLO から OCR、途中 lookup の送信をすべて outbox 化したものではない。
- 再照合は全件 Scan のため、ジョブ件数に応じて読み取り費用・実行時間が増える。継続して時間切れになる規模なら、pending 状態の GSI または再開位置の永続化を追加する。現時点の実データ量・5 分以内の回復時間は未計測。
- 一意のカタログキーを使うため、途中結果・失敗した確定試行の S3 オブジェクトは増える。現在参照中のカタログを消さない回収方式は未実装。保持量が問題になった場合は参照確認付きの回収を追加する。
- 恒久的なデータ障害も pending として再送される。LookupFunction のエラー、LookupDLQ、dispatcher のエラー、長時間 pending のジョブを調べる。自動修復できない入力は原因修正か明示的な運用判断が必要。
- ジョブが実行中にキャンセルされた場合の、すでに書き込んだ棚観測の取り消しはこの変更に含まない。カタログの確定は条件付きで防ぐ。通常 API は collecting 状態のみキャンセル可能。

## デプロイ時と既存ジョブ

この変更をローカルで検証しても本番には反映されない。デプロイ前には新規スキャンを止め、既存ワーカーの処理が落ち着いてから API・ワーカー・dispatcher・Streams をまとめて更新する。旧ワーカーが同時稼働すると、新しい条件付き確定を経由せず書き込む可能性がある。

既存の `final_lookup_queued=true` のみで止まったジョブは、新しい version を持たないので自動再送の対象ではない。個別に status、scan_closed、フレーム/OCR 完了数、元画像・crop の存在を確認し、再実行可能なものだけ `status=lookup_pending` の条件付き更新で version 1 を設定する。処理済み・キャンセル済みを一括して再実行しない。画像には保存期限があるため、古いジョブの復旧を保証しない。

本番では、通常配送、送信失敗後の回復、重複配送、取り消し済みジョブ、5 分の再照合を確認する。この作業では本番デプロイ・データ移行・試験登録は実施しない。

## ローカル検証（2026-09-21）

- `uv run --no-project --with pytest --with boto3 --with 'moto[dynamodb,sqs,s3]' --with pillow --with numpy --with opencv-python-headless pytest tests/test_aws*.py -q`: 41 件成功。
- `aws/functions/go_api` で `go test -race ./...`: 成功。
- `sam validate --template-file aws/template.yaml --lint`: 成功。
- `sam build LookupDispatcherFunction --template-file aws/template.yaml`（出力先は一時ディレクトリ）: 成功。Python Lambda の標準 boto3 を使用する。
- `git diff --check`: 成功。

Moto で DynamoDB の条件式も評価し、送信成功後の応答喪失、Streams イベントなしの再送、S3 書き込み障害、所有権失効・競合、カウンタ更新後の停止、古い Scan スナップショット、遅延した途中結果、完了/キャンセル済みジョブを確認した。実 AWS のイベント配線・IAM、変換後 CloudFormation、YOLO/lookup コンテナイメージ全体の再ビルド、実画像での受け入れは未検証。
