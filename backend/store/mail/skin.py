"""
Whose template it is, and the template with its fixed values filled in.

`plantilla/plantilla-maestra.html` is kept exactly as its designer delivered it.
Its `[bracketed]` values — logo, site, YouTube, legal name, tax id — are put in
here, when it is loaded, so the file can be replaced by a newer design without
anybody re-typing them.

The template belongs to ONE company (`plantilla/marca.json` names it by slug,
like the pilot's migrations do). It is used for that company, and for the
platform's own e-mails when that company is the storefront this installation
serves. For anybody else there is no skin, and that is an answer, not an error.
"""
import html
import json
import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)

_HERE = Path(__file__).resolve().parent / 'plantilla'


@dataclass(frozen=True)
class Skin:
    company: object          # the Company the template belongs to
    template: str            # the Mustache source, fixed values already in
    # What the template's own footer prints, word for word (`marca.json`). The
    # text version signs with these, so the two halves of one e-mail agree
    # whatever the company later types in its settings.
    name: str
    address: str
    whatsapp: str


@lru_cache(maxsize=1)
def _source() -> str:
    return (_HERE / 'plantilla-maestra.html').read_text(encoding='utf-8')


@lru_cache(maxsize=1)
def _brand() -> dict:
    return json.loads((_HERE / 'marca.json').read_text(encoding='utf-8'))


def owner_slug() -> str:
    return _brand().get('empresa', '')


def _as_text(value: str) -> str:
    """
    `value` as characters to print — never as markup, and never as template.

    The fixed values go into the SOURCE, which is rendered afterwards: a legal
    name with `{{` in it would otherwise be Mustache, run with the data of every
    e-mail. Braces become entities, which a mail client prints as braces.
    """
    return html.escape(value, quote=True).replace('{', '&#123;').replace('}', '&#125;')


def _installation_company():
    """The storefront this installation serves, when the operator has said which."""
    from ..models import Company

    slug = (getattr(settings, 'DEFAULT_STOREFRONT_COMPANY_SLUG', '') or '').strip().lower()
    if not slug:
        return None
    return Company.objects.filter(slug=slug, is_active=True).first()


def skin_for(company=None):
    """
    The skin to dress `company`'s e-mail with, or None.

    `company=None` is an e-mail of the platform itself (an account, a password):
    it wears the skin only where the installation's storefront IS the template's
    owner. Without that setting the platform stays neutral, as it always was.
    """
    from .. import company_settings

    target = company if company is not None else _installation_company()
    if target is None or target.slug != owner_slug():
        return None

    brand = _brand()
    identity = company_settings.company_identity(target)
    site = (getattr(settings, 'FRONTEND_URL', '') or '').rstrip('/')
    fixed = {
        '[URL del sitio web]': site,
        '[URL pública del logo]': f'{site}{brand.get("logo", "")}' if site and brand.get('logo') else '',
        '[URL del canal de YouTube]': brand.get('youtube', ''),
        '[Razón social]': identity.legal_name,
        '[RUC]': identity.tax_id,
    }
    missing = [name for name, value in fixed.items() if not value]
    missing += [key for key in ('nombre', 'direccion', 'whatsapp') if not brand.get(key)]
    if missing:
        # A footer with «[RUC]» printed in it is worse than the plain e-mail.
        logger.error('the mail template is not used: no value for %s', ', '.join(missing))
        return None

    template = _source()
    for name, value in fixed.items():
        template = template.replace(name, _as_text(value))
    return Skin(
        company=target, template=template,
        name=brand['nombre'], address=brand['direccion'], whatsapp=brand['whatsapp'],
    )
