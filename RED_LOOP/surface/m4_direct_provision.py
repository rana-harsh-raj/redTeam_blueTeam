#!/usr/bin/env python3
"""Milestone 4 T09 -- fresh DIRECT merchant provisioning, self-check and payout proof.

Runs against the LIVE arena (default compose project env2_compose; override with
ARENA_COMPOSE_PROJECT=env2c_<id>, ARENA_NETWORK=rzp-arena-<id>,
ARENA_KONG_SECRETS_VOLUME=..., KONG_LITE_HOST_URL=http://127.0.0.1:<port>).

  provision --campaign <id> [--count N]   provision N merchants (campaign ids <id>, <id>-2, ...)
  self-check <descriptor.json>            cross-service read-back of one descriptor
  prove <descriptor.json> [--run-dir D]   kong payout proofs (success + immediate failure)
  report <run_dir>                        rebuild the summary JSON from a run dir
  all [--count 2] [--campaign <id>]       provision N (independent ids) + self-check + prove

Evidence: RED_LOOP/runs/m4-direct-provision-<ts>/ (descriptors WITH secrets, git-ignored)
and reports/implementation/m4-direct-provision.json (no secrets). Stdlib only.
"""
import argparse
import json
import subprocess
import sys
import time
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "RED_LOOP"))
from red_loop import provisioner as P  # noqa: E402
from red_loop import provisioner_direct as D  # noqa: E402

IMPL = REPO / "reports" / "implementation"
RUNS = REPO / "RED_LOOP" / "runs"


def _ts():
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def _git_head():
    try:
        return subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:  # noqa: BLE001
        return None


def _fingerprint():
    p = REPO / "ENV2_COMPOSE" / ".runtime" / "arena-fingerprint.json"
    try:
        fp = json.loads(p.read_text())
        return {k: fp.get(k) for k in ("boot_id", "compose_project", "route_profile", "config_digest", "git_head")}
    except Exception:  # noqa: BLE001
        return None


def _run_dir(name=None):
    d = RUNS / (name or ("m4-direct-provision-" + _ts()))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _save(path, obj):
    path.write_text(json.dumps(obj, indent=2, default=str))
    return str(path)


def _print_steps(d):
    bad = [s for s in d["steps"] if not s["ok"]]
    print("  merchant %s  steps=%d existed=%d failed=%d" % (d["merchant_id"], len(d["steps"]), len(d["existed_steps"]), len(bad)))
    for s in bad:
        print("    FAIL %s: %s" % (s["step"], (s.get("err") or "")[:300]))


def _print_checks(res, label):
    print("  %s %s: %d/%d" % (label, res.get("merchant_id"), res["passed"], res["total"]))
    for c in res["checks"]:
        if not c["ok"]:
            print("    FAIL %s: %s" % (c["name"], json.dumps(c["detail"], default=str)[:300]))


def cmd_provision(args, run_dir=None):
    run_dir = run_dir or _run_dir()
    out = []
    for i in range(args.count):
        cid = args.campaign if i == 0 else "%s-%d" % (args.campaign, i + 1)
        d = D.provision_direct_merchant(cid, opening=args.opening)
        _print_steps(d)
        path = _save(run_dir / ("descriptor-%s.json" % d["merchant_id"]), d)
        out.append((d, path))
    return run_dir, out


def cmd_self_check(descriptor, run_dir=None):
    run_dir = run_dir or _run_dir()
    res = D.self_check_direct_merchant(descriptor)
    _print_checks(res, "self-check")
    _save(run_dir / ("selfcheck-%s.json" % descriptor["merchant_id"]), res)
    return res


def cmd_prove(descriptor, run_dir=None):
    run_dir = run_dir or _run_dir()
    res = D.prove_direct_merchant(descriptor)
    _print_checks(res, "proof")
    _save(run_dir / ("proof-%s.json" % descriptor["merchant_id"]), res)
    return res


def _write_summary(run_dir, merchants):
    summary = {
        "milestone": "M4-T09 fresh Direct merchant provisioning",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "tested_commit": _git_head(),
        "arena_fingerprint": _fingerprint(),
        "container_prefix": P.compose_project(),
        "arena_network": P.arena_network(),
        "gateway_profile": "hardened (KONG_ENFORCE_ROUTE_POLICY=1) via " + P.KONG,
        "run_dir": str(run_dir),
        "merchants": [],
    }
    for m in merchants:
        d = m["descriptor"]
        entry = {
            "campaign_id": d["campaign_id"], "merchant_id": d["merchant_id"], "balance_id": d["balance_id"],
            "banking_account_id": d["banking_account_id"], "account_number": d["account_number"],
            "fund_account_id": d["fund_account_id"], "fts_fund_account_id": d["fts_fund_account_id"],
            "fts_source_account_id": d["fts_source_account_id"], "pricing_plan_id": d["pricing_plan_id"],
            "channel": d["channel"], "opening_balance": d["opening_balance"], "restarts": d["restarts"],
            "provision_steps": d["steps"], "failed_steps": d["failed_steps"], "existed_steps": d["existed_steps"],
            "self_check": m.get("self_check"), "payout_proof": m.get("proof"),
        }
        summary["merchants"].append(entry)
    summary["summary"] = {
        "merchants_provisioned": len(merchants),
        "provision_clean": all(not m["descriptor"]["failed_steps"] for m in merchants),
        "self_check_ready": [m.get("self_check", {}).get("ready") for m in merchants],
        "self_check_counts": ["%d/%d" % (m["self_check"]["passed"], m["self_check"]["total"]) for m in merchants if m.get("self_check")],
        "proof_counts": ["%d/%d" % (m["proof"]["passed"], m["proof"]["total"]) for m in merchants if m.get("proof")],
        "proof_ok": [m.get("proof", {}).get("ok") for m in merchants],
        "all_green": all(m.get("self_check", {}).get("ready") and m.get("proof", {}).get("ok") for m in merchants),
    }
    IMPL.mkdir(parents=True, exist_ok=True)
    _save(IMPL / "m4-direct-provision.json", summary)
    _save(run_dir / "m4-direct-provision.json", summary)
    fp = REPO / "ENV2_COMPOSE" / ".runtime" / "arena-fingerprint.json"
    if fp.exists():
        (run_dir / "arena-fingerprint.json").write_text(fp.read_text())
    print(json.dumps(summary["summary"], indent=2))
    return summary


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("provision")
    a.add_argument("--campaign", required=True)
    a.add_argument("--count", type=int, default=1)
    a.add_argument("--opening", type=int, default=10_000_000)
    b = sub.add_parser("self-check")
    b.add_argument("descriptor")
    c = sub.add_parser("prove")
    c.add_argument("descriptor")
    c.add_argument("--run-dir", default=None, help="write proof-<mid>.json into this existing run dir")
    r = sub.add_parser("report", help="rebuild m4-direct-provision.json from a run dir's descriptor/selfcheck/proof files")
    r.add_argument("run_dir")
    e = sub.add_parser("all")
    e.add_argument("--count", type=int, default=2)
    e.add_argument("--campaign", default=None, help="base id; default m4d-<random>, each merchant gets an independent id")
    e.add_argument("--opening", type=int, default=10_000_000)
    args = ap.parse_args()

    if args.cmd == "provision":
        run_dir, out = cmd_provision(args)
        print("run dir:", run_dir)
        return 0 if all(not d["failed_steps"] for d, _ in out) else 1
    if args.cmd == "self-check":
        d = json.loads(Path(args.descriptor).read_text())
        res = cmd_self_check(d)
        return 0 if res["ready"] else 1
    if args.cmd == "prove":
        d = json.loads(Path(args.descriptor).read_text())
        res = cmd_prove(d, Path(args.run_dir) if args.run_dir else None)
        return 0 if res["ok"] else 1
    if args.cmd == "report":
        run_dir = Path(args.run_dir)
        merchants = []
        for dp in sorted(run_dir.glob("descriptor-*.json")):
            d = json.loads(dp.read_text())
            m = {"descriptor": d}
            for kind in ("selfcheck", "proof"):
                fp = run_dir / ("%s-%s.json" % (kind, d["merchant_id"]))
                if fp.exists():
                    m["self_check" if kind == "selfcheck" else "proof"] = json.loads(fp.read_text())
            merchants.append(m)
        s = _write_summary(run_dir, merchants)
        return 0 if s["summary"]["all_green"] else 1
    if args.cmd == "all":
        run_dir = _run_dir()
        merchants = []
        for i in range(args.count):
            cid = (args.campaign + ("" if i == 0 else "-%d" % (i + 1))) if args.campaign else ("m4d-" + uuid.uuid4().hex[:8])
            print("[provision] campaign", cid)
            d = D.provision_direct_merchant(cid, opening=args.opening)
            _print_steps(d)
            _save(run_dir / ("descriptor-%s.json" % d["merchant_id"]), d)
            merchants.append({"descriptor": d})
        for m in merchants:
            print("[self-check]", m["descriptor"]["merchant_id"])
            m["self_check"] = cmd_self_check(m["descriptor"], run_dir)
        if all(m["self_check"]["ready"] for m in merchants):
            for m in merchants:
                print("[prove]", m["descriptor"]["merchant_id"])
                m["proof"] = cmd_prove(m["descriptor"], run_dir)
        else:
            print("self-check not ready for every merchant; payout proofs skipped")
        s = _write_summary(run_dir, merchants)
        print("run dir:", run_dir)
        return 0 if s["summary"]["all_green"] else 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
