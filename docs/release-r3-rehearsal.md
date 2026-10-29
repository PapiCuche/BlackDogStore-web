# Candidato R3 y ensayo de lanzamiento (REL-R3)

**Alcance:** inventario y e-commerce primero (INVENTORY-FIRST-01) más la base de finanzas que ya está en `master`. Producción hoy: R2 `42eccc807b07170f81c3ab55a2fcf5c79851b823`, `store` hasta `0155_pos_sale_key_reservation`. Candidato: `a1a94a7d901f3dd9fc9ad191707d2246033fc90f` (master tras fusionar #199). Este es el SHA del **código** candidato; los scripts de recuperación viven en este documento (PR de solo documentación posterior) y se pasan al servidor por la entrada estándar de `ssh`, como en R2.

Ensayo **local**, 2026-10-28, sobre `bcb6029`. El candidato añade a ese árbol sólo las pruebas de navegador y un arreglo de CSS (#198) y las correcciones de validación de entradas de #199 (backend, sin migraciones; revisadas por separado con 312 pruebas y fuzz). Las migraciones `0156`–`0159` son idénticas. No se desplegó nada ni se tocó producción más allá de una consulta de solo lectura con agregados (§6).

## 1. Qué trae R3 respecto de R2

| PR | Contenido | Migración |
|---|---|---|
| #186 | ya estaba en R2 | — |
| #188 FIN-B1 | pagos fijos, obligaciones, pagos y ajustes (solo API; sin pantalla) | `0156_finance_core`, `0157_finance_capabilities` |
| #190 + #192 FIN-E | cuentas por cobrar de reparación derivadas y manuales; la aprobación de la cotización y la cuenta por cobrar son una sola transacción | `0158_finance_receivable_origin` |
| #194 LABEL-A | lote de etiquetas listo al ingresar stock (equipos: una por unidad; accesorios: exactamente lo recibido) | `0159_label_batches` |
| #195 INV-UX-A1 | buscador de producto (nombre, SKU, escáner), «Recibir mercadería», barra «Siguiente paso» | — |
| #196 INV-FAST-B | catálogo asistido (con aprobación) y registro directo de equipos por el importador oficial; **la caja rechaza precio <= 0** | — |
| #197 ECOM-READY-D1 | «Completar ficha», vista previa del cliente, «Publicar listos» (elección explícita, nada marcado) | — |
| #198 INV-QA-E | pruebas de navegador del recorrido completo y arreglo del desborde móvil de Publicación | — |
| #199 | correcciones de la revisión transversal de R3: precio y ids malformados del catálogo asistido (500 → 400), resumen de etiquetas sin 500 tras el commit, prefijo de la serie de finanzas inmutable | — |

Siguen **fuera**: FIN-F, FIN-B2, FIN-D, SUNAT y correlativos fiscales, Google/AUTH-D.

## 2. Migraciones `0156` → `0159` (todas de `store`)

Medido sobre datos con estado R2 (`blackdog_r3_syn`), `lock_timeout=5s`: `0156` 2,26 s, `0157` 0,07 s (concede `finance.view/manage/payments.record` a los roles con `company.manage`; ninguno recibe los 9 permisos explícitos), `0158` 0,27 s, `0159` 0,61 s; **total 4,8 s**. Sólo `CREATE TABLE` y `ALTER` sobre tablas nuevas; las únicas esperas sobre tablas existentes son bloqueos `SHARE ROW EXCLUSIVE` breves por las llaves foráneas (usuarios, empresa, sucursal, cliente, proveedor, producto, movimientos, equipos). Sin reescritura de tablas.

## 3. Resultados del ensayo

| Comprobación | Resultado |
|---|---|
| `migrate` con código R3 sobre datos de R2 | OK; `makemigrations --check` sin cambios |
| Conteos de las 117 tablas existentes | idénticos; 9 tablas nuevas a 0 |
| Roles | sólo los roles con `company.manage` ganan los 3 permisos básicos; nadie pierde nada |
| Esquema frente a una base desde cero | 0 líneas distintas |
| Ventana mixta (código R2 sobre el esquema nuevo) | `old_smoke` completo OK; R2 ignora las tablas nuevas; aprobar una cotización con R2 no crea cuenta por cobrar (se repone con el relleno); R2 vende un producto a precio 0 |
| Editor de roles de R2 con permisos de finanzas | **FALLA** (H1-R3, §4) |
| Cobros de reparación (`repair_payments.py`) | igual que en R2 |
| `backfill_repair_receivables` | `--dry-run` no escribe; real crea 3; repetido crea 0 |
| Aprobar una cotización con R3 | la cuenta por cobrar nace en la misma transacción |
| Caja con producto a 0 | 400 «Este producto no tiene precio: configúralo antes de venderlo», también con la misma clave; producto normal 201 y réplica 200 |
| Etiquetas | entrada de compra +7 → lote de 7; entrada manual → ninguno; «Registrar equipos» ×2 → lote de 2; alta de accesorio ×6 → lote de 6 y réplica sin segundo lote |
| Finanzas (resumen, obligaciones, categorías, reglas) | 200 para administrador y gerencia; 403 para inventario, ventas y sin permisos; otra empresa: lista vacía, detalle 404, pago 404 |
| Navegador (`inventory-first`, #198) sobre la base actualizada | 8 de 8 |
| Vuelta atrás de código (R2 sobre el esquema `0159`) | OK, con el `strip` de §5 |
| Vuelta atrás de esquema (`migrate store 0155`) | OK en 3,2 s; esquema idéntico al de `0155`; migrar de nuevo OK |

## 4. Hallazgos

- **H1-R3 (alta mientras R2 sirva más que minutos):** con permisos `finance.*` en un rol, el editor de roles de R2 responde 400 «Capacidad desconocida: finance.manage» al guardar con la lista almacenada y 500 al guardar sólo la descripción. La autorización sigue bien. Mitigación: no editar roles en la ventana y, si se vuelve atrás, `strip_r3_capabilities.sql` (§5).
- **H5 (media):** la caja ya no vende productos a precio <= 0 (400 no reintentable). Una venta móvil en cola con un producto así quedaría atascada. **En producción hoy no hay productos activos con precio 0** (§6).
- **H2 (resuelto por #198):** sin ese arreglo, Publicación desborda a 390 px (556 px).
- **H6 (info):** R3 es más ancho que finanzas y etiquetas (catálogo asistido, vista previa, registro directo). No se ejecutó la suite completa de backend en el ensayo: la cubre la CI de master.

## 5. Vuelta atrás

**A. Sólo de código (la recomendada).** El esquema se queda en `0159` y R2 lo tolera.
1. Con la imagen R3: `cancel_migration_counts.py` (de `docs/release-r2-rehearsal/`); hoy no hay recuentos de migración.
2. Si se van a editar roles con R2: `docs/release-r3-rehearsal/strip_r3_capabilities.sql` (quita todo `finance.*` de los roles; una transacción; idempotente). Anotar antes lo otorgado a mano.
3. `docker tag blackdog-backend:rollback-42eccc8 blackdog-backend:latest` (y frontend) y `up -d`; healthchecks.
4. Al volver a subir R3: `regrant_r3_capabilities.py` (si se hizo el paso 2; **solo añade** los 3 permisos básicos a los roles con `company.manage` y nunca quita ninguno, a diferencia de la función `grant` de `0157`, que además retira los nueve explícitos), `backfill_repair_receivables --dry-run` y luego real.

**B. De esquema:** sólo si el esquema mismo es el problema. `migrate store 0155_pos_sale_key_reservation` con la imagen R3 y `lock_timeout`; pierde finanzas y los lotes de etiquetas, y los permisos `finance.*` se quedan (0157 es no-op al revertir), así que exige también el `strip`.

Nunca restaurar una copia sobre transacciones posteriores.

## 6. Comprobaciones previas en producción (solo lectura, agregados, 2026-10-28)

Producción sirve `42eccc8`. Productos activos con precio <= 0: **0**. Líneas de caja a precio 0: **0**. Roles con `company.manage`: **1** (administrador) y ninguno con `finance.*` aún. Recuentos: 1 borrador ordinario. Reservas de clave POS: 0; ventas de caja en 24 h: 0. Reparaciones con presupuesto aprobado: 0 (el relleno no tiene trabajo). El script es `predeploy_checks.sql`; se repite en el preflight de la ventana.

## 7. Procedimiento de lanzamiento (cuando el propietario dé el GO al SHA exacto)

Igual que R2 (`docs/release-r2-rehearsal.md` §8), con estas diferencias:
1. Aviso: no se cierran todas las sesiones esta vez; **no se editan roles ni se crean empresas durante la ventana**.
2. Preflight de solo lectura: servido `42eccc8`, contenedores sanos, `FISCAL_ENABLED=0`, ninguna integración `sunat`, `predeploy_checks.sql` sin sorpresas (productos a 0 = 0).
3. Respaldo: `sh deploy/backup.sh`, `gzip -t` y restauración de prueba en una base desechable; `docker tag` de las imágenes en uso como `rollback-42eccc8`.
4. `git checkout a1a94a7d901f3dd9fc9ad191707d2246033fc90f` en el servidor (el `Caddyfile` modificado a mano se conserva) y construir las imágenes mientras R2 sirve.
5. Un solo `migrate` con `PGOPTIONS='-c lock_timeout=5000 -c statement_timeout=300000'`, sin `backup.sh` en paralelo. Debe aplicar sólo `0156`–`0159`.
6. Comprobar roles: los que tengan `company.manage` deben tener `finance.view/manage/payments.record`.
7. `up -d backend frontend`, healthchecks, y `backfill_repair_receivables --dry-run` (producción: 0 reparaciones aprobadas).
8. Humo sin ventas ni cobros reales: inicio de sesión, tienda, `/admin`, buscador de producto, vista previa de publicación, finanzas con 403 para ventas. El resto, como en R2.

## 8. Riesgos y condiciones

| Riesgo | Mitigación |
|---|---|
| H1-R3: editor de roles de R2 con permisos de finanzas | no editar roles en la ventana; `strip` si se vuelve atrás |
| Se activan endpoints de finanzas sin pantalla | sólo API, con permisos; el administrador los ve en Roles |
| La caja rechaza precio <= 0 | producción no tiene productos así; el aviso del importador marca «sin precio» |
| Las variantes nuevas nacen ocultas a 0.00 | no se publican hasta ponerles precio |
| CI de master del SHA final | debe estar verde antes del GO |

**GO CON CONDICIONES:** GO del propietario al SHA exacto `a1a94a7d901f3dd9fc9ad191707d2246033fc90f`; CI verde de ese SHA; decisiones abiertas anotadas en NEXT_ACTION (serie obligatoria, límite de la descripción, últimos 4 dígitos del IMEI). Sin GO no se toca el servidor.

## 9. Qué se verificó y qué no (para la solicitud de GO)

| Verificación | Estado |
|---|---|
| Migraciones `0156`–`0159`, tiempos, esquema, conteos | EJECUTADO en local con datos sintéticos de estado R2 (§2, §3). **No** sobre una restauración de la copia real de producción |
| Ventana mixta y las dos vueltas atrás | EJECUTADO en local |
| Scripts de recuperación | `strip_r3_capabilities.sql` EJECUTADO y validado dos veces (3 roles → 0; idempotente). `regrant_r3_capabilities.py` **reescrito** el 2026-10-29 para que solo añada (el original reutilizaba `grant` de `0157` y habría retirado permisos explícitos asignados a propósito) y EJECUTADO en una base desechable (añade 1 rol, repetir añade 0, conserva `finance.void`). `predeploy_checks.sql` EJECUTADO en local y, de solo lectura, en producción (§6) |
| Caja con precio 0, etiquetas exactas, cuenta por cobrar atómica, finanzas 200/403, aislamiento | EJECUTADO en local (§3) |
| Navegador `inventory-first` (12 equipos, 50 cargadores, publicación, 390 px) | EJECUTADO 8 de 8 en local, también sobre la base actualizada |
| CI de `master` en `15fbbae` | PENDIENTE de confirmación al redactar esto (ver el informe de GO) |
| Ventas móviles en cola de un producto a 0 | NO VERIFICABLE desde aquí; en producción hay 0 productos activos a precio 0 |
| Prueba de humo en producción con cuentas reales | PENDIENTE (solo tras el GO) |
| Restauración de la copia real y ensayo con ella | PENDIENTE; opcional (R2 mostró que el ensayo sintético coincidió con el real) |

Un ensayo sintético no es una verificación productiva: sus resultados describen el código y el esquema, no los datos reales de la empresa.
