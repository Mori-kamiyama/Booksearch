# 運用安定化

## 方針と安全条件

認識精度の追加調整より、停止処理の回収・画像保全・通知を先に仕上げる。

- 毎時35分UTCにmaintenanceを起動。更新から24時間以上経過した非終端jobだけを期限切れfailedにする。元レコード、task、cropは残す。
- jobと子task/cropのlease、更新日時、状態、進捗、claimを確認。更新競合はDynamoDB transactionで失敗させ、進行中の処理を保護する。子要素が100件以上の場合は原子的に検証できないためスキップして監視へ出す。
- 不正な日時のjobは自動失効対象にしない。大規模化で全件scanが120秒に収まらなくなった場合は処理失敗として検知する。現状の件数向けの実装であり、その段階で期限索引・分割実行へ移行する。
- 画像を7日・30日で一律削除していたS3 lifecycleは撤去。参照中の画像も削除する危険があった。バージョニングを有効化し、不完全multipartの7日後中止だけを残す。過去に削除済みの画像は復元されない。
- `aws/scripts/cleanup_orphan_images.py` はdry-runが既定。90日より古いuploads/crops/live配下のUUID付き画像だけを候補とし、6テーブルの参照・所有jobが残るものは除外する。適用直前に参照を再確認し、ETag条件付きのdelete markerのみを作成する。画像本体のversionは削除しない。
- cleanupは手動実行とする。保存画像の完全削除・非現行versionの期限削除は行わないため、この変更だけで保存容量の継続削減を保証しない。保全が先で、容量回収は参照と保持期間を判断してから行う。

## 監視

既存6件のキュー滞留・DLQ alarmに、以下の5件を追加する。障害・復旧ともSNSへ接続する。メールアドレスはCloudFormationのNoEcho parameterで設定し、リポジトリへ記録しない。

|対象|条件|
|---|---|
|home publisherエラー|5分内に1件以上|
|maintenanceエラー|5分内に1件以上|
|おすすめ更新停止|配信home.htmlのfeatured-weekがUTC ISO週と不一致、2期間連続。月曜06:00UTCまでは更新猶予|
|監視自体の停止|毎時のMaintenanceHealthyが3期間欠落|
|停止jobの回収保留|lease・最近の子更新・子要素数・競合による保留が2期間継続|

期間が1時間のalarmは即時検知ではない。おすすめ監視はpublisherとは独立して配信S3 metadataを確認する。publisherが実行されなくても更新期限後の古い週を検知する。CloudFrontの配信内容や書籍推薦の品質検査は別の責務。

## 操作

```sh
uv run --no-project --with boto3 python aws/scripts/cleanup_orphan_images.py --bucket booksearch-277707097118-ap-northeast-1
# 候補を確認して適用するときだけ末尾に --apply を指定する。
```

適用結果のkey・delete_marker_versionを保存する。復元はそのdelete markerのversionだけをS3から削除する。画像本体のversionを削除しない。

メール購読は受信者がAWSの確認リンクを開いて初めて有効になる。設定済みと配信可能を区別する。

## 2026-09-27 検証・反映

- maintenance/cleanup/既存配送関連のPythonテスト44件成功。競合更新、active lease、100子要素、週の境界、欠落HTML、バージョン無効時の削除拒否、delete markerからの復元を検証。SAM lint成功。
- CloudFormation changeset: `operations-stability-20260927`。既存のAPI/worker/frontendコードは変更せず、稼働中のパッケージを保持してインフラとmaintenanceだけ更新。
- 本番maintenance初回: `expiration={fresh:95,expired:2}, featured_stale=false`。以前から残っていた停止2件を期限切れへ移行。
- 画像cleanup dry-runは候補0件。画像削除は実行していない。
- SNS email購読はPendingConfirmation。受信者の確認と通知到達検証は未完了。
- 再実行は `expiration={fresh:97}, featured_stale=false`。期限切れ処理の重複更新なし。S3 versioning Enabled、一律失効ルールなし、11 alarmの障害/復旧通知接続、毎時schedule ENABLEDを確認。
- CloudFormation最終状態はUPDATE_COMPLETE。実metricはFeaturedStale=0、MaintenanceHealthy=1、StalledJobsSkipped=0。新設alarmは初期データ不足を異常として扱うため、初回metricの評価反映まで一時的にALARMとなる。
