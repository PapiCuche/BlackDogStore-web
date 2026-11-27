"""
TRADE-IN VALUATION PRO · Phase 4 — the assessment and its calculation (docs/adr-trade-in-valuation.md §3.4, §5.1,
§5.2, §6, §8.1).

    draft ──calculate──▶ calculated ──(edit)──▶ draft
    calculated ──approve (when it needs it)──▶ approved
    draft|calculated ──cancel──▶ cancelled          («quoted» is phase 5)

WHAT A CALCULATION IS, IN THIS ORDER (§6, §6.4):
  1. the SECURITY verdict of the device check (phase 3) — read first, never turned into a deduction;
  2. the tariff version in force today in the company's calendar (`tariff_missing` otherwise) and the rule set in
     force (`rules_missing`), frozen in the assessment;
  3. EVERY active question of that rule set that applies to the device must be answered (`unanswered` otherwise: an
     unanswered question is never «no deduction»);
  4. `trade_in_valuation.calculate` (pure) on the frozen rules;
  5. the overall eligibility: NOT ELIGIBLE (security or rules) has NO value; PENDING VERIFICATION keeps a provisional
     value; a rule asking for approval, or an offer raised above the computed value (never above the tariff's
     maximum), needs an approver;
  6. trading towards another device: the price copied now and the difference shown — never a discount, never IGV,
     never a credit.

Approving re-checks that the tariff and rule versions used are STILL the ones in force (`calculation_stale`) and that
the device check is still clear. Whoever calculated an assessment does not approve it (`approve_own` arrives with the
quotes, phase 5a). Nothing here writes stock, a payable, a payment or a fiscal document.
"""
from __future__ import annotations

import hashlib
from decimal import Decimal

from django.db import IntegrityError, connection, transaction
from django.utils import timezone

from . import trade_in_eligibility as eligibility
from . import trade_in_rules as rules
from . import trade_in_tariff as tariff
from . import trade_in_valuation as valuation
from .models import (
    AdminAuditLog,
    Customer,
    Product,
    ProductModel,
    SalesQuote,
    TradeInAssessment,
    TradeInAssessmentAnswer,
    TradeInDeviceCheck,
    TradeInTariffEntry,
)
from .tenancy import has_capability, visible_branches

CAP_VIEW = eligibility.CAP_VIEW
CAP_VALUE = eligibility.CAP_VALUE
CAP_APPROVE = 'sales.tradein.approve'
CAP_QUOTES_MANAGE = 'sales.quotes.manage'

TariffError = tariff.TariffError
TariffPermissionError = tariff.TariffPermissionError
TariffStateError = tariff.TariffStateError
TariffConflict = tariff.TariffConflict

NOT_ELIGIBLE = 'not_eligible'
PENDING_VERIFICATION = 'pending_verification'
REQUIRES_APPROVAL = 'requires_approval'
LABELS = dict(valuation.ELIGIBILITY_LABELS)


def _require(actor, company, capability, message):
    if not has_capability(actor, company, capability):
        raise TariffPermissionError(message)


def _audit(*, actor, company, action, assessment, request=None, **metadata):
    AdminAuditLog.log(actor=actor if getattr(actor, 'is_authenticated', False) else None, action=action,
                      target_type='trade_in_assessment', target_id=assessment.pk,
                      metadata={'company_id': company.pk, 'assessment_id': assessment.pk,
                                'number': assessment.label, 'status': assessment.status, **metadata},
                      request=request, company=company)


def _id(raw, field, label):
    if raw in (None, ''):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise TariffError(f'{label} no válido.', field=field) from None


# ---------------------------------------------------------------------------
# §8.1 — from the device selector to the tariff
# ---------------------------------------------------------------------------

def _parts(*words) -> list[str]:
    """The five folded words, by the tariff's own folding (`device_key`)."""
    return tariff.device_key(*words).split('|')


def match_tariff(company, *, product_model=None, capacity='', sim='', region=''):
    """
    The ACTIVE tariff entry for a model and its options (colour never counts), in this order, the first level with
    results winning: (1) linked to the model, exact capacity/SIM/region; (2) linked, SIM/region empty in the entry as
    «does not distinguish»; (3)/(4) the same two passes by the model's folded name, for entries not linked. One →
    it; several → `tariff_ambiguous` (409, never chosen in silence); none → `tariff_missing` (409).
    """
    model_id = _id(product_model, 'product_model', 'Modelo')
    model = ProductModel.objects.filter(company=company, pk=model_id).first() if model_id else None
    if model is None:
        raise TariffError('El modelo no existe.', code='not_found', field='product_model')
    _b, _m, cap, s, r = _parts('', '', capacity, sim, region)
    folded_name = _parts('', model.name)[1]
    active = list(TradeInTariffEntry.objects.filter(company=company, is_active=True))

    def options_match(entry, wildcard):
        _eb, _em, ecap, esim, ereg = entry.device_key.split('|')
        if ecap != cap:
            return False
        if wildcard:
            return esim in ('', s) and ereg in ('', r)
        return esim == s and ereg == r

    levels = (
        lambda e, w=False: e.product_model_id == model.pk and options_match(e, w),
        lambda e, w=True: e.product_model_id == model.pk and options_match(e, w),
        lambda e, w=False: e.product_model_id is None and e.device_key.split('|')[1] == folded_name and options_match(e, w),
        lambda e, w=True: e.product_model_id is None and e.device_key.split('|')[1] == folded_name and options_match(e, w),
    )
    for test in levels:
        found = [entry for entry in active if test(entry)]
        if len(found) == 1:
            return found[0]
        if len(found) > 1:
            raise TariffConflict('Hay varios equipos del tarifario que podrían ser éste: elígelo en el tarifario.',
                                 code='tariff_ambiguous', extra={'entries': sorted(e.pk for e in found)})
    raise TariffStateError('Ese equipo no está en el tarifario: pide su precio a quien lo administra.',
                           code='tariff_missing')


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

def _next_number(company) -> int:
    if connection.vendor == 'postgresql':
        key = int.from_bytes(hashlib.sha256(f'trade-in-assessment-number|{company.pk}'.encode()).digest()[:8],
                             'big', signed=True)
        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_advisory_xact_lock(%s)', [key])
    last = (TradeInAssessment.objects.filter(company=company).order_by('-number')
            .values_list('number', flat=True).first())
    return (last or 0) + 1


def _contributed(assessment, actor) -> None:
    pk = getattr(actor, 'pk', None)
    if pk is not None and pk not in (assessment.contributor_ids or []):
        assessment.contributor_ids = [*(assessment.contributor_ids or []), pk]


def _authors(assessment) -> set:
    """Everyone who shaped this valuation: none of them approves it."""
    answered = set(assessment.answers.exclude(answered_by=None).values_list('answered_by_id', flat=True))
    return {pk for pk in (assessment.created_by_id, assessment.calculated_by_id, *(assessment.contributor_ids or []),
                          *answered) if pk}


def _check_target_product(product) -> None:
    from . import pos_services

    if not product.is_active:
        raise TariffStateError(f'«{product.name}» está desactivado en el catálogo.', code='product_inactive',
                               field='target_product')
    if pos_services.priced_by_unit(product):
        # A used device is priced unit by unit at the till: naming the unit is a later step (`target_unit`).
        raise TariffError('Un seminuevo se cotiza por equipo: elige un producto con precio propio o enlaza su '
                          'cotización de venta.', code='target_unit_unsupported', field='target_product')


def _check_target_quote(quote, branch) -> None:
    if quote.branch_id != branch.pk:
        raise TariffError('La cotización de venta es de otra sucursal.', code='sales_quote_other_branch',
                          field='target_sales_quote')
    if quote.status not in SalesQuote.LIVE_STATUSES or quote.is_expired:
        raise TariffStateError('La cotización de venta ya no está vigente: elige otra o el producto.',
                               code='sales_quote_not_live', field='target_sales_quote')


def _targets(actor, company, data, purpose, branch) -> dict:
    product_id = _id(data.get('target_product'), 'target_product', 'Producto')
    quote_id = _id(data.get('target_sales_quote'), 'target_sales_quote', 'Cotización')
    if purpose != TradeInAssessment.PURPOSE_TRADE_TOWARDS:
        if product_id or quote_id:
            raise TariffError('Sólo una parte de pago lleva el equipo que se compra.', field='purpose')
        return {'target_product': None, 'target_sales_quote': None}
    if bool(product_id) == bool(quote_id):
        raise TariffError('Indica el producto que compra el cliente o su cotización de venta (uno de los dos).',
                          code='required', field='target_product')
    if product_id:
        product = Product.objects.filter(company=company, pk=product_id).first()
        if product is None:
            raise TariffError('No encontrado.', code='not_found', field='target_product')
        _check_target_product(product)
        return {'target_product': product, 'target_sales_quote': None}
    _require(actor, company, CAP_QUOTES_MANAGE, 'Enlazar una cotización de venta pide elaborar cotizaciones.')
    quote = SalesQuote.objects.filter(company=company, pk=quote_id,
                                      branch__in=visible_branches(actor, company)).first()
    if quote is None:
        raise TariffError('No encontrado.', code='not_found', field='target_sales_quote')
    _check_target_quote(quote, branch)
    return {'target_product': None, 'target_sales_quote': quote}


def _customer(company, raw):
    pk = _id(raw, 'customer', 'Cliente')
    if pk is None:
        return None
    customer = Customer.objects.filter(company=company, pk=pk).first()
    if customer is None:
        raise TariffError('No encontrado.', code='not_found', field='customer')
    return customer


def create_assessment(*, actor, company, data, request=None) -> TradeInAssessment:
    """Open an assessment on an OPEN device check of a reachable branch, for one tariff entry."""
    _require(actor, company, CAP_VALUE, 'No tienes permiso para valorizar equipos usados.')
    check_id = _id(data.get('device_check'), 'device_check', 'Comprobación')
    check = (TradeInDeviceCheck.objects.filter(company=company, pk=check_id,
                                               branch__in=visible_branches(actor, company)).first()
             if check_id else None)
    if check is None:
        raise TariffError('Primero comprueba el equipo (identidad y bloqueos).', code='not_found',
                          field='device_check')
    if check.status != TradeInDeviceCheck.STATUS_OPEN:
        raise TariffStateError('Esa comprobación está cerrada.', code='check_closed')
    entry_id = _id(data.get('tariff_entry'), 'tariff_entry', 'Equipo del tarifario')
    if entry_id:
        entry = TradeInTariffEntry.objects.filter(company=company, pk=entry_id).first()
        if entry is None:
            raise TariffError('No encontrado.', code='not_found', field='tariff_entry')
        if not entry.is_active:
            raise TariffStateError('Ese equipo del tarifario está desactivado.', code='tariff_missing')
    else:
        entry = match_tariff(company, product_model=data.get('product_model'), capacity=data.get('capacity') or '',
                             sim=data.get('sim') or '', region=data.get('region') or '')
    purpose = str(data.get('purpose') or TradeInAssessment.PURPOSE_VALUATION)
    if purpose not in dict(TradeInAssessment.PURPOSE_CHOICES):
        raise TariffError('Propósito no válido.', field='purpose')
    targets = _targets(actor, company, data, purpose, check.branch)
    customer = _customer(company, data.get('customer'))
    with transaction.atomic():
        try:
            with transaction.atomic():
                assessment = TradeInAssessment.objects.create(
                    company=company, branch=check.branch, number=_next_number(company), purpose=purpose,
                    device_check=check, customer=customer, tariff_entry=entry, brand=entry.brand,
                    model_name=entry.model_name, capacity=entry.capacity, sim=entry.sim, region=entry.region,
                    contributor_ids=[actor.pk] if getattr(actor, 'pk', None) else [],
                    created_by=actor if getattr(actor, 'is_authenticated', False) else None, **targets)
        except IntegrityError:
            raise TariffConflict('Esa comprobación ya tiene una evaluación en curso.', code='check_in_use') from None
        _audit(actor=actor, company=company, action='trade_in_assessment_created', assessment=assessment,
               request=request, device_check=check.pk, tariff_entry=entry.pk, purpose=purpose)
    return assessment


def _editable(company, assessment) -> TradeInAssessment:
    if assessment.company_id != company.pk:
        raise TariffError('No encontrado.', code='not_found')
    locked = TradeInAssessment.objects.select_for_update(of=('self',)).get(pk=assessment.pk)
    if locked.status not in TradeInAssessment.EDITABLE:
        raise TariffStateError('Esta evaluación ya no se modifica.', code='not_editable')
    return locked


_CALCULATION_FIELDS = ('tariff_version', 'rule_set_version', 'calculation', 'eligibility', 'computed_value',
                       'offered_value', 'adjustment_reason', 'needs_approval', 'target_price', 'difference',
                       'calculated_by', 'calculated_at')


def _back_to_draft(assessment) -> list[str]:
    """An edit invalidates the calculation: back to draft, nothing computed left behind."""
    if assessment.status == TradeInAssessment.STATUS_DRAFT:
        return []
    assessment.status = TradeInAssessment.STATUS_DRAFT
    assessment.tariff_version = assessment.rule_set_version = None
    assessment.calculation, assessment.eligibility = {}, ''
    assessment.computed_value = assessment.offered_value = assessment.target_price = assessment.difference = None
    assessment.adjustment_reason, assessment.needs_approval = '', False
    assessment.calculated_by = assessment.calculated_at = None
    return ['status', *_CALCULATION_FIELDS]


def update_details(*, actor, company, assessment, data, request=None) -> TradeInAssessment:
    """Customer, purpose and the device bought. Any change sends a calculated assessment back to draft."""
    _require(actor, company, CAP_VALUE, 'No tienes permiso para valorizar equipos usados.')
    with transaction.atomic():
        assessment = _editable(company, assessment)
        fields = []
        if 'customer' in data:
            assessment.customer = _customer(company, data.get('customer'))
            fields.append('customer')
        if {'purpose', 'target_product', 'target_sales_quote'} & set(data):
            purpose = str(data.get('purpose') or assessment.purpose)
            if purpose not in dict(TradeInAssessment.PURPOSE_CHOICES):
                raise TariffError('Propósito no válido.', field='purpose')
            for key, value in _targets(actor, company, data, purpose, assessment.branch).items():
                setattr(assessment, key, value)
            assessment.purpose = purpose
            fields += ['purpose', 'target_product', 'target_sales_quote']
        if not fields:
            raise TariffError('Nada que cambiar.')
        _contributed(assessment, actor)
        fields += ['contributor_ids', *_back_to_draft(assessment)]
        assessment.save(update_fields=[*fields, 'updated_at'])
        _audit(actor=actor, company=company, action='trade_in_assessment_updated', assessment=assessment,
               request=request, fields=sorted(set(fields) - set(_CALCULATION_FIELDS) - {'status'}))
    return assessment


def questions(company, assessment, version=None) -> list[dict]:
    """The questions this device must answer: the active ones of `version` (the rule set in force by default) that
    apply to it."""
    version = version or rules.effective_rule_set(company)
    if version is None:
        raise TariffStateError('No hay reglas de valorización en vigor hoy.', code='rules_missing')
    _groups, factors, _rules = rules.inputs_of(company, version)
    snap = version.snapshot or {}
    device = _device(assessment)
    out = []
    for code, factor in factors.items():
        meta = snap['factors'][code]
        if not (meta.get('is_active', True) and snap['groups'][meta['group']].get('is_active', True)):
            continue
        if not valuation.applies(factor, device):
            continue
        out.append({'code': code, 'label': factor.label, 'input': factor.input, 'options': list(factor.options),
                    'group': snap['groups'][meta['group']]['name'], 'requires_photo': meta.get('requires_photo', False),
                    'sort_order': meta.get('sort_order', 0)})
    return sorted(out, key=lambda q: (q['sort_order'], q['label']))


def _device(assessment) -> valuation.Device:
    return valuation.Device(brand=assessment.brand, model_name=assessment.model_name,
                            product_model=assessment.tariff_entry.product_model_id)


def set_answers(*, actor, company, assessment, data, request=None) -> TradeInAssessment:
    """Answers by question code ({code: value}); an empty value removes one. Back to draft if it was calculated."""
    _require(actor, company, CAP_VALUE, 'No tienes permiso para valorizar equipos usados.')
    answers = data.get('answers')
    if not isinstance(answers, dict) or not answers:
        raise TariffError('Las respuestas son un objeto {pregunta: respuesta}.', field='answers')
    from .models import TradeInFactor

    factors = {f.code: f for f in TradeInFactor.objects.filter(company=company, code__in=list(answers))}
    with transaction.atomic():
        assessment = _editable(company, assessment)
        for code, raw in answers.items():
            factor = factors.get(code)
            if factor is None:
                raise TariffError(f'La pregunta «{code}» no existe.', code='unknown_question', field='answers')
            existing = TradeInAssessmentAnswer.objects.filter(assessment=assessment, factor=factor).first()
            if raw in (None, ''):
                if existing:
                    existing.delete()
                continue
            shape = valuation.Factor(code=code, group='', label=factor.label, input=factor.input,
                                     options=tuple(factor.options or ()))
            try:
                value = valuation.normalise_answer(shape, raw)
            except valuation.ValuationError as exc:
                raise TariffError(str(exc), code='invalid_answer', field='answers') from None
            row = existing or TradeInAssessmentAnswer(company=company, assessment=assessment, factor=factor)
            row.value, row.answered_by = str(value), actor
            row.save()
        _contributed(assessment, actor)
        fields = ['contributor_ids', *_back_to_draft(assessment)]
        assessment.save(update_fields=[*fields, 'updated_at'])
        _audit(actor=actor, company=company, action='trade_in_assessment_answered', assessment=assessment,
               request=request, questions=sorted(answers))
    return assessment


def _security(check) -> dict:
    verdict = eligibility.evaluate(check)
    return {'eligibility': verdict['eligibility'], 'blocked': verdict['blocked'],
            'unverified': verdict['unverified'], 'unchecked_identifiers': verdict['unchecked_identifiers']}


def _target_price(actor, company, assessment) -> Decimal | None:
    """What the customer would pay for the device bought, NOW: the till's own pricing for this branch (promotions
    included), or the total of a live sales quote of this branch."""
    if assessment.purpose != TradeInAssessment.PURPOSE_TRADE_TOWARDS:
        return None
    if assessment.target_product_id:
        from . import sales_quote_services

        product = Product.objects.get(pk=assessment.target_product_id)
        _check_target_product(product)
        try:
            priced = sales_quote_services.price_lines(
                actor=actor, company=company, branch=assessment.branch,
                lines=[{'product': product.pk, 'quantity': 1, 'on_order': False, 'supply_reason': '', 'unit': None}],
                may_order_over_stock=True)
        except sales_quote_services.QuoteError as exc:
            raise TariffStateError(str(exc), code=exc.code or 'target_price', field='target_product') from None
        return Decimal(priced['total']).quantize(Decimal('0.01'))
    quote = assessment.target_sales_quote
    _check_target_quote(quote, assessment.branch)
    return quote.total


def calculate(*, actor, company, assessment, data=None, request=None) -> TradeInAssessment:
    """Compute and freeze the valuation (module docstring, steps 1-6)."""
    _require(actor, company, CAP_VALUE, 'No tienes permiso para valorizar equipos usados.')
    data = data or {}
    today = tariff.company_today(company)
    with transaction.atomic():
        assessment = _editable(company, assessment)
        check = TradeInDeviceCheck.objects.select_for_update(of=('self',)).get(pk=assessment.device_check_id)
        if check.status != TradeInDeviceCheck.STATUS_OPEN:
            raise TariffStateError('La comprobación del equipo está cerrada.', code='check_closed')
        if tariff.policy_of(company).require_identified_customer and not assessment.customer_id:
            raise TariffError('La empresa pide identificar al cliente antes de valorizar.', code='customer_required',
                              field='customer')
        security = _security(check)
        tariff_version = tariff.effective_version(assessment.tariff_entry, on=today)
        if tariff_version is None:
            raise TariffStateError('Ese equipo no tiene precio en vigor hoy.', code='tariff_missing')
        rules_version = rules.effective_rule_set(company, on=today)
        if rules_version is None:
            raise TariffStateError('No hay reglas de valorización en vigor hoy.', code='rules_missing')
        groups, factors, rule_list = rules.inputs_of(company, rules_version)
        asked = {q['code'] for q in questions(company, assessment, rules_version)}
        given = {a.factor.code: a.value for a in assessment.answers.select_related('factor')}
        missing = sorted(asked - set(given))
        if missing:
            raise TariffError('Responde todas las preguntas antes de calcular.', code='unanswered',
                              extra={'questions': missing})
        ignored = sorted(set(given) - asked)
        try:
            result = valuation.calculate(
                base=tariff_version.base_reception_value, maximum=tariff_version.max_reception_value,
                answers={code: given[code] for code in asked}, groups=groups, factors=factors, rules=rule_list,
                device=_device(assessment))
        except valuation.ValuationError as exc:
            raise TariffError(str(exc), code='invalid_answer', field=exc.field) from None
        computed = result['totals']['computed_value']
        computed = None if computed is None else Decimal(computed)
        if security['eligibility'] == eligibility.NOT_ELIGIBLE or result['eligibility'] == valuation.NOT_ELIGIBLE:
            overall = NOT_ELIGIBLE
        elif (security['eligibility'] == eligibility.PENDING_VERIFICATION
              or result['eligibility'] == valuation.PENDING_VERIFICATION):
            overall = PENDING_VERIFICATION
        else:
            overall = result['eligibility']
        offered, reason, raised = _offer(data, overall, computed, tariff_version.max_reception_value, check)
        needs_approval = overall != NOT_ELIGIBLE and (result['needs_approval'] or raised)
        if needs_approval and overall not in (NOT_ELIGIBLE, PENDING_VERIFICATION):
            overall = REQUIRES_APPROVAL
        if overall == NOT_ELIGIBLE:
            computed = offered = None
        price = _target_price(actor, company, assessment)
        difference = None if price is None or offered is None else max(Decimal('0.00'), price - offered)
        assessment.tariff_version, assessment.rule_set_version = tariff_version, rules_version
        # The snapshot says the FINAL word: the values and the verdict after security and the person's adjustment.
        totals = {**result['totals'], 'computed_value': None if computed is None else str(computed),
                  'offered_value': None if offered is None else str(offered)}
        assessment.calculation = {
            **result,
            'totals': totals,
            'rules_eligibility': result['eligibility'],
            'eligibility': overall,
            'eligibility_label': LABELS[overall],
            'needs_approval': needs_approval,
            'security': security,
            'tariff': {'version': tariff_version.pk, 'number': tariff_version.number,
                       'base': str(tariff_version.base_reception_value),
                       'maximum': str(tariff_version.max_reception_value), 'currency': tariff_version.currency},
            'rules': {'version': rules_version.pk, 'number': rules_version.number},
            'ignored_answers': ignored,
            'computed_on': today.isoformat(),
        }
        assessment.eligibility = overall
        assessment.computed_value, assessment.offered_value = computed, offered
        assessment.adjustment_reason, assessment.needs_approval = reason, needs_approval
        assessment.target_price, assessment.difference = price, difference
        assessment.calculated_by, assessment.calculated_at = actor, timezone.now()
        assessment.status = TradeInAssessment.STATUS_CALCULATED
        assessment.approved_by = assessment.approved_at = None
        _contributed(assessment, actor)
        assessment.save(update_fields=['status', *_CALCULATION_FIELDS, 'approved_by', 'approved_at',
                                       'contributor_ids', 'updated_at'])
        _audit(actor=actor, company=company, action='trade_in_assessment_calculated', assessment=assessment,
               request=request, eligibility=overall, tariff_version=tariff_version.pk,
               rule_set_version=rules_version.pk, computed=None if computed is None else str(computed),
               offered=None if offered is None else str(offered), needs_approval=needs_approval)
    return assessment


def _refuse_identifiers(text, field, check) -> None:
    eligibility.refuse_identifiers(text, field, company_id=check.company_id,
                                   digests=(check.imei_digest, check.imei2_digest, check.serial_digest),
                                   last4s=(check.imei_last4, check.imei2_last4))


def _offer(data, overall, computed, maximum, check) -> tuple[Decimal | None, str, bool]:
    """(offered, reason, raised): the computed value unless a person adjusts it, with a reason."""
    raw = data.get('offered_value')
    reason = ' '.join(str(data.get('adjustment_reason') or '').split())
    if len(reason) > 300:
        raise TariffError('El motivo admite hasta 300 caracteres.', field='adjustment_reason')
    _refuse_identifiers(reason, 'adjustment_reason', check)
    if raw in (None, ''):
        return computed, '', False
    if overall == NOT_ELIGIBLE or computed is None:
        raise TariffError('Un equipo no elegible no tiene valor que ajustar.', code='not_eligible',
                          field='offered_value')
    offered = tariff.parse_money(raw, 'el valor ofrecido', required=True)
    if offered == computed:
        return computed, '', False
    if not reason:
        raise TariffError('Indica por qué se ajusta el valor calculado.', code='reason_required',
                          field='adjustment_reason')
    if offered > maximum:
        raise TariffError('El valor ofrecido no puede superar el máximo autorizado del tarifario.',
                          code='above_maximum', field='offered_value', extra={'maximum': str(maximum)})
    return offered, reason, offered > computed


def approve(*, actor, company, assessment, note='', request=None) -> TradeInAssessment:
    """
    Approve a calculation that needs it. Refused when nothing needs approving, when the device is no longer clear,
    when the tariff or rule version used is no longer the one in force (`calculation_stale`), or when the approver
    calculated it (`own_change`).
    """
    _require(actor, company, CAP_APPROVE, 'No tienes permiso para aprobar valorizaciones.')
    note = ' '.join(str(note or '').split())
    if len(note) > 300:
        raise TariffError('La nota admite hasta 300 caracteres.', field='note')
    _refuse_identifiers(note, 'note', assessment.device_check)
    with transaction.atomic():
        if assessment.company_id != company.pk:
            raise TariffError('No encontrado.', code='not_found')
        assessment = TradeInAssessment.objects.select_for_update(of=('self',)).get(pk=assessment.pk)
        check = TradeInDeviceCheck.objects.select_for_update(of=('self',)).get(pk=assessment.device_check_id)
        if assessment.status != TradeInAssessment.STATUS_CALCULATED:
            raise TariffStateError('Sólo se aprueba una evaluación calculada.', code='not_calculated')
        if check.status != TradeInDeviceCheck.STATUS_OPEN:
            raise TariffStateError('La comprobación del equipo está cerrada.', code='check_closed')
        if getattr(actor, 'pk', None) in _authors(assessment):
            raise TariffPermissionError('Otra persona con permiso de aprobación tiene que aprobar esta valorización.',
                                        code='own_change')
        security = _security(check)
        frozen_security = (assessment.calculation or {}).get('security', {}).get('eligibility')
        if security['eligibility'] != eligibility.ELIGIBLE:
            raise TariffStateError('La comprobación del equipo no está limpia: revísala y vuelve a calcular.',
                                   code=security['eligibility'], extra={'blocked': security['blocked']})
        if frozen_security != eligibility.ELIGIBLE:
            raise TariffConflict('Se calculó cuando la comprobación no estaba limpia: vuelve a calcular.',
                                 code='calculation_stale', extra={'reasons': ['security']})
        if assessment.eligibility == PENDING_VERIFICATION:
            raise TariffStateError('Hay una verificación pendiente: complétala y vuelve a calcular.',
                                   code='pending_verification')
        if assessment.eligibility != REQUIRES_APPROVAL:
            raise TariffStateError('Esta valorización no necesita aprobación.', code='not_needed')
        stale = _stale(company, assessment)
        if stale:
            raise TariffConflict('La tarifa o las reglas con que se calculó ya no son las vigentes: vuelve a '
                                 'calcular.', code='calculation_stale', extra={'reasons': stale})
        assessment.status = TradeInAssessment.STATUS_APPROVED
        assessment.approved_by, assessment.approved_at, assessment.approval_note = actor, timezone.now(), note
        assessment.save(update_fields=['status', 'approved_by', 'approved_at', 'approval_note', 'updated_at'])
        _audit(actor=actor, company=company, action='trade_in_assessment_approved', assessment=assessment,
               request=request, offered=str(assessment.offered_value), has_note=bool(note))
    return assessment


def _stale(company, assessment) -> list[str]:
    """Why the frozen calculation is no longer the one today's tariff and rules would give (empty: it is)."""
    today = tariff.company_today(company)
    reasons = []
    current = tariff.effective_version(assessment.tariff_entry, on=today)
    if current is None or current.pk != assessment.tariff_version_id:
        reasons.append('tariff')
    rules_now = rules.effective_rule_set(company, on=today)
    if rules_now is None or rules_now.pk != assessment.rule_set_version_id:
        reasons.append('rules')
    return reasons


def cancel(*, actor, company, assessment, reason, request=None) -> TradeInAssessment:
    _require(actor, company, CAP_VALUE, 'No tienes permiso para valorizar equipos usados.')
    reason = ' '.join(str(reason or '').split())
    if not reason:
        raise TariffError('Indica por qué se anula.', code='reason_required', field='reason')
    if len(reason) > 300:
        raise TariffError('El motivo admite hasta 300 caracteres.', field='reason')
    check = assessment.device_check
    eligibility.refuse_identifiers(reason, 'reason', company_id=company.pk,
                                   digests=(check.imei_digest, check.imei2_digest, check.serial_digest),
                                   last4s=(check.imei_last4, check.imei2_last4))
    with transaction.atomic():
        if assessment.company_id != company.pk:
            raise TariffError('No encontrado.', code='not_found')
        assessment = TradeInAssessment.objects.select_for_update(of=('self',)).get(pk=assessment.pk)
        if assessment.status not in (*TradeInAssessment.EDITABLE, TradeInAssessment.STATUS_APPROVED):
            raise TariffStateError('Esta evaluación ya no se anula.', code='not_cancellable')
        assessment.status = TradeInAssessment.STATUS_CANCELLED
        assessment.cancelled_at, assessment.cancel_reason = timezone.now(), reason
        assessment.save(update_fields=['status', 'cancelled_at', 'cancel_reason', 'updated_at'])
        _audit(actor=actor, company=company, action='trade_in_assessment_cancelled', assessment=assessment,
               request=request, has_reason=True)
    return assessment
