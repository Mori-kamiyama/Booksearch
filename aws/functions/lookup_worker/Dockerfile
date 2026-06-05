# Lookup Worker: library.db (SQLite) と known_books.json を同梱
FROM public.ecr.aws/lambda/python:3.12

COPY requirements.txt /tmp/
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# SQLite DB と補助 JSON はイメージに焼く
COPY assets/ ${LAMBDA_TASK_ROOT}/assets/

COPY handler.py ${LAMBDA_TASK_ROOT}/

CMD ["handler.handler"]
