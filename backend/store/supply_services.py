"""
DEVICE-COMMERCE-01 · DC3 — suppliers and selling «bajo pedido».

THE OWNER'S RULES
-----------------
  * Stock 0 does NOT mean sellable. A product sells without own stock only
    when its `sale_mode` allows it («Bajo pedido») AND a valid offer of a
    supplier covers the WHOLE line. The buyer (or the till) asks for it on
    purpose; nothing slides from «sin stock» to «bajo pedido» on its own.
  * A line is all shelf or all on order, never both: the order line, its
    snapshot and its Kardex have one origin. A line that the shelf covers only
    in part goes on order whole (the buyer asked for that quantity on order).
  * Own stock leaves the shelf as always. Goods of a supplier are never taken
    from a shelf that does not have them: the payment writes no movement, it
    COMMITS the offer; the goods enter and leave when they arrive.
  * Money is never ignored. A payment that arrives when the offer expired or
    ran out is recorded; the line waits for a person («abastecimiento por
    confirmar»), who confirms it against an offer or cancels / refunds it
    through the existing flow.

WHICH OFFER
-----------
`best_offer`: among the VALID offers whose remaining quantity covers the line —
active, of an active supplier, confirmed, today within `valid_from`..
`valid_until` — the LOWEST COMMERCIAL PRICE (what the buyer pays), then the
lowest agreed cost (the shop's better deal), then the one that ends first (use
the offer before it lapses), then the lowest id. Deterministic: the catalogue,
the cart, the checkout and the till all name the same offer.

WHAT THE BUYER PAYS: the offer's `commercial_price`, frozen on the order line
at checkout (the line's `price`, like any line). A supplier's price list moves
weekly; the order does not.

LOCK ORDER: the order row, then offer rows, then shelf rows.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction
from django.db.models import F, Q, Sum
from django.utils import timezone

from .models import AdminAuditLog, Order, OrderItem, Product, StockMovement, Supplier, SupplierOffer

MAX_LEAD_TIME_DAYS = 365
MAX_QUANTITY = 100000


class SupplyError(Exception):
    """A supply rule refused something. `field` names the input, when there is one."""

    def __init__(self, detail: str, *, field: str = '', code: str = 'invalid',
                 line: int | None = None, unit_field: str = ''):
        super().__init__(detail)
        self.detail = detail
        self.field = field
        self.code = code
        # SUPPLIER-DEVICE-FLOW-02: a refused device of a reception says WHICH
        # row (1-based) and which of its inputs, so the form marks that input.
        self.line = line
        self.unit_field = unit_field

    def body(self) -> dict:
        payload = {'detail': self.detail, 'code': self.code}
        if self.field:
            payload['field'] = self.field
        if self.line is not None:
            payload['line'] = self.line
        if self.unit_field:
            payload['unit_field'] = self.unit_field
        return payload


def today() -> date:
    return timezone.localdate()


def _audit(action, *, company, actor, target_type, target_id, request=None, **metadata):
    AdminAuditLog.log(actor=actor, action=action, target_type=target_type, target_id=target_id,
                      metadata=metadata, request=request, company=company)


# ---------------------------------------------------------------------------
# Cleaning
# ---------------------------------------------------------------------------

def _text(value, field, label, *, max_length, required=False) -> str:
    text = ' '.join(str(value or '').split())
    if required and not text:
        raise SupplyError(f'Indica {label}.', field=field)
    if len(text) > max_length:
        raise SupplyError(f'{label[0].upper()}{label[1:]} es demasiado largo.', field=field)
    return text


def clean_money(value, field, label, *, required=False, positive=False) -> Decimal | None:
    if value is None or value == '':
        if required:
            raise SupplyError(f'Indica {label}.', field=field)
        return None
    if isinstance(value, float):
        value = repr(value)
    try:
        amount = Decimal(str(value).replace(',', '.').strip())
    except (InvalidOperation, ValueError):
        raise SupplyError(f'{label[0].upper()}{label[1:]} no es un importe válido.', field=field) from None
    if not amount.is_finite() or amount < 0 or amount >= Decimal('10000000000'):
        raise SupplyError(f'{label[0].upper()}{label[1:]} no es un importe válido.', field=field)
    if positive and amount <= 0:
        raise SupplyError(f'{label[0].upper()}{label[1:]} debe ser mayor que cero.', field=field)
    if amount != amount.quantize(Decimal('0.01')):
        raise SupplyError(f'{label[0].upper()}{label[1:]} lleva a lo sumo dos decimales.', field=field)
    return amount.quantize(Decimal('0.01'))


def clean_int(value, field, label, *, minimum=0, maximum=MAX_QUANTITY, required=True) -> int | None:
    if value is None or value == '':
        if required:
            raise SupplyError(f'Indica {label}.', field=field)
        return None
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise SupplyError(f'{label[0].upper()}{label[1:]} debe ser un número entero.', field=field) from None
    if not number.is_finite() or number != number.to_integral_value():
        raise SupplyError(f'{label[0].upper()}{label[1:]} debe ser un número entero.', field=field)
    number = int(number)
    if number < minimum or number > maximum:
        raise SupplyError(f'{label[0].upper()}{label[1:]} está fuera de rango ({minimum}–{maximum}).', field=field)
    return number


def clean_date(value, field, label) -> date:
    if isinstance(value, date):
        return value
    text = str(value or '').strip()
    if not text:
        raise SupplyError(f'Indica {label}.', field=field)
    for parse in (date.fromisoformat, _dmy):
        try:
            return parse(text[:10])
        except (ValueError, TypeError):
            continue
    raise SupplyError(f'{label[0].upper()}{label[1:]} no es una fecha (AAAA-MM-DD o DD/MM/AAAA).', field=field)


def _dmy(text: str) -> date:
    day, month, year = text.replace('-', '/').split('/')
    return date(int(year), int(month), int(day))


def _clean_document(document_type, document_number) -> tuple[str, str]:
    from .identity_validation import IdentityError, clean_document

    try:
        return clean_document(str(document_type or '').strip().lower(), str(document_number or ''))
    except IdentityError as exc:
        field = exc.args[0] if exc.args and exc.args[0] in ('document_type', 'document_number') else 'document_number'
        raise SupplyError(str(exc.args[-1]) if exc.args else 'Documento inválido.', field=field) from None


# ---------------------------------------------------------------------------
# Suppliers
# ---------------------------------------------------------------------------

_SUPPLIER_FIELDS = ('name', 'document_type', 'document_number', 'contact_name', 'phone', 'email', 'notes')


def _supplier_values(fields: dict, current: Supplier | None = None) -> dict:
    from django.core.exceptions import ValidationError
    from django.core.validators import validate_email

    values = {}
    if 'name' in fields or current is None:
        values['name'] = _text(fields.get('name'), 'name', 'el nombre del proveedor', max_length=200, required=True)
    if 'document_type' in fields or 'document_number' in fields:
        kind = fields.get('document_type', getattr(current, 'document_type', ''))
        number = fields.get('document_number', getattr(current, 'document_number', ''))
        values['document_type'], values['document_number'] = _clean_document(kind, number)
    if 'contact_name' in fields:
        values['contact_name'] = _text(fields['contact_name'], 'contact_name', 'el contacto', max_length=150)
    if 'phone' in fields:
        values['phone'] = _text(fields['phone'], 'phone', 'el teléfono', max_length=30)
    if 'email' in fields:
        email = _text(fields['email'], 'email', 'el correo', max_length=254)
        if email:
            try:
                validate_email(email)
            except ValidationError:
                raise SupplyError('El correo no es válido.', field='email') from None
        values['email'] = email
    if 'notes' in fields:
        values['notes'] = str(fields['notes'] or '').strip()[:1000]
    return values


def create_supplier(*, company, actor=None, request=None, **fields) -> Supplier:
    values = _supplier_values(fields)
    try:
        with transaction.atomic():
            supplier = Supplier.objects.create(company=company, created_by=actor, **values)
    except IntegrityError:
        raise SupplyError('Ya hay un proveedor con ese nombre.', field='name') from None
    _audit('supplier_created', company=company, actor=actor, target_type='supplier', target_id=supplier.pk,
           request=request, name=supplier.name)
    return supplier


def update_supplier(supplier, *, actor=None, request=None, **fields) -> Supplier:
    values = _supplier_values({k: v for k, v in fields.items() if k in _SUPPLIER_FIELDS}, current=supplier)
    if 'is_active' in fields:
        values['is_active'] = fields['is_active'] not in (False, 'false', '0', 0)
    if not values:
        return supplier
    try:
        with transaction.atomic():
            Supplier.objects.filter(pk=supplier.pk).update(updated_at=timezone.now(), **values)
    except IntegrityError:
        raise SupplyError('Ya hay un proveedor con ese nombre.', field='name') from None
    _audit('supplier_updated', company=supplier.company, actor=actor, target_type='supplier',
           target_id=supplier.pk, request=request, fields=sorted(values))
    supplier.refresh_from_db()
    return supplier


# ---------------------------------------------------------------------------
# Sale mode
# ---------------------------------------------------------------------------

def set_sale_mode(product, mode, *, actor=None, request=None) -> Product:
    if mode not in dict(Product.SALE_MODE_CHOICES):
        raise SupplyError('Modo de venta no válido.', field='sale_mode')
    if mode == Product.SALE_MODE_STOCK_OR_ORDER and product.condition_line == Product.CONDITION_USED:
        # A used device is a device: it is sold one by one, never «one like it».
        raise SupplyError('Un seminuevo se vende por equipo, nunca bajo pedido.', field='sale_mode')
    if product.sale_mode == mode:
        return product
    Product.objects.filter(pk=product.pk).update(sale_mode=mode, updated_at=timezone.now())
    _audit('product_sale_mode_changed', company=product.company, actor=actor, target_type='product',
           target_id=product.pk, request=request, sale_mode=mode)
    product.refresh_from_db()
    return product


# ---------------------------------------------------------------------------
# Offers
# ---------------------------------------------------------------------------

def _offer_values(company, fields: dict, *, current=None, may_view_costs: bool, external_card: bool = False) -> dict:
    values = {}
    if 'supplier_id' in fields:
        supplier = Supplier.objects.filter(company=company, pk=_pk(fields['supplier_id'])).first()
        if supplier is None:
            raise SupplyError('Proveedor no encontrado.', field='supplier_id', code='not_found')
        values['supplier'] = supplier
    if 'product_id' in fields:
        product = Product.objects.filter(company=company, pk=_pk(fields['product_id'])).first()
        if product is None:
            raise SupplyError('Producto no encontrado.', field='product_id', code='not_found')
        # INV-USED-EXT-01: a used variant is offered only device by device, through its cards
        # (`external_used`); the generic used offer stays forbidden.
        if product.condition_line == Product.CONDITION_USED and not external_card:
            raise SupplyError('Un seminuevo se vende por equipo, nunca bajo pedido.', field='product_id')
        values['product'] = product
    if 'agreed_cost' in fields and may_view_costs:
        # DC1 — without `costs.view` a cost is neither read nor written.
        values['agreed_cost'] = clean_money(fields['agreed_cost'], 'agreed_cost', 'el costo pactado')
    if 'commercial_price' in fields or current is None:
        values['commercial_price'] = clean_money(fields.get('commercial_price'), 'commercial_price',
                                                 'el precio comercial', required=True, positive=True)
    if 'confirmed_quantity' in fields:
        values['confirmed_quantity'] = clean_int(fields['confirmed_quantity'], 'confirmed_quantity',
                                                 'la cantidad confirmada', required=False)
        # INV-SNAPSHOT-01: a stated quantity confirms the availability; none leaves it unconfirmed. Saving an
        # unconfirmed offer with the SAME number it holds (the editor resends it) confirms nothing (review).
        unchanged = (current is not None and not current.availability_confirmed
                     and values['confirmed_quantity'] == current.confirmed_quantity)
        # Second review: a person may confirm the very number the offer holds, explicitly. An explicit False is
        # not a way to un-confirm: a quantity given always means the supplier stated it (leave it blank instead).
        explicit = fields.get('availability_confirmed') is True
        values['availability_confirmed'] = values['confirmed_quantity'] is not None and (explicit or not unchanged)
    if fields.get('availability_unconfirmed_keeping_commitments') and current is not None:
        # A complete list that gives no quantity for an offer with sales: those keep their course
        # (confirmed = committed), nothing more sells. Only the snapshot import asks for this.
        values['confirmed_quantity'] = current.committed_quantity
        values['availability_confirmed'] = False
    if 'valid_from' in fields or current is None:
        values['valid_from'] = clean_date(fields.get('valid_from'), 'valid_from', 'el inicio de la vigencia')
    if 'valid_until' in fields or current is None:
        values['valid_until'] = clean_date(fields.get('valid_until'), 'valid_until', 'el fin de la vigencia')
    if 'lead_time_days' in fields or current is None:
        values['lead_time_days'] = clean_int(fields.get('lead_time_days'), 'lead_time_days', 'el plazo en días',
                                             maximum=MAX_LEAD_TIME_DAYS)
    if 'is_active' in fields:
        values['is_active'] = fields['is_active'] not in (False, 'false', '0', 0)

    start = values.get('valid_from', getattr(current, 'valid_from', None))
    end = values.get('valid_until', getattr(current, 'valid_until', None))
    if start and end and end < start:
        raise SupplyError('La vigencia termina antes de empezar.', field='valid_until')
    if current is not None and 'confirmed_quantity' in values \
            and not fields.get('availability_unconfirmed_keeping_commitments'):
        confirmed = values['confirmed_quantity']
        if current.committed_quantity and (confirmed is None or confirmed < current.committed_quantity):
            raise SupplyError(
                f'Ya se vendieron {current.committed_quantity} contra esta oferta: la cantidad confirmada '
                'no puede ser menor.', field='confirmed_quantity')
    return values


def _pk(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _offer_snapshot(offer) -> dict:
    return {
        'supplier_id': offer.supplier_id, 'product_id': offer.product_id,
        'commercial_price': str(offer.commercial_price), 'confirmed_quantity': offer.confirmed_quantity,
        'availability_confirmed': offer.availability_confirmed,
        'valid_from': offer.valid_from.isoformat(), 'valid_until': offer.valid_until.isoformat(),
        'lead_time_days': offer.lead_time_days, 'is_active': offer.is_active,
        # The audit trail is read by more people than `costs.view`: never the cost itself.
        'has_cost': offer.agreed_cost is not None,
    }


_DUPLICATE = 'Ya hay una oferta de ese proveedor para ese producto con esa misma vigencia.'


def _is_duplicate(exc) -> bool:
    """The one-offer-per-key index refused it (and not, say, the commitment CHECK)."""
    return 'unique_supplier_offer_window' in str(exc)


def create_offer(*, company, actor=None, request=None, may_view_costs: bool = False, external_card: bool = False,
                 **fields) -> SupplierOffer:
    """`external_card`: only `external_used.create_card` — the offer of a used variant, whose cards set its quantity."""
    fields.setdefault('supplier_id', None)
    fields.setdefault('product_id', None)
    values = _offer_values(company, fields, may_view_costs=may_view_costs, external_card=external_card)
    try:
        with transaction.atomic():
            offer = SupplierOffer.objects.create(company=company, created_by=actor, **values)
    except IntegrityError as exc:
        if not _is_duplicate(exc):
            raise
        raise SupplyError(_DUPLICATE, field='valid_from', code='duplicate') from None
    _audit('supplier_offer_created', company=company, actor=actor, target_type='supplier_offer',
           target_id=offer.pk, request=request, **_offer_snapshot(offer))
    return offer


def update_offer(offer, *, actor=None, request=None, may_view_costs: bool = False, **fields) -> SupplierOffer:
    fields = {k: v for k, v in fields.items() if k not in ('supplier_id', 'product_id')}
    with transaction.atomic():
        locked = SupplierOffer.objects.select_for_update().select_related('product').get(pk=offer.pk)
        if locked.product.condition_line == Product.CONDITION_USED and 'confirmed_quantity' in fields:
            # INV-USED-EXT-01: the quantity of a used offer is its cards; nobody types it.
            raise SupplyError('La cantidad de una oferta de seminuevos es la de sus fichas: agrega o retira fichas.',
                              field='confirmed_quantity')
        values = _offer_values(locked.company, fields, current=locked, may_view_costs=may_view_costs)
        if not values:
            return locked
        before = _offer_snapshot(locked)
        try:
            with transaction.atomic():
                SupplierOffer.objects.filter(pk=locked.pk).update(updated_at=timezone.now(), **values)
        except IntegrityError as exc:
            if not _is_duplicate(exc):
                raise
            raise SupplyError(_DUPLICATE, field='valid_from', code='duplicate') from None
        locked.refresh_from_db()
        changed = sorted(key for key, value in _offer_snapshot(locked).items() if before.get(key) != value)
        if 'agreed_cost' in values:
            changed.append('agreed_cost')
        _audit('supplier_offer_updated', company=locked.company, actor=actor, target_type='supplier_offer',
               target_id=locked.pk, request=request, changed=sorted(set(changed)), **_offer_snapshot(locked))
    offer.refresh_from_db()
    return offer


def deactivate_offer(offer, *, actor=None, request=None, reason: str = '', action='supplier_offer_deactivated'):
    """Switched off, never deleted: what was sold against it keeps pointing at it."""
    reason = ' '.join(str(reason or '').split())[:200]
    updated = SupplierOffer.objects.filter(pk=offer.pk, is_active=True).update(
        is_active=False, deactivated_reason=reason, updated_at=timezone.now())
    if updated:
        _audit(action, company=offer.company, actor=actor, target_type='supplier_offer', target_id=offer.pk,
               request=request, reason=reason)
    offer.refresh_from_db()
    return bool(updated)


def expire_offers(*, company=None, on=None) -> int:
    """
    Deactivate every active offer whose last day has passed. Idempotent (an
    offer already off is not touched again), audited per offer, and it never
    changes what was committed: orders already paid keep their offer.
    """
    on = on or today()
    qs = SupplierOffer.objects.filter(is_active=True, valid_until__lt=on)
    if company is not None:
        qs = qs.filter(company=company)
    done = 0
    for offer in qs.select_related('company'):
        if deactivate_offer(offer, actor=None, reason='Vigencia vencida', action='supplier_offer_expired'):
            done += 1
    return done


def valid_offer_q(*, on=None) -> Q:
    """
    What makes an offer able to sell today — the ONE predicate, for one product
    here and for a page of them in «Consultar disponibilidad».
    """
    on = on or today()
    return Q(
        is_active=True, supplier__is_active=True, confirmed_quantity__isnull=False, availability_confirmed=True,
        valid_from__lte=on, valid_until__gte=on, confirmed_quantity__gt=F('committed_quantity'),
    )


#: Which offer is best (see WHICH OFFER above), as an `order_by`.
BEST_OFFER_ORDER = ('commercial_price', F('agreed_cost').asc(nulls_last=True), 'valid_until', 'pk')


def valid_offers(product, *, on=None):
    """The offers of `product` that may sell today. Expiry is read here, not waited for."""
    offers = SupplierOffer.objects.filter(valid_offer_q(on=on), company_id=product.company_id, product=product)
    if product.condition_line == Product.CONDITION_USED:
        # INV-USED-EXT-01: a used offer sells only what live cards back (the generic one never).
        from django.db.models import Exists, OuterRef

        from .models import ExternalUsedCard

        offers = offers.filter(Exists(ExternalUsedCard.objects.filter(
            offer=OuterRef('pk'), status=ExternalUsedCard.Status.AVAILABLE)))
    return offers


def best_offer(product, quantity: int = 1, *, on=None):
    quantity = max(int(quantity), 1)
    offers = (valid_offers(product, on=on).filter(confirmed_quantity__gte=F('committed_quantity') + quantity)
              .order_by(*BEST_OFFER_ORDER))
    if product.condition_line != Product.CONDITION_USED:
        return offers.first()
    from . import external_used

    return next((offer for offer in offers if external_used.available_cards(offer).count() >= quantity), None)


def _offer_is_valid(offer, quantity: int, *, on=None) -> bool:
    on = on or today()
    valid = bool(
        offer.is_active and offer.supplier.is_active and offer.confirmed_quantity is not None
        and offer.availability_confirmed
        and offer.valid_from <= on <= offer.valid_until
        and offer.committed_quantity + quantity <= offer.confirmed_quantity
    )
    if valid and offer.product.condition_line == Product.CONDITION_USED:
        # INV-USED-EXT-01: a card per device sold, read under the offer's lock.
        from . import external_used

        valid = external_used.available_cards(offer).count() >= quantity
    return valid


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Availability:
    #: 'own' (the shelf covers it), 'supplier' (on order) or None (not sellable).
    origin: str | None
    own_stock: int
    offer: SupplierOffer | None = None
    lead_time_days: int | None = None
    price: Decimal | None = None


def sells_on_order(product) -> bool:
    """
    A new variant: when its mode says «Bajo pedido». A used one (INV-USED-EXT-01): only through the live
    cards of external devices (`valid_offers`); its mode stays «stock» — the generic used offer never sells.
    """
    if product.condition_line == Product.CONDITION_USED:
        return valid_offers(product).exists()
    return product.sale_mode == Product.SALE_MODE_STOCK_OR_ORDER


def own_stock(product, branch) -> int:
    from .models import BranchStock

    if branch is None:
        return 0
    row = BranchStock.objects.filter(branch=branch, product=product).values_list('quantity', flat=True).first()
    return max(int(row or 0), 0)


def availability(product, branch, quantity: int = 1, *, on=None) -> Availability:
    """
    Whether `quantity` of `product` can be sold from `branch` right now, and how.
    The ONE answer for the catalogue, the cart, the checkout and the till.
    """
    quantity = max(int(quantity or 1), 1)
    shelf = own_stock(product, branch)
    if shelf >= quantity:
        return Availability('own', shelf, price=product.price)
    if sells_on_order(product):
        offer = best_offer(product, quantity, on=on)
        if offer is not None:
            return Availability('supplier', shelf, offer, offer.lead_time_days, offer.commercial_price)
    return Availability(None, shelf)


def on_order_payload(product) -> dict | None:
    """
    What the shop may say publicly about selling `product` on order: the lead
    time and the price. Never a cost, a supplier or how many it confirmed.

    `over_stock: true` — only where the company lets the web buyer choose the
    supplier although the shelf has it (owner, 2026-10-15): the page may then
    offer it, saying it ships from the supplier in N days. Absent otherwise,
    so the payload keeps the shape it had.
    """
    if not sells_on_order(product):
        return None
    offer = best_offer(product, 1)
    if offer is None:
        return None
    payload = {'lead_time_days': offer.lead_time_days, 'price': str(offer.commercial_price)}
    if web_allows_on_order_over_stock(product.company_id):
        payload['over_stock'] = True
    return payload


#: What an on-order line chosen by a web buyer while the shelf had it says as its reason.
WEB_OVER_STOCK_REASON = 'Elegido por el comprador en la web'
#: What a web on-order line says when the shelf covers it at checkout but the company does not let
#: the web choose the supplier over stock: it was taken while the shelf could not cover it.
WEB_TAKEN_WHILE_EMPTY_REASON = 'Tomado bajo pedido cuando la tienda no tenía stock suficiente'


def web_allows_on_order_over_stock(company_id) -> bool:
    """
    Owner's decision of 2026-10-15 — own stock first. The web lets a buyer
    choose the supplier while the shelf covers the line only where the company
    turned `CompanySettings.web_on_order_over_stock` on (off as created).
    """
    from .models import CompanySettings

    return CompanySettings.objects.filter(company_id=company_id, web_on_order_over_stock=True).exists()


def over_stock_audit(*, action, order, line, own, actor=None, request=None, reason='') -> None:
    """
    The audit row of an on-order line chosen while the shelf covered it: what,
    how many, the shelf, the offer and its lead time, whether it carries a
    cost (never the cost: that is on the line, behind `costs.view`), and why.
    """
    _audit(action, company=order.company, actor=actor, target_type='order', target_id=order.pk, request=request,
           order_id=order.pk, product_id=line.product_id, quantity=line.quantity, own_stock=own,
           offer_id=line.supplier_offer_id,
           supplier_id=line.supplier_offer.supplier_id if line.supplier_offer_id else None,
           lead_time_days=line.lead_time_days, has_cost=line.unit_cost is not None, reason=reason)


# ---------------------------------------------------------------------------
# The order: checkout, payment, follow-up
# ---------------------------------------------------------------------------

def offer_for_line(product, quantity: int):
    """Checkout: the offer an on-order line is sold against, or None when none covers it."""
    if not sells_on_order(product):
        return None
    return best_offer(product, quantity)


def line_snapshot(offer) -> dict:
    """The fields an on-order line freezes when it is written (checkout or till)."""
    from .cost_services import quantize_cost

    return {
        'supply_origin': OrderItem.SUPPLY_SUPPLIER,
        'supplier_offer': offer,
        'lead_time_days': offer.lead_time_days,
        'unit_cost': quantize_cost(offer.agreed_cost) if offer.agreed_cost is not None else None,
        'cost_source': OrderItem.COST_SUPPLIER_OFFER if offer.agreed_cost is not None else OrderItem.COST_UNKNOWN,
    }


def _note(order, text: str) -> None:
    existing = order.payment_error or ''
    if text in existing:
        return
    order.payment_error = (existing + '\n' if existing else '') + text


def commit_order_lines(order, *, strict: bool = False) -> None:
    """
    The payment of `order` commits each on-order line against its offer, under
    the offer's row lock. Writes no stock movement. Idempotent: a line already
    decided (pending, to_confirm, received, failed) is not looked at again.

    `strict` (the till): nothing is captured yet, so an offer that cannot take
    the line refuses the sale. Otherwise (web) the money is in: the line is
    marked `to_confirm`, the order says so, and it is audited.
    """
    from .inventory_services import InvalidMovementError

    lines = list(
        OrderItem.objects.filter(order=order, supply_origin=OrderItem.SUPPLY_SUPPLIER, supply_status='')
        .select_related('product').order_by('supplier_offer_id', 'pk')
    )
    for line in lines:
        offer = None
        if line.supplier_offer_id:
            offer = (SupplierOffer.objects.select_for_update().select_related('supplier')
                     .filter(pk=line.supplier_offer_id, company_id=order.company_id).first())
        # The product must still be sold «Bajo pedido» when the money arrives: a
        # mode switched off since checkout is a line for a person, not a commitment.
        if offer is not None and sells_on_order(line.product) and _offer_is_valid(offer, line.quantity):
            SupplierOffer.objects.filter(pk=offer.pk).update(
                committed_quantity=F('committed_quantity') + line.quantity, updated_at=timezone.now())
            OrderItem.objects.filter(pk=line.pk).update(supply_status=OrderItem.SUPPLY_PENDING)
            if line.product.condition_line == Product.CONDITION_USED:
                from . import external_used

                external_used.reserve_for_line(offer, line)      # same lock: one card, one sale
            continue
        if strict:
            raise InvalidMovementError(
                f'La oferta del proveedor para «{line.product.name}» ya no cubre {line.quantity}: '
                'no se puede vender bajo pedido.')
        OrderItem.objects.filter(pk=line.pk).update(
            supply_status=OrderItem.SUPPLY_TO_CONFIRM,
            supply_note='La oferta venció o se agotó antes del pago: confirmar con el proveedor o reembolsar.')
        _note(order, f'Abastecimiento por confirmar: «{line.product.name}» (oferta vencida o agotada al pagar).')
        _audit('order_supply_to_confirm', company=order.company, actor=None, target_type='order',
               target_id=order.pk, line_id=line.pk, offer_id=line.supplier_offer_id, quantity=line.quantity)


#: An order that was paid and then refunded or cancelled still holds what it
#: committed until somebody fails its lines: «No se pudo abastecer» works on them.
_RELEASABLE = (Order.Status.PAID, Order.Status.REFUNDED, Order.Status.CANCELLED)


def _locked_line(line, *, company=None, statuses=(Order.Status.PAID,)):
    order = Order.objects.select_for_update().get(pk=line.order_id)
    if company is not None and order.company_id != company.pk:
        raise SupplyError('Pedido no encontrado.', code='not_found')
    locked = OrderItem.objects.select_for_update().select_related('product').get(pk=line.pk)
    if locked.supply_origin != OrderItem.SUPPLY_SUPPLIER:
        raise SupplyError('Esta línea no es bajo pedido.', code='not_on_order')
    if order.status not in statuses:
        raise SupplyError('Sólo un pedido pagado sigue su abastecimiento.', code='not_paid')
    return order, locked


@transaction.atomic
def confirm_line(line, *, actor, offer_id=None, company=None, request=None) -> OrderItem:
    """
    A line «por confirmar»: staff spoke with a supplier. It is committed against
    `offer_id` (or the best valid offer), which then becomes its offer, lead
    time and cost. What the buyer paid does not change.
    """
    order, locked = _locked_line(line, company=company)
    if locked.supply_status != OrderItem.SUPPLY_TO_CONFIRM:
        raise SupplyError('Esta línea no está por confirmar.', code='wrong_state')
    if offer_id not in (None, ''):
        offer = SupplierOffer.objects.filter(company_id=order.company_id, product_id=locked.product_id,
                                             pk=_pk(offer_id)).first()
    else:
        offer = best_offer(locked.product, locked.quantity)
    if offer is None:
        raise SupplyError('No hay una oferta vigente que cubra esta línea.', code='no_offer')
    offer = SupplierOffer.objects.select_for_update().select_related('supplier').get(pk=offer.pk)
    if not _offer_is_valid(offer, locked.quantity):
        raise SupplyError('Esa oferta no está vigente o no cubre esta línea.', code='no_offer')
    SupplierOffer.objects.filter(pk=offer.pk).update(
        committed_quantity=F('committed_quantity') + locked.quantity, updated_at=timezone.now())
    if locked.product.condition_line == Product.CONDITION_USED:
        from . import external_used

        external_used.reserve_for_line(offer, locked)
    snapshot = line_snapshot(offer)
    snapshot.pop('supply_origin')
    OrderItem.objects.filter(pk=locked.pk).update(supply_status=OrderItem.SUPPLY_PENDING, supply_note='', **snapshot)
    _audit('order_supply_confirmed', company=order.company, actor=actor, target_type='order', target_id=order.pk,
           request=request, line_id=locked.pk, offer_id=offer.pk, previous_offer_id=locked.supplier_offer_id)
    locked.refresh_from_db()
    return locked


def _received_units(order, line) -> list[dict]:
    """The devices a reception of `line` named, as staff read them (whole identifiers)."""
    from .models import OrderUnit

    return [
        {'serial_number': row.unit.serial_number or '', 'imei': row.unit.imei or '', 'imei2': row.unit.imei2 or ''}
        for row in OrderUnit.objects.filter(order=order, product_id=line.product_id)
        .select_related('unit').order_by('identified_at', 'pk')
    ]


def receive_line(line, *, actor, units=None, unit_cost=None, company=None, request=None) -> OrderItem:
    """
    INV-USED-EXT-01 (owner D4): a line whose devices are external cards must
    receive THOSE devices. Another one writes nothing (`_receive_line` rolls
    back) and opens an incident in its own transaction; the caller is told
    `identity_mismatch` with the incident. See `_receive_line` for the rest.
    """
    from . import external_used

    try:
        return _receive_line(line, actor=actor, units=units, unit_cost=unit_cost, company=company, request=request)
    except external_used.IdentityMismatch as exc:
        incident = external_used.open_incident(line=line, actor=actor, card_ids=exc.card_ids,
                                               received=exc.received, request=request)
        if incident is None:
            # Another reception settled the line in between: say what it is now, as a repeated one would.
            current = OrderItem.objects.select_related('order').get(pk=line.pk)
            if current.supply_status == OrderItem.SUPPLY_RECEIVED:
                error = SupplyError('Esta línea ya se recibió del proveedor.', code='already_received')
                error.units = _received_units(current.order, current)
                raise error from None
            raise SupplyError('Sólo se recibe una línea pedida al proveedor y aún no recibida.',
                              code='wrong_state') from None
        error = SupplyError(str(exc), code='identity_mismatch')
        error.incident_id = incident.pk
        raise error from None


@transaction.atomic
def _receive_line(line, *, actor, units=None, unit_cost=None, company=None, request=None) -> OrderItem:
    """
    «Recibir del proveedor»: the goods of a committed line arrive, and leave
    with the order in the same transaction — an entry and the sale exit of
    this order: the shelf ends where it was, the Kardex tells both. A
    serialized product arrives as REAL devices (`units`: one row each, exactly
    the line's quantity, under `stock_unit_services`' rule — serial always,
    IMEI when the product requires it, an optional `notes`), and those devices
    are the ones sold to this order.

    COST (SUPPLIER-DEVICE-FLOW-02). Two figures, never mixed:
      * the SALE keeps its snapshot — `OrderItem.unit_cost`, the agreed cost
        frozen at checkout with `cost_source='supplier_offer'` — never
        rewritten here; the ESTIMATED margin and the supplier settlement keep
        reading it;
      * the GOODS enter at the REAL cost when somebody with `costs.view`
        states it (`unit_cost` for the line, or a device's own `cost`), else at
        the agreed one. That is what the Kardex, the branch average and each
        device carry. The line freezes it as `received_unit_cost`
        (`received_cost_source='real'`) — the cost of the REALISED margin
        (owner's decision D2) — ONLY when it was stated for every unit;
        otherwise the source is 'agreed' and the real cost stays pending
        («si falta costo real → pendiente») until `record_real_cost`. The
        estimated margin keeps the agreed snapshot.

    ONCE. A line already received answers `already_received` (the view: 409,
    with the devices it named), under the order's lock: two receptions at the
    same instant queue and the second one is told. An order with a standing
    return answers `return_open` (409) and nothing moves.
    """
    from . import external_used, inventory_services, stock_unit_services, unit_identification
    from .models import OrderUnit

    order, locked = _locked_line(line, company=company)
    if locked.supply_status == OrderItem.SUPPLY_RECEIVED:
        error = SupplyError('Esta línea ya se recibió del proveedor.', code='already_received')
        error.units = _received_units(order, locked)
        raise error
    if locked.supply_status != OrderItem.SUPPLY_PENDING:
        raise SupplyError('Sólo se recibe una línea pedida al proveedor y aún no recibida.', code='wrong_state')
    # ORDER-RETURN-01: while a return stands (a till sale is open to one from
    # birth) the order's goods are coming back; receiving would sell goods out
    # to it again — the «second exit» the close never puts back. Same rule and
    # code as identifying a device (`unit_identification.returning`).
    if unit_identification.returning(order):
        raise SupplyError('Este pedido tiene una devolución: no se recibe mercadería para entregarlo. '
                          'Revisa la devolución desde «Devolución».', code='return_open')
    branch = order.fulfillment_branch
    if branch is None:
        raise SupplyError('El pedido no tiene sucursal de despacho.', code='no_branch')
    product = locked.product
    # Only `costs.view` states a real cost (DC1); from anybody else a sent
    # value is ignored, as the weekly Excel ignores «Costo pactado».
    may_state_cost = _may_view_costs(actor, order.company)
    real = clean_money(unit_cost, 'unit_cost', 'el costo real') if may_state_cost else None
    entry_cost = real if real is not None else locked.unit_cost
    stated = real is not None           # a real cost said for the whole line
    stated_units = 0                    # devices that brought their own
    received_cost = entry_cost
    reason = f'Recibido del proveedor para el pedido #{order.pk}'
    metadata = {'order_id': order.pk, 'line_id': locked.pk, 'supplier_offer_id': locked.supplier_offer_id}
    try:
        if product.is_serialized:
            rows = list(units or [])
            if len(rows) != locked.quantity:
                raise SupplyError(f'Indica los {locked.quantity} equipo(s) recibidos, con su número de serie.',
                                  field='units')
            # INV-USED-EXT-01 (D4): an external device must be the one sold — checked before anything is written.
            cards = external_used.check_reception(locked, rows)
            line_cost = str(entry_cost) if entry_cost is not None else None

            def device(row):
                nonlocal stated_units
                row = dict(row) if isinstance(row, dict) else {}
                # A device's own cost only from `costs.view`; otherwise the line's.
                if may_state_cost and row.get('cost') not in (None, ''):
                    stated_units += 1
                else:
                    row['cost'] = line_cost
                return row

            created = stock_unit_services.receive_units(
                branch=branch, product=product, actor=actor, reason=reason, request=request,
                units=[device(row) for row in rows],
            )
            inventory_services.create_stock_movement(
                branch=branch, product_id=product.pk, movement_type=StockMovement.SALE_EXIT,
                quantity=len(created), reason=f'Venta confirmada — orden #{order.pk} (bajo pedido)', actor=actor,
                order=order, reference_type='order', reference_id=order.pk, metadata=metadata,
                units=created, expected_unit_status='available', sell_units=True,
            )
            # The line's real cost: the mean of what its devices entered at
            # (unknown if any of them has no cost).
            costs = [unit.cost for unit in created]
            received_cost = (None if any(cost is None for cost in costs)
                             else sum(costs, Decimal('0')) / len(costs))
            if cards:
                external_used.mark_received(cards, created)
            for unit in created:
                if not OrderUnit.objects.filter(order=order, unit=unit).exists():
                    unit_identification.record(order=order, unit=unit, method=unit_identification.MANUAL,
                                               actor=actor, request=request)
        else:
            if units:
                raise SupplyError(f'«{product.name}» no se controla por número de serie.', field='units')
            inventory_services.create_stock_movement(
                branch=branch, product_id=product.pk, movement_type=StockMovement.PURCHASE_ENTRY,
                quantity=locked.quantity, reason=reason, actor=actor, order=order,
                reference_type='supplier_order', reference_id=order.pk, metadata=metadata,
                unit_cost=entry_cost,
            )
            inventory_services.create_stock_movement(
                branch=branch, product_id=product.pk, movement_type=StockMovement.SALE_EXIT,
                quantity=locked.quantity, reason=f'Venta confirmada — orden #{order.pk} (bajo pedido)',
                actor=actor, order=order, reference_type='order', reference_id=order.pk, metadata=metadata,
            )
    except stock_unit_services.StockUnitError as exc:
        raise SupplyError(str(exc), field='units', line=exc.line, unit_field=exc.field or '') from None
    except inventory_services.InventoryError as exc:
        raise SupplyError(str(exc)) from None
    from .cost_services import quantize_cost

    # SUPPLIER-MARGIN (D2): the real cost is frozen on the line, here, under the
    # order's lock and once — the sale's agreed `unit_cost` is not touched.
    # It is REAL only when somebody with costs.view STATED it for every unit
    # (the line's cost, or each device's own). An empty field is not a
    # confirmation of the agreed cost (owner's rule D2): the goods entered at
    # the agreed cost, nobody said the real one — «costo real pendiente» until
    # `record_real_cost`.
    if product.is_serialized and stated_units == locked.quantity:
        stated = True
    now = timezone.now()
    real_known = stated and received_cost is not None
    OrderItem.objects.filter(pk=locked.pk).update(
        supply_status=OrderItem.SUPPLY_RECEIVED,
        received_unit_cost=quantize_cost(received_cost) if real_known else None,
        received_cost_source=(OrderItem.RECEIVED_COST_REAL if real_known
                              else OrderItem.RECEIVED_COST_AGREED if received_cost is not None else ''),
        received_at=now,
        received_cost_at=now if real_known else None,
    )
    # Whether a real cost was stated, never the amount: the log is read by more people.
    _audit('order_supply_received', company=order.company, actor=actor, target_type='order', target_id=order.pk,
           request=request, line_id=locked.pk, quantity=locked.quantity, offer_id=locked.supplier_offer_id,
           real_cost_stated=stated)
    locked.refresh_from_db()
    return locked


@transaction.atomic
def record_real_cost(line, *, actor, unit_cost, company=None, request=None) -> OrderItem:
    """
    «Registrar costo real» (owner's decision D2): a received on-order line whose
    real cost nobody recorded (received without costs.view, or before the
    snapshot existed) gets it now — under the order's lock, once.

    Only the line's `received_unit_cost` (and its source and time) is written:
    NOT the Kardex, NOT the branch average, NOT the devices — correcting what
    the goods entered at is a separate, pending phase. The same value twice
    answers the line again without a second audit line; another value over a
    real cost already recorded is refused (`already_real`), never overwritten.
    The audit line says that it was recorded and over which source; never the
    amount (the log is read by more people than costs.view).

    The caller checks the authority (costs.view + what receiving takes).
    """
    from .cost_services import quantize_cost

    order, locked = _locked_line(line, company=company)
    if locked.supply_status != OrderItem.SUPPLY_RECEIVED:
        raise SupplyError('Sólo se registra el costo real de una línea ya recibida.', code='not_received')
    amount = clean_money(unit_cost, 'unit_cost', 'el costo real', required=True, positive=True)
    amount = quantize_cost(amount)
    if locked.received_cost_source == OrderItem.RECEIVED_COST_REAL and locked.received_unit_cost is not None:
        if locked.received_unit_cost == amount:
            return locked
        raise SupplyError('Esta línea ya tiene su costo real registrado; no se sobrescribe.', code='already_real')
    previous = locked.received_cost_source
    OrderItem.objects.filter(pk=locked.pk).update(
        received_unit_cost=amount, received_cost_source=OrderItem.RECEIVED_COST_REAL,
        received_cost_at=timezone.now(),
    )
    _audit('order_supply_real_cost_recorded', company=order.company, actor=actor, target_type='order',
           target_id=order.pk, request=request, line_id=locked.pk, previous_source=previous or 'none',
           kardex_untouched=True)
    locked.refresh_from_db()
    return locked


def _may_view_costs(actor, company) -> bool:
    from .cost_services import user_may_view_costs

    return user_may_view_costs(actor, company)


def line_costs(line) -> dict:
    """
    What an on-order line cost, for `costs.view` ONLY: the agreed cost frozen
    on the sale, the real one frozen at reception (None = «costo real
    pendiente»), where that came from, and real − agreed per unit.
    """
    agreed = line.unit_cost if line.supply_origin == OrderItem.SUPPLY_SUPPLIER else None
    real = line.received_unit_cost if line.supply_origin == OrderItem.SUPPLY_SUPPLIER else None
    difference = real - agreed if real is not None and agreed is not None else None
    return {
        'agreed_unit_cost': None if agreed is None else str(agreed),
        'received_unit_cost': None if real is None else str(real),
        'received_cost_source': line.received_cost_source if line.supply_origin == OrderItem.SUPPLY_SUPPLIER else '',
        'unit_cost_difference': None if difference is None else str(difference),
    }


FAIL_CAUSES = ('supplier_failure', 'other')


@transaction.atomic
def fail_line(line, *, actor, reason, company=None, request=None, cause=None) -> OrderItem:
    """
    «No se pudo abastecer»: the line will not be supplied. The order says it
    has to be refunded or cancelled through the existing flow, and nothing
    touches stock.

    OWNER D3 (2026-10-21) — WHO FAILED DECIDES WHAT THE OFFER GETS BACK.
      * On a PAID order this is a SUPPLIER FAILURE: the line stops counting as
        committed AND the same units leave the confirmed quantity, so what is
        left to sell (confirmed − committed, D1) stays exactly where it was —
        a supply failure is not a stock confirmation.
      * On an order already REFUNDED or CANCELLED by another road it is an
        ordinary release: the supplier still has the units, the offer gets
        back what the line committed. ORDER-CANCEL-01 releases its own lines
        the same ordinary way (`order_cancellation._release_supply`).
    `cause` on a paid order (D3 review P3): `supplier_failure` (the default)
    or `other` — the shop decided not to supply although the supplier has
    the units: an ordinary release.
    Either way never more than the offer holds (never below zero, confirmed
    never below committed), once (the line becomes `failed`), audited with
    the cause and what was left before and after.
    """
    reason = ' '.join(str(reason or '').split())[:250]
    if not reason:
        raise SupplyError('Indica el motivo.', field='reason')
    if cause not in (None, '', *FAIL_CAUSES):
        raise SupplyError('Causa no válida: «supplier_failure» u «other».', field='cause')
    order, locked = _locked_line(line, company=company, statuses=_RELEASABLE)
    if locked.supply_status not in (OrderItem.SUPPLY_PENDING, OrderItem.SUPPLY_TO_CONFIRM):
        raise SupplyError('Esta línea ya no está esperando al proveedor.', code='wrong_state')
    released = confirmed_released = 0
    available_before = available_after = None
    cause = (cause or 'supplier_failure') if order.status == Order.Status.PAID else 'cancellation'
    if locked.supply_status == OrderItem.SUPPLY_PENDING and locked.supplier_offer_id:
        offer = SupplierOffer.objects.select_for_update().filter(pk=locked.supplier_offer_id).first()
        if offer is not None:
            available_before = offer.remaining_known
            released = min(offer.committed_quantity, locked.quantity)
            if released:
                changes = {'committed_quantity': F('committed_quantity') - released}
                if cause == 'supplier_failure':
                    # D3: what the supplier failed leaves the confirmed quantity too (committed > 0 means
                    # confirmed >= committed >= released: never negative, never below committed).
                    changes['confirmed_quantity'] = F('confirmed_quantity') - released
                    confirmed_released = released
                SupplierOffer.objects.filter(pk=offer.pk).update(updated_at=timezone.now(), **changes)
            offer.refresh_from_db()
            available_after = offer.remaining_known
            from . import external_used

            external_used.release_cards(locked, flat=cause == 'supplier_failure')
    OrderItem.objects.filter(pk=locked.pk).update(supply_status=OrderItem.SUPPLY_FAILED, supply_note=reason)
    if order.status == Order.Status.PAID:
        _note(order, f'No se pudo abastecer «{locked.product.name}»: {reason}. '
                     'Cancelar o reembolsar por el flujo existente.')
    else:
        _note(order, f'Abastecimiento liberado de «{locked.product.name}» (pedido {order.get_status_display().lower()}): '
                     f'{reason}.')
    Order.objects.filter(pk=order.pk).update(payment_error=order.payment_error)
    _audit('order_supply_failed', company=order.company, actor=actor, target_type='order', target_id=order.pk,
           request=request, line_id=locked.pk, reason=reason, released=released, order_status=order.status,
           cause=cause, confirmed_released=confirmed_released, available_before=available_before,
           available_after=available_after)
    locked.refresh_from_db()
    return locked


def order_supply_status(order) -> str:
    """The order's supply state, derived from its lines: '' when nothing is on order."""
    states = set(order.items.filter(supply_origin=OrderItem.SUPPLY_SUPPLIER).values_list('supply_status', flat=True))
    if not states:
        return ''
    for state in (OrderItem.SUPPLY_TO_CONFIRM, OrderItem.SUPPLY_FAILED, OrderItem.SUPPLY_PENDING, ''):
        if state in states:
            return state or 'awaiting_payment'
    return OrderItem.SUPPLY_RECEIVED


def pending_settlement(company, branch_ids=None) -> list[dict]:
    """
    PAYABLES-01 hook, READ ONLY: per supplier, what was received on order and
    is still to be settled, at the frozen cost. No debt is written anywhere;
    this is a sum over received lines of paid orders. Lines of unknown cost
    are counted apart, never valued at zero.

    `branch_ids`: only orders fulfilled by those branches — the caller's
    visible ones (integration review of PR #123, P3). None = the company.
    """
    # FINANCIAL-CLOSURE: goods RECEIVED from the supplier are owed to it even
    # when the customer's order was cancelled afterwards (they came back to
    # our own shelf at their cost); only a line never received is not owed.
    lines = OrderItem.objects.filter(
        order__company=company, order__status__in=(Order.Status.PAID, Order.Status.CANCELLED),
        supply_origin=OrderItem.SUPPLY_SUPPLIER, supply_status=OrderItem.SUPPLY_RECEIVED,
        supplier_offer__isnull=False,
    )
    if branch_ids is not None:
        lines = lines.filter(order__fulfillment_branch_id__in=list(branch_ids))
    rows = (
        lines.values('supplier_offer__supplier_id', 'supplier_offer__supplier__name')
        .annotate(
            units=Sum('quantity'),
            amount=Sum(F('unit_cost') * F('quantity'), filter=Q(unit_cost__isnull=False)),
            units_without_cost=Sum('quantity', filter=Q(unit_cost__isnull=True)),
        )
        .order_by('supplier_offer__supplier__name')
    )
    return [
        {'supplier_id': row['supplier_offer__supplier_id'], 'supplier_name': row['supplier_offer__supplier__name'],
         'units': row['units'] or 0,
         'amount': str((row['amount'] or Decimal('0')).quantize(Decimal('0.01'))),
         'units_without_cost': row['units_without_cost'] or 0}
        for row in rows
    ]
