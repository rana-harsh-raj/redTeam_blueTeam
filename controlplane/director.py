"""Campaign Director: generates the campaign's own theses from the world model.

The human gives only a mode and a broad mandate. The Director reads the archkit
world map (families, uncovered trust boundaries, fidelity gaps, high-value
journeys) and proposes its own theses — a thesis is a self-generated *line of
inquiry* (a suspected weakness on a subject), never a prescribed exploit. Theses
are deduplicated by semantic fingerprint and stored durably. On later planning
cycles the Director retires exhausted theses, reprioritises by observed coverage,
and opens new ones on under-explored subjects.

It runs two ways with one record schema:
  * model-assisted — a director model proposes theses over the world map;
  * model-free — a transparent heuristic over ``WorldModel.thesis_subjects``.
The mechanics (dedup, priority, retirement, cycles) are identical, so the plane
is fully testable without a live model.
"""
import json

from red_loop.hypotheses import fingerprint

from .store import utc

_TEMPLATES = {
    "uncovered_route": "Route {s} crosses a trust boundary with no covering journey; a merchant identity may reach or influence it in a way the boundary intends to prevent.",
    "trust_boundary": "Trust boundary {s} may admit an action or read across tenants that its authorization model intends to deny.",
    "fidelity_gap": "Component {s} is modeled below full fidelity; its real authorization or accounting behaviour may diverge from what the twin enforces.",
    "family": "Within family {s}, an ordinary merchant may drive a state or accounting transition that violates an ownership, idempotency or zero-sum invariant.",
    "family_default": "Subject {s} may expose a money-movement or identity weakness reachable by an ordinary merchant.",
}


def _claim_for(sub):
    tmpl = _TEMPLATES.get(sub.get("reason"), _TEMPLATES["family_default"])
    return tmpl.format(s=sub["subject"])


class Director:
    def __init__(self, control, world, router, client_factory=None):
        self.control = control
        self.world = world
        self.router = router
        self.client_factory = client_factory

    # -- planning -------------------------------------------------------------
    def plan_cycle(self, manifest, max_theses=8):
        st = self.control.control_state()
        cycle = int(st.get("planning_cycles", 0)) + 1
        existing = self.control.fold("theses")
        covered = {t["subject"] for t in existing}
        subjects = [s for s in self.world.thesis_subjects(mode=manifest.get("mode"))
                    if s["subject"] not in covered]

        proposals = []
        if self.client_factory is not None:
            proposals = self._model_theses(manifest, subjects, max_theses)
        if not proposals:
            proposals = self._heuristic_theses(subjects, max_theses)

        new_ids = []
        for p in proposals:
            tid = self._propose_thesis(p, cycle)
            if tid:
                new_ids.append(tid)
        self.control.put("planning", {
            "planning_cycle": cycle, "new_theses": new_ids,
            "candidate_subjects": len(subjects), "world_snapshot": self.world.snapshot_id(),
            "mode": manifest.get("mode")})
        self.control.set_control_state(planning_cycles=cycle)
        self.control.event("planning_cycle", cycle=cycle, new_theses=len(new_ids))
        return new_ids

    def _propose_thesis(self, p, cycle):
        subject = p.get("subject")
        claim = (p.get("claim") or "").strip()
        if not subject or not claim:
            return None
        assets = p.get("target_assets") or [{"asset": subject, "kind": p.get("subject_kind", "node")}]
        fp = fingerprint(assets, claim)
        for t in self.control.fold("theses"):
            if t.get("fingerprint") == fp:
                self.control.event("thesis_duplicate_suppressed", fingerprint=fp, duplicate_of=t["thesis_id"])
                return None
        tid = self.control.next_id("theses", "TH")
        self.control.put("theses", {
            "thesis_id": tid, "subject": subject, "subject_kind": p.get("subject_kind", "node"),
            "claim": claim, "rationale": p.get("rationale"), "target_assets": assets,
            "suggested_probes": p.get("suggested_probes") or [], "fingerprint": fp,
            "priority": int(p.get("priority", p.get("score", 2))), "status": "open",
            "source": p.get("source", "director"), "planning_cycle": cycle,
            "created_at": utc(),
        })
        self.control.event("thesis_created", thesis_id=tid, subject=subject, priority=int(p.get("priority", 2)))
        return tid

    def _heuristic_theses(self, subjects, max_theses):
        out = []
        for s in subjects[:max_theses]:
            out.append({"subject": s["subject"], "subject_kind": s.get("context") or "node",
                        "claim": _claim_for(s), "rationale": "world-map heuristic: %s (score %d)" % (s["reason"], s["score"]),
                        "priority": s["score"], "score": s["score"],
                        "suggested_probes": [], "source": "director_heuristic"})
        return out

    def _model_theses(self, manifest, subjects, max_theses):
        try:
            world = self.world.director_world(mode=manifest.get("mode"))
            model = self.router.for_role("director")
            client = self.client_factory(model)
            prompt = _director_prompt(manifest, world, subjects, max_theses)
            resp = client.complete(
                [{"role": "system", "content": prompt},
                 {"role": "user", "content": "Propose theses now as a JSON array."}],
                tools=None)
            self.control.put("model_calls", {"role": "director", "model": model,
                             "finish_reason": resp.get("finish_reason"),
                             "usage": resp.get("raw_usage", {})})
            data = _parse_theses(resp.get("content") or "")
            for d in data:
                d.setdefault("source", "director_model")
            return data[:max_theses]
        except Exception as e:  # noqa: BLE001
            self.control.event("director_model_failed", detail=str(e)[:200])
            return []

    # -- maintenance ----------------------------------------------------------
    def update_theses(self, manifest):
        """Retire theses whose work is exhausted; reprioritise open ones by how
        little they have been explored (coverage-aware)."""
        theses = self.control.fold("theses")
        tasks = self.control.fold("tasks")
        by_thesis = {}
        for t in tasks:
            by_thesis.setdefault(t.get("thesis_id"), []).append(t)
        retired = []
        for th in theses:
            if th.get("status") in ("retired", "exhausted"):
                continue
            ts = by_thesis.get(th["thesis_id"], [])
            if ts and all(x.get("status") in ("done", "retired", "blocked", "failed") for x in ts):
                # exhausted only if every derived task reached a terminal state and
                # none is pending replication
                self.control.update("theses", th["thesis_id"], status="exhausted",
                                    status_reason="all_tasks_terminal")
                self.control.event("thesis_exhausted", thesis_id=th["thesis_id"])
                retired.append(th["thesis_id"])
        return retired


def _director_prompt(manifest, world, subjects, n):
    return ("\n".join([
        manifest["mandate"].strip(),
        "",
        "You are the Campaign Director. From the architecture world map below, "
        "propose up to %d diverse theses. A thesis names a SUBJECT (a node or "
        "family id from the map) and a suspected weakness reachable by an ordinary "
        "merchant. Do not prescribe a full exploit; state the suspicion and 1-3 "
        "probe ideas. Prefer breadth across families and boundaries." % n,
        "",
        "Return ONLY a JSON array of objects with keys: subject, subject_kind, "
        "claim, rationale, priority (1-4), suggested_probes (array of strings).",
        "",
        "World map (families / uncovered trust boundaries / fidelity gaps / journeys):",
        json.dumps({k: world[k] for k in ("families", "uncovered_trust_boundaries",
                    "trust_boundaries", "fidelity_gaps", "journeys") if k in world},
                   default=str)[:12000],
        "",
        "Ranked candidate subjects (hints, not a mandate):",
        json.dumps(subjects[: n * 2], default=str)[:3000],
    ]))


def _parse_theses(text):
    text = (text or "").strip()
    if "```" in text:
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
    start = text.find("[")
    end = text.rfind("]")
    if start >= 0 and end > start:
        try:
            data = json.loads(text[start:end + 1])
            return [d for d in data if isinstance(d, dict) and d.get("subject")]
        except ValueError:
            return []
    return []
