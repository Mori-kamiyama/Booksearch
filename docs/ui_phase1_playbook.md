# Phase 1 UI 実装手順書（AIエージェント向け）

このドキュメントは、このリポジトリの文脈を持たないAIエージェント（または新しいセッション）が、
`feature/production-ui` ブランチで Phase 1 のUIを実装し切るための手順書である。
各ステップに「なぜやるか」を明記している。順番どおりに進めること。

---

## 0. なぜこの実装をするのか（背景）

このプロジェクト「ホンノキ」は、学校図書室の本をカメラ・OCR・AprilTagで認識し
「どの本がどの棚区画にあるか」を答えるシステム。認識パイプライン（YOLO→Gemini OCR→
蔵書DB照合→AprilTagによる棚割当）は検証済みで、本番AWSにもデプロイ済み。

現在のフロントエンドは**機能検証用のモック**であり、次の問題がある：

1. **場所が内部IDのまま表示される**: 検索しても `base-01-c02-r04` としか出ず、
   利用者（全校生）は棚まで歩けない。システムの価値が最後の1mで途切れている。
2. **信頼度が伝わらない**: 位置情報はOCR観測からの推定なのに、確定情報のように
   見えてしまう。間違っていたとき利用者が修正する手段もない。
3. **画面構成が開発者視点**: 検索/スキャン/棚一覧/タグ配置がフラットに並び、
   全校生向けの動線と管理者向けの機能が混在している。

Phase 1 のゴールは「**検索 → 見取り図で場所がわかる → 棚へ歩いて本を見つける**」
という利用者の基本動線を完成させること。これが成立して初めて全校生に公開できる。
（Phase 2=誰でもスキャン、Phase 3=カメラ探索ナビは、この土台の上に載る。）

## 1. 必読資料（実装前に読む）

| ファイル | 何が書いてあるか |
|---|---|
| `docs/site_ia_ui_system.md` | **本体仕様**。URL階層・全ページのコンテンツ定義・コンポーネントprops・デザイントークン・状態規則。この手順書は同仕様のPhase 1部分の実装順序を定めたもの |
| `docs/library_layout_model.md` | 棚の物理モデル（base 4台 13×7、side 4台 3×7、空きスロット規則、座標規約） |
| `docs/location_confirmation_ui.md` | 位置表示UXの製品仮説（信頼度の見せ方・修正動線） |
| `frontend/src/` 既存コード | 既存の書き方・APIクライアント（`lib/api.ts` の `apiFetch`）を踏襲する |

## 2. 環境と検証方法

```bash
# バックエンド（ローカルSQLite: outputs/library/library.db を読む）
cd backend && go build -o booksearch-backend . && ./booksearch-backend
# ポート8080が他プロセスに使われている場合: PORT=8090 ./booksearch-backend

# フロントエンド
cd frontend && npm install && npm run dev   # → http://localhost:5173/
# バックエンドが8090の場合: VITE_API_BASE_URL=http://127.0.0.1:8090 npm run dev

# 検証（各ステップ完了時に必ず実行）
cd frontend && npx tsc --noEmit && npm run build
```

ローカルAPI（`backend/router.go`）: `GET /api/books/search?q=` `GET /api/books/:id`
`GET /api/shelf-candidates` `POST /api/scan` `GET /api/jobs/:id` `GET /api/shelves`。
Phase 1 はこの既存APIだけで完結する。**バックエンドの変更は不要**。

## 3. 実装前に知るべき事実（ハマりどころ）

1. **棚ラベルは自作しない**: `data/library_layout.json` の `slots[]` に全448スロットの
   `label_ja`（例: 「入口側から1台目 1列目 下から1段目」）が既に入っている。
   shelf_id→ラベル変換はこのデータの参照で実装する。文言規則を新たに発明しないこと。
2. **Tailwind は v4**: 設定ファイルは無く、`frontend/src/index.css` の
   `@import "tailwindcss"` 方式。トークンは同ファイルに `@theme { --color-primary: ... }`
   ブロックで定義する。`tailwind.config.js` を新規作成しないこと。
3. **base-01 の左右ミラーはデータ生成側で解決済み**: 表示層で col の反転や
   再変換を絶対にしない（`library_layout_model.md` 参照）。
4. **内部ID（base-01-c02-r04）を一般画面に出さない**: 仕様の設計原則2。
   デバッグ目的でも一般ページに残さない。admin配下のみ可。
5. **shelf-candidates の confidence は 0〜0.99 の推定値**: 1回観測=0.28 程度。
   %表示せず3段階の言葉に変換する（仕様 §5.2 ConfidenceMeter）。
6. **本番の棚データはDynamoDB由来**（ローカルはSQLite）だが、APIの形は同じなので
   フロントは意識しなくてよい。

## 4. 実装ステップ

各ステップは独立してコミットする。コミットメッセージは英語・変更理由を本文に書く。

### Step 1: 型定義とAPIクライアント拡張

- **なぜ**: 以降の全コンポーネントが参照する契約。最初に固めないと `any` が伝染し、
  ページ間で Book / ShelfCandidate の形の解釈がずれる。
- **やること**: `src/lib/types.ts` を新設し、既存APIレスポンスに合わせて
  `Book`, `ShelfCandidate`, `Job`, `LayoutSlot`, `LayoutUnit` を定義。
  `lib/api.ts` に型付きの取得関数（`searchBooks`, `getBook`, `getShelfCandidates` など）を追加。
- **受け入れ基準**: 既存ページが型付き関数経由に置き換わり `tsc --noEmit` が通る。

### Step 2: 棚レイアウトユーティリティ

- **なぜ**: 「場所を図と言葉で伝える」がこのサイトの核心価値であり、全ページが依存する。
  ここを最後にすると各画面に shelf_id のパース処理が散らばり、修正不能になる。
- **やること**: `data/library_layout.json` を `src/data/library_layout.json` として
  ビルドに同梱（またはimport）。`src/lib/shelf.ts` に:
  - `getSlot(shelfId): LayoutSlot | undefined`
  - `formatShelfLabel(shelfId): string`（slot の `label_ja` を返す。未知IDは「場所情報なし」）
  - `getUnit(unitId): LayoutUnit`（cols/rows/empty_rule）
  - `confidenceLevel(c: number): "high" | "mid" | "low"`（閾値: ≥0.5 / ≥0.2 / それ未満）
- **受け入れ基準**: `formatShelfLabel("base-01-c02-r04")` が正しい日本語ラベルを返す
  ユニットテスト的な確認（コンソールでもよい）を実施済み。

### Step 3: デザイントークン

- **なぜ**: 後からトークン化すると全コンポーネントの色・余白を書き直すことになる。
  コンポーネントを1つも作る前に入れるのが最も安い。
- **やること**: `src/index.css` に `@theme` でトークンを定義
  （仕様 §5.1: primary=green-700系, confidence 3色, 角丸, 余白）。
- **受け入れ基準**: `bg-primary` などのユーティリティが任意のコンポーネントで使える。

### Step 4: 共通部品

- **なぜ**: 仕様 §5.3 の状態規則（読込中=Skeleton、0件=EmptyState+次の行動、
  エラー=ErrorState+再試行）は「全画面で統一」が要件。ページを作る前に部品を
  用意しないと、ページごとに独自実装が生まれ規則が形骸化する。
- **やること**: `src/components/` を新設し `EmptyState` `ErrorState` `Skeleton`
  `PageHeader` `BottomTabs` を実装（propsは仕様 §5.2）。
- **受け入れ基準**: Storybook不要。仮ページで3状態を目視確認。

### Step 5: 場所表示3点セット

- **なぜ**: `ShelfMapHighlight`（SVGグリッド描画）はPhase 1で技術リスクが最も高い部品。
  empty_rule の欠けマス表現・ハイライト・レスポンシブを早期に検証し、
  問題があれば仕様側を直す判断を早くするため。
- **やること**: `ShelfChip` `ShelfLocationLabel` `ShelfMapHighlight` を実装。
  `ShelfMapHighlight` は `getUnit()` から 13×7（または3×7）のグリッドSVGを描画、
  `empty_rule.regions` のマスは描かない、`highlight` のマスを primary で塗り
  CSSアニメで点滅。図の下に `ShelfLocationLabel` を必ず併記。
- **受け入れ基準**: base-01〜04（欠けあり）と side-01〜04（3×7）の全ユニットが
  正しく描画され、任意の shelf_id をハイライトできる。375px幅で崩れない。

### Step 6: ルーティング再編

- **なぜ**: ページ追加の前に器を仕様の階層（一般3タブ + /admin 分離）へ揃える。
  先にページを作ると移設時にリンク修正が二度手間になる。
- **やること**: `App.tsx` を仕様 §2 のルートに再編。`/shelves→/admin/shelves`、
  `/tag-placement→/admin/tags` は `<Navigate>` でリダイレクトを残す。
  一般レイアウトに `BottomTabs`（さがす/マップ/スキャン）を組み込む。
- **受け入れ基準**: 旧URLで開いても新URLに飛ぶ。タブのactive表示が正しい。

### Step 7: `/books/:id` 本の詳細＋場所（新規）

- **なぜ**: このサイトの主役ページ。検索結果から遷移して「場所が図でわかる」を
  1画面で完結させる。Phase 1 の価値はこのページに集約される。
- **やること**: 仕様 §3.2 のとおり `BookHero` + `ShelfMapHighlight` +
  `ConfidenceMeter` + `AltShelfList` を組む。データは `GET /api/books/:id`。
  ※ `LocationFeedback`（あった/なかったボタン）は新規APIが要るため **Phase 2 に送る**。
  UIの置き場所だけコメントで確保しておく。
- **受け入れ基準**: 候補あり/候補なし/低確信度の3状態が仕様どおり出る。
  実データ（例: 「識別・予測・異常検知」→ base-01-c02-r04）で目視確認。

### Step 8: `/` 検索の改修

- **なぜ**: 最多アクセスページだが、既存実装があるため新規制作より低リスク。
  主役ページ（Step 7）の遷移先が存在してから改修する方が確認が容易。
- **やること**: 仕様 §3.1。`SearchBar` `BookCard`(list) `ShelfChip` に置換、
  未検索時ガイド・0件EmptyState・skeletonを適用。カードタップで `/books/:id` へ。
- **受け入れ基準**: 検索→カード→詳細→見取り図の動線が通しで動く。

### Step 9: `/map` と `/map/:shelfId`（新規）

- **なぜ**: 検索に依存しない第2の動線（棚からブラウズ）。また Phase 2 の
  「この棚をスキャン」導線の受け皿になるため、Phase 1 のうちに骨格を作る。
- **やること**: 仕様 §3.4/3.5。`LibraryFloorMap`（8ユニット俯瞰・入口は下固定）→
  `ShelfUnitGrid`（冊数濃淡）→ 区画詳細（本一覧＋FreshnessBadge）。
  データは `GET /api/shelf-candidates` をクライアント側で集計。
- **受け入れ基準**: マップ→ユニット→マス→本一覧→本詳細まで遷移が通る。

### Step 10: 仕上げ

- **なぜ**: 個別ページで確認した状態規則も、通しで見ると漏れが出る。
  公開品質の担保はここでしかできない。
- **やること**:
  1. 全ページで読込中/0件/エラーの3状態を再確認（DevToolsのネットワーク遮断で）
  2. 375px幅・PC幅の両方でレイアウト確認
  3. 一般画面に内部IDが出ていないこと（grep で `shelfId` の生表示を点検）
  4. `npx tsc --noEmit && npm run build` 成功
  5. `docs/project_status.md` に Phase 1 完了を追記

## 5. やってはいけないこと

- バックエンド・AWS・DB への変更（Phase 1 はフロントのみで完結する）
- `master` への直接コミット（このブランチで作業し、完了時にPRを作る）
- 仕様にないページ・機能の追加（気づきは実装せず `docs/` にメモを残す）
- 既存の `/scan` `/jobs/:id` の機能改修（Phase 2 スコープ。ルート移設の影響確認のみ）
- コンポーネントへの内部ID直書き表示・`label_ja` 相当ロジックの再発明

## 6. 完了の定義

- [ ] Step 1〜10 が個別コミットで完了している
- [ ] 「検索 → 見取り図 → 棚へ行ける」動線がローカルで通しで動く
- [ ] `npx tsc --noEmit && npm run build` が通る
- [ ] PR を作成し、変更概要・スクリーンショット・仕様との対応を記載
