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

- [x] GLM-OCR を `scripts/` に実装してテスト → `scripts/glm_ocr.py`
- [x] Sarashina OCR をベンチマーク形式で実行 → `scripts/sarashina_bench.py`
- [ ] GLM-OCR / Sarashina を `add_tag_warped.jpg` に対して実行して Recall 比較
- [ ] OLMo OCR は必要になったら検討

## ベンチマーク実行コマンド

```bash
# GLM-OCR 実行（初回はモデルDL ~1.8GB）
uv run python scripts/glm_ocr.py outputs/add_tag/add_tag_warped.jpg --output-dir outputs/add_tag_glm

# Sarashina 実行（7B、MPS で数分）
uv run python scripts/sarashina_bench.py outputs/add_tag/add_tag_warped.jpg --output-dir outputs/add_tag_sarashina

# 比較表を表示（Gemini=1.0 を baseline として）
uv run python benchmark/evaluate.py \
  --compare \
  --baseline outputs/add_tag_gemini/summary.json \
  outputs/add_tag_glm/summary.json \
  outputs/add_tag_sarashina/summary.json \
  outputs/add_tag_segment_yomitoku/summary.json
```
