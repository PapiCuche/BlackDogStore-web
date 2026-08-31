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
    # --- Phase 2D: multi-branch inventory ---
    AdminBranchStockListView, AdminBranchStockPolicyView,
    AdminInventoryBranchListView, AdminInventoryDashboardView,
    AdminInventoryCountApproveView, AdminInventoryCountCancelView,
    AdminInventoryCountDetailView, AdminInventoryCountItemsView,
    AdminInventoryCountListView, AdminReplenishmentView,
    AdminStockTransferCancelView, AdminStockTransferDetailView,
    AdminStockTransferDispatchView, AdminStockTransferItemsView,
    AdminStockTransferListView, AdminStockTransferReceiveView,
)
from .customer_views import (
    AdminCustomerDetailView,
    AdminCustomerListView,
)
from .settings_views import (
    AdminCompanySettingsView, AdminSequenceDetailView, AdminSequenceListView,
    AdminSequenceScopeView, StorefrontConfigView,
)
from .tenant_views import (
    AdminBranchDetailView, AdminBranchListView, AdminCompanyDetailView,
    AdminCompanyFulfillmentBranchView,
    AdminCompanyListView, AdminMembershipDetailView, AdminMembershipListView,
    MyMembershipsView,
)
from .access_views import (
    AdminAreaDetailView, AdminAreaListView, AdminRoleAssignmentDetailView,
    AdminRoleAssignmentListView, AdminRoleDetailView, AdminRoleListView,
    CapabilityCatalogView, InternalDashboardView, MyCompanyAccessView,
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

    # --- Phase 2D: multi-branch inventory ---
    # The Phase 6.0 routes above keep their paths and their names; they gained a
    # `?branch=` parameter and lost nothing, so existing clients keep working.
    path('admin/inventory/branches/', AdminInventoryBranchListView.as_view(), name='admin-inventory-branches'),
    path('admin/inventory/dashboard/', AdminInventoryDashboardView.as_view(), name='admin-inventory-dashboard'),
    path('admin/inventory/stock/', AdminBranchStockListView.as_view(), name='admin-inventory-stock'),
    path('admin/inventory/stock/<int:pk>/policy/', AdminBranchStockPolicyView.as_view(), name='admin-inventory-stock-policy'),
    path('admin/inventory/replenishment/', AdminReplenishmentView.as_view(), name='admin-inventory-replenishment'),
    path('admin/inventory/transfers/', AdminStockTransferListView.as_view(), name='admin-inventory-transfers'),
    path('admin/inventory/transfers/<int:pk>/', AdminStockTransferDetailView.as_view(), name='admin-inventory-transfer-detail'),
    path('admin/inventory/transfers/<int:pk>/items/', AdminStockTransferItemsView.as_view(), name='admin-inventory-transfer-items'),
    path('admin/inventory/transfers/<int:pk>/dispatch/', AdminStockTransferDispatchView.as_view(), name='admin-inventory-transfer-dispatch'),
    path('admin/inventory/transfers/<int:pk>/receive/', AdminStockTransferReceiveView.as_view(), name='admin-inventory-transfer-receive'),
    path('admin/inventory/transfers/<int:pk>/cancel/', AdminStockTransferCancelView.as_view(), name='admin-inventory-transfer-cancel'),
    path('admin/inventory/counts/', AdminInventoryCountListView.as_view(), name='admin-inventory-counts'),
    path('admin/inventory/counts/<int:pk>/', AdminInventoryCountDetailView.as_view(), name='admin-inventory-count-detail'),
    path('admin/inventory/counts/<int:pk>/items/', AdminInventoryCountItemsView.as_view(), name='admin-inventory-count-items'),
    path('admin/inventory/counts/<int:pk>/approve/', AdminInventoryCountApproveView.as_view(), name='admin-inventory-count-approve'),
    path('admin/inventory/counts/<int:pk>/cancel/', AdminInventoryCountCancelView.as_view(), name='admin-inventory-count-cancel'),

    # --- SaaS Phase 1: multi-tenant foundation ---
    path('admin/companies/', AdminCompanyListView.as_view(), name='admin-companies'),
    path('admin/companies/<int:pk>/', AdminCompanyDetailView.as_view(), name='admin-company-detail'),
    path('admin/companies/<int:pk>/fulfillment-branch/', AdminCompanyFulfillmentBranchView.as_view(), name='admin-company-fulfillment-branch'),
    path('admin/branches/', AdminBranchListView.as_view(), name='admin-branches'),
    path('admin/branches/<int:pk>/', AdminBranchDetailView.as_view(), name='admin-branch-detail'),
    path('admin/memberships/', AdminMembershipListView.as_view(), name='admin-memberships'),
    path('admin/memberships/<int:pk>/', AdminMembershipDetailView.as_view(), name='admin-membership-detail'),
    path('me/memberships/', MyMembershipsView.as_view(), name='me-memberships'),

    # --- SaaS Phase 2A.1: configurable areas, roles and assignments ---
    path('admin/capabilities/', CapabilityCatalogView.as_view(), name='admin-capabilities'),
    path('admin/areas/', AdminAreaListView.as_view(), name='admin-areas'),
    path('admin/areas/<int:pk>/', AdminAreaDetailView.as_view(), name='admin-area-detail'),
    path('admin/roles/', AdminRoleListView.as_view(), name='admin-roles'),
    path('admin/roles/<int:pk>/', AdminRoleDetailView.as_view(), name='admin-role-detail'),
    path('admin/membership-role-assignments/', AdminRoleAssignmentListView.as_view(), name='admin-role-assignments'),
    path('admin/membership-role-assignments/<int:pk>/', AdminRoleAssignmentDetailView.as_view(), name='admin-role-assignment-detail'),
    path('me/company-access/', MyCompanyAccessView.as_view(), name='me-company-access'),
    path('me/internal-dashboard/', InternalDashboardView.as_view(), name='me-internal-dashboard'),

    # --- SaaS Phase 3: company configuration and branding ---
    path('storefront/config/', StorefrontConfigView.as_view(), name='storefront-config'),
    path('admin/company-settings/', AdminCompanySettingsView.as_view(), name='admin-company-settings'),

    # --- SaaS Phase 4: customers (internal CRM) ---
    # No public counterpart. A customer's document, phone, address and the notes
    # about them are internal control only.
    path('admin/customers/', AdminCustomerListView.as_view(), name='admin-customers'),
    path('admin/customers/<int:pk>/', AdminCustomerDetailView.as_view(), name='admin-customer-detail'),

    # --- SaaS Phase 2E: internal document sequences ---
    # `scope/` before `<int:pk>/` so the literal segment is not swallowed by
    # the numeric one.
    path('admin/sequences/', AdminSequenceListView.as_view(), name='admin-sequences'),
    path('admin/sequences/scope/', AdminSequenceScopeView.as_view(), name='admin-sequence-scope'),
    path('admin/sequences/<int:pk>/', AdminSequenceDetailView.as_view(), name='admin-sequence-detail'),
]
