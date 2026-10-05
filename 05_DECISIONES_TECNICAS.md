# Decisiones técnicas

Este archivo no existía en el baseline. Es la entrada resumida a las decisiones
de arquitectura del proyecto; el registro completo y su historia viven en los
ADR por dominio, que no se reescriben.

## Registro por dominio

| Dominio | Registro completo |
|---|---|
| Fiscal peruano (SUNAT, UBL 2.1) | [docs/adr-fiscal-c22a1.md](docs/adr-fiscal-c22a1.md) — ADR-1 … ADR-43 |
| Matriz de conformidad UBL 2.1 | [docs/sunat-factura-ubl21-matriz.md](docs/sunat-factura-ubl21-matriz.md) |
| Requisitos y catálogos SUNAT | [docs/sunat-cpe-requisitos.md](docs/sunat-cpe-requisitos.md) |

## Decisiones registradas en esta entrada

### DEC-MEDIA-01 · Una sola tubería de imágenes públicas; la galería sólo dice quién muestra qué

- **No hay un segundo almacén.** Las imágenes de producto pasan por
  `storefront_media`, la tubería del hero y de las categorías: recodifica desde los
  píxeles (ningún metadato sobrevive), genera la ruta en el servidor, pertenece a una
  empresa y borra lo que nadie muestra. El almacenamiento sigue detrás de
  `evidence_storage` (disco hoy, S3/R2 cuando haga falta) sin que el dominio lo sepa.
- **`ProductImage` no guarda archivos.** Guarda la DIRECCIÓN de la imagen
  (`/api/storefront/images/<id>`), el orden, el texto alternativo y cuál es la
  principal. Por ser una dirección en un campo de texto, el recuento de referencias que
  ya existía la ve sin conocer el modelo: una imagen de galería nunca se limpia.
- **`Product.image_url` se queda.** Lo leen el catálogo, el carrito, las líneas de
  pedido y la caja desde la fase 0. Es la dirección de la principal, mantenida por
  `product_media` en la misma transacción; un producto sin galería conserva la
  dirección que tuviera. Pasa de `URLField` a `CharField` validado, como la de las
  categorías, porque una ruta del propio sitio es un valor legítimo.
- **Una imagen subida no se coloca escribiendo su dirección.** Sólo la galería la
  coloca, y comprueba que es de la empresa. Así no hay forma de citar la imagen de otra.
- **Sin sucursal.** El catálogo es de la empresa, no de una sucursal; la autoridad es
  `products.view` / `products.manage`.
- **El contrato público crece sin romper.** `images` se añade al producto;
  `image_url` no cambia de significado.

### DEC-IMPORT-MEDIA-01 · Las imágenes de una carga masiva viajan con el libro y esperan sin almacén propio

- **Nombres de archivo, no direcciones.** «Imagen principal» e «Imágenes» (separadas
  por `|`) citan archivos que llegan en la misma petición, sueltos o en un ZIP, y se
  casan por nombre.
- **Inspeccionar → previsualizar → aplicar no cambia.** Lo que está mal con una imagen
  es un error de SU fila, con el archivo nombrado. Un trabajo con errores no se aplica.
- **Sin almacén temporal.** Entre previsualizar y aplicar, las imágenes que alguna
  fila usará esperan como imágenes «sin colocar» de la empresa del trabajo, en la
  tubería de siempre. Aplicar las coloca; la limpieza diaria (24 h) retira las de una
  previsualización abandonada. Un trabajo con errores no deja ninguna.
- **Aplicar no confía en la fila guardada.** Vuelve a comprobar la autoridad y coloca
  cada imagen con `claim`: tiene que existir todavía y ser de la empresa del trabajo.
  Si no, se deshace la importación entera.
- **El ZIP no se extrae.** Se revisa su índice antes de leer nada (rutas que salen,
  enlaces simbólicos, cifrado, número de entradas, tamaño expandido) y se lee entrada
  por entrada en memoria, con tope. Una entrada hostil rechaza el archivo completo.
- **Importar dos veces no duplica.** La huella del archivo original reconoce, dentro
  del mismo producto, una imagen que ya está en su galería. Nunca se compara entre
  productos ni entre empresas.
- **El Excel dentro del ZIP no se admite.** El libro se inspecciona antes de adjuntar
  nada; meterlo en el ZIP habría obligado a rehacer ese paso.

### DEC-EVIDENCE-01 · La evidencia de servicio se describe a sí misma y nunca se reescribe

- **Se extiende lo que había.** `RepairEvidence` ya era privada, por etapa, interna al
  nacer, anulable y nunca borrada. Se le añade una nota y tres etapas (repuestos, listo
  para entrega, garantía/reingreso); no hay un modelo nuevo ni una tabla por etapa.
- **La etapa no es un estado.** Subir una foto no mueve la orden. Por eso la evidencia
  no cuelga de `RepairStatusHistory`: explica qué pasó con su etapa, su autor y su hora,
  no con una transición.
- **La autoridad es la de producir la etapa.** Repuestos: quien repara. Listo para
  entrega: quien entrega. Garantía/reingreso: quien abre órdenes. No se creó ninguna
  capacidad de «evidencias».
- **La foto no cambia; la nota sí, con registro.** Corregir una nota deja el texto
  anterior en la auditoría. Una evidencia anulada conserva la nota que tenía.
- **El cliente lee la nota de lo que se le comparte.** Compartir es un acto explícito y
  el panel lo avisa. La lista de campos del cliente sigue siendo una lista cerrada.
- **Una petición por foto.** Subir varias a la vez son varias peticiones, cada una con
  su clave de idempotencia: la que falla se reintenta sin duplicar y las demás no
  dependen de ella.

### DEC-PAY-01 · Izipay se prueba contra un Izipay falso, y lo que el falso no puede probar se dice

- La petición del token no la ejercitaba ninguna prueba: se sustituía entera.
  `store/payments/fake_izipay.py` se pone en el socket, comprueba el contrato que el
  adaptador declara (POST, HTTPS, URL configurada, `Authorization` con la clave sin
  esquema, cabecera `transactionId` igual a la del cuerpo, comercio, orden, moneda,
  importe con dos decimales, tiempo de espera) y responde como la pasarela.
- El falso firma las notificaciones con su propio HMAC, no con el del adaptador, y sabe
  enviar lo que enviaría un atacante: editado después de firmar, firmado con otra clave,
  repetido y repetido con otro contenido.
- Es un arnés de pruebas. No tiene interruptor en la configuración ni ruta: no es una
  forma de marcar pedidos como pagados.
- **Lo que no se pudo verificar.** La referencia pública de `Token/Generate` es una
  aplicación de cliente y no se pudo leer. El arnés no exige nada sobre el resto del
  cuerpo: exigir una suposición haría que la suite certificara la suposición. Queda
  abierto IZIPAY-TOKEN-CONTRACT y sólo lo cierra el sandbox real:
  `IzipaySandboxSmokeTest`, que se omite sin credenciales (**BLOCKED/CREDENTIALS**).
- Sólo un intento que sigue esperando su respuesta puede marcarse como «fallo de
  integridad». Un mensaje contradictorio que llega después de autorizar se rechaza y no
  reescribe el registro del pago bueno.

### DEC-FISC-PRINT-01 · La representación impresa sigue los Anexos I y II de la RS 114-2019

- Fuente: los anexos oficiales de SUNAT (RS 114-2019, que sustituyen a los Anexos 1 y 2
  de la RS 097-2012), columna «Representación impresa – información mínima». No se
  encontró ningún «Anexo B» vigente que regule el papel; si existe otro documento, la
  auditoría se repite contra él.
- La etiqueta del documento del adquirente sale del catálogo 06 (RUC, DNI, carné de
  extranjería, pasaporte). Sin documento —la boleta a consumidor final— no se imprime
  ninguna etiqueta de documento.
- La leyenda nombra el comprobante. Cada ítem lleva su unidad de medida. El ticket de
  80 mm imprime el precio de venta unitario y el valor resumen. El importe en letras y
  la forma de pago se leen del XML firmado.
- Todo sigue saliendo del XML firmado o de la fila congelada, nunca de tablas vivas.
- Pendiente (FISCAL-PRINT-EXO): los totales de operaciones exoneradas e inafectas y los
  descuentos no se imprimen por separado; hoy el dominio sólo emite operaciones gravadas.

### DEC-FISC-LOGO-01 · El logotipo se congela con cada comprobante

- La tienda elige un logotipo para sus comprobantes: una imagen subida por ella
  (`CompanySettings.document_logo_url`). No vale una URL externa ni una ruta del
  frontend: el servidor necesita los bytes.
- Al emitir se guarda una copia propia bajo una clave que depende de su contenido
  (`companies/<id>/fiscal/logos/<sha256>.png`) y el comprobante la recuerda. Una
  reimpresión lleva el logotipo con el que se emitió aunque la tienda lo cambie o lo
  borre. Las notas heredan el del comprobante que modifican.
- La copia se aplana sobre blanco y se dibuja una vez, tal cual. Sin sombra, ni en el
  papel ni en la vista previa del panel: la sombra de la portada es para la portada.
- Un logotipo ilegible nunca detiene una emisión ni una reimpresión.

### DEC-PRINT-01 · El ticket lo imprime la tienda, no el navegador

- **Cola por sucursal.** `Printer`, `PrintAgent` y `PrintJob` pertenecen a una
  sucursal. El servidor nunca abre una conexión hacia una impresora.
- **El agente tira, el servidor no empuja.** Un programa de un solo archivo
  (`backend/print_agent/`) corre en la red del local, pregunta por HTTPS si hay trabajo
  para su sucursal y lo entrega a la térmica por el puerto 9100. No se abre ningún
  puerto del local. Su token vale para una sucursal y sólo para la cola.
- **Texto nativo, no PDF.** La térmica recibe ESC/POS: letra nítida, QR dibujado por
  la impresora y logotipo de un bit. El contenido sale del mismo contexto que el PDF
  de 80 mm, así que dicen lo mismo.
- **Sólo tras una confirmación autoritativa.** El trabajo se crea cuando un comprobante
  queda firmado o una nota de venta se crea, sobre un pedido pagado. Ni el navegador ni
  el agente pueden pedir el ticket de algo que no se cobró.
- **Idempotente en sus dos mitades.** La clave del trabajo sale del documento: confirmar
  la venta dos veces no crea dos. Cada entrega lleva un token nuevo: confirmar dos veces
  no cambia nada y una confirmación antigua se rechaza. El papel no sale dos veces
  porque el agente lleva un diario de lo impreso.
- **Sólo la red del local.** La dirección de una impresora tiene que ser privada o
  `.local`; lo comprueban el servidor al guardarla y el agente antes de conectar.
- **Del texto sólo sale texto.** Lo que escribe el comprador —nombre, dirección,
  notas— pierde todo carácter de control antes de ir a la impresora: un `ESC` dentro
  de un nombre sería una orden (abrir el cajón, cortar).
- **Espera entre intentos y caducidad.** Tras un fallo el trabajo espera 10, 30, 60 y
  120 segundos; sin eso una impresora apagada quema los cinco intentos en un segundo.
  Un ticket que nadie recogió en 12 horas ya no se imprime, y uno cuyo pedido dejó de
  estar pagado se cancela al ir a entregarlo.
- **«En cola» no es «enviado».** La venta dice si hay un agente del local escuchando.
  Sin él, la caja lo avisa y sigue ofreciendo imprimir desde el navegador. Desactivar
  una impresora da por fallidos sus trabajos pendientes, y un reenvío va a la impresora
  que el local tiene hoy.
- **El diario del agente no se fía del número de fila.** Cada trabajo lleva un
  identificador que no se repite entre bases; el número sí se repite tras una
  restauración o al cambiar de servidor.
- **Redes escritas a mano.** `is_private` cambia entre versiones de Python (una IPv4
  metida en una IPv6 pasaba por privada). Servidor y agente comparan contra la lista
  explícita de redes locales. El agente no sigue redirecciones.
- **Límite conocido.** Sólo impresoras en red. USB y Bluetooth quedan fuera.

### DEC-SF-06 · Las imágenes de la tienda las sube la tienda

Sustituye a la parte de DEC-SF-01 que decía «el hero es una losa oscura para todas
las tiendas» y cierra STOREFRONT-HERO-VARIANT.

- Una imagen de la tienda (hero, categoría, campaña) es contenido del tenant. No
  hay ninguna compilada en el frontend ni en una migración.
- El panel sube el archivo y guarda su dirección en el hueco. La dirección es una
  ruta del propio sitio (`/api/storefront/images/<id>`), así que no cambia si la
  tienda cambia de dominio ni depende de un host externo.
- Se guardan en el almacenamiento que ya existía para las evidencias, bajo otra
  ruta, y se sirven por Django. No se añade infraestructura: ni un bucket público
  ni un servidor de archivos. Son públicas porque van en la portada; las
  evidencias siguen siendo privadas.
- El formato se conserva. Las evidencias se aplanan sobre blanco y pasan a WebP
  porque son fotos; una imagen de la tienda suele ser un recorte en PNG sin fondo
  y aplanarla le pondría un rectángulo detrás.
- Presentación: cualquier imagen subida por esta tubería usa una sombra suave en
  el storefront. En un PNG/WebP transparente `drop-shadow` sigue la silueta y evita
  el efecto de recorte «pegado»; las URLs externas no reciben esta regla.
- Además de hero/categorías/campañas, la página estable puede guardar una imagen
  editorial de servicio técnico y otra de ubicación. Son opcionales y por tenant.
- El estilo del hero (`dark` o `light`) lo elige cada tienda. `dark` es el valor
  por defecto: ninguna tienda cambia de aspecto por esta decisión.
- Descartado: escribir las imágenes del piloto en el repositorio (no hay
  evidencia de licencia y otra tienda las vería), y guardar la imagen como URL
  externa escrita a mano (obliga al dueño a alojar archivos).

### DEC-42 · Atribución determinista del descuento de promociones a componentes

Regla del motor SaaS, no del tenant piloto. Una promoción reparte su descuento YA
DECIDIDO entre los componentes consumidos en proporción a su valor regular
(`unit_price × quantity_used`), con ROUND_DOWN a céntimos, céntimos restantes a los
mayores residuos y desempate por `product_id` ascendente. Cierra exacto o falla. Se
congela en `AppliedPromotion.metadata["components"]`; el snapshot congelado es la
autoridad histórica (se valida y se usa tal cual), un snapshot anterior se
reconstruye en memoria sin escribir, y un snapshot a medias falla cerrado. No cambia
precio, stacking, prioridades, unidades consumidas ni totales. Detalle, motivos y
descartes: [ADR-42](docs/adr-fiscal-c22a1.md#adr-42--atribución-determinista-del-descuento-de-una-promoción-a-sus-componentes).

### DEC-43 · Descuentos declarados en UBL sin doble descuento

Cupón y manual: un `cac:AllowanceCharge` global con código `02` del Catálogo N.º 53
vigente; promoción: uno por línea rebajada con código `00`. Importes en valor de
venta derivados del snapshot (`subtotal_amount`, `taxable_amount`, `tax_rate`).
`LegalMonetaryTotal/LineExtensionAmount` es la base imponible (regla 3278),
`PayableAmount` es `Order.total`, y `AllowanceTotalAmount` se omite porque SUNAT lo
reserva a descuentos que no afectan la base y lo resta del importe a pagar (reglas
3300/3280). Fuente: archivo oficial de reglas de validación (21.04.2025). Detalle:
[ADR-43](docs/adr-fiscal-c22a1.md#adr-43--un-descuento-se-declara-donde-nació-con-el-código-de-su-nivel-y-no-se-resta-dos-veces).
