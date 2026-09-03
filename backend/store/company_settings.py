"""
Company identity, branding and configuration — SaaS Phase 3.

THE PROBLEM THIS MODULE EXISTS TO CLOSE
---------------------------------------
Until this phase, `email_services`, `pdf_services` and `sales_note_services`
each carried their own copy of six module constants — `_STORE_NAME`,
`_STORE_RUC`, `_STORE_LEGAL_NAME`, `_STORE_ADDRESS`, `_STORE_PHONE` and
`_STORE_WHATSAPP_LINK` — holding literal values.

Those were not defaults. They were the legal identity of one specific business,
compiled into the runtime. A second tenant selling through this platform would
have sent its customers a confirmation email and a PDF receipt bearing another
company's name and tax id — which is not a branding bug, it is putting somebody
else's legal identity on a commercial document.

This module is the single place that answers "who is this company, and what does
it look like". Nothing else reads `CompanySettings` directly.

THE FALLBACK RULE — the important part
--------------------------------------
When a company has not configured something, the answer falls back through:

    CompanySettings.<field>  →  Company.<equivalent>  →  EMPTY

and stops. It NEVER falls back to the pilot's values. A company that has not
filled in its address shows no address; it does not show the pilot tenant's.
Empty is a visible, fixable state. Wrong is neither.

The pilot keeps its identity because migration 0028 WROTE it into that tenant's
own `CompanySettings` row — as data, where it belongs — not because any code
path still knows which business it is.

A test scans this file, and the three commercial services, for the pilot's
literal values and fails if any reappears. That is why the paragraph above names
none of them.

TWO IDENTITIES, DELIBERATELY SEPARATE
-------------------------------------
    PLATFORM   account security: verification and password-reset emails. A User
               is global — one identity across every tenant they buy from or
               work for — so an email about that account is from the platform,
               not from whichever shop they last visited. `settings.PLATFORM_NAME`.

    TENANT     everything commercial: the storefront, order emails, receipts,
               sales notes, the internal control of that company.

Conflating them would mean a customer of three shops receives password resets
branded as whichever one the code happened to reach first.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# The colour a storefront falls back to when a tenant has configured none.
# NEUTRAL ON PURPOSE: a dark, unbranded surface that belongs to no business.
# The pilot does not reach these — its own colours are in its settings row.
NEUTRAL_THEME = {
    'primary_color': '#FFFFFF',
    'accent_color': '#A1A1AA',
    'background_color': '#0A0A0A',
    'surface_color': '#141414',
    'text_color': '#FAFAFA',
    'border_color': '#262626',
}

# CSS custom properties the storefront consumes, in the order a stylesheet
# expects them. Declared here so the API and the frontend cannot drift.
THEME_CSS_VARIABLES = {
    'primary_color': '--brand-primary',
    'accent_color': '--brand-accent',
    'background_color': '--brand-background',
    'surface_color': '--brand-surface',
    'text_color': '--brand-text',
    'border_color': '--brand-border',
}


def get_company_settings(company):
    """
    The settings row for `company`, or None.

    Returns None rather than creating one: creation belongs to provisioning,
    which is explicit and audited. A read that silently wrote a row would make
    "has this tenant been provisioned?" unanswerable.
    """
    if company is None:
        return None
    return getattr(company, 'settings', None)


# ---------------------------------------------------------------------------
# Commercial identity
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CompanyIdentity:
    """
    Who a company is on a commercial document.

    Every field may be empty. An incomplete tenant renders blanks, and the
    settings screen tells them which. Nothing here is ever borrowed from
    another company.
    """

    name: str = ''
    legal_name: str = ''
    tax_id: str = ''
    legal_address: str = ''
    city: str = ''
    phone: str = ''
    whatsapp_number: str = ''
    whatsapp_link: str = ''
    contact_email: str = ''
    website_url: str = ''
    logo_url: str = ''
    warranty_policy_text: str = ''
    warranty_policy_url: str = ''

    def as_dict(self) -> dict:
        return {
            'name': self.name,
            'legal_name': self.legal_name,
            'tax_id': self.tax_id,
            'legal_address': self.legal_address,
            'city': self.city,
            'phone': self.phone,
            'whatsapp_number': self.whatsapp_number,
            'whatsapp_link': self.whatsapp_link,
            'contact_email': self.contact_email,
            'website_url': self.website_url,
            'logo_url': self.logo_url,
            'warranty_policy_text': self.warranty_policy_text,
            'warranty_policy_url': self.warranty_policy_url,
        }


def build_whatsapp_link(number: str) -> str:
    """
    A wa.me link from a stored digit string, or ''.

    Built here rather than stored so the database never holds a URL that gets
    rendered as an anchor in a customer's inbox. Digits in, a known scheme out;
    anything that is not digits produces nothing at all.
    """
    digits = ''.join(ch for ch in str(number or '') if ch.isdigit())
    if not (8 <= len(digits) <= 15):
        return ''
    return f'https://wa.me/{digits}'


def company_identity(company) -> CompanyIdentity:
    """
    The commercial identity of `company`, right now.

    `Company` supplies name / legal_name / tax_id; `CompanySettings` supplies
    everything else. Missing settings mean empty strings, never another tenant's
    values — see the module docstring.
    """
    if company is None:
        return CompanyIdentity()

    s = get_company_settings(company)
    if s is None:
        return CompanyIdentity(
            name=company.name or '',
            legal_name=company.legal_name or '',
            tax_id=company.tax_id or '',
        )

    return CompanyIdentity(
        name=company.name or '',
        legal_name=company.legal_name or '',
        tax_id=company.tax_id or '',
        legal_address=s.legal_address or '',
        city=s.city or '',
        phone=s.phone or '',
        whatsapp_number=s.whatsapp_number or '',
        whatsapp_link=build_whatsapp_link(s.whatsapp_number),
        contact_email=s.contact_email or '',
        website_url=s.website_url or '',
        logo_url=s.logo_url or '',
        warranty_policy_text=s.warranty_policy_text or '',
        warranty_policy_url=s.warranty_policy_url or '',
    )


# ---------------------------------------------------------------------------
# Branding
# ---------------------------------------------------------------------------

#: Qué campo del modelo responde a cada pregunta que hace un componente.
#: La clave es la PREGUNTA —«horizontal, sobre oscuro»—, no el nombre de la
#: columna, para que renombrar una columna no obligue a tocar el frontend.
LOGO_VARIANT_FIELDS = {
    'primary_on_light': 'logo_on_light_url',
    'primary_on_dark': 'logo_on_dark_url',
    'horizontal_on_light': 'logo_horizontal_on_light_url',
    'horizontal_on_dark': 'logo_horizontal_on_dark_url',
}

EMPTY_LOGO_VARIANTS = {key: '' for key in LOGO_VARIANT_FIELDS}

#: M12E — el claro NEUTRO de la plataforma. No pertenece a ningún negocio: un
#: tenant sin tema claro propio usa esto, nunca el de otra empresa.
NEUTRAL_LIGHT_THEME = {
    'light_background_color': '#FFFFFF',
    'light_surface_color': '#F4F4F5',
}


@dataclass(frozen=True)
class CompanyBranding:
    #: Legado. El único que existía, y sigue existiendo: hay tenants que sólo
    #: tienen éste y no deben quedarse sin logo por una fase de branding.
    logo_url: str = ''
    #: M12E — variantes por contraste. Vacío significa «no tengo esta
    #: variante», y es una respuesta legítima: el consumidor cae al nombre de la
    #: empresa antes que dibujar un logo ilegible.
    logos: dict = field(default_factory=lambda: dict(EMPTY_LOGO_VARIANTS))
    colors: dict = field(default_factory=lambda: dict(NEUTRAL_THEME))
    #: M12E — las dos superficies del tema claro. El resto se deriva.
    light_colors: dict = field(
        default_factory=lambda: dict(NEUTRAL_LIGHT_THEME)
    )

    def css_variables(self) -> dict:
        """`{'--brand-primary': '#FFFFFF', ...}` — already validated hex."""
        variables = {
            THEME_CSS_VARIABLES[key]: value
            for key, value in self.colors.items()
            if key in THEME_CSS_VARIABLES
        }
        # M12E — las del tema claro viajan con el prefijo `--brand-light-`, y
        # el CSS elige cuál lee según `data-theme`. Un solo juego de variables
        # obligaría a reescribirlas al cambiar de tema desde JavaScript, que es
        # justo el parpadeo que el script del `<head>` evita.
        variables.update({
            '--brand-light-background': self.light_colors.get(
                'light_background_color', '#FFFFFF',
            ),
            '--brand-light-surface': self.light_colors.get(
                'light_surface_color', '#F4F4F5',
            ),
        })
        return variables


def company_branding(company) -> CompanyBranding:
    """
    Logo and colour palette for `company`, filled in from the neutral theme.

    PER FIELD, not all-or-nothing: a tenant that sets only a background colour
    keeps neutral values for the rest instead of losing the whole palette. Every
    value returned has already passed `validate_hex_color` on the way into the
    database, so it is safe to interpolate into a CSS custom property.
    """
    s = get_company_settings(company)
    colors = dict(NEUTRAL_THEME)
    if s is not None:
        for key in NEUTRAL_THEME:
            value = getattr(s, key, '') or ''
            if value:
                colors[key] = value
    return CompanyBranding(
        logo_url=(s.logo_url if s is not None else '') or '',
        logos={
            key: (getattr(s, field_name, '') or '') if s is not None else ''
            for key, field_name in LOGO_VARIANT_FIELDS.items()
        },
        colors=colors,
        light_colors={
            # Por campo, no todo o nada: un tenant que sólo fija el fondo claro
            # conserva la superficie neutra en vez de perder el tema entero.
            key: (
                (getattr(s, key, '') or '') if s is not None else ''
            ) or NEUTRAL_LIGHT_THEME[key]
            for key in NEUTRAL_LIGHT_THEME
        },
    )


# ---------------------------------------------------------------------------
# Historical snapshot
# ---------------------------------------------------------------------------

def build_identity_snapshot(company, branch=None) -> dict:
    """
    Freeze the commercial identity that belongs on this order's documents.

    Includes the FULFILLMENT BRANCH separately from the legal address, because
    they answer different questions: the legal address is who invoices, the
    branch address is where the customer collects. Printing one where the other
    belongs sends people to the wrong door.

    Contains no secrets, no internal notification address and no configuration —
    only what a customer-facing document shows.
    """
    identity = company_identity(company).as_dict()
    identity['branch'] = None
    if branch is not None:
        identity['branch'] = {
            'name': branch.name or '',
            'address': branch.address or '',
            'phone': branch.phone or '',
            'email': branch.email or '',
        }
    return identity


def order_identity(order) -> CompanyIdentity:
    """
    The identity a document for `order` must show.

    THE SNAPSHOT WINS. It is what the company was when the sale happened, and a
    receipt reprinted a year later has to say the same thing it said the day it
    was issued.

    Falls back to the live company only for orders whose snapshot is empty —
    those created before Phase 3 and not reachable by the backfill. That is the
    best answer available for them, and it is the only case where a document's
    identity can change under it.
    """
    snapshot = getattr(order, 'company_snapshot', None) or {}
    if not snapshot:
        return company_identity(getattr(order, 'company', None))

    return CompanyIdentity(
        name=snapshot.get('name', '') or '',
        legal_name=snapshot.get('legal_name', '') or '',
        tax_id=snapshot.get('tax_id', '') or '',
        legal_address=snapshot.get('legal_address', '') or '',
        city=snapshot.get('city', '') or '',
        phone=snapshot.get('phone', '') or '',
        whatsapp_number=snapshot.get('whatsapp_number', '') or '',
        whatsapp_link=snapshot.get('whatsapp_link', '') or '',
        contact_email=snapshot.get('contact_email', '') or '',
        website_url=snapshot.get('website_url', '') or '',
        logo_url=snapshot.get('logo_url', '') or '',
        warranty_policy_text=snapshot.get('warranty_policy_text', '') or '',
        warranty_policy_url=snapshot.get('warranty_policy_url', '') or '',
    )


def order_pickup_location(order) -> dict:
    """
    Where the customer collects, if this order is a pickup.

    Prefers the branch frozen on the order, then the live fulfillment branch,
    then the company's legal address as a last resort — and says which, so a
    document can label it honestly instead of printing an office address under
    the heading "punto de retiro".
    """
    snapshot = getattr(order, 'company_snapshot', None) or {}
    branch = snapshot.get('branch') or None
    if branch and (branch.get('address') or branch.get('name')):
        return {'source': 'branch', **branch}

    live = getattr(order, 'fulfillment_branch', None)
    if live is not None:
        return {
            'source': 'branch',
            'name': live.name or '',
            'address': live.address or '',
            'phone': live.phone or '',
            'email': live.email or '',
        }

    identity = order_identity(order)
    return {
        'source': 'legal_address',
        'name': identity.name,
        'address': identity.legal_address,
        'phone': identity.phone,
        'email': identity.contact_email,
    }


# ---------------------------------------------------------------------------
# Notification routing
# ---------------------------------------------------------------------------

def order_notification_recipient(order) -> str:
    """
    Where THIS order's new-sale alert goes, or '' for "do not send".

    NO PLATFORM FALLBACK, and that is deliberate. The old global
    `ORDER_NOTIFICATION_EMAIL` held one address, which in this installation is
    the pilot's — so falling back to it would announce a second tenant's sales,
    with the customer's name and phone number, in another company's inbox.

    Silence is recoverable: an operator notices no alerts arrived and fills in
    the field. A misdirected alert is not recoverable, because the data has
    already left.

    Migration 0027 copies the current global value into the pilot's settings, so
    the existing installation keeps receiving exactly what it received before.
    """
    settings_row = get_company_settings(getattr(order, 'company', None))
    if settings_row is None:
        return ''
    return (settings_row.order_notification_email or '').strip()


# ---------------------------------------------------------------------------
# Configuration completeness
# ---------------------------------------------------------------------------

# What a company needs before it can present itself properly. Advisory: nothing
# here blocks an operation. A tenant with no logo still sells.
_COMPLETENESS_CHECKS = (
    ('legal_name', 'Razón social'),
    ('tax_id', 'Identificación fiscal'),
    ('legal_address', 'Dirección legal'),
    ('phone', 'Teléfono'),
    ('contact_email', 'Email de contacto'),
    ('logo_url', 'Logo'),
    ('warranty_policy_text', 'Política de garantía'),
    ('order_notification_email', 'Email de notificación de ventas'),
)


def company_configuration_status(company) -> dict:
    """
    What this company still has to configure. Informative, never blocking.

    Two entries are singled out as CONSEQUENTIAL because their absence changes
    behaviour rather than appearance: with no notification email the company
    receives no new-sale alerts, and with no fulfillment branch it cannot check
    out at all. The rest are cosmetic gaps.
    """
    from .tenancy import company_fulfillment_branch

    identity = company_identity(company)
    settings_row = get_company_settings(company)

    missing = []
    for field_name, label in _COMPLETENESS_CHECKS:
        if field_name in ('order_notification_email',):
            value = (
                getattr(settings_row, field_name, '') if settings_row else ''
            )
        else:
            value = getattr(identity, field_name, '')
        if not (value or '').strip():
            missing.append({'field': field_name, 'label': label})

    has_fulfillment_branch = company_fulfillment_branch(company) is not None
    if not has_fulfillment_branch:
        missing.append({
            'field': 'default_inventory_branch', 'label': 'Sucursal de despacho',
        })

    consequential = [
        m['field'] for m in missing
        if m['field'] in ('order_notification_email', 'default_inventory_branch')
    ]

    return {
        'has_settings': settings_row is not None,
        'missing': missing,
        'missing_count': len(missing),
        'consequential': consequential,
        'is_complete': not missing,
    }
