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
from .models import AdminAuditLog, Branch, Company, Membership, MembershipBranchAccess
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


# ---------------------------------------------------------------------------
# Branch access administration — Phase 2D
# ---------------------------------------------------------------------------
#
# CAPABILITY DELEGATION AND BRANCH ACCESS ARE KEPT APART.
#
# `memberships.manage` lets an administrator say WHAT someone may do. This
# section lets them say WHERE. Conflating them would mean that granting
# somebody the inventory role also handed them every shop, which is precisely
# the decision a chain needs to make separately.
#
# A company administrator may only grant branches OF THEIR OWN COMPANY: the
# queryset below never leaves `membership.company`, so a branch id from another
# tenant is not rejected by a check that could be forgotten — it is simply not
# in the set being searched.

def _apply_branch_access(membership, branch_ids, actor):
    """
    Replace a membership's branch grants with `branch_ids`.

    Returns `(applied_ids, error_response)`. An id outside the membership's own
    company answers as not-found, exactly like one that does not exist.

    Grants are DEACTIVATED rather than deleted, and reactivated when re-granted.
    Deleting would lose who granted what and when, which is the first thing
    anyone asks after an access incident.
    """
    branches = list(Branch.objects.filter(
        company=membership.company, pk__in=branch_ids,
    ))
    found = {b.pk for b in branches}
    missing = [i for i in branch_ids if i not in found]
    if missing:
        return None, Response(
            {'detail': 'Sucursal no encontrada o sin acceso.'},
            status=status.HTTP_404_NOT_FOUND,
        )

    with transaction.atomic():
        membership.branch_access.exclude(branch_id__in=found).update(is_active=False)
        for branch in branches:
            access, created = MembershipBranchAccess.objects.get_or_create(
                membership=membership, branch=branch,
                defaults={'is_active': True, 'granted_by': actor},
            )
            if not created and not access.is_active:
                access.is_active = True
                access.granted_by = actor
                access.save(update_fields=['is_active', 'granted_by', 'updated_at'])

    return sorted(found), None


def _validate_default_branch(membership):
    """
    Clear a default branch the member can no longer reach.

    A stale pointer is not a security hole — every read filters through
    visible_branches() — but leaving it would make the UI open on a branch that
    then answers 404, which reads as a bug rather than as a revoked grant.
    """
    from .tenancy import has_branch_access

    if membership.branch_id and not has_branch_access(membership.user, membership.branch):
        membership.branch = None
        membership.save(update_fields=['branch', 'updated_at'])


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

        # Phase 2D: `default_inventory_branch` decides which shelf the online
        # store sells from, so it is validated against THIS company's branches
        # by CompanySerializer — a branch id from another tenant is rejected as
        # an invalid choice, not silently stored.
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


class AdminCompanyFulfillmentBranchView(APIView):
    """
    PATCH /api/admin/companies/{pk}/fulfillment-branch/
    Body: {"branch": <id|null>}

    WHY THIS IS ITS OWN ENDPOINT.
    `PATCH /api/admin/companies/{pk}/` is platform-administrator only, and stays
    that way: it can flip `is_active`, rename a tenant and change its slug, which
    are platform decisions. But WHICH BRANCH THE ONLINE STORE SHIPS FROM is an
    operational decision that belongs to the business, not to the platform
    operator — a company that opens a second shop must be able to say where its
    web orders come from without filing a ticket.

    So the one operational field Phase 2D added gets its own door, gated by
    `company.manage` inside that company. Everything else on Company keeps the
    authority it had.

    `null` clears it, and that is a legitimate choice with a visible
    consequence: with more than one active branch and no default, checkout
    refuses and says so. It never falls back to "some branch".
    """

    permission_classes = [permissions.IsAuthenticated, HasCompanyMembership]
    throttle_classes = [AdminUsersThrottle]

    def patch(self, request, pk):
        company = visible_companies(request.user).filter(pk=pk).first()
        if not company:
            return Response({'detail': _NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)
        if not can_manage_company(request.user, company):
            return Response(
                {'detail': 'Se requiere rol de administrador de la empresa.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        raw = request.data.get('branch', None)
        branch = None
        if raw not in (None, ''):
            # Untrusted id: it may only SELECT among this company's own branches.
            # Searching inside `company.branches` means a foreign id is not
            # rejected by a check that could be forgotten — it is simply not in
            # the set being searched.
            branch = Branch.objects.filter(company=company, pk=raw).first()
            if branch is None:
                return Response(
                    {'branch': ['Sucursal no encontrada en esta empresa.']},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if not branch.is_active:
                return Response(
                    {'branch': ['La sucursal de despacho debe estar activa.']},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        previous = company.default_inventory_branch_id
        company.default_inventory_branch = branch
        company.save(update_fields=['default_inventory_branch', 'updated_at'])

        AdminAuditLog.log(
            actor=request.user,
            action='company_fulfillment_branch_changed',
            target_type='company',
            target_id=company.pk,
            metadata={
                'company_id': company.pk,
                'old_branch_id': previous,
                'new_branch_id': company.default_inventory_branch_id,
            },
            request=request,
            company=company,
        )
        return Response(CompanySerializer(company).data)


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


class AdminBranchDetailView(APIView):
    """
    GET   /api/admin/branches/{pk}/
    PATCH /api/admin/branches/{pk}/ — name, address, phone, email, is_active.

    Closes the Phase 2D debt: branches could be created and listed but never
    edited, so a shop that moved had no way to correct its own address — the one
    that then prints on every pickup receipt.

    `company` is absent from the writable set. A branch cannot be moved to
    another tenant, and it is not prevented by a check that could be forgotten:
    the field is simply never read from the payload.

    STOCK IS NOT MANAGED HERE. Deactivating a branch does not move, release or
    delete its stock; those units stay on that shelf and stop being sellable,
    which is what "this shop is closed" means. Moving them is a transfer.
    """

    permission_classes = [permissions.IsAuthenticated, HasCompanyMembership]
    throttle_classes = [AdminUsersThrottle]

    _WRITABLE = ('name', 'address', 'phone', 'email', 'is_active')

    def _scoped(self, request, pk):
        return scope_queryset(
            Branch.objects.select_related('company'), request.user,
        ).filter(pk=pk).first()

    def get(self, request, pk):
        branch = self._scoped(request, pk)
        if not branch:
            return Response({'detail': _NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)
        return Response(BranchSerializer(branch).data)

    def patch(self, request, pk):
        branch = self._scoped(request, pk)
        if not branch:
            return Response({'detail': _NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)
        if not can_manage_company(request.user, branch.company):
            return Response(
                {'detail': 'Se requiere rol de administrador de la empresa.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        payload = request.data if isinstance(request.data, dict) else {}
        ser = BranchSerializer(
            branch,
            data={k: v for k, v in payload.items() if k in self._WRITABLE},
            partial=True,
        )
        ser.is_valid(raise_exception=True)
        before = {f: getattr(branch, f) for f in self._WRITABLE}
        updated = ser.save()

        changed = [f for f in self._WRITABLE if before[f] != getattr(updated, f)]

        # Deactivating the branch the storefront ships from would leave checkout
        # refusing every order with no explanation on this screen. Clear the
        # pointer and say so, rather than leaving a dangling one.
        cleared_fulfillment = False
        if not updated.is_active and updated.company.default_inventory_branch_id == updated.pk:
            updated.company.default_inventory_branch = None
            updated.company.save(update_fields=['default_inventory_branch', 'updated_at'])
            cleared_fulfillment = True

        if changed:
            AdminAuditLog.log(
                actor=request.user,
                action='branch_updated',
                target_type='branch',
                target_id=updated.pk,
                metadata={
                    'branch_id': updated.pk,
                    'company_id': updated.company_id,
                    'changed_fields': changed,
                    'cleared_fulfillment_branch': cleared_fulfillment,
                },
                request=request,
                company=updated.company,
            )
        return Response(BranchSerializer(updated).data)


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

        # Phase 2D. Default ALL, matching what a membership meant before this
        # phase: nothing restricted these people by branch, and creating them
        # restricted by surprise would be a silent narrowing.
        access_mode = data.get('branch_access_mode', Membership.ACCESS_MODE_ALL)

        membership = Membership.objects.create(
            user=target_user,
            company=company,
            role=data['role'],
            branch=branch,
            branch_access_mode=access_mode,
            is_active=data.get('is_active', True),
        )

        granted = None
        if 'branch_access' in data:
            granted, access_error = _apply_branch_access(
                membership, data['branch_access'], request.user,
            )
            if access_error:
                # The membership is not left half-configured.
                membership.delete()
                return access_error

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
                'branch_access_mode': membership.branch_access_mode,
                'branch_access': granted,
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
        if 'branch_access_mode' in data:
            membership.branch_access_mode = data['branch_access_mode']

        membership.save()

        # Grants are applied AFTER the mode, so a single request can switch
        # somebody to SELECTED and name their branches at the same time.
        granted = None
        if 'branch_access' in data:
            granted, access_error = _apply_branch_access(
                membership, data['branch_access'], request.user,
            )
            if access_error:
                return access_error

        _validate_default_branch(membership)

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
                'branch_access_mode': membership.branch_access_mode,
                'branch_access': granted,
            },
            request=request,
            company=membership.company,
        )
        membership.refresh_from_db()
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
