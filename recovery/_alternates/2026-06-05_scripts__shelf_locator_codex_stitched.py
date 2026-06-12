    for entry in catalog.get("entries", []):
        source_image = entry.get("source_image")
        if source_image and entry.get("bbox_xyxy"):
            entries_by_image.setdefault(source_image, []).append(entry)

    tag_summaries: dict[str, list[dict[str, Any]]] = {}
    for source_image, entries in entries_by_image.items():
        image = cv2.imread(source_image)
        if image is None:
            continue
        tags = detect_tags(image, mapping)
        assignments = assign_boxes_to_shelves(entries, tags, mapping, max_tag_distance)
        tag_summaries[source_image] = [
            {
                "tag_id": tag.tag_id,
                "center": [round(float(tag.center[0]), 2), round(float(tag.center[1]), 2)],
                "angle_deg": round(tag.angle_deg, 2),
                "orientation_status": tag.orientation_status,
            }
            for tag in tags
        ]
        for entry in entries:
            assignment = assignments.get(str(entry.get("box_id")))
            if assignment is None:
                continue
            entry["shelf_id"] = assignment.shelf_id
            entry["shelf_assignment"] = assignment.to_json()

    catalog["shelf_mapping"] = {
        "mapping_file": str(mapping_path),
        "tag_detections": tag_summaries,
    }
    return catalog


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AprilTag mappingでcatalogのYOLO boxへ棚IDを付与します。")
    parser.add_argument("--catalog", required=True, help="build_book_catalog.py の catalog.json")
    parser.add_argument("--mapping", required=True, help="AprilTagと棚IDのマッピングJSON")
    parser.add_argument("--output", default=None, help="出力JSON。省略時は入力catalogを上書き")
    parser.add_argument(
        "--max-tag-distance",
        type=float,
        default=None,
        help="box重心からtag中心までの最大距離px。0なら距離制限なし。省略時はboxサイズから自動",
    )
    return parser.parse_args()


def resolve_path(path: str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else REPO_ROOT / p


def main() -> int:
    args = parse_args()
    catalog_path = resolve_path(args.catalog)
    mapping_path = resolve_path(args.mapping)
    output_path = resolve_path(args.output) if args.output else catalog_path

    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    annotated = annotate_catalog(catalog, mapping_path, args.max_tag_distance)
    output_path.write_text(json.dumps(annotated, ensure_ascii=False, indent=2), encoding="utf-8")

    assigned = sum(1 for entry in annotated.get("entries", []) if entry.get("shelf_id"))
    skipped = sum(
        1
        for entry in annotated.get("entries", [])
        if (entry.get("shelf_assignment") or {}).get("status") == "skipped"
    )
    print(f"catalog : {output_path}")
    print(f"assigned: {assigned}")
    print(f"skipped : {skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
