"""
M12F — mover a datos el contenido comercial que el piloto tenía compilado.

EL DEFECTO QUE CIERRA. La portada llevaba un `<h2>` con «iPhone 17 Pro Max»
dentro del código. Cambiar de campaña exigía tocar un componente y desplegar; no
cambiarla dejaba una preventa caducada en portada, que es peor — nadie despliega
para borrar algo que ya no existe.

LO QUE SE ARRASTRA Y LO QUE NO
------------------------------
Se arrastra el copy del titular, que es el APROBADO POR EL MANUAL v3.0 y estaba
comentado como tal en el componente.

NO se arrastran las cifras que el bloque anterior afirmaba:

    «Separa el tuyo con solo $300 de reserva»
    «Disponible · 256GB · 512GB · 1TB»

Un precio de reserva y una lista de capacidades son afirmaciones comerciales
concretas. No hay dato aprobado que las respalde para el modelo nuevo, y
copiarlas del bloque viejo cambiándole el número al teléfono sería inventarlas
con aspecto de continuidad. El texto queda neutro y el taller escribe las suyas
desde el panel cuando las tenga.

SÓLO EL PILOTO. Identificado por su slug literal, congelado aquí. Ninguna otra
empresa se toca, y si el taller ya escribió su propio contenido, ése gana.
"""

from django.db import migrations
from django.utils import timezone

PILOT_SLUG = 'black-dog-store'

#: Copy del manual v3.0, el mismo que el componente llevaba compilado con un
#: comentario explicando por qué NO dice «El Mejor Servicio Apple en Perú»:
#: «el mejor» es un superlativo indemostrable y «Servicio Apple» se lee como
#: servicio oficial de Apple, que el manual prohíbe afirmar sin acreditación.
#: Los saltos de línea son composición del titular, no marcado.
PILOT_HERO = {
    'hero_title': 'Tu Apple,\ncon respaldo\nespecializado',
    'hero_subtitle': (
        'Compra, renueva o repara tu equipo Apple con asesoría '
        'especializada y respaldo postventa.'
    ),
    'hero_primary_cta_label': 'Ver catálogo',
    'hero_primary_cta_url': '/product',
}

PILOT_CAMPAIGN = {
    'slot': 'home_bottom_promo',
    'badge': 'Preventa',
    'title': 'iPhone 18 Pro Max',
    'subtitle': '',
    'body': 'Consulta disponibilidad y condiciones de reserva.',
    'cta_label': 'Consultar disponibilidad',
    'priority': 0,
}


def seed(apps, schema_editor):
    Company = apps.get_model('store', 'Company')
    CompanySettings = apps.get_model('store', 'CompanySettings')
    PageSettings = apps.get_model('store', 'StorefrontPageSettings')
    Campaign = apps.get_model('store', 'StorefrontCampaign')

    company = Company.objects.filter(slug=PILOT_SLUG).first()
    if company is None:
        # Instalación limpia sin el piloto. No es un fallo: esta migración
        # siembra contenido de UN tenant concreto, y si no existe no hay nada
        # que sembrar.
        return

    # --- portada ---------------------------------------------------------
    page, _ = PageSettings.objects.get_or_create(company=company)
    written = []
    for field, value in PILOT_HERO.items():
        if (getattr(page, field, '') or '').strip():
            # Ya hay texto. Es del taller.
            continue
        setattr(page, field, value)
        written.append(field)
    if written:
        page.save(update_fields=written)

    # --- campaña ---------------------------------------------------------
    # Si el taller ya tiene CUALQUIER campaña en este slot, no se toca: sembrar
    # una segunda competiría con la suya por la portada.
    if Campaign.objects.filter(
        company=company, slot=PILOT_CAMPAIGN['slot'],
    ).exists():
        return

    # El enlace se CONSTRUYE aquí con el formato congelado en vez de importar
    # `build_whatsapp_link`: una migración tiene que seguir haciendo lo mismo
    # dentro de cinco años, y para eso no puede depender de lo que el código
    # signifique entonces. Mismas reglas que la función viva: sólo dígitos, y
    # una longitud plausible de teléfono internacional.
    settings_row = CompanySettings.objects.filter(company=company).first()
    digits = ''.join(
        ch for ch in str(getattr(settings_row, 'whatsapp_number', '') or '')
        if ch.isdigit()
    )
    # Sin WhatsApp configurado, el botón lleva al catálogo: una ruta del propio
    # sitio que existe siempre. Nunca un destino inventado.
    cta_url = f'https://wa.me/{digits}' if 8 <= len(digits) <= 15 else '/product'

    now = timezone.now()
    Campaign.objects.create(
        company=company,
        status='published',
        published_at=now,
        cta_url=cta_url,
        created_by=None,       # sembrada por el sistema, no por una persona
        **PILOT_CAMPAIGN,
    )
    print(
        f'\n  M12F — portada y campaña de preventa sembradas para el tenant '
        f'piloto ({PILOT_SLUG})\n'
    )


def unseed(apps, schema_editor):
    """
    Retira SÓLO lo que esta migración pudo escribir.

    La campaña se borra únicamente si sigue siendo la sembrada —sin autor y con
    el título con el que nació—. Una campaña que alguien editó es suya.
    """
    Company = apps.get_model('store', 'Company')
    PageSettings = apps.get_model('store', 'StorefrontPageSettings')
    Campaign = apps.get_model('store', 'StorefrontCampaign')

    company = Company.objects.filter(slug=PILOT_SLUG).first()
    if company is None:
        return

    Campaign.objects.filter(
        company=company, slot=PILOT_CAMPAIGN['slot'],
        title=PILOT_CAMPAIGN['title'], created_by__isnull=True,
    ).delete()

    page = PageSettings.objects.filter(company=company).first()
    if page is None:
        return
    cleared = []
    for field, value in PILOT_HERO.items():
        if (getattr(page, field, '') or '') == value:
            setattr(page, field, '')
            cleared.append(field)
    if cleared:
        page.save(update_fields=cleared)


class Migration(migrations.Migration):

    dependencies = [('store', '0074_pilot_isotype')]

    operations = [migrations.RunPython(seed, unseed)]
