from django.contrib import admin
from .models import Category, Product, Order, OrderItem, CartItem, Review, Coupon


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'slug')
    prepopulated_fields = {'slug': ('name',)}


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'price', 'inventory', 'category', 'image_url')
    prepopulated_fields = {'slug': ('name',)}
    list_filter = ('category',)
    search_fields = ('name', 'description')


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = ('price',)


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'customer_name', 'customer_email', 'total', 'discount_amount',
        'coupon_code', 'status', 'paid', 'paid_at', 'created_at',
    )
    list_filter = ('status', 'paid', 'created_at')
    search_fields = ('customer_name', 'customer_email', 'coupon_code', 'stripe_session_id')
    readonly_fields = ('stripe_session_id', 'stripe_payment_intent_id', 'paid_at', 'payment_error')
    inlines = [OrderItemInline]


@admin.register(CartItem)
class CartItemAdmin(admin.ModelAdmin):
    list_display = ('id', 'session_key', 'product', 'quantity', 'added_at')


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ('id', 'product', 'author_name', 'rating', 'created_at')
    list_filter = ('rating', 'product')
    search_fields = ('author_name', 'comment', 'product__name')


@admin.register(Coupon)
class CouponAdmin(admin.ModelAdmin):
    list_display = ('id', 'code', 'discount_percent', 'is_active', 'expires_at')
    list_filter = ('is_active',)
    search_fields = ('code',)
