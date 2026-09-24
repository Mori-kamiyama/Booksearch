# Booksearch

本棚画像から棚位置・OCR結果・図書DB照合をつなぐプロジェクトです。

## ディレクトリ構成

- `app/`: ユーザーに提供するアプリケーションとデプロイ設定
  - `app/frontend/`: React/Vite UI
  - `app/backend/`: ローカル Go API
  - `app/aws/`: SAM ベースの Lambda/SQS/DynamoDB/S3 構成
  - `app/tests/`: アプリケーションE2Eテスト
- `research/`: OCR・棚検知・データ処理の実験と再現用入力
  - `research/scripts/`: カタログ生成、OCR、棚検知、DB構築スクリプト
  - `research/benchmark/`: 評価用コードと正解データ
  - `research/data/`: 生画像などの研究用データ
- `assets/`: アプリと研究で共有する追跡対象の小さな静的データ
- `archive/`: 現役の実行経路から外した復旧資料・デモ・発表資料
- `docs/`: 現在有効な設計、進捗、レビュー記録

VercelのAprilTag配置確認アプリと検出APIは、兄弟プロジェクト
`/Users/yuta/date/classroom/booksearch-vercel/` に分離しています。

生成物、依存ディレクトリ、ローカル環境ファイルはGitへ追加しません。詳細は `.gitignore` を参照してください。

## 主なコマンド

```sh
# ローカル Go API
cd app/backend && go test ./...

# フロントエンド
cd app/frontend && npm ci && npm test && npm run build

# AWS Go API
cd app/aws/functions/go_api && go test ./...
```
