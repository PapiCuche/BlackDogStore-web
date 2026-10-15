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

### DEC-DELIVERY-01 · La entrega es un registro propio JUNTO al pedido, y el flete es un dato

Base de DELIVERY-01 (transportistas, libreta de direcciones, registro de entrega). Lo que el
código cumple y las pruebas vigilan:

- **Aditivo.** `Order` conserva `delivery_method` (con sus tres opciones) y sus columnas de
  dirección, y el checkout web y v1 —con su huella y sus campos prohibidos—, los precios, los
  documentos, los PDF, los tickets y los correos siguen leyendo ESAS columnas. Nada de esta
  fase las cambia. Lo nuevo vive al lado: `Carrier`, `CustomerAddress` y `OrderDelivery`.
- **`OrderDelivery` es una copia, no un enlace.** Nace con el pedido, de las columnas del
  pedido (`delivery_services.snapshot_for_order`, en un punto de guardado que no puede hacer
  fallar un checkout), y guarda en texto lo que necesita: `carrier_name` además de `carrier`,
  y la dirección entera. No apunta a ninguna entrada de la libreta (la columna
  `source_address`, que ningún código escribió nunca, se retiró). Renombrar o apagar un
  transportista, editar o quitar una dirección de la libreta, no reescribe la entrega de un
  pedido que ya salió.
- **El flete es información.** `freight_amount` y `freight_paid_by` dicen qué cobra el
  transportista y quién lo paga. No entran al total del pedido, a su impuesto ni a su
  comprobante, y el comprador no los ve.
- **El personal escribe la entrega, no el pedido.** Destinatario, puerta o agencia (sólo entre
  `national_address` y `agency_pickup`; nunca hacia o desde `pickup_store`), transportista,
  flete, guía y seguimiento. `Order.delivery_method`, el total y el estado de despacho no se
  tocan desde ahí. Ver es `sales.orders.view`, escribir es `sales.orders.manage`, por
  `visible_orders`. Los transportistas se leen con `sales.orders.view` y se cambian con
  `settings.manage` sobre toda la empresa; se desactivan, no se borran; no se siembra ninguno.
- **Un enlace sólo es un enlace si es https.** La plantilla de seguimiento de un transportista
  admite un único marcador, `{guia}`, fuera del dominio; el número de guía se escribe
  codificado; y tanto el servidor como las pantallas se niegan a dar como enlace algo que no
  empiece por `https://`.
- **El enlace de seguimiento sabe de quién es** (`OrderDelivery.tracking_url_typed`). Uno
  DERIVADO sale de la página del transportista y de la guía: se rehace cuando cambia el
  transportista o la guía, y queda vacío si falta cualquiera de los dos o el transportista no
  tiene página. Uno ESCRITO por una persona sólo cambia cuando se envía el propio enlace;
  enviarlo vacío vuelve al derivado, y escribir exactamente la página del transportista no
  cuenta como escrito. Un enlace derivado no se rehace mientras su transportista y su guía no
  cambien: el registro sigue siendo una copia.
- **Lo que no se puede leer no se guarda.** La fecha de envío vale entre el 2000-01-01 y un
  año hacia adelante; el flete se acota antes de redondearse; un texto con medio carácter (un
  sustituto suelto de UTF-16) se rechaza donde ya se rechazaban los caracteres de control; y
  la escritura del personal se relee dentro de su transacción antes de confirmarse.
- **La libreta es del comprador en UNA tienda.** Cuelga de su `Customer` de esa empresa; una
  entrada de otro comprador o de otra tienda responde 404 en todos los verbos. Una
  predeterminada entre las activas (regla de la base de datos, movida en una transacción con
  la ficha bloqueada), tope de diez, se desactivan y no se borran. En el checkout sólo RELLENA
  el formulario en el navegador: lo que se envía al servidor no cambia. Como las entradas
  quitadas siguen siendo filas, un comprador tiene como mucho `MAX_ADDRESS_ROWS` (200) entre
  activas y quitadas; al llegar, la entrada nueva ocupa la fila quitada más antigua. Una ficha
  que la tienda archivó no admite una entrada nueva ni una dirección escrita sobre otra, y la
  respuesta no dice por qué. Una entrada de agencia cuyo transportista se apagó se puede
  corregir: el transportista sólo se juzga cuando es el transportista lo que cambia.

**Lo que espera al propietario** (no empezado): ofrecer «recojo en agencia» en el checkout,
cobrar el flete, e imprimir transportista, guía o destinatario en documentos y correos.
Tampoco se envía al servidor qué dirección de la libreta se usó. El comprador no ve la clave
de recojo ni el destinatario.

**Marcha atrás de las migraciones.** Deshacer `0121` no borra nada: para entonces los
registros llevan lo que escribió el personal. Quien quita los registros es deshacer `0119`,
que quita la tabla.

### DEC-CLIENT-SEC-01 · La revisión de pedidos es un consejo, y de la persona se guarda lo mínimo

Fase 1 de CLIENT-SEC-01 (perfil del comprador, correo verificado, evaluación y revisión
manual de pedidos). Cuatro reglas que el código cumple y las pruebas vigilan:

- **Consultiva por defecto.** Todo pedido de la web o de la app se evalúa al crearse
  (`order_risk.assess`, dentro de la transacción del pedido y en un punto de guardado
  propio: si falla, el pedido queda sin evaluación y la venta sigue). La evaluación no
  rechaza un checkout, no toca un pago y no cancela nada. La decisión de quien revisa
  (`order_risk.review`) tampoco: cambia la evaluación y nada más. Lo único que puede
  retener —«listo para retiro», «enviado» y «entregado» mientras la revisión de un pedido
  PAGADO está pendiente— es una opción
  de cada empresa, `hold_orders_pending_review`, **apagada por defecto**; encendida, la
  levanta cualquiera de las dos respuestas, porque lo que se pide es que alguien mire.
  Con las dos opciones como nacen, checkout, pago y despacho son los de la versión anterior.
- **Proporcional.** Ocho señales con nombre y peso (`order_risk.SIGNALS`, `WEIGHTS`) y
  dos umbrales (`MEDIUM_SCORE`, `HIGH_SCORE`). Lo que parece la mayoría de compradores
  honestos —primera compra sin cuenta— queda en `low` y no pide a nadie. Se pide una
  revisión con nivel `high` o cuando el cliente está archivado en la tienda. Todo se
  calcula **dentro de la empresa del pedido**: ninguna señal cuenta pedidos, clientes u
  orígenes de otra empresa.
- **Sin dato personal en la evaluación.** `OrderRiskAssessment.signals` guarda los
  CÓDIGOS de las reglas, nunca el valor que las activó. El origen se guarda como
  `ip_hash`: un HMAC de la dirección con una clave derivada de `SECRET_KEY` (etiqueta
  propia) y ligado a la empresa, de modo que la misma dirección da otro valor en cada
  tienda. **No se guarda la IP ni el navegador.** La auditoría de la revisión lleva
  decisión, nivel y códigos; la nota de quien revisa se queda en la evaluación y no se
  copia a la auditoría ni a los registros. El aviso al personal dice qué pedido y qué
  nivel, no por qué. El comprador no ve nada de esto en ninguna superficie.
- **Sin biometría ni fotografías de documentos.** El perfil valida la FORMA de un
  teléfono y de un documento (`identity_validation`), y la forma no es identidad: nada
  aquí pide un selfie ni la foto de un DNI, ni de forma general ni por nivel de riesgo.
  Si algún día hiciera falta, es una decisión del propietario con su base legal, no una
  consecuencia de esta fase.

**Correo verificado.** `UserProfile.email_verified_at` es un hecho propio: lo escriben
los caminos que prueban que alguien lee el buzón (enlace, código —también el que pide una
cuenta con sesión—, todo enlace de recuperación consumido, Google cuando nombra esa misma
dirección, invitación de personal enviada a ella) y ninguno más. `is_active` sigue significando «puede entrar», y
`is_email_verified` de la API v1 sigue significando lo que significaba. Nulo es
«desconocido», y desconocido no es verificado.

**Capacidades reutilizadas, catálogo intacto.** Ver la evaluación es `sales.orders.view`;
decidir la revisión es `sales.orders.manage`, con el alcance por sucursal de
`visible_orders`. En el puente legacy del piloto deciden ventas y administración, no
almacén (como cancelar). Las dos opciones de empresa viven en la pantalla de
Configuración (`company.manage`) y cambiarlas exige además `settings.manage`. No se
añadió ninguna capacidad ni se tocó ningún rol.

**Enmiendas tras la revisión independiente** (misma rama, antes de publicar):

- **ENMIENDA A DEC-CUSTOMER-01 · el documento de un invitado no abre la ficha de una
  cuenta.** `link_order_to_customer` enlazaba la compra de un invitado con la ficha que
  tuviera su documento, tuviera o no cuenta. Pero `Order.customer.user` es propiedad
  (`tenancy.customer_owned_orders`) y es adonde van los avisos del comprador, y el checkout
  sólo comprueba la FORMA de un documento: una cuenta que dejara en su ficha el DNI de un
  extraño leía el siguiente pedido de invitado de ese extraño y recibía sus avisos. Ahora un
  invitado sólo enlaza por documento con una ficha SIN cuenta (`_guest_record`, en el primer
  intento y en el reintento tras perder una carrera); si el documento está en la ficha de una
  cuenta, el pedido queda sin enlazar, con su instantánea, sin ficha gemela y sin error para
  quien compra. Una venta con sesión sigue enlazándose sólo por su cuenta. Es el único cambio
  deliberado a un comportamiento anterior, y sólo lo estrecha. Unir una cuenta con una ficha
  de mostrador sigue siendo un acto deliberado (el reclamo de una reparación, o la tienda).
- **La revisión empieza cuando el pedido se PAGA.** Las señales se anotan al crear el pedido,
  pero la revisión pasa a «por revisar» —y el aviso al personal sale— en la confirmación del
  pago (`order_risk.review_when_paid`, en un punto de guardado, incapaz de hacer fallar una
  confirmación o una conciliación). Un pedido que nadie paga no llega a la cola ni a la
  campana. La cola y la retención miran sólo pedidos pagados. La retención incluye «listo
  para retiro», porque ese estado avisa al comprador de que venga.
- **La velocidad cuenta COMPRADORES, y a los demás sólo si pagaron** (ampliado en la segunda
  revisión, más abajo: también cuenta pedidos pagados). `velocity_ip` y
  `velocity_contact` cuentan compradores distintos (quien reintenta un pago es uno), y el
  pedido de OTRO comprador cuenta sólo si se pagó: un pedido sin pagar no cuesta nada, y
  contarlo dejaba marcar el pedido de un tercero o llenar la cola desde un navegador. El
  origen de una dirección IPv6 es su /64.
- **El documento es de quien probó su correo, y pocas veces al día.** Guardar un documento
  responde algo sobre los demás clientes de la tienda: sólo lo hace una cuenta con
  `email_verified_at`, con un cupo propio de tres cambios al día por cuenta (rechazos
  incluidos). Una cuenta con sesión puede verificar su correo con un código enviado a SU
  dirección (`account/verify-email/…`, las reglas de `verification_codes`), y todo enlace de
  recuperación consumido también deja la prueba.
- **Lo que la tienda registró no lo reescribe su titular.** El perfil no cambia nada de una
  ficha archivada (responde como un guardado sin cambios); el documento queda fijo cuando la
  ficha la creó el personal o ya tiene un pedido pagado; y un documento se sustituye, no se
  borra.
- **Una ficha vacía no bloquea el reclamo de una reparación**: si la cuenta sólo tiene la
  ficha que hizo «Mi cuenta» —sin documento, sin notas, sin nada que apunte a ella—, el
  reclamo probado pasa la cuenta a la ficha reclamada y quita la vacía.

**Enmiendas tras la segunda revisión independiente** (misma rama, antes de publicar):

- **El checkout con sesión no es otra puerta al documento** (endurecido en la tercera
  revisión, más abajo: ya no lo toma nunca). La ficha que crea una compra con
  cuenta toma el documento escrito en el checkout sólo con la regla del perfil: la cuenta
  probó su correo y nadie en esa tienda tiene ya ese documento
  (`customer_services._record_may_take_document`). Si no, la ficha nace SIN documento, la
  venta sigue sin decir nada, y el pedido conserva lo que se escribió. La ficha de un
  invitado lo toma como siempre. Sin esto, un checkout sin pagar —gratis y repetible— dejaba
  el DNI de un extraño en la ficha propia, saltándose el correo probado y el cupo.
- **El código «verificar mi correo» tiene su propio propósito y su correo no lleva enlace.**
  `AccountToken.PURPOSE_OWN_EMAIL_PROOF`. Un token de verificación es lo que dice «registrada y
  nunca confirmada» (`accounts.is_unverified`); emitido para una cuenta ACTIVA, hacía que una
  cuenta apagada después se reactivara sola por la recuperación, por el enlace o por el
  reenvío público. Ninguno de esos caminos mira el propósito nuevo. El correo lleva sólo el
  código, que no vale nada sin la sesión que lo pidió, y el código queda ligado a la
  dirección a la que se envió: si la cuenta cambia de correo, no sirve.
- **El cupo de documento se cuenta en la auditoría, no en la memoria del proceso.** Cada
  intento que llega a la pregunta «¿se puede guardar este documento?» —aceptado o
  rechazado— es una entrada `customer_document_change_attempted` (sólo el resultado, nunca
  el documento), y el cupo cuenta las de las últimas 24 horas con la fila de la cuenta
  bloqueada. Se eligió la auditoría y no una tabla propia porque esas entradas son las que
  la auditoría quiere de todos modos, sin migración y sin un segundo contador que mantener
  fiel; un intento por encima del cupo no escribe nada, así que son como mucho tres filas al
  día por cuenta. Un doble toque en el primer guardado gasta un intento. `CACHES` no se
  tocó: los límites de DRF siguen en la memoria del proceso (THROTTLE-CACHE-01).
- **La velocidad cuenta también PEDIDOS PAGADOS, y se vuelve a mirar al pagar.** Además de
  tres compradores distintos, se nota el sexto pedido pagado de un mismo origen en una hora
  (`VELOCITY_IP_ORDERS`) o con un mismo teléfono, correo o documento en un día
  (`VELOCITY_CONTACT_ORDERS`), este incluido y lo haga quien lo haga. Los pedidos sin pagar
  no cuentan nunca, así que quien reintenta el pago de UNA compra sigue siendo una compra.
  Y al pagarse un pedido (`review_when_paid`) las dos señales se preguntan otra vez —para
  entonces los pedidos hechos a su lado ya se pagaron—, en un punto de guardado propio
  dentro del de la revisión: sólo AÑADE a lo anotado, recalcula puntaje y nivel, y si falla
  la revisión que ya tocaba se abre igual.
- **Un dígito es 0-9 también en el checkout** (web y v1: documento y teléfono). Nada más de
  esos dos validadores cambió.
- **Una ficha archivada nunca es «vacía»**: el reclamo de una reparación no la borra.

**Enmiendas tras la tercera revisión independiente** (misma rama, antes de publicar). Las
tres primeras cierran un camino anterior a la fase: un checkout anónimo y SIN PAGAR con el DNI
de otra persona y el contacto propio dejaba una ficha con ese documento, y después el pedido
de la persona real, sus avisos, su reparación en el mostrador y por último la ficha entera
iban a parar a quien la sembró.

- **El aviso de un pedido va al contacto del propio pedido.** «Listo para recoger» y «enviado»
  se escriben a `order.customer_email`, nunca al correo de la ficha a la que el pedido está
  enlazado (`notification_services._recipient_email`). Sin correo en el pedido, no hay correo.
  Los avisos de una reparación siguen yendo al correo de la ficha: ahí la ficha la abrió el
  personal con la persona delante. WhatsApp sólo envía avisos de reparaciones, así que no
  cambia.
- **Ficha ESTABLECIDA** (`customer_services.is_established`): la que creó el personal, o la
  que ya tiene un pedido PAGADO (enlazado a ella o de su cuenta en esa tienda) o una
  reparación. Todo lo demás es provisional —la que dejó un checkout sin pagar, la que una
  cuenta declaró para sí en «Mi cuenta»—: hacerla no cuesta nada y no prueba de quién es el
  documento que lleva. De ahí salen tres reglas:
  - *El pedido de un invitado sólo se enlaza por documento con una ficha establecida y sin
    cuenta.* Si el documento está en una ficha provisional, el pedido queda sin enlazar, sin
    error y sin ficha gemela (enmienda otra vez a DEC-CUSTOMER-01).
  - *Un pedido de invitado se archiva cuando se PAGA* (`file_paid_guest_order`, en la
    confirmación del pago, en un punto de guardado e incapaz de hacerla fallar). Pagar es lo
    que al checkout le falta: cuesta dinero y deja rastro. Si la ficha provisional que tiene
    su documento lleva el mismo correo que el pedido, es la misma persona reintentando tras
    una tarjeta rechazada y el pedido se enlaza a ella (desde la revisión previa al despliegue:
    mismo nombre, teléfono y correo); si no, esa ficha suelta el documento
    (auditado: `customer_document_released`, sólo nombres de campo) y el pedido recibe una
    ficha propia hecha con lo que se escribió en él. Así el primer pedido pagado de un
    invitado establece su ficha para los siguientes.
  - *El personal no es enviado a una ficha provisional.* Al crear o corregir una ficha con un
    documento que está en una ficha NO establecida —con cuenta o sin ella—, esa ficha suelta
    el documento y la del personal se guarda, en una transacción y con auditoría. La cuenta
    que lo había declarado conserva su ficha, sin el documento, y no se le dice nada. Una
    ficha establecida responde el 409 de siempre.
- **El documento de la ficha de una cuenta sólo entra por «Mi cuenta».** Un checkout con
  sesión NUNCA lo escribe (sustituye a la regla de la segunda revisión, que lo permitía con
  el correo probado y dejaba pasar el cupo y servía de oráculo): la ficha nace sin documento
  y el pedido conserva lo escrito. A cambio, un documento VACÍO no está congelado: su
  titular, con el correo probado, lo completa una vez aunque la ficha ya tenga un pedido
  pagado o la haya creado el personal, con la comprobación de conflicto, el cupo y la
  auditoría de siempre; cambiar uno que ya está sigue siendo de la tienda.
- **La libreta no acompaña a la ficha a otra cuenta.** Cuando la tienda desvincula una cuenta
  (`unlink_account`), las direcciones de la ficha se apagan.
- **Un enlace devuelto no es un enlace escrito.** Enviar de vuelta el enlace derivado tal como
  estaba, junto con otra guía u otro transportista, no lo congela; `tracking_url_typed` viaja
  en la respuesta del personal.
- **La libreta ya no rechaza por el tope de filas.** Al llegar a 200, la entrada nueva ocupa
  la fila quitada más antigua, reescrita entera.
- **Tres dispositivos, un comprador.** En la señal de contacto, pedidos con el mismo teléfono,
  correo Y documento son un solo comprador (siguen contando como pedidos pagados).
- `ops_status` dice, como NOTA y nunca como alarma, cuántos pedidos pagados esperan revisión
  y desde cuándo, y cuántos pedidos en línea del último día no tienen evaluación.

**Ajustes tras la revisión previa al despliegue** (misma rama):

- **Un pedido pagado siempre tiene una ficha.** Si su documento está en la ficha de una
  CUENTA, esa ficha no se toca y el pedido recibe una ficha propia SIN documento, hecha con lo
  escrito en él. Sin esto, quien compró una vez con sesión y vuelve a comprar sin ella —o la
  persona cuyo documento declaró otra cuenta— no recibía nunca «listo para recoger» ni
  «enviado». La cuenta no ve ese pedido.
- **«La misma persona reintentando» es el mismo nombre, teléfono Y correo** que la ficha
  provisional, no sólo el correo (que cualquiera puede escribir). Si falta o difiere uno, la
  ficha suelta el documento y el pedido recibe la suya.
- **Archivar un pedido pagado no puede dejar nada a medias.** Una carrera perdida (el personal
  archivando a la vez, o dos pagos) se reintenta una vez; si aun así falla, el pedido queda
  como estaba también en memoria, para que el aviso siguiente no apunte a una ficha deshecha.
  Una ficha provisional que no se puede guardar (inválida por otro motivo) no suelta el
  documento: al personal se le muestra con el 409 y al pedido pagado se le da una ficha sin
  documento. Dos personas archivando el mismo documento a la vez: la segunda recibe el 409.
- `ops_status` cuenta como «sin evaluación» sólo los pedidos posteriores a la primera
  evaluación que exista: el día del despliegue no avisa de los pedidos anteriores.

**Lo que esta fase no decide** (queda como deuda en `docs/AUDIT_MEMORY.md`): unificar los
tres validadores anteriores de teléfono y documento, cuánto tiempo se conserva `ip_hash`, y
—decisión del propietario— los pesos y umbrales de la evaluación y si el monto por sí solo
debe abrir una revisión.

### DEC-CP2-HOST-01 · Lightsail en São Paulo, apertura por separado

Aprobado por el propietario con `GO AWS`, 2026-10-07: instancia Ubuntu 24.04 LTS
`blackdogstore-prod-01` en `sa-east-1a`, plan IPv4 de US$24/mes (4 GB RAM,
2 vCPU, 80 GB SSD), Static IP adjunta. IPv6 desactivado. Se conserva Caddy como
único proxy, PostgreSQL privado y un proceso gunicorn. Sin servicios AWS adicionales.
SSH restringido en el firewall AWS a la administración y browser SSH de Lightsail;
HTTP/HTTPS permanecen cerrados durante preparación. `GO DNS` es independiente,
registros DNS only; la apertura pública requiere otra orden del propietario.

### DEC-DEVICE-01 · Un equipo se identifica por lo que su tipo lleva, y lo que no tiene es NULL

- **La regla es del servidor** (`device_identity`): serie obligatoria en teléfono,
  tablet, laptop, computadora, consola y reloj; IMEI obligatorio sólo en teléfono;
  segundo IMEI opcional. El formulario no la repite.
- **El IMEI se valida con su dígito de control (Luhn).** Un marcador («N/A», «0000»)
  no es un identificador: se rechaza. Lo ausente se guarda vacío/NULL, porque dos
  equipos con el mismo marcador chocarían en las reglas que impiden registrar el mismo
  equipo dos veces.
- **Lo que no se puede leer no bloquea la recepción**, pero se explica: un motivo
  permite crear la orden con un identificador obligatorio vacío.
- **El historial del equipo cruza órdenes dentro de la empresa** y, al mostrarse,
  sólo cuenta las órdenes de las sucursales de quien pregunta.

### DEC-TRACKING-01 · El seguimiento del cliente es por enlace, y el enlace abre una orden

- **Un token opaco, no un número.** 128 bits aleatorios más un sello HMAC con clave
  derivada; se comprueba el sello antes de tocar la base. No contiene ni permite
  deducir el id o el número de la orden, tiene una sola escritura válida, y todo lo
  que no resuelve responde el mismo 404. Revocable y reemplazable; un enlace vivo
  por orden; una lectura nunca recrea uno revocado.
- **La vista pública es una lista de lo que sí sale**, armada con los serializadores
  de la superficie del cliente: sin notas internas, sin personal, sin sucursal, con
  serie e IMEI enmascarados. Las fotos son las que el taller compartió.
- **La superficie Bearer del cliente (ADR de autenticación v1) no cambia.** La web
  lista las reparaciones de la cuenta por cookie (`/api/account/repairs/`) y el
  detalle se lee siempre por el enlace: una sola vista del cliente.
- **Quien tiene el enlace decide como el cliente.** Por eso el personal no lo recibe
  por poder abrir la orden: se revela con un acto explícito y auditado que exige
  `service.quotes.record_decision`.
- **Sumar una orden a una cuenta entrega un cliente entero**, así que pide el enlace
  y el documento registrado, con intentos limitados. No se vincula por correo,
  teléfono ni nombre, y la tienda puede deshacerlo.

### DEC-QUOTE-01 · Una aprobación dice quién la dio y por dónde, y vale para lo que se aprobó

- **El canal lo pone la puerta, no el cuerpo de la petición**: cuenta del cliente,
  enlace de seguimiento, o personal (presencial, llamada, WhatsApp, otro).
- **El personal anota; no finge.** Una decisión registrada por el personal lleva
  `source=staff`, quién la anotó y el canal; la cuenta del cliente no figura como
  autora. Tiene capacidad propia (`service.quotes.record_decision`), separada de
  cotizar.
- **Una cotización aprobada no se edita.** Cambiar el trabajo es reabrirla: queda
  `superseded`, la orden vuelve a diagnóstico y nace una revisión nueva. Sólo antes de
  empezar la reparación.
- **El ticket de 80 mm existe sólo para una aprobación vigente**, lo decide el
  servidor, reutiliza el trazado de los demás tickets y no es un comprobante de pago.

### DEC-WHATSAPP-01 · WhatsApp avisa por la API oficial; las credenciales son referencias

- **Sólo la Cloud API de WhatsApp Business.** Nada de WhatsApp Web ni automatización
  de navegador. El proveedor está detrás de una interfaz (`store/messaging`) con un
  falso que no envía, como el de Izipay.
- **Ningún secreto en la base de datos.** Por empresa se guarda el NOMBRE de la
  variable de entorno de cada credencial; lo fija quien opera la instalación
  (`configure_whatsapp`), nunca la API, y sólo en el espacio `WHATSAPP_`. La API
  informa con booleanos.
- **Consentimiento explícito.** Sin `opt-in` registrado no se escribe; darse de baja
  gana, también por mensaje.
- **El aviso es un resumen y un enlace.** Plantilla aprobada con tres parámetros
  (nombre, número de orden, enlace). Nunca IMEI, notas ni importes.
- **Bandeja de salida en la transacción.** La fila de entrega se escribe con el
  cambio de negocio y se envía al confirmarse; lo demás lo reintenta una tarea. Un
  fallo queda anotado y no deshace nada. Un aviso es un mensaje: fila única y
  bloqueada mientras se envía. Una petición sin respuesta no se reenvía sola.
- **El webhook cree lo que viene firmado** (`X-Hub-Signature-256` sobre el cuerpo
  crudo), sólo toca lo de su empresa y los estados sólo avanzan.

### DEC-SERIAL-01 · Un equipo con serie es stock, no un inventario paralelo

- **`BranchStock.quantity` sigue siendo la única cifra que decide una venta.** Para
  un producto con serie equivale siempre a los equipos DISPONIBLES de la sucursal.
- **Se impone en el escritor único del Kardex.** `create_stock_movement` rechaza
  mover un producto con serie sin los equipos de que se trata; como todo cambio de
  stock pasa por ahí, ningún ajuste, carga, transferencia o recuento puede
  desajustar la cifra.
- **La venta asigna equipos concretos** bajo el bloqueo de la fila de stock, en la
  misma transacción que la salida. Cuáles, lo dice DEC-SERIAL-02.
- **Identificadores únicos por empresa, en cualquier estado.** Un equipo vendido
  conserva su fila y, si vuelve, es la misma.
- **El modo sólo cambia con stock cero**, y el escritor relee el modo cuando ya tiene
  bloqueado el producto.
- **«Equipos disponibles» es un subconjunto de «Unidades en stock»**, no un sumando.

### DEC-SERIAL-02 · El equipo que sale es el que una persona identificó (SERIAL-PICK-01)

- **En caja, nada con serie se vende sin decir qué equipo.** La venta lleva, por cada
  unidad, el equipo y cómo se identificó. Sin eso se rechaza entera. Antes el sistema
  vendía el más antiguo de la sucursal y el papel imprimía ese IMEI, fuera cual fuera
  el que se entregaba.
- **Dos formas, y no pesan igual.** `scanner`: el código entra a ráfaga y se acepta.
  `manual`: cuando el lector no lee. A mano, un equipo que tiene IMEI se identifica por
  su IMEI (15 dígitos y su dígito de control), no por la serie, y el panel **muestra**
  el equipo encontrado para que la persona confirme que es ese.
- **`OrderUnit` es el registro**: qué equipo, de qué pedido, cómo se identificó, quién y
  cuándo. Es lo único que leen la nota de venta, el ticket y, después, la garantía.
  Queda además en auditoría (`order_unit_identified`), sin el IMEI en la línea.
- **Apartar no es identificar.** El pago de un pedido web aparta un equipo
  (`StockUnit.order`) para que nadie más lo venda; no dice cuál se entrega. Lo dice
  quien lo entrega, igual que en caja. Si entrega otro del mismo modelo, el apartado
  vuelve al estante y el cambio queda en el Kardex como una entrada y una salida.
- **Dos pedidos en espera pueden llevarse la caja del otro.** Mientras nadie lo haya
  identificado, qué equipo apartó el pago para qué pedido no importa: quien entrega toma
  cualquiera y los dos pedidos intercambian su apartado, sin línea en el Kardex.
- **Un pedido con equipos sin identificar no sale.** «Enviado» y «entregado» se rechazan
  (409) mientras falte alguno. La comprobación se hace con el pedido bloqueado.
- **Lo que salió no se cambia** (`OrderUnit.left_at`). Se marca al enviar o entregar —y al
  cobrar, en caja— y no se borra: devolver el pedido a «en preparación» no reabre el
  equipo que salió.
- **Un equipo devuelto antes de salir deja de ser el del pedido.** El pedido vuelve a no
  nombrar ninguno y no sale hasta identificar el que sí se entrega; ese sale con una sola
  línea de Kardex, porque la devolución ya puso uno de vuelta. Devuelto después de salir,
  el registro se conserva: el papel de esa venta lo nombró.
- **Un pedido debe un equipo sólo si su stock salió equipo por equipo.** Si se pagó cuando
  el producto se contaba por cantidad y el producto pasó a llevar serie después, no hay
  equipo que ese pedido pueda nombrar.
- **Cómo llegó el código lo declara el panel** (el ritmo de las teclas del texto que se
  envía, o el botón «a mano»): un servidor no puede ver un lector. La regla del IMEI
  para lo escrito a mano se exige donde el servidor recibe el código —la consulta del
  equipo en caja y la entrega de un pedido—. La venta de caja lleva el equipo ya
  consultado, por su identificador, y guarda lo que el panel declaró y quién.
- **Una negativa no dice dónde más está un equipo.** Si no está en la sucursal, se dice
  eso; ni la otra sucursal ni el producto del equipo.
- **Una regla, dos superficies.** El panel y la app (API v1) llaman al mismo servicio,
  tras la misma capacidad que el cambio de despacho.
- **Bloqueos en el orden del módulo:** pedido, estante, equipos. Una entrega y una venta
  del mismo modelo no se interbloquean.
- **Un equipo no tiene precio propio.** Se retira `StockUnit.price_override`: se cobra
  el precio del producto, en caja y en la web. Dos precios para un mismo modelo son dos
  productos. La columna sigue en la base una versión más (SERIAL-PRICE-DROP): `migrate`
  corre con la versión anterior aún en línea, y esa versión la lee.
- **Las ventas anteriores** quedan registradas con método `legacy`: sus documentos se
  reimprimen igual, y se distingue lo que una persona identificó de lo que el sistema
  asignó. No se copian los pedidos que aún esperan entrega: a esos se les aplica la regla.

### DEC-CUSTOMER-01 · La cuenta es la identidad; no se crean fichas vacías

- Registrarse (con contraseña o con Google) crea una cuenta, no un cliente del CRM.
  El registro de cliente nace cuando hay una relación: una compra o una orden.
- No se fusiona por nombre, correo ni teléfono. Una orden de mostrador se suma a una
  cuenta con el enlace y el documento (DEC-TRACKING-01).

### DEC-STOREFRONT-CAT-01 · Las familias de la portada las decide cada tienda

- `Category.is_active`, `show_on_home` y `home_order`. La API pública devuelve ya lo
  que la tienda debe ver y en su orden; el frontend no tiene una lista ni un orden
  propios. Retirar una categoría no oculta sus productos.

### DEC-GOOGLE-01 · «Continuar con Google» se verifica en el servidor y abre la sesión de siempre

- **Flujo de ID token, sin secreto de cliente.** El servidor comprueba firma (sólo
  RS256, con las claves publicadas), audiencia, emisor, caducidad, correo verificado y
  un `nonce` que él firmó y además dejó en una cookie HttpOnly del navegador.
- **La identidad es `sub`, no el correo.** Se guarda proveedor y sujeto; ningún token.
- **Un correo que ya tiene cuenta no se enlaza solo** (`User.email` no está verificado
  ni es único): 409 y contraseña de esa cuenta para vincular.
- **Sin contraseña inventada**: la cuenta nueva tiene una inutilizable.

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

### DEC-MEAS-01 · Quien visita decide, en el navegador y en el servidor

Fase ANALYTICS-MARKETING-INTEGRATIONS-01. Operación: [docs/analytics-marketing.md](docs/analytics-marketing.md).

- Dos categorías opcionales, apagadas hasta que alguien las enciende: **analítica**
  (Google Analytics) y **marketing** (Meta, TikTok). Rechazar cuesta un clic, como aceptar.
- La respuesta vive en el navegador (`localStorage`): es una preferencia del dispositivo
  y tiene que poder leerse antes de iniciar sesión. No lleva ningún identificador.
- **El servidor obedece la misma respuesta.** Viaja con el pedido al empezar a pagar y es
  lo único que permite planificar una conversión. `is True`, no «algo que parezca sí».
- No se guarda un registro de consentimientos en el servidor (MEAS-CONSENT-LOG): sólo el
  del pedido, y se borra con él.

### DEC-MEAS-02 · La tienda dice sus propios eventos; tres adaptadores traducen

- Un vocabulario (`frontend/app/lib/analytics/events.ts`) cuyos tipos no tienen un campo
  para una persona, un equipo o un token: lo que no cabe no se envía por descuido.
- Un servicio (`service.ts`) es lo único a lo que hablan las páginas. `gtag`, `fbq` y
  `ttq` existen sólo en `adapters/`; una prueba lee el código y falla si aparecen fuera.
- Cada adaptador decide a qué evento del proveedor corresponde cada uno, o a ninguno. No
  se inventan eventos para rellenar: Meta y TikTok no tienen «ver carrito».

### DEC-MEAS-03 · La compra nace donde el pedido queda pagado, y es una fila

- `ConversionDelivery`, única por pedido, proveedor y evento, escrita en la transacción de
  `_confirm` bajo un punto de guardado propio: tan duradera como el pago, e incapaz de
  deshacerlo. Se envía tras el commit; los reintentos llevan el mismo identificador.
- El navegador emite su copia sólo cuando el servidor dice que el pedido está pagado, con
  el identificador que le da el servidor. Donde el servidor la envía solo (GA4 con
  secreto), el navegador no la envía.
- **Por qué una tabla y no una tarea en memoria:** una conversión que se pierde si el
  proceso muere, o que se repite si se reintenta a ciegas, es justo lo que hay que evitar.
- `MeasurementContext` guarda lo mínimo y poco tiempo: nada sin consentimiento; IP y
  navegador sólo con el de marketing; se borra al terminar o a los siete días.

### DEC-MEAS-04 · En una dirección privada no hay ningún script

El script de un proveedor lee `location.href` por su cuenta. Sanear lo que se le pasa no
basta: la única protección de una dirección con token es que el script no esté.

- En seguimiento, restablecer contraseña, verificar correo, invitación, pedidos,
  reparaciones y el panel no se carga ni se envía nada, con el permiso dado y todo.
- **Y un script ya cargado no llega a verlas.** Callar nuestros eventos no bastaba (lo
  encontró la revisión de seguridad): el script lee la dirección solo. Desde que hay uno
  cargado, la API de historial está envuelta: ir a una dirección que ese script no puede
  ver se hace con una carga completa, tras decirle que pare. Cuesta una recarga.
- Meta y TikTok tampoco están en las páginas con formulario de datos personales: sus
  paneles pueden activar una lectura automática de campos que este código no puede apagar.
- La referencia del pago salió de la dirección de la página de éxito (`sessionStorage`).
- Desde «Mis reparaciones», el enlace de seguimiento es una navegación completa.
- A Google se le dice una ruta; nunca los parámetros.

### DEC-MEAS-05 · Un identificador público, ninguna dirección configurable

- Cada proveedor tiene un ID que es tan público como una etiqueta `<script>`, y es lo
  único que sale por `GET /api/measurement/config/`. Se construye con lo que el proveedor
  DECLARA público (`runtime_public`), no filtrando la fila guardada.
- De dónde se carga cada script y adónde van los eventos son constantes del código. Un
  campo de URL en la consola dejaría a una sesión MASTER robada apuntar a los clientes de
  la tienda a un script ajeno; un ID con la forma de su proveedor, no.
- Nada de esto usa `NEXT_PUBLIC_*`: cambiar un ID no exige recompilar.

### DEC-MEAS-06 · Una prueba nunca crea un evento real

Con código de evento de prueba, Meta y TikTok se verifican por su canal de pruebas. Sin
él, y en los modos «sólo píxel» y en GA4, el resultado es «Coherente, sin verificar»: el
servidor de validación de Google dice de sí mismo que no comprueba el ID ni el secreto.

### DEC-MEAS-07 · La CSP no se toca en esta fase

La tienda no tiene `script-src` (CSP-01). Añadirla exige un *nonce* en cada script que
emite Next y probarla con el SDK de la pasarela real, que no hay. Abrir una política
laxa «para que funcionen los píxeles» sería peor que no tenerla. Queda la lista exacta
de dominios en `docs/analytics-marketing.md` §7.

### DEC-MEAS-08 · Lo que el propietario decidió al cerrar la fase (2026-10-07)

- **CSP-01 es obligatoria antes de activar un proveedor de analítica o marketing en
  producción.** No se escribe una lista de dominios por adelantado: la política se cierra
  cuando puedan validarse los dominios reales, sobre todo los de la pasarela y los que
  cargan de verdad los SDK.
- **La IP y el navegador van a la API de conversiones de Meta y a Events API de TikTok**,
  sólo con eventos cubiertos por el consentimiento de marketing. Sin ese consentimiento
  no se envían. La minimización de `MeasurementContext` no cambia. Antes de producción,
  la política de privacidad y cookies de la tienda tiene que decirlo (MEAS-PRIVACY-NOTICE).
- **La coincidencia avanzada automática sigue desactivada** en Meta y TikTok.
- **Los dos ciclos de imports de la fase (INT-IMPORT-CYCLE) se corrigen aparte**, en la
  microfase INT-IMPORT-CYCLE-01, para no alterar la línea base ni la regresión de una
  fase ya cerrada.

### DEC-FRESH-DATA-01 · Producción nace de una base vacía; lo que sobra se retira con un paso, no con una migración

- Una base de producción es PostgreSQL vacío y `migrate`. Ni copia de desarrollo, ni
  volcado, ni semilla.
- La migración `0002` dejó tres productos de ejemplo con stock y sin Kardex. No se edita
  una migración aplicada, y **una migración nueva que los borrase correría también en
  cada base de desarrollo y de pruebas**. Se retiran con `bootstrap_pilot_store`, que
  alguien ejecuta una vez sobre la base que va a ser la real.
- **Todo o nada.** Retira los tres productos sólo si están los tres, exactamente como
  los dejó la migración, sin referencias y en una base sin actividad; si no, se niega y
  no cambia nada. Uno o dos de tres no es una base nueva ni una preparada: se niega. Las
  referencias se preguntan al modelo, no a una lista: una relación nueva cuenta el día
  que existe.
- **Prepara un arranque, no mantiene una configuración.** Con la base ya en uso no
  escribe nada, ni siquiera para reponer una categoría que la tienda quitó. Y no se
  ejecuta con `DEBUG` activo: borra filas, y una base de desarrollo no es su sitio.
- **Lo de la tienda piloto no es un valor por defecto de la plataforma.** Sus categorías
  y su campaña están ligadas a su slug; `company_provisioning`, que es de donde nace
  una empresa nueva, no copia catálogo.
- **El MASTER es `is_superuser` y no recibe una membresía por serlo.** Se crea con
  `createsuperuser`, sin contraseña en el repositorio. Puede configurar cualquier
  empresa nombrándola; para vender o constar como personal hace falta una membresía
  explícita, que se da por invitación.

### DEC-ONBOARD-01 · La invitación prueba el buzón; la contraseña es de la persona

- **Una invitación válida para un correo ya responde lo que la verificación pregunta**:
  ¿lee esta persona ese buzón? Registrarse con ella crea la cuenta activa, sin segundo
  correo. Responde por su correo y por ningún otro, y sólo mientras sirve.
- **La invitación no fija la contraseña de una cuenta que ya existe.** Dura siete días;
  el enlace de recuperación, una hora y un solo uso. Quien no conoce su contraseña usa
  la recuperación de siempre, que es de la misma persona y del mismo buzón.
- **La recuperación termina una cuenta que nunca se verificó.** Usarla prueba lo mismo
  que el enlace de verificación, y la contraseña nueva sustituye a la que escribió quien
  creó la cuenta. «Nunca verificada» se pregunta con precisión (`accounts.is_unverified`:
  inactiva, con enlaces de verificación y ninguno usado); una cuenta inactiva por otro
  motivo no se toca.
- **El enlace de recuperación vuelve a una invitación y a nada más.** Lo escribe el
  servidor en un correo: sólo acepta `/invitacion?token=…` con un token de la forma de
  los nuestros.
- **Se decide antes de escribir, y se falla cerrado.** Si la cuenta nace activa o no se
  decide antes de crearla y se escribe una sola vez; lo que no se puede comprobar es un
  «no». Guardarla activa y apagarla después dejaba una cuenta utilizable cada vez que
  algo fallaba en medio.
- **Consecuencia aceptada: para un correo sin cuenta, el enlace de invitación basta.**
  Quien lo tenga puede crear la cuenta y aceptar. Lo acotan la caducidad (siete días),
  el uso único, la revocación y que reenviar anula el anterior. Exigir además un segundo
  correo es lo que dejaba a la gente sin poder terminar.
- **El administrador nunca elige ni ve una contraseña**, y no se envía ninguna por
  correo. No hay contraseñas temporales.
- **Descartado:** activar la cuenta sin verificar con sólo abrir la invitación (dejaría
  viva la contraseña de quien la registró); un segundo sistema de credenciales para
  invitados; una identidad por empresa.

### DEC-REGISTER-01 · Que un correo tenga una sola cuenta lo garantiza la base

- **Preguntar y después escribir no basta.** Entre la pregunta y la escritura cabe otra
  petición. La única regla que dos peticiones simultáneas no pueden saltarse es una
  restricción de la base.
- **Sin distinguir mayúsculas, y con la misma expresión que las búsquedas.**
  `email__iexact` es `UPPER(email) = UPPER(%s)` en PostgreSQL, así que el índice es
  sobre `UPPER(email)`. Con `LOWER` la regla y las búsquedas no coinciden en una base
  UTF-8: «ı» sin punto es «I» para `UPPER` y no cambia para `LOWER`. Deja fuera las
  cuentas sin correo, que pueden ser varias.
- **Un correo de cuenta es ASCII.** Una dirección con «ı» o «ſ» pasa por otra y la base
  las trata como la misma; quien registrara la parecida dejaba a la verdadera sin poder
  registrarse. Los correos con caracteres propios de otro alfabeto son rarísimos y casi
  ningún proveedor los entrega.
- **El modelo de usuario es el de Django**, así que la regla va en una migración con SQL
  y no en el modelo. Funciona igual en PostgreSQL y en SQLite.
- **Lo que ya exista duplicado no se toca.** La migración se detiene y dice cuántos; no
  fusiona, no borra y no nombra a nadie (su texto acaba en un registro de despliegue).
  Cuál de dos cuentas se conserva lo decide una persona: una puede tener los pedidos y
  la otra la contraseña que alguien recuerda.
- **Perder la carrera no es un error.** Se responde lo que se habría respondido un
  instante después, con las mismas palabras del formulario.
- **Las defensas contra duplicados se conservan** (`accounts.with_email`, las respuestas
  silenciosas): una instalación puede traerlos de su pasado.

### DEC-PRODUCT-DEST-01 · «Sólo stock interno» es otro dato que «activo»

- **Por qué un campo y no reutilizar `is_active`.** Inactivo saca el producto de todo:
  la caja no lo vende y el inventario lo aparta. Un repuesto o un equipo que sólo se
  despacha en tienda está en uso; lo único que no hace es venderse por la web.
- **Un booleano, `is_published_online`, por defecto verdadero.** Son dos destinos, y
  todo lo que existía sigue publicado sin tocar una fila.
- **Una sola definición de «se vende por la web».** Para una consulta,
  `tenancy.company_storefront_products`; para un producto ya en la mano (una línea de
  carrito, una campaña), `Product.is_sold_online`. Toda superficie pública pasa por una
  de las dos: una tercera copia es como un producto oculto acaba a la venta.
- **Dentro de la empresa no cambia nada.** Caja, inventario y taller no filtran por este
  dato; quién ve qué lo siguen decidiendo las capacidades de siempre.
- **En la carga masiva, vacío no decide.** Lo nuevo se publica; lo existente se queda
  como estaba. Sólo se aceptan los dos valores de la plantilla: adivinar aquí publica en
  la web algo que alguien quiso dejar fuera.
- **La columna «Destino» se busca por su cabecera, no se confía a la asignación.** La
  asignación puede llegar sin ella por motivos que nadie eligió —un perfil recordado de
  antes, un formato que nunca la tuvo— y entonces una celda que dice «Solo stock
  interno» no se lee y el producto se publica. Fallar ahí es fallar abierto.
- **Un producto interno responde como uno que no existe.** No «ya no está disponible»,
  y nunca con su nombre.
- **Las categorías son las de la empresa, leídas al descargar.** La lista de la
  plantilla ayuda y no prohíbe: un nombre nuevo es como se usa «crear las que falten».
- **Descartado:** un tercer estado en un campo de texto; ocultar por categoría; aplicar
  el destino a las categorías.
### DEC-MAIL-01 · Una plantilla de correo es de una empresa; el correo neutro queda detrás

- **La plantilla no es de la plataforma.** Su pie nombra una tienda, con su dirección,
  su teléfono y sus redes. `store/mail/plantilla/marca.json` dice de qué empresa es, por
  su `slug`. La llevan los correos de esa empresa; los de plataforma (cuenta,
  contraseña), sólo cuando la tienda de la instalación es esa empresa y la persona no lo
  pidió desde la dirección de otra. Otra empresa nunca firma con los datos de la
  primera: sigue con el correo de texto que tenía.
- **El archivo de la plantilla no se edita.** Sus `[corchetes]` se rellenan al cargarla:
  la web desde `FRONTEND_URL`, la razón social y el RUC desde los ajustes de la empresa,
  el logotipo y YouTube desde `marca.json`. Si falta uno, la plantilla no se usa: un pie
  con «[RUC]» impreso es peor que el correo neutro.
- **Los datos tienen contrato y se comprueban antes de rellenar.** Claves desconocidas,
  números sin formatear y enlaces que no sean `https://` o la propia web se rechazan; lo
  vacío se omite, y con ello su bloque. Todo dato se escapa (`{{ }}`).
- **Un correo no se pierde por cómo se ve.** Cualquier fallo al componer la plantilla
  deja salir el correo neutro y un error en el registro. El cuerpo no se registra nunca:
  varios correos llevan un enlace que es el acceso.
- **Componer y enviar son dos pasos, y el respaldo es sólo del primero.** Un envío que
  falla no se repite sin plantilla: un servidor que aceptó el mensaje y no llegó a
  confirmarlo ya lo entregó, y el segundo correo llevaría el mismo enlace. Además
  duplicaba la espera de la petición con el servidor caído.
- **Los valores fijos entran como texto, no como plantilla.** Se insertan en el fuente
  antes de rellenarlo; una razón social con `{{` dentro sería Mustache. Las llaves se
  escriben como entidades.
- **El pie de texto dice lo que imprime la plantilla**, no lo que hay en los ajustes: el
  pie HTML está escrito en el archivo, y las dos mitades de un correo no pueden firmar
  distinto. `marca.json` lo repite y una prueba exige que coincida.
- **El texto plano sale de los mismos datos**, no de quitar etiquetas al HTML: no puede
  decir otra cosa ni llevar otro enlace.
- **Motor:** Mustache con `chevron`, porque la plantilla entregada es Mustache y la del
  propietario no se reescribe a la sintaxis de Django.
- **Descartado:** una plantilla por tipo de correo; meter la marca en variables de
  entorno (son datos de una empresa, ya están en sus ajustes); usar la plantilla para
  todas las empresas con los datos de cada una (el diseño, los colores y las redes son
  de una marca: otra empresa necesitará la suya, con su propio `marca.json`).

### DEC-MAIL-02 · El aviso de una reparación lleva su enlace de seguimiento

Decisión del propietario (2026-10-08). Antes ese enlace sólo se entregaba a mano, y
quedaba escrito quién lo pidió.

- **Por qué:** el aviso «tienes una cotización pendiente» no decía cómo verla. Las
  páginas del cliente piden una cuenta, y quien deja un equipo en el mostrador casi
  nunca la tiene. La página de seguimiento es donde se lee y se responde la cotización.
- **El enlace es una forma de entrar, y se trata como tal.** Va sólo al correo del
  cliente de esa reparación (misma empresa, mismo cliente); nunca a la copia del
  personal; no se escribe en registros ni en el motivo de un envío fallido.
- **Un enlace revocado sigue revocado.** El correo usa el enlace vivo y no crea otro:
  revocar es una decisión de alguien, y un aviso no la deshace.
- **Lo demás no cambia:** el aviso sigue sin llevar precios ni diagnóstico.
- **Riesgo aceptado:** quien lea ese buzón puede abrir el seguimiento y responder la
  cotización. Es el mismo alcance que ya tenía quien recibiera el enlace de mano del
  personal.

### DEC-MAIL-03 · El código de verificación es una comodidad con sus propios límites

El propietario pidió un código de 6 dígitos en el correo de verificación, «que no
moleste», con estas cifras: 15 minutos, 5 intentos, uno nuevo anula el anterior, 60
segundos entre reenvíos y un tope diario (se fijó en 5).

- **El enlace sigue siendo la prueba fuerte.** El código prueba lo mismo —que quien lo
  escribe lee ese buzón— con un secreto de un millón de posibilidades. Verificar un
  correo ajeno no es inocuo aquí: un buzón verificado es lo que deja aceptar una
  invitación de personal dirigida a él.
- **Los límites se cuentan en la cuenta, no en la red.** Intentos por código y códigos
  por día viven en la fila del token; cambiar de red no los reinicia. El límite por
  dirección (10 por minuto) es una segunda capa.
- **Sólo se guarda un HMAC**, con clave derivada de `SECRET_KEY` y ligado a la cuenta.
  Un hash simple de seis dígitos se revierte probando el millón.
- **Una sola respuesta para toda negativa.** Ni «la cuenta no existe», ni «código
  vencido», ni «demasiados intentos». Lo que no es un código (cinco dígitos, letras) no
  gasta intento.
- **Una fila, un correo.** El código vive en la misma fila que su enlace
  (`AccountToken`): usar uno gasta el otro, y no hay un segundo sistema de tokens.
- **Reenviar no dice si envió.** La espera de 60 segundos y el tope diario se aplican en
  silencio; la pantalla muestra la cuenta atrás por su lado.
- **Consecuencia aceptada:** alguien puede gastar los cinco códigos del día de una
  cuenta ajena pidiendo reenvíos. El enlace del primer correo sigue sirviendo 24 horas,
  y la recuperación de contraseña también termina la cuenta.
- **Un mismo orden de bloqueos.** Emitir y comprobar tocan la cuenta y su código; los
  dos bloquean primero la cuenta. Al revés en uno de ellos, un código correcto que
  llegaba a la vez que un «enviar otro» se interbloqueaba y uno de los dos respondía 500.
- **Un código es definitivo cuando su correo salió.** `issue` lo crea, `settle` mata los
  anteriores una vez entregado, `withdraw` lo retira si el envío falló. Un servidor de
  correo caído no puede dejar a nadie un día sin verificación.
- **«Inactiva» no es la pregunta.** El código sólo termina una cuenta registrada y nunca
  verificada (`accounts.is_unverified`), y reenviar sólo emite para esas.
- **Las columnas nuevas llevan valor por defecto en la base** (`db_default`). Al
  desplegar se migra con la versión anterior aún sirviendo; sin él, sus altas de tokens
  fallaban, y pedir recuperar contraseña respondía 500 sólo para correos existentes.
- **Lo que tarda dice poco, no nada.** Toda comprobación bloquea una fila de cuenta y
  una de código y compara un HMAC, exista la cuenta o no. Queda una diferencia de
  fracciones de milisegundo cuando hay un código vivo. El registro ya dice si un correo
  está registrado, así que no se añade un dato que no estuviera.

### DEC-BRAND-ICON-01 · El icono de la pestaña es el isotipo, en dos contrastes

El isotipo es una silueta de un color: el oscuro desaparece en una pestaña oscura. Hay
dos juegos y el navegador elige con `prefers-color-scheme`; no hay un tercero sin
condición, que podría ganar a los otros dos. Sin fondo, sin letra y sin realce: a 16 px
el realce rellenaba la cara y borraba las gafas. Es marca de plataforma, no de empresa.

### DEC-INT-01 · Los secretos de las integraciones van cifrados, con una clave que no está en la base

Fase INTEGRATIONS-CONSOLE-01. Operación: [docs/integraciones-y-secretos.md](docs/integraciones-y-secretos.md).

- **Qué:** los campos secretos de cada integración se guardan juntos, cifrados con Fernet
  (`cryptography`: AES-128-CBC + HMAC-SHA-256), en `IntegrationConfig.sealed_secrets`. La
  clave raíz es `APP_CONFIG_ENCRYPTION_KEY`, del entorno del servidor.
- **Por qué no una columna por secreto:** una columna legible acaba en un serializer, un
  volcado o una bitácora. Aquí no hay ninguna que se pueda leer sin la clave, y la clave
  no viaja con la base.
- **Por qué Fernet y no algo propio:** es cifrado autenticado estándar de una biblioteca
  que el proyecto ya usa para firmar los comprobantes. No se diseña criptografía.
- **Atado a su sitio:** el texto cifrado lleva dentro integración, empresa y si es borrador
  o activo. Una fila copiada a otra empresa no se abre.
- **Escritura sin lectura:** ninguna API devuelve un secreto; devuelve `configured`, cuándo
  y quién. No se guarda en claro nada de él: los cuatro últimos caracteres que se
  guardaban al principio eran un tercio de una contraseña de doce (revisión).
- **Rotación:** `APP_CONFIG_ENCRYPTION_KEYS_PREVIOUS` + `reseal_integration_secrets`.
- **Lo que se paga:** perder la clave raíz es perder las credenciales guardadas (se vuelven
  a escribir). Es el precio de que una copia de la base no las contenga.

### DEC-INT-02 · El dominio no nombra a ningún proveedor

- **Registro de proveedores** (`store.integrations.registry`): cada uno declara id,
  categoría, alcance, campos públicos y secretos, validación, modos, prueba, confirmación
  de activación y respaldo de entorno. La API y la pantalla se construyen con eso.
- **Adaptadores:** el checkout llega a la pasarela por `store.integrations.payments`
  (un adaptador por producto); el correo, por un backend de Django
  (`RuntimeEmailBackend`) que elige en cada envío; WhatsApp, por `store.messaging`.
- **Por qué:** añadir un proveedor es una clase y su declaración, no un `if` nuevo en el
  checkout. Hay una prueba que falla si el checkout vuelve a nombrar una pasarela.

### DEC-INT-03 · Qué es de la instalación y qué es de cada empresa

| Integración | Alcance | Por qué |
|---|---|---|
| Correo SMTP | instalación | un remitente de transporte para todas; la identidad visible de cada mensaje ya es de cada empresa |
| Izipay | instalación | el checkout, las rutas de notificación y el frontend asumen un comercio; por empresa es otra fase (PAY-TENANT-SCOPE) |
| WhatsApp | **empresa** | cada empresa tiene su número; ya era así con las referencias a variables |
| Google | instalación | un ID de cliente por dominio |
| SUNAT | instalación | el código sólo opera BETA con credenciales comunes; `fiscal_config` ya recibe la empresa (FISCAL-TENANT-SCOPE) |

La consola declara el alcance que el código tiene, no el que sería deseable. No se
inventa un «Producción» de SUNAT ni un Izipay por empresa.

### DEC-INT-04 · Borrador, prueba, activación; y apagado es apagado

- Dos filas por integración: `active` (lo que corre) y `draft`. Guardar nunca toca lo que
  corre.
- Sólo se activa un borrador que pasó la prueba, y esa versión exacta: la prueba cambia la
  versión y la activación la exige (concurrencia optimista; 409 si no coincide). El
  resultado de una prueba sólo se escribe si la fila sigue siendo la que se leyó: una
  prueba tarda segundos y lo guardado entretanto no es lo que se probó.
- **«Coherente, sin verificar» no es «Correcto».** Las claves de producción de la pasarela
  no se pueden probar sin tocar dinero real: pasan, y se dice que nadie las verificó.
- **Parar lo que está en uso con algo en curso se escribe** (`INTERRUMPIR`): el proveedor
  declara qué quedaría cortado (hoy, cobros abiertos en la última hora).
- Orden de resolución en cada uso: consola activa → si la consola la tiene apagada, nada →
  si la consola no tiene nada, entorno.
- **Por qué apagado no recurre al entorno:** un interruptor que deja pasar por otra puerta
  no es un interruptor. Lo mismo vale para lo guardado que no se puede leer.
- Lo que cuesta dinero o no tiene vuelta atrás pide escribir una palabra (`PRODUCCION`,
  `EMITIR`, `REVOCAR`). No se pide la contraseña otra vez: no hay una reautenticación
  reutilizable y no se inventa una débil.

### DEC-INT-05 · Sólo el MASTER, y no por capacidades

`IsPlatformAdmin` en cada ruta y `platformAdminOnly` en el registro de módulos. No se creó
una capacidad `integrations.manage`: una capacidad se puede conceder desde un rol de
empresa, y estas credenciales no son de ninguna empresa (salvo WhatsApp, que el MASTER
administra por ellas).

### DEC-INT-06 · Sin caché: cada uso pregunta

La configuración activa se lee —y se descifra— en cada envío, cada cobro y cada inicio de
sesión. Así un cambio vale al instante y en todos los procesos, sin invalidar nada. Es una
consulta por clave única; si el tráfico lo pidiera, se mide antes de añadir una caché
(INTEGRATION-READ-COST). Dentro de una misma petición WhatsApp lo lee una vez.

### DEC-INT-07 · El correo ya no impide arrancar

Antes el backend no arrancaba en producción sin `EMAIL_BACKEND`. Con la consola el correo
se configura después de arrancar, así que arrancar sin él es legítimo: los envíos fallan
con un error registrado, `ops_status` da la alarma y nunca se cae al backend de consola
(MAIL-CONSOLE-DEFAULT sigue en pie). `EMAIL_BACKEND=smtp` sin `EMAIL_HOST` sigue sin
arrancar: es una contradicción del archivo, no una ausencia.

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

- **Actualización (PAYMENTS-EQUIPMENT-DOCUMENTS).** La misma disciplina vale para el
  segundo producto: `store/payments/fake_micuentaweb.py` exige el contrato publicado de
  `Charge/CreatePayment` y firma las notificaciones por su cuenta, incluida la copia que
  recibe el navegador. `MiCuentaWebSandboxSmokeTest` se omite sin claves
  (**BLOCKED/CREDENTIALS**). Una firma con caracteres no ASCII se compara como bytes: ya
  no es un 500.

### DEC-PAY-02 · Izipay son dos productos; una instalación usa uno

- El código hablaba con el «SDK web / Checkout» (developers.izipay.pe). La página
  oficial que indicó el propietario documenta «Mi Cuenta Web» (API REST V4): otras
  credenciales, otro guion, otra firma. Las dos son integraciones oficiales válidas.
- No se reemplazó una por otra: no se sabe de cuál tiene credenciales el propietario, y
  borrar la primera habría tirado un contrato ya probado. Se añadió la segunda como
  adaptador aparte (`store/payments/micuentaweb.py`).
- **Nunca las dos a la vez.** `PAYMENT_PROVIDER` nombra una. El checkout abre el pago
  sólo con esa, el navegador carga sólo su guion y la notificación de la otra responde
  404 antes de leer nada. Un valor desconocido impide cobrar; no cae en «la de siempre».
- **Una sola definición de «pagado».** Cada producto tiene su vista, que hace una cosa
  propia: verificar la firma y reducir el mensaje a un resultado. Lo que sigue
  (`_SignedNotificationMixin`) es común: importe, moneda, comercio y pedido contra la
  base, bloqueo de fila, y una repetición que no paga dos veces.
- **El navegador no es testigo.** En «Mi Cuenta Web» el navegador recibe una copia de la
  respuesta firmada con otra clave. La notificación sólo cree lo firmado con la
  contraseña (`kr-hash-key=password`); la copia del navegador, reenviada, se rechaza. El
  checkout ni siquiera la lee.
- **TEST o PRODUCCIÓN lo dicen las claves.** No hay variable de entorno para eso en este
  producto. Un par mezclado impide cobrar y un pago de TEST no paga un pedido con claves
  de producción.
- PAY-RECONCILE: resuelto para este producto en DEC-PAY-03.

### DEC-PAY-03 · Si la notificación no llega, se pregunta a la pasarela, y se le cree menos

Auditoría de «Mi Cuenta Web» contra su documentación oficial (2026-10-11). La tabla
completa, con la página de cada punto, está en `docs/pagos-equipos-documentos.md` §1.1.

- **La notificación sigue siendo la que paga.** Pero la pasarela la da por fallida si
  la tienda no contesta con `200`–`206` o `301`/`302`/`303`/`307`/`308`, la repite como
  mucho cuatro veces y sólo si el comercio lo activó. Después, el dinero está cobrado y
  el pedido espera. `Order/Get` existe para eso.
- **Un solo juez.** `reconcile_payments` no decide si un pedido se paga: reduce la
  respuesta de `Order/Get` al mismo resultado que da una notificación y lo entrega a
  `_SignedNotificationMixin`. Mismas comprobaciones, mismo bloqueo de fila, un solo pago.
- **Se lee con más desconfianza que una notificación**, porque trae menos: no tiene
  firma ni `orderStatus`, sólo transacciones. Pagado es **un** cobro (`DEBIT`) con
  `status` `PAID` y `detailedStatus` `AUTHORISED` o `CAPTURED`, por el importe completo,
  de esta tienda, esta referencia y este entorno. `PAID` lo llevan también una
  verificación de tarjeta y una preautorización, que no se capturan: no valen.
- **Lo que no se puede decidir no se decide.** Reembolso, dos cobros, pago parcial, otro
  entorno, error de la pasarela: el intento sigue esperando y se registra el motivo. No
  se interpreta ningún código de error como «no hay nada»: la documentación no dice cuál
  lo es.
- **Un rechazo temprano no es una respuesta.** El formulario vive 15 minutos y deja
  reintentar con la misma referencia; un rechazo sólo cierra el intento pasados 30.
- **No se llama firma a lo que no lo es.** El intento pagado así queda con
  `signature_verified = False` y deja `payment_reconciled` en la auditoría, con la
  empresa del pedido.
- **Sin estado nuevo ni migración.** La ventana (de 3 minutos a 2 horas), el tope por
  pasada (20, los más recientes primero) y la frecuencia (cada 5 minutos) acotan las
  llamadas: como mucho 24 por un intento abandonado. La documentación recomienda una
  consulta por minuto y esperar la respuesta; se entiende por referencia, y las llamadas
  van de una en una.
- **La firma del IPN, como el ejemplo oficial.** `ipn.php` calcula el HMAC tras cambiar
  `\/` por `/`. Se acepta la cadena recibida o esa lectura, y se interpreta la que cubrió
  la firma: nunca se lee nada sin firmar.
- **Lo que se dejó como estaba, a propósito.** Los códigos con que responde la
  notificación (un mensaje que no se cree recibe `4xx`: la pasarela avisa al comercio,
  que es lo que conviene). El guion se inserta sin `embedded-form-glue`: la regla es no
  añadir dependencias. La CSP no restringe guiones (FE-AUTH-04), así que no hay orígenes
  que abrir.
- **No resuelve:** el otro producto de Izipay (no tiene esta consulta). Lo que entonces
  quedaba abierto —un cambio de producto con cobros abiertos y una tienda publicada con
  claves de TEST— lo cierran DEC-PAY-04 y DEC-PAY-05.

### DEC-PAY-04 · Un cambio de pasarela que dejaría un cobro sin confirmar no se hace

Orden del propietario: «bloquear cambios inseguros o conservar credenciales anteriores
por intento hasta conciliar». Se eligió **bloquear**: guardar credenciales viejas por
intento es más secreto guardado, durante más tiempo, para un caso que se resuelve
esperando media hora.

- **Qué es un cambio inseguro.** Aquél tras el cual un intento abierto ya no se podría
  confirmar ni consultar: activar el otro producto, apagar o revocar el que cobra,
  cambiar de tienda (usuario / código de comercio) o de entorno (TEST ↔ PRODUCCIÓN).
  Renovar contraseña, clave pública o clave HMAC de la misma tienda y entorno **no** lo
  es: la pasarela firma con la clave nueva y la consulta sigue funcionando.
- **Decide el producto que cobra**, no el que se toca: activar «SDK web» con «Mi Cuenta
  Web» cobrando es un cambio a «Mi Cuenta Web». «El que cobra» incluye al configurado
  por variables del servidor, que antes no avisaba de nada.
- **«Mi Cuenta Web»: primero se pregunta, con las claves que aún están.** Es
  `reconcile_micuentaweb`, la misma consulta de DEC-PAY-03, sobre las dos últimas horas.
  Después: un intento de menos de 30 minutos (su formulario vive) o sin resolver en la
  pasarela **bloquea** (409, nada cambia, ninguna palabra lo salta). Un intento antiguo
  del que la pasarela no tiene nada no detiene nada.
- **`INTERRUMPIR` queda para lo que no se puede comprobar:** «SDK web / Checkout», que
  no tiene consulta (su comportamiento no cambia), y una pasarela que no responde. En
  los dos casos queda `payments_interrupted` en la auditoría, un registro por empresa,
  con los identificadores de los intentos y de sus pedidos. Sin datos personales.
- **La decisión y el cambio son un solo paso.** La consulta a la pasarela se hace antes
  de tomar ningún bloqueo (habla con un servidor ajeno). Después, el cambio toma un
  bloqueo exclusivo (`pg_advisory_xact_lock`) y decide con lo que hay en la base; abrir
  un intento toma el mismo bloqueo compartido y comprueba que las credenciales con que
  empezó el checkout siguen siendo las que cobran. Así un cambio ve todo intento
  anterior, y un intento posterior se abre contra lo que el cambio dejó, o no se abre
  (409 al comprador, que reintenta). Fuera de PostgreSQL el bloqueo no hace nada: es el
  SQLite de desarrollo.
- **Se hace cumplir en el servicio** (`integrations/service.py`: activar, desactivar,
  revocar), que es el único código que escribe la configuración de pagos. La pantalla
  sólo lo muestra.
- **La pasarela es de la instalación** (PAY-TENANT-SCOPE): no hay configuración por
  empresa que aislar. Lo que sí es por empresa es el registro de sus intentos
  interrumpidos.
- **No cubre:** editar `PAYMENT_PROVIDER` o las claves en las variables del servidor y
  reiniciar. Tampoco un cambio de `Dirección de la API`, que no se trata como cambio de
  tienda.

### DEC-PAY-05 · Las claves de TEST de la consola no son para compradores

Orden del propietario: «no activar claves TEST para compradores públicos: usar entorno
aislado o acceso restringido».

- **Acceso restringido, sin interruptor.** Mientras lo que cobra es una configuración
  de TEST puesta en la consola, sólo un MASTER de la plataforma abre un pago. Se pensó
  en un ajuste «quién puede pagar en TEST»; no se añadió: sería la casilla con la que
  una tienda publicada vuelve a aceptar tarjetas de prueba de cualquiera.
- **Por qué MASTER y no una capacidad de empresa.** La pasarela es de la instalación y
  sólo `is_platform_admin` puede configurarla (la consola no se abre con ningún rol de
  empresa). Quien puede poner las claves de TEST es quien tiene que probarlas; un
  cajero o un administrador de empresa no las ve y no tiene por qué pagar con ellas.
- **Antes de todo.** La negativa (503, «El pago en línea todavía no está disponible en
  esta tienda», código `payment_test_only`) sale de
  `require_payment_provider_configured`, la única puerta de los tres caminos que abren
  un pago (web, app y reintento de la app): antes de crear el pedido y antes de hablar
  con la pasarela. Quien llame sin decir quién compra es un comprador.
- **El entorno aislado es el de las variables del servidor.** Las pasarelas simuladas
  de las pruebas, del ensayo y de Playwright se configuran por entorno y no cambian.
  Las pruebas que pagaban como comprador anónimo con claves de TEST de la consola pagan
  ahora como MASTER, o con claves de producción simuladas: no se relajó ninguna.
- **Un pedido pagado en TEST se reconoce.** `PaymentTransaction.environment`
  (migración `0117`, con valor por defecto en la base para que la versión anterior siga
  insertando durante un despliegue) y un registro `payment_confirmed_in_test` con la
  empresa del pedido. Además, un intento abierto en un entorno no lo confirma el otro,
  aunque la firma valga («environment mismatch»): cubre al producto cuyas
  notificaciones no dicen de qué entorno son.
- **Queda:** las claves de TEST puestas por variables del servidor en una tienda
  publicada (PAY-TEST-ENV), y que el panel muestre «pago de prueba» junto al pedido
  (PAY-TEST-BADGE).

### DEC-PAY-06 · Lo que cambió tras la auditoría independiente de la conciliación

Una segunda lectura del código de DEC-PAY-03, hecha por otra sesión, encontró doce
defectos y catorce reglas que ninguna prueba fijaba. Lo que se decidió con cada uno:

- **Un rechazo no cierra un intento mientras su formulario viva** (30 minutos). El
  formulario deja reintentar con otra tarjeta bajo la misma referencia; si ese segundo
  pago llegaba y su notificación se perdía, un intento ya cerrado como rechazado no se
  consultaba nunca. Vale para la notificación y para la consulta. «SDK web / Checkout»
  no cambia.
- **Un intento se resuelve una vez.** Con el pedido bloqueado se relee el intento: si ya
  tiene su pago, un mensaje repetido no hace nada, aunque el pedido se haya cancelado o
  reembolsado después.
- **Un segundo cobro no es un pago.** Si el pedido ya está pagado y la pasarela cobró
  otro intento, queda en el estado «Cobro duplicado», con error en el registro,
  `payment_duplicate` en la auditoría y ATENCIÓN en la comprobación de estado. Antes se
  archivaba como un pago más. Vale para los dos productos: es el mismo juez.
- **No se abre un segundo formulario sin preguntar por el primero.** Sólo la app puede
  pedirlo (repite su petición con la misma clave de idempotencia); la web crea un pedido
  nuevo cada vez. Pagado: no hay segundo formulario. No se puede preguntar: tampoco.
- **Turnos.** Se pregunta primero por quien lleva más sin que nadie pregunte por él
  (`gateway_checked_at`), y lo que ya no puede cobrarse y la pasarela no tiene se cierra
  como «Sin intento de pago». Con «los más recientes primero» y sin cerrar nada, veinte
  compras abandonadas dejaban sin turno a un pago perdido.
- **Lo que no se decide, se dice.** Una respuesta con dos cobros, un reembolso o un
  importe parcial sigue sin decidirse, pero la primera vez deja `payment_undecided` en
  la auditoría y da ATENCIÓN.
- **El registro nace con el pago.** `payment_reconciled` se escribe en la misma
  transacción que marca el pedido pagado, por quien lo marcó. Tres pasadas a la vez: un
  pago, un registro, una cuenta.
- **Si la pasarela rechaza a quien llama, la pasada termina.** `401`, `403`, `429` o un
  código `INT_…` («problema de integración del vendedor», `rest/V4.0/api/errors_int.html`)
  no dependen de la referencia. Un error sobre una referencia (`PSP_…`, otro `4xx`) no
  detiene nada y no se toma por «no hay nada»: `PSP_010` cubre también una transacción
  de otro entorno o de otra tienda (`rest/V4.0/api/errors_psp.html`).
- **La contraseña va a un solo sitio.** No se sigue ninguna redirección, y «Dirección
  de la API» sólo admite `https://api.micuentaweb.pe`, el servidor de todos los ejemplos
  de la documentación (`rest/V4.0/api/kb/authentication.html`). Una respuesta cortada,
  ilegible o de más de 2 MB es «no contestó». Las credenciales no se imprimen.
- **Un intento guarda su tienda y su entorno** (migración `0117`). Con otra tienda u
  otro entorno configurados no se confirma, llegue por notificación o por consulta. Los
  intentos anteriores a la migración no guardan nada y se juzgan como antes.
- **Sin firma no hay «firma verificada».** Un resultado que no dice que fue firmado no
  se toma por firmado.
- **Sin cambiar:** la ventana de dos horas (un medio de pago que tarda más sale de
  ella: lo confirma su notificación o `--reference`) y la comparación en tiempo
  constante de la firma, que no tiene prueba posible.

### DEC-PAY-07 · Lo que cambió tras la segunda auditoría: el entorno manda, y siempre se puede pausar

Una segunda lectura independiente de DEC-PAY-04/05/06 encontró un fallo grave y cinco
serios. Cambian tres decisiones anteriores:

- **DEC-PAY-05, corregida: lo que cuenta es el ENTORNO de lo que cobra, no de dónde
  vienen las claves.** La puerta sólo miraba las claves de TEST de la consola; las de las
  variables del servidor cobraban a cualquiera, y revocar la consola volvía a ellas. Ahora
  toda configuración de TEST es sólo para MASTER. La única excepción es un interruptor
  explícito del servidor, `PAYMENTS_PUBLIC_TEST`, apagado por defecto, para instalaciones
  donde la pasarela simulada es lo que se prueba. Las pruebas lo encienden en UN sitio
  (`store.suite_runner`, declarado en `TEST_RUNNER`), no fixture a fixture. El ensayo
  Docker y Playwright no abren pagos públicos y no lo necesitan.
- **Revocar no cede la tienda a las variables del servidor.** Si al quitar la última
  fila activa de la consola quedara cobrando lo que haya en el entorno, se rechaza (409
  `environment_fallback`) con su motivo. Se eligió rechazar y no «tratarlo como activar»:
  activar pide probar y, en producción, escribir una palabra en un campo que «revocar»
  ya usa para la suya; y nadie que pulsa «revocar» está pidiendo poner otra pasarela.
- **DEC-PAY-04, completada: pausar.** Un comprador que abría un checkout cada 29 minutos
  mantenía bloqueado para siempre apagar, revocar o cambiar. Ahora existe un estado real,
  `IntegrationConfig.paused`: la configuración sigue siendo la que corre (notificación y
  consulta siguen funcionando con sus credenciales) pero no abre nada nuevo. Pausar no
  pide palabra, no pasa por el guardia y no consulta a nadie: toma el bloqueo y escribe.
  Es lo que un MASTER puede hacer siempre; lo demás espera a que se agoten los abiertos.
  Reanudar pide lo que pidió activar. Volver a activar algo desactivado lo deja sin
  pausa.
- **«No se pudo preguntar» va antes que «no se comprobó».** Pasarela caída, que rechaza
  a quien llama, sin credenciales con que preguntar, o que contesta con un error a una
  referencia cuyo formulario ya no cobra: palabra y auditoría, sean uno o veinte los
  intentos. Antes sólo se ofrecía la palabra con exactamente un intento. Sigue bloqueando
  sin palabra lo que SÍ se sabe: un formulario vivo y lo que la pasarela tiene sin
  resolver. Mientras nadie sepa qué contesta `Order/Get` de una referencia sin usar
  (`NO_TRANSACTION_CODES` sigue vacío), un error no puede ser un bloqueo de dos horas.
- **No se pregunta a una tienda por los intentos de otra.** Los que se abrieron con otra
  tienda u otro entorno quedan abiertos, sin consultar y sin cerrar, contados aparte y
  anunciados por la comprobación de estado. Tampoco bloquean el cambio siguiente.
- **DEC-PAY-06, corregida: lo que nadie intentó pagar se cierra al final de su ventana**,
  no al primer «no hay nada» pasada media hora. Y una cuarta parte de cada pasada es
  para quien lleva más tiempo sin respuesta.
- **La app.** Límite por usuario en su checkout (el que había sólo contaba anónimos), tope
  de tres formularios vivos por pedido, y la consulta previa a un segundo formulario se
  detiene en cuanto la pasarela no contesta. La app recibe por qué no hay formulario
  (`code`, campo añadido). Un rechazo con código es «ahora no»: no marca el pedido como
  fallido.
- **La consulta previa a un cambio tiene 20 segundos.** Ocurre dentro de la petición del
  MASTER, en el único proceso que hay.
- **El preflight dice lo mismo que la aplicación:** el interruptor encendido es ATENCIÓN,
  las claves de TEST del archivo «no son para una tienda publicada», y la dirección de la
  API tiene que ser la oficial (una prueba mantiene iguales las dos listas).

### DEC-IMPORT-SPLIT-01 · Tres cargas, cada una con su plantilla de una hoja

- **Ninguna carga hace el trabajo de otra.** Productos y accesorios: crea y edita el
  catálogo, no mueve stock. Equipos: una fila = un equipo físico, por el escritor de
  siempre (`receive_units`). Stock: cantidades de lo que se cuenta por número. Cada
  pantalla lo dice arriba y enlaza a las otras dos; el menú las nombra distinto
  («Carga de productos», «Carga de equipos», «Carga de stock»).
- **Una plantilla es una hoja: sus cabeceras y nada debajo.** Se retiran la fila de
  ayuda, la fila de ejemplo y las hojas «Categorías», «Instrucciones» y «Ejemplo». Un
  archivo vacío es lo que parece, y lo que explica vive donde no se puede importar: en
  la pantalla y en una nota sobre cada cabecera.
- **Las listas viven dentro de la celda**, porque no hay segunda hoja a la que apuntar.
  Categorías activas de la empresa y sucursales de quien descarga, leídas al descargar,
  nunca fijas; «Destino» y «Condición», sus valores. **Ofrecen y no bloquean**, como en
  los archivos del propietario: Excel compara lo escrito letra por letra («nuevo» no es
  «Nuevo») y la carga lee los dos; lo que está mal se dice por fila al previsualizar.
- **Cuándo una lista no sale.** Una lista de celda es un texto de 255 caracteres
  (contados como los guarda Excel) separado por comas, y elegir un valor es escribirlo.
  Si los nombres no caben, si uno lleva coma, comillas, un carácter de control o un salto
  de línea, o si uno empieza como una fórmula (`=`, `+`, `-`, `@`), la plantilla sale sin
  esa lista. El dato se escribe y se valida al subir. Un nombre así es de la propia
  empresa, y una plantilla no entrega a sus usuarios una celda que calcula.
- **Lo que pedimos y lo que aceptamos son cosas distintas.** Los archivos hechos con
  las plantillas anteriores se siguen leyendo: sus filas de ayuda y de ejemplo y sus
  hojas de instrucciones no crean nada.
- **Un archivo de productos con una columna de stock, de serie o de IMEI** no la
  importa y lo dice a la vista, con a dónde ir. No se rechaza: el catálogo de ese
  archivo es válido.
- **Un archivo de equipos con una columna de cantidad o de stock** se rechaza entero.
  Qué cuenta stock se decide en un solo sitio (`import_formats.counts_stock`), para las
  dos cargas: una cabecera cuya primera palabra —pasados «Nº de», «Total»— es cantidad,
  stock, unidades, existencias, inventario, saldo… salvo que lo que sigue diga que
  cuenta otra cosa («Stock mínimo», «Unidades de medida», «Cantidad de SIM», «Unidades
  por caja»). No es una lista cerrada: una cabecera que no reconozca se lee de largo.
- **Un aviso no se entierra.** La previsualización muestra las primeras filas: las de
  error, después las que llevan un aviso y luego el resto. La cuenta de IMEI bajo
  productos sin marca va además arriba de la tabla.
- **Qué lleva un equipo lo dice su producto, con las capacidades que ya existen**
  (`is_serialized`, `requires_imei`): serie siempre; IMEI obligatorio si el producto
  está marcado «lleva IMEI»; IMEI 2 opcional y sólo con el primero. No hay una
  capacidad «doble IMEI» por producto ni se añade ahora (decisión del propietario,
  2026-10-11). Cuando el IMEI 2 viene, se valida igual que el primero: 15 dígitos,
  dígito de control y unicidad en la empresa, en cualquiera de las dos columnas.
- **Un IMEI bajo un producto no marcado «lleva IMEI» se guarda y se avisa** (decisión
  del propietario, 2026-10-11): es la regla de la pantalla «Registrar equipo» y no
  cambia. Queda trazado: el aviso en cada fila del trabajo, cuántas filas lo traían en
  el resumen del trabajo, y cuántos se guardaron en la auditoría de la carga
  (`imei_on_unmarked_products`), contados al registrar —la marca del producto puede
  cambiar entre la previsualización y la carga— y sin nombrar ningún equipo.
- Sin migración. No cambia el escritor del Kardex ni el de productos.

### DEC-UNIT-IMPORT-01 · Los equipos con serie se cargan de a uno por fila, por el escritor de siempre

- La carga masiva de stock escribe cantidades. Un producto con serie no tiene una
  cantidad que alguien escribe, así que ese archivo lo rechaza por fila. Para ellos hay
  una plantilla propia: «Equipos serializados.xlsx», **una fila = un equipo físico**.
- No es otro camino al stock. Cada fila termina en
  `stock_unit_services.receive_units`, el mismo escritor que «Registrar equipo»: mismas
  reglas de serie e IMEI, misma línea de Kardex, misma invariante.
- Una columna «Cantidad» rechaza el archivo entero. Leerla como «dos equipos con una
  serie» es justo lo que la plantilla existe para impedir.
- Previsualizar no escribe. Registrar es todo o nada y vuelve a comprobar cada serie
  contra el stock de ese momento; lo que cambió desde la previsualización nombra su fila.
- La empresa sale de quien carga; la sucursal se busca por nombre dentro de la empresa y
  dentro de sus sucursales. Un trabajo que toca una sucursal fuera de alcance no existe
  para esa persona: ni se aplica ni se lee, porque sus filas llevan series e IMEI.
- Reutiliza `BulkImportJob`/`BulkImportRow` con un tipo nuevo (`units`, migración `0110`).

### DEC-DOC-01 · Los documentos de una tienda comparten un diseño, y lo que dicen sale de la venta

- La nota de venta, su ticket, el comprobante de pedido y el ticket de cotización eran
  cuatro trazados. Ahora hay un módulo de estilo (`document_style`) y una página A4
  (`document_layout.render_a4`) que recibe el documento como datos. Un documento nuevo
  es una descripción, no otro trazado.
- **Ninguna tienda en el código.** Identidad, sucursal, logotipo y textos legales salen
  de la empresa, la sucursal, la venta o la configuración. Una prueba revisa los módulos.
- **Serie e IMEI vienen del equipo vendido.** Se leen de los movimientos de Kardex de la
  venta (que guardan los ids de los equipos) y de los equipos asignados al pedido, dentro
  de la empresa. Nunca de una descripción. Una devolución posterior no cambia lo que dice
  la reimpresión de aquella venta.
- **El logotipo se guarda con la nota**, como ya se hacía con los comprobantes fiscales.
  Una nota anterior a esta fase no tiene copia y usa el logotipo actual.
- **Sólo gris.** La jerarquía la dan el tamaño, el peso, las líneas y el espacio: se
  imprime igual en una láser monocroma. Las fuentes son las incorporadas del PDF; una
  tipografía embebida haría los documentos más pesados y sus pruebas ilegibles.
- **Interno no es fiscal.** La nota dice enmarcada que no es un comprobante electrónico
  y su número, que no es una serie fiscal. Las representaciones de boleta y factura no
  se tocaron: su formato lo fija la RS 114-2019 (DEC-FISC-PRINT-01).
- Las pruebas leen el texto y la estructura (tamaño de página, imágenes, páginas). No
  comparan bytes. `DOCUMENT_SAMPLES_DIR` deja los PDF para revisarlos a la vista.

### DEC-LIMIT-01 · El tamaño de una petición se decide una vez y se aplica tres

- Nada limitaba el cuerpo de una petición. Caddy lo dejaba pasar entero y Django no
  tiene un tope que cubra esta API: un JSON se lee del flujo sin límite y un formulario
  con archivos se escribe en disco, del tamaño que sea, en cualquier ruta —el inicio de
  sesión incluido—.
- **Una tabla, tres aplicaciones.** `store/request_limits.py` dice qué admite cada ruta:
  lo que su pantalla acepta más el sobre del formulario para las que reciben archivos,
  1 MiB para el resto. Un middleware rechaza por la longitud declarada antes de leer;
  `deploy/Caddyfile` repite los mismos números para cortar en el borde; la vista
  conserva su regla, que es la que sabe explicarse.
- **Quien responde 413 es el proxy.** Compara la longitud anunciada con el tope de la
  ruta y contesta sin llamar a ninguna aplicación. Dejar que lo hiciera Django daba 502
  una de cada diez veces: Django contesta y cierra sin leer el cuerpo que el proxy
  todavía le estaba enviando. El middleware de Django se queda como segunda línea, para
  una instalación sin este proxy delante.
- **Los números están escritos dos veces a propósito.** Caddy no puede leer Python. Una
  prueba compara los dos archivos y falla si dejan de coincidir, o si una vista empieza
  a recibir archivos sin tener su tramo.
- Una ruta que nadie listó recibe el tope pequeño: lo nuevo falla cerrado.
- El cuerpo sin longitud declarada (por trozos) no es una vía: Django entrega a la vista
  un flujo limitado a la longitud declarada, que entonces es cero.
- **El proxy lee el cuerpo entero antes de pasarlo donde Django lo leería sin saber quién
  llama**: la ruta general y las dos que reciben llamadas de fuera (notificación de pago,
  webhook de WhatsApp). Django atiende con ocho hilos; sin eso, nueve cuerpos que no
  terminan de llegar dejan a la API sin hilos. Cada tramo declara si es de ésos
  (`read_without_session`) y la prueba exige que el proxy lea exactamente ésos.
- **Las rutas de archivos no se leen antes de autorizar.** Para que sea verdad, la
  comprobación CSRF toma el token sólo de la cabecera: Django lo busca primero en el
  formulario, y buscarlo ahí es leer la subida entera antes de saber si quien la envía
  puede enviarla.

### DEC-LOG-01 · Los registros no guardan lo que una dirección puede llevar

- El registro de acceso escribía la línea de cada petición. Una dirección puede ser una
  credencial (el enlace de seguimiento de una reparación, el token de una invitación, el
  token con que Meta verifica un webhook) o llevar lo que alguien escribió en un
  buscador (un IMEI, un documento, un teléfono).
- **Una ruta conserva su forma y pierde el token. Una consulta conserva sus claves y
  sólo los valores de una lista corta de claves inocuas** (`page`, `branch`, `status`…).
  Negar por omisión: un parámetro en el que nadie pensó sale oculto, no a la vista.
- La misma regla filtra las líneas que escribe la aplicación (Django nombra la ruta de
  cada petición que rechaza).
- Producción no arranca sin `EMAIL_BACKEND`: el valor de desarrollo escribe cada correo,
  con sus enlaces de un solo uso, en ese mismo registro.
- Lo que no se hizo: enmascarar el identificador de un inicio de sesión fallido. Es lo
  que permite investigar un ataque, y la contraseña nunca se registra.

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

### DEC-INVENTORY-UNIFIED-01 · Un catálogo, una regla para publicar en bloque y dos clases de archivo

Fase INVENTORY-UNIFIED-01, partes 1 y 2 (rama `feat/inventory-unified`).

- **Un solo catálogo.** No hay «productos de la web» y «productos internos»: hay
  productos, y `Product.is_published_online` dice de cada uno si se ofrece en la tienda
  en línea (DEC-PRODUCT-DEST-01). La acción en bloque, los filtros y las descargas leen y
  escriben ese dato; ninguno crea otra lista ni toca `is_active`, precios, cantidades,
  equipos o Kardex.
- **Qué significa «puede publicarse en bloque».** Una definición,
  `store.product_publication.publication_problems`: activo (`inactive`), con precio mayor
  que cero (`no_price`), con imagen —`image_url` o alguna imagen de galería— (`no_image`) y
  con categoría (`no_category`). La devuelve la lista del panel en cada fila
  (`publication_problems`, sin una consulta por fila) y la usa el reporte de productos.
- **La regla es del bloque, no de la ficha.** Quien edita UN producto ve lo que hace y
  puede tener motivo para publicarlo sin foto; quien marca «los 600 resultados» no ve cada
  uno. Por eso la ficha guarda como siempre y la acción en bloque deja fuera lo que no está
  listo, diciendo cuál y por qué. Ocultar se permite siempre.
- **Resultado parcial explícito.** Publicar en bloque publica lo que está listo y devuelve
  lo demás en `refused`, con sus códigos. No es un fallo ni un «todo o nada»: rechazar 599
  productos listos por uno sin foto obligaría a trabajar de uno en uno. Lo que sí es todo
  o nada es un id que no es de la empresa (404, nada cambia).
- **«Todo lo que coincide» viaja como filtro, no como lista.** El servidor vuelve a
  evaluar el filtro con la MISMA función que la lista (`admin_views.filter_admin_products`)
  y compara con el total que se mostró (`expected_count`). Si difieren responde 409
  `selection_changed` y no cambia nada: confirmar «312 productos» no puede cambiar 340. Un
  filtro con una clave o un valor que no entiende se rechaza en vez de ignorarse, porque
  para una escritura «no entendí, así que todo» es la lectura equivocada.
- **Tope y bloqueo.** Hasta 2000 productos por lote (`MAX_BULK_PRODUCTS`), en una
  transacción, con las filas bloqueadas en orden de id; la ficha del producto bloquea esa
  misma fila, así que un guardado y un lote se turnan. Una línea de auditoría por lote que
  cambió algo, con los conteos y hasta 200 ids.
- **Un carrito puede tener una línea que ya no se vende.** Se devuelve marcada
  `available: false` con id, nombre e imagen, sin precio ni stock; no se puede cambiar su
  cantidad y sí quitarla. Se arma a mano y no recortando el producto público, para que un
  campo nuevo de mañana no llegue ahí por defecto.
- **Para volver a cargar o reporte.** Cada descarga (`store.inventory_exports`) sale en uno
  de dos modos. `reimport` usa la hoja y las cabeceras de la carga correspondiente, tomadas
  de sus propias constantes: el archivo vuelve a entrar tal cual. `report` añade columnas
  de sólo lectura, su hoja se llama «Reporte de …» y las tres cargas la rechazan por ese
  nombre. Se eligió rechazar y no «importar ignorando columnas»: quien edita «Activo» o
  «Publicado» en un reporte y lo sube esperaría un cambio que no ocurre.
- **Sin stock doble al restaurar.** Cargar los archivos `reimport` sobre la MISMA tienda
  no cambia nada: un producto se reconoce por su código (o su nombre) y queda igual, una
  cantidad igual a la actual no genera movimiento, y un equipo ya registrado se rechaza
  fila por fila. Para que eso sea cierto, el archivo de stock NO lleva productos con serie
  (la carga de stock rechaza una cantidad para ellos) y el de equipos NO lleva vendidos ni
  dados de baja (recargarlos los devolvería al estante).
- **El ZIP completo es de productos activos.** La plantilla de productos no tiene columna
  «activo»: un inactivo cargado en una tienda vacía volvería activo y a la venta.
- **Una celda nunca es una fórmula.** Todo texto exportado que empieza por `=`, `+`, `-`,
  `@`, tabulador o retorno lleva un apóstrofo delante (`xlsx_reader.guard_text`), y el
  lector de las cargas se lo quita (`unguard_text`) para leer el mismo valor. Sólo se quita
  UN apóstrofo y sólo delante de uno de esos caracteres; una celda que ES una fórmula
  sigue siendo un error de la fila. La descarga anterior (`/api/admin/inventory/export/`)
  conserva su forma; desde la revisión también protege esas celdas (más abajo).
- **Permisos de lectura.** El catálogo pide `products.view`; stock y equipos piden
  `inventory.view` y sólo alcanzan las sucursales de `visible_branches`; el ZIP pide las
  dos. `inventory.adjust` no abre una descarga: sólo decide si «Costo» lleva datos. No se
  añadió ninguna capacidad.
- **No hay marca.** El catálogo no tiene campo de marca y no se inventó uno: no hay filtro
  ni columna de marca.
- **Descartado.** Rutas bajo `api/v1/internal/` (no se pidieron en esta fase); un trabajo
  en segundo plano para exportar (el tope de 20 000 filas por libro cabe en una petición);
  una capacidad nueva para exportar.

**Partes 3 y 4 (misma decisión, misma rama).**

- **Un centro que reúne, no una carga que mezcla.** «Gestión masiva de inventario»
  (`/admin/inventory/bulk`) pone a la vista importar, exportar, imágenes e historial.
  Las tres cargas siguen siendo tres, con sus entradas de menú, sus rutas y sus
  asistentes; el centro enlaza a ellas. No hay un importador «universal»: un archivo
  que es dos cargas a la vez no se reparte, se devuelve.
- **El asistente dice a dónde va un archivo y no lo carga.** `POST
  /api/admin/imports/inspect/` (la misma dirección y la misma vista) responde además
  `suggested_import_type`, decidido por `store.import_assistant` SÓLO con los nombres de
  las hojas y las cabeceras, y con el vocabulario que ya comparten las cargas
  (`SERIAL_HEADERS`, `counts_stock`, los formatos de `detect`, las hojas «Reporte de …»).
  No lee filas, no crea un trabajo, no guarda un perfil.
- **Lo ambiguo es `unknown`.** Serie y cantidad en la misma hoja, columnas de producto
  y de serie, u hojas de dos cargas: no se elige la que «se lo tragaría». La única
  mezcla que se resuelve es la que la carga de productos ya resolvía y explicaba: un
  catálogo con una columna de stock es un catálogo, y la respuesta dice que su stock no
  se carga ahí. Una fila que cuenta cosas o trae series pero no nombra ningún producto
  no es una cabecera (así no se confunde el rótulo «Stock …» que el POS del propietario
  pone encima de sus cabeceras).
- **Una carga que no puedes hacer no se nombra.** A quien pregunta por un archivo de una
  carga para la que no tiene permiso se le dice eso, igual sea cual sea la carga.
- **Una entrada de menú puede pedir CUALQUIERA de varias capacidades**
  (`anyCapabilities`), sólo con empresa resuelta. No es una capacidad nueva ni cambia
  lo que piden las demás entradas (`requiredCapabilities` sigue siendo «todas»).
- **Imágenes en lote: por el nombre del archivo, nunca por el nombre del producto.**
  `<código>.<ext>` con sufijo opcional `_N`, `-N` o ` (N)` (uno a tres dígitos) para el
  orden; la de número más bajo es la principal. El código se busca con el mismo
  `CatalogueIndex` de la carga de productos (código interno y códigos de barras, exacto).
  Como un código puede terminar en `-2`, se prueban el nombre entero y el nombre sin su
  número: un producto, es ése; dos distintos o ninguno, el archivo no se carga y se dice.
- **Un archivo sin producto no detiene la carga; un error de un producto sí.** Lo que
  nadie reclama (sin producto, nombre ambiguo) se lista y se deja fuera, como los
  archivos huérfanos de la carga de productos. Lo que falla en un producto que sí tiene
  destino (imagen ilegible, dos archivos distintos con un nombre, más de 12) es un
  error de esa fila y nada se aplica: la misma regla de «no se aplica a medias».
- **Añadir o reemplazar, y reemplazar se escribe.** `add` nunca quita; la principal
  sólo cambia en una galería vacía o si se marcó. `replace` reconstruye la galería de
  los productos de la carga con lo subido, enseña cuántas imágenes pierde cada uno y
  exige `REEMPLAZAR` al aplicar. Reemplazar quita todo y vuelve a colocar: no intenta
  conservar la fila de una imagen que también viene en la carga.
- **Aplicar se detiene si lo previsto ya no es cierto.** El código tiene que seguir
  siendo del mismo producto y la galería tiene que ser, imagen por imagen, la que se
  previsualizó. Dos cargas sobre los mismos productos se turnan (productos bloqueados
  en orden de id) y la segunda recibe ese aviso en vez de sumar a ciegas.
- **Misma ruta para el lote, porque es la única dimensionada para él.** La vista previa
  es `POST /api/admin/products/import/preview/` con `kind=images` (nivel
  `product_import` de `request_limits.py` y del Caddyfile, que no cambian). Aplicar y
  las reglas son rutas JSON nuevas sin archivos. El tipo de trabajo `product_images`
  es una migración sólo de opciones (`0122`).
- **Descartado.** Un importador único que adivine; casar imágenes por nombre de
  producto; borrar imágenes sin confirmación; una vista multipart nueva; mover el
  almacén a S3 en esta fase (queda documentado qué haría falta).

**Tras la revisión independiente (misma decisión).**

- **Una fórmula no sale de aquí por ningún archivo.** También la descarga anterior del
  inventario y el `errors.csv` de cualquier carga pasan su texto por `guard_text`.
- **La selección en bloque se lee una vez.** Los ids de UNA consulta son lo que se compara
  con el total que vio el operador; contar y volver a leer dejaba cambiar un producto que
  nadie había contado.
- **Un solo orden de bloqueo.** El cambio en bloque, la carga de imágenes y la carga de
  productos toman sus productos de menor a mayor id, en una sola consulta. La carga de
  productos los tomaba en el orden del archivo y podía quedar en abrazo mortal con las
  otras dos.
- **Un archivo para volver a cargar es de productos activos**, también el del catálogo
  solo (no sólo el ZIP): la plantilla no tiene columna «activo» y un inactivo que no se
  reconoce al recargar nacería activo y publicado. El reporte los lleva todos.
- **La carga de productos sigue publicando lo que dice su columna «Destino»**, incluido lo
  que la acción en bloque dejaría fuera. No se convirtió en error: cambiaría una operación
  que el propietario ya aprobó. Lo que cambia es que se DICE antes de aplicar: un aviso en
  cada fila que pasa un producto existente a la web cuando le falta algo, y cuántos
  cambian de destino. Un producto nuevo sigue naciendo publicado, sin aviso.
- **Las descargas tienen su propio límite** (`admin_export`, 6 por minuto): cada una arma
  un libro en el único proceso del servidor y gastaba el cupo de las cargas.
- **Aplicar imágenes compara la galería entera**: qué imágenes, en qué orden y cuál es la
  principal. Y de una vista previa sólo queda en espera lo que se va a colocar.
- **«Mis pedidos» cierra el producto que ya no se vende**, con la misma forma que el
  carrito (`closed_product_payload`: id, nombre, imagen). La línea —cantidad y precio
  pagado— es del comprador y no cambia.
- **La acción en bloque va a la empresa de la lista.** La pantalla de productos manda
  explícita la empresa para la que el servidor decidió el acceso, en la lista y en la
  acción; el resto del panel sigue como estaba.
- **Deuda aceptada, no arreglada:** IMPORT-UNGUARD-APOSTROPHE-CODE (N8),
  IMG-IMPORT-NAME-READING (N12) y EXPORT-GUARD-VISIBLE (N14), en `docs/AUDIT_MEMORY.md` §10.
  (N8 y N14 los cerró después la decisión D7.)

**Decisiones del propietario (2026-10-13).** Sustituyen lo que decían arriba sobre los
archivos de productos activos, el aviso de la carga y el apóstrofo.

- **D1 · Lo nuevo nace interno.** «Productos nuevos importados: por defecto Solo stock
  interno.» Un producto NUEVO sin «Destino» nace con `is_published_online` apagado; un
  «Destino» explícito se respeta; uno existente sin «Destino» no cambia. La ficha y las
  demás altas, igual que antes.
- **D2 · Una regla de publicación, la misma en la acción en bloque y en la carga.**
  «Rechazar solo la publicación de productos comercialmente inelegibles, indicando motivo
  por fila. La importación como stock interno sí se permite. No exigir imagen salvo regla
  configurable.» Elegible = activo, con precio y con categoría; la imagen sólo con
  `CompanySettings.publication_requires_image` (migración `0123`, `db_default` apagado,
  como `hold_orders_pending_review`). En la carga el rechazo no es error de fila: el resto
  de la fila se aplica y el producto queda interno; aplicar vuelve a preguntar con el
  producto bloqueado; un producto ya publicado no se despublica por esta regla. Sustituye
  al aviso de la revisión N3.
- **D3 · Activos e inactivos viajan; cargar no reactiva.** El archivo para volver a cargar
  y el ZIP llevan los dos y respetan el filtro; la columna «Estado» sólo decide cómo nace
  un producto nuevo. Una carga NUNCA cambia `is_active` de uno existente. Sustituye a la
  regla «sólo activos» de la revisión N2. (Tercera revisión, F1: se lee también Sí/No,
  1/0 y verdadero/falso, y un valor ilegible sólo es error en una fila que crea.)
- **D4 · El límite de descargas se configura y se explica.** `ADMIN_EXPORT_RATE` (6/min),
  validado al arrancar; un 429 en castellano con `Retry-After`. Las cinco descargas
  seguidas caben en el cupo por omisión.
- **D5 · Identidad estable, nunca un nombre ambiguo.** «Identificador» = `<empresa>-<producto>`:
  ni secreto ni nuevo (los dos números ya están en las direcciones del panel) y sólo vale
  en la empresa sobre la que se actúa. Decide antes que el nombre; ajeno o inexistente se
  ignora con un aviso que no dice si existe en otra parte; contra un código, error. Sin
  identificador ni código, el nombre sólo si lo lleva un único producto (como ya era).
  La plantilla del propietario no gana estas columnas.
- **D6 · Lo oculto no se expone en la app.** Las líneas de «Mis pedidos» v1 de un producto
  que ya no se vende llevan lo que lleva la línea cerrada de la web (nombre e imagen) y
  `product_slug` vacío; cantidad y precio son del pedido. Las claves que lee la app siguen.
- **D7 · Excel sin apóstrofos, CSV con él.** En un .xlsx la protección es el TIPO de celda:
  texto marcado como texto (`quotePrefix`), con el valor intacto; el lector rechaza las
  fórmulas por su tipo y no quita nada. Ningún escritor desplegado puso apóstrofos (la
  guarda nació en esta rama), así que no hay archivos antiguos que leer de otra manera.
  En `errors.csv`, que no tiene tipos, el apóstrofo se queda.

**Decisión del propietario (2026-10-14): imágenes en lote.**

- «Imágenes masivas: comparar códigos sin distinguir mayúsculas, normalizando espacios
  y Unicode. Ante asociación ambigua, rechazarla explícitamente.» Sólo la carga de
  imágenes: el código del nombre de archivo o del libro y todos los códigos de la
  empresa (también de productos inactivos) pasan por la misma clave —NFKC, sin
  mayúsculas, espacios recortados y colapsados, espacio duro como espacio—. Una clave
  que nombra dos productos no se asigna: el archivo o la fila se lista con el motivo.
  Un nombre entero ambiguo no cae a su lectura corta. La carga de productos y las
  demás siguen comparando exacto, como la caja. Cierra IMG-IMPORT-CASE.
- «Imágenes sin producto: permitir aplicar el resto del lote; informar archivos
  huérfanos y errores, sin asociarlos arbitrariamente.» Los archivos ya lo hacían;
  una fila del libro con un código de nadie, o ambiguo, era un error que detenía la
  carga y ahora se lista como un archivo sin producto. Lo que está mal en las imágenes
  de un producto sigue deteniéndola: la carga sigue siendo todo o nada sobre lo que
  sí tiene producto.
- «Mantener fotografías opcionales y permitir carga posterior.» Sin cambios: una
  imagen sólo se exige para publicar en bloque si la empresa lo configura (D2), y la
  carga de imágenes añade a productos que ya existen.
- Deudas que siguen como están, documentadas: ORDER-HISTORY-LIVE-NAME,
  IMPORT-STATE-NEVER-WRITES e IMPORT-ID-FOREIGN-WARNINGS.

### DEC-DEVICE-COMMERCE-01 · DC1 — costos y margen comercial

Fase DEVICE-COMMERCE-01, subfase DC1 (rama `feat/device-commerce`, migraciones `0126`–`0128`).

**Decisiones del propietario (2026-10-14).**
- Valoración: **costo promedio ponderado POR SUCURSAL** para productos por cantidad
  (accesorios) y **costo específico por unidad** para equipos con serie.
- Capacidad nueva `costs.view` («Ver costos y márgenes»), por omisión sólo para el
  preset `Administrador` y los roles heredados `admin`/`superadmin`.
- Nunca se llama «utilidad neta» al margen; es **margen comercial**.
- La historia no se recalcula desde el catálogo.
- Un pedido pendiente o cancelado sin pagar no es un cobro.

**Cómo queda.**
- `costs.view` decide TODA superficie que devuelve un costo: lista y detalle de equipos
  (sin ella la clave `cost` no viaja), las descargas (antes usaban `inventory.adjust`),
  el inventario valorizado y el margen. Corregir el costo de un equipo existente también
  la exige (403); registrar el costo al RECIBIR sigue siendo de `inventory.adjust`. El
  IMEI completo no cambia: es la regla del inventario para el personal.
- `inventory_services.create_stock_movement` decide el costo de cada línea de Kardex
  (`StockMovement.unit_cost`, `cost_source`) y el promedio de la sucursal
  (`BranchStock.average_cost`, 4 decimales, media al alza) bajo el mismo
  `select_for_update`; las reglas viven en `store.cost_services`. Entrada con costo →
  `(qty×prom + n×costo)/(qty+n)`; sin costo → `unknown`, promedio intacto; salidas,
  transferencias y devoluciones → el promedio; una transferencia llega al costo con que
  salió y sólo alimenta el promedio si ese costo era real (no de referencia). Un estante
  de costo desconocido toma el primer costo conocido. Estante vacío conserva el último
  promedio. `Product.reference_cost` sólo se usa sin promedio, marcado `reference`, y
  nunca se convierte en promedio.
- El costo de un equipo VENDIDO no se reescribe (servicio, API y carga de equipos).
- Cada `OrderItem` congela al escribirse su salida (pago web o venta de caja):
  `unit_cost`/`cost_source` y su parte de la base imponible (`net_amount`), del descuento
  sin IGV (`discount_net`) y el precio unitario sin IGV (`unit_price_net`). Las partes se
  reparten por peso bruto con el resto mayor (empates al id menor), así que **las líneas
  suman exactamente `Order.taxable_amount`**. Varias unidades con serie: la media de sus
  costos específicos (no se lee `OrderUnit`, que puede cambiar al entregar). Líneas
  anteriores: NULL/`unknown`, sin backfill.
- `GET /api/admin/sales/margin/` (`sales.analytics.view` + `costs.view`): ventas netas y
  brutas, cobros confirmados (PAGADOS por `paid_at`, la regla del tablero), costo de lo
  vendido, **margen = ventas netas de las líneas con costo − ese costo**, margen %,
  ventas con costo pendiente (líneas, ventas e importe), reembolsos (`REFUNDED`) y
  desgloses por producto, sucursal y canal. Restar el costo conocido de TODAS las ventas
  netas contaría cada venta sin costo como margen del 100 %.
- Excel: «Costo de referencia» en la plantilla de productos (y «Precio costo» de la
  plantilla POS de 18 columnas se lee como tal); «Costo unitario» opcional en la carga de
  stock (sólo en filas que aumentan); «Costo» de la carga de equipos es su costo
  específico. La vista previa enseña el costo anterior y el promedio resultante sólo a
  `costs.view`. Las descargas llevan costos sólo para `costs.view`.


**Revisión independiente de DC1 (2026-10-14): cinco P2 y cuatro P3 corregidos.**
- *Fuga del costo de referencia.* La vista previa de productos sólo avisaba «Costo de
  referencia: pasa a X» cuando el valor guardado era distinto, así que alguien con
  `products.manage` sin `costs.view` lo encontraba probando filas, y además podía
  aplicarlo. Ahora, sin `costs.view`, la columna no se lee: un aviso fijo por fila
  («Columna … ignorada: requiere permiso de costos»), sin comparación, y quien APLICA
  también necesita `costs.view` para escribirlo. Una sola respuesta para «¿puede ver
  costos?»: `cost_services.user_may_view_costs` (usuario + empresa, con el puente
  legacy) y `inventory_views.may_view_costs` (petición); el margen usa la primera.
- *Promedio rancio sobre stock sin costo* — **decisión del coordinador, por confirmar
  con el propietario.** `BranchStock.uncosted_quantity` (`0129`) cuenta las unidades que
  entraron sin costo. El promedio es sólo de la parte con costo (NULL si esa parte está
  vacía y queda stock); esas unidades son «costo pendiente» en la valoración y **salen
  primero**: una salida que se lleva alguna no tiene costo conocido (referencia marcada
  o `unknown`). Una entrada con costo reinicia el promedio para sus unidades; las
  pendientes siguen pendientes hasta que salen (un conteo no inventa un costo). El
  estante vacío conserva su último promedio para una devolución. `0129` marca como
  pendiente todo estante con stock y sin promedio (el stock anterior a DC1).
- *Promociones.* `margin_services.line_nets` pone la rebaja de una promoción en sus
  propias líneas con la misma atribución del comprobante F6
  (`fiscal_services._promotion_line_discounts`); sólo el resto del descuento se
  reparte por peso. Las líneas siguen sumando exactamente la base.
- *Admin de Django.* Instantáneas de la línea y promedio/pendiente de la sucursal, sólo
  lectura.
- *Roles personalizados.* `costs_view_report` (sólo lectura, sólo cuentas) dice cuántas
  empresas quedan sin nadie con `costs.view` tras `migrate`; el despliegue lo corre y se
  otorga a mano donde haga falta.
- P3: filtro de sucursal de los reembolsos fijado por prueba; las líneas anteriores a
  DC1 aparecen en los desgloses con su neto calculado desde el pedido (`net_estimated`,
  costo pendiente) en vez de 0,00; el texto del tablero que decía que no había modelo de
  costos, corregido.

### DEC-DEVICE-COMMERCE-01 · DC2 — variantes y tienda de nuevos y seminuevos

Migraciones `0130`–`0132` (aditivas, nulas o con `db_default`, reversibles).

**Decisión del propietario (2026-10-14).** La compra de unidad concreta vale para equipos
NUEVOS y SEMINUEVOS: el equipo que el comprador apartó es el que se vende. Reemplaza, para
lo que se vende por unidad, la regla de SERIAL-PICK-01 («el pago aparta el más antiguo»).

**Cómo queda.**
- *Una variante sigue siendo un `Product`.* `ProductModel` (por empresa) agrupa variantes;
  `Product.product_model`, `condition_line` (`new`/`used`) y `variant_key` (ids de opción
  ordenados). Opciones configurables por empresa: `VariantAttribute` (texto o color) y
  `VariantOption` (`swatch_hex` `#RRGGBB`). `ProductVariantOption`: una opción por atributo
  (único `product, attribute`); dos variantes de un modelo con las mismas opciones y línea,
  imposibles (único parcial `product_model, condition_line, variant_key`). Stock, Kardex,
  carrito, caja y líneas fiscales siguen leyendo el producto. Un producto sin modelo
  (accesorio) no cambia.
- *Se vende por unidad* (`Product.sells_by_unit`) todo producto serializado que es variante
  de un modelo, nuevo o seminuevo. **Por confirmar con el propietario:** un producto
  serializado SIN modelo conserva SERIAL-PICK-01 (el pago aparta el más antiguo y se
  identifica al entregar) hasta que se le asigne modelo.
- *Navegación.* No se tocan las categorías: `GET /api/catalog/models/?category=&line=`
  cuenta las dos líneas, y la tienda muestra las pestañas sólo cuando hay de ambas. Los
  rótulos son de la empresa (`StorefrontPageSettings.new_line_label/used_line_label`;
  vacío = «Nuevos» / «Seminuevos»). Nada nombra una marca.
- *API pública.* `GET /api/catalog/models/<slug>/`: atributos y opciones, variantes nuevas
  con precio, imágenes y disponibilidad (stock propio de la sucursal que despacha; el campo
  `availability` es el gancho de DC3 para proveedor), y los seminuevos uno por uno. Lista
  blanca escrita a mano: `id` público (UUID `StockUnit.public_id`, no la pk: la pk dice
  cuántos equipos recibió la tienda), opciones, batería (`null` = «no informado», nunca un
  número inventado), estado estético, precio, fotos, notas públicas e `imei_hint` (los 4
  últimos dígitos). Nunca IMEI completo, serie, costo ni proveedor. La lista general de
  productos deja de listar variantes SEMINUEVAS (su precio no significa nada) y gana
  `product_model`, `condition_line`, `sells_by_unit`.
- *Equipo.* `StockUnit.battery_health` (0–100, nula), `cosmetic_grade` (escala por empresa,
  `CosmeticGrade`; la sugerida se crea al aprovisionar una empresa nueva, con
  `seed_cosmetic_grades --company` o desde el panel; nunca por migración), `public_notes`,
  `sale_price` (seminuevo: sin precio no se ofrece en línea; el precio de un equipo vendido
  no cambia), `UnitPhoto` (misma tubería pública que las galerías de producto, nunca el
  almacén de evidencias).
- *Apartado web.* Agregar al carrito aparta UN equipo (el elegido, o el más antiguo de la
  variante) con `stock_unit_services._move`: AVAILABLE→RESERVED con línea de Kardex
  (`manual_exit`, «Apartado web: carrito»), así que la invariante cantidad == disponibles se
  mantiene, la caja no puede vender un equipo apartado y dos compradores hacen cola en la
  fila del estante (el segundo recibe `unit_taken`). Titular = SHA-256 de la clave del
  carrito; plazo `DEVICE_HOLD_MINUTES` (15), se amplía a `DEVICE_ORDER_HOLD_MINUTES` (45) al
  crear el pedido; tope `DEVICE_HOLD_MAX_PER_CART` (3). Vencido, vuelve al estante al leer
  (carrito, página del modelo, checkout) y con `release_expired_unit_holds` (cron). Un
  apartado del personal (sin titular web) no vence.
- *Precio por unidad en el pedido* — **por confirmar.** Una línea de pedido NO puede llevar
  dos precios: dos seminuevos de una variante a 1999,00 y 1500,00 en una línea de cantidad 2
  exigirían un precio unitario de 1749,50 — y con 3 equipos un precio que no existe en
  céntimos. Por eso cada equipo vendido por unidad es SU línea (`OrderItem.stock_unit`,
  cantidad 1, su precio congelado al apartar). La regla «una línea por producto» sigue para
  toda línea sin equipo (índice único parcial) y la salida de Kardex sigue siendo UNA por
  (pedido, producto), nombrando todos sus equipos: la idempotencia del pago no cambia. El
  comprobante y los papeles imprimen cada equipo en su línea con su precio; la instantánea
  DC1 de cada línea es el costo de ESE equipo (`unit`).
- *Pago.* `_confirm` → `record_sale_stock_movements` → `_sell_chosen_units`: el apartado de
  este pedido se libera dentro de la misma transacción y bajo el bloqueo del estante, y la
  salida de venta nombra ese equipo. Si el apartado venció y el equipo sigue libre, se vende
  igual; si otro lo tomó, la línea queda como faltante (el dinero ya se cobró; decide una
  persona), nunca se vende otro equipo. Se registra `OrderUnit` con método `buyer`: no hay
  nada que identificar al entregar, y «olvidar» esa identificación se rechaza.
- *Checkout.* Antes de crear el pedido se revisa cada apartado: sigue siendo de este
  carrito, no venció, está en la sucursal que despacha y su precio no cambió; si cambió, el
  apartado toma el precio nuevo y se avisa (`cart_changed`) sin crear nada. La app nativa
  no tiene carrito con apartados todavía: rechaza esos productos (`unit_required`).
- *Excel.* Equipos: «Modelo», «Capacidad», «Color», «Línea», «Estado estético», «Batería %»,
  «Precio de venta» (opcionales, al final). «Modelo» como única columna de nombre (archivos
  viejos) sigue siendo el nombre y sólo se prueba como modelo si ningún producto se llama
  así. Productos: «Modelo», «Capacidad», «Color», «Línea» (el modelo y las opciones deben
  existir). Un archivo sin esas columnas se carga igual que antes.
