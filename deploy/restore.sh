#!/bin/sh
# Restaura una copia de seguridad de PRODUCCIÓN.
#
#   sh deploy/restore.sh backups/db-AAAAMMDD-HHMMSS.sql.gz [backups/evidence-AAAAMMDD-HHMMSS.tar.gz]
#
# REEMPLAZA la base de datos actual por la de la copia. Antes de hacerlo guarda
# una copia de lo que hay ahora, para poder volver atrás.
set -eu

DB_FILE="${1:?Uso: sh deploy/restore.sh backups/db-....sql.gz [backups/evidence-....tar.gz]}"
EVIDENCE_FILE="${2:-}"
# Se puede sustituir desde fuera para ensayar con otro nombre de proyecto.
COMPOSE="${COMPOSE:-docker compose -f docker-compose.prod.yml --env-file deploy/.env.production}"
export COMPOSE

[ -f "$DB_FILE" ] || { echo "No existe $DB_FILE" >&2; exit 1; }
gzip -t "$DB_FILE" || { echo "$DB_FILE está dañado." >&2; exit 1; }
# Los archivos se comprueban AHORA, antes de tocar nada. Mirarlos después de
# reemplazar la base dejaba, con un nombre mal escrito, la tienda detenida, los
# datos anteriores borrados y los archivos sin restaurar.
if [ -n "$EVIDENCE_FILE" ]; then
  [ -f "$EVIDENCE_FILE" ] || { echo "No existe $EVIDENCE_FILE" >&2; exit 1; }
  { gzip -t "$EVIDENCE_FILE" 2>/dev/null && tar -tzf "$EVIDENCE_FILE" > /dev/null 2>&1; } \
    || { echo "$EVIDENCE_FILE está dañado." >&2; exit 1; }
fi

echo "Se va a REEMPLAZAR la base de datos de producción con: $DB_FILE"
printf "Escribe RESTAURAR para continuar: "
read -r answer
[ "$answer" = "RESTAURAR" ] || { echo "Cancelado. No se cambió nada."; exit 1; }

echo "1/4 Copia de lo que hay ahora…"
sh deploy/backup.sh

echo "2/4 Deteniendo la web (la base sigue encendida)…"
$COMPOSE stop caddy frontend backend

echo "3/4 Restaurando la base de datos…"
$COMPOSE exec -T postgres sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d postgres -c "DROP DATABASE IF EXISTS \"$POSTGRES_DB\" WITH (FORCE);" -c "CREATE DATABASE \"$POSTGRES_DB\" OWNER \"$POSTGRES_USER\";"'
gzip -dc "$DB_FILE" | $COMPOSE exec -T postgres sh -c 'psql -v ON_ERROR_STOP=1 -q -U "$POSTGRES_USER" -d "$POSTGRES_DB"' > /dev/null

if [ -n "$EVIDENCE_FILE" ]; then
  echo "    Restaurando archivos (imágenes y evidencias)…"
  $COMPOSE run --rm -T --no-deps --user root backend sh -c 'tar -xzf - -C /app && chown -R app:app /app/private-media' < "$EVIDENCE_FILE"
fi

echo "4/4 Encendiendo la web…"
$COMPOSE up -d

echo "Restaurado. Comprueba la tienda y el panel."
