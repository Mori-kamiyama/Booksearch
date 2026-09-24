# Cerebras Qwen 3.8 27B vs Gemini 棚OCR実測

実施日: 2026-09-24

## 結論

現状の棚全体OCRでは `gemini-3.1-flash-lite` を採用する。Cerebras の
`qwen-3.8-27b` は約2倍速いが、取りこぼしと文字誤りが大きく、辞書照合を
含めても品質差を回収できない。1000 ms目標には近いので、候補生成器や
Gemini障害時のフォールバックとしては再検討余地がある。

## 条件

- 同じ棚crop 38枚（1652x1531前後を含む）
- 棚画像を分割せず、1画像を1回のVLM入力にした
- Gemini: 直API、`gemini-3.1-flash-lite`、thinking `MINIMAL`、JSON schema
- Cerebras: 直API、`qwen-3.8-27b`、reasoning `none`、最大3回試行
- 正解近似: 既存catalogの書籍辞書照合済みタイトル540件
- Unicode正規化後の文字列類似度で、棚ごとに一対一の最大割当を行った

正解近似は過去のGemini OCRから図書辞書へ照合して作られているため、Geminiに
有利な評価である。ただし、出力冊数と目視例でもQwen側の取りこぼしは確認できる。

## 結果

| 指標 | Gemini 3.1 Flash-Lite | Cerebras Qwen 3.8 27B |
|---|---:|---:|
| 成功棚 | 38/38 | 37/38（最大3回試行） |
| 出力書名数 | 534 | 421 |
| 平均レイテンシ | 2.625秒 | 1.277秒 |
| 中央値 | 2.656秒 | 1.095秒 |
| p95 | 3.319秒 | 2.300秒 |
| 1秒未満 | 0/38 | 10/38 |
| 類似度0.7 Recall | 81.9% | 44.3% |
| 類似度0.7 Precision | 82.8% | 56.8% |
| 類似度0.7 F1 | 82.3% | 49.7% |
| 類似度0.9 F1 | 69.6% | 28.3% |

Cerebrasは38棚を48.5秒、Geminiは99.8秒で逐次処理した。Cerebrasの単発成功時は
0.7〜1.2秒が多いが、JSON破損による再試行で2〜3秒になる棚がある。

## トークンと概算費用

記録された成功応答のトークンのみで計算した。Cerebrasの失敗試行分は記録して
いないため、同社側は下限値である。

| 指標 | Gemini | Cerebras Qwen |
|---|---:|---:|
| 入力tokens / 38棚 | 42,129 | 71,839 |
| 出力tokens / 38棚 | 5,721 | 3,894 |
| 38棚の概算 | $0.0191 | $0.0769以上 |
| 1棚 | $0.000503 | $0.002024以上 |
| 1,500棚 | $0.75 | $3.04以上 |

価格前提は Gemini が入力 $0.25/M・出力 $1.50/M、Cerebras Qwen が入力
$0.99/M・出力 $1.49/M。

## 観察

- Cerebrasは画像入力を正式に受け付け、画像tokensもusageに計上された。
- reasoningを切ると生成自体は非常に速い。遅延の中心は画像転送・前処理・待ち時間。
- Qwenは細い日本語背表紙で、タイトルの短縮、複数冊の結合、一般的な書名への
  置換が多い。
- 1棚を1000 ms以内に保存まで完了する要件には、直列の外部API呼び出しだけでは
  どちらも安定して届かない。撮影後非同期化、先行アップロード、棚分割の並列化、
  または候補辞書を使った制約付き認識が必要。

## 生成物

- `outputs/shelf_ocr_bench/gemini_3_1_flash_lite_direct/summary.json`
- `outputs/shelf_ocr_bench/cerebras_qwen_3_8_27b_final/summary.json`
- `scripts/gemini_shelf_ocr.py`
- `scripts/cerebras_shelf_ocr.py`
