# Correos

Todos los correos de la tienda salen con una sola plantilla. Este documento dice dónde
está, cómo se usa y cómo se prueba.

## 1. La idea en cinco líneas

1. La plantilla es `backend/store/mail/plantilla/plantilla-maestra.html`, tal como la
   entregó quien la diseñó. No se edita a mano.
2. Cada correo es un **diccionario de datos** con la forma de
   `backend/store/mail/ejemplos/*.json`. La plantilla muestra un bloque si su clave
   existe y lo oculta si no.
3. Un **constructor por tipo de correo** (`store/mail/builders.py`) devuelve ese
   diccionario. Decide qué dice el correo; no sabe cómo se ve.
4. `mail.send(tipo, destinatario, datos, company=…)` lo convierte en HTML y en texto
   plano con los mismos datos, y lo envía.
5. **La plantilla es de una empresa**, no de la plataforma (§3).

La guía de bloques de quien diseñó la plantilla está junto a ella:
`backend/store/mail/plantilla/GUIA-plantilla-maestra.md`.

## 2. Qué correos la usan

| Correo | Tipo | Bloques | Dónde se envía |
|---|---|---|---|
| Verificar correo | `verify_email` | principal · botón con enlace | `store/emails.py` |
| Restablecer contraseña | `password_reset` | principal · aviso · botón con enlace | `store/emails.py` |
| Invitación de personal | `staff_invitation` | principal · datos · aviso · botón con enlace | `store/staff_views.py` |
| Compra confirmada | `order_confirmation` | principal · detalle · datos · aviso · botón, y el PDF adjunto | `store/email_services.py` |
| Aviso interno de pedido | `internal_order` | principal · detalle · datos · botón | `store/email_services.py` |

La verificación va por enlace, no por código: el bloque `codigo` de la plantilla no
aparece en ese correo.

## 3. De quién es la plantilla

El pie de la plantilla lleva el nombre, la dirección, el teléfono y las redes de una
tienda. `backend/store/mail/plantilla/marca.json` dice de cuál, por su `slug`.

| Correo de… | ¿Lleva la plantilla? |
|---|---|
| Esa empresa (pedidos, invitaciones) | Sí |
| La plataforma (cuenta, contraseña) | Sólo si `DEFAULT_STOREFRONT_COMPANY_SLUG` es esa empresa, y la persona no lo pidió desde la dirección de otra (`otra-tienda.dominio…`) |
| Cualquier otra empresa | No: conserva el correo neutro que tenía |

Otra empresa nunca firma con la dirección de la primera. Si la plantilla no se puede
rellenar —falta un dato fijo, o un dato del correo no cumple el contrato— sale el correo
neutro: un correo no se pierde por cómo se ve.

Eso vale para **componer**, no para **enviar**. Un correo que el servidor no aceptó no
se envía otra vez sin plantilla: iría al mismo servidor, y uno que lo aceptó y no llegó a
confirmarlo ya lo entregó. Hay un solo intento, con o sin plantilla.

Donde la plantilla viste los correos de cuenta, firman con el nombre de la tienda y
`PLATFORM_NAME` no se usa: esa variable es de los correos neutros.

### Los valores fijos

Los `[corchetes]` de la plantilla se rellenan una vez, al cargarla, sin tocar el archivo:

| Marcador | De dónde sale |
|---|---|
| `[URL del sitio web]` | `FRONTEND_URL` |
| `[URL pública del logo]` | `FRONTEND_URL` + `logo` de `marca.json` (el logotipo ya publicado en `frontend/public/assets/branding/`) |
| `[URL del canal de YouTube]` | `youtube` de `marca.json` |
| `[Razón social]` y `[RUC]` | Los de la empresa, en Panel › Configuración |

Si falta alguno, la plantilla no se usa y queda un error en el registro: un pie con
«[RUC]» impreso es peor que el correo neutro.

Se insertan como texto: lo que la empresa escribió en su razón social no puede actuar
como HTML ni como plantilla.

### El pie

El nombre, la dirección y el WhatsApp del pie están escritos en la plantilla. La versión
de texto los toma de `marca.json` (`nombre`, `direccion`, `whatsapp`), que debe decir lo
mismo que la plantilla; una prueba lo comprueba. Cambiarlos en Panel › Configuración no
cambia el pie de los correos: cuando cambien, se actualiza la plantilla y `marca.json` a
la vez.

## 4. Reglas de los datos

`store/mail/contract.py` las aplica antes de rellenar la plantilla.

- **Lo que no existe se omite.** `None`, `''`, `[]` y `{}` desaparecen, y con ellos su
  bloque. Una fila de «Referencia:» sin valor desaparece entera.
- **Todo es texto ya formateado.** Un número sin formatear se rechaza. Montos con
  `format.money()` → `S/ 1,234.00`; fechas con `format.date()` → `DD/MM/AAAA`, en la
  hora de la tienda.
- **Los datos se escapan siempre.** La plantilla usa `{{ }}` y nunca `{{{ }}}`: el nombre
  de un cliente no llega al HTML sin escapar.
- **Un enlace sólo va a `https://` o a la propia web** (`FRONTEND_URL`). Nada de
  `javascript:` ni de `http://` a otro sitio, y nada que parezca ir a un sitio y vaya a
  otro (`https://tienda@otro-sitio`). Otro sitio por `https://` sí se admite: la página
  de seguimiento de un courier, WhatsApp.
- **Claves desconocidas se rechazan**: un error de tecla no produce un correo a medias.
- Párrafos: de 1 a 3. Pasos de un progreso: un estado cada uno, y sólo uno «en curso».

El cuerpo de un correo no se escribe nunca en el registro: varios llevan un enlace que es
el acceso.

## 5. Añadir un correo nuevo

1. Escribe su ejemplo en `store/mail/ejemplos/NN-nombre.json`.
2. Añade el tipo a `KINDS` en `store/mail/service.py` y su constructor en `builders.py`.
3. Donde se envía:

   ```python
   from store import mail

   if mail.available(company):
       mail.send('mi_tipo', destinatario, mail.builders.mi_tipo(...), company=company)
   else:
       ...  # el correo neutro de siempre
   ```

   Si además debe salir el neutro cuando la plantilla falla, compón y envía por separado
   —`mail.render(...)` dentro del `try`, `mail.deliver(...)` fuera— como hacen
   `store/emails.py` y `store/staff_views.py`.

4. Una prueba en `store/test_mail.py`: el constructor es válido y no se sale de su ejemplo.

## 6. Verlos sin enviarlos

Sólo en desarrollo (`DEBUG=True`); en producción estas rutas responden 404.

```
http://localhost:8000/api/dev/mail-preview/
http://localhost:8000/api/dev/mail-preview/02-confirmacion-compra/
http://localhost:8000/api/dev/mail-preview/02-confirmacion-compra/?formato=texto
```

La primera lista todos los ejemplos; cada uno se abre en HTML y en texto plano. Son datos
inventados: ningún cliente, ningún pedido y ningún enlace que funcione.

Antes de activar un correo nuevo conviene mirarlo también en Gmail (web y app), Outlook
de escritorio y Apple Mail.

## 7. Configuración

No hay variables nuevas. El correo sigue saliendo por el SMTP configurado en Panel ›
Configuración › Integraciones (o, como respaldo, por las variables `EMAIL_*`), con el
remitente `DEFAULT_FROM_EMAIL`. Las credenciales no están en el código.

Lo que la plantilla necesita ya existe: `FRONTEND_URL`, `DEFAULT_STOREFRONT_COMPANY_SLUG`
y la razón social y el RUC de la empresa.

## 8. Pruebas

```
python manage.py test store.test_mail
```
