#!/bin/sh
# Copia de seguridad de PRODUCCIÓN: base de datos + evidencias.
#
# Se ejecuta EN EL SERVIDOR, desde la raíz del repositorio:
#   sh deploy/backup.sh
#
# Deja en ./backups/ (fuera de git):
#   db-AAAAMMDD-HHMMSS.sql.gz        volcado completo de PostgreSQL
#   evidence-AAAAMMDD-HHMMSS.tar.gz  fotos de evidencia del servicio técnico
#
# y borra las copias con más de BACKUP_KEEP_DAYS días (14 por defecto).
#
# UNA COPIA EN EL MISMO SERVIDOR NO ES UNA COPIA. Si el disco o el servidor se
# pierden, se pierden las dos cosas. Hay que sacarla fuera: ver «Copia externa»
# en docs/despliegue-produccion.md.
set -eu

COMPOSE="docker compose -f docker-compose.prod.yml --env-file deploy/.env.production"
DEST="${BACKUP_DIR:-backups}"
KEEP="${BACKUP_KEEP_DAYS:-14}"
STAMP="$(date +%Y%m%d-%H%M%S)"

mkdir -p "$DEST"
umask 077

# El volcado se escribe con otro nombre y sólo se renombra si terminó bien: un
# archivo `db-*.sql.gz` en la carpeta es siempre un volcado completo.
TMP="$DEST/.db-$STAMP.sql.gz.partial"
$COMPOSE exec -T postgres sh -c 'pg_dump --no-owner --no-privileges -U "$POSTGRES_USER" "$POSTGRES_DB"' | gzip > "$TMP"
# Un volcado válido termina con esta marca. Sin ella, pg_dump se cortó.
if ! gzip -dc "$TMP" | tail -n 5 | grep -q "PostgreSQL database dump complete"; then
  rm -f "$TMP"
  echo "ERROR: el volcado de la base de datos quedó incompleto. No se guardó." >&2
  exit 1
fi
mv "$TMP" "$DEST/db-$STAMP.sql.gz"

$COMPOSE exec -T backend tar -czf - -C /app private-media > "$DEST/evidence-$STAMP.tar.gz"

find "$DEST" -maxdepth 1 -type f \( -name 'db-*.sql.gz' -o -name 'evidence-*.tar.gz' \) -mtime +"$KEEP" -delete

echo "Copia guardada en $DEST:"
ls -lh "$DEST/db-$STAMP.sql.gz" "$DEST/evidence-$STAMP.tar.gz"
