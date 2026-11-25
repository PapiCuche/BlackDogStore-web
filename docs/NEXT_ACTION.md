# NEXT ACTION — 2026-11-25

## R13.2 · CIERRE PRODUCTIVO CONTROLADO (2026-11-25) — producción SIN CAMBIOS; todo preparado, cada paso espera su GO

Orden del propietario: cron → normalización → muestras 167 → producto 12 → R13.2. El mensaje del propietario del
2026-11-25 **no concede** ningún GO. Antes de cada operación: SHA y esquema, candado, backup, preflight, huellas y alcance;
sólo el cambio autorizado; datos antes y después; pruebas; evidencia.

| # | Operación | GO exacto que falta | Kit | Estado |
|---|---|---|---|---|
| 1 | Cron de cotizaciones | «GO cron cotizaciones prod-02 kit v2» | `backups/release-r13-ops/cron_expire_quotes_install.sh` (v2, ensayo 33/33) | Listo; primero `--dry-run`, si da >0 cotizaciones se para y se pregunta |
| 2 | Normalización del catálogo | «GO normalización catálogo prod-02 kit v131b» | `f11p_go.sh` (no toca las variantes #19, #22, #23) | Listo |
| 3 | Muestras aproximadas, **sólo 167** | «GO muestras 167 prod-02 kit v131b» | `f12s_go.sh` (excluye las 38 dudosas) | Listo |
| 4 | Imagen rota del producto 12 | «GO producto 12 prod-02 kit v131b» | `p12_go.sh` | Listo |
| 5 | Despliegue R13.2 `c8a43215` | «GO despliegue c8a4321 prod-02» | `backups/release-r132/d_deploy_r132.sh` | Listo (ver abajo) |
| — | Sellos de las 7 notas (fuente `222417`) | «GO restaurar sellos 7 notas prod-02 fuente 222417» | `backups/release-notes7b/notes7_go.sh` | Listo; acepta R13.1 o R13.2, no depende del orden |
| — | Carga del stock real | «GO carga stock real prod-02» | panel + `docs/carga-stock-real.md` §11 | Espera archivos, R13.2 y aprobación del informe |

Los kits 2–4 fijan R13.1 `bf6a3307`: deben ir antes que R13.2, como pidió el propietario. Si R13.2 se desplegara antes,
habría que refijarlos y volver a ensayarlos.

**R13.2 reconfirmada (sólo lectura, 2026-11-25; resultados del ensayo de `c8a43215` en `backups/release-r132/r132-rehearsal.log`):** imágenes `rc-c8a4321` construidas desde un árbol limpio en
`c8a4321538d9…` (backend `sha256:211010ebf1c9…`, frontend `sha256:085254fd4e4c…`); `git diff bf6a3307..c8a43215` sin
cambios en `deploy/`, compose, Dockerfiles ni migraciones (43 archivos de código, pruebas y docs); ensayo: humo 32/32,
simulación 19/19, exportaciones con IMEI ocultos y 403 `full_identifiers_forbidden` sin `inventory.adjust`, reimpresión
de sólo lectura (7 notas A4, ticket y ESC/POS), vuelta atrás a `release-bf6a330` con humo 32/32. No se desplegará un SHA
posterior de `master`.

### Stock real — proceso final preparado · espera archivos del propietario
- Orden: R13.2 desplegada → archivos del propietario en `backups/stock-real-inbox/` → vista previa en copia aislada
  (`backups/stockreal-preview/`, `README-stockreal-preview.md`) → aprobación del informe por el propietario →
  «GO carga stock real prod-02» → carga en el panel (`docs/carga-stock-real.md` §11).
- Kit con revisión independiente (todos sus hallazgos corregidos: lectura de identificadores con el lector del importador, privacidad, guarda por host, backup nuevo obligatorio, identidad de imagen, sondas de red) y validado en prod-02 con archivos sintéticos
  sobre el catálogo real: con R13.2, 4 equipos registrados en la copia, `check_unit_stock` 0, todo deshecho; aislamiento
  179 destinos, 0 abiertos salvo el sshd del propio host (sólo con clave, se informa); producción sin cambios (filas de equipos, movimientos
  y cargas iguales; 0 filas insertadas, actualizadas o borradas).
- Con R13.1, un archivo de nuevos con «Precio de venta» se rechaza entero: R13.2 primero.
- Observado en el catálogo real (para el propietario): «iPhone 15 128Gb Negro» está publicada y no marcada «lleva IMEI».
- Regla vigente tras #432: quien registra una carga con precios o garantías de seminuevos también necesita
  `products.manage` (se rechaza indicando las filas). Para el propietario: confirmar que no hay un flujo en el que un
  almacenero registre la vista previa de un administrador. En la recepción del proveedor el `sale_price` de una fila
  sigue sin esa comprobación (sin efecto práctico: un seminuevo no se vende bajo pedido).

### Siete notas — fuente `db-20261121-222417` (elegida por el propietario) · kit nuevo ENSAYADO 72/72 · espera GO
- Kit `backups/release-notes7b/` (runbook `RUNBOOK-notes7b-prod02.md`). **Sustituye** al de `release-r13-ops`
  (fuente `140538`, marcado `NOTES7-SUPERSEDED.txt`).
- Encuesta (producción sólo lectura): mismos 7 ids, identidad igual, **sólo `pdf_generated_at` difiere** (md5 de las 15
  columnas), 0 filas de auditoría sobre las notas desde el backup, cada original = última descarga hecha por una persona
  (3–4 ms), **notas 5 y 7 → NULL** (nunca descargadas).
- Actualización condicional e idempotente de un solo campo, una fila de auditoría por nota, diff exacto, backup previo.
- Ensayo en copia aislada (`db-20261125-084236`): aplicar, repetir (NO-OP), 11 rechazos del script del GO y 2 de su vuelta atrás, sin escribir (más fallo inyectado y huella equivocada, deshechos), vuelta atrás,
  reaplicar, serializador del panel y reimpresión de sólo lectura correctos (los avisos «fallo al abrir evidencia» son de la copia, que no lleva las fotos de evidencia; no afectan a las notas); acepta R13.1 y R13.2. Producción intacta.
- Huella antes `a42dfa17d6779f45` → después `c2be046bf7bce2d8`.
- GO: **«GO restaurar sellos 7 notas prod-02 fuente 222417»**; vuelta atrás: **«GO vuelta atrás sellos 7 notas prod-02 fuente 222417»**.


## PRODUCCIÓN (prod-02) — `bf6a3307` (R13.1), esquema `0192_unit_photo_provenance`, imágenes `release-bf6a330`; sana

## OPERACIONES PRODUCTIVAS 2026-11-24 (tarde) — autorización controlada del propietario

| GO | Hora (Lima) · ejecutor | Resultado |
|---|---|---|
| «GO despliegue bf6a330 prod-02» | 18:04–18:06 · Claude (sesión principal), bajo `prod_lock.sh` | **HECHO.** R13.1 `bf6a3307`, sólo código, plan de migraciones vacío. Backup previo `db-20261124-180452`. Huellas antes/después idénticas; 14 rutas en 200 y la inexistente en 404; `healthcheck` sin ATENCIÓN; `check_unit_stock` 0 diferencias; 0 errores; reimpresión de **sólo lectura** (`REPRINT_ROLLED_BACK yes`, los sellos de las 7 notas no cambiaron); Caddy, CRM, enrutamiento y crontab intactos. Log `backups/d-r131-go.log`. Vuelta atrás preparada: `backups/release-r131/rollback_r131_code.sh` (a `release-f47bd47`). |
| «GO cron cotizaciones prod-02» | 18:06–18:07 | **DETENIDO por una guarda, nada escrito.** `--dry-run`: 0 cotizaciones por vencer. El kit esperaba `C=` sin `-f deploy/compose.crm-ingress.yml` y el crontab real lo lleva. Kit **v2** corregido (sólo esa constante; ensayo con crontab falso que ya usa el `C=` real: 33/33). Log `backups/go2-cron-quotes.log`. **Espera reconfirmación: «GO cron cotizaciones prod-02 kit v2».** |
| «GO normalización catálogo prod-02» | 18:30 | **DETENIDO por un fallo del kit, base sin tocar.** Sólo se tomó el backup `db-20261124-183009`. `backup()` de `r13ops_env.sh` terminaba en `[ -n "$R13OPS_REHEARSAL" ] && …`: en producción devolvía 1 y `set -e` paraba el GO (los ensayos siempre corrían en modo copia). Kit **v131b** corregido con un `if`, probado con stubs (el viejo sale con rc=1, el nuevo sigue), MANIFEST regenerado. Log `backups/f11p-go-r131.log`. **Espera reconfirmación: «GO normalización catálogo prod-02 kit v131b».** |
| «GO muestras 167 prod-02» | — | **No ejecutado** (mismo `backup()`). **Espera «GO muestras 167 prod-02 kit v131b».** |
| «GO producto 12 prod-02» | — | **No ejecutado** (mismo `backup()`). **Espera «GO producto 12 prod-02 kit v131b».** |

Antes del intento, los tres kits de datos se reapuntaron a R13.1 (sólo SHA e imágenes; huellas de alcance sin tocar) y se reensayaron sobre una copia con las imágenes `release-bf6a330`: aplicar, repetir sin cambios, rechazo con huella equivocada, vuelta atrás y reaplicar (`backups/release-r13-ops/r131ops-rehearsal.log`). Las huellas de alcance de producción coinciden con las fijadas.

### Siete notas de venta (`pdf_generated_at`) — histórico; ver el kit nuevo arriba
- Kit `backups/release-r13-ops/notes7_go.sh` (fuente `140538`) — **SUSTITUIDO** el 2026-11-25 por `release-notes7b`. No usar.
- **Hallazgo:** la reimpresión de los kits escribió en producción tres veces: R12b (2026-11-21), Fase 1 (2026-11-22) y R13 (2026-11-24). El backup `db-20261124-140538` guarda los sellos de la reimpresión de Fase 1. Los sellos reales de los usuarios están en `db-20261121-222417` (coinciden con su última descarga auditada; las notas 5 y 7 nunca se descargaron: vacío). Usar esa fuente exige refijar el manifiesto y reensayar.
- 2026-11-25: el propietario eligió `db-20261121-222417`.

## RELEASE R13.2 — candidata `c8a43215` PREPARADA Y ENSAYADA · espera «GO despliegue c8a4321 prod-02»

- Contenido: #426, #427, #428 y #430 (más docs #425/#429). Sin migraciones; sin cambios en `deploy/` ni compose.
- Imágenes `rc-c8a4321`: backend `sha256:211010ebf1c9…`, frontend `sha256:085254fd4e4c…` (compiladas en prod-02 bajo el candado).
- Ensayo sobre copia aislada de `db-20261125-030242`: huellas idénticas (139 tablas, 3416 filas); humo 32/32; 319 pruebas en la imagen; simulación **19/19** con copia idéntica después; exportaciones nuevas (ocultas y completas) con los encabezados de las plantillas; usuario sólo `inventory.view` → 403 en la completa e IMEI ocultos en «Equipos con serie»; vuelta atrás a `release-bf6a330` correcta.
- `--preflight` del despliegue y de la vuelta atrás contra producción: OK, sin escribir nada.
- Kit `backups/release-r132/` (`d_deploy_r132.sh`, `rollback_r132_code.sh`, `RUNBOOK-c8a4321-prod02.md`, con el aviso al personal sobre los IMEI ocultos).
- **Orden recomendado:** primero los GO de datos sobre R13.1 (sus kits fijan `bf6a3307`), después R13.2. El kit del cron v2 no depende de esto.

## Desarrollo fusionado tras R13.1 (en `master`, NO desplegado)
- #426 `01895791`: «Precio de venta» opcional en la plantilla de nuevos; nunca se aplica solo; diferencia, impacto y propuesta; actualizar exige `products.manage` + confirmación; vista previa desfasada → 409; el costo nunca es precio; orden de bloqueos estante → producto como las ventas.
- #427 `e449d7b8`: cada seminuevo se bloquea sólo a sí mismo; caja, pago y entrega comparten `sale_refusal`; quitar el precio libera el apartado sin pedido y hace fallar el pedido ya creado.
- #428 `b8610b07`: exportaciones con formato de plantilla (revisión con IMEI ocultos para `inventory.view`; completos con `inventory.adjust`); las exportaciones de equipos existentes ocultan IMEI a quien no tiene `inventory.adjust`; un archivo oculto nunca se carga.
- Desplegarlos requiere una release nueva (R13.2), preparada y ensayada aparte, y su GO.

### Preguntas abiertas del propietario
- Plantilla combinada «Equipos»: ¿proponer precio también ahí? · Stock en la propuesta: ¿toda la empresa para `products.manage`? · ¿Acción de auditoría propia para cambios de precio por carga?
- ¿`inventory.adjust` basta para IMEI completos o `inventory.labels.sensitive`? · ¿Ocultar IMEI también en la tabla «Inventario › Equipos» y en `/api/admin/inventory/units/` para quien sólo consulta?
- Seminuevos: quitar el precio no despublica; seminuevos sin modelo por cantidad no exigen publicación/foto y la tienda muestra stock bruto; la caja dice «sin stock» si todas las unidades están sin precio; una unidad despublicada con pedido ya creado se vende a ese pedido.
- `deploy/crontab.example` no refleja el `-f deploy/compose.crm-ingress.yml` que usa el crontab real.

### R13 (histórico)

Desplegada el 2026-11-24 entre las 14:05 y las 14:07 (Lima) con el GO exacto del propietario («GO despliegue f47bd47 prod-02»). Detalle en la sección «RELEASE R13» más abajo.

Comprobado justo después:
- backend y frontend «healthy»;
- huella del enrutamiento y de Caddy sin cambios; 9 contenedores del CRM;
- el candado de producción libre.

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

## POST-R13 — preparación del inventario real (2026-11-24) · todo en `master` `bf6a3307`, **nada desplegado salvo R13**

**Fusionado** (cada PR con revisión independiente, sus correcciones y CI verde sobre el SHA exacto):
- #419 docs del despliegue de R13 (`2fcd38ab`).
- #421 STOCK-REAL-READY (`d0da5591`): auditoría de los importadores, simulación de 13 escenarios como prueba (`store.test_stock_real_simulation`) y guía en `docs/carga-stock-real.md`. Ningún campo pedido por el propietario necesitó columna nueva ni migración.
- #422 recorrido real del empleado (`c839e5db`): la caja rechaza al escanear un equipo de variante en borrador (409 `variant_pending_draft`), foco tras elegir producto, textos de recepción, cortes en 390 px, estado web fresco en la ficha del seminuevo.
- #423 tabla de seminuevos (`c0088c63`): «Editar» en la primera columna, tarjetas en móvil, aviso de variante en borrador al recibir; E2E de recepción corregido para 390 px.
- #420 cotizaciones (`bf6a3307`): `expire_sales_quotes --dry-run`, `previous_status` en la auditoría, bloque del `healthcheck.sh` que sólo vigila el log si existe, §6.1.3e de `docs/despliegue-produccion.md` (incluida la nota del minuto :41).

**Simulación del stock real: 13/13** en tres sitios: CI, base local y una copia aislada de producción con la imagen `release-f47bd47` (`backups/release-r13-ops/r13ops-simstock.log`; copia idéntica después: 139 tablas, 3416 filas). Repetida con la candidata R13.1: 13/13.

**Plantillas** (generadas por el código real; para la carga real se descargan del panel, «Inventario › Más herramientas › Carga de equipos»):
- «Stock de equipos nuevos»: Sucursal, Modelo, Capacidad, Color, SIM, Versión, IMEI 1, IMEI 2, Serie, Proveedor, Costo, Fecha de ingreso, Observaciones.
- «Stock de seminuevos»: lo anterior más Precio de venta, Estado estético, Salud de batería (%), Estado funcional, Defectos, Accesorios, Garantía (días) y Detalles para el comprador.
- Inspección, fotos y publicación se hacen en el panel, nunca desde Excel (regla del propietario).
- Exportación previa para conciliar: «Carga de stock › Exportar › Equipos con serie».

**Stock actual en producción** (lectura, 2026-11-24): 202 unidades en 11 filas (172 en Almacén, 30 en Tienda principal), casi todo accesorios. Sólo 2 equipos con serie: 1 seminuevo pendiente de inspección (IMEI ****3699) y 1 dado de baja (****7982, conserva su IMEI y no se puede volver a cargar con él).

### Kits listos, cada uno con su GO propio (ninguno ejecutado)

| GO exacto | Kit (servidor, `backups/`) | Ensayo |
|---|---|---|
| «GO despliegue bf6a330 prod-02» | `release-r131/d_deploy_r131.sh` (sólo código, sin migraciones; vuelta atrás `rollback_r131_code.sh`) | copia `db-20261124-170751`: huellas idénticas, humo 32/32, 284 pruebas en la imagen, simulación 13/13, vuelta atrás a `release-f47bd47` OK |
| «GO cron cotizaciones prod-02» | `release-r13-ops/cron_expire_quotes_install.sh` (retirada `cron_expire_quotes_remove.sh`) | ensayo local con crontab falso 33/33 |
| «GO normalización catálogo prod-02» | `release-r13-ops/f11p_go.sh` | copia `db-20261124-145438`: aplicar, repetir sin cambios, rechazo por huella, vuelta atrás, reaplicar |
| «GO muestras 167 prod-02» | `release-r13-ops/f12s_go.sh` (las 38 dudosas excluidas y comprobadas en la base) | ídem |
| «GO producto 12 prod-02» | `release-r13-ops/p12_go.sh` | ídem |
| «GO carga stock real prod-02» | archivos reales del propietario + `docs/carga-stock-real.md` | simulación 13/13 |

Runbooks: `release-r131/RUNBOOK-bf6a330-prod02.md`, `release-r13-ops/RUNBOOK-r13-ops-prod02.md`, `release-r13-ops/RUNBOOK-cron-expire-quotes.md`. Los kits anteriores de normalización, muestras y producto 12 (para `16189e4`) quedan con el sufijo `.v16189e4` y no se usan.

### Hallazgo: la reimpresión histórica del kit de R13 escribe en la base

`historical_reprint.py` sella `SalesNote.pdf_generated_at` al generar cada nota. El despliegue de R13 lo ejecutó en producción: las 7 notas de venta tienen ese sello reescrito al 2026-11-24. Nada más cambió. El valor anterior está en el backup `db-20261124-140538`. El kit de R13.1 usa `historical_reprint_ro.py`, que deshace siempre la transacción (ensayado: 0 filas cambiadas). Restaurar los sellos antiguos sería una escritura en producción y pide GO.

### Decisiones pendientes del propietario
- ¿Columna «Precio de venta» en la plantilla de nuevos? Hoy un equipo nuevo se vende al precio de su variante.
- Un seminuevo sin precio impide habilitar la venta de su variante y así bloquea a sus hermanos ya inspeccionados.
- Exportación con IMEI enmascarado y en el formato de las plantillas nuevas (hoy no existe).

## VARIANT-SAFE-01 — rama `feat/variant-safe-01` (migración `0188`, sobre `0187` ya en master); PR abierto, sin fusionar ni desplegar

- Fusionar con CI verde sobre el HEAD exacto; nada desplegado.
- Decisiones por confirmar con el propietario: las imágenes de la galería bloquean la eliminación (no se borran con la variante); los códigos de barras sí se borran con ella (quedan en la auditoría); un carrito con la variante bloquea; desarchivar la deja inactiva; eliminar/archivar sólo para variantes (producto con modelo).
- Despliegue: la migración no cambia SQL de las FK (Django aplica `on_delete`); añade 3 columnas, 1 CHECK y la capacidad `products.delete`. Con `PGOPTIONS='-c lock_timeout=5s -c statement_timeout=30s'`.

## RELEASE R13 — `f47bd477` **DESPLEGADA en prod-02** el 2026-11-24 (14:05–14:07, Lima)

**Ejecución** (`backups/release-r13/d_deploy_r13.sh`, bajo `prod_lock.sh`; log `backups/d-r13-go.log`):
- Backup previo verificado: `backups/db-20261124-140538.sql.gz` (esquema `0186`). Es el punto de restauración.
- `migrate --plan` fue exactamente `0187`–`0192`; migró en 31 s, antes de cambiar el código, sin esperas de candado.
- Sólo se recrearon backend y frontend. Caddy, el CRM y el crontab no se tocaron.
- Huellas antes y después **idénticas**: productos, stock (11 filas, 202 unidades), Kardex (46), equipos, pedidos, notas de venta, modelos, opciones y textos legales. 0 comprobantes fiscales.
- Seminuevo existente (1): sigue disponible, con su IMEI y su precio; queda «pendiente de inspección» y deja de ofrecerse en la web (1 → 0). En la web el modelo aparece como agotado, sin unidades.
- `unit_photo_report`: 1 foto esperando confirmación; 0 publicados saliendo de la web.
- `report_image_health`: 40 enlaces externos, 38 públicas sin licencia verificada, 2 variantes ocultas por la regla de fotos.
- `check_unit_stock`: 0 diferencias. Rutas de la tienda, del panel y de la API en 200. `healthcheck.sh` en verde. Reimpresión histórica sin errores. 0 tickets de cotización. 0 errores desde el cambio.
- Desde fuera (Cloudflare): la portada devolvió un 522 en la primera petición tras el arranque en frío; las siguientes, 200 en menos de 1 s.

**Vuelta atrás:** `rollback_r13_code.sh` (sólo código; antes, `cancel_sales_quote_print_jobs --apply`). La reversión del esquema sólo con el `DELETE` manual de `docs/despliegue-produccion.md` §7 y aceptando la pérdida de datos listada abajo.

**Riesgos residuales:**
- El seminuevo existente no se vende por la web ni por caja hasta que alguien lo inspeccione, confirme su foto y lo publique. Avisar al equipo.
- Cotizaciones vencidas: sin el cron `expire_sales_quotes` el panel ya las muestra vencidas y no deja convertirlas, pero los reportes no se ordenan.
- Vuelta atrás del esquema: sólo tras el `DELETE` manual de los tickets de cotización, y pierde cotizaciones, parte de pago, archivo de variantes, margen, inspección y confirmación de fotos.
- Fotos: 40 enlaces externos y 38 fotos públicas sin licencia verificada siguen en el catálogo; 2 variantes quedan como «Imagen pendiente». La experiencia visual tipo Apple Store no está terminada.
- El 522 de la primera petición tras reiniciar el frontend puede repetirse en cada despliegue (arranque en frío). No afectó a las siguientes peticiones.
- Un seminuevo rechazado entre el pago web y su confirmación necesita reembolso manual (deuda conocida).

**Pendiente, cada paso con su GO propio:**
- instalar en el cron la línea `expire_sales_quotes` (hoy no está);
- inspeccionar, confirmar la foto y publicar el seminuevo;
- «GO carga stock real prod-02», con los archivos reales;
- volver a preparar los kits de normalización, muestras y producto 12 sobre la base nueva.

### Preparación y ensayo (histórico)

Antes del despliegue, producción estaba en `16189e4`, con esquema `0186_label_printing`.

**Candidata:** `master` `f47bd4772b1ec994295c3562d85a87c84777286d`. Contiene las olas 1, 2 y 3 del plan comercial y las guardas de reversión (#417).
- Migraciones: `0187_used_unit_readiness`, `0188_variant_safe`, `0189_sales_quote`, `0190_supplier_margin`, `0191_trade_in_quote` y `0192_unit_photo_provenance`. Son lineales y aditivas.
- La única diferencia bajo `deploy/` es `crontab.example`, que trae la línea `expire_sales_quotes`. Caddy y el enrutamiento del CRM son idénticos.
- CI del #417 en verde sobre el SHA exacto. Revisión independiente con base real: sin hallazgos bloqueantes.

**Imágenes candidatas** compiladas en prod-02 desde el SHA exacto:
- `blackdog-backend:rc-f47bd47` = `sha256:5d6e8959ccd5…`
- `blackdog-frontend:rc-f47bd47` = `sha256:8892639fe189…`

**Ensayo integral sobre una copia aislada** (`db-20261124-110321`, sin salida a internet; todo bajo `prod_lock.sh`):
- **Migraciones:** el plan son exactamente las seis, unos 29 s en un solo contenedor, sin esperas de candado.
- **Datos históricos idénticos:** productos, stock (11 filas, 202 unidades), equipos con IMEI, serie, precio y costo, fotos, pedidos, pagos, notas de venta, movimientos y auditoría.
- **Permisos nuevos:**
  - Administrador: `products.delete`, `inventory.units.inspect` y `sales.quotes.*`.
  - Ventas y venta-servicio-técnico-inventario: `sales.quotes.view` y `sales.quotes.manage`.
- **Seminuevos existentes:** hay 1. Deja la web y la caja hasta que alguien lo inspeccione, confirme su foto y lo publique. Sigue en stock, con el mismo IMEI y el mismo precio.
- **Pruebas sobre la copia:**
  - humo profundo 32/32 con la imagen nueva y con la anterior sobre el esquema nuevo;
  - reimpresión histórica sin errores;
  - **20/20 pruebas funcionales**: recepción en caja, stock inicial en dos tandas sin compras, seminuevos, variantes, precio sugerido, cotizaciones con parte de pago, fotos reales por unidad y aislamiento con una segunda empresa;
  - **441 pruebas** de 12 módulos dentro de la imagen;
  - rutas del frontend en 200.
- **Fotos:** el informe encuentra 40 enlaces externos y 38 fotos públicas sin licencia verificada; 2 variantes pasan a «Imagen pendiente».
- **Vuelta atrás sólo de código** (`rollback_r13_code.sh`): funciona, pero antes hay que cancelar los tickets de cotización con `cancel_sales_quote_print_jobs --apply`. Después, el código anterior vuelve a ofrecer los seminuevos sólo por precio.
- **Reversión del esquema:**
  - **Con tickets de cotización:** se niega en la `0192` sin deshacer nada (copia idéntica). Si la vuelta atrás se detuvo a medio camino, se niega en la `0190`.
  - **Sin tickets:** tras el `DELETE` documentado, que nunca es automático en producción, se completa en 44 s.
  - Se pierden las cotizaciones, la parte de pago, el archivo de variantes, el margen, la inspección y publicación de los seminuevos y la confirmación de fotos. Lo demás queda idéntico.

**Kit** en `backups/release-r13/` del servidor (y una copia local):
- `d_deploy_r13.sh`, fijado al SHA y a las imágenes, con todas las guardas y siempre bajo el candado;
- `rollback_r13_code.sh`;
- `RUNBOOK-f47bd47-prod02.md`: pasos, conteos esperados, comprobaciones posteriores, vuelta atrás y tareas del propietario.

**GO recibido:** «GO despliegue f47bd47 prod-02» (2026-11-24).

Después del despliegue, cada uno con su GO propio:
- instalar en el cron la línea `expire_sales_quotes`;
- inspeccionar, confirmar fotos y publicar los seminuevos;
- «GO carga stock real prod-02», con los archivos reales y las plantillas de `docs/carga-stock-real.md`;
- volver a preparar y ensayar sobre la nueva base los kits pendientes: normalización, muestras y producto 12.

## PLAN MAESTRO COMERCIAL 2026 — Olas 2 y 3 cerradas en `master` (desplegadas con R13 el 2026-11-24)

Estas olas están en producción desde el despliegue de R13 (`f47bd477`, 2026-11-24).

**Ola 2, fusionada** (cada PR con revisión independiente, sus correcciones y CI verde sobre el SHA exacto):

| PR | Merge | Qué resuelve | Migración |
|---|---|---|---|
| #404 NAV-SALES-01 | `aaffd0ea` | Menú por tareas: Productos, Proveedores, Inventario y Ventas. «Venta rápida de equipo» lleva al bloque de equipos de la caja. | no |
| #405 SUPPLY-WEEKLY-01 | `337d771e` | Panel semanal de proveedores con diferencias contra la lista anterior y ofertas no mencionadas. En Excel, una combinación válida sin variante ofrece «Crear variante». | no |
| #407 VARIANT-SAFE-01 | `91c35fb0` | Archivar y eliminar variantes de forma segura: vista previa de impacto, motivo, nombre escrito y permiso `products.delete`. Las FK pasan a PROTECT y una prueba guardiana revisa todas las relaciones. | `0188_variant_safe` |
| #409 INV-LOAD-02 | `0fe0bab9` | **Plantillas de carga de stock real** (equipos nuevos y seminuevos). Conciliación del stock con las unidades, anulación de una carga completa, informe antes y después y guía `docs/carga-stock-real.md`. | no |
| #411 CATALOG-ADMIN-UX-B | `6169f546` | Editor de catálogo: búsqueda y filtros de modelos, historial agrupado con número fijo de consultas, «Crear variante» por línea, precio y publicación por fila con control de cambios simultáneos, exclusiones editables. | no |
| #413 SUPPLY-MARGIN-01 | `db07411f` | Precio sugerido igual al costo más el margen de la empresa, visible sólo con `costs.view`. Nunca cambia precios publicados. | `0190_supplier_margin` |

**Ola 3, fusionada:**

| PR | Merge | Qué resuelve | Migración |
|---|---|---|---|
| #408 IMG-GATE-01 | `4d95e1a9` | Una sola regla de fotos para las variantes de equipos: sólo fotos con licencia verificada del mismo color, nunca enlaces externos. Incluye el informe de salud de imágenes. | no |
| #410 SALES-QUOTE-01 | `5c76538e` | Cotizaciones comerciales: revisiones, aprobación, A4, ticket de 80 mm y ESC/POS, y conversión idempotente en venta. No mueven stock ni cobran. | `0189_sales_quote` |
| #414 TRADE-IN-QUOTE-00 | `bba6d5c1` | Parte de pago **referencial** dentro de la cotización. Con el ejemplo 4500 − 1200 = 3300, la venta se cobra completa. Sólo guarda los últimos 4 dígitos del IMEI. | `0191_trade_in_quote` |
| #415 UNIT-PHOTO-02 | `4ca07c4e` | Fotos reales de cada seminuevo, confirmadas por quien lo inspecciona. Sólo las confirmadas se publican; el carrito y los pedidos muestran la foto de esa unidad. | `0192_unit_photo_provenance` |

También: #406 y #412 (pruebas E2E ajustadas a la ola 1, `3435a068`).

**Prioridad del propietario: carga del stock real.** Está lista en código y probada con datos sintéticos (`SyntheticStockLoadTest`):
- plantillas «Stock de equipos nuevos» y «Stock de seminuevos»;
- inventario inicial por tandas sin compras ficticias;
- vista previa con la simulación del stock antes y después;
- conciliación del stock con las unidades y anulación de una carga completa.

**STOCK-REAL-READY** (rama `feat/stock-real-ready`, PR abierto): auditoría de las plantillas y simulación completa
(`store.test_stock_real_simulation`, 13 escenarios). Sin migraciones. Necesita una decisión del propietario: ¿lleva
precio la plantilla de equipos nuevos?

La venta rápida en caja (#400) ofrece primero el pedido pendiente. Cargar el stock real en producción necesita los archivos reales y el «GO carga stock real prod-02» (`docs/carga-stock-real.md` §11). Antes hay que desplegar la release.

**Experiencia visual tipo Apple Store: NO terminada.** El configurador, la regla de fotos y la licencia están hechos. **Faltan las fotografías autorizadas** de los modelos y acabados (0 verificadas) y las fotos confirmadas de los seminuevos. Sin ellas, la tienda muestra «Imagen pendiente» o «Sin fotos de este equipo».

**Pendiente (bloqueado por decisión del propietario o un ADR):**
- quitar fondos (falta aprobar la herramienta);
- bajo pedido de una combinación nueva (D-F-1);
- cobro al llegar el equipo;
- estado «declarado por el proveedor» (C1);
- liquidación real de la parte de pago (D1–D12, con el contador);
- deuda E2E-HARNESS (ver `AGENTS.md` §6);
- la prueba `inventory-receive` 9: el foco en móvil a 390 px.

Siguen abiertos fuera del alcance: #249, la propuesta de orden de candados entre caja y pago web.

**Decisiones del propietario abiertas en las olas 2 y 3**, cada una detallada en su PR:
- quién borra variantes y qué lo bloquea;
- precio de venta en la plantilla de nuevos (hoy fuera, porque cambiaría el precio de toda la variante);
- la opción «Ambos» en la carga;
- las 7 decisiones de cotizaciones;
- las 6 de parte de pago;
- las 6 de fotos de seminuevos;
- los productos sin modelo, que no tienen control de licencia;
- el margen (múltiplos y redondeo hacia arriba).

## PLAN MAESTRO COMERCIAL 2026 — Ola 1 cerrada en `master` (sin desplegar)

El plan, la auditoría y el orden de los PR están en `docs/plan-maestro-comercial-2026.md`. **Nada de esto está en producción**: desplegarlo requiere ensayo y un GO propio.

**Ola 1, fusionada** (cada PR con revisión independiente, sus correcciones y CI verde sobre el SHA exacto):

| PR | Merge | Qué resuelve | Migración |
|---|---|---|---|
| #400 POS-ORDER-ARRIVAL-01 | `1ce08caa` | «Llegó el equipo» ofrece primero el pedido pagado pendiente de esa variante. Si se ingresa como stock habiendo un pedido esperando, devuelve 409 salvo confirmación con motivo (auditada). El candado sobre las líneas evita una carrera entre dos cajas. | no |
| #401 INV-LOAD-01 | `ca719bde` | Inventario inicial por tandas, sin compras ficticias. La carga y «Registrar equipo» aceptan proveedor, fecha real de ingreso y notas. La vista previa simula el stock antes y después y avisa de una serie repetida en otra variante. Bloqueos en un orden único; un bloqueo cruzado responde 409 «busy». | no |
| #402 SUPPLY-GUARDS-01 | `b47c2da1` | Las ofertas de proveedores inactivos dejan de verse vigentes. Un precio por debajo del costo exige confirmación y motivo, auditados dentro del registro de la oferta; quien no ve costos no puede deducirlo. «Registrar artículo» rechaza ofertas por debajo del costo. | no |
| #403 USED-READY-01 | `40be4fe7` | Seminuevos con inspección, publicación explícita, garantía (la de la empresa o la de la unidad), estado funcional, defectos y accesorios. La web sólo ofrece una unidad inspeccionada, publicada, con precio y al menos una foto. La caja, la entrega de pedidos y el cobro rechazan una unidad sin inspección aprobada. | `0187_used_unit_readiness` |

**Efecto de #403 al desplegar:** todos los seminuevos actuales quedan sin publicar y con la inspección pendiente. No se pueden vender en caja ni en la web hasta que alguien los inspeccione, y en la web, además, los publique con al menos una foto. El ensayo de la release debe contar:
- las unidades que quedan sin vender;
- las visibles hoy en la web;
- los pedidos pagados con un seminuevo pendiente de entrega;
- las reservas web en curso.

**En curso (ola 2 y adelantos de la ola 3):**
- #404 NAV-SALES-01 — FUSIONADO `aaffd0ea`: menú por tareas (Productos, Proveedores, Inventario, Ventas).
- #405 SUPPLY-WEEKLY-01 — FUSIONADO `337d771e`: panel semanal de proveedores, con diferencias y ofertas no mencionadas.
- VARIANT-SAFE-01 (`0188`): archivar y eliminar variantes de forma segura; las FK pasan a PROTECT.
- SALES-QUOTE-01: cotizaciones (su migración se numera al fusionar).
- Pasada completa de Playwright sobre `master`.

**Pendiente:**
- SUPPLY-MARGIN-01 (`0189`): precio sugerido con el margen de la empresa.
- INV-LOAD-02 — PR abierto (`feat/inv-load-02`, sin migración): dos plantillas del stock real, `check_unit_stock` y «Conciliación», anulación por lote, informe antes/después y «busy» en la entrada por cantidad. Procedimiento: `docs/carga-stock-real.md`. La carga real en prod-02 pide datos reales y el GO «GO carga stock real prod-02».
- Plantillas Excel de nuevos y seminuevos.
- UNIT-PHOTO-02, IMG-GATE-01 e IMG-HEALTH-01.
- TRADE-IN-QUOTE-00.
- CATALOG-ADMIN-UX-B.
- Release ensayada.

**Deuda anotada:**
- Un seminuevo rechazado entre el checkout y el pago puede cobrarse y exige devolver el dinero a mano. Habría que comprobarlo al crear la sesión de pago.
- La prueba `test_pos_unit_price …ficha_and_the_price_without_cost` falla a veces: un código de etiqueta aleatorio puede contener «1500».

**Decisiones del propietario abiertas en esta ola:**
1. Caja: ¿«Es del pedido #N» también para pedidos de otra sucursal (el equipo se registra en la sucursal del pedido), o sólo los de la misma sucursal?
2. Seminuevos: aprobar la inspección queda sólo para quien administra la empresa. Publicar y fijar la garantía por unidad exige el permiso de precios. «Funciona al 100 %» pasa a ser «Sin fallas declaradas».
3. Proveedores: quien no ve costos debe confirmar cualquier cambio de precio en una oferta con costo, para que no pueda deducirlo.

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
