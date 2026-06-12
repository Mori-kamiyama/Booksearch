# Agent Notes

このリポジトリは、本棚や箱の画像から YOLO で領域を検出し、crop に OCR をかけて書名を抽出し、ローカル図書 DB や Google Books から ISBN/書誌情報を検索する実験用プロジェクトです。

## 基本方針

- Python コマンドは原則 `uv run ...` で実行する。
- 依存追加も `uv add ...` を使い、手で `.venv` や lockfile をいじらない。
- 既存の実験スクリプトは `scripts/`、再利用する処理は `src/` 直下のモジュールに置く。
- 現在の `src/` はパッケージディレクトリではなく、直下モジュール構成。
  - `src/detection.py`: YOLO 検出、bbox 補正、crop/preview 保存
  - `src/ocr.py`: Gemini OCR、書名 JSON のパース
  - `src/lookup.py`: 書名検索、ISBN/書誌候補検索
- `scripts/build_book_catalog.py` は上記 `src` モジュールを組み合わせる CLI 入口として扱う。

## よく使うコマンド

```bash
uv run python scripts/init_yolo_dataset.py --dataset-dir dataset/box_detection --source-dir data
uv run python scripts/review_yolo_boxes.py --dataset-dir dataset/box_detection --split train --only-unreviewed
uv run python scripts/annotation_status.py --dataset-dir dataset/box_detection
uv run yolo detect train data=dataset/box_detection/dataset.yaml model=yolo11n.pt epochs=100 imgsz=640
uv run python scripts/build_book_catalog.py
uv run python scripts/build_book_catalog.py --source data --output-dir outputs/book_catalog --skip-ocr
uv run python scripts/search_book_catalog.py "検索語" --catalog outputs/book_catalog/catalog.json
uv run python scripts/demo_web.py
```

## 環境変数

- Gemini OCR を使う処理には `GEMINI_API_KEY` が必要。
- OpenRouter 系の実験スクリプトには `OPENROUTER_API_KEY` が必要。

## データと出力

- 入力画像は主に `data/` または `Picture/`。
- YOLO データセットは `dataset/` 配下。
- 実験結果、crop、preview、catalog は `outputs/` 配下。
- 学習済み YOLO 重みは `runs/` 配下を参照していることが多い。

## 作業時の注意

- 大きな画像・出力ファイル・学習結果は不用意に削除しない。
- `outputs/` や `runs/` は既存実験の結果を含むので、必要なものだけ読む。
- OCR/API 呼び出しは外部サービスを使うため、キーの有無とコストに注意する。
- 既存スクリプトから共通化できる処理は、`src/` 直下へ小さく切り出す。
- ファイル検索は `rg` / `rg --files` を優先する。
