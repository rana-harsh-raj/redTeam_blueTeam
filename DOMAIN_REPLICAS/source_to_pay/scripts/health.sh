#!/bin/sh
set -eu
. "$(dirname "$0")/_common.sh"
compose exec -T monolith-boundary python -c "import urllib.request,json; assert json.load(urllib.request.urlopen('http://localhost:8080/health'))['status']=='ok'"
compose exec -T mysql env MYSQL_PWD=s2p mysql -us2p -Nse 'SELECT 1' s2p | grep -qx 1
compose exec -T kafka rpk cluster health --exit-when-healthy >/dev/null
compose exec -T redis redis-cli ping | grep -qx PONG
compose exec -T monolith-boundary python -c "import urllib.request,json; x=json.load(urllib.request.urlopen('http://vp-source:8080/health')); assert x['ready'] and x['source_sha']=='20c4f4d59970471067388afea8b1d65ac39ee126'"
