# 動画をカメラ入力にするE2E

Chromiumの `--use-file-for-fake-video-capture` でY4M動画を `getUserMedia()` に渡す。アプリのカメラ取得、フレーム選別、JPEG生成は差し替えない。動画アップロードとは別のライブスキャン経路を検証する。

## 入力

元動画を残したまま、まず10秒程度の短い区間を変換する（ffmpegが必要）。Y4Mは非圧縮で大きいためリポジトリに追加しない。

```sh
ffmpeg -i '/absolute/path/bookshelf.mov' -t 10 \
  -vf 'scale=960:-2,fps=15' -pix_fmt yuv420p /tmp/bookshelf-camera.y4m
cd tests
CAMERA_VIDEO=/tmp/bookshelf-camera.y4m npm run test:camera
```

ローカルモードは専用port 4182でビルドしたフロントを起動し、APIのみ模擬する。通常確定、確定503→再試行、フレーム各段階503→自動再試行、長い障害→再確定で復旧の4ケース。実カメラストリームの寸法・状態、JPEG送信、送信後のcommit、確定前の送信完了、結果ページ遷移を検証する。最初の送信画像をPlaywrightの添付に保存する。静止画像から作った動画でもこの配線は確認できるが、棚移動・OCR精度の検証にはならない。

## 実バックエンド

```sh
CAMERA_VIDEO=/tmp/bookshelf-camera.y4m CAMERA_LIVE=1 \
  FRONTEND_URL='https://your-test-deployment.example' npm run test:camera
```

このモードではAPIを模擬せず、実際の画像送信・スキャン登録を行う。自動再試行なし、障害注入ケースはskip。最初の画像送信後10秒間スキャンして確定し、最大180秒待って完了画面を確認する。失敗時もジョブを自動削除しない。結果URLを添付する。

完了だけでは認識精度・棚位置・検索反映の正しさは保証しない。指定動画の正解となる本と棚を確定してから、それらのassertionを追加する。棚タグが映らない動画では棚位置の正解確認はできない。スマホ実機の権限画面、ピント、カメラ切替は別途確認する。

## 準備時の確認

指定動画を受け取る前は既存の `frontend/public/dev-scan-shelf.jpg` を3秒・15fps・幅960pxのY4Mへ変換した。実動画・実バックエンドの検証とは区別する。

## 2026-09-27 実行結果

指定動画 `PXL_20260808_054419590.mp4`（93.27秒、HEVC、回転情報付き）を使用。先頭15秒を幅960px・15fpsのY4Mへ変換。変換後は縦960×1706。元動画は変更していない。

- この環境のheadless shellではgetUserMediaがNotSupportedError。`channel: 'chromium'` を指定した通常Chromiumのheadless実行で解決。
- 指定動画でローカルの通常確定・確定503後の再試行の2件が成功（APIは模擬）。ブラウザ側カメラ・画素処理は実処理。
- 起動失敗時、stopCameraがrequest IDを変更することでfinallyの起動中解除が実行されず、開始・ファイル選択ボタンが無効のままになるバグを発見。catchで解除し、権限拒否後の再操作テスト1件成功。修正はローカルのみ。
- 本番1回は失敗。棚タグ4件認識、UI上10フレーム送信後、APIに503およびフレーム初期化の通信失敗が発生し、uploadFailureで確定を阻止した。tags/detectは404も返した（ブラウザ検知は動作）。503の原因はまだ未確定。
- 対象job `7f45987c-4f4d-4ce9-b6b2-870143c013f8` は読み取り時collecting、crop_total=12、ocr_done=6、catalog entries=6。OCRのbooks配列合計72件は精度・一意な蔵書数を意味しない。OCRまで動いたが検索・棚反映の受け入れ完了とはしない。
- 診断後、このテストセッションをcancel APIでcanceledにした。生成済みデータは削除していない。
- 本番traceと画面は `/tmp/booksearch-live-camera-results`、診断時JSONは `/tmp/booksearch-camera-job.json`。一時ファイルで永続保存は保証しない。

次はAPIの503発生原因（同時実行枠・スロットリングなどは仮説）とフレーム保存失敗後の復旧を調べる。取得・PUT・commitはそれぞれ冪等性を確認してから再試行を設計する。まずこの失敗を解消し、同じ動画で完走、その後にタイトル・棚の正解データとの比較を行う。

## 修正後の本番再検証（2026-09-27）

同じ先頭15秒の仮想カメラ入力で成功。Playwright実行47.8秒。job `64264b73-0cff-473f-9b2b-486bbe245a44` はdone、accepted_frames=processed_frames=11、ocr_done=ocr_total=3。catalog entries=5で、タイトルを持つ結果と終了画面を確認。CloudWatchの直近8分のAPI Throttles合計は0（測定時点の到着済みデータ）。前回の保存失敗・確定停止は再発しなかった。

結果の自動照合候補id=727を検索APIで取得可能と確認。ただしその棚候補updated_atは前回試験時刻だったため、今回の試験で新しく棚候補が更新されたとは扱わない。レビュー候補・棚未判定も含まれる。撮影動画の正解データとタイトル/棚を照合する品質検証は引き続き未完了。

検証: Go全テスト、frontend単体46件、仮想カメラ4ケース、配信用フロントの画面回帰60件相当（59成功後、別port/別buildを参照できないホームテスト設定を修正し該当2件成功）。元の作業ツリーでは他作業のBookDetailPage変更により2件失敗したため、その変更を除外した別ディレクトリのビルドを配信・検証した。

CloudFormation changeset: `camera-concurrency-20260927` と `camera-retry-20260927`、いずれもUPDATE_COMPLETE。API/ホームpublisherを更新し、既存の実表紙5冊HTMLも再生成成功。CloudFront invalidation `IC54RNM4MR3IT4S5ZTWD9UHOG5` Completed。古いJSやhome.htmlは削除せず配信。
