#!/usr/bin/env bash
# Apply the arena-only build patches to the repo COPIES under $1 (never to the pristine clones). Idempotent.
set -euo pipefail
ROOT="${1:?usage: $0 <repos-root containing payouts, cfa, x-balances>}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
for r in payouts cfa x-balances; do
  [ -d "$ROOT/$r" ] || { echo "skip $r (not under $ROOT)"; continue; }
  v="$(grep -o 'goutils/dcs v[0-9.]*' "$ROOT/$r/go.mod" | head -1 | awk '{print $2}')"
  [ -d "$HERE/arena-patches/goutils-dcs-$v" ] || { echo "no patched module copy for $r (goutils/dcs $v)" >&2; exit 1; }
  if ! grep -q "arena-patches/goutils-dcs" "$ROOT/$r/go.mod"; then
    printf '\n// ARENA PATCH: local copy of goutils/dcs %s with ARENA_DCS_URL fallback (build/arena-patches/README.md)\nreplace github.com/razorpay/goutils/dcs => %s\n' "$v" "$HERE/arena-patches/goutils-dcs-$v" >> "$ROOT/$r/go.mod"
  fi
  echo "replace: $r -> goutils/dcs $v"
done
ROOT="$ROOT" python3 - <<'PY'
import os, re
root = os.environ["ROOT"]
block = '    // ARENA PATCH (transparent, arena-only): see build/arena-patches/README.md\n    if arenaURL := os.Getenv("ARENA_DCS_URL"); arenaURL != "" {\n        ctx = %s.SetContextUrl(ctx, arenaURL)\n    }\n'
targets = {
    "payouts/pkg/dcs/client.go": ("dcs", r"(func NewService\([^\n]*\{\n)"),
    "cfa/internal/dcsservice/client.go": ("dcs", r"(func New[A-Za-z]*\([^\n]*\{\n)"),
    "x-balances/pkg/dcs/client.go": ("goutilsDcs", r"(func New[A-Za-z]*\([^\n]*\{\n)"),
}
for rel, (alias, anchor) in targets.items():
    p = os.path.join(root, rel)
    if not os.path.exists(p):
        print("skip", rel); continue
    s = open(p).read()
    if "ARENA_DCS_URL" in s:
        print("already patched", rel); continue
    s2 = re.sub(anchor, lambda m: m.group(1) + block % alias, s, count=1)
    if '"os"' not in s2:
        s2 = s2.replace("import (\n", 'import (\n\t"os"\n', 1)
    open(p, "w").write(s2); print("patched", rel)
p = os.path.join(root, "payouts/pkg/stork/client.go")
if os.path.exists(p):
    s = open(p).read()
    if "ARENA_STORK_JSON" not in s:
        s = s.replace("\twebhookClient := webhookv1.NewWebhookAPIProtobufClient(config.Host, requestClient)\n",
                      "\twebhookClient := webhookv1.NewWebhookAPIProtobufClient(config.Host, requestClient)\n"
                      "\t// ARENA PATCH: stork-capture speaks twirp/JSON only (build/arena-patches/README.md)\n"
                      '\tif os.Getenv("ARENA_STORK_JSON") == "1" {\n\t\twebhookClient = webhookv1.NewWebhookAPIJSONClient(config.Host, requestClient)\n\t}\n', 1)
        if '\t"os"\n' not in s:
            s = s.replace("import (\n", 'import (\n\t"os"\n', 1)
        open(p, "w").write(s); print("patched payouts/pkg/stork/client.go")
    else:
        print("already patched payouts/pkg/stork/client.go")
PY
echo "arena patches applied under $ROOT"
