#!/usr/bin/env bash
# Run after core migrations and API schema creation, before monolith starts.
set -euo pipefail
cd "$(dirname "$0")/.."
compose() { docker compose --env-file .env.arena -f docker-compose.yml "$@"; }
provision() {
  local service="$1" user="$2" credential="$3" grant="$4" root_file="$5"
  local password
  password="$(cat "secrets/$credential.txt")"
  # The generator emits base64url. Restrict interpolation to that exact alphabet.
  [[ "$password" =~ ^[A-Za-z0-9_-]{32,}$ ]] || { echo 'Invalid generated credential format' >&2; exit 1; }
  {
    printf "CREATE USER IF NOT EXISTS '%s'@'%%' IDENTIFIED WITH mysql_native_password BY '%s';\n" "$user" "$password"
    printf "ALTER USER '%s'@'%%' IDENTIFIED WITH mysql_native_password BY '%s';\n" "$user" "$password"
    printf "REVOKE ALL PRIVILEGES, GRANT OPTION FROM '%s'@'%%';\n" "$user"
    printf '%s\n' "$grant"
  } | compose exec -T "$service" sh -c 'export MYSQL_PWD="$(cat "$1")"; exec mysql -uroot' sh "$root_file"
}
provision mysql-payouts monolith_reader auth_monolith_payouts_db \
  "GRANT SELECT ON payouts.payouts TO 'monolith_reader'@'%';" /run/secrets/mysql_payouts_root_password
provision mysql-apidb-stub monolith_balance auth_monolith_balance_db \
  "GRANT SELECT, UPDATE (balance, updated_at) ON api_local.balance TO 'monolith_balance'@'%';" /run/secrets/mysql_apidb_root_password
