.PHONY: up down logs ps backup restore models check

up:        ; cd stack && docker compose up -d
down:      ; cd stack && docker compose down
logs:      ; cd stack && docker compose logs -f --tail=100
ps:        ; cd stack && docker compose ps
models:    ; cd stack && ./scripts/pull-models.sh
backup:    ; cd stack && ./scripts/backup.sh
restore:   ; cd stack && ./scripts/restore.sh $(FILE)
check:     ; cd stack && docker compose config -q && echo "compose: ok" && ./scripts/healthcheck.sh
