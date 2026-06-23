import logging

from django.conf import settings
from django.core.mail import send_mail

logger = logging.getLogger(__name__)


def send_verification_email(user, raw_token):
    link = f"{settings.FRONTEND_URL}/auth/verify-email?token={raw_token}"
    try:
        send_mail(
            subject='Verifica tu cuenta en Black Dog Store',
            message=(
                f"Hola {user.first_name or user.username},\n\n"
                f"Para verificar tu cuenta, haz clic en el siguiente enlace "
                f"(válido por 24 horas):\n\n"
                f"{link}\n\n"
                f"Si no creaste esta cuenta, ignora este mensaje.\n\n"
                f"— Black Dog Store"
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            fail_silently=False,
        )
    except Exception:
        logger.exception("Failed to send verification email to user %s", user.pk)


def send_password_reset_email(user, raw_token):
    link = f"{settings.FRONTEND_URL}/auth/reset-password?token={raw_token}"
    try:
        send_mail(
            subject='Recuperación de contraseña — Black Dog Store',
            message=(
                f"Hola {user.first_name or user.username},\n\n"
                f"Recibimos una solicitud para restablecer tu contraseña. "
                f"Usa el siguiente enlace (válido por 1 hora):\n\n"
                f"{link}\n\n"
                f"Si no solicitaste esto, ignora este mensaje. "
                f"Tu contraseña no cambiará a menos que uses este enlace.\n\n"
                f"— Black Dog Store"
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            fail_silently=False,
        )
    except Exception:
        logger.exception("Failed to send password reset email to user %s", user.pk)
