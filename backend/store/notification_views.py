"""
Inbox APIs — M12B.

TWO SURFACES, NEVER ONE. Staff read `/internal/`, customers read `/customer/`,
and each is born scoped to its own audience. A single endpoint branching on
`request.user.is_staff` would put both audiences one boolean away from each
other; this project separated them for a reason and M12B does not reunite them.

A NOTIFICATION IS NOT AN AUTHORISATION. It carries `target_type` / `target_id`
so a client can build a route — never a URL, and never a grant. Opening the
destination re-checks tenant, capability and ownership there. Somebody whose
access was revoked yesterday can still hold last week's notice and will still
be refused at the door.
"""

from __future__ import annotations

from rest_framework import permissions, status
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.views import APIView

from . import notification_services as notif
from .models import Notification
from .v1_customer_views import V1CustomerSurfaceMixin
from .v1_internal_views import V1InternalSurfaceMixin

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


def _serialise(notification):
    """
    What a notification looks like from outside.

    NO `event`, no `payload`, no recipient identity. The summary is the
    contract; anything more would make the inbox a way to read data whose own
    module never granted it.
    """
    return {
        'id': notification.pk,
        'title': notification.title,
        'body': notification.body,
        'priority': notification.priority,
        'source': notification.source,
        'target_type': notification.target_type,
        'target_id': notification.target_id,
        'read_at': notification.read_at,
        'created_at': notification.created_at,
    }


def _listing(request, queryset):
    unread_only = request.query_params.get('unread', '').strip().lower() in (
        '1', 'true', 'yes',
    )
    if unread_only:
        queryset = queryset.filter(read_at__isnull=True)

    total = queryset.count()
    page, size = _paginate(request.query_params)
    start = (page - 1) * size
    rows = list(queryset.order_by('-created_at', '-id')[start:start + size])
    return Response({
        'results': [_serialise(n) for n in rows],
        'count': total,
        'page': page,
        'page_size': size,
    })


class _InboxMixin:
    """The four operations, written once for both audiences."""

    def get_inbox(self):  # pragma: no cover - implementado por cada superficie
        raise NotImplementedError

    def _own(self, pk):
        """
        One notification, from THIS person's inbox.

        Scoped by the same queryset the listing uses, so an id belonging to a
        colleague — or to another tenant — is indistinguishable from one that
        does not exist. 404, never 403: a 403 would confirm it is real.
        """
        found = self.get_inbox().filter(pk=pk).first()
        if found is None:
            raise NotFound('No encontrado.')
        return found


class InternalNotificationListView(V1InternalSurfaceMixin, _InboxMixin, APIView):
    """
    GET — the caller's own staff inbox in one company.

    NO CAPABILITY REQUIRED, deliberately. This is not administrative data about
    other people: it is what the platform has told THIS person. Gating your own
    messages behind a permission would mean an admin could stop somebody
    reading their own assignment notice.
    """

    def get_inbox(self):
        return notif.inbox_for_user(self.request.user, self.get_internal_company())

    def get(self, request, company_slug=None):
        return _listing(request, self.get_inbox())


class InternalUnreadCountView(V1InternalSurfaceMixin, _InboxMixin, APIView):
    """GET — the badge. One COUNT, never a page of rows the client tallies."""

    def get_inbox(self):
        return notif.inbox_for_user(self.request.user, self.get_internal_company())

    def get(self, request, company_slug=None):
        return Response({'unread': notif.unread_count(self.get_inbox())})


class InternalNotificationReadView(V1InternalSurfaceMixin, _InboxMixin, APIView):
    """POST — mark one as read. Idempotent: a second call moves nothing."""

    def get_inbox(self):
        return notif.inbox_for_user(self.request.user, self.get_internal_company())

    def post(self, request, company_slug=None, pk=None):
        notification = self._own(pk)
        notif.mark_read(self.get_inbox().filter(pk=notification.pk))
        notification.refresh_from_db()
        return Response(_serialise(notification))


class InternalNotificationReadAllView(V1InternalSurfaceMixin, _InboxMixin, APIView):
    """POST — mark every unread one as read. Only the caller's own."""

    def get_inbox(self):
        return notif.inbox_for_user(self.request.user, self.get_internal_company())

    def post(self, request, company_slug=None):
        return Response({'marked': notif.mark_read(self.get_inbox())})


class _CustomerInboxMixin(V1CustomerSurfaceMixin, _InboxMixin):
    def get_inbox(self):
        company = self.get_customer_company()
        from .models import Customer
        customer = Customer.objects.filter(
            company=company, user=self.request.user, is_active=True,
        ).first()
        if customer is None:
            return Notification.objects.none()
        return notif.inbox_for_customer(customer, company)


class CustomerNotificationListView(_CustomerInboxMixin, APIView):
    """GET — the customer's own inbox in one company."""

    def get(self, request, company_slug=None):
        return _listing(request, self.get_inbox())


class CustomerUnreadCountView(_CustomerInboxMixin, APIView):
    def get(self, request, company_slug=None):
        return Response({'unread': notif.unread_count(self.get_inbox())})


class CustomerNotificationReadView(_CustomerInboxMixin, APIView):
    def post(self, request, company_slug=None, pk=None):
        notification = self._own(pk)
        notif.mark_read(self.get_inbox().filter(pk=notification.pk))
        notification.refresh_from_db()
        return Response(_serialise(notification))


class CustomerNotificationReadAllView(_CustomerInboxMixin, APIView):
    def post(self, request, company_slug=None):
        return Response({'marked': notif.mark_read(self.get_inbox())})
