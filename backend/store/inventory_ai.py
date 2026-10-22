"""
INV-SWITCH-01 — whether an inventory import may use AI, and why not.

AI is used only when ALL of these hold, checked in this order: the person asked
for it in this import, the platform allows it (`settings.INVENTORY_AI_ENABLED`,
off by default), the company turned it on (`InventoryAISettings`), the person
may run the import, and a provider is configured, up and within quota. The first
one missing is the reason, and nothing is sent anywhere.

The switch the panel shows is not the control: the server decides here, on
every import. AI only ever proposes; what is written goes through the same
services and checks as a manual import.

INV-AI-01 adds what any provider goes through (`extract`): redaction, a fixed
answer shape, per-person quotas, a monthly budget and a circuit breaker, every
call recorded in `InventoryAIUsage`. No real provider is built yet: the
registry is empty, so `get_provider` is None whatever is configured.
"""
from datetime import timedelta
from decimal import Decimal
from typing import NamedTuple, Optional

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from . import inventory_ai_safety as safety
from .models import InventoryAISettings, InventoryAIUsage

# Why AI is not used — one code each, in the order they are asked.
NOT_REQUESTED = 'not_requested'
GLOBAL_OFF = 'global_off'
COMPANY_OFF = 'company_off'
FORBIDDEN = 'forbidden'
NOT_CONFIGURED = 'not_configured'
UNAVAILABLE = 'unavailable'
QUOTA_EXHAUSTED = 'quota_exhausted'

# What the panel shows for a company.
STATE_DISABLED, STATE_ENABLED = 'disabled', 'enabled'
STATE_LABELS = {
    STATE_DISABLED: 'Desactivada',
    STATE_ENABLED: 'Activada',
    NOT_CONFIGURED: 'No configurada',
    UNAVAILABLE: 'Servicio no disponible',
    QUOTA_EXHAUSTED: 'Cuota agotada',
}


class Decision(NamedTuple):
    use_ai: bool
    reason: Optional[str]


#: name -> provider class. EMPTY: no real provider is built yet (INV-AI-02 adds
#: one, with its key from the environment, when the owner provides it).
_PROVIDERS: dict = {}

BREAKER_FAILURES = 3
BREAKER_WINDOW = timedelta(minutes=10)
BUDGET_ALERTS = (50, 80, 100)
#: The most a usage row can record (DecimalField max_digits=8, decimal_places=4).
MAX_RECORDED_COST = Decimal('9999.9999')


def get_provider():
    """
    The configured AI provider, or None — None while the registry is empty, so
    no request ever leaves the server. A provider answers `is_available()`,
    `has_quota(company, user)` and `extract(text, timeout=...) -> (answer, cost_usd)`.
    """
    provider = _PROVIDERS.get(getattr(settings, 'INVENTORY_AI_PROVIDER', '') or '')
    return provider() if provider else None


def _month_start(now):
    """The first instant of this month in the shop's time zone (Lima), not UTC."""
    return timezone.localtime(now).replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _money_setting(name: str) -> Optional[Decimal]:
    """A finite, non-negative amount from settings, or None: an unreadable value fails CLOSED (no AI)."""
    from decimal import InvalidOperation

    try:
        value = Decimal(str(getattr(settings, name, None)))
    except (InvalidOperation, TypeError, ValueError):
        return None
    # Bounded by what a usage row can hold (DecimalField 8,4): anything else fails closed.
    return value if value.is_finite() and 0 <= value <= MAX_RECORDED_COST else None


def _count_setting(name: str) -> Optional[int]:
    """A whole, non-negative count from settings, or None (fails closed)."""
    value = getattr(settings, name, None)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def budget_status(company) -> dict:
    """This month's spend against the budget, and the highest alert level reached (50, 80, 100 or None)."""
    budget = _money_setting('INVENTORY_AI_MONTHLY_BUDGET_USD')
    spent = InventoryAIUsage.objects.filter(company=company, at__gte=_month_start(timezone.now())) \
        .aggregate(total=Sum('cost_usd'))['total'] or Decimal('0')
    share = (spent * 100 / budget) if budget else Decimal('100')
    reached = [level for level in BUDGET_ALERTS if share >= level]
    return {'spent': spent, 'budget': budget, 'alert': reached[-1] if reached else None}


def _max_cost() -> Optional[Decimal]:
    return _money_setting('INVENTORY_AI_MAX_COST_PER_CALL')


def within_quota(company, user) -> bool:
    """
    Per person: so many a minute and a day; per company: the monthly budget.
    A call fits only if the most it may cost still fits (calls in progress
    count their reserve), so the budget is a cut, not an estimate.
    """
    now = timezone.now()
    per_minute, per_day = _count_setting('INVENTORY_AI_USER_PER_MINUTE'), _count_setting('INVENTORY_AI_USER_PER_DAY')
    if per_minute is None or per_day is None:
        return False
    mine = InventoryAIUsage.objects.filter(user=user)
    if mine.filter(at__gte=now - timedelta(minutes=1)).count() >= per_minute:
        return False
    if mine.filter(at__gte=now - timedelta(days=1)).count() >= per_day:
        return False
    status, reserve = budget_status(company), _max_cost()
    if status['budget'] is None or reserve is None:
        return False
    return status['spent'] + reserve <= status['budget']


def breaker_open() -> bool:
    """The last calls, platform-wide, all failed recently: stop calling for a while."""
    recent = list(InventoryAIUsage.objects.filter(at__gte=timezone.now() - BREAKER_WINDOW, ok__isnull=False)
                  .order_by('-at', '-pk').values_list('ok', flat=True)[:BREAKER_FAILURES])
    return len(recent) == BREAKER_FAILURES and not any(recent)


def company_enabled(company) -> bool:
    """Whether `company` turned AI on. Reading writes nothing: no row means off."""
    return InventoryAISettings.objects.filter(company=company, enabled=True).exists()


def _provider_refusal(provider, company, user) -> Optional[str]:
    if provider is None:
        return NOT_CONFIGURED
    if breaker_open() or not provider.is_available():
        return UNAVAILABLE
    if (user is not None and not within_quota(company, user)) or not provider.has_quota(company, user):
        return QUOTA_EXHAUSTED
    return None


def decide(company, user, *, requested: bool, permitted: bool) -> Decision:
    """
    Whether THIS import of `company` by `user` uses AI. `requested`: the person
    asked for it in this import. `permitted`: they may run the import (the
    caller's own authority check, already passed or not).
    """
    if not requested:
        return Decision(False, NOT_REQUESTED)
    if not settings.INVENTORY_AI_ENABLED:
        return Decision(False, GLOBAL_OFF)
    if not company_enabled(company):
        return Decision(False, COMPANY_OFF)
    if not permitted:
        return Decision(False, FORBIDDEN)
    refusal = _provider_refusal(get_provider(), company, user)
    if refusal:
        return Decision(False, refusal)
    return Decision(True, None)


def state(company, user=None) -> str:
    """The panel's word for `company`: off, on, or on but not usable and why."""
    if not company_enabled(company):
        return STATE_DISABLED
    if not settings.INVENTORY_AI_ENABLED:
        return NOT_CONFIGURED
    return _provider_refusal(get_provider(), company, user) or STATE_ENABLED


def import_payload(decision: Decision, *, requested: bool) -> dict:
    """What an import preview says about AI. `used` stays False until INV-AI-01 builds the adapter."""
    return {'requested': requested, 'allowed': decision.use_ai, 'reason': decision.reason, 'used': False}


class ExtractResult(NamedTuple):
    used: bool
    reason: Optional[str]
    candidates: list


def extract(company, user, text: str, *, requested: bool, permitted: bool) -> ExtractResult:
    """
    Candidates read from `text` by the provider — only when `decide` says so.
    Identifiers are taken out before the call and put back here; the answer
    is cleaned to the fixed shape; each call is recorded, never its text.
    Anything that fails means no AI: the caller goes on the local way.
    """
    if user is None:
        return ExtractResult(False, FORBIDDEN, [])
    decision = decide(company, user, requested=requested, permitted=permitted)
    if not decision.use_ai:
        return ExtractResult(False, decision.reason, [])
    # Counted after normalising: a character that expands (NFKC) cannot slip a longer text past the cap.
    if len(text) > safety.MAX_CHARS or len(safety.normalize(text)) > safety.MAX_CHARS:
        return ExtractResult(False, 'too_long', [])
    redacted, secrets = safety.redact(text)
    provider = get_provider()
    # Reserve under the company's AND the person's lock: two calls at once cannot both take the last of a
    # quota (the person's counts every company) or of the budget.
    with transaction.atomic():
        InventoryAISettings.objects.select_for_update().filter(company=company).first()
        get_user_model().objects.select_for_update().filter(pk=user.pk).first()
        if not within_quota(company, user):
            return ExtractResult(False, QUOTA_EXHAUSTED, [])
        reserve = _max_cost()
        usage = InventoryAIUsage.objects.create(company=company, user=user, chars=len(text), ok=None,
                                                cost_usd=reserve)
    closed = False

    def close(ok, cost):
        nonlocal closed
        InventoryAIUsage.objects.filter(pk=usage.pk).update(ok=ok, cost_usd=cost)
        closed = True

    try:
        try:
            answer, cost = provider.extract(redacted, timeout=safety.TIMEOUT_SECONDS)
        except Exception:  # noqa: BLE001 — a provider that fails is no AI, never a 500
            # It may still bill: the reserve stays as spent, so the budget stays a cut.
            close(False, reserve)
            return ExtractResult(False, 'failed', [])
        spent = _cost(cost)
        if spent is None:
            # Unreadable or negative: it never lowers the spend — the reserve stays as spent.
            close(False, reserve)
            return ExtractResult(False, 'bad_cost', [])
        try:
            rows = safety.clean_candidates(answer)
        except safety.BadAnswer:
            close(False, spent)
            return ExtractResult(False, 'bad_answer', [])
        close(True, spent)
        return ExtractResult(True, None, safety.restore(rows, secrets))
    finally:
        if not closed:
            # Anything that escaped (a kill, a BaseException): the reserve is not left open.
            close(False, reserve)


def _cost(value) -> Optional[Decimal]:
    """What the provider says the call cost, recorded as it was (not capped), or None if unreadable or < 0."""
    from decimal import InvalidOperation

    if isinstance(value, bool) or not isinstance(value, (int, str, Decimal)):
        return None
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return amount if amount.is_finite() and 0 <= amount <= MAX_RECORDED_COST else None
