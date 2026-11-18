# Nota de venta: cómo se identifica (registro de decisión)

**Estado:** decidido por el propietario el 2026-11-18 (tercera orden, definitiva). **Pendiente de confirmación por el contador.**
**Alcance:** la nota de venta NV- en sus cuatro salidas: A4, ticket PDF de 80 mm, impresión térmica ESC/POS y copia por correo. Las boletas y facturas electrónicas **no cambian**.

## Decisión

- **Cabecera:** «NOTA DE VENTA» (título comercial; configurable por empresa cuando exista el campo) + número NV- + empresa, cliente, productos y datos de la operación.
- **Ninguna leyenda en la nota**, ni en la cabecera, el detalle o el pie. Se retiran «Número interno del sistema. No es una serie fiscal.» y «Documento de control interno, sin validez tributaria». Tampoco se ponen «No es comprobante de pago», «se entrega aparte», ni referencias a boletas, facturas, comprobantes vinculados o estados de SUNAT.
- **Se retira la fila «Comprobante solicitado: Boleta/Factura»** del papel y del ticket: nombraba un comprobante dentro de la nota. El dato sigue en el pedido y en el panel.
- **Facturas y boletas:** sus leyendas obligatorias (representación impresa, ambiente BETA, rechazada, pendiente) se mantienen sin un solo cambio; sus goldens de texto (PDF y ESC/POS) son idénticos.

## Base legal

Fuente: investigación del 2026-11-18 sobre normas primarias. No es asesoría tributaria.

| Afirmación | Norma / fuente | Confianza |
|---|---|---|
| No hay norma que obligue a poner una leyenda en un documento que no es comprobante. El RCP regula sólo comprobantes y su lista es cerrada («Sólo se consideran comprobantes de pago…»). | Reglamento de Comprobantes de Pago, R.S. 007-99/SUNAT, art. 2 — https://www.sunat.gob.pe/legislacion/comprob/regla/capituloI.pdf ; caps. III–V — https://www.sunat.gob.pe/legislacion/comprob/regla/capituloIII.pdf | Media-alta (búsqueda negativa; el cap. VI del RCP no se pudo revisar) |
| Lo sancionable es emitir u otorgar un documento no previsto como comprobante **en lugar** del comprobante. Sanción: cierre. | TUO del Código Tributario, D.S. 133-2013-EF, art. 174 num. 1 y Tabla I — https://www.sunat.gob.pe/legislacion/codigo/textoCompleto-TUO-CT.pdf | Alta |
| SUNAT lo aplica a emisores electrónicos: entregar un documento no previsto en vez del CPE es la infracción del num. 1; papeles adicionales no sustituyen al CPE. | Informe N.° 127-2020-SUNAT/7T0000, conclusiones 1, 2, 3 y 5 — https://www.sunat.gob.pe/legislacion/oficios/2020/informe-oficios/i127-2020-7T0000.pdf | Alta |
| El comprobante se emite al entregar o al cobrar, lo que ocurra primero (también anticipos). | RCP, art. 5 num. 1 y 4 (cap. I, enlace de arriba) | Alta |
| Las series de CPE del SEE empiezan por F, B, L o G; las de contingencia son numéricas. | Anexo de la R.S. 244-2019/SUNAT — https://ww3.sunat.gob.pe/legislacion/superin/2019/anexo-244-2019.pdf | Alta en la regla |
| Ninguna norma regula la apariencia de un documento interno, pero parecerse a un comprobante (título, serie, QR, «boleta»/«factura») aumenta el riesgo del art. 174.1. | Inferencia de lo anterior | Media |

Conclusión: una leyenda no protege si el comprobante no se emite, y ninguna norma la exige. Lo que reduce el riesgo es que la nota **no se parezca** a un comprobante y que el comprobante se emita a tiempo.

## Salvaguardas estructurales (en lugar de la leyenda)

Todas tienen prueba automática (`backend/store/test_document_legends.py`):

1. **Texto de la nota sin vocabulario fiscal.** En A4, 80 mm, ESC/POS y correo (asunto, texto y HTML) no aparece ninguna de: «factura», «boleta», «comprobante», «SUNAT», «validez», «serie fiscal», «control interno», «registro interno». Se comprueba también con una boleta emitida para la misma venta.
2. **Numeración NV- nunca empieza por F ni por B.** La serie de notas de venta rechaza ese prefijo (mayúsculas o minúsculas) al crearla o al cambiarlo; el valor por defecto es `NV-`. Una serie existente con otro prefijo sigue numerando: sólo se valida el prefijo nuevo.
3. **Sin QR ni hash.** La nota no lleva código QR, «valor resumen» ni «representación impresa»; el ticket térmico no envía la orden de QR a la impresora.
4. **Título.** `document_legends.note_title()` devuelve «Nota de venta» y descarta un título de empresa que sea una denominación fiscal («factura», «boleta», «comprobante», «ticket fiscal», «recibo por honorarios», «liquidación de compra», «nota de crédito/débito»). La validación en el formulario llega con el campo de título.
5. **Un solo módulo de textos:** `backend/store/document_legends.py` reúne los textos de todos los documentos (título de la nota, avisos de la constancia y de la cotización, leyendas fiscales).

## Pendiente de confirmación por el contador

1. Que en cada caso en que se entrega una nota (tienda, pedido web, entrega diferida, anticipo) la boleta o factura se emite en el momento del art. 5 del RCP.
2. Si conviene entregar la nota al cliente o dejarla sólo como registro.
3. Si la ausencia total de leyenda es aceptable o prefiere una frase mínima.
4. Si mostrar el desglose referencial de IGV en la nota es prudente.
5. Riesgos de protección al consumidor (Ley 29571), no revisados.
