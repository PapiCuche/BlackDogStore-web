# Nota de venta: cómo se identifica (registro de decisión)

**Estado:** decidido por el propietario el 2026-11-18 (tercera orden, definitiva). **Pendiente de confirmación por el contador.**
**Alcance:** la nota de venta NV- en sus cuatro salidas: A4, ticket PDF de 80 mm, impresión térmica ESC/POS y copia por correo. Las boletas y facturas electrónicas **no cambian**.

## Decisión

- **Cabecera:** «NOTA DE VENTA» (título comercial; configurable por empresa cuando exista el campo) + número NV- + empresa, cliente, productos y datos de la operación.
- **Ninguna leyenda en la nota**, ni en la cabecera, el detalle o el pie. Se retiran «Número interno del sistema. No es una serie fiscal.» y «Documento de control interno, sin validez tributaria». Tampoco se ponen «No es comprobante de pago», «se entrega aparte», ni referencias a boletas, facturas, comprobantes vinculados o estados de SUNAT.
- **Se retira la fila «Comprobante solicitado: Boleta/Factura»** del papel y del ticket: nombraba un comprobante dentro de la nota. El dato sigue en el pedido y en el panel.
- **Correo:** la copia de la nota se titula «Nota de venta NV-…», sin «interna» ni aviso.
- **Facturas y boletas:** sus leyendas obligatorias (representación impresa, ambiente BETA, rechazada, pendiente) se mantienen sin un solo cambio; sus goldens de texto (PDF y ESC/POS) dicen lo mismo.

### Lo que recomendaba la investigación y lo que decidió el propietario

La investigación **sí recomendaba**, como medida PRUDENTE (no obligatoria), una leyenda corta en la nota: «No es comprobante de pago», con remisión al comprobante electrónico o a su número, por analogía con la leyenda «COPIA NO VALIDA PARA EFECTO TRIBUTARIO» del RCP, art. 11.7. **El propietario decidió no ponerla.** Ninguna norma la exige, y el riesgo real (art. 174.1 del Código Tributario) no lo cubre una frase sino emitir el comprobante a tiempo. En lugar de la leyenda se aplican las salvaguardas estructurales de más abajo.

## Base legal

Fuente: investigación del 2026-11-18 sobre normas primarias. No es asesoría tributaria.

| Afirmación | Norma / fuente | Confianza |
|---|---|---|
| No hay norma que obligue a poner una leyenda en un documento que no es comprobante. El RCP regula sólo comprobantes y su lista es cerrada («Sólo se consideran comprobantes de pago…»). Las leyendas que exige son para comprobantes y sus copias. | Reglamento de Comprobantes de Pago, R.S. 007-99/SUNAT, art. 2 — https://www.sunat.gob.pe/legislacion/comprob/regla/capituloI.pdf ; caps. III, IV y V — https://www.sunat.gob.pe/legislacion/comprob/regla/capituloIII.pdf , https://www.sunat.gob.pe/legislacion/comprob/regla/capituloIV.pdf , https://www.sunat.gob.pe/legislacion/comprob/regla/capituloV.pdf | Media-alta (búsqueda negativa; el cap. VI del RCP no se pudo revisar) |
| Lo sancionable es emitir u otorgar un documento no previsto como comprobante **en lugar** del comprobante. | TUO del Código Tributario, D.S. 133-2013-EF, art. 174 num. 1 y Tabla I — https://www.sunat.gob.pe/legislacion/codigo/textoCompleto-TUO-CT.pdf | Alta |
| SUNAT lo aplica a emisores electrónicos: entregar un documento no previsto en vez del CPE es la infracción del num. 1 (conclusiones 1 y 5). En su **análisis** (no en las conclusiones), el informe indica que SUNAT verifica el CPE otorgado por medios electrónicos; de ahí se deduce que otros papeles no lo sustituyen. | Informe N.° 127-2020-SUNAT/7T0000 — https://www.sunat.gob.pe/legislacion/oficios/2020/informe-oficios/i127-2020-7T0000.pdf | Alta en las conclusiones; la deducción sobre otros papeles es nuestra |
| El comprobante se emite al entregar o al cobrar, lo que ocurra primero (también anticipos). | RCP, art. 5 num. 1 y 4 (cap. I, enlace de arriba) | Alta |
| Las series de CPE del SEE empiezan por F, B, L o G; las de contingencia son numéricas. | Anexo de la R.S. 244-2019/SUNAT — https://ww3.sunat.gob.pe/legislacion/superin/2019/anexo-244-2019.pdf | Alta en la regla |
| Ninguna norma regula la apariencia de un documento interno, pero parecerse a un comprobante (título, serie, QR, «boleta»/«factura») aumenta el riesgo del art. 174.1. | Inferencia de lo anterior | Media |

Conclusión: ninguna norma exige la leyenda y una leyenda no protege si el comprobante no se emite. Lo que reduce el riesgo es que la nota **no se parezca** a un comprobante y que el comprobante se emita a tiempo.

## Salvaguardas estructurales (en lugar de la leyenda)

Todas tienen prueba automática (`backend/store/test_document_legends.py`):

1. **Texto de la nota sin vocabulario fiscal.** En A4, 80 mm, ESC/POS y correo (asunto, texto y HTML) no aparece ninguna de: «factura», «boleta», «comprobante», «SUNAT», «validez», «serie fiscal», «control interno», «registro interno», «representación impresa», «interna». Se comprueba también con una boleta o factura emitida para la misma venta, en varios estados.
2. **La numeración NV- nunca empieza por F ni por B.** El valor por defecto es `NV-`. La serie de notas de venta rechaza un prefijo que empiece por F o B (mayúsculas o minúsculas) cuando alguien lo **elige**: al crear la serie de la empresa o al cambiar un prefijo (panel o API). Una serie que ya tenía un prefijo así antes de esta regla sigue numerando. La serie de una sucursal nueva (ámbito por sucursal) **hereda** el prefijo de la plantilla de la empresa sin volver a validarlo, para que la primera venta de esa sucursal no falle. En producción sólo existe `NV-` (comprobado el 2026-11-18).
3. **Sin QR ni hash.** La nota no lleva código QR, «valor resumen» ni «representación impresa»; el ticket térmico no envía la orden de QR a la impresora.
4. **Título.** `document_legends.note_title()` devuelve «Nota de venta» y descarta un título de empresa que sea una denominación fiscal («factura», «boleta», «comprobante», «ticket fiscal», «recibo por honorarios», «liquidación de compra», «nota de crédito/débito»). La validación en el formulario llega con el campo de título.
5. **Un solo módulo de textos:** `backend/store/document_legends.py` reúne los textos de todos los documentos (título de la nota, avisos de la constancia y de la cotización, leyendas fiscales).

## Pendiente de confirmación por el contador

1. Que en cada caso en que se entrega una nota (tienda, pedido web, entrega diferida, anticipo) la boleta o factura se emite en el momento del art. 5 del RCP.
2. Si conviene entregar la nota al cliente o dejarla sólo como registro.
3. Si la ausencia total de leyenda es aceptable o prefiere la frase mínima que recomendaba la investigación.
4. **Sanciones aplicables:** el art. 174.1 se sanciona con cierre (Tabla I, notas 3 y 3-A). Falta confirmar el régimen de gradualidad y la discrecionalidad de SUNAT para este caso (https://orientacion.sunat.gob.pe/aplicacion-de-la-discrecionalidad-en-sanciones).
5. Si mostrar el desglose referencial de IGV en la nota es prudente.
6. Riesgos de protección al consumidor (Ley 29571), no revisados.
