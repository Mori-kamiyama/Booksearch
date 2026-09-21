# スキャン受付の耐久性とおすすめの永続キャッシュ

## スキャン処理の境界

最終照合だけでなく、受付→画像解析→OCR→完了件数の各境界を保存済みの状態から再開できるようにする。

- API の受付は `ScanTasksTable(job_id, task_id)` の pending 作成と JobsTable の受付更新をトランザクションで保存する。ライブ画像は画像キーから同じ task_id を生成する。SQS 送信失敗を理由に受付件数を巻き戻さない。
- ScanTasksTable の Streams と既存の5分再照合が YOLO を配送する。キャンセル・完了済みジョブは配送しない。
- YOLO は660秒の所有権を取得する（Lambda timeout は600秒）。解析した crop と manifest は試行ごとのキーに保存する。manifest の参照を prepared として保存した後は、再配送でも同じ解析結果を使う。
- task の done と、ジョブの解析済み画像数・crop数・OCR対象数はトランザクションで確定する。途中で停止しても片方だけを確定させない。
- done の task の manifest に含まれ、採用された detection_token と一致する pending crop だけを OCR に配送する。Streams を取りこぼしても再照合する。
- OCR は180秒の所有権を持ち、crop の結果とジョブの OCR 完了件数を同じトランザクションで保存する。失敗した crop を処理対象から外す場合も同じ境界を使う。
- 最終照合も、採用済み task/token の crop だけを強い整合性で読み込む。古い試行が書き残した crop を検索結果に混ぜない。
- 一部のライブ画像を解析できなかった場合は `failed_frames` に記録し、結果画面で撮り直しが必要なことを伝える。

配送は重複し得る。保証するのは、同じ受付 task・crop による完了件数や確定結果の重複更新を防ぐことであり、障害時にも外部 OCR 呼び出しが必ず一回だけになることではない。

## おすすめの保存と更新

- AWS は UTC の ISO週ごとに最大20冊の manifest を既存 AssetsBucket の `featured/weeks/` に保存する。同じ週の manifest は条件付き作成とし、競合した場合は先に保存されたリストを使う。
- `featured/latest.json` は最後の成功を指す。ETag による条件付き更新と週比較で、遅れて終了した古い週の処理が最新値を戻さないようにする。
- 毎週月曜00:00 UTC の EventBridge イベントが事前生成を呼ぶ。初回アクセス時にも不足分を生成できる。空・未保存・前週への退避は事前生成の成功にしない。
- 初回の空結果は週全体の固定リストとして保存しない。更新障害では前回成功分を維持し、後続アクセスで再試行する。永続ストレージ操作には時間制限を設ける。
- ローカル backend は蔵書DBとは別の `<DBパス>.featured.json` に一時ファイル＋renameで保存する。
- ブラウザーは API 環境・表示件数ごとに localStorage に成功結果を保存する。再訪時は保存済みの一覧を先に表示し、5分の期限切れや週替わりでは裏で更新する。成功時だけ差し替え、通信失敗や不正なレスポンスで前回分を消さない。
- localStorage の利用拒否、破損データ、容量不足は画面エラーにせず、メモリキャッシュと通常取得へ戻す。

## 検証

実 AWS・外部 OCR へは接続せず、Moto の DynamoDB/S3/SQS と SDK の模擬HTTPサーバーで停止・再送・競合を再現する。既存の UI 回帰も合わせて実行する。

2026-09-21の結果:

- AWS系Python回帰: 55件成功。実行方法は [tests/README.md](../tests/README.md)。送信漏れ、結果と件数の同時保存、応答喪失後の再送、preparedからの再開、旧解析結果の除外を確認。
- `aws/functions/go_api` と `backend` の `go test -race ./...`: 成功。
- `frontend` の `npm test`: 31件成功。`tests` の `npm test`: UI回帰47件成功、フロントのproductionビルドも成功。
- `sam validate --lint --template-file aws/template.yaml`: 成功。`sam build` は `ApiFunction` と `LookupDispatcherFunction` を個別指定して成功。APIの既存MakefileをSAMのBuildMethodに接続した。
- APIのLinux/arm64ビルド、`git diff --check`: 成功。
- 独立レビューで受付・停止・取消の競合、重複配送、試行tokenによる除外、IAM配線を確認。ローカル検証で追加の重大な問題は見つからなかった。

YOLO/lookupコンテナ全体の再ビルドと実AWSでの動作確認は未実施。

再訪速度はローカル production preview、Chromium、保存済み実データ相当のfixture、20回の画面再読込で計測。全 UI 回帰と並列実行した測定のp95は109ms（単独実行では68ms）。初回の本番取得・Lambdaコールド起動・実モバイル回線を測った数字ではなく、本番p95 1秒以内の達成証明にはしない。

## デプロイ前と残る確認

この実装だけでは本番に反映されない。ScanTasksTable と Streams/IAM、dispatcher、YOLO/OCR/lookup、API、フロントを整合した状態で更新する。古いワーカーと新しい受付が混在しないよう、新規スキャンを止めて処理を落ち着かせてから更新する。

旧 task_id なしメッセージ用のワーカー経路は互換性のため残しているが、旧ジョブの停止窓を自動移行で修復するものではない。既存停止ジョブは入力の保存期限と処理状態を調べ、個別に復旧する。直接 `POST /api/scan` を別リクエストとして送り直す場合の、ジョブIDをまたぐ重複受付は今回の保証外。通常の init/start とライブフレーム受付は同じIDで再試行できる。

実機のカメラ・Safari・登録から検索への反映、本番IAMとイベント配送、おすすめの本番初回p95は A23 で確認する。全件再照合の負荷、manifest/crop/カタログの回収、恒久障害の監視は A25 として残す。
