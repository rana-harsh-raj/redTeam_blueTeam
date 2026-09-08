"""Runtime profiles derived from the M8 snapshot + recipes (never from a handwritten service list).

    full                 every canonical service recipe of the snapshot (the accepted M7 runtime shape) + the
                         Source-to-Pay overlay + the one-shot migration jobs for every included datastore
    focused:<family>     the closure of one journey family over the architecture relationships (rules R1..R9)
    critical-payouts     alias for focused:shared-payouts (the P0 pooled-balance payout flow)
    focused-s2p          alias for focused:cross-domain-s2p (the connected Source-to-Pay journey)

Every included service carries the rule(s) that pulled it in, so a profile is auditable against the graph.
"""
import re
from collections import OrderedDict, defaultdict
from pathlib import Path

import yaml

from . import paths

RULES = OrderedDict([
    ("R1", "family components / entry-point owners (graph families[].components, entry_points -> owns)"),
    ("R2", "journey definitions of the family: journey -> depends_on -> substitute"),
    ("R2b", "journey proves_families -> those families' components"),
    ("R3", "calls closure over included services (svc/worker/sub -calls-> svc/sub; ext -> implemented by sub)"),
    ("R4", "datastore edges (depends_on/reads/writes/produces/consumes -> db:* -> compose service in twin_ref)"),
    ("R5", "compose depends_on (recipe.dependencies.compose_depends_on, transitive)"),
    ("R6", "compose-declared and config-template service references of included services (http://<service>:<port>, \"host:port\", {{SVC.<key>.host|url}} tokens rendered from config/arena.yaml, {{MOZART.url}}, {{WORKFLOW_HOST}})"),
    ("R7", "journey harness requirements (container targets the runner/provisioner exec into)"),
    ("R8", "one-shot migration jobs for included real-binary images whose datastore is included"),
    ("R9", "Source-to-Pay overlay closure (s2p-vp-source needs api-ingress + its private datastores)"),
    ("R10", "declared worker fleet of an included real-binary service (svc -depends_on-> worker in the graph)"),
    ("R11", "compose-declared worker fleet of an included real binary: every real-binary recipe running the same image (the binary's worker roles are declared in the compose definition, e.g. fts `-command=rbl::check_transfer_status`; the graph covers only source-analysed queues)"),
])
MIGRATION_JOBS = {"payouts-migrate": ("rzp-arena/payouts", "mysql-payouts"), "ledger-migrate": ("rzp-arena/ledger", "postgres-ledger"),
                  "fts-migrate": ("rzp-arena/fts", "mysql-fts"), "cfa-migrate": ("rzp-arena/cfa", "mongo-cfa"),
                  "xbalances-migrate": ("rzp-arena/xbalances", "mysql-xbalances")}
TEMPLATE_DIRS = {"payouts": "payouts", "ledger": "ledger", "fts": "fts", "cfa": "cfa", "xbalances": "xbalances"}
ALIASES = {"critical-payouts": "focused:shared-payouts", "focused-s2p": "focused:cross-domain-s2p"}
HARNESS_FILES = ("RED_LOOP/red_loop/provisioner.py", "RED_LOOP/red_loop/provisioner_direct.py", "RED_LOOP/red_loop/ledger_da.py",
                 "RED_LOOP/m6/journeys/framework.py")
JOURNEY_MODULES = {"shared-payouts": "j_shared", "cross-domain-s2p": "j_s2p", "direct-payouts": "j_direct", "webhooks": "j_webhooks",
                   "accounting": "j_accounting", "queued-low-balance": "j_queued", "scheduled-payouts": "j_scheduled",
                   "idempotency-retries": "j_idempotency", "approval-workflow": "j_approval", "shared-ingress": "j_ingress",
                   "failure-reversal-cancellation": "j_failure", "pricing-free-payouts": "j_pricing", "bulk-payouts": "j_bulk",
                   "on-hold": "j_onhold", "fetch-list": "j_fetch", "source-updates": "j_source", "async-workers": "j_workers",
                   "beneficiary-fund-accounts": "j_beneficiary"}
HOST_RE = re.compile(r"https?://([a-z0-9][a-z0-9-]*):(\d+)")
HOSTPORT_RE = re.compile(r'"([a-z][a-z0-9-]*):(\d{4,5})"')
TOKEN_RE = re.compile(r"\{\{(SVC\.[a-z0-9_]+\.(?:host|url)|MOZART\.(?:host|url)|WORKFLOW_HOST)\}\}")


def _arena_services():
    """config/arena.yaml services.<key>.host -> the token map config/generate.py renders ({{SVC.<key>.host|url}})."""
    out = {}
    for line in (paths.ENV2 / "config" / "arena.yaml").read_text().splitlines():
        m = re.match(r"\s+([a-z0-9_]+):\s*\{host:\s*([a-z0-9-]+)", line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


class Profile(dict):
    @property
    def services(self):
        return list(self["services"])

    @property
    def jobs(self):
        return list(self["jobs"])

    @property
    def s2p(self):
        return bool(self["s2p"])


def _twin_ref_service(node):
    ref = str(node.get("twin_ref") or "")
    m = re.search(r"service `([a-z0-9-]+)`", ref) or re.match(r"compose ([a-z0-9-]+)", ref)
    return m.group(1) if m else None


def _compose_docs():
    base = yaml.safe_load(paths.COMPOSE.read_text())
    over = yaml.safe_load(paths.COMPOSE_S2P.read_text())
    return base, over


def _env_values(spec):
    env = spec.get("environment") or {}
    vals = list(env.values()) if isinstance(env, dict) else [str(e).split("=", 1)[-1] for e in env]
    for v in (spec.get("volumes") or []):
        vals.append(str(v))
    return [str(v) for v in vals]


def _template_hosts(service):
    fam = service.split("-", 1)[0]
    if fam not in TEMPLATE_DIRS:
        return set()
    d = paths.ENV2 / "config" / "templates" / "base" / TEMPLATE_DIRS[fam]
    hosts = set()
    svc_map = _arena_services()
    env = (paths.ENV2 / ".env.arena").read_text()
    mz = re.search(r"^ARENA_MOZART_IMPL=([a-z0-9-]+)", env, re.M)
    for p in sorted(d.glob("*.toml")) if d.is_dir() else []:
        t = p.read_text()
        hosts.update(m.group(1) for m in HOST_RE.finditer(t))
        hosts.update(m.group(1) for m in HOSTPORT_RE.finditer(t))
        for m in TOKEN_RE.finditer(t):
            tok = m.group(1)
            if tok.startswith("SVC."):
                hosts.add(svc_map.get(tok.split(".")[1], ""))
            elif tok.startswith("MOZART."):
                hosts.add(mz.group(1) if mz else "mozart-sim")
    # the workflow host is rendered from .env.arena (ARENA_WORKFLOW_HOST) into payouts arena.toml
    if fam == "payouts":
        env = (paths.ENV2 / ".env.arena").read_text()
        m = re.search(r"^ARENA_WORKFLOW_HOST=https?://([a-z0-9-]+):", env, re.M)
        if m:
            hosts.add(m.group(1))
    return hosts


def harness_targets(family_slug):
    """Container names the journey harness execs into: parsed from the runner/provisioner sources (cname("x"))."""
    files = [paths.REPO / f for f in HARNESS_FILES]
    mod = JOURNEY_MODULES.get(family_slug)
    if mod:
        files.append(paths.RED_LOOP / "m6" / "journeys" / (mod + ".py"))
    found = defaultdict(set)
    for f in files:
        if not f.is_file():
            continue
        for m in re.finditer(r'cname\("([a-z0-9-]+)"\)', f.read_text()):
            found[m.group(1)].add(paths.rel(f))
    return found


def full_profile(inputs):
    services = OrderedDict()
    for sid in sorted(inputs.recipes):
        services[sid] = {"category": inputs.recipes[sid]["category"], "reasons": ["FULL: canonical recipe of snapshot %s" % inputs.snapshot_id[:12]]}
    jobs = [j for j, (img, ds) in MIGRATION_JOBS.items() if ds in services]
    return Profile({"name": "full", "kind": "runtime_profile", "schema_version": "m9.1", "architecture_snapshot_id": inputs.snapshot_id,
                    "recipe_set_id": inputs.recipe_set_id, "services": services, "jobs": jobs, "s2p": any(s.startswith("s2p-") for s in services),
                    "families": sorted({f for r in inputs.recipes.values() for f in (r.get("families") or [])}),
                    "journeys": ["journey:shared-payouts/success", "journey:cross-domain-s2p/success"],
                    "derivation": {"rules": {"FULL": "every canonical ServiceRecipe of the snapshot (%d) + migration jobs + Source-to-Pay overlay" % len(services)}},
                    "counts": _counts(services, jobs)})


def _counts(services, jobs):
    c = defaultdict(int)
    for s in services.values():
        c[s["category"]] += 1
    return {"services": len(services), "jobs": len(jobs), "by_category": dict(sorted(c.items()))}


def focused_profile(inputs, family_slug):
    ix = inputs.index
    fid = "family:" + family_slug
    if fid not in ix.families:
        raise KeyError("unknown family %s (known: %s)" % (fid, ", ".join(sorted(ix.families))[:400]))
    base, over = _compose_docs()
    compose = dict(base["services"])
    compose.update({k: v for k, v in over["services"].items() if k not in compose})
    services = OrderedDict()
    trace = defaultdict(list)

    def add(svc, rule, why):
        if svc not in inputs.recipes:
            return False
        trace[svc].append("%s: %s" % (rule, why))
        if svc not in services:
            services[svc] = {"category": inputs.recipes[svc]["category"], "reasons": []}
            return True
        return False

    def node_service(nid):
        r = inputs.by_node.get(nid)
        if r:
            return r
        n = ix.nodes.get(nid) or {}
        if n.get("compose_service") in inputs.recipes:
            return n["compose_service"]
        if n.get("kind") == "datastore":
            return _twin_ref_service(n)
        return None

    # R1 family components + entry-point owners
    fam = ix.families[fid]
    for c in fam.get("components") or []:
        s = node_service(c)
        if s:
            add(s, "R1", "component %s of %s" % (c, fid))
    for ep in fam.get("entry_points") or []:
        for e in ix.edges_of(ep, "in", ("owns",)):
            s = node_service(e["from"])
            if s:
                add(s, "R1", "owner of entry point %s" % ep)
    # R2 journeys -> depends_on subs; R2b proves_families
    journeys = [j for j in ix.journeys_by_family.get(fid, []) if ix.nodes[j].get("family") == fid]
    proves = set()
    for j in journeys:
        for e in ix.edges_of(j, "out", ("depends_on",)):
            s = node_service(e["to"])
            if s:
                add(s, "R2", "%s depends_on %s" % (j, e["to"]))
        proves.update(ix.nodes[j].get("proves_families") or [])
    for pf in sorted(proves):
        for c in (ix.families.get(pf) or {}).get("components") or []:
            s = node_service(c)
            if s:
                add(s, "R2b", "component %s of proven family %s" % (c, pf))
    # R7 harness
    for svc, srcs in sorted(harness_targets(family_slug).items()):
        add(svc, "R7", "journey harness exec target (%s)" % ", ".join(sorted(srcs)))
    # fixed point over R3..R6
    changed = True
    while changed:
        changed = False
        for svc in list(services):
            r = inputs.recipes[svc]
            node = r.get("graph_node")
            if node:
                for e in ix.edges_of(node, "out", ("calls",)):
                    tgt = e["to"]
                    s = node_service(tgt)
                    if s:
                        changed |= add(s, "R3", "%s calls %s" % (node, tgt))
                    if tgt.startswith(("ext:", "svc:")) and not s:
                        for imp in ix.edges_of(tgt, "in", ("implements",)):
                            s = node_service(imp["from"])
                            if s:
                                changed |= add(s, "R3", "%s calls %s implemented by %s" % (node, tgt, imp["from"]))
                for e in ix.edges_of(node, "out", ("depends_on", "reads", "writes", "produces", "consumes")):
                    if e["to"].startswith("db:"):
                        s = node_service(e["to"])
                        if s:
                            changed |= add(s, "R4", "%s %s %s" % (node, e["type"], e["to"]))
                if node.startswith("svc:") and r["category"] == "core-real-binary":
                    for e in ix.edges_of(node, "out", ("depends_on",)):
                        if e["to"].startswith("worker:"):
                            s = node_service(e["to"])
                            if s:
                                changed |= add(s, "R10", "%s depends_on %s (declared worker fleet)" % (node, e["to"]))
            if r["category"] == "core-real-binary":
                img = (r.get("runtime") or {}).get("image")
                for other, ro in inputs.recipes.items():
                    if other != svc and ro["category"] == "core-real-binary" and (ro.get("runtime") or {}).get("image") == img and not (ro.get("runtime") or {}).get("one_shot"):
                        changed |= add(other, "R11", "%s runs the same real binary image as %s (%s)" % (other, svc, img))
            for dep in r["dependencies"].get("compose_depends_on") or []:
                changed |= add(dep, "R5", "%s compose depends_on %s" % (svc, dep))
            hosts = set()
            for v in _env_values(compose.get(svc) or {}):
                hosts.update(m.group(1) for m in HOST_RE.finditer(v))
            hosts |= _template_hosts(svc)
            for h in sorted(hosts):
                if h in inputs.recipes and h != svc:
                    changed |= add(h, "R6", "%s references http://%s:<port> in its compose environment or rendered config" % (svc, h))
            if svc.startswith("s2p-") or svc == "s2p-vp-source":
                for dep in inputs.recipes["s2p-vp-source"]["dependencies"]["compose_depends_on"]:
                    changed |= add(dep, "R9", "Source-to-Pay overlay closure for s2p-vp-source")
    for svc in services:
        services[svc]["reasons"] = trace[svc]
    jobs = [j for j, (img, ds) in MIGRATION_JOBS.items() if ds in services and any(inputs.recipes[s]["runtime"].get("image", "").startswith(img) for s in services)]
    for j in jobs:
        trace[j].append("R8: migration job for %s (image %s)" % (MIGRATION_JOBS[j][1], MIGRATION_JOBS[j][0]))
    services = OrderedDict(sorted(services.items()))
    return Profile({"name": "focused:" + family_slug, "kind": "runtime_profile", "schema_version": "m9.1", "architecture_snapshot_id": inputs.snapshot_id,
                    "recipe_set_id": inputs.recipe_set_id, "services": services, "jobs": jobs, "s2p": any(s.startswith("s2p-") for s in services),
                    "families": [fid] + sorted(proves), "journeys": journeys,
                    "derivation": {"rules": dict(RULES), "family": fid, "journey_definitions": journeys, "proves_families": sorted(proves),
                                   "jobs": {j: trace[j] for j in jobs}},
                    "counts": _counts(services, jobs)})


def derive(inputs, name):
    name = ALIASES.get(name, name)
    if name == "full":
        return full_profile(inputs)
    if name.startswith("focused:"):
        return focused_profile(inputs, name.split(":", 1)[1])
    raise KeyError("unknown profile %r (full | focused:<family> | %s)" % (name, " | ".join(ALIASES)))


def compose_profiles_for(profile, inputs):
    """Which compose --profile flags the service set needs (datastores/substitutes/core/migrations/s2p)."""
    ps = set()
    for s in profile["services"]:
        ps.update(inputs.recipes[s].get("profiles") or [])
    if profile["jobs"]:
        ps.add("migrations")
    return sorted(ps)
