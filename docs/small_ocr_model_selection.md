# 1B以下の背表紙OCRモデル選定

更新日: 2026-09-24

## 結論

第一候補は `PaddlePaddle/PaddleOCR-VL-1.6` (0.9B, Apache-2.0)。同一の背表紙100枚で `ATH-MaaS/OvisOCR2` (0.9B, Apache-2.0) より日本語OCR精度が良く、5,613件で1 epochのLoRA学習を行った結果、書籍単位で分離したtestでも全指標が改善した。PP-OCRv6は軽量だがVLMではなく、現在の「背表紙画像1枚 + 全文転記」データをそのまま認識モデル学習へ投入できないため第二段階の比較対象とする。

## 候補

| モデル | 規模 | ライセンス | 日本語 | この用途での判断 |
|---|---:|---|---|---|
| PaddleOCR-VL-1.6 | 0.9B | Apache-2.0 | 対応 | 第一候補。縦長の日本語背表紙で今回最良 |
| OvisOCR2 | 0.9B | Apache-2.0 | 要実測 | Qwen3.5-0.8B由来で魅力的だが反復出力が多い |
| PP-OCRv6 small/medium | 5.4M / 34.5M | Apache-2.0 | 対応 | AWS推論は最も軽い。学習には行単位crop/boxが必要 |
| SmolVLM-500M-Instruct | 0.5B | Apache-2.0 | 公式上は英語中心 | 日本語OCRの主モデルにはしない |
| LightOnOCR-2-1B | 1B | Apache-2.0 | 公式対応言語に日本語なし | 今回は除外 |
| GOT-OCR2 | 約0.58B | 不明瞭 | 対応余地あり | 「権利がフリーに近い」という条件では除外 |

参照:

- PaddleOCR-VL-1.6: <https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.6>
- PaddleOCR / PP-OCRv6: <https://github.com/PaddlePaddle/PaddleOCR>
- PaddleOCR-VL SFT: <https://github.com/PaddlePaddle/ERNIE/blob/release/v1.5/docs/paddleocr_vl_sft.md>
- OvisOCR2: <https://huggingface.co/ATH-MaaS/OvisOCR2>
- SmolVLM-500M: <https://huggingface.co/HuggingFaceTB/SmolVLM-500M-Instruct>
- LightOnOCR-2-1B: <https://huggingface.co/lightonai/LightOnOCR-2-1B>

Apache-2.0は商用利用、改変、再配布を広く許す。ただしライセンス文・NOTICE等の条件は維持する必要がある。また、モデルのライセンスが入力画像や教師データそのものの権利を消すわけではない。今回の自分で撮影した棚画像と蔵書情報を中心にしたデータは、外部APIの出力をそのまま再学習する場合より整理しやすいが、公開・販売時には画像に写る個人情報や第三者コンテンツを別途確認する。

## ゼロショット比較

`training_v1/test.jsonl` の先頭100件を、同じL4、vLLM 0.30、temperature 0で比較した。正解転記はQwen 4B/8B由来の疑似ラベルなので、絶対精度ではなく候補比較の指標である。

| 指標 | OvisOCR2 | PaddleOCR-VL-1.6 |
|---|---:|---:|
| 成功件数 | 100/100 | 100/100 |
| 疑似正解との平均類似度 | 0.477 | 0.692 |
| 正規化CER（低いほど良い） | 2.811 | 0.608 |
| 蔵書タイトル完全包含率 | 56% | 64% |
| 蔵書タイトル平均類似度 | 0.327 | 0.551 |
| 1件の中央値 | 2.05秒 | 0.88秒 |

OvisOCR2は一部の縦長cropで数字や短い文字列を反復した。PaddleOCR-VL-1.6はタイトル、著者、請求記号を比較的安定して出した。

成果物:

- `outputs/small_ocr_benchmark/summary.json`
- `outputs/small_ocr_benchmark/ovisocr2_100.json`
- `outputs/small_ocr_benchmark/paddleocr_vl_1_6_100.json`
- `modal_apps/small_ocr_benchmark.py`
- `scripts/evaluate_small_ocr_benchmark.py`

## 学習データ

既存の書籍単位splitを保ち、画像間リークを避けたままOCR専用形式へ変換した。

| split | 件数 |
|---|---:|
| train | 5,613 |
| validation | 303 |
| test | 360 |

保存先:

- `outputs/qwen3_vl_modal_spines/ocr_sft_v1/erniekit/`
- `outputs/qwen3_vl_modal_spines/ocr_sft_v1/ms_swift/`
- `outputs/qwen3_vl_modal_spines/ocr_sft_v1/manifest.json`

教師は「画像に見える全文転記」で、プロンプトは公式例に合わせて `OCR:` とした。疑似ラベルには誤字が含まれるため、モデル評価用には最低100〜300枚の人手校正test setを別に用意する。蔵書タイトルは評価補助には使えるが、見えていない文字まで正解に加えるとOCRではなく書名推定を学ぶため、全文転記の教師へ無条件には混ぜない。

## LoRA経路

ms-swift 4.5.3で、言語モデル側だけにrank 16 LoRAを挿入する。学習対象は約604万パラメータ（全体の0.663%）。ViTとalignerは最初の実験では凍結する。

PaddleOCR-VL-1.6の `get_image_features(...).pooler_output` が画像ごとのtupleを返す一方、ms-swift 4.5.3が単一Tensorを前提にしている互換問題があった。`modal_apps/ms_swift_paddleocr16_patch.py` でtupleを連結する最小パッチを入れた。5 stepのsmoke testはL4で完走し、validation 303件で eval loss 1.214、token accuracy 0.7555だった。これは配線確認であり、改善精度ではない。

実行コード:

- `modal_apps/paddleocr_vl_lora.py`
- `modal_apps/ms_swift_paddleocr16_patch.py`
- `scripts/export_ocr_sft_datasets.py`

本学習はL4で1 epoch相当の702 optimizer stepsまで実行した。所要時間は約22分、train loss 0.966、validation loss 0.865、validation token accuracy 0.804。最終step単体はloss 0.847、token accuracy 0.818だった。成功した本学習のModal実課金は `$0.2163`。環境修正、5-step smoke、本学習を含む学習試行全体は `$0.2767` だった。

保存先:

- Modal Volume: `booksearch-ocr-sft-checkpoints/paddleocr-vl-1.6-lora-1790177779/v0-20260923-153640/checkpoint-702`
- ローカルの配布用LoRA: `outputs/paddleocr_vl_lora_checkpoint_702/`（24 MB）

## LoRA後のheld-out評価

ゼロショット比較と同一のtest先頭100件で、checkpoint-702をtemperature 0で評価した。学習・validation・testは書籍単位で分離済み。

| 指標 | ベース | LoRA後 | 変化 |
|---|---:|---:|---:|
| 疑似正解との平均類似度 | 0.692 | 0.750 | +0.058 |
| 正規化CER（低いほど良い） | 0.608 | 0.423 | -0.185 |
| 蔵書タイトル完全包含率 | 64% | 73% | +9pt |
| 蔵書タイトル平均類似度 | 0.551 | 0.605 | +0.054 |

推論100件は228秒だった。ただしベースはvLLM、LoRA後はTransformersの8件バッチなので、この数字を速度比較には使わない。精度は全指標で改善したため、checkpoint-702を次の人手評価へ進める。疑似ラベルへの一致が改善したことは確認できたが、それ自体は真のOCR正解率を保証しない。

成果物:

- `outputs/small_ocr_benchmark/paddleocr_vl_1_6_lora_100.json`
- `outputs/small_ocr_benchmark/base_vs_lora_summary.json`
- `outputs/small_ocr_benchmark/lora/test_predictions_100.jsonl`
- `scripts/convert_ms_swift_ocr_results.py`

## 図書DB検索との組み合わせ

同じ100件についてOCR出力だけをクエリにし、`src/lookup.py` のローカル図書DB検索（候補5件、最低score 0.72）と組み合わせた。書名や正解IDはクエリ生成には使用していない。

| モデル・検索方法 | 書名@1 | 書名@5 | DB ID@1 | DB ID@5 | 候補なし |
|---|---:|---:|---:|---:|---:|
| ベース・全文1クエリ | 58% | 58% | 53% | 58% | 40% |
| LoRA・全文1クエリ | 62% | 62% | 56% | 62% | 36% |
| ベース・行アンサンブル | 84% | 84% | 78% | 84% | 13% |
| LoRA・行アンサンブル | **87%** | **89%** | **81%** | **89%** | **10%** |
| Gemini 3.1 Flash Lite・タイトル検索 | **87%** | **90%** | 79% | **90%** | **10%** |
| LoRA候補なし時だけGemini fallback | **94%** | **98%** | **86%** | **98%** | - |
| LoRA + Gemini・RRF統合 | **95%** | **98%** | **87%** | **98%** | - |

行アンサンブルは全文、正規化後4文字以上の各行、隣接2行の連結をそれぞれ検索し、候補を最大scoreで統合する。LoRAでは書名@1が87/100、2〜5位が2/100、候補ありだが正解なしが1/100、候補なしが10/100だった。同名・同一タイトルの複数蔵書レコードがあるため、実用上の書名一致と厳しいDB ID一致を分けている。

この100件は元々Qwen疑似ラベルと図書DBが高スコアで一致したものから作ったtest splitなので、全cropに対する検索性能より高く見える選択バイアスがある。未一致・要確認・生成失敗を含む母集団での最終@Kは別途測定する。

Geminiは既存の `src.ocr.TITLE_OCR_PROMPT` を使用し、`gemini-3.1-flash-lite-preview` にタイトルだけを抽出させた。5並列で100/100件成功し、API処理は44.2秒。Gemini単体とLoRA単体の書名@1は同じ87%だが、両方が成功したのは79件、LoRAだけ成功とGeminiだけ成功が各8件あった。正解を参照せず、両方の検索順位をreciprocal rank fusion（k=60）すると書名@1 95%、@5 98%になった。さらにLoRA検索で候補が出なかった10件だけGeminiへ送る方式でも書名@1 94%、@5 98%だった。外部API費用・画像送信を抑えられるため、実運用ではこのfallback方式を第一候補とする。

### Gemini失敗13件の診断

書名@1を外した13件を元画像まで確認したところ、原因はGemini APIの失敗ではなく次の3群だった。

- 8件: OCRは利用可能だが、正式タイトルに副題を加えた出力や短いDBタイトルのため検索scoreが0.72未満。例: `造形の基礎 アートに生きる…` は正しく読めているが、DBの `造形の基礎` に対して0.713で候補落ち。
- 2件: 疑似正解側の対応が粗い。画像は `漫画サピエンス全史 文明の正体/人類の誕生編` でGeminiもその通り読んだが、評価ラベルは通常版の `サピエンス全史`。
- 3件: crop品質。極端なぼけ1件、背表紙の左側が欠けたcrop 1件、2冊が1cropに入ってGeminiが両方を正しく列挙したもの1件。

検索の `review_min_score` だけを0.72から0.70へ下げる再計算では、Geminiの書名@1は87%から93%、@5は90%から96%、候補なしは10件から4件になった。今回の集合では誤爆増加はなかったが、閾値変更は未一致を含む母集団でprecisionも測ってから本体へ反映する。

成果物:

- `outputs/small_ocr_benchmark/search_retrieval_at_k.json`
- `outputs/small_ocr_benchmark/gemini_3_1_flash_lite_100.json`
- `outputs/small_ocr_benchmark/gemini_search_retrieval_at_k.json`
- `outputs/small_ocr_benchmark/lora_gemini_search_fusion.json`
- `benchmark/gemini_spine_search_benchmark.py`
- `scripts/evaluate_ocr_search_retrieval.py`
- `scripts/evaluate_ocr_search_fusion.py`

## 次の判定

1. 100〜300枚を画像を見ながら人手校正し、ベースとLoRAを再評価する。
2. 人手正解でもCERとタイトル包含率が改善した場合にLoRAを採用する。
3. AWSの最終構成候補で量子化後の速度・メモリ・費用を測る。
4. PP-OCRv6 mediumは安価な比較ベースとして推論する。追加学習する場合だけ背表紙内の行cropまたはtext boxを作る。

複数epoch化、ViTの解凍、PP-OCRv6用のbox作成は、人手評価で必要性が確認できるまで行わない。

## 人手校正538件の利用方針

`ocr_annotations (2).jsonl` は538件すべてが構造上有効で、既存の書籍単位splitではtrain 426、validation 12、test 100だった。SFT前の保守的フィルターとして、フラグなし、かつ正規化した蔵書書名を転記内に含むものだけを採用すると、train 389、validation 6、test 78となる。残る65件は `review.jsonl` に分離する。書名不包含は誤りの証明ではなく、crop欠けやDB書名との差も含むため、人手確認後に戻してよい。

最初はPaddleOCR-VL-1.6のLoRAをSFTする。OCRには明確な正解文字列があるため、538件規模でオンラインRLを行うより、token-level SFTのほうがデータ効率と安定性が高い。RL/DPOを試す場合は、SFTモデルの誤出力を人が修正し、同じ画像について `chosen=人手正解`、`rejected=モデル出力` のペアを蓄積してから第二段階として実施する。

成果物:

- `scripts/prepare_human_ocr_sft.py`
- `outputs/ocr_annotations/human_sft_538/ms_swift/`
- `outputs/ocr_annotations/human_sft_538/review.jsonl`

### 人手データによる第2段SFT結果

既存の疑似ラベルLoRA `checkpoint-702` を初期値として、人手校正train 389件を3 epoch相当（146 optimizer steps）、learning rate `2e-5` で追加SFTした。ViTとalignerは凍結し、学習対象は言語モデル側LoRAの約604万パラメータ。L4での実学習は約6分28秒、train loss 0.3005、validation 6件でtoken accuracy 0.8843だった。validationは6件しかないため、モデル選択はtest 78件の結果を優先する。

同一の人手校正test 78件で比較した結果:

| 指標 | 疑似ラベルLoRA | 人手SFT後 | 変化 |
|---|---:|---:|---:|
| 正規化完全一致 | 34.6% | **50.0%** | +15.4pt |
| 平均CER（低いほど良い） | 0.874 | **0.424** | -0.451 |
| 人手ラベル平均類似度 | 0.716 | **0.783** | +0.068 |
| 書名平均類似度 | 0.624 | **0.804** | +0.180 |
| 書名の完全包含率 | **89.7%** | 80.8% | -9.0pt |

書名包含率だけは下がったが、短いタイトル中心の出力へ寄った影響が大きく、検索エンジンとの組み合わせでは改善した。

| 検索方法 | モデル | 書名@1 | 書名@5 | DB ID@1 | DB ID@5 |
|---|---|---:|---:|---:|---:|
| 全文1クエリ | 疑似LoRA | 70.5% | 70.5% | 62.8% | 70.5% |
| 全文1クエリ | 人手SFT | **80.8%** | **80.8%** | **71.8%** | **80.8%** |
| 行アンサンブル | 疑似LoRA | 89.7% | 91.0% | 82.1% | 91.0% |
| 行アンサンブル | 人手SFT | **91.0%** | **91.0%** | 82.1% | 91.0% |

最初の試行は `resume_from_checkpoint` が旧global step 702を継承して0 stepで終了した。`--adapters` でLoRA重みだけを読み、optimizerとstepを新規作成する方式へ修正して正常に完走した。

成果物:

- Modal Volume: `booksearch-ocr-sft-checkpoints/paddleocr-vl-1.6-human-sft-1790235336/v0-20260924-073602/checkpoint-146`
- ローカルLoRA: `outputs/paddleocr_vl_human_sft_checkpoint_146/`
- 比較結果: `outputs/ocr_annotations/human_sft_538/results/comparison.json`
- 検索評価: `outputs/ocr_annotations/human_sft_538/results/search_retrieval.json`
