#!/usr/bin/env python3
"""Calibration-only tenant-isolation service (Milestone 3.1).

A SELF-CONTAINED stdlib HTTP service that models a tenant-scoped resource store.
It exists ONLY to validate the harness's reproduce-and-judge machinery on a
genuine, causally-attributable unauthorized-disclosure effect. It is NOT part of
the Payouts Twin, is NOT wired into the arena compose, is NOT referenced by the
open-campaign corpus, and NEVER touches the real Kong gateway or any real service.

Two profiles, selected by the calibration-only env flag ``CALIB_PROFILE``:

  * ``regression``  -- an object-level authorization flaw (IDOR): a resource is
    returned by id WITHOUT checking that the authenticated caller owns it, so an
    attacker tenant can read a victim tenant's resource (and its hidden canary).
  * ``fixed`` (DEFAULT) -- ownership is enforced; a cross-tenant fetch returns
    404 not-found with NO victim-owned field. This is the causal negative control.

Default behaviour (no flag) is the FIXED profile. The regression profile must be
requested explicitly, per run, against a freshly generated fixture.

Every identity, credential, resource id and canary is supplied by the per-run
fixture manifest (``CALIB_FIXTURE``); this file hard-codes none of them.

Env:
  CALIB_FIXTURE  path to the per-run control-plane fixture JSON (required)
  CALIB_PROFILE  'regression' | 'fixed'   (default 'fixed')
  CALIB_PORT     loopback port            (default 19099)
  CALIB_HOST     bind host                (default 127.0.0.1)

Routes:
  GET  /calib/health                       liveness
  GET  /calib/v1/resources/{resource_id}   tenant-scoped fetch (Bearer auth)
"""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse


def _load_fixture():
    path = os.environ["CALIB_FIXTURE"]
    with open(path) as f:
        return json.load(f)


class _Store:
    """Indexes a fixture manifest by credential -> tenant and resource id."""

    def __init__(self, fixture, profile):
        self.profile = profile
        self.run_id = fixture["run_id"]
        # credential (bearer) -> merchant/tenant id
        self.cred_to_tenant = {}
        for actor in fixture["actors"].values():
            self.cred_to_tenant[actor["credential"]] = actor["merchant_id"]
        # resource id -> resource record {owner_merchant_id, payload{...canary...}}
        self.resources = {r["resource_id"]: r for r in fixture["resources"]}

    def tenant_for(self, credential):
        return self.cred_to_tenant.get(credential)

    def fetch(self, caller_tenant, resource_id):
        """Return (status, body_obj). Regression omits the ownership check."""
        res = self.resources.get(resource_id)
        if res is None:
            return 404, {"error": {"code": "not_found", "description": "resource not found"}}
        if self.profile == "fixed" and res["owner_merchant_id"] != caller_tenant:
            # Ownership enforced: deny without leaking that the id exists elsewhere.
            return 404, {"error": {"code": "not_found", "description": "resource not found"}}
        # regression: no ownership check -> object returned to any authenticated caller.
        return 200, {
            "id": res["resource_id"],
            "owner_merchant_id": res["owner_merchant_id"],
            "type": res.get("type", "payout_destination"),
            **res["payload"],
        }


def _make_handler(store):
    class Handler(BaseHTTPRequestHandler):
        server_version = "calib/1.0"

        def log_message(self, *a):  # silence default logging
            pass

        def _send(self, status, obj):
            body = json.dumps(obj).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Calib-Profile", store.profile)
            self.send_header("X-Calib-Run", store.run_id)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/calib/health":
                return self._send(200, {"ok": True, "profile": store.profile,
                                        "run_id": store.run_id})
            if path.startswith("/calib/v1/resources/"):
                resource_id = path[len("/calib/v1/resources/"):]
                auth = self.headers.get("Authorization", "")
                cred = auth[7:] if auth.startswith("Bearer ") else ""
                tenant = store.tenant_for(cred)
                if not tenant:
                    return self._send(401, {"error": {"code": "unauthorized",
                                                       "description": "unknown credential"}})
                status, obj = store.fetch(tenant, resource_id)
                return self._send(status, obj)
            return self._send(404, {"error": {"code": "not_found"}})

    return Handler


def main():
    fixture = _load_fixture()
    profile = os.environ.get("CALIB_PROFILE", "fixed")
    if profile not in ("regression", "fixed"):
        print("CALIB_PROFILE must be 'regression' or 'fixed'", file=sys.stderr)
        return 2
    host = os.environ.get("CALIB_HOST", "127.0.0.1")
    port = int(os.environ.get("CALIB_PORT", "19099"))
    store = _Store(fixture, profile)
    httpd = ThreadingHTTPServer((host, port), _make_handler(store))
    sys.stderr.write("calib service run=%s profile=%s on %s:%d\n"
                     % (store.run_id, profile, host, port))
    sys.stderr.flush()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
