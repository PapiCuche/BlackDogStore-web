# Esquemas UBL 2.1 — procedencia

**Origen**: SUNAT, «Guías y Manuales», acordeón *ARCHIVOS XSL - XSD*, enlace
rotulado literalmente «Archivo XSD - 2.1 (Actualizado al 28/02/2022)».

    https://cpe.sunat.gob.pe/guias-y-manuales
    https://cpe.sunat.gob.pe/sites/default/files/inline-files/Archivos%20XSD%20%281%29.zip

**Descargado**: 6 de septiembre de 2026.
**Tamaño del paquete**: 609 363 bytes.
**SHA-256 del paquete**:

    5cac9d9353521340fbc15d23f465a4946b004535b9ff411541c87da01694dbd5

SUNAT no publica checksum junto al ZIP, así que este valor es el de nuestra
descarga y sirve para detectar que el paquete cambió, no como firma de SUNAT.

## Qué se conservó y qué se quitó

El ZIP trae dos árboles, `2.0/` y `2.1/`, con 105 archivos. Aquí sólo está el
**2.1**, y de su `maindoc/` sólo `UBL-Invoice-2.1.xsd`: los otros 63 documentos
raíz (Order, Waybill, DespatchAdvice…) no se emiten y su presencia sólo añadiría
peso al repositorio.

`common/` se conserva **entero**, porque `UBL-Invoice-2.1.xsd` importa por ruta
relativa y la cadena de imports atraviesa casi todos sus ficheros. Recortarlo
rompería la compilación del esquema.

## Un hallazgo que conviene recordar

El árbol `2.1` **no contiene ningún esquema propio de SUNAT**: es UBL 2.1 de
OASIS sin modificar (cabecera «OASIS Universal Business Language (UBL) 2.1 OS,
Release Date: 04 November 2013»). Los `UBLPE-*.xsd` y
`SunatAggregateComponents-*.xsd` existen sólo en el árbol `2.0`, que es legado.

Consecuencia práctica: los elementos del espacio de nombres `sac:` que SUNAT
exige dentro de `ext:ExtensionContent` **no los valida este XSD** — pasan porque
la extensión admite contenido arbitrario. Sus reglas viven en el paquete XSL y en
la hoja de reglas de validación, no en el esquema.

## Licencia

UBL 2.1 es de OASIS. El aviso de copyright viene embebido en cada `.xsd` y se ha
conservado intacto; no se ha modificado ni una línea de los ficheros.

## Añadido en ERP-FISCAL-5A — CreditNote y DebitNote 2.1

**Origen**: OASIS, paquete oficial UBL 2.1 OS (Release Date 04 November 2013), el
MISMO estándar sin modificar que SUNAT redistribuye para el árbol 2.1 (ver el
hallazgo de arriba: «el árbol 2.1 no contiene ningún esquema propio de SUNAT»). Se
toma de la fuente original —OASIS, la autoridad del estándar— porque el sitio de
SUNAT bloquea las descargas automatizadas (Cloudflare 403).

    https://docs.oasis-open.org/ubl/os-UBL-2.1/xsd/maindoc/UBL-CreditNote-2.1.xsd
    https://docs.oasis-open.org/ubl/os-UBL-2.1/xsd/maindoc/UBL-DebitNote-2.1.xsd

**Descargado**: 23 de septiembre de 2026.
**SHA-256**:

    a54651b1225052f811bf2ba01346f13f2454e7cc3e0be290c91dd680dc7b7b1a  UBL-CreditNote-2.1.xsd
    295d142102a2a0cd8b223a79b8314c0fe1b55055478221dd2f0ed7603fecf920  UBL-DebitNote-2.1.xsd

Sólo estos dos `maindoc/`. Importan por ruta relativa el mismo `common/` ya
conservado (la nota y la factura comparten `CommonAggregate/Basic/Extension`,
`Signature*`, `xmldsig`, etc.), así que compilan contra el árbol existente sin
añadir nada más. Verificado: `etree.XMLSchema` compila los tres maindoc.
