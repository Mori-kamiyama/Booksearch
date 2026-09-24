"""
候補モデル一覧を benchmark/ground_truth_shelf.json に対して一括実行するオーケストレータ。
OpenRouterの/modelsで実在確認済みのスラッグのみを対象とする（2026-07-10時点）。

使い方:
  export OPENROUTER_API_KEY=...
  uv run python scripts/run_shelf_ocr_candidates.py --list
  uv run python scripts/run_shelf_ocr_candidates.py --limit 10          # 全候補を10画像だけで試走（コスト確認用）
  uv run python scripts/run_shelf_ocr_candidates.py --models gemini_3_5_flash kimi_k2_6
  uv run python scripts/run_shelf_ocr_candidates.py                     # 全候補・全画像
"""

import argparse
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
RUNNER = REPO_ROOT / "scripts/openrouter_shelf_bench.py"

# label -> (OpenRouter model id, reasoning_effort or None)
# 2026-07-10 に https://openrouter.ai/api/v1/models で実在確認済み。
CANDIDATE_MODELS: dict[str, tuple[str, str | None]] = {
    "gemini_3_1_flash_lite_preview": ("google/gemini-3.1-flash-lite-preview", None),  # baseline (=ground truth生成モデル)
    "gemini_3_5_flash": ("google/gemini-3.5-flash", None),
    "gemini_3_flash": ("google/gemini-3-flash-preview", None),
    "gemini_3_flash_minimal_thinking": ("google/gemini-3-flash-preview", "minimal"),
    "kimi_k2_6": ("moonshotai/kimi-k2.6", None),
    "kimi_k2_5": ("moonshotai/kimi-k2.5", None),  # 表の "Kimi K2.5 Thinking" はOpenRouter未掲載のためbase版で代替
    "qwen3_7_plus": ("qwen/qwen3.7-plus", None),
    "gpt_5_4_mini_high": ("openai/gpt-5.4-mini", "high"),
    "mimo_v2_5": ("xiaomi/mimo-v2.5", None),
    "gemma_4_31b": ("google/gemma-4-31b-it", None),
    "gemma_4_26b_a4b": ("google/gemma-4-26b-a4b-it", None),
    "qwen3_5_397b_a17b": ("qwen/qwen3.5-397b-a17b", None),
    "qwen3_5_122b_a10b": ("qwen/qwen3.5-122b-a10b", None),
    "qwen3_6_35b_a3b": ("qwen/qwen3.6-35b-a3b", None),
    "qwen3_5_35b_a3b": ("qwen/qwen3.5-35b-a3b", None),
    "qwen3_5_9b": ("qwen/qwen3.5-9b", None),
    "nemotron_3_nano_omni": ("nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free", None),
    # 未掲載のためベンチマーク対象外: "Llama Nemotron Nano VL 8B", "SenseNova-U1 18B"

    # --- 第2弾候補（2026-07-13、10枚サブセットでのスクリーニング用） ---
    "claude_haiku_4_5": ("anthropic/claude-haiku-4.5", None),
    "claude_sonnet_5": ("anthropic/claude-sonnet-5", None),
    "gpt_5_4": ("openai/gpt-5.4", None),
    "qwen3_vl_235b_a22b_instruct": ("qwen/qwen3-vl-235b-a22b-instruct", None),
    "qwen3_vl_32b_instruct": ("qwen/qwen3-vl-32b-instruct", None),
    "glm_5v_turbo": ("z-ai/glm-5v-turbo", None),
    "grok_4_5": ("x-ai/grok-4.5", None),
    "gpt_5_6_sol": ("openai/gpt-5.6-sol", None),

    # --- 第3弾候補（2026-07-13、「VL専用系統は強い」仮説の検証） ---
    "qwen3_vl_8b_instruct": ("qwen/qwen3-vl-8b-instruct", None),
    "qwen3_vl_30b_a3b_instruct": ("qwen/qwen3-vl-30b-a3b-instruct", None),
    "qwen3_vl_30b_a3b_thinking": ("qwen/qwen3-vl-30b-a3b-thinking", None),
    "nemotron_nano_12b_v2_vl": ("nvidia/nemotron-nano-12b-v2-vl:free", None),
    "glm_4_6v": ("z-ai/glm-4.6v", None),
    "perceptron_mk1": ("perceptron/perceptron-mk1", None),
    "ernie_4_5_vl_424b_a47b": ("baidu/ernie-4.5-vl-424b-a47b", None),
    "qwen2_5_vl_72b_instruct": ("qwen/qwen2.5-vl-72b-instruct", None),

    # --- 第4弾候補（2026-07-13、未検証ベンダーの探索） ---
    "nova_premier": ("amazon/nova-premier-v1", None),
    "nova_pro": ("amazon/nova-pro-v1", None),
    "mistral_medium_3_5": ("mistralai/mistral-medium-3-5", None),
    "mistral_small_3_1_24b": ("mistralai/mistral-small-3.1-24b-instruct", None),
    "llama_3_2_11b_vision": ("meta-llama/llama-3.2-11b-vision-instruct", None),
    "step_3_7_flash": ("stepfun/step-3.7-flash", None),
    "minimax_m3": ("minimax/minimax-m3", None),
    "fugu_ultra": ("sakana/fugu-ultra", None),

    # --- 第5弾候補（2026-07-13、Llama 4系ネイティブマルチモーダル） ---
    "llama_4_maverick": ("meta-llama/llama-4-maverick", None),
    "llama_4_scout": ("meta-llama/llama-4-scout", None),

    # --- 第6弾候補（2026-07-13） ---
    "nex_n2_mini": ("nex-agi/nex-n2-mini", None),
}

BASELINE_LABEL = "gemini_3_1_flash_lite_preview"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="候補モデルを一括でベンチマーク実行します。")
    parser.add_argument("--models", nargs="+", choices=list(CANDIDATE_MODELS.keys()), default=None)
    parser.add_argument("--limit", type=int, default=None, help="1モデルあたりの実行画像数上限")
    parser.add_argument("--ground-truth", default=None, help="正解データJSONのパス（省略時はスクリプトのデフォルト）")
    parser.add_argument("--list", action="store_true", help="候補モデル一覧を表示して終了")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.list:
        print(f"{'label':<34}{'openrouter model':<45}{'reasoning'}")
        for label, (model_id, effort) in CANDIDATE_MODELS.items():
            baseline_mark = " [baseline]" if label == BASELINE_LABEL else ""
            print(f"{label:<34}{model_id:<45}{effort or '-'}{baseline_mark}")
        print("\n未掲載のため対象外: Llama Nemotron Nano VL 8B, SenseNova-U1 18B, Kimi K2.5 Thinking(thinking版なし)")
        return 0

    labels = args.models or list(CANDIDATE_MODELS.keys())

    results = {}
    for label in labels:
        model_id, effort = CANDIDATE_MODELS[label]
        cmd = [
            sys.executable,
            str(RUNNER),
            "--model", model_id,
            "--label", label,
        ]
        if effort:
            cmd += ["--reasoning-effort", effort]
        if args.limit:
            cmd += ["--limit", str(args.limit)]
        if args.ground_truth:
            cmd += ["--ground-truth", args.ground_truth]

        print(f"\n{'='*60}\n{label} ({model_id})\n{'='*60}")
        proc = subprocess.run(cmd, cwd=REPO_ROOT)
        results[label] = proc.returncode == 0

    print(f"\n{'='*60}\n実行結果\n{'='*60}")
    for label, ok in results.items():
        print(f"  {'OK' if ok else 'NG'}  {label}")

    print(
        "\n比較するには:\n"
        f"  uv run python benchmark/evaluate_shelf.py --baseline outputs/shelf_ocr_bench/{BASELINE_LABEL}/summary.json "
        + " ".join(f"outputs/shelf_ocr_bench/{l}/summary.json" for l in labels if l != BASELINE_LABEL)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
