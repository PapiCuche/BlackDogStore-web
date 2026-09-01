import re
from decimal import Decimal

from django.utils.text import slugify
from rest_framework import serializers

from .models import (
    Category, Product, Order, OrderItem, CartItem, Review, Coupon,
    SalesNote, StockMovement,
    Branch, Company, Membership,
    CompanyArea, CompanyRole, MembershipRoleAssignment,
    BranchStock, InventoryCount, InventoryCountItem,
    Customer, normalize_customer_phone, normalize_document_number,
    StockTransfer, StockTransferItem,
    CompanySettings, InternalSequence,
)


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ['id', 'name', 'slug']


class ReviewSerializer(serializers.ModelSerializer):
    """
    Reviews, read and written — Phase 0.3 / P0-D.

    THE ID IS A SELECTOR, NOT AN AUTHORITY
    --------------------------------------
    `product` is a writable relation on a ModelSerializer, and DRF resolves
    those against the model's FULL default queryset. Reading was already scoped
    by `storefront_products(request)`; writing was not, so a signed-in customer
    browsing shop A could put shop B's product id in the body and land a review
    on B's catalogue — visible to B's customers, counted in B's rating, written
    by somebody who had never seen the shop.

    The field's queryset is therefore narrowed to the products of the storefront
    the SERVER resolved. The client still sends an id, so the API contract is
    unchanged; what changed is the set that id is allowed to name.

    IT FAILS CLOSED WITHOUT A REQUEST
    ---------------------------------
    With no request in context there is no storefront, and the queryset becomes
    empty rather than global. A future caller who forgets the context gets a
    serializer that can write nothing, instead of one that can write anywhere —
    the failure is loud and local, not silent and cross-tenant.

    `author_name` IS DERIVED, NOT SUPPLIED
    --------------------------------------
    It used to be free text on an authenticated endpoint, which let anyone
    publish under the shop's own support name, or as another customer. The
    column stays,
    because reviews older than the login requirement carry a name and no user,
    and deleting that would destroy the only attribution they have. Going
    forward the name comes from the account (see `ReviewViewSet.perform_create`).
    """

    comment = serializers.CharField(max_length=2000, required=False, allow_blank=True)

    class Meta:
        model = Review
        fields = ['id', 'product', 'author_name', 'rating', 'comment', 'created_at']
        # `author_name` is read-only rather than absent so that existing
        # responses keep the field and old clients keep rendering it.
        read_only_fields = ['created_at', 'author_name']

    # One message for a product of another tenant AND for one that does not
    # exist. Two different answers would let a caller map another shop's
    # catalogue by watching which ids are refused differently.
    default_error_messages = {
        'does_not_exist': 'Producto no disponible en esta tienda.',
        'incorrect_type': 'Producto no disponible en esta tienda.',
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from .tenancy import storefront_products

        product = self.fields.get('product')
        if product is None or not hasattr(product, 'queryset'):
            return
        request = self.context.get('request')
        product.queryset = (
            storefront_products(request) if request is not None
            else Product.objects.none()
        )
        product.error_messages.update({
            'does_not_exist': self.default_error_messages['does_not_exist'],
            'incorrect_type': self.default_error_messages['incorrect_type'],
        })

    def validate_rating(self, value):
        if not 1 <= value <= 5:
            raise serializers.ValidationError('El rating debe ser entre 1 y 5.')
        return value


class ProductSerializer(serializers.ModelSerializer):
    category = CategorySerializer(read_only=True)
    average_rating = serializers.SerializerMethodField()
    review_count = serializers.SerializerMethodField()
    inventory = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            'id', 'name', 'slug', 'description', 'price',
            'inventory', 'category', 'image_url', 'average_rating', 'review_count',
        ]

    def get_inventory(self, obj):
        """
        SELLABLE units on this storefront — the fulfillment branch's stock.

        The field name is unchanged because every storefront page, the cart and
        the product detail already read `inventory`; renaming it would break the
        public API for a rename. What it MEANS changed in Phase 2D: it is what
        checkout can actually deliver, not the company-wide total.

        Three sources, most specific first:
          1. the `available_stock` annotation from tenancy.storefront_products();
          2. a `storefront_branch` in the serializer context, for the cart and
             order paths that hold a few objects rather than a page of them;
          3. `Product.inventory`, the compatibility aggregate — reached only
             from contexts with no storefront at all (admin nesting, tests).
        """
        annotated = getattr(obj, 'available_stock', None)
        if annotated is not None:
            return annotated

        branch = self.context.get('storefront_branch')
        if branch is not None:
            from .models import BranchStock
            row = BranchStock.objects.filter(branch=branch, product=obj).first()
            return row.quantity if row else 0

        return obj.inventory

    def get_average_rating(self, obj):
        reviews = obj.reviews.all()
        if not reviews.exists():
            return None
        return round(sum(r.rating for r in reviews) / reviews.count(), 1)

    def get_review_count(self, obj):
        return obj.reviews.count()


class CouponSerializer(serializers.ModelSerializer):
    class Meta:
        model = Coupon
        fields = ['code', 'discount_percent']


class OrderItemSerializer(serializers.ModelSerializer):
    product = ProductSerializer(read_only=True)

    class Meta:
        model = OrderItem
        fields = ['id', 'product', 'quantity', 'price']


class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, read_only=True)

    class Meta:
        model = Order
        fields = [
            'id',
            'user',
            'customer_name',
            'customer_email',
            'total',
            'discount_amount',
            'coupon_code',
            'status',
            'paid',
            'paid_at',
            'stripe_session_id',
            'created_at',
            'items',
        ]
        read_only_fields = fields


class CartItemSerializer(serializers.ModelSerializer):
    product = ProductSerializer(read_only=True)

    class Meta:
        model = CartItem
        fields = ['id', 'session_key', 'product', 'quantity', 'added_at']
        read_only_fields = ['session_key', 'added_at']


# ---------------------------------------------------------------------------
# Phase 4.0: Checkout input serializer (commercial fields)
# ---------------------------------------------------------------------------

_PERU_PHONE_RE = re.compile(r'^\+?51?\s*9\d{2}\s*\d{3}\s*\d{3}$|^9\d{8}$')

# Fields the frontend must never be allowed to set (injected fields)
_FORBIDDEN_CHECKOUT_FIELDS = frozenset([
    'total', 'discount_amount', 'paid', 'paid_at', 'status', 'fulfillment_status',
    'stripe_session_id', 'stripe_payment_intent_id', 'payment_error', 'cart_session_key',
    'coupon_code',  # accepted separately, validated server-side
])


class CheckoutInputSerializer(serializers.Serializer):
    """Validates and sanitizes all commercial fields submitted at checkout.

    Economic fields (total, discount_amount, paid, etc.) are never accepted here
    — those are calculated and set exclusively by the backend.
    """
    customer_name = serializers.CharField(max_length=255, min_length=2, trim_whitespace=True)
    customer_email = serializers.EmailField(max_length=254)
    customer_phone = serializers.CharField(max_length=30, trim_whitespace=True)

    document_type = serializers.ChoiceField(choices=Order.DocumentType.choices)
    document_number = serializers.CharField(max_length=20, trim_whitespace=True)

    delivery_method = serializers.ChoiceField(choices=Order.DeliveryMethod.choices)
    address_line = serializers.CharField(max_length=300, required=False, allow_blank=True, trim_whitespace=True)
    city = serializers.CharField(max_length=100, required=False, allow_blank=True, trim_whitespace=True)
    district = serializers.CharField(max_length=100, required=False, allow_blank=True, trim_whitespace=True)
    reference = serializers.CharField(max_length=250, required=False, allow_blank=True, trim_whitespace=True)
    notes = serializers.CharField(max_length=500, required=False, allow_blank=True, trim_whitespace=True)

    receipt_type = serializers.ChoiceField(choices=Order.ReceiptType.choices)

    accepted_terms = serializers.BooleanField()
    accepted_warranty_policy = serializers.BooleanField()

    # session_key handled separately — not persisted on Order
    session_key = serializers.CharField(max_length=200, trim_whitespace=True)
    coupon_code = serializers.CharField(max_length=50, required=False, allow_blank=True, trim_whitespace=True)

    def validate_customer_phone(self, value):
        digits = re.sub(r'\s', '', value)
        if not _PERU_PHONE_RE.match(digits):
            raise serializers.ValidationError(
                # Neutral example digits. This message is shown at checkout to
                # EVERY tenant's customers; the previous one used the pilot
                # company's real phone number as the illustration.
                'Teléfono inválido. Ejemplos aceptados: 987654321, +51987654321, '
                '+51 987 654 321.'
            )
        return value

    def validate_document_number(self, value):
        return value  # cross-field validation done in validate()

    def validate_accepted_terms(self, value):
        if not value:
            raise serializers.ValidationError('Debes aceptar los términos y condiciones para continuar.')
        return value

    def validate_accepted_warranty_policy(self, value):
        if not value:
            raise serializers.ValidationError('Debes aceptar la política de garantía para continuar.')
        return value

    def validate(self, attrs):
        doc_type = attrs.get('document_type', '')
        doc_number = attrs.get('document_number', '')
        receipt = attrs.get('receipt_type', '')
        delivery = attrs.get('delivery_method', '')
        address_line = attrs.get('address_line', '').strip()
        city = attrs.get('city', '').strip()
        district = attrs.get('district', '').strip()

        # document_number format per type
        if doc_type == Order.DocumentType.DNI:
            if not re.fullmatch(r'\d{8}', doc_number):
                raise serializers.ValidationError({'document_number': 'El DNI debe tener exactamente 8 dígitos.'})
        elif doc_type == Order.DocumentType.RUC:
            if not re.fullmatch(r'\d{11}', doc_number):
                raise serializers.ValidationError({'document_number': 'El RUC debe tener exactamente 11 dígitos.'})
        elif doc_type == Order.DocumentType.CE:
            if not re.fullmatch(r'[a-zA-Z0-9]{6,12}', doc_number):
                raise serializers.ValidationError(
                    {'document_number': 'El Carnet de Extranjería debe tener entre 6 y 12 caracteres alfanuméricos.'}
                )

        # receipt_type + document_type compatibility
        if receipt == Order.ReceiptType.FACTURA and doc_type != Order.DocumentType.RUC:
            raise serializers.ValidationError(
                {'receipt_type': 'La factura requiere un número de RUC. Selecciona RUC como tipo de documento.'}
            )

        # delivery address requirements
        if delivery == Order.DeliveryMethod.DELIVERY_AREQUIPA:
            if not address_line:
                raise serializers.ValidationError(
                    {'address_line': 'La dirección es requerida para delivery en Arequipa.'}
                )
            if not district:
                raise serializers.ValidationError(
                    {'district': 'El distrito es requerido para delivery en Arequipa.'}
                )
        elif delivery == Order.DeliveryMethod.NATIONAL_SHIPPING:
            if not address_line:
                raise serializers.ValidationError(
                    {'address_line': 'La dirección es requerida para envío nacional.'}
                )
            if not city:
                raise serializers.ValidationError(
                    {'city': 'La ciudad es requerida para envío nacional.'}
                )
            if not district:
                raise serializers.ValidationError(
                    {'district': 'El distrito / departamento es requerido para envío nacional.'}
                )

        return attrs


# ---------------------------------------------------------------------------
# Admin serializers — products
# ---------------------------------------------------------------------------

class AdminProductSerializer(serializers.ModelSerializer):
    """Read serializer: used for list and detail responses from admin endpoints."""
    category_id = serializers.IntegerField(source='category.id', read_only=True, default=None, allow_null=True)
    category_name = serializers.CharField(source='category.name', read_only=True, default='', allow_null=True)

    class Meta:
        model = Product
        fields = [
            'id', 'name', 'slug', 'description', 'price', 'inventory',
            'image_url', 'category_id', 'category_name', 'is_active',
            'created_at', 'updated_at',
        ]


class AdminProductWriteSerializer(serializers.ModelSerializer):
    """
    Write serializer for product create/update.

    Phase 2B: `company` is deliberately ABSENT from `fields`. It cannot be mass
    assigned, and it is never read from the payload — the view passes the
    resolved company to save(). That is what prevents a product being moved
    between tenants by editing an id.

    The category queryset is narrowed to the same company, so a category id
    belonging to another tenant is rejected as an invalid choice rather than
    silently accepted.
    """

    slug = serializers.SlugField(required=False, allow_blank=True, max_length=50)
    image_url = serializers.URLField(required=False, allow_blank=True, max_length=500, default='')
    description = serializers.CharField(required=False, allow_blank=True, default='')
    category = serializers.PrimaryKeyRelatedField(
        queryset=Category.objects.all(), required=False, allow_null=True, default=None
    )
    is_active = serializers.BooleanField(required=False, default=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        company = self.context.get('company')
        if company is not None:
            self.fields['category'].queryset = Category.objects.filter(company=company)

    @property
    def _company(self):
        """The tenant this write belongs to: context first, then the instance."""
        return self.context.get('company') or getattr(self.instance, 'company', None)

    class Meta:
        model = Product
        fields = ['name', 'slug', 'description', 'price', 'inventory', 'image_url', 'category', 'is_active']
        extra_kwargs = {
            'slug': {'validators': []},
        }

    def validate_price(self, value):
        if value <= Decimal('0'):
            raise serializers.ValidationError('El precio debe ser mayor que 0.')
        return value

    def validate_inventory(self, value):
        if value < 0:
            raise serializers.ValidationError('El inventario no puede ser negativo.')
        return value

    def validate_slug(self, value):
        # Uniqueness is per company now: two tenants may both sell "iphone-15".
        if not value:
            return value
        qs = Product.objects.filter(slug=value, company=self._company)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError(
                'Ya existe un producto con este slug en esta empresa.'
            )
        return value

    def validate(self, attrs):
        company = self._company

        # Defence in depth: the queryset above already rejects a foreign category,
        # but the invariant is stated here too so no code path depends on the
        # field construction alone.
        category = attrs.get('category') or getattr(self.instance, 'category', None)
        if category is not None and company is not None and category.company_id != company.pk:
            raise serializers.ValidationError(
                {'category': 'La categoría no pertenece a la empresa de este producto.'}
            )

        if not self.instance and not attrs.get('slug'):
            base = slugify(attrs.get('name', ''))[:45]
            if not base:
                raise serializers.ValidationError({'slug': 'No se pudo generar un slug desde el nombre.'})
            slug, counter = base, 1
            while Product.objects.filter(slug=slug, company=company).exists():
                slug = f'{base}-{counter}'
                counter += 1
            attrs['slug'] = slug
        return attrs


class AdminInventoryAdjustSerializer(serializers.Serializer):
    """Validates inventory adjustment input: delta + reason."""
    delta = serializers.IntegerField()
    reason = serializers.CharField(min_length=3, max_length=500, trim_whitespace=True)

    def validate_delta(self, value):
        if value == 0:
            raise serializers.ValidationError('El delta no puede ser 0.')
        return value


# ---------------------------------------------------------------------------
# Admin serializers — orders
# ---------------------------------------------------------------------------

class AdminOrderItemSerializer(serializers.ModelSerializer):
    product_id = serializers.IntegerField(source='product.id', read_only=True)
    product_name = serializers.CharField(source='product.name', read_only=True)
    subtotal = serializers.SerializerMethodField()

    class Meta:
        model = OrderItem
        fields = ['id', 'product_id', 'product_name', 'quantity', 'price', 'subtotal']

    def get_subtotal(self, obj):
        return str(obj.price * obj.quantity)


class AdminOrderListSerializer(serializers.ModelSerializer):
    user_id = serializers.IntegerField(source='user.id', read_only=True, default=None, allow_null=True)
    username = serializers.CharField(source='user.username', read_only=True, default='', allow_null=True)
    item_count = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = [
            'id', 'customer_name', 'customer_email', 'user_id', 'username',
            'total', 'status', 'fulfillment_status', 'paid', 'created_at', 'paid_at',
            'item_count',
        ]

    def get_item_count(self, obj):
        return obj.items.count()


class AdminOrderDetailSerializer(serializers.ModelSerializer):
    user_id = serializers.IntegerField(source='user.id', read_only=True, default=None, allow_null=True)
    username = serializers.CharField(source='user.username', read_only=True, default='', allow_null=True)
    items = AdminOrderItemSerializer(many=True, read_only=True)

    class Meta:
        model = Order
        fields = [
            'id', 'customer_name', 'customer_email', 'user_id', 'username',
            'total', 'discount_amount', 'coupon_code',
            'status', 'fulfillment_status', 'paid', 'created_at', 'paid_at',
            # Phase 4.0 commercial fields
            'customer_phone', 'document_type', 'document_number',
            'delivery_method', 'address_line', 'city', 'district', 'reference',
            'notes', 'receipt_type', 'accepted_terms', 'accepted_warranty_policy',
            # Phase 4.1 email flags (admin read-only)
            'confirmation_email_sent_at', 'internal_notification_sent_at', 'email_send_error',
            'items',
        ]
        # NOTE: stripe_session_id, stripe_payment_intent_id, payment_error intentionally excluded


class AdminOrderFulfillmentSerializer(serializers.Serializer):
    fulfillment_status = serializers.ChoiceField(choices=Order.FulfillmentStatus.choices)
    note = serializers.CharField(required=False, allow_blank=True, max_length=500, default='')


class AdminCategoryWriteSerializer(serializers.ModelSerializer):
    """
    Write serializer for admin category create.

    `company` is absent from `fields` on purpose — it comes from the resolved
    request context, never from the payload.
    """

    slug = serializers.SlugField(required=False, allow_blank=True, max_length=50)

    class Meta:
        model = Category
        fields = ['name', 'slug']
        extra_kwargs = {'slug': {'validators': []}}

    @property
    def _company(self):
        return self.context.get('company') or getattr(self.instance, 'company', None)

    def validate_slug(self, value):
        # Per company: two tenants may each have a category "iphone".
        if not value:
            return value
        qs = Category.objects.filter(slug=value, company=self._company)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError(
                'Ya existe una categoría con este slug en esta empresa.'
            )
        return value

    def validate(self, attrs):
        company = self._company
        if not self.instance and not attrs.get('slug'):
            base = slugify(attrs.get('name', ''))[:45]
            if not base:
                raise serializers.ValidationError({'slug': 'No se pudo generar un slug.'})
            slug, counter = base, 1
            while Category.objects.filter(slug=slug, company=company).exists():
                slug = f'{base}-{counter}'
                counter += 1
            attrs['slug'] = slug
        return attrs


# ---------------------------------------------------------------------------
# Phase 6.0 — inventory (Kardex) and internal sales notes
# ---------------------------------------------------------------------------

class StockMovementSerializer(serializers.ModelSerializer):
    """Read serializer for one Kardex line."""

    product_name = serializers.CharField(source='product.name', read_only=True)
    product_slug = serializers.CharField(source='product.slug', read_only=True)
    branch_name = serializers.CharField(source='branch.name', read_only=True)
    movement_type_label = serializers.CharField(
        source='get_movement_type_display', read_only=True,
    )
    actor_username = serializers.SerializerMethodField()
    signed_quantity = serializers.IntegerField(read_only=True)
    is_entry = serializers.BooleanField(read_only=True)

    class Meta:
        model = StockMovement
        # Phase 2D adds branch/company and the document links. `stock_before`
        # and `stock_after` now mean THIS BRANCH's running balance — the field
        # names are unchanged so existing clients keep parsing, but a reader has
        # to know the meaning moved. See the model docstring.
        fields = [
            'id', 'company', 'branch', 'branch_name',
            'product', 'product_name', 'product_slug',
            'movement_type', 'movement_type_label', 'is_entry',
            'quantity', 'signed_quantity', 'stock_before', 'stock_after',
            'reason', 'reference_type', 'reference_id', 'order',
            'transfer', 'inventory_count',
            'actor', 'actor_username', 'created_at', 'metadata',
        ]
        read_only_fields = fields

    def get_actor_username(self, obj):
        return obj.actor.username if obj.actor_id else None


class StockMovementCreateSerializer(serializers.Serializer):
    """
    Write serializer for MANUAL movements only.

    `sale_exit` is intentionally absent from the choices: sale movements are
    produced exclusively by the payment pipeline.
    """

    product_id = serializers.IntegerField(min_value=1)
    # Phase 2D: WHERE. Optional in the payload — omitted means "the branch this
    # operator defaults to" — but never trusted: the view resolves whatever
    # arrives against the caller's own grants before a single unit moves.
    branch = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    movement_type = serializers.ChoiceField(choices=sorted(StockMovement.MANUAL_TYPES))
    quantity = serializers.IntegerField(min_value=1)
    reason = serializers.CharField(max_length=500, allow_blank=False, trim_whitespace=True)

    def validate_reason(self, value):
        if not value.strip():
            raise serializers.ValidationError('El motivo es obligatorio.')
        return value.strip()

    def validate_product_id(self, value):
        # Existence only. WHOSE product it is, is an authority question the view
        # answers against the resolved company — a serializer that scoped it
        # here would need request context it has no business holding.
        if not Product.objects.filter(pk=value).exists():
            raise serializers.ValidationError('Producto no encontrado.')
        return value


class InventoryProductSerializer(serializers.ModelSerializer):
    """Compact product row used by the stock reports."""

    category_name = serializers.CharField(source='category.name', read_only=True, default=None)

    class Meta:
        model = Product
        fields = ['id', 'name', 'slug', 'price', 'inventory', 'is_active', 'category_name']
        read_only_fields = fields


class SalesNoteSerializer(serializers.ModelSerializer):
    """
    Internal sales note. NOT a SUNAT electronic receipt.

    Deliberately exposes no Stripe identifier and no payment_error.
    """

    created_by_username = serializers.SerializerMethodField()
    order_total = serializers.DecimalField(
        source='order.total', max_digits=12, decimal_places=2, read_only=True,
    )
    customer_name = serializers.CharField(source='order.customer_name', read_only=True)
    status_label = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = SalesNote
        fields = [
            'id', 'order', 'number', 'status', 'status_label',
            'issued_at', 'created_at', 'created_by', 'created_by_username',
            'pdf_generated_at', 'order_total', 'customer_name',
        ]
        read_only_fields = fields

    def get_created_by_username(self, obj):
        return obj.created_by.username if obj.created_by_id else None


# ---------------------------------------------------------------------------
# SaaS Phase 1 — Company / Branch / Membership
# ---------------------------------------------------------------------------

class CompanySerializer(serializers.ModelSerializer):
    branch_count = serializers.IntegerField(read_only=True, default=0)
    membership_count = serializers.IntegerField(read_only=True, default=0)
    default_inventory_branch_name = serializers.CharField(
        source='default_inventory_branch.name', read_only=True, default=None,
    )

    class Meta:
        model = Company
        fields = [
            'id', 'name', 'legal_name', 'tax_id', 'slug', 'is_active',
            'default_inventory_branch', 'default_inventory_branch_name',
            'created_at', 'updated_at', 'branch_count', 'membership_count',
        ]
        read_only_fields = [
            'id', 'default_inventory_branch_name', 'created_at', 'updated_at',
        ]

    def validate_default_inventory_branch(self, value):
        """A company can only dispatch from one of its OWN branches."""
        if value is None:
            return value
        if self.instance is None or value.company_id != self.instance.pk:
            raise serializers.ValidationError(
                'La sucursal de despacho no pertenece a esta empresa.'
            )
        if not value.is_active:
            raise serializers.ValidationError(
                'La sucursal de despacho debe estar activa.'
            )
        return value

    def validate_slug(self, value):
        qs = Company.objects.filter(slug=value)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError('Ya existe una empresa con este slug.')
        return value


class BranchSerializer(serializers.ModelSerializer):
    company_name = serializers.CharField(source='company.name', read_only=True)

    class Meta:
        model = Branch
        fields = [
            'id', 'company', 'company_name', 'name', 'address', 'phone', 'email',
            'is_active', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'company_name', 'created_at', 'updated_at']


class MembershipSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source='user.username', read_only=True)
    company_name = serializers.CharField(source='company.name', read_only=True)
    branch_name = serializers.CharField(source='branch.name', read_only=True, default=None)
    role_label = serializers.CharField(source='get_role_display', read_only=True)
    branch_access = serializers.SerializerMethodField()

    class Meta:
        model = Membership
        fields = [
            'id', 'user', 'username', 'company', 'company_name',
            'role', 'role_label', 'branch', 'branch_name',
            'branch_access_mode', 'branch_access', 'is_active',
            'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'username', 'company_name', 'branch_name', 'role_label',
            'branch_access', 'created_at', 'updated_at',
        ]

    def get_branch_access(self, obj):
        """
        The branches explicitly granted to this membership.

        Returned in BOTH modes, not only SELECTED: grants are kept when somebody
        switches a person to ALL for a while, and hiding them would make the
        switch look destructive in the UI when it is not.
        """
        return [
            {
                'id': access.branch_id,
                'name': access.branch.name,
                'is_active': access.is_active,
            }
            for access in obj.branch_access.select_related('branch').filter(
                is_active=True,
            ).order_by('branch__name')
        ]


class MembershipWriteSerializer(serializers.Serializer):
    """
    Create/update payload.

    `company` is validated against the caller's own access in the view — reaching
    this serializer never grants access to a company the caller cannot already
    administer. The same is true of every branch id in `branch_access`.
    """
    user = serializers.IntegerField(min_value=1)
    company = serializers.IntegerField(min_value=1)
    role = serializers.ChoiceField(choices=[r[0] for r in Membership.ROLE_CHOICES])
    branch = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    branch_access_mode = serializers.ChoiceField(
        choices=[c[0] for c in Membership.ACCESS_MODE_CHOICES], required=False,
    )
    branch_access = serializers.ListField(
        child=serializers.IntegerField(min_value=1), required=False, allow_empty=True,
    )
    is_active = serializers.BooleanField(required=False, default=True)


class MembershipUpdateSerializer(serializers.Serializer):
    """Partial update. `company` and `user` are immutable — recreate instead."""
    role = serializers.ChoiceField(
        choices=[r[0] for r in Membership.ROLE_CHOICES], required=False,
    )
    branch = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    branch_access_mode = serializers.ChoiceField(
        choices=[c[0] for c in Membership.ACCESS_MODE_CHOICES], required=False,
    )
    # An explicit list REPLACES the grants. Sending [] revokes every branch,
    # which in SELECTED mode means the person can operate nowhere — a real,
    # intended state, and the reason "empty means all" was never on the table.
    branch_access = serializers.ListField(
        child=serializers.IntegerField(min_value=1), required=False, allow_empty=True,
    )
    is_active = serializers.BooleanField(required=False)


# ---------------------------------------------------------------------------
# Phase 2A.1 — configurable areas, roles and assignments
# ---------------------------------------------------------------------------

class CompanyAreaSerializer(serializers.ModelSerializer):
    """
    An organisational area. Areas NEVER grant permissions — see CompanyArea.

    `company` is write-once: moving an area between tenants would drag its
    assignments across a company boundary.
    """

    company_name = serializers.CharField(source='company.name', read_only=True)
    member_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = CompanyArea
        fields = [
            'id', 'company', 'company_name', 'name', 'slug', 'description',
            'is_active', 'sort_order', 'created_at', 'updated_at', 'member_count',
        ]
        read_only_fields = ['id', 'company_name', 'created_at', 'updated_at', 'member_count']

    def validate(self, attrs):
        if self.instance and 'company' in attrs and attrs['company'] != self.instance.company:
            raise serializers.ValidationError(
                {'company': 'No se puede mover un área a otra empresa.'}
            )
        return attrs


class CompanyRoleSerializer(serializers.ModelSerializer):
    """
    A role a company defines for its own staff.

    Capability codes are validated against the platform catalogue here, so no
    request path depends on the model's clean() alone. Whether the CALLER may
    delegate those particular capabilities is an authority question answered in
    the view (can_delegate_capabilities), not a payload-shape question.
    """

    company_name = serializers.CharField(source='company.name', read_only=True)
    assignment_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = CompanyRole
        fields = [
            'id', 'company', 'company_name', 'name', 'slug', 'description',
            'capabilities', 'is_active', 'created_at', 'updated_at', 'assignment_count',
        ]
        read_only_fields = [
            'id', 'company_name', 'created_at', 'updated_at', 'assignment_count',
        ]

    def validate_capabilities(self, value):
        from .capabilities import normalise_capabilities
        try:
            return normalise_capabilities(value)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc))

    def validate(self, attrs):
        if self.instance and 'company' in attrs and attrs['company'] != self.instance.company:
            raise serializers.ValidationError(
                {'company': 'No se puede mover un rol a otra empresa.'}
            )
        return attrs


class MembershipRoleAssignmentSerializer(serializers.ModelSerializer):
    """Read serializer for one role assignment."""

    username = serializers.CharField(source='membership.user.username', read_only=True)
    company = serializers.IntegerField(source='membership.company_id', read_only=True)
    company_name = serializers.CharField(source='membership.company.name', read_only=True)
    role_name = serializers.CharField(source='role.name', read_only=True)
    role_slug = serializers.CharField(source='role.slug', read_only=True)
    area_name = serializers.CharField(source='area.name', read_only=True, default=None)
    capabilities = serializers.SerializerMethodField()

    class Meta:
        model = MembershipRoleAssignment
        fields = [
            'id', 'membership', 'username', 'company', 'company_name',
            'role', 'role_name', 'role_slug', 'area', 'area_name',
            'capabilities', 'is_active', 'assigned_by', 'created_at', 'updated_at',
        ]
        read_only_fields = fields

    def get_capabilities(self, obj):
        return sorted(obj.role.capability_set)


class MembershipRoleAssignmentWriteSerializer(serializers.Serializer):
    """
    Create payload. Every id is UNTRUSTED and re-checked in the view against the
    caller's own tenant before anything is written.
    """

    membership = serializers.IntegerField(min_value=1)
    role = serializers.IntegerField(min_value=1)
    area = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    is_active = serializers.BooleanField(required=False, default=True)


class MembershipRoleAssignmentUpdateSerializer(serializers.Serializer):
    """
    Partial update. `membership` and `role` are immutable: changing either is a
    different grant, not an edit — remove the assignment and create a new one.
    """

    area = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    is_active = serializers.BooleanField(required=False)


# ---------------------------------------------------------------------------
# SaaS Phase 2D — multi-branch inventory
# ---------------------------------------------------------------------------
#
# MASS ASSIGNMENT IS THE THREAT MODEL HERE.
#
# `company` and `branch` never appear in a WRITE serializer's `fields`. Every
# one of them is resolved by the view from the caller's own memberships and
# branch grants, then passed to save() explicitly. Where a branch id must come
# from the client — a transfer needs an origin and a destination, and only the
# user knows which — it arrives as a plain IntegerField that the view validates
# against tenancy.visible_branches() before it is used for anything.

class BranchStockSerializer(serializers.ModelSerializer):
    """One product's stock in one branch, with its replenishment policy."""

    branch_name = serializers.CharField(source='branch.name', read_only=True)
    product_name = serializers.CharField(source='product.name', read_only=True)
    product_slug = serializers.CharField(source='product.slug', read_only=True)
    product_price = serializers.DecimalField(
        source='product.price', max_digits=10, decimal_places=2, read_only=True,
    )
    product_is_active = serializers.BooleanField(source='product.is_active', read_only=True)
    category_name = serializers.CharField(
        source='product.category.name', read_only=True, default=None,
    )
    needs_replenishment = serializers.BooleanField(read_only=True)
    suggested_quantity = serializers.IntegerField(read_only=True)

    class Meta:
        model = BranchStock
        fields = [
            'id', 'branch', 'branch_name', 'product', 'product_name', 'product_slug',
            'product_price', 'product_is_active', 'category_name',
            'quantity', 'minimum_stock', 'target_stock',
            'needs_replenishment', 'suggested_quantity', 'updated_at',
        ]
        read_only_fields = fields


class BranchStockPolicySerializer(serializers.Serializer):
    """
    Write serializer for the replenishment policy ONLY.

    `quantity` is deliberately absent and always will be. Stock changes through
    a movement that says who, why and when; letting an operator type a new
    number into a form would be a stock edit with no Kardex line, which is the
    exact hole this whole module exists to close. To change quantity, register a
    movement or approve a count.
    """

    minimum_stock = serializers.IntegerField(min_value=0, required=False)
    target_stock = serializers.IntegerField(min_value=0, required=False)

    def validate(self, attrs):
        instance = self.instance
        minimum = attrs.get(
            'minimum_stock',
            getattr(instance, 'minimum_stock', 0) if instance else 0,
        )
        target = attrs.get(
            'target_stock',
            getattr(instance, 'target_stock', 0) if instance else 0,
        )
        if target and target < minimum:
            raise serializers.ValidationError({
                'target_stock': 'El objetivo no puede ser menor que el mínimo.',
            })
        return attrs


class StockTransferItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source='product.name', read_only=True)
    product_slug = serializers.CharField(source='product.slug', read_only=True)

    class Meta:
        model = StockTransferItem
        fields = ['id', 'product', 'product_name', 'product_slug', 'quantity']
        read_only_fields = fields


class StockTransferSerializer(serializers.ModelSerializer):
    source_branch_name = serializers.CharField(source='source_branch.name', read_only=True)
    destination_branch_name = serializers.CharField(
        source='destination_branch.name', read_only=True,
    )
    status_label = serializers.CharField(source='get_status_display', read_only=True)
    items = StockTransferItemSerializer(many=True, read_only=True)
    total_units = serializers.SerializerMethodField()
    created_by_username = serializers.SerializerMethodField()

    class Meta:
        model = StockTransfer
        fields = [
            'id', 'company', 'source_branch', 'source_branch_name',
            'destination_branch', 'destination_branch_name',
            'status', 'status_label', 'reason', 'reference',
            'items', 'total_units',
            'created_by', 'created_by_username', 'created_at',
            'dispatched_at', 'received_at', 'cancelled_at', 'updated_at',
        ]
        read_only_fields = fields

    def get_total_units(self, obj):
        return sum(item.quantity for item in obj.items.all())

    def get_created_by_username(self, obj):
        return obj.created_by.username if obj.created_by_id else None


class StockTransferCreateSerializer(serializers.Serializer):
    """Branch ids are untrusted input; the view validates both against grants."""

    source_branch = serializers.IntegerField(min_value=1)
    destination_branch = serializers.IntegerField(min_value=1)
    reason = serializers.CharField(max_length=500, required=False, allow_blank=True, default='')
    reference = serializers.CharField(max_length=120, required=False, allow_blank=True, default='')

    def validate(self, attrs):
        if attrs['source_branch'] == attrs['destination_branch']:
            raise serializers.ValidationError({
                'destination_branch': 'El origen y el destino no pueden ser la misma sucursal.',
            })
        return attrs


class StockTransferItemWriteSerializer(serializers.Serializer):
    """A quantity of 0 removes the line — see inventory_services.set_transfer_item."""

    product = serializers.IntegerField(min_value=1)
    quantity = serializers.IntegerField(min_value=0)


class InventoryCountItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source='product.name', read_only=True)
    product_slug = serializers.CharField(source='product.slug', read_only=True)
    is_counted = serializers.BooleanField(read_only=True)

    class Meta:
        model = InventoryCountItem
        fields = [
            'id', 'product', 'product_name', 'product_slug',
            'theoretical_at_start', 'physical_quantity',
            'theoretical_at_approval', 'difference', 'is_counted',
            'note', 'updated_at',
        ]
        read_only_fields = fields


class InventoryCountSerializer(serializers.ModelSerializer):
    branch_name = serializers.CharField(source='branch.name', read_only=True)
    status_label = serializers.CharField(source='get_status_display', read_only=True)
    items = InventoryCountItemSerializer(many=True, read_only=True)
    counted_items = serializers.SerializerMethodField()
    created_by_username = serializers.SerializerMethodField()

    class Meta:
        model = InventoryCount
        fields = [
            'id', 'company', 'branch', 'branch_name', 'status', 'status_label',
            'reason', 'items', 'counted_items',
            'created_by', 'created_by_username', 'created_at',
            'approved_at', 'cancelled_at', 'updated_at',
        ]
        read_only_fields = fields

    def get_counted_items(self, obj):
        return sum(1 for item in obj.items.all() if item.physical_quantity is not None)

    def get_created_by_username(self, obj):
        return obj.created_by.username if obj.created_by_id else None


class InventoryCountCreateSerializer(serializers.Serializer):
    branch = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    reason = serializers.CharField(max_length=500, required=False, allow_blank=True, default='')


class InventoryCountItemWriteSerializer(serializers.Serializer):
    """
    `physical_quantity=null` means NOT COUNTED, which is not zero.

    An uncounted product is skipped at approval rather than written down to
    nothing — see inventory_services.approve_inventory_count.
    """

    product = serializers.IntegerField(min_value=1)
    physical_quantity = serializers.IntegerField(min_value=0, required=False, allow_null=True)
    note = serializers.CharField(max_length=250, required=False, allow_blank=True, default='')


# ---------------------------------------------------------------------------
# SaaS Phase 3 — company configuration and branding
# ---------------------------------------------------------------------------

class CompanySettingsSerializer(serializers.ModelSerializer):
    """
    Read/write serializer for a company's own configuration.

    `fields` IS THE ALLOWLIST, and that is the security property. `company` is
    absent, so settings cannot be repointed at another tenant by editing an id;
    `Company.slug` and `Company.is_active` are not on this model at all, so a
    company administrator physically cannot reach them from this screen — they
    remain platform-operator decisions.

    `currency` is read-only on purpose: the value is stored so the model is ready
    for multi-currency, but checkout charges through Stripe in one currency
    configured at the platform level. A dropdown that let a tenant pick USD while
    Stripe billed PEN would be a lie with a UI on it.
    """

    company_name = serializers.CharField(source='company.name', read_only=True)
    company_slug = serializers.CharField(source='company.slug', read_only=True)
    whatsapp_link = serializers.SerializerMethodField()

    class Meta:
        model = CompanySettings
        fields = [
            'company_name', 'company_slug',
            # contact
            'contact_email', 'phone', 'whatsapp_number', 'whatsapp_link',
            'website_url', 'facebook_url', 'instagram_url',
            'legal_address', 'city', 'country_code',
            # branding
            'logo_url', 'primary_color', 'accent_color', 'background_color',
            'surface_color', 'text_color', 'border_color',
            # business
            'timezone', 'currency',
            # policies
            'warranty_policy_text', 'warranty_policy_url', 'terms_url', 'privacy_url',
            # notifications
            'order_notification_email',
            'updated_at',
        ]
        read_only_fields = [
            'company_name', 'company_slug', 'whatsapp_link', 'currency', 'updated_at',
        ]

    def get_whatsapp_link(self, obj):
        from .company_settings import build_whatsapp_link
        return build_whatsapp_link(obj.whatsapp_number)

    def validate_warranty_policy_text(self, value):
        """
        Plain text only. This string is rendered in customer emails and PDFs.

        Accepting markup would turn one tenant's settings form into an
        HTML-injection vector aimed at other people's inboxes. The check is
        deliberately blunt — angle brackets have no legitimate use in a warranty
        sentence — rather than a sanitiser, which would be a parser to get wrong.
        """
        if value and ('<' in value or '>' in value):
            raise serializers.ValidationError(
                'La política de garantía debe ser texto plano, sin etiquetas HTML.'
            )
        return value

    def validate_country_code(self, value):
        if value and not (len(value) == 2 and value.isalpha()):
            raise serializers.ValidationError(
                'El código de país debe tener 2 letras (ISO 3166-1, ej. PE).'
            )
        return (value or '').upper()


class CompanyIdentityWriteSerializer(serializers.Serializer):
    """
    The three identity fields that live on `Company` but belong to the business.

    Separated from `CompanySerializer` because that one can also write `slug` and
    `is_active`, which are platform decisions. This one reaches exactly three
    columns and nothing else.
    """

    name = serializers.CharField(max_length=200, required=False)
    legal_name = serializers.CharField(
        max_length=255, required=False, allow_blank=True,
    )
    tax_id = serializers.CharField(max_length=20, required=False, allow_blank=True)

    def validate_name(self, value):
        if not (value or '').strip():
            raise serializers.ValidationError('El nombre de la empresa es obligatorio.')
        return value.strip()


class StorefrontConfigSerializer(serializers.Serializer):
    """
    The PUBLIC face of a company's configuration.

    Built by hand rather than from the model so the public surface is a
    deliberate list rather than "the model minus whatever somebody remembered to
    exclude". `order_notification_email` is the field this shape exists to keep
    out: it is an internal routing address, and publishing it would hand every
    visitor a tenant's operations inbox.
    """

    company = serializers.DictField()
    branding = serializers.DictField()
    contact = serializers.DictField()
    policies = serializers.DictField()


# ---------------------------------------------------------------------------
# SaaS Phase 2E — internal document sequences
# ---------------------------------------------------------------------------

class InternalSequenceSerializer(serializers.ModelSerializer):
    """
    Read serializer for one series.

    `preview` is what the NEXT number would look like. It is computed, not
    allocated: rendering it consumes nothing, which is the difference between a
    settings screen and a number burner.
    """

    branch_name = serializers.CharField(source='branch.name', read_only=True, default=None)
    document_type_label = serializers.CharField(
        source='get_document_type_display', read_only=True,
    )
    preview = serializers.CharField(read_only=True)
    has_issued = serializers.BooleanField(read_only=True)
    can_edit_next_value = serializers.SerializerMethodField()

    class Meta:
        model = InternalSequence
        fields = [
            'id', 'company', 'branch', 'branch_name',
            'document_type', 'document_type_label',
            'prefix', 'padding', 'next_value',
            'preview', 'has_issued', 'can_edit_next_value',
            'is_active', 'updated_at',
        ]
        read_only_fields = fields

    def get_can_edit_next_value(self, obj):
        from .sequences import can_edit_next_value
        return can_edit_next_value(obj)


class InternalSequenceWriteSerializer(serializers.Serializer):
    """
    Write payload for one series.

    `company` and `branch` are ABSENT. A series belongs to the company and branch
    it was created for; letting a PATCH move it would hand one tenant's counter
    to another, and would do it through the field that decides whose numbers
    those are.

    `next_value` is accepted but the VIEW decides whether it may be applied —
    see `sequences.can_edit_next_value()`. Validation of shape and validation of
    authority are different questions, and the second one needs the row.
    """

    prefix = serializers.CharField(
        max_length=12, required=False, allow_blank=True, trim_whitespace=False,
    )
    padding = serializers.IntegerField(
        required=False,
        min_value=InternalSequence.MIN_PADDING,
        max_value=InternalSequence.MAX_PADDING,
    )
    next_value = serializers.IntegerField(required=False, min_value=1)
    is_active = serializers.BooleanField(required=False)

    def validate_prefix(self, value):
        """
        Runs the model validator, so the API and the ORM reject the same strings.

        The message names the allowed characters rather than saying "invalid":
        somebody typing `NV/2026/` needs to know the slash is the problem.
        """
        from django.core.exceptions import ValidationError as DjangoValidationError

        from .models import validate_sequence_prefix

        try:
            validate_sequence_prefix(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages)
        return value


class SequenceScopeSerializer(serializers.Serializer):
    """Just the scope. Separate because changing it is a different decision."""

    scope = serializers.ChoiceField(
        choices=[c[0] for c in CompanySettings.SEQUENCE_SCOPE_CHOICES],
    )


# ---------------------------------------------------------------------------
# SaaS Phase 4 — customers
# ---------------------------------------------------------------------------

class CustomerSerializer(serializers.ModelSerializer):
    """
    A customer as the internal CRM shows it.

    `company` is read-only and absent from every write path. The tenant comes
    from the resolved context, never from the body — a client that could name its
    own company would be able to file a record inside somebody else's CRM.
    """

    display_name = serializers.CharField(read_only=True)
    has_account = serializers.BooleanField(read_only=True)
    document_type_label = serializers.SerializerMethodField()

    class Meta:
        model = Customer
        fields = [
            'id', 'display_name', 'has_account', 'customer_type',
            'first_name', 'last_name', 'business_name',
            'document_type', 'document_type_label', 'document_number',
            'phone', 'email',
            'address_line', 'district', 'city',
            'notes', 'is_active',
            'created_at', 'updated_at',
        ]
        read_only_fields = fields

    def get_document_type_label(self, obj):
        return obj.get_document_type_display() if obj.document_type else ''


class CustomerListSerializer(serializers.ModelSerializer):
    """
    The list row.

    Deliberately WITHOUT `notes`. Internal notes are written for one reader at a
    time and can be blunt; a list is skimmed over someone's shoulder at a
    counter. They are one click away in the detail, which is where the person
    reading them has chosen to look.
    """

    display_name = serializers.CharField(read_only=True)
    has_account = serializers.BooleanField(read_only=True)

    class Meta:
        model = Customer
        fields = [
            'id', 'display_name', 'has_account', 'customer_type',
            'document_type', 'document_number', 'phone', 'email',
            'is_active', 'created_at', 'updated_at',
        ]
        read_only_fields = fields


class CustomerWriteSerializer(serializers.ModelSerializer):
    """
    Create and update.

    `company`, `user`, `created_by` and the timestamps are not fields here at
    all. Omitting them is stronger than validating them away: there is no code
    path where a request body can influence who owns the record.
    """

    class Meta:
        model = Customer
        fields = [
            'customer_type',
            'first_name', 'last_name', 'business_name',
            'document_type', 'document_number',
            'phone', 'email',
            'address_line', 'district', 'city',
            'notes', 'is_active',
        ]

    def validate_notes(self, value):
        if value and len(value) > 2000:
            raise serializers.ValidationError('Máximo 2000 caracteres.')
        return value

    def validate_document_number(self, value):
        value = normalize_document_number(value)
        if value and not re.fullmatch(r'[A-Z0-9-]{4,20}', value):
            raise serializers.ValidationError(
                'Sólo letras, dígitos y guiones (4 a 20 caracteres).'
            )
        return value

    def validate_phone(self, value):
        value = normalize_customer_phone(value)
        if value and not re.fullmatch(r'\+?\d{6,20}', value):
            raise serializers.ValidationError(
                'Indica un número de teléfono válido.'
            )
        return value


class CustomerOrderSerializer(serializers.ModelSerializer):
    """
    One line of a customer's commercial history.

    Reads the ORDER's own snapshot, never the customer's current details: the
    point of showing history is to show what happened, and what happened
    included the address the parcel actually went to.

    Carries no Stripe identifier and no payment error, for the same reason the
    rest of the internal order surface does not.
    """

    class Meta:
        model = Order
        fields = [
            'id', 'created_at', 'paid_at', 'status', 'fulfillment_status',
            'total', 'discount_amount', 'paid',
            'customer_name', 'customer_phone', 'document_type', 'document_number',
            'delivery_method',
        ]
        read_only_fields = fields
