"""Build physical copies of the workflow engine: one pristine 'fixed' reference
and one copy per mutation, each with exactly one invariant-violating source
patch applied to its own wfengine/core.py.

Returns a build manifest (env label -> directory + defect metadata). The
answer key derived from this is control-plane only and must never be handed to
campaign workers.
"""
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
ENGINE_SRC = REPO / "ENV2_COMPOSE" / "substitutes" / "workflow-engine"
sys.path.insert(0, str(HERE))
from mutations import MUTATIONS  # noqa: E402


def _copy_engine(dest: Path):
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(ENGINE_SRC, dest, ignore=shutil.ignore_patterns(
        "__pycache__", "*.pyc", "*.db", "*.db-*", "*.jsonl"))
    return dest


def _apply(core_path: Path, patches):
    text = core_path.read_text()
    for anchor, repl in patches:
        if anchor not in text:
            raise AssertionError(
                f"MUTATION ANCHOR NOT FOUND in {core_path} — core.py drifted; "
                f"anchor head: {anchor[:80]!r}")
        text = text.replace(anchor, repl, 1)
    core_path.write_text(text)


def build(out_root: Path):
    out_root.mkdir(parents=True, exist_ok=True)
    builds = {}

    # fixed reference (pristine)
    fx = _copy_engine(out_root / "engine-fixed")
    builds["fixed"] = {"dir": str(fx), "defect": None,
                       "family": None, "invariant": None}

    # mutants
    for name, spec in MUTATIONS.items():
        d = _copy_engine(out_root / f"engine-{name}")
        _apply(d / "wfengine" / "core.py", spec["patches"])
        builds[name] = {"dir": str(d), "defect": name,
                        "family": spec["family"], "invariant": spec["invariant"]}
    return builds


if __name__ == "__main__":
    import json
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else (HERE / ".builds")
    b = build(root)
    print(json.dumps({k: {"dir": v["dir"], "defect": v["defect"]}
                      for k, v in b.items()}, indent=2))
    print(f"built {len(b)} engines under {root}")
