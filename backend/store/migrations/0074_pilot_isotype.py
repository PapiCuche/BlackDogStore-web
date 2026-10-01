"""
M12F — enlazar el isotipo del tenant piloto.

POR QUÉ EXISTE. En 320 px el lockup horizontal mide 220 px de ancho mínimo según
el manual, y a su lado tienen que caber el carrito, el selector de tema y el
menú. No caben. La salida NO es encoger el horizontal por debajo de su mínimo ni
aplastarlo —«deformar» y «reducir bajo el mínimo» son dos de las seis
alteraciones que prohíbe la diapositiva 23— sino usar la pieza que el manual ya
diseñó para ese tamaño: el isotipo, mínimo digital 48 px.

DE DÓNDE SALEN LOS ARCHIVOS. Del mismo manual v3.0, extraídos en M12E junto con
las otras cuatro composiciones. Ya estaban en el repositorio sin campo que los
enlazara.

SÓLO EL PILOTO, Y SÓLO SI ESTÁN VACÍAS.
"""

from django.db import migrations

PILOT_SLUG = 'black-dog-store'

PILOT_ISOTYPE = {
    'logo_isotype_on_light_url': '/assets/branding/logo-isotype-on-light.png',
    'logo_isotype_on_dark_url': '/assets/branding/logo-isotype-on-dark.png',
}


def link_isotype(apps, schema_editor):
    Company = apps.get_model('store', 'Company')
    CompanySettings = apps.get_model('store', 'CompanySettings')

    company = Company.objects.filter(slug=PILOT_SLUG).first()
    if company is None:
        return
    row = CompanySettings.objects.filter(company=company).first()
    if row is None:
        return

    written = []
    for field, url in PILOT_ISOTYPE.items():
        if (getattr(row, field, '') or '').strip():
            continue
        setattr(row, field, url)
        written.append(field)

    if written:
        row.save(update_fields=written)
        print(
            f'\n  M12F — {len(written)} variante(s) de isotipo enlazadas '
            f'en el tenant piloto ({PILOT_SLUG})\n'
        )


def unlink(apps, schema_editor):
    """Retira SÓLO lo que esta migración pudo escribir."""
    Company = apps.get_model('store', 'Company')
    CompanySettings = apps.get_model('store', 'CompanySettings')

    company = Company.objects.filter(slug=PILOT_SLUG).first()
    if company is None:
        return
    row = CompanySettings.objects.filter(company=company).first()
    if row is None:
        return

    cleared = []
    for field, url in PILOT_ISOTYPE.items():
        if (getattr(row, field, '') or '').strip() == url:
            setattr(row, field, '')
            cleared.append(field)
    if cleared:
        row.save(update_fields=cleared)


class Migration(migrations.Migration):

    dependencies = [('store', '0073_isotype_variants')]

    operations = [migrations.RunPython(link_isotype, unlink)]
