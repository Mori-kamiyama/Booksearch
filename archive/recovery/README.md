# Booksearch

本棚画像から箱を検出し、箱ごとの crop を OCR して蔵書リストを作るためのリポジトリです。
現在の本線は `Picture/` の画像から作った `dataset/picture_box_detection` です。

## 現在の状態

- アノテーション: 完了
  - `dataset/picture_box_detection`
  - train 204 images / val 51 images
  - reviewed 255 / 255
  - labeled 180 images
  - boxes 705
- YOLO 学習: 完了
  - run: `runs/detect/runs/picture_box_detection/yolo11n_quick`
  - model: `runs/detect/runs/picture_box_detection/yolo11n_quick/weights/best.pt`
  - data: `dataset/picture_box_detection/dataset.yaml`
  - epochs: 10
- ローカル図書 DB: 作成済み
  - `outputs/library/library.db`
- AWS バックエンド: SAM でデプロイ済み
  - 詳細は `aws/README.md`
  - API: `https://rx7ylpbzg6.execute-api.ap-northeast-1.amazonaws.com`
- フロントエンド: これから AWS API に合わせて作る/接続する段階

## データセット

```text
dataset/picture_box_detection/
  dataset.yaml
  images/train/
  images/val/
  labels/train/
  labels/val/
  meta/reviewed/train/
  meta/reviewed/val/
  predictions/
  previews/
```

古い小規模データセットとして `dataset/box_detection` も残っていますが、学習・AWS 同梱で使う本線は `dataset/picture_box_detection` です。

## アノテーション

進捗確認:

```bash
uv run python scripts/annotation_status.py --dataset-dir dataset/picture_box_detection
```

現在の表示:

```text
train images=204 reviewed=204 remaining=  0 labeled=132 boxes=514
val   images= 51 reviewed= 51 remaining=  0 labeled= 48 boxes=191
total images=255 reviewed=255 remaining=  0 labeled=180 boxes=705
```

追加画像を入れた場合:

```bash
uv run python scripts/init_yolo_dataset.py --dataset-dir dataset/picture_box_detection --source-dir Picture
uv run python scripts/review_yolo_boxes.py --dataset-dir dataset/picture_box_detection --split train --only-unreviewed
uv run python scripts/review_yolo_boxes.py --dataset-dir dataset/picture_box_detection --split val --only-unreviewed
```

操作:

- 左ドラッグ: box を追加
- 右クリック: その box を削除
- `+` / `-`: 表示を拡大 / 縮小
- `n`: 保存して次へ
- `p`: 保存して前へ
- `s`: 保存
- `c`: 全削除
- `d`: 最後の box を削除
- `r`: reviewed フラグ切り替え
- `q`: 保存して終了

## YOLO 学習

既存の学習済みモデル:

```text
runs/detect/runs/picture_box_detection/yolo11n_quick/weights/best.pt
```

同じ設定で再学習する場合:

```bash
uv run yolo detect train \
  data=dataset/picture_box_detection/dataset.yaml \
  model=yolo11n.pt \
  epochs=10 \
  imgsz=640 \
  batch=8 \
  device=mps \
  project=runs/picture_box_detection \
  name=yolo11n_quick \
  exist_ok=True
```

学習結果:

```text
runs/detect/runs/picture_box_detection/yolo11n_quick/results.csv
runs/detect/runs/picture_box_detection/yolo11n_quick/args.yaml
runs/detect/runs/picture_box_detection/yolo11n_quick/weights/best.pt
runs/detect/runs/picture_box_detection/yolo11n_quick/weights/last.pt
```

## 蔵書リスト生成

学習済み YOLO モデルで箱を検出し、箱ごとの切り抜き画像を Gemini OCR に渡して `catalog.json` を作ります。
OCR では背表紙から読めるタイトルだけを抽出します。

```bash
export GEMINI_API_KEY=...
uv run python scripts/build_book_catalog.py
```

出力:

```text
outputs/book_catalog/
  catalog.json
  crops/
  previews/
```

OCR を使わず、箱検出と crop だけ確認する場合:

```bash
uv run python scripts/build_book_catalog.py --source Picture --output-dir outputs/book_catalog --skip-ocr
```

OCR だけ行い、書誌情報検索を止める場合:

```bash
uv run python scripts/build_book_catalog.py --no-lookup
```

## 図書 DB

ローカル照合用の SQLite DB を作る:

```bash
uv run python scripts/build_library_db.py
```

作成先:

```text
outputs/library/library.db
```

Google Books API の書影キャッシュを少しずつ増やす場合:

```bash
export GOOGLE_BOOKS_API_KEY=...
uv run python scripts/fetch_google_book_covers.py --limit 900
```

1日の上限に余裕を残すため、デフォルトは 900 件です。429 などの連続エラーが出た場合は自動で停止します。

## 検索

生成済み catalog を検索する:

```bash
uv run python scripts/search_book_catalog.py "旅をする木" --catalog outputs/book_catalog/catalog.json
uv run python scripts/search_book_catalog.py "978" --catalog outputs/book_catalog/catalog.json --field isbn
uv run python scripts/search_book_catalog.py "add_tag:box_03" --catalog outputs/book_catalog/catalog.json --field box
```

## ローカルデモ UI

画像をアップロードすると、箱検出、crop、Gemini OCR、ローカル図書 DB 検索、Google Books 由来の書影表示まで実行します。

```bash
export GEMINI_API_KEY=...
uv run python scripts/build_library_db.py
uv run python scripts/demo_web.py
```

ブラウザで開く:

```text
http://127.0.0.1:4174/
```

## AWS バックエンド

SAM 版のバックエンドは `aws/` にあります。

```bash
cd aws
./scripts/prepare_assets.sh
sam build
sam deploy --parameter-overrides "GeminiApiKey=$GEMINI_API_KEY"
```

現在の API:

```text
https://rx7ylpbzg6.execute-api.ap-northeast-1.amazonaws.com
```

主な API:

- `POST /api/scan`
- `GET /api/jobs/{job_id}`
- `GET /api/books/search?q=...&limit=20`
- `GET /api/books/{id}`
- `GET /api/shelves`

フロントエンドは今後、この AWS API に合わせて実装します。AWS 版の `POST /api/scan` は multipart ではなく、次の JSON を受け取ります。

```json
{ "filename": "shelf.jpg", "content_base64": "..." }
```

## メモ

- クラスは `box` 1つに固定しています。
- ラベルは `dataset/picture_box_detection/labels/<split>/<画像名>.txt` に保存されます。
- 画像追加後は `scripts/init_yolo_dataset.py` をもう一度実行すると、未追加分の画像と空ラベルを作れます。
- 検出、OCR、DB照合、低品質 crop 除外の実験結果は `docs/pipeline_experiment_notes.md` にまとめています。
- フロントで良い静止画だけ撮影し、AWS/Lambda に分割する方針メモは `docs/aws_lambda_frontend_capture_plan.md` にあります。
