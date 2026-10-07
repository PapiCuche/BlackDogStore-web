"""
The purchase, measured once and from the place that knows it happened.

    checkout starts      → `capture`          what the buyer accepted, kept with the order
    payment confirmed    → `record_purchase`  one outbox row per provider, in that transaction
    after commit / timer → `attempt`          the row is sent; a failure is retried, never doubled

NOTHING HERE MAY STOP A SALE. `capture` and `record_purchase` run inside the
checkout and inside the transaction that makes an order paid; each catches
everything, and `record_purchase` works under a savepoint so that a database
error of its own cannot poison the payment it is describing.

THE CONSENT TRAVELS WITH THE ORDER. What the browser accepted when the buyer
pressed «pagar» is stored beside the order and is the only thing that lets a
conversion be planned. A provider is also asked again when the row is SENT: one
that was switched off in the console in between receives nothing.
"""
from __future__ import annotations

import logging
import re
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from . import adapters, PROVIDER_IDS

logger = logging.getLogger('store.measurement')

PURCHASE = 'purchase'
#: Minutes to wait before each new attempt. After the last one the row is failed.
BACKOFF_MINUTES = (1, 5, 30, 120, 720)
#: A row left «sending» this long belonged to a process that died: it is retried.
STUCK = timedelta(minutes=10)
#: How long the payment's own request waits for a provider. Short: whoever is
#: slow is left for the timer, which waits the adapters' full time.
INLINE_TIMEOUT_SECONDS = 3
#: For how long after a payment the success page is told the purchase, so that the
#: browser can emit its copy. Afterwards the reference opens a status and a total.
PURCHASE_TOLD_FOR = timedelta(hours=1)
#: The browser identifiers are not kept longer than this, sent or not.
CONTEXT_RETENTION = timedelta(days=7)
#: How late each provider still takes an event (Google 72 h, Meta 7 days; TikTok's 48 h is its dedup window).
MAX_AGE = {'google_analytics': timedelta(hours=71), 'meta': timedelta(days=6, hours=23), 'tiktok': timedelta(hours=47)}

# What each provider's own script writes. Anything else a browser sends is dropped.
# `[0-9]` and `fullmatch`: `\d` takes every alphabet's digits and `$` a trailing line break.
_SHAPES = {
    'ga_client_id': re.compile(r'[0-9]{1,20}\.[0-9]{1,20}'),
    'ga_session_id': re.compile(r'[0-9]{1,20}'),
    'fbp': re.compile(r'fb\.[0-9]\.[0-9]{10,16}\.[0-9]{1,20}'),
    'fbc': re.compile(r'fb\.[0-9]\.[0-9]{10,16}\.[A-Za-z0-9_-]{1,200}'),
    'ttp': re.compile(r'[A-Za-z0-9_-]{10,80}'),
    'ttclid': re.compile(r'[A-Za-z0-9_.-]{10,200}'),
}
_MARKETING_ONLY = ('fbp', 'fbc', 'ttp', 'ttclid')


def event_id(order) -> str:
    """The id both the browser's event and the server's carry. Deterministic: a retry repeats it."""
    return f'{PURCHASE}.{order.pk}'


# -- checkout: what the buyer accepted -----------------------------------------------------

def capture(order, request, payload) -> None:
    """Keep the consent and the provider identifiers of this browser beside the order. Never raises."""
    try:
        _capture(order, request, payload)
    except Exception as exc:  # noqa: BLE001 — measurement must not refuse a checkout
        logger.error('measurement context not stored (%s)', type(exc).__name__)


def _capture(order, request, payload) -> None:
    from ..client_ip import get_client_ip
    from ..models import MeasurementContext

    if not isinstance(payload, dict) or not isinstance(payload.get('consent'), dict):
        return
    forget_old_contexts()        # here, so that it does not depend on any timer being installed
    # Kept only for a provider that will READ it: one that sends the purchase from
    # the server. With pixels alone the browser does everything, and nothing of
    # this browser has any business in the database.
    readers = {active[0].consent for active in map(_server_side, PROVIDER_IDS) if active is not None}
    # `is True`: a string, a number or a missing key is not consent.
    analytics = payload['consent'].get('analytics') is True and 'analytics' in readers
    marketing = payload['consent'].get('marketing') is True and 'marketing' in readers
    if not (analytics or marketing):
        return
    fields = {}
    for name, shape in _SHAPES.items():
        value = payload.get(name)
        allowed = marketing if name in _MARKETING_ONLY else analytics
        if allowed and isinstance(value, str) and value.isascii() and shape.fullmatch(value):
            fields[name] = value
    if marketing:
        fields['user_agent'] = str(request.META.get('HTTP_USER_AGENT') or '')[:400]
        fields['ip'] = get_client_ip(request)
    MeasurementContext.objects.update_or_create(
        order=order, defaults={'analytics': analytics, 'marketing': marketing, **fields})


# -- payment confirmed: one row per provider -----------------------------------------------

def record_purchase(order) -> list:
    """
    Called INSIDE the transaction that makes `order` paid. Writes the outbox rows
    and returns their ids for `send_after_commit`. Never raises, and cannot break
    the transaction it is called from.
    """
    try:
        with transaction.atomic():                       # a savepoint: ours to lose, not the payment's
            return _plan(order)
    except Exception as exc:  # noqa: BLE001
        logger.error('purchase conversion not recorded for an order (%s)', type(exc).__name__)
        return []


def send_after_commit(ids) -> None:
    """
    Schedule the first attempt for after the commit. Registered by the caller
    AFTER its own hooks — the confirmation e-mail goes first — and `robust`: a
    hook that raises would otherwise cancel every hook after it.
    """
    if ids and getattr(settings, 'MEASUREMENT_SEND_INLINE', True):
        transaction.on_commit(lambda: attempt(ids, timeout=INLINE_TIMEOUT_SECONDS), robust=True)


def _server_side(provider_id):
    """`(provider, config)` when this provider is active AND sends purchases from the server; else None."""
    from ..integrations import registry, service

    provider = registry.get(provider_id)
    config = service.resolve(provider_id) if provider is not None else None
    if config is None or provider.runtime_public(config).get('purchase') == 'browser':
        return None
    return provider, config


def _plan(order) -> list:
    from ..models import ConversionDelivery, MeasurementContext

    context = MeasurementContext.objects.filter(order=order).first()
    if context is None:
        return []
    ids = []
    for provider_id in PROVIDER_IDS:
        active = _server_side(provider_id)
        if active is None or not getattr(context, active[0].consent, False):
            continue
        row, _created = ConversionDelivery.objects.get_or_create(
            order=order, provider=provider_id, event=PURCHASE, defaults={'event_id': event_id(order)})
        ids.append(row.pk)
    if not ids:
        context.delete()                                 # nothing will ever read it
    return ids


# -- sending ---------------------------------------------------------------------------------

def attempt(delivery_ids, timeout=None) -> None:
    """Best effort. Swallows everything, twice over: the row keeps the truth."""
    for delivery_id in delivery_ids:
        try:
            deliver(delivery_id, timeout=timeout)
        except Exception as exc:  # noqa: BLE001 — nothing may surface from on_commit
            logger.error('conversion delivery crashed (%s)', type(exc).__name__)
            try:
                _release(delivery_id)
            except Exception as again:  # noqa: BLE001 — the database itself; the timer finds the row later
                logger.error('conversion delivery could not be released (%s)', type(again).__name__)


def due(now=None):
    from ..models import ConversionDelivery

    now = now or timezone.now()
    S = ConversionDelivery.Status
    return ConversionDelivery.objects.filter(
        models_q(status=S.PENDING) & (models_q(next_attempt_at__isnull=True) | models_q(next_attempt_at__lte=now))
        | models_q(status=S.SENDING, last_attempt_at__lt=now - STUCK)
    ).order_by('pk')


def models_q(**kwargs):
    from django.db.models import Q

    return Q(**kwargs)


def _release(delivery_id) -> None:
    """A row whose sending crashed goes back to the queue with its attempt counted."""
    from ..models import ConversionDelivery

    S = ConversionDelivery.Status
    row = ConversionDelivery.objects.filter(pk=delivery_id, status=S.SENDING).first()
    if row is not None:
        _retry_or_fail(row, 'error', '')
        _forget_context_when_done(row.order_id)


def deliver(delivery_id, timeout=None) -> None:
    from ..models import ConversionDelivery, MeasurementContext

    S = ConversionDelivery.Status
    now = timezone.now()
    # Claim it: of two senders, one changes the row and the other changes nothing.
    claimed = ConversionDelivery.objects.filter(pk=delivery_id).filter(
        models_q(status=S.PENDING) | models_q(status=S.SENDING, last_attempt_at__lt=now - STUCK)
    ).update(status=S.SENDING, last_attempt_at=now)
    if not claimed:
        return
    row = ConversionDelivery.objects.select_related('order').get(pk=delivery_id)
    row.attempt_count += 1
    row.save(update_fields=['attempt_count'])
    order = row.order

    active = _server_side(row.provider)
    context = MeasurementContext.objects.filter(order=order).first()
    if active is None or context is None or not getattr(context, active[0].consent, False) or not order.paid:
        # Switched off since, or the consent is no longer on file: nothing is sent.
        _finish(row, S.SKIPPED)
        return
    if timezone.now() - (order.paid_at or row.created_at) > MAX_AGE[row.provider]:
        _finish(row, S.FAILED, 'too_old')
        return

    _provider, config = active
    answer = _SENDERS[row.provider](config, order, context, row.event_id, timeout or adapters.TIMEOUT_SECONDS)
    if answer.ok:
        row.sent_at = timezone.now()
        _finish(row, S.SENT, code=answer.code)
    elif answer.retryable:
        _retry_or_fail(row, answer.kind, answer.code)
        _forget_context_when_done(order.pk)
    else:
        _finish(row, S.FAILED, answer.kind, answer.code)


def _retry_or_fail(row, kind, code) -> None:
    S = type(row).Status
    row.failure_kind, row.provider_code = kind[:32], str(code or '')[:40]
    if row.attempt_count > len(BACKOFF_MINUTES):
        row.status, row.next_attempt_at = S.FAILED, None
    else:
        row.status = S.PENDING
        row.next_attempt_at = timezone.now() + timedelta(minutes=BACKOFF_MINUTES[row.attempt_count - 1])
    row.save(update_fields=['status', 'next_attempt_at', 'failure_kind', 'provider_code'])


def _finish(row, status, kind='', code='') -> None:
    row.status, row.next_attempt_at = status, None
    row.failure_kind, row.provider_code = kind[:32], str(code or '')[:40]
    row.save(update_fields=['status', 'next_attempt_at', 'failure_kind', 'provider_code', 'sent_at'])
    _forget_context_when_done(row.order_id)


def _forget_context_when_done(order_id) -> None:
    from ..models import ConversionDelivery, MeasurementContext

    if not ConversionDelivery.objects.filter(order_id=order_id).exclude(status__in=ConversionDelivery.FINAL).exists():
        MeasurementContext.objects.filter(order_id=order_id).delete()


def forget_old_contexts(now=None) -> int:
    from ..models import MeasurementContext

    deleted, _ = MeasurementContext.objects.filter(created_at__lt=(now or timezone.now()) - CONTEXT_RETENTION).delete()
    return deleted


# -- what is said about an order: amounts and products, never the person --------------------

def _money(value) -> float:
    return float(value or 0)


def order_summary(order) -> dict:
    """The order as a provider may see it. Built from the order's own figures, by an allow-list."""
    items = [{
        'id': str(item.product_id), 'name': item.product.name,
        'category': getattr(item.product.category, 'name', '') if item.product.category_id else '',
        'price': _money(item.price), 'quantity': int(item.quantity),
    } for item in order.items.select_related('product', 'product__category')]
    return {
        'transaction_id': str(order.pk), 'value': _money(order.total), 'currency': order.currency or 'PEN',
        'tax': _money(order.tax_amount), 'coupon': order.coupon_code or '', 'items': items,
    }


def browser_purchase(order):
    """
    What the success page is told once the order is PAID, so that the browser can
    emit its copy of the event with the id the server's carries — or None.

    FOR AN HOUR. The reference of a payment keeps opening its status for as long
    as the order exists; if it kept opening THIS, every new browser that used it
    would emit the purchase again. And without the coupon: the browser's copy does
    not need the code somebody was given.

    Never raises: it is called while answering a status poll.
    """
    try:
        if not order.paid_at or timezone.now() - order.paid_at > PURCHASE_TOLD_FOR:
            return None
        summary = order_summary(order)
        summary.pop('coupon', None)
        return {'event_id': event_id(order), **summary,
                'items': [{k: v for k, v in item.items() if v != ''} for item in summary['items']]}
    except Exception as exc:  # noqa: BLE001
        logger.error('purchase event not described for the browser (%s)', type(exc).__name__)
        return None


def _success_url() -> str:
    # A constant of the installation, not what the browser was showing: no token, no query.
    return (getattr(settings, 'FRONTEND_URL', '') or '').rstrip('/') + '/checkout/success'


def _send_google(config, order, context, _event_id, timeout):
    summary = order_summary(order)
    params = {
        'transaction_id': summary['transaction_id'], 'value': summary['value'], 'currency': summary['currency'],
        'tax': summary['tax'], 'engagement_time_msec': 1,
        'items': [{k: v for k, v in (('item_id', i['id']), ('item_name', i['name']), ('item_category', i['category']),
                                     ('price', i['price']), ('quantity', i['quantity'])) if v != ''}
                  for i in summary['items']],
    }
    if summary['coupon']:
        params['coupon'] = summary['coupon']
    if context.ga_session_id:
        params['session_id'] = context.ga_session_id
    paid_at = order.paid_at or timezone.now()
    advertising = 'GRANTED' if context.marketing else 'DENIED'
    return adapters.ga_send(config.get('measurement_id'), config.get('api_secret'), {
        # Without the browser's own id the purchase still counts; it is just not tied to a visit.
        'client_id': context.ga_client_id or f'{order.pk}.{int(paid_at.timestamp())}',
        'timestamp_micros': int(paid_at.timestamp() * 1_000_000),
        'consent': {'ad_user_data': advertising, 'ad_personalization': advertising},
        'events': [{'name': 'purchase', 'params': params}],
    }, timeout=timeout)


def _send_meta(config, order, context, shared_id, timeout):
    summary = order_summary(order)
    user = {'client_ip_address': context.ip, 'client_user_agent': context.user_agent, 'fbp': context.fbp, 'fbc': context.fbc}
    return adapters.meta_send(config.get('pixel_id'), config.get('access_token'), [{
        'event_name': 'Purchase', 'event_time': int((order.paid_at or timezone.now()).timestamp()),
        'event_id': shared_id, 'action_source': 'website', 'event_source_url': _success_url(),
        'user_data': {key: value for key, value in user.items() if value},
        'custom_data': {
            'currency': summary['currency'], 'value': summary['value'], 'order_id': summary['transaction_id'],
            'content_type': 'product', 'content_ids': [i['id'] for i in summary['items']],
            'contents': [{'id': i['id'], 'quantity': i['quantity'], 'item_price': i['price']} for i in summary['items']],
            'num_items': sum(i['quantity'] for i in summary['items']),
        },
    }], config.get('test_event_code'), timeout=timeout)


def _send_tiktok(config, order, context, shared_id, timeout):
    summary = order_summary(order)
    user = {'ip': context.ip, 'user_agent': context.user_agent, 'ttp': context.ttp, 'ttclid': context.ttclid}
    return adapters.tiktok_send(config.get('pixel_code'), config.get('access_token'), [{
        'event': 'Purchase', 'event_time': int((order.paid_at or timezone.now()).timestamp()), 'event_id': shared_id,
        'user': {key: value for key, value in user.items() if value},
        'page': {'url': _success_url()},
        'properties': {
            'currency': summary['currency'], 'value': summary['value'], 'order_id': summary['transaction_id'],
            'content_type': 'product',
            'contents': [{k: v for k, v in (('content_id', i['id']), ('content_name', i['name']),
                                            ('content_category', i['category']), ('price', i['price']),
                                            ('quantity', i['quantity'])) if v != ''} for i in summary['items']],
        },
    }], config.get('test_event_code'), timeout=timeout)


_SENDERS = {'google_analytics': _send_google, 'meta': _send_meta, 'tiktok': _send_tiktok}
