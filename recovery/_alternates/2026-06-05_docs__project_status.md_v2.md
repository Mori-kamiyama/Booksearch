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
