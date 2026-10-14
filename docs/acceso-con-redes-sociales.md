# Acceso con Google, Facebook y Apple

Cómo entran los **compradores** con su cuenta de Google, Facebook o Apple, qué comprueba
el servidor, qué se guarda y cómo se configura cada proveedor. Fase SOCIAL-AUTH-01
(Facebook y Apple) sobre GOOGLE-AUTH (Google). Decisiones: `05_DECISIONES_TECNICAS.md`
(DEC-GOOGLE-01, DEC-SOCIAL-01).

> **Estado:** implementado y probado con proveedores simulados. **No hay credenciales
> reales**: ningún inicio de sesión real con Facebook o Apple se ha hecho todavía. Sin
> configurar, los botones no aparecen y las rutas responden 404.

## 1. Lo esencial

- **Sólo cuentas de comprador.** Facebook y Apple nunca abren una cuenta del equipo
  (staff, superusuario, rol interno o con cualquier `Membership`, activa o no) y nunca
  dan un privilegio. El personal entra con usuario y contraseña. Google conserva su
  comportamiento anterior (las personas invitadas al equipo ya entran con él).
- **La identidad es proveedor + identificador estable** (`ExternalIdentity.provider` +
  `subject`), nunca el nombre ni el correo.
- **Nada se vincula solo.** Si el correo que da el proveedor ya tiene cuenta, la tienda
  dice «inicia sesión con tu contraseña y vincula desde Mi cuenta». Vincular pide la
  contraseña actual.
- **No se guarda ningún token** del proveedor: se usa una vez en el servidor y se descarta.
- **La sesión es la de siempre**: cookies HttpOnly, con su renovación, cierre y revocación.

## 2. Cómo funciona

| | Google | Facebook | Apple |
|---|---|---|---|
| Flujo | ID token de Google Identity Services (ventana emergente) | Authorization Code + PKCE (S256), en el servidor | Authorization Code, `response_mode=form_post`, en el servidor |
| Anti-falsificación | `nonce` firmado + cookie HttpOnly | `state` + cookie de enlace | `state` + `nonce` + cookie de enlace |
| Verificación | firma RS256 (claves de Google), audiencia, emisor, caducidad, `email_verified` | canje con la clave secreta y el verificador PKCE; `debug_token` (app, validez, persona); `/me` con `appsecret_proof` | secreto de cliente ES256; `id_token` RS256 con las claves de Apple: emisor, audiencia = Services ID, caducidad, `nonce` |
| Correo | verificado: es el de la cuenta | **no se usa como correo de la cuenta** (Facebook no dice si está verificado); se compara y se guarda en `email_at_link` | verificado: es el de la cuenta; puede ser un reenvío privado (`…@privaterelay.appleid.com`) o faltar |
| Configuración | ID de cliente | ID de app + clave secreta + versión de la Graph API | Services ID + Team ID + Key ID + clave `.p8` |

**El intento** (Facebook y Apple): al pulsar el botón, `GET /api/auth/<p>/start/` crea en el
servidor un `state`, un `nonce` y un verificador PKCE, válidos 10 minutos y de un solo uso,
y deja en el navegador una cookie HttpOnly (`blackdog_social`, ruta `/api/auth/`) cuyo
resumen guarda el intento. La vuelta sólo se acepta en el navegador que tiene esa cookie:
un enlace de vuelta copiado, repetido, vencido o de otro proveedor se rechaza sin canjear
nada. El código se canjea una sola vez.

**Apple vuelve con un POST** desde appleid.apple.com. Ese POST no lleva las cookies de la
tienda (son `SameSite=Lax`), así que no decide nada: entrega el código al intento y manda
al navegador (303) a un GET de la misma dirección, que sí las lleva y termina el intento.
El `id_token` que viene en ese POST no se lee: sólo vale el que devuelve Apple al canjear.

**Adónde vuelve la persona:** a `/auth?social=<p>` (sesión abierta; la página la lleva a
`next` si era una ruta de la tienda) o a `/auth?social_error=<motivo>&provider=<p>`.
Motivos: `refused`, `cancelled`, `link_required`, `not_allowed`, `inactive`.

**Sin correo** (Facebook sin correo, o Apple sin él): la cuenta se crea igual y sirve para
comprar; el checkout pide el correo del pedido como siempre.

### Vincular y desvincular («Mi cuenta» › Cuentas vinculadas)

- **Vincular Facebook o Apple** pide la contraseña actual; luego va al proveedor y vuelve a
  `/account?linked=<p>`. Una cuenta sin contraseña (creada con un proveedor) primero crea
  una con «¿Olvidaste tu contraseña?». Si esa cuenta del proveedor ya abre otra cuenta de
  la tienda: `already_linked`, y no se mueve. La vuelta exige que siga abierta la misma
  sesión que empezó.
- **Google** se vincula como antes: al entrar con Google con un correo que ya tiene cuenta,
  se pide la contraseña de esa cuenta.
- **Desvincular** cualquiera de los tres se rechaza si dejaría la cuenta sin forma de
  entrar (ni contraseña ni otro proveedor).

### Rutas

| Ruta | Para |
|---|---|
| `GET /api/auth/social/config/` | `{providers: {google, facebook, apple: {enabled}}}`; nada más |
| `GET /api/auth/{facebook,apple}/start/?next=/ruta` | empezar (302 al proveedor) |
| `GET /api/auth/facebook/callback/` · `POST`+`GET /api/auth/apple/callback/` | vuelta del proveedor |
| `POST /api/auth/social/{facebook,apple}/link/` `{password}` | empezar a vincular (con sesión y CSRF) |
| `GET /api/auth/social/identities/` · `DELETE …/identities/<p>/` | ver y desvincular |

La app móvil (`/api/v1/`) no tiene hoy entrada con Google ni con redes sociales; añadirla
(tokens nativos de Google/Apple/Facebook verificados en el servidor) es trabajo futuro.

## 3. Configuración

Todo se configura en **Panel › Configuración › Integraciones › Acceso** (sólo MASTER). Las
claves secretas se guardan cifradas y no vuelven a mostrarse. Cada entorno (pruebas,
producción) tiene su propia configuración en su propia base.

**La dirección de vuelta sale de `FRONTEND_URL`**, nunca de la petición. Tiene que ser el
origen público exacto de la tienda (`https://<dominio>`), y es la que se registra en cada
proveedor:

| Proveedor | Qué registrar | Valor, por dominio |
|---|---|---|
| Google | Orígenes de JavaScript autorizados | `https://<dominio>` |
| Facebook | URI de redirección de OAuth válidos | `https://<dominio>/api/auth/facebook/callback/` |
| Apple | Return URLs del Services ID | `https://<dominio>/api/auth/apple/callback/` |

Para cada dominio de la tienda (por ejemplo, pruebas y producción) se registra su línea.
La barra final cuenta: se escribe tal cual.

### 3.1 Google Cloud

1. Google Cloud Console › APIs y servicios › **Pantalla de consentimiento de OAuth**:
   tipo «Externo», nombre de la tienda, correo de soporte, dominio autorizado, enlaces de
   privacidad y condiciones. Ámbitos: `openid`, `email`, `profile` (no sensibles).
   Publicar la aplicación («En producción») para que entre cualquiera.
2. **Credenciales › Crear credenciales › ID de cliente de OAuth**, tipo «Aplicación web».
   Orígenes de JavaScript autorizados: `https://<dominio>` (y `http://localhost:3000` sólo
   en el proyecto de desarrollo). No hace falta URI de redirección: el flujo es de ventana.
3. Copiar el ID de cliente (`…apps.googleusercontent.com`) en la integración «Inicio de
   sesión con Google», probar y activar. No hay secreto de cliente en este flujo.

### 3.2 Meta for Developers (Facebook)

1. developers.facebook.com › **Crear app** › caso de uso «Autenticar y solicitar datos a
   los usuarios con el inicio de sesión con Facebook».
2. **Configuración de la app › Básica**: dominio de la app `<dominio>`, URL de la política
   de privacidad, URL de las instrucciones de eliminación de datos, icono y categoría.
   Copiar el **ID de la app** y la **clave secreta**.
3. **Inicio de sesión con Facebook › Configuración**: «Inicio de sesión con OAuth web» sí,
   «Usar el modo estricto para URI de redirección» sí, «Forzar HTTPS» sí, y en «URI de
   redireccionamiento de OAuth válidos» la de la tabla. El SDK de JavaScript no se usa.
4. **Permisos**: `public_profile` y `email`. Pasar la app a **modo Activo** (Meta puede
   pedir verificación del negocio o revisión según el tipo de app; revisarlo en el panel).
   En modo Desarrollo sólo entran las personas con un rol en la app.
5. En la integración «Inicio de sesión con Facebook»: ID de la app, clave secreta y la
   versión de la Graph API (por defecto `v23.0`; Meta retira cada versión unos dos años
   después). «Probar» pide a Facebook un token de la app: comprueba el par ID/clave.

### 3.3 Apple Developer (Sign in with Apple)

Hace falta una membresía del Apple Developer Program.

1. **Certificates, Identifiers & Profiles › Identifiers › App IDs**: un App ID (el
   «primario») con la capacidad **Sign in with Apple**.
2. **Identifiers › Services IDs › +**: identificador, por ejemplo `pe.<tienda>.web` (este
   es el **Services ID** y la audiencia de cada token). Activar Sign in with Apple ›
   Configure: App ID primario; **Domains and Subdomains** `<dominio>` (sin `https://`);
   **Return URLs** la de la tabla. Apple sólo acepta HTTPS y no acepta `localhost`: Apple
   se prueba en un dominio de pruebas con certificado.
3. **Keys › +**: activar Sign in with Apple, asociarla al App ID primario, descargar el
   `.p8` (**sólo se puede descargar una vez**) y anotar su **Key ID**. El **Team ID**
   está en Membership.
4. **Correo a direcciones de reenvío**: para que los correos de pedidos lleguen a una
   dirección `…@privaterelay.appleid.com`, registrar el dominio o el remitente de la tienda
   en Services › **Sign in with Apple for Email Communication**, con SPF y DKIM.
5. En la integración «Inicio de sesión con Apple»: Services ID, Team ID, Key ID y el
   contenido completo del `.p8`. «Probar» comprueba que la clave firma y que el servidor
   alcanza las claves públicas de Apple; si Apple acepta el conjunto sólo se sabe
   entrando.

## 4. Lo que no se ha podido comprobar sin credenciales

- Un inicio real con Facebook o con Apple, y el canje real de un código.
- Que Meta acepte `code_verifier` en el canje con la clave secreta, y la versión de la
  Graph API que esté vigente al configurarlo.
- El formato exacto de lo que Apple publica con `form_post` en un navegador real.
- La entrega de correo a direcciones de reenvío de Apple.

Primera prueba recomendada en un dominio de pruebas con HTTPS: entrar con cada proveedor
con una cuenta nueva, salir, volver a entrar (misma cuenta), vincular desde «Mi cuenta» y
desvincular.
