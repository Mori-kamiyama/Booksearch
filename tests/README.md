# Booksearch E2E Tests (Playwright)

CloudFront 配信のフロントエンドと AWS API の E2E テスト。デスクトップ Chrome とモバイル Safari の 2 プロジェクトで実行する。

## セットアップ

```bash
cd tests
npm install
npx playwright install chromium webkit
```

## 実行

```bash
# 全テスト
npm test

# ヘッド付き（ブラウザを表示）
npm run test:headed

# モバイルのみ
npx playwright test --project=mobile-safari

# レポート表示
npm run report
```

## 環境変数

| 変数 | デフォルト | 用途 |
|------|-----------|------|
| `FRONTEND_URL` | `https://d2uel8nex1m4w7.cloudfront.net` | テスト対象フロント URL |
| `API_BASE` | `https://rx7ylpbzg6.execute-api.ap-northeast-1.amazonaws.com` | API エンドポイント |

ローカル UI に対して実行する場合:

```bash
FRONTEND_URL=http://localhost:5173 API_BASE=http://localhost:8080 npm test
```

## カバレッジ

- UI: ホーム表示、検索（ヒット/ノーヒット）、棚候補、スキャン UI、SPA ルーティング
- API: `/api/health`, `/api/books/search`, `/api/shelf-candidates`, `POST /api/scan` + ジョブ作成
