"""
複数のOCRメソッドで同じ画像をテストし、結果を比較分析するスクリプト。

使い方:
  # 環境変数を設定してから実行
  export GEMINI_API_KEY=...
  export OPENROUTER_API_KEY=...

  # 利用可能なすべてのOCRで実行
  uv run python scripts/run_ocr_comparison.py

  # 特定のモデルのみ実行
  uv run python scripts/run_ocr_comparison.py --models gemini qwen

  # 結果のみ比較（すでに出力がある場合）
  uv run python scripts/run_ocr_comparison.py --analyze-only
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from collections import defaultdict


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_IMAGE = "data/add_tag.jpg"
OUTPUTS_DIR = REPO_ROOT / "outputs"

# 利用可能なOCRモデル
AVAILABLE_MODELS = {
    "gemini": {
        "script": "scripts/gemini_ocr.py",
        "args": ["--image", DEFAULT_IMAGE],
        "output_dir": "outputs/add_tag_gemini",
        "required_env": ["GEMINI_API_KEY"],
    },
    "qwen": {
        "script": "scripts/qwen_ocr.py",
        "args": [DEFAULT_IMAGE],
        "output_dir": "outputs/add_tag_qwen",
        "required_env": [],
    },
    "openrouter_claude": {
        "script": "scripts/openrouter_ocr.py",
        "args": ["--model", "anthropic/claude-3-haiku", DEFAULT_IMAGE],
        "output_dir": "outputs/add_tag_claude",
        "required_env": ["OPENROUTER_API_KEY"],
    },
    "openrouter_llama": {
        "script": "scripts/openrouter_ocr.py",
        "args": ["--model", "meta-llama/llama-3.2-11b-vision-instruct", DEFAULT_IMAGE],
        "output_dir": "outputs/add_tag_llama",
        "required_env": ["OPENROUTER_API_KEY"],
    },
    "openrouter_nova": {
        "script": "scripts/openrouter_ocr.py",
        "args": ["--model", "amazon/nova-lite-v1", DEFAULT_IMAGE],
        "output_dir": "outputs/add_tag_nova",
        "required_env": ["OPENROUTER_API_KEY"],
    },
}


def check_dependencies(model_name: str) -> bool:
    """モデルの実行に必要な環境変数を確認"""
    model_config = AVAILABLE_MODELS.get(model_name)
    if not model_config:
        return False

    script_path = REPO_ROOT / model_config["script"]
    if not script_path.exists():
        print(f"⚠️  {model_name}: スクリプトが見つかりません ({script_path})")
        return False

    missing_env = [
        env for env in model_config["required_env"]
        if not os.environ.get(env)
    ]

    if missing_env:
        print(f"⚠️  {model_name}: 環境変数が未設定です ({', '.join(missing_env)})")
        return False

    return True


def run_ocr_model(model_name: str) -> bool:
    """OCRモデルを実行し、結果を保存"""
    model_config = AVAILABLE_MODELS.get(model_name)
    if not model_config:
        print(f"❌ {model_name}: 未知のモデル")
        return False

    print(f"\n🔄 {model_name} を実行中...")

    script_path = REPO_ROOT / model_config["script"]
    output_dir = REPO_ROOT / model_config["output_dir"]

    cmd = [
        "uv",
        "run",
        "python",
        str(script_path),
        *model_config["args"],
        "--output-dir",
        str(output_dir),
    ]

    try:
        result = subprocess.run(
            cmd,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=300,
        )

        if result.returncode == 0:
            print(f"✅ {model_name}: 成功")
            # 出力のサマリーを表示
            summary_path = output_dir / "summary.json"
            if summary_path.exists():
                data = json.loads(summary_path.read_text(encoding="utf-8"))
                titles = data.get("titles", data.get("strings", []))
                print(f"   検出: {len(titles)}冊")
            return True
        else:
            print(f"❌ {model_name}: 失敗")
            if result.stderr:
                print(f"   エラー: {result.stderr[:200]}")
            return False

    except subprocess.TimeoutExpired:
        print(f"❌ {model_name}: タイムアウト（300秒超過）")
        return False
    except Exception as e:
        print(f"❌ {model_name}: 例外 {e}")
        return False


def analyze_results() -> None:
    """すべての出力結果を比較分析"""
    print("\n" + "="*70)
    print("分析結果")
    print("="*70)

    # 利用可能な出力結果を列挙
    available_results = []
    for model_name, config in AVAILABLE_MODELS.items():
        summary_path = REPO_ROOT / config["output_dir"] / "summary.json"
        if summary_path.exists():
            available_results.append(summary_path)

    if not available_results:
        print("⚠️  分析する出力結果がありません")
        return

    print(f"見つかった結果: {len(available_results)}個")
    for path in available_results:
        print(f"  - {path.parent.name}")

    # analyze_ocr_polarization.py を実行
    analyze_script = REPO_ROOT / "scripts" / "analyze_ocr_polarization.py"
    if available_results:
        baseline = available_results[0]
        others = available_results[1:]

        cmd = [
            "uv",
            "run",
            "python",
            str(analyze_script),
            "--baseline",
            str(baseline),
            *[str(p) for p in others],
        ]

        try:
            result = subprocess.run(
                cmd,
                cwd=REPO_ROOT,
                capture_output=False,
                text=True,
            )
            return result.returncode == 0
        except Exception as e:
            print(f"分析スクリプト実行エラー: {e}")
            return False


def main() -> int:
    parser = argparse.ArgumentParser(description="複数のOCRメソッドを実行し、結果を比較分析します。")
    parser.add_argument(
        "--models",
        nargs="+",
        choices=list(AVAILABLE_MODELS.keys()),
        help="実行するモデル（省略時：すべて試す）",
    )
    parser.add_argument(
        "--analyze-only",
        action="store_true",
        help="OCRを実行せず、既存の結果のみ分析",
    )
    parser.add_argument(
        "--image",
        default=DEFAULT_IMAGE,
        help=f"入力画像パス（デフォルト: {DEFAULT_IMAGE}）",
    )

    args = parser.parse_args()

    # 出力ディレクトリを作成
    OUTPUTS_DIR.mkdir(exist_ok=True)

    # テスト画像を確認
    image_path = REPO_ROOT / args.image
    if not image_path.exists():
        print(f"❌ 画像が見つかりません: {image_path}")
        return 1

    if not args.analyze_only:
        # モデル選択
        if args.models:
            models_to_run = args.models
        else:
            print("利用可能なOCRモデル:")
            for name in AVAILABLE_MODELS.keys():
                has_deps = check_dependencies(name)
                status = "✅ 実行可能" if has_deps else "⚠️  環境変数未設定"
                print(f"  - {name}: {status}")
            print()

            # 依存関係がそろっているモデルのみ実行
            models_to_run = [
                name for name in AVAILABLE_MODELS.keys()
                if check_dependencies(name)
            ]

            if not models_to_run:
                print("❌ 実行可能なモデルがありません。環境変数を確認してください。")
                print(f"\n必要な環境変数:")
                for name, config in AVAILABLE_MODELS.items():
                    if config["required_env"]:
                        print(f"  {name}: {', '.join(config['required_env'])}")
                return 1

        print(f"実行するモデル: {', '.join(models_to_run)}\n")

        # OCRを実行
        results = {}
        for model_name in models_to_run:
            success = run_ocr_model(model_name)
            results[model_name] = success

        # 実行結果サマリー
        print(f"\n{'='*70}")
        print("実行結果")
        print(f"{'='*70}")
        success_count = sum(1 for v in results.values() if v)
        print(f"成功: {success_count}/{len(results)}")
        for name, success in results.items():
            status = "✅" if success else "❌"
            print(f"  {status} {name}")

    # 分析実行
    analyze_results()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
