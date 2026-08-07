"""YOLO Worker Lambda

入力: SQS yolo-queue メッセージ
  body = {"job_id": "...", "image_key": "uploads/<job_id>/upload.jpg"}

処理:
  1. S3 から画像を /tmp に取得
  2. YOLO 推論で box 検出
  3. crop 生成、品質判定
  4. AprilTag 検出と shelf_id 割当
  5. crop を S3 に PUT (crops/<job_id>/<crop_id>.jpg)
  6. DynamoDB crops テーブルに 1 行 / crop
  7. jobs テーブル更新: crop_total = N、status = "ocr_pending"
  8. 各 crop に対して OCR queue に投入
"""

from __future__ import annotations

import json
import math
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import boto3
import cv2
import numpy as np
from botocore.exceptions import ClientError
from ultralytics import YOLO

BUCKET = os.environ["BUCKET"]
JOBS_TABLE = os.environ["JOBS_TABLE"]
CROPS_TABLE = os.environ["CROPS_TABLE"]
FINGERPRINTS_TABLE = os.environ.get("CROP_FINGERPRINTS_TABLE")
OCR_QUEUE_URL = os.environ["OCR_QUEUE_URL"]
LOOKUP_QUEUE_URL = os.environ["LOOKUP_QUEUE_URL"]
TASK_ROOT = os.environ.get("LAMBDA_TASK_ROOT", ".")
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
# Must match the YoloQueue RedrivePolicy so the last attempt is recognized
# before the message is moved to the DLQ.
MAX_RECEIVE_COUNT = int(os.environ.get("YOLO_MAX_RECEIVE_COUNT", "2"))

MODEL_PATH = Path(TASK_ROOT) / "assets" / "yolo_model.pt"
APRILTAG_MAP_PATH = Path(TASK_ROOT) / "assets" / "apriltag_library_map.json"

# YOLO はコンテナの warm 再利用でロードしっぱなしにする
_yolo_model: YOLO | None = None
s3 = boto3.client("s3")
sqs = boto3.client("sqs")
ddb = boto3.resource("dynamodb")
jobs_table = ddb.Table(JOBS_TABLE)
crops_table = ddb.Table(CROPS_TABLE)
fingerprints_table = ddb.Table(FINGERPRINTS_TABLE) if FINGERPRINTS_TABLE else None


def get_model() -> YOLO:
    global _yolo_model
    if _yolo_model is None:
        print(f"loading YOLO model: {MODEL_PATH}")
        _yolo_model = YOLO(str(MODEL_PATH))
    return _yolo_model


# ---------- crop quality ----------
def assess_quality(crop: np.ndarray, box: tuple[int, int, int, int],
                   image_size: tuple[int, int]) -> dict[str, Any]:
    width, height = image_size
    x1, y1, x2, y2 = box
    h, w = crop.shape[:2]
    short_edge = min(w, h)
    aspect_ratio = w / h if h else 0.0
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    blur_score = float(cv2.Laplacian(gray, cv2.CV_64F).var())

    edge_touch = []
    margin_x = max(2, int(width * 0.005))
    margin_y = max(2, int(height * 0.005))
    if x1 <= margin_x:
        edge_touch.append("left")
    if y1 <= margin_y:
        edge_touch.append("top")
    if x2 >= width - margin_x:
        edge_touch.append("right")
    if y2 >= height - margin_y:
        edge_touch.append("bottom")

    # 画像サイズ相対の閾値。スマホ等の小さい入力でも crop が落ちないように。
    # MIN_SHORT_EDGE_PX, MIN_SHORT_EDGE_RATIO, MIN_BLUR_SCORE は環境変数で上書き可。
    min_edge_abs = int(os.environ.get("MIN_SHORT_EDGE_PX", "80"))
    min_edge_ratio = float(os.environ.get("MIN_SHORT_EDGE_RATIO", "0.06"))
    min_short_edge = max(min_edge_abs, int(min(width, height) * min_edge_ratio))
    min_blur = float(os.environ.get("MIN_BLUR_SCORE", "50"))

    reasons = []
    if blur_score < min_blur:
        reasons.append("blurry")
    if short_edge < min_short_edge:
        reasons.append("too_small")
    if edge_touch and aspect_ratio >= 1.45:
        reasons.append("edge_wide")
    elif edge_touch and aspect_ratio <= 0.45:
        reasons.append("edge_tall")

    return {
        "blur_score": round(blur_score, 2),
        "short_edge": short_edge,
        "aspect_ratio": round(aspect_ratio, 3),
        "edge_touch": edge_touch,
        "readable": not reasons,
        "reasons": reasons,
    }


# ---------- AprilTag ----------
ARUCO_DICTIONARIES = {
    "DICT_4X4_50": cv2.aruco.DICT_4X4_50,
    "DICT_4X4_100": cv2.aruco.DICT_4X4_100,
    "DICT_5X5_100": cv2.aruco.DICT_5X5_100,
    "DICT_6X6_250": cv2.aruco.DICT_6X6_250,
    "DICT_APRILTAG_16h5": cv2.aruco.DICT_APRILTAG_16h5,
    "DICT_APRILTAG_25h9": cv2.aruco.DICT_APRILTAG_25h9,
    "DICT_APRILTAG_36h10": cv2.aruco.DICT_APRILTAG_36h10,
    "DICT_APRILTAG_36h11": cv2.aruco.DICT_APRILTAG_36h11,
}


@dataclass
class DetectedTag:
    tag_id: int
    center: np.ndarray
    angle_deg: float
    orientation_status: str

    def quadrant_for_point(self, point) -> str:
        cx, cy = float(self.center[0]), float(self.center[1])
        px, py = point
        if px < cx and py < cy:
            return "top_left"
        if px >= cx and py < cy:
            return "top_right"
        if px >= cx and py >= cy:
            return "bottom_right"
        return "bottom_left"

    def distance_to_point(self, point) -> float:
        return float(np.linalg.norm(np.array(point, dtype=np.float32) - self.center))


def detect_tags(image: np.ndarray, mapping: dict[str, Any]) -> tuple[list[DetectedTag], dict[str, Any]]:
    """AprilTag/ArUco を検出。mapping の辞書で失敗したら全辞書を試行し、
    最も多く検出できた辞書を採用する。検出サマリも返す。"""
    primary = mapping.get("dictionary", "DICT_APRILTAG_36h11")
    candidates = [primary] + [k for k in ARUCO_DICTIONARIES if k != primary]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    best: tuple[list[Any], Any, str] = ([], None, primary)
    diagnostics: dict[str, Any] = {"tried": [], "selected": None, "raw_ids": []}
    for name in candidates:
        if name not in ARUCO_DICTIONARIES:
            continue
        aruco_dict = cv2.aruco.getPredefinedDictionary(ARUCO_DICTIONARIES[name])
        detector = cv2.aruco.ArucoDetector(aruco_dict)
        corners, ids, _ = detector.detectMarkers(gray)
        n = 0 if ids is None else len(ids)
        diagnostics["tried"].append({"dict": name, "count": n})
        if ids is not None and n > len(best[0]):
            best = (list(corners), ids, name)
        # 1辞書で十分検出できれば打ち切り
        if ids is not None and n >= 2:
            break
    corners, ids, used_dict = best
    diagnostics["selected"] = used_dict
    if ids is None or len(ids) == 0:
        return [], diagnostics
    diagnostics["raw_ids"] = [int(x) for x in ids.flatten()]
    tag_cfg = mapping.get("tags", {})
    out = []
    for marker, raw_id in zip(corners, ids.flatten()):
        pts = marker.reshape(4, 2).astype(np.float32)
        tl, tr, _, _ = pts
        x_axis = tr - tl
        nx = float(np.linalg.norm(x_axis))
        if nx == 0:
            continue
        x_axis /= nx
        angle = math.degrees(math.atan2(float(x_axis[1]), float(x_axis[0])))
        cfg = tag_cfg.get(str(int(raw_id)), {})
        expected = cfg.get("expected_angle_deg")
        if expected is None:
            status = "unchecked"
        else:
            tol = float(cfg.get("angle_tolerance_deg", 35.0))
            delta = abs((angle - float(expected) + 180.0) % 360.0 - 180.0)
            status = "ok" if delta <= tol else "mismatch"
        out.append(DetectedTag(
            tag_id=int(raw_id), center=pts.mean(axis=0),
            angle_deg=angle, orientation_status=status,
        ))
    return out, diagnostics


def assign_shelf(box_xyxy, tags, mapping, max_distance=None):
    cx = (box_xyxy[0] + box_xyxy[2]) / 2
    cy = (box_xyxy[1] + box_xyxy[3]) / 2
    tag_cfg = mapping.get("tags", {})

    if max_distance is None:
        diag = math.hypot(box_xyxy[2]-box_xyxy[0], box_xyxy[3]-box_xyxy[1])
        max_distance = diag * float(mapping.get("auto_distance_scale", 1.25))

    votes = []
    for tag in tags:
        cfg = tag_cfg.get(str(tag.tag_id))
        if not cfg:
            continue
        d = tag.distance_to_point((cx, cy))
        if d > max_distance:
            continue
        if tag.orientation_status == "mismatch":
            continue
        q = tag.quadrant_for_point((cx, cy))
        shelf = (cfg.get("quadrants") or {}).get(q)
        if shelf:
            votes.append({"tag_id": tag.tag_id, "shelf_id": shelf, "quadrant": q,
                          "distance_px": round(d, 2)})
    if not votes:
        return None
    votes.sort(key=lambda v: v["distance_px"])
    unique = {v["shelf_id"] for v in votes}
    provenance = {
        "map_id": mapping.get("map_id"),
        "coordinate_schema_version": mapping.get("coordinate_schema_version"),
    }
    if len(unique) == 1:
        return {"shelf_id": votes[0]["shelf_id"], "status": "assigned", "votes": votes, **provenance}
    return {
        "shelf_id": votes[0]["shelf_id"],
        "status": "assigned",
        "reason": "nearest_of_conflicting_votes",
        "conflicting_shelf_ids": sorted(unique),
        "votes": votes,
        **provenance,
    }


# ---------- main ----------
def clamp_box(xyxy, image_size, pad_ratio=0.02):
    width, height = image_size
    x1, y1, x2, y2 = xyxy
    pad = max(x2 - x1, y2 - y1) * pad_ratio
    x1 = max(0, int(x1 - pad))
    y1 = max(0, int(y1 - pad))
    x2 = min(width, int(x2 + pad))
    y2 = min(height, int(y2 + pad))
    return x1, y1, max(x1 + 1, x2), max(y1 + 1, y2)


def crop_phash(crop: np.ndarray, hash_size: int = 8) -> int:
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    resized = cv2.resize(gray, (hash_size, hash_size), interpolation=cv2.INTER_AREA)
    average = float(resized.mean())
    value = 0
    for pixel in resized.flatten():
        value = (value << 1) | int(pixel > average)
    return value


def hash_distance(left: int, right: int) -> int:
    return (left ^ right).bit_count()


def fingerprint_scope(shelf: dict[str, Any] | None, box, image_size) -> str | None:
    if not shelf or not shelf.get("shelf_id"):
        return None
    width, height = image_size
    x1, y1, x2, y2 = box
    # Position and size are bucketed so minor camera motion remains stable, while
    # moving/adding/removing books changes either the bucket or perceptual hash.
    normalized = (
        round(((x1 + x2) / 2) / width * 10),
        round(((y1 + y2) / 2) / height * 10),
        round((x2 - x1) / width * 10),
        round((y2 - y1) / height * 10),
    )
    return f"{shelf['shelf_id']}:{':'.join(str(v) for v in normalized)}"


def persistent_duplicate(scope: str | None, phash: int) -> dict[str, Any] | None:
    if not scope or not fingerprints_table:
        return None
    item = fingerprints_table.get_item(Key={"fingerprint_scope": scope}).get("Item")
    if not item:
        return None
    previous = int(str(item.get("phash", "0")), 16)
    if hash_distance(phash, previous) <= int(os.environ.get("CROP_HASH_DISTANCE", "6")):
        return item
    return None


def process_frame(job_id: str, image_key: str, frame_index: int,
                  seen_hashes: list[tuple[int, str]], local_path: str | None = None
                  ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    print(f"[yolo] job_id={job_id} image_key={image_key}")
    local_image = local_path or f"/tmp/{job_id}_{frame_index:04d}{Path(image_key).suffix or '.jpg'}"
    if local_path is None:
        s3.download_file(BUCKET, image_key, local_image)
    img = cv2.imread(local_image)
    if img is None:
        raise RuntimeError(f"unreadable image: {image_key}")
    height, width = img.shape[:2]
    model = get_model()
    results = model.predict(source=local_image, imgsz=640, conf=0.25, device="cpu", verbose=False)
    boxes = []
    if results and results[0].boxes is not None:
        boxes = sorted(
            zip(results[0].boxes.xyxy.tolist(), results[0].boxes.conf.tolist()),
            key=lambda item: (item[0][1], item[0][0]),
        )
    print(f"[yolo] detected {len(boxes)} boxes")

    mapping = None
    tags: list[DetectedTag] = []
    tag_diag: dict[str, Any] = {}
    if APRILTAG_MAP_PATH.exists():
        try:
            mapping = json.loads(APRILTAG_MAP_PATH.read_text())
            tags, tag_diag = detect_tags(img, mapping)
            print(f"[yolo] detected {len(tags)} apriltags via {tag_diag.get('selected')} ids={tag_diag.get('raw_ids')}")
        except Exception as e:
            print(f"[yolo] apriltag failed: {e}")
            tag_diag = {"error": str(e)}

    image_stem = f"frame_{frame_index:06d}_{Path(image_key).stem[:12]}"
    crop_records: list[dict[str, Any]] = []
    for i, (xyxy, score) in enumerate(boxes, 1):
        box = clamp_box(tuple(xyxy), (width, height))
        x1, y1, x2, y2 = box
        crop = img[y1:y2, x1:x2]
        quality = assess_quality(crop, box, (width, height))
        crop_id = f"{image_stem}_box_{i:02d}"
        crop_key = f"crops/{job_id}/{crop_id}.jpg"

        _, buf = cv2.imencode(".jpg", crop, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
        s3.put_object(Bucket=BUCKET, Key=crop_key, Body=buf.tobytes(),
                      ContentType="image/jpeg")

        shelf = assign_shelf(box, tags, mapping) if mapping else None
        phash = crop_phash(crop)
        scope = fingerprint_scope(shelf, box, (width, height))
        duplicate_ref = None
        for previous_hash, previous_crop_id in seen_hashes:
            if hash_distance(phash, previous_hash) <= int(os.environ.get("CROP_HASH_DISTANCE", "6")):
                duplicate_ref = {"job_id": job_id, "crop_id": previous_crop_id, "source": "session"}
                break
        persisted = None if duplicate_ref else persistent_duplicate(scope, phash)
        if persisted:
            duplicate_ref = {
                "job_id": persisted.get("job_id"), "crop_id": persisted.get("crop_id"),
                "source": "persistent", "titles": persisted.get("titles") or [],
            }

        status = "ocr_pending" if quality["readable"] else "skipped_low_quality"
        ocr_error = None
        titles: list[dict[str, Any]] = []
        if quality["readable"] and duplicate_ref:
            status = "skipped_duplicate_crop"
            ocr_error = "skipped_duplicate_crop"
            titles = duplicate_ref.get("titles") or []

        item = {
            "job_id": job_id,
            "crop_id": crop_id,
            "crop_key": crop_key,
            "bbox_xyxy": [int(v) for v in box],
            "detector_confidence": round(float(score), 4),
            "quality": quality,
            "status": status,
            "shelf": shelf,
            "fingerprint_scope": scope,
            "phash": f"{phash:016x}",
        }
        if ocr_error:
            item["ocr_error"] = ocr_error
            item["existing_ocr_ref"] = duplicate_ref
            item["titles"] = titles
        crops_table.put_item(Item=ddb_safe(item))
        crop_records.append(item)
        if quality["readable"] and not duplicate_ref:
            seen_hashes.append((phash, crop_id))
    return crop_records, {"image_key": image_key, "width": width, "height": height, "apriltag": tag_diag}


def extract_video_frames(video_path: str, job_id: str) -> list[str]:
    """Extract a bounded, time-spaced set of JPEG frames from an uploaded video."""
    interval = max(0.1, float(os.environ.get("VIDEO_FRAME_INTERVAL_SEC", "0.5")))
    max_frames = max(1, int(os.environ.get("VIDEO_MAX_FRAMES", "120")))
    capture = cv2.VideoCapture(video_path)
    if not capture.isOpened():
        raise RuntimeError(f"unreadable video: {video_path}")

    frames: list[str] = []
    next_sample = 0.0
    frame_number = 0
    try:
        while len(frames) < max_frames:
            ok, image = capture.read()
            if not ok:
                break
            fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
            timestamp = frame_number / fps if fps > 0 else float(capture.get(cv2.CAP_PROP_POS_MSEC) or 0.0) / 1000.0
            if timestamp + 1e-6 >= next_sample:
                frame_path = f"/tmp/{job_id}_video_{len(frames) + 1:04d}.jpg"
                if not cv2.imwrite(frame_path, image, [int(cv2.IMWRITE_JPEG_QUALITY), 92]):
                    raise RuntimeError(f"failed to write video frame: {frame_path}")
                frames.append(frame_path)
                next_sample = timestamp + interval
            frame_number += 1
    finally:
        capture.release()

    if not frames:
        raise RuntimeError(f"video contains no readable frames: {video_path}")
    print(f"[yolo] extracted {len(frames)} video frames from {video_path}")
    return frames


def queue_final_lookup_if_ready(job_id: str, item: dict[str, Any]) -> None:
    if not item.get("scan_closed"):
        return
    if int(item.get("processed_frames", 0)) < int(item.get("accepted_frames", 0)):
        return
    if int(item.get("ocr_done", 0)) < int(item.get("ocr_total", 0)):
        return
    try:
        jobs_table.update_item(
            Key={"job_id": job_id},
            UpdateExpression="SET final_lookup_queued = :yes, #s = :pending",
            ConditionExpression="attribute_not_exists(final_lookup_queued)",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":yes": True, ":pending": "lookup_pending"},
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return
        raise
    try:
        sqs.send_message(
            QueueUrl=LOOKUP_QUEUE_URL,
            MessageBody=json.dumps({"job_id": job_id, "incremental": False}),
        )
    except Exception:
        jobs_table.update_item(
            Key={"job_id": job_id},
            UpdateExpression="SET #s = :processing REMOVE final_lookup_queued",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":processing": "processing"},
        )
        raise


def abandon_live_frame(job_id: str, frame_key: str | None, reason: str) -> None:
    """Give up on one live frame without stalling the session.

    processed_frames must keep up with accepted_frames, otherwise the session
    never satisfies the final-lookup condition and the job stays in
    `processing` forever. A dropped frame costs the books in that frame only.
    """
    print(f"[yolo] abandoning frame {job_id}/{frame_key}: {reason}", file=sys.stderr)
    if not frame_key:
        return
    try:
        updated = jobs_table.update_item(
            Key={"job_id": job_id},
            UpdateExpression="SET updated_at = :u ADD processed_frame_keys :frame, "
                             "processed_frames :one, failed_frames :one",
            ConditionExpression="attribute_not_exists(processed_frame_keys) "
                                "OR NOT contains(processed_frame_keys, :frame_key)",
            ExpressionAttributeValues={
                ":frame": {frame_key}, ":frame_key": frame_key, ":one": 1,
                ":u": datetime.now(timezone.utc).isoformat(),
            },
            ReturnValues="ALL_NEW",
        )
        item = updated.get("Attributes", {})
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
            raise
        item = jobs_table.get_item(Key={"job_id": job_id}).get("Item") or {}
    queue_final_lookup_if_ready(job_id, item)


def process_job(job_id: str, image_keys: list[str], incremental: bool = False) -> None:
    if not image_keys:
        raise RuntimeError("image_keys is empty")
    if incremental:
        existing_job = jobs_table.get_item(Key={"job_id": job_id}).get("Item") or {}
        if image_keys[0] in (existing_job.get("processed_frame_keys") or set()):
            print(f"[yolo] frame already processed {job_id}/{image_keys[0]}")
            return
    crop_records: list[dict[str, Any]] = []
    frame_diagnostics = []
    seen_hashes: list[tuple[int, str]] = []
    for index, image_key in enumerate(image_keys, 1):
        suffix = Path(image_key).suffix.lower()
        if suffix in VIDEO_EXTENSIONS:
            video_path = f"/tmp/{job_id}_{index:04d}{suffix}"
            s3.download_file(BUCKET, image_key, video_path)
            frame_paths = extract_video_frames(video_path, job_id)
            try:
                for frame_index, frame_path in enumerate(frame_paths, index):
                    records, diagnostics = process_frame(job_id, image_key, frame_index, seen_hashes, frame_path)
                    crop_records.extend(records)
                    frame_diagnostics.append(diagnostics)
            finally:
                Path(video_path).unlink(missing_ok=True)
                for frame_path in frame_paths:
                    Path(frame_path).unlink(missing_ok=True)
        else:
            records, diagnostics = process_frame(job_id, image_key, index, seen_hashes)
            crop_records.extend(records)
            frame_diagnostics.append(diagnostics)

    ocr_records = [r for r in crop_records if r["status"] == "ocr_pending"]
    duplicate_count = sum(r["status"] == "skipped_duplicate_crop" for r in crop_records)
    skip_reason_counts: dict[str, int] = {}
    for r in crop_records:
        for reason in r["quality"].get("reasons", []):
            skip_reason_counts[reason] = skip_reason_counts.get(reason, 0) + 1
    diag = {
        "frames": frame_diagnostics,
        "skip_reasons": skip_reason_counts,
        "readable_count": len(ocr_records),
        "duplicate_count": duplicate_count,
        "total_crops": len(crop_records),
    }
    first_frame = frame_diagnostics[0]
    terminal_without_ocr = "no_detection" if not crop_records else "no_readable_crops"
    if incremental:
        frame_key = image_keys[0]
        try:
            updated = jobs_table.update_item(
                Key={"job_id": job_id},
                UpdateExpression="SET image_width = :w, image_height = :h, latest_diagnostics = :d, updated_at = :u "
                                 "ADD processed_frame_keys :frame, processed_frames :one, crop_total :n, ocr_total :ot",
                ConditionExpression="attribute_not_exists(processed_frame_keys) OR NOT contains(processed_frame_keys, :frame_key)",
                ExpressionAttributeValues={
                    ":frame": {frame_key}, ":frame_key": frame_key, ":one": 1,
                    ":n": len(crop_records), ":ot": len(ocr_records),
                    ":w": first_frame["width"], ":h": first_frame["height"],
                    ":d": ddb_safe(diag), ":u": datetime.now(timezone.utc).isoformat(),
                },
                ReturnValues="ALL_NEW",
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                print(f"[yolo] frame already processed {job_id}/{frame_key}")
                return
            raise
        job_item = updated.get("Attributes", {})
    else:
        jobs_table.update_item(
            Key={"job_id": job_id},
            UpdateExpression="SET #s = :s, crop_total = :n, ocr_total = :ot, ocr_done = :z, "
                             "image_width = :w, image_height = :h, #d = :d",
            ExpressionAttributeNames={"#s": "status", "#d": "diagnostics"},
            ExpressionAttributeValues={
                ":s": "ocr_pending" if ocr_records else terminal_without_ocr,
                ":n": len(crop_records),
                ":ot": len(ocr_records),
                ":z": 0,
                ":w": first_frame["width"],
                ":h": first_frame["height"],
                ":d": ddb_safe(diag),
            },
        )
        job_item = {}

    for rec in ocr_records:
        sqs.send_message(
            QueueUrl=OCR_QUEUE_URL,
            MessageBody=json.dumps({
                "job_id": job_id, "crop_id": rec["crop_id"], "crop_key": rec["crop_key"],
                "fingerprint_scope": rec.get("fingerprint_scope"), "phash": rec.get("phash"),
                "incremental": incremental,
            }),
        )
    if incremental:
        # Cached titles and zero-detection frames still need to refresh the
        # partial catalog/progress visible in the camera UI.
        sqs.send_message(
            QueueUrl=LOOKUP_QUEUE_URL,
            MessageBody=json.dumps({"job_id": job_id, "incremental": True}),
        )
        queue_final_lookup_if_ready(job_id, job_item)
    elif not ocr_records:
        sqs.send_message(QueueUrl=LOOKUP_QUEUE_URL, MessageBody=json.dumps({"job_id": job_id}))


def ddb_safe(item):
    """floats を Decimal に変換せず、DDB の制約を回避する簡易版"""
    from decimal import Decimal
    def conv(v):
        if isinstance(v, float):
            return Decimal(str(v))
        if isinstance(v, list):
            return [conv(x) for x in v]
        if isinstance(v, dict):
            return {k: conv(x) for k, x in v.items()}
        return v
    return conv(item)


def handler(event, context):
    for rec in event.get("Records", []):
        body = json.loads(rec["body"])
        incremental = bool(body.get("incremental"))
        image_keys = body.get("image_keys") or [body["image_key"]]
        try:
            process_job(body["job_id"], image_keys, incremental)
        except Exception as e:
            print(f"[yolo] ERROR: {e}", file=sys.stderr)
            if incremental:
                # One bad frame out of hundreds must not fail the whole live
                # session. Retry while SQS still has attempts left, then drop
                # just this frame so the session can still finish.
                receive_count = int(rec.get("attributes", {}).get("ApproximateReceiveCount", "1"))
                if receive_count < MAX_RECEIVE_COUNT:
                    raise
                abandon_live_frame(body["job_id"], image_keys[0] if image_keys else None, str(e))
                continue
            try:
                jobs_table.update_item(
                    Key={"job_id": body["job_id"]},
                    UpdateExpression="SET #s = :s, #e = :e",
                    ExpressionAttributeNames={"#s": "status", "#e": "error"},
                    ExpressionAttributeValues={":s": "failed", ":e": str(e)},
                )
            except Exception:
                pass
            raise
    return {"ok": True}
