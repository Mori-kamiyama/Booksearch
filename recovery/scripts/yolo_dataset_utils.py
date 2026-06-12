from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


@dataclass
class YoloBox:
    class_id: int
    x_center: float
    y_center: float
    width: float
    height: float

    def clipped(self) -> "YoloBox":
        width = min(max(self.width, 0.0), 1.0)
        height = min(max(self.height, 0.0), 1.0)
        x_center = min(max(self.x_center, 0.0), 1.0)
        y_center = min(max(self.y_center, 0.0), 1.0)

        x1 = max(0.0, x_center - width / 2.0)
        y1 = max(0.0, y_center - height / 2.0)
        x2 = min(1.0, x_center + width / 2.0)
        y2 = min(1.0, y_center + height / 2.0)

        return YoloBox(
            class_id=self.class_id,
            x_center=(x1 + x2) / 2.0,
            y_center=(y1 + y2) / 2.0,
            width=max(0.0, x2 - x1),
            height=max(0.0, y2 - y1),
        )

    def to_xyxy_pixels(self, image_width: int, image_height: int) -> tuple[int, int, int, int]:
        box = self.clipped()
        x1 = int((box.x_center - box.width / 2.0) * image_width)
        y1 = int((box.y_center - box.height / 2.0) * image_height)
        x2 = int((box.x_center + box.width / 2.0) * image_width)
        y2 = int((box.y_center + box.height / 2.0) * image_height)
        return x1, y1, x2, y2


def list_images(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    return sorted(
        path for path in directory.iterdir() if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
    )


def image_to_label_path(dataset_dir: Path, split: str, image_path: Path) -> Path:
    return dataset_dir / "labels" / split / f"{image_path.stem}.txt"


def image_to_review_flag_path(dataset_dir: Path, split: str, image_path: Path) -> Path:
    return dataset_dir / "meta" / "reviewed" / split / f"{image_path.stem}.done"


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def load_yolo_labels(label_path: Path) -> list[YoloBox]:
    if not label_path.exists():
        return []

    boxes: list[YoloBox] = []
    for line in label_path.read_text(encoding="utf-8").splitlines():
        parts = line.strip().split()
        if len(parts) != 5:
            continue
        class_id, x_center, y_center, width, height = parts
        boxes.append(
            YoloBox(
                class_id=int(float(class_id)),
                x_center=float(x_center),
                y_center=float(y_center),
                width=float(width),
                height=float(height),
            ).clipped()
        )
    return boxes


def save_yolo_labels(label_path: Path, boxes: list[YoloBox]) -> None:
    ensure_parent(label_path)
    lines = []
    for box in boxes:
        clipped = box.clipped()
        lines.append(
            f"{clipped.class_id} "
            f"{clipped.x_center:.6f} "
            f"{clipped.y_center:.6f} "
            f"{clipped.width:.6f} "
            f"{clipped.height:.6f}"
        )
    label_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
