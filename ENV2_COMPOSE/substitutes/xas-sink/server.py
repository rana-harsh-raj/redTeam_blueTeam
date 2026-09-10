#!/usr/bin/env python3
"""xas-sink: skeleton placeholder for x-account-statements' Env 2 role. See CONTRACT.md.

Deliberately does not drain LocalStack SQS queues yet (see CONTRACT.md TODO)
-- only answers /health so `depends_on: service_healthy` chains in
docker-compose.yml resolve, and so a future SQS-draining implementation has
a container to grow into without changing the compose wiring.
"""
import sys

sys.path.insert(0, "/app")
from _common.base_stub import serve  # noqa: E402

ROUTES = {}  # /health is handled unconditionally by base_stub's dispatch

if __name__ == "__main__":
    serve(ROUTES)
