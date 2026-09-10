#!/usr/bin/env python3
"""Machine-readable M4 boundary summary writer (host-side, stdlib only).

Reads the JUnit XML and the per-test trace JSONL files a verifier run produced
(the ARENA_TRACE_DIR / --junitxml layout golden-run.sh uses) and emits
``reports/implementation/m4-boundary-results.json``:

    {
      "generated_at": ...,
      "run_dir": ...,
      "totals": {"passed":N,"failed":N,"xfailed":N,"xpassed":N,"skipped":N,"error":N},
      "gates": {"G46":{"passed":..,"failed":..,...}, ...},
      "tests": [
        {"node": ..., "spec_ids": [...], "outcome": "passed|failed|xfailed|...",
         "duration": ..., "denial_layers_observed": {...},
         "observations": [...], "evidence_file": ".../<node>.jsonl", "hd3": {...}}
      ]
    }

Spec ids come from the @pytest.mark.spec_id(...) decorators in the test source
(parsed with ast); denial layers and raw observations come from the trace
files (kind == "m4_observation" / "m4_hd3_probe" / "m4_g50_layers"). Robust to
a missing junit file (falls back to trace-only, marking outcome "unknown").
"""
import argparse
import ast
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
VERIFIERS_DIR = HERE / "verifiers"


def _sanitize(name):
    # mirrors helpers/trace.py select()
    return re.sub("[^a-zA-Z0-9_.-]", "_", name)


def spec_ids_by_function():
    """function name -> [spec ids] parsed from @pytest.mark.spec_id('G46')."""
    out = {}
    for path in sorted(VERIFIERS_DIR.glob("test_m4_*.py")):
        tree = ast.parse(path.read_text())
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            ids = []
            for dec in node.decorator_list:
                if (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
                        and dec.func.attr == "spec_id"):
                    for a in dec.args:
                        if isinstance(a, ast.Constant):
                            ids.append(a.value)
            if ids:
                out[node.name] = ids
    return out


def parse_junit(run_dir):
    """Return [{node, classname, function, outcome, duration}] from junit.xml."""
    junit = run_dir / "junit.xml"
    if not junit.is_file():
        return None
    root = ET.parse(str(junit)).getroot()
    cases = []
    for tc in root.iter("testcase"):
        name = tc.get("name", "")
        function = name.split("[", 1)[0]
        outcome = "passed"
        for child in tc:
            tag = child.tag.lower()
            if tag == "failure":
                outcome = "failed"
            elif tag == "error":
                outcome = "error"
            elif tag == "skipped":
                t = (child.get("type") or "").lower()
                msg = (child.get("message") or "")
                if "xfail" in t or "xfail" in msg.lower():
                    outcome = "xfailed"
                else:
                    outcome = "skipped"
        # pytest encodes xpassed as a passed testcase with a property; check props
        for prop in tc.iter("property"):
            if prop.get("name") == "xpass":
                outcome = "xpassed"
        cases.append({"node": name, "function": function, "classname": tc.get("classname", ""),
                      "outcome": outcome, "duration": float(tc.get("time", 0) or 0)})
    return cases


def load_trace(run_dir, node):
    path = run_dir / (_sanitize(node) + ".jsonl")
    if not path.is_file():
        return None, []
    records = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                records.append(json.loads(line))
            except ValueError:
                pass
    return path, records


def summarize(run_dir):
    run_dir = Path(run_dir)
    fn_specs = spec_ids_by_function()
    cases = parse_junit(run_dir)
    trace_only = cases is None
    if trace_only:
        # fall back: one entry per m4 trace file
        cases = []
        for path in sorted(run_dir.glob("test_*.jsonl")):
            node = path.stem
            cases.append({"node": node, "function": re.sub(r"_(m1|m2)_.*$", "", node),
                          "classname": "", "outcome": "unknown", "duration": 0.0})

    tests, gates = [], {}
    totals = {k: 0 for k in ("passed", "failed", "xfailed", "xpassed", "skipped", "error", "unknown")}
    for case in cases:
        function = case["function"]
        spec_ids = fn_specs.get(function, [])
        evidence, records = load_trace(run_dir, case["node"])
        meta_specs = []
        layers, observations, hd3 = {}, [], None
        for rec in records:
            data = rec.get("data", {})
            if rec.get("kind") == "m4_test_meta":
                meta_specs = data.get("spec_ids", []) or meta_specs
            elif rec.get("kind") == "m4_observation":
                label = data.get("label")
                layers[label] = data.get("observed_layer")
                observations.append({"label": label, "observed_layer": data.get("observed_layer"),
                                     "expected_layer": data.get("expected_layer"),
                                     "matches_expected": data.get("matches_expected"),
                                     "status": data.get("status")})
            elif rec.get("kind") == "m4_hd3_probe":
                hd3 = data
            elif rec.get("kind") == "m4_g50_layers":
                observations.append({"label": "g50_layers", "layers": data})
        if not spec_ids:
            spec_ids = meta_specs
        outcome = case["outcome"]
        totals[outcome] = totals.get(outcome, 0) + 1
        for sid in spec_ids or ["UNSPEC"]:
            g = gates.setdefault(sid, {k: 0 for k in totals})
            g[outcome] = g.get(outcome, 0) + 1
        entry = {"node": case["node"], "function": function, "spec_ids": spec_ids, "outcome": outcome,
                 "duration": case["duration"], "denial_layers_observed": layers,
                 "observations": observations,
                 "evidence_file": str(evidence) if evidence else None}
        if hd3:
            entry["hd3"] = hd3
        tests.append(entry)

    return {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "run_dir": str(run_dir), "junit_present": not trace_only,
            "totals": totals, "gates": gates, "tests": tests}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir", help="directory containing junit.xml and *.jsonl trace files")
    ap.add_argument("--out", default=str(HERE.parents[1] / "reports" / "implementation" / "m4-boundary-results.json"))
    args = ap.parse_args()
    result = summarize(args.run_dir)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"out": str(out), "totals": result["totals"],
                      "gates": {k: v for k, v in result["gates"].items()}}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
