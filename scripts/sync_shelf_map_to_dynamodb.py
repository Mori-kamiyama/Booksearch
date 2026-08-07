"""Validate and replace production shelf-map data from a remapped catalog.

Dry-run (default):
  uv run --with boto3 python scripts/sync_shelf_map_to_dynamodb.py --catalog <catalog.json>

Replace production data after writing a local DynamoDB-JSON backup:
  uv run --with boto3 python scripts/sync_shelf_map_to_dynamodb.py \
    --catalog <catalog.json> --replace
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import boto3

from import_bookshelf_catalog import build_bookshelf_data


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CATALOG = (
    "outputs/book_catalog_data_260702/"
    "catalog_reassigned_apriltag_canonical_v2_20260806.json"
)
DEFAULT_LAYOUT = "data/library_layout.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", default=DEFAULT_CATALOG)
    parser.add_argument("--layout", default=DEFAULT_LAYOUT)
    parser.add_argument("--region", default="ap-northeast-1")
    parser.add_argument("--candidates-table", default="booksearch-shelf-candidates")
    parser.add_argument("--observations-table", default="booksearch-shelf-observations")
    parser.add_argument("--min-score", type=float, default=0.75)
    parser.add_argument("--backup-dir")
    parser.add_argument("--replace", action="store_true")
    return parser.parse_args()


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else REPO_ROOT / path


def scan_all(table: Any) -> list[dict[str, Any]]:
    response = table.scan()
    items = list(response.get("Items", []))
    while response.get("LastEvaluatedKey"):
        response = table.scan(ExclusiveStartKey=response["LastEvaluatedKey"])
        items.extend(response.get("Items", []))
    return items


def json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    raise TypeError(f"cannot serialize {type(value).__name__}")


def validate_mapping(
    catalog: dict[str, Any],
    layout: dict[str, Any],
    observations: dict[tuple[int, str], list[float]],
) -> None:
    mapping = catalog.get("shelf_mapping") or {}
    if mapping.get("map_id") != layout.get("map_id"):
        raise ValueError(
            f"map_id mismatch: catalog={mapping.get('map_id')!r}, "
            f"layout={layout.get('map_id')!r}"
        )
    if mapping.get("coordinate_schema_version") != layout.get("schema_version"):
        raise ValueError(
            "coordinate schema mismatch: "
            f"catalog={mapping.get('coordinate_schema_version')!r}, "
            f"layout={layout.get('schema_version')!r}"
        )

    usable = {
        slot["shelf_id"]
        for slot in layout.get("slots", [])
        if slot.get("status") == "usable" and slot.get("shelf_id")
    }
    invalid = sorted({shelf_id for _, shelf_id in observations} - usable)
    if invalid:
        raise ValueError(f"mapped data contains non-usable UI shelf IDs: {invalid[:10]}")


def candidate_confidence(scores: list[float]) -> float:
    avg_score = sum(scores) / len(scores)
    return min(0.99, round(avg_score * (1 - math.pow(0.72, len(scores))), 4))


def delete_items(table: Any, items: list[dict[str, Any]], key_names: list[str]) -> None:
    with table.batch_writer() as batch:
        for item in items:
            batch.delete_item(Key={name: item[name] for name in key_names})


def main() -> int:
    args = parse_args()
    catalog_path = resolve_path(args.catalog)
    layout_path = resolve_path(args.layout)
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    layout = json.loads(layout_path.read_text(encoding="utf-8"))
    bookshelf, observations = build_bookshelf_data(catalog, args.min_score)
    validate_mapping(catalog, layout, observations)

    book_by_pair: dict[tuple[int, str], dict[str, Any]] = {}
    for shelf in bookshelf["shelves"]:
        for book in shelf["books"]:
            book_by_pair[(int(book["book_id"]), shelf["shelf_id"])] = book

    print(f"map_id: {layout['map_id']}")
    print(f"coordinate_schema_version: {layout['schema_version']}")
    print(f"accepted_observations: {sum(len(scores) for scores in observations.values())}")
    print(f"unique_book_shelf_pairs: {len(observations)}")
    print("layout_validation: ok (all mapped shelf IDs are usable in the UI layout)")
    if not args.replace:
        print("dynamodb_update: skipped (pass --replace to back up and replace production data)")
        return 0

    dynamodb = boto3.resource("dynamodb", region_name=args.region)
    candidates_table = dynamodb.Table(args.candidates_table)
    observations_table = dynamodb.Table(args.observations_table)
    old_candidates = scan_all(candidates_table)
    old_observations = scan_all(observations_table)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = resolve_path(
        args.backup_dir or f"outputs/backups/shelf-map-{timestamp}"
    )
    backup_dir.mkdir(parents=True, exist_ok=False)
    (backup_dir / f"{args.candidates_table}.json").write_text(
        json.dumps(old_candidates, ensure_ascii=False, indent=2, default=json_default) + "\n",
        encoding="utf-8",
    )
    (backup_dir / f"{args.observations_table}.json").write_text(
        json.dumps(old_observations, ensure_ascii=False, indent=2, default=json_default) + "\n",
        encoding="utf-8",
    )
    print(f"backup_dir: {backup_dir}")
    print(f"backup_counts: candidates={len(old_candidates)}, observations={len(old_observations)}")

    delete_items(candidates_table, old_candidates, ["book_id", "shelf_id"])
    delete_items(observations_table, old_observations, ["observation_id"])

    now = datetime.now(timezone.utc).isoformat()
    source_job = f"remap-{layout['map_id']}-{timestamp}"
    with observations_table.batch_writer() as batch:
        for (book_id, shelf_id), scores in observations.items():
            title = str(book_by_pair[(book_id, shelf_id)].get("title") or "")
            for index, score in enumerate(scores, 1):
                batch.put_item(
                    Item={
                        "observation_id": f"{source_job}:{book_id}:{shelf_id}:{index:03d}",
                        "job_id": source_job,
                        "crop_id": f"remap-{book_id}-{index:03d}",
                        "book_id": book_id,
                        "shelf_id": shelf_id,
                        "score": Decimal(str(score)),
                        "title": title,
                        "created_at": now,
                        "map_id": layout["map_id"],
                        "coordinate_schema_version": layout["schema_version"],
                    }
                )

    with candidates_table.batch_writer() as batch:
        for (book_id, shelf_id), scores in observations.items():
            title = str(book_by_pair[(book_id, shelf_id)].get("title") or "")
            avg_score = sum(scores) / len(scores)
            batch.put_item(
                Item={
                    "book_id": book_id,
                    "shelf_id": shelf_id,
                    "observations": len(scores),
                    "avg_score": Decimal(str(round(avg_score, 4))),
                    "confidence": Decimal(str(candidate_confidence(scores))),
                    "title": title,
                    "updated_at": now,
                    "map_id": layout["map_id"],
                    "coordinate_schema_version": layout["schema_version"],
                }
            )

    new_candidates = scan_all(candidates_table)
    new_observations = scan_all(observations_table)
    expected_observations = sum(len(scores) for scores in observations.values())
    if len(new_candidates) != len(observations) or len(new_observations) != expected_observations:
        raise RuntimeError(
            "post-write count mismatch: "
            f"candidates={len(new_candidates)}/{len(observations)}, "
            f"observations={len(new_observations)}/{expected_observations}"
        )
    print(
        "dynamodb_update: ok "
        f"(candidates={len(new_candidates)}, observations={len(new_observations)})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
