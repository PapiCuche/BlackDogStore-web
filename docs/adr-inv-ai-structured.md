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
| `ref` | Identificador temporal opaco de la petición (`c1`, `c2`…), sin relación con ids de la base |
| `kind` | `device` \| `accessory` |
| `line` | `new` \| `used` \| `accessory` |
| `family` | Uno de un vocabulario cerrado (iPhone, Galaxy, iPad, MacBook, Watch, AirPods, Pencil, Cargador, Cable, Funda…) |
| `model_words` | Hasta 6 tokens; cada uno del vocabulario de modelos (Pro, Max, Plus, Mini, FE, Ultra, SE…) o un número de 1–2 dígitos |
| `capacity_gb` | Entero de {16, 32, 64, 128, 256, 512, 1024, 2048} o ausente |
| `ram_gb` | Entero 1–32 o ausente |
| `sim` | `chip` \| `esim` \| ausente |
| `region` | Código de la lista cerrada (LL/A, ZA/A…) o ausente |
| `colour` | Uno del vocabulario de colores o ausente |
| `ambiguous` | Lista de nombres de campos que el reconocedor no pudo decidir |

**Nunca** se envía: el texto original ni fragmentos de él, nombres de personas, contactos, IMEI, series,
batería, costos, precios, cantidades, nombre del proveedor, empresa, sucursal ni usuario. Un costo no hace
falta para normalizar un modelo.

Un valor que no pasa su validación **no se envía** (el campo va en `ambiguous`), aunque el campo esté
permitido: un nombre permitido no hace seguro un contenido arbitrario.

### 2. Para qué se usa la IA

Sólo para proponer, por `ref`, la normalización del nombre comercial y la coincidencia con una variante del
catálogo cuando el reconocedor local marcó ambigüedad. La IA nunca ve el catálogo completo: recibe, a lo
sumo, los nombres de las variantes candidatas de **esa empresa** que el servidor ya filtró.

### 3. Qué se acepta de vuelta

JSON de esquema fijo: `{ "results": [ { "ref": "c1", "model": "...", "variant_index": 0 | null,
"confidence": "high"|"low" } ] }`. Se validan: `ref` existente en la petición, `model` dentro del
vocabulario, `variant_index` dentro de las candidatas enviadas. Todo lo demás se descarta. La respuesta es
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

## Consecuencias

- La ruta de texto libre (`extract(text)`) se eliminó: `propose` sólo acepta un `StructuredRequest`
  construido por `build_request` (TypeError con cualquier otra cosa). La redacción anterior
  (`inventory_ai_safety.redact`) queda como cable trampa: si cambiaría algo del pedido serializado, no se
  envía (`unsafe_request`).
- Los nombres de variantes del catálogo (datos del inquilino, no confiables) viajan reducidos a tokens del
  vocabulario.
- La IA sólo aporta donde el reconocedor local duda; el resto no cuesta nada.
- Requiere implementar el serializador estructurado y sus pruebas (cada campo, cada vocabulario, valores
  inválidos excluidos, `ref` sin correlación con la base) antes de cualquier proveedor.
- INV-AI-02 (proveedor real) sigue bloqueado hasta aprobar esta implementación y recibir la clave del
  propietario en un despliegue autorizado.
