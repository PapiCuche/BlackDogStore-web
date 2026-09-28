# ERP-SALES-FISCAL-UI — PARADA EN LA PUERTA §5

**Este documento NO es el cierre de §47.** Es lo que §5 y §46 ordenan entregar en su
lugar:

> §5 — «Si la cantidad de caminos alcanzables no soportados impide cumplir
> "autoemisión normal": DETENTE antes de desplegar la integración completa y entrega
> el gap exacto para una fase fiscal intermedia.»
>
> §46 — «Si promociones/cupones u otro camino existente hacen imposible autoemitir
> correctamente con el fiscal core actual: NO declarar la fase IMPLEMENTADA. Entregar
> el gap y recomendar la fase fiscal necesaria.»

La puerta se cruzó. **No se escribió una sola línea de código funcional.** La rama
está en el commit de partida, el árbol limpio, y lo único que se añade es este
informe.

Las secciones siguen la numeración de §47 para que se puedan cotejar una a una; las
que describen implementación dicen NO IMPLEMENTADO y por qué.

---

## 1 · Resumen

El núcleo fiscal **se niega a emitir cualquier venta con descuento**, y el descuento
es alcanzable en los dos canales. En el mostrador hay además un segundo bloqueo
independiente: **el POS nunca fija `receipt_type`**, así que ninguna venta presencial
puede ni resolver qué tipo de comprobante le corresponde.

Dos defectos, los dos verificados leyendo el código:

| # | Defecto | Sitio | Consecuencia |
|---|---|---|---|
| **1** | Un descuento impide emitir | `fiscal_services.py:347` | Ninguna venta con cupón (web) ni con cupón/manual/promoción (POS) puede autoemitirse |
| **2** | El POS no fija `receipt_type` | ausente de `pos_services.py:803-825` y `:922-957` | Ninguna venta de mostrador resuelve tipo de comprobante; falla cerrado en `fiscal_services.py:329-334` |

El defecto 1 **no es un descuido**: está documentado como frontera deliberada del
núcleo desde la matriz UBL —`docs/sunat-factura-ubl21-matriz.md:12` («Sin descuento,
anticipo, percepción, detracción, ISC, gratuitas, exportación…») y `:128`
(`cac:AllowanceCharge` → «No hay descuentos ni cargos»)—. El código explica por qué
se niega en vez de apañarlo, y tiene razón:

> «Un descuento se expresa en UBL con `cac:AllowanceCharge` y su
> `cbc:AllowanceTotalAmount`, no rebajando el importe de la línea: hacer eso produce
> un documento que dice "2 unidades a 100,00" con un total de línea de 184,75, donde
> la aritmética no cierra y la rebaja es invisible. Enviar eso a SUNAT sería declarar
> un precio unitario que nadie cobró.» — `fiscal_services.py:336-345`

Implementar la UI de esta fase sobre ese núcleo significaría que **toda venta con
descuento queda pagada y con un error fiscal permanente**. Eso es exactamente lo que
§5 protege, así que se paró aquí.

Lo que sí está listo y no hace falta inventar: el selector de comprobante de la web,
la autoridad de pago confirmado, la idempotencia del pago, la frontera idempotente de
emisión y el mecanismo de otorgamiento de 5B. El trabajo que falta es más pequeño de
lo que la autorización supone — pero está **detrás** del defecto 1.

---

## 2 · Git

Rama nueva creada según §1, desde el HEAD verificado de 5B.

| | |
|---|---|
| HEAD esperado por §0 | `e17ae46` |
| HEAD verificado | `e17ae46aedd9a16f0015823ee0944e7bbf79ea1b` — coincide |
| Rama creada | `erp/sales-fiscal-ui` desde `e17ae46` |
| Rama fiscal | `erp/fiscal-sunat` intacta en `e17ae46` |
| Merge / push / rebase / squash | ninguno |

## 3 · Branch / base / final / tree

| | |
|---|---|
| Base | `e17ae46` (cierre de ERP-FISCAL-5B) |
| Commits propios | **0 de código**; 1 de documentación (este informe) |
| Árbol antes de escribir este informe | limpio |
| Migraciones | **ninguna** |
| Diff de frontend | **ninguno** |
| Suite | no re-ejecutada, y a propósito: no cambió código, así que el baseline de 5B (**4378, `OK (skipped=3)`**) sigue siendo el estado real. Correr 24 minutos de pruebas sobre un árbol idéntico no es diligencia, es ruido |

## 4 · Método y revisores — con su fallo declarado

La auditoría de §4/§5/§22 se lanzó como abanico de cuatro dimensiones independientes
con verificación adversarial. **Se degradó gravemente y hay que saberlo al leer este
informe:**

- 8 agentes: **2 completaron, 6 se estancaron** (sin progreso durante 180 s × 6
  intentos cada uno).
- Se estancaron precisamente **la dimensión del soporte fiscal** —el asunto central—
  y la de otorgamiento/acceso del cliente.
- **Se estancaron TODOS los verificadores adversariales**, así que ninguna afirmación
  del abanico quedó refutada ni confirmada por un segundo lector.

Qué se hizo con eso, en vez de aceptarlo:

1. **El asunto central se auditó a mano.** La negativa del descuento, la regla de
   gravadas, la ausencia de `cac:AllowanceCharge`, el mapa `receipt_type` → tipo de
   documento y la autoridad de pago se leyeron directamente. No se delegó por segunda
   vez: el primer intento costó 89 minutos y 790 000 tokens para estancarse.
2. **Las afirmaciones más consecuentes del abanico se comprobaron a mano**: que
   `Promotion` no es alcanzable desde la web, que no existe ningún cargo de envío, que
   el POS acepta `payment_method='online'` en el servicio, y que
   `sales.notes.manage` se aplica pese a declararse disponible. Las cuatro se
   sostienen.
3. **Lo que sigue sin verificación independiente** se marca como tal en §7.

## 5 · Auditoría del e-commerce real

Una sola puerta pública crea ventas: `POST /api/payments/create-checkout-session/`
(`urls.py:149` → `views.py:190`, `AllowAny` en `:208`). El checkout nativo v1 no es
público (exige `IsAuthenticated` y relación de cliente). `checkout/quote/` y
`coupons/validate/` no crean nada.

La autoridad del precio es el servidor: `validate_lines_and_subtotal` relee el
catálogo y `price_checkout` recalcula, sin aceptar jamás subtotal, descuento ni total
del cliente (`checkout_services.py:21`, `:247-296`, `:299-331`).

## 6 · Auditoría del POS real

**No se creó ningún segundo sistema de ventas, y no hacía falta:** una venta de
mostrador es un `Order` normal con `sales_channel=POS`
(`pos_services.py:926`), y el modelo `PosSale` aparte fue rechazado a conciencia
(`pos_services.py:4-10`).

- **Escritor autoritativo**: `pos_services.create_pos_sale` (`:802-803`), con dos superficies HTTP
  (`pos_views.py:259` en `admin/pos/sales/` y `v1_pos_views.py:304`), que sólo se
  diferencian en cómo resuelven el tenant.
- **La venta nace pagada**: no hay paso de confirmación. `status=PAID`, `paid=True`,
  `paid_at=now` y `fulfillment_status=DELIVERED` dentro de la misma transacción
  (`pos_services.py:948-954`).
- **Medios de pago**: cinco valores de enum —online, cash, card, transfer, other—. El
  POS **registra** el medio, nunca procesa un cobro; no se invoca pasarela alguna.
- **Idempotencia**: clave obligatoria de 8-64 caracteres más huella SHA-256 de la
  INTENCIÓN del operador, con restricción única condicional en base de datos
  (`models.py:563-567`) y 409 en HTTP. La huella se calcula ANTES de tarificar, así
  que un reintento sobrevive a una edición de catálogo.
- **Cuatro fuentes de descuento, mutuamente excluyentes**: ninguna, cupón, manual
  (porcentaje o importe, con permiso y motivo obligatorios) y promoción automática,
  que gana sobre las demás.
- **Hoy no emite nada**: `create_pos_sale` no produce ni comprobante electrónico ni
  nota interna NV-. Ambos son peticiones separadas y explícitas sobre la orden.

## 7 · Matriz de formas de venta alcanzables

Del abanico, **sin verificación adversarial** salvo donde se indica. Las marcadas ✓
las comprobé yo.

| Forma | Alcanzable desde la web | Nota |
|---|---|---|
| Sin descuento | **SÍ** (forma por defecto) | |
| Cupón (porcentaje entero 1-100) | **SÍ** | público; `AllowAny` en `views.py:151` |
| Promoción / combo automático | **NO** ✓ | sólo POS y admin; 0 referencias en el checkout |
| Descuento de importe fijo | **NO** | el `Coupon` no tiene columna de importe |
| Descuento manual autorizado | **NO** | sólo POS, con permiso y motivo |
| Envío / delivery | **SÍ** | dos métodos, **ambos con coste cero** ✓ |
| Recojo en tienda | **SÍ** (y es el defecto por defecto) | |
| Multi-ítem | **SÍ** | |
| Cantidad > 1 | **SÍ** | sin tope público por debajo del stock de la sucursal |
| Cliente identificado | **SÍ** | sin exigir login |
| Cliente anónimo | **SÍ** (camino principal) | |
| FACTURA con RUC | **SÍ** | |
| BOLETA | **SÍ** (preseleccionada) | |
| Producto inactivo | **NO** como venta persistida | sí como carrito bloqueado |
| Cambio de precio entre carrito y pago | **SÍ**, silencioso antes de la orden | congelado después |
| Ítem gratuito / bonificación | **NO** | el concepto no existe |
| Tarjeta regalo / saldo | **NO** | no hay modelo ni endpoint |
| Cargo de envío cobrado | **NO** ✓ | no hay campo ni aritmética |
| Izipay | **SÍ** — único camino de pago público | |

## 8 · Matriz de soporte fiscal

| Forma | Veredicto fiscal | Autoridad |
|---|---|---|
| Venta web sin descuento, gravada, boleta o factura | **SOPORTADA** | pasa todas las puertas de `get_or_create_fiscal_document` |
| **Venta web con cupón** | **RECHAZADA, FALLA CERRADO** | `fiscal_services.py:347` |
| **Cualquier venta POS** | **RECHAZADA, FALLA CERRADO — dos causas** | `receipt_type` vacío (`:329-334`) **y** descuento si lo hay (`:347`) |
| Línea no gravada | RECHAZADA, pero **latente** | `fiscal_services.py:224`; hoy sólo se emite `TAXED` y nada configura una empresa como no gravada (`tax_services.py:76-79`) |
| Emisión con XML incorrecto | **NO OCURRE** | no hay ninguna forma que se emita mal en silencio: todo lo no soportado se niega |

**Lo importante de esta matriz es la última fila.** No existe hoy ningún camino
`WOULD_EMIT_WRONG_XML`: el núcleo se niega antes de mentir. Esa propiedad es la que
no hay que perder al cerrar el gap.

## 9 · DB Design Gate

**No hubo, porque no hizo falta.** El §41 exige la puerta sólo si se necesita
migración, y no se necesita ninguna: `receipt_type` ya existe en `Order`
(`models.py:500-503`), el snapshot de identidad ya existe (`company_snapshot`,
`customer_*`, `tax_*`), y el mecanismo de otorgamiento de 5B ya tiene sus cinco
campos. §17 y §42 se cumplen por no crear nada.

## 10 · Migraciones

**Ninguna.**

## 11 · Modelo de tipo de comprobante

`Order.ReceiptType` = `BOLETA | FACTURA` exactamente (`models.py:254-256`), y el mapa
fiscal es 1:1 (`fiscal_services.py:140-143`). De ahí sale gratis una exigencia de §3:
**en una venta no se puede elegir NC, ND, RA, Resumen ni GRE**, porque el enum no los
contiene.

El campo es `blank=True` **sin default** (`models.py:500-503`). Consecuencia doble: la
web lo exige y lo congela; el POS lo deja vacío.

## 12 · UI de e-commerce — YA EXISTÍA

§9 pedía construir un selector explícito. **Ya está**: grupo de radios `receipt_type`
en `frontend/app/checkout/page.tsx:596-606`, con el servidor como autoridad
(`serializers.py:229`, `ChoiceField` **requerido**, sin default de servidor) y
compatibilidad factura⇒RUC impuesta en `serializers.py:285-288`.

**Una observación, no un cambio:** el formulario preselecciona
`receipt_type: "boleta"` (`page.tsx:108`). No es un default *oculto* —se ve y se puede
cambiar— pero es un default que el propietario no ha definido. §9 dice que si no hay
default definido, el usuario debe elegir. **Decisión tuya**, y por eso no la toqué:
dejarlo preseleccionado, o arrancar sin selección y exigir la elección.

## 13 · Datos de factura en el checkout — YA EXISTÍAN

RUC y razón social se recogen y se validan en el servidor, que es la autoridad. El
umbral de identificación de la boleta (S/ 700, RCP art. 8 num. 3.10) ya vive en
`fiscal_services.py:145-148`. No se duplicó ninguna regla tributaria en React.

## 14 · Datos de boleta en el checkout — YA EXISTÍAN

Igual: el frontend muestra requisitos, el backend decide.

## 15 · Disparador de pago — LA AUTORIDAD YA EXISTE, EL ENGANCHE NO

Determinado y verificado, pendiente de implementar:

- La autoridad es `_confirm()` (`views.py:476-562`), **dentro del endpoint de
  notificación (IPN)**, no en la URL de retorno del navegador. El propio código lo
  dice: la llamada del SDK en la página del comprador «es una pista de que el
  formulario terminó, no evidencia de un pago», y `PaymentStatusView` sólo LEE la
  orden, «que sólo el endpoint de notificación pudo haber cambiado»
  (`views.py:565-579`). **§6 ya está satisfecho por el diseño existente.**
- La idempotencia ya es de tres capas (`views.py:485-489`): bloqueo de fila
  `select_for_update()`, relectura del estado dentro del bloqueo con salida temprana
  que no repite trabajo (`:494-509`), y salidas de venta con clave `(orden, producto)`.
  Un IPN duplicado toma la rama temprana.
- **El enganche correcto es `transaction.on_commit`**, como ya hace el email
  (`views.py:562`). Llamar a la emisión en línea mantendría un bloqueo sobre `Order`
  durante el SOAP a SUNAT y rompería §14. Al ir post-commit, el pago está
  comprometido antes de que empiece cualquier trabajo fiscal, así que **§7 se cumple
  por arquitectura**: un fallo fiscal no puede revertir la venta.
- **Consecuencia que hay que aceptar**: la rama del IPN duplicado retorna antes de la
  línea 562, así que una orden ya PAID cuya emisión falló no se reintenta desde el
  pago. El reintento tiene que venir de la superficie de administración (§37).

## 16 · Orquestación de autoemisión — LA FRONTERA YA EXISTE

§13 pedía crear algo equivalente a `ensure_sale_fiscal_document(order)`. **Ya existe**:
`get_or_create_fiscal_document(order)` (`fiscal_services.py:310`), idempotente por
restricción de base de datos, sin red dentro, y que exige `paid and status == PAID`
con el argumento correcto: «Un comprobante no es autoridad del pago.»

Lo que falta es **llamarla desde el dominio en `on_commit`**, y no se implementó por
el defecto 1.

## 17 · Idempotencia y concurrencia — NO IMPLEMENTADO (ya cubierto por lo existente)

Nada nuevo haría falta: el pago tiene tres capas, el POS tiene clave + huella +
restricción única, y la emisión tiene su propia restricción. El §29 (regresión de
correlativo del RA bajo concurrencia PostgreSQL) **no se ejecutó**, porque no se tocó
ese algoritmo y no había integración que cerrar.

## 18 · Comportamiento ante fallo fiscal — NO IMPLEMENTADO

Diseño determinado (§15): post-commit ⇒ el pago sobrevive por construcción. Falta el
estado visible «pagado + comprobante pendiente/error» y el camino de reintento del
administrador.

## 19 · Arquitectura de evidencia de otorgamiento — NO IMPLEMENTADO

El mecanismo de 5B **se reutiliza tal cual y no hay que crear ningún modelo** (§17):
`record_grant(document, *, actor, method, evidence=None)`, de escritura única, con
`FiscalGrantMethod` = `ECOMMERCE_PORTAL | EMAIL | POS_PRINT | POS_ELECTRONIC | MANUAL
| API` — los seis valores que esta fase necesita ya están.

**Hoy su único llamante es el endpoint de administración**
(`fiscal_void_views.py:144`). Eso confirma la premisa de §16: el otorgamiento es
100 % manual, y por eso toda baja exige hoy una atestación a mano.

## 20 · Otorgamiento en e-commerce — NO IMPLEMENTADO

## 21 · Acceso del cliente a sus documentos — NO IMPLEMENTADO, y parte de cero

**No existe ninguna ruta fiscal fuera de `admin/`.** Verificado: todas las rutas de
comprobante, PDF, XML y CDR están bajo `admin/`. El cliente tiene
`V1CustomerOrderViewSet` (sólo lectura, con pruebas de propiedad) pero **ningún acceso
a comprobantes**. §20/§21 son enteramente nuevos, y hay que resolver además el
checkout de invitado (`cart_session_key`), donde no hay cuenta que autenticar.

## 22-26 · POS: UI, disparador, otorgamiento de boleta y de factura, atestación manual

**NO IMPLEMENTADO.** §23 pedía que el operador elija boleta o factura antes de
finalizar: hoy **no hay elección alguna** en el POS, ni en backend ni en frontend, y
`receipt_type` queda vacío. Efecto secundario ya visible: el PDF de la nota interna
imprime «Comprobante solicitado: —» para toda venta de mostrador.

El disparador POS, en cambio, ya existe: la venta nace pagada, con idempotencia.

## 27 · Regresión de concurrencia del RA — NO EJECUTADA

Sin integración que cerrar, no procede. El algoritmo no se tocó.

## 28 · Permisos

Ya están separados como §33 pide, y verificado: vender exige `sales.pos.use`; emitir
exige `sales.fiscal.issue`; son disjuntos, y un cajero con sólo `sales.pos.use` no
puede emitir ni una boleta ni una NV-. La emisión fiscal además no tiene puente de rol
legado (`_NO_LEGACY_BRIDGE`).

## 29 · Tenant y sucursal

Sin cambios, porque no se cambió código. Lo existente ya cumple lo que §29/§30/§31 de
la autorización exigen de las APIs: la emisión se acota por `visible_orders()` **antes**
de gastar un correlativo, cruzar de empresa o de sucursal responde **404** —no 403, que
confirmaría que el recurso existe—, y el tenant nunca llega del cliente.

Lo mismo vale para la regla de «no confiar en el navegador»: el checkout recalcula
subtotal, descuento y total desde el catálogo (`checkout_services.py:21`, `:247-296`) y
el POS resuelve la infraestructura fiscal en el servidor. Ni `company_id`, ni
`branch_id`, ni serie, ni correlativo, ni RUC emisor se leen del cuerpo de la petición
en ninguno de los dos canales.

## 30 · Seguridad

Sin cambios de código, así que todo lo que §32 manda conservar —JWT en cookie
HttpOnly, CSRF, RBAC, aislamiento por empresa y sucursal, throttling, auditoría, 404 al
cruzar empresas, y no exponer CDR interno, Clave SOL, secretos de certificado, sobre
SOAP ni claves privadas— sigue exactamente como lo dejó 5B.

Pero la auditoría destapó **dos agujeros PREEXISTENTES** que no son de esta fase y que
**no toqué**, porque §48 prohíbe cambios sin puerta:

1. **El POS acepta `payment_method='online'`.** `pos_services.py:720` valida contra
   `PaymentMethod.values`, que incluye ONLINE; sólo la lista *ofrecida* lo excluye
   (`pos_payloads.py:77-83`) y ninguna vista revalida. Una llamada directa a la API crea
   una venta de mostrador marcada como pagada por pasarela —sin que ninguna pasarela
   haya intervenido— y `resolve_cash` (`:479-480`) se salta entonces toda la validación
   de efectivo: sin importe recibido y sin vuelto calculado.
2. **El roster de capacidades miente sobre sí mismo.** `capabilities.py:123-124` declara
   `sales.notes.manage` como `STATUS_AVAILABLE` y el comentario de `:145-147` afirma que
   «nada la aplica todavía», pero `inventory_views.py:1465` la exige de verdad. Además
   lleva puente de rol legado, al contrario que las capacidades fiscales.

Ambos están en §44 con su evidencia. El primero es el que conviene cerrar antes.

## 31 · Auditoría

Lo que hay hoy es `AdminAuditLog.log(...)` en los seis puntos fiscales que dejó 5B, con
metadatos que llevan identificador, método, motivo, `xml_sha256`, ticket y código de
respuesta, y **ninguna credencial**.

Lo que §43 pide auditar —tipo de comprobante elegido y congelado, pago confirmado,
inicio y resultado de la autoemisión, otorgamiento publicado, otorgamiento entregado y
uso de la atestación manual de respaldo— **no existe todavía**, porque ninguno de esos
eventos existe. No es una omisión de esta entrega: es trabajo de la fase que implemente,
y la lista de §43 sirve tal cual como su especificación.

## 32-37 · Pruebas backend, suite PostgreSQL, pruebas de frontend, TypeScript, lint, build

**NO EJECUTADAS, y es lo correcto.** §34 las exige «después de implementar». No se
implementó nada, no se tocó ningún `.tsx`, y el baseline de 5B sigue vigente sin
alteración. Ejecutarlas no probaría nada de esta fase.

## 38-39 · Revisores A y B

**Degradados, ver §4.** Seis de ocho agentes se estancaron, incluido todo el paso de
refutación. Lo crítico se auditó a mano; lo no verificado está marcado en §7.

## 40 · Archivos

Ninguno modificado. Uno añadido: este informe.

## 41 · Commits

Uno, de documentación. Cero de código.

## 42 · Documentación

**Deliberadamente NO se actualizaron `CHANGELOG.md` ni
`estado-actual-y-auditoria-tecnica.md`, y no se registró ningún ADR.** Nada se
implementó; una entrada de CHANGELOG describiría una fase que no ocurrió, y los ADR de
este repositorio registran decisiones **encarnadas en código**. Las determinaciones de
diseño de §15/§16/§19 quedan aquí, y se convertirán en ADR cuando la fase que las
implemente las encarne.

## 43 · Errores — incluidos los míos

1. **Estuve a punto de inventar una cifra.** Leí «el 40 %» en
   `checkout_services.py:54` y casi lo reporté como «el 40 % de las ventas llevan
   descuento». No es eso: es que el 40 % de los subtotales difiere en un céntimo entre
   redondeo bancario y media al alza. **No tengo ninguna medición de con qué frecuencia
   se usan los descuentos**, y no la voy a estimar.
2. **El abanico de auditoría falló** como se describe en §4, y consumió 89 minutos.
   Debí acotar más los prompts y no pedir siete preguntas amplias por agente.
3. **El informe de esta fase depende de hallazgos sin refutar** en las formas de venta.
   Marcados.

## 44 · Deuda

| Deuda | Estado |
|---|---|
| **`cac:AllowanceCharge` sin implementar** | **Abierta — bloquea esta fase entera.** Frontera documentada en la matriz UBL |
| **POS sin `receipt_type`** | Abierta; independiente del defecto 1, pero inútil sin él |
| Sin acceso del cliente a comprobantes | Abierta; parte de cero, y el invitado no tiene cuenta |
| Otorgamiento 100 % manual | Abierta; heredada de 5B (`GRANT-EVIDENCE-01`) |
| **POS acepta `payment_method='online'`** | **Abierta, PREEXISTENTE y no de esta fase.** `pos_services.py:720` valida contra `PaymentMethod.values`, que incluye ONLINE; sólo la lista ofrecida lo excluye (`pos_payloads.py:77-83`) y ninguna vista revalida. Una llamada directa crea una venta de mostrador marcada como pagada por pasarela, y `resolve_cash` (`:479`) se salta entonces toda la validación de efectivo: sin importe recibido y sin vuelto. No lo toqué: §48 prohíbe cambios sin puerta |
| **Sin cargo de envío en ningún sitio** | **Abierta, PREEXISTENTE, y comercial más que fiscal.** El envío nacional se vende sin flete: el total es sólo `subtotal − descuento` |
| **`sales.notes.manage` se aplica declarándose disponible** | Abierta, preexistente. `capabilities.py:123-124` la marca `STATUS_AVAILABLE` y el comentario de `:145-147` afirma que «nada la aplica todavía», pero `inventory_views.py:1465` la exige. Diseñar roles leyendo sólo `capabilities.py` daría una respuesta equivocada sobre quién emite NV- |
| Cupón sin tope de usos | Abierta, preexistente, comercial: sin contador de canjes ni límite por cliente ni compra mínima |
| Promociones ignoradas en la web | Abierta, preexistente: la misma cesta cuesta distinto por canal, sin avisar |
| `VOID-BOL` / `FISCAL-5C` | Abierta, heredada de 5B |

## 45 · Clasificación final

| Caso | Estado |
|---|---|
| **ECOM-FISCAL-01** selector de comprobante | **IMPLEMENTADO** (preexistente), con la cuestión del preseleccionado en §12 |
| **ECOM-FISCAL-02** disparador de pago | **PARCIAL** — la autoridad y la idempotencia existen; el enganche fiscal no |
| **ECOM-FISCAL-03** autoemisión | **PENDIENTE** — bloqueado por el defecto 1 |
| **ECOM-GRANT-01** | **PENDIENTE** |
| **ECOM-PORTAL-01** | **PENDIENTE** — parte de cero |
| **POS-FISCAL-01** selector de comprobante | **PENDIENTE** — no existe; `receipt_type` nunca se fija |
| **POS-FISCAL-02** disparador de pago | **IMPLEMENTADO** de hecho — la venta nace pagada, con idempotencia |
| **POS-FISCAL-03** autoemisión | **PENDIENTE** — doble bloqueo |
| **POS-GRANT-01** | **PENDIENTE** |
| **RA-FAC** | **IMPLEMENTADO** — aceptado en SUNAT BETA en 5B |
| **VOID-BOL** | **PENDIENTE** — FISCAL-5C |
| **FISCAL-5C** | **PROPUESTA** — en espera de insumos del propietario |

## 46 · Siguiente fase recomendada — NO iniciada

**Recomendada: una fase fiscal intermedia, `ERP-FISCAL-6 — DESCUENTOS DECLARADOS`.**

Es el desbloqueo mínimo, y es pequeño y acotado:

1. **Declarar el descuento en UBL**: `cac:AllowanceCharge` con
   `cbc:ChargeIndicator=false`, su `cbc:Amount` y el `cbc:AllowanceTotalAmount` del
   documento, con el código de motivo del Catálogo N.º 53 — sin rebajar el importe de
   línea, que es justo lo que el núcleo se niega a hacer hoy.
2. **Decidir descuento global frente a descuento por línea.** Los cupones y el
   descuento manual son globales sobre el subtotal; las promociones son por ítem. UBL
   admite ambos y no son el mismo documento. Esta decisión es tributaria, no de
   interfaz.
3. **Cuadrar la aritmética contra el snapshot ya congelado** de la venta, reusando la
   reconciliación por resto mayor de VEN-02A, y **conservar la propiedad de §8**: lo
   que no se sepa declarar se sigue negando, nunca se emite mal.
4. **Fijar `receipt_type` en el POS** (defecto 2), que es pequeño y no depende de lo
   anterior, aunque solo sirve una vez resuelto lo anterior.
5. Cerrar con humo BETA de una factura y una boleta **con descuento**.

Hecho eso, `ERP-SALES-FISCAL-UI` se retoma y queda casi todo cuesta abajo: el
selector web ya está, el disparador y su idempotencia ya están, la frontera de emisión
ya está, y el mecanismo de otorgamiento ya está. Quedaría el enganche `on_commit`, el
selector del POS, el portal del cliente y el otorgamiento automático.

**Alternativa, si se prefiere valor visible antes:** implementar sólo el camino SIN
descuento, con los caminos con descuento clasificados `FISCAL_UNSUPPORTED` y la venta
conservada como «pagada, comprobante pendiente». **No la recomiendo.** Dejaría que el
sistema falle sistemáticamente en un camino corriente del negocio, y §5 existe
precisamente para no hacer eso.

Ninguna de las dos se inicia sin autorización explícita.

---

**STOP** (§48). Sin producción SUNAT, sin FISCAL-5C, sin reglas nuevas de descuento,
sin GRE, sin Consulta Integrada, sin reembolsos, sin devoluciones de stock, sin merge
y sin push. A la espera de tu revisión.
