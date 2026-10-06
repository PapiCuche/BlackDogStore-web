"""
INTEGRATIONS-CONSOLE — third parties, configured from the panel.

    from store import integrations
    config = integrations.resolve('smtp')            # what must be used NOW, or None

The panel configures (`views`), the secret store protects (`secret_store`), the
registry decides what exists (`registry`), the adapters talk to third parties
(`providers`), and the rest of the application asks here instead of naming a
vendor.
"""
from . import registry, secret_store, service  # noqa: F401
from .registry import ConfigError, Resolved, TestOutcome  # noqa: F401
from .service import resolve, source, state  # noqa: F401

from . import providers  # noqa: F401,E402 — registers the built-in providers
