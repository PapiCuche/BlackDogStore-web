import json
import logging
from decimal import Decimal

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import viewsets, mixins, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from .customer_services import link_order_to_customer
from .models import (
    BranchStock, Category, Product, Order, OrderItem, CartItem, PaymentTransaction,
    Review, Coupon, UserProfile, assert_items_match_order,
)
from . import checkout_services as checkout
from .payments import izipay
from .company_settings import build_identity_snapshot
from .inventory_services import record_sale_stock_movements
from .tenancy import (
    company_fulfillment_branch, resolve_storefront_company, storefront_available_stock,
    storefront_cart_items, storefront_categories, storefront_coupon, storefront_orders,
    storefront_fulfillment_branch, storefront_products,
)
from .permissions import get_user_role
from .email_services import send_order_emails_after_payment
from .serializers import (
    CategorySerializer,
    ProductSerializer,
    OrderSerializer,
    CartItemSerializer,
    ReviewSerializer,
    CouponSerializer,
    CheckoutInputSerializer,
)
from .throttles import (
    CouponThrottle,
    ReviewCreateThrottle,
    CheckoutThrottle,
    CartThrottle,
    PaymentStatusThrottle,
)


logger = logging.getLogger(__name__)


class CategoryViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Public categories of THIS storefront's tenant.

    Phase 2B: the queryset is born scoped. Nothing filters a global result set
    afterwards — a serializer that hides rows is a bug waiting to be routed
    around, and an unscoped queryset leaks through count(), pagination and
    ordering long before it reaches a serializer.
    """

    serializer_class = CategorySerializer

    def get_queryset(self):
        return storefront_categories(self.request).order_by('name')


_PRODUCT_ORDERING_WHITELIST = {
    'price': 'price',
    '-price': '-price',
    'name': 'name',
    '-name': '-name',
    'newest': '-id',
}

class ProductViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ProductSerializer

    def get_queryset(self):
        # Born scoped: tenant first, every other filter afterwards. A slug or a
        # category slug that belongs to another tenant therefore matches nothing
        # instead of matching and then being hidden.
        queryset = (
            storefront_products(self.request)
            .select_related('category', 'company')
            .prefetch_related('reviews')
        )
        slug = self.request.query_params.get('slug')
        category = self.request.query_params.get('category')
        search = self.request.query_params.get('search')
        in_stock = self.request.query_params.get('in_stock')
        ordering = self.request.query_params.get('ordering')
        if slug:
            queryset = queryset.filter(slug=slug)
        if category:
            queryset = queryset.filter(category__slug=category)
        if search:
            queryset = queryset.filter(name__icontains=search)
        if in_stock == 'true':
            # The annotation, not the column: "in stock" has to mean "the
            # branch that ships can ship it", or the filter promises deliveries
            # the checkout will refuse.
            queryset = queryset.filter(available_stock__gt=0)
        if ordering and ordering in _PRODUCT_ORDERING_WHITELIST:
            queryset = queryset.order_by(_PRODUCT_ORDERING_WHITELIST[ordering])
        return queryset


class ReviewViewSet(mixins.CreateModelMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = ReviewSerializer
    # GET: public (anyone can read reviews)
    # POST: requires login to prevent spam; anonymous submissions blocked
    permission_classes = [permissions.IsAuthenticatedOrReadOnly]

    def get_queryset(self):
        """
        Reviews reach their tenant through Product — Review has no company of its
        own, and adding a redundant one would be a second source of truth to keep
        in sync. Scoping by the storefront's products is enough and cannot drift.
        """
        product_id = self.request.query_params.get('product')
        if not product_id:
            return Review.objects.none()
        return Review.objects.filter(
            product_id=product_id,
            product__in=storefront_products(self.request),
        )

    def get_throttles(self):
        if self.action == 'create':
            return [ReviewCreateThrottle()]
        return []

    def perform_create(self, serializer):
        """
        The author is the account, not the payload.

        `author_name` used to be free text on an authenticated endpoint, so a
        signed-in customer could publish under the shop's own support name, or as
        another shopper. It is read-only now and filled from the user here. The column
        survives because reviews written before the login requirement carry a
        name and no user, and that name is the only attribution they have.
        """
        user = self.request.user
        display_name = (user.get_full_name() or '').strip() or user.get_username()
        serializer.save(user=user, author_name=display_name[:100])


class CouponValidateView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [CouponThrottle]

    def post(self, request):
        code = request.data.get('code', '').upper().strip()
        if not code:
            return Response({'detail': 'Código requerido.'}, status=status.HTTP_400_BAD_REQUEST)
        # Scoped to this storefront: two tenants may run the same code, and
        # honouring another company's discount is a leak and a financial error.
        coupon = storefront_coupon(request, code)
        if coupon is None:
            return Response(
                {'detail': 'Cupón no válido o inactivo.'}, status=status.HTTP_404_NOT_FOUND
            )
        if coupon.expires_at and coupon.expires_at < timezone.now():
            return Response({'detail': 'El cupón ha expirado.'}, status=status.HTTP_400_BAD_REQUEST)
        return Response(CouponSerializer(coupon).data)


class OrderViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only. Orders are created exclusively through the checkout flow."""
    serializer_class = OrderSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        """
        The customer's own orders ON THIS STOREFRONT.

        Phase 2C removed the staff shortcut that returned every order in the
        database. This is the CUSTOMER surface: a staff member browsing the shop
        is a customer here like anyone else, and internal order administration
        lives at /api/admin/orders/ with its own tenant scoping. The old branch
        would now expose every tenant's orders to any staff user.
        """
        return storefront_orders(self.request, self.request.user).prefetch_related(
            'items__product',
        )


class CreateCheckoutSessionView(APIView):
    """
    The BROWSER checkout. Anonymous, session cart, tenant from the Host.

    ⚠️  `AllowAny` IS DELIBERATE AND STAYS. This storefront takes guest orders
    and has done since before accounts existed; requiring a login here would
    turn away every buyer who does not want one. The native surface has a
    different rule (`/api/v1/customer/<slug>/checkout/` requires a session)
    because an app knows who is holding it — that is a difference between the
    two audiences, not a policy this view should adopt.

    M5 — the commercial reasoning moved to `checkout_services`. What is left
    here is what is genuinely browser-specific: reading the session cart and
    resolving the tenant from the Host. Every figure — prices, stock, coupon,
    total — is now computed by the same code the app uses, so the two can no
    longer disagree about what something costs.
    """

    permission_classes = [permissions.AllowAny]
    throttle_classes = [CheckoutThrottle]

    def post(self, request):
        # Before an Order exists: a checkout that cannot be paid should fail
        # here, not leave an unpayable row behind.
        try:
            credentials = checkout.require_payment_provider_configured()
        except checkout.CheckoutError as exc:
            return Response(exc.as_payload(), status=exc.status_code)

        checkout_ser = CheckoutInputSerializer(data=request.data)
        if not checkout_ser.is_valid():
            return Response(checkout_ser.errors, status=status.HTTP_400_BAD_REQUEST)
        validated = checkout_ser.validated_data
        session_key = validated['session_key']

        # The checkout's tenant comes from the STOREFRONT, never from the
        # request body. A `company` field in the payload is not consulted here.
        storefront_company = resolve_storefront_company(request)
        if storefront_company is None:
            return Response(
                {'detail': 'No se pudo determinar la tienda. Inténtelo nuevamente.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            branch = checkout.resolve_fulfillment_branch(storefront_company)

            # Loaded scoped to session AND storefront, so a cart holding another
            # tenant's products simply is not visible to this checkout.
            cart_items = list(
                storefront_cart_items(request, session_key).select_related('product')
            )
            if not cart_items:
                raise checkout.CheckoutError('El carrito está vacío.')

            lines = [
                checkout.CheckoutLine(product=item.product, quantity=item.quantity)
                for item in cart_items
            ]
            subtotal = checkout.validate_lines_and_subtotal(branch, lines)
            pricing = checkout.price_checkout(
                storefront_company, subtotal, validated.get('coupon_code', ''),
            )

            actor = request.user if request.user.is_authenticated else None
            order = checkout.create_pending_order(
                company=storefront_company,
                branch=branch,
                lines=lines,
                pricing=pricing,
                details=checkout.CustomerDetails(
                    name=validated['customer_name'],
                    email=validated['customer_email'],
                    phone=validated['customer_phone'],
                    document_type=validated['document_type'],
                    document_number=validated['document_number'],
                    delivery_method=validated['delivery_method'],
                    receipt_type=validated['receipt_type'],
                    accepted_terms=validated['accepted_terms'],
                    accepted_warranty_policy=validated['accepted_warranty_policy'],
                    address_line=validated.get('address_line', ''),
                    city=validated.get('city', ''),
                    district=validated.get('district', ''),
                    reference=validated.get('reference', ''),
                    notes=validated.get('notes', ''),
                ),
                actor=actor,
                order_user=actor,
                cart_session_key=session_key,
                # No idempotency key: the browser contract has none and is not
                # being changed to acquire one.
                idempotency=None,
            )
        except checkout.CheckoutError as exc:
            return Response(exc.as_payload(), status=exc.status_code)

        try:
            payment = checkout.start_payment_attempt(order, credentials=credentials)
        except checkout.CheckoutError as exc:
            checkout.mark_payment_failure(order, exc.args[0] if exc.args else '')
            return Response(exc.as_payload(), status=exc.status_code)

        # THE SESSION TOKEN IS NOT A CONFIRMATION. Nothing has been charged, no
        # stock has moved and the cart is untouched — this response only lets the
        # SDK draw a form.
        return Response(_payment_session_payload(order, payment))


def _payment_session_payload(order, payment) -> dict:
    """
    The checkout response.

    PUBLIC VALUES ONLY. The merchant code and the RSA public key are documented
    as browser-safe; the session token authorises exactly one transaction. The
    API key and the hash key are not here, are not in `config`, and have no path
    to any response in this project.
    """
    return {
        'order_id': order.id,
        'provider': izipay.PROVIDER,
        'environment': payment.environment,
        'transaction_id': payment.transaction_id,
        'authorization': payment.authorization,
        'merchant_code': payment.merchant_code,
        'public_key': payment.public_key,
        'config': payment.config,
    }


class IzipayNotificationView(APIView):
    """
    POST /api/payments/izipay/notification/ — the gateway's own word on a payment.

    THIS IS THE ONLY THING IN THE PROJECT THAT CAN MARK AN ORDER PAID.

    It takes no session, no login and no CSRF token, and that is not a gap: the
    caller is Izipay, not a browser, and the message authenticates itself
    cryptographically. A signature made with a key only the two parties hold is
    a stronger statement about who sent this than any cookie, and unlike a
    cookie it also proves the CONTENT was not edited in transit.

    Source IP is deliberately NOT a gate. Izipay does not publish stable ranges,
    and an allowlist built on guesses either rejects real payments after an
    infrastructure change or lulls us into treating the signature as optional.

    WHAT IS CHECKED, IN ORDER, AND WHY EACH ONE MATTERS
    ---------------------------------------------------
      signature        the message is genuinely Izipay's and unmodified
      transaction id   it refers to an attempt this database actually started
      order number     that attempt's own number, not another order's
      merchant         our merchant account, not someone else's
      currency         the currency we asked to be paid in
      amount           EXACTLY `Order.total` — not more, not less
      response code    the gateway actually authorised it

    Only then, under a row lock, does anything change.

    NO THROTTLE, DELIBERATELY. Every other public endpoint here declares one;
    this must not. A rate limit on the gateway's notifications is a rate limit
    on hearing that customers paid — the dropped message is a real payment that
    silently never confirms, and the retry it triggers arrives into the same
    limit. What protects this endpoint is that an unsigned message costs an
    attacker a 400 and changes nothing.
    """

    permission_classes = [permissions.AllowAny]

    def post(self, request):
        try:
            credentials = izipay.load_credentials()
        except izipay.IzipayError as exc:
            logger.error('IPN recibido con la pasarela mal configurada: %s', exc)
            return Response(
                {'detail': 'Pasarela no configurada.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # Parsed from the RAW body rather than `request.data` so the exact
        # `payloadHttp` string Izipay signed is the one that gets verified. DRF
        # would give the same string here, but reading the body directly makes
        # it impossible for a parser or renderer setting to quietly reshape the
        # bytes the signature covers.
        try:
            body = json.loads(request.body.decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            return Response({'detail': 'Payload inválido.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            result = izipay.parse_notification(body, credentials)
        except izipay.IzipayError as exc:
            # No order is touched, no stock moves, no cart is cleared. 400 and
            # nothing else: an unverified message has told us nothing.
            logger.warning('IPN rechazado: %s', exc)
            return Response({'detail': 'Notificación rechazada.'}, status=status.HTTP_400_BAD_REQUEST)

        # --- everything below reads ONLY from the signed payload -------------
        attempt = PaymentTransaction.objects.filter(
            provider=izipay.PROVIDER, transaction_id=result.transaction_id,
        ).select_related('order').first()
        if attempt is None:
            # Correctly signed but unknown to us. It does NOT create an order and
            # it does not get to pick one. Logged with the identifier only.
            logger.error(
                'IPN con transactionId desconocido: %s', result.transaction_id,
            )
            return Response({'detail': 'Transacción desconocida.'}, status=status.HTTP_404_NOT_FOUND)

        failure = self._integrity_failure(attempt, result, credentials)
        if failure is not None:
            self._record_integrity_failure(attempt, result, failure)
            logger.error(
                'IPN con fallo de integridad en la transacción %s: %s',
                result.transaction_id, failure,
            )
            return Response({'detail': 'Datos inconsistentes.'}, status=status.HTTP_400_BAD_REQUEST)

        if not result.authorized:
            self._record_rejection(attempt, result)
            # A decline is a normal outcome and a well-formed message: 200, so
            # the gateway does not retry it forever.
            return Response({'status': 'received'})

        self._confirm(attempt, result)
        return Response({'status': 'received'})

    # -- checks ----------------------------------------------------------------

    def _integrity_failure(self, attempt, result, credentials) -> str | None:
        """
        Compare what the gateway says against what the database already knew.

        Returns the reason, or None when everything agrees. Each of these is a
        separate sentence because each fails for a different reason and an
        operator needs to know which.
        """
        order = attempt.order

        if attempt.order_number and result.order_number != attempt.order_number:
            # A valid transaction paired with someone else's order number. There
            # is no way to tell which half is the truth, so neither is believed.
            return 'order_number mismatch'

        if result.merchant_code and result.merchant_code != credentials.merchant_code:
            # Another merchant's authorisation cannot pay our order.
            return 'merchant mismatch'

        expected_currency = (attempt.currency or credentials.currency).upper()
        if result.currency != expected_currency:
            # 100 of the wrong currency is not 100.
            return 'currency mismatch'

        expected_amount = Decimal(order.total).quantize(Decimal('0.01'))
        if result.amount != expected_amount:
            # BOTH DIRECTIONS. Less is obviously wrong; more is equally wrong —
            # it means this authorisation belongs to a different intent, and
            # keeping the difference would be reconciling by accident.
            return 'amount mismatch'

        if attempt.amount is not None and result.amount != attempt.amount:
            # The order's total was edited between opening the attempt and the
            # answer arriving. The buyer authorised the old figure.
            return 'amount differs from the attempt'

        return None

    # -- outcomes --------------------------------------------------------------

    def _record_integrity_failure(self, attempt, result, reason: str) -> None:
        PaymentTransaction.objects.filter(pk=attempt.pk).update(
            status=PaymentTransaction.Status.INTEGRITY_FAILED,
            signature_verified=True,
            response_code=result.response_code[:8],
            failure_reason=reason[:200],
        )

    def _record_rejection(self, attempt, result) -> None:
        PaymentTransaction.objects.filter(
            pk=attempt.pk, status=PaymentTransaction.Status.PENDING,
        ).update(
            status=PaymentTransaction.Status.REJECTED,
            signature_verified=True,
            response_code=result.response_code[:8],
            payment_method=result.pay_method[:32],
            failure_reason=(result.state_message or 'rechazado')[:200],
        )

    def _confirm(self, attempt, result) -> None:
        """
        Make the order paid — once, whatever happens.

        UNDER A ROW LOCK, because two valid notifications can arrive at the same
        instant and `if order.paid: return` is not a decision, it is a read that
        another writer can walk past between the read and the write. P0-E
        settled that argument: the database decides.

        Idempotent at three levels that each cover what the others cannot:
        the lock serialises concurrent notifications; the status re-read inside
        the lock stops the second one doing the work again; and the sale exits
        are keyed `(order, product)` in the inventory service, so even a replay
        that got past both would not subtract stock twice.
        """
        with transaction.atomic():
            order = Order.objects.select_for_update().get(pk=attempt.order_id)

            if order.status == Order.Status.PAID:
                # Already confirmed — record the attempt's outcome and stop.
                # No stock, no cart, no second e-mail.
                PaymentTransaction.objects.filter(
                    pk=attempt.pk, status=PaymentTransaction.Status.PENDING,
                ).update(
                    status=PaymentTransaction.Status.AUTHORIZED,
                    signature_verified=True,
                    response_code=result.response_code[:8],
                    payment_method=result.pay_method[:32],
                    authorization_code=result.authorization_code[:32],
                    reference_number=result.reference_number[:64],
                    provider_unique_id=result.unique_id[:64],
                    confirmed_at=timezone.now(),
                )
                return

            # Stock and the Kardex in the SAME transaction as the payment.
            # Idempotent per (order, product); a shortfall is recorded on the
            # order rather than raised, because the money is already captured.
            record_sale_stock_movements(order)

            now = timezone.now()
            order.status = Order.Status.PAID
            order.paid = True
            order.paid_at = now
            # `payment_method` is not touched: it already says ONLINE by
            # default for a storefront order, and a POS sale that was recorded
            # as cash does not become a gateway payment by being confirmed.
            order.save(update_fields=[
                'status', 'paid', 'paid_at', 'payment_error',
            ])

            PaymentTransaction.objects.filter(pk=attempt.pk).update(
                status=PaymentTransaction.Status.AUTHORIZED,
                signature_verified=True,
                response_code=result.response_code[:8],
                payment_method=result.pay_method[:32],
                authorization_code=result.authorization_code[:32],
                reference_number=result.reference_number[:64],
                provider_unique_id=result.unique_id[:64],
                confirmed_at=now,
            )

            # ONLY NOW. The cart survives a decline, a cancellation and a forged
            # notification; it is emptied when, and only when, a verified payment
            # says so. Scoped to this order's tenant: one browser session may
            # hold carts in several storefronts and paying at one must not empty
            # the others.
            if order.cart_session_key:
                CartItem.objects.filter(
                    session_key=order.cart_session_key,
                    product__company=order.company_id,
                ).delete()

            _order_pk = order.pk
            transaction.on_commit(lambda: send_order_emails_after_payment(_order_pk))


class PaymentStatusView(APIView):
    """
    GET /api/payments/status/?reference=<transaction_id> — has the payment landed?

    WHAT THE BROWSER IS TOLD, AND WHY IT ASKS AT ALL. The SDK's callback fires in
    the buyer's page and can be replayed, edited or fabricated; it is a hint that
    the form finished, not evidence of a payment. The success page therefore says
    "verificando" and asks this endpoint, which answers from the order — which
    only the notification endpoint can have changed.

    The reference is the attempt's own `transaction_id`: twenty digits generated
    server-side, so possession of one is not something a stranger can arrive at
    by counting. That is the same possession-only model the previous gateway's
    session id had, carried over rather than downgraded to an enumerable
    `order_id` — see the P1-D note in the docs.

    The response carries no personal data: a status, a total and a message.
    """

    permission_classes = [permissions.AllowAny]
    throttle_classes = [PaymentStatusThrottle]

    def get(self, request):
        reference = request.query_params.get('reference', '').strip()
        if not reference:
            return Response(
                {'detail': 'reference es requerido.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        attempt = PaymentTransaction.objects.filter(
            transaction_id=reference,
        ).select_related('order').first()
        if attempt is None:
            return Response(
                {'detail': 'Orden no encontrada.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        order = attempt.order

        # An order that belongs to somebody's account is theirs to read. An
        # anonymous guest order has no owner to compare against and is protected
        # by the reference alone.
        if (
            request.user.is_authenticated
            and order.user
            and order.user != request.user
        ):
            return Response({'detail': 'No autorizado.'}, status=status.HTTP_403_FORBIDDEN)

        status_messages = {
            Order.Status.PENDING_PAYMENT: 'Verificando pago...',
            Order.Status.PAID: 'Pago confirmado',
            Order.Status.FAILED: 'El pago no pudo procesarse',
            Order.Status.CANCELLED: 'Orden cancelada',
            Order.Status.EXPIRED: 'La sesión de pago expiró',
            Order.Status.REFUNDED: 'Pago reembolsado',
        }

        return Response({
            'order_id': order.id,
            'status': order.status,
            'paid': order.paid,
            'total': str(order.total),
            'message': status_messages.get(order.status, 'Estado desconocido'),
        })


class CartViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    queryset = CartItem.objects.all()
    serializer_class = CartItemSerializer
    throttle_classes = [CartThrottle]

    def get_queryset(self):
        """
        Scoped by session AND storefront. One browser may hold a cart in several
        tenants at once; each storefront sees only its own.
        """
        session_key = self.request.query_params.get('session_key')
        return storefront_cart_items(self.request, session_key).select_related('product')

    def get_serializer_context(self):
        """
        Tell the nested ProductSerializer which shelf to report stock from.

        Without it the cart would show the company aggregate while the catalogue
        the item came from showed the fulfillment branch — two different numbers
        for the same product on two screens of the same purchase.
        """
        context = super().get_serializer_context()
        context['storefront_branch'] = storefront_fulfillment_branch(self.request)
        return context

    def _cart_context(self):
        return {
            'request': self.request,
            'storefront_branch': storefront_fulfillment_branch(self.request),
        }

    def partial_update(self, request, *args, **kwargs):
        """PATCH /api/cart/{id}/?session_key=... — only quantity allowed; validates stock."""
        session_key = request.query_params.get('session_key', '').strip()
        if not session_key:
            return Response(
                {'detail': 'session_key es requerido.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Scoped to this storefront: an item of another tenant's cart answers
        # like one that does not exist, even with the right session key.
        item = get_object_or_404(
            storefront_cart_items(request, session_key).select_related('product'),
            pk=kwargs.get('pk'),
        )

        try:
            quantity = int(request.data.get('quantity', item.quantity))
        except (TypeError, ValueError):
            return Response(
                {'detail': 'quantity debe ser un número entero.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if quantity <= 0:
            return Response(
                {'detail': 'La cantidad debe ser mayor a 0.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Phase 2D: against the FULFILLMENT branch, the same number checkout
        # will use. Validating against the company aggregate here would let a
        # cart fill up with units the order could never take.
        available = storefront_available_stock(request, item.product)
        if quantity > available:
            return Response(
                {
                    'detail': (
                        f'Stock insuficiente para {item.product.name}. '
                        f'Disponible: {available}.'
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        item.quantity = quantity
        item.save(update_fields=['quantity'])
        return Response(CartItemSerializer(item, context=self._cart_context()).data)

    def update(self, request, *args, **kwargs):
        """PUT is not supported — use PATCH to update quantity."""
        return Response(status=status.HTTP_405_METHOD_NOT_ALLOWED)

    def create(self, request, *args, **kwargs):
        """
        Adding to the cart goes through `add`, never through the raw create.

        P0: this route was reachable and answered 500 for EVERY input, because
        `CartItemSerializer.product` is read-only, so the insert reached the
        database with a null product.

        It is closed rather than made to work, because making it work would mean
        duplicating everything `add` exists to do: scope the product lookup to
        this storefront, require a session key, and check available stock. A
        second, thinner way to write a CartItem would be a way to put another
        tenant's product in a cart — the exact vector the comment on `add`
        describes closing.
        """
        return Response(
            {'detail': 'Usa POST /api/cart/add/ para agregar al carrito.'},
            status=status.HTTP_405_METHOD_NOT_ALLOWED,
        )

    @action(detail=False, methods=['post'])
    def add(self, request):
        session_key = request.data.get('session_key', '').strip()
        product_id = request.data.get('product')

        if not session_key:
            return Response(
                {'detail': 'session_key es requerido.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            quantity = int(request.data.get('quantity', 1))
        except (TypeError, ValueError):
            return Response(
                {'detail': 'quantity debe ser un número entero.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if quantity <= 0:
            return Response(
                {'detail': 'La cantidad debe ser mayor a 0.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # CART BOUNDARY (Phase 2B).
        # Cart itself is not tenantised yet — that is Phase 2C. But Product now
        # belongs to a company, which opens a brand-new cross-tenant vector: a
        # storefront could otherwise add another tenant's product id to a cart
        # and carry it all the way into checkout. Scoping the lookup to this
        # storefront's products closes it now, without touching the Cart model.
        # A foreign product answers exactly like a non-existent one.
        try:
            product = storefront_products(request).get(pk=product_id)
        except (Product.DoesNotExist, TypeError, ValueError):
            return Response(
                {'detail': 'Producto no encontrado o no disponible.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        # CONCURRENCY — Phase 0.3 / P0-E.
        #
        # This used to read the row, decide, and then either create or assign
        # `quantity = <what was read> + n`. Both halves are races. Two first adds
        # arriving together each found nothing and each created a row, leaving
        # the basket holding one article twice; two increments each read the same
        # number and the second overwrote the first, losing units the shopper
        # asked for.
        #
        # Now the row is taken (or made) in one statement, and the increment is
        # computed BY THE DATABASE with `F()`, so simultaneous adds add up
        # instead of overwriting. `UNIQUE(session_key, product)` is what makes
        # `get_or_create` a real guarantee rather than a smaller window: without
        # it, two concurrent creators both succeed.
        #
        # The IntegrityError branch is the lost race — the other request created
        # the row between our SELECT and our INSERT. That is a normal outcome, so
        # it is handled and answered like any other add, never surfaced as a 500.
        try:
            with transaction.atomic():
                item, created = CartItem.objects.get_or_create(
                    session_key=session_key, product=product,
                    defaults={'quantity': 0},
                )
        except IntegrityError:
            created = False
            item = CartItem.objects.get(session_key=session_key, product=product)

        current_qty = 0 if created else item.quantity
        new_qty = current_qty + quantity

        available = storefront_available_stock(request, product)
        if new_qty > available:
            if created:
                # The placeholder row was ours and holds nothing. Leaving it
                # would put an empty line in a basket the shopper never filled.
                item.delete()
            return Response(
                {
                    'detail': (
                        f'Stock insuficiente para {product.name}. '
                        f'Disponible: {available}, en carrito: {current_qty}.'
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ADD, not SET. The endpoint's contract is "put n more in the basket",
        # and only the database can add to a number it is holding without first
        # telling us what it is.
        CartItem.objects.filter(pk=item.pk).update(quantity=F('quantity') + quantity)
        item.refresh_from_db(fields=['quantity'])
        return Response(CartItemSerializer(item, context=self._cart_context()).data)
