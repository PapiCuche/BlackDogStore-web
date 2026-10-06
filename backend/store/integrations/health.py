"""
What the integrations look like to whoever operates the installation.

`manage.py ops_status` prints it and `deploy/healthcheck.sh` copies its lines.
It reads; it writes nothing. And it says labels, states and where a
configuration comes from — never a host, a user, a key or a token.
"""
from ..models import IntegrationConfig
from . import mail, payments, registry, secret_store, service

_STATE = {
    service.STATE_ACTIVE: 'activa', service.STATE_ERROR: 'activa', service.STATE_DISABLED: 'desactivada',
    service.STATE_CONFIGURED: 'borrador sin activar', service.STATE_VALIDATED: 'borrador sin activar',
    service.STATE_NOT_CONFIGURED: 'sin configurar',
}
_SOURCE = {'panel': 'consola', 'env': 'entorno'}
_MODE = {'test': 'TEST', 'production': 'PRODUCCIÓN'}


def _unreadable(provider) -> int:
    """How many of this provider's running rows cannot be opened with the root key."""
    count = 0
    for config in IntegrationConfig.objects.filter(provider=provider.id, slot=service.ACTIVE, enabled=True):
        try:
            service.open_secrets(config)
        except secret_store.SecretStoreError:
            count += 1
    return count


def _platform(provider, findings) -> str:
    state, source = service.state(provider.id), service.source(provider.id)
    active = service.row(provider.id)
    if state == service.STATE_ERROR and active is None:
        # A first draft whose test failed. Nothing is running: it is not an alarm.
        return f'{provider.label}: borrador con la prueba fallida'
    words = _STATE[state]
    if state in (service.STATE_ACTIVE, service.STATE_ERROR, service.STATE_DISABLED):
        public = dict(active.public) if active is not None else dict((provider.from_env() or ({}, {}))[0])
        mode = _MODE.get(provider.mode(public, {}), '') if len(provider.supported_modes) > 1 else ''
        words += (f', {mode}' if mode and state != service.STATE_DISABLED else '') + f' ({_SOURCE.get(source, source)})'
        if state == service.STATE_ERROR:
            # The status is one of the provider's own fixed words, never a message.
            findings.append(f'{provider.label}: la última prueba falló ({active.last_test_status[:40]}). '
                            'Revísala en Configuración › Integraciones.')
    return f'{provider.label}: {words}'


def _per_company(provider) -> str:
    companies = IntegrationConfig.objects.filter(provider=provider.id, slot=service.ACTIVE, enabled=True).count()
    if not companies:
        return f'{provider.label}: ninguna empresa en la consola'
    return f'{provider.label}: {companies} {"empresa" if companies == 1 else "empresas"} en la consola'


def report() -> tuple:
    """`(findings, summary)`: what needs a person, and one line of what runs."""
    findings, parts = [], []
    store_ready = secret_store.is_available()
    if not store_ready and IntegrationConfig.objects.exists():
        findings.append(
            f'hay configuración guardada en la consola y falta la clave raíz {secret_store.KEY_SETTING}: '
            'nada de lo configurado ahí puede funcionar. Ponla en el entorno del servidor.')

    for provider in sorted(registry.all_providers(), key=lambda p: (p.category, p.id)):
        if store_ready:
            unreadable = _unreadable(provider)
            if unreadable:
                findings.append(
                    f'{provider.label}: lo guardado en la consola no se puede leer con la clave raíz de este servidor. '
                    f'¿Se cambió {secret_store.KEY_SETTING} sin conservar la anterior?')
        part = _per_company(provider) if provider.scope == registry.SCOPE_COMPANY else _platform(provider, findings)
        note = provider.health_note()
        parts.append(f'{part} — {note}' if note else part)

    # The two without which the shop does not do its job.
    if not mail.is_configured():
        findings.append('el correo no está configurado: la verificación de cuentas, la recuperación de contraseña '
                        'y los avisos de pedido no salen. Configúralo en Configuración › Integraciones › Correo.')
    code = payments.active_code()
    adapter = payments.adapter(code)
    if not code:
        findings.append('no hay ninguna pasarela de pago activa: la tienda no puede cobrar.')
    elif adapter is None:
        findings.append('la pasarela de pago seleccionada no existe en esta versión: la tienda no puede cobrar.')
    else:
        try:
            adapter.load_credentials()
        except payments.ERRORS:
            findings.append('la pasarela de pago activa no tiene sus credenciales completas: la tienda no puede cobrar.')
    return findings, ' · '.join(parts)

