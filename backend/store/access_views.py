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
    can_delegate_capabilities,
    has_capability,
    is_platform_admin,
    resolve_capabilities,
    scope_queryset,
    visible_companies,
)
from .throttles import AdminUsersThrottle

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
