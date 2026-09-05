# Shared runtime for every substitutes/<stub>/server.py. Build context MUST be
# ENV2_COMPOSE/substitutes/ (so both _common/ and <stub>/ are visible), with
# --build-arg STUB_DIR=<stub>. stdlib-only stubs: no pip install, no network
# needed at build or run time.
FROM python:3.12-alpine

ARG STUB_DIR
ARG STUB_NAME
ARG STUB_PORT=8080

RUN adduser -D -u 10001 stub
WORKDIR /app
COPY _common/ /app/_common/
COPY ${STUB_DIR}/server.py /app/server.py

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    STUB_NAME=${STUB_NAME} \
    STUB_PORT=${STUB_PORT}

USER stub
EXPOSE ${STUB_PORT}
HEALTHCHECK --interval=5s --timeout=3s --retries=10 \
  CMD wget -q -O- http://127.0.0.1:${STUB_PORT}/health || exit 1

CMD ["python3", "/app/server.py"]
