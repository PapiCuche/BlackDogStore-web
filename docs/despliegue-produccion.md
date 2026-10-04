# Despliegue en producción

Cómo publicar Black Dog Store en internet con dominio propio, cómo actualizarla y
cómo no perder datos. Todo lo de aquí se ensayó en local con Docker antes de
escribirlo; lo que no se pudo ensayar se dice.

Complementa a [requisitos-operativos-produccion.md](requisitos-operativos-produccion.md),
que explica por qué existe `TRUSTED_PROXY_COUNT`.

## 1. Qué se despliega

Un servidor (VPS) con Docker Compose y cuatro contenedores:

```
internet ──443──> Caddy ──┬── /api/*   ──> Django (gunicorn)
                          └── lo demás ──> Next.js
                                           Django ──> PostgreSQL
```

| Pieza | Archivo | Qué hace |
|---|---|---|
| Caddy | `deploy/Caddyfile` | HTTPS automático (Let's Encrypt), redirige el puerto 80 |
| Next.js | `frontend/Dockerfile.prod` | La tienda y el panel, compilados una vez |
| Django | `backend/Dockerfile.prod` | La API, con gunicorn y sin privilegios |
| PostgreSQL | `docker-compose.prod.yml` | La base de datos, en el volumen `pgdata` |

Sólo Caddy publica puertos (80 y 443). Django, Next y PostgreSQL viven en la red
privada de Docker y no son alcanzables desde internet. Caddy no empieza a atender
hasta que Django y Next responden a su comprobación de salud.

### La dirección del visitante

Hay exactamente **un** proxy delante de Django: Caddy. No hay CDN ni otro proxy.

1. El visitante se conecta a Caddy.
2. Caddy descarta el `X-Forwarded-For`, el `X-Forwarded-Proto` y el
   `X-Forwarded-Host` que traiga la petición y escribe los suyos con la dirección
   real de la conexión.
3. Django lee esa dirección porque `TRUSTED_PROXY_COUNT=1`.

Esa dirección es la que cuentan los límites de peticiones y la que queda en el
registro. Si algún día se pone otro proxy delante (un CDN, un balanceador), hay
que subir `TRUSTED_PROXY_COUNT` en la misma cantidad; si no, todos los visitantes
compartirían la dirección de ese proxy.

### Un solo proceso de Django

El backend corre con un proceso de gunicorn y ocho hilos. Es deliberado: los
límites de peticiones se cuentan en la memoria del proceso, y con un proceso son
exactos. **No subas el número de procesos (`--workers`) sin antes poner una caché
compartida**: cada proceso contaría por su lado y los límites se multiplicarían
(THROTTLE-CACHE-01).

### Dónde vive cada archivo

| Qué | Dónde | Quién lo ve |
|---|---|---|
| Fotos de producto | Una URL en cada producto; el archivo está en el host que elijas | Público |
| Logotipos de la tienda | URL en la configuración de la empresa | Público |
| Imágenes de la tienda (hero, categorías, servicio, ubicación, campañas) subidas desde el panel | Volumen `evidence`, bajo `companies/<id>/storefront/` | Público, por `/api/storefront/images/<id>` |
| Evidencias del servicio técnico | El mismo volumen `evidence`, o un almacenamiento S3 privado | Sólo personal con permiso, a través de la API |

**Mismo volumen, distinta autorización.** Las imágenes de la tienda y las
evidencias comparten almacén, pero no permiso. Una imagen de la tienda la sirve
la API a cualquiera, con su transparencia y con caché de un año: su dirección no
cambia nunca de contenido. Una evidencia nunca tiene URL pública: con el volumen
se sirve por la API tras comprobar el permiso; con S3, por un enlace firmado que
caduca a los cinco minutos.

**Caddy no sirve archivos.** No hay ningún `file_server` ni ninguna ruta `/media`:
publicar la carpeta para servir las imágenes de la tienda publicaría también las
evidencias. Todo archivo sale por la API, que es quien decide. El ensayo lo
comprueba pidiendo las claves reales del almacén por cinco rutas de archivo.

Las fotos de producto siguen siendo una URL: la aplicación guarda su dirección,
no el archivo.

**Por qué la API no pasa por el proxy interno de Next.** Ese proxy descarta a
propósito las cabeceras que identifican al cliente. Detrás de él, Django vería a
todos los visitantes con la misma dirección y compartirían los límites de
peticiones: cinco inicios de sesión por minuto para toda la tienda. Caddy escribe
la dirección real y Django la lee con `TRUSTED_PROXY_COUNT=1`.

## 2. Qué hay que contratar y cuánto cuesta

Precios aproximados; confirmar al contratar.

| Concepto | Opción sugerida | Costo aproximado |
|---|---|---|
| Servidor | VPS con 2 vCPU, 4 GB de RAM, 40 GB de disco, Ubuntu 24.04 | US$5–8 al mes |
| Dominio | `.com` o `.pe` | US$10–15 al año (`.com`); US$40–60 al año (`.pe`) |
| Correo saliente (SMTP) | Un proveedor transaccional con plan gratuito o el correo del dominio | US$0–6 al mes |
| Copia externa | Almacenamiento de objetos (unos pocos MB) | US$0–1 al mes |

Total estimado: **US$6–15 al mes** más el dominio.

Con menos de 4 GB de RAM la compilación del frontend puede quedarse sin memoria.

## 3. Datos que sólo tú puedes dar

| Dato | Para qué | Dónde va |
|---|---|---|
| Dominio | La dirección de la tienda | `SITE_DOMAIN` |
| Servidor SMTP, usuario y contraseña | Invitar personal, recuperar contraseñas, avisos de pedido | `EMAIL_*` |
| Correo que recibe los pedidos | Aviso de cada pedido pagado | `ORDER_NOTIFICATION_EMAIL` |
| Credenciales de Izipay | Cobrar en línea | `IZIPAY_*` |
| Dónde se alojan las fotos de producto | Que la web pueda mostrarlas | `NEXT_PUBLIC_IMAGE_HOSTS` |

Sin Izipay la tienda funciona (catálogo, carrito, panel, punto de venta) pero no
cobra en línea. Sin SMTP el backend **no arranca** con la configuración de
ejemplo: es deliberado, porque sin correo no se puede invitar a nadie ni recuperar
una contraseña.

## 4. Publicar por primera vez

### 4.1 Dominio y servidor

1. Compra el dominio y crea dos registros DNS de tipo `A` hacia la IP del servidor:
   uno para el dominio y otro para `www`.
2. En el servidor, abre sólo los puertos 22, 80 y 443.
3. Instala Docker con el plugin Compose y clona el repositorio.

Espera a que el DNS responda antes de seguir: Caddy pide el certificado al
arrancar y Let's Encrypt limita los intentos fallidos.

### 4.2 Variables

```sh
cp deploy/.env.production.example deploy/.env.production
chmod 600 deploy/.env.production
```

Rellena `deploy/.env.production`. Genera los dos secretos en el propio servidor:

```sh
python3 -c "import secrets; print(secrets.token_urlsafe(64))"   # SECRET_KEY
python3 -c "import secrets; print(secrets.token_urlsafe(32))"   # POSTGRES_PASSWORD
```

Ese archivo no se sube al repositorio. No reutilices los valores de desarrollo.

### 4.3 Arranque

Todos los comandos, desde la raíz del repositorio:

```sh
C="docker compose -f docker-compose.prod.yml --env-file deploy/.env.production"

$C build
$C up -d postgres
$C run --rm backend python manage.py migrate --noinput
$C run --rm backend python manage.py check --deploy
$C up -d
$C ps
```

`check --deploy` debe mostrar un único aviso, `security.W008`
(`SECURE_SSL_REDIRECT`). Es el esperado: la redirección a HTTPS la hace Caddy, y
Django recibe además llamadas internas de Next por HTTP que una redirección
rompería.

No hace falta `collectstatic`: Django no sirve ningún archivo estático en
producción. La API responde sólo JSON y el admin de Django no existe allí.

El backend **no arranca** si `FRONTEND_URL` o `CHECKOUT_RETURN_URL` faltan o
apuntan a `localhost`. El archivo de Compose las arma a partir de `SITE_DOMAIN`,
así que basta con que el dominio sea el real.

Las migraciones **no** se aplican solas al arrancar. Algunas cambian datos, y
aplicarlas es una decisión de cada despliegue.

### 4.4 Primer administrador

Las cuentas de demostración no existen en producción y el comando que las crea se
niega a ejecutarse con `DEBUG=0`.

```sh
$C exec backend python manage.py createsuperuser
```

Con esa cuenta entra en `https://<dominio>/auth`, abre el panel, elige la empresa y
desde **Personal** invita a quienes vayan a trabajar con su rol. Usa una
contraseña larga y única.

### 4.5 Comprobaciones

```sh
curl -I http://<dominio>/                     # 308 hacia https
curl -s -o /dev/null -w "%{http_code}\n" https://<dominio>/                       # 200
curl -s -o /dev/null -w "%{http_code}\n" https://<dominio>/api/categories         # 200
curl -s -o /dev/null -w "%{http_code}\n" https://<dominio>/api/dev/demo-accounts  # 404
```

Y en el navegador: iniciar sesión, añadir un producto al carrito, abrir el panel.

## 5. Datos iniciales

Una base nueva, tras `migrate`, ya trae la empresa, su sucursal, los roles, la
configuración de la tienda, las dos categorías y los tres productos: los crean las
migraciones. No hay nada que copiar para eso.

Lo que **no** es real en una base nueva:

- **Existencias.** Las migraciones dejan 10, 15 y 20 unidades de ejemplo. Corrige
  cada producto desde el panel, en **Inventario → Stock**, con un ajuste: queda
  registrado en el Kardex con quién lo hizo. El respaldo local (sección 6.3) trae
  `stock.csv` con las cantidades que había en desarrollo.
- **Fotos de producto.** Vienen vacías. Se cargan como URL en cada producto, y el
  host donde estén alojadas tiene que figurar en `NEXT_PUBLIC_IMAGE_HOSTS`.
- **Usuarios.** Ninguno. Los ocho que hay en desarrollo son cuentas de
  demostración con contraseña pública y no pasan a producción.
- **Pedidos.** Ninguno. Los de desarrollo son pruebas.

## 6. Copias de seguridad

### 6.1 Copia en el servidor

```sh
sh deploy/backup.sh
```

Deja en `backups/` un volcado completo de PostgreSQL y un archivo con todo el
almacén —las evidencias del servicio técnico y las imágenes de la tienda; el
archivo se llama `evidence-…` por el volumen—, y borra las copias con más de 14 días. El volcado sólo se guarda si
terminó entero.

Para hacerla cada noche, en el `crontab` del servidor:

```
15 3 * * * cd /ruta/al/repositorio && sh deploy/backup.sh >> backups/backup.log 2>&1
```

### 6.1.1 Limpieza de sesiones caducadas

Cada inicio y cada renovación de sesión deja una fila en la base. Las caducadas
no se borran solas. Una vez al día, en el mismo `crontab`, después de la copia:

```
45 3 * * * cd /ruta/al/repositorio && docker compose -f docker-compose.prod.yml --env-file deploy/.env.production exec -T backend python manage.py flushexpiredtokens >> backups/flushexpiredtokens.log 2>&1
```

Lo ejecuta el contenedor `backend`, que ya está encendido; no hace falta otro
servicio. Para comprobarlo:

```sh
tail backups/flushexpiredtokens.log        # sin errores
$C exec backend python manage.py shell -c "from rest_framework_simplejwt.token_blacklist.models import OutstandingToken as T; from django.utils import timezone as z; print(T.objects.filter(expires_at__lt=z.now()).count())"
```

El segundo comando debe imprimir `0` justo después de la limpieza.

### 6.1.2 Limpieza de imágenes sin uso

Al reemplazar una imagen de la tienda, la anterior se borra sola si nadie más la
usa. Lo que queda son las subidas que nunca se guardaron (se cerró el formulario
sin guardar). Una vez al día:

```
55 3 * * * cd /ruta/al/repositorio && docker compose -f docker-compose.prod.yml --env-file deploy/.env.production exec -T backend python manage.py cleanup_storefront_images >> backups/cleanup_storefront_images.log 2>&1
```

Sólo borra imágenes con cero referencias en toda la plataforma y con más de 24
horas (`--older-than-hours`). `--dry-run` las lista sin borrar. Cada borrado
queda en el registro de auditoría de su empresa.

### 6.2 Copia externa

Una copia en el mismo servidor no protege si se pierde el servidor. Lleva la
carpeta `backups/` a otro sitio (otro equipo o un almacenamiento de objetos), por
ejemplo con `rclone` después de cada copia. Elegir el destino es decisión tuya; no
está configurado.

### 6.3 Respaldo de los datos locales

Antes de publicar, guarda lo que hay hoy en desarrollo:

```sh
sh deploy/backup-local-data.sh /ruta/al/repo/backend/db.sqlite3
```

Abre la base en sólo lectura y deja una copia íntegra (productos, usuarios,
pedidos, stock y movimientos), `stock.csv`, `products.csv` y un resumen que marca
qué cuentas son de demostración.

### 6.4 Restaurar

```sh
sh deploy/restore.sh backups/db-AAAAMMDD-HHMMSS.sql.gz backups/evidence-AAAAMMDD-HHMMSS.tar.gz
```

Reemplaza la base actual por la de la copia. Pide escribir `RESTAURAR`, y antes de
tocar nada guarda una copia de lo que hay.

**Prueba la restauración una vez** antes de necesitarla: una copia que nunca se ha
restaurado no se sabe si sirve.

## 7. Actualizar la web

```sh
C="docker compose -f docker-compose.prod.yml --env-file deploy/.env.production"

sh deploy/backup.sh                 # 1. copia antes de tocar nada
git pull                            # 2. código nuevo
$C build                            # 3. imágenes nuevas (la web sigue en línea)
$C run --rm backend python manage.py migrate --plan   # 4. qué migraciones hay
$C run --rm backend python manage.py migrate --noinput
$C up -d                            # 5. reemplaza los contenedores
$C ps
```

Si algo sale mal: `git checkout <commit anterior>`, `$C build`, `$C up -d`. Si
además una migración cambió datos, restaura la copia del paso 1.

`docker compose down` conserva los datos. **`docker compose down -v` los borra**:
elimina el volumen de la base de datos y el de los archivos (evidencias e
imágenes de la tienda).

## 8. Seguridad

Ya resuelto por el código o por esta configuración:

- `DEBUG` vale `0` y no puede cambiarse desde el archivo de variables.
- Sin `SECRET_KEY` real, `ALLOWED_HOSTS` y `CORS_ALLOWED_ORIGINS`, Django se niega a
  arrancar.
- Los accesos de demostración responden 404 y la tarjeta de `/auth` no se pinta.
- Una imagen subida se recodifica a partir de sus píxeles: sólo PNG, JPEG o WebP,
  hasta 8 MB. Un SVG, un GIF o un HTML con extensión de imagen se rechazan, y el
  nombre original no llega al almacén.
- Las evidencias no se entregan sin sesión ni a quien no trabaja en la empresa,
  aunque compartan volumen con las imágenes públicas.
- Las páginas renuncian a cámara, micrófono y ubicación (`Permissions-Policy`).
- Las cookies de sesión son `Secure` y `HttpOnly`; las peticiones que modifican
  exigen CSRF y un origen del propio dominio.
- El admin de Django no existe en producción: con `DEBUG=0` el backend no registra
  esa ruta, y además Caddy sólo envía `/api/*` a Django. Lo que haga falta se hace
  desde el panel de la aplicación o con `$C exec backend python manage.py shell`
  en el servidor.
- Renovar la sesión tiene un límite de 30 por minuto y dirección, y cada intento
  de inicio de sesión queda en el registro (`$C logs backend | grep login_`), sin
  la contraseña.
- La tienda y el panel no se pueden incrustar en otro sitio, y las páginas que
  reciben un enlace de un solo uso no lo entregan a terceros.
- Los límites de peticiones se cuentan por la dirección real del cliente. Una
  cabecera `X-Forwarded-For` falsa no los evita.

Pendiente, y conviene saberlo:

- **Un solo proceso de Django.** Los límites se cuentan en la memoria del proceso,
  así que el backend corre con un proceso y varios hilos. Suficiente para una
  tienda; crecer pide antes una caché compartida (THROTTLE-CACHE-01).
- **Facturación electrónica apagada** (`FISCAL_ENABLED=0`). Encenderla necesita
  certificado digital y credenciales SOL, y es una fase aparte.
- El inicio de sesión no exige token CSRF ni rechaza por origen (LOGIN-CSRF-01,
  baja). Las operaciones con sesión sí: un origen ajeno recibe 403.

## 9. Qué se ensayó

Ensayo completo sobre `f0c29ce`, con `master` `833fdec` incorporado, el 2026-10-03.
Imágenes reconstruidas sin caché, PostgreSQL vacío, dominio reservado
`tienda.test` y certificado interno de Caddy. Las comprobaciones de navegador se
hicieron con un navegador real a través de Caddy. Todo se desmontó al terminar.

| # | Comprobación | Resultado |
|---|---|---|
| 1–2 | Compilar las dos imágenes sin caché | OK. Sin `.env`, base ni evidencias dentro; el frontend no lleva ningún secreto del backend |
| 3–4 | PostgreSQL vacío y migraciones | 107 migraciones aplicadas, 0 pendientes |
| 5 | `makemigrations --check --dry-run` | Sin cambios |
| 6 | `collectstatic` | Funciona; no se usa, Django no sirve estáticos en producción |
| — | `check --deploy` | 1 aviso esperado (W008) |
| 7–9 | Arranque, Caddy y comprobaciones de salud | Los cuatro servicios arriba; sólo Caddy publica puertos; backend sin root; un proceso de gunicorn |
| 10–17 | Portada, catálogo, ficha, carrito, checkout, servicios, nosotros, contacto | 200, con contenido real y sin desbordes a 390 px |
| 13–14 | Añadir al carrito y cotizar el pedido, sin pagar | OK; el desglose de impuestos llega del servidor |
| 18 | Inicio de sesión con el administrador creado con `createsuperuser` | OK |
| 19–22 | Panel, inventario, caja (sin vender), servicio técnico, productos | OK, sin ninguna respuesta 5xx |
| 23 | Cookies de sesión | `Secure`, `HttpOnly`, `SameSite=Lax` |
| 24 | CSRF | Con sesión y sin token: 403. Cerrar sesión sin token: 403 |
| 25 | CORS | Sólo se concede al dominio propio |
| 26 | Origen ajeno con sesión | 403 |
| 27 | `X-Forwarded-For` falso | Django ve la dirección real; el límite no se evita |
| 28 | Límites | Inicio de sesión: 429 desde el sexto intento. Renovación: 429 desde la número 31. Otro cliente con otra dirección no hereda el límite |
| 29 | Apagar y encender | Los mismos datos |
| 30 | `deploy/backup.sh` | Volcado completo y evidencias |
| 31 | Cambio posterior y `deploy/restore.sh` | Vuelve exactamente al estado de la copia; relaciones íntegras |
| — | Admin de Django | No existe: 404 incluso preguntando directamente al backend |
| — | Cuentas de demostración | El comando se niega; la ruta responde 404; la tarjeta no se pinta |
| — | Cabeceras | HSTS, `nosniff`, `X-Frame-Options: DENY`, política de contenido; `no-referrer` en las páginas con token |
| — | `flushexpiredtokens` | Se ejecuta sin error |

Observado y anotado: el inicio de sesión no rechaza por origen. Desde un origen
ajeno, con credenciales erróneas, responde 401 y no 403. Las operaciones con
sesión sí rechazan un origen ajeno (LOGIN-CSRF-01, baja).

No se pudo ensayar en local: el certificado público de Let's Encrypt (necesita el
dominio real), el envío de correo por SMTP y el cobro con Izipay.
