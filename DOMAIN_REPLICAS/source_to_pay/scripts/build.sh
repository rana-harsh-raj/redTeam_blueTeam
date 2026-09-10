#!/usr/bin/env bash
set -euo pipefail

domain_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
source_root=${S2P_SOURCE_ROOT:-/Users/rana.singh/.rzp-architecture-replica/repos}
tool_root=${S2P_TOOL_ROOT:-/Users/rana.singh/.rzp-architecture-replica/toolchains/s2p-v1}
default_build_root="$domain_dir/.build"
build_root=${S2P_BUILD_ROOT:-$default_build_root}
staging="$build_root/staging"
output="$build_root/bin"
log="$domain_dir/artifacts/build.log"

if [[ -e "$staging" && -z "${S2P_BUILD_ROOT+x}" ]]; then
  build_root="$default_build_root/source-runs/$(date -u +%Y%m%dT%H%M%SZ)-$$"
  staging="$build_root/staging"
  output="$build_root/bin"
fi

"$domain_dir/scripts/bootstrap.sh"
mkdir -p "$build_root" "$output" "$domain_dir/artifacts"
for repo in vendor-payments vendor-experience accounting-integrations; do
  target="$staging/$repo"
  if [[ -e "$target" ]]; then
    printf 'refusing existing staging directory in explicit S2P_BUILD_ROOT: %s\n' "$target" >&2
    exit 2
  fi
  mkdir -p "$target"
  archive="$build_root/$repo.tar"
  git -C "$source_root/$repo" archive --output="$archive" HEAD
  python3 - "$archive" "$target" <<'PY'
import pathlib, sys, tarfile
archive, target = map(pathlib.Path, sys.argv[1:])
with tarfile.open(archive) as source:
    source.extractall(target)
archive.unlink()
PY
done

python3 "$domain_dir/build/codegen.py" --staging "$staging" --source-root "$source_root" --tools "$tool_root/bin"
python3 "$domain_dir/build/command_packages.py" \
  --staging "$staging" \
  --json "$domain_dir/artifacts/build-command-packages.json" \
  --log "$domain_dir/artifacts/build-command-packages.log"

export GOFLAGS=-mod=readonly GOPRIVATE=github.com/razorpay CGO_ENABLED=0
{
  printf 'go=%s\n' "$(go version)"
  printf 'staging=%s\n' "$staging"
  cd "$staging/vendor-payments"
  go mod verify
  go build -trimpath -o "$output/vendor-payments" ./cmd/vendor-payments
  go build -trimpath -o "$output/vendor-payments-worker" ./cmd/worker
  go build -trimpath -o "$output/vendor-payments-workerv2" ./cmd/workerv2
  go build -trimpath -o "$output/vendor-payments-migration" ./cmd/migration
  cd "$staging/vendor-experience"
  go mod verify
  go build -trimpath -o "$output/vendor-experience-api" ./cmd/api
  go build -trimpath -o "$output/vendor-experience-activity-worker" ./cmd/cadence/activity-worker
  go build -trimpath -o "$output/vendor-experience-workflow-worker" ./cmd/cadence/workflow-worker
  cd "$staging/accounting-integrations"
  go mod verify
  go build -trimpath -o "$output/accounting-integrations-api" ./cmd/api
  go build -trimpath -o "$output/accounting-integrations-worker" ./cmd/worker
  go build -trimpath -o "$output/accounting-integrations-cadence-worker" ./cmd/cadence/worker
} 2>&1 | tee "$log"

python3 - "$output" "$domain_dir/artifacts/build-manifest.json" <<'PY'
import hashlib, json, pathlib, platform, sys
root, destination = map(pathlib.Path, sys.argv[1:])
files = [{"name": p.name, "bytes": p.stat().st_size, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(root.iterdir()) if p.is_file()]
destination.write_text(json.dumps({"platform": platform.platform(), "output": str(root), "binaries": files}, indent=2) + "\n")
PY
printf '%s\n' "$staging" > "$default_build_root/current-generated-stage"
printf 'build outputs: %s\n' "$output"
