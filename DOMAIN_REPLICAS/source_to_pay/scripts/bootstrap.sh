#!/usr/bin/env bash
set -euo pipefail

domain_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
source_root=${S2P_SOURCE_ROOT:-/Users/rana.singh/.rzp-architecture-replica/repos}
tool_root=${S2P_TOOL_ROOT:-/Users/rana.singh/.rzp-architecture-replica/toolchains/s2p-v1}
bin_dir="$tool_root/bin"
mkdir -p "$bin_dir" "$domain_dir/artifacts"

python3 "$domain_dir/scripts/source_recover.py" --source-root "$source_root"
python3 "$domain_dir/scripts/source_verify.py" --source-root "$source_root" --output "$domain_dir/artifacts/source-verification.json" >/dev/null

export GOBIN="$bin_dir"
install_go_tool() {
  binary=$1
  module=$2
  if [[ ! -x "$bin_dir/$binary" ]]; then
    go install "$module"
  fi
}
install_go_tool buf github.com/bufbuild/buf/cmd/buf@v1.32.0
install_go_tool protoc-gen-go google.golang.org/protobuf/cmd/protoc-gen-go@v1.28.1
install_go_tool protoc-gen-go-grpc google.golang.org/grpc/cmd/protoc-gen-go-grpc@v1.3.0
install_go_tool protoc-gen-grpc-gateway github.com/grpc-ecosystem/grpc-gateway/v2/protoc-gen-grpc-gateway@v2.11.3
install_go_tool protoc-gen-twirp github.com/twitchtv/twirp/protoc-gen-twirp@v8.1.2+incompatible

python3 - "$bin_dir" "$domain_dir/source-lock.json" "$domain_dir/artifacts/toolchain.sha256" <<'PY'
import hashlib, json, pathlib, subprocess, sys
root, lock_path, output = map(pathlib.Path, sys.argv[1:])
names = ("buf", "protoc-gen-go", "protoc-gen-go-grpc", "protoc-gen-grpc-gateway", "protoc-gen-twirp")
lock = json.loads(lock_path.read_text())
actual = {n: hashlib.sha256((root / n).read_bytes()).hexdigest() for n in names}
if actual != lock["toolchain"]["sha256"]:
    raise SystemExit("tool binary hash mismatch; inspect and replace the namespaced toolchain")
for name in names:
    info = subprocess.check_output(["go", "version", "-m", str(root / name)], text=True)
    if not info.splitlines()[0].endswith("go1.26.6"):
        raise SystemExit(f"{name} was not built with go1.26.6")
output.write_text("".join(f"{actual[n]}  {n}\n" for n in names))
PY
printf 'source_root=%s\ntool_root=%s\n' "$source_root" "$tool_root"
