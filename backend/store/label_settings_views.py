"""
LABEL-02 — a company's label settings (docs/adr-inventory-labels.md D4, D6).

    GET   /api/admin/label-settings/?company=<id>   `company.view`
    PATCH /api/admin/label-settings/?company=<id>   `company.manage`

`unit_code_prefix` (1–4 capital letters, not the product-code prefix) and
`device_symbol` (`internal` | `imei1`). The company comes from the server's
resolution of the caller; `?company=` only selects among the companies the
caller already reaches, and any other answers 404 like one that does not
exist. A change leaves `AdminAuditLog('label_settings_updated')` with the
company.

`imei1` only names what the commercial device label MAY carry. Printing an IMEI
still needs `inventory.labels.sensitive` (`unit_codes.device_label_access`).
"""
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from . import unit_codes
from .models import CompanyLabelSettings
from .settings_views import CAP_COMPANY_MANAGE, CAP_COMPANY_VIEW, _settings_context
from .tenancy import has_capability
from .throttles import AdminUsersThrottle


def _payload(row, *, can_manage: bool) -> dict:
    return {
        'unit_code_prefix': row.unit_code_prefix,
        'device_symbol': row.device_symbol,
        'device_symbol_label': row.get_device_symbol_display(),
        'device_symbols': [{'value': v, 'label': label} for v, label in CompanyLabelSettings.DEVICE_SYMBOL_CHOICES],
        'can_manage': can_manage,
    }


class AdminLabelSettingsView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminUsersThrottle]

    def get(self, request):
        company, _row, error = _settings_context(request, CAP_COMPANY_VIEW)
        if error:
            return error
        return Response(_payload(unit_codes.settings_for(company),
                                 can_manage=has_capability(request.user, company, CAP_COMPANY_MANAGE)))

    def patch(self, request):
        company, _row, error = _settings_context(request, CAP_COMPANY_MANAGE)
        if error:
            return error
        data = request.data if isinstance(request.data, dict) else {}
        wanted = {}
        for field in ('unit_code_prefix', 'device_symbol'):
            if field in data:
                if not isinstance(data[field], str):
                    return Response({field: ['Se envía como texto.']}, status=status.HTTP_400_BAD_REQUEST)
                wanted[field] = data[field].strip()
        try:
            row = unit_codes.update_settings(company, wanted, actor=request.user, request=request)
        except DjangoValidationError as exc:
            return Response(exc.message_dict, status=status.HTTP_400_BAD_REQUEST)
        return Response(_payload(row, can_manage=True))
