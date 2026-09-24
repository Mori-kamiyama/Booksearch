# 背表紙OCR アクティブ校正システム

人手SFT済みPaddleOCR-VL-1.6が未見の背表紙を先に推論し、人が採用または修正するためのローカルWebアプリ。採用結果は次回SFT用、モデル出力と異なる修正結果はDPO用の `chosen / rejected` ペアとして同時に蓄積する。

## データ

`outputs/ocr_active_review/v1/input.jsonl` は元データのtrain splitから、既存の人手校正538件を除外し、1書籍1cropでランダムに選んだ500件。500件はすべて異なる `library_db_id` で、validation/testは含まない。

人手SFT済みcheckpoint-146で500件を事前推論済み。推論時間は481.7秒（1.04画像/秒）で、全500画像に空でない出力がある。結果は `outputs/ocr_active_review/v1/predictions.jsonl` に保存する。

校正中の状態は `outputs/ocr_active_review/v1/annotations.json` に操作のたびに原子的に保存する。再起動しても続きから再開できる。

## 起動

```bash
uv run python scripts/active_review_web.py
```

ブラウザで <http://127.0.0.1:4181/> を開く。

## 操作

- `A`: モデル出力をそのまま正解として保存
- `Cmd/Ctrl + Enter`: 入力欄の修正を保存
- `S`: 除外
- `←` / `→`: 前後移動

`SFTを書き出す` はフラグなしの全採用データを出力する。`DPOペアを書き出す` は人手正解とモデル出力が異なる採用データだけを出力し、人手正解をassistant応答、元のモデル出力を `rejected_response` とする。`2冊以上`、`読めない`、`crop不良` のフラグ付きデータは両方の学習用exportから除外する。
