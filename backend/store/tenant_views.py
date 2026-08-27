"""
Multi-tenant administration endpoints — SaaS Phase 1.

Isolation model (see store/tenancy.py for the full strategy):

  - Reads are SCOPED, not filtered by the client. `visible_companies()` /
    `scope_queryset()` restrict every queryset to the companies the caller holds
    an active membership in. A caller with no membership gets 403, never the
    unfiltered list.

  - Writes validate the target company against the caller's OWN access before
    doing anything. A `company` id in the body is untrusted input: it selects
    among companies the caller already administers, and can never widen access.

  - A company id belonging to another tenant returns the SAME error as a
    non-existent one, so ids cannot be probed for existence.

These endpoints are new surface. Nothing in the storefront, checkout, inventory
or sales-note flow calls them.
"""

import logging

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Count
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .company_provisioning import ProvisioningError, provision_company_access_defaults
from .models import AdminAuditLog, Branch, Company, Membership
from .permissions import HasCompanyMembership, IsPlatformAdmin
from .serializers import (
    BranchSerializer,
    CompanySerializer,
    MembershipSerializer,
    MembershipUpdateSerializer,
    MembershipWriteSerializer,
)
from .tenancy import (
    CrossTenantError,
    assert_branch_in_company,
    can_grant_company_role,
    can_manage_company,
    can_manage_company_memberships,
    is_platform_admin,
    scope_queryset,
    visible_companies,
)
from .throttles import AdminUsersThrottle

User = get_user_model()
logger = logging.getLogger(__name__)

# Deliberately identical for "not found" and "not yours" — see module docstring.
_NOT_FOUND = 'Empresa no encontrada o sin acceso.'
_MEMBERSHIP_NOT_FOUND = 'Membresía no encontrada o sin acceso.'

# One message for every reason a target user cannot be added: does not exist,
# already a member, not addable. Distinct messages would let a company admin
# probe which user ids exist platform-wide. See PENDING — Membership Invitation
# Flow in docs/saas-multiempresa.md: proper consent-based onboarding removes the
# need to reference foreign user ids at all.
_TARGET_USER_UNAVAILABLE = 'No se puede asignar una membresía a ese usuario.'
_ROLE_NOT_GRANTABLE = (
    'No tienes autoridad para asignar ese rol. El rol "superadmin" es un valor '
    'heredado y solo un administrador de plataforma puede asignarlo.'
)


class AdminCompanyListView(APIView):
    """
    GET  /api/admin/companies/  — companies the caller may see.
    POST /api/admin/companies/  — create a tenant. Platform administrators only.
    """

    throttle_classes = [AdminUsersThrottle]

    def get_permissions(self):
        if self.request.method == 'POST':
            return [permissions.IsAuthenticated(), IsPlatformAdmin()]
        return [permissions.IsAuthenticated(), HasCompanyMembership()]

    def get(self, request):
        qs = (
            visible_companies(request.user)
            .annotate(
                branch_count=Count('branches', distinct=True),
                membership_count=Count('memberships', distinct=True),
            )
            .order_by('name')
        )
        return Response({
            'results': CompanySerializer(qs, many=True).data,
            'count': qs.count(),
        })

    def post(self, request):
        ser = CompanySerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        # Creation and provisioning share one transaction: if the defaults cannot
        # be created, the company is rolled back too. A tenant that exists but has
        # no areas or roles is worse than one that was never created, because the
        # failure is silent and only shows up when someone tries to use it.
        try:
            with transaction.atomic():
                company = ser.save()
                provisioning = provision_company_access_defaults(
                    company, actor=request.user,
                )
        except ProvisioningError:
            logger.exception('Provisioning failed while creating a company')
            return Response(
                {'detail': 'No se pudo inicializar la empresa. Inténtelo nuevamente.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        AdminAuditLog.log(
            actor=request.user,
            action='company_created',
            target_type='company',
            target_id=company.pk,
            metadata={
                'company_id': company.pk, 'name': company.name, 'slug': company.slug,
                'areas_created': provisioning['areas_created'],
                'roles_created': provisioning['roles_created'],
            },
            request=request,
            company=company,
        )
        return Response(CompanySerializer(company).data, status=status.HTTP_201_CREATED)


class AdminCompanyDetailView(APIView):
    """
    GET   /api/admin/companies/{pk}/ — caller must have access to that company.
    PATCH /api/admin/companies/{pk}/ — platform administrators only.

    Deactivating a company is done with `is_active=False`; a company is never
    deleted through the API, so its operational history is preserved.
    """

    throttle_classes = [AdminUsersThrottle]

    def get_permissions(self):
        if self.request.method in ('PATCH', 'PUT'):
            return [permissions.IsAuthenticated(), IsPlatformAdmin()]
        return [permissions.IsAuthenticated(), HasCompanyMembership()]

    def _visible_or_none(self, request, pk):
        return visible_companies(request.user).filter(pk=pk).first()

    def get(self, request, pk):
        company = self._visible_or_none(request, pk)
        if not company:
            return Response({'detail': _NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)
        return Response(CompanySerializer(company).data)

    def patch(self, request, pk):
        company = Company.objects.filter(pk=pk).first()
        if not company:
            return Response({'detail': _NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)

        ser = CompanySerializer(company, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        updated = ser.save()

        AdminAuditLog.log(
            actor=request.user,
            action='company_updated',
            target_type='company',
            target_id=updated.pk,
            metadata={'company_id': updated.pk, 'is_active': updated.is_active},
            request=request,
            company=updated,
        )
        return Response(CompanySerializer(updated).data)


class AdminBranchListView(APIView):
    """
    GET  /api/admin/branches/  — branches of the caller's companies.
    POST /api/admin/branches/ — create one in a company the caller administers.
    """

    permission_classes = [permissions.IsAuthenticated, HasCompanyMembership]
    throttle_classes = [AdminUsersThrottle]

    def get(self, request):
        qs = scope_queryset(
            Branch.objects.select_related('company'), request.user,
        ).order_by('company__name', 'name')

        company_id = request.query_params.get('company')
        if company_id:
            try:
                qs = qs.filter(company_id=int(company_id))
            except (ValueError, TypeError):
                return Response(
                    {'detail': 'Parámetro "company" inválido.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        return Response({'results': BranchSerializer(qs, many=True).data, 'count': qs.count()})

    def post(self, request):
        ser = BranchSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        company = ser.validated_data['company']

        # Untrusted company id: must be one the caller actually administers.
        # can_manage_company() already short-circuits for platform admins.
        if not can_manage_company(request.user, company):
            return Response({'detail': _NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)

        branch = ser.save()
        AdminAuditLog.log(
            actor=request.user,
            action='branch_created',
            target_type='branch',
            target_id=branch.pk,
            metadata={'branch_id': branch.pk, 'company_id': company.pk, 'name': branch.name},
            request=request,
            company=company,
        )
        return Response(BranchSerializer(branch).data, status=status.HTTP_201_CREATED)


class AdminMembershipListView(APIView):
    """
    GET  /api/admin/memberships/  — memberships inside the caller's companies.
    POST /api/admin/memberships/ — grant a role. Company administrators only,
                                   and only inside their OWN company.
    """

    permission_classes = [permissions.IsAuthenticated, HasCompanyMembership]
    throttle_classes = [AdminUsersThrottle]

    def get(self, request):
        qs = scope_queryset(
            Membership.objects.select_related('user', 'company', 'branch'), request.user,
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

        return Response({'results': MembershipSerializer(qs, many=True).data, 'count': qs.count()})

    def post(self, request):
        ser = MembershipWriteSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data

        # A company the caller cannot even see is indistinguishable from one that
        # does not exist (404). A company they CAN see but may not administer is
        # an authority problem, not a visibility one (403).
        company = visible_companies(request.user).filter(pk=data['company']).first()
        if not company:
            return Response({'detail': _NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)
        if not can_manage_company_memberships(request.user, company):
            return Response(
                {'detail': 'Se requiere rol de administrador de la empresa.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        # A company admin may not hand out the legacy `superadmin` value.
        if not can_grant_company_role(request.user, company, data['role']):
            return Response(
                {'detail': _ROLE_NOT_GRANTABLE}, status=status.HTTP_403_FORBIDDEN
            )

        branch = None
        if data.get('branch'):
            branch = Branch.objects.filter(pk=data['branch']).first()
            if not branch:
                return Response(
                    {'detail': 'Sucursal no encontrada.'}, status=status.HTTP_404_NOT_FOUND
                )
            try:
                assert_branch_in_company(branch, company)
            except CrossTenantError as exc:
                return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        # One response for every reason the target cannot be used, so the
        # endpoint is not a platform-wide user-id oracle.
        target_user = User.objects.filter(pk=data['user']).first()
        if target_user is None or Membership.objects.filter(
            user=target_user, company=company,
        ).exists():
            return Response(
                {'detail': _TARGET_USER_UNAVAILABLE}, status=status.HTTP_400_BAD_REQUEST
            )

        membership = Membership.objects.create(
            user=target_user,
            company=company,
            role=data['role'],
            branch=branch,
            is_active=data.get('is_active', True),
        )

        AdminAuditLog.log(
            actor=request.user,
            action='membership_created',
            target_type='membership',
            target_id=membership.pk,
            metadata={
                'membership_id': membership.pk,
                'company_id': company.pk,
                'user_id': target_user.pk,
                'role': membership.role,
            },
            request=request,
            company=company,
        )
        return Response(
            MembershipSerializer(membership).data, status=status.HTTP_201_CREATED
        )


class AdminMembershipDetailView(APIView):
    """
    GET   /api/admin/memberships/{pk}/
    PATCH /api/admin/memberships/{pk}/ — role / branch / is_active.

    A membership belonging to another company is invisible: both verbs answer 404
    exactly as they would for an id that does not exist.
    """

    permission_classes = [permissions.IsAuthenticated, HasCompanyMembership]
    throttle_classes = [AdminUsersThrottle]

    def _scoped(self, request, pk):
        return scope_queryset(
            Membership.objects.select_related('user', 'company', 'branch'), request.user,
        ).filter(pk=pk).first()

    def get(self, request, pk):
        membership = self._scoped(request, pk)
        if not membership:
            return Response(
                {'detail': _MEMBERSHIP_NOT_FOUND}, status=status.HTTP_404_NOT_FOUND
            )
        return Response(MembershipSerializer(membership).data)

    def patch(self, request, pk):
        membership = self._scoped(request, pk)
        if not membership:
            return Response(
                {'detail': _MEMBERSHIP_NOT_FOUND}, status=status.HTTP_404_NOT_FOUND
            )

        # Visibility is not enough to WRITE: the caller must administer the company.
        if not can_manage_company_memberships(request.user, membership.company):
            return Response(
                {'detail': 'Se requiere rol de administrador de la empresa.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        ser = MembershipUpdateSerializer(data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data

        # `user` and `company` are absent from the update serializer by design:
        # changing either would be a different grant, not an edit. Escalating a
        # membership to the legacy `superadmin` value is a platform-admin action.
        if 'role' in data and not can_grant_company_role(
            request.user, membership.company, data['role'],
        ):
            return Response(
                {'detail': _ROLE_NOT_GRANTABLE}, status=status.HTTP_403_FORBIDDEN
            )

        if 'branch' in data:
            if data['branch'] is None:
                membership.branch = None
            else:
                branch = Branch.objects.filter(pk=data['branch']).first()
                if not branch:
                    return Response(
                        {'detail': 'Sucursal no encontrada.'},
                        status=status.HTTP_404_NOT_FOUND,
                    )
                try:
                    assert_branch_in_company(branch, membership.company)
                except CrossTenantError as exc:
                    return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
                membership.branch = branch

        if 'role' in data:
            membership.role = data['role']
        if 'is_active' in data:
            membership.is_active = data['is_active']

        membership.save()

        AdminAuditLog.log(
            actor=request.user,
            action='membership_updated',
            target_type='membership',
            target_id=membership.pk,
            metadata={
                'membership_id': membership.pk,
                'company_id': membership.company_id,
                'role': membership.role,
                'is_active': membership.is_active,
            },
            request=request,
            company=membership.company,
        )
        return Response(MembershipSerializer(membership).data)


class MyMembershipsView(APIView):
    """
    GET /api/me/memberships/ — the caller's own memberships.

    Read-only and self-scoped: it never accepts a user id, so it cannot be used
    to enumerate anyone else's company affiliations.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        from .tenancy import active_memberships

        qs = active_memberships(request.user)
        return Response({
            'results': MembershipSerializer(qs, many=True).data,
            'count': qs.count(),
            'is_platform_admin': is_platform_admin(request.user),
        })
