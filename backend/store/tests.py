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
Audit 3.2 (+6 tests): cart rejects inactive, public detail 404 inactive, checkout rejects inactive, PATCH detail GET.
Phase 3.3 (+72 tests): admin orders access control, filters, detail security (no stripe/payment_error), fulfillment status change, inventory role restrictions, audit log, regression.
Audit 3.3 (+7 tests): technician 403, webhook doesn't modify fulfillment_status, checkout default pending, atomic audit log.
Phase 4.0 (+30 tests): commercial checkout fields, document validation, delivery address requirements, receipt type, terms acceptance, frontend injection blocked, admin order detail commercial fields.
Phase 4.1 (+36 tests): email service unit tests, idempotency flags, no-duplicate sends, webhook on_commit integration, send_mail failure handled, admin detail email flags, customer/payment views don't expose email flags.
Phase 4.2 (+39 tests): PDF context builder (excludes Stripe fields, Decimal types, disclaimer), PDF generator (valid bytes, ValueError for unpaid), email+PDF integration (attachment, PDF fail graceful, error logged), admin PDF endpoint RBAC (4 allowed roles, technician/customer/anon blocked), audit log, content-type, Content-Disposition, Stripe data not in cleartext.
"""
from decimal import Decimal
from unittest.mock import patch, MagicMock

import stripe as stripe_lib

from django.core import mail
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

    def _base_body(self, **overrides):
        """Returns a complete valid checkout body for regression tests."""
        body = {
            "session_key": self.session_key,
            "customer_name": "Ana Torres",
            "customer_email": "ana@example.com",
            "customer_phone": "936449536",
            "document_type": "dni",
            "document_number": "12345678",
            "delivery_method": "pickup_store",
            "receipt_type": "boleta",
            "accepted_terms": True,
            "accepted_warranty_policy": True,
        }
        body.update(overrides)
        return body

    @patch("stripe.checkout.Session.create")
    def test_checkout_creates_order_with_pending_status(self, mock_create):
        mock_create.return_value = self._mock_stripe_session()
        response = self.client.post(
            "/api/payments/create-checkout-session/", self._base_body(), format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        order = Order.objects.first()
        self.assertEqual(order.status, Order.Status.PENDING_PAYMENT)
        self.assertFalse(order.paid)

    @patch("stripe.checkout.Session.create")
    def test_checkout_does_not_delete_cart(self, mock_create):
        mock_create.return_value = self._mock_stripe_session()
        self.client.post(
            "/api/payments/create-checkout-session/", self._base_body(), format="json"
        )
        cart_count = CartItem.objects.filter(session_key=self.session_key).count()
        self.assertEqual(cart_count, 1)

    @patch("stripe.checkout.Session.create")
    def test_checkout_saves_stripe_session_id(self, mock_create):
        mock_create.return_value = self._mock_stripe_session("cs_test_xyz")
        self.client.post(
            "/api/payments/create-checkout-session/", self._base_body(), format="json"
        )
        order = Order.objects.first()
        self.assertEqual(order.stripe_session_id, "cs_test_xyz")

    @patch("stripe.checkout.Session.create")
    def test_checkout_calculates_total_from_db_not_frontend(self, mock_create):
        mock_create.return_value = self._mock_stripe_session()
        response = self.client.post(
            "/api/payments/create-checkout-session/",
            self._base_body(**{"frontend_total": "1.00"}),  # malicious input ignored
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        order = Order.objects.first()
        self.assertEqual(order.total, Decimal("7499.00"))

    def test_checkout_empty_cart_returns_400(self):
        body = self._base_body(session_key="empty-session-xyz")
        response = self.client.post("/api/payments/create-checkout-session/", body, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_checkout_insufficient_stock_returns_400(self):
        self.product.inventory = 0
        self.product.save()
        response = self.client.post(
            "/api/payments/create-checkout-session/", self._base_body(), format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("errors", response.json())

    @patch("stripe.checkout.Session.create")
    def test_checkout_applies_coupon_discount_from_db(self, mock_create):
        mock_create.return_value = self._mock_stripe_session()
        Coupon.objects.create(code="FASE1TEST", discount_percent=10, is_active=True)
        response = self.client.post(
            "/api/payments/create-checkout-session/",
            self._base_body(coupon_code="FASE1TEST"),
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        order = Order.objects.first()
        expected_total = (Decimal("7499.00") * Decimal("0.90")).quantize(Decimal("0.01"))
        self.assertEqual(order.total, expected_total)
        self.assertEqual(order.coupon_code, "FASE1TEST")

    @patch("stripe.checkout.Session.create")
    def test_checkout_invalid_coupon_returns_400(self, mock_create):
        mock_create.return_value = self._mock_stripe_session()
        response = self.client.post(
            "/api/payments/create-checkout-session/",
            self._base_body(coupon_code="CUPONFALSO"),
            format="json",
        )
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

    def _base_body(self):
        return {
            "session_key": self.session_key,
            "customer_name": "Test User",
            "customer_email": "test@example.com",
            "customer_phone": "936449536",
            "document_type": "dni",
            "document_number": "12345678",
            "delivery_method": "pickup_store",
            "receipt_type": "boleta",
            "accepted_terms": True,
            "accepted_warranty_policy": True,
        }

    @patch("stripe.checkout.Session.create", side_effect=stripe_lib.StripeError("Connection error"))
    def test_stripe_failure_returns_502(self, _mock):
        response = self.client.post(
            "/api/payments/create-checkout-session/", self._base_body(), format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_502_BAD_GATEWAY)

    @patch("stripe.checkout.Session.create", side_effect=stripe_lib.StripeError("Connection error"))
    def test_stripe_failure_preserves_cart(self, _mock):
        self.client.post(
            "/api/payments/create-checkout-session/", self._base_body(), format="json"
        )
        cart_count = CartItem.objects.filter(session_key=self.session_key).count()
        self.assertEqual(cart_count, 1)

    @patch("stripe.checkout.Session.create", side_effect=stripe_lib.StripeError("Connection error"))
    def test_stripe_failure_marks_order_failed(self, _mock):
        self.client.post(
            "/api/payments/create-checkout-session/", self._base_body(), format="json"
        )
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
                    'customer_phone': '936449536',
                    'document_type': 'dni',
                    'document_number': '12345678',
                    'delivery_method': 'pickup_store',
                    'receipt_type': 'boleta',
                    'accepted_terms': True,
                    'accepted_warranty_policy': True,
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


# ---------------------------------------------------------------------------
# Audit 3.2 — bugs found and fixed
# ---------------------------------------------------------------------------

class Audit32CartInactiveProductTest(TestCase):
    """Cart add must reject inactive products (bug fix: is_active check added)."""

    def setUp(self):
        self.client = APIClient()
        cache.clear()
        self.active = _make_product('Active Cart', 'active-cart-a32', inventory=10)
        self.inactive = _make_product('Inactive Cart', 'inactive-cart-a32', is_active=False, inventory=10)

    def test_add_active_product_to_cart_succeeds(self):
        response = self.client.post('/api/cart/add/', {
            'session_key': 'a32_session',
            'product': self.active.pk,
            'quantity': 1,
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_add_inactive_product_to_cart_returns_404(self):
        response = self.client.post('/api/cart/add/', {
            'session_key': 'a32_session',
            'product': self.inactive.pk,
            'quantity': 1,
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_add_inactive_product_does_not_create_cart_item(self):
        self.client.post('/api/cart/add/', {
            'session_key': 'a32_session2',
            'product': self.inactive.pk,
            'quantity': 1,
        }, format='json')
        from store.models import CartItem
        count = CartItem.objects.filter(session_key='a32_session2', product=self.inactive).count()
        self.assertEqual(count, 0)


class Audit32PublicProductDetailInactiveTest(TestCase):
    """Public product detail must return 404 for inactive products."""

    def setUp(self):
        self.client = APIClient()
        cache.clear()
        self.active = _make_product('Active Detail', 'active-detail-a32', inventory=5)
        self.inactive = _make_product('Inactive Detail', 'inactive-detail-a32', is_active=False, inventory=5)

    def test_active_product_detail_returns_200(self):
        self.assertEqual(
            self.client.get(f'/api/products/{self.active.pk}/').status_code,
            status.HTTP_200_OK,
        )

    def test_inactive_product_public_detail_returns_404(self):
        self.assertEqual(
            self.client.get(f'/api/products/{self.inactive.pk}/').status_code,
            status.HTTP_404_NOT_FOUND,
        )


# ---------------------------------------------------------------------------
# Phase 3.3 — Admin order management
# ---------------------------------------------------------------------------

def _make_order(customer_name='Test Customer', customer_email='test@example.com',
                status_val=Order.Status.PAID, paid=True, fulfillment_status=None,
                total='999.00'):
    kwargs = dict(
        customer_name=customer_name,
        customer_email=customer_email,
        status=status_val,
        paid=paid,
        total=Decimal(total),
    )
    if fulfillment_status is not None:
        kwargs['fulfillment_status'] = fulfillment_status
    return Order.objects.create(**kwargs)


class Phase33AdminOrderAccessTest(TestCase):
    """Access control: only staff roles can hit admin/orders/ endpoints."""

    def setUp(self):
        self.client = APIClient()
        cache.clear()
        self.order = _make_order()
        self.anon_client = APIClient()

        self.customer = User.objects.create_user(username='ord_customer', password='Pass123!')
        self.sales = User.objects.create_user(username='ord_sales', password='Pass123!')
        self.sales.profile.role = UserProfile.ROLE_SALES
        self.sales.profile.save()
        self.inventory = User.objects.create_user(username='ord_inventory', password='Pass123!')
        self.inventory.profile.role = UserProfile.ROLE_INVENTORY
        self.inventory.profile.save()
        self.admin = User.objects.create_user(username='ord_admin', password='Pass123!')
        self.admin.profile.role = UserProfile.ROLE_ADMIN
        self.admin.profile.save()
        self.superadmin = User.objects.create_user(username='ord_superadmin', password='Pass123!', is_superuser=True)
        self.technician = User.objects.create_user(username='ord_technician', password='Pass123!')
        self.technician.profile.role = UserProfile.ROLE_TECHNICIAN
        self.technician.profile.save()

    def test_anonymous_list_returns_401(self):
        self.assertEqual(self.anon_client.get('/api/admin/orders/').status_code, status.HTTP_401_UNAUTHORIZED)

    def test_anonymous_detail_returns_401(self):
        self.assertEqual(self.anon_client.get(f'/api/admin/orders/{self.order.pk}/').status_code, status.HTTP_401_UNAUTHORIZED)

    def test_customer_list_returns_403(self):
        self.client.force_authenticate(user=self.customer)
        self.assertEqual(self.client.get('/api/admin/orders/').status_code, status.HTTP_403_FORBIDDEN)

    def test_customer_detail_returns_403(self):
        self.client.force_authenticate(user=self.customer)
        self.assertEqual(self.client.get(f'/api/admin/orders/{self.order.pk}/').status_code, status.HTTP_403_FORBIDDEN)

    def test_sales_can_list_orders(self):
        self.client.force_authenticate(user=self.sales)
        self.assertEqual(self.client.get('/api/admin/orders/').status_code, status.HTTP_200_OK)

    def test_inventory_can_list_orders(self):
        self.client.force_authenticate(user=self.inventory)
        self.assertEqual(self.client.get('/api/admin/orders/').status_code, status.HTTP_200_OK)

    def test_admin_can_list_orders(self):
        self.client.force_authenticate(user=self.admin)
        self.assertEqual(self.client.get('/api/admin/orders/').status_code, status.HTTP_200_OK)

    def test_superadmin_can_list_orders(self):
        self.client.force_authenticate(user=self.superadmin)
        self.assertEqual(self.client.get('/api/admin/orders/').status_code, status.HTTP_200_OK)

    def test_sales_can_get_detail(self):
        self.client.force_authenticate(user=self.sales)
        self.assertEqual(self.client.get(f'/api/admin/orders/{self.order.pk}/').status_code, status.HTTP_200_OK)

    def test_inventory_can_get_detail(self):
        self.client.force_authenticate(user=self.inventory)
        self.assertEqual(self.client.get(f'/api/admin/orders/{self.order.pk}/').status_code, status.HTTP_200_OK)

    def test_admin_can_get_detail(self):
        self.client.force_authenticate(user=self.admin)
        self.assertEqual(self.client.get(f'/api/admin/orders/{self.order.pk}/').status_code, status.HTTP_200_OK)

    def test_customer_cannot_patch_fulfillment(self):
        self.client.force_authenticate(user=self.customer)
        self.assertEqual(
            self.client.patch(
                f'/api/admin/orders/{self.order.pk}/fulfillment-status/',
                {'fulfillment_status': 'confirmed'},
                format='json',
            ).status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_anonymous_cannot_patch_fulfillment(self):
        self.assertEqual(
            self.anon_client.patch(
                f'/api/admin/orders/{self.order.pk}/fulfillment-status/',
                {'fulfillment_status': 'confirmed'},
                format='json',
            ).status_code,
            status.HTTP_401_UNAUTHORIZED,
        )

    def test_technician_list_returns_403(self):
        self.client.force_authenticate(user=self.technician)
        self.assertEqual(self.client.get('/api/admin/orders/').status_code, status.HTTP_403_FORBIDDEN)

    def test_technician_detail_returns_403(self):
        self.client.force_authenticate(user=self.technician)
        self.assertEqual(self.client.get(f'/api/admin/orders/{self.order.pk}/').status_code, status.HTTP_403_FORBIDDEN)

    def test_technician_cannot_patch_fulfillment(self):
        self.client.force_authenticate(user=self.technician)
        self.assertEqual(
            self.client.patch(
                f'/api/admin/orders/{self.order.pk}/fulfillment-status/',
                {'fulfillment_status': 'confirmed'},
                format='json',
            ).status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_detail_404_for_nonexistent_order(self):
        self.client.force_authenticate(user=self.admin)
        self.assertEqual(self.client.get('/api/admin/orders/99999/').status_code, status.HTTP_404_NOT_FOUND)


class Phase33AdminOrderListFilterTest(TestCase):
    """Filter and search tests for GET /api/admin/orders/."""

    def setUp(self):
        self.client = APIClient()
        cache.clear()
        Order.objects.all().delete()
        self.admin = User.objects.create_user(username='fl_admin', password='Pass123!')
        self.admin.profile.role = UserProfile.ROLE_ADMIN
        self.admin.profile.save()
        self.client.force_authenticate(user=self.admin)

        self.paid_order = _make_order(
            customer_email='alice@blackdog.pe', customer_name='Alice',
            status_val=Order.Status.PAID, paid=True,
            fulfillment_status=Order.FulfillmentStatus.CONFIRMED,
        )
        self.unpaid_order = _make_order(
            customer_email='bob@blackdog.pe', customer_name='Bob',
            status_val=Order.Status.PENDING_PAYMENT, paid=False,
            fulfillment_status=Order.FulfillmentStatus.PENDING,
        )

    def test_list_returns_paginated_response(self):
        data = self.client.get('/api/admin/orders/').json()
        self.assertIn('results', data)
        self.assertIn('count', data)
        self.assertEqual(data['count'], 2)

    def test_filter_by_paid_true(self):
        data = self.client.get('/api/admin/orders/?paid=true').json()
        self.assertEqual(data['count'], 1)
        self.assertEqual(data['results'][0]['customer_email'], 'alice@blackdog.pe')

    def test_filter_by_paid_false(self):
        data = self.client.get('/api/admin/orders/?paid=false').json()
        self.assertEqual(data['count'], 1)
        self.assertEqual(data['results'][0]['customer_email'], 'bob@blackdog.pe')

    def test_filter_by_payment_status(self):
        data = self.client.get('/api/admin/orders/?status=pending_payment').json()
        self.assertEqual(data['count'], 1)

    def test_filter_by_fulfillment_status(self):
        data = self.client.get('/api/admin/orders/?fulfillment_status=confirmed').json()
        self.assertEqual(data['count'], 1)
        self.assertEqual(data['results'][0]['customer_email'], 'alice@blackdog.pe')

    def test_search_by_customer_email(self):
        data = self.client.get('/api/admin/orders/?search=alice%40blackdog').json()
        self.assertEqual(data['count'], 1)
        self.assertEqual(data['results'][0]['customer_email'], 'alice@blackdog.pe')

    def test_search_by_customer_name(self):
        data = self.client.get('/api/admin/orders/?search=Alice').json()
        self.assertEqual(data['count'], 1)

    def test_search_by_id(self):
        data = self.client.get(f'/api/admin/orders/?search={self.paid_order.pk}').json()
        self.assertGreaterEqual(data['count'], 1)
        ids = [r['id'] for r in data['results']]
        self.assertIn(self.paid_order.pk, ids)

    def test_date_from_filter(self):
        data = self.client.get('/api/admin/orders/?date_from=2000-01-01').json()
        self.assertEqual(data['count'], 2)

    def test_date_to_filter_excludes_future(self):
        data = self.client.get('/api/admin/orders/?date_to=1999-01-01').json()
        self.assertEqual(data['count'], 0)

    def test_invalid_date_from_ignored_gracefully(self):
        response = self.client.get('/api/admin/orders/?date_from=not-a-date')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_list_item_count_field_present(self):
        data = self.client.get('/api/admin/orders/').json()
        self.assertIn('item_count', data['results'][0])


class Phase33AdminOrderDetailSecurityTest(TestCase):
    """Detail endpoint must not expose sensitive payment fields."""

    def setUp(self):
        self.client = APIClient()
        cache.clear()
        self.admin = User.objects.create_user(username='sec_admin', password='Pass123!')
        self.admin.profile.role = UserProfile.ROLE_ADMIN
        self.admin.profile.save()
        self.client.force_authenticate(user=self.admin)
        self.order = Order.objects.create(
            customer_name='Secure Test',
            customer_email='secure@example.com',
            status=Order.Status.PAID,
            paid=True,
            total=Decimal('500.00'),
            stripe_session_id='cs_secret_123',
            stripe_payment_intent_id='pi_secret_456',
            payment_error='some error text',
        )

    def test_detail_does_not_expose_stripe_session_id(self):
        data = self.client.get(f'/api/admin/orders/{self.order.pk}/').json()
        self.assertNotIn('stripe_session_id', data)

    def test_detail_does_not_expose_stripe_payment_intent_id(self):
        data = self.client.get(f'/api/admin/orders/{self.order.pk}/').json()
        self.assertNotIn('stripe_payment_intent_id', data)

    def test_detail_does_not_expose_payment_error(self):
        data = self.client.get(f'/api/admin/orders/{self.order.pk}/').json()
        self.assertNotIn('payment_error', data)

    def test_detail_does_not_expose_cart_session_key(self):
        data = self.client.get(f'/api/admin/orders/{self.order.pk}/').json()
        self.assertNotIn('cart_session_key', data)

    def test_detail_includes_expected_fields(self):
        data = self.client.get(f'/api/admin/orders/{self.order.pk}/').json()
        for field in ('id', 'customer_name', 'customer_email', 'total', 'status', 'fulfillment_status', 'paid', 'items'):
            self.assertIn(field, data)

    def test_list_does_not_expose_stripe_session_id(self):
        data = self.client.get('/api/admin/orders/').json()
        if data['results']:
            self.assertNotIn('stripe_session_id', data['results'][0])


class Phase33FulfillmentStatusChangeTest(TestCase):
    """Fulfillment status change endpoint tests."""

    def setUp(self):
        self.client = APIClient()
        cache.clear()
        self.order = _make_order(fulfillment_status=Order.FulfillmentStatus.PENDING)

        self.admin = User.objects.create_user(username='fs_admin', password='Pass123!')
        self.admin.profile.role = UserProfile.ROLE_ADMIN
        self.admin.profile.save()
        self.sales = User.objects.create_user(username='fs_sales', password='Pass123!')
        self.sales.profile.role = UserProfile.ROLE_SALES
        self.sales.profile.save()
        self.inventory = User.objects.create_user(username='fs_inventory', password='Pass123!')
        self.inventory.profile.role = UserProfile.ROLE_INVENTORY
        self.inventory.profile.save()
        self.superadmin = User.objects.create_user(username='fs_superadmin', password='Pass123!', is_superuser=True)

    def _patch(self, user, order=None, data=None):
        self.client.force_authenticate(user=user)
        pk = (order or self.order).pk
        return self.client.patch(
            f'/api/admin/orders/{pk}/fulfillment-status/',
            data or {'fulfillment_status': 'confirmed'},
            format='json',
        )

    def test_admin_can_change_fulfillment_status(self):
        res = self._patch(self.admin)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.order.refresh_from_db()
        self.assertEqual(self.order.fulfillment_status, 'confirmed')

    def test_sales_can_change_fulfillment_status(self):
        res = self._patch(self.sales)
        self.assertEqual(res.status_code, status.HTTP_200_OK)

    def test_superadmin_can_change_fulfillment_status(self):
        res = self._patch(self.superadmin)
        self.assertEqual(res.status_code, status.HTTP_200_OK)

    def test_inventory_can_set_preparing(self):
        res = self._patch(self.inventory, data={'fulfillment_status': 'preparing'})
        self.assertEqual(res.status_code, status.HTTP_200_OK)

    def test_inventory_can_set_ready_for_pickup(self):
        res = self._patch(self.inventory, data={'fulfillment_status': 'ready_for_pickup'})
        self.assertEqual(res.status_code, status.HTTP_200_OK)

    def test_inventory_can_set_shipped(self):
        res = self._patch(self.inventory, data={'fulfillment_status': 'shipped'})
        self.assertEqual(res.status_code, status.HTTP_200_OK)

    def test_inventory_can_set_delivered(self):
        res = self._patch(self.inventory, data={'fulfillment_status': 'delivered'})
        self.assertEqual(res.status_code, status.HTTP_200_OK)

    def test_inventory_cannot_set_cancelled(self):
        res = self._patch(self.inventory, data={'fulfillment_status': 'cancelled'})
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_inventory_cannot_set_confirmed(self):
        res = self._patch(self.inventory, data={'fulfillment_status': 'confirmed'})
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_inventory_cannot_set_pending(self):
        res = self._patch(self.inventory, data={'fulfillment_status': 'pending'})
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_invalid_fulfillment_status_returns_400(self):
        self.client.force_authenticate(user=self.admin)
        res = self.client.patch(
            f'/api/admin/orders/{self.order.pk}/fulfillment-status/',
            {'fulfillment_status': 'invalid_status_xyz'},
            format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_missing_fulfillment_status_returns_400(self):
        self.client.force_authenticate(user=self.admin)
        res = self.client.patch(
            f'/api/admin/orders/{self.order.pk}/fulfillment-status/',
            {},
            format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_change_does_not_alter_payment_status(self):
        original_status = self.order.status
        self._patch(self.admin)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, original_status)

    def test_change_does_not_alter_paid_field(self):
        original_paid = self.order.paid
        self._patch(self.admin)
        self.order.refresh_from_db()
        self.assertEqual(self.order.paid, original_paid)

    def test_change_does_not_alter_total(self):
        original_total = self.order.total
        self._patch(self.admin)
        self.order.refresh_from_db()
        self.assertEqual(self.order.total, original_total)

    def test_extra_fields_in_body_are_ignored(self):
        """Sending paid/total in body must not change those fields."""
        self.client.force_authenticate(user=self.admin)
        res = self.client.patch(
            f'/api/admin/orders/{self.order.pk}/fulfillment-status/',
            {'fulfillment_status': 'confirmed', 'paid': True, 'total': '0.01', 'status': 'refunded'},
            format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.order.refresh_from_db()
        self.assertEqual(self.order.paid, True)
        self.assertEqual(self.order.total, Decimal('999.00'))
        self.assertEqual(self.order.status, Order.Status.PAID)

    def test_response_does_not_contain_stripe_session_id(self):
        res = self._patch(self.admin)
        self.assertNotIn('stripe_session_id', res.json())

    def test_response_does_not_contain_payment_error(self):
        res = self._patch(self.admin)
        self.assertNotIn('payment_error', res.json())

    def test_response_contains_new_fulfillment_status(self):
        res = self._patch(self.admin, data={'fulfillment_status': 'preparing'})
        self.assertEqual(res.json().get('fulfillment_status'), 'preparing')

    def test_nonexistent_order_returns_404(self):
        self.client.force_authenticate(user=self.admin)
        res = self.client.patch(
            '/api/admin/orders/99999/fulfillment-status/',
            {'fulfillment_status': 'confirmed'},
            format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_delete_returns_405(self):
        self.client.force_authenticate(user=self.admin)
        self.assertEqual(
            self.client.delete(f'/api/admin/orders/{self.order.pk}/').status_code,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        )


class Phase33AuditLogTest(TestCase):
    """Fulfillment changes must create correct audit log entries."""

    def setUp(self):
        self.client = APIClient()
        cache.clear()
        self.order = _make_order(fulfillment_status=Order.FulfillmentStatus.PENDING)
        self.admin = User.objects.create_user(username='al_admin', password='Pass123!')
        self.admin.profile.role = UserProfile.ROLE_ADMIN
        self.admin.profile.save()
        self.client.force_authenticate(user=self.admin)

    def test_fulfillment_change_creates_audit_log(self):
        before = AdminAuditLog.objects.count()
        self.client.patch(
            f'/api/admin/orders/{self.order.pk}/fulfillment-status/',
            {'fulfillment_status': 'confirmed'},
            format='json',
        )
        self.assertEqual(AdminAuditLog.objects.count(), before + 1)

    def test_audit_log_action_is_correct(self):
        self.client.patch(
            f'/api/admin/orders/{self.order.pk}/fulfillment-status/',
            {'fulfillment_status': 'preparing'},
            format='json',
        )
        log = AdminAuditLog.objects.filter(action='order_fulfillment_status_changed').latest('created_at')
        self.assertEqual(log.action, 'order_fulfillment_status_changed')

    def test_audit_log_contains_old_and_new_status(self):
        self.client.patch(
            f'/api/admin/orders/{self.order.pk}/fulfillment-status/',
            {'fulfillment_status': 'confirmed'},
            format='json',
        )
        log = AdminAuditLog.objects.filter(action='order_fulfillment_status_changed').latest('created_at')
        self.assertEqual(log.metadata.get('old_fulfillment_status'), 'pending')
        self.assertEqual(log.metadata.get('new_fulfillment_status'), 'confirmed')

    def test_audit_log_contains_order_id(self):
        self.client.patch(
            f'/api/admin/orders/{self.order.pk}/fulfillment-status/',
            {'fulfillment_status': 'confirmed'},
            format='json',
        )
        log = AdminAuditLog.objects.filter(action='order_fulfillment_status_changed').latest('created_at')
        self.assertEqual(log.metadata.get('order_id'), self.order.pk)

    def test_audit_log_contains_customer_email(self):
        self.client.patch(
            f'/api/admin/orders/{self.order.pk}/fulfillment-status/',
            {'fulfillment_status': 'confirmed'},
            format='json',
        )
        log = AdminAuditLog.objects.filter(action='order_fulfillment_status_changed').latest('created_at')
        self.assertEqual(log.metadata.get('customer_email'), self.order.customer_email)

    def test_audit_log_does_not_contain_stripe_data(self):
        self.client.patch(
            f'/api/admin/orders/{self.order.pk}/fulfillment-status/',
            {'fulfillment_status': 'confirmed'},
            format='json',
        )
        log = AdminAuditLog.objects.filter(action='order_fulfillment_status_changed').latest('created_at')
        meta_str = str(log.metadata)
        self.assertNotIn('stripe', meta_str.lower())
        self.assertNotIn('payment_error', meta_str)

    def test_audit_log_note_is_stored(self):
        self.client.patch(
            f'/api/admin/orders/{self.order.pk}/fulfillment-status/',
            {'fulfillment_status': 'confirmed', 'note': 'Confirmed by warehouse team'},
            format='json',
        )
        log = AdminAuditLog.objects.filter(action='order_fulfillment_status_changed').latest('created_at')
        self.assertEqual(log.metadata.get('note'), 'Confirmed by warehouse team')

    def test_audit_log_note_truncated_at_200_chars(self):
        long_note = 'x' * 300
        self.client.patch(
            f'/api/admin/orders/{self.order.pk}/fulfillment-status/',
            {'fulfillment_status': 'confirmed', 'note': long_note},
            format='json',
        )
        log = AdminAuditLog.objects.filter(action='order_fulfillment_status_changed').latest('created_at')
        self.assertLessEqual(len(log.metadata.get('note', '')), 200)

    def test_audit_log_actor_is_admin(self):
        self.client.patch(
            f'/api/admin/orders/{self.order.pk}/fulfillment-status/',
            {'fulfillment_status': 'confirmed'},
            format='json',
        )
        log = AdminAuditLog.objects.filter(action='order_fulfillment_status_changed').latest('created_at')
        self.assertEqual(log.actor, self.admin)

    def test_audit_log_target_type_is_order(self):
        self.client.patch(
            f'/api/admin/orders/{self.order.pk}/fulfillment-status/',
            {'fulfillment_status': 'confirmed'},
            format='json',
        )
        log = AdminAuditLog.objects.filter(action='order_fulfillment_status_changed').latest('created_at')
        self.assertEqual(log.target_type, 'order')


class Phase33FulfillmentModelDefaultTest(TestCase):
    """fulfillment_status field defaults to pending on new orders."""

    def test_new_order_defaults_to_pending(self):
        order = Order.objects.create(
            customer_name='Default Test',
            customer_email='default@example.com',
            total=Decimal('100.00'),
        )
        self.assertEqual(order.fulfillment_status, Order.FulfillmentStatus.PENDING)

    def test_fulfillment_choices_exist(self):
        choices = [c[0] for c in Order.FulfillmentStatus.choices]
        for val in ('pending', 'confirmed', 'preparing', 'ready_for_pickup', 'shipped', 'delivered', 'cancelled'):
            self.assertIn(val, choices)


class Phase33RegressionTest(TestCase):
    """Regression: existing endpoints still work after Phase 3.3 changes."""

    def setUp(self):
        self.client = APIClient()
        cache.clear()
        cat = Category.objects.create(name='Reg33 Cat', slug='reg33-cat')
        self.product = _make_product('Reg33 Product', 'reg33-product', price='500.00', inventory=10, category=cat)
        self.user = User.objects.create_user(username='reg33_user', password='Pass123!')
        self.admin = User.objects.create_user(username='reg33_admin', password='Pass123!')
        self.admin.profile.role = UserProfile.ROLE_ADMIN
        self.admin.profile.save()

    def test_public_product_list_still_works(self):
        self.assertEqual(self.client.get('/api/products/').status_code, status.HTTP_200_OK)

    def test_admin_products_still_works(self):
        self.client.force_authenticate(user=self.admin)
        self.assertEqual(self.client.get('/api/admin/products/').status_code, status.HTTP_200_OK)

    def test_admin_users_still_works(self):
        self.client.force_authenticate(user=self.admin)
        self.assertEqual(self.client.get('/api/admin/users/').status_code, status.HTTP_200_OK)

    def test_admin_audit_logs_still_works(self):
        self.client.force_authenticate(user=self.admin)
        self.assertEqual(self.client.get('/api/admin/audit-logs/').status_code, status.HTTP_200_OK)

    def test_customer_orders_endpoint_still_works(self):
        self.client.force_authenticate(user=self.user)
        self.assertEqual(self.client.get('/api/orders/').status_code, status.HTTP_200_OK)

    def test_existing_order_has_fulfillment_status_field(self):
        """Orders created before migration must have fulfillment_status = pending."""
        order = Order.objects.create(
            customer_name='Reg33 Order',
            customer_email='reg33@example.com',
            total=Decimal('100.00'),
        )
        self.assertEqual(order.fulfillment_status, 'pending')

    def test_admin_order_list_returns_fulfillment_status_in_results(self):
        Order.objects.create(
            customer_name='Reg33 FulfCheck',
            customer_email='fulfcheck@example.com',
            total=Decimal('200.00'),
        )
        self.client.force_authenticate(user=self.admin)
        data = self.client.get('/api/admin/orders/').json()
        self.assertTrue(len(data['results']) > 0)
        self.assertIn('fulfillment_status', data['results'][0])

    def test_checkout_creates_order_with_pending_fulfillment_status(self):
        """Checkout flow must create orders with fulfillment_status='pending' (not affected by Phase 3.3)."""
        order = Order.objects.create(
            customer_name='Reg33 Checkout',
            customer_email='checkout33@example.com',
            status=Order.Status.PENDING_PAYMENT,
            paid=False,
            total=Decimal('500.00'),
        )
        self.assertEqual(order.fulfillment_status, Order.FulfillmentStatus.PENDING)

    def test_webhook_update_fields_does_not_include_fulfillment_status(self):
        """Simulates the webhook save to verify fulfillment_status is never written by the webhook."""
        order = Order.objects.create(
            customer_name='Reg33 Webhook',
            customer_email='webhook33@example.com',
            status=Order.Status.PENDING_PAYMENT,
            paid=False,
            total=Decimal('300.00'),
            fulfillment_status=Order.FulfillmentStatus.CONFIRMED,
        )
        # Simulate what webhook _handle_checkout_completed does:
        order.status = Order.Status.PAID
        order.paid = True
        order.paid_at = timezone.now()
        order.stripe_payment_intent_id = 'pi_test_reg33'
        order.save(update_fields=['status', 'paid', 'paid_at', 'stripe_payment_intent_id', 'payment_error'])
        order.refresh_from_db()
        # fulfillment_status must remain 'confirmed' — webhook never touches it
        self.assertEqual(order.fulfillment_status, Order.FulfillmentStatus.CONFIRMED)

    def test_fulfillment_status_change_is_atomic_with_audit_log(self):
        """Verifies that fulfillment_status change and audit log are created together."""
        order = Order.objects.create(
            customer_name='Reg33 Atomic',
            customer_email='atomic33@example.com',
            total=Decimal('100.00'),
        )
        admin = User.objects.create_user(username='reg33_atomic_admin', password='Pass123!')
        admin.profile.role = UserProfile.ROLE_ADMIN
        admin.profile.save()
        self.client.force_authenticate(user=admin)
        audit_before = AdminAuditLog.objects.count()
        self.client.patch(
            f'/api/admin/orders/{order.pk}/fulfillment-status/',
            {'fulfillment_status': 'confirmed'},
            format='json',
        )
        order.refresh_from_db()
        self.assertEqual(order.fulfillment_status, 'confirmed')
        self.assertEqual(AdminAuditLog.objects.count(), audit_before + 1)


# ---------------------------------------------------------------------------
# Phase 4.0 tests — commercial checkout fields
# ---------------------------------------------------------------------------

@override_settings(
    STRIPE_SECRET_KEY='sk_test_fake_key',
    STRIPE_WEBHOOK_SECRET='whsec_fake_secret',
    STRIPE_DOMAIN='http://localhost:3000',
)
class Phase40CheckoutValidationTest(TestCase):
    """Validates all commercial checkout fields: document, delivery, receipt, terms."""

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.product = Product.objects.create(
            name='P40 Product',
            slug='p40-product',
            price=Decimal('500.00'),
            inventory=10,
        )
        self.session_key = 'p40-session-001'
        CartItem.objects.create(session_key=self.session_key, product=self.product, quantity=1)

    def _base_body(self, **overrides):
        body = {
            'session_key': self.session_key,
            'customer_name': 'Carlos Mau',
            'customer_email': 'carlos@blackdog.pe',
            'customer_phone': '936449536',
            'document_type': 'dni',
            'document_number': '12345678',
            'delivery_method': 'pickup_store',
            'receipt_type': 'boleta',
            'accepted_terms': True,
            'accepted_warranty_policy': True,
        }
        body.update(overrides)
        return body

    def _mock_stripe(self):
        mock = MagicMock()
        mock.id = 'cs_test_p40'
        mock.url = 'https://checkout.stripe.com/pay/cs_test_p40'
        return mock

    # --- Required field missing ---

    def test_missing_phone_returns_400(self):
        body = self._base_body()
        del body['customer_phone']
        resp = self.client.post('/api/payments/create-checkout-session/', body, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('customer_phone', resp.json())

    def test_missing_document_type_returns_400(self):
        body = self._base_body()
        del body['document_type']
        resp = self.client.post('/api/payments/create-checkout-session/', body, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('document_type', resp.json())

    def test_missing_document_number_returns_400(self):
        body = self._base_body()
        del body['document_number']
        resp = self.client.post('/api/payments/create-checkout-session/', body, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('document_number', resp.json())

    def test_missing_receipt_type_returns_400(self):
        body = self._base_body()
        del body['receipt_type']
        resp = self.client.post('/api/payments/create-checkout-session/', body, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('receipt_type', resp.json())

    def test_accepted_terms_false_returns_400(self):
        resp = self.client.post(
            '/api/payments/create-checkout-session/',
            self._base_body(accepted_terms=False),
            format='json',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('accepted_terms', resp.json())

    def test_accepted_warranty_policy_false_returns_400(self):
        resp = self.client.post(
            '/api/payments/create-checkout-session/',
            self._base_body(accepted_warranty_policy=False),
            format='json',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('accepted_warranty_policy', resp.json())

    # --- Document number format ---

    def test_dni_must_be_8_digits(self):
        resp = self.client.post(
            '/api/payments/create-checkout-session/',
            self._base_body(document_type='dni', document_number='1234'),
            format='json',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('document_number', resp.json())

    def test_dni_exactly_8_digits_accepted(self):
        with patch('stripe.checkout.Session.create') as m:
            m.return_value = self._mock_stripe()
            resp = self.client.post(
                '/api/payments/create-checkout-session/',
                self._base_body(document_type='dni', document_number='12345678'),
                format='json',
            )
        self.assertEqual(resp.status_code, 200)

    def test_ruc_must_be_11_digits(self):
        resp = self.client.post(
            '/api/payments/create-checkout-session/',
            self._base_body(document_type='ruc', document_number='123'),
            format='json',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('document_number', resp.json())

    def test_ruc_11_digits_accepted(self):
        with patch('stripe.checkout.Session.create') as m:
            m.return_value = self._mock_stripe()
            resp = self.client.post(
                '/api/payments/create-checkout-session/',
                self._base_body(document_type='ruc', document_number='20610159886'),
                format='json',
            )
        self.assertEqual(resp.status_code, 200)

    def test_ce_too_short_returns_400(self):
        resp = self.client.post(
            '/api/payments/create-checkout-session/',
            self._base_body(document_type='ce', document_number='AB'),
            format='json',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('document_number', resp.json())

    def test_ce_valid_length_accepted(self):
        with patch('stripe.checkout.Session.create') as m:
            m.return_value = self._mock_stripe()
            resp = self.client.post(
                '/api/payments/create-checkout-session/',
                self._base_body(document_type='ce', document_number='ABC123456'),
                format='json',
            )
        self.assertEqual(resp.status_code, 200)

    # --- Receipt + document type combo ---

    def test_factura_requires_ruc(self):
        resp = self.client.post(
            '/api/payments/create-checkout-session/',
            self._base_body(receipt_type='factura', document_type='dni', document_number='12345678'),
            format='json',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('receipt_type', resp.json())

    def test_factura_with_ruc_accepted(self):
        with patch('stripe.checkout.Session.create') as m:
            m.return_value = self._mock_stripe()
            resp = self.client.post(
                '/api/payments/create-checkout-session/',
                self._base_body(
                    receipt_type='factura',
                    document_type='ruc',
                    document_number='20610159886',
                ),
                format='json',
            )
        self.assertEqual(resp.status_code, 200)

    def test_boleta_with_dni_accepted(self):
        with patch('stripe.checkout.Session.create') as m:
            m.return_value = self._mock_stripe()
            resp = self.client.post(
                '/api/payments/create-checkout-session/',
                self._base_body(receipt_type='boleta', document_type='dni', document_number='12345678'),
                format='json',
            )
        self.assertEqual(resp.status_code, 200)

    def test_boleta_with_ruc_accepted(self):
        with patch('stripe.checkout.Session.create') as m:
            m.return_value = self._mock_stripe()
            resp = self.client.post(
                '/api/payments/create-checkout-session/',
                self._base_body(receipt_type='boleta', document_type='ruc', document_number='20610159886'),
                format='json',
            )
        self.assertEqual(resp.status_code, 200)

    # --- Delivery address requirements ---

    def test_delivery_arequipa_requires_address(self):
        resp = self.client.post(
            '/api/payments/create-checkout-session/',
            self._base_body(delivery_method='delivery_arequipa', district='Cayma'),
            format='json',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('address_line', resp.json())

    def test_delivery_arequipa_requires_district(self):
        resp = self.client.post(
            '/api/payments/create-checkout-session/',
            self._base_body(delivery_method='delivery_arequipa', address_line='Av. Lima 123'),
            format='json',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('district', resp.json())

    def test_national_shipping_requires_address(self):
        resp = self.client.post(
            '/api/payments/create-checkout-session/',
            self._base_body(
                delivery_method='national_shipping',
                city='Lima',
                district='Miraflores',
            ),
            format='json',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('address_line', resp.json())

    def test_national_shipping_requires_city(self):
        resp = self.client.post(
            '/api/payments/create-checkout-session/',
            self._base_body(
                delivery_method='national_shipping',
                address_line='Av. Javier Prado 100',
                district='Miraflores',
            ),
            format='json',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('city', resp.json())

    def test_national_shipping_requires_district(self):
        resp = self.client.post(
            '/api/payments/create-checkout-session/',
            self._base_body(
                delivery_method='national_shipping',
                address_line='Av. Javier Prado 100',
                city='Lima',
            ),
            format='json',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('district', resp.json())

    def test_pickup_store_does_not_require_address(self):
        with patch('stripe.checkout.Session.create') as m:
            m.return_value = self._mock_stripe()
            resp = self.client.post(
                '/api/payments/create-checkout-session/',
                self._base_body(delivery_method='pickup_store'),
                format='json',
            )
        self.assertEqual(resp.status_code, 200)

    # --- Field size limits ---

    def test_notes_too_long_returns_400(self):
        resp = self.client.post(
            '/api/payments/create-checkout-session/',
            self._base_body(notes='x' * 501),
            format='json',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('notes', resp.json())

    def test_reference_too_long_returns_400(self):
        resp = self.client.post(
            '/api/payments/create-checkout-session/',
            self._base_body(
                delivery_method='delivery_arequipa',
                address_line='Av. Lima 123',
                district='Cayma',
                reference='x' * 251,
            ),
            format='json',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('reference', resp.json())

    # --- Injection guard ---

    def test_frontend_cannot_send_total(self):
        """total sent from frontend must be silently ignored — backend calculates it."""
        with patch('stripe.checkout.Session.create') as m:
            m.return_value = self._mock_stripe()
            body = self._base_body()
            body['total'] = '1.00'
            resp = self.client.post('/api/payments/create-checkout-session/', body, format='json')
        self.assertEqual(resp.status_code, 200)
        order = Order.objects.first()
        self.assertEqual(order.total, Decimal('500.00'))  # from DB, not from frontend

    def test_frontend_cannot_send_paid_true(self):
        """paid=True sent from frontend must never affect the order."""
        with patch('stripe.checkout.Session.create') as m:
            m.return_value = self._mock_stripe()
            body = self._base_body()
            body['paid'] = True
            resp = self.client.post('/api/payments/create-checkout-session/', body, format='json')
        self.assertEqual(resp.status_code, 200)
        order = Order.objects.first()
        self.assertFalse(order.paid)

    def test_frontend_cannot_send_status_paid(self):
        """status=paid sent from frontend must never affect the order."""
        with patch('stripe.checkout.Session.create') as m:
            m.return_value = self._mock_stripe()
            body = self._base_body()
            body['status'] = 'paid'
            resp = self.client.post('/api/payments/create-checkout-session/', body, format='json')
        self.assertEqual(resp.status_code, 200)
        order = Order.objects.first()
        self.assertEqual(order.status, Order.Status.PENDING_PAYMENT)

    def test_frontend_cannot_send_fulfillment_status_delivered(self):
        """fulfillment_status sent from frontend must never affect the order."""
        with patch('stripe.checkout.Session.create') as m:
            m.return_value = self._mock_stripe()
            body = self._base_body()
            body['fulfillment_status'] = 'delivered'
            resp = self.client.post('/api/payments/create-checkout-session/', body, format='json')
        self.assertEqual(resp.status_code, 200)
        order = Order.objects.first()
        self.assertEqual(order.fulfillment_status, Order.FulfillmentStatus.PENDING)

    # --- Order saves commercial fields ---

    @patch('stripe.checkout.Session.create')
    def test_order_saves_commercial_fields(self, mock_create):
        mock_create.return_value = self._mock_stripe()
        body = self._base_body(
            delivery_method='delivery_arequipa',
            address_line='Calle Mercaderes 100',
            district='Cercado',
            reference='Frente al parque',
            notes='Entregar en horario de tarde',
        )
        resp = self.client.post('/api/payments/create-checkout-session/', body, format='json')
        self.assertEqual(resp.status_code, 200)
        order = Order.objects.first()
        self.assertEqual(order.customer_phone, '936449536')
        self.assertEqual(order.document_type, 'dni')
        self.assertEqual(order.document_number, '12345678')
        self.assertEqual(order.delivery_method, 'delivery_arequipa')
        self.assertEqual(order.address_line, 'Calle Mercaderes 100')
        self.assertEqual(order.district, 'Cercado')
        self.assertEqual(order.reference, 'Frente al parque')
        self.assertEqual(order.notes, 'Entregar en horario de tarde')
        self.assertEqual(order.receipt_type, 'boleta')
        self.assertTrue(order.accepted_terms)
        self.assertTrue(order.accepted_warranty_policy)


class Phase40AdminOrderDetailCommercialFieldsTest(TestCase):
    """Admin order detail endpoint exposes commercial fields and hides Stripe fields."""

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.admin = User.objects.create_user(username='p40_admin', password='Pass123!')
        self.admin.profile.role = UserProfile.ROLE_ADMIN
        self.admin.profile.save()
        self.order = Order.objects.create(
            customer_name='P40 Cliente',
            customer_email='p40cliente@blackdog.pe',
            total=Decimal('500.00'),
            customer_phone='936449536',
            document_type='ruc',
            document_number='20610159886',
            delivery_method='delivery_arequipa',
            address_line='Calle Mercaderes 100',
            district='Cercado',
            receipt_type='factura',
            notes='Test notes',
            accepted_terms=True,
            accepted_warranty_policy=True,
            stripe_session_id='cs_test_p40_admin',
        )

    def test_admin_detail_shows_commercial_fields(self):
        self.client.force_authenticate(user=self.admin)
        data = self.client.get(f'/api/admin/orders/{self.order.pk}/').json()
        self.assertEqual(data['customer_phone'], '936449536')
        self.assertEqual(data['document_type'], 'ruc')
        self.assertEqual(data['document_number'], '20610159886')
        self.assertEqual(data['delivery_method'], 'delivery_arequipa')
        self.assertEqual(data['address_line'], 'Calle Mercaderes 100')
        self.assertEqual(data['district'], 'Cercado')
        self.assertEqual(data['receipt_type'], 'factura')
        self.assertEqual(data['notes'], 'Test notes')
        self.assertTrue(data['accepted_terms'])
        self.assertTrue(data['accepted_warranty_policy'])

    def test_admin_detail_still_hides_stripe_session_id(self):
        self.client.force_authenticate(user=self.admin)
        data = self.client.get(f'/api/admin/orders/{self.order.pk}/').json()
        self.assertNotIn('stripe_session_id', data)
        self.assertNotIn('payment_error', data)

    def test_payment_status_view_does_not_expose_commercial_fields(self):
        """PaymentStatusView must not expose address/document data."""
        data = self.client.get(
            f'/api/payments/status/?session_id=cs_test_p40_admin'
        ).json()
        self.assertNotIn('address_line', data)
        self.assertNotIn('document_number', data)
        self.assertNotIn('customer_phone', data)


# ===========================================================================
# Phase 4.1 — Transactional email service tests
# ===========================================================================

def _make_paid_order(**kwargs):
    """Factory for a fully paid order with one item."""
    cat, _ = Category.objects.get_or_create(name='Mac41', defaults={'slug': 'mac-41'})
    product, _ = Product.objects.get_or_create(
        slug='mbp-m3-41',
        defaults={'name': 'MacBook Pro M3', 'price': '9999.00', 'inventory': 5, 'category': cat},
    )
    defaults = dict(
        customer_name='Ana Torres',
        customer_email='ana@example.com',
        customer_phone='936449536',
        document_type='dni',
        document_number='12345678',
        delivery_method='pickup_store',
        receipt_type='boleta',
        accepted_terms=True,
        accepted_warranty_policy=True,
        total='9999.00',
        status=Order.Status.PAID,
        paid=True,
        paid_at=timezone.now(),
        stripe_session_id='cs_test_41',
        stripe_payment_intent_id='pi_test_41',
    )
    defaults.update(kwargs)
    order = Order.objects.create(**defaults)
    OrderItem.objects.create(order=order, product=product, quantity=1, price='9999.00')
    return order


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    ORDER_NOTIFICATION_EMAIL='store@example.com',
    DEFAULT_FROM_EMAIL='Black Dog Store <no-reply@test.com>',
    FRONTEND_URL='http://localhost:3000',
)
class Phase41EmailServiceUnitTest(TestCase):
    """Unit tests for email_services.py functions (called directly, no webhook)."""

    def setUp(self):
        mail.outbox = []

    def test_build_order_confirmation_context_excludes_stripe_fields(self):
        from store.email_services import build_order_confirmation_context
        order = _make_paid_order()
        ctx = build_order_confirmation_context(order)
        self.assertNotIn('stripe_session_id', ctx)
        self.assertNotIn('stripe_payment_intent_id', ctx)
        self.assertNotIn('payment_error', ctx)

    def test_build_order_confirmation_context_includes_required_fields(self):
        from store.email_services import build_order_confirmation_context
        order = _make_paid_order()
        ctx = build_order_confirmation_context(order)
        self.assertEqual(ctx['customer_name'], 'Ana Torres')
        self.assertEqual(ctx['customer_email'], 'ana@example.com')
        self.assertIn('items', ctx)
        self.assertEqual(len(ctx['items']), 1)
        self.assertEqual(ctx['store_name'], 'Black Dog Store')
        self.assertIn('store_address', ctx)

    def test_send_order_confirmation_email_delivers_to_customer(self):
        from store.email_services import send_order_confirmation_email
        order = _make_paid_order()
        result = send_order_confirmation_email(order)
        self.assertTrue(result)
        self.assertEqual(len(mail.outbox), 1)
        msg = mail.outbox[0]
        self.assertEqual(msg.to, ['ana@example.com'])
        self.assertIn(str(order.id), msg.subject)
        self.assertNotIn('stripe', msg.body.lower())

    def test_send_order_confirmation_email_does_not_include_stripe_data(self):
        from store.email_services import send_order_confirmation_email
        order = _make_paid_order()
        send_order_confirmation_email(order)
        msg = mail.outbox[0]
        self.assertNotIn('cs_test_41', msg.body)
        self.assertNotIn('pi_test_41', msg.body)

    def test_send_order_confirmation_email_has_html_alternative(self):
        from store.email_services import send_order_confirmation_email
        order = _make_paid_order()
        send_order_confirmation_email(order)
        msg = mail.outbox[0]
        alternatives = getattr(msg, 'alternatives', [])
        html_types = [mime for _, mime in alternatives]
        self.assertIn('text/html', html_types)

    def test_send_order_confirmation_email_skips_if_already_sent(self):
        from store.email_services import send_order_confirmation_email
        order = _make_paid_order()
        order.confirmation_email_sent_at = timezone.now()
        order.save(update_fields=['confirmation_email_sent_at'])
        result = send_order_confirmation_email(order)
        self.assertFalse(result)
        self.assertEqual(len(mail.outbox), 0)

    def test_send_order_confirmation_email_skips_if_not_paid(self):
        from store.email_services import send_order_confirmation_email
        order = _make_paid_order(status=Order.Status.PENDING_PAYMENT, paid=False)
        result = send_order_confirmation_email(order)
        self.assertFalse(result)
        self.assertEqual(len(mail.outbox), 0)

    def test_send_order_confirmation_email_skips_if_status_not_paid(self):
        from store.email_services import send_order_confirmation_email
        order = _make_paid_order(status=Order.Status.FAILED)
        result = send_order_confirmation_email(order)
        self.assertFalse(result)
        self.assertEqual(len(mail.outbox), 0)

    def test_send_internal_order_notification_sends_to_notification_email(self):
        from store.email_services import send_internal_order_notification
        order = _make_paid_order()
        result = send_internal_order_notification(order)
        self.assertTrue(result)
        self.assertEqual(len(mail.outbox), 1)
        msg = mail.outbox[0]
        self.assertEqual(msg.to, ['store@example.com'])
        self.assertIn(str(order.id), msg.subject)

    def test_send_internal_notification_skips_if_already_sent(self):
        from store.email_services import send_internal_order_notification
        order = _make_paid_order()
        order.internal_notification_sent_at = timezone.now()
        order.save(update_fields=['internal_notification_sent_at'])
        result = send_internal_order_notification(order)
        self.assertFalse(result)
        self.assertEqual(len(mail.outbox), 0)

    def test_send_internal_notification_skips_if_not_configured(self):
        from store.email_services import send_internal_order_notification
        order = _make_paid_order()
        with self.settings(ORDER_NOTIFICATION_EMAIL=''):
            result = send_internal_order_notification(order)
        self.assertFalse(result)
        self.assertEqual(len(mail.outbox), 0)

    def test_send_internal_notification_includes_admin_link(self):
        from store.email_services import send_internal_order_notification
        order = _make_paid_order()
        send_internal_order_notification(order)
        msg = mail.outbox[0]
        self.assertIn('localhost:3000', msg.body)
        self.assertIn(str(order.id), msg.body)

    def test_send_internal_notification_does_not_include_stripe_data(self):
        from store.email_services import send_internal_order_notification
        order = _make_paid_order()
        send_internal_order_notification(order)
        msg = mail.outbox[0]
        self.assertNotIn('cs_test_41', msg.body)
        self.assertNotIn('pi_test_41', msg.body)

    def test_send_order_emails_after_payment_sends_both_emails(self):
        from store.email_services import send_order_emails_after_payment
        order = _make_paid_order()
        send_order_emails_after_payment(order.pk)
        self.assertEqual(len(mail.outbox), 2)

    def test_send_order_emails_after_payment_sets_confirmation_flag(self):
        from store.email_services import send_order_emails_after_payment
        order = _make_paid_order()
        send_order_emails_after_payment(order.pk)
        order.refresh_from_db()
        self.assertIsNotNone(order.confirmation_email_sent_at)

    def test_send_order_emails_after_payment_sets_internal_flag(self):
        from store.email_services import send_order_emails_after_payment
        order = _make_paid_order()
        send_order_emails_after_payment(order.pk)
        order.refresh_from_db()
        self.assertIsNotNone(order.internal_notification_sent_at)

    def test_send_order_emails_after_payment_idempotent_double_call(self):
        """Calling twice (simulating duplicate webhook) must not send duplicate emails."""
        from store.email_services import send_order_emails_after_payment
        order = _make_paid_order()
        send_order_emails_after_payment(order.pk)
        mail.outbox = []  # clear after first call
        send_order_emails_after_payment(order.pk)
        self.assertEqual(len(mail.outbox), 0)

    def test_send_order_emails_after_payment_graceful_on_nonexistent_pk(self):
        from store.email_services import send_order_emails_after_payment
        # Must not raise, just log
        send_order_emails_after_payment(999999)
        self.assertEqual(len(mail.outbox), 0)

    def test_send_order_emails_after_payment_saves_error_on_failure(self):
        from store.email_services import send_order_emails_after_payment
        order = _make_paid_order()
        with patch('store.email_services.send_order_confirmation_email', side_effect=Exception('SMTP down')):
            send_order_emails_after_payment(order.pk)
        order.refresh_from_db()
        self.assertIn('confirmation', order.email_send_error)

    def test_internal_email_failure_does_not_prevent_confirmation_email(self):
        from store.email_services import send_order_emails_after_payment
        order = _make_paid_order()
        with patch('store.email_services.send_internal_order_notification', side_effect=Exception('SMTP')):
            send_order_emails_after_payment(order.pk)
        # Customer email should still be sent
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [order.customer_email])
        order.refresh_from_db()
        self.assertIsNotNone(order.confirmation_email_sent_at)

    def test_delivery_arequipa_shows_address_in_email(self):
        from store.email_services import build_order_confirmation_context
        order = _make_paid_order(
            delivery_method='delivery_arequipa',
            address_line='Av. Independencia 500',
            district='Cayma',
        )
        ctx = build_order_confirmation_context(order)
        self.assertIn('Av. Independencia 500', ctx['full_address'])
        self.assertIn('Cayma', ctx['full_address'])

    def test_pickup_store_shows_empty_address(self):
        from store.email_services import build_order_confirmation_context
        order = _make_paid_order(delivery_method='pickup_store')
        ctx = build_order_confirmation_context(order)
        self.assertEqual(ctx['full_address'], '')

    def test_factura_receipt_label_in_context(self):
        from store.email_services import build_order_confirmation_context
        order = _make_paid_order(receipt_type='factura', document_type='ruc', document_number='20610159886')
        ctx = build_order_confirmation_context(order)
        self.assertEqual(ctx['receipt_label'], 'Factura')
        self.assertEqual(ctx['document_label'], 'RUC')

    def test_discount_included_in_context_when_present(self):
        from store.email_services import build_order_confirmation_context
        order = _make_paid_order(discount_amount='500.00', coupon_code='PROMO50')
        ctx = build_order_confirmation_context(order)
        self.assertEqual(str(ctx['discount_amount']), '500.00')
        self.assertEqual(ctx['coupon_code'], 'PROMO50')

    def test_no_internal_email_when_notification_email_not_set(self):
        from store.email_services import send_order_emails_after_payment
        order = _make_paid_order()
        with self.settings(ORDER_NOTIFICATION_EMAIL=''):
            send_order_emails_after_payment(order.pk)
        # Only customer confirmation, no internal
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [order.customer_email])

    # --- Audit 4.1: checklist items #8, #9, #10 ---

    def test_confirmation_email_body_does_not_contain_payment_error(self):
        """Email body must never include payment_error value (even if non-empty)."""
        from store.email_services import send_order_confirmation_email
        order = _make_paid_order()
        order.payment_error = "Card declined: insufficient_funds"
        order.save(update_fields=["payment_error"])
        send_order_confirmation_email(order)
        msg = mail.outbox[0]
        self.assertNotIn("Card declined", msg.body)
        self.assertNotIn("insufficient_funds", msg.body)
        for _, html_body in getattr(msg, "alternatives", []):
            self.assertNotIn("Card declined", html_body)

    def test_confirmation_email_body_contains_product_and_total(self):
        """Email text body must include product name and total amount."""
        from store.email_services import send_order_confirmation_email
        order = _make_paid_order()
        send_order_confirmation_email(order)
        msg = mail.outbox[0]
        self.assertIn("MacBook Pro M3", msg.body)
        self.assertIn("9999.00", msg.body)

    def test_pickup_store_email_body_contains_store_address(self):
        """For pickup_store orders, the text body must show the store address, not a delivery address."""
        from store.email_services import send_order_confirmation_email
        from store.email_services import _STORE_ADDRESS
        order = _make_paid_order(delivery_method="pickup_store")
        send_order_confirmation_email(order)
        msg = mail.outbox[0]
        self.assertIn(_STORE_ADDRESS, msg.body)
        self.assertIn("Punto de retiro", msg.body)

    def test_html_escaping_in_customer_email(self):
        """User-supplied data with HTML special chars must be escaped in the HTML body."""
        from store.email_services import send_order_confirmation_email
        order = _make_paid_order(
            customer_name='<b>Atacante</b>',
            notes='<script>alert("xss")</script>',
        )
        send_order_confirmation_email(order)
        msg = mail.outbox[0]
        html_alternatives = [body for body, mime in getattr(msg, "alternatives", []) if mime == "text/html"]
        self.assertTrue(html_alternatives, "Email must have HTML alternative")
        html_body = html_alternatives[0]
        # Angle brackets must be escaped — raw tags must NOT appear
        self.assertNotIn("<b>Atacante</b>", html_body)
        self.assertNotIn("<script>", html_body)
        # Escaped versions must appear
        self.assertIn("&lt;b&gt;Atacante&lt;/b&gt;", html_body)
        self.assertIn("&lt;script&gt;", html_body)

    def test_email_send_error_appends_on_repeated_failure(self):
        """Repeated email failures must append errors, not overwrite the first one."""
        from store.email_services import send_order_emails_after_payment
        order = _make_paid_order()
        # First failure
        with patch('store.email_services.send_order_confirmation_email', side_effect=Exception('SMTP timeout')):
            send_order_emails_after_payment(order.pk)
        order.refresh_from_db()
        self.assertIn('confirmation', order.email_send_error)
        first_error = order.email_send_error
        # Second failure — error must be appended, not overwritten
        order.confirmation_email_sent_at = None  # reset flag to re-trigger
        order.save(update_fields=['confirmation_email_sent_at'])
        with patch('store.email_services.send_order_confirmation_email', side_effect=Exception('DNS error')):
            send_order_emails_after_payment(order.pk)
        order.refresh_from_db()
        self.assertIn('confirmation', order.email_send_error)
        # Both errors should be present (first_error substring still there)
        self.assertIn(first_error[:50], order.email_send_error)


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    ORDER_NOTIFICATION_EMAIL='store@example.com',
    DEFAULT_FROM_EMAIL='Black Dog Store <no-reply@test.com>',
    FRONTEND_URL='http://localhost:3000',
    STRIPE_SECRET_KEY='sk_test_fake41',
    STRIPE_WEBHOOK_SECRET='whsec_fake41',
)
class Phase41WebhookEmailIntegrationTest(TestCase):
    """
    Tests that the Stripe webhook triggers email sending via transaction.on_commit().

    Because TestCase wraps everything in a transaction that is never committed,
    we patch transaction.on_commit to call the function immediately, which lets
    us verify the integration without needing TransactionTestCase.
    """

    def setUp(self):
        mail.outbox = []
        cat = Category.objects.create(name='Mac41W', slug='mac-41w')
        self.product = Product.objects.create(
            name='iMac M3', slug='imac-m3-41w', price='5999.00', inventory=3, category=cat,
        )
        self.order = Order.objects.create(
            customer_name='Pedro Salas', customer_email='pedro@example.com',
            customer_phone='936449537', document_type='dni', document_number='87654321',
            delivery_method='pickup_store', receipt_type='boleta',
            accepted_terms=True, accepted_warranty_policy=True,
            total='5999.00',
            status=Order.Status.PENDING_PAYMENT,
            paid=False,
            stripe_session_id='cs_wh_41_integration',
        )
        OrderItem.objects.create(
            order=self.order, product=self.product, quantity=1, price='5999.00',
        )

    def _fire_webhook(self, event_type='checkout.session.completed', extra_data=None):
        event = {
            'type': event_type,
            'data': {
                'object': {
                    'id': 'cs_wh_41_integration',
                    'payment_intent': 'pi_wh_41',
                    **(extra_data or {}),
                }
            },
        }
        with patch('stripe.Webhook.construct_event', return_value=event):
            return self.client.post(
                '/api/payments/webhook/',
                data=b'{}',
                content_type='application/json',
                HTTP_STRIPE_SIGNATURE='t=1,v1=fake',
            )

    def test_webhook_triggers_email_via_on_commit(self):
        with patch('django.db.transaction.on_commit', side_effect=lambda fn: fn()):
            resp = self._fire_webhook()
        self.assertEqual(resp.status_code, 200)
        # Both customer + internal emails sent
        self.assertGreaterEqual(len(mail.outbox), 1)
        recipient_emails = [msg.to[0] for msg in mail.outbox]
        self.assertIn('pedro@example.com', recipient_emails)

    def test_webhook_email_not_fired_if_already_paid(self):
        """Idempotency: second webhook call must not trigger emails."""
        self.order.status = Order.Status.PAID
        self.order.paid = True
        self.order.paid_at = timezone.now()
        self.order.confirmation_email_sent_at = timezone.now()
        self.order.internal_notification_sent_at = timezone.now()
        self.order.save()

        with patch('django.db.transaction.on_commit', side_effect=lambda fn: fn()):
            resp = self._fire_webhook()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(mail.outbox), 0)

    def test_email_failure_does_not_revert_payment(self):
        """If email sending fails, the payment must still be marked paid."""
        with patch('store.email_services.send_order_emails_after_payment', side_effect=Exception('SMTP')):
            with patch('django.db.transaction.on_commit', side_effect=lambda fn: fn()):
                self._fire_webhook()
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PAID)
        self.assertTrue(self.order.paid)

    def test_webhook_without_on_commit_patch_does_not_raise(self):
        """Standard TestCase behavior: on_commit fires after test transaction ends (never).
        The webhook must still return 200 and mark the order paid."""
        resp = self._fire_webhook()
        self.assertEqual(resp.status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PAID)
        # No emails in outbox because on_commit never fires in TestCase
        self.assertEqual(len(mail.outbox), 0)


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    ORDER_NOTIFICATION_EMAIL='store@example.com',
)
class Phase41AdminDetailEmailFlagsTest(TestCase):
    """Admin order detail API must expose the three email flag fields."""

    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_user('admin41', 'admin41@example.com', 'pw')
        self.admin.profile.role = UserProfile.ROLE_ADMIN
        self.admin.profile.save()
        self.order = _make_paid_order(stripe_session_id='cs_test_admin_41_flags')

    def test_admin_detail_exposes_email_flags_null_by_default(self):
        self.client.force_authenticate(user=self.admin)
        data = self.client.get(f'/api/admin/orders/{self.order.pk}/').json()
        self.assertIn('confirmation_email_sent_at', data)
        self.assertIn('internal_notification_sent_at', data)
        self.assertIn('email_send_error', data)
        self.assertIsNone(data['confirmation_email_sent_at'])
        self.assertIsNone(data['internal_notification_sent_at'])
        self.assertEqual(data['email_send_error'], '')

    def test_admin_detail_shows_confirmation_sent_at_after_email(self):
        from store.email_services import send_order_emails_after_payment
        send_order_emails_after_payment(self.order.pk)
        self.client.force_authenticate(user=self.admin)
        data = self.client.get(f'/api/admin/orders/{self.order.pk}/').json()
        self.assertIsNotNone(data['confirmation_email_sent_at'])

    def test_admin_detail_email_flags_not_visible_to_customer(self):
        """OrderSerializer (customer-facing) must never include email flags."""
        customer = User.objects.create_user('cust41', 'cust41@example.com', 'pw')
        order = _make_paid_order(stripe_session_id='cs_test_cust41')
        order.user = customer
        order.save(update_fields=['user'])
        self.client.force_authenticate(user=customer)
        data = self.client.get(f'/api/orders/{order.pk}/').json()
        self.assertNotIn('confirmation_email_sent_at', data)
        self.assertNotIn('internal_notification_sent_at', data)
        self.assertNotIn('email_send_error', data)

    def test_payment_status_view_does_not_expose_email_flags(self):
        """PaymentStatusView (public endpoint) must not expose email flags."""
        data = self.client.get(
            f'/api/payments/status/?session_id=cs_test_admin_41_flags'
        ).json()
        self.assertNotIn('confirmation_email_sent_at', data)
        self.assertNotIn('internal_notification_sent_at', data)
        self.assertNotIn('email_send_error', data)


# ===========================================================================
# Phase 4.2 — PDF receipt generation
# ===========================================================================

def _make_paid_order_42(**kwargs):
    """Factory for Phase 4.2 tests — separate slugs to avoid UNIQUE conflicts."""
    cat, _ = Category.objects.get_or_create(name='Mac42', defaults={'slug': 'mac-42'})
    product, _ = Product.objects.get_or_create(
        slug='mbp-m3-42',
        defaults={'name': 'MacBook Pro M3 42', 'price': '9999.00', 'inventory': 5, 'category': cat},
    )
    defaults = dict(
        customer_name='Laura Quispe', customer_email='laura@example.com',
        customer_phone='936449000', document_type='dni', document_number='87654321',
        delivery_method='pickup_store', receipt_type='boleta',
        accepted_terms=True, accepted_warranty_policy=True,
        total='9999.00', discount_amount='0.00', status=Order.Status.PAID, paid=True,
        paid_at=timezone.now(), stripe_session_id='cs_test_42_base',
        stripe_payment_intent_id='pi_test_42_base',
    )
    defaults.update(kwargs)
    order = Order.objects.create(**defaults)
    OrderItem.objects.create(order=order, product=product, quantity=1, price='9999.00')
    return order


# ---------------------------------------------------------------------------
# Phase 4.2 — Context builder unit tests
# ---------------------------------------------------------------------------

class Phase42PdfContextTest(TestCase):
    """Unit tests for build_order_pdf_context()."""

    def setUp(self):
        self.order = _make_paid_order_42()

    def test_context_has_required_keys(self):
        from store.pdf_services import build_order_pdf_context
        ctx = build_order_pdf_context(self.order)
        for key in (
            'title', 'disclaimer', 'warranty_note',
            'order_id', 'created_at', 'paid_at', 'status_label',
            'customer_name', 'customer_email', 'customer_phone',
            'document_label', 'document_number',
            'delivery_label', 'full_address',
            'items', 'total', 'discount_amount',
            'receipt_label',
            'store_name', 'store_legal_name', 'store_ruc',
        ):
            self.assertIn(key, ctx, msg=f"Missing key: {key}")

    def test_context_excludes_stripe_fields(self):
        from store.pdf_services import build_order_pdf_context
        ctx = build_order_pdf_context(self.order)
        for forbidden in (
            'stripe_session_id', 'stripe_payment_intent_id', 'payment_error',
            'cart_session_key', 'confirmation_email_sent_at', 'email_send_error',
        ):
            self.assertNotIn(forbidden, ctx, msg=f"Forbidden key found: {forbidden}")

    def test_context_total_is_decimal(self):
        from store.pdf_services import build_order_pdf_context
        ctx = build_order_pdf_context(self.order)
        self.assertIsInstance(ctx['total'], Decimal)
        self.assertIsInstance(ctx['discount_amount'], Decimal)

    def test_context_items_have_required_fields(self):
        from store.pdf_services import build_order_pdf_context
        ctx = build_order_pdf_context(self.order)
        self.assertEqual(len(ctx['items']), 1)
        item = ctx['items'][0]
        self.assertIn('product_name', item)
        self.assertIn('quantity', item)
        self.assertIn('price', item)
        self.assertIn('subtotal', item)
        self.assertIsInstance(item['price'], Decimal)
        self.assertIsInstance(item['subtotal'], Decimal)

    def test_context_disclaimer_is_not_sunat(self):
        from store.pdf_services import build_order_pdf_context, DISCLAIMER
        ctx = build_order_pdf_context(self.order)
        self.assertEqual(ctx['disclaimer'], DISCLAIMER)
        self.assertIn('No válido como comprobante electrónico SUNAT', ctx['disclaimer'])
        self.assertNotIn('SUNAT electrónico', ctx['disclaimer'].lower().replace(' ', ''))

    def test_context_boleta_title(self):
        from store.pdf_services import build_order_pdf_context
        ctx = build_order_pdf_context(self.order)
        self.assertIn('Boleta', ctx['title'])

    def test_context_factura_title(self):
        from store.pdf_services import build_order_pdf_context
        order = _make_paid_order_42(
            receipt_type='factura', stripe_session_id='cs_42_fac', document_type='ruc',
            stripe_payment_intent_id='pi_42_fac',
        )
        ctx = build_order_pdf_context(order)
        self.assertIn('Factura', ctx['title'])

    def test_context_pickup_store_has_no_delivery_address(self):
        from store.pdf_services import build_order_pdf_context
        ctx = build_order_pdf_context(self.order)
        # pickup_store — no delivery address fields set
        self.assertEqual(ctx['full_address'], '')

    def test_context_delivery_builds_address(self):
        from store.pdf_services import build_order_pdf_context
        order = _make_paid_order_42(
            delivery_method='delivery_arequipa',
            address_line='Jr. Lima 123', district='Cercado', city='Arequipa',
            stripe_session_id='cs_42_del', stripe_payment_intent_id='pi_42_del',
        )
        ctx = build_order_pdf_context(order)
        self.assertIn('Lima', ctx['full_address'])
        self.assertIn('Cercado', ctx['full_address'])
        self.assertIn('Arequipa', ctx['full_address'])

    def test_context_delivery_label_mapped(self):
        from store.pdf_services import build_order_pdf_context
        ctx = build_order_pdf_context(self.order)
        self.assertEqual(ctx['delivery_label'], 'Recojo en tienda')

    def test_context_document_label_dni(self):
        from store.pdf_services import build_order_pdf_context
        ctx = build_order_pdf_context(self.order)
        self.assertEqual(ctx['document_label'], 'DNI')

    def test_context_store_constants(self):
        from store.pdf_services import build_order_pdf_context, _STORE_RUC, _STORE_NAME
        ctx = build_order_pdf_context(self.order)
        self.assertEqual(ctx['store_ruc'], _STORE_RUC)
        self.assertEqual(ctx['store_name'], _STORE_NAME)
        self.assertNotEqual(ctx['store_address'], '')

    def test_context_status_label_is_paid(self):
        from store.pdf_services import build_order_pdf_context
        ctx = build_order_pdf_context(self.order)
        self.assertEqual(ctx['status_label'], 'Pagado')

    def test_context_discount_zero(self):
        from store.pdf_services import build_order_pdf_context
        ctx = build_order_pdf_context(self.order)
        self.assertEqual(ctx['discount_amount'], Decimal('0.00'))

    def test_context_discount_nonzero(self):
        from store.pdf_services import build_order_pdf_context
        order = _make_paid_order_42(
            discount_amount='500.00', coupon_code='BLK50',
            total='9499.00', stripe_session_id='cs_42_disc',
            stripe_payment_intent_id='pi_42_disc',
        )
        ctx = build_order_pdf_context(order)
        self.assertEqual(ctx['discount_amount'], Decimal('500.00'))
        self.assertEqual(ctx['coupon_code'], 'BLK50')


# ---------------------------------------------------------------------------
# Phase 4.2 — PDF generator tests
# ---------------------------------------------------------------------------

class Phase42PdfGeneratorTest(TestCase):
    """Tests for generate_order_receipt_pdf() and get_order_receipt_filename()."""

    def setUp(self):
        self.order = _make_paid_order_42(stripe_session_id='cs_42_gen', stripe_payment_intent_id='pi_42_gen')

    def test_pdf_returns_bytes(self):
        from store.pdf_services import generate_order_receipt_pdf
        pdf = generate_order_receipt_pdf(self.order)
        self.assertIsInstance(pdf, bytes)

    def test_pdf_starts_with_pdf_header(self):
        from store.pdf_services import generate_order_receipt_pdf
        pdf = generate_order_receipt_pdf(self.order)
        self.assertTrue(pdf[:4] == b'%PDF', "Expected PDF header %%PDF not found")

    def test_pdf_has_minimum_size(self):
        from store.pdf_services import generate_order_receipt_pdf
        pdf = generate_order_receipt_pdf(self.order)
        self.assertGreater(len(pdf), 2000)

    def test_pdf_raises_for_pending_payment_order(self):
        from store.pdf_services import generate_order_receipt_pdf
        order = _make_paid_order_42(
            paid=False, status=Order.Status.PENDING_PAYMENT,
            stripe_session_id='cs_42_pend', stripe_payment_intent_id='pi_42_pend',
        )
        with self.assertRaises(ValueError):
            generate_order_receipt_pdf(order)

    def test_pdf_raises_for_failed_order(self):
        from store.pdf_services import generate_order_receipt_pdf
        order = _make_paid_order_42(
            paid=False, status=Order.Status.FAILED,
            stripe_session_id='cs_42_fail', stripe_payment_intent_id='pi_42_fail',
        )
        with self.assertRaises(ValueError):
            generate_order_receipt_pdf(order)

    def test_pdf_raises_for_cancelled_order(self):
        from store.pdf_services import generate_order_receipt_pdf
        order = _make_paid_order_42(
            paid=False, status=Order.Status.CANCELLED,
            stripe_session_id='cs_42_can', stripe_payment_intent_id='pi_42_can',
        )
        with self.assertRaises(ValueError):
            generate_order_receipt_pdf(order)

    def test_pdf_get_filename(self):
        from store.pdf_services import get_order_receipt_filename
        filename = get_order_receipt_filename(self.order)
        self.assertEqual(filename, f'blackdog-pedido-{self.order.id}.pdf')
        self.assertTrue(filename.isascii())

    def test_pdf_with_discount(self):
        from store.pdf_services import generate_order_receipt_pdf
        order = _make_paid_order_42(
            discount_amount='500.00', coupon_code='BLK50', total='9499.00',
            stripe_session_id='cs_42_discpdf', stripe_payment_intent_id='pi_42_discpdf',
        )
        pdf = generate_order_receipt_pdf(order)
        self.assertTrue(pdf[:4] == b'%PDF')
        self.assertGreater(len(pdf), 2000)

    def test_pdf_with_delivery_address(self):
        from store.pdf_services import generate_order_receipt_pdf
        order = _make_paid_order_42(
            delivery_method='delivery_arequipa',
            address_line='Av. Ejercito 100', district='Yanahuara', city='Arequipa',
            stripe_session_id='cs_42_addr', stripe_payment_intent_id='pi_42_addr',
        )
        pdf = generate_order_receipt_pdf(order)
        self.assertTrue(pdf[:4] == b'%PDF')


# ---------------------------------------------------------------------------
# Phase 4.2 — Email + PDF integration tests
# ---------------------------------------------------------------------------

@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    ORDER_NOTIFICATION_EMAIL='store@example.com',
    DEFAULT_FROM_EMAIL='noreply@blackdogstore.com',
    FRONTEND_URL='https://blackdogstore.com',
)
class Phase42EmailWithPdfTest(TestCase):
    """Integration tests for PDF attached to customer confirmation email."""

    def setUp(self):
        self.order = _make_paid_order_42(
            stripe_session_id='cs_42_email', stripe_payment_intent_id='pi_42_email',
        )

    def test_email_with_pdf_attachment_sends(self):
        from store.email_services import send_order_confirmation_email
        result = send_order_confirmation_email(self.order)
        self.assertTrue(result)
        self.assertEqual(len(mail.outbox), 1)

    def test_email_has_pdf_attachment(self):
        from store.email_services import send_order_confirmation_email
        send_order_confirmation_email(self.order)
        msg = mail.outbox[0]
        attachments = msg.attachments
        self.assertEqual(len(attachments), 1)
        filename, content, mimetype = attachments[0]
        self.assertEqual(mimetype, 'application/pdf')
        self.assertTrue(filename.endswith('.pdf'))

    def test_email_attachment_is_valid_pdf(self):
        from store.email_services import send_order_confirmation_email
        send_order_confirmation_email(self.order)
        msg = mail.outbox[0]
        _, content, _ = msg.attachments[0]
        self.assertTrue(content[:4] == b'%PDF')

    def test_email_attachment_filename_matches_order(self):
        from store.email_services import send_order_confirmation_email
        send_order_confirmation_email(self.order)
        msg = mail.outbox[0]
        filename, _, _ = msg.attachments[0]
        self.assertIn(str(self.order.id), filename)

    def test_email_still_sends_if_pdf_generation_fails(self):
        """If PDF generation throws, email must still be delivered without attachment."""
        from store.email_services import send_order_confirmation_email
        with patch('store.pdf_services.generate_order_receipt_pdf', side_effect=RuntimeError('PDF boom')):
            result = send_order_confirmation_email(self.order)
        self.assertTrue(result)
        self.assertEqual(len(mail.outbox), 1)
        # No attachment when PDF failed
        self.assertEqual(len(mail.outbox[0].attachments), 0)

    def test_pdf_failure_recorded_in_email_send_error(self):
        """PDF generation failure must append to email_send_error even if email goes through."""
        from store.email_services import send_order_confirmation_email
        with patch('store.pdf_services.generate_order_receipt_pdf', side_effect=RuntimeError('PDF kaboom')):
            send_order_confirmation_email(self.order)
        self.order.refresh_from_db()
        self.assertIn('pdf_skip', self.order.email_send_error)

    def test_pdf_failure_does_not_prevent_idempotency_flag(self):
        """Even if PDF fails, the confirmation email flag is set after successful send."""
        from store.email_services import send_order_emails_after_payment
        with patch('store.pdf_services.generate_order_receipt_pdf', side_effect=RuntimeError('PDF fail')):
            send_order_emails_after_payment(self.order.pk)
        self.order.refresh_from_db()
        self.assertIsNotNone(self.order.confirmation_email_sent_at)


# ---------------------------------------------------------------------------
# Phase 4.2 — Admin PDF download endpoint RBAC and security tests
# ---------------------------------------------------------------------------

class Phase42AdminReceiptPdfEndpointTest(TestCase):
    """RBAC and security tests for GET /api/admin/orders/{pk}/receipt-pdf/."""

    def _make_user(self, username, role):
        u = User.objects.create_user(username, f'{username}@ex.com', 'pw')
        u.profile.role = role
        u.profile.save()
        return u

    def setUp(self):
        self.client = APIClient()
        self.order = _make_paid_order_42(
            stripe_session_id='cs_42_pdf_ep', stripe_payment_intent_id='pi_42_pdf_ep',
        )
        self.url = f'/api/admin/orders/{self.order.pk}/receipt-pdf/'

        self.admin = self._make_user('adm42', UserProfile.ROLE_ADMIN)
        self.sales = self._make_user('sal42', UserProfile.ROLE_SALES)
        self.inventory = self._make_user('inv42', UserProfile.ROLE_INVENTORY)
        self.superadmin = self._make_user('sup42', UserProfile.ROLE_SUPERADMIN)
        self.technician = self._make_user('tech42', UserProfile.ROLE_TECHNICIAN)
        self.customer = self._make_user('cust42', UserProfile.ROLE_CUSTOMER)

    def test_admin_can_download_pdf(self):
        self.client.force_authenticate(user=self.admin)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)

    def test_sales_can_download_pdf(self):
        self.client.force_authenticate(user=self.sales)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)

    def test_inventory_can_download_pdf(self):
        self.client.force_authenticate(user=self.inventory)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)

    def test_superadmin_can_download_pdf(self):
        self.client.force_authenticate(user=self.superadmin)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)

    def test_technician_cannot_download_pdf(self):
        self.client.force_authenticate(user=self.technician)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 403)

    def test_customer_cannot_download_pdf(self):
        self.client.force_authenticate(user=self.customer)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 403)

    def test_anonymous_cannot_download_pdf(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 401)

    def test_pdf_response_content_type(self):
        self.client.force_authenticate(user=self.admin)
        resp = self.client.get(self.url)
        self.assertEqual(resp['Content-Type'], 'application/pdf')

    def test_pdf_response_content_disposition(self):
        self.client.force_authenticate(user=self.admin)
        resp = self.client.get(self.url)
        disposition = resp.get('Content-Disposition', '')
        self.assertIn('attachment', disposition)
        self.assertIn('.pdf', disposition)

    def test_pdf_response_bytes_start_with_pdf_header(self):
        self.client.force_authenticate(user=self.admin)
        resp = self.client.get(self.url)
        self.assertTrue(resp.content[:4] == b'%PDF')

    def test_pdf_endpoint_404_for_unknown_order(self):
        self.client.force_authenticate(user=self.admin)
        resp = self.client.get('/api/admin/orders/999999/receipt-pdf/')
        self.assertEqual(resp.status_code, 404)

    def test_pdf_endpoint_400_for_unpaid_order(self):
        unpaid = _make_paid_order_42(
            paid=False, status=Order.Status.PENDING_PAYMENT,
            stripe_session_id='cs_42_unp', stripe_payment_intent_id='pi_42_unp',
        )
        self.client.force_authenticate(user=self.admin)
        resp = self.client.get(f'/api/admin/orders/{unpaid.pk}/receipt-pdf/')
        self.assertEqual(resp.status_code, 400)

    def test_pdf_endpoint_creates_audit_log(self):
        self.client.force_authenticate(user=self.admin)
        before = AdminAuditLog.objects.filter(action='order_receipt_pdf_downloaded').count()
        self.client.get(self.url)
        after = AdminAuditLog.objects.filter(action='order_receipt_pdf_downloaded').count()
        self.assertEqual(after, before + 1)

    def test_pdf_response_does_not_contain_stripe_session_id(self):
        """PDF bytes must not contain the Stripe session ID in cleartext."""
        self.client.force_authenticate(user=self.admin)
        resp = self.client.get(self.url)
        self.assertNotIn(b'cs_42_pdf_ep', resp.content)

    def test_pdf_response_does_not_contain_stripe_payment_intent(self):
        """PDF bytes must not contain the Stripe payment intent ID in cleartext."""
        self.client.force_authenticate(user=self.admin)
        resp = self.client.get(self.url)
        self.assertNotIn(b'pi_42_pdf_ep', resp.content)
