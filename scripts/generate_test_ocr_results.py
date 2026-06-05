"""
OCR分析スクリプトをテストするためのシミュレーションデータを生成します。

各OCRメソッドについて、異なる特性を持つ結果を生成し、
二極化の度合いを分析します。

使い方:
  uv run python scripts/generate_test_ocr_results.py
"""

import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent

# 正解データ
GROUND_TRUTH = [
    "会社というモンスターが私たちを不幸にしているのかもしれない。",
    "経営も人生も「ひねらんかい」",
    "校則が変わる、生徒が変わる。学校が変わる。",
    "幸せをつくるシゴト",
    "旅をする木",
    "そらみみ植物園",
    "はつみみ植物園",
    "教育DXで未来の教室をつくろう",
    "なめらかなお金がめぐる社会。",
    "和える",
    "やりがいから考える　自分らしい働き方",
    "プラントハンター西畠清順",
    "ファイブ・フェイ・ポジショニング戦略",
    "から考える「好き」を強みにする生き方",
    "温かいテクノロジー",
    "ビジネスの未来　山口周",
]

# シミュレーション: 各OCRメソッドのパフォーマンスプロファイル
# スコアは (0.0-1.0) で、高いほど認識精度が高い

SIMULATED_RESULTS = {
    "Gemini": {
        # Gemini: 比較的高精度で、完全認識と不完全認識が分かれている（二極化）
        "scores": [0.98, 0.95, 0.92, 0.88, 0.85, 0.78, 0.35, 0.32, 0.28, 0.25, 0.22, 0.20, 0.18, 0.15, 0.12, 0.10],
        "description": "高精度だが、一部の本では認識失敗（二極化傾向）",
    },
    "Qwen2.5-VL-3B": {
        # Qwen: やや低精度だが、それなりに均等分布
        "scores": [0.92, 0.88, 0.85, 0.82, 0.75, 0.70, 0.65, 0.60, 0.55, 0.50, 0.45, 0.40, 0.35, 0.30, 0.25, 0.20],
        "description": "中程度の精度、スコアが均等に分布（二極化なし）",
    },
    "Claude3Haiku": {
        # Claude: 低精度で、ほとんど認識失敗（二極化なし、全体的に低い）
        "scores": [0.45, 0.42, 0.40, 0.38, 0.35, 0.32, 0.30, 0.28, 0.25, 0.22, 0.20, 0.18, 0.15, 0.12, 0.10, 0.08],
        "description": "全体的に低精度（二極化なし）",
    },
    "Llama3.2V": {
        # Llama: 中程度の精度だが、やや二極化
        "scores": [0.88, 0.85, 0.80, 0.75, 0.70, 0.65, 0.38, 0.35, 0.32, 0.30, 0.28, 0.25, 0.22, 0.20, 0.18, 0.15],
        "description": "やや二極化傾向（Geminiほどではない）",
    },
    "AmazonNovaLite": {
        # Nova: 高精度で、強く二極化している
        "scores": [0.99, 0.98, 0.97, 0.96, 0.95, 0.94, 0.15, 0.12, 0.10, 0.08, 0.06, 0.05, 0.04, 0.03, 0.02, 0.01],
        "description": "高精度で、強く二極化（完全 vs 失敗）",
    },
}


def generate_ocr_result(model_name: str, scores: list[float]) -> dict:
    """OCR結果のシミュレーションデータを生成"""
    # スコアから文字列を生成（スコアに応じて文字列の長さを変更）
    # これはシミュレーション用で、実際のOCR出力を模擬しています
    generated_strings = []
    for i, (gt, score) in enumerate(zip(GROUND_TRUTH, scores)):
        if score >= 0.8:
            # 高スコア: ほぼ完全一致
            generated_strings.append(gt)
        elif score >= 0.5:
            # 中程度: 部分一致（文字削減）
            # 最初の部分を取る
            length = max(3, int(len(gt) * score))
            generated_strings.append(gt[:length] + "...")
        elif score >= 0.2:
            # 低スコア: わずかな一致
            generated_strings.append(gt[:10] + "...")
        # スコアが0.2未満: まったく認識できず（省略）

    return {
        "model": model_name,
        "image": "data/add_tag.jpg",
        "titles": generated_strings,
        "elapsed_sec": 5.0,  # ダミー値
    }


def main() -> None:
    print("OCR分析テスト用データを生成中...\n")

    # 出力ディレクトリを作成
    (REPO_ROOT / "outputs").mkdir(exist_ok=True)

    model_names = {
        "Gemini": "add_tag_gemini",
        "Qwen2.5-VL-3B": "add_tag_qwen",
        "Claude3Haiku": "add_tag_claude",
        "Llama3.2V": "add_tag_llama",
        "AmazonNovaLite": "add_tag_nova",
    }

    for model_name, dir_name in model_names.items():
        if model_name in SIMULATED_RESULTS:
            output_dir = REPO_ROOT / "outputs" / dir_name
            output_dir.mkdir(parents=True, exist_ok=True)

            scores = SIMULATED_RESULTS[model_name]["scores"]
            result = generate_ocr_result(model_name, scores)

            # summary.json として保存
            summary_path = output_dir / "summary.json"
            summary_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

            print(f"✅ {model_name}")
            print(f"   出力: {summary_path}")
            print(f"   説明: {SIMULATED_RESULTS[model_name]['description']}")
            print(f"   検出数: {len(result['titles'])}件")
            print()

    print("✅ テストデータ生成完了！")
    print("\n次のコマンドで比較分析を実行できます:")
    print("  uv run python scripts/analyze_ocr_polarization.py \\")
    print("    --baseline outputs/add_tag_gemini/summary.json \\")
    print("    outputs/add_tag_qwen/summary.json \\")
    print("    outputs/add_tag_claude/summary.json \\")
    print("    outputs/add_tag_llama/summary.json \\")
    print("    outputs/add_tag_nova/summary.json")


if __name__ == "__main__":
    main()
