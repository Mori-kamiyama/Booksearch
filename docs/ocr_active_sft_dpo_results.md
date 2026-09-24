# Active review v1: SFT + DPO結果

2026-09-24に、人手校正したactive review v1を用いてPaddleOCR-VL-1.6のLoRAを追加学習した。

## データ

- SFT: 採用370件のうち `multi_spine` 1件を除外した369件
- DPO train: 修正ペア129件
- DPO validation: 修正ペア14件
- 最終評価: 以前から固定している独立human test 78件

active reviewの画像はすべて元データのtrain split由来で、固定test 78件との混入はない。

## 学習

- Active SFT: 92 steps、約2 epoch、LR 1e-5、effective batch 8
- DPO: 32 steps、約2 epoch、LR 5e-6、beta 0.1、RPO alpha 0.5
- vision encoderとalignerは固定し、LLM側LoRAのみ更新

Remote checkpoints:

- SFT: `/checkpoints/paddleocr-vl-1.6-active-sft-1790240016/v0-20260924-085400/checkpoint-92`
- DPO: `/checkpoints/paddleocr-vl-1.6-active-dpo-1790240320/v0-20260924-085905/checkpoint-32`

## 固定test 78件の結果

| Model | Exact | Mean CER | Median CER | Title containment | Title similarity |
|---|---:|---:|---:|---:|---:|
| Human SFT-146 | 50.0% | 0.424 | 0.028 | 80.8% | 0.804 |
| Active SFT-92 | 53.8% | 0.324 | 0.000 | 82.1% | 0.856 |
| Active DPO-32 | 53.8% | 0.297 | 0.000 | 82.1% | 0.873 |

ローカル書名検索のline ensembleでは、Human SFT-146のrecord Recall@1/@5が82.1%/91.0%、Active SFTとActive DPOが83.3%/92.3%だった。SFTとDPOの検索結果は同率。

## 判断

既定モデルにはActive SFT-92を採用する。追加SFTは完全一致、CER、書名検索のすべてを改善した。

DPOは平均CERをさらに下げたが、固定testで変化した出力は7/78件だけだった。5件は編集距離が改善、1件は悪化、1件は同値。最大出力長が145文字から79文字へ短くなり、余計な幻覚文字を削る一方で必要な副題まで削る例があった。現段階ではDPOを本番既定にせず、選好ペアを増やしてから再評価する。

## 成果物

- `outputs/paddleocr_vl_active_sft_checkpoint_92/`
- `outputs/paddleocr_vl_active_dpo_checkpoint_32/`
- `outputs/ocr_active_review/v1/results/comparison.json`
- `outputs/ocr_active_review/v1/results/search_retrieval.json`
