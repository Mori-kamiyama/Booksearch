# Bookfinder YOLO11n-OBB による背表紙分割の予備調査

調査日: 2026-09-23

## 結論

`/Users/yuta/Downloads/picture` の本棚写真に Bookfinder の学習済み
`YOLO11n-OBB` を適用すると、背表紙ごとの回転矩形をかなり実用的な粒度で取得できた。
このデータでは、既存の「棚区画を切り出して複数冊まとめて OCR」する方式より、
`背表紙 OBB -> 傾き補正 crop -> 1冊単位 OCR` を試す価値が高い。

ただし、現時点では検出精度を測る正解ラベルがなく、目視による予備評価である。
また、Bookfinder リポジトリ自体にはライセンス表記が見当たらないため、同梱 weight の
再配布・製品利用は権利条件を確認してから判断する。

## 対象データ

- 画像ディレクトリ: `/Users/yuta/Downloads/picture`
- 画像数: 255枚
- 容量: 約603MB
- 主な解像度: 2268 x 4032
- 特徴: 同じ本棚を距離・角度を変えて連続撮影。直立本、傾いた本、横積み、空棚、
  木枠、掲示物、有孔板が混在する。

255枚は独立した255棚ではなく近接した連続写真が多い。将来 fine-tuning する場合、
ランダムな画像単位 split は類似フレームが train/val に混ざり、精度を過大評価する
おそれがある。棚または撮影シーケンス単位で split すべきである。

## 使用モデル

- Repository: <https://github.com/rauricus/Bookfinder>
- 確認 commit: `1d3aadef1018ad362bb267bf3bef8cac17386ac8`
- Weight: `models/obb/YOLO11-obb-n/detect-book-spines.train2.pt`
- Model: YOLO11n-OBB、単一 class `book-spine`
- 公開値: mAP50 0.972、mAP50-95 0.807、precision 0.963、recall 0.940
- 公開学習設定: imgsz 320、100 epochs

公開値は Bookfinder 側のデータセットに対する値であり、今回の日本語本棚写真での
精度を保証するものではない。

## 実画像での観察

全255枚からファイル名順に9枚おきで29枚を抽出し、Apple MPS で推論した。

| 条件 | 29枚の平均検出数 | 中央値 | 最大 | 観察 |
| --- | ---: | ---: | ---: | --- |
| imgsz 320 / conf 0.5 | 19.1 | - | 63 | 遠景の細い背表紙を多数見落とす |
| imgsz 640 / conf 0.5 | 51.3 | 45 | 167 | 現状で最もバランスがよい |
| imgsz 1024 / conf 0.5 | 60.2 | 46 | 217 | 検出は増えるが追加分の誤検出確認が必要 |

同じ29枚に `imgsz=640 / conf=0.25` を使うと平均68.0件まで増えたが、木枠、掲示物、
床や有孔板の反復模様を背表紙として拾う例が明確に増えた。初期値は `conf=0.5` がよい。

目視で確認できた長所:

- 直立した背表紙を1冊ずつ分離できる。
- 斜めに寄りかかった本にも回転矩形が沿う。
- 横方向に近い本も検出対象になる。
- `conf=0.5` では完全な空棚の代表画像が0件になった。
- 640入力なら、本棚全景でも多くの細い背表紙を拾える。

目視で確認できた失敗:

- 白く細い背表紙、隣と色や境界が似た背表紙を落とす。
- 画面端で切れた本や強く重なった本は不安定。
- 低 confidence では棚の木枠、掲示物、有孔板などを誤検出する。
- 1冊を複数領域として切る例、複数冊を1領域にまとめる例が少数ある。
- OBB が得られても、OCR可能な文字解像度があるとは限らない。

速度の参考値として、warm-up 後の29枚 batch 推論では MPS 上で `imgsz=640` の
Ultralytics reported inference が平均約6ms/枚、前後処理込みが約15ms/枚だった。
ファイル読み込みや1枚ずつの呼び出し、OCR時間はこの値に含めて評価し直す必要がある。

## 現行コードへの影響

`src/detection.py` の `detect_and_crop()` は通常検出の `result.boxes.xyxy` を読む。
OBB model は `result.obb.xyxyxyxy` を返すため、weight の差し替えだけでは検出結果を
取り出せない。

最小の実装変更は次の通り。

1. `result.obb.xyxyxyxy` の四隅を読み取る。
2. 四隅に対して perspective transform を行い、背表紙を長方形へ正規化する。
3. 長辺が縦になるよう90度単位で向きをそろえる。
4. 0度/180度の2候補、または OCR の orientation 判定を使って文字方向を決める。
5. crop の短辺、blur、画面端接触を評価して OCR 対象を絞る。
6. 一覧上の座標用途には OBB の外接 AABB も保存する。

既存の `DetectedBox.xyxy` を直ちに置換するより、四隅を保持する `polygon` または
OBB専用型を追加し、現行の箱検出経路と並行して比較できるようにするのが安全である。

## 推奨する次の実験

いきなり255枚を再学習データにせず、まず撮影条件が重ならない20〜30枚を選び、
背表紙 OBB の正解ラベルを付ける。

評価する指標:

- 背表紙単位の precision / recall
- 1冊を複数分割した数
- 複数冊を結合した数
- deskew crop を Gemini OCR に渡したときの書名取得率
- 従来の棚区画 crop に対する OCR API コストと処理時間

採用判断は検出 mAP だけでなく、最終的な「正しい書名を1冊ずつ得られた割合」で行う。
既製 weight が十分なら fine-tuning は不要。日本語本、白い細背表紙、画面端の本で
不足が残る場合だけ、この255枚から重複を除いた学習セットを作る。

## 棚Cropとの二段構成

本棚全景へ直接背表紙OBBを適用するより、既存の棚Crop YOLOを先に通す構成を優先する。

```text
撮影画像
  -> 棚Crop YOLO
  -> 棚区画ごとの画像
  -> book-spine OBB
  -> perspective補正した1冊crop
  -> OCR・書誌照合
```

この構成では、床、壁、掲示物、空棚などを背表紙モデルへ見せる面積が減るため、
今回確認した背景誤検出を抑えられる可能性が高い。また、同じ入力サイズでも1冊あたりの
画素数が増えるため、遠景で細い背表紙の recall と後段OCRの文字解像度を同時に改善できる。

棚Crop同士が重なる場合は、同じ本を二重登録しないよう、元画像座標へOBBを戻して
polygon IoU、OCR文字列、棚IDで重複排除する必要がある。

## YOLO26n-OBBとの比較

Bookfinder repositoryには、同じ背表紙データで学習された
`YOLO26-obb-n/detect-book-spines.train4.pt` も含まれる。29枚に
`imgsz=640 / conf=0.5` で適用した。

| model | 公開 mAP50 | 公開 mAP50-95 | 今回の平均検出数 | warm後MPS inference中央値 | wall time中央値 |
| --- | ---: | ---: | ---: | ---: | ---: |
| YOLO11n-OBB train2 | 0.972 | 0.807 | 51.6 | 3.8ms/枚 | 88.2ms/枚 |
| YOLO26n-OBB train4 | 0.959 | 0.791 | 51.7 | 6.0ms/枚 | 81.0ms/枚 |

検出数と目視結果はかなり近い。YOLO26版は一部画像でYOLO11版が落とした本を拾う一方、
完全な空棚で縦レールを1件検出した。YOLO26版は推論本体がやや遅いが、end-to-end構造で
NMS後処理がほぼ不要なため、画像ロード等を含む今回のwall timeは逆にわずかに短かった。
初回だけの計測ではcompile/warm-upの影響が大きく、速度比較には使えない。
公開指標もYOLO11版がわずかに高いため、現段階で
常時アンサンブルする根拠は弱い。YOLO11版を本命、YOLO26版をA/B比較用とし、正解ラベルを
付けた評価セットで補完関係が確認できた場合だけアンサンブルを検討する。

## Hugging Faceの近似モデル調査

2026-09-23時点で、Hugging Face上に「book-spine専用YOLO26-OBB」は見つからなかった。
`Ultralytics/YOLO26` や `openvision/yolo26-n-obb` は取得できるが、DOTA航空画像の15クラスを
学習した汎用OBB weightであり、背表紙検出済みモデルではない。背表紙データによる
fine-tuningの初期weightとしては使える。

近い公開モデルは次の通り。

| model | 検出形式 | 評価 |
| --- | --- | --- |
| `izi0/mmrotate-bookspine` | Oriented R-CNN / OBB | 独立した背表紙OBB候補。MIT表記。約366MB。model cardと評価値がなく、旧MMRotate実行環境が必要 |
| `ranathungaWK/book-spine-detection-yolov8` | YOLOv8n / AABB | 約6.2MB。公称mAP50 0.965、mAP50-95 0.813。ライセンス未記入 |
| `quist99/book-spine-detector-yolov8` | YOLOv8 / AABB | 約19.8MB。model card・ライセンス・評価値なし |
| `siddharth060104/book_spine_detection` | 不明 | weightがなくREADMEのみなので利用不可 |

`izi0/mmrotate-bookspine` は異なるモデル系列による比較対象としては興味深いが、サイズ、
依存関係、説明不足を考えると、最初に採用する候補ではない。まず手元で動作確認できた
BookfinderのYOLO11/YOLO26 weightを棚Crop後に比較する方が費用対効果が高い。

- YOLO26 official weights: <https://huggingface.co/Ultralytics/YOLO26>
- Book-spine Oriented R-CNN: <https://huggingface.co/izi0/mmrotate-bookspine>
- Book-spine YOLOv8n: <https://huggingface.co/ranathungaWK/book-spine-detection-yolov8>
- Undocumented book-spine YOLOv8: <https://huggingface.co/quist99/book-spine-detector-yolov8>

## 追加データセット候補

Bookfinderが列挙しているRoboflow由来データ以外にも、次の候補が見つかった。

### 1. BookSpineDataset v1.0

最優先候補。2026-06公開。

- 1,505枚の実在図書館本棚写真
- 全背表紙に四隅のOBB annotation
- Ultralytics YOLO OBB形式をそのまま利用可能
- split済み: train 903 / val 301 / test 301
- 40枚にはtitle、author、publisher、call number等の可視文字annotationも付属
- archive約2.08GB
- 上海電機学院臨港キャンパス図書館でスマートフォン撮影
- 非商用の学術研究・再現用途に限定。商用利用は要許可

中国語図書館というdomain差はあるが、日本語本棚に近い漢字、白い背表紙、請求記号ラベル、
密集配置を含む可能性が高く、既存Bookfinder weightの追加fine-tuningに最も向いている。
ただし、公開直後で利用実績が少ないため、全量学習前に画像品質とannotationの一貫性を
100枚程度samplingして確認する。

- Repository: <https://github.com/wyn2282145606-crypto/BookSpineDataset>
- Release: <https://github.com/wyn2282145606-crypto/BookSpineDataset/releases/tag/v1.0>

### 2. bookshelf-recognition-2 / shelf-photos-batch1

- human-annotated AABB: train 4,624枚・77,468 boxes、val 1,323枚・22,360 boxes
- generic bookshelf / bookstore写真
- COCO exportをHugging Faceから取得可能
- 追加で実図書館写真285枚と自動label proposalを収録
- OBBではなく通常の水平矩形
- Hugging Face dataset cardは`license: other`。元Roboflow projectはMITとの説明があるが、
  再配布前に元projectの条件を再確認する

規模は大きいが、AABBを角度0のOBBへ機械変換して混ぜると角度学習を偏らせる。
既存の棚/本AABB detectorの強化、またはBookfinder OBBによるpseudo-labelを人手修正して
OBB化する素材として使う。

- Dataset: <https://huggingface.co/datasets/Geraldine/shelf-photos-batch1>
- Trained RF-DETR model: <https://huggingface.co/Geraldine/rf-detr-nano-bookshelf>

### 3. book-spine-ocr

- 背表紙crop 2,730件: train 2,184 / val 273 / test 273
- title、author、call numberのJSON正解付き
- 検出annotationではなく、deskew後のOCR学習・評価用
- 約142MBの512px版と約1.13GBのfull版
- samplingした内容は英語中心
- dataset cardに明確なlicense表記がないため、利用条件を確認する

Gemini OCRの固定評価セット、または将来のローカルVLM/OCR fine-tuningには使えるが、
日本語OCR性能の保証にはならない。

- Dataset: <https://huggingface.co/datasets/quist99/book-spine-ocr>

### 4. 論文記載のみのデータ

2024年のImproved Oriented R-CNN論文では、661枚・15,454背表紙instanceのOBB相当データと、
未annotatedの実図書館画像425枚が報告されている。ただし論文ページから直接取得できる
公開archiveは確認できなかったため、現時点では学習データ候補に数えない。

- Paper: <https://pmc.ncbi.nlm.nih.gov/articles/PMC11679322/>

### 採用順序

1. `BookSpineDataset v1.0` を取得し、100枚samplingでannotation QA
2. 既存Bookfinder YOLO11n/YOLO26n weightを初期値として追加fine-tuning
3. `/Users/yuta/Downloads/picture` は類似フレームを除き、棚単位splitで少量追加
4. AABB大規模datasetは、OBBモデルの不足が確認された場合だけpseudo-label経由で使う
5. OCR datasetは検出学習と分離し、1冊crop後のOCR評価に使う

## BookSpineDataset smoke test

全量2.08GBを取得せず、release ZIPから元のsplitを保ったまま、固定seedで
train 160枚 / val 40枚だけRange抽出した。合計3,195 OBBで、目視した12枚のannotationは
概ね背表紙へ正確に沿っていた。trainの1頂点だけ正規化座標が`1.000161`だったため、
smoke用コピーでは`1.0`へclampした。

Bookfinder YOLO11n-OBBを160枚で5 epoch追加学習した結果、BookSpineDataset内のvalは
大きく改善した。

| model | precision | recall | mAP50 | mAP50-95 |
| --- | ---: | ---: | ---: | ---: |
| Bookfinder original | 0.586 | 0.898 | 0.723 | 0.234 |
| BookSpineData追加学習 | 0.931 | 0.970 | 0.982 | 0.588 |

一方、`/Users/yuta/Downloads/picture`から固定抽出した29枚では明確に悪化した。
`imgsz=640 / conf=0.5`の平均検出数は51.6件から10.4件へ減り、`conf=0.05`まで
下げても33.4件だった。既存の棚Crop YOLOを先に適用した98区画でも、検出総数は
1,184件から846件、平均は12.1件から8.6件へ減少した。目視でも、日本語の細い本、
傾いた本、奥行きのある配置の見落としが増え、一部では棚板や支柱へ縦長の誤検出が出た。

したがって、このdatasetだけでの追加fine-tuningは採用しない。dataset内valの向上は
撮影条件とannotation方針への適合であり、手元棚への汎化改善ではなかったと判断する。
BookSpineDatasetは近距離・一段・直立本が中心で、手元データの遠景、複数段、傾き、
日本語の細い背表紙との差が大きいという仮説が最も妥当である。

再利用する場合は、BookSpineDataset単独ではなく、手元棚の少量正解ラベルと
Bookfinder元学習分布を混ぜる。まず手元の棚Crop 20〜30区画をlabelして固定testにし、
元weight、混合学習weightを同じtestで比較する。今回の結果だけなら、既存Bookfinder
weightをそのまま棚Crop後へ適用する方がよい。

なおBookfinder配布weightのBatchNorm統計には、64要素中2個の非有限値が含まれていた。
通常推論の評価値には影響しなかったが、UltralyticsのEMA checkpoint保存を壊した。
該当2要素を有限値の中央値へ置換すると、補修前後のval値を変えずに追加学習と
checkpoint再読込が可能になった。

## ライセンス上の注意

Ultralytics は YOLO のコードとモデルを AGPL-3.0 または商用ライセンスで提供している。
非公開・商用・組み込み用途では公式の現行条件を確認すること。
Bookfinder repository には今回の確認時点で LICENSE が見当たらず、学習元として複数の
Roboflow Universe dataset が列挙されている。技術検証には使えても、そのまま weight を
配布物へ含める判断は別途必要である。

- Ultralytics OBB documentation: <https://docs.ultralytics.com/tasks/obb/>
- Ultralytics license: <https://www.ultralytics.com/license>
