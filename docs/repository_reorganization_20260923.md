# リポジトリ再編（2026-09-23）

## 目的

アプリケーション、研究用コード、発表・復旧資料、生成物をルート直下に混在させず、役割とGit管理方針を明確にする。

## 実施内容

- `app/` に、フロントエンド、ローカルAPI、AWS構成、E2Eテストを集約した。
- `research/` に、OCR・棚検知・評価スクリプト、共有Python実装、入力データを集約した。
- `assets/data/` に、アプリと研究が共有するAprilTagマップと蔵書補助データを置いた。
- `archive/` に、復旧資料、デモ、発表資料を退避した。アーカイブは通常のビルド・テスト・デプロイの入力にしない。
- `.gitignore` をディレクトリ非依存にし、任意の階層の依存ディレクトリ、ビルド成果物、実行出力、ローカル環境ファイルを除外した。
- Vercel設定、AWS補助スクリプト、ローカルGo API、研究スクリプトの主要な参照先を新構成へ更新した。
- 2026-09-23追記: VercelのAprilTag配置確認UIと検出APIは `/Users/yuta/date/classroom/booksearch-vercel/` へ分離し、本リポジトリからVercel設定を撤去した。

## Git管理方針

- 管理する: アプリ実装、再現に必要な小さな静的データ、設定、テスト、設計・運用文書。
- 管理しない: `node_modules/`、`dist/`、`.venv/`、`.env*`、`outputs/`、`runs/`、データセットの生成物。
- 例外: AWSデプロイで同梱するSQLite DBとYOLOモデルは追跡対象として明示的に許可する。

## 検証

- `cd app/backend && go test ./...`
- `cd app/aws/functions/go_api && go test ./...`
- `cd app/frontend && npm test && npm run build`
- `uv run python -m compileall -q research/scripts research/src research/tag-camera-lab`
- `bash -n app/aws/scripts/prepare_assets.sh app/aws/scripts/upload_apriltag_to_s3.sh app/aws/scripts/e2e_test.sh`
- `git diff --check`

いずれも成功した。実AWSデプロイ、実カメラ、実OCR、既存の本番書込みE2Eは実行していない。
