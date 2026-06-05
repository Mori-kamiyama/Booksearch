# Booksearch

本棚画像から箱・本棚位置・OCR結果・図書DB照合をつなぐ実験プロジェクトです。

主な構成:
- `backend/`: ローカル Go API
- `frontend/`: React/Vite UI
- `aws/`: SAM ベースの Lambda/SQS/DynamoDB/S3 構成
- `scripts/`: カタログ生成、OCR、棚検知、DB構築用スクリプト
- `docs/`: 実験メモと進捗
