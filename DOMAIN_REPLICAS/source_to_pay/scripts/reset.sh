#!/bin/sh
set -eu
. "$(dirname "$0")/_common.sh"
guard_project_ownership
compose down --volumes --remove-orphans
