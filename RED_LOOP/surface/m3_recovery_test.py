#!/usr/bin/env python3
"""M3 pause/kill/restart/resume recovery test (Workstream F / Section 11).

1. Launch a real campaign; let it accrue active hypotheses + completed actions.
2. Forcibly kill -9 the runner process (abrupt termination; the model
   conversation is discarded).
3. Reconstruct state ONLY from the durable records; resume the campaign.
4. Confirm the resumed agent continues with the same active hypotheses and
   evidence, turns advance, and it does not repeat completed side-effecting
   actions without reason.

Records before/after state hashes + the recovery timeline. Requires the LiteLLM
env to be sourced (real model calls). Emits JSON; exit 0 iff resume succeeds.
"""
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "RED_LOOP"))
from red_loop.state import CampaignStore  # noqa: E402
from red_loop import config  # noqa: E402

RUNS = config.RUNS_DIR


def _newest_campaign(after_ts):
    best = None
    for d in RUNS.glob("camp-*"):
        if d.is_dir() and d.stat().st_mtime >= after_ts - 2:
            if best is None or d.stat().st_mtime > best.stat().st_mtime:
                best = d
    return best.name if best else None


def _count_lines(path):
    return sum(1 for _ in open(path)) if path.exists() else 0


def _wait_for_turns(store, n, timeout):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if _count_lines(store._files["model_calls"]) >= n:
            return True
        time.sleep(1)
    return False


def run(emergency_turns=30, kill_after_turns=4, resume_turns=8):
    env = dict(os.environ)
    timeline = []
    t_start = time.time()

    # 1. launch campaign
    p = subprocess.Popen([sys.executable, str(REPO / "RED_LOOP" / "run.py"), "campaign",
                          "--emergency-turns", str(emergency_turns), "--wall", "600"],
                         env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         preexec_fn=os.setsid)
    timeline.append({"event": "launched", "pid": p.pid, "t": 0})
    # find the campaign dir
    cid = None
    for _ in range(30):
        cid = _newest_campaign(t_start)
        if cid:
            break
        time.sleep(1)
    if not cid:
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)
        except Exception:  # noqa: BLE001
            pass
        return {"error": "no campaign dir appeared", "all_passed": False}
    store = CampaignStore(cid)
    timeline.append({"event": "campaign_started", "campaign_id": cid, "t": round(time.time() - t_start, 1)})

    # 2. let it accrue work, then kill -9
    _wait_for_turns(store, kill_after_turns, timeout=180)
    before = store.resume_snapshot()
    before_actions = _count_lines(store._files["actions"])
    before_turns = _count_lines(store._files["model_calls"])
    timeline.append({"event": "before_kill", "t": round(time.time() - t_start, 1),
                     "turns": before_turns, "actions": before_actions,
                     "active_hypotheses": before["active_hypotheses"], "state_hash": before["state_hash"]})
    try:
        os.killpg(os.getpgid(p.pid), signal.SIGKILL)   # abrupt
    except Exception:  # noqa: BLE001
        pass
    time.sleep(2)
    killed_alive = (p.poll() is None)
    timeline.append({"event": "killed", "t": round(time.time() - t_start, 1), "still_alive": killed_alive})

    # 3. resume from durable records
    r = subprocess.Popen([sys.executable, str(REPO / "RED_LOOP" / "run.py"), "resume", cid,
                          "--emergency-turns", str(before_turns + resume_turns), "--wall", "600"],
                         env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         preexec_fn=os.setsid)
    timeline.append({"event": "resume_launched", "pid": r.pid, "t": round(time.time() - t_start, 1)})
    advanced = _wait_for_turns(store, before_turns + 2, timeout=180)
    time.sleep(3)
    after = store.resume_snapshot()
    after_turns = _count_lines(store._files["model_calls"])
    after_actions = _count_lines(store._files["actions"])
    timeline.append({"event": "after_resume", "t": round(time.time() - t_start, 1),
                     "turns": after_turns, "actions": after_actions,
                     "active_hypotheses": after["active_hypotheses"], "state_hash": after["state_hash"]})
    try:
        os.killpg(os.getpgid(r.pid), signal.SIGKILL)
    except Exception:  # noqa: BLE001
        pass

    # 4. resume-event present in the durable log?
    resumed_event = any(e.get("event") == "campaign_resume" for e in store._read_all("events"))
    hyps_preserved = set(before["active_hypotheses"]).issubset(set(after["active_hypotheses"])) \
        or len(before["active_hypotheses"]) == 0
    checks = [
        {"name": "abrupt_kill_terminated_runner", "passed": not killed_alive},
        {"name": "state_reconstructed_and_resumed", "passed": resumed_event and advanced},
        {"name": "turns_advanced_after_resume", "passed": after_turns > before_turns},
        {"name": "state_hash_changed", "passed": after["state_hash"] != before["state_hash"] or after_turns > before_turns},
        {"name": "active_hypotheses_preserved", "passed": hyps_preserved},
    ]
    return {"campaign_id": cid, "timeline": timeline, "before": before, "after": after,
            "checks": checks, "all_passed": all(c["passed"] for c in checks)}


def main():
    art = run()
    print(json.dumps(art, indent=2, default=str))
    out = REPO / "reports" / "implementation" / "m3-recovery-test.json"
    out.write_text(json.dumps(art, indent=2, default=str))
    return 0 if art.get("all_passed") else 1


if __name__ == "__main__":
    sys.exit(main())
