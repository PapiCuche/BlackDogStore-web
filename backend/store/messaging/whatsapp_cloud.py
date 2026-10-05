"""
The official WhatsApp Business Cloud API (Meta), and nothing else.

No browser automation, no WhatsApp Web, no personal numbers: one HTTPS request
to Graph, authenticated with the tenant's own token, sending a template Meta
approved. The standard library is enough for one POST, so there is no new
dependency.

THE TOKEN NEVER LEAVES THIS OBJECT. It is not logged, and anything the API
answers is scrubbed before it becomes an error message — a provider that echoes
a credential back must not be able to put it in a database row.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from .base import MessageResult, ProviderError

GRAPH = 'https://graph.facebook.com'
TIMEOUT_SECONDS = 8

#: Graph error codes that mean "later", whatever the HTTP status was.
_RETRYABLE_CODES = frozenset({'1', '2', '4', '80007', '130429', '131048', '131056', '133004'})


class CloudApiProvider:
    def __init__(self, *, phone_number_id: str, access_token: str, api_version: str = 'v21.0'):
        self.phone_number_id = phone_number_id
        self._access_token = access_token
        self.api_version = api_version

    def _scrub(self, text) -> str:
        return str(text or '').replace(self._access_token, '[oculto]')[:160]

    def send_template(self, *, to: str, template: str, language: str, parameters: list[str]) -> MessageResult:
        payload = {
            'messaging_product': 'whatsapp',
            'to': to,
            'type': 'template',
            'template': {
                'name': template,
                'language': {'code': language},
                'components': [{
                    'type': 'body',
                    'parameters': [{'type': 'text', 'text': str(value)} for value in parameters],
                }],
            },
        }
        request = urllib.request.Request(
            f'{GRAPH}/{self.api_version}/{self.phone_number_id}/messages',
            data=json.dumps(payload).encode(),
            headers={
                'Authorization': f'Bearer {self._access_token}',
                'Content-Type': 'application/json',
            },
            method='POST',
        )
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                body = json.loads(response.read() or b'{}')
        except urllib.error.HTTPError as exc:
            raise self._refusal(exc) from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, 'reason', exc)
            if isinstance(exc, TimeoutError) or isinstance(reason, TimeoutError):
                # NO ANSWER IS NOT A REFUSAL. The request may have been written
                # and accepted before the silence, and sending it again by
                # itself is how a customer gets the same message twice. A
                # person decides whether to retry.
                raise ProviderError(
                    'El proveedor no respondió: el mensaje pudo haberse enviado. '
                    'Reinténtalo sólo si el cliente no lo recibió.', retryable=False,
                ) from None
            # Refused, unreachable, no DNS: it never left.
            raise ProviderError(
                f'Sin conexión con el proveedor ({type(reason).__name__}).', retryable=True,
            ) from None
        except ValueError:
            raise ProviderError('Respuesta ilegible del proveedor.', retryable=False) from None

        messages = body.get('messages') if isinstance(body, dict) else None
        message_id = (messages or [{}])[0].get('id') if isinstance(messages, list) else None
        if not message_id:
            # Not retried: the message may have been accepted, and a second
            # send is worse than a row somebody has to look at.
            raise ProviderError('El proveedor no devolvió un identificador.', retryable=False)
        return MessageResult(provider_message_id=str(message_id)[:128])

    def _refusal(self, exc: urllib.error.HTTPError) -> ProviderError:
        code, message = '', ''
        try:
            error = (json.loads(exc.read() or b'{}') or {}).get('error') or {}
            code, message = str(error.get('code') or ''), str(error.get('message') or '')
        except (ValueError, AttributeError):
            pass
        retryable = exc.code == 429 or exc.code >= 500 or code in _RETRYABLE_CODES
        return ProviderError(
            self._scrub(message) or f'HTTP {exc.code}', retryable=retryable, code=code,
        )
