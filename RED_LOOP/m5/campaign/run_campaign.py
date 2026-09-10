#!/usr/bin/env python3
"""M5 campaign runner: build the blind benchmark, launch it, run the autonomous
Director against every environment, verify candidates, tear down.

Usage:
  source RED_LOOP/llm.env
  python3 RED_LOOP/m5/campaign/run_campaign.py [--rounds N] [--models a,b] \
        [--out DIR] [--base-port P] [--seed S]

Writes the campaign artifact set into --out (default a timestamped dir under
RED_LOOP/m5/campaign/.run/). Runtime engine state is disposable; only the
JSON/JSONL evidence is promoted to reports/implementation later.
"""
import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
M5 = HERE.parent
REPO = M5.parents[1]
sys.path.insert(0, str(M5 / "benchmark"))
sys.path.insert(0, str(HERE))
from build import build  # noqa: E402
from launch import launch  # noqa: E402
from director import Director  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=8)
    ap.add_argument("--models", default="kimi-k3")
    ap.add_argument("--out", default=None)
    ap.add_argument("--base-port", type=int, default=8400)
    ap.add_argument("--sink-port", type=int, default=8499)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--work", default=None)
    args = ap.parse_args()

    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = Path(args.out or (HERE / ".run" / f"camp-{stamp}"))
    out.mkdir(parents=True, exist_ok=True)
    work = Path(args.work or (out / "work"))

    builds = build(work / "builds")
    L = launch(builds, work_dir=str(work / "envs"),
               base_port=args.base_port, sink_port=args.sink_port)
    # persist control artifacts (control-plane; not worker-visible)
    (out / "public_manifest.json").write_text(json.dumps(L.public, indent=2))
    (out / ".answer_key.json").write_text(json.dumps(L.answer, indent=2))
    try:
        d = Director({"envs": L.public}, L._by_env, L.answer, out,
                     models=[m.strip() for m in args.models.split(",") if m.strip()],
                     rounds_per_env=args.rounds, seed=args.seed)
        summary = d.run(interrupt_env_index=0)
    finally:
        L.stop()

    print(json.dumps({k: summary[k] for k in (
        "duration_s", "unique_hypotheses", "experiments_reached", "baseline_ops",
        "meaningful_changes", "strategic_reallocations", "recovered_workers",
        "duplicates_suppressed", "candidates", "distinct_defects_found",
        "defect_count", "invariant_families_found",
        "false_findings_on_fixed_reference")}, indent=2))
    print(f"\nartifacts -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
