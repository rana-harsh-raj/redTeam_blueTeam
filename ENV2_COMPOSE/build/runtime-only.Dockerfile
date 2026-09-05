# build/runtime-only.Dockerfile — generic credential-free runtime image used
# by build-host.sh: the build context is a staging directory containing
# ONLY host-built Go binaries (no source, no go.mod, no secrets), so this
# Dockerfile has no build stage at all -- there is nothing to separate a
# credential from, because nothing here ever touched a credential.
FROM alpine:3.21

RUN apk add --no-cache ca-certificates tzdata busybox-extras wget socat && \
    adduser -D -u 10001 appuser

COPY . /app/
RUN chmod +x /app/* 2>/dev/null || true

USER appuser
WORKDIR /app
