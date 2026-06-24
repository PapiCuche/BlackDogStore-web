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
