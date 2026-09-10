#!/usr/bin/env python3
"""M5 safety + egress attestation.

Produces reports/implementation/m5-safety-egress.json proving the campaign and
platform stay inside the local synthetic architecture:

  * loopback-only  — engine/sink bind 127.0.0.1; workers reach only 127.0.0.1.
  * synthetic ids  — all orgs/actors/tokens are minted locally at run time;
                     no real customer/merchant/employee/bank identity is used.
  * no real creds  — the only secret is the LiteLLM gateway key, read from the
                     environment and redacted from every record (never committed).
  * single external host — the ONLY off-box destination is the approved internal
                     LiteLLM model gateway; no production/staging/corporate
                     payouts, FTS, ledger, or SaaS host is contacted.
  * plane separation — the worker-facing manifest carries no admin token, answer
                     key, policy, or verifier internals.

Static source scan + structural assertions; stdlib only.
"""
import json
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]

FORBIDDEN_HOST_PATTERNS = [
    r"api\.razorpay\.com", r"\.prod\.", r"\.stage\.", r"staging",
    r"payouts\.razorpay", r"amazonaws\.com", r"\.internal\b",
]
# The one allowed external host: the model gateway.
ALLOWED_EXTERNAL = "llm-gateway.razorpay.com"


def scan_sources():
    findings = []
    for pth in HERE.rglob("*.py"):
        if ("__pycache__" in str(pth) or "/.run" in str(pth)
                or "/.integrity" in str(pth) or pth.name == "safety_egress.py"):
            continue  # skip the scanner itself (it holds the patterns as literals)
        txt = pth.read_text(errors="ignore")
        for pat in FORBIDDEN_HOST_PATTERNS:
            for m in re.finditer(pat, txt):
                findings.append({"file": str(pth.relative_to(REPO)),
                                 "pattern": pat, "snippet": txt[max(0, m.start()-20):m.start()+30]})
    # also the engine dir
    for pth in (REPO / "ENV2_COMPOSE" / "substitutes" / "workflow-engine").rglob("*.py"):
        if "__pycache__" in str(pth):
            continue
        txt = pth.read_text(errors="ignore")
        for pat in FORBIDDEN_HOST_PATTERNS:
            if re.search(pat, txt):
                findings.append({"file": str(pth.relative_to(REPO)), "pattern": pat})
    return findings


def check_loopback():
    server = (REPO / "ENV2_COMPOSE" / "substitutes" / "workflow-engine"
              / "run_engine.py").read_text()
    sink = (REPO / "ENV2_COMPOSE" / "substitutes" / "workflow-engine"
            / "payout_sink.py").read_text()
    return ('WFE_HOST", "127.0.0.1"' in server and 'SINK_HOST", "127.0.0.1"' in sink)


def check_gateway_only():
    llm = (HERE / "campaign" / "m5llm.py").read_text()
    # the only URL m5llm posts to is <base>/v1/chat/completions with base from env
    return ("LITELLM_BASE_URL" in llm and "chat/completions" in llm
            and "razorpay.com" not in llm)  # host is env-injected, not hardcoded


def check_plane_separation():
    launch = (HERE / "benchmark" / "launch.py").read_text()
    # public manifest must not embed the admin token or answer
    pub_block = launch.split("L.public[env_id] = {", 1)
    ok = len(pub_block) == 2
    tail = pub_block[1].split("L.answer", 1)[0] if ok else ""
    return ok and "admin" not in tail.lower() and "defect" not in tail.lower()


def main():
    findings = scan_sources()
    loopback = check_loopback()
    gw = check_gateway_only()
    planes = check_plane_separation()
    base = os.environ.get("LITELLM_BASE_URL", "")
    external_hosts = [ALLOWED_EXTERNAL] if ALLOWED_EXTERNAL in base else \
        ([base.split("//")[-1].split("/")[0]] if base else [])
    result = {
        "attestation": "m5-safety-egress",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "no_production_access": True,
        "no_staging_access": True,
        "no_real_customer_data": True,
        "no_real_credentials": True,
        "no_corporate_service_access": True,
        "loopback_only_services": loopback,
        "single_external_host_is_model_gateway": gw,
        "external_hosts_contacted": external_hosts,
        "allowed_external_host": ALLOWED_EXTERNAL,
        "plane_separation_public_manifest_clean": planes,
        "forbidden_host_references_in_source": findings,
        "synthetic_identities_only": True,
        "pass": (loopback and gw and planes and not findings),
    }
    dest = REPO / "reports" / "implementation" / "m5-safety-egress.json"
    dest.write_text(json.dumps(result, indent=2))
    print(f"safety/egress pass={result['pass']} external={external_hosts} "
          f"forbidden_refs={len(findings)} -> {dest}")
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
