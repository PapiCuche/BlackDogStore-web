"""
Configurable areas, roles and role assignments — SaaS Phase 2A.1.

Isolation model (same rules as tenant_views.py):
  - Reads are SCOPED to the caller's companies; a caller with no membership gets
    an empty view, never the unfiltered one.
  - Every id in a payload is UNTRUSTED and re-validated against the caller's own
    tenant before anything is written.
  - A company the caller cannot see answers exactly like one that does not exist.

Anti-escalation:
  A company administrator may only put into a role capabilities they themselves
  hold. Without that rule a limited administrator could author a powerful role,
  assign it to themselves and escalate. A platform master is exempt.

Nothing here can write `User.is_superuser`, `User.is_staff` or `UserProfile.role`.
"""

import logging

from django.db.models import Count, Q
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .capabilities import serialise_catalog
from .models import (
    AdminAuditLog, Company, CompanyArea, CompanyRole, Membership,
    MembershipRoleAssignment,
)
from .permissions import HasCompanyMembership
from .serializers import (
    CompanyAreaSerializer,
    CompanyRoleSerializer,
    MembershipRoleAssignmentSerializer,
    MembershipRoleAssignmentUpdateSerializer,
    MembershipRoleAssignmentWriteSerializer,
)
from .tenancy import (
    CrossTenantError,
    NoTenantError,
    active_memberships,
    can_delegate_capabilities,
    has_capability,
    is_platform_admin,
    resolve_capabilities,
    resolve_company_for_user,
    scope_queryset,
    user_areas,
    visible_companies,
)
from .throttles import AdminInventoryReportsThrottle, AdminUsersThrottle

logger = logging.getLogger(__name__)

_NOT_FOUND = 'No encontrado o sin acceso.'
_NEEDS_AUTHORITY = 'No tienes autoridad para esta acción en la empresa indicada.'
_CANNOT_DELEGATE = (
    'No puedes otorgar a un rol capacidades que tú mismo no tienes. '
    'Pide a un administrador con esas capacidades que lo haga.'
)

CAP_AREAS_MANAGE = 'areas.manage'
CAP_ROLES_MANAGE = 'roles.manage'
CAP_MEMBERSHIPS_MANAGE = 'memberships.manage'


def _company_or_none(request, company_id):
    """Resolve an untrusted company id against what the caller can actually see."""
    if company_id is None:
        return None
    return visible_companies(request.user).filter(pk=company_id).first()


def _deny_not_found():
    return Response({'detail': _NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)


def _deny_authority():
    return Response({'detail': _NEEDS_AUTHORITY}, status=status.HTTP_403_FORBIDDEN)


# ---------------------------------------------------------------------------
# Capability catalogue (read-only)
# ---------------------------------------------------------------------------

class CapabilityCatalogView(APIView):
    """
    GET /api/admin/capabilities/ — the platform's capability catalogue.

    Read-only by design: tenants choose FROM this list, they never extend it.
    Also reports which of them the caller currently holds, so a UI can grey out
    what the caller may not delegate.
    """

    permission_classes = [permissions.IsAuthenticated, HasCompanyMembership]
    throttle_classes = [AdminUsersThrottle]

    def get(self, request):
        company = _company_or_none(request, request.query_params.get('company'))
        held = sorted(resolve_capabilities(request.user, company)) if company else []
        return Response({
            'capabilities': serialise_catalog(),
            'held_by_me': held,
            'is_platform_admin': is_platform_admin(request.user),
        })


# ---------------------------------------------------------------------------
# Areas
# ---------------------------------------------------------------------------

class AdminAreaListView(APIView):
    """
    GET  /api/admin/areas/  — areas of the caller's companies.
    POST /api/admin/areas/ — create one. Requires `areas.manage` in that company.
    """

    permission_classes = [permissions.IsAuthenticated, HasCompanyMembership]
    throttle_classes = [AdminUsersThrottle]

    def get(self, request):
        qs = scope_queryset(
            CompanyArea.objects.select_related('company'), request.user,
        ).annotate(
            member_count=Count(
                'role_assignments', filter=Q(role_assignments__is_active=True), distinct=True,
            )
        )

        company_id = request.query_params.get('company')
        if company_id:
            try:
                qs = qs.filter(company_id=int(company_id))
            except (ValueError, TypeError):
                return Response(
                    {'detail': 'Parámetro "company" inválido.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        return Response({'results': CompanyAreaSerializer(qs, many=True).data, 'count': qs.count()})

    def post(self, request):
        ser = CompanyAreaSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        company = _company_or_none(request, ser.validated_data['company'].pk)
        if not company:
            return _deny_not_found()
        if not has_capability(request.user, company, CAP_AREAS_MANAGE):
            return _deny_authority()

        area = ser.save()
        AdminAuditLog.log(
            actor=request.user, action='area_created', target_type='company_area',
            target_id=area.pk,
            metadata={'area_id': area.pk, 'company_id': company.pk,
                      'name': area.name, 'slug': area.slug},
            request=request, company=company,
        )
        return Response(CompanyAreaSerializer(area).data, status=status.HTTP_201_CREATED)


class AdminAreaDetailView(APIView):
    """GET / PATCH /api/admin/areas/{pk}/"""

    permission_classes = [permissions.IsAuthenticated, HasCompanyMembership]
    throttle_classes = [AdminUsersThrottle]

    def _scoped(self, request, pk):
        return scope_queryset(
            CompanyArea.objects.select_related('company'), request.user,
        ).filter(pk=pk).first()

    def get(self, request, pk):
        area = self._scoped(request, pk)
        if not area:
            return _deny_not_found()
        return Response(CompanyAreaSerializer(area).data)

    def patch(self, request, pk):
        area = self._scoped(request, pk)
        if not area:
            return _deny_not_found()
        if not has_capability(request.user, area.company, CAP_AREAS_MANAGE):
            return _deny_authority()

        ser = CompanyAreaSerializer(area, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        updated = ser.save()

        AdminAuditLog.log(
            actor=request.user, action='area_updated', target_type='company_area',
            target_id=updated.pk,
            metadata={'area_id': updated.pk, 'company_id': updated.company_id,
                      'name': updated.name, 'is_active': updated.is_active},
            request=request, company=updated.company,
        )
        return Response(CompanyAreaSerializer(updated).data)


# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------

class AdminRoleListView(APIView):
    """
    GET  /api/admin/roles/  — roles of the caller's companies.
    POST /api/admin/roles/ — create one. Requires `roles.manage`, and the caller
                             may only delegate capabilities they hold.
    """

    permission_classes = [permissions.IsAuthenticated, HasCompanyMembership]
    throttle_classes = [AdminUsersThrottle]

    def get(self, request):
        qs = scope_queryset(
            CompanyRole.objects.select_related('company'), request.user,
        ).annotate(
            assignment_count=Count(
                'assignments', filter=Q(assignments__is_active=True), distinct=True,
            )
        )

        company_id = request.query_params.get('company')
        if company_id:
            try:
                qs = qs.filter(company_id=int(company_id))
            except (ValueError, TypeError):
                return Response(
                    {'detail': 'Parámetro "company" inválido.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        return Response({'results': CompanyRoleSerializer(qs, many=True).data, 'count': qs.count()})

    def post(self, request):
        ser = CompanyRoleSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        company = _company_or_none(request, ser.validated_data['company'].pk)
        if not company:
            return _deny_not_found()
        if not has_capability(request.user, company, CAP_ROLES_MANAGE):
            return _deny_authority()

        capabilities = ser.validated_data.get('capabilities', [])
        if not can_delegate_capabilities(request.user, company, capabilities):
            return Response({'detail': _CANNOT_DELEGATE}, status=status.HTTP_403_FORBIDDEN)

        role = ser.save()
        AdminAuditLog.log(
            actor=request.user, action='company_role_created', target_type='company_role',
            target_id=role.pk,
            metadata={'role_id': role.pk, 'company_id': company.pk,
                      'name': role.name, 'capabilities': sorted(role.capabilities or [])},
            request=request, company=company,
        )
        return Response(CompanyRoleSerializer(role).data, status=status.HTTP_201_CREATED)


class AdminRoleDetailView(APIView):
    """GET / PATCH /api/admin/roles/{pk}/"""

    permission_classes = [permissions.IsAuthenticated, HasCompanyMembership]
    throttle_classes = [AdminUsersThrottle]

    def _scoped(self, request, pk):
        return scope_queryset(
            CompanyRole.objects.select_related('company'), request.user,
        ).filter(pk=pk).first()

    def get(self, request, pk):
        role = self._scoped(request, pk)
        if not role:
            return _deny_not_found()
        return Response(CompanyRoleSerializer(role).data)

    def patch(self, request, pk):
        role = self._scoped(request, pk)
        if not role:
            return _deny_not_found()
        if not has_capability(request.user, role.company, CAP_ROLES_MANAGE):
            return _deny_authority()

        ser = CompanyRoleSerializer(role, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)

        before = sorted(role.capabilities or [])
        capabilities_changed = 'capabilities' in ser.validated_data
        if capabilities_changed:
            requested = ser.validated_data['capabilities']
            # Both the capabilities being ADDED and those already present must be
            # within the caller's own authority: otherwise an admin could keep a
            # role they cannot fully delegate alive while editing around it.
            if not can_delegate_capabilities(
                request.user, role.company, set(requested) | set(before),
            ):
                return Response({'detail': _CANNOT_DELEGATE}, status=status.HTTP_403_FORBIDDEN)

        updated = ser.save()
        action = 'role_permissions_updated' if capabilities_changed else 'company_role_updated'
        AdminAuditLog.log(
            actor=request.user, action=action, target_type='company_role',
            target_id=updated.pk,
            metadata={
                'role_id': updated.pk, 'company_id': updated.company_id,
                'name': updated.name, 'is_active': updated.is_active,
                'capabilities_before': before,
                'capabilities_after': sorted(updated.capabilities or []),
            },
            request=request, company=updated.company,
        )
        return Response(CompanyRoleSerializer(updated).data)


# ---------------------------------------------------------------------------
# Role assignments
# ---------------------------------------------------------------------------

class AdminRoleAssignmentListView(APIView):
    """
    GET  /api/admin/membership-role-assignments/
    POST /api/admin/membership-role-assignments/

    Requires `memberships.manage` in the target company. The membership, role and
    area must all belong to that same company — a valid id from another tenant is
    rejected, not honoured.
    """

    permission_classes = [permissions.IsAuthenticated, HasCompanyMembership]
    throttle_classes = [AdminUsersThrottle]

    def _scoped_qs(self, request):
        return scope_queryset(
            MembershipRoleAssignment.objects.select_related(
                'membership__user', 'membership__company', 'role', 'area',
            ),
            request.user,
            company_field='membership__company',
        )

    def get(self, request):
        qs = self._scoped_qs(request)

        membership_id = request.query_params.get('membership')
        if membership_id:
            try:
                qs = qs.filter(membership_id=int(membership_id))
            except (ValueError, TypeError):
                return Response(
                    {'detail': 'Parámetro "membership" inválido.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        return Response({
            'results': MembershipRoleAssignmentSerializer(qs, many=True).data,
            'count': qs.count(),
        })

    def post(self, request):
        ser = MembershipRoleAssignmentWriteSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data

        membership = scope_queryset(
            Membership.objects.select_related('company'), request.user,
        ).filter(pk=data['membership']).first()
        if not membership:
            return _deny_not_found()

        company = membership.company
        if not has_capability(request.user, company, CAP_MEMBERSHIPS_MANAGE):
            return _deny_authority()

        # The role must belong to the SAME company as the membership. Scoping by
        # the caller's companies is not enough when the caller sees several.
        role = CompanyRole.objects.filter(pk=data['role'], company=company).first()
        if not role:
            return _deny_not_found()

        # Assigning a role hands over its capabilities, so the same delegation
        # limit applies as when authoring one.
        if not can_delegate_capabilities(request.user, company, role.capability_set):
            return Response({'detail': _CANNOT_DELEGATE}, status=status.HTTP_403_FORBIDDEN)

        area = None
        if data.get('area'):
            area = CompanyArea.objects.filter(pk=data['area'], company=company).first()
            if not area:
                return _deny_not_found()

        if MembershipRoleAssignment.objects.filter(
            membership=membership, role=role, area=area,
        ).exists():
            return Response(
                {'detail': 'Esa asignación ya existe.'}, status=status.HTTP_400_BAD_REQUEST
            )

        assignment = MembershipRoleAssignment.objects.create(
            membership=membership, role=role, area=area,
            is_active=data.get('is_active', True), assigned_by=request.user,
        )

        AdminAuditLog.log(
            actor=request.user, action='role_assignment_created',
            target_type='membership_role_assignment', target_id=assignment.pk,
            metadata={
                'assignment_id': assignment.pk, 'company_id': company.pk,
                'membership_id': membership.pk, 'role_id': role.pk,
                'role_name': role.name, 'area_id': area.pk if area else None,
            },
            request=request, company=company,
        )
        return Response(
            MembershipRoleAssignmentSerializer(assignment).data,
            status=status.HTTP_201_CREATED,
        )


class AdminRoleAssignmentDetailView(APIView):
    """
    GET / PATCH / DELETE /api/admin/membership-role-assignments/{pk}/

    DELETE deactivates rather than destroys: an assignment is part of the
    company's access history, and PROTECT on `role` means a hard delete would be
    refused anyway once the role is in use elsewhere.
    """

    permission_classes = [permissions.IsAuthenticated, HasCompanyMembership]
    throttle_classes = [AdminUsersThrottle]

    def _scoped(self, request, pk):
        return scope_queryset(
            MembershipRoleAssignment.objects.select_related(
                'membership__user', 'membership__company', 'role', 'area',
            ),
            request.user,
            company_field='membership__company',
        ).filter(pk=pk).first()

    def get(self, request, pk):
        assignment = self._scoped(request, pk)
        if not assignment:
            return _deny_not_found()
        return Response(MembershipRoleAssignmentSerializer(assignment).data)

    def patch(self, request, pk):
        assignment = self._scoped(request, pk)
        if not assignment:
            return _deny_not_found()

        company = assignment.membership.company
        if not has_capability(request.user, company, CAP_MEMBERSHIPS_MANAGE):
            return _deny_authority()

        ser = MembershipRoleAssignmentUpdateSerializer(data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data

        if 'area' in data:
            if data['area'] is None:
                assignment.area = None
            else:
                area = CompanyArea.objects.filter(pk=data['area'], company=company).first()
                if not area:
                    return _deny_not_found()
                assignment.area = area

        if 'is_active' in data:
            # Re-activating hands the capabilities back, so re-check delegation.
            if data['is_active'] and not can_delegate_capabilities(
                request.user, company, assignment.role.capability_set,
            ):
                return Response({'detail': _CANNOT_DELEGATE}, status=status.HTTP_403_FORBIDDEN)
            assignment.is_active = data['is_active']

        assignment.save()

        AdminAuditLog.log(
            actor=request.user,
            action='role_assignment_updated',
            target_type='membership_role_assignment', target_id=assignment.pk,
            metadata={
                'assignment_id': assignment.pk, 'company_id': company.pk,
                'role_id': assignment.role_id,
                'area_id': assignment.area_id, 'is_active': assignment.is_active,
            },
            request=request, company=company,
        )
        return Response(MembershipRoleAssignmentSerializer(assignment).data)

    def delete(self, request, pk):
        assignment = self._scoped(request, pk)
        if not assignment:
            return _deny_not_found()

        company = assignment.membership.company
        if not has_capability(request.user, company, CAP_MEMBERSHIPS_MANAGE):
            return _deny_authority()

        assignment.is_active = False
        assignment.save(update_fields=['is_active', 'updated_at'])

        AdminAuditLog.log(
            actor=request.user, action='role_assignment_disabled',
            target_type='membership_role_assignment', target_id=assignment.pk,
            metadata={
                'assignment_id': assignment.pk, 'company_id': company.pk,
                'role_id': assignment.role_id, 'membership_id': assignment.membership_id,
            },
            request=request, company=company,
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class MyCompanyAccessView(APIView):
    """
    GET /api/me/company-access/ — the caller's own areas, roles and capabilities.

    Self-scoped: it never accepts a user id, so it cannot enumerate anyone else.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        from .tenancy import active_memberships, user_areas

        results = []
        for membership in active_memberships(request.user):
            company = membership.company
            assignments = membership.role_assignments.filter(
                is_active=True, role__is_active=True,
            ).select_related('role', 'area')
            results.append({
                'company': company.pk,
                'company_name': company.name,
                'legacy_role': membership.role,
                'roles': [
                    {'id': a.role.pk, 'name': a.role.name, 'slug': a.role.slug,
                     'area': a.area.name if a.area else None}
                    for a in assignments
                ],
                'areas': [{'id': a.pk, 'name': a.name, 'slug': a.slug}
                          for a in user_areas(request.user, company)],
                'capabilities': sorted(resolve_capabilities(request.user, company)),
                'source': 'custom_roles' if assignments.exists() else 'legacy_role',
            })

        return Response({
            'results': results,
            'count': len(results),
            'is_platform_admin': is_platform_admin(request.user),
        })


# ---------------------------------------------------------------------------
# Internal control dashboard — Phase 2A.2
# ---------------------------------------------------------------------------

# How many companies the switcher will list. A platform master can see every
# tenant; the list is for a dropdown, not for enumeration.
_MAX_SWITCHER_COMPANIES = 100


class InternalDashboardView(APIView):
    """
    GET /api/me/internal-dashboard/[?company=<id>]

    One safe snapshot of the caller's company context, so the internal control
    shell does not need four round trips to render its header.

    WHAT IT DELIBERATELY DOES NOT RETURN
    ------------------------------------
    No sales totals, revenue, order counts, stock levels, profit, best-selling
    products or customer counts. `Product`, `Order` and `StockMovement` have no
    `company` column yet, so any such number would be a PLATFORM-WIDE figure
    displayed inside a per-company dashboard. Showing a global number in a
    tenant frame is worse than showing nothing: it reads as this company's data.
    Those KPIs arrive when the models are tenantised (Phase 2B/2C).

    TENANT RESOLUTION
    -----------------
    `?company=` is untrusted input. It is passed through
    tenancy.resolve_company_for_user(), which only ever SELECTS among companies
    the caller already has access to; a foreign id answers exactly like a
    non-existent one.
    """

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminInventoryReportsThrottle]

    def get(self, request):
        user = request.user
        platform_admin = is_platform_admin(user)
        memberships = list(active_memberships(user))

        # Business access is never implied by authentication alone, and never by
        # the legacy UserProfile.role.
        if not platform_admin and not memberships:
            return Response(
                {'detail': 'No tienes acceso al control interno de ninguna empresa.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        switcher = self._switcher_companies(user, platform_admin)

        requested = request.query_params.get('company')
        if requested is not None:
            try:
                requested_id = int(requested)
            except (TypeError, ValueError):
                return Response(
                    {'detail': 'Parámetro "company" inválido.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            try:
                company = resolve_company_for_user(user, requested_id)
            except CrossTenantError:
                # Same answer for "does not exist" and "not yours".
                return Response({'detail': _NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)
            except NoTenantError as exc:
                return Response({'detail': str(exc)}, status=status.HTTP_403_FORBIDDEN)
        elif not platform_admin and len(memberships) == 1:
            company = memberships[0].company
        else:
            # Several companies, or a platform master with none chosen: the
            # caller must pick. Returning an arbitrary company here would show
            # someone another tenant's context by accident.
            return Response({
                'company': None,
                'membership': None,
                'access': {
                    'is_platform_admin': platform_admin,
                    'legacy_role': None,
                    'roles': [],
                    'areas': [],
                    'capabilities': [],
                },
                'organization': None,
                'catalog': None,
                'available_companies': switcher,
                'requires_company_selection': True,
                'alerts': [],
            })

        return Response(self._snapshot(user, company, platform_admin, switcher))

    # -- helpers --------------------------------------------------------------

    def _switcher_companies(self, user, platform_admin):
        """Companies the caller may open. Never wider than visible_companies()."""
        qs = visible_companies(user).order_by('name')[:_MAX_SWITCHER_COMPANIES]
        return [
            {'id': c.pk, 'name': c.name, 'slug': c.slug, 'is_active': c.is_active}
            for c in qs
        ]

    def _snapshot(self, user, company, platform_admin, switcher):
        from .models import Branch, CompanyArea, CompanyRole, Membership

        membership = (
            Membership.objects
            .filter(user=user, company=company, is_active=True)
            .select_related('branch')
            .first()
        )

        assignments = []
        if membership is not None:
            assignments = list(
                membership.role_assignments
                .filter(is_active=True, role__is_active=True)
                .select_related('role', 'area')
            )

        capabilities = sorted(resolve_capabilities(user, company))

        # Organisation counters are themselves company information: only a caller
        # who may view the company gets them.
        organization = None
        if 'company.view' in capabilities:
            organization = {
                'active_branches': Branch.objects.filter(
                    company=company, is_active=True).count(),
                'active_memberships': Membership.objects.filter(
                    company=company, is_active=True).count(),
                'active_areas': CompanyArea.objects.filter(
                    company=company, is_active=True).count(),
                'active_roles': CompanyRole.objects.filter(
                    company=company, is_active=True).count(),
                # Series for the dashboard charts. Same capability gate as the
                # counters above — a distribution is company information too.
                'assignments_per_area': self._assignments_per_area(company),
                'assignments_per_role': self._assignments_per_role(company),
            }

        # Catalogue counters — safe from Phase 2B onward because Category and
        # Product now belong to a company, so these are genuinely per-tenant and
        # not a platform-wide figure wearing a tenant's frame.
        #
        # Still ABSENT on purpose: stock totals, inventory value, sales, orders
        # and profit. Order and StockMovement have no company column yet.
        catalog = None
        if 'products.view' in capabilities:
            catalog = self._catalog_snapshot(company)

        return {
            'company': {
                'id': company.pk,
                'name': company.name,
                'slug': company.slug,
                'is_active': company.is_active,
            },
            'membership': None if membership is None else {
                'id': membership.pk,
                'branch': None if membership.branch_id is None else {
                    'id': membership.branch_id,
                    'name': membership.branch.name,
                },
            },
            'access': {
                'is_platform_admin': platform_admin,
                'legacy_role': membership.role if membership else None,
                'roles': [
                    {
                        'id': a.role.pk, 'name': a.role.name, 'slug': a.role.slug,
                        'area': a.area.name if a.area else None,
                    }
                    for a in assignments
                ],
                'areas': [
                    {'id': a.pk, 'name': a.name, 'slug': a.slug}
                    for a in user_areas(user, company)
                ],
                'capabilities': capabilities,
                'source': 'custom_roles' if assignments else 'legacy_role',
            },
            'organization': organization,
            'catalog': catalog,
            'available_companies': switcher,
            'requires_company_selection': False,
            'alerts': self._alerts(company, membership, assignments, capabilities,
                                   platform_admin),
        }

    # -- chart series (Phase 2B.1) -------------------------------------------
    #
    # Every series below is computed with an explicit `company=` filter. They are
    # DISTRIBUTIONS of data this dashboard already reports as totals, so they add
    # no new exposure — but they are gated by the same capability all the same,
    # because "how your catalogue is shaped" is company information.
    #
    # Deliberately NOT here: anything derived from Order or StockMovement. Those
    # models have no company column, so a chart built on them would be a
    # platform-wide figure drawn inside a tenant's dashboard.

    _MAX_SERIES_BUCKETS = 8

    def _catalog_snapshot(self, company):
        from django.db.models import Count, Q

        from .models import Category, Product

        total = Product.objects.filter(company=company).count()
        active = Product.objects.filter(company=company, is_active=True).count()

        # Products per category, biggest first. Uncategorised products are shown
        # as their own bucket rather than dropped — a chart that silently omits
        # rows misrepresents the total it sits next to.
        rows = list(
            Category.objects
            .filter(company=company)
            .annotate(product_count=Count('product', filter=Q(product__company=company)))
            .order_by('-product_count', 'name')
            .values('id', 'name', 'product_count')[:self._MAX_SERIES_BUCKETS]
        )
        series = [
            {'label': r['name'], 'value': r['product_count']} for r in rows
        ]
        uncategorised = Product.objects.filter(
            company=company, category__isnull=True,
        ).count()
        if uncategorised:
            series.append({'label': 'Sin categoría', 'value': uncategorised})

        return {
            'products': total,
            'active_products': active,
            'inactive_products': total - active,
            'categories': Category.objects.filter(company=company).count(),
            'products_per_category': series,
        }

    def _assignments_per_area(self, company):
        from django.db.models import Count, Q

        from .models import CompanyArea

        rows = (
            CompanyArea.objects
            .filter(company=company, is_active=True)
            .annotate(member_count=Count(
                'role_assignments',
                filter=Q(role_assignments__is_active=True,
                         role_assignments__membership__is_active=True),
                distinct=True,
            ))
            .order_by('-member_count', 'sort_order', 'name')
            .values('id', 'name', 'member_count')[:self._MAX_SERIES_BUCKETS]
        )
        return [{'label': r['name'], 'value': r['member_count']} for r in rows]

    def _assignments_per_role(self, company):
        from django.db.models import Count, Q

        from .models import CompanyRole

        rows = (
            CompanyRole.objects
            .filter(company=company, is_active=True)
            .annotate(member_count=Count(
                'assignments',
                filter=Q(assignments__is_active=True,
                         assignments__membership__is_active=True),
                distinct=True,
            ))
            .order_by('-member_count', 'name')
            .values('id', 'name', 'member_count')[:self._MAX_SERIES_BUCKETS]
        )
        return [{'label': r['name'], 'value': r['member_count']} for r in rows]

    def _alerts(self, company, membership, assignments, capabilities, platform_admin):
        """
        Real, safely-derivable conditions only.

        No fabricated commercial alerts ("3 productos sin stock"): inventory is
        not tenantised, so such a count would be platform-wide.
        """
        alerts = []

        if not company.is_active:
            alerts.append({
                'level': 'critical', 'code': 'company_inactive',
                'title': 'Empresa desactivada',
                'detail': 'Esta empresa está desactivada; su personal no tiene acceso operativo.',
            })

        if membership is None and platform_admin:
            alerts.append({
                'level': 'info', 'code': 'platform_admin_no_membership',
                'title': 'Acceso de plataforma',
                'detail': 'Estás viendo esta empresa como administrador de plataforma, '
                          'sin pertenecer a ella.',
            })

        if membership is not None and membership.branch_id is None:
            alerts.append({
                'level': 'info', 'code': 'no_branch_assigned',
                'title': 'Sin sucursal asignada',
                'detail': 'Tu alcance es toda la empresa. El acceso multisucursal '
                          'granular está pendiente.',
            })

        if membership is not None and not assignments and not capabilities:
            alerts.append({
                'level': 'warning', 'code': 'no_capabilities',
                'title': 'Sin permisos efectivos',
                'detail': 'Tu membresía no tiene capacidades. Pide a un administrador '
                          'de la empresa que te asigne un rol.',
            })

        for assignment in assignments:
            if not assignment.role.capability_set:
                alerts.append({
                    'level': 'warning', 'code': 'role_without_capabilities',
                    'title': f'Rol sin permisos: {assignment.role.name}',
                    'detail': 'Este rol no concede ninguna capacidad.',
                })

        return alerts
