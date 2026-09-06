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
]


class Dispatcher:
    def __init__(self, broker, store, candidate_hook):
        self.broker = broker
        self.store = store
        self.candidate_hook = candidate_hook  # callable(candidate_record) -> coarse dict
        self.concluded = False
        self.conclude_summary = None
        self.candidate_count = 0

    def dispatch(self, name, args):
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
            return {"hypothesis_id": h["hypothesis_id"]}
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
        return {"error": "unknown_tool", "name": name}
