#!/usr/bin/env bash
# Load each source into its OWN database on the Zetaris bundle's Postgres, so
# Zetaris registers four separate systems. The local platform reads files only
# from S3, which is why CSVs go through Postgres.
# Usage (repo root, after python zetaris/export_csv.py):
#   ZETARIS_BUNDLE=/path/to/zetaris-platform ./zetaris/load_postgres.sh
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
BUNDLE="${ZETARIS_BUNDLE:?Set ZETARIS_BUNDLE to the zetaris-platform folder}"
DATA="$HERE/runtime/zetaris"
cd "$BUNDLE"
PG="docker compose exec -T postgres psql -U zetaris -v ON_ERROR_STOP=1"

ddl() {
  case "$1" in
    accounts) echo "row_id text, discord_id text, client_version text, last_working_version text, payload text" ;;
    issues)   echo "row_id text, component text, state text, affects_version text, payload text" ;;
    *)        echo "row_id text, component text, payload text" ;;
  esac
}

for table in accounts kb releases issues; do
  db="resolve_$table"
  $PG -d metastore -tc "SELECT 1 FROM pg_database WHERE datname='$db'" | grep -q 1 \
    || $PG -d metastore -c "CREATE DATABASE $db"
  $PG -d "$db" -c "DROP TABLE IF EXISTS $table; CREATE TABLE $table ($(ddl $table));"
  docker compose exec -T postgres psql -U zetaris -d "$db" \
    -c "\\copy $table FROM STDIN WITH (FORMAT csv, HEADER true)" < "$DATA/$table.csv"
  $PG -d "$db" -tc "SELECT '$table', count(*) FROM $table"
done
