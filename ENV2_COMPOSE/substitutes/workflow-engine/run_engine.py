#!/usr/bin/env python3
"""Entrypoint for a single workflow-engine instance.

Env:
  WFE_DB           sqlite path (default ./wfe.db)
  WFE_HOST/WFE_PORT bind (default 127.0.0.1:8093)
  WFE_ADMIN_TOKEN  admin-plane token (required for provisioning)
  WFE_SERVICE_USER/WFE_SERVICE_PASS  create Basic-auth creds (default workflow/workflow)
  WFE_CALLBACK_SINK  base URL of the payout sink for terminal callbacks
  WFE_DELIVER      '0' to record-but-not-send callbacks (unit mode)

Durable + restart-recoverable: state lives entirely in WFE_DB, and undelivered
callbacks resume on boot.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wfengine.server import build_engine, serve  # noqa: E402


def main():
    db = os.environ.get("WFE_DB", "./wfe.db")
    host = os.environ.get("WFE_HOST", "127.0.0.1")
    port = int(os.environ.get("WFE_PORT", "8093"))
    admin = os.environ.get("WFE_ADMIN_TOKEN", "admin-local")
    engine = build_engine(
        db,
        admin_token=admin,
        service_user=os.environ.get("WFE_SERVICE_USER", "workflow"),
        service_pass=os.environ.get("WFE_SERVICE_PASS", "workflow"),
        callback_sink=os.environ.get("WFE_CALLBACK_SINK"),
        callback_user=os.environ.get("WFE_CALLBACK_USER", "rzp_live"),
        callback_pass=os.environ.get("WFE_CALLBACK_PASS", "workflow-secret"),
        deliver_callbacks=os.environ.get("WFE_DELIVER", "1") != "0",
    )
    httpd = serve(engine, host, port)
    print(f"workflow-engine up on http://{host}:{port} db={db}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        engine["callbacks"].stop()
        engine["store"].close()


if __name__ == "__main__":
    main()
