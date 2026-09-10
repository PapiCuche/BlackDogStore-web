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


class FiscalIssueThrottle(AnonRateThrottle):
    """
    Emitir y enviar tocan un servicio externo. Cubo propio, no el del checkout.

    NO ES LA DEFENSA CONTRA EL DOBLE ENVÍO. Eso vive en el dominio: la reserva
    de intento y las restricciones de base de datos. Un limitador sólo espacia
    peticiones; dos clics separados por un segundo pasarían igual.
    """

    scope = 'fiscal_issue'


class FiscalReadThrottle(AnonRateThrottle):
    """Consultar estado y descargar artefactos. Más holgado: no sale a la red."""

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
