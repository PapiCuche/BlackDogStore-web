#!/bin/sh
# Estado de PRODUCCIÓN, en una pasada.
#
# Se ejecuta EN EL SERVIDOR, desde la raíz del repositorio:
#   sh deploy/healthcheck.sh
#
# Escribe una línea por comprobación y termina con 0 si todo está bien, o con 1
# si algo necesita a una persona. Pensado para el `crontab` (ver
# deploy/crontab.example): lo que imprime es lo que hay que leer.
#
#   · los cuatro contenedores están encendidos y sanos;
#   · la tienda y la API responden por su dirección pública;
#   · la base de datos responde;
#   · la aplicación no tiene migraciones sin aplicar, notificaciones de pago
#     rechazadas, pagos esperando ni avisos de WhatsApp fallidos o atrasados
#     (`python manage.py ops_status`);
#   · la última copia de seguridad completa es reciente;
#   · queda espacio en disco.
#
# No arregla nada y no cambia nada: sólo mira.
set -u

# Se puede sustituir desde fuera para ensayar con otro nombre de proyecto.
COMPOSE="${COMPOSE:-docker compose -f docker-compose.prod.yml --env-file deploy/.env.production}"
ENV_FILE="${ENV_FILE:-deploy/.env.production}"
DEST="${BACKUP_DIR:-backups}"
MAX_AGE_HOURS="${BACKUP_MAX_AGE_HOURS:-26}"
DISK_MAX_PERCENT="${DISK_MAX_PERCENT:-90}"
# El valor puede venir entre comillas en el archivo de variables: Compose las quita, y aquí también.
DOMAIN="${SITE_DOMAIN:-$(grep '^SITE_DOMAIN=' "$ENV_FILE" 2>/dev/null | cut -d= -f2- | tr -d "\"'\r ")}"
CURL="${HEALTH_CURL:-curl -s --max-time 15}"

FAILS=0
ok() { echo "OK    $*"; }
bad() { echo "ATENCIÓN $*"; FAILS=$((FAILS + 1)); }

STATES=$($COMPOSE ps --format '{{.Service}} {{.State}} {{.Health}}' 2>/dev/null)
for service in postgres backend frontend caddy; do
  state=$(printf '%s\n' "$STATES" | awk -v s="$service" '$1 == s {print $2 ($3 == "" ? "" : " " $3)}')
  case "$state" in
    "running healthy"|"running") ok "contenedor $service" ;;
    *) bad "contenedor $service: ${state:-no está}" ;;
  esac
done

for path in / /api/categories; do
  code=$($CURL -o /dev/null -w '%{http_code}' "https://$DOMAIN$path" 2>/dev/null)
  if [ "$code" = 200 ]; then ok "https://$DOMAIN$path"; else bad "https://$DOMAIN$path responde ${code:-nada}"; fi
done

if $COMPOSE exec -T postgres sh -c 'pg_isready -q -U "$POSTGRES_USER" -d "$POSTGRES_DB"' > /dev/null 2>&1; then
  ok "base de datos"
else
  bad "la base de datos no responde"
fi

# La aplicación informa de lo suyo, una línea por asunto. Lo que diga se copia tal cual.
REPORT=$($COMPOSE exec -T backend python manage.py ops_status 2>&1); rc=$?
LINES=$(printf '%s\n' "$REPORT" | grep -E '^(OK|NOTA|ATENCIÓN) ')
[ -n "$LINES" ] && printf '%s\n' "$LINES"
ATTENTION=$(printf '%s\n' "$LINES" | grep -c '^ATENCIÓN ')
FAILS=$((FAILS + ATTENTION))
if [ "$rc" -ne 0 ] && [ "$ATTENTION" -eq 0 ]; then
  bad "la aplicación no pudo informar de su estado (python manage.py ops_status terminó con $rc)"
fi

if [ ! -f "$DEST/LAST_OK" ]; then
  bad "copia de seguridad: no hay ninguna copia completa en $DEST (sh deploy/backup.sh)"
elif [ -n "$(find "$DEST/LAST_OK" -mmin +$((MAX_AGE_HOURS * 60)) 2>/dev/null)" ]; then
  bad "copia de seguridad: la última copia completa tiene más de $MAX_AGE_HOURS horas ($(cat "$DEST/LAST_OK"))"
else
  ok "copia de seguridad ($(cat "$DEST/LAST_OK"))"
fi

USED=$(df -P . 2>/dev/null | awk 'NR == 2 {gsub("%", "", $5); print $5}')
if [ -n "$USED" ] && [ "$USED" -lt "$DISK_MAX_PERCENT" ]; then
  ok "disco ($USED % usado)"
else
  bad "disco: ${USED:-?} % usado (aviso a partir de $DISK_MAX_PERCENT %)"
fi

[ "$FAILS" -eq 0 ]
