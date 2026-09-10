FROM python:3.12-slim

WORKDIR /verifier

# No compiler toolchain needed: psycopg[binary] and pymysql ship wheels/are
# pure-python; keep the image minimal per the rest of ENV2_COMPOSE's stub
# containers (see substitutes/Dockerfile for the sibling convention).
COPY requirements.txt /verifier/requirements.txt
RUN pip install --no-cache-dir -r /verifier/requirements.txt

COPY . /verifier

# SECRETS_DIR mirrors substitutes/_common/base_stub.py's convention --
# service credentials are mounted read-only here by the arena, never baked
# into the image (see helpers/creds.py).
ENV SECRETS_DIR=/run/secrets

ENTRYPOINT ["/verifier/run.sh"]
CMD []
