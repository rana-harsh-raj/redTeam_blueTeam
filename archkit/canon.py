"""Canonical JSON, content ids and the volatile-field scanner.

A snapshot body is identified by sha256 of its canonical encoding: sorted keys, no whitespace, ASCII escaped,
one trailing newline. The id is computed over the body WITHOUT the `snapshot_id` field and then written into
the file next to the body (`{"snapshot_id": ..., "body": {...}}`) so the id can always be recomputed.
"""
import hashlib
import json
import re

FORBIDDEN_PATTERNS = [
    (r"(?:202[5-9]|20[3-9]\d)-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", "iso-timestamp"),   # fixture examples dated before 2025 are documentation, not clocks
    (r"\b(?:202[5-9]|20[3-9]\d)\d{4}T\d{6}Z\b", "utc-stamp"),
    (r"/Users/[A-Za-z0-9_.-]+/", "home-path"),
    (r"/private/tmp/|(?<![\w-])/tmp/", "tmp-path"),
    (r"\bis Up \d+ (?:seconds?|minutes?|hours?|days?)", "docker-status"),
    (r"\bboot_id\b", "boot-id"),
    (r"\bcontainer_id\b", "container-id"),
]
FORBIDDEN_KEYS = {"generated_at", "captured_at", "started_at", "finished_at", "evaluated_at", "git_head",
                  "runtime", "result", "merchant_id", "evidence_path", "boot_id", "container_id"}


def canonical_bytes(obj) -> bytes:
    return (json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False) + "\n").encode()


def content_id(obj) -> str:
    return hashlib.sha256(canonical_bytes(obj)).hexdigest()


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def scan_volatile(obj, allow_keys=(), allow_patterns=()) -> list:
    """Return [(json_path, reason, excerpt)] for every volatile key or string in a static body."""
    hits = []
    allow_keys = set(allow_keys)
    pats = [(re.compile(p), name) for p, name in FORBIDDEN_PATTERNS if name not in allow_patterns]

    def walk(x, path):
        if isinstance(x, dict):
            for k, v in x.items():
                if k in FORBIDDEN_KEYS and k not in allow_keys:
                    hits.append((path + "/" + k, "forbidden-key:" + k, ""))
                walk(v, path + "/" + str(k))
        elif isinstance(x, list):
            for i, v in enumerate(x):
                walk(v, path + "[%d]" % i)
        elif isinstance(x, str):
            for rx, name in pats:
                m = rx.search(x)
                if m:
                    hits.append((path, name, x[max(0, m.start() - 20):m.end() + 20]))
                    break
    walk(obj, "")
    return hits


SCRATCH_PREFIX = re.compile(r"^/(?:private/)?tmp/[^ ]*?/(rzp-payouts-architecture|rzp-payouts-clones|repos)/")
HOME_PREFIX = re.compile(r"^/Users/[A-Za-z0-9_.-]+/(?:rzp-payouts-architecture|rzp-payouts-clones|\.rzp-architecture-replica/repos)/")


def normalize_ref(s: str) -> str:
    """Strip host-specific absolute prefixes from a source reference, keeping the repo-relative part."""
    s2 = SCRATCH_PREFIX.sub("", s)
    s2 = HOME_PREFIX.sub("", s2)
    return s2
