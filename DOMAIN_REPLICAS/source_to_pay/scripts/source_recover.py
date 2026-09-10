#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from source_common import git, load_lock, run


def main() -> int:
    parser = argparse.ArgumentParser(description="Materialize missing immutable repositories from pinned remotes")
    parser.add_argument("--source-root", type=Path)
    args = parser.parse_args()
    lock = load_lock()
    root = args.source_root or Path(lock["source_root"])
    root.mkdir(parents=True, exist_ok=True)
    for spec in lock["repositories"]:
        target = root / spec["name"]
        if target.exists():
            head = git(target, "rev-parse", "HEAD")
            if head != spec["sha"] or git(target, "status", "--porcelain"):
                raise SystemExit(f"refusing non-clean or mismatched existing source: {target}")
            continue
        partial = root / f'.{spec["name"]}.partial'
        if partial.exists():
            raise SystemExit(f"remove interrupted bootstrap directory after inspection: {partial}")
        run("git", "init", "--quiet", str(partial))
        run("git", "-C", str(partial), "remote", "add", "origin", spec["remote"])
        run("git", "-C", str(partial), "fetch", "--depth=1", "origin", spec["sha"])
        run("git", "-C", str(partial), "checkout", "--quiet", "--detach", spec["sha"])
        if int(git(partial, "ls-tree", "-r", "--name-only", "HEAD").count("\n") + 1) != spec["tracked_files"]:
            raise SystemExit(f"tracked file count mismatch: {partial}")
        shutil.move(str(partial), str(target))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
