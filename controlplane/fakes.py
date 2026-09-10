"""Deterministic fakes: a model gateway, a broker, a twin pool, and replay/judge.

These let the entire control plane run with no network, no model provider and no
Docker — for the deterministic test suite AND for the model-free "declared action
budget" proof run. They are faithful to the real interfaces (the same
``client.complete`` shape, the same broker methods the RED_LOOP Dispatcher calls,
the same TwinPool surface) so the live path exercises identical engine code.

The fakes never manufacture a verified finding: verification is genuinely
evidence-gated (the fake judge confirms only when the fake twin actually returned
the corroborating marker the candidate cites), so an honest run can end with zero
verified findings.
"""
import itertools
import re

from .twins import TwinHandle, TwinPool


# --------------------------------------------------------------------------
# Fake model gateway
# --------------------------------------------------------------------------
class FakeModelClient:
    """A scripted OpenAI-schema client. ``behaviour`` drives what each turn does.

    behaviours:
      "explore"      -> hypothesis, probe, observe, (maybe) candidate, conclude
      "verify"       -> not used (verification runs through verify_fn)
      "timeout"      -> raises on complete() (model timeout / provider down)
      "refuse"       -> returns a content_filter finish (refusal)
      "silent"       -> returns blank turns (unusable) until fallback
    """

    def __init__(self, model, behaviour="explore", find_candidate=False, usage_tokens=(120, 60)):
        self.model = model
        self.behaviour = behaviour
        self.find_candidate = find_candidate
        self.usage_tokens = usage_tokens
        self._turn = 0

    def complete(self, messages, tools=None, tool_choice="auto", **kw):
        self._turn += 1
        if self.behaviour == "timeout":
            raise TimeoutError("fake gateway timeout for %s" % self.model)
        if self.behaviour == "refuse":
            return self._resp(content="", tool_calls=[], finish_reason="content_filter")
        if self.behaviour == "silent":
            return self._resp(content="", tool_calls=[], finish_reason="stop")
        subject = _subject_from(messages)
        seq = self._explore_script(subject)
        idx = min(self._turn - 1, len(seq) - 1)
        content, calls = seq[idx]
        return self._resp(content=content, tool_calls=calls)

    def _explore_script(self, subject):
        marker = "canary-observed"
        steps = [
            ("Orienting on %s." % subject,
             [_call("record_hypothesis", {"claim": "ordinary merchant may cross a boundary on %s" % subject,
                                          "target_assets": [{"asset": subject, "kind": "node"}]})]),
            ("Probing the surface.",
             [_call("merchant_request", {"method": "GET", "path": "/v1/payouts"})]),
            ("Recording what the twin returned.",
             [_call("record_observation", {"interpretation": "response captured",
                                           "deterministic_facts": {"probe": subject}})]),
        ]
        if self.find_candidate:
            steps.append(("The response corroborates the thesis.",
                          [_call("claim_candidate", {"claimed_outcome": "boundary crossing on %s" % subject,
                                                     "affected_assets": [subject],
                                                     "evidence_summary": "twin returned %s" % marker,
                                                     "minimal_steps": ["GET /v1/payouts"]})]))
        steps.append(("Concluding.", [_call("conclude", {"summary": "assessment of %s complete" % subject})]))
        return steps

    def _resp(self, content, tool_calls, finish_reason=None):
        pt, ct = self.usage_tokens
        return {"role": "assistant", "content": content, "tool_calls": tool_calls,
                "finish_reason": finish_reason or ("tool_calls" if tool_calls else "stop"),
                "raw_usage": {"prompt_tokens": pt, "completion_tokens": ct}}


_counter = itertools.count(1)


def _call(name, args):
    import json
    return {"id": "call-%d" % next(_counter), "name": name, "arguments": json.dumps(args)}


def _subject_from(messages):
    for m in messages:
        if m.get("role") == "system":
            mt = re.search(r"subject: (\S+)", m.get("content") or "")
            if mt:
                return mt.group(1)
    return "unknown"


def fake_client_factory(behaviour_map=None, default="explore", find_candidate_for=None):
    """Return a client_factory(model)->FakeModelClient. ``behaviour_map`` maps a
    model id to a behaviour; ``find_candidate_for`` is a set of model ids whose
    explorers claim a candidate."""
    behaviour_map = behaviour_map or {}
    find_candidate_for = set(find_candidate_for or [])

    def factory(model):
        return FakeModelClient(model, behaviour=behaviour_map.get(model, default),
                               find_candidate=model in find_candidate_for)
    return factory


# --------------------------------------------------------------------------
# Fake broker (satisfies the RED_LOOP Dispatcher's broker interface)
# --------------------------------------------------------------------------
class FakeBroker:
    def __init__(self, merchant_id="acc_fake", fixtures=None, request_budget=10000):
        self._merchant_id = merchant_id
        self.fixtures = fixtures or {}
        self.request_budget = request_budget
        self.request_count = 0
        self.violation_count = 0

    def attacker_identity(self):
        return {"merchant_id": self._merchant_id, "key_id": "key_fake", "mode": "test"}

    def request(self, method, path, headers=None, body=None, _concurrent_idx=None):
        self.request_count += 1
        fx = self.fixtures.get((method, path)) or self.fixtures.get(path) or {}
        return {"status": fx.get("status", 200), "body": fx.get("body", "{}"),
                "fingerprint": "%s:%s" % (method, path), "path": path}

    def concurrent(self, method, path, count, headers=None, body=None):
        return {"results": [self.request(method, path, headers, body) for _ in range(int(count or 1))]}

    def read_own_webhooks(self, since_ts=None, payout_id=None):
        return {"webhooks": []}


# --------------------------------------------------------------------------
# Fake twin pool
# --------------------------------------------------------------------------
class FakeTwinPool(TwinPool):
    def __init__(self, instance_ids, broker_fixtures=None, unhealthy_once=None):
        self._ids = set(instance_ids)
        self._fixtures = broker_fixtures or {}
        # instance_id -> remaining number of health checks to report unhealthy
        self._unhealthy = dict(unhealthy_once or {})
        self._brokers = {}

    def _ensure(self, instance_id):
        self._ids.add(instance_id)

    def handle(self, instance_id):
        self._ensure(instance_id)
        env = {"TWIN_INSTANCE_ID": instance_id, "KONG_LITE_HOST_URL": "http://127.0.0.1:1"}
        return TwinHandle(instance_id, "rt-" + instance_id, env,
                          "http://127.0.0.1:1", healthy=True)

    def broker_for(self, handle):
        iid = handle.instance_id
        if iid not in self._brokers:
            self._brokers[iid] = FakeBroker(merchant_id="acc_" + iid, fixtures=self._fixtures)
        return self._brokers[iid]

    def health(self, instance_id):
        n = self._unhealthy.get(instance_id, 0)
        if n > 0:
            self._unhealthy[instance_id] = n - 1
            return {"healthy": False, "checks_passed": 0, "checks_total": 12}
        return {"healthy": True, "checks_passed": 12, "checks_total": 12, "result": "PASS"}

    def recover(self, instance_id, store=None):
        new_id = instance_id + "-r"
        self._ensure(new_id)
        self._unhealthy[new_id] = 0
        if store is not None:
            store._append("recoveries", {"kind": "reproduce_ok", "instance_id": instance_id,
                                         "new_instance_id": new_id})
            store.event("twin_recovery", stage="reproduce_ok", instance_id=instance_id,
                        new_instance_id=new_id)
        h = self.handle(new_id)
        h.replaced_by = new_id
        return h


# --------------------------------------------------------------------------
# Fake replay + judge (evidence-gated; never manufactures a finding)
# --------------------------------------------------------------------------
def make_fake_verification(twin_pool, confirm_marker="canary-observed"):
    """Return (replay_fn, judge_fn). The judge confirms ONLY when the fake twin
    actually returns the marker the candidate cites AND the replay reproduced it,
    so verification is genuinely evidence-bound."""

    def replay_fn(candidate, twin_handle, verifier_model, verifier_worker):
        broker = twin_pool.broker_for(twin_handle)
        r = broker.request("GET", "/v1/payouts")
        reproduced = confirm_marker in ((candidate.get("evidence_summary") or "")) and \
            confirm_marker in str(r.get("body", ""))
        return {"reproduced": bool(reproduced), "verifier_model": verifier_model,
                "verifier_worker": verifier_worker, "probe": "GET /v1/payouts",
                "twin_returned_marker": confirm_marker in str(r.get("body", ""))}

    def judge_fn(candidate, twin_handle):
        broker = twin_pool.broker_for(twin_handle)
        r = broker.request("GET", "/v1/payouts")
        if confirm_marker in str(r.get("body", "")) and confirm_marker in (candidate.get("evidence_summary") or ""):
            return {"verdict": "confirmed", "basis": "twin state carries the cited marker"}
        return {"verdict": "refuted", "basis": "authoritative state does not corroborate the claim"}

    return replay_fn, judge_fn


# --------------------------------------------------------------------------
# Fake world model (implements the WorldModel surface without archkit)
# --------------------------------------------------------------------------
class FakeWorld:
    def __init__(self, snapshot_id="snap-fake", subjects=None):
        self._sid = snapshot_id
        self._subjects = subjects or [
            {"subject": "svc:payouts-api", "score": 4, "reason": "family", "context": "node"},
            {"subject": "identity:trust-boundary:payouts-service", "score": 6,
             "reason": "trust_boundary", "context": "identity"},
            {"subject": "svc:fts", "score": 3, "reason": "fidelity_gap", "context": "node"},
            {"subject": "family:shared-payouts", "score": 4, "reason": "family", "context": "family"},
            {"subject": "svc:ledger", "score": 3, "reason": "family", "context": "node"},
        ]

    def snapshot_id(self):
        return self._sid

    def thesis_subjects(self, mode="broad_autonomous", cap=24):
        return list(self._subjects)[:cap]

    def director_world(self, mode="broad_autonomous", limit=40):
        return {"snapshot_id": self._sid, "mode": mode,
                "families": [{"id": s["subject"], "label": s["subject"], "priority": "P0"}
                             for s in self._subjects if s["reason"] == "family"],
                "uncovered_trust_boundaries": [s["subject"] for s in self._subjects
                                               if s["reason"] == "trust_boundary"],
                "trust_boundaries": [], "fidelity_gaps": [], "journeys": [], "unknowns": []}

    def worker_packet(self, subject, budget=6000, role="explorer"):
        return {"subject": subject, "role": role,
                "sections": {"subject": {"id": subject},
                             "note": "fake bounded packet for %s" % subject},
                "truncated": False}

    def subject_exists(self, subject):
        return True


# --------------------------------------------------------------------------
# Fully-fake engine assembly (the reproducible, model-free/Docker-free proof)
# --------------------------------------------------------------------------
def build_fake_engine(control, manifest, clock=None, find_candidate_for=None,
                      broker_fixtures=None, unhealthy_once=None, worker_concurrency=2,
                      lease_ttl=60, plan_max_theses=6, director_model=False,
                      subjects=None):
    import time as _time
    from .models import ModelRouter
    from .engine import Engine
    clock = clock or _time.time
    world = FakeWorld(snapshot_id=manifest.get("architecture_snapshot_id"), subjects=subjects)
    router = ModelRouter(manifest["model_pool"], store=control)
    ids = [t["instance_id"] for t in manifest["twins"]]
    fixtures = {("GET", k): v for k, v in (broker_fixtures or {}).items()}
    pool = FakeTwinPool(ids, broker_fixtures=fixtures, unhealthy_once=unhealthy_once)
    cf = fake_client_factory(find_candidate_for=find_candidate_for)
    dcf = fake_client_factory() if director_model else None
    replay_fn, judge_fn = make_fake_verification(pool)
    eng = Engine(control, manifest, world, router, pool, cf, replay_fn=replay_fn,
                 judge_fn=judge_fn, clock=clock, lease_ttl=lease_ttl,
                 worker_concurrency=worker_concurrency, plan_max_theses=plan_max_theses,
                 director_client_factory=dcf)
    return eng, pool
