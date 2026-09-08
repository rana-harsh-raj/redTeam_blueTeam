#!/usr/bin/env python3
"""M6 repository + deployment-artifact inventory and remaining-access manifest.

  python3 scripts/domain/inventory.py            # writes reports/domain/REPOSITORY_INVENTORY_M6.csv
                                                 #        reports/domain/REMAINING_ACCESS_MANIFEST.md
Sources: .local/repos-root (pristine clones), reports/REPOSITORY_INVENTORY.csv (roles),
reports/ACCESS_DELTA.md (roles for post-access repos), reports/REPOSITORY_ACCESS_MATRIX.csv
(GitHub-level access), reports/NON_REPOSITORY_ACCESS_MATRIX.csv, reports/fidelity/
REMAINING_INFORMATION_REQUESTS.md, and (if present) reports/domain/GRAPH_STATS.json for the
graph's blocked_missing_access nodes. Stdlib only; no network; no secrets.
"""
import csv, json, re, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DOM = REPO / "reports" / "domain"

# Deployment / infra artefacts of the payouts domain inside the clones (repo, path, what)
DEPLOY_ARTIFACTS = [
    ("kube-manifests", "cde/payouts", "prod-equivalent payouts deployment values (api, ~25 workers, kafka consumers)"),
    ("kube-manifests", "cde/shadow-payouts", "shadow payouts deployment"),
    ("kube-manifests", "templates/payouts", "payouts helm template"),
    ("kube-manifests", "helmfile/charts/payouts", "payouts chart"),
    ("kube-manifests", "cde/fts", "FTS deployments/workers"),
    ("kube-manifests", "cde/ledger", "Ledger deployments + CronJobs"),
    ("kube-manifests", "cde/cfa", "CFA deployments + CronJobs"),
    ("kube-manifests", "cde/x-balances", "x-balances deployments"),
    ("kube-manifests", "cde/banking-accounts", "BAS deployments"),
    ("kube-manifests", "cde/x-account-statements", "XAS deployments"),
    ("kube-manifests", "cde/workflows", "Workflow Service deployments"),
    ("kube-manifests", "cde/batch", "Batch deployments + CronJobs"),
    ("kube-manifests", "cde/stork", "Stork deployments + CronJobs"),
    ("kube-manifests", "cde/validx", "ValidX deployments"),
    ("kube-manifests", "cde/mozart", "Mozart deployments"),
    ("spinacode", "", "Spinnaker pipelines (deploy)"),
    ("alert-rules", "", "Prometheus alert rules (payouts/fts stuck + webhook failures)"),
    ("terraform-kong", "", "Edge routes/plugins incl. payouts-proxy-cutover"),
    ("proto", "", "protobuf contracts (payouts, ledger, stork, x-*, validx, workflows)"),
    ("config-proto", "", "DCS object schemas (rzp/x/merchant/payouts/*)"),
]


def sh(args, cwd=None):
    try:
        return subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=30).stdout.strip()
    except Exception:
        return ""


def pinned_shas():
    """Pinned SHAs recorded before the /tmp clone root decayed: snapshot manifest + ACCESS_DELTA + inventory."""
    out = {}
    for src in (DOM / "SNAPSHOT_MANIFEST.json",):
        try:
            for r in json.loads(src.read_text()).get("repositories", []):
                if isinstance(r, dict) and r.get("sha"):
                    out.setdefault((r.get("name") or "").lower(), r["sha"])
        except Exception:
            pass
    p = REPO / "reports" / "ACCESS_DELTA.md"
    if p.exists():
        for line in p.read_text().splitlines():
            m = re.match(r"\| razorpay/([\w.-]+) \|[^|]*\| OK \| ([0-9a-f]{8,}) \|", line)
            if m:
                out.setdefault(m.group(1).lower(), m.group(2))
    p = REPO / "reports" / "REPOSITORY_INVENTORY.csv"
    if p.exists():
        for r in csv.DictReader(p.open()):
            m = re.search(r"commit ([0-9a-f]{7,})", r.get("evidence") or "")
            if m:
                out.setdefault(r["repository"].split("/")[-1].lower(), m.group(1))
    return out


PINNED_SHAS = {}


def roles_from_prior():
    roles = {}
    p = REPO / "reports" / "REPOSITORY_INVENTORY.csv"
    if p.exists():
        for r in csv.DictReader(p.open()):
            name = r["repository"].split("/")[-1].replace(" (NOT ACCESSIBLE)", "").strip()
            roles[name.lower()] = (r.get("role_in_payouts") or "")[:200]
    p = REPO / "reports" / "ACCESS_DELTA.md"
    if p.exists():
        for line in p.read_text().splitlines():
            m = re.match(r"\| razorpay/([\w.-]+) \|[^|]*\|[^|]*\|[^|]*\| (.*?) \|\s*$", line)
            if m and m.group(1).lower() not in roles:
                roles[m.group(1).lower()] = m.group(2)[:200]
    return roles


def inventory():
    global PINNED_SHAS
    PINNED_SHAS = pinned_shas()
    root_file = REPO / ".local" / "repos-root"
    root = Path(root_file.read_text().strip()) if root_file.exists() else None
    roles = roles_from_prior()
    rows = []
    if root and root.exists():
        for p in sorted(root.iterdir()):
            if not (p / ".git").exists():
                continue
            sha = sh(["git", "rev-parse", "HEAD"], p); date = sh(["git", "log", "-1", "--format=%cI"], p)
            dirty = bool(sh(["git", "status", "--porcelain"], p))
            origin = sh(["git", "remote", "get-url", "origin"], p)
            gomod = (p / "go.mod").exists(); java = (p / "build.gradle").exists(); php = (p / "composer.json").exists()
            lang = "go" if gomod else "java" if java else "php" if php else "other"
            nfiles = sum(1 for f in p.rglob("*") if f.is_file() and ".git" not in f.parts)
            objects_ok = subprocess.run(["git", "-C", str(p), "cat-file", "-e", "HEAD"], capture_output=True).returncode == 0
            pinned = PINNED_SHAS.get(p.name.lower(), "")
            integrity = "intact" if (sha and objects_ok) else "DECAYED (git objects lost; working files partial)"
            rows.append({"repository": "razorpay/" + p.name, "kind": "repository", "sha": sha or pinned, "commit_date": date,
                         "dirty": dirty, "language": lang, "clone_path": str(p), "origin": origin,
                         "role_in_payouts": roles.get(p.name.lower(), ""),
                         "access": "cloned" if objects_ok else "cloned; " + integrity + ("; pinned sha from snapshot/ACCESS_DELTA" if pinned and not sha else ""),
                         "integrity": integrity, "working_files": nfiles})
    # repos known but not cloneable
    am = REPO / "reports" / "REPOSITORY_ACCESS_MATRIX.csv"
    cloned = {r["repository"] for r in rows}
    if am.exists():
        for r in csv.DictReader(am.open()):
            name = r["repository"]
            if name in cloned:
                continue
            if name.split("/")[-1].lower() in roles and "yes" not in (r.get("cloneable") or ""):
                rows.append({"repository": name, "kind": "repository", "sha": "", "commit_date": r.get("last_updated", ""),
                             "dirty": "", "language": "", "clone_path": "", "origin": "",
                             "role_in_payouts": roles.get(name.split("/")[-1].lower(), ""),
                             "access": "blocked: " + (r.get("reason_if_blocked") or r.get("cloneable") or "not cloneable")})
    # repos listed as still-not-accessible / not cloned in ACCESS_DELTA.md
    ad = REPO / "reports" / "ACCESS_DELTA.md"
    if ad.exists():
        sect = ad.read_text().split("## Still not accessible", 1)
        if len(sect) == 2:
            for line in sect[1].splitlines():
                m = re.match(r"\| razorpay/([\w.-]+) \| (.*?) \| (.*?) \|\s*$", line)
                if m and ("razorpay/" + m.group(1)) not in cloned:
                    rows.append({"repository": "razorpay/" + m.group(1), "kind": "repository", "sha": "", "commit_date": "",
                                 "dirty": "", "language": "", "clone_path": "", "origin": "",
                                 "role_in_payouts": m.group(2)[:200] + " | substitute: " + m.group(3)[:120],
                                 "access": "blocked: 404 to this identity (ACCESS_DELTA 2026-09-04)"})
        if "razorpay/shield" not in cloned:
            rows.append({"repository": "razorpay/shield", "kind": "repository", "sha": "", "commit_date": "", "dirty": "",
                         "language": "", "clone_path": "", "origin": "",
                         "role_in_payouts": "Risk engine; payouts uses shield-sdk (cloned) + payouts/pkg/shield",
                         "access": "granted, not cloned (6.7 GB); shield-sdk used instead"})
    for repo, path, what in DEPLOY_ARTIFACTS:
        p = root / repo / path if root else None
        present = bool(p and p.exists())
        rows.append({"repository": "razorpay/" + repo + ("/" + path if path else ""), "kind": "deployment_artifact",
                     "sha": sh(["git", "rev-parse", "HEAD"], root / repo) if present else "", "commit_date": "",
                     "dirty": "", "language": "yaml", "clone_path": str(p) if present else "", "origin": "",
                     "role_in_payouts": what, "access": "cloned" if present else "missing path"})
    # the twin itself
    rows.append({"repository": "rzp-payouts-architecture (twin)", "kind": "twin", "sha": sh(["git", "rev-parse", "HEAD"], REPO),
                 "commit_date": sh(["git", "log", "-1", "--format=%cI"], REPO), "dirty": bool(sh(["git", "status", "--porcelain"], REPO)),
                 "language": "python/yaml", "clone_path": str(REPO), "origin": "", "role_in_payouts": "functional twin", "access": "local"})
    out = DOM / "REPOSITORY_INVENTORY_M6.csv"
    keys = list(rows[0].keys())
    for r in rows:
        for k in keys:
            r.setdefault(k, "")
    with out.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys); w.writeheader(); w.writerows(rows)
    return rows, out


def remaining_access(rows):
    st = {}
    p = DOM / "GRAPH_STATS.json"
    if p.exists():
        st = json.loads(p.read_text())
    blocked_nodes = st.get("blocked_missing_access", [])
    graph_nodes = {}
    gp = DOM / "PAYOUTS_FUNCTIONAL_GRAPH.json"
    if gp.exists():
        graph_nodes = {n["id"]: n for n in json.loads(gp.read_text())["nodes"]}
    lines = ["# Remaining-access manifest — what prevents the final ~1% of Payouts fidelity",
             "", f"Generated {datetime.now(timezone.utc).isoformat()} by scripts/domain/inventory.py.",
             "Every item is a specific export, schema, config value, or repository the twin cannot derive from the",
             "readable clones. Nothing here asks for production access.", ""]
    lines += ["## A. Repositories not readable by this identity", "",
              "| repository | role for payouts | fallback in twin |", "|---|---|---|"]
    for r in rows:
        if r["kind"] == "repository" and r["access"].startswith("blocked"):
            lines.append(f"| {r['repository']} | {r['role_in_payouts'] or '—'} | see graph node fidelity (graph_only / substitute) |")
    dec = [r for r in rows if r.get("integrity", "").startswith("DECAYED")]
    lines += ["", "## A2. Clone-root decay (2026-09-08)", "",
              "The 2026-09-03 clone batch lived in a macOS /tmp scratchpad and was partially purged (git objects lost, working",
              "files partial). Surviving files were copied to the durable path in `.local/repos-root`; the five core services are",
              "unaffected (admitted build copies `.local/twin-repos/accepted/*` are complete and provenance-verified). Re-fetching",
              "these repositories at their pinned SHAs (recorded in `reports/domain/SNAPSHOT_MANIFEST.json` / ACCESS_DELTA) restores",
              "full source; until then their graph nodes rest on what the discovery lanes read before the purge.", "",
              "| repository | pinned sha | working files left |", "|---|---|---|"]
    for r in dec:
        lines.append(f"| {r['repository']} | {r.get('sha') or '?'} | {r.get('working_files')} |")
    lines += ["", "## B. Graph nodes classified `blocked_missing_access`", ""]
    if blocked_nodes:
        lines += ["| node | why blocked (evidence) |", "|---|---|"]
        for b in blocked_nodes:
            lines.append(f"| `{b}` | {(graph_nodes.get(b, {}).get('fidelity_evidence') or '')[:220]} |")
    else:
        lines.append("_(graph not built yet, or no blocked nodes)_")
    lines += ["", "## C. Non-repository artefacts (runtime values, schemas, exports)", "",
              "| category | artefact | why needed | minimum request | fidelity without it |", "|---|---|---|---|---|"]
    nr = REPO / "reports" / "NON_REPOSITORY_ACCESS_MATRIX.csv"
    if nr.exists():
        for r in csv.DictReader(nr.open()):
            lines.append(f"| {r['category'][:40]} | {r['artifact_needed'][:80]} | {r['why_needed'][:120]} | {r['minimum_permission'][:80]} | {r['fidelity_without_it'][:100]} |")
    lines += ["", "## D. Consolidated P0/P1 information requests", "",
              "See `reports/fidelity/REMAINING_INFORMATION_REQUESTS.md` (24 items, owners + minimal request each).",
              "Top items still open after M6:", "",
              "1. Splitz experiment variant/rollout values (runtime, Splitz MySQL only) — twin stub seeds recorded assignments.",
              "2. FastCron per-endpoint cadence for payouts `/v1/cron/*` — twin cron-driver cadence is assumed.",
              "3. Real DCS values per merchant segment — twin seeds illustrative profiles.",
              "4. recon `workflow_configs` rows — ART repair substitute is contract-level only.",
              "5. Cadence server version + production WFS config templates — approval engine is a reconstruction.",
              "6. Private Go module `integrations-utils` — real Mozart binary unbuildable; mozart-sim substitute.",
              "7. `asv` (account service) repo — ASV stub from goutils contract.", ""]
    out = DOM / "REMAINING_ACCESS_MANIFEST.md"
    out.write_text("\n".join(lines))
    return out


def main():
    rows, inv = inventory()
    ram = remaining_access(rows)
    kinds = {}
    for r in rows: kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
    print(json.dumps({"inventory": str(inv), "rows": len(rows), "by_kind": kinds,
                      "blocked_repos": sum(1 for r in rows if r["access"].startswith("blocked")),
                      "manifest": str(ram)}, indent=2))


if __name__ == "__main__":
    sys.exit(main())
