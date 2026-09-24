"""
scripts/openrouter_shelf_bench.py の出力（複数画像・summary.json）を
benchmark/ground_truth_shelf.json と突き合わせて比較評価する。
sarashina時代の evaluate.py --compare と同じ思想（baselineを100として相対スコア表示）。

使い方:
  uv run python benchmark/evaluate_shelf.py \\
    --baseline outputs/shelf_ocr_bench/gemini_3_1_flash_lite_preview/summary.json \\
    outputs/shelf_ocr_bench/gemini_3_5_flash/summary.json \\
    outputs/shelf_ocr_bench/kimi_k2_6/summary.json
"""

import argparse
import json
from difflib import SequenceMatcher
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
GROUND_TRUTH_PATH = REPO_ROOT / "benchmark/ground_truth_shelf.json"
DEFAULT_THRESHOLDS = [0.9, 0.7, 0.5]


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def best_score(query: str, candidates: list[str]) -> float:
    if not candidates:
        return 0.0
    return max(similarity(query, c) for c in candidates)


def load_ground_truth(path: Path) -> dict[str, list[str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {s["image"]: s["books"] for s in data["samples"]}


def score_model(summary_path: Path, ground_truth: dict[str, list[str]], thresholds: list[float]) -> dict:
    data = json.loads(summary_path.read_text(encoding="utf-8"))
    results_by_image = {r["image"]: r.get("titles", []) for r in data.get("results", [])}

    total_gt = 0
    hits = {t: 0 for t in thresholds}
    n_failed_images = 0

    for image, gt_titles in ground_truth.items():
        ocr_titles = results_by_image.get(image, [])
        if image in results_by_image and not ocr_titles:
            n_failed_images += 1
        total_gt += len(gt_titles)
        for gt in gt_titles:
            score = best_score(gt, ocr_titles)
            for t in thresholds:
                if score >= t:
                    hits[t] += 1

    recalls = {t: (hits[t] / total_gt if total_gt else 0.0) for t in thresholds}
    total_elapsed = data.get("total_elapsed_sec", sum(r.get("elapsed_sec", 0) for r in data.get("results", [])))

    return {
        "model": data.get("model", summary_path.parent.name),
        "reasoning_effort": data.get("reasoning_effort"),
        "n_images": data.get("n_images", len(results_by_image)),
        "n_failed_images": n_failed_images,
        "total_gt": total_gt,
        "recalls": recalls,
        "total_elapsed_sec": total_elapsed,
        "label": summary_path.parent.name,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="棚OCRベンチマークの比較評価")
    parser.add_argument("result_paths", nargs="*", help="比較対象の summary.json パス群")
    parser.add_argument("--baseline", required=True, help="baseline summary.json パス")
    parser.add_argument("--thresholds", nargs="+", type=float, default=DEFAULT_THRESHOLDS)
    parser.add_argument("--ground-truth", default=str(GROUND_TRUTH_PATH), help="正解データJSONのパス")
    args = parser.parse_args()

    ground_truth = load_ground_truth(Path(args.ground_truth))
    print(f"正解データ: 画像{len(ground_truth)}件 / タイトル{sum(len(v) for v in ground_truth.values())}件")

    baseline_path = Path(args.baseline)
    all_paths = [baseline_path] + [Path(p) for p in args.result_paths]

    rows = [score_model(p, ground_truth, args.thresholds) for p in all_paths]
    baseline_recall = rows[0]["recalls"][args.thresholds[0]]

    col_w = 34
    header = f"{'モデル':<{col_w}}"
    for t in args.thresholds:
        header += f"  Recall{t:.0%}"
    header += "  相対スコア  失敗画像  推論時間"
    print(f"\n{'='*len(header)}")
    print("棚OCR比較ベンチマーク（Recall90%をbaseline基準で正規化）")
    print(f"{'='*len(header)}\n")
    print(header)
    print("─" * len(header))

    for i, row in enumerate(rows):
        label = row["label"] + (" [baseline]" if i == 0 else "")
        effort_suffix = f"({row['reasoning_effort']})" if row["reasoning_effort"] else ""
        line = f"{label + effort_suffix:<{col_w}}"
        for t in args.thresholds:
            line += f"  {row['recalls'][t]*100:5.1f}%   "
        relative = row["recalls"][args.thresholds[0]] / baseline_recall * 100 if baseline_recall > 0 else 0.0
        line += f"  {relative:6.1f}      {row['n_failed_images']:>4}件   {row['total_elapsed_sec']:.1f}s"
        print(line)

    print(f"\n※ 相対スコアは閾値{args.thresholds[0]:.0%}のRecallを {rows[0]['model']} =100 として正規化")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
