# ADR · Selector de equipos compartido (SELECTOR-UX-01)

Estado: **aceptada por el propietario (2026-11-25)** en su especificación de SELECTOR-UX-01; implementada en dos PR.
Relacionadas: `docs/adr-catalog-reference.md` (paleta, exclusiones, PR F de la caja), `docs/adr-catalog-commercial.md`
(asistente de listas de proveedor, B1, decisiones D-B1-1…D-B1-5).

## 1. Contexto

Elegir «qué equipo es» (modelo → capacidad → color → SIM → región) se hacía de tres maneras:

| Pantalla | Antes | Problema |
|---|---|---|
| Caja · «Vender equipo por modelo» | Chips visuales sobre la paleta menos exclusiones (CATALOG-REFERENCE-01 · PR F) | — (es la referencia) |
| Proveedores · «Actualizar lista de proveedor» | Sólo texto: el servidor lee la línea y la persona no puede corregir la configuración salvo editando el texto | Una SIM que la línea no dice deja la configuración «incompleta» sin salida; los colores no se marcan ni desmarcan |
| Productos › Registro · «Registrar artículo» | Un `<select>` por atributo con **todas** las opciones de la empresa | Ofrece combinaciones que la paleta no admite o que el modelo excluye; el servidor las rechaza al final |

Además, el resolutor del asistente (`configuration_resolver`) **no miraba las exclusiones** del modelo: una combinación
excluida («Verde · Chip») salía como `combinacion_valida_sin_variante` y la pantalla ofrecía «Crear variante», que
`check_variant` rechazaba.

## 2. Decisión

**Un solo configurador**: la lógica (`frontend/app/admin/lib/device-configurator.ts`) y el aspecto
(`frontend/app/admin/components/DeviceConfigurator.tsx`, `ConfiguratorStep`) de la caja, extraídos sin cambiar su
contrato. La caja sigue usando `pos-device-picker.ts`, que reexporta la lógica y conserva su conteo de equipos propios
(`ownAvailable`); su DOM es el mismo (grupos `radiogroup`, nombres `attribute-<id>`, «no existe» para lectores de
pantalla, conteo verde, muestra o foto del acabado).

Dos modos:

- **único**: una capacidad, un color, una SIM y una región → UNA configuración (caja, «Registrar artículo»);
- **múltiple**: capacidad, SIM y región en común (un valor cada uno) y **varios colores** → una configuración por color
  marcado (asistente de listas). Sólo los colores son varios: nunca se multiplican roles ni se crea un producto
  cartesiano que nadie declaró.

Reglas comunes: la jerarquía de la caja (lo de después nunca cierra una opción de antes); una opción imposible queda
deshabilitada y dice por qué; un paso con una sola opción no se pregunta; los datos son siempre los del catálogo real
del modelo (la caja lee `/pos/models/<id>/configurations/`; las demás pantallas, la paleta
`/admin/catalog/models/<id>/palette/`, adaptada por `device-configurator-palette.ts`). Nada de listas en el código.

## 3. Asistente de listas de proveedor (PR A)

1. Pegar → proveedor, fecha, moneda → «Leer la lista» (sin cambios: gramática, `/resolve`, D-B1-1…D-B1-5).
2. **Una línea = una oferta = un grupo** (modelo, capacidad, SIM, región, importe; uno o varios colores).
3. **Dos ofertas en una línea** (`splitOffers`, en `supplier-list-grammar.ts`): sólo en líneas con dos o más importes.
   Se prueban todos los cortes (hasta 40 palabras y 8 ofertas; cada lectura y cada resto se resuelven una vez); cada
   parte debe leerse sola como UNA oferta limpia (un importe claro, sin dudas, algo ofrecido), tomando de la anterior lo
   que no dice (modelo, capacidad, SIM, región; **nunca** el color), salvo si nombra su propio modelo: entonces es otra
   oferta y no hereda nada. Una línea más larga, o una búsqueda que no termina, queda en duda: nunca «una oferta». Con una sola
   manera de partirla se propone «Separar en N líneas» (reescribe el texto y se vuelve a leer; nada se clasifica). Con
   varias, la línea queda en duda sin propuesta. En ambos casos la línea **no se guarda** tal cual: un importe nunca se
   pega al modelo o al color de otra oferta. Un costo y un PVP de la misma oferta no son dos ofertas.
4. **Selector visual por línea** (`SupplierLineSelector`, desplegable, cerrado por defecto): la paleta real del modelo
   en modo múltiple. Lo leído viene marcado «leído»; lo que una duda nombra, «dudoso» (nunca se elige solo); lo añadido,
   «a mano». La capacidad, SIM o región que el texto **dice** queda fija (cambiarla es corregir el texto, con sus reglas).
   Un paso que el texto no dice se elige a mano. «Usar esta selección» manda a `/resolve` las **etiquetas de la
   empresa**, una fila por color (`L<n>-S<opción>`): el servidor dice otra vez qué es cada configuración (variante
   existente por su id real, válida sin variante, fuera de paleta, **no existe en este modelo**, archivada…). Un color
   cuyo nombre lleva un separador del resolutor no se envía y se dice.
5. Una tarjeta por configuración, con el estado del servidor; la que viene del selector lo dice, y nombra lo elegido a
   mano. Las dudas de texto de la línea (palabras sin reconocer, color compuesto, conflictos) **siguen bloqueando**: el
   selector no las borra (regla de colores compuestos del 2026-11-14).
6. Costo, precio comercial, cantidad confirmada, «Incluir en el guardado», ensayo e importador oficial: los de siempre.
   «Crear variante» es el de siempre (`createModelVariant`, borrador oculto a 0,00, sin stock ni publicación, con
   `products.manage`), sólo para `combinacion_valida_sin_variante`; tras crearla, la selección se vuelve a leer.
7. **Variantes que faltan, en lote**: «Revisar y crear N variantes…» muestra cada configuración válida sin variante de la
   lectura (una por configuración según los ids de opción del servidor, nunca por cómo se escribió, con las líneas que la
   nombran) y sólo las crea con «Confirmar: crear N borradores». Misma regla y misma escritura que «Crear variante»; un
   rechazo se dice por su nombre y no detiene las demás; al final se vuelve a leer una vez.

**Backend**: el resolutor devuelve `combinacion_excluida` cuando todos los roles se dijeron, están en la paleta, ninguna
variante es esa combinación y dos de sus opciones son un par excluido. Una variante que ya es esa combinación sigue siendo
`variante_existente` (la historia no se reescribe). Sin migraciones.

## 4. «Registrar artículo» (PR B)

Con un modelo existente que tiene paleta, los `<select>` de atributos se sustituyen por el configurador en modo único
(línea `new` o `used` según el tipo). Dice si la configuración ya es una variante («se le agregan los equipos») o si se
creará. Sin paleta, o con un modelo nuevo, siguen los `<select>` (no hay nada que validar). Después: escanear IMEI/serie,
proveedor, costo, sucursal, revisar (nuevo); IMEI/serie, estado, batería, precio, fotos (seminuevo). «Registrar otro
igual» conserva lo común (tipo, modelo, configuración, sucursal, proveedor, precios) y vacía los identificadores; cada
registro lleva su propia `Idempotency-Key` y el servidor sigue rechazando un IMEI repetido.

## 5. Seguridad

- Ningún cambio de arquitectura SaaS: la empresa la resuelve el servidor; las llamadas llevan `?company=` como antes.
- Autoridad igual que antes: leer la paleta, `products.view`; crear variantes, `products.manage`; costos, `costs.view`.
- A `/resolve` sólo viajan los campos de `ResolveRowInput` (ni importes, ni cantidades, ni identificadores).
- Nada cambia precios, stock ni publicación por sí solo.

## 6. Fuera de alcance / pendiente

- **ScanIntake** (SCAN-TO-STOCK-01, #437): adopta el configurador cuando se fusione (seguimiento).
- «Recibir mercadería» (`ReceivingWizard`) elige un **artículo** (variante) por búsqueda; no se tocó.
- Defectos y accesorios de un seminuevo como campos propios: no existen en el modelo de datos (hoy van en «Detalles para
  el comprador» y en la inspección de `UsedUnitsPanel`). Decisión del propietario.

## 7. Decisión del propietario (2026-11-27): editar una oferta ya leída sigue validando

Cuando una persona corrige a mano una oferta ya leída (capacidad, SIM, región o colores, sea lo que el texto fija o lo
elegido a mano), la validación de la combinación vuelve a correr **antes** de guardar o de crear una variante, en el
navegador y en el servidor. La revisión del 2026-11-27 confirmó que ya se cumplía. Cada regla, con la prueba que la fija:

| Regla | Prueba |
|---|---|
| Cambiar el texto borra lo leído, el precio y lo marcado; hay que volver a leer | `supplier-list-selector-screen.test.tsx` · «cambiar el texto de una oferta lista para guardar…» |
| Cambiar la selección vuelve a pasar por `/resolve`; las tarjetas anteriores, su precio y su selección se descartan; una combinación excluida no se guarda ni ofrece «Crear variante» | `supplier-list-selector-screen.test.tsx` · «una oferta lista para guardar que se cambia a mano a una combinación excluida…» y «una combinación que el modelo excluye no se puede crear ni guardar» |
| Sólo una configuración con variante (`variante_existente`) se puede elegir para guardar | `supplier-list-assistant.test.tsx` · «sólo una configuración limpia con variante se puede elegir…» |
| Lo que el texto fija no se cambia a mano; si una elección lo hace imposible, la fila va sin ese paso y queda incompleta, nunca con otro valor | `supplier-list-selector-screen.test.tsx` · «una opción elegida a mano que choca con lo que el texto fija…» |
| El servidor juzga otra vez las filas del selector; un par excluido (color × SIM o capacidad × SIM) es `combinacion_excluida`; un rol sin decir es `incompleta` | `test_configuration_resolver.ExcludedCombinationTest` · `test_selector_rows_posted_to_resolve_are_judged_again`, `test_a_capacity_and_a_sim_the_model_excludes_are_never_valid_either` |
| Crear una variante excluida se rechaza (`check_variant`) | `test_model_option_exclusions` · `test_an_excluded_pair_is_refused_when_a_variant_is_created` |
| En la lista semanal en Excel, una fila con un par excluido es un error sin propuesta de «Crear variante» | `test_supply_weekly.ExcludedCombinationRowTest` |
| Editar una oferta en el panel nunca cambia su proveedor ni su variante | `test_supplier_offers.OfferRulesTest` · `test_editing_an_offer_never_changes_its_supplier_or_variant` |
