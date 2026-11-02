"""
DOC-DESIGN-03 — the design of a company's internal papers.

    GET   /api/admin/document-design/?company=<id>   `company.view` or `company.manage`
    PATCH /api/admin/document-design/?company=<id>   `company.manage`

`design` is the whitelist of `document_design.FIELDS` and nothing else: no
amount, tax id, legal name, number or legend is an option. `contact` shows the
values the contact switches would print (they are the company's own settings,
edited in Ajustes › Empresa, never copied here), `limits` the text lengths and
`can_manage` whether the caller may change it.

The company comes from the server's resolution of the caller (`?company=` only
selects among the companies the caller already reaches; any other answers 404
like one that does not exist). Reading writes nothing (`_company_for`, not
`_settings_context`). A change leaves `AdminAuditLog('document_design_updated')`
with the fields that changed, before and after.

Applied to the sales note (A4 and 80 mm), the order receipt and the thermal
print. NOT to boletas nor facturas: that is pending a decision.
"""
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from . import company_settings, document_design
from .models import CompanyDocumentDesign
from .settings_views import CAP_COMPANY_MANAGE, CAP_COMPANY_VIEW, _company_for
from .tenancy import has_capability
from .throttles import AdminUsersThrottle


def _payload(company, design, *, can_manage: bool) -> dict:
    identity = company_settings.company_identity(company)
    return {
        'design': design.as_dict(),
        'logo_sizes': [{'value': value, 'label': label} for value, label in CompanyDocumentDesign.LOGO_SIZE_CHOICES],
        'limits': dict(document_design.LIMITS),
        'contact': {
            'phone': identity.phone,
            'email': identity.contact_email,
            'address': ', '.join(part for part in (identity.legal_address, identity.city) if part),
            'website': identity.website_url,
        },
        'can_manage': can_manage,
    }


class AdminDocumentDesignView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminUsersThrottle]

    def get(self, request):
        company, error = _company_for(request, CAP_COMPANY_VIEW)
        if error is not None and error.status_code == status.HTTP_403_FORBIDDEN:
            # Whoever may change the design may read it, even without `company.view`.
            managed, refused = _company_for(request, CAP_COMPANY_MANAGE)
            if refused is None:
                company, error = managed, None
        if error:
            return error
        return Response(_payload(company, document_design.design_for(company),
                                 can_manage=has_capability(request.user, company, CAP_COMPANY_MANAGE)))

    def patch(self, request):
        company, error = _company_for(request, CAP_COMPANY_MANAGE)
        if error:
            return error
        try:
            design = document_design.update(company, request.data if isinstance(request.data, dict) else None,
                                            actor=request.user, request=request)
        except document_design.DesignError as exc:
            return Response(exc.errors, status=status.HTTP_400_BAD_REQUEST)
        return Response(_payload(company, design, can_manage=True))
