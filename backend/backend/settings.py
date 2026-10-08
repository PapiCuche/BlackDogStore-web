from datetime import timedelta
from pathlib import Path
import os
import environ
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

# django-environ — DEBUG DEFAULTS TO FALSE, and that default is a security
# control, not a preference (ERP-1 · DEBT-01). Every production fail-safe below
# hangs on `if not DEBUG`: the real SECRET_KEY, ALLOWED_HOSTS, HTTPS/HSTS,
# Secure cookies, CORS. When DEBUG defaulted to True, a deployment that merely
# FORGOT to set the variable came up as a developer's machine — signing its JWTs
# with the public 'changeme-dev-only' key, hosts wide open, demo accounts live.
# Failing closed is the safe direction: local dev and CI set DEBUG explicitly in
# their .env, so this changes nothing for them and everything for a misconfigured
# server, which now refuses to start rather than start insecure.
env = environ.Env(
    DEBUG=(bool, False),
)
environ.Env.read_env(os.path.join(BASE_DIR, '.env'))

DEBUG = env('DEBUG')

# SECRET_KEY — must be real in production
SECRET_KEY = env('SECRET_KEY', default='changeme-dev-only')
_INSECURE_KEYS = {'changeme', 'changeme-dev-only', 'changeme-replace-in-production', ''}
if not DEBUG and SECRET_KEY in _INSECURE_KEYS:
    raise ImproperlyConfigured(
        "SECRET_KEY must be set to a real value in production. "
        "Generate one with:\n"
        "  python -c \"from django.core.management.utils import "
        "get_random_secret_key; print(get_random_secret_key())\""
    )

# ALLOWED_HOSTS — safe defaults for dev; production must set this explicitly
if DEBUG:
    ALLOWED_HOSTS = env.list('ALLOWED_HOSTS', default=['localhost', '127.0.0.1', '0.0.0.0'])
else:
    ALLOWED_HOSTS = env.list('ALLOWED_HOSTS', default=[])
    if not ALLOWED_HOSTS:
        raise ImproperlyConfigured(
            "ALLOWED_HOSTS must be set in production (e.g. ALLOWED_HOSTS=yourdomain.com)"
        )

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'rest_framework_simplejwt.token_blacklist',
    'corsheaders',
    'store.apps.StoreConfig',
]

# ---------------------------------------------------------------------------
# Trusted proxy boundary — Phase 0.3 / P0-B
# ---------------------------------------------------------------------------
#
# How many proxies in front of this process may be believed when they say who
# the caller is. See `store/client_ip.py` for the full reasoning.
#
#   0  (default)  Ignore X-Forwarded-For entirely; the client is REMOTE_ADDR.
#   N  > 0        EXACTLY N proxies append to X-Forwarded-For and the Nth entry
#                 from the right is the client.
#
# Set this ONLY once the edge is known to strip the caller's own
# X-Forwarded-For and append the real peer. Declaring a count while the proxy
# appends nothing is worse than declaring zero: the rightmost entry is then
# whatever the caller typed, and the setting that was supposed to establish
# trust hands it to the attacker.
TRUSTED_PROXY_COUNT = env.int('TRUSTED_PROXY_COUNT', default=0)

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'store.authentication.CookieJWTAuthentication',
    ),
    # Drives DRF's BaseThrottle.get_ident(). Left unset it defaults to None,
    # which makes DRF use the WHOLE X-Forwarded-For header as the throttle
    # identity — so a client could mint a fresh rate-limit bucket per request
    # just by varying a header. Pinned to the same policy the audit log uses.
    'NUM_PROXIES': TRUSTED_PROXY_COUNT,
    'DEFAULT_PERMISSION_CLASSES': (
        'rest_framework.permissions.AllowAny',
    ),
    # SEC-SET-09. The browsable API is a developer tool: an HTML page with
    # forms for every endpoint. Production answers JSON only. A view that needs
    # another format declares its own `renderer_classes`, as before.
    'DEFAULT_RENDERER_CLASSES': (
        ('rest_framework.renderers.JSONRenderer', 'rest_framework.renderers.BrowsableAPIRenderer')
        if DEBUG else ('rest_framework.renderers.JSONRenderer',)
    ),
    # Pagination NOT enabled globally — frontend expects raw arrays.
    # Add per-ViewSet pagination in Phase 5 after updating the frontend fetchers.
    #
    # Throttle rates — individual views declare their throttle_classes.
    # Rates apply per IP for AnonRateThrottle (login, register, etc.).
    'DEFAULT_THROTTLE_RATES': {
        'login': '5/min',
        # GOOGLE-AUTH. Pedir la configuración, entrar y vincular comparten cupo.
        'google_sign_in': '20/min',
        # SEC-SET-04-A. Renovar la sesión, por dirección: web y app comparten cubo.
        'token_refresh': '30/min',
        'register': '5/min',
        'coupon': '20/min',
        'review_create': '5/min',
        'checkout': '10/min',
        # Cubo PROPIO, separado de 'checkout': cotizar es una lectura que
        # ocurre en cada cambio del carrito, y compartir presupuesto con el
        # cobro dejaba al comprador sin poder pagar por haber mirado.
        'checkout_quote': '60/min',
        # C2.2A.1. Emitir sale a la red de SUNAT; consultar no.
        'fiscal_issue': '20/min',
        'fiscal_read': '120/min',
        # H4.1. Invitar envía correo a terceros; aceptar es la superficie que
        # alguien probaría con tokens al azar.
        'staff_invite': '30/min',
        'staff_accept': '20/min',
        'cart': '60/min',
        # El agente de impresión de cada local pregunta cada pocos segundos.
        'print_agent': '240/min',
        'payment_status': '30/min',
        'resend_verification': '3/min',
        # El código de 6 dígitos: además de sus 5 intentos por código, por dirección.
        'verify_email_code': '10/min',
        'password_reset_request': '3/min',
        'password_reset_confirm': '5/min',
        'change_password': '5/min',
        'admin_users': '120/min',
        'admin_role_change': '30/min',
        'admin_audit_logs': '120/min',
        'admin_products': '120/min',
        'admin_product_write': '60/min',
        'admin_inventory_adjust': '60/min',
        'admin_categories': '120/min',
        'admin_orders': '120/min',
        'admin_order_status_change': '60/min',
        # Una galería pide una imagen por miniatura; subir varias fotos son
        # varias peticiones. Cada cosa con su cupo (EVIDENCE-THROTTLE).
        'service_evidence_read': '600/min',
        'service_evidence_write': '120/min',
        # Inspeccionar, previsualizar y aplicar una carga masiva. Cada
        # previsualización puede recodificar cientos de imágenes.
        'admin_import': '30/min',
        'admin_order_email_resend': '10/min',
        # TRACKING. Una página de seguimiento pide la orden y una imagen por foto.
        'tracking_read': '240/min',
        'tracking_write': '20/min',
        # Meta reports every sent, delivered and read message here.
        'whatsapp_webhook': '600/min',
        'account_repairs': '60/min',
        'admin_inventory_reports': '120/min',
        'admin_stock_movements': '60/min',
        'admin_sales_notes': '60/min',
        'admin_customers': '120/min',
        'admin_customer_write': '60/min',
        # A barcode scanner fires several lookups a second while an
        # operator works through a basket, so this ceiling is generous.
        'admin_pos': '600/min',
        'admin_pos_sale': '60/min',
        'admin_sales_analytics': '120/min',
    },
}

SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(minutes=30),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),
    'ROTATE_REFRESH_TOKENS': True,
    'BLACKLIST_AFTER_ROTATION': True,
    'ALGORITHM': 'HS256',
    'AUTH_HEADER_TYPES': ('Bearer',),
    'USER_ID_FIELD': 'id',
    'USER_ID_CLAIM': 'user_id',
}

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    # BODY-LIMIT. Antes que nada que pueda leer el cuerpo: rechaza por la
    # longitud declarada. La tabla vive en store/request_limits.py y el proxy
    # (deploy/Caddyfile) repite los mismos números.
    'store.request_limits.RequestBodyLimitMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'backend.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'backend.wsgi.application'

DATABASES = {
    'default': env.db_url('DATABASE_URL', default=f'sqlite:///{BASE_DIR / "db.sqlite3"}')
}

# ---------------------------------------------------------------------------
# C2.2A.1 — comprobantes de pago electrónicos
# ---------------------------------------------------------------------------
#
# APAGADO POR DEFECTO. Una instalación que no ha configurado nada fiscal no debe
# poder enviar nada a SUNAT por el hecho de tener el código instalado.
FISCAL_ENABLED = env.bool('FISCAL_ENABLED', default=False)

# EL AMBIENTE LO DECIDE EL SERVIDOR, nunca una petición. `store.fiscal_config`
# falla cerrado ante cualquier valor distinto de «beta»: producción exige un
# certificado acreditado y credenciales por empresa, y ninguna de las dos cosas
# existe todavía.
FISCAL_ENVIRONMENT = env('FISCAL_ENVIRONMENT', default='beta')

# Credenciales y certificado. NO viven en la base de datos: una columna existe
# para llenarse, y un campo `sol_password` acaba en un serializer, un volcado o
# una bitácora.
#
# Para el entorno de pruebas SUNAT publica credenciales comunes en su Manual del
# programador; no hay ningún secreto real en un despliegue de desarrollo.
FISCAL_SOL_RUC = env('FISCAL_SOL_RUC', default='')
FISCAL_SOL_USER = env('FISCAL_SOL_USER', default='')
FISCAL_SOL_PASSWORD = env('FISCAL_SOL_PASSWORD', default='')
FISCAL_CERT_PEM = env('FISCAL_CERT_PEM', default='')
FISCAL_KEY_PEM = env('FISCAL_KEY_PEM', default='')

# CDT en contenedor PKCS#12 (.p12). Alternativa al par PEM de arriba: NO se
# configuran ambos a la vez (fiscal_config falla cerrado ante configuración
# ambigua). La ruta la da el entorno; el código NO la busca en el disco ni la
# hardcodea. El contenedor se carga y convierte a PEM EN MEMORIA — nunca a /tmp.
FISCAL_CERT_P12_PATH = env('FISCAL_CERT_P12_PATH', default='')
FISCAL_CERT_P12_PASSWORD = env('FISCAL_CERT_P12_PASSWORD', default='')

# CONSULTA/RECONCILIACIÓN en línea (getStatusCdr, billConsultService). Capacidad
# SEPARADA de la emisión: `billConsultService` sólo existe en producción, así que
# consultarlo de verdad es una decisión propia. Apagada por defecto; encenderla NO
# habilita `sendBill` producción (resolutores y banderas distintos). El endpoint,
# si se configura, lo fija el servidor — nunca una petición.
FISCAL_CONSULT_ENABLED = env.bool('FISCAL_CONSULT_ENABLED', default=False)
FISCAL_CONSULT_ENDPOINT = env('FISCAL_CONSULT_ENDPOINT', default='')

# Prueba de humo BETA (comando fiscal_beta_smoke). Apagada por defecto: el comando
# habla con SUNAT de verdad y PERSISTE lo que envía. Se enciende a propósito, nunca
# en CI ni en producción.
FISCAL_BETA_SMOKE_ENABLED = env.bool('FISCAL_BETA_SMOKE_ENABLED', default=False)

# ---------------------------------------------------------------------------
# M12D — evidencias fotográficas
# ---------------------------------------------------------------------------
#
# Neutrales a propósito. Ninguno nombra a Cloudflare: el backend se elige por su
# rol —"s3" o "filesystem"—, no por su proveedor, y el día que el proveedor
# cambie sólo cambia el valor de las variables de entorno.
#
# Las credenciales viven ÚNICAMENTE en el entorno. No en el repositorio, no en
# el frontend, no en la base de datos y no en un log.
EVIDENCE_STORAGE_BACKEND = env('EVIDENCE_STORAGE_BACKEND', default='filesystem')
EVIDENCE_STORAGE_BUCKET = env('EVIDENCE_STORAGE_BUCKET', default='')
EVIDENCE_STORAGE_ENDPOINT_URL = env('EVIDENCE_STORAGE_ENDPOINT_URL', default='')
EVIDENCE_STORAGE_ACCESS_KEY_ID = env('EVIDENCE_STORAGE_ACCESS_KEY_ID', default='')
EVIDENCE_STORAGE_SECRET_ACCESS_KEY = env(
    'EVIDENCE_STORAGE_SECRET_ACCESS_KEY', default=''
)
EVIDENCE_STORAGE_REGION = env('EVIDENCE_STORAGE_REGION', default='auto')
#: Corto a propósito. Una URL firmada es una llave temporal; cuanto más dura,
#: más se parece a un enlace público que alguien puede reenviar.
EVIDENCE_STORAGE_URL_TTL_SECONDS = env.int(
    'EVIDENCE_STORAGE_URL_TTL_SECONDS', default=300
)

#: Lo que se acepta RECIBIR, antes de decodificar nada. Distinto de lo que se
#: acaba almacenando: una foto de 20 MB es legítima y termina pesando 200 KB.
# Imágenes públicas de la tienda, subidas desde el panel (hero, categorías).
# Comparten el almacenamiento de las evidencias, bajo otra ruta.
STOREFRONT_IMAGE_MAX_UPLOAD_BYTES = env.int(
    'STOREFRONT_IMAGE_MAX_UPLOAD_BYTES', default=8 * 1024 * 1024
)
STOREFRONT_IMAGE_MAX_EDGE = env.int('STOREFRONT_IMAGE_MAX_EDGE', default=2400)
STOREFRONT_IMAGE_MAX_PIXELS = env.int('STOREFRONT_IMAGE_MAX_PIXELS', default=40_000_000)

# PRODUCT-MEDIA / BULK-MEDIA. Las imágenes de producto pasan por la misma
# tubería que las de la tienda, así que comparten los límites de arriba por
# archivo. Éstos son los suyos: cuántas por producto y cuánto admite UNA carga
# masiva. El total de una carga es lo que viaja en una sola petición.
PRODUCT_IMAGE_MAX_PER_PRODUCT = env.int('PRODUCT_IMAGE_MAX_PER_PRODUCT', default=12)
IMPORT_IMAGES_MAX_FILES = env.int('IMPORT_IMAGES_MAX_FILES', default=200)
IMPORT_IMAGES_MAX_TOTAL_BYTES = env.int(
    'IMPORT_IMAGES_MAX_TOTAL_BYTES', default=100 * 1024 * 1024
)
IMPORT_IMAGES_ZIP_MAX_ENTRIES = env.int('IMPORT_IMAGES_ZIP_MAX_ENTRIES', default=400)
IMPORT_IMAGES_ZIP_MAX_RATIO = env.int('IMPORT_IMAGES_ZIP_MAX_RATIO', default=200)
# Django corta en 100 archivos por petición, y lo hace con una página HTML antes
# de que ninguna vista opine. Una carga masiva lleva el libro y sus imágenes en
# la misma petición: el tope de Django tiene que quedar por encima del nuestro,
# que es el que sabe explicarse.
DATA_UPLOAD_MAX_NUMBER_FILES = IMPORT_IMAGES_MAX_FILES + 20
# Lo que Django acepta tener en memoria de un cuerpo que no es un archivo. El
# tope por ruta lo pone store/request_limits.py; éste sólo no debe quedar por
# debajo del mayor cuerpo sin archivos que se lee entero: el webhook de WhatsApp.
DATA_UPLOAD_MAX_MEMORY_SIZE = 3 * 1024 * 1024

SERVICE_EVIDENCE_MAX_UPLOAD_BYTES = env.int(
    'SERVICE_EVIDENCE_MAX_UPLOAD_BYTES', default=25 * 1024 * 1024
)
#: El lado mayor de lo que se guarda. Suficiente para ver un golpe o una
#: rayadura; muy por debajo de los 4032 px que entrega un móvil actual, que no
#: aportan nada a mirar el estado de un equipo en una pantalla.
SERVICE_EVIDENCE_MAX_EDGE = env.int('SERVICE_EVIDENCE_MAX_EDGE', default=1600)
SERVICE_EVIDENCE_IMAGE_QUALITY = env.int('SERVICE_EVIDENCE_IMAGE_QUALITY', default=75)
#: El piso. Por debajo la compresión empieza a borrar justamente el detalle que
#: la foto existía para demostrar.
SERVICE_EVIDENCE_MIN_QUALITY = env.int('SERVICE_EVIDENCE_MIN_QUALITY', default=60)
#: Un OBJETIVO, no una garantía: si llegar exige destruir la evidencia, se
#: conserva la imagen más pesada.
SERVICE_EVIDENCE_TARGET_BYTES = env.int(
    'SERVICE_EVIDENCE_TARGET_BYTES', default=1_000_000
)
SERVICE_EVIDENCE_MAX_COMPRESSION_ATTEMPTS = env.int(
    'SERVICE_EVIDENCE_MAX_COMPRESSION_ATTEMPTS', default=6
)
#: Contra la bomba de descompresión: un PNG de 20 KB puede declarar 30.000 px de
#: lado y pedir varios GB al decodificarse. El límite de bytes no lo ve venir.
SERVICE_EVIDENCE_MAX_PIXELS = env.int(
    'SERVICE_EVIDENCE_MAX_PIXELS', default=60_000_000
)

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'es-pe'
TIME_ZONE = 'America/Lima'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'static')

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# CORS — always explicit origins (CORS_ALLOW_ALL_ORIGINS is incompatible with credentials)
CORS_ALLOW_CREDENTIALS = True

# CSRF — csrftoken cookie must be JS-readable so fetchWithAuth can send X-CSRFToken
CSRF_COOKIE_HTTPONLY = False

if DEBUG:
    CORS_ALLOWED_ORIGINS = env.list(
        'CORS_ALLOWED_ORIGINS',
        default=['http://localhost:3000', 'http://localhost:3002', 'http://127.0.0.1:3000'],
    )
    CSRF_TRUSTED_ORIGINS = env.list(
        'CSRF_TRUSTED_ORIGINS',
        default=['http://localhost:3000', 'http://localhost:3002', 'http://127.0.0.1:3000'],
    )
else:
    CORS_ALLOWED_ORIGINS = env.list('CORS_ALLOWED_ORIGINS', default=[])
    if not CORS_ALLOWED_ORIGINS:
        raise ImproperlyConfigured(
            "CORS_ALLOWED_ORIGINS must be set in production "
            "(e.g. CORS_ALLOWED_ORIGINS=https://yourdomain.com)"
        )
    CSRF_TRUSTED_ORIGINS = env.list('CSRF_TRUSTED_ORIGINS', default=CORS_ALLOWED_ORIGINS)

# JWT cookie settings
JWT_COOKIE_ACCESS_NAME = env('JWT_COOKIE_ACCESS_NAME', default='blackdog_access')
JWT_COOKIE_REFRESH_NAME = env('JWT_COOKIE_REFRESH_NAME', default='blackdog_refresh')
JWT_COOKIE_HTTPONLY = True
# SEC-SET-03. Only Lax or Strict. The refresh endpoint checks CSRF only when the
# access cookie is present; when it has expired — the normal case for a refresh —
# what stops a cross-site request is SameSite. `None` removed that, silently.
JWT_COOKIE_SAMESITE = env('JWT_COOKIE_SAMESITE', default='Lax').strip().capitalize()
if JWT_COOKIE_SAMESITE not in ('Lax', 'Strict'):
    raise ImproperlyConfigured(
        "JWT_COOKIE_SAMESITE must be 'Lax' or 'Strict'. 'None' would send the "
        "session cookies on cross-site requests."
    )
JWT_COOKIE_SECURE = not DEBUG

# --- Payments -------------------------------------------------------------
#
# The gateway is Izipay, which sells TWO products with different credentials,
# different browser scripts and different signatures:
#
#   izipay        «SDK web / Checkout» (developers.izipay.pe) — IZIPAY_* below
#   micuentaweb   «Mi Cuenta Web», REST API V4 (secure.micuentaweb.pe) — MICUENTAWEB_*
#
# `PAYMENT_PROVIDER` names the ONE this installation holds credentials for. It
# is not a way to run both: the checkout opens payments with that one only, and
# the notification endpoint of the other answers 404. Change it only while no
# payment is waiting for its answer.
PAYMENT_PROVIDER = env('PAYMENT_PROVIDER', default='izipay')

# SANDBOX OR PRODUCTION, SAID OUT LOUD.
#
# Never derived from DEBUG. `DEBUG = False` means "this is not a developer's
# laptop"; it does not mean "charge real cards", and a staging box that is not
# in debug mode is exactly the deployment that would have silently gone live.
IZIPAY_ENV = env('IZIPAY_ENV', default='sandbox')

# Public — Izipay documents both as browser-safe. They are sent to the frontend
# because the SDK needs them there.
IZIPAY_MERCHANT_CODE = env('IZIPAY_MERCHANT_CODE', default='')
IZIPAY_PUBLIC_KEY = env('IZIPAY_PUBLIC_KEY', default='')

# SECRET — backend only, and never serialised to any response. The API key
# mints session tokens; the hash key verifies notification signatures. Either
# one in a browser is the whole integration.
IZIPAY_API_KEY = env('IZIPAY_API_KEY', default='')
IZIPAY_HASH_KEY = env('IZIPAY_HASH_KEY', default='')

# Endpoint for the session-token API of THIS environment.
#
# Configuration rather than a constant, and with no default, because the URL
# could not be read from Izipay's public documentation — the API reference is
# rendered client-side and the operator's merchant panel is the authority. A
# guessed default would be an invented endpoint; an empty one fails closed with
# a message naming the variable. The SDK URLs, which ARE documented verbatim,
# are constants in the adapter instead.
IZIPAY_TOKEN_URL = env('IZIPAY_TOKEN_URL', default='')

# The currency the storefront charges in. One value, resolved here, so no part
# of the code decides for itself what "PEN" was.
IZIPAY_CURRENCY = env('IZIPAY_CURRENCY', default='PEN')

# Absolute, publicly reachable URL of our own notification endpoint, handed to
# Izipay per transaction as `urlIPN`. Empty means notifications are configured
# in the merchant panel instead; payments still work, because the endpoint's
# security is its signature and not how Izipay learned the address.
IZIPAY_IPN_URL = env('IZIPAY_IPN_URL', default='')

# --- Mi Cuenta Web (REST API V4), used when PAYMENT_PROVIDER=micuentaweb -----
#
# The four values of «Configuración › Tienda › Claves de API REST» in the Back
# Office. TEST or PRODUCTION is not a variable: it is which pair of keys is
# pasted here (`testpassword_…` + `…:testpublickey_…`, or the production pair).
# A mixed pair refuses the checkout.
#
# Public — the Krypton client needs them in the browser.
MICUENTAWEB_SHOP_ID = env('MICUENTAWEB_SHOP_ID', default='')          # «Usuario»
MICUENTAWEB_PUBLIC_KEY = env('MICUENTAWEB_PUBLIC_KEY', default='')    # <usuario>:<clave pública>
# SECRET — backend only. Creates payments and verifies the notification (IPN).
MICUENTAWEB_PASSWORD = env('MICUENTAWEB_PASSWORD', default='')
# SECRET — the «clave HMAC-SHA-256». It signs the copy of the answer the BROWSER
# receives, which this backend never believes; nothing reads it today. Declared
# so that it has one place to live if a signed return is ever checked.
MICUENTAWEB_HMAC_KEY = env('MICUENTAWEB_HMAC_KEY', default='')
# «Nombre del servidor de la API REST».
MICUENTAWEB_API_URL = env('MICUENTAWEB_API_URL', default='https://api.micuentaweb.pe')
MICUENTAWEB_CURRENCY = env('MICUENTAWEB_CURRENCY', default='PEN')
# Our notification endpoint, sent per payment as `ipnTargetUrl`. Empty = the
# URL configured in the Back Office («Reglas de notificación») is used.
MICUENTAWEB_IPN_URL = env('MICUENTAWEB_IPN_URL', default='')

# Where the buyer comes back to after paying.
CHECKOUT_RETURN_URL = env('CHECKOUT_RETURN_URL', default='http://localhost:3000')


def _require_public_url(name, value):
    """
    SEC-SET-10 / ENV-02. These URLs go into emails and payment returns. Left at
    their development default in production, every verification, password reset
    and invitation link points at the recipient's own machine, and nothing says
    so until a customer reports it.
    """
    from urllib.parse import urlsplit
    host = (urlsplit(value).hostname or '').lower()
    if not host or host in ('localhost', '127.0.0.1', '0.0.0.0', '::1'):
        raise ImproperlyConfigured(
            f"{name} must be set to the public address of the site in production "
            f"(e.g. {name}=https://yourdomain.com), not {value!r}."
        )


if not DEBUG:
    _require_public_url('CHECKOUT_RETURN_URL', CHECKOUT_RETURN_URL)

# INTEGRATIONS-CONSOLE — la clave raíz del almacén de secretos.
#
# Las credenciales que un administrador de plataforma escribe en el panel
# (Configuración › Integraciones) se guardan cifradas en la base de datos. Ésta
# es la clave que las cifra: pertenece al despliegue, como SECRET_KEY, y NO vive
# en la base ni se administra desde el panel. Sin ella el panel no puede guardar
# ni leer ninguna credencial; con otra distinta, tampoco las ya guardadas.
#
#   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
#
# Para cambiarla: la nueva aquí, la anterior en APP_CONFIG_ENCRYPTION_KEYS_PREVIOUS
# (varias, separadas por comas), y `python manage.py reseal_integration_secrets`.
# En desarrollo (DEBUG) puede faltar: se deriva una de SECRET_KEY.
APP_CONFIG_ENCRYPTION_KEY = env('APP_CONFIG_ENCRYPTION_KEY', default='')
APP_CONFIG_ENCRYPTION_KEYS_PREVIOUS = env('APP_CONFIG_ENCRYPTION_KEYS_PREVIOUS', default='')

# Email
#
# EL CORREO SE CONFIGURA EN EL PANEL (Configuración › Integraciones › Correo SMTP).
# Django entrega cada mensaje a `RuntimeEmailBackend`, que en CADA envío pregunta
# qué configuración está activa: la del panel primero; si no hay, las `EMAIL_*` de
# este archivo, que quedan como respaldo de una instalación anterior al panel.
#
# `EMAIL_BACKEND` (la variable) nombra ese respaldo. En desarrollo, por omisión, es
# el de consola, que escribe cada mensaje —con sus enlaces de un solo uso— en la
# salida. En producción NO hay valor por omisión: si no hay SMTP ni en el panel ni
# aquí, el correo queda «sin configurar», los envíos fallan con un error que se
# registra y `ops_status` lo dice. Nunca se cae a la consola en silencio
# (MAIL-CONSOLE-DEFAULT).
EMAIL_BACKEND_LEGACY = env('EMAIL_BACKEND', default='django.core.mail.backends.console.EmailBackend' if DEBUG else '')
EMAIL_BACKEND = 'store.integrations.mail.RuntimeEmailBackend'
# Transport-level sender. Platform configuration, not tenant configuration:
# the SMTP credentials behind it belong to the operator, and a per-tenant
# sender would need per-tenant SMTP — explicitly out of scope (no secrets in
# CompanySettings). The DISPLAY identity inside each message is per tenant.
DEFAULT_FROM_EMAIL = env('DEFAULT_FROM_EMAIL', default='no-reply@localhost')
FRONTEND_URL = env('FRONTEND_URL', default='http://localhost:3000')

# GOOGLE-AUTH. The OAuth client id of «Continuar con Google» (Google Identity
# Services). It is public — it travels to every browser — and it is what the
# backend requires as the audience of every ID token. Empty = the button is not
# offered and the endpoints do not exist. There is no client secret: the ID
# token flow has none.
GOOGLE_OAUTH_CLIENT_ID = env('GOOGLE_OAUTH_CLIENT_ID', default='')

# WHATSAPP-NOTIFY. Which provider carries customer notices:
#   cloud_api  the official WhatsApp Business Cloud API (Meta) — the default
#   fake       records what would be sent and sends nothing (tests, local review)
#   disabled   nothing is sent from this environment
# No credential is configured here: each company references its own variables
# (`manage.py configure_whatsapp`), all of them in the WHATSAPP_ namespace.
WHATSAPP_PROVIDER = env('WHATSAPP_PROVIDER', default='cloud_api')
WHATSAPP_GRAPH_API_VERSION = env('WHATSAPP_GRAPH_API_VERSION', default='v21.0')
# Try the send right after the transaction commits. Turn it off where a timer
# runs `send_pending_notifications`, so a slow provider never delays a counter.
WHATSAPP_SEND_INLINE = env.bool('WHATSAPP_SEND_INLINE', default=True)

# ANALYTICS-MARKETING. The purchase conversion (Google Analytics, Meta, TikTok) is
# written with the payment and sent right after the transaction commits. Turn
# this off where a timer runs `send_pending_conversions`, so a slow provider is
# never waited for in the request that confirms a payment. Which providers
# exist, their IDs and their keys are NOT configured here: a master sets them in
# Configuración › Integraciones.
MEASUREMENT_SEND_INLINE = env.bool('MEASUREMENT_SEND_INLINE', default=True)
if not DEBUG:
    _require_public_url('FRONTEND_URL', FRONTEND_URL)
REQUIRE_EMAIL_VERIFICATION = env.bool('REQUIRE_EMAIL_VERIFICATION', default=False)
EMAIL_HOST = env('EMAIL_HOST', default='')
EMAIL_PORT = env.int('EMAIL_PORT', default=587)
EMAIL_HOST_USER = env('EMAIL_HOST_USER', default='')
EMAIL_HOST_PASSWORD = env('EMAIL_HOST_PASSWORD', default='')
# TLS implícito (puerto 465): la conexión es cifrada desde el primer byte. Hay
# proveedores que sólo ofrecen eso. Excluye a STARTTLS (`EMAIL_USE_TLS`, 587).
EMAIL_USE_SSL = env.bool('EMAIL_USE_SSL', default=False)
EMAIL_USE_TLS = env.bool('EMAIL_USE_TLS', default=not EMAIL_USE_SSL)
if EMAIL_USE_SSL and EMAIL_USE_TLS:
    raise ImproperlyConfigured(
        "EMAIL_USE_SSL and EMAIL_USE_TLS are mutually exclusive: use EMAIL_USE_SSL=1 "
        "for implicit TLS (port 465) or EMAIL_USE_TLS=1 for STARTTLS (port 587)."
    )
# MAIL-TIMEOUT. Django espera a un servidor de correo sin límite si no se le
# dice otra cosa, y un registro o una recuperación de contraseña envían su
# mensaje dentro de la petición: con ocho hilos, un proveedor que deja de
# responder tumbaría la API registro a registro.
EMAIL_TIMEOUT = env.int('EMAIL_TIMEOUT', default=10)
if EMAIL_TIMEOUT <= 0:
    raise ImproperlyConfigured("EMAIL_TIMEOUT must be a positive number of seconds.")
# --- Internal new-sale notifications ---------------------------------------
#
# DEPRECATED AS A RECIPIENT since Phase 3. Each company now names its own address
# in `CompanySettings.order_notification_email`, and there is deliberately NO
# fallback to this value: it holds ONE address, so falling back would announce a
# second tenant's sales — customer names and phone numbers included — in whoever
# owns this address's inbox.
#
# It survives only so migration 0027 can copy it into the pilot company's
# settings during the upgrade, keeping the existing installation's alerts
# working. Nothing in the request path reads it.
ORDER_NOTIFICATION_EMAIL = env('ORDER_NOTIFICATION_EMAIL', default='')

# --- Platform identity ------------------------------------------------------
#
# The SaaS operator's own name, used only on account-security emails
# (verification, password reset). Those are about a global User account, not
# about a purchase from any one tenant, so branding them as a company would be
# wrong — see store/emails.py.
#
# Empty means those emails carry no brand name at all. That is the honest
# default: the platform has no more claim to a compiled-in name than a tenant
# does, and shipping one would put this installation's brand on every fork.
PLATFORM_NAME = env('PLATFORM_NAME', default='')

if (
    not DEBUG
    and EMAIL_BACKEND_LEGACY == 'django.core.mail.backends.smtp.EmailBackend'
    and not EMAIL_HOST
):
    raise ImproperlyConfigured(
        "EMAIL_HOST must be set when using SMTP backend in production. "
        "Set EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend for development."
    )

# Logging — SEC-SET-06.
#
# There was no LOGGING at all. Django's default sends `django.*` records to a
# handler that only speaks when DEBUG is on, so in production nothing below
# WARNING reached anywhere and security events (a refused Host, a failed CSRF
# check) were discarded. One handler to stderr, which is where a container's
# logs are read from; the level is INFO in production and can be raised or
# lowered with DJANGO_LOG_LEVEL.
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'plain': {'format': '%(asctime)s %(levelname)s %(name)s %(message)s'},
    },
    # LOG-REDACT: a URL can be a credential. See backend/log_redaction.py.
    'filters': {
        'redact': {'()': 'backend.log_redaction.RedactingFilter'},
    },
    'handlers': {
        'console': {'class': 'logging.StreamHandler', 'formatter': 'plain', 'filters': ['redact']},
    },
    'root': {
        'handlers': ['console'],
        'level': env('DJANGO_LOG_LEVEL', default='WARNING' if DEBUG else 'INFO'),
    },
    'loggers': {
        'django.security': {'handlers': ['console'], 'level': 'INFO', 'propagate': False},
        'django.request': {'handlers': ['console'], 'level': 'WARNING', 'propagate': False},
        # AUTH-LOGGING-01: sign-in attempts. See store/security_log.py.
        'store.security': {'handlers': ['console'], 'level': 'INFO', 'propagate': False},
    },
}

# HTTPS security headers — only enforce in production
if not DEBUG:
    SECURE_SSL_REDIRECT = env.bool('SECURE_SSL_REDIRECT', default=True)
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = env.int('SECURE_HSTS_SECONDS', default=31536000)
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    # Only when a trusted proxy is actually declared.
    #
    # This header tells Django "the connection in front of you was HTTPS". It is
    # a header, so if this process is reachable directly, any caller can send it
    # and Django will believe a plaintext request was secure — which disables
    # SECURE_SSL_REDIRECT and lets it set Secure cookies over cleartext. It is
    # only meaningful when something trustworthy is in front, and that is
    # exactly what TRUSTED_PROXY_COUNT declares.
    if TRUSTED_PROXY_COUNT > 0:
        SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# ---------------------------------------------------------------------------
# Storefront tenant resolution — SaaS Phase 2B
# ---------------------------------------------------------------------------
#
# The public catalogue belongs to ONE company. Which one is resolved, in order:
#
#   1. the request host      blackdog.example.com -> Company.slug == "blackdog"
#   2. this setting          an explicit single-store deployment
#   3. single-company        only when the database holds exactly ONE active
#                            company — unambiguous by construction
#
# There is deliberately NO "first company in the database" fallback: on a
# multi-tenant install that would silently serve one tenant's catalogue under
# another tenant's domain. Step 3 is a different thing — it is not "the first of
# many", it is "the only one" — and it disappears the moment a second company
# exists, which is exactly when this setting becomes required. It also keeps an
# existing single-store deployment serving its catalogue across the upgrade.
#
# Set it for any single-store deployment:
#   DEFAULT_STOREFRONT_COMPANY_SLUG=mi-empresa
DEFAULT_STOREFRONT_COMPANY_SLUG = env('DEFAULT_STOREFRONT_COMPANY_SLUG', default='')


# --- Phase 2D: where historical stock lives ---------------------------------
#
# Read ONCE, by migration 0025, and only when it cannot work the answer out on
# its own. Nothing in the application layer imports it.
#
# Before Phase 2D a product carried a single stock figure and a company was
# implicitly one place. Splitting that figure across branches needs a fact the
# database does not contain, so the migration asks in the only case where the
# answer is not obvious: a company with SEVERAL active branches and stock,
# movements or orders to place. With exactly one active branch it resolves by
# itself and this stays empty — which is every installation that exists today.
#
# Map company slug to branch name:
#   INVENTORY_MIGRATION_BRANCHES = {'mi-empresa': 'Tienda principal'}
#
# Leaving it unset is safe: the migration refuses and explains, rather than
# distributing units to a shop that never had them.
INVENTORY_MIGRATION_BRANCHES: dict[str, str] = {}
