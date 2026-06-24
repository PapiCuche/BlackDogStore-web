import re
from decimal import Decimal

from django.utils.text import slugify
from rest_framework import serializers

from .models import Category, Product, Order, OrderItem, CartItem, Review, Coupon


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ['id', 'name', 'slug']


class ReviewSerializer(serializers.ModelSerializer):
    comment = serializers.CharField(max_length=2000, required=False, allow_blank=True)

    class Meta:
        model = Review
        fields = ['id', 'product', 'author_name', 'rating', 'comment', 'created_at']
        read_only_fields = ['created_at']

    def validate_rating(self, value):
        if not 1 <= value <= 5:
            raise serializers.ValidationError('El rating debe ser entre 1 y 5.')
        return value


class ProductSerializer(serializers.ModelSerializer):
    category = CategorySerializer(read_only=True)
    average_rating = serializers.SerializerMethodField()
    review_count = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            'id', 'name', 'slug', 'description', 'price',
            'inventory', 'category', 'image_url', 'average_rating', 'review_count',
        ]

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
                'Teléfono inválido. Ejemplos aceptados: 936449536, +51936449536, +51 936 449 536.'
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
    """Write serializer: validates and saves product create/update."""
    slug = serializers.SlugField(required=False, allow_blank=True, max_length=50)
    image_url = serializers.URLField(required=False, allow_blank=True, max_length=500, default='')
    description = serializers.CharField(required=False, allow_blank=True, default='')
    category = serializers.PrimaryKeyRelatedField(
        queryset=Category.objects.all(), required=False, allow_null=True, default=None
    )
    is_active = serializers.BooleanField(required=False, default=True)

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
        if not value:
            return value
        qs = Product.objects.filter(slug=value)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError('Ya existe un producto con este slug.')
        return value

    def validate(self, attrs):
        if not self.instance and not attrs.get('slug'):
            base = slugify(attrs.get('name', ''))[:45]
            if not base:
                raise serializers.ValidationError({'slug': 'No se pudo generar un slug desde el nombre.'})
            slug, counter = base, 1
            while Product.objects.filter(slug=slug).exists():
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
    """Write serializer for admin category create."""
    slug = serializers.SlugField(required=False, allow_blank=True, max_length=50)

    class Meta:
        model = Category
        fields = ['name', 'slug']
        extra_kwargs = {'slug': {'validators': []}}

    def validate_slug(self, value):
        if not value:
            return value
        qs = Category.objects.filter(slug=value)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError('Ya existe una categoría con este slug.')
        return value

    def validate(self, attrs):
        if not self.instance and not attrs.get('slug'):
            base = slugify(attrs.get('name', ''))[:45]
            if not base:
                raise serializers.ValidationError({'slug': 'No se pudo generar un slug.'})
            slug, counter = base, 1
            while Category.objects.filter(slug=slug).exists():
                slug = f'{base}-{counter}'
                counter += 1
            attrs['slug'] = slug
        return attrs
