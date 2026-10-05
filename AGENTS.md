# AGENTS.md — Black Dog Store

Contexto por defecto de cualquier agente: **este archivo + [docs/NEXT_ACTION.md](docs/NEXT_ACTION.md)**.
Lo demás se lee sólo cuando la fase lo pide.

TOKEN POLICY: sigue [docs/AGENT_TOKEN_POLICY.md](docs/AGENT_TOKEN_POLICY.md). No dupliques contexto del
proyecto en prompts. Prompt estándar: [docs/AGENT_BOOTSTRAP.md](docs/AGENT_BOOTSTRAP.md).

## 1. Fuentes de verdad

Orden de autoridad: código de `master` > migraciones > tests > `02_ESTADO_ACTUAL.md` >
`05_DECISIONES_TECNICAS.md` > resto de docs > historial y chats.

| Para | Leer |
|---|---|
| Qué toca ahora | `docs/NEXT_ACTION.md` |
| Dónde vive cada cosa | `docs/CODEBASE_MAP.md` |
| Invariantes, hallazgos, deuda | `docs/AUDIT_MEMORY.md` (buscar por ID con `rg`) |
| Estado por fase | `02_ESTADO_ACTUAL.md` (sólo la entrada relevante) |
| Decisiones | `05_DECISIONES_TECNICAS.md`, ADR en `docs/adr-*.md` |
| Historial | `07_CHANGELOG.md` |
| Multiempresa | `docs/saas-multiempresa.md` |
| Producción | `docs/despliegue-produccion.md` (cuando esté en `master`) |
| Next.js de este repo | `frontend/AGENTS.md` |

`00_`, `01_`, `03_`, `04_` y `06_` **no existen** en el repositorio. No los recrees.
Nada está implementado porque un documento lo diga: se verifica en código, migraciones y tests.

## 2. SaaS / multiempresa

Black Dog Store es el tenant piloto, no el producto. Nunca en código global: nombre, Apple/iPhone,
dirección, teléfono, RUC, categorías, productos, garantías, políticas, imágenes, logos, textos
comerciales, sucursales, métodos de pago. Todo eso sale de configuración del tenant, CMS, uploads o
una migración de datos del piloto. Prueba mínima: otra tienda sin datos no ve nada del piloto.

## 3. Seguridad

- La empresa y la sucursal se resuelven en el servidor. Un id que manda el frontend selecciona, no autoriza.
- Autoridad = capacidades (`tenancy.has_capability`), nunca `user.role` ni lo que oculte la interfaz.
- Recurso de otra empresa responde igual que uno inexistente (404).
- Sesión web: JWT en cookies HttpOnly + CSRF. Nunca tokens en `localStorage`.
- Toda escritura sensible deja `AdminAuditLog` **con `company`**.
- Repositorio PÚBLICO: antes de cada push, sin `.env`, claves, volcados, SQLite, backups, evidencias ni PII.
- No se debilita una protección para que pase un test. No se silencia un aviso para dejar CI verde.

## 4. Autonomía

Autorizado sin preguntar entre fases: ramas, código, migraciones incrementales, tests, CI, commits, push,
PR, resolver conflictos, mergear a `master` con las compuertas verdes, cerrar PR obsoletos con explicación.

Nunca, sin orden expresa del propietario: despliegue público, VPS, dominio, DNS, certificados públicos,
correos reales, credenciales o cobros de Izipay productivos. No inventar credenciales, licencias ni
decisiones comerciales o legales: se registran como `BLOCKED` y se sigue con lo demás.

Clasificar siempre: `IMPLEMENTADO` · `PARCIAL` · `PENDIENTE` · `PROPUESTA` · `OBSOLETO` · `BLOCKED`.
Un `PENDIENTE` definido, seguro y probable se implementa. Una `PROPUESTA` no se implementa para vaciar la lista.

## 5. Git / PR

- Nunca sobre `master`. Ramas pequeñas (`fix/`, `feat/`, `security/`, `ci/`, `docs/`), una responsabilidad por PR.
- Antes de asumir un SHA: `git fetch --all --prune` y `gh pr list`. Otra sesión puede haber movido `master`.
- Merge normal: sin squash, sin rebase, sin force push. `gh pr merge N --merge --match-head-commit <sha>`.
- Antes de mergear: 0 commits detrás de `master`, CI verde sobre ese HEAD exacto, diff revisado, escaneo de secretos.
- Cada PR añade su entrada arriba de `02_` y `07_`: dos PR abiertos chocan ahí. Mergear de uno en uno y
  traer `master` al siguiente. Los PR con `backend/**` tardan ~70 min de CI: agruparlos o apilarlos; los de
  sólo frontend (1 min) van al final.
- El árbol principal `/Users/cmaucorp/Desktop/BlackDogStore-web` tiene trabajo del propietario sin
  commitear: **sólo lectura**. Trabajar en worktrees fuera de `/tmp`
  (`git worktree add ~/Library/Caches/blackdog-worktrees/<nombre>`): un reinicio vacía `/tmp` y se lleva lo
  que no estaba confirmado. Confirmar pronto; lo confirmado vive en `.git` y sobrevive.
- Commits en inglés, docs y PR en español. Código separado de documentación.

## 6. Tests

RED antes que GREEN: el test falla por el motivo correcto antes de escribir el código.

| Compuerta | Comando | Notas |
|---|---|---|
| Backend focal | `DATABASE_URL=postgres://postgres:postgres@localhost:5432/blackdog python3 manage.py test <módulo> --noinput` | Sólo PostgreSQL. **Un proceso a la vez**: dos comparten `test_blackdog` y se destruyen. |
| Backend completo | el CI del PR (`Backend PostgreSQL Validation`) | ~70 min. Sólo corre si el PR toca `backend/**`. |
| Frontend | `npm test -- --runInBand` · `npx tsc --noEmit` · `npm run lint` · `npm run build` | Parar `next dev` antes del build: comparten `.next`. |
| E2E | `E2E_BACKEND_DIR=<backend> E2E_BASE_URL=http://localhost:3002 E2E_API_BASE=http://127.0.0.1:8100/api npx playwright test` | Ver abajo. |

Playwright: `next dev` se arranca con `NEXT_PUBLIC_API_URL=http://127.0.0.1:8100/api`; sin esa variable su
proxy apunta a `:8000`, que es el servidor del propietario. La URL base es `localhost`, no `127.0.0.1`:
Next dev no hidrata desde otro origen. Backend en `:8100` y `next dev` en `:3002` (el único origen que el backend acepta), sobre una
**copia** de `backend/db.sqlite3` del árbol de desarrollo, migrada y sembrada con
`seed_demo_users --company-slug black-dog-store --e2e-fixtures --fiscal-beta`. Una base creada desde cero
no sirve: le falta el pedido pagado con factura y `fiscal-invoice` omite sus 9 pruebas. En la copia, y sólo
en ella, se vacían las fotos de producto que apuntan a un host externo y se archivan las campañas publicadas
(`next/image` rechaza un host que no está en `NEXT_PUBLIC_IMAGE_HOSTS` y la portada cae). Una prueba omitida
no cuenta como verde: la pasada completa son 179 y 0 omitidas.
Una pasada sólo vale si el equipo no se suspendió (`pmset -g log`), si no hubo login manual justo antes
(límite 5/min) y si queda stock (cada pasada vende; reponer por `inventory-adjust`). Un fallo por límite de
peticiones o stock se arregla aislando el test, no ignorando la pasada.

Path impact: sólo docs → revisar el diff, sin suites. Código → sus compuertas.

## 7. Frontend

- Next.js 16: leer `frontend/AGENTS.md` antes de usar una API de Next.
- Llamadas con sesión por `fetchWithAuth` (sólo bajo `API_BASE`). Con `FormData`, sin `Content-Type`.
- La interfaz no ofrece lo que el servidor va a negar, pero la autoridad sigue en el servidor.
- Responsive: 320, 360, 375, 390, 414, 768 y escritorio; sin desborde global; tablas anchas en su propio contenedor.
- Accesibilidad: teclado, foco visible, `aria-expanded`/`aria-controls`, movimiento reducido, contraste por tokens.
- Texto recortado se mide con rectángulos de texto, no con `scrollWidth`.

## 8. Backend

- Antes de crear un modelo: buscar si existe. Migraciones incrementales; nunca editar una aplicada.
- Tras cualquier migración: `makemigrations --check --dry-run`.
- Los límites de peticiones viven en la memoria del proceso: ver §10.
- Subidas: el tope se aplica antes de leer; el tipo lo decide el decodificador, no el nombre.

## 9. Imágenes de la tienda

- Las sube la tienda desde el panel (`POST /api/admin/storefront/images/`); nunca van compiladas ni versionadas.
- Son **públicas**; las evidencias de servicio son **privadas**. Comparten almacenamiento, nunca autorización.
  No servir el volumen como archivos estáticos.
- PNG/WebP conservan la transparencia; se muestran con `object-contain`, sin caja ni fondo propio.
- Toda imagen de `/api/storefront/images/<id>` lleva una sombra `drop-shadow` suave que sigue la silueta
  (`frontend/app/lib/storefront-media.ts`): hero, categorías, campañas, servicio, ubicación y vista previa
  del panel. No se aplica a URL externas.
- Sin licencia demostrada no entra ninguna imagen al repositorio (`BLOCKED: LICENSING`).

## 10. Producción

Topología: internet → Caddy → `/api/*` a Django (gunicorn), lo demás a Next → PostgreSQL privado. Sólo
Caddy publica puertos. Un proxy: `TRUSTED_PROXY_COUNT=1`.

**Un proceso de gunicorn.** No subir `--workers` sin migrar antes los límites a una caché compartida
(THROTTLE-CACHE-01 pasaría a bloqueante). Migraciones: paso explícito, nunca al arrancar. Sin cuentas de
demostración; primer administrador con `createsuperuser`. No decir «READY FOR PRODUCTION» sin dominio,
servidor y credenciales reales: lo correcto es «READY FOR EXTERNAL PRODUCTION CONFIGURATION».

## 11. Documentación

Tras cada fase: `02_ESTADO_ACTUAL.md`, `07_CHANGELOG.md` y **`docs/NEXT_ACTION.md` (se sobrescribe, no es
un changelog)**. Cuando aplique: `05_`, `docs/AUDIT_MEMORY.md`, `.env.example`, guía de despliegue.
No escribir `IMPLEMENTADO` de algo que sólo está en una rama. No crear informes paralelos
(`status.md`, `summary.md`, `checkpoint.md`): la información vive en los archivos de arriba.

## 12. Cuándo se termina

P0 = 0, P1 y P2 implementables = 0, CI de frontend y backend verde, Playwright verde, ensayo Docker y
copia/restauración verdes, escaneo de secretos limpio, aislamiento entre empresas probado, docs al día.
Lo que quede sólo puede ser: credenciales, dominio, DNS, servidor, licencias o decisiones del propietario.
