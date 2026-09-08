#!/usr/bin/env python3
"""Container entrypoint for workflow-engine INSIDE the Env 2 arena.

run_engine.py stays the standalone (host-process) entrypoint used by
RED_LOOP/m5/* -- it is untouched and still authoritative for the M5 suite,
benchmark and campaign. This file is the arena wrapper around the exact same
wfengine package; it differs only in how configuration arrives:

  * credentials come from *_FILE paths (docker/materialized secrets mounted at
    /run/secrets, uid 10001, mode 0400) instead of inline env values, so no
    credential is ever baked into an image or visible in `docker inspect`;
  * PS_API_URL points the terminal callbacks at the REAL payouts-api, and the
    engine then uses the url_path/method/headers payouts itself supplied in the
    create body's callback_details (verbatim, exactly like workflow-sim);
  * WFE_TENANT_KEY=owner_id makes the engine's org == the payouts merchant;
  * WFE_WORKFLOW_ID_LEN=14 keeps the returned id inside payouts' CHAR(14)
    workflow_id columns;
  * WFE_AUTOPROVISION_POLICY=1 mints the documented default approval policy the
    first time an unknown merchant's payout arrives (see ARENA.md).

Env (all optional unless marked):
  WFE_DB                      sqlite path                (default /data/wfe.db)
  WFE_HOST / WFE_PORT         bind                       (default 0.0.0.0:8093)
  WFE_ADMIN_TOKEN_FILE        file holding the admin-plane token  [preferred]
  WFE_ADMIN_TOKEN             inline fallback (dev only)
  WFE_SERVICE_USER/_PASS      inbound Basic auth payouts' [workflow.auth] sends
  WFE_SERVICE_PASS_FILE       file form of the above
  WFE_CALLBACK_USER           default rzp_live           (payouts [auth.workflow])
  WFE_CALLBACK_PASS_FILE      default /run/secrets/auth_workflow_payouts
  PS_API_URL                  default http://payouts-api:9400
  WFE_TENANT_KEY              org_id | owner_id          (default owner_id here)
  WFE_AUTOPROVISION_POLICY    1 to auto-bind a default policy (default 1 here)
  WFE_DEFAULT_REQUIRED_APPROVALS / _SEPARATION / _EXPIRY_SECONDS

Stdlib only. Fails CLOSED on a missing admin token: without it the admin plane
would be open, so the process refuses to start rather than serve unauthenticated
provisioning.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from wfengine.server import build_engine, serve  # noqa: E402


def _log(msg):
    sys.stderr.write("[workflow-engine] %s\n" % msg)
    sys.stderr.flush()


def _read_file(path):
    if not path:
        return ""
    try:
        with open(path) as fh:
            return fh.read().strip()
    except OSError as exc:
        _log("WARNING: cannot read %s: %s" % (path, exc))
        return ""


def _resolve(inline_env, file_env, default=""):
    """File wins over inline; inline wins over default."""
    path = os.environ.get(file_env)
    if path:
        value = _read_file(path)
        if value:
            return value
    return os.environ.get(inline_env) or default


def main():
    db = os.environ.get("WFE_DB", "/data/wfe.db")
    host = os.environ.get("WFE_HOST", "0.0.0.0")
    port = int(os.environ.get("WFE_PORT", "8093"))

    admin = _resolve("WFE_ADMIN_TOKEN", "WFE_ADMIN_TOKEN_FILE")
    if not admin:
        _log("FATAL: no admin token (WFE_ADMIN_TOKEN_FILE / WFE_ADMIN_TOKEN). "
             "Refusing to start with an unauthenticated admin plane.")
        return 2

    service_user = os.environ.get("WFE_SERVICE_USER", "workflow")
    service_pass = _resolve("WFE_SERVICE_PASS", "WFE_SERVICE_PASS_FILE", "workflow")

    callback_user = os.environ.get("WFE_CALLBACK_USER", "rzp_live")
    callback_pass_file = os.environ.get("WFE_CALLBACK_PASS_FILE",
                                        "/run/secrets/auth_workflow_payouts")
    callback_pass = _resolve("WFE_CALLBACK_PASS", "WFE_CALLBACK_PASS_FILE", "")
    if not callback_pass:
        # Not fatal: the engine is still a faithful approval engine, but every
        # terminal callback into payouts would 401. Loud, and visible in
        # /health-adjacent logs, exactly as workflow-sim warns.
        _log("WARNING: no callback password at %s -- approve/reject callbacks "
             "into payouts will be rejected with 401." % callback_pass_file)

    ps_api_url = os.environ.get("PS_API_URL", "http://payouts-api:9400")
    tenant_key = os.environ.get("WFE_TENANT_KEY", "owner_id")

    autoprovision = None
    if os.environ.get("WFE_AUTOPROVISION_POLICY", "1") == "1":
        autoprovision = {
            "required_approvals": int(
                os.environ.get("WFE_DEFAULT_REQUIRED_APPROVALS", "1")),
            "separation": 1 if os.environ.get("WFE_DEFAULT_SEPARATION", "1") == "1" else 0,
            "expiry_seconds": int(
                os.environ.get("WFE_DEFAULT_EXPIRY_SECONDS", "86400")),
        }

    engine = build_engine(
        db,
        admin_token=admin,
        service_user=service_user,
        service_pass=service_pass,
        callback_sink=os.environ.get("WFE_CALLBACK_SINK"),
        callback_user=callback_user,
        callback_pass=callback_pass,
        deliver_callbacks=os.environ.get("WFE_DELIVER", "1") != "0",
        ps_api_url=ps_api_url,
        tenant_key=tenant_key,
        autoprovision=autoprovision,
    )
    httpd = serve(engine, host, port)
    _log("listening on %s:%d db=%s ps_api=%s tenant_key=%s inbound_user=%s "
         "callback_user=%s autoprovision=%s"
         % (host, port, db, ps_api_url, tenant_key, service_user,
            callback_user, bool(autoprovision)))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        engine["callbacks"].stop()
        engine["store"].close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
