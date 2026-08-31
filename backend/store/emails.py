"""
Account-security emails — verification and password reset.

THESE ARE PLATFORM EMAILS, NOT TENANT EMAILS. That distinction is the whole
point of this module's separation from `email_services.py`.

A `User` is global: one identity that can buy from several storefronts and work
for several companies. An email about that account — "verify your address",
"someone asked to reset your password" — is therefore from the PLATFORM, not
from whichever shop the person happened to visit last. Branding it as a tenant
would be actively confusing in the case that matters most: a customer of three
shops receiving a password reset from a business they never asked about.

Order emails are the opposite and live in `email_services.py`: those are about a
purchase from one specific company, and they carry that company's identity.

The platform's own name comes from `settings.PLATFORM_NAME`. It is not
hardcoded, because the platform is no more entitled to a compiled-in brand than
a tenant is; when unset, these emails simply carry no brand name rather than
borrowing one.
"""

import logging

from django.conf import settings
from django.core.mail import send_mail

logger = logging.getLogger(__name__)


def _platform_name() -> str:
    return (getattr(settings, 'PLATFORM_NAME', '') or '').strip()


def _suffix(prefix: str) -> str:
    """`"Verifica tu cuenta"` → `"Verifica tu cuenta en Acme"`, or unchanged."""
    name = _platform_name()
    return f'{prefix} en {name}' if name else prefix


def _signature() -> str:
    name = _platform_name()
    return f'\n\n— {name}' if name else ''


def send_verification_email(user, raw_token):
    link = f"{settings.FRONTEND_URL}/auth/verify-email?token={raw_token}"
    try:
        send_mail(
            subject=_suffix('Verifica tu cuenta'),
            message=(
                f"Hola {user.first_name or user.username},\n\n"
                f"Para verificar tu cuenta, haz clic en el siguiente enlace "
                f"(válido por 24 horas):\n\n"
                f"{link}\n\n"
                f"Si no creaste esta cuenta, ignora este mensaje."
                f"{_signature()}"
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            fail_silently=False,
        )
    except Exception:
        logger.exception("Failed to send verification email to user %s", user.pk)


def send_password_reset_email(user, raw_token):
    link = f"{settings.FRONTEND_URL}/auth/reset-password?token={raw_token}"
    name = _platform_name()
    subject = f'Recuperación de contraseña — {name}' if name else 'Recuperación de contraseña'
    try:
        send_mail(
            subject=subject,
            message=(
                f"Hola {user.first_name or user.username},\n\n"
                f"Recibimos una solicitud para restablecer tu contraseña. "
                f"Usa el siguiente enlace (válido por 1 hora):\n\n"
                f"{link}\n\n"
                f"Si no solicitaste esto, ignora este mensaje. "
                f"Tu contraseña no cambiará a menos que uses este enlace."
                f"{_signature()}"
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            fail_silently=False,
        )
    except Exception:
        logger.exception("Failed to send password reset email to user %s", user.pk)
