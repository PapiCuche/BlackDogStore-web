"""
The provider registry: what can be configured from the console, declared once.

A provider is one third party the application can talk to — an SMTP server, one
of Izipay's products, the WhatsApp Cloud API. It DECLARES itself: which values it
needs, which of them are secrets, how to check them, how to test a connection
and whether it is configured for the platform or for one company. The console,
the secret store, the audit trail and the health report work from that
declaration, so a new provider gets all four by being registered.

NOT A KEY/VALUE TABLE. Nothing can be stored that a provider did not declare,
and a value declared secret cannot be stored anywhere but sealed.

ADDING A PROVIDER is an adapter, a declaration here and its tests. It is not a
change in checkout, orders, service or inventory: they ask the registry.
"""
import base64
import binascii
import re
from dataclasses import dataclass, field as dataclass_field

SCOPE_PLATFORM = 'platform'
SCOPE_COMPANY = 'company'

_HOST = re.compile(r'^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)*[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$', re.I)
_EMAIL = re.compile(r'^[^@\s<>,;:"\\]+@[^@\s<>,;:"\\]+\.[^@\s<>,;:"\\]+$')
MAX_TEXT = 2000
MAX_SECRET = 16 * 1024
#: Decoded size of a `file` field that declares no maximum of its own.
MAX_FILE = 64 * 1024


class ConfigError(Exception):
    """What is wrong, per field. Never carries a value."""

    def __init__(self, errors: dict):
        super().__init__('Configuración no válida.')
        self.errors = dict(errors)


@dataclass(frozen=True)
class Field:
    name: str
    label: str
    required: bool = False
    #: text · int · bool · choice · email · host · url · textarea · file (base64 in JSON; `maximum` = bytes)
    kind: str = 'text'
    choices: tuple = ()
    default: object = None
    secret: bool = False
    help: str = ''
    minimum: int = None
    maximum: int = None

    def _clean_file(self, value) -> str:
        """
        A small file, as the base64 a JSON body carries. Stored as that text.

        The limit is on the DECODED size and is checked before decoding, on the
        length of what arrived: nothing larger is ever held twice in memory.
        """
        if not isinstance(value, str):
            raise ValueError('Tiene que ser un archivo.')
        text = ''.join(value.split())
        if not text:
            return ''
        limit = self.maximum or MAX_FILE
        too_big = f'El archivo supera el máximo de {limit // 1024} KiB.'
        if len(text) > (limit + 2) // 3 * 4:
            raise ValueError(too_big)
        try:
            data = base64.b64decode(text, validate=True)
        except (ValueError, binascii.Error):
            raise ValueError('El archivo no llegó completo. Vuelve a seleccionarlo.') from None
        if len(data) > limit:
            raise ValueError(too_big)
        return text

    def describe(self) -> dict:
        data = {
            'name': self.name, 'label': self.label, 'kind': self.kind, 'required': self.required,
            'secret': self.secret, 'help': self.help,
        }
        if self.kind == 'file':
            data['max_bytes'] = self.maximum or MAX_FILE
        if self.choices:
            data['choices'] = [{'value': value, 'label': label} for value, label in self.choices]
        if self.default is not None and not self.secret:
            data['default'] = self.default
        return data

    def clean(self, value):
        """The value as it will be stored, or ConfigError's message for this field."""
        if self.kind == 'file':
            return self._clean_file(value)
        if self.kind == 'bool':
            if isinstance(value, bool):
                return value
            raise ValueError('Tiene que ser sí o no.')
        if self.kind == 'int':
            if isinstance(value, bool) or not isinstance(value, (int, str)):
                raise ValueError('Tiene que ser un número.')
            try:
                number = int(str(value).strip())
            except ValueError:
                raise ValueError('Tiene que ser un número.') from None
            if self.minimum is not None and number < self.minimum:
                raise ValueError(f'El mínimo es {self.minimum}.')
            if self.maximum is not None and number > self.maximum:
                raise ValueError(f'El máximo es {self.maximum}.')
            return number
        if not isinstance(value, str):
            raise ValueError('Tiene que ser un texto.')
        text = value.strip()
        if len(text) > (MAX_SECRET if self.secret else MAX_TEXT):
            raise ValueError('Es demasiado largo.')
        if not text:
            return ''
        if self.kind == 'choice' and text not in {choice for choice, _label in self.choices}:
            raise ValueError('No es una de las opciones.')
        if self.kind == 'email' and not _EMAIL.match(text):
            raise ValueError('No es una dirección de correo.')
        if self.kind == 'host' and not _HOST.match(text):
            raise ValueError('Tiene que ser el nombre del servidor, sin esquema, usuario ni puerto.')
        if self.kind == 'url' and not text.startswith('https://'):
            raise ValueError('Tiene que empezar por https://')
        return text


@dataclass(frozen=True)
class TestOutcome:
    """What a connection test found, already safe to show: a code and a sentence."""

    ok: bool
    status: str
    message: str

    __test__ = False     # not a test case, whatever its name says to a test runner


@dataclass(frozen=True)
class Resolved:
    """One provider's configuration, opened and ready for its adapter."""

    provider_id: str
    public: dict
    secrets: dict
    source: str                       # 'panel' or 'env'
    company_id: int = None
    extra: dict = dataclass_field(default_factory=dict)

    def get(self, name, default=''):
        if name in self.secrets:
            return self.secrets[name]
        return self.public.get(name, default)


class Provider:
    """Subclass, fill in, register. See the module docstring."""

    id = ''
    label = ''
    category = ''
    scope = SCOPE_PLATFORM
    description = ''
    fields: tuple = ()
    #: Activating one provider of an exclusive category switches the others off.
    exclusive_in_category = False
    #: Modes this provider can run in, for the console to show ('test', 'production'…).
    supported_modes: tuple = ()

    # -- declaration helpers ---------------------------------------------------

    def public_fields(self):
        return [f for f in self.fields if not f.secret]

    def secret_fields(self):
        return [f for f in self.fields if f.secret]

    def field(self, name):
        return next((f for f in self.fields if f.name == name), None)

    def describe(self) -> dict:
        return {
            'id': self.id, 'label': self.label, 'category': self.category, 'scope': self.scope,
            'description': self.description, 'supported_modes': list(self.supported_modes),
            'fields': [f.describe() for f in self.fields],
        }

    # -- behaviour a provider may override -------------------------------------

    def clean(self, public: dict, secrets: dict) -> None:
        """Cross-field rules. Raise ConfigError({field: message})."""

    def missing(self, public: dict, secrets: dict) -> list:
        """Required fields that are empty, by name."""
        absent = []
        for f in self.fields:
            value = secrets.get(f.name) if f.secret else public.get(f.name)
            if f.required and (value is None or value == ''):
                absent.append(f.name)
        return absent

    def mode(self, public: dict, secrets: dict) -> str:
        """'test', 'production' or '' — what the console shows beside the state."""
        return ''

    def activation_confirmation(self, public: dict, secrets: dict):
        """
        `(word, message)` when activating THIS configuration is something a
        master has to mean — the word is typed back — or None.
        """
        return None

    def activation_guard(self, public: dict, secrets: dict, payload: dict) -> None:
        """Last word before a draft becomes what runs. Raise ConfigError to refuse."""
        confirmation = self.activation_confirmation(public, secrets)
        if confirmation and payload.get('confirm') != confirmation[0]:
            raise ConfigError({'confirm': confirmation[1]})

    def test(self, config: Resolved, **options) -> TestOutcome:
        raise NotImplementedError

    def from_env(self, company=None):
        """`(public, secrets)` read from the legacy environment settings, or None."""
        return None

    def on_activated(self, company, public: dict) -> None:
        """
        Inside the transaction that activates: rows of the domain that must exist
        for this configuration to be usable. It fails, the activation fails.
        """

    def after_change(self, company=None) -> None:
        """Called after activation, disabling, enabling or revocation."""


_PROVIDERS = {}


def register(provider: Provider) -> Provider:
    if not provider.id:
        raise ValueError('A provider needs an id.')
    if provider.id in _PROVIDERS:
        raise ValueError(f'Provider id already registered: {provider.id}')
    _PROVIDERS[provider.id] = provider
    return provider


def unregister(provider_id: str) -> None:
    _PROVIDERS.pop(provider_id, None)


def get(provider_id: str):
    return _PROVIDERS.get(provider_id)


def all_providers() -> list:
    return list(_PROVIDERS.values())


def in_category(category: str) -> list:
    return [p for p in _PROVIDERS.values() if p.category == category]
