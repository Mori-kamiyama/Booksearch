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
from pathlib import Path
from typing import Any

import boto3
import cv2
import numpy as np
from ultralytics import YOLO

BUCKET = os.environ["BUCKET"]
JOBS_TABLE = os.environ["JOBS_TABLE"]
CROPS_TABLE = os.environ["CROPS_TABLE"]
OCR_QUEUE_URL = os.environ["OCR_QUEUE_URL"]
LOOKUP_QUEUE_URL = os.environ["LOOKUP_QUEUE_URL"]
TASK_ROOT = os.environ.get("LAMBDA_TASK_ROOT", ".")

MODEL_PATH = Path(TASK_ROOT) / "assets" / "yolo_model.pt"
APRILTAG_MAP_PATH = Path(TASK_ROOT) / "assets" / "apriltag_shelf_map.json"

# YOLO はコンテナの warm 再利用でロードしっぱなしにする
_yolo_model: YOLO | None = None
s3 = boto3.client("s3")
sqs = boto3.client("sqs")
ddb = boto3.resource("dynamodb")
jobs_table = ddb.Table(JOBS_TABLE)
crops_table = ddb.Table(CROPS_TABLE)


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
    if x1 <= margin_x: edge_touch.append("left")
    if y1 <= margin_y: edge_touch.append("top")
    if x2 >= width - margin_x: edge_touch.append("right")
    if y2 >= height - margin_y: edge_touch.append("bottom")

    # 画像サイズ相対の閾値。スマホ等の小さい入力でも crop が落ちないように。
    # MIN_SHORT_EDGE_PX, MIN_SHORT_EDGE_RATIO, MIN_BLUR_SCORE は環境変数で上書き可。
    min_edge_abs = int(os.environ.get("MIN_SHORT_EDGE_PX", "80"))
    min_edge_ratio = float(os.environ.get("MIN_SHORT_EDGE_RATIO", "0.06"))
    min_short_edge = max(min_edge_abs, int(min(width, height) * min_edge_ratio))
    min_blur = float(os.environ.get("MIN_BLUR_SCORE", "50"))

    reasons = []
    if blur_score < min_blur: reasons.append("blurry")
    if short_edge < min_short_edge: reasons.append("too_small")
    if edge_touch and aspect_ratio >= 1.45: reasons.append("edge_wide")
    elif edge_touch and aspect_ratio <= 0.45: reasons.append("edge_tall")

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
        if px < cx and py < cy: return "top_left"
        if px >= cx and py < cy: return "top_right"
        if px >= cx and py >= cy: return "bottom_right"
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
    unique = {v["shelf_id"] for v in votes}
    if len(unique) == 1:
        return {"shelf_id": votes[0]["shelf_id"], "status": "assigned", "votes": votes}
    return {"shelf_id": None, "status": "skipped",
            "reason": "conflicting", "votes": votes}


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


def process_job(job_id: str, image_key: str) -> None:
    print(f"[yolo] job_id={job_id} image_key={image_key}")

    # 1. S3 → /tmp
    local_image = f"/tmp/{job_id}_input{Path(image_key).suffix}"
    s3.download_file(BUCKET, image_key, local_image)

    img = cv2.imread(local_image)
    if img is None:
        raise RuntimeError(f"unreadable image: {image_key}")
    height, width = img.shape[:2]

    # 2. YOLO 推論
    model = get_model()
    results = model.predict(source=local_image, imgsz=640, conf=0.25, device="cpu", verbose=False)
    boxes = []
    if results and results[0].boxes is not None:
        boxes = sorted(
            zip(results[0].boxes.xyxy.tolist(), results[0].boxes.conf.tolist()),
            key=lambda item: (item[0][1], item[0][0]),
        )
    print(f"[yolo] detected {len(boxes)} boxes")

    # 3. AprilTag (optional)
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

    # 4. crop, S3 PUT, DDB
    image_stem = Path(image_key).stem
    crop_records = []
    readable_count = 0
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

        item = {
            "job_id": job_id,
            "crop_id": crop_id,
            "crop_key": crop_key,
            "bbox_xyxy": [int(v) for v in box],
            "detector_confidence": round(float(score), 4),
            "quality": quality,
            "status": "ocr_pending" if quality["readable"] else "skipped_low_quality",
            "shelf": shelf,
        }
        crops_table.put_item(Item=ddb_safe(item))
        crop_records.append(item)
        if quality["readable"]:
            readable_count += 1

    # 5. jobs テーブル更新（apriltag 診断情報を含む）
    skip_reason_counts: dict[str, int] = {}
    for r in crop_records:
        for reason in r["quality"].get("reasons", []):
            skip_reason_counts[reason] = skip_reason_counts.get(reason, 0) + 1
    diag = {
        "apriltag": tag_diag,
        "skip_reasons": skip_reason_counts,
        "readable_count": readable_count,
        "total_crops": len(crop_records),
    }
    jobs_table.update_item(
        Key={"job_id": job_id},
        UpdateExpression="SET #s = :s, crop_total = :n, ocr_total = :ot, ocr_done = :z, "
                         "image_width = :w, image_height = :h, #d = :d",
        ExpressionAttributeNames={"#s": "status", "#d": "diagnostics"},
        ExpressionAttributeValues={
            ":s": "ocr_pending" if readable_count > 0 else "no_readable_crops",
            ":n": len(crop_records),
            ":ot": readable_count,
            ":z": 0,
            ":w": width,
            ":h": height,
            ":d": ddb_safe(diag),
        },
    )

    # 6. fan-out: 各 readable crop を OCR queue へ
    for rec in crop_records:
        if rec["quality"]["readable"]:
            sqs.send_message(
                QueueUrl=OCR_QUEUE_URL,
                MessageBody=json.dumps({
                    "job_id": job_id,
                    "crop_id": rec["crop_id"],
                    "crop_key": rec["crop_key"],
                }),
            )

    # 全 crop が unreadable なら直接 lookup_queue へ (空 catalog 生成)
    if readable_count == 0 and crop_records:
        sqs.send_message(
            QueueUrl=LOOKUP_QUEUE_URL,
            MessageBody=json.dumps({"job_id": job_id}),
        )
    elif not crop_records:
        # 検出 0 件: 即 lookup へ
        jobs_table.update_item(
            Key={"job_id": job_id},
            UpdateExpression="SET #s = :s, crop_total = :n",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":s": "no_detection", ":n": 0},
        )
        sqs.send_message(
            QueueUrl=LOOKUP_QUEUE_URL,
            MessageBody=json.dumps({"job_id": job_id}),
        )


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
        try:
            process_job(body["job_id"], body["image_key"])
        except Exception as e:
            print(f"[yolo] ERROR: {e}", file=sys.stderr)
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
