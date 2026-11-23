# NEXT ACTION — 2026-11-23

## FASE 1.2 — catálogo visual de equipos nuevos: en master `79058ac`, SIN DESPLEGAR

**Hecho:** #387, #388, #389 y #390 están fusionados. Incluyen:
- la matriz verificable y los números de modelo;
- el informe de cobertura y los acabados sin foto en el panel;
- la vista de cada foto y «Reemplazar foto»;
- las muestras aproximadas y la muestra propia por modelo;
- el importador de fotos por manifiesto;
- las pruebas E2E: 48/48, con capturas de escritorio y móvil.

La única migración es `0185`, aditiva. Detalle en la entrada del 2026-11-23 de `02_ESTADO_ACTUAL.md` y en `docs/adr-catalog-reference.md` §10–§11.

**GO pedidos.** Cada uno es independiente y ninguno se da por concedido:
1. **«GO despliegue 79058ac prod-02»**
   - Cómo: en `/srv/blackdogstore`, `sh backups/release-f12/d_deploy_f12.sh 79058acd64c9b44050ede2c0e025c19a2c1460b7`. Las imágenes `rc-79058ac` ya están construidas en prod-02.
   - El script exige producción en `ed9f5e0` con el esquema `0184`, y que el SHA no cambie `deploy/` ni el compose.
   - Antes de cambiar nada: backup y huella.
   - Aplica `0185` antes de cambiar el código y recrea sólo backend y frontend. No toca Caddy ni el CRM, no importa fotos, no cambia muestras y no normaliza el catálogo.
   - Vuelta atrás: `sh backups/release-f12/rollback_f12.sh` devuelve sólo el código, porque el de `ed9f5e0` funciona con `0185` (ensayado). `rollback_f12.sh schema` además deshace `0185` y pierde las vistas y las muestras guardadas desde entonces.
2. **«GO normalización catálogo prod-02»** (Fase 1.1): sin cambios, ver abajo. Funciona antes o después del despliegue.
3. **«GO muestras aproximadas prod-02»**: sólo después del punto 1.
   - Aplica 205 muestras por modelo, todas marcadas «aproximada (pendiente de validación comercial)», desde la propuesta `backups/release-f12/swatch-approx-proposal.json`. No son códigos oficiales.
   - Pasa por la vista del panel en una sola transacción, con una auditoría que guarda los valores anteriores. La vuelta atrás es exacta.
   - Script: `f12s_ops.py`, con su ensayo `f12s_rehearse.sh`.
   - El propietario puede cambiar o quitar valores antes del GO.
4. **Fotos:**
   - El propietario consigue fotos propias o con licencia comprobable y prepara la carpeta y el manifiesto (formato en el ADR §10). Después se ensaya la importación y se pide «GO importación fotos prod-02».
   - Las fotos de apple.com no se copian (`BLOCKED: LICENSING`).

**Decisiones del propietario (BLOCKED):**
- fotos con licencia (0 de 205);
- qué hacer con los 14 productos que enlazan fotos de sitios ajenos (5 del CDN de Apple);
- códigos de color aprobados (la propuesta los deja «aproximados»);
- si una foto sin licencia verificada puede salir en la tienda (hoy sí, y la tienda no lo indica);
- si el carrito debe usar la foto «principal» en vez de la primera.

Siguen sin empezar las fases 2 (seminuevos) y 3 (inventario general), y el cambio masivo de fotos.

## Producción vigente: Fase 1 `ed9f5e0` (esquema `0184`), desplegada el 2026-11-22 08:11 en prod-02

- Detalle y verificación en la entrada del 2026-11-22 de `02_ESTADO_ACTUAL.md`.
- **Vuelta atrás de la Fase 1:** `sh backups/release-p1/rollback_p1.sh` en `/srv/blackdogstore` (sólo código, a `release-4a3ce34`). Repone los tres archivos de enrutamiento del CRM desde `backups/p1-routing-20261122-081143/`: en `ed9f5e0` están versionados y un checkout simple a R12b borraría dos de ellos.
- **Catálogo importado** el 2026-11-22 (entrada «CATÁLOGO iPHONE IMPORTADO» de `02_ESTADO_ACTUAL.md`): 21 modelos nuevos ocultos, 28 enlazados, 6 parciales pendientes de revisión; nada publicado, sin variantes ni stock.
- **Fase 1.1 (CATALOG-F11):** código #383 fusionado (`ffc1315`, sin desplegar y no necesario para el GO), ensayo aislado y simulación final verdes, producción sin cambios. Detalle en `docs/adr-catalog-reference.md` §9.
- **Simulación final v2 (2026-11-22 15:30 Lima)**, sobre una copia aislada de `db-20261122-153017`, con el código de producción tal cual. Producción leída sin escribir: precondiciones exactas, huella `1689fd2825cc5724`. «Región» es procedencia (mercado de venta), así que #23 queda fuera: `docs/adr-catalog-reference.md` §9.5.
- **GO pedido — «GO normalización catálogo prod-02».** Autorizaría sólo, en prod-02 y para Black Dog Store, con backup previo, instantánea por tabla antes y después y actor #1:
  1. rol «capacidad» para «Capacidad» (#1) y rol «color» para «Color» (#2);
  2. «128Gb» → «128 GB» (orden 128), «256Gb» → «256 GB» (256), «512Gb» → «512 GB» (512), «1TB» → «1 TB» (1024).
  Cómo: `sh backups/release-p1/f11p_go.sh` en `/srv/blackdogstore`. Usa las vistas del panel de `ed9f5e0` en **una sola transacción**, con la empresa bloqueada. Se detiene sin escribir si cambian el SHA, el esquema, la imagen, los roles, las ocho capacidades o #23. Deja 6 auditorías de las vistas y una `catalog_normalized` con los valores anteriores; repetir no escribe.
  No toca #19, #22 ni #23, precios, fotos, stock, publicación ni ventas.
  Efecto visible: la caja pregunta Capacidad → Color → SIM → Región; las capacidades salen por tamaño en caja y tienda.
  Vuelta atrás: `F11_MODE=rollback`, por las mismas vistas y en una transacción; deja exactamente el estado anterior.
- **#23:** se completa cuando haya equipo y su número de modelo, antes de recibirlo (§9.5). Decisión del propietario.
- **Decisiones del propietario (BLOCKED):**
  1. SIM y región de la variante #22 «iPhone 15 128Gb Negro», o quitar SIM/región de la paleta del iPhone 15;
  2. colores hexadecimales aprobados (hoy se ve el nombre del color);
  3. fotos por color con origen y licencia (0 de 205);
  4. qué regiones compra por modelo (para recortar paletas: hoy 1472 combinaciones);
  5. #23: completar sus opciones con el número de modelo del equipo cuando llegue, y si se renombra «iPhone 17 Pro 256 GB Plata …»;
  6. si el atributo «Región» pasa a llamarse «Versión (país de venta)»;
  7. #19 «iPhone 13 Pro Max» (equipo usado) queda para la Fase 2.
- Los 6 modelos parciales (iPhone 5c, 4s, 4, 3GS, 3G y el original) siguen fuera; aprobarlos uno por uno sólo si se van a vender.
- Pendiente con el propietario: la prueba en la impresora térmica real.
- Cada despliegue nuevo necesita un GO del propietario para su SHA exacto.

## Historial — estado al 2026-11-15

## B1-0b-2 — FUSIONADO en master `237ed15` (#292), sin desplegar

Importes de las cargas de productos y de stock según DEC-INV-MONEY-01 (D-B1-0b-2a…h). El propietario aceptó el riesgo de fusionar sin archivos reales de los trabajadores. Producción sigue en `b192fff`. Siguiente paso: un GO de despliegue independiente, en una ventana que incluya también `0165`–`0169`. Recomendado antes: pasar una carga real anonimizada por la vista previa, sin aplicarla.

## URGENTE — LEGAL-POLICIES-01 (privacidad para Meta)

Hotfix `hotfix/legal-policies` sobre `e0290c1`, con la migración propia `0165_company_legal_policies`, que sólo crea una tabla. Master lo incorpora con `0169_merge_legal_policies`. Estado del despliegue y verificación pública: ver la entrada de `02_ESTADO_ACTUAL.md`. Los tres textos se publicaron con `load_legal_policy --publish` (auditado) y el propietario los revisa y edita desde el panel. #290 FUSIONADO en master `5f29d88`; #289 cerrado como reemplazado. B1-0b-2 sigue sin implementar.

**Producción vigente: `b192fff`** = `e0290c1` + hotfix legal (esquema `0164` + `0165_company_legal_policies`). Lo descrito en la sección R4.5 siguiente sigue vigente salvo el SHA; la vuelta atrás del hotfix vuelve a `release-e0290c1`.

## Base de producción — R4.5 `e0290c1` (sustituida el 2026-11-15 por el hotfix legal `b192fff`) · conciliación de pagos activa (TEST)

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
- **A1 (paleta por modelo): FUSIONADO en master `27e3503`** (#269), sin desplegar; migración `0165` pendiente de un despliegue;
- **A1b-1 (columnas por rol, nombre de modelo normalizado, migración `0166`): FUSIONADO en master `402ce17` (#271), sin desplegar.** D-A1b-1 aprobada e implementada; D-A1b-2/D-A1b-3 (crear variante desde la paleta) PENDIENTES, el endpoint no va en #271. Runbook de `0165`+`0166`: `python manage.py check_model_names` debe devolver **0** antes de migrar, después de migrar y tras cualquier ventana de vuelta atrás con el código anterior (si devuelve 2: `--fill` para claves vacías o desfasadas; ante colisiones, decidir desde el panel; nunca fusionar). En la copia del 2026-11-12: 31 modelos de 1 empresa, 0 colisiones. **A1b-2a FUSIONADO en master `e796795`** (#274, migración `0167`), sin desplegar. Despliegue futuro: migrar `0165`–`0167` antes del código, reversión sólo de código, `check_model_names` = 0. **A1b-2b FUSIONADO en master `fc0a513`** (#275, sólo frontend), sin desplegar; 3 deudas menores de frontend registradas en `02_`. **A1b completo en master; `0165`–`0167` pendientes de producción.** **A2-1 (alias por empresa y resolución comercial, migración `0168`): FUSIONADO en master `9841966` (#277)**, sin desplegar. DEC-CATALOG-ALIAS-01 aprobada (P3/P5, P6, P7, P8, P9). R-4: P1 y P2 pendientes del propietario; P4 aprobada (línea sin color = `incompleta`). **A2-2 (frontend, vocabulario de proveedores y deuda de A1b-2b): FUSIONADO en master `f0b2c87` (#279)**, sin desplegar. **A2 completo en master.** **B1:** D-B1-1 a D-B1-4 APROBADAS (DEC-SUPPLIER-LIST-01). **B1-0 (importes ambiguos) FUSIONADO en master `9f5410a` (#281)**, sin desplegar. **B1-1 (asistente de listas de WhatsApp) FUSIONADO en master `f35bd6f` (#283)**, sin desplegar; D-B1-5 (colores compuestos) aprobada e implementada. **B1-0b:** auditoría hecha (ver `02_`); los PR propuestos esperan GO. **B1-0b-1 (costos de inventario) FUSIONADO en master `9ab8430` (#285)**, sin desplegar y sin migraciones (DEC-INV-MONEY-01). Siguiente: B1-0b-2 (cargas de productos y stock) con la política de moneda aprobada el 2026-11-15, sin implementar hasta revisar 2 o 3 archivos representativos del propietario (D-B1-0b-2c moneda, D-B1-0b-2d precisión y D-B1-0b-2e seguridad, aprobadas el 2026-11-15; sin GO de implementación); prueba aleatoria de la IA de inventario corregida en #287 (`630d49c`); la consulta de producción sobre costos mal leídos está propuesta y sin ejecutar; B1-0b-3 (finanzas) y promociones, caja y nota de débito esperan GO propio. Producción sigue en R4.5 `e0290c1` (`0164`); `0165`–`0168` necesitan una ventana propia. Despliegue futuro: `0165`–`0168` en una sola ventana, antes que el código;
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
