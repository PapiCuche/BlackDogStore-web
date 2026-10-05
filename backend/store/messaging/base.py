"""
What a WhatsApp provider is, to the rest of the platform.

ONE OPERATION: send an approved template to one number. Business code never
talks to a provider; `whatsapp_services` does, through this shape. Swapping the
Cloud API for a BSP is a new class here and nothing anywhere else.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MessageResult:
    """The provider accepted the message. `provider_message_id` is its own id."""

    provider_message_id: str


class ProviderError(Exception):
    """
    The provider did not take the message.

    `retryable` says whether trying again later can help: a rate limit or an
    outage can; a template that does not exist or a revoked token cannot.
    `code` is the provider's own, kept so an operator can look it up.
    """

    def __init__(self, message: str, *, retryable: bool = False, code: str = ''):
        super().__init__(message)
        self.retryable = retryable
        self.code = str(code or '')


class NotConfigured(Exception):
    """This company cannot send: a reference or its credential is missing."""
