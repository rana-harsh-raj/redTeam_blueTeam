# workflow-engine — the M5 durable maker/checker approval engine, containerised
# for the Env 2 arena.
#
# Why this is NOT the shared substitutes/Dockerfile: that one does
# `COPY ${STUB_DIR}/*.py /app/` and so copies only top-level modules. The engine
# is a real package (wfengine/{server,core,identity,callbacks,store}.py +
# schema.sql), so it needs a package-aware copy. Everything else (python:3.12-alpine,
# non-root uid 10001, stdlib-only, no network at build or run time, HEALTHCHECK on
# /health) matches the shared runtime's conventions exactly.
#
# Build context MUST be ENV2_COMPOSE/substitutes/workflow-engine.
FROM python:3.12-alpine

RUN adduser -D -u 10001 stub

WORKDIR /app
COPY wfengine/ /app/wfengine/
COPY arena_entrypoint.py run_engine.py /app/

# Durable state lives on the wfengine-data named volume. Creating the directory
# with uid 10001 ownership in the image makes Docker initialise a fresh named
# volume with the same ownership, so the non-root process can write its SQLite
# db (+ -wal/-shm) without a chown at runtime and without root.
RUN mkdir -p /data && chown 10001:10001 /data \
 && find /app -name '__pycache__' -type d -prune -exec rm -rf {} + || true

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    WFE_DB=/data/wfe.db \
    WFE_HOST=0.0.0.0 \
    WFE_PORT=8093

USER stub
EXPOSE 8093
VOLUME ["/data"]

HEALTHCHECK --interval=5s --timeout=3s --retries=20 --start-period=5s \
  CMD wget -q -O- http://127.0.0.1:8093/health || exit 1

CMD ["python3", "/app/arena_entrypoint.py"]
