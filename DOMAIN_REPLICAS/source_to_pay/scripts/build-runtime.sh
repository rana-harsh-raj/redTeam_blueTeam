#!/usr/bin/env bash
set -euo pipefail

domain_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
source_root=${S2P_SOURCE_ROOT:-/Users/rana.singh/.rzp-architecture-replica/repos}
generated_stage=${S2P_GENERATED_STAGE:-}
if [[ -z "$generated_stage" ]]; then
  if [[ -f "$domain_dir/.build/current-generated-stage" ]]; then
    generated_stage="$(<"$domain_dir/.build/current-generated-stage")/vendor-payments"
  else
    generated_stage="$domain_dir/.build/staging/vendor-payments"
  fi
fi
driver_dir="$domain_dir/runtime/source-driver"
artifacts="$domain_dir/artifacts"
source_sha=20c4f4d59970471067388afea8b1d65ac39ee126

if [[ -n "${S2P_RUNTIME_STAGE+x}" || -n "${S2P_RUNTIME_OUT+x}" ]]; then
  printf 'S2P_RUNTIME_STAGE and S2P_RUNTIME_OUT overrides are unsupported because Docker inputs must remain inside the domain build context\n' >&2
  exit 2
fi
driver_hash=$(python3 - "$driver_dir/main.go" "$driver_dir/boot.go" <<'PY'
import hashlib, pathlib, sys
h = hashlib.sha256()
for name in sys.argv[1:]: h.update(pathlib.Path(name).read_bytes())
print(h.hexdigest()[:16])
PY
)
build_inputs_hash=$(python3 - "$domain_dir" "$generated_stage" "$source_sha" <<'PY'
import hashlib, pathlib, sys
domain, stage, source_sha = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]), sys.argv[3]
h = hashlib.sha256()
def add(label, path):
    h.update(label.encode() + b"\0")
    h.update(path.read_bytes())
for relative in (
    "source-lock.json", "build/codegen.py", "runtime/images/source-runtime.Dockerfile",
    "runtime/source-driver/main.go", "runtime/source-driver/boot.go", "artifacts/toolchain.sha256",
): add(relative, domain / relative)
h.update(b"source_sha\0" + source_sha.encode())
for root_name in ("generated_endpoints/rpc", "genericAI/rpc"):
    root = stage / root_name
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        add(f"generated:{path.relative_to(stage)}", path)
print(h.hexdigest())
PY
)
build_inputs_short=${build_inputs_hash:0:16}
run_id="${build_inputs_short}-$(date -u +%Y%m%dT%H%M%SZ)-$$"
runtime_stage="$domain_dir/.build/runtime-stage/$run_id/vendor-payments"
runtime_out="$domain_dir/.build/runtime-bin/$run_id"

"$domain_dir/scripts/bootstrap.sh" >/dev/null
mkdir -p "$(dirname "$runtime_stage")" "$runtime_out" "$artifacts"
if [[ -e "$runtime_stage" ]]; then
  printf 'refusing existing runtime stage: %s\n' "$runtime_stage" >&2
  exit 2
fi
if [[ ! -d "$generated_stage/generated_endpoints/rpc" || ! -d "$generated_stage/genericAI/rpc" ]]; then
  printf 'generated vendor-payments stage is incomplete: %s\n' "$generated_stage" >&2
  exit 2
fi

python3 - "$source_root/vendor-payments" "$generated_stage" <<'PY'
import hashlib, pathlib, subprocess, sys
source, stage = map(pathlib.Path, sys.argv[1:])
head = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
if head != "20c4f4d59970471067388afea8b1d65ac39ee126":
    raise SystemExit("vendor-payments source SHA mismatch")
for relative in subprocess.check_output(["git", "-C", str(source), "ls-files"], text=True).splitlines():
    if pathlib.Path(relative).name.startswith("._"):
        continue
    left, right = source / relative, stage / relative
    if not left.is_file():
        continue
    if not right.exists() or hashlib.sha256(left.read_bytes()).digest() != hashlib.sha256(right.read_bytes()).digest():
        raise SystemExit(f"generated stage changed tracked source: {relative}")
for relative, expected in (("generated_endpoints/rpc", 59), ("genericAI/rpc", 124)):
    actual = sum(p.is_file() for p in (stage / relative).rglob("*"))
    if actual != expected:
        raise SystemExit(f"generated file count mismatch for {relative}: {actual} != {expected}")
PY

cp -R "$generated_stage" "$runtime_stage"
python3 - "$source_root/vendor-payments" "$runtime_stage" "$artifacts/build-runtime-source-verification.json" <<'PY'
import hashlib, json, pathlib, shutil, subprocess, sys
source, stage, output = map(pathlib.Path, sys.argv[1:])
repaired = []
for relative in subprocess.check_output(["git", "-C", str(source), "ls-files"], text=True).splitlines():
    left, right = source / relative, stage / relative
    if not left.is_file():
        continue
    if not right.exists() and pathlib.Path(relative).name.startswith("._"):
        right.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(left, right)
        repaired.append(relative)
    if not right.exists() or hashlib.sha256(left.read_bytes()).digest() != hashlib.sha256(right.read_bytes()).digest():
        raise SystemExit(f"runtime stage differs from pinned tracked source: {relative}")
output.write_text(json.dumps({
    "source_sha": "20c4f4d59970471067388afea8b1d65ac39ee126",
    "verified": "all regular tracked source files match byte-for-byte before the declared synthetic patch",
    "archive_metadata_repairs": repaired
}, indent=2) + "\n")
PY
mkdir -p "$runtime_stage/cmd/s2p-replica"
cp "$driver_dir/main.go" "$runtime_stage/cmd/s2p-replica/main.go"
cp "$driver_dir/boot.go" "$runtime_stage/internal/boot/s2p_replica.go"

python3 - "$runtime_stage/internal/taxpayments/constants.go" "$artifacts/build-runtime-patch.json" <<'PY'
import hashlib, json, pathlib, re, sys
path, output = map(pathlib.Path, sys.argv[1:])
original = path.read_bytes()
text = original.decode()
changes = {
    "TaxPaymentFundAccountNumber": "000000000000001",
    "TaxPaymentFundAccountIFSC": "TEST0000001",
}
for symbol, replacement in changes.items():
    pattern = rf'(?m)^(\s*{symbol}\s*=\s*)"[^"]+"(\s*)$'
    text, count = re.subn(pattern, rf'\g<1>"{replacement}"\g<2>', text)
    if count != 1:
        raise SystemExit(f"anchored constant patch matched {count} lines for {symbol}")
updated = text.encode()
path.write_bytes(updated)
output.write_text(json.dumps({
    "file": "internal/taxpayments/constants.go",
    "changed_symbols": sorted(changes),
    "before_sha256": hashlib.sha256(original).hexdigest(),
    "after_sha256": hashlib.sha256(updated).hexdigest(),
    "policy": "only the two current tax-payment fund-account constants are replaced with synthetic values; original values are intentionally omitted"
}, indent=2) + "\n")
PY

binary="$runtime_out/s2p-replica-linux-arm64"
log="$artifacts/build-runtime.log"
(
  cd "$runtime_stage"
  printf 'source_sha=%s\ndriver_hash=%s\ngo=%s\n' "$source_sha" "$driver_hash" "$(go version)"
  printf 'command=CGO_ENABLED=0 GOOS=linux GOARCH=arm64 GOFLAGS=-mod=readonly go build -trimpath -o %s ./cmd/s2p-replica\n' "$binary"
  CGO_ENABLED=0 GOOS=linux GOARCH=arm64 GOFLAGS=-mod=readonly GOPRIVATE=github.com/razorpay \
    go build -trimpath -o "$binary" ./cmd/s2p-replica
) 2>&1 | tee "$log"

image="s2p-architecture-replica/vendor-payments:${source_sha}-${build_inputs_short}"
binary_context=${binary#"$domain_dir/"}
migrations_context=${runtime_stage#"$domain_dir/"}/internal/migrations
if [[ "$binary_context" = "$binary" || "$migrations_context" = "$runtime_stage/internal/migrations" ]]; then
  printf 'runtime Docker inputs escaped domain context\n' >&2
  exit 2
fi
docker build --file "$domain_dir/runtime/images/source-runtime.Dockerfile" \
  --build-arg "BINARY=$binary_context" \
  --build-arg "MIGRATIONS=$migrations_context" \
  --label "org.opencontainers.image.revision=$source_sha" \
  --label "io.razorpay.s2p.driver-sha256=$driver_hash" \
  --tag "$image" "$domain_dir" 2>&1 | tee -a "$log"

python3 - "$binary" "$image" "$source_sha" "$driver_hash" "$build_inputs_hash" "$generated_stage" "$artifacts/build-runtime.json" <<'PY'
import hashlib, json, pathlib, subprocess, sys
binary, image, source_sha, driver_hash, build_inputs_hash, generated_stage, output = sys.argv[1:]
path = pathlib.Path(binary)
inspect = json.loads(subprocess.check_output(["docker", "image", "inspect", image], text=True))[0]
output_path = pathlib.Path(output)
output_path.write_text(json.dumps({
    "source_sha": source_sha,
    "driver_hash": driver_hash,
    "build_inputs_sha256": build_inputs_hash,
    "build_inputs": ["source-lock.json", "build/codegen.py", "artifacts/toolchain.sha256", "runtime/source-driver/main.go", "runtime/source-driver/boot.go", "runtime/images/source-runtime.Dockerfile", "generated_endpoints/rpc/**", "genericAI/rpc/**", "pinned vendor-payments source SHA"],
    "generated_stage": generated_stage,
    "binary": {"path": str(path), "bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "target": "linux/arm64"},
    "image": {"tag": image, "id": inspect["Id"], "repo_digests": inspect.get("RepoDigests", [])},
    "contents_policy": "runtime layer contains the statically linked driver binary and internal/migrations only; no upstream config or credentials",
    "container_started": False
}, indent=2) + "\n")
PY
printf 'S2P_SOURCE_IMAGE=%s\n' "$image" > "$domain_dir/.build/runtime-image.env"
printf 'runtime image: %s\n' "$image"
