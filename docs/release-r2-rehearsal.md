# Candidato R2 y ensayo de lanzamiento (REL-R2)

> **Actualización de cierre, 2026-10-26:** R2 fue desplegado con SHA `42eccc807b07170f81c3ab55a2fcf5c79851b823`, hasta la migración `0155`, y el propietario confirmó seis verificaciones funcionales. Estado **DEPLOYED / ACCEPTED**. Las frases sobre candidato pendiente, ensayo exclusivamente local y ausencia de despliegue en las secciones históricas describen el estado **anterior** al despliegue. Finanzas `0156`–`0158` permanecen fuera de producción.

**Recomendación: GO CON CONDICIONES** (sección 9). No hay ninguna incompatibilidad que impida desplegar. Las nueve migraciones se aplicaron sobre la base sintética y sobre una restauración local de la copia de producción. En los dos casos:
- no dieron errores y tardaron unos 3 s en total;
- conservaron los conteos;
- otorgaron los permisos tal como están documentados;
- el esquema final es idéntico al de una base migrada desde cero.

El código de producción (`6834bc2`) siguió sirviendo sobre el esquema nuevo, y las dos vueltas atrás ensayadas funcionan. Hay dos hallazgos operativos nuevos, sin bloqueo:
- **H1** (§4.4, §7): el editor de roles de `6834bc2` no guarda un rol que tenga un permiso de R2.
- **H2** (§4.3, §7): los roles creados por el código anterior durante la ventana de migración no reciben los permisos nuevos.

Ensayo **local**, 2026-10-25. No se desplegó nada, no se tocó el servidor de producción ni su configuración, y no se hizo SSH. La copia de producción la autorizó el propietario y sólo se restauró en local (§6). Las bases que salieron de ella se borraron al terminar.

## 1. SHA y montaje

| | Valor |
|---|---|
| Producción hoy | `6834bc28a78fb496a8356e24d6a99b1dbb637c84` (R1, desplegado el 2026-10-23), `store` hasta `0145_inventory_ai_usage` |
| Candidato (congelado) | `42eccc807b07170f81c3ab55a2fcf5c79851b823` (master tras #186; hoja `0155_pos_sale_key_reservation`, sin `0156` ni finanzas). CI de master en ese SHA (`push`): backend PostgreSQL y frontend en verde. El primer ensayo (§2–§7) se hizo sobre `c3ee89e`; la diferencia es #189 y #186, sin migraciones, y se reensayó en el §10 |
| Código anterior | worktree separado en `6834bc2`; sólo su `manage.py` |
| PostgreSQL | contenedor `postgres:16` (16.15, la misma versión que el volcado de producción), en `127.0.0.1` y sobre `tmpfs` |
| Bases sintéticas | `blackdog_r2_syn` (principal), `blackdog_r2_syn0145` (foto en 0145), `blackdog_r2_rba` (vuelta atrás de esquema), `blackdog_r2_fresh` (desde cero, para comparar) |
| Entorno | `DEBUG=1`, `FISCAL_ENABLED=0`, `WHATSAPP_PROVIDER=fake`, envíos en línea apagados, correo `locmem`, sin clave de integraciones, `HTTP(S)_PROXY` a un puerto muerto |
| Scripts | [release-r2-rehearsal/](release-r2-rehearsal/) |

Scripts:
- `counts.sh` sólo imprime conteos.
- `old_seed.py`, `old_smoke.py` y `old_roles.py` corren con el código ANTERIOR.
- `new_writes.py` corre con el código NUEVO.
- `cancel_migration_counts.py`, `strip_r2_capabilities.sql` y `regrant_r2_capabilities.py` son los pasos de la vuelta atrás.
- `local_accounts.py` crea cuentas de prueba, y sólo en una restauración local.

Las contraseñas de los scripts son las de desarrollo de `seed_demo_users`. Las cuentas locales de la restauración usan una contraseña aleatoria que no se guardó.

## 2. Contenido de R2 (`6834bc2..c3ee89e`; el candidato final añade #189 y #186, §10)

En el rango hay 18 PR fusionados, todos de primer padre.
- No hay ninguno indirecto: #181 y #184 iban apilados, pero entraron como PR propios.
- **No entran** #180 (ADR de sesiones, `docs/adr-auth-sessions.md`, abierto), #173 (ADR fiscal de producción, abierto), #169 ni #162.
- El código cita `docs/adr-auth-sessions.md`, pero el archivo no está en el candidato.
- `02_ESTADO_ACTUAL.md` y `07_CHANGELOG.md` todavía dicen que AUTH-B/AUTH-C están «en borrador»: hay que actualizarlos al desplegar.

| Área | PR | Qué cambia para quien usa el sistema |
|---|---|---|
| Docs | #164, #165, #166, #172 | Registro de R1 y ADR de etiquetas, recuento de migración y centro contable. Sin cambios de comportamiento. |
| Etiquetas / códigos | #167 LABEL-01 (0146) | Un producto creado **sin código** recibe `P000001`, `P000002`, … de una serie por empresa: en el panel, en el registro guiado y en la importación. Ver los detalles debajo de la tabla. |
| Importación histórica | #170 LABEL-MIGRATION-01 + MIGRATION-B | Modo «Base histórica» en la importación de productos. Ver los detalles debajo de la tabla. |
| Recuento | #175 MIGRATION-C (0147, 0148) | Motor del recuento de migración y nuevos bloqueos de la aprobación. Ver los detalles debajo de la tabla. |
| Recuento (pantalla) | #171 MIGRATION-D | `/admin/inventory/counts/[id]` es nueva: escáner o buscador, «Cantidad total» o «Sumar 1», blanco ≠ 0, paneles de pendientes, ambiguos y solapados, «Recontar» y «Enviar a revisión». Desaparece la rejilla de «primeros 100 productos». **El recuento de migración no se aprueba en la pantalla** (MIGRATION-F no está en R2), ni ninguna pantalla lo abre. |
| Centro contable | #174, #177 ACCOUNTING-02/03 (0149) | Pantalla nueva `/admin/accounting`, de sólo lectura, con cuatro API GET. Ver los detalles debajo de la tabla. |
| Fiscal | #176 FISCAL-PROD-02, #179 H-02 (0153) | Las credenciales y el par de firma se comprueban **antes** de reservar un correlativo. Ver los detalles debajo de la tabla. |
| Etiquetas de equipo | #178 LABEL-02 (0151, 0152) | Cada equipo que se recibe obtiene `label_code` (`U` + 9 dígitos + Luhn). Ver los detalles debajo de la tabla. |
| Etiquetas (pantallas) | #168 LABEL-03, #182 LABEL-03b | `/admin/inventory/labels` (vista previa sin escrituras y PDF a escala real 30×20, 50×25 y 60×40) y `/admin/inventory/labels/settings`. Ver los detalles debajo de la tabla. |
| Seguimientos | #183 | La aprobación vuelve a leer bajo bloqueo; las cantidades de referencia sólo vienen de filas de alta o actualización; aviso `code_series` en la vista previa de la importación; una prueba inestable de la IA corregida. |
| Sesión web | #181 AUTH-B (0154), #184 AUTH-C (0155) | Ver §2.1. |

Detalles por área:
- **#167 LABEL-01.** El formulario de alta tiene «Código (opcional)». La ficha del producto tiene el panel de códigos: ver, copiar, añadir y «Generar».
  - Se rechaza un código igual al IMEI o la serie de un equipo (409).
  - Los productos existentes **no** se codifican solos.
- **#170 LABEL-MIGRATION-01 + MIGRATION-B.**
  - Plantilla nueva `?variant=historical`.
  - Las filas que coinciden por código van a revisión, y aplicar exige teclear cuántas son.
  - Las cantidades antiguas se guardan como referencia y nunca mueven stock.
  - Hay un CSV de conciliación.
- **#175 MIGRATION-C.**
  - Las vistas de recuento pasan a `count_views.py`, con las mismas URL y nombres, más `products/`, `scan/`, `recount/`, `exclude/` y `submit/`.
  - Aprobar exige `inventory.counts.approve`.
  - En **cualquier** recuento, una línea ambigua o solapada bloquea la aprobación (`approval_blocked`).
  - `theoretical_at_start` puede ser nulo y `difference_value` sólo se muestra con `costs.view`.
  - La carga INICIAL de stock rechaza un producto que esté en un recuento de migración abierto.
- **#174 y #177 ACCOUNTING-02/03.**
  - Rutas: `admin/accounting/{summary,operations,fiscal-documents,pending}/`.
  - `accounting.export` sólo desenmascara documentos y no hay exportación de archivos.
  - **Regla provisional P4:** un pago con claves Izipay **TEST** se excluye de los ingresos (`excluded_test`). Producción opera con TEST, así que el centro mostrará ingresos web casi nulos.
- **#176 y #179 fiscal.**
  - Si las credenciales o la firma fallan: 503 y la auditoría `fiscal_issue_refused`.
  - Un envío de resultado incierto queda `TRANSPORT_UNKNOWN`, y su reenvío se rechaza (409).
  - En la caja aparece la opción deshabilitada `FISCAL_NOT_CONFIGURED`.
  - El CDR se guarda byte a byte, con su ZIP y su hash. Las filas antiguas no cambian.
- **#178 LABEL-02.**
  - El código del equipo se resuelve primero en la caja.
  - Recepción: 409 si un IMEI o una serie coincide con un código de producto o con el código de otro equipo.
  - Ruta nueva `admin/label-settings/`.
- **#168 y #182 LABEL-03 / LABEL-03b.**
  - Un PDF que muestra el IMEI se audita (`label_sensitive_printed`) y se sirve `no-store`.
  - Un equipo sin código va a revisión.

Sin cambios en estos frentes:
- **Dependencias:** ni en `backend/requirements.txt`, `frontend/package.json` y `package-lock.json`, ni en los Dockerfile, `docker-compose*.yml`, `deploy/Caddyfile` o `deploy/crontab.example`.
- **Ficheros de entorno:** ni en `deploy/.env.production.example` ni en `.env.example`.
- **Infraestructura:** no hace falta ningún cron nuevo. El `Caddyfile` modificado a mano en el servidor se conserva, porque el candidato no toca ese archivo.

### 2.1 Sesión web (AUTH-B #181 + AUTH-C #184)

- **Al desplegar se cierran una vez TODAS las sesiones web**, de personal y de clientes. Un cookie de `6834bc2` no lleva `sid`. Ensayado (§4.5):
  - `auth/me` responde 401 con `X-Session-Expired: 1`;
  - la tienda sigue cargando como invitado;
  - `auth/refresh` responde 401.
- El panel envía a `/auth?reason=idle`, que dice «cerró por inactividad». El mensaje es inexacto el día del despliegue (P4 provisional).
- **Reglas nuevas:**
  - 30 min sin actividad para todas las sesiones web;
  - tope absoluto de 8 h para el personal y de 7 días para los clientes;
  - cerrar sesión, cambiar o restablecer la contraseña o desactivar la cuenta terminan la sesión.
- **Qué cuenta como actividad:** sólo una petición con `X-Session-Activity: 1`, que pase el CSRF y termine en 2xx/3xx. La envía el frontend nuevo cuando una persona teclea o pulsa. Los sondeos no cuentan.
  - **Consecuencia:** una pestaña que siga con el JavaScript de R1 no envía esa cabecera y su sesión caduca a los 30 min aunque se use. Se arregla recargando la página (aviso del §8, paso 1).
- **Aviso:**
  - en el panel aparece a los 25 min;
  - en `/checkout`, antes de pagar, se pregunta si la sesión sigue viva y se ofrece «Iniciar sesión» o «Comprar como invitado».
- **Caja:** la venta en vuelo se conserva en `sessionStorage`. Tras volver a entrar, «volver a comprobar» consulta `admin/pos/sales/by-key/` y bloquea el cobro hasta resolverla. «No se cobró» deja una `PosSaleKeyReservation`, y esa clave ya no puede cobrarse (409 `key_reserved`).
- **`/admin`** se sirve con `Cache-Control: no-store`, comprobado en el ensayo.
- **App móvil V1 (Bearer):** el contrato no cambia, comprobado en §4.5. Login, refresh y `me` funcionan y no se crean filas `AuthSession`. Lo único nuevo es que se rechaza un token que lleve `sid`.
- **Google:** pasa por la misma emisión de sesión. AUTH-D (autoridad del correo y `nonce` en la base) **no** entra. Que Google esté activo sigue dependiendo de `GOOGLE_OAUTH_CLIENT_ID`.
- **Configuración nueva**, toda opcional y que **en producción no se define**:

  | Variable | Valor por omisión |
  |---|---|
  | `AUTH_SESSION_IDLE_SECONDS` | 1800 |
  | `AUTH_SESSION_ACTIVITY_GRANULARITY` | 30 |
  | `AUTH_STAFF_SESSION_MAX_SECONDS` | 28800 |
  | `AUTH_CUSTOMER_SESSION_MAX_SECONDS` | 604800 |

  - Con `DEBUG=0`, Django no arranca si alguna supera su valor por omisión.
  - `deploy/preflight.py` (`check_sessions`) marca como fallo cualquier valor distinto del de omisión.
- **Otros cambios de la sesión:**
  - throttles `session_status` 120/min y `session_keepalive` 12/min;
  - middleware `WebSessionMiddleware`;
  - manejador de excepciones `store.auth_sessions.exception_handler`;
  - `CORS_EXPOSE_HEADERS`.
- **Deuda:** no hay purga de `AuthSession` (una fila por inicio de sesión) ni de `PosSaleKeyReservation`.

### 2.2 Órdenes nuevas (ninguna corre sola)

| Orden | Qué hace | Por omisión | ¿Después del despliegue? |
|---|---|---|---|
| `audit_code_collisions [--company]` | Lista los códigos de producto iguales a un IMEI, una serie o un código de equipo. Sólo imprime ids | Sólo lectura | Sí, antes de la siguiente |
| `assign_unit_codes --company <slug> [--apply]` | Da `label_code` a los equipos existentes. Es idempotente y la primera vez crea `CompanyLabelSettings` | En seco | Con el OK del propietario: sin código, sus equipos van a revisión en las etiquetas |
| `assign_internal_codes --company <slug> [--apply]` | Codifica los productos activos sin código | En seco | **No.** Regla del propietario: no antes de LABEL-MIGRATION-01 con el archivo real |
| `fiscal_beta_smoke` (cambiada) | Resuelve y comprueba las credenciales antes de reservar, y firma los resúmenes de forma atómica | — | — |

### 2.3 Lo que tiene que seguir APAGADO, y lo que espera al propietario

- **SUNAT en producción no se puede activar.**
  - `FISCAL_ENABLED` vale `False` por omisión (`settings.py:277`).
  - `fiscal_config.resolve_environment()` falla cerrado ante cualquier ambiente que no sea `beta` (`ONLY_SUPPORTED_ENVIRONMENT = BETA`).
  - R2 sólo añade comprobaciones que fallan cerrado.
- **R2 no enciende nada:** ni la emisión fiscal, ni Google, ni el proveedor de IA (sin cambios en `INVENTORY_AI_*`), ni los cobros por WhatsApp, ni los pagos en modo producción (los módulos de pago no cambian).
- **Decisiones provisionales que R2 ya trae dentro**, y que el propietario debe confirmar o corregir:
  - **Contabilidad P3:** `accounting.*` para quien tenga `company.manage`. Son provisionales también P1 (reparaciones por caja), P2 (`paid_at`) y P4 (los pagos TEST se excluyen).
  - **Etiquetas P11:** `inventory.labels.sensitive` sólo para un Administrador sin modificar.
  - **Etiquetas P3:** la serie de producto empieza en `P` + 6 dígitos. **El primer producto que se cree sin código después del despliegue fija la serie**: si el propietario prefiere continuar la serie antigua, tiene que decirlo antes.
  - **Etiquetas P5:** prefijo de equipo `U`.
  - **Etiquetas, valor por omisión del dispositivo:** `internal`. La decisión 3 del propietario pedía IMEI 1, que exige `device_symbol=imei1` y el permiso sensible.
  - **Recuentos P6:** aprobar para todo el que tenga `inventory.adjust`.
  - **Sesiones:** P1–P5 (§12.1 del ADR de #180, que no se ha fusionado).

## 3. Migraciones `0146` → `0155` (todas de `store`)

`git diff --name-only 6834bc2 c3ee89e -- '**/migrations/*'` devuelve sólo nueve archivos de `store`:
- **otras apps:** ninguna migración de `token_blacklist`, `auth`, `admin`, `contenttypes` ni `sessions`;
- **`0150`:** no existe a propósito (cabecera de `0151`).

Cada migración es una transacción, y el SQL sale de `sqlmigrate`. En producción las tablas afectadas son diminutas (§6.2): los tiempos de bloqueo son de milisegundos. Si una tabla fuese grande, las columnas nuevas serían igualmente sólo de metadatos (PostgreSQL ≥ 11, con valores por omisión constantes). Lo que escalaría con el tamaño son los `CREATE INDEX` sin `CONCURRENTLY`, que toman `SHARE` sobre la tabla y bloquean las escrituras mientras leen, y el `CHECK` de `0151`, que valida leyendo la tabla con `ACCESS EXCLUSIVE`.

| Mig. | Operaciones | Datos | Bloqueos | Con el código anterior encima | Vuelta atrás |
|---|---|---|---|---|---|
| `0146` | `AlterField` de las opciones de `InternalSequence.document_type` (`product_code`) | Ninguno; `sqlmigrate` está vacío | Ninguno | Sin efecto | Reversible y vacía |
| `0147` | Recuento: 4 columnas nulas y `kind` con `db_default='regular'` en `inventorycount`; 10 columnas nulas o con `db_default` en `inventorycountitem`. Ver la nota de bloqueos | Ninguno | Ver la nota de bloqueos | **Compatible** (ensayado): el código anterior crea, llena y aprueba recuentos ordinarios, y sus filas reciben `kind='regular'` | Reversible, pero ver la nota sobre la vuelta atrás |
| `0148` | `RunPython`: `inventory.counts.approve` a **todo** rol con `inventory.adjust`, editados incluidos | Filas de `CompanyRole`; idempotente | Bloqueos de fila en `companyrole` | El código anterior autoriza bien con el código desconocido, pero **no guarda** ese rol (H1) | `noop` a propósito: los permisos se quedan |
| `0149` | `RunPython`: `accounting.view` y `accounting.export` a todo rol con `company.manage` (P3 provisional) | Igual; idempotente | Igual | Igual (H1) | `noop` |
| `0151` | Tabla `companylabelsettings` (`company_id` único, FK → `store_company`); `stockunit.label_code` `varchar(24) NULL`. Ver la nota de bloqueos | Ninguno: los equipos existentes se codifican con `assign_unit_codes` | Ver la nota de bloqueos | **Compatible**: el código anterior recibe equipos con `label_code NULL` | Reversible. **Borra los códigos**: las etiquetas impresas dejan de valer y, al volver a subir, salen códigos nuevos |
| `0152` | `RunPython`: `inventory.labels.print` a todo rol con `inventory.view`; `inventory.labels.sensitive` sólo a un rol idéntico al Administrador anterior (`ADMIN_PREVIOUS`) | Idempotente | Igual que `0148` | Igual (H1) | `noop` |
| `0153` | `cdr_raw` y `cdr_zip` (`bytea NULL`) y `cdr_zip_sha256` (`db_default=''`) en `fiscaldocument`, `fiscaldailysummary` y `fiscalvoidcommunication` | Ninguno | `ACCESS EXCLUSIVE` breve en las tres tablas, sólo metadatos | **Compatible**: un documento escrito por el código anterior recibe `cdr_zip_sha256=''` | Reversible. **Borra los bytes del CDR** (en producción no hay: SUNAT apagado) |
| `0154` | Tabla `store_authsession` (`sid` único, FK → `auth_user`, dos índices) | Ninguno | `SHARE ROW EXCLUSIVE` breve en `auth_user` (FK) | **Compatible**: el código anterior no la lee | Reversible. **Borra las sesiones** |
| `0155` | Tabla `store_possalekeyreservation` (único `(company, idempotency_key)`, FK → `store_company` y `auth_user`) | Ninguno | `SHARE ROW EXCLUSIVE` breve en `store_company` y `auth_user` | **Compatible** (el código anterior no la lee) | Reversible. **Borra las decisiones «no se cobró»** |

Notas a la tabla:
- **`0147`, resto de operaciones.**
  - `DROP NOT NULL` en `theoretical_at_start`.
  - FK `source_job` → `bulkimportjob`, `submitted_by` y `counted_by` → `auth_user`.
  - Índice parcial único `one_open_migration_count_per_branch`.
  - Tres índices de FK.
- **`0147`, bloqueos.**
  - `ACCESS EXCLUSIVE` en `inventorycount` e `inventorycountitem` hasta el `COMMIT`: 0,76–0,83 s ensayados.
  - `SHARE ROW EXCLUSIVE` en `auth_user` y `bulkimportjob` por las FK. Durante esa fracción de segundo, un inicio de sesión que escriba `last_login` espera.
  - El `CHECK` de `reference_quantity` y los índices recorren las tablas, que en producción tienen 1 y 0 filas.
- **`0147`, compatibilidad.**
  - El índice único sólo afecta a `kind='migration'`, que el código anterior nunca escribe.
  - `DROP NOT NULL` no rompe nada: el código anterior siempre escribe el valor.
- **`0147`, vuelta atrás.** Al deshacerla, Django **convierte en silencio** los `theoretical_at_start` nulos en `0`, porque es el `default=0` del campo anterior. Además, el recuento de migración se queda como recuento ordinario `counting`. Ensayado (§4.6): el código anterior lo aprobó y escribió una corrección.
  - **Antes de cualquier vuelta atrás hay que cancelar los recuentos de migración abiertos** (`cancel_migration_counts.py`).
- **`0151`, resto de operaciones.**
  - Índice parcial único `(company, label_code) WHERE label_code IS NOT NULL`.
  - `CHECK label_code <> ''`.
- **`0151`, bloqueos.**
  - `CREATE UNIQUE INDEX` toma `SHARE` sobre `stockunit` (sin `CONCURRENTLY`).
  - `ADD CONSTRAINT CHECK` toma `ACCESS EXCLUSIVE` y valida leyendo la tabla, que en producción tiene 1 fila.
  - `SHARE ROW EXCLUSIVE` en `store_company` por la FK.
  - Ensayado: 0,20 s.

**`lock_timeout=5s`:** si un bloqueo no llega, la migración en curso se revierte entera y `migrate` se puede repetir. Mientras espera, también hace esperar a quien venga detrás en esa tabla, con un máximo de 5 s. **No hay que lanzar `migrate` mientras corre `deploy/backup.sh`**: `pg_dump` tiene `ACCESS SHARE` en todas las tablas durante el volcado.

## 4. Ensayo con datos sintéticos

### 4.1 Código ANTERIOR: base en `0145` y datos

```
migrate --noinput                         # 0001 → 0145, 21 s
seed_demo_users --company-slug black-dog-store --e2e-fixtures
TAG=s1 shell < old_seed.py                # servicios de 6834bc2
TAG=s1 shell < ../release-r1-rehearsal/old_writes.py
TAG=s1 shell < old_smoke.py
```

`old_seed.py` escribe con los servicios de `6834bc2`:
- accesorios con carga inicial y sus códigos: uno de ellos ya es `P000001`, para comprobar que la serie nueva lo salta;
- un iPhone por serie recibido con tres IMEI;
- un código de barras heredado **igual a un IMEI**;
- una venta de caja;
- un recuento ordinario aprobado y otro abierto;
- la reparación de las fixtures E2E, pagada;
- una boleta BETA escrita directamente, sin llamar a SUNAT;
- dos roles editados, uno de bodega y un Administrador sin `reports.view`, y un rol de caja.

`old_writes.py` (el script de R1) crea una segunda empresa con un proveedor, ofertas, la importación semanal y un pedido web pagado por la confirmación real.

`old_smoke.py` comprueba:
- el inicio de sesión con contraseña y cookies, y `me`;
- el catálogo;
- los recuentos: crear, llenar y aprobar;
- una venta por la API con su repetición idempotente;
- la recepción de un equipo;
- las reparaciones y la caja por V1 interno;
- el cierre de sesión.

```
company=2 auth_user=9 companyrole=14 product=12 productbarcode=3 stockunit=4 stockmovement=9 order=3
paymenttransaction=1 repairorder=1 repairpayment=1 inventorycount=3 (approved 2, counting 1) inventorycountitem=3
fiscaldocument=1 supplieroffer=3 bulkimportjob=1 adminauditlog=31 outstandingtoken=3 · última = 0145
roles: adjust=7 company.manage=3 inventory.view=7 · ningún permiso de R2
```

También se guardaron las cookies de dos sesiones iniciadas con `6834bc2`, una de `dev_admin` y otra de `dev_customer`.

### 4.2 Código NUEVO: `migrate` con `lock_timeout`

```
PGOPTIONS='-c lock_timeout=5s -c statement_timeout=300s' python3 manage.py migrate --noinput -v 2
```

| Migración | Tiempo | Mensaje |
|---|---|---|
| 0146 | 0,043 s | |
| 0147 | 0,79 s | |
| 0148 | 0,071 s | aprobar recuentos otorgado a 7 roles |
| 0149 | 0,068 s | centro contable otorgado a 3 roles con `company.manage` |
| 0151 | 0,21 s | |
| 0152 | 0,068 s | imprimir etiquetas a 7 roles; IMEI a 2 administradores sin modificar |
| 0153 | 0,51 s | |
| 0154 | 0,057 s | |
| 0155 | 0,057 s | |
| **Orden completa** | **3,6 s** | |

- **Errores:** ninguno, y `makemigrations --check --dry-run` devuelve «No changes detected».
- **Conteos:** iguales a los de §4.1, fila por fila.
- **Recuentos y equipos:**
  - `kind`: los 3 recuentos son `regular`;
  - ningún `theoretical_at_start` nulo;
  - `label_code`: 0 equipos codificados.
- **Restricciones:** las cuatro nuevas están presentes.
- **Permisos, rol por rol, como están documentados:**
  - `counts.approve` = los 7 roles con `inventory.adjust`, incluidos el de bodega y el Administrador editado;
  - `accounting.*` = los 3 roles con `company.manage`, incluido el Administrador editado;
  - `labels.print` = los 7 roles con `inventory.view`;
  - `labels.sensitive` = sólo los 2 Administradores sin modificar, no el editado;
  - el rol de caja no recibe nada.
- **Esquema:** idéntico al de `blackdog_r2_fresh`, migrada desde cero (`pg_dump -s` sin `\restrict`, comentarios ni `SET`): 0 líneas distintas de 4625.

### 4.3 Ventana mixta: el código ANTERIOR sobre el esquema `0155`

`old_smoke.py` respondió todo como en §4.1:
- `login` 200 y `me` 200;
- recuento: crear 201, líneas 200 y aprobar 200;
- venta 201 y su repetición 200, con un solo pedido;
- recepción de un equipo;
- V1 interno 200;
- `logout` 200 y `me` 401 después.

El script de R1 vuelve a crear empresa, ofertas, importación y pedido pagado (`order 6 paid`). En el resto de `old_seed.py` funcionaron la venta, los recuentos, el cobro y la boleta. Las filas que escribió el código anterior quedan así:
- 4 equipos con `label_code NULL`;
- 3 recuentos `regular`;
- una boleta con `cdr_zip_sha256=''`.

**H2.** Los roles que **crea** el código anterior después de `0148`–`0152` no reciben los permisos nuevos: tras la ventana había `adjust=11` y `counts.approve=7`. Por ejemplo, los presets de una empresa dada de alta en la ventana, o un rol nuevo. Los permisos se completan volviendo a ejecutar las concesiones, que son idempotentes (`regrant_r2_capabilities.py`, §4.7). En producción la ventana dura segundos, pero el paso 7 del §8 lo comprueba igualmente.

### 4.4 H1: el editor de roles de `6834bc2` y los permisos nuevos

`old_roles.py`, con el código anterior y un rol que ya tiene `inventory.counts.approve`:

| Llamada | Resultado |
|---|---|
| listado | 200 |
| detalle | 200 |
| guardar con la lista guardada (lo que envía el panel antiguo, `admin/roles/page.tsx`) | **400** «Capacidad desconocida: inventory.counts.approve» |
| guardar sólo la descripción | **500**: `CompanyRole.save()` llama a `clean()`, que valida la lista |
| `has_capability(..., 'inventory.adjust')` | `True`: la autorización no se ve afectada |

El mismo `ValidationError` aparece en `old_seed.py` al crear un rol copiando las capacidades del Administrador.

Esto afecta a la ventana de migración, de segundos o minutos, y a una vuelta atrás sólo de código. Se corrige con `strip_r2_capabilities.sql` (§7), que quitó los cinco códigos de 7 roles; después, guardar respondió 200. Durante la ventana: **no editar roles ni dar de alta empresas**.

### 4.5 Código NUEVO: lo que sólo escribe R2, y la API del candidato

`new_writes.py` (código nuevo), en dos pasadas:

| Comprobación | Resultado |
|---|---|
| Sesiones de `6834bc2` | `me` 401 con `X-Session-Expired: 1`, la tienda 200 como invitado y `refresh` 401, para el personal y para el cliente: **cierre forzado único** |
| Inicio de sesión con contraseña | 200 y una fila `AuthSession`; `auth/session/` responde `X-Session-Idle-Remaining: 1799` |
| `audit_code_collisions` | Señala el código heredado igual a un IMEI |
| `assign_unit_codes` | En seco: 0 escritos. `--apply`: 8/8 equipos con código |
| «Generar» en un producto sin código | 201 con `P000002`: el `P000001` heredado se salta |
| Recepción nueva | El equipo sale con `label_code` |
| Etiquetas | Vista previa 200. PDF 200: `application/pdf`, empieza por `%PDF`, `X-Labels-Pages` 3, `X-Labels-Review` 1 (un equipo de prueba no listo) |
| Configuración de etiquetas | GET 200 y PATCH 200 |
| Recuento ordinario | Crear 201, líneas 200, **aprobar 200** |
| Recuento de migración | Crear 201, escanear 200, detalle 200, 8 líneas pendientes con `theoretical_at_start` nulo. **Un segundo en la misma sucursal: 400** (índice parcial) |
| Caja | Venta 201 y repetición 200 con la misma clave. `by-key` 200 (encontrada y no encontrada). «No se cobró» 200. Vender con esa clave: **409 `key_reserved`** |
| Reparaciones | V1 interno de órdenes de servicio: 200 |
| Centro contable | `summary`, `operations`, `fiscal-documents` y `pending`: 200 para el Administrador; **403** para `dev_sales` |
| Dos empresas | El personal de la otra empresa recibe **403** en los recuentos de esta, **404** en su contabilidad y **404** en sus etiquetas, y 200 en los suyos |
| App móvil V1 | `login` 200 (`access` y `refresh` en el cuerpo), `customer/<slug>/` 200 con Bearer, `refresh` 200, **0** filas `AuthSession` |
| `logout` | 200, y después `me` 401 |

### 4.6 Vuelta atrás DE ESQUEMA, sobre una copia (`blackdog_r2_rba`)

Se hizo sin cancelar el recuento de migración abierto, a propósito, para ver qué pasa.

```
migrate store 0145_inventory_ai_usage   (código NUEVO, lock_timeout=5s)
```

| Deshacer | 0155 | 0154 | 0153 | 0152 | 0151 | 0149 | 0148 | 0147 | 0146 |
|---|---|---|---|---|---|---|---|---|---|
| Tiempo (s) | 0,059 | 0,054 | 0,487 | 0,002 | 0,209 | 0,002 | 0,002 | 0,844 | 0,201 |

**Sin errores. Qué quedó en la base:**
- los 8 `theoretical_at_start` nulos pasan a **0**;
- el recuento de migración se queda como ordinario `counting`, y el código anterior lo aprobó: una corrección de −2 sobre la línea contada;
- `label_code` y las tablas `authsession`, `possalekeyreservation` y `companylabelsettings` desaparecen;
- los permisos de R2 se quedan, y el editor de roles responde 400/500 hasta `strip_r2_capabilities.sql`, que tocó 7 roles; después, 200.

`old_smoke.py` responde todo bien con el código anterior. Hacia adelante otra vez aplica 9 migraciones y vuelve a otorgar los permisos (11 / 4 / 11 / 3). El esquema vuelve a ser idéntico al de una base desde cero: 0 líneas distintas. Lo que se perdió en esa vuelta atrás:
- **los códigos de equipo**: había 10 y quedaron 0, así que las etiquetas impresas ya no sirven;
- las sesiones abiertas (todo el mundo vuelve a salir);
- las decisiones «no se cobró»;
- la configuración de etiquetas;
- los bytes del CDR;
- los metadatos del recuento.

### 4.7 Vuelta atrás SÓLO DE CÓDIGO (la recomendada), sobre la base principal

1. **Código nuevo:** `cancel_migration_counts.py` cancela el recuento de migración abierto (1 → 0).
2. **Código anterior, con el esquema en 0155 y los datos de R2:**
   - las **cookies emitidas por R2 siguen valiendo** (`me` 200 y `refresh` 200), así que no hay un segundo cierre de sesión;
   - los 9 detalles de recuento responden 200, incluido el de migración ya cancelado;
   - `old_smoke.py`, `old_seed.py` y el script de R1 funcionan.
3. **Código anterior, editor de roles:** responde 400/500 (H1). Tras `strip_r2_capabilities.sql` (7 roles), 200.
4. **Hacia adelante:**
   - `migrate` responde «No migrations to apply»;
   - `regrant_r2_capabilities.py` vuelve a otorgar los permisos (16 / 6 / 16 / 4);
   - `new_writes.py` sale entero como en §4.5, con 14/14 equipos con código después de `assign_unit_codes --apply`;
   - `makemigrations --check` está limpio.

Qué hace falta saber de esta vuelta atrás:
- Con `6834bc2` sirviendo, los códigos `U…` no se resuelven en su caja: se escanea el IMEI o la serie.
- Una reserva «no se cobró» no detiene al código anterior.
- Los equipos que reciba el código anterior quedan sin código hasta el siguiente `assign_unit_codes`.
- Volver a otorgar con `regrant` deshace cualquier retirada manual que el propietario hubiera hecho, por ejemplo, para pedir cuatro ojos en la aprobación.

## 5. Prueba funcional con navegador (candidato: `next build` + `next start` y `runserver`)

Montaje:
- **Backend:** `runserver 127.0.0.1:8562` sobre `blackdog_r2_syn` migrada, con `AUTH_SESSION_IDLE_SECONDS=60` y `AUTH_SESSION_ACTIVITY_GRANULARITY=5`.
- **Frontend:** `next build` (correcto) + `next start -p 3562` con `NEXT_PUBLIC_API_URL=http://127.0.0.1:8562/api`.
- **Comprobado:** `/admin` responde `Cache-Control: no-store`.

La tarjeta de accesos de desarrollo no existe en una compilación de producción (`NODE_ENV=production`). Por eso las pruebas que inician sesión por ella corrieron contra `next dev -p 3002` y el mismo backend, como pide `AGENTS.md`. Las demás corrieron contra `next start`.

| Pasada | Frontend | Backend | Pruebas | Resultado |
|---|---|---|---|---|
| A | `next start` | inactividad 60 s | `storefront`, `accounting-centre`, `label-settings`, `inventory-count` | **97/97** |
| B | `next dev` | inactividad 60 s | `session-idle` (las 6) y `pos-ticket` | **7/7** |
| C | `next dev` | inactividad 60 s | `demo-accounts`, `serial-pick`, `service-pos`, `h411` A–E, G y H | **42 pasan, 1 falla, 3 se omiten** (detalle debajo) |
| D | `next dev` | 30 min (valor por omisión) | `h411-auth-interop` (incluida F), `storefront`, `pos-receipt-options` | **89 pasan, 1 se omite** |

**`session-idle`, lo que comprobó:**
- el aviso con cuenta atrás a los 50 s;
- «Continuar sesión» renueva;
- a los 60 s la pantalla interna se va a `/auth` con el motivo, y «Atrás» no la devuelve;
- dos pestañas comparten la sesión;
- **caja: la venta se registró, la respuesta se perdió y la sesión caducó; al volver, «volver a comprobar» no cobra dos veces**;
- las respuestas tardías de una sesión anterior no cierran la nueva.

**Detalle de la pasada C:**
- **`h411` F falló con 401.** La sesión de su `beforeAll` caducó porque el backend corría con 60 s de inactividad. En la pasada D, con el valor por omisión, pasa.
- **Omisiones:**
  - `pos-ticket` y la caja de `session-idle`: sin `DEFAULT_STOREFRONT_COMPANY_SLUG` el catálogo público venía vacío. Pasan en la pasada B.
  - `pos-receipt-options`: ver debajo.

**Omisión que queda:** `pos-receipt-options` («BETA no preparado»). La causa es `FISCAL_ENABLED=0`, apagado a propósito en este ensayo: ni la boleta ni la factura se habilitan, que es lo correcto sin SUNAT.

**Primera pasada descartada.** En una primera pasada contra `next start`, sin `DEFAULT_STOREFRONT_COMPANY_SLUG`, cinco pruebas de `storefront` fallaron (logotipo y `/services`). Con la variable definida pasan las 81 (pasadas A y D). La causa era el entorno: R2 no toca `Header`, `Footer`, `StorefrontChrome` ni `/services`.

**Prueba de pago por el camino de prueba.** Con el código nuevo y el script de R1, el pedido web quedó `paid` por la confirmación de prueba, sin tarjetas reales.

## 6. Ensayo sobre una restauración LOCAL de la copia de producción

El propietario lo autorizó el 2026-10-25. Se usó la última copia existente, sin hacer un volcado nuevo. Reglas que se cumplieron:
- la copia se restauró sólo en una base local desechable;
- nada de sus datos (filas, nombres, correos, teléfonos, IMEI, documentos) se copió al repositorio, al PR, a los registros ni a este documento: aquí sólo hay conteos, tiempos y hechos del esquema;
- antes de ejecutar cualquier código se apagaron en la copia local las 4 integraciones: SMTP, Google Analytics ×2 e Izipay Mi Cuenta Web. Quedaron `enabled=false` y `paused=true`, y se vaciaron sus secretos sellados, que de todos modos no se podían abrir sin la clave del servidor;
- WhatsApp no tenía configuración por empresa (0 filas), y además corrió `fake`;
- no hubo cron;
- las cuentas de prueba fueron tres cuentas locales nuevas (`r2_local_*@r2.example.invalid`); no se usó ni se tocó ninguna cuenta real;
- **las tres bases que salieron de la copia (`blackdog_r2_prod`, `blackdog_r2_prod0145` y `blackdog_r2_prodrb`) se borraron al terminar.**

### 6.1 Copia

- **Integridad:** sha256 `645fccad…ee1a`, igual al indicado; `gzip -t` correcto.
- **Origen y tamaño:** PostgreSQL 16.15; 84 KB comprimidos.
- **Restauración:** 0,7 s; 120 tablas; `store` en `0145_inventory_ai_usage` (157 filas de migración de `store`).

### 6.2 Forma de los datos reales (sólo conteos)

```
company=1 auth_user=6 companyrole=6 membership=3 product=2 productbarcode=0 stockunit=1 branchstock=2
stockmovement=16 order=7 (5 cancelled, 2 pending_payment) paymenttransaction=6 repairorder=0
inventorycount=1 (draft, 0 líneas) fiscaldocument=0 fiscaldailysummary=0 fiscalvoidcommunication=0
internalsequence=2 (sales_note, repair_order) supplieroffer=0 bulkimportjob=2 adminauditlog=149 outstandingtoken=34
```

- **Pagos:** los 6 son del proveedor en ambiente **TEST**. **Ninguno es anterior a `0117`**: todos se crearon después de que se aplicara, el 2026-10-12.
- **Roles:** 5 presets y 1 rol creado por la empresa (34 capacidades, con `inventory.adjust` e `inventory.view` y sin `company.manage`).
  - El Administrador es **idéntico al preset** anterior (45 capacidades), así que recibe `labels.sensitive`.
- **Códigos de producto:** **0**, así que no hay choques posibles con los prefijos `P`/`U`. `audit_code_collisions` devuelve «0 choques».
- **Recuentos:** 1 abierto, ordinario y vacío; ninguno de migración.

### 6.3 Migración `0145` → `0155` con `lock_timeout=5s`

| Migración | Tiempo | Mensaje |
|---|---|---|
| 0146 | 0,044 s | |
| 0147 | 0,763 s | |
| 0148 | 0,057 s | 4 roles: Administrador, Inventario, Supervisor técnico y el rol de la empresa |
| 0149 | 0,055 s | 1 rol: Administrador |
| 0151 | 0,198 s | |
| 0152 | 0,062 s | imprimir a 5 roles (incluido **Ventas**, que tiene `inventory.view`); IMEI a 1 Administrador sin modificar |
| 0153 | 0,477 s | |
| 0154 | 0,058 s | |
| 0155 | 0,061 s | |
| **Orden completa** | **3,2 s** | sin errores |

- **Conteos:** sin cambios.
- **Esquema:** igual al de una base desde cero salvo 2 líneas cosméticas. Son dos `CHECK`/índices con `ARRAY[...]` que PostgreSQL vuelve a escribir de otra forma tras un volcado y una restauración. Ya existían y no son de R2.

### 6.4 Ventana mixta, candidato, vueltas atrás

- **Ventana mixta, código `6834bc2` sobre `0155`:**
  - `old_smoke.py` responde todo bien;
  - el editor de roles, 400/500 (H1, igual que en sintético).
- **Candidato, `new_writes.py` con las cuentas locales:**
  - inicio de sesión, `auth/session`, caja con clave repetida, `by-key`, «no se cobró» y 409, y recuentos ordinario y de migración: todo como en §4.5;
  - «Generar» da **`P000001`**: es el primer código de producto de la empresa;
  - `assign_unit_codes`: en seco «2 equipos sin código interno (prefijo U)» (el real y uno de la ventana); con `--apply`, 2/2;
  - etiquetas: el equipo real sale **listo**; los productos reales van a **revisión** («no tiene ningún código de barras activo»), lo esperado con 0 códigos;
  - el rol **Ventas** puede imprimir etiquetas (200) y no ve la contabilidad (403);
  - centro contable: las 4 vistas 200, con `excluded_test` y `TEST_PAYMENT` en pendientes;
  - V1: Bearer 200 y 0 filas `AuthSession`.
- **Vuelta atrás sólo de código:**
  - se canceló 1 recuento de migración;
  - `old_smoke.py` responde bien;
  - `strip_r2_capabilities.sql` tocó 5 roles, y el editor respondió 200;
  - hacia adelante, `regrant` repuso 4 / 1 / 5 / 1;
  - `new_writes.py` salió bien. El PDF dio 409 «nada que imprimir» porque la selección sólo tenía equipos de prueba seminuevos sin precio propio, que es una regla de negocio y no un fallo.
- **Vuelta atrás de esquema, sobre otra copia:**
  - `migrate store 0145` tardó 4,9 s, sin errores;
  - `old_smoke.py` respondió bien;
  - hacia adelante aplicó 9 migraciones y el esquema volvió a quedar igual.
- **En el registro**, `EvidenceStorageError: No se pudo leer la imagen`. Es local: los archivos de medios no se restauraron. El PDF se sirvió igual.

### 6.5 Lo que esta copia NO verifica

- Los datos llegan hasta la copia del 2026-10-25 03:15. Si producción cambia mucho antes del despliegue (productos con códigos, roles editados, recuentos de migración), el preflight del §8 los cuenta.
- Los tiempos de bloqueo con tráfico real: la copia no tiene concurrencia. Con estas tablas (≤ 16 filas) el riesgo está en esperar un bloqueo, no en la duración, y eso es lo que acota `lock_timeout`.
- Los archivos de medios y evidencias, que no están en el volcado.

## 7. Procedimiento de vuelta atrás

**Disparadores (§8, paso 10)** que llevan a la vuelta atrás tras el diagnóstico:
- 5xx repetidos en el panel o la caja;
- ventas de caja que fallan;
- sesiones que caducan mientras se usan, con el frontend nuevo ya cargado;
- inicio de sesión imposible.

Que todo el mundo tenga que entrar otra vez es lo esperado y **no** es un disparador. Tampoco lo es que `migrate` se detenga por `lock_timeout`: se repite.

**A. Sólo de código: la recomendada. El esquema se queda en `0155`; ensayada en §4.7 y §6.4.**
1. Con la imagen NUEVA: `docker compose -f docker-compose.prod.yml --env-file deploy/.env.production run --rm backend python manage.py shell < docs/release-r2-rehearsal/cancel_migration_counts.py`. En producción hoy no hay ningún recuento de migración, y ninguna pantalla de R2 los abre.
2. El propietario decide sobre los permisos:
   - ejecutar `strip_r2_capabilities.sql` (`psql` dentro del contenedor de la base) **si** va a editar roles con `6834bc2`. Borra también lo otorgado a mano después del despliegue, así que hay que anotarlo antes;
   - o bien no editar roles hasta volver a subir.
3. `docker tag blackdog-backend:rollback-6834bc2 blackdog-backend:latest`, lo mismo con el frontend, y `up -d backend frontend`; después, healthcheck.
4. No hay un segundo cierre de sesión: los tokens de R2 valen en `6834bc2`. Lo que se queda en la base sin uso:
   - `label_code` y `CompanyLabelSettings`;
   - `AuthSession` y `PosSaleKeyReservation`;
   - los bytes del CDR y los códigos `P…` generados (son `ProductBarcode` normales y escanean bien).
5. Al volver a subir R2, con la imagen nueva:
   - `regrant_r2_capabilities.py`, si se hizo el paso 2;
   - `assign_unit_codes --company <slug>` (primero en seco), para los equipos que se recibieron en medio.

**B. De esquema: sólo si el esquema mismo es el problema; ensayada en §4.6 y §6.4.**
1. El paso A.1, **obligatorio**: deshacer `0147` convierte en 0 los `theoretical_at_start` nulos.
2. Con la imagen NUEVA y `PGOPTIONS=-c lock_timeout=5000 -c statement_timeout=300000`: `migrate store 0145_inventory_ai_usage`. Tardó 4,9 s en la copia.
3. `strip_r2_capabilities.sql`, porque los permisos se quedan (`noop`).
4. Imágenes `rollback-6834bc2` y `up -d`.
5. Lo que se pierde:
   - los códigos de equipo (las etiquetas impresas dejan de valer);
   - las sesiones (otro cierre forzado);
   - las decisiones «no se cobró»;
   - la configuración de etiquetas;
   - los bytes del CDR;
   - los metadatos del recuento.

**Nunca** restaurar la copia encima de transacciones posteriores. **Nunca** arrancar la imagen anterior con un recuento de migración abierto.

## 8. Procedimiento de lanzamiento (cuando el propietario dé el GO al SHA exacto)

A diferencia de R1, **todas** las migraciones de R2 toleran que el código anterior siga sirviendo encima (§4.3), así que basta **un único `migrate`** antes de cambiar las imágenes.

1. **Aviso** (el propietario, antes): «Durante unos minutos se actualiza el sistema. Al terminar **todas las sesiones web se cierran una vez**: personal y clientes vuelven a iniciar sesión. Si tenías el panel o la tienda abiertos, **recarga la página**. La app móvil no se ve afectada. No edites roles ni des de alta empresas mientras dure la actualización». Mejor en un momento sin caja abierta, y sin ventas en vuelo: si las hay, la caja lo resuelve con «volver a comprobar».
2. **Preflight, sólo lectura:**
   - el SHA servido es `6834bc2`, los contenedores están sanos y hay disco suficiente;
   - `ops_status` y `python deploy/preflight.py`: la línea nueva de sesión tiene que decir «30 min sin actividad, 8 h personal, 7 días clientes» y ningún `AUTH_SESSION_*` debe estar definido;
   - `FISCAL_ENABLED=0`; `INVENTORY_AI_ENABLED` sin definir o falso;
   - ninguna fila de integración `sunat` habilitada en la consola de integraciones (la fila manda sobre la variable de entorno; el ambiente sigue fijado en BETA);
   - pagos `pending` y su antigüedad;
   - recuentos abiertos por tipo, roles por permiso, `ProductBarcode` y `StockUnit` (comparar con §6.2);
   - md5 de `deploy/Caddyfile` local y de `crontab` y env.
3. **Copia:** `sh deploy/backup.sh` y `gzip -t`. Después, `docker tag` de las imágenes en uso como `rollback-6834bc2`.
4. `git fetch` y `git checkout 42eccc807b07170f81c3ab55a2fcf5c79851b823`; comprobar que el `Caddyfile` local sigue igual (R2 no lo toca). Construir las imágenes con `nohup` mientras la tienda sirve.
5. **Opcional y recomendado:** el ensayo rápido de R1 con la imagen nueva sobre una restauración desechable de la copia del paso 3 (red interna, entorno sacado de `docker compose config --format json`, filtrando `\restrict`): ida, vuelta A y vuelta B, ida, esquema y conteos.
6. **Migrar con `6834bc2` sirviendo**, sin que corra `backup.sh`. Debe aplicar `0146`–`0155` en unos segundos.

   ```
   docker compose -f docker-compose.prod.yml --env-file deploy/.env.production run --rm \
     -e "PGOPTIONS=-c lock_timeout=5000 -c statement_timeout=300000" backend python manage.py migrate --noinput
   ```

   Si falla por `lock_timeout`, se repite.
7. Comprobar los permisos: `count(roles con inventory.adjust sin inventory.counts.approve) = 0` y lo mismo para `company.manage` → `accounting.view` e `inventory.view` → `inventory.labels.print`. Si no da 0 (H2), ejecutar `regrant_r2_capabilities.py` con la imagen nueva.
8. `up -d backend frontend` y healthchecks. Después:
   - `manage.py check` y `makemigrations --check`;
   - `audit_code_collisions` (sólo lectura);
   - `assign_unit_codes --company black-dog-store` en seco. `--apply` sólo con el OK del propietario: hoy afecta a 1 equipo.
9. **Prueba de humo**, por el propietario o con una cuenta suya. En producción el agente no crea cuentas ni escribe contraseñas.
   - Iniciar sesión con contraseña; las cookies anteriores están muertas.
   - `/auth/session` debe decir 30 min de inactividad.
   - `/admin` sirve `Cache-Control: no-store`.
   - Tienda, `/product` y carrito; el checkout en modo prueba pregunta por la sesión.
   - Caja: buscar y vender un accesorio, con la repetición por clave (o, si no se quiere vender, sólo la vista previa).
   - `/admin/inventory/counts`, `/admin/inventory/labels` (vista previa y PDF de un equipo), `/admin/inventory/labels/settings` y `/admin/accounting`, que mostrará `excluded_test`.
   - Una reparación (lista) y, con el OK del propietario, un cobro de reparación y su repetición con la misma clave (debe devolver el mismo pago).
   - Con un usuario de Ventas: no ve la contabilidad.
   - La app móvil V1 inicia sesión.
   - Registros sin errores; `Caddyfile`, `crontab` y env sin cambios.
10. **Disparadores de la vuelta atrás:** los del §7.
11. Registrar el despliegue en `02_ESTADO_ACTUAL.md`, `07_CHANGELOG.md` (AUTH-B/C ya no están en borrador) y `docs/NEXT_ACTION.md`.

## 9. Riesgos y recomendación

| Riesgo | Probabilidad / impacto | Mitigación |
|---|---|---|
| Todas las sesiones web caen una vez | Seguro / bajo | Aviso del paso 1. Esperado (P4) |
| Pestañas abiertas con el JS de R1 no marcan actividad y caducan a los 30 min | Probable el primer día / bajo | «Recarga la página» en el aviso. Desaparece al recargar |
| H1: editor de roles de `6834bc2` con permisos de R2 (400/500) | Sólo en la ventana o tras una vuelta atrás / bajo | No editar roles durante la ventana. `strip_r2_capabilities.sql` si se vuelve atrás |
| H2: roles creados en la ventana sin los permisos nuevos | Muy baja / bajo | Comprobación del paso 7 y `regrant_r2_capabilities.py` |
| Deshacer `0147` convierte en 0 lo pendiente | Sólo en la vuelta atrás B / medio | Cancelar los recuentos de migración primero (A.1). Hoy no hay ninguno |
| La vuelta atrás B borra los códigos de equipo | Sólo en B / medio si ya se imprimieron etiquetas | Preferir A |
| El primer producto sin código fija la serie `P` | Seguro al primer alta / medio | **Decisión P3 del propietario antes del despliegue**, o aceptar `P000001` |
| El centro contable muestra ingresos web casi nulos (TEST excluido, P4) | Seguro / bajo (sorpresa) | El aviso de la pantalla lo dice. Explicárselo al propietario |
| Ventas puede imprimir etiquetas (se le dio `labels.print` porque tiene `inventory.view`) | Seguro / bajo | Decisión del propietario: se mantiene lo que concede `0152` |
| El personal tiene tope de 8 h de sesión sin deslizar: un turno de caja más largo obliga a iniciar sesión otra vez | Seguro en turnos largos / bajo | Decirlo en el aviso (paso 1). La caja resuelve una venta en vuelo con «volver a comprobar» |
| La marca de venta en vuelo vive en `sessionStorage`: si se cierra la pestaña tras una respuesta perdida y una sesión caducada, el carrito reconstruido usa otra clave | Muy baja / medio (venta duplicada) | Solo con respuesta perdida + caducidad + pestaña cerrada. Aviso: no cerrar la caja con una venta sin confirmar |
| La pantalla `/auth?reason=idle` dice «inactividad» el día del despliegue | Seguro / cosmético | Avisar antes |
| Un cobro de reparación escrito por el código nuevo no se repite con el código anterior (409 «pago diferente») | Sólo tras una vuelta atrás / bajo | Es seguro: no duplica el cobro. Recargar muestra el pago ya registrado |
| `AuthSession` y `PosSaleKeyReservation` crecen sin purga | Lento / bajo | Deuda: una orden de purga más adelante |
| ADR de sesiones (#180) sin fusionar, citado por el código | — / documental | Fusionar #180, o aceptar que las decisiones P1–P5 quedan en el PR |

**GO CON CONDICIONES** (decisiones del propietario, 2026-10-26):
1. GO del propietario al SHA exacto `42eccc807b07170f81c3ab55a2fcf5c79851b823` (frase: `GO DESPLIEGUE R2 — SHA 42eccc807b07170f81c3ab55a2fcf5c79851b823`), sabiendo que se cierran todas las sesiones web una sola vez, con aviso y en la ventana acordada.
2. **Decididas:** serie `P` para los códigos nuevos (los históricos no se tocan); `inventory.labels.print` se queda como lo concede `0152` (sin ampliar permisos y sin IMEI por defecto: el perfil por defecto de etiquetas es el identificador interno); `accounting.export` se queda para los roles con `company.manage`; `assign_unit_codes` solo en seco (`--apply` solo con una autorización posterior).
3. Seguir el §8, con la migración bajo `lock_timeout`, sin `backup.sh` en paralelo y sin editar roles en la ventana.
4. No entran en R2: FIN-B1, FIN-E ni sus migraciones `0156`–`0158`, ni FIN-F, FIN-B2 o FIN-D.

**Ventana operativa propuesta** (a confirmar por el propietario): un día laborable a primera hora de Lima (07:00–08:00), antes de abrir y con la caja cerrada, lejos del respaldo diario de las 03:15. Duración estimada 20–30 min (construcción de imágenes con la tienda sirviendo, copia y `gzip -t`, `migrate` de unos segundos, `up -d`, prueba de humo). Aviso previo al personal el día anterior: se cierra la sesión una vez, hay que recargar el navegador, tope de 8 h de sesión, 30 min de inactividad.

Si alguna no se cumple: NO-GO hasta que se cumpla. No hace falta otro ensayo sobre datos de producción salvo que el preflight del §8 muestre cambios grandes respecto de §6.2.

## 10. Reensayo sobre el candidato final (`42eccc8`), 2026-10-26

Motivo: el primer ensayo fue sobre `c3ee89e`. El candidato final añade #189 (pruebas de la clave de caja) y #186 (cobro de reparaciones). No cambia ninguna migración: `git diff c3ee89e 42eccc8 -- backend/store/migrations` está vacío. Se repitió todo lo que depende de código y datos, en local, sobre datos sintéticos (`postgres:16`, 16.15, sobre `tmpfs`, puerto propio) y sin red de salida. No se volvió a copiar nada de producción.

| Comprobación | Resultado |
|---|---|
| Revisión independiente del diff completo `6834bc2..42eccc8` | GO CON CONDICIONES; sin P0 ni P1; las condiciones están en el §8 y el §9 |
| `migrate` `0145` → `0155` con `lock_timeout=5s` | Sin errores; `0147` 0,87 s, las demás por debajo de 0,53 s; `makemigrations --check`: «No changes detected» |
| Conteos antes y después | Iguales fila por fila (`counts.sh`) |
| Permisos tras migrar | `counts.approve` 7 = `inventory.adjust` 7; `accounting.*` 3 = `company.manage` 3; `labels.print` 7 = `inventory.view` 7; `labels.sensitive` 2 |
| Esquema frente a una base migrada desde cero | 0 líneas distintas (9234) |
| Ventana mixta: código `6834bc2` sobre el esquema `0155` (`old_smoke.py`, R1 `old_writes.py`) | Todo 200/201 como antes; cierre de sesión correcto |
| H1 (editor de roles de `6834bc2`) | Reproducido: 400 al guardar con la lista, 500 al guardar sin ella; `strip_r2_capabilities.sql` lo arregla |
| Código nuevo (`new_writes.py`) | Idéntico al §4.5: sesión de 30 min, `assign_unit_codes` en seco 0 y `--apply` 5/5, `P000002`, PDF de etiquetas, recuento ordinario y de migración, caja con `key_reserved` 409, contabilidad 403 para Ventas, otra empresa 403/404, app móvil V1 con 0 filas de sesión |
| Cobros de reparación (`repair_payments.py`, #186, API V1 interna) | Reversión 200 y repetida 200 (saldo 0 → 120). Dos cobros idénticos de 50,00 con claves distintas: **201 y 201, dos pagos, 100,00**. Misma clave: devuelve el mismo pago, sin segundo registro. Misma clave con otro importe: **409 `idempotency_conflict`**. «50» y «50.00»: el mismo intento. Sobrepago: 400. Saldo final 20,00 = 120 − 100 |
| Vuelta atrás DE ESQUEMA en una copia | Las nueve deshacen sin error; `0147` 0,8 s; el código anterior responde (`old_smoke.py`); hacia adelante aplica 9 y el esquema vuelve a ser idéntico al de cero |
| Vuelta atrás SÓLO DE CÓDIGO (la recomendada) | `cancel_migration_counts.py` 1 → 0; `old_smoke.py` correcto; el editor de roles falla hasta `strip_r2_capabilities.sql` y luego guarda (200); hacia adelante «No migrations to apply», `regrant_r2_capabilities.py` y `makemigrations --check` limpio |
| Navegador (`next dev` :3002, `runserver`, esquema `0155`): `service-pos` (incluye «el técnico registra un cobro y no puede reversarlo»), `service-tracking`, `h411-auth-interop`, `pos-ticket`, `serial-pick` | **20 pasan** |

No se repitió el §5 (sesiones, `session-idle`, `storefront`, contabilidad, recuentos, etiquetas): el código de esos módulos no cambió entre `c3ee89e` y `42eccc8` (el diff son diez archivos: servicio de reparaciones y su consola, pruebas y documentos).

**Lo que sigue sin verificarse** (igual que el §6.5): el contenido real de producción tras la última copia, y la compilación de producción con `NODE_ENV=production` (la cubre el CI de master).
