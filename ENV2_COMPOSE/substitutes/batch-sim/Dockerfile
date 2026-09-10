# batch-sim runtime.
#
# Byte-for-byte the shared substitutes/Dockerfile EXCEPT for one added line:
#
#     RUN mkdir -p /data && chown stub:stub /data
#
# Why it is needed: batch-sim is the first substitute with a durable store. The
# shared runtime runs as the unprivileged `stub` user (uid 10001), and a Docker
# named volume mounted onto a path that does NOT exist in the image is created
# root:root — so `stub` cannot write it and sqlite3.connect() fails with
# "unable to open database file". A named volume mounted onto a path that DOES
# exist in the image inherits that path's ownership, so pre-creating /data as
# `stub` makes the volume writable without relaxing the non-root posture.
#
# Verified: building the shared Dockerfile and running it with
# `-v <fresh volume>:/data --read-only` reproduces the failure; this file fixes
# it with the container still running as uid 10001 and root FS read-only.
#
# PREFERRED ALTERNATIVE for the coordinator: delete this file and add the same
# one line to substitutes/Dockerfile (right after the COPY steps). That keeps
# the "one shared runtime" convention and benefits every future stateful stub.
# This lane is not permitted to edit existing files, hence the local copy.
#
# Build context MUST still be ENV2_COMPOSE/substitutes/ (so both _common/ and
# batch-sim/ are visible), with --build-arg STUB_DIR=batch-sim.
# stdlib-only: no pip install, no network needed at build or run time.
FROM python:3.12-alpine

ARG STUB_DIR
ARG STUB_NAME
ARG STUB_PORT=8080

RUN adduser -D -u 10001 stub
WORKDIR /app
COPY _common/ /app/_common/
COPY ${STUB_DIR}/*.py /app/

# The only deviation from substitutes/Dockerfile — see the header.
RUN mkdir -p /data && chown stub:stub /data

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    STUB_NAME=${STUB_NAME} \
    STUB_PORT=${STUB_PORT}

USER stub
EXPOSE ${STUB_PORT}
HEALTHCHECK --interval=5s --timeout=3s --retries=10 \
  CMD wget -q -O- http://127.0.0.1:${STUB_PORT}/health || exit 1

CMD ["python3", "/app/server.py"]
