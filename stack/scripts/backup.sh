#!/usr/bin/env bash
# Dump Postgres + tar every volume + the RAG data dir into backups/<timestamp>.tar.gz
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a
TS=$(date -u +%Y%m%dT%H%M%SZ); OUT=backups/$TS; mkdir -p "$OUT"
echo "pg_dump ..."; docker compose exec -T postgres pg_dump -U "${POSTGRES_USER:-litellm}" "${POSTGRES_DB:-litellm}" | gzip > "$OUT/postgres.sql.gz"
for v in open_webui_data; do
  echo "volume $v ..."
  docker run --rm -v "private-ai-stack_${v}:/from:ro" -v "$PWD/$OUT:/to" alpine sh -c "cd /from && tar czf /to/${v}.tar.gz ."
done
echo "rag data ..."; tar czf "$OUT/rag-data.tar.gz" -C data .
cp .env "$OUT/env.backup"; chmod 600 "$OUT/env.backup"
tar czf "backups/$TS.tar.gz" -C backups "$TS" && rm -rf "$OUT"
echo "wrote backups/$TS.tar.gz ($(du -h "backups/$TS.tar.gz" | cut -f1))"
