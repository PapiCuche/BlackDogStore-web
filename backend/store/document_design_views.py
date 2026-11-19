"""
DOC-DESIGN-03 — the design of a company's internal papers.

    GET   /api/admin/document-design/?company=<id>   `company.view` or `company.manage`
    PATCH /api/admin/document-design/?company=<id>   `company.manage`

`design` is the whitelist of `document_design.FIELDS` and nothing else: no
amount, tax id, legal name, number or legend is an option. `contact` shows the
values the contact switches would print (they are the company's own settings,
edited in Ajustes › Empresa, never copied here), `limits` the text lengths and
`can_manage` whether the caller may change it. DOC-DESIGN-04 adds the closed
choices of its options (`logo_aligns`, `warranty_modes`, `tax_breakdown_modes`,
`densities`, `blocks`), `logo_width_mm`, `max_lines` and `contact.warranty_url`.

The company comes from the server's resolution of the caller (`?company=` only
selects among the companies the caller already reaches; any other answers 404
like one that does not exist). Reading writes nothing (`_company_for`, not
`_settings_context`). A change leaves `AdminAuditLog('document_design_updated')`
with the fields that changed, before and after.

Applied to the sales note (A4 and 80 mm), the order receipt and the thermal
print. NOT to boletas nor facturas: that is pending a decision.

OPTIMISTIC CONCURRENCY (DESIGN-VERIFY). Every answer carries the design's
`version` (also as `ETag`). A PATCH that sends `If-Match: "<version>"` is
applied only if that is still the saved version; otherwise it is a 409
`{"code": "design_conflict", "detail": …, "current": <this payload>}` that
writes and audits nothing, so the panel reloads what the other person saved
and keeps its own changes on top. Without `If-Match` (or with `*`) a PATCH is
applied over whatever is saved, as before. The check runs under the row's lock
(`document_design.update`), after validation and after `company.manage`.
"""
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from . import company_settings, document_design
from .models import CompanyDocumentDesign
from .settings_views import CAP_COMPANY_MANAGE, CAP_COMPANY_VIEW, _company_for
from .tenancy import has_capability
from .throttles import AdminUsersThrottle


def _options(choices) -> list[dict]:
    return [{'value': value, 'label': label} for value, label in choices]


CONFLICT_CODE = 'design_conflict'
CONFLICT_DETAIL = ('Otra persona guardó cambios en el diseño mientras lo editabas. Cargamos lo que guardó y '
                   'mantuvimos tus cambios sin guardar encima: revísalos y vuelve a guardar.')


def _expected_version(request) -> str | None:
    """The version of `If-Match` (`"v"`, `W/"v"` or `v`); `None` without one or with `*`."""
    header = (request.headers.get('If-Match') or '').strip()
    if not header or header == '*':
        return None
    if header.startswith('W/'):
        header = header[2:]
    return header.strip().strip('"')


def _respond(company, design, version, *, can_manage: bool, conflict: bool = False) -> Response:
    """The payload with its `ETag`; a conflict wraps it as `current` under its code and message."""
    payload = _payload(company, design, version, can_manage=can_manage)
    if conflict:
        response = Response({'code': CONFLICT_CODE, 'detail': CONFLICT_DETAIL, 'current': payload},
                            status=status.HTTP_409_CONFLICT)
    else:
        response = Response(payload)
    response['ETag'] = f'"{version}"'
    # Never a cached copy: an old design (and its old version) would only lead to a conflict.
    response['Cache-Control'] = 'no-store'
    return response


def _payload(company, design, version, *, can_manage: bool) -> dict:
    identity = company_settings.company_identity(company)
    model = CompanyDocumentDesign
    return {
        'design': design.as_dict(),
        # DESIGN-VERIFY: what a PATCH sends back as `If-Match` (optimistic concurrency).
        'version': version,
        'logo_sizes': _options(model.LOGO_SIZE_CHOICES),
        # DOC-DESIGN-04: the closed choices of the new options, in the order the panel offers them.
        'logo_aligns': _options(model.ALIGN_CHOICES),
        'warranty_modes': _options(model.WARRANTY_MODE_CHOICES),
        'tax_breakdown_modes': _options(model.TAX_BREAKDOWN_CHOICES),
        'densities': _options(model.DENSITY_CHOICES),
        'blocks': _options(model.BLOCK_CHOICES),
        'logo_width_mm': {'a4': list(document_design.LOGO_A4_WIDTH_MM),
                          'ticket': list(document_design.LOGO_TICKET_WIDTH_MM)},
        'max_lines': document_design.MESSAGE_MAX_LINES,
        'limits': dict(document_design.LIMITS),
        'contact': {
            'phone': identity.phone,
            'email': identity.contact_email,
            'address': ', '.join(part for part in (identity.legal_address, identity.city) if part),
            'website': identity.website_url,
            'warranty_url': identity.warranty_policy_url,
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
        design, version = document_design.saved(company)
        return _respond(company, design, version,
                        can_manage=has_capability(request.user, company, CAP_COMPANY_MANAGE))

    def patch(self, request):
        company, error = _company_for(request, CAP_COMPANY_MANAGE)
        if error:
            return error
        try:
            document_design.update(company, request.data if isinstance(request.data, dict) else None,
                                   actor=request.user, request=request,
                                   expected_version=_expected_version(request))
        except document_design.DesignError as exc:
            return Response(exc.errors, status=status.HTTP_400_BAD_REQUEST)
        except document_design.DesignConflict:
            design, version = document_design.saved(company)
            return _respond(company, design, version, can_manage=True, conflict=True)
        design, version = document_design.saved(company)
        return _respond(company, design, version, can_manage=True)
