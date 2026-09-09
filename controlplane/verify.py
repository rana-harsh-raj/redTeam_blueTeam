"""Independent verification of candidates.

A candidate becomes *verified* only when BOTH hold, and neither is performed by
the worker that claimed it:

  1. an INDEPENDENT REPLAY reproduces the claimed effect through the broker,
     driven by a decorrelated model (different family than the producer) and a
     different worker id;
  2. a DETERMINISTIC JUDGE, reading authoritative twin state (never the agent's
     prose), confirms the effect and does not classify it as a known/expected
     gap.

Otherwise the candidate is ``rejected`` (replay or judge negative) or ``invalid``
(a boundary/known-gap classification). Candidates remain candidates until this
passes; the plane never manufactures a verified finding.

``replay_fn`` and ``judge_fn`` are injected so the plane runs live (RED_LOOP
reproducer + judge in a twin-scoped subprocess) or under deterministic tests
(fakes, no Docker, no model).
"""
from .store import utc
from .models import family_of


class Verifier:
    def __init__(self, control, router, replay_fn=None, judge_fn=None):
        self.control = control
        self.router = router
        self.replay_fn = replay_fn
        self.judge_fn = judge_fn

    def verify_candidate(self, task, twin_handle=None, worker_id=None):
        cid = task["verifies_candidate"]
        cand = self.control.get("candidates", cid)
        if cand is None:
            return {"error": "unknown_candidate", "candidate_id": cid}
        claimant = cand.get("worker_id")
        producer_model = cand.get("producer_model")
        worker_id = worker_id or ("V-" + cid)
        if worker_id == claimant:
            worker_id = "V-" + cid                     # never the claimant
        verifier_model = self.router.verifier_for(producer_model)
        decorrelated = (verifier_model is not None
                        and family_of(verifier_model) != family_of(producer_model)
                        and verifier_model != producer_model)

        vid = self.control.next_id("verifications", "VER")
        self.control.event("verification_started", verification_id=vid, candidate_id=cid,
                           claimant_worker=claimant, verifier_worker=worker_id,
                           producer_model=producer_model, verifier_model=verifier_model,
                           decorrelated=decorrelated)

        # 1) independent replay
        replay = {"reproduced": None, "note": "no_replay_fn"}
        if self.replay_fn is not None:
            replay = self.replay_fn(cand, twin_handle, verifier_model, worker_id)
        # 2) deterministic judge over authoritative state
        judged = {"verdict": "unjudged", "note": "no_judge_fn"}
        if self.judge_fn is not None:
            judged = self.judge_fn(cand, twin_handle)

        status = self._decide(replay, judged)
        rec = {
            "verification_id": vid, "candidate_id": cid, "thesis_id": cand.get("thesis_id"),
            "twin": cand.get("twin"), "claimant_worker": claimant,
            "verifier_worker": worker_id, "producer_model": producer_model,
            "verifier_model": verifier_model, "decorrelated": decorrelated,
            "replay": replay, "judge": judged, "status": status, "decided_at": utc(),
        }
        self.control.put("verifications", rec)
        self.control.update("candidates", cid, status=status, verification_id=vid,
                            verifier_worker=worker_id, verifier_model=verifier_model)
        self.control.event("verification_decided", verification_id=vid, candidate_id=cid,
                           status=status, decorrelated=decorrelated)
        return rec

    @staticmethod
    def _decide(replay, judged):
        verdict = (judged or {}).get("verdict")
        reproduced = (replay or {}).get("reproduced")
        if verdict in ("known_gap", "boundary", "calibration_rediscovery", "invalid"):
            return "invalid"
        if reproduced is True and verdict in ("confirmed", "violation", "supported"):
            return "verified"
        if reproduced is False or verdict in ("refuted", "no_violation", "safe"):
            return "rejected"
        # inconclusive evidence never promotes a candidate
        return "rejected"
