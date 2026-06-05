"""
OCRパイプラインのベンチマーク評価スクリプト。

単体評価:
  uv run python benchmark/evaluate.py --ocr-result outputs/add_tag_gemini/summary.json
  uv run python benchmark/evaluate.py --ocr-result outputs/add_tag_segment_yomitoku/summary.json
  uv run python benchmark/evaluate.py --strings "本のタイトル1" "本のタイトル2" ...

複数モデル比較 (Gemini を baseline=1.0 として相対スコア表示):
  uv run python benchmark/evaluate.py \\
    --compare \\
    --baseline outputs/add_tag_gemini/summary.json \\
    outputs/add_tag_glm/summary.json \\
    outputs/add_tag_sarashina/summary.json \\
    outputs/add_tag_segment_yomitoku/summary.json
"""

import argparse
import json
from difflib import SequenceMatcher
from pathlib import Path


GROUND_TRUTH_PATH = Path(__file__).parent / "ground_truth.json"
DEFAULT_THRESHOLDS = [0.9, 0.7, 0.5]


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def best_match(query: str, candidates: list[str]) -> tuple[str, float]:
    best = max(candidates, key=lambda c: similarity(query, c))
    return best, similarity(query, best)


def collect_strings(summary_path: Path) -> list[str]:
    """summary.json の形式を問わず OCR 文字列リストを返す。

    対応フォーマット:
      - {"titles": [...]}                       Gemini 形式
      - {"strings": [...]}                      GLM-OCR / Sarashina bench 形式
      - {"books": [{"strings": [...]}]}         yomitoku segment 形式
      - {"books": [{"strings": [...]}]}         moondream+yomitoku 形式
    """
    data = json.loads(summary_path.read_text(encoding="utf-8"))

    if "titles" in data:
        return [t for t in data["titles"] if t]

    if "strings" in data:
        return [s for s in data["strings"] if s]

    if "books" in data:
        strings: list[str] = []
        for book in data["books"]:
            strings.extend(s for s in book.get("strings", []) if s)
        return strings

    return []


def model_label(path: Path) -> str:
    parent = path.parent.name
    # ディレクトリ名から読みやすいラベルを生成
    label_map = {
        "add_tag_gemini": "Gemini",
        "add_tag_glm": "GLM-OCR",
        "add_tag_sarashina": "Sarashina",
        "add_tag_segment_yomitoku": "yomitoku(seg)",
        "add_tag_moondream_yomitoku": "moondream+yomitoku",
    }
    return label_map.get(parent, parent)


def elapsed_label(path: Path) -> str:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        sec = data.get("elapsed_sec")
        if sec is not None:
            return f"{sec:.1f}s"
    except Exception:
        pass
    return "API"


def recall_at(ground_truth: list[str], ocr_strings: list[str], threshold: float) -> float:
    if not ocr_strings:
        return 0.0
    hits = sum(1 for gt in ground_truth if best_match(gt, ocr_strings)[1] >= threshold)
    return hits / len(ground_truth)


def evaluate_single(ground_truth: list[str], ocr_strings: list[str], thresholds: list[float]) -> None:
    if not ocr_strings:
        print("OCR結果が空です。")
        return

    print(f"\n{'='*60}")
    print(f"Ground Truth: {len(ground_truth)}冊")
    print(f"OCR出力テキスト数: {len(ocr_strings)}件")
    print(f"{'='*60}\n")

    results = [{"gt": gt, **dict(zip(["best_match", "score"], best_match(gt, ocr_strings)))} for gt in ground_truth]

    for threshold in thresholds:
        hits = [r for r in results if r["score"] >= threshold]
        recall = len(hits) / len(ground_truth) * 100
        print(f"[閾値 {threshold:.0%}] 検出: {len(hits)}/{len(ground_truth)}冊  Recall: {recall:.1f}%")

    print(f"\n{'─'*60}")
    print("各書籍の最高一致スコア:")
    print(f"{'─'*60}")
    for r in sorted(results, key=lambda r: r["score"], reverse=True):
        icon = "✓" if r["score"] >= 0.6 else "✗"
        print(f"{icon} [{r['score']:.2f}] {r['gt']}")
        if r["score"] < 0.9:
            print(f"       → OCR: {r['best_match']}")


def evaluate_compare(
    ground_truth: list[str],
    baseline_path: Path,
    other_paths: list[Path],
    thresholds: list[float],
) -> None:
    all_paths = [baseline_path] + other_paths
    col_w = 22

    # ヘッダー
    header = f"{'モデル':<{col_w}}"
    for t in thresholds:
        header += f"  Recall{t:.0%}"
    header += "  相対スコア  推論時間"
    print(f"\n{'='*len(header)}")
    print(f"OCR比較ベンチマーク  (正解: {len(ground_truth)}冊)")
    print(f"{'='*len(header)}\n")
    print(header)
    print("─" * len(header))

    baseline_strings = collect_strings(baseline_path)
    baseline_recall = recall_at(ground_truth, baseline_strings, thresholds[0])

    for path in all_paths:
        strings = collect_strings(path)
        label = model_label(path)
        is_baseline = path == baseline_path

        recalls = [recall_at(ground_truth, strings, t) for t in thresholds]
        relative = recalls[0] / baseline_recall if baseline_recall > 0 else 0.0
        time_label = elapsed_label(path)

        row = f"{'[baseline] ' + label if is_baseline else label:<{col_w}}"
        for r in recalls:
            row += f"  {r*100:5.1f}%   "
        row += f"  {relative:.2f}       {time_label}"
        print(row)

    print(f"\n※ 相対スコアは閾値{thresholds[0]:.0%}のRecallをGemini基準で正規化")


def main() -> int:
    parser = argparse.ArgumentParser(description="OCRベンチマーク評価")
    parser.add_argument("result_paths", nargs="*", help="比較モード時の追加 summary.json パス群")
    parser.add_argument("--ocr-result", help="単体評価: summary.json のパス")
    parser.add_argument("--strings", nargs="+", help="単体評価: OCR文字列を直接渡す")
    parser.add_argument("--compare", action="store_true", help="複数モデル比較モード")
    parser.add_argument("--baseline", help="比較モード時のベースライン summary.json")
    parser.add_argument(
        "--thresholds",
        nargs="+",
        type=float,
        default=DEFAULT_THRESHOLDS,
        help="評価閾値（デフォルト: 0.9 0.7 0.5）",
    )
    args = parser.parse_args()

    gt_data = json.loads(GROUND_TRUTH_PATH.read_text(encoding="utf-8"))
    ground_truth: list[str] = gt_data["books"]

    if args.compare or args.baseline:
        if not args.baseline:
            parser.error("--compare には --baseline が必要です。")
        baseline_path = Path(args.baseline)
        other_paths = [Path(p) for p in args.result_paths]
        evaluate_compare(ground_truth, baseline_path, other_paths, args.thresholds)
        return 0

    # 単体評価
    if args.strings:
        ocr_strings = args.strings
    elif args.ocr_result:
        ocr_strings = collect_strings(Path(args.ocr_result))
    else:
        parser.error("--ocr-result か --strings か --compare のいずれかを指定してください。")

    evaluate_single(ground_truth, ocr_strings, args.thresholds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
