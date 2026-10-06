#!/usr/bin/env python3
"""
Qué falta para publicar, dicho por un programa.

    python3 deploy/preflight.py                      lee deploy/.env.production
    python3 deploy/preflight.py --env <archivo>
    python3 deploy/preflight.py --smtp               además entra al servidor de correo (no envía nada)
    python3 deploy/preflight.py --smtp-send-to <dirección>
                                                     además entrega un mensaje de prueba a esa dirección
    python3 deploy/preflight.py --dns [--server-ip <IP>]
                                                     además pregunta a qué dirección apunta el dominio

Lee el archivo de variables de producción y responde, línea a línea:

    OK                    está y es coherente
    BLOCKED/OWNER-DATA    falta, y sólo el propietario puede darlo
    BLOCKED/OPTIONAL      falta, y la tienda publica sin ello
    ATENCIÓN              está, y se contradice o es inseguro

NUNCA IMPRIME UN VALOR. Lo que escribe está pensado para pegarse en un chat o en
una incidencia, y el archivo que lee es el único sitio donde viven todos los
secretos de producción. Dice nombres de variables, nunca su contenido. Las dos
excepciones son datos públicos que antes se comprueba que tienen forma de serlo:
el dominio de la tienda y el nombre del servidor de correo. Un valor que no la
tiene —porque alguien pegó otra cosa en esa línea— se rechaza sin repetirlo, y
ningún error se muestra con el texto que lo provocó.

LEE EL ARCHIVO COMO LO LEEN QUIENES LO USAN. Docker Compose se lo entrega al
backend (comentarios tras un valor, comillas, `export`, `$NOMBRE` sustituido) y
Django interpreta cada interruptor (vacío es «no»; «y» y «ok» son «sí»). Un
veredicto que saliera de otra lectura sería sobre otro archivo.

Comprueba la CONFIGURACIÓN. No sustituye al pago de prueba en el entorno TEST de
la pasarela (docs/pagos-equipos-documentos.md §1.4) ni al ensayo
(`sh deploy/rehearsal.sh`).

Sólo la biblioteca estándar. Termina con 0 si no hay nada que el propietario
deba todavía ni nada que corregir, y con 1 en otro caso.
"""
import argparse
import base64
import os
import re
import smtplib
import socket
import ssl
import stat
import subprocess
import sys
from email.message import EmailMessage

#: Toda variable que el archivo de ejemplo ofrece. Una prueba falla si el
#: ejemplo gana una que no esté aquí: una variable que este programa no conoce
#: sería una variable que nadie mira.
KNOWN = {
    'SITE_DOMAIN', 'SECRET_KEY', 'POSTGRES_PASSWORD', 'POSTGRES_USER', 'POSTGRES_DB',
    'APP_CONFIG_ENCRYPTION_KEY', 'APP_CONFIG_ENCRYPTION_KEYS_PREVIOUS',
    'DEFAULT_STOREFRONT_COMPANY_SLUG', 'PLATFORM_NAME', 'NEXT_PUBLIC_IMAGE_HOSTS',
    'EMAIL_BACKEND', 'EMAIL_HOST', 'EMAIL_PORT', 'EMAIL_USE_TLS', 'EMAIL_USE_SSL', 'EMAIL_TIMEOUT',
    'EMAIL_HOST_USER', 'EMAIL_HOST_PASSWORD', 'DEFAULT_FROM_EMAIL', 'ORDER_NOTIFICATION_EMAIL',
    'REQUIRE_EMAIL_VERIFICATION',
    'PAYMENT_PROVIDER', 'IZIPAY_ENV', 'IZIPAY_MERCHANT_CODE', 'IZIPAY_PUBLIC_KEY', 'IZIPAY_API_KEY',
    'IZIPAY_HASH_KEY', 'IZIPAY_TOKEN_URL', 'IZIPAY_CURRENCY', 'IZIPAY_IPN_URL',
    'MICUENTAWEB_SHOP_ID', 'MICUENTAWEB_PUBLIC_KEY', 'MICUENTAWEB_PASSWORD', 'MICUENTAWEB_HMAC_KEY',
    'MICUENTAWEB_API_URL', 'MICUENTAWEB_CURRENCY', 'MICUENTAWEB_IPN_URL',
    'EVIDENCE_STORAGE_BACKEND', 'EVIDENCE_STORAGE_BUCKET', 'EVIDENCE_STORAGE_ENDPOINT_URL',
    'EVIDENCE_STORAGE_ACCESS_KEY_ID', 'EVIDENCE_STORAGE_SECRET_ACCESS_KEY', 'EVIDENCE_STORAGE_REGION',
    'GOOGLE_OAUTH_CLIENT_ID',
    'WHATSAPP_PROVIDER', 'WHATSAPP_GRAPH_API_VERSION', 'WHATSAPP_SEND_INLINE',
    'DJANGO_LOG_LEVEL', 'FISCAL_ENABLED',
}

IZIPAY_KEYS = ('IZIPAY_MERCHANT_CODE', 'IZIPAY_PUBLIC_KEY', 'IZIPAY_API_KEY', 'IZIPAY_HASH_KEY', 'IZIPAY_TOKEN_URL')
#: Lo que el adaptador de «Mi Cuenta Web» lee. La clave HMAC firma la copia que
#: recibe el navegador, que esta aplicación no usa para confirmar nada.
MICUENTAWEB_KEYS = ('MICUENTAWEB_SHOP_ID', 'MICUENTAWEB_PASSWORD', 'MICUENTAWEB_PUBLIC_KEY')
MICUENTAWEB_ANY = MICUENTAWEB_KEYS + ('MICUENTAWEB_HMAC_KEY',)
MAIL_KEYS = ('EMAIL_HOST', 'EMAIL_HOST_USER', 'EMAIL_HOST_PASSWORD', 'DEFAULT_FROM_EMAIL')
INSECURE = {'', 'changeme', 'changeme-dev-only', 'changeme-replace-in-production', 'postgres', 'password', 'blackdog'}
SMTP_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
#: Las mismas palabras que Django (django-environ) toma por «sí».
TRUE = {'true', 'on', 'ok', 'y', 'yes', '1'}
#: Variables cuyo valor es un secreto: un `$` dentro de ellas lo cambiaría Compose.
SECRET_NAMES = ('SECRET_KEY', 'POSTGRES_PASSWORD', 'APP_CONFIG_ENCRYPTION_KEY', 'APP_CONFIG_ENCRYPTION_KEYS_PREVIOUS',
                'EMAIL_HOST_PASSWORD', 'IZIPAY_API_KEY', 'IZIPAY_HASH_KEY',
                'MICUENTAWEB_PASSWORD', 'MICUENTAWEB_HMAC_KEY', 'EVIDENCE_STORAGE_SECRET_ACCESS_KEY')

HOSTNAME = re.compile(r'^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,61}[a-z0-9]$', re.I)
LOOPBACK = {'localhost', '127.0.0.1', '::1'}
ADDRESS = re.compile(r'^[^@\s<>,;:"\\]+@[^@\s<>,;:"\\]+\.[^@\s<>,;:"\\]+$')
RESERVED_TLDS = {'test', 'invalid', 'localhost', 'local', 'example'}


class Report:
    def __init__(self):
        self.owner = self.console = self.optional = self.attention = 0

    def ok(self, text):
        print(f'OK    {text}')

    def owner_data(self, text, *variables):
        self.owner += 1
        print(f'BLOCKED/OWNER-DATA {text}' + (f' [{", ".join(variables)}]' if variables else ''))

    def console_data(self, text, *variables):
        """
        Lo debe el propietario igual, pero no impide arrancar: se escribe después
        en Configuración › Integraciones (o en este archivo, en esas variables).
        """
        self.console += 1
        print(f'BLOCKED/OWNER-DATA {text}' + (f' [{", ".join(variables)}]' if variables else ''))

    def optional_data(self, text, *variables):
        self.optional += 1
        print(f'BLOCKED/OPTIONAL {text}' + (f' [{", ".join(variables)}]' if variables else ''))

    def bad(self, text):
        self.attention += 1
        print(f'ATENCIÓN {text}')


# --- el archivo, leído como lo lee Compose ---------------------------------------

def read_env(path):
    """
    `(valores, entrecomillados)`. Como Docker Compose: `export` delante se
    ignora; un valor sin comillas termina donde empieza un comentario (` #`);
    entre comillas se toma tal cual. `entrecomillados` guarda con qué comilla
    venía cada valor: dentro de comillas simples Compose no sustituye `$NOMBRE`.
    """
    values, quotes = {}, {}
    with open(path, encoding='utf-8') as handle:
        for raw in handle:
            line = raw.strip()
            if line.startswith('export '):
                line = line[len('export '):].lstrip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, _, value = line.partition('=')
            key, value = key.strip(), value.strip()
            quote = ''
            if value[:1] in ('"', "'") and value.find(value[0], 1) > 0:
                quote = value[0]
                value = value[1:value.find(quote, 1)]
            else:
                if value.startswith('#'):
                    value = ''
                value = re.split(r'\s+#', value, maxsplit=1)[0].strip()
            values[key], quotes[key] = value, quote
    return values, quotes


def flag(values, name, default=False):
    """Un interruptor, como lo lee Django: ausente es el valor por omisión; presente y vacío es «no»."""
    if name not in values:
        return default
    return values[name].strip().lower() in TRUE


def number(values, name, default):
    """El entero de `name`, `default` si no está, o None si no es un número."""
    raw = values.get(name)
    if raw is None:
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return None


def missing(values, names):
    return [name for name in names if not values.get(name, '').strip()]


# --- el archivo mismo -----------------------------------------------------------

def check_file(report, path, values, quotes):
    mode = stat.S_IMODE(os.stat(path).st_mode)
    if mode & 0o077:
        report.bad(f'el archivo de variables lo pueden leer otros usuarios del servidor (chmod 600 {path})')
    else:
        report.ok('el archivo de variables sólo lo lee su dueño')
    try:
        tracked = subprocess.run(['git', 'ls-files', '--error-unmatch', path],
                                 capture_output=True, text=True, timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        tracked = False
    if tracked:
        report.bad('el archivo de variables está versionado en Git: sus secretos ya no lo son')
    for name in SECRET_NAMES:
        if '$' in values.get(name, '') and quotes.get(name) != "'":
            report.bad(f'{name} contiene un signo «$»: Docker Compose lo sustituiría y la tienda recibiría otro valor. '
                       "Escríbelo entre comillas simples")


# --- dominio y secretos ---------------------------------------------------------

def check_site(report, values):
    domain = values.get('SITE_DOMAIN', '').strip()
    if not domain:
        report.owner_data('dominio de la tienda', 'SITE_DOMAIN')
        return ''
    if (not HOSTNAME.match(domain) or domain.lower().startswith('www.')
            or domain.rsplit('.', 1)[-1].lower() in RESERVED_TLDS):
        # Sin repetirlo: lo que hay en esa línea no tiene forma de dominio, así que puede ser cualquier cosa.
        report.bad('SITE_DOMAIN tiene que ser el dominio público, sin https://, sin www, sin puerto y sin espacios')
        return ''
    report.ok(f'dominio {domain}')
    return domain


def check_secrets(report, values):
    key = values.get('SECRET_KEY', '')
    if not key:
        report.owner_data('SECRET_KEY: se genera en el servidor (docs/despliegue-produccion.md §4.2)', 'SECRET_KEY')
    elif key in INSECURE or len(key) < 50 or len(set(key)) < 10:
        report.bad('SECRET_KEY es corta o es la de desarrollo: genera una de 64 caracteres al azar')
    else:
        report.ok('SECRET_KEY')
    password = values.get('POSTGRES_PASSWORD', '')
    if not password:
        report.owner_data('POSTGRES_PASSWORD: se genera en el servidor (§4.2)', 'POSTGRES_PASSWORD')
    elif password in INSECURE or len(password) < 20:
        report.bad('POSTGRES_PASSWORD es corta o es la de desarrollo: genera una de 32 caracteres al azar')
    elif not re.fullmatch(r'[A-Za-z0-9_\-]+', password):
        report.bad('POSTGRES_PASSWORD lleva caracteres que rompen la dirección de la base de datos (viaja dentro de '
                   'una URL): genera una con `secrets.token_urlsafe(32)` (§4.2)')
    else:
        report.ok('POSTGRES_PASSWORD')
    check_root_key(report, values)


def _is_root_key(text) -> bool:
    """La forma de una clave de Fernet: 32 bytes al azar en base64 para URL (44 caracteres)."""
    try:
        return len(text) == 44 and len(base64.urlsafe_b64decode(text.encode('ascii'))) == 32
    except (ValueError, UnicodeError):
        return False


def check_root_key(report, values):
    """
    La clave que cifra lo que se guarda en Configuración › Integraciones. Es del
    despliegue: no está en la base de datos y no se administra desde el panel.
    """
    key = values.get('APP_CONFIG_ENCRYPTION_KEY', '').strip()
    if not key:
        report.owner_data('APP_CONFIG_ENCRYPTION_KEY: la clave raíz del almacén de secretos; se genera en el servidor '
                          '(docs/integraciones-y-secretos.md)', 'APP_CONFIG_ENCRYPTION_KEY')
    elif not _is_root_key(key):
        report.bad('APP_CONFIG_ENCRYPTION_KEY no tiene la forma de una clave: genera una como dice el archivo de ejemplo '
                   '(una sola, sin comas ni espacios)')
    else:
        report.ok('APP_CONFIG_ENCRYPTION_KEY (clave raíz del almacén de secretos)')
    previous = [item.strip() for item in values.get('APP_CONFIG_ENCRYPTION_KEYS_PREVIOUS', '').split(',') if item.strip()]
    if previous and not all(_is_root_key(item) for item in previous):
        report.bad('APP_CONFIG_ENCRYPTION_KEYS_PREVIOUS: alguna de las claves anteriores no tiene la forma de una clave '
                   '(van separadas por comas)')
    elif previous:
        report.ok(f'{len(previous)} clave(s) raíz anterior(es): quítalas tras `manage.py reseal_integration_secrets`')


# --- correo ---------------------------------------------------------------------

def check_mail(report, values):
    """True si la configuración del correo es coherente y se puede hablar con el servidor."""
    backend = values.get('EMAIL_BACKEND', '').strip()
    absent = missing(values, MAIL_KEYS)
    if backend and backend != SMTP_BACKEND:
        report.bad('EMAIL_BACKEND no es SMTP: los correos, con sus enlaces de un solo uso, '
                   'irían al registro del servidor y no a su destinatario')
        return False
    if not backend and len(absent) == len(MAIL_KEYS):
        # Nada del correo está aquí: se escribirá en la consola. El backend arranca
        # sin él; hasta entonces no sale ningún mensaje y `ops_status` lo dice.
        report.console_data('correo saliente (SMTP): servidor, usuario, contraseña y remitente. Se escriben tras '
                            'arrancar en Configuración › Integraciones › Correo, o en este archivo',
                            'EMAIL_BACKEND', *absent)
        _check_order_address(report, values)
        return False
    if not backend or absent:
        report.owner_data('correo saliente (SMTP): servidor, usuario, contraseña y remitente. A medio escribir no '
                          'sirve: complétalo, o quita estas líneas y configúralo en Configuración › Integraciones',
                          *(['EMAIL_BACKEND'] if not backend else []), *absent)
        return False

    host = values['EMAIL_HOST'].strip()
    port = number(values, 'EMAIL_PORT', 587)
    timeout = number(values, 'EMAIL_TIMEOUT', 10)
    use_ssl = flag(values, 'EMAIL_USE_SSL')
    use_tls = flag(values, 'EMAIL_USE_TLS', default=not use_ssl)
    local = host.lower() in LOOPBACK
    coherent = True
    if not local and not HOSTNAME.match(host):
        report.bad('EMAIL_HOST tiene que ser el nombre del servidor de correo, sin esquema, usuario ni puerto '
                   '(el usuario y la contraseña van en EMAIL_HOST_USER y EMAIL_HOST_PASSWORD)')
        coherent = False
    if port is None or not 1 <= port <= 65535:
        report.bad('EMAIL_PORT tiene que ser un número de puerto (587 o 465)')
        coherent = False
    if timeout is None or timeout <= 0:
        report.bad('EMAIL_TIMEOUT tiene que ser un número de segundos mayor que cero')
        coherent = False
    if use_ssl and use_tls:
        report.bad('EMAIL_USE_SSL y EMAIL_USE_TLS a la vez: es uno u otro')
        coherent = False
    elif port == 465 and not use_ssl:
        report.bad('el puerto 465 cifra desde el primer byte: pide EMAIL_USE_SSL=1 y EMAIL_USE_TLS=0')
        coherent = False
    elif not use_ssl and not use_tls and not local:
        report.bad('correo sin cifrar: la contraseña del correo viajaría en claro '
                   '(EMAIL_USE_TLS=1, o EMAIL_USE_SSL=1 en el 465; una línea vacía cuenta como «no»)')
        coherent = False
    if not ADDRESS.match(values['DEFAULT_FROM_EMAIL'].strip()):
        report.bad('DEFAULT_FROM_EMAIL tiene que ser una dirección de correo')
        coherent = False
    if coherent:
        kind = 'TLS implícito' if use_ssl else 'STARTTLS' if use_tls else 'sin cifrar, en esta misma máquina'
        report.ok(f'correo saliente por {host}:{port} ({kind})')
    _check_order_address(report, values)
    return coherent


def _check_order_address(report, values):
    if not values.get('ORDER_NOTIFICATION_EMAIL', '').strip():
        report.owner_data('dirección que recibe el aviso de cada pedido pagado', 'ORDER_NOTIFICATION_EMAIL')


def _smtp_session(values):
    host = values['EMAIL_HOST'].strip()
    port = number(values, 'EMAIL_PORT', 587)
    timeout = number(values, 'EMAIL_TIMEOUT', 10)
    use_ssl = flag(values, 'EMAIL_USE_SSL')
    if use_ssl:
        server = smtplib.SMTP_SSL(host, port, timeout=timeout, context=ssl.create_default_context())
    else:
        server = smtplib.SMTP(host, port, timeout=timeout)
    server.ehlo()
    if not use_ssl and flag(values, 'EMAIL_USE_TLS', default=True):
        server.starttls(context=ssl.create_default_context())
        server.ehlo()
    if values.get('EMAIL_HOST_USER', '').strip():
        server.login(values['EMAIL_HOST_USER'].strip(), values['EMAIL_HOST_PASSWORD'])
    return server


def check_smtp_live(report, values, domain, send_to=None):
    """
    Entra al servidor de correo, por el mismo camino cifrado que usará la
    tienda. Un fallo se dice con la clase de error y el código del servidor,
    nunca con su texto: hay servidores que repiten el usuario en su respuesta,
    y hay errores que citan un trozo de la contraseña.
    """
    try:
        server = _smtp_session(values)
    except smtplib.SMTPAuthenticationError as exc:
        report.bad(f'el servidor de correo rechazó las credenciales (código {exc.smtp_code})')
        return
    except (smtplib.SMTPException, OSError, ssl.SSLError) as exc:
        code = getattr(exc, 'smtp_code', '')
        report.bad(f'no se pudo conectar con el servidor de correo ({type(exc).__name__}{f", código {code}" if code else ""})')
        return
    except UnicodeError:
        report.bad('la contraseña o el usuario del correo llevan caracteres que el protocolo no admite '
                   '(letras con tilde, eñes): pide al proveedor una contraseña de aplicación sin ellos')
        return
    try:
        report.ok('el servidor de correo acepta la conexión y las credenciales')
        if send_to:
            message = EmailMessage()
            message['From'] = values['DEFAULT_FROM_EMAIL'].strip()
            message['To'] = send_to
            message['Subject'] = f'Prueba de correo de {domain or "la tienda"}'
            message.set_content(
                f'Este mensaje comprueba que {domain or "la tienda"} puede enviar correo.\n'
                'No contiene ningún enlace ni requiere ninguna acción.\n')
            try:
                refused = server.send_message(message)
            except (smtplib.SMTPException, OSError) as exc:
                report.bad(f'el servidor de correo no aceptó el mensaje de prueba ({type(exc).__name__})')
            else:
                if refused:
                    report.bad('el servidor de correo rechazó al destinatario del mensaje de prueba')
                else:
                    report.ok('mensaje de prueba entregado al servidor de correo; comprueba que llega al buzón')
    finally:
        try:
            server.quit()
        except (smtplib.SMTPException, OSError):
            pass


# --- pagos ----------------------------------------------------------------------

def check_payments(report, values, domain):
    # Ausente, la aplicación toma «izipay». Presente, tiene que ser uno de los dos.
    provider = values.get('PAYMENT_PROVIDER', 'izipay').strip().lower()
    izipay_missing = missing(values, IZIPAY_KEYS)
    mcw_missing = missing(values, MICUENTAWEB_KEYS)
    has_izipay = len(izipay_missing) < len(IZIPAY_KEYS)
    has_mcw = len(missing(values, MICUENTAWEB_ANY)) < len(MICUENTAWEB_ANY)

    if has_izipay and has_mcw:
        report.bad('hay claves de los dos productos de Izipay: una instalación usa uno. '
                   'Deja vacías las del que no sea PAYMENT_PROVIDER')
        return
    if not has_izipay and not has_mcw:
        report.console_data(
            'Izipay: cuál de los dos productos tiene contratado («SDK web / Checkout» = izipay, '
            '«Mi Cuenta Web» = micuentaweb) y sus claves de TEST. Se escriben tras arrancar en '
            'Configuración › Integraciones › Pagos, o en este archivo',
            'PAYMENT_PROVIDER', 'IZIPAY_* o MICUENTAWEB_*')
        return
    if provider not in ('izipay', 'micuentaweb'):
        report.bad('PAYMENT_PROVIDER tiene que ser izipay o micuentaweb')
        return
    if (provider == 'izipay') != has_izipay:
        report.bad('PAYMENT_PROVIDER nombra un producto de Izipay y las claves que hay son del otro '
                   '(sin esa línea, la aplicación toma izipay)')
        return

    if provider == 'izipay':
        if izipay_missing:
            report.owner_data('Izipay «SDK web / Checkout»: faltan claves', *izipay_missing)
            return
        environment = values.get('IZIPAY_ENV', 'sandbox').strip().lower()
        url = values['IZIPAY_TOKEN_URL'].strip()
        coherent = True
        if environment not in ('sandbox', 'production'):
            report.bad('IZIPAY_ENV tiene que decir sandbox o production: no se deduce de nada')
            coherent = False
        if not url.startswith('https://'):
            report.bad('IZIPAY_TOKEN_URL tiene que ser https')
            coherent = False
        elif environment == 'production' and 'sandbox' in url.lower():
            report.bad('IZIPAY_ENV=production con un IZIPAY_TOKEN_URL de sandbox')
            coherent = False
        coherent &= _check_ipn(report, values, 'IZIPAY_IPN_URL', domain, 'izipay')
        if coherent:
            report.ok(f'Izipay «SDK web / Checkout», entorno {environment}')
        return

    if mcw_missing:
        report.owner_data('Izipay «Mi Cuenta Web»: faltan claves', *mcw_missing)
        return
    shop = values['MICUENTAWEB_SHOP_ID'].strip()
    password = values['MICUENTAWEB_PASSWORD'].strip()
    owner, separator, public = values['MICUENTAWEB_PUBLIC_KEY'].strip().partition(':')
    environment = 'TEST' if password.startswith('testpassword_') else 'PRODUCCIÓN' if password.startswith('prodpassword_') else ''
    coherent = True
    if not environment:
        report.bad('MICUENTAWEB_PASSWORD no es la contraseña de TEST (testpassword_…) ni la de PRODUCCIÓN (prodpassword_…)')
        coherent = False
    if not separator or not public:
        report.bad('MICUENTAWEB_PUBLIC_KEY tiene la forma <usuario>:<clave pública>')
        coherent = False
    else:
        if owner != shop:
            report.bad('la clave pública de Mi Cuenta Web es de otra tienda: su usuario no es MICUENTAWEB_SHOP_ID')
            coherent = False
        if environment and public.startswith('testpublickey_') != (environment == 'TEST'):
            report.bad('las claves de Mi Cuenta Web mezclan TEST y PRODUCCIÓN: copia las del mismo entorno')
            coherent = False
    if not values.get('MICUENTAWEB_API_URL', 'https://api.micuentaweb.pe').strip().startswith('https://'):
        report.bad('MICUENTAWEB_API_URL tiene que ser https')
        coherent = False
    coherent &= _check_ipn(report, values, 'MICUENTAWEB_IPN_URL', domain, 'micuentaweb')
    if coherent:
        report.ok(f'Izipay «Mi Cuenta Web», claves de {environment}')


def _check_ipn(report, values, name, domain, provider):
    url = values.get(name, '').strip()
    if not url or not domain:
        return True
    expected = f'https://{domain}/api/payments/{provider}/notification/'
    if url.rstrip('/') != expected.rstrip('/'):
        report.bad(f'{name}: la dirección de notificación tiene que ser {expected}')
        return False
    return True


# --- opcionales -----------------------------------------------------------------

def check_optional(report, values):
    client = values.get('GOOGLE_OAUTH_CLIENT_ID', '').strip()
    if not client:
        report.optional_data('«Continuar con Google»: ID de cliente OAuth con el dominio autorizado', 'GOOGLE_OAUTH_CLIENT_ID')
    elif not client.endswith('.apps.googleusercontent.com'):
        report.bad('GOOGLE_OAUTH_CLIENT_ID no tiene la forma de un ID de cliente de Google (….apps.googleusercontent.com)')
    else:
        report.ok('«Continuar con Google»')

    # Ausente, la aplicación toma el proveedor real («cloud_api»): no es «apagado».
    provider = values.get('WHATSAPP_PROVIDER', 'cloud_api').strip().lower()
    names = {key.split('_', 2)[1] for key, value in values.items()
             if key.startswith(('WHATSAPP_TOKEN_', 'WHATSAPP_SECRET_', 'WHATSAPP_VERIFY_')) and value.strip()}
    owed = ('WhatsApp: número, plantillas aprobadas, token, secreto de la aplicación y token de verificación de cada '
            'empresa. Se escriben en Configuración › Integraciones › WhatsApp Business')
    if provider == 'disabled':
        report.optional_data(owed + '. Hoy está apagado en este servidor: con WHATSAPP_PROVIDER=disabled ninguna '
                                    'empresa envía aunque esté configurada', 'WHATSAPP_PROVIDER')
    elif provider != 'cloud_api':
        report.bad('WHATSAPP_PROVIDER tiene que ser cloud_api o disabled en producción (una línea vacía no es ninguno de los dos)')
    elif not names:
        # Encendido y sin variables: cada empresa se configura en la consola.
        report.optional_data(owed)
    elif names != {'TOKEN', 'SECRET', 'VERIFY'}:
        report.bad('WhatsApp con variables WHATSAPP_… a medias: el esquema anterior necesita las tres (WHATSAPP_TOKEN_…, '
                   'WHATSAPP_SECRET_…, WHATSAPP_VERIFY_…). Complétalas, o quítalas y configura la empresa en la consola')
    else:
        report.ok('WhatsApp: credenciales presentes en el entorno (enlázalas con `manage.py configure_whatsapp`, '
                  'o pásalas a la consola)')

    if flag(values, 'FISCAL_ENABLED'):
        report.bad('FISCAL_ENABLED está encendido: emitir a SUNAT es una fase aparte, con certificado y credenciales SOL. '
                   'Para la primera publicación va en 0')
    else:
        report.optional_data('facturación electrónica (SUNAT): apagada; encenderla necesita certificado y credenciales SOL, '
                             'que se escriben en Configuración › Integraciones › SUNAT',
                             'FISCAL_ENABLED')

    if not values.get('NEXT_PUBLIC_IMAGE_HOSTS', '').strip():
        report.ok('fotos de producto: sólo las subidas desde el panel (ningún host externo autorizado)')


# --- DNS ------------------------------------------------------------------------

def check_dns(report, domain, server_ip):
    if not domain:
        return
    for name in (domain, f'www.{domain}'):
        try:
            addresses = sorted({info[4][0] for info in socket.getaddrinfo(name, 443, proto=socket.IPPROTO_TCP)})
        except (OSError, UnicodeError):
            report.owner_data(f'DNS: {name} no resuelve todavía')
            continue
        if server_ip and server_ip not in addresses:
            report.bad(f'DNS: {name} apunta a {", ".join(addresses)}, no a la dirección del servidor')
        else:
            report.ok(f'DNS: {name} → {", ".join(addresses)}')


def run(args):
    report = Report()
    if not os.path.isfile(args.env):
        print(f'BLOCKED/OWNER-DATA no existe {args.env}. Créalo así y rellénalo:')
        print('    cp deploy/.env.production.example deploy/.env.production && chmod 600 deploy/.env.production')
        print('CONFIGURACIÓN: INCOMPLETA')
        return 1
    if args.smtp_send_to and not ADDRESS.match(args.smtp_send_to):
        print('ATENCIÓN --smtp-send-to tiene que ser una sola dirección de correo')
        print('CONFIGURACIÓN: INCOMPLETA')
        return 1
    try:
        values, quotes = read_env(args.env)
    except UnicodeError:
        print('ATENCIÓN el archivo de variables no es texto UTF-8: guárdalo como texto plano, sin formato')
        print('CONFIGURACIÓN: INCOMPLETA')
        return 1

    check_file(report, args.env, values, quotes)
    domain = check_site(report, values)
    check_secrets(report, values)
    mail = check_mail(report, values)
    if args.smtp or args.smtp_send_to:
        if mail:
            check_smtp_live(report, values, domain, args.smtp_send_to)
        else:
            print('      no se prueba el servidor de correo hasta que su configuración esté completa y sea segura')
    check_payments(report, values, domain)
    check_optional(report, values)
    if args.dns:
        check_dns(report, domain, args.server_ip)

    print()
    if report.owner or report.attention:
        print(f'CONFIGURACIÓN: INCOMPLETA — {report.owner} dato(s) del propietario, {report.attention} por corregir, '
              f'{report.optional} opcional(es)')
    elif report.console:
        print(f'CONFIGURACIÓN: SUFICIENTE PARA ARRANCAR — {report.console} dato(s) del propietario por escribir en la '
              f'consola (Configuración › Integraciones) antes de abrir la tienda, {report.optional} opcional(es)')
    elif report.optional:
        print(f'CONFIGURACIÓN: COMPLETA SALVO OPCIONALES — {report.optional} opcional(es) sin configurar')
    else:
        print('CONFIGURACIÓN: COMPLETA')
    print('Esto comprueba el archivo de variables. No sustituye al pago de prueba en el entorno TEST de la '
          'pasarela ni a `sh deploy/rehearsal.sh`.')
    return 1 if (report.owner or report.attention) else 0


def main():
    parser = argparse.ArgumentParser(description='Qué falta para publicar. No imprime ningún valor secreto.')
    parser.add_argument('--env', default='deploy/.env.production')
    parser.add_argument('--smtp', action='store_true', help='entra al servidor de correo; no envía nada')
    parser.add_argument('--smtp-send-to', metavar='DIRECCIÓN', help='entrega un mensaje de prueba a esa dirección')
    parser.add_argument('--dns', action='store_true', help='pregunta a qué dirección apunta el dominio')
    parser.add_argument('--server-ip', help='la dirección del servidor, para compararla con el DNS')
    args = parser.parse_args()
    try:
        return run(args)
    except Exception as exc:  # noqa: BLE001 — la traza de un error citaría el valor que lo provocó
        print(f'ATENCIÓN error interno al comprobar el archivo ({type(exc).__name__}). No se muestra el detalle '
              'porque podría contener un valor del archivo')
        print('CONFIGURACIÓN: INCOMPLETA')
        return 1


if __name__ == '__main__':
    sys.exit(main())
