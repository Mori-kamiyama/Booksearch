# OCRツール比較メモ

棚検知（gemini-3.1-flash-lite で矩形クロップ）後の OCR 工程で試したいツールのまとめ。

## 検討候補

| モデル | サイズ | 日本語 | MPS対応 | インストール |
|--------|--------|--------|---------|------------|
| **Sarashina2.2-OCR** (`sbintuitions/sarashina2.2-ocr`) | 7B | ◎ 日本語特化 | ○ | 実装済み (`experiments/test_sarashina_ocr.py`) |
| **GLM-OCR** (`zai-org/GLM-OCR`) | 0.9B | ○ CJK明記 | ◎ ネイティブ | `pip install glmocr` |
| **OLMo OCR** (`allenai/olmOCR-7B`) | 7B | △ 英語中心 | △ | `pip install olmocr` |

## 所感

- **GLM-OCR** が最有力候補。0.9B と超軽量で Mac 上で速い。日本語・CJK サポートを明記。
- **Sarashina2.2-OCR** はすでにコードあり、すぐ動かせる。日本語特化なので精度期待大。
- **OLMo OCR** は英語ドキュメント前提なので日本語背表紙には不向き。優先度低。

## パイプライン案

1. gemini-3.1-flash-lite → 棚の矩形検出・クロップ
2. 各 OCR モデルに棚クロップ画像を投入
3. benchmark/evaluate.py で Recall 評価して比較

## 対象画像

`data/add_tag.jpg`（タグ付き棚、正解ラベルあり）をメインに使う予定。

## TODO

- [ ] GLM-OCR を `scripts/` に実装してテスト
- [ ] Sarashina OCR を棚クロップに対して実行
- [ ] 両モデルの Recall 比較
- [ ] OLMo OCR は必要になったら検討
