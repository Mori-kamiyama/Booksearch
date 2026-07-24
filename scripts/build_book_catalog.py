"""
YOLO で本の箱を検出し、箱ごとの crop を Gemini OCR にかけて蔵書リストを作る。

使い方:
  uv run python scripts/build_book_catalog.py
  uv run python scripts/build_book_catalog.py data/add_tag.jpg
  uv run python scripts/build_book_catalog.py --source data --skip-ocr
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
from ultralytics import YOLO


REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import detection as book_detection  # noqa: E402
import lookup as book_lookup  # noqa: E402
import ocr as book_ocr  # noqa: E402
import shelf_locator  # noqa: E402
import video as book_video  # noqa: E402

DEFAULT_YOLO_MODEL = "aws/functions/yolo_worker/assets/yolo_model.pt"
DEFAULT_LIBRARY_DB = "outputs/library/library.db"
KNOWN_BOOKS_PATH = REPO_ROOT / "data" / "known_books.json"
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}

@dataclass
class DetectedBox:
    image_path: Path
    index: int
    confidence: float
    xyxy: tuple[int, int, int, int]
    crop_path: Path
    box_id: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="箱検出、GeminiタイトルOCR、書誌情報検索を行ってcatalogを作成します。"
    )
    parser.add_argument(
        "input",
        nargs="?",
        default=None,
        help="入力画像または画像ディレクトリ。省略時は data/",
    )
    parser.add_argument("--source", default=None, help="入力画像または画像ディレクトリ")
    parser.add_argument("--model", default=DEFAULT_YOLO_MODEL, help="YOLO重みファイル")
    parser.add_argument("--output-dir", default="outputs/book_catalog", help="出力先")
    parser.add_argument("--catalog-name", default="catalog.json", help="catalog JSON名")
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--conf", type=float, default=0.25)
    parser.add_argument("--device", default="mps", help="mps/cpu/cuda など")
    parser.add_argument("--crop-pad", type=float, default=0.02, help="検出boxの余白率")
    parser.add_argument("--min-blur-score", type=float, default=150.0, help="これ未満のcropはぼやけとしてOCRをスキップ")
    parser.add_argument("--min-crop-short-edge", type=int, default=550, help="短辺がこれ未満のcropは小さすぎとしてOCRをスキップ")
    parser.add_argument("--reject-edge-touch", action="store_true", help="画像端に接するcropを見切れ疑いとしてOCRをスキップ")
    parser.add_argument(
        "--reject-edge-aspect",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="画像端に接し、横長/縦長すぎるcropをOCRスキップ",
    )
    parser.add_argument("--edge-wide-aspect", type=float, default=1.45, help="edge cropを横長すぎとして扱う幅/高さ比")
    parser.add_argument("--edge-tall-aspect", type=float, default=0.45, help="edge cropを縦長すぎとして扱う幅/高さ比")
    parser.add_argument("--include-low-quality", action="store_true", help="低品質判定のcropもOCRする")
    parser.add_argument("--gemini-model", default="gemini-3.1-flash-lite-preview")
    parser.add_argument("--library-db", default=DEFAULT_LIBRARY_DB, help="ローカル図書DB")
    parser.add_argument("--skip-ocr", action="store_true", help="crop作成のみ行う")
    parser.add_argument(
        "--no-lookup",
        action="store_true",
        help="タイトルからの図書DB検索を行わない",
    )
    parser.add_argument("--google-fallback", action="store_true", help="ローカルDBがない時にGoogle Booksを使う")
    parser.add_argument(
        "--apriltag-map",
        default=None,
        help="AprilTag ID/象限と棚IDのマッピングJSON。指定時はboxへshelf_idを付与",
    )
    parser.add_argument(
        "--max-tag-distance",
        type=float,
        default=None,
        help="box重心からtag中心までの最大距離px。0なら距離制限なし。省略時はboxサイズから自動",
    )
    parser.add_argument("--video", default=None, help="動画ファイル。--source より優先。フレームを抽出して処理する")
    parser.add_argument("--video-interval", type=float, default=0.5, help="キーフレーム最小間隔（秒, default=0.5）")
    parser.add_argument("--min-frame-blur", type=float, default=80.0, help="フレーム全体のブレ閾値。未満はスキップ（手ぶれ対策）")
    parser.add_argument("--scene-change-threshold", type=float, default=25.0, help="前フレームとの差分閾値。未満は同じシーンとしてスキップ")
    parser.add_argument("--no-dedup", action="store_true", help="crop重複スキップを無効化")
    parser.add_argument("--dedup-crops", action="store_true", help="画像ディレクトリでも同一セッション内のcrop重複をOCRしない")
    parser.add_argument("--fingerprint-db", default=None, help="成功済みOCR cropの永続フィンガープリントDB")
    parser.add_argument("--max-images", type=int, default=None)
    parser.add_argument("--sleep", type=float, default=0.4, help="Gemini呼び出し間隔")
    return parser.parse_args()


def resolve_path(path: str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else REPO_ROOT / p


def list_source_images(source: Path, max_images: int | None = None) -> list[Path]:
    if source.is_file():
        images = [source] if source.suffix.lower() in IMAGE_EXTENSIONS else []
    else:
        images = sorted(
            p for p in source.iterdir()
            if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS
        )
    if max_images is not None:
        images = images[:max_images]
    return images


def clamp_box(
    xyxy: tuple[float, float, float, float],
    image_size: tuple[int, int],
    pad_ratio: float,
) -> tuple[int, int, int, int]:
    width, height = image_size
    x1, y1, x2, y2 = xyxy
    pad = max(x2 - x1, y2 - y1) * pad_ratio
    x1 = max(0, int(x1 - pad))
    y1 = max(0, int(y1 - pad))
    x2 = min(width, int(x2 + pad))
    y2 = min(height, int(y2 + pad))
    return x1, y1, max(x1 + 1, x2), max(y1 + 1, y2)


def detect_and_crop(
    model_path: Path,
    images: list[Path],
    output_dir: Path,
    imgsz: int,
    conf: float,
    device: str,
    crop_pad: float,
) -> list[DetectedBox]:
    crop_dir = output_dir / "crops"
    preview_dir = output_dir / "previews"
    crop_dir.mkdir(parents=True, exist_ok=True)
    preview_dir.mkdir(parents=True, exist_ok=True)

    model = YOLO(str(model_path))
    results = model.predict(
        source=[str(p) for p in images],
        imgsz=imgsz,
        conf=conf,
        device=device,
        verbose=False,
    )

    detected: list[DetectedBox] = []
    for result, image_path in zip(results, images):
        image = cv2.imread(str(image_path))
        if image is None:
            print(f"skip unreadable image: {image_path}")
            continue
        height, width = image.shape[:2]
        boxes = []
        if result.boxes is not None:
            boxes = sorted(
                zip(result.boxes.xyxy.tolist(), result.boxes.conf.tolist()),
                key=lambda item: (item[0][1], item[0][0]),
            )

        for i, (xyxy, score) in enumerate(boxes, 1):
            box = clamp_box(tuple(xyxy), (width, height), crop_pad)
            x1, y1, x2, y2 = box
            crop = image[y1:y2, x1:x2]
            crop_name = f"{image_path.stem}_box_{i:02d}.jpg"
            crop_path = crop_dir / crop_name
            cv2.imwrite(str(crop_path), crop, [int(cv2.IMWRITE_JPEG_QUALITY), 92])

            box_id = f"{image_path.stem}:box_{i:02d}"
            detected.append(
                DetectedBox(
                    image_path=image_path,
                    index=i,
                    confidence=float(score),
                    xyxy=box,
                    crop_path=crop_path,
                    box_id=box_id,
                )
            )

            cv2.rectangle(image, (x1, y1), (x2, y2), (0, 200, 0), 5)
            cv2.putText(
                image,
                f"{i} {score:.2f}",
                (x1 + 8, y1 + 36),
                cv2.FONT_HERSHEY_SIMPLEX,
                1.0,
                (0, 200, 0),
                3,
                cv2.LINE_AA,
            )

        cv2.imwrite(
            str(preview_dir / image_path.name),
            image,
            [int(cv2.IMWRITE_JPEG_QUALITY), 92],
        )

    return detected


def make_catalog_entry(
    box: DetectedBox,
    books: list[dict[str, Any]],
    lookup_books: bool,
    library_db_path: Path,
    google_fallback: bool,
    shelf_assignment: shelf_locator.ShelfAssignment | None = None,
    ocr_error: str | None = None,
    existing_ocr_ref: dict[str, Any] | None = None,
) -> dict[str, Any]:
    enriched_books = []
    for book in books:
        enriched = dict(book)
        lookup = None
        if lookup_books:
            lookup = book_lookup.lookup_title_metadata(
                enriched.get("title"),
                db_path=library_db_path,
                google_fallback=google_fallback,
            )
        enriched["book_lookup"] = lookup
        enriched_books.append(enriched)

    entry = {
        "box_id": box.box_id,
        "source_image": str(box.image_path),
        "crop_image": str(box.crop_path),
        "detector_confidence": box.confidence,
        "bbox_xyxy": list(box.xyxy),
        "crop_quality": box.quality.to_json() if getattr(box, "quality", None) else None,
        "shelf_id": shelf_assignment.shelf_id if shelf_assignment else None,
        "shelf_assignment": shelf_assignment.to_json() if shelf_assignment else None,
        "ocr_error": ocr_error,
        "existing_ocr_ref": existing_ocr_ref,
        "books": enriched_books,
    }
    return entry


def fingerprint_scope(box: Any, shelf_id: str | None) -> str | None:
    if not shelf_id:
        return None
    image = cv2.imread(str(box.image_path))
    if image is None:
        return None
    height, width = image.shape[:2]
    x1, y1, x2, y2 = box.xyxy
    normalized = (
        round(((x1 + x2) / 2) / width * 10), round(((y1 + y2) / 2) / height * 10),
        round((x2 - x1) / width * 10), round((y2 - y1) / height * 10),
    )
    return f"{shelf_id}:{':'.join(str(v) for v in normalized)}"


def open_fingerprint_db(path: Path | None) -> sqlite3.Connection | None:
    if path is None:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=10)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=10000")
    con.execute("""CREATE TABLE IF NOT EXISTS crop_fingerprints (
        fingerprint_scope TEXT PRIMARY KEY, phash TEXT NOT NULL, source_job TEXT,
        source_crop TEXT, titles_json TEXT NOT NULL, updated_at TEXT NOT NULL
    )""")
    return con


def find_persistent_crop(con: sqlite3.Connection | None, scope: str | None, phash: int) -> dict[str, Any] | None:
    if con is None or scope is None:
        return None
    row = con.execute("SELECT phash, source_job, source_crop, titles_json FROM crop_fingerprints WHERE fingerprint_scope = ?", [scope]).fetchone()
    if not row or bin(phash ^ int(row[0], 16)).count("1") > 6:
        return None
    return {"source": "persistent", "job_id": row[1], "crop_id": row[2], "titles": json.loads(row[3])}


def save_persistent_crop(con: sqlite3.Connection | None, scope: str | None, phash: int,
                         source_job: str, source_crop: str, titles: list[dict[str, Any]]) -> None:
    if con is None or scope is None:
        return
    con.execute("""INSERT INTO crop_fingerprints VALUES (?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(fingerprint_scope) DO UPDATE SET phash=excluded.phash,
        source_job=excluded.source_job, source_crop=excluded.source_crop,
        titles_json=excluded.titles_json, updated_at=excluded.updated_at""",
        [scope, f"{phash:016x}", source_job, source_crop, json.dumps(titles, ensure_ascii=False)])
    con.commit()


def main() -> int:
    args = parse_args()
    source_value = args.source or args.input or "data"
    source = resolve_path(source_value)
    model_path = resolve_path(args.model)
    library_db_path = resolve_path(args.library_db)
    output_dir = resolve_path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    fingerprint_db = open_fingerprint_db(resolve_path(args.fingerprint_db) if args.fingerprint_db else None)

    if args.video:
        video_path = resolve_path(args.video)
        frames_dir = output_dir / "frames"
        print(f"video : {video_path}")
        print(f"  blur_thresh={args.min_frame_blur}  scene_thresh={args.scene_change_threshold}  min_interval={args.video_interval}s")
        images = book_video.extract_keyframes(
            video_path, frames_dir,
            min_frame_blur=args.min_frame_blur,
            scene_change_threshold=args.scene_change_threshold,
            min_interval_sec=args.video_interval,
            max_frames=args.max_images,
        )
        if not images:
            raise RuntimeError(f"動画からキーフレームを抽出できませんでした: {video_path}")
        print(f"frames: {len(images)}")
        source = frames_dir
    else:
        images = book_detection.list_source_images(source, args.max_images)
        if not images:
            raise FileNotFoundError(f"画像が見つかりません: {source}")

    print(f"source: {source}")
    print(f"images: {len(images)}")
    print(f"model : {model_path}")
    print(f"db    : {library_db_path if library_db_path.exists() else '(not found)'}")
    print(f"out   : {output_dir}")

    boxes = book_detection.detect_and_crop(
        model_path=model_path,
        images=images,
        output_dir=output_dir,
        config=book_detection.DetectionConfig(
            imgsz=args.imgsz,
            conf=args.conf,
            device=args.device,
            crop_pad=args.crop_pad,
            min_blur_score=args.min_blur_score,
            min_short_edge=args.min_crop_short_edge,
            reject_edge_touch=args.reject_edge_touch,
            reject_edge_aspect=args.reject_edge_aspect,
            edge_wide_aspect=args.edge_wide_aspect,
            edge_tall_aspect=args.edge_tall_aspect,
        ),
    )
    print(f"boxes : {len(boxes)}")

    shelf_assignments: dict[str, shelf_locator.ShelfAssignment] = {}
    tag_detections: dict[str, list[dict[str, Any]]] = {}
    if args.apriltag_map:
        mapping_path = resolve_path(args.apriltag_map)
        print(f"tags  : {mapping_path}")
        boxes_by_image: dict[Path, list[book_detection.DetectedBox]] = {}
        for box in boxes:
            boxes_by_image.setdefault(box.image_path, []).append(box)
        for image_path, image_boxes in boxes_by_image.items():
            assignments, tags = shelf_locator.locate_shelves_for_boxes(
                image_path=image_path,
                boxes=image_boxes,
                mapping_path=mapping_path,
                max_tag_distance=args.max_tag_distance,
            )
            shelf_assignments.update(assignments)
            tag_detections[str(image_path)] = [
                {
                    "tag_id": tag.tag_id,
                    "center": [round(float(tag.center[0]), 2), round(float(tag.center[1]), 2)],
                    "angle_deg": round(tag.angle_deg, 2),
                    "orientation_status": tag.orientation_status,
                }
                for tag in tags
            ]
        assigned_count = sum(1 for item in shelf_assignments.values() if item.shelf_id)
        skipped_count = sum(1 for item in shelf_assignments.values() if item.status == "skipped")
        print(f"shelf: assigned={assigned_count} skipped={skipped_count}")

    # 動画モードの crop 重複スキップ用: 処理済み crop の phash を蓄積
    seen_crop_hashes: list[tuple[int, str, list[dict[str, Any]]]] = []
    is_video_mode = bool(args.video) or args.dedup_crops

    entries = []
    for i, box in enumerate(boxes, 1):
        action = "crop" if args.skip_ocr else "OCR"
        print(f"[{i}/{len(boxes)}] {action} {box.box_id} -> {box.crop_path.name}")
        books: list[dict[str, Any]] = []
        ocr_error = None
        existing_ocr_ref = None
        quality = getattr(box, "quality", None)
        skip_quality = bool(quality and not quality.readable and not args.include_low_quality)
        if skip_quality:
            ocr_error = f"skipped_low_quality: {','.join(quality.reasons)}"
            print(f"  skip: {ocr_error}")
        elif not args.skip_ocr:
            phash = None
            scope = None
            if is_video_mode and not args.no_dedup:
                # 動画モード: 同じ crop が別フレームで既に OCR 済みならスキップ
                crop_img = cv2.imread(str(box.crop_path))
                if crop_img is not None:
                    phash = book_video.crop_phash(crop_img)
                    session_match = next((item for item in seen_crop_hashes if book_video.is_duplicate_crop(phash, [item[0]])), None)
                    assignment = shelf_assignments.get(box.box_id)
                    scope = fingerprint_scope(box, assignment.shelf_id if assignment else None)
                    persistent_match = None if session_match else find_persistent_crop(fingerprint_db, scope, phash)
                    if session_match:
                        ocr_error = "skipped_duplicate_crop"
                        books = session_match[2]
                        existing_ocr_ref = {"source": "session", "crop_id": session_match[1]}
                        print("  skip: duplicate crop")
                    elif persistent_match:
                        ocr_error = "skipped_duplicate_crop"
                        books = persistent_match.pop("titles")
                        existing_ocr_ref = persistent_match
                        print("  skip: persistent duplicate crop")
                    else:
                        seen_crop_hashes.append((phash, box.box_id, books))
            if not ocr_error:
                try:
                    books = book_ocr.gemini_title_ocr(box.crop_path, args.gemini_model)
                    if phash is not None:
                        if books and seen_crop_hashes and seen_crop_hashes[-1][1] == box.box_id:
                            seen_crop_hashes[-1] = (phash, box.box_id, books)
                            save_persistent_crop(fingerprint_db, scope, phash, output_dir.name, box.box_id, books)
                        elif not books:
                            seen_crop_hashes = [item for item in seen_crop_hashes if item[1] != box.box_id]
                except Exception as exc:
                    ocr_error = str(exc)
                    seen_crop_hashes = [item for item in seen_crop_hashes if item[1] != box.box_id]
                    print(f"  OCR error: {ocr_error}")
            time.sleep(args.sleep)
        entries.append(
            make_catalog_entry(
                box=box,
                books=books,
                lookup_books=not args.no_lookup,
                library_db_path=library_db_path,
                google_fallback=args.google_fallback,
                shelf_assignment=shelf_assignments.get(box.box_id),
                ocr_error=ocr_error,
                existing_ocr_ref=existing_ocr_ref,
            )
        )

    if fingerprint_db is not None:
        fingerprint_db.close()

    catalog = {
        "source": str(source),
        "detector_model": str(model_path),
        "gemini_model": None if args.skip_ocr else args.gemini_model,
        "book_lookup": not args.no_lookup,
        "library_db": str(library_db_path) if library_db_path.exists() else None,
        "shelf_mapping": {
            "mapping_file": str(resolve_path(args.apriltag_map)),
            "tag_detections": tag_detections,
        } if args.apriltag_map else None,
        "entries": entries,
    }
    catalog_path = output_dir / args.catalog_name
    catalog_path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")

    book_count = sum(len(entry["books"]) for entry in entries)
    print(f"books : {book_count}")
    print(f"json  : {catalog_path}")
    print(f"crops : {output_dir / 'crops'}")
    print(f"prev  : {output_dir / 'previews'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
