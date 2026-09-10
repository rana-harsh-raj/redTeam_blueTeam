"""OpenAI-function tool schemas for the primary red agent, and the dispatcher
that executes them against the broker (runtime), corpus (code/arch), and store
(campaign bookkeeping). Every call is recorded; the agent never touches the
judge, evidence store, control plane, or another tenant's data directly."""
import json

from . import corpus, llm

TOOLS = [
    # --- runtime (through kong-lite as the fixed attacker merchant) ---
    {"type": "function", "function": {
        "name": "merchant_request",
        "description": "Send an authenticated HTTP request AS YOUR merchant through the API gateway. "
                       "Your merchant credential is injected automatically; you cannot change identity. "
                       "path must be an absolute product path (e.g. /v1/payouts). Returns status, headers, body.",
        "parameters": {"type": "object", "properties": {
            "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"]},
            "path": {"type": "string"},
            "headers": {"type": "object", "description": "optional extra request headers"},
            "body": {"description": "optional JSON body (object) or string"}},
            "required": ["method", "path"]}}},
    {"type": "function", "function": {
        "name": "merchant_request_concurrent",
        "description": "Fire the same request COUNT times concurrently (for race/duplicate probing). Max 8.",
        "parameters": {"type": "object", "properties": {
            "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"]},
            "path": {"type": "string"}, "count": {"type": "integer"},
            "headers": {"type": "object"}, "body": {}},
            "required": ["method", "path", "count"]}}},
    {"type": "function", "function": {
        "name": "read_own_webhooks",
        "description": "Read webhook deliveries sent to YOUR merchant's webhook endpoint. Scoped to you only.",
        "parameters": {"type": "object", "properties": {
            "payout_id": {"type": "string", "description": "optional filter by payout id"},
            "since_ts": {"type": "number", "description": "optional unix ts floor"}}}}},
    {"type": "function", "function": {
        "name": "whoami",
        "description": "Return your own merchant identity (merchant_id, key_id, mode).",
        "parameters": {"type": "object", "properties": {}}}},
    # --- code / architecture (read-only, untrusted target data) ---
    {"type": "function", "function": {
        "name": "code_search",
        "description": "Search the approved service source (payouts, fts, ledger, cfa, x-balances) for a "
                       "literal substring (or regex if regex=true). Returns file:line matches.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"}, "path_glob": {"type": "string"},
            "regex": {"type": "boolean"}}, "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "code_read",
        "description": "Read a bounded range of a source file (corpus-relative path like payouts/internal/...).",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "start": {"type": "integer"}, "end": {"type": "integer"}},
            "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "code_list",
        "description": "List a directory in the source corpus (empty path lists the service roots).",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}},
    {"type": "function", "function": {
        "name": "code_find_symbol",
        "description": "Find likely definitions of a Go/SQL/proto symbol across the corpus.",
        "parameters": {"type": "object", "properties": {"symbol": {"type": "string"}},
                       "required": ["symbol"]}}},
    # --- campaign bookkeeping (authoritative record) ---
    {"type": "function", "function": {
        "name": "record_hypothesis",
        "description": "Record a hypothesis BEFORE a material experiment. Returns the hypothesis id.",
        "parameters": {"type": "object", "properties": {
            "claim": {"type": "string"}, "assets": {"type": "array", "items": {"type": "string"}},
            "suspected_weakness": {"type": "string"}, "expected_impact": {"type": "string"},
            "experiment": {"type": "string"}}, "required": ["claim"]}}},
    {"type": "function", "function": {
        "name": "update_hypothesis",
        "description": "Update a hypothesis status (proposed|testing|supported|refuted) with a note.",
        "parameters": {"type": "object", "properties": {
            "hypothesis_id": {"type": "string"}, "status": {"type": "string"}, "note": {"type": "string"}},
            "required": ["hypothesis_id", "status"]}}},
    {"type": "function", "function": {
        "name": "record_observation",
        "description": "Record an observation with the concrete deterministic facts you saw.",
        "parameters": {"type": "object", "properties": {
            "hypothesis_id": {"type": "string"}, "interpretation": {"type": "string"},
            "deterministic_facts": {"type": "object"}, "unlocked_capability": {"type": "string"}},
            "required": ["interpretation"]}}},
    {"type": "function", "function": {
        "name": "claim_candidate",
        "description": "Claim you have produced a real unauthorized/invariant-breaking EFFECT. Provide the "
                       "minimal exact steps and evidence. An independent judge decides; you get only a coarse outcome.",
        "parameters": {"type": "object", "properties": {
            "claimed_outcome": {"type": "string"},
            "minimal_steps": {"type": "array", "items": {"type": "string"}},
            "affected_assets": {"type": "array", "items": {"type": "string"}},
            "suspected_root_cause": {"type": "string"},
            "evidence_summary": {"type": "string"},
            "capabilities": {"type": "array", "items": {"type": "string"},
                             "description": "impact classes, e.g. cross_tenant_write, money_conservation"}},
            "required": ["claimed_outcome", "minimal_steps", "evidence_summary"]}}},
    {"type": "function", "function": {
        "name": "recall",
        "description": "Reopen your own older records that were compacted out of the visible context: "
                       "past hypotheses, observations, actions/requests, or candidates. Filter by kind "
                       "and optional id or keyword substring. Your compiled state shows only the recent "
                       "and active items; use this to retrieve anything older.",
        "parameters": {"type": "object", "properties": {
            "kind": {"type": "string", "enum": ["hypotheses", "observations", "actions", "candidates", "events"]},
            "id": {"type": "string", "description": "optional exact record id (e.g. H-003, O-0004, C-002)"},
            "keyword": {"type": "string", "description": "optional substring filter"},
            "limit": {"type": "integer"}}, "required": ["kind"]}}},
    {"type": "function", "function": {
        "name": "note",
        "description": "Write a short working note to your campaign scratchpad.",
        "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}}},
    {"type": "function", "function": {
        "name": "conclude",
        "description": "End the campaign when you judge you have exhausted productive avenues.",
        "parameters": {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]}}},
    # --- M4 lifecycle tools (additive; only active when a hyp_manager is wired) ---
    {"type": "function", "function": {
        "name": "define_experiment",
        "description": "Attach a bounded experiment to a hypothesis and advance it to experiment_defined. "
                       "Provide actions/shape, success_condition, stop_condition, max_actions, max_duration_s.",
        "parameters": {"type": "object", "properties": {
            "hypothesis_id": {"type": "string"},
            "experiment": {"type": "object"}, "success_condition": {"type": "string"},
            "stop_condition": {"type": "string"}, "max_actions": {"type": "integer"},
            "max_duration_s": {"type": "integer"}}, "required": ["hypothesis_id", "experiment"]}}},
    {"type": "function", "function": {
        "name": "mark_blocked",
        "description": "Record that a hypothesis is BLOCKED (distinct from falsified). Requires a blocker "
                       "from the fixed enum and a reason. A block is information, never proof of safety.",
        "parameters": {"type": "object", "properties": {
            "hypothesis_id": {"type": "string"},
            "blocker": {"type": "string", "enum": [
                "route_not_exposed", "required_actor_unavailable", "service_not_modeled",
                "fidelity_insufficient", "missing_event_consumer", "missing_tool_capability",
                "environment_failure", "time_budget_exhausted"]},
            "reason": {"type": "string"}, "next_best_action": {"type": "string"}},
            "required": ["hypothesis_id", "blocker", "reason"]}}},
    {"type": "function", "function": {
        "name": "request_replay",
        "description": "Request independent replay of a SUPPORTED hypothesis by a different provider from a "
                       "clean state. You cannot certify your own finding.",
        "parameters": {"type": "object", "properties": {
            "hypothesis_id": {"type": "string"}, "reason": {"type": "string"}},
            "required": ["hypothesis_id"]}}},
]


class Dispatcher:
    def __init__(self, broker, store, candidate_hook, hyp_manager=None,
                 lease_manager=None, allowed_tools=None, owner=None):
        self.broker = broker
        self.store = store
        self.candidate_hook = candidate_hook  # callable(candidate_record) -> coarse dict
        self.concluded = False
        self.conclude_summary = None
        self.candidate_count = 0
        # M4 additive wiring (all optional; None preserves legacy behavior)
        self.hyp_manager = hyp_manager
        self.lease_manager = lease_manager
        self.allowed_tools = set(allowed_tools) if allowed_tools is not None else None
        self.owner = owner

    def dispatch(self, name, args):
        if self.allowed_tools is not None and name not in self.allowed_tools:
            self.store.event("tool_blocked_by_context", tool=name, owner=self.owner)
            return {"blocked": True, "reason": "tool_not_in_context_allowlist",
                    "tool": name, "owner": self.owner}
        try:
            return self._dispatch(name, args)
        except Exception as e:  # noqa: BLE001 -- never crash the loop on a tool error
            return {"error": "tool_exception", "detail": str(e)[:400]}

    def _capture_response(self, path, result):
        """Persist an attacker-observed response body for the judge's canary scan."""
        if isinstance(result, dict) and "body" in result:
            self.store.add_response({"path": path, "status": result.get("status"),
                                     "fingerprint": result.get("fingerprint"),
                                     "body": (result.get("body") or "")[:8000]})

    def _dispatch(self, name, args):
        if name == "merchant_request":
            r = self.broker.request(args.get("method", "GET"), args.get("path", ""),
                                    headers=args.get("headers"), body=args.get("body"))
            self._capture_response(args.get("path", ""), r)
            return r
        if name == "merchant_request_concurrent":
            r = self.broker.concurrent(args.get("method", "GET"), args.get("path", ""),
                                       args.get("count", 2), headers=args.get("headers"),
                                       body=args.get("body"))
            for sub in (r.get("results") or []):
                self._capture_response(args.get("path", ""), sub)
            return r
        if name == "read_own_webhooks":
            return self.broker.read_own_webhooks(since_ts=args.get("since_ts"),
                                                 payout_id=args.get("payout_id"))
        if name == "whoami":
            return self.broker.attacker_identity()
        if name == "code_search":
            return corpus.search(args["query"], path_glob=args.get("path_glob"),
                                 regex=bool(args.get("regex")))
        if name == "code_read":
            return corpus.read_file(args["path"], args.get("start", 1), args.get("end"))
        if name == "code_list":
            return corpus.list_dir(args.get("path", ""))
        if name == "code_find_symbol":
            return corpus.find_definition(args["symbol"])
        if name == "record_hypothesis":
            h = self.store.add_hypothesis(
                args["claim"], assets=args.get("assets"), weakness=args.get("suspected_weakness"),
                expected_impact=args.get("expected_impact"), experiment=args.get("experiment"))
            out = {"hypothesis_id": h["hypothesis_id"]}
            # M4: mirror into the v2 lifecycle with duplicate suppression.
            if self.hyp_manager is not None:
                hid, dup = self.hyp_manager.propose(
                    args["claim"], target_assets=args.get("target_assets") or args.get("assets"),
                    suspected_cause=args.get("suspected_weakness"),
                    expected_observation=args.get("expected_impact"),
                    preconditions=args.get("preconditions"),
                    experiment=args.get("experiment") if isinstance(args.get("experiment"), dict) else None,
                    priority=args.get("priority"), owner=self.owner)
                out["v2_hypothesis_id"] = hid
                out["duplicate_suppressed"] = dup
            return out
        if name == "update_hypothesis":
            self.store.update_hypothesis(args["hypothesis_id"], status=args.get("status"),
                                         note=args.get("note"))
            return {"ok": True}
        if name == "record_observation":
            o = self.store.add_observation(
                hypothesis_id=args.get("hypothesis_id"), interpretation=args.get("interpretation"),
                deterministic_facts=args.get("deterministic_facts"),
                unlocked_capability=args.get("unlocked_capability"))
            return {"observation_id": o["observation_id"]}
        if name == "claim_candidate":
            self.candidate_count += 1
            rec = self.store.add_candidate({
                "claimed_outcome": args.get("claimed_outcome"),
                "minimal_steps": args.get("minimal_steps", []),
                "affected_assets": args.get("affected_assets", []),
                "suspected_root_cause": args.get("suspected_root_cause"),
                "evidence_summary": args.get("evidence_summary"),
                "capabilities": args.get("capabilities", []),
                "status": "claimed"})
            coarse = self.candidate_hook(rec)  # deterministic pre-adjudication (coarse only)
            return coarse
        if name == "recall":
            kind = args.get("kind")
            if kind not in self.store._files:
                return {"error": "unknown_kind", "kind": kind}
            rows = self.store._read_all(kind)
            rid = args.get("id")
            kw = (args.get("keyword") or "").lower()
            limit = int(args.get("limit") or 20)
            id_field = {"hypotheses": "hypothesis_id", "observations": "observation_id",
                        "candidates": "candidate_id"}.get(kind)
            out = []
            for r in rows:
                if rid and id_field and r.get(id_field) != rid:
                    continue
                if kw and kw not in json.dumps(r, default=str).lower():
                    continue
                out.append(r)
            return {"kind": kind, "count": len(out), "records": out[-limit:]}
        if name == "note":
            self.store.event("note", text=args.get("text", "")[:2000])
            return {"ok": True}
        if name == "conclude":
            self.concluded = True
            self.conclude_summary = args.get("summary", "")
            return {"ok": True, "note": "campaign will end"}
        # --- M4 lifecycle tools (no-op unless a hyp_manager is wired) ---
        if name in ("define_experiment", "mark_blocked", "request_replay"):
            if self.hyp_manager is None:
                return {"error": "lifecycle_not_enabled", "tool": name}
            return self._dispatch_lifecycle(name, args)
        return {"error": "unknown_tool", "name": name}

    def _dispatch_lifecycle(self, name, args):
        from . import hypotheses as hyp
        hid = args.get("hypothesis_id")
        mgr = self.hyp_manager
        if name == "define_experiment":
            rec = mgr.get(hid)
            if rec is None:
                return {"error": "unknown_hypothesis", "hypothesis_id": hid}
            self.store._append(mgr.KIND, {
                "hypothesis_id": hid, "_update": True,
                "experiment": args.get("experiment"),
                "success_condition": args.get("success_condition"),
                "stop_condition": args.get("stop_condition"),
                "max_actions": args.get("max_actions", rec.get("max_actions")),
                "max_duration_s": args.get("max_duration_s", rec.get("max_duration_s"))})
            # advance PROPOSED->CLAIMED->EXPERIMENT_DEFINED as needed
            cur = mgr.get(hid)["status"]
            if cur == hyp.HypothesisState.PROPOSED.value:
                mgr.advance(hid, hyp.HypothesisState.CLAIMED, reason="experiment_defined", actor=self.owner)
            mgr.advance(hid, hyp.HypothesisState.EXPERIMENT_DEFINED,
                        reason="experiment_defined", actor=self.owner)
            return {"ok": True, "hypothesis_id": hid, "status": mgr.get(hid)["status"]}
        if name == "mark_blocked":
            try:
                mgr.advance(hid, hyp.HypothesisState.BLOCKED, reason=args.get("reason"),
                            blocker=args.get("blocker"), actor=self.owner,
                            next_best_action=args.get("next_best_action"))
            except hyp.LifecycleError as e:
                return {"error": "illegal_block", "detail": str(e)}
            return {"ok": True, "hypothesis_id": hid, "status": "blocked",
                    "blocker": args.get("blocker")}
        if name == "request_replay":
            try:
                mgr.advance(hid, hyp.HypothesisState.REPLAY_REQUESTED,
                            reason=args.get("reason") or "independent_replay", actor=self.owner)
            except hyp.LifecycleError as e:
                return {"error": "illegal_replay_request", "detail": str(e)}
            return {"ok": True, "hypothesis_id": hid, "status": "replay_requested"}
        return {"error": "unknown_tool", "name": name}
