"""
Measurement — analytics and marketing conversions — as the shop sees it.

The domain says «an order was paid». It does not know Google Analytics, Meta or
TikTok: those are providers of the integrations console, and what talks to them
is `measurement.adapters`. Nothing here can stop a sale, a payment or a page: every
entry point is best effort and says so.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: The providers of the console that measure, in the order they are announced.
PROVIDER_IDS = ('google_analytics', 'meta', 'tiktok')


def public_config() -> dict:
    """
    What a browser may be told, without a session: the public ID of each active
    provider and who sends the purchase. Built from what each provider DECLARES
    as public (`runtime_public`), never by filtering a stored row — a field
    added tomorrow is not announced until somebody decides that it is public.
    """
    from ..integrations import registry, secret_store, service

    announced = {}
    for provider_id in PROVIDER_IDS:
        provider = registry.get(provider_id)
        if provider is None:
            continue
        try:
            config = service.resolve(provider_id)
        except secret_store.SecretStoreError:
            # Stored, and this server cannot read it. Not announced, not a crash.
            logger.error('the stored configuration of %s cannot be read with this server\'s root key', provider_id)
            continue
        if config is None:
            continue
        public = provider.runtime_public(config)
        if public:
            announced[provider_id] = public
    return announced
