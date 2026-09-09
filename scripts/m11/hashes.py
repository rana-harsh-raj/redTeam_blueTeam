#!/usr/bin/env python3
"""Bind every M11 artifact into reports/implementation/M11_ARTIFACT_HASHES.json.
  python3 scripts/m11/hashes.py            # (re)build the manifest
  python3 scripts/m11/hashes.py --verify   # read-only: exit 1 on any changed / missing file
The manifest excludes itself and M11_ACCEPTANCE.json (the acceptance record binds this manifest, not the reverse).
Journey evidence bundles of the two variant runs live in the factory instance directories (git-ignored by design) and
are hash-bound through the run directories recorded in reports/implementation/m11/differential.json.
"""
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "reports/implementation/M11_ARTIFACT_HASHES.json"
TRACKED = [
    "reports/implementation/M11_FINAL_REPORT.md", "reports/implementation/M11_RUNBOOK.md", "reports/implementation/M11_DIFFERENTIAL.md",
    "reports/implementation/m11/differential.json", "reports/implementation/m11/monolith-boot-attempt.json", "reports/implementation/m11/monolith-boot-attempt.md",
    "reports/implementation/m11/historical-tags.json", "reports/implementation/m11/resources.md", "reports/implementation/m11/clean-checkout.json",
    "reports/architecture/snapshots/REGISTRY.json",
    "reports/domain/parts/m11-trust-path.json", "reports/domain/parts/m6-journeys.json", "reports/domain/GRAPH_STATS.json",
    "ENV2_COMPOSE/docker-compose.m11.yml", "ENV2_COMPOSE/trustpath/edge/kong-config.json", "ENV2_COMPOSE/trustpath/edge/provision_kong.py",
    "ENV2_COMPOSE/trustpath/shield/seed_rules.py", "ENV2_COMPOSE/trustpath/bankingaccounts/render_seed.py", "ENV2_COMPOSE/trustpath/workflows/seed_configs.py",
    "ENV2_COMPOSE/build/m11/edge-kong.Dockerfile", "ENV2_COMPOSE/build/m11/build-shield.sh", "ENV2_COMPOSE/build/m11/build-banking-accounts.sh",
    "ENV2_COMPOSE/build/m11/build-workflows.sh", "ENV2_COMPOSE/build/m11/package.sh",
    "ENV2_COMPOSE/substitutes/api-ingress/server.py", "ENV2_COMPOSE/substitutes/api-ingress/CONTRACT.md",
    "ENV2_COMPOSE/config/generate.py", "ENV2_COMPOSE/config/templates/base/payouts/arena.toml", "ENV2_COMPOSE/config/templates/base/workflows/arena.toml",
    "ENV2_COMPOSE/config/templates/base/bankingaccounts/arena.toml", "ENV2_COMPOSE/secrets/gen-secrets.sh", "ENV2_COMPOSE/secrets/gen-tls.sh", "ENV2_COMPOSE/secrets/materialize.py",
    "ENV2_COMPOSE/scripts/ingress.py",
    "RED_LOOP/m6/journeys/j_trustpath.py", "RED_LOOP/m6/journeys/trustpath_adapter.py", "RED_LOOP/m6/journeys/framework.py", "RED_LOOP/m6/journeys/j_approval.py",
    "twinfactory/profiles.py", "twinfactory/boot.py", "twinfactory/images.py", "twinfactory/factory.py",
    "archkit/recipes.py", "archkit/static_graph.py", "scripts/m11/differential.py", "scripts/m11/acceptance.py", "scripts/m11/measure_resources.py",
    "scripts/m11/monolith_boot_attempt.py", "scripts/m11/derive_kong_config.py", "scripts/m11/trust_path_part.py", "scripts/m11/clean_checkout.py",
]


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def evidence_bundles():
    out = []
    try:
        d = json.loads((REPO / "reports/implementation/m11/differential.json").read_text())
        for k in ("real_run", "substitute_run"):
            run = Path(d[k])
            out += sorted(p for p in (run / "evidence").glob("*.json")) + [run / "results.json"]
    except Exception:  # noqa: BLE001
        pass
    for p in sorted((REPO / "reports/implementation/m11").glob("resources-*.json")):
        out.append(p)
    return out


def build():
    files, missing = {}, []
    for rel in TRACKED:
        p = REPO / rel
        if p.is_file():
            files[rel] = sha(p)
        else:
            missing.append(rel)
    for p in evidence_bundles():
        if p.is_file():
            files[str(p)] = sha(p)
    return {"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "count": len(files), "missing_tracked": missing, "files": files}


def main():
    if "--verify" in sys.argv:
        cur = json.loads(OUT.read_text())
        bad = []
        for rel, h in cur["files"].items():
            p = Path(rel) if rel.startswith("/") else REPO / rel
            if not p.is_file() or sha(p) != h:
                bad.append(rel)
        print(json.dumps({"verified": len(cur["files"]) - len(bad), "changed_or_missing": bad[:20]}))
        return 1 if bad else 0
    doc = build()
    OUT.write_text(json.dumps(doc, indent=1))
    print(json.dumps({"count": doc["count"], "missing_tracked": doc["missing_tracked"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
