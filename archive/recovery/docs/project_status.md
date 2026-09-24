# Booksearch 現在の実装状況

このドキュメントは、アノテーション、YOLO 学習、OCR、書籍 DB 照合、デモ UI までの現在地をまとめる。

## 結論

Booksearch は、実験段階としては次の MVP が一通り動く状態まで到達している。

1. 本棚・箱画像を入力する。
2. YOLO で本が入っている箱や棚区画を検出する。
3. 検出領域を crop する。
4. crop の品質を判定し、読めなさそうな画像は OCR 前に除外する。
5. Gemini OCR で背表紙から本のタイトルを抽出する。
6. ローカル図書 DB と照合する。
7. 必要に応じて Google Books 由来の書影を表示する。
8. Web デモ UI から画像アップロードして結果を確認できる。

次にやるべきことは、追加のアノテーションや再学習ではなく、発表・提出に向けた「デモの安定化」と「成功例の固定」である。

## アノテーション状況

本命のデータセットは `dataset/picture_box_detection`。

`dataset/box_detection` は 5 枚だけの小規模な古いデータセットなので、現在の主データセットとしては扱わない。

```text
dataset: dataset/picture_box_detection
train images: 204
val images:    51
total images: 255

reviewed: 255 / 255
remaining: 0
labeled images: 180
total boxes: 705
```

確認コマンド:

```bash
uv run python scripts/annotation_status.py --dataset-dir dataset/picture_box_detection
```

出力:

```text
train images=204 reviewed=204 remaining=  0 labeled=132 boxes=514
val   images= 51 reviewed= 51 remaining=  0 labeled= 48 boxes=191
total images=255 reviewed=255 remaining=  0 labeled=180 boxes=705
```

画像ファイルは `Picture/` の実体を `dataset/picture_box_detection/images/...` から symlink している。

## YOLO 学習状況

学習済みモデル:

```text
runs/detect/runs/picture_box_detection/yolo11n_quick/weights/best.pt
```

学習に使った設定:

```text
data: dataset/picture_box_detection/dataset.yaml
model: yolo11n.pt
epochs: 10
batch: 8
imgsz: 640
device: mps
```

最終 epoch の主な指標:

```text
precision(B): 0.82561
recall(B):    0.74360
mAP50(B):     0.81925
mAP50-95(B):  0.61925
```

箱・棚区画の検出器としては、MVP に進めるだけの性能が出ている。
今後さらに精度を上げる場合は、追加学習よりも先に失敗例を集めて、誤検出・未検出の傾向を見てからデータを増やすのがよい。

## 現在の処理パイプライン

実装済みの中心スクリプト:

```text
scripts/build_book_catalog.py
scripts/demo_web.py
scripts/build_library_db.py
scripts/search_book_catalog.py
scripts/quality_filter_demo.py
```

共通処理:

```text
src/detection.py
src/ocr.py
src/lookup.py
```

`build_book_catalog.py` の処理:

1. 入力画像を列挙する。
2. YOLO で box を検出する。
3. box ごとの crop と preview を保存する。
4. crop 品質を計算する。
5. 低品質 crop は OCR をスキップする。
6. Gemini OCR でタイトルを抽出する。
7. ローカル図書 DB と照合する。
8. `data/known_books.json` の補助リストでも補完する。
9. 結果を `catalog.json` に保存する。

実行例:

```bash
uv run python scripts/build_book_catalog.py \
  --source Picture \
  --output-dir outputs/book_catalog_picture_eval \
  --model runs/detect/runs/picture_box_detection/yolo11n_quick/weights/best.pt \
  --max-images 3
```

## 3 枚サンプルの end-to-end 評価

出力:

```text
outputs/book_catalog_picture_eval/catalog.json
```

結果:

```text
入力画像: 3 枚
検出 box: 25 個
OCR 抽出タイトル: 187 件
OCR エラー: 0 件
図書 DB 照合あり: 160 件
score >= 0.9: 117 件
score >= 0.8: 152 件
```

この結果から、検出、OCR、DB 照合はかなり実用に近いところまでつながっている。
失敗は主に以下に分かれる。

- OCR が一部だけ切れる
- OCR が一部誤読する
- 英語名や別名で読めているが、DB 上の日本語名と一致しない
- そもそも蔵書 DB に登録がない

## 図書 DB と照合

ローカル図書 DB:

```text
outputs/library/library.db
```

作成コマンド:

```bash
uv run python scripts/build_library_db.py
```

検索コマンド:

```bash
uv run python scripts/search_book_catalog.py "旅をする木" --catalog outputs/book_catalog/catalog.json
```

DB 照合では、完全一致だけでなく、OCR 文字列が DB タイトルを内包しているケースも拾う。

例:

```text
OCR: musik macht frei 音楽は自由にする
DB:  音楽は自由にする
```

照合結果は confidence で分ける。

```text
score >= 0.75        -> auto
0.65 <= score < 0.75 -> review
score < 0.65         -> 候補に残さない
```

`data/known_books.json` には、蔵書 DB にないが存在確認済みの本を補助的に入れている。
特に編入数学系のように OCR では短縮タイトルで出る本を拾うために使っている。

## crop 品質フィルタ

低品質な crop を OCR に投げると、コストとノイズが増える。
そのため `crop_quality` を計算し、明らかに読めない crop を OCR 前に除外する。

現在の主な閾値:

```text
min blur score: 150
min short edge: 550
edge + aspect 除外: ON
edge wide aspect: 1.45
edge tall aspect: 0.45
```

採用した方針:

```text
edge_touch 単体: 記録だけする
edge_touch + 横長/縦長すぎ: OCR から除外する
```

理由:

- 画像端に接していても読める crop はある
- 端に接していて横長すぎる crop は、見切れた切れ端である可能性が高い
- 端に接していて縦長すぎる crop も、箱の一部だけを拾っている可能性がある

品質フィルタ調整 UI:

```bash
python3 scripts/quality_filter_demo.py \
  --catalog outputs/book_catalog_val_quality_edge_aspect/catalog.json \
  --port 4178
```

ブラウザ:

```text
http://127.0.0.1:4178/
```

## デモ UI

デモ UI は `scripts/demo_web.py`。
画像アップロードから、箱検出、crop、Gemini OCR、ローカル図書 DB 検索、書影表示までを確認できる。

起動:

```bash
export GEMINI_API_KEY=...
uv run python scripts/build_library_db.py
uv run python scripts/demo_web.py
```

ブラウザ:

```text
http://127.0.0.1:4174/
```

デモで見せたい流れ:

1. 本棚画像をアップロードする。
2. YOLO が箱・棚区画を検出する。
3. crop ごとに OCR されたタイトルが並ぶ。
4. ローカル図書 DB の候補が出る。
5. ISBN がある候補では Google Books 書影も表示できる。

出力例は `outputs/demo_web/` に保存される。

```text
outputs/demo_web/<timestamp>/result.json
outputs/demo_web/<timestamp>/previews/
```

## 発表・提出前にやること

優先度が高い順:

1. デモで成功しやすい画像を 1-3 枚選ぶ。
2. その画像で `demo_web.py` を実行し、結果が安定して出ることを確認する。
3. 成功例の preview 画像と `result.json` を発表用に固定する。
4. `catalog.json` から「検出数」「OCR 件数」「DB 照合数」を発表資料に載せる。
5. 失敗例も 1 つだけ用意し、「今後の改善点」として説明できるようにする。

現時点では、再アノテーションや再学習よりも、デモ体験と説明資料の完成度を上げる方が効果が大きい。

## 次の改善候補

- `demo_web.py` の UI を発表向けに整える。
- 成功例を固定して、毎回同じ画像で安定して説明できるようにする。
- `review` 候補だけを人間が確認できる画面を作る。
- full dataset に対して catalog を作り、検索対象を増やす。
- 棚 ID や AprilTag を使って、どの棚・どの区画にあるかまで表示する。
- AWS 移行時は、現在の Python パイプラインを Lambda/SQS へ段階的に分ける。
