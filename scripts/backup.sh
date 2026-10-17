#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
mkdir -p backups
chmod 700 backups
name="arcana-$(date -u +%Y%m%d-%H%M%S).dump"
docker compose exec -T db pg_dump -U arcana -d arcana -Fc -f "/backups/$name"
find backups -maxdepth 1 -type f -name 'arcana-*.dump' -mtime +6 -delete
printf '%s\n' "Saved backups/$name; retain runtime/deletions.log independently."
