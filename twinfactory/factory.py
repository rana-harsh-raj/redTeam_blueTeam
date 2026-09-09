"""The reusable lifecycle interface: create -> build -> start -> status/health -> reset -> snapshot/restore state
-> stop -> destroy (+ journeys, reproduce). Backend-independent; every operation appends to the instance event log and
updates the durable registry and the instance manifest."""
import json
import os
import re
import shutil
import time
import uuid
from pathlib import Path

from . import paths, profiles as P, plan as PL, workspace as W, images as I, backend as B
from .boot import Boot, load_s2p_secrets, DATASTORES, S2P_TOPICS
from .registry import Registry
from .snapshot import ArchitectureInputs
from .util import sh, CommandError, now, stamp, write_json, read_json, content_id, digest_of, sha256_file

STATES = ("created", "built", "running", "stopped", "destroyed", "failed")
DUMPS = {"mysql-payouts": ("mysql", "payouts", "mysql_payouts_root_password"), "mysql-fts": ("mysql", "fts", "mysql_fts_root_password"),
         "mysql-xbalances": ("mysql", "rx_balances_local", "mysql_xbalances_root_password"), "mysql-apidb-stub": ("mysql", "api_local", "mysql_apidb_root_password"),
         "postgres-ledger": ("psql", "ledger", "postgres_ledger_password"), "mongo-cfa": ("mongo", "cfa", "mongo_cfa_root_password")}


def _arena_tag():
    m = re.search(r"^ARENA_TAG=(\S+)", (paths.ENV2 / ".env.arena").read_text(), re.M)
    return m.group(1) if m else "local"


S2P_IMAGE_PREFIX = "s2p-architecture-replica/vendor-payments:"


def _s2p_image(cache=None):
    """Source-to-Pay runtime image ref: the checkout's build record (DOMAIN_REPLICAS/source_to_pay/.build, not committed)
    or, in a clean checkout, the newest S2P image in the factory image cache (exported from a checkout that built it)."""
    p = paths.S2P_IMAGE_ENV
    if p.is_file():
        m = re.search(r"^S2P_SOURCE_IMAGE=(\S+)", p.read_text(), re.M)
        if m:
            return m.group(1)
    if cache is not None:
        cands = [(v.get("exported_at") or "", ref) for ref, v in cache.index()["images"].items() if ref.startswith(S2P_IMAGE_PREFIX)]
        if cands:
            return sorted(cands)[-1][1]
    return None


def _git_head():
    r = sh(["git", "-C", str(paths.REPO), "rev-parse", "HEAD"], check=False, timeout=20)
    return r["stdout"].strip() or None


class Factory:
    def __init__(self, home=None, store=None):
        self.home = Path(home) if home else paths.FACTORY_HOME
        self.registry = Registry(self.home / "registry.json")
        self.cache = I.ImageCache(self.home / "images")
        self.store = store
        self._inputs = {}

    # ---- helpers ----
    def inputs(self, snapshot_id=None):
        key = snapshot_id or "current"
        if key not in self._inputs:
            self._inputs[key] = ArchitectureInputs(self.store, snapshot_id)
        return self._inputs[key]

    def idir(self, instance_id):
        return self.home / "instances" / instance_id

    def manifest(self, instance_id):
        m = read_json(self.idir(instance_id) / "manifest.json")
        if not m:
            raise KeyError("unknown instance %r" % instance_id)
        return m

    def save_manifest(self, m):
        m["updated_at"] = now()
        write_json(self.idir(m["instance_id"]) / "manifest.json", m, mode=0o600)
        self.registry.upsert({k: m.get(k) for k in ("instance_id", "state", "profile", "architecture_snapshot_id", "recipe_set_id", "seed", "backend",
                                                    "kong_host_port", "arena_subnet", "compose_project", "exec_dir", "runtime_instance_id", "created_at")})
        return m

    def backend(self, m):
        return B.get_backend(m["backend"]["kind"], m["backend"].get("options"))

    def profile(self, m):
        p = read_json(self.idir(m["instance_id"]) / "profile.json")
        return P.Profile(p)

    def denv(self, m):
        return self.backend(m).docker_env(m)

    def boot(self, m, profile=None):
        prof = profile or self.profile(m)
        return Boot(m, prof, self.denv(m), self.idir(m["instance_id"]) / "logs", secrets_env=load_s2p_secrets(m["env2_root"]))

    def _event(self, m, kind, **f):
        self.registry.event(m["instance_id"], kind, **f)

    def _set_state(self, m, state, **f):
        m["state"] = state
        m.update(f)
        self.save_manifest(m)
        self._event(m, "state", state=state)

    # ---- create ----
    def create(self, instance_id, profile_name, seed, backend_name="colima", snapshot_id=None, sizing=None, backend_options=None, s2p_image=None, force=False, trust_path="real"):
        inputs = self.inputs(snapshot_id)
        prof = P.derive(inputs, profile_name, trust_path)
        recs = self.registry.records()
        if any(r["instance_id"] == PL.derive_names(instance_id)["instance_id"] for r in recs) and not force:
            raise RuntimeError("instance %s already exists in the registry (destroy it first or use force)" % instance_id)
        pl = PL.plan_instance(instance_id, prof["name"], recs, sizing, service_count=len(prof["services"]))
        exec_dir = self.idir(pl["instance_id"])
        if exec_dir.exists() and force:
            # clear the CONTENTS only: a kept boundary (colima VM) mounts this directory by path and a fresh inode
            # would leave a stale mount inside the VM
            for child in exec_dir.iterdir():
                shutil.rmtree(child) if child.is_dir() and not child.is_symlink() else child.unlink()
        exec_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(exec_dir, 0o700)
        epoch = PL.seed_epoch(seed)
        img = s2p_image or (_s2p_image(self.cache) if prof["s2p"] else None)
        if prof["s2p"] and not img:
            raise RuntimeError("profile needs the Source-to-Pay runtime image but none is recorded (build it with RED_LOOP/m7/s2p_stack.py build or seed the image cache)")
        t0 = time.time()
        ws = W.render(exec_dir, pl, epoch, img, _arena_tag(), trust_path=prof.get("trust_path", trust_path))
        bk = B.get_backend(backend_name, backend_options)
        m = dict(pl)
        m.update({"kind": "twin_instance_manifest", "schema_version": "m9.1", "created_at": now(), "state": "created",
                  "architecture_snapshot_id": inputs.snapshot_id, "recipe_set_id": inputs.recipe_set_id, "profile": prof["name"],
                  "trust_path": prof.get("trust_path", trust_path),
                  "profile_digest": content_id({"services": prof["services"], "jobs": prof["jobs"], "s2p": prof["s2p"], "trust_path": prof.get("trust_path", trust_path)}),
                  "compose_profiles": P.compose_profiles_for(prof, inputs), "seed": str(seed), "seed_epoch": epoch, "arena_tag": _arena_tag(),
                  "s2p_image": img, "backend": {"kind": bk.kind, "isolation_boundary": bk.isolation_boundary, "options": dict(backend_options or {}),
                                                "profile": pl["backend_profile"]},
                  "exec_dir": str(exec_dir), "env2_root": ws["env2_root"], "inputs_hash": ws["inputs_hash"], "inputs_file_count": ws["inputs_file_count"],
                  "workspace": {k: ws[k] for k in ("migrations", "fts_config", "s2p_inputs", "compose_subnet_defaults_rewritten", "source_definition_hashes")},
                  "source_checkout_head": _git_head(), "timings": {"create_secs": round(time.time() - t0, 1)},
                  "runtime_instance_id": None, "image_digests": None, "rendered_config_hash": None, "secrets_manifest_digest": None})
        write_json(exec_dir / "profile.json", prof)
        self.save_manifest(m)
        self._event(m, "create", profile=prof["name"], seed=str(seed), backend=bk.kind, trust_path=prof.get("trust_path", trust_path))
        return m

    # ---- build: provision boundary, resolve/load images, record digests ----
    def build(self, instance_id):
        m = self.manifest(instance_id)
        prof = self.profile(m)
        bk = self.backend(m)
        log = []
        t0 = time.time()
        ident = bk.provision(m, log=log) if bk.kind == "colima" else bk.provision(m)
        m["backend"]["identity"] = ident
        m["timings"]["provision_secs"] = ident.get("provision_secs", round(time.time() - t0, 1))
        self.save_manifest(m)
        t1 = time.time()
        boot = self.boot(m, prof)
        boot.generate(regen_secrets=not (Path(m["env2_root"]) / "secrets" / "postgres_ledger_password.txt").is_file())
        boot.secrets_env = load_s2p_secrets(m["env2_root"])
        m["timings"]["generate_secs"] = round(time.time() - t1, 1)
        m["rendered_config_hash"] = W.rendered_config_digest(m["exec_dir"])["digest"]
        sm = W.secrets_manifest(m["exec_dir"])
        m["secrets_manifest_digest"] = digest_of(sm)
        m["secrets_manifest_count"] = len(sm)
        write_json(self.idir(instance_id) / "evidence" / "secrets-manifest.json", {"note": "names and sha256 only; values never leave the instance directory", "files": sm}, mode=0o600)
        m["generate_steps"] = boot.steps
        t1 = time.time()
        images = I.resolve(m, prof, Path(m["env2_root"]), boot.env(secrets=True), self.cache, log=log)
        m["image_digests"] = I.digests_summary(images)
        m["image_records"] = images
        m["timings"]["images_secs"] = round(time.time() - t1, 1)
        m["timings"]["build_secs"] = round(time.time() - t0, 1)
        write_json(self.idir(instance_id) / "logs" / "build-commands.json", log)
        self._set_state(m, "built")
        self._runtime_record(m)
        return m

    # ---- start: full boot from empty state (first start) or restart of an existing stopped instance ----
    def start(self, instance_id, regen_secrets=None, resume_from=None):
        m = self.manifest(instance_id)
        if m["state"] not in ("built", "stopped", "running", "failed"):
            raise RuntimeError("instance %s is %s; build it first" % (instance_id, m["state"]))
        prof = self.profile(m)
        bk = self.backend(m)
        st = bk.status(m)
        if not (st.get("identity") or {}).get("reachable"):
            m["backend"]["restart"] = bk.start(m)
        boot = self.boot(m, prof)
        t0 = time.time()
        if resume_from and m["state"] != "failed":
            raise RuntimeError("--resume-from applies to a FAILED boot only (instance %s is %s)" % (instance_id, m["state"]))
        if m["state"] == "stopped" and m.get("boot"):
            # containers and volumes exist: resume them, then re-arm the host bridge
            boot.compose("resume-arena", "start", check=False)
            if prof["s2p"]:
                boot.compose("resume-s2p", "start", s2p=True, check=False)
            boot.wait_healthy([s for s in prof["services"] if s != "ledger-scheduler"], timeout=420, name="resume-health")
            boot.bridge("start")
            m["timings"]["restart_secs"] = round(time.time() - t0, 1)
            m["boot"]["resumes"] = m["boot"].get("resumes", 0) + 1
            m["boot"]["last_resume_steps"] = boot.steps
        else:
            try:
                info = boot.full(resume_from=resume_from)
            except Exception as e:  # noqa: BLE001
                m["boot"] = {"failed": True, "error": str(e)[:2000], "steps": boot.steps, "at": now()}
                self._set_state(m, "failed")
                raise
            info["started_at"] = now()
            m["boot"] = info
            m["timings"]["boot_secs"] = info["secs"]
            fp = read_json(Path(m["env2_root"]) / ".runtime" / "arena-fingerprint.json")
            if fp:
                write_json(self.idir(instance_id) / "evidence" / "arena-fingerprint.json", fp)
        self._set_state(m, "running")
        self._runtime_record(m)
        write_json(self.idir(instance_id) / "evidence" / ("boot-%s.json" % stamp()), {"steps": boot.steps, "boot": m.get("boot", {}).get("boot_id")})
        return m

    def _runtime_record(self, m):
        body = {"kind": "RuntimeInstance", "schema_version": "m9.1", "instance_id": m["instance_id"], "architecture_snapshot_id": m["architecture_snapshot_id"],
                "recipe_set_id": m["recipe_set_id"], "profile": m["profile"], "trust_path": m.get("trust_path", "substitute"), "profile_digest": m["profile_digest"], "seed": m["seed"], "seed_epoch": m["seed_epoch"],
                "backend": {"kind": m["backend"]["kind"], "isolation_boundary": m["backend"]["isolation_boundary"], "profile": m["backend"]["profile"],
                            "daemon_id": (m["backend"].get("identity") or {}).get("daemon_id")},
                "inputs_hash": m["inputs_hash"], "rendered_config_hash": m.get("rendered_config_hash"), "secrets_manifest_digest": m.get("secrets_manifest_digest"),
                "image_digests": m.get("image_digests"), "compose_project": m["compose_project"], "arena_suffix": m["arena_suffix"],
                "networks": [m["arena_network"], m["ingress_network"]] + ([m["s2p_network"]] if self.profile(m)["s2p"] else []),
                "subnets": [m["arena_subnet"], m["ingress_subnet"]], "published_ports": {("edge-kong" if m.get("trust_path") == "real" else "kong-lite"): m["kong_host_port"]},
                "boot_id": (m.get("boot") or {}).get("boot_id"), "arena_tag": m["arena_tag"], "s2p_image": m.get("s2p_image"),
                "source_checkout_head": m.get("source_checkout_head")}
        rid = content_id(body)
        m["runtime_instance_id"] = rid
        write_json(self.idir(m["instance_id"]) / "runtime_instance.json", {"runtime_instance_id": rid, "body": body, "recorded_at": now()})
        self.save_manifest(m)
        return rid

    # ---- status / health ----
    def containers(self, m):
        env = self.denv(m)
        r = sh(["docker", "ps", "-a", "--filter", "label=com.docker.compose.project=%s" % m["compose_project"],
                "--format", "{{.Names}}\t{{.Status}}\t{{.Label \"com.docker.compose.service\"}}"], env=env, check=False, timeout=60)
        out = []
        for line in (r["stdout"] or "").splitlines():
            name, _, rest = line.partition("\t")
            status, _, svc = rest.partition("\t")
            has_hc = "(health" in status
            out.append({"name": name, "service": svc, "status": status, "running": status.startswith("Up"),
                        "healthy": ("(healthy)" in status) or (status.startswith("Up") and not has_hc)})
        return sorted(out, key=lambda c: c["name"])

    def status(self, instance_id):
        m = self.manifest(instance_id)
        bk = self.backend(m)
        st = bk.status(m)
        out = {"instance_id": m["instance_id"], "state": m["state"], "profile": m["profile"], "architecture_snapshot_id": m["architecture_snapshot_id"],
               "runtime_instance_id": m.get("runtime_instance_id"), "backend": st, "compose_project": m["compose_project"], "kong_host_port": m["kong_host_port"],
               "exec_dir": m["exec_dir"], "at": now()}
        if (st.get("identity") or {}).get("reachable"):
            cs = self.containers(m)
            out["containers"] = {"total": len(cs), "running": sum(1 for c in cs if c["running"]), "healthy": sum(1 for c in cs if c["healthy"]),
                                 "unhealthy": [c["service"] for c in cs if c["running"] and not c["healthy"]], "not_running": [c["service"] for c in cs if not c["running"]]}
            out["bridge"] = self.bridge_probe(m)
        return out

    def bridge_probe(self, m):
        import urllib.request
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open("http://127.0.0.1:%d/_bridge/health" % m["kong_host_port"], timeout=3) as r:
                return {"ok": True, "port": m["kong_host_port"], "body": json.loads(r.read(500))}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "port": m["kong_host_port"], "error": str(e)[:200]}

    def health(self, instance_id):
        s = self.status(instance_id)
        c = s.get("containers") or {}
        s["healthy"] = bool(c) and not c.get("unhealthy") and c.get("running", 0) > 0 and (s.get("bridge") or {}).get("ok", False)
        return s

    # ---- reset: mutable state back to seeded fixtures (arena reset semantics + ingress reset + fresh S2P) ----
    def reset(self, instance_id):
        m = self.manifest(instance_id)
        prof = self.profile(m)
        boot = self.boot(m, prof)
        t0 = time.time()
        rep = {"instance_id": instance_id, "started_at": now(), "steps": []}
        if "redis" in prof["services"]:
            boot.compose("reset-redis-flush", "exec", "-T", "redis", "redis-cli", "FLUSHALL", profiles=("datastores",), check=False)
        if "localstack" in prof["services"]:
            r = boot.compose("reset-localstack-list", "exec", "-T", "localstack", "sh", "-c", "awslocal sqs list-queues --output json 2>/dev/null", profiles=("datastores",), check=False)
            try:
                urls = json.loads(r["stdout"] or "{}").get("QueueUrls", [])
            except ValueError:
                urls = []
            for u in urls:
                boot.compose("reset-purge", "exec", "-T", "localstack", "awslocal", "sqs", "purge-queue", "--queue-url", u, profiles=("datastores",), check=False)
            rep["queues_purged"] = len(urls)
        boot.seed()
        if "api-ingress" in prof["services"]:
            admin = "admin:" + boot.secret("ingress_admin_token")
            r = boot.curl("reset-ingress", "-u", admin, "-X", "POST", "-H", "Content-Type: application/json", "-d", "{}", "http://api-ingress:8080/_ingress/reset")
            rep["ingress_reset"] = '"reset": true' in (r["stdout"] or "") or '"reset":true' in (r["stdout"] or "")
        if prof["s2p"]:
            names = ["%s-%s-1" % (m["compose_project"], s) for s in ("s2p-vp-source", "s2p-kafka", "s2p-mysql", "s2p-redis")]
            boot.step("reset-s2p-rm", ["docker", "rm", "-f", *names], check=False)
            boot.step("reset-s2p-volume", ["docker", "volume", "rm", "-f", "rzp-arena-s2p-mysql-data" + m["arena_suffix"]], check=False)
            boot.s2p()
        if "cron-driver" in prof["services"] and "payouts-api" in prof["services"]:
            boot.compose("reset-reservation-reconcile", "exec", "-T", "cron-driver", "python3", "/app/driver.py", "--once", "reservation_reconcile", profiles=("substitutes",), check=False)
        rep["steps"] = boot.steps
        rep["secs"] = round(time.time() - t0, 1)
        rep["finished_at"] = now()
        m.setdefault("timings", {})["reset_secs"] = rep["secs"]
        m["resets"] = m.get("resets", 0) + 1
        self.save_manifest(m)
        self._event(m, "reset", secs=rep["secs"])
        write_json(self.idir(instance_id) / "evidence" / ("reset-%s.json" % stamp()), rep)
        return rep

    # ---- datastore state snapshot / restore (plain dumps inside the instance directory) ----
    def snapshot_state(self, instance_id, label=None):
        m = self.manifest(instance_id)
        prof = self.profile(m)
        boot = self.boot(m, prof)
        label = label or stamp()
        out = self.idir(instance_id) / "state" / label
        out.mkdir(parents=True, exist_ok=True)
        os.chmod(out, 0o700)
        t0 = time.time()
        files = {}
        for ds, (kind, db, pw) in DUMPS.items():
            if ds not in prof["services"]:
                continue
            if kind == "mysql":
                r = boot.compose("dump-" + ds, "exec", "-T", ds, "mysqldump", "-uroot", "-p" + boot.secret(pw), db, profiles=("datastores",), timeout=600)
                (out / (ds + ".sql")).write_text(r["stdout"]); files[ds] = ds + ".sql"
            elif kind == "psql":
                r = boot.compose("dump-" + ds, "exec", "-T", "-e", "PGPASSWORD=" + boot.secret(pw), ds, "pg_dump", "-U", "ledger", "-d", "ledger", "--clean", "--if-exists", profiles=("datastores",), timeout=600)
                (out / (ds + ".sql")).write_text(r["stdout"]); files[ds] = ds + ".sql"
            elif kind == "mongo":
                cid = boot.container_id(ds)
                sh(["docker", "exec", cid, "mongodump", "--username", "cfa_root", "--password", boot.secret(pw), "--authenticationDatabase", "admin", "--db", "cfa", "--archive=/tmp/cfa.archive"], env=boot.env(), timeout=600)
                sh(["docker", "cp", cid + ":/tmp/cfa.archive", str(out / (ds + ".archive"))], env=boot.env(), timeout=600)
                files[ds] = ds + ".archive"
        for f in out.iterdir():
            os.chmod(f, 0o600)
        rec = {"label": label, "instance_id": instance_id, "files": files, "sha256": {k: sha256_file(out / v) for k, v in files.items()}, "secs": round(time.time() - t0, 1), "at": now()}
        write_json(out / "STATE.json", rec)
        m.setdefault("timings", {})["snapshot_state_secs"] = rec["secs"]
        self.save_manifest(m)
        self._event(m, "snapshot_state", label=label, secs=rec["secs"])
        return rec

    def restore_state(self, instance_id, label):
        m = self.manifest(instance_id)
        prof = self.profile(m)
        boot = self.boot(m, prof)
        src = self.idir(instance_id) / "state" / label
        rec = read_json(src / "STATE.json")
        if not rec:
            raise KeyError("no state snapshot %r for %s" % (label, instance_id))
        t0 = time.time()
        for ds, fname in rec["files"].items():
            kind, db, pw = DUMPS[ds]
            data = (src / fname)
            if kind == "mysql":
                boot.compose("restore-" + ds, "exec", "-T", ds, "mysql", "-uroot", "-p" + boot.secret(pw), db, input=data.read_text(), profiles=("datastores",), timeout=600)
            elif kind == "psql":
                boot.compose("restore-" + ds, "exec", "-T", "-e", "PGPASSWORD=" + boot.secret(pw), ds, "psql", "-q", "-U", "ledger", "-d", "ledger", input=data.read_text(), profiles=("datastores",), timeout=600)
            elif kind == "mongo":
                cid = boot.container_id(ds)
                sh(["docker", "cp", str(data), cid + ":/tmp/restore.archive"], env=boot.env(), timeout=600)
                sh(["docker", "exec", cid, "mongorestore", "--username", "cfa_root", "--password", boot.secret(pw), "--authenticationDatabase", "admin", "--db", "cfa", "--archive=/tmp/restore.archive", "--drop"], env=boot.env(), timeout=600)
        out = {"label": label, "restored": sorted(rec["files"]), "secs": round(time.time() - t0, 1), "at": now()}
        m.setdefault("timings", {})["restore_state_secs"] = out["secs"]
        self.save_manifest(m)
        self._event(m, "restore_state", label=label, secs=out["secs"])
        return out

    # ---- stop / destroy ----
    def stop(self, instance_id, boundary=False):
        m = self.manifest(instance_id)
        prof = self.profile(m)
        boot = self.boot(m, prof)
        t0 = time.time()
        boot.bridge("stop")
        boot.compose("stop-arena", "stop", s2p=prof["s2p"], check=False, timeout=600)
        rep = {"stopped_at": now(), "secs": round(time.time() - t0, 1), "steps": boot.steps}
        if boundary:
            rep["boundary"] = self.backend(m).stop(m)
        m.setdefault("timings", {})["stop_secs"] = rep["secs"]
        self._set_state(m, "stopped")
        self._event(m, "stop", secs=rep["secs"], boundary=boundary)
        return rep

    def destroy(self, instance_id, purge=False, keep_boundary=False):
        m = self.manifest(instance_id)
        prof = self.profile(m)
        bk = self.backend(m)
        t0 = time.time()
        rep = {"instance_id": instance_id, "started_at": now()}
        reachable = (bk.status(m).get("identity") or {}).get("reachable")
        if reachable:
            boot = self.boot(m, prof)
            boot.bridge("stop")
            boot.compose("destroy-down", "down", "-v", "--remove-orphans", s2p=prof["s2p"], check=False, timeout=900)
            env = boot.env()
            for kind in ("volume", "network"):
                r = sh(["docker", kind, "ls", "--format", "{{.Name}}"], env=env, check=False, timeout=60)
                for n in (r["stdout"] or "").split():
                    if n.endswith(m["arena_suffix"]) or n.startswith(m["compose_project"] + "_"):
                        sh(["docker", kind, "rm", "-f" if kind == "volume" else n] + ([n] if kind == "volume" else []), env=env, check=False, timeout=120)
            left = self.containers(m)
            rep["containers_left"] = [c["name"] for c in left]
            rep["steps"] = boot.steps
        else:
            rep["note"] = "boundary not reachable; skipping in-daemon cleanup"
        if not keep_boundary:
            rep["boundary"] = bk.destroy(m)
        rep["secs"] = round(time.time() - t0, 1)
        m.setdefault("timings", {})["destroy_secs"] = rep["secs"]
        m["state"] = "destroyed"
        m["destroyed_at"] = now()
        write_json(self.idir(instance_id) / "manifest.json", m, mode=0o600)
        self.registry.remove(instance_id)
        self._event(m, "destroy", secs=rep["secs"], purge=purge)
        write_json(self.idir(instance_id) / "evidence" / ("destroy-%s.json" % stamp()), rep)
        if purge:
            for d in ("ENV2_COMPOSE", "inputs", "DOMAIN_REPLICAS", "state"):
                shutil.rmtree(self.idir(instance_id) / d, ignore_errors=True)
            rep["purged"] = ["ENV2_COMPOSE", "inputs", "DOMAIN_REPLICAS", "state"]
        return rep

    # ---- journeys ----
    def journeys(self, instance_id, only=None, family=None, timeout=7200):
        from .journeys import run_journeys
        m = self.manifest(instance_id)
        return run_journeys(self, m, only=only, family=family, timeout=timeout)

    # ---- reproduce: a new instance from an existing manifest (same snapshot, recipes, profile, seed, backend, images) ----
    def reproduce(self, instance_id, new_id, backend_name=None):
        src = self.manifest(instance_id)
        m = self.create(new_id, src["profile"], src["seed"], backend_name or src["backend"]["kind"], snapshot_id=src["architecture_snapshot_id"],
                        sizing=src.get("sizing"), backend_options=src["backend"].get("options"), s2p_image=src.get("s2p_image"), trust_path=src.get("trust_path", "substitute"))
        m["reproduced_from"] = {"instance_id": src["instance_id"], "runtime_instance_id": src.get("runtime_instance_id"), "inputs_hash": src["inputs_hash"]}
        self.save_manifest(m)
        return m

    def list(self):
        return self.registry.records()
