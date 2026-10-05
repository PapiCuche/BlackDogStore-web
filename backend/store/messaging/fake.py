"""
A WhatsApp provider that sends nothing, for tests and local review.

Selected with `WHATSAPP_PROVIDER=fake`. It records what WOULD have been sent so
a test can read the exact message, and it can be told to fail the next call.
It never touches the network — the same role `FakeIzipay` plays for payments.
"""
from __future__ import annotations

from .base import MessageResult


class FakeProvider:
    sent: list[dict] = []
    _failures: list[Exception] = []
    _counter = 0

    def __init__(self, *, phone_number_id: str, **_ignored):
        self.phone_number_id = phone_number_id

    @classmethod
    def reset(cls) -> None:
        cls.sent = []
        cls._failures = []
        cls._counter = 0

    @classmethod
    def fail_next(cls, error: Exception) -> None:
        cls._failures.append(error)

    def send_template(self, *, to: str, template: str, language: str, parameters: list[str]) -> MessageResult:
        cls = type(self)
        if cls._failures:
            raise cls._failures.pop(0)
        cls._counter += 1
        cls.sent.append({
            'phone_number_id': self.phone_number_id, 'to': to, 'template': template,
            'language': language, 'parameters': list(parameters),
        })
        return MessageResult(provider_message_id=f'wamid.FAKE{cls._counter:06d}')
