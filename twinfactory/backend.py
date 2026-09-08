"""Execution backends = isolation boundaries. The factory only ever talks to a backend through this interface:

    provision(inst)  -> ensure the boundary exists and its Docker daemon answers; return the boundary identity
    docker_env(inst) -> environment that points docker/compose at THIS instance's daemon (DOCKER_HOST)
    identity(inst)   -> daemon identity (docker info ID/Name/version/cpus/memory) used by the isolation proof
    stop/start       -> pause/resume the boundary itself (containers inside are handled by the lifecycle layer)
    destroy(inst)    -> remove the boundary and everything inside it

ColimaBackend: one colima profile (a separate Lima/vz virtual machine with its own dockerd) per instance. Only the
instance's execution directory is mounted into its VM, so no other instance's files (or the source checkout) are visible.
LocalDockerBackend: the caller's current daemon (development/tests only) -- reports isolation_boundary=False.
RemoteDockerBackend: an explicit DOCKER_HOST (ssh:// or tcp://) per instance -- interface-compatible, unexercised here."""
import json
import os
import shutil
import time
from pathlib import Path

from .util import sh, CommandError, now


class Backend:
    kind = "abstract"
    isolation_boundary = False

    def __init__(self, options=None):
        self.options = dict(options or {})

    def provision(self, inst):
        raise NotImplementedError

    def docker_env(self, inst):
        raise NotImplementedError

    def identity(self, inst):
        env = self.docker_env(inst)
        r = sh(["docker", "info", "--format", "{{json .}}"], env=env, check=False, timeout=60)
        if r["rc"] != 0:
            return {"reachable": False, "error": r["stderr_tail"][-300:]}
        info = json.loads(r["stdout"])
        return {"reachable": True, "daemon_id": info.get("ID"), "name": info.get("Name"), "server_version": info.get("ServerVersion"),
                "ncpu": info.get("NCPU"), "mem_total": info.get("MemTotal"), "os": info.get("OperatingSystem"), "kernel": info.get("KernelVersion"),
                "docker_root": info.get("DockerRootDir"), "docker_host": env.get("DOCKER_HOST")}

    def status(self, inst):
        return {"kind": self.kind, "identity": self.identity(inst)}

    def stop(self, inst):
        return {"stopped": False, "reason": "%s has no boundary to stop" % self.kind}

    def start(self, inst):
        return {"started": False, "reason": "%s has no boundary to start" % self.kind}

    def destroy(self, inst):
        return {"destroyed": False, "reason": "%s has no boundary to destroy" % self.kind}

    def describe(self, inst):
        return {"kind": self.kind, "isolation_boundary": self.isolation_boundary}


class LocalDockerBackend(Backend):
    kind = "local-docker"
    isolation_boundary = False

    def provision(self, inst):
        ident = self.identity(inst)
        if not ident.get("reachable"):
            raise RuntimeError("local docker daemon unreachable: %s" % ident.get("error"))
        return ident

    def docker_env(self, inst):
        env = {}
        if os.environ.get("DOCKER_HOST"):
            env["DOCKER_HOST"] = os.environ["DOCKER_HOST"]
        return env

    def describe(self, inst):
        return {"kind": self.kind, "isolation_boundary": False,
                "warning": "shares the caller's Docker daemon with every other project on it; NOT an isolation boundary"}


class RemoteDockerBackend(Backend):
    kind = "remote-docker"
    isolation_boundary = True

    def provision(self, inst):
        if not self.options.get("docker_host"):
            raise RuntimeError("remote-docker backend needs options.docker_host (ssh://user@host or tcp://host:2376)")
        ident = self.identity(inst)
        if not ident.get("reachable"):
            raise RuntimeError("remote daemon unreachable: %s" % ident.get("error"))
        return ident

    def docker_env(self, inst):
        return {"DOCKER_HOST": self.options["docker_host"]}

    def describe(self, inst):
        return {"kind": self.kind, "isolation_boundary": True, "docker_host": self.options.get("docker_host"),
                "note": "interface-compatible; not exercised in the M9 proof"}


class ColimaBackend(Backend):
    kind = "colima"
    isolation_boundary = True

    def __init__(self, options=None):
        super().__init__(options)
        self.colima = shutil.which("colima")
        self.home = Path(os.environ.get("COLIMA_HOME") or (Path.home() / ".colima"))

    def profile(self, inst):
        return inst["backend_profile"]

    def socket(self, inst):
        return self.home / self.profile(inst) / "docker.sock"

    def docker_env(self, inst):
        return {"DOCKER_HOST": "unix://" + str(self.socket(inst))}

    def _list(self):
        r = sh([self.colima, "list", "--json"], check=False, timeout=60)
        rows = []
        for line in (r["stdout"] or "").splitlines():
            line = line.strip()
            if line.startswith("{"):
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    pass
        return rows

    def vm(self, inst):
        for row in self._list():
            if row.get("name") == self.profile(inst):
                return row
        return None

    def provision(self, inst, log=None):
        if not self.colima:
            raise RuntimeError("colima not found on PATH")
        size = inst["sizing"]
        mount = Path(inst["exec_dir"])
        vm = self.vm(inst)
        if vm and vm.get("status") == "Running":
            ident = self.identity(inst)
            if ident.get("reachable"):
                return ident
        cmd = [self.colima, "start", "--profile", self.profile(inst), "--cpu", str(size["cpus"]), "--memory", str(size["memory_gib"]),
               "--disk", str(size["disk_gib"]), "--vm-type", self.options.get("vm_type", "vz"), "--mount-type", self.options.get("mount_type", "virtiofs"),
               "--runtime", "docker", "--activate=false", "--network-address=false", "--mount", "%s:w" % mount]
        t0 = time.time()
        sh(cmd, timeout=900, log=log)
        env = self.docker_env(inst)
        deadline = time.time() + 120
        while time.time() < deadline:
            r = sh(["docker", "info", "--format", "{{.ID}}"], env=env, check=False, timeout=30)
            if r["rc"] == 0:
                break
            time.sleep(2)
        ident = self.identity(inst)
        if not ident.get("reachable"):
            raise RuntimeError("colima profile %s started but its docker daemon does not answer" % self.profile(inst))
        ident["provision_secs"] = round(time.time() - t0, 1)
        return ident

    def status(self, inst):
        vm = self.vm(inst)
        return {"kind": self.kind, "profile": self.profile(inst), "vm": vm, "identity": self.identity(inst) if vm and vm.get("status") == "Running" else {"reachable": False}}

    def stop(self, inst, log=None):
        t0 = time.time()
        r = sh([self.colima, "stop", "--profile", self.profile(inst)], check=False, timeout=600, log=log)
        return {"stopped": r["rc"] == 0, "secs": round(time.time() - t0, 1), "rc": r["rc"]}

    def start(self, inst, log=None):
        t0 = time.time()
        r = sh([self.colima, "start", "--profile", self.profile(inst), "--activate=false"], check=False, timeout=900, log=log)
        env = self.docker_env(inst)
        deadline = time.time() + 120
        while time.time() < deadline:
            if sh(["docker", "info", "--format", "{{.ID}}"], env=env, check=False, timeout=30)["rc"] == 0:
                break
            time.sleep(2)
        return {"started": r["rc"] == 0 and self.identity(inst).get("reachable", False), "secs": round(time.time() - t0, 1), "rc": r["rc"]}

    def destroy(self, inst, log=None):
        t0 = time.time()
        if self.vm(inst) is None:
            return {"destroyed": True, "secs": 0.0, "note": "profile absent"}
        # --data: colima >= 0.9 keeps the container-runtime data disk (images, volumes, containers) across delete/start
        # unless asked to remove it; an instance boundary must take its data with it
        r = sh([self.colima, "delete", "--profile", self.profile(inst), "--force", "--data"], check=False, timeout=600, log=log)
        gone = self.vm(inst) is None
        left = [str(d) for d in self.vm_dirs(inst) if d.exists()]
        return {"destroyed": gone and not left, "secs": round(time.time() - t0, 1), "rc": r["rc"], "data_dirs_left": left}

    def vm_dirs(self, inst):
        """Lima instance directory + colima data disk directory of this profile (for disk measurements and cleanup checks)."""
        name = "colima-" + self.profile(inst)
        return [self.home / "_lima" / name, self.home / "_lima" / "_disks" / name]

    def describe(self, inst):
        return {"kind": self.kind, "isolation_boundary": True, "profile": self.profile(inst), "docker_host": self.docker_env(inst)["DOCKER_HOST"],
                "vm_type": self.options.get("vm_type", "vz"), "mount": inst.get("exec_dir"), "sizing": inst.get("sizing")}


BACKENDS = {"colima": ColimaBackend, "local-docker": LocalDockerBackend, "remote-docker": RemoteDockerBackend}


def get_backend(name, options=None):
    if name not in BACKENDS:
        raise KeyError("unknown backend %r (known: %s)" % (name, ", ".join(BACKENDS)))
    return BACKENDS[name](options)
