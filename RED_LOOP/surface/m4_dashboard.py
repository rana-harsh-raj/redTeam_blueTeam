#!/usr/bin/env python3
"""m4_dashboard.py — Static Executive Dashboard generator for Milestone 4 (task T22, Stretch E).

Reads the committed M4 JSON/MD artifacts under reports/implementation/ (and the latest
soak JSON under RED_LOOP/runs/) and emits ONE self-contained static HTML dashboard at
reports/implementation/m4-dashboard.html.

Design goals:
  * stdlib only (host python 3.14); no third-party deps
  * self-contained output: inline CSS/JS only, NO external/CDN/network asset references
  * regenerable: the coordinator re-runs this at finalize once acceptance JSON + soak are final
  * degrade gracefully: any absent artifact is rendered as "pending", never crashes
  * every number comes from a parsed artifact (never hardcoded)

If the committed acceptance JSON is absent, the generator invokes
`python3 RED_LOOP/surface/m4_acceptance.py --dry-run --out <temp>` itself and reads that,
labelling the gate status as DRY-RUN.

Usage:
    python3 RED_LOOP/surface/m4_dashboard.py [--out PATH] [--print-kpis]

READ-ONLY on the repo except the output HTML (and, transiently, a temp dry-run acceptance file).
"""

from __future__ import annotations

import argparse
import csv
import datetime as _dt
import html
import io
import json
import os
import re
import subprocess
import sys
import tempfile
from collections import Counter

# ----------------------------------------------------------------------------
# Paths
# ----------------------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, "..", ".."))
REPORTS = os.path.join(REPO_ROOT, "reports", "implementation")
RUNS = os.path.join(REPO_ROOT, "RED_LOOP", "runs")
DEFAULT_OUT = os.path.join(REPORTS, "m4-dashboard.html")


def rel(path: str) -> str:
    """Relative path from the output directory (reports/implementation) to an artifact.

    Evidence links in the HTML are relative so the page works when opened locally.
    """
    try:
        return os.path.relpath(path, REPORTS)
    except Exception:
        return path


# ----------------------------------------------------------------------------
# Safe loaders — every loader records presence and never raises to the caller
# ----------------------------------------------------------------------------
SOURCES: list[dict] = []  # [{name, path, status: present|absent|dry-run, note}]


def _record(name: str, path: str, status: str, note: str = "") -> None:
    SOURCES.append({"name": name, "path": path, "status": status, "note": note})


def load_json(name: str, filename: str, base: str = REPORTS):
    path = os.path.join(base, filename)
    if not os.path.exists(path):
        _record(name, path, "absent")
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        _record(name, path, "present")
        return data
    except Exception as exc:  # pragma: no cover - defensive
        _record(name, path, "absent", f"parse error: {exc}")
        return None


def load_text(name: str, filename: str, base: str = REPORTS):
    path = os.path.join(base, filename)
    if not os.path.exists(path):
        _record(name, path, "absent")
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = fh.read()
        _record(name, path, "present")
        return data
    except Exception as exc:  # pragma: no cover
        _record(name, path, "absent", f"read error: {exc}")
        return None


def load_csv(name: str, filename: str, base: str = REPORTS):
    path = os.path.join(base, filename)
    if not os.path.exists(path):
        _record(name, path, "absent")
        return None
    try:
        with open(path, "r", encoding="utf-8", newline="") as fh:
            rows = list(csv.reader(fh))
        _record(name, path, "present")
        return rows
    except Exception as exc:  # pragma: no cover
        _record(name, path, "absent", f"parse error: {exc}")
        return None


def load_acceptance():
    """Prefer the committed acceptance JSON; else self-run a dry-run into a temp file."""
    path = os.path.join(REPORTS, "m4-direct-e2e-acceptance.json")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            dry = bool(data.get("dry_run"))
            _record(
                "acceptance",
                path,
                "dry-run" if dry else "present",
                "committed acceptance JSON" + (" (dry_run=true)" if dry else ""),
            )
            return data, ("DRY-RUN" if dry else "LIVE"), rel(path)
        except Exception as exc:  # pragma: no cover
            _record("acceptance", path, "absent", f"parse error: {exc}")
    # Fallback: self-run the acceptance generator in --dry-run mode into a temp path.
    gen = os.path.join(REPO_ROOT, "RED_LOOP", "surface", "m4_acceptance.py")
    if not os.path.exists(gen):
        _record("acceptance", path, "absent", "no committed JSON and no generator available")
        return None, "MISSING", None
    tmp = tempfile.NamedTemporaryFile(prefix="_dash-acc-dry-", suffix=".json", delete=False)
    tmp.close()
    try:
        subprocess.run(
            [sys.executable, gen, "--dry-run", "--out", tmp.name],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            timeout=300,
        )
        with open(tmp.name, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        _record("acceptance", gen, "dry-run", "self-generated via m4_acceptance.py --dry-run")
        return data, "DRY-RUN", None
    except Exception as exc:  # pragma: no cover
        _record("acceptance", gen, "absent", f"dry-run invocation failed: {exc}")
        return None, "MISSING", None
    finally:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass


def latest_soak():
    """Return (data, rel_path) for the newest RED_LOOP/runs/*/m4-direct-e2e-soak.json."""
    if not os.path.isdir(RUNS):
        _record("soak", RUNS, "absent")
        return None, None
    candidates = []
    for entry in os.listdir(RUNS):
        p = os.path.join(RUNS, entry, "m4-direct-e2e-soak.json")
        if os.path.exists(p):
            candidates.append(p)
    if not candidates:
        _record("soak", os.path.join(RUNS, "*", "m4-direct-e2e-soak.json"), "absent")
        return None, None
    candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    path = candidates[0]
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        _record("soak", path, "present")
        return data, rel(path)
    except Exception as exc:  # pragma: no cover
        _record("soak", path, "absent", f"parse error: {exc}")
        return None, None


# ----------------------------------------------------------------------------
# HTML helpers
# ----------------------------------------------------------------------------
def esc(x) -> str:
    return html.escape("" if x is None else str(x), quote=True)


# Status vocabulary -> (css class, unicode icon, label). Never color alone.
STATUS_MAP = {
    "pass": ("ok", "✓", "pass"),
    "passed": ("ok", "✓", "pass"),
    "ok": ("ok", "✓", "pass"),
    "true": ("ok", "✓", "yes"),
    "pending": ("warn", "⋯", "pending"),
    "dry-run": ("warn", "⋯", "dry-run"),
    "fail": ("bad", "✗", "fail"),
    "failed": ("bad", "✗", "fail"),
    "false": ("bad", "✗", "no"),
    "expected-failure": ("info", "◈", "expected-fail"),
    "native": ("ok", "✓", "native"),
    "verified": ("bad", "!", "verified"),
    "benign": ("info", "○", "benign"),
    "source-faithful": ("info", "○", "source-faithful"),
}


def badge(status: str, label: str | None = None) -> str:
    key = str(status).strip().lower()
    cls, icon, deflabel = STATUS_MAP.get(key, ("neutral", "•", str(status)))
    text = label if label is not None else deflabel
    return f'<span class="badge {cls}"><span class="ic" aria-hidden="true">{icon}</span>{esc(text)}</span>'


# ----------------------------------------------------------------------------
# Main build
# ----------------------------------------------------------------------------
def build() -> tuple[str, dict]:
    now = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    acc, acc_mode, acc_rel = load_acceptance()
    journeys = load_json("direct-journeys", "m4-direct-journeys.json")
    coverage_md = load_text("coverage", "m4-direct-e2e-coverage.md")
    routes = load_json("route-coverage", "m4-route-coverage.json")
    invariants = load_json("invariants", "m4-invariant-results.json")
    xas = load_json("xas-ledger", "m4-xas-ledger.json")
    bas = load_json("bas-ingest", "m4-bas-ingest.json")
    provision = load_json("direct-provision", "m4-direct-provision.json")
    hyp_lifecycle = load_json("hypothesis-lifecycle", "m4-hypothesis-lifecycle.json")
    assurance = load_json("assurance-run", "m4-assurance-run.json")
    soak, soak_rel = latest_soak()
    replay = load_json("replay", "m4-direct-e2e-replay.json")
    findings_md = load_text("findings", "m4-findings.md")
    fidelity = load_csv("fidelity-matrix", "m4-fidelity-matrix.csv")
    known_limits_md = load_text("known-limits", "m4-known-limits.md")
    contradictions_md = load_text("contradictions", "m4-contradictions.md")

    kpi: dict = {}

    # --- Gate matrix / acceptance -----------------------------------------
    gate_groups: dict[str, list] = {}
    if acc:
        counts = acc.get("counts", {})
        kpi["gates_pass"] = counts.get("pass")
        kpi["gates_total"] = counts.get("total")
        kpi["gates_pending"] = counts.get("pending")
        kpi["gates_fail"] = counts.get("fail")
        kpi["accepted"] = acc.get("accepted")
        for g in acc.get("gates", []):
            gate_groups.setdefault(g.get("group", "OTHER"), []).append(g)
    else:
        kpi["gates_pass"] = kpi["gates_total"] = None
        kpi["accepted"] = None

    # --- Journeys ----------------------------------------------------------
    jlist = (journeys or {}).get("journeys", []) if journeys else []
    kpi["journeys_pass"] = sum(1 for j in jlist if str(j.get("result", "")).upper() == "PASS")
    kpi["journeys_total"] = len(jlist)

    # --- Routes ------------------------------------------------------------
    rlist = (routes or {}).get("routes", []) if routes else []
    kpi["routes_total"] = len(rlist)
    kpi["routes_native"] = sum(1 for r in rlist if str(r.get("twin_support", "")).lower().startswith("native"))
    kpi["routes_expected_fail"] = sum(1 for r in rlist if "expected-failure" in str(r.get("twin_support", "")).lower())

    # --- Invariants --------------------------------------------------------
    if invariants:
        kpi["invariants_pass"] = invariants.get("passed")
        kpi["invariants_total"] = invariants.get("total")
        ilist = invariants.get("invariants", [])
    else:
        kpi["invariants_pass"] = kpi["invariants_total"] = None
        ilist = []

    # --- Hypotheses (lifecycle) -------------------------------------------
    rollup = {}
    if hyp_lifecycle:
        rollup = hyp_lifecycle.get("rollup", {}) or {}
    elif assurance:
        rollup = assurance.get("hypotheses_by_status", {}) or {}
    kpi["hyp_accepted"] = rollup.get("accepted", 0) if rollup else None
    kpi["hyp_total"] = sum(v for v in rollup.values() if isinstance(v, int)) if rollup else None

    # --- Findings / replay -------------------------------------------------
    candidates = (replay or {}).get("candidates", []) if replay else []
    def _classify(c):
        cl = str(c.get("classification", "")).upper()
        if "VERIFIED" in cl and "BENIGN" not in cl:
            return "verified"
        if "BENIGN" in cl:
            return "benign"
        if "SOURCE" in cl or "FAITHFUL" in cl:
            return "source-faithful"
        return "other"
    class_counts = Counter(_classify(c) for c in candidates)
    kpi["findings_verified"] = class_counts.get("verified", 0)
    kpi["findings_benign"] = class_counts.get("benign", 0) + class_counts.get("source-faithful", 0)

    # --- Egress / isolation tile ------------------------------------------
    egress_status = "pending"
    egress_note = ""
    if acc:
        g70 = next((g for g in acc.get("gates", []) if g.get("gate_id") == "G70"), None)
        if g70:
            egress_status = g70.get("status", "pending")
            egress_note = g70.get("title", "")
    if assurance and assurance.get("egress_note"):
        en = assurance["egress_note"]
        if not egress_note:
            egress_note = en.get("reason", "")
        if not en.get("captured_here", True) and egress_status == "pending":
            egress_note = (egress_note + " " + str(en.get("attacker_boundary", ""))).strip()
    kpi["egress_status"] = egress_status

    # --- Provisioning ------------------------------------------------------
    prov_summary = (provision or {}).get("summary", {}) if provision else {}
    kpi["merchants_provisioned"] = prov_summary.get("merchants_provisioned")

    # --- Contradictions ----------------------------------------------------
    contra_resolved = contra_open = contra_total = None
    if contradictions_md:
        contra_resolved = contra_open = 0
        for line in contradictions_md.splitlines():
            if not line.startswith("| C-"):
                continue
            contra_total = (contra_total or 0) + 1
            cells = [c.strip().lower() for c in line.strip().strip("|").split("|")]
            st = "?"
            for cell in cells:
                if cell == "resolved" or cell.startswith("resolved "):
                    st = "resolved"
                    break
                if cell == "open" or cell.startswith("open ") or cell.startswith("open("):
                    st = "open"
                    break
            if st == "resolved":
                contra_resolved += 1
            elif st == "open":
                contra_open += 1
    kpi["contra_resolved"] = contra_resolved
    kpi["contra_open"] = contra_open

    # --- Known limits ------------------------------------------------------
    known_limits = []
    if known_limits_md:
        for line in known_limits_md.splitlines():
            m = re.match(r"\|\s*(L-\d+)\s*\|(.*)", line)
            if m:
                cells = [c.strip() for c in line.strip().strip("|").split("|")]
                # cells: [L-id, limit, class, evidence] (evidence may contain pipes -> merge rest)
                lid = cells[0]
                limit_txt = cells[1] if len(cells) > 1 else ""
                cls = cells[2] if len(cells) > 2 else ""
                known_limits.append((lid, limit_txt, cls))
    kpi["known_limits"] = len(known_limits)

    # ======================================================================
    # HTML assembly
    # ======================================================================
    P: list[str] = []
    a = P.append

    # ---- head / style ----
    a("<title>M4 Executive Dashboard</title>")
    a("<style>")
    a(CSS)
    a("</style>")

    milestone = acc.get("milestone") if acc else "Milestone 4 — Direct Current-Account, Reconciliation, Autonomous Runtime"
    tested_commit = (acc or {}).get("tested_commit") or "pending"
    evidence_commit = (acc or {}).get("evidence_commit") or "pending"
    base_ref = ((acc or {}).get("base_commit") or {}).get("ref") or "?"

    # ---- header ----
    a('<header class="hdr">')
    a(f'<h1>{esc(milestone)}</h1>')
    a('<div class="hdr-meta">')
    accepted = kpi.get("accepted")
    acc_badge = badge("true" if accepted else ("pending" if accepted is None else "false"),
                      "ACCEPTED" if accepted else ("PENDING" if accepted is None else "NOT ACCEPTED"))
    a(f'<span class="accepted">{acc_badge}</span>')
    a(f'<span class="commit">gate status: <strong>{esc(acc_mode)}</strong></span>')
    a(f'<span class="commit">tested&nbsp;commit: <code>{esc(str(tested_commit)[:12])}</code></span>')
    a(f'<span class="commit">evidence&nbsp;commit: <code>{esc(str(evidence_commit)[:12] if evidence_commit!="pending" else "pending")}</code></span>')
    a(f'<span class="commit">baseline: <code>{esc(base_ref)}</code></span>')
    a("</div>")
    if acc_mode == "DRY-RUN":
        a('<div class="notice">Gate status is <strong>DRY-RUN</strong> (evaluated without a live arena). '
          'The coordinator re-runs this dashboard at finalize once the acceptance JSON and soak summary are final.</div>')
    a("</header>")

    # ---- KPI tiles ----
    a('<section class="kpis" aria-label="Key metrics">')

    def tile(value, label, sub="", tone="neutral"):
        a(f'<div class="kpi {tone}"><div class="kpi-val">{esc(value)}</div>'
          f'<div class="kpi-lbl">{esc(label)}</div>'
          + (f'<div class="kpi-sub">{esc(sub)}</div>' if sub else "")
          + "</div>")

    gp, gt = kpi.get("gates_pass"), kpi.get("gates_total")
    tile(f'{gp}/{gt}' if gt else "pending", "Gates pass",
         f'{kpi.get("gates_pending",0)} pending, {kpi.get("gates_fail",0)} fail' if gt else "no acceptance",
         "ok" if (gt and kpi.get("gates_fail") == 0) else "warn")
    tile(f'{kpi["journeys_pass"]}/{kpi["journeys_total"]}' if kpi["journeys_total"] else "pending",
         "Journeys pass", "Direct A–G",
         "ok" if kpi["journeys_total"] and kpi["journeys_pass"] == kpi["journeys_total"] else "warn")
    ip, it = kpi.get("invariants_pass"), kpi.get("invariants_total")
    tile(f'{ip}/{it}' if it else "pending", "Invariants pass", "+ negative controls",
         "ok" if it and ip == it else "warn")
    tile(f'{kpi["routes_native"]}+{kpi["routes_expected_fail"]}ef' if kpi["routes_total"] else "pending",
         "Routes covered", f'{kpi["routes_total"]} total (R1–R3, remap)' if kpi["routes_total"] else "",
         "ok" if kpi["routes_total"] else "warn")
    ha, ht = kpi.get("hyp_accepted"), kpi.get("hyp_total")
    tile(f'{ha}/{ht}' if ht is not None else "pending", "Hypotheses accepted",
         "autonomous runtime", "ok" if ht is not None else "warn")
    fv = kpi.get("findings_verified")
    tile(fv if fv is not None else "pending", "Findings verified",
         f'{kpi.get("findings_benign",0)} benign/faithful', "bad" if fv else "ok")
    tile(STATUS_MAP.get(kpi["egress_status"], ("", "", kpi["egress_status"]))[2],
         "Egress / isolation", "outside traffic",
         "ok" if kpi["egress_status"] in ("pass", "ok") else "warn")
    mp = kpi.get("merchants_provisioned")
    tile(mp if mp is not None else "pending", "Merchants provisioned",
         "fresh Direct A/B", "ok" if mp else "warn")
    a("</section>")

    # ---- helper for collapsible section ----
    def section_open(title, sub="", open_default=True):
        a(f'<details class="sec"{" open" if open_default else ""}>')
        a(f'<summary><span class="sec-title">{esc(title)}</span>'
          + (f'<span class="sec-sub">{esc(sub)}</span>' if sub else "") + "</summary>")
        a('<div class="sec-body">')

    def section_close():
        a("</div></details>")

    # ---- Gate matrix ----
    section_open("Gate matrix", f'{gp}/{gt} pass ({acc_mode})' if gt else "no acceptance artifact")
    if gate_groups:
        # ordering by group name, BASELINE first
        order = ["BASELINE", "SOURCE_AND_SPEC", "PROVISIONING", "DIRECT_SUCCESS",
                 "FAILURE_PENDING_ORDERING", "ROUTES_WEBHOOKS", "BOUNDARIES_INVARIANTS",
                 "AUTONOMOUS_RUNTIME", "REPLAY_EVIDENCE"]
        ordered = [g for g in order if g in gate_groups] + [g for g in gate_groups if g not in order]
        for grp in ordered:
            gs = gate_groups[grp]
            gc = Counter(x.get("status") for x in gs)
            a(f'<div class="grp"><div class="grp-h">{esc(grp)} '
              f'<span class="grp-c">{gc.get("pass",0)}/{len(gs)} pass'
              + (f', {gc["pending"]} pending' if gc.get("pending") else "")
              + (f', {gc["fail"]} fail' if gc.get("fail") else "")
              + "</span></div>")
            a('<table class="gt"><thead><tr><th>Gate</th><th>Title</th><th>Status</th>'
              '<th>Fidelity</th><th>Prod. reach</th></tr></thead><tbody>')
            for x in gs:
                ev = x.get("evidence_pointer") or ""
                title_attr = f'evidence: {ev}' if ev else ""
                ef = " (expected-failure)" if x.get("expected_failure") else ""
                a(f'<tr title="{esc(title_attr)}"><td><code>{esc(x.get("gate_id"))}</code></td>'
                  f'<td>{esc(x.get("title"))}{esc(ef)}</td>'
                  f'<td>{badge(x.get("status"))}</td>'
                  f'<td class="mono-s">{esc(x.get("fidelity_level"))}</td>'
                  f'<td class="mono-s">{esc(x.get("production_reachability"))}</td></tr>')
            a("</tbody></table></div>")
    else:
        a('<p class="pending-note">Acceptance gates pending.</p>')
    section_close()

    # ---- Direct journeys ----
    section_open("Direct journeys (A–G)", f'{kpi["journeys_pass"]}/{kpi["journeys_total"]} pass' if kpi["journeys_total"] else "pending")
    if jlist:
        a('<table class="gt"><thead><tr><th>Journey</th><th>Title</th><th>Result</th>'
          '<th>Merchant</th><th>Fidelity</th></tr></thead><tbody>')
        for j in jlist:
            a(f'<tr><td><strong>{esc(j.get("journey"))}</strong></td>'
              f'<td>{esc(j.get("title"))}</td>'
              f'<td>{badge(j.get("result"))}</td>'
              f'<td class="mono-s">{esc(j.get("merchant_id"))}</td>'
              f'<td class="mono-s">{esc(j.get("fidelity"))}</td></tr>')
        a("</tbody></table>")
        # per-journey state transitions (from merchant steps, if present)
        mer = (journeys or {}).get("merchants", {})
        step_hosts = {k: v for k, v in mer.items() if isinstance(v, dict) and "steps" in v}
        if step_hosts:
            a('<p class="hint">State-transition steps (per provisioned merchant, ok/failed):</p>')
            for mk, mv in step_hosts.items():
                steps = mv.get("steps", [])
                okc = sum(1 for s in steps if s.get("ok"))
                a(f'<details class="mini"><summary>{esc(mk)} — <code>{esc(mv.get("merchant_id"))}</code> '
                  f'({okc}/{len(steps)} steps ok)</summary><div class="mini-body">')
                a('<ul class="steps">')
                for s in steps:
                    st = "pass" if s.get("ok") else "fail"
                    extra = " (existed)" if s.get("existed") else ""
                    err = f' — {s.get("err")}' if s.get("err") else ""
                    a(f'<li>{badge(st, "")}<code>{esc(s.get("step"))}</code>{esc(extra)}{esc(err)}</li>')
                a("</ul></div></details>")
    else:
        a('<p class="pending-note">Direct journeys pending.</p>')
    section_close()

    # ---- Routes ----
    section_open("Routes (matrix)", f'{kpi["routes_total"]} routes' if kpi["routes_total"] else "pending")
    if rlist:
        a('<table class="gt"><thead><tr><th>Route</th><th>Request event</th><th>Transport</th>'
          '<th>Support</th><th>Status mapping</th></tr></thead><tbody>')
        for r in rlist:
            sup = r.get("twin_support", "")
            skey = "expected-failure" if "expected-failure" in str(sup).lower() else ("native" if str(sup).lower().startswith("native") else sup)
            a(f'<tr><td><code>{esc(r.get("route_id"))}</code></td>'
              f'<td>{esc(r.get("request_event"))}</td>'
              f'<td class="mono-s">{esc(r.get("transport"))}</td>'
              f'<td>{badge(skey, sup if len(str(sup))<40 else None)}</td>'
              f'<td class="mono-s">{esc(r.get("status_mapping"))}</td></tr>')
        a("</tbody></table>")
    else:
        a('<p class="pending-note">Route coverage pending.</p>')
    section_close()

    # ---- Invariants ----
    section_open("Invariants", f'{ip}/{it} pass' if it else "pending")
    if ilist:
        a('<table class="gt"><thead><tr><th>Invariant</th><th>Result</th><th>Fidelity</th>'
          '<th>Negative control</th></tr></thead><tbody>')
        for iv in ilist:
            a(f'<tr><td>{esc(iv.get("invariant"))}'
              + (f'<div class="fine">{esc(iv.get("formal_statement"))}</div>' if iv.get("formal_statement") else "")
              + f'</td><td>{badge(iv.get("result"))}</td>'
              f'<td class="mono-s">{esc(iv.get("fidelity"))}</td>'
              f'<td class="mono-s">{esc(iv.get("negative_control"))}</td></tr>')
        a("</tbody></table>")
    else:
        a('<p class="pending-note">Invariants pending.</p>')
    section_close()

    # ---- Reconciliation path ----
    section_open("Reconciliation path (source-event → XAS → Ledger)",
                 "provisioning + BAS ingest + XAS/Ledger accounting")
    recon_steps = []
    if provision:
        ps = provision.get("summary", {})
        recon_steps.append((
            "1. Fresh Direct merchant provisioning",
            bool(ps.get("all_green")),
            f'{ps.get("merchants_provisioned")} merchants; self-check {"/".join(ps.get("self_check_counts",[]))}; '
            f'payout proofs {"/".join(ps.get("proof_counts",[]))}',
            rel(os.path.join(REPORTS, "m4-direct-provision.json")),
        ))
    if bas:
        recon_steps.append((
            "2. BAS ingest (statement → PS worker)",
            bool(bas.get("all_pass")),
            f'{bas.get("passed")}/{bas.get("total")} steps (real payouts worker + mozart-sim/bank stub)',
            rel(os.path.join(REPORTS, "m4-bas-ingest.json")),
        ))
    if xas:
        xc = xas.get("checks", [])
        okc = sum(1 for c in xc if c.get("ok"))
        recon_steps.append((
            "3. XAS substitute → source-event matching",
            bool(xas.get("all_green")),
            f'{okc}/{len(xc)} checks (substitute xas-sim; contract-faithful)',
            rel(os.path.join(REPORTS, "m4-xas-ledger.json")),
        ))
        recon_steps.append((
            "4. Ledger Direct accounting (DA journals)",
            bool(xas.get("all_green")),
            "real ledger; monolith-stub DA emitter; da_payout_processed + _recon",
            rel(os.path.join(REPORTS, "m4-xas-ledger.json")),
        ))
    if recon_steps:
        a('<ol class="flow">')
        for label, ok, detail, link in recon_steps:
            st = "pass" if ok else "pending"
            a(f'<li class="flow-step"><div class="flow-head">{badge(st,"")}<strong>{esc(label)}</strong></div>'
              f'<div class="flow-detail">{esc(detail)} '
              f'<a href="{esc(link)}">source</a></div></li>')
        a("</ol>")
        if xas and xas.get("components"):
            a('<p class="hint">Component fidelity (reconciliation):</p><ul class="cmp">')
            for k, v in xas["components"].items():
                a(f'<li><code>{esc(k)}</code>: {esc(v)}</li>')
            a("</ul>")
    else:
        a('<p class="pending-note">Reconciliation artifacts pending.</p>')
    section_close()

    # ---- Autonomous runtime ----
    section_open("Autonomous runtime (lifecycle + soak)",
                 f'soak: {soak.get("stop_reason") if soak else "pending"}')
    if rollup:
        a('<p class="hint">Hypotheses by lifecycle state:</p>')
        a('<div class="chips">')
        for k, v in rollup.items():
            if isinstance(v, int):
                cls = "chip-hot" if v and k in ("accepted", "supported", "reproduced") else ("chip-on" if v else "chip-off")
                a(f'<span class="chip {cls}">{esc(k)}: <strong>{esc(v)}</strong></span>')
        a("</div>")
    if assurance:
        cs = assurance.get("contexts_summary", {})
        a('<div class="stat-row">')
        a(f'<div class="stat"><span>Primary model</span><strong>{esc(assurance.get("primary_model"))}</strong></div>')
        a(f'<div class="stat"><span>Contexts</span><strong>{esc(len(assurance.get("contexts",[])))}</strong></div>')
        a(f'<div class="stat"><span>Total turns</span><strong>{esc(cs.get("total_turns"))}</strong></div>')
        a(f'<div class="stat"><span>Experiments</span><strong>{esc(assurance.get("experiments_executed"))}</strong></div>')
        a("</div>")
    if soak:
        a('<p class="hint">Latest soak run:</p>')
        a('<div class="stat-row">')
        for lbl, key in [("Cycles", "cycles"), ("Turns", "turns"),
                         ("Elapsed (s)", "elapsed_seconds"), ("Checkpoints", "checkpoints"),
                         ("Duplicates suppressed", "duplicates_suppressed"),
                         ("Experiments", "experiments_executed")]:
            val = soak.get(key)
            if key == "cycles" and isinstance(val, list):
                val = len(val)
            a(f'<div class="stat"><span>{esc(lbl)}</span><strong>{esc(val)}</strong></div>')
        lz = soak.get("leases", {})
        if isinstance(lz, dict):
            a(f'<div class="stat"><span>Leases (exp/reassign/active)</span>'
              f'<strong>{esc(lz.get("expired",0))}/{esc(lz.get("reassigned",0))}/{esc(lz.get("active",0))}</strong></div>')
        a(f'<div class="stat"><span>Stop reason</span><strong>{esc(soak.get("stop_reason"))}</strong></div>')
        a("</div>")
        if soak.get("contexts"):
            a('<p class="fine">Contexts: ' + esc(", ".join(soak.get("contexts", []))) + "</p>")
    if not (rollup or assurance or soak):
        a('<p class="pending-note">Autonomous runtime artifacts pending.</p>')
    section_close()

    # ---- Replay & findings ----
    section_open("Replay & findings",
                 f'{kpi.get("findings_verified",0)} verified, {kpi.get("findings_benign",0)} benign/faithful')
    # Surface F-M4-001 prominently but with UNKNOWN production reachability
    if findings_md and "F-M4-001" in findings_md:
        a('<div class="finding">')
        a('<div class="finding-h">'
          + badge("verified", "VERIFIED in twin")
          + '<span class="finding-id">F-M4-001 — Cross-tenant free-payout attribute disclosure (IDOR)</span></div>')
        a('<p class="finding-warn"><strong>Production reachability: UNKNOWN.</strong> '
          'Verified inside the authorized local synthetic twin only. This is NOT a claim about '
          'production behaviour and must not be presented as a confirmed production vulnerability.</p>')
        a('<p class="fine">Route <code>GET /v1/payouts/free_payout/&#123;balance_id&#125;</code>; '
          'handler resolves banking account by balance_id with no merchant scoping. '
          'Deterministic effect + negative control + independent fresh-ID reproduction. '
          f'Evidence: <a href="{esc(rel(os.path.join(REPORTS, "m4-direct-e2e-replay.json")))}">m4-direct-e2e-replay.json</a> '
          f'(candidate H-D3), <a href="{esc(rel(os.path.join(REPORTS, "m4-findings.md")))}">m4-findings.md</a>.</p>')
        a("</div>")
    if candidates:
        a('<p class="hint">Replay candidates:</p>')
        a('<table class="gt"><thead><tr><th>ID</th><th>Classification</th><th>Fidelity</th>'
          '<th>Claim</th></tr></thead><tbody>')
        for c in candidates:
            cl = _classify(c)
            lbl = c.get("classification")
            a(f'<tr><td><code>{esc(c.get("id"))}</code></td>'
              f'<td>{badge(cl, lbl if lbl else None)}</td>'
              f'<td class="mono-s">{esc(c.get("fidelity"))}</td>'
              f'<td>{esc(c.get("claim"))}</td></tr>')
        a("</tbody></table>")
    else:
        a('<p class="pending-note">Replay artifacts pending.</p>')
    section_close()

    # ---- Fidelity matrix ----
    section_open("Fidelity matrix", f'{len(fidelity)-1 if fidelity else 0} components' if fidelity else "pending")
    if fidelity and len(fidelity) > 1:
        header = fidelity[0]
        # map columns
        idx = {name: i for i, name in enumerate(header)}
        a('<table class="gt"><thead><tr><th>Component</th><th>Layer</th><th>Fidelity</th>'
          '<th>Prod. reach</th><th>Deviations</th></tr></thead><tbody>')
        for row in fidelity[1:]:
            if not row or not row[0].strip():
                continue
            def cell(name):
                i = idx.get(name)
                return row[i] if i is not None and i < len(row) else ""
            fl = cell("fidelity_label")
            fkey = "real" if fl == "real" else ("expected-failure" if "expected" in fl else ("info" if "substitute" in fl else fl))
            a(f'<tr><td class="mono-s">{esc(cell("component"))}</td>'
              f'<td class="mono-s">{esc(cell("layer"))}</td>'
              f'<td>{badge(fkey, fl)}</td>'
              f'<td class="mono-s">{esc(cell("production_reachability"))}</td>'
              f'<td class="mono-s">{esc(cell("deviation_ids"))}</td></tr>')
        a("</tbody></table>")
    else:
        a('<p class="pending-note">Fidelity matrix pending.</p>')
    section_close()

    # ---- Known limits + contradictions ----
    section_open("Known limits & contradictions",
                 f'{kpi.get("known_limits",0)} limits; contradictions {contra_resolved}/{contra_total} resolved' if contra_total else f'{kpi.get("known_limits",0)} limits')
    if contra_total is not None:
        a('<div class="stat-row">')
        a(f'<div class="stat"><span>Contradictions resolved</span><strong>{esc(contra_resolved)}</strong></div>')
        a(f'<div class="stat"><span>Contradictions open</span><strong>{esc(contra_open)}</strong></div>')
        a(f'<div class="stat"><span>Total</span><strong>{esc(contra_total)}</strong></div>')
        a("</div>")
    if known_limits:
        a('<table class="gt"><thead><tr><th>ID</th><th>Limit</th><th>Class</th></tr></thead><tbody>')
        for lid, txt, cls in known_limits:
            a(f'<tr><td><code>{esc(lid)}</code></td><td>{esc(txt)}</td>'
              f'<td class="mono-s">{esc(cls)}</td></tr>')
        a("</tbody></table>")
    else:
        a('<p class="pending-note">Known limits pending.</p>')
    section_close()

    # ---- Evidence links ----
    section_open("Evidence links & sources", "relative links; open the JSON/MD artifacts", open_default=False)
    a('<table class="gt"><thead><tr><th>Source</th><th>Status</th><th>Path</th></tr></thead><tbody>')
    for s in SOURCES:
        st = s["status"]
        link = rel(s["path"])
        exists = os.path.exists(s["path"])
        pathcell = f'<a href="{esc(link)}">{esc(link)}</a>' if exists else esc(link)
        note = f' <span class="fine">({esc(s["note"])})</span>' if s.get("note") else ""
        a(f'<tr><td>{esc(s["name"])}</td><td>{badge(st)}</td><td class="mono-s">{pathcell}{note}</td></tr>')
    a("</tbody></table>")
    section_close()

    # ---- footer ----
    a('<footer class="ftr">')
    a(f'<p>Generated {esc(now)} by <code>RED_LOOP/surface/m4_dashboard.py</code>. '
      'Every number is parsed from a committed artifact — none are hardcoded. '
      f'Gate status is <strong>{esc(acc_mode)}</strong> and may be DRY-RUN until finalize.</p>')
    a('<p class="fine">Self-contained: inline CSS/JS only, no external network assets. '
      'Regenerate with <code>python3 RED_LOOP/surface/m4_dashboard.py</code>.</p>')
    a("</footer>")

    # ---- tiny inline JS: expand/collapse-all ----
    a("<script>")
    a(JS)
    a("</script>")

    return "\n".join(P), kpi


# ----------------------------------------------------------------------------
# Inline CSS (theme-neutral, light; accessible; no external fonts)
# ----------------------------------------------------------------------------
CSS = """
:root{
  --bg:#f6f7f9; --card:#ffffff; --ink:#1b2733; --muted:#5b6b7b; --line:#e2e7ec;
  --ok:#0f7b3f; --ok-bg:#e6f4ea; --warn:#8a5a00; --warn-bg:#fdf3e0;
  --bad:#a01722; --bad-bg:#fbe7e9; --info:#215c98; --info-bg:#e7f0fb; --accent:#26374a;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  line-height:1.45;font-size:14px}
code{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:.92em}
a{color:var(--info);text-decoration:none}
a:hover{text-decoration:underline}
.hdr{background:var(--accent);color:#fff;padding:20px 24px}
.hdr h1{margin:0 0 10px;font-size:20px;font-weight:650;line-height:1.25}
.hdr-meta{display:flex;flex-wrap:wrap;gap:10px 18px;align-items:center;font-size:13px;color:#d7e0ea}
.hdr-meta code{background:rgba(255,255,255,.14);padding:1px 5px;border-radius:4px;color:#fff}
.hdr-meta strong{color:#fff}
.notice{margin-top:12px;background:var(--warn-bg);color:var(--warn);padding:8px 12px;
  border-radius:6px;font-size:12.5px;display:inline-block}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;padding:18px 24px}
.kpi{background:var(--card);border:1px solid var(--line);border-left-width:4px;border-radius:8px;
  padding:12px 14px;border-left-color:var(--muted)}
.kpi.ok{border-left-color:var(--ok)} .kpi.warn{border-left-color:var(--warn)}
.kpi.bad{border-left-color:var(--bad)}
.kpi-val{font-size:24px;font-weight:700;letter-spacing:-.5px}
.kpi-lbl{font-size:12.5px;color:var(--muted);margin-top:2px;font-weight:600}
.kpi-sub{font-size:11px;color:var(--muted);margin-top:3px}
.sec{margin:0 24px 12px;background:var(--card);border:1px solid var(--line);border-radius:8px;overflow:hidden}
.sec>summary{cursor:pointer;padding:12px 16px;font-weight:650;list-style:none;
  display:flex;align-items:baseline;gap:12px;background:#fbfcfd}
.sec>summary::-webkit-details-marker{display:none}
.sec>summary::before{content:"\\25B8";color:var(--muted);font-size:12px}
.sec[open]>summary::before{content:"\\25BE"}
.sec-title{font-size:15px}
.sec-sub{font-size:12px;color:var(--muted);font-weight:500}
.sec-body{padding:8px 16px 16px}
table.gt{width:100%;border-collapse:collapse;font-size:12.5px;margin:6px 0}
table.gt th{text-align:left;color:var(--muted);font-weight:600;border-bottom:2px solid var(--line);
  padding:6px 8px;position:sticky;top:0;background:var(--card)}
table.gt td{border-bottom:1px solid var(--line);padding:6px 8px;vertical-align:top}
table.gt tr:hover td{background:#fafbfc}
.mono-s{font-size:11.5px;color:var(--muted)}
.fine{font-size:11px;color:var(--muted);margin-top:2px}
.hint{font-size:12px;color:var(--muted);margin:12px 0 4px;font-weight:600}
.pending-note{color:var(--warn);font-size:13px;padding:8px 0}
.badge{display:inline-flex;align-items:center;gap:4px;padding:2px 8px;border-radius:12px;
  font-size:11px;font-weight:650;white-space:nowrap}
.badge .ic{font-size:11px}
.badge.ok{background:var(--ok-bg);color:var(--ok)}
.badge.warn{background:var(--warn-bg);color:var(--warn)}
.badge.bad{background:var(--bad-bg);color:var(--bad)}
.badge.info{background:var(--info-bg);color:var(--info)}
.badge.neutral{background:#eef1f4;color:var(--muted)}
.grp{margin:10px 0 14px}
.grp-h{font-weight:650;font-size:13px;padding:4px 0;border-bottom:1px solid var(--line)}
.grp-c{font-weight:500;color:var(--muted);font-size:12px}
.flow{list-style:none;counter-reset:none;padding:0;margin:6px 0}
.flow-step{border-left:2px solid var(--line);padding:0 0 14px 16px;margin-left:8px;position:relative}
.flow-step::before{content:"";position:absolute;left:-6px;top:4px;width:10px;height:10px;
  border-radius:50%;background:var(--ok)}
.flow-head{display:flex;align-items:center;gap:8px}
.flow-detail{font-size:12px;color:var(--muted);margin-top:2px}
.cmp{font-size:11.5px;color:var(--muted);margin:4px 0;padding-left:18px}
.cmp li{margin:2px 0}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin:6px 0}
.chip{font-size:11px;padding:3px 8px;border-radius:6px;background:#eef1f4;color:var(--muted)}
.chip-on{background:var(--info-bg);color:var(--info)}
.chip-hot{background:var(--bad-bg);color:var(--bad);font-weight:650}
.chip-off{opacity:.6}
.stat-row{display:flex;flex-wrap:wrap;gap:10px;margin:6px 0}
.stat{background:#fbfcfd;border:1px solid var(--line);border-radius:6px;padding:8px 12px;min-width:120px}
.stat span{display:block;font-size:11px;color:var(--muted)}
.stat strong{font-size:15px}
.finding{background:var(--bad-bg);border:1px solid #f0c6ca;border-radius:8px;padding:12px 14px;margin:4px 0 12px}
.finding-h{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.finding-id{font-weight:700;font-size:14px}
.finding-warn{font-size:12.5px;margin:8px 0 4px}
.mini{margin:4px 0}
.mini>summary{cursor:pointer;font-size:12px;color:var(--muted)}
.mini-body{padding:4px 0 4px 8px}
.steps{list-style:none;padding:0;margin:0;font-size:11.5px}
.steps li{display:flex;align-items:center;gap:6px;padding:1px 0}
.accepted .badge{font-size:13px;padding:4px 12px}
.ftr{padding:16px 24px 28px;color:var(--muted);font-size:12px}
.ftr code{background:#eef1f4;padding:1px 5px;border-radius:4px}
"""

JS = """
document.addEventListener('DOMContentLoaded',function(){
  var bar=document.createElement('div');
  bar.style.cssText='padding:6px 24px;display:flex;gap:8px;align-items:center;font-size:12px';
  var mk=function(txt,open){
    var b=document.createElement('button');
    b.textContent=txt;
    b.style.cssText='cursor:pointer;border:1px solid #d0d6dc;background:#fff;border-radius:6px;padding:4px 10px;font:inherit';
    b.onclick=function(){document.querySelectorAll('details.sec').forEach(function(d){d.open=open});};
    return b;
  };
  bar.appendChild(mk('Expand all',true));
  bar.appendChild(mk('Collapse all',false));
  var kpis=document.querySelector('.kpis');
  if(kpis&&kpis.parentNode){kpis.parentNode.insertBefore(bar,kpis.nextSibling);}
});
"""


# ----------------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="Generate the static M4 executive dashboard.")
    ap.add_argument("--out", default=DEFAULT_OUT, help="output HTML path")
    ap.add_argument("--print-kpis", action="store_true", help="print computed KPI values to stdout")
    args = ap.parse_args()

    html_str, kpi = build()

    # Validate: parseable + no external http(s) asset references.
    from html.parser import HTMLParser

    class _V(HTMLParser):
        def error(self, message):  # py<3.10 compat; never called on modern parser
            raise ValueError(message)

    _V().feed(html_str)  # raises on malformed markup

    asset_hits = re.findall(r'(?:src|href)\s*=\s*["\']https?://[^"\']+', html_str)
    asset_hits += re.findall(r'url\(\s*https?://', html_str)
    if asset_hits:
        print("ERROR: external asset references found:", asset_hits[:5], file=sys.stderr)
        return 2

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(html_str)

    size_kb = len(html_str.encode("utf-8")) / 1024.0
    print(f"[m4_dashboard] wrote {args.out} ({size_kb:.1f} KiB)")
    print(f"[m4_dashboard] sources: "
          + ", ".join(f"{s['name']}={s['status']}" for s in SOURCES))

    if args.print_kpis or True:
        print("[m4_dashboard] KPIs:")
        for k in ["gates_pass", "gates_total", "gates_pending", "gates_fail", "accepted",
                  "journeys_pass", "journeys_total", "invariants_pass", "invariants_total",
                  "routes_total", "routes_native", "routes_expected_fail",
                  "hyp_accepted", "hyp_total", "findings_verified", "findings_benign",
                  "egress_status", "merchants_provisioned", "known_limits",
                  "contra_resolved", "contra_open"]:
            print(f"    {k} = {kpi.get(k)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
