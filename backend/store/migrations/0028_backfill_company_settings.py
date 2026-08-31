"""
Phase 3, step 2 of 2 — turn the pilot's compiled-in identity into data.

WHAT THIS MIGRATION IS ACTUALLY FOR
-----------------------------------
Until Phase 3, `email_services.py`, `pdf_services.py` and `sales_note_services.py`
each held six module constants naming one specific business:

    _STORE_NAME = "Black Dog Store"
    _STORE_RUC  = "20610159886"
    ...

Those constants have been deleted from the runtime. This migration writes the
same values into the PILOT COMPANY'S OWN `CompanySettings` row, so that
installation keeps sending exactly the emails and printing exactly the PDFs it
sent and printed yesterday — but now because the data says so, not because the
code does.

The literals below are therefore HISTORICAL DATA, and this is the correct place
for them. A migration records what was true when it ran; it is not business
logic, and a fresh installation sold to somebody else gets these values only if
it also inherits this installation's pilot tenant, which is the pre-existing
bootstrap debt tracked in docs/saas-multiempresa.md.

HOW THE PILOT IS IDENTIFIED
---------------------------
By SLUG, `black-dog-store` — the same stable identifier migration 0015 created it
with, and the same one 0017 used to seed its presets. Not "the first company",
which would silently claim whichever tenant happened to have the lowest id on an
installation that had already onboarded others.

If no company with that slug exists — a database bootstrapped differently — this
migration writes NOTHING for identity and simply gives every company a neutral
settings row. It never guesses which tenant the values belong to.

EVERY OTHER COMPANY gets a settings row with the NEUTRAL theme and empty
commercial fields. Under no circumstances does a second tenant inherit the
pilot's name, tax id, address, phone or colours.
"""

from django.conf import settings as django_settings
from django.db import migrations

# The slug migration 0015 created the pilot with. Stable identifier, not a guess.
_PILOT_SLUG = 'black-dog-store'

# HISTORICAL DATA — the exact values that were compiled into the services until
# Phase 3. Reproduced here so the pilot's documents do not change across the
# upgrade. Nothing in the application layer may import these.
_PILOT_IDENTITY = {
    'contact_email': '',
    'phone': '+51 936 449 536',
    'whatsapp_number': '51936449536',
    'website_url': '',
    'facebook_url': 'https://www.facebook.com/Blackdogstore.pe',
    'instagram_url': 'https://www.instagram.com/blackdogstore_pe',
    'legal_address': 'Octavio Muñoz Najar 238, Tienda 104',
    'city': 'Arequipa, Perú',
    'country_code': 'PE',
    'warranty_policy_text': (
        'La garantía se aplicará según la condición del producto y los términos '
        'informados al momento de la compra.'
    ),
}

# The pilot's current visual identity, so its storefront looks unchanged. These
# are the values already in frontend/app/globals.css.
_PILOT_THEME = {
    'primary_color': '#FFFFFF',
    'accent_color': '#6B7280',
    'background_color': '#080808',
    'surface_color': '#111111',
    'text_color': '#FFFFFF',
    'border_color': '#272727',
}

# Neutral platform theme for every other tenant. Kept in sync with
# store.company_settings.NEUTRAL_THEME by a test — a migration may not import it,
# because a migration has to reproduce what it did when it ran.
_NEUTRAL_THEME = {
    'primary_color': '#FFFFFF',
    'accent_color': '#A1A1AA',
    'background_color': '#0A0A0A',
    'surface_color': '#141414',
    'text_color': '#FAFAFA',
    'border_color': '#262626',
}


def _identity_snapshot(company, settings_row, branch):
    """
    Same shape as `company_settings.build_identity_snapshot()`.

    Duplicated rather than imported for the usual migration reason: this has to
    keep producing the Phase 3 shape forever, even after that function changes.
    A test asserts the two agree today.
    """
    number = (settings_row.whatsapp_number or '') if settings_row else ''
    digits = ''.join(ch for ch in number if ch.isdigit())
    link = f'https://wa.me/{digits}' if 8 <= len(digits) <= 15 else ''

    snapshot = {
        'name': company.name or '',
        'legal_name': company.legal_name or '',
        'tax_id': company.tax_id or '',
        'legal_address': (settings_row.legal_address or '') if settings_row else '',
        'city': (settings_row.city or '') if settings_row else '',
        'phone': (settings_row.phone or '') if settings_row else '',
        'whatsapp_number': number,
        'whatsapp_link': link,
        'contact_email': (settings_row.contact_email or '') if settings_row else '',
        'website_url': (settings_row.website_url or '') if settings_row else '',
        'logo_url': (settings_row.logo_url or '') if settings_row else '',
        'warranty_policy_text': (
            (settings_row.warranty_policy_text or '') if settings_row else ''
        ),
        'warranty_policy_url': (
            (settings_row.warranty_policy_url or '') if settings_row else ''
        ),
        'branch': None,
    }
    if branch is not None:
        snapshot['branch'] = {
            'name': branch.name or '',
            'address': branch.address or '',
            'phone': branch.phone or '',
            'email': branch.email or '',
        }
    return snapshot


def create_settings(apps, schema_editor):
    Company = apps.get_model('store', 'Company')
    CompanySettings = apps.get_model('store', 'CompanySettings')

    # The global recipient this installation has been using. Copied into the
    # PILOT'S settings only — it is one address, and it belongs to whoever
    # configured it, which on this installation is the pilot. Giving it to every
    # tenant would route their sales into somebody else's inbox, which is the
    # exact leak Phase 3 closes.
    global_notification = (
        getattr(django_settings, 'ORDER_NOTIFICATION_EMAIL', '') or ''
    ).strip()

    for company in Company.objects.all().iterator():
        is_pilot = (company.slug or '') == _PILOT_SLUG

        defaults = {'currency': 'PEN'}
        if is_pilot:
            defaults.update(_PILOT_IDENTITY)
            defaults.update(_PILOT_THEME)
            defaults['order_notification_email'] = global_notification
        else:
            defaults.update(_NEUTRAL_THEME)

        CompanySettings.objects.get_or_create(company=company, defaults=defaults)


def backfill_order_snapshots(apps, schema_editor):
    """
    Freeze each existing order's seller identity.

    Historical orders are stamped with their company's identity AS OF NOW, which
    for the pilot is the identity that was compiled into the services when those
    orders were placed — so nothing changes for them. For any other tenant it is
    whatever they have configured, which is the best answer available: the
    platform never recorded what they looked like at the time, and inventing one
    would be worse than recording the truth we have.

    Documented limitation: orders placed before Phase 3 by a NON-pilot tenant
    receive today's identity, not the one in force on the day of the sale.
    """
    Company = apps.get_model('store', 'Company')
    CompanySettings = apps.get_model('store', 'CompanySettings')
    Order = apps.get_model('store', 'Order')

    settings_by_company = {
        row.company_id: row for row in CompanySettings.objects.all()
    }
    companies = {c.pk: c for c in Company.objects.all()}

    orders = (
        Order.objects
        .filter(company_snapshot={})
        .select_related('fulfillment_branch')
        .iterator()
    )
    for order in orders:
        company = companies.get(order.company_id)
        if company is None:
            continue
        snapshot = _identity_snapshot(
            company,
            settings_by_company.get(order.company_id),
            order.fulfillment_branch,
        )
        Order.objects.filter(pk=order.pk).update(company_snapshot=snapshot)


def unmigrate(apps, schema_editor):
    """
    Reverse: remove ONLY what this migration created.

    The settings rows and the snapshots are creations of 0028, so they go. No
    order, product, movement or company is touched — a rollback loses no history,
    it only returns the identity to being compiled in, which is where the
    previous version of the code expects it.
    """
    CompanySettings = apps.get_model('store', 'CompanySettings')
    Order = apps.get_model('store', 'Order')

    Order.objects.update(company_snapshot={})
    CompanySettings.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0027_company_settings'),
    ]

    operations = [
        migrations.RunPython(create_settings, unmigrate),
        migrations.RunPython(backfill_order_snapshots, migrations.RunPython.noop),
    ]
