from rest_framework.throttling import AnonRateThrottle, UserRateThrottle


class LoginThrottle(AnonRateThrottle):
    scope = 'login'


class RegisterThrottle(AnonRateThrottle):
    scope = 'register'


class CouponThrottle(AnonRateThrottle):
    scope = 'coupon'


class ReviewCreateThrottle(AnonRateThrottle):
    """Applied only to POST (create) in ReviewViewSet via get_throttles()."""
    scope = 'review_create'


class CheckoutThrottle(AnonRateThrottle):
    scope = 'checkout'


class CartThrottle(AnonRateThrottle):
    scope = 'cart'


class PaymentStatusThrottle(AnonRateThrottle):
    scope = 'payment_status'


class ResendVerificationThrottle(AnonRateThrottle):
    scope = 'resend_verification'


class PasswordResetRequestThrottle(AnonRateThrottle):
    scope = 'password_reset_request'


class PasswordResetConfirmThrottle(AnonRateThrottle):
    scope = 'password_reset_confirm'


class ChangePasswordThrottle(UserRateThrottle):
    scope = 'change_password'


class AdminUsersThrottle(UserRateThrottle):
    scope = 'admin_users'


class AdminRoleChangeThrottle(UserRateThrottle):
    scope = 'admin_role_change'


class AdminAuditLogsThrottle(UserRateThrottle):
    scope = 'admin_audit_logs'


class AdminProductsThrottle(UserRateThrottle):
    scope = 'admin_products'


class AdminProductWriteThrottle(UserRateThrottle):
    scope = 'admin_product_write'


class AdminInventoryAdjustThrottle(UserRateThrottle):
    scope = 'admin_inventory_adjust'


class AdminCategoriesThrottle(UserRateThrottle):
    scope = 'admin_categories'


class AdminOrdersThrottle(UserRateThrottle):
    scope = 'admin_orders'


class AdminOrderStatusChangeThrottle(UserRateThrottle):
    scope = 'admin_order_status_change'


class AdminOrderEmailResendThrottle(UserRateThrottle):
    """10/min per authenticated user — prevents email spam from admin panel."""
    scope = 'admin_order_email_resend'


# --- Phase 6.0 ---

class AdminInventoryReportsThrottle(UserRateThrottle):
    """Read-only inventory dashboards and Kardex."""
    scope = 'admin_inventory_reports'


class AdminStockMovementsThrottle(UserRateThrottle):
    """Creating manual stock entries/exits."""
    scope = 'admin_stock_movements'


class AdminSalesNotesThrottle(UserRateThrottle):
    """Issuing / downloading internal sales notes."""
    scope = 'admin_sales_notes'


class AdminCustomersThrottle(UserRateThrottle):
    """Reading and searching the CRM."""
    scope = 'admin_customers'


class AdminCustomerWriteThrottle(UserRateThrottle):
    """Creating, editing and archiving customers."""
    scope = 'admin_customer_write'


class AdminPosThrottle(UserRateThrottle):
    """POS lookups and searches — a scanner fires these in bursts."""
    scope = 'admin_pos'


class AdminPosSaleThrottle(UserRateThrottle):
    """Completing a counter sale."""
    scope = 'admin_pos_sale'


class AdminSalesAnalyticsThrottle(UserRateThrottle):
    """Commercial dashboard and replenishment report."""
    scope = 'admin_sales_analytics'
