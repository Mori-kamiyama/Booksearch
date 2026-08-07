# ライブスキャン逐次処理

## 目的

録画停止後にまとめて解析するのではなく、録画中からフレーム単位で
YOLO・OCR・書誌照合を進める。利用者には撮影改善の指示と途中結果を
同じスキャン画面で返し、停止後は明示的な「リザルト」ボタンから結果へ進む。

## 実装したフロー

1. ブラウザは15fpsで軽量な画質評価を行い、最初の良好フレームと、以後の変化した良好フレームを最大1fpsで選ぶ。
   カメラ権限は画面表示時に自動要求せず、中央ボタンのユーザー操作から開始する。これはiOS/Safariやアプリ内ブラウザの自動再生・カメラ制限を避けるためである。
2. フレームをS3へPUTした後、`POST /api/scan/sessions/:id/commit-frame`でアップロード完了を確定する。
3. AWS APIは確定済みフレームを一度だけYOLOキューへ投入する。
4. YOLO、OCR、lookupは`incremental`メッセージとして処理し、途中の`catalog.json`を更新する。
5. フロントはセッションIDのjobを650ms間隔で読み、見つかった本を重複排除してスキャン画面下部へ追加する。
6. 録画停止時は新しい一括解析を始めない。`scan_closed=true`にし、受理フレーム、YOLO、OCRのカウンターが揃った時だけ最終lookupを一度投入する。
7. 停止完了後はカメラ画面に「リザルト」ボタンを出し、同じjob IDの結果画面へ遷移する。

## UIフィードバック

- 端末内のLaplacian分散が低い: `画像がぶれています。1秒止めてください。`
- 白く飽和した低彩度画素が多い: `光が反射しています。角度を変えてください。`
- 3フレーム以上処理してcropが0件: `背表紙が小さいため、少し近づいてください。`
- cropがありOCR未完了: `本を検出しました。タイトルを照合中…`
- 新しいAprilTagの安定検知: 黒いバーを同じ位置へ2.8秒ポップアップし、棚の確認完了と次の棚への移動を案内する。

閾値は現時点では初期値であり、実際の本棚・照明条件で誤案内率を計測して調整する必要がある。特に反射判定は、白い棚そのものを反射と誤認しないかを実機で確認する。

## 完了条件と重複対策

- `committed_frame_keys`で同一フレームの再投入を防ぐ。
- `processed_frame_keys`でSQS再配信時の二重カウントを防ぐ。
- `accepted_frames <= processed_frames`かつ`ocr_total <= ocr_done`になった時だけ`final_lookup_queued`を条件付き更新する。
- 途中lookupはstatusを完了にせず、最終lookupだけが`done`へ遷移させる。

## 検証結果

- frontend Vitest: 15 tests passed。
- frontend production build: passed。
- local Go backend tests: passed。
- AWS Go API build/tests: passed。
- Python worker syntax check: passed。
- `sam validate`: passed。
- 402x874のブラウザ実画面で、開始、1フレーム送信、中央の赤い停止ボタン、停止後のリザルトボタン、結果画面への遷移を確認した。
- ローカルの最終カタログ解析は、既定の`runs/detect/runs/picture_box_detection/yolo11n_quick/weights/best.pt`が存在しないため失敗した。これは今回のUI/API変更ではなくローカルモデル配置の未充足である。

## 未検証

- AWSへのデプロイと、実際のSQS/Lambda/DynamoDB上での逐次catalog更新。
- 実棚でのぶれ、反射、背表紙サイズの閾値。
- 長時間スキャン時のLambda起動数、キュー滞留、Gemini OCRコスト。最大送信を1fpsに抑えたが、計測後に棚単位の最新フレーム優先などを追加検討する。
