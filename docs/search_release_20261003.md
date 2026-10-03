# 2026-10-03 検索更新の本番反映

## 反映内容

GitHubの `origin/master` を取得し、作業ブランチへ取り込む未反映コミットがないことを確認した。[PR #15](https://github.com/Mori-kamiyama/Booksearch/pull/15)には、日本語検索補正・無効状態の意味検索API/UIと、既存ブランチの棚スキャン・検索導線の変更を含める。本番アプリケーションのビルド元は `95905e80`。

本番はAWSの[Honnoki](https://d2uel8nex1m4w7.cloudfront.net)。フロントエンドの静的ファイル、`booksearch-api`、`booksearch-home-publisher` を更新し、ホームを強制再生成してCloudFrontの無効化完了を確認した。蔵書DBとブラウザ索引は4,202冊のID・書名が一致する。

優先OCRはCloudFormation外で管理されているため、全体のSAMデプロイは行っていない。OCR通常・優先、lookup dispatcher、YOLO、lookupの本番コードは現行ソースと一致しており、更新は不要だった。全体SAMビルドの試行は中止し、不要なYOLO再ビルドでPillowのJPEG依存エラーが出たが、配信するAPI・ホーム生成の個別ビルドは成功した。

意味検索の索引は生成していない。環境変数とIAMは変更せず、本番の `/api/books/semantic/status` は `{"available":false}`。今回のリリースで蔵書のモデル送信・ベクトル生成課金は発生させていない。

## 確認と限界

- フロントエンド単体67件、ホーム生成8件、ローカル/AWS Goテスト、Python AWS/意味検索80件が成功。
- UI回帰89件のうち全体実行88件が成功。残りは未確定OCRの表示名に関する古い期待値を修正し、該当テストの再実行が成功。
- 本番の読み取り専用E2EはChromiumとmobile Safariで計20件成功。`デザiン → デザイン`、元の語での再検索、複数語・ジャンル維持、短いかな候補の選択、詳細・戻る、ホームを確認。
- S3のindexは配信用ビルドと一致し、ホームは新しいJSと5冊の埋め込み画像を参照する。LambdaはActive/更新成功、CloudFront無効化はCompleted。
- 更新直後のCloudWatch確認では対象2関数のErrors・Throttlesは0。ただし集計遅延があり、長時間の安定稼働を示すものではない。

## 復旧用データ

更新前のLambda ZIP・設定、S3のindex/home、配信ZIP、ホーム生成結果は、Git管理外の `outputs/deploy-20261003-search/` に保存した。旧ハッシュの静的資産は削除していない。

復旧時は保存した `booksearch-api-before.zip` と `booksearch-home-publisher-before.zip` で各関数を戻し、`index.html.before` と `home.html.before` をS3の同名オブジェクトへ戻してCloudFrontを無効化する。ホーム生成の次回実行も旧コードになることを確認する。今回の反映では環境変数・IAM・DBを変更していない。
