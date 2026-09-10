# build/mozart.Dockerfile — builds the REAL `mozart` binary to run in its
# own built-in `-mock` mode (mozart/app/mock/, `main.go` `flag.Bool("mock",...)`).
#
# CONFIRMED BLOCKED in the Env 2 substitutes pass: `go build` of a real
# `mozart` clone fails on the private module
# `github.com/razorpay/integrations-utils` (404 for the build identity
# available in that environment). `mozart-sim` (substitutes/mozart-sim/,
# pure Python stdlib) is the PRIMARY Mozart substitute for this arena --
# see its CONTRACT.md. This Dockerfile is kept, unmodified, for the
# `mozart-real` compose profile (opt-in, not depended on by `core` or
# `substitutes`) in case a working `integrations-utils` credential is
# available in a future environment. See
# findings/25_stork_mozart.md §B.4/§C.1 for why the real binary would beat
# a hand-rolled simulator if it could be built (identical routing/
# error-handling pipeline, real fixture set).
#
# Build context MUST be an absolute path to a real `mozart` repo clone
# (set MOZART_BUILD_CONTEXT in .env.arena; build.sh reads it). This
# Dockerfile itself lives in ENV2_COMPOSE and is passed via -f / the
# compose `dockerfile:` field so the mozart clone itself is never modified.
#
# go.mod: module github.com/razorpay/mozart, go 1.25.1.
#
# ASSUMPTION (flagged, not confirmed by running it -- see
# findings/29_env2_scaffold.md): `-mock` mode reads its LISTEN_PORT/LISTEN_IP
# from the same TOML config as normal boot (mozart/app/environment/reader.go),
# selected by an `env.<name>.toml` file under `conf/`. This Dockerfile copies
# in an arena-specific `conf/env.arena.toml` (rendered by
# config/generate.py, mounted at runtime under /config, NOT baked in --
# see docker-compose.yml's mozart-mock volume) and assumes `APP_ENV=arena`
# selects it; confirm against reader.go before relying on this in a real run.

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
    go build -trimpath -ldflags="-s -w" -o /out/mozart .

# ---- runtime stage ----
# Fixtures (mozart/app/testdata/fts/**, ~76MB) are real, public-repo test
# data (no credentials) -- copied in so `-mock` has scenario fixtures to
# replay. This is the one runtime image in this scaffold that is
# meaningfully large; acceptable since it's a substitute, not a real service.
FROM alpine:3.21 AS runtime

RUN apk add --no-cache ca-certificates busybox-extras wget && \
    adduser -D -u 10001 appuser

WORKDIR /app
COPY --from=builder /out/mozart /app/mozart
COPY --from=builder /src/app/testdata /app/app/testdata
# conf/ is intentionally NOT copied from the builder stage (it may contain
# real `[secrets.<gateway>]` structure with env-var placeholders resolving
# to real Vault paths in a real deployment) -- the arena's own
# conf/env.arena.toml is bind-mounted at runtime instead (docker-compose.yml
# mozart-mock volume: ./generated/mozart-mock:/config:ro), never baked in.
RUN mkdir -p /app/conf

USER appuser
EXPOSE 8085
ENTRYPOINT ["/app/mozart", "-mock"]
