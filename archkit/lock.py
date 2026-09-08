"""SourceLock: the pinned source set an ArchitectureSnapshot was compiled against.

Built ONLY from committed inputs so a clean checkout reproduces it byte-for-byte:
  * DOMAIN_REPLICAS/source_to_pay/source-lock.json      (9 repos: sha, tree, remote, role)   -- the model
  * reports/domain/REPOSITORY_INVENTORY_M6.csv           (38 reachable repos: sha, origin, role)
  * repo:* nodes of the functional graph                 (sha as seen by the discovery lanes)
No clone is read; `clone_path` columns are deliberately dropped (host-specific).
"""
import csv
import json
from . import paths
from .canon import content_id


def _short(sha):
    return (sha or "")[:40]


def build_source_lock(graph_nodes) -> dict:
    repos = {}
    inv = paths.DOMAIN / "REPOSITORY_INVENTORY_M6.csv"
    if inv.is_file():
        for row in csv.DictReader(inv.open()):
            if row.get("kind") != "repository":
                continue
            name = row["repository"].split("/")[-1]
            repos[name.lower()] = {"name": name, "sha": _short(row.get("sha")), "remote": row.get("origin") or None,
                                   "role": (row.get("role_in_payouts") or "")[:200], "language": row.get("language") or None,
                                   "access": row.get("access") or None, "integrity": row.get("integrity") or None,
                                   "sources": ["reports/domain/REPOSITORY_INVENTORY_M6.csv"]}
    if paths.S2P_LOCK.is_file():
        s2p = json.loads(paths.S2P_LOCK.read_text())
        for r in s2p.get("repositories", []):
            key = r["name"].lower()
            cur = repos.setdefault(key, {"name": r["name"], "sha": None, "remote": None, "role": None, "sources": []})
            if cur.get("sha") and r.get("sha") and not r["sha"].startswith(cur["sha"]) and not cur["sha"].startswith(r["sha"]):
                cur.setdefault("conflicts", []).append({"source": "DOMAIN_REPLICAS/source_to_pay/source-lock.json", "sha": r["sha"]})
            else:
                cur["sha"] = r.get("sha") if len(r.get("sha") or "") >= len(cur.get("sha") or "") else cur["sha"]
            cur["tree"] = r.get("tree")
            cur["remote"] = cur.get("remote") or r.get("remote")
            cur["s2p_role"] = r.get("role")
            cur["tracked_files"] = r.get("tracked_files")
            cur["sources"].append("DOMAIN_REPLICAS/source_to_pay/source-lock.json")
    for n in graph_nodes:
        if n.get("kind") != "repository":
            continue
        name = n["id"].split(":", 1)[1]
        key = name.lower()
        cur = repos.setdefault(key, {"name": name, "sha": None, "remote": None, "role": None, "sources": []})
        sha = n.get("sha")
        if sha:
            if cur.get("sha") and not (cur["sha"].startswith(sha) or sha.startswith(cur["sha"])):
                cur.setdefault("conflicts", []).append({"source": "graph:" + n["id"], "sha": sha})
            elif len(sha) > len(cur.get("sha") or ""):
                cur["sha"] = sha
        cur["graph_node"] = n["id"]
        cur["graph_fidelity"] = n.get("fidelity")
        if "graph:" + n["id"] not in cur["sources"]:
            cur["sources"].append("graph:" + n["id"])
    for cur in repos.values():
        cur["sources"] = sorted(set(cur["sources"]))
        if not cur.get("sha"):
            cur["sha"] = None
            cur["sha_status"] = "UNKNOWN: no committed record carries this repository's commit"
    body = {"kind": "source_lock", "schema_version": "m8.1", "repositories": dict(sorted(repos.items()))}
    return {"source_lock_id": content_id(body), **body}


def readable_repo_names(lock: dict) -> set:
    """Repositories whose source was readable when the lanes ran (used instead of build_graph._reachable_repos,
    which inspects .local/repos-root on the host)."""
    out = set()
    for key, r in lock["repositories"].items():
        if r.get("sha") and (r.get("access") or "").startswith("cloned"):
            out.add(key)
        elif r.get("sha") and r.get("s2p_role"):
            out.add(key)
    return out
