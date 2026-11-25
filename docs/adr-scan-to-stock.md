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

### 3.3 Sin fuga entre empresas

Las consultas filtran por la empresa del usuario antes de mirar un identificador: un IMEI o un código de otra empresa no
se lee, así que no se puede decir nada de él. El IMEI es único **por empresa** (`unique_stock_unit_imei_per_company`),
así que registrarlo aquí no choca con la otra. Un equipo de otra sucursal de la misma empresa que el usuario no alcanza se
dice «registrado en otra sucursal», sin estado (vendido, de baja), producto ni sucursal. Lo mismo el aviso de una serie
en otra variante y el 409 de un IMEI repetido, **también en la caja** (`pos_device_services._duplicate`, que ahora
recibe a quien registra y no devuelve `unit` fuera de sus sucursales).

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

Un único campo recibe todas las lecturas; terminador Enter, Tab o ambos (preferencia del navegador,
`scan-intake-terminator` en `browser-storage`). **Tab termina una lectura sólo sin modificadores y con un código entero
escrito (4 caracteres o más)**: con el campo vacío o casi vacío, y con Shift+Tab, Tab mueve el foco como siempre. Esc
vacía el campo. Las teclas imprimibles que llegan a un botón, un radio o la página van al campo del lector, y un Enter
ahí con algo escrito termina esa lectura: nunca pulsa «Confirmar ingreso» ni una opción de una pregunta. Cada lectura
pertenece a un equipo, un modelo y una sucursal: si alguno cambia mientras se consulta, la respuesta se descarta y se
dice. Una pregunta abierta no deja pasar otra lectura. En pantalla táctil el campo no abre el teclado del móvil
(`inputMode="none"`) salvo que se pida con «Teclado». La clave de idempotencia sólo cambia tras un rechazo 4xx; tras un
5xx el reintento usa la misma. Mientras se registra, salir de la página se detiene. Las lecturas se atienden en orden (`ReadQueue`). Indicador «Esperando:
IMEI 1 / IMEI 2 / Número de serie». Una lectura repetida se ignora y se dice; Enter con el campo vacío no hace nada;
**ninguna lectura confirma**: sólo el botón, protegido contra doble clic y con clave de idempotencia. Un pedido pagado que
espera la variante en la sucursal se resuelve antes de confirmar (su pedido, o stock con motivo).

## 4. Alternativas descartadas

- **Clasificar en el navegador.** Dos clasificadores divergen; además la consulta de duplicados necesita al servidor.
- **Decidir «lector» por el ritmo para aceptar o rechazar.** Un lector Bluetooth lento o un teclado rápido lo engañan.
- **Un endpoint nuevo de recepción.** Habría duplicado reglas de costo, proveedor, pedidos y variantes.
- **Usar `/api/admin/pos/...`** tal cual: exige `sales.pos.use`, que el almacén no tiene.

## 5. Decisiones abiertas para el propietario

- **D-SCAN-1 · Prefijo de serie en cajas.** Algunos fabricantes codifican la serie de la caja con una letra delante
  (p. ej. «S» + serie). No se quita sola (sería decidir por marca). Propuesta: una tabla de prefijos por fabricante en
  `backend/catalog_reference/` (excepción de AGENTS §2), aplicada sólo si el modelo elegido es de ese fabricante.
- **D-SCAN-2 · Inventario inicial.** Hoy el escaneo registra **compra** (`PURCHASE_ENTRY`), también un seminuevo sin
  proveedor ni costo, igual que «Llegó el equipo» de la caja (se mantiene por coherencia). El inventario inicial sigue
  en la carga masiva. ¿Se quiere también aquí?
- **D-SCAN-3 · EID.** Se reconoce y se rechaza; no hay dónde guardarlo. ¿Se quiere guardar (migración)?
