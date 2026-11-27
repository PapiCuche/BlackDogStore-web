"""
TRADE-IN VALUATION PRO · Phase 1 — the per-company reception tariff (docs/adr-trade-in-valuation.md §3.1, §4, §7).

WHAT IT IS
----------
What the company would pay for a used device, per device as the company names it (brand, model, capacity, SIM,
region), with the money the owner wants kept apart: the base reception value, the expected refurbishment cost, the
estimated resale value, the commercial margin aimed at and the maximum reception value authorised. Everything here
is the company's own data: no brand, model, price or factor is written in code.

ENTRIES AND VERSIONS
--------------------
An entry is the device; it is never deleted, only deactivated. Its money lives in versions, append-only:

    propose  → `pending` (a relevant change)   → approve → `approved`
                                               → reject  → `rejected`
                                               → withdraw→ `withdrawn`
             → `approved` by the company's policy when the change is not relevant

A change is RELEVANT (`approval_reasons`) when it creates the first price of a device, raises the base or the
maximum reception value above the company's tolerance, or extends the validity; with the policy `all`, every change
is. One change waits per device at a time (database constraint). Approving a change one proposed needs
`approve_own` and a reason, flagged in the audit (the DEC-INV-06 pattern).

IN FORCE ON A DAY (owner's decision, 2026-11-27): the approved version with the highest number that has STARTED by
that day (`valid_from <= day`, the COMPANY'S calendar), of an ACTIVE entry, and only if its window still contains the
day. A newer version replaces the older ones for good: when a newer version with an end date expires, an older one
does NOT come back — the device has no price until a new version is approved (`effective_version`). A version
approved for a later start leaves the current one in force until its day. Later phases freeze the version a
valuation used (id and values), so a new price never rewrites an issued quote.

WHAT IT NEVER DOES
------------------
It never receives a device, never writes stock, a payable, a credit, a payment or a fiscal document (D1–D12).
Every write leaves an `AdminAuditLog` with the company; no IMEI or personal datum ever reaches it.
"""
from __future__ import annotations

import re
import zoneinfo
from datetime import date
from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction
from django.utils import timezone

from .configuration_resolver import fold
from .models import (
    AdminAuditLog,
    ProductModel,
    TradeInPolicy,
    TradeInTariffEntry,
    TradeInTariffVersion,
)
from .tenancy import has_capability

CAP_VIEW = 'sales.tradein.tariff.view'
CAP_MANAGE = 'sales.tradein.tariff.manage'
CAP_APPROVE = 'sales.tradein.tariff.approve'
CAP_APPROVE_OWN = 'sales.tradein.tariff.approve_own'

#: The five amounts of a version, in the order the owner listed them. `REQUIRED_MONEY` must be given.
MONEY_FIELDS = ('base_reception_value', 'refurbishment_cost', 'resale_value', 'target_margin', 'max_reception_value')
REQUIRED_MONEY = ('base_reception_value', 'max_reception_value')
MONEY_LABELS = {
    'base_reception_value': 'el valor base de recepción',
    'refurbishment_cost': 'el costo de reacondicionamiento',
    'resale_value': 'el valor estimado de reventa',
    'target_margin': 'el margen comercial',
    'max_reception_value': 'el valor máximo autorizado',
}
#: The identity of a device, in the company's words.
IDENTITY_FIELDS = ('brand', 'model_name', 'capacity', 'sim', 'region')
IDENTITY_LIMITS = {'brand': 80, 'model_name': 160, 'capacity': 40, 'sim': 40, 'region': 40}
IDENTITY_LABELS = {'brand': 'La marca', 'model_name': 'El modelo', 'capacity': 'La capacidad', 'sim': 'El SIM',
                   'region': 'La región'}

MAX_AMOUNT = Decimal('9999999999.99')
MAX_REASON = 300

REASON_NEW_ENTRY = 'new_entry'
REASON_BASE_INCREASE = 'base_increase'
REASON_MAX_INCREASE = 'max_increase'
REASON_VALIDITY_EXTENDED = 'validity_extended'
REASON_STARTS_EARLIER = 'starts_earlier'
REASON_POLICY_ALL = 'policy_all'
REASON_LABELS = {
    REASON_NEW_ENTRY: 'Primer precio del equipo',
    REASON_BASE_INCREASE: 'Sube el valor base de recepción',
    REASON_MAX_INCREASE: 'Sube el valor máximo autorizado',
    REASON_VALIDITY_EXTENDED: 'Amplía la vigencia',
    REASON_STARTS_EARLIER: 'Adelanta el inicio o pone precio donde no lo hay',
    REASON_POLICY_ALL: 'La política de la empresa pide aprobar todo cambio',
}

#: Exactly `YYYY-MM-DD`: nothing after it.
_ISO_DATE = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}')

#: A device key joins the five folded words with this separator (never typed: «|» is folded out of each word).
_KEY_SEPARATOR = '|'


class TariffError(Exception):
    """The request cannot be honoured. HTTP 400 unless a subclass says otherwise."""

    status = 400
    code = 'invalid'

    def __init__(self, message: str, *, code: str | None = None, field: str | None = None,
                 extra: dict | None = None):
        super().__init__(message)
        if code:
            self.code = code
        self.field = field
        self.extra = extra or {}


class TariffPermissionError(TariffError):
    status = 403
    code = 'forbidden'


class TariffStateError(TariffError):
    """The entry or version is not in a state that allows this (409)."""

    status = 409
    code = 'invalid_state'


class TariffConflict(TariffError):
    status = 409
    code = 'conflict'


# ---------------------------------------------------------------------------
# Small pure helpers
# ---------------------------------------------------------------------------

def company_timezone(company):
    """The company's own time zone, or the installation's when it has none (or a stored one no longer resolves)."""
    name = getattr(getattr(company, 'settings', None), 'timezone', '') or ''
    if name:
        try:
            return zoneinfo.ZoneInfo(name)
        except Exception:
            pass
    return timezone.get_default_timezone()


def company_today(company, *, now=None) -> date:
    """Today as the company reckons it."""
    return timezone.localdate(now or timezone.now(), company_timezone(company))


def company_currency(company) -> str:
    return (getattr(getattr(company, 'settings', None), 'currency', '') or 'PEN').upper()


def _word(value) -> str:
    """One identity word, as stored: whitespace collapsed. The key folds it further."""
    return ' '.join(str(value or '').split())


def device_key(brand, model_name, capacity='', sim='', region='') -> str:
    """
    The identity of a device in a company's tariff: the five words folded (case, accents, spaces) and joined.
    «Modelo  13», «MODELO 13» are one model; «128 GB» and «128gb» one capacity (spaces dropped inside a word).
    """
    parts = []
    for index, value in enumerate((brand, model_name, capacity, sim, region)):
        folded = fold(value).replace(_KEY_SEPARATOR, ' ')
        if index == 2:  # a capacity compares without spaces: «128 gb» = «128gb»
            folded = folded.replace(' ', '')
        parts.append(' '.join(folded.split()))
    return _KEY_SEPARATOR.join(parts)


def parse_money(raw, label: str, *, required: bool) -> Decimal | None:
    """An amount from a JSON body (number or plain decimal text): two decimals at most, never negative."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        if required:
            raise TariffError(f'Indica {label}.', code='required')
        return None
    if isinstance(raw, bool):
        raise TariffError(f'{label[0].upper()}{label[1:]} no es un importe válido.')
    try:
        value = Decimal(str(raw).strip())
    except (InvalidOperation, ValueError):
        raise TariffError(f'{label[0].upper()}{label[1:]} no es un importe válido.') from None
    if not value.is_finite():
        raise TariffError(f'{label[0].upper()}{label[1:]} no es un importe válido.')
    if value < 0:
        raise TariffError(f'{label[0].upper()}{label[1:]} no puede ser negativo.')
    # Before quantizing: a value with more digits than the context holds makes `quantize` raise (a 500 otherwise).
    if value > MAX_AMOUNT:
        raise TariffError(f'{label[0].upper()}{label[1:]} es demasiado grande.')
    try:
        cents = value.quantize(Decimal('0.01'))
    except InvalidOperation:
        raise TariffError(f'{label[0].upper()}{label[1:]} no es un importe válido.') from None
    if value != cents:
        raise TariffError(f'{label[0].upper()}{label[1:]} lleva a lo sumo dos decimales.')
    return cents


def parse_date(raw, label: str, *, required: bool) -> date | None:
    """A date from a JSON body: `YYYY-MM-DD`."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        if required:
            raise TariffError(f'Indica {label}.', code='required')
        return None
    if isinstance(raw, date):
        return raw
    text = str(raw).strip()
    try:
        if not _ISO_DATE.fullmatch(text):
            raise ValueError(text)
        return date.fromisoformat(text)
    except ValueError:
        raise TariffError(f'{label[0].upper()}{label[1:]} no es una fecha válida (AAAA-MM-DD).') from None


def _text(value, limit: int, label: str) -> str:
    text = str(value or '').strip()
    if len(text) > limit:
        raise TariffError(f'{label} admite hasta {limit} caracteres.')
    return text


def clean_identity(data) -> dict:
    """The five words of a device, cleaned; brand and model are required."""
    out = {}
    for field in IDENTITY_FIELDS:
        value = _word(data.get(field))
        if len(value) > IDENTITY_LIMITS[field]:
            raise TariffError(f'{IDENTITY_LABELS[field]} admite hasta {IDENTITY_LIMITS[field]} caracteres.',
                              field=field)
        out[field] = value
    if not out['brand']:
        raise TariffError('Indica la marca del equipo.', code='required', field='brand')
    if not out['model_name']:
        raise TariffError('Indica el modelo del equipo.', code='required', field='model_name')
    return out


def clean_terms(data, *, today: date, default_from: date | None = None) -> dict:
    """
    The terms of a version from a body: the five amounts, the window and the reason. A start before the company's
    today is refused (a tariff is never retroactive); no start means today.
    """
    terms = {}
    for field in MONEY_FIELDS:
        try:
            terms[field] = parse_money(data.get(field), MONEY_LABELS[field], required=field in REQUIRED_MONEY)
        except TariffError as exc:
            exc.field = field
            raise
    if terms['base_reception_value'] > terms['max_reception_value']:
        raise TariffError('El valor base de recepción no puede superar el valor máximo autorizado.',
                          code='base_above_max', field='base_reception_value')
    for field, label in (('valid_from', 'el inicio de la vigencia'), ('valid_until', 'el fin de la vigencia')):
        try:
            terms[field] = parse_date(data.get(field), label, required=False)
        except TariffError as exc:
            exc.field = field
            raise
    terms['valid_from'] = terms['valid_from'] or default_from or today
    if terms['valid_until'] is not None and terms['valid_until'] < terms['valid_from']:
        raise TariffError('La vigencia termina antes de empezar.', code='window', field='valid_until')
    terms['reason'] = _text(data.get('reason'), MAX_REASON, 'El motivo')
    return terms


def check_not_retroactive(terms: dict, *, today: date) -> None:
    if terms['valid_from'] < today:
        raise TariffError('La vigencia no puede empezar antes de hoy: el tarifario no es retroactivo.',
                          code='retroactive', field='valid_from')
    if terms['valid_until'] is not None and terms['valid_until'] < today:
        raise TariffError('La vigencia ya terminó.', code='window', field='valid_until')


def same_terms(version, terms: dict) -> bool:
    """Whether `terms` say exactly what `version` says (amounts and window; the reason is not a term)."""
    if version is None:
        return False
    return all(getattr(version, field) == terms[field] for field in (*MONEY_FIELDS, 'valid_from', 'valid_until'))


def policy_of(company) -> TradeInPolicy:
    """The company's policy, or the defaults (unsaved) when it never changed it."""
    found = TradeInPolicy.objects.filter(company=company).first()
    return found or TradeInPolicy(company=company)


def _rises(before: Decimal, after: Decimal, tolerance_percent: Decimal) -> bool:
    limit = before * (Decimal('1') + tolerance_percent / Decimal('100'))
    return after > limit


def approval_reasons(entry, terms: dict, policy: TradeInPolicy) -> list[str]:
    """
    Why a change of `entry` (None: a new device) needs an approver; empty = not relevant (the policy approves it).

    Decisions of 2026-11-27 (review of #457):
      * a RISE is measured against the last version a PERSON approved (`approver` or `self`), never against one the
        policy approved: chained small rises cannot walk the price past the tolerance without an approver;
      * a longer validity than the latest approved one is relevant;
      * starting EARLIER than the latest approved one, or putting a price on a day that has none (a gap, or before a
        future price starts), is relevant — the same amount moved forward is still a new price on those days.
    """
    if entry is None or latest_approved(entry) is None:
        return [REASON_NEW_ENTRY]
    latest = latest_approved(entry)
    reference = rise_reference(entry) or latest
    reasons = []
    tolerance = policy.tariff_increase_tolerance_percent or Decimal('0')
    if _rises(reference.base_reception_value, terms['base_reception_value'], tolerance):
        reasons.append(REASON_BASE_INCREASE)
    if _rises(reference.max_reception_value, terms['max_reception_value'], tolerance):
        reasons.append(REASON_MAX_INCREASE)
    before_end, after_end = latest.valid_until, terms['valid_until']
    if before_end is not None and (after_end is None or after_end > before_end):
        reasons.append(REASON_VALIDITY_EXTENDED)
    if terms['valid_from'] < latest.valid_from or effective_version(entry, on=terms['valid_from']) is None:
        reasons.append(REASON_STARTS_EARLIER)
    if policy.tariff_approval_mode == TradeInPolicy.APPROVAL_ALL and not reasons:
        reasons.append(REASON_POLICY_ALL)
    return reasons


def margin_warnings(version_or_terms) -> list[str]:
    """
    Consistency notes, never refusals: the resale estimate minus the refurbishment and the maximum paid leaves less
    than the margin aimed at. Computed on read; nothing is stored.
    """
    get = (version_or_terms.get if isinstance(version_or_terms, dict)
           else lambda field: getattr(version_or_terms, field))
    resale = get('resale_value')
    if resale is None:
        return []
    left = resale - (get('refurbishment_cost') or Decimal('0')) - get('max_reception_value')
    target = get('target_margin') or Decimal('0')
    if left < target:
        return ['margin_below_target']
    return []


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def latest_approved(entry):
    return entry.versions.filter(status=TradeInTariffVersion.STATUS_APPROVED).order_by('-number').first()


def rise_reference(entry):
    """The last version a PERSON approved: what a rise is measured against."""
    return (entry.versions.filter(status=TradeInTariffVersion.STATUS_APPROVED,
                                  approval_kind__in=(TradeInTariffVersion.APPROVAL_APPROVER,
                                                     TradeInTariffVersion.APPROVAL_SELF))
            .order_by('-number').first())


def pending_version(entry):
    return entry.versions.filter(status=TradeInTariffVersion.STATUS_PENDING).first()


def effective_version(entry, *, on: date | None = None):
    """
    The version in force on `on` (company's local date; today by default), or None: the newest approved version that
    has started by that day, if it has not ended. An ended newer version never hands the day back to an older one.
    """
    if not entry.is_active:
        return None
    day = on or company_today(entry.company)
    latest_started = (entry.versions.filter(status=TradeInTariffVersion.STATUS_APPROVED, valid_from__lte=day)
                      .order_by('-number').first())
    if latest_started is None or (latest_started.valid_until is not None and latest_started.valid_until < day):
        return None
    return latest_started


def find_entry(company, *, brand, model_name, capacity='', sim='', region=''):
    """The company's entry for those words (folded), active or not, or None."""
    key = device_key(brand, model_name, capacity, sim, region)
    return TradeInTariffEntry.objects.filter(company=company, device_key=key).first()


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

def _require(actor, company, capability: str, message: str) -> None:
    if not has_capability(actor, company, capability):
        raise TariffPermissionError(message)


def _audit(*, actor, company, action: str, target, request=None, **metadata):
    target_type = 'trade_in_tariff_version' if isinstance(target, TradeInTariffVersion) else 'trade_in_tariff_entry'
    entry = target.entry if isinstance(target, TradeInTariffVersion) else target
    AdminAuditLog.log(
        actor=actor if getattr(actor, 'is_authenticated', False) else None,
        action=action, target_type=target_type, target_id=target.pk,
        metadata={'company_id': company.pk, 'entry_id': entry.pk, 'device_key': entry.device_key, **metadata},
        request=request, company=company,
    )


def _money_metadata(version) -> dict:
    return {field: (None if getattr(version, field) is None else str(getattr(version, field)))
            for field in MONEY_FIELDS}


def _linked_model(company, raw):
    if raw in (None, ''):
        return None
    try:
        pk = int(raw)
    except (TypeError, ValueError):
        raise TariffError('Modelo de catálogo no válido.', field='product_model') from None
    model = ProductModel.objects.filter(company=company, pk=pk).first()
    if model is None:
        # Another company's model answers like one that does not exist.
        raise TariffError('El modelo de catálogo no existe.', code='not_found', field='product_model')
    return model


def _write_version(*, actor, company, entry, terms, previous, source, batch, policy) -> TradeInTariffVersion:
    reasons = approval_reasons(None if previous is None else entry, terms, policy)
    if previous is not None:
        previous = rise_reference(entry) or previous
    number = (entry.versions.order_by('-number').values_list('number', flat=True).first() or 0) + 1
    now = timezone.now()
    version = TradeInTariffVersion(
        company=company, entry=entry, number=number, currency=company_currency(company),
        source=source, import_batch=batch, previous=previous, approval_reasons=reasons,
        proposed_by=actor if getattr(actor, 'is_authenticated', False) else None, proposed_at=now,
        **{field: terms[field] for field in (*MONEY_FIELDS, 'valid_from', 'valid_until', 'reason')},
    )
    if not reasons:
        version.status = TradeInTariffVersion.STATUS_APPROVED
        version.approval_kind = TradeInTariffVersion.APPROVAL_POLICY
        version.decided_by = version.proposed_by
        version.decided_at = now
    try:
        with transaction.atomic():
            version.save()
    except IntegrityError:
        raise TariffConflict('Este equipo ya tiene un cambio pendiente de aprobación.', code='pending_exists') from None
    return version


def create_entry(*, actor, company, data, request=None, source=TradeInTariffVersion.SOURCE_MANUAL, batch=None,
                 today: date | None = None):
    """A new device in the tariff with its first price, which always waits for an approver. (entry, version)."""
    _require(actor, company, CAP_MANAGE, 'No tienes permiso para editar el tarifario de parte de pago.')
    today = today or company_today(company)
    identity = clean_identity(data)
    terms = clean_terms(data, today=today)
    check_not_retroactive(terms, today=today)
    product_model = _linked_model(company, data.get('product_model'))
    key = device_key(*(identity[field] for field in IDENTITY_FIELDS))
    with transaction.atomic():
        existing = TradeInTariffEntry.objects.filter(company=company, device_key=key).first()
        if existing is not None:
            raise TariffConflict('Ese equipo ya está en el tarifario: propone un precio nuevo en su ficha.',
                                 code='duplicate', extra={'entry': existing.pk})
        try:
            with transaction.atomic():
                entry = TradeInTariffEntry.objects.create(
                    company=company, product_model=product_model, device_key=key,
                    created_by=actor if getattr(actor, 'is_authenticated', False) else None, **identity)
        except IntegrityError:
            raise TariffConflict('Ese equipo ya está en el tarifario.', code='duplicate') from None
        version = _write_version(actor=actor, company=company, entry=entry, terms=terms, previous=None,
                                 source=source, batch=batch, policy=policy_of(company))
        _audit(actor=actor, company=company, action='trade_in_tariff_entry_created', target=entry, request=request,
               version=version.number, status=version.status, source=source,
               import_id=batch.pk if batch else None)
        _audit(actor=actor, company=company, action='trade_in_tariff_version_proposed', target=version,
               request=request, number=version.number, status=version.status, reasons=version.approval_reasons,
               source=source, import_id=batch.pk if batch else None, **_money_metadata(version))
    return entry, version


def _locked_entry(entry) -> TradeInTariffEntry:
    return TradeInTariffEntry.objects.select_for_update().get(pk=entry.pk)


def propose_version(*, actor, company, entry, data, request=None, source=TradeInTariffVersion.SOURCE_MANUAL,
                    batch=None, today: date | None = None) -> TradeInTariffVersion:
    """A new price for a device: approved by the policy when not relevant, otherwise pending."""
    _require(actor, company, CAP_MANAGE, 'No tienes permiso para editar el tarifario de parte de pago.')
    if entry.company_id != company.pk:
        raise TariffError('No encontrado.', code='not_found')
    today = today or company_today(company)
    terms = clean_terms(data, today=today)
    with transaction.atomic():
        entry = _locked_entry(entry)
        if not entry.is_active:
            raise TariffStateError('Esta entrada está desactivada: reactívala antes de proponer un precio.',
                                   code='inactive')
        if pending_version(entry) is not None:
            raise TariffConflict('Este equipo ya tiene un cambio pendiente de aprobación: decídelo o retíralo '
                                 'antes de proponer otro.', code='pending_exists')
        previous = latest_approved(entry)
        if same_terms(previous, terms):
            raise TariffError('Los importes y la vigencia son los mismos que los aprobados: no hay nada que cambiar.',
                              code='unchanged')
        check_not_retroactive(terms, today=today)
        version = _write_version(actor=actor, company=company, entry=entry, terms=terms, previous=previous,
                                 source=source, batch=batch, policy=policy_of(company))
        _audit(actor=actor, company=company, action='trade_in_tariff_version_proposed', target=version,
               request=request, number=version.number, status=version.status, reasons=version.approval_reasons,
               rise_reference=version.previous.number if version.previous_id else None,
               latest_approved=previous.number if previous else None, source=source,
               import_id=batch.pk if batch else None, **_money_metadata(version))
    return version


def _locked_version(company, version) -> TradeInTariffVersion:
    if version.company_id != company.pk:
        raise TariffError('No encontrado.', code='not_found')
    _locked_entry(version.entry)
    return TradeInTariffVersion.objects.select_for_update(of=('self',)).select_related('entry').get(pk=version.pk)


def approve_version(*, actor, company, version, note='', request=None, today: date | None = None):
    """
    Approve a pending change. Somebody other than its proposer, or its proposer holding `approve_own` with a
    reason (flagged in the audit).
    """
    _require(actor, company, CAP_APPROVE, 'No tienes permiso para aprobar cambios del tarifario.')
    note = _text(note, MAX_REASON, 'La nota')
    today = today or company_today(company)
    with transaction.atomic():
        version = _locked_version(company, version)
        if version.status != TradeInTariffVersion.STATUS_PENDING:
            raise TariffStateError('Este cambio ya fue decidido.', code='not_pending')
        if not version.entry.is_active:
            raise TariffStateError('La entrada está desactivada: reactívala antes de aprobar.', code='inactive')
        if version.valid_until is not None and version.valid_until < today:
            raise TariffStateError('La vigencia de este cambio ya terminó: recházalo y propone otro.',
                                   code='expired')
        own = version.proposed_by_id is not None and version.proposed_by_id == getattr(actor, 'pk', None)
        if own:
            if not has_capability(actor, company, CAP_APPROVE_OWN):
                raise TariffPermissionError('Otra persona con permiso de aprobación tiene que aprobar este cambio: '
                                            'lo propusiste tú.', code='own_change')
            if not note:
                raise TariffError('Indica el motivo para aprobar un cambio propio.', code='reason_required',
                                  field='note')
        fields = ['status', 'approval_kind', 'decided_by', 'decided_at', 'decision_note']
        moved_from = None
        if version.valid_from < today:
            # It waited for approval past its start: it starts today, never retroactively (documented, audited).
            moved_from = version.valid_from.isoformat()
            version.valid_from = today
            fields.append('valid_from')
        version.status = TradeInTariffVersion.STATUS_APPROVED
        version.approval_kind = TradeInTariffVersion.APPROVAL_SELF if own else TradeInTariffVersion.APPROVAL_APPROVER
        version.decided_by = actor
        version.decided_at = timezone.now()
        version.decision_note = note
        version.save(update_fields=fields)
        _audit(actor=actor, company=company, action='trade_in_tariff_version_approved', target=version,
               request=request, number=version.number, self_approval=own, reasons=version.approval_reasons,
               note=note, start_moved_from=moved_from, valid_from=version.valid_from.isoformat(),
               **_money_metadata(version))
    return version


def reject_version(*, actor, company, version, note, request=None):
    _require(actor, company, CAP_APPROVE, 'No tienes permiso para rechazar cambios del tarifario.')
    note = _text(note, MAX_REASON, 'La nota')
    if not note:
        raise TariffError('Indica por qué se rechaza el cambio.', code='reason_required', field='note')
    with transaction.atomic():
        version = _locked_version(company, version)
        if version.status != TradeInTariffVersion.STATUS_PENDING:
            raise TariffStateError('Este cambio ya fue decidido.', code='not_pending')
        version.status = TradeInTariffVersion.STATUS_REJECTED
        version.decided_by = actor
        version.decided_at = timezone.now()
        version.decision_note = note
        version.save(update_fields=['status', 'decided_by', 'decided_at', 'decision_note'])
        _audit(actor=actor, company=company, action='trade_in_tariff_version_rejected', target=version,
               request=request, number=version.number, note=note)
    return version


def withdraw_version(*, actor, company, version, request=None, note=''):
    """Its proposer (or an approver) takes a pending change back."""
    _require(actor, company, CAP_MANAGE, 'No tienes permiso para editar el tarifario de parte de pago.')
    note = _text(note, MAX_REASON, 'La nota')
    with transaction.atomic():
        version = _locked_version(company, version)
        if version.status != TradeInTariffVersion.STATUS_PENDING:
            raise TariffStateError('Este cambio ya fue decidido.', code='not_pending')
        if version.proposed_by_id != getattr(actor, 'pk', None) and not has_capability(actor, company, CAP_APPROVE):
            raise TariffPermissionError('Sólo quien propuso el cambio, o quien puede aprobarlo, lo retira.')
        _withdraw(actor=actor, company=company, version=version, note=note, request=request)
    return version


def _withdraw(*, actor, company, version, note, request):
    version.status = TradeInTariffVersion.STATUS_WITHDRAWN
    version.decided_by = actor
    version.decided_at = timezone.now()
    version.decision_note = note
    version.save(update_fields=['status', 'decided_by', 'decided_at', 'decision_note'])
    _audit(actor=actor, company=company, action='trade_in_tariff_version_withdrawn', target=version,
           request=request, number=version.number, note=note)


def deactivate_entry(*, actor, company, entry, reason, request=None):
    """
    The device leaves the tariff: no version of it is in force any more. Lowering exposure needs no approver;
    a pending change is withdrawn with it. Reversible only by an approver (`reactivate_entry`).
    """
    _require(actor, company, CAP_MANAGE, 'No tienes permiso para editar el tarifario de parte de pago.')
    if entry.company_id != company.pk:
        raise TariffError('No encontrado.', code='not_found')
    reason = _text(reason, MAX_REASON, 'El motivo')
    if not reason:
        raise TariffError('Indica por qué se desactiva.', code='reason_required', field='reason')
    with transaction.atomic():
        entry = _locked_entry(entry)
        if not entry.is_active:
            raise TariffStateError('Esta entrada ya está desactivada.', code='inactive')
        pending = pending_version(entry)
        if pending is not None:
            _withdraw(actor=actor, company=company, version=pending, note='Entrada desactivada.', request=request)
        entry.is_active = False
        entry.deactivated_at = timezone.now()
        entry.deactivated_by = actor
        entry.deactivation_reason = reason
        entry.save(update_fields=['is_active', 'deactivated_at', 'deactivated_by', 'deactivation_reason',
                                  'updated_at'])
        _audit(actor=actor, company=company, action='trade_in_tariff_entry_deactivated', target=entry,
               request=request, reason=reason, withdrawn=pending.number if pending else None)
    return entry


def reactivate_entry(*, actor, company, entry, reason, request=None):
    """Back in the tariff, with the versions it had: putting a price back in force is an approver's decision."""
    _require(actor, company, CAP_APPROVE, 'No tienes permiso para reactivar entradas del tarifario.')
    if entry.company_id != company.pk:
        raise TariffError('No encontrado.', code='not_found')
    reason = _text(reason, MAX_REASON, 'El motivo')
    if not reason:
        raise TariffError('Indica por qué se reactiva.', code='reason_required', field='reason')
    with transaction.atomic():
        entry = _locked_entry(entry)
        if entry.is_active:
            raise TariffStateError('Esta entrada ya está activa.', code='active')
        entry.is_active = True
        entry.deactivated_at = None
        entry.deactivated_by = None
        entry.deactivation_reason = ''
        entry.save(update_fields=['is_active', 'deactivated_at', 'deactivated_by', 'deactivation_reason',
                                  'updated_at'])
        _audit(actor=actor, company=company, action='trade_in_tariff_entry_reactivated', target=entry,
               request=request, reason=reason)
    return entry


def link_model(*, actor, company, entry, product_model, request=None):
    """Link (or unlink, with None) the company's catalogue model. Not a price: no approval."""
    _require(actor, company, CAP_MANAGE, 'No tienes permiso para editar el tarifario de parte de pago.')
    if entry.company_id != company.pk:
        raise TariffError('No encontrado.', code='not_found')
    model = _linked_model(company, product_model)
    with transaction.atomic():
        entry = _locked_entry(entry)
        before = entry.product_model_id
        entry.product_model = model
        entry.save(update_fields=['product_model', 'updated_at'])
        _audit(actor=actor, company=company, action='trade_in_tariff_entry_linked', target=entry, request=request,
               before=before, after=model.pk if model else None)
    return entry


def update_policy(*, actor, company, data, request=None) -> TradeInPolicy:
    """The company's approval policy. An approver's decision: it decides what reaches an approver."""
    _require(actor, company, CAP_APPROVE, 'No tienes permiso para cambiar la política del tarifario.')
    with transaction.atomic():
        policy, _ = TradeInPolicy.objects.select_for_update().get_or_create(company=company)
        before = {'tariff_approval_mode': policy.tariff_approval_mode,
                  'tariff_increase_tolerance_percent': str(policy.tariff_increase_tolerance_percent)}
        if 'tariff_approval_mode' in data:
            mode = str(data.get('tariff_approval_mode') or '').strip()
            if mode not in dict(TradeInPolicy.APPROVAL_CHOICES):
                raise TariffError('Modo de aprobación no válido.', field='tariff_approval_mode')
            policy.tariff_approval_mode = mode
        if 'tariff_increase_tolerance_percent' in data:
            value = parse_money(data.get('tariff_increase_tolerance_percent'), 'la tolerancia', required=True)
            if value > Decimal('100'):
                raise TariffError('La tolerancia va de 0 a 100 %.', field='tariff_increase_tolerance_percent')
            policy.tariff_increase_tolerance_percent = value
        policy.updated_by = actor
        policy.save()
        after = {'tariff_approval_mode': policy.tariff_approval_mode,
                 'tariff_increase_tolerance_percent': str(policy.tariff_increase_tolerance_percent)}
        AdminAuditLog.log(
            actor=actor, action='trade_in_policy_updated', target_type='trade_in_policy', target_id=policy.pk,
            metadata={'company_id': company.pk, 'before': before, 'after': after}, request=request,
            company=company)
    return policy
