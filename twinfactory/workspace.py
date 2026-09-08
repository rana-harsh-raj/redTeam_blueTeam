"""Instance execution directory rendering.

    <FACTORY_HOME>/instances/<id>/
        manifest.json               instance manifest (binding: snapshot, recipes, profile, seed, hashes, images)
        runtime_instance.json       RuntimeInstance record (content-id chained to the manifest)
        profile.json                the derived runtime profile with its derivation trace
        events.jsonl                lifecycle events
        ENV2_COMPOSE/               rendered copy of the static arena definition (+ instance .env.arena, generated
                                    secrets/, generated/, seeds/generated/, .runtime/)  -- never the source checkout
        inputs/migrations/<svc>/    credential-free migration assets mounted read-only into the one-shot migrate jobs
        inputs/fts-config/          fts repo config dir (FTS_REPO_CONFIG_DIR)
        DOMAIN_REPLICAS/source_to_pay/runtime/mysql/init.sql   (docker-compose.s2p.yml mounts it by relative path)
        logs/                       per-step logs;  state/<label>/ datastore dumps;  journeys/ runner output
        evidence/                   runtime evidence (fingerprint, health, isolation checks)

Only the static definition is copied: nothing generated in the source checkout (secrets, rendered config, seeds,
.runtime, snapshots) is ever carried over. The instance VM mounts ONLY this directory."""
import json
import re
import shutil
from pathlib import Path

from . import paths
from .util import hash_tree, digest_of, write_json, sha256_file

EXCLUDE_DIRS = {"generated", "snapshots", ".runtime", "__pycache__", "merchant-keys", "verifier-bridge", ".pytest_cache"}
EXCLUDE_FILES = {"SAFETY_PREFLIGHT.md", "EGRESS_AUDIT.md", "jwks.json", ".env.secrets", ".DS_Store"}
# static-definition trees hashed into inputs_hash (credential-free); mirrors ENV2_COMPOSE/scripts/fingerprint.py TREES
INPUT_TREES = ("config/templates", "config/routes.py", "config/generate.py", "config/arena.yaml", "seeds", "substitutes", "scripts",
               "build", "preflight", "secrets/gen-secrets.sh", "secrets/materialize.py", "docker-compose.yml", "docker-compose.s2p.yml",
               "docker-compose.twin.yml", "verifier", "network")


def _copy_env2(dst):
    src = paths.ENV2

    def ignore(d, names):
        out = set()
        rel = Path(d).relative_to(src)
        for n in names:
            p = rel / n
            if n in EXCLUDE_DIRS or n in EXCLUDE_FILES or n.endswith((".pyc", ".pyo")):
                out.add(n)
            elif rel.as_posix() == "secrets" and n.endswith(".txt"):
                out.add(n)          # never carry a generated credential over
            elif rel.as_posix() == "seeds" and n == "generated":
                out.add(n)
        return out

    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=ignore, symlinks=False)
    for p in dst.rglob("*"):
        if p.is_dir() and p.name in EXCLUDE_DIRS:
            shutil.rmtree(p, ignore_errors=True)


def input_bundle_sources(name):
    """Where a credential-free build input (migration assets, fts config) may come from, in order: the accepted local
    copies of the checkout (.local/, never committed) or the factory home's input bundle (exported from such a checkout
    with `python3 -m twinfactory inputs export`, so a CLEAN checkout can still provision)."""
    local = {"migrations": paths.LOCAL_MIGRATIONS, "fts-config": paths.LOCAL_REPOS / "fts" / "config"}[name]
    return [("local-accepted-copies", local), ("factory-home-bundle", paths.FACTORY_HOME / "inputs" / name)]


def export_input_bundles():
    """Copy the checkout's accepted credential-free inputs into the factory home (content digests recorded)."""
    out = {}
    for name in ("migrations", "fts-config"):
        src = input_bundle_sources(name)[0][1]
        dst = paths.FACTORY_HOME / "inputs" / name
        if not src.is_dir():
            out[name] = {"status": "UNKNOWN", "reason": "source not present: %s" % src}
            continue
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", ".DS_Store"))
        out[name] = {"status": "EXPORTED", "source": str(src), "tree_digest": digest_of(hash_tree(dst)), "files": len(hash_tree(dst))}
    write_json(paths.FACTORY_HOME / "inputs" / "INDEX.json", out)
    return out


def _copy_input(exec_dir, name):
    dst = exec_dir / "inputs" / name
    for label, src in input_bundle_sources(name):
        if src.is_dir() and any(src.iterdir()):
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", ".DS_Store"))
            rec = {"status": "COPIED", "source_kind": label, "source": str(src), "tree_digest": digest_of(hash_tree(dst))}
            man = dst / "manifest.json"
            if man.is_file():
                rec["manifest_sha256"] = sha256_file(man)
            return rec, dst
    dst.mkdir(parents=True, exist_ok=True)
    return {"status": "UNKNOWN", "reason": "no %s assets: neither the checkout's accepted copies nor the factory-home bundle (%s) is present" %
            (name, paths.FACTORY_HOME / "inputs" / name)}, dst


def _copy_migrations(exec_dir):
    return _copy_input(exec_dir, "migrations")


def _copy_fts_config(exec_dir):
    return _copy_input(exec_dir, "fts-config")


def _copy_s2p_inputs(exec_dir):
    src = paths.S2P / "runtime" / "mysql" / "init.sql"
    dst = exec_dir / "DOMAIN_REPLICAS" / "source_to_pay" / "runtime" / "mysql" / "init.sql"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return {"init_sql_sha256": sha256_file(dst)}


def _patch_subnet_defaults(compose_path, arena_subnet, ingress_subnet):
    """preflight.py scans docker-compose.yml LITERALLY and flags RFC1918 fallbacks outside the instance subnets; rewrite
    the `${ARENA_SUBNET:-...}` / `${INGRESS_SUBNET:-...}` default literals of the COPY to the instance's subnets."""
    t = compose_path.read_text()
    t2 = re.sub(r"(ARENA_SUBNET:-)[0-9./]+", r"\g<1>" + arena_subnet, t)
    t2 = re.sub(r"(INGRESS_SUBNET:-)[0-9./]+", r"\g<1>" + ingress_subnet, t2)
    compose_path.write_text(t2)
    return t != t2


def render_env_arena(exec_dir, plan, epoch, s2p_image, arena_tag, extra=None):
    """The instance's own .env.arena: every host-global or instance-specific value is set here and nowhere else."""
    env2 = exec_dir / "ENV2_COMPOSE"
    mig = exec_dir / "inputs" / "migrations"
    lines = [
        "# Rendered by twinfactory for instance %s -- NO CREDENTIALS HERE (secrets/ is generated per instance)" % plan["instance_id"],
        "COMPOSE_PROJECT_NAME=%s" % plan["compose_project"],
        "ARENA_INSTANCE=%s" % plan["instance_id"],
        "ARENA_SUFFIX=%s" % plan["arena_suffix"],
        "ARENA_SUBNET=%s" % plan["arena_subnet"],
        "INGRESS_SUBNET=%s" % plan["ingress_subnet"],
        "KONG_LITE_HOST_PORT=%s" % plan["kong_host_port"],
        "ARENA_TAG=%s" % arena_tag,
        "ARENA_WORKFLOW_HOST=http://workflow-engine:8093",
        "ARENA_MOZART_IMPL=mozart-sim",
        "ARENA_SEED_EPOCH=%d" % epoch,
        "PAYOUTS_REPO_MIGRATIONS_DIR=%s" % (mig / "payouts"),
        "LEDGER_REPO_MIGRATIONS_DIR=%s" % (mig / "ledger"),
        "FTS_REPO_MIGRATIONS_DIR=%s" % (mig / "fts"),
        "XBALANCES_REPO_MIGRATIONS_DIR=%s" % (mig / "x-balances"),
        "FTS_REPO_CONFIG_DIR=%s" % (exec_dir / "inputs" / "fts-config"),
        "S2P_SOURCE_IMAGE=%s" % (s2p_image or ""),
        "S2P_MYSQL_USER=s2p",
    ]
    for k, v in sorted((extra or {}).items()):
        lines.append("%s=%s" % (k, v))
    (env2 / ".env.arena").write_text("\n".join(lines) + "\n")
    return env2 / ".env.arena"


def render(exec_dir, plan, epoch, s2p_image, arena_tag):
    exec_dir = Path(exec_dir)
    exec_dir.mkdir(parents=True, exist_ok=True)
    for d in ("logs", "evidence", "journeys", "state", "inputs"):
        (exec_dir / d).mkdir(exist_ok=True)
    env2 = exec_dir / "ENV2_COMPOSE"
    _copy_env2(env2)
    migrations, _ = _copy_migrations(exec_dir)
    fts_cfg, _ = _copy_fts_config(exec_dir)
    s2p_inputs = _copy_s2p_inputs(exec_dir)
    patched = _patch_subnet_defaults(env2 / "docker-compose.yml", plan["arena_subnet"], plan["ingress_subnet"])
    render_env_arena(exec_dir, plan, epoch, s2p_image, arena_tag)
    inputs_hash = inputs_digest(exec_dir)
    rec = {"exec_dir": str(exec_dir), "env2_root": str(env2), "migrations": migrations, "fts_config": fts_cfg, "s2p_inputs": s2p_inputs,
           "compose_subnet_defaults_rewritten": patched, "inputs_hash": inputs_hash["digest"], "inputs_file_count": inputs_hash["count"],
           "source_checkout": str(paths.REPO), "source_definition_hashes": {"docker-compose.yml": sha256_file(paths.COMPOSE), "docker-compose.s2p.yml": sha256_file(paths.COMPOSE_S2P)}}
    write_json(exec_dir / "workspace.json", rec)
    return rec


def inputs_digest(exec_dir):
    """Credential-free digest of the rendered static definition (compose files, templates, seed templates, substitutes,
    scripts, migration assets). Two instances rendered from the same checkout + profile share it; secrets never enter it."""
    env2 = Path(exec_dir) / "ENV2_COMPOSE"
    hashes = {}
    for t in INPUT_TREES:
        p = env2 / t
        if t == "docker-compose.yml" and paths.COMPOSE.is_file():
            hashes["ENV2_COMPOSE/" + t] = sha256_file(paths.COMPOSE)   # the source definition; the copy only differs by instance subnet defaults
        elif p.is_file():
            hashes["ENV2_COMPOSE/" + t] = sha256_file(p)
        elif p.is_dir():
            excl = ("seeds/generated",) if t == "seeds" else ()
            for k, v in hash_tree(p).items():
                rel = "ENV2_COMPOSE/%s/%s" % (t, k)
                if any(rel.startswith("ENV2_COMPOSE/" + e) for e in excl):
                    continue
                hashes[rel] = v
    for t in ("inputs/migrations", "inputs/fts-config", "DOMAIN_REPLICAS"):
        p = Path(exec_dir) / t
        if p.is_dir():
            for k, v in hash_tree(p).items():
                hashes[t + "/" + k] = v
    return {"digest": digest_of(hashes), "count": len(hashes), "hashes": hashes}


def rendered_config_digest(exec_dir):
    """Digest over the RENDERED, secret-bearing configuration (generated/, seeds/generated/, secrets/*) -- instance-private;
    only the digest is recorded, never the content."""
    env2 = Path(exec_dir) / "ENV2_COMPOSE"
    hashes = {}
    for t in ("generated", "seeds/generated", "secrets", ".env.arena"):
        p = env2 / t
        if p.is_file():
            hashes["ENV2_COMPOSE/" + t] = sha256_file(p)
        elif p.is_dir():
            for k, v in hash_tree(p).items():
                hashes["ENV2_COMPOSE/%s/%s" % (t, k)] = v
    return {"digest": digest_of(hashes), "count": len(hashes)}


def secrets_manifest(exec_dir):
    """Names + sha256 of every generated secret file (values never leave the instance directory)."""
    sec = Path(exec_dir) / "ENV2_COMPOSE" / "secrets"
    out = {}
    for p in sorted(sec.rglob("*")):
        if p.is_file() and (p.suffix == ".txt" or p.name in ("jwks.json", ".env.secrets") or p.parent.name in ("merchant-keys", "verifier-bridge")):
            out[p.relative_to(sec).as_posix()] = sha256_file(p)
    return out
