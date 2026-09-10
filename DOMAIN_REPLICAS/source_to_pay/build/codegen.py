#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path

DOMAIN = Path(__file__).resolve().parents[1]


def copy_tree(source: Path, target: Path) -> None:
    shutil.copytree(source, target, dirs_exist_ok=True)


def without_openapi(source: Path, target: Path) -> None:
    lines = source.read_text().splitlines()
    output, skipping = [], False
    for line in lines:
        if line.startswith("  - name: openapiv2"):
            skipping = True
            continue
        if skipping and line.startswith("  - name:"):
            skipping = False
        if not skipping:
            output.append(line)
    target.write_text("\n".join(output) + "\n")


def generate(repo: Path, relative_root: str, tools: Path) -> None:
    root = repo / relative_root
    config = root / "buf.gen.architecture.yaml"
    without_openapi(root / "buf.gen.yaml", config)
    env_path = f"{tools}:{Path('/usr/local/bin')}:{Path('/usr/bin')}:{Path('/bin')}"
    subprocess.run([str(tools / "buf"), "generate", "--template", str(config)], cwd=root, check=True, env={"PATH": env_path, "HOME": str(Path.home())})
    config.unlink()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--staging", required=True, type=Path)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--tools", required=True, type=Path)
    args = parser.parse_args()
    proto = args.source_root / "proto"
    vp = args.staging / "vendor-payments"
    ai = args.staging / "accounting-integrations"
    for module in ("accounting-payouts", "vendor-payments", "vendor-portal", "tax-payments"):
        copy_tree(proto / module, vp / "generated_endpoints" / "proto" / module)
    copy_tree(proto / "accounting_integrations", vp / "genericAI" / "proto" / "accounting_integrations")
    copy_tree(proto / "accounting_integrations", ai / "proto" / "accounting_integrations")
    generate(vp, "generated_endpoints", args.tools)
    generate(vp, "genericAI", args.tools)
    generate(args.staging / "vendor-experience", ".", args.tools)
    generate(ai, ".", args.tools)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
