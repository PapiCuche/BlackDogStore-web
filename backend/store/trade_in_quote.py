"""
TRADE-IN VALUATION PRO · Phase 5a — the trade-in quote (docs/adr-trade-in-valuation.md §3.5, §5.3).

    assessment ──generate──▶ pending_approval ──approve──▶ approved ──issue──▶ issued ──accept/decline──▶ …
                         └──▶ approved (approved assessment, or nothing to approve)
    issued ──(valid_until passes)──▶ expired      the cron stamps it; every read derives it
    pending_approval|approved ──cancel──▶ cancelled
    approved|issued|accepted|declined|expired|rejected ──revise──▶ revision N+1 (N superseded)

GENERATING freezes the assessment («quoted») and copies its snapshot. ISSUING fixes `issued_at` and an ABSOLUTE
`valid_until` = issued_at + the company's hours (96 by default); the company's time zone only displays it. Approving
and issuing refuse a STALE snapshot (`calculation_stale`): the tariff or rule version used is no longer in force, the
device check is no longer clear, or the price of the device bought moved. Accepting refuses an expired quote even
before the cron passes. A REVISION recomputes from the assessment's answers with the tariff and rules in force now;
the previous revision keeps its own snapshot (nothing retroactive); revising an accepted quote needs a reason.

Approval follows the assessment's rules: somebody who took part in it (or generated or revised the quote) approves
only with `sales.tradein.approve_own` and a reason. Nothing here buys, receives, pays or credits (D1-D12).
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta
from decimal import Decimal

from django.db import connection, transaction
from django.utils import timezone

from . import trade_in_assessment as assessments
from . import trade_in_eligibility as eligibility
from . import trade_in_rules as rules
from . import trade_in_tariff as tariff
from .models import AdminAuditLog, TradeInAssessment, TradeInDeviceCheck, TradeInQuote
from .tenancy import has_capability

CAP_VIEW = assessments.CAP_VIEW
CAP_VALUE = assessments.CAP_VALUE
CAP_APPROVE = assessments.CAP_APPROVE
CAP_APPROVE_OWN = assessments.CAP_APPROVE_OWN

TariffError = tariff.TariffError
TariffPermissionError = tariff.TariffPermissionError
TariffStateError = tariff.TariffStateError
TariffConflict = tariff.TariffConflict

MAX_NOTE = 300


def _require(actor, company, capability, message):
    if not has_capability(actor, company, capability):
        raise TariffPermissionError(message)


def _audit(*, actor, company, action, quote, request=None, **metadata):
    AdminAuditLog.log(actor=actor if getattr(actor, 'is_authenticated', False) else None, action=action,
                      target_type='trade_in_quote', target_id=quote.pk,
                      metadata={'company_id': company.pk, 'quote_id': quote.pk, 'number': quote.label,
                                'revision': quote.revision, 'status': quote.status, **metadata},
                      request=request, company=company)


def _note(raw, field, check, *, required=False, label='la nota') -> str:
    text = ' '.join(str(raw or '').split())
    if len(text) > MAX_NOTE:
        raise TariffError(f'{label[0].upper()}{label[1:]} admite hasta {MAX_NOTE} caracteres.', field=field)
    if required and not text:
        raise TariffError(f'Indica {label}.', code='reason_required', field=field)
    eligibility.refuse_identifiers(text, field, company_id=check.company_id,
                                   digests=(check.imei_digest, check.imei2_digest, check.serial_digest),
                                   last4s=(check.imei_last4, check.imei2_last4))
    return text


def _next_number(company) -> int:
    if connection.vendor == 'postgresql':
        key = int.from_bytes(hashlib.sha256(f'trade-in-quote-number|{company.pk}'.encode()).digest()[:8],
                             'big', signed=True)
        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_advisory_xact_lock(%s)', [key])
    last = TradeInQuote.objects.filter(company=company).order_by('-number').values_list('number', flat=True).first()
    return (last or 0) + 1


def _lock(quote) -> tuple[TradeInDeviceCheck, TradeInAssessment, TradeInQuote]:
    """The one lock order: device check → assessment → quote (as `trade_in_assessment._lock` and `close_check`)."""
    assessment = TradeInAssessment.objects.only('pk', 'device_check_id').get(pk=quote.assessment_id)
    check, locked_assessment = assessments._lock(assessment)
    locked = TradeInQuote.objects.select_for_update(of=('self',)).get(pk=quote.pk)
    return check, locked_assessment, locked


def _stale(actor, company, quote, check) -> list[str]:
    """Why a quote's snapshot is no longer what today's tariff, rules, device check and prices say (empty: it is)."""
    today = tariff.company_today(company)
    reasons = []
    current = tariff.effective_version(quote.assessment.tariff_entry, on=today)
    if current is None or current.pk != quote.tariff_version_id:
        reasons.append('tariff')
    rules_now = rules.effective_rule_set(company, on=today)
    if rules_now is None or rules_now.pk != quote.rule_set_version_id:
        reasons.append('rules')
    if check.status != TradeInDeviceCheck.STATUS_OPEN or eligibility.evaluate(check)['eligibility'] != eligibility.ELIGIBLE:
        reasons.append('security')
    if quote.assessment.purpose == TradeInAssessment.PURPOSE_TRADE_TOWARDS:
        try:
            price_now = assessments._target_price(actor, company, quote.assessment)
        except TariffError:
            price_now = None
        if price_now != quote.target_price:
            reasons.append('target_price')
    return reasons


def _refuse_stale(actor, company, quote, check) -> None:
    stale = _stale(actor, company, quote, check)
    if stale:
        raise TariffConflict('La tarifa, las reglas, la comprobación del equipo o el precio ya no son los de esta '
                             'cotización: revísala.', code='calculation_stale', extra={'reasons': stale})


def _snapshot_fields(company, *, assessment, tariff_version, rules_version, calculation, eligibility_value, computed,
                     offered, reason, price, difference) -> dict:
    policy = tariff.policy_of(company)
    return {
        'tariff_version': tariff_version, 'rule_set_version': rules_version, 'calculation': calculation,
        'eligibility': eligibility_value, 'computed_value': computed, 'offered_value': offered,
        'adjustment_reason': reason, 'max_reception_value': tariff_version.max_reception_value,
        'currency': tariff_version.currency, 'target_price': price, 'difference': difference,
        'refurbishment_cost': tariff_version.refurbishment_cost, 'resale_value': tariff_version.resale_value,
        'target_margin': tariff_version.target_margin, 'conditions': policy.quote_conditions,
        'public_uid': secrets.token_hex(16),
    }


def _quotable(eligibility_value) -> None:
    if eligibility_value == assessments.NOT_ELIGIBLE:
        raise TariffStateError('Un equipo no elegible no se cotiza.', code='not_eligible')
    if eligibility_value == assessments.PENDING_VERIFICATION:
        raise TariffStateError('Hay una verificación pendiente: complétala antes de cotizar.',
                               code='pending_verification')


def generate(*, actor, company, assessment, request=None) -> TradeInQuote:
    """Revision 1 of a quote from a calculated (or approved) assessment, which becomes «quoted» and frozen."""
    _require(actor, company, CAP_VALUE, 'No tienes permiso para cotizar equipos usados.')
    if assessment.company_id != company.pk:
        raise TariffError('No encontrado.', code='not_found')
    with transaction.atomic():
        check, assessment = assessments._lock(assessment)
        if assessment.status not in (TradeInAssessment.STATUS_CALCULATED, TradeInAssessment.STATUS_APPROVED):
            raise TariffStateError('Sólo se cotiza una evaluación calculada.', code='not_calculated')
        _quotable(assessment.eligibility)
        if check.status != TradeInDeviceCheck.STATUS_OPEN:
            raise TariffStateError('La comprobación del equipo está cerrada.', code='check_closed')
        stale = assessments._stale(company, assessment)
        if eligibility.evaluate(check)['eligibility'] != eligibility.ELIGIBLE:
            stale.append('security')
        if stale:
            raise TariffConflict('La evaluación ya no está al día: vuelve a calcularla.', code='calculation_stale',
                                 extra={'reasons': stale})
        approved = assessment.status == TradeInAssessment.STATUS_APPROVED
        if approved:
            status, kind = TradeInQuote.STATUS_APPROVED, TradeInQuote.APPROVAL_ASSESSMENT
        elif assessment.eligibility == assessments.REQUIRES_APPROVAL:
            status, kind = TradeInQuote.STATUS_PENDING_APPROVAL, ''
        else:
            status, kind = TradeInQuote.STATUS_APPROVED, TradeInQuote.APPROVAL_NOT_NEEDED
        quote = TradeInQuote.objects.create(
            company=company, branch=assessment.branch, assessment=assessment, number=_next_number(company),
            revision=1, origin='new', status=status, approval_kind=kind,
            approved_by=assessment.approved_by if approved else None,
            approved_at=assessment.approved_at if approved else None,
            created_by=actor if getattr(actor, 'is_authenticated', False) else None,
            **_snapshot_fields(company, assessment=assessment, tariff_version=assessment.tariff_version,
                               rules_version=assessment.rule_set_version, calculation=assessment.calculation,
                               eligibility_value=assessment.eligibility, computed=assessment.computed_value,
                               offered=assessment.offered_value, reason=assessment.adjustment_reason,
                               price=assessment.target_price, difference=assessment.difference))
        assessment.status = TradeInAssessment.STATUS_QUOTED
        assessment.save(update_fields=['status', 'updated_at'])
        _audit(actor=actor, company=company, action='trade_in_quote_generated', quote=quote, request=request,
               assessment=assessment.pk, offered=str(quote.offered_value), approval_kind=kind)
    return quote


def _authors(quote) -> set:
    authors = assessments._authors(quote.assessment)
    for revision in TradeInQuote.objects.filter(company_id=quote.company_id, number=quote.number):
        if revision.created_by_id:
            authors.add(revision.created_by_id)
    return authors


def approve(*, actor, company, quote, note='', request=None) -> TradeInQuote:
    _require(actor, company, CAP_APPROVE, 'No tienes permiso para aprobar cotizaciones de recepción.')
    if quote.company_id != company.pk:
        raise TariffError('No encontrado.', code='not_found')
    with transaction.atomic():
        check, _assessment, quote = _lock(quote)
        note = _note(note, 'note', check)
        if quote.status != TradeInQuote.STATUS_PENDING_APPROVAL:
            raise TariffStateError('Esta cotización no está pendiente de aprobación.', code='not_pending')
        own = getattr(actor, 'pk', None) in _authors(quote)
        if own:
            if not has_capability(actor, company, CAP_APPROVE_OWN):
                raise TariffPermissionError('Otra persona con permiso de aprobación tiene que aprobarla.',
                                            code='own_change')
            if not note:
                raise TariffError('Indica el motivo para aprobar una cotización propia.', code='reason_required',
                                  field='note')
        _refuse_stale(actor, company, quote, check)
        quote.status = TradeInQuote.STATUS_APPROVED
        quote.approval_kind = TradeInQuote.APPROVAL_SELF if own else TradeInQuote.APPROVAL_APPROVER
        quote.approved_by, quote.approved_at, quote.approval_note = actor, timezone.now(), note
        quote.save(update_fields=['status', 'approval_kind', 'approved_by', 'approved_at', 'approval_note',
                                  'updated_at'])
        _audit(actor=actor, company=company, action='trade_in_quote_approved', quote=quote, request=request,
               self_approval=own, has_note=bool(note))
    return quote


def reject(*, actor, company, quote, note='', request=None) -> TradeInQuote:
    _require(actor, company, CAP_APPROVE, 'No tienes permiso para aprobar cotizaciones de recepción.')
    if quote.company_id != company.pk:
        raise TariffError('No encontrado.', code='not_found')
    with transaction.atomic():
        check, _assessment, quote = _lock(quote)
        note = _note(note, 'note', check, required=True, label='el motivo')
        if quote.status != TradeInQuote.STATUS_PENDING_APPROVAL:
            raise TariffStateError('Esta cotización no está pendiente de aprobación.', code='not_pending')
        quote.status = TradeInQuote.STATUS_REJECTED
        quote.decided_by, quote.decided_at, quote.decision_note = actor, timezone.now(), note
        quote.save(update_fields=['status', 'decided_by', 'decided_at', 'decision_note', 'updated_at'])
        _audit(actor=actor, company=company, action='trade_in_quote_rejected', quote=quote, request=request,
               has_note=True)
    return quote


def issue(*, actor, company, quote, request=None, now=None) -> TradeInQuote:
    """Hand the quote to the customer: an exact instant of issue and of expiry (issued + the company's hours)."""
    _require(actor, company, CAP_VALUE, 'No tienes permiso para cotizar equipos usados.')
    if quote.company_id != company.pk:
        raise TariffError('No encontrado.', code='not_found')
    with transaction.atomic():
        check, _assessment, quote = _lock(quote)
        if quote.status != TradeInQuote.STATUS_APPROVED:
            raise TariffStateError('Sólo se emite una cotización aprobada.', code='not_approved')
        _refuse_stale(actor, company, quote, check)
        issued = now or timezone.now()
        quote.status = TradeInQuote.STATUS_ISSUED
        quote.issued_by, quote.issued_at = actor, issued
        quote.valid_until = issued + timedelta(hours=tariff.policy_of(company).quote_validity_hours)
        quote.save(update_fields=['status', 'issued_by', 'issued_at', 'valid_until', 'updated_at'])
        _audit(actor=actor, company=company, action='trade_in_quote_issued', quote=quote, request=request,
               valid_until=quote.valid_until.isoformat())
    return quote


def decide(*, actor, company, quote, accepted: bool, note='', request=None) -> TradeInQuote:
    """The customer's answer, as staff recorded it. An expired quote is refused even before the cron passes."""
    _require(actor, company, CAP_VALUE, 'No tienes permiso para cotizar equipos usados.')
    if quote.company_id != company.pk:
        raise TariffError('No encontrado.', code='not_found')
    with transaction.atomic():
        check, _assessment, quote = _lock(quote)
        note = _note(note, 'note', check)
        if quote.status != TradeInQuote.STATUS_ISSUED:
            raise TariffStateError('Sólo se responde una cotización emitida.', code='not_issued')
        if quote.is_expired:
            raise TariffStateError('La cotización venció: revísala para ofrecer otra.', code='expired')
        quote.status = TradeInQuote.STATUS_ACCEPTED if accepted else TradeInQuote.STATUS_DECLINED
        quote.decided_by, quote.decided_at, quote.decision_note = actor, timezone.now(), note
        quote.save(update_fields=['status', 'decided_by', 'decided_at', 'decision_note', 'updated_at'])
        _audit(actor=actor, company=company, action=f'trade_in_quote_{"accepted" if accepted else "declined"}',
               quote=quote, request=request, has_note=bool(note))
    return quote


def cancel(*, actor, company, quote, reason, request=None) -> TradeInQuote:
    _require(actor, company, CAP_VALUE, 'No tienes permiso para cotizar equipos usados.')
    if quote.company_id != company.pk:
        raise TariffError('No encontrado.', code='not_found')
    with transaction.atomic():
        check, _assessment, quote = _lock(quote)
        reason = _note(reason, 'reason', check, required=True, label='el motivo')
        if quote.status not in (TradeInQuote.STATUS_PENDING_APPROVAL, TradeInQuote.STATUS_APPROVED):
            raise TariffStateError('Sólo se anula una cotización que no se emitió.', code='not_cancellable')
        quote.status = TradeInQuote.STATUS_CANCELLED
        quote.decided_by, quote.decided_at, quote.decision_note = actor, timezone.now(), reason
        quote.save(update_fields=['status', 'decided_by', 'decided_at', 'decision_note', 'updated_at'])
        _audit(actor=actor, company=company, action='trade_in_quote_cancelled', quote=quote, request=request,
               has_reason=True)
    return quote


def revise(*, actor, company, quote, data=None, request=None) -> TradeInQuote:
    """
    Revision N+1: recomputed from the assessment's answers with the tariff and rules in force now (the offer may be
    adjusted again, with a reason); revision N is superseded and keeps its snapshot. An accepted quote needs a
    reason to be revised.
    """
    _require(actor, company, CAP_VALUE, 'No tienes permiso para cotizar equipos usados.')
    data = data or {}
    if quote.company_id != company.pk:
        raise TariffError('No encontrado.', code='not_found')
    today = tariff.company_today(company)
    with transaction.atomic():
        check, assessment, quote = _lock(quote)
        if quote.status not in TradeInQuote.REVISABLE:
            raise TariffStateError('Esta cotización no se revisa.', code='not_revisable')
        reason = _note(data.get('revision_reason'), 'revision_reason', check,
                       required=quote.status == TradeInQuote.STATUS_ACCEPTED, label='el motivo de la revisión')
        if check.status != TradeInDeviceCheck.STATUS_OPEN:
            raise TariffStateError('La comprobación del equipo está cerrada.', code='check_closed')
        done = assessments.compute(actor=actor, company=company, assessment=assessment, check=check, data=data,
                                   today=today)
        _quotable(done['eligibility'])
        needs = done['eligibility'] == assessments.REQUIRES_APPROVAL
        quote.status = TradeInQuote.STATUS_SUPERSEDED
        quote.superseded_at = timezone.now()
        quote.save(update_fields=['status', 'superseded_at', 'updated_at'])
        revision = TradeInQuote.objects.create(
            company=company, branch=quote.branch, assessment=assessment, number=quote.number,
            revision=quote.revision + 1, origin='revision', source_quote=quote, revision_reason=reason,
            status=TradeInQuote.STATUS_PENDING_APPROVAL if needs else TradeInQuote.STATUS_APPROVED,
            approval_kind='' if needs else TradeInQuote.APPROVAL_NOT_NEEDED,
            created_by=actor if getattr(actor, 'is_authenticated', False) else None,
            **_snapshot_fields(company, assessment=assessment, tariff_version=done['tariff_version'],
                               rules_version=done['rules_version'], calculation=done['calculation'],
                               eligibility_value=done['eligibility'], computed=done['computed'],
                               offered=done['offered'], reason=done['reason'], price=done['price'],
                               difference=done['difference']))
        _audit(actor=actor, company=company, action='trade_in_quote_revised', quote=revision, request=request,
               previous=quote.pk, previous_status=quote.status, offered=str(revision.offered_value),
               has_reason=bool(reason))
    return revision


def expire_due(*, company=None, now=None, dry_run: bool = False) -> int:
    """Stamp `expired` on every issued quote past its instant. Idempotent, cron-friendly; reads never write."""
    now = now or timezone.now()
    due = TradeInQuote.objects.filter(status=TradeInQuote.STATUS_ISSUED, valid_until__lte=now)
    if company is not None:
        due = due.filter(company=company)
    if dry_run:
        return due.count()
    count = 0
    for quote_id in list(due.values_list('pk', flat=True)):
        with transaction.atomic():
            locked = TradeInQuote.objects.select_for_update(of=('self',)).filter(pk=quote_id).first()
            if locked is None or locked.status != TradeInQuote.STATUS_ISSUED or locked.valid_until > now:
                continue
            locked.status = TradeInQuote.STATUS_EXPIRED
            locked.expired_at = now
            locked.save(update_fields=['status', 'expired_at', 'updated_at'])
            _audit(actor=None, company=locked.company, action='trade_in_quote_expired', quote=locked,
                   valid_until=locked.valid_until.isoformat())
            count += 1
    return count


def display_valid_until(company, quote) -> str:
    """The expiry instant as the company reads it («01/12/2026 18:30»)."""
    if quote.valid_until is None:
        return ''
    return timezone.localtime(quote.valid_until, tariff.company_timezone(company)).strftime('%d/%m/%Y %H:%M')
