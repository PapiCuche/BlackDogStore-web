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

Sólo Caddy publica puertos. Django, Next y PostgreSQL no son alcanzables desde
internet.

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

Deja en `backups/` un volcado completo de PostgreSQL y las evidencias del servicio
técnico, y borra las copias con más de 14 días. El volcado sólo se guarda si
terminó entero.

Para hacerla cada noche, en el `crontab` del servidor:

```
15 3 * * * cd /ruta/al/repositorio && sh deploy/backup.sh >> backups/backup.log 2>&1
```

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
elimina el volumen de la base de datos.

## 8. Seguridad

Ya resuelto por el código o por esta configuración:

- `DEBUG` vale `0` y no puede cambiarse desde el archivo de variables.
- Sin `SECRET_KEY` real, `ALLOWED_HOSTS` y `CORS_ALLOWED_ORIGINS`, Django se niega a
  arrancar.
- Los accesos de demostración responden 404 y la tarjeta de `/auth` no se pinta.
- Las cookies de sesión son `Secure` y `HttpOnly`; las peticiones que modifican
  exigen CSRF y un origen del propio dominio.
- El admin de Django (`/admin/` del backend) no se publica: Caddy sólo envía
  `/api/*` a Django. Lo que haga falta se hace desde el panel de la aplicación o
  con `$C exec backend python manage.py shell` en el servidor.
- Los límites de peticiones se cuentan por la dirección real del cliente. Una
  cabecera `X-Forwarded-For` falsa no los evita.

Pendiente, y conviene saberlo:

- **Un solo proceso de Django.** Los límites se cuentan en la memoria del proceso,
  así que el backend corre con un proceso y varios hilos. Suficiente para una
  tienda; crecer pide antes una caché compartida (THROTTLE-CACHE-01).
- **Sin integración continua de backend** en el repositorio (CI-01).
- **Facturación electrónica apagada** (`FISCAL_ENABLED=0`). Encenderla necesita
  certificado digital y credenciales SOL, y es una fase aparte.
- El inicio de sesión no exige token CSRF cuando el origen es el propio dominio;
  un origen ajeno recibe 403.

## 9. Qué se ensayó

Con `SITE_DOMAIN=localhost` y un certificado interno de Caddy:

| Comprobación | Resultado |
|---|---|
| Compilar las dos imágenes | OK |
| `migrate` sobre PostgreSQL nuevo | OK |
| `check --deploy` | 1 aviso esperado (W008) |
| Puerto 80 redirige a HTTPS | 308 |
| Tienda, catálogo, carrito, servicios, inicio de sesión | 200 |
| API sin barra final, con y sin parámetros | 200 |
| Accesos de demostración | 404 |
| Admin de Django desde fuera, incluido con `..` en la ruta | no alcanzable |
| Inicio de sesión real, cookies `Secure` + `HttpOnly` | OK |
| Petición desde un origen ajeno | 403 |
| Cerrar sesión sin CSRF | 403 |
| Carrito anónimo: añadir y leer | OK |
| Siete intentos de inicio de sesión con `X-Forwarded-For` distinto | 429 desde el sexto |
| `down` y `up`: los datos siguen | OK |
| Copia, cambio posterior y restauración | vuelve al estado de la copia |

No se pudo ensayar en local: el certificado público de Let's Encrypt (necesita el
dominio real), el envío de correo por SMTP y el cobro con Izipay.
