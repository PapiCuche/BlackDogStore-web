"""
M12D — apuntar la configuración del piloto a su logotipo oficial.

El asset lleva en el repositorio desde antes de esta fase —
`/assets/branding/logo.png`, la composición VERTICAL del manual — y nada le
apuntaba: `logo_url` estaba vacío, así que el storefront caía al nombre en
tipografía. El archivo estaba ahí y no se veía.

DÓNDE QUEDA BIEN Y DÓNDE ES UN COMPROMISO

El manual (diapositiva 17) asigna la composición vertical a portadas y piezas
institucionales, y la horizontal a cabeceras. La configuración expone UN
`logo_url`, así que el mismo archivo se usa en los dos sitios:

  · HERO — correcto. Se dibuja a 256-320 px, muy por encima de los 140 px de
    ancho mínimo que la diapositiva 19 exige para la vertical.

  · CABECERA — compromiso declarado. A 48-56 px de alto la vertical queda por
    debajo de ese mínimo. Lo que lo resuelve es `logo_horizontal_url`, que es
    deuda registrada a propósito: añadir un campo al esquema del SaaS por un
    refresh visual ensancharía M12D sin necesidad.

    No se recorta el archivo para fabricar una variante, no se recolorea y no se
    recrea el lettering. Se usa el asset aprobado de la forma menos incorrecta
    que la configuración actual permite, y queda dicho.

SÓLO SI ESTÁ VACÍO. Si el taller ya subió su logo, ése es el suyo.
"""

from django.db import migrations

PILOT_SLUG = 'black-dog-store'
#: Servido por el frontend desde `public/assets/branding/`. Ruta relativa a
#: propósito: el dominio cambia entre desarrollo, staging y producción, y una
#: URL absoluta convertiría un cambio de host en un logo roto.
PILOT_LOGO_URL = '/assets/branding/logo.png'


def point_at_the_official_logo(apps, schema_editor):
    Company = apps.get_model('store', 'Company')
    CompanySettings = apps.get_model('store', 'CompanySettings')

    company = Company.objects.filter(slug=PILOT_SLUG).first()
    if company is None:
        return
    row = CompanySettings.objects.filter(company=company).first()
    if row is None:
        return
    if (row.logo_url or '').strip():
        # Ya hay uno. Es del taller.
        return
    row.logo_url = PILOT_LOGO_URL
    row.save(update_fields=['logo_url'])
    print(f'\n  M12D — logotipo oficial enlazado en el tenant piloto ({PILOT_SLUG})')


def unpoint(apps, schema_editor):
    Company = apps.get_model('store', 'Company')
    CompanySettings = apps.get_model('store', 'CompanySettings')
    company = Company.objects.filter(slug=PILOT_SLUG).first()
    if company is None:
        return
    row = CompanySettings.objects.filter(company=company).first()
    if row is not None and (row.logo_url or '') == PILOT_LOGO_URL:
        row.logo_url = ''
        row.save(update_fields=['logo_url'])


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0065_pilot_brand_palette'),
    ]

    operations = [
        migrations.RunPython(point_at_the_official_logo, unpoint),
    ]
