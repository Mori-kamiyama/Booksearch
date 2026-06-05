"""
OCRの精度分布を分析するスクリプト。

複数のOCR方法で本の認識精度を調べ、本が100%認識できるものと
できないもので二極化しているかを分析します。

使い方:
  uv run python scripts/analyze_ocr_polarization.py \\
    --baseline outputs/add_tag_gemini/summary.json \\
    outputs/add_tag_qwen/summary.json \\
    outputs/add_tag_llama/summary.json

  # または単一モデルの分析
  uv run python scripts/analyze_ocr_polarization.py \\
    --single outputs/add_tag_gemini/summary.json
"""

import argparse
import json
import statistics
from difflib import SequenceMatcher
from pathlib import Path
from collections import defaultdict


GROUND_TRUTH_PATH = Path(__file__).parent.parent / "benchmark" / "ground_truth.json"


def similarity(a: str, b: str) -> float:
    """2つの文字列の類似度を0.0-1.0で返す"""
    return SequenceMatcher(None, a, b).ratio()


def best_match(query: str, candidates: list[str]) -> tuple[str, float]:
    """クエリに最も類似した候補と類似度を返す"""
    if not candidates:
        return "", 0.0
    best = max(candidates, key=lambda c: similarity(query, c))
    return best, similarity(query, best)


def collect_strings(summary_path: Path) -> list[str]:
    """summary.json から OCR 文字列リストを返す"""
    data = json.loads(summary_path.read_text(encoding="utf-8"))

    if "titles" in data:
        return [t for t in data["titles"] if t]
    if "strings" in data:
        return [s for s in data["strings"] if s]
    if "books" in data:
        strings = []
        for book in data["books"]:
            strings.extend(s for s in book.get("strings", []) if s)
        return strings
    return []


def model_label(path: Path) -> str:
    """パスからモデルのわかりやすいラベルを生成"""
    parent = path.parent.name
    label_map = {
        "add_tag_gemini": "Gemini",
        "add_tag_gemini25": "Gemini2.5Flash",
        "add_tag_gemini31lite": "Gemini3.1FlashLite",
        "add_tag_qwen": "Qwen2.5-VL-3B",
        "add_tag_glm": "GLM-OCR",
        "add_tag_sarashina": "Sarashina",
        "add_tag_yomitoku": "yomitoku",
        "add_tag_llama": "Llama3.2V",
        "add_tag_claude": "Claude3Haiku",
        "add_tag_nova": "AmazonNovaLite",
    }
    return label_map.get(parent, parent)


def analyze_book_scores(ground_truth: list[str], ocr_strings: list[str], threshold: float = 0.9) -> dict:
    """
    各本のマッチスコアを計算し、二極化の度合いを分析

    Returns:
        {
            "scores": [score1, score2, ...],  # 各本のスコア
            "perfect": int,                     # 閾値以上のスコア数
            "imperfect": int,                   # 閾値未満のスコア数
            "mean": float,                      # 平均スコア
            "stdev": float,                     # 標準偏差
            "gap": float,                       # パーフェクト vs インパーフェクトのギャップ
            "polarization": float,              # 二極化スコア (0-1, 1に近いほど二極化)
            "details": [...]                    # 各本の詳細情報
        }
    """
    scores = []
    details = []

    for gt in ground_truth:
        _, score = best_match(gt, ocr_strings)
        scores.append(score)
        details.append({
            "book": gt,
            "score": round(score, 3),
            "perfect": score >= threshold,
        })

    if not scores:
        return {}

    # 基本統計
    mean = statistics.mean(scores)
    stdev = statistics.stdev(scores) if len(scores) > 1 else 0.0

    perfect = sum(1 for s in scores if s >= threshold)
    imperfect = len(scores) - perfect

    # 二極化指標
    perfect_ratio = perfect / len(scores)
    imperfect_ratio = imperfect / len(scores)

    # パーフェクト vs インパーフェクトのギャップ（平均値）
    perfect_scores = [s for s in scores if s >= threshold]
    imperfect_scores = [s for s in scores if s < threshold]

    perfect_mean = statistics.mean(perfect_scores) if perfect_scores else 0.0
    imperfect_mean = statistics.mean(imperfect_scores) if imperfect_scores else 0.0
    gap = perfect_mean - imperfect_mean

    # 二極化スコア: ギャップが大きく、かつ両群が存在すれば高い
    # - ギャップが大きい (0.3以上で加点)
    # - 完全群と不完全群の両方が存在する
    # - 完全群の比率が中程度（20-80%）
    if perfect > 0 and imperfect > 0:
        gap_score = min(gap / 0.5, 1.0)  # ギャップが0.5以上なら最高値
        balance_score = 1.0 - abs(perfect_ratio - 0.5) * 2  # 50%に近いほど高い
        polarization = gap_score * balance_score
    else:
        polarization = 0.0

    return {
        "scores": scores,
        "perfect": perfect,
        "imperfect": imperfect,
        "count": len(scores),
        "perfect_ratio": round(perfect_ratio, 3),
        "imperfect_ratio": round(imperfect_ratio, 3),
        "mean": round(mean, 3),
        "stdev": round(stdev, 3),
        "perfect_mean": round(perfect_mean, 3),
        "imperfect_mean": round(imperfect_mean, 3),
        "gap": round(gap, 3),
        "polarization": round(polarization, 3),
        "details": sorted(details, key=lambda d: d["score"], reverse=True),
    }


def print_single_analysis(model_label: str, analysis: dict) -> None:
    """単一モデルの分析結果を表示"""
    print(f"\n{'='*70}")
    print(f"モデル: {model_label}")
    print(f"{'='*70}\n")

    if not analysis:
        print("分析データなし")
        return

    print(f"対象書籍: {analysis['count']}冊")
    print(f"完全認識（≥90%）: {analysis['perfect']}/{analysis['count']} 冊 ({analysis['perfect_ratio']*100:.1f}%)")
    print(f"不完全（<90%）: {analysis['imperfect']}/{analysis['count']} 冊 ({analysis['imperfect_ratio']*100:.1f}%)")
    print()

    print(f"スコア統計:")
    print(f"  平均: {analysis['mean']:.3f}")
    print(f"  標準偏差: {analysis['stdev']:.3f}")
    print(f"  完全群の平均: {analysis['perfect_mean']:.3f}")
    print(f"  不完全群の平均: {analysis['imperfect_mean']:.3f}")
    print(f"  ギャップ: {analysis['gap']:.3f}")
    print()

    # 二極化度の判定
    polarization = analysis['polarization']
    if polarization > 0.3:
        polarization_level = "高い（明確に二極化）"
    elif polarization > 0.15:
        polarization_level = "中程度（やや二極化）"
    else:
        polarization_level = "低い（均等分布に近い）"

    print(f"二極化スコア: {polarization:.3f} → {polarization_level}")
    print()

    # 分布の可視化
    print("スコア分布:")
    ranges = [(0.0, 0.3), (0.3, 0.5), (0.5, 0.7), (0.7, 0.9), (0.9, 1.0)]
    for low, high in ranges:
        count = sum(1 for s in analysis['scores'] if low <= s < high)
        bar = "█" * count
        print(f"  {low:.1f}-{high:.1f}: {bar} ({count}冊)")

    # 詳細
    print(f"\n{'─'*70}")
    print("各書籍の認識スコア:")
    print(f"{'─'*70}")
    for detail in analysis['details']:
        icon = "✓" if detail['perfect'] else "✗"
        print(f"{icon} [{detail['score']:.3f}] {detail['book']}")


def print_comparison(ground_truth: list[str], model_results: dict) -> None:
    """複数モデルの比較結果を表示"""
    print(f"\n{'='*100}")
    print(f"OCRモデル比較分析 (正解: {len(ground_truth)}冊)")
    print(f"{'='*100}\n")

    # テーブルのヘッダー
    col_w = 25
    header = f"{'モデル':<{col_w}} {'完全認識':<12} {'不完全':<12} {'平均スコア':<15} {'二極化':<12}"
    print(header)
    print("─" * len(header))

    # 各モデルの結果を表示
    for model_name in sorted(model_results.keys()):
        analysis = model_results[model_name]
        if not analysis:
            continue

        perfect_str = f"{analysis['perfect']}/{analysis['count']} ({analysis['perfect_ratio']*100:.0f}%)"
        imperfect_str = f"{analysis['imperfect']}/{analysis['count']} ({analysis['imperfect_ratio']*100:.0f}%)"
        mean_str = f"{analysis['mean']:.3f}±{analysis['stdev']:.3f}"
        polarization = analysis['polarization']
        polarization_str = f"{polarization:.3f}"

        print(f"{model_name:<{col_w}} {perfect_str:<12} {imperfect_str:<12} {mean_str:<15} {polarization_str:<12}")

    # 総括
    print()
    print("二極化分析:")
    for model_name in sorted(model_results.keys()):
        analysis = model_results[model_name]
        if not analysis:
            continue

        gap = analysis['gap']
        polarization = analysis['polarization']

        # 二極化判定
        if polarization > 0.5:
            polarization_status = "強く二極化"
        elif polarization > 0.3:
            polarization_status = "中程度に二極化"
        elif polarization > 0.1:
            polarization_status = "わずかに二極化"
        else:
            polarization_status = "均等分布（二極化なし）"

        gap_status = "大きい" if abs(gap) > 0.3 else "小さい"

        print(f"  {model_name}:")
        print(f"    - 完全群の平均: {analysis['perfect_mean']:.3f}")
        print(f"    - 不完全群の平均: {analysis['imperfect_mean']:.3f}")
        print(f"    - ギャップ: {gap:.3f} ({gap_status})")
        print(f"    - 二極化: {polarization_status}")


def main() -> int:
    parser = argparse.ArgumentParser(description="OCRの精度分布と二極化を分析します。")
    parser.add_argument("result_paths", nargs="*", help="追加の summary.json パス")
    parser.add_argument("--single", help="単一モデルの詳細分析")
    parser.add_argument("--baseline", help="比較モードのベースライン summary.json")

    args = parser.parse_args()

    # 正解データを読み込む
    gt_data = json.loads(GROUND_TRUTH_PATH.read_text(encoding="utf-8"))
    ground_truth = gt_data["books"]

    if args.single:
        # 単一モデルの詳細分析
        result_path = Path(args.single)
        ocr_strings = collect_strings(result_path)
        analysis = analyze_book_scores(ground_truth, ocr_strings)
        label = model_label(result_path)
        print_single_analysis(label, analysis)
        return 0

    if args.baseline or args.result_paths:
        # 複数モデルの比較
        all_paths = []
        if args.baseline:
            all_paths.append(Path(args.baseline))
        all_paths.extend(Path(p) for p in args.result_paths)

        model_results = {}
        for path in all_paths:
            if path.exists():
                ocr_strings = collect_strings(path)
                analysis = analyze_book_scores(ground_truth, ocr_strings)
                label = model_label(path)
                model_results[label] = analysis

        print_comparison(ground_truth, model_results)
        return 0

    parser.error("--single か --baseline のいずれかを指定してください。")


if __name__ == "__main__":
    raise SystemExit(main())
