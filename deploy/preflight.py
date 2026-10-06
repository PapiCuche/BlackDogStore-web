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
secretos de producción. Dice nombres de variables, nunca su contenido.

Comprueba la CONFIGURACIÓN. No sustituye al pago de prueba en el entorno TEST de
la pasarela (docs/pagos-equipos-documentos.md §1.4) ni al ensayo
(`sh deploy/rehearsal.sh`).

Sólo la biblioteca estándar. Termina con 0 si no hay nada que el propietario
deba todavía ni nada que corregir, y con 1 en otro caso.
"""
import argparse
import os
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
MICUENTAWEB_KEYS = ('MICUENTAWEB_SHOP_ID', 'MICUENTAWEB_PASSWORD', 'MICUENTAWEB_PUBLIC_KEY', 'MICUENTAWEB_HMAC_KEY')
MAIL_KEYS = ('EMAIL_HOST', 'EMAIL_HOST_USER', 'EMAIL_HOST_PASSWORD', 'DEFAULT_FROM_EMAIL')
INSECURE = {'', 'changeme', 'changeme-dev-only', 'changeme-replace-in-production', 'postgres', 'password', 'blackdog'}
SMTP_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
TRUE = {'1', 'true', 'yes', 'on'}


class Report:
    def __init__(self):
        self.owner = self.optional = self.attention = 0

    def ok(self, text):
        print(f'OK    {text}')

    def owner_data(self, text, *variables):
        self.owner += 1
        print(f'BLOCKED/OWNER-DATA {text}' + (f' [{", ".join(variables)}]' if variables else ''))

    def optional_data(self, text, *variables):
        self.optional += 1
        print(f'BLOCKED/OPTIONAL {text}' + (f' [{", ".join(variables)}]' if variables else ''))

    def bad(self, text):
        self.attention += 1
        print(f'ATENCIÓN {text}')


def read_env(path):
    values = {}
    with open(path, encoding='utf-8') as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, _, value = line.partition('=')
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in '"\'':
                value = value[1:-1]
            values[key.strip()] = value
    return values


def flag(values, name, default=False):
    raw = values.get(name, '')
    return raw.strip().lower() in TRUE if raw.strip() else default


def missing(values, names):
    return [name for name in names if not values.get(name, '').strip()]


# --- el archivo mismo -----------------------------------------------------------

def check_file(report, path):
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


# --- dominio y secretos ---------------------------------------------------------

def check_site(report, values):
    domain = values.get('SITE_DOMAIN', '').strip()
    if not domain:
        report.owner_data('dominio de la tienda', 'SITE_DOMAIN')
        return ''
    labels = domain.split('.')
    if ('/' in domain or ':' in domain or domain.startswith('www.') or len(labels) < 2
            or labels[-1] in ('test', 'invalid', 'localhost', 'local', 'example') or domain == 'localhost'):
        report.bad('SITE_DOMAIN tiene que ser el dominio público, sin https://, sin www y sin puerto')
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
    else:
        report.ok('POSTGRES_PASSWORD')


# --- correo ---------------------------------------------------------------------

def check_mail(report, values):
    """True si hay un servidor de correo con el que se pueda hablar."""
    backend = values.get('EMAIL_BACKEND', '').strip()
    absent = missing(values, MAIL_KEYS)
    if backend and backend != SMTP_BACKEND:
        report.bad('EMAIL_BACKEND no es SMTP: los correos, con sus enlaces de un solo uso, '
                   'irían al registro del servidor y no a su destinatario')
        return False
    if not backend or absent:
        report.owner_data('correo saliente (SMTP): servidor, usuario, contraseña y remitente',
                          *(['EMAIL_BACKEND'] if not backend else []), *absent)
        return False
    port = values.get('EMAIL_PORT', '587').strip() or '587'
    use_ssl = flag(values, 'EMAIL_USE_SSL')
    use_tls = flag(values, 'EMAIL_USE_TLS', default=not use_ssl)
    coherent = True
    if use_ssl and use_tls:
        report.bad('EMAIL_USE_SSL y EMAIL_USE_TLS a la vez: es uno u otro')
        coherent = False
    elif port == '465' and not use_ssl:
        report.bad('el puerto 465 cifra desde el primer byte: pide EMAIL_USE_SSL=1 y EMAIL_USE_TLS=0')
        coherent = False
    elif not use_ssl and not use_tls:
        report.bad('correo sin cifrar: la contraseña del correo viajaría en claro (EMAIL_USE_TLS=1, o EMAIL_USE_SSL=1 en el 465)')
        coherent = False
    if '@' not in values.get('DEFAULT_FROM_EMAIL', ''):
        report.bad('DEFAULT_FROM_EMAIL tiene que ser una dirección de correo')
        coherent = False
    if coherent:
        report.ok(f"correo saliente por {values['EMAIL_HOST'].strip()}:{port} "
                  f"({'TLS implícito' if use_ssl else 'STARTTLS'})")
    if not values.get('ORDER_NOTIFICATION_EMAIL', '').strip():
        report.owner_data('dirección que recibe el aviso de cada pedido pagado', 'ORDER_NOTIFICATION_EMAIL')
    return True


def _smtp_session(values):
    host = values['EMAIL_HOST'].strip()
    port = int(values.get('EMAIL_PORT', '587').strip() or 587)
    timeout = int(values.get('EMAIL_TIMEOUT', '10').strip() or 10)
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
    Entra al servidor de correo. Un fallo se dice con la clase de error y el
    código del servidor, nunca con el texto que el servidor devuelve ni con las
    credenciales: hay servidores que repiten el usuario en su respuesta.
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
            except smtplib.SMTPException as exc:
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
    provider = values.get('PAYMENT_PROVIDER', '').strip().lower()
    izipay_missing = missing(values, IZIPAY_KEYS)
    mcw_missing = missing(values, MICUENTAWEB_KEYS)
    has_izipay = len(izipay_missing) < len(IZIPAY_KEYS)
    has_mcw = len(mcw_missing) < len(MICUENTAWEB_KEYS)

    if has_izipay and has_mcw:
        report.bad('hay claves de los dos productos de Izipay: una instalación usa uno. '
                   'Deja vacías las del que no sea PAYMENT_PROVIDER')
        return
    if not has_izipay and not has_mcw:
        report.owner_data(
            'Izipay: cuál de los dos productos tiene contratado («SDK web / Checkout» = izipay, '
            '«Mi Cuenta Web» = micuentaweb) y sus claves de TEST',
            'PAYMENT_PROVIDER', 'IZIPAY_* o MICUENTAWEB_*')
        return
    if provider not in ('izipay', 'micuentaweb'):
        report.bad('PAYMENT_PROVIDER tiene que ser izipay o micuentaweb')
        return
    if (provider == 'izipay') != has_izipay:
        report.bad(f'PAYMENT_PROVIDER={provider}, pero las claves que hay son del otro producto de Izipay')
        return

    if provider == 'izipay':
        if izipay_missing:
            report.owner_data('Izipay «SDK web / Checkout»: faltan claves', *izipay_missing)
            return
        environment = values.get('IZIPAY_ENV', '').strip().lower()
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
            report.bad('las claves de Mi Cuenta Web mezclan TEST y PRODUCCIÓN: copia las cuatro del mismo entorno')
            coherent = False
    if not (values.get('MICUENTAWEB_API_URL', '').strip() or 'https://').startswith('https://'):
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

    provider = values.get('WHATSAPP_PROVIDER', '').strip().lower()
    names = {key.split('_', 2)[1] for key, value in values.items()
             if key.startswith(('WHATSAPP_TOKEN_', 'WHATSAPP_SECRET_', 'WHATSAPP_VERIFY_')) and value.strip()}
    if provider in ('', 'disabled'):
        report.optional_data('WhatsApp: número, plantillas aprobadas, token, secreto de la aplicación y token de verificación',
                             'WHATSAPP_PROVIDER', 'WHATSAPP_TOKEN_<EMPRESA>', 'WHATSAPP_SECRET_<EMPRESA>', 'WHATSAPP_VERIFY_<EMPRESA>')
    elif provider != 'cloud_api':
        report.bad('WHATSAPP_PROVIDER tiene que ser cloud_api o disabled en producción')
    elif names != {'TOKEN', 'SECRET', 'VERIFY'}:
        report.bad('WhatsApp encendido sin sus tres credenciales (WHATSAPP_TOKEN_…, WHATSAPP_SECRET_…, WHATSAPP_VERIFY_…): '
                   'ponlas o deja WHATSAPP_PROVIDER=disabled')
    else:
        report.ok('WhatsApp: credenciales presentes (enlázalas con `manage.py configure_whatsapp`)')

    if flag(values, 'FISCAL_ENABLED'):
        report.bad('FISCAL_ENABLED=1: emitir a SUNAT es una fase aparte, con certificado y credenciales SOL. '
                   'Para la primera publicación va en 0')
    else:
        report.optional_data('facturación electrónica (SUNAT): apagada; encenderla necesita certificado y credenciales SOL',
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
        except OSError:
            report.owner_data(f'DNS: {name} no resuelve todavía')
            continue
        if server_ip and server_ip not in addresses:
            report.bad(f'DNS: {name} apunta a {", ".join(addresses)}, no a {server_ip}')
        else:
            report.ok(f'DNS: {name} → {", ".join(addresses)}')


def main():
    parser = argparse.ArgumentParser(description='Qué falta para publicar. No imprime ningún valor secreto.')
    parser.add_argument('--env', default='deploy/.env.production')
    parser.add_argument('--smtp', action='store_true', help='entra al servidor de correo; no envía nada')
    parser.add_argument('--smtp-send-to', metavar='DIRECCIÓN', help='entrega un mensaje de prueba a esa dirección')
    parser.add_argument('--dns', action='store_true', help='pregunta a qué dirección apunta el dominio')
    parser.add_argument('--server-ip', help='la dirección del servidor, para compararla con el DNS')
    args = parser.parse_args()

    report = Report()
    if not os.path.isfile(args.env):
        print(f'BLOCKED/OWNER-DATA no existe {args.env}. Créalo así y rellénalo:')
        print('    cp deploy/.env.production.example deploy/.env.production && chmod 600 deploy/.env.production')
        print('CONFIGURACIÓN: INCOMPLETA')
        return 1

    values = read_env(args.env)
    check_file(report, args.env)
    domain = check_site(report, values)
    check_secrets(report, values)
    mail = check_mail(report, values)
    if mail and (args.smtp or args.smtp_send_to):
        check_smtp_live(report, values, domain, args.smtp_send_to)
    check_payments(report, values, domain)
    check_optional(report, values)
    if args.dns:
        check_dns(report, domain, args.server_ip)

    print()
    if report.owner or report.attention:
        print(f'CONFIGURACIÓN: INCOMPLETA — {report.owner} dato(s) del propietario, {report.attention} por corregir, '
              f'{report.optional} opcional(es)')
    elif report.optional:
        print(f'CONFIGURACIÓN: COMPLETA SALVO OPCIONALES — {report.optional} opcional(es) sin configurar')
    else:
        print('CONFIGURACIÓN: COMPLETA')
    print('Esto comprueba el archivo de variables. No sustituye al pago de prueba en el entorno TEST de la '
          'pasarela ni a `sh deploy/rehearsal.sh`.')
    return 1 if (report.owner or report.attention) else 0


if __name__ == '__main__':
    sys.exit(main())
