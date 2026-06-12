# Box Detection Dataset

## Layout

```text
dataset/box_detection/
  dataset.yaml
  images/train/
  images/val/
  labels/train/
  labels/val/
  meta/reviewed/train/
  meta/reviewed/val/
  predictions/<model>/<split>/
  previews/<model>/<split>/
```

## Workflow

1. Put source images in `data/`.
2. Run `scripts/init_yolo_dataset.py`.
3. Open `scripts/review_yolo_boxes.py` and draw boxes by hand.
4. Check progress with `scripts/annotation_status.py`.
5. Train YOLO with `dataset.yaml`.

## Commands

```bash
uv run python scripts/review_yolo_boxes.py --dataset-dir dataset/box_detection --split train --only-unreviewed
uv run python scripts/review_yolo_boxes.py --dataset-dir dataset/box_detection --split val --only-unreviewed
uv run python scripts/annotation_status.py --dataset-dir dataset/box_detection
uv run yolo detect train data=dataset/box_detection/dataset.yaml model=yolo11n.pt epochs=100 imgsz=640
```

Use `+` / `-` while annotating to zoom the image.
