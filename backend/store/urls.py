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
from .auth_views import RegisterView, UserDetailView, LoginView, RefreshView, LogoutView, CsrfView
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
    path('payments/create-checkout-session/', CreateCheckoutSessionView.as_view(), name='create-checkout-session'),
    path('payments/webhook/', StripeWebhookView.as_view(), name='stripe-webhook'),
    path('payments/status/', PaymentStatusView.as_view(), name='payment-status'),
    path('coupons/validate/', CouponValidateView.as_view(), name='coupon-validate'),
]
