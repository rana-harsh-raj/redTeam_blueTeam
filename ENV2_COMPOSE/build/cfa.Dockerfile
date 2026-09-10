# build/cfa.Dockerfile — multi-stage build for the `cfa` repo.
#
# Facts: go.mod `module github.com/razorpay/cfa`, `go 1.24.0`; entrypoints
# cmd/server, cmd/worker, cmd/migration. Ports (confirmed,
# config/default.toml [Server.ServerAddresses]): Grpc=":8080", Http=":8081",
# Internal=":8082". Store is MongoDB ([Store.MongoDB]).
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
    go build -trimpath -ldflags="-s -w" -o /out/cfa-server ./cmd/server && \
    go build -trimpath -ldflags="-s -w" -o /out/cfa-worker ./cmd/worker && \
    go build -trimpath -ldflags="-s -w" -o /out/cfa-migration ./cmd/migration

# ---- runtime stage ----
FROM alpine:3.21 AS runtime

RUN apk add --no-cache ca-certificates busybox-extras wget && \
    adduser -D -u 10001 appuser

COPY --from=builder /out/cfa-server /out/cfa-worker /out/cfa-migration /app/

USER appuser
EXPOSE 8080 8081 8082
ENTRYPOINT ["/app/cfa-server"]
