"""
P2 · F5c — `POST /api/admin/claims/check/ {text}`: the claims-control warnings of one text (Manual V4 §21).

WHY ONE ENDPOINT AND NOT A FIELD IN EVERY SAVE RESPONSE. The texts live behind six different save endpoints
(models, products, grades, used devices, campaigns, the home page and the service rows), each with its own
response shape and its own tests. Adding `warnings` to all of them would touch every one; and a warning that only
arrives after saving comes too late to help. One read-only endpoint that the panel calls while the person types
changes nothing that exists, and no save path can ever be blocked by it.

WHO MAY ASK. Whoever writes a text the shop publishes: `products.manage` (descriptions, grades), `company.manage`
(campaigns, home page, services) or `inventory.adjust` (the notes of a used device), in the company the server
resolves for the caller. A `?company=` only selects among the caller's own companies. The legacy pilot operator
keeps the catalogue's legacy roles.

READ-ONLY, BOUNDED. Nothing is written or audited (the text is not stored). At most `MAX_TEXT_LENGTH` characters
(413 beyond), and its own per-user rate (`admin_claims_check`).
"""
from __future__ import annotations

from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from . import claims_check
from .admin_views import _LEGACY_MANAGE_CATALOG_ROLES, _requested_company_id
from .permissions import get_user_role
from .tenancy import CATALOG_SOURCE_LEGACY, has_capability, resolve_catalog_company
from .throttles import AdminClaimsCheckThrottle

#: Any one of these lets a person check a text: each writes some text the shop publishes.
CLAIMS_CHECK_CAPABILITIES = ('products.manage', 'company.manage', 'inventory.adjust')

_FORBIDDEN = {'detail': 'No tienes permisos para revisar textos de la tienda.'}


def _may_check(request):
    """None when the caller may check texts; otherwise the error Response."""
    requested_id, ok = _requested_company_id(request)
    if ok is None:
        return Response({'detail': 'Parámetro "company" inválido.'}, status=status.HTTP_400_BAD_REQUEST)
    company, source = resolve_catalog_company(request.user, requested_id)
    if company is None:
        return Response(_FORBIDDEN, status=status.HTTP_403_FORBIDDEN)
    if source == CATALOG_SOURCE_LEGACY:
        allowed = get_user_role(request.user) in _LEGACY_MANAGE_CATALOG_ROLES
    else:
        allowed = any(has_capability(request.user, company, cap) for cap in CLAIMS_CHECK_CAPABILITIES)
    return None if allowed else Response(_FORBIDDEN, status=status.HTTP_403_FORBIDDEN)


class AdminClaimsCheckView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminClaimsCheckThrottle]

    def post(self, request):
        error = _may_check(request)
        if error is not None:
            return error
        data = request.data if isinstance(request.data, dict) else {}
        text = data.get('text')
        if not isinstance(text, str):
            return Response({'detail': 'Falta el texto que se revisa.', 'field': 'text'},
                            status=status.HTTP_400_BAD_REQUEST)
        if len(text) > claims_check.MAX_TEXT_LENGTH:
            return Response({'detail': claims_check.too_long_message(len(text)), 'field': 'text'},
                            status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
        return Response({'warnings': claims_check.find(text)})
