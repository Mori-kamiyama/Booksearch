# PP-OCRv6 背表紙認識 fine-tuning 実験

実施日: 2026-09-24

## 目的

1棚を撮影から保存まで1秒以内で処理する同期OCR候補として、約20 MBの
`PP-OCRv6_small_rec`を人手校正済み背表紙データでfine-tuningし、固定testで
精度と速度を確認した。

## データと変換

- 元の人手校正train: 389枚
- active review SFT train: 369枚
- 重複排除後train: 758枚
- validation: 6枚
- 固定test: 78枚（学習には未使用）
- 各背表紙cropを時計回り・反時計回り90度の2方向へ回転
- trainは1,516画像、validation/testは方向別に評価
- 改行をASCII空白へ畳み、背表紙全体の転写を単一系列として学習
- 最大ラベル長77文字、設定上限80文字

データmanifestは
`outputs/ppocrv6_spine_rec/data/manifest.json`、作成コードは
`scripts/prepare_ppocrv6_rec.py`にある。

## 学習

- Model: `PP-OCRv6_small_rec` pretrained weights
- Input: multi-scale 640x48 / 640x64 / 960x64
- Epoch: 30
- LR: 1e-4 cosine decay、2 epoch warmup
- GPU: Modal L4
- 学習時間: 589.9秒
- 最大GPUメモリ: 約7.5 GB reserved
- 学習throughput: おおむね100〜130 samples/s

Remote run:
`/outputs/ppocrv6-small-spines-1790243171`

## 固定test結果

実運用に使える向きは反時計回り90度だった。`norm_edit_dis`は1に近いほど良く、
概算CERは `1 - norm_edit_dis`。

| Model | Exact | norm_edit_dis | CER | FPS |
|---|---:|---:|---:|---:|
| pretrained | 3.85% | 0.5001 | 0.4999 | 51.7 |
| epoch 5 | 1.28% | 0.5129 | 0.4871 | 61.4 |
| epoch 10 | 3.85% | 0.4893 | 0.5107 | 48.3 |
| epoch 15 | 2.56% | 0.4651 | 0.5349 | 67.0 |
| epoch 20 | 2.56% | 0.4973 | 0.5027 | 65.1 |
| epoch 25 | 0.00% | 0.4454 | 0.5546 | 56.6 |
| epoch 30 | 1.28% | 0.4585 | 0.5415 | 62.2 |

validation反時計回りの編集距離でepoch 5を選んだ。validationが6枚しかないため、
この選択には大きな分散がある。選択後の固定testを見る限り、fine-tuningによる
CER改善は約1.3ポイントと小さく、完全一致は悪化した。

## 判断と仮説

速度は約60 crop/sなので、40冊の棚の認識部分は約0.7秒で処理できる可能性がある。
一方、現状精度では同期経路の主OCRとして採用できない。

主因はモデル規模より教師形式の不一致と考える。PP-OCRのrecognition modelは基本的に
1 text-line cropから1文字列を認識する。一方、現在の画像は背表紙全体で、タイトル、
副題、著者、出版社などが離れた複数領域・複数方向に存在し、教師はそれらを並べた全文
転写である。VLMには自然な教師だが、CTC/NRTRのline recognizerには対応位置が曖昧である。

次にPP-OCR系を試すなら、背表紙全体→text detection→行cropを作り、各行を人手転写の
行と対応づけた教師データが必要。ただし自動対応の誤りが新たなノイズになるため、現在の
758件をそのまま追加学習するだけでは大幅改善は期待しにくい。

1秒以内の製品構成としては、同期経路で未学習またはepoch 5 PP-OCRv6を候補生成に使い、
書誌辞書検索で確定できるものだけ採用し、残りを非同期PaddleOCR-VL/Geminiへ送る構成を
引き続き検討する価値がある。

## 成果物

- 選択モデル: `outputs/ppocrv6_spine_rec/selected_epoch_5/`
- 全checkpoint評価: `outputs/ppocrv6_spine_rec/checkpoint_metrics.json`
- pretrained評価: `outputs/ppocrv6_spine_rec/pretrained_baseline_metrics.json`
- 学習・評価Modal app: `modal_apps/ppocrv6_rec.py`
- 学習設定: `modal_apps/ppocrv6_spine_rec.yml`
