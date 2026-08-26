import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator
from django.utils import timezone


class Category(models.Model):
    name = models.CharField(max_length=200)
    slug = models.SlugField(unique=True)

    def __str__(self):
        return self.name


class Product(models.Model):
    name = models.CharField(max_length=255)
    slug = models.SlugField(unique=True)
    description = models.TextField(blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    inventory = models.IntegerField(default=0)
    image_url = models.URLField(blank=True, default='')
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name


class Coupon(models.Model):
    code = models.CharField(max_length=50, unique=True)
    discount_percent = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(100)]
    )
    is_active = models.BooleanField(default=True)
    expires_at = models.DateTimeField(null=True, blank=True)

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

    class DocumentType(models.TextChoices):
        DNI = 'dni', 'DNI'
        RUC = 'ruc', 'RUC'
        CE = 'ce', 'Carnet de Extranjería'

    class DeliveryMethod(models.TextChoices):
        PICKUP_STORE = 'pickup_store', 'Recojo en tienda'
        DELIVERY_AREQUIPA = 'delivery_arequipa', 'Delivery Arequipa'
        NATIONAL_SHIPPING = 'national_shipping', 'Envío nacional'

    class ReceiptType(models.TextChoices):
        BOLETA = 'boleta', 'Boleta'
        FACTURA = 'factura', 'Factura'

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
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

    def __str__(self):
        owner = self.customer_name or self.user or "Anon"
        return f"Order #{self.id} [{self.status}] - {owner}"


class OrderItem(models.Model):
    order = models.ForeignKey(Order, related_name='items', on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField(default=1)
    price = models.DecimalField(max_digits=10, decimal_places=2)

    def __str__(self):
        return f"{self.quantity} x {self.product.name}"


class CartItem(models.Model):
    session_key = models.CharField(max_length=100, db_index=True)
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField(default=1)
    added_at = models.DateTimeField(auto_now_add=True)

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

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"AuditLog[{self.action}] by={self.actor_id} on {self.target_type}:{self.target_id}"

    @classmethod
    def log(cls, actor, action, target_type, target_id='', metadata=None, request=None):
        ip = None
        ua = ''
        if request:
            xff = request.META.get('HTTP_X_FORWARDED_FOR', '')
            ip = xff.split(',')[0].strip() if xff else request.META.get('REMOTE_ADDR')
            ua = request.META.get('HTTP_USER_AGENT', '')[:500]
        return cls.objects.create(
            actor=actor,
            action=action,
            target_type=target_type,
            target_id=str(target_id),
            metadata=metadata or {},
            ip_address=ip or None,
            user_agent=ua,
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
    Immutable Kardex line. Every change to Product.inventory must produce one.

    Rules enforced by store.inventory_services (never write this model directly):
      - quantity is always POSITIVE; movement_type decides the sign.
      - stock_before / stock_after are snapshots taken under select_for_update().
      - stock_after is never negative.
      - manual movements require an actor and a reason.
      - sale movements require an order and are idempotent per (order, product).
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

    MOVEMENT_TYPE_CHOICES = [
        (INITIAL_STOCK, 'Stock inicial'),
        (PURCHASE_ENTRY, 'Entrada por compra'),
        (MANUAL_ENTRY, 'Entrada manual'),
        (RETURN_ENTRY, 'Entrada por devolución'),
        (CORRECTION_POSITIVE, 'Corrección positiva'),
        (MANUAL_EXIT, 'Salida manual'),
        (SALE_EXIT, 'Salida por venta'),
        (CORRECTION_NEGATIVE, 'Corrección negativa'),
        (DAMAGED_EXIT, 'Salida por daño / merma'),
        (SERVICE_EXIT, 'Salida por servicio técnico'),
    ]

    ENTRY_TYPES = frozenset([
        INITIAL_STOCK, PURCHASE_ENTRY, MANUAL_ENTRY, RETURN_ENTRY, CORRECTION_POSITIVE,
    ])
    EXIT_TYPES = frozenset([
        MANUAL_EXIT, SALE_EXIT, CORRECTION_NEGATIVE, DAMAGED_EXIT, SERVICE_EXIT,
    ])
    # Types an operator may create through the admin API. sale_exit is excluded
    # on purpose: it is only ever produced by the payment pipeline.
    MANUAL_TYPES = frozenset([
        PURCHASE_ENTRY, MANUAL_ENTRY, RETURN_ENTRY, CORRECTION_POSITIVE,
        MANUAL_EXIT, CORRECTION_NEGATIVE, DAMAGED_EXIT,
    ])

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
    legal/tax validity. The number is an internal correlativo (NV-000001).
    """

    STATUS_ISSUED = 'issued'
    STATUS_VOID = 'void'
    STATUS_CHOICES = [
        (STATUS_ISSUED, 'Emitida'),
        (STATUS_VOID, 'Anulada'),
    ]

    NUMBER_PREFIX = 'NV-'
    NUMBER_PADDING = 6

    order = models.OneToOneField(
        Order, on_delete=models.PROTECT, related_name='sales_note',
    )
    number = models.CharField(max_length=30, unique=True)
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

    def __str__(self):
        return f"SalesNote({self.number}, order={self.order_id})"
