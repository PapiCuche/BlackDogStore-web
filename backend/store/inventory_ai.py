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

No provider exists in this phase (`get_provider` is always None): the adapter,
its credentials, quotas and budget come with INV-AI-01, behind this same gate.
"""
from typing import NamedTuple, Optional

from django.conf import settings

from .models import InventoryAISettings

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


def get_provider():
    """
    The configured AI provider, or None. Always None in this phase: no adapter
    is built, so no request ever leaves the server. A provider answers
    `is_available()` and `has_quota(company, user)`.
    """
    return None


def company_enabled(company) -> bool:
    """Whether `company` turned AI on. Reading writes nothing: no row means off."""
    return InventoryAISettings.objects.filter(company=company, enabled=True).exists()


def _provider_refusal(provider, company, user) -> Optional[str]:
    if provider is None:
        return NOT_CONFIGURED
    if not provider.is_available():
        return UNAVAILABLE
    if not provider.has_quota(company, user):
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
