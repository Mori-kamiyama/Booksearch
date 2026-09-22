# おすすめを最初のHTMLから表示する

## 表示の契約

ホーム `/` は `home.html` を配信する。5冊のおすすめ、実際の表紙、検索画面のHTMLを事前生成し、表紙をdata URIとして同梱する。ブラウザが画面を開いてからおすすめAPIや外部の表紙配信元を待つ必要はない。HTMLとCSSの転送・描画時間そのものは残るため、無通信で瞬時に表示できるという意味ではない。

Reactは同じデータで既存HTMLへイベントを接続する。初期snapshotがあるホームでは、灰色の骨組み・代替表紙アイコン・API再取得による一覧差し替えを行わない。検索・詳細等への直接アクセスは従来の `index.html` を使う。

## 事前生成と失敗時

`HomePublisherFunction` が毎時05分にUTCのISO週とclient HTMLのhashを確認し、更新が必要な場合だけ生成する。週次manifestがない場合は既存APIの事前生成処理を実行する。候補20冊までから実表紙を取得できた5冊を選ぶ。

取得先は許可済みHTTPSホストに限定。リダイレクトも再検証し、時間・画像サイズを制限する。画像を実decodeしてから小さく整え、HTMLに埋め込む。5冊が揃わない・画像が壊れている・生成に失敗した場合は `home.html` を上書きせず、前回成功分を保持する。ブラウザごとに画像失敗を見せる方式から、公開前に失敗を検出する方式へ変更した。

S3への公開は読み取り時のETagを条件とし、他の実行が先に更新した場合は上書きせず失敗させる。専用の同時実行枠は予約しない。

## ビルドと配信

1. `node scripts/build-home-publisher.mjs` でclientとSSRを同じソースからビルドし、publisher用の成果物を用意する。
2. 新しい `frontend/dist` のassetとSPA用indexをS3へ配信する。旧assetと `home.html` を削除しない。
3. SAM build/deployでpublisherを更新する。sharpはLambdaのLinux/arm64向けにインストールする。
4. publisherを `{"force":true}` で実行し、FunctionErrorなし・status=publishedを確認する。
5. 初回のみCloudFrontのDefaultRootObjectをhome.htmlへ変更する。通常更新はキャッシュ無効化する。

SAMが先にpublisherを更新すると、定期実行が未配信assetを参照するHTMLを公開し得るため、asset配信を先行する。`s3 sync --delete`は禁止。古いHTMLを保持していても、その参照先JSを消すと操作できなくなる。

## 検証

- フロント単体43件成功。
- 従来の画面回帰57件成功。
- 追加2件: JavaScript無効で5表紙が描画され、API要求が発生しないこと、hydration後も表紙が維持され検索・戻る操作ができることを確認。
- publisher単体8件成功。取得失敗・破損画像・タイムアウト・更新競合・JSON埋め込み・週キーを確認。
- 実際の今週先頭5冊の表紙を取得・目視確認。元JPEGは各8〜14KB、合計約50KB。

本番への反映結果・表紙までの表示計測は下記へ追記する。

初回デプロイはAWSアカウントの未予約同時実行数の下限により失敗し、UPDATE_ROLLBACK_COMPLETEとなった。CloudFrontはまだ切り替えておらず、既存ホームを維持した。予約設定を外し、上記の条件付きS3更新で競合を検出する形へ修正した。

macOSからのcross buildでnpmがLinux用libvipsの取得失敗をoptional dependencyとして無視するケースがあった。ビルド時に必要なLinux/arm64 packageの存在を確認し、不足分はlockfileのバージョン・SHA-512で検証して梱包する。実際の画像ライブラリのELF aarch64形式も確認した。
