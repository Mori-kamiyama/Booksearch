# Qwen3-VL 8Bによる棚画像と蔵書の紐付け

## 方針

入力は `/Users/yuta/Downloads/picture` の原画像255枚とする。原画像を変更せず、既存YOLOで棚領域を切り出し、Modal上の `Qwen/Qwen3-VL-8B-Instruct` によって背表紙を左から順に転記する。出力書名はローカルの `outputs/library/library.db` へ曖昧照合し、原画像、棚Crop、座標、OCR原文、候補書誌を同じレコードに保持する。

背表紙OBBを全画像へ直接適用すると、最初の5枚だけで399個の背表紙が検出された。255枚には撮影範囲の重複があり、単純比例では1万件を超える。したがって最初から全背表紙を個別推論せず、通常は棚Cropから複数冊を一括抽出する。JSON不正、低信頼、蔵書DB不一致のCropだけをOBBで1冊ずつ再処理する。この段階的構成なら画像との対応関係を失わず、GPU推論量を抑えられる。

## 再現性とデータ管理

- モデル: `Qwen/Qwen3-VL-8B-Instruct`
- Hugging Face revision: `e0a319f4d147b3916275a053b0583ca82f351e90`
- ライセンス: Apache-2.0
- Modal Volume: `booksearch-hf-cache`
- GPU: L4（24GB）。収まらない場合だけL40Sへ変更する
- 原画像やCropをモデル学習へ利用する外部APIではなく、公開ウェイトを専用Modalコンテナ内で実行する
- 途中結果はJSONLへ追記し、成功済みCropを再実行しない

## 実行

まず棚Cropだけを作る。

```bash
uv run python scripts/build_book_catalog.py \
  --source /Users/yuta/Downloads/picture \
  --output-dir outputs/qwen3_vl_modal \
  --skip-ocr --no-lookup --device mps
```

4 Cropの煙試験:

```bash
modal run modal_apps/qwen3_vl_ocr.py \
  --catalog outputs/qwen3_vl_modal/catalog.json \
  --output outputs/qwen3_vl_modal/qwen_results.json \
  --max-items 4
```

同じコマンドから `--max-items` を外すと、JSONLを使って続きから全件を処理する。

## 評価

モデル自身の `confidence` は採否の根拠にしない。人手で確認したサンプルに対する文字誤り率と、蔵書DBの正しい書籍へ照合できた割合を主指標にする。書名が一致していても、著者・版・巻の識別に必要な文字が欠けている場合は正解にしない。

## 2026-09-23 煙試験

全255枚に `imgsz=640 / conf=0.5` でBookfinder YOLO11n-OBBを適用した結果は12,138検出、厳格な近接画像重複除去後12,065 Cropだった。当初想定した実在冊数約1,500より大幅に多い。同じ本の別角度、部分検出、連続写真での再出現を画像ハッシュだけでは十分に統合できていない。

Modal L4上のQwen3-VL 8Bで単冊Crop 8件を試した結果:

- 有効JSON: 8/8
- 1 Cropあたり生成時間: 3.1〜9.0秒、平均約4.5秒
- 蔵書DB: 自動一致6件、要確認候補1件、候補なし1件
- 8件中2件は同じ長い書名の部分Cropと全体Cropで、OCR後の書誌IDなら統合可能

対して棚Crop 4件の一括抽出は有効JSON 4/4だったが、密集棚で誤読が多く、1件36.4秒かかった。最終データは単冊OBB方式を使う。

ModalのL4公示単価 `$0.000222/秒` と実測平均4.5秒から、12,065 Cropの純GPU生成費は約12ドル、逐次時間は約15時間と見積もる。全量処理前に、複数コンテナ化とOCR後の `library_db_id` 統合を実装し、まず500〜1,500 Cropで重複率と照合率を測る。
## Qwen3-VL-4B + vLLM 全件実行（2026-09-23）

`Qwen/Qwen3-VL-4B-Instruct` を Modal の L40S 上で vLLM 0.30.0 に載せ、OpenAI互換APIへ最大32並列で投入した。Bookfinder OBBによる正規化済み背表紙 crop 12,065件を全件処理できた。

- 推論時間: 約14分（コールドスタート込みで約16分）
- スループット: おおむね17–22 crop/秒（32件のスモークテストでは15.18 crop/秒）
- API成功: 12,065 / 12,065
- JSONとして正常: 11,883 / 12,065（98.5%）
- 書籍情報あり: 11,855件
- ローカル蔵書DBとの自動一致: 6,899 crop
- 要確認候補: 1,330 crop
- 一致なし: 3,626 crop
- 一致した蔵書ID: 1,598冊（うち自動一致のみで1,451冊）
- 何らかの一致があった元画像: 242 / 253枚

12,065 crop は同じ本の別写真や部分検出を大量に含むため、実体として約1,500冊という見積もりと整合する。182件の不正JSONは、主に同じ語や数字を反復して最大トークンへ到達したケースだった。

現時点では SGLang を追加評価する必要性は低い。vLLMですでに全件を短時間で処理できたため、次は全件を別エンジンで再実行するより、182件の生成失敗と一致なしのうち有望な crop のみを8Bへフォールバックする方が費用対効果がよい。

成果物:

- `outputs/qwen3_vl_modal_spines/qwen4b_vllm_results.json`
- `outputs/qwen3_vl_modal_spines/qwen4b_vllm_results.jsonl`
- `modal_apps/qwen3_vl_vllm.py`
- `scripts/postprocess_qwen_vllm.py`

## 8Bフォールバックと学習用データ（2026-09-23）

4Bで自動一致しなかった行に、空結果も加えた5,187 cropを `Qwen/Qwen3-VL-8B-Instruct` + vLLM + L40Sへフォールバックした。

- 対象: 5,187 crop
- 成功: 5,187 / 5,187
- 正常JSON: 5,110 / 5,187
- 辞書との自動一致: 1,694 crop
- 要確認: 977 crop
- 一致なし: 2,443 crop
- 推論時間: 起動込み約9分40秒
- 本番ラン費用: $0.35932157
- 32件スモークテスト費用: $0.15567471（初回コンパイルを含む）

4B本番ラン単体のModal請求額は $0.61930076。4Bの各種試行、旧8Bテスト、今回の8Bフォールバックをすべて含む同日の合計は、確認時点で $2.26673834 だった。

4Bと8Bの結果を辞書スコア0.88以上で統合し、同一書籍を最大8画像に制限した学習用JSONLを作成した。書籍ID単位で90/5/5に分割しているため、同じ本がtrain/validation/testを跨がない。

- 学習候補（上限制限前）: 7,311画像
- 最終サンプル: 6,276画像 / 1,512冊
- train: 5,613画像 / 1,353冊
- validation: 303画像 / 76冊
- test: 360画像 / 83冊
- 8Bフォールバックが救済した学習候補: 1,443画像
- 欠損画像: 0
- split間の書籍ID重複: 0

成果物:

- `outputs/qwen3_vl_modal_spines/qwen8b_vllm_results.json`
- `outputs/qwen3_vl_modal_spines/training_v1/manifest.json`
- `outputs/qwen3_vl_modal_spines/training_v1/train.jsonl`
- `outputs/qwen3_vl_modal_spines/training_v1/validation.jsonl`
- `outputs/qwen3_vl_modal_spines/training_v1/test.jsonl`
- `scripts/select_qwen_fallback.py`
- `scripts/build_vlm_training_dataset.py`
