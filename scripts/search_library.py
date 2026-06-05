"""Search the local SQLite library DB from the command line."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from lookup import search_library


def main() -> int:
    parser = argparse.ArgumentParser(description="Search library.db")
    parser.add_argument("query")
    parser.add_argument("--db", default="outputs/library/library.db")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--include-isbn", action="store_true")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = REPO_ROOT / db_path
    rows = search_library(db_path, args.query, limit=args.limit, include_isbn=args.include_isbn)
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
