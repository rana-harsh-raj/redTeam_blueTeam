"""Service-set-aware boot orchestrator: the arena's proven boot order (ENV2_COMPOSE/scripts/up.sh, which stays
untouched for the M6/M7 path) ported step by step and gated by profile membership, so a focused profile boots only
what its derivation included. Every step is logged with timing to <instance>/logs/<nn>-<step>.log.

Order: seeds -> secrets -> config -> preflight -> materialize -> datastores -> migrations -> schema patches -> seeds
-> monolith db users -> substitutes -> core -> ledger accounting configs / DA parents -> reservation reconcile
-> host bridge -> fingerprint -> Source-to-Pay overlay."""
import base64
import json
import os
import re
import shutil
import time
from pathlib import Path

from . import paths
from .util import sh, CommandError, now, write_json, read_json

DATASTORES = ("mysql-payouts", "mysql-fts", "mysql-xbalances", "mysql-apidb-stub", "postgres-ledger", "mongo-cfa", "redis", "localstack", "kafka")
SUB_HEALTH = ("kong-lite", "api-ingress", "monolith-stub", "dcs-stub", "splitz-stub", "shield-stub", "pricing-stub", "asv-stub", "stork-capture",
              "merchant-webhook-sink", "xas-sim", "workflow-engine", "batch-sim", "bankingaccounts-stub", "ledger-gate", "workflow-sim", "xas-sink")
SEEDS = {  # datastore -> (generated seed file, loader)
    "mysql-payouts": ("seeds/generated/s4/payouts.sql", "mysql", "payouts", "mysql_payouts_root_password"),
    "mysql-fts": ("seeds/generated/s4/fts.sql", "mysql", "fts", "mysql_fts_root_password"),
    "mysql-xbalances": ("seeds/generated/s4/xbalances.sql", "mysql", "rx_balances_local", "mysql_xbalances_root_password"),
    "mysql-apidb-stub": ("seeds/generated/s4/apidb.sql", "mysql", "api_local", "mysql_apidb_root_password"),
    "postgres-ledger": ("seeds/generated/s4/ledger.sql", "psql", "ledger", "postgres_ledger_password"),
    "mongo-cfa": ("seeds/generated/s4/cfa.js", "mongo", "cfa", "mongo_cfa_root_password"),
}
SCHEMA_PATCHES = {"mysql-payouts": ("seeds/schema-patches/payouts.sql", "payouts", "mysql_payouts_root_password"),
                  "mysql-apidb-stub": ("seeds/schema-patches/apidb.sql", "api_local", "mysql_apidb_root_password")}
S2P_TOPICS = ("add-tds-entry", "prod.x.vendor-payments.accounting-payouts.status-update")
CURL = "curlimages/curl:latest"
MAX_CRASH_RESTARTS = 2
MINIMAL_ENV_KEYS = ("PATH", "HOME", "USER", "LANG", "LC_ALL", "TMPDIR", "SHELL", "COLIMA_HOME")


class Boot:
    def __init__(self, inst, profile, docker_env, log_dir, secrets_env=None):
        self.inst = inst
        self.profile = profile
        self.services = set(profile["services"])
        self.jobs = list(profile["jobs"])
        self.env2 = Path(inst["env2_root"])
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.docker_env = dict(docker_env)
        self.secrets_env = dict(secrets_env or {})
        self.steps = []
        self.crashes = []
        self.n = 0

    # ---- environment: curated (preflight scans os.environ; secrets only where compose interpolation needs them) ----
    def env(self, secrets=False, extra=None):
        e = {k: v for k, v in os.environ.items() if k in MINIMAL_ENV_KEYS}
        e.update(self.docker_env)
        e.update({"COMPOSE_PROJECT_NAME": self.inst["compose_project"], "ARENA_SUFFIX": self.inst["arena_suffix"], "ARENA_INSTANCE": self.inst["instance_id"],
                  "ARENA_SUBNET": self.inst["arena_subnet"], "INGRESS_SUBNET": self.inst["ingress_subnet"], "KONG_LITE_HOST_PORT": str(self.inst["kong_host_port"]),
                  "ARENA_TAG": self.inst["arena_tag"], "ARENA_WORKFLOW_HOST": "http://workflow-engine:8093", "ARENA_MOZART_IMPL": "mozart-sim",
                  "ARENA_ROUTE_PROFILE": "monolith", "ARENA_SEED_EPOCH": str(self.inst["seed_epoch"]), "S2P_SOURCE_IMAGE": self.inst.get("s2p_image") or "",
                  "PYTHONDONTWRITEBYTECODE": "1"})
        if secrets:
            e.update(self.secrets_env)
        if extra:
            e.update(extra)
        return e

    def compose_cmd(self, s2p=False, profiles=("datastores", "migrations", "substitutes", "core", "verify")):
        cmd = ["docker", "compose", "--env-file", ".env.arena", "-f", "docker-compose.yml"]
        if s2p:
            cmd += ["-f", "docker-compose.s2p.yml", "-f", "docker-compose.twin.yml"]
        for p in profiles:
            cmd += ["--profile", p]
        if s2p:
            cmd += ["--profile", "s2p"]
        return cmd

    # ---- step runner ----
    def step(self, name, cmd, cwd=None, env=None, timeout=1800, check=True, input=None, secrets=False, extra_env=None):
        self.n += 1
        e = env if env is not None else self.env(secrets=secrets, extra=extra_env)
        rec = sh(cmd, cwd=cwd or self.env2, env=e, check=False, timeout=timeout, input=input)
        log = self.log_dir / ("%02d-%s.log" % (self.n, re.sub(r"[^a-z0-9-]+", "-", name.lower())[:40]))
        log.write_text("$ %s\nrc=%s secs=%s at=%s\n--- stdout ---\n%s\n--- stderr ---\n%s\n" % (rec["cmd"], rec["rc"], rec["secs"], rec["at"], rec["stdout"], rec["stderr"]))
        srec = {"n": self.n, "step": name, "rc": rec["rc"], "secs": rec["secs"], "log": log.name, "at": rec["at"]}
        self.steps.append(srec)
        if check and rec["rc"] != 0:
            raise CommandError(rec)
        return rec

    def compose(self, name, *args, s2p=False, timeout=1800, check=True, input=None, profiles=None, retries=0):
        cmd = self.compose_cmd(s2p=s2p, profiles=profiles or ("datastores", "migrations", "substitutes", "core", "verify")) + list(args)
        rec = self.step(name, cmd, timeout=timeout, check=check and retries == 0, input=input, secrets=s2p)
        attempt = 0
        while rec["rc"] != 0 and attempt < retries:
            # a fresh dockerd occasionally races when many containers are created at once ("RWLayer ... unexpectedly nil");
            # remove the half-created containers of this project that are not running and try again
            attempt += 1
            time.sleep(5)
            r = sh(["docker", "ps", "-a", "--filter", "label=com.docker.compose.project=%s" % self.inst["compose_project"], "--filter", "status=created", "--filter", "status=exited", "-q"], env=self.env(), check=False, timeout=60)
            ids = [x for x in (r["stdout"] or "").split() if x]
            if ids:
                sh(["docker", "rm", "-f", *ids], env=self.env(), check=False, timeout=120)
            rec = self.step(name + "-retry%d" % attempt, cmd, timeout=timeout, check=check and attempt == retries, input=input, secrets=s2p)
        return rec

    def container_id(self, service):
        r = sh(["docker", "ps", "-a", "--no-trunc", "--filter", "label=com.docker.compose.project=%s" % self.inst["compose_project"],
                "--filter", "label=com.docker.compose.service=%s" % service, "--format", "{{.ID}}"], env=self.env(), check=False, timeout=60)
        ids = [x for x in r["stdout"].split() if x]
        return ids[0] if ids else None

    def wait_healthy(self, services, timeout=240, name="health"):
        t0 = time.time()
        pending = [s for s in services if s in self.services or s in self.jobs]
        results = {}
        restarts = {}
        while pending and time.time() - t0 < timeout:
            for s in list(pending):
                cid = self.container_id(s)
                if not cid:
                    continue
                r = sh(["docker", "inspect", "--format", "{{.State.Running}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}|{{.State.ExitCode}}|{{.State.Status}}", cid],
                       env=self.env(), check=False, timeout=60)
                running, health, exit_code, status = (r["stdout"].strip().split("|") + ["", "", "", ""])[:4]
                if health == "healthy" or (health == "none" and running == "true"):
                    results[s] = {"status": health if health != "none" else "running-no-healthcheck", "secs": round(time.time() - t0, 1), "restarts": restarts.get(s, 0)}
                    pending.remove(s)
                elif status == "exited" and exit_code not in ("", "0") and restarts.get(s, 0) < MAX_CRASH_RESTARTS:
                    # a real binary that crashed at start (e.g. x-balances worker: "concurrent map read and map write" race in
                    # pkg/worker/manager.go) is restarted a bounded number of times; the crash is recorded, never hidden
                    tail = sh(["docker", "logs", "--tail", "8", cid], env=self.env(), check=False, timeout=60)
                    restarts[s] = restarts.get(s, 0) + 1
                    self.crashes.append({"service": s, "exit_code": exit_code, "restart": restarts[s], "at": now(), "log_tail": ((tail["stdout"] or "") + (tail["stderr"] or ""))[-600:]})
                    sh(["docker", "start", cid], env=self.env(), check=False, timeout=120)
            if pending:
                time.sleep(2)
        self.n += 1
        srec = {"n": self.n, "step": name, "rc": 0 if not pending else 1, "secs": round(time.time() - t0, 1), "healthy": sorted(results), "unhealthy": sorted(pending),
                "crash_restarts": {k: v for k, v in restarts.items()}}
        self.steps.append(srec)
        if pending:
            logs = {}
            for s in pending[:5]:
                r = self.compose("logs-" + s, "logs", "--tail=60", s, check=False)
                logs[s] = r["stdout"][-2500:]
            raise RuntimeError("services not healthy within %ss: %s\n%s" % (timeout, ", ".join(pending), json.dumps(logs, indent=1)[:4000]))
        return results

    def secret(self, name):
        return (self.env2 / "secrets" / (name + ".txt")).read_text().strip()

    # ---- phases ----
    def generate(self, regen_secrets=True):
        repos_root = paths.LOCAL_REPOS if paths.LOCAL_REPOS.is_dir() else Path("/nonexistent")
        self.step("seeds-generate", ["python3", "seeds/generator/generate.py", "--epoch", str(self.inst["seed_epoch"]), "--repos-root", str(repos_root)])
        if regen_secrets or not (self.env2 / "secrets" / "postgres_ledger_password.txt").is_file():
            self.step("gen-secrets", ["bash", "secrets/gen-secrets.sh"])
            self.write_s2p_secrets()
        self.step("config-generate", ["python3", "config/generate.py"])
        self.step("preflight", ["python3", "preflight/preflight.py"])

    def write_s2p_secrets(self):
        """Per-instance Source-to-Pay credentials (M9): the driver reads them through docker-compose.twin.yml."""
        import secrets as _s
        p = self.env2 / "secrets" / ".env.s2p"
        vals = {"S2P_MYSQL_ROOT_PASSWORD": _s.token_urlsafe(24), "S2P_MYSQL_PASSWORD": _s.token_urlsafe(24), "S2P_API_SECRET": _s.token_urlsafe(24)}
        p.write_text("".join("%s=%s\n" % kv for kv in vals.items()))
        os.chmod(p, 0o600)
        # the shared ingress must recognise the same application secret (gen-secrets pins the historical constant)
        (self.env2 / "secrets" / "app_vendor_payments.txt").write_text(vals["S2P_API_SECRET"] + "\n")
        os.chmod(self.env2 / "secrets" / "app_vendor_payments.txt", 0o600)
        self.secrets_env = load_s2p_secrets(self.env2)

    def materialize(self):
        self.step("materialize", ["python3", "secrets/materialize.py"], timeout=600)

    def datastores(self):
        ds = [d for d in DATASTORES if d in self.services]
        if not ds:
            return
        self.compose("datastores-up", "up", "-d", "--no-build", *ds, profiles=("datastores",), retries=2)
        self.wait_healthy(ds, timeout=300, name="datastores-health")

    def migrations(self):
        for job in self.jobs:
            self.compose("migrate-" + job, "run", "--rm", "--no-deps", job, profiles=("datastores", "migrations"), timeout=900)
        for ds, (patch, db, pw) in SCHEMA_PATCHES.items():
            if ds in self.services:
                self.compose("schema-patch-" + ds, "exec", "-T", ds, "mysql", "-uroot", "-p" + self.secret(pw), db, input=(self.env2 / patch).read_text(), profiles=("datastores",))

    def seed(self):
        for ds, (f, kind, db, pw) in SEEDS.items():
            if ds not in self.services:
                continue
            data = (self.env2 / f).read_text()
            if kind == "mysql":
                self.compose("seed-" + ds, "exec", "-T", ds, "mysql", "-uroot", "-p" + self.secret(pw), db, input=data, profiles=("datastores",))
            elif kind == "psql":
                self.compose("seed-" + ds, "exec", "-T", "-e", "PGPASSWORD=" + self.secret(pw), ds, "psql", "-v", "ON_ERROR_STOP=1", "-q", "-U", "ledger", "-d", "ledger", input=data, profiles=("datastores",))
            elif kind == "mongo":
                self.compose("seed-" + ds, "exec", "-T", ds, "mongosh", "cfa", "--quiet", "--file", "/dev/stdin", "--username", "cfa_root", "--password", self.secret(pw),
                             "--authenticationDatabase", "admin", input=data, profiles=("datastores",))
        if "mysql-payouts" in self.services and "mysql-apidb-stub" in self.services:
            self.step("provision-monolith-db", ["bash", "scripts/provision-monolith-db.sh"])

    def substitutes(self):
        subs = sorted(s for s in self.services if self.profile["services"][s]["category"] == "substitute")
        first = [s for s in subs if s not in ("cron-driver", "mozart-sim")]
        if first:
            self.compose("substitutes-up", "up", "-d", "--no-build", *first, profiles=("datastores", "substitutes"), retries=2)
            self.wait_healthy([s for s in SUB_HEALTH if s in first], timeout=240, name="substitutes-health")
        if "mozart-sim" in subs:
            self.compose("mozart-sim-up", "up", "-d", "--no-build", "mozart-sim", profiles=("datastores", "substitutes"))
            self.wait_healthy(["mozart-sim"], timeout=120, name="mozart-health")
        if "cron-driver" in subs:
            self.compose("cron-driver-up", "up", "-d", "--no-build", "cron-driver", profiles=("datastores", "substitutes"))
        if "stork-capture" in subs:
            r = self.compose("stork-subscriptions", "logs", "stork-capture", check=False, profiles=("datastores", "substitutes"))
            self.steps[-1]["stork_loaded"] = bool(re.search(r"loaded \d+ webhook subscription", r["stdout"]))

    def core(self):
        core = sorted(s for s in self.services if self.profile["services"][s]["category"] == "core-real-binary")
        if not core:
            return
        self.compose("core-up", "up", "-d", "--no-build", *core, profiles=("datastores", "substitutes", "core"), timeout=900, retries=2)
        self.wait_healthy([s for s in core if s != "ledger-scheduler"], timeout=420, name="core-health")

    def curl(self, name, *args, network=None, check=False, timeout=60):
        net = network or self.inst["arena_network"]
        return self.step(name, ["docker", "run", "--rm", "--pull", "never", "--network", net, CURL, "-sS", "-m", "20", *args], timeout=timeout, check=check)

    def post_core(self):
        if "ledger-api" in self.services and "postgres-ledger" in self.services:
            auth = base64.b64encode(("payouts_key:" + self.secret("auth_payouts_ledger")).encode()).decode()
            hdr = ["-H", "Authorization: Basic " + auth, "-H", "Ledger-Tenant: X", "-H", "Content-Type: application/json"]
            for ident in ("shared_account_x", "direct_account_x"):
                self.curl("ledger-config-" + ident, "-o", "/dev/null", "-w", "%{http_code}", "-X", "POST", *hdr, "-d", json.dumps({"ledger_config_data_identifier": ident}),
                          "http://ledger-api:8080/twirp/rzp.ledger.ledger_config.v1.LedgerConfigAPI/CreateInBulk")
            self.curl("ledger-da-parents", "-o", "/dev/null", "-w", "%{http_code}", "-X", "POST", *hdr, "-d", '{"account_data_identifier":"direct_account_x"}',
                      "http://ledger-api:8080/twirp/rzp.ledger.account.v1.AccountAPI/CreateInBulk")
            pw = self.secret("postgres_ledger_password")
            q = ("SELECT a.id FROM accounts a JOIN account_details d ON d.account_id=a.id WHERE d.tenant='X' AND d.deleted_at IS NULL AND "
                 "(d.parent_account_id IS NULL OR d.parent_account_id='') AND d.account_name LIKE 'Direct %' AND a.status<>'ACTIVATED'")
            r = self.compose("ledger-da-inactive", "exec", "-T", "-e", "PGPASSWORD=" + pw, "postgres-ledger", "psql", "-U", "ledger", "-d", "ledger", "-tA", "-c", q, profiles=("datastores",))
            for acc in [x.strip() for x in r["stdout"].splitlines() if x.strip()]:
                self.curl("ledger-da-activate", "-o", "/dev/null", "-w", "%{http_code}", "-X", "POST", *hdr, "-d", json.dumps({"id": acc}),
                          "http://ledger-api:8080/twirp/rzp.ledger.account.v1.AccountAPI/Activate")
            r = self.compose("ledger-da-count", "exec", "-T", "-e", "PGPASSWORD=" + pw, "postgres-ledger", "psql", "-U", "ledger", "-d", "ledger", "-tA", "-c", q.replace("a.status<>'ACTIVATED'", "a.status='ACTIVATED'").replace("SELECT a.id", "SELECT count(*)"), profiles=("datastores",))
            self.steps[-1]["da_parents_activated"] = r["stdout"].strip()
        if "cron-driver" in self.services and "payouts-api" in self.services:
            self.compose("reservation-reconcile", "exec", "-T", "cron-driver", "python3", "/app/driver.py", "--once", "reservation_reconcile", profiles=("substitutes",), check=False)
            if "monolith-stub" in self.services:
                self.step("reservation-readiness", ["python3", "scripts/reservation-readiness.py"], check=False)

    def bridge(self, action="start"):
        if "kong-lite" not in self.services:
            return
        args = ["python3", "scripts/ingress.py", action]
        if action == "start":
            args += ["--port", str(self.inst["kong_host_port"])]
        self.step("host-bridge-" + action, args, check=(action == "start"))

    def fingerprint(self):
        self.step("fingerprint", ["python3", "scripts/fingerprint.py", "--project", self.inst["compose_project"]])
        fp = read_json(self.env2 / ".runtime" / "arena-fingerprint.json", {})
        return fp

    def s2p(self):
        if not self.profile["s2p"]:
            return
        if not self.inst.get("s2p_image"):
            raise RuntimeError("profile includes the Source-to-Pay overlay but no S2P runtime image is recorded for the instance")
        self.compose("s2p-ingress-attach", "up", "-d", "--no-build", "api-ingress", s2p=True)
        self.compose("s2p-datastores-up", "up", "-d", "--no-build", "--wait", "s2p-kafka", "s2p-mysql", "s2p-redis", s2p=True, timeout=600)
        kafka = self.container_id("s2p-kafka")
        for topic in S2P_TOPICS:
            r = self.step("s2p-topic-" + topic[:20], ["docker", "exec", kafka, "rpk", "topic", "describe", topic], check=False)
            if r["rc"] != 0:
                self.step("s2p-topic-create-" + topic[:20], ["docker", "exec", kafka, "rpk", "topic", "create", topic])
        self.compose("s2p-source-up", "up", "-d", "--no-build", "--wait", "s2p-vp-source", s2p=True, timeout=600)
        r = self.step("s2p-health", ["docker", "exec", self.container_id("s2p-vp-source"), "/service", "health"], check=False)
        self.steps[-1]["vp_source_health_rc"] = r["rc"]

    def full(self):
        """Boot a BUILT instance (seeds/secrets/config already generated by the build phase) from empty volumes."""
        t0 = time.time()
        self.materialize()
        self.datastores()
        self.migrations()
        self.seed()
        self.substitutes()
        self.core()
        self.post_core()
        self.bridge("start")
        fp = self.fingerprint()
        self.s2p()
        return {"boot_id": fp.get("boot_id"), "config_digest": fp.get("config_digest"), "secs": round(time.time() - t0, 1), "steps": self.steps,
                "crash_restarts": self.crashes, "finished_at": now()}


def load_s2p_secrets(env2):
    p = Path(env2) / "secrets" / ".env.s2p"
    out = {}
    if p.is_file():
        for line in p.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    return out
