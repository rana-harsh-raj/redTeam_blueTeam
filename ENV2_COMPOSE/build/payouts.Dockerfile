# build/payouts.Dockerfile — multi-stage build for the `payouts` repo.
#
# Facts this Dockerfile is grounded on (see findings/29_env2_scaffold.md /
# the fork research folded in there): go.mod `module github.com/razorpay/payouts`,
# `go 1.26.0`; entrypoints cmd/api, cmd/workers, cmd/kafkaConsumers,
# cmd/migration; server port :9400, metrics :8001.
#
# SAFETY: build stage takes a git credential ONLY via a BuildKit secret
# mount (never a build arg, never COPYed into a layer) so `go mod download`
# can reach the private github.com/razorpay/* modules (GOPRIVATE below); the
# credential is gone before the runtime stage starts, and the runtime stage
# is built FROM SCRATCH-adjacent alpine with only the compiled binaries + CA
# certs -- no .netrc, no token, no source tree.
#
# Usage (see build.sh for the exact invocation incl. secret sourcing):
#   DOCKER_BUILDKIT=1 docker build \
#     -f build/payouts.Dockerfile \
#     --secret id=netrc,src="$ARENA_NETRC" \
#     -t rzp-arena/payouts:local \
#     <path-to-payouts-repo-clone>

# ---- build stage ----
FROM golang:1.26-alpine AS builder

RUN apk add --no-cache git ca-certificates

ENV CGO_ENABLED=0 \
    GOPRIVATE=github.com/razorpay/* \
    GOFLAGS=-mod=mod

WORKDIR /src

# Dependency manifests first for layer caching.
COPY go.mod go.sum ./

# BuildKit secret mount: /run/secrets/netrc exists ONLY for this RUN's
# process tree and is never written to an image layer. Expected file
# contents: a standard `.netrc` granting `machine github.com login <user>
# password <token>` (build.sh documents how to produce this from
# `gh auth token`).
RUN --mount=type=secret,id=netrc,required=true \
    cp /run/secrets/netrc /root/.netrc && chmod 600 /root/.netrc && \
    go mod download && \
    rm -f /root/.netrc

COPY . .

RUN mkdir -p /out && \
    go build -trimpath -ldflags="-s -w" -o /out/payouts-api ./cmd/api && \
    go build -trimpath -ldflags="-s -w" -o /out/payouts-workers ./cmd/workers && \
    go build -trimpath -ldflags="-s -w" -o /out/payouts-kafka-consumer ./cmd/kafkaConsumers && \
    go build -trimpath -ldflags="-s -w" -o /out/payouts-migration ./cmd/migration

# ---- runtime stage: no credentials, no source, no build toolchain ----
FROM alpine:3.21 AS runtime

RUN apk add --no-cache ca-certificates wget busybox-extras && \
    adduser -D -u 10001 appuser

COPY --from=builder /out/payouts-api /out/payouts-workers /out/payouts-kafka-consumer /out/payouts-migration /app/

USER appuser
EXPOSE 9400 8001
ENTRYPOINT ["/app/payouts-api"]
