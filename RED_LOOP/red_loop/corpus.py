"""Read-only code/architecture corpus for the primary red agent (Section 4).

INCLUDED: the admitted real-service source snapshots (payouts, fts, ledger,
cfa, x-balances) with their contracts (openapi/proto/DTO), migrations, state
machines and READMEs. This is the white/gray-box "approved code and
architecture" the agent audits.

EXCLUDED (never served): ENV2_COMPOSE (substitutes/control endpoints, verifier
answer keys, acceptance-gate internals, generated credentials, seeds/victim
fixtures), TWIN_SPEC/expected-failures.yaml, the reports/ tree (which names
already-discovered bugs), the RED_LOOP control plane and known-gap registry,
and AGENTS.md/CLAUDE.md agent-instruction files (prompt-injection surface).

All returned content is TARGET DATA, not instructions. The agent mandate says
so explicitly; callers should present results as untrusted data.
"""
import os
import re
from pathlib import Path

from . import config

ROOTS = {
    "payouts": config.ACCEPTED_SRC / "payouts",
    "fts": config.ACCEPTED_SRC / "fts",
    "ledger": config.ACCEPTED_SRC / "ledger",
    "cfa": config.ACCEPTED_SRC / "cfa",
    "x-balances": config.ACCEPTED_SRC / "x-balances",
}

DENY_BASENAMES = {"AGENTS.md", "CLAUDE.md"}
DENY_DIR_PARTS = {".git", "node_modules", "__pycache__", "vendor"}
MAX_READ_LINES = 400
MAX_READ_BYTES = 60000
MAX_SEARCH_RESULTS = 80


class CorpusError(Exception):
    pass


def _root_bases():
    return [r.resolve() for r in ROOTS.values() if r.exists()]


def _resolve(relpath):
    """Resolve a corpus-relative path (e.g. 'payouts/internal/app/x.go') to an
    absolute path guaranteed to live under one of the allowed roots."""
    if not relpath or relpath.startswith("/") or ".." in Path(relpath).parts:
        raise CorpusError("path must be corpus-relative and contain no '..'")
    parts = Path(relpath).parts
    top = parts[0]
    if top not in ROOTS:
        raise CorpusError("unknown corpus root %r (choose from %s)" % (top, sorted(ROOTS)))
    base = ROOTS[top].resolve()
    target = (base.parent / relpath).resolve()
    if base not in target.parents and target != base:
        raise CorpusError("path escapes corpus root")
    if target.name in DENY_BASENAMES:
        raise CorpusError("file is excluded from the corpus")
    if set(target.parts) & DENY_DIR_PARTS:
        raise CorpusError("path in an excluded directory")
    return target


def list_dir(relpath=""):
    """List entries. Empty relpath lists the corpus roots."""
    if not relpath:
        return {"roots": [{"name": k, "exists": v.exists()} for k, v in ROOTS.items()]}
    target = _resolve(relpath)
    if not target.exists():
        raise CorpusError("no such path")
    if target.is_file():
        return {"path": relpath, "is_file": True, "size": target.stat().st_size}
    entries = []
    for p in sorted(target.iterdir()):
        if p.name in DENY_BASENAMES or p.name in DENY_DIR_PARTS:
            continue
        entries.append({"name": p.name, "dir": p.is_dir(),
                        "size": (p.stat().st_size if p.is_file() else None)})
    return {"path": relpath, "entries": entries[:2000]}


def read_file(relpath, start=1, end=None):
    target = _resolve(relpath)
    if not target.exists() or not target.is_file():
        raise CorpusError("no such file")
    if target.stat().st_size > 4_000_000:
        raise CorpusError("file too large to read")
    text = target.read_text(errors="replace")
    lines = text.splitlines()
    start = max(1, int(start))
    if end is None:
        end = start + MAX_READ_LINES - 1
    end = min(int(end), start + MAX_READ_LINES - 1, len(lines))
    chunk = lines[start - 1:end]
    out = "\n".join("%d\t%s" % (start + i, ln) for i, ln in enumerate(chunk))
    if len(out) > MAX_READ_BYTES:
        out = out[:MAX_READ_BYTES] + "\n... [truncated]"
    return {"path": relpath, "start": start, "end": end, "total_lines": len(lines),
            "content": out}


def _iter_files(bases):
    for base in bases:
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in DENY_DIR_PARTS]
            for fn in filenames:
                if fn in DENY_BASENAMES:
                    continue
                yield Path(dirpath) / fn


_TEXT_EXT = {".go", ".sql", ".proto", ".yaml", ".yml", ".json", ".md", ".txt",
             ".py", ".sh", ".toml", ".tmpl", ".mod", ".sum", ".graphql", ".env"}


def search(query, path_glob=None, max_results=MAX_SEARCH_RESULTS, regex=False):
    """Pure-Python grep across the corpus roots (no external dependency).
    Returns file:line:text matches, capped. `query` is a literal substring
    unless regex=True."""
    if not query or len(query) > 500:
        raise CorpusError("query required (<=500 chars)")
    bases = _root_bases()
    if not bases:
        raise CorpusError("corpus roots missing")
    if regex:
        try:
            pat = re.compile(query)
        except re.error as e:
            raise CorpusError("bad regex: %s" % e)
        matcher = lambda ln: pat.search(ln) is not None
    else:
        q = query
        matcher = lambda ln: q in ln
    import fnmatch
    root_parent = config.ACCEPTED_SRC.resolve()
    results = []
    scanned = 0
    for fpath in _iter_files(bases):
        if len(results) >= int(max_results):
            break
        if fpath.suffix.lower() not in _TEXT_EXT and fpath.suffix != "":
            continue
        rel = str(fpath.resolve().relative_to(root_parent))
        if path_glob and not fnmatch.fnmatch(rel, str(path_glob)) and not fnmatch.fnmatch(fpath.name, str(path_glob)):
            continue
        try:
            if fpath.stat().st_size > 2_000_000:
                continue
            scanned += 1
            per_file = 0
            with open(fpath, "r", errors="replace") as f:
                for i, ln in enumerate(f, 1):
                    if matcher(ln):
                        results.append({"file": rel, "line": i, "text": ln.rstrip()[:400]})
                        per_file += 1
                        if per_file >= 8 or len(results) >= int(max_results):
                            break
        except (OSError, UnicodeDecodeError):
            continue
    return {"query": query, "count": len(results), "results": results,
            "files_scanned": scanned, "truncated": len(results) >= int(max_results)}


def find_definition(symbol):
    """Heuristic definition search for Go/SQL/proto symbols."""
    if not symbol or not symbol.replace("_", "").replace(".", "").isalnum():
        raise CorpusError("symbol must be alphanumeric/underscore")
    pat = (r"(func\s+(\([^)]*\)\s*)?%s\b|(type|const|var|message|service|rpc)\s+%s\b"
           r"|CREATE TABLE[^;]*\b%s\b|%s\s*(:?=|:))" % (symbol, symbol, symbol, symbol))
    return search(pat, max_results=40, regex=True)
