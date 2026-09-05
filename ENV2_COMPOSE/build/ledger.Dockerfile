# build/ledger.Dockerfile — multi-stage build for the `ledger` repo.
#
# Facts: go.mod `module github.com/razorpay/ledger`, `go 1.25.0`; entrypoints
# cmd/api, cmd/worker, cmd/scheduler, cmd/migration (repo also has
# worker_kafka/worker_makeshift_txn/outbox_relay/etc -- out of scope for
# Env 2's core profile, which only needs api/worker/scheduler per the task
# brief). Server binds `127.0.0.1:8080` in ledger/config/default.toml --
# config/generate.py MUST override the bind host to 0.0.0.0, or the
# container is unreachable even though the process is healthy.
#
# Same build/runtime separation as build/payouts.Dockerfile -- see that
# file's header comment for the secret-mount contract, identical here.

# ---- build stage ----
FROM golang:1.25-alpine AS builder

RUN apk add --no-cache git ca-certificates

ENV CGO_ENABLED=0 \
    GOPRIVATE=github.com/razorpay/* \
    GOFLAGS=-mod=mod

WORKDIR /src

COPY go.mod go.sum ./

RUN --mount=type=secret,id=netrc,required=true \
    cp /run/secrets/netrc /root/.netrc && chmod 600 /root/.netrc && \
    go mod download && \
    rm -f /root/.netrc

COPY . .

RUN mkdir -p /out && \
    go build -trimpath -ldflags="-s -w" -o /out/ledger-api ./cmd/api && \
    go build -trimpath -ldflags="-s -w" -o /out/ledger-worker ./cmd/worker && \
    go build -trimpath -ldflags="-s -w" -o /out/ledger-scheduler ./cmd/scheduler && \
    go build -trimpath -ldflags="-s -w" -o /out/ledger-migration ./cmd/migration

# ---- runtime stage ----
FROM alpine:3.21 AS runtime

RUN apk add --no-cache ca-certificates wget busybox-extras && \
    adduser -D -u 10001 appuser

COPY --from=builder /out/ledger-api /out/ledger-worker /out/ledger-scheduler /out/ledger-migration /app/

USER appuser
EXPOSE 8080
ENTRYPOINT ["/app/ledger-api"]
