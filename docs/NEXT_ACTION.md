# NEXT ACTION — 2026-11-23

## PRODUCCIÓN (prod-02) — `16189e4`, esquema `0186_label_printing`, imágenes `release-16189e4`; sana

Comprobado en sólo lectura a las 22:38 (Lima):
- backend y frontend «healthy»;
- md5 del enrutamiento sin cambios (`108dfbf3`, `dc0b749a`, `00949a94`);
- 9 contenedores del CRM;
- ningún despliegue ni migración en marcha, y el candado de producción libre.

### LABEL-04 (`16189e4`): cambio productivo con **autorización no acreditada**

No se revierte: producción está sana. **El propietario debe confirmar si dio el GO.** Un PR fusionado no es un GO.

| Punto | Evidencia |
|---|---|
| Quién lo ejecutó | Otra sesión, sin identificar. Ninguna transcripción local la registra: ni Claude Code, ni Codex (su última sesión acabó a las 16:37:15), ni Antigravity, ni Gemini. La sesión del CRM sólo lo observó. |
| Cuándo | 2026-11-23, hora de Lima. Imágenes `rc-16189e4` compiladas a las 16:40:35. Primer intento a las 16:40:43: hizo su backup (16:40:57), migró `0185` y `0186`, falló la espera de salud y volvió el código a `ed9f5e0` a las 16:41:31, sin rebobinar la base. Segundo intento a las 16:42:24: correcto. |
| GO del propietario | **No encontrado.** El #367 no tiene comentarios ni revisiones. Lo fusionó la cuenta del propietario (14:52 Lima), pero fusionar no es autorizar el despliegue. |
| SHA y alcance | `16189e4` (#367, LABEL-04). Contiene `d32f43b`, así que también desplegó el código de la Fase 1.2 y su migración `0185`. |
| Runbook | `backups/deploy-label04-16189e4.sh` y `backups/deploy-label04-16189e4-retry.sh`. No existe un runbook revisado. |
| Backup previo | **El verdadero es `backups/label04-20261123-164057/`**: completo, y su dump no contiene `0185` ni `0186` (comprobado). El del reintento, `label04-20261123-164229`, ya contiene `0185` y `0186`. |
| Resultado | Rutas de la tienda y del panel en 200. LABEL-04 no cambió Caddy ni los archivos de enrutamiento del CRM (md5 iguales). 0 fotos de acabado, 0 muestras por modelo y 0 licencias verificadas. |
| Concurrencia | Aparte, entre las 16:40:58 y las 16:41:48, la sesión del CRM desplegó el CRM (sus propios contenedores) en el mismo servidor. Coincidió con el primer intento de la tienda, el que falló. |

### Un solo ejecutor en producción

Regla nueva en `AGENTS.md` §10. Todo cambio productivo se hace con el GO exacto, tras comprobar el estado real del servidor y bajo el candado `backups/ops/prod_lock.sh`.
- El candado se niega (código 75) si otro lo tiene o si hay un deploy, un migrate, un `compose up`/`build` o un backup en marcha.
- Ensayado: un segundo titular, un deploy simulado y la liberación al terminar.
- Todos los scripts de GO del kit se relanzan solos bajo el candado.

## VARIANT-SAFE-01 — rama `feat/variant-safe-01` (migración `0188`, sobre `0187` ya en master); PR abierto, sin fusionar ni desplegar

- Fusionar con CI verde sobre el HEAD exacto; nada desplegado.
- Decisiones por confirmar con el propietario: las imágenes de la galería bloquean la eliminación (no se borran con la variante); los códigos de barras sí se borran con ella (quedan en la auditoría); un carrito con la variante bloquea; desarchivar la deja inactiva; eliminar/archivar sólo para variantes (producto con modelo).
- Despliegue: la migración no cambia SQL de las FK (Django aplica `on_delete`); añade 3 columnas, 1 CHECK y la capacidad `products.delete`. Con `PGOPTIONS='-c lock_timeout=5s -c statement_timeout=30s'`.

## FASES 1.1 Y 1.2 — código de la Fase 1.2 en producción con `16189e4`; todo lo demás preparado y ensayado, SIN EJECUTAR

Runbook: `backups/release-f12/RUNBOOK-16189e4-prod02.md`.

- **Normalización 1.1.** Ensayada sobre una copia posterior a LABEL-04 (`backups/f11p-rehearsal-16189e4.log`):
  - huella `1689fd2825cc5724`; un paso fallido, un fallo provocado o una huella equivocada dejan 0 diferencias;
  - se aplica con 2 roles, 4 capacidades y 7 auditorías;
  - repetirla no escribe nada y la vuelta atrás es exacta.
  - #19, #22 y #23 no se tocan.
- **Muestras.** Sólo las **167 aplicables** (40 modelos), marcadas «aproximada» y editables (`backups/f12s-batch-rehearsal-16189e4.log`).
  - Las **38 dudosas** (9 modelos, ninguno común con los 40) quedan fuera; `f12s_go.sh` rechaza cualquier propuesta que las contenga.
  - La vuelta atrás deshace el último lote no revertido.
- **Producto 12** («Cable Apple USB-C a USB-C»: activo, publicado, 90 en stock). La tienda muestra el icono de imagen rota porque la URL apunta a un sitio de terceros.
  - La operación sólo vacía `image_url` a través de la vista del panel, que deja su propia auditoría. Así la tienda muestra el marcador neutro.
  - No cambia precio, stock, publicación ni otras imágenes.
  - Ensayada (`backups/p12-rehearsal-16189e4.log`): un fallo provocado y una huella equivocada no cambian nada; se aplica; repetirla se rechaza; la vuelta atrás repone la URL y vuelve a la huella `10e9b5c6256a054b`.
- **Producto 7** sigue inactivo hasta sustituir su foto, que mezcla cuatro acabados.
- **Release `edbeeb0` (master con el #396)**, para que la caja sólo muestre fotos con licencia verificada.
  - `edbeeb0` es la fusión del #397 (sólo documentación) sobre el #396 (`8899bf9`). Entre `16189e4` y `edbeeb0` entran además los PR de documentación #394, #395 y #397.
  - Código auditado: `finish_media.py`, `pos_device_services.py` y `test_finish_images.py`; el resto es documentación. Sin migraciones, sin cambios en `deploy/` ni en el compose, sin cambios en el frontend.
  - `d_deploy_pos.sh` aborta si el HEAD no es `16189e4`, si el esquema no es `0186`, si las imágenes en marcha no son `release-16189e4`, si aparece un archivo de código no auditado o si la nueva imagen planea migraciones.
  - Sólo recrea backend y frontend: nunca Caddy ni el CRM. Vuelta atrás de sólo código con `rollback_pos.sh`.
  - Ensayo sobre una copia aislada de un backup recién hecho (`backups/pos-rehearsal-edbeeb0.log`), en verde:
    - plan sin migraciones; `check` y `makemigrations --check` limpios;
    - humo profundo 32/32 con la imagen vieja (como quedaría tras volver atrás) y con la nueva; reimpresión histórica correcta;
    - caja con una foto sintética de iPhone 15 Negro: sin verificar, la imagen nueva muestra la muestra de color y la vieja la foto; verificada desde el panel, la nueva muestra la foto;
    - 368 pruebas de los módulos afectados dentro de la imagen, en verde;
    - rutas del frontend correctas (la página inexistente da 404).
- **Fotos:** las 58 del manifiesto no tienen licencia acreditada; ninguna se da por autorizada. El plan de sustitución está en `release-f12/biblioteca-visual/SUSTITUCIONES.md`.

**GO pedidos**, cada uno por separado y por este orden. Se ejecutan en prod-02, en `/srv/blackdogstore`. Los scripts no están en el repositorio: viven en el kit privado `backups/release-p1/` y `backups/release-f12/` del servidor (las copias de `/tmp` se comprobaron idénticas).
1. **«GO normalización catálogo prod-02»**: `sh backups/release-p1/f11p_go.sh`.
2. **«GO muestras aproximadas prod-02»**: sólo las 167, con `sh /tmp/f12s_go.sh /tmp/swatch-approx-aplicables.json`.
3. **«GO quitar enlace roto producto 12 prod-02»**: `sh backups/release-f12/p12_go.sh`. Independiente de 1 y 2.
4. **«GO despliegue edbeeb0 prod-02»**: `sh backups/release-f12/d_deploy_pos.sh edbeeb0b28c500424e77609d145cebfff07e000d`. Vuelta atrás: `sh backups/release-f12/rollback_pos.sh`.
5. **«GO importación fotos prod-02»**: sólo después del 4 y con fotos propias o con licencia verificable.

**Decisiones del propietario (BLOCKED):**
- confirmar si autorizó LABEL-04;
- fotos propias o con licencia: producto 12, producto 7 antes de reactivarlo y las otras 56 del manifiesto;
- las 38 muestras dudosas;
- #22: SIM y país de venta, que no se deducen;
- #23: completarla o renombrarla, sólo con datos del equipo;
- #19: queda para la Fase 2.

**Propuesta para una fase posterior, sin aplicar:** «Versión (país de venta)» como etiqueta de «Región». No se crean stock ni variantes ficticias. La Fase 2 de seminuevos no se abre.

## LABEL-04 en caja (pendiente del propietario)

En caja, actualizar el agente a 1.4 y asociar la Advance con la cola `POS-80-Series` y la HL80 con `HL80`. Confirmar el tamaño del rollo cargado antes de activar las etiquetas automáticas.

## Producción anterior: Fase 1 `ed9f5e0` (esquema `0184`), desplegada el 2026-11-22 08:11 en prod-02 (sustituida el 2026-11-23 16:42 por `16189e4`)

- Detalle y verificación en la entrada del 2026-11-22 de `02_ESTADO_ACTUAL.md`.
- **Vuelta atrás de la Fase 1 — YA NO APLICA:** producción está en `16189e4` con `0185` y `0186`; este script volvería a un código anterior a ese esquema. No ejecutarlo. Texto histórico: `sh backups/release-p1/rollback_p1.sh` en `/srv/blackdogstore` (sólo código, a `release-4a3ce34`). Repone los tres archivos de enrutamiento del CRM desde `backups/p1-routing-20261122-081143/`: en `ed9f5e0` están versionados y un checkout simple a R12b borraría dos de ellos.
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
