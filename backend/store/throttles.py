from django.conf import settings
from rest_framework.exceptions import APIException
from rest_framework.throttling import (
    AnonRateThrottle,
    SimpleRateThrottle,
    UserRateThrottle,
)


class LoginThrottle(SimpleRateThrottle):
    """
    Rate-limit login per IP whether or not the request already carries a session.

    ERP-1 · AUTH-THROTTLE-01. This was an AnonRateThrottle, and DRF's
    AnonRateThrottle returns no cache key — i.e. does not throttle — once the
    request is authenticated. So a user signed into their own account could
    POST other accounts' passwords to the login endpoint with no limit at all;
    the brute-force defence only covered anonymous callers. Both the web
    LoginView and V1LoginView use this class, so keying purely by IP closes the
    hole on both channels. A login attempt is a login attempt regardless of who
    is currently signed in.
    """

    scope = 'login'

    def get_cache_key(self, request, view):
        return self.cache_format % {
            'scope': self.scope,
            'ident': self.get_ident(request),
        }


class RefreshThrottle(SimpleRateThrottle):
    """
    Rate-limit session renewal per IP — SEC-SET-04-A.

    Neither refresh endpoint had a limiter. Every valid renewal rotates the
    refresh token and writes an `OutstandingToken` row, so a caller holding one
    refresh token could write rows without a ceiling, and anyone could hammer
    the endpoint with no session at all.

    Keyed by IP like `LoginThrottle`, and for the same reason: both endpoints
    are open to anyone, and an AnonRateThrottle does not count requests that
    arrive authenticated — which for the web refresh is the normal case. The
    web and the native endpoint share the bucket: it is one operation reached
    through two doors.

    Only requests that CARRY a refresh credential are counted, valid or forged.
    The web client tries to renew whenever a request answers 401, including for
    a visitor who never signed in; that request has no refresh cookie, is
    refused before any work is done, and must not spend the budget of the
    people who do hold a session behind the same address.

    The rate is far above honest use (an access token lives 30 minutes, so a
    session renews about twice an hour) and leaves room for a shop whose whole
    staff shares one address.
    """

    scope = 'token_refresh'

    def get_cache_key(self, request, view):
        presented = request.COOKIES.get(settings.JWT_COOKIE_REFRESH_NAME)
        if not presented:
            try:
                body = request.data
            except APIException:
                # A body that cannot be parsed carries no credential either; the
                # view answers it with its own error.
                body = None
            presented = body.get('refresh') if hasattr(body, 'get') else None
        if not presented:
            return None
        return self.cache_format % {
            'scope': self.scope,
            'ident': self.get_ident(request),
        }


class RegisterThrottle(AnonRateThrottle):
    scope = 'register'


class CouponThrottle(AnonRateThrottle):
    scope = 'coupon'


class ReviewCreateThrottle(AnonRateThrottle):
    """Applied only to POST (create) in ReviewViewSet via get_throttles()."""
    scope = 'review_create'


class CheckoutThrottle(AnonRateThrottle):
    scope = 'checkout'


class CheckoutQuoteThrottle(AnonRateThrottle):
    """
    Cotizar el carrito NO puede gastar el presupuesto de pagar.

    La cotización nació compartiendo `CheckoutThrottle` con la creación de la
    sesión de pago, y eso convertía mirar el checkout en un motivo para no poder
    comprar: se reproduce con doce cotizaciones seguidas, tras las cuales
    `payments/create-checkout-session/` responde 429 sin llegar siquiera a
    ejecutarse. Y la pantalla vuelve a cotizar en CADA cambio del carrito o del
    cupón, así que alguien ajustando cantidades se cerraba la compra a sí mismo.

    Cubo propio y más holgado: es una lectura que no crea nada, no cobra y no
    reserva stock. Sigue limitada porque recalcula precios y stock en cada
    llamada, que es trabajo real contra la base de datos.
    """

    scope = 'checkout_quote'


class CartThrottle(AnonRateThrottle):
    scope = 'cart'


class PaymentStatusThrottle(AnonRateThrottle):
    scope = 'payment_status'


class ResendVerificationThrottle(AnonRateThrottle):
    scope = 'resend_verification'


class VerifyEmailCodeThrottle(AnonRateThrottle):
    scope = 'verify_email_code'


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


class AdminImportThrottle(UserRateThrottle):
    """
    Las cargas masivas: inspeccionar, previsualizar y aplicar.

    No tenían límite. Una previsualización con imágenes las recodifica todas y
    las deja guardadas hasta la limpieza diaria: sin tope, un bucle llena el
    almacenamiento y ocupa el único proceso del servidor.
    """
    scope = 'admin_import'


class ServiceEvidenceReadThrottle(UserRateThrottle):
    """
    Mirar la galería de una orden: la lista y cada imagen.

    Cupo propio y amplio. Cada miniatura es una petición, así que una galería
    de cuarenta fotos son cuarenta y una al abrirse; compartir cupo con los
    cambios de estado dejaba al técnico sin poder subir tras recargar.
    """
    scope = 'service_evidence_read'


class ServiceEvidenceWriteThrottle(UserRateThrottle):
    """Subir, anotar, compartir, ocultar o anular una evidencia."""
    scope = 'service_evidence_write'


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


class FiscalIssueThrottle(UserRateThrottle):
    """
    Emitir y enviar tocan un servicio externo. Cubo propio, no el del checkout.

    ERP-FISCAL-1C. Era `AnonRateThrottle`, que NO limita a peticiones
    autenticadas (devuelve cache key None en cuanto hay usuario) — el mismo
    defecto que corrigió AUTH-THROTTLE-01 en login. Como TODO endpoint fiscal
    es `IsAuthenticated`, el ritmo `fiscal_issue` nunca se aplicaba. Con
    `UserRateThrottle` el cubo es por usuario y el límite sí rige.

    NO ES LA DEFENSA CONTRA EL DOBLE ENVÍO. Eso vive en el dominio: la reserva
    de intento y las restricciones de base de datos. Un limitador sólo espacia
    peticiones; dos clics separados por un segundo pasarían igual.
    """

    scope = 'fiscal_issue'


class FiscalReadThrottle(UserRateThrottle):
    """
    Consultar estado y descargar artefactos. Más holgado: no sale a la red.

    ERP-FISCAL-1C. Igual que arriba: `UserRateThrottle`, no `AnonRateThrottle`,
    para que el límite aplique a los usuarios autenticados que son los únicos
    que llegan aquí.
    """

    scope = 'fiscal_read'


class StaffInviteThrottle(UserRateThrottle):
    """
    Invitar, reenviar y revocar. Cubo propio: envía correo a terceros.

    No se reutiliza el de checkout ni el del punto de venta — comparten cubo
    significa que una operación agota el presupuesto de otra sin relación, que
    es el defecto que ya apareció una vez con la cotización fiscal.
    """

    scope = 'staff_invite'


class StaffAcceptThrottle(AnonRateThrottle):
    """
    Leer y aceptar una invitación. ANÓNIMO: es la única defensa contra alguien
    probando tokens al azar, porque quien lo hace no está autenticado.
    """

    scope = 'staff_accept'


class TrackingReadThrottle(AnonRateThrottle):
    """
    Abrir un enlace de seguimiento y pedir sus fotos. Por dirección.

    El enlace no se puede adivinar (128 bits más un sello), así que este cupo
    no es lo que lo protege: evita que la página pública sea un grifo abierto.
    """
    scope = 'tracking_read'


class TrackingDecisionThrottle(AnonRateThrottle):
    """Responder una cotización o reclamar una orden por su enlace."""
    scope = 'tracking_write'

    def get_cache_key(self, request, view):
        # Por dirección también con sesión: `AnonRateThrottle` no cuenta a quien
        # inició sesión, y reclamar órdenes es justo lo que se haría con una.
        return self.cache_format % {'scope': self.scope, 'ident': self.get_ident(request)}


class AccountRepairsThrottle(UserRateThrottle):
    scope = 'account_repairs'


class WhatsAppWebhookThrottle(AnonRateThrottle):
    """El proveedor informa aquí de cada mensaje. La firma decide; esto acota."""
    scope = 'whatsapp_webhook'


class GoogleSignInThrottle(AnonRateThrottle):
    """Entrar con Google, por dirección. Su propio cupo: no gasta el del login con contraseña."""
    scope = 'google_sign_in'
