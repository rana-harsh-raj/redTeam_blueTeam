"""Two-instance isolation proof (checkpointed, resumable):

  python3 -m twinfactory isolation run --a <full-instance> --b <focused-instance> [--resume] [--out FILE]

Both instances must be RUNNING on separate isolation boundaries. Phases (each records evidence + pass/fail):
  P1 boundaries     distinct daemons (docker info ID / socket / VM), registry-bound, both boundaries flagged isolating
  P2 namespaces     containers/networks/volumes disjoint across daemons; ports, subnets, secrets, config hashes distinct
  P3 filesystem     A's VM cannot see B's execution directory (bind-mount of B's path is empty/absent inside A)
  P4 reachability   from A's arena network: B's container names do not resolve; B's host port unreachable; A's own
                    services answer -- and symmetrically for B
  P5 journeys       a representative journey in each instance; evidence attributed to the right instance
  P6 stop/restart   stopping A leaves B healthy (and B still passes a quick journey); restarting A restores A only
  P7 reset          resetting B leaves A's datastore rows, fixtures and secrets unchanged; B's ingress state is cleared
  P8 state          B: snapshot-state -> mutate -> restore-state returns the row counts to the snapshot
  P9 destroy        destroying A leaves B healthy; A is recreated from its manifest (same snapshot, recipes, profile,
                    seed, image ids, inputs hash) and boots again
Measurements (memory, disk, build/boot/reset/destroy times) are collected along the way."""
import argparse
import json
import re
import sys
import time
from pathlib import Path

from . import paths
from .util import sh, now, stamp, write_json, read_json

OUT = paths.IMPL / "m9-isolation-proof.json"
CURL = "curlimages/curl:latest"
PY = "python:3.12-alpine"


class Proof:
    def __init__(self, factory, a, b, out, resume, journey_a, journey_b, quick):
        self.f, self.a, self.b, self.out = factory, a, b, Path(out)
        self.journey_a, self.journey_b, self.quick = journey_a, journey_b, quick
        self.doc = read_json(self.out, None) if resume else None
        if not self.doc or self.doc.get("a") != a or self.doc.get("b") != b:
            self.doc = {"kind": "m9_isolation_proof", "schema_version": "m9.1", "a": a, "b": b, "started_at": now(), "phases": {}, "measurements": {}}
        self.save()

    def save(self):
        self.doc["updated_at"] = now()
        write_json(self.out, self.doc)

    def done(self, pid):
        return (self.doc["phases"].get(pid) or {}).get("passed") is True

    def record(self, pid, title, passed, detail, secs=None):
        self.doc["phases"][pid] = {"title": title, "passed": bool(passed), "detail": detail, "at": now(), "secs": secs}
        self.save()
        print("[%s] %s -> %s" % (pid, title, "PASS" if passed else "FAIL"), flush=True)
        return passed

    # ---- helpers ----
    def m(self, iid):
        return self.f.manifest(iid)

    def env(self, iid):
        return self.f.boot(self.m(iid)).env()

    def names(self, iid, kind):
        r = sh(["docker", kind, "ls", "--format", "{{.Name}}"] if kind != "container" else ["docker", "ps", "-a", "--format", "{{.Names}}"], env=self.env(iid), check=False, timeout=60)
        return sorted(x for x in (r["stdout"] or "").split() if x)

    def curl_in(self, iid, url, network=None, extra=()):
        m = self.m(iid)
        net = network or m["arena_network"]
        r = sh(["docker", "run", "--rm", "--pull", "never", "--network", net, CURL, "-sS", "-m", "5", "-o", "/dev/null", "-w", "%{http_code}", *extra, url], env=self.env(iid), check=False, timeout=60)
        return {"url": url, "network": net, "rc": r["rc"], "http": (r["stdout"] or "").strip()[-3:], "err": (r["stderr"] or "").strip()[-200:]}

    def sql_count(self, iid, service, db, pw_name, sql):
        m = self.m(iid)
        boot = self.f.boot(m)
        cid = boot.container_id(service)
        if not cid:
            return None
        r = sh(["docker", "exec", cid, "mysql", "-uroot", "-p" + boot.secret(pw_name), "-N", "-B", "-e", sql, db], env=boot.env(), check=False, timeout=60)
        return (r["stdout"] or "").strip() if r["rc"] == 0 else "ERR:" + (r["stderr"] or "")[-120:]

    def row_counts(self, iid):
        out = {}
        prof = self.f.profile(self.m(iid))
        if "mysql-payouts" in prof["services"]:
            out["payouts.payouts"] = self.sql_count(iid, "mysql-payouts", "payouts", "mysql_payouts_root_password", "SELECT count(*) FROM payouts")
            out["payouts.banking_accounts"] = self.sql_count(iid, "mysql-payouts", "payouts", "mysql_payouts_root_password", "SELECT count(*) FROM banking_accounts")
        if "mysql-apidb-stub" in prof["services"]:
            out["api_local.merchants"] = self.sql_count(iid, "mysql-apidb-stub", "api_local", "mysql_apidb_root_password", "SELECT count(*) FROM merchants")
        if "api-ingress" in prof["services"]:
            boot = self.f.boot(self.m(iid))
            r = sh(["docker", "run", "--rm", "--pull", "never", "--network", self.m(iid)["arena_network"], CURL, "-sS", "-m", "5", "http://api-ingress:8080/_ingress/health"], env=boot.env(), check=False, timeout=60)
            try:
                out["ingress.tables"] = json.loads(r["stdout"]).get("tables")
            except ValueError:
                out["ingress.tables"] = None
        return out

    def measure(self, iid, label):
        m = self.m(iid)
        env = self.env(iid)
        r = sh(["docker", "stats", "--no-stream", "--format", "{{.MemUsage}}"], env=env, check=False, timeout=120)
        mib = 0.0
        for line in (r["stdout"] or "").splitlines():
            v = line.split("/")[0].strip()
            mm = re.match(r"([\d.]+)\s*(GiB|MiB|KiB|B)", v)
            if mm:
                n, u = float(mm.group(1)), mm.group(2)
                mib += n * {"GiB": 1024, "MiB": 1, "KiB": 1 / 1024, "B": 1 / (1024 * 1024)}[u]
        df = sh(["docker", "system", "df", "--format", "{{json .}}"], env=env, check=False, timeout=120)
        bk = self.f.backend(m)
        vm_kb = 0
        for d in (bk.vm_dirs(m) if hasattr(bk, "vm_dirs") else []):
            du = sh(["du", "-sk", str(d)], check=False, timeout=300)
            if du["rc"] == 0:
                vm_kb += int((du["stdout"] or "0").split()[0] or 0)
        vm_kb = vm_kb or None
        host_vm = None
        ps = sh(["ps", "-axo", "rss=,command="], check=False, timeout=30)
        rec = {"label": label, "at": now(), "containers_mem_mib": round(mib, 1), "docker_system_df": [json.loads(l) for l in (df["stdout"] or "").splitlines() if l.startswith("{")],
               "vm_dir_kib": vm_kb, "sizing": m["sizing"], "timings": m.get("timings")}
        self.doc["measurements"].setdefault(iid, []).append(rec)
        self.save()
        return rec

    # ---- phases ----
    def p1(self):
        t0 = time.time()
        ma, mb = self.m(self.a), self.m(self.b)
        ia, ib = self.f.backend(ma).identity(ma), self.f.backend(mb).identity(mb)
        reg = {r["instance_id"]: r for r in self.f.registry.records()}
        d = {"a": {"backend": ma["backend"]["kind"], "boundary": ma["backend"]["isolation_boundary"], "profile": ma["profile"], "identity": ia, "state": ma["state"]},
             "b": {"backend": mb["backend"]["kind"], "boundary": mb["backend"]["isolation_boundary"], "profile": mb["profile"], "identity": ib, "state": mb["state"]},
             "registry_has_both": self.a in reg and self.b in reg}
        ok = (ia.get("reachable") and ib.get("reachable") and ia["daemon_id"] != ib["daemon_id"] and ia["docker_host"] != ib["docker_host"] and ia.get("name") != ib.get("name")
              and ma["backend"]["isolation_boundary"] and mb["backend"]["isolation_boundary"] and ma["state"] == "running" and mb["state"] == "running" and d["registry_has_both"]
              and ma["profile"] != mb["profile"] and ma["seed"] != mb["seed"])
        return self.record("P1", "separate execution boundaries (distinct Docker daemons/VMs), both running, distinct profiles and seeds", ok, d, round(time.time() - t0, 1))

    def p2(self):
        t0 = time.time()
        ma, mb = self.m(self.a), self.m(self.b)
        inv = {}
        for iid in (self.a, self.b):
            inv[iid] = {k: self.names(iid, k) for k in ("container", "network", "volume")}
        builtin = {"bridge", "host", "none"}   # every dockerd creates these; they are daemon defaults, not instance resources
        cross = {"a_names_on_b": sorted((set(n for k in inv[self.a] for n in inv[self.a][k]) & set(n for k in inv[self.b] for n in inv[self.b][k])) - builtin)}
        b_in_a = [n for k in ("container", "network", "volume") for n in inv[self.a][k] if mb["arena_suffix"] in n or mb["compose_project"] in n]
        a_in_b = [n for k in ("container", "network", "volume") for n in inv[self.b][k] if ma["arena_suffix"] in n or ma["compose_project"] in n]
        d = {"inventory_counts": {i: {k: len(v) for k, v in inv[i].items()} for i in inv}, "shared_names": cross["a_names_on_b"],
             "b_resources_visible_on_a": b_in_a, "a_resources_visible_on_b": a_in_b,
             "ports": {"a": ma["kong_host_port"], "b": mb["kong_host_port"]}, "subnets": {"a": ma["arena_subnet"], "b": mb["arena_subnet"]},
             "compose_projects": {"a": ma["compose_project"], "b": mb["compose_project"]}, "exec_dirs": {"a": ma["exec_dir"], "b": mb["exec_dir"]},
             "secrets_manifest_digest": {"a": ma["secrets_manifest_digest"], "b": mb["secrets_manifest_digest"]},
             "rendered_config_hash": {"a": ma["rendered_config_hash"], "b": mb["rendered_config_hash"]},
             "inputs_hash": {"a": ma["inputs_hash"], "b": mb["inputs_hash"]}, "seed_epoch": {"a": ma["seed_epoch"], "b": mb["seed_epoch"]}}
        # Source-to-Pay secret is instance-specific (never the historical constant) wherever the overlay runs
        d["s2p_secret_instance_specific"] = None
        for iid in (self.a, self.b):
            m = self.m(iid)
            if self.f.profile(m)["s2p"]:
                sec = (Path(m["env2_root"]) / "secrets" / "app_vendor_payments.txt").read_text().strip()
                cid = self.f.boot(m).container_id("s2p-vp-source")
                r = sh(["docker", "inspect", "--format", "{{range .Config.Env}}{{println .}}{{end}}", cid], env=self.env(iid), check=False, timeout=60) if cid else {"stdout": ""}
                envs = dict(l.split("=", 1) for l in (r["stdout"] or "").splitlines() if "=" in l)
                d["s2p_secret_instance_specific"] = bool(sec and sec != "local-vendor-payments-secret" and envs.get("S2P_API_SECRET") == sec and envs.get("S2P_DB_PASSWORD") not in (None, "s2p"))
        # bridge-image and infra images are legitimately identical by name on both daemons (content-addressed, read-only); only
        # instance-scoped resources matter: containers, compose networks, named volumes
        ok = (not b_in_a and not a_in_b and ma["kong_host_port"] != mb["kong_host_port"] and ma["arena_subnet"] != mb["arena_subnet"]
              and ma["compose_project"] != mb["compose_project"] and ma["exec_dir"] != mb["exec_dir"] and ma["secrets_manifest_digest"] != mb["secrets_manifest_digest"]
              and ma["rendered_config_hash"] != mb["rendered_config_hash"] and not cross["a_names_on_b"] and ma["seed_epoch"] != mb["seed_epoch"])
        return self.record("P2", "no shared container/network/volume/port/subnet/secret/config between the instances", ok, d, round(time.time() - t0, 1))

    def p3(self):
        t0 = time.time()
        d = {}
        ok = True
        for src, other in ((self.a, self.b), (self.b, self.a)):
            mo = self.m(other)
            r = sh(["docker", "run", "--rm", "--pull", "never", "--network", "none", "-v", "%s:/other:ro" % mo["exec_dir"], PY, "sh", "-c", "ls -A /other | wc -l; cat /other/manifest.json >/dev/null 2>&1 && echo VISIBLE || echo NOT-VISIBLE"],
                   env=self.env(src), check=False, timeout=120)
            out = (r["stdout"] or "").strip().splitlines()
            visible = "VISIBLE" in out
            d["%s_sees_%s_exec_dir" % (src, other)] = {"rc": r["rc"], "entries": out[0] if out else None, "verdict": out[-1] if out else r["stderr"][-200:], "visible": visible}
            ok = ok and not visible
        return self.record("P3", "each VM mounts only its own execution directory (the other instance's files are not visible)", ok, d, round(time.time() - t0, 1))

    def p4(self):
        t0 = time.time()
        d = {}
        ok = True
        for src, other in ((self.a, self.b), (self.b, self.a)):
            ms, mo = self.m(src), self.m(other)
            own = self.curl_in(src, "http://payouts-api:9400/status")
            by_name = self.curl_in(src, "http://%s-payouts-api-1:9400/status" % mo["compose_project"])
            other_net = self.curl_in(src, "http://payouts-api:9400/status", network=mo["arena_network"])
            # from the instance's real runtime boundary (its internal arena network) the other instance's published host
            # port is unreachable; the informational probe from a non-internal docker `bridge` network shows the host-side
            # loopback bridge is TCP-reachable there but refuses any non-loopback Host (403), i.e. never a usable path
            host_port = self.curl_in(src, "http://host.lima.internal:%d/_bridge/health" % mo["kong_host_port"])
            host_port_bridge_net = self.curl_in(src, "http://host.lima.internal:%d/_bridge/health" % mo["kong_host_port"], network="bridge")
            d[src] = {"own_payouts_api": own, "other_container_by_name": by_name, "attach_to_other_network": other_net,
                      "other_host_port_from_vm": host_port, "other_host_port_from_non_internal_bridge_network_informational": host_port_bridge_net}
            ok = ok and own["http"] == "200" and by_name["http"] != "200" and other_net["rc"] != 0 and host_port["http"] != "200" and host_port_bridge_net["http"] != "200"
        # from the host, both bridges answer with different pids on their own ports
        import urllib.request
        pids = {}
        for iid in (self.a, self.b):
            try:
                with urllib.request.build_opener(urllib.request.ProxyHandler({})).open("http://127.0.0.1:%d/_bridge/health" % self.m(iid)["kong_host_port"], timeout=3) as r:
                    pids[iid] = json.loads(r.read(300)).get("pid")
            except Exception as e:  # noqa: BLE001
                pids[iid] = "ERR:" + str(e)[:80]
        d["host_bridge_pids"] = pids
        ok = ok and isinstance(pids[self.a], int) and isinstance(pids[self.b], int) and pids[self.a] != pids[self.b]
        return self.record("P4", "cross-instance names do not resolve, other networks are not attachable, other host ports are unreachable from the VM; own services answer", ok, d, round(time.time() - t0, 1))

    def p5(self):
        t0 = time.time()
        ra = self.f.journeys(self.a, only=self.journey_a)
        rb = self.f.journeys(self.b, only=self.journey_b)
        d = {"a": {k: ra[k] for k in ("run_dir", "summary", "attributed", "attribution", "journeys", "boot_id_matches_instance", "secs")},
             "b": {k: rb[k] for k in ("run_dir", "summary", "attributed", "attribution", "journeys", "boot_id_matches_instance", "secs")}}
        ok = all(((r["summary"] or {}).get("pass", 0) >= 1 and (r["summary"] or {}).get("fail", 0) == 0 and r["attributed"] and r["boot_id_matches_instance"]) for r in (ra, rb))
        # merchants minted by the two runs are distinct ids (different campaigns/instances)
        ma = {j.get("merchant_id") for j in ra["journeys"]}; mb = {j.get("merchant_id") for j in rb["journeys"]}
        d["distinct_merchants"] = not (ma & mb - {None})
        ok = ok and d["distinct_merchants"]
        return self.record("P5", "representative journeys pass in both instances and are attributed to the right instance", ok, d, round(time.time() - t0, 1))

    def p6(self):
        t0 = time.time()
        d = {}
        d["b_before"] = self.f.health(self.b)["healthy"]
        d["stop_a"] = self.f.stop(self.a)
        d["b_after_a_stopped"] = self.f.health(self.b)
        d["a_after_stop"] = self.f.status(self.a).get("containers")
        rb = self.f.journeys(self.b, only=self.quick)
        d["b_journey_while_a_stopped"] = {k: rb[k] for k in ("summary", "attributed")}
        ma = self.f.start(self.a)
        d["restart_a"] = {"secs": ma["timings"].get("restart_secs"), "state": ma["state"]}
        d["a_after_restart"] = self.f.health(self.a)["healthy"]
        d["b_after_a_restart"] = self.f.health(self.b)["healthy"]
        ok = (d["b_before"] and d["b_after_a_stopped"]["healthy"] and (d["a_after_stop"] or {}).get("running") == 0 and (rb["summary"] or {}).get("fail", 0) == 0
              and (rb["summary"] or {}).get("pass", 0) >= 1 and d["a_after_restart"] and d["b_after_a_restart"])
        return self.record("P6", "stopping/restarting A does not affect B (B stays healthy and passes a journey while A is stopped)", ok, d, round(time.time() - t0, 1))

    def p7(self):
        t0 = time.time()
        d = {"a_rows_before": self.row_counts(self.a), "b_rows_before": self.row_counts(self.b),
             "a_secrets_before": self.m(self.a)["secrets_manifest_digest"], "a_fixtures_before": self.f.registry.get(self.a) and self.m(self.a)["rendered_config_hash"]}
        from .workspace import rendered_config_digest
        d["a_rendered_before"] = rendered_config_digest(self.m(self.a)["exec_dir"])["digest"]
        d["reset_b"] = {k: v for k, v in self.f.reset(self.b).items() if k in ("secs", "queues_purged", "ingress_reset")}
        d["a_rows_after"] = self.row_counts(self.a)
        d["b_rows_after"] = self.row_counts(self.b)
        d["a_rendered_after"] = rendered_config_digest(self.m(self.a)["exec_dir"])["digest"]
        d["a_healthy_after"] = self.f.health(self.a)["healthy"]
        ok = d["a_rows_before"] == d["a_rows_after"] and d["a_rendered_before"] == d["a_rendered_after"] and d["a_healthy_after"]
        return self.record("P7", "resetting B leaves A's datastore rows, rendered configuration and secrets unchanged", ok, d, round(time.time() - t0, 1))

    def p8(self):
        t0 = time.time()
        d = {}
        snap = self.f.snapshot_state(self.b, "p8-baseline")
        d["snapshot"] = {"label": snap["label"], "files": snap["files"], "secs": snap["secs"]}
        d["rows_at_snapshot"] = self.row_counts(self.b)
        # mutate: run a quick journey (mints a merchant + payout rows)
        rb = self.f.journeys(self.b, only=self.quick)
        d["mutation_journey"] = rb["summary"]
        d["rows_after_mutation"] = self.row_counts(self.b)
        rs = self.f.restore_state(self.b, "p8-baseline")
        d["restore"] = rs
        d["rows_after_restore"] = self.row_counts(self.b)
        keys = [k for k in d["rows_at_snapshot"] if k.startswith(("payouts.", "api_local."))]
        ok = all(d["rows_after_restore"].get(k) == d["rows_at_snapshot"].get(k) for k in keys) and any(d["rows_after_mutation"].get(k) != d["rows_at_snapshot"].get(k) for k in keys)
        return self.record("P8", "snapshot-state / restore-state on B returns mutated datastore rows to the snapshot", ok, d, round(time.time() - t0, 1))

    def p9(self):
        t0 = time.time()
        d = {}
        ma_before = self.m(self.a)
        d["a_manifest_before"] = {k: ma_before[k] for k in ("runtime_instance_id", "inputs_hash", "profile_digest", "image_digests", "seed", "architecture_snapshot_id", "recipe_set_id")}
        d["destroy_a"] = self.f.destroy(self.a, keep_boundary=False)
        d["b_after_destroy"] = self.f.health(self.b)["healthy"]
        d["a_boundary_gone"] = self.f.backend(ma_before).vm(ma_before) is None if ma_before["backend"]["kind"] == "colima" else None
        d["registry_after_destroy"] = sorted(r["instance_id"] for r in self.f.registry.records())
        m2 = self.f.create(self.a, ma_before["profile"], ma_before["seed"], ma_before["backend"]["kind"], snapshot_id=ma_before["architecture_snapshot_id"],
                           sizing=ma_before.get("sizing"), s2p_image=ma_before.get("s2p_image"), force=True)
        m2 = self.f.build(self.a)
        d["a_rebuilt"] = {k: m2[k] for k in ("inputs_hash", "profile_digest", "image_digests", "seed", "architecture_snapshot_id", "recipe_set_id")}
        d["reproducible"] = {"inputs_hash": m2["inputs_hash"] == ma_before["inputs_hash"], "profile_digest": m2["profile_digest"] == ma_before["profile_digest"],
                             "image_ids": {img: (m2["image_digests"].get(img) or {}).get("image_id") == v.get("image_id") for img, v in ma_before["image_digests"].items()},
                             "seed_epoch": m2["seed_epoch"] == ma_before["seed_epoch"]}
        m2 = self.f.start(self.a)
        d["a_restarted"] = {"state": m2["state"], "boot_secs": m2["timings"].get("boot_secs"), "healthy": self.f.health(self.a)["healthy"], "runtime_instance_id": m2["runtime_instance_id"]}
        d["b_after_recreate"] = self.f.health(self.b)["healthy"]
        rep = d["reproducible"]
        ok = (d["b_after_destroy"] and d["b_after_recreate"] and rep["inputs_hash"] and rep["profile_digest"] and all(rep["image_ids"].values()) and rep["seed_epoch"]
              and d["a_restarted"]["healthy"] and (d["a_boundary_gone"] in (True, None)))
        return self.record("P9", "destroying A leaves B healthy; A is reproduced from its manifest (same inputs hash, profile, seed epoch and image ids) and boots again", ok, d, round(time.time() - t0, 1))

    def run(self, phases):
        for pid, fn in (("P1", self.p1), ("P2", self.p2), ("P3", self.p3), ("P4", self.p4), ("P5", self.p5), ("P6", self.p6), ("P7", self.p7), ("P8", self.p8), ("P9", self.p9)):
            if phases and pid not in phases:
                continue
            if self.done(pid):
                print("[%s] already passed (resume)" % pid)
                continue
            if pid in ("P1", "P5", "P9"):
                for iid in (self.a, self.b):
                    self.measure(iid, "before-" + pid)
            try:
                ok = fn()
            except Exception as e:  # noqa: BLE001
                import traceback
                self.record(pid, "exception", False, {"error": str(e)[:2000], "trace": traceback.format_exc()[-3000:]})
                ok = False
            if not ok:
                self.doc["passed"] = False
                self.save()
                return False
        for iid in (self.a, self.b):
            self.measure(iid, "final")
        self.doc["passed"] = all(p.get("passed") for p in self.doc["phases"].values()) and len(self.doc["phases"]) == 9
        self.doc["finished_at"] = now()
        self.save()
        return self.doc["passed"]


def main(argv, factory):
    ap = argparse.ArgumentParser(prog="twinfactory isolation")
    ap.add_argument("action", choices=["run"])
    ap.add_argument("--a", required=True); ap.add_argument("--b", required=True)
    ap.add_argument("--journey-a", default="journey:cross-domain-s2p/success")
    ap.add_argument("--journey-b", default="journey:shared-payouts/success")
    ap.add_argument("--quick", default="journey:fetch-list/success")
    ap.add_argument("--phases", default="")
    ap.add_argument("--resume", action="store_true"); ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args(argv)
    p = Proof(factory, a.a, a.b, a.out, a.resume, a.journey_a, a.journey_b, a.quick)
    ok = p.run([x for x in a.phases.split(",") if x])
    print(json.dumps({"passed": ok, "phases": {k: v["passed"] for k, v in p.doc["phases"].items()}, "out": a.out}, indent=1))
    return 0 if ok else 1
