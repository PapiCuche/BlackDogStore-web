"""
M12C — the communiqué APIs.

TWO SURFACES, AND THE URL SAYS WHICH IS WHICH.

`/internal/<company_slug>/communications/` belongs to one tenant. Everything it
does happens inside the company named in the path, and the company named in the
path is the only one it can reach.

`/platform/announcements/` is the master's, and it is separate precisely
BECAUSE it is multi-company. Putting cross-tenant targeting behind a URL that
promises a single slug would make the path a lie, and the first person to read
it would believe the wrong thing about what the endpoint can do.

A NOTIFICATION IS NOT AN AUTHORISATION, still. Being a recipient lets somebody
read the communiqué they were sent — nothing more. It grants no
`communications.manage`, no staff list, no other tenant's messages.
"""

from __future__ import annotations

from rest_framework import permissions, status
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from . import announcement_services as svc
from .models import (
    Announcement, AnnouncementAudienceRule, Branch, Company, CompanyRole,
    Membership, Notification,
)
from .v1_authentication import V1BearerAuthentication
from .v1_internal_views import V1InternalSurfaceMixin

CAPABILITY = 'communications.manage'

_PAGE_SIZE = 20
_MAX_PAGE_SIZE = 50


def _paginate(params):
    try:
        page = max(1, int(params.get('page', 1)))
    except (TypeError, ValueError):
        page = 1
    try:
        size = min(_MAX_PAGE_SIZE, max(1, int(params.get('page_size', _PAGE_SIZE))))
    except (TypeError, ValueError):
        size = _PAGE_SIZE
    return page, size


def _author_display(user):
    """A name, never an e-mail address. Recipients do not need a mailbox."""
    if user is None:
        return ''
    full = (getattr(user, 'get_full_name', lambda: '')() or '').strip()
    return full or getattr(user, 'username', '') or ''


def _summary(announcement):
    return {
        'id': announcement.pk,
        'title': announcement.title,
        'priority': announcement.priority,
        'status': announcement.status,
        'author': _author_display(announcement.author),
        'created_at': announcement.created_at,
        'published_at': announcement.published_at,
        'recipient_count': announcement.recipient_count,
    }


def _detail(announcement, *, include_audience=False):
    """
    What a RECIPIENT sees: the message, and who signed it.

    NOT the targeting. Which capability was selected, which branches were
    chosen and who else received it are the sender's working notes; publishing
    a message does not publish the reasoning behind its distribution list.
    """
    data = _summary(announcement)
    data['body'] = announcement.body
    if include_audience:
        data['audience'] = [
            {
                'kind': rule.kind,
                'company': rule.company.slug,
                'branch': rule.branch.name if rule.branch_id else None,
                'role': rule.role.name if rule.role_id else None,
                'capability_code': rule.capability_code or None,
                'user': _author_display(rule.user) if rule.user_id else None,
            }
            for rule in announcement.audience_rules
            .select_related('company', 'branch', 'role', 'user').order_by('pk')
        ]
    return data


def _error(exc):
    return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)


# ---------------------------------------------------------------------------
# Tenant surface
# ---------------------------------------------------------------------------

class _TenantCommunicationsMixin(V1InternalSurfaceMixin):
    """Every route here needs `communications.manage` in THIS company."""

    def get_scope(self):
        company = self.get_internal_company()
        self.require_capability(company, CAPABILITY)
        return company

    def owned(self, company, pk):
        """
        404 for anything that is not this company's own communiqué.

        A 403 would confirm the id exists, which is exactly what somebody
        trying numbers is asking. Master-authored messages are not listed here
        either: a tenant administers what its own people wrote.
        """
        try:
            return Announcement.objects.get(pk=pk, source_company=company)
        except (Announcement.DoesNotExist, ValueError, TypeError):
            raise NotFound('No encontrado.')


class TenantAnnouncementListView(_TenantCommunicationsMixin, APIView):
    def get(self, request, company_slug=None):
        company = self.get_scope()
        qs = Announcement.objects.filter(source_company=company).select_related('author')
        state = (request.query_params.get('status') or '').strip()
        if state in dict(Announcement.Status.choices):
            qs = qs.filter(status=state)
        page, size = _paginate(request.query_params)
        total = qs.count()
        start = (page - 1) * size
        return Response({
            'count': total,
            'page': page,
            'page_size': size,
            'results': [_summary(a) for a in qs[start:start + size]],
        })

    def post(self, request, company_slug=None):
        company = self.get_scope()
        try:
            announcement = svc.create_draft(
                author=request.user, source_company=company,
                title=request.data.get('title'),
                body=request.data.get('body'),
                priority=request.data.get('priority', Notification.Priority.INFO),
            )
        except svc.AnnouncementError as exc:
            return _error(exc)
        return Response(_detail(announcement, include_audience=True),
                        status=status.HTTP_201_CREATED)


class TenantAnnouncementDetailView(_TenantCommunicationsMixin, APIView):
    def get(self, request, company_slug=None, pk=None):
        company = self.get_scope()
        return Response(_detail(self.owned(company, pk), include_audience=True))

    def patch(self, request, company_slug=None, pk=None):
        company = self.get_scope()
        announcement = self.owned(company, pk)
        try:
            if 'audience' in request.data:
                svc.set_audience(
                    announcement=announcement,
                    rules=_parse_rules(company, request.data.get('audience')),
                )
            announcement = svc.update_draft(
                announcement=announcement,
                title=request.data.get('title'),
                body=request.data.get('body'),
                priority=request.data.get('priority'),
            )
        except svc.AnnouncementError as exc:
            return _error(exc)
        return Response(_detail(announcement, include_audience=True))


class TenantAnnouncementPreviewView(_TenantCommunicationsMixin, APIView):
    def post(self, request, company_slug=None, pk=None):
        company = self.get_scope()
        return Response(svc.preview(self.owned(company, pk)))


class TenantAnnouncementPublishView(_TenantCommunicationsMixin, APIView):
    def post(self, request, company_slug=None, pk=None):
        company = self.get_scope()
        announcement = self.owned(company, pk)
        try:
            published = svc.publish(
                announcement=announcement, actor=request.user, request=request,
            )
        except svc.AnnouncementError as exc:
            return _error(exc)
        return Response(_detail(published, include_audience=True))


class TenantAnnouncementCancelView(_TenantCommunicationsMixin, APIView):
    def post(self, request, company_slug=None, pk=None):
        company = self.get_scope()
        announcement = self.owned(company, pk)
        try:
            cancelled = svc.cancel_draft(
                announcement=announcement, actor=request.user, request=request,
            )
        except svc.AnnouncementError as exc:
            return _error(exc)
        return Response(_detail(cancelled, include_audience=True))


class TenantAnnouncementStatsView(_TenantCommunicationsMixin, APIView):
    def get(self, request, company_slug=None, pk=None):
        company = self.get_scope()
        return Response(svc.stats(self.owned(company, pk)))


# ---------------------------------------------------------------------------
# The recipient's view
# ---------------------------------------------------------------------------

class InternalAnnouncementReadView(V1InternalSurfaceMixin, APIView):
    """
    GET — the communiqué a notification pointed at.

    NO CAPABILITY REQUIRED, and that is not an oversight. Reading a message
    addressed to you is not an authority; M12B's inbox asks for no permission
    either, for the same reason.

    WHAT IS REQUIRED is that the message was actually addressed to you IN THIS
    COMPANY: the notification row is the proof, and it is the same row that
    freezes the audience. Somebody who acquired the role last week cannot read
    last month's communiqué, because nothing was ever written to them.

    Managers of communiqués reach their own company's messages too, so the
    composer can reopen what it sent.
    """

    def get(self, request, company_slug=None, pk=None):
        from .tenancy import has_capability

        company = self.get_internal_company()
        try:
            announcement = Announcement.objects.get(
                pk=pk, status=Announcement.Status.PUBLISHED,
            )
        except (Announcement.DoesNotExist, ValueError, TypeError):
            raise NotFound('No encontrado.')

        addressed = Notification.objects.filter(
            company=company, user=request.user,
            source=Notification.Source.ANNOUNCEMENT,
            target_type='announcement', target_id=announcement.pk,
        ).exists()
        manages = (
            announcement.source_company_id == company.pk
            and has_capability(request.user, company, CAPABILITY)
        )
        if not (addressed or manages):
            # 404, not 403: a communiqué you were not sent does not exist for
            # you, and saying otherwise would let somebody enumerate what other
            # companies are telling their staff.
            raise NotFound('No encontrado.')
        return Response(_detail(announcement, include_audience=manages))


# ---------------------------------------------------------------------------
# Platform surface
# ---------------------------------------------------------------------------

class _PlatformMixin:
    """
    `User.is_superuser`, and nothing else.

    Never a tenant role called something like "Master", never a membership
    stood up for the purpose. The platform master is a property of the account.
    """

    authentication_classes = [V1BearerAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def require_master(self):
        if not self.request.user.is_superuser:
            raise NotFound('No encontrado.')

    def get_announcement(self, pk):
        self.require_master()
        try:
            return Announcement.objects.get(pk=pk)
        except (Announcement.DoesNotExist, ValueError, TypeError):
            raise NotFound('No encontrado.')


class PlatformAnnouncementListView(_PlatformMixin, APIView):
    def get(self, request):
        self.require_master()
        qs = Announcement.objects.select_related('author', 'source_company')
        state = (request.query_params.get('status') or '').strip()
        if state in dict(Announcement.Status.choices):
            qs = qs.filter(status=state)
        page, size = _paginate(request.query_params)
        total = qs.count()
        start = (page - 1) * size
        return Response({
            'count': total, 'page': page, 'page_size': size,
            'results': [_summary(a) for a in qs[start:start + size]],
        })

    def post(self, request):
        self.require_master()
        try:
            announcement = svc.create_draft(
                author=request.user, source_company=None,
                title=request.data.get('title'),
                body=request.data.get('body'),
                priority=request.data.get('priority', Notification.Priority.INFO),
            )
        except svc.AnnouncementError as exc:
            return _error(exc)
        return Response(_detail(announcement, include_audience=True),
                        status=status.HTTP_201_CREATED)


class PlatformAnnouncementDetailView(_PlatformMixin, APIView):
    def get(self, request, pk=None):
        return Response(_detail(self.get_announcement(pk), include_audience=True))

    def patch(self, request, pk=None):
        announcement = self.get_announcement(pk)
        try:
            if 'audience' in request.data:
                svc.set_audience(
                    announcement=announcement,
                    rules=_parse_platform_rules(request.data.get('audience')),
                )
            announcement = svc.update_draft(
                announcement=announcement,
                title=request.data.get('title'),
                body=request.data.get('body'),
                priority=request.data.get('priority'),
            )
        except svc.AnnouncementError as exc:
            return _error(exc)
        return Response(_detail(announcement, include_audience=True))


class PlatformAnnouncementPreviewView(_PlatformMixin, APIView):
    def post(self, request, pk=None):
        return Response(svc.preview(self.get_announcement(pk)))


class PlatformAnnouncementPublishView(_PlatformMixin, APIView):
    def post(self, request, pk=None):
        announcement = self.get_announcement(pk)
        try:
            published = svc.publish(
                announcement=announcement, actor=request.user, request=request,
            )
        except svc.AnnouncementError as exc:
            return _error(exc)
        return Response(_detail(published, include_audience=True))


class PlatformAnnouncementStatsView(_PlatformMixin, APIView):
    def get(self, request, pk=None):
        return Response(svc.stats(self.get_announcement(pk)))


# ---------------------------------------------------------------------------
# Parsing audience payloads
# ---------------------------------------------------------------------------

#: The literal a caller must send to reach every active company. There is no
#: shorter way and no implicit one: an omitted target is a mistake, never a
#: broadcast, and a `companies: []` that meant "all" would be the single most
#: dangerous default this platform could ship.
ALL_ACTIVE_COMPANIES = 'ALL_ACTIVE_COMPANIES'


def _parse_rules(company, payload):
    """Tenant rules. The company is fixed by the URL and cannot be overridden."""
    if not isinstance(payload, list) or not payload:
        raise svc.AnnouncementError('Elige a quién va dirigido el comunicado.')
    return [_one_rule(company, entry) for entry in payload]


def _one_rule(company, entry):
    if not isinstance(entry, dict):
        raise svc.AnnouncementError('Regla de audiencia inválida.')
    Kind = AnnouncementAudienceRule.Kind
    kind = entry.get('kind')
    spec = {'company': company, 'kind': kind}

    if kind == Kind.BRANCH:
        spec['branch'] = Branch.objects.filter(
            pk=_int(entry.get('branch_id')), company=company,
        ).first()
    elif kind == Kind.ROLE:
        spec['role'] = CompanyRole.objects.filter(
            pk=_int(entry.get('role_id')), company=company,
        ).first()
    elif kind == Kind.CAPABILITY:
        spec['capability_code'] = entry.get('capability_code')
    elif kind == Kind.USER:
        membership = Membership.objects.filter(
            user_id=_int(entry.get('user_id')), company=company, is_active=True,
        ).select_related('user').first()
        spec['user'] = membership.user if membership else None
    return spec


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _parse_platform_rules(payload):
    """
    Master rules, which name their own companies.

    `ALL_ACTIVE_COMPANIES` is spelled out. A body that forgets to say where a
    message goes is refused; it is never widened into a platform-wide send.
    """
    if not isinstance(payload, dict):
        raise svc.AnnouncementError('Audiencia inválida.')

    scope = payload.get('companies')
    if scope == ALL_ACTIVE_COMPANIES:
        companies = list(Company.objects.filter(is_active=True))
        if not companies:
            raise svc.AnnouncementError('No hay empresas activas.')
    elif isinstance(scope, list) and scope:
        companies = list(Company.objects.filter(slug__in=scope, is_active=True))
        missing = sorted(set(scope) - {c.slug for c in companies})
        if missing:
            raise svc.AnnouncementError(
                f'Empresas desconocidas o inactivas: {", ".join(missing)}.'
            )
    else:
        raise svc.AnnouncementError(
            'Indica las empresas destinatarias, o '
            f'"{ALL_ACTIVE_COMPANIES}" de forma explícita.'
        )

    entries = payload.get('rules')
    if not isinstance(entries, list) or not entries:
        raise svc.AnnouncementError('Elige a quién va dirigido el comunicado.')

    rules = []
    for company in companies:
        for entry in entries:
            rules.append(_one_rule(company, entry))
    return rules
