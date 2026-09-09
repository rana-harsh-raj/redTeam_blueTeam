"""Twin-scoped verification subprocess (independent replay + deterministic judge).

  echo '<candidate json>' | python3 -m controlplane.verify_main

Runs INSIDE a twin-scoped environment (DOCKER_HOST / KONG_LITE_HOST_URL set by
the engine). It replays the candidate's minimal steps through a FRESH broker (a
different merchant than the claimant) and consults the RED_LOOP deterministic
judge's authoritative-state invariant scan when available. Prints one JSON line:
{reproduced, verdict, replay_detail, judge_basis}. Any failure yields
``verdict: "inconclusive"`` so a candidate is never promoted without genuine
corroboration.
"""
import json
import sys


def main():
    payload = json.loads(sys.stdin.read() or "{}")
    cand = payload.get("candidate", {})
    out = {"reproduced": None, "verdict": "inconclusive",
           "replay_detail": None, "judge_basis": None}
    try:
        out.update(_replay(cand))
    except Exception as e:  # noqa: BLE001
        out["replay_detail"] = "replay_error: %s" % str(e)[:200]
    try:
        out.update(_judge(cand))
    except Exception as e:  # noqa: BLE001
        out["judge_basis"] = "judge_error: %s" % str(e)[:200]
    print(json.dumps(out, default=str))
    return 0


def _replay(cand):
    from red_loop.broker import Broker
    from red_loop import provisioner
    # a DIFFERENT merchant than the claimant performs the independent replay
    attacker = provisioner.provision_funded_merchant("verify-" + (cand.get("candidate_id") or "x"),
                                                     role="verifier")
    broker = Broker(attacker, action_sink=lambda r: None)
    reproduced = None
    details = []
    for step in (cand.get("minimal_steps") or [])[:8]:
        method, path = _parse_step(step)
        if not path:
            continue
        r = broker.request(method, path)
        details.append({"step": step, "status": r.get("status")})
        # a reproduced effect must at least be a non-denied response on the surface
        reproduced = (reproduced in (None, True)) and int(r.get("status", 0)) < 400
    return {"reproduced": reproduced, "replay_detail": details}


def _judge(cand):
    from red_loop.judge import Judge
    j = Judge()
    # invariant scan over authoritative state; if the scan cannot bind to this
    # candidate's assets it returns no violation -> refuted (honest).
    try:
        facts = j.scan_invariants(actor_merchants=cand.get("affected_assets") or [],
                                  attacker_id=None, since_ts=0, candidate_payouts=[])
        violated = bool(facts.get("violations")) if isinstance(facts, dict) else False
        return {"verdict": "confirmed" if violated else "refuted",
                "judge_basis": "invariant_scan"}
    except Exception:  # noqa: BLE001
        return {"verdict": "inconclusive", "judge_basis": "judge_unavailable"}


def _parse_step(step):
    if isinstance(step, dict):
        return step.get("method", "GET").upper(), step.get("path", "")
    parts = str(step).split()
    if len(parts) >= 2 and parts[0].upper() in ("GET", "POST", "PUT", "PATCH", "DELETE"):
        return parts[0].upper(), parts[1]
    if str(step).startswith("/"):
        return "GET", str(step)
    return "GET", ""


if __name__ == "__main__":
    raise SystemExit(main())
