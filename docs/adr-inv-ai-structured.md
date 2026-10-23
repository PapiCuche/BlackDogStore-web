# ADR — La IA del inventario sólo recibe datos estructurados y validados (PROPUESTA)

Estado: **dirección aprobada por el propietario (2026-10-22); contrato implementado en #148** (`inventory_ai_payload.py`,
`inventory_ai.propose`), sin proveedor. #148 sigue en NO-GO para
cualquier proveedor externo: sin claves, sin llamadas, registro de proveedores vacío.

## Contexto

Tres auditorías independientes de #148 mostraron que redactar texto libre con reglas siempre deja fugas
(nombres sin palabra que los anuncie, cifras con separadores poco comunes). El propietario decidió: **la IA no
recibe el texto del proveedor**; recibe una representación mínima que el reconocedor local (INV-TEXT-02,
#142) ya produjo, y cada valor se valida antes de salir.

## Decisión

### 1. Qué se envía: lista permitida, por candidato

| Campo | Tipo y validación antes de enviar |
|---|---|
| `ref` | Identificador temporal opaco de la petición (`c1-<nonce>`, `c2-<nonce>`…, nonce aleatorio por petición), sin relación con ids de la base |
| `kind` | `device` \| `accessory` |
| `line` | `new` \| `used` \| `accessory` |
| `family` | Uno de un vocabulario cerrado (iPhone, Galaxy, iPad, MacBook, Apple Watch, AirPods, Apple Pencil, Cargador, Cable, Funda…) |
| `model_words` | Hasta 6 tokens del vocabulario (Pro, Max, Plus, Mini, FE, Ultra, SE…); a lo sumo **un** código de modelo, sólo en primera posición y de una **lista cerrada** (1–20, S20–S30, A00–A59), para que no se puedan deletrear cifras token a token |
| `capacity_gb` | Entero de {16, 32, 64, 128, 256, 512, 1024, 2048} o ausente |
| `ram_gb` | Entero 1–32 o ausente |
| `sim` | `chip` \| `esim` \| ausente |
| `region` | Código de la lista cerrada (LL/A, ZA/A…) o ausente |
| `colour` | Uno del vocabulario de colores o ausente |
| `ambiguous` | Lista de nombres de campos que el reconocedor no pudo decidir |
| `variants` | Sólo si hay candidatas: lista no vacía de hasta 5 `{index, tokens}`; `index` = su posición; `tokens` del vocabulario, tamaños (128GB, 1TB…) y a lo sumo **un** código de modelo, justo después de una familia |

**Nunca** se envía: el texto original ni fragmentos de él, nombres de personas, contactos, IMEI, series,
batería, costos, precios, cantidades, nombre del proveedor, empresa, sucursal ni usuario. Un costo no hace
falta para normalizar un modelo.

Un valor que no pasa su validación **no se envía** (el campo va en `ambiguous`), aunque el campo esté
permitido: un nombre permitido no hace seguro un contenido arbitrario.

### 2. Para qué se usa la IA

Sólo para proponer, por `ref`, la normalización del nombre comercial y la coincidencia con una variante del
catálogo cuando el reconocedor local marcó ambigüedad. La IA nunca ve el catálogo completo: el servidor busca
por id, **en la empresa de la sesión**, a lo sumo 5 variantes candidatas, y las envía reducidas a tokens del
vocabulario (un número sólo justo después de su familia, también si la familia tiene dos palabras: «Apple Watch 9»).

### 3. Qué se acepta de vuelta

JSON de esquema fijo: `{ "results": [ { "ref": "c1-…", "model_words": [...], "variant_index": 0 | null,
"confidence": "high"|"low" } ] }`. Se validan: `ref` (texto) existente en la petición, `model_words` del
vocabulario o de la lista cerrada de códigos, `variant_index` dentro de las candidatas enviadas. Es una
propuesta: nada la aplica sin revisión humana. Todo lo demás se descarta. La respuesta es
dato, nunca instrucción: no hay acciones, precios, cantidades, empresas ni permisos en el esquema.

### 4. Controles que se mantienen (ya en #144/#148)

- Interruptores: global (`INVENTORY_AI_ENABLED`), por empresa (`InventoryAISettings`) y por operación
  (`use_ai`); decide el servidor, en cada operación.
- Permiso de la operación y empresa completa en el alcance.
- Cuotas por persona (2/min, 20/día) y presupuesto mensual por empresa con reserva bajo bloqueo; alertas
  50/80/100; corte al 100 %; cortacircuitos tras 3 fallos.
- Cada llamada deja `InventoryAIUsage` sin contenido.
- Fallback: la revisión manual y el reconocedor local funcionan igual sin IA.
- Aislamiento por empresa: las candidatas enviadas son sólo de la empresa de la sesión.

### 5. Prompt injection

Como no se envía texto libre, el proveedor no puede inyectar instrucciones; los valores enviados pertenecen
a vocabularios cerrados o son números acotados. La respuesta se valida contra el esquema y contra lo
enviado.

### 6. Vocabularios cerrados y multiempresa (juicio SaaS, 2026-10-22)

`FAMILIES`, `MODEL_WORDS`, `REGIONS`, `COLOURS` y `MODEL_CODES` son **vocabulario genérico de mercado**, no
configuración de un inquilino: familias de varios fabricantes (Apple, Samsung, Google, Xiaomi, Motorola,
Honor), palabras de accesorio genéricas, sufijos de región y colores. No contienen nombre, catálogo, precios,
políticas ni datos de la tienda piloto, y no se muestran a ningún cliente: son una **lista permitida de
seguridad** (minimización de datos), igual que el vocabulario del reconocedor local de #142 ya en `master`
(`inventory-text-stages.ts`). AGENTS.md §2 prohíbe datos del piloto en código global; esta lista no lo es.
Una tienda cuyos equipos no estén en la lista no ve nada de otra: sus candidatos salen `ambiguous` y van a
revisión manual (falla cerrado). Lo fija `VocabularyIsGenericTest` (sólo letras, como mucho dos palabras,
varios fabricantes, sin datos del piloto).

**No** se hace configurable por empresa: una palabra escrita por un inquilino en el vocabulario sería un canal
de texto libre (un nombre o un teléfono escrito como «familia» viajaría). Si hiciera falta ampliarla, se
amplía en código, revisada en un PR, con la misma forma validada (letras, como mucho dos palabras, sin
dígitos); nunca desde configuración del inquilino ni desde el texto del proveedor. (PROPUESTA, no implementada.)

### 7. Ruta de punta a punta probada (2026-10-22)

Del texto pegado a lo que recibiría un proveedor, sin nada inventado en medio:

1. `previewSupplierText` + `matchPayload` (navegador, #142) → sólo `key, kind, line, model, capacityGb,
   colour`. Jest (`frontend/__tests__/inventory-ai-recognizer-path.test.ts`) fija esa salida en
   `frontend/__tests__/support/inventory-ai/recognizer-output.json` a partir de una lista de WhatsApp
   **des-identificada** (IMEI ficticios que pasan Luhn, series, teléfonos, nombre y correo inventados, costos,
   cantidades, batería).
2. Servidor: `text_preview_match.clean` y `match` (de la empresa de la sesión) → `item_from_preview` →
   `build_request` → `payload_is_clean` → `propose`, con un proveedor falso que registra lo que recibe
   (`store.test_inventory_ai_recognizer_path`). Ningún valor sensible llega, y todo lo recibido es
   vocabulario, números acotados o `ref`.

`item_from_preview` sólo lee los campos del limpiador; parte `model` en familia y palabras con los
vocabularios. Una palabra desconocida deja `model_words` en `ambiguous` (no se envía); una primera palabra
desconocida o un identificador marcado por el limpiador dejan fuera todo el modelo. RAM, SIM y región no
viajan por esta ruta porque `matchPayload` no los lleva.

Hallazgo: el reconocedor de #142 deja palabras de cantidad dentro de `model` («Galaxy S24 Ultra x3»,
«iPhone 15 Pro (quedan 2)»). Llegan a nuestra búsqueda de catálogo, no a la IA: aquí vuelven el modelo
`ambiguous`. Conviene corregirlo en el reconocedor (INV-TEXT-03); al hacerlo cambia el JSON fijado y hay que
regenerarlo a propósito (`WRITE_INV_AI_FIXTURE=1`).

## Consecuencias

- `propose` exige un `StructuredRequest` **sellado** por `build_request` (el sello indica el origen, no la
  seguridad) y vuelve a validar cada valor del pedido (`payload_is_clean`: claves, refs, valores, forma e
  índice de las variantes, y en sus tokens un solo código de modelo tras una familia): uno falsificado o alterado no sale (`unsafe_request`). Al proveedor se le entrega
  exactamente lo validado (la serialización comprobada), no el objeto vivo.
- Cada llamada cuenta al menos su reserva, aunque el proveedor declare menos: el presupuesto sigue siendo un
  corte.
- La ruta de texto libre (`extract(text)`) y su respuesta antigua (`clean_candidates`, `restore`) se eliminaron: `propose` sólo acepta un `StructuredRequest`
  construido por `build_request` (TypeError con cualquier otra cosa). La redacción anterior
  (`inventory_ai_safety.redact`) queda como cable trampa: si cambiaría algo del pedido serializado, no se
  envía (`unsafe_request`).
- Los nombres de variantes del catálogo (datos del inquilino, no confiables) viajan reducidos a tokens del
  vocabulario.
- La IA sólo aporta donde el reconocedor local duda; el resto no cuesta nada.
- El serializador estructurado y sus pruebas (cada campo, cada vocabulario, valores inválidos excluidos,
  `ref` sin correlación con la base) están en #148, y la prueba de punta a punta desde el reconocedor local
  (INV-TEXT-02) con una lista des-identificada también (§7). Falta la autorización del propietario para integrar.
- INV-AI-02 (proveedor real) sigue bloqueado hasta aprobar esta implementación y recibir la clave del
  propietario en un despliegue autorizado.
