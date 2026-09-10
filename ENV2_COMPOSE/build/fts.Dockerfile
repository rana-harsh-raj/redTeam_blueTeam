# build/fts.Dockerfile — multi-stage build for the `fts` repo.
#
# Facts: go.mod `module github.com/razorpay/fts`, `go 1.24.0`; entrypoints
# cmd/web, cmd/worker, cmd/migration. Real fts fronts the Go binary (which
# listens on LISTEN_IP:LISTEN_PORT, default 127.0.0.1:8080 -- MUST be
# overridden to 0.0.0.0:8080 by config/generate.py) with nginx on :80 (public)
# and a metrics-only :8081 (see build/fts-nginx.conf, an arena-adjusted
# reconstruction of the shape described in fts/build/nginx.conf -- the real
# file is not read verbatim/vendored). Migration binary takes `--env=` and
# `--mode=` flags (findings/29), not just an env var.
#
# Same secret-mount build/runtime separation as build/payouts.Dockerfile.

# ---- build stage ----
FROM golang:1.24-alpine AS builder

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
    go build -trimpath -ldflags="-s -w" -o /out/fts-web ./cmd/web && \
    go build -trimpath -ldflags="-s -w" -o /out/fts-worker ./cmd/worker && \
    go build -trimpath -ldflags="-s -w" -o /out/fts-migrate ./cmd/migration

# ---- runtime stage: alpine + nginx, no credentials/source/toolchain ----
FROM alpine:3.21 AS runtime

RUN apk add --no-cache ca-certificates nginx busybox-extras wget && \
    adduser -D -u 10001 appuser && \
    mkdir -p /tmp/client_body /tmp/proxy /tmp/fastcgi /tmp/uwsgi /tmp/scgi && \
    chown -R appuser:appuser /tmp /var/lib/nginx 2>/dev/null || true

COPY --from=builder /out/fts-web /out/fts-worker /out/fts-migrate /app/
COPY build/fts-nginx.conf /etc/nginx/nginx.conf
COPY build/fts-entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# NOTE: nginx needs to bind port 80, which on most images requires root or
# CAP_NET_BIND_SERVICE -- this image intentionally does NOT switch to
# appuser for the web command (nginx drops privileges internally via its
# own worker_processes model is not configured here since we run a single
# foreground master); docker-compose.yml compensates by NOT setting a
# non-root `user:` override for fts-web and instead relies on
# `no-new-privileges` + a read-only-adjacent tmpfs set, consistent with
# fts-web's `runtime-security` anchor already only adding tmpfs+seccomp,
# not `user:`. fts-worker has no such constraint and could run as appuser;
# left uniform for simplicity in this scaffold pass.
EXPOSE 80 8081 8080
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["web"]
