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
from urllib.parse import quote

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


def _another_shops_address(request) -> bool:
    """
    Whether the person asked from the address of a company that has no template.

    `DEFAULT_STOREFRONT_COMPANY_SLUG` says which storefront this installation
    serves by default; a request's host can name another. Somebody who registers
    at another shop's address is not written to with this shop's name, address
    and phone. A host that cannot be read is treated as one.
    """
    from . import mail
    from .tenancy import resolve_company_from_host

    if request is None:
        return False
    try:
        company = resolve_company_from_host(request.get_host())
    except Exception:
        return True
    return company is not None and not mail.available(company)


def _dressed(kind: str, user, build, request):
    """
    `kind` rendered with the installation's template, or None.

    None means «send the plain one»: there is no template here — the neutral
    default of this module — or it could not be filled. An account e-mail that
    does not go out because of how it looks is the wrong trade.

    It only renders. Sending is the caller's one attempt, dressed or plain: a
    message the server did not take is not sent a second time in other clothes.
    """
    from . import mail

    try:
        if _another_shops_address(request) or not mail.available():
            return None
        name = user.first_name or user.username
        return mail.render(kind, build(brand=mail.brand(), name=name))
    except Exception:
        # Without the body: it carries the link.
        logger.exception("The %s e-mail could not be dressed with the template for user %s", kind, user.pk)
        return None


def send_verification_email(user, raw_token, *, request=None):
    """`request` is the one the person made, when there is one: see `_another_shops_address`."""
    from . import mail
    from .mail import builders

    link = f"{settings.FRONTEND_URL}/auth/verify-email?token={raw_token}"
    dressed = _dressed('verify_email', user, lambda **who: builders.verify_email(link=link, **who), request)
    try:
        if dressed is not None:
            mail.deliver(dressed, user.email)
            return
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


def send_password_reset_email(user, raw_token, next_path=None, *, request=None):
    """
    `next_path` is where the person was going when they found they could not
    log in. The caller has already checked it is the address of an invitation:
    nothing else is ever written into this link.
    """
    from . import mail
    from .mail import builders

    link = f"{settings.FRONTEND_URL}/auth/reset-password?token={raw_token}"
    if next_path:
        link += f"&next={quote(next_path, safe='')}"
    dressed = _dressed('password_reset', user, lambda **who: builders.password_reset(link=link, **who), request)
    name = _platform_name()
    subject = f'Recuperación de contraseña — {name}' if name else 'Recuperación de contraseña'
    try:
        if dressed is not None:
            mail.deliver(dressed, user.email)
            return
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
