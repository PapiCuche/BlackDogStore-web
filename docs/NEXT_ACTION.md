# NEXT ACTION — 2026-11-12

## Producción vigente — R4.5 `e0290c1` — DESPLEGADO · conciliación de pagos activa (TEST)

Desplegada el 2026-11-12 a las 01:08 (Lima) con GO para ese SHA exacto; esquema `0164`; Caddy actualizado a las 01:15. Backend `release-e0290c1` / `efe368deb5f4`, frontend `release-e0290c1` / `019f75df2b46`. Vuelta atrás: `rollback-fc72201` (código) y `migrate store 0163` sólo con la imagen `release-e0290c1`. Detalle y vigilancia en la entrada R4.5 de `02_ESTADO_ACTUAL.md`. La revisión visual del panel por el propietario sigue PENDIENTE.

## Historial del candidato R4.5 (integrado el 2026-11-11/12)

Contiene #264, #254, #255, #256, #257, #261 (F1), #262 (B0, migración `0164`) y #265 (bloqueo mutuo abastecimiento ↔ pago), más la documentación #258, #259, #263 y la de este cierre. Código idéntico a `e0290c1`. Tabla de merges en la entrada R4.5 de `02_ESTADO_ACTUAL.md`; runbook en la carpeta de preflight `r45-20261111/PREFLIGHT.md`.

**Lo que se hizo en el despliegue** (queda como referencia del runbook):
- respaldo, restauración aislada y `migrate store 0164` de ida, vuelta e ida sobre la copia;
- en producción, `0164` se aplica **antes** de cambiar el código, con `PGOPTIONS='-c lock_timeout=5s -c statement_timeout=30s'`;
- **vuelta atrás del esquema con la imagen R4.5**: con la anterior, `migrate store 0163` no hace nada y sale con 0;
- Caddy: aplicar a mano la fusión a tres vías de #257 (2 líneas), `caddy validate` y `caddy reload`. Hay que conservar la configuración de Cloudflare;
- humo de pedidos, pagos, ofertas de proveedor (fecha obligatoria) y subidas de fotos de unidad de más de 1 MB.

**Pendiente de decisión del propietario:**
- PR #266, prueba E2E `catalog-editor` (sólo pruebas): CI verde, sin fusionar;
- `reconcile_payments`: **ACTIVO en el cron desde el 2026-11-12 02:19** (claves TEST). Seguimiento de 48–72 h en curso; condiciones para pasar a claves de producción en la entrada PAY-RECONCILE de `02_ESTADO_ACTUAL.md`;
- seguimiento en la app móvil (`handoff-mobile-f1-409.md`): mostrar el `detail` del 409 y enviar el motivo al retroceder un paso;
- borrar unos 269 volúmenes Docker huérfanos de este Mac (7 con copias de producción): inventario listo, pendiente de autorización;
- borradores #162 y #249, fuera de R4.5;
- **A1 (paleta por modelo): FUSIONADO en master `27e3503`** (#269), sin desplegar; migración `0165` pendiente de un despliegue. Siguientes etapas propuestas: A1b, A2 y B1;
- retirar la regla temporal del firewall para `179.6.165.234/32`, que corresponde al propietario.

## Historial — estado al 2026-10-30

R4 (`17ed87f`, 2026-10-31), R4.1 (`75a8f2d`, 2026-11-04) y R4.2 (`1894551`, 2026-11-06) están en `02_ESTADO_ACTUAL.md`. Lo que sigue es el estado de este archivo al 2026-10-30 y ya no es el vigente.


### Producción vigente — `d52e9b8` — IMPLEMENTADO — DESPLEGADO

INV-RECEIVE-UX-02, NAV-UX-01 y NAV-UX-02 desplegados con GO el 30/10/2026, 11:58 Lima. Backend `release-d52e9b8` / `37cb59d55205`, frontend `release-d52e9b8` / `6f67fc548f35`, sanos; CI 8/8, respaldo externo/restauración aislada, healthcheck y humo HTTP/API PASS. Esquema `0159`, sin migraciones; SUNAT BETA desactivado; rollback `rollback-b21c7d7` conservado.

**Siguiente:** registrar la aceptación funcional expresa del propietario. Humo visual autenticado del menú, pestañas, herramientas, caja y asistente completado tras su inicio de sesión, sin líneas ni operaciones ficticias; sin desborde en los tamaños comprobados. No se declara aceptación del release por ese humo. No repetir el despliegue ni desplegar otras fases por este cierre. Detalle y límites de evidencia en la entrada de despliegue de `02_ESTADO_ACTUAL.md`.

**Historial de integración:** los tres encabezados siguientes conservan su estado anterior al GO; «NO DESPLEGADO» ya no es el estado vigente. Los pendientes posteriores y propuestas permanecen abiertos.

### NAV-UX-02 — IMPLEMENTADO — PR #214 — NO DESPLEGADO

Pestañas por destino, página «Más herramientas» y recibir desde Productos; sólo frontend de navegación. Sigue pendiente la decisión del propietario sobre el precio de venta por equipo (INV-RECEIVE-SALE-PRICE) y las propuestas NAV-ACTION-SEARCH y NAV-ROW-RECEIVE (`docs/AUDIT_MEMORY.md`).

### NAV-UX-01 — IMPLEMENTADO — PR #213 — NO DESPLEGADO

Menú lateral por destinos (nueve entradas entre Catálogo e Inventario), sólo frontend de navegación, sin migraciones. NAV-UX-02 (pestañas por destino, página «Más herramientas», recibir desde Productos) sigue en su propio PR. Un despliegue de cualquiera de los dos es de código y **requiere GO aparte**.

### Estado vigente — INV-RECEIVE-UX-02: IMPLEMENTADO — EN MASTER (#210, `b49bcf2`) — NO DESPLEGADO

Pegar una lista de equipos ya es parte del paso 2 de «Recibir mercadería» (ver `02_ESTADO_ACTUAL.md`, entrada del 2026-10-30). Sin migraciones: un despliegue sería de código (imágenes nuevas, como INV-OPS-01) y **requiere GO aparte**. Antes de desplegar: CI de master sobre el SHA a desplegar, preflight y respaldo como en INV-OPS-01 y, si el propietario quiere, una pasada manual con su lista real de seminuevos (la prueba automática usa datos ficticios). Decisión pendiente del propietario: precio de venta por equipo en la recepción (`INV-RECEIVE-SALE-PRICE`, propuesta; hoy se ignora igual que antes).

### Estado previo — INV-OPS-01: IMPLEMENTADO — DESPLEGADO — ACEPTADO

Código productivo `b21c7d7efbe7e26b4678eb478def56419d4ae9d5`. Backend `ops-b21c7d7` / `f01b8b68e239`; frontend `ops-b21c7d7` / `2461a31ee95c`. Esquema `0159`, sin migraciones nuevas. Healthchecks, humo y restauración aislada PASS según los informes existentes de Codex; aceptación funcional del propietario **8/8 PASS**. SUNAT BETA desactivado según preflight; rollback R3 conservado. Este cierre no consulta ni modifica producción.

El predeploy de `6747fa9` y la aceptación pendiente del informe de despliegue quedan superados por este cierre. El historial de fases, pruebas y decisiones permanece en `02_ESTADO_ACTUAL.md`, `07_CHANGELOG.md` y Git; no repetir su lista de PR, CI ni despliegue pendientes.

### Próximas entregas — PENDIENTE

1. **LABEL-B** (independiente de INV-RECEIVE-UX-02): bandeja de lotes pendientes, API de lectura/impresión, recuperación de movimientos sin lote y estado real de impresión. LABEL-A y `0159_label_batches` ya forman parte del código desplegado; no tratarlos como pendientes de fusión o migración.
2. **LABEL-C:** plantillas por empresa y logotipo.
3. **INV-OPS-02:** simplificar el recuento.
4. **LABEL-D:** BLOCKED: HARDWARE; no imprimir ni instalar agentes sin la autorización correspondiente.

### Deudas que no cierra la aceptación

- **P3:** historial y navegación tras recarga; el borrador sigue sólo en memoria, sin persistir datos sensibles.
- **P3:** foco tras «Corregir» + «Atrás», texto de líneas con error y conexión del selector de empresa en pantallas de inventario bajo `AccessGuard`.
- Alcance y criterios de estas deudas: [runbook INV-OPS-01](release-inv-ops-01.md#deudas-y-siguientes-entregas). Resto de deudas y bloqueos del proyecto: [AUDIT_MEMORY §10](AUDIT_MEMORY.md#10-deuda-técnica-activa); no se consideran cerrados por la aceptación de INV-OPS-01.
- Decisiones previas que siguen pendientes de decisión o comprobación específica: serie obligatoria por equipo, tope de descripción, últimos cuatro dígitos de IMEI público y cron de `generate_finance_obligations`. No se implementan ni programan en este cierre.

### Otros frentes previos que este cierre no resuelve

- FIN-B2 y FIN-D siguen en la lista pendiente previa; FIN-G continúa BLOCKED. El ADR financiero #185 requiere su revisión específica.
- ECOM-READY-D2 (fotos por unidad de seminuevos, edición por lotes y tema) e INV-UX-A2 no quedan cerrados por esta aceptación.
- Antes de retomar estos frentes, verificar su estado real en código y `02_ESTADO_ACTUAL.md`. El [NEXT_ACTION anterior completo](https://github.com/PapiCuche/BlackDogStore-web/blob/6747fa92a17088696269921a03ddea15d2446ecd/docs/NEXT_ACTION.md) conserva los handoffs históricos de finanzas, inventario, catálogo y pruebas; no reutilizar sus estados de despliegue obsoletos.

### Límites y continuidad

Trabajar en worktrees; el árbol principal contiene cambios del propietario. Seguir `AGENTS.md` y `docs/AGENT_TOKEN_POLICY.md`. Verificar código y estado de master antes de retomar cualquier rama antigua; sus estados históricos «en rama» no describen automáticamente pendientes actuales.

Este PR es exclusivamente documental. Cualquier siguiente despliegue, migración, integración productiva, envío externo o cambio de datos requiere el GO que corresponda. La aceptación de INV-OPS-01 no autoriza esas operaciones ni una restauración sobre la base productiva.
