# Nota de venta: verificación tributaria y salvaguardas (NOTE-NOT-SUBSTITUTE)

**Fecha:** 2026-11-19. **Pedido del propietario:** «documentar la verificación tributaria de las identificaciones necesarias y asegurar que el sistema no entregue la nota como sustituto del comprobante correspondiente».
**Decisión vigente que no cambia:** la nota de venta conserva su diseño limpio, **sin leyenda ni aviso impreso** en ningún formato (A4, ticket de 80 mm, ESC/POS, copia por correo). Todos los controles viven en las pantallas del personal: caja, panel y seguimiento de ventas. Registro de esa decisión: [nota-de-venta-identificacion.md](nota-de-venta-identificacion.md).
**No es asesoría tributaria.** Lo marcado como pendiente debe confirmarlo el contador.

---

## Aviso operativo para el propietario

> **Mientras la facturación electrónica del sistema esté desactivada en producción, el sistema NO emite boletas ni facturas.**
> Cada venta debe recibir su comprobante electrónico **por otro canal de SUNAT** (SEE-SOL con Clave SOL, aplicativo de SUNAT u otro proveedor) **al cobrar o al entregar, lo que ocurra primero**, también por cada adelanto o pago parcial.
> La nota de venta es un documento interno: **nunca reemplaza** a la boleta o factura, y entregarla en su lugar es la infracción del art. 174 num. 1 del Código Tributario (sancionada con cierre).

- Cómo saber si la integración está apagada: `manage.py ops_status` y `deploy/healthcheck.sh` imprimen la línea **«SUNAT · Facturación electrónica: desactivada»** (`store/integrations/health.py`). Además, esta versión **sólo sabe operar el entorno BETA (pruebas) de SUNAT** (`fiscal_config.resolve_environment` falla cerrado ante producción): aunque se encienda, lo que emite el sistema no tiene validez tributaria.
- El sistema **no garantiza** que el comprobante se entregue: depende de un proceso humano externo que el sistema no puede verificar. Lo que sí hace es avisar, listar lo pendiente y dejar registrar lo emitido fuera para conciliarlo.

---

## 1. Verificación de normas

Fuentes primarias consultadas el 2026-11-18 (detalle y enlaces en [nota-de-venta-identificacion.md](nota-de-venta-identificacion.md), sección «Base legal»).

### 1.1 Requisitos confirmados (obligatorios)

| Requisito | Norma | Confianza |
|---|---|---|
| Emitir u otorgar un documento no previsto como comprobante **en lugar** del comprobante es infracción. | TUO del Código Tributario (D.S. 133-2013-EF), art. 174 num. 1, Tabla I (cierre, notas 3 y 3-A) | Alta |
| Para un emisor electrónico, no emitir el CPE por el SEE al terminar la operación, o entregar en su lugar un documento no previsto, es esa infracción. | Informe N.° 127-2020-SUNAT/7T0000, conclusiones 1 y 5 | Alta |
| Venta de bienes: el comprobante se emite al entregar el bien o al recibir el pago, **lo que ocurra primero**. En ventas por internet con pago previo, al recibir la conformidad del pago. | Reglamento de Comprobantes de Pago (R.S. 007-99/SUNAT), art. 5 num. 1 | Alta |
| Adelantos y pagos parciales antes de la entrega: comprobante en la fecha y por el monto recibido. | RCP, art. 5 num. 4 | Alta |
| Servicios (servicio técnico): al terminar el servicio, al percibir la retribución (total o parcial, por el monto percibido) o al vencer el plazo, lo que ocurra primero. | RCP, art. 5 num. 5 | Media (cita de memoria; confirmar el texto vigente) |
| La obligación existe aunque la operación no esté gravada. | RCP, art. 5, último párrafo | Alta |
| Boleta electrónica: no entregar su representación impresa (o el envío electrónico) también es la infracción del num. 1. | Informe N.° 127-2020, conclusiones 2 y 3 | Alta |
| Las series de los CPE del SEE empiezan por F, B, L o G (SEE-SOL usa E001 / EB01); las de contingencia son numéricas. | Anexo de la R.S. 244-2019/SUNAT; manuales de SEE-SOL | Alta en la regla F/B; Media en las series de SEE-SOL |

### 1.2 Lo que **no** exige ninguna norma

- Una leyenda en un documento interno. El RCP regula sólo comprobantes (lista cerrada del art. 2); sus leyendas son para comprobantes y sus copias. Confianza media-alta (búsqueda negativa; el cap. VI del RCP no se pudo revisar).
- Una apariencia determinada para la nota. Parecerse a un comprobante (título, serie F/B, QR, «boleta»/«factura») sólo **aumenta** el riesgo del art. 174.1 (inferencia, confianza media).

### 1.3 Controles recomendados (prudentes, no obligatorios)

1. Que la nota no se parezca a un comprobante: aplicado (ver la decisión de identificación: sin vocabulario fiscal, prefijo NV- nunca F/B, sin QR).
2. Que el personal sepa, venta por venta, cuándo falta el comprobante y por qué canal emitirlo: **aplicado en esta entrega** (sección 3).
3. Conciliar las ventas cobradas con los comprobantes emitidos en SUNAT: **aplicado** (lista de ventas sin comprobante + registro del comprobante externo, sección 4).
4. Entregar la nota sólo junto con el comprobante o después de él, o dejarla como registro interno: **pendiente de decisión** (sección 6).

---

## 2. Auditoría de flujos (lectura del código, sin acceso a producción)

«CPE» = boleta o factura electrónica. «Integración desactivada» = el estado actual de producción.

| Flujo | Cuándo nace la obligación del CPE | ¿Emite el sistema el CPE? | Con la integración desactivada | ¿Se entrega la nota? | Riesgo |
|---|---|---|---|---|---|
| **Caja (POS) al contado** — efectivo, tarjeta, transferencia u otro | Al cobrar (cobro y entrega son simultáneos). | Sólo si en la caja se elige boleta/factura **y** la integración está activa: se prepara en la misma transacción de la venta, en BETA (sin validez). | La caja **rechaza** boleta y factura («La emisión fiscal no está habilitada», 400). La venta se completa **sólo con nota de venta o sin documento**. | **Sí.** Si el local tiene impresora automática, el ticket de la nota se encola solo; si no, «Imprimir ticket» / «PDF A4». | **Alto** si no se emite el CPE por SOL en ese momento. Mitigado con el aviso de la caja (sección 3). |
| **Caja bajo pedido** (proveedor, entrega posterior) | Al cobrar en caja (el pago va primero). | Igual que la fila anterior. | Igual: sólo nota o nada. | Sí, al cobrar. | **Alto**, igual que arriba; la entrega posterior no cambia la fecha. |
| **Tienda web pagada** (pasarela) | Al recibir la conformidad del pago (RCP art. 5.1, ventas por internet). | **No automáticamente.** El cliente elige boleta o factura (`receipt_type`, «comprobante solicitado»); el personal puede emitirla desde la ficha del pedido sólo con la integración activa (BETA). | No se puede emitir desde el sistema. | **No automáticamente.** El cliente recibe la confirmación con la «Constancia de pedido» en PDF, que dice «Documento interno de compra. No válido como comprobante electrónico SUNAT». El personal puede generar e imprimir la nota a mano. | **Medio-alto**: el CPE debe emitirse fuera, al confirmarse el pago. Listado en «Ventas sin comprobante electrónico». |
| **Tienda web cobrada con claves de pruebas** (estado actual de la pasarela) | No hay cobro real: no nace. | Rechazado en producción (regla H-11). | — | No. | Bajo. El sistema lo marca «Cobrada con las claves de pruebas…» y no lo lista como pendiente. |
| **Pedido sin pagar, fallido o expirado** | No nace (no hubo pago ni entrega). | No. | — | No: la nota sólo se emite sobre pedidos pagados. | Nulo. El despacho no avanza sin pago (`order_shortfall.not_paid_detail`). |
| **Entrega / despacho del pedido** | Ya nació al cobrar (el pago siempre va primero). | No interviene. | El despacho **no exige** el CPE. | Puede imprimirse la nota en cualquier momento. | Medio: decidir si el despacho debe pedir el comprobante (sección 6). |
| **Reservas de equipos** (carrito web, «apartado» del personal) | No nace: no hay cobro ni entrega. | No. | — | No. | Bajo. **Pero** si se cobra un adelanto por un apartado fuera del sistema, nace el CPE por ese monto (art. 5.4) y el sistema no lo ve. |
| **Cotizaciones** (servicio técnico, cotización de la tienda) | No es una venta. | No. | — | No. El ticket dice «No es un comprobante de pago ni un documento tributario». | Nulo. |
| **Servicio técnico: cobros, adelantos y saldos** (`RepairPayment`) | En cada cobro, por el monto cobrado (art. 5 num. 4 y 5). | **No.** El sistema no emite comprobantes de servicio. | — | No hay nota de venta de servicio. | **Alto**: cada cobro necesita su CPE externo. El centro contable lo marca «Cobro de servicio técnico sin comprobante»; el registro externo de esta entrega es sólo para ventas (pendiente para servicios). |
| **Cancelación o devolución de una venta pagada** | El CPE nació al cobrar; luego corresponde nota de crédito. | Notas de crédito sólo en BETA. | — | La nota de venta queda anulada con su número. | Medio: lo lista el centro contable (vista C). Fuera del alcance de esta entrega. |
| **Copia por correo** («Enviar documentos a otro correo») | — | — | La copia de la factura/boleta sólo sale si es de producción, aceptada y no anulada (hoy: nunca). | Sí, si el personal la envía: asunto «Nota de venta NV-…», sin palabras fiscales ni aviso (probado). | Bajo: nunca se llama «comprobante» ni se ofrece en lugar del CPE. |
| **Cuenta del cliente** (web y app) | — | — | — | No: el cliente sólo lee sus pedidos; no descarga la nota ni el comprobante. | Nulo. |

**Ningún flujo emite, imprime o envía la nota como «boleta» o «factura»**, ni la usa para dar por cumplida la obligación: la nota no tiene vocabulario fiscal (probado en sus cuatro salidas) y ningún estado del sistema pasa a «con comprobante» por tener nota. Lo que **sí** ocurre hoy es que una venta puede completarse con sólo la nota (caja) o sin documento: ése es el riesgo que cubren las salvaguardas.

---

## 3. Salvaguardas implementadas (sólo para el personal)

Ninguna toca la nota impresa, la emisión fiscal, la numeración ni las credenciales. Ninguna afirma que un comprobante se emitió sin evidencia.

1. **Una sola respuesta a «¿tiene comprobante esta venta?»** — `backend/store/receipt_coverage.py`. Sólo cuenta como evidencia una boleta o factura del sistema **de producción** aceptada (o informada por un resumen diario aceptado) y no dada de baja. Una nota de venta, un comprobante BETA o el «comprobante solicitado» **no** son evidencia. Estados: `system`, `external` (registrado a mano, sección 4), `pending`, `not_due` (sin cobro), `cancelled`, `test_payment`.
2. **Aviso en la caja.** Al elegir «Nota de venta» con la facturación del sistema no disponible: «al cobrar, emite la boleta o factura por SUNAT (SOL u otro medio)». Al terminar la venta, la pantalla de «Venta registrada» muestra, con el texto del servidor: «Esta venta no tiene comprobante electrónico emitido por el sistema: emite la boleta o factura por SUNAT (SOL u otro medio) al cobrar o entregar. La facturación electrónica del sistema está desactivada; la nota de venta no reemplaza al comprobante.»
3. **Aviso en la ficha del pedido y en las notas de venta** (panel del pedido y detalle en «Notas de venta»), con el mismo texto. Con la integración encendida el texto aclara que el sistema opera sólo en el entorno de pruebas.
4. **«Ventas sin comprobante electrónico»** — filtro en «Órdenes» (`?receipt=pending`, el contador es el total del listado) y en «Notas de venta», y una marca en cada fila. Sólo ventas pagadas y vigentes de la empresa (y sucursales) de quien consulta.
5. **Copia por correo de la nota** — ya garantizado y probado antes de esta entrega: asunto «Nota de venta NV-…», cuerpo neutro, sin «comprobante», «boleta», «factura», «SUNAT» ni «interna», con o sin la plantilla de correo de la empresa (`test_document_legends`, `test_order_document_copy`). No se cambió.
6. **Healthcheck** — la línea «SUNAT · Facturación electrónica: desactivada» ya existía; esta guía la cita como la señal del estado.

Pruebas: `backend/store/test_note_not_substitute.py`, `frontend/__tests__/receipt-coverage-notice.test.tsx`, `frontend/__tests__/external-receipt-panel.test.tsx`.

---

## 4. Registro del comprobante emitido fuera del sistema

En la ficha del pedido, panel «Comprobante emitido fuera del sistema» (`backend/store/external_receipts.py`, modelo `ExternalFiscalReceipt`, migración `0179_external_fiscal_receipt`).

- **Datos:** tipo (boleta 03 / factura 01), serie (4 caracteres; una factura no lleva serie B ni una boleta serie F), número (1–99999999), fecha de emisión (no futura), medio (SEE-SOL, aplicativo de SUNAT, otro proveedor, otro) y una **referencia verificable** (consulta en SOL, enlace, archivo o identificador).
- **Permisos:** ver con `sales.fiscal.view`; registrar y anular con `sales.fiscal.issue` — las capacidades fiscales existentes, las más cercanas (no se creó una nueva). Con los roles de fábrica, «Administrador» puede registrar y anular y «Ventas» sólo ver; quien cobra en caja necesita que un administrador le dé `sales.fiscal.issue` si debe registrar él mismo. Sin puente de roles antiguos; el pedido se resuelve con `visible_orders()`, así que otra empresa u otra sucursal responden 404.
- **Duplicados:** un registro vigente por venta, y uno por empresa + tipo + serie + número (reglas también en la base de datos). Se rechaza un número que el propio sistema emitió en producción (un número de prueba BETA no es un comprobante).
- **Corrección:** un registro equivocado se **anula con motivo** (al menos 10 caracteres); no se edita ni se borra. La venta vuelve a «pendiente».
- **Bitácora:** `external_receipt_registered` y `external_receipt_voided` en `AdminAuditLog`, con pedido, tipo, serie, número, fecha y medio.
- **Lo que nunca hace:** no consulta SUNAT, no guarda un estado «aceptado», no se imprime ni se envía al cliente. La venta se lee **«comprobante externo registrado (no verificado en SUNAT por el sistema)»** y sale de «Ventas sin comprobante electrónico». Si la fecha de emisión es posterior al día del cobro, el panel lo dice («el comprobante correspondía al cobrar o entregar») sin juzgarlo.
- **Centro contable (vista C):** una venta con registro externo deja de figurar como «sin comprobante» / «sólo nota interna» / «se pidió y no se emitió» y aparece como información: «Comprobante emitido fuera del sistema y registrado a mano: el sistema no verifica su aceptación en SUNAT.»

---

## 5. Lo que el sistema no garantiza

- Que el comprobante externo exista, sea correcto o esté aceptado en SUNAT: es lo que una persona escribió. La conciliación con SOL la hace una persona.
- Que el personal lea el aviso y emita el comprobante a tiempo.
- Los cobros de **servicio técnico** no tienen todavía registro externo: siguen listados en el centro contable.

---

## 6. Pendiente con el contador o el propietario

1. Confirmar, flujo por flujo (tabla de la sección 2), el momento de emisión, en especial adelantos de servicio técnico y cualquier adelanto por un «apartado».
2. Si la nota debe entregarse al cliente o quedar como registro interno; y si la caja debe imprimir la nota automáticamente cuando la venta no tiene comprobante.
3. Si el despacho o la entrega deben **bloquearse** mientras la venta no tenga comprobante (hoy sólo se avisa).
4. Si la ausencia total de leyenda en la nota es aceptable (decisión del propietario vigente; la investigación recomendaba una frase mínima).
5. Si el registro externo debe ampliarse a los cobros de servicio técnico, guardar el importe del comprobante o exigir un adjunto (PDF/XML de SOL).
6. Texto vigente del RCP art. 5 num. 5 (servicios) y régimen de gradualidad y discrecionalidad del art. 174.1.
7. Riesgos de protección al consumidor (Ley 29571), no revisados.
8. La confirmación por correo al cliente dice «Comprobante: Boleta/Factura» (el tipo **solicitado**) junto a la constancia en PDF, que aclara que no es comprobante. Decidir si conviene escribir «Comprobante solicitado».
