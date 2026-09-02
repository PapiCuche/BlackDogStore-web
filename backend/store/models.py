import hashlib
import re
import secrets
from datetime import timedelta

from decimal import Decimal

from django.conf import settings
from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator
from django.utils import timezone


class DocumentType(models.TextChoices):
    """
    Identity documents accepted across the platform.

    Defined ONCE, at module level, because two domains need it: an `Order`
    records the document the BUYER gave at checkout, and a `Customer` records the
    document the CRM holds for that person or business. They describe the same
    real-world vocabulary.

    The alternative — a second enum on `Customer` — would have let the two drift.
    A `Customer` saved with `'DNI'` and an `Order` saved with `'dni'` would never
    match again, and the deterministic matching this phase depends on would fail
    silently rather than loudly.

    `Order.DocumentType` remains as an alias so existing call sites keep working,
    and so the dependency runs Customer → shared vocabulary, never
    Customer → Order.
    """

    DNI = 'dni', 'DNI'
    RUC = 'ruc', 'RUC'
    CE = 'ce', 'Carnet de Extranjería'


class SalesChannel(models.TextChoices):
    """
    Where a sale happened. Module level because analytics, the POS service and
    the storefront all reason about it, and none of them should import another.
    """

    ONLINE = 'online', 'Tienda online'
    POS = 'pos', 'Punto de venta'


class DiscountSource(models.TextChoices):
    """
    Why money came off a sale.

    Kept apart from the AMOUNT on purpose: `discount_amount` is what the
    customer did not pay, and this is the reason it was allowed. A promotion the
    company configured in advance and a decision somebody made at the counter
    are different events, and only the second one needs a name attached to it.
    """

    NONE = 'none', 'Sin descuento'
    COUPON = 'coupon', 'Código promocional'
    MANUAL = 'manual', 'Descuento manual'
    # C1.3: fired by the basket itself, with nobody typing anything.
    PROMOTION = 'promotion', 'Promoción automática'


class PaymentMethod(models.TextChoices):
    """
    How the money arrived.

    `STRIPE` is what every historical order used. The rest are what a person at
    a counter reports having received — this records the fact, it does not
    process a payment. A card terminal integration, a cash drawer and a till
    reconciliation are a different phase, and this field is shaped so that phase
    does not have to migrate it.
    """

    STRIPE = 'stripe', 'Stripe (online)'
    CASH = 'cash', 'Efectivo'
    CARD = 'card', 'Tarjeta'
    TRANSFER = 'transfer', 'Transferencia'
    OTHER = 'other', 'Otro'


class Category(models.Model):
    """
    A catalogue category owned by one Company.

    Phase 2B: `slug` is no longer globally unique. Two tenants may each have a
    category called "iphone" — a global unique would have made the platform
    unsellable to a second Apple reseller.
    """

    company = models.ForeignKey(
        'store.Company', on_delete=models.PROTECT, related_name='categories',
    )
    name = models.CharField(max_length=200)
    slug = models.SlugField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['company', 'slug'], name='unique_category_slug_per_company',
            ),
        ]
        indexes = [models.Index(fields=['company', 'slug'])]

    def __str__(self):
        return self.name


class Product(models.Model):
    """
    A catalogue product owned by one Company.

    INVARIANT: a product's category must belong to the same company. Otherwise a
    tenant could attach its product to another tenant's taxonomy and leak the
    relationship in both directions. Enforced in clean() (so every ORM write is
    covered) AND independently in the admin serializer, so no request path relies
    on clean() alone.

    `inventory` — SEMANTICS CHANGED IN PHASE 2D. READ THIS BEFORE USING IT.

    It is no longer the source of truth. `BranchStock.quantity` is, per branch.
    `inventory` is now a COMPATIBILITY AGGREGATE: the sum of this product's
    BranchStock quantities across its company's branches, maintained inside the
    same transaction as every stock movement by `store.inventory_services`.

    It survives because the public catalogue API, the admin product list and
    several reports have exposed a field with this name since Phase 0, and
    breaking that would break the storefront for no gain. It is a DERIVED
    number: nothing outside inventory_services may write it, and no decision
    about whether a sale can be fulfilled may be taken from it — that question
    is always "how much is in THIS branch", which only BranchStock answers.

    See docs/saas-multiempresa.md, "Product.inventory (compatibilidad)".
    """

    company = models.ForeignKey(
        'store.Company', on_delete=models.PROTECT, related_name='products',
    )
    name = models.CharField(max_length=255)
    slug = models.SlugField()
    description = models.TextField(blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    inventory = models.IntegerField(default=0)
    image_url = models.URLField(blank=True, default='')
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['company', 'slug'], name='unique_product_slug_per_company',
            ),
        ]
        indexes = [
            models.Index(fields=['company', 'slug']),
            models.Index(fields=['company', 'is_active']),
            models.Index(fields=['company', 'category']),
        ]

    def __str__(self):
        return self.name

    def clean(self):
        """
        Reject a category owned by a different company.

        Known gap, consistent with the rest of the codebase: bulk_create() and
        queryset.update() bypass save() and therefore this check. The admin
        serializer validates the same invariant independently.
        """
        from django.core.exceptions import ValidationError

        if (
            self.category_id
            and self.company_id
            and self.category.company_id is not None
            and self.category.company_id != self.company_id
        ):
            raise ValidationError(
                {'category': 'La categoría no pertenece a la empresa de este producto.'}
            )

    def save(self, *args, **kwargs):
        self.clean()
        return super().save(*args, **kwargs)


class Coupon(models.Model):
    """
    A discount code owned by one Company.

    Phase 2C: `code` is no longer globally unique. Two tenants may each run a
    "BIENVENIDO10" campaign — a global unique would have made the second one
    impossible, and silently sharing one company's coupon with another would be
    worse.
    """

    company = models.ForeignKey(
        'store.Company', on_delete=models.PROTECT, related_name='coupons',
    )
    code = models.CharField(max_length=50)
    discount_percent = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(100)]
    )
    is_active = models.BooleanField(default=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['company', 'code'], name='unique_coupon_code_per_company',
            ),
        ]
        indexes = [models.Index(fields=['company', 'code'])]

    def __str__(self):
        return f"{self.code} — {self.discount_percent}%"


class Order(models.Model):
    class Status(models.TextChoices):
        PENDING_PAYMENT = 'pending_payment', 'Pendiente de pago'
        PAID = 'paid', 'Pagado'
        FAILED = 'failed', 'Fallido'
        CANCELLED = 'cancelled', 'Cancelado'
        EXPIRED = 'expired', 'Expirado'
        REFUNDED = 'refunded', 'Reembolsado'

    class FulfillmentStatus(models.TextChoices):
        PENDING = 'pending', 'Pendiente'
        CONFIRMED = 'confirmed', 'Confirmado'
        PREPARING = 'preparing', 'En preparación'
        READY_FOR_PICKUP = 'ready_for_pickup', 'Listo para retiro'
        SHIPPED = 'shipped', 'Enviado'
        DELIVERED = 'delivered', 'Entregado'
        CANCELLED = 'cancelled', 'Cancelado operativo'

    # Alias of the module-level vocabulary — see DocumentType. Kept so that
    # `Order.DocumentType.DNI` continues to read naturally where an order is the
    # subject, without giving Customer a reason to import Order.
    DocumentType = DocumentType

    class DeliveryMethod(models.TextChoices):
        PICKUP_STORE = 'pickup_store', 'Recojo en tienda'
        DELIVERY_AREQUIPA = 'delivery_arequipa', 'Delivery Arequipa'
        NATIONAL_SHIPPING = 'national_shipping', 'Envío nacional'

    class ReceiptType(models.TextChoices):
        BOLETA = 'boleta', 'Boleta'
        FACTURA = 'factura', 'Factura'

    # Phase 2C — explicit ownership.
    #
    # The tenant is NOT inferred from the items on every read. An order is used
    # by administration, reports, the dashboard, fulfillment, auditing, the
    # customer portal, the webhook and emails; making each of those re-derive the
    # company through a join would be both slow and easy to get wrong once. The
    # invariant `Order.company == every OrderItem.product.company` is enforced at
    # write time instead — see checkout and OrderItem validation.
    company = models.ForeignKey(
        'store.Company', on_delete=models.PROTECT, related_name='orders',
    )
    # Phase 2D — WHICH BRANCH SELLS THIS ORDER.
    #
    # Stock lives in branches now, so "the company has 20 units" is not an
    # answer to "can this order ship". Checkout resolves the storefront's
    # fulfillment branch (Company.default_inventory_branch) once, stamps it here,
    # and every later step — stock validation, the webhook's sale exits, the
    # Kardex — reads it from the order instead of re-deciding. Re-deriving it
    # later would let a configuration change reroute stock for orders that were
    # already priced and paid against a different branch.
    #
    # Nullable because historical orders predate branches; migration 0025
    # backfills them to the same branch their stock was migrated to. New orders
    # always carry one — checkout refuses to create an order without it.
    #
    # INVARIANT: fulfillment_branch.company == order.company.
    fulfillment_branch = models.ForeignKey(
        'store.Branch', null=True, blank=True, on_delete=models.PROTECT,
        related_name='fulfilled_orders',
    )
    # Phase 3 — WHO THE SELLER WAS, AT THE TIME OF THE SALE.
    #
    # Every document about this order — the confirmation email, the receipt PDF,
    # the internal sales note — carries the company's legal identity. Rendering
    # those from the CURRENT settings means a business that moves premises, is
    # renamed or re-registers silently rewrites what a receipt from six months
    # ago says it was. That is not a cosmetic problem: a customer holding a
    # printed document would find it no longer matches the one the system
    # reprints.
    #
    # So the identity is frozen here when the order is created, and historical
    # documents read it from the order. The live settings drive the storefront
    # and everything else; this drives the paperwork.
    #
    # Contains ONLY the commercial identity that appears on documents. No
    # secrets, no configuration, no internal notification address — see
    # company_settings.build_identity_snapshot().
    #
    # Empty dict for orders that predate the field and could not be backfilled;
    # `order_identity()` then falls back to the live company, which is the best
    # answer available and is documented as such.
    company_snapshot = models.JSONField(default=dict, blank=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)

    # PHASE 4 — the CRM record this sale belongs to.
    #
    # NULLABLE, and a null is a real answer rather than a gap to be filled later:
    # a legacy order whose buyer cannot be identified with certainty is left
    # unlinked on purpose. Attaching it to the wrong client would put one
    # person's purchase history in another person's file, which is the failure
    # this whole model is arranged to avoid.
    #
    # PROTECT because a client who has bought something is not deletable. The
    # supported way to retire a customer is `is_active = False`; the database
    # refuses the other one. Note this deliberately does NOT replace the
    # `customer_*` snapshot fields below — see the comment there.
    customer = models.ForeignKey(
        'store.Customer', on_delete=models.PROTECT,
        null=True, blank=True, related_name='orders',
    )
    # --- Commercial Phase C1 -------------------------------------------
    #
    # ONE sales core, two channels. The alternative — a separate PosSale model —
    # would have meant every report, every stock movement, every internal
    # document and every customer history had to be computed twice and then
    # reconciled. A shop that sells the same article over the counter and online
    # has made one sale either way.
    sales_channel = models.CharField(
        max_length=16, choices=SalesChannel.choices,
        default=SalesChannel.ONLINE, db_index=True,
    )
    payment_method = models.CharField(
        max_length=16, choices=PaymentMethod.choices,
        default=PaymentMethod.STRIPE, db_index=True,
    )
    # Who rang it up. Null for every online order and for history — nobody sold
    # those. NEVER consulted for permissions: recording who did something is not
    # the same as deciding who may.
    sold_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='pos_sales',
    )
    # POS IDEMPOTENCY — the reason a double click cannot charge twice.
    #
    # The key is minted by the browser before the request leaves, so a retry
    # after a timeout carries the SAME key as the attempt whose answer was lost.
    # The fingerprint is what makes the key trustworthy: it pins what the key
    # was used for, so a key reused with different contents is refused instead
    # of silently returning somebody else's sale.
    pos_idempotency_key = models.CharField(max_length=64, blank=True, default='')
    pos_request_fingerprint = models.CharField(max_length=64, blank=True, default='')

    # --- Commercial Phase C1.2: the enriched sale -------------------------
    #
    # WHO GETS CREDITED, frozen as text. `sold_by` can become NULL if the
    # account is ever removed, and a commission ledger that forgets whose it
    # was is not a ledger. Never consulted for permissions — a name is not an
    # authority.
    seller_name_snapshot = models.CharField(max_length=150, blank=True, default='')

    # WHERE THE DISCOUNT CAME FROM. `discount_amount` already records how much
    # money came off; this records why, which is what an auditor asks second.
    discount_source = models.CharField(
        max_length=10, choices=DiscountSource.choices,
        default=DiscountSource.NONE, db_index=True,
    )
    discount_reason = models.CharField(max_length=200, blank=True, default='')
    # Who authorised a MANUAL discount. Coupons need nobody: the company
    # configured the promotion in advance, so applying one is not a decision.
    discount_authorized_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='authorized_discounts',
    )

    # CASH. Null for card, transfer and online — those have no change to give,
    # and inventing a zero would make "paid exactly" indistinguishable from
    # "not cash".
    amount_received = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
    )
    change_amount = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
    )
    # An operation number, an authorisation code, a bank reference. Short text
    # a human typed off a terminal — never a credential.
    payment_reference = models.CharField(max_length=100, blank=True, default='')

    # NAMED FIELDS, not a JSON catch-all. A free-form blob becomes the place
    # everything lands and nothing can be validated, reported on, or removed.
    external_reference = models.CharField(max_length=100, blank=True, default='')
    # About THIS sale, not about the customer — `Customer.notes` is the
    # standing file on a person and outlives every order. Internal control
    # only; no public surface returns it.
    sale_notes = models.TextField(max_length=1000, blank=True, default='')

    # SNAPSHOT OF THE BUYER, frozen at the sale. NOT redundant with `customer`
    # above, and not to be re-derived from it: when a client changes their phone
    # number or moves, last year's order must keep saying what it said when it
    # was issued. `customer` is who they are now; these are who they were then.
    customer_name = models.CharField(max_length=255, blank=True)
    customer_email = models.EmailField(blank=True)
    total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    coupon_code = models.CharField(max_length=50, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    paid = models.BooleanField(default=False)

    # Phase 1: secure checkout tracking
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING_PAYMENT,
        db_index=True,
    )
    fulfillment_status = models.CharField(
        max_length=30,
        choices=FulfillmentStatus.choices,
        default=FulfillmentStatus.PENDING,
        db_index=True,
    )
    stripe_session_id = models.CharField(max_length=255, blank=True, null=True, unique=True, default=None)
    stripe_payment_intent_id = models.CharField(max_length=255, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    cart_session_key = models.CharField(max_length=100, blank=True)
    payment_error = models.TextField(blank=True)

    # Phase 4.1: transactional email idempotency flags
    confirmation_email_sent_at = models.DateTimeField(null=True, blank=True)
    internal_notification_sent_at = models.DateTimeField(null=True, blank=True)
    email_send_error = models.TextField(blank=True)

    # Phase 4.0: commercial checkout fields
    customer_phone = models.CharField(max_length=30, blank=True)
    document_type = models.CharField(
        max_length=10,
        choices=DocumentType.choices,
        blank=True,
    )
    document_number = models.CharField(max_length=20, blank=True)
    delivery_method = models.CharField(
        max_length=25,
        choices=DeliveryMethod.choices,
        blank=True,
        db_index=True,
    )
    address_line = models.CharField(max_length=300, blank=True)
    city = models.CharField(max_length=100, blank=True)
    district = models.CharField(max_length=100, blank=True)
    reference = models.CharField(max_length=250, blank=True)
    notes = models.TextField(max_length=500, blank=True)
    receipt_type = models.CharField(
        max_length=10,
        choices=ReceiptType.choices,
        blank=True,
    )
    accepted_terms = models.BooleanField(default=False)
    accepted_warranty_policy = models.BooleanField(default=False)

    # --- M5: durable checkout idempotency (native clients) -------------------
    #
    # WHY THIS IS A COLUMN AND NOT A CACHE.
    #
    # A double tap, a retried request or a timeout must not create two orders
    # and two payment sessions. A disabled button improves the odds and
    # guarantees nothing: the second request may already be in flight, and a
    # cache that can be evicted or that lives in one process is not a guarantee
    # either. The only thing that holds under concurrency is a uniqueness
    # constraint the database enforces.
    #
    # NULL for every browser order, and that is deliberate rather than a gap:
    # the web checkout has no client request key and is not being changed to
    # acquire one. The partial constraint below therefore only binds rows that
    # actually carry a key.
    idempotency_key = models.CharField(max_length=100, blank=True, null=True, db_index=True)

    # SHA-256 of the canonical request payload, so a replay of the same key with
    # DIFFERENT contents can be refused (409) instead of silently answering with
    # the first order. Returning the earlier order there would tell a client its
    # new basket was accepted when it was not.
    #
    # A hash, not the payload: nothing about the buyer is stored twice.
    idempotency_fingerprint = models.CharField(max_length=64, blank=True)

    class Meta:
        # Every admin list, dashboard KPI and report starts by narrowing to one
        # company and then filters by date or status — these match that shape.
        indexes = [
            models.Index(fields=['company', '-created_at']),
            models.Index(fields=['company', 'paid_at']),
            models.Index(fields=['company', 'status']),
            models.Index(fields=['company', 'fulfillment_status']),
            # C1: analytics slice by channel, and the POS history by seller.
            models.Index(fields=['company', 'sales_channel', 'paid_at']),
            models.Index(fields=['company', 'sold_by', 'paid_at']),
        ]
        # TWO idempotency guarantees, deliberately NOT unified.
        #
        # They look alike and they are not. The POS key is typed by a till
        # operator's browser and is unique per COMPANY, because a shop floor has
        # one sequence of sales no matter who rings them up. The checkout key
        # comes from a native client and is unique per company AND USER, because
        # two customers picking the same client-generated key must not collide —
        # scoping that one to the company alone would make one customer's
        # checkout fail on a stranger's.
        #
        # Merging them into one "generic idempotency" column would have to pick
        # one of those two scopes and would be wrong for the other surface.
        constraints = [
            # ONE sale per idempotency key per company.
            #
            # Conditional on the key being present, because the column is blank
            # for every online order and for all history — and in SQL a plain
            # unique over a column that is usually '' would make the second
            # online order collide with the first.
            models.UniqueConstraint(
                fields=['company', 'pos_idempotency_key'],
                condition=~models.Q(pos_idempotency_key=''),
                name='unique_pos_idempotency_key_per_company',
            ),
            # Scoped to company AND user, not to the key alone: two customers
            # may generate the same client key, and a global constraint would
            # make one of them fail on the other's checkout.
            models.UniqueConstraint(
                fields=['company', 'user', 'idempotency_key'],
                condition=models.Q(idempotency_key__isnull=False),
                name='unique_checkout_idempotency_per_customer',
            ),
        ]

    def __str__(self):
        owner = self.customer_name or self.user or "Anon"
        return f"Order #{self.id} [{self.status}] - {owner}"


class OrderItem(models.Model):
    """
    A line of an order.

    INVARIANT: `item.product.company == item.order.company`. Without it a tenant
    could attach another tenant's product to its own order and drag it through
    checkout, Stripe, the webhook and stock.

    Enforced in `clean()` (covering every ORM object write) AND, critically, in
    `assert_items_match_order()` for bulk paths — `bulk_create()` does NOT call
    `clean()`, so a set-level check has to exist for code that uses it.
    """

    order = models.ForeignKey(Order, related_name='items', on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField(default=1)
    price = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        constraints = [
            # ONE LINE PER PRODUCT PER ORDER — Phase 0.3 / P0-E.
            #
            # Every writer already treats this as the rule. The POS merges
            # repeated ids before writing and says in its own words that merging
            # is required for correctness, not tidiness; the native checkout sums
            # repeated slugs; the browser checkout now merges too. None of them
            # has a reason to write the same article twice — the price on a line
            # is the product's price, so two lines of one product in one order
            # carry no information a single line with the summed quantity does
            # not.
            #
            # It is here because `record_sale_stock_movements` is keyed on
            # (order, product). That key is what stops a replayed Stripe webhook
            # decrementing stock twice, and it is only sound while an order has
            # at most one line per product. This constraint is what keeps it
            # sound against a writer nobody has written yet.
            models.UniqueConstraint(
                fields=['order', 'product'],
                name='unique_order_line_per_product',
            ),
        ]

    def __str__(self):
        return f"{self.quantity} x {self.product.name}"

    def clean(self):
        from django.core.exceptions import ValidationError

        if not (self.order_id and self.product_id):
            return
        order_company = self.order.company_id
        if order_company is None:
            return  # pre-Phase-2C row being backfilled
        if self.product.company_id != order_company:
            raise ValidationError(
                {'product': 'El producto no pertenece a la empresa de este pedido.'}
            )

    def save(self, *args, **kwargs):
        self.clean()
        return super().save(*args, **kwargs)


def assert_items_match_order(order, products):
    """
    Set-level guard for bulk paths — `bulk_create()` bypasses `clean()`.

    Raises ValidationError if any product belongs to a different company than the
    order. Call this BEFORE writing, not after.
    """
    from django.core.exceptions import ValidationError

    if order.company_id is None:
        return
    foreign = [p for p in products if p.company_id != order.company_id]
    if foreign:
        raise ValidationError(
            f'Los productos {[p.pk for p in foreign]} no pertenecen a la empresa '
            f'{order.company_id} del pedido {order.pk}.'
        )


class CartItem(models.Model):
    session_key = models.CharField(max_length=100, db_index=True)
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField(default=1)
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            # ONE ROW PER BASKET LINE — Phase 0.3 / P0-E.
            #
            # The add endpoint reads then writes: it looks for an existing row
            # and creates one if it finds none. Two requests arriving together
            # both find none, and the basket ends up holding the same article
            # twice. From there the damage compounds: the browser checkout turned
            # each row into its own line, stock was checked per line rather than
            # per product, and `record_sale_stock_movements` — idempotent per
            # (order, product) so a replayed webhook is safe — wrote the exit for
            # the first line and skipped the second. Six charged, three shipped
            # out of the books.
            #
            # The application handles the race; this is what holds when it does
            # not. A future writer that forgets cannot corrupt the basket.
            #
            # NOT scoped by company: the tenant arrives through
            # `product.company`, and a second copy of it here would be a second
            # source of truth able to disagree with the first.
            models.UniqueConstraint(
                fields=['session_key', 'product'],
                name='unique_cart_line_per_session',
            ),
        ]

    def __str__(self):
        return f"CartItem {self.product.name} ({self.quantity})"


class Review(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='reviews')
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    author_name = models.CharField(max_length=100, blank=True)
    rating = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)]
    )
    comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Review {self.rating}★ — {self.product.name}"


class UserProfile(models.Model):
    ROLE_CUSTOMER = 'customer'
    ROLE_SALES = 'sales'
    ROLE_INVENTORY = 'inventory'
    ROLE_TECHNICIAN = 'technician'
    ROLE_ADMIN = 'admin'
    ROLE_SUPERADMIN = 'superadmin'

    ROLE_CHOICES = [
        (ROLE_CUSTOMER, 'Cliente'),
        (ROLE_SALES, 'Vendedor'),
        (ROLE_INVENTORY, 'Inventario'),
        (ROLE_TECHNICIAN, 'Técnico'),
        (ROLE_ADMIN, 'Administrador'),
        (ROLE_SUPERADMIN, 'Superadministrador'),
    ]

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='profile',
    )
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default=ROLE_CUSTOMER, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Profile({self.user.username}, {self.role})"


class AdminAuditLog(models.Model):
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='audit_actions',
    )
    action = models.CharField(max_length=100)
    target_type = models.CharField(max_length=50)
    target_id = models.CharField(max_length=50, blank=True)
    metadata = models.JSONField(default=dict)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    # SaaS Phase 1 — nullable on purpose: historical logs predate multi-tenancy
    # and must not be rewritten. Populated going forward for company-scoped actions.
    company = models.ForeignKey(
        'store.Company', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='audit_logs',
    )

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"AuditLog[{self.action}] by={self.actor_id} on {self.target_type}:{self.target_id}"

    @classmethod
    def log(cls, actor, action, target_type, target_id='', metadata=None, request=None,
            company=None):
        # P0-B — the IP comes from the single trust policy, not from a header.
        #
        # This used to read `xff.split(',')[0]`: the LEFTMOST entry of
        # X-Forwarded-For, which is the position furthest from us and entirely
        # under the caller's control. Anyone could therefore choose which IP
        # address their own administrative actions were filed under — and an
        # audit log that records an address the subject picked is worse than one
        # that records nothing, because somebody will later believe it.
        from .client_ip import get_client_ip

        ip = None
        ua = ''
        if request:
            ip = get_client_ip(request)
            ua = request.META.get('HTTP_USER_AGENT', '')[:500]
        return cls.objects.create(
            actor=actor,
            action=action,
            target_type=target_type,
            target_id=str(target_id),
            metadata=metadata or {},
            ip_address=ip or None,
            user_agent=ua,
            company=company,
        )


class AccountToken(models.Model):
    """
    Single-use, time-limited token for account actions (email verification, password reset).

    Only the SHA-256 hash of the raw token is stored. The raw token is sent by email
    and never persisted, so a DB compromise cannot be used to verify emails or reset
    passwords without access to the user's inbox.
    """
    PURPOSE_EMAIL_VERIFICATION = 'email_verification'
    PURPOSE_PASSWORD_RESET = 'password_reset'
    PURPOSE_CHOICES = [
        (PURPOSE_EMAIL_VERIFICATION, 'Email Verification'),
        (PURPOSE_PASSWORD_RESET, 'Password Reset'),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='account_tokens',
    )
    token_hash = models.CharField(max_length=64, db_index=True)
    purpose = models.CharField(max_length=32, choices=PURPOSE_CHOICES)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=['token_hash', 'purpose']),
            models.Index(fields=['user', 'purpose']),
        ]

    def __str__(self):
        return f"AccountToken [{self.purpose}] user={self.user_id} used={self.used_at is not None}"

    @classmethod
    def make(cls, user, purpose, ttl_hours):
        """Generate a raw token, store its hash, and return (raw_token, AccountToken)."""
        raw = secrets.token_urlsafe(48)
        token_hash = hashlib.sha256(raw.encode()).hexdigest()
        obj = cls.objects.create(
            user=user,
            token_hash=token_hash,
            purpose=purpose,
            expires_at=timezone.now() + timedelta(hours=ttl_hours),
        )
        return raw, obj

    @classmethod
    def consume(cls, raw_token, purpose):
        """
        Look up the token by hash, validate it, mark it used, and return the instance.
        Raises ValueError with a safe message on any failure.
        """
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        try:
            obj = cls.objects.select_related('user').get(
                token_hash=token_hash,
                purpose=purpose,
            )
        except cls.DoesNotExist:
            raise ValueError('Token inválido.')

        now = timezone.now()
        if obj.expires_at < now:
            raise ValueError('El token ha expirado.')
        if obj.used_at is not None:
            raise ValueError('Este token ya fue utilizado.')

        obj.used_at = now
        obj.save(update_fields=['used_at'])
        return obj


# ---------------------------------------------------------------------------
# Phase 6.0 — Inventory (Kardex) and internal sales notes
# ---------------------------------------------------------------------------

class StockMovement(models.Model):
    """
    Immutable Kardex line. Every change to BranchStock.quantity must produce one.

    Rules enforced by store.inventory_services (never write this model directly):
      - quantity is always POSITIVE; movement_type decides the sign.
      - stock_before / stock_after are snapshots taken under select_for_update().
      - stock_after is never negative.
      - manual movements require an actor and a reason.
      - sale movements require an order and are idempotent per (order, product).

    PHASE 2D — stock_before / stock_after CHANGED MEANING.
    They are the stock OF THIS BRANCH before and after the movement, not a
    company-wide total. A company with 3 branches now has 3 independent running
    balances for the same product, which is the only reading that makes a Kardex
    auditable: a branch's balance must be reconstructible from its own lines.

    Rows migrated from before Phase 2D belong to the branch chosen by migration
    0025 (see its docstring), and their snapshots are the pre-2D company totals —
    correct, because at that time the company had exactly one stock location.
    """

    # --- Entries (add stock) ---
    INITIAL_STOCK = 'initial_stock'
    PURCHASE_ENTRY = 'purchase_entry'
    MANUAL_ENTRY = 'manual_entry'
    RETURN_ENTRY = 'return_entry'
    CORRECTION_POSITIVE = 'correction_positive'
    # --- Exits (remove stock) ---
    MANUAL_EXIT = 'manual_exit'
    SALE_EXIT = 'sale_exit'
    CORRECTION_NEGATIVE = 'correction_negative'
    DAMAGED_EXIT = 'damaged_exit'
    SERVICE_EXIT = 'service_exit'
    # --- Phase 2D: inter-branch transfer, one line on each side ---
    TRANSFER_OUT = 'transfer_out'
    TRANSFER_IN = 'transfer_in'

    MOVEMENT_TYPE_CHOICES = [
        (INITIAL_STOCK, 'Stock inicial'),
        (PURCHASE_ENTRY, 'Entrada por compra'),
        (MANUAL_ENTRY, 'Entrada manual'),
        (RETURN_ENTRY, 'Entrada por devolución'),
        (CORRECTION_POSITIVE, 'Corrección positiva'),
        (TRANSFER_IN, 'Entrada por transferencia'),
        (MANUAL_EXIT, 'Salida manual'),
        (SALE_EXIT, 'Salida por venta'),
        (CORRECTION_NEGATIVE, 'Corrección negativa'),
        (DAMAGED_EXIT, 'Salida por daño / merma'),
        (SERVICE_EXIT, 'Salida por servicio técnico'),
        (TRANSFER_OUT, 'Salida por transferencia'),
    ]

    ENTRY_TYPES = frozenset([
        INITIAL_STOCK, PURCHASE_ENTRY, MANUAL_ENTRY, RETURN_ENTRY,
        CORRECTION_POSITIVE, TRANSFER_IN,
    ])
    EXIT_TYPES = frozenset([
        MANUAL_EXIT, SALE_EXIT, CORRECTION_NEGATIVE, DAMAGED_EXIT, SERVICE_EXIT,
        TRANSFER_OUT,
    ])
    # Types an operator may create through the admin API. sale_exit is excluded
    # on purpose: it is only ever produced by the payment pipeline, and the two
    # transfer types likewise only by the transfer pipeline — a hand-written
    # transfer_out with no matching transfer_in would be stock that vanished.
    MANUAL_TYPES = frozenset([
        PURCHASE_ENTRY, MANUAL_ENTRY, RETURN_ENTRY, CORRECTION_POSITIVE,
        MANUAL_EXIT, CORRECTION_NEGATIVE, DAMAGED_EXIT,
    ])

    # Phase 2D — explicit tenant and location.
    #
    # `company` is denormalised from product.company rather than joined on every
    # read: the Kardex is filtered by company in every report, dashboard and
    # export, and a two-table join to answer "whose movement is this?" would be
    # both slower and easy to forget once. The invariants below are enforced at
    # write time by inventory_services, which is the only writer.
    #
    # INVARIANTS:
    #   movement.company == movement.product.company
    #   movement.branch.company == movement.company
    #   movement.order.company == movement.company          (when order is set)
    company = models.ForeignKey(
        'store.Company', on_delete=models.PROTECT, related_name='stock_movements',
    )
    branch = models.ForeignKey(
        'store.Branch', on_delete=models.PROTECT, related_name='stock_movements',
    )
    product = models.ForeignKey(
        Product, on_delete=models.PROTECT, related_name='stock_movements',
    )
    movement_type = models.CharField(
        max_length=40, choices=MOVEMENT_TYPE_CHOICES, db_index=True,
    )
    quantity = models.PositiveIntegerField()
    stock_before = models.IntegerField()
    stock_after = models.IntegerField()
    reason = models.TextField(blank=True)
    reference_type = models.CharField(max_length=40, blank=True)
    reference_id = models.CharField(max_length=120, blank=True)
    order = models.ForeignKey(
        Order, null=True, blank=True, on_delete=models.PROTECT,
        related_name='stock_movements',
    )
    # Phase 2D: real foreign keys to the documents that caused the movement,
    # rather than only `reference_type`/`reference_id` strings. A string pair
    # cannot be joined, cannot be validated and silently rots when a row is
    # renumbered; these can, and PROTECT means a transfer or a count can never
    # be deleted out from under the Kardex line that cites it.
    # `reference_type`/`reference_id` are still populated for the generic
    # reference display the existing UI already renders.
    transfer = models.ForeignKey(
        'store.StockTransfer', null=True, blank=True, on_delete=models.PROTECT,
        related_name='stock_movements',
    )
    inventory_count = models.ForeignKey(
        'store.InventoryCount', null=True, blank=True, on_delete=models.PROTECT,
        related_name='stock_movements',
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='stock_movements',
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['-created_at', '-id']
        indexes = [
            models.Index(fields=['product', '-created_at']),
            models.Index(fields=['movement_type', '-created_at']),
            models.Index(fields=['order', 'movement_type']),
            # Phase 2D — the shape every tenantised Kardex query actually has:
            # narrow to the company, then to a branch, then order by date.
            models.Index(fields=['company', '-created_at']),
            models.Index(fields=['branch', '-created_at']),
            models.Index(fields=['branch', 'product', '-created_at']),
            models.Index(fields=['branch', 'movement_type', '-created_at']),
        ]

    def __str__(self):
        return f"{self.movement_type} {self.signed_quantity:+d} product={self.product_id}"

    @property
    def is_entry(self) -> bool:
        return self.movement_type in self.ENTRY_TYPES

    @property
    def signed_quantity(self) -> int:
        """Quantity with the sign implied by movement_type."""
        return self.quantity if self.is_entry else -self.quantity


class SalesNote(models.Model):
    """
    INTERNAL sales note for a paid order.

    This is NOT a SUNAT electronic receipt, NOT fiscal numbering and has no
    legal/tax validity.

    NUMBERING — PHASE 2E
    --------------------
    Three fields, three different meanings, and conflating them is the mistake
    this split exists to prevent:

      number          THE IDENTIFIER THAT WAS ISSUED. A snapshot string, never
                      recomputed. Editing the series prefix afterwards does not
                      touch a note already issued — a document a customer or an
                      auditor is holding must keep saying what it said.
      sequence        which series handed it out. Gives the note its company,
                      its branch scope and its formatting rules at issue time.
      sequence_value  the ordinal within that series. The sortable, arithmetic
                      form of the same fact — `number` is for reading.

    `number` IS NO LONGER GLOBALLY UNIQUE, on purpose. Two companies must both
    be able to issue NV-000001; a global unique made one tenant's numbering
    depend on another's. Uniqueness belongs to the series, which is what
    `unique_value_per_sequence` enforces.

    NO `company` FIELD, deliberately. It is reachable twice already —
    `sequence.company` and `order.company` — and a third copy would be a third
    thing to keep in step, with no query that needs it.
    """

    STATUS_ISSUED = 'issued'
    STATUS_VOID = 'void'
    STATUS_CHOICES = [
        (STATUS_ISSUED, 'Emitida'),
        (STATUS_VOID, 'Anulada'),
    ]

    order = models.OneToOneField(
        Order, on_delete=models.PROTECT, related_name='sales_note',
    )
    # PROTECT: a series that has issued documents can never be deleted out from
    # under them. Retire it with `is_active` instead — the history stays
    # readable, and reactivating continues where it left off.
    sequence = models.ForeignKey(
        'store.InternalSequence', null=True, blank=True, on_delete=models.PROTECT,
        related_name='sales_notes',
    )
    # Nullable for exactly one reason, documented in migration 0030: a legacy
    # number that does not parse into an ordinal. Inventing one would either
    # collide or lie; leaving it empty says truthfully "this number predates the
    # series and has no ordinal".
    sequence_value = models.PositiveBigIntegerField(null=True, blank=True)
    number = models.CharField(max_length=40)
    status = models.CharField(
        max_length=30, choices=STATUS_CHOICES, default=STATUS_ISSUED, db_index=True,
    )
    issued_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='sales_notes_created',
    )
    pdf_generated_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['-created_at', '-id']
        constraints = [
            # THE REAL UNIQUENESS. Not the display string — two tenants may both
            # show NV-000001 — but one ordinal per series.
            #
            # Conditional because a legacy note may carry neither: it predates
            # the series and has no ordinal to be unique about.
            models.UniqueConstraint(
                fields=['sequence', 'sequence_value'],
                condition=models.Q(sequence__isnull=False)
                & models.Q(sequence_value__isnull=False),
                name='unique_value_per_sequence',
            ),
        ]
        indexes = [
            models.Index(fields=['sequence', 'sequence_value']),
            models.Index(fields=['number']),
        ]

    def __str__(self):
        return f"SalesNote({self.number}, order={self.order_id})"


# ---------------------------------------------------------------------------
# SaaS Phase 1 — multi-tenant foundation
# ---------------------------------------------------------------------------
#
# These models introduce the structural base for running more than one business
# on the platform. They are ADDITIVE: nothing in the existing e-commerce flow
# reads them yet, so the current single-company behaviour is unchanged.
#
# Tenant resolution is never taken from client input — see store/tenancy.py.

class Company(models.Model):
    """
    A tenant: one business operating on the platform.

    The first Company is seeded by a data migration, not by a constant in the
    code — the platform must be able to host a completely different business
    without touching business logic. From Phase 3 the same is true of its
    commercial identity, which lives in `CompanySettings`.
    """

    name = models.CharField(max_length=200)
    legal_name = models.CharField(max_length=255, blank=True)
    tax_id = models.CharField(max_length=20, blank=True, db_index=True)
    slug = models.SlugField(max_length=80, unique=True)
    is_active = models.BooleanField(default=True, db_index=True)
    # Phase 2D — WHERE THE ONLINE STORE SELLS FROM.
    #
    # The e-commerce has no branch picker: a customer adds to cart and pays
    # without ever naming a location. Somebody still has to decide which branch's
    # stock was sold, and "whichever one the query returns first" is not a
    # decision — it is a bug that only shows up once a company opens its second
    # branch. So the tenant states it, once, here.
    #
    # Nullable, and a null is a real state, not a placeholder: a company with no
    # fulfillment branch simply cannot check out, and says so. It is never
    # silently replaced by "some branch". SET_NULL rather than PROTECT because
    # deleting the branch must not be blocked by this pointer — it must clear it,
    # loudly, so the next checkout fails instead of shipping from nowhere.
    default_inventory_branch = models.ForeignKey(
        'store.Branch', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='fulfilling_companies',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        verbose_name_plural = 'companies'

    def __str__(self):
        return self.name

    @property
    def is_operational(self) -> bool:
        """A deactivated company keeps its history but cannot transact."""
        return self.is_active

    def clean(self):
        """A company cannot fulfil orders from another company's branch."""
        from django.core.exceptions import ValidationError

        if (
            self.default_inventory_branch_id
            and self.pk
            and self.default_inventory_branch.company_id != self.pk
        ):
            raise ValidationError({
                'default_inventory_branch':
                    'La sucursal de despacho no pertenece a esta empresa.',
            })

    def save(self, *args, **kwargs):
        self.clean()
        return super().save(*args, **kwargs)


class Branch(models.Model):
    """
    A physical location belonging to exactly one Company.

    The FK makes cross-company ownership structurally impossible: a Branch can
    never belong to two companies. Code that receives a branch id must still
    verify `branch.company_id` matches the caller's tenant — see
    store.tenancy.assert_branch_in_company.
    """

    company = models.ForeignKey(
        Company, on_delete=models.PROTECT, related_name='branches',
    )
    name = models.CharField(max_length=200)
    address = models.CharField(max_length=300, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['company__name', 'name']
        verbose_name_plural = 'branches'
        constraints = [
            models.UniqueConstraint(
                fields=['company', 'name'], name='unique_branch_name_per_company',
            ),
        ]
        indexes = [models.Index(fields=['company', 'is_active'])]

    def __str__(self):
        return f"{self.name} ({self.company.name})"


class Membership(models.Model):
    """
    Links a user to a company with a role, optionally scoped to one branch.

    TRANSITION NOTE (SaaS Phase 1):
    `UserProfile.role` remains the authoritative source for permission checks in
    this phase — `get_user_role()` is deliberately untouched. Membership records
    are created alongside it (see migration 0015) so a later phase can switch the
    permission layer over without a data migration under time pressure.

    Role semantics during the transition:
      - `User.is_superuser`      → PLATFORM administrator (cross-tenant, SaaS operator)
      - `Membership.role=admin`  → COMPANY administrator (scoped to one Company)
      - `UserProfile.role=superadmin` → legacy global role, still honoured
    """

    ROLE_CHOICES = UserProfile.ROLE_CHOICES

    # --- Phase 2D: branch access mode -------------------------------------
    #
    # WHY A MODE AND NOT "NO ROWS MEANS EVERYTHING".
    # The obvious design is a plain grant table where an empty set means "all
    # branches". It is also the one that fails open: revoking a person's last
    # branch would silently promote them from one branch to every branch, and
    # a bug that deletes grants would widen access instead of narrowing it.
    # An explicit mode makes "none" expressible and makes ALL a deliberate act.
    #
    #   ALL       every ACTIVE branch of the company, including ones created
    #             tomorrow. For owners and small businesses where per-branch
    #             restriction would be pure friction.
    #   SELECTED  exactly the branches granted in MembershipBranchAccess and no
    #             others. A branch opened later is NOT granted automatically —
    #             that is the whole point of choosing this mode.
    #
    # SELECTED with zero active grants means NO branch. It is a valid state (a
    # member who has not been placed anywhere yet), and it denies rather than
    # allows.
    ACCESS_MODE_ALL = 'all'
    ACCESS_MODE_SELECTED = 'selected'
    ACCESS_MODE_CHOICES = [
        (ACCESS_MODE_ALL, 'Todas las sucursales'),
        (ACCESS_MODE_SELECTED, 'Sucursales seleccionadas'),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='memberships',
    )
    company = models.ForeignKey(
        Company, on_delete=models.PROTECT, related_name='memberships',
    )
    role = models.CharField(
        max_length=20, choices=ROLE_CHOICES, default=UserProfile.ROLE_CUSTOMER, db_index=True,
    )
    # LEGACY / DEPRECATED AS AUTHORITY — Phase 2D.
    #
    # Until 2D this field was the only branch a membership knew about, and it
    # could not express "these three of our five". It is NOT consulted by any
    # access decision any more; `branch_access_mode` + MembershipBranchAccess
    # are. What it still means, and all it means, is the member's DEFAULT
    # branch: which one the internal control opens on. Validated to be a branch
    # they can actually reach — see clean().
    #
    # It is kept rather than dropped because dropping a column that every
    # existing row uses, in the same phase that changes what it means, would
    # make the migration unreviewable. Removal is tracked in
    # docs/saas-multiempresa.md.
    branch = models.ForeignKey(
        Branch, null=True, blank=True, on_delete=models.SET_NULL, related_name='memberships',
    )
    branch_access_mode = models.CharField(
        max_length=20, choices=ACCESS_MODE_CHOICES, default=ACCESS_MODE_ALL,
        db_index=True,
    )
    is_active = models.BooleanField(default=True, db_index=True)
    # --- Commercial Phase C1.2 -------------------------------------------
    #
    # COMMISSION BELONGS TO THE EMPLOYMENT, NOT TO THE PERSON.
    #
    # One human can sell for two businesses on different terms — 3% here, 5%
    # there — so a rate on `User` could not express the truth. It is not on
    # `role` either: two salespeople in one shop are routinely on different
    # deals, and tying the rate to a role would force a new role per rate.
    #
    # Zero is the default and means exactly that: this person earns no
    # commission. It is not "unconfigured" — see `SalesCommission`, which is
    # simply not written when the rate is zero.
    commission_rate_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('0.00'),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['company__name', 'user__username']
        constraints = [
            # One membership per (user, company). A user may belong to several
            # companies, but never twice to the same one.
            models.UniqueConstraint(
                fields=['user', 'company'], name='unique_membership_per_user_company',
            ),
            models.CheckConstraint(
                condition=models.Q(commission_rate_percent__gte=Decimal('0.00'))
                & models.Q(commission_rate_percent__lte=Decimal('100.00')),
                name='membership_commission_rate_within_range',
            ),
        ]
        indexes = [
            models.Index(fields=['company', 'is_active']),
            models.Index(fields=['user', 'is_active']),
        ]

    def __str__(self):
        return f"Membership({self.user.username} @ {self.company.name} as {self.role})"

    def clean(self):
        """
        Reject a branch that belongs to a different company.

        Defence in depth: `save()` calls this, so every ORM object write is
        covered, and the admin API validates the same invariant with
        `tenancy.assert_branch_in_company()` BEFORE building the instance — the
        endpoints do not rely on clean() alone.

        Known gap: `bulk_create()` and `queryset.update()` bypass save() and
        therefore this check. Nothing in the codebase uses them for Membership;
        any future code that does must call assert_branch_in_company() itself.
        """
        from django.core.exceptions import ValidationError

        if self.branch_id and self.company_id and self.branch.company_id != self.company_id:
            raise ValidationError(
                {'branch': 'La sucursal no pertenece a la empresa de esta membresía.'}
            )

    def save(self, *args, **kwargs):
        # full_clean is not called automatically by save(); enforce the
        # cross-company branch rule here so the DB never holds a mismatched row.
        self.clean()
        return super().save(*args, **kwargs)

    @property
    def grants_business_access(self) -> bool:
        """A membership only confers company access while both it and the company are active."""
        return self.is_active and self.company.is_active

    @property
    def sees_all_branches(self) -> bool:
        return self.branch_access_mode == self.ACCESS_MODE_ALL


# ---------------------------------------------------------------------------
# SaaS Phase 2A.1 — configurable areas, roles and assignments
# ---------------------------------------------------------------------------
#
# Three surfaces, kept apart on purpose. They are SURFACES, not user types: one
# User may stand on more than one at the same time.
#
#   EXTERNAL PORTAL   the e-commerce. Open to ANY user — and its public parts to
#                     anonymous visitors too. Holding a Membership does not take
#                     it away: a company's own technician is still a customer
#                     when they buy something from it.
#   INTERNAL CONTROL  requires User + active Membership + active Company +
#                     the capabilities of their roles.
#   PLATFORM CONTROL  requires User.is_superuser, and only that.
#
# So a single identity can be all three at once:
#
#   User Carlos
#     ├── buys products as a customer          (external portal)
#     └── Membership @ una empresa             (internal control)
#           └── Técnico
#
# Nothing in this module can turn a user into a platform master.

class CompanyArea(models.Model):
    """
    An internal area of a company (Ventas, Taller, Recepción, Caja…).

    AREAS DO NOT GRANT PERMISSIONS. Belonging to "Inventario" confers no
    authority whatsoever; authority comes from CompanyRole capabilities alone.
    An area exists for organisation, filtering, assignment and reporting.

    The example areas are presets seeded per company, never a closed list:
    every tenant may create its own.
    """

    company = models.ForeignKey(
        Company, on_delete=models.PROTECT, related_name='areas',
    )
    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=120)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    sort_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['company__name', 'sort_order', 'name']
        constraints = [
            # Slug is the stable identifier inside a tenant; name is what people
            # read. Both are unique per company, and neither is unique globally —
            # two tenants may each have a "Ventas".
            models.UniqueConstraint(
                fields=['company', 'slug'], name='unique_area_slug_per_company',
            ),
            models.UniqueConstraint(
                fields=['company', 'name'], name='unique_area_name_per_company',
            ),
        ]
        indexes = [models.Index(fields=['company', 'is_active'])]

    def __str__(self):
        return f"{self.name} ({self.company.name})"


class CompanyRole(models.Model):
    """
    A role a company defines for its own staff, holding a set of capabilities.

    `capabilities` stores capability CODES from store.capabilities — the platform
    owns the vocabulary, the tenant only picks from it. See that module for why
    the catalogue lives in code rather than in a table.
    """

    company = models.ForeignKey(
        Company, on_delete=models.PROTECT, related_name='roles',
    )
    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=120)
    description = models.TextField(blank=True)
    capabilities = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['company__name', 'name']
        constraints = [
            models.UniqueConstraint(
                fields=['company', 'slug'], name='unique_role_slug_per_company',
            ),
            models.UniqueConstraint(
                fields=['company', 'name'], name='unique_role_name_per_company',
            ),
        ]
        indexes = [models.Index(fields=['company', 'is_active'])]

    def __str__(self):
        return f"{self.name} ({self.company.name})"

    @property
    def capability_set(self) -> frozenset[str]:
        """Capabilities this role grants. Empty while the role is inactive."""
        if not self.is_active:
            return frozenset()
        return frozenset(self.capabilities or [])

    def clean(self):
        """
        Reject unknown or reserved capability codes.

        Known gap, same as Membership: bulk_create() and queryset.update() bypass
        save() and therefore this check. The API validates independently in its
        serializer, so no request path depends on clean() alone.
        """
        from django.core.exceptions import ValidationError

        from .capabilities import normalise_capabilities

        try:
            self.capabilities = normalise_capabilities(self.capabilities)
        except ValueError as exc:
            raise ValidationError({'capabilities': str(exc)})

    def save(self, *args, **kwargs):
        self.clean()
        return super().save(*args, **kwargs)


class MembershipRoleAssignment(models.Model):
    """
    Grants one CompanyRole to one Membership, optionally scoped to an area.

    This is what lets a single membership carry several hats:

        User X @ Company A
          ├── Técnico    — área Taller
          └── Recepción  — área Recepción

    without duplicating the Membership (which stays unique per user+company).

    The area is organisational metadata attached to the assignment; it never
    widens or narrows the capabilities the role grants.
    """

    membership = models.ForeignKey(
        Membership, on_delete=models.CASCADE, related_name='role_assignments',
    )
    role = models.ForeignKey(
        CompanyRole, on_delete=models.PROTECT, related_name='assignments',
    )
    area = models.ForeignKey(
        CompanyArea, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='role_assignments',
    )
    is_active = models.BooleanField(default=True, db_index=True)
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='role_assignments_made',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['membership__company__name', 'role__name']
        constraints = [
            # The same role may be held in two different areas, but not twice in
            # the same one.
            models.UniqueConstraint(
                fields=['membership', 'role', 'area'],
                name='unique_role_assignment_per_area',
            ),
        ]
        indexes = [
            models.Index(fields=['membership', 'is_active']),
            models.Index(fields=['role', 'is_active']),
        ]

    def __str__(self):
        return f"{self.membership.user.username} → {self.role.name}"

    def clean(self):
        """
        Structural tenant guard: role and area must belong to the membership's
        own company. Without this, a valid id from another tenant would be a
        cross-company privilege grant.
        """
        from django.core.exceptions import ValidationError

        if not self.membership_id:
            return
        company_id = self.membership.company_id

        if self.role_id and self.role.company_id != company_id:
            raise ValidationError(
                {'role': 'El rol no pertenece a la empresa de esta membresía.'}
            )
        if self.area_id and self.area.company_id != company_id:
            raise ValidationError(
                {'area': 'El área no pertenece a la empresa de esta membresía.'}
            )

    def save(self, *args, **kwargs):
        self.clean()
        return super().save(*args, **kwargs)

    @property
    def grants_capabilities(self) -> bool:
        """An assignment only counts while it, its role and its membership are live."""
        return (
            self.is_active
            and self.role.is_active
            and self.membership.grants_business_access
        )


# ---------------------------------------------------------------------------
# SaaS Phase 2D — multi-branch inventory
# ---------------------------------------------------------------------------
#
# THE SHAPE OF THE PROBLEM
#
#   Company
#     ├── Branch                     a physical stock location
#     ├── Product                    what is sold, company-wide
#     └── BranchStock(branch, product)   how much of it is HERE
#
# Before this phase a product carried one integer and the platform pretended a
# company was one place. That is true of exactly one kind of business — the one
# with a single shop — and false of every business worth selling this platform
# to. Everything below exists to make "how much do we have?" a question that
# cannot be asked without also asking "where?".
#
# AUTHORITY IS TWO INDEPENDENT AXES, AND BOTH MUST PASS
#
#   capability   what you may DO            inventory.adjust
#   branch       where you may do it        MembershipBranchAccess
#
# Neither implies the other. `inventory.adjust` is not permission to adjust
# every branch, and access to a branch is not permission to touch its stock.
# See tenancy.assert_branch_access / has_capability — every write checks both.

class MembershipBranchAccess(models.Model):
    """
    One branch a membership may operate in, while `branch_access_mode` is SELECTED.

    Rows are IGNORED while the membership is in ALL mode. They are not deleted
    when the mode flips, so switching a person to ALL for a week and back does
    not destroy the grants somebody deliberately configured.

    INVARIANT: membership.company == branch.company. A grant that crossed
    companies would be a cross-tenant privilege, which is the one thing the
    whole tenancy layer exists to prevent.
    """

    membership = models.ForeignKey(
        Membership, on_delete=models.CASCADE, related_name='branch_access',
    )
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name='membership_access',
    )
    is_active = models.BooleanField(default=True, db_index=True)
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='branch_access_granted',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['membership__company__name', 'branch__name']
        verbose_name_plural = 'membership branch access'
        constraints = [
            models.UniqueConstraint(
                fields=['membership', 'branch'],
                name='unique_branch_access_per_membership',
            ),
        ]
        indexes = [
            models.Index(fields=['membership', 'is_active']),
            models.Index(fields=['branch', 'is_active']),
        ]

    def __str__(self):
        return f"{self.membership.user.username} → {self.branch.name}"

    def clean(self):
        from django.core.exceptions import ValidationError

        if not (self.membership_id and self.branch_id):
            return
        if self.branch.company_id != self.membership.company_id:
            raise ValidationError(
                {'branch': 'La sucursal no pertenece a la empresa de esta membresía.'}
            )

    def save(self, *args, **kwargs):
        self.clean()
        return super().save(*args, **kwargs)


class BranchStock(models.Model):
    """
    How many units of one product sit in one branch. THE source of truth.

    Rows are created on demand by store.inventory_services and never by a view,
    a serializer or a signal. A product with no row in a branch has zero units
    there — absence and zero mean the same thing, so no code has to distinguish
    them.

    `minimum_stock` / `target_stock` are the replenishment policy FOR THIS
    BRANCH. The same product can be a fast mover downtown and dead weight in a
    satellite shop; one company-wide threshold could not say that, which is why
    the old global `?threshold=` parameter is now only a fallback.

    INVARIANT: branch.company == product.company. Enforced in clean(), in the
    service layer before every write, and structurally by the fact that no view
    ever lets a caller name a branch outside their own tenant.
    """

    branch = models.ForeignKey(
        Branch, on_delete=models.PROTECT, related_name='stock_levels',
    )
    product = models.ForeignKey(
        Product, on_delete=models.PROTECT, related_name='branch_stocks',
    )
    quantity = models.PositiveIntegerField(default=0)
    minimum_stock = models.PositiveIntegerField(default=0)
    target_stock = models.PositiveIntegerField(default=0)
    # --- Commercial Phase C1: replenishment configuration ----------------
    #
    # FOUR NUMBERS, FOUR DISTINCT JOBS. Collapsing any two of them is how a
    # replenishment screen starts giving advice nobody can explain:
    #
    #   minimum_stock  the line below which an operator wants to SEE the product
    #   target_stock   how much to hold after restocking
    #   safety_stock   the buffer the ARITHMETIC keeps against demand variance
    #   lead_time_days how long a resupply actually takes to arrive
    #
    # `safety_stock` is deliberately not `minimum_stock` reused: one is a display
    # threshold a shopkeeper sets by feel, the other is an input to a formula.
    # Making them the same field means changing an alert silently changes what
    # the system tells you to buy.
    safety_stock = models.PositiveIntegerField(default=0)
    # ZERO MEANS UNCONFIGURED, not "arrives instantly". No reorder point is
    # computed without it: a made-up lead time produces a confident number that
    # is wrong, which is worse than saying the setting is missing.
    lead_time_days = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['branch__name', 'product__name']
        constraints = [
            models.UniqueConstraint(
                fields=['branch', 'product'], name='unique_stock_per_branch_product',
            ),
            # PositiveIntegerField already refuses negatives at the ORM layer;
            # this states the same rule to the DATABASE, which is what actually
            # holds when a future raw query or a bulk path skips the ORM.
            models.CheckConstraint(
                condition=models.Q(quantity__gte=0), name='branch_stock_quantity_non_negative',
            ),
            # A target below the minimum would make every replenishment
            # suggestion negative, i.e. "order less than nothing". Zero means
            # "no target set" and is therefore exempt.
            models.CheckConstraint(
                condition=models.Q(target_stock=0) | models.Q(target_stock__gte=models.F('minimum_stock')),
                name='branch_stock_target_at_least_minimum',
            ),
        ]
        indexes = [
            models.Index(fields=['branch', 'quantity']),
            models.Index(fields=['product', 'branch']),
        ]

    def __str__(self):
        return f"{self.product.name} @ {self.branch.name}: {self.quantity}"

    @property
    def needs_replenishment(self) -> bool:
        """At or below the branch minimum, with a minimum actually configured."""
        return self.minimum_stock > 0 and self.quantity <= self.minimum_stock

    @property
    def suggested_quantity(self) -> int:
        """
        Units to bring this branch back up to target. A SUGGESTION, never an order.

        Zero unless the branch is at or below its minimum: topping up a product
        that is comfortably stocked is not replenishment, it is tying up cash.
        """
        if not self.needs_replenishment:
            return 0
        return max(self.target_stock - self.quantity, 0)

    def clean(self):
        from django.core.exceptions import ValidationError

        if not (self.branch_id and self.product_id):
            return
        if self.branch.company_id != self.product.company_id:
            raise ValidationError(
                {'product': 'El producto no pertenece a la empresa de esta sucursal.'}
            )

    def save(self, *args, **kwargs):
        self.clean()
        return super().save(*args, **kwargs)


class StockTransfer(models.Model):
    """
    A movement of stock from one branch to another, as a DOCUMENT.

    THE REASON THIS IS A MODEL AND NOT TWO UPDATES.
    `source -= q; destination += q` in one transaction is arithmetically
    correct and operationally useless: units are not teleported, they travel.
    Between dispatch and receipt they belong to neither branch's shelf, and the
    business needs to know they exist, who sent them and who has not yet
    confirmed arrival. A document holds that; two updates cannot.

    LIFECYCLE — stock moves at the EDGES, never on a status field:

        DRAFT ──dispatch──▶ IN_TRANSIT ──receive──▶ RECEIVED
          │                (source -q)              (dest +q)
          └──cancel──▶ CANCELLED

    Dispatch subtracts from the source. Receipt adds to the destination. There
    is deliberately NO write that does both: crediting the destination at
    dispatch would show stock in a shop that does not physically have it, and
    every stock count there would then be "wrong" by the contents of a van.
    """

    STATUS_DRAFT = 'draft'
    STATUS_IN_TRANSIT = 'in_transit'
    STATUS_RECEIVED = 'received'
    STATUS_CANCELLED = 'cancelled'
    STATUS_CHOICES = [
        (STATUS_DRAFT, 'Borrador'),
        (STATUS_IN_TRANSIT, 'En tránsito'),
        (STATUS_RECEIVED, 'Recibida'),
        (STATUS_CANCELLED, 'Anulada'),
    ]

    company = models.ForeignKey(
        Company, on_delete=models.PROTECT, related_name='stock_transfers',
    )
    source_branch = models.ForeignKey(
        Branch, on_delete=models.PROTECT, related_name='transfers_out',
    )
    destination_branch = models.ForeignKey(
        Branch, on_delete=models.PROTECT, related_name='transfers_in',
    )
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT, db_index=True,
    )
    reason = models.TextField(blank=True)
    reference = models.CharField(max_length=120, blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='transfers_created',
    )
    dispatched_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='transfers_dispatched',
    )
    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='transfers_received',
    )
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='transfers_cancelled',
    )

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    dispatched_at = models.DateTimeField(null=True, blank=True)
    received_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['-created_at', '-id']
        constraints = [
            # Stock cannot travel to where it already is, and a "transfer" that
            # did would produce a matching pair of movements that cancel out —
            # noise in the Kardex describing nothing.
            models.CheckConstraint(
                condition=~models.Q(source_branch=models.F('destination_branch')),
                name='transfer_source_differs_from_destination',
            ),
        ]
        indexes = [
            models.Index(fields=['company', '-created_at']),
            models.Index(fields=['source_branch', 'status']),
            models.Index(fields=['destination_branch', 'status']),
            models.Index(fields=['company', 'status']),
        ]

    def __str__(self):
        return (
            f"Transfer #{self.pk} {self.source_branch_id}→{self.destination_branch_id} "
            f"[{self.status}]"
        )

    @property
    def is_editable(self) -> bool:
        """Items may only change before anything physically moved."""
        return self.status == self.STATUS_DRAFT

    def clean(self):
        from django.core.exceptions import ValidationError

        errors = {}
        if self.source_branch_id and self.destination_branch_id:
            if self.source_branch_id == self.destination_branch_id:
                errors['destination_branch'] = (
                    'El origen y el destino no pueden ser la misma sucursal.'
                )
        if self.company_id:
            if self.source_branch_id and self.source_branch.company_id != self.company_id:
                errors['source_branch'] = 'La sucursal de origen no pertenece a esta empresa.'
            if (
                self.destination_branch_id
                and self.destination_branch.company_id != self.company_id
            ):
                errors['destination_branch'] = (
                    'La sucursal de destino no pertenece a esta empresa.'
                )
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.clean()
        return super().save(*args, **kwargs)


class StockTransferItem(models.Model):
    """One product line of a transfer. Quantities are fixed once dispatched."""

    transfer = models.ForeignKey(
        StockTransfer, on_delete=models.CASCADE, related_name='items',
    )
    product = models.ForeignKey(
        Product, on_delete=models.PROTECT, related_name='transfer_items',
    )
    quantity = models.PositiveIntegerField()

    class Meta:
        ordering = ['product__name']
        constraints = [
            models.UniqueConstraint(
                fields=['transfer', 'product'], name='unique_product_per_transfer',
            ),
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0), name='transfer_item_quantity_positive',
            ),
        ]

    def __str__(self):
        return f"{self.quantity} x {self.product.name}"

    def clean(self):
        from django.core.exceptions import ValidationError

        if not (self.transfer_id and self.product_id):
            return
        if self.product.company_id != self.transfer.company_id:
            raise ValidationError(
                {'product': 'El producto no pertenece a la empresa de esta transferencia.'}
            )

    def save(self, *args, **kwargs):
        self.clean()
        return super().save(*args, **kwargs)


class InventoryCount(models.Model):
    """
    A physical stock count of one branch.

    THE CONCURRENCY PROBLEM THIS MODEL IS SHAPED AROUND.
    Counting is not instantaneous. Somebody walks the shelves for an hour while
    the shop keeps selling. The naive implementation records "system said 10,
    I found 8, therefore -2" and applies -2 at approval — by which time the
    system may say 6, and the correction silently destroys two units that were
    legitimately sold during the count.

    So each item keeps THREE numbers, not two:

        theoretical_at_start    what the system said when counting began
        physical_quantity       what the person actually found
        theoretical_at_approval what the system says at the moment of approval,
                                re-read under lock

    and the correction applied is `physical - theoretical_at_approval`, never
    `physical - theoretical_at_start`. The start value is kept because it is the
    only evidence of what the counter was looking at — an auditor needs it, the
    arithmetic does not.
    """

    STATUS_DRAFT = 'draft'
    STATUS_COUNTING = 'counting'
    STATUS_REVIEW = 'review'
    STATUS_APPROVED = 'approved'
    STATUS_CANCELLED = 'cancelled'
    STATUS_CHOICES = [
        (STATUS_DRAFT, 'Borrador'),
        (STATUS_COUNTING, 'En conteo'),
        (STATUS_REVIEW, 'En revisión'),
        (STATUS_APPROVED, 'Aprobado'),
        (STATUS_CANCELLED, 'Anulado'),
    ]
    # Statuses from which approval is possible. A draft with no counted items is
    # not a count, and an approved one is finished.
    APPROVABLE_STATUSES = frozenset([STATUS_COUNTING, STATUS_REVIEW])
    EDITABLE_STATUSES = frozenset([STATUS_DRAFT, STATUS_COUNTING, STATUS_REVIEW])

    company = models.ForeignKey(
        Company, on_delete=models.PROTECT, related_name='inventory_counts',
    )
    branch = models.ForeignKey(
        Branch, on_delete=models.PROTECT, related_name='inventory_counts',
    )
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT, db_index=True,
    )
    reason = models.TextField(blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='inventory_counts_created',
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='inventory_counts_approved',
    )
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='inventory_counts_cancelled',
    )

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['-created_at', '-id']
        indexes = [
            models.Index(fields=['company', '-created_at']),
            models.Index(fields=['branch', 'status']),
            models.Index(fields=['company', 'status']),
        ]

    def __str__(self):
        return f"Count #{self.pk} @ {self.branch_id} [{self.status}]"

    @property
    def is_editable(self) -> bool:
        return self.status in self.EDITABLE_STATUSES

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.company_id and self.branch_id and self.branch.company_id != self.company_id:
            raise ValidationError(
                {'branch': 'La sucursal no pertenece a la empresa de este recuento.'}
            )

    def save(self, *args, **kwargs):
        self.clean()
        return super().save(*args, **kwargs)


class InventoryCountItem(models.Model):
    """
    One product counted in one InventoryCount.

    `physical_quantity` is null until somebody actually counts it — which is not
    the same as counting zero. A product nobody reached must not be treated as
    "we have none of these" and written down to zero at approval; those items
    are skipped, and the count says how many were left uncounted.
    """

    count = models.ForeignKey(
        InventoryCount, on_delete=models.CASCADE, related_name='items',
    )
    product = models.ForeignKey(
        Product, on_delete=models.PROTECT, related_name='count_items',
    )
    theoretical_at_start = models.IntegerField(default=0)
    physical_quantity = models.PositiveIntegerField(null=True, blank=True)
    theoretical_at_approval = models.IntegerField(null=True, blank=True)
    difference = models.IntegerField(null=True, blank=True)
    note = models.CharField(max_length=250, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['product__name']
        constraints = [
            models.UniqueConstraint(
                fields=['count', 'product'], name='unique_product_per_count',
            ),
        ]
        indexes = [models.Index(fields=['count', 'product'])]

    def __str__(self):
        return f"{self.product.name}: {self.physical_quantity}"

    @property
    def is_counted(self) -> bool:
        return self.physical_quantity is not None

    def clean(self):
        from django.core.exceptions import ValidationError

        if not (self.count_id and self.product_id):
            return
        if self.product.company_id != self.count.company_id:
            raise ValidationError(
                {'product': 'El producto no pertenece a la empresa de este recuento.'}
            )

    def save(self, *args, **kwargs):
        self.clean()
        return super().save(*args, **kwargs)


# ---------------------------------------------------------------------------
# SaaS Phase 3 — company configuration and branding
# ---------------------------------------------------------------------------

def validate_hex_color(value):
    """
    Accept `#RRGGBB` and nothing else.

    A colour configured by a tenant ends up inside a `style` attribute and a CSS
    custom property. Anything richer than six hex digits — `url(...)`, `var(...)`,
    a `javascript:` scheme, a closing brace that escapes the rule — is a CSS
    injection with a colour picker in front of it. Six hex digits cannot express
    any of those, which is the whole reason the format is this narrow.
    """
    import re

    from django.core.exceptions import ValidationError

    if not value:
        return
    if not re.fullmatch(r'#[0-9A-Fa-f]{6}', str(value)):
        raise ValidationError(
            'El color debe tener el formato #RRGGBB (por ejemplo #1A1A1A).'
        )


def validate_timezone_name(value):
    """Accept a real IANA zone name (`America/Lima`), never arbitrary text."""
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    from django.core.exceptions import ValidationError

    if not value:
        return
    try:
        ZoneInfo(str(value))
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        raise ValidationError(
            f'"{value}" no es una zona horaria IANA válida (ejemplo: America/Lima).'
        )


def validate_whatsapp_number(value):
    """
    Digits only, with the country code and no `+`.

    Stored as digits rather than as a finished `https://wa.me/...` link on
    purpose: a URL field is a place to put any URL, and this one is rendered as
    an anchor in emails that customers open. Digits cannot carry a scheme.
    """
    import re

    from django.core.exceptions import ValidationError

    if not value:
        return
    if not re.fullmatch(r'\d{8,15}', str(value)):
        raise ValidationError(
            'El número de WhatsApp debe contener solo dígitos, incluido el código '
            'de país y sin el signo "+" (ejemplo: 51987654321).'
        )


class CompanySettings(models.Model):
    """
    Everything a company can configure about how it presents itself.

    WHY A SEPARATE MODEL AND NOT MORE COLUMNS ON `Company`
    ------------------------------------------------------
    `Company` is STRUCTURAL: who this tenant is to the platform. Its `slug` is
    routing, its `is_active` decides whether the business can transact, and both
    are platform-operator decisions. This model is OPERATIONAL: what the business
    looks like and how it talks to its customers, edited by the business itself.

    Keeping them apart means the endpoint a company administrator uses cannot
    reach `slug` or `is_active` at all — not because a serializer remembers to
    exclude them, but because they are not in the table it writes.

    WHAT IS DELIBERATELY *NOT* HERE
    -------------------------------
      - `name`, `legal_name`, `tax_id` — they already exist on `Company`.
        Duplicating them as `public_name` and friends would create two answers to
        "what is this business called" with no rule for which wins. Where a
        distinction is genuinely needed later, it can be added then, with the
        rule written down.
      - SMTP credentials, API keys, any secret. Transport stays in the
        platform's environment. A tenant-editable table is the wrong place for a
        password, and no UI here should ever ask for one.
      - Series and correlativos. That is Phase 2E, and this model is where its
        configuration will land.
    """

    company = models.OneToOneField(
        Company, on_delete=models.CASCADE, related_name='settings',
    )

    # --- Contact, as customers see it ------------------------------------
    contact_email = models.EmailField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    whatsapp_number = models.CharField(
        max_length=20, blank=True, validators=[validate_whatsapp_number],
        help_text='Solo dígitos, con código de país y sin "+". Ej: 51987654321',
    )
    website_url = models.URLField(max_length=300, blank=True)
    # Two social links, not a generic list. A JSON blob of arbitrary
    # network/URL pairs would be a place to put anything, rendered as an anchor
    # in a public footer; two URLFields are validated by Django and are what the
    # footer actually renders.
    facebook_url = models.URLField(max_length=300, blank=True)
    instagram_url = models.URLField(max_length=300, blank=True)
    legal_address = models.CharField(max_length=300, blank=True)
    city = models.CharField(max_length=120, blank=True)
    country_code = models.CharField(max_length=2, blank=True, default='')

    # --- Branding ---------------------------------------------------------
    #
    # Six colours, each mapping to exactly one CSS custom property the storefront
    # already consumes. A seventh with no component reading it would be a field
    # nobody fills and nobody notices is empty.
    logo_url = models.URLField(max_length=500, blank=True)
    primary_color = models.CharField(
        max_length=7, blank=True, validators=[validate_hex_color],
    )
    accent_color = models.CharField(
        max_length=7, blank=True, validators=[validate_hex_color],
    )
    background_color = models.CharField(
        max_length=7, blank=True, validators=[validate_hex_color],
    )
    surface_color = models.CharField(
        max_length=7, blank=True, validators=[validate_hex_color],
    )
    text_color = models.CharField(
        max_length=7, blank=True, validators=[validate_hex_color],
    )
    border_color = models.CharField(
        max_length=7, blank=True, validators=[validate_hex_color],
    )

    # --- Internal document numbering (Phase 2E) --------------------------
    #
    # WHERE THE SCOPE LIVES, AND WHY IT IS HERE RATHER THAN ON THE SERIES.
    #
    # It is a POLICY — "this business numbers per company" or "per branch" — not
    # a counter, so it does not belong on a row that is locked during every
    # issuance. Making it explicit rather than inferring it from which sequence
    # rows exist matters for the same reason `branch_access_mode` is explicit:
    # the presence of a branch row cannot distinguish a deliberate choice from a
    # leftover, and guessing wrong here silently splits or merges a company's
    # numbering.
    #
    # ONE COLUMN, ONE DOCUMENT TYPE, on purpose. This phase implements sales
    # notes and nothing else; a generic policy table for document types that do
    # not exist would be scaffolding around an empty room. When a second type
    # arrives, that phase decides whether to add a column or generalise — with a
    # real second case to design against.
    SEQUENCE_SCOPE_COMPANY = 'company'
    SEQUENCE_SCOPE_BRANCH = 'branch'
    SEQUENCE_SCOPE_CHOICES = [
        (SEQUENCE_SCOPE_COMPANY, 'Una numeración para toda la empresa'),
        (SEQUENCE_SCOPE_BRANCH, 'Una numeración por sucursal'),
    ]
    sales_note_sequence_scope = models.CharField(
        max_length=16, choices=SEQUENCE_SCOPE_CHOICES, default=SEQUENCE_SCOPE_COMPANY,
    )

    # --- Business ---------------------------------------------------------
    timezone = models.CharField(
        max_length=64, blank=True, validators=[validate_timezone_name],
        help_text='Zona horaria IANA. Ej: America/Lima',
    )
    # READ-ONLY IN THE UI, AND THAT IS THE POINT.
    #
    # The value is stored so the model is ready for multi-currency, but checkout
    # charges through Stripe in a single currency configured at the platform
    # level. A settings screen that let a tenant pick USD while Stripe billed PEN
    # would be a lie with a dropdown on it. See docs/saas-multiempresa.md.
    currency = models.CharField(max_length=3, blank=True, default='PEN')

    # --- Commercial policies ---------------------------------------------
    #
    # Plain text, escaped wherever it is rendered. Not HTML: this string reaches
    # customer inboxes, and accepting markup would make every tenant's settings
    # form an HTML-injection vector into other people's email clients.
    warranty_policy_text = models.TextField(blank=True, max_length=2000)
    warranty_policy_url = models.URLField(max_length=300, blank=True)
    terms_url = models.URLField(max_length=300, blank=True)
    privacy_url = models.URLField(max_length=300, blank=True)

    # --- Internal notifications ------------------------------------------
    #
    # WHERE THIS COMPANY'S NEW-SALE ALERTS GO. Before Phase 3 there was one
    # platform-wide address, which meant a second tenant's sales would have been
    # announced in the pilot's inbox — customer names, phone numbers and all.
    # Empty means no notification is sent, and that is the safe answer: silence
    # is recoverable, a leak is not.
    order_notification_email = models.EmailField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'company settings'
        verbose_name_plural = 'company settings'

    def __str__(self):
        return f"Settings({self.company.name})"

    def clean(self):
        """
        Run the field validators on every ORM write, not only on form input.

        `full_clean()` is not called by `save()`, so a validator attached to a
        field protects a serializer and a Django admin form and nothing else.
        Calling it here means a colour written by a management command or a data
        migration is checked too.
        """
        from django.core.exceptions import ValidationError

        errors = {}
        for field, validator in (
            ('primary_color', validate_hex_color),
            ('accent_color', validate_hex_color),
            ('background_color', validate_hex_color),
            ('surface_color', validate_hex_color),
            ('text_color', validate_hex_color),
            ('border_color', validate_hex_color),
            ('timezone', validate_timezone_name),
            ('whatsapp_number', validate_whatsapp_number),
        ):
            try:
                validator(getattr(self, field))
            except ValidationError as exc:
                errors[field] = exc.messages
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.clean()
        return super().save(*args, **kwargs)


# ---------------------------------------------------------------------------
# SaaS Phase 2E — internal document sequences
# ---------------------------------------------------------------------------

def validate_sequence_prefix(value):
    """
    Letters, digits, hyphen and underscore. Nothing else.

    This string is typed by a tenant and ends up in a PDF, in the internal UI,
    and — through `_safe_slug` — in a `Content-Disposition` header. The character
    set is therefore the smallest one that still expresses a real series name.

    `/` is DELIBERATELY EXCLUDED even though `NV/2026/` is a plausible
    convention. It is a path separator; allowing it would put `../` one typo away
    from a filename builder, and the convention buys nothing for an internal
    document. CR, LF, quotes, angle brackets and control characters are excluded
    for the same reason: header injection and markup are the two ways a string
    like this stops being a label.
    """
    import re

    from django.core.exceptions import ValidationError

    if value in (None, ''):
        return
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,12}', str(value)):
        raise ValidationError(
            'El prefijo solo admite letras, dígitos, guion y guion bajo, '
            'con un máximo de 12 caracteres.'
        )


class InternalSequence(models.Model):
    """
    A counter for one kind of INTERNAL document, owned by one company.

    WHAT WAS WRONG BEFORE
    ---------------------
    `SalesNote.number` was allocated with `MAX(number) + 1` over the whole table
    and protected by a GLOBAL unique constraint. Two consequences, both bad:

      - Company A issued NV-000001, company B got NV-000002, company A got
        NV-000003. Each tenant saw gaps it could not explain, because its
        numbering was interleaved with a stranger's.
      - The next value was recovered by PARSING a formatted string, so changing
        the prefix or the padding changed what "the last number" meant.

    A counter is a number. This model stores it as one.

    CONFIGURATION vs STATE
    ----------------------
      prefix, padding   configuration — how a number is DISPLAYED
      next_value        transactional state — the next ordinal to hand out

    They live in the same row because they are per-series, but only the second is
    written during an issuance, and issuance locks THIS row and nothing else.
    Locking `CompanySettings` instead would make two people issuing notes in
    different branches queue behind each other for no reason, and would block the
    whole company's configuration while a PDF number is allocated.

    SCOPE
    -----
    `branch IS NULL`  the company-level series. Always exists, created by
                      provisioning. Under company scope it is the counter; under
                      branch scope it is the TEMPLATE new branch series copy
                      their prefix and padding from.
    `branch` set      one series per branch, created on demand.

    Which one an issuance uses is decided by
    `CompanySettings.sales_note_sequence_scope` — never by which rows happen to
    exist, because "a branch row is present" cannot distinguish a deliberate
    choice from a leftover.
    """

    # A choice exists here only when the document it names exists. 2E shipped
    # one; M8 added the second when the technical-service domain arrived, which
    # is exactly the condition this comment has always stated. The rest of the
    # roadmap is still absent, and stays absent until its domain lands.
    DOCUMENT_SALES_NOTE = 'sales_note'
    DOCUMENT_REPAIR_ORDER = 'repair_order'
    DOCUMENT_TYPE_CHOICES = [
        (DOCUMENT_SALES_NOTE, 'Nota de venta interna'),
        (DOCUMENT_REPAIR_ORDER, 'Orden de servicio técnico'),
    ]

    MIN_PADDING = 1
    MAX_PADDING = 12

    company = models.ForeignKey(
        Company, on_delete=models.PROTECT, related_name='sequences',
    )
    branch = models.ForeignKey(
        Branch, null=True, blank=True, on_delete=models.PROTECT,
        related_name='sequences',
    )
    document_type = models.CharField(
        max_length=32, choices=DOCUMENT_TYPE_CHOICES, db_index=True,
    )

    prefix = models.CharField(
        max_length=12, blank=True, validators=[validate_sequence_prefix],
    )
    padding = models.PositiveSmallIntegerField(default=6)

    # THE COUNTER. A number, not a string to be parsed back.
    #
    # Monotonic and never recycled: a number handed out is spent, even if the
    # document it was going to identify was never created. Gaps are cheaper than
    # the alternative, which is two documents that once shared an identifier.
    next_value = models.PositiveBigIntegerField(default=1)

    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['company__name', 'document_type', 'branch__name']
        constraints = [
            # TWO CONDITIONAL UNIQUES, not one over (company, branch, type).
            #
            # In SQL, NULL is not equal to NULL, so a plain unique including
            # `branch` would let a company hold any number of company-level rows
            # for the same document type — exactly the row that must be unique.
            models.UniqueConstraint(
                fields=['company', 'document_type'],
                condition=models.Q(branch__isnull=True),
                name='unique_company_sequence_per_document',
            ),
            models.UniqueConstraint(
                fields=['company', 'branch', 'document_type'],
                condition=models.Q(branch__isnull=False),
                name='unique_branch_sequence_per_document',
            ),
            models.CheckConstraint(
                condition=models.Q(padding__gte=1) & models.Q(padding__lte=12),
                name='sequence_padding_within_range',
            ),
            models.CheckConstraint(
                condition=models.Q(next_value__gte=1),
                name='sequence_next_value_positive',
            ),
        ]
        indexes = [
            models.Index(fields=['company', 'document_type']),
            models.Index(fields=['branch', 'document_type']),
        ]

    def __str__(self):
        where = self.branch.name if self.branch_id else 'empresa'
        return f'{self.document_type} @ {self.company.name} ({where})'

    def format(self, value: int) -> str:
        """
        `NV-` + `42` padded to 6 → `NV-000042`.

        Used ONLY when a number is issued. A document already issued keeps the
        string it was given; re-rendering it from the current configuration would
        make a receipt printed last year change when somebody edits the prefix.
        """
        return f'{self.prefix}{str(int(value)).zfill(self.padding)}'

    @property
    def preview(self) -> str:
        """What the NEXT issued number would look like. Allocates nothing."""
        return self.format(self.next_value)

    @property
    def has_issued(self) -> bool:
        """
        Whether this series has ever handed out a number.

        Read from `next_value`, not by counting documents: a number spent by a
        rolled-back issuance is still spent, and the counter is the authority on
        that.
        """
        return self.next_value > 1

    def clean(self):
        from django.core.exceptions import ValidationError

        errors = {}
        if self.branch_id and self.company_id and self.branch.company_id != self.company_id:
            errors['branch'] = 'La sucursal no pertenece a la empresa de esta serie.'
        if not (self.MIN_PADDING <= (self.padding or 0) <= self.MAX_PADDING):
            errors['padding'] = (
                f'Los dígitos deben estar entre {self.MIN_PADDING} y {self.MAX_PADDING}.'
            )
        if (self.next_value or 0) < 1:
            errors['next_value'] = 'El próximo número debe ser 1 o mayor.'
        try:
            validate_sequence_prefix(self.prefix)
        except ValidationError as exc:
            errors['prefix'] = exc.messages
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.clean()
        return super().save(*args, **kwargs)


# ---------------------------------------------------------------------------
# SaaS Phase 4 — customers (CRM)
# ---------------------------------------------------------------------------

def normalize_customer_email(value: str) -> str:
    """
    Lowercase and trim. Nothing else.

    Deliberately NOT doing the clever normalisations (stripping Gmail dots,
    cutting `+tags`): they are provider-specific folklore, they are wrong for
    some hosts, and here they would silently merge two people who typed two
    different addresses. Email is not an identity key in this design anyway —
    see `Customer`.
    """
    return (value or '').strip().lower()


def normalize_customer_phone(value: str) -> str:
    """
    Collapse whitespace, keep digits and the leading `+`.

    NOT a libphonenumber-grade parser, and not trying to be: the platform serves
    one country today, numbers arrive from WhatsApp, from a form and from a
    receptionist's memory, and a strict parser would reject valid input at the
    counter. What this guarantees is that `+51 999 111 222`, `+51999111222` and
    ` +51-999-111-222 ` land on the same string, so a search finds them.
    """
    raw = (value or '').strip()
    if not raw:
        return ''
    plus = '+' if raw.startswith('+') else ''
    digits = ''.join(ch for ch in raw if ch.isdigit())
    return f'{plus}{digits}'


def normalize_document_number(value: str) -> str:
    """Trim and uppercase. `ce-x123` and `CE-X123` are the same document."""
    return (value or '').strip().upper()


class Customer(models.Model):
    """
    A commercial client of ONE company — the CRM record.

    WHAT THIS IS NOT
    ----------------
    Not a `User`. A `User` is a platform login; most clients of a repair shop
    walk in, phone, or write on WhatsApp and will never have one. A Customer
    exists with no login at all, and that is the normal case rather than the
    exception.

    Not a `Membership`. A Membership says a person is STAFF of a company. A
    Customer says a person BUYS from it. The same human can be both, in different
    companies or even in the same one, and neither implies the other.

    Not the `customer_*` fields on `Order`. Those are a SNAPSHOT of who bought,
    frozen at the sale. This is who the client is TODAY. When someone changes
    their phone number, this row changes and last year's order does not — that is
    the entire reason both exist.

    ONE PERSON, SEVERAL COMPANIES, SEVERAL RECORDS
    ----------------------------------------------
    `Customer(company=A, user=X)` and `Customer(company=B, user=X)` are two
    independent records. They share a login and nothing else: not notes, not
    address, not history. Two businesses that happen to serve the same person
    must not be able to read each other's file on them.
    """

    TYPE_PERSON = 'person'
    TYPE_BUSINESS = 'business'
    TYPE_CHOICES = [
        (TYPE_PERSON, 'Persona'),
        (TYPE_BUSINESS, 'Empresa'),
    ]

    company = models.ForeignKey(
        Company, on_delete=models.PROTECT, related_name='customers',
    )
    # OPTIONAL, and the option is the point. A null here is not missing data: it
    # is a client who has no account and does not need one.
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='customer_records',
    )
    customer_type = models.CharField(
        max_length=16, choices=TYPE_CHOICES, default=TYPE_PERSON, db_index=True,
    )

    first_name = models.CharField(max_length=120, blank=True)
    last_name = models.CharField(max_length=120, blank=True)
    business_name = models.CharField(max_length=200, blank=True)

    document_type = models.CharField(
        max_length=10, choices=DocumentType.choices, blank=True,
    )
    document_number = models.CharField(max_length=20, blank=True)

    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)

    address_line = models.CharField(max_length=300, blank=True)
    district = models.CharField(max_length=100, blank=True)
    city = models.CharField(max_length=100, blank=True)

    # INTERNAL. Never returned by a public endpoint — there is no public
    # endpoint for this model at all, which is the real guarantee.
    notes = models.TextField(max_length=2000, blank=True)

    is_active = models.BooleanField(default=True, db_index=True)

    # Traceability only. Never consulted for permissions: who typed a record in
    # is not a reason to let them read it later.
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='customers_created',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['last_name', 'first_name', 'business_name', 'pk']
        indexes = [
            models.Index(fields=['company', 'is_active']),
            models.Index(fields=['company', 'document_number']),
            models.Index(fields=['company', 'email']),
            models.Index(fields=['company', 'phone']),
            models.Index(fields=['company', 'created_at']),
            models.Index(fields=['company', 'last_name', 'first_name']),
            models.Index(fields=['company', 'business_name']),
        ]
        constraints = [
            # One CRM record per login per company. Conditional because SQL
            # treats NULLs as distinct, which is exactly what is wanted here: any
            # number of customers may have no account.
            models.UniqueConstraint(
                fields=['company', 'user'],
                condition=models.Q(user__isnull=False),
                name='unique_customer_per_user_per_company',
            ),
            # A document identifies one client INSIDE one company. Company A and
            # company B may each hold DNI 12345678 — they are different files on
            # the same person, and neither can see the other.
            models.UniqueConstraint(
                fields=['company', 'document_type', 'document_number'],
                condition=~models.Q(document_number=''),
                name='unique_customer_document_per_company',
            ),
            # A record must be identifiable as SOMETHING. A row with no name, no
            # business name and no document is not a client, it is an empty form
            # that will be re-created tomorrow by whoever cannot find it.
            models.CheckConstraint(
                condition=(
                    ~models.Q(first_name='')
                    | ~models.Q(last_name='')
                    | ~models.Q(business_name='')
                    | ~models.Q(document_number='')
                ),
                name='customer_has_some_identity',
            ),
        ]

    def __str__(self):
        return f'{self.display_name} ({self.company.name})'

    # -- identity ---------------------------------------------------------

    @property
    def display_name(self) -> str:
        """
        What to call this client on screen.

        A business is its registered name; a person is their name. Falls back to
        the other field rather than showing an empty row: a business whose
        `business_name` was never filled is still better identified by the
        contact person's name than by nothing.
        """
        if self.customer_type == self.TYPE_BUSINESS:
            return (
                self.business_name.strip()
                or f'{self.first_name} {self.last_name}'.strip()
                or self.document_number
                or f'Cliente #{self.pk}'
            )
        return (
            f'{self.first_name} {self.last_name}'.strip()
            or self.business_name.strip()
            or self.document_number
            or f'Cliente #{self.pk}'
        )

    @property
    def has_account(self) -> bool:
        return self.user_id is not None

    # -- normalisation ----------------------------------------------------

    def clean(self):
        from django.core.exceptions import ValidationError

        self.email = normalize_customer_email(self.email)
        self.phone = normalize_customer_phone(self.phone)
        self.document_number = normalize_document_number(self.document_number)
        self.first_name = (self.first_name or '').strip()
        self.last_name = (self.last_name or '').strip()
        self.business_name = (self.business_name or '').strip()

        errors = {}

        # A document number with no type is unidentifiable, and a type with no
        # number is noise. Either both or neither.
        if self.document_number and not self.document_type:
            errors['document_type'] = ['Indica el tipo de documento.']
        if self.document_type and not self.document_number:
            errors['document_number'] = ['Indica el número de documento.']

        if self.customer_type == self.TYPE_BUSINESS:
            if not self.business_name:
                errors['business_name'] = ['La razón social es obligatoria para una empresa.']
        else:
            if not self.first_name and not self.last_name:
                errors['first_name'] = ['Indica al menos un nombre o apellido.']

        # RUC is NOT required of a business, on purpose. A neighbourhood shop
        # that brings in a laptop is a business the moment the counter says so,
        # and refusing to file it until somebody produces a tax id would push the
        # record out of the system and onto paper.

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.clean()
        return super().save(*args, **kwargs)


# ---------------------------------------------------------------------------
# Commercial Phase C1 — barcodes
# ---------------------------------------------------------------------------

_BARCODE_ALLOWED = re.compile(r'^[\x21-\x7E]{4,64}$')


def normalize_barcode(value: str) -> str:
    """
    Trim the outside, keep everything else exactly as scanned.

    NOT uppercased and NOT cast to an integer, and both of those are the point.

    A barcode is a STRING of symbols, not a number. `0123456789012` and
    `123456789012` are different articles, and an int cast silently merges them.
    Code128 can carry mixed case that a scanner reproduces faithfully, so
    upper-casing would make two distinct codes collide.

    What is stripped is the wrapping whitespace a keyboard-wedge scanner adds
    around the payload — most importantly the trailing CR/LF it sends instead of
    Enter, which would otherwise become part of the stored code and never match
    again.
    """
    return (value or '').strip()


def validate_barcode(value):
    """
    Printable ASCII, 4 to 64 characters.

    Control characters are refused rather than stripped: a code containing one
    means the scanner or the form sent something unexpected, and silently
    repairing it would store a code that no future scan reproduces.
    """
    from django.core.exceptions import ValidationError

    if not _BARCODE_ALLOWED.fullmatch(str(value or '')):
        raise ValidationError(
            'El código debe tener entre 4 y 64 caracteres imprimibles, sin espacios '
            'ni caracteres de control.'
        )


class ProductBarcode(models.Model):
    """
    A scannable code that identifies one product inside one company.

    WHY A TABLE AND NOT `Product.barcode`
    -------------------------------------
    One article routinely carries several codes: the manufacturer's EAN, a
    distributor's UPC, and the shop's own internal label stuck over both. A
    single field forces a choice between them, and whichever loses stops
    scanning — which the person at the counter experiences as "the system does
    not have this product".

    WHY `company` IS DUPLICATED HERE
    --------------------------------
    It is reachable through `product.company`, and it is stored anyway so that
    the uniqueness constraint and every lookup are expressed directly in this
    table. A scan is untrusted input arriving at speed; resolving it must be one
    indexed query scoped to the caller's company, not a join that some future
    refactor could widen. `clean()` keeps the two in agreement.
    """

    EAN13 = 'ean13'
    EAN8 = 'ean8'
    UPCA = 'upca'
    CODE128 = 'code128'
    CODE39 = 'code39'
    INTERNAL = 'internal'
    UNKNOWN = 'unknown'
    SYMBOLOGY_CHOICES = [
        (EAN13, 'EAN-13'),
        (EAN8, 'EAN-8'),
        (UPCA, 'UPC-A'),
        (CODE128, 'Code 128'),
        (CODE39, 'Code 39'),
        (INTERNAL, 'Código interno'),
        (UNKNOWN, 'Sin especificar'),
    ]

    company = models.ForeignKey(
        Company, on_delete=models.PROTECT, related_name='product_barcodes',
    )
    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name='barcodes',
    )
    code = models.CharField(max_length=64, validators=[validate_barcode])
    # Informational only. A keyboard-wedge scanner sends the digits and nothing
    # else, so nothing may DEPEND on this being right — lookup is by code alone.
    symbology = models.CharField(
        max_length=16, choices=SYMBOLOGY_CHOICES, default=UNKNOWN,
    )
    is_primary = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-is_primary', 'code']
        indexes = [
            models.Index(fields=['company', 'code']),
            models.Index(fields=['product', 'is_active']),
        ]
        constraints = [
            # A code identifies ONE article inside a company. Across companies it
            # may repeat freely: two shops selling the same manufacturer's cable
            # scan the same EAN, and neither can see the other's catalogue.
            models.UniqueConstraint(
                fields=['company', 'code'],
                name='unique_barcode_per_company',
            ),
            # At most one primary per product. Conditional, because "not primary"
            # is the normal state and must not be constrained at all.
            models.UniqueConstraint(
                fields=['product'],
                condition=models.Q(is_primary=True),
                name='unique_primary_barcode_per_product',
            ),
        ]

    def __str__(self):
        return f'{self.code} → {self.product.name}'

    def clean(self):
        from django.core.exceptions import ValidationError

        self.code = normalize_barcode(self.code)
        validate_barcode(self.code)

        if self.product_id and self.company_id:
            if self.product.company_id != self.company_id:
                raise ValidationError(
                    {'product': 'El producto no pertenece a esta empresa.'}
                )

    def save(self, *args, **kwargs):
        # The company is DERIVED from the product rather than accepted, so the
        # two cannot disagree even if a caller passes the wrong one.
        if self.product_id and not self.company_id:
            self.company_id = self.product.company_id
        self.clean()
        return super().save(*args, **kwargs)


# ---------------------------------------------------------------------------
# Commercial Phase C1.2 — sales commissions
# ---------------------------------------------------------------------------

class SalesCommission(models.Model):
    """
    What a company owes a seller for one sale.

    WHY ITS OWN TABLE RATHER THAN THREE COLUMNS ON `Order`
    ------------------------------------------------------
    Columns would have been less code today and a migration later. A commission
    is not a property of a sale, it is an OBLIGATION with a life of its own: it
    is accrued now, it may be voided when the goods come back, and eventually it
    is settled and paid in a batch alongside dozens of others. None of that fits
    in three fields hanging off the order it happened to originate from.

    The row is written only when there is something to owe. A seller on 0% does
    not generate a row of zeros — a ledger should list obligations, and "nothing
    is owed" is not one. That also keeps the table meaningful to count.

    EVERYTHING IS FROZEN AT THE SALE
    --------------------------------
    The rate, the base and the amount are all snapshots. When somebody is moved
    from 3% to 5%, last month's sales stay at 3%: the company agreed to pay 3%
    for those, and recomputing them from today's rate would rewrite a debt after
    the fact. `seller_name_snapshot` exists for the same reason — the ledger must
    still name whose money it is after an account is deleted.
    """

    STATUS_ACCRUED = 'accrued'
    STATUS_VOIDED = 'voided'
    STATUS_CHOICES = [
        # ACCRUED: earned and owed. Deliberately NOT called "pending", which
        # would imply a payment process that does not exist yet.
        (STATUS_ACCRUED, 'Devengada'),
        # VOIDED: the sale came back. Written by a future returns flow; nothing
        # in this phase sets it, and pretending otherwise would be a fiction.
        (STATUS_VOIDED, 'Anulada'),
    ]

    company = models.ForeignKey(
        Company, on_delete=models.PROTECT, related_name='sales_commissions',
    )
    order = models.OneToOneField(
        Order, on_delete=models.PROTECT, related_name='sales_commission',
    )
    seller = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='sales_commissions',
    )
    seller_name_snapshot = models.CharField(max_length=150, blank=True, default='')

    rate_percent = models.DecimalField(max_digits=5, decimal_places=2)
    # The NET sale — what the customer actually paid for goods. Commission is
    # not owed on money the shop never received, so the discount comes off
    # before the rate is applied.
    base_amount = models.DecimalField(max_digits=12, decimal_places=2)
    amount = models.DecimalField(max_digits=12, decimal_places=2)

    status = models.CharField(
        max_length=12, choices=STATUS_CHOICES, default=STATUS_ACCRUED,
        db_index=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['company', 'seller', 'created_at']),
            models.Index(fields=['company', 'status']),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(rate_percent__gte=Decimal('0.00'))
                & models.Q(rate_percent__lte=Decimal('100.00')),
                name='commission_rate_within_range',
            ),
            models.CheckConstraint(
                condition=models.Q(amount__gte=Decimal('0.00')),
                name='commission_amount_not_negative',
            ),
            models.CheckConstraint(
                condition=models.Q(base_amount__gte=Decimal('0.00')),
                name='commission_base_not_negative',
            ),
        ]

    def __str__(self):
        return f'{self.seller_name_snapshot or "?"} — {self.amount} (#{self.order_id})'

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.order_id and self.company_id:
            if self.order.company_id != self.company_id:
                raise ValidationError(
                    {'order': 'El pedido no pertenece a esta empresa.'}
                )

    def save(self, *args, **kwargs):
        if self.order_id and not self.company_id:
            self.company_id = self.order.company_id
        self.clean()
        return super().save(*args, **kwargs)


# ---------------------------------------------------------------------------
# Commercial Phase C1.3 — automatic promotions and combos
# ---------------------------------------------------------------------------

class Promotion(models.Model):
    """
    A discount the business configured in advance, applied AUTOMATICALLY.

    PROMOTION VERSUS COUPON, and why both exist
    -------------------------------------------
    A `Coupon` is activated by somebody typing a CODE. A `Promotion` fires on
    its own the moment a basket qualifies. They are the same idea — the shop
    decided in advance to charge less — arriving through opposite doors, and
    collapsing them would mean either every promotion needs a code somebody must
    remember, or every coupon fires without being asked for.

    A COMBO IS NOT A PRODUCT, and this is the decision that shapes everything
    -------------------------------------------------------------------------
    The tempting design is a `Product` called "Combo iPhone + case + glass" with
    its own price. It is wrong, and expensively so: that product would need its
    own stock, which does not exist. Selling one would have to decrement three
    other articles through some bespoke path, the Kardex would show a sale of a
    thing that was never on a shelf, and every stock report would have to learn
    which products are real.

    So a combo changes ONLY the money. The order still contains the three real
    articles, three real `SALE_EXIT` movements still leave three real shelves,
    and the promotion records that the customer paid less for buying them
    together.
    """

    BUNDLE_FIXED_PRICE = 'bundle_fixed_price'
    BUNDLE_PERCENT = 'bundle_percent'
    TYPE_CHOICES = [
        (BUNDLE_FIXED_PRICE, 'Combo a precio fijo'),
        (BUNDLE_PERCENT, 'Combo con porcentaje de descuento'),
    ]

    SCOPE_ALL = 'all'
    SCOPE_SELECTED = 'selected'
    SCOPE_CHOICES = [
        (SCOPE_ALL, 'Todas las sucursales'),
        (SCOPE_SELECTED, 'Sucursales seleccionadas'),
    ]

    company = models.ForeignKey(
        Company, on_delete=models.PROTECT, related_name='promotions',
    )
    name = models.CharField(max_length=150)
    promotion_type = models.CharField(max_length=24, choices=TYPE_CHOICES)

    # Higher wins. Two promotions that both want the same unit are resolved by
    # this and then by id — deterministic, explainable, and under the admin's
    # control. See `promotion_services` for why not global optimisation.
    priority = models.IntegerField(default=0, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)

    # EXPLICIT, never inferred from an empty table. "No rows means everywhere"
    # would turn deleting the last branch row into a silent widening of scope —
    # the same fail-open trap `Membership.branch_access_mode` avoids.
    branch_scope = models.CharField(
        max_length=10, choices=SCOPE_CHOICES, default=SCOPE_ALL,
    )

    # The benefit. Exactly one is meaningful per type.
    fixed_price = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
    )
    discount_percent = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
    )

    # Null means no ceiling: a basket with six qualifying sets gets six.
    max_applications_per_order = models.PositiveSmallIntegerField(
        null=True, blank=True,
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='promotions_created',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-priority', 'name']
        indexes = [
            models.Index(fields=['company', 'is_active', 'priority']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['company', 'name'], name='unique_promotion_name_per_company',
            ),
            models.CheckConstraint(
                condition=models.Q(discount_percent__isnull=True)
                | (models.Q(discount_percent__gt=Decimal('0.00'))
                   & models.Q(discount_percent__lte=Decimal('100.00'))),
                name='promotion_percent_within_range',
            ),
            models.CheckConstraint(
                condition=models.Q(fixed_price__isnull=True)
                | models.Q(fixed_price__gte=Decimal('0.00')),
                name='promotion_fixed_price_not_negative',
            ),
        ]

    def __str__(self):
        return f'{self.name} ({self.company.name})'

    def is_live(self, at=None) -> bool:
        """Active and inside its window. Not branch-aware — see `applies_to`."""
        if not self.is_active:
            return False
        at = at or timezone.now()
        if self.starts_at and at < self.starts_at:
            return False
        if self.ends_at and at > self.ends_at:
            return False
        return True

    def applies_to_branch(self, branch) -> bool:
        if self.branch_scope == self.SCOPE_ALL:
            return True
        if branch is None:
            return False
        # SELECTED with no rows applies NOWHERE. Fail closed: a promotion whose
        # branch list was emptied should stop, not spread.
        return self.branches.filter(branch=branch).exists()

    def clean(self):
        from django.core.exceptions import ValidationError

        errors = {}
        if self.starts_at and self.ends_at and self.starts_at >= self.ends_at:
            errors['ends_at'] = ['La fecha de fin debe ser posterior a la de inicio.']

        if self.promotion_type == self.BUNDLE_FIXED_PRICE:
            if self.fixed_price is None:
                errors['fixed_price'] = ['Indica el precio del combo.']
        elif self.promotion_type == self.BUNDLE_PERCENT:
            if self.discount_percent is None:
                errors['discount_percent'] = ['Indica el porcentaje de descuento.']
            elif not (Decimal('0') < self.discount_percent <= Decimal('100')):
                errors['discount_percent'] = ['Debe estar entre 0 y 100.']

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.clean()
        return super().save(*args, **kwargs)


class PromotionBranch(models.Model):
    """One branch a SELECTED-scope promotion runs in."""

    promotion = models.ForeignKey(
        Promotion, on_delete=models.CASCADE, related_name='branches',
    )
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name='promotions',
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['promotion', 'branch'], name='unique_promotion_branch',
            ),
        ]

    def __str__(self):
        return f'{self.promotion.name} @ {self.branch.name}'

    def clean(self):
        """
        INVARIANT: `branch.company == promotion.company`.

        The API checks this before it ever builds one of these, walking DOWN
        from the company so a foreign branch is simply not in the candidate set.
        This is the second lock on the same door: a shell, a data fix or a
        future endpoint that skips that check would otherwise attach another
        tenant's branch to this tenant's promotion — and because
        `applies_to_branch()` reads exactly this table, the effect would be a
        discount firing in a shop that belongs to someone else.
        """
        from django.core.exceptions import ValidationError

        if self.promotion_id and self.branch_id:
            if self.branch.company_id != self.promotion.company_id:
                raise ValidationError(
                    {'branch': 'La sucursal no pertenece a la empresa de la promoción.'}
                )

    def save(self, *args, **kwargs):
        self.clean()
        return super().save(*args, **kwargs)

    @staticmethod
    def assert_all_match_promotion(rows):
        """
        Set-level check for bulk paths.

        `bulk_create()` does NOT call `save()` and therefore does NOT call
        `clean()` — so the guarantee above evaporates for exactly the code that
        writes many rows at once, which is the code that writes these. Any
        authorised bulk path calls this first.

        Resolved with one query over the whole set rather than one per row: a
        promotion covering twenty branches should cost one round trip.
        """
        from django.core.exceptions import ValidationError

        rows = list(rows)
        if not rows:
            return
        promotion_ids = {r.promotion_id for r in rows}
        branch_ids = {r.branch_id for r in rows}
        companies = dict(
            Promotion.objects.filter(pk__in=promotion_ids)
            .values_list('pk', 'company_id')
        )
        branch_companies = dict(
            Branch.objects.filter(pk__in=branch_ids)
            .values_list('pk', 'company_id')
        )
        for row in rows:
            if companies.get(row.promotion_id) != branch_companies.get(row.branch_id):
                raise ValidationError(
                    'La sucursal no pertenece a la empresa de la promoción.'
                )


class PromotionItem(models.Model):
    """
    One component of a combo: an article and how many of it are required.

    The set of these IS the qualifying condition. A basket qualifies once for
    every complete set it contains.
    """

    promotion = models.ForeignKey(
        Promotion, on_delete=models.CASCADE, related_name='items',
    )
    product = models.ForeignKey(
        Product, on_delete=models.PROTECT, related_name='promotion_items',
    )
    quantity = models.PositiveSmallIntegerField(default=1)

    class Meta:
        ordering = ['pk']
        constraints = [
            models.UniqueConstraint(
                fields=['promotion', 'product'], name='unique_promotion_item',
            ),
            models.CheckConstraint(
                condition=models.Q(quantity__gte=1),
                name='promotion_item_quantity_positive',
            ),
        ]

    def __str__(self):
        return f'{self.quantity}× {self.product.name}'

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.promotion_id and self.product_id:
            if self.product.company_id != self.promotion.company_id:
                raise ValidationError(
                    {'product': 'El producto no pertenece a la empresa de la promoción.'}
                )

    def save(self, *args, **kwargs):
        self.clean()
        return super().save(*args, **kwargs)

    @staticmethod
    def assert_all_match_promotion(rows):
        """Set-level counterpart of `clean()`, for `bulk_create()` paths."""
        from django.core.exceptions import ValidationError

        rows = list(rows)
        if not rows:
            return
        promotion_companies = dict(
            Promotion.objects.filter(pk__in={r.promotion_id for r in rows})
            .values_list('pk', 'company_id')
        )
        product_companies = dict(
            Product.objects.filter(pk__in={r.product_id for r in rows})
            .values_list('pk', 'company_id')
        )
        for row in rows:
            if promotion_companies.get(row.promotion_id) != product_companies.get(row.product_id):
                raise ValidationError(
                    'El producto no pertenece a la empresa de la promoción.'
                )


class AppliedPromotion(models.Model):
    """
    What a promotion actually did to one sale, frozen.

    A sale is NEVER re-priced from a live `Promotion`. The combo that was
    running in March gets edited in April, renamed in May and switched off in
    June; March's receipt must keep saying what March's customer was charged and
    why. So the name, the type, the count and both amounts are snapshots, and
    the FK to `Promotion` is PROTECT — a promotion that has been applied is part
    of the record and cannot be deleted out from under it.
    """

    company = models.ForeignKey(
        Company, on_delete=models.PROTECT, related_name='applied_promotions',
    )
    order = models.ForeignKey(
        Order, on_delete=models.PROTECT, related_name='applied_promotions',
    )
    promotion = models.ForeignKey(
        Promotion, on_delete=models.PROTECT, related_name='applications',
    )
    promotion_name_snapshot = models.CharField(max_length=150)
    promotion_type_snapshot = models.CharField(max_length=24)

    applications = models.PositiveSmallIntegerField(default=1)
    # What the components would have cost separately, and what came off.
    regular_amount = models.DecimalField(max_digits=12, decimal_places=2)
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2)
    # The components and unit prices at the moment of sale, so the line can be
    # explained years later without joining anything that may have changed.
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['pk']
        indexes = [
            models.Index(fields=['company', 'promotion', 'created_at']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['order', 'promotion'], name='unique_applied_promotion_per_order',
            ),
            models.CheckConstraint(
                condition=models.Q(discount_amount__gte=Decimal('0.00')),
                name='applied_promotion_discount_not_negative',
            ),
        ]

    def __str__(self):
        return f'{self.promotion_name_snapshot} ×{self.applications} (#{self.order_id})'

    def clean(self):
        """
        INVARIANT: `company == order.company == promotion.company`.

        `company` is denormalised here so the reporting index can slice by
        tenant without joining `Order`. Denormalisation is what makes this check
        necessary: the column can now disagree with the row it was copied from,
        and a wrong value would put one tenant's discount in another tenant's
        promotion report.
        """
        from django.core.exceptions import ValidationError

        if self.order_id and self.company_id:
            if self.order.company_id != self.company_id:
                raise ValidationError(
                    {'order': 'El pedido no pertenece a esta empresa.'}
                )
        if self.promotion_id and self.company_id:
            if self.promotion.company_id != self.company_id:
                raise ValidationError(
                    {'promotion': 'La promoción no pertenece a esta empresa.'}
                )

    def save(self, *args, **kwargs):
        self.clean()
        return super().save(*args, **kwargs)

    @staticmethod
    def assert_all_match_company(rows):
        """
        Set-level check for the snapshot writer, which uses `bulk_create()`.

        The sale service derives every one of these values from the order it is
        building, so under correct code this can never fire. It is here for the
        case that stops being true — and it costs two queries once per sale that
        actually had a promotion.
        """
        from django.core.exceptions import ValidationError

        rows = list(rows)
        if not rows:
            return
        order_companies = dict(
            Order.objects.filter(pk__in={r.order_id for r in rows})
            .values_list('pk', 'company_id')
        )
        promotion_companies = dict(
            Promotion.objects.filter(pk__in={r.promotion_id for r in rows})
            .values_list('pk', 'company_id')
        )
        for row in rows:
            if order_companies.get(row.order_id) != row.company_id:
                raise ValidationError('El pedido no pertenece a esta empresa.')
            if promotion_companies.get(row.promotion_id) != row.company_id:
                raise ValidationError('La promoción no pertenece a esta empresa.')


# =============================================================================
# Bulk import — Commercial Phase C1.4
# =============================================================================
#
# WHY AN IMPORT IS THREE TABLES AND NOT AN UPLOAD HANDLER
# ------------------------------------------------------
# The naive shape is: receive the file, loop, write. It is wrong here for a
# reason specific to what is being written. A product import can be corrected by
# editing the product. A STOCK import cannot: it becomes Kardex movements, and
# the Kardex is an append-only record of physical fact. Undoing it means issuing
# compensating movements that are themselves permanent history — so the moment
# to catch a mistake is BEFORE the write, not after.
#
# Hence: parse and normalise into `BulkImportRow` (which touches no business
# table at all), show the operator exactly what will happen, and only then, on a
# second deliberate action, apply. The file itself is never stored — its SHA256
# and its normalised rows are, which is what an audit actually needs.


class BulkImportJob(models.Model):
    """
    One upload, from the moment it was parsed to the moment it was applied.

    Deliberately NOT a file store. Keeping the original workbook would mean
    holding the tenant's commercial data — prices, costs, every article they
    sell — indefinitely, to answer a question the normalised rows already
    answer. The SHA256 is enough to prove which file this was.
    """

    PRODUCTS = 'products'
    STOCK = 'stock'
    TYPE_CHOICES = [
        (PRODUCTS, 'Productos'),
        (STOCK, 'Inventario'),
    ]

    PREVIEWED = 'previewed'
    APPLIED = 'applied'
    FAILED = 'failed'
    STATUS_CHOICES = [
        (PREVIEWED, 'Previsualizado'),
        (APPLIED, 'Aplicado'),
        (FAILED, 'Fallido'),
    ]

    # Stock only: what the numbers in the file MEAN.
    MODE_INITIAL = 'initial'
    MODE_RECONCILE = 'reconcile_target'
    MODE_CHOICES = [
        (MODE_INITIAL, 'Carga inicial'),
        (MODE_RECONCILE, 'Ajuste a stock objetivo'),
    ]

    company = models.ForeignKey(
        Company, on_delete=models.PROTECT, related_name='import_jobs',
    )
    import_type = models.CharField(max_length=16, choices=TYPE_CHOICES, db_index=True)
    status = models.CharField(
        max_length=16, choices=STATUS_CHOICES, default=PREVIEWED, db_index=True,
    )
    stock_mode = models.CharField(
        max_length=20, choices=MODE_CHOICES, blank=True, default='',
    )

    original_filename = models.CharField(max_length=255, blank=True, default='')
    file_sha256 = models.CharField(max_length=64, blank=True, default='', db_index=True)

    # Frozen at preview so apply cannot be argued into using a different
    # mapping than the one the operator reviewed.
    mapping_snapshot = models.JSONField(default=dict, blank=True)
    options_snapshot = models.JSONField(default=dict, blank=True)

    rows_total = models.PositiveIntegerField(default=0)
    rows_create = models.PositiveIntegerField(default=0)
    rows_update = models.PositiveIntegerField(default=0)
    rows_no_change = models.PositiveIntegerField(default=0)
    rows_skip = models.PositiveIntegerField(default=0)
    rows_error = models.PositiveIntegerField(default=0)

    summary = models.JSONField(default=dict, blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='import_jobs_created',
    )
    applied_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='import_jobs_applied',
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    applied_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at', '-pk']
        indexes = [
            models.Index(fields=['company', 'import_type', '-created_at']),
            models.Index(fields=['company', 'status']),
        ]

    def __str__(self):
        return f'{self.get_import_type_display()} #{self.pk} ({self.status})'

    @property
    def is_applicable(self) -> bool:
        return self.status == self.PREVIEWED and self.rows_error == 0


class BulkImportRow(models.Model):
    """
    One line of a preview, kept so that apply reads from the DATABASE.

    WHY NOT JUST POST THE ROWS BACK FROM THE BROWSER
    -----------------------------------------------
    Because then the browser decides what gets written. Everything the operator
    approved is here, normalised and tenant-scoped, and apply re-reads it —
    the client sends an id and a confirmation, never data.

    Only MAPPED columns are stored. Copying every unrecognised column of a
    workbook into the database would persist whatever else the tenant keeps in
    their spreadsheet — costs, suppliers, notes — that nobody asked to store.
    """

    CREATE = 'create'
    UPDATE = 'update'
    NO_CHANGE = 'no_change'
    SKIP = 'skip'
    ERROR = 'error'
    ACTION_CHOICES = [
        (CREATE, 'Crear'),
        (UPDATE, 'Actualizar'),
        (NO_CHANGE, 'Sin cambios'),
        (SKIP, 'Omitir'),
        (ERROR, 'Error'),
    ]

    job = models.ForeignKey(
        BulkImportJob, on_delete=models.CASCADE, related_name='rows',
    )
    sheet_name = models.CharField(max_length=120, blank=True, default='')
    # The row number the OPERATOR sees in Excel. Off-by-one here means they fix
    # the wrong line.
    row_number = models.PositiveIntegerField()
    action = models.CharField(max_length=12, choices=ACTION_CHOICES, db_index=True)
    match_key = models.CharField(max_length=120, blank=True, default='')
    normalized_data = models.JSONField(default=dict, blank=True)
    errors = models.JSONField(default=list, blank=True)
    warnings = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['sheet_name', 'row_number', 'pk']
        indexes = [
            models.Index(fields=['job', 'action']),
        ]

    def __str__(self):
        return f'{self.sheet_name}!{self.row_number} → {self.action}'


class ImportMappingProfile(models.Model):
    """
    A remembered answer to "which column is which".

    Mapping eighteen columns by hand is a five-minute job the first time and an
    error every time after. A profile is keyed by a HEADER SIGNATURE — the shape
    of the file — so the same export from the same system is recognised
    tomorrow, next month, and by a different member of staff.

    Keyed on the headers and NOT on the file's SHA256, which changes with every
    new set of data and would recognise nothing twice. And keyed on the headers
    and NOT on the company's slug: a preset written for one tenant's export
    works for any tenant whose system produces the same columns, which is what
    makes this a SaaS feature rather than one customer's hardcoding.
    """

    company = models.ForeignKey(
        Company, on_delete=models.PROTECT, related_name='import_profiles',
    )
    name = models.CharField(max_length=120)
    import_type = models.CharField(
        max_length=16, choices=BulkImportJob.TYPE_CHOICES, db_index=True,
    )
    header_signature = models.CharField(max_length=64, db_index=True)
    mapping = models.JSONField(default=dict, blank=True)
    options = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='import_profiles_created',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        constraints = [
            models.UniqueConstraint(
                fields=['company', 'import_type', 'header_signature'],
                name='unique_import_profile_per_signature',
            ),
        ]
        indexes = [
            models.Index(fields=['company', 'import_type', 'is_active']),
        ]

    def __str__(self):
        return f'{self.name} ({self.import_type})'

# ===========================================================================
# BR-005A — TECHNICAL SERVICE CORE (M8)
# ===========================================================================
#
# A repair shop receives a device, opens an order for it, moves that order
# through a lifecycle and assigns somebody to work on it. That is the whole of
# M8. Diagnostics, quotes, approval, repair execution, parts, quality control
# and warranty are named in the roadmap and DELIBERATELY ABSENT: an empty table
# for a module nobody has written is an invitation to write code against a
# semantics that has not been decided.
#
# WHY THIS IS NOT `Order`
# -----------------------
# `Order` is a SALE. It has a cart, a total, a payment status, a Stripe session
# and a fulfilment state that ends in "delivered". A `RepairOrder` has none of
# those: nothing is bought, there is no price at intake, and its lifecycle is
# about a physical object somebody left on a counter. The two share the word
# "order" in English and nothing else. Making one a subclass — or hanging a
# ForeignKey between them — would mean every future change to a sale had to be
# reasoned about twice, once for a sale and once for a repair.
#
# WHAT A CLIENT NEVER DECIDES
# ---------------------------
# The order number, the initial status, the company, who received the device,
# and who may work on it. All five are set by the server, and the API has no
# field for any of them.


class Device(models.Model):
    """
    A physical object a customer left with the shop.

    ONE CUSTOMER, ONE COMPANY, MANY VISITS. A device is registered once and
    reused by every repair order that touches it, which is what makes "this
    laptop has been here three times" a question the data can answer.

    WHY THERE IS NO BRAND TABLE
    ---------------------------
    `brand` and `model` are normalised text, not foreign keys to a catalogue,
    and that is a decision rather than a shortcut. A brand catalogue has to be
    owned by somebody: platform-owned it goes stale the week a new phone ships
    and no tenant can fix it; tenant-owned it is three tables of CRUD that
    nobody asked for, standing between a receptionist and a device on the
    counter. The fields are indexed, so search works today, and nothing here
    prevents a later migration to a catalogue — the API shape would not change.

    `device_type` IS a closed list, because it is small, stable, and drives
    presentation rather than vocabulary. It is also deliberately generic: this
    platform is not an Apple reseller's software, and a shop that repairs
    consoles must be able to file a console.

    WHAT THIS MODEL REFUSES TO STORE
    --------------------------------
    No unlock PIN, no pattern, no password, no Apple ID, no iCloud credential.
    Repair shops do ask for them, and a field for one would make this table a
    credential store with no encryption-at-rest decision, no access policy, no
    retention rule and no deletion story. That policy does not exist yet, so
    neither does the field. A structural test fails if one appears.
    """

    TYPE_PHONE = 'phone'
    TYPE_TABLET = 'tablet'
    TYPE_LAPTOP = 'laptop'
    TYPE_DESKTOP = 'desktop'
    TYPE_CONSOLE = 'console'
    TYPE_WEARABLE = 'wearable'
    TYPE_OTHER = 'other'
    TYPE_CHOICES = [
        (TYPE_PHONE, 'Teléfono'),
        (TYPE_TABLET, 'Tablet'),
        (TYPE_LAPTOP, 'Laptop'),
        (TYPE_DESKTOP, 'Computadora de escritorio'),
        (TYPE_CONSOLE, 'Consola'),
        (TYPE_WEARABLE, 'Wearable'),
        (TYPE_OTHER, 'Otro'),
    ]

    company = models.ForeignKey(
        Company, on_delete=models.PROTECT, related_name='devices',
    )
    # PROTECT, not CASCADE. Deleting a customer must not silently take their
    # devices — and with them the repair history that references those devices.
    # Archiving a customer is `is_active=False`; it is not a delete.
    customer = models.ForeignKey(
        Customer, on_delete=models.PROTECT, related_name='devices',
    )

    device_type = models.CharField(
        max_length=16, choices=TYPE_CHOICES, default=TYPE_OTHER, db_index=True,
    )
    brand = models.CharField(max_length=80)
    model = models.CharField(max_length=120)

    # OPTIONAL, both of them, and no global unique constraint anywhere near
    # them. See `service_services.find_possible_duplicate_devices` for why: a
    # serial can be mistyped, absent, shared across tenants, or belong to a
    # device that has legitimately been registered before.
    serial_number = models.CharField(max_length=80, blank=True)
    imei = models.CharField(max_length=32, blank=True)

    color = models.CharField(max_length=40, blank=True)
    storage_capacity = models.CharField(max_length=40, blank=True)

    # INTERNAL. Physical quirks, prior interventions, what the cable looks like.
    # Never returned by the customer surface.
    notes = models.TextField(max_length=2000, blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='devices_created',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['brand', 'model', 'pk']
        indexes = [
            models.Index(fields=['company', 'customer']),
            models.Index(fields=['company', 'serial_number']),
            models.Index(fields=['company', 'imei']),
            models.Index(fields=['company', 'brand', 'model']),
            models.Index(fields=['company', 'created_at']),
        ]

    def __str__(self):
        label = f'{self.brand} {self.model}'.strip()
        return label or f'Equipo #{self.pk}'

    @property
    def display_name(self) -> str:
        parts = [self.brand, self.model]
        detail = ' '.join(p for p in parts if p).strip()
        return detail or self.get_device_type_display()

    def clean(self):
        from django.core.exceptions import ValidationError

        errors = {}
        if self.customer_id and self.company_id and self.customer.company_id != self.company_id:
            errors['customer'] = ['El cliente no pertenece a esta empresa.']
        if not (self.brand or '').strip():
            errors['brand'] = ['Indica la marca del equipo.']
        if not (self.model or '').strip():
            errors['model'] = ['Indica el modelo del equipo.']
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        # Normalised here rather than in a serializer, so the invariant holds
        # for the admin, for a data migration and for a shell session too.
        self.brand = (self.brand or '').strip()
        self.model = (self.model or '').strip()
        self.serial_number = (self.serial_number or '').strip().upper()
        self.imei = (self.imei or '').strip()
        self.clean()
        return super().save(*args, **kwargs)


class RepairStatusCode(models.TextChoices):
    """
    The lifecycle's STABLE codes. Platform-owned, never tenant-editable.

    A tenant renames what its staff read; it does not get to decide what
    "delivered" means, because the machine, the reports and every future
    integration are written against these strings. Separating the code from the
    label is the whole of DEC-014: presentation adapts, semantics does not.

    ONLY WHAT THE CODE CAN HONESTLY SUPPORT
    ---------------------------------------
    M8 shipped four. M9 added `APPROVED` and `REJECTED`, because it built the
    thing that gives them meaning: a quote a customer can decide on. That is the
    rule, and it has not changed — a state arrives with its module.

    `IN_REPAIR`, `WAITING_PARTS`, `REPAIRED`, `QUALITY_CONTROL`,
    `READY_FOR_PICKUP`, `DELIVERED` and `WARRANTY` are all real states of a real
    repair shop and none of them means anything yet: repair needs parts and
    execution, quality control needs a checklist, warranty needs a completed
    repair to warrant. Shipping the words without the modules would let an order
    be moved into a state no code can act on — a status that lies.

    `APPROVED` is now the deliberate edge: it is where M9 stops.

    NEITHER `APPROVED` NOR `REJECTED` IS REACHABLE BY MOVING AN ORDER. They are
    the recorded outcome of a customer deciding on a published quote, and
    `service_services` refuses to set them any other way. `WAITING_APPROVAL` is
    the same: from M9 it means "a frozen, published quote is waiting", and only
    publishing one can produce it.
    """

    RECEIVED = 'received', 'Recibido'
    DIAGNOSING = 'diagnosing', 'En diagnóstico'
    WAITING_APPROVAL = 'waiting_approval', 'Esperando aprobación'
    APPROVED = 'approved', 'Aprobado'
    REJECTED = 'rejected', 'Rechazado'
    CANCELLED = 'cancelled', 'Cancelado'


class RepairStatusSetting(models.Model):
    """
    How ONE company presents ONE lifecycle code.

    THE CODE IS NOT HERE. `code` on this row names a `RepairStatusCode` the
    platform defines; this row carries only what a tenant may safely change:
    what the state is CALLED, whether the customer sees the event at all, and
    the order the states are listed in.

    What a tenant may NOT change, and why the fields do not exist:

      · the meaning of a code — reports and integrations are written against it;
      · which transitions are legal — that is the machine, in `service_services`;
      · whether a state exists — deactivating `received` would leave new orders
        with nowhere to be born.

    Created for every company by provisioning, and re-ensured idempotently, so a
    company registered tomorrow is as usable as one registered before the
    migration ran.
    """

    company = models.ForeignKey(
        Company, on_delete=models.PROTECT, related_name='repair_status_settings',
    )
    code = models.CharField(max_length=32, choices=RepairStatusCode.choices, db_index=True)

    label = models.CharField(max_length=60)
    # Whether an event ARRIVING at this state is shown to the customer by
    # default. The event carries the final answer — see RepairStatusHistory —
    # but this is where a company sets its policy once.
    is_customer_visible = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['company', 'sort_order', 'code']
        constraints = [
            models.UniqueConstraint(
                fields=['company', 'code'], name='unique_repair_status_per_company',
            ),
        ]
        indexes = [models.Index(fields=['company', 'sort_order'])]

    def __str__(self):
        return f'{self.label} ({self.code})'


class RepairOrder(models.Model):
    """
    One visit of one device to the workshop.

    NOT AN `Order`. Nothing is sold here. There is no total at intake, no
    payment, no cart and no Stripe session — a price only exists once somebody
    has diagnosed the fault and quoted it, which is M9. The two models share a
    word in English and no fields, and there is deliberately no ForeignKey
    between them: a repair that later becomes a sale is a decision with business
    consequences, not a column somebody adds.

    THE SERVER OWNS ITS IDENTITY. `number`, `sequence_value`, `status`,
    `received_by` and `received_at` are written by
    `service_services.create_repair_order()` and appear in no request payload.
    A client that could choose its own order number could choose one that
    already exists; a client that could choose its own status could open an
    order that is already finished.

    `status` IS A PROJECTION. The evidence is `RepairStatusHistory`, which is
    append-only. This column exists so a list of two hundred orders does not
    need a subquery per row, and it is written only by
    `service_services.transition_repair_order()`, inside the same transaction as
    the history row it is derived from.
    """

    company = models.ForeignKey(
        Company, on_delete=models.PROTECT, related_name='repair_orders',
    )
    # NOT NULL. Provisioning gives every company a branch, because stock,
    # checkout and now intake all happen somewhere. A repair order with no
    # location cannot answer "where is my laptop", which is the first question
    # anybody asks.
    branch = models.ForeignKey(
        Branch, on_delete=models.PROTECT, related_name='repair_orders',
    )
    customer = models.ForeignKey(
        Customer, on_delete=models.PROTECT, related_name='repair_orders',
    )
    device = models.ForeignKey(
        Device, on_delete=models.PROTECT, related_name='repair_orders',
    )

    # The human-readable identifier, allocated from `InternalSequence` — the
    # same atomic, tenant-scoped machinery that numbers sales notes. A second
    # numbering system would be a second race condition to get right.
    number = models.CharField(max_length=32)
    sequence_value = models.PositiveBigIntegerField()

    status = models.CharField(
        max_length=32, choices=RepairStatusCode.choices,
        default=RepairStatusCode.RECEIVED, db_index=True,
    )

    # What the CUSTOMER said is wrong. Their words, not a diagnosis.
    reported_issue = models.TextField(max_length=2000)
    # What the counter SAW: scratches, a cracked back, missing screws. Recorded
    # at intake because it is the only defence either side has later.
    physical_condition = models.TextField(max_length=2000, blank=True)
    # Charger, case, SIM tray, box. Free text in M8 on purpose: a configurable
    # accessory catalogue is a subdomain, and inventing one here would freeze a
    # vocabulary nobody has agreed on.
    received_accessories = models.TextField(max_length=1000, blank=True)

    # INTERNAL. Never serialised to the customer surface.
    internal_notes = models.TextField(max_length=2000, blank=True)

    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='repair_orders_received',
    )
    received_at = models.DateTimeField(default=timezone.now)
    closed_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-received_at', '-pk']
        constraints = [
            models.UniqueConstraint(
                fields=['company', 'number'], name='unique_repair_order_number_per_company',
            ),
        ]
        indexes = [
            models.Index(fields=['company', 'status']),
            models.Index(fields=['company', 'branch', 'status']),
            models.Index(fields=['company', 'customer']),
            models.Index(fields=['company', 'received_at']),
            models.Index(fields=['device']),
        ]

    def __str__(self):
        return self.number or f'Orden de servicio #{self.pk}'

    def clean(self):
        from django.core.exceptions import ValidationError

        errors = {}
        if self.branch_id and self.company_id and self.branch.company_id != self.company_id:
            errors['branch'] = ['La sucursal no pertenece a esta empresa.']
        if self.customer_id and self.company_id and self.customer.company_id != self.company_id:
            errors['customer'] = ['El cliente no pertenece a esta empresa.']
        if self.device_id and self.company_id and self.device.company_id != self.company_id:
            errors['device'] = ['El equipo no pertenece a esta empresa.']
        # The device must be THIS customer's. Otherwise one client's repair
        # order would carry another client's property, and the customer surface
        # would hand it to the wrong person.
        if self.device_id and self.customer_id and self.device.customer_id != self.customer_id:
            errors['device'] = ['El equipo no pertenece a este cliente.']
        if not (self.reported_issue or '').strip():
            errors['reported_issue'] = ['Describe el problema reportado.']
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.clean()
        return super().save(*args, **kwargs)

    @property
    def current_assignment(self):
        """The technician working on this order, or None. Never a stored column."""
        return self.assignments.filter(unassigned_at__isnull=True).select_related(
            'technician',
        ).first()


class RepairStatusHistory(models.Model):
    """
    APPEND-ONLY evidence of everything that happened to an order.

    `RepairOrder.status` says where the order is; this says how it got there,
    and it is the half that cannot be rewritten. Rows are never updated and
    never deleted — `save()` refuses a second write and `delete()` refuses at
    all. A history that can be edited is not a history, it is a draft.

    THE FIRST ROW IS THE INTAKE. Creating an order writes an event with
    `from_status` empty and `to_status='received'`, so the timeline starts where
    the device did rather than at the first change.

    `comment` IS INTERNAL. Always, with no per-row exception: it is where a
    technician writes what they actually think, and the customer surface has no
    field for it. What the customer sees is the status and when it happened,
    governed by `is_customer_visible`.
    """

    ORIGIN_INTERNAL = 'internal'
    ORIGIN_CUSTOMER = 'customer'
    ORIGIN_SYSTEM = 'system'
    ORIGIN_INTEGRATION = 'integration'
    ORIGIN_CHOICES = [
        (ORIGIN_INTERNAL, 'Personal interno'),
        (ORIGIN_CUSTOMER, 'Cliente'),
        (ORIGIN_SYSTEM, 'Sistema'),
        (ORIGIN_INTEGRATION, 'Integración'),
    ]

    # PROTECT, and it is doing real work: an order with history cannot be
    # deleted at all. That is the intended answer. Losing the record of a device
    # somebody handed over is not a cleanup operation.
    repair_order = models.ForeignKey(
        RepairOrder, on_delete=models.PROTECT, related_name='status_history',
    )
    # Denormalised on purpose. A history query must be tenant-scoped without
    # joining through the order, so a bug in one query cannot become a
    # cross-tenant read.
    company = models.ForeignKey(
        Company, on_delete=models.PROTECT, related_name='repair_status_events',
    )

    # Empty for the intake event: nothing precedes the beginning.
    from_status = models.CharField(
        max_length=32, choices=RepairStatusCode.choices, blank=True,
    )
    to_status = models.CharField(max_length=32, choices=RepairStatusCode.choices)

    # SET_NULL: deleting a staff account must not delete what they recorded.
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='repair_status_events',
    )
    origin = models.CharField(
        max_length=16, choices=ORIGIN_CHOICES, default=ORIGIN_INTERNAL,
    )
    comment = models.TextField(max_length=1000, blank=True)

    # Decided when the event is written, from the company's setting for the
    # target status, and stored so that changing a company's policy tomorrow
    # does not retroactively reveal or hide what a customer was already shown.
    is_customer_visible = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at', 'pk']
        verbose_name_plural = 'repair status history'
        indexes = [
            models.Index(fields=['repair_order', 'created_at']),
            models.Index(fields=['company', 'created_at']),
        ]

    def __str__(self):
        return f'{self.repair_order_id}: {self.from_status or "—"} → {self.to_status}'

    def save(self, *args, **kwargs):
        from django.core.exceptions import ValidationError

        if self.pk is not None:
            raise ValidationError('El historial de servicio no se puede modificar.')
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        from django.core.exceptions import ValidationError

        raise ValidationError('El historial de servicio no se puede borrar.')


class TechnicianAssignment(models.Model):
    """
    Who was responsible for an order, and when.

    A COLUMN WOULD HAVE BEEN SMALLER AND WRONG. `repair_order.technician_id`
    answers "who has it now" and destroys the answer every time it changes. Who
    had it last week is the question that matters when something was done badly,
    and a column cannot answer it.

    The current assignment is derived: the row whose `unassigned_at` is null. A
    partial unique constraint guarantees there is at most one, so "reassign"
    means closing the open row and opening another — never editing the old one.

    WHO MAY BE ASSIGNED is not decided here. `service_services.assign_technician`
    checks that the candidate has an ACTIVE membership in the same company; the
    model only records the decision.
    """

    repair_order = models.ForeignKey(
        RepairOrder, on_delete=models.PROTECT, related_name='assignments',
    )
    company = models.ForeignKey(
        Company, on_delete=models.PROTECT, related_name='technician_assignments',
    )
    # PROTECT: a technician's history is the order's history too.
    technician = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name='repair_assignments',
    )
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='repair_assignments_made',
    )

    assigned_at = models.DateTimeField(default=timezone.now)
    unassigned_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-assigned_at', '-pk']
        constraints = [
            models.UniqueConstraint(
                fields=['repair_order'],
                condition=models.Q(unassigned_at__isnull=True),
                name='unique_active_assignment_per_repair_order',
            ),
        ]
        indexes = [
            models.Index(fields=['company', 'technician']),
            models.Index(fields=['repair_order', 'assigned_at']),
        ]

    def __str__(self):
        return f'{self.repair_order_id} → {self.technician_id}'

    @property
    def is_active(self) -> bool:
        return self.unassigned_at is None

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.repair_order_id and self.company_id:
            if self.repair_order.company_id != self.company_id:
                raise ValidationError({'repair_order': ['La orden no pertenece a esta empresa.']})


# ===========================================================================
# BR-005B — DIAGNOSIS, VERSIONED QUOTES AND CUSTOMER APPROVAL (M9)
# ===========================================================================
#
# M8 could receive a device and move it as far as "esperando aprobación", and
# that state meant only "somebody pressed a button". M9 gives it a meaning it
# can be held to: a concrete quote, frozen, published, still inside its validity
# window, waiting for a decision that belongs to the customer.
#
# THREE THINGS THIS MODULE IS NOT
# -------------------------------
#   · A QUOTE IS NOT AN ORDER. Nothing is sold, no cart exists, no Stripe
#     session is created and no `Order` row is written. A repair that is later
#     paid for is a decision with commercial consequences, not a foreign key.
#   · APPROVAL IS NOT PAYMENT. A customer saying "go ahead" is authorising work,
#     not settling an amount. Confusing the two would let a shop believe it had
#     been paid because somebody tapped a button.
#   · QUOTING A PART IS NOT RESERVING ONE. No `StockMovement`, no reservation,
#     no `PartUsage`. The line may point at a `Product` for reference; the stock
#     consequences belong to the phase that actually consumes parts.
#
# WHAT IS FROZEN, AND WHY
# -----------------------
# A quote that has been sent is evidence. The customer approved ONE REVISION at
# one set of prices, not "whatever the order costs today". So a sent quote and
# its lines stop being writable, and a change of mind produces a NEW revision
# rather than an edit — the same rule `RepairStatusHistory` follows.


class RepairDiagnostic(models.Model):
    """
    What a technician found, and what they recommend doing about it.

    VERSIONED, because a diagnosis that backed a quote somebody has already
    received cannot be quietly rewritten. While it is a DRAFT it is the
    technician's working note and freely editable; the moment a quote built on
    it is published it is FINALIZED, and a later change of understanding becomes
    revision 2 rather than an edit to revision 1.

    `root_cause` IS NOT REQUIRED. A technician often knows a laptop does not
    charge long before they know why, and forcing a field turns "I do not know
    yet" into a guess written down as fact. `recommended_action` is required,
    because that is the part a quote is built from.

    NO EVIDENCE FIELDS. Photographs are the natural companion to a diagnosis and
    there is still no storage provider decided (DEC-016) — no `FileField`
    anywhere in this backend, and a base64 column would be a storage decision
    taken by accident. A structural test fails if one appears.
    """

    STATUS_DRAFT = 'draft'
    STATUS_FINALIZED = 'finalized'
    STATUS_CHOICES = [
        (STATUS_DRAFT, 'Borrador'),
        (STATUS_FINALIZED, 'Finalizado'),
    ]

    company = models.ForeignKey(
        Company, on_delete=models.PROTECT, related_name='repair_diagnostics',
    )
    repair_order = models.ForeignKey(
        RepairOrder, on_delete=models.PROTECT, related_name='diagnostics',
    )
    # Allocated by the domain service under a lock, never sent by a client.
    revision = models.PositiveIntegerField()

    status = models.CharField(
        max_length=16, choices=STATUS_CHOICES, default=STATUS_DRAFT, db_index=True,
    )

    # What is wrong, in the technician's words. Required: a diagnosis with no
    # description is not a diagnosis.
    description = models.TextField(max_length=4000)
    # Why it is wrong. Optional on purpose — see the class docstring.
    root_cause = models.TextField(max_length=2000, blank=True)
    # What should be done. Required: this is what the quote is priced from.
    recommended_action = models.TextField(max_length=4000)

    # INTERNAL. Never serialised to the customer surface, in any state.
    internal_notes = models.TextField(max_length=2000, blank=True)

    # The authenticated actor, always. Not a `technician_id` from a payload:
    # "I am recording this" is the only claim M9 supports, and recording a
    # diagnosis in somebody else's name is a business decision nobody has made.
    diagnosed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='repair_diagnostics',
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    finalized_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-revision', '-pk']
        constraints = [
            models.UniqueConstraint(
                fields=['repair_order', 'revision'],
                name='unique_diagnostic_revision_per_order',
            ),
        ]
        indexes = [
            models.Index(fields=['company', 'status']),
            models.Index(fields=['repair_order', 'revision']),
        ]

    def __str__(self):
        return f'Diagnóstico #{self.revision} · orden {self.repair_order_id}'

    @property
    def is_finalized(self) -> bool:
        return self.status == self.STATUS_FINALIZED

    def clean(self):
        from django.core.exceptions import ValidationError

        errors = {}
        if self.repair_order_id and self.company_id:
            if self.repair_order.company_id != self.company_id:
                errors['repair_order'] = ['La orden no pertenece a esta empresa.']
        if not (self.description or '').strip():
            errors['description'] = ['Describe lo que encontraste.']
        if not (self.recommended_action or '').strip():
            errors['recommended_action'] = ['Indica la acción recomendada.']
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        from django.core.exceptions import ValidationError

        # FROZEN ONCE FINALIZED. `update_fields` is how the service stamps the
        # transition itself, so the guard reads the DATABASE's opinion of the
        # row rather than this instance's — an instance can be told anything.
        if self.pk is not None:
            stored_status = (
                RepairDiagnostic.objects.filter(pk=self.pk)
                .values_list('status', flat=True).first()
            )
            if stored_status == self.STATUS_FINALIZED:
                raise ValidationError(
                    'Un diagnóstico finalizado no se puede modificar. '
                    'Crea una revisión nueva.'
                )
        self.clean()
        return super().save(*args, **kwargs)


class RepairQuote(models.Model):
    """
    What the shop proposes to do, and what it will cost. One revision of it.

    THE CUSTOMER APPROVES A REVISION, NOT AN ORDER. `revision` exists because a
    first quote gets rejected and a second one gets approved, and both have to
    survive: "you agreed to this" is only answerable if the thing they agreed to
    still exists, unedited, next to the one they refused.

    MONEY IS FROZEN HERE. `unit_price` on a line is copied at composition time
    and never re-read from `Product`, so a price change tomorrow cannot rewrite
    what somebody was quoted yesterday. The optional `Product` link is a
    reference for a later phase, not the authority for a historical price.

    TAX IS ZERO, AND THAT IS RECORDED RATHER THAN COMPUTED. This platform models
    no tax at all — no rate, no regime, no configuration, nothing on
    `CompanySettings` and no field on `SalesNote`. Inventing an 18% IGV because
    the pilot is Peruvian would be writing one country's tax law into a SaaS
    schema. The column exists so a quote already sent keeps whatever it carried
    when tax arrives; nothing computes it, and no client can set it.

    NO PAYMENT FIELDS. Approval authorises work. It settles nothing.
    """

    STATUS_DRAFT = 'draft'
    STATUS_SENT = 'sent'
    STATUS_APPROVED = 'approved'
    STATUS_REJECTED = 'rejected'
    STATUS_CANCELLED = 'cancelled'
    STATUS_CHOICES = [
        (STATUS_DRAFT, 'Borrador'),
        (STATUS_SENT, 'Enviada'),
        (STATUS_APPROVED, 'Aprobada'),
        (STATUS_REJECTED, 'Rechazada'),
        (STATUS_CANCELLED, 'Anulada'),
    ]

    #: Once a quote leaves DRAFT it is evidence. Nothing below may be edited.
    EDITABLE_STATUSES = frozenset({STATUS_DRAFT})

    company = models.ForeignKey(
        Company, on_delete=models.PROTECT, related_name='repair_quotes',
    )
    repair_order = models.ForeignKey(
        RepairOrder, on_delete=models.PROTECT, related_name='quotes',
    )
    # PROTECT: the diagnosis a quote was built from cannot disappear from
    # underneath it. That pairing is most of what makes a quote defensible.
    diagnostic = models.ForeignKey(
        RepairDiagnostic, on_delete=models.PROTECT, related_name='quotes',
        null=True, blank=True,
    )
    revision = models.PositiveIntegerField()

    status = models.CharField(
        max_length=16, choices=STATUS_CHOICES, default=STATUS_DRAFT, db_index=True,
    )

    # Frozen from `CompanySettings` when the quote is created. Never from a
    # client: a currency chosen by the caller is a price in a unit nobody agreed.
    currency = models.CharField(max_length=3)

    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    discount_amount = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal('0.00'),
    )
    tax_amount = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal('0.00'),
    )
    total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))

    # After this instant the quote may still be READ — hiding it would make a
    # customer think it never existed — but it can no longer be approved.
    valid_until = models.DateTimeField(null=True, blank=True)

    # Written for the customer, and the only free text they see.
    customer_notes = models.TextField(max_length=2000, blank=True)
    # INTERNAL. Never leaves the internal surface.
    internal_notes = models.TextField(max_length=2000, blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='repair_quotes_created',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    rejected_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-revision', '-pk']
        constraints = [
            models.UniqueConstraint(
                fields=['repair_order', 'revision'],
                name='unique_quote_revision_per_order',
            ),
            models.CheckConstraint(
                condition=models.Q(discount_amount__gte=Decimal('0.00')),
                name='repair_quote_discount_non_negative',
            ),
            models.CheckConstraint(
                condition=models.Q(total__gte=Decimal('0.00')),
                name='repair_quote_total_non_negative',
            ),
        ]
        indexes = [
            models.Index(fields=['company', 'status']),
            models.Index(fields=['repair_order', 'revision']),
            models.Index(fields=['company', 'sent_at']),
        ]

    def __str__(self):
        return f'Cotización #{self.revision} · orden {self.repair_order_id}'

    @property
    def is_editable(self) -> bool:
        return self.status in self.EDITABLE_STATUSES

    @property
    def is_expired(self) -> bool:
        """
        DERIVED, never stored, and never written during a read.

        There is no scheduler in this project, and adding one so a row could
        change its own status is a lot of infrastructure for a comparison. A GET
        that mutated the database to "keep the state fresh" would also make
        reading a quote a write — which is how a report ends up changing what it
        reports on.
        """
        if self.valid_until is None:
            return False
        return timezone.now() > self.valid_until

    @property
    def can_be_decided(self) -> bool:
        """Whether a customer may still act on it."""
        return self.status == self.STATUS_SENT and not self.is_expired

    def clean(self):
        from django.core.exceptions import ValidationError

        errors = {}
        if self.repair_order_id and self.company_id:
            if self.repair_order.company_id != self.company_id:
                errors['repair_order'] = ['La orden no pertenece a esta empresa.']
        if self.diagnostic_id and self.repair_order_id:
            if self.diagnostic.repair_order_id != self.repair_order_id:
                errors['diagnostic'] = ['El diagnóstico es de otra orden.']
        if self.discount_amount is not None and self.subtotal is not None:
            if self.discount_amount > self.subtotal:
                errors['discount_amount'] = [
                    'El descuento no puede superar el subtotal.'
                ]
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        from django.core.exceptions import ValidationError

        if self.pk is not None:
            stored_status = (
                RepairQuote.objects.filter(pk=self.pk)
                .values_list('status', flat=True).first()
            )
            # A quote that has left DRAFT is evidence. The domain service moves
            # its status and stamps its timestamps through `update_fields`, and
            # those transitions are allowed; editing its CONTENT is not.
            if stored_status is not None and stored_status != self.STATUS_DRAFT:
                allowed = set(kwargs.get('update_fields') or [])
                content = {
                    'subtotal', 'discount_amount', 'tax_amount', 'total',
                    'currency', 'customer_notes', 'valid_until', 'diagnostic',
                    'diagnostic_id', 'revision', 'repair_order', 'repair_order_id',
                }
                if not allowed or (allowed & content):
                    raise ValidationError(
                        'Una cotización enviada no se puede modificar. '
                        'Crea una revisión nueva.'
                    )
        self.clean()
        return super().save(*args, **kwargs)


class RepairQuoteItem(models.Model):
    """
    One line of a quote: labour, a part, or a service.

    `line_total` IS COMPUTED BY THE SERVER, always, from quantity × unit_price.
    An internal user composing a quote legitimately chooses both of those — that
    is what writing a quote is — but the multiplication is not theirs to send.

    THE `Product` LINK IS A REFERENCE, NOT A PRICE. `description` and
    `unit_price` are copied at composition time and never re-read, so a
    catalogue change tomorrow cannot alter what a customer was quoted. Linking
    a product also does NOT touch stock: no movement, no reservation. Quoting a
    part is not taking one off a shelf.
    """

    TYPE_LABOR = 'labor'
    TYPE_PART = 'part'
    TYPE_SERVICE = 'service'
    TYPE_CHOICES = [
        (TYPE_LABOR, 'Mano de obra'),
        (TYPE_PART, 'Repuesto'),
        (TYPE_SERVICE, 'Servicio'),
    ]

    quote = models.ForeignKey(
        RepairQuote, on_delete=models.CASCADE, related_name='items',
    )
    item_type = models.CharField(max_length=16, choices=TYPE_CHOICES, default=TYPE_LABOR)

    description = models.CharField(max_length=300)
    quantity = models.DecimalField(max_digits=10, decimal_places=2)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    line_total = models.DecimalField(max_digits=12, decimal_places=2)

    # Optional, and PROTECT so a quoted product cannot be deleted out from under
    # a historical quote.
    product = models.ForeignKey(
        Product, on_delete=models.PROTECT, null=True, blank=True,
        related_name='repair_quote_items',
    )
    sort_order = models.PositiveSmallIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['sort_order', 'pk']
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gt=Decimal('0.00')),
                name='repair_quote_item_quantity_positive',
            ),
            models.CheckConstraint(
                condition=models.Q(unit_price__gte=Decimal('0.00')),
                name='repair_quote_item_price_non_negative',
            ),
        ]
        indexes = [models.Index(fields=['quote', 'sort_order'])]

    def __str__(self):
        return f'{self.description} × {self.quantity}'

    def clean(self):
        from django.core.exceptions import ValidationError

        errors = {}
        if not (self.description or '').strip():
            errors['description'] = ['Describe la línea.']
        if self.product_id and self.quote_id:
            if self.product.company_id != self.quote.company_id:
                errors['product'] = ['El producto no pertenece a esta empresa.']
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        from django.core.exceptions import ValidationError

        # A line belongs to its quote's state. Once the quote has been sent its
        # lines are evidence, and evidence does not get edited.
        if self.quote_id:
            quote_status = (
                RepairQuote.objects.filter(pk=self.quote_id)
                .values_list('status', flat=True).first()
            )
            if quote_status is not None and quote_status != RepairQuote.STATUS_DRAFT:
                raise ValidationError(
                    'No se puede modificar la línea de una cotización enviada.'
                )
        self.clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        from django.core.exceptions import ValidationError

        quote_status = (
            RepairQuote.objects.filter(pk=self.quote_id)
            .values_list('status', flat=True).first()
        )
        if quote_status is not None and quote_status != RepairQuote.STATUS_DRAFT:
            raise ValidationError(
                'No se puede borrar la línea de una cotización enviada.'
            )
        return super().delete(*args, **kwargs)


class RepairQuoteDecision(models.Model):
    """
    The customer's answer. One per quote, ever.

    A ONE-TO-ONE, and the database is what enforces it. Two taps on a slow
    connection, two devices, or a retry after a timeout must not be able to
    produce two answers to one question — and a uniqueness rule implemented in
    Python is a rule that a race can walk straight through.

    `channel` IS THE SERVER'S. A decision made through the authenticated
    customer surface is `customer_account`, full stop. A future endpoint may let
    a receptionist record "the customer approved by phone", and that will be a
    different endpoint with different authority — not a string in a body that
    anyone can set to whatever makes the record look better.

    `quoted_total` AND `currency` ARE SNAPSHOTS. The quote is already frozen, so
    they are belt and braces; but a decision that could not state what was
    agreed, on its own, would be a poor piece of evidence.
    """

    DECISION_APPROVE = 'approve'
    DECISION_REJECT = 'reject'
    DECISION_CHOICES = [
        (DECISION_APPROVE, 'Aprobada'),
        (DECISION_REJECT, 'Rechazada'),
    ]

    CHANNEL_CUSTOMER_ACCOUNT = 'customer_account'
    CHANNEL_CHOICES = [
        (CHANNEL_CUSTOMER_ACCOUNT, 'Cuenta del cliente'),
    ]

    company = models.ForeignKey(
        Company, on_delete=models.PROTECT, related_name='repair_quote_decisions',
    )
    repair_order = models.ForeignKey(
        RepairOrder, on_delete=models.PROTECT, related_name='quote_decisions',
    )
    quote = models.OneToOneField(
        RepairQuote, on_delete=models.PROTECT, related_name='decision',
    )
    customer = models.ForeignKey(
        Customer, on_delete=models.PROTECT, related_name='repair_quote_decisions',
    )
    # The login that acted. SET_NULL so deleting an account does not delete the
    # record of a decision the business acted on.
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='repair_quote_decisions',
    )

    decision = models.CharField(max_length=16, choices=DECISION_CHOICES)
    channel = models.CharField(
        max_length=32, choices=CHANNEL_CHOICES, default=CHANNEL_CUSTOMER_ACCOUNT,
    )

    # Customer → company. Optional, never echoed to a public timeline: free text
    # from a customer is not something a future visibility policy should be able
    # to publish by accident.
    reason = models.TextField(max_length=1000, blank=True)

    quoted_total = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3)

    # Resolved through `client_ip.get_client_ip()` — the platform's single
    # authority on who the caller is (P0-B). Never `X-Forwarded-For` read by
    # hand: the leftmost entry of that header is the one an attacker writes.
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    decided_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-decided_at', '-pk']
        indexes = [
            models.Index(fields=['company', 'decided_at']),
            models.Index(fields=['repair_order']),
        ]

    def __str__(self):
        return f'{self.get_decision_display()} · cotización {self.quote_id}'

    def save(self, *args, **kwargs):
        from django.core.exceptions import ValidationError

        if self.pk is not None:
            raise ValidationError('Una decisión del cliente no se puede modificar.')
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        from django.core.exceptions import ValidationError

        raise ValidationError('Una decisión del cliente no se puede borrar.')
