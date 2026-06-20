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

from .models import Category, Product, Order, OrderItem, CartItem, Review, Coupon
from .serializers import (
    CategorySerializer,
    ProductSerializer,
    OrderSerializer,
    CartItemSerializer,
    ReviewSerializer,
    CouponSerializer,
)
from .throttles import (
    CouponThrottle,
    ReviewCreateThrottle,
    CheckoutThrottle,
    CartThrottle,
    PaymentStatusThrottle,
)


class CategoryViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Category.objects.all()
    serializer_class = CategorySerializer


class ProductViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ProductSerializer

    def get_queryset(self):
        queryset = Product.objects.prefetch_related('reviews').all()
        slug = self.request.query_params.get('slug')
        category = self.request.query_params.get('category')
        search = self.request.query_params.get('search')
        if slug:
            queryset = queryset.filter(slug=slug)
        if category:
            queryset = queryset.filter(category__slug=category)
        if search:
            queryset = queryset.filter(name__icontains=search)
        return queryset


class ReviewViewSet(mixins.CreateModelMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = ReviewSerializer
    # GET: public (anyone can read reviews)
    # POST: requires login to prevent spam; anonymous submissions blocked
    permission_classes = [permissions.IsAuthenticatedOrReadOnly]

    def get_queryset(self):
        product_id = self.request.query_params.get('product')
        if product_id:
            return Review.objects.filter(product_id=product_id)
        return Review.objects.none()

    def get_throttles(self):
        if self.action == 'create':
            return [ReviewCreateThrottle()]
        return []

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class CouponValidateView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [CouponThrottle]

    def post(self, request):
        code = request.data.get('code', '').upper().strip()
        if not code:
            return Response({'detail': 'Código requerido.'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            coupon = Coupon.objects.get(code=code, is_active=True)
            if coupon.expires_at and coupon.expires_at < timezone.now():
                return Response({'detail': 'El cupón ha expirado.'}, status=status.HTTP_400_BAD_REQUEST)
            return Response(CouponSerializer(coupon).data)
        except Coupon.DoesNotExist:
            return Response({'detail': 'Cupón no válido o inactivo.'}, status=status.HTTP_404_NOT_FOUND)


class OrderViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only. Orders are created exclusively through the checkout flow."""
    serializer_class = OrderSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if user.is_authenticated:
            if user.is_staff:
                return Order.objects.prefetch_related('items__product').all()
            return Order.objects.prefetch_related('items__product').filter(user=user)
        return Order.objects.none()


class CreateCheckoutSessionView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [CheckoutThrottle]

    def post(self, request):
        secret_key = settings.STRIPE_SECRET_KEY
        if not secret_key:
            return Response(
                {'detail': 'Stripe no está configurado. Define STRIPE_SECRET_KEY.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        stripe.api_key = secret_key

        # Extract input — all economic validation happens in backend, never trust frontend values
        session_key = request.data.get('session_key', '').strip()
        customer_name = request.data.get('customer_name', '').strip()
        customer_email = request.data.get('customer_email', '').strip()
        coupon_code = request.data.get('coupon_code', '').upper().strip()

        if not session_key:
            return Response(
                {'detail': 'session_key es requerido.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Load cart and validate stock
        cart_items = list(
            CartItem.objects.select_related('product').filter(session_key=session_key)
        )
        if not cart_items:
            return Response(
                {'detail': 'El carrito está vacío.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        stock_errors = []
        subtotal = Decimal('0.00')
        for item in cart_items:
            if item.quantity <= 0:
                stock_errors.append(f'Cantidad inválida para {item.product.name}.')
                continue
            if item.product.inventory < item.quantity:
                stock_errors.append(
                    f'Stock insuficiente para {item.product.name}. '
                    f'Disponible: {item.product.inventory}, solicitado: {item.quantity}.'
                )
                continue
            subtotal += item.product.price * item.quantity

        if stock_errors:
            return Response(
                {'detail': 'Problemas con el carrito.', 'errors': stock_errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Validate and apply coupon (from backend, ignoring any frontend discount)
        discount_multiplier = Decimal('1.0')
        discount_amount = Decimal('0.00')
        applied_coupon = None
        if coupon_code:
            try:
                coupon = Coupon.objects.get(code=coupon_code, is_active=True)
                if coupon.expires_at and coupon.expires_at < timezone.now():
                    return Response(
                        {'detail': 'El cupón ha expirado.'},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                discount_multiplier = (
                    (Decimal('100') - Decimal(str(coupon.discount_percent))) / Decimal('100')
                )
                discount_amount = (subtotal * (1 - discount_multiplier)).quantize(Decimal('0.01'))
                applied_coupon = coupon
            except Coupon.DoesNotExist:
                return Response(
                    {'detail': 'Cupón no válido o inactivo.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        total = (subtotal - discount_amount).quantize(Decimal('0.01'))

        # Create order inside a transaction — cart NOT deleted, inventory NOT decremented
        with transaction.atomic():
            order = Order.objects.create(
                user=request.user if request.user.is_authenticated else None,
                customer_name=customer_name,
                customer_email=customer_email,
                total=total,
                discount_amount=discount_amount,
                coupon_code=applied_coupon.code if applied_coupon else '',
                cart_session_key=session_key,
                status=Order.Status.PENDING_PAYMENT,
                paid=False,
            )
            for item in cart_items:
                OrderItem.objects.create(
                    order=order,
                    product=item.product,
                    quantity=item.quantity,
                    price=item.product.price,
                )

        # Create Stripe checkout session
        line_items = []
        for item in order.items.select_related('product').all():
            unit_amount = int(item.price * discount_multiplier * 100)
            line_items.append({
                'price_data': {
                    'currency': settings.STRIPE_CURRENCY,
                    'product_data': {'name': item.product.name},
                    'unit_amount': unit_amount,
                },
                'quantity': item.quantity,
            })

        domain = settings.STRIPE_DOMAIN
        try:
            stripe_session = stripe.checkout.Session.create(
                payment_method_types=['card'],
                line_items=line_items,
                mode='payment',
                success_url=f"{domain}/checkout/success?session_id={{CHECKOUT_SESSION_ID}}",
                cancel_url=f"{domain}/checkout?cancelled=true",
                customer_email=customer_email or None,
                metadata={'order_id': str(order.id)},
            )
        except stripe.StripeError as e:
            order.status = Order.Status.FAILED
            order.payment_error = str(e)
            order.save(update_fields=['status', 'payment_error'])
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

        # Idempotency guard — if already paid, do nothing
        if order.status == Order.Status.PAID:
            return

        with transaction.atomic():
            order = Order.objects.select_for_update().get(pk=order.pk)

            # Double-check after acquiring row lock
            if order.status == Order.Status.PAID:
                return

            # Decrement inventory atomically for each item
            for item in order.items.select_related('product').all():
                updated = Product.objects.filter(
                    pk=item.product.pk,
                    inventory__gte=item.quantity,
                ).update(inventory=F('inventory') - item.quantity)

                if updated == 0:
                    # Stock ran out between checkout creation and payment confirmation.
                    # Money was already collected — record the discrepancy for admin review.
                    order.payment_error = (
                        (order.payment_error + '\n' if order.payment_error else '') +
                        f'Stock insuficiente para producto ID={item.product.pk} al confirmar pago.'
                    )

            order.status = Order.Status.PAID
            order.paid = True
            order.paid_at = timezone.now()
            order.stripe_payment_intent_id = payment_intent_id
            order.save(update_fields=[
                'status', 'paid', 'paid_at', 'stripe_payment_intent_id', 'payment_error',
            ])

            # Clear cart only after payment is confirmed
            if order.cart_session_key:
                CartItem.objects.filter(session_key=order.cart_session_key).delete()

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
        session_key = self.request.query_params.get('session_key')
        if session_key:
            return CartItem.objects.filter(session_key=session_key)
        return CartItem.objects.none()

    def partial_update(self, request, *args, **kwargs):
        """PATCH /api/cart/{id}/?session_key=... — only quantity allowed; validates stock."""
        session_key = request.query_params.get('session_key', '').strip()
        if not session_key:
            return Response(
                {'detail': 'session_key es requerido.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        item = get_object_or_404(
            CartItem.objects.select_related('product'),
            pk=kwargs.get('pk'),
            session_key=session_key,
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

        if quantity > item.product.inventory:
            return Response(
                {
                    'detail': (
                        f'Stock insuficiente para {item.product.name}. '
                        f'Disponible: {item.product.inventory}.'
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        item.quantity = quantity
        item.save(update_fields=['quantity'])
        return Response(CartItemSerializer(item).data)

    def update(self, request, *args, **kwargs):
        """PUT is not supported — use PATCH to update quantity."""
        return Response(status=status.HTTP_405_METHOD_NOT_ALLOWED)

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

        try:
            product = Product.objects.get(pk=product_id)
        except (Product.DoesNotExist, TypeError, ValueError):
            return Response(
                {'detail': 'Producto no encontrado.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        existing = CartItem.objects.filter(session_key=session_key, product=product).first()
        current_qty = existing.quantity if existing else 0
        new_qty = current_qty + quantity

        if new_qty > product.inventory:
            return Response(
                {
                    'detail': (
                        f'Stock insuficiente para {product.name}. '
                        f'Disponible: {product.inventory}, en carrito: {current_qty}.'
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if existing:
            existing.quantity = new_qty
            existing.save(update_fields=['quantity'])
            return Response(CartItemSerializer(existing).data)

        item = CartItem.objects.create(
            session_key=session_key,
            product=product,
            quantity=quantity,
        )
        return Response(CartItemSerializer(item).data)
