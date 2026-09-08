"""Small shared helpers: subprocess wrapper with timing, hashing, canonical JSON (delegates to archkit.canon), time."""
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from archkit.canon import canonical_bytes, content_id, sha256_file  # noqa: E402,F401


class CommandError(RuntimeError):
    def __init__(self, rec):
        self.rec = rec
        super().__init__("%s rc=%s\n%s" % (rec["cmd"], rec["rc"], (rec.get("stderr_tail") or "")[-1500:]))


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def stamp():
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def sha256_text(s):
    return sha256_bytes(s.encode())


def read_json(p, default=None):
    p = Path(p)
    if not p.is_file():
        return default
    return json.loads(p.read_text())


def write_json(p, obj, mode=None):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, sort_keys=True, default=str) + "\n")
    if mode is not None:
        os.chmod(tmp, mode)
    tmp.replace(p)
    return p


def append_jsonl(p, obj):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as f:
        f.write(json.dumps(obj, sort_keys=True, default=str) + "\n")


def sh(cmd, cwd=None, env=None, check=True, timeout=1800, log=None, input=None, quiet_env=("DOCKER_HOST",)):
    """Run a command, return a record {cmd, rc, secs, stdout, stderr}. Never logs secret-bearing environment."""
    e = dict(os.environ)
    if env:
        e.update({k: str(v) for k, v in env.items()})
    t0 = time.time()
    try:
        p = subprocess.run(cmd, cwd=str(cwd) if cwd else None, env=e, capture_output=True, text=True, timeout=timeout, input=input)
        rc, out, err = p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired as ex:
        rc, out, err = 124, (ex.stdout or b"").decode() if isinstance(ex.stdout, bytes) else (ex.stdout or ""), "timeout after %ss" % timeout
    rec = {"cmd": cmd if isinstance(cmd, str) else " ".join(str(c) for c in cmd), "rc": rc, "secs": round(time.time() - t0, 2),
           "stdout": out, "stderr": err, "stdout_tail": out[-3000:], "stderr_tail": err[-3000:], "at": now()}
    if log is not None:
        log.append({k: v for k, v in rec.items() if k not in ("stdout", "stderr")})
    if check and rc != 0:
        raise CommandError(rec)
    return rec


def hash_tree(root, include=None, exclude_dirs=("__pycache__", ".git"), exclude_names=(".DS_Store",), suffixes=None):
    """sha256 per file (relative posix path -> hash) for a directory tree; deterministic ordering."""
    root = Path(root)
    out = {}
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.is_symlink():
            continue
        rp = p.relative_to(root)
        if any(part in exclude_dirs for part in rp.parts) or p.name in exclude_names or p.suffix in (".pyc", ".pyo"):
            continue
        if suffixes and p.suffix not in suffixes:
            continue
        if include and not any(rp.as_posix() == i or rp.as_posix().startswith(i + "/") for i in include):
            continue
        out[rp.as_posix()] = sha256_file(p)
    return out


def digest_of(mapping):
    return sha256_bytes(json.dumps(dict(sorted(mapping.items())), sort_keys=True, separators=(",", ":")).encode())


def sanitize_id(s):
    import re
    s = re.sub(r"[^a-z0-9-]", "-", str(s).lower())
    s = re.sub(r"-+", "-", s).strip("-")
    if not s or len(s) > 40:
        raise ValueError("instance id must sanitize to 1..40 chars of [a-z0-9-]: %r" % s)
    return s
