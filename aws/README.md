# Booksearch AWS Backend

ホンノキ バックエンドの AWS (SAM) 移植版。プランの「Go API + S3 + DynamoDB + SQS + Python Worker」分割を、Lambda 群として実装した。

## アーキテクチャ

```text
Client → API
  → JobsTableの受付とScanTasksTable.pendingを同時保存
  → Streams / 定期再照合 → dispatcher → YOLO queue
  → YOLO: cropとmanifestを保存、task.doneとジョブ件数を同時確定
  → dispatcher → OCR queue
  → OCR: crop結果と完了件数を同時保存
  → JobsTableの最終lookup送信予定
  → Streams / 定期再照合 → dispatcher → lookup queue
  → lookup: 採用済みcropを照合、catalog保存、条件付き完了
```

配送・再送・重複実行の契約は [scan_delivery_and_featured_cache.md](../docs/scan_delivery_and_featured_cache.md)、最終確定は [final_lookup_outbox.md](../docs/final_lookup_outbox.md)、滞留の読み取り専用診断は [scan_operations.md](../docs/scan_operations.md) を参照。

APIは互換ワーカーの更新後に更新する依存関係を持つ。旧taskなしジョブは自動移行しない。週次おすすめはS3保存と週次スケジュール、関連本は同梱SQLiteの事前計算結果を使う。

## 前提

- AWS CLI v2、`aws configure` 済み、ap-northeast-1
- AWS SAM CLI 1.16+
- Docker (Container Lambda の build と push に必要)
- Go 1.22+
- `aws/functions/go_api/library.db` または `outputs/library/library.db` がローカルに存在
- YOLO 学習済みモデル `aws/functions/yolo_worker/assets/yolo_model.pt`
- Gemini API key

## デプロイ手順

```bash
cd aws

# 1. Container イメージに同梱するアセットを集める
./scripts/prepare_assets.sh

# 2. ECR リポジトリ作成 (sam が image_repositories を要求する)
aws ecr create-repository --repository-name booksearch/yolo --region ap-northeast-1 || true
aws ecr create-repository --repository-name booksearch/lookup --region ap-northeast-1 || true

# 3. ビルド (Go バイナリ + 2 つの Container イメージ)
sam build

# 4. 初回デプロイ (--guided で対話設定が記録される)
sam deploy --guided \
  --parameter-overrides "GeminiApiKey=$GEMINI_API_KEY"

# 以降の更新
sam deploy --parameter-overrides "GeminiApiKey=$GEMINI_API_KEY"
```

初回 `sam deploy --guided` で次を聞かれる:
- Stack Name: `booksearch`
- Region: `ap-northeast-1`
- `Save arguments to samconfig.toml`: yes
- 2 つのコンテナ Lambda の image repo: `277707097118.dkr.ecr.ap-northeast-1.amazonaws.com/booksearch/yolo` 等

## アセットの S3 配置 (任意)

`/api/shelves` で AprilTag mapping を返すには S3 にも置く必要がある:

```bash
./scripts/upload_apriltag_to_s3.sh
```

## フロントエンドのデプロイ

S3 + CloudFront のスタックは `frontend-stack.yaml` で別管理。ホームのroot objectはpublisherが作る `home.html`、検索・詳細等のSPA fallbackは `index.html`。初回切り替えは下記publisherの生成成功後に実施する:

```bash
cd aws
aws cloudformation deploy \
  --template-file frontend-stack.yaml \
  --stack-name booksearch-frontend \
  --region ap-northeast-1 \
  --capabilities CAPABILITY_IAM
```

ホームは5冊の実表紙をHTMLに埋め込んで事前生成する。フロント変更時はpublisherも同じビルド成果物で更新する。リポジトリのルートで:

```bash
npm --prefix frontend ci
node scripts/build-home-publisher.mjs
# 以下のassetsをアップロードしてからHomePublisherFunctionを更新する。
BUCKET=$(aws cloudformation describe-stacks --stack-name booksearch-frontend \
  --query "Stacks[0].Outputs[?OutputKey=='FrontendBucketName'].OutputValue" --output text)
DIST=$(aws cloudformation describe-stacks --stack-name booksearch-frontend \
  --query "Stacks[0].Outputs[?OutputKey=='FrontendDistributionId'].OutputValue" --output text)
aws s3 sync frontend/dist/ s3://$BUCKET/ --exclude index.html --exclude home.html --exclude ".DS_Store" --cache-control "public, max-age=300"
aws s3 cp frontend/dist/index.html s3://$BUCKET/index.html --content-type text/html --cache-control no-cache
# ここで aws/template.yaml をSAM build/deployし、HomePublisherFunctionも更新する。
aws lambda invoke --function-name booksearch-home-publisher \
  --cli-binary-format raw-in-base64-out --payload '{"force":true}' /tmp/booksearch-home-publish.json
# FunctionErrorがなく、payloadがstatus=publishedであることを確認してから無効化する。
aws cloudfront create-invalidation --distribution-id $DIST --paths "/*"
```

現在の配信 URL: <https://d2uel8nex1m4w7.cloudfront.net>

API base URL は `frontend/.env.production` で固定。差し替える場合は `VITE_API_BASE_URL` を書き換えてから `npm run build`。

`home.html` と旧hash付きassetは削除しない。旧HTMLを開いているブラウザもそのassetを参照するため、`sync --delete` は使わない。publisherは毎時05分に週とclient templateのhashを確認し、変更時のみ更新する。画像取得・実decode・5冊の準備が失敗した場合は成功済みHTMLを上書きしない。配信待ち時間は最大5分のキャッシュTTLを含む。詳細と検証は `docs/featured_initial_html.md` を参照。

## E2E テスト

```bash
./scripts/e2e_test.sh                        # Picture/ の 1 枚目
./scripts/e2e_test.sh path/to/your_image.jpg # 任意の画像
```

POST → 5秒ごとにポーリング → status が `done` になったら catalog の book 件数を表示する。

### Playwright E2E（ブラウザ + API）

```bash
cd ../tests
npm install && npx playwright install
npm test
```

詳細は `tests/README.md`。

## デバッグ

```bash
# 各 Lambda のログ
sam logs --stack-name booksearch -n ApiFunction --tail
sam logs --stack-name booksearch -n YoloFunction --tail
sam logs --stack-name booksearch -n OcrFunction --tail
sam logs --stack-name booksearch -n LookupFunction --tail

# DynamoDB の状態
aws dynamodb get-item --table-name booksearch-jobs \
  --key '{"job_id":{"S":"<UUID>"}}'

aws dynamodb query --table-name booksearch-crops \
  --key-condition-expression "job_id = :j" \
  --expression-attribute-values '{":j":{"S":"<UUID>"}}'

# 失敗した SQS は DLQ に
aws sqs receive-message --queue-url $(aws cloudformation describe-stacks \
  --stack-name booksearch --query "Stacks[0].Outputs[?OutputKey=='YoloQueueUrl'].OutputValue" \
  --output text | sed 's/-queue$/-dlq/')
```

## 解体

```bash
# S3 バケットを空にしてから sam delete
BUCKET=$(aws cloudformation describe-stacks --stack-name booksearch \
  --query "Stacks[0].Outputs[?OutputKey=='BucketName'].OutputValue" --output text)
aws s3 rm "s3://$BUCKET" --recursive
sam delete --stack-name booksearch --no-prompts

# ECR
aws ecr delete-repository --repository-name booksearch/yolo --force
aws ecr delete-repository --repository-name booksearch/lookup --force
```

## ファイル構成

```
aws/
├── template.yaml              # SAM テンプレ
├── samconfig.toml             # stack 名・region
├── README.md
├── scripts/
│   ├── prepare_assets.sh      # 同梱アセットを集める
│   ├── upload_apriltag_to_s3.sh
│   └── e2e_test.sh
└── functions/
    ├── go_api/                # provided.al2023 zip Lambda (Go)
    │   ├── main.go            # ルーティング、handler 群
    │   ├── books.go           # SQLite 検索
    │   ├── go.mod
    │   └── Makefile           # SAM BuildMethod: makefile
    ├── yolo_worker/           # Container Lambda
    │   ├── Dockerfile
    │   ├── handler.py         # YOLO + AprilTag + crop + fan-out
    │   ├── requirements.txt
    │   └── assets/            # prepare_assets.sh が生成
    │       ├── yolo_model.pt
    │       ├── apriltag_library_map.json
    │       └── known_books.json
    ├── ocr_worker/            # Python zip Lambda
    │   ├── handler.py         # Gemini OCR + ATOMIC INCR
    │   └── requirements.txt
    └── lookup_worker/         # Container Lambda
        ├── Dockerfile
        ├── handler.py         # SQLite 照合 + catalog 生成
        ├── requirements.txt
        └── assets/            # prepare_assets.sh が生成
            ├── library.db
            └── known_books.json
```

## API

### `POST /api/scan`
```json
{ "filename": "shelf.jpg", "content_base64": "..." }
```
→ `202 { "job_id": "..." }`

### `GET /api/jobs/{job_id}`
→ `200 { "status": "pending|ocr_pending|lookup_pending|done|failed", "catalog": { ... } }`

`status==done` のときだけ `catalog` フィールドが返る（S3 から動的に取得）。

### `GET /api/books/search?q=...&limit=20`
ローカル SQLite 検索（Lambda zip に同梱）。

### `GET /api/books/{id}`
個別取得。

### `GET /api/shelves`
S3 の AprilTag mapping を返す。
