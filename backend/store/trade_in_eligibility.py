"""
TRADE-IN VALUATION PRO · Phase 3 — eligibility and the assisted identity check (docs/adr-trade-in-valuation.md §3.3,
§5.2).

A DEVICE CHECK holds the security half of an assessment:

  * the identifiers, kept ONLY as a keyed digest (HMAC-SHA256 with a key derived for this use and this company: never
    in a response or the audit) and their last four characters. An IMEI that fails its check digit is kept as such
    (`imei_valid=False`): the device is NOT ELIGIBLE (`imei_invalid`), never silently «corrected»;
  * the server's own findings at creation: the IMEI is a live unit of this company's stock, or another OPEN check's
    (`imei_duplicate`); a unit the company sold or wrote off is only reported (D9 pending, nothing decided);
  * the answers to the blockers (`clear` / `blocked` / `not_verified`);
  * the identity records: what a person saw in an OFFICIAL source consulted BY HAND (the company's
    `TradeInVerificationSource`; no API is invented and nothing is scraped), or what they declared after checking the
    device itself. Every identifier needs a valid record (IMEI 1, IMEI 2 when there is one, or the serial when there
    is no IMEI). No evidence file is stored until D6 is decided.

ELIGIBILITY here is the security verdict (§5.2): NOT ELIGIBLE if anything blocks; PENDING VERIFICATION if a required
blocker is unanswered or unverified, or an identifier lacks a valid record; ELIGIBLE otherwise. Phase 4 combines it
with the valuation rules: a block is NEVER turned into a deduction, and the rules' result is used only when this
verdict is not NOT ELIGIBLE.

THE MANDATORY FLOOR: `MANDATORY` blockers are required for every company and cannot be switched off. The others are
required by default; a company may mark them as not applicable (`TradeInPolicy.optional_blockers`) and add its own
(`extra_blockers`).

Nothing here receives, buys, pays or credits a device.
"""
from __future__ import annotations

import hashlib
import hmac
import re
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from . import device_identity
from . import trade_in_tariff as tariff
from .models import (
    AdminAuditLog,
    StockUnit,
    TradeInBlockerAnswer,
    TradeInDeviceCheck,
    TradeInIdentityRecord,
    TradeInVerificationSource,
)
from .tenancy import has_capability, visible_branches

CAP_VIEW = 'sales.tradein.view'
CAP_VALUE = 'sales.tradein.value'

TariffError = tariff.TariffError
TariffPermissionError = tariff.TariffPermissionError
TariffStateError = tariff.TariffStateError
TariffConflict = tariff.TariffConflict

#: The platform's blockers (closed, generic security vocabulary: no maker, no shop).
BLOCKERS = {
    'theft_or_loss_report': 'Reporte de robo o pérdida',
    'imei_invalid': 'IMEI o serie inválidos',
    'imei_duplicate': 'IMEI duplicado',
    'imei_cloned': 'IMEI clonado',
    'imei_tampered': 'IMEI alterado',
    'activation_lock': 'Bloqueo de activación',
    'previous_account': 'Cuenta anterior vinculada',
    'mdm_enrolled': 'Gestión remota (MDM)',
    'illicit_signs': 'Indicios de origen ilícito',
    'ownership_unproven': 'Propiedad no acreditada',
    'identifiers_mismatch': 'Los identificadores no coinciden con el equipo',
    'dangerous_condition': 'Condición física peligrosa',
}
#: Required for every company: no policy switches these off.
MANDATORY = frozenset({'theft_or_loss_report', 'imei_invalid', 'activation_lock', 'ownership_unproven',
                       'identifiers_mismatch', 'dangerous_condition'})
#: Decided by the server, never answered by hand.
COMPUTED = frozenset({'imei_invalid', 'imei_duplicate'})

ELIGIBLE = 'eligible'
PENDING_VERIFICATION = 'pending_verification'
NOT_ELIGIBLE = 'not_eligible'
LABELS = {ELIGIBLE: 'APTO', PENDING_VERIFICATION: 'PENDIENTE DE VERIFICACIÓN', NOT_ELIGIBLE: 'NO ELEGIBLE'}

#: A clear record; a reported one blocks; an invalid one blocks as `imei_invalid`; the rest leave it pending.
CLEAR_RESULTS = frozenset({TradeInIdentityRecord.RESULT_NO_REPORT, TradeInIdentityRecord.RESULT_DECLARED_CLEAR})

_EXTRA_CODE = re.compile(r'^[a-z0-9][a-z0-9_]{1,40}$')
MAX_EXTRA = 20


# ---------------------------------------------------------------------------
# Identifiers
# ---------------------------------------------------------------------------

def _key(company_id: int) -> bytes:
    # Derived, never the secret itself: one use, one key (as `tracking_services._key`). Rotating SECRET_KEY only loses
    # the duplicate detection against older checks; nothing else depends on it.
    return hashlib.sha256(f'trade-in-identity|{company_id}|'.encode() + settings.SECRET_KEY.encode('utf-8')).digest()


def digest(company_id: int, value: str) -> str:
    """The keyed digest of an identifier. NEVER in a response or the audit."""
    return hmac.new(_key(company_id), value.encode('utf-8'), hashlib.sha256).hexdigest()


def read_imei(raw, label: str) -> tuple[str, bool]:
    """(digits, valid). Fifteen digits are kept even when the check digit fails: that IS the finding."""
    text = re.sub(r'[\s.\-/]', '', str(raw or ''))
    if not text:
        return '', True
    if not text.isdigit() or len(text) != 15:
        raise TariffError(f'{label} son 15 dígitos.', field='imei' if label == 'El IMEI' else 'imei2')
    try:
        device_identity.clean_imei(text, label=label)
    except device_identity.DeviceIdentityError:
        return text, False
    return text, True


def masked(last4: str) -> str:
    return f'•••• {last4}' if last4 else ''


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------

def required_blockers(policy) -> dict[str, str]:
    """code → label of every blocker this company must answer (mandatory floor + defaults − optional + its own)."""
    optional = set(policy.optional_blockers or []) - MANDATORY
    out = {code: label for code, label in BLOCKERS.items() if code not in optional}
    for extra in policy.extra_blockers or []:
        out[extra['code']] = extra['label']
    return out


def clean_policy(data) -> dict:
    """The phase-3 part of a policy update: validity hours, optional blockers, the company's own blockers."""
    out = {}
    if 'identity_check_validity_hours' in data:
        raw = data.get('identity_check_validity_hours')
        try:
            hours = int(str(raw).strip())
        except (TypeError, ValueError):
            raise TariffError('La vigencia de una verificación son horas (1 a 720).',
                              field='identity_check_validity_hours') from None
        if not 1 <= hours <= 720 or isinstance(raw, bool):
            raise TariffError('La vigencia de una verificación son horas (1 a 720).',
                              field='identity_check_validity_hours')
        out['identity_check_validity_hours'] = hours
    if 'optional_blockers' in data:
        raw = data.get('optional_blockers') or []
        if not isinstance(raw, list) or any(code not in BLOCKERS for code in raw):
            raise TariffError('Bloqueo no reconocido.', field='optional_blockers')
        floor = sorted(set(raw) & MANDATORY)
        if floor:
            raise TariffError('Estos bloqueos son obligatorios y no se pueden desactivar: '
                              + ', '.join(BLOCKERS[c] for c in floor) + '.', code='mandatory_blocker',
                              field='optional_blockers', extra={'codes': floor})
        out['optional_blockers'] = sorted(set(raw))
    if 'extra_blockers' in data:
        raw = data.get('extra_blockers') or []
        if not isinstance(raw, list) or len(raw) > MAX_EXTRA:
            raise TariffError(f'Hasta {MAX_EXTRA} bloqueos propios.', field='extra_blockers')
        extras, seen = [], set()
        for item in raw:
            code = str((item or {}).get('code') or '').strip() if isinstance(item, dict) else ''
            label = ' '.join(str((item or {}).get('label') or '').split()) if isinstance(item, dict) else ''
            if not _EXTRA_CODE.match(code) or code in BLOCKERS or code in seen or not label or len(label) > 120:
                raise TariffError('Cada bloqueo propio tiene un código único (minúsculas, números y _) y un texto.',
                                  field='extra_blockers')
            seen.add(code)
            extras.append({'code': code, 'label': label})
        out['extra_blockers'] = extras
    return out


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

def _require(actor, company, capability, message):
    if not has_capability(actor, company, capability):
        raise TariffPermissionError(message)


def _audit(*, actor, company, action, check, request=None, **metadata):
    # Ids and states only: never an identifier, its last four characters or its digest.
    AdminAuditLog.log(actor=actor if getattr(actor, 'is_authenticated', False) else None, action=action,
                      target_type='trade_in_device_check', target_id=check.pk,
                      metadata={'company_id': company.pk, 'check_id': check.pk, 'branch_id': check.branch_id,
                                **metadata},
                      request=request, company=company)


def _branch(actor, company, raw):
    try:
        pk = int(raw)
    except (TypeError, ValueError):
        raise TariffError('Elige la sucursal.', code='required', field='branch') from None
    branch = visible_branches(actor, company).filter(pk=pk).first()
    if branch is None:
        raise TariffError('No encontrado.', code='not_found')
    return branch


def _stock_match(company, imeis: list[str]):
    """(live, known): a live unit of this company has one of these IMEIs; a sold or written-off one has."""
    from django.db.models import Q

    if not imeis:
        return False, False
    units = StockUnit.objects.filter(company=company).filter(Q(imei__in=imeis) | Q(imei2__in=imeis))
    statuses = set(units.values_list('status', flat=True))
    live = bool(statuses & {StockUnit.Status.AVAILABLE, StockUnit.Status.RESERVED})
    known = bool(statuses & {StockUnit.Status.SOLD, StockUnit.Status.WRITTEN_OFF})
    return live, known


def create_check(*, actor, company, data, request=None) -> TradeInDeviceCheck:
    """Register a device offered at a branch: its identifiers as digests, and the server's own findings."""
    _require(actor, company, CAP_VALUE, 'No tienes permiso para comprobar equipos usados.')
    branch = _branch(actor, company, data.get('branch'))
    has_imei = data.get('has_imei', True)
    has_imei = has_imei if isinstance(has_imei, bool) else str(has_imei).strip().lower() in ('true', '1', 'si', 'sí')
    description = ' '.join(str(data.get('description') or '').split())
    if len(description) > 200:
        raise TariffError('La descripción admite hasta 200 caracteres.', field='description')
    if device_identity.looks_like_eid(description) or re.search(r'\d{8}', re.sub(r'[\s.\-/,]', '', description)):
        raise TariffError('La descripción no lleva identificadores: escríbelos en sus campos.', field='description')
    imei, imei_valid = read_imei(data.get('imei'), 'El IMEI')
    imei2, imei2_valid = read_imei(data.get('imei2'), 'El IMEI 2')
    try:
        serial = device_identity.clean_serial(data.get('serial'))
    except device_identity.DeviceIdentityError as exc:
        raise TariffError(str(exc), field='serial') from None
    if has_imei and not imei:
        raise TariffError('Escribe el IMEI del equipo (o marca que no tiene IMEI).', code='required', field='imei')
    if not has_imei and (imei or imei2):
        raise TariffError('Un equipo sin IMEI no lleva IMEI: escribe su número de serie.', field='imei')
    if not has_imei and not serial:
        raise TariffError('Un equipo sin IMEI se identifica por su número de serie.', code='required', field='serial')
    if imei and imei == imei2:
        raise TariffError('El IMEI 2 no puede ser igual al IMEI 1.', field='imei2')
    with transaction.atomic():
        digests = [digest(company.pk, value) for value in (imei, imei2) if value]
        duplicate = ''
        if digests:
            from django.db.models import Q

            if TradeInDeviceCheck.objects.filter(company=company, status=TradeInDeviceCheck.STATUS_OPEN).filter(
                    Q(imei_digest__in=digests) | Q(imei2_digest__in=digests)).exists():
                duplicate = 'open_check'
        live, known = _stock_match(company, [value for value in (imei, imei2) if value])
        if live:
            duplicate = 'stock'
        check = TradeInDeviceCheck.objects.create(
            company=company, branch=branch, description=description, has_imei=has_imei,
            imei_last4=imei[-4:], imei_digest=digest(company.pk, imei) if imei else '', imei_valid=imei_valid,
            imei2_last4=imei2[-4:], imei2_digest=digest(company.pk, imei2) if imei2 else '', imei2_valid=imei2_valid,
            serial_last4=serial[-4:], serial_digest=digest(company.pk, serial) if serial else '',
            duplicate_of=duplicate, known_to_company=known,
            created_by=actor if getattr(actor, 'is_authenticated', False) else None)
        _audit(actor=actor, company=company, action='trade_in_device_check_created', check=check, request=request,
               has_imei=has_imei, has_imei2=bool(imei2), has_serial=bool(serial),
               imei_valid=imei_valid and imei2_valid, duplicate=duplicate, known_to_company=known)
    return check


def _open(company, check) -> TradeInDeviceCheck:
    if check.company_id != company.pk:
        raise TariffError('No encontrado.', code='not_found')
    locked = TradeInDeviceCheck.objects.select_for_update().get(pk=check.pk)
    if locked.status != TradeInDeviceCheck.STATUS_OPEN:
        raise TariffStateError('Esta comprobación está cerrada.', code='closed')
    return locked


def answer_blocker(*, actor, company, check, data, request=None) -> TradeInBlockerAnswer:
    _require(actor, company, CAP_VALUE, 'No tienes permiso para comprobar equipos usados.')
    code = str(data.get('code') or '').strip()
    policy = tariff.policy_of(company)
    required = required_blockers(policy)
    if code in COMPUTED:
        raise TariffError('Este bloqueo lo decide el sistema con el IMEI: no se responde a mano.', code='computed',
                          field='code')
    if code not in required:
        raise TariffError('Bloqueo no reconocido para esta empresa.', field='code')
    answer = str(data.get('answer') or '').strip()
    if answer not in dict(TradeInBlockerAnswer.ANSWER_CHOICES):
        raise TariffError('Respuesta no válida (clear, blocked o not_verified).', field='answer')
    note = ' '.join(str(data.get('note') or '').split())
    if len(note) > 300:
        raise TariffError('La nota admite hasta 300 caracteres.', field='note')
    with transaction.atomic():
        check = _open(company, check)
        row = TradeInBlockerAnswer.objects.filter(device_check=check, code=code).first()
        before = row.answer if row else None
        if row is None:
            row = TradeInBlockerAnswer(company=company, device_check=check, code=code)
        row.answer, row.note, row.answered_by, row.answered_at = answer, note, actor, timezone.now()
        row.save()
        _audit(actor=actor, company=company, action='trade_in_blocker_answered', check=check, request=request,
               code=code, answer=answer, before=before)
    return row


def record_identity(*, actor, company, check, data, request=None, now=None) -> TradeInIdentityRecord:
    """What a person saw in an official source (or declared about the device itself), for one identifier."""
    _require(actor, company, CAP_VALUE, 'No tienes permiso para comprobar equipos usados.')
    now = now or timezone.now()
    slot = str(data.get('slot') or '').strip()
    if slot not in dict(TradeInIdentityRecord.SLOT_CHOICES):
        raise TariffError('Identificador no válido (imei, imei2 o serial).', field='slot')
    result = str(data.get('result') or '').strip()
    if result not in dict(TradeInIdentityRecord.RESULT_CHOICES):
        raise TariffError('Resultado no reconocido.', field='result')
    source = None
    raw = data.get('source')
    if raw not in (None, ''):
        try:
            source = TradeInVerificationSource.objects.filter(company=company, pk=int(raw)).first()
        except (TypeError, ValueError):
            source = None
        if source is None:
            raise TariffError('La fuente no existe.', code='not_found', field='source')
        if not source.is_active:
            raise TariffError('Esa fuente está desactivada.', field='source')
        wanted = (TradeInVerificationSource.IDENTIFIER_SERIAL if slot == TradeInIdentityRecord.SLOT_SERIAL
                  else TradeInVerificationSource.IDENTIFIER_IMEI)
        if source.identifier != wanted:
            raise TariffError('Esa fuente no consulta ese identificador.', field='source')
    elif result != TradeInIdentityRecord.RESULT_DECLARED_CLEAR:
        raise TariffError('Indica la fuente oficial que consultaste.', code='required', field='source')
    checked_raw = data.get('checked_at')
    checked_at = parse_datetime(str(checked_raw)) if checked_raw not in (None, '') else now
    if checked_at is None:
        raise TariffError('La fecha y hora de la consulta no es válida.', field='checked_at')
    if timezone.is_naive(checked_at):
        checked_at = timezone.make_aware(checked_at, tariff.company_timezone(company))
    if checked_at > now + timedelta(minutes=5):
        raise TariffError('La consulta no puede ser futura.', field='checked_at')
    note = ' '.join(str(data.get('note') or '').split())
    if len(note) > 300:
        raise TariffError('La nota admite hasta 300 caracteres.', field='note')
    hours = tariff.policy_of(company).identity_check_validity_hours
    with transaction.atomic():
        check = _open(company, check)
        present = {TradeInIdentityRecord.SLOT_IMEI: bool(check.imei_digest),
                   TradeInIdentityRecord.SLOT_IMEI2: bool(check.imei2_digest),
                   TradeInIdentityRecord.SLOT_SERIAL: bool(check.serial_digest)}
        if not present[slot]:
            raise TariffError('Este equipo no tiene ese identificador.', field='slot')
        record = TradeInIdentityRecord.objects.create(
            company=company, device_check=check, slot=slot, source=source, result=result, checked_at=checked_at,
            valid_until=checked_at + timedelta(hours=hours), note=note,
            recorded_by=actor if getattr(actor, 'is_authenticated', False) else None)
        _audit(actor=actor, company=company, action='trade_in_identity_recorded', check=check, request=request,
               record=record.pk, slot=slot, source=source.pk if source else None, result=result,
               checked_at=checked_at.isoformat())
    return record


def close_check(*, actor, company, check, reason, request=None):
    _require(actor, company, CAP_VALUE, 'No tienes permiso para comprobar equipos usados.')
    reason = ' '.join(str(reason or '').split())[:300]
    if not reason:
        raise TariffError('Indica por qué se cierra.', code='reason_required', field='reason')
    with transaction.atomic():
        check = _open(company, check)
        check.status = TradeInDeviceCheck.STATUS_CLOSED
        check.closed_at, check.closed_by, check.close_reason = timezone.now(), actor, reason
        check.save(update_fields=['status', 'closed_at', 'closed_by', 'close_reason', 'updated_at'])
        _audit(actor=actor, company=company, action='trade_in_device_check_closed', check=check, request=request,
               reason=reason)
    return check


# ---------------------------------------------------------------------------
# The verdict
# ---------------------------------------------------------------------------

def required_slots(check) -> list[str]:
    if check.has_imei:
        return [TradeInIdentityRecord.SLOT_IMEI] + ([TradeInIdentityRecord.SLOT_IMEI2] if check.imei2_digest else [])
    return [TradeInIdentityRecord.SLOT_SERIAL]


def evaluate(check, *, now=None, policy=None) -> dict:
    """
    The security verdict of a check, computed on each read and never stored: per blocker (answered, computed or
    from the identity records) and per identifier, and the overall NOT ELIGIBLE / PENDING / ELIGIBLE.
    """
    now = now or timezone.now()
    policy = policy or tariff.policy_of(check.company)
    required = required_blockers(policy)
    answers = {row.code: row for row in check.blocker_answers.all()}
    states: dict[str, dict] = {}
    for code, label in required.items():
        row = answers.get(code)
        states[code] = {'code': code, 'label': label, 'mandatory': code in MANDATORY,
                        'state': row.answer if row else 'unanswered', 'source': 'answer' if row else None}
    # The server's own findings.
    if 'imei_invalid' in states:
        bad = not (check.imei_valid and check.imei2_valid)
        states['imei_invalid'].update(state='blocked' if bad else 'clear', source='computed')
    if 'imei_duplicate' in states:
        states['imei_duplicate'].update(state='blocked' if check.duplicate_of else 'clear', source='computed',
                                        detail=check.duplicate_of or None)
    # The identity records: the latest per identifier decides; an expired one is not a verification.
    slots = []
    records = list(check.identity_records.select_related('source').order_by('-checked_at', '-pk'))
    for slot in required_slots(check):
        latest = next((r for r in records if r.slot == slot), None)
        valid = latest is not None and latest.valid_until > now
        state = 'missing' if latest is None else ('expired' if not valid else latest.result)
        slots.append({'slot': slot, 'state': state, 'record': latest.pk if latest else None,
                      'valid_until': latest.valid_until if latest else None})
        if valid and latest.result == TradeInIdentityRecord.RESULT_REPORTED and 'theft_or_loss_report' in states:
            states['theft_or_loss_report'].update(state='blocked', source='identity_record')
        if valid and latest.result == TradeInIdentityRecord.RESULT_INVALID and 'imei_invalid' in states:
            states['imei_invalid'].update(state='blocked', source='identity_record')
    blocked = sorted(code for code, s in states.items() if s['state'] == 'blocked')
    unverified = sorted(code for code, s in states.items() if s['state'] in ('unanswered', 'not_verified'))
    unchecked = [s['slot'] for s in slots if s['state'] not in CLEAR_RESULTS]
    if blocked:
        verdict = NOT_ELIGIBLE
    elif unverified or unchecked:
        verdict = PENDING_VERIFICATION
    else:
        verdict = ELIGIBLE
    return {'eligibility': verdict, 'label': LABELS[verdict], 'blocked': blocked, 'unverified': unverified,
            'unchecked_identifiers': unchecked, 'blockers': list(states.values()), 'identifiers': slots,
            'known_to_company': check.known_to_company}


# ---------------------------------------------------------------------------
# Verification sources (the company's data)
# ---------------------------------------------------------------------------

def _source_fields(data, *, partial=False) -> dict:
    out = {}
    if not partial or 'name' in data:
        name = ' '.join(str(data.get('name') or '').split())
        if not name or len(name) > 120:
            raise TariffError('El nombre de la fuente tiene de 1 a 120 caracteres.', field='name')
        out['name'] = name
    if not partial or 'url' in data:
        url = str(data.get('url') or '').strip()
        if not url.startswith('https://') or len(url) > 300 or any(c.isspace() for c in url):
            raise TariffError('La dirección de la fuente es https://… (se abre en una pestaña nueva).', field='url')
        out['url'] = url
    if not partial or 'identifier' in data:
        identifier = str(data.get('identifier') or TradeInVerificationSource.IDENTIFIER_IMEI).strip()
        if identifier not in dict(TradeInVerificationSource.IDENTIFIER_CHOICES):
            raise TariffError('Identificador no válido (imei o serial).', field='identifier')
        out['identifier'] = identifier
    if not partial or 'checks' in data:
        checks = data.get('checks') or []
        if not isinstance(checks, list) or any(c not in TradeInVerificationSource.CHECK_CHOICES for c in checks):
            raise TariffError('Comprobación no reconocida.', field='checks')
        out['checks'] = sorted(set(checks))
    if not partial or 'instructions' in data:
        text = str(data.get('instructions') or '').strip()
        if len(text) > 1000:
            raise TariffError('Las instrucciones admiten hasta 1000 caracteres.', field='instructions')
        out['instructions'] = text
    if partial and 'is_active' in data:
        out['is_active'] = bool(data.get('is_active'))
    return out


def save_source(*, actor, company, data, source=None, request=None) -> TradeInVerificationSource:
    _require(actor, company, tariff.CAP_MANAGE, 'No tienes permiso para configurar fuentes de verificación.')
    from django.db import IntegrityError

    fields = _source_fields(data, partial=source is not None)
    try:
        with transaction.atomic():
            if source is None:
                source = TradeInVerificationSource.objects.create(company=company, **fields)
                action = 'trade_in_verification_source_created'
            else:
                for key, value in fields.items():
                    setattr(source, key, value)
                source.save()
                action = 'trade_in_verification_source_updated'
    except IntegrityError:
        raise TariffConflict('Ya existe una fuente con ese nombre.', code='duplicate', field='name') from None
    AdminAuditLog.log(actor=actor, action=action, target_type='trade_in_verification_source', target_id=source.pk,
                      metadata={'company_id': company.pk, 'fields': sorted(fields)}, request=request, company=company)
    return source
