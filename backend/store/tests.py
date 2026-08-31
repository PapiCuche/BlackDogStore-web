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
Phase 4.2 (+46 tests, audit +4=50): PDF context builder (excludes Stripe fields, Decimal types, disclaimer), PDF generator (valid bytes, ValueError for unpaid), email+PDF integration (attachment, PDF fail graceful, error logged), admin PDF endpoint RBAC (4 allowed roles, technician/customer/anon blocked), audit log clean metadata, content-type, Content-Disposition, Stripe data not in cleartext, copywriting disclaimer, no-SUNAT-electronico title.
Phase 4.3 (+37 tests, audit +3=42): resend_order_confirmation_email service (bypasses idempotency, best-effort PDF, updates flag, raises on SMTP failure), AdminOrderResendEmailView RBAC (admin+superadmin allowed; customer/sales/inventory/technician/anon blocked), 400 for unpaid, 404 for missing, 502 for SMTP failure, 405 for non-POST methods, audit log clean metadata (no Stripe IDs), SMTP failure records email_send_error, audit log NOT created on SMTP failure, HTML body no Stripe IDs, regression (automatic webhook flow unaffected).
"""
from decimal import Decimal
from unittest.mock import patch, MagicMock

import stripe as stripe_lib

from django.core import mail
from django.core.cache import cache
from django.test import TestCase, TransactionTestCase, override_settings
from django.contrib.auth import get_user_model
from django.utils import timezone
from datetime import timedelta
from rest_framework.test import APIClient
from rest_framework import status

from .models import AccountToken, AdminAuditLog, Category, Product, Coupon, Order, OrderItem, CartItem, Review, UserProfile

User = get_user_model()


def _storefront_of(company):
    """
    Pin the public storefront to `company` for the duration of a block.

    Needed by any test that creates a SECOND company: the single-company
    fallback in tenancy.resolve_storefront_company() correctly stops firing once
    the tenant is ambiguous, and the test must then say which storefront it means
    — exactly as a real multi-tenant deployment has to.
    """
    return override_settings(DEFAULT_STOREFRONT_COMPANY_SLUG=company.slug)


def _pilot_branch():
    """
    The branch the pilot tenant's stock lives in.

    Phase 2D: `create_stock_movement()` needs a place to put units, so tests that
    call the service layer directly have to name one. Migration 0015 created this
    branch and migration 0025 put the historical stock in it.
    """
    from .models import Branch
    return Branch.objects.filter(company=_pilot_company()).order_by('pk').first()


def _set_notification_email(company, address):
    """
    Point a company's new-sale alerts at `address` — Phase 3.

    The global `ORDER_NOTIFICATION_EMAIL` setting stopped being a recipient: one
    address for every tenant meant a second company's sales would be announced in
    the pilot's inbox. Tests that expect an internal alert now say WHOSE alert it
    is, which is the point of the change.
    """
    from .models import CompanySettings
    row, _ = CompanySettings.objects.get_or_create(company=company)
    row.order_notification_email = address
    row.save(update_fields=['order_notification_email', 'updated_at'])
    return row


def _set_stock(product, quantity, branch=None):
    """
    Set what the SHELF holds — Phase 2D.

    `product.inventory = N; product.save()` no longer changes what can be sold:
    it is a derived aggregate, and checkout, the cart and the webhook all read
    BranchStock. Tests that mean "there are N of these to sell" say it here.
    """
    return _seeded(product, quantity)


def _reset_products():
    """
    Drop the seeded demo catalogue, stock rows first.

    `BranchStock.product` and `StockMovement.product` are PROTECT — a product
    with units on a shelf or a line in the Kardex must not vanish — so a test
    that wants a clean catalogue has to clear those first. That protection is
    correct in production and only needs acknowledging here.
    """
    from .models import BranchStock, StockMovement
    StockMovement.objects.all().delete()
    BranchStock.objects.all().delete()
    Product.objects.all().delete()


def _seeded(product, quantity=None):
    """
    Put a product's stock on a branch shelf — Phase 2D test bridge.

    From Phase 2D, `Product.inventory` is a compatibility AGGREGATE and
    `BranchStock.quantity` is the source of truth, so
    `_seeded(Product.objects.create(inventory=10))` on its own creates ten units that are
    nowhere: the catalogue shows nothing sellable and checkout refuses. In
    production nothing does that — migration 0025 placed the historical stock and
    `apply_initial_stock()` places a new product's opening balance — but hundreds
    of tests written before 2D create products directly.

    This does what the platform does, in one line: places the units in the
    company's fulfillment branch (creating one when the test's company has none,
    which is the state of every company built with `Company.objects.create`) and
    keeps the aggregate in step.
    """
    from .models import Branch, BranchStock

    qty = product.inventory if quantity is None else quantity
    company = product.company
    branch = company.default_inventory_branch
    if branch is None or branch.company_id != company.pk:
        branch = Branch.objects.filter(
            company=company, is_active=True,
        ).order_by('pk').first()
    if branch is None:
        branch = Branch.objects.create(company=company, name='Sucursal de pruebas')
    if company.default_inventory_branch_id != branch.pk:
        company.default_inventory_branch = branch
        company.save(update_fields=['default_inventory_branch', 'updated_at'])

    BranchStock.objects.update_or_create(
        branch=branch, product=product, defaults={'quantity': qty},
    )
    Product.objects.filter(pk=product.pk).update(inventory=qty)
    product.refresh_from_db()
    return product


def _pilot_company():
    """
    The installation's first tenant.

    Every test database runs the full migration chain, so migration 0015 has
    already created the pilot company — the same one migration 0019 backfilled
    the catalogue onto. Tests that do not care WHICH tenant they are in use this
    one; tests about isolation create their own companies explicitly.
    """
    from .models import Company
    return Company.objects.order_by('pk').first()


# ---------------------------------------------------------------------------
# Phase 0.1 tests
# ---------------------------------------------------------------------------

class CategoryModelTest(TestCase):
    def test_create_and_str(self):
        cat = Category.objects.create(company=_pilot_company(), name="MacBook", slug="macbook-test")
        self.assertEqual(str(cat), "MacBook")
        self.assertEqual(cat.slug, "macbook-test")

    def test_slug_unique(self):
        Category.objects.create(company=_pilot_company(), name="iPad", slug="ipad-test")
        with self.assertRaises(Exception):
            Category.objects.create(company=_pilot_company(), name="iPad Copia", slug="ipad-test")


class ProductModelTest(TestCase):
    def setUp(self):
        self.category = Category.objects.create(company=_pilot_company(), name="MacBook", slug="macbook-model-test")

    def test_create_product(self):
        product = _seeded(Product.objects.create(company=_pilot_company(),
            name="MacBook Pro M4",
            slug="macbook-pro-m4-test",
            price=Decimal("9999.00"),
            inventory=5,
            category=self.category,
        ))
        self.assertEqual(str(product), "MacBook Pro M4")
        self.assertEqual(product.price, Decimal("9999.00"))
        self.assertEqual(product.inventory, 5)

    def test_product_defaults(self):
        product = _seeded(Product.objects.create(company=_pilot_company(),
            name="Test Product Defaults",
            slug="test-product-defaults-001",
            price=Decimal("100.00"),
        ))
        self.assertEqual(product.inventory, 0)
        self.assertEqual(product.image_url, "")
        self.assertIsNone(product.category)


class CouponModelTest(TestCase):
    def test_active_coupon(self):
        coupon = Coupon.objects.create(company=_pilot_company(),
            code="DESCUENTO10",
            discount_percent=10,
            is_active=True,
        )
        self.assertEqual(str(coupon), "DESCUENTO10 — 10%")
        self.assertTrue(coupon.is_active)
        self.assertIsNone(coupon.expires_at)

    def test_inactive_coupon(self):
        coupon = Coupon.objects.create(company=_pilot_company(),
            code="VENCIDO",
            discount_percent=20,
            is_active=False,
        )
        self.assertFalse(coupon.is_active)

    def test_expired_coupon_still_has_future_date(self):
        past = timezone.now() - timedelta(days=1)
        coupon = Coupon.objects.create(company=_pilot_company(),
            code="PASADO",
            discount_percent=15,
            is_active=True,
            expires_at=past,
        )
        self.assertTrue(coupon.expires_at < timezone.now())


class ProductAPITest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.category = Category.objects.create(company=_pilot_company(), name="iPad Test", slug="ipad-api-test")
        self.product = _seeded(Product.objects.create(company=_pilot_company(),
            name="iPad Pro M4 Test",
            slug="ipad-pro-m4-api-test",
            price=Decimal("3999.00"),
            inventory=8,
            category=self.category,
        ))

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


class Phase50ProductAPITest(TestCase):
    """Phase 5.0: select_related, in_stock filter, ordering whitelist."""

    def setUp(self):
        self.client = APIClient()
        self.cat = Category.objects.create(company=_pilot_company(), name="Mac Phase50", slug="mac-p50")
        self.p1 = _seeded(Product.objects.create(company=_pilot_company(),
            name="MacBook Air P50",
            slug="macbook-air-p50",
            price=Decimal("4999.00"),
            inventory=5,
            category=self.cat,
            is_active=True,
        ))
        self.p2 = _seeded(Product.objects.create(company=_pilot_company(),
            name="MacBook Pro P50",
            slug="macbook-pro-p50",
            price=Decimal("9999.00"),
            inventory=0,
            category=self.cat,
            is_active=True,
        ))
        self.p3 = _seeded(Product.objects.create(company=_pilot_company(),
            name="Mac Studio P50",
            slug="mac-studio-p50",
            price=Decimal("7499.00"),
            inventory=3,
            category=self.cat,
            is_active=True,
        ))
        self.inactive = _seeded(Product.objects.create(company=_pilot_company(),
            name="Inactive Mac P50",
            slug="inactive-mac-p50",
            price=Decimal("1.00"),
            inventory=10,
            category=self.cat,
            is_active=False,
        ))

    def _get_slugs(self, path):
        res = self.client.get(path)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        results = data.get("results", data) if isinstance(data, dict) else data
        return [r["slug"] for r in results]

    def test_list_returns_200_anonymous(self):
        res = self.client.get("/api/products/")
        self.assertEqual(res.status_code, 200)

    def test_inactive_excluded_from_public_list(self):
        slugs = self._get_slugs("/api/products/")
        self.assertNotIn("inactive-mac-p50", slugs)

    def test_category_field_present_in_response(self):
        res = self.client.get("/api/products/?slug=macbook-air-p50")
        data = res.json()
        results = data.get("results", data) if isinstance(data, dict) else data
        self.assertEqual(results[0]["category"]["slug"], "mac-p50")

    def test_in_stock_filter_excludes_zero_inventory(self):
        slugs = self._get_slugs("/api/products/?category=mac-p50&in_stock=true")
        self.assertIn("macbook-air-p50", slugs)
        self.assertIn("mac-studio-p50", slugs)
        self.assertNotIn("macbook-pro-p50", slugs)

    def test_in_stock_false_not_filtered(self):
        slugs = self._get_slugs("/api/products/?category=mac-p50")
        self.assertIn("macbook-pro-p50", slugs)

    def test_ordering_price_asc(self):
        slugs = self._get_slugs("/api/products/?category=mac-p50&ordering=price")
        active = [s for s in slugs if s in {"macbook-air-p50", "macbook-pro-p50", "mac-studio-p50"}]
        self.assertEqual(active, ["macbook-air-p50", "mac-studio-p50", "macbook-pro-p50"])

    def test_ordering_price_desc(self):
        slugs = self._get_slugs("/api/products/?category=mac-p50&ordering=-price")
        active = [s for s in slugs if s in {"macbook-air-p50", "macbook-pro-p50", "mac-studio-p50"}]
        self.assertEqual(active, ["macbook-pro-p50", "mac-studio-p50", "macbook-air-p50"])

    def test_ordering_name_asc(self):
        slugs = self._get_slugs("/api/products/?category=mac-p50&ordering=name")
        active = [s for s in slugs if s in {"macbook-air-p50", "macbook-pro-p50", "mac-studio-p50"}]
        # "Mac Studio" < "MacBook Air" < "MacBook Pro" (space ASCII 32 < 'B' ASCII 66)
        self.assertEqual(active, ["mac-studio-p50", "macbook-air-p50", "macbook-pro-p50"])

    def test_ordering_newest(self):
        slugs = self._get_slugs("/api/products/?category=mac-p50&ordering=newest")
        active = [s for s in slugs if s in {"macbook-air-p50", "macbook-pro-p50", "mac-studio-p50"}]
        self.assertEqual(active, ["mac-studio-p50", "macbook-pro-p50", "macbook-air-p50"])

    def test_ordering_invalid_returns_200_not_500(self):
        res = self.client.get("/api/products/?ordering=injected__field")
        self.assertEqual(res.status_code, 200)

    def test_categories_endpoint_returns_200(self):
        res = self.client.get("/api/categories/")
        self.assertEqual(res.status_code, 200)

    def test_product_without_category_does_not_crash(self):
        _seeded(Product.objects.create(company=_pilot_company(),
            name="No Category P50",
            slug="no-cat-p50",
            price=Decimal("99.00"),
            inventory=1,
            is_active=True,
        ))
        res = self.client.get("/api/products/?slug=no-cat-p50")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        results = data.get("results", data) if isinstance(data, dict) else data
        self.assertIsNone(results[0]["category"])


class CouponAPITest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.coupon = Coupon.objects.create(company=_pilot_company(),
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
        self.category = Category.objects.create(company=_pilot_company(), name="Cables Test", slug="cables-cart-test")
        self.product = _seeded(Product.objects.create(company=_pilot_company(),
            name="Cable USB-C Test",
            slug="cable-usbc-cart-test",
            price=Decimal("149.00"),
            inventory=50,
            category=self.category,
        ))
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
        self.product = _seeded(Product.objects.create(company=_pilot_company(),
            name="iPhone Test Order",
            slug="iphone-test-order-001",
            price=Decimal("4299.00"),
            inventory=5,
        ))

    def test_create_order_str(self):
        order = Order.objects.create(company=_pilot_company(),
            customer_name="Carlos García",
            customer_email="carlos@example.com",
            total=Decimal("4299.00"),
        )
        self.assertIn("Carlos García", str(order))
        self.assertFalse(order.paid)

    def test_order_paid_field_defaults_false(self):
        order = Order.objects.create(company=_pilot_company(), total=Decimal("100.00"))
        self.assertFalse(order.paid)


# ---------------------------------------------------------------------------
# Phase 1 tests
# ---------------------------------------------------------------------------

class CartStockValidationTest(TestCase):
    """Cart add() endpoint validates inventory before accepting items."""

    def setUp(self):
        self.client = APIClient()
        self.product = _seeded(Product.objects.create(company=_pilot_company(),
            name="iPhone 16 Pro Stock Test",
            slug="iphone-16-pro-stock-test",
            price=Decimal("5999.00"),
            inventory=3,
        ))
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
        self.product = _seeded(Product.objects.create(company=_pilot_company(),
            name="MacBook Air M3 Checkout Test",
            slug="macbook-air-m3-checkout-test",
            price=Decimal("7499.00"),
            inventory=5,
        ))
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
        # Phase 2D: checkout validates the FULFILLMENT BRANCH, so emptying the
        # shelf is what "no stock" means. Zeroing Product.inventory alone would
        # leave five units sitting in the branch that actually ships.
        _set_stock(self.product, 0)
        response = self.client.post(
            "/api/payments/create-checkout-session/", self._base_body(), format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("errors", response.json())

    @patch("stripe.checkout.Session.create")
    def test_checkout_applies_coupon_discount_from_db(self, mock_create):
        mock_create.return_value = self._mock_stripe_session()
        Coupon.objects.create(company=_pilot_company(), code="FASE1TEST", discount_percent=10, is_active=True)
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
        self.product = _seeded(Product.objects.create(company=_pilot_company(),
            name="AirPods Pro Webhook Test",
            slug="airpods-pro-webhook-test",
            price=Decimal("799.00"),
            inventory=10,
        ))
        self.session_key = "webhook-test-session-001"
        self.order = Order.objects.create(company=_pilot_company(),
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
        self.order = Order.objects.create(company=_pilot_company(),
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
        owner_order = Order.objects.create(company=_pilot_company(),
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
        self.product = _seeded(Product.objects.create(company=_pilot_company(),
            name="iPhone Stripe Error Test",
            slug="iphone-stripe-error-test",
            price=Decimal("4999.00"),
            inventory=5,
        ))
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
        self.order_u1 = Order.objects.create(company=_pilot_company(),
            user=self.user1,
            customer_email="u1@example.com",
            total=Decimal("500.00"),
            status=Order.Status.PAID,
        )
        self.order_u2 = Order.objects.create(company=_pilot_company(),
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
        self.product = _seeded(Product.objects.create(company=_pilot_company(),
            name='iPhone 16 Patch Test',
            slug='iphone-16-patch-test',
            price=Decimal('5999.00'),
            inventory=5,
        ))
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
        other_product = _seeded(Product.objects.create(company=_pilot_company(),
            name='Other Product',
            slug='other-product-patch',
            price=Decimal('100.00'),
            inventory=10,
        ))
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
        other_product = _seeded(Product.objects.create(company=_pilot_company(),
            name='Other Product Patch',
            slug='other-product-patch-2',
            price=Decimal('100.00'),
            inventory=10,
        ))
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
        self.category = Category.objects.create(company=_pilot_company(), name='Review Cat', slug='review-cat-test')
        self.product = _seeded(Product.objects.create(company=_pilot_company(),
            name='Review Product Test',
            slug='review-product-test',
            price=Decimal('999.00'),
            inventory=5,
            category=self.category,
        ))
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
        self.order1 = Order.objects.create(company=_pilot_company(),
            user=self.customer1,
            customer_email='c1@example.com',
            total=Decimal('100.00'),
            status=Order.Status.PAID,
        )
        self.order2 = Order.objects.create(company=_pilot_company(),
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

    def test_sales_only_sees_its_own_orders_on_the_customer_endpoint(self):
        """
        BEHAVIOUR CHANGE, Phase 2C. /api/orders/ is the CUSTOMER surface.

        It used to return every order in the database to any staff user. With
        orders tenantised that shortcut became a cross-tenant leak: a salesperson
        of company A would see company B's orders. Internal administration lives
        at /api/admin/orders/, which scopes by company and checks capabilities.
        """
        self.client.force_authenticate(user=self.sales_user)
        response = self.client.get('/api/orders/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = [o['id'] for o in response.json()]
        # order1 belongs to user1, order2 to user2; the salesperson owns neither
        self.assertNotIn(self.order1.id, ids)
        self.assertNotIn(self.order2.id, ids)

    def test_admin_only_sees_its_own_orders_on_the_customer_endpoint(self):
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.get('/api/orders/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = [o['id'] for o in response.json()]
        self.assertNotIn(self.order1.id, ids)
        self.assertNotIn(self.order2.id, ids)

    def test_anon_gets_401(self):
        response = self.client.get('/api/orders/')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class Phase30RegressionTest(TestCase):
    """Phase 3.0 regression: login, refresh, logout, checkout, webhook, payment, reviews, coupons."""

    def setUp(self):
        self.client = APIClient()
        cache.clear()
        cat = Category.objects.create(company=_pilot_company(), name='Reg Cat', slug='reg-cat-30')
        self.product = _seeded(Product.objects.create(company=_pilot_company(),
            name='Reg Product', slug='reg-product-30', price=Decimal('100.00'), inventory=10, category=cat
        ))
        self.coupon = Coupon.objects.create(company=_pilot_company(), code='REGCOUPON30', discount_percent=10, is_active=True)
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
    return _seeded(Product.objects.create(company=_pilot_company(),
        name=name, slug=slug, price=Decimal(price),
        inventory=inventory, is_active=is_active, category=category,
    ))


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
        cat = Category.objects.create(company=_pilot_company(), name='PLA Cat', slug='pla-cat')
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

    def test_superadmin_must_select_a_company(self):
        """
        BEHAVIOUR CHANGE, Phase 2B. A superuser is a PLATFORM master: with the
        catalogue tenantised there is no longer such a thing as "all products".
        They must name the tenant they are acting on, exactly as the internal
        dashboard already required.
        """
        self.client.force_authenticate(user=self.roles[UserProfile.ROLE_SUPERADMIN])
        self.assertEqual(
            self.client.get('/api/admin/products/').status_code,
            status.HTTP_403_FORBIDDEN,
        )
        pilot = _pilot_company()
        self.assertEqual(
            self.client.get(f'/api/admin/products/?company={pilot.pk}').status_code,
            status.HTTP_200_OK,
        )

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
        _reset_products()
        self.admin = User.objects.create_user(username='pf_admin', password='Pass123!')
        self.admin.profile.role = UserProfile.ROLE_ADMIN
        self.admin.profile.save()
        self.cat = Category.objects.create(company=_pilot_company(), name='PF Cat', slug='pf-cat')
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
        self.cat = Category.objects.create(company=_pilot_company(), name='PC Cat', slug='pc-cat')

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
        # Phase 2B: a platform master names the tenant it creates into.
        self.client.force_authenticate(user=self.roles[UserProfile.ROLE_SUPERADMIN])
        pilot = _pilot_company()
        response = self.client.post(
            f'/api/admin/products/?company={pilot.pk}',
            self._base_payload(name='Super Product'), format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Product.objects.get(name='Super Product').company_id, pilot.pk)

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
        self.cat = Category.objects.create(company=_pilot_company(), name='PD Cat', slug='pd-cat')
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
        # A platform master names the company: they belong to no tenant, and
        # picking one for them would be the cross-tenant leak Phase 2B closed.
        self.client.force_authenticate(user=self.roles[UserProfile.ROLE_SUPERADMIN])
        pilot = _pilot_company().pk
        response = self.client.post(
            f'{self._url()}?company={pilot}',
            {'delta': 2, 'reason': 'Superadmin test'}, format='json',
        )
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
        Category.objects.create(company=_pilot_company(), name='Cat A', slug='cat-a-test')

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
        cat = Category.objects.create(company=_pilot_company(), name='PUB Cat', slug='pub-cat')
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
        cat = Category.objects.create(company=_pilot_company(), name='Reg32 Cat', slug='reg32-cat')
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
    return Order.objects.create(company=_pilot_company(), **kwargs)


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

    def test_superadmin_must_select_a_company_to_list_orders(self):
        """
        BEHAVIOUR CHANGE, Phase 2C. A superuser is a PLATFORM master: with orders
        tenantised there is no longer "all orders". They name the tenant, exactly
        as the catalogue and the dashboard already require.
        """
        self.client.force_authenticate(user=self.superadmin)
        self.assertEqual(
            self.client.get('/api/admin/orders/').status_code,
            status.HTTP_403_FORBIDDEN,
        )
        pilot = _pilot_company()
        self.assertEqual(
            self.client.get(f'/api/admin/orders/?company={pilot.pk}').status_code,
            status.HTTP_200_OK,
        )

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
        self.order = Order.objects.create(company=_pilot_company(),
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

    def test_superadmin_must_select_a_company_to_change_fulfillment(self):
        """Phase 2C: a platform master names the tenant it acts on."""
        self.assertEqual(self._patch(self.superadmin).status_code,
                         status.HTTP_403_FORBIDDEN)

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
        order = Order.objects.create(company=_pilot_company(),
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
        cat = Category.objects.create(company=_pilot_company(), name='Reg33 Cat', slug='reg33-cat')
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
        order = Order.objects.create(company=_pilot_company(),
            customer_name='Reg33 Order',
            customer_email='reg33@example.com',
            total=Decimal('100.00'),
        )
        self.assertEqual(order.fulfillment_status, 'pending')

    def test_admin_order_list_returns_fulfillment_status_in_results(self):
        Order.objects.create(company=_pilot_company(),
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
        order = Order.objects.create(company=_pilot_company(),
            customer_name='Reg33 Checkout',
            customer_email='checkout33@example.com',
            status=Order.Status.PENDING_PAYMENT,
            paid=False,
            total=Decimal('500.00'),
        )
        self.assertEqual(order.fulfillment_status, Order.FulfillmentStatus.PENDING)

    def test_webhook_update_fields_does_not_include_fulfillment_status(self):
        """Simulates the webhook save to verify fulfillment_status is never written by the webhook."""
        order = Order.objects.create(company=_pilot_company(),
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
        order = Order.objects.create(company=_pilot_company(),
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
        self.product = _seeded(Product.objects.create(company=_pilot_company(),
            name='P40 Product',
            slug='p40-product',
            price=Decimal('500.00'),
            inventory=10,
        ))
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
        self.order = Order.objects.create(company=_pilot_company(),
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
    cat, _ = Category.objects.get_or_create(company=_pilot_company(), name='Mac41', defaults={'slug': 'mac-41'})
    product, _ = Product.objects.get_or_create(
        company=_pilot_company(),
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
    order = Order.objects.create(company=_pilot_company(), **defaults)
    OrderItem.objects.create(order=order, product=product, quantity=1, price='9999.00')
    return order


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    DEFAULT_FROM_EMAIL='no-reply@test.invalid',
    FRONTEND_URL='http://localhost:3000',
)
class Phase41EmailServiceUnitTest(TestCase):
    """Unit tests for email_services.py functions (called directly, no webhook)."""

    def setUp(self):
        # Phase 3: the recipient belongs to the COMPANY, not to a setting.
        _set_notification_email(_pilot_company(), 'store@example.com')
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
        """
        No address on the COMPANY means no alert — and no platform fallback.

        Phase 3 removed the global recipient precisely so that an unconfigured
        tenant stays silent instead of announcing its sales in whichever inbox
        the platform-wide setting happened to name.
        """
        from store.email_services import send_internal_order_notification
        order = _make_paid_order()
        _set_notification_email(order.company, '')
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
        _set_notification_email(order.company, '')
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

    def test_pickup_store_email_body_contains_the_pickup_point(self):
        """
        A pickup order shows WHERE TO COLLECT, which is the fulfilling branch.

        Phase 3 separated this from the legal address: one is who invoices, the
        other is which door the customer knocks on. Printing the office address
        under "Punto de retiro" sends people to the wrong place.
        """
        from store.company_settings import order_pickup_location
        from store.email_services import send_order_confirmation_email

        order = _make_paid_order(delivery_method="pickup_store")
        order.fulfillment_branch = _pilot_branch()
        order.company_snapshot = {}
        order.save(update_fields=['fulfillment_branch', 'company_snapshot'])

        send_order_confirmation_email(order)
        msg = mail.outbox[0]
        pickup = order_pickup_location(order)
        self.assertEqual(pickup['source'], 'branch')
        self.assertIn(pickup['address'], msg.body)
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
        cat = Category.objects.create(company=_pilot_company(), name='Mac41W', slug='mac-41w')
        self.product = _seeded(Product.objects.create(company=_pilot_company(),
            name='iMac M3', slug='imac-m3-41w', price='5999.00', inventory=3, category=cat,
        ))
        self.order = Order.objects.create(company=_pilot_company(),
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
    cat, _ = Category.objects.get_or_create(company=_pilot_company(), name='Mac42', defaults={'slug': 'mac-42'})
    product, _ = Product.objects.get_or_create(
        company=_pilot_company(),
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
    order = Order.objects.create(company=_pilot_company(), **defaults)
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

    def test_context_seller_identity_comes_from_the_order_company(self):
        """
        Phase 3: there are no store constants to compare against any more.

        The identity on the document is the ORDER's own company — which for this
        installation's pilot is the same values that used to be compiled in, now
        living in its CompanySettings row where migration 0028 put them.
        """
        from store.company_settings import company_identity
        from store.pdf_services import build_order_pdf_context

        expected = company_identity(self.order.company)
        ctx = build_order_pdf_context(self.order)
        self.assertEqual(ctx['store_name'], expected.name)
        self.assertEqual(ctx['store_ruc'], expected.tax_id)
        self.assertEqual(ctx['store_legal_name'], expected.legal_name)
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
        """
        Phase 3: built from the company SLUG, not from a literal.

        The slug rather than the NAME because the name is free text a tenant
        types, and this string reaches a Content-Disposition header and a
        filesystem — the two places where a stray quote or slash stops being
        cosmetic.
        """
        from store.pdf_services import get_order_receipt_filename
        filename = get_order_receipt_filename(self.order)
        self.assertEqual(
            filename, f'{self.order.company.slug}-pedido-{self.order.id}.pdf',
        )
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

    def test_audit_log_metadata_does_not_contain_stripe_ids(self):
        """Audit log entry must not store stripe_session_id or stripe_payment_intent_id."""
        self.client.force_authenticate(user=self.admin)
        self.client.get(self.url)
        log = AdminAuditLog.objects.filter(
            action='order_receipt_pdf_downloaded',
            target_id=str(self.order.pk),
        ).latest('created_at')
        metadata_str = str(log.metadata)
        self.assertNotIn('cs_42_pdf_ep', metadata_str)
        self.assertNotIn('pi_42_pdf_ep', metadata_str)
        self.assertNotIn('payment_error', metadata_str)
        self.assertIn('order_id', metadata_str)

    def test_pdf_context_does_not_label_itself_comprobante_electronico(self):
        """The PDF title must NOT say 'comprobante electrónico' without the SUNAT disclaimer."""
        from store.pdf_services import build_order_pdf_context
        ctx = build_order_pdf_context(self.order)
        title_lower = ctx['title'].lower()
        # Title can say "constancia" but never "comprobante electrónico"
        self.assertNotIn('comprobante electrónico', title_lower)
        self.assertNotIn('comprobante electronico', title_lower)

    def test_pdf_disclaimer_present_in_context(self):
        """Disclaimer must contain both 'interno' and 'No válido como comprobante electrónico SUNAT'."""
        from store.pdf_services import build_order_pdf_context, DISCLAIMER
        ctx = build_order_pdf_context(self.order)
        self.assertIn('interno', ctx['disclaimer'].lower())
        self.assertIn('No válido como comprobante electrónico SUNAT', ctx['disclaimer'])


# ---------------------------------------------------------------------------
# Phase 4.3 — Manual email resend
# ---------------------------------------------------------------------------

def _make_paid_order_43(**kwargs):
    """Factory for Phase 4.3 tests — separate slugs to avoid UNIQUE conflicts."""
    cat, _ = Category.objects.get_or_create(company=_pilot_company(), name='Mac43', defaults={'slug': 'mac-43'})
    product, _ = Product.objects.get_or_create(
        company=_pilot_company(),
        slug='mbp-m3-43',
        defaults={'name': 'MacBook Pro M3 43', 'price': '8999.00', 'inventory': 3, 'category': cat},
    )
    defaults = dict(
        customer_name='Marco Quispe', customer_email='marco@example.com',
        customer_phone='936449111', document_type='dni', document_number='11223344',
        delivery_method='pickup_store', receipt_type='boleta',
        accepted_terms=True, accepted_warranty_policy=True,
        total='8999.00', discount_amount='0.00', status=Order.Status.PAID, paid=True,
        paid_at=timezone.now(), stripe_session_id=None,
        stripe_payment_intent_id='pi_test_43_base',
    )
    defaults.update(kwargs)
    order = Order.objects.create(company=_pilot_company(), **defaults)
    OrderItem.objects.create(order=order, product=product, quantity=1, price='8999.00')
    return order


def _make_user_43(username, role):
    user = User.objects.create_user(username, f'{username}@example.com', 'x')
    user.profile.role = role
    user.profile.save()
    return user


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    DEFAULT_FROM_EMAIL='noreply@blackdogstore.test',
    EMAIL_HOST='localhost',
    EMAIL_PORT=25,
)
class Phase43ResendEmailServiceTest(TestCase):
    """Unit tests for resend_order_confirmation_email()."""

    def setUp(self):
        self.order = _make_paid_order_43()

    def test_resend_returns_dict_with_had_pdf(self):
        from store.email_services import resend_order_confirmation_email
        result = resend_order_confirmation_email(self.order)
        self.assertIn('had_pdf', result)
        self.assertIsInstance(result['had_pdf'], bool)

    def test_resend_email_sent_to_customer(self):
        from store.email_services import resend_order_confirmation_email
        resend_order_confirmation_email(self.order)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(self.order.customer_email, mail.outbox[0].to)

    def test_resend_attaches_pdf_when_generation_succeeds(self):
        from store.email_services import resend_order_confirmation_email
        result = resend_order_confirmation_email(self.order)
        self.assertTrue(result['had_pdf'])
        msg = mail.outbox[0]
        pdf_attachments = [a for a in msg.attachments if isinstance(a, tuple) and a[2] == 'application/pdf']
        self.assertEqual(len(pdf_attachments), 1)

    def test_resend_updates_confirmation_email_sent_at(self):
        from store.email_services import resend_order_confirmation_email
        self.order.confirmation_email_sent_at = None
        self.order.save(update_fields=['confirmation_email_sent_at'])
        resend_order_confirmation_email(self.order)
        self.order.refresh_from_db()
        self.assertIsNotNone(self.order.confirmation_email_sent_at)

    def test_resend_bypasses_idempotency_flag(self):
        """Resend must work even if confirmation_email_sent_at is already set."""
        from store.email_services import resend_order_confirmation_email
        self.order.confirmation_email_sent_at = timezone.now() - timedelta(days=1)
        self.order.save(update_fields=['confirmation_email_sent_at'])
        result = resend_order_confirmation_email(self.order)
        self.assertIn('had_pdf', result)
        self.assertEqual(len(mail.outbox), 1)

    def test_resend_raises_if_order_not_paid(self):
        from store.email_services import resend_order_confirmation_email
        self.order.paid = False
        self.order.status = Order.Status.PENDING_PAYMENT
        self.order.save(update_fields=['paid', 'status'])
        with self.assertRaises(ValueError):
            resend_order_confirmation_email(self.order)

    def test_resend_raises_if_status_not_paid(self):
        from store.email_services import resend_order_confirmation_email
        self.order.status = Order.Status.REFUNDED
        self.order.save(update_fields=['status'])
        with self.assertRaises(ValueError):
            resend_order_confirmation_email(self.order)

    def test_resend_pdf_fail_email_still_sent(self):
        from store.email_services import resend_order_confirmation_email
        with patch('store.pdf_services.generate_order_receipt_pdf', side_effect=RuntimeError('PDF error')):
            result = resend_order_confirmation_email(self.order)
        self.assertFalse(result['had_pdf'])
        self.assertEqual(len(mail.outbox), 1)

    def test_resend_pdf_fail_records_error_with_prefix(self):
        from store.email_services import resend_order_confirmation_email
        with patch('store.pdf_services.generate_order_receipt_pdf', side_effect=RuntimeError('PDF fail 43')):
            resend_order_confirmation_email(self.order)
        self.order.refresh_from_db()
        self.assertIn('resend_pdf_skip:', self.order.email_send_error)

    def test_resend_does_not_send_internal_notification(self):
        from store.email_services import resend_order_confirmation_email
        resend_order_confirmation_email(self.order)
        # Only one email in outbox (no internal notification)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(self.order.customer_email, mail.outbox[0].to)

    def test_resend_email_does_not_contain_stripe_payment_intent(self):
        from store.email_services import resend_order_confirmation_email
        order = _make_paid_order_43(
            stripe_payment_intent_id='pi_secret_43_test',
            stripe_session_id=None,
        )
        resend_order_confirmation_email(order)
        body = mail.outbox[0].body
        self.assertNotIn('pi_secret_43_test', body)

    def test_resend_email_does_not_contain_payment_error(self):
        from store.email_services import resend_order_confirmation_email
        self.order.payment_error = 'some_sensitive_error_43'
        self.order.save(update_fields=['payment_error'])
        resend_order_confirmation_email(self.order)
        body = mail.outbox[0].body
        self.assertNotIn('some_sensitive_error_43', body)

    def test_resend_raises_on_smtp_failure(self):
        from store.email_services import resend_order_confirmation_email
        import smtplib
        with patch('django.core.mail.EmailMultiAlternatives.send', side_effect=smtplib.SMTPException('SMTP down')):
            with self.assertRaises(smtplib.SMTPException):
                resend_order_confirmation_email(self.order)

    def test_resend_does_not_update_flag_on_smtp_failure(self):
        """confirmation_email_sent_at must NOT be updated if SMTP fails."""
        from store.email_services import resend_order_confirmation_email
        import smtplib
        before = self.order.confirmation_email_sent_at
        with patch('django.core.mail.EmailMultiAlternatives.send', side_effect=smtplib.SMTPException('down')):
            try:
                resend_order_confirmation_email(self.order)
            except smtplib.SMTPException:
                pass
        self.order.refresh_from_db()
        self.assertEqual(self.order.confirmation_email_sent_at, before)


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    DEFAULT_FROM_EMAIL='noreply@blackdogstore.test',
    EMAIL_HOST='localhost',
    EMAIL_PORT=25,
)
class Phase43ResendEmailEndpointTest(TestCase):
    """RBAC, validation, audit log, and regression tests for AdminOrderResendEmailView."""

    def setUp(self):
        cache.clear()  # reset throttle state between tests
        mail.outbox = []
        self.client = APIClient()
        self.order = _make_paid_order_43(stripe_session_id='cs_43_ep', stripe_payment_intent_id='pi_43_ep')
        self.url = f'/api/admin/orders/{self.order.pk}/resend-confirmation-email/'

        self.admin = _make_user_43('adm43', UserProfile.ROLE_ADMIN)
        self.superadmin = _make_user_43('sup43', UserProfile.ROLE_SUPERADMIN)
        self.customer = _make_user_43('cust43', UserProfile.ROLE_CUSTOMER)
        self.sales = _make_user_43('sal43', UserProfile.ROLE_SALES)
        self.inventory = _make_user_43('inv43', UserProfile.ROLE_INVENTORY)
        self.technician = _make_user_43('tech43', UserProfile.ROLE_TECHNICIAN)

    # --- RBAC ---

    def test_anonymous_receives_401(self):
        resp = self.client.post(self.url)
        self.assertEqual(resp.status_code, 401)

    def test_customer_receives_403(self):
        self.client.force_authenticate(user=self.customer)
        resp = self.client.post(self.url)
        self.assertEqual(resp.status_code, 403)

    def test_sales_receives_403(self):
        self.client.force_authenticate(user=self.sales)
        resp = self.client.post(self.url)
        self.assertEqual(resp.status_code, 403)

    def test_inventory_receives_403(self):
        self.client.force_authenticate(user=self.inventory)
        resp = self.client.post(self.url)
        self.assertEqual(resp.status_code, 403)

    def test_technician_receives_403(self):
        self.client.force_authenticate(user=self.technician)
        resp = self.client.post(self.url)
        self.assertEqual(resp.status_code, 403)

    def test_admin_can_resend(self):
        self.client.force_authenticate(user=self.admin)
        resp = self.client.post(self.url)
        self.assertEqual(resp.status_code, 200)

    def test_superadmin_can_resend(self):
        self.client.force_authenticate(user=self.superadmin)
        resp = self.client.post(self.url)
        self.assertEqual(resp.status_code, 200)

    # --- Validation ---

    def test_unpaid_order_returns_400(self):
        unpaid = _make_paid_order_43(paid=False, status=Order.Status.PENDING_PAYMENT, stripe_session_id=None, stripe_payment_intent_id='pi_43_unpaid')
        self.client.force_authenticate(user=self.admin)
        resp = self.client.post(f'/api/admin/orders/{unpaid.pk}/resend-confirmation-email/')
        self.assertEqual(resp.status_code, 400)

    def test_nonexistent_order_returns_404(self):
        self.client.force_authenticate(user=self.admin)
        resp = self.client.post('/api/admin/orders/99999999/resend-confirmation-email/')
        self.assertEqual(resp.status_code, 404)

    # --- HTTP method restrictions ---

    def test_get_method_not_allowed(self):
        self.client.force_authenticate(user=self.admin)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 405)

    def test_put_method_not_allowed(self):
        self.client.force_authenticate(user=self.admin)
        resp = self.client.put(self.url)
        self.assertEqual(resp.status_code, 405)

    def test_patch_method_not_allowed(self):
        self.client.force_authenticate(user=self.admin)
        resp = self.client.patch(self.url)
        self.assertEqual(resp.status_code, 405)

    def test_delete_method_not_allowed(self):
        self.client.force_authenticate(user=self.admin)
        resp = self.client.delete(self.url)
        self.assertEqual(resp.status_code, 405)

    # --- SMTP failure ---

    def test_smtp_failure_returns_502(self):
        import smtplib
        self.client.force_authenticate(user=self.admin)
        with patch('django.core.mail.EmailMultiAlternatives.send', side_effect=smtplib.SMTPException('down')):
            resp = self.client.post(self.url)
        self.assertEqual(resp.status_code, 502)

    def test_502_response_does_not_expose_smtp_secrets(self):
        import smtplib
        self.client.force_authenticate(user=self.admin)
        with patch('django.core.mail.EmailMultiAlternatives.send', side_effect=smtplib.SMTPException('smtp_password=supersecret')):
            resp = self.client.post(self.url)
        self.assertNotIn(b'smtp_password', resp.content)
        self.assertNotIn(b'supersecret', resp.content)

    # --- Email content ---

    def test_resend_sends_email_to_customer(self):
        self.client.force_authenticate(user=self.admin)
        self.client.post(self.url)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(self.order.customer_email, mail.outbox[0].to)

    def test_resend_has_pdf_attachment(self):
        self.client.force_authenticate(user=self.admin)
        self.client.post(self.url)
        msg = mail.outbox[0]
        pdf_attachments = [a for a in msg.attachments if isinstance(a, tuple) and a[2] == 'application/pdf']
        self.assertEqual(len(pdf_attachments), 1)

    def test_response_includes_resent_to_and_had_pdf(self):
        self.client.force_authenticate(user=self.admin)
        resp = self.client.post(self.url)
        data = resp.json()
        self.assertIn('resent_to', data)
        self.assertIn('had_pdf_attachment', data)
        self.assertEqual(data['resent_to'], self.order.customer_email)

    # --- Idempotency bypass ---

    def test_resend_works_when_confirmation_sent_at_already_exists(self):
        """Endpoint must succeed even if email was already sent (idempotency bypassed)."""
        self.order.confirmation_email_sent_at = timezone.now() - timedelta(hours=1)
        self.order.save(update_fields=['confirmation_email_sent_at'])
        self.client.force_authenticate(user=self.admin)
        resp = self.client.post(self.url)
        self.assertEqual(resp.status_code, 200)

    def test_resend_updates_confirmation_email_sent_at(self):
        self.order.confirmation_email_sent_at = None
        self.order.save(update_fields=['confirmation_email_sent_at'])
        self.client.force_authenticate(user=self.admin)
        self.client.post(self.url)
        self.order.refresh_from_db()
        self.assertIsNotNone(self.order.confirmation_email_sent_at)

    # --- Audit log ---

    def test_resend_creates_audit_log(self):
        self.client.force_authenticate(user=self.admin)
        before = AdminAuditLog.objects.filter(action='order_confirmation_email_resent').count()
        self.client.post(self.url)
        after = AdminAuditLog.objects.filter(action='order_confirmation_email_resent').count()
        self.assertEqual(after, before + 1)

    def test_audit_log_metadata_does_not_contain_stripe_ids(self):
        self.client.force_authenticate(user=self.admin)
        self.client.post(self.url)
        log = AdminAuditLog.objects.filter(
            action='order_confirmation_email_resent',
            target_id=str(self.order.pk),
        ).latest('created_at')
        metadata_str = str(log.metadata)
        self.assertNotIn('cs_43_ep', metadata_str)
        self.assertNotIn('pi_43_ep', metadata_str)
        self.assertNotIn('payment_error', metadata_str)
        self.assertIn('order_id', metadata_str)
        self.assertIn('customer_email', metadata_str)

    def test_audit_log_records_had_pdf_attachment(self):
        self.client.force_authenticate(user=self.admin)
        self.client.post(self.url)
        log = AdminAuditLog.objects.filter(
            action='order_confirmation_email_resent',
            target_id=str(self.order.pk),
        ).latest('created_at')
        self.assertIn('had_pdf_attachment', log.metadata)

    # --- Regression: automatic webhook flow unaffected ---

    def test_automatic_confirmation_email_still_uses_idempotency_guard(self):
        """send_order_confirmation_email still skips if confirmation_email_sent_at is set."""
        from store.email_services import send_order_confirmation_email
        self.order.confirmation_email_sent_at = timezone.now()
        self.order.save(update_fields=['confirmation_email_sent_at'])
        result = send_order_confirmation_email(self.order)
        self.assertFalse(result)
        self.assertEqual(len(mail.outbox), 0)

    def test_smtp_failure_records_email_send_error(self):
        """SMTP failure must append resend_smtp_fail: to email_send_error (BUG-2 regression guard)."""
        import smtplib
        self.order.email_send_error = ''
        self.order.save(update_fields=['email_send_error'])
        self.client.force_authenticate(user=self.admin)
        with patch('django.core.mail.EmailMultiAlternatives.send', side_effect=smtplib.SMTPException('down')):
            resp = self.client.post(self.url)
        self.assertEqual(resp.status_code, 502)
        self.order.refresh_from_db()
        self.assertIn('resend_smtp_fail:', self.order.email_send_error)

    def test_smtp_failure_does_not_create_audit_log(self):
        import smtplib
        self.client.force_authenticate(user=self.admin)
        before = AdminAuditLog.objects.filter(action='order_confirmation_email_resent').count()
        with patch('django.core.mail.EmailMultiAlternatives.send', side_effect=smtplib.SMTPException('down')):
            self.client.post(self.url)
        after = AdminAuditLog.objects.filter(action='order_confirmation_email_resent').count()
        self.assertEqual(before, after)

    def test_resend_html_body_does_not_contain_stripe_payment_intent(self):
        """HTML alternative body must also exclude Stripe IDs."""
        self.client.force_authenticate(user=self.admin)
        self.client.post(self.url)
        self.assertEqual(len(mail.outbox), 1)
        msg = mail.outbox[0]
        html_alternatives = [body for body, mime in getattr(msg, 'alternatives', []) if mime == 'text/html']
        self.assertTrue(html_alternatives, "No HTML alternative found")
        self.assertNotIn('pi_43_ep', html_alternatives[0])
        self.assertNotIn('cs_43_ep', html_alternatives[0])

    def test_resend_does_not_modify_paid_status_total(self):
        self.order.refresh_from_db()
        original_paid = self.order.paid
        original_status = self.order.status
        original_total = self.order.total
        self.client.force_authenticate(user=self.admin)
        self.client.post(self.url)
        self.order.refresh_from_db()
        self.assertEqual(self.order.paid, original_paid)
        self.assertEqual(self.order.status, original_status)
        self.assertEqual(self.order.total, original_total)


# ---------------------------------------------------------------------------
# Phase 6.0 — inventory (Kardex), reports and INTERNAL sales notes
# ---------------------------------------------------------------------------

from .inventory_services import (  # noqa: E402
    InsufficientStockError,
    InvalidMovementError,
    apply_manual_stock_movement,
    create_stock_movement,
    get_best_selling_products,
    get_inventory_summary,
    record_sale_stock_movements,
)
from .models import SalesNote, StockMovement  # noqa: E402
from .sales_note_services import (  # noqa: E402
    SalesNoteError,
    generate_sales_note_pdf,
    get_or_create_sales_note,
)


def _p60_users():
    """Create one user per business role. Returns a dict keyed by role name."""
    users = {}
    for role in (
        UserProfile.ROLE_CUSTOMER,
        UserProfile.ROLE_SALES,
        UserProfile.ROLE_INVENTORY,
        UserProfile.ROLE_TECHNICIAN,
        UserProfile.ROLE_ADMIN,
    ):
        u = User.objects.create_user(username=f'p60_{role}', password='Pass123!')
        u.profile.role = role
        u.profile.save()
        users[role] = u
    users[UserProfile.ROLE_SUPERADMIN] = User.objects.create_user(
        username='p60_superadmin', password='Pass123!', is_superuser=True,
    )
    return users


def _p60_product(name='iPhone 15 Pro P60', inventory=10, price='5599.00'):
    return _seeded(Product.objects.create(company=_pilot_company(),
        name=name,
        slug=name.lower().replace(' ', '-'),
        price=Decimal(price),
        inventory=inventory,
    ))


def _p60_paid_order(product, quantity=2, **extra):
    # Phase 2D: a real order carries the branch that sold it — checkout stamps
    # one and migration 0025 backfilled the historical ones. A test order
    # without it would drop out of every branch-scoped sales report.
    extra.setdefault('fulfillment_branch', _pilot_branch())
    order = Order.objects.create(company=_pilot_company(),
        customer_name='Cliente Demo',
        customer_email='cliente@example.com',
        customer_phone='+51 999 999 999',
        document_type=Order.DocumentType.DNI,
        document_number='12345678',
        delivery_method=Order.DeliveryMethod.PICKUP_STORE,
        receipt_type=Order.ReceiptType.BOLETA,
        total=Decimal(product.price) * quantity,
        status=Order.Status.PAID,
        paid=True,
        paid_at=timezone.now(),
        stripe_session_id=extra.pop('stripe_session_id', None),
        stripe_payment_intent_id=extra.pop('stripe_payment_intent_id', ''),
        **extra,
    )
    OrderItem.objects.create(order=order, product=product, quantity=quantity, price=product.price)
    return order


class Phase60InventoryAccessTest(TestCase):
    """RBAC on /api/admin/inventory/ — customer, technician and anonymous are locked out."""

    def setUp(self):
        cache.clear()
        self.users = _p60_users()
        self.product = _p60_product()
        self.anon = APIClient()

    def _as(self, role):
        c = APIClient()
        c.force_authenticate(user=self.users[role])
        return c

    def test_01_anonymous_cannot_read_summary(self):
        self.assertEqual(
            self.anon.get('/api/admin/inventory/summary/').status_code,
            status.HTTP_401_UNAUTHORIZED,
        )

    def test_01b_anonymous_cannot_read_movements(self):
        self.assertEqual(
            self.anon.get('/api/admin/inventory/movements/').status_code,
            status.HTTP_401_UNAUTHORIZED,
        )

    def test_02_customer_cannot_read_summary(self):
        self.assertEqual(
            self._as(UserProfile.ROLE_CUSTOMER).get('/api/admin/inventory/summary/').status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_02b_technician_cannot_read_summary(self):
        self.assertEqual(
            self._as(UserProfile.ROLE_TECHNICIAN).get('/api/admin/inventory/summary/').status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_03_sales_cannot_create_movements(self):
        res = self._as(UserProfile.ROLE_SALES).post('/api/admin/inventory/movements/', {
            'product_id': self.product.pk,
            'movement_type': 'manual_entry',
            'quantity': 5,
            'reason': 'Intento no autorizado',
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(StockMovement.objects.count(), 0)

    def test_03b_customer_cannot_create_movements(self):
        res = self._as(UserProfile.ROLE_CUSTOMER).post('/api/admin/inventory/movements/', {
            'product_id': self.product.pk,
            'movement_type': 'manual_entry',
            'quantity': 5,
            'reason': 'Intento no autorizado',
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_04_inventory_creates_entry(self):
        res = self._as(UserProfile.ROLE_INVENTORY).post('/api/admin/inventory/movements/', {
            'product_id': self.product.pk,
            'movement_type': 'purchase_entry',
            'quantity': 5,
            'reason': 'Compra de stock',
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.product.refresh_from_db()
        self.assertEqual(self.product.inventory, 15)

    def test_05_inventory_creates_exit(self):
        res = self._as(UserProfile.ROLE_INVENTORY).post('/api/admin/inventory/movements/', {
            'product_id': self.product.pk,
            'movement_type': 'manual_exit',
            'quantity': 3,
            'reason': 'Salida por muestra',
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.product.refresh_from_db()
        self.assertEqual(self.product.inventory, 7)

    def test_06_admin_creates_entry(self):
        res = self._as(UserProfile.ROLE_ADMIN).post('/api/admin/inventory/movements/', {
            'product_id': self.product.pk,
            'movement_type': 'manual_entry',
            'quantity': 2,
            'reason': 'Ajuste admin',
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.product.refresh_from_db()
        self.assertEqual(self.product.inventory, 12)

    def test_07_superadmin_creates_exit(self):
        # Phase 2D: a platform master belongs to no tenant, so they name the
        # company explicitly — exactly as they already had to on the catalogue
        # since Phase 2B. Falling back to "the first company" would be the
        # silent cross-tenant leak the whole tenancy layer exists to prevent.
        pilot = _pilot_company().pk
        res = self._as(UserProfile.ROLE_SUPERADMIN).post(
            f'/api/admin/inventory/movements/?company={pilot}', {
                'product_id': self.product.pk,
                'movement_type': 'damaged_exit',
                'quantity': 1,
                'reason': 'Equipo dañado en tienda',
            }, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.product.refresh_from_db()
        self.assertEqual(self.product.inventory, 9)

    def test_sale_exit_cannot_be_created_manually(self):
        res = self._as(UserProfile.ROLE_ADMIN).post('/api/admin/inventory/movements/', {
            'product_id': self.product.pk,
            'movement_type': 'sale_exit',
            'quantity': 1,
            'reason': 'Intento de salida por venta manual',
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(StockMovement.objects.count(), 0)


class Phase60StockMovementRulesTest(TestCase):
    """Validation rules on manual movements."""

    def setUp(self):
        cache.clear()
        self.users = _p60_users()
        self.product = _p60_product(inventory=4)
        self.client = APIClient()
        self.client.force_authenticate(user=self.users[UserProfile.ROLE_INVENTORY])

    def test_08_exit_that_would_go_negative_fails(self):
        res = self.client.post('/api/admin/inventory/movements/', {
            'product_id': self.product.pk,
            'movement_type': 'manual_exit',
            'quantity': 99,
            'reason': 'Salida excesiva',
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.product.refresh_from_db()
        self.assertEqual(self.product.inventory, 4)
        self.assertEqual(StockMovement.objects.count(), 0)

    def test_08b_service_layer_raises_on_negative_stock(self):
        with self.assertRaises(InsufficientStockError):
            create_stock_movement(
                branch=_pilot_branch(),
                product_id=self.product.pk,
                movement_type=StockMovement.MANUAL_EXIT,
                quantity=99,
                reason='Salida excesiva',
            )
        self.product.refresh_from_db()
        self.assertEqual(self.product.inventory, 4)

    def test_09_quantity_zero_fails(self):
        res = self.client.post('/api/admin/inventory/movements/', {
            'product_id': self.product.pk,
            'movement_type': 'manual_entry',
            'quantity': 0,
            'reason': 'Cantidad inválida',
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(StockMovement.objects.count(), 0)

    def test_09b_negative_quantity_fails(self):
        res = self.client.post('/api/admin/inventory/movements/', {
            'product_id': self.product.pk,
            'movement_type': 'manual_entry',
            'quantity': -5,
            'reason': 'Cantidad inválida',
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_10_empty_reason_fails(self):
        res = self.client.post('/api/admin/inventory/movements/', {
            'product_id': self.product.pk,
            'movement_type': 'manual_entry',
            'quantity': 1,
            'reason': '   ',
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(StockMovement.objects.count(), 0)

    def test_10b_manual_movement_without_actor_is_rejected(self):
        with self.assertRaises(InvalidMovementError):
            apply_manual_stock_movement(
                branch=_pilot_branch(),
                product_id=self.product.pk,
                movement_type=StockMovement.MANUAL_ENTRY,
                quantity=1,
                reason='Sin responsable',
                actor=None,
            )

    def test_11_movement_records_stock_before(self):
        res = self.client.post('/api/admin/inventory/movements/', {
            'product_id': self.product.pk,
            'movement_type': 'manual_entry',
            'quantity': 6,
            'reason': 'Reposición',
        }, format='json')
        self.assertEqual(res.data['stock_before'], 4)

    def test_12_movement_records_stock_after(self):
        res = self.client.post('/api/admin/inventory/movements/', {
            'product_id': self.product.pk,
            'movement_type': 'manual_entry',
            'quantity': 6,
            'reason': 'Reposición',
        }, format='json')
        self.assertEqual(res.data['stock_after'], 10)

    def test_13_movement_updates_product_inventory(self):
        self.client.post('/api/admin/inventory/movements/', {
            'product_id': self.product.pk,
            'movement_type': 'manual_entry',
            'quantity': 6,
            'reason': 'Reposición',
        }, format='json')
        self.product.refresh_from_db()
        self.assertEqual(self.product.inventory, 10)

    def test_14_movement_creates_audit_log(self):
        self.client.post('/api/admin/inventory/movements/', {
            'product_id': self.product.pk,
            'movement_type': 'manual_entry',
            'quantity': 6,
            'reason': 'Reposición',
        }, format='json')
        log = AdminAuditLog.objects.filter(action='stock_entry_created').first()
        self.assertIsNotNone(log)
        self.assertEqual(log.actor, self.users[UserProfile.ROLE_INVENTORY])
        self.assertEqual(log.metadata['stock_before'], 4)
        self.assertEqual(log.metadata['stock_after'], 10)
        self.assertEqual(log.metadata['reason'], 'Reposición')

    def test_14b_exit_creates_exit_audit_log(self):
        self.client.post('/api/admin/inventory/movements/', {
            'product_id': self.product.pk,
            'movement_type': 'manual_exit',
            'quantity': 1,
            'reason': 'Muestra',
        }, format='json')
        self.assertTrue(AdminAuditLog.objects.filter(action='stock_exit_created').exists())

    def test_14c_audit_metadata_has_no_payment_data(self):
        self.client.post('/api/admin/inventory/movements/', {
            'product_id': self.product.pk,
            'movement_type': 'manual_entry',
            'quantity': 1,
            'reason': 'Reposición',
        }, format='json')
        log = AdminAuditLog.objects.filter(action='stock_entry_created').first()
        raw = str(log.metadata)
        for forbidden in ('stripe', 'payment_intent', 'payment_error', 'cs_test', 'pi_'):
            self.assertNotIn(forbidden, raw.lower())

    def test_movement_records_actor(self):
        res = self.client.post('/api/admin/inventory/movements/', {
            'product_id': self.product.pk,
            'movement_type': 'manual_entry',
            'quantity': 1,
            'reason': 'Reposición',
        }, format='json')
        movement = StockMovement.objects.get(pk=res.data['id'])
        self.assertEqual(movement.actor, self.users[UserProfile.ROLE_INVENTORY])
        self.assertEqual(movement.reference_type, 'manual')


class Phase60SaleStockMovementTest(TestCase):
    """Sale exits from the Stripe webhook: created once, never duplicated."""

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.product = _p60_product(name='AirPods P60', inventory=10, price='999.00')
        self.session_key = 'p60-webhook-session'
        self.order = Order.objects.create(company=_pilot_company(),
            customer_email='p60@example.com',
            total=Decimal('1998.00'),
            cart_session_key=self.session_key,
            stripe_session_id='cs_test_p60_001',
            status=Order.Status.PENDING_PAYMENT,
        )
        OrderItem.objects.create(
            order=self.order, product=self.product, quantity=2, price=self.product.price,
        )
        CartItem.objects.create(session_key=self.session_key, product=self.product, quantity=2)

    def _post_webhook(self, session_id='cs_test_p60_001'):
        event = {
            'type': 'checkout.session.completed',
            'data': {'object': {'id': session_id, 'payment_intent': 'pi_test_p60'}},
        }
        with patch('stripe.Webhook.construct_event', return_value=event):
            return self.client.post(
                '/api/payments/webhook/',
                data=b'{}',
                content_type='application/json',
                HTTP_STRIPE_SIGNATURE='t=1,v1=fake',
            )

    def test_15_webhook_creates_sale_exit_movements(self):
        self._post_webhook()
        movements = StockMovement.objects.filter(
            order=self.order, movement_type=StockMovement.SALE_EXIT,
        )
        self.assertEqual(movements.count(), 1)
        m = movements.first()
        self.assertEqual(m.quantity, 2)
        self.assertEqual(m.stock_before, 10)
        self.assertEqual(m.stock_after, 8)

    def test_16_duplicate_webhook_does_not_duplicate_movements(self):
        self._post_webhook()
        self._post_webhook()
        self._post_webhook()
        self.assertEqual(
            StockMovement.objects.filter(
                order=self.order, movement_type=StockMovement.SALE_EXIT,
            ).count(),
            1,
        )

    def test_17_stock_not_decremented_twice(self):
        self._post_webhook()
        self._post_webhook()
        self.product.refresh_from_db()
        self.assertEqual(self.product.inventory, 8)

    def test_17b_service_is_idempotent_when_called_directly(self):
        record_sale_stock_movements(self.order)
        record_sale_stock_movements(self.order)
        self.product.refresh_from_db()
        self.assertEqual(self.product.inventory, 8)
        self.assertEqual(StockMovement.objects.filter(order=self.order).count(), 1)

    def test_17c_sale_movement_links_the_order(self):
        self._post_webhook()
        m = StockMovement.objects.get(order=self.order)
        self.assertEqual(m.reference_type, 'order')
        self.assertEqual(m.reference_id, str(self.order.pk))
        self.assertIsNone(m.actor)

    def test_17d_insufficient_stock_flags_order_without_breaking_payment(self):
        # Stock disappears between checkout and confirmation.
        # Phase 2D: emptying the SHELF, not the compatibility aggregate —
        # `Product.inventory` is derived now, and zeroing it would leave the
        # branch holding units that the webhook would happily sell.
        from .models import BranchStock
        BranchStock.objects.filter(product=self.product).update(quantity=0)
        Product.objects.filter(pk=self.product.pk).update(inventory=0)
        res = self._post_webhook()
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PAID)
        self.assertIn('Stock insuficiente', self.order.payment_error)
        self.product.refresh_from_db()
        self.assertEqual(self.product.inventory, 0)

    def test_17e_webhook_still_marks_order_paid_and_clears_cart(self):
        self._post_webhook()
        self.order.refresh_from_db()
        self.assertTrue(self.order.paid)
        self.assertEqual(self.order.status, Order.Status.PAID)
        self.assertEqual(CartItem.objects.filter(session_key=self.session_key).count(), 0)


class Phase60ReportsTest(TestCase):
    """Kardex, low/high stock, best-selling and summary."""

    def setUp(self):
        cache.clear()
        self.users = _p60_users()
        self.client = APIClient()
        self.client.force_authenticate(user=self.users[UserProfile.ROLE_INVENTORY])

        # The 0002_initial_data migration seeds demo products; drop them so the
        # summary assertions below describe exactly the fixtures created here.
        _reset_products()

        self.low = _p60_product(name='Producto Bajo P60', inventory=2, price='100.00')
        self.high = _p60_product(name='Producto Alto P60', inventory=50, price='200.00')
        self.zero = _p60_product(name='Producto Agotado P60', inventory=0, price='300.00')

    def test_18_stock_card_returns_movements_newest_first(self):
        for qty in (1, 2, 3):
            create_stock_movement(
                branch=_pilot_branch(),
                product_id=self.low.pk,
                movement_type=StockMovement.MANUAL_ENTRY,
                quantity=qty,
                reason=f'Entrada {qty}',
            )
        res = self.client.get(f'/api/admin/products/{self.low.pk}/stock-card/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.data['movements']), 3)
        self.assertEqual(res.data['current_stock'], 2 + 1 + 2 + 3)
        quantities = [m['quantity'] for m in res.data['movements']]
        self.assertEqual(quantities, [3, 2, 1])

    def test_18b_stock_card_404_for_unknown_product(self):
        self.assertEqual(
            self.client.get('/api/admin/products/999999/stock-card/').status_code,
            status.HTTP_404_NOT_FOUND,
        )

    def test_19_low_stock_report(self):
        res = self.client.get('/api/admin/inventory/low-stock/?threshold=5')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        # Phase 2D: the report answers with BranchStock rows — the same product
        # can be low in one branch and fine in another, so a row is a
        # (product, branch) pair rather than a product.
        names = [r['product_name'] for r in res.data['results']]
        self.assertIn('Producto Bajo P60', names)
        self.assertIn('Producto Agotado P60', names)
        self.assertNotIn('Producto Alto P60', names)

    def test_20_high_stock_report(self):
        res = self.client.get('/api/admin/inventory/high-stock/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['results'][0]['product_name'], 'Producto Alto P60')

    def test_21_best_selling_report(self):
        _p60_paid_order(self.high, quantity=4)
        _p60_paid_order(self.low, quantity=1)
        res = self.client.get('/api/admin/inventory/best-selling/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        top = res.data['results'][0]
        self.assertEqual(top['product_name'], 'Producto Alto P60')
        self.assertEqual(top['units_sold'], 4)
        self.assertEqual(top['revenue'], '800.00')

    def test_21b_best_selling_ignores_unpaid_orders(self):
        order = Order.objects.create(company=_pilot_company(),
            customer_email='pending@example.com',
            total=Decimal('200.00'),
            status=Order.Status.PENDING_PAYMENT,
        )
        OrderItem.objects.create(order=order, product=self.high, quantity=9, price=self.high.price)
        rows = get_best_selling_products()
        self.assertEqual(rows, [])

    def test_22_summary_computes_total_units(self):
        res = self.client.get('/api/admin/inventory/summary/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['total_units'], 52)  # 2 + 50, agotado no suma

    def test_23_summary_computes_inventory_value(self):
        res = self.client.get('/api/admin/inventory/summary/')
        # 2*100 + 50*200 = 10200.00 ; el agotado no aporta valor
        self.assertEqual(res.data['inventory_value'], '10200.00')

    def test_23b_summary_counts_out_of_stock_and_low_stock(self):
        res = self.client.get('/api/admin/inventory/summary/?threshold=5')
        self.assertEqual(res.data['out_of_stock_count'], 1)
        self.assertEqual(res.data['low_stock_count'], 1)

    def test_23c_movements_list_filters_by_product(self):
        create_stock_movement(
            branch=_pilot_branch(),
            product_id=self.low.pk, movement_type=StockMovement.MANUAL_ENTRY,
            quantity=1, reason='x',
        )
        create_stock_movement(
            branch=_pilot_branch(),
            product_id=self.high.pk, movement_type=StockMovement.MANUAL_ENTRY,
            quantity=1, reason='y',
        )
        res = self.client.get(f'/api/admin/inventory/movements/?product={self.low.pk}')
        self.assertEqual(res.data['count'], 1)
        self.assertEqual(res.data['results'][0]['product'], self.low.pk)

    def test_23d_movements_list_filters_by_type(self):
        create_stock_movement(
            branch=_pilot_branch(),
            product_id=self.low.pk, movement_type=StockMovement.MANUAL_ENTRY,
            quantity=1, reason='x',
        )
        create_stock_movement(
            branch=_pilot_branch(),
            product_id=self.low.pk, movement_type=StockMovement.MANUAL_EXIT,
            quantity=1, reason='y',
        )
        res = self.client.get('/api/admin/inventory/movements/?movement_type=manual_exit')
        self.assertEqual(res.data['count'], 1)

    def test_23e_movements_list_is_paginated(self):
        for i in range(30):
            create_stock_movement(
                branch=_pilot_branch(),
                product_id=self.high.pk, movement_type=StockMovement.MANUAL_ENTRY,
                quantity=1, reason=f'entrada {i}',
            )
        res = self.client.get('/api/admin/inventory/movements/?page_size=10')
        self.assertEqual(res.data['count'], 30)
        self.assertEqual(len(res.data['results']), 10)
        self.assertEqual(res.data['page'], 1)

    def test_23f_sales_role_can_read_best_selling(self):
        c = APIClient()
        c.force_authenticate(user=self.users[UserProfile.ROLE_SALES])
        self.assertEqual(
            c.get('/api/admin/inventory/best-selling/').status_code, status.HTTP_200_OK,
        )


class Phase60SalesNoteTest(TestCase):
    """Internal sales notes: issuance rules, numbering, RBAC, PDF and audit."""

    def setUp(self):
        cache.clear()
        self.users = _p60_users()
        self.product = _p60_product(name='iPad P60', inventory=10, price='4999.00')
        self.paid_order = _p60_paid_order(
            self.product, quantity=1,
            stripe_session_id='cs_test_p60_note',
            stripe_payment_intent_id='pi_test_p60_note',
        )
        self.unpaid_order = Order.objects.create(company=_pilot_company(),
            customer_email='pending@example.com',
            total=Decimal('4999.00'),
            status=Order.Status.PENDING_PAYMENT,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.users[UserProfile.ROLE_ADMIN])

    def _as(self, role):
        c = APIClient()
        c.force_authenticate(user=self.users[role])
        return c

    def test_24_no_note_for_unpaid_order(self):
        res = self.client.post(f'/api/admin/orders/{self.unpaid_order.pk}/sales-note/')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(SalesNote.objects.count(), 0)

    def test_24b_service_raises_for_unpaid_order(self):
        with self.assertRaises(SalesNoteError):
            get_or_create_sales_note(self.unpaid_order)

    def test_24c_no_note_for_cancelled_order(self):
        self.unpaid_order.status = Order.Status.CANCELLED
        self.unpaid_order.save(update_fields=['status'])
        res = self.client.post(f'/api/admin/orders/{self.unpaid_order.pk}/sales-note/')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_25_note_created_for_paid_order(self):
        res = self.client.post(f'/api/admin/orders/{self.paid_order.pk}/sales-note/')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data['number'], 'NV-000001')
        self.assertEqual(SalesNote.objects.count(), 1)

    def test_26_does_not_duplicate_note_for_same_order(self):
        first = self.client.post(f'/api/admin/orders/{self.paid_order.pk}/sales-note/')
        second = self.client.post(f'/api/admin/orders/{self.paid_order.pk}/sales-note/')
        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(first.data['number'], second.data['number'])
        self.assertEqual(SalesNote.objects.count(), 1)

    def test_27_internal_number_is_unique_and_sequential(self):
        other_product = _p60_product(name='Watch P60', inventory=5, price='1799.00')
        other_order = _p60_paid_order(other_product, quantity=1)

        n1, _ = get_or_create_sales_note(self.paid_order)
        n2, _ = get_or_create_sales_note(other_order)

        self.assertEqual(n1.number, 'NV-000001')
        self.assertEqual(n2.number, 'NV-000002')
        self.assertNotEqual(n1.number, n2.number)

    def test_28_pdf_starts_with_pdf_magic_bytes(self):
        note, _ = get_or_create_sales_note(self.paid_order)
        pdf = generate_sales_note_pdf(note)
        self.assertTrue(pdf.startswith(b'%PDF'))
        self.assertGreater(len(pdf), 1000)

    def test_29_pdf_contains_no_sunat_disclaimer(self):
        note, _ = get_or_create_sales_note(self.paid_order)
        res = self._as(UserProfile.ROLE_ADMIN).get(
            f'/api/admin/orders/{self.paid_order.pk}/sales-note/pdf/'
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res['Content-Type'], 'application/pdf')
        # The disclaimer text lives in the service and is rendered into the story
        from .sales_note_services import SALES_NOTE_DISCLAIMER, build_sales_note_context
        self.assertIn('SUNAT', SALES_NOTE_DISCLAIMER)
        self.assertEqual(build_sales_note_context(note)['disclaimer'], SALES_NOTE_DISCLAIMER)

    def test_30_pdf_context_contains_no_stripe_ids(self):
        note, _ = get_or_create_sales_note(self.paid_order)
        from .sales_note_services import build_sales_note_context
        raw = str(build_sales_note_context(note)).lower()
        self.assertNotIn('cs_test', raw)
        self.assertNotIn('pi_test', raw)
        self.assertNotIn('stripe', raw)
        self.assertNotIn('payment_error', raw)

    def test_30b_pdf_bytes_contain_no_stripe_ids_in_cleartext(self):
        note, _ = get_or_create_sales_note(self.paid_order)
        pdf = generate_sales_note_pdf(note)
        self.assertNotIn(b'cs_test_p60_note', pdf)
        self.assertNotIn(b'pi_test_p60_note', pdf)

    def test_30c_serializer_exposes_no_payment_fields(self):
        res = self.client.post(f'/api/admin/orders/{self.paid_order.pk}/sales-note/')
        raw = str(res.data).lower()
        for forbidden in ('stripe', 'payment_error', 'cs_test', 'pi_test'):
            self.assertNotIn(forbidden, raw)

    def test_31_customer_cannot_download(self):
        get_or_create_sales_note(self.paid_order)
        res = self._as(UserProfile.ROLE_CUSTOMER).get(
            f'/api/admin/orders/{self.paid_order.pk}/sales-note/pdf/'
        )
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_31b_anonymous_cannot_download(self):
        get_or_create_sales_note(self.paid_order)
        res = APIClient().get(f'/api/admin/orders/{self.paid_order.pk}/sales-note/pdf/')
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_31c_inventory_cannot_issue_or_download(self):
        c = self._as(UserProfile.ROLE_INVENTORY)
        self.assertEqual(
            c.post(f'/api/admin/orders/{self.paid_order.pk}/sales-note/').status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_32_sales_can_download(self):
        get_or_create_sales_note(self.paid_order)
        res = self._as(UserProfile.ROLE_SALES).get(
            f'/api/admin/orders/{self.paid_order.pk}/sales-note/pdf/'
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn('attachment;', res['Content-Disposition'])

    def test_33_admin_can_download(self):
        get_or_create_sales_note(self.paid_order)
        res = self._as(UserProfile.ROLE_ADMIN).get(
            f'/api/admin/orders/{self.paid_order.pk}/sales-note/pdf/'
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)

    def test_34_audit_log_sales_note_created(self):
        self.client.post(f'/api/admin/orders/{self.paid_order.pk}/sales-note/')
        log = AdminAuditLog.objects.filter(action='sales_note_created').first()
        self.assertIsNotNone(log)
        self.assertEqual(log.metadata['sales_note_number'], 'NV-000001')
        self.assertEqual(log.metadata['order_id'], self.paid_order.pk)
        raw = str(log.metadata).lower()
        self.assertNotIn('stripe', raw)

    def test_34b_audit_log_not_duplicated_on_second_post(self):
        self.client.post(f'/api/admin/orders/{self.paid_order.pk}/sales-note/')
        self.client.post(f'/api/admin/orders/{self.paid_order.pk}/sales-note/')
        self.assertEqual(
            AdminAuditLog.objects.filter(action='sales_note_created').count(), 1,
        )

    def test_35_audit_log_sales_note_pdf_downloaded(self):
        get_or_create_sales_note(self.paid_order)
        self.client.get(f'/api/admin/orders/{self.paid_order.pk}/sales-note/pdf/')
        log = AdminAuditLog.objects.filter(action='sales_note_pdf_downloaded').first()
        self.assertIsNotNone(log)
        self.assertEqual(log.metadata['sales_note_number'], 'NV-000001')

    def test_36_note_does_not_modify_payment(self):
        before = {
            'status': self.paid_order.status,
            'paid': self.paid_order.paid,
            'total': self.paid_order.total,
            'stripe_session_id': self.paid_order.stripe_session_id,
        }
        self.client.post(f'/api/admin/orders/{self.paid_order.pk}/sales-note/')
        self.paid_order.refresh_from_db()
        self.assertEqual(self.paid_order.status, before['status'])
        self.assertEqual(self.paid_order.paid, before['paid'])
        self.assertEqual(self.paid_order.total, before['total'])
        self.assertEqual(self.paid_order.stripe_session_id, before['stripe_session_id'])

    def test_37_note_does_not_modify_inventory(self):
        before = self.product.inventory
        self.client.post(f'/api/admin/orders/{self.paid_order.pk}/sales-note/')
        self.client.get(f'/api/admin/orders/{self.paid_order.pk}/sales-note/pdf/')
        self.product.refresh_from_db()
        self.assertEqual(self.product.inventory, before)
        self.assertEqual(StockMovement.objects.count(), 0)

    def test_get_returns_404_when_no_note_issued(self):
        res = self.client.get(f'/api/admin/orders/{self.paid_order.pk}/sales-note/')
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_get_returns_note_after_issuance(self):
        self.client.post(f'/api/admin/orders/{self.paid_order.pk}/sales-note/')
        res = self.client.get(f'/api/admin/orders/{self.paid_order.pk}/sales-note/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['number'], 'NV-000001')
        self.assertIn('SUNAT', res.data['notice'])


class Phase60RegressionTest(TestCase):
    """Phase 6.0 must not disturb checkout, payments, admin or existing PDFs."""

    def setUp(self):
        cache.clear()
        self.users = _p60_users()
        self.product = _p60_product(name='Regression P60', inventory=20, price='1000.00')
        self.client = APIClient()

    def test_38_checkout_still_creates_pending_order(self):
        CartItem.objects.create(session_key='p60-reg-cart', product=self.product, quantity=1)
        with patch('stripe.checkout.Session.create') as mock_create:
            mock_create.return_value = MagicMock(id='cs_test_reg_p60', url='https://stripe.test/x')
            res = self.client.post('/api/payments/create-checkout-session/', {
                'session_key': 'p60-reg-cart',
                'customer_name': 'Cliente Regresión',
                'customer_email': 'reg@example.com',
                'customer_phone': '999999999',
                'document_type': 'dni',
                'document_number': '12345678',
                'delivery_method': 'pickup_store',
                'receipt_type': 'boleta',
                'accepted_terms': True,
                'accepted_warranty_policy': True,
            }, format='json')
        self.assertIn(res.status_code, (status.HTTP_200_OK, status.HTTP_201_CREATED))
        order = Order.objects.get(stripe_session_id='cs_test_reg_p60')
        self.assertEqual(order.status, Order.Status.PENDING_PAYMENT)
        # No stock movement before payment is confirmed
        self.assertEqual(StockMovement.objects.filter(order=order).count(), 0)
        self.product.refresh_from_db()
        self.assertEqual(self.product.inventory, 20)

    def test_39_webhook_still_marks_order_paid(self):
        order = Order.objects.create(company=_pilot_company(),
            customer_email='reg2@example.com',
            total=Decimal('1000.00'),
            stripe_session_id='cs_test_reg_p60_2',
            status=Order.Status.PENDING_PAYMENT,
        )
        OrderItem.objects.create(order=order, product=self.product, quantity=1, price=self.product.price)
        event = {
            'type': 'checkout.session.completed',
            'data': {'object': {'id': 'cs_test_reg_p60_2', 'payment_intent': 'pi_reg_p60'}},
        }
        with patch('stripe.Webhook.construct_event', return_value=event):
            res = self.client.post(
                '/api/payments/webhook/', data=b'{}',
                content_type='application/json', HTTP_STRIPE_SIGNATURE='t=1,v1=fake',
            )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PAID)
        self.assertEqual(order.stripe_payment_intent_id, 'pi_reg_p60')

    def test_40_payment_status_view_still_works(self):
        order = Order.objects.create(company=_pilot_company(),
            customer_email='reg3@example.com',
            total=Decimal('1000.00'),
            stripe_session_id='cs_test_reg_p60_3',
            status=Order.Status.PAID,
            paid=True,
        )
        res = self.client.get('/api/payments/status/?session_id=cs_test_reg_p60_3')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['order_id'], order.pk)

    def test_41_admin_products_still_works(self):
        c = APIClient()
        c.force_authenticate(user=self.users[UserProfile.ROLE_ADMIN])
        res = c.get('/api/admin/products/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)

    def test_41b_legacy_inventory_adjust_endpoint_still_works(self):
        c = APIClient()
        c.force_authenticate(user=self.users[UserProfile.ROLE_INVENTORY])
        res = c.post(f'/api/admin/products/{self.product.pk}/inventory-adjust/', {
            'delta': 5, 'reason': 'Ajuste heredado',
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.product.refresh_from_db()
        self.assertEqual(self.product.inventory, 25)

    def test_42_admin_orders_still_works(self):
        c = APIClient()
        c.force_authenticate(user=self.users[UserProfile.ROLE_ADMIN])
        res = c.get('/api/admin/orders/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)

    def test_44_existing_order_receipt_pdf_still_works(self):
        order = _p60_paid_order(self.product, quantity=1)
        c = APIClient()
        c.force_authenticate(user=self.users[UserProfile.ROLE_ADMIN])
        res = c.get(f'/api/admin/orders/{order.pk}/receipt-pdf/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res['Content-Type'], 'application/pdf')
        self.assertTrue(res.content.startswith(b'%PDF'))

    def test_45_public_catalog_still_works(self):
        res = self.client.get('/api/products/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# SaaS Phase 1 — multi-tenant foundation
# ---------------------------------------------------------------------------

from django.core.exceptions import ValidationError as DjangoValidationError  # noqa: E402
from django.db import IntegrityError, transaction  # noqa: E402

from .models import Branch, Company, Membership  # noqa: E402
from .tenancy import (  # noqa: E402
    CrossTenantError,
    NoTenantError,
    active_memberships,
    assert_branch_in_company,
    has_company_access,
    is_company_admin,
    is_platform_admin,
    resolve_company_for_user,
    resolve_company_from_host,
    scope_queryset,
    visible_companies,
)


def _saas_company(name='Empresa A', slug='empresa-a', **extra):
    return Company.objects.create(
        name=name,
        legal_name=extra.pop('legal_name', f'{name} S.A.C.'),
        tax_id=extra.pop('tax_id', '20000000001'),
        slug=slug,
        **extra,
    )


def _saas_branch(company, name='Sucursal principal', **extra):
    return Branch.objects.create(company=company, name=name, **extra)


def _saas_user(username, **extra):
    return User.objects.create_user(username=username, password='Pass123!', **extra)


class SaasCompanyModelTest(TestCase):
    """Company: creation, uniqueness and deactivation semantics."""

    def test_company_creation_is_valid(self):
        c = _saas_company()
        self.assertTrue(c.is_active)
        self.assertTrue(c.is_operational)
        self.assertEqual(str(c), 'Empresa A')
        self.assertIsNotNone(c.created_at)

    def test_slug_is_unique(self):
        _saas_company(slug='duplicada')
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                _saas_company(name='Otra', slug='duplicada')

    def test_deactivation_preserves_history(self):
        c = _saas_company()
        branch = _saas_branch(c)
        user = _saas_user('saas_hist')
        membership = Membership.objects.create(user=user, company=c, role='admin')

        c.is_active = False
        c.save(update_fields=['is_active'])
        c.refresh_from_db()

        self.assertFalse(c.is_operational)
        # Nothing was cascaded away
        self.assertTrue(Branch.objects.filter(pk=branch.pk).exists())
        self.assertTrue(Membership.objects.filter(pk=membership.pk).exists())

    def test_company_with_operations_cannot_be_deleted(self):
        c = _saas_company()
        _saas_branch(c)
        # PROTECT on Branch.company blocks the delete
        with self.assertRaises(Exception):
            with transaction.atomic():
                c.delete()
        self.assertTrue(Company.objects.filter(pk=c.pk).exists())

    def test_pilot_company_exists_from_data_migration(self):
        """The seed migration creates the first tenant — not a code constant."""
        pilot = Company.objects.filter(slug='black-dog-store').first()
        self.assertIsNotNone(pilot)
        self.assertEqual(pilot.legal_name, 'CMAU CORP E.I.R.L.')
        self.assertEqual(pilot.tax_id, '20610159886')
        self.assertTrue(pilot.branches.exists())

    def test_no_company_name_constant_in_business_layer(self):
        """The pilot's identity must live in the DB, not in importable code."""
        from store import tenancy, permissions as perms_mod
        for module in (tenancy, perms_mod):
            for attr in dir(module):
                if attr.isupper():
                    value = getattr(module, attr)
                    if isinstance(value, str):
                        self.assertNotIn('Black Dog', value)


class SaasBranchModelTest(TestCase):
    """Branch: ownership by exactly one company."""

    def setUp(self):
        self.company_a = _saas_company('Empresa A', 'empresa-a')
        self.company_b = _saas_company('Empresa B', 'empresa-b', tax_id='20000000002')

    def test_branch_belongs_to_its_company(self):
        b = _saas_branch(self.company_a)
        self.assertEqual(b.company_id, self.company_a.pk)
        self.assertIn(b, self.company_a.branches.all())
        self.assertNotIn(b, self.company_b.branches.all())

    def test_branch_name_unique_per_company_but_reusable_across_companies(self):
        _saas_branch(self.company_a, name='Tienda centro')
        # Same name in another company is fine
        _saas_branch(self.company_b, name='Tienda centro')
        # Same name twice in the same company is not
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                _saas_branch(self.company_a, name='Tienda centro')

    def test_branch_cannot_belong_to_two_companies(self):
        """The FK is single-valued: reassigning moves it, it never belongs to both."""
        b = _saas_branch(self.company_a)
        self.assertEqual(Company.objects.filter(branches=b).count(), 1)

    def test_assert_branch_in_company_rejects_foreign_branch(self):
        b = _saas_branch(self.company_a)
        assert_branch_in_company(b, self.company_a)  # no raise
        with self.assertRaises(CrossTenantError):
            assert_branch_in_company(b, self.company_b)


class SaasMembershipModelTest(TestCase):
    """Membership: user+company pairing, roles and activity."""

    def setUp(self):
        self.company_a = _saas_company('Empresa A', 'empresa-a')
        self.company_b = _saas_company('Empresa B', 'empresa-b', tax_id='20000000002')
        self.branch_a = _saas_branch(self.company_a)
        self.branch_b = _saas_branch(self.company_b)
        self.user = _saas_user('saas_member')

    def test_membership_links_user_and_company(self):
        m = Membership.objects.create(user=self.user, company=self.company_a, role='sales')
        self.assertEqual(m.user, self.user)
        self.assertEqual(m.company, self.company_a)
        self.assertTrue(m.grants_business_access)

    def test_all_existing_roles_are_valid(self):
        valid = {r[0] for r in Membership.ROLE_CHOICES}
        self.assertEqual(
            valid,
            {'customer', 'sales', 'inventory', 'technician', 'admin', 'superadmin'},
        )

    def test_duplicate_membership_per_company_is_rejected(self):
        Membership.objects.create(user=self.user, company=self.company_a, role='sales')
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Membership.objects.create(user=self.user, company=self.company_a, role='admin')

    def test_same_user_may_belong_to_several_companies(self):
        Membership.objects.create(user=self.user, company=self.company_a, role='sales')
        Membership.objects.create(user=self.user, company=self.company_b, role='inventory')
        self.assertEqual(Membership.objects.filter(user=self.user).count(), 2)

    def test_inactive_membership_grants_nothing(self):
        m = Membership.objects.create(
            user=self.user, company=self.company_a, role='admin', is_active=False,
        )
        self.assertFalse(m.grants_business_access)
        self.assertFalse(has_company_access(self.user, self.company_a))
        self.assertEqual(active_memberships(self.user).count(), 0)

    def test_membership_in_deactivated_company_grants_nothing(self):
        Membership.objects.create(user=self.user, company=self.company_a, role='admin')
        self.company_a.is_active = False
        self.company_a.save(update_fields=['is_active'])
        self.assertFalse(has_company_access(self.user, self.company_a))

    def test_branch_from_another_company_is_rejected(self):
        with self.assertRaises(DjangoValidationError):
            Membership.objects.create(
                user=self.user, company=self.company_a, role='sales', branch=self.branch_b,
            )

    def test_branch_from_own_company_is_accepted(self):
        m = Membership.objects.create(
            user=self.user, company=self.company_a, role='sales', branch=self.branch_a,
        )
        self.assertEqual(m.branch, self.branch_a)


class SaasTenancyResolutionTest(TestCase):
    """Tenant resolution never widens access based on client input."""

    def setUp(self):
        self.company_a = _saas_company('Empresa A', 'empresa-a')
        self.company_b = _saas_company('Empresa B', 'empresa-b', tax_id='20000000002')
        self.user_a = _saas_user('saas_res_a')
        self.user_b = _saas_user('saas_res_b')
        self.platform = _saas_user('saas_platform', is_superuser=True)
        Membership.objects.create(user=self.user_a, company=self.company_a, role='admin')
        Membership.objects.create(user=self.user_b, company=self.company_b, role='admin')

    def test_single_membership_resolves_without_client_input(self):
        self.assertEqual(resolve_company_for_user(self.user_a), self.company_a)

    def test_user_without_membership_raises(self):
        orphan = _saas_user('saas_orphan')
        with self.assertRaises(NoTenantError):
            resolve_company_for_user(orphan)

    def test_multiple_memberships_require_explicit_choice(self):
        Membership.objects.create(user=self.user_a, company=self.company_b, role='sales')
        with self.assertRaises(NoTenantError):
            resolve_company_for_user(self.user_a)

    def test_requested_company_id_cannot_widen_access(self):
        """Passing another tenant's id must not resolve to that tenant."""
        with self.assertRaises(CrossTenantError):
            resolve_company_for_user(self.user_a, requested_company_id=self.company_b.pk)

    def test_requested_company_id_selects_among_own_companies(self):
        Membership.objects.create(user=self.user_a, company=self.company_b, role='sales')
        resolved = resolve_company_for_user(self.user_a, requested_company_id=self.company_b.pk)
        self.assertEqual(resolved, self.company_b)

    def test_nonexistent_and_foreign_ids_are_indistinguishable(self):
        """Error text must not reveal whether an id belongs to another tenant."""
        with self.assertRaises(CrossTenantError) as foreign:
            resolve_company_for_user(self.user_a, requested_company_id=self.company_b.pk)
        with self.assertRaises(CrossTenantError) as missing:
            resolve_company_for_user(self.user_a, requested_company_id=999999)
        self.assertEqual(str(foreign.exception), str(missing.exception))

    def test_platform_admin_is_recognised(self):
        self.assertTrue(is_platform_admin(self.platform))
        self.assertFalse(is_platform_admin(self.user_a))
        self.assertTrue(has_company_access(self.platform, self.company_a))
        self.assertTrue(has_company_access(self.platform, self.company_b))

    def test_company_admin_is_scoped_to_own_company(self):
        self.assertTrue(is_company_admin(self.user_a, self.company_a))
        self.assertFalse(is_company_admin(self.user_a, self.company_b))

    def test_scope_queryset_returns_none_for_user_without_membership(self):
        orphan = _saas_user('saas_orphan2')
        qs = scope_queryset(Membership.objects.all(), orphan)
        self.assertEqual(qs.count(), 0)

    def test_scope_queryset_restricts_to_own_company(self):
        qs = scope_queryset(Membership.objects.all(), self.user_a)
        self.assertEqual(list(qs.values_list('company_id', flat=True)), [self.company_a.pk])

    def test_visible_companies_excludes_other_tenants(self):
        self.assertEqual(list(visible_companies(self.user_a)), [self.company_a])
        self.assertEqual(visible_companies(self.platform).count(), Company.objects.count())

    def test_resolve_company_from_host_maps_subdomain_to_slug(self):
        self.assertEqual(resolve_company_from_host('empresa-a.example.com'), self.company_a)
        self.assertEqual(resolve_company_from_host('empresa-a.example.com:443'), self.company_a)

    def test_resolve_company_from_host_ignores_reserved_and_bare_hosts(self):
        for host in ('www.example.com', 'api.example.com', 'example.com', 'localhost', ''):
            self.assertIsNone(resolve_company_from_host(host))

    def test_resolve_company_from_host_skips_inactive_company(self):
        self.company_a.is_active = False
        self.company_a.save(update_fields=['is_active'])
        self.assertIsNone(resolve_company_from_host('empresa-a.example.com'))


class SaasIsolationApiTest(TestCase):
    """Cross-tenant isolation on the multi-tenant admin endpoints."""

    def setUp(self):
        cache.clear()
        self.company_a = _saas_company('Empresa A', 'empresa-a')
        self.company_b = _saas_company('Empresa B', 'empresa-b', tax_id='20000000002')
        self.branch_a = _saas_branch(self.company_a, name='Sucursal A')
        self.branch_b = _saas_branch(self.company_b, name='Sucursal B')

        self.admin_a = _saas_user('saas_admin_a')
        self.admin_b = _saas_user('saas_admin_b')
        self.sales_a = _saas_user('saas_sales_a')
        self.orphan = _saas_user('saas_orphan_api')
        self.platform = _saas_user('saas_platform_api', is_superuser=True)

        self.m_admin_a = Membership.objects.create(
            user=self.admin_a, company=self.company_a, role='admin', branch=self.branch_a,
        )
        self.m_admin_b = Membership.objects.create(
            user=self.admin_b, company=self.company_b, role='admin', branch=self.branch_b,
        )
        self.m_sales_a = Membership.objects.create(
            user=self.sales_a, company=self.company_a, role='sales',
        )

    def _as(self, user):
        c = APIClient()
        c.force_authenticate(user=user)
        return c

    # --- listing isolation ---

    def test_company_a_cannot_see_company_b_memberships(self):
        res = self._as(self.admin_a).get('/api/admin/memberships/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        company_ids = {row['company'] for row in res.data['results']}
        self.assertEqual(company_ids, {self.company_a.pk})

    def test_company_a_cannot_read_company_b_membership_detail(self):
        res = self._as(self.admin_a).get(f'/api/admin/memberships/{self.m_admin_b.pk}/')
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_company_a_cannot_modify_company_b_membership(self):
        res = self._as(self.admin_a).patch(
            f'/api/admin/memberships/{self.m_admin_b.pk}/', {'role': 'customer'}, format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
        self.m_admin_b.refresh_from_db()
        self.assertEqual(self.m_admin_b.role, 'admin')

    def test_company_a_cannot_grant_membership_in_company_b(self):
        """A valid id from another tenant must not become a privilege escalation."""
        res = self._as(self.admin_a).post('/api/admin/memberships/', {
            'user': self.orphan.pk,
            'company': self.company_b.pk,
            'role': 'admin',
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(
            Membership.objects.filter(user=self.orphan, company=self.company_b).exists()
        )

    def test_company_a_cannot_list_company_b_branches(self):
        res = self._as(self.admin_a).get('/api/admin/branches/')
        names = {row['name'] for row in res.data['results']}
        self.assertIn('Sucursal A', names)
        self.assertNotIn('Sucursal B', names)

    def test_company_a_cannot_read_company_b_detail(self):
        res = self._as(self.admin_a).get(f'/api/admin/companies/{self.company_b.pk}/')
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_foreign_company_and_missing_company_answer_identically(self):
        client = self._as(self.admin_a)
        foreign = client.get(f'/api/admin/companies/{self.company_b.pk}/')
        missing = client.get('/api/admin/companies/999999/')
        self.assertEqual(foreign.status_code, missing.status_code)
        self.assertEqual(foreign.data['detail'], missing.data['detail'])

    # --- membership required ---

    def test_user_without_membership_gets_403(self):
        client = self._as(self.orphan)
        for url in ('/api/admin/companies/', '/api/admin/memberships/', '/api/admin/branches/'):
            self.assertEqual(client.get(url).status_code, status.HTTP_403_FORBIDDEN, url)

    def test_inactive_membership_grants_no_access(self):
        self.m_sales_a.is_active = False
        self.m_sales_a.save(update_fields=['is_active'])
        res = self._as(self.sales_a).get('/api/admin/memberships/')
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_membership_in_deactivated_company_grants_no_access(self):
        self.company_a.is_active = False
        self.company_a.save(update_fields=['is_active'])
        res = self._as(self.admin_a).get('/api/admin/memberships/')
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_anonymous_gets_401(self):
        self.assertEqual(
            APIClient().get('/api/admin/memberships/').status_code,
            status.HTTP_401_UNAUTHORIZED,
        )

    # --- write authority inside own company ---

    def test_non_admin_member_cannot_write_membership(self):
        """`sales` can see its company but must not be able to grant roles."""
        res = self._as(self.sales_a).patch(
            f'/api/admin/memberships/{self.m_admin_a.pk}/', {'role': 'customer'}, format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_company_admin_can_grant_inside_own_company(self):
        res = self._as(self.admin_a).post('/api/admin/memberships/', {
            'user': self.orphan.pk,
            'company': self.company_a.pk,
            'role': 'inventory',
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertTrue(
            Membership.objects.filter(user=self.orphan, company=self.company_a).exists()
        )

    def test_duplicate_membership_via_api_is_rejected(self):
        res = self._as(self.admin_a).post('/api/admin/memberships/', {
            'user': self.sales_a.pk,
            'company': self.company_a.pk,
            'role': 'inventory',
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_branch_from_another_company_is_rejected_by_api(self):
        res = self._as(self.admin_a).post('/api/admin/memberships/', {
            'user': self.orphan.pk,
            'company': self.company_a.pk,
            'role': 'sales',
            'branch': self.branch_b.pk,
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_membership_creation_is_audited_with_company(self):
        self._as(self.admin_a).post('/api/admin/memberships/', {
            'user': self.orphan.pk,
            'company': self.company_a.pk,
            'role': 'inventory',
        }, format='json')
        log = AdminAuditLog.objects.filter(action='membership_created').first()
        self.assertIsNotNone(log)
        self.assertEqual(log.company_id, self.company_a.pk)
        self.assertEqual(log.actor, self.admin_a)

    # --- platform administrator ---

    def test_platform_admin_sees_every_tenant(self):
        res = self._as(self.platform).get('/api/admin/companies/')
        self.assertEqual(res.data['count'], Company.objects.count())

    def test_only_platform_admin_can_create_a_company(self):
        payload = {'name': 'Empresa C', 'slug': 'empresa-c', 'tax_id': '20000000003'}
        self.assertEqual(
            self._as(self.admin_a).post('/api/admin/companies/', payload, format='json').status_code,
            status.HTTP_403_FORBIDDEN,
        )
        self.assertEqual(
            self._as(self.platform).post('/api/admin/companies/', payload, format='json').status_code,
            status.HTTP_201_CREATED,
        )

    def test_duplicate_slug_is_rejected_by_api(self):
        res = self._as(self.platform).post('/api/admin/companies/', {
            'name': 'Otra', 'slug': 'empresa-a',
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_company_cannot_be_deleted_through_the_api(self):
        res = self._as(self.platform).delete(f'/api/admin/companies/{self.company_a.pk}/')
        self.assertEqual(res.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertTrue(Company.objects.filter(pk=self.company_a.pk).exists())

    # --- self-scoped endpoint ---

    def test_me_memberships_is_self_scoped(self):
        res = self._as(self.admin_a).get('/api/me/memberships/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['count'], 1)
        self.assertEqual(res.data['results'][0]['company'], self.company_a.pk)
        self.assertFalse(res.data['is_platform_admin'])

    def test_me_memberships_empty_for_user_without_membership(self):
        res = self._as(self.orphan).get('/api/me/memberships/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['count'], 0)


class SaasMembershipBackfillTest(TestCase):
    """The data migration mirrors staff roles into the pilot tenant."""

    def test_pilot_branch_belongs_to_pilot_company(self):
        pilot = Company.objects.get(slug='black-dog-store')
        branch = pilot.branches.first()
        self.assertIsNotNone(branch)
        self.assertEqual(branch.company_id, pilot.pk)

    def test_customers_are_not_given_a_membership(self):
        """A shopper must not become staff of a tenant."""
        customer = _saas_user('saas_backfill_customer')
        self.assertEqual(customer.profile.role, UserProfile.ROLE_CUSTOMER)
        self.assertEqual(Membership.objects.filter(user=customer).count(), 0)

    def test_userprofile_is_untouched_by_the_saas_models(self):
        """UserProfile stays the authoritative role source during the transition."""
        user = _saas_user('saas_backfill_staff')
        user.profile.role = UserProfile.ROLE_INVENTORY
        user.profile.save()
        company = _saas_company('Empresa X', 'empresa-x')
        Membership.objects.create(user=user, company=company, role='admin')

        from .permissions import get_user_role
        # get_user_role still reads UserProfile, not Membership
        self.assertEqual(get_user_role(user), UserProfile.ROLE_INVENTORY)


class SaasNoRegressionTest(TestCase):
    """The existing e-commerce must behave identically with the SaaS models present."""

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.product = _seeded(Product.objects.create(company=_pilot_company(),
            name='Producto SaaS Reg', slug='producto-saas-reg',
            price=Decimal('1000.00'), inventory=20,
        ))
        self.admin = _saas_user('saas_reg_admin')
        self.admin.profile.role = UserProfile.ROLE_ADMIN
        self.admin.profile.save()
        self.inventory_user = _saas_user('saas_reg_inv')
        self.inventory_user.profile.role = UserProfile.ROLE_INVENTORY
        self.inventory_user.profile.save()

    def _admin_client(self):
        c = APIClient()
        c.force_authenticate(user=self.admin)
        return c

    def test_public_catalog_still_works(self):
        res = self.client.get('/api/products/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)

    def test_cart_still_works(self):
        res = self.client.post('/api/cart/add/', {
            'session_key': 'saas-reg-cart', 'product': self.product.pk, 'quantity': 1,
        }, format='json')
        self.assertIn(res.status_code, (status.HTTP_200_OK, status.HTTP_201_CREATED))
        self.assertEqual(CartItem.objects.filter(session_key='saas-reg-cart').count(), 1)

    def test_checkout_still_creates_pending_order(self):
        CartItem.objects.create(session_key='saas-reg-co', product=self.product, quantity=1)
        with patch('stripe.checkout.Session.create') as mock_create:
            mock_create.return_value = MagicMock(id='cs_saas_reg', url='https://stripe.test/x')
            res = self.client.post('/api/payments/create-checkout-session/', {
                'session_key': 'saas-reg-co',
                'customer_name': 'Cliente SaaS',
                'customer_email': 'saas@example.com',
                'customer_phone': '999999999',
                'document_type': 'dni',
                'document_number': '12345678',
                'delivery_method': 'pickup_store',
                'receipt_type': 'boleta',
                'accepted_terms': True,
                'accepted_warranty_policy': True,
            }, format='json')
        self.assertIn(res.status_code, (status.HTTP_200_OK, status.HTTP_201_CREATED))
        order = Order.objects.get(stripe_session_id='cs_saas_reg')
        self.assertEqual(order.status, Order.Status.PENDING_PAYMENT)

    def _fire_webhook(self, session_id):
        event = {
            'type': 'checkout.session.completed',
            'data': {'object': {'id': session_id, 'payment_intent': 'pi_saas_reg'}},
        }
        with patch('stripe.Webhook.construct_event', return_value=event):
            return self.client.post(
                '/api/payments/webhook/', data=b'{}',
                content_type='application/json', HTTP_STRIPE_SIGNATURE='t=1,v1=fake',
            )

    def test_webhook_still_marks_paid_and_remains_idempotent(self):
        order = Order.objects.create(company=_pilot_company(),
            customer_email='saas-wh@example.com', total=Decimal('1000.00'),
            stripe_session_id='cs_saas_wh', status=Order.Status.PENDING_PAYMENT,
        )
        OrderItem.objects.create(order=order, product=self.product, quantity=2, price=self.product.price)

        self._fire_webhook('cs_saas_wh')
        self._fire_webhook('cs_saas_wh')
        self._fire_webhook('cs_saas_wh')

        order.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PAID)
        self.assertTrue(order.paid)
        # No double stock decrement, exactly one Kardex line
        self.assertEqual(self.product.inventory, 18)
        self.assertEqual(
            StockMovement.objects.filter(
                order=order, movement_type=StockMovement.SALE_EXIT,
            ).count(),
            1,
        )

    def test_payment_status_view_still_works(self):
        Order.objects.create(company=_pilot_company(),
            customer_email='saas-ps@example.com', total=Decimal('1000.00'),
            stripe_session_id='cs_saas_ps', status=Order.Status.PAID, paid=True,
        )
        res = self.client.get('/api/payments/status/?session_id=cs_saas_ps')
        self.assertEqual(res.status_code, status.HTTP_200_OK)

    def test_admin_products_and_orders_still_work(self):
        c = self._admin_client()
        self.assertEqual(c.get('/api/admin/products/').status_code, status.HTTP_200_OK)
        self.assertEqual(c.get('/api/admin/orders/').status_code, status.HTTP_200_OK)

    def test_inventory_and_kardex_still_work(self):
        c = APIClient()
        c.force_authenticate(user=self.inventory_user)
        res = c.post('/api/admin/inventory/movements/', {
            'product_id': self.product.pk,
            'movement_type': 'manual_entry',
            'quantity': 5,
            'reason': 'Regresión SaaS',
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.product.refresh_from_db()
        self.assertEqual(self.product.inventory, 25)
        self.assertEqual(
            c.get('/api/admin/inventory/summary/').status_code, status.HTTP_200_OK,
        )
        self.assertEqual(
            c.get(f'/api/admin/products/{self.product.pk}/stock-card/').status_code,
            status.HTTP_200_OK,
        )

    def test_sales_notes_and_pdfs_still_work(self):
        order = _p60_paid_order(self.product, quantity=1)
        c = self._admin_client()

        note_res = c.post(f'/api/admin/orders/{order.pk}/sales-note/')
        self.assertEqual(note_res.status_code, status.HTTP_201_CREATED)

        pdf_res = c.get(f'/api/admin/orders/{order.pk}/sales-note/pdf/')
        self.assertEqual(pdf_res.status_code, status.HTTP_200_OK)
        self.assertTrue(pdf_res.content.startswith(b'%PDF'))

        receipt_res = c.get(f'/api/admin/orders/{order.pk}/receipt-pdf/')
        self.assertEqual(receipt_res.status_code, status.HTTP_200_OK)
        self.assertTrue(receipt_res.content.startswith(b'%PDF'))

    def test_login_still_sets_httponly_cookies(self):
        _saas_user('saas_login_user')
        res = APIClient().post('/api/auth/login/', {
            'username': 'saas_login_user', 'password': 'Pass123!',
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        access = res.cookies.get('blackdog_access')
        self.assertIsNotNone(access)
        self.assertTrue(access['httponly'])

    def test_audit_log_company_is_nullable_and_history_survives(self):
        """Existing audit calls pass no company; they must still work."""
        AdminAuditLog.log(
            actor=self.admin, action='regression_probe',
            target_type='product', target_id=self.product.pk,
        )
        log = AdminAuditLog.objects.filter(action='regression_probe').first()
        self.assertIsNotNone(log)
        self.assertIsNone(log.company_id)


# ---------------------------------------------------------------------------
# Phase 2A — tenant-aware RBAC
# ---------------------------------------------------------------------------

from .permissions import (  # noqa: E402
    CanManageCompanyInventory, CanManageCompanyMemberships, CanManageCompanySales,
    CanManageCompanySettings, CanManageCompanyTechnicalService, get_user_role,
)
from .tenancy import (  # noqa: E402
    CAP_MANAGE_COMPANY, CAP_MANAGE_INVENTORY, CAP_MANAGE_MEMBERSHIPS,
    CAP_MANAGE_SALES, CAP_MANAGE_TECHNICAL_SERVICE, CAP_VIEW_COMPANY,
    COMPANY_CAPABILITIES, GRANTABLE_BY_COMPANY_ADMIN, CompanyContext,
    build_company_context, can_grant_company_role, can_manage_company,
    can_manage_company_inventory, can_manage_company_memberships,
    can_manage_company_sales, can_manage_company_technical_service,
    get_company_role, has_company_capability, has_company_role,
    holds_any_capability,
)


class Phase2aCapabilityMatrixTest(TestCase):
    """The capability matrix is the single source of truth for company roles."""

    def setUp(self):
        self.company = _saas_company('Matriz SA', 'matriz-sa')
        self.users = {}
        for role in ('customer', 'sales', 'inventory', 'technician', 'admin', 'superadmin'):
            u = _saas_user(f'cap_{role}')
            Membership.objects.create(user=u, company=self.company, role=role)
            self.users[role] = u

    def _caps(self, role):
        u = self.users[role]
        return {
            cap for cap in COMPANY_CAPABILITIES
            if has_company_capability(u, self.company, cap)
        }

    def test_customer_is_not_operational_staff(self):
        self.assertEqual(self._caps('customer'), set())

    def test_sales_scope(self):
        self.assertEqual(self._caps('sales'), {CAP_VIEW_COMPANY, CAP_MANAGE_SALES})

    def test_inventory_scope(self):
        self.assertEqual(self._caps('inventory'), {CAP_VIEW_COMPANY, CAP_MANAGE_INVENTORY})

    def test_technician_scope(self):
        self.assertEqual(
            self._caps('technician'), {CAP_VIEW_COMPANY, CAP_MANAGE_TECHNICAL_SERVICE},
        )

    def test_admin_holds_every_capability(self):
        self.assertEqual(self._caps('admin'), set(COMPANY_CAPABILITIES))

    def test_legacy_superadmin_membership_behaves_like_company_admin(self):
        self.assertEqual(self._caps('superadmin'), set(COMPANY_CAPABILITIES))

    def test_named_helpers_agree_with_the_matrix(self):
        pairs = [
            (can_manage_company, CAP_MANAGE_COMPANY),
            (can_manage_company_memberships, CAP_MANAGE_MEMBERSHIPS),
            (can_manage_company_inventory, CAP_MANAGE_INVENTORY),
            (can_manage_company_sales, CAP_MANAGE_SALES),
            (can_manage_company_technical_service, CAP_MANAGE_TECHNICAL_SERVICE),
        ]
        for role, user in self.users.items():
            for helper, cap in pairs:
                self.assertEqual(
                    helper(user, self.company),
                    has_company_capability(user, self.company, cap),
                    f'{helper.__name__} desalineado para rol {role}',
                )

    def test_unknown_capability_raises(self):
        with self.assertRaises(ValueError):
            has_company_capability(self.users['admin'], self.company, 'no_existe')

    def test_permission_classes_reuse_the_matrix(self):
        self.assertEqual(CanManageCompanyMemberships.capability, CAP_MANAGE_MEMBERSHIPS)
        self.assertEqual(CanManageCompanySettings.capability, CAP_MANAGE_COMPANY)
        self.assertEqual(CanManageCompanyInventory.capability, CAP_MANAGE_INVENTORY)
        self.assertEqual(CanManageCompanySales.capability, CAP_MANAGE_SALES)
        self.assertEqual(
            CanManageCompanyTechnicalService.capability, CAP_MANAGE_TECHNICAL_SERVICE,
        )


class Phase2aAuthorityLevelsTest(TestCase):
    """PLATFORM, COMPANY and LEGACY authority stay three separate things."""

    def setUp(self):
        self.company_a = _saas_company('Empresa A', 'auth-a')
        self.company_b = _saas_company('Empresa B', 'auth-b', tax_id='20000000009')
        self.platform = _saas_user('auth_platform', is_superuser=True)

    def test_membership_superadmin_does_not_imply_platform_admin(self):
        u = _saas_user('auth_msuper')
        Membership.objects.create(user=u, company=self.company_a, role='superadmin')
        self.assertFalse(is_platform_admin(u))
        self.assertFalse(u.is_superuser)
        # ...and confers nothing in another company
        self.assertFalse(can_manage_company(u, self.company_b))
        self.assertIsNone(get_company_role(u, self.company_b))

    def test_legacy_userprofile_superadmin_does_not_imply_platform_admin(self):
        u = _saas_user('auth_lsuper')
        u.profile.role = UserProfile.ROLE_SUPERADMIN
        u.profile.save()
        self.assertFalse(is_platform_admin(u))
        self.assertFalse(u.is_superuser)

    def test_legacy_role_grants_nothing_in_the_saas_surface(self):
        """UserProfile.role must never leak into company authority."""
        u = _saas_user('auth_legacy_admin')
        u.profile.role = UserProfile.ROLE_ADMIN
        u.profile.save()
        # Legacy RBAC still sees an admin...
        self.assertEqual(get_user_role(u), UserProfile.ROLE_ADMIN)
        # ...but the SaaS layer sees nothing, because there is no membership.
        self.assertIsNone(get_company_role(u, self.company_a))
        self.assertFalse(can_manage_company(u, self.company_a))
        self.assertFalse(can_manage_company_memberships(u, self.company_a))
        self.assertFalse(holds_any_capability(u, CAP_MANAGE_MEMBERSHIPS))

    def test_platform_admin_has_authority_without_any_membership(self):
        self.assertTrue(is_platform_admin(self.platform))
        self.assertEqual(Membership.objects.filter(user=self.platform).count(), 0)
        self.assertTrue(can_manage_company(self.platform, self.company_a))
        self.assertTrue(can_manage_company(self.platform, self.company_b))

    def test_platform_admin_has_no_company_ROLE(self):
        """Platform authority is not a company role — the two must not merge."""
        self.assertIsNone(get_company_role(self.platform, self.company_a))

    def test_inactive_membership_confers_nothing(self):
        u = _saas_user('auth_inactive')
        Membership.objects.create(
            user=u, company=self.company_a, role='admin', is_active=False,
        )
        self.assertIsNone(get_company_role(u, self.company_a))
        self.assertFalse(can_manage_company(u, self.company_a))
        self.assertFalse(holds_any_capability(u, CAP_MANAGE_MEMBERSHIPS))

    def test_inactive_company_confers_nothing(self):
        u = _saas_user('auth_inactive_co')
        Membership.objects.create(user=u, company=self.company_a, role='admin')
        self.company_a.is_active = False
        self.company_a.save(update_fields=['is_active'])
        self.assertIsNone(get_company_role(u, self.company_a))
        self.assertFalse(can_manage_company(u, self.company_a))

    def test_user_without_membership_has_zero_saas_permissions(self):
        u = _saas_user('auth_orphan')
        for cap in COMPANY_CAPABILITIES:
            self.assertFalse(has_company_capability(u, self.company_a, cap))
            self.assertFalse(holds_any_capability(u, cap))


class Phase2aMultiCompanyRoleTest(TestCase):
    """A user with several memberships keeps a distinct role in each company."""

    def setUp(self):
        self.company_a = _saas_company('Empresa A', 'multi-a')
        self.company_b = _saas_company('Empresa B', 'multi-b', tax_id='20000000010')
        self.company_c = _saas_company('Empresa C', 'multi-c', tax_id='20000000011')
        self.user = _saas_user('multi_user')
        Membership.objects.create(user=self.user, company=self.company_a, role='admin')
        Membership.objects.create(user=self.user, company=self.company_b, role='technician')

    def test_roles_are_per_company(self):
        self.assertEqual(get_company_role(self.user, self.company_a), 'admin')
        self.assertEqual(get_company_role(self.user, self.company_b), 'technician')
        self.assertIsNone(get_company_role(self.user, self.company_c))

    def test_admin_in_a_is_not_admin_in_b(self):
        self.assertTrue(can_manage_company(self.user, self.company_a))
        self.assertFalse(can_manage_company(self.user, self.company_b))

    def test_technician_in_b_is_not_technician_authority_in_a_only(self):
        # admin in A subsumes technical service; technician in B does not manage memberships
        self.assertTrue(can_manage_company_technical_service(self.user, self.company_a))
        self.assertTrue(can_manage_company_technical_service(self.user, self.company_b))
        self.assertTrue(can_manage_company_memberships(self.user, self.company_a))
        self.assertFalse(can_manage_company_memberships(self.user, self.company_b))

    def test_no_global_admin_is_implied(self):
        self.assertFalse(is_platform_admin(self.user))
        self.assertFalse(self.user.is_superuser)

    def test_has_company_role_is_scoped(self):
        self.assertTrue(has_company_role(self.user, self.company_a, ['admin']))
        self.assertFalse(has_company_role(self.user, self.company_b, ['admin']))

    def test_holds_any_capability_is_a_coarse_gate_only(self):
        """Holding a capability somewhere must never imply holding it everywhere."""
        self.assertTrue(holds_any_capability(self.user, CAP_MANAGE_MEMBERSHIPS))
        self.assertFalse(can_manage_company_memberships(self.user, self.company_b))


class Phase2aCompanyContextTest(TestCase):
    """CompanyContext packages authority without trusting client input."""

    def setUp(self):
        self.company_a = _saas_company('Empresa A', 'ctx-a')
        self.company_b = _saas_company('Empresa B', 'ctx-b', tax_id='20000000012')
        self.user = _saas_user('ctx_user')
        Membership.objects.create(user=self.user, company=self.company_a, role='inventory')
        self.platform = _saas_user('ctx_platform', is_superuser=True)

    def test_context_from_single_membership(self):
        ctx = build_company_context(self.user)
        self.assertIsInstance(ctx, CompanyContext)
        self.assertEqual(ctx.company, self.company_a)
        self.assertEqual(ctx.role, 'inventory')
        self.assertFalse(ctx.is_platform_admin)
        self.assertTrue(ctx.can(CAP_MANAGE_INVENTORY))
        self.assertFalse(ctx.can(CAP_MANAGE_MEMBERSHIPS))
        self.assertTrue(ctx.has_role('inventory'))

    def test_context_rejects_a_foreign_company_id(self):
        with self.assertRaises(CrossTenantError):
            build_company_context(self.user, requested_company_id=self.company_b.pk)

    def test_context_requires_a_choice_when_multi_company(self):
        Membership.objects.create(user=self.user, company=self.company_b, role='sales')
        with self.assertRaises(NoTenantError):
            build_company_context(self.user)
        ctx = build_company_context(self.user, requested_company_id=self.company_b.pk)
        self.assertEqual(ctx.role, 'sales')

    def test_platform_admin_context_has_no_role_but_full_authority(self):
        ctx = build_company_context(self.platform, requested_company_id=self.company_a.pk)
        self.assertTrue(ctx.is_platform_admin)
        self.assertIsNone(ctx.role)
        self.assertIsNone(ctx.membership)
        for cap in COMPANY_CAPABILITIES:
            self.assertTrue(ctx.can(cap))

    def test_context_capability_matches_the_matrix(self):
        ctx = build_company_context(self.user)
        for cap in COMPANY_CAPABILITIES:
            self.assertEqual(
                ctx.can(cap), has_company_capability(self.user, self.company_a, cap),
            )

    def test_context_unknown_capability_raises(self):
        with self.assertRaises(ValueError):
            build_company_context(self.user).can('no_existe')


class Phase2aPrivilegeEscalationTest(TestCase):
    """Membership endpoints must not be a path to platform authority."""

    def setUp(self):
        cache.clear()
        self.company_a = _saas_company('Empresa A', 'esc-a')
        self.company_b = _saas_company('Empresa B', 'esc-b', tax_id='20000000013')
        self.branch_a = _saas_branch(self.company_a, name='Sucursal A')

        self.admin_a = _saas_user('esc_admin_a')
        self.sales_a = _saas_user('esc_sales_a')
        self.inventory_a = _saas_user('esc_inv_a')
        self.technician_a = _saas_user('esc_tech_a')
        self.admin_b = _saas_user('esc_admin_b')
        self.target = _saas_user('esc_target')
        self.platform = _saas_user('esc_platform', is_superuser=True)

        Membership.objects.create(user=self.admin_a, company=self.company_a, role='admin')
        Membership.objects.create(user=self.sales_a, company=self.company_a, role='sales')
        Membership.objects.create(user=self.inventory_a, company=self.company_a, role='inventory')
        Membership.objects.create(user=self.technician_a, company=self.company_a, role='technician')
        self.m_admin_b = Membership.objects.create(
            user=self.admin_b, company=self.company_b, role='admin',
        )

    def _as(self, user):
        c = APIClient()
        c.force_authenticate(user=user)
        return c

    def _post(self, actor, **payload):
        # `actor` is who calls; payload keys go into the request body.
        body = {'user': self.target.pk, 'company': self.company_a.pk, 'role': 'sales'}
        body.update(payload)
        return self._as(actor).post('/api/admin/memberships/', body, format='json')

    # --- who may administer memberships ---

    def test_company_admin_may_grant_inside_own_company(self):
        self.assertEqual(self._post(self.admin_a).status_code, status.HTTP_201_CREATED)

    def test_sales_may_not_administer_memberships(self):
        res = self._post(self.sales_a)
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(Membership.objects.filter(user=self.target).exists())

    def test_inventory_may_not_administer_memberships(self):
        self.assertEqual(self._post(self.inventory_a).status_code, status.HTTP_403_FORBIDDEN)

    def test_technician_may_not_administer_memberships(self):
        self.assertEqual(self._post(self.technician_a).status_code, status.HTTP_403_FORBIDDEN)

    def test_other_company_admin_gets_404_not_403(self):
        """Company B's admin must not even learn that company A exists."""
        res = self._post(self.admin_b)
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    # --- superadmin cannot be handed out by a company admin ---

    def test_company_admin_cannot_grant_legacy_superadmin(self):
        res = self._post(self.admin_a, role='superadmin')
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(
            Membership.objects.filter(user=self.target, role='superadmin').exists()
        )

    def test_platform_admin_may_still_grant_legacy_superadmin(self):
        res = self._post(self.platform, role='superadmin')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

    def test_company_admin_cannot_escalate_an_existing_membership_to_superadmin(self):
        m = Membership.objects.create(
            user=self.target, company=self.company_a, role='sales',
        )
        res = self._as(self.admin_a).patch(
            f'/api/admin/memberships/{m.pk}/', {'role': 'superadmin'}, format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        m.refresh_from_db()
        self.assertEqual(m.role, 'sales')

    def test_granting_superadmin_never_touches_is_superuser(self):
        self._post(self.platform, role='superadmin')
        self.target.refresh_from_db()
        self.assertFalse(self.target.is_superuser)
        self.assertFalse(is_platform_admin(self.target))

    def test_grantable_set_excludes_superadmin_for_company_admins(self):
        self.assertNotIn('superadmin', GRANTABLE_BY_COMPANY_ADMIN)
        self.assertFalse(
            can_grant_company_role(self.admin_a, self.company_a, 'superadmin')
        )
        self.assertTrue(
            can_grant_company_role(self.platform, self.company_a, 'superadmin')
        )

    # --- immutable fields ---

    def test_membership_company_cannot_be_changed(self):
        m = Membership.objects.create(user=self.target, company=self.company_a, role='sales')
        res = self._as(self.admin_a).patch(
            f'/api/admin/memberships/{m.pk}/',
            {'company': self.company_b.pk, 'role': 'sales'}, format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        m.refresh_from_db()
        self.assertEqual(m.company_id, self.company_a.pk)  # silently ignored, not moved

    def test_membership_user_cannot_be_changed(self):
        m = Membership.objects.create(user=self.target, company=self.company_a, role='sales')
        other = _saas_user('esc_other_target')
        res = self._as(self.admin_a).patch(
            f'/api/admin/memberships/{m.pk}/', {'user': other.pk, 'role': 'inventory'},
            format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        m.refresh_from_db()
        self.assertEqual(m.user_id, self.target.pk)

    def test_api_never_touches_userprofile_role(self):
        before = self.target.profile.role
        self._post(self.admin_a, role='admin')
        self.target.profile.refresh_from_db()
        self.assertEqual(self.target.profile.role, before)
        self.assertEqual(before, UserProfile.ROLE_CUSTOMER)

    def test_api_never_touches_is_superuser(self):
        self._post(self.admin_a, role='admin')
        self.target.refresh_from_db()
        self.assertFalse(self.target.is_superuser)

    # --- enumeration ---

    def test_missing_user_and_duplicate_membership_answer_identically(self):
        """The endpoint must not be a platform-wide user-id oracle."""
        Membership.objects.create(user=self.target, company=self.company_a, role='sales')
        duplicate = self._post(self.admin_a, user=self.target.pk)
        missing = self._post(self.admin_a, user=999999)
        self.assertEqual(duplicate.status_code, missing.status_code)
        self.assertEqual(duplicate.data['detail'], missing.data['detail'])
        self.assertEqual(duplicate.status_code, status.HTTP_400_BAD_REQUEST)

    def test_failed_grant_leaks_no_user_details(self):
        res = self._post(self.admin_a, user=999999)
        raw = str(res.data).lower()
        for leak in ('username', 'email', 'esc_', '@'):
            self.assertNotIn(leak, raw)


class Phase2aAuditTest(TestCase):
    """Company-scoped actions are audited with actor, company and safe metadata."""

    def setUp(self):
        cache.clear()
        self.company = _saas_company('Audit SA', 'audit-sa')
        self.branch = _saas_branch(self.company, name='Sucursal auditoría')
        self.admin = _saas_user('audit_admin')
        Membership.objects.create(user=self.admin, company=self.company, role='admin')
        self.target = _saas_user('audit_target')
        self.platform = _saas_user('audit_platform', is_superuser=True)
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)

    def _all_metadata(self):
        return ' '.join(str(log.metadata) for log in AdminAuditLog.objects.all()).lower()

    def test_membership_created_is_audited_with_company(self):
        self.client.post('/api/admin/memberships/', {
            'user': self.target.pk, 'company': self.company.pk, 'role': 'sales',
        }, format='json')
        log = AdminAuditLog.objects.filter(action='membership_created').first()
        self.assertIsNotNone(log)
        self.assertEqual(log.actor, self.admin)
        self.assertEqual(log.company_id, self.company.pk)
        self.assertIsNotNone(log.created_at)

    def test_membership_updated_is_audited_with_company(self):
        m = Membership.objects.create(user=self.target, company=self.company, role='sales')
        self.client.patch(
            f'/api/admin/memberships/{m.pk}/', {'role': 'inventory'}, format='json',
        )
        log = AdminAuditLog.objects.filter(action='membership_updated').first()
        self.assertIsNotNone(log)
        self.assertEqual(log.company_id, self.company.pk)
        self.assertEqual(log.metadata['role'], 'inventory')

    def test_branch_created_is_audited_with_company(self):
        self.client.post('/api/admin/branches/', {
            'company': self.company.pk, 'name': 'Sucursal nueva',
        }, format='json')
        log = AdminAuditLog.objects.filter(action='branch_created').first()
        self.assertIsNotNone(log)
        self.assertEqual(log.company_id, self.company.pk)

    def test_company_created_and_updated_are_audited(self):
        c = APIClient()
        c.force_authenticate(user=self.platform)
        c.post('/api/admin/companies/', {'name': 'Nueva', 'slug': 'nueva-sa'}, format='json')
        created = AdminAuditLog.objects.filter(action='company_created').first()
        self.assertIsNotNone(created)
        self.assertIsNotNone(created.company_id)

        c.patch(f'/api/admin/companies/{created.company_id}/', {'is_active': False}, format='json')
        updated = AdminAuditLog.objects.filter(action='company_updated').first()
        self.assertIsNotNone(updated)
        self.assertEqual(updated.company_id, created.company_id)

    def test_audit_metadata_carries_no_secrets(self):
        self.client.post('/api/admin/memberships/', {
            'user': self.target.pk, 'company': self.company.pk, 'role': 'sales',
        }, format='json')
        raw = self._all_metadata()
        for secret in (
            'password', 'pass123', 'jwt', 'bearer', 'blackdog_access',
            'blackdog_refresh', 'csrf', 'stripe', 'sk_', 'pi_', 'cs_', 'token',
        ):
            self.assertNotIn(secret, raw, f'metadata expone "{secret}"')

    def test_rejected_grant_creates_no_audit_log(self):
        sales = _saas_user('audit_sales')
        Membership.objects.create(user=sales, company=self.company, role='sales')
        c = APIClient()
        c.force_authenticate(user=sales)
        c.post('/api/admin/memberships/', {
            'user': self.target.pk, 'company': self.company.pk, 'role': 'admin',
        }, format='json')
        self.assertEqual(AdminAuditLog.objects.filter(action='membership_created').count(), 0)


class Phase2aLegacyRbacRegressionTest(TestCase):
    """
    Legacy RBAC must keep working untouched.

    Product, Order, StockMovement and SalesNote have no `company` column, so the
    endpoints that manage them still authorise through UserProfile.role. Moving
    them to Membership before the data is tenantised would grant tenant-shaped
    permissions over globally-shared rows — a false sense of isolation.
    """

    def setUp(self):
        cache.clear()
        self.company = _saas_company('Legacy SA', 'legacy-sa')
        self.product = _seeded(Product.objects.create(company=_pilot_company(),
            name='Producto Legacy 2A', slug='producto-legacy-2a',
            price=Decimal('500.00'), inventory=30,
        ))
        self.users = {}
        for role in ('customer', 'sales', 'inventory', 'technician', 'admin'):
            u = _saas_user(f'legacy_{role}')
            u.profile.role = role
            u.profile.save()
            self.users[role] = u
        self.users['superadmin'] = _saas_user('legacy_superadmin', is_superuser=True)

    def _as(self, role):
        c = APIClient()
        c.force_authenticate(user=self.users[role])
        return c

    def test_get_user_role_still_reads_userprofile(self):
        for role in ('customer', 'sales', 'inventory', 'technician', 'admin'):
            self.assertEqual(get_user_role(self.users[role]), role)
        self.assertEqual(get_user_role(self.users['superadmin']), 'superadmin')

    def test_legacy_roles_work_without_any_membership(self):
        """The legacy surface must not start requiring a Membership."""
        self.assertEqual(Membership.objects.count(), 0)
        self.assertEqual(
            self._as('admin').get('/api/admin/products/').status_code, status.HTTP_200_OK,
        )

    def test_can_view_admin_products(self):
        """
        Phase 2B: the catalogue is tenantised, so the legacy bridge carries these
        operators — and a platform master (`superadmin`) is excluded from the
        bridge on purpose and must name a tenant.
        """
        for role in ('inventory', 'sales', 'admin'):
            self.assertEqual(
                self._as(role).get('/api/admin/products/').status_code,
                status.HTTP_200_OK, role,
            )
        for role in ('customer', 'technician'):
            self.assertEqual(
                self._as(role).get('/api/admin/products/').status_code,
                status.HTTP_403_FORBIDDEN, role,
            )
        pilot = _pilot_company()
        self.assertEqual(
            self._as('superadmin').get(f'/api/admin/products/?company={pilot.pk}').status_code,
            status.HTTP_200_OK,
        )

    def test_can_manage_products(self):
        payload = {'name': 'Nuevo Legacy', 'slug': 'nuevo-legacy-2a', 'price': '10.00', 'inventory': 1}
        self.assertEqual(
            self._as('sales').post('/api/admin/products/', payload, format='json').status_code,
            status.HTTP_403_FORBIDDEN,
        )
        self.assertEqual(
            self._as('admin').post('/api/admin/products/', payload, format='json').status_code,
            status.HTTP_201_CREATED,
        )

    def test_can_manage_inventory_legacy_adjust(self):
        res = self._as('inventory').post(
            f'/api/admin/products/{self.product.pk}/inventory-adjust/',
            {'delta': 3, 'reason': 'Legacy 2A'}, format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(
            self._as('sales').post(
                f'/api/admin/products/{self.product.pk}/inventory-adjust/',
                {'delta': 1, 'reason': 'no'}, format='json',
            ).status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_can_view_admin_orders_and_manage_orders(self):
        """
        Phase 2C: orders are tenantised, so the legacy bridge carries these
        operators — and a platform master (`superadmin`) is excluded from the
        bridge on purpose and must name a tenant.
        """
        for role in ('inventory', 'sales', 'admin'):
            self.assertEqual(
                self._as(role).get('/api/admin/orders/').status_code,
                status.HTTP_200_OK, role,
            )
        for role in ('customer', 'technician'):
            self.assertEqual(
                self._as(role).get('/api/admin/orders/').status_code,
                status.HTTP_403_FORBIDDEN, role,
            )
        pilot = _pilot_company()
        self.assertEqual(
            self._as('superadmin').get(f'/api/admin/orders/?company={pilot.pk}').status_code,
            status.HTTP_200_OK,
        )

    def test_can_view_inventory_reports_and_manage_stock_movements(self):
        # Phase 2D: a platform master belongs to no tenant, so they name the
        # company. The legacy bridge deliberately excludes them — picking a
        # tenant for someone whose whole job is acting across tenants is the
        # silent leak Phase 2B closed.
        pilot = _pilot_company().pk
        for role in ('inventory', 'admin'):
            self.assertEqual(
                self._as(role).get('/api/admin/inventory/summary/').status_code,
                status.HTTP_200_OK, role,
            )
        self.assertEqual(
            self._as('superadmin').get(
                f'/api/admin/inventory/summary/?company={pilot}').status_code,
            status.HTTP_200_OK, 'superadmin',
        )
        for role in ('customer', 'sales', 'technician'):
            self.assertEqual(
                self._as(role).get('/api/admin/inventory/summary/').status_code,
                status.HTTP_403_FORBIDDEN, role,
            )

        res = self._as('inventory').post('/api/admin/inventory/movements/', {
            'product_id': self.product.pk, 'movement_type': 'manual_entry',
            'quantity': 2, 'reason': 'Legacy 2A',
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(
            self._as('sales').post('/api/admin/inventory/movements/', {
                'product_id': self.product.pk, 'movement_type': 'manual_entry',
                'quantity': 1, 'reason': 'no',
            }, format='json').status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_can_manage_sales_notes(self):
        """
        Phase 2C: sales notes hang off an order, so they are tenant-scoped too.
        The legacy bridge carries sales and admin; a platform master is excluded
        from it by design and names the tenant instead.
        """
        order = _p60_paid_order(self.product, quantity=1)
        for role in ('sales', 'admin'):
            res = self._as(role).get(f'/api/admin/orders/{order.pk}/sales-note/')
            self.assertIn(res.status_code, (status.HTTP_200_OK, status.HTTP_404_NOT_FOUND), role)
        for role in ('customer', 'inventory', 'technician'):
            self.assertEqual(
                self._as(role).get(f'/api/admin/orders/{order.pk}/sales-note/').status_code,
                status.HTTP_403_FORBIDDEN, role,
            )

    def test_saas_membership_does_not_grant_legacy_permissions(self):
        """
        A Membership with the legacy `admin` value but NO custom role resolves,
        through the legacy fallback, to that role's capabilities — which include
        products.*. So it reaches the tenantised catalogue of ITS OWN company,
        and nothing else: inventory is still legacy and stays shut.
        """
        u = _saas_user('legacy_membership_only')
        Membership.objects.create(user=u, company=self.company, role='admin')
        self.assertEqual(u.profile.role, UserProfile.ROLE_CUSTOMER)
        c = APIClient()
        c.force_authenticate(user=u)

        res = c.get('/api/admin/products/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        # ...and only its own company's catalogue
        self.assertEqual(
            {p['slug'] for p in res.data['results']},
            {p.slug for p in Product.objects.filter(company=self.company)},
        )

        self.assertEqual(
            c.get('/api/admin/inventory/summary/').status_code, status.HTTP_403_FORBIDDEN,
        )


# ---------------------------------------------------------------------------
# Phase 2A.1 — configurable areas, roles and capability resolution
# ---------------------------------------------------------------------------

from .capabilities import (  # noqa: E402
    ALL_CAPABILITY_CODES, ASSIGNABLE_CAPABILITY_CODES, CAPABILITIES,
    RESERVED_CAPABILITY_CODES, normalise_capabilities,
)
from .models import CompanyArea, CompanyRole, MembershipRoleAssignment  # noqa: E402
from .tenancy import (  # noqa: E402
    LEGACY_CAP_TO_CODE, LEGACY_ROLE_CAPABILITIES, can_delegate_capabilities,
    has_capability, resolve_capabilities, user_areas,
)


def _area(company, name='Taller', slug=None, **extra):
    return CompanyArea.objects.create(
        company=company, name=name, slug=slug or name.lower().replace(' ', '-'), **extra,
    )


def _role(company, name='Técnico', capabilities=None, slug=None, **extra):
    return CompanyRole.objects.create(
        company=company, name=name, slug=slug or name.lower().replace(' ', '-'),
        capabilities=capabilities or [], **extra,
    )


def _assign(membership, role, area=None, **extra):
    return MembershipRoleAssignment.objects.create(
        membership=membership, role=role, area=area, **extra,
    )


class Phase2a1CatalogTest(TestCase):
    """The capability catalogue is owned by the platform, not by tenants."""

    def test_catalog_has_no_duplicate_codes(self):
        self.assertEqual(len(CAPABILITIES), len(ALL_CAPABILITY_CODES))

    def test_reserved_capabilities_are_not_assignable(self):
        self.assertTrue(RESERVED_CAPABILITY_CODES)
        self.assertFalse(RESERVED_CAPABILITY_CODES & ASSIGNABLE_CAPABILITY_CODES)
        for code in RESERVED_CAPABILITY_CODES:
            self.assertTrue(code.startswith('service.'), code)

    def test_normalise_rejects_unknown_code(self):
        with self.assertRaises(ValueError):
            normalise_capabilities(['inventory.teleport'])

    def test_normalise_rejects_reserved_code(self):
        with self.assertRaises(ValueError):
            normalise_capabilities(['service.repair.manage'])

    def test_normalise_deduplicates_and_sorts(self):
        self.assertEqual(
            normalise_capabilities(['inventory.adjust', 'company.view', 'inventory.adjust']),
            ['company.view', 'inventory.adjust'],
        )

    def test_normalise_rejects_a_bare_string(self):
        with self.assertRaises(ValueError):
            normalise_capabilities('company.view')

    def test_legacy_role_capabilities_reference_real_codes(self):
        for role, caps in LEGACY_ROLE_CAPABILITIES.items():
            self.assertTrue(caps <= ASSIGNABLE_CAPABILITY_CODES, role)

    def test_legacy_capability_names_map_into_the_catalog(self):
        self.assertTrue(set(LEGACY_CAP_TO_CODE.values()) <= ALL_CAPABILITY_CODES)


class Phase2a1ModelInvariantTest(TestCase):
    """Structural guards on areas, roles and assignments."""

    def setUp(self):
        self.company_a = _saas_company('Empresa A', 'p21-a')
        self.company_b = _saas_company('Empresa B', 'p21-b', tax_id='20000000021')
        self.user = _saas_user('p21_user')
        self.membership = Membership.objects.create(
            user=self.user, company=self.company_a, role='sales',
        )

    def test_area_slug_unique_per_company_but_reusable_across_companies(self):
        _area(self.company_a, 'Taller', 'taller')
        _area(self.company_b, 'Taller', 'taller')  # otra empresa: permitido
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                _area(self.company_a, 'Taller Bis', 'taller')

    def test_role_slug_unique_per_company(self):
        _role(self.company_a, 'Técnico', ['company.view'], 'tecnico')
        _role(self.company_b, 'Técnico', ['company.view'], 'tecnico')
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                _role(self.company_a, 'Otro', ['company.view'], 'tecnico')

    def test_role_rejects_unknown_capability(self):
        with self.assertRaises(DjangoValidationError):
            _role(self.company_a, 'Malo', ['inventory.teleport'], 'malo')

    def test_role_rejects_reserved_capability(self):
        with self.assertRaises(DjangoValidationError):
            _role(self.company_a, 'Reservado', ['service.repair.manage'], 'reservado')

    def test_role_capabilities_are_stored_sorted_and_deduplicated(self):
        role = _role(self.company_a, 'Orden', ['inventory.adjust', 'company.view', 'company.view'], 'orden')
        role.refresh_from_db()
        self.assertEqual(role.capabilities, ['company.view', 'inventory.adjust'])

    def test_assignment_rejects_role_from_another_company(self):
        foreign_role = _role(self.company_b, 'Ajeno', ['company.view'], 'ajeno')
        with self.assertRaises(DjangoValidationError):
            _assign(self.membership, foreign_role)

    def test_assignment_rejects_area_from_another_company(self):
        own_role = _role(self.company_a, 'Propio', ['company.view'], 'propio')
        foreign_area = _area(self.company_b, 'Ajena', 'ajena')
        with self.assertRaises(DjangoValidationError):
            _assign(self.membership, own_role, area=foreign_area)

    def test_same_role_may_be_held_in_two_areas(self):
        role = _role(self.company_a, 'Multi', ['company.view'], 'multi')
        taller = _area(self.company_a, 'Taller', 'taller')
        recepcion = _area(self.company_a, 'Recepción', 'recepcion')
        _assign(self.membership, role, area=taller)
        _assign(self.membership, role, area=recepcion)
        self.assertEqual(self.membership.role_assignments.count(), 2)

    def test_same_role_twice_in_the_same_area_is_rejected(self):
        role = _role(self.company_a, 'Duplicado', ['company.view'], 'duplicado')
        taller = _area(self.company_a, 'Taller', 'taller')
        _assign(self.membership, role, area=taller)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                _assign(self.membership, role, area=taller)

    def test_one_membership_carries_several_hats(self):
        """User X @ Company A: Técnico en Taller + Recepción en Recepción."""
        tecnico = _role(self.company_a, 'Técnico', ['company.view', 'service.manage'], 'tecnico')
        recepcion_role = _role(self.company_a, 'Recepción', ['company.view', 'sales.orders.view'], 'recepcion-rol')
        taller = _area(self.company_a, 'Taller', 'taller')
        recepcion = _area(self.company_a, 'Recepción', 'recepcion')
        _assign(self.membership, tecnico, area=taller)
        _assign(self.membership, recepcion_role, area=recepcion)

        self.assertEqual(Membership.objects.filter(user=self.user, company=self.company_a).count(), 1)
        self.assertEqual(
            resolve_capabilities(self.user, self.company_a),
            frozenset({'company.view', 'service.manage', 'sales.orders.view'}),
        )
        self.assertEqual(
            {a.name for a in user_areas(self.user, self.company_a)},
            {'Taller', 'Recepción'},
        )


class Phase2a1ResolutionTest(TestCase):
    """Custom roles replace the legacy fallback — they never merely add to it."""

    def setUp(self):
        self.company = _saas_company('Resolución SA', 'p21-res')
        self.platform = _saas_user('p21_platform', is_superuser=True)

    def _member(self, username, legacy_role):
        u = _saas_user(username)
        m = Membership.objects.create(user=u, company=self.company, role=legacy_role)
        return u, m

    def test_legacy_fallback_when_no_custom_role(self):
        u, _ = self._member('p21_legacy_inv', 'inventory')
        self.assertEqual(
            resolve_capabilities(u, self.company),
            LEGACY_ROLE_CAPABILITIES['inventory'],
        )

    def test_legacy_fallback_matches_the_phase_2a_matrix_for_every_role(self):
        """The new resolution must reproduce Phase 2A exactly without custom roles."""
        for legacy_role in ('customer', 'sales', 'inventory', 'technician', 'admin', 'superadmin'):
            u, _ = self._member(f'p21_parity_{legacy_role}', legacy_role)
            for cap_name, code in LEGACY_CAP_TO_CODE.items():
                expected = legacy_role in COMPANY_CAPABILITIES[cap_name]
                self.assertEqual(
                    has_company_capability(u, self.company, cap_name), expected,
                    f'{legacy_role}/{cap_name} divergió',
                )
                self.assertEqual(
                    has_capability(u, self.company, code), expected,
                    f'{legacy_role}/{code} divergió',
                )

    def test_custom_role_replaces_legacy_authority(self):
        """A legacy admin restricted by a custom role must actually be restricted."""
        u, m = self._member('p21_restricted', 'admin')
        self.assertTrue(has_capability(u, self.company, 'memberships.manage'))

        limited = _role(self.company, 'Solo lectura', ['company.view'], 'solo-lectura')
        _assign(m, limited)

        self.assertEqual(resolve_capabilities(u, self.company), frozenset({'company.view'}))
        self.assertFalse(has_capability(u, self.company, 'memberships.manage'))
        # ...and the Phase 2A helper agrees, so the SaaS endpoints honour it
        self.assertFalse(
            has_company_capability(u, self.company, CAP_MANAGE_MEMBERSHIPS)
        )

    def test_two_roles_union_their_capabilities(self):
        u, m = self._member('p21_union', 'customer')
        _assign(m, _role(self.company, 'A', ['company.view', 'inventory.view'], 'rol-a'))
        _assign(m, _role(self.company, 'B', ['sales.orders.view'], 'rol-b'))
        self.assertEqual(
            resolve_capabilities(u, self.company),
            frozenset({'company.view', 'inventory.view', 'sales.orders.view'}),
        )

    def test_inactive_role_grants_nothing(self):
        u, m = self._member('p21_inactive_role', 'customer')
        role = _role(self.company, 'Apagado', ['company.view'], 'apagado', is_active=False)
        _assign(m, role)
        # No active assignment remains -> falls back to the legacy role (customer = nothing)
        self.assertEqual(resolve_capabilities(u, self.company), frozenset())

    def test_inactive_assignment_grants_nothing(self):
        u, m = self._member('p21_inactive_assign', 'customer')
        _assign(m, _role(self.company, 'Vivo', ['company.view'], 'vivo'), is_active=False)
        self.assertEqual(resolve_capabilities(u, self.company), frozenset())

    def test_inactive_area_does_not_remove_capabilities(self):
        """Areas are organisational: deactivating one must not change authority."""
        u, m = self._member('p21_inactive_area', 'customer')
        area = _area(self.company, 'Taller', 'taller')
        _assign(m, _role(self.company, 'Con área', ['company.view'], 'con-area'), area=area)
        area.is_active = False
        area.save(update_fields=['is_active'])
        self.assertEqual(resolve_capabilities(u, self.company), frozenset({'company.view'}))
        self.assertEqual(user_areas(u, self.company), [])

    def test_area_alone_grants_no_permission(self):
        """Belonging to 'Inventario' must NOT confer inventory.adjust."""
        u, m = self._member('p21_area_only', 'customer')
        inventario = _area(self.company, 'Inventario', 'inventario')
        empty_role = _role(self.company, 'Sin permisos', [], 'sin-permisos')
        _assign(m, empty_role, area=inventario)

        self.assertEqual(user_areas(u, self.company), [inventario])
        self.assertFalse(has_capability(u, self.company, 'inventory.adjust'))
        self.assertEqual(resolve_capabilities(u, self.company), frozenset())

    def test_inactive_membership_grants_nothing_even_with_roles(self):
        u, m = self._member('p21_dead_member', 'admin')
        _assign(m, _role(self.company, 'Potente', sorted(ASSIGNABLE_CAPABILITY_CODES), 'potente'))
        m.is_active = False
        m.save(update_fields=['is_active'])
        self.assertEqual(resolve_capabilities(u, self.company), frozenset())

    def test_inactive_company_grants_nothing_even_with_roles(self):
        u, m = self._member('p21_dead_company', 'admin')
        _assign(m, _role(self.company, 'Potente2', sorted(ASSIGNABLE_CAPABILITY_CODES), 'potente2'))
        self.company.is_active = False
        self.company.save(update_fields=['is_active'])
        self.assertEqual(resolve_capabilities(u, self.company), frozenset())

    def test_platform_master_holds_every_assignable_capability(self):
        self.assertEqual(
            resolve_capabilities(self.platform, self.company), ASSIGNABLE_CAPABILITY_CODES,
        )

    def test_platform_master_holds_no_reserved_capability(self):
        held = resolve_capabilities(self.platform, self.company)
        self.assertFalse(held & RESERVED_CAPABILITY_CODES)

    def test_user_without_membership_holds_nothing(self):
        self.assertEqual(resolve_capabilities(_saas_user('p21_orphan'), self.company), frozenset())

    def test_has_capability_rejects_unknown_code(self):
        u, _ = self._member('p21_unknown', 'admin')
        with self.assertRaises(ValueError):
            has_capability(u, self.company, 'nope.nope')


class Phase2a1AccessApiTest(TestCase):
    """Areas/roles/assignments API: tenant scoping and escalation limits."""

    def setUp(self):
        cache.clear()
        self.company_a = _saas_company('Empresa A', 'p21api-a')
        self.company_b = _saas_company('Empresa B', 'p21api-b', tax_id='20000000022')

        self.area_a = _area(self.company_a, 'Taller A', 'taller-a')
        self.area_b = _area(self.company_b, 'Taller B', 'taller-b')
        self.role_a = _role(self.company_a, 'Técnico A', ['company.view', 'service.manage'], 'tecnico-a')
        self.role_b = _role(self.company_b, 'Técnico B', ['company.view'], 'tecnico-b')

        self.admin_a = _saas_user('p21api_admin_a')
        self.admin_b = _saas_user('p21api_admin_b')
        self.sales_a = _saas_user('p21api_sales_a')
        self.orphan = _saas_user('p21api_orphan')
        self.platform = _saas_user('p21api_platform', is_superuser=True)
        self.staff_a = _saas_user('p21api_staff_a')

        self.m_admin_a = Membership.objects.create(
            user=self.admin_a, company=self.company_a, role='admin')
        self.m_admin_b = Membership.objects.create(
            user=self.admin_b, company=self.company_b, role='admin')
        self.m_sales_a = Membership.objects.create(
            user=self.sales_a, company=self.company_a, role='sales')
        self.m_staff_a = Membership.objects.create(
            user=self.staff_a, company=self.company_a, role='customer')

    def _as(self, user):
        c = APIClient()
        c.force_authenticate(user=user)
        return c

    # --- cross-tenant reads ---

    def test_company_a_cannot_list_company_b_areas(self):
        res = self._as(self.admin_a).get('/api/admin/areas/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        names = {r['name'] for r in res.data['results']}
        self.assertIn('Taller A', names)
        self.assertNotIn('Taller B', names)

    def test_company_a_cannot_read_company_b_area_detail(self):
        res = self._as(self.admin_a).get(f'/api/admin/areas/{self.area_b.pk}/')
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_company_a_cannot_list_company_b_roles(self):
        res = self._as(self.admin_a).get('/api/admin/roles/')
        slugs = {r['slug'] for r in res.data['results']}
        self.assertIn('tecnico-a', slugs)
        self.assertNotIn('tecnico-b', slugs)

    def test_company_a_cannot_edit_company_b_role(self):
        res = self._as(self.admin_a).patch(
            f'/api/admin/roles/{self.role_b.pk}/', {'name': 'Secuestrado'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
        self.role_b.refresh_from_db()
        self.assertEqual(self.role_b.name, 'Técnico B')

    def test_company_a_cannot_create_an_area_in_company_b(self):
        res = self._as(self.admin_a).post('/api/admin/areas/', {
            'company': self.company_b.pk, 'name': 'Intrusa', 'slug': 'intrusa',
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(CompanyArea.objects.filter(slug='intrusa').exists())

    def test_company_a_cannot_create_a_role_in_company_b(self):
        res = self._as(self.admin_a).post('/api/admin/roles/', {
            'company': self.company_b.pk, 'name': 'Intruso', 'slug': 'intruso',
            'capabilities': ['company.view'],
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    # --- cross-tenant assignment attempts ---

    def test_membership_of_a_cannot_receive_role_of_b(self):
        res = self._as(self.admin_a).post('/api/admin/membership-role-assignments/', {
            'membership': self.m_staff_a.pk, 'role': self.role_b.pk,
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(MembershipRoleAssignment.objects.count(), 0)

    def test_role_of_a_cannot_reference_area_of_b(self):
        res = self._as(self.admin_a).post('/api/admin/membership-role-assignments/', {
            'membership': self.m_staff_a.pk, 'role': self.role_a.pk, 'area': self.area_b.pk,
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(MembershipRoleAssignment.objects.count(), 0)

    def test_company_b_admin_cannot_assign_into_company_a(self):
        res = self._as(self.admin_b).post('/api/admin/membership-role-assignments/', {
            'membership': self.m_staff_a.pk, 'role': self.role_a.pk,
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_assignments_list_is_scoped(self):
        _assign(self.m_staff_a, self.role_a)
        _assign(self.m_admin_b, self.role_b)
        res = self._as(self.admin_a).get('/api/admin/membership-role-assignments/')
        self.assertEqual(res.data['count'], 1)
        self.assertEqual(res.data['results'][0]['company'], self.company_a.pk)

    # --- authority ---

    def test_user_without_membership_is_rejected(self):
        c = self._as(self.orphan)
        for url in ('/api/admin/areas/', '/api/admin/roles/',
                    '/api/admin/membership-role-assignments/', '/api/admin/capabilities/'):
            self.assertEqual(c.get(url).status_code, status.HTTP_403_FORBIDDEN, url)

    def test_anonymous_is_rejected(self):
        self.assertEqual(
            APIClient().get('/api/admin/roles/').status_code, status.HTTP_401_UNAUTHORIZED)

    def test_sales_can_read_but_not_manage_areas(self):
        c = self._as(self.sales_a)
        self.assertEqual(c.get('/api/admin/areas/').status_code, status.HTTP_200_OK)
        res = c.post('/api/admin/areas/', {
            'company': self.company_a.pk, 'name': 'Nueva', 'slug': 'nueva',
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_sales_cannot_create_roles(self):
        res = self._as(self.sales_a).post('/api/admin/roles/', {
            'company': self.company_a.pk, 'name': 'Nuevo', 'slug': 'nuevo',
            'capabilities': ['company.view'],
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_can_create_area_and_role_in_own_company(self):
        c = self._as(self.admin_a)
        area = c.post('/api/admin/areas/', {
            'company': self.company_a.pk, 'name': 'Caja', 'slug': 'caja',
        }, format='json')
        self.assertEqual(area.status_code, status.HTTP_201_CREATED)
        role = c.post('/api/admin/roles/', {
            'company': self.company_a.pk, 'name': 'Cajero', 'slug': 'cajero',
            'capabilities': ['company.view', 'sales.orders.view'],
        }, format='json')
        self.assertEqual(role.status_code, status.HTTP_201_CREATED)
        self.assertEqual(role.data['capabilities'], ['company.view', 'sales.orders.view'])

    def test_role_rejects_reserved_capability_via_api(self):
        res = self._as(self.admin_a).post('/api/admin/roles/', {
            'company': self.company_a.pk, 'name': 'Reservado', 'slug': 'reservado',
            'capabilities': ['service.repair.manage'],
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_role_rejects_unknown_capability_via_api(self):
        res = self._as(self.admin_a).post('/api/admin/roles/', {
            'company': self.company_a.pk, 'name': 'Falso', 'slug': 'falso',
            'capabilities': ['inventory.teleport'],
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    # --- privilege escalation ---

    def test_admin_cannot_delegate_a_capability_they_lack(self):
        """A limited admin must not author a role more powerful than themselves."""
        limited = _role(self.company_a, 'Limitado',
                        ['company.view', 'roles.manage'], 'limitado')
        _assign(self.m_admin_a, limited)

        res = self._as(self.admin_a).post('/api/admin/roles/', {
            'company': self.company_a.pk, 'name': 'Poderoso', 'slug': 'poderoso',
            'capabilities': ['company.view', 'memberships.manage'],
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(CompanyRole.objects.filter(slug='poderoso').exists())

    def test_admin_can_delegate_capabilities_they_hold(self):
        limited = _role(self.company_a, 'Limitado2',
                        ['company.view', 'roles.manage', 'inventory.view'], 'limitado2')
        _assign(self.m_admin_a, limited)
        res = self._as(self.admin_a).post('/api/admin/roles/', {
            'company': self.company_a.pk, 'name': 'Espejo', 'slug': 'espejo',
            'capabilities': ['inventory.view'],
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

    def test_admin_cannot_escalate_an_existing_role_beyond_their_authority(self):
        limited = _role(self.company_a, 'Limitado3',
                        ['company.view', 'roles.manage'], 'limitado3')
        _assign(self.m_admin_a, limited)
        target = _role(self.company_a, 'Objetivo', ['company.view'], 'objetivo')

        res = self._as(self.admin_a).patch(f'/api/admin/roles/{target.pk}/', {
            'capabilities': ['company.view', 'memberships.manage'],
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        target.refresh_from_db()
        self.assertEqual(target.capabilities, ['company.view'])

    def test_admin_cannot_assign_a_role_stronger_than_themselves(self):
        limited = _role(self.company_a, 'Limitado4',
                        ['company.view', 'memberships.manage'], 'limitado4')
        _assign(self.m_admin_a, limited)
        powerful = _role(self.company_a, 'Fuerte',
                         ['company.view', 'inventory.adjust'], 'fuerte')

        res = self._as(self.admin_a).post('/api/admin/membership-role-assignments/', {
            'membership': self.m_staff_a.pk, 'role': powerful.pk,
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_platform_master_is_exempt_from_the_delegation_limit(self):
        res = self._as(self.platform).post('/api/admin/roles/', {
            'company': self.company_a.pk, 'name': 'Total', 'slug': 'total',
            'capabilities': sorted(ASSIGNABLE_CAPABILITY_CODES),
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

    def test_api_never_writes_is_superuser_or_userprofile_role(self):
        before_profile = self.staff_a.profile.role
        c = self._as(self.admin_a)
        c.post('/api/admin/roles/', {
            'company': self.company_a.pk, 'name': 'Jefe', 'slug': 'jefe',
            'capabilities': sorted(ASSIGNABLE_CAPABILITY_CODES),
        }, format='json')
        role = CompanyRole.objects.get(slug='jefe')
        c.post('/api/admin/membership-role-assignments/', {
            'membership': self.m_staff_a.pk, 'role': role.pk,
        }, format='json')

        self.staff_a.refresh_from_db()
        self.staff_a.profile.refresh_from_db()
        self.assertFalse(self.staff_a.is_superuser)
        self.assertFalse(self.staff_a.is_staff)
        self.assertEqual(self.staff_a.profile.role, before_profile)
        self.assertFalse(is_platform_admin(self.staff_a))

    # --- lifecycle ---

    def test_delete_deactivates_instead_of_destroying(self):
        assignment = _assign(self.m_staff_a, self.role_a)
        res = self._as(self.admin_a).delete(
            f'/api/admin/membership-role-assignments/{assignment.pk}/')
        self.assertEqual(res.status_code, status.HTTP_204_NO_CONTENT)
        assignment.refresh_from_db()
        self.assertFalse(assignment.is_active)
        self.assertTrue(MembershipRoleAssignment.objects.filter(pk=assignment.pk).exists())

    def test_duplicate_assignment_via_api_is_rejected(self):
        _assign(self.m_staff_a, self.role_a)
        res = self._as(self.admin_a).post('/api/admin/membership-role-assignments/', {
            'membership': self.m_staff_a.pk, 'role': self.role_a.pk,
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_area_cannot_be_moved_between_companies(self):
        res = self._as(self.platform).patch(f'/api/admin/areas/{self.area_a.pk}/', {
            'company': self.company_b.pk,
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.area_a.refresh_from_db()
        self.assertEqual(self.area_a.company_id, self.company_a.pk)

    # --- catalogue and self-service ---

    def test_capability_catalog_is_read_only_and_complete(self):
        res = self._as(self.admin_a).get('/api/admin/capabilities/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.data['capabilities']), len(ALL_CAPABILITY_CODES))
        reserved = [c for c in res.data['capabilities'] if not c['assignable']]
        self.assertTrue(reserved)
        self.assertEqual(
            self._as(self.admin_a).post('/api/admin/capabilities/', {}, format='json').status_code,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        )

    def test_me_company_access_reports_source_and_capabilities(self):
        res = self._as(self.sales_a).get('/api/me/company-access/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        row = res.data['results'][0]
        self.assertEqual(row['company'], self.company_a.pk)
        self.assertEqual(row['source'], 'legacy_role')
        self.assertIn('sales.orders.manage', row['capabilities'])

        _assign(self.m_sales_a, _role(self.company_a, 'Solo ver', ['company.view'], 'solo-ver'))
        row = self._as(self.sales_a).get('/api/me/company-access/').data['results'][0]
        self.assertEqual(row['source'], 'custom_roles')
        self.assertEqual(row['capabilities'], ['company.view'])

    def test_me_company_access_is_self_scoped(self):
        res = self._as(self.orphan).get('/api/me/company-access/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['count'], 0)


class Phase2a1AuditTest(TestCase):
    """Sensitive access changes are audited with actor, company and safe metadata."""

    def setUp(self):
        cache.clear()
        self.company = _saas_company('Audit 2A1', 'p21-audit')
        self.admin = _saas_user('p21audit_admin')
        self.m_admin = Membership.objects.create(
            user=self.admin, company=self.company, role='admin')
        self.staff = _saas_user('p21audit_staff')
        self.m_staff = Membership.objects.create(
            user=self.staff, company=self.company, role='customer')
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)

    def _log(self, action):
        return AdminAuditLog.objects.filter(action=action).first()

    def test_area_created_and_updated(self):
        res = self.client.post('/api/admin/areas/', {
            'company': self.company.pk, 'name': 'Caja', 'slug': 'caja',
        }, format='json')
        log = self._log('area_created')
        self.assertIsNotNone(log)
        self.assertEqual(log.company_id, self.company.pk)
        self.assertEqual(log.actor, self.admin)

        self.client.patch(f"/api/admin/areas/{res.data['id']}/",
                          {'is_active': False}, format='json')
        self.assertIsNotNone(self._log('area_updated'))

    def test_company_role_created_and_permissions_updated(self):
        res = self.client.post('/api/admin/roles/', {
            'company': self.company.pk, 'name': 'Cajero', 'slug': 'cajero',
            'capabilities': ['company.view'],
        }, format='json')
        created = self._log('company_role_created')
        self.assertIsNotNone(created)
        self.assertEqual(created.metadata['capabilities'], ['company.view'])

        self.client.patch(f"/api/admin/roles/{res.data['id']}/",
                          {'capabilities': ['company.view', 'reports.view']}, format='json')
        perms = self._log('role_permissions_updated')
        self.assertIsNotNone(perms)
        self.assertEqual(perms.metadata['capabilities_before'], ['company.view'])
        self.assertEqual(perms.metadata['capabilities_after'],
                         ['company.view', 'reports.view'])

    def test_role_assignment_created_and_disabled(self):
        role = _role(self.company, 'Asignable', ['company.view'], 'asignable')
        res = self.client.post('/api/admin/membership-role-assignments/', {
            'membership': self.m_staff.pk, 'role': role.pk,
        }, format='json')
        created = self._log('role_assignment_created')
        self.assertIsNotNone(created)
        self.assertEqual(created.company_id, self.company.pk)

        self.client.delete(f"/api/admin/membership-role-assignments/{res.data['id']}/")
        self.assertIsNotNone(self._log('role_assignment_disabled'))

    def test_audit_metadata_carries_no_secrets(self):
        role = _role(self.company, 'Seguro', ['company.view'], 'seguro')
        self.client.post('/api/admin/membership-role-assignments/', {
            'membership': self.m_staff.pk, 'role': role.pk,
        }, format='json')
        raw = ' '.join(str(log.metadata) for log in AdminAuditLog.objects.all()).lower()
        for secret in ('password', 'pass123', 'jwt', 'bearer', 'blackdog_access',
                       'blackdog_refresh', 'csrf', 'stripe', 'sk_', 'token'):
            self.assertNotIn(secret, raw, f'metadata expone "{secret}"')

    def test_rejected_action_creates_no_audit_log(self):
        other = _saas_company('Otra', 'p21-audit-otra', tax_id='20000000023')
        self.client.post('/api/admin/areas/', {
            'company': other.pk, 'name': 'Intrusa', 'slug': 'intrusa',
        }, format='json')
        self.assertEqual(AdminAuditLog.objects.filter(action='area_created').count(), 0)


class Phase2a1SeedAndRegressionTest(TestCase):
    """Presets are per-company, and the external/legacy surfaces are untouched."""

    def setUp(self):
        cache.clear()
        self.pilot = Company.objects.get(slug='black-dog-store')

    def test_presets_are_seeded_for_the_existing_company(self):
        self.assertGreaterEqual(self.pilot.areas.count(), 7)
        self.assertGreaterEqual(self.pilot.roles.count(), 4)

    def test_presets_belong_to_the_company_not_to_the_platform(self):
        for area in self.pilot.areas.all():
            self.assertEqual(area.company_id, self.pilot.pk)
        for role in self.pilot.roles.all():
            self.assertEqual(role.company_id, self.pilot.pk)

    def test_preset_roles_mirror_the_legacy_capability_sets(self):
        ventas = self.pilot.roles.get(slug='ventas')
        self.assertEqual(
            set(ventas.capabilities), set(LEGACY_ROLE_CAPABILITIES['sales']),
        )
        inventario = self.pilot.roles.get(slug='inventario')
        self.assertEqual(
            set(inventario.capabilities), set(LEGACY_ROLE_CAPABILITIES['inventory']),
        )

    def test_backfill_assigns_nobody(self):
        """Presets are offered, not imposed: no membership was flipped."""
        self.assertEqual(MembershipRoleAssignment.objects.count(), 0)

    def test_backfill_did_not_touch_legacy_role_fields(self):
        u = _saas_user('p21_seed_user')
        self.assertEqual(u.profile.role, UserProfile.ROLE_CUSTOMER)
        m = Membership.objects.create(user=u, company=self.pilot, role='inventory')
        self.assertEqual(m.role, 'inventory')

    def test_external_customer_keeps_working_without_membership(self):
        """
        A shopper with no Membership is a customer of the storefront.

        Note this is one CASE, not the definition: the external portal is open to
        any user. See test_membership_holder_is_still_an_external_customer for
        the other direction.
        """
        user = _saas_user('p21_shopper')
        self.assertEqual(Membership.objects.filter(user=user).count(), 0)

        c = APIClient()
        self.assertEqual(c.get('/api/products/').status_code, status.HTTP_200_OK)

        login = c.post('/api/auth/login/', {
            'username': 'p21_shopper', 'password': 'Pass123!',
        }, format='json')
        self.assertEqual(login.status_code, status.HTTP_200_OK)
        self.assertTrue(login.cookies.get('blackdog_access')['httponly'])

        # ...and gets no internal access
        c2 = APIClient()
        c2.force_authenticate(user=user)
        self.assertEqual(c2.get('/api/admin/roles/').status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(c2.get('/api/admin/products/').status_code, status.HTTP_403_FORBIDDEN)

    def test_legacy_ecommerce_rbac_still_works(self):
        admin = _saas_user('p21_legacy_admin')
        admin.profile.role = UserProfile.ROLE_ADMIN
        admin.profile.save()
        c = APIClient()
        c.force_authenticate(user=admin)
        self.assertEqual(c.get('/api/admin/products/').status_code, status.HTTP_200_OK)
        self.assertEqual(c.get('/api/admin/orders/').status_code, status.HTTP_200_OK)

    def test_custom_roles_do_not_leak_into_legacy_ecommerce_rbac(self):
        """A powerful CompanyRole must not open the legacy admin surface."""
        u = _saas_user('p21_custom_only')
        m = Membership.objects.create(user=u, company=self.pilot, role='customer')
        _assign(m, _role(self.pilot, 'Todo', sorted(ASSIGNABLE_CAPABILITY_CODES), 'todo'))

        c = APIClient()
        c.force_authenticate(user=u)
        # Company surface: yes
        self.assertEqual(c.get('/api/admin/roles/').status_code, status.HTTP_200_OK)
        # Phase 2B: products ARE now governed by the company capability, so a
        # role holding products.view opens them. That is the migration working.
        self.assertEqual(c.get('/api/admin/products/').status_code, status.HTTP_200_OK)
        # Phase 2D: inventory joined them. StockMovement carries a company and a
        # branch now, so `inventory.view` is real authority rather than a label
        # over globally shared data — the same migration, one module later.
        self.assertEqual(
            c.get('/api/admin/inventory/summary/').status_code, status.HTTP_200_OK)
        # Orders are the module that has NOT migrated: a custom role holding
        # every capability still does not open the legacy order surface.
        self.assertEqual(
            c.get('/api/admin/orders/').status_code, status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# Phase 2A.1 closure — provisioning and surface separation
# ---------------------------------------------------------------------------

from .company_provisioning import (  # noqa: E402
    PRESET_AREAS, PRESET_ROLES, ProvisioningError,
    provision_company_access_defaults,
)


class Phase2a1ProvisioningTest(TestCase):
    """New companies get their default areas and roles, idempotently."""

    def setUp(self):
        cache.clear()
        self.platform = _saas_user('prov_platform', is_superuser=True)
        self.company_admin = _saas_user('prov_company_admin')
        self.existing = _saas_company('Existente SA', 'prov-existente')
        Membership.objects.create(
            user=self.company_admin, company=self.existing, role='admin')

    def _as(self, user):
        c = APIClient()
        c.force_authenticate(user=user)
        return c

    def _create_via_api(self, slug='nueva-sa', name='Nueva SA'):
        return self._as(self.platform).post('/api/admin/companies/', {
            'name': name, 'slug': slug, 'tax_id': '20111111111',
        }, format='json')

    # --- 1 & 2: a company created through the API is provisioned ---

    def test_01_new_company_via_api_gets_preset_areas(self):
        res = self._create_via_api()
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        company = Company.objects.get(slug='nueva-sa')
        self.assertEqual(company.areas.count(), len(PRESET_AREAS))
        self.assertEqual(
            {a.slug for a in company.areas.all()},
            {slug for _n, slug, _o in PRESET_AREAS},
        )

    def test_02_new_company_via_api_gets_preset_roles(self):
        self._create_via_api()
        company = Company.objects.get(slug='nueva-sa')
        self.assertEqual(company.roles.count(), len(PRESET_ROLES))
        ventas = company.roles.get(slug='ventas')
        self.assertIn('sales.orders.manage', ventas.capabilities)

    # --- 3: idempotency ---

    def test_03_running_provisioning_twice_does_not_duplicate(self):
        first = provision_company_access_defaults(self.existing)
        second = provision_company_access_defaults(self.existing)

        self.assertEqual(len(first['areas_created']), len(PRESET_AREAS))
        self.assertEqual(second['areas_created'], [])
        self.assertEqual(second['roles_created'], [])
        self.assertEqual(self.existing.areas.count(), len(PRESET_AREAS))
        self.assertEqual(self.existing.roles.count(), len(PRESET_ROLES))

    # --- 4: an edited preset is never overwritten ---

    def test_04_edited_preset_is_not_overwritten(self):
        provision_company_access_defaults(self.existing)
        role = self.existing.roles.get(slug='ventas')
        role.name = 'Ventas Mostrador'
        role.capabilities = ['company.view']
        role.save()

        area = self.existing.areas.get(slug='caja')
        area.name = 'Caja Principal'
        area.is_active = False
        area.save()

        provision_company_access_defaults(self.existing)

        role.refresh_from_db()
        area.refresh_from_db()
        self.assertEqual(role.name, 'Ventas Mostrador')
        self.assertEqual(role.capabilities, ['company.view'])
        self.assertEqual(area.name, 'Caja Principal')
        self.assertFalse(area.is_active)

    # --- 5: tenant isolation ---

    def test_05_provisioning_company_a_does_not_touch_company_b(self):
        other = _saas_company('Otra SA', 'prov-otra', tax_id='20222222222')
        provision_company_access_defaults(self.existing)

        self.assertEqual(other.areas.count(), 0)
        self.assertEqual(other.roles.count(), 0)
        for area in CompanyArea.objects.filter(company=self.existing):
            self.assertEqual(area.company_id, self.existing.pk)

        provision_company_access_defaults(other)
        self.assertEqual(other.areas.count(), len(PRESET_AREAS))
        # ...and A still has exactly its own, not doubled
        self.assertEqual(self.existing.areas.count(), len(PRESET_AREAS))

    # --- 6: neutrality ---

    def test_06_presets_contain_no_tenant_specific_data(self):
        import store.capabilities as capabilities_mod
        import store.company_provisioning as provisioning_mod

        for module in (provisioning_mod, capabilities_mod):
            source = open(module.__file__, encoding='utf-8').read().lower()
            for tenant_token in ('black dog', 'blackdog', 'cmau', '20610159886'):
                self.assertNotIn(
                    tenant_token, source,
                    f'{module.__name__} contiene datos de un tenant concreto',
                )

    # --- 7-9: provisioning changes nothing about identity ---

    def test_07_provisioning_creates_no_membership(self):
        before = Membership.objects.count()
        provision_company_access_defaults(self.existing)
        self.assertEqual(Membership.objects.count(), before)
        self.assertEqual(MembershipRoleAssignment.objects.count(), 0)

    def test_08_provisioning_does_not_change_userprofile_role(self):
        user = _saas_user('prov_profile_probe')
        before = user.profile.role
        self._create_via_api(slug='prov-probe', name='Probe SA')
        user.profile.refresh_from_db()
        self.assertEqual(user.profile.role, before)
        self.assertEqual(before, UserProfile.ROLE_CUSTOMER)

    def test_09_provisioning_does_not_change_is_superuser_or_is_staff(self):
        user = _saas_user('prov_super_probe')
        self._create_via_api(slug='prov-probe2', name='Probe 2 SA')
        user.refresh_from_db()
        self.company_admin.refresh_from_db()
        self.assertFalse(user.is_superuser)
        self.assertFalse(user.is_staff)
        self.assertFalse(self.company_admin.is_superuser)
        self.assertFalse(self.company_admin.is_staff)

    # --- 10-11: authority around company creation ---

    def test_10_company_created_by_platform_master_is_operational(self):
        self._create_via_api(slug='operativa-sa', name='Operativa SA')
        company = Company.objects.get(slug='operativa-sa')

        # A membership in it resolves real capabilities straight away
        staff = _saas_user('prov_new_staff')
        membership = Membership.objects.create(
            user=staff, company=company, role='customer')
        _assign(membership, company.roles.get(slug='inventario'))

        self.assertIn('inventory.adjust', resolve_capabilities(staff, company))
        self.assertTrue(has_capability(staff, company, 'inventory.view'))

    def test_11_company_admin_still_cannot_create_a_company(self):
        res = self._as(self.company_admin).post('/api/admin/companies/', {
            'name': 'Intrusa SA', 'slug': 'intrusa-sa',
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(Company.objects.filter(slug='intrusa-sa').exists())

    # --- 12: the Django admin path uses the same service ---

    def test_12_django_admin_creation_provisions_the_same_defaults(self):
        from django.contrib.admin.sites import AdminSite

        from store.admin import CompanyAdmin

        admin_instance = CompanyAdmin(Company, AdminSite())
        request = type('R', (), {'user': self.platform})()
        company = Company(name='Admin SA', slug='admin-sa', tax_id='20333333333')

        admin_instance.save_model(request, company, form=None, change=False)

        self.assertEqual(company.areas.count(), len(PRESET_AREAS))
        self.assertEqual(company.roles.count(), len(PRESET_ROLES))

    def test_12b_django_admin_edit_does_not_re_provision(self):
        from django.contrib.admin.sites import AdminSite

        from store.admin import CompanyAdmin

        provision_company_access_defaults(self.existing)
        self.existing.areas.get(slug='caja').delete()

        admin_instance = CompanyAdmin(Company, AdminSite())
        request = type('R', (), {'user': self.platform})()
        self.existing.name = 'Existente Renombrada'
        admin_instance.save_model(request, self.existing, form=None, change=True)

        # change=True must not silently recreate what an operator removed
        self.assertEqual(self.existing.areas.count(), len(PRESET_AREAS) - 1)

    # --- misc ---

    def test_unsaved_company_is_rejected(self):
        with self.assertRaises(ProvisioningError):
            provision_company_access_defaults(Company(name='X', slug='x'))

    def test_api_creation_is_audited_with_provisioning_summary(self):
        self._create_via_api(slug='auditada-sa', name='Auditada SA')
        log = AdminAuditLog.objects.filter(action='company_created').first()
        self.assertIsNotNone(log)
        self.assertEqual(len(log.metadata['areas_created']), len(PRESET_AREAS))
        self.assertEqual(len(log.metadata['roles_created']), len(PRESET_ROLES))

    def test_preset_roles_only_reference_assignable_capabilities(self):
        for _name, slug, _desc, caps in PRESET_ROLES:
            self.assertTrue(
                set(caps) <= ASSIGNABLE_CAPABILITY_CODES,
                f'preset {slug} referencia capacidades no asignables',
            )


class Phase2a1SurfaceSeparationTest(TestCase):
    """
    The three surfaces are surfaces, not user types.

    Holding a Membership does NOT remove e-commerce access — the earlier wording
    "external portal = User without Membership" was wrong.
    """

    def setUp(self):
        cache.clear()
        self.company = _saas_company('Superficies SA', 'surf-sa')
        self.product = _seeded(Product.objects.create(company=_pilot_company(),
            name='Producto Superficie', slug='producto-superficie',
            price=Decimal('100.00'), inventory=10,
        ))
        provision_company_access_defaults(self.company)

        # Carlos: customer AND technician of the same company
        self.carlos = _saas_user('surf_carlos')
        self.membership = Membership.objects.create(
            user=self.carlos, company=self.company, role='technician')
        _assign(self.membership, self.company.roles.get(slug='servicio-tecnico'))

    def test_membership_holder_is_still_an_external_customer(self):
        """The other direction of the corrected rule."""
        c = APIClient()

        with _storefront_of(_pilot_company()):
            # public catalogue, anonymously
            self.assertEqual(c.get('/api/products/').status_code, status.HTTP_200_OK)

            # ...and Carlos can still shop, Membership notwithstanding
            self.assertEqual(
                c.post('/api/cart/add/', {
                    'session_key': 'surf-carlos-cart',
                    'product': self.product.pk, 'quantity': 1,
                }, format='json').status_code,
                status.HTTP_200_OK,
            )
        login = c.post('/api/auth/login/', {
            'username': 'surf_carlos', 'password': 'Pass123!',
        }, format='json')
        self.assertEqual(login.status_code, status.HTTP_200_OK)
        self.assertTrue(login.cookies.get('blackdog_access')['httponly'])

    def test_anonymous_can_use_the_public_part_of_the_external_portal(self):
        c = APIClient()
        self.assertEqual(c.get('/api/products/').status_code, status.HTTP_200_OK)
        self.assertEqual(
            c.get('/api/cart/?session_key=surf-anon').status_code, status.HTTP_200_OK)

    def test_same_user_holds_internal_control_too(self):
        c = APIClient()
        c.force_authenticate(user=self.carlos)
        self.assertEqual(c.get('/api/me/company-access/').status_code, status.HTTP_200_OK)
        self.assertIn('service.manage', resolve_capabilities(self.carlos, self.company))

    def test_user_may_belong_to_two_companies_with_different_roles(self):
        other = _saas_company('Otra Superficie', 'surf-otra', tax_id='20444444444')
        provision_company_access_defaults(other)
        m2 = Membership.objects.create(user=self.carlos, company=other, role='customer')
        _assign(m2, other.roles.get(slug='ventas'))

        self.assertIn('service.manage', resolve_capabilities(self.carlos, self.company))
        self.assertNotIn('sales.orders.manage', resolve_capabilities(self.carlos, self.company))
        self.assertIn('sales.orders.manage', resolve_capabilities(self.carlos, other))
        self.assertNotIn('service.manage', resolve_capabilities(self.carlos, other))

    def test_no_company_object_can_create_a_platform_master(self):
        """Roles, areas, memberships and assignments must never reach is_superuser."""
        powerful = _role(self.company, 'Omnipotente',
                         sorted(ASSIGNABLE_CAPABILITY_CODES), 'omnipotente')
        _assign(self.membership, powerful)

        self.carlos.refresh_from_db()
        self.assertFalse(self.carlos.is_superuser)
        self.assertFalse(self.carlos.is_staff)
        self.assertFalse(is_platform_admin(self.carlos))
        # ...and a second company remains out of reach
        other = _saas_company('Fuera', 'surf-fuera', tax_id='20555555555')
        self.assertEqual(resolve_capabilities(self.carlos, other), frozenset())


# ---------------------------------------------------------------------------
# Development-only demo users — TEMPORARY
# ---------------------------------------------------------------------------

from io import StringIO  # noqa: E402

from django.core.management import call_command  # noqa: E402
from django.core.management.base import CommandError  # noqa: E402

from .management.commands.seed_demo_users import (  # noqa: E402
    ALL_DEMO_USERNAMES, DEMO_INTERNAL_USERS, DEMO_PASSWORD, demo_email,
)


@override_settings(DEBUG=True)
class DemoUsersCommandTest(TestCase):
    """
    `seed_demo_users` is a development fixture, not a product feature.

    These tests pin the safety properties: it refuses production, it never
    hijacks a real account, and no CompanyRole it creates can make anyone a
    platform master.
    """

    DEMO_COMPANY_SLUG = 'demo-users-co'

    def setUp(self):
        cache.clear()
        self.company = _saas_company('Demo Users SA', self.DEMO_COMPANY_SLUG)

    def _seed(self, slug=None):
        out = StringIO()
        call_command(
            'seed_demo_users', company_slug=slug or self.DEMO_COMPANY_SLUG, stdout=out,
        )
        return out.getvalue()

    def _purge(self):
        out = StringIO()
        call_command('seed_demo_users', purge=True, stdout=out)
        return out.getvalue()

    # --- 1: production is refused, with no escape hatch ---

    @override_settings(DEBUG=False)
    def test_01_debug_false_is_rejected(self):
        with self.assertRaises(CommandError) as ctx:
            call_command('seed_demo_users', company_slug=self.DEMO_COMPANY_SLUG)
        self.assertIn('desarrollo', str(ctx.exception))
        self.assertEqual(User.objects.filter(username__startswith='dev_').count(), 0)

    @override_settings(DEBUG=False)
    def test_01b_purge_is_also_rejected_in_production(self):
        with self.assertRaises(CommandError):
            call_command('seed_demo_users', purge=True)

    def test_01c_command_offers_no_production_override_flag(self):
        """
        Introspect the real parser rather than scanning the source text: this
        catches a bypass flag whatever it is called, and does not trip over
        prose that merely *mentions* one.
        """
        from store.management.commands.seed_demo_users import Command

        parser = Command().create_parser('manage.py', 'seed_demo_users')
        declared = {
            opt for action in parser._actions for opt in action.option_strings
        }
        self.assertEqual(
            declared,
            {'-h', '--help', '--version', '-v', '--verbosity', '--settings',
             '--pythonpath', '--traceback', '--no-color', '--force-color',
             '--skip-checks', '--company-slug', '--purge'},
        )
        for opt in declared:
            self.assertNotIn('force-production', opt)
            self.assertNotIn('ignore-debug', opt)

    @override_settings(DEBUG=False)
    def test_01d_unknown_bypass_kwargs_do_not_help(self):
        """
        An invented override keyword must not run the command.

        Django rejects unknown options with TypeError, which is itself the proof
        that no such escape hatch is declared; CommandError is accepted too so
        the test states the intent rather than Django's exact mechanism.
        """
        with self.assertRaises((TypeError, CommandError)):
            call_command(
                'seed_demo_users',
                company_slug=self.DEMO_COMPANY_SLUG,
                force_production=True,
            )
        self.assertEqual(User.objects.filter(username__startswith='dev_').count(), 0)

    # --- 2: the six accounts ---

    def test_02_creates_the_six_demo_users(self):
        self._seed()
        for username in ALL_DEMO_USERNAMES:
            user = User.objects.filter(username=username).first()
            self.assertIsNotNone(user, username)
            self.assertEqual(user.email, demo_email(username))
            self.assertTrue(user.check_password(DEMO_PASSWORD), username)
        self.assertEqual(len(ALL_DEMO_USERNAMES), 6)

    # --- 3-7: memberships ---

    def test_03_customer_gets_no_membership(self):
        self._seed()
        customer = User.objects.get(username='dev_customer')
        self.assertEqual(Membership.objects.filter(user=customer).count(), 0)
        self.assertEqual(customer.profile.role, UserProfile.ROLE_CUSTOMER)
        self.assertEqual(resolve_capabilities(customer, self.company), frozenset())

    def test_04_to_07_internal_users_get_the_right_membership(self):
        self._seed()
        for username, legacy_role, _role_slug, _area_slug in DEMO_INTERNAL_USERS:
            user = User.objects.get(username=username)
            membership = Membership.objects.filter(user=user, company=self.company).first()
            self.assertIsNotNone(membership, username)
            self.assertEqual(membership.role, legacy_role, username)
            self.assertTrue(membership.is_active, username)
            self.assertEqual(user.profile.role, legacy_role, username)

    # --- 8: custom role + area ---

    def test_08_internal_users_get_their_company_role_and_area(self):
        self._seed()
        for username, _legacy, role_slug, area_slug in DEMO_INTERNAL_USERS:
            membership = Membership.objects.get(
                user__username=username, company=self.company)
            assignment = membership.role_assignments.filter(is_active=True).first()
            self.assertIsNotNone(assignment, username)
            self.assertEqual(assignment.role.slug, role_slug, username)
            self.assertIsNotNone(assignment.area, username)
            self.assertEqual(assignment.area.slug, area_slug, username)
            # role and area belong to the same company as the membership
            self.assertEqual(assignment.role.company_id, self.company.pk)
            self.assertEqual(assignment.area.company_id, self.company.pk)

    def test_08b_internal_users_resolve_real_capabilities(self):
        self._seed()
        inventory = User.objects.get(username='dev_inventory')
        sales = User.objects.get(username='dev_sales')
        self.assertIn('inventory.adjust', resolve_capabilities(inventory, self.company))
        self.assertNotIn('inventory.adjust', resolve_capabilities(sales, self.company))
        self.assertIn('sales.orders.manage', resolve_capabilities(sales, self.company))

    # --- 9-10: idempotency ---

    def test_09_assignments_are_not_duplicated(self):
        self._seed()
        self._seed()
        self._seed()
        for username, _l, _r, _a in DEMO_INTERNAL_USERS:
            membership = Membership.objects.get(
                user__username=username, company=self.company)
            self.assertEqual(membership.role_assignments.count(), 1, username)

    def test_10_second_run_is_idempotent(self):
        self._seed()
        counts = (
            User.objects.filter(username__startswith='dev_').count(),
            Membership.objects.count(),
            MembershipRoleAssignment.objects.count(),
            CompanyArea.objects.filter(company=self.company).count(),
            CompanyRole.objects.filter(company=self.company).count(),
        )
        self._seed()
        self.assertEqual(
            (
                User.objects.filter(username__startswith='dev_').count(),
                Membership.objects.count(),
                MembershipRoleAssignment.objects.count(),
                CompanyArea.objects.filter(company=self.company).count(),
                CompanyRole.objects.filter(company=self.company).count(),
            ),
            counts,
        )

    def test_10b_reseeding_restores_a_disabled_assignment(self):
        self._seed()
        assignment = MembershipRoleAssignment.objects.filter(
            membership__user__username='dev_sales').first()
        assignment.is_active = False
        assignment.save()
        self._seed()
        assignment.refresh_from_db()
        self.assertTrue(assignment.is_active)

    # --- 11-13: MASTER ---

    def test_11_dev_master_is_a_platform_master_without_membership(self):
        self._seed()
        master = User.objects.get(username='dev_master')
        self.assertTrue(master.is_superuser)
        self.assertTrue(master.is_staff)
        self.assertEqual(Membership.objects.filter(user=master).count(), 0)
        self.assertTrue(is_platform_admin(master))

    def test_12_dev_admin_is_not_a_platform_master(self):
        self._seed()
        admin = User.objects.get(username='dev_admin')
        self.assertFalse(admin.is_superuser)
        self.assertFalse(admin.is_staff)
        self.assertFalse(is_platform_admin(admin))
        # ...even though it holds every company capability in its own company
        self.assertEqual(
            resolve_capabilities(admin, self.company), ASSIGNABLE_CAPABILITY_CODES)
        # ...and nothing at all in another one
        other = _saas_company('Ajena', 'demo-ajena', tax_id='20666666666')
        self.assertEqual(resolve_capabilities(admin, other), frozenset())

    def test_13_no_company_role_turns_a_user_into_a_master(self):
        self._seed()
        for username, _l, _r, _a in DEMO_INTERNAL_USERS:
            user = User.objects.get(username=username)
            self.assertFalse(user.is_superuser, username)
            self.assertFalse(is_platform_admin(user), username)

    # --- 14-15: company validation ---

    def test_14_unknown_company_is_rejected(self):
        with self.assertRaises(CommandError) as ctx:
            call_command('seed_demo_users', company_slug='no-existe')
        self.assertIn('no-existe', str(ctx.exception))
        self.assertEqual(User.objects.filter(username__startswith='dev_').count(), 0)

    def test_15_inactive_company_is_rejected(self):
        self.company.is_active = False
        self.company.save(update_fields=['is_active'])
        with self.assertRaises(CommandError) as ctx:
            call_command('seed_demo_users', company_slug=self.DEMO_COMPANY_SLUG)
        self.assertIn('desactivada', str(ctx.exception))

    def test_15b_company_slug_is_required(self):
        with self.assertRaises(CommandError):
            call_command('seed_demo_users')

    def test_15c_command_works_with_any_company_slug(self):
        """No tenant is hardcoded: a different company works identically."""
        other = _saas_company('Servicio Técnico X', 'servicio-tecnico-x',
                              tax_id='20777777777')
        self._seed(slug='servicio-tecnico-x')
        self.assertEqual(
            Membership.objects.filter(company=other).count(), len(DEMO_INTERNAL_USERS))
        self.assertEqual(Membership.objects.filter(company=self.company).count(), 0)

    def test_15d_command_source_hardcodes_no_tenant(self):
        import store.management.commands.seed_demo_users as mod
        source = open(mod.__file__, encoding='utf-8').read().lower()
        for token in ('black dog', 'blackdog', 'cmau', '20610159886'):
            self.assertNotIn(token, source)

    # --- 16: never hijack a real account ---

    def test_16_existing_non_demo_username_aborts(self):
        real = User.objects.create_user(
            username='dev_admin', email='persona.real@empresa.com', password='Real123!')
        with self.assertRaises(CommandError) as ctx:
            call_command('seed_demo_users', company_slug=self.DEMO_COMPANY_SLUG)
        self.assertIn('otra identidad', str(ctx.exception))

        real.refresh_from_db()
        self.assertEqual(real.email, 'persona.real@empresa.com')
        self.assertTrue(real.check_password('Real123!'))
        self.assertFalse(real.is_superuser)

    # --- 17-18: purge ---

    def test_17_purge_removes_the_demo_users(self):
        self._seed()
        self.assertEqual(
            User.objects.filter(username__startswith='dev_').count(), 6)
        self._purge()
        self.assertEqual(
            User.objects.filter(username__startswith='dev_').count(), 0)
        self.assertEqual(Membership.objects.count(), 0)
        self.assertEqual(MembershipRoleAssignment.objects.count(), 0)

    def test_17b_purge_leaves_company_areas_and_roles_alone(self):
        self._seed()
        areas = CompanyArea.objects.filter(company=self.company).count()
        roles = CompanyRole.objects.filter(company=self.company).count()
        self._purge()
        self.assertEqual(CompanyArea.objects.filter(company=self.company).count(), areas)
        self.assertEqual(CompanyRole.objects.filter(company=self.company).count(), roles)

    def test_18_purge_never_deletes_a_lookalike_account(self):
        real = User.objects.create_user(
            username='dev_master', email='jefe.real@empresa.com', password='Real123!')
        output = self._purge()
        real.refresh_from_db()
        self.assertTrue(User.objects.filter(username='dev_master').exists())
        self.assertEqual(real.email, 'jefe.real@empresa.com')
        self.assertIn('OMITIDO', output)

    def test_18b_purge_is_safe_when_nothing_was_seeded(self):
        self._purge()  # must not raise
        self.assertEqual(User.objects.filter(username__startswith='dev_').count(), 0)

    # --- 19-20: the rest of the system is untouched ---

    def test_19_internal_demo_users_can_still_use_the_public_storefront(self):
        """Holding a Membership does not remove e-commerce access."""
        self._seed()
        product = _seeded(Product.objects.create(company=_pilot_company(),
            name='Producto Demo Users', slug='producto-demo-users',
            price=Decimal('50.00'), inventory=5,
        ))
        c = APIClient()
        with _storefront_of(_pilot_company()):
            self.assertEqual(c.get('/api/products/').status_code, status.HTTP_200_OK)
            self.assertEqual(
                c.post('/api/cart/add/', {
                    'session_key': 'demo-users-cart', 'product': product.pk, 'quantity': 1,
                }, format='json').status_code,
                status.HTTP_200_OK,
            )

    def test_20_demo_users_authenticate_through_the_real_login(self):
        """No bypass: real login, real HttpOnly cookies, real CSRF flow."""
        self._seed()
        c = APIClient()
        res = c.post('/api/auth/login/', {
            'username': 'dev_technician', 'password': DEMO_PASSWORD,
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        access = res.cookies.get('blackdog_access')
        self.assertIsNotNone(access)
        self.assertTrue(access['httponly'])
        self.assertNotIn('token', res.data)

    def test_20b_demo_users_get_no_extra_authority(self):
        """dev_technician must not reach the legacy admin surface."""
        self._seed()
        technician = User.objects.get(username='dev_technician')
        c = APIClient()
        c.force_authenticate(user=technician)
        self.assertEqual(
            c.get('/api/admin/products/').status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(
            c.get('/api/admin/inventory/summary/').status_code, status.HTTP_403_FORBIDDEN)

    def test_20c_no_demo_user_is_created_without_running_the_command(self):
        """No migration, signal or import may create these accounts."""
        self.assertEqual(User.objects.filter(username__startswith='dev_').count(), 0)


# ---------------------------------------------------------------------------
# Phase 2A.2 — internal control dashboard endpoint
# ---------------------------------------------------------------------------

class InternalDashboardTest(TestCase):
    """
    GET /api/me/internal-dashboard/

    One safe snapshot of company context. The security surface is the same as
    every other SaaS endpoint: no membership means no access, and `?company=`
    only selects among companies the caller already reaches.
    """

    URL = '/api/me/internal-dashboard/'

    def setUp(self):
        cache.clear()
        self.company_a = _saas_company('Empresa A', 'dash-a')
        self.company_b = _saas_company('Empresa B', 'dash-b', tax_id='20888888888')
        provision_company_access_defaults(self.company_a)
        provision_company_access_defaults(self.company_b)
        self.branch_a = _saas_branch(self.company_a, name='Sucursal A')

        self.staff_a = _saas_user('dash_staff_a')
        self.staff_b = _saas_user('dash_staff_b')
        self.orphan = _saas_user('dash_orphan')
        self.platform = _saas_user('dash_platform', is_superuser=True)

        self.m_a = Membership.objects.create(
            user=self.staff_a, company=self.company_a, role='inventory',
            branch=self.branch_a,
        )
        _assign(self.m_a, self.company_a.roles.get(slug='inventario'),
                area=self.company_a.areas.get(slug='inventario'))
        Membership.objects.create(
            user=self.staff_b, company=self.company_b, role='admin')

    def _get(self, user, params=''):
        c = APIClient()
        c.force_authenticate(user=user)
        return c.get(f'{self.URL}{params}')

    # --- 1-3: who is refused ---

    def test_01_user_without_membership_is_denied(self):
        res = self._get(self.orphan)
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_01b_anonymous_is_denied(self):
        self.assertEqual(
            APIClient().get(self.URL).status_code, status.HTTP_401_UNAUTHORIZED)

    def test_01c_legacy_role_alone_grants_nothing(self):
        """UserProfile.role must not open the SaaS dashboard."""
        legacy = _saas_user('dash_legacy_admin')
        legacy.profile.role = UserProfile.ROLE_ADMIN
        legacy.profile.save()
        self.assertEqual(self._get(legacy).status_code, status.HTTP_403_FORBIDDEN)

    def test_02_inactive_membership_is_denied(self):
        self.m_a.is_active = False
        self.m_a.save(update_fields=['is_active'])
        self.assertEqual(self._get(self.staff_a).status_code, status.HTTP_403_FORBIDDEN)

    def test_03_inactive_company_is_denied(self):
        self.company_a.is_active = False
        self.company_a.save(update_fields=['is_active'])
        self.assertEqual(self._get(self.staff_a).status_code, status.HTTP_403_FORBIDDEN)

    # --- 4: the happy path ---

    def test_04_active_membership_gets_its_own_company(self):
        res = self._get(self.staff_a)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['company']['id'], self.company_a.pk)
        self.assertFalse(res.data['requires_company_selection'])
        self.assertEqual(res.data['membership']['branch']['name'], 'Sucursal A')
        self.assertEqual(res.data['access']['legacy_role'], 'inventory')
        self.assertEqual(res.data['access']['source'], 'custom_roles')
        self.assertIn('inventory.adjust', res.data['access']['capabilities'])
        self.assertEqual(
            [r['slug'] for r in res.data['access']['roles']], ['inventario'])
        self.assertEqual(
            [a['slug'] for a in res.data['access']['areas']], ['inventario'])

    # --- 5: cross tenant ---

    def test_05_user_of_a_cannot_get_dashboard_of_b(self):
        res = self._get(self.staff_a, f'?company={self.company_b.pk}')
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_05b_switcher_lists_only_own_companies(self):
        res = self._get(self.staff_a)
        ids = {c['id'] for c in res.data['available_companies']}
        self.assertEqual(ids, {self.company_a.pk})

    # --- 6: multi-membership ---

    def test_06_multi_membership_requires_selection(self):
        Membership.objects.create(
            user=self.staff_a, company=self.company_b, role='sales')
        res = self._get(self.staff_a)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertTrue(res.data['requires_company_selection'])
        self.assertIsNone(res.data['company'])
        self.assertIsNone(res.data['organization'])
        self.assertEqual(res.data['access']['capabilities'], [])
        self.assertEqual(len(res.data['available_companies']), 2)

    def test_06b_multi_membership_selection_works_for_own_companies(self):
        Membership.objects.create(
            user=self.staff_a, company=self.company_b, role='sales')
        res = self._get(self.staff_a, f'?company={self.company_b.pk}')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['company']['id'], self.company_b.pk)
        self.assertEqual(res.data['access']['legacy_role'], 'sales')

    # --- 7-8: platform master ---

    def test_07_master_may_select_a_company_explicitly(self):
        res = self._get(self.platform, f'?company={self.company_b.pk}')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['company']['id'], self.company_b.pk)
        self.assertTrue(res.data['access']['is_platform_admin'])
        # Platform authority is not a company role
        self.assertIsNone(res.data['access']['legacy_role'])
        self.assertIsNone(res.data['membership'])

    def test_08_master_without_selection_gets_no_arbitrary_company(self):
        res = self._get(self.platform)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertTrue(res.data['requires_company_selection'])
        self.assertIsNone(res.data['company'])
        self.assertIsNone(res.data['organization'])

    def test_08b_master_switcher_sees_every_tenant(self):
        res = self._get(self.platform)
        ids = {c['id'] for c in res.data['available_companies']}
        self.assertIn(self.company_a.pk, ids)
        self.assertIn(self.company_b.pk, ids)

    # --- 9: no information leaks ---

    def test_09_missing_and_foreign_company_answer_identically(self):
        foreign = self._get(self.staff_a, f'?company={self.company_b.pk}')
        missing = self._get(self.staff_a, '?company=999999')
        self.assertEqual(foreign.status_code, missing.status_code)
        self.assertEqual(foreign.data['detail'], missing.data['detail'])

    def test_09b_invalid_company_parameter_is_rejected(self):
        res = self._get(self.staff_a, '?company=abc')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_09c_foreign_company_name_never_leaks(self):
        raw = str(self._get(self.staff_a, f'?company={self.company_b.pk}').data)
        self.assertNotIn('Empresa B', raw)
        self.assertNotIn('dash-b', raw)

    # --- 10: counters are company scoped ---

    def test_10_organization_counts_only_the_selected_company(self):
        _saas_branch(self.company_b, name='Extra B1')
        _saas_branch(self.company_b, name='Extra B2')
        res = self._get(self.staff_a)
        # The count must be A's OWN branches, whatever that number is — asserting
        # a literal would break every time provisioning changes what a new tenant
        # starts with (Phase 2D gave it a first branch). What matters is that B's
        # branches are not in it, which the second assertion pins down.
        own = Branch.objects.filter(company=self.company_a, is_active=True).count()
        self.assertEqual(res.data['organization']['active_branches'], own)
        self.assertLess(
            res.data['organization']['active_branches'],
            Branch.objects.filter(is_active=True).count(),
            'el conteo no puede incluir sucursales de otra empresa',
        )
        self.assertEqual(res.data['organization']['active_memberships'], 1)
        self.assertEqual(
            res.data['organization']['active_areas'],
            self.company_a.areas.filter(is_active=True).count())

    def test_10b_organization_is_withheld_without_company_view(self):
        """Counters are company information: they need the capability."""
        stripped = _role(self.company_a, 'Sin ver', [], 'sin-ver')
        MembershipRoleAssignment.objects.filter(membership=self.m_a).delete()
        _assign(self.m_a, stripped)
        res = self._get(self.staff_a)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIsNone(res.data['organization'])

    # --- 11-12: the rest of the system ---

    def test_11_customer_without_membership_still_uses_the_storefront(self):
        product = _seeded(Product.objects.create(company=_pilot_company(),
            name='Producto Dash', slug='producto-dash',
            price=Decimal('20.00'), inventory=3))
        c = APIClient()
        with _storefront_of(_pilot_company()):
            self.assertEqual(c.get('/api/products/').status_code, status.HTTP_200_OK)
            self.assertEqual(
                c.post('/api/cart/add/', {
                    'session_key': 'dash-cart', 'product': product.pk, 'quantity': 1,
                }, format='json').status_code,
                status.HTTP_200_OK,
            )
        self.assertEqual(self._get(self.orphan).status_code, status.HTTP_403_FORBIDDEN)

    def test_12_response_exposes_no_other_tenants_data(self):
        """
        Catalogue, sales and inventory figures are REAL now — 2B, 2C and 2D
        tenantised the models behind them — so the rule is no longer "no
        commercial data". It is: nothing from another tenant, and nothing
        derived from a cost the platform does not have.

        The check that matters is a product of a DIFFERENT company: it must not
        appear, and neither must its 77 units.
        """
        _seeded(Product.objects.create(company=_pilot_company(),
            name='Producto Global', slug='producto-global',
            price=Decimal('999.00'), inventory=77))
        data = self._get(self.staff_a).data
        raw = str(data).lower()

        for forbidden in ('producto global', 'stripe', 'profit', 'margin', 'utilidad'):
            self.assertNotIn(forbidden, raw, f'la respuesta expone "{forbidden}"')

        # The other tenant's 77 units are not in this company's totals.
        self.assertEqual(data['catalog']['products'], 0)
        self.assertEqual(data['inventory']['total_units'], 0)

        # And the money figure that IS shown says what it is made of.
        self.assertEqual(data['inventory']['value_basis'], 'sale_price')

    # --- alerts ---

    def test_alerts_report_only_safely_derivable_conditions(self):
        res = self._get(self.staff_a)
        codes = {a['code'] for a in res.data['alerts']}
        # staff_a has a branch and capabilities: nothing to warn about
        self.assertNotIn('no_branch_assigned', codes)
        self.assertNotIn('no_capabilities', codes)

    def test_alert_states_the_real_branch_scope(self):
        """
        Phase 2D replaced the old "sin sucursal asignada / pendiente" placeholder.
        Branch scope is explicit now, so the alert states which of the two rules
        applies instead of describing a missing feature.
        """
        from .models import Membership, MembershipBranchAccess

        self.m_a.branch = None
        self.m_a.branch_access_mode = Membership.ACCESS_MODE_ALL
        self.m_a.save(update_fields=['branch', 'branch_access_mode'])
        codes = {a['code'] for a in self._get(self.staff_a).data['alerts']}
        self.assertIn('branch_scope_all', codes)

        # SELECTED with nothing granted is a real state, and it warns: this
        # person cannot operate inventory anywhere until somebody grants a branch.
        self.m_a.branch_access_mode = Membership.ACCESS_MODE_SELECTED
        self.m_a.save(update_fields=['branch_access_mode'])
        MembershipBranchAccess.objects.filter(membership=self.m_a).delete()
        codes = {a['code'] for a in self._get(self.staff_a).data['alerts']}
        self.assertIn('no_branch_access', codes)
        self.assertNotIn('branch_scope_all', codes)

    def test_alert_when_an_assigned_role_has_no_capabilities(self):
        """The specific alert names the offending role, which is more actionable."""
        empty = _role(self.company_a, 'Vacío', [], 'vacio')
        MembershipRoleAssignment.objects.filter(membership=self.m_a).delete()
        _assign(self.m_a, empty)
        alerts = self._get(self.staff_a).data['alerts']
        codes = {a['code'] for a in alerts}
        self.assertIn('role_without_capabilities', codes)
        self.assertTrue(
            any('Vacío' in a['title'] for a in alerts),
            'la alerta debe nombrar el rol sin permisos',
        )

    def test_alert_when_membership_has_no_role_at_all(self):
        """The generic alert covers a membership with no assignment and no fallback."""
        MembershipRoleAssignment.objects.filter(membership=self.m_a).delete()
        self.m_a.role = UserProfile.ROLE_CUSTOMER  # legacy fallback grants nothing
        self.m_a.save(update_fields=['role'])
        codes = {a['code'] for a in self._get(self.staff_a).data['alerts']}
        self.assertIn('no_capabilities', codes)

    def test_alert_when_master_views_a_company_without_belonging(self):
        codes = {
            a['code']
            for a in self._get(self.platform, f'?company={self.company_a.pk}').data['alerts']
        }
        self.assertIn('platform_admin_no_membership', codes)

    # --- demo users behave as documented ---

    @override_settings(DEBUG=True)
    def test_demo_users_reach_the_dashboard_but_not_every_module(self):
        call_command('seed_demo_users', company_slug='dash-a', stdout=StringIO())

        technician = User.objects.get(username='dev_technician')
        res = self._get(technician)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['company']['id'], self.company_a.pk)

        # ...and still cannot reach inventory or products
        c = APIClient()
        c.force_authenticate(user=technician)
        self.assertEqual(
            c.get('/api/admin/inventory/summary/').status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(
            c.get('/api/admin/products/').status_code, status.HTTP_403_FORBIDDEN)

        # dev_customer has no internal access at all
        customer = User.objects.get(username='dev_customer')
        self.assertEqual(self._get(customer).status_code, status.HTTP_403_FORBIDDEN)

    @override_settings(DEBUG=True)
    def test_demo_master_must_choose_a_company(self):
        call_command('seed_demo_users', company_slug='dash-a', stdout=StringIO())
        master = User.objects.get(username='dev_master')
        res = self._get(master)
        self.assertTrue(res.data['requires_company_selection'])
        self.assertTrue(res.data['access']['is_platform_admin'])


# ---------------------------------------------------------------------------
# Phase 2B — tenant-aware catalogue
# ---------------------------------------------------------------------------

from .tenancy import (  # noqa: E402
    CATALOG_SOURCE_LEGACY, CATALOG_SOURCE_TENANT, legacy_catalog_company,
    pilot_company, resolve_catalog_company, resolve_storefront_company,
    storefront_categories, storefront_products,
)


def _cat(company, name, slug):
    return Category.objects.create(company=company, name=name, slug=slug)


def _prod(company, name, slug, category=None, price='100.00', inventory=10, is_active=True):
    return _seeded(Product.objects.create(
        company=company, name=name, slug=slug, category=category,
        price=Decimal(price), inventory=inventory, is_active=is_active,
    ))


class Phase2bCatalogModelTest(TestCase):
    """Ownership, per-company uniqueness and the category/product invariant."""

    def setUp(self):
        self.a = _saas_company('Empresa A', 'cat-a')
        self.b = _saas_company('Empresa B', 'cat-b', tax_id='20777000001')

    def test_backfill_assigned_the_seed_catalogue_to_the_pilot(self):
        pilot = pilot_company()
        self.assertIsNotNone(pilot)
        self.assertGreater(Product.objects.filter(company=pilot).count(), 0)
        self.assertEqual(Product.objects.filter(company__isnull=True).count(), 0)
        self.assertEqual(Category.objects.filter(company__isnull=True).count(), 0)

    def test_same_category_slug_may_exist_in_two_companies(self):
        # The seed migration already gave the pilot an "iphone" category, so this
        # asserts per company rather than globally — which is the point.
        _cat(self.a, 'iPhone', 'iphone')
        _cat(self.b, 'iPhone', 'iphone')  # must not clash
        self.assertEqual(Category.objects.filter(slug='iphone', company=self.a).count(), 1)
        self.assertEqual(Category.objects.filter(slug='iphone', company=self.b).count(), 1)
        self.assertGreaterEqual(Category.objects.filter(slug='iphone').count(), 2)

    def test_category_slug_is_unique_within_a_company(self):
        _cat(self.a, 'iPhone', 'iphone')
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                _cat(self.a, 'iPhone Bis', 'iphone')

    def test_same_product_slug_may_exist_in_two_companies(self):
        _prod(self.a, 'iPhone 15', 'iphone-15')
        _prod(self.b, 'iPhone 15', 'iphone-15')
        self.assertEqual(Product.objects.filter(slug='iphone-15').count(), 2)

    def test_product_slug_is_unique_within_a_company(self):
        _prod(self.a, 'iPhone 15', 'iphone-15')
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                _prod(self.a, 'Otro', 'iphone-15')

    def test_product_rejects_a_category_of_another_company(self):
        foreign = _cat(self.b, 'Ajena', 'ajena')
        with self.assertRaises(DjangoValidationError):
            _prod(self.a, 'Producto', 'producto', category=foreign)

    def test_product_accepts_a_category_of_its_own_company(self):
        own = _cat(self.a, 'Propia', 'propia')
        product = _prod(self.a, 'Producto', 'producto', category=own)
        self.assertEqual(product.category_id, own.pk)

    def test_company_with_catalogue_cannot_be_deleted(self):
        _prod(self.a, 'Protegido', 'protegido')
        with self.assertRaises(Exception):
            with transaction.atomic():
                self.a.delete()

    def test_a_new_company_starts_with_an_empty_catalogue(self):
        fresh = _saas_company('Nueva', 'cat-nueva', tax_id='20777000002')
        provision_company_access_defaults(fresh)
        self.assertEqual(fresh.products.count(), 0)
        self.assertEqual(fresh.categories.count(), 0)


class Phase2bStorefrontResolutionTest(TestCase):
    """The public catalogue resolves ONE tenant, and never guesses."""

    def setUp(self):
        cache.clear()
        self.pilot = pilot_company()
        self.b = _saas_company('Empresa B', 'store-b', tax_id='20777000003')
        self.p_a = _prod(self.pilot, 'Producto A', 'producto-compartido')
        self.p_b = _prod(self.b, 'Producto B', 'producto-compartido')

    def _factory(self, host='testserver'):
        from django.test import RequestFactory
        return RequestFactory(SERVER_NAME=host).get('/api/products/')

    def test_host_subdomain_selects_the_tenant(self):
        with override_settings(ALLOWED_HOSTS=['*']):
            self.assertEqual(
                resolve_storefront_company(self._factory('store-b.example.com')), self.b)

    def test_host_ignores_the_port(self):
        with override_settings(ALLOWED_HOSTS=['*']):
            self.assertEqual(
                resolve_storefront_company(self._factory('store-b.example.com:8443')), self.b)

    def test_setting_is_used_when_the_host_says_nothing(self):
        with override_settings(DEFAULT_STOREFRONT_COMPANY_SLUG='store-b'):
            self.assertEqual(resolve_storefront_company(self._factory()), self.b)

    def test_inactive_company_never_serves_a_storefront(self):
        self.b.is_active = False
        self.b.save(update_fields=['is_active'])
        with override_settings(DEFAULT_STOREFRONT_COMPANY_SLUG='store-b',
                               ALLOWED_HOSTS=['*']):
            self.assertIsNone(resolve_storefront_company(self._factory()))
            self.assertIsNone(
                resolve_storefront_company(self._factory('store-b.example.com')))

    def test_unknown_host_and_unset_setting_resolve_to_nothing(self):
        """Two companies, no hint: an EMPTY catalogue is the safe failure."""
        with override_settings(DEFAULT_STOREFRONT_COMPANY_SLUG='', ALLOWED_HOSTS=['*']):
            self.assertIsNone(resolve_storefront_company(self._factory('nadie.example.com')))
            self.assertEqual(storefront_products(self._factory()).count(), 0)

    def test_never_falls_back_to_the_first_company(self):
        """The dangerous fallback must not exist: two tenants, no guess."""
        with override_settings(DEFAULT_STOREFRONT_COMPANY_SLUG=''):
            self.assertIsNone(resolve_storefront_company(self._factory()))

    def test_single_active_company_resolves_unambiguously(self):
        """An existing single-store install must keep serving after the upgrade."""
        self.b.is_active = False
        self.b.save(update_fields=['is_active'])
        with override_settings(DEFAULT_STOREFRONT_COMPANY_SLUG=''):
            self.assertEqual(resolve_storefront_company(self._factory()), self.pilot)

    def test_reserved_subdomains_are_ignored(self):
        with override_settings(DEFAULT_STOREFRONT_COMPANY_SLUG='', ALLOWED_HOSTS=['*']):
            for host in ('www.example.com', 'api.example.com', 'admin.example.com',
                         'app.example.com', 'example.com'):
                self.assertIsNone(resolve_storefront_company(self._factory(host)), host)


class Phase2bPublicCatalogIsolationTest(TestCase):
    """Each storefront serves only its own catalogue."""

    def setUp(self):
        cache.clear()
        # Two purpose-built tenants rather than reusing the pilot: the seed
        # migration already gave the pilot an "iphone" category, and this test is
        # about two storefronts holding the SAME slugs.
        self.a = _saas_company('Empresa A', 'pub-a', tax_id='20777000010')
        self.b = _saas_company('Empresa B', 'pub-b', tax_id='20777000004')

        self.cat_a = _cat(self.a, 'iPhone', 'iphone')
        self.cat_b = _cat(self.b, 'iPhone', 'iphone')
        self.prod_a = _prod(self.a, 'iPhone 15 A', 'iphone-15', category=self.cat_a)
        self.prod_b = _prod(self.b, 'iPhone 15 B', 'iphone-15', category=self.cat_b)
        self.client = APIClient()

    def _as_storefront(self, company):
        return _storefront_of(company)

    def test_product_list_is_isolated(self):
        with self._as_storefront(self.b):
            slugs = {p['slug'] for p in self.client.get('/api/products/').json()}
            names = {p['name'] for p in self.client.get('/api/products/').json()}
        self.assertIn('iPhone 15 B', names)
        self.assertNotIn('iPhone 15 A', names)
        self.assertIn('iphone-15', slugs)

    def test_same_slug_resolves_per_storefront(self):
        with self._as_storefront(self.a):
            a = self.client.get('/api/products/?slug=iphone-15').json()
        with self._as_storefront(self.b):
            b = self.client.get('/api/products/?slug=iphone-15').json()
        self.assertEqual(len(a), 1)
        self.assertEqual(len(b), 1)
        self.assertEqual(a[0]['name'], 'iPhone 15 A')
        self.assertEqual(b[0]['name'], 'iPhone 15 B')

    def test_search_is_isolated(self):
        with self._as_storefront(self.b):
            names = {p['name'] for p in self.client.get('/api/products/?search=iPhone').json()}
        self.assertEqual(names, {'iPhone 15 B'})

    def test_category_filter_is_isolated(self):
        """A category slug shared by both tenants filters within the storefront."""
        with self._as_storefront(self.b):
            names = {p['name'] for p in self.client.get('/api/products/?category=iphone').json()}
        self.assertEqual(names, {'iPhone 15 B'})

    def test_category_list_is_isolated(self):
        with self._as_storefront(self.b):
            ids = {c['id'] for c in self.client.get('/api/categories/').json()}
        self.assertIn(self.cat_b.pk, ids)
        self.assertNotIn(self.cat_a.pk, ids)

    def test_reviews_are_isolated_through_their_product(self):
        Review.objects.create(product=self.prod_a, author_name='X', rating=5, comment='ok')
        with self._as_storefront(self.b):
            data = self.client.get(f'/api/reviews/?product={self.prod_a.pk}').json()
        self.assertEqual(len(data), 0)

    def test_unresolved_storefront_serves_an_empty_catalogue(self):
        with override_settings(DEFAULT_STOREFRONT_COMPANY_SLUG=''):
            self.assertEqual(self.client.get('/api/products/').json(), [])
            self.assertEqual(self.client.get('/api/categories/').json(), [])

    # --- cart boundary ---

    def test_cart_rejects_a_product_of_another_storefront(self):
        with self._as_storefront(self.b):
            res = self.client.post('/api/cart/add/', {
                'session_key': 'cross-cart', 'product': self.prod_a.pk, 'quantity': 1,
            }, format='json')
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(CartItem.objects.filter(session_key='cross-cart').count(), 0)

    def test_cart_accepts_a_product_of_its_own_storefront(self):
        with self._as_storefront(self.b):
            res = self.client.post('/api/cart/add/', {
                'session_key': 'own-cart', 'product': self.prod_b.pk, 'quantity': 1,
            }, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(CartItem.objects.filter(session_key='own-cart').count(), 1)


class Phase2bAdminCatalogIsolationTest(TestCase):
    """Internal catalogue endpoints are tenant-scoped and capability-driven."""

    def setUp(self):
        cache.clear()
        self.a = _saas_company('Empresa A', 'adm-a', tax_id='20777000005')
        self.b = _saas_company('Empresa B', 'adm-b', tax_id='20777000006')
        provision_company_access_defaults(self.a)
        provision_company_access_defaults(self.b)

        self.cat_a = _cat(self.a, 'Cat A', 'cat-a-slug')
        self.cat_b = _cat(self.b, 'Cat B', 'cat-b-slug')
        self.prod_a = _prod(self.a, 'Producto A', 'prod-a', category=self.cat_a)
        self.prod_b = _prod(self.b, 'Producto B', 'prod-b', category=self.cat_b)

        # admin of A, through the SaaS model (custom role with products.manage)
        self.admin_a = _saas_user('cat_admin_a')
        m = Membership.objects.create(user=self.admin_a, company=self.a, role='admin')
        _assign(m, self.a.roles.get(slug='administrador'))

        # a member of A that can only VIEW the catalogue
        self.viewer_a = _saas_user('cat_viewer_a')
        mv = Membership.objects.create(user=self.viewer_a, company=self.a, role='customer')
        _assign(mv, _role(self.a, 'Solo ver', ['company.view', 'products.view'], 'solo-ver-cat'))

        self.admin_b = _saas_user('cat_admin_b')
        mb = Membership.objects.create(user=self.admin_b, company=self.b, role='admin')
        _assign(mb, self.b.roles.get(slug='administrador'))

        self.platform = _saas_user('cat_platform', is_superuser=True)

    def _as(self, user):
        c = APIClient()
        c.force_authenticate(user=user)
        return c

    # --- listing ---

    def test_admin_of_a_lists_only_its_own_products(self):
        res = self._as(self.admin_a).get('/api/admin/products/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        slugs = {p['slug'] for p in res.data['results']}
        self.assertIn('prod-a', slugs)
        self.assertNotIn('prod-b', slugs)

    def test_admin_of_a_lists_only_its_own_categories(self):
        res = self._as(self.admin_a).get('/api/admin/categories/')
        slugs = {c['slug'] for c in res.data}
        self.assertIn('cat-a-slug', slugs)
        self.assertNotIn('cat-b-slug', slugs)

    def test_search_does_not_cross_tenants(self):
        res = self._as(self.admin_a).get('/api/admin/products/?search=Producto')
        slugs = {p['slug'] for p in res.data['results']}
        self.assertEqual(slugs, {'prod-a'})

    # --- detail ---

    def test_foreign_product_detail_answers_like_a_missing_one(self):
        foreign = self._as(self.admin_a).get(f'/api/admin/products/{self.prod_b.pk}/')
        missing = self._as(self.admin_a).get('/api/admin/products/999999/')
        self.assertEqual(foreign.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(foreign.status_code, missing.status_code)

    def test_admin_of_a_cannot_modify_a_product_of_b(self):
        res = self._as(self.admin_a).patch(
            f'/api/admin/products/{self.prod_b.pk}/', {'name': 'Secuestrado'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
        self.prod_b.refresh_from_db()
        self.assertEqual(self.prod_b.name, 'Producto B')

    def test_admin_of_a_cannot_deactivate_a_product_of_b(self):
        res = self._as(self.admin_a).patch(
            f'/api/admin/products/{self.prod_b.pk}/', {'is_active': False}, format='json')
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
        self.prod_b.refresh_from_db()
        self.assertTrue(self.prod_b.is_active)

    # --- creation ---

    def test_created_product_belongs_to_the_callers_company(self):
        res = self._as(self.admin_a).post('/api/admin/products/', {
            'name': 'Nuevo A', 'slug': 'nuevo-a', 'price': '50.00', 'inventory': 1,
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Product.objects.get(slug='nuevo-a').company_id, self.a.pk)

    def test_company_in_the_payload_is_ignored(self):
        """Mass assignment must not move a product into another tenant."""
        res = self._as(self.admin_a).post('/api/admin/products/', {
            'name': 'Intruso', 'slug': 'intruso', 'price': '50.00', 'inventory': 1,
            'company': self.b.pk,
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Product.objects.get(slug='intruso').company_id, self.a.pk)

    def test_product_cannot_be_moved_between_companies_by_patch(self):
        res = self._as(self.admin_a).patch(
            f'/api/admin/products/{self.prod_a.pk}/',
            {'company': self.b.pk, 'name': 'Renombrado'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.prod_a.refresh_from_db()
        self.assertEqual(self.prod_a.company_id, self.a.pk)
        self.assertEqual(self.prod_a.name, 'Renombrado')

    def test_product_rejects_a_category_of_another_company_via_api(self):
        res = self._as(self.admin_a).post('/api/admin/products/', {
            'name': 'Con categoría ajena', 'slug': 'cat-ajena', 'price': '10.00',
            'inventory': 1, 'category': self.cat_b.pk,
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(Product.objects.filter(slug='cat-ajena').exists())

    def test_created_category_belongs_to_the_callers_company(self):
        res = self._as(self.admin_a).post('/api/admin/categories/', {
            'name': 'Nueva Cat', 'slug': 'nueva-cat',
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Category.objects.get(slug='nueva-cat').company_id, self.a.pk)

    def test_same_slug_can_be_created_in_two_companies_via_api(self):
        for user, company in ((self.admin_a, self.a), (self.admin_b, self.b)):
            res = self._as(user).post('/api/admin/products/', {
                'name': 'Compartido', 'slug': 'compartido', 'price': '10.00', 'inventory': 1,
            }, format='json')
            self.assertEqual(res.status_code, status.HTTP_201_CREATED, company.slug)
        self.assertEqual(Product.objects.filter(slug='compartido').count(), 2)

    # --- capabilities are real authority now ---

    def test_products_view_alone_cannot_write(self):
        res = self._as(self.viewer_a).post('/api/admin/products/', {
            'name': 'No permitido', 'slug': 'no-permitido', 'price': '10.00', 'inventory': 1,
        }, format='json')
        self.assertIn(res.status_code, (status.HTTP_403_FORBIDDEN,))
        self.assertFalse(Product.objects.filter(slug='no-permitido').exists())

    def test_products_view_can_read(self):
        res = self._as(self.viewer_a).get('/api/admin/products/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)

    def test_member_without_product_capabilities_is_refused(self):
        user = _saas_user('cat_no_caps')
        m = Membership.objects.create(user=user, company=self.a, role='customer')
        _assign(m, _role(self.a, 'Nada', ['company.view'], 'nada-cat'))
        self.assertEqual(
            self._as(user).get('/api/admin/products/').status_code,
            status.HTTP_403_FORBIDDEN)

    def test_inactive_membership_loses_catalogue_access(self):
        Membership.objects.filter(user=self.admin_a, company=self.a).update(is_active=False)
        self.assertEqual(
            self._as(self.admin_a).get('/api/admin/products/').status_code,
            status.HTTP_403_FORBIDDEN)

    def test_inactive_company_loses_catalogue_access(self):
        self.a.is_active = False
        self.a.save(update_fields=['is_active'])
        self.assertEqual(
            self._as(self.admin_a).get('/api/admin/products/').status_code,
            status.HTTP_403_FORBIDDEN)

    def test_user_without_membership_or_legacy_role_is_refused(self):
        orphan = _saas_user('cat_orphan')
        self.assertEqual(
            self._as(orphan).get('/api/admin/products/').status_code,
            status.HTTP_403_FORBIDDEN)

    # --- platform master ---

    def test_master_selects_a_company_explicitly(self):
        res_a = self._as(self.platform).get(f'/api/admin/products/?company={self.a.pk}')
        res_b = self._as(self.platform).get(f'/api/admin/products/?company={self.b.pk}')
        self.assertEqual({p['slug'] for p in res_a.data['results']}, {'prod-a'})
        self.assertEqual({p['slug'] for p in res_b.data['results']}, {'prod-b'})

    def test_master_never_sees_tenants_mixed(self):
        res = self._as(self.platform).get(f'/api/admin/products/?company={self.a.pk}')
        slugs = {p['slug'] for p in res.data['results']}
        self.assertNotIn('prod-b', slugs)

    def test_master_without_selection_gets_no_arbitrary_catalogue(self):
        res = self._as(self.platform).get('/api/admin/products/')
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    # --- multi-company user ---

    def test_multi_company_user_does_not_inherit_permissions_across_tenants(self):
        """Manage in A, view-only in B — the roles must not bleed."""
        user = _saas_user('cat_multi')
        ma = Membership.objects.create(user=user, company=self.a, role='admin')
        _assign(ma, self.a.roles.get(slug='administrador'))
        mb = Membership.objects.create(user=user, company=self.b, role='customer')
        _assign(mb, _role(self.b, 'Solo ver B', ['company.view', 'products.view'], 'solo-ver-b'))

        client = self._as(user)
        # In A: may create
        self.assertEqual(
            client.post(f'/api/admin/products/?company={self.a.pk}', {
                'name': 'En A', 'slug': 'en-a', 'price': '10.00', 'inventory': 1,
            }, format='json').status_code,
            status.HTTP_201_CREATED,
        )
        # In B: may read...
        self.assertEqual(
            client.get(f'/api/admin/products/?company={self.b.pk}').status_code,
            status.HTTP_200_OK)
        # ...but not write
        self.assertEqual(
            client.post(f'/api/admin/products/?company={self.b.pk}', {
                'name': 'En B', 'slug': 'en-b', 'price': '10.00', 'inventory': 1,
            }, format='json').status_code,
            status.HTTP_403_FORBIDDEN)
        self.assertFalse(Product.objects.filter(slug='en-b').exists())


class Phase2bLegacyBridgeTest(TestCase):
    """
    The legacy bridge keeps pre-SaaS operators working — on the pilot ONLY.

    The failure mode this guards against: a legacy admin with no Membership
    quietly administering every tenant's catalogue.
    """

    def setUp(self):
        cache.clear()
        self.pilot = pilot_company()
        self.other = _saas_company('Otra', 'bridge-otra', tax_id='20777000007')
        self.prod_pilot = _prod(self.pilot, 'Del piloto', 'del-piloto')
        self.prod_other = _prod(self.other, 'De la otra', 'de-la-otra')

        self.legacy_admin = _saas_user('bridge_admin')
        self.legacy_admin.profile.role = UserProfile.ROLE_ADMIN
        self.legacy_admin.profile.save()

    def _as(self, user):
        c = APIClient()
        c.force_authenticate(user=user)
        return c

    def test_legacy_admin_keeps_working(self):
        res = self._as(self.legacy_admin).get('/api/admin/products/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)

    def test_legacy_admin_sees_only_the_pilot_catalogue(self):
        """The whole point: the bridge must not hand over every tenant."""
        res = self._as(self.legacy_admin).get('/api/admin/products/')
        slugs = {p['slug'] for p in res.data['results']}
        self.assertIn('del-piloto', slugs)
        self.assertNotIn('de-la-otra', slugs)

    def test_legacy_admin_cannot_touch_another_tenants_product(self):
        res = self._as(self.legacy_admin).patch(
            f'/api/admin/products/{self.prod_other.pk}/', {'name': 'X'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_bridge_resolves_only_the_pilot(self):
        self.assertEqual(legacy_catalog_company(self.legacy_admin), self.pilot)

    def test_bridge_does_not_apply_to_customers(self):
        customer = _saas_user('bridge_customer')
        self.assertIsNone(legacy_catalog_company(customer))

    def test_bridge_does_not_apply_to_technicians(self):
        tech = _saas_user('bridge_tech')
        tech.profile.role = UserProfile.ROLE_TECHNICIAN
        tech.profile.save()
        self.assertIsNone(legacy_catalog_company(tech))

    def test_bridge_does_not_apply_to_users_with_a_membership(self):
        """A real company context always wins over the bridge."""
        Membership.objects.create(
            user=self.legacy_admin, company=self.other, role='admin')
        self.assertIsNone(legacy_catalog_company(self.legacy_admin))
        company, source = resolve_catalog_company(self.legacy_admin)
        self.assertEqual(company, self.other)
        self.assertEqual(source, CATALOG_SOURCE_TENANT)

    def test_bridge_source_is_reported(self):
        company, source = resolve_catalog_company(self.legacy_admin)
        self.assertEqual(company, self.pilot)
        self.assertEqual(source, CATALOG_SOURCE_LEGACY)

    def test_naming_a_foreign_company_does_not_fall_back_to_the_bridge(self):
        company, source = resolve_catalog_company(
            self.legacy_admin, requested_company_id=self.other.pk)
        self.assertIsNone(company)
        self.assertIsNone(source)


class Phase2bDashboardCatalogTest(TestCase):
    """Catalogue counters on the internal dashboard are per tenant."""

    URL = '/api/me/internal-dashboard/'

    def setUp(self):
        cache.clear()
        self.a = _saas_company('Empresa A', 'dashcat-a', tax_id='20777000008')
        self.b = _saas_company('Empresa B', 'dashcat-b', tax_id='20777000009')
        provision_company_access_defaults(self.a)
        provision_company_access_defaults(self.b)

        for i in range(3):
            _prod(self.a, f'A{i}', f'a-{i}')
        for i in range(8):
            _prod(self.b, f'B{i}', f'b-{i}')
        _cat(self.a, 'Cat A', 'dashcat-cat-a')

        self.user_a = _saas_user('dashcat_a')
        ma = Membership.objects.create(user=self.user_a, company=self.a, role='admin')
        _assign(ma, self.a.roles.get(slug='administrador'))
        self.user_b = _saas_user('dashcat_b')
        mb = Membership.objects.create(user=self.user_b, company=self.b, role='admin')
        _assign(mb, self.b.roles.get(slug='administrador'))
        self.platform = _saas_user('dashcat_platform', is_superuser=True)

    def _get(self, user, params=''):
        c = APIClient()
        c.force_authenticate(user=user)
        return c.get(f'{self.URL}{params}')

    def test_counts_are_per_company(self):
        self.assertEqual(self._get(self.user_a).data['catalog']['products'], 3)
        self.assertEqual(self._get(self.user_b).data['catalog']['products'], 8)

    def test_master_sees_the_selected_company_only(self):
        self.assertEqual(
            self._get(self.platform, f'?company={self.a.pk}').data['catalog']['products'], 3)
        self.assertEqual(
            self._get(self.platform, f'?company={self.b.pk}').data['catalog']['products'], 8)

    def test_categories_are_counted_per_company(self):
        self.assertEqual(self._get(self.user_a).data['catalog']['categories'], 1)
        self.assertEqual(self._get(self.user_b).data['catalog']['categories'], 0)

    def test_active_products_are_counted_separately(self):
        Product.objects.filter(company=self.a, slug='a-0').update(is_active=False)
        catalog = self._get(self.user_a).data['catalog']
        self.assertEqual(catalog['products'], 3)
        self.assertEqual(catalog['active_products'], 2)

    def test_catalog_is_withheld_without_products_view(self):
        user = _saas_user('dashcat_nocaps')
        m = Membership.objects.create(user=user, company=self.a, role='customer')
        _assign(m, _role(self.a, 'Sin productos', ['company.view'], 'sin-productos'))
        self.assertIsNone(self._get(user).data['catalog'])

    def test_dashboard_payload_shape_is_pinned(self):
        """
        Checks the response STRUCTURE, not substrings: "orders" legitimately
        appears inside capability codes like `sales.orders.view`, and a substring
        scan would have flagged that as a leak.

        Pinning the key set is the point: a new field on this payload has to be a
        deliberate change that somebody reviewed here. 2C added `sales`; 2D added
        `inventory`; 3 added `configuration`.
        """
        data = self._get(self.user_a).data
        self.assertEqual(
            set(data.keys()),
            {'company', 'membership', 'access', 'organization', 'catalog', 'sales',
             'inventory', 'configuration', 'available_companies',
             'requires_company_selection', 'alerts'},
        )
        # Phase 2B.1 added the chart series; the point of pinning the key set is
        # that a new key must be a deliberate change, reviewed here.
        self.assertEqual(
            set(data['catalog'].keys()),
            {'products', 'active_products', 'inactive_products', 'categories',
             'products_per_category'})
        self.assertEqual(
            set(data['organization'].keys()),
            {'active_branches', 'active_memberships', 'active_areas', 'active_roles',
             'assignments_per_area', 'assignments_per_role'})


# ---------------------------------------------------------------------------
# Phase 2B.1 — dashboard chart series
# ---------------------------------------------------------------------------

class Phase2b1DashboardSeriesTest(TestCase):
    """
    The series that feed the dashboard charts are strictly per company.

    A distribution is company information just like a total, so it carries the
    same capability gate — and it must never aggregate across tenants.
    """

    URL = '/api/me/internal-dashboard/'

    def setUp(self):
        cache.clear()
        self.a = _saas_company('Empresa A', 'series-a', tax_id='20999000001')
        self.b = _saas_company('Empresa B', 'series-b', tax_id='20999000002')
        provision_company_access_defaults(self.a)
        provision_company_access_defaults(self.b)

        # A: 2 categories (3 + 1 products) + 1 uncategorised, one of them hidden
        self.cat_a1 = _cat(self.a, 'Teléfonos', 'telefonos')
        self.cat_a2 = _cat(self.a, 'Accesorios', 'accesorios-a')
        for i in range(3):
            _prod(self.a, f'A tel {i}', f'a-tel-{i}', category=self.cat_a1)
        _prod(self.a, 'A acc', 'a-acc', category=self.cat_a2)
        _prod(self.a, 'A suelto', 'a-suelto', is_active=False)

        # B: a much bigger catalogue, to prove it never bleeds into A
        self.cat_b = _cat(self.b, 'Teléfonos', 'telefonos')
        for i in range(9):
            _prod(self.b, f'B tel {i}', f'b-tel-{i}', category=self.cat_b)

        self.admin_a = _saas_user('series_admin_a')
        ma = Membership.objects.create(user=self.admin_a, company=self.a, role='admin')
        _assign(ma, self.a.roles.get(slug='administrador'),
                area=self.a.areas.get(slug='administracion'))
        self.admin_b = _saas_user('series_admin_b')
        mb = Membership.objects.create(user=self.admin_b, company=self.b, role='admin')
        _assign(mb, self.b.roles.get(slug='administrador'))
        self.platform = _saas_user('series_platform', is_superuser=True)

    def _get(self, user, params=''):
        c = APIClient()
        c.force_authenticate(user=user)
        return c.get(f'{self.URL}{params}')

    # --- catalogue series ---

    def test_products_per_category_is_company_scoped(self):
        series = self._get(self.admin_a).data['catalog']['products_per_category']
        by_label = {row['label']: row['value'] for row in series}
        self.assertEqual(by_label['Teléfonos'], 3)
        self.assertEqual(by_label['Accesorios'], 1)
        # B also has a "Teléfonos" with 9 products; it must not be added in
        self.assertNotIn(9, by_label.values())

    def test_uncategorised_products_get_their_own_bucket(self):
        """A chart that silently drops rows misrepresents the total beside it."""
        series = self._get(self.admin_a).data['catalog']['products_per_category']
        by_label = {row['label']: row['value'] for row in series}
        self.assertEqual(by_label['Sin categoría'], 1)
        self.assertEqual(
            sum(by_label.values()),
            self._get(self.admin_a).data['catalog']['products'],
        )

    def test_active_and_inactive_split_matches_the_total(self):
        catalog = self._get(self.admin_a).data['catalog']
        self.assertEqual(catalog['products'], 5)
        self.assertEqual(catalog['active_products'], 4)
        self.assertEqual(catalog['inactive_products'], 1)
        self.assertEqual(
            catalog['active_products'] + catalog['inactive_products'],
            catalog['products'],
        )

    def test_each_company_sees_only_its_own_catalogue_series(self):
        a = self._get(self.admin_a).data['catalog']
        b = self._get(self.admin_b).data['catalog']
        self.assertEqual(a['products'], 5)
        self.assertEqual(b['products'], 9)
        self.assertNotEqual(a['products_per_category'], b['products_per_category'])

    # --- organisation series ---

    def test_assignments_per_area_is_company_scoped(self):
        series = self._get(self.admin_a).data['organization']['assignments_per_area']
        by_label = {row['label']: row['value'] for row in series}
        self.assertEqual(by_label['Administración'], 1)
        # Every other area of A exists but has nobody
        self.assertEqual(by_label['Ventas'], 0)
        self.assertEqual(len(series), self.a.areas.filter(is_active=True).count())

    def test_assignments_per_role_is_company_scoped(self):
        series = self._get(self.admin_a).data['organization']['assignments_per_role']
        by_label = {row['label']: row['value'] for row in series}
        self.assertEqual(by_label['Administrador'], 1)
        self.assertEqual(by_label['Ventas'], 0)

    def test_inactive_membership_is_not_counted_in_the_series(self):
        user = _saas_user('series_inactive')
        m = Membership.objects.create(
            user=user, company=self.a, role='sales', is_active=False)
        _assign(m, self.a.roles.get(slug='ventas'),
                area=self.a.areas.get(slug='ventas'))
        series = self._get(self.admin_a).data['organization']['assignments_per_area']
        by_label = {row['label']: row['value'] for row in series}
        self.assertEqual(by_label['Ventas'], 0)

    def test_inactive_assignment_is_not_counted_in_the_series(self):
        user = _saas_user('series_inactive_assign')
        m = Membership.objects.create(user=user, company=self.a, role='sales')
        _assign(m, self.a.roles.get(slug='ventas'),
                area=self.a.areas.get(slug='ventas'), is_active=False)
        series = self._get(self.admin_a).data['organization']['assignments_per_area']
        by_label = {row['label']: row['value'] for row in series}
        self.assertEqual(by_label['Ventas'], 0)

    # --- capability gating ---

    def test_series_are_withheld_without_the_capability(self):
        user = _saas_user('series_nocaps')
        m = Membership.objects.create(user=user, company=self.a, role='customer')
        _assign(m, _role(self.a, 'Solo empresa', ['company.view'], 'solo-empresa'))
        data = self._get(user).data
        # company.view grants the organisation block...
        self.assertIsNotNone(data['organization'])
        self.assertIn('assignments_per_area', data['organization'])
        # ...but not the catalogue, which needs products.view
        self.assertIsNone(data['catalog'])

    def test_no_series_at_all_without_company_view(self):
        user = _saas_user('series_nothing')
        m = Membership.objects.create(user=user, company=self.a, role='customer')
        _assign(m, _role(self.a, 'Nada de nada', [], 'nada-de-nada'))
        data = self._get(user).data
        self.assertIsNone(data['organization'])
        self.assertIsNone(data['catalog'])

    # --- platform master ---

    def test_master_series_follow_the_selected_company(self):
        a = self._get(self.platform, f'?company={self.a.pk}').data
        b = self._get(self.platform, f'?company={self.b.pk}').data
        self.assertEqual(a['catalog']['products'], 5)
        self.assertEqual(b['catalog']['products'], 9)

    def test_master_series_never_aggregate_across_tenants(self):
        a = self._get(self.platform, f'?company={self.a.pk}').data
        total = sum(row['value'] for row in a['catalog']['products_per_category'])
        self.assertEqual(total, 5)  # not 14

    # --- shape and safety ---

    def test_series_are_bounded(self):
        """A chart is not a data dump: the backend caps how many buckets it returns."""
        for i in range(20):
            _cat(self.a, f'Cat {i}', f'cat-{i}')
        series = self._get(self.admin_a).data['catalog']['products_per_category']
        # 8 category buckets at most, plus the uncategorised one
        self.assertLessEqual(len(series), 9)

    def test_series_expose_no_sales_or_stock_data(self):
        data = self._get(self.admin_a).data
        self.assertEqual(
            set(data['catalog'].keys()),
            {'products', 'active_products', 'inactive_products', 'categories',
             'products_per_category'},
        )
        self.assertEqual(
            set(data['organization'].keys()),
            {'active_branches', 'active_memberships', 'active_areas', 'active_roles',
             'assignments_per_area', 'assignments_per_role'},
        )

    def test_series_rows_carry_only_label_and_value(self):
        """No ids or internal fields leak through a chart payload."""
        data = self._get(self.admin_a).data
        for key in ('products_per_category',):
            for row in data['catalog'][key]:
                self.assertEqual(set(row.keys()), {'label', 'value'})
        for key in ('assignments_per_area', 'assignments_per_role'):
            for row in data['organization'][key]:
                self.assertEqual(set(row.keys()), {'label', 'value'})

    def test_empty_company_returns_empty_series_not_an_error(self):
        empty = _saas_company('Vacía', 'series-vacia', tax_id='20999000003')
        provision_company_access_defaults(empty)
        user = _saas_user('series_empty_admin')
        m = Membership.objects.create(user=user, company=empty, role='admin')
        _assign(m, empty.roles.get(slug='administrador'))
        data = self._get(user).data
        self.assertEqual(data['catalog']['products'], 0)
        self.assertEqual(data['catalog']['products_per_category'], [])
        self.assertEqual(data['status_code'] if 'status_code' in data else 200, 200)


# ---------------------------------------------------------------------------
# Phase 2C — tenant-aware commerce
# ---------------------------------------------------------------------------

from .models import assert_items_match_order  # noqa: E402
from .tenancy import (  # noqa: E402
    storefront_cart_items, storefront_coupon, storefront_orders,
)


def _coupon(company, code='PROMO10', percent=10, **extra):
    return Coupon.objects.create(
        company=company, code=code, discount_percent=percent, **extra,
    )


def _order(company, user=None, total='100.00', paid=False, **extra):
    from django.utils import timezone as dj_tz
    order = Order.objects.create(
        company=company, user=user,
        customer_email=extra.pop('customer_email', 'c@example.com'),
        total=Decimal(total),
        status=Order.Status.PAID if paid else Order.Status.PENDING_PAYMENT,
        paid=paid,
        paid_at=dj_tz.now() if paid else None,
        **extra,
    )
    return order


class Phase2cModelTest(TestCase):
    """Ownership and the order/item invariant."""

    def setUp(self):
        self.a = _saas_company('Empresa A', '2c-a', tax_id='21000000001')
        self.b = _saas_company('Empresa B', '2c-b', tax_id='21000000002')
        self.prod_a = _prod(self.a, 'Producto A', '2c-prod-a')
        self.prod_b = _prod(self.b, 'Producto B', '2c-prod-b')

    def test_backfill_assigned_every_order_and_coupon(self):
        self.assertEqual(Order.objects.filter(company__isnull=True).count(), 0)
        self.assertEqual(Coupon.objects.filter(company__isnull=True).count(), 0)

    def test_same_coupon_code_may_exist_in_two_companies(self):
        _coupon(self.a, 'BIENVENIDO10')
        _coupon(self.b, 'BIENVENIDO10')
        self.assertEqual(Coupon.objects.filter(code='BIENVENIDO10').count(), 2)

    def test_coupon_code_is_unique_within_a_company(self):
        _coupon(self.a, 'UNICO')
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                _coupon(self.a, 'UNICO')

    def test_order_item_rejects_a_product_of_another_company(self):
        order = _order(self.a)
        with self.assertRaises(DjangoValidationError):
            OrderItem.objects.create(
                order=order, product=self.prod_b, quantity=1, price=Decimal('10'))

    def test_order_item_accepts_a_product_of_its_own_company(self):
        order = _order(self.a)
        item = OrderItem.objects.create(
            order=order, product=self.prod_a, quantity=1, price=Decimal('10'))
        self.assertEqual(item.product.company_id, order.company_id)

    def test_bulk_guard_catches_what_clean_cannot(self):
        """bulk_create() bypasses clean(); the set-level guard must not."""
        order = _order(self.a)
        with self.assertRaises(DjangoValidationError):
            assert_items_match_order(order, [self.prod_a, self.prod_b])
        assert_items_match_order(order, [self.prod_a])  # no raise

    def test_company_with_orders_cannot_be_deleted(self):
        _order(self.a)
        with self.assertRaises(Exception):
            with transaction.atomic():
                self.a.delete()

    def test_a_new_company_starts_with_no_orders_or_coupons(self):
        fresh = _saas_company('Nueva', '2c-nueva', tax_id='21000000003')
        self.assertEqual(fresh.orders.count(), 0)
        self.assertEqual(fresh.coupons.count(), 0)


class Phase2cCartIsolationTest(TestCase):
    """One browser, several storefronts, several logical carts."""

    SESSION = 'shared-browser-session'

    def setUp(self):
        cache.clear()
        self.a = _saas_company('Empresa A', '2c-cart-a', tax_id='21000000004')
        self.b = _saas_company('Empresa B', '2c-cart-b', tax_id='21000000005')
        self.prod_a = _prod(self.a, 'Producto A', '2c-cart-prod-a', inventory=20)
        self.prod_b = _prod(self.b, 'Producto B', '2c-cart-prod-b', inventory=20)
        self.client = APIClient()

    def _add(self, product, quantity=1):
        return self.client.post('/api/cart/add/', {
            'session_key': self.SESSION, 'product': product.pk, 'quantity': quantity,
        }, format='json')

    def test_storefront_a_accepts_its_own_product(self):
        with _storefront_of(self.a):
            self.assertEqual(self._add(self.prod_a).status_code, status.HTTP_200_OK)

    def test_storefront_a_rejects_a_product_of_b(self):
        with _storefront_of(self.a):
            res = self._add(self.prod_b)
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(CartItem.objects.count(), 0)

    def test_two_carts_coexist_under_one_session_key(self):
        with _storefront_of(self.a):
            self._add(self.prod_a)
        with _storefront_of(self.b):
            self._add(self.prod_b)
        self.assertEqual(CartItem.objects.filter(session_key=self.SESSION).count(), 2)

    def test_list_shows_only_the_current_storefront(self):
        with _storefront_of(self.a):
            self._add(self.prod_a)
        with _storefront_of(self.b):
            self._add(self.prod_b)

        with _storefront_of(self.a):
            data = self.client.get(f'/api/cart/?session_key={self.SESSION}').json()
        ids = {row['product']['id'] if isinstance(row['product'], dict) else row['product']
               for row in data}
        self.assertIn(self.prod_a.pk, ids)
        self.assertNotIn(self.prod_b.pk, ids)

    def test_update_cannot_touch_another_storefronts_item(self):
        with _storefront_of(self.b):
            self._add(self.prod_b)
        item_b = CartItem.objects.get(product=self.prod_b)

        with _storefront_of(self.a):
            res = self.client.patch(
                f'/api/cart/{item_b.pk}/?session_key={self.SESSION}',
                {'quantity': 9}, format='json')
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
        item_b.refresh_from_db()
        self.assertEqual(item_b.quantity, 1)

    def test_delete_cannot_touch_another_storefronts_item(self):
        with _storefront_of(self.b):
            self._add(self.prod_b)
        item_b = CartItem.objects.get(product=self.prod_b)

        with _storefront_of(self.a):
            res = self.client.delete(
                f'/api/cart/{item_b.pk}/?session_key={self.SESSION}')
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
        self.assertTrue(CartItem.objects.filter(pk=item_b.pk).exists())

    def test_unresolved_storefront_shows_an_empty_cart(self):
        with _storefront_of(self.a):
            self._add(self.prod_a)
        with override_settings(DEFAULT_STOREFRONT_COMPANY_SLUG=''):
            self.assertEqual(
                self.client.get(f'/api/cart/?session_key={self.SESSION}').json(), [])


class Phase2cCouponIsolationTest(TestCase):
    """A coupon belongs to one storefront, however common the code."""

    def setUp(self):
        cache.clear()
        self.a = _saas_company('Empresa A', '2c-cup-a', tax_id='21000000006')
        self.b = _saas_company('Empresa B', '2c-cup-b', tax_id='21000000007')
        self.coupon_b = _coupon(self.b, 'BIENVENIDO10', 10)
        self.client = APIClient()

    def test_storefront_a_cannot_validate_a_coupon_of_b(self):
        with _storefront_of(self.a):
            res = self.client.post('/api/coupons/validate/',
                                   {'code': 'BIENVENIDO10'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_storefront_b_validates_its_own_coupon(self):
        with _storefront_of(self.b):
            res = self.client.post('/api/coupons/validate/',
                                   {'code': 'BIENVENIDO10'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)

    def test_helper_never_crosses_tenants(self):
        from django.test import RequestFactory
        request = RequestFactory().post('/')
        with _storefront_of(self.a):
            self.assertIsNone(storefront_coupon(request, 'BIENVENIDO10'))
        with _storefront_of(self.b):
            self.assertEqual(storefront_coupon(request, 'BIENVENIDO10'), self.coupon_b)


class Phase2cCheckoutTest(TestCase):
    """Checkout derives its tenant from the storefront and nothing else."""

    SESSION = '2c-checkout-session'

    def setUp(self):
        cache.clear()
        self.a = _saas_company('Empresa A', '2c-chk-a', tax_id='21000000008')
        self.b = _saas_company('Empresa B', '2c-chk-b', tax_id='21000000009')
        self.prod_a = _prod(self.a, 'Producto A', '2c-chk-prod-a', inventory=10, price='100.00')
        self.prod_b = _prod(self.b, 'Producto B', '2c-chk-prod-b', inventory=10, price='100.00')
        self.client = APIClient()

    def _payload(self, **extra):
        base = {
            'session_key': self.SESSION,
            'customer_name': 'Cliente',
            'customer_email': 'c@example.com',
            'customer_phone': '999999999',
            'document_type': 'dni',
            'document_number': '12345678',
            'delivery_method': 'pickup_store',
            'receipt_type': 'boleta',
            'accepted_terms': True,
            'accepted_warranty_policy': True,
        }
        base.update(extra)
        return base

    def _checkout(self, session_id='cs_2c'):
        with patch('stripe.checkout.Session.create') as mock_create:
            mock_create.return_value = MagicMock(id=session_id, url='https://stripe.test/x')
            return self.client.post('/api/payments/create-checkout-session/',
                                    self._payload(), format='json'), mock_create

    def test_order_belongs_to_the_storefront_company(self):
        CartItem.objects.create(session_key=self.SESSION, product=self.prod_a, quantity=1)
        with _storefront_of(self.a):
            res, _ = self._checkout()
        self.assertIn(res.status_code, (status.HTTP_200_OK, status.HTTP_201_CREATED))
        order = Order.objects.get(stripe_session_id='cs_2c')
        self.assertEqual(order.company_id, self.a.pk)
        self.assertEqual(order.items.first().product.company_id, self.a.pk)

    def test_company_in_the_payload_is_ignored(self):
        CartItem.objects.create(session_key=self.SESSION, product=self.prod_a, quantity=1)
        with _storefront_of(self.a):
            with patch('stripe.checkout.Session.create') as mock_create:
                mock_create.return_value = MagicMock(id='cs_2c_payload', url='u')
                self.client.post('/api/payments/create-checkout-session/',
                                 self._payload(company=self.b.pk), format='json')
        self.assertEqual(
            Order.objects.get(stripe_session_id='cs_2c_payload').company_id, self.a.pk)

    def test_a_cart_of_another_tenant_is_invisible_to_this_checkout(self):
        """The cross-tenant cart cannot even reach the order-creation step."""
        CartItem.objects.create(session_key=self.SESSION, product=self.prod_b, quantity=1)
        with _storefront_of(self.a):
            res, _ = self._checkout()
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Order.objects.filter(stripe_session_id='cs_2c').count(), 0)

    def test_coupon_of_another_tenant_is_rejected(self):
        _coupon(self.b, 'SOLO_B', 50)
        CartItem.objects.create(session_key=self.SESSION, product=self.prod_a, quantity=1)
        with _storefront_of(self.a):
            with patch('stripe.checkout.Session.create') as mock_create:
                mock_create.return_value = MagicMock(id='cs_2c_cup', url='u')
                res = self.client.post('/api/payments/create-checkout-session/',
                                       self._payload(coupon_code='SOLO_B'), format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_own_coupon_is_applied(self):
        _coupon(self.a, 'MITAD', 50)
        CartItem.objects.create(session_key=self.SESSION, product=self.prod_a, quantity=1)
        with _storefront_of(self.a):
            with patch('stripe.checkout.Session.create') as mock_create:
                mock_create.return_value = MagicMock(id='cs_2c_own', url='u')
                self.client.post('/api/payments/create-checkout-session/',
                                 self._payload(coupon_code='MITAD'), format='json')
        order = Order.objects.get(stripe_session_id='cs_2c_own')
        self.assertEqual(order.total, Decimal('50.00'))

    def test_stripe_metadata_carries_the_company(self):
        CartItem.objects.create(session_key=self.SESSION, product=self.prod_a, quantity=1)
        with _storefront_of(self.a):
            _res, mock_create = self._checkout('cs_2c_meta')
        metadata = mock_create.call_args.kwargs['metadata']
        self.assertEqual(metadata['company_id'], str(self.a.pk))


class Phase2cWebhookTest(TestCase):
    """The webhook resolves its tenant from the database, never from the request."""

    SESSION = '2c-webhook-session'

    def setUp(self):
        cache.clear()
        self.a = _saas_company('Empresa A', '2c-wh-a', tax_id='21000000010')
        self.b = _saas_company('Empresa B', '2c-wh-b', tax_id='21000000011')
        self.prod_a = _prod(self.a, 'Producto A', '2c-wh-prod-a', inventory=10)
        self.prod_b = _prod(self.b, 'Producto B', '2c-wh-prod-b', inventory=10)

        self.order = _order(self.a, stripe_session_id='cs_wh_2c')
        OrderItem.objects.create(
            order=self.order, product=self.prod_a, quantity=2, price=self.prod_a.price)
        self.order.cart_session_key = self.SESSION
        self.order.save(update_fields=['cart_session_key'])

        # Two carts under one session key, one per storefront
        CartItem.objects.create(session_key=self.SESSION, product=self.prod_a, quantity=2)
        CartItem.objects.create(session_key=self.SESSION, product=self.prod_b, quantity=1)
        self.client = APIClient()

    def _fire(self, session_id='cs_wh_2c', metadata=None):
        event = {
            'type': 'checkout.session.completed',
            'data': {'object': {
                'id': session_id, 'payment_intent': 'pi_2c',
                **({'metadata': metadata} if metadata is not None else {}),
            }},
        }
        with patch('stripe.Webhook.construct_event', return_value=event):
            return self.client.post('/api/payments/webhook/', data=b'{}',
                                    content_type='application/json',
                                    HTTP_STRIPE_SIGNATURE='t=1,v1=fake')

    def test_payment_is_confirmed_for_the_orders_own_company(self):
        self._fire()
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PAID)
        movement = StockMovement.objects.get(order=self.order)
        self.assertEqual(movement.product.company_id, self.a.pk)

    def test_cart_cleanup_only_empties_the_paid_storefront(self):
        """Paying at one storefront must not empty the browser's other cart."""
        self._fire()
        remaining = CartItem.objects.filter(session_key=self.SESSION)
        self.assertEqual(remaining.count(), 1)
        self.assertEqual(remaining.first().product_id, self.prod_b.pk)

    def test_replayed_webhook_stays_idempotent(self):
        self._fire()
        self._fire()
        self._fire()
        self.prod_a.refresh_from_db()
        self.assertEqual(self.prod_a.inventory, 8)
        self.assertEqual(
            StockMovement.objects.filter(
                order=self.order, movement_type=StockMovement.SALE_EXIT).count(),
            1,
        )

    def test_metadata_company_mismatch_is_refused(self):
        """Metadata came back from a third party; it is checked, never trusted."""
        res = self._fire(metadata={'company_id': str(self.b.pk)})
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.order.refresh_from_db()
        self.assertNotEqual(self.order.status, Order.Status.PAID)
        self.assertEqual(StockMovement.objects.filter(order=self.order).count(), 0)

    def test_matching_metadata_is_accepted(self):
        self._fire(metadata={'company_id': str(self.a.pk)})
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PAID)

    def test_webhook_host_never_decides_the_tenant(self):
        """Stripe calls one endpoint; the host says nothing about the seller."""
        with _storefront_of(self.b):
            self._fire()
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PAID)
        self.assertEqual(self.order.company_id, self.a.pk)


class Phase2cCustomerOrderIsolationTest(TestCase):
    """One identity, several storefronts, separate histories."""

    def setUp(self):
        cache.clear()
        self.a = _saas_company('Empresa A', '2c-hist-a', tax_id='21000000012')
        self.b = _saas_company('Empresa B', '2c-hist-b', tax_id='21000000013')
        self.user = _saas_user('2c_shopper')
        self.order_a = _order(self.a, user=self.user, total='100.00', paid=True)
        self.order_b = _order(self.b, user=self.user, total='200.00', paid=True)
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_history_shows_only_the_current_storefront(self):
        with _storefront_of(self.a):
            ids = [o['id'] for o in self.client.get('/api/orders/').json()]
        self.assertIn(self.order_a.pk, ids)
        self.assertNotIn(self.order_b.pk, ids)

    def test_the_other_storefront_shows_the_other_history(self):
        with _storefront_of(self.b):
            ids = [o['id'] for o in self.client.get('/api/orders/').json()]
        self.assertIn(self.order_b.pk, ids)
        self.assertNotIn(self.order_a.pk, ids)

    def test_knowing_the_id_does_not_expose_a_foreign_order(self):
        with _storefront_of(self.a):
            res = self.client.get(f'/api/orders/{self.order_b.pk}/')
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_another_users_order_is_never_visible(self):
        other = _saas_user('2c_other_shopper')
        foreign = _order(self.a, user=other, paid=True)
        with _storefront_of(self.a):
            ids = [o['id'] for o in self.client.get('/api/orders/').json()]
        self.assertNotIn(foreign.pk, ids)

    def test_same_user_is_one_identity_not_two(self):
        self.assertEqual(Order.objects.filter(user=self.user).count(), 2)
        from django.test import RequestFactory
        request = RequestFactory().get('/')
        with _storefront_of(self.a):
            self.assertEqual(storefront_orders(request, self.user).count(), 1)


class Phase2cAdminOrderIsolationTest(TestCase):
    """Internal order administration is tenant-scoped and capability-driven."""

    def setUp(self):
        cache.clear()
        self.a = _saas_company('Empresa A', '2c-adm-a', tax_id='21000000014')
        self.b = _saas_company('Empresa B', '2c-adm-b', tax_id='21000000015')
        provision_company_access_defaults(self.a)
        provision_company_access_defaults(self.b)

        self.prod_a = _prod(self.a, 'Producto A', '2c-adm-prod-a')
        self.order_a = _order(self.a, total='150.00', paid=True)
        OrderItem.objects.create(
            order=self.order_a, product=self.prod_a, quantity=1, price=Decimal('150'))
        self.order_b = _order(self.b, total='999.00', paid=True)

        self.admin_a = _saas_user('2c_admin_a')
        ma = Membership.objects.create(user=self.admin_a, company=self.a, role='admin')
        _assign(ma, self.a.roles.get(slug='administrador'))

        self.viewer_a = _saas_user('2c_viewer_a')
        mv = Membership.objects.create(user=self.viewer_a, company=self.a, role='customer')
        _assign(mv, _role(self.a, 'Solo ver pedidos',
                          ['company.view', 'sales.orders.view'], '2c-solo-ver'))

        self.admin_b = _saas_user('2c_admin_b')
        mb = Membership.objects.create(user=self.admin_b, company=self.b, role='admin')
        _assign(mb, self.b.roles.get(slug='administrador'))

        self.platform = _saas_user('2c_platform', is_superuser=True)

    def _as(self, user):
        c = APIClient()
        c.force_authenticate(user=user)
        return c

    def test_admin_of_a_lists_only_its_own_orders(self):
        res = self._as(self.admin_a).get('/api/admin/orders/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        ids = {o['id'] for o in res.data['results']}
        self.assertIn(self.order_a.pk, ids)
        self.assertNotIn(self.order_b.pk, ids)

    def test_foreign_order_detail_answers_like_a_missing_one(self):
        foreign = self._as(self.admin_a).get(f'/api/admin/orders/{self.order_b.pk}/')
        missing = self._as(self.admin_a).get('/api/admin/orders/999999/')
        self.assertEqual(foreign.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(foreign.status_code, missing.status_code)

    def test_admin_of_a_cannot_change_fulfillment_of_b(self):
        res = self._as(self.admin_a).patch(
            f'/api/admin/orders/{self.order_b.pk}/fulfillment-status/',
            {'fulfillment_status': 'shipped'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
        self.order_b.refresh_from_db()
        self.assertEqual(self.order_b.fulfillment_status, Order.FulfillmentStatus.PENDING)

    def test_admin_of_a_cannot_download_the_receipt_of_b(self):
        res = self._as(self.admin_a).get(
            f'/api/admin/orders/{self.order_b.pk}/receipt-pdf/')
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_admin_of_a_cannot_issue_a_sales_note_for_b(self):
        res = self._as(self.admin_a).post(
            f'/api/admin/orders/{self.order_b.pk}/sales-note/')
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(SalesNote.objects.count(), 0)

    def test_orders_view_alone_cannot_change_fulfillment(self):
        res = self._as(self.viewer_a).patch(
            f'/api/admin/orders/{self.order_a.pk}/fulfillment-status/',
            {'fulfillment_status': 'shipped'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_orders_view_can_read(self):
        self.assertEqual(
            self._as(self.viewer_a).get('/api/admin/orders/').status_code,
            status.HTTP_200_OK)

    def test_member_without_sales_capabilities_is_refused(self):
        user = _saas_user('2c_no_sales')
        m = Membership.objects.create(user=user, company=self.a, role='customer')
        _assign(m, _role(self.a, 'Sin ventas', ['company.view'], '2c-sin-ventas'))
        self.assertEqual(
            self._as(user).get('/api/admin/orders/').status_code,
            status.HTTP_403_FORBIDDEN)

    def test_master_selects_a_company_explicitly(self):
        res_a = self._as(self.platform).get(f'/api/admin/orders/?company={self.a.pk}')
        res_b = self._as(self.platform).get(f'/api/admin/orders/?company={self.b.pk}')
        self.assertEqual({o['id'] for o in res_a.data['results']}, {self.order_a.pk})
        self.assertEqual({o['id'] for o in res_b.data['results']}, {self.order_b.pk})

    def test_master_without_selection_gets_nothing(self):
        self.assertEqual(
            self._as(self.platform).get('/api/admin/orders/').status_code,
            status.HTTP_403_FORBIDDEN)

    def test_multi_company_user_does_not_inherit_permissions(self):
        """Manage in A, read-only in B."""
        user = _saas_user('2c_multi')
        ma = Membership.objects.create(user=user, company=self.a, role='admin')
        _assign(ma, self.a.roles.get(slug='administrador'))
        mb = Membership.objects.create(user=user, company=self.b, role='customer')
        _assign(mb, _role(self.b, 'Ver pedidos B',
                          ['company.view', 'sales.orders.view'], '2c-ver-b'))

        client = self._as(user)
        self.assertEqual(
            client.patch(
                f'/api/admin/orders/{self.order_a.pk}/fulfillment-status/?company={self.a.pk}',
                {'fulfillment_status': 'confirmed'}, format='json').status_code,
            status.HTTP_200_OK)
        self.assertEqual(
            client.get(f'/api/admin/orders/?company={self.b.pk}').status_code,
            status.HTTP_200_OK)
        self.assertEqual(
            client.patch(
                f'/api/admin/orders/{self.order_b.pk}/fulfillment-status/?company={self.b.pk}',
                {'fulfillment_status': 'shipped'}, format='json').status_code,
            status.HTTP_403_FORBIDDEN)

    def test_fulfillment_change_is_audited_with_company(self):
        self._as(self.admin_a).patch(
            f'/api/admin/orders/{self.order_a.pk}/fulfillment-status/',
            {'fulfillment_status': 'confirmed'}, format='json')
        log = AdminAuditLog.objects.filter(
            action='order_fulfillment_status_changed').first()
        self.assertIsNotNone(log)
        self.assertEqual(log.company_id, self.a.pk)
        raw = str(log.metadata).lower()
        for secret in ('stripe', 'cs_', 'pi_', 'token'):
            self.assertNotIn(secret, raw)


class Phase2cDashboardSalesTest(TestCase):
    """Commercial KPIs are real, per tenant, and honest about what they omit."""

    URL = '/api/me/internal-dashboard/'

    def setUp(self):
        cache.clear()
        self.a = _saas_company('Empresa A', '2c-kpi-a', tax_id='21000000016')
        self.b = _saas_company('Empresa B', '2c-kpi-b', tax_id='21000000017')
        provision_company_access_defaults(self.a)
        provision_company_access_defaults(self.b)

        _order(self.a, total='100.00', paid=True)
        _order(self.a, total='300.00', paid=True)
        _order(self.a, total='999.00', paid=False)          # pending: not a sale
        cancelled = _order(self.a, total='500.00', paid=False)
        cancelled.status = Order.Status.CANCELLED
        cancelled.save(update_fields=['status'])
        _order(self.b, total='7000.00', paid=True)

        self.user_a = _saas_user('2c_kpi_a')
        ma = Membership.objects.create(user=self.user_a, company=self.a, role='admin')
        _assign(ma, self.a.roles.get(slug='administrador'))
        self.user_b = _saas_user('2c_kpi_b')
        mb = Membership.objects.create(user=self.user_b, company=self.b, role='admin')
        _assign(mb, self.b.roles.get(slug='administrador'))
        self.platform = _saas_user('2c_kpi_platform', is_superuser=True)

    def _get(self, user, params=''):
        c = APIClient()
        c.force_authenticate(user=user)
        return c.get(f'{self.URL}{params}')

    def test_only_paid_orders_count_as_revenue(self):
        sales = self._get(self.user_a).data['sales']
        self.assertEqual(sales['total_revenue'], '400.00')
        self.assertEqual(sales['total_paid_orders'], 2)
        self.assertEqual(sales['pending_payment'], 1)

    def test_average_ticket_is_computed_over_paid_orders(self):
        self.assertEqual(self._get(self.user_a).data['sales']['average_ticket'], '200.00')

    def test_average_ticket_of_nothing_is_zero_not_an_error(self):
        empty = _saas_company('Vacía', '2c-kpi-vacia', tax_id='21000000018')
        provision_company_access_defaults(empty)
        user = _saas_user('2c_kpi_empty')
        m = Membership.objects.create(user=user, company=empty, role='admin')
        _assign(m, empty.roles.get(slug='administrador'))
        sales = self._get(user).data['sales']
        self.assertEqual(sales['average_ticket'], '0.00')
        self.assertEqual(sales['total_revenue'], '0.00')

    def test_revenue_never_crosses_tenants(self):
        self.assertEqual(self._get(self.user_a).data['sales']['total_revenue'], '400.00')
        self.assertEqual(self._get(self.user_b).data['sales']['total_revenue'], '7000.00')

    def test_master_sees_the_selected_company_only(self):
        self.assertEqual(
            self._get(self.platform, f'?company={self.a.pk}').data['sales']['total_revenue'],
            '400.00')
        self.assertEqual(
            self._get(self.platform, f'?company={self.b.pk}').data['sales']['total_revenue'],
            '7000.00')

    def test_todays_revenue_uses_paid_at(self):
        sales = self._get(self.user_a).data['sales']
        # Both paid orders were paid now, so today's revenue equals the total
        self.assertEqual(sales['today_revenue'], '400.00')
        self.assertEqual(sales['today_orders'], 2)

    def test_revenue_trend_has_one_point_per_day_including_empty_ones(self):
        trend = self._get(self.user_a).data['sales']['revenue_trend']
        self.assertEqual(len(trend), 7)
        self.assertEqual(trend[-1]['value'], 400.0)
        self.assertTrue(all(isinstance(p['value'], (int, float)) for p in trend))

    def test_orders_by_status_covers_every_status(self):
        series = self._get(self.user_a).data['sales']['orders_by_status']
        self.assertEqual(len(series), len(Order.Status.choices))
        by_label = {row['label']: row['value'] for row in series}
        self.assertEqual(by_label['Pagado'], 2)
        self.assertEqual(by_label['Cancelado'], 1)

    def test_sales_are_withheld_without_the_capability(self):
        user = _saas_user('2c_kpi_nocaps')
        m = Membership.objects.create(user=user, company=self.a, role='customer')
        _assign(m, _role(self.a, 'Sin ventas KPI', ['company.view'], '2c-kpi-sin'))
        self.assertIsNone(self._get(user).data['sales'])

    def test_no_profit_figure_is_reported(self):
        """There is no cost model, so a margin would be an invented number."""
        sales = self._get(self.user_a).data['sales']
        self.assertEqual(
            set(sales.keys()),
            {'today_revenue', 'today_orders', 'total_revenue', 'total_paid_orders',
             'average_ticket', 'pending_payment', 'awaiting_fulfillment',
             'revenue_trend', 'orders_by_status'},
        )
        for forbidden in ('profit', 'margin', 'utilidad', 'cost'):
            self.assertNotIn(forbidden, str(sales).lower())


# ---------------------------------------------------------------------------
# SaaS Phase 2D — multi-branch inventory
# ---------------------------------------------------------------------------
#
# WHAT THESE TESTS ARE ACTUALLY DEFENDING
#
# Two independent axes of authority, and the ways they can be confused:
#
#   capability   what you may DO        inventory.view / adjust / reports
#   branch       where you may do it    Membership.branch_access_mode + grants
#
# The bugs worth catching are the ones that look right in a code review: an
# aggregate that quietly sums a branch the caller was never granted, a foreign
# branch id accepted because it happened to exist, a dispatch that runs twice, a
# count approval computed against an hour-old photograph of the stock.

from .inventory_services import (  # noqa: E402
    InventoryCountError,
    TransferError,
    approve_inventory_count,
    branch_quantity,
    cancel_inventory_count,
    cancel_transfer,
    create_inventory_count,
    create_stock_transfer,
    dispatch_transfer,
    get_replenishment_rows,
    product_inventory_drift,
    receive_transfer,
    recalculate_product_inventory,
    set_count_item,
    set_transfer_item,
)
from .models import (  # noqa: E402
    BranchStock,
    InventoryCount as InventoryCountModel,
    MembershipBranchAccess,
    StockTransfer,
)
from .tenancy import (  # noqa: E402
    BranchAccessError,
    NoBranchError,
    company_fulfillment_branch,
    has_branch_access,
    resolve_branch_for_user,
    visible_branches,
)


def _p2d_company(slug, name=None):
    """A tenant with its access defaults, exactly as the API would create one."""
    from .company_provisioning import provision_company_access_defaults

    company = Company.objects.create(
        name=name or f'Empresa {slug}', slug=slug, tax_id='20000000009',
    )
    provision_company_access_defaults(company)
    return company


def _p2d_branch(company, name):
    return Branch.objects.create(company=company, name=name, is_active=True)


def _p2d_member(company, username, capabilities, *, mode=None, branches=None):
    """
    A staff user of `company` with `capabilities` and an explicit branch scope.

    `branches=None` leaves them in ALL mode; a list puts them in SELECTED mode
    with exactly those grants — which is the state most of these tests are about.
    """
    from .models import Membership

    user = User.objects.create_user(username=username, password='Pass123!')
    membership = Membership.objects.create(
        user=user, company=company, role=UserProfile.ROLE_CUSTOMER,
        branch_access_mode=mode or (
            Membership.ACCESS_MODE_SELECTED if branches is not None
            else Membership.ACCESS_MODE_ALL
        ),
    )
    _assign(membership, _role(
        company, f'Rol {username}', sorted(capabilities), slug=f'rol-{username}',
    ))
    for branch in branches or []:
        MembershipBranchAccess.objects.create(membership=membership, branch=branch)
    return user, membership


def _p2d_product(company, name, slug, price='100.00'):
    return Product.objects.create(
        company=company, name=name, slug=slug, price=Decimal(price), inventory=0,
    )


def _p2d_stock(branch, product, quantity, *, minimum=0, target=0):
    """Put units on a shelf through the service layer, so the Kardex exists."""
    from .inventory_services import create_stock_movement

    if quantity:
        create_stock_movement(
            branch=branch, product_id=product.pk,
            movement_type=StockMovement.INITIAL_STOCK,
            quantity=quantity, reason='Stock de prueba',
        )
    # Quantity zero creates no movement, so the row may not exist yet — absence
    # and zero mean the same thing, which is exactly why the service layer
    # creates rows on demand.
    from .inventory_services import get_or_create_branch_stock

    row = get_or_create_branch_stock(branch, product)
    if minimum or target:
        row.minimum_stock = minimum
        row.target_stock = target
        row.save(update_fields=['minimum_stock', 'target_stock'])
    return row


_INV_ALL = ['company.view', 'inventory.view', 'inventory.adjust', 'inventory.reports']


class Phase2dBranchAccessModelTest(TestCase):
    """Membership.branch_access_mode + MembershipBranchAccess resolution."""

    def setUp(self):
        cache.clear()
        self.company = _p2d_company('p2d-access')
        self.b1 = _p2d_branch(self.company, 'Centro')
        self.b2 = _p2d_branch(self.company, 'Cayma')
        self.b3 = _p2d_branch(self.company, 'Norte')

    def test_mode_all_sees_every_active_branch(self):
        user, _m = _p2d_member(self.company, 'p2d_all', _INV_ALL)
        names = set(visible_branches(user, self.company).values_list('name', flat=True))
        self.assertIn('Centro', names)
        self.assertIn('Cayma', names)
        self.assertIn('Norte', names)

    def test_mode_selected_sees_only_its_grants(self):
        user, _m = _p2d_member(
            self.company, 'p2d_sel', _INV_ALL, branches=[self.b1, self.b2],
        )
        names = set(visible_branches(user, self.company).values_list('name', flat=True))
        self.assertEqual(names, {'Centro', 'Cayma'})
        self.assertNotIn('Norte', names)

    def test_selected_with_zero_grants_sees_nothing(self):
        """
        A real, expressible state — and it DENIES.

        The rejected design was "no rows means all branches", which fails open:
        revoking somebody's last branch would have promoted them to every branch.
        """
        user, _m = _p2d_member(self.company, 'p2d_none', _INV_ALL, branches=[])
        self.assertFalse(visible_branches(user, self.company).exists())
        self.assertFalse(has_branch_access(user, self.b1))

    def test_mode_all_picks_up_a_branch_created_later(self):
        """That automatic inclusion is the entire reason ALL exists."""
        user, _m = _p2d_member(self.company, 'p2d_all_future', _INV_ALL)
        later = _p2d_branch(self.company, 'Abierta después')
        self.assertTrue(has_branch_access(user, later))

    def test_mode_selected_does_NOT_pick_up_a_branch_created_later(self):
        """
        The counterpart, and the more important half: a restricted membership
        must not silently widen when the company opens a shop. Somebody has to
        grant it.
        """
        user, _m = _p2d_member(
            self.company, 'p2d_sel_future', _INV_ALL, branches=[self.b1],
        )
        later = _p2d_branch(self.company, 'Abierta después 2')
        self.assertFalse(has_branch_access(user, later))
        self.assertEqual(
            set(visible_branches(user, self.company).values_list('name', flat=True)),
            {'Centro'},
        )

    def test_an_inactive_branch_is_not_operational(self):
        user, _m = _p2d_member(self.company, 'p2d_inactive', _INV_ALL)
        self.b3.is_active = False
        self.b3.save(update_fields=['is_active'])
        self.assertFalse(has_branch_access(user, self.b3))

    def test_an_inactive_company_grants_no_branches(self):
        user, _m = _p2d_member(self.company, 'p2d_dead_co', _INV_ALL)
        self.company.is_active = False
        self.company.save(update_fields=['is_active'])
        self.assertFalse(visible_branches(user, self.company).exists())

    def test_a_deactivated_grant_stops_counting(self):
        user, m = _p2d_member(
            self.company, 'p2d_revoked', _INV_ALL, branches=[self.b1, self.b2],
        )
        MembershipBranchAccess.objects.filter(membership=m, branch=self.b2).update(
            is_active=False,
        )
        self.assertFalse(has_branch_access(user, self.b2))
        self.assertTrue(has_branch_access(user, self.b1))

    def test_grants_cannot_cross_companies(self):
        from django.core.exceptions import ValidationError

        other = _p2d_company('p2d-other-access')
        foreign = _p2d_branch(other, 'Ajena')
        _user, m = _p2d_member(self.company, 'p2d_cross', _INV_ALL, branches=[])
        with self.assertRaises(ValidationError):
            MembershipBranchAccess.objects.create(membership=m, branch=foreign)

    def test_platform_master_reaches_the_selected_companys_branches(self):
        master = User.objects.create_user(username='p2d_master', password='Pass123!')
        master.is_superuser = True
        master.save()
        self.assertTrue(has_branch_access(master, self.b1))
        # And never mixes tenants: another company's branch is reached only by
        # asking about THAT company.
        other = _p2d_company('p2d-master-other')
        other_branch = _p2d_branch(other, 'De otra empresa')
        self.assertNotIn(
            other_branch.pk,
            list(visible_branches(master, self.company).values_list('pk', flat=True)),
        )


class Phase2dBranchResolutionTest(TestCase):
    """resolve_branch_for_user: an untrusted id can select, never widen."""

    def setUp(self):
        cache.clear()
        self.company = _p2d_company('p2d-resolve')
        self.b1 = _p2d_branch(self.company, 'Uno')
        self.b2 = _p2d_branch(self.company, 'Dos')
        self.b3 = _p2d_branch(self.company, 'Tres')
        self.user, self.m = _p2d_member(
            self.company, 'p2d_resolver', _INV_ALL, branches=[self.b1, self.b2],
        )

    def test_a_granted_id_resolves(self):
        self.assertEqual(
            resolve_branch_for_user(self.user, self.company, self.b2.pk).pk, self.b2.pk,
        )

    def test_an_ungranted_id_is_refused(self):
        with self.assertRaises(BranchAccessError):
            resolve_branch_for_user(self.user, self.company, self.b3.pk)

    def test_a_foreign_id_is_refused_exactly_like_a_missing_one(self):
        other = _p2d_company('p2d-resolve-other')
        foreign = _p2d_branch(other, 'Ajena')
        with self.assertRaises(BranchAccessError):
            resolve_branch_for_user(self.user, self.company, foreign.pk)
        with self.assertRaises(BranchAccessError):
            resolve_branch_for_user(self.user, self.company, 999999)

    def test_no_branch_falls_back_to_the_default(self):
        self.m.branch = self.b2
        self.m.save(update_fields=['branch'])
        self.assertEqual(resolve_branch_for_user(self.user, self.company).pk, self.b2.pk)

    def test_a_revoked_default_degrades_instead_of_granting(self):
        """A stale pointer must not become access it no longer carries."""
        self.m.branch = self.b3  # never granted
        self.m.save(update_fields=['branch'])
        resolved = resolve_branch_for_user(self.user, self.company)
        self.assertIn(resolved.pk, {self.b1.pk, self.b2.pk})

    def test_all_is_refused_on_a_write_path(self):
        """There is no such place as "all branches" to put units into."""
        with self.assertRaises(BranchAccessError):
            resolve_branch_for_user(self.user, self.company, 'all', allow_all=False)
        self.assertIsNone(
            resolve_branch_for_user(self.user, self.company, 'all', allow_all=True),
        )

    def test_a_member_with_no_branches_raises(self):
        user, _m = _p2d_member(self.company, 'p2d_nobranch', _INV_ALL, branches=[])
        with self.assertRaises(NoBranchError):
            resolve_branch_for_user(user, self.company)


class Phase2dCrossTenantIsolationTest(TestCase):
    """
    Company A must never read, move, transfer or count company B's stock.

    Every foreign id in here is VALID — it exists — which is the only version of
    this test worth writing: rejecting a nonexistent id proves nothing.
    """

    def setUp(self):
        cache.clear()
        self.a = _p2d_company('p2d-iso-a', 'Empresa A')
        self.b = _p2d_company('p2d-iso-b', 'Empresa B')
        self.a1 = _p2d_branch(self.a, 'A1')
        self.a2 = _p2d_branch(self.a, 'A2')
        self.b1 = _p2d_branch(self.b, 'B1')

        self.pa = _p2d_product(self.a, 'Producto A', 'producto-a-2d')
        self.pb = _p2d_product(self.b, 'Producto B', 'producto-b-2d')
        _p2d_stock(self.a1, self.pa, 10)
        _p2d_stock(self.b1, self.pb, 99)

        self.user_a, _m = _p2d_member(self.a, 'p2d_iso_a', _INV_ALL)
        self.client = APIClient()
        self.client.force_authenticate(user=self.user_a)

    def test_a_never_sees_bs_stock(self):
        res = self.client.get('/api/admin/inventory/stock/?branch=all')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        names = {r['product_name'] for r in res.data['results']}
        self.assertIn('Producto A', names)
        self.assertNotIn('Producto B', names)

    def test_a_never_sees_bs_units_in_the_summary(self):
        res = self.client.get('/api/admin/inventory/summary/?branch=all')
        self.assertEqual(res.data['total_units'], 10)

    def test_a_cannot_read_bs_branch(self):
        res = self.client.get(f'/api/admin/inventory/stock/?branch={self.b1.pk}')
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_a_cannot_move_stock_in_bs_branch(self):
        res = self.client.post('/api/admin/inventory/movements/', {
            'product_id': self.pa.pk, 'branch': self.b1.pk,
            'movement_type': 'manual_entry', 'quantity': 1, 'reason': 'Intrusión',
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(branch_quantity(self.b1, self.pb), 99)

    def test_a_cannot_move_bs_product(self):
        res = self.client.post('/api/admin/inventory/movements/', {
            'product_id': self.pb.pk, 'branch': self.a1.pk,
            'movement_type': 'manual_entry', 'quantity': 1, 'reason': 'Intrusión',
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_a_cannot_read_bs_kardex(self):
        res = self.client.get(f'/api/admin/products/{self.pb.pk}/stock-card/')
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_a_cannot_transfer_to_bs_branch(self):
        res = self.client.post('/api/admin/inventory/transfers/', {
            'source_branch': self.a1.pk, 'destination_branch': self.b1.pk,
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(StockTransfer.objects.count(), 0)

    def test_a_cannot_count_bs_branch(self):
        res = self.client.post('/api/admin/inventory/counts/', {
            'branch': self.b1.pk,
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(InventoryCountModel.objects.count(), 0)

    def test_a_cannot_read_bs_transfer(self):
        user_b, _m = _p2d_member(self.b, 'p2d_iso_b', _INV_ALL)
        b2 = _p2d_branch(self.b, 'B2')
        transfer = create_stock_transfer(
            company=self.b, source_branch=self.b1, destination_branch=b2,
            actor=user_b,
        )
        res = self.client.get(f'/api/admin/inventory/transfers/{transfer.pk}/')
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_the_service_layer_refuses_a_cross_company_pairing(self):
        from .inventory_services import InvalidMovementError, create_stock_movement

        with self.assertRaises(InvalidMovementError):
            create_stock_movement(
                branch=self.a1, product_id=self.pb.pk,
                movement_type=StockMovement.MANUAL_ENTRY,
                quantity=1, reason='Cruzado',
            )


class Phase2dBranchScopedReadsTest(TestCase):
    """A restricted operator's totals are their branches, never the company's."""

    def setUp(self):
        cache.clear()
        self.company = _p2d_company('p2d-scope')
        self.a1 = _p2d_branch(self.company, 'A1')
        self.a2 = _p2d_branch(self.company, 'A2')
        self.a3 = _p2d_branch(self.company, 'A3')
        self.product = _p2d_product(self.company, 'Compartido', 'compartido-2d')
        _p2d_stock(self.a1, self.product, 5)
        _p2d_stock(self.a2, self.product, 7)
        _p2d_stock(self.a3, self.product, 100)

        self.user, self.m = _p2d_member(
            self.company, 'p2d_scoped', _INV_ALL, branches=[self.a1, self.a2],
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_aggregate_sums_only_the_visible_branches(self):
        res = self.client.get('/api/admin/inventory/summary/?branch=all')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['total_units'], 12)  # 5 + 7, never + 100

    def test_the_scope_payload_names_what_it_counted(self):
        """A heading must not claim more than the figure covers."""
        res = self.client.get('/api/admin/inventory/summary/?branch=all')
        scope = res.data['scope']
        self.assertTrue(scope['is_aggregate'])
        self.assertEqual({b['name'] for b in scope['branches']}, {'A1', 'A2'})

    def test_a_single_branch_answers_only_for_itself(self):
        res = self.client.get(f'/api/admin/inventory/summary/?branch={self.a1.pk}')
        self.assertEqual(res.data['total_units'], 5)

    def test_an_ungranted_branch_answers_404(self):
        res = self.client.get(f'/api/admin/inventory/summary/?branch={self.a3.pk}')
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_the_branch_list_offers_only_granted_branches(self):
        res = self.client.get('/api/admin/inventory/branches/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual({b['name'] for b in res.data['results']}, {'A1', 'A2'})
        self.assertTrue(res.data['allows_aggregate'])

    def test_the_kardex_hides_movements_of_an_ungranted_branch(self):
        res = self.client.get('/api/admin/inventory/movements/?branch=all')
        branches = {m['branch_name'] for m in res.data['results']}
        self.assertNotIn('A3', branches)

    def test_the_stock_card_totals_only_the_visible_branches(self):
        res = self.client.get(f'/api/admin/products/{self.product.pk}/stock-card/?branch=all')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['current_stock'], 12)

    def test_capability_and_branch_are_independent(self):
        """
        Holding inventory.adjust is not permission to adjust EVERY branch.

        This is the confusion the whole two-axis design exists to prevent: the
        capability says what, the grant says where, and both have to pass.
        """
        res = self.client.post('/api/admin/inventory/movements/', {
            'product_id': self.product.pk, 'branch': self.a3.pk,
            'movement_type': 'manual_entry', 'quantity': 1, 'reason': 'Sin acceso',
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(branch_quantity(self.a3, self.product), 100)

    def test_branch_access_without_the_capability_grants_nothing(self):
        """And the mirror image: reaching a branch is not permission to move it."""
        viewer, _m = _p2d_member(
            self.company, 'p2d_viewer', ['company.view', 'inventory.view'],
            branches=[self.a1],
        )
        client = APIClient()
        client.force_authenticate(user=viewer)
        self.assertEqual(
            client.get('/api/admin/inventory/summary/').status_code, status.HTTP_200_OK,
        )
        res = client.post('/api/admin/inventory/movements/', {
            'product_id': self.product.pk, 'branch': self.a1.pk,
            'movement_type': 'manual_entry', 'quantity': 1, 'reason': 'Sin permiso',
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)


class Phase2dTransferTest(TestCase):
    """
    Inter-branch transfers: stock moves at the edges, and only once.

    The interesting failures here are the ones a happy-path test never sees: a
    retried dispatch, a cancel after the van left, a source that cannot cover
    the lines.
    """

    def setUp(self):
        cache.clear()
        self.company = _p2d_company('p2d-transfer')
        self.src = _p2d_branch(self.company, 'Origen')
        self.dst = _p2d_branch(self.company, 'Destino')
        self.p1 = _p2d_product(self.company, 'Producto T1', 'producto-t1-2d')
        self.p2 = _p2d_product(self.company, 'Producto T2', 'producto-t2-2d')
        _p2d_stock(self.src, self.p1, 10)
        _p2d_stock(self.src, self.p2, 4)

        self.user, self.m = _p2d_member(self.company, 'p2d_tr', _INV_ALL)
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def _draft(self, lines=((None, 3),)):
        transfer = create_stock_transfer(
            company=self.company, source_branch=self.src,
            destination_branch=self.dst, actor=self.user,
        )
        for product, qty in lines:
            set_transfer_item(transfer, product=product or self.p1, quantity=qty)
        return transfer

    # --- creation rules ---

    def test_source_equal_to_destination_is_rejected(self):
        with self.assertRaises(TransferError):
            create_stock_transfer(
                company=self.company, source_branch=self.src,
                destination_branch=self.src, actor=self.user,
            )

    def test_a_foreign_branch_is_rejected(self):
        other = _p2d_company('p2d-transfer-other')
        foreign = _p2d_branch(other, 'Ajena')
        with self.assertRaises(TransferError):
            create_stock_transfer(
                company=self.company, source_branch=self.src,
                destination_branch=foreign, actor=self.user,
            )

    def test_a_foreign_product_is_rejected_as_a_line(self):
        other = _p2d_company('p2d-transfer-other2')
        foreign = _p2d_product(other, 'Ajeno', 'ajeno-2d')
        transfer = self._draft(lines=())
        with self.assertRaises(TransferError):
            set_transfer_item(transfer, product=foreign, quantity=1)

    def test_an_inactive_branch_cannot_receive(self):
        self.dst.is_active = False
        self.dst.save(update_fields=['is_active'])
        with self.assertRaises(TransferError):
            create_stock_transfer(
                company=self.company, source_branch=self.src,
                destination_branch=self.dst, actor=self.user,
            )

    # --- dispatch ---

    def test_dispatch_decrements_the_source_only(self):
        transfer = self._draft(lines=((self.p1, 3),))
        dispatch_transfer(transfer, actor=self.user)
        self.assertEqual(branch_quantity(self.src, self.p1), 7)
        # NOT credited to the destination yet: the units are on a van, and
        # showing them on a shelf that does not have them would make every count
        # at the destination wrong by the contents of that van.
        self.assertEqual(branch_quantity(self.dst, self.p1), 0)
        transfer.refresh_from_db()
        self.assertEqual(transfer.status, StockTransfer.STATUS_IN_TRANSIT)

    def test_dispatch_is_all_or_nothing(self):
        """One uncoverable line refuses the whole dispatch, leaving stock intact."""
        transfer = self._draft(lines=((self.p1, 3), (self.p2, 99)))
        with self.assertRaises(InsufficientStockError):
            dispatch_transfer(transfer, actor=self.user)
        self.assertEqual(branch_quantity(self.src, self.p1), 10)
        self.assertEqual(branch_quantity(self.src, self.p2), 4)
        transfer.refresh_from_db()
        self.assertEqual(transfer.status, StockTransfer.STATUS_DRAFT)

    def test_dispatch_is_idempotent(self):
        transfer = self._draft(lines=((self.p1, 3),))
        dispatch_transfer(transfer, actor=self.user)
        dispatch_transfer(transfer, actor=self.user)
        dispatch_transfer(transfer, actor=self.user)
        self.assertEqual(branch_quantity(self.src, self.p1), 7)
        self.assertEqual(
            StockMovement.objects.filter(
                transfer=transfer, movement_type=StockMovement.TRANSFER_OUT,
            ).count(), 1,
        )

    def test_an_empty_transfer_cannot_be_dispatched(self):
        transfer = self._draft(lines=())
        with self.assertRaises(TransferError):
            dispatch_transfer(transfer, actor=self.user)

    def test_lines_are_frozen_after_dispatch(self):
        transfer = self._draft(lines=((self.p1, 3),))
        dispatch_transfer(transfer, actor=self.user)
        with self.assertRaises(TransferError):
            set_transfer_item(transfer, product=self.p1, quantity=5)

    # --- receipt ---

    def test_receive_credits_the_destination(self):
        transfer = self._draft(lines=((self.p1, 3),))
        dispatch_transfer(transfer, actor=self.user)
        receive_transfer(transfer, actor=self.user)
        self.assertEqual(branch_quantity(self.src, self.p1), 7)
        self.assertEqual(branch_quantity(self.dst, self.p1), 3)
        transfer.refresh_from_db()
        self.assertEqual(transfer.status, StockTransfer.STATUS_RECEIVED)

    def test_receive_is_idempotent(self):
        transfer = self._draft(lines=((self.p1, 3),))
        dispatch_transfer(transfer, actor=self.user)
        receive_transfer(transfer, actor=self.user)
        receive_transfer(transfer, actor=self.user)
        self.assertEqual(branch_quantity(self.dst, self.p1), 3)
        self.assertEqual(
            StockMovement.objects.filter(
                transfer=transfer, movement_type=StockMovement.TRANSFER_IN,
            ).count(), 1,
        )

    def test_a_draft_cannot_be_received(self):
        transfer = self._draft(lines=((self.p1, 3),))
        with self.assertRaises(TransferError):
            receive_transfer(transfer, actor=self.user)

    def test_net_company_stock_is_unchanged_by_a_completed_transfer(self):
        """Units moved, none were created or destroyed."""
        before = branch_quantity(self.src, self.p1) + branch_quantity(self.dst, self.p1)
        transfer = self._draft(lines=((self.p1, 3),))
        dispatch_transfer(transfer, actor=self.user)
        receive_transfer(transfer, actor=self.user)
        after = branch_quantity(self.src, self.p1) + branch_quantity(self.dst, self.p1)
        self.assertEqual(before, after)
        self.p1.refresh_from_db()
        self.assertEqual(self.p1.inventory, after)

    # --- cancellation ---

    def test_a_draft_can_be_cancelled(self):
        transfer = self._draft(lines=((self.p1, 3),))
        cancel_transfer(transfer, actor=self.user)
        transfer.refresh_from_db()
        self.assertEqual(transfer.status, StockTransfer.STATUS_CANCELLED)
        self.assertEqual(branch_quantity(self.src, self.p1), 10)

    def test_a_dispatched_transfer_cannot_be_cancelled(self):
        """
        A deliberate refusal, not a missing feature.

        Its units have physically left the source. Flipping the status back would
        return them to the shelf in the database while they sit in a van, and the
        shop would then sell stock it does not have.
        """
        transfer = self._draft(lines=((self.p1, 3),))
        dispatch_transfer(transfer, actor=self.user)
        with self.assertRaises(TransferError):
            cancel_transfer(transfer, actor=self.user)
        transfer.refresh_from_db()
        self.assertEqual(transfer.status, StockTransfer.STATUS_IN_TRANSIT)
        self.assertEqual(branch_quantity(self.src, self.p1), 7)

    # --- Kardex and audit ---

    def test_movements_are_linked_to_the_transfer_on_both_sides(self):
        transfer = self._draft(lines=((self.p1, 3),))
        dispatch_transfer(transfer, actor=self.user)
        receive_transfer(transfer, actor=self.user)
        movements = StockMovement.objects.filter(transfer=transfer)
        self.assertEqual(movements.count(), 2)
        self.assertEqual(
            {m.movement_type for m in movements},
            {StockMovement.TRANSFER_OUT, StockMovement.TRANSFER_IN},
        )
        out = movements.get(movement_type=StockMovement.TRANSFER_OUT)
        self.assertEqual(out.branch_id, self.src.pk)
        self.assertEqual(out.company_id, self.company.pk)
        into = movements.get(movement_type=StockMovement.TRANSFER_IN)
        self.assertEqual(into.branch_id, self.dst.pk)

    def test_dispatch_and_receipt_are_audited_with_no_sensitive_data(self):
        transfer = self._draft(lines=((self.p1, 3),))
        dispatch_transfer(transfer, actor=self.user)
        receive_transfer(transfer, actor=self.user)
        logs = AdminAuditLog.objects.filter(
            action__in=['stock_transfer_dispatched', 'stock_transfer_received'],
        )
        self.assertEqual(logs.count(), 2)
        for log in logs:
            self.assertEqual(log.company_id, self.company.pk)
            blob = str(log.metadata).lower()
            for sensitive in ('password', 'token', 'stripe', 'cookie'):
                self.assertNotIn(sensitive, blob)

    def test_transfer_types_cannot_be_registered_by_hand(self):
        """
        A hand-written transfer_out with no matching transfer_in would be stock
        that simply vanished from the company.
        """
        for movement_type in ('transfer_out', 'transfer_in'):
            res = self.client.post('/api/admin/inventory/movements/', {
                'product_id': self.p1.pk, 'branch': self.src.pk,
                'movement_type': movement_type, 'quantity': 1, 'reason': 'A mano',
            }, format='json')
            self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST, movement_type)

    # --- authority ---

    def test_operating_a_transfer_needs_access_to_BOTH_branches(self):
        limited, _m = _p2d_member(
            self.company, 'p2d_tr_one_end', _INV_ALL, branches=[self.src],
        )
        transfer = self._draft(lines=((self.p1, 3),))
        client = APIClient()
        client.force_authenticate(user=limited)
        res = client.post(f'/api/admin/inventory/transfers/{transfer.pk}/dispatch/')
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(branch_quantity(self.src, self.p1), 10)

    def test_the_destination_manager_still_SEES_an_incoming_transfer(self):
        """Visibility follows the ends; acting on it needs both."""
        limited, _m = _p2d_member(
            self.company, 'p2d_tr_dest', _INV_ALL, branches=[self.dst],
        )
        transfer = self._draft(lines=((self.p1, 3),))
        client = APIClient()
        client.force_authenticate(user=limited)
        res = client.get(f'/api/admin/inventory/transfers/{transfer.pk}/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)


class Phase2dInventoryCountTest(TestCase):
    """
    Physical counts, and the re-read that makes them safe.

    The bug this class exists to prevent: applying a difference computed against
    an hour-old photograph of the stock, which silently un-sells everything sold
    while somebody walked the shelves.
    """

    def setUp(self):
        cache.clear()
        self.company = _p2d_company('p2d-count')
        self.branch = _p2d_branch(self.company, 'Sucursal C')
        self.product = _p2d_product(self.company, 'Producto C', 'producto-c-2d')
        _p2d_stock(self.branch, self.product, 10)
        self.user, self.m = _p2d_member(self.company, 'p2d_count', _INV_ALL)
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def _count(self, physical=None):
        count = create_inventory_count(
            company=self.company, branch=self.branch, actor=self.user,
        )
        if physical is not None:
            set_count_item(count, product=self.product, physical_quantity=physical)
        count.refresh_from_db()
        return count

    def test_a_foreign_branch_is_rejected(self):
        other = _p2d_company('p2d-count-other')
        foreign = _p2d_branch(other, 'Ajena')
        with self.assertRaises(InventoryCountError):
            create_inventory_count(
                company=self.company, branch=foreign, actor=self.user,
            )

    def test_a_foreign_product_is_rejected(self):
        other = _p2d_company('p2d-count-other2')
        foreign = _p2d_product(other, 'Ajeno', 'ajeno-c-2d')
        count = self._count()
        with self.assertRaises(InventoryCountError):
            set_count_item(count, product=foreign, physical_quantity=1)

    def test_a_negative_physical_quantity_is_rejected(self):
        count = self._count()
        with self.assertRaises(InventoryCountError):
            set_count_item(count, product=self.product, physical_quantity=-1)

    def test_theoretical_at_start_is_captured_and_never_overwritten(self):
        count = self._count(physical=8)
        item = count.items.get()
        self.assertEqual(item.theoretical_at_start, 10)
        # Stock moves, then the counter corrects their own entry.
        from .inventory_services import create_stock_movement
        create_stock_movement(
            branch=self.branch, product_id=self.product.pk,
            movement_type=StockMovement.MANUAL_EXIT, quantity=2, reason='Venta',
        )
        set_count_item(count, product=self.product, physical_quantity=7)
        item.refresh_from_db()
        self.assertEqual(item.theoretical_at_start, 10, 'la foto inicial es evidencia')
        self.assertEqual(item.physical_quantity, 7)

    def test_a_positive_difference_adds_stock(self):
        count = self._count(physical=13)
        approve_inventory_count(count, actor=self.user)
        self.assertEqual(branch_quantity(self.branch, self.product), 13)
        movement = StockMovement.objects.get(inventory_count=count)
        self.assertEqual(movement.movement_type, StockMovement.CORRECTION_POSITIVE)
        self.assertEqual(movement.quantity, 3)

    def test_a_negative_difference_removes_stock(self):
        count = self._count(physical=6)
        approve_inventory_count(count, actor=self.user)
        self.assertEqual(branch_quantity(self.branch, self.product), 6)
        movement = StockMovement.objects.get(inventory_count=count)
        self.assertEqual(movement.movement_type, StockMovement.CORRECTION_NEGATIVE)
        self.assertEqual(movement.quantity, 4)

    def test_no_difference_creates_no_movement(self):
        count = self._count(physical=10)
        movements = approve_inventory_count(count, actor=self.user)
        self.assertEqual(movements, [])
        self.assertEqual(StockMovement.objects.filter(inventory_count=count).count(), 0)
        count.refresh_from_db()
        self.assertEqual(count.status, InventoryCountModel.STATUS_APPROVED)

    def test_approval_re_reads_the_stock_instead_of_trusting_the_photo(self):
        """
        THE TEST THIS WHOLE MODEL EXISTS FOR.

        Stock is 10 when counting starts and the counter finds 8. Two units then
        sell legitimately, leaving 8 in the system. Approving must produce NO
        correction — the shelf and the system already agree — rather than
        applying the −2 that the starting photograph implies and destroying two
        real units.
        """
        from .inventory_services import create_stock_movement

        count = self._count(physical=8)
        create_stock_movement(
            branch=self.branch, product_id=self.product.pk,
            movement_type=StockMovement.SALE_EXIT, quantity=2,
            reason='Venta durante el conteo',
        )
        self.assertEqual(branch_quantity(self.branch, self.product), 8)

        movements = approve_inventory_count(count, actor=self.user)
        self.assertEqual(movements, [], 'no debe haber corrección: ya coinciden')
        self.assertEqual(branch_quantity(self.branch, self.product), 8)

        item = count.items.get()
        self.assertEqual(item.theoretical_at_start, 10)
        self.assertEqual(item.theoretical_at_approval, 8)
        self.assertEqual(item.difference, 0)

    def test_an_uncounted_product_is_skipped_not_written_off(self):
        """"Nobody counted this" is not "there are none of these"."""
        second = _p2d_product(self.company, 'Producto C2', 'producto-c2-2d')
        _p2d_stock(self.branch, second, 20)

        count = self._count(physical=10)
        set_count_item(count, product=second, physical_quantity=None)

        approve_inventory_count(count, actor=self.user)
        self.assertEqual(branch_quantity(self.branch, second), 20)
        self.assertIsNone(count.items.get(product=second).difference)

    def test_double_approval_does_not_duplicate_corrections(self):
        count = self._count(physical=13)
        approve_inventory_count(count, actor=self.user)
        approve_inventory_count(count, actor=self.user)
        self.assertEqual(branch_quantity(self.branch, self.product), 13)
        self.assertEqual(StockMovement.objects.filter(inventory_count=count).count(), 1)

    def test_a_cancelled_count_cannot_be_approved(self):
        count = self._count(physical=13)
        cancel_inventory_count(count, actor=self.user)
        with self.assertRaises(InventoryCountError):
            approve_inventory_count(count, actor=self.user)
        self.assertEqual(branch_quantity(self.branch, self.product), 10)

    def test_an_approved_count_cannot_be_cancelled(self):
        count = self._count(physical=13)
        approve_inventory_count(count, actor=self.user)
        with self.assertRaises(InventoryCountError):
            cancel_inventory_count(count, actor=self.user)

    def test_a_count_with_nothing_counted_cannot_be_approved(self):
        count = self._count()
        with self.assertRaises(InventoryCountError):
            approve_inventory_count(count, actor=self.user)

    def test_approval_is_audited(self):
        count = self._count(physical=13)
        approve_inventory_count(count, actor=self.user)
        log = AdminAuditLog.objects.filter(action='inventory_count_approved').first()
        self.assertIsNotNone(log)
        self.assertEqual(log.company_id, self.company.pk)
        self.assertEqual(log.metadata['branch_id'], self.branch.pk)


class Phase2dReplenishmentTest(TestCase):
    """suggested = max(target − current, 0), only at or below the minimum."""

    def setUp(self):
        cache.clear()
        self.company = _p2d_company('p2d-repl')
        self.b1 = _p2d_branch(self.company, 'R1')
        self.b2 = _p2d_branch(self.company, 'R2')
        self.product = _p2d_product(self.company, 'Producto R', 'producto-r-2d')

    def test_below_minimum_suggests_the_gap_to_target(self):
        _p2d_stock(self.b1, self.product, 2, minimum=3, target=10)
        rows = get_replenishment_rows([self.b1])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['suggested_quantity'], 8)

    def test_above_minimum_suggests_nothing(self):
        _p2d_stock(self.b1, self.product, 5, minimum=3, target=10)
        self.assertEqual(get_replenishment_rows([self.b1]), [])

    def test_exactly_at_the_minimum_still_suggests(self):
        _p2d_stock(self.b1, self.product, 3, minimum=3, target=10)
        rows = get_replenishment_rows([self.b1])
        self.assertEqual(rows[0]['suggested_quantity'], 7)

    def test_a_product_with_no_minimum_is_never_suggested(self):
        """No policy configured is not the same as "needs restocking"."""
        _p2d_stock(self.b1, self.product, 0, minimum=0, target=0)
        self.assertEqual(get_replenishment_rows([self.b1]), [])

    def test_below_minimum_with_no_target_is_listed_with_zero(self):
        """The operator still needs to SEE it; the platform will not guess how much."""
        _p2d_stock(self.b1, self.product, 1, minimum=5, target=0)
        rows = get_replenishment_rows([self.b1])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['suggested_quantity'], 0)

    def test_suggestions_are_per_branch(self):
        """
        The same product can be scarce downtown and overstocked elsewhere; a
        single company-wide threshold could never say that.
        """
        _p2d_stock(self.b1, self.product, 1, minimum=5, target=10)
        _p2d_stock(self.b2, self.product, 50, minimum=5, target=10)
        rows = get_replenishment_rows([self.b1, self.b2])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['branch_id'], self.b1.pk)

    def test_it_creates_nothing(self):
        """A suggestion is a suggestion: no purchase, no transfer, no movement."""
        _p2d_stock(self.b1, self.product, 1, minimum=5, target=10)
        before_movements = StockMovement.objects.count()
        get_replenishment_rows([self.b1, self.b2])
        self.assertEqual(StockMovement.objects.count(), before_movements)
        self.assertEqual(StockTransfer.objects.count(), 0)
        self.assertEqual(branch_quantity(self.b1, self.product), 1)

    def test_the_endpoint_is_branch_scoped(self):
        _p2d_stock(self.b1, self.product, 1, minimum=5, target=10)
        _p2d_stock(self.b2, self.product, 0, minimum=5, target=10)
        user, _m = _p2d_member(self.company, 'p2d_repl', _INV_ALL, branches=[self.b1])
        client = APIClient()
        client.force_authenticate(user=user)
        res = client.get('/api/admin/inventory/replenishment/?branch=all')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual({r['branch_id'] for r in res.data['results']}, {self.b1.pk})


class Phase2dConsistencyTest(TestCase):
    """
    `Product.inventory` is a DERIVED aggregate. It must never drift.

    The compatibility field survives because the public catalogue, the admin
    product list and several reports have exposed a field with that name since
    Phase 0. What it may not do is disagree with the shelves it summarises —
    a second source of truth is worse than no second field at all.
    """

    def setUp(self):
        cache.clear()
        self.company = _p2d_company('p2d-consist')
        self.b1 = _p2d_branch(self.company, 'K1')
        self.b2 = _p2d_branch(self.company, 'K2')
        self.product = _p2d_product(self.company, 'Producto K', 'producto-k-2d')
        self.user, _m = _p2d_member(self.company, 'p2d_consist', _INV_ALL)

    def test_the_aggregate_is_the_sum_across_branches(self):
        _p2d_stock(self.b1, self.product, 4)
        _p2d_stock(self.b2, self.product, 6)
        self.product.refresh_from_db()
        self.assertEqual(self.product.inventory, 10)
        self.assertEqual(product_inventory_drift(self.company), [])

    def test_every_movement_type_keeps_the_aggregate_in_step(self):
        from .inventory_services import create_stock_movement

        _p2d_stock(self.b1, self.product, 10)
        for movement_type, qty in (
            (StockMovement.PURCHASE_ENTRY, 5),
            (StockMovement.MANUAL_EXIT, 3),
            (StockMovement.DAMAGED_EXIT, 1),
            (StockMovement.CORRECTION_POSITIVE, 2),
            (StockMovement.RETURN_ENTRY, 1),
        ):
            create_stock_movement(
                branch=self.b1, product_id=self.product.pk,
                movement_type=movement_type, quantity=qty, reason='Consistencia',
            )
        self.product.refresh_from_db()
        self.assertEqual(self.product.inventory, branch_quantity(self.b1, self.product))
        self.assertEqual(product_inventory_drift(self.company), [])

    def test_a_transfer_moves_units_without_changing_the_total(self):
        _p2d_stock(self.b1, self.product, 10)
        transfer = create_stock_transfer(
            company=self.company, source_branch=self.b1,
            destination_branch=self.b2, actor=self.user,
        )
        set_transfer_item(transfer, product=self.product, quantity=4)
        dispatch_transfer(transfer, actor=self.user)

        # Mid-flight the units belong to neither shelf, and the aggregate says so.
        self.product.refresh_from_db()
        self.assertEqual(self.product.inventory, 6)
        self.assertEqual(product_inventory_drift(self.company), [])

        receive_transfer(transfer, actor=self.user)
        self.product.refresh_from_db()
        self.assertEqual(self.product.inventory, 10)
        self.assertEqual(product_inventory_drift(self.company), [])

    def test_a_count_approval_keeps_the_aggregate_in_step(self):
        _p2d_stock(self.b1, self.product, 10)
        count = create_inventory_count(
            company=self.company, branch=self.b1, actor=self.user,
        )
        set_count_item(count, product=self.product, physical_quantity=7)
        approve_inventory_count(count, actor=self.user)
        self.product.refresh_from_db()
        self.assertEqual(self.product.inventory, 7)
        self.assertEqual(product_inventory_drift(self.company), [])

    def test_drift_is_detected_and_repairable(self):
        """
        A raw write behind the service layer's back IS drift, and the repair path
        exists. The point is that the detector works: nothing in the application
        writes this field, but the invariant should be provable, not assumed.
        """
        _p2d_stock(self.b1, self.product, 10)
        Product.objects.filter(pk=self.product.pk).update(inventory=999)
        drift = product_inventory_drift(self.company)
        self.assertEqual(len(drift), 1)
        self.assertEqual(drift[0]['inventory'], 999)
        self.assertEqual(drift[0]['branch_total'], 10)

        recalculate_product_inventory(self.product.pk)
        self.assertEqual(product_inventory_drift(self.company), [])

    def test_no_endpoint_writes_stock_outside_the_service_layer(self):
        """
        The product API cannot edit stock, and says so instead of ignoring it.

        Silently dropping the field would leave a form that looks like it saved.
        """
        admin, _m = _p2d_member(
            self.company, 'p2d_prod_admin',
            ['company.view', 'products.view', 'products.manage'],
        )
        client = APIClient()
        client.force_authenticate(user=admin)
        _p2d_stock(self.b1, self.product, 10)

        res = client.patch(
            f'/api/admin/products/{self.product.pk}/', {'inventory': 500}, format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('inventory', res.data)
        self.product.refresh_from_db()
        self.assertEqual(self.product.inventory, 10)

    def test_creating_a_product_with_stock_writes_an_initial_stock_movement(self):
        """No unit appears in a branch without a Kardex line explaining it."""
        admin, _m = _p2d_member(
            self.company, 'p2d_prod_admin2',
            ['company.view', 'products.view', 'products.manage', 'inventory.adjust'],
        )
        client = APIClient()
        client.force_authenticate(user=admin)

        res = client.post('/api/admin/products/', {
            'name': 'Con stock inicial', 'slug': 'con-stock-inicial-2d',
            'price': '50.00', 'inventory': 12,
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        created = Product.objects.get(slug='con-stock-inicial-2d')
        self.assertEqual(created.inventory, 12)

        movement = StockMovement.objects.get(product=created)
        self.assertEqual(movement.movement_type, StockMovement.INITIAL_STOCK)
        self.assertEqual(movement.quantity, 12)
        self.assertEqual(movement.stock_before, 0)
        self.assertEqual(movement.stock_after, 12)
        self.assertEqual(movement.company_id, self.company.pk)
        self.assertEqual(product_inventory_drift(self.company), [])

    def test_a_company_with_no_branch_cannot_open_stock(self):
        """Units have to be SOMEWHERE; this refuses rather than inventing a place."""
        bare = Company.objects.create(name='Sin sucursal', slug='sin-sucursal-2d')
        from .company_provisioning import provision_company_access_defaults

        provision_company_access_defaults(bare)
        Branch.objects.filter(company=bare).delete()

        admin, _m = _p2d_member(
            bare, 'p2d_bare_admin',
            ['company.view', 'products.view', 'products.manage'],
        )
        client = APIClient()
        client.force_authenticate(user=admin)
        res = client.post('/api/admin/products/', {
            'name': 'Sin lugar', 'slug': 'sin-lugar-2d', 'price': '10.00', 'inventory': 3,
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(Product.objects.filter(slug='sin-lugar-2d').exists())


class Phase2dConcurrencyTest(TransactionTestCase):
    """
    Two simultaneous exits of the last unit: exactly one may win.

    WHAT SQLITE CAN AND CANNOT PROVE HERE
    -------------------------------------
    `select_for_update()` is a no-op on SQLite — the engine serialises writes with
    a database-level lock instead of row locks — so a threaded race on SQLite
    tests the engine's global lock, not this module's row locking. Running it
    anyway would produce a green test that proves nothing about PostgreSQL, which
    is worse than no test.

    So: the sequential invariant is asserted unconditionally (it must hold on any
    backend), and the genuinely concurrent case runs ONLY on a backend with real
    row locking. On SQLite it is skipped, loudly, rather than faked.
    """

    def setUp(self):
        cache.clear()
        self.company = _p2d_company('p2d-conc')
        self.branch = _p2d_branch(self.company, 'Conc')
        self.product = _p2d_product(self.company, 'Producto Z', 'producto-z-2d')
        _p2d_stock(self.branch, self.product, 1)

    def _exit_one(self):
        from .inventory_services import create_stock_movement

        return create_stock_movement(
            branch=self.branch, product_id=self.product.pk,
            movement_type=StockMovement.MANUAL_EXIT, quantity=1, reason='Carrera',
        )

    def test_the_second_exit_of_the_last_unit_fails(self):
        self._exit_one()
        with self.assertRaises(InsufficientStockError):
            self._exit_one()
        self.assertEqual(branch_quantity(self.branch, self.product), 0)

    def test_stock_never_goes_below_zero(self):
        for _ in range(5):
            try:
                self._exit_one()
            except InsufficientStockError:
                pass
        self.assertEqual(branch_quantity(self.branch, self.product), 0)
        self.assertGreaterEqual(branch_quantity(self.branch, self.product), 0)

    def test_a_transfer_cannot_dispatch_stock_a_manual_exit_already_took(self):
        user, _m = _p2d_member(self.company, 'p2d_conc_user', _INV_ALL)
        dst = _p2d_branch(self.company, 'Conc destino')
        transfer = create_stock_transfer(
            company=self.company, source_branch=self.branch,
            destination_branch=dst, actor=user,
        )
        set_transfer_item(transfer, product=self.product, quantity=1)

        self._exit_one()  # the unit is gone before the van arrives
        with self.assertRaises(InsufficientStockError):
            dispatch_transfer(transfer, actor=user)
        self.assertEqual(branch_quantity(self.branch, self.product), 0)
        self.assertEqual(branch_quantity(dst, self.product), 0)

    def test_a_count_approval_uses_the_stock_a_later_movement_left(self):
        """Approval re-reads under lock, so a movement in between is respected."""
        user, _m = _p2d_member(self.company, 'p2d_conc_count', _INV_ALL)
        from .inventory_services import create_stock_movement

        # setUp left 1 unit; bring the shelf to 11.
        create_stock_movement(
            branch=self.branch, product_id=self.product.pk,
            movement_type=StockMovement.PURCHASE_ENTRY, quantity=10, reason='Compra',
        )
        self.assertEqual(branch_quantity(self.branch, self.product), 11)

        count = create_inventory_count(
            company=self.company, branch=self.branch, actor=user,
        )
        set_count_item(count, product=self.product, physical_quantity=10)

        # Four sell while the counting is being reviewed: the shelf is now 7.
        create_stock_movement(
            branch=self.branch, product_id=self.product.pk,
            movement_type=StockMovement.MANUAL_EXIT, quantity=4, reason='Venta',
        )
        approve_inventory_count(count, actor=user)

        item = count.items.get()
        self.assertEqual(item.theoretical_at_start, 11)
        self.assertEqual(item.theoretical_at_approval, 7, 're-lee el stock del momento')
        # 10 counted vs 7 at approval → +3. Using the STARTING figure would have
        # applied 10 − 11 = −1 and destroyed a real unit.
        self.assertEqual(item.difference, 3)
        self.assertEqual(branch_quantity(self.branch, self.product), 10)

    def test_simultaneous_exits_leave_exactly_one_winner(self):
        import threading

        from django.db import connection, connections

        if connection.vendor == 'sqlite':
            self.skipTest(
                'SQLite has no row-level locking: select_for_update() is a no-op, '
                'so a threaded race here would exercise the engine\'s global write '
                'lock rather than this module\'s locking. Run the suite against '
                'PostgreSQL to exercise it.'
            )

        results = []

        def worker():
            try:
                self._exit_one()
                results.append('ok')
            except InsufficientStockError:
                results.append('refused')
            finally:
                connections.close_all()

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(sorted(results), ['ok', 'refused'])
        self.assertEqual(branch_quantity(self.branch, self.product), 0)

    def test_the_service_layer_locks_the_stock_row_not_the_product(self):
        """
        Introspection, because the behavioural test above cannot run on SQLite.

        Locking the PRODUCT would serialise every branch of a chain against every
        other for the same article, turning unrelated shops into each other's
        queue. The lock has to be on BranchStock.
        """
        import inspect

        from . import inventory_services

        source = inspect.getsource(inventory_services._locked_branch_stocks)
        self.assertIn('select_for_update', source)
        self.assertIn('BranchStock', source)
        self.assertIn("order_by('branch_id', 'product_id')", source)

        core = inspect.getsource(inventory_services.create_stock_movement)
        self.assertNotIn('Product.objects.select_for_update', core)


class Phase2dMigrationRuleTest(TestCase):
    """
    The rule migration 0025 uses to place historical stock — tested directly.

    The migration itself cannot run inside a test whose database is already
    migrated, so its DECISION FUNCTION is exercised here against the real models.
    That function is where the whole risk lives: everything else in the migration
    is bulk writes, and the only way to corrupt an installation is to answer
    "which branch holds these units?" wrongly.
    """

    def setUp(self):
        cache.clear()

    @property
    def _module(self):
        # The module name starts with a digit, so it cannot be imported with an
        # `import` statement — importlib is the only way in.
        import importlib

        return importlib.import_module(
            'store.migrations.0025_backfill_multibranch_inventory',
        )

    def _resolve(self, company, overrides=None):
        problems = []
        branch = self._module._resolve_historical_branch(
            company, Branch, overrides or {}, problems,
        )
        return branch, problems

    def test_one_active_branch_resolves_without_asking(self):
        """
        Unambiguous by construction — not "the first of several", "the only one".

        This is every single-shop installation, which is every installation that
        exists today.
        """
        company = Company.objects.create(name='Una sucursal', slug='una-suc-2d')
        only = _p2d_branch(company, 'Única')
        branch, problems = self._resolve(company)
        self.assertEqual(branch, only)
        self.assertEqual(problems, [])

    def test_several_active_branches_REFUSE_instead_of_guessing(self):
        """
        The decision this phase is most exposed to.

        With two branches and one integer there is no fact in the database saying
        where the units are. Splitting them, or taking the lowest id, would write
        a number that looks authoritative and is fiction — and every count,
        report and replenishment decision downstream would inherit it silently.
        """
        company = Company.objects.create(name='Dos sucursales', slug='dos-suc-2d')
        _p2d_branch(company, 'Centro')
        _p2d_branch(company, 'Cayma')
        branch, problems = self._resolve(company)
        self.assertIsNone(branch)
        self.assertEqual(len(problems), 1)
        self.assertIn('Centro', problems[0])
        self.assertIn('Cayma', problems[0])

    def test_no_active_branch_refuses_too(self):
        company = Company.objects.create(name='Sin sucursales', slug='sin-suc-2d')
        branch, problems = self._resolve(company)
        self.assertIsNone(branch)
        self.assertIn('NINGUNA sucursal activa', problems[0])

    def test_an_inactive_branch_does_not_count_as_the_only_one(self):
        company = Company.objects.create(name='Inactiva', slug='inactiva-suc-2d')
        active = _p2d_branch(company, 'Activa')
        Branch.objects.create(company=company, name='Cerrada', is_active=False)
        branch, problems = self._resolve(company)
        self.assertEqual(branch, active)
        self.assertEqual(problems, [])

    def test_the_operator_can_answer_explicitly(self):
        company = Company.objects.create(name='Explícita', slug='explicita-suc-2d')
        _p2d_branch(company, 'Centro')
        cayma = _p2d_branch(company, 'Cayma')
        branch, problems = self._resolve(company, {'explicita-suc-2d': 'Cayma'})
        self.assertEqual(branch, cayma)
        self.assertEqual(problems, [])

    def test_an_override_naming_a_missing_branch_refuses(self):
        """A typo in the setting must not fall through to a guess."""
        company = Company.objects.create(name='Typo', slug='typo-suc-2d')
        _p2d_branch(company, 'Centro')
        _p2d_branch(company, 'Cayma')
        branch, problems = self._resolve(company, {'typo-suc-2d': 'Kayma'})
        self.assertIsNone(branch)
        self.assertIn('no existe', problems[0])

    def test_a_company_with_nothing_to_place_is_not_asked_about(self):
        """Existing is not a reason to block the migration."""
        empty = Company.objects.create(name='Vacía', slug='vacia-suc-2d')
        _p2d_branch(empty, 'A')
        _p2d_branch(empty, 'B')  # ambiguous, but has no stock, orders or Kardex
        needing = self._module._companies_needing_a_branch(
            Company, Product, StockMovement, Order,
        )
        self.assertNotIn(empty, needing)

    def test_a_company_with_stock_IS_asked_about(self):
        company = Company.objects.create(name='Con stock', slug='con-stock-suc-2d')
        _p2d_branch(company, 'A')
        Product.objects.create(
            company=company, name='Algo', slug='algo-2d',
            price=Decimal('1.00'), inventory=5,
        )
        needing = self._module._companies_needing_a_branch(
            Company, Product, StockMovement, Order,
        )
        self.assertIn(company, needing)


class Phase2dStorefrontTest(TestCase):
    """
    The public store sells what the FULFILLMENT BRANCH can deliver.

    Showing the company-wide total would promise units the checkout cannot take:
    a customer sees 20, checkout finds 2 in the branch that ships, and the sale
    fails at the last step for no reason the customer can understand.
    """

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.company = _p2d_company('p2d-store')
        self.main = _p2d_branch(self.company, 'Tienda online')
        self.other = _p2d_branch(self.company, 'Almacén')
        self.company.default_inventory_branch = self.main
        self.company.save(update_fields=['default_inventory_branch'])

        self.product = _p2d_product(self.company, 'Vendible', 'vendible-2d', '100.00')
        _p2d_stock(self.main, self.product, 2)
        _p2d_stock(self.other, self.product, 18)  # 20 company-wide, 2 sellable

    def _storefront(self):
        return _storefront_of(self.company)

    def test_the_catalogue_shows_the_fulfillment_branchs_stock(self):
        with self._storefront():
            res = self.client.get(f'/api/products/?slug={self.product.slug}')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data[0]['inventory'], 2, 'no los 20 de toda la empresa')

    def test_the_cart_refuses_more_than_the_branch_holds(self):
        with self._storefront():
            res = self.client.post('/api/cart/add/', {
                'session_key': 'p2d-store-1', 'product': self.product.pk, 'quantity': 5,
            }, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('Disponible: 2', res.data['detail'])

    def test_the_cart_accepts_what_the_branch_holds(self):
        with self._storefront():
            res = self.client.post('/api/cart/add/', {
                'session_key': 'p2d-store-2', 'product': self.product.pk, 'quantity': 2,
            }, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)

    def test_checkout_validates_against_the_branch_not_the_company(self):
        CartItem.objects.create(
            session_key='p2d-store-3', product=self.product, quantity=5,
        )
        with self._storefront():
            res = self.client.post('/api/payments/create-checkout-session/', {
                'session_key': 'p2d-store-3',
                'customer_name': 'Ana Torres', 'customer_email': 'ana@example.com',
                'customer_phone': '+51 999 999 999',
                'document_type': 'dni', 'document_number': '12345678',
                'delivery_method': 'pickup_store', 'receipt_type': 'boleta',
                'accepted_terms': True, 'accepted_warranty_policy': True,
            }, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('errors', res.json())

    @patch('stripe.checkout.Session.create')
    def test_checkout_stamps_the_fulfillment_branch_on_the_order(self, mock_create):
        mock = MagicMock()
        mock.id = 'cs_p2d_branch'
        mock.url = 'https://checkout.stripe.com/pay/cs_p2d_branch'
        mock_create.return_value = mock

        CartItem.objects.create(
            session_key='p2d-store-4', product=self.product, quantity=2,
        )
        with self._storefront():
            res = self.client.post('/api/payments/create-checkout-session/', {
                'session_key': 'p2d-store-4',
                'customer_name': 'Ana Torres', 'customer_email': 'ana@example.com',
                'customer_phone': '+51 999 999 999',
                'document_type': 'dni', 'document_number': '12345678',
                'delivery_method': 'pickup_store', 'receipt_type': 'boleta',
                'accepted_terms': True, 'accepted_warranty_policy': True,
            }, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        order = Order.objects.get(stripe_session_id='cs_p2d_branch')
        self.assertEqual(order.fulfillment_branch_id, self.main.pk)

    def test_a_company_with_no_fulfillment_branch_cannot_check_out(self):
        """
        Two branches and no explicit choice: the store says so instead of
        shipping from a shop that does not know it sold anything.
        """
        self.company.default_inventory_branch = None
        self.company.save(update_fields=['default_inventory_branch'])
        CartItem.objects.create(
            session_key='p2d-store-5', product=self.product, quantity=1,
        )
        with self._storefront():
            res = self.client.post('/api/payments/create-checkout-session/', {
                'session_key': 'p2d-store-5',
                'customer_name': 'Ana Torres', 'customer_email': 'ana@example.com',
                'customer_phone': '+51 999 999 999',
                'document_type': 'dni', 'document_number': '12345678',
                'delivery_method': 'pickup_store', 'receipt_type': 'boleta',
                'accepted_terms': True, 'accepted_warranty_policy': True,
            }, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('sucursal de despacho', res.data['detail'])

    def test_a_single_branch_company_needs_no_configuration(self):
        """
        The rule that keeps every existing single-shop installation selling
        across the upgrade, with nothing to configure.
        """
        solo = _p2d_company('p2d-store-solo')
        Branch.objects.filter(company=solo).delete()
        only = _p2d_branch(solo, 'La única')
        solo.refresh_from_db()  # the deleted branch cleared the pointer (SET_NULL)
        self.assertIsNone(solo.default_inventory_branch_id)
        self.assertEqual(company_fulfillment_branch(solo), only)

    def test_the_sale_exit_comes_off_the_orders_own_branch(self):
        from .inventory_services import record_sale_stock_movements

        order = Order.objects.create(
            company=self.company, fulfillment_branch=self.main,
            customer_email='x@example.com', total=Decimal('200.00'),
            status=Order.Status.PAID, paid=True, paid_at=timezone.now(),
        )
        OrderItem.objects.create(
            order=order, product=self.product, quantity=2, price=self.product.price,
        )
        record_sale_stock_movements(order)
        self.assertEqual(branch_quantity(self.main, self.product), 0)
        self.assertEqual(branch_quantity(self.other, self.product), 18, 'intacto')

    def test_a_shortfall_is_never_covered_from_another_branch(self):
        """
        18 units sit in the warehouse and the order still fails.

        Quietly taking them would create a second, invisible discrepancy in a
        branch nobody is looking at — the payment is already captured, so the
        honest outcome is a flagged order.
        """
        from .inventory_services import record_sale_stock_movements

        order = Order.objects.create(
            company=self.company, fulfillment_branch=self.main,
            customer_email='x@example.com', total=Decimal('500.00'),
            status=Order.Status.PAID, paid=True, paid_at=timezone.now(),
        )
        OrderItem.objects.create(
            order=order, product=self.product, quantity=5, price=self.product.price,
        )
        record_sale_stock_movements(order)
        self.assertEqual(branch_quantity(self.main, self.product), 2, 'sin tocar')
        self.assertEqual(branch_quantity(self.other, self.product), 18, 'sin tocar')
        self.assertIn('Stock insuficiente', order.payment_error)
        self.assertIn(str(self.main.pk), order.payment_error)


class Phase2dBranchAccessApiTest(TestCase):
    """
    Administering branch access: grants are company-scoped, and audited.

    Capability delegation and branch access are separate on purpose — granting
    somebody the inventory role must not also hand them every shop.
    """

    def setUp(self):
        cache.clear()
        self.company = _p2d_company('p2d-api')
        self.b1 = _p2d_branch(self.company, 'API 1')
        self.b2 = _p2d_branch(self.company, 'API 2')
        self.other = _p2d_company('p2d-api-other')
        self.foreign = _p2d_branch(self.other, 'Ajena')

        self.admin, self.admin_m = _p2d_member(
            self.company, 'p2d_api_admin',
            ['company.view', 'company.manage', 'memberships.view', 'memberships.manage'],
        )
        self.target = User.objects.create_user(username='p2d_api_target', password='x')
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)

    def _create_membership(self, **extra):
        payload = {
            'user': self.target.pk, 'company': self.company.pk, 'role': 'inventory',
        }
        payload.update(extra)
        return self.client.post('/api/admin/memberships/', payload, format='json')

    def test_a_new_membership_defaults_to_all_branches(self):
        """
        Matching what a membership meant before Phase 2D: nothing restricted
        these people by branch, and creating them restricted by surprise would be
        a silent narrowing nobody asked for.
        """
        res = self._create_membership()
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data['branch_access_mode'], 'all')

    def test_a_membership_can_be_created_with_selected_branches(self):
        res = self._create_membership(
            branch_access_mode='selected', branch_access=[self.b1.pk],
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data['branch_access_mode'], 'selected')
        self.assertEqual([b['id'] for b in res.data['branch_access']], [self.b1.pk])

    def test_a_foreign_branch_cannot_be_granted(self):
        res = self._create_membership(
            branch_access_mode='selected', branch_access=[self.foreign.pk],
        )
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
        from .models import Membership

        self.assertFalse(
            Membership.objects.filter(user=self.target, company=self.company).exists(),
            'la membresía no debe quedar a medio configurar',
        )

    def test_grants_can_be_replaced(self):
        created = self._create_membership(
            branch_access_mode='selected', branch_access=[self.b1.pk],
        )
        pk = created.data['id']
        res = self.client.patch(
            f'/api/admin/memberships/{pk}/', {'branch_access': [self.b2.pk]},
            format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual([b['id'] for b in res.data['branch_access']], [self.b2.pk])
        self.assertTrue(has_branch_access(self.target, self.b2))
        self.assertFalse(has_branch_access(self.target, self.b1))

    def test_an_empty_grant_list_revokes_everything(self):
        """A real, intended state — and the reason "empty means all" was never on the table."""
        created = self._create_membership(
            branch_access_mode='selected', branch_access=[self.b1.pk, self.b2.pk],
        )
        pk = created.data['id']
        res = self.client.patch(
            f'/api/admin/memberships/{pk}/', {'branch_access': []}, format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['branch_access'], [])
        self.assertFalse(visible_branches(self.target, self.company).exists())

    def test_a_revoked_default_branch_is_cleared(self):
        created = self._create_membership(
            branch='__placeholder__' if False else self.b1.pk,
            branch_access_mode='selected', branch_access=[self.b1.pk],
        )
        pk = created.data['id']
        res = self.client.patch(
            f'/api/admin/memberships/{pk}/', {'branch_access': [self.b2.pk]},
            format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIsNone(res.data['branch'], 'un puntero obsoleto se limpia')

    def test_branch_access_changes_are_audited(self):
        self._create_membership(
            branch_access_mode='selected', branch_access=[self.b1.pk],
        )
        log = AdminAuditLog.objects.filter(action='membership_created').first()
        self.assertIsNotNone(log)
        self.assertEqual(log.metadata['branch_access_mode'], 'selected')
        self.assertEqual(log.metadata['branch_access'], [self.b1.pk])
        self.assertEqual(log.company_id, self.company.pk)

    def _set_fulfillment(self, value):
        return self.client.patch(
            f'/api/admin/companies/{self.company.pk}/fulfillment-branch/',
            {'branch': value}, format='json',
        )

    def test_the_fulfillment_branch_must_belong_to_the_company(self):
        res = self._set_fulfillment(self.foreign.pk)
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.company.refresh_from_db()
        self.assertNotEqual(self.company.default_inventory_branch_id, self.foreign.pk)

    def test_the_fulfillment_branch_can_be_set_to_an_own_branch(self):
        """
        A company administrator configures this without a platform operator.

        Where the online store ships from is an operational decision that belongs
        to the business; the rest of Company (slug, is_active) stays
        platform-only, which is why this has its own endpoint.
        """
        res = self._set_fulfillment(self.b2.pk)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.company.refresh_from_db()
        self.assertEqual(self.company.default_inventory_branch_id, self.b2.pk)

    def test_an_inactive_branch_cannot_be_the_fulfillment_branch(self):
        self.b2.is_active = False
        self.b2.save(update_fields=['is_active'])
        self.assertEqual(self._set_fulfillment(self.b2.pk).status_code,
                         status.HTTP_400_BAD_REQUEST)

    def test_the_fulfillment_branch_can_be_cleared(self):
        """A legitimate choice with a visible consequence: checkout then refuses."""
        self._set_fulfillment(self.b2.pk)
        res = self._set_fulfillment(None)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.company.refresh_from_db()
        self.assertIsNone(self.company.default_inventory_branch_id)

    def test_a_member_without_company_manage_cannot_change_it(self):
        plain, _m = _p2d_member(
            self.company, 'p2d_api_plain', ['company.view', 'inventory.view'],
        )
        client = APIClient()
        client.force_authenticate(user=plain)
        res = client.patch(
            f'/api/admin/companies/{self.company.pk}/fulfillment-branch/',
            {'branch': self.b2.pk}, format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_another_companys_admin_cannot_change_it(self):
        outsider, _m = _p2d_member(
            self.other, 'p2d_api_outsider',
            ['company.view', 'company.manage'],
        )
        client = APIClient()
        client.force_authenticate(user=outsider)
        res = client.patch(
            f'/api/admin/companies/{self.company.pk}/fulfillment-branch/',
            {'branch': self.b2.pk}, format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_the_change_is_audited(self):
        self._set_fulfillment(self.b2.pk)
        log = AdminAuditLog.objects.filter(
            action='company_fulfillment_branch_changed',
        ).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.metadata['new_branch_id'], self.b2.pk)
        self.assertEqual(log.company_id, self.company.pk)


class Phase2dProvisioningTest(TestCase):
    """A new tenant arrives usable: one branch, and the storefront pointed at it."""

    def test_a_new_company_gets_its_first_branch(self):
        from .company_provisioning import (
            PRESET_BRANCH_NAME, provision_company_access_defaults,
        )

        company = Company.objects.create(name='Nueva', slug='nueva-prov-2d')
        result = provision_company_access_defaults(company)
        self.assertTrue(result['branch_created'])
        company.refresh_from_db()
        self.assertEqual(company.branches.count(), 1)
        self.assertEqual(company.branches.get().name, PRESET_BRANCH_NAME)
        self.assertEqual(
            company.default_inventory_branch_id, company.branches.get().pk,
        )

    def test_provisioning_is_idempotent_and_creates_no_second_branch(self):
        from .company_provisioning import provision_company_access_defaults

        company = Company.objects.create(name='Otra', slug='otra-prov-2d')
        provision_company_access_defaults(company)
        result = provision_company_access_defaults(company)
        self.assertFalse(result['branch_created'])
        self.assertEqual(company.branches.count(), 1)

    def test_provisioning_never_renames_an_existing_branch(self):
        from .company_provisioning import provision_company_access_defaults

        company = Company.objects.create(name='Con sucursal', slug='con-suc-prov-2d')
        existing = Branch.objects.create(company=company, name='La de siempre')
        result = provision_company_access_defaults(company)
        self.assertFalse(result['branch_created'])
        existing.refresh_from_db()
        self.assertEqual(existing.name, 'La de siempre')


# ---------------------------------------------------------------------------
# SaaS Phase 3 — company configuration and branding
# ---------------------------------------------------------------------------
#
# WHAT THESE TESTS ARE DEFENDING
#
# The failure this phase exists to prevent is not cosmetic. Before it, a second
# tenant's customers would have received a confirmation email and a PDF receipt
# carrying ANOTHER COMPANY'S legal name and tax id, and that company's inbox
# would have received their sales alerts. So the tests below care about three
# things above all:
#
#   1. Nothing falls back to the pilot. An unconfigured tenant shows blanks.
#   2. Documents are frozen. A rename does not rewrite last year's receipts.
#   3. The internal notification goes to the order's own company, or nowhere.

from .company_settings import (  # noqa: E402
    NEUTRAL_THEME,
    build_identity_snapshot,
    build_whatsapp_link,
    company_branding,
    company_configuration_status,
    company_identity,
    order_identity,
    order_notification_recipient,
    order_pickup_location,
)
from .models import CompanySettings  # noqa: E402


def _p3_company(slug, name, **identity):
    """A tenant with its access defaults, exactly as the API creates one."""
    from .company_provisioning import provision_company_access_defaults

    company = Company.objects.create(
        name=name, slug=slug,
        legal_name=identity.pop('legal_name', ''),
        tax_id=identity.pop('tax_id', ''),
    )
    provision_company_access_defaults(company)
    if identity:
        row = company.settings
        for field, value in identity.items():
            setattr(row, field, value)
        row.save()
    company.refresh_from_db()
    return company


def _p3_order(company, *, branch=None, snapshot=True, **extra):
    """A paid order of `company`, with its identity frozen like checkout does."""
    branch = branch or company.default_inventory_branch
    defaults = {
        'customer_name': 'Cliente P3',
        'customer_email': 'cliente-p3@example.invalid',
        'customer_phone': '+51 999 111 222',
        'document_type': Order.DocumentType.DNI,
        'document_number': '12345678',
        'delivery_method': Order.DeliveryMethod.PICKUP_STORE,
        'receipt_type': Order.ReceiptType.BOLETA,
        'total': Decimal('100.00'),
        'status': Order.Status.PAID,
        'paid': True,
        'paid_at': timezone.now(),
        'fulfillment_branch': branch,
    }
    defaults.update(extra)
    if snapshot:
        defaults['company_snapshot'] = build_identity_snapshot(company, branch)
    order = Order.objects.create(company=company, **defaults)
    # Slug unique per company, so it carries the order id: a test that makes two
    # orders for one tenant must not collide on the product.
    product = _seeded(Product.objects.create(
        company=company, name=f'Producto {company.slug} {order.pk}',
        slug=f'producto-{company.slug}-{order.pk}',
        price=Decimal('100.00'), inventory=5,
    ))
    OrderItem.objects.create(
        order=order, product=product, quantity=1, price=Decimal('100.00'),
    )
    return order


class Phase3CompanySettingsModelTest(TestCase):
    """The model, its validators and the one-to-one relationship."""

    def setUp(self):
        cache.clear()
        self.company = _p3_company('p3-model', 'Empresa Modelo')

    def test_settings_are_one_to_one_with_a_company(self):
        from django.db import IntegrityError, transaction

        self.assertIsNotNone(self.company.settings)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                CompanySettings.objects.create(company=self.company)

    def test_a_valid_hex_colour_is_accepted(self):
        row = self.company.settings
        row.primary_color = '#1A2B3C'
        row.save()
        row.refresh_from_db()
        self.assertEqual(row.primary_color, '#1A2B3C')

    def test_css_injection_through_a_colour_is_rejected(self):
        """
        The reason the format is six hex digits and nothing else.

        These values are interpolated into a CSS custom property and a style
        attribute. Anything that can express a function call, a scheme or a
        closing brace is a CSS injection with a colour picker in front of it.
        """
        from django.core.exceptions import ValidationError

        row = self.company.settings
        for attack in (
            'url(https://evil.invalid/x.png)',
            'var(--x)',
            'javascript:alert(1)',
            '#fff;} body{display:none',
            'red',
            '#GGGGGG',
            '#FFF',
        ):
            row.primary_color = attack
            with self.assertRaises(ValidationError, msg=attack):
                row.save()

    def test_an_invalid_timezone_is_rejected(self):
        from django.core.exceptions import ValidationError

        row = self.company.settings
        row.timezone = 'Mars/Olympus'
        with self.assertRaises(ValidationError):
            row.save()

    def test_a_valid_iana_timezone_is_accepted(self):
        row = self.company.settings
        row.timezone = 'America/Lima'
        row.save()
        row.refresh_from_db()
        self.assertEqual(row.timezone, 'America/Lima')

    def test_a_whatsapp_number_must_be_digits(self):
        """
        Stored as digits, never as a finished URL.

        The link is BUILT from it, so the database can never hold something that
        renders as an anchor with an arbitrary scheme in a customer's inbox.
        """
        from django.core.exceptions import ValidationError

        row = self.company.settings
        for attack in (
            'https://evil.invalid',
            'javascript:alert(1)',
            '+51 987 654 321',
            '123',
        ):
            row.whatsapp_number = attack
            with self.assertRaises(ValidationError, msg=attack):
                row.save()

    def test_the_whatsapp_link_is_derived_not_stored(self):
        self.assertEqual(build_whatsapp_link('51987654321'), 'https://wa.me/51987654321')
        self.assertEqual(build_whatsapp_link('javascript:alert(1)'), '')
        self.assertEqual(build_whatsapp_link(''), '')
        self.assertEqual(build_whatsapp_link('123'), '')


class Phase3IdentityFallbackTest(TestCase):
    """
    THE RULE: fall back to empty, never to another company.

    An incomplete tenant renders blanks. Blanks are a visible, fixable state;
    another business's legal identity on a document is neither.
    """

    def setUp(self):
        cache.clear()
        self.bare = _p3_company('p3-bare', 'Empresa Vacía')

    def test_an_unconfigured_company_has_empty_commercial_fields(self):
        identity = company_identity(self.bare)
        self.assertEqual(identity.name, 'Empresa Vacía')
        self.assertEqual(identity.legal_address, '')
        self.assertEqual(identity.tax_id, '')
        self.assertEqual(identity.phone, '')
        self.assertEqual(identity.warranty_policy_text, '')

    def test_no_pilot_values_leak_into_another_tenant(self):
        """The single most important assertion in this phase."""
        identity = company_identity(self.bare)
        blob = ' '.join(str(v) for v in identity.as_dict().values()).lower()
        for pilot_value in (
            'black dog', 'cmau', '20610159886', 'octavio', '936 449', '51936449536',
        ):
            self.assertNotIn(pilot_value, blob)

    def test_a_company_with_no_settings_row_still_resolves(self):
        """Settings can be missing on a tenant created before Phase 3."""
        CompanySettings.objects.filter(company=self.bare).delete()
        self.bare.refresh_from_db()
        identity = company_identity(self.bare)
        self.assertEqual(identity.name, 'Empresa Vacía')
        self.assertEqual(identity.phone, '')

    def test_branding_falls_back_to_the_neutral_theme_per_field(self):
        """
        PER FIELD, not all-or-nothing: setting one colour must not lose the rest.
        """
        row = self.bare.settings
        row.background_color = '#123456'
        row.primary_color = ''
        row.save()
        self.bare.refresh_from_db()

        branding = company_branding(self.bare)
        self.assertEqual(branding.colors['background_color'], '#123456')
        self.assertEqual(branding.colors['primary_color'], NEUTRAL_THEME['primary_color'])

    def test_the_neutral_theme_belongs_to_no_business(self):
        branding = company_branding(self.bare)
        self.assertEqual(branding.colors, NEUTRAL_THEME)
        self.assertEqual(branding.logo_url, '')

    def test_css_variable_names_are_fixed(self):
        branding = company_branding(self.bare)
        self.assertEqual(
            set(branding.css_variables()),
            {'--brand-primary', '--brand-accent', '--brand-background',
             '--brand-surface', '--brand-text', '--brand-border'},
        )


class Phase3IdentitySnapshotTest(TestCase):
    """
    Documents are frozen at sale time. This is the §59 test.

    A business that is renamed or re-registers must not silently rewrite what a
    receipt from six months ago says it was — a customer holding a printed copy
    would find it no longer matches the one the system reprints.
    """

    def setUp(self):
        cache.clear()
        self.company = _p3_company(
            'p3-snap', 'Empresa A', legal_name='Empresa A S.A.C.', tax_id='111',
            legal_address='Calle Uno 100', phone='+51 111 111 111',
        )

    def test_an_order_freezes_the_identity_of_its_moment(self):
        old_order = _p3_order(self.company)

        self.company.name = 'Empresa Nueva'
        self.company.legal_name = 'Empresa Nueva S.A.C.'
        self.company.tax_id = '222'
        self.company.save()
        row = self.company.settings
        row.legal_address = 'Avenida Dos 200'
        row.save()
        self.company.refresh_from_db()

        old_identity = order_identity(old_order)
        self.assertEqual(old_identity.tax_id, '111', 'el documento histórico no muta')
        self.assertEqual(old_identity.name, 'Empresa A')
        self.assertEqual(old_identity.legal_address, 'Calle Uno 100')

        new_order = _p3_order(self.company)
        new_identity = order_identity(new_order)
        self.assertEqual(new_identity.tax_id, '222')
        self.assertEqual(new_identity.name, 'Empresa Nueva')

    def test_the_pdf_of_an_old_order_shows_the_old_identity(self):
        """The same rule, through the actual document builder."""
        from store.pdf_services import build_order_pdf_context

        order = _p3_order(self.company)
        self.company.tax_id = '999'
        self.company.save()

        ctx = build_order_pdf_context(order)
        self.assertEqual(ctx['store_ruc'], '111')

    def test_an_order_without_a_snapshot_falls_back_to_the_live_company(self):
        """
        The documented limitation, for orders that predate Phase 3.

        It is the best answer available for them, and it is the only case where a
        document's identity can change under it.
        """
        order = _p3_order(self.company, snapshot=False)
        self.assertEqual(order.company_snapshot, {})
        self.assertEqual(order_identity(order).tax_id, '111')

        self.company.tax_id = '333'
        self.company.save()
        order.refresh_from_db()
        self.assertEqual(order_identity(order).tax_id, '333')

    def test_the_snapshot_carries_no_secrets_or_internal_routing(self):
        row = self.company.settings
        row.order_notification_email = 'interno@example.invalid'
        row.save()
        self.company.refresh_from_db()

        snapshot = build_identity_snapshot(self.company)
        blob = str(snapshot).lower()
        for forbidden in ('interno@example.invalid', 'notification', 'password',
                          'token', 'secret', 'stripe'):
            self.assertNotIn(forbidden, blob)

    def test_the_pickup_point_is_the_branch_not_the_legal_address(self):
        """
        One is who invoices, the other is which door to knock on.

        Printing the office address under "punto de retiro" sends people to the
        wrong place.
        """
        branch = Branch.objects.create(
            company=self.company, name='Tienda Centro',
            address='Jirón Retiro 500', phone='+51 222 222 222',
        )
        order = _p3_order(self.company, branch=branch)
        pickup = order_pickup_location(order)
        self.assertEqual(pickup['source'], 'branch')
        self.assertEqual(pickup['address'], 'Jirón Retiro 500')
        self.assertNotEqual(pickup['address'], self.company.settings.legal_address)


class Phase3CrossTenantDocumentTest(TestCase):
    """
    Company A's documents say A. Company B's say B. Neither ever says the other.

    This is the failure Phase 3 exists to prevent, exercised end to end through
    the real email and PDF builders rather than through the helpers.
    """

    def setUp(self):
        cache.clear()
        # Tax ids chosen so they cannot appear inside any other value in the
        # document (a bare "222" collides with a phone number).
        self.a = _p3_company(
            'p3-doc-a', 'Empresa A', legal_name='A S.A.C.', tax_id='20111111111',
            legal_address='Calle A 1', phone='+51 111 111 111',
            contact_email='hola@a.invalid', whatsapp_number='51111111111',
            warranty_policy_text='Garantía A: 12 meses.',
            order_notification_email='ventas@a.invalid',
        )
        self.b = _p3_company(
            'p3-doc-b', 'Empresa B', legal_name='B S.A.C.', tax_id='20222222222',
            legal_address='Calle B 2', phone='+51 222 222 222',
            contact_email='hola@b.invalid', whatsapp_number='51222222222',
            warranty_policy_text='Garantía B: 3 meses.',
            order_notification_email='ventas@b.invalid',
        )
        mail.outbox = []

    @override_settings(
        EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
        DEFAULT_FROM_EMAIL='no-reply@test.invalid',
    )
    def test_each_customer_email_carries_its_own_company(self):
        from store.email_services import send_order_confirmation_email

        for company, mine, theirs in (
            (self.a, '20111111111', '20222222222'),
            (self.b, '20222222222', '20111111111'),
        ):
            mail.outbox = []
            order = _p3_order(company)
            send_order_confirmation_email(order)
            msg = mail.outbox[0]
            body = msg.body + msg.subject + str(msg.alternatives)

            self.assertIn(company.name, body)
            self.assertIn(mine, body)
            self.assertNotIn(theirs, body, 'no puede aparecer el RUC de la otra empresa')
            other = self.b if company is self.a else self.a
            self.assertNotIn(other.name, body)

    @override_settings(
        EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
        DEFAULT_FROM_EMAIL='no-reply@test.invalid',
    )
    def test_each_email_carries_its_own_warranty_policy(self):
        from store.email_services import send_order_confirmation_email

        order = _p3_order(self.a)
        send_order_confirmation_email(order)
        body = mail.outbox[0].body
        self.assertIn('Garantía A: 12 meses.', body)
        self.assertNotIn('Garantía B', body)

    @override_settings(
        EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
        DEFAULT_FROM_EMAIL='no-reply@test.invalid',
    )
    def test_the_internal_alert_goes_to_the_orders_own_company(self):
        """
        The §60 test. Before Phase 3 there was one platform-wide recipient, so
        B's sales — customer name, phone, what they bought — would have landed in
        A's inbox.
        """
        from store.email_services import send_internal_order_notification

        for company, expected, forbidden in (
            (self.a, 'ventas@a.invalid', 'ventas@b.invalid'),
            (self.b, 'ventas@b.invalid', 'ventas@a.invalid'),
        ):
            mail.outbox = []
            order = _p3_order(company)
            self.assertTrue(send_internal_order_notification(order))
            self.assertEqual(mail.outbox[0].to, [expected])
            self.assertNotIn(forbidden, str(mail.outbox[0].to))

    @override_settings(
        EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
        ORDER_NOTIFICATION_EMAIL='plataforma@example.invalid',
        DEFAULT_FROM_EMAIL='no-reply@test.invalid',
    )
    def test_there_is_no_platform_fallback_recipient(self):
        """
        A company with no address gets NO alert — it does not fall through to the
        global setting, which holds one address belonging to somebody else.

        Silence is recoverable; a misdirected alert is not, because the data has
        already left.
        """
        from store.email_services import send_internal_order_notification

        row = self.a.settings
        row.order_notification_email = ''
        row.save()
        self.a.refresh_from_db()

        order = _p3_order(self.a)
        self.assertFalse(send_internal_order_notification(order))
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(order_notification_recipient(order), '')

    def test_each_pdf_carries_its_own_company(self):
        from store.pdf_services import build_order_pdf_context, generate_order_receipt_pdf

        for company, mine, theirs in (
            (self.a, '20111111111', '20222222222'),
            (self.b, '20222222222', '20111111111'),
        ):
            order = _p3_order(company)
            ctx = build_order_pdf_context(order)
            self.assertEqual(ctx['store_name'], company.name)
            self.assertEqual(ctx['store_ruc'], mine)
            self.assertNotEqual(ctx['store_ruc'], theirs)
            # And it renders.
            self.assertTrue(generate_order_receipt_pdf(order).startswith(b'%PDF'))

    def test_the_sales_note_carries_its_own_company(self):
        from store.sales_note_services import (
            build_sales_note_context, get_or_create_sales_note,
        )

        order = _p3_order(self.b)
        note, _created = get_or_create_sales_note(order)
        ctx = build_sales_note_context(note)
        self.assertEqual(ctx['store_name'], 'Empresa B')
        self.assertEqual(ctx['store_ruc'], '20222222222')
        self.assertIn('Garantía B', ctx['warranty_note'])

    def test_the_sales_note_keeps_its_internal_document_disclaimer(self):
        """Tenant branding must not turn an internal note into a fiscal receipt."""
        from store.sales_note_services import (
            SALES_NOTE_DISCLAIMER, build_sales_note_context, get_or_create_sales_note,
        )

        order = _p3_order(self.a)
        note, _created = get_or_create_sales_note(order)
        ctx = build_sales_note_context(note)
        self.assertEqual(ctx['disclaimer'], SALES_NOTE_DISCLAIMER)
        self.assertIn('No válido como comprobante electrónico SUNAT', ctx['disclaimer'])

    def test_filenames_use_the_slug_and_stay_safe(self):
        from store.pdf_services import get_order_receipt_filename
        from store.sales_note_services import (
            get_or_create_sales_note, get_sales_note_filename,
        )

        order = _p3_order(self.a)
        name = get_order_receipt_filename(order)
        self.assertTrue(name.isascii())
        self.assertNotIn('/', name)
        self.assertNotIn('..', name)
        self.assertIn('p3-doc-a', name)

        note, _created = get_or_create_sales_note(order)
        note_name = get_sales_note_filename(note)
        self.assertTrue(note_name.isascii())
        self.assertNotIn('/', note_name)

    def test_a_hostile_company_name_cannot_escape_a_filename(self):
        """
        The NAME is free text a tenant types; the SLUG is not.

        This is why the filename is built from the slug and filtered again: a
        name is the kind of value that reaches a Content-Disposition header and a
        filesystem.
        """
        from store.pdf_services import get_order_receipt_filename

        self.a.name = '../../etc/passwd "; rm -rf /'
        self.a.save()
        order = _p3_order(self.a)
        name = get_order_receipt_filename(order)
        self.assertNotIn('..', name)
        self.assertNotIn('/', name)
        self.assertNotIn('"', name)
        self.assertTrue(name.isascii())

    @override_settings(
        EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
        DEFAULT_FROM_EMAIL='no-reply@test.invalid',
    )
    def test_a_hostile_company_name_is_escaped_in_the_html_email(self):
        """
        Company identity became TENANT INPUT in Phase 3.

        Before, those values were module constants and were interpolated raw. Now
        one company types them into a form and they render inside another
        person's email client, so every one of them has to be escaped.
        """
        from store.email_services import send_order_confirmation_email

        self.a.name = '<script>alert(1)</script>'
        self.a.legal_name = '<img src=x onerror=alert(2)>'
        self.a.save()
        row = self.a.settings
        row.legal_address = '<b>Calle</b>'
        row.warranty_policy_text = '<iframe src="evil"></iframe>'
        row.save()
        self.a.refresh_from_db()

        order = _p3_order(self.a)
        send_order_confirmation_email(order)
        html = mail.outbox[0].alternatives[0][0]

        # What matters is that no TAG is ever constructed. `onerror=` surviving
        # as inert text inside a <p> is harmless; `<img ... onerror=` is not.
        self.assertNotIn('<script', html)
        self.assertNotIn('<img src=x', html)
        self.assertNotIn('<iframe', html)
        self.assertNotIn('<b>Calle</b>', html)
        # ...and that it survives as escaped, visible text rather than vanishing.
        self.assertIn('&lt;script&gt;', html)
        self.assertIn('&lt;img src=x onerror=alert(2)&gt;', html)
        self.assertIn('&lt;iframe', html)


class Phase3AuthEmailTest(TestCase):
    """
    Account-security emails are PLATFORM emails, not tenant emails.

    A User is global — one identity across every shop they buy from — so an email
    about that account is from the platform. Branding it as a tenant would mean a
    customer of three shops receiving a password reset from a business they never
    asked about.
    """

    def setUp(self):
        cache.clear()
        mail.outbox = []
        self.user = User.objects.create_user(
            username='p3authuser', email='p3auth@example.invalid', password='Pass123!',
        )

    @override_settings(
        EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
        PLATFORM_NAME='Mi Plataforma',
        FRONTEND_URL='http://localhost:3000',
    )
    def test_auth_emails_carry_the_platform_name(self):
        from store.emails import send_password_reset_email, send_verification_email

        send_verification_email(self.user, 'tok')
        send_password_reset_email(self.user, 'tok')
        self.assertEqual(len(mail.outbox), 2)
        for msg in mail.outbox:
            self.assertIn('Mi Plataforma', msg.subject + msg.body)

    @override_settings(
        EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
        PLATFORM_NAME='',
        FRONTEND_URL='http://localhost:3000',
    )
    def test_without_a_platform_name_they_carry_no_brand_at_all(self):
        """
        The honest default. The platform has no more claim to a compiled-in name
        than a tenant does, and shipping one would put this installation's brand
        on every fork.
        """
        from store.emails import send_verification_email

        send_verification_email(self.user, 'tok')
        blob = mail.outbox[0].subject + mail.outbox[0].body
        self.assertNotIn('—', blob.split('\n')[-1] if blob else '')
        for pilot_value in ('black dog', 'cmau'):
            self.assertNotIn(pilot_value, blob.lower())
        self.assertIn('Verifica tu cuenta', mail.outbox[0].subject)


class Phase3NoHardcodeTest(TestCase):
    """
    A STRUCTURAL guard: the commercial services must stay tenant-neutral.

    Phase 3 deleted six module constants from three files. Nothing stops somebody
    adding them back next month while fixing something unrelated — and the damage
    would be silent, because the pilot's own documents would look correct. This
    scans those files and fails if one specific business's identity reappears.

    Migrations, tests and documentation are deliberately NOT scanned: a migration
    records what was true when it ran, and that is exactly where these values
    belong now.
    """

    _COMMERCIAL_SERVICES = (
        'email_services.py',
        'pdf_services.py',
        'sales_note_services.py',
        'company_settings.py',
        # Included because its validation messages are customer-facing: the
        # checkout phone-format error used to illustrate itself with the pilot
        # company's real number, shown to every tenant's buyers.
        'serializers.py',
    )

    # Values that identify ONE business. Any of them in a runtime service means
    # somebody else's customers are about to receive them.
    _PILOT_MARKERS = (
        'black dog',
        'cmau corp',
        '20610159886',
        'octavio muñoz',
        '936 449 536',
        '51936449536',
    )

    def _source(self, filename):
        import pathlib

        path = pathlib.Path(__file__).resolve().parent / filename
        return path.read_text(encoding='utf-8')

    def test_commercial_services_name_no_specific_business(self):
        for filename in self._COMMERCIAL_SERVICES:
            source = self._source(filename).lower()
            for marker in self._PILOT_MARKERS:
                self.assertNotIn(
                    marker, source,
                    f'{filename} contiene "{marker}": la identidad de una empresa '
                    f'volvió al runtime. Debe vivir en CompanySettings.',
                )

    def test_the_store_constants_are_gone(self):
        """The specific shape the old hardcodes had."""
        for filename in ('email_services.py', 'pdf_services.py', 'sales_note_services.py'):
            source = self._source(filename)
            for constant in (
                '_STORE_NAME', '_STORE_RUC', '_STORE_ADDRESS',
                '_STORE_PHONE', '_STORE_LEGAL_NAME', '_STORE_CITY',
                '_STORE_WHATSAPP_LINK',
            ):
                self.assertNotIn(
                    constant, source,
                    f'{filename} volvió a declarar {constant}.',
                )

    def test_the_migration_neutral_theme_matches_the_runtime_one(self):
        """
        Migration 0028 carries its own copy of the neutral palette, as a
        migration must. This asserts the two agree TODAY, so a change to one is
        a deliberate divergence rather than an accident.
        """
        import importlib

        module = importlib.import_module(
            'store.migrations.0028_backfill_company_settings',
        )
        self.assertEqual(module._NEUTRAL_THEME, NEUTRAL_THEME)


class Phase3StorefrontConfigTest(TestCase):
    """
    GET /api/storefront/config/ — public, tenant resolved from the HOST.

    Anonymous by design: it is what the shop's own visitors need to render the
    page. The tenant therefore cannot come from a parameter — a public request
    has no identity to validate one against.
    """

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.a = _p3_company(
            'p3-store-a', 'Tienda A', legal_name='A S.A.C.', tax_id='20111111111',
            phone='+51 111 111 111', whatsapp_number='51111111111',
            legal_address='Calle A 1', contact_email='hola@a.invalid',
            warranty_policy_text='Garantía A.',
            order_notification_email='interno-a@example.invalid',
            primary_color='#AABBCC',
        )
        self.b = _p3_company(
            'p3-store-b', 'Tienda B', legal_name='B S.A.C.', tax_id='20222222222',
            phone='+51 222 222 222', legal_address='Calle B 2',
            order_notification_email='interno-b@example.invalid',
        )

    def _config(self, company):
        with _storefront_of(company):
            return self.client.get('/api/storefront/config/')

    def test_each_storefront_returns_its_own_tenant(self):
        res = self._config(self.a)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['company']['name'], 'Tienda A')
        self.assertEqual(res.data['company']['tax_id'], '20111111111')

        res_b = self._config(self.b)
        self.assertEqual(res_b.data['company']['name'], 'Tienda B')
        self.assertEqual(res_b.data['company']['tax_id'], '20222222222')

    def test_no_data_of_another_tenant_appears(self):
        blob = str(self._config(self.a).data)
        self.assertNotIn('Tienda B', blob)
        self.assertNotIn('20222222222', blob)
        self.assertNotIn('Calle B 2', blob)

    def test_the_internal_notification_email_is_never_public(self):
        """
        The field this response shape exists to keep out.

        It is where a tenant's sales alerts go; publishing it would hand every
        visitor an operations inbox to aim at. The serializer is hand-built for
        exactly this reason — a ModelSerializer with `exclude` would leak every
        field added after somebody forgot to update it.
        """
        for company in (self.a, self.b):
            blob = str(self._config(company).data)
            self.assertNotIn('interno-a@example.invalid', blob)
            self.assertNotIn('interno-b@example.invalid', blob)
            self.assertNotIn('order_notification_email', blob)

    def test_no_internal_or_membership_data_is_exposed(self):
        blob = str(self._config(self.a).data).lower()
        for forbidden in (
            'membership', 'role', 'capabilit', 'audit', 'stripe', 'secret',
            'password', 'token', 'is_active',
        ):
            self.assertNotIn(forbidden, blob, f'la respuesta pública expone "{forbidden}"')

    def test_the_response_carries_validated_css_variables(self):
        import re

        variables = self._config(self.a).data['branding']['css_variables']
        self.assertEqual(variables['--brand-primary'], '#AABBCC')
        for name, value in variables.items():
            self.assertTrue(name.startswith('--brand-'))
            self.assertRegex(value, re.compile(r'^#[0-9A-Fa-f]{6}$'))

    def test_an_unbranded_tenant_gets_the_neutral_theme_not_another_shops(self):
        colors = self._config(self.b).data['branding']['colors']
        self.assertEqual(colors, NEUTRAL_THEME)
        self.assertNotEqual(colors['primary_color'], '#AABBCC')

    def test_a_deactivated_company_serves_no_storefront(self):
        self.a.is_active = False
        self.a.save(update_fields=['is_active'])
        self.assertEqual(self._config(self.a).status_code, status.HTTP_404_NOT_FOUND)

    def test_an_unresolvable_host_fails_safely(self):
        """
        404, not a default tenant. Serving somebody's branding under the wrong
        domain is exactly what the storefront resolver exists to prevent.
        """
        # A slug that matches no company, with several active tenants in the
        # database so the single-company fallback correctly does not fire either.
        with override_settings(DEFAULT_STOREFRONT_COMPANY_SLUG='no-existe'):
            res = self.client.get('/api/storefront/config/')
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_the_response_varies_on_host(self):
        """
        A shared cache keyed without the host would serve one company's branding
        under another's domain — the same bug as a cache key with no tenant.
        """
        res = self._config(self.a)
        self.assertIn('Host', res['Vary'])

    def test_it_needs_no_authentication(self):
        with _storefront_of(self.a):
            res = APIClient().get('/api/storefront/config/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)


class Phase3SettingsApiTest(TestCase):
    """
    The internal configuration API: who may read it, who may write it, and what
    they physically cannot reach from it.
    """

    def setUp(self):
        cache.clear()
        self.a = _p3_company('p3-api-a', 'Empresa A')
        self.b = _p3_company('p3-api-b', 'Empresa B')

        self.admin_a, _ = _p2d_member(
            self.a, 'p3_admin_a', ['company.view', 'company.manage'],
        )
        self.viewer_a, _ = _p2d_member(self.a, 'p3_viewer_a', ['company.view'])
        self.admin_b, _ = _p2d_member(
            self.b, 'p3_admin_b', ['company.view', 'company.manage'],
        )
        self.outsider = User.objects.create_user(username='p3_outsider', password='x')

    def _as(self, user):
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    def test_an_admin_reads_their_own_settings(self):
        res = self._as(self.admin_a).get('/api/admin/company-settings/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['company']['name'], 'Empresa A')
        self.assertTrue(res.data['can_manage'])

    def test_a_viewer_reads_but_cannot_write(self):
        client = self._as(self.viewer_a)
        read = client.get('/api/admin/company-settings/')
        self.assertEqual(read.status_code, status.HTTP_200_OK)
        self.assertFalse(read.data['can_manage'])

        write = client.patch(
            '/api/admin/company-settings/', {'phone': '+51 000'}, format='json',
        )
        self.assertEqual(write.status_code, status.HTTP_403_FORBIDDEN)

    def test_an_admin_cannot_read_another_companys_settings(self):
        res = self._as(self.admin_a).get(
            f'/api/admin/company-settings/?company={self.b.pk}',
        )
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_an_admin_cannot_write_another_companys_settings(self):
        res = self._as(self.admin_a).patch(
            f'/api/admin/company-settings/?company={self.b.pk}',
            {'phone': '+51 999'}, format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
        self.b.refresh_from_db()
        self.assertEqual(self.b.settings.phone, '')

    def test_a_user_with_no_membership_is_refused(self):
        res = self._as(self.outsider).get('/api/admin/company-settings/')
        self.assertIn(
            res.status_code,
            (status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND),
        )

    def test_authority_follows_the_company_for_a_multi_company_user(self):
        """
        §46: admin in A, view-only in B. Switching tenant switches authority —
        permissions are not inherited across the switch.
        """
        from .models import Membership

        membership = Membership.objects.create(
            user=self.admin_a, company=self.b, role=UserProfile.ROLE_CUSTOMER,
        )
        _assign(membership, _role(
            self.b, 'Solo lectura B', ['company.view'], slug='solo-lectura-b',
        ))

        client = self._as(self.admin_a)
        in_a = client.get(f'/api/admin/company-settings/?company={self.a.pk}')
        self.assertTrue(in_a.data['can_manage'])

        in_b = client.get(f'/api/admin/company-settings/?company={self.b.pk}')
        self.assertEqual(in_b.status_code, status.HTTP_200_OK)
        self.assertFalse(in_b.data['can_manage'], 'la autoridad no se hereda')

        write_b = client.patch(
            f'/api/admin/company-settings/?company={self.b.pk}',
            {'phone': '+51 000'}, format='json',
        )
        self.assertEqual(write_b.status_code, status.HTTP_403_FORBIDDEN)

    def test_a_platform_master_must_name_the_company(self):
        master = User.objects.create_user(username='p3_master', password='x')
        master.is_superuser = True
        master.save()
        client = self._as(master)

        self.assertEqual(
            client.get('/api/admin/company-settings/').status_code,
            status.HTTP_403_FORBIDDEN,
        )
        named = client.get(f'/api/admin/company-settings/?company={self.b.pk}')
        self.assertEqual(named.status_code, status.HTTP_200_OK)
        self.assertEqual(named.data['company']['name'], 'Empresa B')

    def test_an_admin_saves_identity_and_settings_in_one_request(self):
        res = self._as(self.admin_a).patch('/api/admin/company-settings/', {
            'name': 'Empresa A Renombrada',
            'legal_name': 'A S.A.C.',
            'tax_id': '20111111111',
            'phone': '+51 987 654 321',
            'primary_color': '#123456',
            'warranty_policy_text': 'Doce meses.',
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.a.refresh_from_db()
        self.assertEqual(self.a.name, 'Empresa A Renombrada')
        self.assertEqual(self.a.tax_id, '20111111111')
        self.assertEqual(self.a.settings.primary_color, '#123456')

    def test_slug_and_is_active_are_unreachable_from_this_endpoint(self):
        """
        Not "excluded by a serializer" — absent from both writers, so no payload
        can reach them. They are routing and platform decisions.
        """
        before_slug, before_active = self.a.slug, self.a.is_active
        res = self._as(self.admin_a).patch('/api/admin/company-settings/', {
            'slug': 'secuestrada', 'is_active': False, 'company': self.b.pk,
            'phone': '+51 111',
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.a.refresh_from_db()
        self.assertEqual(self.a.slug, before_slug)
        self.assertEqual(self.a.is_active, before_active)
        self.assertEqual(self.a.settings.company_id, self.a.pk)

    def test_currency_is_read_only(self):
        """
        Stored, but not editable: checkout charges through Stripe in one
        platform-level currency. A dropdown offering USD while Stripe billed PEN
        would be a lie with a UI on it.
        """
        res = self._as(self.admin_a).patch(
            '/api/admin/company-settings/', {'currency': 'USD'}, format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.a.refresh_from_db()
        self.assertEqual(self.a.settings.currency, 'PEN')

    def test_an_invalid_colour_is_rejected_with_a_field_error(self):
        res = self._as(self.admin_a).patch(
            '/api/admin/company-settings/',
            {'primary_color': 'url(https://evil.invalid)'}, format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('primary_color', res.data)

    def test_html_in_the_warranty_policy_is_rejected(self):
        """
        This string is rendered in customer emails and PDFs. Accepting markup
        would turn one tenant's settings form into an HTML-injection vector
        aimed at other people's inboxes.
        """
        res = self._as(self.admin_a).patch(
            '/api/admin/company-settings/',
            {'warranty_policy_text': '<script>alert(1)</script>'}, format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('warranty_policy_text', res.data)

    def test_invalid_urls_are_rejected(self):
        for field in ('website_url', 'logo_url', 'terms_url'):
            res = self._as(self.admin_a).patch(
                '/api/admin/company-settings/',
                {field: 'javascript:alert(1)'}, format='json',
            )
            self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST, field)
            self.assertIn(field, res.data)

    def test_an_invalid_whatsapp_number_is_rejected(self):
        res = self._as(self.admin_a).patch(
            '/api/admin/company-settings/',
            {'whatsapp_number': 'https://evil.invalid'}, format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('whatsapp_number', res.data)

    def test_a_rejected_field_leaves_nothing_written(self):
        """
        Everything validates before anything is written, so a bad colour cannot
        leave the company name already changed.
        """
        original = self.a.name
        res = self._as(self.admin_a).patch('/api/admin/company-settings/', {
            'name': 'No debería guardarse', 'primary_color': 'rojo',
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.a.refresh_from_db()
        self.assertEqual(self.a.name, original)

    def test_an_update_is_audited_with_field_names_only(self):
        """
        Who changed what and when. NOT the values: copying a policy or an
        address into every audit row would put the same data in a second place
        for no gain.
        """
        self._as(self.admin_a).patch('/api/admin/company-settings/', {
            'phone': '+51 987 654 321',
            'warranty_policy_text': 'Doce meses de garantía.',
        }, format='json')

        log = AdminAuditLog.objects.filter(action='company_settings_updated').first()
        self.assertIsNotNone(log)
        self.assertEqual(log.company_id, self.a.pk)
        self.assertIn('phone', log.metadata['changed_fields'])
        self.assertIn('warranty_policy_text', log.metadata['changed_fields'])
        blob = str(log.metadata)
        self.assertNotIn('Doce meses de garantía.', blob, 'no se guardan valores')
        self.assertNotIn('987 654 321', blob)

    def test_a_logo_change_records_the_event_not_the_image(self):
        self._as(self.admin_a).patch(
            '/api/admin/company-settings/',
            {'logo_url': 'https://cdn.example.invalid/logo.png'}, format='json',
        )
        log = AdminAuditLog.objects.filter(action='company_settings_updated').first()
        self.assertIn('logo_url', log.metadata['changed_fields'])
        self.assertNotIn('cdn.example.invalid', str(log.metadata))


class Phase3ProvisioningTest(TestCase):
    """A new tenant arrives configurable, and configured by nobody else."""

    def test_a_new_company_gets_its_settings(self):
        from .company_provisioning import provision_company_access_defaults

        company = Company.objects.create(name='Nueva P3', slug='nueva-p3')
        result = provision_company_access_defaults(company)
        self.assertTrue(result['settings_created'])
        company.refresh_from_db()
        self.assertIsNotNone(company.settings)

    def test_provisioning_is_idempotent(self):
        from .company_provisioning import provision_company_access_defaults

        company = Company.objects.create(name='Otra P3', slug='otra-p3')
        provision_company_access_defaults(company)
        company.refresh_from_db()
        company.settings.phone = '+51 555 555 555'
        company.settings.save()

        result = provision_company_access_defaults(company)
        self.assertFalse(result['settings_created'])
        company.refresh_from_db()
        self.assertEqual(company.settings.phone, '+51 555 555 555', 'no se sobreescribe')

    def test_a_new_company_inherits_nothing_from_another_tenant(self):
        """
        A new business starts BLANK, not wearing somebody else's name, address
        and colours until it notices.
        """
        from .company_provisioning import provision_company_access_defaults

        company = Company.objects.create(name='Limpia P3', slug='limpia-p3')
        provision_company_access_defaults(company)
        company.refresh_from_db()

        row = company.settings
        for field in (
            'contact_email', 'phone', 'whatsapp_number', 'website_url',
            'legal_address', 'city', 'logo_url', 'warranty_policy_text',
            'order_notification_email', 'facebook_url', 'instagram_url',
        ):
            self.assertEqual(getattr(row, field), '', field)

        self.assertEqual(company_branding(company).colors, NEUTRAL_THEME)

    def test_a_company_created_through_the_api_gets_settings(self):
        master = User.objects.create_user(username='p3_prov_master', password='x')
        master.is_superuser = True
        master.save()
        client = APIClient()
        client.force_authenticate(user=master)

        res = client.post('/api/admin/companies/', {
            'name': 'Vía API P3', 'slug': 'via-api-p3', 'tax_id': '20999999999',
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        created = Company.objects.get(slug='via-api-p3')
        self.assertIsNotNone(getattr(created, 'settings', None))

    def test_configuration_status_reports_gaps_without_blocking(self):
        company = _p3_company('p3-status', 'Incompleta')
        report = company_configuration_status(company)
        self.assertFalse(report['is_complete'])
        self.assertGreater(report['missing_count'], 0)
        self.assertIn('order_notification_email', report['consequential'])

    def test_a_fully_configured_company_reports_complete(self):
        company = _p3_company(
            'p3-complete', 'Completa', legal_name='C S.A.C.', tax_id='20333333333',
            legal_address='Calle C 3', phone='+51 333 333 333',
            contact_email='hola@c.invalid', logo_url='https://cdn.invalid/l.png',
            warranty_policy_text='Garantía C.',
            order_notification_email='ventas@c.invalid',
        )
        report = company_configuration_status(company)
        self.assertTrue(report['is_complete'], report['missing'])


class Phase3BranchConfigurationTest(TestCase):
    """
    Branch CRUD and the fulfillment branch — closing the Phase 2D UI debt.

    §53: changing which branch the storefront ships from must NOT rewrite orders
    already placed. They carry their own `fulfillment_branch`, decided when they
    were sold.
    """

    def setUp(self):
        cache.clear()
        self.a = _p3_company('p3-br-a', 'Empresa A')
        self.b = _p3_company('p3-br-b', 'Empresa B')
        self.a2 = Branch.objects.create(company=self.a, name='Segunda A')
        self.foreign = Branch.objects.get(company=self.b)

        self.admin, _ = _p2d_member(
            self.a, 'p3_br_admin', ['company.view', 'company.manage'],
        )
        self.viewer, _ = _p2d_member(self.a, 'p3_br_viewer', ['company.view'])
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)

    def test_an_admin_edits_their_own_branch(self):
        branch = Branch.objects.get(company=self.a, name='Sucursal principal')
        res = self.client.patch(f'/api/admin/branches/{branch.pk}/', {
            'name': 'Tienda Centro', 'address': 'Jirón Nuevo 123',
            'phone': '+51 111 111 111',
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        branch.refresh_from_db()
        self.assertEqual(branch.name, 'Tienda Centro')
        self.assertEqual(branch.address, 'Jirón Nuevo 123')

    def test_a_branch_cannot_be_moved_to_another_company(self):
        """
        `company` is never read from the payload, so this is not prevented by a
        check that could be forgotten — the field simply is not written.
        """
        res = self.client.patch(f'/api/admin/branches/{self.a2.pk}/', {
            'company': self.b.pk, 'name': 'Intento',
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.a2.refresh_from_db()
        self.assertEqual(self.a2.company_id, self.a.pk)

    def test_another_companys_branch_is_invisible(self):
        res = self.client.patch(
            f'/api/admin/branches/{self.foreign.pk}/', {'name': 'Robada'},
            format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
        self.foreign.refresh_from_db()
        self.assertNotEqual(self.foreign.name, 'Robada')

    def test_a_viewer_cannot_edit_a_branch(self):
        client = APIClient()
        client.force_authenticate(user=self.viewer)
        res = client.patch(
            f'/api/admin/branches/{self.a2.pk}/', {'name': 'No'}, format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_deactivating_the_fulfillment_branch_clears_the_pointer(self):
        """
        Rather than leaving a dangling one, which would make checkout refuse
        every order with no explanation on the settings screen.
        """
        branch = self.a.default_inventory_branch
        self.assertIsNotNone(branch)
        res = self.client.patch(
            f'/api/admin/branches/{branch.pk}/', {'is_active': False}, format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.a.refresh_from_db()
        self.assertIsNone(self.a.default_inventory_branch_id)

    def test_deactivating_a_branch_does_not_touch_its_stock(self):
        """A closed shop still holds its units; moving them is a transfer."""
        product = _p2d_product(self.a, 'Producto BR', 'producto-br-p3')
        _p2d_stock(self.a2, product, 7)
        self.client.patch(
            f'/api/admin/branches/{self.a2.pk}/', {'is_active': False}, format='json',
        )
        self.assertEqual(branch_quantity(self.a2, product), 7)

    def test_changing_the_fulfillment_branch_does_not_rewrite_old_orders(self):
        """The §53 test."""
        original = self.a.default_inventory_branch
        order = _p3_order(self.a, branch=original)
        self.assertEqual(order.fulfillment_branch_id, original.pk)

        res = self.client.patch(
            f'/api/admin/companies/{self.a.pk}/fulfillment-branch/',
            {'branch': self.a2.pk}, format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.a.refresh_from_db()
        self.assertEqual(self.a.default_inventory_branch_id, self.a2.pk)

        order.refresh_from_db()
        self.assertEqual(
            order.fulfillment_branch_id, original.pk,
            'un pedido existente conserva la sucursal con la que se vendió',
        )

    def test_a_branch_edit_is_audited(self):
        self.client.patch(
            f'/api/admin/branches/{self.a2.pk}/', {'address': 'Calle Nueva 9'},
            format='json',
        )
        log = AdminAuditLog.objects.filter(action='branch_updated').first()
        self.assertIsNotNone(log)
        self.assertEqual(log.company_id, self.a.pk)
        self.assertIn('address', log.metadata['changed_fields'])


class Phase3DashboardConfigurationTest(TestCase):
    """The dashboard reports configuration gaps, gated and tenant-safe."""

    def setUp(self):
        cache.clear()
        self.company = _p3_company('p3-dash', 'Empresa Dash')
        self.admin, _ = _p2d_member(
            self.company, 'p3_dash_admin', ['company.view', 'company.manage'],
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)

    def test_the_dashboard_reports_configuration_status(self):
        res = self.client.get('/api/me/internal-dashboard/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIsNotNone(res.data['configuration'])
        self.assertFalse(res.data['configuration']['is_complete'])

    def test_an_incomplete_configuration_raises_a_warning_alert(self):
        codes = {a['code'] for a in self.client.get(
            '/api/me/internal-dashboard/').data['alerts']}
        self.assertIn('configuration_incomplete', codes)

    def test_only_consequential_gaps_raise_the_alert(self):
        """
        A missing logo is a gap, not an alarm. Alerting on cosmetic gaps trains
        people to ignore the panel.
        """
        row = self.company.settings
        row.order_notification_email = 'ventas@dash.invalid'
        row.save()
        self.company.refresh_from_db()

        data = self.client.get('/api/me/internal-dashboard/').data
        codes = {a['code'] for a in data['alerts']}
        self.assertNotIn('configuration_incomplete', codes)
        self.assertFalse(data['configuration']['is_complete'], 'sigue habiendo huecos')
        self.assertGreater(data['configuration']['missing_count'], 0)

    def test_configuration_is_withheld_without_company_view(self):
        stripped, _ = _p2d_member(self.company, 'p3_dash_blind', [])
        client = APIClient()
        client.force_authenticate(user=stripped)
        res = client.get('/api/me/internal-dashboard/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIsNone(res.data['configuration'])


class Phase3MigrationRuleTest(TestCase):
    """
    How migration 0028 decides whose identity is whose — tested directly.

    The migration itself cannot run inside a database that is already migrated,
    so its DECISION is exercised here: the pilot is identified by SLUG, and every
    other company gets a neutral row.
    """

    @property
    def _module(self):
        import importlib

        return importlib.import_module(
            'store.migrations.0028_backfill_company_settings',
        )

    def test_the_pilot_is_identified_by_slug_not_by_being_first(self):
        """
        `Company.objects.first()` would claim whichever tenant happened to have
        the lowest id on an installation that had already onboarded others.
        """
        self.assertEqual(self._module._PILOT_SLUG, 'black-dog-store')

    def test_the_pilot_kept_its_identity_as_data(self):
        """
        The values that used to be compiled into the services now live in the
        pilot's own settings row, put there by 0028 during the upgrade.
        """
        pilot = Company.objects.filter(slug='black-dog-store').first()
        self.assertIsNotNone(pilot, 'la empresa piloto debería existir tras 0015')
        identity = company_identity(pilot)
        self.assertEqual(identity.tax_id, '20610159886')
        self.assertEqual(identity.legal_address, 'Octavio Muñoz Najar 238, Tienda 104')
        self.assertTrue(identity.whatsapp_link.startswith('https://wa.me/'))
        self.assertNotEqual(identity.warranty_policy_text, '')

    def test_the_pilot_kept_its_palette(self):
        pilot = Company.objects.filter(slug='black-dog-store').first()
        colors = company_branding(pilot).colors
        self.assertEqual(colors['background_color'], '#080808')
        self.assertNotEqual(colors, NEUTRAL_THEME)

    def test_another_company_never_inherits_the_pilots_identity(self):
        """
        The assertion this whole migration is shaped around. A tenant created
        after the upgrade must be blank, not wearing the pilot's name and RUC.
        """
        other = _p3_company('p3-mig-other', 'Otra Empresa')
        identity = company_identity(other)
        blob = ' '.join(str(v) for v in identity.as_dict().values()).lower()
        for pilot_value in ('black dog', 'cmau', '20610159886', 'octavio', '51936449536'):
            self.assertNotIn(pilot_value, blob)
        self.assertEqual(company_branding(other).colors, NEUTRAL_THEME)

    def test_historical_orders_were_given_a_snapshot(self):
        """
        Existing orders were stamped with their company's identity during the
        upgrade, so their documents stop depending on live configuration.
        """
        pilot = Company.objects.filter(slug='black-dog-store').first()
        order = _p3_order(pilot, snapshot=False)
        Order.objects.filter(pk=order.pk).update(
            company_snapshot=self._module._identity_snapshot(
                pilot, pilot.settings, order.fulfillment_branch,
            ),
        )
        order.refresh_from_db()
        self.assertEqual(order_identity(order).tax_id, '20610159886')

    def test_the_migration_snapshot_shape_matches_the_runtime_one(self):
        """
        0028 carries its own copy of the snapshot builder, as a migration must.
        This asserts the two produce the same KEYS today, so a change to one is a
        deliberate divergence rather than an accident nobody notices.
        """
        company = _p3_company(
            'p3-mig-shape', 'Forma', legal_name='F S.A.C.', tax_id='20444444444',
            phone='+51 444 444 444', legal_address='Calle F 4',
        )
        branch = company.default_inventory_branch

        runtime = build_identity_snapshot(company, branch)
        migrated = self._module._identity_snapshot(company, company.settings, branch)
        self.assertEqual(set(runtime), set(migrated))
        self.assertEqual(runtime['tax_id'], migrated['tax_id'])
        self.assertEqual(runtime['whatsapp_link'], migrated['whatsapp_link'])
        self.assertEqual(set(runtime['branch'] or {}), set(migrated['branch'] or {}))

    def test_the_global_notification_setting_is_no_longer_a_recipient(self):
        """
        It survives only so 0028 could copy it into the pilot's settings. Nothing
        in the request path reads it — otherwise a second tenant's sales would be
        announced at whichever address it holds.

        Introspects the COMPILED function rather than the source: the docstring
        explains the setting's history, and a text scan would fail on the
        explanation instead of on the behaviour. `co_names` holds the identifiers
        the bytecode actually touches.
        """
        from .email_services import send_internal_order_notification

        names = set(send_internal_order_notification.__code__.co_names)
        self.assertNotIn('ORDER_NOTIFICATION_EMAIL', names)
        self.assertIn('order_notification_recipient', names)


# ---------------------------------------------------------------------------
# SaaS Phase 2E — internal document sequences
# ---------------------------------------------------------------------------
#
# WHAT THESE TESTS ARE DEFENDING
#
# A document number is an identifier somebody else is holding. Three failures
# matter more than the rest, and each has a test whose name says so:
#
#   1. Two companies interleaving one counter — the bug this phase removes.
#   2. A number handed out twice, by a race or by a rewound counter.
#   3. A number already issued CHANGING, because somebody edited a prefix.

from .models import InternalSequence  # noqa: E402
import importlib  # noqa: E402
import inspect  # noqa: E402
import json  # noqa: E402

from django.db import migrations  # noqa: E402

from .sales_note_services import (  # noqa: E402
    build_sales_note_context,
    get_sales_note_filename,
)
from .sequences import (  # noqa: E402
    DEFAULT_PADDING,
    DEFAULT_PREFIX,
    SequenceError,
    allocate,
    can_change_scope,
    can_edit_next_value,
    company_sequence,
    ensure_branch_sequence,
    ensure_company_sequence,
    resolve_sequence_for_order,
    sequence_scope,
)


def _p2e_set_scope(company, scope):
    """Put a company on company- or branch-level numbering."""
    row = company.settings
    row.sales_note_sequence_scope = scope
    row.save(update_fields=['sales_note_sequence_scope', 'updated_at'])
    company.refresh_from_db()
    return row


def _p2e_issue(company, branch=None):
    """Issue one sales note for a fresh paid order. Returns the note."""
    from .sales_note_services import get_or_create_sales_note

    order = _p3_order(company, branch=branch or company.default_inventory_branch)
    note, _created = get_or_create_sales_note(order)
    return note


class Phase2eSequenceModelTest(TestCase):
    """The model, its constraints and its validators."""

    def setUp(self):
        cache.clear()
        self.company = _p3_company('p2e-model', 'Empresa Modelo')

    def test_provisioning_creates_the_company_series(self):
        sequence = company_sequence(self.company)
        self.assertIsNotNone(sequence)
        self.assertIsNone(sequence.branch_id)
        self.assertEqual(sequence.prefix, DEFAULT_PREFIX)
        self.assertEqual(sequence.padding, DEFAULT_PADDING)
        self.assertEqual(sequence.next_value, 1)

    def test_a_company_can_hold_only_one_company_level_series(self):
        """
        The reason the unique is CONDITIONAL.

        A plain unique over (company, branch, document_type) would not catch this:
        in SQL, NULL is not equal to NULL, so a company could accumulate any
        number of company-level rows for one document type — the exact row that
        must be singular.
        """
        from django.db import IntegrityError, transaction

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                InternalSequence.objects.create(
                    company=self.company, branch=None,
                    document_type=InternalSequence.DOCUMENT_SALES_NOTE,
                )

    def test_a_branch_can_hold_only_one_series_per_document_type(self):
        from django.db import IntegrityError, transaction

        branch = self.company.default_inventory_branch
        ensure_branch_sequence(self.company, branch)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                InternalSequence.objects.create(
                    company=self.company, branch=branch,
                    document_type=InternalSequence.DOCUMENT_SALES_NOTE,
                )

    def test_a_series_cannot_point_at_another_companys_branch(self):
        from django.core.exceptions import ValidationError

        other = _p3_company('p2e-model-other', 'Otra')
        with self.assertRaises(ValidationError):
            InternalSequence.objects.create(
                company=self.company, branch=other.default_inventory_branch,
                document_type=InternalSequence.DOCUMENT_SALES_NOTE,
            )

    def test_the_service_refuses_a_foreign_branch(self):
        other = _p3_company('p2e-model-other2', 'Otra 2')
        with self.assertRaises(SequenceError):
            ensure_branch_sequence(self.company, other.default_inventory_branch)

    def test_a_dangerous_prefix_is_rejected(self):
        """
        The prefix reaches a PDF, the UI and — through `_safe_slug` — a
        Content-Disposition header. The character set is the smallest one that
        still expresses a series name.
        """
        from django.core.exceptions import ValidationError

        sequence = company_sequence(self.company)
        for attack in (
            '\r\nContent-Disposition:',
            '../../',
            '<script>',
            'NV-"',
            'NV/2026/',          # `/` is a path separator; excluded deliberately
            'A' * 20,            # too long
            'NV ',               # whitespace
            'NV;',
        ):
            sequence.prefix = attack
            with self.assertRaises(ValidationError, msg=attack):
                sequence.save()

    def test_a_reasonable_prefix_is_accepted(self):
        sequence = company_sequence(self.company)
        for good in ('NV-', 'V', 'NOTA_', 'A1-', ''):
            sequence.prefix = good
            sequence.save()
            sequence.refresh_from_db()
            self.assertEqual(sequence.prefix, good)

    def test_padding_is_bounded(self):
        from django.core.exceptions import ValidationError

        sequence = company_sequence(self.company)
        for bad in (0, -1, 13, 1000000):
            sequence.padding = bad
            with self.assertRaises(ValidationError, msg=str(bad)):
                sequence.save()

        for good in (1, 6, 12):
            sequence.padding = good
            sequence.save()

    def test_next_value_cannot_be_zero_or_negative(self):
        from django.core.exceptions import ValidationError

        sequence = company_sequence(self.company)
        sequence.next_value = 0
        with self.assertRaises(ValidationError):
            sequence.save()

    def test_format_pads_to_the_configured_width(self):
        sequence = company_sequence(self.company)
        self.assertEqual(sequence.format(42), 'NV-000042')
        sequence.padding = 3
        self.assertEqual(sequence.format(42), 'NV-042')
        sequence.padding = 8
        self.assertEqual(sequence.format(42), 'NV-00000042')

    def test_preview_shows_the_next_number_and_allocates_nothing(self):
        """§44: the preview is informative. Reading it must not consume a number."""
        sequence = company_sequence(self.company)
        before = sequence.next_value
        self.assertEqual(sequence.preview, 'NV-000001')
        self.assertEqual(sequence.preview, 'NV-000001')
        sequence.refresh_from_db()
        self.assertEqual(sequence.next_value, before)


class Phase2eAllocationTest(TestCase):
    """Allocation: atomic, monotonic, and joined to the document it numbers."""

    def setUp(self):
        cache.clear()
        self.company = _p3_company('p2e-alloc', 'Empresa Alloc')
        self.sequence = company_sequence(self.company)

    def test_the_first_number_is_one(self):
        from django.db import transaction

        with transaction.atomic():
            value, number = allocate(self.sequence)
        self.assertEqual(value, 1)
        self.assertEqual(number, 'NV-000001')

    def test_successive_allocations_increment(self):
        from django.db import transaction

        seen = []
        for _ in range(5):
            with transaction.atomic():
                seen.append(allocate(self.sequence)[0])
        self.assertEqual(seen, [1, 2, 3, 4, 5])
        self.sequence.refresh_from_db()
        self.assertEqual(self.sequence.next_value, 6)

    def test_a_rolled_back_transaction_does_not_consume_a_number(self):
        """§24: if the document fails, the number is not spent."""
        from django.db import transaction

        class Boom(Exception):
            pass

        with self.assertRaises(Boom):
            with transaction.atomic():
                allocate(self.sequence)
                raise Boom()

        self.sequence.refresh_from_db()
        self.assertEqual(self.sequence.next_value, 1)

    def test_a_deactivated_series_issues_nothing(self):
        from django.db import transaction

        self.sequence.is_active = False
        self.sequence.save()
        with self.assertRaises(SequenceError):
            with transaction.atomic():
                allocate(self.sequence)

    def test_reactivating_continues_rather_than_restarting(self):
        """§68: deactivation retires a series; it does not reset it."""
        from django.db import transaction

        with transaction.atomic():
            allocate(self.sequence)
        self.sequence.refresh_from_db()

        self.sequence.is_active = False
        self.sequence.save()
        self.sequence.is_active = True
        self.sequence.save()

        with transaction.atomic():
            value, _ = allocate(self.sequence)
        self.assertEqual(value, 2)

    def test_the_service_does_not_derive_numbers_from_existing_documents(self):
        """
        §7: no MAX, no count, no order_by on the number.

        Introspects the compiled functions rather than the source, so the
        docstrings explaining what the old code did cannot fail the test.
        """
        from . import sales_note_services, sequences

        for module, function in (
            (sequences, sequences.allocate),
            (sales_note_services, sales_note_services.get_or_create_sales_note),
        ):
            names = set(function.__code__.co_names)
            for forbidden in ('aggregate', 'Max', 'count'):
                self.assertNotIn(
                    forbidden, names,
                    f'{function.__name__} vuelve a derivar el número de los documentos',
                )

    def test_the_counter_is_a_number_not_a_parsed_string(self):
        field = InternalSequence._meta.get_field('next_value')
        self.assertEqual(field.get_internal_type(), 'PositiveBigIntegerField')


class Phase2eCrossTenantTest(TestCase):
    """
    THE BUG THIS PHASE REMOVES.

    Before: A → NV-000001, B → NV-000002, A → NV-000003. Each tenant saw gaps
    caused by a stranger's activity.
    """

    def setUp(self):
        cache.clear()
        self.a = _p3_company('p2e-a', 'Empresa A')
        self.b = _p3_company('p2e-b', 'Empresa B')

    def test_two_companies_number_independently(self):
        """§61, exactly as written in the brief."""
        self.assertEqual(_p2e_issue(self.a).number, 'NV-000001')
        self.assertEqual(_p2e_issue(self.b).number, 'NV-000001')
        self.assertEqual(_p2e_issue(self.a).number, 'NV-000002')
        self.assertEqual(_p2e_issue(self.b).number, 'NV-000002')

    def test_the_same_display_number_may_exist_in_two_companies(self):
        """
        §63: no prefix and no number is reserved globally.

        The old schema forbade this with a unique on `number`, which is what made
        one tenant's numbering depend on another's.
        """
        first = _p2e_issue(self.a)
        second = _p2e_issue(self.b)
        self.assertEqual(first.number, second.number)
        self.assertNotEqual(first.sequence_id, second.sequence_id)

    def test_each_note_records_which_series_issued_it(self):
        note = _p2e_issue(self.a)
        self.assertEqual(note.sequence.company_id, self.a.pk)
        self.assertEqual(note.sequence_value, 1)
        self.assertEqual(note.number, note.sequence.format(note.sequence_value))

    def test_companies_may_use_different_prefixes(self):
        """§64."""
        sequence_b = company_sequence(self.b)
        sequence_b.prefix = 'SALE-'
        sequence_b.save()
        self.assertEqual(_p2e_issue(self.a).number, 'NV-000001')
        self.assertEqual(_p2e_issue(self.b).number, 'SALE-000001')

    def test_a_companys_counter_is_untouched_by_another_companys_issuance(self):
        for _ in range(3):
            _p2e_issue(self.a)
        self.assertEqual(company_sequence(self.b).next_value, 1)


class Phase2eBranchScopeTest(TestCase):
    """Per-branch numbering, and where the branch comes from."""

    def setUp(self):
        cache.clear()
        self.company = _p3_company('p2e-branch', 'Empresa Branch')
        self.a1 = self.company.default_inventory_branch
        self.a2 = Branch.objects.create(company=self.company, name='A2')
        _p2e_set_scope(self.company, CompanySettings.SEQUENCE_SCOPE_BRANCH)

    def test_each_branch_numbers_independently(self):
        """§62, exactly as written in the brief."""
        self.assertEqual(_p2e_issue(self.company, self.a1).number, 'NV-000001')
        self.assertEqual(_p2e_issue(self.company, self.a1).number, 'NV-000002')
        self.assertEqual(_p2e_issue(self.company, self.a2).number, 'NV-000001')
        self.assertEqual(_p2e_issue(self.company, self.a1).number, 'NV-000003')

    def test_branch_series_are_created_on_demand(self):
        """Lazy: no counter exists for a branch that has never issued."""
        self.assertFalse(
            InternalSequence.objects.filter(company=self.company, branch=self.a2).exists()
        )
        _p2e_issue(self.company, self.a2)
        self.assertTrue(
            InternalSequence.objects.filter(company=self.company, branch=self.a2).exists()
        )

    def test_a_new_branch_series_copies_the_company_template(self):
        template = company_sequence(self.company)
        template.prefix = 'X_'
        template.padding = 4
        template.save()

        sequence = ensure_branch_sequence(self.company, self.a2)
        self.assertEqual(sequence.prefix, 'X_')
        self.assertEqual(sequence.padding, 4)
        self.assertEqual(sequence.next_value, 1, 'una sucursal nueva empieza en 1')

    def test_the_branch_is_derived_from_the_order_never_chosen(self):
        """
        §47: letting a caller name the branch would let them pick whichever
        series gives them the number they want.
        """
        order = _p3_order(self.company, branch=self.a2)
        sequence = resolve_sequence_for_order(order)
        self.assertEqual(sequence.branch_id, self.a2.pk)

    def test_an_order_with_no_branch_falls_back_to_the_company_series(self):
        """
        Possible for orders that predate Phase 2D. The note must be issuable, and
        the company series is the one that order's numbering already belonged to.
        """
        order = _p3_order(self.company, branch=None)
        Order.objects.filter(pk=order.pk).update(fulfillment_branch=None)
        order.refresh_from_db()
        sequence = resolve_sequence_for_order(order)
        self.assertIsNone(sequence.branch_id)

    def test_the_scope_is_read_from_settings_not_inferred_from_rows(self):
        """
        A leftover branch row must not silently mean "branch scope". Only the
        stored policy decides.
        """
        ensure_branch_sequence(self.company, self.a2)
        _p2e_set_scope(self.company, CompanySettings.SEQUENCE_SCOPE_COMPANY)
        order = _p3_order(self.company, branch=self.a2)
        self.assertIsNone(resolve_sequence_for_order(order).branch_id)

    def test_company_series_still_counts_under_company_scope(self):
        _p2e_set_scope(self.company, CompanySettings.SEQUENCE_SCOPE_COMPANY)
        self.assertEqual(_p2e_issue(self.company, self.a1).number, 'NV-000001')
        self.assertEqual(_p2e_issue(self.company, self.a2).number, 'NV-000002')


class Phase2eConcurrencyTest(TransactionTestCase):
    """
    Two people issuing at once. The interesting cases are the ones a happy path
    never reaches.

    WHAT SQLITE CAN AND CANNOT PROVE
    --------------------------------
    `select_for_update()` is a no-op on SQLite — the engine serialises writes
    with a database-level lock — so a threaded race here would exercise the
    engine's global lock rather than this module's row lock, and pass for the
    wrong reason. That is worse than no test.

    So the sequential invariants run on any backend, and the genuinely
    concurrent case runs ONLY where row locking exists. On SQLite it is skipped,
    loudly. Same rule as the Phase 2D inventory tests.
    """

    def setUp(self):
        cache.clear()
        self.company = _p3_company('p2e-conc', 'Empresa Conc')
        self.sequence = company_sequence(self.company)

    def test_allocation_outside_a_transaction_is_refused(self):
        """
        The number and the document must commit or roll back together.

        Only observable from a TransactionTestCase: a plain TestCase wraps every
        test in a transaction, so the guard would never see the outside.
        """
        with self.assertRaises(SequenceError):
            allocate(self.sequence)

    def test_no_number_is_ever_handed_out_twice(self):
        numbers = [_p2e_issue(self.company).number for _ in range(10)]
        self.assertEqual(len(numbers), len(set(numbers)))
        self.assertEqual(numbers[0], 'NV-000001')
        self.assertEqual(numbers[-1], 'NV-000010')

    def test_issuing_twice_for_the_same_order_consumes_one_number(self):
        """
        §28. The order lock is what makes this true: the second request waits,
        finds the note already there, and returns WITHOUT allocating.

        Allocating before the existence check would burn an ordinal on a note
        that is never written — a gap with no document to explain it.
        """
        from .sales_note_services import get_or_create_sales_note

        order = _p3_order(self.company)
        first, created_first = get_or_create_sales_note(order)
        second, created_second = get_or_create_sales_note(order)

        self.assertTrue(created_first)
        self.assertFalse(created_second)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(first.number, second.number)

        self.sequence.refresh_from_db()
        self.assertEqual(self.sequence.next_value, 2, 'un solo número consumido')
        self.assertEqual(SalesNote.objects.filter(order=order).count(), 1)

    def test_two_companies_do_not_contend(self):
        """
        §84: issuance in A locks A's row and nothing else. Verified by state
        rather than by timing — B's counter is untouched by A's activity.
        """
        other = _p3_company('p2e-conc-b', 'Empresa Conc B')
        for _ in range(3):
            _p2e_issue(self.company)
        self.assertEqual(company_sequence(other).next_value, 1)
        self.assertEqual(_p2e_issue(other).number, 'NV-000001')

    def test_two_branches_do_not_contend(self):
        other_branch = Branch.objects.create(company=self.company, name='Conc B2')
        _p2e_set_scope(self.company, CompanySettings.SEQUENCE_SCOPE_BRANCH)

        _p2e_issue(self.company, self.company.default_inventory_branch)
        _p2e_issue(self.company, self.company.default_inventory_branch)
        self.assertEqual(_p2e_issue(self.company, other_branch).number, 'NV-000001')

    def test_the_database_refuses_a_duplicate_ordinal_even_if_the_service_is_wrong(self):
        """
        §42: the constraint is the backstop, not the service.

        Simulated by writing a second note on an ordinal already taken — the
        shape a bug in the allocator would produce.
        """
        from django.db import IntegrityError, transaction

        note = _p2e_issue(self.company)
        order = _p3_order(self.company)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                SalesNote.objects.create(
                    order=order,
                    sequence=note.sequence,
                    sequence_value=note.sequence_value,
                    number=note.number,
                    status=SalesNote.STATUS_ISSUED,
                    issued_at=timezone.now(),
                )

    def test_simultaneous_issuance_for_different_orders(self):
        import threading

        from django.db import connection, connections

        if connection.vendor == 'sqlite':
            self.skipTest(
                'SQLite has no row-level locking: select_for_update() is a no-op, '
                'so a threaded race here would exercise the engine\'s global write '
                'lock rather than this module\'s. Run the suite against PostgreSQL '
                'to exercise it.'
            )

        from .sales_note_services import get_or_create_sales_note

        orders = [_p3_order(self.company) for _ in range(8)]
        results = []
        lock = threading.Lock()

        def worker(order):
            try:
                note, _created = get_or_create_sales_note(order)
                with lock:
                    results.append(note.number)
            finally:
                connections.close_all()

        threads = [threading.Thread(target=worker, args=(o,)) for o in orders]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(results), len(orders))
        self.assertEqual(len(set(results)), len(orders), 'ningún número repetido')
        self.sequence.refresh_from_db()
        self.assertEqual(self.sequence.next_value, len(orders) + 1)

    def test_the_allocator_locks_the_sequence_row_and_nothing_wider(self):
        """
        Introspection, because the behavioural test above cannot run on SQLite.

        Locking `CompanySettings` — the tempting shortcut, since the scope lives
        there — would serialise every branch of a company behind every other and
        block the company's whole configuration for the duration of a PDF number.
        """
        import inspect

        from . import sequences

        source = inspect.getsource(sequences.allocate)
        self.assertIn('select_for_update', source)
        self.assertIn('InternalSequence', source)
        self.assertNotIn('CompanySettings.objects.select_for_update', source)

        module_source = inspect.getsource(sequences)
        self.assertNotIn('CompanySettings.objects.select_for_update', module_source)


class Phase2eConfigTest(TestCase):
    """
    Changing the shape of the numbers, and what changing it must NOT touch.
    """

    def setUp(self):
        cache.clear()
        self.company = _p3_company('p2e-config', 'Empresa Config')
        self.sequence = company_sequence(self.company)

    def test_prefix_and_padding_apply_to_the_next_document(self):
        self.sequence.prefix = 'VTA-'
        self.sequence.padding = 4
        self.sequence.save()
        self.assertEqual(_p2e_issue(self.company).number, 'VTA-0001')

    def test_changing_the_prefix_does_not_rewrite_documents_already_issued(self):
        """
        §33 and the reason `number` is a STORED string rather than a property.

        A note is handed to a customer and attached to an email. If the PDF
        re-derived its number from the series, editing the prefix would silently
        change what an already-delivered document says — the paper in someone's
        hand and the record in the system would stop agreeing.
        """
        issued = _p2e_issue(self.company)
        self.assertEqual(issued.number, 'NV-000001')

        # Reloaded, not reused: the in-memory copy from setUp still believes the
        # counter is at 1, and a full save() would write that belief back.
        self.sequence.refresh_from_db()
        self.sequence.prefix = 'VTA-'
        self.sequence.padding = 3
        self.sequence.save()

        issued.refresh_from_db()
        self.assertEqual(issued.number, 'NV-000001', 'el documento emitido no cambia')
        self.assertEqual(_p2e_issue(self.company).number, 'VTA-002')

    def test_padding_only_pads_it_never_truncates(self):
        """
        A counter past its padding must widen, not wrap. `zfill` already does
        this; the test pins it, because the alternative — slicing to width —
        would produce a duplicate number, which is the one outcome forbidden.
        """
        self.sequence.padding = 2
        self.sequence.next_value = 1000
        self.sequence.save()
        self.assertEqual(self.sequence.preview, 'NV-1000')

    def test_an_empty_prefix_is_allowed(self):
        self.sequence.prefix = ''
        self.sequence.save()
        self.assertEqual(_p2e_issue(self.company).number, '000001')

    def test_the_counter_may_be_set_before_the_first_document(self):
        """A business migrating from another system continues its numbering."""
        self.assertTrue(can_edit_next_value(self.sequence))
        self.sequence.next_value = 5001
        self.sequence.save()
        self.assertEqual(_p2e_issue(self.company).number, 'NV-005001')

    def test_the_counter_is_frozen_once_it_has_issued(self):
        _p2e_issue(self.company)
        self.sequence.refresh_from_db()
        self.assertTrue(self.sequence.has_issued)
        self.assertFalse(can_edit_next_value(self.sequence))

    def test_a_preview_does_not_consume_a_number(self):
        """
        §36. The obvious wrong implementation is to allocate and show the result,
        which turns every glance at the settings page into a gap in the series.
        """
        before = self.sequence.next_value
        preview = self.sequence.preview
        self.sequence.refresh_from_db()
        self.assertEqual(self.sequence.next_value, before)
        self.assertEqual(preview, 'NV-000001')
        self.assertEqual(_p2e_issue(self.company).number, preview)

    def test_a_deactivated_series_refuses_to_issue(self):
        self.sequence.is_active = False
        self.sequence.save()
        with self.assertRaises(SequenceError):
            with transaction.atomic():
                allocate(self.sequence)

    def test_editing_a_series_does_not_rewind_its_counter(self):
        """
        The hazard behind the endpoint's narrow update_fields.

        A settings form is loaded, a sale happens, the form is saved. A full save
        of the object read BEFORE the sale writes back the old counter, and the
        next document reuses an ordinal that is already on paper. The endpoint
        writes only the fields that changed; this pins that it stays that way.
        """
        stale = InternalSequence.objects.get(pk=self.sequence.pk)
        _p2e_issue(self.company)

        stale.prefix = 'VTA-'
        stale.save(update_fields=['prefix', 'updated_at'])

        self.sequence.refresh_from_db()
        self.assertEqual(self.sequence.next_value, 2, 'el contador no retrocede')
        self.assertEqual(self.sequence.prefix, 'VTA-')

    def test_the_scope_is_frozen_once_the_company_has_issued(self):
        self.assertTrue(can_change_scope(self.company))
        _p2e_issue(self.company)
        self.assertFalse(can_change_scope(self.company))


class Phase2eSequenceApiTest(TestCase):
    """
    The configuration endpoints: who may read, who may write, and what is
    unreachable from another tenant's session.
    """

    def setUp(self):
        cache.clear()
        self.a = _p3_company('p2e-api-a', 'Empresa API A')
        self.b = _p3_company('p2e-api-b', 'Empresa API B')
        self.admin_a, _ = _p2d_member(
            self.a, 'p2e_admin_a', ['company.view', 'company.manage'],
        )
        self.viewer_a, _ = _p2d_member(self.a, 'p2e_viewer_a', ['company.view'])
        self.admin_b, _ = _p2d_member(
            self.b, 'p2e_admin_b', ['company.view', 'company.manage'],
        )
        self.outsider = User.objects.create_user(username='p2e_outsider', password='x')
        self.seq_a = company_sequence(self.a)
        self.seq_b = company_sequence(self.b)

    def _as(self, user):
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    def test_an_admin_lists_their_own_series(self):
        res = self._as(self.admin_a).get('/api/admin/sequences/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.data['results']), 1)
        self.assertEqual(res.data['results'][0]['id'], self.seq_a.pk)
        self.assertEqual(res.data['results'][0]['preview'], 'NV-000001')
        self.assertTrue(res.data['can_manage'])

    def test_the_response_says_the_numbering_is_not_fiscal(self):
        """
        §57. `NV-000001` next to a logo and a total looks like a receipt. The
        product must keep saying, at every surface, that it is not one.
        """
        res = self._as(self.admin_a).get('/api/admin/sequences/')
        self.assertIn('No es numeración fiscal', res.data['notice'])
        self.assertIn('SUNAT', res.data['notice'])

    def test_a_viewer_reads_but_cannot_write(self):
        client = self._as(self.viewer_a)
        self.assertEqual(
            client.get('/api/admin/sequences/').status_code, status.HTTP_200_OK,
        )
        write = client.patch(
            f'/api/admin/sequences/{self.seq_a.pk}/', {'prefix': 'X-'}, format='json',
        )
        self.assertEqual(write.status_code, status.HTTP_403_FORBIDDEN)
        self.seq_a.refresh_from_db()
        self.assertEqual(self.seq_a.prefix, 'NV-')

    def test_an_admin_cannot_read_another_tenants_series(self):
        res = self._as(self.admin_a).get(f'/api/admin/sequences/{self.seq_b.pk}/')
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_an_admin_cannot_write_another_tenants_series(self):
        """
        The leak this phase could most easily have shipped: a numeric id in a URL
        with the tenant check left to a later line that a refactor drops.
        """
        res = self._as(self.admin_a).patch(
            f'/api/admin/sequences/{self.seq_b.pk}/',
            {'prefix': 'HACK-', 'next_value': 9999}, format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
        self.seq_b.refresh_from_db()
        self.assertEqual(self.seq_b.prefix, 'NV-')
        self.assertEqual(self.seq_b.next_value, 1)

    def test_a_company_parameter_cannot_reach_another_tenant(self):
        res = self._as(self.admin_a).get(f'/api/admin/sequences/?company={self.b.pk}')
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_a_user_with_no_membership_is_refused(self):
        res = self._as(self.outsider).get('/api/admin/sequences/')
        self.assertIn(
            res.status_code, (status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND),
        )

    def test_anonymous_is_refused(self):
        res = APIClient().get('/api/admin/sequences/')
        self.assertIn(
            res.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
        )

    def test_an_admin_edits_prefix_and_padding(self):
        res = self._as(self.admin_a).patch(
            f'/api/admin/sequences/{self.seq_a.pk}/',
            {'prefix': 'VTA-', 'padding': 4}, format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['preview'], 'VTA-0001')

    def test_an_invalid_prefix_is_rejected(self):
        for bad in ('NV/', '../', 'NV 1', 'a' * 13):
            res = self._as(self.admin_a).patch(
                f'/api/admin/sequences/{self.seq_a.pk}/', {'prefix': bad}, format='json',
            )
            self.assertEqual(
                res.status_code, status.HTTP_400_BAD_REQUEST, f'aceptó {bad!r}',
            )
        self.seq_a.refresh_from_db()
        self.assertEqual(self.seq_a.prefix, 'NV-')

    def test_padding_outside_its_range_is_rejected(self):
        for bad in (0, 13, 99):
            res = self._as(self.admin_a).patch(
                f'/api/admin/sequences/{self.seq_a.pk}/', {'padding': bad}, format='json',
            )
            self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_the_counter_cannot_be_moved_after_the_first_document(self):
        _p2e_issue(self.a)
        res = self._as(self.admin_a).patch(
            f'/api/admin/sequences/{self.seq_a.pk}/', {'next_value': 1}, format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('next_value', res.data)
        self.seq_a.refresh_from_db()
        self.assertEqual(self.seq_a.next_value, 2)

    def test_resending_the_same_counter_is_not_a_change(self):
        """
        A settings form PATCHes every field it renders, including the ones the
        user did not touch. Treating an unchanged value as an edit would make the
        page impossible to save at all after the first document.
        """
        _p2e_issue(self.a)
        self.seq_a.refresh_from_db()
        res = self._as(self.admin_a).patch(
            f'/api/admin/sequences/{self.seq_a.pk}/',
            {'prefix': 'VTA-', 'next_value': self.seq_a.next_value}, format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['prefix'], 'VTA-')

    def test_an_admin_switches_the_scope_before_issuing(self):
        res = self._as(self.admin_a).patch(
            '/api/admin/sequences/scope/',
            {'scope': CompanySettings.SEQUENCE_SCOPE_BRANCH}, format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertTrue(res.data['changed'])
        self.a.refresh_from_db()
        self.assertEqual(
            self.a.settings.sales_note_sequence_scope,
            CompanySettings.SEQUENCE_SCOPE_BRANCH,
        )

    def test_the_scope_is_frozen_after_the_first_document(self):
        _p2e_issue(self.a)
        res = self._as(self.admin_a).patch(
            '/api/admin/sequences/scope/',
            {'scope': CompanySettings.SEQUENCE_SCOPE_BRANCH}, format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.a.refresh_from_db()
        self.assertEqual(
            self.a.settings.sales_note_sequence_scope,
            CompanySettings.SEQUENCE_SCOPE_COMPANY,
        )

    def test_reasserting_the_current_scope_is_allowed_after_issuing(self):
        _p2e_issue(self.a)
        res = self._as(self.admin_a).patch(
            '/api/admin/sequences/scope/',
            {'scope': CompanySettings.SEQUENCE_SCOPE_COMPANY}, format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertFalse(res.data['changed'])

    def test_a_viewer_cannot_switch_the_scope(self):
        res = self._as(self.viewer_a).patch(
            '/api/admin/sequences/scope/',
            {'scope': CompanySettings.SEQUENCE_SCOPE_BRANCH}, format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_a_company_with_no_series_gets_one_on_read(self):
        """
        A tenant provisioned before Phase 2E has no series row. The settings page
        must be usable anyway, so the list creates it — idempotently.
        """
        InternalSequence.objects.filter(company=self.a).delete()
        client = self._as(self.admin_a)
        first = client.get('/api/admin/sequences/')
        second = client.get('/api/admin/sequences/')
        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(len(second.data['results']), 1)
        self.assertEqual(InternalSequence.objects.filter(company=self.a).count(), 1)

    def test_a_restricted_user_sees_only_their_branches_series(self):
        """
        Phase 2D's second axis, applied to numbering: a user who may operate two
        of three shops configures two counters, not three.
        """
        b1 = self.a.default_inventory_branch
        b2 = Branch.objects.create(company=self.a, name='API Sucursal 2')
        b3 = Branch.objects.create(company=self.a, name='API Sucursal 3')
        _p2e_set_scope(self.a, CompanySettings.SEQUENCE_SCOPE_BRANCH)
        for branch in (b1, b2, b3):
            ensure_branch_sequence(self.a, branch)

        restricted, _ = _p2d_member(
            self.a, 'p2e_restricted', ['company.view', 'company.manage'],
            mode='selected', branches=[b1, b2],
        )
        res = self._as(restricted).get('/api/admin/sequences/')
        branch_ids = {r['branch'] for r in res.data['results'] if r['branch']}
        self.assertEqual(branch_ids, {b1.pk, b2.pk})
        self.assertNotIn(b3.pk, branch_ids)

    def test_under_company_scope_dormant_branch_rows_are_not_listed(self):
        """
        Leftovers from a branch-scope configuration are not in use. Listing them
        would invite someone to configure a counter that never issues anything.
        """
        ensure_branch_sequence(self.a, self.a.default_inventory_branch)
        res = self._as(self.admin_a).get('/api/admin/sequences/')
        self.assertEqual(len(res.data['results']), 1)
        self.assertIsNone(res.data['results'][0]['branch'])


class Phase2eMigrationTest(TestCase):
    """
    What the two migrations promise about data that already exists.

    The upgrade itself was verified end-to-end against a database populated at
    0028 and migrated forward; these pin the invariants so a later edit to the
    backfill cannot quietly break them.
    """

    def test_reverse_guard_raises_with_an_explanation(self):
        """
        Reversing 0029 restores the GLOBAL unique on number — unsatisfiable once
        two tenants each hold an NV-000001, which is the entire point of the
        phase. Refusing loudly beats a database error nobody can act on, and
        beats "succeeding" by renumbering documents people are holding.
        """
        module = importlib.import_module('store.migrations.0029_internal_sequences')
        with self.assertRaises(RuntimeError) as ctx:
            module._refuse_reverse(None, None)
        message = str(ctx.exception)
        self.assertIn('no es reversible', message)
        self.assertIn('copia de seguridad', message)

    def test_the_reverse_guard_runs_first_on_a_rollback(self):
        """
        Django applies reverse operations in reverse order, so the guard must be
        the LAST operation to be the FIRST thing a rollback hits. Anywhere else
        and it fires after some of the schema is already undone.
        """
        module = importlib.import_module('store.migrations.0029_internal_sequences')
        last = module.Migration.operations[-1]
        self.assertIsInstance(last, migrations.RunPython)
        self.assertIs(last.reverse_code, module._refuse_reverse)

    def test_the_backfill_never_writes_the_number_column(self):
        """
        §33: already-issued documents are history. The backfill attaches them to
        a series and records their ordinal; it must not touch the string a
        customer already received.
        """
        module = importlib.import_module(
            'store.migrations.0030_backfill_internal_sequences'
        )
        source = inspect.getsource(module)
        self.assertNotIn("number=", source.replace('sequence_value=', ''))
        self.assertNotIn("'number'", source.split('_NUMBER_RE')[-1].split('def ')[0])

    def test_the_backfill_parses_a_conventional_number(self):
        module = importlib.import_module(
            'store.migrations.0030_backfill_internal_sequences'
        )
        match = module._NUMBER_RE.match('NV-000015')
        self.assertIsNotNone(match)
        self.assertEqual(match.group('prefix'), 'NV-')
        self.assertEqual(int(match.group('digits')), 15)

    def test_the_backfill_leaves_an_unparseable_number_alone(self):
        """
        A hand-typed `MANUAL-ABC` has no ordinal. Inventing one would put it in
        the series and risk colliding with a real number; the honest outcome is
        a note with its string intact and `sequence_value` NULL.
        """
        module = importlib.import_module(
            'store.migrations.0030_backfill_internal_sequences'
        )
        self.assertIsNone(module._NUMBER_RE.match('MANUAL-ABC'))

    def test_a_note_with_no_ordinal_is_allowed_by_the_constraint(self):
        """
        The uniqueness constraint is CONDITIONAL on both columns being present,
        so unparseable legacy notes coexist rather than blocking the migration.
        """
        company = _p3_company('p2e-mig', 'Empresa Migración')
        sequence = company_sequence(company)
        for _ in range(2):
            SalesNote.objects.create(
                order=_p3_order(company),
                sequence=sequence,
                sequence_value=None,
                number='MANUAL-ABC',
                status=SalesNote.STATUS_ISSUED,
                issued_at=timezone.now(),
            )
        self.assertEqual(SalesNote.objects.filter(number='MANUAL-ABC').count(), 2)

    def test_two_tenants_may_each_hold_the_same_number(self):
        """
        The reason the global unique was dropped. Before Phase 2E, company B
        issuing after company A got NV-000002 — one tenant's numbering visibly
        depending on another's activity.
        """
        a = _p3_company('p2e-mig-a', 'Migración A')
        b = _p3_company('p2e-mig-b', 'Migración B')
        self.assertEqual(_p2e_issue(a).number, 'NV-000001')
        self.assertEqual(_p2e_issue(b).number, 'NV-000001')
        self.assertEqual(SalesNote.objects.filter(number='NV-000001').count(), 2)


class Phase2ePdfTest(TestCase):
    """The number as it reaches paper."""

    def setUp(self):
        cache.clear()
        self.company = _p3_company(
            'p2e-pdf', 'Empresa PDF',
            legal_name='Empresa PDF S.A.C.', tax_id='20555555555',
        )
        self.note = _p2e_issue(self.company)

    def test_the_pdf_shows_the_number_that_was_stored(self):
        ctx = build_sales_note_context(self.note)
        self.assertEqual(ctx['number'], self.note.number)
        self.assertEqual(ctx['number'], 'NV-000001')

    def test_the_pdf_number_survives_a_later_prefix_change(self):
        """
        The PDF is regenerated on demand — a customer asks for their copy months
        later. It must render what the document said when it was issued, not what
        the series would produce today.
        """
        sequence = company_sequence(self.company)
        sequence.prefix = 'VTA-'
        sequence.padding = 3
        sequence.save()

        self.note.refresh_from_db()
        self.assertEqual(build_sales_note_context(self.note)['number'], 'NV-000001')

    def test_the_pdf_carries_the_issuing_companys_identity(self):
        ctx = build_sales_note_context(self.note)
        self.assertEqual(ctx['store_name'], 'Empresa PDF')
        self.assertEqual(ctx['store_ruc'], '20555555555')

    def test_the_pdf_says_it_is_not_a_sunat_document(self):
        ctx = build_sales_note_context(self.note)
        self.assertIn('SUNAT', ctx['disclaimer'])
        self.assertTrue(ctx['disclaimer'])

    def test_two_tenants_holding_the_same_number_render_different_documents(self):
        other = _p3_company('p2e-pdf-b', 'Empresa PDF B', tax_id='20666666666')
        other_note = _p2e_issue(other)

        mine = build_sales_note_context(self.note)
        theirs = build_sales_note_context(other_note)
        self.assertEqual(mine['number'], theirs['number'])
        self.assertNotEqual(mine['store_name'], theirs['store_name'])
        self.assertNotEqual(mine['store_ruc'], theirs['store_ruc'])

    def test_the_filename_is_built_from_the_slug_not_free_text(self):
        name = get_sales_note_filename(self.note)
        self.assertTrue(name.endswith('.pdf'))
        self.assertIn('nv-000001', name.lower())
        self.assertNotIn('/', name)
        self.assertNotIn('..', name)

    def test_a_prefix_cannot_smuggle_a_path_into_the_filename(self):
        """
        Belt and braces. The validator already refuses `/`, and the filename
        builder slugs whatever it gets; this pins both, because either alone
        being dropped would make the other a single point of failure.
        """
        sequence = company_sequence(self.company)
        with self.assertRaises(DjangoValidationError):
            sequence.prefix = '../etc/'
            sequence.full_clean()


class Phase2eAuditTest(TestCase):
    """Configuration changes leave a trail; issuance already did."""

    def setUp(self):
        cache.clear()
        self.company = _p3_company('p2e-audit', 'Empresa Audit')
        # Two capabilities because they are genuinely two permissions: deciding
        # what the numbers look like is `company.manage`, handing out the next
        # one is `sales.notes.manage`.
        self.admin, _ = _p2d_member(
            self.company, 'p2e_audit_admin',
            ['company.view', 'company.manage', 'sales.notes.manage'],
        )
        self.sequence = company_sequence(self.company)
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)

    def test_editing_a_series_is_logged(self):
        AdminAuditLog.objects.all().delete()
        self.client.patch(
            f'/api/admin/sequences/{self.sequence.pk}/',
            {'prefix': 'VTA-', 'padding': 4}, format='json',
        )
        entry = AdminAuditLog.objects.filter(action='sequence_updated').first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.actor, self.admin)
        self.assertEqual(entry.metadata['company_id'], self.company.pk)
        self.assertEqual(entry.metadata['changed_fields'], ['padding', 'prefix'])

    def test_a_patch_that_changes_nothing_is_not_logged(self):
        """
        A settings page that PATCHes on every render would otherwise fill the
        audit trail with entries recording that nothing happened, which is how a
        trail stops being read.
        """
        AdminAuditLog.objects.all().delete()
        res = self.client.patch(
            f'/api/admin/sequences/{self.sequence.pk}/',
            {'prefix': 'NV-', 'padding': 6}, format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertFalse(AdminAuditLog.objects.filter(action='sequence_updated').exists())

    def test_changing_the_scope_is_logged(self):
        AdminAuditLog.objects.all().delete()
        self.client.patch(
            '/api/admin/sequences/scope/',
            {'scope': CompanySettings.SEQUENCE_SCOPE_BRANCH}, format='json',
        )
        entry = AdminAuditLog.objects.filter(action='sequence_scope_changed').first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.company, self.company)
        self.assertEqual(entry.metadata['scope'], CompanySettings.SEQUENCE_SCOPE_BRANCH)

    def test_a_rejected_scope_change_is_not_logged_as_a_change(self):
        _p2e_issue(self.company)
        AdminAuditLog.objects.all().delete()
        self.client.patch(
            '/api/admin/sequences/scope/',
            {'scope': CompanySettings.SEQUENCE_SCOPE_BRANCH}, format='json',
        )
        self.assertFalse(
            AdminAuditLog.objects.filter(action='sequence_scope_changed').exists()
        )

    def test_issuing_a_note_records_the_number(self):
        order = _p3_order(self.company)
        AdminAuditLog.objects.all().delete()
        res = self.client.post(f'/api/admin/orders/{order.pk}/sales-note/')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        entry = AdminAuditLog.objects.filter(action='sales_note_created').first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.metadata['sales_note_number'], 'NV-000001')

    def test_the_audit_trail_never_carries_a_secret(self):
        self.client.patch(
            f'/api/admin/sequences/{self.sequence.pk}/', {'prefix': 'VTA-'}, format='json',
        )
        for entry in AdminAuditLog.objects.all():
            blob = json.dumps(entry.metadata).lower()
            for forbidden in ('password', 'secret', 'token', 'sk_'):
                self.assertNotIn(forbidden, blob)


# ===========================================================================
# SaaS Phase 4 — customers (CRM)
# ===========================================================================

from .customer_services import (  # noqa: E402
    DuplicateCustomerError,
    assert_document_available,
    find_possible_duplicates,
    link_order_to_customer,
    resolve_customer,
)
from .models import (  # noqa: E402
    Customer,
    normalize_customer_email,
    normalize_customer_phone,
    normalize_document_number,
)

from django.apps import apps as django_apps  # noqa: E402

_P4_VIEW = 'service.customers.view'
_P4_MANAGE = 'service.customers.manage'


def _p4_customer(company, **kw):
    """A person of `company`, with just enough identity to be valid."""
    defaults = {
        'customer_type': Customer.TYPE_PERSON,
        'first_name': 'Cliente',
        'last_name': 'Prueba',
    }
    defaults.update(kw)
    return Customer.objects.create(company=company, **defaults)


class Phase4CustomerModelTest(TestCase):
    """The model, its constraints and the four concepts it must not conflate."""

    def setUp(self):
        cache.clear()
        self.a = _p3_company('p4-model-a', 'Empresa Modelo A')
        self.b = _p3_company('p4-model-b', 'Empresa Modelo B')

    def test_a_customer_belongs_to_exactly_one_company(self):
        customer = _p4_customer(self.a)
        self.assertEqual(customer.company, self.a)
        self.assertIn(customer, self.a.customers.all())
        self.assertNotIn(customer, self.b.customers.all())

    def test_a_customer_exists_with_no_account(self):
        """
        The normal case, not the exception. Most clients of a repair shop walk
        in, phone or write on WhatsApp and will never have a login.
        """
        customer = _p4_customer(self.a)
        self.assertIsNone(customer.user_id)
        self.assertFalse(customer.has_account)

    def test_one_account_holds_one_record_per_company(self):
        from django.db import IntegrityError, transaction

        user = User.objects.create_user(username='p4_model_user', password='x')
        _p4_customer(self.a, user=user)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                _p4_customer(self.a, user=user, first_name='Otro')

    def test_one_account_holds_independent_records_in_two_companies(self):
        """
        §70. Two businesses that happen to serve the same person keep separate
        files on them: separate notes, separate address, separate history.
        """
        user = User.objects.create_user(username='p4_two_companies', password='x')
        in_a = _p4_customer(self.a, user=user, notes='Nota privada de A')
        in_b = _p4_customer(self.b, user=user, notes='Nota privada de B')

        self.assertNotEqual(in_a.pk, in_b.pk)
        self.assertEqual(in_a.notes, 'Nota privada de A')
        self.assertEqual(in_b.notes, 'Nota privada de B')
        self.assertEqual(user.customer_records.count(), 2)

    def test_customers_with_no_account_do_not_collide(self):
        """
        The reason the unique on `user` is CONDITIONAL: SQL treats NULLs as
        distinct, so any number of clients may have no login.
        """
        for i in range(4):
            _p4_customer(self.a, first_name=f'Sin cuenta {i}')
        self.assertEqual(Customer.objects.filter(company=self.a, user__isnull=True).count(), 4)

    def test_a_document_identifies_one_client_inside_a_company(self):
        from django.db import IntegrityError, transaction

        _p4_customer(self.a, document_type='dni', document_number='12345678')
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                _p4_customer(self.a, document_type='dni', document_number='12345678',
                             first_name='Impostor')

    def test_the_same_document_may_exist_in_two_companies(self):
        """
        §71. Uniqueness is per company. Two businesses each holding a file on
        DNI 12345678 is two files on one person, and neither can see the other.
        """
        _p4_customer(self.a, document_type='dni', document_number='12345678')
        _p4_customer(self.b, document_type='dni', document_number='12345678')
        self.assertEqual(Customer.objects.filter(document_number='12345678').count(), 2)

    def test_customers_without_a_document_do_not_collide(self):
        for i in range(3):
            _p4_customer(self.a, first_name=f'Sin documento {i}')
        self.assertEqual(
            Customer.objects.filter(company=self.a, document_number='').count(), 3,
        )

    def test_a_record_must_be_identifiable_as_something(self):
        """
        A row with no name, no business name and no document is not a client, it
        is an empty form somebody will re-create tomorrow because they could not
        find it.
        """
        from django.core.exceptions import ValidationError as DjangoValidationError

        with self.assertRaises(DjangoValidationError):
            Customer.objects.create(company=self.a, customer_type=Customer.TYPE_PERSON)

    def test_a_business_needs_a_business_name(self):
        from django.core.exceptions import ValidationError as DjangoValidationError

        with self.assertRaises(DjangoValidationError):
            Customer.objects.create(
                company=self.a, customer_type=Customer.TYPE_BUSINESS,
                first_name='Solo', last_name='Persona',
            )

    def test_a_business_does_not_need_a_tax_id(self):
        """
        §11. A neighbourhood shop is a business the moment the counter says so.
        Refusing to file it until somebody produces a RUC pushes the record out
        of the system and onto paper.
        """
        customer = _p4_customer(
            self.a, customer_type=Customer.TYPE_BUSINESS,
            business_name='Bodega Los Álamos', first_name='', last_name='',
        )
        self.assertEqual(customer.document_number, '')
        self.assertEqual(customer.display_name, 'Bodega Los Álamos')

    def test_a_document_number_and_its_type_travel_together(self):
        from django.core.exceptions import ValidationError as DjangoValidationError

        with self.assertRaises(DjangoValidationError):
            _p4_customer(self.a, document_number='12345678')
        with self.assertRaises(DjangoValidationError):
            _p4_customer(self.a, document_type='dni')

    def test_display_name_follows_the_customer_type(self):
        person = _p4_customer(self.a, first_name='Ana', last_name='Quispe')
        business = _p4_customer(
            self.a, customer_type=Customer.TYPE_BUSINESS,
            business_name='Servicios SAC', first_name='', last_name='',
        )
        self.assertEqual(person.display_name, 'Ana Quispe')
        self.assertEqual(business.display_name, 'Servicios SAC')

    def test_archiving_keeps_the_record(self):
        customer = _p4_customer(self.a)
        customer.is_active = False
        customer.save()
        customer.refresh_from_db()
        self.assertFalse(customer.is_active)
        self.assertTrue(Customer.objects.filter(pk=customer.pk).exists())

    def test_a_customer_with_orders_cannot_be_deleted(self):
        """
        §25 and §77, enforced by the database rather than by a convention. The
        supported way to retire a client is `is_active = False`.
        """
        from django.db.models import ProtectedError

        customer = _p4_customer(self.a)
        order = _p3_order(self.a)
        order.customer = customer
        order.save(update_fields=['customer'])

        with self.assertRaises(ProtectedError):
            customer.delete()

    def test_the_document_vocabulary_is_shared_with_orders(self):
        """
        §12: one vocabulary, not two. A customer saved with a code an order
        cannot express would never match again, and the deterministic linking
        this phase depends on would fail silently.
        """
        from .models import DocumentType

        self.assertIs(Order.DocumentType, DocumentType)
        self.assertEqual(
            {c[0] for c in Customer._meta.get_field('document_type').choices},
            {c[0] for c in Order._meta.get_field('document_type').choices},
        )


class Phase4NormalizationTest(TestCase):
    """§16 — enough normalisation to make search work, and no more."""

    def test_email_is_lowercased_and_trimmed(self):
        self.assertEqual(normalize_customer_email('  Ana@Example.COM '), 'ana@example.com')

    def test_email_normalisation_does_not_get_clever(self):
        """
        Gmail-style dot and `+tag` stripping is deliberately NOT done: it is
        provider-specific folklore, wrong for some hosts, and here it would
        silently merge two people who typed two different addresses.
        """
        self.assertEqual(
            normalize_customer_email('a.n.a+tienda@example.com'),
            'a.n.a+tienda@example.com',
        )

    def test_phone_variants_land_on_one_string(self):
        for raw in ('+51 999 111 222', '+51999111222', ' +51-999-111-222 '):
            self.assertEqual(normalize_customer_phone(raw), '+51999111222')

    def test_phone_keeps_the_leading_plus_only(self):
        self.assertEqual(normalize_customer_phone('999 111 222'), '999111222')
        self.assertEqual(normalize_customer_phone(''), '')

    def test_document_is_trimmed_and_uppercased(self):
        self.assertEqual(normalize_document_number('  ce-x123 '), 'CE-X123')

    def test_the_model_normalises_on_save(self):
        company = _p3_company('p4-norm', 'Empresa Norm')
        customer = _p4_customer(
            company, email='  Ana@Example.COM ', phone='+51 999 111 222',
            document_type='ce', document_number=' ce-x123 ',
        )
        customer.refresh_from_db()
        self.assertEqual(customer.email, 'ana@example.com')
        self.assertEqual(customer.phone, '+51999111222')
        self.assertEqual(customer.document_number, 'CE-X123')


class Phase4DeduplicationTest(TestCase):
    """
    §17–19. The rule the whole module exists for: nobody is merged on a guess.
    """

    def setUp(self):
        cache.clear()
        self.company = _p3_company('p4-dedup', 'Empresa Dedup')

    def test_matching_by_account_is_exact(self):
        user = User.objects.create_user(username='p4_dedup_user', password='x')
        customer = _p4_customer(self.company, user=user)
        self.assertEqual(resolve_customer(self.company, user=user), customer)

    def test_matching_by_document_is_exact_and_normalised(self):
        customer = _p4_customer(self.company, document_type='dni', document_number='12345678')
        self.assertEqual(
            resolve_customer(self.company, document_type='dni', document_number=' 12345678 '),
            customer,
        )

    def test_the_account_wins_over_the_document(self):
        """
        Order matters. The account is the identity the person actually proved.
        """
        user = User.objects.create_user(username='p4_dedup_order', password='x')
        by_account = _p4_customer(self.company, user=user,
                                  document_type='dni', document_number='11111111')
        _p4_customer(self.company, document_type='dni', document_number='22222222')
        self.assertEqual(
            resolve_customer(self.company, user=user,
                             document_type='dni', document_number='22222222'),
            by_account,
        )

    def test_a_shared_email_never_matches(self):
        """
        §14, §73. Families, offices and assistants share an inbox. Treating that
        as identity would put one person's history in another person's file.
        """
        _p4_customer(self.company, email='familia@example.invalid',
                     document_type='dni', document_number='11111111')
        self.assertIsNone(
            resolve_customer(self.company, document_type='dni', document_number='99999999'),
        )

    def test_a_shared_phone_never_matches(self):
        _p4_customer(self.company, phone='+51999000000',
                     document_type='dni', document_number='11111111')
        self.assertIsNone(resolve_customer(self.company, document_type='', document_number=''))

    def test_a_similar_name_never_matches(self):
        _p4_customer(self.company, first_name='Juan', last_name='Pérez',
                     document_type='dni', document_number='11111111')
        self.assertIsNone(
            resolve_customer(self.company, document_type='dni', document_number='11111112'),
        )

    def test_a_shared_email_is_reported_as_a_suggestion(self):
        """
        §19: advisory, never automatic. Two records, and a human decides.
        """
        first = _p4_customer(self.company, email='familia@example.invalid',
                             document_type='dni', document_number='11111111')
        second = _p4_customer(self.company, email='familia@example.invalid',
                              document_type='dni', document_number='22222222')
        suggestions = find_possible_duplicates(
            self.company, email='familia@example.invalid', exclude_pk=second.pk,
        )
        self.assertEqual([c.pk for c in suggestions], [first.pk])

    def test_suggestions_do_not_cross_companies(self):
        other = _p3_company('p4-dedup-b', 'Empresa Dedup B')
        _p4_customer(other, email='cruce@example.invalid')
        self.assertEqual(
            find_possible_duplicates(self.company, email='cruce@example.invalid'), [],
        )

    def test_an_exact_duplicate_document_is_refused(self):
        """
        §72. This one DOES block: two records with the same document in one
        company are one client entered twice, and half the history goes to each.
        """
        existing = _p4_customer(self.company, document_type='dni', document_number='12345678')
        with self.assertRaises(DuplicateCustomerError) as ctx:
            assert_document_available(self.company, 'dni', '12345678')
        self.assertEqual(ctx.exception.existing.pk, existing.pk)

    def test_a_document_of_another_company_does_not_block(self):
        other = _p3_company('p4-dedup-c', 'Empresa Dedup C')
        _p4_customer(other, document_type='dni', document_number='12345678')
        assert_document_available(self.company, 'dni', '12345678')  # no raise

    def test_no_merge_function_is_exposed(self):
        """
        §20 — merging is PENDING, on purpose. It has to move orders and, in later
        phases, devices, repair orders and warranties. A merge that moves some of
        those and not the others is worse than no merge at all.
        """
        from . import customer_services

        exported = [n for n in dir(customer_services) if 'merge' in n.lower()]
        self.assertEqual(exported, [])


class Phase4OrderLinkTest(TestCase):
    """§42–44, §48–51 — the sale, the CRM record, and the snapshot between them."""

    def setUp(self):
        cache.clear()
        self.company = _p3_company('p4-link', 'Empresa Link')

    def test_checkout_data_creates_a_record_for_an_anonymous_buyer(self):
        order = _p3_order(self.company, document_type='dni', document_number='12345678')
        customer = link_order_to_customer(order)
        order.refresh_from_db()

        self.assertIsNotNone(customer)
        self.assertEqual(order.customer, customer)
        self.assertEqual(customer.company, self.company)
        self.assertIsNone(customer.user_id)
        self.assertEqual(customer.document_number, '12345678')

    def test_a_second_sale_with_the_same_document_reuses_the_record(self):
        first = link_order_to_customer(
            _p3_order(self.company, document_type='dni', document_number='12345678'),
        )
        second = link_order_to_customer(
            _p3_order(self.company, document_type='dni', document_number='12345678'),
        )
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(Customer.objects.filter(company=self.company).count(), 1)

    def test_an_authenticated_buyer_is_matched_by_account(self):
        user = User.objects.create_user(username='p4_link_user', password='x')
        existing = _p4_customer(self.company, user=user)
        order = _p3_order(self.company, user=user,
                          document_type='dni', document_number='87654321')
        self.assertEqual(link_order_to_customer(order), existing)

    def test_a_ruc_creates_a_business_record(self):
        """
        Inferred from the DOCUMENT, which is a fact about the document rather
        than a guess about the buyer: a RUC is issued to a business.
        """
        order = _p3_order(
            self.company, document_type='ruc', document_number='20123456789',
            customer_name='Servicios Generales SAC', receipt_type=Order.ReceiptType.FACTURA,
        )
        customer = link_order_to_customer(order)
        self.assertEqual(customer.customer_type, Customer.TYPE_BUSINESS)
        self.assertEqual(customer.business_name, 'Servicios Generales SAC')

    def test_linking_never_overwrites_the_existing_record(self):
        """
        A client may have deliberately corrected their details in the CRM. A new
        sale is not a reason to revert them to whatever the checkout form said.
        """
        existing = _p4_customer(
            self.company, document_type='dni', document_number='12345678',
            first_name='Ana', last_name='Quispe', phone='+51999888777',
        )
        order = _p3_order(self.company, document_type='dni', document_number='12345678',
                          customer_name='ANA Q', customer_phone='+51 111 111 111')
        link_order_to_customer(order)
        existing.refresh_from_db()
        self.assertEqual(existing.first_name, 'Ana')
        self.assertEqual(existing.phone, '+51999888777')

    def test_the_order_snapshot_is_never_rewritten_from_the_customer(self):
        """
        §43–44, the reason both exist. The client changes their phone today;
        last year's order must keep saying what it said when it was issued.
        """
        order = _p3_order(self.company, document_type='dni', document_number='12345678',
                          customer_phone='+51 999 111 222', customer_name='Ana Quispe')
        customer = link_order_to_customer(order)

        customer.phone = '+51999000000'
        customer.first_name = 'Anabel'
        customer.save()

        order.refresh_from_db()
        self.assertEqual(order.customer_phone, '+51 999 111 222')
        self.assertEqual(order.customer_name, 'Ana Quispe')
        self.assertEqual(order.customer, customer)

    def test_a_crm_failure_never_costs_the_sale(self):
        """
        §49. The order keeps every snapshot field it needs to be linked by hand
        afterwards; what it does not do is fail, or attach the wrong person.
        """
        order = _p3_order(self.company, document_type='dni', document_number='12345678')
        with patch('store.customer_services.resolve_customer', side_effect=RuntimeError('boom')):
            result = link_order_to_customer(order)
        order.refresh_from_db()
        self.assertIsNone(result)
        self.assertIsNone(order.customer_id)
        self.assertEqual(order.document_number, '12345678')

    def test_an_unlinked_customer_record_adopts_a_free_account(self):
        existing = _p4_customer(self.company, document_type='dni', document_number='12345678')
        user = User.objects.create_user(username='p4_adopt', password='x')
        order = _p3_order(self.company, user=user,
                          document_type='dni', document_number='12345678')
        link_order_to_customer(order)
        existing.refresh_from_db()
        self.assertEqual(existing.user, user)

    def test_an_account_already_used_here_is_not_stolen(self):
        """
        The unique on (company, user) must not be violated by the adoption path.
        """
        user = User.objects.create_user(username='p4_no_steal', password='x')
        _p4_customer(self.company, user=user, first_name='Titular')
        other = _p4_customer(self.company, document_type='dni', document_number='12345678')
        order = _p3_order(self.company, user=user,
                          document_type='dni', document_number='12345678')
        link_order_to_customer(order)
        other.refresh_from_db()
        self.assertIsNone(other.user_id)

    def test_the_checkout_flow_links_the_order_end_to_end(self):
        """The real path, through the view rather than the service."""
        import inspect

        from . import views

        source = inspect.getsource(views)
        self.assertIn('link_order_to_customer', source)


class Phase4ApiTest(TestCase):
    """§34–41, §82–83 — who reaches what, and what is unreachable."""

    def setUp(self):
        cache.clear()
        self.a = _p3_company('p4-api-a', 'Empresa API A')
        self.b = _p3_company('p4-api-b', 'Empresa API B')

        self.manager_a, _ = _p2d_member(self.a, 'p4_manager_a', ['company.view', _P4_VIEW, _P4_MANAGE])
        self.viewer_a, _ = _p2d_member(self.a, 'p4_viewer_a', ['company.view', _P4_VIEW])
        self.blind_a, _ = _p2d_member(self.a, 'p4_blind_a', ['company.view'])
        self.manager_b, _ = _p2d_member(self.b, 'p4_manager_b', ['company.view', _P4_VIEW, _P4_MANAGE])
        self.outsider = User.objects.create_user(username='p4_outsider', password='x')

        self.customer_a = _p4_customer(self.a, first_name='Ana', last_name='Quispe',
                                       document_type='dni', document_number='11111111')
        self.customer_b = _p4_customer(self.b, first_name='Beto', last_name='Ramos',
                                       document_type='dni', document_number='22222222')

    def _as(self, user):
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    # -- reading ---------------------------------------------------------

    def test_a_viewer_lists_their_own_customers(self):
        res = self._as(self.viewer_a).get('/api/admin/customers/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual([r['id'] for r in res.data['results']], [self.customer_a.pk])
        self.assertFalse(res.data['can_manage'])

    def test_the_list_never_contains_another_companys_customers(self):
        res = self._as(self.manager_a).get('/api/admin/customers/?page_size=100')
        ids = {r['id'] for r in res.data['results']}
        self.assertIn(self.customer_a.pk, ids)
        self.assertNotIn(self.customer_b.pk, ids)

    def test_the_list_row_does_not_carry_internal_notes(self):
        """
        §55. A list is skimmed at a counter, sometimes with the client on the
        other side of it.
        """
        self.customer_a.notes = 'Reclamó por garantía, trato difícil.'
        self.customer_a.save()
        res = self._as(self.manager_a).get('/api/admin/customers/')
        self.assertNotIn('notes', res.data['results'][0])
        self.assertNotIn('difícil', json.dumps(res.data))

    def test_reading_another_companys_customer_is_a_404(self):
        res = self._as(self.manager_a).get(f'/api/admin/customers/{self.customer_b.pk}/')
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_writing_another_companys_customer_is_a_404(self):
        res = self._as(self.manager_a).patch(
            f'/api/admin/customers/{self.customer_b.pk}/',
            {'first_name': 'Secuestrado'}, format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
        self.customer_b.refresh_from_db()
        self.assertEqual(self.customer_b.first_name, 'Beto')

    def test_archiving_another_companys_customer_is_a_404(self):
        res = self._as(self.manager_a).patch(
            f'/api/admin/customers/{self.customer_b.pk}/',
            {'is_active': False}, format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
        self.customer_b.refresh_from_db()
        self.assertTrue(self.customer_b.is_active)

    def test_a_company_parameter_cannot_reach_another_tenant(self):
        res = self._as(self.manager_a).get(f'/api/admin/customers/?company={self.b.pk}')
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_searching_another_companys_document_finds_nothing(self):
        """§82. Not 'finds it but hides it' — the queryset never contained it."""
        res = self._as(self.manager_a).get('/api/admin/customers/?search=22222222')
        self.assertEqual(res.data['count'], 0)

    # -- capabilities ----------------------------------------------------

    def test_without_the_view_capability_the_list_is_refused(self):
        res = self._as(self.blind_a).get('/api/admin/customers/')
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_a_viewer_cannot_create(self):
        res = self._as(self.viewer_a).post(
            '/api/admin/customers/', {'first_name': 'Nuevo'}, format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_a_viewer_cannot_edit(self):
        res = self._as(self.viewer_a).patch(
            f'/api/admin/customers/{self.customer_a.pk}/',
            {'first_name': 'Editado'}, format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_a_user_with_no_membership_is_refused(self):
        res = self._as(self.outsider).get('/api/admin/customers/')
        self.assertIn(
            res.status_code, (status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND),
        )

    def test_anonymous_is_refused(self):
        res = APIClient().get('/api/admin/customers/')
        self.assertIn(
            res.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
        )

    def test_there_is_no_public_customer_endpoint(self):
        """
        §26, §61. The strongest guarantee available: the route does not exist.
        """
        for path in ('/api/customers/', '/api/customers/1/', '/api/storefront/customers/'):
            res = APIClient().get(path)
            self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND, path)

    # -- writing ---------------------------------------------------------

    def test_a_manager_creates_a_customer(self):
        res = self._as(self.manager_a).post(
            '/api/admin/customers/',
            {'first_name': 'Nuevo', 'last_name': 'Cliente', 'phone': '+51 999 555 444'},
            format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        created = Customer.objects.get(pk=res.data['id'])
        self.assertEqual(created.company, self.a)
        self.assertEqual(created.created_by, self.manager_a)
        self.assertEqual(created.phone, '+51999555444')

    def test_a_created_customer_belongs_to_the_resolved_company_not_the_body(self):
        """
        §37. `company` is not a field on the write serializer at all, which is
        stronger than validating it away.
        """
        res = self._as(self.manager_a).post(
            '/api/admin/customers/',
            {'first_name': 'Intruso', 'company': self.b.pk}, format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Customer.objects.get(pk=res.data['id']).company, self.a)

    def test_a_duplicate_document_returns_a_conflict_with_the_existing_record(self):
        res = self._as(self.manager_a).post(
            '/api/admin/customers/',
            {'first_name': 'Otra', 'document_type': 'dni', 'document_number': '11111111'},
            format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(res.data['existing']['id'], self.customer_a.pk)

    def test_a_shared_email_is_allowed_and_reported(self):
        """§73: allowed, with a warning a human can act on."""
        self.customer_a.email = 'familia@example.invalid'
        self.customer_a.save()
        res = self._as(self.manager_a).post(
            '/api/admin/customers/',
            {'first_name': 'Hermano', 'email': 'familia@example.invalid'}, format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(
            [d['id'] for d in res.data['possible_duplicates']], [self.customer_a.pk],
        )

    def test_a_shared_phone_is_allowed_and_reported(self):
        self.customer_a.phone = '+51999000000'
        self.customer_a.save()
        res = self._as(self.manager_a).post(
            '/api/admin/customers/',
            {'first_name': 'Oficina', 'phone': '+51 999 000 000'}, format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertTrue(res.data['possible_duplicates'])

    def test_a_manager_edits_a_customer(self):
        res = self._as(self.manager_a).patch(
            f'/api/admin/customers/{self.customer_a.pk}/',
            {'first_name': 'Anabel', 'notes': 'Prefiere WhatsApp.'}, format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.customer_a.refresh_from_db()
        self.assertEqual(self.customer_a.first_name, 'Anabel')

    def test_editing_into_an_existing_document_is_refused(self):
        other = _p4_customer(self.a, first_name='Otro', document_type='dni',
                             document_number='33333333')
        res = self._as(self.manager_a).patch(
            f'/api/admin/customers/{other.pk}/',
            {'document_type': 'dni', 'document_number': '11111111'}, format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_409_CONFLICT)
        other.refresh_from_db()
        self.assertEqual(other.document_number, '33333333')

    def test_a_customer_keeping_its_own_document_is_not_a_conflict(self):
        res = self._as(self.manager_a).patch(
            f'/api/admin/customers/{self.customer_a.pk}/',
            {'document_type': 'dni', 'document_number': '11111111',
             'first_name': 'Anabel'}, format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)

    def test_the_company_of_a_customer_is_not_editable(self):
        """§39: not rejected by a check — simply not a field."""
        res = self._as(self.manager_a).patch(
            f'/api/admin/customers/{self.customer_a.pk}/',
            {'company': self.b.pk, 'first_name': 'Movida'}, format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.customer_a.refresh_from_db()
        self.assertEqual(self.customer_a.company, self.a)

    def test_created_by_is_not_editable(self):
        res = self._as(self.manager_a).patch(
            f'/api/admin/customers/{self.customer_a.pk}/',
            {'created_by': self.manager_b.pk, 'first_name': 'X'}, format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.customer_a.refresh_from_db()
        self.assertNotEqual(self.customer_a.created_by, self.manager_b)

    def test_the_account_link_is_not_editable_through_the_form(self):
        """
        §40. Linking an account is not a text field on a CRM form; letting it be
        one would allow attaching somebody else's login to a client record.
        """
        res = self._as(self.manager_a).patch(
            f'/api/admin/customers/{self.customer_a.pk}/',
            {'user': self.manager_b.pk, 'first_name': 'X'}, format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.customer_a.refresh_from_db()
        self.assertIsNone(self.customer_a.user_id)

    def test_there_is_no_delete_verb(self):
        """
        §25: a DELETE that silently archived would be a lie in the URL.
        """
        res = self._as(self.manager_a).delete(f'/api/admin/customers/{self.customer_a.pk}/')
        self.assertEqual(res.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    # -- archive / search / history ---------------------------------------

    def test_archiving_and_reactivating(self):
        client = self._as(self.manager_a)
        archived = client.patch(
            f'/api/admin/customers/{self.customer_a.pk}/', {'is_active': False}, format='json',
        )
        self.assertEqual(archived.status_code, status.HTTP_200_OK)
        self.assertFalse(archived.data['is_active'])

        hidden = client.get('/api/admin/customers/')
        self.assertEqual(hidden.data['count'], 0)

        listed = client.get('/api/admin/customers/?state=archived')
        self.assertEqual([r['id'] for r in listed.data['results']], [self.customer_a.pk])

        detail = client.get(f'/api/admin/customers/{self.customer_a.pk}/')
        self.assertEqual(detail.status_code, status.HTTP_200_OK)

        back = client.patch(
            f'/api/admin/customers/{self.customer_a.pk}/', {'is_active': True}, format='json',
        )
        self.assertTrue(back.data['is_active'])

    def test_search_finds_by_every_supported_field(self):
        customer = _p4_customer(
            self.a, first_name='Rosa', last_name='Mendoza',
            document_type='dni', document_number='44444444',
            phone='+51987654321', email='rosa@example.invalid',
        )
        client = self._as(self.manager_a)
        for term in ('Rosa', 'Mendoza', '4444', '987654321', 'rosa@example'):
            res = client.get(f'/api/admin/customers/?search={term}')
            ids = [r['id'] for r in res.data['results']]
            self.assertIn(customer.pk, ids, f'no encontró por {term!r}')

    def test_search_normalises_a_typed_phone_number(self):
        """
        Without this, every phone search would silently return nothing and look
        like an empty CRM.
        """
        customer = _p4_customer(self.a, first_name='Tel', phone='+51987654321')
        res = self._as(self.manager_a).get('/api/admin/customers/?search=%2B51 987 654 321')
        self.assertIn(customer.pk, [r['id'] for r in res.data['results']])

    def test_search_finds_a_business_by_its_registered_name(self):
        customer = _p4_customer(
            self.a, customer_type=Customer.TYPE_BUSINESS,
            business_name='Importaciones del Sur', first_name='', last_name='',
        )
        res = self._as(self.manager_a).get('/api/admin/customers/?search=Importaciones')
        self.assertIn(customer.pk, [r['id'] for r in res.data['results']])

    def test_the_detail_reports_paid_history_separately(self):
        """§51: an unpaid order is intent, not money."""
        paid = _p3_order(self.a, total=Decimal('250.00'))
        paid.customer = self.customer_a
        paid.save(update_fields=['customer'])

        unpaid = _p3_order(self.a, total=Decimal('999.00'), paid=False,
                           status=Order.Status.PENDING_PAYMENT, paid_at=None)
        unpaid.customer = self.customer_a
        unpaid.save(update_fields=['customer'])

        res = self._as(self.manager_a).get(f'/api/admin/customers/{self.customer_a.pk}/')
        summary = res.data['summary']
        self.assertEqual(summary['orders_total'], 2)
        self.assertEqual(summary['paid_orders'], 1)
        self.assertEqual(Decimal(summary['paid_amount']), Decimal('250.00'))
        self.assertIsNotNone(summary['last_purchase_at'])

    def test_the_history_carries_no_payment_internals(self):
        order = _p3_order(self.a, stripe_payment_intent_id='pi_secret_123')
        order.customer = self.customer_a
        order.save(update_fields=['customer'])
        res = self._as(self.manager_a).get(f'/api/admin/customers/{self.customer_a.pk}/')
        # The RENDERED body, not `res.data`: what leaks is what goes on the wire.
        blob = res.content.decode()
        self.assertNotIn('pi_secret_123', blob)
        self.assertNotIn('stripe', blob.lower())

    def test_the_history_shows_the_orders_own_snapshot(self):
        order = _p3_order(self.a, customer_name='Ana Quispe',
                          customer_phone='+51 999 111 222')
        order.customer = self.customer_a
        order.save(update_fields=['customer'])

        self.customer_a.first_name = 'Anabel'
        self.customer_a.phone = '+51999000000'
        self.customer_a.save()

        res = self._as(self.manager_a).get(f'/api/admin/customers/{self.customer_a.pk}/')
        row = res.data['orders'][0]
        self.assertEqual(row['customer_name'], 'Ana Quispe')
        self.assertEqual(row['customer_phone'], '+51 999 111 222')


class Phase4AuditPrivacyTest(TestCase):
    """§62–63 — the trail records WHAT changed, never the client's data."""

    def setUp(self):
        cache.clear()
        self.company = _p3_company('p4-audit', 'Empresa Audit')
        self.manager, _ = _p2d_member(
            self.company, 'p4_audit_manager', ['company.view', _P4_VIEW, _P4_MANAGE],
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.manager)

    def _blob(self):
        return json.dumps(
            [e.metadata for e in AdminAuditLog.objects.filter(target_type='customer')]
        )

    def test_creating_is_logged(self):
        AdminAuditLog.objects.all().delete()
        res = self.client.post(
            '/api/admin/customers/', {'first_name': 'Nuevo', 'last_name': 'Cliente'},
            format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        entry = AdminAuditLog.objects.filter(action='customer_created').first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.actor, self.manager)
        self.assertEqual(entry.company, self.company)
        self.assertEqual(entry.metadata['customer_id'], res.data['id'])

    def test_editing_logs_field_names_only(self):
        customer = _p4_customer(self.company)
        AdminAuditLog.objects.all().delete()
        self.client.patch(
            f'/api/admin/customers/{customer.pk}/',
            {'first_name': 'Anabel', 'phone': '+51 999 123 456'}, format='json',
        )
        entry = AdminAuditLog.objects.filter(action='customer_updated').first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.metadata['changed_fields'], ['first_name', 'phone'])
        self.assertNotIn('Anabel', self._blob())
        self.assertNotIn('999123456', self._blob())

    def test_archiving_and_reactivating_have_their_own_actions(self):
        customer = _p4_customer(self.company)
        AdminAuditLog.objects.all().delete()
        self.client.patch(
            f'/api/admin/customers/{customer.pk}/', {'is_active': False}, format='json',
        )
        self.client.patch(
            f'/api/admin/customers/{customer.pk}/', {'is_active': True}, format='json',
        )
        actions = list(
            AdminAuditLog.objects.filter(target_type='customer')
            .order_by('pk').values_list('action', flat=True)
        )
        self.assertEqual(actions, ['customer_archived', 'customer_reactivated'])

    def test_a_patch_that_changes_nothing_is_not_logged(self):
        customer = _p4_customer(self.company, first_name='Ana', last_name='Quispe')
        AdminAuditLog.objects.all().delete()
        res = self.client.patch(
            f'/api/admin/customers/{customer.pk}/',
            {'first_name': 'Ana', 'last_name': 'Quispe'}, format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertFalse(AdminAuditLog.objects.filter(target_type='customer').exists())

    def test_the_trail_never_carries_the_clients_pii(self):
        """
        §63. An audit table is read by more people and purged by nobody; copying
        a document number into it creates a second, longer-lived store of the
        exact data this model exists to protect.
        """
        AdminAuditLog.objects.all().delete()
        self.client.post(
            '/api/admin/customers/',
            {
                'first_name': 'Rosa', 'last_name': 'Mendoza',
                'document_type': 'dni', 'document_number': '44444444',
                'phone': '+51987654321', 'email': 'rosa@example.invalid',
                'notes': 'Nota interna delicada.',
            },
            format='json',
        )
        blob = self._blob()
        for secret in ('44444444', '987654321', 'rosa@example', 'delicada', 'Mendoza'):
            self.assertNotIn(secret, blob, f'la auditoría filtró {secret!r}')
        # What it DOES record is enough to investigate.
        entry = AdminAuditLog.objects.filter(action='customer_created').first()
        self.assertTrue(entry.metadata['has_document'])
        self.assertEqual(entry.metadata['company_id'], self.company.pk)


class Phase4CapabilityTest(TestCase):
    """§28–31 — the capabilities become real without widening anyone's access."""

    def test_the_customer_capabilities_are_active(self):
        from .capabilities import CAPABILITIES, STATUS_ACTIVE

        for code in (_P4_VIEW, _P4_MANAGE):
            self.assertEqual(CAPABILITIES[code].status, STATUS_ACTIVE, code)

    def test_they_are_assignable(self):
        from .capabilities import ASSIGNABLE_CAPABILITY_CODES

        self.assertIn(_P4_VIEW, ASSIGNABLE_CAPABILITY_CODES)
        self.assertIn(_P4_MANAGE, ASSIGNABLE_CAPABILITY_CODES)

    def test_service_manage_is_not_an_umbrella_over_them(self):
        """
        §29. `service.manage` is the Phase 2A membership-in-technical-service
        concept. Letting it silently absorb every capability the service module
        ever adds would make it exactly the implicit super-permission the
        catalogue exists to replace.
        """
        company = _p3_company('p4-cap-umbrella', 'Empresa Umbrella')
        user, _ = _p2d_member(company, 'p4_umbrella', ['company.view', 'service.manage'])
        self.assertFalse(has_capability(user, company, _P4_VIEW))
        self.assertFalse(has_capability(user, company, _P4_MANAGE))

    def test_a_new_company_grants_them_to_its_administrator_preset(self):
        company = _p3_company('p4-cap-new', 'Empresa Nueva')
        role = CompanyRole.objects.get(company=company, slug='administrador')
        self.assertIn(_P4_VIEW, role.capabilities)
        self.assertIn(_P4_MANAGE, role.capabilities)

    def test_a_new_companys_technician_may_view_but_not_manage(self):
        """
        §30. A technician needs to know whose device is on the bench. Deciding
        what the client file says is a different job, and a company that wants
        that grants it — one checkbox, and a decision the business makes.
        """
        company = _p3_company('p4-cap-tech', 'Empresa Técnica')
        role = CompanyRole.objects.get(company=company, slug='servicio-tecnico')
        self.assertIn(_P4_VIEW, role.capabilities)
        self.assertNotIn(_P4_MANAGE, role.capabilities)

    def test_a_legacy_admin_membership_resolves_them_live(self):
        company = _p3_company('p4-cap-legacy', 'Empresa Legacy')
        user = User.objects.create_user(username='p4_legacy_admin', password='x')
        Membership.objects.create(user=user, company=company, role='admin', is_active=True)
        self.assertTrue(has_capability(user, company, _P4_VIEW))

    def test_a_customised_role_is_never_widened_by_the_migration(self):
        """
        §31, and the reason the discriminator is EXACT equality.

        A role a tenant has deliberately shaped is theirs. Writing new authority
        into it because software shipped would widen access somebody chose to
        narrow, silently, in a migration.
        """
        import importlib

        company = _p3_company('p4-cap-custom', 'Empresa Custom')
        role = CompanyRole.objects.get(company=company, slug='administrador')
        # A tenant that removed one capability: no longer the platform's preset.
        role.capabilities = sorted(
            set(role.capabilities) - {_P4_VIEW, _P4_MANAGE, 'inventory.adjust'}
        )
        role.save()

        module = importlib.import_module(
            'store.migrations.0033_customer_capabilities_for_untouched_admin_presets'
        )
        module.grant(django_apps, None)

        role.refresh_from_db()
        self.assertNotIn(_P4_VIEW, role.capabilities)
        self.assertNotIn(_P4_MANAGE, role.capabilities)

    def test_an_untouched_admin_preset_is_upgraded_by_the_migration(self):
        import importlib

        from .capabilities import ASSIGNABLE_CAPABILITY_CODES

        company = _p3_company('p4-cap-untouched', 'Empresa Intacta')
        role = CompanyRole.objects.get(company=company, slug='administrador')
        # Rewound to what the preset granted BEFORE this phase.
        role.capabilities = sorted(
            set(ASSIGNABLE_CAPABILITY_CODES) - {_P4_VIEW, _P4_MANAGE}
        )
        role.save()

        module = importlib.import_module(
            'store.migrations.0033_customer_capabilities_for_untouched_admin_presets'
        )
        module.grant(django_apps, None)

        role.refresh_from_db()
        self.assertIn(_P4_VIEW, role.capabilities)
        self.assertIn(_P4_MANAGE, role.capabilities)


class Phase4BranchScopeTest(TestCase):
    """
    §84 — a customer is company-level, and branch access does not narrow it.
    """

    def test_a_branch_restricted_user_still_sees_every_customer(self):
        """
        A client buys in one shop, leaves a laptop at another and collects it at
        a third. Scoping master data by branch would fragment one person into
        three files and break the history this module exists to keep.

        Repair orders WILL be branch-scoped in a later phase. That is an
        operation on a customer, not the customer.
        """
        company = _p3_company('p4-branch', 'Empresa Sucursal')
        first = company.default_inventory_branch
        Branch.objects.create(company=company, name='Sucursal 2')

        customer = _p4_customer(company, first_name='Cliente', last_name='Compartido')
        restricted, _ = _p2d_member(
            company, 'p4_branch_user', ['company.view', _P4_VIEW],
            mode='selected', branches=[first],
        )
        client = APIClient()
        client.force_authenticate(user=restricted)

        res = client.get('/api/admin/customers/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn(customer.pk, [r['id'] for r in res.data['results']])

    def test_the_model_has_no_branch_field(self):
        self.assertNotIn(
            'branch', [f.name for f in Customer._meta.get_fields()],
        )


class Phase4MigrationTest(TestCase):
    """
    §45–47, §69 — what the backfill promises about orders that already exist.

    The upgrade itself was verified end-to-end against a database populated at
    0030 and migrated forward. These pin the rules so a later edit cannot quietly
    reintroduce a merge.
    """

    def _module(self):
        import importlib

        return importlib.import_module('store.migrations.0032_backfill_customers')

    def test_the_backfill_never_merges_by_name_email_or_phone(self):
        """
        A wrong merge publishes one client's address, history and internal notes
        inside another client's file, permanently, in a migration nobody
        re-reads. The rule is enforced by what the code does NOT look at.
        """
        import inspect

        source = inspect.getsource(self._module())
        body = source.split('def backfill')[1]
        for forbidden in ('customer_email=', 'customer_phone=', 'customer_name__'):
            self.assertNotIn(
                f'filter({forbidden}', body,
                f'la migración agrupa por {forbidden!r}, que no es identidad',
            )

    def test_business_shape_is_inferred_from_the_document_not_the_name(self):
        module = self._module()

        class FakeOrder:
            document_type = 'ruc'
            customer_name = 'Servicios SAC'

        fields = module._identity_from_order(FakeOrder())
        self.assertEqual(fields['customer_type'], 'business')
        self.assertEqual(fields['business_name'], 'Servicios SAC')

        class PersonOrder:
            document_type = 'dni'
            customer_name = 'Ana Quispe'

        fields = module._identity_from_order(PersonOrder())
        self.assertEqual(fields['customer_type'], 'person')
        self.assertEqual((fields['first_name'], fields['last_name']), ('Ana', 'Quispe'))

    def test_the_identity_guard_mirrors_the_check_constraint(self):
        module = self._module()
        empty = {'first_name': '', 'last_name': '', 'business_name': ''}
        self.assertFalse(module._has_identity(empty, ''))
        self.assertTrue(module._has_identity(empty, '12345678'))
        self.assertTrue(module._has_identity({**empty, 'first_name': 'Ana'}, ''))

    def test_the_backfill_normalisers_agree_with_the_model(self):
        """
        A migration cannot import the model's helpers — historical models have no
        methods — so it carries its own copies. If they drifted, records written
        by the backfill would never match records written by the application.
        """
        module = self._module()
        for raw in ('+51 999 111 222', '999111222', '', ' +51-999-111-222 '):
            self.assertEqual(module._norm_phone(raw), normalize_customer_phone(raw), raw)
        for raw in ('  ce-x123 ', 'DNI1', ''):
            self.assertEqual(module._norm_doc(raw), normalize_document_number(raw), raw)
        for raw in ('  Ana@Example.COM ', ''):
            self.assertEqual(module._norm_email(raw), normalize_customer_email(raw), raw)

    def test_an_ambiguous_order_is_left_unlinked(self):
        """
        §46. An unlinked order is visibly incomplete and fixable by hand. A
        wrongly linked one looks correct forever.
        """
        company = _p3_company('p4-mig-ambiguous', 'Empresa Ambigua')
        order = _p3_order(company, document_type='', document_number='',
                          customer_name='Anónimo', customer_email='anon@example.invalid')
        self.assertIsNone(order.customer_id)
        self.assertEqual(order.customer_name, 'Anónimo')


# ===========================================================================
# P0 — runtime stabilisation regression
# ===========================================================================

class P0RuntimeRegressionTest(TestCase):
    """
    The four surfaces that were answering 500 on localhost.

    HONEST NOTE ABOUT WHAT THESE TESTS CAN AND CANNOT DO
    ----------------------------------------------------
    These would have PASSED throughout the incident. The failure was not in the
    code: the development database was ten migrations behind, and Django builds a
    FRESH, fully-migrated database for every test run. A green suite says "this
    code agrees with these migrations"; it says nothing about the database the
    server is connected to.

    So these pin the CONTRACT — the shape of a correct response, not merely a
    status that is not 500. The drift itself is caught by `store.checks`, which
    is an environment check rather than a test, and by its own test below.
    """

    def setUp(self):
        cache.clear()
        self.company = _p3_company('p0-runtime', 'Empresa Runtime')
        self.product = _seeded(Product.objects.create(
            company=self.company, name='Producto P0', slug='producto-p0',
            price=Decimal('100.00'), inventory=5,
        ))

    @override_settings(DEFAULT_STOREFRONT_COMPANY_SLUG='p0-runtime')
    def test_storefront_product_list_returns_the_catalogue(self):
        res = APIClient().get('/api/products/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn(self.product.pk, [p['id'] for p in res.data])

    @override_settings(DEFAULT_STOREFRONT_COMPANY_SLUG='p0-runtime')
    def test_storefront_config_returns_the_tenants_branding(self):
        res = APIClient().get('/api/storefront/config/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['company']['slug'], 'p0-runtime')

    @override_settings(DEFAULT_STOREFRONT_COMPANY_SLUG='p0-runtime')
    def test_an_empty_cart_returns_an_empty_list(self):
        """
        §19. A valid cart with nothing in it is the most ordinary request the
        storefront makes, and it was answering 500.
        """
        res = APIClient().get('/api/cart/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(list(res.data), [])

    @override_settings(DEFAULT_STOREFRONT_COMPANY_SLUG='p0-runtime')
    def test_a_cart_holding_an_inactive_product_still_loads(self):
        """
        §20. A legacy cart row whose product was later unpublished must not take
        the whole GET down with it — one stale row is not a reason to make the
        cart unreachable.
        """
        client = APIClient()
        added = client.post(
            '/api/cart/add/',
            {'session_key': 'p0-session', 'product': self.product.pk, 'quantity': 1},
            format='json',
        )
        self.assertIn(added.status_code, (status.HTTP_200_OK, status.HTTP_201_CREATED))

        self.product.is_active = False
        self.product.save(update_fields=['is_active'])

        res = client.get('/api/cart/?session_key=p0-session')
        self.assertEqual(res.status_code, status.HTTP_200_OK)

    @override_settings(DEFAULT_STOREFRONT_COMPANY_SLUG='p0-runtime')
    def test_the_raw_cart_create_route_is_closed(self):
        """
        Found while writing these tests: `CreateModelMixin` exposed
        `POST /api/cart/`, and it answered 500 for every input because the
        serializer's `product` is read-only.

        It is refused rather than repaired. Repairing it would mean a second way
        to write a CartItem that does not scope the product to this storefront,
        does not require a session key and does not check stock — which is the
        cross-tenant vector `add` exists to close.
        """
        res = APIClient().post(
            '/api/cart/', {'product': self.product.pk, 'quantity': 1}, format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertEqual(CartItem.objects.count(), 0)

    def test_the_internal_dashboard_loads_for_a_company_admin(self):
        user, _ = _p2d_member(
            self.company, 'p0_admin', ['company.view', 'products.view'],
        )
        client = APIClient()
        client.force_authenticate(user=user)
        res = client.get('/api/me/internal-dashboard/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['company']['slug'], 'p0-runtime')
        self.assertFalse(res.data['requires_company_selection'])

    def test_a_missing_capability_does_not_take_down_the_whole_dashboard(self):
        """
        §22. Somebody without `inventory.view` gets a dashboard WITHOUT the
        inventory block — not an error page. One locked block must not cost the
        other nine.
        """
        user, _ = _p2d_member(self.company, 'p0_partial', ['company.view'])
        client = APIClient()
        client.force_authenticate(user=user)
        res = client.get('/api/me/internal-dashboard/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIsNotNone(res.data['company'])
        self.assertIsNone(res.data['inventory'])

    def test_the_master_dashboard_requires_an_explicit_company(self):
        """
        §23. MASTER gets a choice, never a platform-wide aggregate.
        """
        master = User.objects.create_superuser(
            username='p0_master', email='p0@example.invalid', password='x',
        )
        client = APIClient()
        client.force_authenticate(user=master)

        res = client.get('/api/me/internal-dashboard/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIsNone(res.data['company'])
        self.assertTrue(res.data['requires_company_selection'])
        self.assertIsNone(res.data['inventory'])
        self.assertIn(
            self.company.pk, [c['id'] for c in res.data['available_companies']],
        )

    def test_the_master_dashboard_loads_for_a_selected_company(self):
        master = User.objects.create_superuser(
            username='p0_master_sel', email='p0s@example.invalid', password='x',
        )
        client = APIClient()
        client.force_authenticate(user=master)
        res = client.get(f'/api/me/internal-dashboard/?company={self.company.pk}')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['company']['slug'], 'p0-runtime')
        self.assertFalse(res.data['requires_company_selection'])

    def test_an_unauthenticated_internal_request_is_401_not_500(self):
        """
        §26. An authorisation problem must answer as one. A 500 there hides a
        working guard behind what looks like a broken server.
        """
        for path in ('/api/me/internal-dashboard/', '/api/admin/customers/',
                     '/api/admin/sequences/', '/api/admin/products/'):
            res = APIClient().get(path)
            self.assertIn(
                res.status_code,
                (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
                path,
            )


class P0SchemaDriftCheckTest(TestCase):
    """
    The guard that WOULD have caught the incident, and that the suite cannot.
    """

    def test_the_check_is_registered_under_the_database_tag(self):
        from django.core.checks import registry

        from .checks import check_pending_migrations

        self.assertIn(check_pending_migrations, registry.registry.get_checks())

    def test_a_fully_migrated_database_produces_no_warning(self):
        from .checks import check_pending_migrations

        self.assertEqual(check_pending_migrations(None), [])

    def test_a_database_behind_the_code_is_reported(self):
        """
        Simulated by handing the executor a plan that is not empty — the same
        shape a development database ten migrations behind produces.
        """
        from unittest.mock import patch

        from .checks import check_pending_migrations

        class FakeMigration:
            app_label = 'store'
            name = '0024_multibranch_inventory_nullable'

        with patch(
            'django.db.migrations.executor.MigrationExecutor.migration_plan',
            return_value=[(FakeMigration(), False)],
        ):
            issues = check_pending_migrations(None)

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].id, 'store.W001')
        self.assertIn('0024_multibranch_inventory_nullable', issues[0].hint)
        self.assertIn('manage.py migrate', issues[0].hint)

    def test_the_check_never_applies_anything(self):
        """
        A check that migrated on its own would be worse than the failure it
        replaces: migrations are a deployment decision, some carry data changes,
        and 0025 is designed to stop and ask rather than guess.
        """
        import inspect

        from . import checks

        source = inspect.getsource(checks)
        for forbidden in ('call_command', 'migrate_all', 'executor.migrate('):
            self.assertNotIn(forbidden, source)

    def test_an_unreachable_database_does_not_produce_a_misleading_warning(self):
        from unittest.mock import patch

        from .checks import check_pending_migrations

        with patch(
            'django.db.migrations.executor.MigrationExecutor.__init__',
            side_effect=Exception('connection refused'),
        ):
            self.assertEqual(check_pending_migrations(None), [])


# =============================================================================
# BR-002 / BR-007 — versioned PUBLIC catalogue for native clients: /api/v1/
# =============================================================================
#
# The tenant is named in the PATH here, not derived from the Host, because a
# mobile app reaches one shared API host and has no Host to be identified by.
# That makes the slug a client-supplied value, so the whole point of these
# tests is to pin down what it can and cannot do: it SELECTS a public shop
# window, and it authorizes nothing.


def _v1(company_slug, resource='products', suffix=''):
    return f'/api/v1/storefront/{company_slug}/{resource}/{suffix}'


class V1PublicCatalogIsolationTest(TestCase):
    """Two tenants, the same slugs, one shared API host."""

    def setUp(self):
        cache.clear()
        self.a = _saas_company('Empresa A', 'v1-a', tax_id='20777100001')
        self.b = _saas_company('Empresa B', 'v1-b', tax_id='20777100002')

        self.cat_a = _cat(self.a, 'iPhone', 'iphone')
        self.cat_b = _cat(self.b, 'iPhone', 'iphone')
        self.prod_a = _prod(self.a, 'iPhone 15 A', 'iphone-15', category=self.cat_a)
        self.prod_b = _prod(self.b, 'iPhone 15 B', 'iphone-15', category=self.cat_b)
        self.client = APIClient()

    # --- lists ---------------------------------------------------------------

    def test_tenant_a_lists_only_its_own_products(self):
        names = {p['name'] for p in self.client.get(_v1('v1-a')).json()}
        self.assertIn('iPhone 15 A', names)
        self.assertNotIn('iPhone 15 B', names)

    def test_tenant_b_lists_only_its_own_products(self):
        names = {p['name'] for p in self.client.get(_v1('v1-b')).json()}
        self.assertIn('iPhone 15 B', names)
        self.assertNotIn('iPhone 15 A', names)

    def test_tenant_a_lists_only_its_own_categories(self):
        rows = self.client.get(_v1('v1-a', 'categories')).json()
        self.assertTrue(rows)
        self.assertEqual({c['id'] for c in rows}, {self.cat_a.id})

    def test_categories_of_two_tenants_never_overlap(self):
        ids_a = {c['id'] for c in self.client.get(_v1('v1-a', 'categories')).json()}
        ids_b = {c['id'] for c in self.client.get(_v1('v1-b', 'categories')).json()}
        self.assertFalse(ids_a & ids_b)

    # --- detail --------------------------------------------------------------

    def test_the_same_slug_resolves_per_tenant(self):
        # The whole reason `unique_product_slug_per_company` exists: "iphone-15"
        # is a different product in each company, and the PATH decides which.
        a = self.client.get(_v1('v1-a', 'products', 'iphone-15/')).json()
        b = self.client.get(_v1('v1-b', 'products', 'iphone-15/')).json()
        self.assertEqual(a['name'], 'iPhone 15 A')
        self.assertEqual(b['name'], 'iPhone 15 B')

    def test_a_slug_only_tenant_b_owns_is_404_under_tenant_a(self):
        _prod(self.b, 'Exclusivo B', 'solo-de-b', category=self.cat_b)
        self.assertEqual(self.client.get(_v1('v1-a', 'products', 'solo-de-b/')).status_code, 404)
        self.assertEqual(self.client.get(_v1('v1-b', 'products', 'solo-de-b/')).status_code, 200)

    def test_a_numeric_id_from_another_tenant_is_not_an_address(self):
        # Lookup is by slug, so a leaked primary key is not even well-formed here.
        self.assertEqual(
            self.client.get(_v1('v1-a', 'products', f'{self.prod_b.id}/')).status_code, 404,
        )

    def test_inactive_products_are_not_exposed(self):
        _prod(self.a, 'Retirado', 'retirado', category=self.cat_a, is_active=False)
        slugs = {p['slug'] for p in self.client.get(_v1('v1-a')).json()}
        self.assertNotIn('retirado', slugs)
        self.assertEqual(self.client.get(_v1('v1-a', 'products', 'retirado/')).status_code, 404)

    # --- filters cannot widen the scope --------------------------------------

    def test_a_category_slug_shared_by_both_filters_within_the_path_tenant(self):
        rows = self.client.get(_v1('v1-a') + '?category=iphone').json()
        self.assertEqual({p['name'] for p in rows}, {'iPhone 15 A'})

    def test_filtering_by_a_category_only_tenant_b_owns_returns_nothing(self):
        cat = _cat(self.b, 'Solo B', 'solo-b')
        _prod(self.b, 'Producto B2', 'producto-b2', category=cat)
        rows = self.client.get(_v1('v1-a') + '?category=solo-b').json()
        self.assertEqual(rows, [])

    def test_search_cannot_reach_another_tenant(self):
        rows = self.client.get(_v1('v1-a') + '?search=iPhone').json()
        self.assertEqual({p['name'] for p in rows}, {'iPhone 15 A'})

    def test_search_for_a_name_only_tenant_b_owns_returns_nothing(self):
        _prod(self.b, 'Palabra Irrepetible', 'irrepetible', category=self.cat_b)
        self.assertEqual(self.client.get(_v1('v1-a') + '?search=Irrepetible').json(), [])

    def test_ordering_is_allowlisted_and_stays_scoped(self):
        _prod(self.a, 'Barato A', 'barato-a', category=self.cat_a, price='10.00')
        rows = self.client.get(_v1('v1-a') + '?ordering=price').json()
        self.assertEqual([p['slug'] for p in rows][0], 'barato-a')
        self.assertNotIn('iPhone 15 B', {p['name'] for p in rows})

    def test_an_unknown_ordering_key_is_ignored_rather_than_applied(self):
        # Without the allowlist this would sort by an arbitrary column, which is
        # a way to infer values the serializer never returns.
        rows = self.client.get(_v1('v1-a') + '?ordering=company_id').json()
        self.assertEqual({p['name'] for p in rows}, {'iPhone 15 A'})

    def test_in_stock_filter_stays_scoped(self):
        rows = self.client.get(_v1('v1-a') + '?in_stock=true').json()
        self.assertNotIn('iPhone 15 B', {p['name'] for p in rows})


class V1TenantSelectorAuthorityTest(TestCase):
    """The path names the tenant. Nothing else may override it."""

    def setUp(self):
        cache.clear()
        self.a = _saas_company('Empresa A', 'sel-a', tax_id='20777200001')
        self.b = _saas_company('Empresa B', 'sel-b', tax_id='20777200002')
        _prod(self.a, 'Producto A', 'producto-a')
        _prod(self.b, 'Producto B', 'producto-b')
        self.client = APIClient()

    def test_a_company_query_parameter_cannot_change_the_tenant(self):
        rows = self.client.get(_v1('sel-a') + f'?company={self.b.id}').json()
        self.assertEqual({p['name'] for p in rows}, {'Producto A'})

    def test_a_company_slug_query_parameter_cannot_change_the_tenant(self):
        rows = self.client.get(_v1('sel-a') + '?company_slug=sel-b').json()
        self.assertEqual({p['name'] for p in rows}, {'Producto A'})

    def test_an_invented_tenant_header_cannot_change_the_tenant(self):
        rows = self.client.get(_v1('sel-a'), HTTP_X_COMPANY_SLUG='sel-b').json()
        self.assertEqual({p['name'] for p in rows}, {'Producto A'})

    def test_the_host_cannot_override_the_path(self):
        # The web storefront resolves by Host. This surface must NOT, or the same
        # URL would answer differently depending on which domain reached it.
        with override_settings(ALLOWED_HOSTS=['*'], DEFAULT_STOREFRONT_COMPANY_SLUG='sel-b'):
            rows = self.client.get(_v1('sel-a'), HTTP_HOST='sel-b.example.com').json()
        self.assertEqual({p['name'] for p in rows}, {'Producto A'})

    def test_the_default_storefront_setting_cannot_override_the_path(self):
        with override_settings(DEFAULT_STOREFRONT_COMPANY_SLUG='sel-b'):
            rows = self.client.get(_v1('sel-a')).json()
        self.assertEqual({p['name'] for p in rows}, {'Producto A'})

    def test_the_slug_is_case_insensitive(self):
        self.assertEqual(self.client.get(_v1('SEL-A')).status_code, 200)


class V1UnresolvableTenantTest(TestCase):
    """Fail-safe: nothing resolves, nothing is served, nothing is revealed."""

    def setUp(self):
        cache.clear()
        self.active = _saas_company('Activa', 'fs-activa', tax_id='20777300001')
        self.inactive = _saas_company(
            'Inactiva', 'fs-inactiva', tax_id='20777300002', is_active=False,
        )
        _prod(self.active, 'Visible', 'visible')
        _prod(self.inactive, 'Oculto', 'oculto')
        self.client = APIClient()

    def test_an_unknown_company_is_404(self):
        self.assertEqual(self.client.get(_v1('no-existe-en-absoluto')).status_code, 404)

    def test_an_inactive_company_is_404(self):
        self.assertEqual(self.client.get(_v1('fs-inactiva')).status_code, 404)

    def test_an_inactive_company_never_leaks_its_catalogue(self):
        body = self.client.get(_v1('fs-inactiva')).content.decode()
        self.assertNotIn('Oculto', body)
        self.assertNotIn('oculto', body)

    def test_unknown_and_inactive_are_INDISTINGUISHABLE(self):
        # A 403 for "inactive" and a 404 for "unknown" would answer, to anyone
        # willing to iterate the namespace, which companies exist.
        unknown = self.client.get(_v1('no-existe-en-absoluto'))
        inactive = self.client.get(_v1('fs-inactiva'))
        self.assertEqual(unknown.status_code, inactive.status_code)
        self.assertEqual(unknown.json(), inactive.json())

    def test_the_404_body_names_no_company(self):
        body = self.client.get(_v1('fs-inactiva')).content.decode()
        self.assertNotIn('fs-inactiva', body)
        self.assertNotIn('Inactiva', body)

    def test_categories_fail_safe_the_same_way(self):
        self.assertEqual(self.client.get(_v1('fs-inactiva', 'categories')).status_code, 404)
        self.assertEqual(self.client.get(_v1('no-existe', 'categories')).status_code, 404)

    def test_detail_of_an_unknown_tenant_is_404(self):
        self.assertEqual(
            self.client.get(_v1('no-existe', 'products', 'visible/')).status_code, 404,
        )

    def test_there_is_no_first_company_fallback(self):
        # The failure this exists to prevent: an unresolved tenant quietly
        # serving whichever company the database happened to return first.
        body = self.client.get(_v1('no-existe')).content.decode()
        self.assertNotIn('Visible', body)


class V1PublicContractTest(TestCase):
    """Shape, exposure and the fact that this surface is genuinely public."""

    def setUp(self):
        cache.clear()
        self.company = _saas_company('Contrato', 'contrato', tax_id='20777400001')
        self.category = _cat(self.company, 'Mac', 'mac')
        self.product = _prod(
            self.company, 'MacBook Air', 'macbook-air', category=self.category, price='4500.00',
        )
        self.client = APIClient()

    def test_product_fields_are_exactly_the_agreed_contract(self):
        row = self.client.get(_v1('contrato')).json()[0]
        self.assertEqual(
            set(row),
            {
                'id', 'name', 'slug', 'description', 'price',
                'inventory', 'category', 'image_url', 'average_rating', 'review_count',
            },
        )

    def test_category_fields_are_exactly_the_agreed_contract(self):
        row = self.client.get(_v1('contrato', 'categories')).json()[0]
        self.assertEqual(set(row), {'id', 'name', 'slug'})

    def test_no_internal_field_is_exposed(self):
        body = self.client.get(_v1('contrato')).content.decode()
        for leaked in ('company', 'tax_id', 'legal_name', 'cost', 'branch', 'stripe'):
            self.assertNotIn(leaked, body.lower())

    def test_the_list_is_a_raw_array_not_a_paginated_envelope(self):
        # Matches the legacy surface, which the mobile client already maps.
        self.assertIsInstance(self.client.get(_v1('contrato')).json(), list)

    def test_inventory_reports_sellable_units_of_the_fulfillment_branch(self):
        self.assertEqual(self.client.get(_v1('contrato')).json()[0]['inventory'], 10)

    def test_a_company_with_no_fulfillment_branch_reports_zero_not_the_aggregate(self):
        from .models import Branch
        Branch.objects.filter(company=self.company).update(is_active=False)
        self.assertEqual(self.client.get(_v1('contrato')).json()[0]['inventory'], 0)

    def test_the_endpoint_needs_no_authentication(self):
        self.assertEqual(self.client.get(_v1('contrato')).status_code, 200)

    def test_the_endpoint_ignores_a_session_cookie(self):
        # Authentication is switched off on this surface, so a logged-in browser
        # and an anonymous app must receive byte-identical catalogues.
        anonymous = self.client.get(_v1('contrato')).json()
        user = _saas_user('cliente-v1')
        self.client.force_authenticate(user=user)
        authenticated = self.client.get(_v1('contrato')).json()
        self.client.force_authenticate(user=None)
        self.assertEqual(anonymous, authenticated)

    def test_the_surface_is_read_only(self):
        for method in ('post', 'put', 'patch', 'delete'):
            response = getattr(self.client, method)(_v1('contrato'))
            self.assertIn(response.status_code, (401, 403, 405))

    def test_the_detail_endpoint_is_read_only(self):
        for method in ('post', 'put', 'patch', 'delete'):
            response = getattr(self.client, method)(_v1('contrato', 'products', 'macbook-air/'))
            self.assertIn(response.status_code, (401, 403, 405))


class V1IsAdditiveTest(TestCase):
    """Regression: `/api/` behaves exactly as it did before v1 existed."""

    def setUp(self):
        cache.clear()
        self.company = _saas_company('Aditiva', 'aditiva', tax_id='20777500001')
        self.category = _cat(self.company, 'Mac', 'mac-aditiva')
        _prod(self.company, 'MacBook Pro', 'macbook-pro-aditiva', category=self.category)
        self.client = APIClient()

    def test_the_legacy_catalogue_still_resolves_by_host_setting(self):
        with _storefront_of(self.company):
            rows = self.client.get('/api/products/').json()
        self.assertEqual({p['slug'] for p in rows}, {'macbook-pro-aditiva'})

    def test_the_legacy_catalogue_still_returns_a_raw_array(self):
        with _storefront_of(self.company):
            self.assertIsInstance(self.client.get('/api/products/').json(), list)

    def test_the_legacy_categories_endpoint_is_unchanged(self):
        with _storefront_of(self.company):
            self.assertEqual(self.client.get('/api/categories/').status_code, 200)

    def test_v1_and_legacy_agree_on_the_same_tenant(self):
        with _storefront_of(self.company):
            legacy = self.client.get('/api/products/').json()
        versioned = self.client.get(_v1('aditiva')).json()
        self.assertEqual(
            {p['slug'] for p in legacy}, {p['slug'] for p in versioned},
        )

    def test_v1_does_not_appear_under_the_legacy_prefix(self):
        self.assertEqual(self.client.get('/api/storefront/aditiva/products/').status_code, 404)

    def test_the_legacy_surface_still_ignores_a_path_tenant(self):
        # Proves the two resolvers are genuinely separate code paths.
        with override_settings(DEFAULT_STOREFRONT_COMPANY_SLUG=''):
            self.assertEqual(self.client.get('/api/products/').json(), [])


class V1DoesNotTouchAuthenticationTest(TestCase):
    """
    The WEB authentication contract is untouched, and Bearer is never global.

    Written when `/api/v1/` held only the catalogue and BR-001 was entirely
    pending. BR-001A has since added the native session core, so the assertions
    about `/api/v1/auth/*` were re-pointed; everything about the LEGACY surface
    and about `DEFAULT_AUTHENTICATION_CLASSES` still holds and still matters.
    """

    def test_the_project_default_authentication_is_still_cookie_jwt(self):
        from django.conf import settings
        self.assertEqual(
            settings.REST_FRAMEWORK['DEFAULT_AUTHENTICATION_CLASSES'],
            ('store.authentication.CookieJWTAuthentication',),
        )

    def test_no_bearer_authentication_class_is_installed_globally(self):
        from django.conf import settings
        classes = ' '.join(settings.REST_FRAMEWORK['DEFAULT_AUTHENTICATION_CLASSES'])
        self.assertNotIn('JWTAuthentication', classes.replace('CookieJWTAuthentication', ''))
        self.assertNotIn('TokenAuthentication', classes)

    def test_v1_declares_no_authentication_of_its_own(self):
        from .v1_views import V1StorefrontProductViewSet
        self.assertEqual(V1StorefrontProductViewSet.authentication_classes, [])

    def test_the_v1_auth_endpoints_now_EXIST(self):
        # Written in the catalogue phase to assert these were 404, and it failed
        # the moment BR-001A shipped them — which is precisely what it was for.
        # Re-pointed rather than deleted: the claim it protects is "auth does not
        # appear by accident", and that is still worth asserting, now in the
        # affirmative. What must still be absent is covered by
        # V1AccountLifecycleIsOutOfScopeTest.
        client = APIClient()
        for path in ('/api/v1/auth/login/', '/api/v1/auth/refresh/', '/api/v1/auth/logout/'):
            self.assertNotEqual(client.post(path, {}, format='json').status_code, 404)
        self.assertNotEqual(client.get('/api/v1/auth/me/').status_code, 404)

    def test_there_is_no_v1_private_surface_yet(self):
        client = APIClient()
        for path in ('/api/v1/orders/', '/api/v1/me/', '/api/v1/repairs/'):
            self.assertEqual(client.get(path).status_code, 404)

    def test_a_bearer_token_grants_nothing_on_the_public_surface(self):
        company = _saas_company('Bearer', 'bearer-nada', tax_id='20777600001')
        _prod(company, 'Producto', 'producto-bearer')
        client = APIClient()
        anonymous = client.get(_v1('bearer-nada')).json()
        with_header = client.get(_v1('bearer-nada'), HTTP_AUTHORIZATION='Bearer inventado').json()
        self.assertEqual(anonymous, with_header)


class V1PublicResolverUnitTest(TestCase):
    """`resolve_public_storefront_company` in isolation — the security seam."""

    def setUp(self):
        cache.clear()
        self.company = _saas_company('Resolver', 'resolver-ok', tax_id='20777700001')
        _saas_company('Apagada', 'resolver-off', tax_id='20777700002', is_active=False)

    def test_it_resolves_an_active_company(self):
        from .tenancy import resolve_public_storefront_company
        self.assertEqual(resolve_public_storefront_company('resolver-ok'), self.company)

    def test_it_normalizes_case_and_surrounding_whitespace(self):
        from .tenancy import resolve_public_storefront_company
        self.assertEqual(resolve_public_storefront_company('  RESOLVER-OK '), self.company)

    def test_it_refuses_an_inactive_company(self):
        from .tenancy import resolve_public_storefront_company
        self.assertIsNone(resolve_public_storefront_company('resolver-off'))

    def test_it_refuses_blank_and_non_string_input(self):
        from .tenancy import resolve_public_storefront_company
        for value in ('', '   ', None, 42, [], {'slug': 'resolver-ok'}):
            self.assertIsNone(resolve_public_storefront_company(value))

    def test_it_refuses_an_absurdly_long_slug(self):
        from .tenancy import resolve_public_storefront_company
        self.assertIsNone(resolve_public_storefront_company('x' * 5000))

    def test_it_never_falls_back_to_another_company(self):
        from .tenancy import resolve_public_storefront_company
        self.assertIsNone(resolve_public_storefront_company('no-existe'))


class V1SharedScopingHelperTest(TestCase):
    """The refactor must leave the web storefront byte-identical."""

    def setUp(self):
        cache.clear()
        self.company = _saas_company('Compartida', 'compartida', tax_id='20777800001')
        self.category = _cat(self.company, 'Mac', 'mac-compartida')
        _prod(self.company, 'Producto', 'producto-compartido', category=self.category)
        self.client = APIClient()

    def test_the_host_path_and_the_slug_path_produce_the_same_queryset(self):
        from .tenancy import company_storefront_products, storefront_products
        from django.test import RequestFactory
        request = RequestFactory().get('/api/products/')
        with _storefront_of(self.company):
            by_host = list(storefront_products(request).values_list('id', flat=True))
        by_company = list(company_storefront_products(self.company).values_list('id', flat=True))
        self.assertEqual(by_host, by_company)

    def test_the_shared_helper_annotates_sellable_stock(self):
        from .tenancy import company_storefront_products
        product = company_storefront_products(self.company).first()
        self.assertEqual(product.available_stock, 10)

    def test_a_none_company_yields_an_empty_queryset_not_everything(self):
        from .tenancy import company_storefront_categories, company_storefront_products
        self.assertEqual(company_storefront_products(None).count(), 0)
        self.assertEqual(company_storefront_categories(None).count(), 0)


# =============================================================================
# BR-001A — native session core: /api/v1/auth/
# =============================================================================
#
# Two authentication contracts now coexist. The web one reads an HttpOnly cookie
# and enforces CSRF; the native one reads `Authorization: Bearer` and does not.
# Most of what follows exists to pin down that they stay apart: a token minted
# for the app must not open a single legacy endpoint, and the web must keep
# behaving exactly as it did.

from .models import Customer as _V1Customer  # noqa: E402
from .v1_authentication import V1BearerAuthentication  # noqa: E402

V1_LOGIN = '/api/v1/auth/login/'
V1_REFRESH = '/api/v1/auth/refresh/'
V1_LOGOUT = '/api/v1/auth/logout/'
V1_ME = '/api/v1/auth/me/'


def _v1_user(username='cliente-v1', email='cliente@example.com', password='Pass123!', **extra):
    return User.objects.create_user(
        username=username, email=email, password=password, **extra,
    )


def _bearer(client, token):
    client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
    return client


def _v1_customer(company, user, **extra):
    """A CRM record for `user` at `company`. `Customer.clean()` requires a name."""
    extra.setdefault('first_name', 'Cliente')
    return _V1Customer.objects.create(company=company, user=user, **extra)


class V1LoginTest(TestCase):
    """Signing in with an email, and every way it must refuse to."""

    def setUp(self):
        cache.clear()
        self.password = 'Pass123!'
        self.user = _v1_user(password=self.password, first_name='Carlos')
        self.client = APIClient()

    def test_valid_credentials_return_tokens(self):
        response = self.client.post(
            V1_LOGIN, {'email': 'cliente@example.com', 'password': self.password}, format='json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn('access', response.json())
        self.assertIn('refresh', response.json())

    def test_response_reports_the_access_lifetime(self):
        response = self.client.post(
            V1_LOGIN, {'email': 'cliente@example.com', 'password': self.password}, format='json',
        )

        # 30 minutes, from SIMPLE_JWT. The client schedules its refresh on this.
        self.assertEqual(response.json()['expires_in'], 1800)

    def test_response_carries_the_user_identity(self):
        response = self.client.post(
            V1_LOGIN, {'email': 'cliente@example.com', 'password': self.password}, format='json',
        )

        user = response.json()['user']
        self.assertEqual(user['email'], 'cliente@example.com')
        self.assertEqual(user['first_name'], 'Carlos')
        self.assertEqual(user['role'], 'customer')
        self.assertTrue(user['is_email_verified'])

    def test_email_matching_is_case_insensitive(self):
        response = self.client.post(
            V1_LOGIN, {'email': 'CLIENTE@Example.COM', 'password': self.password}, format='json',
        )

        self.assertEqual(response.status_code, 200)

    def test_surrounding_whitespace_in_the_email_is_ignored(self):
        response = self.client.post(
            V1_LOGIN, {'email': '  cliente@example.com  ', 'password': self.password},
            format='json',
        )

        self.assertEqual(response.status_code, 200)

    def test_wrong_password_is_401(self):
        response = self.client.post(
            V1_LOGIN, {'email': 'cliente@example.com', 'password': 'incorrecta'}, format='json',
        )

        self.assertEqual(response.status_code, 401)

    def test_unknown_email_is_401(self):
        response = self.client.post(
            V1_LOGIN, {'email': 'nadie@example.com', 'password': self.password}, format='json',
        )

        self.assertEqual(response.status_code, 401)

    def test_unknown_email_and_wrong_password_are_INDISTINGUISHABLE(self):
        # Otherwise a login form answers "does this person have an account here?"
        # to anyone willing to ask.
        unknown = self.client.post(
            V1_LOGIN, {'email': 'nadie@example.com', 'password': 'x'}, format='json',
        )
        wrong = self.client.post(
            V1_LOGIN, {'email': 'cliente@example.com', 'password': 'x'}, format='json',
        )

        self.assertEqual(unknown.status_code, wrong.status_code)
        self.assertEqual(unknown.json(), wrong.json())

    def test_an_inactive_user_cannot_sign_in(self):
        # In this installation an inactive account is either deactivated OR
        # never email-verified — registration creates it inactive.
        self.user.is_active = False
        self.user.save(update_fields=['is_active'])

        response = self.client.post(
            V1_LOGIN, {'email': 'cliente@example.com', 'password': self.password}, format='json',
        )

        self.assertEqual(response.status_code, 401)

    def test_an_inactive_user_is_indistinguishable_from_a_wrong_password(self):
        self.user.is_active = False
        self.user.save(update_fields=['is_active'])

        inactive = self.client.post(
            V1_LOGIN, {'email': 'cliente@example.com', 'password': self.password}, format='json',
        )
        wrong = self.client.post(
            V1_LOGIN, {'email': 'cliente@example.com', 'password': 'x'}, format='json',
        )

        self.assertEqual(inactive.json(), wrong.json())

    def test_DUPLICATE_emails_refuse_rather_than_guess(self):
        # `email` carries NO unique constraint on Django's stock User model, and
        # the registration check has a race. Picking "the first match" would let
        # whoever registered a duplicate address sign in as someone else.
        _v1_user(username='otro', email='cliente@example.com', password=self.password)

        response = self.client.post(
            V1_LOGIN, {'email': 'cliente@example.com', 'password': self.password}, format='json',
        )

        self.assertEqual(response.status_code, 401)

    def test_a_duplicate_email_looks_like_any_other_failure(self):
        _v1_user(username='otro', email='cliente@example.com', password=self.password)

        duplicate = self.client.post(
            V1_LOGIN, {'email': 'cliente@example.com', 'password': self.password}, format='json',
        )
        unknown = self.client.post(
            V1_LOGIN, {'email': 'nadie@example.com', 'password': 'x'}, format='json',
        )

        self.assertEqual(duplicate.json(), unknown.json())

    def test_a_malformed_email_is_rejected_as_validation(self):
        response = self.client.post(
            V1_LOGIN, {'email': 'no-es-un-email', 'password': self.password}, format='json',
        )

        self.assertEqual(response.status_code, 400)

    def test_a_missing_password_is_rejected(self):
        response = self.client.post(V1_LOGIN, {'email': 'cliente@example.com'}, format='json')

        self.assertEqual(response.status_code, 400)

    def test_login_sets_NO_cookies(self):
        # The whole point of a native contract: the app holds its own tokens.
        # A cookie here would be a second, invisible copy of a credential.
        response = self.client.post(
            V1_LOGIN, {'email': 'cliente@example.com', 'password': self.password}, format='json',
        )

        self.assertEqual(len(response.cookies), 0)

    def test_the_response_leaks_no_secret(self):
        response = self.client.post(
            V1_LOGIN, {'email': 'cliente@example.com', 'password': self.password}, format='json',
        )
        body = response.content.decode()

        self.assertNotIn(self.password, body)
        self.assertNotIn('password', body)
        self.assertNotIn('is_superuser', body)
        self.assertNotIn('is_staff', body)

    def test_the_user_object_carries_no_token(self):
        response = self.client.post(
            V1_LOGIN, {'email': 'cliente@example.com', 'password': self.password}, format='json',
        )

        self.assertEqual(
            set(response.json()['user']),
            {'id', 'username', 'email', 'first_name', 'last_name', 'role', 'is_email_verified'},
        )

    def test_login_is_throttled_at_five_per_minute(self):
        # Reuses the existing `login` scope rather than inventing a second
        # budget: two endpoints with independent counters would double the
        # attempts an attacker gets per minute.
        for _ in range(5):
            self.client.post(
                V1_LOGIN, {'email': 'cliente@example.com', 'password': 'x'}, format='json',
            )
        blocked = self.client.post(
            V1_LOGIN, {'email': 'cliente@example.com', 'password': 'x'}, format='json',
        )

        self.assertEqual(blocked.status_code, 429)


class V1RefreshTest(TestCase):
    """Rotation, blacklisting, and refusing to explain itself."""

    def setUp(self):
        cache.clear()
        self.password = 'Pass123!'
        self.user = _v1_user(password=self.password)
        self.client = APIClient()
        login = self.client.post(
            V1_LOGIN, {'email': 'cliente@example.com', 'password': self.password}, format='json',
        )
        self.refresh = login.json()['refresh']

    def test_a_valid_refresh_returns_a_new_access_token(self):
        response = self.client.post(V1_REFRESH, {'refresh': self.refresh}, format='json')

        self.assertEqual(response.status_code, 200)
        self.assertIn('access', response.json())

    def test_the_refresh_token_ROTATES(self):
        response = self.client.post(V1_REFRESH, {'refresh': self.refresh}, format='json')

        self.assertIn('refresh', response.json())
        self.assertNotEqual(response.json()['refresh'], self.refresh)

    def test_the_OLD_refresh_token_stops_working_after_rotation(self):
        # BLACKLIST_AFTER_ROTATION. A stolen refresh token is worth one use, and
        # using it locks out the thief or the owner — whichever moves second.
        self.client.post(V1_REFRESH, {'refresh': self.refresh}, format='json')

        replay = self.client.post(V1_REFRESH, {'refresh': self.refresh}, format='json')

        self.assertEqual(replay.status_code, 401)

    def test_the_rotated_token_works(self):
        rotated = self.client.post(
            V1_REFRESH, {'refresh': self.refresh}, format='json',
        ).json()['refresh']

        again = self.client.post(V1_REFRESH, {'refresh': rotated}, format='json')

        self.assertEqual(again.status_code, 200)

    def test_a_malformed_token_is_401_not_500(self):
        # A 500 here would hand a stack trace to an anonymous caller.
        response = self.client.post(V1_REFRESH, {'refresh': 'no-es-un-jwt'}, format='json')

        self.assertEqual(response.status_code, 401)

    def test_an_access_token_is_not_accepted_as_a_refresh_token(self):
        access = self.client.post(
            V1_LOGIN, {'email': 'cliente@example.com', 'password': self.password}, format='json',
        ).json()['access']

        response = self.client.post(V1_REFRESH, {'refresh': access}, format='json')

        self.assertEqual(response.status_code, 401)

    def test_a_deactivated_user_cannot_extend_their_session(self):
        # Being switched off must take effect on the next refresh, not whenever
        # the refresh token happens to expire.
        self.user.is_active = False
        self.user.save(update_fields=['is_active'])

        response = self.client.post(V1_REFRESH, {'refresh': self.refresh}, format='json')

        self.assertEqual(response.status_code, 401)

    def test_refresh_sets_no_cookies(self):
        response = self.client.post(V1_REFRESH, {'refresh': self.refresh}, format='json')

        self.assertEqual(len(response.cookies), 0)

    def test_refresh_needs_no_csrf_token(self):
        # No cookie is involved, so there is no cross-site request to forge.
        enforcing = APIClient(enforce_csrf_checks=True)
        response = enforcing.post(V1_REFRESH, {'refresh': self.refresh}, format='json')

        self.assertEqual(response.status_code, 200)

    def test_every_failure_answers_the_same(self):
        malformed = self.client.post(V1_REFRESH, {'refresh': 'basura'}, format='json')
        self.client.post(V1_REFRESH, {'refresh': self.refresh}, format='json')
        blacklisted = self.client.post(V1_REFRESH, {'refresh': self.refresh}, format='json')

        self.assertEqual(malformed.json(), blacklisted.json())


class V1MeTest(TestCase):
    """Reading your own identity — the cold-start call."""

    def setUp(self):
        cache.clear()
        self.password = 'Pass123!'
        self.user = _v1_user(password=self.password, first_name='Carlos')
        self.client = APIClient()
        self.access = self.client.post(
            V1_LOGIN, {'email': 'cliente@example.com', 'password': self.password}, format='json',
        ).json()['access']

    def test_a_valid_bearer_token_returns_the_profile(self):
        response = _bearer(self.client, self.access).get(V1_ME)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['user']['email'], 'cliente@example.com')

    def test_no_credentials_is_401(self):
        self.assertEqual(self.client.get(V1_ME).status_code, 401)

    def test_a_malformed_bearer_token_is_401(self):
        self.assertEqual(_bearer(self.client, 'basura').get(V1_ME).status_code, 401)

    def test_an_empty_bearer_header_is_401(self):
        self.client.credentials(HTTP_AUTHORIZATION='Bearer')
        self.assertEqual(self.client.get(V1_ME).status_code, 401)

    def test_a_token_containing_spaces_is_401(self):
        self.client.credentials(HTTP_AUTHORIZATION='Bearer una cosa rara')
        self.assertEqual(self.client.get(V1_ME).status_code, 401)

    def test_another_scheme_is_not_accepted(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {self.access}')
        self.assertEqual(self.client.get(V1_ME).status_code, 401)

    def test_a_refresh_token_is_not_an_access_token(self):
        refresh = self.client.post(
            V1_LOGIN, {'email': 'cliente@example.com', 'password': self.password}, format='json',
        ).json()['refresh']

        self.assertEqual(_bearer(self.client, refresh).get(V1_ME).status_code, 401)

    def test_a_user_deactivated_after_signing_in_is_401(self):
        self.user.is_active = False
        self.user.save(update_fields=['is_active'])

        self.assertEqual(_bearer(self.client, self.access).get(V1_ME).status_code, 401)

    def test_a_deleted_user_is_401_not_500(self):
        self.user.delete()

        self.assertEqual(_bearer(self.client, self.access).get(V1_ME).status_code, 401)

    def test_the_failure_never_says_WHY(self):
        self.user.is_active = False
        self.user.save(update_fields=['is_active'])
        inactive = _bearer(APIClient(), self.access).get(V1_ME)
        malformed = _bearer(APIClient(), 'basura').get(V1_ME)

        self.assertEqual(inactive.json(), malformed.json())

    def test_me_sets_no_cookies(self):
        response = _bearer(self.client, self.access).get(V1_ME)

        self.assertEqual(len(response.cookies), 0)


class V1CompanyContextTest(TestCase):
    """`available_companies` — server-verified relations, never client claims."""

    def setUp(self):
        cache.clear()
        self.password = 'Pass123!'
        self.user = _v1_user(password=self.password)
        self.a = _saas_company('Empresa A', 'ctx-a', tax_id='20778000001')
        self.b = _saas_company('Empresa B', 'ctx-b', tax_id='20778000002')
        self.client = APIClient()

    def _login(self):
        return self.client.post(
            V1_LOGIN, {'email': 'cliente@example.com', 'password': self.password}, format='json',
        ).json()

    def _slugs(self, payload):
        return {row['slug'] for row in payload['available_companies']}

    def test_a_user_with_nothing_gets_an_empty_list(self):
        # Fail-safe: no relation is not "all companies", it is none.
        self.assertEqual(self._login()['available_companies'], [])

    def test_an_active_membership_appears_as_member(self):
        Membership.objects.create(user=self.user, company=self.a, role='sales')

        rows = self._login()['available_companies']

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['slug'], 'ctx-a')
        self.assertEqual(rows[0]['relation'], 'member')

    def test_a_CUSTOMER_record_appears_too(self):
        # Migration 0015 deliberately gave customers no Membership: a shopper is
        # not staff. Memberships alone would therefore return nothing for the
        # mobile app's entire audience.
        _v1_customer(self.a, self.user)

        rows = self._login()['available_companies']

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['relation'], 'customer')

    def test_being_staff_AND_customer_reports_member(self):
        Membership.objects.create(user=self.user, company=self.a, role='sales')
        _v1_customer(self.a, self.user)

        rows = self._login()['available_companies']

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['relation'], 'member')

    def test_an_INACTIVE_membership_is_excluded(self):
        Membership.objects.create(user=self.user, company=self.a, role='sales', is_active=False)

        self.assertEqual(self._login()['available_companies'], [])

    def test_a_membership_of_an_INACTIVE_company_is_excluded(self):
        self.a.is_active = False
        self.a.save(update_fields=['is_active'])
        Membership.objects.create(user=self.user, company=self.a, role='sales')

        self.assertEqual(self._login()['available_companies'], [])

    def test_an_ARCHIVED_customer_record_is_excluded(self):
        _v1_customer(self.a, self.user, is_active=False)

        self.assertEqual(self._login()['available_companies'], [])

    def test_ANOTHER_users_relations_never_appear(self):
        other = _v1_user(username='ajeno', email='ajeno@example.com')
        Membership.objects.create(user=other, company=self.b, role='admin')
        Membership.objects.create(user=self.user, company=self.a, role='sales')

        self.assertEqual(self._slugs(self._login()), {'ctx-a'})

    def test_several_relations_are_all_reported(self):
        Membership.objects.create(user=self.user, company=self.a, role='sales')
        _v1_customer(self.b, self.user)

        self.assertEqual(self._slugs(self._login()), {'ctx-a', 'ctx-b'})

    def test_a_SUPERUSER_does_not_receive_every_company(self):
        # A platform administrator does not silently get every tenant on a
        # phone. If that is ever wanted it will be an explicit, audited feature.
        self.user.is_superuser = True
        self.user.is_staff = True
        self.user.save(update_fields=['is_superuser', 'is_staff'])

        self.assertEqual(self._login()['available_companies'], [])

    def test_a_company_sent_BY_THE_CLIENT_is_never_echoed_back(self):
        response = self.client.post(
            V1_LOGIN,
            {'email': 'cliente@example.com', 'password': self.password, 'company': 'ctx-b'},
            format='json',
        )

        self.assertEqual(response.json()['available_companies'], [])

    def test_a_company_HEADER_grants_nothing(self):
        response = self.client.post(
            V1_LOGIN,
            {'email': 'cliente@example.com', 'password': self.password},
            format='json',
            HTTP_X_COMPANY_SLUG='ctx-b',
        )

        self.assertEqual(response.json()['available_companies'], [])

    def test_me_reports_the_same_context_as_login(self):
        Membership.objects.create(user=self.user, company=self.a, role='sales')
        payload = self._login()

        me = _bearer(APIClient(), payload['access']).get(V1_ME).json()

        self.assertEqual(me['available_companies'], payload['available_companies'])
        self.assertEqual(me['user'], payload['user'])

    def test_the_context_carries_no_internal_company_data(self):
        Membership.objects.create(user=self.user, company=self.a, role='admin')
        body = str(self._login()['available_companies'])

        for internal in ('tax_id', 'legal_name', 'id', 'capabilit'):
            self.assertNotIn(internal, body)


class V1LogoutTest(TestCase):
    """Best effort, always 200, never an oracle."""

    def setUp(self):
        cache.clear()
        self.password = 'Pass123!'
        self.user = _v1_user(password=self.password)
        self.client = APIClient()
        payload = self.client.post(
            V1_LOGIN, {'email': 'cliente@example.com', 'password': self.password}, format='json',
        ).json()
        self.refresh = payload['refresh']
        self.access = payload['access']

    def test_a_valid_refresh_token_is_blacklisted(self):
        self.assertEqual(
            self.client.post(V1_LOGOUT, {'refresh': self.refresh}, format='json').status_code, 200,
        )

        replay = self.client.post(V1_REFRESH, {'refresh': self.refresh}, format='json')
        self.assertEqual(replay.status_code, 401)

    def test_logging_out_twice_is_not_an_error(self):
        self.client.post(V1_LOGOUT, {'refresh': self.refresh}, format='json')

        again = self.client.post(V1_LOGOUT, {'refresh': self.refresh}, format='json')

        self.assertEqual(again.status_code, 200)

    def test_a_malformed_token_still_answers_200(self):
        # The client has already cleared its own credentials. Failing here would
        # leave the app unable to finish a sign-out it has already committed to.
        response = self.client.post(V1_LOGOUT, {'refresh': 'basura'}, format='json')

        self.assertEqual(response.status_code, 200)

    def test_no_refresh_token_at_all_still_answers_200(self):
        self.assertEqual(self.client.post(V1_LOGOUT, {}, format='json').status_code, 200)

    def test_logout_does_NOT_require_a_live_access_token(self):
        # Precisely when a session has expired is when a user reaches for
        # "sign out". Requiring a valid access token would make it impossible.
        response = APIClient().post(V1_LOGOUT, {'refresh': self.refresh}, format='json')

        self.assertEqual(response.status_code, 200)

    def test_logout_needs_no_csrf(self):
        enforcing = APIClient(enforce_csrf_checks=True)

        response = enforcing.post(V1_LOGOUT, {'refresh': self.refresh}, format='json')

        self.assertEqual(response.status_code, 200)

    def test_logout_sets_no_cookies(self):
        response = self.client.post(V1_LOGOUT, {'refresh': self.refresh}, format='json')

        self.assertEqual(len(response.cookies), 0)

    def test_it_is_not_an_oracle_about_token_state(self):
        # A live token, a dead token and a nonsense token answer identically, so
        # this cannot be used to probe which refresh tokens are still valid.
        valid = self.client.post(V1_LOGOUT, {'refresh': self.refresh}, format='json')
        dead = self.client.post(V1_LOGOUT, {'refresh': self.refresh}, format='json')
        nonsense = self.client.post(V1_LOGOUT, {'refresh': 'basura'}, format='json')

        self.assertEqual(valid.json(), dead.json())
        self.assertEqual(valid.json(), nonsense.json())


class V1BearerIsScopedTest(TestCase):
    """
    THE ISOLATION BOUNDARY.

    A token minted for the app must open the native surface and nothing else.
    """

    def setUp(self):
        cache.clear()
        self.password = 'Pass123!'
        self.user = _v1_user(password=self.password)
        self.user.is_staff = True
        self.user.is_superuser = True
        self.user.save(update_fields=['is_staff', 'is_superuser'])
        profile, _ = UserProfile.objects.get_or_create(user=self.user)
        profile.role = UserProfile.ROLE_SUPERADMIN
        profile.save(update_fields=['role'])
        self.client = APIClient()
        self.access = self.client.post(
            V1_LOGIN, {'email': 'cliente@example.com', 'password': self.password}, format='json',
        ).json()['access']

    def test_the_token_opens_the_native_surface(self):
        self.assertEqual(_bearer(APIClient(), self.access).get(V1_ME).status_code, 200)

    def test_the_token_does_NOT_open_the_legacy_admin_surface(self):
        # Even for a superadmin. The legacy surface authenticates by cookie, and
        # a Bearer header means nothing to it.
        response = _bearer(APIClient(), self.access).get('/api/admin/users/')

        self.assertIn(response.status_code, (401, 403))

    def test_the_token_does_NOT_open_the_legacy_profile_endpoint(self):
        response = _bearer(APIClient(), self.access).get('/api/auth/me/')

        self.assertIn(response.status_code, (401, 403))

    def test_the_token_does_NOT_open_legacy_orders(self):
        response = _bearer(APIClient(), self.access).get('/api/orders/')

        self.assertIn(response.status_code, (401, 403))

    def test_the_project_default_authentication_is_UNCHANGED(self):
        from django.conf import settings

        self.assertEqual(
            settings.REST_FRAMEWORK['DEFAULT_AUTHENTICATION_CLASSES'],
            ('store.authentication.CookieJWTAuthentication',),
        )

    def test_the_bearer_class_is_not_installed_globally(self):
        from django.conf import settings

        joined = ' '.join(settings.REST_FRAMEWORK['DEFAULT_AUTHENTICATION_CLASSES'])
        self.assertNotIn('V1Bearer', joined)

    def test_only_the_private_v1_view_declares_it(self):
        from .v1_auth_views import V1LoginView, V1LogoutView, V1MeView, V1RefreshView

        self.assertEqual(V1MeView.authentication_classes, [V1BearerAuthentication])
        # The public ones authenticate nobody: they mint or destroy sessions.
        self.assertEqual(V1LoginView.authentication_classes, [])
        self.assertEqual(V1RefreshView.authentication_classes, [])
        self.assertEqual(V1LogoutView.authentication_classes, [])

    def test_a_bearer_token_does_not_change_the_public_catalogue(self):
        company = _saas_company('Aislada', 'aislada-v1', tax_id='20778100001')
        _prod(company, 'Producto', 'producto-aislado')
        path = '/api/v1/storefront/aislada-v1/products/'

        anonymous = APIClient().get(path).json()
        with_token = _bearer(APIClient(), self.access).get(path).json()

        self.assertEqual(anonymous, with_token)


class V1DoesNotDisturbWebAuthTest(TestCase):
    """Regression: the web contract behaves exactly as it did before."""

    def setUp(self):
        cache.clear()
        self.password = 'Pass123!'
        self.user = _v1_user(username='web-user', email='web@example.com', password=self.password)
        self.client = APIClient()

    def test_web_login_still_authenticates_by_USERNAME(self):
        response = self.client.post(
            '/api/auth/login/', {'username': 'web-user', 'password': self.password}, format='json',
        )

        self.assertEqual(response.status_code, 200)

    def test_web_login_still_returns_its_tokens_in_COOKIES_not_the_body(self):
        response = self.client.post(
            '/api/auth/login/', {'username': 'web-user', 'password': self.password}, format='json',
        )

        self.assertNotIn('access', response.json())
        self.assertNotIn('refresh', response.json())
        self.assertIn('blackdog_access', response.cookies)

    def test_the_web_cookie_still_opens_the_web_profile_endpoint(self):
        self.client.post(
            '/api/auth/login/', {'username': 'web-user', 'password': self.password}, format='json',
        )

        self.assertEqual(self.client.get('/api/auth/me/').status_code, 200)

    def test_the_web_cookie_does_NOT_open_the_native_surface(self):
        # The two contracts are separate in both directions.
        self.client.post(
            '/api/auth/login/', {'username': 'web-user', 'password': self.password}, format='json',
        )

        self.assertEqual(self.client.get(V1_ME).status_code, 401)

    def test_the_native_login_does_not_accept_a_username(self):
        response = self.client.post(
            V1_LOGIN, {'username': 'web-user', 'password': self.password}, format='json',
        )

        self.assertEqual(response.status_code, 400)

    def test_web_csrf_enforcement_is_untouched(self):
        from .authentication import enforce_csrf  # noqa: F401
        from .authentication import CookieJWTAuthentication

        # Still the cookie class, still enforcing CSRF inside authenticate().
        self.assertTrue(hasattr(CookieJWTAuthentication, 'enforce_csrf'))


class V1AccountLifecycleIsOutOfScopeTest(TestCase):
    """BR-001B. These endpoints must not silently appear to exist."""

    def test_there_is_no_native_registration_endpoint(self):
        self.assertEqual(APIClient().post('/api/v1/auth/register/').status_code, 404)

    def test_there_is_no_native_password_reset_endpoint(self):
        client = APIClient()
        for path in (
            '/api/v1/auth/password-reset/',
            '/api/v1/auth/password-reset/confirm/',
            '/api/v1/auth/change-password/',
        ):
            self.assertEqual(client.post(path).status_code, 404)

    def test_there_is_no_native_email_verification_endpoint(self):
        client = APIClient()
        for path in ('/api/v1/auth/verify-email/', '/api/v1/auth/resend-verification/'):
            self.assertEqual(client.post(path).status_code, 404)

    def test_there_is_still_no_private_v1_business_surface(self):
        client = APIClient()
        for path in ('/api/v1/orders/', '/api/v1/repairs/', '/api/v1/me/'):
            self.assertEqual(client.get(path).status_code, 404)


# =============================================================================
# M4 — CUSTOMER surface: /api/v1/customer/<company_slug>/orders/
# =============================================================================
#
# THREE AUDIENCES, THREE SURFACES (DEC-API-001):
#
#   /api/v1/storefront/  PUBLIC    anonymous
#   /api/v1/customer/    CUSTOMER  a client reading their OWN records
#   /api/v1/internal/    INTERNAL  staff under a capability — NOT BUILT YET
#
# Most of what follows exists to pin down that the second never becomes the
# third. The failure being prevented is a customer screen quietly listing every
# client's purchases because an employee happened to be signed in.

from .models import OrderItem as _M4OrderItem  # noqa: E402


def _m4_orders_url(slug):
    return f'/api/v1/customer/{slug}/orders/'


def _m4_user(username, email=None, password='Pass123!'):
    return User.objects.create_user(
        username=username, email=email or f'{username}@example.com', password=password,
    )


def _m4_login(user_email, password='Pass123!'):
    client = APIClient()
    payload = client.post(
        '/api/v1/auth/login/', {'email': user_email, 'password': password}, format='json',
    ).json()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {payload['access']}")
    return client, payload


class M4CustomerOrdersOwnershipTest(TestCase):
    """Whose orders come back, and whose never do."""

    def setUp(self):
        cache.clear()
        self.a = _saas_company('Empresa A', 'm4-a', tax_id='20779000001')
        self.b = _saas_company('Empresa B', 'm4-b', tax_id='20779000002')

        self.ana = _m4_user('ana')
        self.beto = _m4_user('beto')

        _v1_customer(self.a, self.ana)
        _v1_customer(self.a, self.beto)

        self.ana_order = _order(self.a, user=self.ana, total='100.00', paid=True)
        self.beto_order = _order(self.a, user=self.beto, total='200.00', paid=True)

    def test_anonymous_is_401(self):
        self.assertEqual(APIClient().get(_m4_orders_url('m4-a')).status_code, 401)

    def test_a_customer_sees_their_own_orders(self):
        client, _ = _m4_login('ana@example.com')

        rows = client.get(_m4_orders_url('m4-a')).json()

        self.assertEqual([row['id'] for row in rows], [self.ana_order.id])

    def test_a_customer_does_NOT_see_another_customer_of_the_same_company(self):
        # The single most important assertion in this file.
        client, _ = _m4_login('ana@example.com')

        rows = client.get(_m4_orders_url('m4-a')).json()

        self.assertNotIn(self.beto_order.id, [row['id'] for row in rows])

    def test_another_customers_order_is_404_by_id(self):
        client, _ = _m4_login('ana@example.com')

        response = client.get(f"{_m4_orders_url('m4-a')}{self.beto_order.id}/")

        self.assertEqual(response.status_code, 404)

    def test_orders_of_another_COMPANY_never_appear(self):
        _v1_customer(self.b, self.ana)
        other = _order(self.b, user=self.ana, total='300.00', paid=True)
        client, _ = _m4_login('ana@example.com')

        rows = client.get(_m4_orders_url('m4-a')).json()

        self.assertNotIn(other.id, [row['id'] for row in rows])

    def test_a_customer_of_TWO_companies_gets_the_right_scope_per_path(self):
        _v1_customer(self.b, self.ana)
        in_b = _order(self.b, user=self.ana, total='300.00', paid=True)
        client, _ = _m4_login('ana@example.com')

        in_a_ids = [row['id'] for row in client.get(_m4_orders_url('m4-a')).json()]
        in_b_ids = [row['id'] for row in client.get(_m4_orders_url('m4-b')).json()]

        self.assertEqual(in_a_ids, [self.ana_order.id])
        self.assertEqual(in_b_ids, [in_b.id])

    def test_a_cross_tenant_id_does_not_resolve(self):
        _v1_customer(self.b, self.ana)
        in_b = _order(self.b, user=self.ana, total='300.00', paid=True)
        client, _ = _m4_login('ana@example.com')

        response = client.get(f"{_m4_orders_url('m4-a')}{in_b.id}/")

        self.assertEqual(response.status_code, 404)

    def test_an_order_linked_through_the_CRM_record_is_owned_too(self):
        # Bought anonymously, matched to their CRM file by document, and that
        # file later linked to their login. Those purchases are genuinely theirs
        # and `Order.user` alone would miss them.
        # setUp already created it: Customer is unique per (company, user).
        record = Customer.objects.get(company=self.a, user=self.ana)
        anonymous = _order(self.a, user=None, total='400.00', paid=True, customer=record)
        client, _ = _m4_login('ana@example.com')

        rows = client.get(_m4_orders_url('m4-a')).json()

        self.assertIn(anonymous.id, [row['id'] for row in rows])

    def test_an_order_is_NOT_owned_by_matching_email(self):
        # `customer_email` is a snapshot of what was typed, carries no
        # uniqueness, and a household or a small office share one address.
        _order(self.a, user=None, total='500.00', customer_email='ana@example.com')
        client, _ = _m4_login('ana@example.com')

        rows = client.get(_m4_orders_url('m4-a')).json()

        self.assertEqual([row['id'] for row in rows], [self.ana_order.id])

    def test_an_unlinked_anonymous_order_belongs_to_nobody(self):
        orphan = _order(self.a, user=None, total='600.00')
        client, _ = _m4_login('ana@example.com')

        rows = client.get(_m4_orders_url('m4-a')).json()

        self.assertNotIn(orphan.id, [row['id'] for row in rows])

    def test_orders_are_newest_first(self):
        newer = _order(self.a, user=self.ana, total='700.00', paid=True)
        client, _ = _m4_login('ana@example.com')

        rows = client.get(_m4_orders_url('m4-a')).json()

        self.assertEqual(rows[0]['id'], newer.id)


class M4CustomerSurfaceIsNotStaffAccessTest(TestCase):
    """
    Being an employee is not a customer relation.

    Every one of these would be a privilege-confusion bug: staff reading the
    company's client history through an endpoint built for one client.
    """

    def setUp(self):
        cache.clear()
        self.company = _saas_company('Empresa', 'm4-staff', tax_id='20779100001')
        self.client_user = _m4_user('cliente')
        _v1_customer(self.company, self.client_user)
        self.customer_order = _order(
            self.company, user=self.client_user, total='100.00', paid=True,
        )

    def _as_member(self, username, role):
        user = _m4_user(username)
        Membership.objects.create(user=user, company=self.company, role=role)
        return _m4_login(f'{username}@example.com')[0]

    def test_a_SALES_member_gets_no_customer_history_here(self):
        # `sales.orders.view` is a real capability, and this is not where it
        # applies. Company-wide orders are the INTERNAL surface (DEC-API-001).
        client = self._as_member('vendedor', 'sales')

        self.assertEqual(client.get(_m4_orders_url('m4-staff')).status_code, 404)

    def test_an_INVENTORY_member_gets_nothing(self):
        client = self._as_member('almacen', 'inventory')

        self.assertEqual(client.get(_m4_orders_url('m4-staff')).status_code, 404)

    def test_a_TECHNICIAN_gets_nothing(self):
        client = self._as_member('tecnico', 'technician')

        self.assertEqual(client.get(_m4_orders_url('m4-staff')).status_code, 404)

    def test_a_COMPANY_ADMIN_gets_nothing_here(self):
        client = self._as_member('jefa', 'admin')

        self.assertEqual(client.get(_m4_orders_url('m4-staff')).status_code, 404)

    def test_a_PLATFORM_MASTER_cannot_inspect_clients_through_this_surface(self):
        master = _m4_user('plataforma')
        master.is_superuser = True
        master.is_staff = True
        master.save(update_fields=['is_superuser', 'is_staff'])
        client, _ = _m4_login('plataforma@example.com')

        self.assertEqual(client.get(_m4_orders_url('m4-staff')).status_code, 404)

    def test_a_member_who_is_ALSO_a_client_sees_only_their_own_purchases(self):
        # The legitimate overlap: an employee who shops where they work.
        employee = _m4_user('empleada')
        Membership.objects.create(user=employee, company=self.company, role='sales')
        _v1_customer(self.company, employee)
        own = _order(self.company, user=employee, total='50.00', paid=True)
        client, _ = _m4_login('empleada@example.com')

        rows = client.get(_m4_orders_url('m4-staff')).json()

        self.assertEqual([row['id'] for row in rows], [own.id])
        self.assertNotIn(self.customer_order.id, [row['id'] for row in rows])


class M4CustomerCompanySelectorTest(TestCase):
    """The path selects a company. It authorises nothing."""

    def setUp(self):
        cache.clear()
        self.a = _saas_company('Empresa A', 'm4-sel-a', tax_id='20779200001')
        self.b = _saas_company('Empresa B', 'm4-sel-b', tax_id='20779200002')
        self.ana = _m4_user('selana')
        _v1_customer(self.a, self.ana)
        _order(self.a, user=self.ana, total='100.00', paid=True)
        self.client_a, _ = _m4_login('selana@example.com')

    def test_a_company_the_user_has_no_relation_with_is_404(self):
        self.assertEqual(self.client_a.get(_m4_orders_url('m4-sel-b')).status_code, 404)

    def test_an_unknown_company_is_404(self):
        self.assertEqual(self.client_a.get(_m4_orders_url('no-existe')).status_code, 404)

    def test_an_inactive_company_is_404(self):
        self.a.is_active = False
        self.a.save(update_fields=['is_active'])

        self.assertEqual(self.client_a.get(_m4_orders_url('m4-sel-a')).status_code, 404)

    def test_unrelated_unknown_and_inactive_are_INDISTINGUISHABLE(self):
        # Otherwise any valid login can map which companies exist on the
        # platform, one slug at a time.
        unrelated = self.client_a.get(_m4_orders_url('m4-sel-b'))
        unknown = self.client_a.get(_m4_orders_url('no-existe'))

        self.assertEqual(unrelated.status_code, unknown.status_code)
        self.assertEqual(unrelated.json(), unknown.json())

    def test_the_404_body_names_no_company(self):
        body = self.client_a.get(_m4_orders_url('m4-sel-b')).content.decode()

        self.assertNotIn('m4-sel-b', body)
        self.assertNotIn('Empresa B', body)

    def test_a_query_parameter_cannot_change_the_company(self):
        _v1_customer(self.b, self.ana)
        in_b = _order(self.b, user=self.ana, total='999.00', paid=True)

        rows = self.client_a.get(
            f"{_m4_orders_url('m4-sel-a')}?company=m4-sel-b&company_id={self.b.id}",
        ).json()

        self.assertNotIn(in_b.id, [row['id'] for row in rows])

    def test_a_tenant_header_cannot_change_the_company(self):
        _v1_customer(self.b, self.ana)
        in_b = _order(self.b, user=self.ana, total='999.00', paid=True)

        rows = self.client_a.get(
            _m4_orders_url('m4-sel-a'), HTTP_X_COMPANY_SLUG='m4-sel-b',
        ).json()

        self.assertNotIn(in_b.id, [row['id'] for row in rows])

    def test_an_ARCHIVED_customer_keeps_access_to_their_own_purchases(self):
        # Closing a CRM file is a commercial decision. "You may no longer see
        # what you bought" is not one it should make by accident.
        Customer.objects.filter(company=self.a, user=self.ana).update(is_active=False)

        self.assertEqual(self.client_a.get(_m4_orders_url('m4-sel-a')).status_code, 200)


class M4CustomerOrderContractTest(TestCase):
    """Shape, BR-003, and everything that must not be in the response."""

    def setUp(self):
        cache.clear()
        self.company = _saas_company('Contrato', 'm4-contrato', tax_id='20779300001')
        self.ana = _m4_user('contratoana')
        _v1_customer(self.company, self.ana)
        product = _prod(self.company, 'MacBook Air', 'macbook-air-m4')
        self.order = _order(
            self.company, user=self.ana, total='4500.00', paid=True,
            stripe_session_id='cs_test_secreto',
            stripe_payment_intent_id='pi_test_secreto',
            cart_session_key='session-secreta',
            payment_error='tarjeta rechazada internamente',
            customer_phone='999888777',
            fulfillment_status=Order.FulfillmentStatus.SHIPPED,
            delivery_method=Order.DeliveryMethod.PICKUP_STORE,
        )
        _M4OrderItem.objects.create(
            order=self.order, product=product, quantity=2, price=Decimal('2250.00'),
        )
        self.client_ana, _ = _m4_login('contratoana@example.com')

    def _row(self):
        return self.client_ana.get(_m4_orders_url('m4-contrato')).json()[0]

    def test_the_field_list_is_exactly_the_agreed_contract(self):
        self.assertEqual(
            set(self._row()),
            {
                'id', 'status', 'status_label',
                'fulfillment_status', 'fulfillment_status_label',
                'total', 'discount_amount', 'coupon_code',
                'delivery_method', 'delivery_method_label',
                'created_at', 'paid_at', 'items',
            },
        )

    def test_fulfillment_status_is_exposed_at_last(self):
        # BR-003. The column has existed since Phase 2C; the legacy serializer
        # never exposed it, so a customer could see that a payment succeeded and
        # nothing about whether the goods had moved.
        row = self._row()

        self.assertEqual(row['fulfillment_status'], 'shipped')
        self.assertTrue(row['fulfillment_status_label'])

    def test_it_carries_human_labels_so_the_app_invents_no_copy(self):
        row = self._row()

        self.assertTrue(row['status_label'])
        self.assertTrue(row['fulfillment_status_label'])
        self.assertTrue(row['delivery_method_label'])

    def test_an_order_with_NO_delivery_method_still_serializes(self):
        # `delivery_method` is `blank=True` with no default: orders predating
        # the field have none. An empty label is the honest answer; a crash on
        # an old order would be a customer unable to open their own history.
        legacy = _order(self.company, user=self.ana, total='10.00', paid=True)
        rows = self.client_ana.get(_m4_orders_url('m4-contrato')).json()

        row = next(r for r in rows if r['id'] == legacy.id)
        self.assertEqual(row['delivery_method'], '')
        self.assertEqual(row['delivery_method_label'], '')

    def test_items_carry_the_price_PAID(self):
        item = self._row()['items'][0]

        self.assertEqual(item['quantity'], 2)
        self.assertEqual(str(item['price']), '2250.00')
        self.assertEqual(item['product_name'], 'MacBook Air')

    def test_NO_stripe_identifier_reaches_the_customer(self):
        body = self.client_ana.get(_m4_orders_url('m4-contrato')).content.decode()

        self.assertNotIn('cs_test_secreto', body)
        self.assertNotIn('pi_test_secreto', body)
        self.assertNotIn('stripe', body.lower())

    def test_NO_operational_diagnostic_reaches_the_customer(self):
        body = self.client_ana.get(_m4_orders_url('m4-contrato')).content.decode()

        self.assertNotIn('tarjeta rechazada internamente', body)
        self.assertNotIn('payment_error', body)
        self.assertNotIn('email_send_error', body)

    def test_NO_session_handle_reaches_the_customer(self):
        body = self.client_ana.get(_m4_orders_url('m4-contrato')).content.decode()

        self.assertNotIn('session-secreta', body)
        self.assertNotIn('cart_session_key', body)

    def test_NO_internal_structure_reaches_the_customer(self):
        body = self.client_ana.get(_m4_orders_url('m4-contrato')).content.decode()

        for internal in ('company_snapshot', 'fulfillment_branch', 'internal_notification'):
            self.assertNotIn(internal, body)

    def test_the_surface_is_READ_ONLY(self):
        # Cancelling and refunding are decisions the business makes. A mobile
        # client asserting one would be asserting an outcome the server owns.
        url = _m4_orders_url('m4-contrato')
        for method in ('post', 'put', 'patch', 'delete'):
            self.assertIn(getattr(self.client_ana, method)(url).status_code, (403, 405))

    def test_the_detail_endpoint_is_read_only_too(self):
        url = f"{_m4_orders_url('m4-contrato')}{self.order.id}/"
        for method in ('post', 'put', 'patch', 'delete'):
            self.assertIn(getattr(self.client_ana, method)(url).status_code, (403, 405))

    def test_it_answers_a_raw_array(self):
        self.assertIsInstance(
            self.client_ana.get(_m4_orders_url('m4-contrato')).json(), list,
        )


class M4CustomerSurfaceAuthenticationTest(TestCase):
    """Only a v1 Bearer token opens this. Nothing else."""

    def setUp(self):
        cache.clear()
        self.company = _saas_company('Auth', 'm4-auth', tax_id='20779400001')
        self.ana = _m4_user('authana')
        _v1_customer(self.company, self.ana)
        _order(self.company, user=self.ana, total='100.00', paid=True)

    def test_a_web_session_cookie_does_NOT_open_it(self):
        # This surface exists for native clients. A browser reaching it would be
        # reaching past the frontend that was built for it.
        client = APIClient()
        client.post(
            '/api/auth/login/', {'username': 'authana', 'password': 'Pass123!'}, format='json',
        )

        self.assertEqual(client.get(_m4_orders_url('m4-auth')).status_code, 401)

    def test_a_malformed_bearer_token_is_401(self):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer basura')

        self.assertEqual(client.get(_m4_orders_url('m4-auth')).status_code, 401)

    def test_a_refresh_token_is_not_an_access_token(self):
        plain = APIClient()
        payload = plain.post(
            '/api/v1/auth/login/', {'email': 'authana@example.com', 'password': 'Pass123!'},
            format='json',
        ).json()
        plain.credentials(HTTP_AUTHORIZATION=f"Bearer {payload['refresh']}")

        self.assertEqual(plain.get(_m4_orders_url('m4-auth')).status_code, 401)

    def test_a_user_deactivated_after_signing_in_is_401(self):
        client, _ = _m4_login('authana@example.com')
        self.ana.is_active = False
        self.ana.save(update_fields=['is_active'])

        self.assertEqual(client.get(_m4_orders_url('m4-auth')).status_code, 401)


class M4AccessContextTest(TestCase):
    """`access_contexts` — where an identity may act, and what it may see."""

    def setUp(self):
        cache.clear()
        self.a = _saas_company('Empresa A', 'm4-ctx-a', tax_id='20779500001')
        self.b = _saas_company('Empresa B', 'm4-ctx-b', tax_id='20779500002')
        self.user = _m4_user('ctxuser')

    def _me(self):
        client, _ = _m4_login('ctxuser@example.com')
        return client.get('/api/v1/auth/me/').json()

    def test_no_relation_yields_an_empty_list(self):
        self.assertEqual(self._me()['access_contexts'], [])

    def test_a_customer_relation_reports_customer_true_and_member_false(self):
        _v1_customer(self.a, self.user)

        context = self._me()['access_contexts'][0]

        self.assertTrue(context['customer'])
        self.assertFalse(context['member'])
        self.assertEqual(context['capabilities'], [])

    def test_a_membership_reports_member_true_with_capabilities(self):
        Membership.objects.create(user=self.user, company=self.a, role='admin')

        context = self._me()['access_contexts'][0]

        self.assertTrue(context['member'])
        self.assertFalse(context['customer'])
        self.assertGreater(len(context['capabilities']), 0)

    def test_BOTH_relations_are_reported_independently(self):
        # The same person can buy from a company and work for it. Collapsing
        # that into a role would force the client to pick which truth to believe.
        Membership.objects.create(user=self.user, company=self.a, role='sales')
        _v1_customer(self.a, self.user)

        context = self._me()['access_contexts'][0]

        self.assertTrue(context['customer'])
        self.assertTrue(context['member'])

    def test_capabilities_come_from_the_real_resolver(self):
        Membership.objects.create(user=self.user, company=self.a, role='inventory')

        capabilities = self._me()['access_contexts'][0]['capabilities']

        self.assertIn('inventory.view', capabilities)
        self.assertNotIn('memberships.manage', capabilities)

    def test_capabilities_do_not_leak_across_companies(self):
        Membership.objects.create(user=self.user, company=self.a, role='admin')
        _v1_customer(self.b, self.user)

        contexts = {c['company']['slug']: c for c in self._me()['access_contexts']}

        self.assertGreater(len(contexts['m4-ctx-a']['capabilities']), 0)
        self.assertEqual(contexts['m4-ctx-b']['capabilities'], [])

    def test_an_inactive_membership_contributes_nothing(self):
        Membership.objects.create(user=self.user, company=self.a, role='admin', is_active=False)

        self.assertEqual(self._me()['access_contexts'], [])

    def test_an_inactive_company_is_excluded(self):
        Membership.objects.create(user=self.user, company=self.a, role='admin')
        self.a.is_active = False
        self.a.save(update_fields=['is_active'])

        self.assertEqual(self._me()['access_contexts'], [])

    def test_another_users_contexts_never_appear(self):
        other = _m4_user('ctxotro')
        Membership.objects.create(user=other, company=self.b, role='admin')
        _v1_customer(self.a, self.user)

        slugs = {c['company']['slug'] for c in self._me()['access_contexts']}

        self.assertEqual(slugs, {'m4-ctx-a'})

    def test_platform_master_is_reported_SEPARATELY_and_enumerates_nothing(self):
        # Being a platform administrator is not membership of every tenant.
        self.user.is_superuser = True
        self.user.save(update_fields=['is_superuser'])

        payload = self._me()

        self.assertTrue(payload['platform']['is_master'])
        self.assertEqual(payload['access_contexts'], [])

    def test_a_normal_user_is_not_platform_master(self):
        self.assertFalse(self._me()['platform']['is_master'])

    def test_available_companies_is_STILL_present(self):
        # Additive, not a replacement. A shipped client reads the older field
        # and must keep working for as long as it is installed.
        _v1_customer(self.a, self.user)
        payload = self._me()

        self.assertIn('available_companies', payload)
        self.assertEqual(payload['available_companies'][0]['slug'], 'm4-ctx-a')

    def test_login_reports_the_same_contexts_as_me(self):
        Membership.objects.create(user=self.user, company=self.a, role='sales')
        client = APIClient()
        login = client.post(
            '/api/v1/auth/login/', {'email': 'ctxuser@example.com', 'password': 'Pass123!'},
            format='json',
        ).json()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {login['access']}")

        self.assertEqual(client.get('/api/v1/auth/me/').json()['access_contexts'],
                         login['access_contexts'])


class M4SurfacesStayApartTest(TestCase):
    """Regression: adding a customer surface moved nothing else."""

    def setUp(self):
        cache.clear()
        self.company = _saas_company('Aparte', 'm4-aparte', tax_id='20779600001')
        self.ana = _m4_user('aparteana')
        _v1_customer(self.company, self.ana)
        _prod(self.company, 'Producto', 'producto-m4')

    def test_the_public_catalogue_is_still_anonymous(self):
        response = APIClient().get('/api/v1/storefront/m4-aparte/products/')

        self.assertEqual(response.status_code, 200)

    def test_a_customer_token_does_not_change_the_public_catalogue(self):
        path = '/api/v1/storefront/m4-aparte/products/'
        anonymous = APIClient().get(path).json()
        client, _ = _m4_login('aparteana@example.com')

        self.assertEqual(client.get(path).json(), anonymous)

    def test_there_is_still_NO_internal_surface(self):
        client, _ = _m4_login('aparteana@example.com')

        for path in (
            '/api/v1/internal/m4-aparte/orders/',
            '/api/v1/internal/m4-aparte/inventory/',
        ):
            self.assertEqual(client.get(path).status_code, 404)

    def test_the_legacy_orders_endpoint_is_unchanged(self):
        # It still authenticates by cookie and ignores a Bearer header.
        client, _ = _m4_login('aparteana@example.com')

        self.assertIn(client.get('/api/orders/').status_code, (401, 403))

    def test_the_project_default_authentication_is_still_cookie_only(self):
        from django.conf import settings

        self.assertEqual(
            settings.REST_FRAMEWORK['DEFAULT_AUTHENTICATION_CLASSES'],
            ('store.authentication.CookieJWTAuthentication',),
        )

    def test_the_customer_view_declares_the_bearer_class_explicitly(self):
        from .v1_authentication import V1BearerAuthentication
        from .v1_customer_views import V1CustomerOrderViewSet

        self.assertEqual(
            V1CustomerOrderViewSet.authentication_classes, [V1BearerAuthentication],
        )

    def test_web_login_still_returns_its_tokens_in_cookies(self):
        client = APIClient()
        response = client.post(
            '/api/auth/login/', {'username': 'aparteana', 'password': 'Pass123!'}, format='json',
        )

        self.assertIn('blackdog_access', response.cookies)
        self.assertNotIn('access', response.json())


# =============================================================================
# M5 — public storefront config v1, and the AUTHENTICATED native checkout
# =============================================================================
#
# Two audiences buy from the same shop through different doors. The browser
# checkout stays anonymous with a session cart; the native one requires a
# session and receives item intents. What must NOT differ is what anything
# costs, and most of what follows is about pinning that down.

from .models import BranchStock as _M5BranchStock  # noqa: E402


def _m5_config_url(slug):
    return f'/api/v1/storefront/{slug}/config/'


def _m5_checkout_url(slug):
    return f'/api/v1/customer/{slug}/checkout/'


def _m5_stripe_session(session_id='cs_test_m5', url='https://checkout.stripe.com/pay/cs_test_m5'):
    mock = MagicMock()
    mock.id = session_id
    mock.url = url
    return mock


def _m5_stock(product, branch, quantity):
    row, _ = _M5BranchStock.objects.get_or_create(branch=branch, product=product)
    row.quantity = quantity
    row.save(update_fields=['quantity'])
    return row


class M5StorefrontConfigTest(TestCase):
    """BR-006 for native clients: the same payload, addressed by slug."""

    def setUp(self):
        cache.clear()
        self.a = _saas_company('Empresa A', 'm5-cfg-a', tax_id='20780000001')
        self.b = _saas_company('Empresa B', 'm5-cfg-b', tax_id='20780000002')
        self.client = APIClient()

    def test_it_serves_the_company_named_in_the_path(self):
        payload = self.client.get(_m5_config_url('m5-cfg-a')).json()

        self.assertEqual(payload['company']['slug'], 'm5-cfg-a')
        self.assertEqual(payload['company']['name'], 'Empresa A')

    def test_two_tenants_get_their_OWN_config(self):
        a = self.client.get(_m5_config_url('m5-cfg-a')).json()
        b = self.client.get(_m5_config_url('m5-cfg-b')).json()

        self.assertNotEqual(a['company']['slug'], b['company']['slug'])
        self.assertNotEqual(a['company']['tax_id'], b['company']['tax_id'])

    def test_it_needs_no_authentication(self):
        # Branding is what the app draws before anyone signs in. Gating it would
        # make the login screen unbrandable.
        self.assertEqual(self.client.get(_m5_config_url('m5-cfg-a')).status_code, 200)

    def test_the_payload_matches_the_WEB_one_exactly(self):
        # One builder, two transports. Two builders would drift into a shop
        # whose app shows one phone number and whose website shows another.
        from_slug = self.client.get(_m5_config_url('m5-cfg-a')).json()
        with _storefront_of(self.a):
            from_host = APIClient().get('/api/storefront/config/').json()

        self.assertEqual(from_slug, from_host)

    def test_it_carries_the_sections_the_app_needs(self):
        payload = self.client.get(_m5_config_url('m5-cfg-a')).json()

        self.assertEqual(set(payload), {'company', 'branding', 'contact', 'policies'})
        self.assertIn('whatsapp_link', payload['contact'])
        self.assertIn('warranty_url', payload['policies'])

    def test_it_exposes_NO_operational_configuration(self):
        body = self.client.get(_m5_config_url('m5-cfg-a')).content.decode()

        for internal in (
            'order_notification_email', 'stripe', 'secret', 'capabilit', 'membership',
        ):
            self.assertNotIn(internal, body.lower())

    def test_an_unknown_company_is_404(self):
        self.assertEqual(self.client.get(_m5_config_url('no-existe')).status_code, 404)

    def test_an_inactive_company_is_404(self):
        self.a.is_active = False
        self.a.save(update_fields=['is_active'])

        self.assertEqual(self.client.get(_m5_config_url('m5-cfg-a')).status_code, 404)

    def test_unknown_and_inactive_are_INDISTINGUISHABLE(self):
        self.b.is_active = False
        self.b.save(update_fields=['is_active'])

        unknown = self.client.get(_m5_config_url('no-existe'))
        inactive = self.client.get(_m5_config_url('m5-cfg-b'))

        self.assertEqual(unknown.status_code, inactive.status_code)
        self.assertEqual(unknown.json(), inactive.json())

    def test_a_query_parameter_cannot_change_the_tenant(self):
        payload = self.client.get(f"{_m5_config_url('m5-cfg-a')}?company=m5-cfg-b").json()

        self.assertEqual(payload['company']['slug'], 'm5-cfg-a')

    def test_the_HOST_cannot_change_the_tenant(self):
        with override_settings(ALLOWED_HOSTS=['*'], DEFAULT_STOREFRONT_COMPANY_SLUG='m5-cfg-b'):
            payload = self.client.get(
                _m5_config_url('m5-cfg-a'), HTTP_HOST='m5-cfg-b.example.com',
            ).json()

        self.assertEqual(payload['company']['slug'], 'm5-cfg-a')

    def test_a_tenant_header_cannot_change_the_tenant(self):
        payload = self.client.get(
            _m5_config_url('m5-cfg-a'), HTTP_X_COMPANY_SLUG='m5-cfg-b',
        ).json()

        self.assertEqual(payload['company']['slug'], 'm5-cfg-a')


class M5CheckoutBase(TestCase):
    """Shared fixture: one company, one branch with stock, one signed-in client."""

    def setUp(self):
        cache.clear()
        self.company = _saas_company('Tienda M5', 'm5-shop', tax_id='20781000001')
        self.branch = self.company.branches.first() or _saas_branch(self.company)
        self.company.default_inventory_branch = self.branch
        self.company.save(update_fields=['default_inventory_branch'])

        self.product = _prod(self.company, 'iPhone 15', 'iphone-15-m5', price='4000.00')
        _m5_stock(self.product, self.branch, 10)

        self.user = User.objects.create_user(
            username='compradora', email='compradora@example.com', password='Pass123!',
        )
        _v1_customer(self.company, self.user)

        self.client = APIClient()
        payload = self.client.post(
            '/api/v1/auth/login/',
            {'email': 'compradora@example.com', 'password': 'Pass123!'},
            format='json',
        ).json()
        self.access = payload['access']
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.access}')

    def body(self, **overrides):
        body = {
            'items': [{'product_slug': 'iphone-15-m5', 'quantity': 1}],
            'customer_name': 'Ana Torres',
            'customer_phone': '987654321',
            'document_type': 'dni',
            'document_number': '12345678',
            'delivery_method': 'pickup_store',
            'receipt_type': 'boleta',
            'accepted_terms': True,
            'accepted_warranty_policy': True,
            'idempotency_key': 'req-0000000001',
        }
        body.update(overrides)
        return body


class M5CheckoutAuthTest(M5CheckoutBase):
    """Only a v1 Bearer token, held by a client of this company, opens this."""

    def test_anonymous_is_401(self):
        self.assertEqual(
            APIClient().post(_m5_checkout_url('m5-shop'), self.body(), format='json').status_code,
            401,
        )

    def test_a_WEB_session_cookie_does_not_open_it(self):
        web = APIClient()
        web.post(
            '/api/auth/login/', {'username': 'compradora', 'password': 'Pass123!'},
            format='json',
        )

        self.assertEqual(
            web.post(_m5_checkout_url('m5-shop'), self.body(), format='json').status_code, 401,
        )

    def test_a_malformed_bearer_token_is_401(self):
        bad = APIClient()
        bad.credentials(HTTP_AUTHORIZATION='Bearer basura')

        self.assertEqual(
            bad.post(_m5_checkout_url('m5-shop'), self.body(), format='json').status_code, 401,
        )

    @patch('stripe.checkout.Session.create')
    def test_a_customer_of_ANOTHER_company_cannot_check_out_here(self, mock_create):
        mock_create.return_value = _m5_stripe_session()
        other = _saas_company('Otra', 'm5-otra', tax_id='20781000002')
        stranger = User.objects.create_user(
            username='ajena', email='ajena@example.com', password='Pass123!',
        )
        _v1_customer(other, stranger)
        client = APIClient()
        token = client.post(
            '/api/v1/auth/login/', {'email': 'ajena@example.com', 'password': 'Pass123!'},
            format='json',
        ).json()['access']
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')

        response = client.post(_m5_checkout_url('m5-shop'), self.body(), format='json')

        self.assertEqual(response.status_code, 404)
        self.assertEqual(Order.objects.filter(company=self.company).count(), 0)

    @patch('stripe.checkout.Session.create')
    def test_an_EMPLOYEE_who_is_not_a_client_cannot_check_out(self, mock_create):
        # Signing in as staff is not a customer relation. A point of sale is a
        # different surface with a different permission.
        mock_create.return_value = _m5_stripe_session()
        staff = User.objects.create_user(
            username='vendedor', email='vendedor@example.com', password='Pass123!',
        )
        Membership.objects.create(user=staff, company=self.company, role='sales')
        client = APIClient()
        token = client.post(
            '/api/v1/auth/login/', {'email': 'vendedor@example.com', 'password': 'Pass123!'},
            format='json',
        ).json()['access']
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')

        self.assertEqual(
            client.post(_m5_checkout_url('m5-shop'), self.body(), format='json').status_code, 404,
        )

    @patch('stripe.checkout.Session.create')
    def test_an_employee_who_IS_also_a_client_may_buy_as_a_client(self, mock_create):
        mock_create.return_value = _m5_stripe_session()
        staff = User.objects.create_user(
            username='empleada', email='empleada@example.com', password='Pass123!',
        )
        Membership.objects.create(user=staff, company=self.company, role='sales')
        _v1_customer(self.company, staff)
        client = APIClient()
        token = client.post(
            '/api/v1/auth/login/', {'email': 'empleada@example.com', 'password': 'Pass123!'},
            format='json',
        ).json()['access']
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')

        response = client.post(_m5_checkout_url('m5-shop'), self.body(), format='json')

        self.assertEqual(response.status_code, 201)
        self.assertEqual(Order.objects.get(pk=response.json()['order_id']).user, staff)


class M5CheckoutCommercialAuthorityTest(M5CheckoutBase):
    """The client proposes items. The server decides everything about money."""

    @patch('stripe.checkout.Session.create')
    def test_it_creates_a_pending_order_priced_by_the_SERVER(self, mock_create):
        mock_create.return_value = _m5_stripe_session()

        response = self.client.post(
            _m5_checkout_url('m5-shop'), self.body(items=[
                {'product_slug': 'iphone-15-m5', 'quantity': 2},
            ]), format='json',
        )

        self.assertEqual(response.status_code, 201)
        order = Order.objects.get(pk=response.json()['order_id'])
        self.assertEqual(order.total, Decimal('8000.00'))
        self.assertEqual(order.status, Order.Status.PENDING_PAYMENT)

    def test_it_REFUSES_a_client_sent_price(self):
        # Rejected loudly rather than dropped: a client sending a price believes
        # it sets prices, and silence would let it keep believing that.
        response = self.client.post(
            _m5_checkout_url('m5-shop'), self.body(total='1.00'), format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('total', response.json())

    def test_it_refuses_every_server_owned_field(self):
        for field, value in [
            ('price', '1.00'), ('subtotal', '1.00'), ('discount_amount', '999.00'),
            ('company_id', 99), ('branch_id', 99), ('status', 'paid'),
            ('paid', True), ('user_id', 1), ('stripe_session_id', 'cs_fake'),
            ('session_key', 'abc'),
        ]:
            response = self.client.post(
                _m5_checkout_url('m5-shop'), self.body(**{field: value}), format='json',
            )
            self.assertEqual(response.status_code, 400, field)

    @patch('stripe.checkout.Session.create')
    def test_the_price_comes_from_the_product_even_if_it_changed(self, mock_create):
        mock_create.return_value = _m5_stripe_session()
        self.product.price = Decimal('4500.00')
        self.product.save(update_fields=['price'])

        response = self.client.post(_m5_checkout_url('m5-shop'), self.body(), format='json')

        self.assertEqual(Order.objects.get(pk=response.json()['order_id']).total,
                         Decimal('4500.00'))

    def test_insufficient_stock_is_refused(self):
        _m5_stock(self.product, self.branch, 1)

        response = self.client.post(
            _m5_checkout_url('m5-shop'),
            self.body(items=[{'product_slug': 'iphone-15-m5', 'quantity': 5}]),
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(Order.objects.count(), 0)

    def test_an_inactive_product_is_refused(self):
        self.product.is_active = False
        self.product.save(update_fields=['is_active'])

        response = self.client.post(_m5_checkout_url('m5-shop'), self.body(), format='json')

        self.assertEqual(response.status_code, 400)

    def test_a_product_of_ANOTHER_company_does_not_resolve(self):
        # Scoped to the company first, so a foreign slug matches nothing rather
        # than matching and then being filtered.
        other = _saas_company('Otra', 'm5-otra-2', tax_id='20781000003')
        _prod(other, 'Ajeno', 'producto-ajeno-m5')

        response = self.client.post(
            _m5_checkout_url('m5-shop'),
            self.body(items=[{'product_slug': 'producto-ajeno-m5', 'quantity': 1}]),
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(Order.objects.count(), 0)

    @patch('stripe.checkout.Session.create')
    def test_a_coupon_of_another_company_is_refused(self, mock_create):
        mock_create.return_value = _m5_stripe_session()
        other = _saas_company('Otra', 'm5-otra-3', tax_id='20781000004')
        Coupon.objects.create(company=other, code='AJENO50', discount_percent=50)

        response = self.client.post(
            _m5_checkout_url('m5-shop'), self.body(coupon_code='AJENO50'), format='json',
        )

        self.assertEqual(response.status_code, 400)

    @patch('stripe.checkout.Session.create')
    def test_a_coupon_of_THIS_company_is_applied_by_the_server(self, mock_create):
        mock_create.return_value = _m5_stripe_session()
        Coupon.objects.create(company=self.company, code='M5DIEZ', discount_percent=10)

        response = self.client.post(
            _m5_checkout_url('m5-shop'), self.body(coupon_code='M5DIEZ'), format='json',
        )

        order = Order.objects.get(pk=response.json()['order_id'])
        self.assertEqual(order.discount_amount, Decimal('400.00'))
        self.assertEqual(order.total, Decimal('3600.00'))

    @patch('stripe.checkout.Session.create')
    def test_the_same_slug_twice_becomes_ONE_line(self, mock_create):
        # Summing is what the shopper meant; taking the last would drop the first.
        mock_create.return_value = _m5_stripe_session()

        response = self.client.post(
            _m5_checkout_url('m5-shop'),
            self.body(items=[
                {'product_slug': 'iphone-15-m5', 'quantity': 1},
                {'product_slug': 'iphone-15-m5', 'quantity': 2},
            ]),
            format='json',
        )

        order = Order.objects.get(pk=response.json()['order_id'])
        self.assertEqual(order.items.count(), 1)
        self.assertEqual(order.items.first().quantity, 3)

    @patch('stripe.checkout.Session.create')
    def test_it_stamps_the_fulfillment_branch_and_company_snapshot(self, mock_create):
        mock_create.return_value = _m5_stripe_session()

        response = self.client.post(_m5_checkout_url('m5-shop'), self.body(), format='json')

        order = Order.objects.get(pk=response.json()['order_id'])
        self.assertEqual(order.fulfillment_branch, self.branch)
        self.assertTrue(order.company_snapshot)


class M5CheckoutOwnershipTest(M5CheckoutBase):
    """The order must belong to the person who bought it."""

    @patch('stripe.checkout.Session.create')
    def test_order_user_is_the_authenticated_user(self, mock_create):
        mock_create.return_value = _m5_stripe_session()

        response = self.client.post(_m5_checkout_url('m5-shop'), self.body(), format='json')

        self.assertEqual(Order.objects.get(pk=response.json()['order_id']).user, self.user)

    @patch('stripe.checkout.Session.create')
    def test_the_new_order_APPEARS_in_customer_orders(self, mock_create):
        # The end-to-end claim of this phase: buy in the app, see it in "mis
        # pedidos" without any further wiring.
        mock_create.return_value = _m5_stripe_session()
        created = self.client.post(
            _m5_checkout_url('m5-shop'), self.body(), format='json',
        ).json()['order_id']

        rows = self.client.get('/api/v1/customer/m5-shop/orders/').json()

        self.assertIn(created, [row['id'] for row in rows])

    @patch('stripe.checkout.Session.create')
    def test_it_links_the_CRM_record(self, mock_create):
        mock_create.return_value = _m5_stripe_session()

        response = self.client.post(_m5_checkout_url('m5-shop'), self.body(), format='json')

        order = Order.objects.get(pk=response.json()['order_id'])
        self.assertIsNotNone(order.customer)
        self.assertEqual(order.customer.user, self.user)

    @patch('stripe.checkout.Session.create')
    def test_a_contact_email_does_NOT_change_ownership(self, mock_create):
        # A buyer may want the receipt elsewhere. That is contact information,
        # never a claim about whose order it is.
        mock_create.return_value = _m5_stripe_session()

        response = self.client.post(
            _m5_checkout_url('m5-shop'),
            self.body(contact_email='otra.persona@example.com'),
            format='json',
        )

        order = Order.objects.get(pk=response.json()['order_id'])
        self.assertEqual(order.user, self.user)
        self.assertEqual(order.customer_email, 'otra.persona@example.com')

    @patch('stripe.checkout.Session.create')
    def test_another_customer_cannot_read_the_resulting_order(self, mock_create):
        mock_create.return_value = _m5_stripe_session()
        created = self.client.post(
            _m5_checkout_url('m5-shop'), self.body(), format='json',
        ).json()['order_id']

        intruder = User.objects.create_user(
            username='curiosa', email='curiosa@example.com', password='Pass123!',
        )
        _v1_customer(self.company, intruder)
        client = APIClient()
        token = client.post(
            '/api/v1/auth/login/', {'email': 'curiosa@example.com', 'password': 'Pass123!'},
            format='json',
        ).json()['access']
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')

        self.assertEqual(
            client.get(f'/api/v1/customer/m5-shop/orders/{created}/').status_code, 404,
        )


class M5CheckoutIdempotencyTest(M5CheckoutBase):
    """A retry must not cost anyone twice."""

    @patch('stripe.checkout.Session.retrieve')
    @patch('stripe.checkout.Session.create')
    def test_the_same_key_and_basket_yields_ONE_order(self, mock_create, mock_retrieve):
        mock_create.return_value = _m5_stripe_session()
        mock_retrieve.return_value = _m5_stripe_session()

        first = self.client.post(_m5_checkout_url('m5-shop'), self.body(), format='json')
        second = self.client.post(_m5_checkout_url('m5-shop'), self.body(), format='json')

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json()['order_id'], second.json()['order_id'])
        self.assertEqual(Order.objects.count(), 1)

    @patch('stripe.checkout.Session.retrieve')
    @patch('stripe.checkout.Session.create')
    def test_stripe_is_asked_to_CREATE_only_once(self, mock_create, mock_retrieve):
        mock_create.return_value = _m5_stripe_session()
        mock_retrieve.return_value = _m5_stripe_session()

        self.client.post(_m5_checkout_url('m5-shop'), self.body(), format='json')
        self.client.post(_m5_checkout_url('m5-shop'), self.body(), format='json')

        self.assertEqual(mock_create.call_count, 1)

    @patch('stripe.checkout.Session.create')
    def test_stripe_receives_its_OWN_idempotency_key(self, mock_create):
        # A second layer, for the case where the order was created, Stripe
        # accepted the call, and the response never arrived.
        mock_create.return_value = _m5_stripe_session()

        self.client.post(_m5_checkout_url('m5-shop'), self.body(), format='json')

        self.assertIn('idempotency_key', mock_create.call_args.kwargs)

    @patch('stripe.checkout.Session.create')
    def test_the_same_key_with_a_DIFFERENT_basket_is_409(self, mock_create):
        # Returning the first order would tell the client its new basket was
        # accepted when it was not.
        mock_create.return_value = _m5_stripe_session()
        self.client.post(_m5_checkout_url('m5-shop'), self.body(), format='json')

        conflict = self.client.post(
            _m5_checkout_url('m5-shop'),
            self.body(items=[{'product_slug': 'iphone-15-m5', 'quantity': 3}]),
            format='json',
        )

        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(Order.objects.count(), 1)

    @patch('stripe.checkout.Session.retrieve')
    @patch('stripe.checkout.Session.create')
    def test_a_DIFFERENT_key_creates_a_second_order(self, mock_create, mock_retrieve):
        # Distinct session ids per call: `Order.stripe_session_id` is unique, as
        # it should be, and real Stripe never returns the same id twice.
        mock_create.side_effect = [_m5_stripe_session('cs_a'), _m5_stripe_session('cs_b')]
        mock_retrieve.return_value = _m5_stripe_session()

        self.client.post(_m5_checkout_url('m5-shop'), self.body(), format='json')
        self.client.post(
            _m5_checkout_url('m5-shop'),
            self.body(idempotency_key='req-0000000002'),
            format='json',
        )

        self.assertEqual(Order.objects.count(), 2)

    @patch('stripe.checkout.Session.create')
    def test_two_users_may_use_the_SAME_key(self, mock_create):
        # The constraint is scoped to company AND user. A global one would make
        # one customer's checkout fail because of another's.
        mock_create.side_effect = [_m5_stripe_session('cs_u1'), _m5_stripe_session('cs_u2')]
        self.client.post(_m5_checkout_url('m5-shop'), self.body(), format='json')

        other = User.objects.create_user(
            username='otra', email='otra@example.com', password='Pass123!',
        )
        _v1_customer(self.company, other)
        client = APIClient()
        token = client.post(
            '/api/v1/auth/login/', {'email': 'otra@example.com', 'password': 'Pass123!'},
            format='json',
        ).json()['access']
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')

        response = client.post(_m5_checkout_url('m5-shop'), self.body(), format='json')

        self.assertEqual(response.status_code, 201)
        self.assertEqual(Order.objects.count(), 2)

    def test_the_idempotency_key_is_REQUIRED(self):
        body = self.body()
        del body['idempotency_key']

        response = self.client.post(_m5_checkout_url('m5-shop'), body, format='json')

        self.assertEqual(response.status_code, 400)

    @patch('stripe.checkout.Session.retrieve')
    @patch('stripe.checkout.Session.create')
    def test_a_replay_never_echoes_a_stripe_identifier(self, mock_create, mock_retrieve):
        mock_create.return_value = _m5_stripe_session()
        mock_retrieve.return_value = _m5_stripe_session()
        self.client.post(_m5_checkout_url('m5-shop'), self.body(), format='json')

        replay = self.client.post(_m5_checkout_url('m5-shop'), self.body(), format='json')

        self.assertNotIn('cs_test_m5', str(replay.json().get('order_id')))
        self.assertEqual(set(replay.json()), {'order_id', 'checkout_url'})


class M5CheckoutDoesNotConsumeTest(M5CheckoutBase):
    """Nothing is spent before Stripe confirms."""

    @patch('stripe.checkout.Session.create')
    def test_stock_is_NOT_decremented_at_checkout(self, mock_create):
        # An order that never gets paid must not cost the shop its inventory.
        mock_create.return_value = _m5_stripe_session()

        self.client.post(_m5_checkout_url('m5-shop'), self.body(), format='json')

        self.assertEqual(
            _M5BranchStock.objects.get(branch=self.branch, product=self.product).quantity, 10,
        )

    @patch('stripe.checkout.Session.create')
    def test_the_order_is_not_marked_paid(self, mock_create):
        mock_create.return_value = _m5_stripe_session()

        response = self.client.post(_m5_checkout_url('m5-shop'), self.body(), format='json')

        order = Order.objects.get(pk=response.json()['order_id'])
        self.assertFalse(order.paid)
        self.assertIsNone(order.paid_at)

    @patch('stripe.checkout.Session.create')
    def test_a_stripe_failure_marks_the_order_failed_and_answers_502(self, mock_create):
        mock_create.side_effect = stripe_lib.StripeError('boom')

        response = self.client.post(_m5_checkout_url('m5-shop'), self.body(), format='json')

        self.assertEqual(response.status_code, 502)
        self.assertEqual(Order.objects.first().status, Order.Status.FAILED)

    @patch('stripe.checkout.Session.create')
    def test_a_stripe_failure_does_not_leak_the_provider_message(self, mock_create):
        mock_create.side_effect = stripe_lib.StripeError('api key rotated: sk_live_xxx')

        response = self.client.post(_m5_checkout_url('m5-shop'), self.body(), format='json')

        self.assertNotIn('sk_live', response.content.decode())


class M5CheckoutValidationTest(M5CheckoutBase):
    """The form rules, matched to the web where the business is the same."""

    def test_terms_must_be_accepted(self):
        response = self.client.post(
            _m5_checkout_url('m5-shop'), self.body(accepted_terms=False), format='json',
        )
        self.assertEqual(response.status_code, 400)

    def test_warranty_policy_must_be_accepted(self):
        response = self.client.post(
            _m5_checkout_url('m5-shop'),
            self.body(accepted_warranty_policy=False), format='json',
        )
        self.assertEqual(response.status_code, 400)

    def test_a_delivery_needs_an_address(self):
        response = self.client.post(
            _m5_checkout_url('m5-shop'),
            self.body(delivery_method='delivery_arequipa'), format='json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('address_line', response.json())

    @patch('stripe.checkout.Session.create')
    def test_a_delivery_WITH_an_address_is_accepted(self, mock_create):
        mock_create.return_value = _m5_stripe_session()

        response = self.client.post(
            _m5_checkout_url('m5-shop'),
            self.body(
                delivery_method='delivery_arequipa',
                address_line='Calle 1', city='Arequipa', district='Cercado',
            ),
            format='json',
        )

        self.assertEqual(response.status_code, 201)

    def test_a_factura_requires_a_RUC(self):
        response = self.client.post(
            _m5_checkout_url('m5-shop'),
            self.body(receipt_type='factura', document_type='dni'), format='json',
        )
        self.assertEqual(response.status_code, 400)

    def test_a_dni_must_have_eight_digits(self):
        response = self.client.post(
            _m5_checkout_url('m5-shop'), self.body(document_number='123'), format='json',
        )
        self.assertEqual(response.status_code, 400)

    def test_an_invalid_phone_is_refused_exactly_as_on_the_web(self):
        response = self.client.post(
            _m5_checkout_url('m5-shop'), self.body(customer_phone='12'), format='json',
        )
        self.assertEqual(response.status_code, 400)

    def test_an_empty_basket_is_refused(self):
        response = self.client.post(
            _m5_checkout_url('m5-shop'), self.body(items=[]), format='json',
        )
        self.assertEqual(response.status_code, 400)

    def test_a_zero_quantity_is_refused(self):
        response = self.client.post(
            _m5_checkout_url('m5-shop'),
            self.body(items=[{'product_slug': 'iphone-15-m5', 'quantity': 0}]),
            format='json',
        )
        self.assertEqual(response.status_code, 400)

    def test_a_negative_quantity_is_refused(self):
        response = self.client.post(
            _m5_checkout_url('m5-shop'),
            self.body(items=[{'product_slug': 'iphone-15-m5', 'quantity': -3}]),
            format='json',
        )
        self.assertEqual(response.status_code, 400)


class M5WebCheckoutRegressionTest(TestCase):
    """The browser checkout must behave exactly as it did before the extraction."""

    def test_it_is_still_AllowAny(self):
        # Guest orders are the storefront's normal case. The native rule does
        # not apply here and must not be imported by accident.
        from .views import CreateCheckoutSessionView

        self.assertEqual(
            [p.__name__ for p in CreateCheckoutSessionView.permission_classes], ['AllowAny'],
        )

    def test_the_native_checkout_requires_authentication(self):
        from .v1_checkout_views import V1CustomerCheckoutView

        self.assertEqual(
            [p.__name__ for p in V1CustomerCheckoutView.permission_classes], ['IsAuthenticated'],
        )

    def test_the_two_surfaces_share_the_pricing_code(self):
        # If these ever diverge, a customer is charged one price by the web and
        # another by the app.
        import inspect

        from . import checkout_services
        from .views import CreateCheckoutSessionView

        source = inspect.getsource(CreateCheckoutSessionView)
        self.assertIn('checkout.price_checkout', source)
        self.assertIn('checkout.validate_lines_and_subtotal', source)
        self.assertTrue(hasattr(checkout_services, 'create_pending_order'))

    def test_the_native_surface_does_not_accept_a_session_key(self):
        from .v1_checkout_serializers import V1CheckoutSerializer

        self.assertNotIn('session_key', V1CheckoutSerializer().fields)


class M5OrderIdempotencyModelTest(TestCase):
    """The constraint that actually holds under a race."""

    def setUp(self):
        cache.clear()
        self.company = _saas_company('Modelo', 'm5-modelo', tax_id='20782000001')
        self.user = User.objects.create_user(
            username='modelo', email='modelo@example.com', password='Pass123!',
        )

    def test_two_orders_cannot_share_a_key_for_one_customer(self):
        _order(self.company, user=self.user, idempotency_key='dup-key-1')

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                _order(self.company, user=self.user, idempotency_key='dup-key-1')

    def test_the_constraint_does_NOT_bind_orders_without_a_key(self):
        # Every browser order has a null key. A non-partial constraint would let
        # exactly one guest order exist per company, which is absurd.
        _order(self.company, user=None)
        _order(self.company, user=None)

        self.assertEqual(Order.objects.filter(company=self.company).count(), 2)

    def test_two_customers_may_use_the_same_key(self):
        other = User.objects.create_user(
            username='modelo2', email='modelo2@example.com', password='Pass123!',
        )
        _order(self.company, user=self.user, idempotency_key='shared-key')
        _order(self.company, user=other, idempotency_key='shared-key')

        self.assertEqual(Order.objects.filter(idempotency_key='shared-key').count(), 2)
