"""
WHATSAPP-NOTIFY — carrying a customer notice over WhatsApp.

    WHATSAPP TELLS; THE PORTAL KEEPS THE WHOLE STORY.

A message is a summary and a secure link: a name, an order number and the
tracking address. No IMEI, no serial, no notes, no amounts — whoever wants the
detail opens the link, which is where authorisation lives.

THE OUTBOX IS `NotificationDelivery`
------------------------------------
    BEGIN
      the business change
      the NotificationEvent / Notification rows
      the WhatsApp delivery row, PENDING          ← `queue()`, in the transaction
    COMMIT
      on_commit: attempt the send, best effort    ← `attempt()`
    LATER
      `manage.py send_pending_notifications`      ← whatever did not go out

A rollback takes the row with it, so nothing is announced that did not happen.
A provider failure is a fact recorded on the row; it cannot undo the work.

ONE NOTICE IS ONE MESSAGE. The row is unique per (notification, channel) and is
locked while it is sent, so a retry, a replayed event and two workers all end
in a single message.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import re
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from . import messaging
from . import notification_events as ev
from .messaging import phones
from .models import (
    AdminAuditLog, CompanyMessagingSettings, Customer, Notification,
    NotificationDelivery, RepairOrder,
)

logger = logging.getLogger(__name__)

CHANNEL = NotificationDelivery.Channel.WHATSAPP
Status = NotificationDelivery.Status

MAX_ATTEMPTS = 5
#: Seconds to wait after the 1st, 2nd, 3rd and 4th failed attempt.
BACKOFF_SECONDS = (60, 300, 1800, 7200)
#: A PENDING row younger than this belongs to a request that is still sending.
PENDING_GRACE = timedelta(seconds=60)

#: The customer notices that may travel by WhatsApp, and what each is called on
#: the settings screen. Every one is a real event of the repair lifecycle.
EVENT_LABELS = {
    ev.SERVICE_ORDER_CREATED: 'Equipo recibido',
    ev.SERVICE_QUOTE_AVAILABLE: 'Cotización lista para revisar',
    ev.SERVICE_STATUS_CHANGED: 'Avance de la reparación (en reparación, esperando repuesto)',
    ev.SERVICE_READY_FOR_PICKUP: 'Equipo listo para recoger',
    ev.SERVICE_DELIVERED: 'Equipo entregado',
}
EVENTS = frozenset(EVENT_LABELS)

#: What every template receives, in order: {{1}} {{2}} {{3}}.
TEMPLATE_PARAMETERS = ('nombre del cliente', 'número de orden', 'enlace de seguimiento')

_TEMPLATE_NAME = re.compile(r'^[a-z0-9_]{1,64}$')
_LANGUAGE = re.compile(r'^[a-z]{2}(_[A-Z]{2})?$')
_STOP_WORDS = frozenset({'baja', 'stop', 'alto', 'cancelar', 'unsubscribe'})


class WhatsAppSettingsError(Exception):
    """A settings change was refused. Views render it as 400."""


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def config_for(company) -> CompanyMessagingSettings | None:
    return CompanyMessagingSettings.objects.filter(company=company).first()


def readiness(config) -> tuple[bool, list[str]]:
    """Whether this company could send right now, and what is missing if not."""
    if config is None:
        return False, ['phone_number_id', *messaging.CREDENTIALS]
    missing = [] if config.whatsapp_phone_number_id else ['phone_number_id']
    missing += [name for name, present in messaging.credentials_state(config).items() if not present]
    return not missing, missing


def settings_payload(company) -> dict:
    """
    What the settings screen may know. BOOLEANS FOR CREDENTIALS, never values
    and never the names of the variables that hold them.
    """
    config = config_for(company)
    ready, missing = readiness(config)
    templates = (config.whatsapp_templates if config else {}) or {}
    return {
        'enabled': bool(config and config.whatsapp_enabled),
        'provider': config.whatsapp_provider if config else CompanyMessagingSettings.PROVIDER_CLOUD_API,
        'ready': ready,
        'missing': missing,
        'phone_number_configured': bool(config and config.whatsapp_phone_number_id),
        'credentials': (
            messaging.credentials_state(config) if config
            else {name: False for name in messaging.CREDENTIALS}
        ),
        'default_calling_code': config.default_calling_code if config else '',
        'template_language': config.template_language if config else 'es',
        'templates': {code: templates.get(code, '') for code in EVENT_LABELS},
        'events': [{'code': code, 'label': label} for code, label in EVENT_LABELS.items()],
        'template_parameters': list(TEMPLATE_PARAMETERS),
        'webhook_path': f'/api/v1/webhooks/whatsapp/{company.slug}/',
    }


@transaction.atomic
def update_settings(company, *, actor, data: dict, request=None) -> dict:
    """
    What a tenant administrator may change. A CLOSED LIST.

    The phone number id and the three credential references are not here: they
    are set by the operator of the deployment (`configure_whatsapp`). A body
    that names them is not an error worth explaining — they are simply not read.
    """
    config, _ = CompanyMessagingSettings.objects.select_for_update().get_or_create(company=company)
    changed = []

    if 'templates' in data:
        templates = data['templates']
        if not isinstance(templates, dict):
            raise WhatsAppSettingsError('Las plantillas deben indicarse por aviso.')
        merged = dict(config.whatsapp_templates or {})
        for code, name in templates.items():
            if code not in EVENTS:
                raise WhatsAppSettingsError(f'Aviso desconocido: {code}.')
            name = str(name or '').strip()
            if name and not _TEMPLATE_NAME.match(name):
                raise WhatsAppSettingsError(
                    'El nombre de una plantilla sólo lleva minúsculas, números y guion bajo.'
                )
            if name:
                merged[code] = name
            else:
                merged.pop(code, None)
        config.whatsapp_templates = merged
        changed.append('templates')

    if 'default_calling_code' in data:
        code = re.sub(r'\D', '', str(data['default_calling_code'] or ''))
        if len(code) > 4:
            raise WhatsAppSettingsError('El código de país tiene entre 1 y 4 dígitos.')
        config.default_calling_code = code
        changed.append('default_calling_code')

    if 'template_language' in data:
        language = str(data['template_language'] or '').strip()
        if not _LANGUAGE.match(language):
            raise WhatsAppSettingsError('Idioma no reconocido. Ejemplos: es, es_MX, en_US.')
        config.template_language = language
        changed.append('template_language')

    if 'enabled' in data:
        enabled = bool(data['enabled'])
        if enabled:
            ready, missing = readiness(config)
            if not ready:
                raise WhatsAppSettingsError(
                    'No se puede activar todavía: faltan credenciales del proveedor. '
                    'Las configura quien administra la instalación.'
                )
        config.whatsapp_enabled = enabled
        changed.append('enabled')

    config.updated_by = actor
    config.save()
    if changed:
        AdminAuditLog.log(
            actor=actor, action='whatsapp_settings_updated', target_type='company',
            target_id=company.pk,
            # What changed, never a value that could be a credential.
            metadata={'changed': changed, 'enabled': config.whatsapp_enabled},
            request=request, company=company,
        )
    return settings_payload(company)


# ---------------------------------------------------------------------------
# Consent
# ---------------------------------------------------------------------------

def has_consent(customer) -> bool:
    """They said yes, and have not said no since."""
    if customer is None or customer.whatsapp_opt_in_at is None:
        return False
    out = customer.whatsapp_opt_out_at
    return out is None or out < customer.whatsapp_opt_in_at


@transaction.atomic
def set_consent(customer, *, opt_in: bool, source: str = 'counter', actor=None, request=None):
    locked = Customer.objects.select_for_update().get(pk=customer.pk)
    now = timezone.now()
    if opt_in:
        locked.whatsapp_opt_in_at = now
        locked.whatsapp_opt_in_source = source
        fields = ['whatsapp_opt_in_at', 'whatsapp_opt_in_source', 'updated_at']
    else:
        locked.whatsapp_opt_out_at = now
        fields = ['whatsapp_opt_out_at', 'updated_at']
    locked.save(update_fields=fields)
    AdminAuditLog.log(
        actor=actor, action='customer_whatsapp_consent_changed', target_type='customer',
        target_id=locked.pk,
        metadata={'customer_id': locked.pk, 'opt_in': bool(opt_in), 'source': source},
        request=request, company=locked.company,
    )
    return locked


# ---------------------------------------------------------------------------
# Outbox
# ---------------------------------------------------------------------------

def _eligible(notification) -> bool:
    return (
        notification is not None
        and notification.audience == Notification.Audience.CUSTOMER
        and notification.customer_id is not None
        and notification.target_type == 'repair_order'
        and notification.event_id is not None
        and notification.event.event_type in EVENTS
    )


def _skip_reason(config, notification) -> str:
    """Why this notice will not be sent, or '' when it should be."""
    if not (config.whatsapp_templates or {}).get(notification.event.event_type):
        return 'sin plantilla configurada para este aviso'
    customer = notification.customer
    if not has_consent(customer):
        return 'sin consentimiento del cliente'
    if phones.to_wa_id(customer.phone, config.default_calling_code) is None:
        return 'sin teléfono válido'
    return ''


def queue(notifications) -> list[int]:
    """
    Write the outbox rows for these notices. Called INSIDE the transaction.

    A company that has not turned WhatsApp on gets no row at all: there is
    nothing to report about a channel that is not in use. One that has gets a
    row per eligible notice — PENDING, or SKIPPED with the reason, so the order
    screen can say why a customer was not messaged.
    """
    pending: list[int] = []
    configs: dict[int, CompanyMessagingSettings | None] = {}
    for notification in notifications:
        if not _eligible(notification):
            continue
        company_id = notification.company_id
        if company_id not in configs:
            configs[company_id] = config_for(notification.company)
        config = configs[company_id]
        if config is None or not config.whatsapp_enabled:
            continue

        reason = _skip_reason(config, notification)
        wa_id = phones.to_wa_id(notification.customer.phone, config.default_calling_code)
        row, created = NotificationDelivery.objects.get_or_create(
            notification=notification, channel=CHANNEL,
            defaults={
                'status': Status.SKIPPED if reason else Status.PENDING,
                'failure_reason': reason,
                'recipient_masked': phones.mask(wa_id) if wa_id else '',
            },
        )
        if created and not reason:
            pending.append(row.pk)
    return pending


def attempt(delivery_ids) -> None:
    """After commit, best effort. Swallows everything: the row keeps the truth."""
    if not getattr(settings, 'WHATSAPP_SEND_INLINE', True):
        return
    for delivery_id in delivery_ids:
        try:
            deliver(delivery_id)
        except Exception:  # noqa: BLE001 — nothing may surface from on_commit
            logger.exception('fallo no controlado enviando WhatsApp (entrega %s)', delivery_id)


def _parameters(notification, order) -> list[str]:
    from . import tracking_services

    customer = notification.customer
    name = (customer.first_name or customer.business_name or 'cliente').strip()
    link = tracking_services.url_for(order) or settings.FRONTEND_URL.rstrip('/')
    return [name, order.number, link]


def _fail(row, reason: str, *, retryable: bool) -> None:
    row.status = Status.FAILED
    row.failure_reason = reason[:200]
    row.next_attempt_at = None
    if retryable and row.attempt_count < MAX_ATTEMPTS:
        wait = BACKOFF_SECONDS[min(row.attempt_count, len(BACKOFF_SECONDS)) - 1]
        row.next_attempt_at = timezone.now() + timedelta(seconds=wait)


@transaction.atomic
def deliver(delivery_id) -> NotificationDelivery:
    """
    Send one outbox row, once. Safe to call any number of times.

    The row is LOCKED for the duration, and a row that already left is returned
    untouched — that is the whole idempotency. Consent is read again here: a
    customer who said no between the event and a retry is not messaged.
    """
    # `of=('self',)`: only the outbox row is locked. The notice's customer and
    # event are nullable joins, which PostgreSQL refuses to lock — and they are
    # not what two workers would race for.
    row = (
        NotificationDelivery.objects.select_for_update(of=('self',))
        .select_related('notification__company', 'notification__customer', 'notification__event')
        .get(pk=delivery_id, channel=CHANNEL)
    )
    if row.status in NotificationDelivery.DONE_STATUSES or row.status == Status.SKIPPED:
        return row
    if row.attempt_count >= MAX_ATTEMPTS:
        return row

    notification = row.notification
    config = config_for(notification.company)
    order = RepairOrder.objects.filter(
        pk=notification.target_id, company_id=notification.company_id,
    ).first()

    reason = ''
    if config is None or not config.whatsapp_enabled:
        reason = 'WhatsApp desactivado para esta empresa'
    elif order is None:
        reason = 'la orden ya no existe'
    else:
        reason = _skip_reason(config, notification)
    if reason:
        row.status, row.failure_reason, row.next_attempt_at = Status.SKIPPED, reason, None
        row.save(update_fields=['status', 'failure_reason', 'next_attempt_at'])
        return row

    wa_id = phones.to_wa_id(notification.customer.phone, config.default_calling_code)
    row.recipient_masked = phones.mask(wa_id)
    row.attempt_count += 1
    row.last_attempt_at = timezone.now()
    try:
        provider = messaging.provider_for(config)
        result = provider.send_template(
            to=wa_id,
            template=config.whatsapp_templates[notification.event.event_type],
            language=config.template_language or 'es',
            parameters=_parameters(notification, order),
        )
    except messaging.NotConfigured as exc:
        _fail(row, f'credenciales no configuradas: {exc}', retryable=False)
    except messaging.ProviderError as exc:
        _fail(row, f'{exc.code}: {exc}' if exc.code else str(exc), retryable=exc.retryable)
        logger.warning('WhatsApp rechazado por el proveedor (código %s)', exc.code or '—')
    except Exception as exc:  # noqa: BLE001 — recorded, never raised into the caller
        _fail(row, f'error inesperado ({type(exc).__name__})', retryable=True)
        logger.exception('error inesperado enviando WhatsApp')
    else:
        row.status = Status.SENT
        row.provider_message_id = result.provider_message_id
        row.sent_at = timezone.now()
        row.failure_reason = ''
        row.next_attempt_at = None
    row.save()
    return row


def due(now=None):
    """What the retry pass should pick up now."""
    now = now or timezone.now()
    return NotificationDelivery.objects.filter(channel=CHANNEL).filter(
        Q(status=Status.PENDING, created_at__lte=now - PENDING_GRACE)
        | Q(status=Status.FAILED, next_attempt_at__isnull=False, next_attempt_at__lte=now),
    ).filter(attempt_count__lt=MAX_ATTEMPTS).order_by('created_at', 'pk')


@transaction.atomic
def retry(delivery, *, actor, request=None) -> NotificationDelivery:
    """A person asks for another try. A message that already left is left alone."""
    row = NotificationDelivery.objects.select_for_update().get(pk=delivery.pk, channel=CHANNEL)
    if row.status in NotificationDelivery.DONE_STATUSES:
        return row
    # A deliberate retry gets a fresh run of attempts, and re-reads everything:
    # the template may have been fixed, the customer may have agreed since.
    row.status, row.attempt_count, row.next_attempt_at = Status.PENDING, 0, None
    row.save(update_fields=['status', 'attempt_count', 'next_attempt_at'])
    AdminAuditLog.log(
        actor=actor, action='whatsapp_delivery_retried', target_type='notification',
        target_id=row.notification_id,
        metadata={'notification_id': row.notification_id, 'previous_reason': row.failure_reason[:120]},
        request=request, company=row.notification.company,
    )
    return deliver(row.pk)


# ---------------------------------------------------------------------------
# Webhook
# ---------------------------------------------------------------------------

def signature_is_valid(config, body: bytes, header: str) -> bool:
    """`X-Hub-Signature-256` against the app secret, in constant time."""
    secret = messaging.credential(config, 'app_secret')
    if not secret or not header or not header.startswith('sha256='):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(header[len('sha256='):].strip(), expected)


def verify_token_matches(config, presented: str) -> bool:
    expected = messaging.credential(config, 'verify_token')
    return bool(expected) and hmac.compare_digest(str(presented or ''), expected)


_RANK = {Status.SENT: 1, Status.DELIVERED: 2, Status.READ: 3}


def apply_status_report(company, report: dict) -> bool:
    """
    One delivery report from the provider. Returns whether anything changed.

    SCOPED TO THE COMPANY whose webhook received it: a message id that belongs
    to another tenant is simply not found. And it only ever moves FORWARD —
    reports arrive out of order, and a late "delivered" must not un-read a
    message.
    """
    message_id = str(report.get('id') or '')[:128]
    state = str(report.get('status') or '')
    if not message_id or state not in ('sent', 'delivered', 'read', 'failed'):
        return False
    with transaction.atomic():
        row = (
            NotificationDelivery.objects.select_for_update()
            .filter(channel=CHANNEL, provider_message_id=message_id,
                    notification__company=company)
            .first()
        )
        if row is None:
            return False
        now = timezone.now()
        if state == 'failed':
            if row.status in (Status.DELIVERED, Status.READ):
                return False
            errors = report.get('errors') or [{}]
            first = errors[0] if isinstance(errors, list) and errors else {}
            code = str(first.get('code') or '')
            title = str(first.get('title') or first.get('message') or 'no entregado')
            row.status = Status.FAILED
            row.failure_reason = (f'{code}: {title}' if code else title)[:200]
            row.next_attempt_at = None
        else:
            if state in ('delivered', 'read') and row.delivered_at is None:
                row.delivered_at = now
            if state == 'read' and row.read_at is None:
                row.read_at = now
            target = Status(state)
            if _RANK.get(target, 0) > _RANK.get(row.status, 0):
                row.status = target
        row.save()
        return True


def apply_inbound(company, message: dict) -> bool:
    """A customer wrote back. Only one thing is understood: "stop"."""
    if message.get('type') != 'text':
        return False
    word = str((message.get('text') or {}).get('body') or '').strip().lower()
    sender = re.sub(r'\D', '', str(message.get('from') or ''))
    if word not in _STOP_WORDS or not sender:
        return False
    config = config_for(company)
    code = config.default_calling_code if config else ''
    changed = False
    for customer in Customer.objects.filter(company=company).exclude(phone=''):
        if phones.to_wa_id(customer.phone, code) == sender and has_consent(customer):
            set_consent(customer, opt_in=False, source='message')
            changed = True
    return changed


def process_webhook(company, payload: dict) -> None:
    for entry in payload.get('entry') or []:
        for change in (entry or {}).get('changes') or []:
            value = (change or {}).get('value') or {}
            for report in value.get('statuses') or []:
                apply_status_report(company, report or {})
            for message in value.get('messages') or []:
                apply_inbound(company, message or {})
