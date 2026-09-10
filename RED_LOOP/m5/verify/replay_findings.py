#!/usr/bin/env python3
"""Independent clean-state replay of every accepted M5 finding.

Reads the accepted findings from reports/implementation/m5-campaign-summary.json,
then rebuilds and relaunches a FRESH blind benchmark and, for each finding's
invariant family, independently reproduces the violation against a freshly
provisioned mutant while confirming the fixed reference still upholds the
invariant (negative control). This proves the accepted findings reproduce from
clean state with fresh IDs, independent of the original campaign run.

Writes reports/implementation/m5-finding-replays.json.
"""
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
M5 = HERE.parent
REPO = M5.parents[1]
for p in (M5 / "benchmark", M5, HERE):
    sys.path.insert(0, str(p))
from build import build  # noqa: E402
from launch import launch  # noqa: E402
from verifier import probe  # noqa: E402


def main():
    summ = REPO / "reports" / "implementation" / "m5-campaign-summary.json"
    if not summ.exists():
        print("no campaign summary to replay")
        return 1
    findings = json.loads(summ.read_text()).get("confirmed_findings", [])
    if not findings:
        print("no accepted findings to replay")
        (REPO / "reports" / "implementation" / "m5-finding-replays.json").write_text(
            json.dumps({"replays": [], "all_reproduced": True,
                        "note": "no findings"}, indent=2))
        return 0

    work = Path(sys.argv[1]) if len(sys.argv) > 1 else (HERE / ".replay")
    builds = build(work / "builds")
    L = launch(builds, work_dir=str(work / "envs"), base_port=8850, sink_port=8949)
    # map defect -> fresh env
    by_defect = {a["defect"]: e for e, a in L.answer.items() if not a["is_fixed"]}
    fixed_env = next(e for e, a in L.answer.items() if a["is_fixed"])
    fixed_ctrl = L._by_env[fixed_env]
    replays = []
    try:
        for f in findings:
            fam = f["family"]
            defect = f["root_cause"]
            env = by_defect.get(defect)
            if not env:
                replays.append({"finding": defect, "reproduced": False,
                                "reason": "no fresh mutant for defect"})
                continue
            tgt = L._by_env[env]
            v1, ev1 = probe(fam, tgt["base_url"], tgt["admin_token"])
            v2, ev2 = probe(fam, tgt["base_url"], tgt["admin_token"])
            cneg, evc = probe(fam, fixed_ctrl["base_url"], fixed_ctrl["admin_token"])
            reproduced = v1 and v2 and not cneg
            replays.append({
                "finding_defect": defect, "family": fam,
                "first_proposed_by": f.get("first_proposed_by"),
                "reproduced_by": f.get("reproduced_by"),
                "fresh_replay_1": {"violated": v1, "evidence": ev1},
                "fresh_replay_2": {"violated": v2, "evidence": ev2},
                "negative_control_fixed": {"violated": cneg, "evidence": evc},
                "reproduced": reproduced,
            })
    finally:
        L.stop()
    out = {
        "replay": "m5-finding-replays",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "count": len(replays),
        "all_reproduced": all(r.get("reproduced") for r in replays),
        "replays": replays,
    }
    dest = REPO / "reports" / "implementation" / "m5-finding-replays.json"
    dest.write_text(json.dumps(out, indent=2))
    print(f"replayed {len(replays)} findings; all_reproduced={out['all_reproduced']} "
          f"-> {dest}")
    return 0 if out["all_reproduced"] else 1


if __name__ == "__main__":
    sys.exit(main())
