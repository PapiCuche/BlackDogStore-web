#!/bin/sh
# Copia de seguridad de PRODUCCIÓN: base de datos + archivos subidos.
#
# Se ejecuta EN EL SERVIDOR, desde la raíz del repositorio:
#   sh deploy/backup.sh
#
# Deja en ./backups/ (fuera de git):
#   db-AAAAMMDD-HHMMSS.sql.gz        volcado completo de PostgreSQL
#   evidence-AAAAMMDD-HHMMSS.tar.gz  todo el almacén de archivos: fotos de producto,
#                                    imágenes de la tienda, logotipos y evidencias
#   LAST_OK                          la hora de la última copia completa
#
# NO copia `deploy/.env.production`. Ese archivo se guarda aparte, en un gestor de
# contraseñas: una copia de seguridad con las claves dentro es un secreto más que
# custodiar. Sin su SECRET_KEY, los enlaces de seguimiento ya enviados a los
# clientes dejan de abrir.
#
# y borra las copias con más de BACKUP_KEEP_DAYS días (14 por defecto).
#
# UNA COPIA EN EL MISMO SERVIDOR NO ES UNA COPIA. Si el disco o el servidor se
# pierden, se pierden las dos cosas. Hay que sacarla fuera: ver «Copia externa»
# en docs/despliegue-produccion.md.
set -eu

# Se puede sustituir desde fuera para ensayar con otro nombre de proyecto.
COMPOSE="${COMPOSE:-docker compose -f docker-compose.prod.yml --env-file deploy/.env.production}"
export COMPOSE
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

# Lo mismo con los archivos: se escriben con otro nombre y sólo se renombran si
# el archivo está entero y se puede leer. Uno cortado a medias no debe quedar en
# la carpeta con aspecto de copia.
FILES_TMP="$DEST/.evidence-$STAMP.tar.gz.partial"
if ! $COMPOSE exec -T backend tar -czf - -C /app private-media > "$FILES_TMP" \
   || ! gzip -t "$FILES_TMP" 2>/dev/null \
   || ! tar -tzf "$FILES_TMP" > /dev/null 2>&1; then
  rm -f "$FILES_TMP"
  echo "ERROR: el archivo de imágenes y evidencias quedó incompleto. No se guardó." >&2
  exit 1
fi
mv "$FILES_TMP" "$DEST/evidence-$STAMP.tar.gz"

# La hora de la última copia COMPLETA. Sólo se escribe aquí, cuando las dos
# partes terminaron bien: es lo que mira `deploy/healthcheck.sh` para avisar de
# que las copias dejaron de hacerse.
printf '%s\n' "$STAMP" > "$DEST/LAST_OK"

find "$DEST" -maxdepth 1 -type f \( -name 'db-*.sql.gz' -o -name 'evidence-*.tar.gz' \) -mtime +"$KEEP" -delete

echo "Copia guardada en $DEST:"
ls -lh "$DEST/db-$STAMP.sql.gz" "$DEST/evidence-$STAMP.tar.gz"
