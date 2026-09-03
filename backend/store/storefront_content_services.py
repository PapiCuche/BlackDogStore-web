"""
M12F — el contenido comercial del escaparate, como dato del tenant.

EL DEFECTO QUE CIERRA ESTE MÓDULO
---------------------------------
La preventa del Home vivía compilada. Cambiarla exigía tocar un componente y
desplegar; NO cambiarla dejaba una promoción caducada en portada — que es el
fallo peor, porque nadie despliega para borrar algo que ya no existe.

DÓNDE ESTÁ LA SEGURIDAD
-----------------------
En el queryset. `StorefrontCampaign.objects.active(company)` responde las cuatro
condiciones a la vez —empresa, estado, inicio, fin— y quien la usa no puede
olvidarse de una porque no las escribe. El escaparate público y la vista previa
del admin llaman a la MISMA función; si divergieran, un borrador acabaría
publicado.

QUÉ NO HACE
-----------
No acepta HTML, ni Markdown, ni CSS. Acepta campos. Un editor que acepta marcado
es un editor que acepta `<script>`, y esto lo rellena personal del tenant, no el
equipo que revisa el código.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import (
    AdminAuditLog, Product, StorefrontCampaign, StorefrontPageSettings,
)

#: La autoridad que ya gobierna la configuración de la empresa.
#:
#: NO se crea una capacidad nueva. `company.manage` es exactamente lo que hoy
#: autoriza editar `CompanySettings`, incluido el logotipo y la paleta, y una
#: campaña de portada es contenido de la misma naturaleza. Añadir
#: `storefront.content.manage` habría exigido una migración de presets
#: congelados — la clase de cambio que M12B.1 dedicó una fase entera a reparar—
#: a cambio de una distinción que hoy no separa a nadie de nadie.
#:
#: Queda ANOTADO como propuesta: el día que un taller quiera que su encargado de
#: marketing publique campañas SIN poder cambiar el RUC de la empresa, esa
#: capacidad tendrá un motivo. Hoy no lo tiene.
CONTENT_CAPABILITY = 'company.manage'
CONTENT_VIEW_CAPABILITY = 'company.view'

#: Lo que el público llega a ver de una campaña. Lista blanca, no lista negra:
#: un campo nuevo en el modelo NO aparece solo en la respuesta pública.
PUBLIC_CAMPAIGN_FIELDS = (
    'slot', 'badge', 'title', 'subtitle', 'body',
    'image_url', 'cta_label', 'cta_url',
    'secondary_cta_label', 'secondary_cta_url',
)


# ---------------------------------------------------------------------------
# Lectura pública
# ---------------------------------------------------------------------------

def public_campaigns(company, now=None) -> dict:
    """
    `{slot: payload}` con lo que este tenant publica AHORA MISMO.

    Un diccionario por slot y no una lista: la página pregunta «¿qué va en la
    promoción inferior?» y recibe una respuesta o nada. Recorrer una lista
    buscando el slot correcto es el tipo de bucle que un día muestra la campaña
    de otro sitio.

    El desempate cuando dos campañas comparten slot es determinista y vive en el
    `order_by` del queryset: prioridad, luego la publicada más recientemente. Sin
    él, la portada mostraría la fila que la base devolviera primero — una
    respuesta distinta según el plan de consulta.
    """
    out: dict[str, dict] = {}
    for campaign in StorefrontCampaign.objects.active(company, now).select_related('product'):
        if campaign.slot in out:
            # Ya ganó una de mayor prioridad para este slot.
            continue
        out[campaign.slot] = _public_payload(campaign)
    return out


def _public_payload(campaign) -> dict:
    data = {field: getattr(campaign, field) or '' for field in PUBLIC_CAMPAIGN_FIELDS}
    # El producto enlazado viaja REDUCIDO: lo justo para pintar un enlace. Ni
    # coste interno, ni stock, ni identificadores que el catálogo público no dé
    # ya por su cuenta.
    if campaign.product_id and campaign.product.is_active:
        data['product'] = {
            'slug': campaign.product.slug,
            'name': campaign.product.name,
        }
    else:
        data['product'] = None
    return data


def public_page_settings(company) -> dict:
    """
    El contenido estable de la portada de este tenant.

    Todo vacío es una respuesta válida y significa «usa el texto genérico de la
    plataforma». NUNCA el de otra empresa: el copy aprobado del manual del
    piloto vive en la fila del piloto, escrito por una migración, igual que su
    identidad comercial desde la Fase 3.
    """
    row = getattr(company, 'storefront_page', None) if company is not None else None
    fields = (
        'hero_eyebrow', 'hero_title', 'hero_subtitle',
        'hero_primary_cta_label', 'hero_primary_cta_url',
        'hero_secondary_cta_label', 'hero_secondary_cta_url',
    )
    return {f: (getattr(row, f, '') or '') if row is not None else '' for f in fields}


# ---------------------------------------------------------------------------
# Escritura — siempre con empresa explícita
# ---------------------------------------------------------------------------

def _check_product(company, product_id):
    """
    Un producto de OTRA empresa no existe para ésta.

    Se responde igual a «no existe» y a «no es tuyo»: confirmar que un id ajeno
    es válido ya filtra que existe.
    """
    if product_id in (None, '', 0):
        return None
    product = Product.objects.filter(pk=product_id, company=company).first()
    if product is None:
        raise ValidationError({'product': 'Producto no encontrado.'})
    return product


EDITABLE_FIELDS = (
    'slot', 'badge', 'title', 'subtitle', 'body', 'image_url',
    'cta_label', 'cta_url', 'secondary_cta_label', 'secondary_cta_url',
    'starts_at', 'ends_at', 'priority',
)


@transaction.atomic
def create_campaign(*, company, actor, data, request=None):
    campaign = StorefrontCampaign(company=company, created_by=actor)
    for field in EDITABLE_FIELDS:
        if field in data:
            setattr(campaign, field, data[field])
    campaign.product = _check_product(company, data.get('product'))
    # `full_clean` porque `save()` no lo llama: sin esto, el validador de los
    # campos de URL protegería al serializador y a nada más.
    campaign.full_clean()
    campaign.save()
    AdminAuditLog.log(
        actor=actor, action='storefront_campaign_created',
        target_type='storefront_campaign', target_id=campaign.pk,
        metadata={'slot': campaign.slot, 'title': campaign.title},
        request=request, company=company,
    )
    return campaign


@transaction.atomic
def update_campaign(*, campaign, actor, data, request=None):
    """
    Editar NO publica. Guardar un borrador lo deja en borrador, y editar una
    campaña publicada la deja publicada: el estado sólo cambia con una acción
    que se llama por su nombre.
    """
    locked = StorefrontCampaign.objects.select_for_update().get(pk=campaign.pk)
    if locked.status == StorefrontCampaign.Status.ARCHIVED:
        raise ValidationError('Una campaña archivada no se edita: duplícala.')

    for field in EDITABLE_FIELDS:
        if field in data:
            setattr(locked, field, data[field])
    if 'product' in data:
        locked.product = _check_product(locked.company, data.get('product'))
    locked.updated_by = actor
    locked.full_clean()
    locked.save()
    AdminAuditLog.log(
        actor=actor, action='storefront_campaign_updated',
        target_type='storefront_campaign', target_id=locked.pk,
        metadata={'slot': locked.slot, 'title': locked.title},
        request=request, company=locked.company,
    )
    return locked


@transaction.atomic
def publish_campaign(*, campaign, actor, request=None):
    """
    Publicar es una acción con nombre propio, no el efecto de guardar.

    Es idempotente: publicar dos veces no mueve `published_at`, que es lo que
    desempata dos campañas del mismo slot. Si se moviera, pulsar el botón otra
    vez reordenaría la portada sin que nadie hubiera cambiado nada.
    """
    locked = StorefrontCampaign.objects.select_for_update().get(pk=campaign.pk)
    if locked.status == StorefrontCampaign.Status.ARCHIVED:
        raise ValidationError('Una campaña archivada no se republica.')
    if locked.status == StorefrontCampaign.Status.PUBLISHED:
        return locked

    locked.full_clean()
    locked.status = StorefrontCampaign.Status.PUBLISHED
    locked.published_at = timezone.now()
    locked.updated_by = actor
    locked.save(update_fields=['status', 'published_at', 'updated_by', 'updated_at'])
    AdminAuditLog.log(
        actor=actor, action='storefront_campaign_published',
        target_type='storefront_campaign', target_id=locked.pk,
        metadata={'slot': locked.slot, 'title': locked.title},
        request=request, company=locked.company,
    )
    return locked


@transaction.atomic
def archive_campaign(*, campaign, actor, request=None):
    """
    Archivar, no borrar.

    La campaña de hace tres años es el historial de lo que esta tienda anunció.
    Un DELETE deja el mismo vacío que dejaba el código: nadie puede responder
    qué se publicó y cuándo.
    """
    locked = StorefrontCampaign.objects.select_for_update().get(pk=campaign.pk)
    if locked.status == StorefrontCampaign.Status.ARCHIVED:
        return locked
    locked.status = StorefrontCampaign.Status.ARCHIVED
    locked.updated_by = actor
    locked.save(update_fields=['status', 'updated_by', 'updated_at'])
    AdminAuditLog.log(
        actor=actor, action='storefront_campaign_archived',
        target_type='storefront_campaign', target_id=locked.pk,
        metadata={'slot': locked.slot, 'title': locked.title},
        request=request, company=locked.company,
    )
    return locked


@transaction.atomic
def update_page_settings(*, company, actor, data, request=None):
    row, _ = StorefrontPageSettings.objects.get_or_create(company=company)
    fields = (
        'hero_eyebrow', 'hero_title', 'hero_subtitle',
        'hero_primary_cta_label', 'hero_primary_cta_url',
        'hero_secondary_cta_label', 'hero_secondary_cta_url',
    )
    for field in fields:
        if field in data:
            setattr(row, field, data[field] or '')
    row.updated_by = actor
    row.full_clean()
    row.save()
    AdminAuditLog.log(
        actor=actor, action='storefront_page_updated',
        target_type='storefront_page', target_id=row.pk,
        metadata={'company': company.slug},
        request=request, company=company,
    )
    return row
