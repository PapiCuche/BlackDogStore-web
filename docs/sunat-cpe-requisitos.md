# Requisitos SUNAT para comprobantes de pago electrónicos (CPE)

**Fase C2.2A.** Consultado el **5 de septiembre de 2026**.

Este documento existe para que la implementación fiscal no dependa de la memoria
de nadie. Cada requisito lleva su fuente, y sólo cuentan como autoridad las
fuentes de SUNAT: `cpe.sunat.gob.pe`, `orientacion.sunat.gob.pe`,
`www.sunat.gob.pe` y los anexos de resolución enlazados desde ahí.

Donde no se pudo confirmar algo en fuente oficial, **se dice** en vez de
rellenarlo. Un dato tributario inventado es peor que un hueco declarado.

---

## 0. Cómo leer esto

| Marca | Significado |
|---|---|
| **OFICIAL** | Se abrió la fuente y dice literalmente eso. |
| **DERIVADO** | Se deduce de dos o más fuentes oficiales; el razonamiento va escrito. |
| **SIN CONFIRMAR** | No se localizó en fuente oficial. No se implementa sobre esto. |

---

## 1. Servicios web — direcciones y credenciales

Fuente: SUNAT, *SEE Sistemas del Contribuyente — Manual del programador*,
`https://cpe.sunat.gob.pe/sites/default/files/inline-files/manual_programador%20(1).pdf`,
sección «Web Services». Consultado 2026-09-05.

**OFICIAL — Producción.** Cubre factura, notas vinculadas, servicios públicos,
**resumen diario**, comunicación de baja y lotes de facturas:

    https://e-factura.sunat.gob.pe/ol-ti-itcpfegem/billService?wsdl

**OFICIAL — Beta.** El manual lo rotula literalmente
«Beta: SERVICIO EXCLUSIVO PARA PRUEBAS»:

    https://e-beta.sunat.gob.pe/ol-ti-itcpfegem-beta/billService?wsdl

**OFICIAL — Métodos**: `sendBill`, `sendSummary`, `getStatus`, `getStatusCdr`.

**OFICIAL — Autenticación**: WS-Security, modelo UsernameToken. El manual trae
este ejemplo literal:

```xml
<wsse:UsernameToken>
  <wsse:Username>20100066603MODDATOS</wsse:Username>
  <wsse:Password>moddatos</wsse:Password>
</wsse:UsernameToken>
```

De donde: **`Username` = RUC (11 dígitos) + usuario SOL secundario, concatenados
sin separador**; `Password` = la Clave SOL de ese usuario.

Confirmado además por el **Anexo N.º 6 vigente desde el 1.8.2026** (R.S.
000048-2026, `https://www.sunat.gob.pe/legislacion/superin/2026/anexo-000048-2026.pdf`),
numerales 6.1.2 y 6.1.3: «el emisor electrónico debe usar el protocolo de
seguridad WS-Security, el modelo UsernameToken, y usar como credenciales su
código de usuario y la Clave SOL».

### Credenciales públicas de prueba

**OFICIAL.** El propio manual publica las credenciales de BETA en su ejemplo:
RUC `20100066603`, usuario `MODDATOS`, clave `moddatos`.

Esto es importante para el alcance de C2.2A: **una prueba real contra BETA no
depende de que el cliente compre nada**. Lo que sí hace falta es un certificado
digital para firmar; para BETA sirve uno autofirmado generado localmente.

---

## 2. Factura electrónica (tipo 01)

Fuentes: Anexo N.º 1 de la R.S. 123-2022
(`https://www.sunat.gob.pe/legislacion/superin/2022/anexo-123-2022.pdf`),
R.S. 117-2017 (`https://www.sunat.gob.pe/legislacion/superin/2017/117-2017.pdf`),
Anexo N.º 6 de la R.S. 000048-2026.

| # | Requisito | Marca |
|---|---|---|
| F1 | Tipo de documento = `01` del Catálogo N.º 01 | OFICIAL |
| F2 | Serie **alfanumérica de exactamente 4 caracteres**, primer carácter la letra `F` | OFICIAL |
| F3 | Correlativo de **hasta 8 caracteres, se inicia en 1**, independiente del correlativo del comprobante impreso | OFICIAL |
| F4 | En el XML, la numeración va en `/Invoice/cbc:ID` como `<Serie>-<Número>`, `an..13` | OFICIAL |
| F5 | El adquirente se identifica con **tipo de documento `6` (RUC)**; la factura se emite sólo a favor de quien posea RUC, salvo dos excepciones tasadas | OFICIAL |
| F6 | Razón social / denominación del adquirente es **obligatoria** | OFICIAL |
| F7 | Firma digital válida, vigente y **del emisor** (o de un PSE inscrito y autorizado) | OFICIAL |
| F8 | **Plazo de envío: hasta 3 días calendario** contados desde el día siguiente a la fecha de emisión (R.S. 003-2023, vigente desde 05/01/2023) | OFICIAL |

---

## 3. Boleta de venta electrónica (tipo 03)

Fuentes: Anexo N.º 2 de la R.S. 114-2019
(`https://www.sunat.gob.pe/legislacion/superin/2019/anexoII-114-2019.pdf`),
R.S. 117-2017 art. 29.1.1.

| # | Requisito | Marca |
|---|---|---|
| B1 | Tipo de documento = `03` | OFICIAL |
| B2 | Serie de 4 caracteres, primer carácter la letra `B` | OFICIAL |
| B3 | **Identificación del adquirente obligatoria cuando el importe total supera S/ 700.00**, o cuando el adquirente lo solicita | OFICIAL |
| B4 | El tipo y número de documento siguen el Catálogo N.º 06 | OFICIAL |
| B5 | El ejemplar puede remitirse individualmente **o informarse por resumen diario**; si se opta por el resumen, el emisor conserva el ejemplar | OFICIAL |

**El umbral de S/ 700 sigue vigente.** Se verificó contra el texto del Anexo
N.º 2, no se dio por bueno de memoria: «cuando el importe total por boleta de
venta supera la suma de setecientos soles».

---

## 4. Catálogos (Anexo N.º 8)

Fuente: `https://www.sunat.gob.pe/legislacion/superin/2017/anexoVII-117-2017.pdf`.
Se descargó el PDF y se extrajo su texto; los códigos de abajo son **literales**
del documento. Verificado además que el anexo de la R.S. 000108-2026 sólo
modifica el Catálogo N.º 55 — no toca el 01 ni el 06.

**Catálogo 01** — `cbc:InvoiceTypeCode`
| Código | Descripción |
|---|---|
| `01` | FACTURA |
| `03` | BOLETA DE VENTA |
| `07` | NOTA DE CREDITO |
| `08` | NOTA DE DEBITO |

**Catálogo 02** — `cbc:DocumentCurrencyCode`: ISO 4217 (`PEN`).

**Catálogo 03** — `@unitCode`: UN/ECE Recommendation 20.

**Catálogo 05** — `cbc:TaxTypeCode`
| Código | Descripción | UN/ECE 5153 | UN/ECE 5305 |
|---|---|---|---|
| `1000` | IGV — IMPUESTO GENERAL A LAS VENTAS | `VAT` | `S` |
| `9997` | EXONERADO | `VAT` | `E` |
| `9998` | INAFECTO | `FRE` | `O` |

La columna 5305 está marcada «Vigente para la versión UBL 2.1».

**Catálogo 06** — `cbc:AdditionalAccountID` (documento del adquirente)
| Código | Descripción |
|---|---|
| `0` | DOC.TRIB.NO.DOM.SIN.RUC |
| `1` | DOC. NACIONAL DE IDENTIDAD (DNI) |
| `4` | CARNET DE EXTRANJERIA |
| `6` | REG. UNICO DE CONTRIBUYENTES (RUC) |
| `7` | PASAPORTE |

**Catálogo 07** — `cbc:TaxExemptionReasonCode` (afectación del IGV)
| Código | Descripción |
|---|---|
| `10` | **Gravado - Operación Onerosa** ← el único que C2.2A soporta |
| `20` | Exonerado - Operación Onerosa |
| `30` | Inafecto - Operación Onerosa |

**Catálogo 11** — `cbc:InstructionID` (resumen diario, tipo de valor de venta):
`01` Gravado · `02` Exonerado · `03` Inafecto · `04` Exportación · `05` Gratuitas.

**Catálogo 16** — `cac:AlternativeConditionPrice/cbc:PriceTypeCode`
| Código | Descripción |
|---|---|
| `01` | Precio unitario (incluye el IGV) |
| `02` | Valor referencial unitario en operaciones no onerosas |

**Catálogo 17** — `sac:SUNATTransaction/cbc:ID`: `01` Venta interna.

**Catálogo 51** — código de tipo de factura: `0101` Venta interna.

**Catálogo 52** — leyendas: `1000` Monto en Letras.

---

## 5. Firma digital

| # | Requisito | Marca |
|---|---|---|
| S1 | La firma va en `/Invoice/ext:UBLExtensions/ext:UBLExtension/ext:ExtensionContent/ds:Signature` y se referencia desde `/Invoice/cac:Signature` | OFICIAL |
| S2 | `cac:Signature` lleva `cbc:ID`, y en `cac:SignatoryParty/cac:PartyIdentification/cbc:ID` el **RUC del firmante** | OFICIAL |
| S3 | Canonicalización `http://www.w3.org/TR/2001/REC-xml-c14n-20010315` | OFICIAL |
| S4 | Espacio de nombres XMLDSig `http://www.w3.org/2000/09/xmldsig#` | OFICIAL |
| S5 | El Anexo N.º 6 **no fija** el algoritmo de hash: remite a lo que declare `<ds:DigestMethod>`. Los ejemplos de la Guía UBL 2.1 usan SHA-1 | DERIVADO |

Sobre S5: el algoritmo se declara en el propio documento, así que la
implementación debe poder emitir el que corresponda en vez de asumir uno.

---

## 6. Empaquetado y nombres de archivo

Fuentes: Manual del programador; Anexo N.º 6 de la R.S. 000048-2026, numeral
6.1.3.b.1.

**OFICIAL — Comprobante individual**:

    RUC(11) - TT(2) - SERIE(4) - CORRELATIVO(1..8) . XML   →  y el mismo nombre .ZIP

Ejemplo del manual: `20100066603-01-F001-1.ZIP`.

Del Anexo N.º 6: posiciones 16-19 = serie, «se espera que el primer carácter sea
la constante F, B, C, L o G según corresponda, seguido por tres caracteres
alfanuméricos»; posiciones 21-28 = correlativo, «mínimo 1 y máximo 8 dígitos».

**OFICIAL — CDR**: SUNAT devuelve el CDR dentro de un ZIP; al desempaquetarlo el
XML se llama `R-<nombre del archivo enviado sin extensión>.xml`.

---

## 7. CDR y códigos de respuesta

| # | Requisito | Marca |
|---|---|---|
| C1 | El CDR es un `ApplicationResponse` **UBL 2.0** | OFICIAL |
| C2 | El resultado va en `/ApplicationResponse/cac:DocumentResponse/cac:Response/cbc:ResponseCode` y su texto en `cbc:Description` | OFICIAL |
| C3 | `cbc:ReferenceID` identifica el documento: para facturas y notas, `<FAAA>-<NNNNNNNN>` | OFICIAL |
| C4 | `ResponseCode` = `0` → **aceptada**; distinto de `0` → rechazada | OFICIAL |
| C5 | `cbc:Note` transporta advertencias que **no** representan rechazo → aceptada **con observaciones** | OFICIAL |
| C6 | Rangos verificados contra el catálogo oficial de reglas de validación (2 077 códigos): 0100–0999 (59), 1000–1999 (87), 2000–2999 (964), 3000–3999 (540), 4000–4999 (427) | OFICIAL |

**DERIVADO — cómo clasificar una respuesta:**

1. Excepción o fault **sin ZIP** → no hay CDR. Estado de **excepción**, no
   rechazo. Códigos 0100–1999. Se reintenta.
2. CDR con `ResponseCode = 0` → **aceptada**. Si además trae `cbc:Note` →
   **aceptada con observaciones**.
3. CDR con `ResponseCode` en 2000–3999 → **rechazada**. El documento emitido no
   tiene validez tributaria.
4. 4000+ → observación.

**Un timeout, un 500 o una conexión perdida NO son un rechazo.** Esa distinción
es la razón de que el estado de excepción exista por separado.

---

## 8. Código QR

Fuentes: Anexo A de la R.S. 113-2018
(`https://www.sunat.gob.pe/legislacion/superin/2018/anexoA-113-2018.pdf`) y
Anexo de la R.S. 244-2019
(`https://www.sunat.gob.pe/legislacion/superin/2019/anexo-244-2019.pdf`).

| # | Requisito | Marca |
|---|---|---|
| Q1 | El **Valor Resumen** del QR es «el valor del elemento `<ds:DigestValue>` del documento» — **no se recalcula un hash aparte** | OFICIAL |
| Q2 | La cadena del QR **no termina en separador**. El contraste es deliberado: para el PDF417 (numeral 6.3.3) SUNAT sí imprime un pipe de cierre | DERIVADO, con cita del contraste |
| Q3 | El Valor Resumen puede además imprimirse como texto fuera del QR, pero eso no lo exime de ir dentro | OFICIAL |

---

## 9. Plazos, puesta a disposición y conservación

| # | Requisito | Marca |
|---|---|---|
| P1 | Factura y nota vinculada: **hasta 3 días calendario** desde el día siguiente a la emisión | OFICIAL |
| P2 | El emisor debe ofrecer al adquirente una **consulta web** de sus comprobantes, por un plazo **no menor a un año** desde la emisión, con autenticación | OFICIAL |
| P3 | El emisor debe almacenar, archivar y conservar los CPE y notas emitidos y recibidos, los resúmenes diarios, las comunicaciones de baja y las constancias de rechazo | OFICIAL |

---

## 10. Huecos declarados

No se implementará nada sobre estos puntos hasta confirmarlos en fuente oficial:

1. **Estructura exacta del Resumen Diario** (`SummaryDocuments`): namespace,
   `CustomizationID`, `ReferenceDate` frente a `IssueDate`, y la semántica de los
   estados 1/2/3 de `SummaryDocumentsLine`. El agente que investigaba este tema
   no llegó a terminar.
2. **Orden literal completo de los campos del QR.** Se confirmó qué es el Valor
   Resumen y que no hay separador final, pero la lista ordenada de los 10 campos
   debe releerse del anexo antes de generar un QR.
3. **Plazo del resumen diario de boletas.** Confirmado el de la factura (3 días);
   el del resumen debe verificarse por separado.
4. **Algoritmo de hash exigible hoy.** Los ejemplos usan SHA-1; el anexo no lo
   fija. Hay que decidir qué emitir y poder cambiarlo.

---

## 11. Lo que esto significa para el alcance

- **La prueba real contra BETA es posible sin comprar nada**: SUNAT publica las
  credenciales de prueba. Falta sólo un certificado, y para BETA sirve uno
  autofirmado.
- **Producción necesita**: RUC, usuario SOL **secundario** con perfil de
  facturación electrónica, su Clave SOL, y un **certificado digital tributario**
  `.pfx`/`.p12` emitido por una entidad acreditada ante INDECOPI a nombre del
  RUC, más su contraseña. Es un gasto anual real.
- El endpoint de producción y el de beta son **direcciones distintas**, así que
  la guarda de «nunca producción» puede apoyarse en configuración de servidor y
  no en un parámetro que viaje desde el navegador.


---

## 12. Prueba real contra SUNAT BETA — lo verificado y lo que falta

Se ejecutó la cadena completa contra el servicio BETA real, no contra un doble.
El guion está en [spike-sunat-beta.py.txt](spike-sunat-beta.py.txt).

### Verificado ejecutando, no razonando

| Eslabón | Resultado |
|---|---|
| Endpoint `e-beta.sunat.gob.pe/ol-ti-itcpfegem-beta/billService` | Alcanzable, **HTTP 200** |
| WS-Security UsernameToken `20100066603MODDATOS` / `moddatos` | **Aceptado** — ningún fault de autenticación |
| Empaquetado ZIP `20100066603-01-F001-1.ZIP` | **Aceptado** — SUNAT lo descomprimió |
| Firma XMLDSig: C14N 1.0, RSA-SHA256, certificado autofirmado | Producida y aceptada por el parser |
| `DigestValue` | Generado, p. ej. `494tH4AHwQ6O6LMHKsugVIiquKWgZ1WiFYxm/m6+zTY=` |
| Nombre de archivo según Anexo N.º 6 | **Aceptado** |

Que SUNAT devuelva un **código de regla de validación numerado** en vez de un
error de transporte o de parseo es la prueba de que llegó a leer el documento.

### Lo que NO se logró

**No hay CDR.** El documento se rechaza con:

    soap-env:Client.3244
    «Debe consignar la informacion del tipo de transaccion del comprobante»
    Detalle: (nodo: "/" valor: "")

Se probaron tres ubicaciones del dato:

1. `ext:ExtensionContent/sac:AdditionalInformation/sac:SUNATTransaction/cbc:ID`
2. `ext:ExtensionContent/sac:SUNATTransaction/cbc:ID`
3. Sin extensión `sac`, sólo `cbc:InvoiceTypeCode/@listID="0101"`

**Las tres devuelven exactamente el mismo error**, incluida la que no lleva esa
extensión en absoluto. Eso descarta que el problema sea dónde va el dato: el
validador falla antes y reporta 3244 de forma genérica — el `nodo: "/"` del
detalle apunta a que evalúa un XPath contra la raíz y no encuentra nada.

**No se siguió adivinando.** Resolverlo exige leer la Guía XML de Factura UBL
2.1 campo por campo, y ése es justamente el hueco n.º 1 declarado arriba: el
agente que investigaba esa guía no llegó a terminar. Probar combinaciones contra
el servicio de SUNAT hasta que una pase no es implementar una norma; es adivinar
con un servidor de por medio.

### Dependencias evaluadas

| Paquete | Versión | Para qué | Licencia |
|---|---|---|---|
| `lxml` | 6.1.3 | Árbol XML y **C14N 1.0** | BSD |
| `signxml` | 5.1.0 | XMLDSig sobre lxml + cryptography | Apache-2.0 |

`cryptography` 50.0.1 ya estaba en el entorno.

**Por qué no basta la biblioteca estándar**: `xml.etree.ElementTree.canonicalize`
implementa **C14N 2.0**, y SUNAT exige
`http://www.w3.org/TR/2001/REC-xml-c14n-20010315`, que es la **1.0**. No son
compatibles para un documento con prefijos de espacio de nombres, que es
exactamente lo que es un UBL.

**`requirements.txt` NO se ha modificado**: las dependencias están instaladas en
el entorno para el espolón, pero ningún código del proyecto las usa todavía.
Fijarlas antes de que haya código que las necesite sería declarar una decisión
que aún no se ha tomado.


---

## 13. C2.2A.1 — la factura aceptada, y las dos causas reales

### El 3244 no era lo que parecía

El mensaje «Debe consignar la informacion del tipo de transaccion del
comprobante» **no habla del tipo de operación**. «Tipo de transacción» es la
etiqueta que SUNAT usa para el bloque **Contado / Crédito**.

Regla exacta, hoja `Factura2_0` del archivo oficial «Reglas de validación —
actualizado al 26.08.2026», líneas 174-177:

    NODO      /Invoice/cac:PaymentTerms/cbc:ID
    ESPERADO  "FormaPago"
    CONDICIÓN «No existe al menos un tag cac:PaymentTerms con cbc:ID igual a
              'FormaPago'» — «Validación a partir del 01/01/2022 es ERROR»

Es una prueba de existencia sobre ese nodo. Nada más.

Eso explica por qué las tres ubicaciones que se probaron en C2.2A daban el mismo
error, **incluida la que no llevaba `sac:SUNATTransaction` en absoluto**: se
estaba moviendo un nodo que no interviene en la regla.

Regla encadenada **3245**: `/Invoice/cac:PaymentTerms/cbc:PaymentMeansID` debe
decir si es al contado o al crédito.

**Trampa registrada**: el mismo contenedor `cac:PaymentTerms` se reutiliza para
detracciones con `cbc:ID = "Detraccion"`. Tenerlo no basta; tiene que valer
exactamente `FormaPago`.

### El 3206: dos catálogos para la misma idea

Con la forma de pago ya puesta, SUNAT devolvió **3206**, «El dato ingresado como
tipo de operación no corresponde a un valor esperado (catálogo nro. 51)».

La causa: el tipo de operación vive en **dos catálogos distintos con longitudes
distintas**, y se estaba usando uno en el sitio del otro.

| Dónde | Catálogo | Venta interna |
|---|---|---|
| `sac:SUNATTransaction/cbc:ID` | N.º 17 | `01` |
| `cbc:InvoiceTypeCode/@listID` | N.º 51 | `0101` |

### Resultado real

Tercer envío, endpoint `e-beta.sunat.gob.pe/ol-ti-itcpfegem-beta/billService`,
6 de septiembre de 2026:

    RESULTADO   accepted
    código      0
    mensaje     La Factura numero F001-1, ha sido aceptada
    CDR         R-20100066603-01-F001-1.XML (3353 bytes)
    ReferenceID F001-1
    observaciones (ninguna)

Tres llamadas en total a lo largo de las dos fases, **cada una precedida de una
corrección con fuente**. No se probaron variantes contra el servidor.

### La disciplina que lo resolvió

Las dos causas salieron de **leer el archivo oficial de reglas de validación**,
no de permutar campos. El primer intento —tres envíos moviendo el mismo nodo—
no avanzó nada; el segundo enfoque acertó a la primera en ambos casos.
