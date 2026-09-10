#!/usr/bin/env bash
# M11: package a host-built staging dir into a credential-free runtime image (build/runtime-only.Dockerfile).
set -euo pipefail
NAME="${1:?image name (shield|banking-accounts|workflows)}"; STAGE="${2:?staging dir}"; TAG="${ARENA_TAG:-v1-candidate}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
find "$STAGE" -type d -exec chmod 0755 {} + ; find "$STAGE" -type f -exec chmod a+r {} +
docker build -f "$HERE/../runtime-only.Dockerfile" -t "rzp-arena/${NAME}:${TAG}" "$STAGE"
