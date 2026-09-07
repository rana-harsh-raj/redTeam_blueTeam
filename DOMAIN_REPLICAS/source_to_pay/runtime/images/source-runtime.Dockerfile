# syntax=docker/dockerfile:1.7
FROM alpine:3.22.1@sha256:4bcff63911fcb4448bd4fdacec207030997caf25e9bea4045fa6c8c44de311d1
ARG BINARY
ARG MIGRATIONS
COPY ${BINARY} /service
COPY ${MIGRATIONS}/ /app/internal/migrations/
WORKDIR /app
USER 65532:65532
ENTRYPOINT ["/service"]
