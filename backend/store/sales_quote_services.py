"""
SALES-QUOTE-01 — commercial quotes: compose, revise, duplicate, approve, send, record the
customer's answer, expire, and convert into a counter sale.

WHAT A QUOTE NEVER DOES
-----------------------
It never charges, never writes a Kardex line, never reserves a device and never numbers like a
fiscal document. Every operation here writes `SalesQuote`, `SalesQuoteItem`, its sequence and
`AdminAuditLog` — nothing else. CONVERSION is the one exception, and it is not a quote operation:
it is a counter sale (`pos_services.create_pos_sale`), with the till's own permissions, stock
rules, idempotency and documents. The quote only says what was offered and is linked to the sale
(`Order.source_quote`); it stays as it was.

ONE PRICING CORE
----------------
A quote is priced by `pos_services.build_pos_sale` — the function the till's preview and sale use —
with the quote's branch, customer, seller and manual discount. So its subtotal, automatic
promotion, discount and IGV breakdown are, cent for cent, what a sale of that basket would show
at that moment. There is no second formula to drift.

PRICES MOVE; A QUOTE DOES NOT
-----------------------------
The totals are frozen when the draft is composed. Approving re-prices and refuses a stale draft
(the approver approves today's numbers). Converting re-prices again: a different total is a 409
with both totals, and the sale goes ahead only when somebody decides — charge today's price
(`price_decision='current'`, with the total they read), or keep the quoted one
(`price_decision='quoted'`, a manual discount, so `sales.discounts.apply`).
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from . import pos_services
from .models import (
    AdminAuditLog,
    DiscountSource,
    Order,
    PaymentMethod,
    ProductBarcode,
    SalesQuote,
    SalesQuoteItem,
    StockUnit,
)
from .tenancy import has_capability

CAP_VIEW = 'sales.quotes.view'
CAP_MANAGE = 'sales.quotes.manage'
CAP_APPROVE = 'sales.quotes.approve'
CAP_POS = 'sales.pos.use'
CAP_DISCOUNTS = 'sales.discounts.apply'
CAP_ASSIGN_SELLER = 'sales.pos.assign_seller'
CAP_ORDERS_MANAGE = 'sales.orders.manage'

#: Owner's default validity when the composer names none (decision to confirm).
DEFAULT_VALIDITY_DAYS = 7
MAX_VALIDITY_DAYS = 365
MAX_TEXT = 2000
MAX_NOTE = 300
MAX_CUSTOMER_NAME = 255
#: A device is NAMED for pricing, never identified: the till identifies it when it is sold.
_NAMED = 'manual'


class QuoteError(Exception):
    """The request cannot be honoured. HTTP 400 unless a subclass says otherwise."""

    status = 400
    code = 'invalid'

    def __init__(self, message: str, *, code: str | None = None, extra: dict | None = None):
        super().__init__(message)
        if code:
            self.code = code
        self.extra = extra or {}


class QuotePermissionError(QuoteError):
    status = 403
    code = 'forbidden'


class QuoteStateError(QuoteError):
    """The quote is not in a state that allows this (409)."""

    status = 409
    code = 'invalid_state'


class QuoteConflict(QuoteError):
    status = 409
    code = 'conflict'


class QuotePriceChanged(QuoteError):
    """The total priced now is not the one expected. Nothing was written (409 `price_changed`)."""

    status = 409
    code = 'price_changed'

    def __init__(self, message: str, *, expected: Decimal, current: Decimal, priced: dict,
                 can_keep_quoted: bool = False):
        super().__init__(message)
        self.expected, self.current, self.priced = expected, current, priced
        self.can_keep_quoted = can_keep_quoted


def _wrap_pos(exc: Exception) -> QuoteError:
    """A till refusal, in the quote's vocabulary (status and code kept)."""
    if isinstance(exc, pos_services.PosPermissionError):
        return QuotePermissionError(str(exc))
    code = getattr(exc, 'code', None)
    if code:
        # DC3 `stock_available`, USED-READY `unit_not_inspected`: refusals the till answers 409.
        return QuoteConflict(str(exc), code=code)
    return QuoteError(str(exc))


def _audit(*, actor, company, action: str, quote, request=None, **metadata):
    AdminAuditLog.log(
        actor=actor if getattr(actor, 'is_authenticated', False) else None,
        action=action, target_type='sales_quote', target_id=quote.pk,
        metadata={'company_id': company.pk, 'quote_id': quote.pk, 'number': quote.number,
                  'revision': quote.revision, 'status': quote.status, **metadata},
        request=request, company=company,
    )


# ---------------------------------------------------------------------------
# USED-READY adapter
# ---------------------------------------------------------------------------

def used_unit_till_refusal(unit) -> str:
    """
    USED-READY-01 (PR #403): '' when a used device may be sold at the till, else the sentence.

    A SMALL ADAPTER, on purpose: `used_unit_services` lands with that PR. Until then there is no
    inspection rule to apply and this answers ''; once it is on `master` the same call applies it,
    to quoting and to converting alike — and the till (`pos_services.unit_prices`) applies it again
    when the sale is written.
    """
    try:
        from . import used_unit_services  # noqa: PLC0415 — optional until USED-READY-01 is merged
    except ImportError:
        return ''
    refusal = getattr(used_unit_services, 'till_refusal', None)
    return refusal(unit) if refusal is not None else ''


# ---------------------------------------------------------------------------
# Request shaping
# ---------------------------------------------------------------------------

def _text(value, limit: int, label: str) -> str:
    text = str(value or '').strip()
    if len(text) > limit:
        raise QuoteError(f'{label} admite {limit} caracteres.')
    return text


def _validity(raw, *, today: date | None = None) -> date:
    today = today or timezone.localdate()
    if raw in (None, ''):
        return today + timedelta(days=DEFAULT_VALIDITY_DAYS)
    try:
        value = raw if isinstance(raw, date) else date.fromisoformat(str(raw)[:10])
    except ValueError:
        raise QuoteError('Vigencia no válida. Usa el formato AAAA-MM-DD.') from None
    if value < today:
        raise QuoteError('La vigencia no puede terminar antes de hoy.')
    if value > today + timedelta(days=MAX_VALIDITY_DAYS):
        raise QuoteError(f'La vigencia máxima es de {MAX_VALIDITY_DAYS} días.')
    return value


def _discount_spec(data) -> tuple[str, object, str]:
    kind = str(data.get('discount_type') or '').strip()
    value = data.get('discount_value')
    reason = str(data.get('discount_reason') or '')
    if not kind and value in (None, ''):
        return '', None, ''
    return kind, value, reason


def _raw_lines(raw) -> list[dict]:
    """The composer's lines: `{product, quantity, on_order?, supply_reason?, unit?}`. Prices are never read."""
    if not isinstance(raw, (list, tuple)) or not raw:
        raise QuoteError('Agrega al menos un producto a la cotización.')
    if len(raw) > pos_services.MAX_LINES:
        raise QuoteError(f'Demasiadas líneas (máximo {pos_services.MAX_LINES}).')
    lines = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise QuoteError('Formato de línea inválido.')
        unit = entry.get('unit')
        if unit in ('', None):
            unit = None
        elif isinstance(unit, bool) or not isinstance(unit, int) or unit <= 0:
            raise QuoteError('Equipo inválido.')
        lines.append({
            'product': entry.get('product'),
            'quantity': entry.get('quantity', 1),
            'on_order': entry.get('on_order') in (True, 'true', '1', 1),
            'supply_reason': entry.get('supply_reason') or '',
            'unit': unit,
        })
    return lines


def _pos_items(lines: list[dict]) -> list[dict]:
    """The till's basket for these lines (devices NAMED, so the till prices a used one by itself)."""
    return [{
        'product': line['product'], 'quantity': line['quantity'],
        **({'on_order': True} if line['on_order'] else {}),
        **({'supply_reason': line['supply_reason']} if line['on_order'] and line['supply_reason'] else {}),
        **({'units': [{'id': line['unit'], 'method': _NAMED}]} if line['unit'] else {}),
    } for line in lines]


def _check_units(company, branch, lines: list[dict], products: dict) -> dict[int, StockUnit]:
    """
    A used device is quoted by the device (its own price), one line each; nothing else names one.
    The device must be of this company and product; whether it is still on this shelf, available and
    priced is the till's own check (`unit_prices`), run while pricing.
    """
    named = [line['unit'] for line in lines if line['unit']]
    if len(named) != len(set(named)):
        raise QuoteError('El mismo equipo está dos veces en la cotización.')
    units = {u.pk: u for u in StockUnit.objects.filter(company=company, pk__in=named).select_related('product')}
    for line in lines:
        product = products[int(line['product'])]
        by_unit = pos_services.priced_by_unit(product, {'on_order': line['on_order']})
        if line['unit'] is None:
            if by_unit:
                raise QuoteError(f'«{product.name}» se cotiza por equipo: elige el seminuevo que ofreces.')
            continue
        if not by_unit:
            raise QuoteError(f'«{product.name}» se cotiza por producto: el equipo se identifica al vender.')
        if int(line['quantity']) != 1:
            raise QuoteError(f'«{product.name}»: un seminuevo es una línea por equipo (cantidad 1).')
        unit = units.get(line['unit'])
        if unit is None or unit.product_id != product.pk:
            raise QuoteError(f'«{product.name}»: ese equipo no existe en esta empresa.')
        refusal = used_unit_till_refusal(unit)
        if refusal:
            raise QuoteConflict(f'«{product.name}»: {refusal}', code='unit_not_inspected')
    return units


def price_lines(*, actor, company, branch, lines, customer_id=None, seller_id=None,
                discount_type='', discount_value=None, discount_reason='',
                may_assign_seller=False, may_apply_manual_discount=False, may_order_over_stock=False) -> dict:
    """
    Price `lines` exactly as the till would now (`build_pos_sale`, no unit identification, no cash),
    writing nothing. Raises `QuoteError` with the till's own words when the till would refuse.
    """
    try:
        products = pos_services.resolve_pos_products(company, pos_services.normalize_items(_pos_items(lines)))
        _check_units(company, branch, lines, products)
        return pos_services.build_pos_sale(
            operator=actor, company=company, branch=branch, items=_pos_items(lines),
            customer=customer_id, seller_id=seller_id, payment_method=PaymentMethod.CASH,
            manual_discount_type=discount_type, manual_discount_value=discount_value,
            discount_reason=discount_reason,
            may_assign_seller=may_assign_seller, may_apply_manual_discount=may_apply_manual_discount,
            may_order_over_stock=may_order_over_stock, validate_cash=False, require_units=False,
        )
    except pos_services.PosError as exc:
        raise _wrap_pos(exc) from None


def _codes(company, product_ids) -> dict[int, str]:
    codes: dict[int, str] = {}
    for product_id, code in ProductBarcode.objects.filter(
        company=company, product_id__in=list(product_ids), is_active=True,
    ).order_by('is_primary', '-code').values_list('product_id', 'code'):
        codes[product_id] = code          # the primary one is written last and wins
    return codes


def _write_lines(quote, priced: dict, lines: list[dict]) -> None:
    """The quote's lines, in the till's order: one per product, one per device priced on its own."""
    from .order_line_snapshot import fields_for_lines

    by_product = {int(line['product']): line for line in lines}
    pairs = list(zip(priced['lines'], priced['line_units']))
    snapshots = fields_for_lines([(product, unit) for (product, _q, _p), unit in pairs])
    codes = _codes(quote.company, {product.pk for (product, _q, _p), _u in pairs})
    rows = []
    for order, (((product, quantity, unit_price), unit), snapshot) in enumerate(zip(pairs, snapshots)):
        line = by_product.get(product.pk, {})
        on_order = bool(line.get('on_order'))
        priced_by = (SalesQuoteItem.PRICED_BY_UNIT if unit is not None
                     else SalesQuoteItem.PRICED_BY_OFFER if product.pk in priced['offers']
                     else SalesQuoteItem.PRICED_BY_PRODUCT)
        rows.append(SalesQuoteItem(
            quote=quote, product=product, stock_unit=unit, on_order=on_order,
            supply_reason=(line.get('supply_reason') or '')[:200] if on_order else '',
            priced_by=priced_by,
            description=(snapshot['description_snapshot'] or product.name or '')[:300],
            variant_snapshot=snapshot['variant_snapshot'] or {},
            code=codes.get(product.pk, '')[:64],
            quantity=quantity, unit_price=unit_price,
            line_total=pos_services._money(unit_price * quantity), sort_order=order,
        ))
    SalesQuoteItem.objects.bulk_create(rows)


def _money_fields(priced: dict, *, discount_type, discount_value, discount_reason, actor) -> dict:
    tax = priced['tax']
    discount = priced['discount']
    manual = discount['source'] == DiscountSource.MANUAL
    value = None
    if manual:
        try:
            value = Decimal(str(discount_value)).quantize(Decimal('0.01'))
        except (InvalidOperation, TypeError, ValueError):
            value = None
    return {
        'currency': tax.currency, 'subtotal': priced['subtotal'], 'discount_amount': priced['discount_amount'],
        'discount_source': discount['source'],
        'discount_type': discount_type if manual else '',
        'discount_value': value,
        'discount_reason': discount['reason'] if manual else '',
        'discount_authorized_by': actor if manual and getattr(actor, 'is_authenticated', False) else None,
        'taxable_amount': tax.taxable_amount, 'tax_amount': tax.tax_amount, 'tax_rate': tax.tax_rate,
        'tax_treatment': tax.tax_treatment, 'total': priced['total'],
    }


def _customer_fields(company, data, customer) -> dict:
    if customer is not None:
        return {
            'customer': customer, 'customer_name': (customer.display_name or '')[:MAX_CUSTOMER_NAME],
            'customer_document_type': customer.document_type or '',
            'customer_document_number': customer.document_number or '',
            'customer_phone': customer.phone or '', 'customer_email': customer.email or '',
        }
    from django.core.exceptions import ValidationError
    from django.core.validators import validate_email

    email = _text(data.get('customer_email'), 254, 'El correo del cliente')
    if email:
        try:
            validate_email(email)
        except ValidationError:
            raise QuoteError('El correo del cliente no es válido.') from None
    document_type = _text(data.get('customer_document_type'), 10, 'El tipo de documento').lower()
    if document_type and document_type not in ('dni', 'ruc', 'ce', 'passport', 'other'):
        raise QuoteError('Tipo de documento no reconocido.')
    return {
        'customer': None,
        'customer_name': _text(data.get('customer_name'), MAX_CUSTOMER_NAME, 'El nombre del cliente'),
        'customer_document_type': document_type,
        'customer_document_number': _text(data.get('customer_document_number'), 20, 'El documento del cliente'),
        'customer_phone': _text(data.get('customer_phone'), 30, 'El teléfono del cliente'),
        'customer_email': email,
    }


def _compose(*, actor, company, data, branch=None, previous=None) -> dict:
    """
    Everything a draft is made of, priced, from a request body. Writes nothing.

    `previous` (the quote being edited or revised): its seller and its manual discount were decided by
    somebody allowed to when it was composed. Kept as they are, they need no authority again; changed,
    they need the caller's own (`sales.pos.assign_seller`, `sales.discounts.apply`).
    """
    if branch is None:
        try:
            branch = pos_services.resolve_pos_branch(actor, company, data.get('branch'))
        except pos_services.PosValidationError as exc:
            raise QuoteError(str(exc)) from None
    try:
        customer = pos_services.resolve_pos_customer(company, data.get('customer'))
    except pos_services.PosValidationError as exc:
        raise QuoteError(str(exc)) from None
    lines = _raw_lines(data.get('items'))
    discount_type, discount_value, discount_reason = _discount_spec(data)
    seller_id = data.get('seller')
    priced = price_lines(
        actor=actor, company=company, branch=branch, lines=lines,
        customer_id=customer.pk if customer else None, seller_id=seller_id,
        discount_type=discount_type, discount_value=discount_value, discount_reason=discount_reason,
        may_assign_seller=(has_capability(actor, company, CAP_ASSIGN_SELLER)
                           or _same_seller(previous, seller_id)),
        may_apply_manual_discount=(has_capability(actor, company, CAP_DISCOUNTS)
                                   or _same_discount_request(previous, discount_type, discount_value,
                                                             discount_reason)),
        may_order_over_stock=has_capability(actor, company, CAP_ORDERS_MANAGE),
    )
    seller = priced['seller'] if getattr(priced['seller'], 'is_authenticated', False) else None
    return {
        'branch': branch, 'lines': lines, 'priced': priced,
        'fields': {
            'branch': branch,
            **_customer_fields(company, data, customer),
            'seller': seller,
            'seller_name_snapshot': pos_services.seller_display_name(seller),
            'valid_until': _validity(data.get('valid_until')),
            'conditions': _text(data.get('conditions'), MAX_TEXT, 'Las condiciones'),
            'notes': _text(data.get('notes'), MAX_TEXT, 'Las notas internas'),
            **_money_fields(priced, discount_type=discount_type, discount_value=discount_value,
                            discount_reason=discount_reason, actor=actor),
        },
    }


def _same_seller(previous, seller_id) -> bool:
    if previous is None or previous.seller_id is None or seller_id in (None, ''):
        return False
    try:
        return int(seller_id) == previous.seller_id
    except (TypeError, ValueError):
        return False


def _same_discount_request(previous, kind, value, reason) -> bool:
    """Whether the manual discount asked for is the one `previous` already carries, unchanged."""
    if previous is None or previous.discount_source != DiscountSource.MANUAL or not kind:
        return False
    try:
        same_value = Decimal(str(value)).quantize(Decimal('0.01')) == previous.discount_value
    except (InvalidOperation, TypeError, ValueError):
        return False
    return (kind == previous.discount_type and same_value
            and ' '.join(str(reason or '').split()) == ' '.join(previous.discount_reason.split()))


def _require(actor, company, capability: str, message: str) -> None:
    if not has_capability(actor, company, capability):
        raise QuotePermissionError(message)


def _locked(quote) -> SalesQuote:
    return SalesQuote.objects.select_for_update().get(pk=quote.pk)


# ---------------------------------------------------------------------------
# Compose
# ---------------------------------------------------------------------------

def create_quote(*, actor, company, data, request=None) -> SalesQuote:
    """A new draft, numbered from the company's `sales_quote` series. Nothing but the quote is written."""
    from . import sequences

    _require(actor, company, CAP_MANAGE, 'No tienes permiso para elaborar cotizaciones.')
    composed = _compose(actor=actor, company=company, data=data)
    with transaction.atomic():
        try:
            value, number = sequences.allocate(sequences.resolve_sequence_for_quote(company))
        except sequences.SequenceError as exc:
            raise QuoteError(str(exc)) from None
        quote = SalesQuote(company=company, number=number, sequence_value=value, revision=1,
                           origin=SalesQuote.ORIGIN_NEW, created_by=actor, updated_by=actor,
                           **composed['fields'])
        quote.save()
        _write_lines(quote, composed['priced'], composed['lines'])
        _audit(actor=actor, company=company, action='sales_quote_created', quote=quote, request=request,
               total=str(quote.total), lines=len(composed['priced']['lines']),
               discount_source=quote.discount_source)
    return quote


def _lines_of(quote) -> list[dict]:
    """A quote's own lines as a composer's request (to re-price, revise or duplicate it)."""
    merged: dict[int, dict] = {}
    out = []
    for item in quote.items.order_by('sort_order', 'pk'):
        if item.stock_unit_id:
            out.append({'product': item.product_id, 'quantity': 1, 'on_order': False, 'supply_reason': '',
                        'unit': item.stock_unit_id})
            continue
        line = merged.get(item.product_id)
        if line is None:
            line = {'product': item.product_id, 'quantity': 0, 'on_order': item.on_order,
                    'supply_reason': item.supply_reason, 'unit': None}
            merged[item.product_id] = line
            out.append(line)
        line['quantity'] += item.quantity
    return out


def _body_of(quote) -> dict:
    """The request that would compose this quote again (for a revision or a copy without changes)."""
    return {
        'branch': quote.branch_id, 'customer': quote.customer_id,
        'customer_name': quote.customer_name, 'customer_document_type': quote.customer_document_type,
        'customer_document_number': quote.customer_document_number, 'customer_phone': quote.customer_phone,
        'customer_email': quote.customer_email, 'seller': quote.seller_id,
        'items': _lines_of(quote),
        'discount_type': quote.discount_type if quote.discount_source == DiscountSource.MANUAL else '',
        'discount_value': quote.discount_value if quote.discount_source == DiscountSource.MANUAL else None,
        'discount_reason': quote.discount_reason if quote.discount_source == DiscountSource.MANUAL else '',
        'valid_until': quote.valid_until.isoformat() if quote.valid_until else None,
        'conditions': quote.conditions, 'notes': quote.notes,
    }


def update_quote(*, actor, company, quote, data, request=None) -> SalesQuote:
    """
    Edit a quote. A DRAFT is edited in place (re-priced whole, lines replaced). Anything already
    offered is never rewritten: a new revision is written and the old one is superseded.
    """
    _require(actor, company, CAP_MANAGE, 'No tienes permiso para elaborar cotizaciones.')
    if quote.status != SalesQuote.STATUS_DRAFT:
        return revise_quote(actor=actor, company=company, quote=quote, data=data, request=request)
    body = {**_body_of(quote), **(data or {})}
    with transaction.atomic():
        locked = _locked(quote)
        if locked.status != SalesQuote.STATUS_DRAFT:
            raise QuoteStateError('La cotización cambió de estado mientras la editabas. Vuelve a abrirla.')
        branch = None if 'branch' in (data or {}) else locked.branch
        composed = _compose(actor=actor, company=company, data=body, branch=branch, previous=locked)
        for name, value in composed['fields'].items():
            setattr(locked, name, value)
        if composed['fields']['discount_source'] != DiscountSource.MANUAL:
            locked.discount_authorized_by = None
        elif _same_manual_discount(quote, composed['fields']):
            locked.discount_authorized_by = quote.discount_authorized_by
        locked.updated_by = actor
        locked.save()
        locked.items.all().delete()
        _write_lines(locked, composed['priced'], composed['lines'])
        _audit(actor=actor, company=company, action='sales_quote_updated', quote=locked, request=request,
               total=str(locked.total))
    return locked


def _same_manual_discount(quote, fields) -> bool:
    return (quote.discount_source == DiscountSource.MANUAL
            and quote.discount_type == fields['discount_type']
            and quote.discount_value == fields['discount_value']
            and quote.discount_reason == fields['discount_reason'])


def revise_quote(*, actor, company, quote, data=None, request=None) -> SalesQuote:
    """Revision N+1 of an offered quote, as a new DRAFT with the same number. N becomes `superseded`."""
    _require(actor, company, CAP_MANAGE, 'No tienes permiso para elaborar cotizaciones.')
    with transaction.atomic():
        locked = _locked(quote)
        if locked.status not in SalesQuote.REVISABLE_STATUSES:
            if locked.status == SalesQuote.STATUS_CONVERTED:
                raise QuoteStateError('Esta cotización ya se convirtió en venta: duplícala para ofrecer de nuevo.')
            if locked.status == SalesQuote.STATUS_SUPERSEDED:
                raise QuoteStateError('Esta revisión ya fue reemplazada: edita la más reciente.')
            raise QuoteStateError('Sólo se revisa una cotización aprobada, enviada o respondida.')
        body = {**_body_of(locked), **(data or {})}
        if 'valid_until' not in (data or {}) and locked.valid_until and locked.valid_until < timezone.localdate():
            body['valid_until'] = None      # an expired offer is renewed with the default validity
        branch = None if 'branch' in (data or {}) else locked.branch
        composed = _compose(actor=actor, company=company, data=body, branch=branch, previous=locked)
        now = timezone.now()
        locked.status = SalesQuote.STATUS_SUPERSEDED
        locked.superseded_at = now
        locked.save(update_fields=['status', 'superseded_at', 'updated_at'])
        fields = composed['fields']
        if fields['discount_source'] == DiscountSource.MANUAL and _same_manual_discount(locked, fields):
            fields = {**fields, 'discount_authorized_by': locked.discount_authorized_by}
        new = SalesQuote(company=company, number=locked.number, sequence_value=locked.sequence_value,
                         revision=locked.revision + 1, origin=SalesQuote.ORIGIN_REVISION, source_quote=locked,
                         created_by=actor, updated_by=actor, **fields)
        new.save()
        _write_lines(new, composed['priced'], composed['lines'])
        _audit(actor=actor, company=company, action='sales_quote_superseded', quote=locked, request=request,
               superseded_by=new.pk)
        _audit(actor=actor, company=company, action='sales_quote_revised', quote=new, request=request,
               previous_id=locked.pk, total=str(new.total))
    return new


def duplicate_quote(*, actor, company, quote, request=None) -> SalesQuote:
    """A NEW quote (new number, revision 1) with the same lines, re-priced today. The original is untouched."""
    _require(actor, company, CAP_MANAGE, 'No tienes permiso para elaborar cotizaciones.')
    body = _body_of(quote)
    body['valid_until'] = None
    body['seller'] = None          # whoever copies it offers it
    copy = create_quote(actor=actor, company=company, data=body, request=None)
    SalesQuote.objects.filter(pk=copy.pk).update(origin=SalesQuote.ORIGIN_DUPLICATE, source_quote=quote)
    copy.refresh_from_db()
    _audit(actor=actor, company=company, action='sales_quote_duplicated', quote=copy, request=request,
           source_id=quote.pk, source_number=quote.label)
    return copy


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

def _not_expired(quote) -> None:
    if quote.valid_until and quote.valid_until < timezone.localdate():
        raise QuoteStateError('La cotización está vencida: revísala para renovar su vigencia.', code='expired')


def approve_quote(*, actor, company, quote, request=None) -> SalesQuote:
    """
    `sales.quotes.approve`: the shop commits to this draft. Re-priced first — an approval is of
    today's numbers, so a draft whose prices moved is refused (409 `price_changed`) until refreshed.
    """
    _require(actor, company, CAP_APPROVE, 'No tienes permiso para aprobar cotizaciones.')
    with transaction.atomic():
        locked = _locked(quote)
        if locked.status != SalesQuote.STATUS_DRAFT:
            raise QuoteStateError('Sólo se aprueba un borrador.')
        _not_expired(locked)
        body = _body_of(locked)
        priced = price_lines(
            actor=actor, company=company, branch=locked.branch, lines=body['items'],
            customer_id=locked.customer_id, seller_id=locked.seller_id,
            discount_type=body['discount_type'], discount_value=body['discount_value'],
            discount_reason=body['discount_reason'],
            # Frozen decisions of the composer: the seller and the manual discount were authorised then.
            may_assign_seller=True, may_apply_manual_discount=True,
            may_order_over_stock=True,
        )
        if priced['total'] != locked.total:
            raise QuotePriceChanged(
                f'Los precios cambiaron desde que se elaboró: el total era {locked.total} y hoy es '
                f'{priced["total"]}. Actualiza el borrador antes de aprobarlo.',
                expected=locked.total, current=priced['total'], priced=priced)
        locked.status = SalesQuote.STATUS_APPROVED
        locked.approved_by = actor
        locked.approved_at = timezone.now()
        locked.save(update_fields=['status', 'approved_by', 'approved_at', 'updated_at'])
        _audit(actor=actor, company=company, action='sales_quote_approved', quote=locked, request=request,
               total=str(locked.total))
    return locked


def send_quote(*, actor, company, quote, request=None) -> SalesQuote:
    """Mark an approved quote as sent to the customer (the PDF is shared by hand). Sending again is a no-op."""
    _require(actor, company, CAP_MANAGE, 'No tienes permiso para enviar cotizaciones.')
    with transaction.atomic():
        locked = _locked(quote)
        if locked.status == SalesQuote.STATUS_SENT:
            return locked
        if locked.status != SalesQuote.STATUS_APPROVED:
            raise QuoteStateError('Sólo se envía una cotización aprobada.')
        _not_expired(locked)
        locked.status = SalesQuote.STATUS_SENT
        locked.sent_at = timezone.now()
        locked.save(update_fields=['status', 'sent_at', 'updated_at'])
        _audit(actor=actor, company=company, action='sales_quote_sent', quote=locked, request=request)
    return locked


def decide_quote(*, actor, company, quote, accepted: bool, note: str = '', request=None) -> SalesQuote:
    """Record the customer's answer to an approved or sent quote, as staff heard it."""
    _require(actor, company, CAP_MANAGE, 'No tienes permiso para registrar la respuesta del cliente.')
    note = _text(note, MAX_NOTE, 'La nota')
    with transaction.atomic():
        locked = _locked(quote)
        if locked.status not in (SalesQuote.STATUS_APPROVED, SalesQuote.STATUS_SENT):
            raise QuoteStateError('Sólo se registra la respuesta de una cotización aprobada o enviada.')
        _not_expired(locked)
        locked.status = SalesQuote.STATUS_ACCEPTED if accepted else SalesQuote.STATUS_REJECTED
        locked.decided_by = actor
        locked.decided_at = timezone.now()
        locked.decision_note = note
        locked.save(update_fields=['status', 'decided_by', 'decided_at', 'decision_note', 'updated_at'])
        _audit(actor=actor, company=company, request=request, quote=locked,
               action='sales_quote_accepted' if accepted else 'sales_quote_rejected')
    return locked


def expire_due(*, company=None, today: date | None = None) -> int:
    """
    Stamp `expired` on every live quote whose validity has passed. Idempotent and cron-friendly
    (`manage.py expire_sales_quotes`): reads never write, so this is the only writer of the state.
    """
    today = today or timezone.localdate()
    due = SalesQuote.objects.filter(status__in=SalesQuote.LIVE_STATUSES, valid_until__lt=today)
    if company is not None:
        due = due.filter(company=company)
    count = 0
    for quote_id in list(due.values_list('pk', flat=True)):
        with transaction.atomic():
            locked = SalesQuote.objects.select_for_update().filter(pk=quote_id).first()
            if locked is None or locked.status not in SalesQuote.LIVE_STATUSES or locked.valid_until >= today:
                continue
            locked.status = SalesQuote.STATUS_EXPIRED
            locked.expired_at = timezone.now()
            locked.save(update_fields=['status', 'expired_at', 'updated_at'])
            _audit(actor=None, company=locked.company, action='sales_quote_expired', quote=locked,
                   valid_until=locked.valid_until.isoformat())
            count += 1
    return count


# ---------------------------------------------------------------------------
# Conversion
# ---------------------------------------------------------------------------

CONVERTIBLE = frozenset({SalesQuote.STATUS_APPROVED, SalesQuote.STATUS_SENT, SalesQuote.STATUS_ACCEPTED})
PRICE_DECISIONS = ('', 'current', 'quoted')


def _conversion_units(quote, raw) -> dict[int, list[dict]]:
    """`{item id: [{id, method}]}` from the request; validated by the till when the sale is written."""
    if raw in (None, ''):
        return {}
    if not isinstance(raw, dict):
        raise QuoteError('Formato de equipos inválido.')
    ids = set(quote.items.values_list('pk', flat=True))
    out = {}
    for key, value in raw.items():
        try:
            item_id = int(key)
        except (TypeError, ValueError):
            raise QuoteError('Línea de la cotización inválida.') from None
        if item_id not in ids:
            raise QuoteError('Esa línea no es de esta cotización.')
        if not isinstance(value, list):
            raise QuoteError('Formato de equipos inválido.')
        out[item_id] = value
    return out


def _sale_items(quote, units: dict[int, list[dict]]) -> list[dict]:
    """The till's basket for a quote: its lines, the devices the cashier identified, the quoted used ones."""
    items = []
    for item in quote.items.order_by('sort_order', 'pk'):
        entry = {'product': item.product_id, 'quantity': item.quantity}
        if item.on_order:
            entry['on_order'] = True
            if item.supply_reason:
                entry['supply_reason'] = item.supply_reason
        named = units.get(item.pk)
        if named:
            entry['units'] = named
        elif item.stock_unit_id:
            # The device the customer was offered, chosen by hand from the quote.
            entry['units'] = [{'id': item.stock_unit_id, 'method': _NAMED}]
        items.append(entry)
    return items


def _check_quoted_units(quote) -> None:
    for item in quote.items.select_related('stock_unit__product').filter(stock_unit__isnull=False):
        refusal = used_unit_till_refusal(item.stock_unit)
        if refusal:
            raise QuoteConflict(f'«{item.description}»: {refusal}', code='unit_not_inspected')


def convert_quote(*, actor, company, quote, data, idempotency_key, request=None):
    """
    Turn an approved, sent or accepted quote into a COUNTER SALE. Returns `(order, created)`.

    The sale is `create_pos_sale`'s, whole: payment, stock (strict), devices, commission, receipt,
    audit and idempotency by `idempotency_key`. The quote adds three things: its lines and customer
    and seller; the price check against what was quoted (`expected_total`); and the link
    (`Order.source_quote`, quote `converted`). A retry with the same key answers the same sale; a
    second conversion with another key is a 409 naming the sale.
    """
    _require(actor, company, CAP_POS, 'Convertir en venta es cobrar en caja: necesitas permiso de punto de venta.')
    data = data or {}
    try:
        key = pos_services.validate_idempotency_key(idempotency_key)
    except pos_services.PosValidationError as exc:
        raise QuoteError(str(exc)) from None
    decision = str(data.get('price_decision') or '')
    if decision not in PRICE_DECISIONS:
        raise QuoteError('Decisión de precio no reconocida: «current» o «quoted».')

    with transaction.atomic():
        locked = _locked(quote)
        if locked.status == SalesQuote.STATUS_CONVERTED:
            if locked.conversion_key == key and locked.converted_order_id:
                return locked.converted_order, False
            raise QuoteConflict('Esta cotización ya se convirtió en venta.', code='already_converted',
                                extra={'order_id': locked.converted_order_id})
        if locked.status not in CONVERTIBLE:
            raise QuoteStateError('Sólo se convierte en venta una cotización aprobada, enviada o aceptada.')
        _not_expired(locked)
        try:
            branch = pos_services.resolve_pos_branch(actor, company, locked.branch_id)
        except pos_services.PosValidationError:
            raise QuotePermissionError('No tienes acceso a la sucursal de esta cotización.') from None
        _check_quoted_units(locked)
        items = _sale_items(locked, _conversion_units(locked, data.get('units')))

        # The quote's manual discount was decided (and authorised) when it was composed.
        manual = locked.discount_source == DiscountSource.MANUAL
        discount_type = locked.discount_type if manual else ''
        discount_value = locked.discount_value if manual else None
        discount_reason = locked.discount_reason if manual else ''
        may_discount = manual
        expected = locked.total
        if decision == 'current':
            if data.get('expected_total') in (None, ''):
                raise QuoteError('Indica el total actual que leíste para cobrar al precio de hoy.')
            expected = data.get('expected_total')
        elif decision == 'quoted':
            discount_type, discount_value, discount_reason = _keep_quoted_price(
                actor=actor, company=company, quote=locked, branch=branch, items=items)
            may_discount = discount_type != ''

        seller_id = data.get('seller') if data.get('seller') not in (None, '') else locked.seller_id
        may_assign = (seller_id == locked.seller_id) or has_capability(actor, company, CAP_ASSIGN_SELLER)
        try:
            order, created = pos_services.create_pos_sale(
                actor=actor, company=company, branch=branch, items=items,
                customer=locked.customer_id, seller_id=seller_id,
                payment_method=data.get('payment_method', PaymentMethod.CASH),
                idempotency_key=key, terms_confirmed=data.get('terms_confirmed') is True,
                manual_discount_type=discount_type, manual_discount_value=discount_value,
                discount_reason=discount_reason,
                amount_received=data.get('amount_received'),
                payment_reference=data.get('payment_reference', ''),
                external_reference=data.get('external_reference', ''),
                sale_notes=data.get('sale_notes') or f'Cotización {locked.label}',
                receipt_type=data.get('receipt_type'),
                may_assign_seller=may_assign, may_apply_manual_discount=may_discount,
                may_order_over_stock=has_capability(actor, company, CAP_ORDERS_MANAGE),
                expected_total=expected, request=request,
            )
        except pos_services.PosPriceChanged as exc:
            raise QuotePriceChanged(
                f'El precio cambió desde la cotización: el total cotizado era {locked.total} y hoy es '
                f'{exc.current}. Confirma cobrar el precio actual'
                + (' o mantener el precio cotizado.' if _may_keep_quoted(actor, company, locked, exc.priced)
                   else '.'),
                expected=Decimal(str(locked.total)), current=exc.current, priced=exc.priced,
                can_keep_quoted=_may_keep_quoted(actor, company, locked, exc.priced),
            ) from None
        except pos_services.PosKeyReserved as exc:
            raise QuoteConflict(str(exc), code='key_reserved') from None
        except pos_services.PosIdempotencyConflict as exc:
            raise QuoteConflict(str(exc), code='idempotency_conflict',
                                extra={'order_id': exc.existing_order.pk}) from None
        except pos_services.PosError as exc:
            raise _wrap_pos(exc) from None

        if not created:
            # The till answered a sale it had already made with this key, and it is not this quote's.
            raise QuoteConflict('Esa clave ya se usó para otra venta.', code='idempotency_conflict',
                                extra={'order_id': order.pk})
        link = {'source_quote': locked}
        if manual and decision != 'quoted' and locked.discount_authorized_by_id:
            # Who decided the discount the customer was quoted, not who rang it up.
            link['discount_authorized_by'] = locked.discount_authorized_by_id
        Order.objects.filter(pk=order.pk).update(**link)
        order.refresh_from_db()
        locked.status = SalesQuote.STATUS_CONVERTED
        locked.converted_order = order
        locked.converted_by = actor
        locked.converted_at = timezone.now()
        locked.conversion_key = key
        locked.save(update_fields=['status', 'converted_order', 'converted_by', 'converted_at',
                                   'conversion_key', 'updated_at'])
        _audit(actor=actor, company=company, action='sales_quote_converted', quote=locked, request=request,
               order_id=order.pk, quoted_total=str(locked.total), charged_total=str(order.total),
               price_decision=decision or 'quoted_total')
    return order, True


def _may_keep_quoted(actor, company, quote, priced) -> bool:
    """Whether this caller could keep the quoted price now: a discount, only on a dearer basket with no promotion."""
    if not has_capability(actor, company, CAP_DISCOUNTS):
        return False
    if priced['promotions']['discount'] > 0:
        return False
    return priced['subtotal'] > quote.total


def _keep_quoted_price(*, actor, company, quote, branch, items):
    """
    `price_decision='quoted'`: charge the quoted total although today's prices are higher. That is a
    MANUAL discount (`sales.discounts.apply`, audited by the till) of exactly the difference.
    """
    if not has_capability(actor, company, CAP_DISCOUNTS):
        raise QuotePermissionError('Mantener el precio cotizado es un descuento: necesitas permiso de descuentos.')
    try:
        now = pos_services.build_pos_sale(
            operator=actor, company=company, branch=branch, items=items, customer=quote.customer_id,
            seller_id=quote.seller_id, payment_method=PaymentMethod.CASH,
            may_assign_seller=True, may_order_over_stock=has_capability(actor, company, CAP_ORDERS_MANAGE),
            validate_cash=False, require_units=False)
    except pos_services.PosError as exc:
        raise _wrap_pos(exc) from None
    if now['promotions']['discount'] > 0:
        raise QuoteConflict('Hoy aplica una promoción automática y no se combina con otro descuento: '
                            'cobra el precio actual.', code='promotion_applies')
    difference = now['subtotal'] - Decimal(str(quote.total))
    if difference < 0:
        raise QuoteConflict('El precio de hoy es menor que el cotizado: cobra el precio actual.',
                            code='current_is_lower')
    if difference == 0:
        return '', None, ''
    reason = f'Precio cotizado {quote.label}'
    if quote.discount_reason:
        reason = f'{reason} · {quote.discount_reason}'
    return 'amount', difference, reason[:pos_services.MAX_REASON]


def history(quote):
    """Every revision of this quote's number, oldest first."""
    return SalesQuote.objects.filter(company_id=quote.company_id, number=quote.number).order_by('revision')


