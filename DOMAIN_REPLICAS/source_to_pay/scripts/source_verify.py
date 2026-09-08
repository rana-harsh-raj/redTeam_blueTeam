#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from source_common import git, load_lock, run


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify pinned S2P repositories without modifying them")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--source-root", type=Path)
    args = parser.parse_args()
    lock = load_lock()
    root = args.source_root or Path(os.environ.get("S2P_SOURCE_ROOT", lock["source_root"]))
    result = {"verified_at": datetime.now(timezone.utc).isoformat(), "source_root": str(root), "repositories": []}
    failed = False
    for expected in lock["repositories"]:
        repo = root / expected["name"]
        item = {"name": expected["name"], "path": str(repo), "expected_sha": expected["sha"]}
        try:
            item["head"] = git(repo, "rev-parse", "HEAD")
            item["tree"] = git(repo, "rev-parse", "HEAD^{tree}")
            git(repo, "cat-file", "-e", f'{expected["sha"]}^{{commit}}')
            item["tracked_files"] = int(git(repo, "ls-tree", "-r", "--name-only", "HEAD").count("\n") + 1)
            item["status"] = git(repo, "status", "--porcelain")
            item["shallow"] = git(repo, "rev-parse", "--is-shallow-repository") == "true"
            fsck = run("git", "-C", str(repo), "fsck", "--full", "--no-reflogs", check=False)
            item["fsck_rc"] = fsck.returncode
            item["fsck_output"] = (fsck.stdout + fsck.stderr).strip()
            item["ok"] = item["head"] == expected["sha"] and item["tree"] == expected["tree"] and item["tracked_files"] == expected["tracked_files"] and not item["status"] and fsck.returncode == 0
        except Exception as exc:
            item.update(ok=False, error=str(exc))
        failed |= not item["ok"]
        result["repositories"].append(item)
    result["ok"] = not failed
    rendered = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered)
    print(rendered, end="")
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
