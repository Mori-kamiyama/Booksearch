"""YOLO image recognition and crop utilities for book boxes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


@dataclass(frozen=True)
class BoxQuality:
    """Cheap image-quality signals used before sending crops to OCR."""

    blur_score: float
    short_edge: int
    aspect_ratio: float
    edge_touch: list[str]
    readable: bool
    reasons: list[str]

    def to_json(self) -> dict[str, object]:
        return {
            "blur_score": round(self.blur_score, 2),
            "short_edge": self.short_edge,
            "aspect_ratio": round(self.aspect_ratio, 3),
            "edge_touch": self.edge_touch,
            "readable": self.readable,
            "reasons": self.reasons,
        }


@dataclass(frozen=True)
class DetectedBox:
    """A detected book box and the crop written for it."""

    image_path: Path
    index: int
    confidence: float
    xyxy: tuple[int, int, int, int]
    crop_path: Path
    box_id: str
    quality: BoxQuality | None = None


@dataclass(frozen=True)
class DetectionConfig:
    """Runtime options for YOLO box detection."""

    imgsz: int = 640
    conf: float = 0.25
    device: str = "mps"
    crop_pad: float = 0.02
    jpeg_quality: int = 92
    min_blur_score: float = 150.0
    min_short_edge: int = 550
    reject_edge_touch: bool = False
    reject_edge_aspect: bool = True
    edge_wide_aspect: float = 1.45
    edge_tall_aspect: float = 0.45


def assess_crop_quality(
    crop: object,
    box: tuple[int, int, int, int],
    image_size: tuple[int, int],
    min_blur_score: float = 150.0,
    min_short_edge: int = 550,
    reject_edge_touch: bool = False,
    reject_edge_aspect: bool = True,
    edge_wide_aspect: float = 1.45,
    edge_tall_aspect: float = 0.45,
) -> BoxQuality:
    """Return conservative readability signals for a crop image."""

    import cv2

    width, height = image_size
    x1, y1, x2, y2 = box
    crop_height, crop_width = crop.shape[:2]
    short_edge = min(crop_width, crop_height)
    aspect_ratio = crop_width / crop_height if crop_height else 0.0
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

    reasons = []
    if blur_score < min_blur_score:
        reasons.append("blurry")
    if short_edge < min_short_edge:
        reasons.append("too_small")
    if reject_edge_touch and edge_touch:
        reasons.append("edge_touch")
    if reject_edge_aspect and edge_touch:
        if aspect_ratio >= edge_wide_aspect:
            reasons.append("edge_wide")
        elif aspect_ratio <= edge_tall_aspect:
            reasons.append("edge_tall")

    return BoxQuality(
        blur_score=blur_score,
        short_edge=short_edge,
        aspect_ratio=aspect_ratio,
        edge_touch=edge_touch,
        readable=not reasons,
        reasons=reasons,
    )


def list_source_images(source: Path, max_images: int | None = None) -> list[Path]:
    """Return image files from a file or a flat directory."""

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
    """Pad and clamp a YOLO xyxy box to image bounds."""

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
    config: DetectionConfig | None = None,
) -> list[DetectedBox]:
    """Detect book boxes with YOLO, save crops and preview images."""

    import cv2
    from ultralytics import YOLO

    config = config or DetectionConfig()
    crop_dir = output_dir / "crops"
    preview_dir = output_dir / "previews"
    crop_dir.mkdir(parents=True, exist_ok=True)
    preview_dir.mkdir(parents=True, exist_ok=True)

    model = YOLO(str(model_path))
    results = model.predict(
        source=[str(p) for p in images],
        imgsz=config.imgsz,
        conf=config.conf,
        device=config.device,
        verbose=False,
    )

    detected: list[DetectedBox] = []
    for result, image_path in zip(results, images):
        image = cv2.imread(str(image_path))
        if image is None:
            continue

        height, width = image.shape[:2]
        boxes = []
        if result.boxes is not None:
            boxes = sorted(
                zip(result.boxes.xyxy.tolist(), result.boxes.conf.tolist()),
                key=lambda item: (item[0][1], item[0][0]),
            )

        for i, (xyxy, score) in enumerate(boxes, 1):
            box = clamp_box(tuple(xyxy), (width, height), config.crop_pad)
            x1, y1, x2, y2 = box
            crop = image[y1:y2, x1:x2]
            quality = assess_crop_quality(
                crop,
                box,
                (width, height),
                min_blur_score=config.min_blur_score,
                min_short_edge=config.min_short_edge,
                reject_edge_touch=config.reject_edge_touch,
                reject_edge_aspect=config.reject_edge_aspect,
                edge_wide_aspect=config.edge_wide_aspect,
                edge_tall_aspect=config.edge_tall_aspect,
            )
            crop_path = crop_dir / f"{image_path.stem}_box_{i:02d}.jpg"
            cv2.imwrite(
                str(crop_path),
                crop,
                [int(cv2.IMWRITE_JPEG_QUALITY), config.jpeg_quality],
            )

            box_id = f"{image_path.stem}:box_{i:02d}"
            detected.append(
                DetectedBox(
                    image_path=image_path,
                    index=i,
                    confidence=float(score),
                    xyxy=box,
                    crop_path=crop_path,
                    box_id=box_id,
                    quality=quality,
                )
            )

            color = (0, 200, 0) if quality.readable else (0, 165, 255)
            cv2.rectangle(image, (x1, y1), (x2, y2), color, 5)
            cv2.putText(
                image,
                f"{i} {score:.2f}",
                (x1 + 8, y1 + 36),
                cv2.FONT_HERSHEY_SIMPLEX,
                1.0,
                color,
                3,
                cv2.LINE_AA,
            )

        cv2.imwrite(
            str(preview_dir / image_path.name),
            image,
            [int(cv2.IMWRITE_JPEG_QUALITY), config.jpeg_quality],
        )

    return detected
