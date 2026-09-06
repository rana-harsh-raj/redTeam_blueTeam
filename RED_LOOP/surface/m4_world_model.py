#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
m4_world_model.py  —  World-Model Graph Export (M4 Direct-reconciliation twin), task T21 (Stretch H).

A *generated* (not hand-drawn) typed graph of the M4 Direct twin. It PARSES committed repo
artifacts and emits three views of the same graph so re-running after a source change re-derives it:

  reports/implementation/m4-world-model.json     — canonical, deterministic, with sha256 provenance
  reports/implementation/m4-world-model.graphml  — GraphML (PAYOUTS_SERVICE_GRAPH.graphml conventions)
  reports/implementation/m4-world-model.mmd       — readable mermaid (grouped by trust boundary, fidelity-styled)

Inputs parsed (all committed):
  reports/implementation/m4-source-map.md          (routes A-I, reservation machine, DA events, workers, evidence file:line)
  reports/implementation/m4-fidelity-matrix.csv    (per-component layer/fidelity_label/evidence/reachability)
  reports/implementation/m4-route-coverage.json    (R1..R3,R-remap executed evidence)
  reports/implementation/m4-invariant-results.json (G26/G31/G51/G53/G54 executed evidence)
  reports/implementation/m4-direct-journeys.json   (journeys A..G)
  reports/implementation/m4-direct-e2e-replay.json (finding F-M4-001 = candidate H-D3, + non-findings)
  TWIN_SPEC/route-matrix.yaml                       (direct_status_transports A-I; direct_reconciliation_routes R1-R15; ingress_paths)
  TWIN_SPEC/components.yaml                         (24 architecture components)
  TWIN_SPEC/acceptance-invariants.yaml             (I01-I31)
  ENV2_COMPOSE/docker-compose.yml                  (75 services -> real/substitute/datastore inventory)
  ENV2_COMPOSE/seeds/localstack/init-queues.sh     (SQS queues + SNS topics)

Fidelity vocabulary on every node/edge:  real | substitute | fixture | expected-failure | missing
Coverage depth (where known):            executed | source_inferred | substitute | implemented | independently_reproduced | deliberately_simplified | unresolved

Determinism: nodes sorted by (type,id); edges by (type,src,dst). generated_at may be pinned via
env M4_WM_GENERATED_AT (or SOURCE_DATE_EPOCH) so the substantive graph diffs cleanly across runs.

Stdlib only (host python 3.14; no pyyaml assumed). Uses ENV2_COMPOSE/config/_miniyaml.py for the
two well-formed specs (components, acceptance-invariants); the route-matrix / source-map are parsed
with purpose-built tolerant scanners because they carry inline comments / colons miniyaml rejects.
"""
import csv
import datetime
import hashlib
import io
import json
import os
import re
import sys
import xml.dom.minidom as minidom
import xml.sax.saxutils as sax

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "ENV2_COMPOSE", "config"))
try:
    import _miniyaml as miniyaml
except Exception:  # pragma: no cover
    miniyaml = None

FIDELITIES = ("real", "substitute", "fixture", "expected-failure", "missing")

INPUTS = {
    "source_map": "reports/implementation/m4-source-map.md",
    "fidelity_matrix": "reports/implementation/m4-fidelity-matrix.csv",
    "route_coverage": "reports/implementation/m4-route-coverage.json",
    "invariant_results": "reports/implementation/m4-invariant-results.json",
    "journeys": "reports/implementation/m4-direct-journeys.json",
    "replay": "reports/implementation/m4-direct-e2e-replay.json",
    "route_matrix": "TWIN_SPEC/route-matrix.yaml",
    "components": "TWIN_SPEC/components.yaml",
    "acceptance_invariants": "TWIN_SPEC/acceptance-invariants.yaml",
    "compose": "ENV2_COMPOSE/docker-compose.yml",
    "init_queues": "ENV2_COMPOSE/seeds/localstack/init-queues.sh",
    "predecessor_graph": "reports/PAYOUTS_SERVICE_GRAPH.json",
}


def p(rel):
    return os.path.join(REPO, rel)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------------------
# Graph accumulator
# ---------------------------------------------------------------------------
class Graph:
    def __init__(self):
        self.nodes = {}       # id -> node dict
        self.edges = {}       # (src,dst,type) -> edge dict
        self.unparsed = []    # notes about inputs we could not fully parse

    def node(self, nid, ntype, label, fidelity, evidence=None, **attrs):
        assert fidelity in FIDELITIES, "bad fidelity %r for %s" % (fidelity, nid)
        if nid in self.nodes:
            n = self.nodes[nid]
            # enrich: fill missing evidence / merge attrs, do not clobber a set evidence
            if evidence and not n.get("evidence"):
                n["evidence"] = evidence
            for k, v in attrs.items():
                if v not in (None, "") and not n["attrs"].get(k):
                    n["attrs"][k] = v
            return nid
        self.nodes[nid] = {
            "id": nid,
            "type": ntype,
            "label": label,
            "attrs": {k: v for k, v in attrs.items() if v not in (None, "")},
            "fidelity": fidelity,
            "evidence": evidence or "",
        }
        return nid

    def edge(self, src, dst, etype, fidelity, evidence=None, **attrs):
        assert fidelity in FIDELITIES, "bad edge fidelity %r %s->%s" % (fidelity, src, dst)
        key = (src, dst, etype)
        if key in self.edges:
            e = self.edges[key]
            if evidence and not e.get("evidence"):
                e["evidence"] = evidence
            return key
        self.edges[key] = {
            "src": src, "dst": dst, "type": etype,
            "attrs": {k: v for k, v in attrs.items() if v not in (None, "")},
            "fidelity": fidelity, "evidence": evidence or "",
        }
        return key


# ---------------------------------------------------------------------------
# fidelity/coverage normalisation
# ---------------------------------------------------------------------------
def norm_fidelity(raw):
    """Map any source label to the 5-value graph fidelity vocabulary."""
    r = (raw or "").strip().lower()
    if r in ("real", "real_service", "executed", "independently_reproduced", "implemented", "native"):
        return "real"
    if r in ("substitute", "substitute_bank", "real-worker/substitute-bank"):
        return "substitute"
    if r in ("fixture", "deliberately_simplified"):
        return "fixture"
    if r in ("expected-failure", "expected_failure"):
        return "expected-failure"
    if r in ("missing", "unresolved"):
        return "missing"
    return None


def coverage_of(raw):
    r = (raw or "").strip().lower()
    known = {"executed", "source_inferred", "substitute", "implemented",
             "independently_reproduced", "deliberately_simplified", "unresolved", "missing", "real"}
    return r if r in known else ""


# ---------------------------------------------------------------------------
# tolerant YAML-block helpers (route-matrix.yaml)
# ---------------------------------------------------------------------------
def extract_block(text, top_key):
    """Return the raw lines of a top-level `top_key:` block (indent > 0 lines until next top-level key)."""
    lines = text.splitlines()
    out = []
    inblk = False
    for ln in lines:
        if not inblk:
            if re.match(r"^%s:\s*$" % re.escape(top_key), ln):
                inblk = True
            continue
        if ln.strip() == "":
            out.append(ln)
            continue
        if re.match(r"^[A-Za-z0-9_]+:", ln):  # next top-level key
            break
        out.append(ln)
    return out


def split_flow(s):
    """Split a `{a: x, b: [1,2], c: "y:z"}` flow-mapping into a dict, top-level commas only."""
    s = s.strip()
    if s.startswith("{") and s.endswith("}"):
        s = s[1:-1]
    parts, depth, buf, q = [], 0, [], None
    for ch in s:
        if q:
            buf.append(ch)
            if ch == q:
                q = None
            continue
        if ch in ("'", '"'):
            q = ch
            buf.append(ch)
            continue
        if ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if buf:
        parts.append("".join(buf))
    out = {}
    for part in parts:
        if ":" not in part:
            continue
        # split on first top-level colon outside quotes
        depth, q, idx = 0, None, -1
        for i, ch in enumerate(part):
            if q:
                if ch == q:
                    q = None
                continue
            if ch in ("'", '"'):
                q = ch
            elif ch in "[{":
                depth += 1
            elif ch in "]}":
                depth -= 1
            elif ch == ":" and depth == 0:
                idx = i
                break
        if idx < 0:
            continue
        k = part[:idx].strip()
        v = part[idx + 1:].strip().strip('"').strip("'")
        out[k] = v
    return out


def parse_status_transports(text):
    """direct_status_transports: A_..I_ inline flow mappings."""
    block = extract_block(text, "direct_status_transports")
    routes = {}
    for ln in block:
        m = re.match(r"^  ([A-I])_([A-Za-z0-9_]+):\s*(\{.*\})\s*$", ln)
        if not m:
            continue
        letter, name, flow = m.group(1), m.group(2), m.group(3)
        d = split_flow(flow)
        d["_name"] = name
        routes[letter] = d
    return routes


def parse_reconciliation_routes(text):
    """direct_reconciliation_routes: R1_.. blocks with 4-space `field: value` children."""
    block = extract_block(text, "direct_reconciliation_routes")
    routes = {}
    cur = None
    for ln in block:
        m = re.match(r"^  (R\d+)_([A-Za-z0-9_]+):\s*$", ln)
        if m:
            cur = m.group(1)
            routes[cur] = {"_name": m.group(2)}
            continue
        if cur is None:
            continue
        m = re.match(r"^    ([A-Za-z0-9_]+):\s*(.*)$", ln)
        if m:
            routes[cur][m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return routes


def parse_ingress(text):
    """ingress_paths: {public_api_create, dashboard_create, internal_app_create, bulk_create}."""
    block = extract_block(text, "ingress_paths")
    ingress = {}
    cur = None
    for ln in block:
        m = re.match(r"^  ([A-Za-z0-9_]+):\s*$", ln)
        if m:
            cur = m.group(1)
            ingress[cur] = {}
            continue
        if cur is None:
            continue
        m = re.match(r"^    ([A-Za-z0-9_]+):\s*(.*)$", ln)
        if m:
            ingress[cur][m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return ingress


# ---------------------------------------------------------------------------
# source-map markdown table helpers
# ---------------------------------------------------------------------------
def md_section(text, header_regex):
    """Return the lines of a markdown section starting at a header matching header_regex up to next `## `."""
    lines = text.splitlines()
    out, inblk = [], False
    for ln in lines:
        if not inblk:
            if re.match(header_regex, ln):
                inblk = True
            continue
        if re.match(r"^## ", ln):
            break
        out.append(ln)
    return out


def md_rows(section_lines):
    """Yield table rows (list of cell strings) from `| a | b |` lines, skipping the header separator."""
    for ln in section_lines:
        if not ln.strip().startswith("|"):
            continue
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if all(set(c) <= set("-: ") for c in cells):  # separator row
            continue
        yield cells


def parse_da_events(text):
    """Section 4 config table -> {event_name: {config, evidence}}."""
    sec = md_section(text, r"^## 4\. DA ledger event table")
    events = {}
    # transactor-event list paragraph
    listed = []
    joined = " ".join(sec)
    m = re.search(r"constant\.go:507-518\)[^`]*", joined)
    for ev in re.findall(r"`(da_[a-z_]+)`", joined):
        if ev not in listed:
            listed.append(ev)
    # config table rows: | Config id | Rule | Entries | file:line |
    for cells in md_rows(sec):
        if len(cells) < 4:
            continue
        if cells[0].startswith("Config id"):
            continue
        rule = cells[1]
        ev_names = re.findall(r"`?(da_[a-z_]+)`?", rule)
        evidence = "LEDGER:internal/journal/ledger_config/seed_data/direct_account_x.go" + cells[3].replace("`", "").replace("LEDGER:internal/journal/ledger_config/seed_data/direct_account_x.go", "")
        for ev in ev_names:
            events.setdefault(ev, {"config": cells[0].strip("`"), "entries": cells[2],
                                   "evidence": evidence.strip()})
    for ev in listed:
        events.setdefault(ev, {"config": "", "entries": "",
                               "evidence": "LEDGER:internal/common/constant.go:507-518"})
    return events


def parse_reservation_machine(text):
    """Section 3 table -> transitions [(from, event, guard, to, effect, function)]."""
    sec = md_section(text, r"^## 3\. Reservation state machine")
    trans = []
    for cells in md_rows(sec):
        if len(cells) < 6:
            continue
        if cells[0] == "From":
            continue
        frm, event, guard, to, effect, func = cells[:6]
        trans.append({"from": frm, "event": event, "guard": guard, "to": to,
                      "effect": effect, "function": func})
    return trans


def parse_queues(text):
    """init-queues.sh -> ({queue names}, {sns topic names})."""
    queues, topics = [], []
    for m in re.finditer(r"create-queue --queue-name ([A-Za-z0-9_-]+)", text):
        queues.append(m.group(1))
    for m in re.finditer(r"create-topic --name ([A-Za-z0-9_-]+)", text):
        topics.append(m.group(1))
    return queues, topics


def parse_compose_services(text):
    """Top-level service names under `services:`."""
    svc, inservices = [], False
    for ln in text.splitlines():
        if re.match(r"^services:", ln):
            inservices = True
            continue
        if inservices:
            if re.match(r"^[A-Za-z0-9_]+:", ln):  # left top-level block
                break
            m = re.match(r"^  ([A-Za-z0-9_-]+):\s*$", ln)
            if m:
                svc.append(m.group(1))
    return svc


def parse_fidelity_matrix(text):
    rows = []
    for row in csv.DictReader(io.StringIO(text)):
        rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# service classification
# ---------------------------------------------------------------------------
DATASTORES = {
    "mysql-payouts": ("MySQL — payouts DB", "banking_accounts / payouts / banking_account_statement(_details) / reversals / counters"),
    "mysql-fts": ("MySQL — FTS DB", "transfers / attempts / source_accounts / direct_account_routing_rules / transfer_meta"),
    "mysql-xbalances": ("MySQL — x-balances DB", "balance (id==monolith balance id; account_type direct|shared|sub_balance)"),
    "mysql-apidb-stub": ("MySQL — monolith apidb stub", "balance.account_type=direct provisioner seed"),
    "postgres-ledger": ("Postgres — ledger DB", "accounts / account_details(entities) / journal / ledger_config / idempotency"),
    "mongo-cfa": ("MongoDB — CFA", "contacts / fund accounts"),
    "redis": ("Redis", "reservations in_flight / partner_bank_health / pricing-rule / idempotency mutex"),
    "localstack": ("LocalStack", "SQS queues + SNS topics"),
    "kafka": ("Kafka broker", "rx-fts-status-update-events topic transport"),
}
SUBSTITUTE_SVCS = {
    "kong-lite", "ledger-gate", "monolith-stub", "dcs-stub", "splitz-stub", "shield-stub",
    "pricing-stub", "asv-stub", "bankingaccounts-stub", "stork-capture", "merchant-webhook-sink",
    "xas-sim", "mozart-mock", "mozart-sim", "workflow-sim", "cron-driver", "verifier",
}
# real services -> evidence (from fidelity matrix where available, else compose)
REAL_SVC_EVIDENCE = {
    "payouts-api": "reports/implementation/m4-direct-provision.json (payouts-api real PS v1-candidate)",
    "fts-web": "reports/implementation/m4-direct-provision.json (fts-web real FTS v1-candidate)",
    "ledger-api": "reports/implementation/m4-xas-ledger.json:checks (ledger real v1-candidate)",
    "xbalances-server": "reports/implementation/m4-direct-provision.json (x-balances real v1-candidate)",
    "cfa-server": "reports/implementation/m4-baseline-report.md (cfa real v1-candidate)",
}


def classify_service(name):
    if name in DATASTORES:
        return "datastore"
    if name.endswith("-migrate"):
        return "migrate"
    if name in SUBSTITUTE_SVCS or name.endswith("-stub") or name.endswith("-sim") or name.endswith("-mock"):
        return "substitute"
    if name.endswith("-worker") or "-worker-" in name or "-kafka-" in name or name.endswith("-scheduler"):
        return "worker"
    return "service"


# ---------------------------------------------------------------------------
# build the graph
# ---------------------------------------------------------------------------
def build():
    g = Graph()
    texts = {k: read(p(v)) for k, v in INPUTS.items()}

    # ---- 1. trust boundaries + identities -------------------------------
    g.node("boundary:internet-edge", "trust_boundary", "Internet edge (kong-lite hardened)", "substitute",
           evidence="TWIN_SPEC/route-matrix.yaml ingress_paths; ENV2_COMPOSE/substitutes/kong-lite/route_policy.py",
           zone="edge")
    g.node("boundary:internal-mesh", "trust_boundary", "Internal service mesh (payouts + real services)", "real",
           evidence="reports/implementation/m4-source-map.md #6 workers", zone="internal")
    g.node("boundary:bank", "trust_boundary", "Bank boundary (Mozart / RBL)", "substitute",
           evidence="ENV2_COMPOSE/substitutes/mozart-sim/server.py:53-60", zone="external")
    g.node("id:merchant-passport", "identity", "Merchant passport (consumer.id wins over body/header)", "real",
           evidence="PS:internal/auth/passport.go:173-207; PS:internal/app/payouts/core.go:411-418",
           coverage="executed")
    g.node("id:service-creds", "identity", "Service credentials ([fts.auth]/[payouts_service.auth]/cred.API)", "real",
           evidence="PS:internal/auth/authHelper.go:13-25; TWIN:route-matrix.yaml direct_status_transports")
    g.node("id:kong-edge", "identity", "kong-lite edge (mints passport at hardened edge)", "substitute",
           evidence="ENV2_COMPOSE/substitutes/kong-lite/route_policy.py:39,70")
    g.node("id:broker", "identity", "Attacker Broker (fixed-identity injection through kong-lite)", "substitute",
           evidence="reports/implementation/m4-direct-e2e-replay.json (H-D3 independent_reproduction)")
    g.edge("id:kong-edge", "id:merchant-passport", "mints_identity_for", "substitute",
           evidence="ENV2_COMPOSE/substitutes/kong-lite/route_policy.py:39,70")
    g.edge("id:kong-edge", "boundary:internet-edge", "enforces", "substitute")
    g.edge("id:broker", "id:kong-edge", "calls", "substitute",
           evidence="reports/implementation/m4-direct-e2e-replay.json (H-D3 broker path)")

    # ---- 2. compose services / datastores / workers ---------------------
    services = parse_compose_services(texts["compose"])
    for name in services:
        kind = classify_service(name)
        if kind == "migrate":
            continue  # one-shot migration jobs are not graph participants
        comp_ev = "ENV2_COMPOSE/docker-compose.yml (service: %s)" % name
        if kind == "datastore":
            label, note = DATASTORES.get(name, (name, ""))
            g.node("ds:" + name, "datastore", label, "real",
                   evidence=comp_ev, contents=note, coverage="executed")
            g.edge("ds:" + name, "boundary:internal-mesh", "uses", "real", evidence=comp_ev)
        elif kind == "substitute":
            g.node("svc:" + name, "substitute", name, "substitute",
                   evidence=comp_ev, role="twin substitute (D-006)")
            b = "boundary:internet-edge" if name in ("kong-lite", "ledger-gate") else (
                "boundary:bank" if name in ("mozart-sim", "mozart-mock") else "boundary:internal-mesh")
            g.edge("svc:" + name, b, "uses", "substitute", evidence=comp_ev)
        elif kind == "worker":
            g.node("svc:" + name, "worker", name, "real", evidence=comp_ev, coverage="implemented")
            g.edge("svc:" + name, "boundary:internal-mesh", "uses", "real", evidence=comp_ev)
        else:  # real service
            g.node("svc:" + name, "service", name, "real",
                   evidence=REAL_SVC_EVIDENCE.get(name, comp_ev),
                   coverage="executed" if name in REAL_SVC_EVIDENCE else "implemented")
            g.edge("svc:" + name, "boundary:internal-mesh", "uses", "real", evidence=comp_ev)

    # service -> datastore money-path edges (evidence from source-map table §5)
    for src, dst, ev in [
        ("svc:payouts-api", "ds:mysql-payouts", "PS:internal/database/migrations (banking_accounts/payouts/bas)"),
        ("svc:fts-web", "ds:mysql-fts", "FTS:internal/migrations/00005,00007,00020"),
        ("svc:xbalances-server", "ds:mysql-xbalances", "XB:internal/database/migrations/20250127224555_balance.go:17-48"),
        ("svc:ledger-api", "ds:postgres-ledger", "LEDGER:internal/journal/... journal/accounts/ledger_config"),
        ("svc:cfa-server", "ds:mongo-cfa", "ENV2_COMPOSE/docker-compose.yml (cfa-server -> mongo-cfa)"),
        ("svc:payouts-api", "ds:redis", "PS:internal/app/payouts/reservation/store.go:32-39 (in_flight)"),
    ]:
        if src in g.nodes and dst in g.nodes:
            g.edge(src, dst, "writes_to", "real", evidence=ev)

    # ---- 3. queues + topics ---------------------------------------------
    queues, topics = parse_queues(texts["init_queues"])
    M4_QUEUES = {
        "rbl_banking_account_statement": ("SQS rbl_banking_account_statement", "real",
            "reports/implementation/m4-source-map.md #6; init-queues.sh"),
        "x_account_statement_source_event": ("SQS x_account_statement_source_event", "real",
            "PS:internal/job/x_account_statement_source_event.go:21-56; route-matrix R5"),
        "journal_create": ("SQS journal_create (= prod-ledger-journal-create-live)", "real",
            "reports/implementation/m4-source-map.md #4; route-matrix R12"),
        "stage-x-balances-balance-refresh-live": ("SQS x-balances-balance-refresh (reservation release trigger A)",
            "expected-failure", "route-matrix R2/R3; TWIN_SPEC/expected-failures.yaml EF-007"),
    }
    for q in queues:
        if q in M4_QUEUES:
            label, fid, ev = M4_QUEUES[q]
            g.node("q:" + q, "queue", label, fid, evidence=ev)
            g.edge("q:" + q, "ds:localstack", "uses", "real",
                   evidence="ENV2_COMPOSE/seeds/localstack/init-queues.sh (create-queue %s)" % q)
    for t in topics:
        g.node("topic:" + t, "topic", "SNS %s" % t, "real",
               evidence="ENV2_COMPOSE/seeds/localstack/init-queues.sh (create-topic %s)" % t)
    # canonical named SNS + Kafka topics from the source map
    g.node("topic:api-ledger-journal-create-live", "topic",
           "SNS api-ledger-journal-create-live", "real",
           evidence="reports/implementation/m4-source-map.md #4 (SNS -> SQS journal_create)")
    g.node("topic:rx-fts-status-update-events", "topic",
           "Kafka rx-fts-status-update-events (FTS status transport)", "expected-failure",
           evidence="route-matrix.yaml direct_status_transports F; TWIN_SPEC/expected-failures.yaml EF-002/003/006")
    # SNS -> SQS fan and worker consume edges
    g.edge("topic:api-ledger-journal-create-live", "q:journal_create", "delivers_to", "real",
           evidence="reports/implementation/m4-source-map.md #4")
    if "svc:ledger-worker-journal-create" in g.nodes:
        g.edge("svc:ledger-worker-journal-create", "q:journal_create", "consumes", "real",
               evidence="route-matrix.yaml R12 (job JournalCreate)")
    if "svc:payouts-worker-rbl-banking-account-statement" in g.nodes:
        g.edge("svc:payouts-worker-rbl-banking-account-statement", "q:rbl_banking_account_statement",
               "consumes", "real", evidence="PS:internal/job/rbl_banking_account_statement.go:16-24")
    if "svc:payouts-api" in g.nodes and "q:x_account_statement_source_event" in g.nodes:
        g.edge("svc:payouts-api", "q:x_account_statement_source_event", "publishes", "real",
               evidence="PS:internal/job/x_account_statement_source_event.go:21-56 (Direct terminal)")
    if "svc:payouts-kafka-fts-status-updates-consumer" in g.nodes:
        g.edge("svc:payouts-kafka-fts-status-updates-consumer", "topic:rx-fts-status-update-events",
               "consumes", "expected-failure",
               evidence="PS:internal/taskHandlers/fts_status_updates.go:37-80 (FAILED/REVERSED dropped)")

    # ---- 4. ingress endpoints + free_payout finding route ---------------
    ingress = parse_ingress(texts["route_matrix"])
    ING_FID = {"public_api_create": "real", "dashboard_create": "substitute",
               "internal_app_create": "real", "bulk_create": "real"}
    for name, d in ingress.items():
        route = d.get("route") or d.get("path") or name
        ep_ev = "TWIN_SPEC/route-matrix.yaml ingress_paths.%s" % name
        g.node("ep:" + name, "endpoint", route, ING_FID.get(name, "real"), evidence=ep_ev, ingress=name)
        g.edge("boundary:internet-edge", "ep:" + name, "routes_to", ING_FID.get(name, "real"), evidence=ep_ev)
        g.edge("ep:" + name, "svc:payouts-api", "calls", ING_FID.get(name, "real"), evidence=ep_ev)
    # the finding route
    fp_ev = "PS:internal/app/freePayout/core.go:678-684 (no merchant scoping); public allowlist (T06)"
    g.node("ep:free_payout", "endpoint", "GET /v1/payouts/free_payout/{balance_id}", "real",
           evidence=fp_ev, scoping="none (H-D3 / F-M4-001)")
    g.edge("boundary:internet-edge", "ep:free_payout", "routes_to", "real", evidence=fp_ev)
    g.edge("ep:free_payout", "svc:payouts-api", "calls", "real",
           evidence="PS:internal/app/freePayout/core.go:678-684")
    g.node("sym:GetFreePayoutAttributes", "source_symbol",
           "GetFreePayoutAttributes(ctx, balanceId) — resolves banking account by balance_id alone", "real",
           evidence="PS:internal/app/freePayout/core.go:678-684")
    g.edge("ep:free_payout", "sym:GetFreePayoutAttributes", "implements", "real",
           evidence="PS:internal/app/freePayout/core.go:678-684")

    # ---- 5. status transports A-I ---------------------------------------
    transports = parse_status_transports(texts["route_matrix"])
    SRC_DST_SVC = {  # map source-map service phrases to compose service node ids
        "payouts-worker-fts-async-processing": "svc:payouts-worker-fts-async-processing",
        "fts-web": "svc:fts-web", "payouts-api": "svc:payouts-api",
        "monolith-stub": "svc:monolith-stub", "mozart-sim": "svc:mozart-sim",
        "kafka": "topic:rx-fts-status-update-events",
        "payouts-kafka-fts-status-updates-consumer": "svc:payouts-kafka-fts-status-updates-consumer",
        "fts-worker-fire-transfer-status-webhook": "svc:fts-worker-fire-transfer-status-webhook",
        "verifier": "svc:verifier", "xas-sim": "svc:xas-sim",
    }

    def resolve_svc(token):
        token = token.strip()
        for key, nid in SRC_DST_SVC.items():
            if key in token:
                return nid
        return None

    for letter, d in sorted(transports.items()):
        fid = norm_fidelity(d.get("fidelity")) or "substitute"
        rid = "route:%s" % letter
        g.node(rid, "route", "%s — %s" % (letter, d.get("route", d.get("_name", ""))), fid,
               evidence="TWIN_SPEC/route-matrix.yaml direct_status_transports %s_%s" % (letter, d.get("_name", "")),
               transport=d.get("transport", ""), status_map=d.get("status_map", ""),
               twin=d.get("twin", ""), correlation=d.get("correlation", ""),
               expected_failures=d.get("expected_failures", ""))
        # chain edge from src_dst
        sd = d.get("src_dst", "")
        if "->" in sd:
            hops = [h.strip() for h in sd.split("->")]
            for a, b in zip(hops, hops[1:]):
                na, nb = resolve_svc(a), resolve_svc(b)
                if na and nb and na in g.nodes and nb in g.nodes:
                    g.edge(na, nb, "status_transport", fid, evidence=rid.replace("route:", "route "),
                           route=letter)

    # ---- 6. reconciliation routes R1-R15 --------------------------------
    recon = parse_reconciliation_routes(texts["route_matrix"])
    for rid, d in sorted(recon.items(), key=lambda kv: int(kv[0][1:])):
        fid = norm_fidelity(d.get("fidelity")) or "substitute"
        nid = "route:%s" % rid
        g.node(nid, "route", "%s — %s" % (rid, d.get("route", d.get("effect", d.get("_name", "")))[:90]), fid,
               evidence=d.get("source", "TWIN_SPEC/route-matrix.yaml direct_reconciliation_routes %s" % rid),
               twin=d.get("twin", ""), gates=d.get("gates", ""), effect=d.get("effect", ""),
               name=d.get("_name", ""))

    # accounting money-path chain (task-required), evidence from source map §1.5 + route-matrix
    accounting_chain = [
        ("route:R5", "svc:xas-sim", "PS:internal/job/x_account_statement_source_event.go:21-56"),
        ("svc:xas-sim", "route:R6", "route-matrix.yaml R6 (xas-sim matcher)"),
        ("route:R6", "route:R7", "XAS:internal/gateway/api/service/service.go:34-35,108-127"),
        ("route:R7", "svc:monolith-stub", "route-matrix.yaml R7 (relay server.py:743-764)"),
        ("svc:monolith-stub", "route:R8", "PS:internal/app/payouts/core.go:7203-7395 (UpdatePayoutAfterBASRecon)"),
        ("route:R8", "route:R12", "route-matrix.yaml R12 (monolith-stub DA emitter)"),
        ("route:R12", "svc:ledger-api", "reports/implementation/m4-source-map.md #4"),
    ]
    for a, b, ev in accounting_chain:
        if a in g.nodes and b in g.nodes:
            g.edge(a, b, "accounting_path", "substitute", evidence=ev)
    # ART / real linking route R9 -> R8
    if "route:R9" in g.nodes and "route:R8" in g.nodes:
        g.edge("route:R9", "route:R8", "accounting_path", "real",
               evidence="PS:internal/app/bankingAccountStatement/core.go:627-663 (SendUpdatesToPayoutSource)")
    # statement ingestion chain R1->R3->R4
    for a, b, ev in [("route:R1", "route:R3", "PS:internal/job/rbl_banking_account_statement.go:33-40"),
                     ("route:R3", "route:R4", "PS:internal/app/bankingAccountStatement/processor/base.go:55-97")]:
        if a in g.nodes and b in g.nodes:
            g.edge(a, b, "accounting_path", "substitute" if a == "route:R3" else "real", evidence=ev)

    # ---- 7. DA ledger events --------------------------------------------
    da_events = parse_da_events(texts["source_map"])
    DIRECT_PATH_EVENTS = {"da_payout_processed", "da_payout_processed_recon", "da_payout_reversed",
                          "da_payout_reversed_recon", "da_ext_debit", "da_ext_credit"}
    for ev_name, meta in sorted(da_events.items()):
        on_direct = ev_name in DIRECT_PATH_EVENTS
        g.node("da:" + ev_name, "da_event", ev_name, "substitute" if on_direct else "missing",
               evidence=meta.get("evidence") or "LEDGER:internal/common/constant.go:507-518",
               config=meta.get("config", ""), entries=meta.get("entries", ""),
               on_direct_path=str(on_direct))
    # ledger emits the two success journals
    for ev_name in ("da_payout_processed", "da_payout_processed_recon"):
        if "da:" + ev_name in g.nodes:
            g.edge("svc:ledger-api", "da:" + ev_name, "posts_journal", "substitute",
                   evidence="reports/implementation/m4-invariant-results.json G54 (two balanced DA journals)")

    # ---- 8. reservation state machine -----------------------------------
    RES_STATES = {
        "live": ("Reservation live (counter held)", "real"),
        "awaiting_balance_refresh": ("Reservation awaiting_balance_refresh", "real"),
        "released": ("Reservation released (HDEL+DECRBY)", "real"),
    }
    for sid, (label, fid) in RES_STATES.items():
        g.node("state:reservation:" + sid, "state", label, fid,
               evidence="reports/implementation/m4-source-map.md #3 reservation state machine",
               machine="inflight_reservation")
    for trn in parse_reservation_machine(texts["source_map"]):
        frm, to = trn["from"], trn["to"]
        src = None
        if "live" in frm and "awaiting" not in frm:
            src = "state:reservation:live"
        elif "awaiting" in frm:
            src = "state:reservation:awaiting_balance_refresh"
        dst = None
        if to == "live":
            dst = "state:reservation:live"
        elif "awaiting" in to:
            dst = "state:reservation:awaiting_balance_refresh"
        elif "(none)" in to or "expired" in to:
            dst = "state:reservation:released"
        if src and dst and src != dst:
            g.edge(src, dst, "transition", "real",
                   evidence="reports/implementation/m4-source-map.md #3 (%s)" % trn["function"][:60],
                   trigger=trn["event"], guard=trn["guard"][:80])
    # canonical happy-path transitions the invariants assert
    g.edge("state:reservation:live", "state:reservation:awaiting_balance_refresh", "transition", "real",
           evidence="PS:internal/app/payouts/reservation_hook.go:83-86 (OnEvent processed)", trigger="OnEvent(processed)")
    g.edge("state:reservation:awaiting_balance_refresh", "state:reservation:released", "transition", "real",
           evidence="PS:internal/app/payouts/reservation_hook.go:349-394 (BalanceRefreshEvent)", trigger="BalanceRefreshEvent/reconciler")

    # payout / fts / bas state summaries
    for sid, label, ev in [
        ("payout:created", "payout.created", "PS:internal/app/payouts/reservation_hook.go:42-48"),
        ("payout:processed", "payout.processed (Direct, no reversal)", "PS:internal/app/payouts/core.go:3866-3868"),
        ("payout:failed", "payout.failed (Direct: no reversal row)", "PS:internal/app/common/appConstants/states.go:84"),
        ("payout:reversed", "payout.reversed (credit-after-debit upgrade)", "PS:internal/app/payouts/core.go:7289-7318"),
    ]:
        g.node("state:" + sid, "state", label, "real", evidence=ev, machine="payout")

    # ---- 9. invariants (G-gates executed + acceptance I01-I31) ----------
    inv_res = json.loads(texts["invariant_results"])
    for inv in inv_res.get("invariants", []):
        name = inv["invariant"]
        m = re.search(r"\((G\d+[a-z-]*)\)", name)
        gid = m.group(1) if m else name
        g.node("inv:" + gid, "invariant", name, norm_fidelity(inv.get("fidelity")) or "real",
               evidence="reports/implementation/m4-invariant-results.json (%s: %s)" % (gid, inv.get("result")),
               statement=inv.get("formal_statement", ""), result=inv.get("result", ""),
               coverage="executed", negative_control=inv.get("negative_control", "")[:120])
    acc = miniyaml.load_file(p(INPUTS["acceptance_invariants"])) if miniyaml else {"invariants": []}
    for inv in acc.get("invariants", []):
        iid = inv.get("id")
        if not iid:
            continue
        g.node("inv:" + iid, "invariant", inv.get("name", iid), "real",
               evidence=inv.get("source", "TWIN_SPEC/acceptance-invariants.yaml %s" % iid),
               prod_enforced=inv.get("prod_enforced", ""), verifier=inv.get("verifier", ""),
               coverage="source_inferred")

    # invariant -> journey / route evidence edges (from invariant_results setup/action)
    INV_TARGET = {
        "G31": ["journey:B", "journey:D", "route:R-remap"],
        "G54": ["journey:A", "da:da_payout_processed", "da:da_payout_processed_recon"],
        "G53": ["journey:A", "state:reservation:awaiting_balance_refresh"],
        "G28-adj": ["journey:F", "route:R4"],
        "G51": ["journey:A", "svc:ledger-api"],
        "G26": ["journey:A", "route:R6", "route:R9"],
    }
    for gid, targets in INV_TARGET.items():
        src = "inv:" + gid
        if src not in g.nodes:
            continue
        for t in targets:
            if t in g.nodes:
                g.edge(src, t, "asserts", g.nodes[t]["fidelity"] if g.nodes[t]["fidelity"] in FIDELITIES else "real",
                       evidence=g.nodes[src]["evidence"])

    # ---- 10. journeys ---------------------------------------------------
    journeys = json.loads(texts["journeys"])
    for j in journeys.get("journeys", []):
        jid = "journey:" + j["journey"]
        g.node(jid, "journey", "Journey %s — %s" % (j["journey"], j["title"]),
               norm_fidelity(j.get("fidelity", "").split()[0]) or "real",
               evidence="reports/implementation/m4-direct-journeys.json (%s: %s)" % (j["journey"], j["result"]),
               result=j.get("result", ""), merchant_id=j.get("merchant_id", ""),
               fidelity_note=j.get("fidelity", ""), coverage="executed")
        g.edge(jid, "svc:payouts-api", "exercises", "real", evidence=g.nodes[jid]["evidence"])

    # route-coverage executed evidence -> enrich R1..R3 + remap
    rc = json.loads(texts["route_coverage"])
    RC_MAP = {"R1": "route:D", "R2": "route:E", "R3": "route:F", "R-remap": "route:R-remap"}
    g.node("route:R-remap", "route", "R-remap — terminal status remap by account_type/channel", "real",
           evidence="reports/implementation/m4-route-coverage.json (R-remap); PS:internal/app/payouts/fts_transfer_status_webhook.go:1300-1339",
           coverage="executed")
    for r in rc.get("routes", []):
        rcid = r["route_id"]
        target = RC_MAP.get(rcid)
        if target and target in g.nodes:
            g.nodes[target]["attrs"]["coverage"] = "executed"
            g.nodes[target]["attrs"].setdefault("route_coverage_evidence",
                                                "reports/implementation/m4-route-coverage.json (%s)" % rcid)

    # ---- 11. expected failures ------------------------------------------
    EF = {
        "EF-001": "Shared PROCESSED over Kafka cannot enqueue the processed Ledger journal",
        "EF-002": "FTS FAILED over Kafka acknowledged and dropped",
        "EF-003": "FTS REVERSED over Kafka acknowledged and dropped",
        "EF-004": "PS-recon merchants receive no DA Ledger journal on a matched debit statement",
        "EF-005": "PS source-event field name mismatch vs XAS (event_created_timestamp vs event_create_timestamp)",
        "EF-006": "FTS FAILED/REVERSED for a Direct payout over Kafka dropped (root cause is the consumer)",
        "EF-007": "x-balances BalanceRefreshEvent (reservation release trigger A) unreachable without injection",
        "EF-008": "FTS partner-bank health notification to PS (route H) never emitted in the twin",
    }
    for efid, title in EF.items():
        g.node("ef:" + efid, "expected_failure", "%s — %s" % (efid, title), "expected-failure",
               evidence="TWIN_SPEC/expected-failures.yaml (%s)" % efid)
    for efid, targets in {"EF-002": ["route:F", "topic:rx-fts-status-update-events"],
                          "EF-003": ["route:F"], "EF-004": ["route:R12"],
                          "EF-005": ["route:R5", "svc:xas-sim"], "EF-006": ["route:F"],
                          "EF-007": ["q:stage-x-balances-balance-refresh-live", "state:reservation:awaiting_balance_refresh"],
                          "EF-008": ["route:H"]}.items():
        for t in targets:
            if t in g.nodes:
                g.edge("ef:" + efid, t, "predicts_failure", "expected-failure",
                       evidence=g.nodes["ef:" + efid]["evidence"])

    # ---- 12. finding F-M4-001 + non-findings ----------------------------
    replay = json.loads(texts["replay"])
    FIND_MAP = {"H-D3": ("F-M4-001", "Cross-tenant free-payout attribute disclosure (IDOR)")}
    for cand in replay.get("candidates", []):
        cid = cand["id"]
        cls = cand.get("classification", "")
        verified = cls == "VERIFIED_UNAUTHORIZED_EFFECT"
        if cid in FIND_MAP:
            fid_id, title = FIND_MAP[cid]
        else:
            fid_id, title = cid, cand.get("claim", "")[:80]
        g.node("finding:" + fid_id, "finding", "%s — %s" % (fid_id, title), "real",
               evidence="reports/implementation/m4-findings.md; m4-direct-e2e-replay.json (candidate %s)" % cid,
               classification=cls, verified=str(verified),
               claim=cand.get("claim", "")[:200], coverage="independently_reproduced" if verified else "executed")
    # F-M4-001 -> route -> source symbol
    if "finding:F-M4-001" in g.nodes:
        g.edge("finding:F-M4-001", "ep:free_payout", "targets_route", "real",
               evidence="reports/implementation/m4-findings.md (Route: GET /v1/payouts/free_payout/{balance_id})")
        g.edge("finding:F-M4-001", "sym:GetFreePayoutAttributes", "root_cause", "real",
               evidence="PS:internal/app/freePayout/core.go:678-684")
        g.edge("finding:F-M4-001", "boundary:internet-edge", "crosses", "substitute",
               evidence="reports/implementation/m4-findings.md (kong-lite mints passport; no ownership check)")
    if "finding:F-T10-1" in g.nodes:
        for t, ev in [("route:D", "fts_transfer_status_webhook.go:654-710 (guard present)"),
                      ("route:E", "core.go:1465-1585 HandlePayoutStatusUpdateViaFTS (no guard)")]:
            if t in g.nodes:
                g.edge("finding:F-T10-1", t, "targets_route", g.nodes[t]["fidelity"], evidence=ev)

    # ---- 13. fidelity-matrix component enrichment / leftover nodes ------
    fm = parse_fidelity_matrix(texts["fidelity_matrix"])
    for row in fm:
        comp = row["component"].strip()
        layer = row["layer"].strip()
        label = row["fidelity_label"].strip()
        ev = row["evidence_pointer"].strip()
        reach = row["production_reachability"].strip()
        dev = row["deviation_ids"].strip()
        fid = {"real_service": "real", "substitute": "substitute", "fixture": "fixture"}.get(layer)
        if fid is None:
            fid = norm_fidelity(layer) or "substitute"
        # try to attach to an existing route node (Route X ...)
        mrt = re.match(r"Route ([A-I]) ", comp)
        target = None
        if mrt and ("route:%s" % mrt.group(1)) in g.nodes:
            target = "route:%s" % mrt.group(1)
        if target:
            n = g.nodes[target]
            n["attrs"].setdefault("coverage", coverage_of(label))
            n["attrs"].setdefault("production_reachability", reach)
            if dev:
                n["attrs"].setdefault("deviation_ids", dev)
            continue
        # else create a component node (services already exist by compose name -> skip dupes)
        cid = "comp:" + re.sub(r"[^a-z0-9]+", "-", comp.lower()).strip("-")[:60]
        if cid in g.nodes:
            continue
        g.node(cid, "component", comp, fid, evidence=ev,
               coverage=coverage_of(label), production_reachability=reach, deviation_ids=dev)

    # ---- 14. architecture components (components.yaml) ------------------
    comps = miniyaml.load_file(p(INPUTS["components"])) if miniyaml else {"components": {}}
    for key, c in comps.get("components", {}).items():
        today = (c.get("today", "") or "")
        fid = "real" if today.strip().upper().startswith("REAL") else (
            "missing" if today.strip().upper().startswith("MISSING") else "substitute")
        g.node("arch:" + key, "arch_component", key, fid,
               evidence=c.get("evidence", "TWIN_SPEC/components.yaml components.%s" % key),
               repo=c.get("repo", ""), role=(c.get("role", "") or "")[:120],
               today=today[:80], target=(c.get("target", "") or "")[:80])

    return g, texts


# ---------------------------------------------------------------------------
# emitters
# ---------------------------------------------------------------------------
def stats(g):
    by_type, by_fid = {}, {}
    for n in g.nodes.values():
        by_type[n["type"]] = by_type.get(n["type"], 0) + 1
        by_fid[n["fidelity"]] = by_fid.get(n["fidelity"], 0) + 1
    e_type, e_fid = {}, {}
    for e in g.edges.values():
        e_type[e["type"]] = e_type.get(e["type"], 0) + 1
        e_fid[e["fidelity"]] = e_fid.get(e["fidelity"], 0) + 1
    return {
        "nodes_total": len(g.nodes), "edges_total": len(g.edges),
        "nodes_by_type": dict(sorted(by_type.items())),
        "nodes_by_fidelity": dict(sorted(by_fid.items())),
        "edges_by_type": dict(sorted(e_type.items())),
        "edges_by_fidelity": dict(sorted(e_fid.items())),
    }


def sorted_nodes(g):
    return sorted(g.nodes.values(), key=lambda n: (n["type"], n["id"]))


def sorted_edges(g):
    return sorted(g.edges.values(), key=lambda e: (e["type"], e["src"], e["dst"]))


def emit_json(g, provenance, generated_at):
    return {
        "version": "m4-world-model.v1",
        "generated_at": generated_at,
        "generated_from": provenance,
        "milestone": "M4-direct-reconciliation",
        "task": "T21 (Stretch H) World-Model Graph Export",
        "fidelity_vocabulary": list(FIDELITIES),
        "coverage_vocabulary": ["executed", "source_inferred", "substitute", "implemented",
                                "independently_reproduced", "deliberately_simplified", "unresolved"],
        "nodes": [
            {"id": n["id"], "type": n["type"], "label": n["label"], "attrs": n["attrs"],
             "fidelity": n["fidelity"], "evidence": n["evidence"]}
            for n in sorted_nodes(g)
        ],
        "edges": [
            {"src": e["src"], "dst": e["dst"], "type": e["type"], "attrs": e["attrs"],
             "fidelity": e["fidelity"], "evidence": e["evidence"]}
            for e in sorted_edges(g)
        ],
        "stats": stats(g),
    }


def emit_graphml(g):
    N_KEYS = [("n_type", "type"), ("n_label", "label"), ("n_fidelity", "fidelity"), ("n_evidence", "evidence")]
    E_KEYS = [("e_type", "type"), ("e_fidelity", "fidelity"), ("e_evidence", "evidence")]
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<graphml xmlns="http://graphml.graphdrawing.org/xmlns">']
    for kid, attr in N_KEYS:
        out.append('<key id="%s" for="node" attr.name="%s" attr.type="string"/>' % (kid, attr))
    out.append('<key id="n_attrs" for="node" attr.name="attrs" attr.type="string"/>')
    for kid, attr in E_KEYS:
        out.append('<key id="%s" for="edge" attr.name="%s" attr.type="string"/>' % (kid, attr))
    out.append('<key id="e_attrs" for="edge" attr.name="attrs" attr.type="string"/>')
    out.append('<graph id="m4-world-model" edgedefault="directed">')
    for n in sorted_nodes(g):
        cells = "".join(
            '<data key="%s">%s</data>' % (kid, sax.escape(str(n[attr]))) for kid, attr in N_KEYS)
        cells += '<data key="n_attrs">%s</data>' % sax.escape(json.dumps(n["attrs"], sort_keys=True))
        out.append('<node id="%s">%s</node>' % (sax.quoteattr(n["id"])[1:-1], cells))
    for i, e in enumerate(sorted_edges(g)):
        cells = "".join(
            '<data key="%s">%s</data>' % (kid, sax.escape(str(e[attr]))) for kid, attr in E_KEYS)
        cells += '<data key="e_attrs">%s</data>' % sax.escape(json.dumps(e["attrs"], sort_keys=True))
        out.append('<edge id="e%d" source="%s" target="%s">%s</edge>' % (
            i, sax.quoteattr(e["src"])[1:-1], sax.quoteattr(e["dst"])[1:-1], cells))
    out.append("</graph>")
    out.append("</graphml>")
    return "\n".join(out)


MMD_CLASS = {
    "real": "classDef real fill:#e6f4ea,stroke:#137333,color:#0b3d1e;",
    "substitute": "classDef substitute fill:#fef7e0,stroke:#b06000,color:#5c3200;",
    "fixture": "classDef fixture fill:#f1f3f4,stroke:#5f6368,color:#202124;",
    "expected-failure": "classDef expectedfailure fill:#fce8e6,stroke:#c5221f,color:#5c0f0d;",
    "missing": "classDef missing fill:#f3e8fd,stroke:#7b1fa2,color:#3d0a52,stroke-dasharray:4 3;",
}
MMD_FID_CLASS = {"real": "real", "substitute": "substitute", "fixture": "fixture",
                 "expected-failure": "expectedfailure", "missing": "missing"}


def mmd_id(nid):
    return re.sub(r"[^A-Za-z0-9]", "_", nid)


def mmd_label(n):
    lbl = n["label"]
    lbl = lbl.replace('"', "'").replace("[", "(").replace("]", ")").replace("{", "(").replace("}", ")")
    lbl = re.sub(r"\s+", " ", lbl).strip()
    if len(lbl) > 58:
        lbl = lbl[:55] + "..."
    return lbl


# group nodes into readable subgraphs by trust boundary / layer
MMD_GROUPS = [
    ("Edge & identity", lambda n: n["type"] in ("trust_boundary", "identity", "endpoint")),
    ("Real services & workers", lambda n: n["type"] in ("service", "worker")),
    ("Substitutes", lambda n: n["type"] in ("substitute", "component", "arch_component")),
    ("Datastores / queues / topics", lambda n: n["type"] in ("datastore", "queue", "topic")),
    ("Routes (status transports + reconciliation)", lambda n: n["type"] in ("route", "source_symbol")),
    ("State machines & DA events", lambda n: n["type"] in ("state", "da_event")),
    ("Invariants / findings / expected-failures", lambda n: n["type"] in ("invariant", "finding", "expected_failure", "journey")),
]


def emit_mermaid(g):
    nodes = sorted_nodes(g)
    edges = sorted_edges(g)
    lines = ["%%{init: {'flowchart': {'htmlLabels': false, 'curve': 'basis'}}}%%", "flowchart LR"]
    assigned = set()
    for gi, (title, pred) in enumerate(MMD_GROUPS):
        members = [n for n in nodes if n["id"] not in assigned and pred(n)]
        if not members:
            continue
        lines.append('  subgraph g%d["%s"]' % (gi, title))
        for n in members:
            assigned.add(n["id"])
            lines.append('    %s["%s"]' % (mmd_id(n["id"]), mmd_label(n)))
        lines.append("  end")
    # any leftovers
    for n in nodes:
        if n["id"] not in assigned:
            assigned.add(n["id"])
            lines.append('  %s["%s"]' % (mmd_id(n["id"]), mmd_label(n)))
    for e in edges:
        if e["src"] not in g.nodes or e["dst"] not in g.nodes:
            continue
        lbl = e["type"].replace("_", " ")
        lines.append("  %s -->|%s| %s" % (mmd_id(e["src"]), lbl, mmd_id(e["dst"])))
    lines.append("")
    for fid, cd in MMD_CLASS.items():
        lines.append("  " + cd)
    # assign classes
    buckets = {}
    for n in nodes:
        buckets.setdefault(MMD_FID_CLASS[n["fidelity"]], []).append(mmd_id(n["id"]))
    for cls, ids in sorted(buckets.items()):
        for i in range(0, len(ids), 40):
            lines.append("  class %s %s;" % (",".join(ids[i:i + 40]), cls))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# lint / validation
# ---------------------------------------------------------------------------
def lint_mermaid(text):
    errors = []
    if "flowchart" not in text and "graph " not in text:
        errors.append("no flowchart/graph header")
    if text.count("[") != text.count("]"):
        errors.append("unbalanced []")
    if text.count("subgraph") != text.count("\n  end") + text.count("\n    end"):
        # count 'end' lines
        ends = len(re.findall(r"^\s*end\s*$", text, re.M))
        if text.count("subgraph") != ends:
            errors.append("subgraph/end mismatch (%d subgraph, %d end)" % (text.count("subgraph"), ends))
    for m in re.finditer(r"^\s*([A-Za-z0-9_]+)\[", text, re.M):
        if not re.match(r"^[A-Za-z_]", m.group(1)):
            errors.append("bad node id %s" % m.group(1))
    return errors


def validate(json_obj, graphml_str, mmd_str):
    problems = []
    # json round-trips
    try:
        json.loads(json.dumps(json_obj))
    except Exception as e:
        problems.append("json: %r" % e)
    # graphml well-formed
    try:
        minidom.parseString(graphml_str)
    except Exception as e:
        problems.append("graphml: %r" % e)
    # mermaid lint
    for e in lint_mermaid(mmd_str):
        problems.append("mermaid: " + e)
    # evidence rule: every real/executed node must carry evidence
    for n in json_obj["nodes"]:
        cov = n["attrs"].get("coverage", "")
        if (n["fidelity"] == "real" or cov in ("executed", "independently_reproduced")) and not n["evidence"]:
            problems.append("no evidence on real/executed node %s" % n["id"])
    for e in json_obj["edges"]:
        if e["fidelity"] == "real" and not e["evidence"]:
            problems.append("no evidence on real edge %s->%s" % (e["src"], e["dst"]))
    return problems


# ---------------------------------------------------------------------------
def main():
    generated_at = os.environ.get("M4_WM_GENERATED_AT")
    if not generated_at:
        epoch = os.environ.get("SOURCE_DATE_EPOCH")
        if epoch:
            generated_at = datetime.datetime.utcfromtimestamp(int(epoch)).strftime("%Y-%m-%dT%H:%M:%SZ")
        else:
            generated_at = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    provenance = []
    for key, rel in sorted(INPUTS.items()):
        ap = p(rel)
        provenance.append({"key": key, "path": rel,
                           "sha256": sha256(ap) if os.path.exists(ap) else "MISSING",
                           "exists": os.path.exists(ap)})

    g, _ = build()
    json_obj = emit_json(g, provenance, generated_at)
    graphml_str = emit_graphml(g)
    mmd_str = emit_mermaid(g)

    problems = validate(json_obj, graphml_str, mmd_str)

    outdir = p("reports/implementation")
    with open(os.path.join(outdir, "m4-world-model.json"), "w", encoding="utf-8") as f:
        json.dump(json_obj, f, indent=1, sort_keys=False, ensure_ascii=False)
        f.write("\n")
    with open(os.path.join(outdir, "m4-world-model.graphml"), "w", encoding="utf-8") as f:
        f.write(graphml_str + "\n")
    with open(os.path.join(outdir, "m4-world-model.mmd"), "w", encoding="utf-8") as f:
        f.write(mmd_str + "\n")

    st = json_obj["stats"]
    print("=" * 68)
    print("M4 World-Model Graph  (task T21, Stretch H)")
    print("generated_at:", generated_at)
    print("-" * 68)
    print("NODES total:", st["nodes_total"])
    print("  by type:     ", json.dumps(st["nodes_by_type"]))
    print("  by fidelity: ", json.dumps(st["nodes_by_fidelity"]))
    print("EDGES total:", st["edges_total"])
    print("  by type:     ", json.dumps(st["edges_by_type"]))
    print("  by fidelity: ", json.dumps(st["edges_by_fidelity"]))
    print("-" * 68)
    print("outputs:")
    print("  reports/implementation/m4-world-model.json")
    print("  reports/implementation/m4-world-model.graphml")
    print("  reports/implementation/m4-world-model.mmd")
    print("-" * 68)
    if problems:
        print("VALIDATION PROBLEMS (%d):" % len(problems))
        for pr in problems:
            print("  !", pr)
        print("=" * 68)
        return 1
    print("VALIDATION: OK (json parses, graphml well-formed XML, mermaid lint clean, evidence rule holds)")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    sys.exit(main())
