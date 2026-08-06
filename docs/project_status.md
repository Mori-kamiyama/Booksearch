# Project Status

## 2026-06-05

- DB confidence update was tested with the full/hidden book image pair.
- Observed behavior matched the expected shape: 43 total books, 10 hidden, 32 books seen twice and 10 seen once in the confidence test run.
- AWS Lambda/DynamoDB integration and a small frontend for shelf candidates were implemented before cleanup.
- Generated artifacts and raw local outputs were removed from the working tree.
- Git history rewrite attempt accidentally removed tracked files; recovery was performed from local Claude/Codex logs and file-history backups.

## 2026-06-12

- Additional source, AWS, frontend, E2E, model, and historical files were salvaged without overwriting the recovery directory.
- Verification passed for the backend Go tests, AWS Go API build/tests, Python syntax checks, and frontend production build.
- The recovered YOLO model loaded successfully and detected four `box` instances in `A4 - 9.png`.
- Embedded credentials in recovered historical scripts were removed before the recovery checkpoint was pushed to GitHub.
- Production Playwright E2E passed 22/22 against CloudFront and API Gateway:
  desktop Chromium and mobile Safari UI, search, shelf candidates, SPA routing,
  API health/search, legacy scan upload, and presigned S3 upload/start flow.

## 2026-06-19

- Fixed a production job stuck in `ocr_pending`: YOLO counted all crops in
  `crop_total`, while OCR only received readable crops. OCR now compares
  `ocr_done` with `ocr_total` or the job diagnostics `readable_count`.
- Updated Lookup Lambda to use the restored bundled `library.db` and open it as
  an immutable read-only SQLite database.
- Hardened the API Lambda request parser so job/status routes survive API
  Gateway payload-shape differences.
- Improved the job page so polling failures are visible instead of leaving the
  last status on screen forever.
- Reprocessed job `465791b6-2ece-48af-accd-06974db2e6a5`; it now finishes as
  `done` with 3 detected boxes, 17 OCR titles, and 16 DB matches.
- Redeployed API, OCR, Lookup, frontend S3/CloudFront, then reran production
  Playwright E2E: 22/22 passed.
- Deployed the current YOLO worker image to AWS so edge-touching wide/tall crop
  rejection (`edge_wide` / `edge_tall`) is active in production. The image was
  pushed as `edge-aspect-filter-20260619` and Lambda now resolves to digest
  `sha256:3ac833e0525afc06b20908897c87e7d321b7ef7398a38f54fb5fe60269a338ad`.
- Pinned YOLO worker `scipy==1.11.4` so the arm64 Lambda image uses a wheel
  instead of trying to compile the latest SciPy with the Lambda base GCC.
- Fixed the remaining production display issue where unreadable crops were
  correctly skipped by YOLO/OCR but still included in the Lookup catalog. Lookup
  now omits `skipped_low_quality` / `quality.readable=false` crops from
  `catalog.entries`; job `ccdd375b-76c5-4e72-afff-502b812eaed9` was reprocessed
  and now shows 1 readable box instead of 3 total detected boxes.
- Clarified the frontend location workflow: search results now always show a
  location row, using the highest-confidence shelf candidate when available and
  showing `場所未登録` when no shelf observation has been learned yet. The old
  `棚候補` navigation label was renamed to `本の場所`.
- Added a growing Google Books cover cache to the AWS API. Search/book detail
  responses now backfill missing `thumbnail` / `info_link` values by querying
  Google Books for up to 5 missing covers per request and storing results in
  `s3://booksearch-277707097118-ap-northeast-1/cache/google_book_covers.json`.
  Missing-cover results are cached, while transient Google Books 429/5xx errors
  are retried after 1 hour instead of being treated as long-term misses.

## 2026-06-29

- Improved the search-result location UI from a mock-ish row into a reviewable
  panel: top shelf candidate, confidence, observation count, alternate shelves,
  and local confirm/reject/correct actions.
- Added local backend support for `book_shelf_candidates` so search and book
  detail responses can attach `shelf_candidates` in the same shape as production.
- Production scan job `2aa5cf53-228a-4483-9016-6fc8252bbce3` previously read
  books but added `0` shelf observations because AprilTag votes from nearby tags
  conflicted. The hypothesis was that conflict should not discard the crop when
  the nearest tag still provides a useful shelf.
- Updated the YOLO worker so conflicting shelf votes assign the nearest voted
  shelf with diagnostic reason `nearest_of_conflicting_votes` instead of dropping
  the shelf assignment.
- Deployed the frontend to `https://d2uel8nex1m4w7.cloudfront.net/` and deployed
  the AWS stack update, including shelf observation/candidate DynamoDB tables and
  the new YOLO image.
- Verified with production scan job `d75d6990-7e55-47bf-92a9-3c9859c27b63`:
  `status=done`, `crop_total=4`, `ocr_done=2`, and
  `shelf_observations_added=15`.
- Verified production API visibility after the scan:
  `/api/shelf-candidates` returns the new candidates, and searches such as
  `詳説デザインマネジメント` include `shelf_candidates` with `shelf-B-02`.
- Production Playwright E2E now includes explicit checks that the scanned book
  appears with `この本はここにありそう` in search results and on the `本の場所`
  page; desktop Chromium and mobile Safari passed 26/26.

## 2026-07-03

- Implemented the Phase 1 UI slice from `docs/ui_phase1_playbook.md`: typed
  frontend API helpers, layout-backed shelf label utilities, common loading /
  empty / error states, bottom tabs, `/books/:id`, `/map`, and `/map/:shelfId`.
- General search now sends users to a detail page where the top shelf candidate
  is shown as a Japanese label plus a highlighted shelf grid, not as a raw
  shelf ID.
- Verified locally with `識別・予測・異常検知`, which resolves to
  `base-01-c02-r04` in the API and displays as
  `入口側から1台目 2列目 下から4段目` in the UI.
- Hypothesis: the map-first browse flow is good enough for Phase 1, but the
  density view will need better grouping or filtering once every shelf has many
  candidates; otherwise a single crowded cell can dominate the experience.

## 2026-07-10

- Started the local Phase 1 acceptance pass with the Go backend and Vite
  frontend running together.
- Verified the real user path: search for `識別` -> book detail -> Japanese
  shelf label and highlighted shelf map. Also verified map -> shelf cell ->
  `/map/base-01-c02-r04` -> shelf detail.
- Verified the book detail at 375px width: document width stayed at 375px with
  no horizontal overflow, and the human-readable shelf label remained visible.
- Local Playwright E2E passed 24 tests across desktop Chromium and mobile
  Safari. Two production-only scan API tests are skipped for each browser when
  `API_BASE` points at localhost; the local Phase 1 backend does not expose the
  presigned S3 endpoints. Before this adjustment those four checks failed with
  404/400, while all UI and read-only API checks passed.
- Added an E2E check for map-to-shelf-detail navigation. The remaining Phase 1
  work is visual review and deciding whether to commit/PR the current UI slice;
  no new backend work was needed.
- Adjusted the physical left/right convention for `base-01` through `base-03`.
  The layout source now marks those units as mirrored, and the frontend, tag
  generator, and tag-placement visualization all read the same flag. This
  keeps displayed shelf IDs adjacent to the tags that identify them.
- The physical tags on those units were pasted from a horizontally mirrored
  initial diagram. Their IDs and shelf quadrants stay anchored to that initial
  diagram, while only their physical intersections are mirrored; e.g. tag 29
  is the upper-left tag on the reflected first unit.
- The Go application now loads `data/apriltag_library_map.json` by default,
  so both scan jobs and `POST /api/tags/detect` use the generated physical tag
  map without requiring an `--apriltag-map` startup flag.
- Re-ran AprilTag detection and shelf assignment only (without repeating OCR)
  for `outputs/book_catalog_data_260702/catalog.json`. Replaced the SQLite
  shelf candidates with 455 accepted observations / 370 unique book-shelf
  pairs from the reassigned catalog, after saving
  `outputs/library/library.before_apriltag_reassign_20260710.db` as a backup.
- Added live AprilTag scanning to `/scan`: while the camera is active, a
  downscaled frame is sent to `POST /api/tags/detect` every 1.6 seconds with
  overlap protection, detected IDs and status shown below the preview, and
  the existing stop-and-upload video flow remains available.

## 2026-07-24

- 推薦機能の初期実験として、Google Booksから内容紹介・カテゴリ等を保守的に収集する `scripts/enrich_book_metadata.py` と、依存なしの日本語文字n-gram + BM25で関連本を事前計算する `scripts/build_bm25_recommendations.py` を追加した。結果は `book_metadata` / `book_recommendations` テーブルに保存する。現時点では貸出・個人の閲覧履歴を使用しない。
- `backend/dev.db` は `books` テーブルを持たない空DBであることを確認したため、実験実行時は実データを持つカタログSQLite DBを明示指定する。ネットワークを使う前に `--limit 10 --dry-run` で照合率を確認する方針とした。


- ヘッダー (`SiteHeader`) を `sticky` から `fixed` 配置に変更し、`bg-white/60` および `backdrop-blur-xl` を指定。これにより、背後のイラストやコンテンツが綺麗に回り込み、ヘッダーの美しい「透明・すりガラス効果（backdrop-blur）」が効くように調整。
- ヘッダーの `fixed` 化に伴い、通常ページ (`!usesFigmaLayout`) でメインコンテンツがヘッダーの下に隠れてしまわないよう、`AppShell` (`App.tsx`) 内の `main` 要素に上部パディング (`pt-[72px] md:pt-[88px]`) を追加。
- 検索トップページ (`SearchPage.tsx`) について、高さを `h-svh` (および `md:min-h-screen`) に変更して画面最上部から全画面描画されるように調整。また、ロゴや検索バーなどのコンテンツがヘッダーに被らないよう、スマホ表示時の内側コンテナのパディングを `pt-[162px]`、PC表示時のマージントップを `md:mt-[168px]` に最適化。
- トップページ (`isHome === true`) のときに `SiteHeader` を完全に透明化（背景、ブラー、境界線を削除。クラス切り替え）する処理を実装。
- トップページ (`isHome`) およびスキャンフロー (`isScanFlow`) 以外のすべてのページで、自動的にヘッダー分の高さ（スマホ: `72px`, PC: `88px`）のパディングを `main` 要素に付与するよう `AppShell` (`App.tsx`) 内の条件判定 (`needsHeaderPadding`) と `className` を修正。詳細ページ等でヘッダーがコンテンツに被る問題を完全に解消。
- トップページ (`SearchPage`) で縦スクロールが一切発生しないように、画面の縦幅にフィットさせて収める修正を実施：
  - `AppShell` (`App.tsx`) の最外コンテナにて、トップページ (`isHome === true`) のときに高さを画面の高さ (`h-svh`) に固定し、はみ出たスクロールを禁止 (`overflow-hidden`) するように変更。
  - `SearchPage.tsx` 内に残っていた、直書きのロゴ部分 (`<BrandMark />`) を完全に削除。
  - 画面内にすべての要素（検索窓、おすすめ本カルーセル、スキャンボタン）がちょうど良く収まるよう、コンテンツを包む `div` のスマホ側上部パディングを `pt-[162px]` から `pt-[110px]` に、PC用余白を `md:gap-[92px]` から `md:gap-16` にコンパクト化。また、メインコンテンツの上部マージンも `md:mt-[168px]` から `md:mt-[130px]` に引き上げてバランスを調整。これにより、スマホ・PC両方で縦スクロールが一切発生しない完全なフィット感を実現。
- 「図書室マップ」や「管理用ページ」などの通常（非Figma）レイアウトページ (`!usesFigmaLayout`) において、ヘッダー直下にページタイトルやコンテンツが隙間なく配置されて窮屈だった表示を改善するため、`App.tsx` の padding-top 指定を `usesFigmaLayout` の有無で条件分岐。通常レイアウト時はヘッダーの高さに加え、スマホで24px、PCで32pxの余白を追加した `pt-[96px] md:pt-[120px]` を適用し、十分な上部マージンを確保。
- 「索引」ページ (`IndexPage.tsx`) のPC表示において、`md:pt-0` 指定により「索引」タイトルがヘッダー下端に密着していた不具合を修正。他のFigmaレイアウトページ（検索結果や本詳細ページ等）と一貫した余白を持たせるため、PC表示時の上部パディングを `md:pt-[54px]` に最適化。
- 「今週のおすすめ」のランダム表示の一時的実装：
  - バックエンド (`books.go` の `FeaturedBooks`) において、書影画像 (`book_covers.thumbnail`) を持つ本に制限する条件（`WHERE`句および `JOIN`）を外し、`LEFT JOIN` を用いてデータベース内の全4,202件の本から完全にランダムに選択するように一時変更。
  - フロントエンド (`SearchPage.tsx` および `BookDetailPage.tsx`) において、開発環境 (`import.meta.env.DEV`) のときに Figma UI 再現用の特定の3冊のダミー本に常にフォールバックさせていたロジック（`featuredBooksForDisplay`, `recommendationsForDisplay`）を一時的にバイパスし、APIから返ってきた本物のランダムな本を表示するように修正（DBが空など、取得結果が1件もない場合のみダミー本にフォールバック）。
  - トップページで `BottomTabs` が表示されない原因だった `!usesFigmaLayout` 条件を `!isScanFlow` に変更し、スキャンフロー以外では共通フッター/下部タブを常時表示する方針に統一。トップページの縦スクロール原因だった `SearchPage` の `min-h-[620px]`、`md:h-auto`、`md:overflow-y-visible` を外し、`h-svh max-h-svh overflow-hidden` で1画面に固定。
  - `BookDetailPage.tsx` を商品詳細ページ風のレイアウトに刷新。表紙を左、タイトル・著者・出版社・出版日・分類・登録番号・ISBN・読了時間・外部詳細リンク・棚位置カードを右に配置し、下部にAIカルテ風の要約セクションとおすすめ本を並べる構成へ変更。
  - 詳細ページから一旦 `ライブラリに追加` CTA を削除し、`本の詳細を見る` は API の `info_link` を優先、未設定時は ISBN/タイトルで Google Books 検索へフォールバックするように修正。色味やカード表現もアプリ既存の `primary`/`primary-soft`/`line` ベースに寄せた。
  - 詳細ページの構成を `本の詳細情報 → MAP → AIカルテ → おすすめ` の順番に整理。既存の棚位置カード/ミニマップをAIカルテ位置へ移し、将来差し替える地図UIのための独立した `MapSection` にした。
  - 詳細ページに一般的なカードUIを足しすぎたため、トップ/索引/検索結果のFigma系UIに合わせて再調整。表紙・MAP・AIカルテの囲み枠、影、背景面を取り除き、`402px`（モバイル）/`886px`（PC）の既存レイアウト幅、フラットな見出し・余白、従来の棒状フロアマップに戻した。
- 書影未登録の本は、ISBN があれば Google Books の既存書影エンドポイントを API 応答の `thumbnail` として返すフォールバックに整理した。DB への一括取得・API キー・キャッシュを必要としない。
- 索引は `/api/shelf-candidates` を使用し、検索・詳細の `Book` レスポンスを使用しないため、当初は書影を受け取れていなかった。候補レスポンスにも `thumbnail` を追加し、索引では書影、棚写真、Figma の代替画像の順に表示するよう修正した。
- 索引の書影は固定の `93×131px`・切り抜き表示をやめ、画像の縦横比を維持する可変高さの表示に変更した。幅は最大 93px、高さは最大 160px に制限する。
- 同じ固定・切り抜き表示が残っていた共通書籍カード、書籍詳細、おすすめ、検索結果、OCR 結果にも、最大サイズを保った `object-contain` の書影表示を適用した。プレースホルダーの固定サイズは維持する。
- 可変高の書影でも一覧の統一感を保つため、各カードに最大表示高の画像ステージを設けて底面へ揃えた。詳細ページは画像ステージと PC グリッドの左列を広げ、モバイル最大 `240×350px`、PC 最大 `300×405px` に拡大した。
- 詳細の Google Books サムネイルは元の低解像度の寸法で描画されていたため、画像ステージの高さまで拡大するよう `h-full w-auto` を指定した。横幅は引き続きステージ内に制限する。
- モバイル用のボトムバー (`BottomTabs`) の撤去と、共通フッター (`SiteFooter`) の新規実装：
  - モバイル表示時において画面下部に常駐していた `BottomTabs`（さがす、マップ、スキャン）を、UI的な煩雑さを解消するため完全に削除しました。
  - 代わりに、PCおよびモバイル共通で利用できる、シンプルでスタイリッシュなフッター (`SiteFooter`) を新規に実装しました。
  - フッターには「さがす」「索引」「スキャン」「図書室マップ」への動線と、ブランドロゴ、コピーライト情報を配置しています。
  - `AppShell` に Flexbox を用いた Sticky Footer 設計を適用し、コンテンツが少ないページでもフッターが常に画面最下部に表示されるように最適化しました。
  - カメラをフル画面で表示するスキャン画面 (`/scan`) や、1画面に完全に収まるように設計された検索トップページ（`isHome`）では、レイアウトを崩さないようにフッターの描画を行わないよう自動判定を組み込みました。
  - 不要となったボトムバー用の余白パディング（`pb-20 md:pb-0`）を最外コンテナから削除しました。
- 索引と書籍詳細で利用する棒状のフロアマップを `LibraryMap` として共通化した。索引は詳細ページと同じ物理的なマップ形状で棚ユニットを選べるようになった。
- `LibraryMap` は棚IDからレイアウト上のユニットを解決し、該当位置をハイライトする。書籍詳細では、対象棚のバーまたは壁側棚に応じて「ここ！」の吹き出し位置も連動して変わる。
- フロアマップの棚番号は右から左へ `1 → 4` とするよう対応表を訂正した。上段の左側2本は将来設置する棚 `5`・`6` のため、現時点では選択対象にしていない。
- フロアマップ下部の小さな四角は、`base-*` とは別の `side-*` 独立棚として扱うよう訂正した。右から `side-01`〜`side-04` に対応し、選択時は `library_layout.json` の横3×縦7グリッドを表示する。索引では全体マップの棚選択と細かい区画選択を分離し、PC表示は横幅を広げてマップを大きく、書影一覧を4列・おすすめ本に近いサイズへ調整した。マップと区画グリッドは視線の流れを優先して縦並びに戻した。

### スキャン画面: 実カメラプレビューと録画状態UI

- `ScanPage.tsx` の開発用静止画 (`dev-scan-shelf.jpg`) フォールバックを削除。カメラ許可待ち・起動中も、モックではなく実際の `<video>` プレビュー領域を使用するようにした。
- カメラには背面優先、最大 1920×1080 の理想解像度、音声なしを要求する制約を指定。`autoPlay`/`playsInline` と `loadeddata` による準備完了通知を追加した。
- ライブスキャン開始ボタンは、開始前の白い丸から、録画中の赤い角丸四角へ 200ms で遷移する。カメラ未起動時は誤ってスキャン開始できないよう無効化する。
- 棚マップ取得によりカメラ起動用の React effect が再実行され、動画リソースが中断される問題を修正。棚マップは ref から参照し、ライブプレビューの開始・停止を不要に繰り返さない。
- スキャン画面の幅制限と外側余白を外し、カメラプレビューを画面幅いっぱいにした。戻るボタンも斜め矢印ではなく左矢印に統一した。
- 開発環境の `React.StrictMode` が effect を開始直後に cleanup することで `video.play()` が中断され、古い非同期要求が有効なストリームを停止し得る競合を解消した。起動を次のイベントループまで遅延してダミー effect を破棄し、要求IDで古いカメラ要求を無効化している。
- 認識結果のステータスカードに残っていた `overflow-hidden` と `line-clamp-1` を撤去。カード幅を画面幅に追従させ、高さを認識文に応じて拡張することで長い認識結果も省略せず表示する。
- ヘッダー右上のハンバーガーメニューは、デスクトップ時のみ `md:w-[31px]` が適用されてホバー背景が円形から崩れていたため削除し、常に `size-11` の正方形クリック領域で `rounded-full` が正しく円になるよう修正した。
- ハンバーガーボタンとドロワー内の閉じるボタンの位置ずれを解消するため、ドロワー側ヘッダーの高さをデスクトップで `88px` に揃え、右余白も `32px` 相当に合わせた。
- 索引ページ (`frontend/src/pages/IndexPage.tsx`) の表示切替トグル (`Map` / `リスト`) を少しだけ拡大し、`h-[28px]`・`w-[156px]`・`text-sm`・`leading-[26px]` に調整して操作感と視認性を改善した。

## 2026-08-06 書影拡充

- 書影付き蔵書からランダムに5冊返すAWS API `/api/books/featured` を追加し、書籍IDルートへの誤判定を修正した。
- `outputs/library/library.db` の未登録3,141冊をISBNで走査し、Open Libraryから231冊、APIキー付きGoogle Booksから595冊を追加した。書影付きは1,061冊から1,887冊へ増加し、このDBスナップショットを`booksearch-api` Lambdaへ反映した。
- 再開可能な `scripts/fetch_missing_covers.py` を追加した。OpenBD、Open Library、Google Booksの順で未取得分のみ処理し、プロバイダ別の試行結果を`cover_fetch_attempts`へ保存する。
- Google BooksはAPIキー未設定時のクォータが0でHTTP 429になる。有効な`GOOGLE_BOOKS_API_KEY`で取得を再開し、短期制限は1並列と指数バックオフで回避した。残り2,315冊は日次1,000件の余裕を確保しつつ翌日以降に再実行する。国立国会図書館の書影APIは2026年3月31日に終了済みのため代替には使わない。
