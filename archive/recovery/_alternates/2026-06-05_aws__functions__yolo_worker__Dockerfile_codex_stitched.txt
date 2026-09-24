# YOLO + OpenCV + AprilTag を全部入れた Lambda Container (x86_64)
FROM public.ecr.aws/lambda/python:3.11

# OS deps (opencv が必要とする libGL など)
RUN dnf install -y mesa-libGL libglvnd-glx && dnf clean all

# Python deps
COPY requirements.txt /tmp/
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# YOLO モデルと AprilTag mapping、known_books をイメージに同梱（cold start 高速化）
COPY assets/ ${LAMBDA_TASK_ROOT}/assets/

# Handler
COPY handler.py ${LAMBDA_TASK_ROOT}/

CMD ["handler.handler"]
