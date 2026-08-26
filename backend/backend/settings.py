from datetime import timedelta
from pathlib import Path
import os
import environ
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

# django-environ — DEBUG defaults True for local dev convenience
env = environ.Env(
    DEBUG=(bool, True),
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

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'store.authentication.CookieJWTAuthentication',
    ),
    'DEFAULT_PERMISSION_CLASSES': (
        'rest_framework.permissions.AllowAny',
    ),
    # Pagination NOT enabled globally — frontend expects raw arrays.
    # Add per-ViewSet pagination in Phase 5 after updating the frontend fetchers.
    #
    # Throttle rates — individual views declare their throttle_classes.
    # Rates apply per IP for AnonRateThrottle (login, register, etc.).
    'DEFAULT_THROTTLE_RATES': {
        'login': '5/min',
        'register': '5/min',
        'coupon': '20/min',
        'review_create': '5/min',
        'checkout': '10/min',
        'cart': '60/min',
        'payment_status': '30/min',
        'resend_verification': '3/min',
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
        'admin_order_email_resend': '10/min',
        'admin_inventory_reports': '120/min',
        'admin_stock_movements': '60/min',
        'admin_sales_notes': '60/min',
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
JWT_COOKIE_SAMESITE = env('JWT_COOKIE_SAMESITE', default='Lax')
JWT_COOKIE_SECURE = not DEBUG

# Stripe
STRIPE_SECRET_KEY = env('STRIPE_SECRET_KEY', default='')
STRIPE_WEBHOOK_SECRET = env('STRIPE_WEBHOOK_SECRET', default='')
STRIPE_DOMAIN = env('STRIPE_DOMAIN', default='http://localhost:3000')
STRIPE_CURRENCY = env('STRIPE_CURRENCY', default='pen')

# Email
EMAIL_BACKEND = env('EMAIL_BACKEND', default='django.core.mail.backends.console.EmailBackend')
DEFAULT_FROM_EMAIL = env('DEFAULT_FROM_EMAIL', default='Black Dog Store <no-reply@blackdogstoreperu.online>')
FRONTEND_URL = env('FRONTEND_URL', default='http://localhost:3000')
REQUIRE_EMAIL_VERIFICATION = env.bool('REQUIRE_EMAIL_VERIFICATION', default=False)
EMAIL_HOST = env('EMAIL_HOST', default='')
EMAIL_PORT = env.int('EMAIL_PORT', default=587)
EMAIL_HOST_USER = env('EMAIL_HOST_USER', default='')
EMAIL_HOST_PASSWORD = env('EMAIL_HOST_PASSWORD', default='')
EMAIL_USE_TLS = env.bool('EMAIL_USE_TLS', default=True)
# Recipient for internal new-sale notifications (optional; leave empty to disable)
ORDER_NOTIFICATION_EMAIL = env('ORDER_NOTIFICATION_EMAIL', default='')

if (
    not DEBUG
    and EMAIL_BACKEND == 'django.core.mail.backends.smtp.EmailBackend'
    and not EMAIL_HOST
):
    raise ImproperlyConfigured(
        "EMAIL_HOST must be set when using SMTP backend in production. "
        "Set EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend for development."
    )

# HTTPS security headers — only enforce in production
if not DEBUG:
    SECURE_SSL_REDIRECT = env.bool('SECURE_SSL_REDIRECT', default=True)
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = env.int('SECURE_HSTS_SECONDS', default=31536000)
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
