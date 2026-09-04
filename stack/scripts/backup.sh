#!/usr/bin/env bash
# Dump Postgres (--clean so restore replaces, not appends) + tar every volume + the RAG data dir
# into backups/<timestamp>.tar.gz. Safe to run while the stack is up.
# KEEP=n keeps only the newest n tarballs (default 7; 0 = keep all).
set -euo pipefail
cd "$(dirname "$0")/.."
KEEP=${KEEP:-7}
set -a; . ./.env; set +a
TS=$(date -u +%Y%m%dT%H%M%SZ); OUT=backups/$TS; mkdir -p "$OUT"
echo "pg_dump ..."; docker compose exec -T postgres pg_dump --clean --if-exists -U "${POSTGRES_USER:-litellm}" "${POSTGRES_DB:-litellm}" | gzip > "$OUT/postgres.sql.gz"
for v in open_webui_data; do
  echo "volume $v ..."
  docker run --rm -v "private-ai-stack_${v}:/from:ro" -v "$PWD/$OUT:/to" alpine sh -c "cd /from && tar czf /to/${v}.tar.gz ."
done
echo "rag data ..."; tar czf "$OUT/rag-data.tar.gz" -C data .
cp .env "$OUT/env.backup"; chmod 600 "$OUT/env.backup"
tar czf "backups/$TS.tar.gz" -C backups "$TS" && rm -rf "$OUT"
echo "wrote backups/$TS.tar.gz ($(du -h "backups/$TS.tar.gz" | cut -f1))"
if [ "$KEEP" -gt 0 ]; then
  ls -1t backups/*.tar.gz 2>/dev/null | tail -n +$((KEEP + 1)) | while read -r old; do echo "prune $old"; rm -f "$old"; done
fi
