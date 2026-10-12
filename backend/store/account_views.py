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
from .tenancy import resolve_storefront_company
from .throttles import AccountProfileWriteThrottle, AccountRepairsThrottle


def _no_store(response):
    """Private content: no shared cache keeps it. The one rule of `evidence_views`."""
    # Imported here: `evidence_views` builds on `v1_customer_views`, and that
    # module builds its address-book views on this one.
    from .evidence_views import _no_store as private

    return private(response)


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


# ---------------------------------------------------------------------------
# DELIVERY-01 — the buyer's address book
# ---------------------------------------------------------------------------

_ADDRESS_NOT_FOUND = 'Dirección no encontrada.'


class AddressBookActions:
    """
    What the web's book and the app's book do, once each of them has said
    WHICH shop it is (`book_company`): the web from the Host, the app from the
    path. One set of answers for the two doors.

    Whose book it is is never asked of the request: it is the signed-in
    account's, and an entry that is not in it answers 404 in every verb.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get_throttles(self):
        from .throttles import AccountAddressWriteThrottle

        if self.request.method in ('POST', 'PATCH', 'DELETE'):
            return [AccountAddressWriteThrottle()]
        return [AccountRepairsThrottle()]

    def book_company(self, request):
        """The shop this book is in, or None. Overridden by each door."""
        raise NotImplementedError

    def _missing(self):
        return Response({'detail': _ADDRESS_NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)

    def list_book(self, request):
        from . import delivery_services as delivery

        company = self.book_company(request)
        if company is None:
            return self._missing()
        return _no_store(Response(delivery.book_payload(company, request.user)))

    def add_entry(self, request):
        from . import delivery_services as delivery

        company = self.book_company(request)
        if company is None:
            return self._missing()
        try:
            entry = delivery.create_address(company=company, user=request.user, data=request.data, request=request)
        except delivery.DeliveryError as refused:
            return Response(refused.errors, status=status.HTTP_400_BAD_REQUEST)
        return _no_store(Response(delivery.address_payload(entry), status=status.HTTP_201_CREATED))

    def change_entry(self, request, pk):
        from . import delivery_services as delivery

        company = self.book_company(request)
        if company is None:
            return self._missing()
        try:
            entry = delivery.update_address(company=company, user=request.user, pk=pk, data=request.data, request=request)
        except delivery.DeliveryError as refused:
            return Response(refused.errors, status=status.HTTP_400_BAD_REQUEST)
        if entry is None:
            return self._missing()
        return _no_store(Response(delivery.address_payload(entry)))

    def remove_entry(self, request, pk):
        from . import delivery_services as delivery

        company = self.book_company(request)
        if company is None or not delivery.deactivate_address(company=company, user=request.user, pk=pk, request=request):
            return self._missing()
        return Response(status=status.HTTP_204_NO_CONTENT)

    def choose_default(self, request, pk):
        from . import delivery_services as delivery

        company = self.book_company(request)
        entry = None if company is None else delivery.set_default_address(
            company=company, user=request.user, pk=pk, request=request,
        )
        if entry is None:
            return self._missing()
        return _no_store(Response(delivery.address_payload(entry)))


class _WebBook(AddressBookActions):
    def book_company(self, request):
        company = resolve_storefront_company(request)
        return company if company is not None and company.is_active else None


class AccountAddressListView(_WebBook, APIView):
    """GET — my address book in this shop. POST — add an entry."""

    def get(self, request):
        return self.list_book(request)

    def post(self, request):
        return self.add_entry(request)


class AccountAddressDetailView(_WebBook, APIView):
    """PATCH — correct an entry. DELETE — take it out of the book (it is switched off)."""

    def patch(self, request, pk):
        return self.change_entry(request, pk)

    def delete(self, request, pk):
        return self.remove_entry(request, pk)


class AccountAddressDefaultView(_WebBook, APIView):
    """POST — this entry is the default one from now on."""

    def post(self, request, pk):
        return self.choose_default(request, pk)
