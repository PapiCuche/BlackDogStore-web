"""
M12C — composing and publishing internal communiqués.

THE ONE IDEA THIS MODULE EXISTS TO PROTECT:

    THE AUDIENCE FREEZES AT PUBLICATION.

A communiqué is resolved once, into rows, and those rows are the record. Read
it a year later and it still says who it was sent to, because it was written
down rather than recomputed. The alternative — asking "who holds this role?"
every time somebody opens the page — quietly rewrites history every morning: a
new hire would appear to have received last year's message, and the person it
was actually written for would vanish from it the day they changed jobs.

    A CHANGE OF ROLE TOMORROW DOES NOT REWRITE A MESSAGE FROM YESTERDAY.

PUBLISHED IS IMMUTABLE for the same reason. There is no unsend and no edit; a
correction is a new communiqué. An inbox somebody can rewrite behind you is
worse than no record at all.

GLOBAL IS ALWAYS EXPLICIT. There is no code path here where an empty target
means "everyone". A publication with no rules is refused, not broadened.
"""

from __future__ import annotations

import logging

from django.db import IntegrityError, transaction
from django.utils import timezone

from . import notification_events as events
from . import notification_services as notif
from .capabilities import is_valid_capability
from .models import (
    AdminAuditLog, Announcement, AnnouncementAudienceRule, Branch, Company,
    CompanyRole, Membership, Notification,
)

logger = logging.getLogger(__name__)

TITLE_MAX = 140
BODY_MAX = 4000


class AnnouncementError(Exception):
    """A refusal a caller can act on."""


class AnnouncementStateError(AnnouncementError):
    """The communiqué is not in a state where this makes sense."""


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------

def _clean_text(title, body):
    title = (title or '').strip()
    body = (body or '').strip()
    if not title:
        raise AnnouncementError('El comunicado necesita un título.')
    if not body:
        raise AnnouncementError('El comunicado necesita un cuerpo.')
    if len(title) > TITLE_MAX:
        raise AnnouncementError(f'El título no puede pasar de {TITLE_MAX} caracteres.')
    if len(body) > BODY_MAX:
        raise AnnouncementError(f'El cuerpo no puede pasar de {BODY_MAX} caracteres.')
    return title, body


def create_draft(*, author, source_company=None, title, body,
                 priority=Notification.Priority.INFO):
    """
    A draft, and NOTHING ELSE.

    Saving a draft ten times produces ten saves and zero recipients. Nothing is
    resolved, no event is recorded and no inbox is touched until somebody
    publishes on purpose.
    """
    title, body = _clean_text(title, body)
    if priority not in dict(Notification.Priority.choices):
        raise AnnouncementError('Prioridad desconocida.')
    return Announcement.objects.create(
        author=author, source_company=source_company,
        title=title, body=body, priority=priority,
        status=Announcement.Status.DRAFT,
    )


def update_draft(*, announcement, title=None, body=None, priority=None):
    """Editable until it is published, and not one moment after."""
    locked = _lock(announcement)
    _require_draft(locked)
    new_title = locked.title if title is None else title
    new_body = locked.body if body is None else body
    new_title, new_body = _clean_text(new_title, new_body)
    if priority is not None and priority not in dict(Notification.Priority.choices):
        raise AnnouncementError('Prioridad desconocida.')
    locked.title = new_title
    locked.body = new_body
    if priority is not None:
        locked.priority = priority
    locked.save(update_fields=['title', 'body', 'priority', 'updated_at'])
    return locked


def cancel_draft(*, announcement, actor=None, request=None):
    """
    Throw a draft away. NOT a recall.

    `CANCELLED` never applies to something that went out: nothing here can
    reach into an inbox and remove a message somebody already read.
    """
    with transaction.atomic():
        locked = _lock(announcement)
        if locked.status == Announcement.Status.CANCELLED:
            return locked
        _require_draft(locked)
        locked.status = Announcement.Status.CANCELLED
        locked.save(update_fields=['status', 'updated_at'])
        AdminAuditLog.log(
            actor=actor, action='announcement_cancelled',
            target_type='announcement', target_id=locked.pk,
            metadata={'title': locked.title},
            request=request, company=locked.source_company,
        )
    return locked


def _lock(announcement):
    return Announcement.objects.select_for_update().get(pk=announcement.pk)


def _require_draft(announcement):
    if announcement.status != Announcement.Status.DRAFT:
        raise AnnouncementStateError(
            'Un comunicado publicado no se edita. Publica una corrección.'
        )


# ---------------------------------------------------------------------------
# Audience rules
# ---------------------------------------------------------------------------

def set_audience(*, announcement, rules):
    """
    Replace the audience of a DRAFT.

    Every rule is validated against the company it names, and the company is
    always named. A branch, role or user belonging to a different tenant is not
    a rule with a small mistake in it — it is a request to address somebody
    else's staff, and it is refused rather than filtered.
    """
    with transaction.atomic():
        locked = _lock(announcement)
        _require_draft(locked)
        prepared = [_validate_rule(locked, spec) for spec in rules]
        if not prepared:
            raise AnnouncementError('Elige a quién va dirigido el comunicado.')
        locked.audience_rules.all().delete()
        AnnouncementAudienceRule.objects.bulk_create(prepared)
    return list(locked.audience_rules.all())


def _validate_rule(announcement, spec):
    Kind = AnnouncementAudienceRule.Kind
    company = spec.get('company')
    if company is None or not isinstance(company, Company):
        raise AnnouncementError('Cada regla debe indicar una empresa.')
    if not company.is_active:
        raise AnnouncementError(f'La empresa {company.slug} no está activa.')

    kind = spec.get('kind')
    if kind not in dict(Kind.choices):
        raise AnnouncementError('Tipo de audiencia desconocido.')

    rule = AnnouncementAudienceRule(
        announcement=announcement, company=company, kind=kind,
    )

    if kind == Kind.BRANCH:
        branch = spec.get('branch')
        if not isinstance(branch, Branch) or branch.company_id != company.pk:
            raise AnnouncementError('Esa sucursal no pertenece a la empresa.')
        rule.branch = branch
    elif kind == Kind.ROLE:
        role = spec.get('role')
        if not isinstance(role, CompanyRole) or role.company_id != company.pk:
            raise AnnouncementError('Ese rol no pertenece a la empresa.')
        rule.role = role
    elif kind == Kind.CAPABILITY:
        code = (spec.get('capability_code') or '').strip()
        if not is_valid_capability(code):
            raise AnnouncementError('Esa capacidad no existe en el catálogo.')
        rule.capability_code = code
    elif kind == Kind.USER:
        user = spec.get('user')
        if user is None:
            raise AnnouncementError('Falta la persona destinataria.')
        member = Membership.objects.filter(
            user=user, company=company, is_active=True,
        ).exists()
        if not member:
            raise AnnouncementError('Esa persona no trabaja en la empresa.')
        rule.user = user

    return rule


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------

def preview(announcement):
    """
    How many people this WOULD reach, if published right now.

    INFORMATIVE, NEVER AUTHORITATIVE. The number can be stale by the time the
    button is pressed — somebody joins, somebody leaves — and publication
    resolves the audience again from scratch. A preview that were treated as
    the snapshot would let a stale count decide who gets a message.
    """
    by_company = {}
    for company, rules in _rules_by_company(announcement).items():
        by_company[company] = notif.resolve_audience(company, rules)
    return {
        'companies': [
            {
                'slug': c.slug, 'name': c.name,
                'recipient_count': len(users),
            }
            for c, users in sorted(by_company.items(), key=lambda kv: kv[0].slug)
        ],
        'company_count': len(by_company),
        'recipient_count': sum(len(u) for u in by_company.values()),
    }


def _rules_by_company(announcement):
    grouped = {}
    rules = (
        announcement.audience_rules
        .select_related('company', 'branch', 'role', 'user')
        .order_by('pk')
    )
    for rule in rules:
        grouped.setdefault(rule.company, []).append(rule)
    return grouped


# ---------------------------------------------------------------------------
# Publication
# ---------------------------------------------------------------------------

def publish(*, announcement, actor=None, request=None):
    """
    Resolve the audience, write the rows, mark it published. Once.

    ATOMIC, so a failure halfway leaves no communiqué that reached eight
    tenants out of ten and claims to be published.

    IDEMPOTENT at two levels. The status transition happens under a row lock,
    so a double click finds the second attempt already published and returns
    it. And the event key is derived from the announcement and the company, so
    even if a second fanout were somehow reached it would write no second
    notice.

    ONE EVENT PER COMPANY, because `NotificationEvent.company` is NOT NULL and
    a platform-wide message that shared one event would put two tenants' notices
    behind a single row. Isolation is not something to relax for convenience.
    """
    with transaction.atomic():
        locked = _lock(announcement)

        if locked.status == Announcement.Status.PUBLISHED:
            # Already out. The retry of a request that worked is not an error.
            return locked
        _require_draft(locked)

        grouped = _rules_by_company(locked)
        if not grouped:
            # An empty target NEVER means everybody. It means nobody chose one.
            raise AnnouncementError('Elige a quién va dirigido el comunicado.')

        published_at = timezone.now()
        total = 0
        for company, rules in grouped.items():
            recipients = notif.resolve_audience(company, rules)
            if not recipients:
                continue
            notif.emit(
                company=company,
                event_type=events.COMMUNICATIONS_ANNOUNCEMENT_PUBLISHED,
                # From the document and the tenant, never from the request.
                # No timestamp, no client-supplied uuid: a retry must land on
                # the same key or it is not a retry.
                event_key=events.event_key(
                    events.COMMUNICATIONS_ANNOUNCEMENT_PUBLISHED,
                    'announcement', locked.pk, company.pk,
                ),
                title=locked.title,
                # The preview. The whole text stays on the Announcement, and
                # the notification points at it.
                body=locked.body,
                target_type='announcement', target_id=locked.pk,
                users=recipients,
                priority=locked.priority,
                source=Notification.Source.ANNOUNCEMENT,
            )
            total += len(recipients)

        locked.status = Announcement.Status.PUBLISHED
        locked.published_at = published_at
        locked.recipient_count = total
        locked.save(update_fields=[
            'status', 'published_at', 'recipient_count', 'updated_at',
        ])

        AdminAuditLog.log(
            actor=actor, action='announcement_published',
            target_type='announcement', target_id=locked.pk,
            metadata={
                'title': locked.title,
                'scope': sorted(c.slug for c in grouped),
                'company_count': len(grouped),
                'recipient_count': total,
                # No names, no e-mail addresses, no recipient ids. The log
                # records that a message went out and how widely; who read it
                # is a question for the notification rows, under their own
                # authorisation.
            },
            request=request, company=locked.source_company,
        )
    return locked


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

def stats(announcement):
    """
    Aggregates only.

    `recipient_count` is the FROZEN denominator: counting live notification
    rows would be almost the same number and wrong for the same reason the
    audience is frozen. Read counts come from `Notification.read_at`, which
    M12B already keeps — a separate receipt table would be a second answer to
    a question that already has one.

    No per-person list. Knowing that eleven of forty have read a notice is
    management; knowing which eleven, by default, is surveillance, and nothing
    in this phase needs it.
    """
    rows = Notification.objects.filter(
        source=Notification.Source.ANNOUNCEMENT,
        target_type='announcement', target_id=announcement.pk,
    )
    delivered = rows.count()
    read = rows.filter(read_at__isnull=False).count()
    total = announcement.recipient_count or delivered
    return {
        'recipients': total,
        'read': read,
        'unread': max(0, total - read),
        'read_pct': round(100.0 * read / total, 1) if total else 0.0,
    }
