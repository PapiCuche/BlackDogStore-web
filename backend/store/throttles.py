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
