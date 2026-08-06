# Booksearch AWS Backend

ホンノキ バックエンドの AWS (SAM) 移植版。プランの「Go API + S3 + DynamoDB + SQS + Python Worker」分割を、4 つの Lambda として実装した。

## アーキテクチャ

```
[Client]
    │ POST /api/scan {filename, content_base64}
    ▼
[API Gateway HTTP API]
    │
    ▼
[ApiFunction] (Go, provided.al2023, arm64)
    ├─ S3 PUT uploads/{job_id}/upload.jpg
    ├─ DDB PutItem jobs (status=pending)
    └─ SQS yolo-queue へ
            │
            ▼
    [YoloFunction] (Container, x86_64, 3GB)
        ├─ S3 GET image
        ├─ YOLO 推論 (model.pt は image 同梱)
        ├─ AprilTag 検出 + shelf 割当
        ├─ crop S3 PUT crops/{job_id}/{crop_id}.jpg
        ├─ DDB PutItem crops × N
        ├─ DDB Update jobs.crop_total = N
        └─ SQS ocr-queue へ × N (readable のみ)
                │
                ▼
        [OcrFunction] (Python zip, arm64) × N 並列
            ├─ Secrets Manager から GEMINI_API_KEY
            ├─ S3 GET crop
            ├─ Gemini OCR
            ├─ DDB Update crops.titles
            ├─ DDB ADD jobs.ocr_done += 1 (ATOMIC)
            └─ 最後の 1 件のみ SQS lookup-queue へ
                    │
                    ▼
            [LookupFunction] (Container, arm64)
                ├─ DDB Query crops (全件)
                ├─ library.db (image 同梱) で照合
                ├─ known_books.json で補完
                ├─ catalog.json を S3 PUT catalogs/{job_id}/
                └─ DDB Update jobs.status=done
```

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
- 4 つの Lambda の image repo: `277707097118.dkr.ecr.ap-northeast-1.amazonaws.com/booksearch/yolo` 等

## アセットの S3 配置 (任意)

`/api/shelves` で AprilTag mapping を返すには S3 にも置く必要がある:

```bash
./scripts/upload_apriltag_to_s3.sh
```

## フロントエンドのデプロイ

S3 + CloudFront のスタックは `frontend-stack.yaml` で別管理。初回のみ:

```bash
cd aws
aws cloudformation deploy \
  --template-file frontend-stack.yaml \
  --stack-name booksearch-frontend \
  --region ap-northeast-1 \
  --capabilities CAPABILITY_IAM
```

ビルド + 同期 + キャッシュ無効化:

```bash
cd ../frontend
npm install
npm run build
BUCKET=$(aws cloudformation describe-stacks --stack-name booksearch-frontend \
  --query "Stacks[0].Outputs[?OutputKey=='FrontendBucketName'].OutputValue" --output text)
DIST=$(aws cloudformation describe-stacks --stack-name booksearch-frontend \
  --query "Stacks[0].Outputs[?OutputKey=='FrontendDistributionId'].OutputValue" --output text)
aws s3 sync dist/ s3://$BUCKET/ --delete --cache-control "public, max-age=300"
aws cloudfront create-invalidation --distribution-id $DIST --paths "/*"
```

現在の配信 URL: <https://d2uel8nex1m4w7.cloudfront.net>

API base URL は `frontend/.env.production` で固定。差し替える場合は `VITE_API_BASE_URL` を書き換えてから `npm run build`。

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
