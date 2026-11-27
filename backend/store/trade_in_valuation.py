"""
TRADE-IN VALUATION PRO · Phase 2 — the valuation calculation (docs/adr-trade-in-valuation.md §6).

PURE: plain data in, plain data out; it reads no table and writes nothing. The services hand it the tariff version's
base and maximum, the answers, and the groups, factors and rules of ONE rule-set version (as frozen in its `snapshot`
once proposed), so the same inputs always give the same result.

THE ORDER (§6):
  3. per answered factor, the rules whose condition matches and whose factor applies to the device:
       * every STATUS rule applies (requires approval, pending verification, not eligible), whatever its priority;
       * ONE money rule per factor: the highest priority (tie: the larger deduction, then the lower id);
  4. each deduction on the BASE value (never compounded), rounded to 0.01 half-up;
  5. no double penalty, in this fixed order: (1) per component, before the groups, by line amount; (2) per group,
     `max` keeps the largest, `sum` adds; (3) the group's cap; (4) never more than the base;
  6. computed value = base − deductions, never negative (deductions only: never above the base);
  8. eligibility from the flags; NOT ELIGIBLE has no value at all (null), never zero «on account».

Blockers and identity checks are phase 3: here eligibility comes from the rules only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from .configuration_resolver import fold

CENT = Decimal('0.01')
HUNDRED = Decimal('100')

ELIGIBLE = 'eligible'
ELIGIBLE_WITH_DEDUCTIONS = 'eligible_with_deductions'
REQUIRES_APPROVAL = 'requires_approval'
PENDING_VERIFICATION = 'pending_verification'
NOT_ELIGIBLE = 'not_eligible'
ELIGIBILITY_LABELS = {
    ELIGIBLE: 'APTO',
    ELIGIBLE_WITH_DEDUCTIONS: 'APTO CON DESCUENTOS',
    REQUIRES_APPROVAL: 'REQUIERE APROBACIÓN',
    PENDING_VERIFICATION: 'PENDIENTE DE VERIFICACIÓN',
    NOT_ELIGIBLE: 'NO ELEGIBLE',
}

MONEY_EFFECTS = ('none', 'fixed', 'percent', 'repair_cost')
STATUS_EFFECTS = ('requires_approval', 'pending_verification', 'not_eligible')


class ValuationError(ValueError):
    """An answer the factors do not accept. `field` names the factor code."""

    def __init__(self, message: str, *, field: str = ''):
        super().__init__(message)
        self.field = field


@dataclass(frozen=True)
class Group:
    key: str
    kind: str
    name: str
    combine: str = 'max'
    cap_percent: Decimal | None = None


@dataclass(frozen=True)
class Factor:
    code: str
    group: str
    label: str
    input: str
    component: str = ''
    options: tuple = ()
    applies_brand: str = ''
    applies_model_name: str = ''
    applies_product_model: int | None = None


@dataclass(frozen=True)
class Rule:
    id: int
    factor: str
    effect: str
    when_outcome: str = ''
    when_percent_gte: Decimal | None = None
    when_percent_lt: Decimal | None = None
    amount: Decimal | None = None
    percent: Decimal | None = None
    priority: int = 0


@dataclass(frozen=True)
class Device:
    brand: str = ''
    model_name: str = ''
    product_model: int | None = None


@dataclass
class _Line:
    factor: str
    group: str
    component: str
    outcome: str
    rule_id: int
    effect: str
    amount: Decimal
    suppressed_by: str = ''
    notes: list = field(default_factory=list)


def _cents(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def applies(factor: Factor, device: Device) -> bool:
    """Whether the factor is asked of this device (its optional filters, folded)."""
    if factor.applies_product_model is not None and factor.applies_product_model != device.product_model:
        return False
    if factor.applies_brand and fold(factor.applies_brand) != fold(device.brand):
        return False
    if factor.applies_model_name and fold(factor.applies_model_name) != fold(device.model_name):
        return False
    return True


CHECK_OUTCOMES = ('works', 'detail', 'not_working', 'replaced', 'not_verified', 'not_applicable')


def normalise_answer(factor: Factor, raw):
    """The answer as the rules compare it: an outcome text, or a Decimal percentage for `percent`."""
    if factor.input == 'percent':
        try:
            value = Decimal(str(raw).strip())
        except Exception:  # noqa: BLE001 — any unreadable value is the same refusal
            raise ValuationError(f'«{factor.label}» es un porcentaje de 0 a 100.', field=factor.code) from None
        if not value.is_finite() or value < 0 or value > HUNDRED:
            raise ValuationError(f'«{factor.label}» es un porcentaje de 0 a 100.', field=factor.code)
        return value
    if factor.input == 'boolean':
        if isinstance(raw, bool):
            return 'true' if raw else 'false'
        text = str(raw).strip().lower()
        if text not in ('true', 'false'):
            raise ValuationError(f'«{factor.label}» es sí o no.', field=factor.code)
        return text
    text = str(raw if raw is not None else '').strip()
    if factor.input == 'check' and text not in CHECK_OUTCOMES:
        raise ValuationError(f'«{factor.label}»: resultado no reconocido.', field=factor.code)
    if factor.input == 'choice' and text not in factor.options:
        raise ValuationError(f'«{factor.label}»: opción no reconocida.', field=factor.code)
    return text


def matches(rule: Rule, answer) -> bool:
    if isinstance(answer, Decimal):
        if rule.when_outcome:
            return False
        if rule.when_percent_gte is not None and answer < rule.when_percent_gte:
            return False
        if rule.when_percent_lt is not None and answer >= rule.when_percent_lt:
            return False
        return rule.when_percent_gte is not None or rule.when_percent_lt is not None
    if rule.when_percent_gte is not None or rule.when_percent_lt is not None:
        return False
    return bool(rule.when_outcome) and rule.when_outcome == answer


def deduction(rule: Rule, base: Decimal) -> Decimal:
    if rule.effect in ('fixed', 'repair_cost'):
        return _cents(rule.amount or Decimal('0'))
    if rule.effect == 'percent':
        return _cents(base * (rule.percent or Decimal('0')) / HUNDRED)
    return Decimal('0.00')


def _money_rule(candidates: list[Rule], base: Decimal) -> Rule | None:
    """One per factor: the highest priority; tie → the larger deduction; then the lower id."""
    if not candidates:
        return None
    return sorted(candidates, key=lambda r: (-r.priority, -deduction(r, base), r.id))[0]


def calculate(*, base: Decimal, maximum: Decimal, answers: dict, groups: dict[str, Group],
              factors: dict[str, Factor], rules: list[Rule], device: Device) -> dict:
    """
    The valuation of one device. `answers` maps factor codes to raw answers; an unknown code or an unreadable answer
    raises `ValuationError`. Returns lines (applied and suppressed), status rules, totals, flags and eligibility.
    """
    base = _cents(Decimal(base))
    maximum = _cents(Decimal(maximum))
    by_factor: dict[str, list[Rule]] = {}
    for rule in rules:
        by_factor.setdefault(rule.factor, []).append(rule)

    lines: list[_Line] = []
    status_rules: list[dict] = []
    flags: set[str] = set()
    for code, raw in answers.items():
        factor = factors.get(code)
        if factor is None:
            raise ValuationError(f'La pregunta «{code}» no existe en estas reglas.', field=code)
        if not applies(factor, device):
            continue
        answer = normalise_answer(factor, raw)
        matching = [rule for rule in by_factor.get(code, []) if matches(rule, answer)]
        for rule in matching:
            if rule.effect in STATUS_EFFECTS:
                flags.add(rule.effect)
                status_rules.append({'factor': code, 'rule_id': rule.id, 'effect': rule.effect,
                                     'outcome': str(answer)})
        chosen = _money_rule([rule for rule in matching if rule.effect in MONEY_EFFECTS], base)
        if chosen is not None:
            lines.append(_Line(factor=code, group=factor.group, component=fold(factor.component),
                               outcome=str(answer), rule_id=chosen.id, effect=chosen.effect,
                               amount=deduction(chosen, base)))

    # 5.1 — per component, before the groups: only the largest line of a component counts.
    by_component: dict[str, _Line] = {}
    for line in sorted(lines, key=lambda l: (-l.amount, l.rule_id)):
        if not line.component or line.amount <= 0:
            continue
        keeper = by_component.setdefault(line.component, line)
        if keeper is not line:
            line.suppressed_by = keeper.factor
    counted = [line for line in lines if not line.suppressed_by and line.amount > 0]

    # 5.2 / 5.3 — per group: max or sum, then the group's cap.
    group_totals: dict[str, Decimal] = {}
    for key in sorted({line.group for line in counted}):
        group = groups.get(key)
        members = sorted((line for line in counted if line.group == key), key=lambda l: (-l.amount, l.rule_id))
        if group is not None and group.combine == 'sum':
            total = sum((line.amount for line in members), Decimal('0.00'))
        else:
            total = members[0].amount
            for line in members[1:]:
                line.suppressed_by = members[0].factor
        if group is not None and group.cap_percent is not None:
            total = min(total, _cents(base * group.cap_percent / HUNDRED))
        group_totals[key] = total

    # 5.4 — never more than the base.
    deductions = min(sum(group_totals.values(), Decimal('0.00')), base)
    computed = max(Decimal('0.00'), base - deductions)

    if 'not_eligible' in flags:
        eligibility = NOT_ELIGIBLE
    elif 'pending_verification' in flags:
        eligibility = PENDING_VERIFICATION
    elif 'requires_approval' in flags:
        eligibility = REQUIRES_APPROVAL
    elif deductions > 0:
        eligibility = ELIGIBLE_WITH_DEDUCTIONS
    else:
        eligibility = ELIGIBLE
    value = None if eligibility == NOT_ELIGIBLE else computed

    return {
        'lines': [{'factor': l.factor, 'group': l.group, 'component': l.component, 'outcome': l.outcome,
                   'rule_id': l.rule_id, 'effect': l.effect, 'amount': str(l.amount),
                   'suppressed_by': l.suppressed_by or None} for l in lines],
        'status_rules': status_rules,
        'groups': {key: str(total) for key, total in group_totals.items()},
        'totals': {'base': str(base), 'maximum': str(maximum), 'deductions': str(deductions),
                   'computed_value': None if value is None else str(value)},
        'flags': sorted(flags),
        'needs_approval': 'requires_approval' in flags,
        'eligibility': eligibility,
        'eligibility_label': ELIGIBILITY_LABELS[eligibility],
    }
