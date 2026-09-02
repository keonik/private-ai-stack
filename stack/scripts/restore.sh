#!/usr/bin/env bash
# Restore from a backup tarball made by backup.sh. Stops the app services, replaces the database,
# the Open WebUI volume, and the RAG data, then restarts. Services are restarted even if a step fails.
#   ./scripts/restore.sh backups/20260902T030000Z.tar.gz
set -euo pipefail
cd "$(dirname "$0")/.."
FILE=${1:?backup tarball}
set -a; . ./.env; set +a
PGU=${POSTGRES_USER:-litellm}; PGD=${POSTGRES_DB:-litellm}
# tmp dir under the project so Docker Desktop can bind-mount it (macOS /var/folders is not shared)
TMP=$(mktemp -d "$PWD/backups/.restore.XXXXXX"); trap 'docker compose start litellm rag-ingest open-webui >/dev/null 2>&1 || true; rm -rf "$TMP"' EXIT
tar xzf "$FILE" -C "$TMP"; SRC=$(find "$TMP" -mindepth 1 -maxdepth 1 -type d | head -1)
[ -f "$SRC/postgres.sql.gz" ] || { echo "not a backup tarball: $FILE" >&2; exit 1; }
docker compose stop open-webui rag-ingest litellm
echo "postgres: recreate $PGD ..."
docker compose exec -T postgres psql -U "$PGU" -d postgres -v ON_ERROR_STOP=1 -q \
  -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='$PGD' AND pid<>pg_backend_pid();" \
  -c "DROP DATABASE IF EXISTS \"$PGD\";" -c "CREATE DATABASE \"$PGD\";" >/dev/null
gunzip -c "$SRC/postgres.sql.gz" | docker compose exec -T postgres psql -U "$PGU" -d "$PGD" -q -v ON_ERROR_STOP=0 >/dev/null 2>"$TMP/psql.err" || true
n=$(grep -c ERROR "$TMP/psql.err" || true); echo "  psql errors: $n (0 expected)"
echo "open-webui volume ..."; docker run --rm -v private-ai-stack_open_webui_data:/to -v "$SRC:/from:ro" alpine sh -c "rm -rf /to/* /to/.[!.]* 2>/dev/null; tar xzf /from/open_webui_data.tar.gz -C /to"
echo "rag data ..."; find data -mindepth 1 -delete; tar xzf "$SRC/rag-data.tar.gz" -C data
echo "restored from $FILE; restarting services"
