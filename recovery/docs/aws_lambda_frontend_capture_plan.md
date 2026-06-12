# フロント撮影と AWS 分割方針メモ

このメモは、Booksearch を AWS に載せ、最終的に Lambda 化や worker 分割を検討するときの方向性をまとめる。

## 方針

サーバへ動画を送るのではなく、フロントエンド側で「撮るべき瞬間」を判定し、良い静止画だけを送る。

これにより、バックエンド側から動画フレーム抽出、長時間処理、大きな一時ファイル、重いリトライをできるだけ外す。バックエンドは 1 枚の画像に対して YOLO、OCR、検索、catalog 生成を行うだけに近づける。

## フロントエンド側でやること

カメラ映像を見ながら、次の条件を満たしたタイミングで自動撮影する。

- AprilTag または ArUco などのマーカーが見えている
- 新しい棚、または前回とは十分違う位置を見ている
- 手ぶれが少ない
- 暗すぎない、白飛びしすぎていない
- マーカーや棚が小さすぎず、十分近い
- 数フレーム連続で安定している

最初からフロントで YOLO を回す必要はない。MVP では手動撮影に blur/明るさチェックを加えるだけでもよい。次に AprilTag/ArUco 検出で自動撮影し、必要ならさらに軽量な棚検出や YOLO を検討する。

## AprilTag と ArUco

AprilTag は棚割り当てと相性がよい。既存実装にも AprilTag から shelf_id を付ける考え方が入っているため、サーバ側の棚割り当てとつなぎやすい。

一方で、ブラウザ上での検出実装や速度によっては ArUco も候補になる。ArUco は OpenCV 系の実装と相性がよく、WebAssembly/OpenCV.js で扱いやすい可能性がある。マーカーの種類は最終固定せず、フロントで安定して検出できるものを選ぶ。

候補:

- AprilTag: 棚IDとの対応付けに向く。既存の棚割り当て設計と近い。
- ArUco: OpenCV 系で扱いやすい可能性がある。ブラウザ実装の選択肢として有力。
- QR/独自マーカー: 実装は楽だが、姿勢や向き、棚交点との関係を使いにくい可能性がある。

## バックエンド側の基本処理

フロントから 1 枚の画像を受け取り、次を実行する。

```text
画像入力
-> YOLO で箱/棚区画検出
-> crop/preview 生成
-> crop 品質判定
-> readable crop だけ Gemini OCR
-> ローカル図書 DB / known_books 照合
-> catalog.json 生成
```

この形なら、最初は worker を細かく分けすぎなくてよい。1 枚処理であれば、YOLO、OCR、検索、catalog 生成を 1 つの Python worker にまとめても運用しやすい。

## AWS に置くときの最初の分割

最初は以下の分割が現実的。

```text
Frontend
  -> 画像を撮影
  -> S3 へアップロード、または API 経由で送信

Go API Lambda
  -> job 作成
  -> S3 key を保存
  -> SQS に job 投入
  -> job 状態取得 API

Python Worker
  -> S3 から画像を読む
  -> YOLO -> crop -> OCR -> 検索 -> catalog
  -> 結果を S3 に保存
  -> job 状態を DynamoDB に更新
```

永続データ:

- S3: 入力画像、crop、preview、catalog.json
- DynamoDB: job 状態、進捗、エラー、結果の S3 key
- SQS: 非同期 worker キュー
- Secrets Manager または Parameter Store: Gemini API key など

## どこまで Lambda 化するか

Lambda に向く処理:

- Go API
- job 作成、状態取得
- S3 presigned URL 発行
- catalog 集約
- DB/known_books 照合
- crop ごとの OCR 呼び出し

Lambda で注意が必要な処理:

- YOLO 推論
- OpenCV での大量 crop
- 動画フレーム抽出
- 大きいモデルファイルを含む worker

動画をサーバに送らず、フロントで良い静止画だけ送る方針なら、YOLO worker も Lambda に載せられる可能性は上がる。ただしモデルサイズ、cold start、一時ストレージ、処理時間によっては ECS Fargate や AWS Batch に逃がせるようにしておく。

## 段階的な進め方

1. フロントは手動撮影 + blur/明るさチェックから始める。
2. バックエンドは 1 枚画像を処理する worker として安定させる。
3. フロントに AprilTag/ArUco 検出を入れ、自動撮影条件を作る。
4. AWS では Go API、S3、DynamoDB、SQS、Python worker に分ける。
5. OCR の並列化が必要になったら、crop ごとの OCR job に分ける。
6. YOLO worker が Lambda で重ければ、そこだけ ECS Fargate / AWS Batch に逃がす。

## 後で決めること

- マーカーは AprilTag と ArUco のどちらを採用するか
- フロント検出を JS 実装にするか、OpenCV.js/WASM にするか
- 自動撮影の閾値
- YOLO worker を Lambda に置くか、Fargate/Batch に置くか
- OCR を worker 内で逐次実行するか、crop 単位で並列化するか
- catalog.json の正式スキーマ

## 判断

現時点では、All Go 化よりも、フロントで入力を軽くして、バックエンドを非同期 worker 化するほうがよい。サーバに動画を送らず、良い静止画だけ送る設計にすると、Lambda 化や AWS 分割がかなりやりやすくなる。
