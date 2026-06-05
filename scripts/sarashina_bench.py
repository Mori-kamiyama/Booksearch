"""
Sarashina2.2-OCR をベンチマーク用に実行するラッパー。
.venv_sarashina（transformers==4.57.1）の Python でサブプロセスを起動する。

使い方:
  uv run python scripts/sarashina_bench.py outputs/add_tag/add_tag_warped.jpg
  uv run python scripts/sarashina_bench.py data/add_tag.jpg --output-dir outputs/add_tag_sarashina
"""

import argparse
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
SARASHINA_PYTHON = REPO_ROOT / ".venv_sarashina" / "bin" / "python"
SARASHINA_RUNNER = REPO_ROOT / "scripts" / "sarashina_runner.py"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sarashina2.2-OCR をベンチマーク用に実行します。")
    parser.add_argument("image", help="入力画像パス")
    parser.add_argument("--output-dir", default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    image_path = REPO_ROOT / args.image
    if not image_path.exists():
        raise FileNotFoundError(f"画像が見つかりません: {image_path}")

    if not SARASHINA_PYTHON.exists():
        print(
            f"エラー: {SARASHINA_PYTHON} が見つかりません。\n"
            "以下を実行して Sarashina 専用 venv を作成してください:\n"
            "  uv venv .venv_sarashina --python 3.12\n"
            "  uv pip install --python .venv_sarashina/bin/python "
            "transformers==4.57.1 torch accelerate huggingface_hub 'pillow<11' sentencepiece",
            file=sys.stderr,
        )
        return 1

    stem = image_path.stem
    output_dir = REPO_ROOT / (args.output_dir or f"outputs/{stem}_sarashina")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_json = output_dir / "summary.json"

    print(f"Sarashina venv: {SARASHINA_PYTHON}")
    print(f"入力: {image_path}")
    print(f"出力: {output_json}")

    result = subprocess.run(
        [str(SARASHINA_PYTHON), str(SARASHINA_RUNNER), str(image_path), str(output_json)],
        check=False,
    )

    if result.returncode != 0:
        print(f"sarashina_runner.py が終了コード {result.returncode} で失敗しました。", file=sys.stderr)
        return result.returncode

    print("完了")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
