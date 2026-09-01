import logging
from decimal import Decimal

import stripe
from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import viewsets, mixins, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from .customer_services import link_order_to_customer
from .models import (
    BranchStock, Category, Product, Order, OrderItem, CartItem, Review, Coupon,
    UserProfile, assert_items_match_order,
)
from . import checkout_services as checkout
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
        try:
            checkout.require_stripe_configured()
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

        line_items = checkout.build_stripe_line_items(order, pricing.discount_multiplier)
        try:
            stripe_session = checkout.create_stripe_session(
                order, line_items, customer_email=validated['customer_email'],
            )
        except stripe.StripeError as e:
            checkout.mark_stripe_failure(order, e)
            return Response(
                {'detail': 'Error al crear la sesión de Stripe. Intenta de nuevo.'},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        order.stripe_session_id = stripe_session.id
        order.save(update_fields=['stripe_session_id'])

        return Response({'url': stripe_session.url, 'order_id': order.id})


class StripeWebhookView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        webhook_secret = settings.STRIPE_WEBHOOK_SECRET
        if not webhook_secret:
            return Response(
                {'detail': 'Stripe webhook secret no configurado.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        stripe.api_key = settings.STRIPE_SECRET_KEY
        payload = request.body
        sig_header = request.META.get('HTTP_STRIPE_SIGNATURE', '')

        try:
            event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
        except ValueError:
            return Response({'detail': 'Payload inválido.'}, status=status.HTTP_400_BAD_REQUEST)
        except stripe.SignatureVerificationError:
            return Response({'detail': 'Firma Stripe inválida.'}, status=status.HTTP_400_BAD_REQUEST)

        event_type = event['type']
        if event_type == 'checkout.session.completed':
            self._handle_checkout_completed(event['data']['object'])
        elif event_type == 'checkout.session.expired':
            self._handle_checkout_expired(event['data']['object'])
        elif event_type == 'payment_intent.payment_failed':
            self._handle_payment_failed(event['data']['object'])

        return Response({'status': 'success'})

    def _handle_checkout_completed(self, session_obj):
        """
        Confirm a payment.

        TENANT RESOLUTION IN A WEBHOOK (Phase 2C)
        -----------------------------------------
        The company comes from `Order.company` — the database — and from nowhere
        else. Specifically NOT from the request host: Stripe calls a single
        endpoint, so the host says nothing about which tenant sold what. And not
        from Stripe metadata either: metadata is convenient for humans reading
        the dashboard, but it is data we sent to a third party and got back, so
        it can only ever be CHECKED against the database, never trusted over it.
        """
        stripe_session_id = session_obj.get('id', '')
        payment_intent_id = session_obj.get('payment_intent', '') or ''

        if not stripe_session_id:
            return

        try:
            order = Order.objects.get(stripe_session_id=stripe_session_id)
        except Order.DoesNotExist:
            return
        except Order.MultipleObjectsReturned:
            # stripe_session_id has a unique constraint but catch defensively
            return

        # If we did send a company in the metadata, it must agree with the order.
        # A mismatch means something is wrong upstream; refuse rather than guess.
        metadata = session_obj.get('metadata') or {}
        metadata_company = metadata.get('company_id')
        if metadata_company not in (None, '') and str(metadata_company) != str(order.company_id):
            logger.error(
                'Stripe metadata company mismatch for order %s: metadata=%s, order=%s',
                order.pk, metadata_company, order.company_id,
            )
            return

        # Same treatment for the branch: checked against the database, never
        # allowed to override it. Stock comes off the shelf the ORDER names.
        metadata_branch = metadata.get('branch_id')
        if metadata_branch not in (None, '') and str(metadata_branch) != str(
            order.fulfillment_branch_id or ''
        ):
            logger.error(
                'Stripe metadata branch mismatch for order %s: metadata=%s, order=%s',
                order.pk, metadata_branch, order.fulfillment_branch_id,
            )
            return

        # Idempotency guard — if already paid, do nothing
        if order.status == Order.Status.PAID:
            return

        with transaction.atomic():
            order = Order.objects.select_for_update().get(pk=order.pk)

            # Double-check after acquiring row lock
            if order.status == Order.Status.PAID:
                return

            # Decrement inventory AND write the Kardex in the same transaction.
            # record_sale_stock_movements is idempotent per (order, product): a
            # replayed webhook never subtracts stock twice. Insufficient stock is
            # recorded on order.payment_error (money is already captured) rather
            # than raising — same behaviour as before Phase 6.0.
            record_sale_stock_movements(order)

            order.status = Order.Status.PAID
            order.paid = True
            order.paid_at = timezone.now()
            order.stripe_payment_intent_id = payment_intent_id
            order.save(update_fields=[
                'status', 'paid', 'paid_at', 'stripe_payment_intent_id', 'payment_error',
            ])

            # Clear cart only after payment is confirmed
            if order.cart_session_key:
                # Scoped to the order's own tenant. A browser may hold carts in
                # several storefronts under one session key; paying at one must
                # not empty the others.
                CartItem.objects.filter(
                    session_key=order.cart_session_key,
                    product__company=order.company_id,
                ).delete()

            # Schedule transactional emails to fire after this transaction commits.
            # on_commit guarantees payment is persisted before email goes out.
            # Email failures never revert the payment (captured in email_send_error).
            _order_pk = order.pk
            transaction.on_commit(
                lambda: send_order_emails_after_payment(_order_pk)
            )

    def _handle_checkout_expired(self, session_obj):
        stripe_session_id = session_obj.get('id', '')
        if stripe_session_id:
            Order.objects.filter(
                stripe_session_id=stripe_session_id,
                status=Order.Status.PENDING_PAYMENT,
            ).update(status=Order.Status.EXPIRED)

    def _handle_payment_failed(self, payment_intent_obj):
        # For Stripe Checkout + card payments, a declined card fires this event
        # but the checkout session REMAINS OPEN so the user can retry with another card.
        # Marking the order as FAILED here would be wrong — the session may still result in payment.
        # Failure cases are handled by checkout.session.expired (_handle_checkout_expired).
        # For async payment methods (bank transfer, etc.) this would need different handling,
        # but those are out of scope for the current MVP.
        pass


class PaymentStatusView(APIView):
    """GET /api/payments/status/?session_id=cs_xxx — returns order payment status."""
    permission_classes = [permissions.AllowAny]
    throttle_classes = [PaymentStatusThrottle]

    def get(self, request):
        session_id = request.query_params.get('session_id', '').strip()
        if not session_id:
            return Response(
                {'detail': 'session_id es requerido.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            order = Order.objects.get(stripe_session_id=session_id)
        except Order.DoesNotExist:
            return Response(
                {'detail': 'Orden no encontrada.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Prevent authenticated users from reading other users' orders
        if (
            request.user.is_authenticated
            and order.user
            and order.user != request.user
        ):
            return Response({'detail': 'No autorizado.'}, status=status.HTTP_403_FORBIDDEN)

        status_messages = {
            Order.Status.PENDING_PAYMENT: 'Verificando pago con Stripe...',
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

        existing = CartItem.objects.filter(session_key=session_key, product=product).first()
        current_qty = existing.quantity if existing else 0
        new_qty = current_qty + quantity

        available = storefront_available_stock(request, product)
        if new_qty > available:
            return Response(
                {
                    'detail': (
                        f'Stock insuficiente para {product.name}. '
                        f'Disponible: {available}, en carrito: {current_qty}.'
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if existing:
            existing.quantity = new_qty
            existing.save(update_fields=['quantity'])
            return Response(CartItemSerializer(existing, context=self._cart_context()).data)

        item = CartItem.objects.create(
            session_key=session_key,
            product=product,
            quantity=quantity,
        )
        return Response(CartItemSerializer(item, context=self._cart_context()).data)
