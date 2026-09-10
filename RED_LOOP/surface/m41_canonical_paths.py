#!/usr/bin/env python3
"""M4.1 canonical-evidence-path validator.

Fails (exit 1) whenever a MANDATORY acceptance-evidence path:
  * is git-ignored, or
  * is not tracked by git, or
  * lives outside the repository checkout, or
  * has no canonical tracked representation.

This is the guard that makes the M4 clean-checkout defect non-recurring:
the accepted 74/74 previously keyed off a git-ignored
RED_LOOP/runs/<campaign>/m4-direct-e2e-soak.json, so a fresh checkout
scored 71/2/1. The soak is now a tracked canonical artifact; this
validator asserts that invariant for every artifact the evidence
manifest hash-binds.

Usage:
  python3 RED_LOOP/surface/m41_canonical_paths.py        # human + exit code
  python3 RED_LOOP/surface/m41_canonical_paths.py --json  # machine
"""
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MANIFEST = REPO / "reports" / "implementation" / "m4-direct-e2e-evidence-manifest.json"


def git(args):
    return subprocess.run(["git", "-C", str(REPO)] + args,
                          capture_output=True, text=True, timeout=30)


def is_ignored(rel):
    # git check-ignore exits 0 when the path IS ignored.
    return git(["check-ignore", "-q", rel]).returncode == 0


def tracked_set():
    out = git(["ls-files"]).stdout
    return set(out.splitlines()) if out else set()


def main():
    as_json = "--json" in sys.argv
    if not MANIFEST.exists():
        msg = f"manifest absent: {MANIFEST.relative_to(REPO)} (run make m4-evidence-verify)"
        print(json.dumps({"ok": False, "reason": msg}) if as_json else "FAIL: " + msg)
        return 1

    man = json.loads(MANIFEST.read_text())
    tracked = tracked_set()
    violations = []
    checked = 0
    for a in man.get("artifacts", []):
        rel = a["path"]
        checked += 1
        p = (REPO / rel)
        why = []
        # outside checkout
        try:
            p.resolve().relative_to(REPO)
        except ValueError:
            why.append("outside_checkout")
        if is_ignored(rel):
            why.append("git_ignored")
        if rel not in tracked:
            why.append("not_tracked")
        if not p.exists():
            why.append("missing")
        if why:
            violations.append({"path": rel, "reasons": why})

    ok = not violations
    result = {
        "ok": ok,
        "manifest": str(MANIFEST.relative_to(REPO)),
        "artifacts_checked": checked,
        "violations": violations,
        "rule": "every mandatory evidence path must be git-tracked, not ignored, "
                "inside the checkout, and present",
    }
    if as_json:
        print(json.dumps(result, indent=2))
    else:
        if ok:
            print(f"OK: {checked} mandatory evidence paths are canonical "
                  f"(tracked, not ignored, present).")
        else:
            print(f"FAIL: {len(violations)} non-canonical evidence path(s):")
            for v in violations:
                print(f"  - {v['path']}: {', '.join(v['reasons'])}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
