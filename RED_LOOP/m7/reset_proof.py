#!/usr/bin/env python3
"""M7 reset proof: the shared ingress's mutable state is removed by POST /_ingress/reset (seed ownership kept), a
replayed idempotent request after the reset is treated as NEW (no stale replay), and the Source-to-Pay stack's
volume is removed by `s2p_stack.py down`. Writes reports/implementation/m7-reset-proof.json.

  python3 RED_LOOP/m7/reset_proof.py            # leaves the S2P stack UP again at the end (down -> up)
"""
import base64, json, subprocess, sys, time, uuid
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ENV2 = REPO / "ENV2_COMPOSE"
IMPL = REPO / "reports/implementation"
ING = "http://api-ingress:8080"
CURL = ["docker", "run", "--rm", "--pull", "never", "--network", "rzp-arena", "curlimages/curl:latest", "-s", "-m", "20"]


def call(method, path, body=None, basic=None, headers=None):
    cmd = CURL + ["-o", "/dev/stdout", "-w", "\n__STATUS__%{http_code}", "-X", method, ING + path]
    if basic:
        cmd += ["-u", basic]
    for k, v in (headers or {}).items():
        cmd += ["-H", "%s: %s" % (k, v)]
    if body is not None:
        cmd += ["-H", "Content-Type: application/json", "--data-binary", json.dumps(body)]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=60).stdout
    payload, _, code = out.rpartition("__STATUS__")
    try:
        return int(code.strip()), json.loads(payload or "{}")
    except ValueError:
        return int(code.strip() or 0), {"raw": payload[:300]}


def main():
    admin = "admin:" + (ENV2 / "secrets/ingress_admin_token.txt").read_text().strip()
    rep = {"started_at": datetime.now(timezone.utc).isoformat()}
    st, before = call("GET", "/_ingress/health")
    rep["tables_before"] = before.get("tables")
    mutable = ("sessions", "otps", "idempotency", "audit", "payout_details", "internal_payouts", "callbacks")
    rep["ingress_mutable_rows_before"] = sum((before.get("tables") or {}).get(t, 0) for t in mutable)
    # a merchant key from the live seeds: replay control across the reset
    merchants = json.loads((ENV2 / "seeds/generated/merchants.json").read_text())["merchants"]
    mid, rec = next(iter(merchants.items()))
    secret = (ENV2 / "secrets/merchant-keys" / (rec["secret_file"] + ".txt")).read_text().strip()
    key = "m7reset-" + uuid.uuid4().hex[:8]
    st1, r1 = call("POST", "/v1/payouts", {"amount": 1, "currency": "INR"}, basic="%s:%s" % (rec["key_id_live"], secret), headers={"X-Payout-Idempotency": key})
    rep["pre_reset_request"] = {"status": st1}
    st, rst = call("POST", "/_ingress/reset", {}, basic=admin)
    rep["reset"] = {"status": st, "body": rst}
    st, after = call("GET", "/_ingress/health")
    rep["tables_after_reset"] = after.get("tables")
    rep["ingress_mutable_rows_after_reset"] = sum((after.get("tables") or {}).get(t, 0) for t in mutable)
    seeds = json.loads((ENV2 / "seeds/generated/monolith/fund_accounts.json").read_text())
    fas = seeds.get("fund_accounts", seeds)
    st, reg = call("GET", "/_ingress/registry/fund_accounts?limit=1", basic=admin)
    rep["seed_ownership_kept"] = (after.get("tables") or {}).get("resources", 0) >= len(fas) and st == 200
    st2, ev = call("GET", "/_ingress/evidence", basic=admin)
    rep["evidence_after_reset_empty"] = all(not ev.get(k) for k in ("audit", "callbacks", "payout_details", "internal_payouts", "idempotency")) if st2 == 200 else None
    st3, r3 = call("POST", "/v1/payouts", {"amount": 1, "currency": "INR"}, basic="%s:%s" % (rec["key_id_live"], secret), headers={"X-Payout-Idempotency": key})
    st4, ev2 = call("GET", "/_ingress/evidence", basic=admin)
    replays = [a for a in (ev2.get("audit") or []) if a.get("decision") == "idempotent_replay"]
    rep["ingress_replay_after_reset_is_new"] = not replays
    rep["post_reset_request"] = {"status": st3, "replayed": bool(replays)}
    # S2P stack: down removes the volume, up restores a fresh one
    vol = "rzp-arena-s2p-mysql-data"
    subprocess.run([sys.executable, "RED_LOOP/m7/s2p_stack.py", "down"], cwd=REPO, capture_output=True, text=True)
    ls = subprocess.run(["docker", "volume", "ls", "-q", "--filter", "name=^%s$" % vol], capture_output=True, text=True).stdout.strip()
    rep["s2p_volume_removed"] = ls == ""
    up = subprocess.run([sys.executable, "RED_LOOP/m7/s2p_stack.py", "up"], cwd=REPO, capture_output=True, text=True)
    rep["s2p_up_rc"] = up.returncode
    rows = subprocess.run(["docker", "exec", "env2_compose-s2p-mysql-1", "env", "MYSQL_PWD=s2p", "mysql", "-Nse", "SELECT count(*) FROM tax_payments", "vendor_payments"], capture_output=True, text=True)
    rep["s2p_tax_payments_after_fresh_up"] = rows.stdout.strip()
    rep["arena_down_removes_volumes"] = "compose ... down -v (ENV2_COMPOSE/scripts/down.sh) -- proven by m6-clean-boot.json from_empty_state"
    rep["finished_at"] = datetime.now(timezone.utc).isoformat()
    (IMPL / "m7-reset-proof.json").write_text(json.dumps(rep, indent=2))
    print(json.dumps({k: rep.get(k) for k in ("ingress_mutable_rows_before", "ingress_mutable_rows_after_reset", "seed_ownership_kept", "evidence_after_reset_empty", "ingress_replay_after_reset_is_new", "s2p_volume_removed", "s2p_up_rc", "s2p_tax_payments_after_fresh_up")}))
    return 0 if rep["ingress_mutable_rows_after_reset"] == 0 and rep["seed_ownership_kept"] and rep["s2p_volume_removed"] and rep["ingress_replay_after_reset_is_new"] and up.returncode == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
