#!/usr/bin/env python3
"""Blind-benchmark integrity check (emits a durable artifact).

Builds the fixed reference + all mutants, launches them, and runs the hidden
verifier's canonical probe for EVERY invariant family against EVERY environment.
Asserts the ground truth the campaign must independently rediscover:
  * the fixed reference violates NO invariant family;
  * each mutant violates EXACTLY its own seeded family.

Writes reports/implementation/m5-benchmark-integrity.json. This is control-plane
evidence (it uses the answer key) and is never shown to campaign workers.
"""
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
M5 = HERE.parent
REPO = M5.parents[1]
for p in (HERE, M5, M5 / "verify"):
    sys.path.insert(0, str(p))
from build import build  # noqa: E402
from launch import launch  # noqa: E402
from verifier import probe, FAMILIES  # noqa: E402


def run(work_dir, base_port=8800, sink_port=8899):
    builds = build(Path(work_dir) / "builds")
    L = launch(builds, work_dir=str(Path(work_dir) / "envs"),
               base_port=base_port, sink_port=sink_port)
    matrix = []
    ok = True
    try:
        for env_id, ec in L._by_env.items():
            truth = L.answer[env_id]
            fired = []
            row = {"env_id": env_id, "is_fixed": truth["is_fixed"],
                   "seeded_defect": truth["defect"], "seeded_family": truth["family"],
                   "probes": {}}
            for fam in FAMILIES:
                violated, ev = probe(fam, ec["base_url"], ec["admin_token"])
                row["probes"][fam] = {"violated": violated, "evidence": ev}
                if violated:
                    fired.append(fam)
            row["families_fired"] = fired
            if truth["is_fixed"]:
                row["correct"] = (fired == [])
            else:
                row["correct"] = (fired == [truth["family"]])
            ok = ok and row["correct"]
            matrix.append(row)
    finally:
        L.stop()

    result = {
        "check": "m5-benchmark-integrity",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "families": list(FAMILIES),
        "environments": len(matrix),
        "mutants": sum(1 for r in matrix if not r["is_fixed"]),
        "fixed_reference_clean": all(r["correct"] for r in matrix if r["is_fixed"]),
        "each_mutant_isolated": all(r["correct"] for r in matrix if not r["is_fixed"]),
        "all_correct": ok,
        "matrix": matrix,
    }
    dest = REPO / "reports" / "implementation" / "m5-benchmark-integrity.json"
    dest.write_text(json.dumps(result, indent=2))
    print(f"benchmark integrity: all_correct={ok} -> {dest}")
    return 0 if ok else 1


if __name__ == "__main__":
    work = sys.argv[1] if len(sys.argv) > 1 else str(HERE / ".integrity")
    sys.exit(run(work))
