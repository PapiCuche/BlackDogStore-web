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

from . import accounts, customer_profile, security_log, verification_codes
from .emails import send_verification_email
from .models import AdminAuditLog
from .tenancy import resolve_storefront_company
from .throttles import (
    AccountProfileWriteThrottle, AccountRepairsThrottle, AccountVerifyConfirmThrottle, AccountVerifySendThrottle,
)


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
# CLIENT-SEC-01 · F7 — a signed-in account proves its own mailbox
# ---------------------------------------------------------------------------

_CODE_REFUSED = {'detail': 'El código no es válido o ya venció. Pide uno nuevo.'}


class VerifyOwnEmailActions:
    """
    The two steps, once each door has said which shop it is (`shop`): send a
    6-digit code to the account's OWN address, and take it back.

    The address is never read from the request — it is the account's — and the
    code is the registration code of `verification_codes`, with every rule it
    has. The shop only says whose audit trail the act is written in.
    """

    permission_classes = [permissions.IsAuthenticated]

    def shop(self, request):
        raise NotImplementedError

    def send_code(self, request):
        company = self.shop(request)
        if company is None:
            return Response({'detail': 'Tienda no encontrada.'}, status=status.HTTP_404_NOT_FOUND)
        user = request.user
        if not accounts.can_verify_own_email(user):
            # Already proved (or nothing to prove): nothing is sent.
            return Response({
                'detail': 'Tu correo ya está verificado.', 'already_verified': True,
                'email_verified_at': accounts.email_verified_at(user),
            })
        issued = verification_codes.issue(user)
        if issued is None:
            # One a minute and five a day, counted on the account.
            return Response(
                {'detail': 'Ya te enviamos un código hace poco. Espera un minuto antes de pedir otro.'},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )
        if not send_verification_email(user, issued.token, code=issued.code, request=request):
            # Nobody received it: the wait, the day and the previous code stay as they were.
            verification_codes.withdraw(issued)
            return Response(
                {'detail': 'No pudimos enviar el correo. Inténtalo de nuevo en unos minutos.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        verification_codes.settle(issued)
        AdminAuditLog.log(
            actor=user, action='account_email_verification_sent', target_type='user', target_id=user.pk,
            metadata={}, request=request, company=company,
        )
        return Response({'detail': 'Te enviamos un código a tu correo.', 'already_verified': False})

    def confirm_code(self, request):
        company = self.shop(request)
        if company is None:
            return Response({'detail': 'Tienda no encontrada.'}, status=status.HTTP_404_NOT_FOUND)
        data = request.data if hasattr(request.data, 'get') else {}
        user = request.user
        if not verification_codes.confirm_own(
            user, data.get('code'),
            on_exhausted=lambda account: security_log.verify_code_exhausted(request, user=account),
        ):
            return Response(_CODE_REFUSED, status=status.HTTP_400_BAD_REQUEST)
        AdminAuditLog.log(
            actor=user, action='account_email_verified', target_type='user', target_id=user.pk,
            metadata={}, request=request, company=company,
        )
        return Response({
            'detail': 'Correo verificado.', 'email_verified_at': accounts.email_verified_at(user),
        })


class _WebShop:
    def shop(self, request):
        company = resolve_storefront_company(request)
        return company if company is not None and company.is_active else None


class AccountVerifyEmailSendView(_WebShop, VerifyOwnEmailActions, APIView):
    """POST — send me the code that proves I read this account's mailbox."""
    throttle_classes = [AccountVerifySendThrottle]

    def post(self, request):
        return self.send_code(request)


class AccountVerifyEmailConfirmView(_WebShop, VerifyOwnEmailActions, APIView):
    """POST {code} — this is the code I was sent."""
    throttle_classes = [AccountVerifyConfirmThrottle]

    def post(self, request):
        return self.confirm_code(request)


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
