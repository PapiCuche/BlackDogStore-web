"""
The checkout domain, shared by both surfaces.

TWO TRANSPORTS, ONE SET OF COMMERCIAL RULES (DEC-API-002).

  BROWSER   `/api/payments/create-checkout-session/`  anonymous, session cart,
                                                      tenant from Host
  NATIVE    `/api/v1/customer/<slug>/checkout/`       Bearer, item intents in
                                                      the body, tenant from path

What differs is how a request identifies itself and where its items come from.
What must NOT differ is what a thing costs, whether there is stock for it, which
branch ships it, what the coupon is worth, and what an Order looks like
afterwards. Two copies of that would drift, and the drift would be a customer
charged one price by the web and another by the app.

So the surfaces keep their own authentication, their own tenant resolution and
their own input shape, and both funnel into the functions below.

⚠️  THE CLIENT IS NEVER A PRICING AUTHORITY. Nothing here reads a price, a
subtotal, a discount or a total from a request. Every figure is recomputed from
`Product.price` and `Coupon.discount_percent` at the moment of checkout.
"""
import logging
from dataclasses import dataclass, field
from datetime import timezone as dt_timezone
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .company_settings import build_identity_snapshot
from .customer_services import link_order_to_customer
from .models import (
    BranchStock, Coupon, Order, OrderItem, PaymentTransaction, Product,
    assert_items_match_order,
)
from .payments import izipay
from .tenancy import company_fulfillment_branch

logger = logging.getLogger(__name__)

CENTS = Decimal('0.01')


class CheckoutError(Exception):
    """
    A checkout that cannot proceed, with the answer already decided.

    Carries the HTTP status so both surfaces report the same failure the same
    way — an empty cart is a 400 on the web and must not become a 500 on mobile
    just because a different view caught it.
    """

    def __init__(self, detail: str, *, status_code: int = 400, errors=None):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code
        self.errors = errors or []

    def as_payload(self) -> dict:
        payload = {'detail': self.detail}
        if self.errors:
            payload['errors'] = self.errors
        return payload


@dataclass(frozen=True)
class CheckoutLine:
    """One product and how many of it. The normalised form both surfaces reach."""

    product: Product
    quantity: int


def merge_lines(lines: list[CheckoutLine]) -> list[CheckoutLine]:
    """
    Collapse repeated products into one line each — Phase 0.3 / P0-E.

    WHY THIS IS CORRECTNESS AND NOT TIDINESS
    ----------------------------------------
    `inventory_services.record_sale_stock_movements` is idempotent per
    `(order, product)`, and that guard is what makes a replayed notification
    safe. It cannot tell a replay from an order that genuinely carries two lines
    of one product: it writes the exit for the first and skips the second. Six
    units charged, three decremented.

    The guard is not the defect and must not be weakened. What has to be
    impossible is the duplicate line — so every surface merges before it
    validates, prices or persists anything.

    The native checkout already summed repeated slugs and the POS already merged
    repeated ids, each for this reason and each in its own way. The browser
    checkout did neither: it turned every cart row into its own line.

    GROUPED BY PRIMARY KEY, deliberately. Two ORM instances of the same row are
    the same product; grouping by object identity would merge nothing, and
    grouping by name or slug would merge things that are not the same article.

    Order is preserved — first appearance wins — so a shopper reading their own
    basket back sees it in the order they built it.
    """
    merged: dict[int, int] = {}
    first_seen: dict[int, Product] = {}
    for line in lines:
        key = line.product.pk
        if key not in first_seen:
            first_seen[key] = line.product
        merged[key] = merged.get(key, 0) + line.quantity
    return [
        CheckoutLine(product=first_seen[key], quantity=quantity)
        for key, quantity in merged.items()
    ]


@dataclass
class CheckoutPricing:
    subtotal: Decimal
    discount_amount: Decimal
    total: Decimal
    # Applied to each line when a per-line view of the basket is needed, so the discount is
    # distributed across the basket rather than shown as a phantom extra line.
    discount_multiplier: Decimal
    coupon: Coupon | None = None


@dataclass
class CustomerDetails:
    """
    The commercial fields a buyer supplies, already validated by the surface.

    A plain container on purpose: the two surfaces validate with different
    serializers (the browser sends `session_key`, the app does not) but the
    business needs exactly these facts either way.
    """

    name: str
    email: str
    phone: str
    document_type: str
    document_number: str
    delivery_method: str
    receipt_type: str
    accepted_terms: bool
    accepted_warranty_policy: bool
    address_line: str = ''
    city: str = ''
    district: str = ''
    reference: str = ''
    notes: str = ''


@dataclass
class IdempotencyStamp:
    """What makes a native checkout replay-safe. Absent for the browser."""

    key: str
    fingerprint: str


def resolve_fulfillment_branch(company):
    """
    Which branch sells this order, decided ONCE.

    Resolved from the company's configuration, never from the request: a
    customer has no branch picker and must not acquire one by editing a payload.

    No branch means no sale. A company that has not said where it ships from
    cannot take an online order, and saying so beats shipping from a shop that
    does not know it sold anything.
    """
    branch = company_fulfillment_branch(company)
    if branch is None:
        raise CheckoutError(
            'La tienda no tiene una sucursal de despacho configurada. Inténtelo más tarde.',
        )
    return branch


def resolve_lines_from_intents(company, intents) -> list[CheckoutLine]:
    """
    Turn `[{slug, quantity}]` into products of THIS company.

    The native counterpart of loading a session cart. Resolution is scoped to
    the company first, so a slug belonging to another tenant matches nothing
    rather than matching and then being filtered — and a product id is never
    accepted at all, because an id is guessable across tenants and a slug that
    resolves inside the wrong company simply does not exist.
    """
    if not intents:
        raise CheckoutError('El carrito está vacío.')

    wanted = {}
    for intent in intents:
        slug = (intent.get('product_slug') or '').strip()
        quantity = int(intent.get('quantity') or 0)
        if not slug:
            raise CheckoutError('Hay un producto sin identificar en el carrito.')
        # A slug repeated across lines is one basket entry, not two. Summing is
        # what the shopper meant; taking the last would silently drop the first.
        wanted[slug] = wanted.get(slug, 0) + quantity

    products = {
        product.slug: product
        for product in Product.objects.filter(company=company, slug__in=list(wanted))
    }

    lines = []
    missing = []
    for slug, quantity in wanted.items():
        product = products.get(slug)
        if product is None:
            missing.append(slug)
            continue
        lines.append(CheckoutLine(product=product, quantity=quantity))

    if missing:
        # Named rather than counted: the app has to tell the shopper WHICH line
        # to remove, and a slug is not a secret — they just sent it.
        raise CheckoutError(
            'Algunos productos ya no están disponibles.',
            errors=[f'Producto no disponible: {slug}.' for slug in sorted(missing)],
        )

    return lines


def validate_lines_and_subtotal(branch, lines: list[CheckoutLine]) -> Decimal:
    """
    Check every line against the FULFILLMENT BRANCH and price it.

    Stock is never read from `Product.inventory`: buying 5 must fail when the
    shipping branch holds 2, even if the company holds 20 across its other
    shops. The storefront already shows the branch figure, so the two agree.

    Every problem is collected before answering. Fixing a basket one rejection
    at a time is a miserable way to buy something.
    """
    if not lines:
        raise CheckoutError('El carrito está vacío.')

    # Merged FIRST. Checking each line on its own asked "is 3 available?" twice
    # of a shelf holding 5 and answered yes both times, selling six. Stock is a
    # property of the product, so the question has to be asked once per product,
    # about the total.
    lines = merge_lines(lines)

    stock = {
        row.product_id: row.quantity
        for row in BranchStock.objects.filter(
            branch=branch, product_id__in=[line.product.id for line in lines],
        )
    }

    errors = []
    subtotal = Decimal('0.00')
    for line in lines:
        product = line.product
        if not product.is_active:
            errors.append(f'{product.name} ya no está disponible.')
            continue
        if line.quantity <= 0:
            errors.append(f'Cantidad inválida para {product.name}.')
            continue
        available = stock.get(product.id, 0)
        if available < line.quantity:
            errors.append(
                f'Stock insuficiente para {product.name}. '
                f'Disponible: {available}, solicitado: {line.quantity}.'
            )
            continue
        subtotal += product.price * line.quantity

    if errors:
        raise CheckoutError('Problemas con el carrito.', errors=errors)

    return subtotal


def price_checkout(company, subtotal: Decimal, coupon_code: str = '') -> CheckoutPricing:
    """
    Apply the coupon and produce the definitive figures.

    The coupon is looked up WITHIN the company. Two tenants may run the same
    code, and honouring another company's discount is both a leak and a
    financial error.
    """
    from django.utils import timezone

    code = (coupon_code or '').upper().strip()
    if not code:
        return CheckoutPricing(
            subtotal=subtotal,
            discount_amount=Decimal('0.00'),
            total=subtotal.quantize(CENTS),
            discount_multiplier=Decimal('1.0'),
        )

    coupon = Coupon.objects.filter(company=company, code=code, is_active=True).first()
    if coupon is None:
        raise CheckoutError('Cupón no válido o inactivo.')
    if coupon.expires_at and coupon.expires_at < timezone.now():
        raise CheckoutError('El cupón ha expirado.')

    multiplier = (Decimal('100') - Decimal(str(coupon.discount_percent))) / Decimal('100')
    discount = (subtotal * (1 - multiplier)).quantize(CENTS)
    return CheckoutPricing(
        subtotal=subtotal,
        discount_amount=discount,
        total=(subtotal - discount).quantize(CENTS),
        discount_multiplier=multiplier,
        coupon=coupon,
    )


def create_pending_order(
    *,
    company,
    branch,
    lines: list[CheckoutLine],
    pricing: CheckoutPricing,
    details: CustomerDetails,
    actor=None,
    order_user=None,
    cart_session_key: str = '',
    idempotency: IdempotencyStamp | None = None,
) -> Order:
    """
    Write the Order, its items and its CRM link, in one transaction.

    NOTHING IS CONSUMED HERE. The cart is not cleared and stock is not
    decremented: payment has not happened yet, and an order that never gets paid
    must not have cost anyone their basket or the shop its inventory. Both
    happen when the gateway's notification confirms.

    `order_user` is separate from `actor` on purpose. `actor` is who performed
    the action (used as `created_by` on a new CRM record); `order_user` is who
    the order BELONGS to. The browser surface has neither for a guest; the
    native surface always has both, and they are the same person.
    """
    # Merged here too, not only in validation. This function is the last point
    # before the lines become rows, and it is reached by every surface, so a
    # caller that skipped validation — or a future one — still cannot persist two
    # lines of one product. Merging an already-merged basket is a no-op, so
    # doing it in both places costs nothing and removes the need to remember.
    lines = merge_lines(lines)

    with transaction.atomic():
        order = Order.objects.create(
            company=company,
            fulfillment_branch=branch,
            # Freeze WHO IS SELLING, right now. Every document this order ever
            # produces reads its identity from here, so a business that is later
            # renamed cannot rewrite what a receipt from months ago says.
            company_snapshot=build_identity_snapshot(company, branch),
            user=order_user,
            customer_name=details.name,
            customer_email=details.email,
            total=pricing.total,
            discount_amount=pricing.discount_amount,
            coupon_code=pricing.coupon.code if pricing.coupon else '',
            cart_session_key=cart_session_key,
            status=Order.Status.PENDING_PAYMENT,
            paid=False,
            customer_phone=details.phone,
            document_type=details.document_type,
            document_number=details.document_number,
            delivery_method=details.delivery_method,
            address_line=details.address_line,
            city=details.city,
            district=details.district,
            reference=details.reference,
            notes=details.notes,
            receipt_type=details.receipt_type,
            accepted_terms=details.accepted_terms,
            accepted_warranty_policy=details.accepted_warranty_policy,
            idempotency_key=idempotency.key if idempotency else None,
            idempotency_fingerprint=idempotency.fingerprint if idempotency else '',
        )

        # Belt and braces: the queryset already guarantees this, but the
        # invariant is asserted before writing rather than trusted.
        assert_items_match_order(order, [line.product for line in lines])

        # Best-effort BY DESIGN: `link_order_to_customer` swallows its own
        # failures and leaves `order.customer` null, because a problem in the
        # CRM must never cost a sale. Matching is the account or the validated
        # document — never a resemblance, never an email.
        link_order_to_customer(order, actor=actor)

        for line in lines:
            OrderItem.objects.create(
                order=order,
                product=line.product,
                quantity=line.quantity,
                price=line.product.price,
            )

    return order


@dataclass(frozen=True)
class PaymentSession:
    """
    Everything the browser needs to render the gateway's form — and nothing else.

    Every field here is public by the provider's own documentation: the merchant
    code, the RSA public key and a session token minted for ONE transaction. The
    API key and the hash key are not in this object and never reach a response;
    that is the whole point of it being a declared shape rather than an ad-hoc
    dict a view assembles.

    `environment` is a NAME, not a URL. The frontend holds the two official SDK
    addresses and picks by name, so no script source ever travels as data and a
    tampered response cannot point the page at someone else's script.
    """

    transaction_id: str
    order_number: str
    authorization: str
    environment: str
    merchant_code: str
    public_key: str
    config: dict


def require_payment_provider_configured() -> izipay.IzipayCredentials:
    """
    Load the gateway credentials, or refuse the checkout before creating an order.

    Checked FIRST, deliberately. Creating a pending order and only then finding
    out there is no gateway leaves a row nobody can pay and a buyer looking at an
    error, every single time, until someone sets the variable.
    """
    try:
        return izipay.load_credentials()
    except izipay.IzipayError as exc:
        # The operator needs the detail; it names variables, never values.
        logger.error('Pasarela de pago mal configurada: %s', exc)
        raise CheckoutError(
            'La pasarela de pago no está configurada.', status_code=500,
        )


def build_payment_config(
    order: Order,
    *,
    credentials: izipay.IzipayCredentials,
    transaction_id: str,
    order_number: str,
) -> dict:
    """
    The provider's view of this payment, assembled entirely from the database.

    THE AMOUNT IS `Order.total`, quantised once, as a string. Not a sum of line
    prices recomputed here — that is the arithmetic that produces a figure a
    cent away from the order for a basket of three items at 33.333, and then a
    notification that fails the equality check for a payment that was perfectly
    fine.

    The buyer's own name, e-mail and document go in the billing block because
    the gateway requires them to authorise. Nothing is read from the request:
    every value comes off the Order that was already validated.
    """
    config = {
        'transactionId': transaction_id,
        # `pay` — a single immediate charge. NOT `register` or a token flow:
        # this project stores no cards, so there is no PCI surface to defend.
        'action': 'pay',
        'merchantCode': credentials.merchant_code,
        'order': {
            'orderNumber': order_number,
            'currency': credentials.currency,
            'amount': izipay.format_amount(order.total),
            # 'AT' as shown in every official example for an immediate
            # authorisation.
            'processType': 'AT',
            'merchantBuyerId': str(order.customer_id or order.pk),
            # Epoch milliseconds. The exact format is not stated on any
            # server-rendered page of the official documentation; this matches
            # the shape of every published example, and is one of the values to
            # confirm against sandbox.
            'dateTimeTransaction': str(
                int(timezone.now().astimezone(dt_timezone.utc).timestamp() * 1000)
            ),
        },
        'billing': {
            'firstName': (order.customer_name or '').strip()[:60] or 'Cliente',
            'lastName': '',
            'email': order.customer_email or '',
            'phoneNumber': order.customer_phone or '',
            'street': order.address_line or '',
            'city': order.city or '',
            'state': order.district or '',
            'country': 'PE',
            'postalCode': '',
            'documentType': (order.document_type or '').upper(),
            'document': order.document_number or '',
        },
    }
    # Told per transaction when we have a public address for it; otherwise the
    # merchant panel holds it. Either way the endpoint trusts the signature and
    # not the route the message took.
    if settings.IZIPAY_IPN_URL:
        config['urlIPN'] = settings.IZIPAY_IPN_URL
    return config


def start_payment_attempt(
    order: Order, *, credentials: izipay.IzipayCredentials,
) -> PaymentSession:
    """
    Open ONE attempt to charge this order.

    A row first, then the network call. The `PaymentTransaction` is what a later
    notification is resolved by, so it has to exist before anything can arrive
    about it — the reverse order leaves a window in which a genuine, correctly
    signed notification refers to an attempt this database has never heard of.

    NOTHING IS DECREMENTED HERE. No stock, no cart, no order status. Creating a
    payment attempt is asking a question, and the answer only arrives, verified,
    at the notification endpoint.

    A NEW `order_number` EVERY TIME. Izipay rejects a repeated one (P69), so a
    buyer retrying after a decline needs a fresh one, and the previous attempt
    keeps its own record of having been declined.
    """
    transaction_id = izipay.new_transaction_id()
    order_number = izipay.new_order_number()

    attempt = PaymentTransaction.objects.create(
        order=order,
        provider=izipay.PROVIDER,
        transaction_id=transaction_id,
        order_number=order_number,
        amount=Decimal(order.total).quantize(CENTS),
        currency=credentials.currency,
        status=PaymentTransaction.Status.PENDING,
    )

    config = build_payment_config(
        order,
        credentials=credentials,
        transaction_id=transaction_id,
        order_number=order_number,
    )

    try:
        authorization = izipay.request_session_token(
            credentials=credentials,
            transaction_id=transaction_id,
            payload=config,
        )
    except izipay.IzipayError as exc:
        attempt.status = PaymentTransaction.Status.REJECTED
        attempt.failure_reason = str(exc)[:200]
        attempt.save(update_fields=['status', 'failure_reason'])
        raise CheckoutError(
            'No pudimos iniciar el pago. Vuelve a intentarlo.', status_code=502,
        )

    return PaymentSession(
        transaction_id=transaction_id,
        order_number=order_number,
        authorization=authorization,
        environment=credentials.environment,
        merchant_code=credentials.merchant_code,
        public_key=credentials.public_key,
        config=config,
    )


def mark_payment_failure(order: Order, message: str) -> None:
    """
    Record that this order could not be paid.

    `payment_error` is for an operator. The message handed to the buyer is
    written at the view, generic on purpose: a provider's error text can name
    internal endpoints and configuration.
    """
    order.status = Order.Status.FAILED
    order.payment_error = (message or '')[:500]
    order.save(update_fields=['status', 'payment_error'])
