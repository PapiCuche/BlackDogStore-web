"""
INV-SWITCH-01 — Configuración › Inventario › Inteligencia artificial.

GET   /api/admin/inventory/ai-settings/ — the company's switch and what it means
      today. `settings.view`.
PATCH — turn it on or off with a literal boolean. `settings.manage` with the
      whole company in scope (the switch is every branch's). Audited.

The panel's switch is not the control: `inventory_ai.decide` asks again, on the
server, on every import. Nothing here names a provider, a model or a key.
"""
from django.conf import settings
from django.db import transaction
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from . import inventory_ai
from .admin_views import _LEGACY_MANAGE_CATALOG_ROLES, _LEGACY_VIEW_CATALOG_ROLES, _company_context, _holds
from .models import AdminAuditLog, InventoryAISettings
from .tenancy import has_company_wide_scope

CAP_SETTINGS_VIEW = 'settings.view'
CAP_SETTINGS_MANAGE = 'settings.manage'
_NEEDS_WHOLE_COMPANY = 'La IA del inventario es de toda la empresa: tu acceso está limitado a algunas sucursales.'


def _may_manage(user, company) -> bool:
    return _holds(user, company, CAP_SETTINGS_MANAGE, _LEGACY_MANAGE_CATALOG_ROLES) and has_company_wide_scope(user, company)


def _payload(user, company) -> dict:
    state = inventory_ai.state(company, user)
    return {
        'enabled': inventory_ai.company_enabled(company),
        'global_enabled': bool(settings.INVENTORY_AI_ENABLED),
        'state': state,
        'state_label': inventory_ai.STATE_LABELS[state],
        'can_manage': _may_manage(user, company),
    }


class AdminInventoryAISettingsView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        company, error = _company_context(request, CAP_SETTINGS_VIEW, _LEGACY_VIEW_CATALOG_ROLES)
        if error:
            return error
        return Response(_payload(request.user, company))

    def patch(self, request):
        company, error = _company_context(request, CAP_SETTINGS_MANAGE, _LEGACY_MANAGE_CATALOG_ROLES)
        if error:
            return error
        if not has_company_wide_scope(request.user, company):
            return Response({'detail': _NEEDS_WHOLE_COMPANY}, status=status.HTTP_403_FORBIDDEN)
        enabled = request.data.get('enabled') if isinstance(request.data, dict) else None
        if not isinstance(enabled, bool):
            return Response({'detail': 'Indica «enabled» como verdadero o falso.', 'code': 'enabled_invalid'},
                            status=status.HTTP_400_BAD_REQUEST)
        with transaction.atomic():
            row, _ = InventoryAISettings.objects.select_for_update().get_or_create(company=company)
            previous = row.enabled
            if previous != enabled:
                row.enabled, row.updated_by = enabled, request.user
                row.save(update_fields=['enabled', 'updated_by', 'updated_at'])
                AdminAuditLog.log(
                    actor=request.user, action='inventory_ai_setting_changed', target_type='company',
                    target_id=company.pk, metadata={'enabled': enabled, 'previous': previous},
                    request=request, company=company,
                )
        return Response(_payload(request.user, company))
