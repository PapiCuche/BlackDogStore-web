"""
The counter till, for native clients. IP1A.

THIS FILE CREATES NO BUSINESS LOGIC, AND THAT IS THE POINT.

Every decision here already existed and was already in production behind the Web
till: `pos_services.build_pos_sale` prices a basket, `pos_services.create_pos_sale`
completes a sale, `pos_services.resolve_pos_branch` decides which shop a caller
may sell from. This module resolves a tenant, asks the capability questions, and
hands the answers to those functions — the same three things `pos_views` does.

WHAT IS GENUINELY DIFFERENT, and it is exactly one thing: WHERE THE COMPANY
COMES FROM. The legacy admin surface has no slug in its path, so it derives the
tenant from the caller's own membership — an administrator of two companies gets
whichever one `resolve_company_for_user` picks. This surface takes the slug from
the URL and then proves membership IN THAT COMPANY, which is what makes a native
client able to say which shop it is standing in.

Everything else — the branch resolver, the capability flags passed into the
domain, the error mapping, the response shape — is shared, not copied. The
payloads come from `pos_payloads`, which both surfaces import, so a parity test
can assert the two responses are EQUAL rather than merely similar.

WHAT THIS SURFACE STILL WILL NOT DO
-----------------------------------
It does not accept a price, a subtotal, a discount amount or a total. A till is
TOLD what to charge; it is never asked. Every figure in every response is
computed by the domain from the catalogue, the promotions and the caller's own
authority — which is why `preview/` exists at all: so an operator can read the
real number aloud before taking money, rather than a number their phone worked
out.
"""

from __future__ import annotations

from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.views import APIView

from . import inventory_services, pos_payloads, pos_services
from .models import PaymentMethod, Product, ProductBarcode, normalize_barcode
from .tenancy import has_capability, visible_branches
from .throttles import AdminPosSaleThrottle, AdminPosThrottle
from .v1_internal_views import V1InternalSurfaceMixin

CAP_POS = 'sales.pos.use'
CAP_ASSIGN_SELLER = 'sales.pos.assign_seller'
CAP_DISCOUNTS = 'sales.discounts.apply'
CAP_COMMISSIONS_VIEW = 'sales.commissions.view'
CAP_INVENTORY_VIEW = 'inventory.view'
CAP_CUSTOMERS_MANAGE = 'service.customers.manage'

_MAX_SEARCH = 25


class V1PosSurfaceMixin(V1InternalSurfaceMixin):
    """
    The three gates, in the order the rest of this API uses them.

    404 for a company the caller is not staff of — before anything else, because
    a 403 would confirm the tenant exists and let somebody map the platform by
    trying slugs.

    403 for `sales.pos.use`, re-resolved on every request.

    400 for a branch the caller may not sell from, from `resolve_pos_branch` —
    the SAME resolver the Web till uses, so neither surface can be more
    permissive than the other by accident.
    """

    throttle_classes = [AdminPosThrottle]

    def get_till(self, request):
        company = self.get_internal_company()
        self.require_capability(company, CAP_POS)
        return company

    def resolve_branch(self, request, company, raw):
        try:
            return pos_services.resolve_pos_branch(request.user, company, raw)
        except pos_services.PosValidationError as exc:
            # Bubbled as a 400 by the caller. Deliberately NOT a 404: the branch
            # may well exist and simply not be theirs, and the message from the
            # resolver already says the right thing without saying which.
            raise _BranchRefused(str(exc)) from None


class _BranchRefused(Exception):
    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


def _branch_error(exc: _BranchRefused) -> Response:
    return Response({'detail': exc.detail}, status=status.HTTP_400_BAD_REQUEST)


class V1PosContextView(V1PosSurfaceMixin, APIView):
    """
    GET — what this till may do, before it opens.

    Returns the branches the caller can ACTUALLY sell from, the payment methods
    a counter may pick, and one boolean per control the UI would otherwise have
    to guess about. A control that appears and then 403s is worse than one that
    was never offered.
    """

    def get(self, request, company_slug=None):
        company = self.get_till(request)
        branches = list(visible_branches(request.user, company).filter(is_active=True))

        may_assign = has_capability(request.user, company, CAP_ASSIGN_SELLER)
        return Response(pos_payloads.context_payload(
            company, branches,
            default_branch=pos_payloads.default_branch_for(company, branches),
            actor=request.user,
            can_manage_customers=has_capability(
                request.user, company, CAP_CUSTOMERS_MANAGE,
            ),
            can_assign_seller=may_assign,
            can_apply_discount=has_capability(request.user, company, CAP_DISCOUNTS),
            can_view_commissions=has_capability(
                request.user, company, CAP_COMMISSIONS_VIEW,
            ),
            # Only to somebody who may reassign. A list of colleagues is
            # staffing information, and a cashier who cannot use it has no
            # reason to receive it.
            sellers=(
                [
                    {
                        'id': m.user_id,
                        'name': (
                            pos_services.seller_display_name(m.user)
                            or m.user.get_username()
                        ),
                    }
                    for m in pos_services.eligible_pos_sellers(company)[:200]
                ]
                if may_assign else []
            ),
        ))


class V1PosProductSearchView(V1PosSurfaceMixin, APIView):
    """
    GET ?q=&branch= — the fallback for a torn label, or a shop with no scanner.

    Scoped to the company AND priced against the chosen branch, because a
    national stock figure would tell a cashier they can sell something that is
    three cities away.
    """

    def get(self, request, company_slug=None):
        company = self.get_till(request)
        try:
            branch = self.resolve_branch(
                request, company, request.query_params.get('branch'),
            )
        except _BranchRefused as exc:
            return _branch_error(exc)

        term = (request.query_params.get('q') or '').strip()
        if len(term) < 2:
            # Not an error. Two characters is where a search stops being a scan
            # of the whole catalogue.
            return Response({'results': []})

        from django.db.models import Q

        products = (
            Product.objects
            .filter(company=company, is_active=True)
            .filter(Q(name__icontains=term) | Q(barcodes__code__istartswith=term))
            .distinct()
            .prefetch_related('barcodes')[:_MAX_SEARCH]
        )
        return Response({
            'results': [pos_payloads.product_payload(p, branch) for p in products],
        })


class V1PosProductLookupView(V1PosSurfaceMixin, APIView):
    """
    GET ?code=&branch= — the scanner's endpoint. One indexed query, scoped to
    the company.

    A code belonging to ANOTHER company answers exactly like one that does not
    exist anywhere. Distinguishing them would turn this into an oracle for
    somebody else's catalogue.
    """

    def get(self, request, company_slug=None):
        company = self.get_till(request)
        try:
            branch = self.resolve_branch(
                request, company, request.query_params.get('branch'),
            )
        except _BranchRefused as exc:
            return _branch_error(exc)

        code = normalize_barcode(request.query_params.get('code', ''))
        if not code:
            return Response(
                {'detail': 'Indica un código.'}, status=status.HTTP_400_BAD_REQUEST,
            )

        entry = (
            ProductBarcode.objects
            .filter(company=company, code=code, is_active=True, product__is_active=True)
            .select_related('product')
            .first()
        )
        if entry is None:
            raise NotFound('Código no encontrado en esta empresa.')

        return Response(
            pos_payloads.product_payload(entry.product, branch, barcode=entry)
        )


class V1PosPreviewView(V1PosSurfaceMixin, APIView):
    """
    POST — what this basket costs, before charging it.

    It runs the SAME resolution and the SAME arithmetic the sale will, and then
    writes nothing: no order, no stock, no idempotency key. A preview that spent
    a key would make the sale that follows it a "retry" and return the preview's
    own answer forever.

    Cash is deliberately NOT validated here — the operator is still counting it
    out, and refusing to show a total until they finish would defeat the point.
    The sale checks it.
    """

    def post(self, request, company_slug=None):
        company = self.get_till(request)
        try:
            branch = self.resolve_branch(request, company, request.data.get('branch'))
        except _BranchRefused as exc:
            return _branch_error(exc)

        data = request.data
        try:
            priced = pos_services.build_pos_sale(
                operator=request.user,
                company=company,
                branch=branch,
                items=data.get('items'),
                customer=data.get('customer'),
                seller_id=data.get('seller'),
                payment_method=data.get('payment_method', PaymentMethod.CASH),
                coupon_code=data.get('coupon_code', ''),
                manual_discount_type=data.get('manual_discount_type', ''),
                manual_discount_value=data.get('manual_discount_value'),
                discount_reason=data.get('discount_reason', ''),
                validate_cash=False,
                # Authority is resolved HERE, from the caller's capabilities —
                # never read from the request body. A client that sent
                # `may_apply_manual_discount: true` changes nothing.
                may_assign_seller=has_capability(
                    request.user, company, CAP_ASSIGN_SELLER,
                ),
                may_apply_manual_discount=has_capability(
                    request.user, company, CAP_DISCOUNTS,
                ),
            )
        except pos_services.PosPermissionError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_403_FORBIDDEN)
        except pos_services.PosValidationError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(pos_payloads.preview_payload(
            priced,
            may_see_commission=has_capability(
                request.user, company, CAP_COMMISSIONS_VIEW,
            ),
        ))


class V1PosSaleView(V1PosSurfaceMixin, APIView):
    """
    POST — complete a counter sale.

    Body: branch, items[{product, quantity}], customer?, seller?,
    payment_method, amount_received?, idempotency_key, terms_confirmed, and the
    optional discount fields.

    PRICES AND TOTALS ARE NOT ACCEPTED. A client is told what to display; it is
    never asked what to charge.

    `terms_confirmed` must be `true`. Consent is ASSERTED by the operator, not
    inferred from the fact that a sale was attempted — handing the article over
    proves nothing was explained.

    IDEMPOTENT, and the mechanism is the domain's, not this view's. The same key
    with the same basket returns the same order and moves no stock a second
    time; the same key with a DIFFERENT basket is 409, because answering with
    the first sale would tell a caller their basket was sold when it was not.
    """

    throttle_classes = [AdminPosSaleThrottle]

    def post(self, request, company_slug=None):
        company = self.get_till(request)
        try:
            branch = self.resolve_branch(request, company, request.data.get('branch'))
        except _BranchRefused as exc:
            return _branch_error(exc)

        data = request.data
        try:
            order, created = pos_services.create_pos_sale(
                actor=request.user,
                company=company,
                branch=branch,
                items=data.get('items'),
                customer=data.get('customer'),
                seller_id=data.get('seller'),
                payment_method=data.get('payment_method', PaymentMethod.CASH),
                # NOT truncated. `[:64]` would fold two distinct long keys into
                # one and answer the second sale with the first one's order; the
                # service validates and rejects instead.
                idempotency_key=data.get('idempotency_key'),
                terms_confirmed=data.get('terms_confirmed') is True,
                coupon_code=data.get('coupon_code', ''),
                manual_discount_type=data.get('manual_discount_type', ''),
                manual_discount_value=data.get('manual_discount_value'),
                discount_reason=data.get('discount_reason', ''),
                amount_received=data.get('amount_received'),
                payment_reference=data.get('payment_reference', ''),
                external_reference=data.get('external_reference', ''),
                sale_notes=data.get('sale_notes', ''),
                may_assign_seller=has_capability(
                    request.user, company, CAP_ASSIGN_SELLER,
                ),
                may_apply_manual_discount=has_capability(
                    request.user, company, CAP_DISCOUNTS,
                ),
                request=request,
            )
        except pos_services.PosIdempotencyConflict as exc:
            return Response(
                {
                    'detail': str(exc),
                    'code': 'idempotency_conflict',
                    'existing_order': exc.existing_order.pk,
                },
                status=status.HTTP_409_CONFLICT,
            )
        except pos_services.PosPermissionError as exc:
            # 403, not 400: the request is fine, the caller is not allowed to
            # make it. A 400 would send an operator hunting for a typo.
            return Response({'detail': str(exc)}, status=status.HTTP_403_FORBIDDEN)
        except pos_services.PosValidationError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except inventory_services.InsufficientStockError as exc:
            # The whole sale rolled back. Nothing was charged and nothing moved.
            return Response(
                {
                    'detail': str(exc),
                    'code': 'insufficient_stock',
                    **(
                        {'available_elsewhere': self._elsewhere(request, company, branch)}
                        if self._elsewhere(request, company, branch) else {}
                    ),
                },
                status=status.HTTP_409_CONFLICT,
            )

        return Response(
            pos_payloads.sale_payload(
                order, branch,
                created=created,
                may_see_commission=has_capability(
                    request.user, company, CAP_COMMISSIONS_VIEW,
                ),
            ),
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    def _elsewhere(self, request, company, branch):
        """
        Where the units are, for somebody allowed to see stock.

        It informs; it moves nothing. Covering this sale from another shop is a
        transfer, and a transfer is a decision with paperwork.
        """
        if not has_capability(request.user, company, CAP_INVENTORY_VIEW):
            return []
        try:
            product_ids = [
                int(i.get('product'))
                for i in (request.data.get('items') or [])
                if str(i.get('product', '')).isdigit()
            ]
        except (TypeError, ValueError):
            return []
        others = visible_branches(request.user, company).exclude(pk=branch.pk)
        return pos_payloads.other_branches_with_stock(others, product_ids)
