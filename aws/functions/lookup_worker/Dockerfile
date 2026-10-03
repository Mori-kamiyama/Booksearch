# Lookup Worker: library.db (SQLite) と known_books.json を同梱
FROM public.ecr.aws/lambda/python:3.12

COPY lookup_worker/requirements.txt /tmp/
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# SQLite DB と補助 JSON はイメージに焼く
COPY lookup_worker/assets/ ${LAMBDA_TASK_ROOT}/assets/

COPY lookup_worker/handler.py ${LAMBDA_TASK_ROOT}/

COPY shared/ ${LAMBDA_TASK_ROOT}/shared/

CMD ["handler.handler"]
