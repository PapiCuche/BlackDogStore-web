"""
TRADE-IN VALUATION PRO · Phase 2 — the company's valuation rules (docs/adr-trade-in-valuation.md §3.2, §6).

GROUPS AND FACTORS are the company's questions (battery, cosmetics…, «Pantalla», «Salud de batería»), written live.
RULES live in versions of the whole set:

    draft ──propose──▶ pending ──approve──▶ approved
      │                   ├──reject───▶ rejected
      └──withdraw──▶ withdrawn ◀──withdraw┘

A draft is edited freely (one per company); proposing freezes, in `snapshot`, the groups and factors as they are then,
so a later rename or a new cap never changes what an approved version computes. EVERY version waits for an approver
(the conservative reading of the tariff's relevance rule: any rule change moves what the shop pays); the proposer
approves only with `approve_own` and a reason, flagged in the audit. A version starts no earlier than today; one
approved after its start moves to today. In force on a day: the newest approved version that has started, if it has
not ended — an ended newer version never hands the day back to an older one (the tariff's rule, 2026-11-27).

AUTHORITY: the tariff's four capabilities (`sales.tradein.tariff.*`): the rules are the same commercial decision —
what the shop pays for a used device. Nothing here buys, pays, credits or receives anything.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone

from . import trade_in_tariff as tariff
from . import trade_in_valuation as valuation
from .models import (
    AdminAuditLog,
    ProductModel,
    TradeInFactor,
    TradeInFactorGroup,
    TradeInRule,
    TradeInRuleSetVersion,
    TradeInTariffEntry,
)
from .tenancy import has_capability

CAP_VIEW = tariff.CAP_VIEW
CAP_MANAGE = tariff.CAP_MANAGE
CAP_APPROVE = tariff.CAP_APPROVE
CAP_APPROVE_OWN = tariff.CAP_APPROVE_OWN

TariffError = tariff.TariffError
TariffPermissionError = tariff.TariffPermissionError
TariffStateError = tariff.TariffStateError
TariffConflict = tariff.TariffConflict

MAX_OPTIONS = 30
MAX_OPTION = 60


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _require(actor, company, capability, message):
    if not has_capability(actor, company, capability):
        raise TariffPermissionError(message)


def _manage(actor, company):
    _require(actor, company, CAP_MANAGE, 'No tienes permiso para editar las reglas de valorización.')


def _audit(*, actor, company, action, target_type, target_id, request=None, **metadata):
    AdminAuditLog.log(actor=actor if getattr(actor, 'is_authenticated', False) else None, action=action,
                      target_type=target_type, target_id=target_id,
                      metadata={'company_id': company.pk, **metadata}, request=request, company=company)


def _text(data, key, limit, label, *, required=False) -> str:
    text = ' '.join(str(data.get(key) or '').split())
    if len(text) > limit:
        raise TariffError(f'{label} admite hasta {limit} caracteres.', field=key)
    if required and not text:
        raise TariffError(f'Indica {label.lower()}.', code='required', field=key)
    return text


def _choice(data, key, choices, label, *, default=None) -> str:
    value = str(data.get(key) if data.get(key) is not None else (default or '')).strip()
    if value not in dict(choices):
        raise TariffError(f'{label} no válido.', field=key)
    return value


def _percent(raw, key, label, *, required=False) -> Decimal | None:
    try:
        value = tariff.parse_money(raw, label, required=required)
    except TariffError as exc:
        exc.field = key
        raise
    if value is not None and value > Decimal('100'):
        raise TariffError(f'{label[0].upper()}{label[1:]} va de 0 a 100.', field=key)
    return value


INT_MAX = 2_147_483_647


def _int(raw, key, label, *, default=0, minimum=-INT_MAX) -> int:
    """An integer that fits the column (a database `integer`): out of range is a 400 on its field, never a 500."""
    if raw in (None, ''):
        return default
    if isinstance(raw, bool):
        raise TariffError(f'{label} no es un número entero.', field=key)
    try:
        value = int(str(raw).strip())
    except ValueError:
        raise TariffError(f'{label} no es un número entero.', field=key) from None
    if value < minimum or value > INT_MAX:
        raise TariffError(f'{label} va de {minimum} a {INT_MAX}.', field=key)
    return value


def _bool(raw, *, default=False) -> bool:
    if raw in (None, ''):
        return default
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in ('true', '1', 'si', 'sí', 'yes')


# ---------------------------------------------------------------------------
# Groups and factors
# ---------------------------------------------------------------------------

def _group_fields(data, *, partial=False, instance=None) -> dict:
    out = {}
    if not partial or 'kind' in data:
        out['kind'] = _choice(data, 'kind', TradeInFactorGroup.KIND_CHOICES, 'Tipo de grupo')
    if not partial or 'name' in data:
        out['name'] = _text(data, 'name', 80, 'El nombre', required=True)
    if not partial or 'combine' in data:
        out['combine'] = _choice(data, 'combine', TradeInFactorGroup.COMBINE_CHOICES, 'Modo de combinar',
                                 default=TradeInFactorGroup.COMBINE_MAX)
    if not partial or 'group_cap_percent' in data:
        out['group_cap_percent'] = _percent(data.get('group_cap_percent'), 'group_cap_percent', 'el tope del grupo')
    if not partial or 'sort_order' in data:
        out['sort_order'] = _int(data.get('sort_order'), 'sort_order', 'El orden', minimum=0)
    if partial and 'is_active' in data:
        out['is_active'] = _bool(data.get('is_active'), default=True)
    return out


def _touch_open_draft(company, actor) -> None:
    """
    Editing a group or a question changes what the company's OPEN draft computes (its snapshot is taken only when it
    is proposed): whoever does it becomes one of the draft's authors. A pending set is frozen and is not touched.
    """
    pk = getattr(actor, 'pk', None)
    if pk is None:
        return
    draft = (TradeInRuleSetVersion.objects.select_for_update()
             .filter(company=company, status=TradeInRuleSetVersion.STATUS_DRAFT).first())
    if draft is not None and pk not in (draft.contributor_ids or []):
        draft.contributor_ids = [*(draft.contributor_ids or []), pk]
        draft.save(update_fields=['contributor_ids'])


def create_group(*, actor, company, data, request=None) -> TradeInFactorGroup:
    _manage(actor, company)
    fields = _group_fields(data)
    try:
        with transaction.atomic():
            group = TradeInFactorGroup.objects.create(company=company, **fields)
            _touch_open_draft(company, actor)
    except IntegrityError:
        raise TariffConflict('Ya existe un grupo con ese nombre.', code='duplicate', field='name') from None
    _audit(actor=actor, company=company, action='trade_in_factor_group_created', target_type='trade_in_factor_group',
           target_id=group.pk, request=request, name=group.name, kind=group.kind)
    return group


def update_group(*, actor, company, group, data, request=None) -> TradeInFactorGroup:
    _manage(actor, company)
    fields = _group_fields(data, partial=True)
    before = {key: str(getattr(group, key)) for key in fields}
    for key, value in fields.items():
        setattr(group, key, value)
    try:
        with transaction.atomic():
            group.save()
            _touch_open_draft(company, actor)
    except IntegrityError:
        raise TariffConflict('Ya existe un grupo con ese nombre.', code='duplicate', field='name') from None
    _audit(actor=actor, company=company, action='trade_in_factor_group_updated', target_type='trade_in_factor_group',
           target_id=group.pk, request=request, before=before,
           after={key: str(getattr(group, key)) for key in fields})
    return group


def _factor_fields(company, data, *, partial=False, input_kind=None) -> dict:
    out = {}
    if not partial or 'group' in data:
        try:
            group_id = int(data.get('group'))
        except (TypeError, ValueError):
            raise TariffError('Elige el grupo.', code='required', field='group') from None
        group = TradeInFactorGroup.objects.filter(company=company, pk=group_id).first()
        if group is None:
            raise TariffError('El grupo no existe.', code='not_found', field='group')
        out['group'] = group
    if not partial or 'label' in data:
        out['label'] = _text(data, 'label', 120, 'La pregunta', required=True)
    if not partial:
        out['input'] = _choice(data, 'input', TradeInFactor.INPUT_CHOICES, 'Tipo de respuesta')
    kind = out.get('input') or input_kind
    if 'options' in data or (not partial and kind == TradeInFactor.INPUT_CHOICE):
        raw = data.get('options') or []
        if not isinstance(raw, list):
            raise TariffError('Las opciones son una lista de textos.', field='options')
        options = []
        for item in raw[:MAX_OPTIONS + 1]:
            text = ' '.join(str(item or '').split())
            if not text or len(text) > MAX_OPTION:
                raise TariffError(f'Cada opción tiene de 1 a {MAX_OPTION} caracteres.', field='options')
            if text not in options:
                options.append(text)
        if len(options) > MAX_OPTIONS:
            raise TariffError(f'Hasta {MAX_OPTIONS} opciones.', field='options')
        if kind == TradeInFactor.INPUT_CHOICE and not options:
            raise TariffError('Una pregunta de opciones necesita al menos una opción.', field='options')
        if kind != TradeInFactor.INPUT_CHOICE and options:
            raise TariffError('Sólo una pregunta de opciones lleva opciones.', field='options')
        out['options'] = options
    for key, limit, label in (('component', 60, 'El componente'), ('applies_brand', 80, 'La marca'),
                              ('applies_model_name', 160, 'El modelo')):
        if not partial or key in data:
            out[key] = _text(data, key, limit, label)
    if not partial or 'applies_product_model' in data:
        raw = data.get('applies_product_model')
        if raw in (None, ''):
            out['applies_product_model'] = None
        else:
            try:
                model = ProductModel.objects.filter(company=company, pk=int(raw)).first()
            except (TypeError, ValueError):
                model = None
            if model is None:
                raise TariffError('El modelo de catálogo no existe.', code='not_found', field='applies_product_model')
            out['applies_product_model'] = model
    if not partial or 'requires_photo' in data:
        out['requires_photo'] = _bool(data.get('requires_photo'))
    if not partial or 'sort_order' in data:
        out['sort_order'] = _int(data.get('sort_order'), 'sort_order', 'El orden', minimum=0)
    if partial and 'is_active' in data:
        out['is_active'] = _bool(data.get('is_active'), default=True)
    return out


def create_factor(*, actor, company, data, request=None) -> TradeInFactor:
    _manage(actor, company)
    from django.utils.text import slugify

    code = slugify(str(data.get('code') or data.get('label') or ''))[:60]
    if not code:
        raise TariffError('Indica el código de la pregunta.', code='required', field='code')
    fields = _factor_fields(company, data)
    try:
        with transaction.atomic():
            factor = TradeInFactor.objects.create(company=company, code=code, **fields)
            _touch_open_draft(company, actor)
    except IntegrityError:
        raise TariffConflict('Ya existe una pregunta con ese código.', code='duplicate', field='code') from None
    _audit(actor=actor, company=company, action='trade_in_factor_created', target_type='trade_in_factor',
           target_id=factor.pk, request=request, code=factor.code, group=factor.group_id)
    return factor


def update_factor(*, actor, company, factor, data, request=None) -> TradeInFactor:
    """Everything but the code and the kind of answer: rules and frozen versions refer to those."""
    _manage(actor, company)
    refused = sorted({'code', 'input'} & set(data))
    if refused:
        raise TariffError('El código y el tipo de respuesta de una pregunta no cambian: crea otra.',
                          code='identity_fixed', extra={'fields': refused})
    fields = _factor_fields(company, data, partial=True, input_kind=factor.input)
    if 'options' in fields:
        # A draft rule that names an option being removed would never match again: say which, change nothing.
        orphaned = sorted(TradeInRule.objects.filter(
            factor=factor, version__status=TradeInRuleSetVersion.STATUS_DRAFT).exclude(
            when_outcome__in=fields['options']).values_list('pk', flat=True))
        if orphaned:
            raise TariffConflict('Hay reglas del borrador que usan una opción que quitas: cámbialas antes.',
                                 code='options_in_use', field='options', extra={'rules': orphaned})
    for key, value in fields.items():
        setattr(factor, key, value)
    with transaction.atomic():
        factor.save()
        _touch_open_draft(company, actor)
    _audit(actor=actor, company=company, action='trade_in_factor_updated', target_type='trade_in_factor',
           target_id=factor.pk, request=request, code=factor.code, fields=sorted(fields))
    return factor


# ---------------------------------------------------------------------------
# Rule-set versions
# ---------------------------------------------------------------------------

def latest_approved(company):
    return (TradeInRuleSetVersion.objects.filter(company=company, status=TradeInRuleSetVersion.STATUS_APPROVED)
            .order_by('-number').first())


def effective_rule_set(company, *, on: date | None = None):
    """The rule set in force on `on` (company's calendar): the newest approved one that has started, if not ended."""
    day = on or tariff.company_today(company)
    started = (TradeInRuleSetVersion.objects.filter(company=company, status=TradeInRuleSetVersion.STATUS_APPROVED,
                                                    valid_from__lte=day).order_by('-number').first())
    if started is None or (started.valid_until is not None and started.valid_until < day):
        return None
    return started


def _version_audit(action, *, actor, company, version, request=None, **metadata):
    _audit(actor=actor, company=company, action=action, target_type='trade_in_rule_set_version',
           target_id=version.pk, request=request, number=version.number, status=version.status, **metadata)


def create_draft(*, actor, company, data=None, request=None) -> TradeInRuleSetVersion:
    """A new draft: a copy of the rules of `source` (an approved version; by default the latest), or empty."""
    _manage(actor, company)
    data = data or {}
    with transaction.atomic():
        source = None
        raw = data.get('source_version')
        if raw not in (None, ''):
            try:
                source = TradeInRuleSetVersion.objects.filter(company=company, pk=int(raw)).first()
            except (TypeError, ValueError):
                source = None
            if source is None:
                raise TariffError('La versión de origen no existe.', code='not_found', field='source_version')
            if source.status != TradeInRuleSetVersion.STATUS_APPROVED:
                raise TariffError('Sólo se copia una versión aprobada.', code='not_approved', field='source_version')
        elif not data.get('empty'):
            source = latest_approved(company)
        number = (TradeInRuleSetVersion.objects.filter(company=company).order_by('-number')
                  .values_list('number', flat=True).first() or 0) + 1
        try:
            with transaction.atomic():
                draft = TradeInRuleSetVersion.objects.create(
                    company=company, number=number, source_version=source,
                    contributor_ids=[actor.pk] if getattr(actor, 'pk', None) else [],
                    created_by=actor if getattr(actor, 'is_authenticated', False) else None)
        except IntegrityError:
            raise TariffConflict('Ya hay un borrador de reglas: termínalo o retíralo antes de abrir otro.',
                                 code='draft_exists') from None
        dropped = []
        if source is not None:
            for rule in source.rules.select_related('factor__group'):
                if not (rule.factor.is_active and rule.factor.group.is_active):
                    # A question (or its group) the company switched off is not copied: said, never silent.
                    dropped.append({'rule': rule.pk, 'factor': rule.factor.code})
                    continue
                TradeInRule.objects.create(
                    company=company, version=draft, factor_id=rule.factor_id, when_outcome=rule.when_outcome,
                    when_percent_gte=rule.when_percent_gte, when_percent_lt=rule.when_percent_lt,
                    effect=rule.effect, amount=rule.amount, percent=rule.percent, priority=rule.priority,
                    owner_id=rule.owner_id, notes=rule.notes)
        _version_audit('trade_in_rule_set_draft_created', actor=actor, company=company, version=draft,
                       request=request, source=source.number if source else None, dropped=dropped)
    draft.dropped_rules = dropped
    return draft


def _draft(company, version) -> TradeInRuleSetVersion:
    if version.company_id != company.pk:
        raise TariffError('No encontrado.', code='not_found')
    locked = TradeInRuleSetVersion.objects.select_for_update().get(pk=version.pk)
    if locked.status != TradeInRuleSetVersion.STATUS_DRAFT:
        raise TariffStateError('Sólo se editan las reglas de un borrador.', code='not_draft')
    return locked


def check_condition(input_kind: str, options, outcome: str, gte, lt) -> None:
    """Whether a rule's condition can ever match an answer to a question of this kind (and these options)."""
    if input_kind == TradeInFactor.INPUT_PERCENT:
        if outcome or (gte is None and lt is None):
            raise TariffError('Una pregunta de porcentaje se condiciona con un rango (desde / hasta).',
                              field='when_percent_gte')
        if gte is not None and lt is not None and lt <= gte:
            raise TariffError('El rango termina antes de empezar.', field='when_percent_lt')
        return
    if gte is not None or lt is not None or not outcome:
        raise TariffError('Indica el resultado que activa la regla.', code='required', field='when_outcome')
    allowed = {TradeInFactor.INPUT_CHECK: TradeInFactor.CHECK_OUTCOMES,
               TradeInFactor.INPUT_BOOLEAN: ('true', 'false'),
               TradeInFactor.INPUT_CHOICE: tuple(options or ())}[input_kind]
    if outcome not in allowed:
        raise TariffError('Ese resultado no existe para esta pregunta.', field='when_outcome')


def _rule_fields(company, data, *, partial=False, rule=None) -> dict:
    out = {}
    factor = rule.factor if rule else None
    if not partial or 'factor' in data:
        try:
            factor = TradeInFactor.objects.filter(company=company, pk=int(data.get('factor'))).first()
        except (TypeError, ValueError):
            factor = None
        if factor is None:
            raise TariffError('La pregunta no existe.', code='not_found', field='factor')
        out['factor'] = factor
    condition_keys = {'when_outcome', 'when_percent_gte', 'when_percent_lt'}
    if not partial or condition_keys & set(data):
        # A partial edit names only what changes: the other parts of the condition stay as stored.
        outcome = (_text(data, 'when_outcome', 60, 'El resultado') if not partial or 'when_outcome' in data
                   else rule.when_outcome)
        gte = (_percent(data.get('when_percent_gte'), 'when_percent_gte', 'el porcentaje mínimo')
               if not partial or 'when_percent_gte' in data else rule.when_percent_gte)
        lt = (_percent(data.get('when_percent_lt'), 'when_percent_lt', 'el porcentaje máximo')
              if not partial or 'when_percent_lt' in data else rule.when_percent_lt)
        check_condition(factor.input, factor.options, outcome, gte, lt)
        out.update(when_outcome=outcome, when_percent_gte=gte, when_percent_lt=lt)
    elif 'factor' in out:
        # Another question, the same condition: it must still make sense for the new question.
        check_condition(factor.input, factor.options, rule.when_outcome, rule.when_percent_gte, rule.when_percent_lt)
    if not partial or {'effect', 'amount', 'percent'} & set(data):
        effect = _choice(data, 'effect', TradeInRule.EFFECT_CHOICES, 'Efecto') if (not partial or 'effect' in data) \
            else rule.effect
        amount = percent = None
        if effect in (TradeInRule.EFFECT_FIXED, TradeInRule.EFFECT_REPAIR_COST):
            try:
                amount = tariff.parse_money(data.get('amount'), 'el importe', required=True)
            except TariffError as exc:
                exc.field = 'amount'
                raise
        elif effect == TradeInRule.EFFECT_PERCENT:
            percent = _percent(data.get('percent'), 'percent', 'el porcentaje', required=True)
        out.update(effect=effect, amount=amount, percent=percent)
    if not partial or 'priority' in data:
        out['priority'] = _int(data.get('priority'), 'priority', 'La prioridad')
    if not partial or 'notes' in data:
        out['notes'] = _text(data, 'notes', 300, 'La nota')
    if not partial or 'owner' in data:
        raw = data.get('owner')
        if raw in (None, ''):
            out['owner'] = None
        else:
            from .models import Membership

            try:
                membership = Membership.objects.filter(company=company, user_id=int(raw), is_active=True).first()
            except (TypeError, ValueError):
                membership = None
            if membership is None:
                raise TariffError('El responsable no es del personal de la empresa.', code='not_found', field='owner')
            out['owner'] = membership.user
    return out


def _contributed(draft, actor) -> None:
    """Record `actor` among the draft's writers (approving it later is approving their own work)."""
    pk = getattr(actor, 'pk', None)
    if pk is not None and pk not in (draft.contributor_ids or []):
        draft.contributor_ids = [*(draft.contributor_ids or []), pk]
        draft.save(update_fields=['contributor_ids'])


def authors(version) -> set:
    """Who wrote this set: whoever opened, edited or proposed it."""
    return {pk for pk in (version.created_by_id, version.proposed_by_id, *(version.contributor_ids or [])) if pk}


def add_rule(*, actor, company, version, data, request=None) -> TradeInRule:
    _manage(actor, company)
    with transaction.atomic():
        draft = _draft(company, version)
        rule = TradeInRule.objects.create(company=company, version=draft, **_rule_fields(company, data))
        _contributed(draft, actor)
        _version_audit('trade_in_rule_added', actor=actor, company=company, version=draft, request=request,
                       rule=rule.pk, factor=rule.factor.code, effect=rule.effect)
    return rule


def update_rule(*, actor, company, rule, data, request=None) -> TradeInRule:
    _manage(actor, company)
    with transaction.atomic():
        draft = _draft(company, rule.version)
        for key, value in _rule_fields(company, data, partial=True, rule=rule).items():
            setattr(rule, key, value)
        rule.save()
        _contributed(draft, actor)
        _version_audit('trade_in_rule_updated', actor=actor, company=company, version=draft, request=request,
                       rule=rule.pk, factor=rule.factor.code, effect=rule.effect)
    return rule


def delete_rule(*, actor, company, rule, request=None) -> None:
    _manage(actor, company)
    with transaction.atomic():
        draft = _draft(company, rule.version)
        pk, code = rule.pk, rule.factor.code
        rule.delete()
        _contributed(draft, actor)
        _version_audit('trade_in_rule_deleted', actor=actor, company=company, version=draft, request=request,
                       rule=pk, factor=code)


def snapshot_of(company, version) -> dict:
    """The groups and factors a version computes with: every active one, plus any its rules name. Plain data."""
    factor_ids = set(version.rules.values_list('factor_id', flat=True))
    factors = TradeInFactor.objects.filter(company=company).filter(
        models_q_active_or(factor_ids)).select_related('group')
    groups, out_factors = {}, {}
    for factor in factors:
        group = factor.group
        groups[str(group.pk)] = {'kind': group.kind, 'name': group.name, 'combine': group.combine,
                                 'cap_percent': None if group.group_cap_percent is None else str(group.group_cap_percent),
                                 'is_active': group.is_active, 'sort_order': group.sort_order}
        out_factors[factor.code] = {
            'id': factor.pk, 'group': str(group.pk), 'label': factor.label, 'input': factor.input,
            'options': list(factor.options or []), 'component': factor.component,
            'applies_brand': factor.applies_brand, 'applies_model_name': factor.applies_model_name,
            'applies_product_model': factor.applies_product_model_id, 'requires_photo': factor.requires_photo,
            'is_active': factor.is_active, 'sort_order': factor.sort_order,
        }
    return {'groups': groups, 'factors': out_factors}


def models_q_active_or(factor_ids):
    from django.db.models import Q

    return Q(is_active=True) | Q(pk__in=factor_ids)


def propose(*, actor, company, version, data, request=None, today: date | None = None):
    """Freeze the draft (its window, its groups and factors) and wait for an approver."""
    _manage(actor, company)
    today = today or tariff.company_today(company)
    reason = _text(data, 'reason', 300, 'El motivo')
    valid_from = tariff.parse_date(data.get('valid_from'), 'el inicio de la vigencia', required=False) or today
    valid_until = tariff.parse_date(data.get('valid_until'), 'el fin de la vigencia', required=False)
    if valid_from < today:
        raise TariffError('La vigencia no puede empezar antes de hoy: las reglas no son retroactivas.',
                          code='retroactive', field='valid_from')
    if valid_until is not None and valid_until < valid_from:
        raise TariffError('La vigencia termina antes de empezar.', code='window', field='valid_until')
    with transaction.atomic():
        draft = _draft(company, version)
        if not draft.rules.exists():
            raise TariffStateError('Un conjunto de reglas sin reglas no se propone.', code='empty')
        snapshot = snapshot_of(company, draft)
        _check_against_snapshot(draft, snapshot)
        draft.snapshot = snapshot
        if getattr(actor, 'pk', None) and actor.pk not in (draft.contributor_ids or []):
            draft.contributor_ids = [*(draft.contributor_ids or []), actor.pk]
        draft.valid_from, draft.valid_until, draft.reason = valid_from, valid_until, reason
        draft.status = TradeInRuleSetVersion.STATUS_PENDING
        draft.proposed_by = actor if getattr(actor, 'is_authenticated', False) else None
        draft.proposed_at = timezone.now()
        try:
            with transaction.atomic():
                draft.save()
        except IntegrityError:
            raise TariffConflict('Ya hay un conjunto de reglas pendiente de aprobación: decídelo o retíralo.',
                                 code='pending_exists') from None
        _version_audit('trade_in_rule_set_proposed', actor=actor, company=company, version=draft, request=request,
                       valid_from=valid_from.isoformat(), rules=draft.rules.count(), reason=reason)
    return draft


def _check_against_snapshot(draft, snapshot) -> None:
    """Every rule of the draft against the questions as they will be frozen: active, and a condition that can match."""
    by_id = {f['id']: f for f in snapshot['factors'].values()}
    inactive, invalid = [], []
    for rule in draft.rules.all():
        factor = by_id[rule.factor_id]
        group = snapshot['groups'][factor['group']]
        if not (factor['is_active'] and group['is_active']):
            inactive.append(rule.pk)
            continue
        try:
            check_condition(factor['input'], factor['options'], rule.when_outcome, rule.when_percent_gte,
                            rule.when_percent_lt)
        except TariffError:
            invalid.append(rule.pk)
    if inactive:
        raise TariffStateError('Hay reglas sobre preguntas o grupos desactivados: quítalas o reactívalos.',
                               code='inactive_in_rules', extra={'rules': sorted(inactive)})
    if invalid:
        raise TariffStateError('Hay reglas cuya condición ya no corresponde a su pregunta: corrígelas.',
                               code='rules_invalid', extra={'rules': sorted(invalid)})


def _locked(company, version):
    if version.company_id != company.pk:
        raise TariffError('No encontrado.', code='not_found')
    return TradeInRuleSetVersion.objects.select_for_update().get(pk=version.pk)


def approve(*, actor, company, version, note='', request=None, today: date | None = None):
    _require(actor, company, CAP_APPROVE, 'No tienes permiso para aprobar reglas de valorización.')
    note = ' '.join(str(note or '').split())[:300]
    today = today or tariff.company_today(company)
    with transaction.atomic():
        version = _locked(company, version)
        if version.status != TradeInRuleSetVersion.STATUS_PENDING:
            raise TariffStateError('Este conjunto de reglas no está pendiente de aprobación.', code='not_pending')
        if version.valid_until is not None and version.valid_until < today:
            raise TariffStateError('Su vigencia ya terminó: recházalo y propone otro.', code='expired')
        own = getattr(actor, 'pk', None) in authors(version)
        if own:
            if not has_capability(actor, company, CAP_APPROVE_OWN):
                raise TariffPermissionError('Otra persona con permiso de aprobación tiene que aprobar estas reglas: '
                                            'participaste en ellas.', code='own_change')
            if not note:
                raise TariffError('Indica el motivo para aprobar un cambio propio.', code='reason_required',
                                  field='note')
        fields = ['status', 'approval_kind', 'decided_by', 'decided_at', 'decision_note']
        moved_from = None
        if version.valid_from < today:
            moved_from = version.valid_from.isoformat()
            version.valid_from = today
            fields.append('valid_from')
        version.status = TradeInRuleSetVersion.STATUS_APPROVED
        version.approval_kind = (TradeInRuleSetVersion.APPROVAL_SELF if own
                                 else TradeInRuleSetVersion.APPROVAL_APPROVER)
        version.decided_by, version.decided_at, version.decision_note = actor, timezone.now(), note
        version.save(update_fields=fields)
        _version_audit('trade_in_rule_set_approved', actor=actor, company=company, version=version, request=request,
                       self_approval=own, note=note, start_moved_from=moved_from,
                       valid_from=version.valid_from.isoformat())
    return version


def reject(*, actor, company, version, note, request=None):
    _require(actor, company, CAP_APPROVE, 'No tienes permiso para rechazar reglas de valorización.')
    note = ' '.join(str(note or '').split())[:300]
    if not note:
        raise TariffError('Indica por qué se rechaza.', code='reason_required', field='note')
    with transaction.atomic():
        version = _locked(company, version)
        if version.status != TradeInRuleSetVersion.STATUS_PENDING:
            raise TariffStateError('Este conjunto de reglas no está pendiente de aprobación.', code='not_pending')
        version.status = TradeInRuleSetVersion.STATUS_REJECTED
        version.decided_by, version.decided_at, version.decision_note = actor, timezone.now(), note
        version.save(update_fields=['status', 'decided_by', 'decided_at', 'decision_note'])
        _version_audit('trade_in_rule_set_rejected', actor=actor, company=company, version=version, request=request,
                       note=note)
    return version


def withdraw(*, actor, company, version, note='', request=None):
    """A draft or a pending set, taken back by whoever wrote or proposed it, or by an approver."""
    _manage(actor, company)
    note = ' '.join(str(note or '').split())[:300]
    with transaction.atomic():
        version = _locked(company, version)
        if version.status not in (TradeInRuleSetVersion.STATUS_DRAFT, TradeInRuleSetVersion.STATUS_PENDING):
            raise TariffStateError('Este conjunto de reglas ya fue decidido.', code='not_pending')
        if getattr(actor, 'pk', None) not in authors(version) and not has_capability(actor, company, CAP_APPROVE):
            raise TariffPermissionError('Sólo quien lo escribió o propuso, o quien puede aprobarlo, lo retira.')
        fields = ['status', 'decided_by', 'decided_at', 'decision_note']
        if version.status == TradeInRuleSetVersion.STATUS_DRAFT and version.valid_from is None:
            version.valid_from = tariff.company_today(company)   # a withdrawn set still has a (never used) window
            fields.append('valid_from')
        version.status = TradeInRuleSetVersion.STATUS_WITHDRAWN
        version.decided_by, version.decided_at, version.decision_note = actor, timezone.now(), note
        version.save(update_fields=fields)
        _version_audit('trade_in_rule_set_withdrawn', actor=actor, company=company, version=version,
                       request=request, note=note)
    return version


# ---------------------------------------------------------------------------
# Calculation inputs and preview
# ---------------------------------------------------------------------------

def inputs_of(company, version) -> tuple[dict, dict, list]:
    """(groups, factors, rules) for `trade_in_valuation.calculate`: the frozen snapshot of a proposed or decided
    version; the live groups and factors of a draft."""
    snap = version.snapshot if version.status != TradeInRuleSetVersion.STATUS_DRAFT else snapshot_of(company, version)
    groups = {key: valuation.Group(key=key, kind=g['kind'], name=g['name'], combine=g['combine'],
                                   cap_percent=None if g['cap_percent'] is None else Decimal(g['cap_percent']))
              for key, g in (snap.get('groups') or {}).items()}
    factors = {code: valuation.Factor(code=code, group=f['group'], label=f['label'], input=f['input'],
                                      component=f['component'], options=tuple(f['options']),
                                      applies_brand=f['applies_brand'], applies_model_name=f['applies_model_name'],
                                      applies_product_model=f['applies_product_model'])
               for code, f in (snap.get('factors') or {}).items()}
    codes = {f['id']: code for code, f in (snap.get('factors') or {}).items()}
    rules = [valuation.Rule(id=rule.pk, factor=codes.get(rule.factor_id, ''), effect=rule.effect,
                            when_outcome=rule.when_outcome, when_percent_gte=rule.when_percent_gte,
                            when_percent_lt=rule.when_percent_lt, amount=rule.amount, percent=rule.percent,
                            priority=rule.priority)
             for rule in version.rules.all()]
    return groups, factors, rules


def preview(*, actor, company, data) -> dict:
    """
    «Probar un caso»: the valuation of a device of the tariff with these answers, by the rules in force (or by a draft
    or pending version named in `rule_set_version`). Writes nothing.
    """
    _require(actor, company, CAP_VIEW, 'No tienes permiso para ver las reglas de valorización.')
    try:
        entry = TradeInTariffEntry.objects.filter(company=company, pk=int(data.get('tariff_entry'))).first()
    except (TypeError, ValueError):
        entry = None
    if entry is None:
        raise TariffError('El equipo del tarifario no existe.', code='not_found', field='tariff_entry')
    today = tariff.company_today(company)
    tariff_version = tariff.effective_version(entry, on=today)
    if tariff_version is None:
        raise TariffStateError('Ese equipo no tiene precio en vigor hoy.', code='tariff_missing')
    raw = data.get('rule_set_version')
    if raw in (None, ''):
        rules_version = effective_rule_set(company, on=today)
        if rules_version is None:
            raise TariffStateError('No hay reglas de valorización en vigor hoy.', code='rules_missing')
    else:
        try:
            rules_version = TradeInRuleSetVersion.objects.filter(company=company, pk=int(raw)).first()
        except (TypeError, ValueError):
            rules_version = None
        if rules_version is None:
            raise TariffError('La versión de reglas no existe.', code='not_found', field='rule_set_version')
    answers = data.get('answers') or {}
    if not isinstance(answers, dict):
        raise TariffError('Las respuestas son un objeto {pregunta: respuesta}.', field='answers')
    groups, factors, rules = inputs_of(company, rules_version)
    try:
        result = valuation.calculate(
            base=tariff_version.base_reception_value, maximum=tariff_version.max_reception_value, answers=answers,
            groups=groups, factors=factors, rules=rules,
            device=valuation.Device(brand=entry.brand, model_name=entry.model_name,
                                    product_model=entry.product_model_id))
    except valuation.ValuationError as exc:
        raise TariffError(str(exc), code='invalid_answer', field=exc.field) from None
    result['tariff'] = {'entry': entry.pk, 'version': tariff_version.pk, 'number': tariff_version.number,
                        'currency': tariff_version.currency}
    result['rules'] = {'version': rules_version.pk, 'number': rules_version.number, 'status': rules_version.status}
    return result
