# build/xbalances.Dockerfile — multi-stage build for the `x-balances` repo.
#
# Facts: go.mod `module github.com/razorpay/x-balances`, `go 1.25.0`;
# entrypoints cmd/server, cmd/worker, cmd/migration. Ports (confirmed,
# same shape as cfa): Grpc=":8080", Http=":8081", Internal=":8082". Store is
# MySQL ([Store.Sql]) plus a second MySQL connection into the api-monolith
# DB ([APIStore.Sql], points at mysql-apidb-stub in this arena).
#
# Same secret-mount build/runtime separation as build/payouts.Dockerfile.

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
    go build -trimpath -ldflags="-s -w" -o /out/xbalances-server ./cmd/server && \
    go build -trimpath -ldflags="-s -w" -o /out/xbalances-worker ./cmd/worker && \
    go build -trimpath -ldflags="-s -w" -o /out/xbalances-migration ./cmd/migration

# ---- runtime stage ----
FROM alpine:3.21 AS runtime

RUN apk add --no-cache ca-certificates busybox-extras wget && \
    adduser -D -u 10001 appuser

COPY --from=builder /out/xbalances-server /out/xbalances-worker /out/xbalances-migration /app/

USER appuser
EXPOSE 8080 8081 8082
ENTRYPOINT ["/app/xbalances-server"]
