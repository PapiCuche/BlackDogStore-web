"""
Company configuration endpoints — SaaS Phase 3.

TWO SURFACES, TWO DIFFERENT ANSWERS TO "WHO IS ASKING"

  PUBLIC     GET /api/storefront/config/
             Anonymous. The tenant comes from the REQUEST HOST, through the same
             `resolve_storefront_company()` the catalogue and cart use — never
             from a parameter, because a public request has no identity to
             validate one against. Returns a hand-built subset: branding and
             public contact details, and nothing operational.

  INTERNAL   GET/PATCH /api/admin/company-settings/?company=<id>
             `company.view` to read, `company.manage` to write, resolved inside
             the company the caller actually acts on. The `?company=` value is
             untrusted input that can only SELECT among companies the caller
             already reaches.

WHAT THE PUBLIC ENDPOINT MUST NEVER RETURN
------------------------------------------
`order_notification_email`. It is where a tenant's sales alerts go, and
publishing it would hand every visitor an operations inbox to aim at. The public
serializer is written as an explicit dict for that reason: a `ModelSerializer`
with `exclude` would leak every field added after somebody forgot to update it.
"""

import logging

from django.utils.decorators import method_decorator
from django.views.decorators.cache import cache_control
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .company_settings import (
    company_branding,
    company_configuration_status,
    company_identity,
    get_company_settings,
)
from .models import AdminAuditLog, Company, CompanySettings
from django.core.exceptions import ValidationError as DjangoValidationError

from .serializers import (
    CompanyIdentityWriteSerializer,
    CompanySettingsSerializer,
    InternalSequenceSerializer,
    InternalSequenceWriteSerializer,
)
from .tenancy import (
    CrossTenantError,
    NoTenantError,
    has_capability,
    resolve_company_for_user,
    resolve_storefront_company,
)
from .throttles import AdminUsersThrottle

logger = logging.getLogger(__name__)

CAP_COMPANY_VIEW = 'company.view'
CAP_COMPANY_MANAGE = 'company.manage'

_NOT_FOUND = 'Empresa no encontrada o sin acceso.'


# ---------------------------------------------------------------------------
# Public storefront configuration
# ---------------------------------------------------------------------------

def build_storefront_config_payload(company) -> dict:
    """
    The PUBLIC configuration of one storefront.

    Extracted in M5 so the Host-resolved web view and the slug-resolved
    `/api/v1/storefront/<slug>/config/` return byte-identical bodies. Two
    builders would drift, and the drift would be a shop whose app shows one
    phone number and whose website shows another.

    Everything here is commercial identity a customer is meant to read: the name
    the business trades under, its legal name and tax id — both of which appear
    on every boleta and factura it issues — its public contact channels and its
    published policies.

    NOTHING OPERATIONAL. `order_notification_email`, branch configuration,
    payment credentials, capabilities and internal notes are not in this payload
    and must not be added to it: this is the one response the platform serves to
    anyone who asks.
    """
    identity = company_identity(company)
    branding = company_branding(company)
    settings_row = get_company_settings(company)

    return {
        'company': {
            'name': identity.name,
            'slug': company.slug,
            'legal_name': identity.legal_name,
            'tax_id': identity.tax_id,
        },
        'branding': {
            'logo_url': branding.logo_url,
            'colors': branding.colors,
            'css_variables': branding.css_variables(),
        },
        'contact': {
            'email': identity.contact_email,
            'phone': identity.phone,
            'whatsapp_number': identity.whatsapp_number,
            'whatsapp_link': identity.whatsapp_link,
            'website_url': identity.website_url,
            'facebook_url': settings_row.facebook_url if settings_row else '',
            'instagram_url': settings_row.instagram_url if settings_row else '',
            'address': identity.legal_address,
            'city': identity.city,
        },
        'policies': {
            'warranty_text': identity.warranty_policy_text,
            'warranty_url': identity.warranty_policy_url,
            'terms_url': settings_row.terms_url if settings_row else '',
            'privacy_url': settings_row.privacy_url if settings_row else '',
        },
    }


class StorefrontConfigView(APIView):
    """
    GET /api/storefront/config/ — branding and public contact for this storefront.

    Anonymous by design: it is what the shop's own visitors need to render the
    page. An unresolvable host returns 404 rather than a default tenant — serving
    somebody's branding under the wrong domain is exactly the failure the
    storefront resolver exists to prevent.
    """

    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    # Short cache, and VARY ON HOST. The host is what selects the tenant, so a
    # cache keyed without it would serve one company's branding to another's
    # domain — the same bug as a global cache key, one layer down. See §63.
    @method_decorator(cache_control(max_age=60, public=True))
    def get(self, request):
        company = resolve_storefront_company(request)
        if company is None or not company.is_active:
            # A deactivated company keeps its history but does not present a
            # storefront. Same answer as an unknown host: nothing to serve.
            return Response(
                {'detail': 'Tienda no encontrada.'}, status=status.HTTP_404_NOT_FOUND,
            )

        payload = build_storefront_config_payload(company)
        response = Response(payload)
        response['Vary'] = 'Host'
        return response


# ---------------------------------------------------------------------------
# Internal configuration
# ---------------------------------------------------------------------------

def _settings_context(request, capability):
    """
    Resolve the company this configuration request acts on and authorise it.

    Returns `(company, settings_row, error_response)`. The `?company=` value is
    untrusted: `resolve_company_for_user` only ever uses it to select among
    companies the caller already reaches, so it can never widen access. A company
    the caller cannot see answers exactly like one that does not exist.
    """
    raw = request.query_params.get('company')
    requested_id = None
    if raw not in (None, ''):
        try:
            requested_id = int(raw)
        except (TypeError, ValueError):
            return None, None, Response(
                {'detail': 'Parámetro "company" inválido.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

    try:
        company = resolve_company_for_user(request.user, requested_id)
    except CrossTenantError:
        return None, None, Response(
            {'detail': _NOT_FOUND}, status=status.HTTP_404_NOT_FOUND,
        )
    except NoTenantError as exc:
        return None, None, Response(
            {'detail': str(exc)}, status=status.HTTP_403_FORBIDDEN,
        )

    if not has_capability(request.user, company, capability):
        return None, None, Response(
            {'detail': 'No tienes permisos sobre la configuración de esta empresa.'},
            status=status.HTTP_403_FORBIDDEN,
        )

    settings_row = get_company_settings(company)
    if settings_row is None:
        # A tenant created before Phase 3 or outside provisioning. Create the row
        # on demand rather than 500-ing: it is empty configuration, not data, and
        # refusing to show a settings screen because the settings row is missing
        # would be a dead end with no way out from the UI.
        from .company_settings import NEUTRAL_THEME
        settings_row = CompanySettings.objects.create(
            company=company, currency='PEN', **NEUTRAL_THEME,
        )

    return company, settings_row, None


class AdminCompanySettingsView(APIView):
    """
    GET   /api/admin/company-settings/?company=<id>  — `company.view`
    PATCH /api/admin/company-settings/?company=<id>  — `company.manage`

    PATCH accepts the settings fields AND the three identity fields that live on
    `Company` (`name`, `legal_name`, `tax_id`), because from the business's point
    of view those are the same screen. It does NOT accept `slug` or `is_active`:
    those are routing and platform decisions, and they are not reachable from
    here at all — not excluded by a serializer, simply absent from both writers.
    """

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminUsersThrottle]

    def get(self, request):
        company, settings_row, error = _settings_context(request, CAP_COMPANY_VIEW)
        if error:
            return error

        from .tenancy import company_fulfillment_branch

        branch = company_fulfillment_branch(company)
        return Response({
            'company': {
                'id': company.pk,
                'name': company.name,
                'legal_name': company.legal_name,
                'tax_id': company.tax_id,
                'slug': company.slug,
                'is_active': company.is_active,
            },
            'settings': CompanySettingsSerializer(settings_row).data,
            'fulfillment_branch': (
                None if branch is None else {'id': branch.pk, 'name': branch.name}
            ),
            'status': company_configuration_status(company),
            'can_manage': has_capability(request.user, company, CAP_COMPANY_MANAGE),
        })

    def patch(self, request):
        company, settings_row, error = _settings_context(request, CAP_COMPANY_MANAGE)
        if error:
            return error

        payload = request.data if isinstance(request.data, dict) else {}

        # Identity lives on Company; the rest on CompanySettings. Validate BOTH
        # before writing EITHER, so a rejected colour cannot leave the name
        # already changed.
        identity_ser = CompanyIdentityWriteSerializer(
            data={k: v for k, v in payload.items()
                  if k in {'name', 'legal_name', 'tax_id'}},
            partial=True,
        )
        identity_ser.is_valid(raise_exception=True)

        settings_ser = CompanySettingsSerializer(
            settings_row, data=payload, partial=True,
        )
        settings_ser.is_valid(raise_exception=True)

        changed = []

        for field, value in identity_ser.validated_data.items():
            if getattr(company, field) != value:
                setattr(company, field, value)
                changed.append(field)
        if changed:
            company.save(update_fields=[*changed, 'updated_at'])

        settings_changed = [
            field for field, value in settings_ser.validated_data.items()
            if getattr(settings_row, field) != value
        ]
        if settings_changed:
            settings_ser.save()
            changed.extend(settings_changed)

        if changed:
            AdminAuditLog.log(
                actor=request.user,
                action='company_settings_updated',
                target_type='company',
                target_id=company.pk,
                # FIELD NAMES ONLY, never values. The point of the log is who
                # changed what and when; copying a warranty policy or an address
                # into every audit row would bloat it and put the same data in a
                # second place for no gain. Logo changes record that the logo
                # changed — never the image, never a data URI.
                metadata={
                    'company_id': company.pk,
                    'changed_fields': sorted(set(changed)),
                },
                request=request,
                company=company,
            )

        company.refresh_from_db()
        settings_row.refresh_from_db()
        return self.get(request)


# ---------------------------------------------------------------------------
# Internal document sequences — SaaS Phase 2E
# ---------------------------------------------------------------------------
#
# WHY THESE ARE NOT PART OF THE CompanySettings PATCH
#
# `prefix` and `padding` are configuration and could have gone there. `next_value`
# could not: it is TRANSACTIONAL STATE, the same counter an issuance locks and
# increments. Putting a counter inside a generic settings PATCH would mean every
# save of an unrelated field carried the power to move it, and a stale form
# reloaded and re-saved would silently rewind numbering.
#
# So the whole series lives behind its own endpoint, where the rules that only
# apply to a counter — read-only after the first issuance, scope frozen after the
# first document — have somewhere to live.

class AdminSequenceListView(APIView):
    """
    GET /api/admin/sequences/?company=<id> — this company's series.

    Returns the company-level series plus, under branch scope, one row per branch
    the CALLER may operate. A user restricted to two of five shops sees two
    counters, not five: the same rule Phase 2D applies to stock applies to the
    numbers that stock generates.
    """

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminUsersThrottle]

    def get(self, request):
        company, _settings_row, error = _settings_context(request, CAP_COMPANY_VIEW)
        if error:
            return error

        from .models import InternalSequence
        from .sequences import can_change_scope, ensure_company_sequence, sequence_scope
        from .tenancy import visible_branches

        # Created on read if absent: a company that predates Phase 2E, or one
        # created outside provisioning, must still be configurable. Idempotent.
        ensure_company_sequence(company)

        scope = sequence_scope(company)
        rows = InternalSequence.objects.filter(company=company).select_related('branch')

        if scope == CompanySettings.SEQUENCE_SCOPE_BRANCH:
            visible = set(
                visible_branches(request.user, company).values_list('pk', flat=True)
            )
            rows = [r for r in rows if r.branch_id is None or r.branch_id in visible]
        else:
            # Under company scope the branch rows are dormant leftovers from a
            # previous configuration. Showing them would suggest they are in use.
            rows = [r for r in rows if r.branch_id is None]

        return Response({
            'scope': scope,
            'can_change_scope': can_change_scope(company),
            'can_manage': has_capability(request.user, company, CAP_COMPANY_MANAGE),
            'results': InternalSequenceSerializer(rows, many=True).data,
            # Restated on every response because the UI shows numbers that look
            # official and are not.
            'notice': (
                'Numeración interna. No es numeración fiscal ni una serie SUNAT.'
            ),
        })


class AdminSequenceDetailView(APIView):
    """
    PATCH /api/admin/sequences/{pk}/ — prefix, padding, next_value, is_active.

    Requires `company.manage`. Issuing a sales note requires the commercial
    capability instead: being allowed to hand out the next number is not being
    allowed to decide what the numbers look like.
    """

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminUsersThrottle]

    def _scoped(self, request, pk):
        """
        The series, if it belongs to a company the caller may configure.

        Resolved by walking DOWN from the caller's companies rather than by
        loading the row and checking it afterwards: a series id from another
        tenant is not rejected by a check that could be forgotten, it is simply
        not in the set being searched.
        """
        from .models import InternalSequence
        from .tenancy import visible_companies

        sequence = (
            InternalSequence.objects
            .filter(company__in=visible_companies(request.user))
            .select_related('company', 'branch')
            .filter(pk=pk)
            .first()
        )
        if sequence is None:
            return None, Response(
                {'detail': _NOT_FOUND}, status=status.HTTP_404_NOT_FOUND,
            )
        if not has_capability(request.user, sequence.company, CAP_COMPANY_MANAGE):
            return None, Response(
                {'detail': 'Se requiere rol de administrador de la empresa.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        return sequence, None

    def get(self, request, pk):
        sequence, error = self._scoped(request, pk)
        if error:
            return error
        return Response(InternalSequenceSerializer(sequence).data)

    def patch(self, request, pk):
        from .sequences import can_edit_next_value

        sequence, error = self._scoped(request, pk)
        if error:
            return error

        ser = InternalSequenceWriteSerializer(data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data

        # THE COUNTER IS READ-ONLY ONCE IT HAS ISSUED.
        #
        # Before the first document it is genuinely useful — a business migrating
        # from another system starts at 5001 instead of 1. Afterwards, moving it
        # backwards reissues identifiers that are already on paper, and moving it
        # forwards is a gap somebody will have to explain. Rejected loudly rather
        # than ignored: a form that appears to save and does not is worse.
        if 'next_value' in data and data['next_value'] != sequence.next_value:
            if not can_edit_next_value(sequence):
                return Response(
                    {
                        'next_value': [
                            'Esta serie ya emitió documentos, así que su próximo '
                            'número no puede modificarse. Cambiarlo reasignaría '
                            'identificadores que ya están en documentos emitidos.',
                        ],
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

        changed = []
        for field in ('prefix', 'padding', 'next_value', 'is_active'):
            if field in data and getattr(sequence, field) != data[field]:
                setattr(sequence, field, data[field])
                changed.append(field)

        if changed:
            try:
                # ONLY the fields that actually changed.
                #
                # A full save() writes `next_value` as it was read at the start of
                # this request. A note issued in the meantime — the settings page
                # is open while the shop is selling — would have its counter
                # rewound and the next document would reuse an ordinal already on
                # paper. The database constraint would refuse that write, which is
                # a 500 for whoever was making the sale.
                sequence.save(update_fields=[*changed, 'updated_at'])
            except DjangoValidationError as exc:
                return Response(exc.message_dict, status=status.HTTP_400_BAD_REQUEST)

            AdminAuditLog.log(
                actor=request.user,
                action='sequence_updated',
                target_type='internal_sequence',
                target_id=sequence.pk,
                metadata={
                    'company_id': sequence.company_id,
                    'branch_id': sequence.branch_id,
                    'document_type': sequence.document_type,
                    'changed_fields': sorted(changed),
                },
                request=request,
                company=sequence.company,
            )

        sequence.refresh_from_db()
        return Response(InternalSequenceSerializer(sequence).data)


class AdminSequenceScopeView(APIView):
    """
    PATCH /api/admin/sequences/scope/?company=<id> — company or branch numbering.

    FROZEN AFTER THE FIRST DOCUMENT, and this is a deliberate v1 limit rather
    than a missing feature.

    Switching is not a database problem — every ordinal stays unique inside its
    own series. It is a legibility one: a company that issued NV-000001..000050
    at company scope and then switched would see its next branch note numbered
    NV-000001 again. One business showing the same identifier on two documents is
    exactly what an internal correlativo exists to prevent.

    Doing it properly means deciding what happens to the numbers already out
    there, which needs a business answer this phase does not have. Recorded as
    PENDING in docs/saas-multiempresa.md.
    """

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminUsersThrottle]

    def patch(self, request):
        company, settings_row, error = _settings_context(request, CAP_COMPANY_MANAGE)
        if error:
            return error

        from .sequences import can_change_scope
        from .serializers import SequenceScopeSerializer

        ser = SequenceScopeSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        scope = ser.validated_data['scope']

        # Asking for the scope it already has is not a change, so it does not
        # need to pass the "no documents issued yet" gate.
        if scope == settings_row.sales_note_sequence_scope:
            return Response({'scope': scope, 'changed': False})

        if not can_change_scope(company):
            return Response(
                {
                    'scope': [
                        'Esta empresa ya emitió notas de venta, así que el alcance '
                        'de su numeración no puede cambiarse. Cambiarlo haría que '
                        'una nota nueva repitiera un número ya emitido.',
                    ],
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        settings_row.sales_note_sequence_scope = scope
        settings_row.save(update_fields=['sales_note_sequence_scope', 'updated_at'])

        AdminAuditLog.log(
            actor=request.user,
            action='sequence_scope_changed',
            target_type='company',
            target_id=company.pk,
            metadata={'company_id': company.pk, 'scope': scope},
            request=request,
            company=company,
        )
        return Response({'scope': scope, 'changed': True})
