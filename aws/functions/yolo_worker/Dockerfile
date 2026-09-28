# YOLO + OpenCV + AprilTag を全部入れた Lambda Container (x86_64)
# python:3.11 は AL2 ベース、yum を使う
FROM --platform=linux/amd64 public.ecr.aws/lambda/python:3.11

# OS deps (opencv が必要とする libGL、numpy ビルド用 gcc など)
RUN yum install -y mesa-libGL libglvnd-glx gcc gcc-c++ make && yum clean all

# pip と build tools を最新化（meson-python が新しい numpy ビルドに必要）
RUN pip install --no-cache-dir --upgrade pip setuptools wheel

# Python deps
COPY requirements.txt /tmp/
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# YOLO モデルと AprilTag mapping、known_books をイメージに同梱（cold start 高速化）
COPY assets/ ${LAMBDA_TASK_ROOT}/assets/

# Handler
COPY handler.py ${LAMBDA_TASK_ROOT}/
COPY durable.py ${LAMBDA_TASK_ROOT}/

CMD ["handler.handler"]
