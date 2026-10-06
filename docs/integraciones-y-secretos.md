# Integraciones y secretos

Cómo se configuran los servicios externos de la tienda —correo, pasarela de pago,
WhatsApp, Google y SUNAT— desde **Panel › Configuración › Integraciones**, qué se queda
en el servidor, y cómo se protege, se cambia y se recupera lo que se guarda.

Fase INTEGRATIONS-CONSOLE-01. Decisiones: `05_DECISIONES_TECNICAS.md` (DEC-INT-01 a 06).

## 1. La idea en cinco líneas

- **El panel configura.** Un usuario MASTER escribe las credenciales, las prueba y las
  activa. No se edita ningún archivo, no se reinicia nada y no se vuelve a publicar.
- **El almacén de secretos protege.** Lo que se guarda va cifrado en la base de datos con
  una clave que no está en la base de datos.
- **El registro de proveedores decide.** Cada integración declara sus campos, cuáles son
  secretos, cómo se valida y cómo se prueba. La pantalla se dibuja con eso.
- **Los adaptadores hablan con terceros.** El código de la tienda pide «enviar un correo»
  o «abrir un cobro»; no sabe con qué proveedor.
- **Lo que se activa manda.** En cada envío, cada cobro y cada inicio de sesión se
  pregunta qué configuración está activa. Cambiarla en el panel cambia el siguiente.

## 2. Quién puede

**Sólo el MASTER** (superusuario de la plataforma). No existe una capacidad de empresa que
abra esta pantalla: ni el Administrador de una empresa con todas sus capacidades, ni el
dueño de una tienda, ni una cuenta con acceso al admin de Django.

| Dónde se comprueba | Cómo |
|---|---|
| El servidor, en cada ruta de `/api/admin/integrations/…` | `IsPlatformAdmin`: 403 a cualquier otra cuenta, en lectura y en escritura |
| El menú | el módulo es `platformAdminOnly`: no aparece para nadie más |
| La página | para quien no es MASTER dice «no está disponible» y no pide nada al servidor |

Todo lo que se hace queda en la auditoría (`AdminAuditLog`, `target_type='integration'`):
quién, qué integración, qué campos cambiaron y con qué resultado. **Nunca un valor.**

## 3. Cómo se usa

Cada integración es una tarjeta con su estado y su última prueba. «Configurar» abre el
editor, que sigue siempre el mismo orden:

1. **Guardar borrador.** Nada cambia para la tienda.
2. **Probar conexión.** La prueba usa lo que está guardado y dice qué clase de fallo hubo
   (credenciales rechazadas, sin respuesta, certificado no válido…), nunca el texto que
   devolvió el proveedor.
3. **Activar.** Sólo se puede activar lo que pasó la prueba, y exactamente esa versión: si
   se toca el borrador después de probarlo, hay que probar de nuevo.

Así, **una configuración que funciona nunca se sustituye por una que no se ha probado.**

| Estado | Qué significa |
|---|---|
| Sin configurar | no hay nada, ni en el panel ni en el entorno |
| Borrador sin probar | guardado; la tienda no lo usa |
| Probada, sin activar | pasó la prueba; la tienda todavía no lo usa |
| Activa | es lo que la tienda usa ahora |
| Activa, con error | es lo que la tienda usa y su última prueba falló: revisarla |
| Desactivada | apagada a propósito. **No se recurre al entorno:** apagado es apagado |

Otras acciones: **Probar la configuración en uso**, **Desactivar / Volver a activar**,
**Reemplazar** un secreto y **Revocar** (borra lo guardado; pide escribir `REVOCAR`).

Si dos personas editan a la vez, la segunda recibe «otra persona cambió este borrador» y
la pantalla carga lo que hay ahora. Nadie pisa el trabajo de nadie.

### Los secretos se escriben y no vuelven

De un secreto guardado la pantalla sólo sabe que está: muestra `••••••••••` «Configurada»
y un botón **Reemplazar**. No hay un campo del que leerlo, ni una API que lo devuelva.

- Lo que no se reemplaza no se reenvía: el servidor conserva lo que tenía.
- Lo que se escribe vive en la pantalla hasta que se guarda. No va a `localStorage`, ni a
  la dirección de la página, ni al paquete del frontend.
- No aparece en los registros, ni en la auditoría, ni en `ops_status`.

## 4. Qué se configura en el panel y qué se queda en el servidor

| En el panel (MASTER) | En el servidor (`deploy/.env.production`) |
|---|---|
| Correo SMTP | `SECRET_KEY` |
| Izipay: cuál de los dos productos y sus claves | Credenciales de PostgreSQL |
| WhatsApp Business de cada empresa | **`APP_CONFIG_ENCRYPTION_KEY`**, la clave raíz (§8) |
| ID de cliente de Google | `SITE_DOMAIN` y lo que sale de él |
| SUNAT: credenciales SOL y certificado | Almacenamiento de evidencias, nivel de registro |
| | `WHATSAPP_PROVIDER`, el interruptor de WhatsApp de ese servidor |
| | `ORDER_NOTIFICATION_EMAIL`, quién recibe el aviso de cada pedido |

Lo de la derecha es lo que hace falta para arrancar. No se administra desde el panel
porque el panel lo necesita para existir.

## 5. Cada integración

### 5.1 Correo SMTP

Alcance: la instalación. Por aquí salen la verificación de cuenta, la recuperación de
contraseña, los correos de pedido, las invitaciones y los avisos.

Campos: servidor, puerto, seguridad, usuario, contraseña (o contraseña de aplicación),
correo y nombre del remitente, espera máxima.

- **Seguridad:** STARTTLS (587) o SSL/TLS (465). «Ninguna» sólo se admite para un servidor
  en la misma máquina: sin cifrar, la contraseña viajaría en claro.
- **Probar conexión** entra al servidor y se autentica; no envía nada. Opcionalmente
  entrega un mensaje de prueba a la dirección que se indique.
- Resultados: Correcto · Credenciales rechazadas · Certificado TLS no válido · Sin
  respuesta a tiempo · Conexión rechazada · Servidor no encontrado.
- **Sin correo configurado, en producción el correo falla y se dice** (`ops_status`:
  ATENCIÓN). Nunca se escribe en el registro del servidor: los mensajes llevan enlaces de
  un solo uso.

### 5.2 Izipay (pasarela de pago)

Alcance: la instalación. Izipay vende dos productos distintos y **se usa uno**:

| En el panel | Qué es | Credenciales |
|---|---|---|
| Izipay · SDK web / Checkout | developers.izipay.pe | código de comercio, clave pública, API key, clave hash, dirección del token |
| Izipay · Mi Cuenta Web | secure.micuentaweb.pe, API REST V4 | usuario, contraseña, clave pública (HMAC opcional) |

- **Activar uno desactiva el otro**, en el mismo acto. No pueden estar los dos.
- Las credenciales de un producto no se mezclan con las del otro: son formularios
  distintos y se guardan por separado.
- **TEST y PRODUCCIÓN se distinguen siempre** en pantalla. Mi Cuenta Web lo dice en sus
  propias claves (`testpassword_…` / `prodpassword_…`) y mezclarlas se rechaza.
- **«Probar conexión» con claves de TEST** pide a la pasarela lo primero que pide un
  cobro —un token de sesión o un `formToken`— para una orden que no existe. No se crea
  ningún pedido ni ningún pago.
- **Las claves de PRODUCCIÓN no se envían a ningún sitio desde la prueba:** sólo se
  comprueba que son coherentes. Activarlas pide escribir `PRODUCCION`.
- El cobro lo sigue confirmando la notificación de la pasarela al servidor, firmada. Lo
  que diga el navegador no marca nada como pagado.
- La ruta de notificación del producto que no está activo no existe (404).

**Cambiar de producto con cobros abiertos.** Un pago que se abrió con el producto anterior
y cuya notificación llega después del cambio no encuentra su ruta. Cambia de producto
cuando no haya compras a medias (PAY-SWITCH-PENDING, §10).

**Lo que sigue abierto.** IZIPAY-PRODUCT e IZIPAY-TOKEN-CONTRACT no se cierran con esta
fase: hace falta saber qué producto tiene contratado el propietario y validar sus claves
de TEST contra la pasarela real. La consola es ahora el sitio donde hacerlo.

### 5.3 WhatsApp Business

Alcance: **cada empresa**. El MASTER lo administra desde la consola: Integraciones ›
WhatsApp Business › elegir la empresa.

Campos: identificador del número, identificador de la cuenta (opcional), token de acceso,
secreto de la aplicación y token de verificación del webhook.

- Lo que se activa para una empresa sólo lo usa esa empresa.
- **«Probar conexión»** le pregunta a Meta por el número (una lectura). No se envía ningún
  mensaje a nadie.
- **Activar no enciende los avisos.** Encenderlos sigue siendo decisión de la empresa, en
  Administración › Mensajería, y cada cliente tiene que haber aceptado.
- La dirección del webhook que se registra en Meta está en esa misma pantalla de la
  empresa. El webhook sólo cree lo que viene firmado con el secreto de la aplicación.
- `WHATSAPP_PROVIDER` es el interruptor del servidor: con `disabled` ninguna empresa
  envía, esté como esté en la consola. `ops_status` lo dice.

### 5.4 Inicio de sesión con Google

Alcance: la instalación. **Un solo valor: el ID de cliente de OAuth**, que es público.
Este flujo no usa secreto de cliente y la consola no lo pide.

- En Google Cloud, el dominio de la tienda tiene que estar entre los «orígenes de
  JavaScript autorizados» de ese cliente.
- La prueba comprueba la forma del ID y que el servidor alcanza las claves públicas de
  Google. Que sea el ID del proyecto correcto se termina de comprobar iniciando sesión.
- No cambia nada de lo demás: el servidor verifica el token, el *nonce* es de un solo uso,
  la sesión es la cookie HttpOnly de siempre y enlazar una cuenta existente sigue pidiendo
  estar dentro.

### 5.5 SUNAT (facturación electrónica)

Alcance: **la instalación**, y **sólo el entorno BETA** (pruebas) de SUNAT. Es lo que el
código sabe hacer hoy; la consola no ofrece «Producción» porque no existe.

Campos: RUC, usuario SOL, clave SOL, certificado digital (`.p12` / `.pfx`) y su contraseña.

- **El certificado** se sube desde el formulario, se abre y se comprueba antes de guardar
  nada (contraseña, clave privada), está limitado a 64 KiB y se guarda cifrado con lo
  demás. No queda como archivo en el servidor y no tiene dirección pública.
- La prueba dice de quién es el certificado y hasta cuándo vale. Un certificado vencido no
  pasa. **La clave SOL no se puede comprobar sin emitir:** se comprueba al emitir en BETA.
- **Activar es lo que enciende la emisión**, y pide escribir `EMITIR`. Guardar o probar no
  encienden nada.
- Series, endpoint y entorno siguen sin poder elegirse desde ningún formulario.

## 6. «Configurado mediante entorno»

Una instalación anterior a la consola sigue funcionando con sus variables de entorno. La
regla, para cada integración:

1. Si hay una configuración **activa en el panel**, se usa ésa.
2. Si el panel la tiene **desactivada**, no se usa nada. No se recurre al entorno.
3. Si el panel **no tiene nada**, se usan las variables de entorno, como antes.

La tarjeta dice «Configurado mediante entorno». **Copiar a la consola** crea un borrador
con esos valores sin enseñarlos; se prueba y se activa como cualquier otro. Desde ese
momento las variables dejan de usarse y se pueden quitar del archivo.

Excepción deliberada: un correo del entorno **sin cifrar hacia otro equipo** no se copia.
La consola no guarda una contraseña que viajaría en claro.

| Integración | Variables de respaldo |
|---|---|
| Correo | `EMAIL_BACKEND`, `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_USE_TLS`/`SSL`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `DEFAULT_FROM_EMAIL` |
| Izipay | `PAYMENT_PROVIDER` y las `IZIPAY_*` o las `MICUENTAWEB_*` |
| WhatsApp | las `WHATSAPP_*` de cada empresa, enlazadas con `manage.py configure_whatsapp` |
| Google | `GOOGLE_OAUTH_CLIENT_ID` |
| SUNAT | `FISCAL_ENABLED` y las `FISCAL_*` |

**Retirada del respaldo.** No se elimina ahora. El plan: (1) esta fase, respaldo completo y
aviso en pantalla; (2) una fase posterior, `ops_status` avisa de cada integración que
siga en el entorno; (3) cuando la instalación real esté en la consola, se deja de leer el
entorno para estas cinco. Las de la derecha de la tabla del §4 se quedan siempre.

## 7. Saber cómo están

`python manage.py ops_status` (y `deploy/healthcheck.sh`, que lo copia) añade una línea:

```
OK    integraciones: Correo SMTP: activa (consola) · … · Izipay · Mi Cuenta Web: activa, TEST (consola) · …
```

Y da la alarma (`ATENCIÓN`, salida 1) cuando:

- la tienda no puede enviar correo, o no puede cobrar;
- hay configuración guardada y falta la clave raíz, o no se puede leer con la de ese servidor;
- la última prueba de una integración activa falló.

Nunca dice un servidor, un usuario, una clave ni un token.

## 8. El cifrado

- **Qué se cifra:** todos los campos secretos de una integración, juntos, en una columna.
  Los valores públicos (servidor, puerto, código de comercio…) se guardan legibles.
- **Con qué:** Fernet, de la biblioteca `cryptography` (AES-128-CBC con HMAC-SHA-256):
  cifrado autenticado estándar. No hay criptografía propia.
- **La clave raíz, `APP_CONFIG_ENCRYPTION_KEY`:** es del despliegue. Vive en
  `deploy/.env.production`, **no en la base de datos**, y no se administra desde el panel.
  Quien se lleve una copia de la base de datos sin ella no puede leer ninguna credencial.
- **Cada secreto está atado a su sitio:** integración, empresa (o plataforma) y borrador o
  activo. Una fila copiada a otra empresa no se abre.
- En desarrollo (`DEBUG`) la clave puede faltar: se deriva una de `SECRET_KEY`. En
  producción, sin ella, la consola no guarda ni lee nada y lo dice.

Generarla, en el servidor:

```sh
python3 -c "import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
```

`python3 deploy/preflight.py` comprueba que está y que tiene forma de clave, sin imprimirla.

## 9. Cambiar la clave raíz, y recuperarse

### Cambiarla (rotación)

1. Genera una clave nueva.
2. En `deploy/.env.production`: la nueva en `APP_CONFIG_ENCRYPTION_KEY`, la anterior en
   `APP_CONFIG_ENCRYPTION_KEYS_PREVIOUS` (varias, separadas por comas).
3. Reinicia el backend. Todo sigue leyéndose: se prueba con la nueva y, si no, con las
   anteriores.
4. `docker compose … exec backend python manage.py reseal_integration_secrets` vuelve a
   cifrar todo con la nueva.
5. Quita `APP_CONFIG_ENCRYPTION_KEYS_PREVIOUS` y reinicia.

Cambiar la clave raíz **no** cambia las credenciales de los proveedores. Si lo que se
sospecha es que una de ésas se filtró, se cambia en el proveedor y se **Reemplaza** en la
consola.

### Copias de seguridad

La copia de la base de datos lleva las credenciales **cifradas**. Sin la clave raíz no
sirven. Por eso:

- **Guarda la clave raíz con la copia de `deploy/.env.production`, en un sitio distinto del
  de las copias de la base de datos.** Juntas en el mismo sitio, quien tenga una tiene todo.
- Restaurar en un servidor nuevo necesita la misma clave raíz (o la anterior en
  `…_KEYS_PREVIOUS`).

### Si se pierde la clave raíz

Las credenciales guardadas no se pueden recuperar: es lo que hace que el cifrado sirva.
No se pierde nada más. `ops_status` lo dice («no se puede leer con la clave raíz de este
servidor»). Qué hacer:

1. Genera una clave nueva y ponla en el servidor.
2. En la consola, **Revoca** cada integración y vuelve a escribir sus credenciales (o
   pídelas de nuevo al proveedor).

Mientras tanto, una integración que también esté en las variables de entorno **no** vuelve
a ellas por sí sola: el panel la tiene tomada. Revocarla es lo que la devuelve al entorno.

## 10. Lo que queda pendiente

| ID | Qué |
|---|---|
| IZIPAY-PRODUCT, IZIPAY-TOKEN-CONTRACT | siguen abiertas: falta validar claves de TEST reales, desde la consola |
| PAY-TENANT-SCOPE | la pasarela es de la instalación, no de cada empresa |
| FISCAL-TENANT-SCOPE | SUNAT es de la instalación y sólo BETA; `fiscal_config` ya recibe la empresa |
| PAY-SWITCH-PENDING | cambiar de producto deja sin ruta la notificación de un cobro abierto con el anterior |
| SUNAT-SOL-CHECK | la clave SOL sólo se comprueba al emitir |
| ENV-FALLBACK-RETIRE | retirar el respaldo de entorno (plan en §6) |
| INTEGRATION-READ-COST | cada uso lee y descifra la configuración: sin caché a propósito, para que un cambio valga al instante y en todos los procesos. Medirlo si el tráfico crece |
| MAIL-TENANT-SCOPE | un solo correo saliente para todas las empresas |

## 11. Para quien añada una integración

Un proveedor es una clase en `backend/store/integrations/providers/` registrada en el
registro (`registry.register`). Declara:

| Qué | Dónde |
|---|---|
| `id`, `label`, `category`, `scope` (plataforma o empresa), `description` | atributos |
| campos públicos y secretos, obligatorios, tipo (`text`, `int`, `choice`, `email`, `host`, `url`, `file`…) | `fields` |
| reglas entre campos | `clean(public, secrets)` |
| modos (`test`, `production`) y cuál es cada configuración | `supported_modes`, `mode()` |
| si en su categoría sólo puede haber uno activo | `exclusive_in_category` |
| la prueba, y qué se le puede pasar | `test(config, **opciones)`, `test_fields` |
| si activarlo exige escribir una palabra | `activation_confirmation()` |
| de dónde sale el respaldo de entorno | `from_env(company)` |

Y el código que lo usa pregunta al servicio (`store.integrations.service.resolve`) en cada
uso, nunca al arrancar. La pantalla no necesita ningún cambio: se dibuja con lo declarado.

Pruebas que toda integración trae: que lo activado en el panel es lo que se usa, que
cambiarlo cambia el siguiente uso sin reiniciar, que desactivada no recurre al entorno, que
ningún secreto vuelve, y —si es por empresa— que una empresa no ve lo de otra.
