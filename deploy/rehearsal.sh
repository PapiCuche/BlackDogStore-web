#!/bin/sh
# Ensayo completo de producción, en este equipo y sin salir a Internet.
#
#   sh deploy/rehearsal.sh
#
# Levanta la pila de `docker-compose.prod.yml` desde cero con un proyecto, unos
# puertos y un dominio reservados para el ensayo (`bds-rehearsal`, 18080/18443,
# `tienda.test`), la recorre como lo haría un cliente y la desmonta entera al
# terminar. No toca ningún otro proyecto de Docker ni ningún `.env` existente:
# si `deploy/.env.production` ya existe, no arranca.
#
# Los secretos del ensayo se generan aquí, viven en un archivo temporal y se
# borran al salir. Nada de lo que imprime es un secreto.
#
# Requisitos: Docker, python3 y curl. La parte de navegador necesita Playwright:
# `SMOKE_FRONTEND_DIR` apunta a un `frontend/` con `node_modules` (por defecto,
# el de este repositorio); si no lo hay, esa parte se omite y se dice.
#
# Termina con `ENSAYO: OK` o `ENSAYO: n FALLO(S)` y sale con 0 o 1.
set -u

ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT" || exit 1
PROJECT=${R_PROJECT:-bds-rehearsal}
DOMAIN=${R_DOMAIN:-tienda.test}
HTTP_PORT=${R_HTTP_PORT:-18080}
PORT=${R_PORT:-18443}
SLUG=${R_SLUG:-black-dog-store}
SMOKE_FRONTEND_DIR=${SMOKE_FRONTEND_DIR:-$ROOT/frontend}
ENVF=deploy/.env.production
[ -e "$ENVF" ] && { echo "deploy/.env.production ya existe: el ensayo no lo pisa."; exit 1; }

WORK=$(mktemp -d)
BK="$WORK/backups"; mkdir -p "$BK"
umask 077
ADMIN_PW=$(python3 -c "import secrets; print(secrets.token_urlsafe(18))")
OUTSIDER_PW=$(python3 -c "import secrets; print(secrets.token_urlsafe(18))")
python3 - > "$ENVF" <<PY
import secrets
print(f"""SITE_DOMAIN=$DOMAIN
HTTP_PORT=$HTTP_PORT
HTTPS_PORT=$PORT
CADDY_TLS_DIRECTIVE=tls internal
SECRET_KEY={secrets.token_urlsafe(64)}
POSTGRES_PASSWORD={secrets.token_urlsafe(32)}
POSTGRES_USER=blackdog
POSTGRES_DB=blackdog
DEFAULT_STOREFRONT_COMPANY_SLUG=$SLUG
EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend
REQUIRE_EMAIL_VERIFICATION=1
PAYMENT_PROVIDER=izipay
IZIPAY_ENV=sandbox
IZIPAY_MERCHANT_CODE=9{secrets.randbelow(10**6):06d}
IZIPAY_PUBLIC_KEY=ensayo-{secrets.token_hex(8)}
IZIPAY_API_KEY={secrets.token_urlsafe(24)}
IZIPAY_HASH_KEY={secrets.token_urlsafe(32)}
IZIPAY_TOKEN_URL=https://pasarela.invalid/token
WHATSAPP_PROVIDER=disabled
EVIDENCE_STORAGE_BACKEND=filesystem
FISCAL_ENABLED=0""")
PY
# Las claves de la pasarela son del ensayo: se generan aquí, la pasarela a la que
# apuntan no existe (`.invalid`) y sirven para una sola cosa, comprobar que la
# notificación de pago sólo acepta lo que viene firmado. No son de Izipay.
# WhatsApp va apagado y Google sin ID de cliente: así llega una instalación nueva.
envval() { grep "^$1=" "$ENVF" | cut -d= -f2-; }

C="docker compose -p $PROJECT -f docker-compose.prod.yml --env-file $ENVF"
K="curl -sk --connect-to $DOMAIN:443:127.0.0.1:$PORT"
BASE="https://$DOMAIN"
FAILS=0
code() { $K -o /dev/null -w '%{http_code}' "$@"; }
step() { echo; echo "### $*"; }
# expect <descripción> <obtenido> <esperado, o varios separados por |>
expect() {
  case "|$3|" in
    *"|$2|"*) echo "  OK    $1 ($2)" ;;
    *) echo "  FALLO $1: obtenido «$2», esperado «$3»"; FAILS=$((FAILS + 1)) ;;
  esac
}
# media <descripción> <modo> [claves]: corre rehearsal_media.py y enseña sólo lo que falló.
media() {
  label=$1; shift
  python3 deploy/rehearsal_media.py "$@" > "$WORK/media.out" 2>&1; rc=$?
  grep -E "FALLO|Traceback|Error" "$WORK/media.out" | cut -c1-200
  expect "$label ($(grep -c '  OK   ' "$WORK/media.out") comprobaciones)" "$rc" 0
}
pause() { python3 -c "import time; time.sleep($1)"; }
healthy() {
  i=0
  until [ "$(docker inspect -f '{{.State.Health.Status}}' "$PROJECT-$1-1" 2>/dev/null)" = healthy ] || [ $i -ge 80 ]; do
    i=$((i + 1)); pause 3
  done
  docker inspect -f '{{.State.Health.Status}}' "$PROJECT-$1-1" 2>/dev/null
}
shell() { $C exec -T backend python manage.py shell -c "$1" 2>&1 | grep -v "objects imported" | grep -v '^$'; }
cleanup() {
  $C down -v --rmi local >/dev/null 2>&1
  rm -f "$ENVF" "$SMOKE_FRONTEND_DIR/.rehearsal-smoke.mjs"
  rm -rf "$WORK"
}
trap cleanup EXIT
export R_DOMAIN="$DOMAIN" R_PORT="$PORT" R_SLUG="$SLUG" R_STATE="$WORK/media.json"
export R_USER=ensayo_admin R_PASSWORD="$ADMIN_PW" R_OUTSIDER=ensayo_ajeno R_OUTSIDER_PASSWORD="$OUTSIDER_PW"
export R_IZIPAY_HASH_KEY="$(envval IZIPAY_HASH_KEY)" R_IZIPAY_MERCHANT="$(envval IZIPAY_MERCHANT_CODE)"
# flows <descripción> <modo> [archivo]: corre rehearsal_flows.py y enseña sólo lo que falló.
flows() {
  label=$1; shift
  (cd deploy && python3 rehearsal_flows.py "$@") > "$WORK/flows.out" 2>&1; rc=$?
  grep -E "FALLO|Traceback|Error" "$WORK/flows.out" | cut -c1-220
  expect "$label ($(grep -c '  OK   ' "$WORK/flows.out") comprobaciones)" "$rc" 0
}

step "0 base"
echo "HEAD $(git rev-parse --short HEAD) · master $(git rev-parse --short origin/master 2>/dev/null) · detrás de master $(git rev-list --count HEAD..origin/master 2>/dev/null) · cambios sin confirmar $(git status --short | grep -v '^??' | wc -l | tr -d ' ')"
$C down -v --rmi local >/dev/null 2>&1

step "1 construcción sin caché"
$C build --no-cache -q 2>&1 | tail -3
expect "imágenes construidas" "$(docker images --filter "reference=$PROJECT-*" -q | wc -l | tr -d ' ')" 2
echo "backend: $(docker run --rm --entrypoint sh "$PROJECT-backend" -c 'ls -a /app | grep -cE "^\.env|sqlite3$"; id -u; pip list 2>/dev/null | grep -iE "^(gunicorn|Django|pillow) " | tr "\n" " "' 2>&1 | tr '\n' ' ')(archivos .env/sqlite, uid, versiones)"
expect "la imagen del frontend no lleva secretos en su entorno" "$(docker run --rm --entrypoint sh "$PROJECT-frontend" -c 'env | grep -cE "SECRET|PASSWORD|IZIPAY|EMAIL_"' 2>&1)" 0
# Ni en su entorno ni en lo que compiló: ningún valor secreto del ensayo aparece en los archivos que sirve.
LEAKED=0
for name in SECRET_KEY POSTGRES_PASSWORD IZIPAY_API_KEY IZIPAY_HASH_KEY; do
  hits=$(docker run --rm -e NEEDLE="$(envval $name)" --entrypoint sh "$PROJECT-frontend" -c 'grep -rlF -- "$NEEDLE" /app/.next /app/public 2>/dev/null | wc -l' | tr -d ' \r')
  LEAKED=$((LEAKED + ${hits:-1}))
done
expect "ningún secreto del servidor está en los archivos del frontend" "$LEAKED" 0
expect "la imagen del backend no lleva claves, copias ni la configuración del agente" "$(docker run --rm --entrypoint sh "$PROJECT-backend" -c 'find /app -name "*.p12" -o -name "*.pem" -o -name "*.key" -o -name "*.sql.gz" -o -name "config.json" -path "*print_agent*" | wc -l' | tr -d ' \r')" 0

step "2 PostgreSQL limpio"
$C up -d postgres 2>&1 | tail -1
expect "postgres" "$(healthy postgres)" healthy

step "3 migraciones"
$C run --rm backend python manage.py migrate --noinput 2>&1 | tail -1
echo "migraciones de store aplicadas: $($C run --rm backend python manage.py showmigrations store 2>/dev/null | grep -c '\[X\]')"
expect "migraciones sin aplicar" "$($C run --rm backend python manage.py showmigrations 2>/dev/null | grep -c '\[ \]')" 0
expect "makemigrations --check" "$($C run --rm backend python manage.py makemigrations --check --dry-run 2>&1 | tail -1)" "No changes detected"
echo "check --deploy: $($C run --rm backend python manage.py check --deploy 2>&1 | grep -E 'security\.|System check' | cut -c1-110 | tr '\n' ' ')"

step "4 arranque, Caddy y comprobaciones de salud"
$C up -d 2>&1 | tail -1
expect "backend" "$(healthy backend)" healthy
expect "frontend" "$(healthy frontend)" healthy
pause 6
$C ps --format '{{.Service}} | {{.Status}} | {{.Ports}}'
expect "sólo Caddy publica puertos" "$($C ps --format '{{.Service}} {{.Ports}}' | grep -v '^caddy' | grep -c '0.0.0.0')" 0
docker inspect -f '{{.Name}} user={{.Config.User}} restart={{.HostConfig.RestartPolicy.Name}}' "$PROJECT-backend-1" "$PROJECT-frontend-1" "$PROJECT-caddy-1" "$PROJECT-postgres-1"
expect "un solo proceso de aplicación (maestro + 1 worker)" "$(docker top "$PROJECT-backend-1" 2>/dev/null | grep -c gunicorn)" 2
expect "el backend arranca sin errores en su registro" "$($C logs backend 2>&1 | grep -c '\[ERROR\]')" 0
# El tope de tamaño está en la configuración que Caddy cargó, en cada ruta hacia una aplicación.
ADAPTED=$($C exec -T caddy caddy adapt --config /etc/caddy/Caddyfile --adapter caddyfile 2>/dev/null)
expect "cada ruta hacia una aplicación tiene tope de tamaño (8 de 8)" "$(printf '%s' "$ADAPTED" | grep -o '"max_size"' | wc -l | tr -d ' ') $(printf '%s' "$ADAPTED" | grep -o '"handler":"reverse_proxy"' | wc -l | tr -d ' ')" "8 8"

step "5 ajustes efectivos de Django"
shell "
from django.conf import settings as s
print('DEBUG', s.DEBUG, '| ALLOWED_HOSTS', s.ALLOWED_HOSTS)
print('NUM_PROXIES', s.REST_FRAMEWORK.get('NUM_PROXIES'), '| SECURE_PROXY_SSL_HEADER', getattr(s,'SECURE_PROXY_SSL_HEADER',None), '| SSL_REDIRECT', s.SECURE_SSL_REDIRECT)
print('cookies Secure', s.SESSION_COOKIE_SECURE, s.CSRF_COOKIE_SECURE, s.JWT_COOKIE_SECURE, '| HttpOnly', s.JWT_COOKIE_HTTPONLY, '| SameSite', s.JWT_COOKIE_SAMESITE, '| HSTS', s.SECURE_HSTS_SECONDS)
print('CORS', s.CORS_ALLOWED_ORIGINS, '| CSRF_TRUSTED', s.CSRF_TRUSTED_ORIGINS)
print('FRONTEND_URL', s.FRONTEND_URL, '| CHECKOUT_RETURN_URL', s.CHECKOUT_RETURN_URL)
print('renderers', s.REST_FRAMEWORK['DEFAULT_RENDERER_CLASSES'])
print('EMAIL_BACKEND', s.EMAIL_BACKEND.rsplit('.',1)[-1], '| IZIPAY_ENV', s.IZIPAY_ENV, '| FISCAL', s.FISCAL_ENABLED, '| ALMACÉN', s.EVIDENCE_STORAGE_BACKEND)
print('CACHE', s.CACHES['default']['BACKEND'].rsplit('.',1)[-1], '| DB', s.DATABASES['default']['ENGINE'].rsplit('.',1)[-1])
from django.urls import get_resolver; print('rutas raíz', sorted(str(p.pattern) for p in get_resolver().url_patterns))
"
expect "DEBUG" "$(shell "from django.conf import settings as s; print(s.DEBUG)")" False

step "6 datos de ensayo: semilla, primer administrador y una persona ajena"
$C exec -T backend python manage.py seed_demo_users --company-slug "$SLUG" >/dev/null 2>&1
expect "seed_demo_users se niega en producción" "$([ $? -ne 0 ] && echo sí || echo no)" "sí"
expect "no existe ninguna cuenta de demostración" "$(shell "from django.contrib.auth import get_user_model as U; print(U().objects.filter(username__startswith='dev_').count())")" 0
expect "las cuentas de demostración no se anuncian" "$(code "$BASE/api/dev/demo-accounts")" 404
$C exec -T -e DJANGO_SUPERUSER_PASSWORD="$ADMIN_PW" backend python manage.py createsuperuser --noinput --username ensayo_admin --email ensayo@example.invalid 2>&1 | tail -1
$C exec -T -e OUTSIDER_PW="$OUTSIDER_PW" backend python manage.py shell -c "
import os
from django.contrib.auth import get_user_model
get_user_model().objects.create_user('ensayo_ajeno', email='ajeno@example.invalid', password=os.environ['OUTSIDER_PW'])
print('persona sin empresa creada')" 2>&1 | grep creada
# Una orden de servicio, para tener una evidencia privada que proteger.
shell "
from django.contrib.auth import get_user_model
from store import service_services as service
from store.models import Branch, Company, Customer, Device
company = Company.objects.get(slug='$SLUG')
branch = Branch.objects.filter(company=company, is_active=True).order_by('pk').first()
admin = get_user_model().objects.get(username='ensayo_admin')
customer = Customer.objects.create(company=company, customer_type=Customer.TYPE_PERSON, first_name='Ensayo', last_name='Evidencia', notes='ensayo')
device = Device.objects.create(company=company, customer=customer, device_type=Device.TYPE_PHONE, brand='Ensayo', model='Evidencia', notes='ensayo')
order = service.create_repair_order(company=company, branch=branch, customer=customer, device=device, reported_issue='Ensayo de evidencia privada', actor=admin)
print('orden de servicio de ensayo creada:', bool(order.pk))" | tail -1
echo "admin de Django, directo al backend: $($C exec -T backend python -c "
import urllib.request, urllib.error
for p in ('/admin/', '/admin/login/'):
    try: print(p, urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8000'+p, headers={'Host':'backend'}), timeout=5).status)
    except urllib.error.HTTPError as e: print(p, e.code)
" 2>&1 | tr '\n' ' ')"

step "7 rutas a través de Caddy"
expect "http redirige a https" "$(curl -s -o /dev/null -w '%{http_code}' --connect-to "$DOMAIN:80:127.0.0.1:$HTTP_PORT" "http://$DOMAIN/")" "308|301"
PRODUCT=$($K "$BASE/api/products" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d[0]['slug'], d[0]['id'])")
PSLUG=${PRODUCT% *}; PID=${PRODUCT#* }
for p in / /product "/product/$PSLUG" /cart /checkout /services /about /contact /auth /admin /api/categories /api/products /api/storefront/config; do
  expect "$p" "$(code "$BASE$p")" 200
done
for p in /admin/login/ /static/admin/css/base.css /media/x.png /private-media/x.png; do
  expect "$p no llega a Django ni a un archivo" "$(code "$BASE$p")" "404|307|308"
done
expect "/api/../admin/login/" "$(code --path-as-is "$BASE/api/../admin/login/")" "404|400|307|308"
expect "host desconocido" "$(curl -sk -o /dev/null -w '%{http_code}' --connect-to "otra.test:443:127.0.0.1:$PORT" https://otra.test/api/categories 2>/dev/null)" "000|400|404|421"

step "8 cabeceras"
for p in / /auth/reset-password /api/categories; do
  echo "$p: $($K -I "$BASE$p" | grep -iE '^(strict-transport-security|x-content-type-options|referrer-policy|x-frame-options|content-security-policy|permissions-policy|server|x-powered-by):' | tr -d '\r' | tr '\n' ';')"
done
expect "una sola cabecera HSTS en la API" "$($K -I "$BASE/api/categories" | grep -ic '^strict-transport-security')" 1
expect "una sola cabecera HSTS en las páginas" "$($K -I "$BASE/" | grep -ic '^strict-transport-security')" 1
expect "no se anuncia el servidor" "$($K -I "$BASE/" | grep -icE '^(server|x-powered-by):')" 0
expect "Permissions-Policy en las páginas" "$($K -I "$BASE/" | grep -ic '^permissions-policy: camera=()')" 1
expect "no-referrer donde la URL lleva un token" "$($K -I "$BASE/auth/reset-password" | grep -ic '^referrer-policy: no-referrer')" 1

step "9 carrito y cotización (sin pago)"
printf '{"session_key":"guest-ensayo-0001","product":%s,"quantity":1}' "$PID" > "$WORK/cart.json"
expect "añadir al carrito" "$($K -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' -H "Origin: $BASE" -d @"$WORK/cart.json" "$BASE/api/cart/add")" "200|201"
printf '{"session_key":"guest-ensayo-0001","delivery_method":"pickup"}' > "$WORK/quote.json"
expect "cotización del checkout" "$($K -o "$WORK/quote.out" -w '%{http_code}' -X POST -H 'Content-Type: application/json' -H "Origin: $BASE" -d @"$WORK/quote.json" "$BASE/api/checkout/quote")" 200

step "10 CORS y origen ajeno"
expect "CORS para el origen propio" "$($K -I -H "Origin: $BASE" "$BASE/api/categories" | grep -ic '^access-control-allow-origin')" 1
expect "CORS para un origen ajeno" "$($K -I -H 'Origin: https://evil.test' "$BASE/api/categories" | grep -ic '^access-control-allow-origin')" 0
expect "login desde un origen ajeno" "$(code -X POST -H 'Content-Type: application/json' -H 'Origin: https://evil.test' -d '{"username":"x","password":"y"}' "$BASE/api/auth/login")" "403|401|400"

step "11 archivos subidos: imágenes públicas y evidencias privadas"
# El inicio de sesión está limitado a 5 por minuto y por dirección: se deja
# vaciar la ventana antes de que el ensayo inicie sus propias sesiones.
pause 62
media "subir, colocar y servir" upload
KEYS=$(shell "
from store.models import StorefrontImage, RepairEvidence
print(','.join(list(StorefrontImage.objects.order_by('pk').values_list('storage_key', flat=True)[:1]) + list(RepairEvidence.objects.order_by('pk').values_list('storage_key', flat=True)[:1])))" | tail -1)
echo "  claves del almacén a probar por rutas de archivo: $(echo "$KEYS" | tr ',' '\n' | wc -l | tr -d ' ')"
media "públicas, privadas y sin rutas de archivo" verify "$KEYS"
expect "hay imágenes de tienda en el volumen" "$($C exec -T backend sh -c 'find /app/private-media -type f -path "*/storefront/*" | wc -l' | awk '{print ($1 > 0) ? "sí" : "no"}')" "sí"
expect "hay evidencias en el mismo volumen" "$($C exec -T backend sh -c 'find /app/private-media -type f ! -path "*/storefront/*" | wc -l' | awk '{print ($1 > 0) ? "sí" : "no"}')" "sí"

step "11b lo que la tienda hace: límites, pagos, equipos con serie, documentos, seguimiento"
# Otra ventana de un minuto: las comprobaciones de archivos gastaron sus inicios de sesión.
pause 62
flows "límites, pagos, equipos, documentos, seguimiento, WhatsApp y Google apagados" run
expect "con WhatsApp apagado no se envió ni quedó pendiente ningún mensaje" "$(shell "
from store.models import NotificationDelivery as N
print(N.objects.filter(channel='whatsapp', status__in=['pending', 'sent', 'delivered', 'read']).count())" | tail -1)" 0
$C logs backend frontend caddy > "$WORK/logs.txt" 2>&1
flows "los registros no guardan enlaces, IMEI, contraseñas ni claves" logs "$WORK/logs.txt"

step "12 navegador real: tienda, imágenes, sesión y panel"
# El límite de inicio de sesión es de 5 por minuto y por dirección: se deja
# vaciar la ventana que gastaron las comprobaciones anteriores.
pause 62
if [ -d "$SMOKE_FRONTEND_DIR/node_modules/@playwright/test" ]; then
  cp deploy/rehearsal_smoke.mjs "$SMOKE_FRONTEND_DIR/.rehearsal-smoke.mjs"
  (cd "$SMOKE_FRONTEND_DIR" && SMOKE_USER=ensayo_admin SMOKE_PASSWORD="$ADMIN_PW" node .rehearsal-smoke.mjs > "$WORK/smoke.out" 2>&1; echo $? > "$WORK/smoke.rc")
  cut -c1-200 "$WORK/smoke.out"
  expect "navegador" "$(cat "$WORK/smoke.rc")" 0
  rm -f "$SMOKE_FRONTEND_DIR/.rehearsal-smoke.mjs"
else
  echo "  OMITIDO: no hay Playwright en $SMOKE_FRONTEND_DIR/node_modules"
fi

step "13 CSRF y cookies de sesión"
J="$WORK/jar"
printf '{"username":"ensayo_admin","password":"%s"}' "$ADMIN_PW" > "$WORK/login.json"
expect "inicio de sesión" "$($K -c "$J" -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' -H "Origin: $BASE" -d @"$WORK/login.json" "$BASE/api/auth/login")" 200
expect "cookies de sesión HttpOnly y Secure" "$(grep -E "blackdog_(access|refresh)" "$J" | awk '$1 ~ /^#HttpOnly_/ && $4 == "TRUE"' | wc -l | tr -d ' ')" 2
expect "con sesión, desde un origen ajeno" "$($K -b "$J" -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' -H 'Origin: https://evil.test' -H 'Referer: https://evil.test/' -d '{}' "$BASE/api/auth/change-password")" 403
expect "con sesión, origen propio, sin token CSRF" "$($K -b "$J" -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' -H "Origin: $BASE" -H "Referer: $BASE/" -d '{}' "$BASE/api/auth/change-password")" 403
expect "panel con sesión" "$($K -b "$J" -o /dev/null -w '%{http_code}' "$BASE/api/me/internal-dashboard")" 200
expect "panel sin sesión" "$(code "$BASE/api/me/internal-dashboard")" "401|403"
echo "registro de seguridad: $($C logs backend 2>/dev/null | grep -cE 'login_(ok|failed)') líneas de login"

step "14 tareas programadas"
$C exec -T backend python manage.py flushexpiredtokens >/dev/null 2>&1; expect "flushexpiredtokens" "$?" 0
$C exec -T backend python manage.py send_pending_notifications >/dev/null 2>&1; expect "send_pending_notifications (con WhatsApp apagado no hace nada y termina bien)" "$?" 0
if $C exec -T backend python manage.py help cleanup_storefront_images >/dev/null 2>&1; then
  echo "  $($C exec -T backend python manage.py cleanup_storefront_images --dry-run 2>&1 | tail -1 | cut -c1-140)"
  expect "cleanup_storefront_images --dry-run" "$?" 0
  media "la limpieza en seco no borra nada colocado" verify
else
  echo "  cleanup_storefront_images no existe en esta versión"
fi

step "15 X-Forwarded-For falso y límites (tras vaciar la ventana de un minuto)"
pause 62
A=""; for i in 1 2 3 4 5 6 7; do A="$A $(code -X POST -H 'Content-Type: application/json' -H "Origin: $BASE" -H "X-Forwarded-For: 10.9.9.$i" -d '{"username":"nadie","password":"x"}' "$BASE/api/auth/login")"; done
echo "7 logins fallidos, con un X-Forwarded-For distinto cada vez:$A"
expect "el límite de login no se salta cambiando la cabecera" "$(echo "$A" | tr ' ' '\n' | grep -c 429)" "2|3"
expect "Django vio la dirección real, no la inventada" "$($C logs backend 2>/dev/null | grep login_failed | tail -1 | grep -c 'ip=10\.9\.9\.')" 0
R=""; for i in $(seq 1 33); do R="$R $(code -X POST -H "Origin: $BASE" -H 'Cookie: blackdog_refresh=no-es-un-token' -H "X-Forwarded-For: 10.8.8.$i" "$BASE/api/auth/refresh")"; done
expect "33 renovaciones con credencial falsa: las 3 últimas, limitadas" "$(echo "$R" | tr ' ' '\n' | grep -c 429)" 3
cat > "$WORK/client.py" <<PY
import json, ssl, urllib.request, urllib.error
ctx = ssl._create_unverified_context()
out = []
for _ in range(2):
    r = urllib.request.Request('$BASE/api/auth/login/', data=json.dumps({'username': 'nadie', 'password': 'x'}).encode(), headers={'Content-Type': 'application/json', 'Origin': '$BASE'}, method='POST')
    try: out.append(urllib.request.urlopen(r, context=ctx, timeout=20).status)
    except urllib.error.HTTPError as e: out.append(e.code)
print(' '.join(map(str, out)))
PY
chmod 644 "$WORK/client.py"; chmod 755 "$WORK"
CIP=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$PROJECT-caddy-1")
expect "otro cliente, desde otra dirección, no hereda el bloqueo" "$(docker run --rm --network "${PROJECT}_default" --add-host "$DOMAIN:$CIP" -v "$WORK/client.py:/c.py:ro" --entrypoint python "$PROJECT-backend" /c.py 2>&1 | tail -1)" "401 401|400 400"

COUNTS='from store.models import *; from django.contrib.auth import get_user_model as U; print("empresas",Company.objects.count(),"sucursales",Branch.objects.count(),"usuarios",U().objects.count(),"membresías",Membership.objects.count(),"categorías",Category.objects.count(),"productos",Product.objects.count(),"stock",sum(BranchStock.objects.values_list("quantity",flat=True)),"movimientos",StockMovement.objects.count(),"pedidos",Order.objects.count(),"reparaciones",RepairOrder.objects.count(),"imágenes de tienda",StorefrontImage.objects.count(),"evidencias",RepairEvidence.objects.count(),"líneas de carrito",CartItem.objects.count(),"equipos",StockUnit.objects.count(),"equipos vendidos",StockUnit.objects.filter(status="sold").count(),"notas de venta",SalesNote.objects.count(),"enlaces de seguimiento",RepairTrackingLink.objects.count())'
count() { shell "$COUNTS" | grep empresas; }
files() { $C exec -T backend sh -c 'find /app/private-media -type f | wc -l' | tr -d ' \r'; }

step "16 persistencia: apagar y encender"
BEFORE=$(count); FILES=$(files); echo "antes:   $BEFORE · archivos $FILES"
$C down 2>&1 | tail -1; $C up -d 2>&1 | tail -1
expect "backend" "$(healthy backend)" healthy; expect "frontend" "$(healthy frontend)" healthy; pause 5
expect "los datos son los mismos" "$(count)" "$BEFORE"
expect "los archivos son los mismos" "$(files)" "$FILES"
media "imágenes y evidencias siguen en su sitio" verify
flows "la foto de producto y el enlace de seguimiento siguen ahí" verify

step "17 persistencia: reconstruir las imágenes y recrear los contenedores"
$C build -q 2>&1 | tail -1; $C up -d --force-recreate 2>&1 | tail -1
expect "backend" "$(healthy backend)" healthy; expect "frontend" "$(healthy frontend)" healthy; pause 5
expect "los datos son los mismos" "$(count)" "$BEFORE"
expect "los archivos son los mismos" "$(files)" "$FILES"
media "imágenes y evidencias siguen en su sitio" verify
flows "la foto de producto y el enlace de seguimiento siguen ahí" verify

step "18 copia de seguridad (deploy/backup.sh): base de datos y archivos"
COMPOSE="$C" BACKUP_DIR="$BK" sh deploy/backup.sh 2>&1 | tail -3 | sed "s|$BK|<copias>|g"
DB=$(ls "$BK"/db-*.sql.gz 2>/dev/null | head -1); EV=$(ls "$BK"/evidence-*.tar.gz 2>/dev/null | head -1)
expect "la copia de archivos lleva imágenes de tienda" "$(tar -tzf "$EV" 2>/dev/null | grep -c '/storefront/.*\.png$' | awk '{print ($1 > 0) ? "sí" : "no"}')" "sí"
expect "la copia de archivos lleva evidencias" "$(tar -tzf "$EV" 2>/dev/null | grep -v '/storefront/' | grep -cE '\.(jpe?g|png|webp)$' | awk '{print ($1 > 0) ? "sí" : "no"}')" "sí"

step "19 daño posterior y restauración (deploy/restore.sh)"
$C exec -T -e DJANGO_SUPERUSER_PASSWORD="$ADMIN_PW" backend python manage.py createsuperuser --noinput --username intruso_posterior --email posterior@example.invalid 2>&1 | tail -1
$C exec -T backend sh -c 'find /app/private-media -type f -delete'
expect "tras el daño no queda ningún archivo" "$(files)" 0
expect "tras el daño las imágenes no se sirven" "$(python3 deploy/rehearsal_media.py verify >/dev/null 2>&1; echo $?)" 1
echo RESTAURAR | COMPOSE="$C" BACKUP_DIR="$BK" sh deploy/restore.sh "$DB" "$EV" 2>&1 | grep -E "^[0-9]/4|Restaurado|ERROR|error" | cut -c1-100
expect "backend" "$(healthy backend)" healthy; expect "frontend" "$(healthy frontend)" healthy; pause 5
expect "los datos vuelven a ser los de la copia" "$(count)" "$BEFORE"
expect "los archivos vuelven a ser los de la copia" "$(files)" "$FILES"
expect "lo creado después de la copia ya no existe" "$(shell "from django.contrib.auth import get_user_model as U; print(U().objects.filter(username='intruso_posterior').exists())")" False
media "imágenes y evidencias siguen en su sitio" verify
flows "la foto de producto y el enlace de seguimiento siguen ahí" verify
expect "tienda tras restaurar" "$(code "$BASE/") $(code "$BASE/api/products")" "200 200"

step "20 volúmenes"
docker volume ls --filter "name=$PROJECT" --format '{{.Name}}' | tr '\n' ' '; echo

step "21 desmontaje"
trap - EXIT; cleanup
LEFT="contenedores $(docker ps -a --filter "name=$PROJECT" -q | wc -l | tr -d ' ') volúmenes $(docker volume ls --filter "name=$PROJECT" -q | wc -l | tr -d ' ') imágenes $(docker images --filter "reference=$PROJECT-*" -q | wc -l | tr -d ' ') env $(ls "$ENVF" 2>/dev/null | wc -l | tr -d ' ')"
expect "no queda nada del ensayo" "$LEFT" "contenedores 0 volúmenes 0 imágenes 0 env 0"

echo
if [ "$FAILS" -eq 0 ]; then echo "ENSAYO: OK"; else echo "ENSAYO: $FAILS FALLO(S)"; fi
[ "$FAILS" -eq 0 ]
