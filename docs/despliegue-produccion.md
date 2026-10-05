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
| Fotos de producto subidas desde el panel o en una carga masiva | Volumen `evidence`, bajo `companies/<id>/storefront/` | Público, por `/api/storefront/images/<id>` |
| Fotos de producto que son una URL externa | En el host que elijas; tiene que figurar en `NEXT_PUBLIC_IMAGE_HOSTS` | Público |
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

El volumen se llama `evidence` por lo primero que guardó. Hoy es **todo el
almacén de archivos**: fotos de producto, imágenes de la tienda, logotipos de los
comprobantes y evidencias. Perderlo es perder todo eso; la copia de seguridad lo
incluye entero (§6.1).

### Tamaño máximo de una petición

Ninguna petición entra sin tope. Lo que exceda el suyo recibe 413 y no llega
entero a la aplicación.

| Ruta | Tope | Por qué |
|---|---|---|
| Carga masiva de productos (libro + imágenes) | 111 MiB | 10 MB del libro y 100 MB de imágenes en una sola petición |
| Foto de evidencia del servicio técnico | 26 MiB | La pantalla acepta fotos de hasta 25 MB |
| Libro de stock, de equipos o a inspeccionar | 11 MiB | El libro admite 10 MB |
| Imagen de la tienda o de un producto | 9 MiB | La pantalla acepta hasta 8 MB |
| Webhook de WhatsApp | 3 MiB | El máximo que documenta Meta |
| Notificación de pago | 128 KiB | Una notificación pesa unos pocos kilobytes |
| Todo lo demás, API y páginas | 1 MiB | Ninguna petición normal se acerca |

El tope se aplica tres veces: en Caddy, en Django antes de leer el cuerpo y en la
pantalla, que es la que sabe explicarlo. Caddy compara lo que la petición anuncia
con el tope de su ruta y responde él mismo, sin llamar a ninguna aplicación; a un
cuerpo que no anunció su tamaño lo corta al llegar al tope. La tabla vive en
`backend/store/request_limits.py` y `deploy/Caddyfile` la repite; una prueba
falla si dejan de coincidir. **Quien suba un límite por variable de entorno
(`STOREFRONT_IMAGE_MAX_UPLOAD_BYTES`, `SERVICE_EVIDENCE_MAX_UPLOAD_BYTES`,
`IMPORT_IMAGES_MAX_TOTAL_BYTES`) tiene que subirlo también en `deploy/Caddyfile`.**

Además Caddy no espera indefinidamente: 15 segundos para las cabeceras, 15
minutos para el cuerpo (una carga masiva por una conexión lenta) y 2 minutos
para una conexión sin uso.

**Caddy lee el cuerpo entero antes de pasarlo** allí donde Django lo leería sin
saber quién llama: la ruta general, la notificación de pago y el webhook de
WhatsApp. Django atiende con ocho hilos, y un cuerpo que no termina de llegar
ocuparía uno mientras dure. En las rutas de archivos no hace falta: Django no
lee el cuerpo hasta haber comprobado la sesión, la cabecera CSRF y el permiso.

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
| Credenciales de Izipay, y cuál de sus dos productos es | Cobrar en línea | `PAYMENT_PROVIDER` y `IZIPAY_*` o `MICUENTAWEB_*` (`docs/pagos-equipos-documentos.md` §1) |
| Dónde se alojan las fotos de producto | Que la web pueda mostrarlas | `NEXT_PUBLIC_IMAGE_HOSTS` |
| Número de WhatsApp Business, plantillas aprobadas, token, secreto de la aplicación y token de verificación | Avisos al cliente por WhatsApp (opcional) | `WHATSAPP_*` y `configure_whatsapp` |
| ID de cliente OAuth de Google, con el dominio como origen autorizado | «Continuar con Google» (opcional) | `GOOGLE_OAUTH_CLIENT_ID` |

Sin Izipay la tienda funciona (catálogo, carrito, panel, punto de venta) pero no
cobra en línea. Sin SMTP el backend **no arranca** con la configuración de
ejemplo: es deliberado, porque sin correo no se puede invitar a nadie ni recuperar
una contraseña. Tampoco arranca sin `EMAIL_BACKEND`: el valor por omisión de
desarrollo escribe cada correo —con sus enlaces de un solo uso— en el registro
del contenedor.

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

**Guarda una copia de `deploy/.env.production` fuera del servidor**, en un gestor
de contraseñas. La copia de seguridad no lo incluye (§6.1). Sin su `SECRET_KEY`
no basta con restaurar la base: las sesiones abiertas caducan y **los enlaces de
seguimiento ya enviados a los clientes dejan de abrir**. Sin su
`POSTGRES_PASSWORD` no se puede leer el volumen de la base.

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
almacén —fotos de producto, imágenes de la tienda, logotipos y evidencias del
servicio técnico; el archivo se llama `evidence-…` por el volumen—, y borra las
copias con más de 14 días.

| Qué | ¿Va en la copia? |
|---|---|
| Base de datos (productos, stock, equipos con serie, ventas, clientes, reparaciones, usuarios, configuración de cada empresa) | Sí, `db-….sql.gz` |
| Fotos de producto, imágenes de la tienda, logotipos, evidencias | Sí, `evidence-….tar.gz` |
| `deploy/.env.production` (claves y contraseñas) | **No.** Se guarda aparte (§4.2) |
| Certificados de Caddy | No. Caddy los vuelve a pedir |
| El código | No. Está en el repositorio |

Cada parte se escribe con otro nombre y sólo se renombra si quedó entera y se
puede leer: un archivo `db-…` o `evidence-…` en la carpeta es siempre una copia
completa. Si una parte falla, el guion termina con error y no toca
`backups/LAST_OK`, que guarda la hora de la última copia completa.

### 6.1.0 Las tareas programadas, de una vez

`deploy/crontab.example` trae las cinco líneas de esta sección —copia, sesiones,
imágenes sin uso, avisos de WhatsApp y comprobación de estado—. Para instalarlas
en el `crontab` del usuario que administra el servidor:

```sh
sed "s|/ruta/al/repositorio|$(pwd)|g" deploy/crontab.example | crontab -
```

Ese comando reemplaza el `crontab` de ese usuario. Las subsecciones siguientes
explican cada línea.

La copia, cada noche:

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

Esta misma limpieza retira las imágenes de una **carga masiva de productos** que
se previsualizó y no se aplicó: por eso entre previsualizar y aplicar hay, como
mucho, esas 24 horas. Sin la tarea programada esas imágenes no se borran nunca.
Las evidencias del servicio técnico no pasan por aquí: no se limpian.

Los límites de imágenes (por archivo, por producto, por carga masiva) y lo que
hay que comprobar antes de publicar una versión con imágenes están en
[imagenes-y-evidencias.md](imagenes-y-evidencias.md) §5.

### 6.1.3 Avisos por WhatsApp pendientes

El primer intento de un aviso por WhatsApp ocurre al confirmarse la operación que lo
causa. Lo que no salió entonces —un fallo que admite reintento, o un proceso
interrumpido— lo envía esta tarea. Una vez por minuto:

```
* * * * * cd /ruta/al/repositorio && docker compose -f docker-compose.prod.yml --env-file deploy/.env.production exec -T backend python manage.py send_pending_notifications >> backups/send_pending_notifications.log 2>&1
```

Es seguro ejecutarla dos veces a la vez y nunca envía un mensaje dos veces. Con
`WHATSAPP_PROVIDER=disabled` (el valor del archivo de ejemplo) no hay nada que
enviar y la tarea no hace falta. Cómo se enlazan las credenciales de cada empresa
y qué registra en Meta: [seguimiento-whatsapp-equipos.md](seguimiento-whatsapp-equipos.md) §4.1.

### 6.1.4 Saber que algo va mal

```sh
sh deploy/healthcheck.sh
```

Una línea por comprobación: `OK`, `NOTA` (conviene saberlo; es un día normal) o
`ATENCIÓN`. Termina con error sólo si alguna dice `ATENCIÓN`. No cambia nada:
sólo mira.

| Qué puede ir mal | Cómo se detecta |
|---|---|
| Un contenedor caído o que no pasa su comprobación | `docker compose ps`, los cuatro servicios |
| La tienda o la API no responden | Se piden `https://<dominio>/` y `/api/categories` |
| La base de datos no responde | `pg_isready` |
| Una actualización se quedó a medias | Migraciones sin aplicar |
| Una notificación de pago que no coincidía con el pedido | Intentos de pago con fallo de integridad, últimas 24 h |
| Un cliente pagó y la tienda no se enteró | **Nota, no alarma**: cuántos pagos siguen sin respuesta de la pasarela. Lo habitual es un comprador que no terminó, y desde la tienda no se distingue de una notificación perdida (PAY-RECONCILE) |
| WhatsApp dejó de funcionar | Tres o más mensajes sin entregar en 24 h y ninguno entregado. Uno suelto es una nota: suele ser un número sin WhatsApp |
| Nadie está enviando los avisos | Mensajes sin enviar desde hace más de 15 minutos |
| Las copias dejaron de hacerse | `backups/LAST_OK` con más de 26 horas |
| El disco se llena | Más del 90 % usado |

Las cuatro del medio las informa la propia aplicación
(`python manage.py ops_status`, sólo lectura, sin datos de clientes).

En el `crontab`, cada 15 minutos. No imprime nada si todo está bien; si algo
falla, imprime las líneas `ATENCIÓN`, y cron las envía al correo de `MAILTO` si
el servidor puede enviar correo. El registro completo queda en
`backups/healthcheck.log`.

Esto vigila desde dentro. **Si el servidor entero se cae, no avisa nadie**: para
eso hace falta un vigilante externo (cualquier servicio de «uptime») que pida
`https://<dominio>/api/categories` cada pocos minutos. Esa dirección consulta la
base de datos: si responde 200, Caddy, Django y PostgreSQL están arriba.

**Los registros no crecen sin límite.** Cada contenedor guarda como mucho 5
archivos de 10 MB (`docker compose logs`). El registro de acceso no guarda lo que
una dirección puede llevar: el enlace de seguimiento de una reparación, el token
de una invitación, un IMEI o un documento escritos en un buscador.

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

Reemplaza la base actual por la de la copia. Comprueba que los dos archivos
existen y se pueden leer, pide escribir `RESTAURAR`, y antes de tocar nada guarda
una copia de lo que hay.

En un servidor nuevo: instalar Docker, clonar el repositorio, poner el
`deploy/.env.production` guardado (§4.2), seguir §4.3 hasta `$C up -d` —con la
base recién creada y sus migraciones— y entonces `sh deploy/restore.sh` con los
dos archivos.

**Prueba la restauración una vez** antes de necesitarla: una copia que nunca se ha
restaurado no se sabe si sirve.

## 6.5 Impresoras de la tienda

El servidor no imprime: deja el ticket en una cola y un programa pequeño dentro de la
red de cada local —el agente— lo entrega a la térmica. El agente llama al servidor por
HTTPS; **no se abre ningún puerto del local**.

1. En el panel, **Administración › Impresoras**: da de alta la térmica con su dirección
   en la red del local (por ejemplo `192.168.1.50`, puerto 9100) y crea un agente para
   esa sucursal. Su token se muestra una sola vez.
2. En un equipo del local que quede encendido, copia `backend/print_agent/agent.py` y
   `config.example.json`, pon la dirección de la tienda y el token en `config.json` y
   ejecuta `python3 agent.py --config config.json`. Sólo necesita Python 3.9.
3. Desde ese momento cada venta confirmada de ese local sale sola por la impresora,
   también las cobradas desde un teléfono.

El detalle —garantías, límites y cómo dejarlo como servicio— está en
`backend/print_agent/README.md`. Un agente perdido se revoca en el panel.

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
- El enlace de seguimiento de una reparación no se puede adivinar, no revela el
  número de orden y muestra la serie y el IMEI enmascarados. La página no se
  indexa y no entrega su dirección a otros sitios.
- Ninguna credencial de WhatsApp vive en la base de datos ni sale por la API: se
  guarda el nombre de la variable, y sólo con el prefijo `WHATSAPP_`. El webhook
  sólo acepta llamadas firmadas.
- «Continuar con Google» se verifica en el servidor y abre la sesión de siempre; no
  se guarda ningún token de Google.

- Ninguna petición entra sin tope de tamaño (§1), y un cuerpo que no termina de
  llegar no ocupa a Django: Caddy lo lee antes.
- Los registros no guardan enlaces de seguimiento, tokens de invitación, el token
  de verificación de WhatsApp ni lo que se escribe en un buscador (IMEI,
  documento, teléfono), y no crecen sin límite.
- Una imagen de producción no lleva claves, certificados, copias ni la
  configuración real del agente de impresión aunque estén junto al código.

Pendiente, y conviene saberlo:

- **Un solo proceso de Django.** Los límites se cuentan en la memoria del proceso,
  así que el backend corre con un proceso y varios hilos. Suficiente para una
  tienda; crecer pide antes una caché compartida (THROTTLE-CACHE-01).
- **Facturación electrónica apagada** (`FISCAL_ENABLED=0`). Encenderla necesita
  certificado digital y credenciales SOL, y es una fase aparte.
- El uso único del intento de «Continuar con Google» y el bloqueo de intentos al
  vincular una orden se cuentan en la memoria del proceso, como los límites
  (THROTTLE-CACHE-01): valen con el proceso único de esta instalación.
- El inicio de sesión no exige token CSRF ni rechaza por origen (LOGIN-CSRF-01,
  baja). Las operaciones con sesión sí: un origen ajeno recibe 403.
- Un inicio de sesión fallido deja en el registro lo que se escribió como usuario
  (nunca la contraseña). Sirve para investigar un ataque; quien escriba su
  contraseña en la casilla del usuario la deja ahí.
- Si un cliente paga y la notificación de la pasarela no llega, el pedido queda
  esperando (PAY-RECONCILE). `deploy/healthcheck.sh` anota cuántos pagos siguen sin
  respuesta, sin dar la alarma: no se distingue de una compra abandonada. Si un cliente
  dice que pagó, se comprueba en el panel de la pasarela.
- La caja no deja elegir qué equipo con serie se vende: asigna el más antiguo y
  hay que entregar el que nombra la nota (SERIAL-PICK,
  [seguimiento-whatsapp-equipos.md](seguimiento-whatsapp-equipos.md) §5).

## 9. Qué se ensayó

El ensayo es un guion y se puede repetir:

```sh
sh deploy/rehearsal.sh
```

Construye las dos imágenes sin caché, arranca PostgreSQL vacío, recorre la tienda
a través de Caddy como lo haría un cliente y lo desmonta todo. Usa un proyecto de
Docker, unos puertos y un dominio reservados (`bds-rehearsal`, 18080/18443,
`tienda.test`) y un certificado interno de Caddy. No sale a Internet, no envía
correo y no cobra. Termina con `ENSAYO: OK` o con el número de fallos.

Última pasada: 2026-10-05, sobre `81421d9` (rama `chore/production-readiness-01`,
`master` `78ad79c` incorporado). Resultado: `ENSAYO: OK` — 107 comprobaciones del
guion y 49 pasos de navegador, 0 fallos.

La pasada anterior sobre `master` (`78ad79c`) dio `ENSAYO: 7 FALLO(S)`, los siete
por una sola comprobación del propio guion, que seguía pidiendo a la portada una
variante de hero que la V3 no tiene. La aplicación no fallaba en nada de lo demás.

| # | Comprobación | Resultado |
|---|---|---|
| 1 | Construcción sin caché | Dos imágenes. Sin `.env` ni base dentro; el backend corre sin root (uid 10001); el frontend no lleva ningún secreto en su entorno ni en los archivos que sirve; el backend no lleva claves ni copias |
| 2–3 | PostgreSQL 16 vacío y migraciones | 122 migraciones de `store` aplicadas, 0 pendientes; `makemigrations --check` sin cambios |
| 4 | Arranque | Backend y frontend sanos; sólo Caddy publica puertos; un proceso de gunicorn; ningún error en el registro de arranque; tope de tamaño y rechazo por longitud anunciada en las ocho rutas de Caddy; tiempos de espera; los cuatro contenedores rotan su registro |
| 5 | Ajustes efectivos | `DEBUG=False`; cookies `Secure` y `HttpOnly`; un proxy de confianza; sólo JSON; sin admin de Django |
| 6 | Datos de demostración | `seed_demo_users` se niega; ninguna cuenta `dev_`; la ruta responde 404 |
| 7 | Rutas por Caddy | Tienda, ficha, carrito, checkout, panel y API: 200. `/admin/login/`, `/static/admin/…`, `/media/…` y `/private-media/…` no llegan a Django ni a un archivo. Host desconocido: sin respuesta |
| 8 | Cabeceras | HSTS una sola vez, `nosniff`, `X-Frame-Options: DENY`, política de contenido, `Permissions-Policy`; `no-referrer` en las páginas con token; no se anuncia el servidor |
| 9–10 | Carrito, cotización, CORS | Añadir y cotizar: 200. CORS sólo para el origen propio |
| 11 | **Imágenes de la tienda** | Cinco PNG sin fondo subidos (hero, categoría, servicio, ubicación, campaña): 200 para cualquiera, `image/png`, transparencia intacta, caché inmutable |
| 11 | **Evidencia privada, en el mismo volumen** | Sin sesión: 401. Con sesión de quien no trabaja en la empresa: no se entrega. Quien trabaja en ella: 200, sin caché pública |
| 11 | **Sin rutas de archivo** | Las claves reales del almacén (una imagen y una evidencia) pedidas por `/media/`, `/private-media/`, `/app/private-media/`, `/api/media/` y `/static/`: ninguna se sirve |
| 11 | Subidas rechazadas | SVG, SVG y HTML con extensión `.png`, GIF, PNG truncado y archivo de 9 MB: rechazados. Nombre con `../`: la dirección no lo conserva. Sin sesión: 401 |
| 11b | **Límites de tamaño** | 2 MiB anunciados al inicio de sesión o a una página, 300 MiB a una ruta cualquiera o a la carga masiva, 10 MiB a una imagen, 30 MiB a una evidencia y 200 KiB a la notificación de pago: 413 sin esperar el cuerpo. Un cuerpo real de 2 MiB: 413 con un mensaje que la pantalla entiende. Una imagen de 3 MiB y una evidencia de 10 MiB: aceptadas |
| 11b | **Cuerpos que no terminan de llegar** | Con nueve peticiones a medio enviar a una ruta cualquiera, a la notificación de pago o al webhook de WhatsApp, la API responde en menos de un segundo |
| 11b | **Notificación de pago** (claves generadas en el ensayo) | Sin firma, con firma inventada o con el importe cambiado tras firmar: 400. Bien firmada, de un pago que la tienda no abrió: no paga nada, y repetida responde igual. La del producto de Izipay que no está configurado: 404 |
| 11b | **Equipo con serie** | Registrar un equipo: stock +1 y tablero +1. Venderlo: queda «vendido», stock −1, tablero −1. Venderlo otra vez: «stock insuficiente». El mismo IMEI no entra dos veces. Por cantidad no se ajusta |
| 11b | **Documentos** | Nota de venta A4 y ticket de 80 mm: PDF con la serie y el IMEI del equipo vendido. Comprobante de pedido: PDF |
| 11b | **Seguimiento** | El enlace abre sin sesión, sin indexar y sin referente; IMEI enmascarado; alterado responde 404 |
| 11b | **WhatsApp apagado, Google sin configurar** | La orden se crea igual y no queda ningún mensaje enviado ni pendiente; la pantalla dice qué falta. Google se anuncia apagado, su entrada responde 404 y el acceso con usuario sigue |
| 11b | **Registros** | Tras todo lo anterior no contienen el enlace de seguimiento, ningún IMEI usado, la contraseña del administrador, la clave de la pasarela ni tokens de sesión |
| 12 | Navegador real | Portada, catálogo, ficha, carrito, checkout, servicios, nosotros, contacto y acceso a 390 y 1440 px: sin desbordes, sin imágenes rotas y sin errores de script. Las cinco imágenes de la tienda a 320, 390, 768 y 1440 px, en claro y en oscuro. Seguimiento de una reparación sin sesión. Sin botón de Google. Categorías con teclado. Sesión, panel y catorce pantallas del panel, entre ellas equipos, cargas masivas, impresoras y mensajería |
| 13 | CSRF | Con sesión, desde un origen ajeno o sin token: 403 |
| 14 | Tareas programadas | `flushexpiredtokens`, `send_pending_notifications` y `cleanup_storefront_images --dry-run` corren; la limpieza no toca nada colocado |
| 15 | Límites | Inicio de sesión: 429 desde el sexto intento aunque cambie `X-Forwarded-For`; Django ve la dirección real. Renovación: 429 desde la número 31. Otro cliente no hereda el límite |
| 16 | Apagar y encender | Los mismos datos y los mismos archivos; imágenes y evidencias responden igual |
| 17 | Reconstruir y recrear contenedores | Lo mismo |
| 16–17 | Lo que más importa conservar | Tras apagar, reconstruir y recrear: la foto de producto y el enlace de seguimiento siguen funcionando; equipos, equipos vendidos y notas de venta, los mismos |
| 18 | `deploy/backup.sh` | Volcado completo y archivo con fotos de producto, imágenes de la tienda y evidencias |
| 18b | `deploy/healthcheck.sh` | Todo sano: 0. Con el backend detenido: error, y nombra el contenedor y la API. Al encenderlo: 0 otra vez |
| 18b | Registro de Caddy con el backend caído | Anota el fallo (502) sin la dirección de la petición: no aparece el enlace de seguimiento pedido |
| 19 | **Prueba de recuperación**: daño y `deploy/restore.sh` | Se borran todos los archivos y se crea una cuenta nueva; tras restaurar, datos y archivos son los de la copia —equipos, ventas, notas y enlaces de seguimiento incluidos—, la foto de producto se sirve, el enlace abre y la cuenta posterior no existe |
| 21 | Desmontaje | No queda ningún contenedor, volumen, imagen ni archivo de variables |

Observado y anotado: el inicio de sesión no rechaza por origen. Desde un origen
ajeno, con credenciales erróneas, responde 401 y no 403. Las operaciones con
sesión sí rechazan un origen ajeno (LOGIN-CSRF-01, baja).

`check --deploy` avisa de `SECURE_SSL_REDIRECT` (W008). Es deliberado: la
redirección a HTTPS la hace Caddy, que es quien termina TLS; hacerla también en
Django rompería la comprobación de salud interna.

No se puede ensayar en local: el certificado público de Let's Encrypt (necesita
el dominio real), el envío de correo por SMTP, el cobro con Izipay —el ensayo
comprueba la notificación con claves propias, no la pasarela—, el envío por
WhatsApp y el acceso con Google.

## 10. Lista de publicación

Qué falta para publicar, y de quién depende.

- **LISTO** (CODE READY): está en el código y pasó el ensayo.
- **FALTAN DATOS** (BLOCKED/CREDENTIALS): sólo el propietario puede darlos.
- **FALTA INFRAESTRUCTURA** (BLOCKED/INFRA): hay que contratarla o configurarla fuera
  del código.
- **OPCIONAL** (OPTIONAL): la tienda publica sin ello.

| | Qué | Estado | Dónde |
|---|---|---|---|
| ☐ | Dominio | FALTA INFRAESTRUCTURA | §2, §4.1 |
| ☐ | DNS (`A` del dominio y de `www` al servidor) | FALTA INFRAESTRUCTURA | §4.1 |
| ☐ | TLS | LISTO: Caddy lo pide solo cuando el DNS responde | §4.1 |
| ☐ | Servidor (VPS, 4 GB de RAM, puertos 22, 80 y 443) | FALTA INFRAESTRUCTURA | §2, §4.1 |
| ☐ | PostgreSQL | LISTO: contenedor y volumen `pgdata` | §1 |
| ☐ | Volumen de archivos (fotos de producto, tienda, evidencias) | LISTO: sobrevive a reinicio, reconstrucción y restauración | §1, §9 |
| ☐ | Variables y secretos (`deploy/.env.production`) y su copia fuera del servidor | FALTAN DATOS | §4.2 |
| ☐ | SMTP | FALTAN DATOS: servidor, usuario y contraseña. Sin él el backend no arranca | §3 |
| ☐ | Migraciones | LISTO: paso explícito de cada despliegue; el estado avisa si quedan sin aplicar | §4.3, §7 |
| ☐ | Primer administrador | LISTO: `createsuperuser` | §4.4 |
| ☐ | Tareas programadas | LISTO: `deploy/crontab.example` | §6.1.0 |
| ☐ | Copias de seguridad | LISTO: `deploy/backup.sh`, con prueba de recuperación | §6.1, §9 |
| ☐ | Copia externa | FALTAN DATOS: el destino | §6.2 |
| ☐ | Comprobación de estado | LISTO: `deploy/healthcheck.sh` | §6.1.4 |
| ☐ | Vigilante externo y correo de avisos (`MAILTO`) | FALTA INFRAESTRUCTURA | §6.1.4 |
| ☐ | Registros | LISTO: rotan y no guardan tokens ni datos de búsqueda | §6.1.4 |
| ☐ | Izipay, validado en TEST | FALTAN DATOS: cuál de los dos productos y sus claves de TEST. Sin eso no hay prueba contra la pasarela real | `pagos-equipos-documentos.md` §1 |
| ☐ | Izipay, producción | FALTAN DATOS: claves de producción, después del pago de prueba | `pagos-equipos-documentos.md` §1.3 |
| ☐ | WhatsApp | OPCIONAL · FALTAN DATOS: número, plantillas aprobadas y credenciales. Apagado no rompe nada | `seguimiento-whatsapp-equipos.md` §4.1 |
| ☐ | «Continuar con Google» | OPCIONAL · FALTAN DATOS: ID de cliente OAuth. Sin él el botón no aparece | `seguimiento-whatsapp-equipos.md` §7 |
| ☐ | Facturación electrónica (SUNAT) | OPCIONAL · FALTAN DATOS: certificado y credenciales SOL. Va apagada (`FISCAL_ENABLED=0`); encenderla es una fase aparte | §8 |
| ☐ | Impresoras de tienda | OPCIONAL: se dan de alta en el panel y se instala el agente en el local | §6.5 |
| ☐ | Existencias, fotos y precios reales | FALTAN DATOS: una base nueva trae tres productos de ejemplo | §5 |
| ☐ | Volver atrás | LISTO: copia antes de actualizar, `git checkout` del commit anterior y, si una migración cambió datos, `restore.sh` | §7 |
| ☐ | Repetir `sh deploy/rehearsal.sh` sobre el commit que se publica | LISTO: es un comando | §9 |

Antes de cobrar de verdad: un pago completo en TEST con la tienda ya publicada. Es
la única forma de ver llegar una notificación real de la pasarela.
