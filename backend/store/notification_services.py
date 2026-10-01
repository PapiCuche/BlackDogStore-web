"""
The notification centre — M12B.

ONE ENTRY POINT: `emit()`. Business code says what happened; it does not decide
who is told, what the text says, or whether an e-mail goes out. Those are three
separate questions and they are answered here, once, for every event.

THE TRANSACTION BOUNDARY, WHICH IS THE PART THAT MATTERS
--------------------------------------------------------
    BEGIN
      the business change
      the NotificationEvent          ← durable, inside the transaction
      the Notification rows          ← durable, inside the transaction
    COMMIT
      on_commit: attempt e-mail      ← outside, because it leaves the process

A rollback takes the event with it, so nobody is told about something that did
not happen. And an SMTP failure cannot roll anything back, because by then
there is nothing left to roll back.

WHAT THIS MODULE MUST NEVER DO
------------------------------
Raise into business code. A repair that was completed is completed whether or
not anyone could be notified; letting a notification failure abort the
transaction would make the notice more important than the work.
"""

from __future__ import annotations

import logging

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from . import notification_events as events
from .models import (
    AnnouncementAudienceRule, Company, Membership, MembershipRoleAssignment,
    Notification, NotificationDelivery, NotificationEvent,
)
from .tenancy import has_branch_access, resolve_capabilities

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Recipient resolution
# ---------------------------------------------------------------------------

def resolve_internal_recipients(company, *, capability, branch=None):
    """
    The staff who should hear about something happening in `company`.

    BY CAPABILITY, NEVER BY ROLE NAME. "Whoever can hand a device back" is
    `service.delivery.manage`; a company that gave that to a custom role called
    "Mostrador" gets its counter staff notified, and one that narrowed it does
    not. Resolving by `role == 'sales'` would answer for the pilot tenant and
    lie about everybody else.

    BRANCH SCOPE IS PART OF THE QUESTION. An order ready in Miraflores is not
    news for somebody who only works in San Isidro. When `branch` is given,
    anybody without access to it is dropped.

    THE PLATFORM MASTER IS NOT INCLUDED, and this is the subtle one.
    `resolve_capabilities()` returns every capability for a superuser in EVERY
    company, so a naive "who holds this capability" query hands the master an
    inbox containing every event of every tenant on the platform. Being able to
    act anywhere is not the same as wanting to hear about everywhere.
    Authorisation and addressing are different questions; M12C is where the
    master deliberately sends, and there they opt in.
    """
    recipients = []
    for user in active_internal_users(company):
        if capability not in resolve_capabilities(user, company):
            continue
        if branch is not None and not has_branch_access(user, branch):
            continue
        recipients.append(user)
    return recipients


def active_internal_users(company):
    """
    Everybody who currently works at `company`, master excluded.

    THE ONE PLACE THAT DECIDES WHO COUNTS AS STAFF. M12B answered it inside
    `resolve_internal_recipients`; M12C needs the same answer for five more
    kinds of audience, and a second copy of these four conditions is a second
    place for "active" to quietly come to mean something different.

    The master exclusion travels with it, deliberately. A superuser holds every
    capability in every company, so any audience expressed as a query over
    capabilities would sweep them into every tenant's staff list. In M12C they
    SEND; they are not swept in as a side effect of being able to.
    """
    return [
        m.user for m in (
            Membership.objects
            .filter(company=company, is_active=True, company__is_active=True)
            .select_related('user')
        )
        if m.user is not None and m.user.is_active and not m.user.is_superuser
    ]


def resolve_audience(company, rules):
    """
    Turn audience RULES into the people who will actually be written to.

    Called ONCE, at publication. The result becomes `Notification` rows and
    those rows are the snapshot: somebody who loses the role tomorrow keeps
    the message, and somebody who gains it tomorrow never had it. Re-resolving
    on read would rewrite the past every morning.

    A person matched by four rules at once is one recipient. The union is taken
    here and the database constraint is still the guarantee behind it.
    """
    from .capabilities import is_valid_capability

    staff = active_internal_users(company)
    if not staff:
        return []

    chosen, seen = [], set()

    def take(user):
        if user.pk not in seen:
            seen.add(user.pk)
            chosen.append(user)

    for rule in rules:
        kind = rule.kind
        if kind == AnnouncementAudienceRule.Kind.ALL_COMPANY:
            for user in staff:
                take(user)
        elif kind == AnnouncementAudienceRule.Kind.BRANCH:
            branch = rule.branch
            # The real branch-access authority, not `membership.branch_id`.
            # A person with access to three shops works in three shops.
            if branch is None or branch.company_id != company.pk:
                continue
            for user in staff:
                if has_branch_access(user, branch):
                    take(user)
        elif kind == AnnouncementAudienceRule.Kind.ROLE:
            role = rule.role
            if role is None or role.company_id != company.pk:
                continue
            holders = set(
                MembershipRoleAssignment.objects
                .filter(role=role, is_active=True,
                        membership__company=company, membership__is_active=True)
                .values_list('membership__user_id', flat=True)
            )
            for user in staff:
                if user.pk in holders:
                    take(user)
        elif kind == AnnouncementAudienceRule.Kind.CAPABILITY:
            code = rule.capability_code
            if not code or not is_valid_capability(code):
                continue
            for user in staff:
                if code in resolve_capabilities(user, company):
                    take(user)
        elif kind == AnnouncementAudienceRule.Kind.USER:
            target = rule.user
            if target is None:
                continue
            for user in staff:
                if user.pk == target.pk:
                    take(user)

    return chosen


# ---------------------------------------------------------------------------
# Emission
# ---------------------------------------------------------------------------

def emit(
    *,
    company,
    event_type: str,
    event_key: str,
    title: str,
    body: str = '',
    target_type: str = '',
    target_id=None,
    users=(),
    customers=(),
    priority=Notification.Priority.INFO,
    payload=None,
    source=Notification.Source.SYSTEM,
):
    """
    Record that something happened and materialise one notice per recipient.

    Returns the `NotificationEvent`, or None when this key was already
    recorded — which is the normal answer to a replayed webhook, not an error.

    Called INSIDE the business transaction on purpose: see the module docstring.
    The e-mail attempt is scheduled with `on_commit` and therefore only happens
    if the business change survived.
    """
    if event_type not in events.ALL_EVENT_TYPES:
        raise ValueError(f'Evento desconocido: {event_type}')

    try:
        with transaction.atomic():
            event = NotificationEvent.objects.create(
                company=company, event_type=event_type, event_key=event_key,
                target_type=target_type, target_id=target_id,
                payload=payload or {},
            )
    except IntegrityError:
        # Already recorded. A replay, and the correct behaviour is silence.
        logger.debug('evento ya registrado: %s', event_key)
        return None

    made = []
    for user in _unique(users):
        made.append(_materialise(
            event, company, Notification.Audience.INTERNAL, title, body,
            priority, target_type, target_id, user=user, source=source,
        ))
    for customer in _unique(customers):
        made.append(_materialise(
            event, company, Notification.Audience.CUSTOMER, title, body,
            priority, target_type, target_id, customer=customer, source=source,
        ))

    if event_type in events.EMAIL_WORTHY_EVENTS:
        alive = [n for n in made if n is not None]
        transaction.on_commit(lambda: _deliver_emails(alive))

    return event


def _unique(items):
    """
    Deduplicate recipients, preserving order.

    One person can satisfy two recipient rules for the same event — the
    technician who is also the branch's delivery staff. The database constraint
    is the real guarantee; this just avoids provoking it.
    """
    seen, out = set(), []
    for item in items or ():
        if item is None or item.pk in seen:
            continue
        seen.add(item.pk)
        out.append(item)
    return out


def _materialise(event, company, audience, title, body, priority,
                 target_type, target_id, *, user=None, customer=None,
                 source=Notification.Source.SYSTEM):
    try:
        with transaction.atomic():
            return Notification.objects.create(
                event=event, company=company, audience=audience,
                user=user, customer=customer, source=source,
                # `body` is TRUNCATED, not widened. This field is a preview a
                # bell renders in one line; M12C's full text lives on the
                # `Announcement` the notification points at.
                title=title[:140], body=body[:400], priority=priority,
                target_type=target_type, target_id=target_id,
            )
    except IntegrityError:
        # The dedupe constraint fired: this person already has this notice.
        return None


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------

def _recipient_email(notification) -> str:
    if notification.user_id is not None:
        return (notification.user.email or '').strip()
    if notification.customer_id is not None:
        return (notification.customer.email or '').strip()
    return ''


def _deliver_emails(notifications):
    """
    Attempt the e-mail channel. Runs AFTER commit, and swallows everything.

    An exception here would surface from `on_commit`, long after the business
    code returned, in a place no caller can handle. The failure belongs in the
    delivery row, where an operator can see it and a future retry can find it.
    """
    for notification in notifications:
        try:
            deliver_email(notification)
        except Exception:  # noqa: BLE001 — see docstring
            logger.exception('fallo no controlado entregando notificación %s',
                             notification.pk)


def deliver_email(notification) -> NotificationDelivery:
    """
    Send one notification by e-mail, once.

    `get_or_create` on `(notification, channel)` so a retry UPDATES the attempt
    rather than inserting a second one — two rows for one channel is how
    somebody receives the same message twice.
    """
    delivery, _ = NotificationDelivery.objects.get_or_create(
        notification=notification,
        channel=NotificationDelivery.Channel.EMAIL,
    )
    if delivery.status == NotificationDelivery.Status.SENT:
        return delivery

    address = _recipient_email(notification)
    if not address:
        # Nothing was wrong; there is nowhere to send it. SKIPPED rather than
        # FAILED so a retry pass does not chase it forever.
        delivery.status = NotificationDelivery.Status.SKIPPED
        delivery.failure_reason = 'destinatario sin correo'
        delivery.save(update_fields=['status', 'failure_reason'])
        return delivery

    delivery.attempt_count += 1
    delivery.last_attempt_at = timezone.now()
    try:
        _send(notification, address)
    except Exception as exc:  # noqa: BLE001
        delivery.status = NotificationDelivery.Status.FAILED
        # The class and a short message. Never the traceback: it quotes the
        # call, and the call can carry credentials.
        delivery.failure_reason = f'{type(exc).__name__}: {exc}'[:200]
        delivery.save(update_fields=[
            'status', 'failure_reason', 'attempt_count', 'last_attempt_at',
        ])
        logger.warning('entrega por correo fallida (%s)', type(exc).__name__)
        return delivery

    delivery.status = NotificationDelivery.Status.SENT
    delivery.sent_at = timezone.now()
    delivery.failure_reason = ''
    delivery.save(update_fields=[
        'status', 'sent_at', 'failure_reason', 'attempt_count', 'last_attempt_at',
    ])
    return delivery


def _send(notification, address):
    """
    The actual send, through the project's existing mail configuration.

    NOT a second e-mail subsystem. `email_services` owns the rich transactional
    templates for orders — receipts with PDFs, tenant identity, the lot — and
    this does not touch them: it is the plain-text companion for notification
    events, and the order confirmation e-mail keeps being sent by the code that
    already sends it exactly once.
    """
    from django.core.mail import EmailMultiAlternatives
    from django.conf import settings as dj_settings
    from django.utils.html import escape

    company_name = notification.company.name
    subject = f'{company_name} · {notification.title}'
    text = f'{notification.title}\n\n{notification.body}\n\n— {company_name}'

    # ESCAPED. Company names, status labels and customer names are tenant data
    # and reach this HTML; an unescaped one is a stored XSS in somebody's mail
    # client.
    html = (
        f'<p><strong>{escape(notification.title)}</strong></p>'
        f'<p>{escape(notification.body)}</p>'
        f'<p style="color:#888">— {escape(company_name)}</p>'
    )
    message = EmailMultiAlternatives(
        subject=subject, body=text,
        from_email=dj_settings.DEFAULT_FROM_EMAIL, to=[address],
    )
    message.attach_alternative(html, 'text/html')
    message.send(fail_silently=False)


def retry_failed_delivery(delivery) -> NotificationDelivery:
    """Re-attempt one failed delivery. Idempotent: a SENT one is left alone."""
    if delivery.status == NotificationDelivery.Status.SENT:
        return delivery
    return deliver_email(delivery.notification)


# ---------------------------------------------------------------------------
# Inbox
# ---------------------------------------------------------------------------

def inbox_for_user(user, company):
    return Notification.objects.filter(
        company=company, user=user, audience=Notification.Audience.INTERNAL,
    )


def inbox_for_customer(customer, company):
    return Notification.objects.filter(
        company=company, customer=customer, audience=Notification.Audience.CUSTOMER,
    )


def mark_read(queryset, *, only_unread=True) -> int:
    """
    Stamp `read_at` server-side. Idempotent: already-read rows are untouched,
    so a second click does not move the timestamp.
    """
    if only_unread:
        queryset = queryset.filter(read_at__isnull=True)
    return queryset.update(read_at=timezone.now())


def unread_count(queryset) -> int:
    return queryset.filter(read_at__isnull=True).count()
