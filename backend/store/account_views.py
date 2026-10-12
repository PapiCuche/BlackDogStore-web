"""
«Mi cuenta» on the web storefront — CLIENT-SEC-01.

The buyer's own profile in the shop being looked at. Cookie session, CSRF on
the write like every other unsafe request of the browser; the company is the
storefront's, resolved from the Host, and nothing in the request names another.
The app reaches the same profile through `customer/<slug>/profile/`
(`v1_customer_views.V1CustomerProfileView`): one service, two doors.
"""
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from . import customer_profile
from .evidence_views import _no_store
from .tenancy import resolve_storefront_company
from .throttles import AccountProfileWriteThrottle, AccountRepairsThrottle


class AccountProfileView(APIView):
    """
    GET   — my name, phone and document in this shop, and my account's address.
    PATCH — correct them. The first save makes the record; a read never does.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get_throttles(self):
        # Saving has a budget of its own, and a small one: each save may ask
        # «is this document somebody's?», which is not a question to ask freely.
        if self.request.method == 'PATCH':
            return [AccountProfileWriteThrottle()]
        return [AccountRepairsThrottle()]

    def _company(self, request):
        company = resolve_storefront_company(request)
        return company if company is not None and company.is_active else None

    def get(self, request):
        company = self._company(request)
        if company is None:
            return Response({'detail': 'Tienda no encontrada.'}, status=status.HTTP_404_NOT_FOUND)
        return _no_store(Response(customer_profile.payload(company, request.user)))

    def patch(self, request):
        company = self._company(request)
        if company is None:
            return Response({'detail': 'Tienda no encontrada.'}, status=status.HTTP_404_NOT_FOUND)
        try:
            body = customer_profile.update(
                company=company, user=request.user, data=request.data, request=request,
            )
        except customer_profile.ProfileError as refused:
            return Response(refused.errors, status=status.HTTP_400_BAD_REQUEST)
        return _no_store(Response(body))
