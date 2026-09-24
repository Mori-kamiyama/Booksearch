# Research

アプリケーション本体から独立して、OCR、棚検知、カタログ生成を再現・評価するための領域です。

- `scripts/`: 実験およびローカル処理の実行スクリプト
- `src/`: スクリプトが共有するPython実装
- `benchmark/`: 評価コードと正解データ
- `data/`: 実験入力とサンプル
- `outputs/`、`runs/`: 再生成可能な実行結果。Git管理しません。

ルートから `uv run python research/scripts/<script>.py` として実行します。
