"""
M12D — la paleta del manual de marca v3.0, aplicada AL TENANT PILOTO.

    BLACK DOG STORE ES EL PILOTO, NO EL BRANDING DEL SaaS.

Esta migración no toca `globals.css` ni ningún default de la plataforma. Escribe
en la configuración de UNA empresa, identificada por el slug que 0015 creó, y
deja a todas las demás exactamente como estaban. Un tenant nuevo sigue naciendo
con el tema neutro, que no pertenece a ningún negocio a propósito: una página
sin marca es un problema visible; una vistiendo los colores de otra empresa, no.

QUÉ CAMBIA Y POR QUÉ

El manual v3.0 (BDS-MAR-001) fija una paleta que el tema que 0028 escribió no
reproduce. La diferencia no es cosmética:

  · el blanco es CÁLIDO `#F5F3EE`, no `#FFFFFF`. Un blanco puro sobre negro
    produce el contraste duro de una interfaz de sistema; el cálido es lo que
    hace que la marca se lea premium en vez de técnica.
  · aparece un DORADO `#C8A45D` que antes no existía en la configuración. El
    manual lo reserva para precio, garantía y CTA — 3-5% de la pieza — y lo dice
    con estas palabras: «la sensación premium depende de usar menos dorado».
  · el gris de soporte `#D5D2CB` es cálido también, no el `#6B7280` azulado que
    había.

EL DORADO VA EN `accent_color`, y no en `primary_color`. `primary_color` es el
color de los botones principales y de los bloques sólidos: poner ahí el dorado
lo convertiría en el color dominante de cada pantalla, que es exactamente lo
contrario de lo que el manual manda. El acento es donde el sistema de tokens lo
usa con moderación.

SÓLO SI SIGUE INTACTO

Se compara contra la forma EXACTA que 0028 escribió. Si el taller ya ajustó sus
colores desde la pantalla de configuración, esos valores son una decisión suya y
esta migración no está autorizada a sobrescribirlos — la misma regla que M12B.1
estableció para los presets de roles, por la misma razón.

SEGURA SI EL PILOTO NO EXISTE. Una base arrancada de otra forma simplemente no
encuentra el slug y no hace nada.
"""

from django.db import migrations

#: El slug que 0015 creó. Identificador estable, no una conjetura.
PILOT_SLUG = 'black-dog-store'

#: La forma EXACTA que 0028 dejó en la configuración del piloto. Congelada aquí:
#: una migración reconoce el pasado con su propio literal, nunca importando lo
#: que el código significa hoy. Es la lección que M12B.1 costó una subfase.
_PREVIOUS_PILOT_THEME = {
    'primary_color': '#FFFFFF',
    'accent_color': '#6B7280',
    'background_color': '#080808',
    'surface_color': '#111111',
    'text_color': '#FFFFFF',
    'border_color': '#272727',
}

#: Manual de marca v3.0, diapositiva 24.
#:
#:   NEGRO BLACK DOG  #0A0A0A   fondos, titulares, autoridad      60%
#:   BLANCO CÁLIDO    #F5F3EE   respiración y claridad            30%
#:   DORADO PREMIUM   #C8A45D   precio, garantía y CTA            3-5%
#:   CARBÓN           #232323   superficies elevadas
#:   GRIS SOPORTE     #D5D2CB   información secundaria            5-7%
_BRAND_V3_THEME = {
    'primary_color': '#F5F3EE',
    'accent_color': '#C8A45D',
    'background_color': '#0A0A0A',
    'surface_color': '#232323',
    'text_color': '#F5F3EE',
    'border_color': '#3A3A3A',
}

_FIELDS = tuple(_BRAND_V3_THEME)


def apply_brand_palette(apps, schema_editor):
    Company = apps.get_model('store', 'Company')
    CompanySettings = apps.get_model('store', 'CompanySettings')

    company = Company.objects.filter(slug=PILOT_SLUG).first()
    if company is None:
        # Base arrancada de otra forma. No es un error: es una plataforma sin
        # este piloto, y no hay nada que hacer.
        return

    settings_row = CompanySettings.objects.filter(company=company).first()
    if settings_row is None:
        return

    current = {field: (getattr(settings_row, field) or '').upper() for field in _FIELDS}
    expected = {field: value.upper() for field, value in _PREVIOUS_PILOT_THEME.items()}
    if current != expected:
        # El taller ya eligió sus colores. Son suyos.
        return

    for field, value in _BRAND_V3_THEME.items():
        setattr(settings_row, field, value)
    settings_row.save(update_fields=list(_FIELDS))
    print(
        '\n  M12D — paleta del manual v3.0 aplicada al tenant piloto '
        f'({PILOT_SLUG})'
    )


def restore_previous_palette(apps, schema_editor):
    """
    Vuelve atrás sólo si la paleta sigue siendo exactamente la que esta
    migración escribió. Un taller que la ajustó después conserva lo suyo.
    """
    Company = apps.get_model('store', 'Company')
    CompanySettings = apps.get_model('store', 'CompanySettings')

    company = Company.objects.filter(slug=PILOT_SLUG).first()
    if company is None:
        return
    settings_row = CompanySettings.objects.filter(company=company).first()
    if settings_row is None:
        return

    current = {field: (getattr(settings_row, field) or '').upper() for field in _FIELDS}
    written = {field: value.upper() for field, value in _BRAND_V3_THEME.items()}
    if current != written:
        return

    for field, value in _PREVIOUS_PILOT_THEME.items():
        setattr(settings_row, field, value)
    settings_row.save(update_fields=list(_FIELDS))


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0064_repair_evidence'),
    ]

    operations = [
        migrations.RunPython(apply_brand_palette, restore_previous_palette),
    ]
