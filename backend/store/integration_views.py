"""
INTEGRATIONS-CONSOLE — the API behind Configuración › Integraciones.

PLATFORM MASTERS ONLY. Every route here carries `IsPlatformAdmin`, which is
`User.is_superuser` and nothing else: no company role, no capability and no
`is_staff` flag opens it. A refused caller is told the same thing whether the
integration they named is configured, empty or does not exist.

SECRETS ARE WRITE-ONLY. A request may carry a secret; no response does. What
comes back about a secret is that it is configured, its last four characters,
when it was set and by whom.

Reads write nothing. Every change is one of the POST/PUT routes, each of which
leaves its line in the audit trail.
"""
from rest_framework import permissions, status
from rest_framework.exceptions import NotFound as ApiNotFound
from rest_framework.response import Response
from rest_framework.views import APIView

from .integrations import registry, secret_store, service
from .integrations.registry import ConfigError
from .models import Company
from .permissions import IsPlatformAdmin
from .throttles import AdminUsersThrottle


class _IntegrationView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsPlatformAdmin]
    throttle_classes = [AdminUsersThrottle]

    def target(self, request, provider_id):
        """`(provider, company)` for this request, or the error that ends it."""
        provider = registry.get(provider_id)
        if provider is None:
            raise ApiNotFound('No encontrado.')
        raw = request.query_params.get('company')
        if provider.scope == registry.SCOPE_PLATFORM:
            if raw:
                raise _Refusal('Esta integración es de la plataforma: no lleva empresa.')
            return provider, None
        if not raw:
            raise _Refusal('Esta integración se configura por empresa: indica cuál.')
        try:
            company = Company.objects.get(pk=int(raw))
        except (ValueError, TypeError, Company.DoesNotExist):
            raise ApiNotFound('No encontrado.') from None
        return provider, company

    @staticmethod
    def body(request) -> dict:
        """The JSON object of the request. Anything else — a list, a string — is refused, not guessed at."""
        if not isinstance(request.data, dict):
            raise _Refusal('El cuerpo de la petición tiene que ser un objeto JSON.')
        return request.data

    def handle_exception(self, exc):
        if isinstance(exc, _Refusal):
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        if isinstance(exc, ConfigError):
            return Response({'detail': 'Revisa los campos señalados.', 'errors': exc.errors},
                            status=status.HTTP_400_BAD_REQUEST)
        if isinstance(exc, service.Conflict):
            return Response({'detail': str(exc), 'version': exc.version}, status=status.HTTP_409_CONFLICT)
        if isinstance(exc, service.NotFound):
            return Response({'detail': str(exc)}, status=status.HTTP_404_NOT_FOUND)
        if isinstance(exc, secret_store.SecretStoreError):
            # The store's messages name a setting or a condition, never a value.
            return Response({'detail': str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        return super().handle_exception(exc)


class _Refusal(Exception):
    pass


class IntegrationListView(_IntegrationView):
    """GET — every registered provider and how it stands."""

    http_method_names = ['get', 'head', 'options']

    def get(self, request):
        companies = list(Company.objects.order_by('name'))
        results = []
        for provider in registry.all_providers():
            if provider.scope == registry.SCOPE_PLATFORM:
                results.append(service.describe(provider, with_fields=False))
                continue
            entry = {
                'id': provider.id, 'label': provider.label, 'category': provider.category,
                'scope': provider.scope, 'description': provider.description, 'state': None,
                'companies': [
                    {'id': c.pk, 'name': c.name, 'slug': c.slug,
                     'state': service.state(provider.id, c), 'source': service.source(provider.id, c)}
                    for c in companies
                ],
            }
            results.append(entry)
        return Response({'results': results, 'store_available': secret_store.is_available()})


class IntegrationDetailView(_IntegrationView):
    """GET — one provider: its fields, its active configuration and its draft, without a secret."""

    http_method_names = ['get', 'head', 'options']

    def get(self, request, provider_id):
        provider, company = self.target(request, provider_id)
        return Response(service.describe(provider, company))


class IntegrationDraftView(_IntegrationView):
    """PUT — save the draft. Secrets go in; none comes out."""

    http_method_names = ['put', 'options']

    def put(self, request, provider_id):
        provider, company = self.target(request, provider_id)
        data = self.body(request)
        version = data.get('version')
        service.save_draft(
            provider, company, actor=request.user, request=request,
            public=data.get('public', {}), secrets=data.get('secrets', {}),
            version=version if isinstance(version, int) else None,
        )
        return Response(service.describe(provider, company))


class IntegrationActionView(_IntegrationView):
    """POST — test, activate, disable, enable, revoke, or start a draft from the environment."""

    http_method_names = ['post', 'options']

    def post(self, request, provider_id, action):
        provider, company = self.target(request, provider_id)
        data = self.body(request)
        if action == 'test':
            options, errors = {}, {}
            for field in provider.test_fields:
                raw = data.get(field.name)
                if raw is None or raw == '':
                    continue
                try:
                    options[field.name] = field.clean(raw)
                except ValueError as exc:
                    errors[field.name] = str(exc)
            if errors:
                raise ConfigError(errors)
            target = data.get('target') if data.get('target') in ('active', 'draft') else None
            result = service.test(provider, company, actor=request.user, request=request, target=target, **options)
            return Response({**result, 'integration': service.describe(provider, company)})
        if action == 'activate':
            version = data.get('version')
            service.activate(provider, company, actor=request.user, request=request, payload=data,
                             version=version if isinstance(version, int) else None)
        elif action in ('enable', 'disable'):
            service.set_enabled(provider, company, actor=request.user, request=request, enabled=action == 'enable',
                                payload=data)
        elif action == 'revoke':
            if data.get('confirm') != service.REVOKE_WORD:
                raise _Refusal(f'Para revocar esta integración escribe {service.REVOKE_WORD}.')
            service.revoke(provider, company, actor=request.user, request=request, payload=data)
        elif action == 'import-env':
            service.import_env(provider, company, actor=request.user, request=request)
        else:
            raise ApiNotFound('No encontrado.')
        return Response(service.describe(provider, company))
