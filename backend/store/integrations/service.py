"""
What the console does to a provider's configuration, and what the running
system reads back.

    save a draft  →  test it  →  activate it  →  the system resolves it

Every write here is made by a platform master (the views check; nothing in this
module trusts its caller for that) and leaves a line in the audit trail that
names fields and never values.
"""
import logging

from django.db import transaction
from django.utils import timezone

from ..models import AdminAuditLog, IntegrationConfig
from . import registry, secret_store
from .registry import ConfigError, Resolved, TestOutcome

logger = logging.getLogger('store.integrations')

ACTIVE = IntegrationConfig.SLOT_ACTIVE
DRAFT = IntegrationConfig.SLOT_DRAFT

STATE_NOT_CONFIGURED = 'NOT_CONFIGURED'
STATE_CONFIGURED = 'CONFIGURED'
STATE_VALIDATED = 'VALIDATED'
STATE_ACTIVE = 'ACTIVE'
STATE_ERROR = 'ERROR'
STATE_DISABLED = 'DISABLED'

REVOKE_WORD = 'REVOCAR'


class Conflict(Exception):
    """The caller acted on something that is no longer what they read."""

    def __init__(self, message, version=None):
        super().__init__(message)
        self.version = version


class NotFound(Exception):
    pass


# -- rows ----------------------------------------------------------------------

def _context(provider_id, company, slot) -> str:
    return f'{provider_id}|{company.pk if company else "platform"}|{slot}'


def row(provider_id, company=None, slot=ACTIVE, *, lock=False):
    rows = IntegrationConfig.objects.filter(provider=provider_id, slot=slot)
    rows = rows.filter(company=company) if company else rows.filter(company__isnull=True)
    if lock:
        rows = rows.select_for_update()
    return rows.first()


def open_secrets(config: IntegrationConfig) -> dict:
    return secret_store.open_sealed(
        _context(config.provider, config.company, config.slot), config.sealed_secrets,
    )


def _seal(config: IntegrationConfig, secrets: dict) -> None:
    config.sealed_secrets = secret_store.seal(
        _context(config.provider, config.company, config.slot), secrets,
    )


def _meta(value: str, actor) -> dict:
    """What may be shown about a secret. Four characters of a long one; none of a short one."""
    return {
        'last_four': value[-4:] if len(value) >= 12 else '',
        'updated_at': timezone.now().isoformat(),
        'updated_by': getattr(actor, 'username', '') or '',
    }


def _audit(action, provider, company, actor, request, **metadata):
    AdminAuditLog.log(
        actor=actor, action=action, target_type='integration', target_id=provider.id,
        metadata={'provider': provider.id, 'scope': provider.scope, **metadata},
        request=request, company=company,
    )


# -- what the running system reads -----------------------------------------------

def resolve(provider_id, company=None):
    """
    The configuration the system must use NOW for this provider, or None.

    The console first: an active, enabled row. Then the legacy environment, for
    an installation that has not moved its credentials to the console yet. A
    disabled row is an explicit «off» and does not fall back to the environment.
    """
    provider = registry.get(provider_id)
    if provider is None:
        return None
    active = row(provider_id, company, ACTIVE)
    if active is not None:
        if not active.enabled:
            return None
        return Resolved(
            provider_id=provider_id, public=dict(active.public), secrets=open_secrets(active),
            source='panel', company_id=company.pk if company else None,
        )
    legacy = provider.from_env(company)
    if legacy is None:
        return None
    public, secrets = legacy
    return Resolved(
        provider_id=provider_id, public=dict(public), secrets=dict(secrets), source='env',
        company_id=company.pk if company else None,
    )


def resolve_panel(provider_id, company=None):
    """Only what the console activated. None when the console has nothing enabled for this provider."""
    active = row(provider_id, company, ACTIVE)
    if active is None or not active.enabled:
        return None
    return Resolved(
        provider_id=provider_id, public=dict(active.public), secrets=open_secrets(active),
        source='panel', company_id=company.pk if company else None,
    )


def has_panel_row(provider_id, company=None) -> bool:
    """The console has taken this provider over (even if it then switched it off)."""
    return row(provider_id, company, ACTIVE) is not None


def source(provider_id, company=None) -> str:
    """'panel', 'env' or 'none' — where the running configuration comes from."""
    if row(provider_id, company, ACTIVE) is not None:
        return 'panel'
    provider = registry.get(provider_id)
    return 'env' if provider is not None and provider.from_env(company) is not None else 'none'


def state(provider_id, company=None) -> str:
    active = row(provider_id, company, ACTIVE)
    draft = row(provider_id, company, DRAFT)
    if active is not None:
        if not active.enabled:
            return STATE_DISABLED
        return STATE_ERROR if active.last_test_status not in ('', 'ok') else STATE_ACTIVE
    if draft is not None:
        if draft.validated:
            return STATE_VALIDATED
        return STATE_ERROR if draft.last_test_status not in ('', 'ok', 'incomplete') else STATE_CONFIGURED
    provider = registry.get(provider_id)
    if provider is not None and provider.from_env(company) is not None:
        return STATE_ACTIVE
    return STATE_NOT_CONFIGURED


# -- drafts ----------------------------------------------------------------------

def _clean(provider, public_in, secrets_in):
    """Validated `(public, secret changes)`; secret changes map a name to a value or to None."""
    errors = {}
    if not isinstance(public_in, dict) or not isinstance(secrets_in, dict):
        raise ConfigError({'__all__': 'Formato no válido.'})
    public_names = {f.name for f in provider.public_fields()}
    secret_names = {f.name for f in provider.secret_fields()}
    for name in public_in:
        if name not in public_names:
            errors[name] = 'Este campo no existe en esta integración.' if name not in secret_names \
                else 'Este valor es un secreto: va en «secrets».'
    for name in secrets_in:
        if name not in secret_names:
            errors[name] = 'Este secreto no existe en esta integración.'
    public = {}
    for f in provider.public_fields():
        raw = public_in.get(f.name, f.default if f.default is not None else ('' if f.kind != 'bool' else False))
        try:
            public[f.name] = f.clean(raw)
        except ValueError as exc:
            errors[f.name] = str(exc)
    changes = {}
    for f in provider.secret_fields():
        if f.name not in secrets_in:
            continue
        raw = secrets_in[f.name]
        if raw is None or raw == '':
            changes[f.name] = None
            continue
        try:
            changes[f.name] = f.clean(raw)
        except ValueError as exc:
            errors[f.name] = str(exc)
    if errors:
        raise ConfigError(errors)
    return public, changes


@transaction.atomic
def save_draft(provider, company, *, actor, public, secrets, version=None, request=None):
    secret_store.seal('probe', {'probe': 'x'})          # fail before touching anything if there is no root key
    public, changes = _clean(provider, public, secrets)
    draft = row(provider.id, company, DRAFT, lock=True)
    active = row(provider.id, company, ACTIVE)
    if draft is not None and version != draft.version:
        raise Conflict('Otra persona cambió este borrador. Vuelve a cargarlo antes de guardar.', draft.version)

    if draft is None:
        # A new draft starts from what is running, so that changing a host does
        # not mean typing the password again.
        current_secrets = open_secrets(active) if active is not None else {}
        current_meta = dict(active.secret_meta) if active is not None else {}
        previous_public = dict(active.public) if active is not None else {}
        draft = IntegrationConfig(provider=provider.id, company=company, slot=DRAFT, version=0)
    else:
        current_secrets, current_meta = open_secrets(draft), dict(draft.secret_meta)
        previous_public = dict(draft.public)

    for name, value in changes.items():
        if value is None:
            current_secrets.pop(name, None)
            current_meta.pop(name, None)
        else:
            current_secrets[name] = value
            current_meta[name] = _meta(value, actor)
    provider.clean(public, current_secrets)

    draft.public = public
    draft.secret_meta = current_meta
    _seal(draft, current_secrets)
    draft.validated = False
    draft.last_test_status = draft.last_test_message = ''
    draft.last_tested_at = None
    draft.version += 1
    draft.updated_by = actor
    draft.save()
    _audit(
        'integration_draft_saved', provider, company, actor, request,
        fields_changed=sorted(n for n in public if public[n] != previous_public.get(n)),
        secrets_changed=sorted(changes),
    )
    return draft


def import_env(provider, company, *, actor, request=None):
    """A draft made from what the environment holds today. Nothing is returned to the caller but the draft's description."""
    legacy = provider.from_env(company)
    if legacy is None:
        raise NotFound('No hay configuración de esta integración en el entorno del servidor.')
    public, secrets = legacy
    existing = row(provider.id, company, DRAFT)
    return save_draft(
        provider, company, actor=actor, request=request,
        public={k: v for k, v in public.items() if provider.field(k) and not provider.field(k).secret},
        secrets={k: v for k, v in secrets.items() if v},
        version=existing.version if existing else None,
    )


# -- testing ---------------------------------------------------------------------

def test(provider, company, *, actor, request=None, target=None, **options) -> dict:
    """
    Test what is STORED — the draft if there is one, else the active row, else
    what the environment holds. Never what a browser sends along with the request.
    """
    config = row(provider.id, company, DRAFT if target != ACTIVE else ACTIVE)
    if config is None and target is None:
        config = row(provider.id, company, ACTIVE)
    if config is not None:
        public, secrets, origin = dict(config.public), open_secrets(config), 'panel'
    else:
        legacy = provider.from_env(company)
        if legacy is None:
            raise NotFound('Todavía no hay nada que probar: guarda primero la configuración.')
        public, secrets, origin = dict(legacy[0]), dict(legacy[1]), 'env'

    absent = provider.missing(public, secrets)
    if absent:
        labels = ', '.join(provider.field(name).label for name in absent)
        outcome = TestOutcome(False, 'incomplete', f'Faltan datos: {labels}.')
    else:
        resolved = Resolved(provider.id, public, secrets, origin, company.pk if company else None)
        try:
            outcome = provider.test(resolved, **options)
        except Exception as exc:  # noqa: BLE001 — an adapter's exception may quote a credential
            logger.error('integration test crashed provider=%s error=%s', provider.id, type(exc).__name__)
            outcome = TestOutcome(False, 'error', 'La prueba no se pudo completar.')

    tested_at = timezone.now()
    if config is not None:
        with transaction.atomic():
            locked = IntegrationConfig.objects.select_for_update().get(pk=config.pk)
            locked.last_tested_at = tested_at
            locked.last_test_status = outcome.status[:40]
            locked.last_test_message = outcome.message[:300]
            locked.validated = outcome.ok
            locked.version += 1
            locked.save(update_fields=['last_tested_at', 'last_test_status', 'last_test_message',
                                       'validated', 'version', 'updated_at'])
            config = locked
    _audit('integration_tested', provider, company, actor, request,
           result=outcome.status, slot=config.slot if config is not None else 'env')
    return {
        'ok': outcome.ok, 'status': outcome.status, 'message': outcome.message,
        'tested_at': tested_at.isoformat(), 'version': config.version if config is not None else None,
    }


# -- activation ------------------------------------------------------------------

@transaction.atomic
def activate(provider, company, *, actor, version, payload=None, request=None):
    draft = row(provider.id, company, DRAFT, lock=True)
    if draft is None:
        raise NotFound('No hay ningún borrador que activar.')
    if version != draft.version:
        raise Conflict('El borrador cambió desde que lo abriste. Vuelve a cargarlo.', draft.version)
    if not draft.validated:
        raise Conflict('Antes de activar esta configuración hay que pasar la prueba con lo que está guardado.',
                       draft.version)
    secrets = open_secrets(draft)
    provider.activation_guard(dict(draft.public), secrets, payload or {})

    if provider.exclusive_in_category:
        # One provider of this category runs at a time. Switching is this one act.
        others = [p.id for p in registry.in_category(provider.category) if p.id != provider.id]
        siblings = IntegrationConfig.objects.select_for_update().filter(provider__in=others, slot=ACTIVE, enabled=True)
        siblings = siblings.filter(company=company) if company else siblings.filter(company__isnull=True)
        for sibling in siblings:
            sibling.enabled = False
            sibling.version += 1
            sibling.save(update_fields=['enabled', 'version', 'updated_at'])
            _audit('integration_disabled', registry.get(sibling.provider), company, actor, request,
                   reason=f'replaced_by:{provider.id}')

    active = row(provider.id, company, ACTIVE, lock=True) or IntegrationConfig(
        provider=provider.id, company=company, slot=ACTIVE, version=0)
    active.public = dict(draft.public)
    active.secret_meta = dict(draft.secret_meta)
    _seal(active, secrets)
    active.enabled = True
    active.validated = True
    active.last_tested_at = draft.last_tested_at
    active.last_test_status = draft.last_test_status
    active.last_test_message = draft.last_test_message
    active.version += 1
    active.updated_by = actor
    active.save()
    draft.delete()
    _audit('integration_activated', provider, company, actor, request, mode=provider.mode(active.public, secrets))
    transaction.on_commit(lambda: provider.after_change(company))
    return active


@transaction.atomic
def set_enabled(provider, company, *, actor, enabled: bool, request=None):
    active = row(provider.id, company, ACTIVE, lock=True)
    if active is None:
        raise NotFound('Esta integración no tiene una configuración activa.')
    if active.enabled != enabled:
        if enabled and provider.exclusive_in_category:
            others = [p.id for p in registry.in_category(provider.category) if p.id != provider.id]
            running = IntegrationConfig.objects.filter(provider__in=others, slot=ACTIVE, enabled=True)
            running = running.filter(company=company) if company else running.filter(company__isnull=True)
            if running.exists():
                raise Conflict('Ya hay otro proveedor de esta categoría activo. Desactívalo primero.', active.version)
        active.enabled = enabled
        active.version += 1
        active.updated_by = actor
        active.save(update_fields=['enabled', 'version', 'updated_by', 'updated_at'])
        _audit('integration_enabled' if enabled else 'integration_disabled', provider, company, actor, request)
        transaction.on_commit(lambda: provider.after_change(company))
    return active


@transaction.atomic
def revoke(provider, company, *, actor, request=None):
    rows = IntegrationConfig.objects.select_for_update().filter(provider=provider.id)
    rows = rows.filter(company=company) if company else rows.filter(company__isnull=True)
    if not rows.exists():
        raise NotFound('Esta integración no tiene nada guardado.')
    rows.delete()
    _audit('integration_revoked', provider, company, actor, request)
    transaction.on_commit(lambda: provider.after_change(company))


def reseal_all() -> int:
    """After a root-key rotation: seal every row again with the current key."""
    count = 0
    with transaction.atomic():
        for config in IntegrationConfig.objects.select_for_update().exclude(sealed_secrets=''):
            _seal(config, open_secrets(config))
            config.save(update_fields=['sealed_secrets', 'updated_at'])
            count += 1
    return count


# -- description for the console ---------------------------------------------------

def _describe_row(provider, config):
    if config is None:
        return None
    secrets = {}
    for f in provider.secret_fields():
        meta = config.secret_meta.get(f.name)
        secrets[f.name] = {'configured': True, **meta} if meta else {'configured': False}
    return {
        'public': dict(config.public), 'secrets': secrets, 'enabled': config.enabled,
        'validated': config.validated, 'version': config.version,
        'last_tested_at': config.last_tested_at.isoformat() if config.last_tested_at else None,
        'last_test_status': config.last_test_status, 'last_test_message': config.last_test_message,
        'updated_at': config.updated_at.isoformat() if config.updated_at else None,
        'updated_by': getattr(config.updated_by, 'username', '') or '',
        'mode': provider.mode(dict(config.public), {}),
    }


def describe(provider, company=None, *, with_fields=True) -> dict:
    active = row(provider.id, company, ACTIVE)
    draft = row(provider.id, company, DRAFT)
    legacy = provider.from_env(company) if active is None else None
    data = {
        'id': provider.id, 'label': provider.label, 'category': provider.category, 'scope': provider.scope,
        'description': provider.description, 'supported_modes': list(provider.supported_modes),
        'state': state(provider.id, company), 'source': source(provider.id, company),
        'company': {'id': company.pk, 'name': company.name, 'slug': company.slug} if company else None,
        'active': _describe_row(provider, active), 'draft': _describe_row(provider, draft),
        # What the environment contributes: public values and WHICH secrets it holds, never their values.
        'env': None if legacy is None else {
            'public': {k: v for k, v in legacy[0].items() if provider.field(k) and not provider.field(k).secret},
            'secrets': sorted(k for k, v in legacy[1].items() if v),
            'mode': provider.mode(dict(legacy[0]), {}),
        },
        'store_available': secret_store.is_available(),
    }
    if with_fields:
        data['fields'] = [f.describe() for f in provider.fields]
    return data
