"""Timing / memory measurements for the report (no docker)."""
import json
import resource
import time
from . import paths
from .query import Query
from .store import SnapshotStore


def _rss_mb():
    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024 / 1024, 1)


def _t(fn, n=1):
    t = time.perf_counter()
    r = None
    for _ in range(n):
        r = fn()
    return round((time.perf_counter() - t) * 1000 / n, 3), r


def run(store=None, sid=None):
    st = store if isinstance(store, SnapshotStore) else SnapshotStore(store)
    out = {"rss_mb_start": _rss_mb()}
    ms, _ = _t(lambda: st.load(sid))
    out["snapshot_load_and_verify_ms"] = ms
    sid = st.resolve(sid)
    out["snapshot_id"] = sid
    out["snapshot_bytes"] = (st.root / sid / "snapshot.json").stat().st_size
    ms, q = _t(lambda: Query(st, sid))
    out["index_build_ms"] = ms
    out["rss_mb_after_index"] = _rss_mb()
    cases = {
        "node": lambda: q.node("svc:payouts-api"),
        "search": lambda: q.search("fund account", limit=20),
        "service_card": lambda: q.service_card("svc:payouts-api"),
        "neighbors_d2": lambda: q.neighbors("svc:fts-web", depth=2, limit=100),
        "shortest_path": lambda: q.shortest_path("identity:merchant-api-key", "table:payouts/payouts"),
        "paths_h6": lambda: q.paths("sub:api-ingress", "svc:fts-web", max_hops=6, limit=10),
        "identity_reachability": lambda: q.identity_reachability("identity:ingress-app-secret"),
        "data_flow": lambda: q.data_flow("table:payouts/payouts"),
        "fidelity_gaps": lambda: q.fidelity_gaps(),
        "uncovered_trust_boundaries": lambda: q.uncovered_trust_boundaries(),
        "affected": lambda: q.affected(nodes="sub:api-ingress"),
        "context_packet": lambda: q.context_packet("svc:payouts-api", budget=6000),
        "compare_self": lambda: q.compare(sid),
    }
    out["queries_ms"] = {}
    for name, fn in cases.items():
        ms, _ = _t(fn, n=20 if name not in ("compare_self", "affected") else 3)
        out["queries_ms"][name] = ms
    out["detail_namespace_loaded_after_canonical_queries"] = st.s2p_detail_loads > 0
    ms, _ = _t(lambda: q.s2p_detail(prefix="vp.", limit=5))
    out["s2p_detail_first_call_ms"] = ms
    ms, _ = _t(lambda: q.s2p_detail(prefix="vp.", limit=5), n=10)
    out["s2p_detail_warm_ms"] = ms
    out["rss_mb_after_s2p_detail"] = _rss_mb()
    return out
