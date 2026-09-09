#!/usr/bin/env python3
"""M11: provision the REAL Kong gateway (edge-kong) through its Admin API from the mechanically derived
trustpath/edge/kong-config.json, then seed one consumer + basic-auth-x credentials per arena merchant.

Idempotent (PUT by name / upsert by username) so it can run at boot and again when a fresh merchant is provisioned.
stdlib only. Runs inside the arena (one-shot compose job `edge-kong-config`) or from the host bridge via
`docker exec` (see RED_LOOP/red_loop/provisioner.py register_kong_key).

  python3 provision_kong.py apply      # services/routes/plugins + anonymous consumer + every seeded merchant
  python3 provision_kong.py merchant <merchant_id> <live_secret_file> [<test_secret_file>] [role ...]
"""
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request

ADMIN = os.environ.get("KONG_ADMIN_URL", "http://edge-kong:8001")
CONFIG = os.environ.get("KONG_CONFIG_FILE", "/app/kong-config.json")
MERCHANTS = os.environ.get("KONG_MERCHANTS_FILE", "/app/seed/merchants.json")
SECRETS_DIR = os.environ.get("KONG_MERCHANT_SECRETS_DIR", "/run/secrets/merchants")


def call(method, path, body=None, ok=(200, 201, 204, 409)):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(ADMIN + path, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            doc = json.loads(raw) if raw else {}
        except ValueError:
            doc = {"raw": raw.decode("utf-8", "replace")[:300]}
        if e.code in ok:
            return e.code, doc
        raise RuntimeError("%s %s -> %s %s" % (method, path, e.code, json.dumps(doc)[:400]))


def wait_admin(timeout=180):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            st, _ = call("GET", "/status")
            if st == 200:
                return
        except Exception:  # noqa: BLE001
            pass
        time.sleep(2)
    raise RuntimeError("kong admin api not reachable at %s" % ADMIN)


def upsert_consumer(username, tags=None):
    st, doc = call("PUT", "/consumers/" + username, {"username": username, "tags": tags or []})
    return doc["id"]


def upsert_credential(consumer_id, username, password, tags):
    """basic-auth-x credential (kong-plugin-basic-auth-x daos.lua: password hashed on write = sha512(pw .. consumer_id)).
    The plugin resolves the most recent credential per username (select_most_recent_by_username), so an existing row
    for the same username is deleted first to keep exactly one."""
    st, doc = call("GET", "/consumers/%s/basic-auth-x" % consumer_id)
    for cred in doc.get("data", []):
        if cred.get("username") == username:
            call("DELETE", "/consumers/%s/basic-auth-x/%s" % (consumer_id, cred["id"]))
    st, doc = call("POST", "/consumers/%s/basic-auth-x" % consumer_id, {"username": username, "password": password, "tags": tags})
    return doc.get("id")


def read_secret(name):
    p = os.path.join(SECRETS_DIR, name if name.endswith(".txt") else name + ".txt")
    with open(p) as f:
        return f.read().strip()


def merchant(mid, live_secret_file, test_secret_file=None, roles=()):
    cid = upsert_consumer(mid, tags=["tenant~razorpay"])
    out = {"consumer_id": cid, "credentials": []}
    live = read_secret(live_secret_file)
    test = read_secret(test_secret_file) if test_secret_file else live
    role_tags = ["r~%s" % r for r in roles if r]
    out["credentials"].append(upsert_credential(cid, "rzp_live_" + mid, live, ["m~l"] + role_tags))
    out["credentials"].append(upsert_credential(cid, "rzp_test_" + mid, test, ["m~t"] + role_tags))
    return out


def apply():
    cfg = json.load(open(CONFIG))
    anon_id = upsert_consumer(cfg["consumers"]["anonymous"]["username"])
    report = {"services": {}, "consumers": 0, "anonymous_id": anon_id}
    for sname, s in cfg["services"].items():
        svc = dict(s["service"])
        st, sdoc = call("PUT", "/services/" + svc["name"], svc)
        sid = sdoc["id"]
        # service-level plugins (upsert by name on this service)
        st, existing = call("GET", "/services/%s/plugins" % sid)
        by_name = {p["name"]: p for p in existing.get("data", []) if not p.get("route")}
        for p in s["service_plugins"]:
            conf = json.loads(json.dumps(p["config"]).replace("__ANONYMOUS_CONSUMER_ID__", anon_id))
            body = {"name": p["name"], "config": conf, "enabled": True}
            if p["name"] in by_name:
                call("PATCH", "/plugins/" + by_name[p["name"]]["id"], body)
            else:
                call("POST", "/services/%s/plugins" % sid, body)
        nroutes = 0
        for rname, r in s["routes"].items():
            route = {"name": r["kong_route_name"], "paths": r["paths"], "methods": r["methods"], "strip_path": False, "preserve_host": True,
                     "protocols": ["http", "https"], "service": {"id": sid}, "regex_priority": 0}
            if r.get("hosts"):
                route["hosts"] = r["hosts"]      # production hosts (payouts-ext), see kong-config.json route_hosts
            st, rdoc = call("PUT", "/routes/" + route["name"], route)
            rid = rdoc["id"]
            st, rex = call("GET", "/routes/%s/plugins" % rid)
            rby = {p["name"]: p for p in rex.get("data", [])}
            for p in r["plugins"]:
                conf = json.loads(json.dumps(p["config"]).replace("__ANONYMOUS_CONSUMER_ID__", anon_id))
                body = {"name": p["name"], "config": conf, "enabled": True}
                if p["name"] in rby:
                    call("PATCH", "/plugins/" + rby[p["name"]]["id"], body)
                else:
                    call("POST", "/routes/%s/plugins" % rid, body)
            nroutes += 1
        report["services"][sname] = {"id": sid, "routes": nroutes}
    if os.path.isfile(MERCHANTS):
        doc = json.load(open(MERCHANTS))
        for mid, m in doc.get("merchants", {}).items():
            try:
                merchant(mid, m["secret_file"], m.get("secret_file_test"), m.get("roles") or [])
                report["consumers"] += 1
            except FileNotFoundError as e:
                print("skip %s: %s" % (mid, e), file=sys.stderr)
    return report


if __name__ == "__main__":
    wait_admin()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "apply"
    if cmd == "apply":
        print(json.dumps(apply()))
    elif cmd == "merchant":
        mid, live = sys.argv[2], sys.argv[3]
        test = sys.argv[4] if len(sys.argv) > 4 and not sys.argv[4].startswith("r:") else None
        roles = [a[2:] for a in sys.argv[4:] if a.startswith("r:")]
        print(json.dumps(merchant(mid, live, test, roles)))
    else:
        raise SystemExit("usage: provision_kong.py apply | merchant <mid> <live_secret_file> [<test_secret_file>] [r:<role> ...]")
