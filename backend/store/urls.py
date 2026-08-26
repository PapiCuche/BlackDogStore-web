from rest_framework.routers import DefaultRouter
from .views import (
    CategoryViewSet,
    ProductViewSet,
    OrderViewSet,
    CartViewSet,
    CreateCheckoutSessionView,
    StripeWebhookView,
    PaymentStatusView,
    ReviewViewSet,
    CouponValidateView,
)
from .auth_views import (
    RegisterView, UserDetailView, LoginView, RefreshView, LogoutView, CsrfView,
    VerifyEmailView, ResendVerificationView,
    PasswordResetRequestView, PasswordResetConfirmView,
    ChangePasswordView,
)
from .admin_views import (
    AdminUserListView, AdminUserRoleView, AdminAuditLogListView,
    AdminProductListView, AdminProductDetailView, AdminProductInventoryAdjustView,
    AdminCategoryListView,
    AdminOrderListView, AdminOrderDetailView, AdminOrderFulfillmentView,
    AdminOrderReceiptPdfView, AdminOrderResendEmailView,
)
from .inventory_views import (
    AdminBestSellingView, AdminHighStockView, AdminInventorySummaryView,
    AdminLowStockView, AdminOrderSalesNotePdfView, AdminOrderSalesNoteView,
    AdminProductStockCardView, AdminStaleStockView, AdminStockMovementListView,
)
from django.urls import path, include

router = DefaultRouter()
router.register('categories', CategoryViewSet, basename='category')
router.register('products', ProductViewSet, basename='product')
router.register('orders', OrderViewSet, basename='order')
router.register('cart', CartViewSet, basename='cart')
router.register('reviews', ReviewViewSet, basename='review')

urlpatterns = [
    path('', include(router.urls)),
    path('auth/register/', RegisterView.as_view(), name='auth-register'),
    path('auth/login/', LoginView.as_view(), name='token_obtain_pair'),
    path('auth/refresh/', RefreshView.as_view(), name='token_refresh'),
    path('auth/logout/', LogoutView.as_view(), name='auth-logout'),
    path('auth/csrf/', CsrfView.as_view(), name='auth-csrf'),
    path('auth/me/', UserDetailView.as_view(), name='auth-me'),
    path('auth/verify-email/', VerifyEmailView.as_view(), name='auth-verify-email'),
    path('auth/resend-verification/', ResendVerificationView.as_view(), name='auth-resend-verification'),
    path('auth/password-reset/request/', PasswordResetRequestView.as_view(), name='auth-password-reset-request'),
    path('auth/password-reset/confirm/', PasswordResetConfirmView.as_view(), name='auth-password-reset-confirm'),
    path('auth/change-password/', ChangePasswordView.as_view(), name='auth-change-password'),
    path('payments/create-checkout-session/', CreateCheckoutSessionView.as_view(), name='create-checkout-session'),
    path('payments/webhook/', StripeWebhookView.as_view(), name='stripe-webhook'),
    path('payments/status/', PaymentStatusView.as_view(), name='payment-status'),
    path('coupons/validate/', CouponValidateView.as_view(), name='coupon-validate'),
    path('admin/users/', AdminUserListView.as_view(), name='admin-users'),
    path('admin/users/<int:pk>/role/', AdminUserRoleView.as_view(), name='admin-user-role'),
    path('admin/audit-logs/', AdminAuditLogListView.as_view(), name='admin-audit-logs'),
    path('admin/products/', AdminProductListView.as_view(), name='admin-products'),
    path('admin/products/<int:pk>/', AdminProductDetailView.as_view(), name='admin-product-detail'),
    path('admin/products/<int:pk>/inventory-adjust/', AdminProductInventoryAdjustView.as_view(), name='admin-product-inventory-adjust'),
    path('admin/categories/', AdminCategoryListView.as_view(), name='admin-categories'),
    path('admin/orders/', AdminOrderListView.as_view(), name='admin-orders'),
    path('admin/orders/<int:pk>/', AdminOrderDetailView.as_view(), name='admin-order-detail'),
    path('admin/orders/<int:pk>/fulfillment-status/', AdminOrderFulfillmentView.as_view(), name='admin-order-fulfillment'),
    path('admin/orders/<int:pk>/receipt-pdf/', AdminOrderReceiptPdfView.as_view(), name='admin-order-receipt-pdf'),
    path('admin/orders/<int:pk>/resend-confirmation-email/', AdminOrderResendEmailView.as_view(), name='admin-order-resend-email'),

    # --- Phase 6.0: inventory (Kardex) + internal sales notes ---
    path('admin/inventory/summary/', AdminInventorySummaryView.as_view(), name='admin-inventory-summary'),
    path('admin/inventory/movements/', AdminStockMovementListView.as_view(), name='admin-inventory-movements'),
    path('admin/inventory/low-stock/', AdminLowStockView.as_view(), name='admin-inventory-low-stock'),
    path('admin/inventory/high-stock/', AdminHighStockView.as_view(), name='admin-inventory-high-stock'),
    path('admin/inventory/best-selling/', AdminBestSellingView.as_view(), name='admin-inventory-best-selling'),
    path('admin/inventory/no-movement/', AdminStaleStockView.as_view(), name='admin-inventory-no-movement'),
    path('admin/products/<int:pk>/stock-card/', AdminProductStockCardView.as_view(), name='admin-product-stock-card'),
    path('admin/orders/<int:pk>/sales-note/', AdminOrderSalesNoteView.as_view(), name='admin-order-sales-note'),
    path('admin/orders/<int:pk>/sales-note/pdf/', AdminOrderSalesNotePdfView.as_view(), name='admin-order-sales-note-pdf'),
]
