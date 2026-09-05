#!/bin/sh
# Arena-only wrapper: cfa's Mongo client skips TLS only when Endpoint is localhost/127.0.0.1
# (cfa/pkg/storage/mongodb/mongo.go:64). Forward 127.0.0.1:27017 -> mongo-cfa:27017 inside the
# container so the real client code runs unmodified without TLS on the internal network.
socat TCP-LISTEN:27017,bind=127.0.0.1,fork,reuseaddr TCP:${CFA_MONGO_HOST:-mongo-cfa}:${CFA_MONGO_PORT:-27017} &
exec "$@"
