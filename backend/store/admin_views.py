from django.contrib.auth import get_user_model
from django.db.models import Q
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import AdminAuditLog, UserProfile
from .permissions import IsAdminRole, IsSuperAdminRole, get_user_role
from .throttles import AdminUsersThrottle, AdminRoleChangeThrottle, AdminAuditLogsThrottle

User = get_user_model()

_VALID_ROLES = {r[0] for r in UserProfile.ROLE_CHOICES}
_DEFAULT_PAGE_SIZE = 25
_MAX_PAGE_SIZE = 100


def _paginate(queryset, request):
    """Simple page-based pagination. Returns (sliced_qs, meta_dict)."""
    try:
        page = max(1, int(request.query_params.get('page', 1)))
    except (ValueError, TypeError):
        page = 1
    try:
        page_size = min(_MAX_PAGE_SIZE, max(1, int(request.query_params.get('page_size', _DEFAULT_PAGE_SIZE))))
    except (ValueError, TypeError):
        page_size = _DEFAULT_PAGE_SIZE

    total = queryset.count()
    offset = (page - 1) * page_size
    return (
        queryset[offset: offset + page_size],
        {'count': total, 'page': page, 'page_size': page_size},
    )


class AdminUserListView(APIView):
    """GET /api/admin/users/ — paginated user list with search and role filter. Admin+ only."""
    permission_classes = [permissions.IsAuthenticated, IsAdminRole]
    throttle_classes = [AdminUsersThrottle]

    def get(self, request):
        users = User.objects.select_related('profile').order_by('id')

        search = request.query_params.get('search', '').strip()
        if search:
            users = users.filter(
                Q(username__icontains=search) | Q(email__icontains=search)
            )

        role_filter = request.query_params.get('role', '').strip()
        if role_filter:
            if role_filter == UserProfile.ROLE_SUPERADMIN:
                users = users.filter(
                    Q(is_superuser=True) | Q(profile__role=UserProfile.ROLE_SUPERADMIN)
                )
            elif role_filter in _VALID_ROLES:
                users = users.filter(profile__role=role_filter, is_superuser=False)

        page_qs, meta = _paginate(users, request)
        results = [
            {
                'id': u.id,
                'username': u.username,
                'email': u.email,
                'is_active': u.is_active,
                'role': get_user_role(u),
                'date_joined': u.date_joined,
            }
            for u in page_qs
        ]
        return Response({**meta, 'results': results})


class AdminUserRoleView(APIView):
    """PATCH /api/admin/users/{pk}/role/ — change role. Superadmin only."""
    permission_classes = [permissions.IsAuthenticated, IsSuperAdminRole]
    throttle_classes = [AdminRoleChangeThrottle]

    def patch(self, request, pk):
        if request.user.pk == pk:
            return Response(
                {'detail': 'No puedes cambiar tu propio rol.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        new_role = (request.data.get('role') or '').strip()
        if new_role not in _VALID_ROLES:
            return Response(
                {'detail': f'Rol inválido. Opciones: {", ".join(sorted(_VALID_ROLES))}.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            target = User.objects.get(pk=pk)
        except User.DoesNotExist:
            return Response({'detail': 'Usuario no encontrado.'}, status=status.HTTP_404_NOT_FOUND)

        profile, _ = UserProfile.objects.get_or_create(
            user=target,
            defaults={'role': UserProfile.ROLE_CUSTOMER},
        )
        old_role = profile.role
        profile.role = new_role
        profile.save(update_fields=['role', 'updated_at'])

        AdminAuditLog.log(
            actor=request.user,
            action='role_change',
            target_type='user',
            target_id=target.pk,
            metadata={'old_role': old_role, 'new_role': new_role, 'target_username': target.username},
            request=request,
        )

        return Response({
            'id': target.id,
            'username': target.username,
            'role': new_role,
            'detail': f'Rol actualizado de {old_role} a {new_role}.',
        })


class AdminAuditLogListView(APIView):
    """GET /api/admin/audit-logs/ — paginated audit log with optional filters. Admin+ only."""
    permission_classes = [permissions.IsAuthenticated, IsAdminRole]
    throttle_classes = [AdminAuditLogsThrottle]

    def get(self, request):
        logs = AdminAuditLog.objects.select_related('actor').order_by('-created_at')

        action_filter = request.query_params.get('action', '').strip()
        if action_filter:
            logs = logs.filter(action__icontains=action_filter)

        actor_filter = request.query_params.get('actor', '').strip()
        if actor_filter:
            logs = logs.filter(actor__username__icontains=actor_filter)

        target_type_filter = request.query_params.get('target_type', '').strip()
        if target_type_filter:
            logs = logs.filter(target_type=target_type_filter)

        page_qs, meta = _paginate(logs, request)
        results = [
            {
                'id': log.id,
                'actor': log.actor.username if log.actor else None,
                'action': log.action,
                'target_type': log.target_type,
                'target_id': log.target_id,
                'metadata': log.metadata,
                'ip_address': log.ip_address,
                'created_at': log.created_at,
            }
            for log in page_qs
        ]
        return Response({**meta, 'results': results})
