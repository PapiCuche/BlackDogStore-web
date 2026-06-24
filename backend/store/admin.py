from django.contrib import admin
from .models import (
    AccountToken, AdminAuditLog, Category, Product,
    Order, OrderItem, CartItem, Review, Coupon, UserProfile,
)


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
        'coupon_code', 'status', 'fulfillment_status', 'delivery_method',
        'receipt_type', 'paid', 'paid_at', 'created_at',
    )
    list_filter = ('status', 'fulfillment_status', 'delivery_method', 'receipt_type', 'paid', 'created_at')
    search_fields = ('customer_name', 'customer_email', 'coupon_code', 'stripe_session_id',
                     'document_number', 'customer_phone')
    readonly_fields = ('stripe_session_id', 'stripe_payment_intent_id', 'paid_at', 'payment_error',
                       'accepted_terms', 'accepted_warranty_policy',
                       'confirmation_email_sent_at', 'internal_notification_sent_at', 'email_send_error')
    fieldsets = (
        ('Identificación', {
            'fields': ('id', 'user', 'status', 'fulfillment_status', 'paid', 'paid_at'),
        }),
        ('Cliente', {
            'fields': ('customer_name', 'customer_email', 'customer_phone',
                       'document_type', 'document_number'),
        }),
        ('Económico', {
            'fields': ('total', 'discount_amount', 'coupon_code'),
        }),
        ('Entrega', {
            'fields': ('delivery_method', 'address_line', 'city', 'district', 'reference'),
        }),
        ('Comprobante y notas', {
            'fields': ('receipt_type', 'notes', 'accepted_terms', 'accepted_warranty_policy'),
        }),
        ('Emails transaccionales (Fase 4.1)', {
            'fields': ('confirmation_email_sent_at', 'internal_notification_sent_at', 'email_send_error'),
        }),
        ('Técnico (Stripe)', {
            'classes': ('collapse',),
            'fields': ('stripe_session_id', 'stripe_payment_intent_id', 'payment_error',
                       'cart_session_key'),
        }),
    )
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


@admin.register(AccountToken)
class AccountTokenAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'purpose', 'created_at', 'expires_at', 'used_at')
    list_filter = ('purpose',)
    search_fields = ('user__username', 'user__email')
    readonly_fields = ('token_hash', 'created_at', 'expires_at', 'used_at', 'user', 'purpose')


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'role', 'created_at', 'updated_at')
    list_filter = ('role',)
    search_fields = ('user__username', 'user__email')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(AdminAuditLog)
class AdminAuditLogAdmin(admin.ModelAdmin):
    list_display = ('id', 'actor', 'action', 'target_type', 'target_id', 'ip_address', 'created_at')
    list_filter = ('action', 'target_type', 'created_at')
    search_fields = ('actor__username', 'target_id', 'action')
    readonly_fields = ('actor', 'action', 'target_type', 'target_id', 'metadata', 'ip_address', 'user_agent', 'created_at')
