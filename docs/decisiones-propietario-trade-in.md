# TRADE-IN-01 · Decisiones que necesita el propietario

Estado: **PROPUESTA — nada implementado.** Resume lo que hay que decidir antes de construir «equipo recibido como parte
de pago». El diseño completo y la evidencia están en [adr-trade-in.md](adr-trade-in.md). Esto **no es asesoría
tributaria ni legal**: lo marcado «contador» o «legal» debe validarlo esa persona antes de tocar boletas, facturas, IGV
o la emisión electrónica. Ninguna de estas decisiones cambia hoy la facturación ni SUNAT.

**Qué ya está resuelto y no hay que decidir:** un trade-in no es un descuento (nunca `manual_discount`); el equipo
recibido es una compra aparte; el total, la base y el IGV de la venta no cambian; el seminuevo se vende en caja a su
precio propio (INV-COM-01C). Lo que sigue es lo que sí cambia según la respuesta.

**Cómo leerlo.** *Recomendación* es la opción que el equipo técnico propone y por qué; si el propietario o el contador
dicen otra, el diseño se ajusta. *Responsable* es quien debe responder, no quien programa.

---

### D1 · Qué comprobante se emite con una venta que lleva parte de pago
- **Decisión:** qué documento ve el cliente y qué dice, mientras SUNAT BETA siga apagado y después.
- **Opciones:** (A) comprobante por el **valor total** del equipo vendido, forma de pago contado, más una liquidación
  comercial interna con el desglose; (B) facturar solo el saldo en dinero; (C) mientras SUNAT esté apagado, solo nota de
  venta interna + liquidación interna.
- **Recomendación técnica:** A para el diseño, C mientras no haya emisión. B no.
- **Riesgos:** B subdeclara base e IGV y esconde la compra del usado. C deja la venta sin boleta o factura, que el
  Reglamento de Comprobantes exige: es temporal y debe saberse.
- **Responsable:** propietario + **contador**.

### D2 · Con qué documento se sustenta la compra del equipo usado
- **Decisión:** qué papel respalda que la tienda compró el equipo a un particular, y a qué costo contable.
- **Opciones:** Formulario 820 tramitado por el vendedor; factura si el cliente tiene RUC; contrato o declaración jurada
  interna firmada.
- **Recomendación técnica:** guardar siempre un documento interno firmado + los datos de D6, y que el contador diga cuál
  vale. La liquidación de compra casi seguro no aplica a celulares.
- **Riesgos:** sin sustento, el costo del equipo puede no ser deducible; el Formulario 820 puede no ser practicable en
  mostrador.
- **Responsable:** **contador**.

### D3 · ¿Se acepta un trade-in con cliente anónimo?
- **Decisión:** si quien entrega el equipo debe estar identificado en el sistema.
- **Opciones:** (a) siempre cliente identificado; (b) anónimo solo bajo un monto; (c) anónimo permitido.
- **Recomendación técnica:** (a). Es la única forma de tener quién respondió por la propiedad del equipo.
- **Riesgos:** (c) deja sin responsable un equipo posiblemente robado (receptación). (a) frena ventas de mostrador rápidas.
- **Responsable:** propietario (con **legal** si se elige b o c).

### D4 · ¿El crédito puede igualar o superar el total de la venta?
- **Decisión:** si la valorización puede cubrir toda la venta, y qué pasa con el sobrante.
- **Opciones:** (a) tope: crédito ≤ total, nunca vuelto; (b) permitir crédito = total (sin dinero); (c) permitir
  sobrante pagado en dinero o saldo a favor.
- **Recomendación técnica:** (b) con tope estricto (a) sobre el total; el sobrante no existe.
- **Riesgos:** (c) convierte la caja en una recompra de equipos con salida de dinero: otro proceso, otro control.
- **Responsable:** propietario.

### D5 · Método de pago cuando todo se paga en especie
- **Decisión:** qué valor lleva `payment_method` si el efectivo a cobrar es cero.
- **Opciones:** (a) un valor nuevo; (b) vacío/«ninguno» solo en esa combinación; (c) conservar el método elegido con monto 0.
- **Recomendación técnica:** (b) o (c). (a) toca contratos ajenos: el campo lo comparten reparaciones, obligaciones y
  la API v1.
- **Riesgos:** (a) rompe tres superficies a la vez; (c) puede confundir un reporte de caja.
- **Responsable:** propietario + diseño.

### D6 · Qué datos y pruebas se guardan de quien entrega el equipo
- **Decisión:** DNI, firma, declaración de propiedad, consulta de IMEI; cuánto tiempo se conservan.
- **Opciones:** mínimo (DNI + declaración); recomendado (DNI + firma + declaración + resultado y fecha de la consulta de
  IMEI); ampliado (más copia del documento).
- **Recomendación técnica:** el recomendado, guardando **solo** esos datos y mostrándolos enmascarados.
- **Riesgos:** guardar de más es un riesgo de datos personales; guardar de menos no protege a la tienda.
- **Responsable:** propietario + **legal** (retención y datos personales).

### D7 · Quién valora y quién aprueba
- **Decisión:** capacidades nuevas, límite de valorización por rol y quién aprueba por encima del límite.
- **Opciones:** (a) cualquiera con caja puede valorar; (b) valorar con capacidad propia hasta un límite y aprobar con otra
  capacidad (autoridades distintas, como la devolución); (c) solo el administrador.
- **Recomendación técnica:** (b); ningún rol gana el poder por defecto, se otorga a mano.
- **Riesgos:** (a) permite fijar una valorización inflada; (c) ahoga el mostrador.
- **Responsable:** propietario.

### D8 · Qué se devuelve si se cancela o se devuelve una venta con parte de pago
- **Decisión:** al cliente, ¿dinero, crédito de tienda, su equipo, o nada?
- **Opciones:** (a) se devuelve el equipo recibido (si sigue en la tienda) y el dinero cobrado; (b) se devuelve solo el
  dinero cobrado y el equipo queda con la tienda; (c) se devuelve la valorización en dinero.
- **Recomendación técnica:** (a), y si el equipo ya se movió, se bloquea y se resuelve a mano con rastro. Reembolso
  máximo = dinero cobrado (más lo que diga esta decisión).
- **Riesgos:** (c) puede pagar más de lo recibido; (b) puede ser reclamado por el cliente.
- **Responsable:** propietario (con **legal** si hay reclamos).

### D9 · Un equipo que vuelve y figuraba como vendido por esta tienda
- **Decisión:** qué hacer cuando el IMEI que entra ya existe como vendido por nosotros.
- **Opciones:** (a) bloquear siempre; (b) permitir como devolución de garantía enlazando la venta original; (c) permitir
  como equipo nuevo del stock con aviso.
- **Recomendación técnica:** (b) con enlace a la venta original; nunca duplicar la identidad.
- **Riesgos:** (c) rompe la trazabilidad de la unidad; (a) bloquea casos legítimos de garantía.
- **Responsable:** propietario.

### D10 · Consulta de IMEI y qué hacer con un IMEI bloqueado descubierto después
- **Decisión:** si la consulta es manual o integrada, y el procedimiento si luego sale en lista negra.
- **Opciones:** (a) manual por el empleado, con el resultado guardado; (b) integración automática; (c) ninguna.
- **Recomendación técnica:** (a). Ningún IMEI sale a un proveedor externo sin aprobación expresa.
- **Riesgos:** (c) acepta equipos reportados; (b) envía datos a un tercero. Un equipo bloqueado descubierto después
  necesita un procedimiento (¿se revierte la compra?) que hoy no existe.
- **Responsable:** propietario + **legal**.

### D11 · Base de la comisión del vendedor con crédito
- **Decisión:** si la comisión se calcula sobre el total vendido o sobre el dinero cobrado.
- **Opciones:** (a) sobre subtotal − descuento (hoy); (b) sobre el dinero cobrado; (c) sobre el margen.
- **Recomendación técnica:** (a) hasta que se decida otra cosa: no cambia nada hoy y no castiga al vendedor.
- **Riesgos:** (a) paga comisión sobre un valor que no entró en caja; (b) desincentiva el trade-in.
- **Responsable:** propietario.

### D12 · ¿Cuándo se puede vender el equipo recibido?
- **Decisión:** si queda disponible al aprobarse o solo tras una revisión técnica.
- **Opciones:** (a) disponible al aprobar; (b) disponible solo después de la revisión técnica o el control de calidad.
- **Recomendación técnica:** (b) para equipos con batería o estética dudosas; el precio propio (INV-COM-01C) debe estar
  puesto antes de venderlo.
- **Riesgos:** (a) vende un equipo sin revisar; (b) lo inmoviliza y demora la rotación.
- **Responsable:** propietario.

---

## Quién puede cerrar qué

| Quién | Decisiones |
|---|---|
| **Equipo técnico (ya cerradas en el ADR, reversibles hasta implementar)** | Un intake no es único por venta; `pending_approval` forma parte de la máquina de estados; el crédito es un campo propio de la orden (`trade_in_credit`) y **nunca** `manual_discount` ni un valor nuevo de `PaymentMethod`; la huella de idempotencia incluye los intakes y no los precios; los movimientos se compensan, no se borran; ningún IMEI en auditoría ni respuestas, máscara en documentos; primero recepción y valorización (01B), después el crédito en caja (01C). |
| **Propietario** | D3, D4, D5, D7, D8, D9, D11, D12 y, junto con legal, D6 y D10. |
| **Contador** | D1 y D2 (más la afectación del usado y la base de la comisión si afecta a cuentas). |
| **Legal** | D6 (datos y retención), D10 (consulta de IMEI y bloqueos descubiertos después), y D3 si se admite cliente anónimo. |

## Propuesta operativa recomendada (para aprobar o corregir; no es una decisión tomada)

Si el propietario y el contador aceptan las recomendaciones de arriba, el día a día sería:

1. El vendedor arma la venta en la caja como siempre. Si el cliente trae un equipo, abre «Recibir equipo en parte de pago» (hoy la caja sólo lo anuncia, deshabilitado).
2. **Identifica al cliente** (D3-a: obligatorio) y **al equipo** por serie e IMEI; el sistema rechaza un IMEI repetido y, si el equipo ya figuraba como vendido por la tienda, lo trata como garantía enlazada a la venta original (D9-b).
3. Se **revisan** batería, estado estético y funcionamiento, y se guardan la declaración de propiedad, la firma y el resultado y la fecha de la consulta manual de IMEI (D6, D10-a).
4. Quien tiene la capacidad de **valorar** propone el importe; si excede su límite o no tiene la capacidad, queda `pending_approval` hasta que otra persona apruebe (D7-b). Mientras no se aprueba, **el equipo no existe en el inventario** y no hay crédito.
5. Aprobada la valorización, la venta puede **cobrarse**: el total, la base y el IGV de la venta **no cambian**; el crédito (nunca mayor que el total, D4) se resta del dinero a cobrar y la unidad recibida entra al inventario por la entrada oficial de compra, en la misma operación y sin crear ninguna salida paralela. Si el cobro falla, no queda ni unidad ni crédito.
6. El equipo recibido **no se vende** hasta pasar la revisión técnica y tener su precio propio (D12-b, INV-COM-01C).
7. El comprobante: nota de venta interna con el bloque «Parte de pago» (equipo enmascarado, valorización, crédito, dinero y saldo) y, mientras SUNAT esté apagado, **sin emitir** boleta ni factura (D1-C); cuando el contador decida, comprobante por el valor total (D1-A). La compra del usado se sustenta como determine el contador (D2).
8. Si luego se cancela o se devuelve la venta: se devuelve el equipo recibido si sigue en la tienda y el dinero cobrado (D8-a); si el equipo ya se movió, la cancelación se bloquea y se resuelve a mano con rastro. La comisión sigue calculándose como hoy (D11-a).

## Estado

**TRADE-IN-01 sigue en PROPUESTA y BLOQUEADO POR DECISIÓN.** TRADE-IN-01B (recepción y valorización) no se construye mientras no estén aprobadas D2, D3, D6, D7, D9 y D10; TRADE-IN-01C (crédito en caja, documentos y reversión) tampoco mientras no lo estén D1, D4, D5, D8 y D11. La caja anuncia «Equipo como parte de pago» **deshabilitado** (SALES-UX-02): no registra ningún crédito, ningún stock ni ningún pago.

## Orden en que conviene decidir

1. **D1, D2** (contador) — condicionan los documentos; sin ellas no se toca nada fiscal.
2. **D3, D6, D10** (propietario + legal) — condicionan qué se pide al cliente en el mostrador.
3. **D7, D9** — condicionan la recepción y la valorización (TRADE-IN-01B).
4. **D4, D5, D8, D11, D12** — condicionan la caja, el crédito y las devoluciones (TRADE-IN-01C).

TRADE-IN-01B no se construye sin D2, D3, D6, D7, D9 y D10. TRADE-IN-01C no, sin D1, D4, D5, D8 y D11. Hasta entonces
no hay migración ni cambio de caja por este tema.
