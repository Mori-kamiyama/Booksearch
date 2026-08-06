# ホンノキ 本番サイト — 情報設計 & UIシステム仕様

このドキュメントは本番サイトの全ページ構造・コンテンツ・UIコンポーネントを定義する。
これを渡された実装者が、追加の意思決定なしで画面を作れる状態を目指す。

- 対象利用者: 全校生（探す人）＋ 図書委員・管理者（整備する人）
- 前提資料: `docs/library_layout_model.md`（棚の物理モデル）, `docs/location_confirmation_ui.md`（位置確認UX仮説）
- 実装スタック: React + Vite + TypeScript + Tailwind CSS（既存 frontend/ を継続）

---

## 1. 設計原則

1. **探す人ファースト**: 全校生が使うのは検索→場所確認だけ。この動線に管理機能を一切混ぜない。
2. **場所は必ず図で伝える**: `base-01-c02-r04` のような内部IDを利用者に生で見せない。常に見取り図ハイライト＋人間可読ラベルに変換する。
3. **信頼度を隠さない**: 位置情報は推定である。確信度・観測回数・鮮度を常に表示し、間違っていたら1タップで正せる。
4. **スキャンは副産物**: 探す行為・訪れる行為が棚データを新しくする。専用の「当番作業」を前提にしない。
5. **モバイル前提**: 利用シーンは図書室内のスマホ。全画面を375px幅で先に設計し、PCは拡張とする。

---

## 2. サイトマップ / URL階層

```
/                      検索（ホーム）
├── /books/:id         本の詳細 + 場所（見取り図ハイライト）
│   └── /books/:id/find    カメラ探索モード（Phase 3）
├── /map               図書室マップ（棚ブラウズ）
│   └── /map/:shelfId      棚区画の詳細（その区画にある本の一覧）
├── /scan              棚スキャン（撮影・アップロード）
│   └── /jobs/:id          スキャン結果（処理状況 + 認識結果）
├── /about             使い方・このサイトについて（1枚もの）
└── /admin             管理（リンクを知っている人のみ。Phase 2まで認証なしでよい）
    ├── /admin/review      要確認キュー（match_confidence=review の一覧処理）
    ├── /admin/shelves     棚データ管理（候補一覧・鮮度・削除）
    └── /admin/tags        タグ配置ガイド（既存 /tag-placement を移設）
```

### ナビゲーション

- **一般向けボトムタブ（モバイル）**: 「さがす /」「マップ /map」「スキャン /scan」の3タブ。ヘッダーは薄くロゴのみ。
- **PC**: 同じ3項目をトップのヘッダーナビに。
- `/admin` 配下はタブに出さない。フッターの小さなリンクと直URLのみ。
- 既存ルート `/shelves` は `/admin/shelves` に、`/tag-placement` は `/admin/tags` に移設（リダイレクトを残す）。

---

## 3. ページ仕様

各ページを「目的 / コンテンツ / 使用コンポーネント / データ / 状態」で定義する。
コンポーネント名は §5 のインベントリに対応。

### 3.1 `/` 検索（ホーム）

| 項目 | 内容 |
|---|---|
| 目的 | 最短で本を見つけて場所ページへ送る |
| コンテンツ | ロゴ＋一言(「図書室の本、どこにある？」) / 検索バー / 検索結果リスト / 未検索時はガイド（人気検索・使い方1行・蔵書冊数） |
| コンポーネント | `SearchBar`, `BookCard`(list), `ShelfChip`, `EmptyState` |
| データ | `GET /api/books/search?q=&limit=30`（既存） |
| 状態 | 未検索（ガイド表示）/ 検索中（skeleton 3件）/ 0件（EmptyState: 「見つかりませんでした。別の言葉で試すか、書名の一部だけで検索」）/ 通常 |

`BookCard` は list variant: サムネイル(あれば) / 書名 / 著者 / `ShelfChip`（最有力棚の人間可読ラベル＋確信度ドット）。カードタップで `/books/:id`。

### 3.2 `/books/:id` 本の詳細＋場所

このサイトの主役ページ。「どこにあるか」を1画面で完結させる。

| 項目 | 内容 |
|---|---|
| 目的 | 場所を図で示し、棚まで歩けるようにする |
| コンテンツ | 上: 書誌（表紙・書名・著者・出版社・ISBN）/ 中: **`ShelfMapHighlight`**（該当ユニットの見取り図で該当マスを点滅ハイライト、`ShelfLocationLabel` を併記）/ 下: `ConfidenceMeter`（確信度・観測回数・最終確認日時）+ `LocationFeedback`（「あった」「なかった」ボタン）/ 複数候補がある場合は `AltShelfList` |
| コンポーネント | `BookHero`, `ShelfMapHighlight`, `ShelfLocationLabel`, `ConfidenceMeter`, `LocationFeedback`, `AltShelfList`, `FindWithCameraButton`(Phase 3) |
| データ | `GET /api/books/:id`（既存, shelf_candidates 含む） / フィードバック送信は **新規 API** `POST /api/books/:id/location-feedback` `{shelf_id, verdict: "found"\|"not_found"}` |
| 状態 | 候補あり（通常）/ 候補なし（「場所は未登録です。スキャンで見つかると表示されます」＋マップへの導線）/ 低確信度（review 相当: 見取り図は表示しつつ「情報が古い可能性」バナー） |

### 3.3 `/books/:id/find` カメラ探索（Phase 3）

| 項目 | 内容 |
|---|---|
| 目的 | カメラをかざすだけで棚まで誘導し、裏でスキャンに貢献させる |
| コンテンツ | フルスクリーンカメラ / 上部に対象の書名バー / 中央に `CameraGuidanceOverlay`（タグ検出時「この棚です・右へ2列・上へ1段」矢印、未検出時「棚のタグが写るように」）/ 下部に `ScanContributionIndicator`（「撮影データは棚情報の更新に使われます」常時表示） |
| コンポーネント | `CameraView`, `CameraGuidanceOverlay`, `ScanContributionIndicator` |
| データ | タグ検出はブラウザ内 WASM（36h11）。誘導計算は `data/apriltag_library_map.json` の physical_intersection をクライアントに同梱。キーフレーム送信は既存 `POST /api/scan`（品質ゲート・クールダウンはサーバー側） |
| 状態 | カメラ許可待ち / 拒否（静的見取り図へフォールバック）/ タグ未検出 / 誘導中 / 到着（該当区画が画面内: 「この区画です」強調） |

### 3.4 `/map` 図書室マップ

| 項目 | 内容 |
|---|---|
| 目的 | 検索を経ずに棚から本を探す・図書室の全体像を掴む |
| コンテンツ | `LibraryFloorMap`（入口を下にした俯瞰図: base-01〜04 と side-01〜04 の8ユニットを配置図で表示。ユニットごとの登録冊数バッジ）→ ユニットタップで `ShelfUnitGrid`（13×7 / 3×7 グリッド。空きスロットはグレー、本のあるマスは冊数濃淡）→ マスタップで `/map/:shelfId` |
| コンポーネント | `LibraryFloorMap`, `ShelfUnitGrid`, `FreshnessBadge` |
| データ | `GET /api/shelf-candidates`（既存, AWS）＋ `data/library_layout.json`（静的: グリッド形状・空きスロット） |
| 状態 | 読込中 / データなし（「まだスキャンされていません」） |

### 3.5 `/map/:shelfId` 棚区画の詳細

| 項目 | 内容 |
|---|---|
| 目的 | 「この区画に何があるか」の一覧。棚の前で使う |
| コンテンツ | `ShelfLocationLabel`（大）+ ミニ `ShelfMapHighlight` / `FreshnessBadge`（最終スキャン日時）/ 本のリスト（`BookCard` list, confidence順）/ 「この棚をスキャンして更新」ボタン → `/scan?shelf=:shelfId` |
| データ | `GET /api/shelf-candidates` をクライアントで shelf_id フィルタ（将来 **新規 API** `GET /api/shelves/:shelfId/books` に置換） |

### 3.6 `/scan` 棚スキャン

| 項目 | 内容 |
|---|---|
| 目的 | 誰でも棚写真を送って棚データを更新できる |
| コンテンツ | 撮影ガイド（`ScanGuideCard`: 良い例/悪い例のイラスト3点 — タグを写す・正面から・1区画ずつ）/ `ScanUploader`（カメラ起動 or ファイル選択、複数枚キュー）/ 送信後は `/jobs/:id` へ遷移 |
| コンポーネント | `ScanGuideCard`, `ScanUploader`, `UploadQueue` |
| データ | `POST /api/scan`（既存） |
| 状態 | 待機 / アップロード中（枚数進捗）/ 失敗（リトライボタン、画像は端末に保持） |

### 3.7 `/jobs/:id` スキャン結果

| 項目 | 内容 |
|---|---|
| 目的 | 処理の進行と認識結果を見せ、貢献を実感させる |
| コンテンツ | `JobProgress`（pending→検出→OCR→照合→完了 のステップ表示、ポーリング）/ 完了後: 認識サマリ（「23冊を認識し、base-02 の3区画を更新しました」）/ 認識できた本のリスト（`BookCard` + `MatchConfidenceBadge`）/ review 判定はその場で「これで合ってる/違う」の軽量確認 |
| コンポーネント | `JobProgress`, `BookCard`, `MatchConfidenceBadge`, `CropThumbnail` |
| データ | `GET /api/jobs/:id`（既存, ポーリング 3s） |
| 状態 | 処理中 / 完了 / 失敗（「画像がぶれていた可能性。撮り直しのコツ」＋ /scan へ戻る） |

### 3.8 `/admin/review` 要確認キュー

| 項目 | 内容 |
|---|---|
| 目的 | review 判定・not_found フィードバックを人手で確定する |
| コンテンツ | フィルタタブ（要確認 / 利用者報告 / 処理済み）/ `ReviewCard` の縦積み: クロップ画像・OCRテキスト・候補書誌（スコア付き）・[承認][別の本を検索][棚から削除] |
| データ | **新規 API**: `GET /api/admin/review-queue`, `POST /api/admin/review/:id/resolve` |
| 備考 | Phase 2。それまでは `/jobs/:id` 内の軽量確認のみで運用 |

### 3.9 `/admin/shelves`, `/admin/tags`

既存の ShelvesPage / TagPlacementPage を移設。スタイルだけ §5 のトークンに合わせる。機能追加なし。

---

## 4. 場所の人間可読ラベル規則

内部ID `{unit}-c{col}-r{row}` からの変換を全画面で共通化する（ユーティリティ `formatShelfLabel()` を1箇所に実装）。

| 内部 | 表示 |
|---|---|
| `base-01` | `入口から1番目の棚` |
| `base-04` | `入口から4番目の棚` |
| `side-02` | `壁側の棚 2` |
| `c05` | `入口側から5列目` |
| `r03` | `下から3段目` |
| 全体例 | `入口から1番目の棚 ・ 入口側から2列目 ・ 下から4段目` |

- col は入口側から、row は下から数える（`library_layout_model.md` の座標規約に一致）。
- base-01 の左右ミラーは data 生成側で解決済み。**表示層で再変換しない。**
- 補助として常にミニ見取り図を併記する。文字だけで伝えない。

---

## 5. UIコンポーネントシステム

### 5.1 デザイントークン（tailwind.config で定義）

```
色
  primary:   green-700 (#15803d)   ホンノキ=木。ボタン・アクティブタブ・ハイライト
  primary-soft: green-100          ハイライト背景・選択マス
  ink:       zinc-900 / zinc-600 / zinc-400   本文・補助・無効
  surface:   white / zinc-50       カード / ページ背景
  line:      zinc-200              罫線
  confidence-high: green-600       確信度 高（auto, confidence >= 0.5）
  confidence-mid:  amber-500       中（review または 0.2–0.5）
  confidence-low:  zinc-400        低・未確認（< 0.2 または候補なし）
  danger:    red-600               削除・not_found

タイポグラフィ（font-family: system-ui + "Hiragino Sans", "Noto Sans JP"）
  page-title: text-xl  font-bold      （各ページ1つ）
  section:    text-base font-semibold
  body:       text-sm
  caption:    text-xs  text-zinc-500  （観測回数・日時など）
  数値・ID:   font-mono text-xs       （admin のみ。一般画面に内部IDを出さない）

形状・余白
  角丸: rounded-xl（カード）/ rounded-lg（ボタン・入力）/ rounded-full（チップ・バッジ）
  影:   shadow-sm のみ。それ以上使わない
  余白: p-4 基本。カード間 gap-3。セクション間 mt-6
  タップ領域: 最小 44px 四方
```

### 5.2 コンポーネントインベントリ

実装単位。props は TypeScript シグネチャで示す。

```ts
// --- 検索・本 ---
SearchBar        { value, onChange, onSubmit, autoFocus? }
                 // 虫眼鏡アイコン内包・×クリア・IME確定でのみ検索発火

BookCard         { book: Book; variant: "list" | "result"; onClick }
                 // list: サムネ48px+書名+著者+ShelfChip。result: /jobs用、MatchConfidenceBadge付き

BookHero         { book: Book }  // 詳細ページ上部。サムネ96px+書誌全項目

// --- 場所表示（このシステムの核。3点セットで常に併用する）---
ShelfChip        { shelfId: string; confidence: number }
                 // 丸チップ。「1番棚・2列・4段」の短縮表記+確信度ドット(高/中/低の3色)

ShelfLocationLabel { shelfId: string; size: "sm" | "lg" }
                 // §4 の完全形ラベル。lg は詳細ページ用 text-base

ShelfMapHighlight { unitId: string; highlight: string[]; onCellClick? }
                 // 1ユニットの正面グリッド(13×7/3×7)をSVG描画。
                 // library_layout.json から形状・空きスロットを取得。
                 // highlight のマスを primary で塗り+pulse アニメ

LibraryFloorMap  { unitCounts: Record<string, number>; onUnitClick }
                 // 俯瞰配置図。入口を下に固定。ユニット矩形+冊数バッジ

ShelfUnitGrid    { unitId; cellCounts: Record<string, number>; onCellClick }
                 // /map 用。冊数を primary の濃淡で表現(0=白, 1-2=100, 3-5=300, 6+=500)

// --- 信頼度・鮮度 ---
ConfidenceMeter  { confidence: number; observations: number; lastSeenAt: string }
                 // 3段階ラベル(「ほぼ確実」「たぶんここ」「情報が古いかも」)+
                 // caption「3回の観測 ・ 最終確認 6/29」。%表示はしない

MatchConfidenceBadge { level: "auto" | "review" | "none" }
                 // auto=「自動照合」green / review=「要確認」amber / none=「未一致」zinc

FreshnessBadge   { lastSeenAt: string }
                 // 「今日確認」「3日前」「2週間以上前」(zinc→amber へ)

// --- フィードバック ---
LocationFeedback { bookId; shelfId; onSubmit(verdict) }
                 // 「この場所にあった？」→ [あった][なかった] 2ボタン。
                 // 送信後は「ありがとう！」に置換(連打防止)

AltShelfList     { candidates: ShelfCandidate[]; onSelect }
                 // 第2候補以降。ShelfChip の縦リスト

// --- スキャン ---
ScanGuideCard    { }                    // 静的。良い例/悪い例イラスト3点
ScanUploader     { onFiles(files) }     // カメラ/ファイル両対応。HEIC→JPEG変換
UploadQueue      { items: UploadItem[] }// 枚数・進捗・失敗リトライ
JobProgress      { status: JobStatus }  // 横ステッパー: 受付→検出→読取→照合→完了
CropThumbnail    { src; title? }        // 認識クロップのサムネ表示

// --- カメラ探索 (Phase 3) ---
CameraView               { onFrame(frame) }
CameraGuidanceOverlay    { target: ShelfCoord; visible: TagDetection[] }
                         // 差分ベクトル→「右へ2列・上へ1段」+ 矢印
ScanContributionIndicator{ active: boolean }  // 録画中の常時表示バッジ

// --- 共通 ---
EmptyState       { icon; title; hint; action? }
ErrorState       { message; onRetry }
Skeleton         { variant: "card" | "grid" | "hero" }
BottomTabs       { }   // さがす/マップ/スキャン。active=primary
PageHeader       { title; back?: boolean }
ReviewCard       { item: ReviewItem; onResolve }   // admin用
```

### 5.3 状態の共通規則

全ページで以下を統一する。個別ページで独自の文言・挙動を作らない。

| 状態 | 規則 |
|---|---|
| 読込中 | `Skeleton`。スピナー単体は使わない |
| 0件 | `EmptyState`。必ず「次にできること」を1つ添える |
| 通信エラー | `ErrorState`＋再試行ボタン。技術的詳細は出さない |
| オフライン | ヘッダー下に黄色バナー「オフラインです」。検索・マップは直近キャッシュ表示 |
| 破壊的操作 | admin のみ存在。confirm ダイアログ必須 |

---

## 6. API マッピング（既存 / 新規）

| API | 状態 | 使用ページ |
|---|---|---|
| `GET /api/books/search` | 既存 | / |
| `GET /api/books/:id` | 既存 | /books/:id |
| `GET /api/shelf-candidates` | 既存(AWS) | /map, /map/:shelfId |
| `POST /api/scan` | 既存 | /scan, find |
| `GET /api/jobs/:id` | 既存 | /jobs/:id |
| `GET /api/shelves` | 既存 | /map(タグ配置参照) |
| `POST /api/books/:id/location-feedback` | **新規** | /books/:id |
| `GET /api/admin/review-queue` | **新規(Phase 2)** | /admin/review |
| `POST /api/admin/review/:id/resolve` | **新規(Phase 2)** | /admin/review |
| `GET /api/shelves/:shelfId/books` | 新規(任意) | /map/:shelfId |
| スキャンのクールダウン制御 | **新規(Phase 2, サーバー側)** | scan系全部 |

静的データとしてクライアントに同梱するもの:
- `data/library_layout.json`（グリッド形状・空きスロット）→ ShelfMapHighlight / ShelfUnitGrid / LibraryFloorMap
- `data/apriltag_library_map.json` の tags.physical_intersection（Phase 3 のみ）→ CameraGuidanceOverlay

---

## 7. フェーズ別ビルド計画

| Phase | 作るページ | 作るコンポーネント |
|---|---|---|
| **1: 探す体験の完成** | `/` 改修, `/books/:id` 新規, `/map` `/map/:shelfId` 新規, ナビ再編 | SearchBar, BookCard, ShelfChip, ShelfLocationLabel, ShelfMapHighlight, LibraryFloorMap, ShelfUnitGrid, ConfidenceMeter, FreshnessBadge, EmptyState/ErrorState/Skeleton, BottomTabs, formatShelfLabel() |
| **2: みんなで更新** | `/scan` 改修, `/jobs/:id` 改修, `/admin/review` 新規, feedback API | ScanGuideCard, ScanUploader, UploadQueue, JobProgress, MatchConfidenceBadge, LocationFeedback, AltShelfList, ReviewCard |
| **3: カメラ探索** | `/books/:id/find` 新規 | CameraView, CameraGuidanceOverlay, ScanContributionIndicator |

Phase 1 完了時点で「検索→図で場所がわかる→棚へ行ける」が成立し、発表可能な最小完成形になる。
