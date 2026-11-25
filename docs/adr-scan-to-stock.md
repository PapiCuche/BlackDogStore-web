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

### 3.3 Sin fuga entre empresas

Las consultas filtran por la empresa del usuario antes de mirar un identificador: un IMEI o un código de otra empresa no
se lee, así que no se puede decir nada de él. El IMEI es único **por empresa** (`unique_stock_unit_imei_per_company`),
así que registrarlo aquí no choca con la otra. Un equipo de otra sucursal de la misma empresa que el usuario no alcanza se
dice «registrado» sin producto ni sucursal (la recepción lo rechazaría igual).

### 3.4 Estado de un identificador ya usado

Igual que la recepción (`_already_in_stock`): **vendido** y **dado de baja** siguen ocupando el IMEI y la serie;
**anulado** los libera (docs/carga-stock-real.md §3). La pantalla lo dice con su motivo (devolución del pedido, ficha
del equipo).

### 3.5 Seminuevos

Sólo a una variante de seminuevos existente, controlada por serie, activa y no archivada. Entra **pendiente de
inspección y sin publicar** (como toda puerta). Precio propio y garantía sólo con `products.manage` (`prices_forbidden`,
regla #432 `may_set_warranty`), costo con `costs.view`. Un seminuevo sin inspección aprobada no se ofrece en caja (probado).
Fotos reales, confirmación de fotos e inspección siguen en «Inventario › Seminuevos».

### 3.6 Pantalla (`frontend/app/admin/inventory/scan`)

Un único campo recibe todas las lecturas; terminador Enter, Tab o ambos (preferencia del navegador,
`scan-intake-terminator` en `browser-storage`). Las lecturas se atienden en orden (`ReadQueue`). Indicador «Esperando:
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
- **D-SCAN-2 · Inventario inicial.** Hoy el escaneo registra **compra** (`PURCHASE_ENTRY`). El inventario inicial sigue
  en la carga masiva. ¿Se quiere también aquí?
- **D-SCAN-3 · EID.** Se reconoce y se rechaza; no hay dónde guardarlo. ¿Se quiere guardar (migración)?
