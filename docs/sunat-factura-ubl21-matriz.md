# Matriz de conformidad — Factura electrónica UBL 2.1

**Fase C2.2A.1.** Generada el 6 de septiembre de 2026 a partir del XML que
realmente emite `store/fiscal/builder.py` para el caso mínimo.

Esta matriz existe para poder responder **«¿por qué este nodo está aquí?»**
sin decir «porque vi un XML en GitHub». Cada fila cita su autoridad.

## Caso mínimo

Venta interna gravada · factura · PEN · una línea · 100,00 + 18,00 = 118,00.
Sin descuento, anticipo, percepción, detracción, ISC, gratuitas, exportación,
exoneradas ni inafectas. Es deliberadamente el documento más pequeño posible.

Los **descuentos** se declaran desde ERP-FISCAL-6 y tienen su propia sección más
abajo («Descuentos declarados»); el caso mínimo sigue sin ellos a propósito.

## Abreviaturas de fuente

| Clave | Documento |
|---|---|
| XSD | `UBL-Invoice-2.1.xsd`, versionado en `backend/store/fiscal/schemas/` |
| A1 | Anexo N.º 1 — Factura electrónica (R.S. 123-2022) |
| A6 | Anexo N.º 6 — Aspectos técnicos SEE (R.S. 000048-2026, vigente 1.8.2026) |
| A8 | Anexo N.º 8 — Catálogo de códigos |
| RV | Reglas de validación, actualizado al 26.08.2026 (hoja `Factura2_0`) |
| RV-2025 | «Reglas de validación de CPE», archivo oficial `AjustesValidacionesCPEv20250421.xlsx` (cpe.sunat.gob.pe), hojas `Factura2_0`, `Boleta2_0` y `Catálogos` — fuente de la sección de descuentos |
| guía | Guía de Elaboración de Documentos XML — Factura, UBL 2.1 |

Las URL y las fechas de consulta están en [sunat-cpe-requisitos.md](sunat-cpe-requisitos.md).

## Nodos emitidos, en orden documental

El orden es el del esquema. **UBL es una secuencia XSD**: los mismos campos
en otro orden son un documento inválido, y hay un test que lo comprueba.

| # | XPath | Card. | Valor en el caso mínimo | Atributos | Fuente | Por qué |
|---|---|---|---|---|---|---|
| 1 | `/Invoice` | — | — | — | XSD | Campo del esquema; su presencia la impone el padre. |
| 2 | `/Invoice/ext:UBLExtensions` | 1..1 | — | — | XSD | Contenedor de extensiones. El esquema lo exige y es donde vive la firma. |
| 3 | `/Invoice/ext:UBLExtensions/ext:UBLExtension` | 1..n | — | — | XSD | Dos: la primera lleva la información adicional; la segunda, la firma. |
| 4 | `/Invoice/ext:UBLExtensions/ext:UBLExtension/ext:ExtensionContent` | 1..1 | — | — | XSD | Admite contenido de otro espacio de nombres. Por eso caben `sac:` y `ds:`. |
| 5 | `/Invoice/ext:UBLExtensions/ext:UBLExtension/ext:ExtensionContent/sac:AdditionalInformation` | 0..1 | — | — | A8 | Contenedor de la información adicional de SUNAT. |
| 6 | `/Invoice/ext:UBLExtensions/ext:UBLExtension/ext:ExtensionContent/sac:AdditionalInformation/sac:SUNATTransaction` | 0..1 | — | — | A8 cat. 17 | Tipo de operación. `01` = venta interna. |
| 7 | `/Invoice/ext:UBLExtensions/ext:UBLExtension/ext:ExtensionContent/sac:AdditionalInformation/sac:SUNATTransaction/cbc:ID` | 1..1 | 01 | — | A1 campo 8 | Serie-correlativo, `an..13`, formato `<Serie>-<Número>`. |
| 8 | `/Invoice/ext:UBLExtensions/ext:UBLExtension` | 1..n | — | — | XSD | Dos: la primera lleva la información adicional; la segunda, la firma. |
| 9 | `/Invoice/ext:UBLExtensions/ext:UBLExtension/ext:ExtensionContent` | 1..1 | — | — | XSD | Admite contenido de otro espacio de nombres. Por eso caben `sac:` y `ds:`. |
| 10 | `/Invoice/cbc:UBLVersionID` | 0..1 | 2.1 | — | XSD + guía | `2.1`. Declara la versión del estándar. |
| 11 | `/Invoice/cbc:CustomizationID` | 0..1 | 2.0 | — | guía | `2.0`. Es la personalización peruana, no la versión de UBL. |
| 12 | `/Invoice/cbc:ID` | 1..1 | F001-1 | — | A1 campo 8 | Serie-correlativo, `an..13`, formato `<Serie>-<Número>`. |
| 13 | `/Invoice/cbc:IssueDate` | 1..1 | 2026-09-06 | — | A1 campo 1 | `yyyy-mm-dd`. |
| 14 | `/Invoice/cbc:IssueTime` | 0..1 | 12:00:00 | — | guía | `hh:mm:ss`. |
| 15 | `/Invoice/cbc:InvoiceTypeCode` | 0..1 | 01 | `listID="01"` | A8 cat. 01 | `01` factura. El `listID` lleva el tipo de operación (cat. 51). |
| 16 | `/Invoice/cbc:Note` | 0..n | CIENTO DIECIOCHO CON 00/10… | `languageLocaleID="1000"` | A8 cat. 52 | Importe en letras, con `languageLocaleID="1000"`. |
| 17 | `/Invoice/cbc:DocumentCurrencyCode` | 0..1 | PEN | — | A8 cat. 02 | ISO 4217. `PEN`. |
| 18 | `/Invoice/cac:Signature` | 0..n | — | — | A1 campo 9 | Bloque declarativo de firma; apunta al `ds:Signature` por URI. |
| 19 | `/Invoice/cac:Signature/cbc:ID` | 1..1 | F001-1 | — | A1 campo 8 | Serie-correlativo, `an..13`, formato `<Serie>-<Número>`. |
| 20 | `/Invoice/cac:Signature/cac:SignatoryParty` | — | — | — | XSD | Campo del esquema; su presencia la impone el padre. |
| 21 | `/Invoice/cac:Signature/cac:SignatoryParty/cac:PartyIdentification` | 0..n | — | — | A8 cat. 06 | `schemeID` = `6` (RUC). |
| 22 | `/Invoice/cac:Signature/cac:SignatoryParty/cac:PartyIdentification/cbc:ID` | 1..1 | 20100066603 | — | A1 campo 8 | Serie-correlativo, `an..13`, formato `<Serie>-<Número>`. |
| 23 | `/Invoice/cac:Signature/cac:SignatoryParty/cac:PartyName` | — | — | — | XSD | Campo del esquema; su presencia la impone el padre. |
| 24 | `/Invoice/cac:Signature/cac:SignatoryParty/cac:PartyName/cbc:Name` | — | ENTORNO DE PRUEBAS SAC | — | XSD | Campo del esquema; su presencia la impone el padre. |
| 25 | `/Invoice/cac:Signature/cac:DigitalSignatureAttachment` | — | — | — | XSD | Campo del esquema; su presencia la impone el padre. |
| 26 | `/Invoice/cac:Signature/cac:DigitalSignatureAttachment/cac:ExternalReference` | — | — | — | XSD | Campo del esquema; su presencia la impone el padre. |
| 27 | `/Invoice/cac:Signature/cac:DigitalSignatureAttachment/cac:ExternalReference/cbc:URI` | — | #SignatureSP | — | XSD | Campo del esquema; su presencia la impone el padre. |
| 28 | `/Invoice/cac:AccountingSupplierParty` | 1..1 | — | — | A1 | El emisor. |
| 29 | `/Invoice/cac:AccountingSupplierParty/cac:Party` | — | — | — | XSD | Campo del esquema; su presencia la impone el padre. |
| 30 | `/Invoice/cac:AccountingSupplierParty/cac:Party/cac:PartyIdentification` | 0..n | — | — | A8 cat. 06 | `schemeID` = `6` (RUC). |
| 31 | `/Invoice/cac:AccountingSupplierParty/cac:Party/cac:PartyIdentification/cbc:ID` | 1..1 | 20100066603 | `schemeID="6"` | A1 campo 8 | Serie-correlativo, `an..13`, formato `<Serie>-<Número>`. |
| 32 | `/Invoice/cac:AccountingSupplierParty/cac:Party/cac:PartyLegalEntity` | — | — | — | XSD | Campo del esquema; su presencia la impone el padre. |
| 33 | `/Invoice/cac:AccountingSupplierParty/cac:Party/cac:PartyLegalEntity/cbc:RegistrationName` | — | ENTORNO DE PRUEBAS SAC | — | XSD | Campo del esquema; su presencia la impone el padre. |
| 34 | `/Invoice/cac:AccountingSupplierParty/cac:Party/cac:PartyLegalEntity/cac:RegistrationAddress` | — | — | — | XSD | Campo del esquema; su presencia la impone el padre. |
| 35 | `/Invoice/cac:AccountingSupplierParty/cac:Party/cac:PartyLegalEntity/cac:RegistrationAddress/cbc:ID` | 1..1 | 040101 | — | A1 campo 8 | Serie-correlativo, `an..13`, formato `<Serie>-<Número>`. |
| 36 | `/Invoice/cac:AccountingSupplierParty/cac:Party/cac:PartyLegalEntity/cac:RegistrationAddress/cbc:AddressTypeCode` | — | 0000 | — | XSD | Campo del esquema; su presencia la impone el padre. |
| 37 | `/Invoice/cac:AccountingSupplierParty/cac:Party/cac:PartyLegalEntity/cac:RegistrationAddress/cac:AddressLine` | — | — | — | XSD | Campo del esquema; su presencia la impone el padre. |
| 38 | `/Invoice/cac:AccountingSupplierParty/cac:Party/cac:PartyLegalEntity/cac:RegistrationAddress/cac:AddressLine/cbc:Line` | — | AV PRUEBA 123 | — | XSD | Campo del esquema; su presencia la impone el padre. |
| 39 | `/Invoice/cac:AccountingSupplierParty/cac:Party/cac:PartyLegalEntity/cac:RegistrationAddress/cac:Country` | — | — | — | XSD | Campo del esquema; su presencia la impone el padre. |
| 40 | `/Invoice/cac:AccountingSupplierParty/cac:Party/cac:PartyLegalEntity/cac:RegistrationAddress/cac:Country/cbc:IdentificationCode` | — | PE | — | XSD | Campo del esquema; su presencia la impone el padre. |
| 41 | `/Invoice/cac:AccountingCustomerParty` | 1..1 | — | — | A1 campos 11-12 | El adquirente. En factura, RUC obligatorio. |
| 42 | `/Invoice/cac:AccountingCustomerParty/cac:Party` | — | — | — | XSD | Campo del esquema; su presencia la impone el padre. |
| 43 | `/Invoice/cac:AccountingCustomerParty/cac:Party/cac:PartyIdentification` | 0..n | — | — | A8 cat. 06 | `schemeID` = `6` (RUC). |
| 44 | `/Invoice/cac:AccountingCustomerParty/cac:Party/cac:PartyIdentification/cbc:ID` | 1..1 | 20000000001 | `schemeID="6"` | A1 campo 8 | Serie-correlativo, `an..13`, formato `<Serie>-<Número>`. |
| 45 | `/Invoice/cac:AccountingCustomerParty/cac:Party/cac:PartyLegalEntity` | — | — | — | XSD | Campo del esquema; su presencia la impone el padre. |
| 46 | `/Invoice/cac:AccountingCustomerParty/cac:Party/cac:PartyLegalEntity/cbc:RegistrationName` | — | CLIENTE DE PRUEBA SAC | — | XSD | Campo del esquema; su presencia la impone el padre. |
| 47 | `/Invoice/cac:PaymentTerms` | 0..n | — | — | RV Factura2_0 líneas 174-177 | **Su ausencia es el error 3244.** `cbc:ID` debe valer `FormaPago`. |
| 48 | `/Invoice/cac:PaymentTerms/cbc:ID` | 1..1 | FormaPago | — | A1 campo 8 | Serie-correlativo, `an..13`, formato `<Serie>-<Número>`. |
| 49 | `/Invoice/cac:PaymentTerms/cbc:PaymentMeansID` | — | Contado | — | XSD | Campo del esquema; su presencia la impone el padre. |
| 50 | `/Invoice/cac:TaxTotal` | 0..n | — | — | A1 | Impuesto del documento y de cada línea. |
| 51 | `/Invoice/cac:TaxTotal/cbc:TaxAmount` | — | 18.00 | `currencyID="PEN"` | XSD | Campo del esquema; su presencia la impone el padre. |
| 52 | `/Invoice/cac:TaxTotal/cac:TaxSubtotal` | — | — | — | XSD | Campo del esquema; su presencia la impone el padre. |
| 53 | `/Invoice/cac:TaxTotal/cac:TaxSubtotal/cbc:TaxableAmount` | — | 100.00 | `currencyID="PEN"` | XSD | Campo del esquema; su presencia la impone el padre. |
| 54 | `/Invoice/cac:TaxTotal/cac:TaxSubtotal/cbc:TaxAmount` | — | 18.00 | `currencyID="PEN"` | XSD | Campo del esquema; su presencia la impone el padre. |
| 55 | `/Invoice/cac:TaxTotal/cac:TaxSubtotal/cac:TaxCategory` | — | — | — | XSD | Campo del esquema; su presencia la impone el padre. |
| 56 | `/Invoice/cac:TaxTotal/cac:TaxSubtotal/cac:TaxCategory/cac:TaxScheme` | 0..1 | — | — | A8 cat. 05 | IGV: `1000` / `IGV` / `VAT`. |
| 57 | `/Invoice/cac:TaxTotal/cac:TaxSubtotal/cac:TaxCategory/cac:TaxScheme/cbc:ID` | 1..1 | 1000 | — | A1 campo 8 | Serie-correlativo, `an..13`, formato `<Serie>-<Número>`. |
| 58 | `/Invoice/cac:TaxTotal/cac:TaxSubtotal/cac:TaxCategory/cac:TaxScheme/cbc:Name` | — | IGV | — | XSD | Campo del esquema; su presencia la impone el padre. |
| 59 | `/Invoice/cac:TaxTotal/cac:TaxSubtotal/cac:TaxCategory/cac:TaxScheme/cbc:TaxTypeCode` | — | VAT | — | XSD | Campo del esquema; su presencia la impone el padre. |
| 60 | `/Invoice/cac:LegalMonetaryTotal` | 1..1 | — | — | A1 | Los totales. `PayableAmount` es lo que se cobra. |
| 61 | `/Invoice/cac:LegalMonetaryTotal/cbc:LineExtensionAmount` | — | 100.00 | `currencyID="PEN"` | XSD | Campo del esquema; su presencia la impone el padre. |
| 62 | `/Invoice/cac:LegalMonetaryTotal/cbc:TaxInclusiveAmount` | — | 118.00 | `currencyID="PEN"` | XSD | Campo del esquema; su presencia la impone el padre. |
| 63 | `/Invoice/cac:LegalMonetaryTotal/cbc:PayableAmount` | — | 118.00 | `currencyID="PEN"` | XSD | Campo del esquema; su presencia la impone el padre. |
| 64 | `/Invoice/cac:InvoiceLine` | 1..n | — | — | A1 | Una por renglón. |
| 65 | `/Invoice/cac:InvoiceLine/cbc:ID` | 1..1 | 1 | — | A1 campo 8 | Serie-correlativo, `an..13`, formato `<Serie>-<Número>`. |
| 66 | `/Invoice/cac:InvoiceLine/cbc:InvoicedQuantity` | — | 1.00 | `unitCode="NIU"` | XSD | Campo del esquema; su presencia la impone el padre. |
| 67 | `/Invoice/cac:InvoiceLine/cbc:LineExtensionAmount` | — | 100.00 | `currencyID="PEN"` | XSD | Campo del esquema; su presencia la impone el padre. |
| 68 | `/Invoice/cac:InvoiceLine/cac:PricingReference` | 0..1 | — | — | A8 cat. 16 | Precio unitario con IGV incluido, código `01`. |
| 69 | `/Invoice/cac:InvoiceLine/cac:PricingReference/cac:AlternativeConditionPrice` | — | — | — | XSD | Campo del esquema; su presencia la impone el padre. |
| 70 | `/Invoice/cac:InvoiceLine/cac:PricingReference/cac:AlternativeConditionPrice/cbc:PriceAmount` | — | 118.00 | `currencyID="PEN"` | XSD | Campo del esquema; su presencia la impone el padre. |
| 71 | `/Invoice/cac:InvoiceLine/cac:PricingReference/cac:AlternativeConditionPrice/cbc:PriceTypeCode` | — | 01 | — | XSD | Campo del esquema; su presencia la impone el padre. |
| 72 | `/Invoice/cac:InvoiceLine/cac:TaxTotal` | 0..n | — | — | A1 | Impuesto del documento y de cada línea. |
| 73 | `/Invoice/cac:InvoiceLine/cac:TaxTotal/cbc:TaxAmount` | — | 18.00 | `currencyID="PEN"` | XSD | Campo del esquema; su presencia la impone el padre. |
| 74 | `/Invoice/cac:InvoiceLine/cac:TaxTotal/cac:TaxSubtotal` | — | — | — | XSD | Campo del esquema; su presencia la impone el padre. |
| 75 | `/Invoice/cac:InvoiceLine/cac:TaxTotal/cac:TaxSubtotal/cbc:TaxableAmount` | — | 100.00 | `currencyID="PEN"` | XSD | Campo del esquema; su presencia la impone el padre. |
| 76 | `/Invoice/cac:InvoiceLine/cac:TaxTotal/cac:TaxSubtotal/cbc:TaxAmount` | — | 18.00 | `currencyID="PEN"` | XSD | Campo del esquema; su presencia la impone el padre. |
| 77 | `/Invoice/cac:InvoiceLine/cac:TaxTotal/cac:TaxSubtotal/cac:TaxCategory` | — | — | — | XSD | Campo del esquema; su presencia la impone el padre. |
| 78 | `/Invoice/cac:InvoiceLine/cac:TaxTotal/cac:TaxSubtotal/cac:TaxCategory/cbc:Percent` | — | 18.00 | — | XSD | Campo del esquema; su presencia la impone el padre. |
| 79 | `/Invoice/cac:InvoiceLine/cac:TaxTotal/cac:TaxSubtotal/cac:TaxCategory/cbc:TaxExemptionReasonCode` | 0..1 | 10 | — | A8 cat. 07 | `10` = gravado, operación onerosa. |
| 80 | `/Invoice/cac:InvoiceLine/cac:TaxTotal/cac:TaxSubtotal/cac:TaxCategory/cac:TaxScheme` | 0..1 | — | — | A8 cat. 05 | IGV: `1000` / `IGV` / `VAT`. |
| 81 | `/Invoice/cac:InvoiceLine/cac:TaxTotal/cac:TaxSubtotal/cac:TaxCategory/cac:TaxScheme/cbc:ID` | 1..1 | 1000 | — | A1 campo 8 | Serie-correlativo, `an..13`, formato `<Serie>-<Número>`. |
| 82 | `/Invoice/cac:InvoiceLine/cac:TaxTotal/cac:TaxSubtotal/cac:TaxCategory/cac:TaxScheme/cbc:Name` | — | IGV | — | XSD | Campo del esquema; su presencia la impone el padre. |
| 83 | `/Invoice/cac:InvoiceLine/cac:TaxTotal/cac:TaxSubtotal/cac:TaxCategory/cac:TaxScheme/cbc:TaxTypeCode` | — | VAT | — | XSD | Campo del esquema; su presencia la impone el padre. |
| 84 | `/Invoice/cac:InvoiceLine/cac:Item` | — | — | — | XSD | Campo del esquema; su presencia la impone el padre. |
| 85 | `/Invoice/cac:InvoiceLine/cac:Item/cbc:Description` | — | ARTICULO DE PRUEBA | — | XSD | Campo del esquema; su presencia la impone el padre. |
| 86 | `/Invoice/cac:InvoiceLine/cac:Price` | — | — | — | XSD | Campo del esquema; su presencia la impone el padre. |
| 87 | `/Invoice/cac:InvoiceLine/cac:Price/cbc:PriceAmount` | — | 100.00 | `currencyID="PEN"` | XSD | Campo del esquema; su presencia la impone el padre. |

## Descuentos declarados (ERP-FISCAL-6, ADR-43)

Soporte real, en **BETA**, para factura (01) y boleta (03) con el mismo generador.
Ningún importe se recalcula: el estado «después» es el snapshot de la venta y el
estado «antes» sale de `Order.subtotal_amount` con la misma autoridad de cálculo.

| Origen comercial (`Order.discount_source`) | Dónde se declara | Código Catálogo N.º 53 (RV-2025, hoja `Catálogos`) |
|---|---|---|
| `coupon`, `manual` | UN `/Invoice/cac:AllowanceCharge` global | `02` — Descuentos globales que afectan la base imponible del IGV/IVAP |
| `promotion` | `/Invoice/cac:InvoiceLine/cac:AllowanceCharge` en cada línea rebajada | `00` — Descuentos que afectan la base imponible del IGV/IVAP (nivel ítem) |
| `none` con `discount_amount > 0` | No se emite: `FiscalError` | — |

Ejemplo real de los tests (`Fiscal6DiscountDeclarationTest`): funda 100,00 + vidrio
50,00 con cupón del 10 % → total 135,00; y el mismo par en combo a 100,00 → total 100,00.

| XPath | Card. | Global (cupón 10 %) | Línea (combo a 100) | Fuente | Por qué |
|---|---|---|---|---|---|
| `/Invoice/cac:AllowanceCharge` | 0..n | 1 | 0 | XSD; RV-2025 fila 50 | Va entre `cac:PaymentTerms` y `cac:TaxTotal`: es su sitio en la secuencia del XSD. |
| `…/cac:AllowanceCharge/cbc:ChargeIndicator` | 1..1 | `false` | — | RV-2025 regla 3114 | Descuento. `false` es obligatorio para `02`/`00`. |
| `…/cac:AllowanceCharge/cbc:AllowanceChargeReasonCode` | 0..1 | `02` | — | RV-2025 reglas 3071/3072/4291 | `00`/`01`/`47`/`48` a nivel global es la observación 4291. |
| `…/cac:AllowanceCharge/cbc:Amount` | 1..1 | `12.71` | — | RV-2025 regla 2968 | Descuento NETO: base previa 127,12 − base 114,41. Positivo y distinto de cero. |
| `…/cac:AllowanceCharge/cbc:BaseAmount` | 0..1 | `127.12` | — | RV-2025 regla 3016 | La base previa al descuento (Σ valor de venta de las líneas). |
| `…/cac:AllowanceCharge/cbc:MultiplierFactorNumeric` | 0..1 | omitido | omitido | RV-2025 reglas 3290/3307 | Sólo si el porcentaje quedó congelado como tal; derivarlo inventaría un dato. |
| `/Invoice/cac:TaxTotal/cac:TaxSubtotal/cbc:TaxableAmount` | 1..1 | `114.41` | `84.75` | RV-2025 regla 3277 | Σ líneas − globales `02` (+ cargos `49`). Es `Order.taxable_amount`. |
| `/Invoice/cac:TaxTotal/cac:TaxSubtotal/cbc:TaxAmount` | 1..1 | `20.59` | `15.25` | RV-2025 regla 3291 | (Σ bases de línea − `02`) × tasa, ±1. Es `Order.tax_amount`. |
| `/Invoice/cac:LegalMonetaryTotal/cbc:LineExtensionAmount` | 1..1 | `114.41` | `84.75` | RV-2025 regla 3278 | «Total valor de venta» = Σ líneas − globales `02`: la base imponible, también con descuento. |
| `/Invoice/cac:LegalMonetaryTotal/cbc:TaxInclusiveAmount` | 1..1 | `135.00` | `100.00` | RV-2025 regla 3279 | Total valor de venta + tributos. Es `Order.total`. |
| `/Invoice/cac:LegalMonetaryTotal/cbc:PayableAmount` | 1..1 | `135.00` | `100.00` | RV-2025 regla 3280 | Lo cobrado. `TaxInclusiveAmount` + cargos no afectos − `AllowanceTotalAmount` − anticipos. |
| `/Invoice/cac:LegalMonetaryTotal/cbc:AllowanceTotalAmount` | 0..1 | **omitido** | **omitido** | RV-2025 fila 51, reglas 3300/3280 | «Sumatoria otros descuentos (que NO afectan la base)»: se valida contra `01`/`03`/`63` y se RESTA del importe a pagar. Escribir aquí nuestros descuentos los declararía dos veces. |
| `/Invoice/cac:InvoiceLine/cbc:LineExtensionAmount` | 1..1 | `84.75` · `42.37` | `56.50` · `28.25` | RV-2025 regla 3271 | Cantidad × valor unitario − descuentos `00` de la línea (+ cargos `47`). Sin descuento de línea, es la base previa de la línea. |
| `/Invoice/cac:InvoiceLine/cac:PricingReference/cac:AlternativeConditionPrice/cbc:PriceAmount` | 1..1 | `100.00` · `50.00` | `66.67` · `33.33` | RV-2025 regla 3270; guía §38 | Precio de venta unitario, n(12,10): (valor de venta + tributos) / cantidad. Lo que el cliente pagó por unidad. |
| `/Invoice/cac:InvoiceLine/cac:AllowanceCharge` | 0..n | 0 | 1 por línea rebajada | XSD; RV-2025 fila 39 | Entre `cac:PricingReference` y `cac:TaxTotal`. |
| `…/cac:InvoiceLine/cac:AllowanceCharge/cbc:AllowanceChargeReasonCode` | 0..1 | — | `00` | RV-2025 reglas 2954/3073/4268 | Un código global en la línea es la observación 4268. |
| `…/cac:InvoiceLine/cac:AllowanceCharge/cbc:Amount` | 1..1 | — | `28.24` · `14.13` | RV-2025 regla 2955 | Reparto del descuento neto del documento (42,37) entre las líneas, proporcional a su rebaja bruta (33,33 / 16,67), mayor residuo. |
| `…/cac:InvoiceLine/cac:AllowanceCharge/cbc:BaseAmount` | 1..1 | — | `84.74` · `42.38` | RV-2025 regla 3053; guía §40 | Valor de la línea ANTES de la rebaja = valor de venta + descuento. |
| `/Invoice/cac:InvoiceLine/cac:Price/cbc:PriceAmount` | 1..1 | `84.75` · `42.37` | `84.74` · `42.38` | RV-2025 fila 32 | Valor unitario SIN rebajar, n(12,10): la base previa de la línea / cantidad. |

Identidades que `InvoiceData.check()` y `rules.validate` exigen antes de firmar
(exactas salvo donde se indica ±0,01, que absorbe el valor unitario a 10 decimales):

    Σ InvoiceLine.LineExtensionAmount − Σ global.Amount == taxable_amount        (exacta)
    taxable_amount + tax_amount == total                                         (exacta)
    cada descuento: 0 < Amount ≤ BaseAmount, BaseAmount == valor antes de rebajar (exacta)
    cantidad × valor unitario − descuentos de línea == LineExtensionAmount       (±0,01)
    tax_amount == taxable_amount × tasa                                          (±0,01)
    código `02` sólo en el documento; código `00` sólo en la línea

Lo que SUNAT no comprueba y aquí sí: que el descuento exista en la venta
(`discount_source`), que el subtotal previo se conserve, que las promociones
aplicadas sumen el descuento y que cada componente pertenezca a la venta y a la
empresa. Todo eso falla cerrado en `fiscal_services._order_to_invoice_data`.

Boleta (03): las mismas reglas existen en la hoja `Boleta2_0` como observaciones
(4287, 4288, 4290, 4299, 4307, 4309, 4310, 4312) y los mismos errores de catálogo y
formato (3071, 3072, 3114, 2968, 3016, 2065). El generador y el validador son los
mismos. El envío individual de boletas sigue prohibido: van por el Resumen Diario.

**Estado:** XSD y reglas locales PASAN para factura y boleta con descuento global y
de línea (tests `Fiscal6DiscountDeclarationTest`). La aceptación en SUNAT BETA de un
comprobante con descuento está **PENDIENTE DE VERIFICACIÓN DIRECTA**. Producción:
no implementado.

## Lo que NO se emite, y por qué

| Nodo | Motivo |
|---|---|
| `sac:AdditionalMonetaryTotal` | Sólo aplica a operaciones gratuitas, exoneradas, inafectas o con anticipos. Ninguna entra en el caso mínimo. |
| `cac:AllowanceCharge` (cargos) | No hay cargos. Los **descuentos** sí se emiten: ver «Descuentos declarados». |
| `cbc:AllowanceTotalAmount` | Reservado por SUNAT a los descuentos que NO afectan la base (`01`/`03`), y restado del importe a pagar (reglas 3300/3280). Los nuestros afectan la base y ya están dentro de ella. |
| `cbc:MultiplierFactorNumeric` | Opcional. Sólo si el porcentaje quedara congelado en la venta como tal. |
| `cac:Delivery` | Ni detracción ni guía asociada. |
| `cac:PrepaidPayment` | Sin anticipos. |
| `cbc:DueDate` | Venta al contado; no hay vencimiento. |
| `cac:InvoicePeriod` | No es un servicio con periodo facturado. |

## Estado

| Comprobación | Resultado |
|---|---|
| XML bien formado | PASA |
| Valida contra `UBL-Invoice-2.1.xsd` | PASA (sobre el documento **firmado**) |
| Reglas locales (`fiscal/rules.py`) | PASA |
| Firma verifica | PASA |
| Alterar un importe rompe la firma | PASA |
| Orden de elementos vigilado por test | PASA |

El borrador **sin firmar** no valida contra el esquema, y es correcto que no
lo haga: el hueco de la firma está vacío. Un comprobante sin firmar no es un
comprobante.
