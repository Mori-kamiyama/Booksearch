# Booksearch パイプライン実験メモ

本棚・箱画像から本を検索できる状態にするために行った、検出、OCR、照合、低品質 crop 除外の実験結果をまとめる。

## 現在のパイプライン

1. YOLO で箱領域を検出する。
2. 検出 box を crop する。
3. crop の品質を判定し、読めなさそうなものは OCR 前にスキップする。
4. Gemini OCR で背表紙から読める本のタイトルだけを抽出する。
5. ローカル図書 DB と照合する。
6. DB にないが存在確認済みの本は `data/known_books.json` の補助リストで補完する。
7. `catalog.json` に検出、OCR、照合、品質判定の結果を保存する。

## 学習済み YOLO モデル

使用している学習済みモデル:

```text
runs/detect/runs/picture_box_detection/yolo11n_quick/weights/best.pt
```

学習結果の最終 epoch 付近:

```text
precision(B): 0.826
recall(B):    0.744
mAP50(B):     0.819
mAP50-95(B):  0.619
```

箱検出器としては実用に進める水準と判断した。

## 3枚サンプルの end-to-end 評価

実行例:

```bash
uv run python scripts/build_book_catalog.py \
  --source Picture \
  --output-dir outputs/book_catalog_picture_eval \
  --model runs/detect/runs/picture_box_detection/yolo11n_quick/weights/best.pt \
  --max-images 3
```

結果:

```text
入力画像: 3枚
検出 box: 25個
OCR 抽出タイトル: 187件
OCR エラー: 0件
図書DB照合あり: 160件
score >= 0.9: 117件
score >= 0.8: 152件
```

この時点で、OCR 精度は体感として 9 割近く、主要な失敗は以下に分かれた。

- OCR が途中で切れる
- OCR が一部誤読する
- 英語名や別名で DB に当たらない
- 蔵書 DB に登録がない

例:

```text
SYNCHRONICITY
```

これは `シンクロニシティ` の英語名なので、OCR としては読めているが DB 照合では拾えなかった。

## DB lookup の改善

OCR 文字列に余計な文字が混ざっていても、DB タイトルを内包している場合は拾えるようにした。

例:

```text
OCR: musik macht frei 音楽は自由にする
DB:  音楽は自由にする
```

改善後:

```text
0.875 auto | 音楽は自由にする
```

照合結果には `match_confidence` を追加した。

```text
score >= 0.75       -> auto
0.65 <= score <0.75 -> review
score < 0.65        -> 候補に残さない
```

これにより、OCR 誤読の可能性がある候補を自動確定せず、人間確認用として残せる。

## known_books 補完

蔵書 DB にはないが存在確認済みの本を `data/known_books.json` に追加した。

追加した編入系:

```text
編入数学入門: 講義と演習
編入数学徹底研究: 頻出問題と過去問題の演習
編入数学過去問特訓: 入試問題による徹底演習
編入の微分積分 徹底研究: 基本事項の整理と問題演習
```

OCR では短縮タイトルとして出ることが多い。

```text
編入数学入門
編入数学徹底研究
編入数学過去問特訓
編入の微分積分徹底研究
```

補完後:

```text
編入数学入門           -> 1.000 auto known_books
編入数学徹底研究       -> 1.000 auto known_books
編入数学過去問特訓     -> 1.000 auto known_books
編入の微分積分徹底研究 -> 1.000 auto known_books
```

3枚サンプルでは、候補なしが次のように減った。

```text
候補なし: 27 -> 9
known_books で拾えた件数: 11
```

残った候補なしは、主に OCR が正しく読めていないものだったため許容する。

## 低品質 crop 除外

OCR に投げても読めない可能性が高い crop を事前に除外するため、`crop_quality` を追加した。

`catalog.json` には以下のような情報が入る。

```json
{
  "blur_score": 328.83,
  "short_edge": 922,
  "aspect_ratio": 0.972,
  "edge_touch": [],
  "readable": true,
  "reasons": []
}
```

現在のデフォルト値:

```text
min blur score = 150
min short edge = 550
画像端かつ横長/縦長すぎる box を除外 = ON
Edge wide aspect = 1.45
Edge tall aspect = 0.45
```

除外理由:

```text
blurry    blur_score が低い
too_small crop の短辺が短すぎる
edge_wide 画像端に接していて横長すぎる
edge_tall 画像端に接していて縦長すぎる
```

単に画像端に接しているだけでは除外しない。端に接していても読める box があるため、`edge_touch` 単体除外は強すぎる。

最終的には、以下の考え方にした。

```text
edge_touch 単体: 記録だけする
edge_touch + 横長/縦長すぎ: OCR から除外する
```

理由:

- 背表紙は縦方向に文字が並ぶことが多い
- 横長で縦が短い edge crop は、画面端の切れ端である可能性が高い
- 縦長すぎる edge crop も、箱の一部だけを拾っている可能性がある

## val 画像での品質閾値確認

val 12枚で検出だけ行い、品質条件を比較した。

```bash
uv run python scripts/build_book_catalog.py \
  --source dataset/picture_box_detection/images/val \
  --output-dir outputs/book_catalog_val_quality_edge_aspect \
  --model runs/detect/runs/picture_box_detection/yolo11n_quick/weights/best.pt \
  --max-images 12 \
  --skip-ocr
```

比較:

```text
edge_touch 全除外:
  excluded 23/35

edge + aspect 除外:
  excluded 15/35
```

`edge_touch` 全除外は読めそうな箱まで落としすぎる。
`edge + aspect` は、見切れた切れ端を狙って落とせるため採用した。

## 品質フィルタ調整 UI

除外パラメータを手元で調整するためのデモ UI を作成した。

```bash
python3 scripts/quality_filter_demo.py \
  --catalog outputs/book_catalog_val_quality_edge_aspect/catalog.json \
  --port 4178
```

ブラウザ:

```text
http://127.0.0.1:4178/
```

調整できる項目:

- `Min blur score`
- `Min short edge`
- `Min detector confidence`
- 画像端に接する box を除外
- 画像端かつ横長/縦長すぎる box を除外
- `Edge wide aspect`
- `Edge tall aspect`
- include/exclude の表示切り替え

各カードには以下が表示される。

- crop 画像
- box ID
- include / exclude
- blur
- short edge
- detector confidence
- aspect ratio
- edge touch
- 除外理由

## 現在の完成判断

現時点で以下がつながっている。

- YOLO による箱検出
- crop 生成
- crop 品質判定
- 低品質 crop の OCR スキップ
- Gemini OCR によるタイトル抽出
- ローカル図書 DB 照合
- 既知タイトル補完
- auto / review 候補の分離
- 品質閾値調整 UI

全体フローとしては一旦完成。
今後は、全画像を流した時の `skipped_low_quality` と `review` 候補だけを人間が確認する運用にすればよい。
