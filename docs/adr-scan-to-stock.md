# ADR — Registrar un equipo por escaneo (SCAN-TO-STOCK-01)

Estado: implementado en la rama `feat/scan-to-stock-01` (sin migraciones). No desplegado.

## 1. Problema

Un almacenero registra un equipo físico (nuevo o seminuevo) leyendo con un lector de códigos USB o Bluetooth los
códigos de la caja, de la pantalla de identificación del equipo, de la etiqueta comercial y de la etiqueta interna. El
lector es un teclado («keyboard wedge»): escribe el código y termina con Enter o Tab. En una caja hay varios códigos y
no todos son del equipo: IMEI 1, IMEI 2, serie, UPC/EAN (identifica el producto), EID (eSIM), a veces MEID.

## 2. Qué ya existía y se reutiliza (no hay un segundo motor)

| Pieza | Dónde | Uso aquí |
|---|---|---|
| Reglas de IMEI (15 dígitos + Luhn) y de serie | `device_identity.clean_imei` / `clean_serial` | El clasificador las usa para decidir; la recepción las vuelve a aplicar. |
| Un equipo entra sólo por aquí | `stock_unit_services.receive_units` (duplicados por empresa, candados por identificador, Kardex, `label_code`) | Seminuevos. |
| «Llegó el equipo» de la caja | `pos_device_services.receive_once` (variante desde la paleta, pedido pagado que espera, proveedor, costo, idempotencia, lote de etiquetas) | Equipos nuevos, con `origin='scan'`: sólo cambia el texto del Kardex («Inventario: registro por escaneo») y queda en el registro de idempotencia. |
| Código comercial ≠ identificador de equipo | `unit_codes.product_code_holding`, `_refuse_a_product_code` (LABEL-01/02) | Un UPC/EAN de la empresa elige la variante; nunca se guarda en un equipo. |
| Etiqueta interna del equipo | `StockUnit.label_code` + `label_batches` + agente de impresión (LABEL-02/04) | Ya se asigna en cada ingreso; la pantalla ofrece el lote de etiquetas al terminar. |
| Buscar un equipo por IMEI, serie o etiqueta en caja | `unit_identification.find_unit` / `sellable_unit` | Un equipo registrado por escaneo se vende escaneándolo (probado). |

## 3. Decisión

### 3.1 Clasificador puro (`backend/store/scan_classifier.py`)

`classify(code) -> Reading(kind, value, certain, alternatives, hint, problem, detail)`. Sin base de datos, sin reloj.

- **Nunca por el ritmo de las teclas.** El ritmo sólo dice cómo llegó (lector o a mano, regla de SERIAL-PICK), y se
  guarda como `method`; no decide qué es un código.
- **Sin fabricantes.** Formas internacionales: IMEI 15 dígitos + Luhn (3GPP), EID 32 dígitos (GSMA), MEID 14 hex o
  18 decimales (3GPP2), IMEISV 16 dígitos, GTIN 8/12/13/14 con dígito GS1. Un test comprueba que el módulo no nombra
  ninguna marca. **Nunca se deduce el modelo del IMEI** (no se lee el TAC).
- **Un EID o un MEID nunca es un IMEI**, tampoco «un IMEI sin su último dígito»: `imei` nunca es alternativa de un
  código que no lo es. 15 dígitos que no pasan Luhn no son un IMEI: se pregunta si es la serie.
- **15 dígitos con Luhn son un IMEI seguro sólo si se espera un IMEI** (la pantalla manda `expected`: `imei` o `imei2`)
  o si el rótulo dice «IMEI». Si no, `certain=false` con `imei` y `serial`: una serie numérica de cada diez también
  pasa Luhn.
- **14 dígitos con dígito GS1 son primero un GTIN-14** (`certain=false`, alternativas `gtin` y `serial`; el texto
  menciona que podría ser un MEID). Sin dígito GS1, un MEID. Nunca un IMEI.
- **Un rótulo no salta las reglas:** «EID: …» debe tener 32 dígitos; «MEID: …», 14 hex o 18 dígitos. «S/N: …» con
  espacios u otro rótulo dentro (un QR de varios campos) no es una serie: `not_a_code`.
- **Si no es seguro, se pregunta.** `certain=False` + `alternatives` (`imei`, `serial`, `gtin`); la pantalla muestra
  los botones y el servidor vuelve a validar la elección (`as_identifier`).
- **Datos, no ramas.** Los rótulos que puede traer un QR o un texto («IMEI2: …», «EID …», «S/N: …») son una tabla
  (`LABEL_HINTS`). El rótulo exige separador (`:`, `#`, `=` o espacio).

### 3.2 Superficie HTTP (`backend/store/scan_intake_views.py`)

| Método y ruta | Qué hace | Autoridad |
|---|---|---|
| `POST /api/admin/inventory/scan/check/` | Clasifica y busca **sólo en la empresa** del usuario: equipo que ya tiene el identificador (cualquier estado salvo anulado), producto cuyo código comercial es. | `inventory.adjust` |
| `GET /api/admin/inventory/scan/models/?q=&branch=` | Búsqueda de modelos de la caja. | `inventory.adjust` + sucursal propia |
| `GET /api/admin/inventory/scan/models/<id>/configurations/?branch=` | Selector de la caja (paleta, variantes, pedidos que esperan) + `used_variants`. | ídem |
| `POST /api/admin/inventory/scan/receive/?branch=` + `Idempotency-Key` | `condition=new` → `pos_device_services.receive_once(origin='scan')`; `condition=used` → `scan_intake.receive_used` (`receive_units`). | ídem; lo demás por `authority_for` |

El código viaja en el cuerpo, nunca en la URL (los IMEI no van a los registros de acceso). El almacén no necesita
`sales.pos.use`: la autoridad es la del inventario.

### 3.2.1 La casilla de la serie

En la puerta del escaneo la serie nunca recibe otro identificador (`scan_intake.refuse_serial_that_is_not_one`,
400 `serial_not_a_serial`): un EID, un código comercial seguro o el IMEI del mismo equipo, siempre; 15 dígitos con
Luhn, un IMEISV o un MEID, salvo que la persona haya respondido «Es la serie» al preguntarle (`serial_confirmed`).

Diferencia consciente con otras puertas (revisión H3): la caja («Llegó el equipo»), «Registrar equipos», la recepción
y la carga masiva **aceptan** un equipo cuya serie sea igual a su propio IMEI (es el mismo equipo y la búsqueda lo
encuentra igual); sólo la puerta del escaneo lo rechaza, porque allí una lectura puede caer en la casilla equivocada.
No se cambia el comportamiento de esas puertas.

### 3.2.2 Un IMEI y una serie nunca nombran dos equipos (todas las puertas)

`stock_unit_services._already_in_stock` (por donde pasan la recepción, la carga masiva, la caja y el escaneo) y
`pos_device_services._duplicate` rechazan una **serie igual al IMEI 1 o 2** de un equipo vivo de la empresa, y un
**IMEI igual a la serie** de un equipo vivo. Misma ocupación que el IMEI: vendido y dado de baja ocupan; anulado
libera. Motivo: la caja y toda búsqueda leen primero un código con forma de IMEI como IMEI (`find_unit`), así que un
equipo cuya serie fuera el IMEI de otro sería inalcanzable o confundible. Regla mínima: sólo igualdad exacta del valor
guardado (las series se guardan en mayúsculas y los IMEI en dígitos). El candado por identificador también encola una
serie con el mismo valor recibido como IMEI. Vale también **dentro de una misma carga, recepción o archivo** (revisión
H1): la serie de una fila nunca es el IMEI de otra, ni al revés (`receive_units`, `receipt_services._check_unit`, la
vista previa de la carga de equipos). La ocupación de un lote se lee una sola vez (`stock_unit_services._Occupancy`: dos
consultas para todo el lote, no hasta seis por fila dentro de la transacción con candados).

Después del ingreso, el IMEI 1, el IMEI 2 y la serie de un equipo **no se editan** (revisión L5): «Corregir» cambia
detalles (`update_unit`), «Anular ingreso» los libera, «Cambiar variante» los conserva y una corrección vuelve a entrar
por `receive_units`. Así ninguna puerta escribe un identificador sin el candado y la ocupación de arriba; un test fija
que el PATCH del equipo los ignora.

### 3.3 Sin fuga entre empresas

Las consultas filtran por la empresa del usuario antes de mirar un identificador: un IMEI o un código de otra empresa no
se lee, así que no se puede decir nada de él. El IMEI es único **por empresa** (`unique_stock_unit_imei_per_company`),
así que registrarlo aquí no choca con la otra. Un equipo de otra sucursal de la misma empresa que el usuario no alcanza se
dice «registrado en otra sucursal», sin estado (vendido, de baja), producto ni sucursal. Lo mismo el aviso de una serie
en otra variante, el conflicto con un código interno («ya existe en la empresa») y el 409 de un IMEI repetido,
**también en la caja** (`pos_device_services._duplicate`, que recibe a quien registra y no devuelve `unit`).
**Sólo se oculta a quien está limitado a algunas sucursales** (Membership SELECTED): quien tiene alcance de toda la
empresa (`has_company_wide_scope`: maestro de plataforma, puente legacy, Membership ALL) ve el detalle completo, también
de un equipo en una sucursal desactivada (`pos_device_services.reachable_branch_ids`).

### 3.4 Estado de un identificador ya usado

Igual que la recepción (`_already_in_stock`): **vendido** y **dado de baja** siguen ocupando el IMEI y la serie;
**anulado** los libera (docs/carga-stock-real.md §3). La pantalla lo dice con su motivo (devolución del pedido, ficha
del equipo).

### 3.5 Seminuevos

Sólo a una variante de seminuevos existente, controlada por serie, activa y no archivada. Un pedido pagado que espera
esa variante en la sucursal se presenta antes (409 `pending_order_line`, la regla de POS-ORDER-ARRIVAL-01); entrar como
stock pide un motivo y se audita. Entregarlo a su pedido no se hace desde aquí (400 `used_order_line`): la ficha del
seminuevo se perdería; se recibe desde el pedido. Entra **pendiente de
inspección y sin publicar** (como toda puerta). Precio propio y garantía sólo con `products.manage` (`prices_forbidden`,
regla #432 `may_set_warranty`), costo con `costs.view`. Un seminuevo sin inspección aprobada no se ofrece en caja (probado).
Fotos reales, confirmación de fotos e inspección siguen en «Inventario › Seminuevos».

### 3.6 Pantalla (`frontend/app/admin/inventory/scan`)

Un único campo recibe todas las lecturas; el terminador se elige por navegador (`scan-intake-terminator` en
`browser-storage`): «Enter» o «Tab».

**Regla de las teclas (determinista; el ritmo de las teclas nunca decide nada):**
- **Enter** termina siempre la lectura (con algo escrito). Quien escribe a mano tiene además el botón **«Leer»**.
- **Tab** termina la lectura **sólo** con el terminador «Tab», sin modificadores (Shift+Tab siempre vuelve atrás) y con
  un código entero escrito (4 caracteres o más). Con el terminador «Tab» y un código escrito, el primer Tab lo lee y el
  segundo (campo vacío) sale.
- En cualquier otro caso Tab sale del campo como siempre; si quedaba algo escrito, se descarta y se dice, para que no
  se pegue a la lectura siguiente. Esc vacía el campo. Una preferencia vieja «Enter o Tab» se lee como «Tab».
- Las teclas imprimibles que caen en la página o en un control de **esta pantalla** que no es un campo de texto (un
  botón, un radio, un `select`) van al campo del lector, y un Enter ahí con algo escrito termina esa lectura: nunca
  pulsa «Confirmar ingreso», una opción de una pregunta ni cambia una sucursal o un proveedor. Nunca desde la barra
  lateral o superior, un diálogo, un menú, un combobox, un buscador o una lista; Espacio sobre un botón, enlace, radio o
  casilla los activa como siempre. Cada lectura
pertenece a un equipo, un modelo y una sucursal: si la persona cambia alguno mientras se consulta (limpiar, elegir o
escribir otro modelo, sucursal, nuevo/seminuevo, variante), la respuesta se descarta y se dice. El modelo que elige un
código comercial leído no cuenta como cambio: el IMEI leído justo después sigue valiendo. Una pregunta abierta no deja pasar otra lectura. En pantalla táctil el campo no abre el teclado del móvil
(`inputMode="none"`) salvo que se pida con «Teclado». La clave de idempotencia cambia tras un rechazo 4xx
o si el cuerpo cambió desde el último envío; tras un 5xx sin cambios el reintento usa la misma. Mientras se registra,
salir de la página (recargar, un enlace, «atrás») se detiene; «atrás» devuelve a esta pantalla (una entrada de guarda
que se quita al terminar). Si tras un 5xx el reintento (con otra clave, porque el cuerpo cambió) recibe «ya registrado»
para este mismo identificador, la pantalla busca el equipo y, si es de esta variante y esta sucursal, muestra el
registro del intento anterior en lugar del rechazo (sólo con una variante ya existente: una combinación que todavía no es
variante nunca se da por «el mismo equipo»). Cada envío pone una sola entrada de guarda en el historial; sólo ese envío
la quita al terminar, y desmontar la pantalla nunca navega. Las lecturas se atienden en orden (`ReadQueue`). Indicador «Esperando:
IMEI 1 / IMEI 2 / Número de serie». Una lectura repetida se ignora y se dice; Enter con el campo vacío no hace nada;
**ninguna lectura confirma**: sólo el botón, protegido contra doble clic y con clave de idempotencia. Un pedido pagado que
espera la variante en la sucursal se resuelve antes de confirmar (su pedido, o stock con motivo).

## 4. Alternativas descartadas

- **Clasificar en el navegador.** Dos clasificadores divergen; además la consulta de duplicados necesita al servidor.
- **Decidir «lector» por el ritmo para aceptar o rechazar.** Un lector Bluetooth lento o un teclado rápido lo engañan.
- **Un endpoint nuevo de recepción.** Habría duplicado reglas de costo, proveedor, pedidos y variantes.
- **Usar `/api/admin/pos/...`** tal cual: exige `sales.pos.use`, que el almacén no tiene.

## 5. Decisiones del propietario (2026-11-27)

- **D-SCAN-1 · Sin equivalencias de serie ni de código comercial sin fuente verificada.** Ninguna tabla ni regla
  del código convierte un prefijo de serie en un modelo, un GTIN en una variante o un IMEI en un modelo. Una
  equivalencia así sólo existe si tiene una fuente verificada, o si es un dato que la empresa escribe y activa.
  Revisión del código (master `5acc743e`):
  - **GTIN → variante.** Sólo por los códigos que la empresa escribió (`ProductBarcode`, `unit_codes.product_code_holding`).
  - **IMEI → modelo.** No existe; el TAC nunca se lee (`scan_classifier`, que además tiene un test que impide nombrar
    marcas).
  - **Números de modelo.** Los «A3090» de `backend/catalog_reference/apple-iphone.json` llevan su fuente (`sources`,
    `support.apple.com`) y `verified_at`, y sólo describen el catálogo de referencia: no se leen de una serie.
  - **Sufijos de región** («LL/A», «ZP/A»…) de `inventory-text-stages.ts` e `inventory_ai_payload.py`. Son vocabulario
    para reconocer cómo está escrito un texto; la opción de la empresa sale de sus propias etiquetas y alias (A2).
  - **La única regla sin fuente que había: la «S» de la caja de Apple** (`receive-bulk.ts` `appleBoxSerialFix`,
    `ReceivingUnitsBulk` `SerialPrefixHint` y la casilla «Quitar la «S» inicial»). Su propio comentario decía «NOT
    VERIFIED». Se quitó: una serie queda como se leyó, y quien ve una «S» de más la corrige a mano.

  Pendiente para el propietario: si quiere prefijos de caja, se diseñan como un dato que la empresa escribe y activa
  (fabricante, prefijo, longitud). Eso no existe todavía y probablemente pide una migración.
- **D-SCAN-2 · Inventario inicial.** Sigue abierta: hoy el escaneo registra **compra** (`PURCHASE_ENTRY`), también un
  seminuevo sin proveedor ni costo, igual que «Llegó el equipo» de la caja. El inventario inicial sigue en la carga
  masiva.
- **D-SCAN-3 · EID.** Un EID nunca se guarda como IMEI ni como serie, y no se guarda en ninguna parte: ni `StockUnit`
  ni `Device` tienen un campo para él, y se mantiene así.
  - **IMEI.** Nunca pudo entrar: son 15 dígitos con su dígito de control.
  - **Serie.** La puerta del escaneo ya lo rechazaba (§3.2.1). Las demás puertas (caja, «Registrar equipos», carga
    masiva, recepción, taller) lo aceptaban. Ahora `device_identity.clean_serial` rechaza la forma de un EID
    (32 dígitos, agrupados o no) en todas ellas (PR #451).
  - **MEID.** Una serie que sólo se parece a un MEID (14 hexadecimales, 18 dígitos) se sigue aceptando fuera del
    escaneo: es la forma de series reales, y sólo el escaneo puede preguntar «¿es la serie?».
- **D-SCAN-4 · Espacios del lector.** Lo que un lector o un pegado deja en los **bordes** de un código (espacios,
  tabuladores, CR, LF, NBSP) se quita en todas las puertas. Lo de **dentro** sólo cambia por las reglas que ya
  existían:
  - un IMEI se queda con sus dígitos;
  - una serie pierde sus espacios internos y pasa a mayúsculas;
  - los guiones, puntos y barras de una serie se conservan.

  Fijado puerta por puerta en `test_scanner_whitespace.py`. El lector de lo pegado de «Recibir mercadería» separa
  también con un CR solo (PR #452). Un lector que antepone un espacio sigue la regla del §3.6: con el campo del lector
  vacío, Espacio no se manda al campo. Si las tiendas tienen lectores así, se configuran sin prefijo.
