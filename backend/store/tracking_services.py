"""
SERVICE-TRACKING — el enlace con el que un cliente sigue su reparación.

QUÉ ES. Una dirección que abre UNA orden y enseña sólo lo que su cliente puede
ver. No es una cuenta y no concede nada más. Es lo que se entrega en el
mostrador y lo que viaja dentro de cada aviso: el aviso resume, el enlace es
donde está todo.

EL TOKEN NO SE GUARDA. En la base vive `uid`, un identificador aleatorio de 128
bits. El token que recibe el cliente es ese `uid` más un código de
autenticación calculado con el secreto del servidor:

    token = base64url( uid  ‖  HMAC-SHA256(secreto, uid)[:16] )

  * no se puede adivinar ni enumerar: son 128 bits al azar, y además hace falta
    el secreto para que el servidor lo acepte;
  * una copia de la tabla no es una lista de enlaces que funcionen;
  * se puede volver a construir cuando un aviso lo necesita, sin haberlo
    guardado en claro.

UN ENLACE VIVO POR ORDEN. Rotar apaga el actual y crea otro; revocar deja la
orden sin enlace. Uno inventado, uno alterado y uno apagado responden lo mismo:
no encontrado.

LO QUE SALE POR EL ENLACE ES LO DEL CLIENTE. Se arma con los mismos
serializadores que la superficie del cliente con cuenta —el estado visible, la
línea de tiempo visible, la cotización enviada, las evidencias compartidas, el
saldo en cinco cifras— y los identificadores del equipo van enmascarados.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from . import device_identity
from .models import AdminAuditLog, Customer, RepairOrder, RepairTrackingLink

_UID_BYTES = 16
_MAC_BYTES = 16
#: 32 bytes en base64url sin relleno.
TOKEN_LENGTH = 43


class TrackingError(Exception):
    """No se puede hacer lo pedido con este enlace. `conflict` -> 409."""

    def __init__(self, message: str, *, conflict: bool = False, forbidden: bool = False):
        super().__init__(message)
        self.conflict = conflict
        self.forbidden = forbidden


def _key() -> bytes:
    # Derivada, no el secreto tal cual: un uso, una clave.
    return hashlib.sha256(b'repair-tracking-link|' + settings.SECRET_KEY.encode('utf-8')).digest()


def _mac(uid: str) -> bytes:
    return hmac.new(_key(), bytes.fromhex(uid), hashlib.sha256).digest()[:_MAC_BYTES]


def _token(uid: str) -> str:
    return base64.urlsafe_b64encode(bytes.fromhex(uid) + _mac(uid)).rstrip(b'=').decode('ascii')


def resolve(token) -> RepairTrackingLink | None:
    """El enlace vivo que corresponde a `token`, o None. Nunca dice por qué no."""
    text = str(token or '')
    if len(text) != TOKEN_LENGTH:
        return None
    try:
        raw = base64.urlsafe_b64decode(text + '=')
    except (ValueError, TypeError):
        return None
    if len(raw) != _UID_BYTES + _MAC_BYTES:
        return None
    uid = raw[:_UID_BYTES].hex()
    # Primero la firma: un token inventado no llega a preguntar a la base.
    if not hmac.compare_digest(raw[_UID_BYTES:], _mac(uid)):
        return None
    # UNA SOLA ESCRITURA. Los 32 bytes dejan dos bits sin usar en el último
    # carácter, así que otros tres caracteres decodifican igual. Sólo vale el
    # enlace tal como se emitió.
    if not hmac.compare_digest(_token(uid).encode(), text.encode()):
        return None
    return (
        RepairTrackingLink.objects
        .filter(uid=uid, revoked_at__isnull=True, company__is_active=True)
        .select_related('repair_order', 'repair_order__device', 'company')
        .first()
    )


def link_for(repair_order, *, actor=None) -> RepairTrackingLink | None:
    """
    El enlace vivo de la orden. Se crea si la orden NUNCA tuvo uno.

    UN ENLACE REVOCADO NO RESUCITA SOLO. Si alguien lo apagó, la orden se queda
    sin seguimiento público hasta que alguien con permiso cree otro (`rotate`).
    Crear uno aquí haría que listar las reparaciones o mandar un aviso
    deshiciera, sin decirlo, una decisión que se tomó a propósito.
    """
    link = RepairTrackingLink.objects.filter(
        repair_order=repair_order, revoked_at__isnull=True,
    ).first()
    if link is not None:
        return link
    if RepairTrackingLink.objects.filter(repair_order=repair_order).exists():
        return None
    try:
        with transaction.atomic():
            return RepairTrackingLink.objects.create(
                company_id=repair_order.company_id, repair_order=repair_order,
                uid=secrets.token_hex(_UID_BYTES), created_by=actor,
            )
    except IntegrityError:
        # Dos peticiones a la vez: gana una y la otra usa lo que dejó.
        return RepairTrackingLink.objects.get(repair_order=repair_order, revoked_at__isnull=True)


def active_link(repair_order) -> RepairTrackingLink | None:
    return RepairTrackingLink.objects.filter(
        repair_order=repair_order, revoked_at__isnull=True,
    ).first()


def token_for(repair_order) -> str | None:
    link = link_for(repair_order)
    return _token(link.uid) if link is not None else None


def path_for(repair_order) -> str | None:
    """La ruta del sitio: `/seguimiento/<token>`. None si el enlace fue revocado."""
    token = token_for(repair_order)
    return f'/seguimiento/{token}' if token else None


def url_for(repair_order) -> str | None:
    """La dirección completa, para un mensaje o un código QR."""
    path = path_for(repair_order)
    return f'{settings.FRONTEND_URL.rstrip("/")}{path}' if path else None


def _audit(action, order, actor, request):
    AdminAuditLog.log(
        actor=actor, action=action, target_type='repair_order', target_id=order.pk,
        # Ni el token ni el `uid`: el registro dice que pasó, no cómo entrar.
        metadata={'repair_order_id': order.pk, 'number': order.number},
        request=request, company=order.company,
    )


@transaction.atomic
def revoke(repair_order, *, actor, request=None) -> None:
    """Apaga el enlace vivo. La orden queda sin seguimiento público."""
    updated = RepairTrackingLink.objects.filter(
        repair_order=repair_order, revoked_at__isnull=True,
    ).update(revoked_at=timezone.now(), revoked_by=actor)
    if updated:
        _audit('service_tracking_link_revoked', repair_order, actor, request)


@transaction.atomic
def rotate(repair_order, *, actor, request=None) -> str:
    """Apaga el enlace vivo y crea otro. Devuelve el token nuevo."""
    RepairOrder.objects.select_for_update().filter(pk=repair_order.pk).first()
    RepairTrackingLink.objects.filter(
        repair_order=repair_order, revoked_at__isnull=True,
    ).update(revoked_at=timezone.now(), revoked_by=actor)
    link = RepairTrackingLink.objects.create(
        company_id=repair_order.company_id, repair_order=repair_order,
        uid=secrets.token_hex(_UID_BYTES), created_by=actor,
    )
    _audit('service_tracking_link_rotated', repair_order, actor, request)
    return _token(link.uid)


def record_view(link: RepairTrackingLink) -> None:
    """Cuenta la visita. Sin dirección ni navegador: saber que se usa no pide saber quién."""
    RepairTrackingLink.objects.filter(pk=link.pk).update(
        view_count=F('view_count') + 1, last_viewed_at=timezone.now(),
    )


def staff_payload(repair_order, *, can_reveal: bool = False) -> dict:
    """
    Lo que cualquiera que abre la orden puede saber del enlace: SI EXISTE Y SI
    SE USA. El enlace mismo no va aquí — ver `reveal`.

    Una orden anterior a los enlaces recibe el suyo aquí, la primera vez que
    alguien pregunta. Una orden cuyo enlace fue REVOCADO sigue sin enlace: eso
    no lo deshace una lectura (ver `link_for`).
    """
    link = link_for(repair_order)
    return {
        'active': link is not None,
        'view_count': link.view_count if link is not None else 0,
        'last_viewed_at': link.last_viewed_at if link is not None else None,
        'can_reveal': bool(can_reveal),
    }


def reveal(repair_order, *, actor, request=None) -> dict:
    """
    El enlace, para entregárselo al cliente. UN ACTO, CON SU REGISTRO.

    QUIEN TIENE EL ENLACE PUEDE RESPONDER LA COTIZACIÓN COMO EL CLIENTE. Por eso
    no se entrega a quien sólo puede abrir la orden: lo pide quien ya puede
    anotar la decisión del cliente, y queda escrito quién lo pidió. El registro
    no guarda el enlace.
    """
    link = link_for(repair_order)
    if link is None:
        raise TrackingError(
            'Esta orden no tiene un enlace activo. Crea uno nuevo para entregarlo.',
            conflict=True,
        )
    _audit('service_tracking_link_revealed', repair_order, actor, request)
    token = _token(link.uid)
    return {
        'path': f'/seguimiento/{token}',
        'url': f'{settings.FRONTEND_URL.rstrip("/")}/seguimiento/{token}',
    }


def _status_context(company) -> dict:
    from . import service_services
    from .models import RepairStatusCode

    settings_by_code = service_services.status_settings(company)
    return {'status_labels': {
        code: service_services.status_label(company, code, settings_by_code)
        for code, _label in RepairStatusCode.choices
    }}


def public_view(repair_order) -> dict:
    """
    La orden como su cliente puede verla. UNA LISTA DE LO QUE SÍ SALE.

    Se arma con los serializadores de la superficie del cliente, así que lo que
    allí no se muestra —notas internas, estado físico, sucursal, técnico, motivo
    de una decisión— aquí tampoco. Lo que este módulo añade es el equipo, con
    sus identificadores enmascarados, y la identidad pública de la tienda.
    """
    from . import evidence_services, service_services
    from .company_settings import company_identity
    from .evidence_views import _customer as evidence_payload
    from .models import RepairEvidence
    from .v1_service_serializers import (
        V1CustomerPaymentSummarySerializer, V1CustomerQuoteSerializer,
        V1CustomerRepairDetailSerializer,
    )

    company = repair_order.company
    context = _status_context(company)
    order = dict(V1CustomerRepairDetailSerializer(repair_order, context=context).data)
    timeline = order.pop('timeline', [])
    # El identificador interno no hace falta: el enlace ya es la orden.
    order.pop('id', None)

    device = repair_order.device
    quote = service_services.customer_visible_quote(repair_order)
    summary = service_services.service_payment_summary(repair_order)
    payment_status = summary['payment_status']
    if payment_status == service_services.PAYMENT_STATUS_OVERPAID:
        payment_status = service_services.PAYMENT_STATUS_PAID

    identity = company_identity(company)
    stage_labels = dict(RepairEvidence.Stage.choices)
    evidence = [
        {**evidence_payload(row), 'stage_label': stage_labels.get(row.stage, row.stage)}
        for row in evidence_services.customer_evidence_for_order(repair_order).order_by('created_at', 'pk')
    ]

    return {
        'company': {
            'name': identity.name,
            'phone': identity.phone,
            'whatsapp_link': identity.whatsapp_link,
            'logo_url': identity.logo_url,
            # La política de garantía ya es pública en la tienda; aquí acompaña
            # a la orden entregada, que es cuando el cliente la busca.
            'warranty_policy_text': identity.warranty_policy_text,
            'warranty_policy_url': identity.warranty_policy_url,
        },
        'order': order,
        'device': {
            'type_label': device.get_device_type_display(),
            'brand': device.brand,
            'model': device.model,
            # Enmascarados SIEMPRE: quien abre el enlace reconoce su equipo por
            # los últimos dígitos, y un enlace reenviado no reparte un IMEI.
            'serial_number': device_identity.mask(device.serial_number),
            'imei': device_identity.mask(device.imei),
        } if device is not None else None,
        'timeline': list(timeline),
        'quote': (
            V1CustomerQuoteSerializer(quote, context=context).data if quote is not None else None
        ),
        'can_decide': bool(quote is not None and quote.can_be_decided),
        'payments': V1CustomerPaymentSummarySerializer({
            'currency': summary['currency'],
            'quoted_total': summary['quoted_total'],
            'paid': summary['confirmed_paid'],
            'outstanding': summary['outstanding'],
            'status': payment_status,
        }).data,
        'evidence': evidence,
    }


def account_rows(user, company) -> list[dict]:
    """Las órdenes de servicio de quien inició sesión, con el enlace de cada una."""
    from . import service_services
    from .v1_service_serializers import V1CustomerRepairListSerializer

    orders = (
        service_services.customer_owned_repair_orders(user, company)
        .select_related('device', 'company').order_by('-received_at', '-pk')
    )
    context = _status_context(company)
    rows = []
    for order in orders:
        row = dict(V1CustomerRepairListSerializer(order, context=context).data)
        row.pop('id', None)
        row['tracking_path'] = path_for(order)
        rows.append(row)
    return rows


CLAIM_ATTEMPTS = 5
CLAIM_WINDOW_SECONDS = 3600


def _claim_key(customer_id) -> str:
    return f'tracking-claim-attempts:{customer_id}'


@transaction.atomic
def claim(token, *, user, document_number='', request=None) -> Customer:
    """
    Suma a la cuenta de `user` el cliente de la orden cuyo enlace presenta.

        EL ENLACE ABRE UNA ORDEN. VINCULAR ENTREGA UN CLIENTE ENTERO.

    Todas sus reparaciones, sus compras y el derecho a responder sus
    cotizaciones, para siempre. Un enlace se reenvía, se queda en un teléfono
    prestado, lo ve quien lo entregó: por sí solo no puede comprar eso. Hace
    falta además el DOCUMENTO con el que el cliente se registró en la tienda,
    que el enlace no muestra en ninguna parte.

    No se vincula por correo, teléfono o nombre: esos los escribe cualquiera.

    Lo que NO hace, y por eso no sirve para quedarse con una cuenta:
      * no toca a un cliente que ya tiene cuenta;
      * no deja a una cuenta ser dos clientes de la misma empresa;
      * no deja probar documentos sin fin: cinco fallos y ese cliente queda sin
        vinculación por formulario durante una hora;
      * un cliente sin documento registrado se vincula en la tienda, no aquí.
    """
    import re

    from django.core.cache import cache

    def normalize_document_number(value) -> str:
        # Sólo letras y números: «4567-8912», «4567 8912» y «45678912» son el
        # mismo documento dictado de tres maneras.
        return re.sub(r'[^0-9A-Za-z]', '', str(value or '')).upper()

    link = resolve(token)
    if link is None:
        raise TrackingError('No encontrado.')
    order = link.repair_order
    customer = Customer.objects.select_for_update().get(pk=order.customer_id)

    if customer.user_id == user.pk:
        return customer
    if customer.user_id is not None:
        raise TrackingError('Esta orden ya pertenece a otra cuenta.', conflict=True)
    if Customer.objects.filter(company_id=order.company_id, user=user).exists():
        raise TrackingError(
            'Tu cuenta ya está asociada a otro registro de cliente de esta tienda. '
            'Pide en la tienda que unan los dos.', conflict=True,
        )

    on_file = normalize_document_number(customer.document_number)
    if not on_file:
        raise TrackingError(
            'Esta orden no tiene un documento registrado con el que comprobar que es tuya. '
            'Pide en la tienda que la vinculen a tu cuenta.', conflict=True,
        )
    key = _claim_key(customer.pk)
    if (cache.get(key) or 0) >= CLAIM_ATTEMPTS:
        raise TrackingError(
            'Demasiados intentos. Pide en la tienda que vinculen la orden a tu cuenta.',
            forbidden=True,
        )
    given = normalize_document_number(document_number)
    if not given or not hmac.compare_digest(given.encode(), on_file.encode()):
        cache.add(key, 0, CLAIM_WINDOW_SECONDS)
        try:
            cache.incr(key)
        except ValueError:
            cache.set(key, 1, CLAIM_WINDOW_SECONDS)
        raise TrackingError(
            'El documento no coincide con el de esta orden.', forbidden=True,
        )

    customer.user = user
    customer.save(update_fields=['user', 'updated_at'])
    AdminAuditLog.log(
        actor=user, action='customer_account_linked', target_type='customer',
        target_id=customer.pk,
        metadata={'customer_id': customer.pk, 'via': 'tracking_link',
                  'repair_order_id': order.pk},
        request=request, company=order.company,
    )
    return customer


@transaction.atomic
def unlink_account(customer, *, actor, reason: str, request=None) -> Customer:
    """
    La tienda deshace una vinculación. Con motivo y con registro.

    Es la salida para la cuenta equivocada: sin ella, el cliente de verdad se
    encontraría un «ya pertenece a otra cuenta» sin nadie que pudiera arreglarlo.
    """
    reason = ' '.join(str(reason or '').split())
    if not reason:
        raise TrackingError('Indica por qué se desvincula la cuenta.')
    if len(reason) > 300:
        raise TrackingError('El motivo admite hasta 300 caracteres.')
    locked = Customer.objects.select_for_update().get(pk=customer.pk)
    previous = locked.user_id
    if previous is None:
        return locked
    locked.user = None
    locked.save(update_fields=['user', 'updated_at'])
    AdminAuditLog.log(
        actor=actor, action='customer_account_unlinked', target_type='customer',
        target_id=locked.pk,
        metadata={'customer_id': locked.pk, 'previous_user_id': previous, 'reason': reason},
        request=request, company=locked.company,
    )
    return locked
