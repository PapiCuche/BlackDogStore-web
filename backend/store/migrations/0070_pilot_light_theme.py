"""
M12E — el tema claro del piloto, según su manual v3.0.

El manual (diapositiva 24) fija BLANCO CÁLIDO `#F5F3EE` como la superficie de
respiración de la marca, con el 30% de la jerarquía. No es blanco puro: sobre
negro, el blanco puro da el contraste duro de una interfaz de sistema; el cálido
es lo que hace que se lea como marca. En un tema CLARO ese matiz es todavía más
visible, porque deja de ser un detalle del texto y pasa a ser la página entera.

La superficie elevada se deriva del GRIS SOPORTE `#D5D2CB`, aclarado hacia el
fondo para que una tarjeta se distinga sin convertirse en un beige distinto. No
se inventa un sexto color: se usa un punto entre dos aprobados.

SÓLO EL PILOTO, Y SÓLO SI ESTÁ VACÍO. Un tenant sin tema claro propio sigue
usando el claro NEUTRO de la plataforma —blanco y gris, de ningún negocio—, que
es lo que impide que la marca de una tienda se convierta en el default de todas.
"""

from django.db import migrations

PILOT_SLUG = 'black-dog-store'

#: Manual v3.0, diapositiva 24.
#:   BLANCO CÁLIDO  #F5F3EE   respiración y claridad
#:   GRIS SOPORTE   #D5D2CB   información secundaria
#:
#: La superficie es el punto medio entre ambos: distinta del fondo lo justo para
#: que una tarjeta se lea como tarjeta, sin ser un color nuevo.
PILOT_LIGHT_THEME = {
    'light_background_color': '#F5F3EE',
    'light_surface_color': '#EDEAE3',
}


def apply_light_theme(apps, schema_editor):
    Company = apps.get_model('store', 'Company')
    CompanySettings = apps.get_model('store', 'CompanySettings')

    company = Company.objects.filter(slug=PILOT_SLUG).first()
    if company is None:
        return
    row = CompanySettings.objects.filter(company=company).first()
    if row is None:
        return

    written = []
    for field, value in PILOT_LIGHT_THEME.items():
        if (getattr(row, field, '') or '').strip():
            # Ya lo eligió el taller.
            continue
        setattr(row, field, value)
        written.append(field)

    if written:
        row.save(update_fields=written)
        print(f'\n  M12E — tema claro del manual aplicado al piloto ({PILOT_SLUG})')


def clear_light_theme(apps, schema_editor):
    Company = apps.get_model('store', 'Company')
    CompanySettings = apps.get_model('store', 'CompanySettings')
    company = Company.objects.filter(slug=PILOT_SLUG).first()
    if company is None:
        return
    row = CompanySettings.objects.filter(company=company).first()
    if row is None:
        return
    cleared = [f for f, v in PILOT_LIGHT_THEME.items()
               if (getattr(row, f, '') or '') == v]
    for field in cleared:
        setattr(row, field, '')
    if cleared:
        row.save(update_fields=cleared)


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0069_light_theme_colors'),
    ]

    operations = [
        migrations.RunPython(apply_light_theme, clear_light_theme),
    ]
