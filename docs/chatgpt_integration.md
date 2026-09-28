# ChatGPTからホンノキの蔵書検索

## 現在の運用

AWS版を利用する。MCP: `https://h8wcg8zqd3.execute-api.ap-northeast-1.amazonaws.com/mcp`。
ChatGPT: https://chatgpt.com/plugins/plugin_asdk_app_6aba2d5e92e88191ad3749bc116b7d38 （ホンノキ蔵書検索（AWS）、アイコン付き・接続済み）。
MacのAPI・SQLite・Secure MCP Tunnelへ依存しない。以下のローカル起動・トンネル設定は試作時の記録として残す。旧アプリは削除していないので、利用時にはAWS版を選択する。

## 試作時の状態と設計

独自の会話UIは取り下げ、ChatGPTが検索ツールを使う方式を試す。`integrations/chatgpt/server.py` に読み取り専用MCPサーバーを実装した。ChatGPTへの登録と実際の会話評価は未完了。通常検索のフロントエンドは変更しない。

- `search_books`: 既存APIへ短いキーワード・ページ数範囲・ページ送りを渡す。最大10冊。通常画面の「もしかして」はフロント側なので、このツールでは自動補正しない。ChatGPTが別の語で再検索する。
- `get_book`: 検索結果のIDから書誌、確認済み内容紹介、ページ数、推定棚候補と観測時刻、詳細リンクを返す。
- 内容紹介等は任意の閉じたSQLiteスナップショットから補完。ID・ISBN・書名が一致するときだけ付加し、別のカタログとの誤結合を防ぐ。紹介文最大6000文字。不明値は不明のまま。
- 書籍の登録、スキャン、削除、任意URLへのアクセスは提供しない。モデル呼び出しやAPIキーはサーバー本体には不要。
- 公式Python MCP SDK 1.30.0を個別依存として固定。メインのPython環境へ追加しない。Streamable HTTPとstdioに対応し、HTTPは127.0.0.1のみで待ち受ける。

## 起動

プロジェクトルートで実行（既存API18089、画面18090が起動済みの場合）:

```sh
uv run --no-project --with-requirements integrations/chatgpt/requirements.txt \
  python integrations/chatgpt/server.py \
  --snapshot outputs/discovery-language/library.db
```

MCP URLは `http://127.0.0.1:18092/mcp`。このローカルURLをChatGPTへ貼るだけでは接続できない。別PCから開く詳細リンクには、確認済み公開サイトのURLを `--site-url` で指定する。`--api-url` も変更可能。APIとスナップショットの内容・ID体系を揃える。

## ChatGPTへの接続

最初はSecure MCP Tunnelを候補とする。公開HTTPSへ無認証で出す構成は未実装。

1. 利用するChatGPTアカウントで開発者モードの利用可否を確認。組織アカウントでは管理者設定も必要。
2. PlatformのTunnel設定で対象ワークスペースへ関連づける。Tunnel IDと、クライアントを実行するための資格情報を用意（リポジトリやチャットへ秘密鍵を記載しない）。
3. 公式tunnel-clientを設定し、このローカルMCP URLへ転送する。公式のHTTP設定は `--mcp-server-url`。現時点ではクライアント未導入、Tunnel未作成。
4. ChatGPTの開発用アプリ作成で接続方式Tunnelを選択し、対象を登録する。
5. 「ホンノキでPythonの本を探して」「その本はどの棚？」「ページ数より簡単めがいい」で検証する。検索語と取得した書誌・内容紹介がChatGPTへ渡る。

[公式MCPサーバー構築](https://developers.openai.com/plugins/build/mcp-server)、[Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)。アカウント側の利用可否に応じて公開HTTPS＋適切な認証方式も検討するが、本番公開・ストア申請は今回行っていない。

## 検証

```sh
uv run --no-project --with-requirements integrations/chatgpt/requirements.txt \
  --with pytest python -m pytest integrations/chatgpt/test_server.py -q
```

3テストで入力範囲、ページ条件の転送、読み取り専用宣言、異なる書籍への補完拒否、上流障害を0件扱いしないことを確認。MCPクライアントのinitialize/list_tools/call_toolから実APIへの検索・詳細取得も確認。ChatGPT会話の品質評価とは区別する。

## 接続設定の準備状況

ChatGPTでPro表示とMCPアプリ作成画面、PlatformでPersonal組織とTunnel作成権限を確認。`honno-ki-library` の作成フォームを用意した段階で、まだ作成・資格情報発行・ChatGPT登録はしていない。接続先はローカル18092の読み取り専用MCP。API18089も再起動した。

## トンネル作成済み・キー入力待ち

ユーザー承認後、Personal組織と選択可能だったChatGPTワークスペースに `honno-ki-library` トンネルを作成した。IDは `tunnel_6aba1c379f6c8191b9d7429055259712`。ChatGPT登録フォームへ入力済みだが、作成ボタンは未送信。MCP側のOAuthは使わず、トンネル側のアクセス制御を利用する。

公式tunnel-client v0.0.15を `/tmp/booksearch-tunnel-client/tunnel-client` に展開（恒久インストールではない）。プロファイルは `~/.config/tunnel-client/honno-ki-library.yaml`。実行用キーはまだない。新規認証情報の作成・入力はブラウザ操作ルールに従いユーザーへ引き継ぐ。

キーはチャットに貼らず、プロジェクトルートで以下を実行し、非表示のプロンプトへ貼り付ける:

```sh
uv run --no-project python integrations/chatgpt/save_tunnel_key.py
```

保存先は `~/.config/booksearch/tunnel-runtime.key`（0600）。入力完了後に、キーの内容を表示せず `file:` 参照でmanaged runtimeへ渡す。起動前にAPI18089とMCP18092を確認し、起動後にreadyを確認してからChatGPT側の作成を確定する。

## ChatGPT登録完了

ユーザーが実行用キーを保存後、キーの内容を表示せずfile参照でmanaged runtimeを起動。`process_running=true / healthy=true / ready=true` を確認。ChatGPTで「ホンノキ蔵書検索」の作成・接続を完了した。

プラグイン: https://chatgpt.com/plugins/plugin_asdk_app_6aba211995e4819199f7ddb6056d68e5

現在はMac上でAPI18089・MCP18092・トンネルを動かす試用構成。詳細リンク用のVite18090も起動し、VITE_API_BASE_URLを18089へ向けた。Macの停止・スリープ、プロセス終了で利用できなくなる。常時運用や自動起動は未実装。キーはリポジトリ外に保存し、ログやドキュメントへ記録しない。

状態確認:
```sh
/tmp/booksearch-tunnel-client/tunnel-client runtimes status honno-ki-library
```

ChatGPT実会話でも「Pythonの本を2冊・300ページ以下・内容紹介確認」で『Python』（282ページ、ID472）と『Python1年生』（202ページ、ID179）が紹介文の内容と詳細リンク付きで返った。接続の疎通を確認したもので、推薦品質全体の保証ではない。検証会話: https://chatgpt.com/c/6aba214b-9770-83e8-a912-75958632ba97

## 書影カードと公開リンク

`show_books` を追加し、最終候補のIDから書誌を再取得してMCP AppsのHTMLカードを表示する。`search_books`・`get_book` はデータ取得専用。カードには書影、書名、著者、ページ数、折りたたみの内容紹介、公開アプリの詳細ボタンを配置。既定のsite-urlをCloudFront公開URLへ変更した（本472のID・ISBN一致を本番APIで確認）。

書影URLは既存thumbnailを利用し、既知のHTTPS配信元に限定。Google BooksのHTTP画像はHTTPSへ正規化。画像の読み込み失敗・未収録時は「書影なし」と表示し、別の画像を代用しない。CSPは画像配信元だけ許可し、ブラウザからAPIへ直接問い合わせない。メタデータはtextContentで描画し、リンクはアプリのoriginへ限定。MCP Appsのui/open-linkで外部へ移動する。

Python5テストと実MCP通信でカード資源のMIME/CSP、ツール定義、書影URLと公開リンクを確認。ChatGPT管理画面でツールを更新済み。

ChatGPT実会話で『Python1年生』『Python』の実際の書影2枚とカード表示を目視確認。カードのリンク操作でChatGPTの外部リンク確認に正しい公開URLが渡ることを確認し、公開アプリ側の本472の詳細表示も別途確認した。検証会話: https://chatgpt.com/c/6aba2422-f540-83e8-a145-f8e23c65f181

検証時のChatGPT開発者設定はCSPオフ表示だったため、宣言内容のテストとブラウザでのCSP強制適用は区別する。設定は変更していない。

### 会話に馴染む表示

OpenAIの[UI指針](https://developers.openai.com/plugins/concepts/ui-guidelines)を基に、独自の緑背景・重複するアプリ見出し・全面CTAを撤去。透明背景、ニュートラルな文字色、システムフォントを使い、書影を中心にした2列表示と420px以下の小型書影付きリストに変更した。追加フレームワークは導入せず、既存のMCP Apps資源を更新する。

内容紹介は操作時のみ表示し、長文は240文字の抜粋と明示する。本文側は書誌の反復より選んだ理由・違いを伝える方針。カードの高さはbodyの実測値を切り上げて通知し、端数による内部スクロールを避ける。UI資源URIはキャッシュを更新するためv3へ変更。

v3をChatGPTで実表示し、内容紹介の開閉と390px幅のリスト表示を確認。既存Python5テスト、JavaScript構文検査、diffチェックも通過。

### 推薦理由と用途の確認

v4では「本の場所を知る」をアプリの検索・スキャン画面と同じ緑（#087f5b）にし、本の詳細ページから位置情報を確認する導線にした。棚の推定情報を確定位置とは扱わない。

`show_books.recommendations`に本IDと200文字以内の理由を受け付ける。表示対象外ID・重複・空欄を拒否し、理由はtextContentで描画する。理由はChatGPTが目的と取得済み紹介文を結び付けて作る説明で、サーバーが正しさを自動判定するものではない。入力検証を含むPython6テストが通過。

曖昧なおすすめ依頼では用途を一問だけ確認する方針をMCP instructionsと検索ツール説明へ追加。「英語」の学習・読書・デザイン資料という用途差を例にし、自由回答、方針変更、おまかせを受け入れる。書名指定・場所確認・目的が明確な依頼には質問を強制しない。毎回の固定質問フローは作らない。

実会話で「英語の本をおすすめして」→用途の一問→「英語学習用。簡単めのものを2冊、まず見たい」→追加質問なしの2冊・推薦理由・緑の場所ボタンを確認した。検証会話: https://chatgpt.com/c/6aba26dd-2244-83ee-a1d9-3a9e088e26ed 。一方で、短いページ数を理由に挙げる傾向と本文の重複は残った。難易度の適合性や推薦理由の品質を、この疎通確認だけで保証しない。

### アイコン

既存leaf.svgを白い正方形へ配置し、frontend/publicにfavicon.svg、favicon.ico（16/32/48px）、apple-touch-icon.png（180px）、app-icon.png（512px）を追加。index.htmlへ設定し、ビルドとローカル配信を確認。本番Webへのデプロイは未実施。

MCP initializeのserverInfo.iconsにも512px PNGを含め、実通信で確認した。ただしChatGPTのプラグイン一覧画像には反映されていない。管理画面にアイコン単独の編集欄がなく、ZIP書き出しも取得できず、パッケージのlogo/composerIcon更新は未完了。元パッケージを取得せず接続設定を推測して上書きしない。

ブラウザのメニュークリックが別項目を作動させ、一度アンインストールされた。同一プラグインを再インストールし、接続済みと低リスクツール許可の復旧を確認。以後メニューは上下キーでフォーカス対象を確認してから実行する。

追記：ChatGPTの「追加 → MCPアプリを作成」には「アイコンを選択」がある。PNG・256px以上推奨・10KB以下。既存512px PNGは18,060 bytesなので、256pxのchatgpt-icon.png（8,361 bytes）を追加し、作成フォームで選択できることを確認した。これは新規作成フォームであり、既存アプリのアイコン変更を完了したことにはならない。新規作成は未送信。

## AWS移行（2026-09-28）

MCPを専用Lambda + HTTP APIへ移す。Streamable HTTPはstateless/json_responseを使い、呼び出しごとにMCPのsession managerを作る（同じインスタンスのlifespanは再利用できない）。API Gatewayは5 req/s・burst 10、Lambdaは25秒、ログは14日。読み取り専用の公開蔵書APIだけへアクセスし、AWSデータ操作権限やモデルの課金キーを持たない。公開済み蔵書と同じ範囲を認証なしで提供する。ディレクトリへの申請は別作業。

MCPは本番APIから内容紹介・出典・ページ数・難易度を取得する。ローカルsnapshotは開発用に残すがAWSでは指定しない。本番Lambdaから取得した既存DBを基底に、全4,202冊のID・書名・ISBNの完全一致を確認して、検証済みメタデータ・検索索引・関連本テーブルだけを更新する。既存の蔵書・書影・棚テーブルは維持する。内容紹介はfetch_status=matchedのみ返す。

ビルド: `uv run --no-project python integrations/chatgpt/build_aws.py --output /tmp/booksearch-mcp-release.zip`。zipをS3へ配置し`aws-template.yaml`のArtifactBucket/ArtifactKeyを指定して`booksearch-mcp`スタックをデプロイする。出力McpUrlをChatGPTのサーバーURL方式で登録する。Mac上のトンネルは不要になる。通知メール設定は保留のまま、Lambda Errorsアラームを用意する。

### AWS移行の検証結果

PR #10（パンくず）・#11（検索とMCP）をmasterへマージ。実装のmerge commitは`5155530d`。`booksearch-mcp`はCREATE_COMPLETE、既存`booksearch`はUPDATE_COMPLETE。本番スタックの元テンプレートのコード参照だけを変更し、展開後の差分がApiFunction/HomePublisherFunctionのCodeのみであることを確認して反映した。スキャン処理の構成は維持した。

APIとMCPの実通信で、本472の282ページ・出典google_books・641文字の紹介文、2冊の書影、HTML資源を確認。「パイソン」の検索は185冊。本番4,202冊のうち3,970冊にmatchedメタデータを収録。紹介文やページ数が欠ける本は不明のまま返す。

ホームはweek=2026-40、5冊、clientIndexHash=`b778432bd7592c877a623a437ff0d6bd872bd29d89f903ef1a9d36ec3ffddd86`で生成成功。CloudFront invalidation `IATOVZ0W7LHOI8XXSBA1MXG4O3`はCompleted。配信JSは`index-CmtBhNVP.js`。favicon.svgと新しいgenre索引もHTTP200を確認。

検証: MCP7件、Go API/backend全件、フロント53件、検索Python52件、検索UI回帰16件、home publisher8件が成功。本番smokeは10件成功・2件が旧searchboxロール参照で失敗。現行の候補付きcomboboxに合わせてテストを修正し、Chromium/WebKitの2件を再実行して成功した。

ChatGPT実会話: https://chatgpt.com/c/6aba2db6-dd98-83ee-b14f-f3b3168df8dc 。用途の一問→入門用→本298/1797の書影・紹介・理由・場所リンクを表示した。これは接続と表示の検証であり、推薦精度の包括評価ではない。ChatGPTの開発者CSPは引き続きオフ表示で、強制有効時の検証は未完了。
