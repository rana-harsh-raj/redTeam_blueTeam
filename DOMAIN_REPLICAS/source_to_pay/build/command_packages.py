#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path


CHECKS = (
    ("vendor-payments", (), ("./cmd/...",)),
    ("vendor-experience", (), ("./cmd/...",)),
    ("vendor-experience", ("boot",), ("./cmd/migration", "./cmd/vp_migration")),
    ("accounting-integrations", (), ("./cmd/...",)),
)


def execute(command: list[str], cwd: Path, env: dict[str, str]) -> tuple[subprocess.CompletedProcess[str], float]:
    started = time.monotonic()
    result = subprocess.run(command, cwd=cwd, env=env, text=True, capture_output=True)
    return result, round(time.monotonic() - started, 3)


def main() -> int:
    parser = argparse.ArgumentParser(description="Compile every production command package")
    parser.add_argument("--staging", required=True, type=Path)
    parser.add_argument("--json", required=True, type=Path)
    parser.add_argument("--log", required=True, type=Path)
    args = parser.parse_args()
    env = os.environ.copy()
    env.update(CGO_ENABLED="0", GOFLAGS="-mod=readonly", GOPRIVATE="github.com/razorpay")
    records, log_lines, failed = [], [], False
    for repo, tags, packages in CHECKS:
        cwd = args.staging / repo
        tag_args = ["-tags", ",".join(tags)] if tags else []
        list_command = ["go", "list", "-e", *tag_args, "-f", "{{.ImportPath}}", *packages]
        listed, list_seconds = execute(list_command, cwd, env)
        package_list = [line for line in listed.stdout.splitlines() if line]
        command = ["go", "build", *tag_args, *packages]
        built, build_seconds = execute(command, cwd, env)
        record = {
            "repository": repo,
            "tags": list(tags),
            "requested_packages": list(packages),
            "resolved_packages": package_list,
            "list_command": list_command,
            "list_rc": listed.returncode,
            "build_command": command,
            "build_rc": built.returncode,
            "duration_seconds": {"list": list_seconds, "build": build_seconds},
            "stdout": built.stdout,
            "stderr": built.stderr,
            "ok": listed.returncode == 0 and built.returncode == 0,
        }
        records.append(record)
        failed |= not record["ok"]
        log_lines.extend([
            f"repository={repo} tags={','.join(tags) or '<none>'}",
            f"list_command={' '.join(list_command)}",
            listed.stdout.rstrip(),
            listed.stderr.rstrip(),
            f"list_rc={listed.returncode}",
            f"build_command={' '.join(command)}",
            built.stdout.rstrip(),
            built.stderr.rstrip(),
            f"build_rc={built.returncode}",
        ])
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": "production command packages only; test helper packages requiring omitted mockgen outputs are excluded",
        "staging": str(args.staging),
        "checks": records,
        "ok": not failed,
    }
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(payload, indent=2) + "\n")
    args.log.write_text("\n".join(line for line in log_lines if line) + "\n")
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
