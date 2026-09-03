"""
M12E — enlazar las variantes por contraste del tenant piloto.

EL DEFECTO QUE ESTO CIERRA. Un logotipo negro sobre una cabecera negra es
invisible. No se arregla haciéndolo más grande y no se arregla con
`filter: invert(1)`: invertir el logo de un tenant arbitrario produce basura con
la misma confianza con la que produciría un acierto, porque no sabemos su
geometría ni sus colores. Se arregla con la versión cromática que el manual de
ESA marca autorice.

DE DÓNDE SALEN LOS ARCHIVOS. Del propio manual v3.0, que trae las cuatro
composiciones en sus dos versiones. No se han redibujado, ni recortado, ni
recoloreado: se extrajeron y se convirtieron de escala de grises con alfa a RGBA
conservando el canal alfa píxel a píxel, que es un cambio de formato y no de
diseño. Los originales del repositorio siguen intactos.

  vertical negra    → superficies claras    (manual, diapositiva 20)
  vertical blanca   → superficies oscuras
  horizontal negra  → cabeceras claras      (diapositiva 17: horizontal = cabecera)
  horizontal blanca → cabeceras oscuras

SÓLO EL PILOTO, Y SÓLO SI ESTÁN VACÍAS. Ningún otro tenant se toca, y si el
taller ya subió alguna variante, ésa es la suya.
"""

from django.db import migrations

PILOT_SLUG = 'black-dog-store'

#: Campo del modelo -> ruta servida por el frontend. Relativas a propósito: una
#: URL absoluta convierte un cambio de host en un logo roto.
PILOT_LOGO_VARIANTS = {
    'logo_on_light_url': '/assets/branding/logo-vertical-on-light.png',
    'logo_on_dark_url': '/assets/branding/logo-vertical-on-dark.png',
    'logo_horizontal_on_light_url': '/assets/branding/logo-horizontal-on-light.png',
    'logo_horizontal_on_dark_url': '/assets/branding/logo-horizontal-on-dark.png',
}


def link_variants(apps, schema_editor):
    Company = apps.get_model('store', 'Company')
    CompanySettings = apps.get_model('store', 'CompanySettings')

    company = Company.objects.filter(slug=PILOT_SLUG).first()
    if company is None:
        return
    row = CompanySettings.objects.filter(company=company).first()
    if row is None:
        return

    written = []
    for field, url in PILOT_LOGO_VARIANTS.items():
        if (getattr(row, field, '') or '').strip():
            # Ya hay una. Es del taller.
            continue
        setattr(row, field, url)
        written.append(field)

    if written:
        row.save(update_fields=written)
        print(
            f'\n  M12E — {len(written)} variante(s) de logotipo enlazadas en el '
            f'tenant piloto ({PILOT_SLUG})'
        )


def unlink_variants(apps, schema_editor):
    Company = apps.get_model('store', 'Company')
    CompanySettings = apps.get_model('store', 'CompanySettings')
    company = Company.objects.filter(slug=PILOT_SLUG).first()
    if company is None:
        return
    row = CompanySettings.objects.filter(company=company).first()
    if row is None:
        return
    cleared = []
    for field, url in PILOT_LOGO_VARIANTS.items():
        if (getattr(row, field, '') or '') == url:
            setattr(row, field, '')
            cleared.append(field)
    if cleared:
        row.save(update_fields=cleared)


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0067_logo_contrast_variants'),
    ]

    operations = [
        migrations.RunPython(link_variants, unlink_variants),
    ]
