# Booksearch Recovery Report

Codex CLI セッションログから、git 履歴消失で失われた .md / docs ファイルを復元した結果のサマリです。

## 調査範囲

- セッションログ探索先: `/Users/yuta/.codex/sessions/2026/05/` および `/Users/yuta/.codex/sessions/2026/06/`
- スキャン総セッション数: 78 jsonl ファイル
- `payload.cwd` が `/Users/yuta/date/classroom/Booksearch` 配下のものに絞り込み: **14 セッション**
  - 2026-05-08 (2), 2026-05-15 (4), 2026-05-22 (4), 2026-05-29 (2), 2026-06-04 (1), 2026-06-05 (1)
- 抽出方法: 各セッションを `function_call` / `function_call_output` を `call_id` でペアリング。`cat`/`sed`/`head`/`tail` などの読み出しコマンドからファイルパスを正規化し (workdir + 相対パス -> プロジェクト相対)、ターゲットファイルに一致するものを採用。`apply_patch` のヘッダから書き込み内容も抽出。

## 検出された削除/縮小候補

| ファイル | 現在のサイズ | 復元版サイズ | 最新セッション日 | 状態 |
|---|---:|---:|---|---|
| `README.md` | 373 B | 4,921 B (元読み出し) | 2026-06-04 | 復元 (現状は実質ボイラープレート) |
| `AGENT.md` | 275 B | 1,694 B | 2026-05-29 | 復元 |
| `docs/project_status.md` | 578 B | 4,956 B | 2026-05-22 | 復元 |
| `docs/ocr_tools_comparison.md` | 2,261 B | 924 B (sed出力) | 2026-05-22 | 部分復元 (詳細は注意点参照) |
| `docs/about.md` | 欠如 | 6,298 B | 2026-06-04 | 新規復元 |
| `docs/pipeline_experiment_notes.md` | 欠如 | 4,803 B (stitched) | 2026-05-22 | 新規復元 |
| `docs/aws_lambda_frontend_capture_plan.md` | 欠如 | 2,658 B | 2026-05-29 | 新規復元 |
| `aws/README.md` | 8,002 B | 6,058 B | 2026-06-04 | 復元 (現状の方が新しい可能性あり、注意点参照) |
| `dataset/picture_box_detection/README.md` | 欠如 | 1,009 B | 2026-05-08 | 新規復元 (古い) |
| `dataset/box_detection/README.md` | 欠如 | 966 B | 2026-05-08 | 新規復元 (古い) |
| `tests/README.md` | 1,179 B | — | — | 復元不能 (セッションログに本文なし) |
| `frontend/README.md` | 欠如 | — | — | 復元不能 (セッションログに本文なし) |

## 復元ファイルの一行説明

- `recovery/README.md` — Booksearch トップ README。dataset, YOLO 学習, Gemini OCR, ローカル DB, デモ UI, AWS バックエンドへの導線。
- `recovery/AGENT.md` — Codex/Claude 向けの作業メモ。`uv run` 規約、`src/` モジュール構成、よく使うコマンド、データ/出力場所、注意事項。
- `recovery/docs/project_status.md` — Booksearch の実装現在地のまとめ (アノテーション完了状況, 学習結果, デモ UI, 次にやること)。
- `recovery/docs/ocr_tools_comparison.md` — 棚検知後の OCR 工程で試すツール候補と比較メモ (yomitoku, gemini, sarashina など)。
- `recovery/docs/about.md` — 「Web プログラミング アクティブラーナー企画書」。プロジェクトの動機、技術概要、現状の達成状況。
- `recovery/docs/pipeline_experiment_notes.md` — 検出 / OCR / DB 照合 / 低品質 crop 除外 の実験結果メモ。
- `recovery/docs/aws_lambda_frontend_capture_plan.md` — フロント側で良い静止画を撮り、AWS Lambda に処理を分割する方針メモ。
- `recovery/aws/README.md` — Booksearch AWS バックエンド (SAM 版、4 Lambda 構成) のセットアップ・デプロイ手順。
- `recovery/dataset/box_detection/README.md` — 旧 Box Detection データセットの構造説明 (古いスナップショット)。
- `recovery/dataset/picture_box_detection/README.md` — Picture Box Detection データセットの構造説明 (古いスナップショット)。

各ファイルの代替バージョン (より古い、または別の段階で読まれたもの) は `recovery/_alternates/` に
`YYYY-MM-DD_<canonpath>_vN.md` 形式で保存しました。

## 復元できなかった / 部分的にしか復元できなかったもの

- **`tests/README.md`** (現存 1,179 B): Codex セッション内で `cat` 等で開かれた形跡なし。新規復元不能。**現状ファイルを残すこと**。
- **`frontend/README.md`** (欠如): 対象 14 セッションのいずれの読み出しコマンドにも登場せず、復元不能。
- **`docs/ocr_tools_comparison.md`**: 復元版 (924 B) は `sed -n '1,260p'` の出力で、当時すでにファイル全体が 924 B 程度しかなかった可能性が高い。**現状ファイル (2,261 B) の方が後で拡張された可能性があるため、現状を残しつつ recovery 版は参考扱い**。
- **`aws/README.md`**: 復元版 (6,058 B、2026-06-04 時点) より、現状の 8,002 B の方が新しい可能性がある (git 履歴消失前後の編集を想定)。現状を上書きしていません。
- **`dataset/*/README.md`**: 2026-05-08 時点の読み出ししか残っておらず、その後の更新があれば取りこぼし。

## 注意点・限界

1. **Codex セッションの cat/sed 出力範囲のみが復元元**: ファイル全体が一度も読まれていない区間は欠落します。ストイッチングロジックは `sed -n 'N,Mp'` の連続範囲、`tail -n N` の末尾断片を結合しますが、中間欠落があれば該当部分は失われます。
2. **apply_patch ヒットは 0 件**: 該当期間に新規作成または更新パッチが残っていれば直接全文を取れますが、対象セッションには見つかりませんでした (ファイルの新規作成セッションが含まれていない可能性)。
3. **既存リポジトリファイルは一切上書きしていません**: 復元結果はすべて `recovery/` 配下に書き出しています。マージは利用者判断で行ってください。
4. **truncated output**: Codex の `function_call_output` は `max_output_tokens` で頭打ちになる場合があります。長いファイルは複数回に分けて読まれていれば結合されますが、分割が無い長い 1 回読みは末尾が削れている可能性があります。
5. **タイムスタンプ**: 復元版のタイムスタンプは「最後にそのファイルが該当形で Codex セッション内で読まれた瞬間」であり、ファイルの真の最終更新時刻ではありません。

## コード復元（追加調査）

docs/README 復元と同じ Codex セッション 14 本（cwd が `/Users/yuta/date/classroom/Booksearch` 配下のもの）を再走査し、`src/` / `scripts/` / `backend/` / `tests/` / `aws/` 配下のコードファイル（`.py`, `.go`, `.ts(x)`, `.js`, `.yaml`, `.toml`, `.json`, `.sh`, `.mod`, `.sum`, `Dockerfile`）を対象に復元しました。**`frontend/` 配下、および `node_modules` / `.venv` / `.aws-sam` / `dist` / `__pycache__` などの生成物は対象外**です。

### 抽出方法

- `exec_command` (Codex 独自 shell) と `apply_patch` (custom_tool_call) を call_id でペアリング
- `cat` / `cat -n` / `sed -n 'N,Mp'` / `head` / `apply_patch Add File` の各形式から本文を抽出
- `sed -n 'N,Mp'` の出力は **line-number map** で stitching (複数セッション・複数 range の重ね合わせ)
- 行番号付きの `cat -n` / Read tool 出力からは prefix を除去
- 同じファイルの複数 capture がある場合は **新しい日付 + 長いコンテンツ** を優先

### 41 ファイル抽出 → 13 ファイル復元 + 18 alternate

セッションログから 41 個のコードファイルへの参照を抽出し、全て**欠損 0 行**で stitching に成功（`# MISSING_LINE_N` マーカーは一切残っていません）。現状リポジトリと比較した結果：

| カテゴリ | 件数 | 扱い |
|---|---:|---|
| 現状にない / 大きく欠落（>= 1.5x） | **13** | `recovery/` に**本体として復元** |
| 復元版と現状が同等以上に充実 | 18 | `recovery/_alternates/` に参考保存 |
| 内容が実質同一 | 10 | スキップ |

### 本体復元したファイル（13 件）

| パス | 現状 → 復元版 | ソース日付 | 種別 |
|---|---|---|---|
| `recovery/scripts/build_library_db.py` | 欠如 → 23,096 B (705 行) | 2026-05-29 stitched | 完全 |
| `recovery/scripts/quality_filter_demo.py` | 欠如 → 14,899 B (467 行) | 2026-05-22 apply_patch_add | 完全 |
| `recovery/scripts/fetch_google_book_covers.py` | 欠如 → 8,830 B (288 行) | 2026-05-22 stitched | 完全 |
| `recovery/scripts/detect_shelves.py` | 欠如 → 8,404 B (258 行) | 2026-05-08 stitched | 完全 |
| `recovery/scripts/search_book_catalog.py` | 欠如 → 5,619 B (179 行) | 2026-05-08 stitched | 完全 |
| `recovery/scripts/yolo_dataset_utils.py` | 欠如 → 3,126 B (101 行) | 2026-05-08 stitched | 完全 |
| `recovery/scripts/gemini_ocr.py` | 欠如 → 2,785 B (114 行) | 2026-05-08 stitched | 完全 |
| `recovery/scripts/search_library.py` | 1,022 B → 6,118 B (190 行) | 2026-05-29 stitched | 完全 (現状の 6x) |
| `recovery/backend/cmd/import-shelf-observations/main.go` | 欠如 → 4,448 B (192 行) | 2026-06-05 stitched | 完全 |
| `recovery/backend/cmd/server/main.go` | 欠如 → 2,256 B (92 行) | 2026-06-05 stitched | 完全 |
| `recovery/backend/internal/db/books.go` | 2,557 B → 8,292 B (323 行) | 2026-06-05 stitched | 完全 (現状の 3.2x、14 関数 vs 5 関数) |
| `recovery/backend/internal/db/schema.go` | 689 B → 1,228 B (50 行) | 2026-06-05 stitched | 完全 (現状の 1.8x) |
| `recovery/src/video.py` | 1,319 B → 4,577 B (158 行) | 2026-05-29 stitched | 完全 (現状の 3.5x) |

### 代替版保存（18 件）

現状ファイルの方が長い／ほぼ同サイズだが diff があるものは `recovery/_alternates/` に `2026-06-05_<canonpath>_codex_stitched.<ext>` 形式で保存（参考用）。主なもの：

- `backend/api_test.go` (現状 19,074 B vs 復元 8,742 B): 現状の方が大幅に新しい/拡張済み
- `backend/internal/job/job_test.go` (現状 5,605 B vs 復元 1,864 B): 同上
- `aws/functions/go_api/main.go` (現状 17,951 B vs 復元 8,523 B): 同上
- `aws/functions/lookup_worker/handler.py` (現状 13,997 B vs 復元 14,888 B): わずかに復元版が大きいが、現状が後発の可能性
- `aws/functions/yolo_worker/handler.py` (現状 14,190 B vs 復元 12,064 B): 現状の方が新しい

これらは現状を残す方針です。

### 復元できなかった候補

- **`backend/api_test.go`、`backend/internal/job/job_test.go`、`aws/functions/go_api/main.go`、`aws/functions/yolo_worker/handler.py`** の**現状版**は Codex セッションログには対応する完全形が見つかりません。これらは git 履歴消失後に追加・拡張されたコードである可能性が高く、**現状ファイルを残してください**。
- `tests/e2e/booksearch.spec.ts`、`tests/playwright.config.ts` などの tests 配下ファイルは、対象セッションのいずれにも明示的な読み出しがなく、抽出 0 件でした。現状版を保持してください。

### コード復元の注意点

1. **stitching 結果はあくまで「Codex がそのファイルを読んだ瞬間のスナップショット」**: 各セッションでの sed range の重ね合わせから line_map を構築しているため、`missing line` が 0 でも、必ずしも当時の完全形と一致するとは限りません（同一行で日付の異なる版が来た場合は新しい方を採用）。
2. **行番号付き出力は除去済み**: `sed -n` / `cat -n` のプレフィックス `\d+\t` は剥がしてあります。
3. **`backend/go.mod` のような特殊出力**: 一度 `find` 結果に紛れた誤検出を見つけ、CAT/SED の正規表現を「コマンドの先頭でしか発火しない」よう強化済み（最終出力には混入なし）。
4. **既存リポジトリファイルは一切上書きしていません**: 現状の `backend/` `scripts/` `src/` `aws/` などはそのまま、復元結果はすべて `recovery/` 配下です。マージ判断は利用者に委ねます。

## 旧 TypeScript/Booksearch からの復元

ユーザは Booksearch を 4月後半に `/Users/yuta/date/TypeScript/Booksearch` から現在地 `/Users/yuta/date/classroom/Booksearch` へ移行しています。旧ディレクトリは現在 `.DS_Store` のみで実質空のため、`cwd` が旧パスだった Codex セッション 9本（2026-04-16 〜 2026-04-17）を追加で走査し、復元結果を `recovery/typescript_era/` に保存しました。

### 対象セッション 9本

| 日付・時刻 | rollout ID 抜粋 | 何をしていたか |
|---|---|---|
| 2026-04-16 17:00 | 019d954e | `about.md`（企画書）を読みながらフロントエンド/バックエンドの技術スタックを相談 |
| 2026-04-16 22:42 | 019d9687 | `markdown-preview.nvim` を nvim に導入（プロジェクト外、`~/.config/nvim/`） |
| 2026-04-16 22:51 | 019d968f | nvim/Ghostty 上での markdown プレビュー設定の続き（プロジェクト外） |
| 2026-04-17 10:03 | 019d98f6 | moondream の API key を取得し、テストする最初のスクリプト作成 |
| 2026-04-17 10:08 | 019d98fb | `test_moondream.py` の出力（caption / objects）を確認・改善 |
| 2026-04-17 10:12 | 019d98ff | 検知した「箱」を台形補正して正方形化し、再度本の物体検知をする方針実装 |
| 2026-04-17 10:36 | 019d9915 | プロンプトを変えて棚検知を試行 → YOLO で棚検知する選択肢を検討 |
| 2026-04-17 11:43 | 019d9952 | `data/add_tag.jpg`（AprilTag 1/2/4/5 で囲んだ範囲）を切り取って台形補正、その中の本に bounding box を描くフロー実装 |
| 2026-04-17 11:58 | 019d995f | moondream + sarashina OCR / yomitoku OCR を組み合わせて背表紙文字を読む実験。`outputs/...` 配下に JSON で結果が大量に蓄積 |

このうち 2026-04-16 22:42 / 22:51 の 2本は実質 nvim 設定（プロジェクト外ファイル）が中心で、`/Users/yuta/date/TypeScript/Booksearch` 配下の捕捉ファイルは 0〜少数でした。

### 復元成功ファイル一覧（16 件）

すべて現状プロジェクトには**存在しない**ファイルです（=新規復元候補）。書き出し先は `recovery/typescript_era/<元の相対パス>`。

| パス | サイズ | ソース日付 | 抽出方法 | 完全性 |
|---|---:|---|---|---|
| `recovery/typescript_era/about.md` | 1,453 B | 2026-04-16 | stitched_range | 完全（21〜行末欠落の可能性なし、cat 一発読み） |
| `recovery/typescript_era/main.py` | 5,777 B | 2026-04-17 | apply_patch_add | 完全 |
| `recovery/typescript_era/run_moondream.py` | 729 B | 2026-04-17 | stitched_range | 完全 |
| `recovery/typescript_era/run_moondream_yomitoku.py` | 5,289 B | 2026-04-17 | apply_patch_add | 完全 |
| `recovery/typescript_era/run_segment_yomitoku.py` | 5,664 B | 2026-04-17 | apply_patch_add | 完全 |
| `recovery/typescript_era/test_moondream.py` | 7,727 B | 2026-04-17 | apply_patch_add | 完全 |
| `recovery/typescript_era/test_moondream_segment.py` | 3,766 B | 2026-04-17 | apply_patch_add | 完全 |
| `recovery/typescript_era/test_sarashina_ocr.py` | 4,217 B | 2026-04-17 | apply_patch_add | 完全 |
| `recovery/typescript_era/outputs/add_tag/moondream_response.json` | 4,573 B | 2026-04-17 | stitched_range | 完全 |
| `recovery/typescript_era/outputs/add_tag_moondream_yomitoku/summary.json` | 5,916 B | 2026-04-17 | stitched_range | 完全 |
| `recovery/typescript_era/outputs/add_tag_moondream_yomitoku/yomitoku/book_09/crops_book_09_p1.json` | 5,565 B | 2026-04-17 | stitched_range | 完全 |
| `recovery/typescript_era/outputs/add_tag_segment/book_09_segment.json` | 3,630 B | 2026-04-17 | stitched_range | 完全 |
| `recovery/typescript_era/outputs/add_tag_segment/summary.json` | 3,283 B | 2026-04-17 | stitched_range | 完全 |
| `recovery/typescript_era/outputs/add_tag_segment_yomitoku/summary.json` | 3,094 B | 2026-04-17 | stitched_range | 完全 |
| `recovery/typescript_era/outputs/yomitoku_add_tag_json/add_tag_add_tag_warped_p1.json` | 8,303 B | 2026-04-17 | stitched_range | 完全 |
| `recovery/typescript_era/outputs/yomitoku_book_spines_json/book_spines_book_spines_box_square_p1.json` | 79 B | 2026-04-17 | stitched_range | 完全 |

### 旧プロジェクトと現プロジェクトの構造差（重要）

旧 TypeScript/Booksearch 期と現 classroom/Booksearch では、コードの組織化が大きく変わっています。

| 観点 | 旧（2026-04-17 時点） | 現（2026-06-05 時点） |
|---|---|---|
| Python 構造 | リポジトリルート直下に `main.py` / `test_*.py` / `run_*.py` がベタ置き | `src/{detection,lookup,ocr,video}.py` ＋ `scripts/<目的別>.py` にモジュール化 |
| OCR 候補 | moondream / sarashina / yomitoku を試している段階 | yomitoku/GLM/Qwen/PPOCR/openrouter など複数のベンチが揃い、`OCR_POLARIZATION_ANALYSIS.md` で精度二極化を分析するレベルまで進展 |
| 出力配置 | `outputs/<実験名>/...` にデモ用 JSON が直接保存 | `outputs/` は同様だが、現状の中身は別物（旧の `add_tag_*` や `yomitoku_*` ディレクトリは現状にはない） |
| バックエンド | この時点では Go バックエンドの実装はまだなし（about.md で「Go で書く予定」と相談しているのみ） | `backend/` に Go 実装、`aws/` に Lambda 移植が存在 |
| フロントエンド | 検討段階（next.js / AR kit / React） | `frontend/` に Vite + React で実装済み（JobPage / ScanPage / SearchPage / ShelvesPage） |
| 企画書 | `about.md` 1,453 B（プロジェクト直下、初期版） | `recovery/docs/about.md` 11,805 B（より詳細化、5月以降の版） |

**つまり旧 TypeScript/Booksearch 期は「moondream/sarashina/yomitoku を組み合わせて棚＋背表紙を検知/OCR する実験スクリプトを書いていた段階」のスナップショットです。**実験コード（`test_moondream.py`、`run_moondream_yomitoku.py` 等）は現状リポジトリには移行されておらず、現在の `src/ocr.py` / `scripts/run_ocr_comparison.py` などのリファクタ後コードに置き換わっていると判断できます。

### 注意・限界

- **APIキーが平文で含まれます**: 復元した `main.py` / `test_moondream.py` 等には `MOONDREAM_API_KEY` の JWT がデフォルト値として埋め込まれています。これは旧コードの状態そのものです。秘密鍵管理の観点では、これらを参考にする際にトークンの再発行を推奨します。
- **`recovery/typescript_era/about.md`（1,453 B）vs `recovery/docs/about.md`（11,805 B）**: 後者は 5〜6月時点に Codex が読んだ拡張版で、`recovery/typescript_era/` 配下のものは 4月時点の初期版。両方とも参考価値があるため別ディレクトリに残しています。
- **`pyproject.toml`**: 旧版（382 B、`recovery/typescript_era/_alternates/pyproject.toml_codex.toml` に保存）と現状（416 B）はほぼ同サイズで、現状を残しました。実質的な依存関係はリファクタで再整理されている可能性が高いです。
- **`README.md`**: 旧プロジェクトルートの README は今回のセッションログには `cat`/`sed` ヒットが無く、復元できませんでした（参照されただけで本文が読まれていない）。
- **`frontend/` 配下**: 対象 9セッションでは旧プロジェクトに `frontend/` 配下のファイル参照は **0 件**でした。旧プロジェクトの時点ではフロントエンドはまだ実装されておらず、「企画書で検討段階」だったことを裏付けます。
- **`outputs/*.json` の取り扱い**: 復元した JSON は OCR 結果のスナップショットであり、再実行で再生成可能なデータです。コード復元の補助情報として価値がありますが、必須ではありません。
- **行番号付き出力（`cat -n` / `sed` プレフィックス）は除去済み**、`apply_patch Add File` は `+` プレフィックスを剥がして本文化済みです。
- **既存リポジトリファイルは一切上書きしていません**。すべて `recovery/typescript_era/` 配下のみ書き出しています。
