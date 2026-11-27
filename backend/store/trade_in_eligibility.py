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
import unicodedata
from datetime import timedelta, timezone as dt_timezone

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
#: Required for every company: no policy switches these off (the two computed ones included: review of #463).
MANDATORY = frozenset({'theft_or_loss_report', 'imei_invalid', 'imei_duplicate', 'activation_lock',
                       'ownership_unproven', 'identifiers_mismatch', 'dangerous_condition'})
#: Decided by the server, never answered by hand.
COMPUTED = frozenset({'imei_invalid', 'imei_duplicate'})

ELIGIBLE = 'eligible'
PENDING_VERIFICATION = 'pending_verification'
NOT_ELIGIBLE = 'not_eligible'
LABELS = {ELIGIBLE: 'APTO', PENDING_VERIFICATION: 'PENDIENTE DE VERIFICACIÓN', NOT_ELIGIBLE: 'NO ELEGIBLE'}

#: A clear record; a reported one blocks; an invalid one blocks as `imei_invalid`; the rest leave it pending.
CLEAR_RESULTS = frozenset({TradeInIdentityRecord.RESULT_NO_REPORT, TradeInIdentityRecord.RESULT_DECLARED_CLEAR})
#: A unit in one of these is no longer a device the company holds: matching it is informative, never a duplicate.
GONE_STATUSES = (StockUnit.Status.SOLD, StockUnit.Status.WRITTEN_OFF)

_EXTRA_CODE = re.compile(r'^[a-z0-9][a-z0-9_]{1,40}$')
MAX_EXTRA = 20
MAX_TEXT = 300


# ---------------------------------------------------------------------------
# Identifiers
# ---------------------------------------------------------------------------

def _key(company_id: int) -> bytes:
    # Derived, never the secret itself: one use, one key (as `tracking_services._key`). Rotating SECRET_KEY only loses
    # the duplicate detection against older checks (docs/despliegue-produccion.md, «Rotar SECRET_KEY»).
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


_NON_ALNUM = re.compile(r'[\W_]+')
_LONG_DIGITS = re.compile(r'[0-9]{8,}')
#: Dates and times people write in notes («27/11/2026», «2026-11-27», «10:30»), standing on their own (no digit
#: glued before or after): removed first, and only when they ARE a date or a time (`_real_date_or_time`).
_DATES_AND_TIMES = re.compile(
    r'(?<![0-9])(?:(?P<y>[0-9]{4})-(?P<ym>[0-9]{2})-(?P<yd>[0-9]{2})'
    r'|(?P<d>[0-9]{1,2})[/.-](?P<m>[0-9]{1,2})(?:[/.-](?P<yy>[0-9]{2,4}))?'
    r'|(?P<h>[0-9]{1,2}):(?P<mi>[0-9]{2}))(?![0-9]|[/.:-][0-9])')


def _real_date_or_time(match) -> str:
    g = match.groupdict()
    if g['y'] is not None:
        ok = 1 <= int(g['ym']) <= 12 and 1 <= int(g['yd']) <= 31
    elif g['d'] is not None:
        ok = 1 <= int(g['d']) <= 31 and 1 <= int(g['m']) <= 12
    else:
        ok = int(g['h']) <= 23 and int(g['mi']) <= 59
    return ' ' if ok else match.group(0)
#: A generic serial: a plain run of 10+ UPPER-CASE letters and digits with at least one of each. Mixed case words
#: («Modelo13Pro») and short codes («SM-G991B», «MQ8T3LL/A») are everyday text.
_SERIAL_RUN = re.compile(r'[A-Z0-9]{10,}')
IMEI_DIGITS = 15


def _looks_like_serial(run: str) -> bool:
    return bool(_SERIAL_RUN.fullmatch(run)) and any(c.isdigit() for c in run) and any(c.isalpha() for c in run)


def _imei_window(digits: str) -> bool:
    """Any 15 consecutive digits that pass the IMEI check digit."""
    for start in range(0, len(digits) - IMEI_DIGITS + 1):
        window = digits[start:start + IMEI_DIGITS]
        if device_identity.luhn_check_digit(window[:14]) == window[14]:
            return True
    return False


def identity_form(value: str) -> str:
    """How an identifier is digested: without separators, upper case («C02-XG0-FDH7JY» = «c02xg0fdh7jy»)."""
    return _NON_ALNUM.sub('', unicodedata.normalize('NFKC', value or '')).upper()


def refuse_identifiers(text: str, field: str, *, company_id=None, digests=(), typed=(), last4s=()) -> None:
    """
    A free text (a description, a note, a reason) must never carry an identifier: it would reach the database, the
    screen and the audit in full. NFKC first, dates and times removed, then refused when:

      * a single word holds 8 or more digits in a row;
      * across separators (any non-digit, letters included), the text holds 14 or more digits, or any 15 of them in a
        row pass the IMEI check digit;
      * it has the shape of an EID, or a plain run of 10+ upper-case letters and digits (a generic serial);
      * it repeats an identifier typed in the same request;
      * any word — as typed, without its separators, or each of its parts — is, by its keyed digest, one of THIS
        check's identifiers; or any 11 digits in a row that, followed by the last four the screen shows, are one of
        its IMEIs («3569 3803 564» + «•••• 4380»).

    Everyday notes pass: dates, times, capacities, prices, model codes, versions.
    """
    if not text:
        return
    message = 'Este texto no lleva IMEI, series ni otros identificadores: van en sus campos.'
    normal = unicodedata.normalize('NFKC', text)
    if device_identity.looks_like_eid(normal):
        raise TariffError(message, code='identifier_in_text', field=field)
    scrubbed = _DATES_AND_TIMES.sub(_real_date_or_time, normal)
    words = scrubbed.split()
    if any(_LONG_DIGITS.search(word) for word in words):
        raise TariffError(message, code='identifier_in_text', field=field)
    digits = ''.join(ch for ch in scrubbed if ch.isdigit())
    if len(digits) >= 14 or _imei_window(digits):
        raise TariffError(message, code='identifier_in_text', field=field)
    runs = [run for run in _NON_ALNUM.split(scrubbed) if run]
    if any(_looks_like_serial(run) for run in runs):
        raise TariffError(message, code='identifier_in_text', field=field)
    compact = _NON_ALNUM.sub('', normal).upper()
    if any(value and _NON_ALNUM.sub('', value).upper() in compact for value in typed):
        raise TariffError(message, code='identifier_in_text', field=field)
    wanted = {d for d in digests if d}
    if wanted and company_id is not None:
        candidates = set()
        for word in normal.split():
            candidates.add(word.upper())
            candidates.add(_NON_ALNUM.sub('', word).upper())
            candidates.update(part.upper() for part in _NON_ALNUM.split(word) if part)
        if any(len(token) >= 4 and digest(company_id, token) in wanted for token in candidates):
            raise TariffError(message, code='identifier_in_text', field=field)
        stream = ''.join(ch for ch in normal if ch.isdigit())
        for start in range(0, len(stream) - 10):
            head = stream[start:start + 11]
            if any(last4 and digest(company_id, head + last4) in wanted for last4 in last4s):
                raise TariffError(message, code='identifier_in_text', field=field)


def _free_text(data, key, field, *, check=None, typed=()) -> str:
    text = ' '.join(str(data.get(key) or '').split())
    if len(text) > MAX_TEXT:
        raise TariffError(f'El texto admite hasta {MAX_TEXT} caracteres.', field=field)
    refuse_identifiers(text, field, company_id=check.company_id if check else None,
                       digests=(check.imei_digest, check.imei2_digest, check.serial_digest) if check else (),
                       typed=typed, last4s=(check.imei_last4, check.imei2_last4) if check else ())
    return text


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
    if 'require_identified_customer' in data:
        raw = data.get('require_identified_customer')
        if not isinstance(raw, bool):
            raise TariffError('Indica sí o no.', field='require_identified_customer')
        out['require_identified_customer'] = raw
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
            refuse_identifiers(label, 'extra_blockers')
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
    # Ids and states only: never an identifier, its last four characters, its digest or a free text.
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


def _stock_match(company, *, imeis=(), serial=''):
    """(live, known): a unit this company still holds (anything but sold, written off or voided) has one of these
    identifiers; a unit it sold or wrote off has."""
    from django.db.models import Q

    query = Q()
    if imeis:
        query |= Q(imei__in=imeis) | Q(imei2__in=imeis)
    if serial:
        query |= Q(serial_number=serial)       # stored by `clean_serial`: upper case, no spaces (uses the index)
    if not query:
        return False, False
    statuses = set(StockUnit.objects.filter(company=company).exclude(status=StockUnit.Status.VOIDED)
                   .filter(query).values_list('status', flat=True))
    return bool(statuses - set(GONE_STATUSES)), bool(statuses & set(GONE_STATUSES))


def _lock_identifiers(company_id: int, digests: list[str]) -> None:
    """Serialise two checks of the same identifier at once (PostgreSQL advisory lock per company and digest)."""
    from django.db import connection

    if connection.vendor != 'postgresql':
        return
    with connection.cursor() as cursor:
        for value in sorted(set(digests)):
            key = int.from_bytes(hashlib.sha256(f'trade-in-check|{company_id}|{value}'.encode()).digest()[:8],
                                 'big', signed=True)
            cursor.execute('SELECT pg_advisory_xact_lock(%s)', [key])


def create_check(*, actor, company, data, request=None) -> TradeInDeviceCheck:
    """Register a device offered at a branch: its identifiers as digests, the server's findings, the blockers due."""
    _require(actor, company, CAP_VALUE, 'No tienes permiso para comprobar equipos usados.')
    branch = _branch(actor, company, data.get('branch'))
    has_imei = data.get('has_imei', True)
    if has_imei is None:
        has_imei = True
    elif not isinstance(has_imei, bool):
        has_imei = str(has_imei).strip().lower() in ('true', '1', 'si', 'sí')
    imei, imei_valid = read_imei(data.get('imei'), 'El IMEI')
    imei2, imei2_valid = read_imei(data.get('imei2'), 'El IMEI 2')
    try:
        serial = device_identity.clean_serial(data.get('serial'))
    except device_identity.DeviceIdentityError as exc:
        raise TariffError(str(exc), field='serial') from None
    description = _free_text(data, 'description', 'description', typed=(imei, imei2, serial))
    if has_imei and not imei:
        raise TariffError('Escribe el IMEI del equipo (o marca que no tiene IMEI).', code='required', field='imei')
    if not has_imei and (imei or imei2):
        raise TariffError('Un equipo sin IMEI no lleva IMEI: escribe su número de serie.', field='imei')
    if not has_imei and not serial:
        raise TariffError('Un equipo sin IMEI se identifica por su número de serie.', code='required', field='serial')
    if imei and imei == imei2:
        raise TariffError('El IMEI 2 no puede ser igual al IMEI 1.', field='imei2')
    imei_digest = digest(company.pk, imei) if imei else ''
    imei2_digest = digest(company.pk, imei2) if imei2 else ''
    # The serial is digested in its identity form (no separators, upper case): «C02-XG0-FDH7JY» and «c02xg0fdh7jy»
    # are one device, for the duplicates and for the notes. The stock lookup keeps `clean_serial`'s form.
    serial_digest = digest(company.pk, identity_form(serial)) if serial else ''
    with transaction.atomic():
        imei_digests = [d for d in (imei_digest, imei2_digest) if d]
        _lock_identifiers(company.pk, imei_digests + ([serial_digest] if serial_digest else []))
        from django.db.models import Q

        opened = TradeInDeviceCheck.objects.filter(company=company, status=TradeInDeviceCheck.STATUS_OPEN)
        clash = Q(pk__in=[])
        if imei_digests:
            clash |= Q(imei_digest__in=imei_digests) | Q(imei2_digest__in=imei_digests)
        if not has_imei:
            clash |= Q(serial_digest=serial_digest)
        duplicate = 'open_check' if opened.filter(clash).exists() else ''
        live, known = _stock_match(company, imeis=[v for v in (imei, imei2) if v],
                                   serial=serial if not has_imei else '')
        if live:
            duplicate = 'stock'
        check = TradeInDeviceCheck.objects.create(
            company=company, branch=branch, description=description, has_imei=has_imei,
            imei_last4=imei[-4:], imei_digest=imei_digest, imei_valid=imei_valid,
            imei2_last4=imei2[-4:], imei2_digest=imei2_digest, imei2_valid=imei2_valid,
            serial_last4=serial[-4:], serial_digest=serial_digest,
            duplicate_of=duplicate, known_to_company=known,
            required_blockers=required_blockers(tariff.policy_of(company)),
            created_by=actor if getattr(actor, 'is_authenticated', False) else None)
        _audit(actor=actor, company=company, action='trade_in_device_check_created', check=check, request=request,
               has_imei=has_imei, has_imei2=bool(imei2), has_serial=bool(serial),
               imei_valid=imei_valid and imei2_valid, duplicate=duplicate, known_to_company=known)
    return check


def _open(company, check) -> TradeInDeviceCheck:
    if check.company_id != company.pk:
        raise TariffError('No encontrado.', code='not_found')
    locked = TradeInDeviceCheck.objects.select_for_update(of=('self',)).get(pk=check.pk)
    if locked.status != TradeInDeviceCheck.STATUS_OPEN:
        raise TariffStateError('Esta comprobación está cerrada.', code='closed')
    return locked


def answer_blocker(*, actor, company, check, data, request=None) -> TradeInBlockerAnswer:
    _require(actor, company, CAP_VALUE, 'No tienes permiso para comprobar equipos usados.')
    code = str(data.get('code') or '').strip()
    if code in COMPUTED:
        raise TariffError('Este bloqueo lo decide el sistema con el IMEI: no se responde a mano.', code='computed',
                          field='code')
    if code not in due_blockers(check):
        raise TariffError('Bloqueo no reconocido para esta comprobación.', field='code')
    answer = str(data.get('answer') or '').strip()
    if answer not in dict(TradeInBlockerAnswer.ANSWER_CHOICES):
        raise TariffError('Respuesta no válida (clear, blocked o not_verified).', field='answer')
    note = _free_text(data, 'note', 'note', check=check)
    with transaction.atomic():
        check = _open(company, check)
        row = TradeInBlockerAnswer.objects.filter(device_check=check, code=code).first()
        before = row.answer if row else None
        if row is None:
            row = TradeInBlockerAnswer(company=company, device_check=check, code=code)
        row.answer, row.note, row.answered_by, row.answered_at = answer, note, actor, timezone.now()
        row.save()
        _audit(actor=actor, company=company, action='trade_in_blocker_answered', check=check, request=request,
               code=code, answer=answer, before=before, has_note=bool(note))
    return row


def _checked_at(raw, company, now, *, earliest):
    """
    The moment of the consultation: readable, not in the future, and not before the check could matter (its opening
    minus one validity window). A far past or out-of-range value is a 400, never a row that later breaks a read.
    """
    if raw in (None, ''):
        return now
    try:
        value = parse_datetime(str(raw))
        if value is None:
            raise ValueError(raw)
        if timezone.is_naive(value):
            value = timezone.make_aware(value, tariff.company_timezone(company))
        value = value.astimezone(dt_timezone.utc)
        too_early = value < earliest
        too_late = value > now + timedelta(minutes=5)
    except (ValueError, TypeError, OverflowError):
        raise TariffError('La fecha y hora de la consulta no es válida.', field='checked_at') from None
    if too_late:
        raise TariffError('La consulta no puede ser futura.', field='checked_at')
    if too_early:
        raise TariffError('La consulta es anterior a esta comprobación: vuelve a consultar la fuente.',
                          code='too_old', field='checked_at')
    return value


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
    if result == TradeInIdentityRecord.RESULT_DECLARED_CLEAR:
        # A declaration replaces a consultation only where none is possible: the serial of a device without IMEI, in a
        # company with no active source for serials. Never for an IMEI (review of #463).
        if slot != TradeInIdentityRecord.SLOT_SERIAL or check.has_imei:
            raise TariffError('Un IMEI se verifica en una fuente oficial, no por declaración.',
                              code='declaration_not_allowed', field='result')
        if TradeInVerificationSource.objects.filter(
                company=company, is_active=True, identifier=TradeInVerificationSource.IDENTIFIER_SERIAL).exists():
            raise TariffError('La empresa tiene una fuente para consultar series: úsala.',
                              code='declaration_not_allowed', field='result')
        if raw not in (None, ''):
            raise TariffError('Una declaración no lleva fuente.', field='source')
    else:
        if raw in (None, ''):
            raise TariffError('Indica la fuente oficial que consultaste.', code='required', field='source')
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
    hours = tariff.policy_of(company).identity_check_validity_hours
    checked_at = _checked_at(data.get('checked_at'), company, now,
                             earliest=check.created_at - timedelta(hours=hours))
    note = _free_text(data, 'note', 'note', check=check)
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
               checked_at=checked_at.isoformat(), has_note=bool(note))
    return record


def _cancel_live_assessments(*, actor, company, check, request=None) -> None:
    """A device check that closes takes its live assessment with it (audited): nothing is valued on a device whose
    check is over."""
    from .models import TradeInAssessment

    live = TradeInAssessment.objects.select_for_update(of=('self',)).filter(
        device_check=check, status__in=(*TradeInAssessment.EDITABLE, TradeInAssessment.STATUS_APPROVED))
    for assessment in live:
        assessment.status = TradeInAssessment.STATUS_CANCELLED
        assessment.cancelled_at, assessment.cancel_reason = timezone.now(), 'Comprobación del equipo cerrada.'
        assessment.save(update_fields=['status', 'cancelled_at', 'cancel_reason', 'updated_at'])
        AdminAuditLog.log(actor=actor if getattr(actor, 'is_authenticated', False) else None,
                          action='trade_in_assessment_cancelled', target_type='trade_in_assessment',
                          target_id=assessment.pk,
                          metadata={'company_id': company.pk, 'assessment_id': assessment.pk,
                                    'number': assessment.label, 'status': assessment.status,
                                    'because': 'device_check_closed', 'check_id': check.pk},
                          request=request, company=company)


def close_check(*, actor, company, check, reason, request=None):
    _require(actor, company, CAP_VALUE, 'No tienes permiso para comprobar equipos usados.')
    reason = _free_text({'reason': reason}, 'reason', 'reason', check=check)
    if not reason:
        raise TariffError('Indica por qué se cierra.', code='reason_required', field='reason')
    with transaction.atomic():
        check = _open(company, check)
        check.status = TradeInDeviceCheck.STATUS_CLOSED
        check.closed_at, check.closed_by, check.close_reason = timezone.now(), actor, reason
        check.save(update_fields=['status', 'closed_at', 'closed_by', 'close_reason', 'updated_at'])
        _cancel_live_assessments(actor=actor, company=company, check=check, request=request)
        # Never the reason itself: a free text is not for the audit.
        _audit(actor=actor, company=company, action='trade_in_device_check_closed', check=check, request=request,
               has_reason=True, reason_length=len(reason))
    return check


# ---------------------------------------------------------------------------
# The verdict
# ---------------------------------------------------------------------------

def due_blockers(check) -> dict[str, str]:
    """The blockers this check answers: the mandatory floor ALWAYS, plus the ones frozen when it opened. An empty or
    damaged frozen set never makes a check eligible."""
    out = {code: BLOCKERS[code] for code in MANDATORY}
    frozen = check.required_blockers if isinstance(check.required_blockers, dict) else {}
    out.update(frozen)
    return out


def required_slots(check) -> list[str]:
    if check.has_imei:
        return [TradeInIdentityRecord.SLOT_IMEI] + ([TradeInIdentityRecord.SLOT_IMEI2] if check.imei2_digest else [])
    return [TradeInIdentityRecord.SLOT_SERIAL]


def evaluate(check, *, now=None) -> dict:
    """
    The security verdict of a check, computed on each read and never stored. The blockers due are the ones frozen
    when the check was opened; any `blocked` answer blocks, whatever the policy says later; a `reported` or `invalid`
    record blocks for the whole life of the check, even once expired or followed by a cleaner record — clearing it
    takes closing the check and opening a new one, both audited.
    """
    now = now or timezone.now()
    required = due_blockers(check)
    answers = {row.code: row for row in check.blocker_answers.all()}
    for code, row in answers.items():
        required.setdefault(code, BLOCKERS.get(code, code))
    states: dict[str, dict] = {}
    for code, label in required.items():
        row = answers.get(code)
        states[code] = {'code': code, 'label': label, 'mandatory': code in MANDATORY,
                        'state': row.answer if row else 'unanswered', 'source': 'answer' if row else None}
    for code in COMPUTED:
        states.setdefault(code, {'code': code, 'label': BLOCKERS[code], 'mandatory': True})
    bad = not (check.imei_valid and check.imei2_valid)
    states['imei_invalid'].update(state='blocked' if bad else 'clear', source='computed')
    states['imei_duplicate'].update(state='blocked' if check.duplicate_of else 'clear', source='computed',
                                    detail=check.duplicate_of or None)
    records = sorted(check.identity_records.all(), key=lambda r: (r.checked_at, r.pk), reverse=True)
    for record in records:
        if record.result == TradeInIdentityRecord.RESULT_REPORTED:
            states.setdefault('theft_or_loss_report', {'code': 'theft_or_loss_report',
                                                       'label': BLOCKERS['theft_or_loss_report'], 'mandatory': True})
            states['theft_or_loss_report'].update(state='blocked', source='identity_record')
        elif record.result == TradeInIdentityRecord.RESULT_INVALID:
            states['imei_invalid'].update(state='blocked', source='identity_record')
    slots = []
    for slot in required_slots(check):
        latest = next((r for r in records if r.slot == slot), None)
        valid = latest is not None and latest.valid_until > now
        state = 'missing' if latest is None else ('expired' if not valid else latest.result)
        slots.append({'slot': slot, 'state': state, 'record': latest.pk if latest else None,
                      'valid_until': latest.valid_until if latest else None})
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
