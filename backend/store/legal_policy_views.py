"""
LEGAL-POLICIES-01 — admin editor and public reading of the three legal documents.

ADMIN (Configuración › Políticas legales), with the SAME authority as the rest of
the company settings (`AdminCompanySettingsView`):
    GET   /api/admin/legal-policies/                    company.view
    PUT   /api/admin/legal-policies/<kind>/             company.manage + company-wide scope
    POST  /api/admin/legal-policies/<kind>/publish/     company.manage + company-wide scope
    POST  /api/admin/legal-policies/<kind>/unpublish/   company.manage + company-wide scope
The company comes from the session (`?company=` only selects among the caller's own).

PUBLIC (anonymous):
    GET   /api/storefront/legal/<kind>/   the published text of the company this HOST
          serves; 404 when there is none. Short cache, `Vary: Host`, like the config.
"""

from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.decorators import method_decorator
from django.views.decorators.cache import cache_control
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from . import legal_policy_services as legal
from .settings_views import CAP_COMPANY_MANAGE, CAP_COMPANY_VIEW, _company_for, _require_company_wide_scope
from .tenancy import has_capability, resolve_storefront_company
from .throttles import AdminUsersThrottle


def _row_payload(kind, row):
    return {
        'kind': kind,
        'draft_text': row.draft_text if row else '',
        'published_text': row.published_text if row else '',
        'is_published': bool(row and row.published_text.strip()),
        'published_at': row.published_at.isoformat() if row and row.published_at else None,
        'has_unpublished_changes': bool(row and row.draft_text != row.published_text),
        'updated_at': row.updated_at.isoformat() if row else None,
    }


def _error(exc: DjangoValidationError):
    """`{detail, errors}`, the shape the admin screens already read for field errors."""
    errors = exc.message_dict if hasattr(exc, 'error_dict') else {'text': exc.messages}
    detail = ' '.join(message for messages in errors.values() for message in messages)
    return Response({'detail': detail, 'errors': errors}, status=status.HTTP_400_BAD_REQUEST)


class AdminLegalPolicyListView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminUsersThrottle]

    def get(self, request):
        company, error = _company_for(request, CAP_COMPANY_VIEW)
        if error:
            return error
        rows = legal.policies_for(company)
        return Response({
            'company': {'id': company.pk, 'name': company.name},
            'max_length': legal.MAX_LENGTH,
            'can_manage': has_capability(request.user, company, CAP_COMPANY_MANAGE),
            'policies': [_row_payload(kind, rows[kind]) for kind in legal.KINDS],
        })


class _LegalWrite(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminUsersThrottle]

    def _context(self, request, kind):
        if kind not in legal.KINDS:
            return None, Response({'detail': 'Documento legal desconocido.'}, status=status.HTTP_404_NOT_FOUND)
        company, error = _company_for(request, CAP_COMPANY_MANAGE)
        if error:
            return None, error
        error = _require_company_wide_scope(request, company)
        if error:
            return None, error
        return company, None


class AdminLegalPolicyDetailView(_LegalWrite):
    def put(self, request, kind):
        company, error = self._context(request, kind)
        if error:
            return error
        payload = request.data if isinstance(request.data, dict) else {}
        if 'draft_text' not in payload:
            return _error(DjangoValidationError({'text': 'Falta el texto.'}))
        try:
            row = legal.save_draft(company, kind, payload.get('draft_text'), actor=request.user, request=request)
        except DjangoValidationError as exc:
            return _error(exc)
        return Response(_row_payload(kind, row))


class AdminLegalPolicyPublishView(_LegalWrite):
    def post(self, request, kind):
        company, error = self._context(request, kind)
        if error:
            return error
        try:
            row = legal.publish(company, kind, actor=request.user, request=request)
        except DjangoValidationError as exc:
            return _error(exc)
        return Response(_row_payload(kind, row))


class AdminLegalPolicyUnpublishView(_LegalWrite):
    def post(self, request, kind):
        company, error = self._context(request, kind)
        if error:
            return error
        row = legal.unpublish(company, kind, actor=request.user, request=request)
        return Response(_row_payload(kind, row))


class PublicLegalPolicyView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    @method_decorator(cache_control(max_age=60, public=True))
    def get(self, request, kind):
        company = resolve_storefront_company(request)
        if company is None or not company.is_active:
            response = Response({'detail': 'Tienda no encontrada.'}, status=status.HTTP_404_NOT_FOUND)
        else:
            policy = legal.public_policy(company, kind)
            response = (Response(policy) if policy else
                        Response({'detail': 'Documento no publicado.'}, status=status.HTTP_404_NOT_FOUND))
        response['Vary'] = 'Host'
        return response
