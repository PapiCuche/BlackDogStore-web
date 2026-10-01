"""
M12F.1 — el contenido de /services pasa a ser dato del tenant, RECONCILIADO.

QUÉ SE ESTÁ CORRIGIENDO
-----------------------
La página de servicios publicaba como hechos varias afirmaciones que el propio
proyecto contradice o no respalda:

    «5.000+ dispositivos reparados»
        Sin ninguna fuente dentro del proyecto. No se traslada: cambiar una
        cifra de sitio no le da respaldo.

    «Todos nuestros servicios incluyen 6 meses de garantía»
        CONTRADICE al manual v3.0 del propio piloto, que dice que la cobertura
        de servicios técnicos DEPENDE del producto o reparación. Los seis meses
        son de los equipos seminuevos; alguien los trasladó a las reparaciones.
        Y contradice también la política que el tenant tiene configurada en su
        propia fila: «se aplicará según la condición del producto y los términos
        informados».

    «Baterías Nasan Originales» · «certificado de autenticidad» · «Certificado
    Nasan — Abril 2025»
        El manual exige publicar «original» ÚNICAMENTE cuando exista
        trazabilidad o validación. No hay ningún documento de Nasan en el
        proyecto. Queda el hecho comprobable —con qué marca se trabaja— y se
        retira la certificación hasta que exista el documento.

    «Sin msg / Pieza reparada»
        Depende del firmware del equipo, que es de un tercero y cambia con cada
        actualización. No se puede garantizar.

    «Diagnóstico gratuito» · «S/ 0»
        Sin política configurada que lo respalde. No se siembra. Si el taller lo
        ofrece, lo escribe desde el panel y responde por ello.

LOS TIEMPOS SÍ SE CONSERVAN, COMO ESTIMACIONES
----------------------------------------------
El manual pide informar «que el tiempo y costo pueden variar según equipo, falla
y disponibilidad de repuestos». Una estimación etiquetada como tal es
información; presentada como dato es una promesa. El campo se llama
`estimated_time_text` y la interfaz lo rotula.

MÉTRICAS: NINGUNA
-----------------
No se siembra ni una. Un bloque de cifras vacío no se dibuja, y eso es
exactamente lo que debe pasar mientras nadie responda por ellas.
"""

from django.db import migrations

PILOT_SLUG = 'black-dog-store'

SERVICES = [
    {
        'title': 'Cambio de Pantalla',
        'description': (
            'Pantallas OLED/LCD con calibración de color y brillo. Revisamos el '
            'equipo, explicamos el diagnóstico y confirmamos el costo antes de '
            'reparar.'
        ),
        'devices_text': 'iPhone · iPad',
        'estimated_time_text': '2–3 horas',
        'highlight': 'Costo confirmado antes',
    },
    {
        'title': 'Cambio de Batería',
        # Antes: «Baterías Nasan con certificado de autenticidad». Se conserva
        # con qué marca se trabaja —comprobable— y se retira la certificación.
        'description': (
            'Trabajamos con baterías Nasan. Antes de reparar te indicamos qué '
            'repuesto se usará y en qué condiciones.'
        ),
        'devices_text': 'iPhone · iPad · MacBook',
        'estimated_time_text': '1–2 horas',
        'highlight': '',
    },
    {
        'title': 'Cambio de Tapa Trasera',
        'description': (
            'Cambio de tapa trasera con acabado cuidado. Te explicamos el '
            'alcance del trabajo y el costo antes de empezar.'
        ),
        'devices_text': 'iPhone',
        'estimated_time_text': '2–3 horas',
        'highlight': 'Alcance explicado',
    },
    {
        'title': 'Cambio de Glass',
        'description': (
            'Cristal frontal de protección. Instalación limpia, sin polvo ni '
            'burbujas.'
        ),
        'devices_text': 'iPhone · iPad',
        'estimated_time_text': '1 hora',
        'highlight': '',
    },
    {
        'title': 'Daño por Líquidos',
        # Antes empezaba por «Diagnóstico gratuito y…».
        'description': (
            'Limpieza ultrasónica de la placa para recuperar el dispositivo '
            'tras contacto con agua u otros líquidos. El resultado depende del '
            'alcance del daño y se evalúa antes de intervenir.'
        ),
        'devices_text': 'iPhone · iPad · MacBook',
        'estimated_time_text': '24–48 horas',
        'highlight': '',
    },
    {
        'title': 'Diagnóstico Técnico',
        # Antes: «Sin costo» + etiqueta «Gratis».
        'description': (
            'Evaluación del estado del dispositivo: hardware, batería, '
            'conectores y sistema operativo. Te explicamos qué encontramos y '
            'qué opciones hay antes de decidir nada.'
        ),
        'devices_text': 'iPhone · iPad · MacBook · Apple Watch',
        'estimated_time_text': '30 min',
        'highlight': '',
    },
    {
        'title': 'Recuperación de Datos',
        'description': (
            'Recuperamos fotos, contactos, notas y archivos de dispositivos '
            'dañados, con pantalla rota o que no encienden. El resultado '
            'depende del estado del equipo.'
        ),
        'devices_text': 'iPhone · iPad · MacBook',
        'estimated_time_text': '1–5 días',
        'highlight': '',
    },
    {
        'title': 'Software y Sistema',
        'description': (
            'Actualizaciones de iOS/macOS, configuración de iCloud, '
            'recuperación de Apple ID, restauración DFU y resolución de errores.'
        ),
        'devices_text': 'iPhone · iPad · MacBook',
        'estimated_time_text': '1 hora',
        'highlight': '',
    },
]

FAQS = [
    {
        'question': '¿Cuánto demora una reparación?',
        # Se conserva como ESTIMACIÓN y se dice que varía, como pide el manual.
        'answer': (
            'Depende del trabajo y de la disponibilidad del repuesto. Los '
            'cambios de batería y pantalla suelen resolverse el mismo día; una '
            'reparación por daño de líquidos puede llevar varios. Al recibir el '
            'equipo te damos un plazo estimado.'
        ),
    },
    {
        'question': '¿Qué repuestos utilizan?',
        'answer': (
            'Trabajamos con baterías Nasan. Antes de cada reparación te '
            'indicamos qué repuesto se usará y en qué condiciones.'
        ),
    },
    {
        'question': '¿Qué me explican antes de reparar?',
        'answer': (
            'Revisamos tu equipo, te explicamos el diagnóstico y confirmamos el '
            'costo antes de reparar. Si algo cambia durante el trabajo, te '
            'consultamos primero.'
        ),
    },
    {
        'question': '¿Tienen garantía los servicios?',
        # LA CORRECCIÓN CENTRAL. Texto tomado del manual v3.0 del piloto, que
        # distingue producto de reparación — la distinción que la página había
        # borrado.
        'answer': (
            'Los equipos nuevos cuentan con la garantía limitada de Apple por '
            '1 año, sujeta a sus términos, cobertura y validación. Los equipos '
            'seminuevos tienen 6 meses de garantía con nosotros. La cobertura '
            'de una reparación depende del trabajo realizado y del repuesto '
            'instalado: te informamos las condiciones aplicables antes de '
            'empezar.'
        ),
    },
    {
        'question': '¿Puedo llevar mi equipo sin cita?',
        'answer': (
            'Sí. Para reparaciones complejas recomendamos coordinar por '
            'WhatsApp para asegurar la disponibilidad del repuesto.'
        ),
    },
]

PAGE = {
    'services_hero_title': '¿Tu iPhone\nno funciona\ncomo antes?',
    'services_hero_subtitle': (
        'Técnicos especializados en equipos Apple. Te decimos qué repuesto se '
        'usa y en qué condiciones antes de empezar.'
    ),
    'services_warranty_note': (
        'La cobertura de cada reparación depende del trabajo realizado y del '
        'repuesto instalado. Te informamos las condiciones aplicables antes de '
        'empezar.'
    ),
}


def seed(apps, schema_editor):
    Company = apps.get_model('store', 'Company')
    PageSettings = apps.get_model('store', 'StorefrontPageSettings')
    Service = apps.get_model('store', 'StorefrontServiceOffering')
    Faq = apps.get_model('store', 'StorefrontFaq')

    company = Company.objects.filter(slug=PILOT_SLUG).first()
    if company is None:
        return

    page, _ = PageSettings.objects.get_or_create(company=company)
    written = []
    for field, value in PAGE.items():
        if (getattr(page, field, '') or '').strip():
            continue
        setattr(page, field, value)
        written.append(field)
    if written:
        page.save(update_fields=written)

    # Si el taller ya tiene servicios o preguntas propias, no se toca nada:
    # sembrar al lado dejaría dos listas compitiendo, que es el defecto que esto
    # viene a cerrar.
    if not Service.objects.filter(company=company).exists():
        for order, data in enumerate(SERVICES):
            Service.objects.create(company=company, sort_order=order * 10, **data)

    if not Faq.objects.filter(company=company).exists():
        for order, data in enumerate(FAQS):
            Faq.objects.create(company=company, sort_order=order * 10, **data)

    # MÉTRICAS: ninguna. A propósito.
    print(
        f'\n  M12F.1 — servicios y preguntas del tenant piloto sembrados '
        f'({PILOT_SLUG}); sin métricas, porque ninguna tiene respaldo\n'
    )


def unseed(apps, schema_editor):
    Company = apps.get_model('store', 'Company')
    PageSettings = apps.get_model('store', 'StorefrontPageSettings')
    Service = apps.get_model('store', 'StorefrontServiceOffering')
    Faq = apps.get_model('store', 'StorefrontFaq')

    company = Company.objects.filter(slug=PILOT_SLUG).first()
    if company is None:
        return

    Service.objects.filter(
        company=company, title__in=[s['title'] for s in SERVICES], updated_by__isnull=True,
    ).delete()
    Faq.objects.filter(
        company=company, question__in=[f['question'] for f in FAQS], updated_by__isnull=True,
    ).delete()

    page = PageSettings.objects.filter(company=company).first()
    if page is None:
        return
    cleared = []
    for field, value in PAGE.items():
        if (getattr(page, field, '') or '') == value:
            setattr(page, field, '')
            cleared.append(field)
    if cleared:
        page.save(update_fields=cleared)


class Migration(migrations.Migration):

    dependencies = [('store', '0077_services_content')]

    operations = [migrations.RunPython(seed, unseed)]
