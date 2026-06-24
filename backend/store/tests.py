"""
Black Dog Store backend tests.
Phase 0.1 (24 tests): models, catalog API, cart, coupons.
Phase 1 (+16 tests): checkout flow, inventory, webhook, payment status.
Audit Phase 1 (+8 tests): Stripe error path, OrderViewSet access control, cross-user isolation.
Phase 2.0 (+22 tests): register/login security, cart PATCH validation, review permissions.
Phase 2.1 (+19 tests): cookie JWT auth, CSRF enforcement, refresh/logout/csrf endpoints.
Phase 2.2 (+10 tests): logout CSRF enforcement, token blacklist after rotation and logout.
Phase 2.3 (+28 tests): AccountToken model, email verification flow, password reset, change password.
Phase 3.0 (+34 tests): UserProfile auto-create, RBAC permissions, admin endpoints, audit log, OrderViewSet roles.
Phase 3.1 (+9 tests): paginated responses, search/filter users, filter audit logs, rate limits.
Audit 3.1 (+14 tests): page_size cap, page invalid, CSRF on role change, extra fields ignored, actor filter, pagination edge cases.
Phase 3.2 (+52 tests): admin products CRUD, inventory adjust, categories, is_active filter, regression.
"""
from decimal import Decimal
from unittest.mock import patch, MagicMock

import stripe as stripe_lib

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from django.utils import timezone
from datetime import timedelta
from rest_framework.test import APIClient
from rest_framework import status

from .models import AccountToken, AdminAuditLog, Category, Product, Coupon, Order, OrderItem, CartItem, Review, UserProfile

User = get_user_model()


# ---------------------------------------------------------------------------
# Phase 0.1 tests
# ---------------------------------------------------------------------------

class CategoryModelTest(TestCase):
    def test_create_and_str(self):
        cat = Category.objects.create(name="MacBook", slug="macbook-test")
        self.assertEqual(str(cat), "MacBook")
        self.assertEqual(cat.slug, "macbook-test")

    def test_slug_unique(self):
        Category.objects.create(name="iPad", slug="ipad-test")
        with self.assertRaises(Exception):
            Category.objects.create(name="iPad Copia", slug="ipad-test")


class ProductModelTest(TestCase):
    def setUp(self):
        self.category = Category.objects.create(name="MacBook", slug="macbook-model-test")

    def test_create_product(self):
        product = Product.objects.create(
            name="MacBook Pro M4",
            slug="macbook-pro-m4-test",
            price=Decimal("9999.00"),
            inventory=5,
            category=self.category,
        )
        self.assertEqual(str(product), "MacBook Pro M4")
        self.assertEqual(product.price, Decimal("9999.00"))
        self.assertEqual(product.inventory, 5)

    def test_product_defaults(self):
        product = Product.objects.create(
            name="Test Product Defaults",
            slug="test-product-defaults-001",
            price=Decimal("100.00"),
        )
        self.assertEqual(product.inventory, 0)
        self.assertEqual(product.image_url, "")
        self.assertIsNone(product.category)


class CouponModelTest(TestCase):
    def test_active_coupon(self):
        coupon = Coupon.objects.create(
            code="DESCUENTO10",
            discount_percent=10,
            is_active=True,
        )
        self.assertEqual(str(coupon), "DESCUENTO10 — 10%")
        self.assertTrue(coupon.is_active)
        self.assertIsNone(coupon.expires_at)

    def test_inactive_coupon(self):
        coupon = Coupon.objects.create(
            code="VENCIDO",
            discount_percent=20,
            is_active=False,
        )
        self.assertFalse(coupon.is_active)

    def test_expired_coupon_still_has_future_date(self):
        past = timezone.now() - timedelta(days=1)
        coupon = Coupon.objects.create(
            code="PASADO",
            discount_percent=15,
            is_active=True,
            expires_at=past,
        )
        self.assertTrue(coupon.expires_at < timezone.now())


class ProductAPITest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.category = Category.objects.create(name="iPad Test", slug="ipad-api-test")
        self.product = Product.objects.create(
            name="iPad Pro M4 Test",
            slug="ipad-pro-m4-api-test",
            price=Decimal("3999.00"),
            inventory=8,
            category=self.category,
        )

    def test_list_products(self):
        response = self.client.get("/api/products/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        results = data.get("results", data) if isinstance(data, dict) else data
        self.assertGreaterEqual(len(results), 1)

    def test_filter_by_slug(self):
        response = self.client.get("/api/products/?slug=ipad-pro-m4-api-test")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        results = data.get("results", data) if isinstance(data, dict) else data
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["name"], "iPad Pro M4 Test")

    def test_filter_by_category(self):
        response = self.client.get("/api/products/?category=ipad-api-test")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        results = data.get("results", data) if isinstance(data, dict) else data
        self.assertEqual(len(results), 1)

    def test_search_products(self):
        response = self.client.get("/api/products/?search=iPad+Pro+M4+Test")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        results = data.get("results", data) if isinstance(data, dict) else data
        self.assertEqual(len(results), 1)

    def test_product_not_found(self):
        response = self.client.get("/api/products/?slug=slug-that-does-not-exist-xyz")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        results = data.get("results", data) if isinstance(data, dict) else data
        self.assertEqual(len(results), 0)


class CouponAPITest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.coupon = Coupon.objects.create(
            code="BDOG10",
            discount_percent=10,
            is_active=True,
        )

    def test_validate_valid_coupon(self):
        response = self.client.post("/api/coupons/validate/", {"code": "BDOG10"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()["discount_percent"], 10)

    def test_validate_inactive_coupon(self):
        self.coupon.is_active = False
        self.coupon.save()
        response = self.client.post("/api/coupons/validate/", {"code": "BDOG10"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_validate_expired_coupon(self):
        self.coupon.expires_at = timezone.now() - timedelta(days=1)
        self.coupon.save()
        response = self.client.post("/api/coupons/validate/", {"code": "BDOG10"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_validate_nonexistent_coupon(self):
        response = self.client.post("/api/coupons/validate/", {"code": "FAKE"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_validate_empty_code(self):
        response = self.client.post("/api/coupons/validate/", {"code": ""}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_case_insensitive_validation(self):
        response = self.client.post("/api/coupons/validate/", {"code": "bdog10"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)


class CartAPITest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.category = Category.objects.create(name="Cables Test", slug="cables-cart-test")
        self.product = Product.objects.create(
            name="Cable USB-C Test",
            slug="cable-usbc-cart-test",
            price=Decimal("149.00"),
            inventory=50,
            category=self.category,
        )
        self.session_key = "test-session-key-001"

    def test_empty_cart(self):
        response = self.client.get(f"/api/cart/?session_key={self.session_key}")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        results = data.get("results", data) if isinstance(data, dict) else data
        self.assertEqual(len(results), 0)

    def test_add_to_cart(self):
        response = self.client.post("/api/cart/add/", {
            "session_key": self.session_key,
            "product": self.product.id,
            "quantity": 2,
        }, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()["quantity"], 2)

    def test_add_same_product_increments_quantity(self):
        self.client.post("/api/cart/add/", {
            "session_key": self.session_key,
            "product": self.product.id,
            "quantity": 1,
        }, format="json")
        self.client.post("/api/cart/add/", {
            "session_key": self.session_key,
            "product": self.product.id,
            "quantity": 2,
        }, format="json")
        items = CartItem.objects.filter(session_key=self.session_key)
        self.assertEqual(items.count(), 1)
        self.assertEqual(items.first().quantity, 3)

    def test_cart_isolation_by_session_key(self):
        self.client.post("/api/cart/add/", {
            "session_key": "session-A",
            "product": self.product.id,
            "quantity": 1,
        }, format="json")
        response = self.client.get("/api/cart/?session_key=session-B")
        data = response.json()
        results = data.get("results", data) if isinstance(data, dict) else data
        self.assertEqual(len(results), 0)


class OrderModelTest(TestCase):
    def setUp(self):
        self.product = Product.objects.create(
            name="iPhone Test Order",
            slug="iphone-test-order-001",
            price=Decimal("4299.00"),
            inventory=5,
        )

    def test_create_order_str(self):
        order = Order.objects.create(
            customer_name="Carlos García",
            customer_email="carlos@example.com",
            total=Decimal("4299.00"),
        )
        self.assertIn("Carlos García", str(order))
        self.assertFalse(order.paid)

    def test_order_paid_field_defaults_false(self):
        order = Order.objects.create(total=Decimal("100.00"))
        self.assertFalse(order.paid)


# ---------------------------------------------------------------------------
# Phase 1 tests
# ---------------------------------------------------------------------------

class CartStockValidationTest(TestCase):
    """Cart add() endpoint validates inventory before accepting items."""

    def setUp(self):
        self.client = APIClient()
        self.product = Product.objects.create(
            name="iPhone 16 Pro Stock Test",
            slug="iphone-16-pro-stock-test",
            price=Decimal("5999.00"),
            inventory=3,
        )
        self.session_key = "stock-test-session-001"

    def test_add_zero_quantity_rejected(self):
        response = self.client.post("/api/cart/add/", {
            "session_key": self.session_key,
            "product": self.product.id,
            "quantity": 0,
        }, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_add_negative_quantity_rejected(self):
        response = self.client.post("/api/cart/add/", {
            "session_key": self.session_key,
            "product": self.product.id,
            "quantity": -1,
        }, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_add_exceeds_inventory_rejected(self):
        response = self.client.post("/api/cart/add/", {
            "session_key": self.session_key,
            "product": self.product.id,
            "quantity": 10,
        }, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("insuficiente", response.json()["detail"])

    def test_add_exact_inventory_accepted(self):
        response = self.client.post("/api/cart/add/", {
            "session_key": self.session_key,
            "product": self.product.id,
            "quantity": 3,
        }, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_incremental_add_respects_inventory(self):
        """Adding 2 then 2 more when only 3 available must fail on second add."""
        self.client.post("/api/cart/add/", {
            "session_key": self.session_key,
            "product": self.product.id,
            "quantity": 2,
        }, format="json")
        response = self.client.post("/api/cart/add/", {
            "session_key": self.session_key,
            "product": self.product.id,
            "quantity": 2,
        }, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


@override_settings(
    STRIPE_SECRET_KEY='sk_test_fake_key',
    STRIPE_WEBHOOK_SECRET='whsec_fake_secret',
    STRIPE_DOMAIN='http://localhost:3000',
)
class CheckoutFlowTest(TestCase):
    """CreateCheckoutSessionView: validates cart, calculates totals from DB, preserves cart."""

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.product = Product.objects.create(
            name="MacBook Air M3 Checkout Test",
            slug="macbook-air-m3-checkout-test",
            price=Decimal("7499.00"),
            inventory=5,
        )
        self.session_key = "checkout-flow-session-001"
        CartItem.objects.create(
            session_key=self.session_key,
            product=self.product,
            quantity=1,
        )

    def _mock_stripe_session(self, session_id="cs_test_abc123"):
        mock = MagicMock()
        mock.id = session_id
        mock.url = "https://checkout.stripe.com/pay/cs_test_abc123"
        return mock

    @patch("stripe.checkout.Session.create")
    def test_checkout_creates_order_with_pending_status(self, mock_create):
        mock_create.return_value = self._mock_stripe_session()
        response = self.client.post("/api/payments/create-checkout-session/", {
            "session_key": self.session_key,
            "customer_name": "Ana Torres",
            "customer_email": "ana@example.com",
        }, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        order = Order.objects.first()
        self.assertEqual(order.status, Order.Status.PENDING_PAYMENT)
        self.assertFalse(order.paid)

    @patch("stripe.checkout.Session.create")
    def test_checkout_does_not_delete_cart(self, mock_create):
        mock_create.return_value = self._mock_stripe_session()
        self.client.post("/api/payments/create-checkout-session/", {
            "session_key": self.session_key,
            "customer_name": "Ana Torres",
            "customer_email": "ana@example.com",
        }, format="json")
        cart_count = CartItem.objects.filter(session_key=self.session_key).count()
        self.assertEqual(cart_count, 1)

    @patch("stripe.checkout.Session.create")
    def test_checkout_saves_stripe_session_id(self, mock_create):
        mock_create.return_value = self._mock_stripe_session("cs_test_xyz")
        self.client.post("/api/payments/create-checkout-session/", {
            "session_key": self.session_key,
            "customer_name": "Ana Torres",
            "customer_email": "ana@example.com",
        }, format="json")
        order = Order.objects.first()
        self.assertEqual(order.stripe_session_id, "cs_test_xyz")

    @patch("stripe.checkout.Session.create")
    def test_checkout_calculates_total_from_db_not_frontend(self, mock_create):
        mock_create.return_value = self._mock_stripe_session()
        response = self.client.post("/api/payments/create-checkout-session/", {
            "session_key": self.session_key,
            "customer_name": "Ana Torres",
            "customer_email": "ana@example.com",
            "frontend_total": "1.00",  # malicious input ignored
        }, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        order = Order.objects.first()
        self.assertEqual(order.total, Decimal("7499.00"))

    def test_checkout_empty_cart_returns_400(self):
        response = self.client.post("/api/payments/create-checkout-session/", {
            "session_key": "empty-session-xyz",
            "customer_name": "Test",
            "customer_email": "test@example.com",
        }, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_checkout_insufficient_stock_returns_400(self):
        self.product.inventory = 0
        self.product.save()
        response = self.client.post("/api/payments/create-checkout-session/", {
            "session_key": self.session_key,
            "customer_name": "Test",
            "customer_email": "test@example.com",
        }, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("errors", response.json())

    @patch("stripe.checkout.Session.create")
    def test_checkout_applies_coupon_discount_from_db(self, mock_create):
        mock_create.return_value = self._mock_stripe_session()
        coupon = Coupon.objects.create(code="FASE1TEST", discount_percent=10, is_active=True)
        response = self.client.post("/api/payments/create-checkout-session/", {
            "session_key": self.session_key,
            "customer_name": "Test",
            "customer_email": "test@example.com",
            "coupon_code": "FASE1TEST",
        }, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        order = Order.objects.first()
        expected_total = (Decimal("7499.00") * Decimal("0.90")).quantize(Decimal("0.01"))
        self.assertEqual(order.total, expected_total)
        self.assertEqual(order.coupon_code, "FASE1TEST")

    @patch("stripe.checkout.Session.create")
    def test_checkout_invalid_coupon_returns_400(self, mock_create):
        mock_create.return_value = self._mock_stripe_session()
        response = self.client.post("/api/payments/create-checkout-session/", {
            "session_key": self.session_key,
            "customer_name": "Test",
            "customer_email": "test@example.com",
            "coupon_code": "CUPONFALSO",
        }, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


@override_settings(
    STRIPE_SECRET_KEY='sk_test_fake_key',
    STRIPE_WEBHOOK_SECRET='whsec_fake_secret',
)
class StripeWebhookTest(TestCase):
    """StripeWebhookView: idempotent payment confirmation, inventory decrement, cart cleanup."""

    def setUp(self):
        self.client = APIClient()
        self.product = Product.objects.create(
            name="AirPods Pro Webhook Test",
            slug="airpods-pro-webhook-test",
            price=Decimal("799.00"),
            inventory=10,
        )
        self.session_key = "webhook-test-session-001"
        self.order = Order.objects.create(
            customer_email="webhook@example.com",
            total=Decimal("799.00"),
            cart_session_key=self.session_key,
            stripe_session_id="cs_test_webhook_001",
            status=Order.Status.PENDING_PAYMENT,
        )
        OrderItem.objects.create(
            order=self.order,
            product=self.product,
            quantity=2,
            price=self.product.price,
        )
        CartItem.objects.create(
            session_key=self.session_key,
            product=self.product,
            quantity=2,
        )

    def _post_webhook(self, event_type, session_id="cs_test_webhook_001",
                      payment_intent_id="pi_test_001"):
        event = {
            "type": event_type,
            "data": {
                "object": {
                    "id": session_id,
                    "payment_intent": payment_intent_id,
                }
            },
        }
        with patch("stripe.Webhook.construct_event", return_value=event):
            return self.client.post(
                "/api/payments/webhook/",
                data=b'{}',
                content_type="application/json",
                HTTP_STRIPE_SIGNATURE="t=1,v1=fake",
            )

    def test_webhook_marks_order_paid(self):
        response = self._post_webhook("checkout.session.completed")
        self.assertEqual(response.status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PAID)
        self.assertTrue(self.order.paid)
        self.assertIsNotNone(self.order.paid_at)

    def test_webhook_saves_payment_intent_id(self):
        self._post_webhook("checkout.session.completed", payment_intent_id="pi_test_real_001")
        self.order.refresh_from_db()
        self.assertEqual(self.order.stripe_payment_intent_id, "pi_test_real_001")

    def test_webhook_decrements_inventory(self):
        self._post_webhook("checkout.session.completed")
        self.product.refresh_from_db()
        self.assertEqual(self.product.inventory, 8)  # 10 - 2

    def test_webhook_idempotent_no_double_decrement(self):
        """Duplicate webhook must not decrement inventory twice."""
        self._post_webhook("checkout.session.completed")
        self._post_webhook("checkout.session.completed")
        self.product.refresh_from_db()
        self.assertEqual(self.product.inventory, 8)  # still 8, not 6

    def test_webhook_deletes_cart_after_payment(self):
        self._post_webhook("checkout.session.completed")
        remaining = CartItem.objects.filter(session_key=self.session_key).count()
        self.assertEqual(remaining, 0)

    def test_webhook_expired_marks_order_expired(self):
        response = self._post_webhook("checkout.session.expired")
        self.assertEqual(response.status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.EXPIRED)

    def test_webhook_unknown_session_id_is_safe(self):
        """Webhook for unknown session ID must return 200 (don't expose 404 to Stripe)."""
        response = self._post_webhook("checkout.session.completed", session_id="cs_nonexistent")
        self.assertEqual(response.status_code, 200)


@override_settings(
    STRIPE_SECRET_KEY='sk_test_fake_key',
    STRIPE_WEBHOOK_SECRET='whsec_fake_secret',
)
class PaymentStatusViewTest(TestCase):
    """GET /api/payments/status/?session_id= returns accurate order state."""

    def setUp(self):
        self.client = APIClient()
        self.order = Order.objects.create(
            customer_email="status@example.com",
            total=Decimal("1299.00"),
            stripe_session_id="cs_test_status_001",
            status=Order.Status.PENDING_PAYMENT,
        )

    def test_status_pending(self):
        response = self.client.get("/api/payments/status/?session_id=cs_test_status_001")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "pending_payment")
        self.assertFalse(data["paid"])

    def test_status_paid(self):
        self.order.status = Order.Status.PAID
        self.order.paid = True
        self.order.save()
        response = self.client.get("/api/payments/status/?session_id=cs_test_status_001")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "paid")
        self.assertTrue(data["paid"])

    def test_status_unknown_session_returns_404(self):
        response = self.client.get("/api/payments/status/?session_id=cs_does_not_exist")
        self.assertEqual(response.status_code, 404)

    def test_status_missing_param_returns_400(self):
        response = self.client.get("/api/payments/status/")
        self.assertEqual(response.status_code, 400)

    def test_status_returns_total(self):
        response = self.client.get("/api/payments/status/?session_id=cs_test_status_001")
        self.assertEqual(response.json()["total"], "1299.00")

    def test_authenticated_user_cannot_access_other_user_order(self):
        """Authenticated user must get 403 when trying to read another user's order status."""
        requester = User.objects.create_user(username="requester_x", password="pass")
        owner = User.objects.create_user(username="owner_x", password="pass")
        owner_order = Order.objects.create(
            user=owner,
            customer_email="owner@example.com",
            total=Decimal("999.00"),
            stripe_session_id="cs_cross_user_001",
            status=Order.Status.PAID,
        )
        self.client.force_authenticate(user=requester)
        response = self.client.get(
            f"/api/payments/status/?session_id={owner_order.stripe_session_id}"
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_status_does_not_expose_internal_fields(self):
        """Response must not include payment_error, cart_session_key, or stripe_payment_intent_id."""
        self.order.payment_error = "Card declined internal log"
        self.order.cart_session_key = "sensitive-session-key"
        self.order.stripe_payment_intent_id = "pi_secret_intent"
        self.order.save()
        response = self.client.get("/api/payments/status/?session_id=cs_test_status_001")
        body = response.json()
        self.assertNotIn("payment_error", body)
        self.assertNotIn("cart_session_key", body)
        self.assertNotIn("stripe_payment_intent_id", body)
        self.assertNotIn("customer_email", body)


@override_settings(
    STRIPE_SECRET_KEY='sk_test_fake_key',
    STRIPE_WEBHOOK_SECRET='whsec_fake_secret',
    STRIPE_DOMAIN='http://localhost:3000',
)
class CheckoutStripeErrorTest(TestCase):
    """Stripe call failure: cart preserved, order marked FAILED, 502 returned."""

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.product = Product.objects.create(
            name="iPhone Stripe Error Test",
            slug="iphone-stripe-error-test",
            price=Decimal("4999.00"),
            inventory=5,
        )
        self.session_key = "stripe-error-session-001"
        CartItem.objects.create(
            session_key=self.session_key,
            product=self.product,
            quantity=1,
        )

    @patch("stripe.checkout.Session.create", side_effect=stripe_lib.StripeError("Connection error"))
    def test_stripe_failure_returns_502(self, _mock):
        response = self.client.post("/api/payments/create-checkout-session/", {
            "session_key": self.session_key,
            "customer_name": "Test",
            "customer_email": "test@example.com",
        }, format="json")
        self.assertEqual(response.status_code, status.HTTP_502_BAD_GATEWAY)

    @patch("stripe.checkout.Session.create", side_effect=stripe_lib.StripeError("Connection error"))
    def test_stripe_failure_preserves_cart(self, _mock):
        self.client.post("/api/payments/create-checkout-session/", {
            "session_key": self.session_key,
            "customer_name": "Test",
            "customer_email": "test@example.com",
        }, format="json")
        cart_count = CartItem.objects.filter(session_key=self.session_key).count()
        self.assertEqual(cart_count, 1)

    @patch("stripe.checkout.Session.create", side_effect=stripe_lib.StripeError("Connection error"))
    def test_stripe_failure_marks_order_failed(self, _mock):
        self.client.post("/api/payments/create-checkout-session/", {
            "session_key": self.session_key,
            "customer_name": "Test",
            "customer_email": "test@example.com",
        }, format="json")
        order = Order.objects.first()
        self.assertEqual(order.status, Order.Status.FAILED)
        self.assertIn("Connection error", order.payment_error)


class OrderViewSetAccessTest(TestCase):
    """OrderViewSet: read-only, scoped to authenticated user's own orders."""

    def setUp(self):
        self.client = APIClient()
        self.user1 = User.objects.create_user(username="orders_user1", password="pass")
        self.user2 = User.objects.create_user(username="orders_user2", password="pass")
        self.order_u1 = Order.objects.create(
            user=self.user1,
            customer_email="u1@example.com",
            total=Decimal("500.00"),
            status=Order.Status.PAID,
        )
        self.order_u2 = Order.objects.create(
            user=self.user2,
            customer_email="u2@example.com",
            total=Decimal("300.00"),
            status=Order.Status.PAID,
        )

    def test_anonymous_gets_401(self):
        response = self.client.get("/api/orders/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_anonymous_detail_gets_401(self):
        response = self.client.get(f"/api/orders/{self.order_u1.id}/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_user_can_see_own_order(self):
        self.client.force_authenticate(user=self.user1)
        response = self.client.get(f"/api/orders/{self.order_u1.id}/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_user_cannot_see_other_user_order(self):
        self.client.force_authenticate(user=self.user1)
        response = self.client.get(f"/api/orders/{self.order_u2.id}/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_delete_returns_405(self):
        self.client.force_authenticate(user=self.user1)
        response = self.client.delete(f"/api/orders/{self.order_u1.id}/")
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_patch_returns_405(self):
        self.client.force_authenticate(user=self.user1)
        response = self.client.patch(
            f"/api/orders/{self.order_u1.id}/", {}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_put_returns_405(self):
        self.client.force_authenticate(user=self.user1)
        response = self.client.put(
            f"/api/orders/{self.order_u1.id}/", {}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)


# ---------------------------------------------------------------------------
# Phase 2.0 tests
# ---------------------------------------------------------------------------

class RegisterSecurityTest(TestCase):
    """RegisterView: email normalization, password strength, duplicate detection."""

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.valid_payload = {
            'username': 'testuser_reg',
            'email': 'testuser@example.com',
            'password': 'StrongPass123!',
            'password_confirm': 'StrongPass123!',
        }

    def test_register_success(self):
        response = self.client.post('/api/auth/register/', self.valid_payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(User.objects.filter(username='testuser_reg').exists())

    def test_register_normalizes_email_to_lowercase(self):
        payload = {**self.valid_payload, 'email': 'TestUser@EXAMPLE.COM'}
        self.client.post('/api/auth/register/', payload, format='json')
        user = User.objects.get(username='testuser_reg')
        self.assertEqual(user.email, 'testuser@example.com')

    def test_register_password_too_short_rejected(self):
        payload = {**self.valid_payload, 'password': 'Abc1!', 'password_confirm': 'Abc1!'}
        response = self.client.post('/api/auth/register/', payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('password', response.json())

    def test_register_common_password_rejected(self):
        payload = {**self.valid_payload, 'password': 'password123', 'password_confirm': 'password123'}
        response = self.client.post('/api/auth/register/', payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('password', response.json())

    def test_register_numeric_only_password_rejected(self):
        payload = {**self.valid_payload, 'password': '12345678', 'password_confirm': '12345678'}
        response = self.client.post('/api/auth/register/', payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('password', response.json())

    def test_register_duplicate_email_case_insensitive_rejected(self):
        self.client.post('/api/auth/register/', self.valid_payload, format='json')
        payload2 = {
            'username': 'otheruser',
            'email': 'TESTUSER@EXAMPLE.COM',
            'password': 'StrongPass123!',
            'password_confirm': 'StrongPass123!',
        }
        response = self.client.post('/api/auth/register/', payload2, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('email', response.json())

    def test_login_correct_credentials_sets_cookies(self):
        """Phase 2.1: login no longer returns tokens in body — they are in HttpOnly cookies."""
        User.objects.create_user(username='loginuser', password='StrongPass123!')
        response = self.client.post('/api/auth/login/', {
            'username': 'loginuser',
            'password': 'StrongPass123!',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        # Tokens NOT in body
        self.assertNotIn('access', data)
        self.assertNotIn('refresh', data)
        # Tokens in cookies
        self.assertIn('blackdog_access', response.cookies)
        self.assertIn('blackdog_refresh', response.cookies)

    def test_login_wrong_password_returns_401(self):
        User.objects.create_user(username='loginuser2', password='StrongPass123!')
        response = self.client.post('/api/auth/login/', {
            'username': 'loginuser2',
            'password': 'wrongpassword',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class CartPatchTest(TestCase):
    """CartViewSet PATCH: validates quantity, enforces stock, ignores non-quantity fields."""

    def setUp(self):
        self.client = APIClient()
        self.product = Product.objects.create(
            name='iPhone 16 Patch Test',
            slug='iphone-16-patch-test',
            price=Decimal('5999.00'),
            inventory=5,
        )
        self.session_key = 'patch-test-session-001'
        self.item = CartItem.objects.create(
            session_key=self.session_key,
            product=self.product,
            quantity=1,
        )
        self.url = f'/api/cart/{self.item.id}/?session_key={self.session_key}'

    def test_patch_valid_quantity(self):
        response = self.client.patch(self.url, {'quantity': 3}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.item.refresh_from_db()
        self.assertEqual(self.item.quantity, 3)

    def test_patch_quantity_zero_rejected(self):
        response = self.client.patch(self.url, {'quantity': 0}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('detail', response.json())

    def test_patch_quantity_negative_rejected(self):
        response = self.client.patch(self.url, {'quantity': -1}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_patch_quantity_exceeds_stock_rejected(self):
        response = self.client.patch(self.url, {'quantity': 99}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('insuficiente', response.json()['detail'])

    def test_patch_ignores_session_key_in_body(self):
        """session_key in body must be ignored; only quantity changes."""
        other_product = Product.objects.create(
            name='Other Product',
            slug='other-product-patch',
            price=Decimal('100.00'),
            inventory=10,
        )
        response = self.client.patch(self.url, {
            'quantity': 2,
            'session_key': 'evil-session-key',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.item.refresh_from_db()
        self.assertEqual(self.item.session_key, self.session_key)
        self.assertEqual(self.item.quantity, 2)

    def test_patch_ignores_product_in_body(self):
        """product in body must be ignored; only quantity changes."""
        other_product = Product.objects.create(
            name='Other Product Patch',
            slug='other-product-patch-2',
            price=Decimal('100.00'),
            inventory=10,
        )
        response = self.client.patch(self.url, {
            'quantity': 2,
            'product': other_product.id,
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.item.refresh_from_db()
        self.assertEqual(self.item.product_id, self.product.id)
        self.assertEqual(self.item.quantity, 2)

    def test_patch_wrong_session_key_returns_404(self):
        """PATCH with a session_key that doesn't own the item must return 404."""
        url_bad_session = f'/api/cart/{self.item.id}/?session_key=other-session-xyz'
        response = self.client.patch(url_bad_session, {'quantity': 2}, format='json')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class ReviewSecurityTest(TestCase):
    """ReviewViewSet: requires auth for POST, validates rating range and comment length."""

    def setUp(self):
        self.client = APIClient()
        self.category = Category.objects.create(name='Review Cat', slug='review-cat-test')
        self.product = Product.objects.create(
            name='Review Product Test',
            slug='review-product-test',
            price=Decimal('999.00'),
            inventory=5,
            category=self.category,
        )
        self.user = User.objects.create_user(username='review_user', password='pass')

    def _create_review_payload(self, rating=5, comment='Excelente producto'):
        return {
            'product': self.product.id,
            'author_name': 'Test Author',
            'rating': rating,
            'comment': comment,
        }

    def test_review_anonymous_create_rejected(self):
        response = self.client.post('/api/reviews/', self._create_review_payload(), format='json')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_review_authenticated_create_accepted(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.post('/api/reviews/', self._create_review_payload(), format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        review = Review.objects.get(product=self.product)
        self.assertEqual(review.user, self.user)

    def test_review_rating_0_rejected(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.post('/api/reviews/', self._create_review_payload(rating=0), format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_review_rating_6_rejected(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.post('/api/reviews/', self._create_review_payload(rating=6), format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_review_comment_too_long_rejected(self):
        self.client.force_authenticate(user=self.user)
        long_comment = 'a' * 2001
        response = self.client.post('/api/reviews/', self._create_review_payload(comment=long_comment), format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_review_list_public(self):
        """GET /api/reviews/?product=N must be accessible without auth."""
        Review.objects.create(product=self.product, user=self.user, rating=4, comment='Bueno')
        response = self.client.get(f'/api/reviews/?product={self.product.id}')
        self.assertEqual(response.status_code, status.HTTP_200_OK)


class ThrottleConfigTest(TestCase):
    """Verify throttle classes are wired to views — DRF respects allow_request=False → 429."""

    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    def test_login_throttle_is_active(self):
        """LoginView returns 429 when LoginThrottle blocks the request."""
        from store.throttles import LoginThrottle
        User.objects.create_user(username='throttle_login_user', password='ValidPass123!')
        client = APIClient()
        with patch.object(LoginThrottle, 'allow_request', return_value=False), \
             patch.object(LoginThrottle, 'wait', return_value=60.0):
            response = client.post('/api/auth/login/', {
                'username': 'throttle_login_user', 'password': 'ValidPass123!',
            }, format='json')
        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    def test_register_throttle_is_active(self):
        """RegisterView returns 429 when RegisterThrottle blocks the request."""
        from store.throttles import RegisterThrottle
        client = APIClient()
        with patch.object(RegisterThrottle, 'allow_request', return_value=False), \
             patch.object(RegisterThrottle, 'wait', return_value=60.0):
            response = client.post('/api/auth/register/', {
                'username': 'throttletest', 'email': 'throttle@test.com',
                'password': 'ValidPass123!', 'password_confirm': 'ValidPass123!',
            }, format='json')
        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)


# ---------------------------------------------------------------------------
# Phase 2.1 — Cookie JWT authentication
# ---------------------------------------------------------------------------

class CookieLoginTest(TestCase):
    """LoginView: tokens go to HttpOnly cookies, NOT the response body."""

    def setUp(self):
        cache.clear()  # prevent throttle counter accumulation across test methods
        self.client = APIClient()
        self.user = User.objects.create_user(username='cookielogin', password='ValidPass123!')
        self.url = '/api/auth/login/'
        self.creds = {'username': 'cookielogin', 'password': 'ValidPass123!'}

    def test_login_sets_access_cookie(self):
        response = self.client.post(self.url, self.creds, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('blackdog_access', response.cookies)

    def test_login_sets_refresh_cookie(self):
        response = self.client.post(self.url, self.creds, format='json')
        self.assertIn('blackdog_refresh', response.cookies)

    def test_login_access_cookie_is_httponly(self):
        response = self.client.post(self.url, self.creds, format='json')
        self.assertTrue(response.cookies['blackdog_access']['httponly'])

    def test_login_refresh_cookie_is_httponly(self):
        response = self.client.post(self.url, self.creds, format='json')
        self.assertTrue(response.cookies['blackdog_refresh']['httponly'])

    def test_login_does_not_expose_tokens_in_body(self):
        response = self.client.post(self.url, self.creds, format='json')
        data = response.json()
        self.assertNotIn('access', data)
        self.assertNotIn('refresh', data)

    def test_login_returns_user_object(self):
        response = self.client.post(self.url, self.creds, format='json')
        data = response.json()
        self.assertIn('user', data)
        self.assertEqual(data['user']['username'], 'cookielogin')

    def test_login_returns_detail_message(self):
        response = self.client.post(self.url, self.creds, format='json')
        self.assertIn('detail', response.json())

    def test_login_access_cookie_max_age(self):
        """Access cookie max-age must match ACCESS_TOKEN_LIFETIME (30 min = 1800 s)."""
        response = self.client.post(self.url, self.creds, format='json')
        from django.conf import settings as django_settings
        expected = int(django_settings.SIMPLE_JWT['ACCESS_TOKEN_LIFETIME'].total_seconds())
        self.assertEqual(response.cookies['blackdog_access']['max-age'], expected)

    def test_login_refresh_cookie_max_age(self):
        """Refresh cookie max-age must match REFRESH_TOKEN_LIFETIME (7 days = 604800 s)."""
        response = self.client.post(self.url, self.creds, format='json')
        from django.conf import settings as django_settings
        expected = int(django_settings.SIMPLE_JWT['REFRESH_TOKEN_LIFETIME'].total_seconds())
        self.assertEqual(response.cookies['blackdog_refresh']['max-age'], expected)

    def test_login_wrong_password_still_returns_401(self):
        response = self.client.post(self.url, {'username': 'cookielogin', 'password': 'wrong'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class CookieJWTAuthTest(TestCase):
    """CookieJWTAuthentication: cookie grants access; Bearer header does NOT."""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username='cookieauth', password='ValidPass123!')
        cache.clear()

    def _get_access_token(self):
        from rest_framework_simplejwt.tokens import RefreshToken
        return str(RefreshToken.for_user(self.user).access_token)

    def test_me_accessible_with_access_cookie(self):
        self.client.cookies['blackdog_access'] = self._get_access_token()
        response = self.client.get('/api/auth/me/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()['username'], 'cookieauth')

    def test_me_returns_401_without_cookie(self):
        response = self.client.get('/api/auth/me/')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_bearer_token_header_not_accepted(self):
        """Authorization: Bearer is no longer a valid auth mechanism."""
        token = self._get_access_token()
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        response = self.client.get('/api/auth/me/')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_orders_returns_401_without_cookie(self):
        response = self.client.get('/api/orders/')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_orders_accessible_with_access_cookie(self):
        self.client.cookies['blackdog_access'] = self._get_access_token()
        response = self.client.get('/api/orders/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)


class CookieRefreshTest(TestCase):
    """RefreshView: reads refresh cookie, issues new access cookie."""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username='refreshuser', password='ValidPass123!')

    def _get_refresh_token(self):
        from rest_framework_simplejwt.tokens import RefreshToken
        return str(RefreshToken.for_user(self.user))

    def test_refresh_sets_new_access_cookie(self):
        self.client.cookies['blackdog_refresh'] = self._get_refresh_token()
        response = self.client.post('/api/auth/refresh/', format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('blackdog_access', response.cookies)

    def test_refresh_rotates_refresh_cookie(self):
        """With ROTATE_REFRESH_TOKENS=True, refresh also issues a new refresh cookie."""
        self.client.cookies['blackdog_refresh'] = self._get_refresh_token()
        response = self.client.post('/api/auth/refresh/', format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('blackdog_refresh', response.cookies)

    def test_refresh_without_cookie_returns_401(self):
        response = self.client.post('/api/auth/refresh/', format='json')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_refresh_with_invalid_token_returns_401(self):
        self.client.cookies['blackdog_refresh'] = 'not.a.valid.token'
        response = self.client.post('/api/auth/refresh/', format='json')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_refresh_does_not_expose_token_in_body(self):
        self.client.cookies['blackdog_refresh'] = self._get_refresh_token()
        response = self.client.post('/api/auth/refresh/', format='json')
        data = response.json()
        self.assertNotIn('access', data)
        self.assertNotIn('refresh', data)


class CookieLogoutTest(TestCase):
    """LogoutView: clears both JWT cookies."""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username='logoutuser', password='ValidPass123!')

    def test_logout_returns_200(self):
        response = self.client.post('/api/auth/logout/', format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_logout_clears_access_cookie(self):
        from rest_framework_simplejwt.tokens import RefreshToken
        self.client.cookies['blackdog_access'] = str(RefreshToken.for_user(self.user).access_token)
        response = self.client.post('/api/auth/logout/', format='json')
        access_cookie = response.cookies.get('blackdog_access')
        self.assertIsNotNone(access_cookie)
        self.assertEqual(access_cookie['max-age'], 0)

    def test_logout_clears_refresh_cookie(self):
        from rest_framework_simplejwt.tokens import RefreshToken
        self.client.cookies['blackdog_refresh'] = str(RefreshToken.for_user(self.user))
        response = self.client.post('/api/auth/logout/', format='json')
        refresh_cookie = response.cookies.get('blackdog_refresh')
        self.assertIsNotNone(refresh_cookie)
        self.assertEqual(refresh_cookie['max-age'], 0)


class CsrfEndpointTest(TestCase):
    """GET /api/auth/csrf/ must set the csrftoken cookie (not HttpOnly)."""

    def setUp(self):
        self.client = APIClient()

    def test_csrf_endpoint_sets_csrftoken_cookie(self):
        response = self.client.get('/api/auth/csrf/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('csrftoken', response.cookies)

    def test_csrf_cookie_is_not_httponly(self):
        response = self.client.get('/api/auth/csrf/')
        self.assertFalse(response.cookies['csrftoken']['httponly'])


class CookieJWTCSRFTest(TestCase):
    """CookieJWTAuthentication.enforce_csrf: POST without csrftoken must be rejected."""

    def _get_access_token(self, user):
        from rest_framework_simplejwt.tokens import RefreshToken
        return str(RefreshToken.for_user(user).access_token)

    def test_post_with_cookie_but_no_csrf_rejected(self):
        """RequestFactory bypasses test-client CSRF skip, so our enforce_csrf fires."""
        from django.test import RequestFactory
        from rest_framework.request import Request as DRFRequest
        from rest_framework.exceptions import PermissionDenied
        from store.authentication import CookieJWTAuthentication

        user = User.objects.create_user(username='csrfcheck', password='ValidPass123!')
        access = self._get_access_token(user)

        raw = RequestFactory().post('/api/auth/logout/', content_type='application/json', data='{}')
        raw.COOKIES['blackdog_access'] = access
        # No csrftoken cookie — CSRF must fail

        with self.assertRaises(PermissionDenied):
            CookieJWTAuthentication().authenticate(DRFRequest(raw))

    def test_get_with_cookie_skips_csrf(self):
        """Safe methods (GET) never trigger CSRF check."""
        from django.test import RequestFactory
        from rest_framework.request import Request as DRFRequest
        from store.authentication import CookieJWTAuthentication

        user = User.objects.create_user(username='csrfget', password='ValidPass123!')
        access = self._get_access_token(user)

        raw = RequestFactory().get('/api/auth/me/')
        raw.COOKIES['blackdog_access'] = access
        # No csrftoken cookie — should not matter for GET

        result = CookieJWTAuthentication().authenticate(DRFRequest(raw))
        self.assertIsNotNone(result)
        self.assertEqual(result[0], user)


# ---------------------------------------------------------------------------
# Phase 2.2 — Logout CSRF enforcement
# ---------------------------------------------------------------------------

class LogoutCSRFTest(TestCase):
    """LogoutView: CSRF must be enforced when auth cookies are present."""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username='logoutcsrf', password='ValidPass123!')

    def _get_tokens(self):
        from rest_framework_simplejwt.tokens import RefreshToken as RT
        rt = RT.for_user(self.user)
        return str(rt.access_token), str(rt)

    def _post_logout_no_csrf_bypass(self, cookies):
        """POST to LogoutView via django.test.RequestFactory.
        Django's RequestFactory does NOT set _dont_enforce_csrf_checks, so our
        manual enforce_csrf() call inside LogoutView.post() runs for real."""
        from django.test import RequestFactory
        from store.auth_views import LogoutView
        req = RequestFactory().post('/api/auth/logout/', content_type='application/json', data='{}')
        for k, v in cookies.items():
            req.COOKIES[k] = v
        return LogoutView.as_view()(req)

    def test_logout_without_auth_cookies_returns_200(self):
        """No auth cookies present — CSRF is not required, logout always succeeds."""
        response = self.client.post('/api/auth/logout/', format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_logout_with_access_cookie_no_csrf_returns_403(self):
        """blackdog_access present but no X-CSRFToken → 403."""
        access, _ = self._get_tokens()
        response = self._post_logout_no_csrf_bypass({'blackdog_access': access})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_logout_with_refresh_cookie_no_csrf_returns_403(self):
        """blackdog_refresh present but no X-CSRFToken → 403."""
        _, refresh = self._get_tokens()
        response = self._post_logout_no_csrf_bypass({'blackdog_refresh': refresh})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_logout_with_auth_cookies_clears_both_cookies(self):
        """Auth cookies + CSRF valid (APIClient bypasses) → 200 + cookies cleared."""
        access, refresh = self._get_tokens()
        self.client.cookies['blackdog_access'] = access
        self.client.cookies['blackdog_refresh'] = refresh
        response = self.client.post('/api/auth/logout/', format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.cookies['blackdog_access']['max-age'], 0)
        self.assertEqual(response.cookies['blackdog_refresh']['max-age'], 0)

    def test_logout_with_expired_access_and_refresh_succeeds(self):
        """Access token expired (or invalid string) but refresh present → still 200.
        LogoutView must not reject the request just because the access token is stale."""
        _, refresh = self._get_tokens()
        self.client.cookies['blackdog_access'] = 'expired.access.token'
        self.client.cookies['blackdog_refresh'] = refresh
        response = self.client.post('/api/auth/logout/', format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# Phase 2.2 — Token blacklist
# ---------------------------------------------------------------------------

class TokenBlacklistTest(TestCase):
    """BLACKLIST_AFTER_ROTATION=True + blacklist on logout."""

    def setUp(self):
        self.client = APIClient()
        cache.clear()

    def _create_user(self, username):
        return User.objects.create_user(username=username, password='ValidPass123!')

    def _get_refresh_token(self, user):
        from rest_framework_simplejwt.tokens import RefreshToken as RT
        return str(RT.for_user(user))

    def test_rotation_blacklists_old_refresh_token(self):
        """After a successful refresh, the old token is blacklisted and returns 401."""
        user = self._create_user('bl_rotate')
        old_refresh = self._get_refresh_token(user)

        self.client.cookies['blackdog_refresh'] = old_refresh
        r1 = self.client.post('/api/auth/refresh/', format='json')
        self.assertEqual(r1.status_code, status.HTTP_200_OK)

        # Old token must be blacklisted now
        self.client.cookies['blackdog_refresh'] = old_refresh
        r2 = self.client.post('/api/auth/refresh/', format='json')
        self.assertEqual(r2.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_logout_blacklists_refresh_token(self):
        """After logout, the refresh token is blacklisted and cannot refresh a new session."""
        user = self._create_user('bl_logout')
        refresh = self._get_refresh_token(user)

        self.client.cookies['blackdog_refresh'] = refresh
        logout_resp = self.client.post('/api/auth/logout/', format='json')
        self.assertEqual(logout_resp.status_code, status.HTTP_200_OK)

        # The blacklisted refresh must now be rejected
        self.client.cookies['blackdog_refresh'] = refresh
        refresh_resp = self.client.post('/api/auth/refresh/', format='json')
        self.assertEqual(refresh_resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_refresh_after_logout_returns_401(self):
        """Attempting /auth/refresh/ after logout (cookie cleared) returns 401."""
        user = self._create_user('bl_postlogout')
        refresh = self._get_refresh_token(user)

        self.client.cookies['blackdog_refresh'] = refresh
        self.client.post('/api/auth/logout/', format='json')

        # No refresh cookie (cleared by logout) — RefreshView should return 401
        response = self.client.post('/api/auth/refresh/', format='json')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_logout_with_invalid_refresh_still_clears_cookies(self):
        """Invalid or expired refresh token does not break logout — cookies are still cleared."""
        self.client.cookies['blackdog_refresh'] = 'not.a.real.token'
        response = self.client.post('/api/auth/logout/', format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.cookies['blackdog_refresh']['max-age'], 0)

    def test_logout_with_already_blacklisted_token_still_succeeds(self):
        """Blacklisting an already-blacklisted token is silenced — logout still returns 200."""
        from rest_framework_simplejwt.tokens import RefreshToken as RT
        user = self._create_user('bl_double')
        refresh = self._get_refresh_token(user)

        # Blacklist manually first
        RT(refresh).blacklist()

        # Second logout attempt with the same (now-blacklisted) token must not raise
        self.client.cookies['blackdog_refresh'] = refresh
        response = self.client.post('/api/auth/logout/', format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.cookies['blackdog_access']['max-age'], 0)


# ---------------------------------------------------------------------------
# Phase 2.3 tests
# ---------------------------------------------------------------------------

class AccountTokenModelTest(TestCase):
    """AccountToken: hash storage, consume validation."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='tokenmodel', password='ValidPass123!', email='tokenmodel@example.com'
        )

    def test_token_hash_is_sha256_not_plain(self):
        """The raw token must NOT be stored in token_hash — only its SHA-256 digest."""
        import hashlib
        raw, obj = AccountToken.make(self.user, AccountToken.PURPOSE_EMAIL_VERIFICATION, ttl_hours=24)
        self.assertNotEqual(obj.token_hash, raw)
        self.assertEqual(obj.token_hash, hashlib.sha256(raw.encode()).hexdigest())

    def test_consume_valid_token_marks_used(self):
        raw, obj = AccountToken.make(self.user, AccountToken.PURPOSE_EMAIL_VERIFICATION, ttl_hours=24)
        result = AccountToken.consume(raw, AccountToken.PURPOSE_EMAIL_VERIFICATION)
        self.assertEqual(result.pk, obj.pk)
        self.assertIsNotNone(result.used_at)

    def test_consume_expired_token_raises(self):
        raw, obj = AccountToken.make(self.user, AccountToken.PURPOSE_EMAIL_VERIFICATION, ttl_hours=24)
        obj.expires_at = timezone.now() - timedelta(hours=1)
        obj.save(update_fields=['expires_at'])
        with self.assertRaises(ValueError) as ctx:
            AccountToken.consume(raw, AccountToken.PURPOSE_EMAIL_VERIFICATION)
        self.assertIn('expirado', str(ctx.exception))

    def test_consume_used_token_raises(self):
        raw, _ = AccountToken.make(self.user, AccountToken.PURPOSE_EMAIL_VERIFICATION, ttl_hours=24)
        AccountToken.consume(raw, AccountToken.PURPOSE_EMAIL_VERIFICATION)
        with self.assertRaises(ValueError) as ctx:
            AccountToken.consume(raw, AccountToken.PURPOSE_EMAIL_VERIFICATION)
        self.assertIn('utilizado', str(ctx.exception))

    def test_consume_invalid_token_raises(self):
        with self.assertRaises(ValueError) as ctx:
            AccountToken.consume('not-a-real-token', AccountToken.PURPOSE_EMAIL_VERIFICATION)
        self.assertIn('inválido', str(ctx.exception))

    def test_consume_wrong_purpose_raises(self):
        raw, _ = AccountToken.make(self.user, AccountToken.PURPOSE_EMAIL_VERIFICATION, ttl_hours=24)
        with self.assertRaises(ValueError):
            AccountToken.consume(raw, AccountToken.PURPOSE_PASSWORD_RESET)


@override_settings(
    REQUIRE_EMAIL_VERIFICATION=True,
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    FRONTEND_URL='http://testserver',
)
class EmailVerificationFlowTest(TestCase):
    """Email verification: registration creates inactive user, verify endpoint activates it."""

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.payload = {
            'username': 'verifytest',
            'email': 'verify@example.com',
            'password': 'ValidPass123!',
            'password_confirm': 'ValidPass123!',
        }

    def _register(self, payload=None):
        return self.client.post('/api/auth/register/', payload or self.payload, format='json')

    def test_register_creates_inactive_user(self):
        response = self._register()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        user = User.objects.get(username='verifytest')
        self.assertFalse(user.is_active)

    def test_register_response_requires_verification_flag(self):
        response = self._register()
        data = response.json()
        self.assertTrue(data.get('requires_verification'))

    def test_register_creates_account_token(self):
        self._register()
        user = User.objects.get(username='verifytest')
        self.assertTrue(
            AccountToken.objects.filter(user=user, purpose=AccountToken.PURPOSE_EMAIL_VERIFICATION).exists()
        )

    def test_verify_email_activates_user(self):
        """POST /auth/verify-email/ with valid token sets is_active=True."""
        user = User.objects.create_user(
            username='notactive', password='ValidPass123!', email='na@example.com', is_active=False
        )
        raw, _ = AccountToken.make(user, AccountToken.PURPOSE_EMAIL_VERIFICATION, ttl_hours=24)
        response = self.client.post('/api/auth/verify-email/', {'token': raw}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        user.refresh_from_db()
        self.assertTrue(user.is_active)

    def test_verify_email_used_token_returns_400(self):
        user = User.objects.create_user(
            username='usedtoken', password='ValidPass123!', email='used@example.com', is_active=False
        )
        raw, _ = AccountToken.make(user, AccountToken.PURPOSE_EMAIL_VERIFICATION, ttl_hours=24)
        self.client.post('/api/auth/verify-email/', {'token': raw}, format='json')
        response = self.client.post('/api/auth/verify-email/', {'token': raw}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_verify_email_expired_token_returns_400(self):
        user = User.objects.create_user(
            username='expiredtk', password='ValidPass123!', email='expired@example.com', is_active=False
        )
        raw, obj = AccountToken.make(user, AccountToken.PURPOSE_EMAIL_VERIFICATION, ttl_hours=24)
        obj.expires_at = timezone.now() - timedelta(hours=1)
        obj.save(update_fields=['expires_at'])
        response = self.client.post('/api/auth/verify-email/', {'token': raw}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_resend_verification_returns_200_for_unknown_email(self):
        """Anti-enumeration: unknown email returns 200 with generic message."""
        response = self.client.post(
            '/api/auth/resend-verification/', {'email': 'nobody@example.com'}, format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_login_inactive_user_returns_401(self):
        """Login is blocked when is_active=False (email not verified)."""
        User.objects.create_user(
            username='blockeduser', password='ValidPass123!', email='blocked@example.com', is_active=False
        )
        response = self.client.post('/api/auth/login/', {
            'username': 'blockeduser',
            'password': 'ValidPass123!',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    FRONTEND_URL='http://testserver',
)
class PasswordResetTest(TestCase):
    """Password reset: request creates token, confirm changes password, anti-enumeration."""

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.user = User.objects.create_user(
            username='resetuser', password='OldPass123!', email='reset@example.com'
        )

    def test_reset_request_generic_response_unknown_email(self):
        """Anti-enumeration: unknown email returns 200 with generic message."""
        response = self.client.post(
            '/api/auth/password-reset/request/', {'email': 'nobody@example.com'}, format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_reset_request_creates_token_for_active_user(self):
        self.client.post('/api/auth/password-reset/request/', {'email': 'reset@example.com'}, format='json')
        self.assertTrue(
            AccountToken.objects.filter(user=self.user, purpose=AccountToken.PURPOSE_PASSWORD_RESET).exists()
        )

    def test_reset_token_hash_not_plain(self):
        """DB stores only the hash, not the raw token."""
        import hashlib
        self.client.post('/api/auth/password-reset/request/', {'email': 'reset@example.com'}, format='json')
        obj = AccountToken.objects.get(user=self.user, purpose=AccountToken.PURPOSE_PASSWORD_RESET)
        # The token_hash must look like a sha256 hex digest (64 chars), not a URL-safe token
        self.assertEqual(len(obj.token_hash), 64)
        self.assertNotIn('-', obj.token_hash)  # sha256 hex has no dashes

    def test_reset_confirm_changes_password(self):
        raw, _ = AccountToken.make(self.user, AccountToken.PURPOSE_PASSWORD_RESET, ttl_hours=1)
        response = self.client.post('/api/auth/password-reset/confirm/', {
            'token': raw,
            'new_password': 'NewSecure456!',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('NewSecure456!'))

    def test_reset_confirm_used_token_fails(self):
        raw, _ = AccountToken.make(self.user, AccountToken.PURPOSE_PASSWORD_RESET, ttl_hours=1)
        self.client.post('/api/auth/password-reset/confirm/', {
            'token': raw, 'new_password': 'NewSecure456!'
        }, format='json')
        response = self.client.post('/api/auth/password-reset/confirm/', {
            'token': raw, 'new_password': 'AnotherPass789!'
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_reset_confirm_expired_token_fails(self):
        raw, obj = AccountToken.make(self.user, AccountToken.PURPOSE_PASSWORD_RESET, ttl_hours=1)
        obj.expires_at = timezone.now() - timedelta(minutes=5)
        obj.save(update_fields=['expires_at'])
        response = self.client.post('/api/auth/password-reset/confirm/', {
            'token': raw, 'new_password': 'NewSecure456!'
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_reset_confirm_weak_password_rejected(self):
        raw, _ = AccountToken.make(self.user, AccountToken.PURPOSE_PASSWORD_RESET, ttl_hours=1)
        response = self.client.post('/api/auth/password-reset/confirm/', {
            'token': raw, 'new_password': '1234'
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_login_with_old_password_after_reset_fails(self):
        raw, _ = AccountToken.make(self.user, AccountToken.PURPOSE_PASSWORD_RESET, ttl_hours=1)
        self.client.post('/api/auth/password-reset/confirm/', {
            'token': raw, 'new_password': 'NewSecure456!'
        }, format='json')
        response = self.client.post('/api/auth/login/', {
            'username': 'resetuser', 'password': 'OldPass123!'
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_login_with_new_password_after_reset_succeeds(self):
        raw, _ = AccountToken.make(self.user, AccountToken.PURPOSE_PASSWORD_RESET, ttl_hours=1)
        self.client.post('/api/auth/password-reset/confirm/', {
            'token': raw, 'new_password': 'NewSecure456!'
        }, format='json')
        response = self.client.post('/api/auth/login/', {
            'username': 'resetuser', 'password': 'NewSecure456!'
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('blackdog_access', response.cookies)


class ChangePasswordTest(TestCase):
    """Change password: requires auth + CSRF, clears cookies, old password rejected after change."""

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.user = User.objects.create_user(
            username='changepass', password='OldPass123!', email='change@example.com'
        )

    def _get_tokens(self):
        from rest_framework_simplejwt.tokens import RefreshToken as RT
        rt = RT.for_user(self.user)
        return str(rt.access_token), str(rt)

    def test_requires_authentication(self):
        """Unauthenticated request → 401."""
        response = self.client.post('/api/auth/change-password/', {
            'current_password': 'OldPass123!', 'new_password': 'NewPass456!'
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_wrong_current_password_fails(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.post('/api/auth/change-password/', {
            'current_password': 'WrongPassword!', 'new_password': 'NewPass456!'
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('actual', response.json().get('detail', ''))

    def test_weak_new_password_rejected(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.post('/api/auth/change-password/', {
            'current_password': 'OldPass123!', 'new_password': '1234'
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_success_returns_200_and_clears_cookies(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.post('/api/auth/change-password/', {
            'current_password': 'OldPass123!', 'new_password': 'NewPass456!'
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.cookies['blackdog_access']['max-age'], 0)
        self.assertEqual(response.cookies['blackdog_refresh']['max-age'], 0)

    def test_login_with_old_password_after_change_fails(self):
        self.client.force_authenticate(user=self.user)
        self.client.post('/api/auth/change-password/', {
            'current_password': 'OldPass123!', 'new_password': 'NewPass456!'
        }, format='json')
        self.client.logout()
        response = self.client.post('/api/auth/login/', {
            'username': 'changepass', 'password': 'OldPass123!'
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_login_with_new_password_after_change_succeeds(self):
        self.client.force_authenticate(user=self.user)
        self.client.post('/api/auth/change-password/', {
            'current_password': 'OldPass123!', 'new_password': 'NewPass456!'
        }, format='json')
        self.client.logout()
        response = self.client.post('/api/auth/login/', {
            'username': 'changepass', 'password': 'NewPass456!'
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('blackdog_access', response.cookies)

    def test_requires_csrf_when_access_cookie_present(self):
        """CookieJWTAuthentication enforces CSRF when blackdog_access cookie is present."""
        import json
        from django.test import RequestFactory as DjangoRequestFactory
        from store.auth_views import ChangePasswordView
        from rest_framework_simplejwt.tokens import RefreshToken as RT

        access = str(RT.for_user(self.user).access_token)
        req = DjangoRequestFactory().post(
            '/api/auth/change-password/',
            content_type='application/json',
            data=json.dumps({'current_password': 'OldPass123!', 'new_password': 'NewPass456!'}),
        )
        req.COOKIES['blackdog_access'] = access
        # No X-CSRFToken header → enforce_csrf raises PermissionDenied → 403
        response = ChangePasswordView.as_view()(req)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


# ---------------------------------------------------------------------------
# Phase 3.0 tests
# ---------------------------------------------------------------------------

class UserProfileAutoCreateTest(TestCase):
    """UserProfile is auto-created via signal when a User is created."""

    def setUp(self):
        self.client = APIClient()
        cache.clear()

    def test_register_api_creates_customer_profile(self):
        """Registering via API triggers signal → profile with role=customer."""
        payload = {
            'username': 'profile_test_1',
            'email': 'profiletest1@example.com',
            'password': 'StrongPass123!',
            'password_confirm': 'StrongPass123!',
        }
        self.client.post('/api/auth/register/', payload, format='json')
        user = User.objects.get(username='profile_test_1')
        self.assertTrue(hasattr(user, 'profile'))
        self.assertEqual(user.profile.role, UserProfile.ROLE_CUSTOMER)

    def test_create_user_directly_creates_profile(self):
        user = User.objects.create_user(username='direct_create', password='StrongPass123!')
        profile = UserProfile.objects.get(user=user)
        self.assertEqual(profile.role, UserProfile.ROLE_CUSTOMER)

    def test_profile_str(self):
        user = User.objects.create_user(username='str_user', password='StrongPass123!')
        self.assertIn('str_user', str(user.profile))
        self.assertIn('customer', str(user.profile))

    def test_auth_me_returns_role_field(self):
        user = User.objects.create_user(username='me_role_user', password='StrongPass123!')
        self.client.force_authenticate(user=user)
        response = self.client.get('/api/auth/me/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('role', response.json())
        self.assertEqual(response.json()['role'], 'customer')

    def test_auth_me_returns_is_staff_field(self):
        user = User.objects.create_user(username='me_staff_user', password='StrongPass123!', is_staff=True)
        self.client.force_authenticate(user=user)
        response = self.client.get('/api/auth/me/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.json()['is_staff'])

    def test_superuser_role_is_superadmin_in_me_endpoint(self):
        su = User.objects.create_superuser(username='superadmin_me', password='StrongPass123!', email='su@example.com')
        self.client.force_authenticate(user=su)
        response = self.client.get('/api/auth/me/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()['role'], UserProfile.ROLE_SUPERADMIN)

    def test_get_or_create_profile_for_user_without_one(self):
        """Profile is created on demand via get_or_create when missing."""
        user = User.objects.create_user(username='no_profile_user', password='StrongPass123!')
        UserProfile.objects.filter(user=user).delete()
        profile, created = UserProfile.objects.get_or_create(
            user=user, defaults={'role': UserProfile.ROLE_CUSTOMER}
        )
        self.assertTrue(created)
        self.assertEqual(profile.role, UserProfile.ROLE_CUSTOMER)


class AdminUserListTest(TestCase):
    """GET /api/admin/users/ — only admin+ roles can list users."""

    def setUp(self):
        self.client = APIClient()
        cache.clear()
        self.customer = User.objects.create_user(username='adm_customer', password='Pass123!')
        self.admin_user = User.objects.create_user(username='adm_admin', password='Pass123!')
        self.admin_user.profile.role = UserProfile.ROLE_ADMIN
        self.admin_user.profile.save()
        self.superadmin = User.objects.create_user(username='adm_superadmin', password='Pass123!')
        self.superadmin.profile.role = UserProfile.ROLE_SUPERADMIN
        self.superadmin.profile.save()

    def test_anon_gets_401(self):
        response = self.client.get('/api/admin/users/')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_customer_gets_403(self):
        self.client.force_authenticate(user=self.customer)
        response = self.client.get('/api/admin/users/')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_can_list_users(self):
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.get('/api/admin/users/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIn('results', data)
        self.assertIn('count', data)
        self.assertIsInstance(data['results'], list)

    def test_superadmin_can_list_users(self):
        self.client.force_authenticate(user=self.superadmin)
        response = self.client.get('/api/admin/users/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_response_contains_role_field(self):
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.get('/api/admin/users/')
        users_data = response.json()['results']
        self.assertTrue(all('role' in u for u in users_data))

    def test_response_does_not_contain_password(self):
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.get('/api/admin/users/')
        users_data = response.json()['results']
        self.assertTrue(all('password' not in u for u in users_data))

    def test_django_superuser_has_superadmin_role_in_list(self):
        su = User.objects.create_superuser(username='list_su', password='Pass123!', email='list_su@x.com')
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.get('/api/admin/users/')
        su_entry = next((u for u in response.json()['results'] if u['username'] == 'list_su'), None)
        self.assertIsNotNone(su_entry)
        self.assertEqual(su_entry['role'], UserProfile.ROLE_SUPERADMIN)


class AdminUserRoleChangeTest(TestCase):
    """PATCH /api/admin/users/{pk}/role/ — only superadmin can change roles."""

    def setUp(self):
        self.client = APIClient()
        cache.clear()
        self.customer = User.objects.create_user(username='rc_customer', password='Pass123!')
        self.admin_user = User.objects.create_user(username='rc_admin', password='Pass123!')
        self.admin_user.profile.role = UserProfile.ROLE_ADMIN
        self.admin_user.profile.save()
        self.superadmin = User.objects.create_user(username='rc_superadmin', password='Pass123!')
        self.superadmin.profile.role = UserProfile.ROLE_SUPERADMIN
        self.superadmin.profile.save()
        self.target = User.objects.create_user(username='rc_target', password='Pass123!')

    def test_anon_gets_401(self):
        response = self.client.patch(f'/api/admin/users/{self.target.pk}/role/', {'role': 'sales'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_customer_gets_403(self):
        self.client.force_authenticate(user=self.customer)
        response = self.client.patch(f'/api/admin/users/{self.target.pk}/role/', {'role': 'sales'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_role_cannot_change_roles(self):
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.patch(f'/api/admin/users/{self.target.pk}/role/', {'role': 'sales'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_sales_role_cannot_change_roles(self):
        sales_user = User.objects.create_user(username='rc_sales', password='Pass123!')
        sales_user.profile.role = UserProfile.ROLE_SALES
        sales_user.profile.save()
        self.client.force_authenticate(user=sales_user)
        response = self.client.patch(f'/api/admin/users/{self.target.pk}/role/', {'role': 'customer'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_inventory_role_cannot_change_roles(self):
        inv_user = User.objects.create_user(username='rc_inventory', password='Pass123!')
        inv_user.profile.role = UserProfile.ROLE_INVENTORY
        inv_user.profile.save()
        self.client.force_authenticate(user=inv_user)
        response = self.client.patch(f'/api/admin/users/{self.target.pk}/role/', {'role': 'customer'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_technician_role_cannot_change_roles(self):
        tech_user = User.objects.create_user(username='rc_technician', password='Pass123!')
        tech_user.profile.role = UserProfile.ROLE_TECHNICIAN
        tech_user.profile.save()
        self.client.force_authenticate(user=tech_user)
        response = self.client.patch(f'/api/admin/users/{self.target.pk}/role/', {'role': 'customer'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_superadmin_can_assign_superadmin_role_to_others(self):
        self.client.force_authenticate(user=self.superadmin)
        response = self.client.patch(f'/api/admin/users/{self.target.pk}/role/', {'role': 'superadmin'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.target.profile.refresh_from_db()
        self.assertEqual(self.target.profile.role, UserProfile.ROLE_SUPERADMIN)

    def test_superadmin_can_change_role(self):
        self.client.force_authenticate(user=self.superadmin)
        response = self.client.patch(f'/api/admin/users/{self.target.pk}/role/', {'role': 'sales'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.target.profile.refresh_from_db()
        self.assertEqual(self.target.profile.role, UserProfile.ROLE_SALES)

    def test_invalid_role_rejected_400(self):
        self.client.force_authenticate(user=self.superadmin)
        response = self.client.patch(f'/api/admin/users/{self.target.pk}/role/', {'role': 'hacker'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_nonexistent_user_returns_404(self):
        self.client.force_authenticate(user=self.superadmin)
        response = self.client.patch('/api/admin/users/999999/role/', {'role': 'sales'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_cannot_change_own_role(self):
        self.client.force_authenticate(user=self.superadmin)
        response = self.client.patch(
            f'/api/admin/users/{self.superadmin.pk}/role/', {'role': 'customer'}, format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_role_change_creates_audit_log(self):
        self.client.force_authenticate(user=self.superadmin)
        self.client.patch(f'/api/admin/users/{self.target.pk}/role/', {'role': 'inventory'}, format='json')
        log = AdminAuditLog.objects.filter(action='role_change').first()
        self.assertIsNotNone(log)
        self.assertEqual(log.actor, self.superadmin)
        self.assertEqual(log.target_id, str(self.target.pk))

    def test_audit_log_metadata_has_old_and_new_role(self):
        self.client.force_authenticate(user=self.superadmin)
        self.client.patch(f'/api/admin/users/{self.target.pk}/role/', {'role': 'technician'}, format='json')
        log = AdminAuditLog.objects.filter(action='role_change').first()
        self.assertIn('old_role', log.metadata)
        self.assertIn('new_role', log.metadata)
        self.assertEqual(log.metadata['new_role'], 'technician')

    def test_audit_log_does_not_contain_passwords(self):
        self.client.force_authenticate(user=self.superadmin)
        self.client.patch(f'/api/admin/users/{self.target.pk}/role/', {'role': 'sales'}, format='json')
        log = AdminAuditLog.objects.filter(action='role_change').first()
        log_str = str(log.metadata)
        self.assertNotIn('password', log_str)
        self.assertNotIn('token', log_str)


class AdminAuditLogViewTest(TestCase):
    """GET /api/admin/audit-logs/ — only admin+ can view."""

    def setUp(self):
        self.client = APIClient()
        cache.clear()
        self.customer = User.objects.create_user(username='al_customer', password='Pass123!')
        self.admin_user = User.objects.create_user(username='al_admin', password='Pass123!')
        self.admin_user.profile.role = UserProfile.ROLE_ADMIN
        self.admin_user.profile.save()
        AdminAuditLog.objects.create(
            actor=self.admin_user,
            action='role_change',
            target_type='user',
            target_id='1',
            metadata={'old_role': 'customer', 'new_role': 'sales'},
        )

    def test_anon_gets_401(self):
        response = self.client.get('/api/admin/audit-logs/')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_customer_gets_403(self):
        self.client.force_authenticate(user=self.customer)
        response = self.client.get('/api/admin/audit-logs/')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_can_view_logs(self):
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.get('/api/admin/audit-logs/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIn('results', data)
        self.assertIn('count', data)
        self.assertGreater(data['count'], 0)

    def test_log_response_has_expected_fields(self):
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.get('/api/admin/audit-logs/')
        entry = response.json()['results'][0]
        for field in ('id', 'actor', 'action', 'target_type', 'target_id', 'metadata', 'created_at'):
            self.assertIn(field, entry)


class OrderViewSetRBACTest(TestCase):
    """OrderViewSet respects role-based access: customer→own, sales/admin/superadmin→all."""

    def setUp(self):
        self.client = APIClient()
        cache.clear()
        self.customer1 = User.objects.create_user(username='rbac_c1', password='Pass123!')
        self.customer2 = User.objects.create_user(username='rbac_c2', password='Pass123!')
        self.sales_user = User.objects.create_user(username='rbac_sales', password='Pass123!')
        self.sales_user.profile.role = UserProfile.ROLE_SALES
        self.sales_user.profile.save()
        self.admin_user = User.objects.create_user(username='rbac_admin', password='Pass123!')
        self.admin_user.profile.role = UserProfile.ROLE_ADMIN
        self.admin_user.profile.save()
        self.order1 = Order.objects.create(
            user=self.customer1,
            customer_email='c1@example.com',
            total=Decimal('100.00'),
            status=Order.Status.PAID,
        )
        self.order2 = Order.objects.create(
            user=self.customer2,
            customer_email='c2@example.com',
            total=Decimal('200.00'),
            status=Order.Status.PAID,
        )

    def test_customer_sees_only_own_orders(self):
        self.client.force_authenticate(user=self.customer1)
        response = self.client.get('/api/orders/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = [o['id'] for o in response.json()]
        self.assertIn(self.order1.id, ids)
        self.assertNotIn(self.order2.id, ids)

    def test_customer_cannot_access_other_user_order(self):
        self.client.force_authenticate(user=self.customer1)
        response = self.client.get(f'/api/orders/{self.order2.id}/')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_sales_sees_all_orders(self):
        self.client.force_authenticate(user=self.sales_user)
        response = self.client.get('/api/orders/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = [o['id'] for o in response.json()]
        self.assertIn(self.order1.id, ids)
        self.assertIn(self.order2.id, ids)

    def test_admin_sees_all_orders(self):
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.get('/api/orders/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = [o['id'] for o in response.json()]
        self.assertIn(self.order1.id, ids)
        self.assertIn(self.order2.id, ids)

    def test_anon_gets_401(self):
        response = self.client.get('/api/orders/')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class Phase30RegressionTest(TestCase):
    """Phase 3.0 regression: login, refresh, logout, checkout, webhook, payment, reviews, coupons."""

    def setUp(self):
        self.client = APIClient()
        cache.clear()
        cat = Category.objects.create(name='Reg Cat', slug='reg-cat-30')
        self.product = Product.objects.create(
            name='Reg Product', slug='reg-product-30', price=Decimal('100.00'), inventory=10, category=cat
        )
        self.coupon = Coupon.objects.create(code='REGCOUPON30', discount_percent=10, is_active=True)
        self.user = User.objects.create_user(username='reg30_user', password='StrongPass123!')

    def test_login_still_works(self):
        response = self.client.post('/api/auth/login/', {
            'username': 'reg30_user', 'password': 'StrongPass123!'
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('blackdog_access', response.cookies)

    def test_me_endpoint_still_works(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.get('/api/auth/me/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data['username'], 'reg30_user')
        self.assertIn('role', data)

    def test_logout_still_works(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.post('/api/auth/logout/', {})
        self.assertIn(response.status_code, (status.HTTP_200_OK, status.HTTP_403_FORBIDDEN))

    def test_products_endpoint_still_public(self):
        response = self.client.get('/api/products/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_categories_endpoint_still_public(self):
        response = self.client.get('/api/categories/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_coupon_validate_still_works(self):
        response = self.client.post('/api/coupons/validate/', {'code': 'REGCOUPON30'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_review_list_still_public(self):
        response = self.client.get('/api/reviews/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_cart_still_works(self):
        response = self.client.get('/api/cart/?session_key=reg30session')
        self.assertEqual(response.status_code, status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# Phase 3.1 tests
# ---------------------------------------------------------------------------

class AdminUserListSearchFilterTest(TestCase):
    """Pagination, search and role-filter for GET /api/admin/users/."""

    def setUp(self):
        self.client = APIClient()
        cache.clear()
        self.admin_user = User.objects.create_user(username='sf_admin', password='Pass123!')
        self.admin_user.profile.role = UserProfile.ROLE_ADMIN
        self.admin_user.profile.save()
        # Extra users for search/filter
        self.u_alice = User.objects.create_user(username='alice_sf', email='alice@example.com', password='Pass123!')
        self.u_bob = User.objects.create_user(username='bob_sf', email='bob@example.com', password='Pass123!')
        self.u_sales = User.objects.create_user(username='sales_sf', email='sales@example.com', password='Pass123!')
        self.u_sales.profile.role = UserProfile.ROLE_SALES
        self.u_sales.profile.save()
        self.su = User.objects.create_superuser(username='super_sf', email='super@example.com', password='Pass123!')

    def test_response_has_pagination_structure(self):
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.get('/api/admin/users/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        for key in ('count', 'page', 'page_size', 'results'):
            self.assertIn(key, data)

    def test_search_by_username(self):
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.get('/api/admin/users/?search=alice_sf')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.json()['results']
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['username'], 'alice_sf')

    def test_search_by_email(self):
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.get('/api/admin/users/?search=bob@example')
        results = response.json()['results']
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['email'], 'bob@example.com')

    def test_filter_by_role(self):
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.get(f'/api/admin/users/?role={UserProfile.ROLE_SALES}')
        results = response.json()['results']
        self.assertTrue(all(u['role'] == UserProfile.ROLE_SALES for u in results))
        usernames = [u['username'] for u in results]
        self.assertIn('sales_sf', usernames)

    def test_filter_superadmin_includes_django_superusers(self):
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.get(f'/api/admin/users/?role={UserProfile.ROLE_SUPERADMIN}')
        results = response.json()['results']
        usernames = [u['username'] for u in results]
        self.assertIn('super_sf', usernames)
        self.assertTrue(all(u['role'] == UserProfile.ROLE_SUPERADMIN for u in results))

    def test_page_size_respected(self):
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.get('/api/admin/users/?page_size=2')
        data = response.json()
        self.assertLessEqual(len(data['results']), 2)
        self.assertEqual(data['page_size'], 2)


class AdminAuditLogFilterTest(TestCase):
    """Pagination and filters for GET /api/admin/audit-logs/."""

    def setUp(self):
        self.client = APIClient()
        cache.clear()
        self.admin_user = User.objects.create_user(username='alf_admin', password='Pass123!')
        self.admin_user.profile.role = UserProfile.ROLE_ADMIN
        self.admin_user.profile.save()
        self.actor1 = User.objects.create_user(username='alf_actor1', password='Pass123!')
        self.actor2 = User.objects.create_user(username='alf_actor2', password='Pass123!')
        # Create entries with slight ordering guarantee
        AdminAuditLog.objects.create(
            actor=self.actor1, action='role_change', target_type='user', target_id='1',
            metadata={'old_role': 'customer', 'new_role': 'sales'},
        )
        AdminAuditLog.objects.create(
            actor=self.actor2, action='login_event', target_type='session', target_id='',
            metadata={},
        )

    def test_response_has_pagination_structure(self):
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.get('/api/admin/audit-logs/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        for key in ('count', 'page', 'page_size', 'results'):
            self.assertIn(key, data)

    def test_filter_by_action(self):
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.get('/api/admin/audit-logs/?action=role_change')
        results = response.json()['results']
        self.assertTrue(all(r['action'] == 'role_change' for r in results))

    def test_no_post_method_allowed(self):
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.post('/api/admin/audit-logs/', {}, format='json')
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_filter_by_actor(self):
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.get('/api/admin/audit-logs/?actor=alf_actor1')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.json()['results']
        self.assertTrue(len(results) >= 1)
        self.assertTrue(all(r['actor'] == 'alf_actor1' for r in results))


# ---------------------------------------------------------------------------
# Audit 3.1 — Pagination edge cases, CSRF enforcement, extra-field protection
# ---------------------------------------------------------------------------

class Audit31PaginationEdgeCasesTest(TestCase):
    """page_size cap at 100, invalid page param defaults gracefully."""

    def setUp(self):
        self.client = APIClient()
        cache.clear()
        self.admin_user = User.objects.create_user(username='pe_admin', password='Pass123!')
        self.admin_user.profile.role = UserProfile.ROLE_ADMIN
        self.admin_user.profile.save()

    def test_page_size_exceeds_max_is_clamped_to_100(self):
        """?page_size=500 must be silently clamped to 100."""
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.get('/api/admin/users/?page_size=500')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data['page_size'], 100)

    def test_page_invalid_string_defaults_to_page_1(self):
        """?page=abc must not raise a 500; defaults to page 1."""
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.get('/api/admin/users/?page=abc')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()['page'], 1)

    def test_page_negative_defaults_to_page_1(self):
        """?page=-5 must be clamped to 1 by max(1, ...)."""
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.get('/api/admin/users/?page=-5')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()['page'], 1)

    def test_page_out_of_range_returns_empty_results_with_correct_count(self):
        """?page=9999 must return empty results but count must still reflect total."""
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.get('/api/admin/users/?page=9999&page_size=25')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data['results'], [])
        self.assertGreaterEqual(data['count'], 1)  # at least admin_user exists

    def test_audit_log_page_size_clamped_to_100(self):
        """Same clamping must apply to audit-logs endpoint."""
        AdminAuditLog.objects.create(
            actor=self.admin_user, action='test_action', target_type='test', target_id='1', metadata={},
        )
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.get('/api/admin/audit-logs/?page_size=999')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()['page_size'], 100)


class Audit31RoleChangeCsrfTest(TestCase):
    """PATCH /api/admin/users/{pk}/role/ must enforce CSRF when using cookie auth."""

    def _get_access_token(self, user):
        from rest_framework_simplejwt.tokens import RefreshToken
        return str(RefreshToken.for_user(user).access_token)

    def test_patch_with_cookie_but_no_csrf_is_rejected(self):
        """Cookie auth without X-CSRFToken header must trigger 403 PermissionDenied."""
        from django.test import RequestFactory
        from rest_framework.request import Request as DRFRequest
        from rest_framework.exceptions import PermissionDenied
        from store.authentication import CookieJWTAuthentication

        superadmin = User.objects.create_user(username='csrf_sa', password='Pass123!')
        superadmin.profile.role = UserProfile.ROLE_SUPERADMIN
        superadmin.profile.save()
        access = self._get_access_token(superadmin)

        raw = RequestFactory().patch(
            '/api/admin/users/1/role/',
            content_type='application/json',
            data='{"role": "sales"}',
        )
        raw.COOKIES['blackdog_access'] = access
        # Intentionally NO csrftoken cookie

        with self.assertRaises(PermissionDenied):
            CookieJWTAuthentication().authenticate(DRFRequest(raw))

    def test_get_with_cookie_no_csrf_succeeds_safe_method(self):
        """GET /api/admin/users/ must not require CSRF — safe methods are exempt."""
        from django.test import RequestFactory
        from rest_framework.request import Request as DRFRequest
        from store.authentication import CookieJWTAuthentication

        admin = User.objects.create_user(username='csrf_admin_get', password='Pass123!')
        admin.profile.role = UserProfile.ROLE_ADMIN
        admin.profile.save()
        access = self._get_access_token(admin)

        raw = RequestFactory().get('/api/admin/users/')
        raw.COOKIES['blackdog_access'] = access

        result = CookieJWTAuthentication().authenticate(DRFRequest(raw))
        self.assertIsNotNone(result)
        self.assertEqual(result[0], admin)


class Audit31ExtraFieldsIgnoredTest(TestCase):
    """PATCH /api/admin/users/{pk}/role/ must silently ignore extra request body fields."""

    def setUp(self):
        self.client = APIClient()
        cache.clear()
        self.superadmin = User.objects.create_user(username='ef_superadmin', password='Pass123!')
        self.superadmin.profile.role = UserProfile.ROLE_SUPERADMIN
        self.superadmin.profile.save()
        self.target = User.objects.create_user(username='ef_target', password='OriginalPass123!')

    def test_extra_fields_in_body_do_not_modify_user(self):
        """Sending is_superuser=true, password=hacked alongside role must not change those fields."""
        self.client.force_authenticate(user=self.superadmin)
        original_password_hash = self.target.password
        original_is_superuser = self.target.is_superuser

        response = self.client.patch(
            f'/api/admin/users/{self.target.pk}/role/',
            {
                'role': 'sales',
                'is_superuser': True,
                'password': 'hacked_password',
                'username': 'hijacked_username',
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.target.refresh_from_db()
        self.assertEqual(self.target.password, original_password_hash)
        self.assertEqual(self.target.is_superuser, original_is_superuser)
        self.assertEqual(self.target.username, 'ef_target')
        self.assertEqual(self.target.profile.role, UserProfile.ROLE_SALES)


# ---------------------------------------------------------------------------
# Phase 3.2 — Admin product tests
# ---------------------------------------------------------------------------

def _make_product(name, slug, price='100.00', inventory=10, is_active=True, category=None):
    return Product.objects.create(
        name=name, slug=slug, price=Decimal(price),
        inventory=inventory, is_active=is_active, category=category,
    )


def _make_roles(prefix):
    """Helper: create one user per role, return dict role->user."""
    roles = {}
    for role in (
        UserProfile.ROLE_CUSTOMER, UserProfile.ROLE_SALES, UserProfile.ROLE_INVENTORY,
        UserProfile.ROLE_TECHNICIAN, UserProfile.ROLE_ADMIN,
    ):
        u = User.objects.create_user(username=f'{prefix}_{role}', password='Pass123!')
        u.profile.role = role
        u.profile.save()
        roles[role] = u
    su = User.objects.create_user(username=f'{prefix}_superadmin', password='Pass123!')
    su.is_superuser = True
    su.save()
    roles[UserProfile.ROLE_SUPERADMIN] = su
    return roles


class AdminProductListAccessTest(TestCase):
    """GET /api/admin/products/ — access control."""

    def setUp(self):
        self.client = APIClient()
        cache.clear()
        self.roles = _make_roles('pla')
        cat = Category.objects.create(name='PLA Cat', slug='pla-cat')
        _make_product('PLA Product', 'pla-product', category=cat)

    def test_anon_gets_401(self):
        response = self.client.get('/api/admin/products/')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_customer_gets_403(self):
        self.client.force_authenticate(user=self.roles[UserProfile.ROLE_CUSTOMER])
        self.assertEqual(self.client.get('/api/admin/products/').status_code, status.HTTP_403_FORBIDDEN)

    def test_technician_gets_403(self):
        self.client.force_authenticate(user=self.roles[UserProfile.ROLE_TECHNICIAN])
        self.assertEqual(self.client.get('/api/admin/products/').status_code, status.HTTP_403_FORBIDDEN)

    def test_sales_can_list(self):
        self.client.force_authenticate(user=self.roles[UserProfile.ROLE_SALES])
        self.assertEqual(self.client.get('/api/admin/products/').status_code, status.HTTP_200_OK)

    def test_inventory_can_list(self):
        self.client.force_authenticate(user=self.roles[UserProfile.ROLE_INVENTORY])
        self.assertEqual(self.client.get('/api/admin/products/').status_code, status.HTTP_200_OK)

    def test_admin_can_list(self):
        self.client.force_authenticate(user=self.roles[UserProfile.ROLE_ADMIN])
        self.assertEqual(self.client.get('/api/admin/products/').status_code, status.HTTP_200_OK)

    def test_superadmin_can_list(self):
        self.client.force_authenticate(user=self.roles[UserProfile.ROLE_SUPERADMIN])
        self.assertEqual(self.client.get('/api/admin/products/').status_code, status.HTTP_200_OK)

    def test_response_has_pagination_structure(self):
        self.client.force_authenticate(user=self.roles[UserProfile.ROLE_ADMIN])
        data = self.client.get('/api/admin/products/').json()
        for key in ('count', 'page', 'page_size', 'results'):
            self.assertIn(key, data)

    def test_response_has_is_active_and_updated_at(self):
        self.client.force_authenticate(user=self.roles[UserProfile.ROLE_ADMIN])
        results = self.client.get('/api/admin/products/').json()['results']
        self.assertTrue(len(results) >= 1)
        self.assertIn('is_active', results[0])
        self.assertIn('updated_at', results[0])

    def test_admin_list_includes_inactive_products(self):
        _make_product('Inactive P', 'inactive-p-list', is_active=False)
        self.client.force_authenticate(user=self.roles[UserProfile.ROLE_ADMIN])
        data = self.client.get('/api/admin/products/').json()
        slugs = [r['slug'] for r in data['results']]
        self.assertIn('inactive-p-list', slugs)


class AdminProductListFilterTest(TestCase):
    """GET /api/admin/products/ — search and filters."""

    def setUp(self):
        self.client = APIClient()
        cache.clear()
        Product.objects.all().delete()
        self.admin = User.objects.create_user(username='pf_admin', password='Pass123!')
        self.admin.profile.role = UserProfile.ROLE_ADMIN
        self.admin.profile.save()
        self.cat = Category.objects.create(name='PF Cat', slug='pf-cat')
        self.p1 = _make_product('iPhone 15', 'iphone-15-pf', inventory=5, is_active=True, category=self.cat)
        self.p2 = _make_product('MacBook Air', 'macbook-air-pf', inventory=0, is_active=True)
        self.p3 = _make_product('Old iPad', 'old-ipad-pf', inventory=2, is_active=False)

    def test_search_by_name(self):
        self.client.force_authenticate(user=self.admin)
        results = self.client.get('/api/admin/products/?search=iphone').json()['results']
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['slug'], 'iphone-15-pf')

    def test_filter_by_is_active_true(self):
        self.client.force_authenticate(user=self.admin)
        results = self.client.get('/api/admin/products/?is_active=true').json()['results']
        self.assertTrue(all(r['is_active'] for r in results))
        slugs = [r['slug'] for r in results]
        self.assertNotIn('old-ipad-pf', slugs)

    def test_filter_by_is_active_false(self):
        self.client.force_authenticate(user=self.admin)
        results = self.client.get('/api/admin/products/?is_active=false').json()['results']
        self.assertTrue(all(not r['is_active'] for r in results))

    def test_filter_by_category(self):
        self.client.force_authenticate(user=self.admin)
        results = self.client.get(f'/api/admin/products/?category={self.cat.pk}').json()['results']
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['slug'], 'iphone-15-pf')

    def test_filter_stock_in_stock(self):
        self.client.force_authenticate(user=self.admin)
        results = self.client.get('/api/admin/products/?stock=in_stock').json()['results']
        self.assertTrue(all(r['inventory'] > 0 for r in results))

    def test_filter_stock_out_of_stock(self):
        self.client.force_authenticate(user=self.admin)
        results = self.client.get('/api/admin/products/?stock=out_of_stock').json()['results']
        self.assertTrue(all(r['inventory'] == 0 for r in results))

    def test_filter_stock_low_stock(self):
        self.client.force_authenticate(user=self.admin)
        results = self.client.get('/api/admin/products/?stock=low_stock').json()['results']
        self.assertTrue(all(0 < r['inventory'] <= 5 for r in results))


class AdminProductCreateTest(TestCase):
    """POST /api/admin/products/ — create product permissions and validation."""

    def setUp(self):
        self.client = APIClient()
        cache.clear()
        self.roles = _make_roles('pc')
        self.cat = Category.objects.create(name='PC Cat', slug='pc-cat')

    def _base_payload(self, **overrides):
        data = {
            'name': 'Test Product',
            'price': '299.00',
            'inventory': 5,
            'category': self.cat.pk,
        }
        data.update(overrides)
        return data

    def test_anon_gets_401(self):
        response = self.client.post('/api/admin/products/', self._base_payload(), format='json')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_customer_gets_403(self):
        self.client.force_authenticate(user=self.roles[UserProfile.ROLE_CUSTOMER])
        self.assertEqual(
            self.client.post('/api/admin/products/', self._base_payload(), format='json').status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_inventory_role_cannot_create(self):
        self.client.force_authenticate(user=self.roles[UserProfile.ROLE_INVENTORY])
        self.assertEqual(
            self.client.post('/api/admin/products/', self._base_payload(), format='json').status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_sales_role_cannot_create(self):
        self.client.force_authenticate(user=self.roles[UserProfile.ROLE_SALES])
        self.assertEqual(
            self.client.post('/api/admin/products/', self._base_payload(), format='json').status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_admin_can_create(self):
        self.client.force_authenticate(user=self.roles[UserProfile.ROLE_ADMIN])
        response = self.client.post('/api/admin/products/', self._base_payload(), format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIn('id', response.json())

    def test_superadmin_can_create(self):
        self.client.force_authenticate(user=self.roles[UserProfile.ROLE_SUPERADMIN])
        response = self.client.post('/api/admin/products/', self._base_payload(name='Super Product'), format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_price_zero_rejected(self):
        self.client.force_authenticate(user=self.roles[UserProfile.ROLE_ADMIN])
        response = self.client.post('/api/admin/products/', self._base_payload(price='0'), format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_price_negative_rejected(self):
        self.client.force_authenticate(user=self.roles[UserProfile.ROLE_ADMIN])
        response = self.client.post('/api/admin/products/', self._base_payload(price='-10'), format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_inventory_negative_rejected(self):
        self.client.force_authenticate(user=self.roles[UserProfile.ROLE_ADMIN])
        response = self.client.post('/api/admin/products/', self._base_payload(inventory=-1), format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_slug_auto_generated_from_name(self):
        self.client.force_authenticate(user=self.roles[UserProfile.ROLE_ADMIN])
        response = self.client.post('/api/admin/products/', self._base_payload(name='Auto Slug Product'), format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIn('slug', response.json())
        self.assertNotEqual(response.json()['slug'], '')

    def test_slug_duplicate_rejected(self):
        _make_product('Existing', 'existing-slug-pc')
        self.client.force_authenticate(user=self.roles[UserProfile.ROLE_ADMIN])
        response = self.client.post(
            '/api/admin/products/', self._base_payload(name='Dup', slug='existing-slug-pc'), format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_create_creates_audit_log(self):
        self.client.force_authenticate(user=self.roles[UserProfile.ROLE_ADMIN])
        self.client.post('/api/admin/products/', self._base_payload(name='Audit Product Create'), format='json')
        log = AdminAuditLog.objects.filter(action='product_created').first()
        self.assertIsNotNone(log)
        self.assertIn('product_name', log.metadata)

    def test_response_does_not_contain_sensitive_fields(self):
        self.client.force_authenticate(user=self.roles[UserProfile.ROLE_ADMIN])
        response = self.client.post('/api/admin/products/', self._base_payload(name='Safe Product'), format='json')
        data = response.json()
        for field in ('password', 'token', 'stripe_secret', 'cookie'):
            self.assertNotIn(field, data)


class AdminProductDetailTest(TestCase):
    """GET and PATCH /api/admin/products/{pk}/."""

    def setUp(self):
        self.client = APIClient()
        cache.clear()
        self.roles = _make_roles('pd')
        self.cat = Category.objects.create(name='PD Cat', slug='pd-cat')
        self.product = _make_product('PD iPhone', 'pd-iphone', price='999.00', inventory=10, category=self.cat)

    def test_anon_get_401(self):
        self.assertEqual(
            self.client.get(f'/api/admin/products/{self.product.pk}/').status_code,
            status.HTTP_401_UNAUTHORIZED,
        )

    def test_customer_get_403(self):
        self.client.force_authenticate(user=self.roles[UserProfile.ROLE_CUSTOMER])
        self.assertEqual(
            self.client.get(f'/api/admin/products/{self.product.pk}/').status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_admin_can_get_detail(self):
        self.client.force_authenticate(user=self.roles[UserProfile.ROLE_ADMIN])
        response = self.client.get(f'/api/admin/products/{self.product.pk}/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()['slug'], 'pd-iphone')

    def test_inventory_can_get_detail(self):
        self.client.force_authenticate(user=self.roles[UserProfile.ROLE_INVENTORY])
        self.assertEqual(
            self.client.get(f'/api/admin/products/{self.product.pk}/').status_code,
            status.HTTP_200_OK,
        )

    def test_admin_can_patch_price(self):
        self.client.force_authenticate(user=self.roles[UserProfile.ROLE_ADMIN])
        response = self.client.patch(
            f'/api/admin/products/{self.product.pk}/', {'price': '1299.00'}, format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()['price'], '1299.00')

    def test_inventory_role_cannot_patch_price(self):
        self.client.force_authenticate(user=self.roles[UserProfile.ROLE_INVENTORY])
        response = self.client.patch(
            f'/api/admin/products/{self.product.pk}/', {'price': '1.00'}, format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_sales_role_cannot_patch(self):
        self.client.force_authenticate(user=self.roles[UserProfile.ROLE_SALES])
        response = self.client.patch(
            f'/api/admin/products/{self.product.pk}/', {'name': 'Hacked'}, format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_deactivate_creates_audit_log(self):
        self.client.force_authenticate(user=self.roles[UserProfile.ROLE_ADMIN])
        self.client.patch(f'/api/admin/products/{self.product.pk}/', {'is_active': False}, format='json')
        log = AdminAuditLog.objects.filter(action='product_deactivated').first()
        self.assertIsNotNone(log)
        self.assertEqual(log.target_id, str(self.product.pk))

    def test_reactivate_creates_audit_log(self):
        self.product.is_active = False
        self.product.save()
        self.client.force_authenticate(user=self.roles[UserProfile.ROLE_ADMIN])
        self.client.patch(f'/api/admin/products/{self.product.pk}/', {'is_active': True}, format='json')
        log = AdminAuditLog.objects.filter(action='product_reactivated').first()
        self.assertIsNotNone(log)

    def test_patch_no_change_creates_no_audit_log(self):
        initial_log_count = AdminAuditLog.objects.count()
        self.client.force_authenticate(user=self.roles[UserProfile.ROLE_ADMIN])
        self.client.patch(f'/api/admin/products/{self.product.pk}/', {}, format='json')
        self.assertEqual(AdminAuditLog.objects.count(), initial_log_count)

    def test_delete_returns_405(self):
        self.client.force_authenticate(user=self.roles[UserProfile.ROLE_SUPERADMIN])
        self.assertEqual(
            self.client.delete(f'/api/admin/products/{self.product.pk}/').status_code,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        )

    def test_nonexistent_product_returns_404(self):
        self.client.force_authenticate(user=self.roles[UserProfile.ROLE_ADMIN])
        self.assertEqual(
            self.client.get('/api/admin/products/999999/').status_code,
            status.HTTP_404_NOT_FOUND,
        )


class AdminInventoryAdjustTest(TestCase):
    """POST /api/admin/products/{pk}/inventory-adjust/."""

    def setUp(self):
        self.client = APIClient()
        cache.clear()
        self.roles = _make_roles('ia')
        self.product = _make_product('IA iPhone', 'ia-iphone', inventory=10)

    def _url(self):
        return f'/api/admin/products/{self.product.pk}/inventory-adjust/'

    def test_anon_gets_401(self):
        self.assertEqual(
            self.client.post(self._url(), {'delta': 1, 'reason': 'Test'}, format='json').status_code,
            status.HTTP_401_UNAUTHORIZED,
        )

    def test_customer_gets_403(self):
        self.client.force_authenticate(user=self.roles[UserProfile.ROLE_CUSTOMER])
        self.assertEqual(
            self.client.post(self._url(), {'delta': 1, 'reason': 'Test'}, format='json').status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_sales_gets_403(self):
        self.client.force_authenticate(user=self.roles[UserProfile.ROLE_SALES])
        self.assertEqual(
            self.client.post(self._url(), {'delta': 1, 'reason': 'Test'}, format='json').status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_technician_gets_403(self):
        self.client.force_authenticate(user=self.roles[UserProfile.ROLE_TECHNICIAN])
        self.assertEqual(
            self.client.post(self._url(), {'delta': 1, 'reason': 'Test'}, format='json').status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_inventory_role_can_adjust(self):
        self.client.force_authenticate(user=self.roles[UserProfile.ROLE_INVENTORY])
        response = self.client.post(self._url(), {'delta': 5, 'reason': 'Ingreso de stock'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_admin_can_adjust(self):
        self.client.force_authenticate(user=self.roles[UserProfile.ROLE_ADMIN])
        response = self.client.post(self._url(), {'delta': -3, 'reason': 'Corrección manual'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_superadmin_can_adjust(self):
        self.client.force_authenticate(user=self.roles[UserProfile.ROLE_SUPERADMIN])
        response = self.client.post(self._url(), {'delta': 2, 'reason': 'Superadmin test'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_delta_zero_rejected(self):
        self.client.force_authenticate(user=self.roles[UserProfile.ROLE_INVENTORY])
        response = self.client.post(self._url(), {'delta': 0, 'reason': 'Zero delta'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_reason_too_short_rejected(self):
        self.client.force_authenticate(user=self.roles[UserProfile.ROLE_INVENTORY])
        response = self.client.post(self._url(), {'delta': 1, 'reason': 'AB'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_reason_empty_rejected(self):
        self.client.force_authenticate(user=self.roles[UserProfile.ROLE_INVENTORY])
        response = self.client.post(self._url(), {'delta': 1, 'reason': ''}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_negative_delta_leaving_stock_negative_rejected(self):
        self.client.force_authenticate(user=self.roles[UserProfile.ROLE_INVENTORY])
        response = self.client.post(self._url(), {'delta': -99, 'reason': 'Overdraft test'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_positive_adjust_updates_inventory(self):
        self.client.force_authenticate(user=self.roles[UserProfile.ROLE_ADMIN])
        self.client.post(self._url(), {'delta': 5, 'reason': 'Restock bulk'}, format='json')
        self.product.refresh_from_db()
        self.assertEqual(self.product.inventory, 15)

    def test_negative_adjust_updates_inventory(self):
        self.client.force_authenticate(user=self.roles[UserProfile.ROLE_ADMIN])
        self.client.post(self._url(), {'delta': -3, 'reason': 'Manual removal'}, format='json')
        self.product.refresh_from_db()
        self.assertEqual(self.product.inventory, 7)

    def test_adjust_creates_audit_log(self):
        self.client.force_authenticate(user=self.roles[UserProfile.ROLE_ADMIN])
        self.client.post(self._url(), {'delta': 4, 'reason': 'Audit test reason'}, format='json')
        log = AdminAuditLog.objects.filter(action='product_inventory_adjusted').first()
        self.assertIsNotNone(log)
        self.assertEqual(log.metadata['delta'], 4)
        self.assertEqual(log.metadata['reason'], 'Audit test reason')
        self.assertEqual(log.metadata['old_inventory'], 10)
        self.assertEqual(log.metadata['new_inventory'], 14)

    def test_adjust_audit_log_no_sensitive_data(self):
        self.client.force_authenticate(user=self.roles[UserProfile.ROLE_ADMIN])
        self.client.post(self._url(), {'delta': 1, 'reason': 'Security test'}, format='json')
        log = AdminAuditLog.objects.filter(action='product_inventory_adjusted').first()
        log_str = str(log.metadata)
        for sensitive in ('password', 'token', 'stripe', 'cookie'):
            self.assertNotIn(sensitive, log_str)

    def test_nonexistent_product_returns_404(self):
        self.client.force_authenticate(user=self.roles[UserProfile.ROLE_ADMIN])
        response = self.client.post(
            '/api/admin/products/999999/inventory-adjust/',
            {'delta': 1, 'reason': 'Test'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class AdminCategoryTest(TestCase):
    """GET and POST /api/admin/categories/."""

    def setUp(self):
        self.client = APIClient()
        cache.clear()
        self.admin = User.objects.create_user(username='catadmin', password='Pass123!')
        self.admin.profile.role = UserProfile.ROLE_ADMIN
        self.admin.profile.save()
        self.customer = User.objects.create_user(username='catcustomer', password='Pass123!')
        Category.objects.create(name='Cat A', slug='cat-a-test')

    def test_anon_gets_401(self):
        self.assertEqual(self.client.get('/api/admin/categories/').status_code, status.HTTP_401_UNAUTHORIZED)

    def test_customer_gets_403(self):
        self.client.force_authenticate(user=self.customer)
        self.assertEqual(self.client.get('/api/admin/categories/').status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_can_list(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.get('/api/admin/categories/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsInstance(response.json(), list)

    def test_admin_can_create_category(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post('/api/admin/categories/', {'name': 'Accesorios'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIn('slug', response.json())

    def test_customer_cannot_create_category(self):
        self.client.force_authenticate(user=self.customer)
        self.assertEqual(
            self.client.post('/api/admin/categories/', {'name': 'Hack'}, format='json').status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_create_category_creates_audit_log(self):
        self.client.force_authenticate(user=self.admin)
        self.client.post('/api/admin/categories/', {'name': 'Nueva Categoria'}, format='json')
        log = AdminAuditLog.objects.filter(action='category_created').first()
        self.assertIsNotNone(log)
        self.assertIn('name', log.metadata)


class ProductIsActivePublicCatalogTest(TestCase):
    """Public catalog must not show inactive products; admin must see them."""

    def setUp(self):
        self.client = APIClient()
        cache.clear()
        cat = Category.objects.create(name='PUB Cat', slug='pub-cat')
        self.active = _make_product('Active Pub', 'active-pub', is_active=True, category=cat)
        self.inactive = _make_product('Inactive Pub', 'inactive-pub', is_active=False, category=cat)
        self.admin = User.objects.create_user(username='pub_admin', password='Pass123!')
        self.admin.profile.role = UserProfile.ROLE_ADMIN
        self.admin.profile.save()

    def test_inactive_product_not_in_public_catalog(self):
        response = self.client.get('/api/products/')
        slugs = [p['slug'] for p in response.json()]
        self.assertNotIn('inactive-pub', slugs)
        self.assertIn('active-pub', slugs)

    def test_inactive_product_appears_in_admin_list(self):
        self.client.force_authenticate(user=self.admin)
        data = self.client.get('/api/admin/products/').json()
        slugs = [p['slug'] for p in data['results']]
        self.assertIn('inactive-pub', slugs)

    def test_inactive_product_blocked_in_checkout(self):
        """Checkout must reject cart items containing inactive products."""
        CartItem.objects.create(session_key='pub_sess', product=self.inactive, quantity=1)
        with patch('store.views.stripe') as mock_stripe:
            mock_stripe.checkout.Session.create.return_value = MagicMock(id='cs_test', url='https://stripe.com/test')
            mock_stripe.api_key = ''
            with override_settings(STRIPE_SECRET_KEY='sk_test_fake', STRIPE_WEBHOOK_SECRET='whsec_fake'):
                response = self.client.post('/api/payments/create-checkout-session/', {
                    'session_key': 'pub_sess',
                    'customer_name': 'Test',
                    'customer_email': 'test@test.com',
                }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('errors', response.json())


class Phase32RegressionTest(TestCase):
    """Regression: existing endpoints still work after Phase 3.2 changes."""

    def setUp(self):
        self.client = APIClient()
        cache.clear()
        cat = Category.objects.create(name='Reg32 Cat', slug='reg32-cat')
        self.product = _make_product('Reg32 Product', 'reg32-product', price='500.00', inventory=10, category=cat)
        self.user = User.objects.create_user(username='reg32_user', password='Pass123!')

    def test_public_product_list_still_works(self):
        self.assertEqual(self.client.get('/api/products/').status_code, status.HTTP_200_OK)

    def test_public_product_detail_still_works(self):
        self.assertEqual(self.client.get(f'/api/products/{self.product.pk}/').status_code, status.HTTP_200_OK)

    def test_public_categories_still_works(self):
        self.assertEqual(self.client.get('/api/categories/').status_code, status.HTTP_200_OK)

    def test_cart_still_works(self):
        self.assertEqual(self.client.get('/api/cart/?session_key=reg32sess').status_code, status.HTTP_200_OK)

    def test_auth_login_still_works(self):
        response = self.client.post('/api/auth/login/', {'username': 'reg32_user', 'password': 'Pass123!'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_admin_users_still_works(self):
        admin = User.objects.create_user(username='reg32_admin', password='Pass123!')
        admin.profile.role = UserProfile.ROLE_ADMIN
        admin.profile.save()
        self.client.force_authenticate(user=admin)
        self.assertEqual(self.client.get('/api/admin/users/').status_code, status.HTTP_200_OK)

    def test_admin_audit_logs_still_works(self):
        admin = User.objects.create_user(username='reg32_adminb', password='Pass123!')
        admin.profile.role = UserProfile.ROLE_ADMIN
        admin.profile.save()
        self.client.force_authenticate(user=admin)
        self.assertEqual(self.client.get('/api/admin/audit-logs/').status_code, status.HTTP_200_OK)

    def test_product_public_serializer_no_is_active_field(self):
        """Public ProductSerializer must NOT expose is_active to clients."""
        response = self.client.get(f'/api/products/{self.product.pk}/')
        self.assertNotIn('is_active', response.json())
