#!/usr/bin/env python3
"""Merge one or more journey run directories of a twin into a single executed-suite record (the shape run.py writes as
summary.json; a later run's result supersedes an earlier one per journey id -- re-runs of individual journeys after
the full suite). The merged record is what scripts/domain/journeys_part.py projects into the graph (M6_JOURNEYS_JSON)
and what the M11 report cites.

  python3 scripts/m11/merge_runs.py --out reports/implementation/m11/journeys-real.json <run_dir> [<run_dir> ...]
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True); ap.add_argument("runs", nargs="+")
    a = ap.parse_args()
    base, rows = None, {}
    for rd in a.runs:
        rd = Path(rd)
        doc = json.loads((rd / "summary.json").read_text())
        if base is None:
            base = {k: v for k, v in doc.items() if k != "journeys"}
        for j in doc.get("journeys", []):
            rows[j["id"]] = j | {"run_dir": str(rd)}
    merged = dict(base or {})
    merged["journeys"] = sorted(rows.values(), key=lambda r: r["id"])
    merged["run_dir"] = ",".join(a.runs)
    merged["merged_from"] = a.runs
    merged["merged_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    summary = {"total": len(rows)}
    for r in rows.values():
        summary[r["result"].lower()] = summary.get(r["result"].lower(), 0) + 1
    merged["summary"] = summary
    out = Path(a.out); out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(merged, indent=1))
    print(json.dumps({"out": str(out), "summary": summary, "runs": len(a.runs)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
