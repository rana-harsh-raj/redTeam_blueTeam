#!/bin/sh
set -eu
S2P_DIR=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
[ ! -f "$S2P_DIR/.build/runtime-image.env" ] || . "$S2P_DIR/.build/runtime-image.env"
export S2P_SOURCE_IMAGE=${S2P_SOURCE_IMAGE:-}
export COMPOSE_PROJECT_NAME=${COMPOSE_PROJECT_NAME:-s2p_architecture_replica}
case "$COMPOSE_PROJECT_NAME" in
  s2p_*) ;;
  *) echo "refusing non-S2P Compose project: $COMPOSE_PROJECT_NAME" >&2; exit 64 ;;
esac
compose() { docker compose -f "$S2P_DIR/docker-compose.yml" "$@"; }
guard_project_ownership() {
  ids=$(docker ps -aq --filter "label=com.docker.compose.project=$COMPOSE_PROJECT_NAME")
  [ -z "$ids" ] && return 0
  for id in $ids; do
    config=$(docker inspect -f '{{ index .Config.Labels "com.docker.compose.project.config_files" }}' "$id")
    case "$config" in *"$S2P_DIR/docker-compose.yml"*) ;; *) echo "refusing foreign container $id in project $COMPOSE_PROJECT_NAME" >&2; exit 65;; esac
  done
}
