# Booksearch regression checks

## ローカル UI

```bash
cd tests
npm ci
npx playwright install chromium
npm test
```

`npm test` はフロントをビルドしてローカル preview を起動し、API を模擬した Chromium 回帰テストを実行する。本番 API への登録は行わない。画面表示付きは `npm run test:headed`。既に起動したフロントを使う場合は `PRIORITY_FRONTEND_URL` を明示する。

## 実環境の読み取りスモークテスト

```bash
npx playwright install chromium webkit
FRONTEND_URL=https://example.com npm run test:live
```

対象 URL の明示が必須。デスクトップ Chromium / モバイル Safari の閲覧のみを確認し、実画像 OCR・試験登録の検証にはならない。

`featured-persistence.spec.ts` は週替わり・通信失敗時のおすすめ保持、解析失敗画像の警告、保存済みおすすめの再訪表示速度を確認する。速度は模擬API・ローカルpreviewでの測定であり、本番初回表示の評価ではない。

## AWS スキャン配送・最終 lookup の障害回帰

リポジトリのルートから実行する。AWS の認証情報は不要。SDK の通信はテスト内で模擬する。

```bash
uv run --no-project --with pytest --with boto3 --with 'moto[dynamodb,sqs,s3]' --with pillow --with numpy --with opencv-python-headless \
  pytest tests/test_aws*.py -q
```

`test_aws_lookup_outbox_integration.py` は Moto の DynamoDB / SQS / S3 を通し、送信予定の保存、Streams イベントを失った場合の再送、二重配送、S3 障害後の再実行、遅延した途中結果による上書き防止を検証する。実 AWS の権限・イベント配線・コンテナ配布は別途受け入れ確認が必要。

`test_aws_scan_delivery.py` と `test_aws_yolo_durable.py` は受付taskの再配送、解析済みmanifestからの再開、OCR結果と件数の原子的な保存、旧試行の除外、キャンセルを確認する。
