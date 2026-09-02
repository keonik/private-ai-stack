#!/usr/bin/env bash
# Restore from a backup tarball made by backup.sh. Stops the stack, restores, restarts.
#   ./scripts/restore.sh backups/20260902T030000Z.tar.gz
set -euo pipefail
cd "$(dirname "$0")/.."
FILE=${1:?backup tarball}; TMP=$(mktemp -d); tar xzf "$FILE" -C "$TMP"; SRC=$(ls -d "$TMP"/*/)
set -a; . ./.env; set +a
docker compose stop open-webui rag-ingest litellm
echo "postgres ..."; gunzip -c "$SRC/postgres.sql.gz" | docker compose exec -T postgres psql -U "${POSTGRES_USER:-litellm}" "${POSTGRES_DB:-litellm}" >/dev/null
echo "open-webui volume ..."; docker run --rm -v private-ai-stack_open_webui_data:/to -v "$SRC:/from:ro" alpine sh -c "rm -rf /to/* && tar xzf /from/open_webui_data.tar.gz -C /to"
echo "rag data ..."; rm -rf data/* && tar xzf "$SRC/rag-data.tar.gz" -C data
docker compose start litellm rag-ingest open-webui
rm -rf "$TMP"; echo "restored from $FILE"
