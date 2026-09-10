"""Bounded, role-specific context packets sourced from the M8 archkit world model.

Two consumers:

* the Campaign Director reads a broad *world map* (families, uncovered trust
  boundaries, fidelity gaps, high-value journeys, identity reach) to invent its
  own theses — it is never handed a target;
* a worker reads a *tight* packet scoped to one thesis subject (archkit's own
  bounded ``context_packet`` plus a couple of targeted slices), so it sees only
  what its task needs.

Everything is derived from the immutable snapshot pinned in the manifest. The
``query`` object is injected (the live archkit ``Query`` or a test stub), so no
consumer here imports archkit directly or needs a snapshot store to exist.
"""


def _items(env):
    return (env or {}).get("items", []) if isinstance(env, dict) else (env or [])


def _extra(env):
    return (env or {}).get("extra", {}) if isinstance(env, dict) else {}


class WorldModel:
    """Read-only façade over an archkit Query for the control plane."""

    def __init__(self, query):
        self.q = query

    def snapshot_id(self):
        try:
            return self.q.snapshot()["items"][0].get("snapshot_id") or self.q.sid
        except Exception:  # noqa: BLE001
            return getattr(self.q, "sid", None)

    # -- Director world map ---------------------------------------------------
    def director_world(self, mode="broad_autonomous", limit=40):
        """A compact, deterministic map the Director reasons over to make theses.
        Pure archkit facts; no targets, classes or paths are prescribed."""
        world = {"snapshot_id": self.snapshot_id(), "mode": mode}
        world["families"] = _items(self._safe("families"))
        utb = self._safe("uncovered_trust_boundaries")
        world["trust_boundaries"] = _items(utb)
        world["uncovered_trust_boundaries"] = _extra(utb).get("uncovered", [])
        fg = self._safe("fidelity_gaps", population="p0_critical_kinds", limit=limit)
        world["fidelity_gaps"] = _items(fg)[:limit]
        world["fidelity_gap_summary"] = _extra(fg)
        # a spread of high-criticality journeys as behavioural anchors
        js = _items(self._safe("journeys", limit=300))
        world["journeys"] = [j for j in js if (j.get("priority") in ("P0", "P1"))][:limit] or js[:limit]
        world["unknowns"] = _items(self._safe("unknowns"))[:limit]
        return world

    def thesis_subjects(self, mode="broad_autonomous", cap=24):
        """Candidate subjects (node/family ids) worth a thesis, ranked by a
        transparent heuristic over the world map. The Director may keep, drop or
        add to these; it does not receive an attack for any of them."""
        w = self.director_world(mode=mode)
        ranked = []
        for tb in w["trust_boundaries"]:
            score = 5 if not tb.get("covered") else 2
            for r in tb.get("uncovered_routes", []):
                ranked.append((score + 2, r, "uncovered_route", tb.get("trust_boundary")))
            ranked.append((score, tb.get("trust_boundary"), "trust_boundary", None))
        for g in w["fidelity_gaps"]:
            ranked.append((3, g.get("id"), "fidelity_gap", g.get("m8_label")))
        for f in w["families"]:
            pr = 4 if f.get("priority") == "P0" else 2
            ranked.append((pr, f.get("id"), "family", f.get("label")))
        # dedup by subject id keeping the highest score, stable order
        best = {}
        for score, sid, why, ctx in ranked:
            if not sid:
                continue
            if sid not in best or score > best[sid][0]:
                best[sid] = (score, why, ctx)
        out = [{"subject": sid, "score": v[0], "reason": v[1], "context": v[2]}
               for sid, v in best.items()]
        out.sort(key=lambda r: (-r["score"], r["subject"]))
        return out[:cap]

    # -- Worker packet --------------------------------------------------------
    def worker_packet(self, subject, budget=6000, role="explorer"):
        """A bounded packet for a worker assigned to ``subject``."""
        try:
            pkt = self.q.context_packet(subject, budget=budget)["items"][0]
        except Exception as e:  # noqa: BLE001
            pkt = {"subject": subject, "sections": {}, "error": str(e)[:200], "truncated": False}
        pkt["role"] = role
        return pkt

    def subject_exists(self, subject):
        try:
            self.q.node(subject)
            return True
        except Exception:  # noqa: BLE001
            try:
                self.q.family(subject)
                return True
            except Exception:  # noqa: BLE001
                return False

    def _safe(self, method, **kw):
        try:
            return getattr(self.q, method)(**kw)
        except Exception:  # noqa: BLE001
            return {"items": [], "extra": {}}
