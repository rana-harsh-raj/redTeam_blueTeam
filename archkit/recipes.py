"""Normalized ServiceRecipe for every service of the canonical M7 runtime (98 = 94 compose services with a
datastores/substitutes/core profile + the 4 Source-to-Pay overlay services).

Built ONLY from committed inputs (compose files, build-host.sh, Dockerfiles, CONTRACT/FIDELITY docs, gen-secrets.sh,
the static snapshot graph and SourceLock, the S2P spec). Nothing is read from clones, .local, docker or secrets.
Every value that cannot be derived is an explicit {"status": "UNKNOWN", "reason": ...} -- never a guess.
"""
import importlib.util
import json
import re
from collections import Counter
from . import paths
from .canon import canonical_bytes, content_id, sha256_file

CORE = {"payouts": "payouts", "ledger": "ledger", "fts": "fts", "cfa": "cfa", "xbalances": "x-balances"}
UNKNOWN = lambda reason: {"status": "UNKNOWN", "reason": reason}  # noqa: E731
RESET = {
    "datastore": "volume removed by ENV2_COMPOSE/scripts/down.sh (compose down -v); re-seeded by scripts/up.sh",
    "api-ingress": "POST /_ingress/reset (admin) empties mutable tables (sessions, otps, idempotency, payout_details, internal_payouts, callbacks; seed ownership kept); volume rzp-arena-ingress-data removed by down.sh -v",
    "batch-sim": "volume rzp-arena-batch-sim-data removed by down.sh -v",
    "workflow-engine": "volume wfengine-data removed by down.sh -v",
    "s2p": "RED_LOOP/m7/s2p_stack.py down removes rzp-arena-s2p-mysql-data + rzp-s2p-internal; up restores a fresh stack",
    "stateless": "no persistent state in the container; recreated by compose up / down.sh",
}


def _mod(name, p):
    spec = importlib.util.spec_from_file_location(name, p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def canonical_services():
    import yaml
    base = yaml.safe_load(paths.COMPOSE.read_text())
    over = yaml.safe_load(paths.COMPOSE_S2P.read_text())
    svcs = {}
    for name, spec in base["services"].items():
        if set(spec.get("profiles") or []) & {"datastores", "substitutes", "core"}:
            svcs[name] = (spec, "ENV2_COMPOSE/docker-compose.yml")
    for name, spec in over["services"].items():
        if name not in base["services"]:
            svcs[name] = (spec, "ENV2_COMPOSE/docker-compose.s2p.yml")
    return dict(sorted(svcs.items())), base, over


def _secret_names_from_script():
    txt = (paths.ENV2 / "secrets/gen-secrets.sh").read_text()
    return sorted(set(re.findall(r"secrets/([a-z0-9_]+)\.txt", txt)))


def _env_secret_refs(env):
    out = []
    if isinstance(env, list):
        env = dict(e.split("=", 1) for e in env if "=" in e)
    for k, v in sorted((env or {}).items()):
        v = str(v)
        if k.endswith("_FILE") or k.endswith("_DIR") and "secret" in v.lower():
            if v.startswith("/run/secrets/"):
                out.append({"env": k, "secret_path": v})
    return out


def _networks(spec, compose_doc):
    names = []
    for n in spec.get("networks") or []:
        blk = (compose_doc.get("networks") or {}).get(n) or {}
        names.append({"compose_key": n, "name": blk.get("name", n), "internal": blk.get("internal")})
    return names


def _volumes(spec, compose_doc):
    out = []
    for v in spec.get("volumes") or []:
        src = str(v).split(":")[0]
        blk = (compose_doc.get("volumes") or {}).get(src)
        if blk is not None or src in (compose_doc.get("volumes") or {}):
            out.append({"volume": src, "name": (blk or {}).get("name") or ("<project>_" + src), "mount": ":".join(str(v).split(":")[1:2]), "kind": "named"})
        else:
            out.append({"volume": src, "mount": ":".join(str(v).split(":")[1:2]), "kind": "bind"})
    return out


def _health(spec):
    hc = spec.get("healthcheck")
    if not hc:
        return UNKNOWN("compose service declares no healthcheck")
    test = hc.get("test")
    return {"test": " ".join(test[1:]) if isinstance(test, list) else str(test), "interval": hc.get("interval"), "timeout": hc.get("timeout"),
            "retries": hc.get("retries"), "start_period": hc.get("start_period")}


def _core_target(image):
    m = re.match(r"rzp-arena/([a-z]+):", image or "")
    return m.group(1) if m and m.group(1) in CORE else None


def _build(spec, name, image, build_host_text):
    if spec.get("build"):
        b = spec["build"]
        ctx = b.get("context") if isinstance(b, dict) else b
        df = (b.get("dockerfile") if isinstance(b, dict) else None) or "Dockerfile"
        args = (b.get("args") if isinstance(b, dict) else None) or {}
        dpath = paths.ENV2 / str(ctx).lstrip("./") / df
        return {"kind": "compose-build", "canonical": "cd ENV2_COMPOSE && docker compose build %s" % name, "context": "ENV2_COMPOSE/" + str(ctx).lstrip("./"),
                "dockerfile": "ENV2_COMPOSE/" + str(ctx).lstrip("./") + "/" + df, "dockerfile_sha256": sha256_file(dpath) if dpath.is_file() else None,
                "build_args": {k: str(v) for k, v in sorted(args.items())}}
    t = _core_target(image)
    if t:
        steps = [l.strip() for l in build_host_text.splitlines() if ("go build" in l and ("$REPOS_ROOT/" + CORE[t] in l or "/" + CORE[t] + " " in l)) or ("docker build" in l and t in l)]
        return {"kind": "build-host", "canonical": "REPOS_ROOT=<accepted copies> ARENA_TAG=<tag> bash ENV2_COMPOSE/build/build-host.sh %s" % t,
                "script": "ENV2_COMPOSE/build/build-host.sh", "script_sha256": sha256_file(paths.ENV2 / "build/build-host.sh"),
                "dockerfile": "ENV2_COMPOSE/build/runtime-only.Dockerfile", "steps": steps[:12],
                "provenance": "python3 ENV2_COMPOSE/build/prepare-repos.py + check-inputs.py (build evidence under .local/twin-repos/build-evidence-*; not committed)"}
    if image and not image.startswith("rzp-arena/"):
        return {"kind": "pulled", "canonical": "docker pull " + image, "image": image}
    return UNKNOWN("no build block, no build-host target and no pullable image for %s" % name)


def _deviations(name, spec, node):
    subdir = paths.ENV2 / "substitutes" / name
    out = []
    for fn in ("CONTRACT.md", "FIDELITY.md"):
        p = subdir / fn
        if p.is_file():
            out.append({"ref": paths.rel(p), "sha256": sha256_file(p), "kind": "substitute-contract"})
    spec_doc = paths.ENV2.parent / "TWIN_SPEC" / "substitute-contracts"
    for p in sorted(spec_doc.glob("*.md")):
        stem = p.stem
        if name.split("-")[0] in stem or stem.split("-")[0] in name:
            out.append({"ref": paths.rel(p), "sha256": sha256_file(p), "kind": "twin-spec-contract"})
    t = _core_target(str(spec.get("image") or ""))
    if t:
        pd = paths.ENV2 / "build/arena-patches"
        for p in sorted(pd.rglob("*")):
            if p.is_file() and (CORE[t] in p.as_posix() or t in p.name):
                out.append({"ref": paths.rel(p), "sha256": sha256_file(p), "kind": "arena-build-patch"})
    if name.startswith("s2p-"):
        p = paths.S2P / "spec/declared-deviations.yaml"
        out.append({"ref": paths.rel(p), "sha256": sha256_file(p), "kind": "declared-deviations"})
    return out


def build_recipes(snapshot_body, index):
    svcs, base, over = canonical_services()
    R = _mod("m6_recipes", paths.REPO / "scripts/snapshot/recipes.py")
    build_host_text = (paths.ENV2 / "build/build-host.sh").read_text()
    secret_names = _secret_names_from_script()
    lock = snapshot_body["source_lock"]["repositories"]
    node_by_svc = {n.get("compose_service"): n["id"] for n in snapshot_body["graph"]["nodes"] if n.get("compose_service")}
    recipes = {}
    for name, (spec, cfile) in svcs.items():
        doc = base if cfile.endswith("docker-compose.yml") else over
        image = str(spec.get("image") or "")
        nid = node_by_svc.get(name)
        node = index.nodes.get(nid) if nid else None
        t = _core_target(image)
        is_sub = name in R.SUB_REPLACES or name in ("api-ingress", "workflow-engine", "batch-sim")
        repo_key = CORE.get(t) if t else ("vendor-payments" if name == "s2p-vp-source" else None)
        if is_sub or name.startswith("s2p-") and name != "s2p-vp-source":
            src_dir = paths.ENV2 / "substitutes" / name
            server = src_dir / "server.py"
            source = {"kind": "twin-local", "path": paths.rel(src_dir) if src_dir.is_dir() else None,
                      "content_sha256": sha256_file(server) if server.is_file() else None,
                      "replaces": R.SUB_REPLACES.get(name, ([], None))[0] or ([node.get("label")] if node else []),
                      "replaced_repo": R.SUB_REPLACES.get(name, ([], None))[1]}
            if source["replaced_repo"] and source["replaced_repo"] in lock:
                source["replaced_repo_sha"] = lock[source["replaced_repo"]].get("sha")
            if not src_dir.is_dir():
                source = {"kind": "pulled-image" if image and not image.startswith("rzp-arena/") else "twin-local", "image": image or None,
                          "path": None, "content_sha256": None, "note": "no twin-local source directory; runtime is the pulled image"}
        elif repo_key:
            lk = lock.get(repo_key, {})
            source = {"kind": "pinned-repository", "repository": "razorpay/" + repo_key, "sha": lk.get("sha") or UNKNOWN("not in SourceLock"),
                      "remote": lk.get("remote"), "source_lock_id": snapshot_body["source_lock_id"], "graph_node": nid}
        else:
            source = {"kind": "pulled-image", "image": image, "path": None}
        env = spec.get("environment") or {}
        env_keys = sorted(env.keys()) if isinstance(env, dict) else sorted(e.split("=", 1)[0] for e in env)
        secret_refs = _env_secret_refs(env)
        compose_secrets = [s if isinstance(s, str) else s.get("source") for s in (spec.get("secrets") or [])]
        vols = _volumes(spec, doc)
        named = [v for v in vols if v["kind"] == "named"]
        if spec.get("profiles") == ["datastores"] or name in ("kafka", "localstack", "redis") or name.startswith(("mysql", "postgres", "mongo")) or name in ("s2p-mysql", "s2p-kafka", "s2p-redis"):
            reset = RESET["s2p"] if name.startswith("s2p-") else RESET["datastore"]
        elif name in RESET:
            reset = RESET[name]
        elif name.startswith("s2p-"):
            reset = RESET["s2p"]
        elif named:
            reset = "named volume(s) %s removed by down.sh -v" % ",".join(v["volume"] for v in named)
        else:
            reset = RESET["stateless"]
        fams = sorted(index.families_of.get(nid, [])) if nid else []
        journeys = sorted({j for f in fams for j in index.journeys_by_family.get(f, [])} | ({e["from"] for e in index.edges_of(nid, "in", ("depends_on",)) if e["from"].startswith("journey:")} if nid else set()))
        edges = index.edges_of(nid) if nid else []
        deps = {"compose_depends_on": sorted((spec.get("depends_on") or {}).keys()) if isinstance(spec.get("depends_on"), dict) else sorted(spec.get("depends_on") or []),
                "graph_edges": sorted({"%s (%s)" % (e["to"] if e["from"] == nid else e["from"], e["type"]) for e in edges if e["type"] in ("calls", "depends_on", "reads", "writes", "produces", "consumes", "callback")})}
        datastores = sorted({e["to"] for e in edges if e["from"] == nid and e["to"].startswith(("db:", "table:"))} | set(node.get("tables") or []) if node else set())
        queues = sorted(set((node.get("queues") or []) + (node.get("topics") or [])) | {e["to"] for e in edges if e["from"] == nid and e["to"].startswith(("queue:", "topic:"))}) if node else []
        fixtures = list(R.SEEDS_FOR.get(name, [])) + (list(R.CORE_SEEDS.get(CORE[t], [])) if t else [])
        if name.startswith("s2p-"):
            fixtures += ["DOMAIN_REPLICAS/source_to_pay/fixtures/tds-entry.json"]
        unknowns = []
        if isinstance(source.get("sha"), dict):
            unknowns.append("source.sha")
        unknowns.append("runtime.image_digest")
        if not spec.get("healthcheck"):
            unknowns.append("health")
        if not fixtures:
            unknowns.append("fixtures")
        unknowns += ["production.replica_count", "production.configuration", "production.deployment_manifest"]
        r = {"kind": "service_recipe", "schema_version": "m8.1", "id": name, "compose_file": cfile, "profiles": sorted(spec.get("profiles") or []),
             "graph_node": nid, "category": "core-real-binary" if t else ("source-to-pay" if name.startswith("s2p-") else "substitute" if is_sub else "datastore-or-infra"),
             "source": source,
             "build": _build(spec, name, image, build_host_text),
             "runtime": {"image": image or None, "image_digest": UNKNOWN("image ids are build outputs, not committed inputs; see SNAPSHOT_MANIFEST.json image_digests for the observed local ids"),
                         "entrypoint": spec.get("entrypoint"), "command": spec.get("command"), "environment_keys": env_keys, "user": spec.get("user"),
                         "one_shot": name == "ledger-scheduler"},
             "health": _health(spec),
             "dependencies": deps,
             "networks": _networks(spec, doc),
             "state": {"volumes": vols, "stateful": bool(named), "datastores": datastores, "queues_topics": queues,
                       "migrations": ("cd ENV2_COMPOSE && docker compose --profile migrations run --rm %s-migrate" % t) if t else None},
             "fixtures": sorted(set(fixtures)),
             "identities": {"identity_model": (node or {}).get("identity_model"), "compose_secrets": sorted(s for s in compose_secrets if s),
                            "secret_files_referenced": secret_refs,
                            "secret_names_generated_per_boot": [s for s in secret_names if name.replace("-", "_") in s or (t and t in s) or (name == "api-ingress" and (s.startswith("app_") or s == "ingress_admin_token"))]},
             "fidelity": {"class": (node or {}).get("fidelity"), "m8_label": index.label_of(nid) if nid else None, "basis": (node or {}).get("fidelity_basis"),
                          "evidence": (node or {}).get("fidelity_evidence"), "missing_dependency": (node or {}).get("missing_dependency")},
             "deviations": _deviations(name, spec, node),
             "reset": reset,
             "journeys": journeys, "families": fams,
             "production_unknowns": index.unknowns_by_node.get(nid, []) if nid else [],
             "unknowns": sorted(set(unknowns))}
        if nid is None:
            r["unknowns"].append("graph_node")
            r["fidelity"] = {"class": None, "m8_label": None, "basis": None, "evidence": UNKNOWN("no functional-graph node maps to this compose service"), "missing_dependency": None}
        r["recipe_id"] = content_id(r)
        recipes[name] = r
    return recipes


def write_recipes(store, sid, recipes):
    d = store.dir(sid) / "recipes"
    d.mkdir(parents=True, exist_ok=True)
    for name, r in recipes.items():
        (d / (name + ".json")).write_bytes(canonical_bytes(r))
    hist = Counter(u for r in recipes.values() for u in r["unknowns"])
    idx = {"kind": "service_recipe_index", "schema_version": "m8.1", "snapshot_id": sid, "count": len(recipes),
           "recipes": [{"id": n, "recipe_id": r["recipe_id"], "category": r["category"], "graph_node": r["graph_node"], "fidelity": r["fidelity"]["m8_label"],
                        "stateful": r["state"]["stateful"], "unknowns": r["unknowns"]} for n, r in sorted(recipes.items())],
           "by_category": dict(sorted(Counter(r["category"] for r in recipes.values()).items())),
           "unknown_field_histogram": dict(sorted(hist.items()))}
    idx["recipe_set_id"] = content_id(idx)
    (d / "INDEX.json").write_bytes(canonical_bytes(idx))
    store.register(sid, recipe_set_id=idx["recipe_set_id"], recipe_count=len(recipes))
    return idx
