"""
CATALOG-REFERENCE-01 · PR F — quick device sale at the till (docs/adr-catalog-reference.md §4.5, D-F-1/2/3).

    THE TILL USES EXACTLY THE SAME CATALOGUE. THIS MODULE WRITES NOTHING OF ITS OWN.

Two questions a seller asks at the counter, and the services that already answer them:

  «what can I sell of this model?»   `configurations` — the model's palette (`model_palette.palette_of`) minus its
                                     excluded pairs, in reading order (capacity → colour → SIM → region), each
                                     combination with the variant that IS it (`device_catalog.variant_key`), that
                                     variant's own stock on THIS shelf (`inventory_services.branch_quantity`) and
                                     whether it sells on order (`supply_services.on_order_payload`: lead time and
                                     price, never what the supplier holds). A cost only to `costs.view`.

  «the device is here»               `receive` — the combination is resolved as a WRITER resolves it
                                     (`resolve_variant_spec(strict=True)`'s rule, on ids: the palette whole for a
                                     combination no variant is yet, excluded pairs always), the variant found
                                     (`find_variant`) or created, and the device received — both through
                                     `registration_services.register` (variant + `receive_units` + Kardex + label,
                                     in one transaction). An on-order line goes through `supply_services.receive_line`
                                     (entry + exit to its order + `OrderUnit`). The till then names the device in the
                                     sale like any other (SERIAL-PICK: `OrderUnit`, with the method it was read by).

DECISIONS (ADR §4.5):
  * D-F-1 — a variant created here is enabled for the shop (`draft_origin=''`, like «Registrar artículo») and NEVER
    published online (`is_published_online=False`; DEC-CATALOG-VARIANT-DRAFT-01 still governs the web). It is never
    created without its device: both are one transaction.
  * D-F-2 — receiving needs `inventory.adjust`; creating a variant needs `products.manage` too (what «Registrar
    artículo» asks); a cost needs `costs.view`; a price change needs `products.manage`. A seller without them still
    reads the picker and sells what exists, and is told to ask an authorised user.
  * D-F-3 — the device keeps its supplier (`StockUnit.supplier`), shown only to who sees suppliers or costs.

POS-ORDER-ARRIVAL-01 — a device a paid order is waiting for (an on-order line, committed and not yet received) is
that order's, not free stock:
  * `configurations` lists, on each combination, the lines of the caller's visible orders that wait for one
    (`pending_order_lines`, the customer masked) — to whoever may receive at the till;
  * «Llegó el equipo» as plain stock while such a line waits for that variant in THIS branch answers 409
    `pending_order_line` with the lines, unless the body says `confirm_stock: true` and a `reason`, which is audited
    (`pos_device_received_over_pending_order`). With `order_item` the device goes to its order (ADR D-F-9, revised);
  * the answer names the branch where the device ENTERED (an order's is its dispatch branch) and how it was read
    (`method`: the scanner or by hand).

The idempotency trail is the audit line (`pos_device_received`): key, fingerprint, and a RECORD of ids and flags —
never an IMEI, a serial or an amount.
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from itertools import product as cartesian

from django.db import connection, transaction
from django.db.models import Count, Exists, OuterRef, Q, Sum

from . import device_catalog, device_identity, finish_media, inventory_services, model_palette, pos_payloads, supply_services
from . import registration_services as registration
from .models import (
    AdminAuditLog, Branch, BranchStock, ModelOption, Order, OrderItem, OrderReturn, OrderUnit, Product, ProductModel,
    ProductVariantOption, StockMovement, StockUnit, Supplier, VariantAttribute, VariantOption,
)

AUDIT_ACTION = 'pos_device_received'
#: POS-ORDER-ARRIVAL-01: a device received as plain stock although a paid order of that variant waited here.
OVER_PENDING_AUDIT_ACTION = 'pos_device_received_over_pending_order'
#: The reason that confirms it — the length every other «why» of the panel asks.
REASON_MIN, REASON_MAX = 5, 500
#: At most this many waiting lines travel with a model's configurations (oldest first).
MAX_PENDING_LINES = 200
#: What the Kardex line of a device received at the till says.
REASON = 'Caja: llegó el equipo'
#: SCAN-TO-STOCK-01 — the same reception from «Registrar equipo por escaneo» (`scan_intake`): what the Kardex line and
#: the record say of where the device came in. The rules are the till's, unchanged.
ORIGIN_POS, ORIGIN_SCAN = 'pos', 'scan'
REASONS = {ORIGIN_POS: REASON, ORIGIN_SCAN: 'Inventario: registro por escaneo'}
LINE = Product.CONDITION_NEW
MAX_SEARCH = 25
MAX_SUPPLIERS = 200
MAX_KEY_LENGTH = 100
MAX_ID = 2_147_483_647

RECEIVE_FORBIDDEN = ('Recibir un equipo requiere el permiso «Mover inventario». Puedes vender los equipos que ya '
                     'están en la sucursal; para registrar uno que llegó, pide a un usuario autorizado que lo reciba.')
CREATE_FORBIDDEN = ('Esta combinación todavía no existe como variante. Crearla requiere además «Administrar '
                    'productos»: pide a un usuario autorizado que reciba el equipo.')
COSTS_FORBIDDEN = 'Necesitas el permiso «Ver costos y márgenes» para indicar un costo.'
PRICE_FORBIDDEN = 'Cambiar el precio de venta requiere el permiso «Administrar productos».'


class QuickSaleError(Exception):
    """Nothing was written. `body()` is the answer; `status` its HTTP status."""

    def __init__(self, detail: str, *, code: str = 'invalid', field: str = '', status: int = 400, extra=None):
        super().__init__(detail)
        self.detail = detail
        self.code = code
        self.field = field
        self.status = status
        self.extra = extra or {}

    def body(self) -> dict:
        body = {'detail': self.detail, 'code': self.code}
        if self.field:
            body['field'] = self.field
        body.update(self.extra)
        return body


# ---------------------------------------------------------------------------
# Authority — decided once from (user, company), handed in as facts
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Authority:
    receive: bool            # inventory.adjust
    create_variant: bool     # inventory.adjust + products.manage
    set_price: bool          # products.manage
    view_costs: bool         # costs.view
    choose_supplier: bool    # inventory.adjust: the receiver states who sold the device (D-F-10)
    open_suppliers: bool     # products.view: may open «Proveedores» (the empty state links to it only then)
    receive_on_order: bool   # inventory.adjust + sales.orders.manage (the order screen's «Recibir»)

    def can(self) -> dict:
        # POS-ORDER-ARRIVAL-01: `receive_on_order` travels too — the till offers «Es del pedido» only to who may.
        return asdict(self)


def _holds(user, company, capability, legacy_roles) -> bool:
    from .permissions import get_user_role
    from .tenancy import has_capability, uses_legacy_bridge

    if company is None or user is None or not getattr(user, 'is_authenticated', False):
        return False
    if uses_legacy_bridge(user, company):
        return get_user_role(user) in legacy_roles
    return has_capability(user, company, capability)


def authority_for(user, company) -> Authority:
    from .admin_views import _LEGACY_MANAGE_CATALOG_ROLES, _LEGACY_MANAGE_ORDERS_ROLES, _LEGACY_VIEW_CATALOG_ROLES
    from .cost_services import user_may_view_costs
    from .inventory_views import _LEGACY_INVENTORY_ADJUST_ROLES

    adjust = _holds(user, company, 'inventory.adjust', _LEGACY_INVENTORY_ADJUST_ROLES)
    manage = _holds(user, company, 'products.manage', _LEGACY_MANAGE_CATALOG_ROLES)
    view = _holds(user, company, 'products.view', _LEGACY_VIEW_CATALOG_ROLES)
    orders = _holds(user, company, 'sales.orders.manage', _LEGACY_MANAGE_ORDERS_ROLES)
    # D-F-10 (review of #375): choosing the supplier on receipt is the RECEIVER's, per the owner's order — the
    # name of an active supplier of the company, to pick it. Reading the supplier screen (and the supplier of a
    # device in the unit lists, D-F-3) keeps its own authority.
    return Authority(receive=adjust, create_variant=adjust and manage, set_price=manage,
                     view_costs=user_may_view_costs(user, company), choose_supplier=adjust, open_suppliers=view,
                     receive_on_order=adjust and orders)


# ---------------------------------------------------------------------------
# Reading: the model search and the model's configurations
# ---------------------------------------------------------------------------

def _sellable_variants(company):
    """The variants the till counts: of the till's line, active, never a pending draft (`pending_draft_q`)."""
    return (Product.objects.filter(company=company, condition_line=LINE, is_active=True, product_model__isnull=False)
            .exclude(Product.pending_draft_q()))


def search_models(company, branch, term) -> list[dict]:
    """Models of `company` whose name holds `term` (accents and case aside), with their stock on THIS shelf."""
    term = ' '.join(str(term or '').split())[:80]
    if len(term) < 2:
        return []
    folded = device_catalog.normalize_model_name(term)
    models = list(ProductModel.objects.filter(company=company)
                  .filter(Q(name__icontains=term) | Q(normalized_name__icontains=folded))
                  .order_by('sort_order', 'name', 'pk')[:MAX_SEARCH])
    ids = [m.pk for m in models]
    variants = dict(_sellable_variants(company).filter(product_model_id__in=ids)
                    .values('product_model_id').annotate(n=Count('pk')).values_list('product_model_id', 'n'))
    stock = dict(BranchStock.objects.filter(branch=branch, product__in=_sellable_variants(company)
                                            .filter(product_model_id__in=ids))
                 .values('product__product_model_id').annotate(n=Sum('quantity'))
                 .values_list('product__product_model_id', 'n'))
    with_palette = set(ModelOption.objects.filter(model_id__in=ids, company=company)
                       .values_list('model_id', flat=True).distinct())
    return [{'id': m.pk, 'name': m.name, 'has_palette': m.pk in with_palette, 'variants': variants.get(m.pk, 0),
             'available': int(stock.get(m.pk) or 0)} for m in models]


def customer_hint(name) -> str:
    """«Ana María Quispe» → «Ana Q.»: enough to say «this is Ana's order» at a counter a customer may see."""
    words = str(name or '').split()
    if not words:
        return ''
    first = words[0][:40]
    return f'{first} {words[-1][0].upper()}.' if len(words) > 1 else first


def waiting_lines(company, actor, product_ids, *, branch=None, lock=False):
    """
    The on-order lines that wait for a device: of `company`, of an order the caller may see (`visible_orders`), PAID,
    committed to a supplier and neither received nor failed (`supply_status='pending'` — exactly what
    `supply_services.receive_line` accepts), of an order with no standing return (`unit_identification.returning`),
    for `product_ids`; in `branch` (the order's dispatch branch) when given. Oldest first. One query, as values.

    `lock` (inside the caller's transaction): the LINE rows only (`of=('self',)`), FOR UPDATE — see
    `_refuse_over_waiting`.
    """
    from .tenancy import visible_orders

    returns = OrderReturn.objects.filter(order_id=OuterRef('order_id')).exclude(status=OrderReturn.STATUS_WITHDRAWN)
    lines = (OrderItem.objects
             .filter(order__company=company, order__in=visible_orders(actor, company),
                     order__status=Order.Status.PAID, supply_origin=OrderItem.SUPPLY_SUPPLIER,
                     supply_status=OrderItem.SUPPLY_PENDING, product_id__in=list(product_ids))
             .filter(~Exists(returns)))
    if branch is not None:
        lines = lines.filter(order__fulfillment_branch=branch)
    if lock:
        lines = lines.select_for_update(of=('self',))
    return (lines.order_by('order__created_at', 'pk')
            .values('pk', 'order_id', 'product_id', 'quantity', 'order__customer_name', 'order__created_at',
                    'order__fulfillment_branch_id', 'order__fulfillment_branch__name')[:MAX_PENDING_LINES])


def waiting_line_payload(row, branch) -> dict:
    """One waiting line as the till shows it: the order, the masked customer, where it is dispatched from."""
    created = row['order__created_at']
    return {
        'order_item': row['pk'], 'order_id': row['order_id'], 'order_number': row['order_id'],
        'quantity': row['quantity'], 'customer': customer_hint(row['order__customer_name']),
        'created_at': created.isoformat() if created else None,
        'branch': {'id': row['order__fulfillment_branch_id'], 'name': row['order__fulfillment_branch__name'] or ''},
        'here': row['order__fulfillment_branch_id'] == branch.pk,
    }


def _variant_payload(product, available: int, authority: Authority) -> dict:
    pending = product.is_pending_draft
    payload = {
        'product_id': product.pk, 'name': product.name, 'price': str(product.price), 'available': available,
        'is_active': product.is_active, 'pending_draft': pending,
        'sellable': bool(product.is_active and not pending and product.price is not None and product.price > 0),
        'is_serialized': product.is_serialized, 'requires_imei': product.requires_imei,
        # DC3: lead time and price of the best valid offer. NEVER the supplier's quantity as own stock.
        'on_order': None if pending or not product.is_active else supply_services.on_order_payload(product),
    }
    if authority.view_costs:
        payload['reference_cost'] = None if product.reference_cost is None else str(product.reference_cost)
    return payload


def configurations(company, model, branch, authority: Authority, *, actor=None) -> dict:
    """
    What the till's picker reads for `model`: the roles in reading order, each with its options in the company's
    order and whether it VARIES (one option: the picker does not ask); every combination the palette admits minus
    the excluded pairs, with the variant that is it, or null. A model without a palette offers only the variants it
    already has (a combination nobody admitted is not created at the counter).

    POS-ORDER-ARRIVAL-01: each combination carries `pending_order_lines` — the paid orders that wait for a device of
    its variant (`waiting_lines`, within what `actor` may see) — filled only for who may receive at the till; empty
    for everybody else, who reaches no «Llegó el equipo».
    """
    palette = model_palette.palette_of(model)
    attributes = sorted(VariantAttribute.objects.filter(company_id=company.pk).prefetch_related('options'),
                        key=model_palette.attribute_sort_key)
    variants = list(Product.objects.filter(company=company, product_model=model, condition_line=LINE))
    links: dict[int, list[int]] = {}
    for product_id, option_id in ProductVariantOption.objects.filter(
            company=company, product__in=variants).values_list('product_id', 'option_id'):
        links.setdefault(product_id, []).append(option_id)
    by_key = {device_catalog.variant_key(links.get(p.pk, [])): p for p in variants}
    stock = dict(BranchStock.objects.filter(branch=branch, product__in=variants)
                 .values_list('product_id', 'quantity'))
    waiting: dict[int, list[dict]] = {}
    if authority.receive and actor is not None and variants:
        for row in waiting_lines(company, actor, [p.pk for p in variants]):
            waiting.setdefault(row['product_id'], []).append(waiting_line_payload(row, branch))

    if palette:
        allowed = palette
    else:
        allowed = {}
        used = {option_id for ids in links.values() for option_id in ids}
        for attribute in attributes:
            chosen = {o.pk for o in attribute.options.all() if o.pk in used}
            if chosen:
                allowed[attribute.pk] = chosen
    columns, attribute_rows = [], []
    # G (owner's visual order 4): a colour chip shows its finish's authorized picture, else the company's swatch,
    # else only its name — never the importer's neutral grey as if it were the colour.
    pictures = None
    for attribute in attributes:
        if attribute.pk not in allowed:
            continue
        options = [o for o in attribute.options.all() if o.pk in allowed[attribute.pk] and o.company_id == company.pk]
        columns.append([o.pk for o in options])
        # Review of #375: without a palette a variant may LACK an attribute (A = 128 · black, B = 256 · black · a
        # region). The attribute does not vary only when EVERY variant carries its one option; otherwise the picker
        # asks it, so a variant without it stays reachable. With a palette every combination carries every role.
        own = {o.pk for o in options}
        carried_by_all = palette or all(own & set(links.get(p.pk, [])) for p in variants if links.get(p.pk))
        attribute_rows.append({
            'id': attribute.pk, 'name': attribute.name, 'role': attribute.role, 'kind': attribute.kind,
            'varies': len(options) > 1 or not carried_by_all,
            'options': [{'id': o.pk, 'label': o.label, 'swatch_hex': o.swatch_hex} for o in options],
        })
        if attribute.role == VariantAttribute.ROLE_COLOR or attribute.kind == VariantAttribute.KIND_COLOR:
            if pictures is None:
                # The till may be seen by customers: only pictures whose licence is verified (owner, 2026-11-23).
                pictures = finish_media.chip_pictures(model, verified_only=True)
                own_swatches = model_palette.model_swatches(model)
            # FASE 1.2 · Y: the model's own swatch, else the company's, else none; and whether it is approximate.
            rows = []
            for o in options:
                swatch, approximate = device_catalog.resolved_swatch(o, *own_swatches.get(o.pk, ('', False)))
                rows.append({'id': o.pk, 'label': o.label, 'swatch_hex': swatch,
                             'swatch_is_approximate': approximate, 'thumbnail': pictures.get(o.pk)})
            attribute_rows[-1]['options'] = rows
    every_option = {pk for column in columns for pk in column}
    excluded = [pair for pair in (palette.excluded if palette else set()) if pair <= every_option]

    combinations, truncated = [], False
    if palette:
        for combo in cartesian(*columns):
            chosen = set(combo)
            if any(pair <= chosen for pair in excluded):
                continue
            if len(combinations) >= model_palette.MAX_COMBINATIONS:
                truncated = True
                break
            combinations.append(list(combo))
    else:
        order = {pk: n for n, pk in enumerate(pk for column in columns for pk in column)}
        for product in variants:
            ids = links.get(product.pk, [])
            if ids:
                combinations.append(sorted(ids, key=lambda pk: order.get(pk, len(order))))
    rows = []
    for ids in combinations:
        key = device_catalog.variant_key(ids)
        product = by_key.get(key)
        rows.append({'key': key, 'option_ids': ids,
                     'variant': None if product is None else _variant_payload(product, int(stock.get(product.pk) or 0),
                                                                             authority),
                     'pending_order_lines': [] if product is None else waiting.get(product.pk, [])})
    answer = {
        'model': {'id': model.pk, 'name': model.name},
        'line': LINE,
        'has_palette': bool(palette),
        'attributes': attribute_rows,
        'combinations': rows,
        'truncated': truncated,
        'can': authority.can(),
    }
    if excluded:
        answer['excluded_pairs'] = sorted(sorted(pair) for pair in excluded)
    if authority.choose_supplier:
        answer['suppliers'] = [{'id': pk, 'name': name} for pk, name in Supplier.objects.filter(
            company=company, is_active=True).order_by('name', 'pk').values_list('pk', 'name')[:MAX_SUPPLIERS]]
    return answer


# ---------------------------------------------------------------------------
# Writing: «Llegó el equipo»
# ---------------------------------------------------------------------------

def _pk(value, field, *, label='Identificador'):
    if isinstance(value, bool) or value in (None, ''):
        raise QuickSaleError(f'{label} no válido.', field=field)
    if isinstance(value, str) and not model_palette.is_ascii_id(value):
        raise QuickSaleError(f'{label} no válido.', field=field)
    try:
        pk = int(value)
    except (TypeError, ValueError):
        raise QuickSaleError(f'{label} no válido.', field=field) from None
    if not 0 < pk <= MAX_ID:
        raise QuickSaleError(f'{label} no válido.', field=field)
    return pk


def _device(body: dict) -> dict:
    """The device as typed: IMEI 1, IMEI 2 and serial — checked now (check digit, format) so the field says it."""
    row = {}
    for field, label in (('imei', 'El IMEI'), ('imei2', 'El segundo IMEI')):
        raw = body.get(field)
        if raw in (None, ''):
            continue
        if not isinstance(raw, (str, int)) or isinstance(raw, bool):
            raise QuickSaleError(f'{label} no es válido.', field=field)
        try:
            row[field] = device_identity.clean_imei(str(raw), label=label) or None
        except device_identity.DeviceIdentityError as exc:
            raise QuickSaleError(str(exc), field=field) from None
    serial = body.get('serial_number')
    if serial not in (None, ''):
        if not isinstance(serial, str):
            raise QuickSaleError('La serie no es válida.', field='serial_number')
        try:
            row['serial_number'] = device_identity.clean_serial(serial) or None
        except device_identity.DeviceIdentityError as exc:
            raise QuickSaleError(str(exc), field='serial_number') from None
    if not row.get('imei') and not row.get('serial_number'):
        raise QuickSaleError('Indica el IMEI 1 del equipo (o su serie, si no tiene IMEI).', field='imei')
    if row.get('imei2') and not row.get('imei'):
        raise QuickSaleError('El IMEI 2 acompaña al IMEI 1: indica primero el IMEI 1.', field='imei')
    if row.get('imei2') and row['imei2'] == row.get('imei'):
        raise QuickSaleError('El IMEI 2 es distinto del IMEI 1.', field='imei2')
    return row


def _unit_brief(unit) -> dict:
    return {'id': unit.pk, 'label_code': unit.label_code or '', 'product_id': unit.product_id,
            'product_name': unit.product.name, 'status': unit.status, 'status_label': unit.get_status_display(),
            'branch_name': unit.branch.name}


def duplicate(company, row: dict, product=None, *, actor=None) -> QuickSaleError | None:
    """SCAN-TO-STOCK-01 — the 409 of `_duplicate`, for the scan screen's used devices (the same words, the same unit)."""
    return _duplicate(company, row, product, actor=actor)


def _visible_branch_ids(company, actor) -> set[int] | None:
    """The branches `actor` reaches in `company`; None (no masking) only when no actor is known."""
    if actor is None:
        return None
    from .tenancy import visible_branches

    return set(visible_branches(actor, company).values_list('pk', flat=True))


def _duplicate(company, row: dict, product=None, *, actor=None) -> QuickSaleError | None:
    """
    409 naming the device of the company that already holds one of these identifiers, or None.

    SCAN-TO-STOCK-01 review B1: the device is named (code, product, status, branch) only when it is in a branch the
    caller reaches; otherwise the answer says it exists elsewhere in the company and nothing more, with no `unit`.
    """
    live = (StockUnit.objects.filter(company=company).exclude(status=StockUnit.Status.VOIDED)
            .select_related('product', 'branch'))
    visible = _visible_branch_ids(company, actor)
    for field in ('imei', 'imei2'):
        value = row.get(field)
        if not value:
            continue
        holder = live.filter(Q(imei=value) | Q(imei2=value)).order_by('pk').first()
        if holder is not None:
            if visible is not None and holder.branch_id not in visible:
                return QuickSaleError(
                    'Ese IMEI ya está registrado en un equipo de otra sucursal de la empresa. Revisa el número.',
                    code='duplicate_imei', field=field, status=409)
            name = holder.label_code or f'#{holder.pk}'
            return QuickSaleError(
                f'Ese IMEI ya está registrado en el equipo {name} («{holder.product.name}», '
                f'{holder.get_status_display().lower()}, {holder.branch.name}). Revisa el número o busca ese equipo.',
                code='duplicate_imei', field=field, status=409, extra={'unit': _unit_brief(holder)})
    serial = row.get('serial_number')
    if serial and product is not None:
        holder = live.filter(product=product, serial_number=serial).order_by('pk').first()
        if holder is not None:
            if visible is not None and holder.branch_id not in visible:
                return QuickSaleError(
                    'Esa serie ya está registrada en un equipo de esta variante en otra sucursal de la empresa.',
                    code='duplicate_serial', field='serial_number', status=409)
            return QuickSaleError(
                f'Esa serie ya está registrada en el equipo {holder.label_code or holder.pk} de esta variante.',
                code='duplicate_serial', field='serial_number', status=409, extra={'unit': _unit_brief(holder)})
    return None


def _refuse_cost(body, authority):
    from .cost_services import states_a_cost

    if states_a_cost(body.get('cost')) and not authority.view_costs:
        raise QuickSaleError(COSTS_FORBIDDEN, code='costs_forbidden', field='cost', status=403)


def _money(value, field, label, *, positive=True):
    try:
        return supply_services.clean_money(value, field, label, required=True, positive=positive)
    except supply_services.SupplyError as exc:
        raise QuickSaleError(exc.detail, field=field) from None


def _supplier(company, body, authority):
    raw = body.get('supplier')
    if raw in (None, ''):
        return None
    if not authority.choose_supplier:
        raise QuickSaleError('Indicar el proveedor requiere el permiso «Mover inventario».',
                             code='suppliers_forbidden', field='supplier', status=403)
    supplier = Supplier.objects.filter(company=company, pk=_pk(raw, 'supplier')).first()
    if supplier is None:
        raise QuickSaleError('Proveedor no encontrado.', code='not_found', field='supplier', status=404)
    if not supplier.is_active:
        raise QuickSaleError('Ese proveedor está desactivado.', field='supplier')
    return supplier


def _lock_combination(company_id, model_id, key) -> None:
    """
    Two tills creating the SAME new variant queue here, before anything is read or locked: the second re-reads and
    finds the first one's variant. Taken first and only by this path, so it cannot join a cycle of row locks.
    """
    if connection.vendor != 'postgresql':
        return
    digest = hashlib.sha256(f'pos-variant|{company_id}|{model_id}|{LINE}|{key}'.encode()).digest()[:8]
    with connection.cursor() as cursor:
        cursor.execute('SELECT pg_advisory_xact_lock(%s)', [int.from_bytes(digest, 'big', signed=True)])


def _resolve(company, model, options, palette):
    """
    The variant this combination IS, or None — held to the palette as a WRITER is (`resolve_variant_spec`,
    `strict=True`): a combination no variant is yet must fit the palette whole; an excluded pair is refused even
    when a variant already holds it (that device cannot exist).
    """
    ids = sorted(o.pk for o in options)
    spec = {'model_id': model.pk, 'condition_line': LINE, 'option_ids': ids, 'variant_key': device_catalog.variant_key(ids)}
    product = device_catalog.find_variant(company, spec)
    try:
        if product is None:
            if not palette:
                raise QuickSaleError(
                    f'«{model.name}» no tiene paleta de opciones: en caja sólo se venden sus variantes existentes. '
                    'Define su paleta en Catálogo › Modelos.', code='no_palette', field='option_ids')
            model_palette.check_variant(model, options, palette=palette)
        elif palette.excluded:
            chosen = set(ids)
            for pair in palette.excluded:
                if pair <= chosen:
                    model_palette.check_variant(model, [o for o in options if o.pk in pair], palette=palette,
                                                complete=False)
    except device_catalog.CatalogError as exc:
        raise QuickSaleError(exc.args[0], code=exc.code or 'invalid', field='option_ids') from None
    return spec, product


def _usable(product):
    if product.archived_at is not None:
        # VARIANT-SAFE-01: the till never receives into (nor creates again) an archived variant.
        from .variant_lifecycle import archived_refusal

        raise QuickSaleError(archived_refusal(product), code='variant_archived', field='option_ids', status=409)
    if product.is_pending_draft:
        raise QuickSaleError(
            f'«{product.name}» es un borrador sin habilitar para la venta: habilítalo en Catálogo › Modelos antes de '
            'recibir equipos en caja.', code='variant_pending_draft', field='option_ids', status=409)
    if not product.is_active:
        raise QuickSaleError(f'«{product.name}» está inactivo: actívalo antes de recibir equipos en caja.',
                             code='variant_inactive', field='option_ids', status=409)


def _registration_error(company, exc: registration.RegistrationError, row, product, actor=None) -> QuickSaleError:
    if exc.field in ('imei', 'imei2', 'serial_number', 'units'):
        duplicate = _duplicate(company, row, product, actor=actor)
        if duplicate is not None:
            return duplicate
    field = exc.field if exc.field in ('imei', 'imei2', 'serial_number', 'cost', 'price') else (
        'option_ids' if exc.step == 'model' else exc.field or '')
    return QuickSaleError(exc.detail, code=exc.code, field=field, status=exc.status)


def _method(body) -> str:
    """How the device was read: `scanner` only when the till says so; by hand otherwise (the default)."""
    from . import unit_identification

    raw = body.get('method')
    if raw in (None, ''):
        return unit_identification.MANUAL
    if raw not in unit_identification.METHODS:
        raise QuickSaleError('Indica si el equipo se leyó con el lector o se escribió a mano.', code='invalid_method',
                             field='method')
    return raw


def _confirmation(body) -> str | None:
    """The reason that confirms plain stock over a waiting order — or None when the body does not confirm."""
    if body.get('confirm_stock') is not True:
        return None
    raw = body.get('reason')
    text = ' '.join(raw.split()) if isinstance(raw, str) else ''
    if len(text) < REASON_MIN:
        raise QuickSaleError(f'Escribe por qué entra como stock (al menos {REASON_MIN} caracteres).',
                             code='reason_required', field='reason')
    if len(text) > REASON_MAX:
        raise QuickSaleError(f'El motivo no puede pasar de {REASON_MAX} caracteres.', code='reason_too_long',
                             field='reason')
    return text


def _refuse_over_waiting(company, actor, product, branch, confirmed: str | None) -> list[dict]:
    """
    POS-ORDER-ARRIVAL-01. A paid order waits here for a device of `product`: plain stock is refused (409, with the
    lines) unless confirmed with a reason. Returns the waiting lines (empty when none) for the audit.

    RACES (owner: block them). The waiting lines are read FOR UPDATE, inside the receipt's transaction, the LINE rows
    only. A delivery of one of them (`supply_services.receive_line`: the order, then its line) that is mid-way makes
    this wait, and PostgreSQL re-reads the line once it commits: received, it no longer waits and the device enters
    as stock; still pending (the delivery failed), it is refused. A delivery that starts after this read waits for the
    receipt's commit. Nothing here locks an order, so the two never wait on each other in a cycle.
    The window left: a line that is not pending YET — an order being paid (`commit_order_lines`) at the very same
    instant — is not among the rows read, so a device can enter as stock just before that payment commits it. The
    till cannot know of an order that is not paid yet.
    """
    lines = [waiting_line_payload(row, branch)
             for row in waiting_lines(company, actor, [product.pk], branch=branch, lock=True)]
    if lines and confirmed is None:
        first = lines[0]
        who = f' ({first["customer"]})' if first['customer'] else ''
        more = f' y {len(lines) - 1} más' if len(lines) > 1 else ''
        raise QuickSaleError(
            f'Hay un pedido pagado que espera este equipo en esta sucursal: pedido #{first["order_number"]}{who}{more}. '
            'Entrégalo a su pedido, o confirma que entra como stock e indica el motivo.',
            code='pending_order_line', status=409, extra={'lines': lines})
    return lines


def receive(*, company, branch, body, actor, request=None, authority: Authority, origin: str = ORIGIN_POS) -> dict:
    """
    «Llegó el equipo». All of it or none of it — the caller holds the transaction. Returns the RECORD.

    `branch` is the till's, resolved by the caller within the caller's grants. `origin` (SCAN-TO-STOCK-01) only
    changes what the Kardex line says (`REASONS`) and is kept in the record.
    """
    if not isinstance(body, dict):
        raise QuickSaleError('Datos no válidos.')
    if body.get('order_item') not in (None, ''):
        return {**_receive_order_line(company=company, body=body, actor=actor, request=request, authority=authority),
                'origin': origin}
    if not authority.receive:
        raise QuickSaleError(RECEIVE_FORBIDDEN, code='receive_forbidden', status=403)
    method = _method(body)
    if body.get('line') not in (None, '', LINE):
        raise QuickSaleError('En caja se reciben equipos nuevos. Un seminuevo se registra con su ficha en '
                             'Inventario › Registrar artículo.', code='used_line', field='line')
    model = ProductModel.objects.filter(company=company, pk=_pk(body.get('model'), 'model', label='Modelo')).first()
    if model is None:
        raise QuickSaleError('Modelo no encontrado.', code='not_found', field='model', status=404)
    try:
        ids = model_palette.clean_option_ids(body.get('option_ids'))
    except device_catalog.CatalogError:
        raise QuickSaleError('Opciones no válidas.', code='invalid_options', field='option_ids') from None
    if not ids:
        raise QuickSaleError('Elige la capacidad, el color y la configuración del equipo.', code='invalid_options',
                             field='option_ids')
    options = list(VariantOption.objects.filter(company=company, pk__in=ids).select_related('attribute'))
    if len(options) != len(ids):
        raise QuickSaleError('Alguna opción no existe.', code='not_found', field='option_ids', status=404)
    if len({o.attribute_id for o in options}) != len(options):
        raise QuickSaleError('Una variante lleva una sola opción de cada atributo.', code='invalid_options',
                             field='option_ids')
    row = _device(body)
    _refuse_cost(body, authority)
    supplier = _supplier(company, body, authority)
    confirmed = _confirmation(body)

    palette = model_palette.palette_of(model)
    spec, product = _resolve(company, model, options, palette)
    if product is None:
        if not authority.create_variant:
            raise QuickSaleError(CREATE_FORBIDDEN, code='create_forbidden', status=403)
        price = _money(body.get('price'), 'price', 'el precio de venta')
        _lock_combination(company.pk, model.pk, spec['variant_key'])
        product = device_catalog.find_variant(company, spec)      # another till may have created it meanwhile
    price_changed = False

    def decide(variant):
        """Usable, and whether the typed price changes it (a change needs `set_price`)."""
        _usable(variant)
        if body.get('price') in (None, ''):
            return None, False
        typed = _money(body.get('price'), 'price', 'el precio de venta')
        if typed != variant.price and not authority.set_price:
            raise QuickSaleError(PRICE_FORBIDDEN, code='price_forbidden', field='price', status=403)
        return typed, typed != variant.price

    if product is not None:
        typed, price_changed = decide(product)          # early answer, on the row as read
        if typed is not None:
            price = typed
    duplicate = _duplicate(company, row, product, actor=actor)
    if duplicate is not None:
        raise duplicate
    waiting = [] if product is None else _refuse_over_waiting(company, actor, product, branch, confirmed)
    if product is not None:
        # VARIANT-SAFE-01 review — decided again on the LOCKED row, so an archive or a deactivation that commits
        # meanwhile either waits for this reception or is seen by it. AFTER the waiting lines (line → product, the
        # order of `supply_services.receive_line`: order → line → shelf → product): locking the product before them
        # closed a cycle with a delivery holding its line and shelf and waiting for the product (re-review of #407).
        product = Product.objects.select_for_update(of=('self',)).get(pk=product.pk)
        typed, price_changed = decide(product)
        if typed is not None:
            price = typed

    if price_changed:
        # Lock order: the product first (DEC-CATALOG-PALETTE-LOCK-01), then the shelf the reception writes.
        from .admin_views import write_product_fields

        locked = Product.objects.select_for_update(of=('self',)).get(pk=product.pk)
        locked, changed = write_product_fields(company, locked, {'price': str(price)})
        if changed:
            AdminAuditLog.log(actor=actor, action='product_updated', target_type='product', target_id=locked.pk,
                              request=request, company=company,
                              metadata={'product_name': locked.name, 'product_id': locked.pk,
                                        'changed_fields': changed, 'via': 'pos_device_receive'})

    unit_row = {key: value for key, value in row.items() if value}
    if body.get('cost') not in (None, ''):
        unit_row['cost'] = body.get('cost')
    reg_body = {
        'kind': registration.KIND_NEW_DEVICE,
        'model': {'id': model.pk},
        'options': ids,
        'product': {'name': device_catalog.variant_name(model, options, condition_line=LINE),
                    'price': str(price) if product is None else str(product.price),
                    'category': model.category_id, 'show_online': False},
        'units': [unit_row],
        'branch': branch.pk,
        'requires_imei': bool(row.get('imei')),
    }
    try:
        record = registration.register(
            company=company, body=reg_body, actor=actor, request=request, may_adjust=True,
            may_view_costs=authority.view_costs, branch_for=lambda _branch_id: branch, labels=True,
            reason=REASONS[origin], entry_type=StockMovement.PURCHASE_ENTRY, unit_supplier=supplier,
        )
    except registration.RegistrationError as exc:
        raise _registration_error(company, exc, row, product, actor) from None
    unit_id = record['unit_ids'][0]
    if waiting:
        # Ids and the reason, never the customer: the log is read by more people.
        AdminAuditLog.log(
            actor=actor, action=OVER_PENDING_AUDIT_ACTION, target_type='stock_unit', target_id=unit_id,
            request=request, company=company,
            metadata={'reason': confirmed, 'product_id': record['product_id'], 'branch_id': branch.pk,
                      'order_item_ids': [line['order_item'] for line in waiting],
                      'order_ids': [line['order_id'] for line in waiting]})
    return {
        'mode': 'stock', 'branch_id': branch.pk, 'model_id': model.pk,
        'product_id': record['product_id'], 'product_created': record['product_created'],
        'unit_id': unit_id, 'movement_ids': record['movement_ids'],
        'label_batch_ids': record['label_batch_ids'], 'supplier_id': supplier.pk if supplier else None,
        'price_changed': price_changed, 'order_id': None, 'order_item_id': None, 'method': method,
        'over_pending_order_items': [line['order_item'] for line in waiting], 'origin': origin,
    }


def _receive_order_line(*, company, body, actor, request, authority) -> dict:
    """A device sold on order arrives: `supply_services.receive_line` (entry + exit to its order + `OrderUnit`)."""
    from .tenancy import visible_orders

    if not authority.receive_on_order:
        raise QuickSaleError('Recibir un equipo pedido al proveedor requiere «Mover inventario» y «Administrar '
                             'pedidos». Pide a un usuario autorizado que lo reciba.', code='receive_forbidden',
                             status=403)
    pk = _pk(body.get('order_item'), 'order_item', label='Línea')
    line = (OrderItem.objects.filter(pk=pk, order__company=company, order__in=visible_orders(actor, company))
            .select_related('order', 'product').first())
    if line is None:
        raise QuickSaleError('Línea de pedido no encontrada.', code='not_found', field='order_item', status=404)
    if line.quantity != 1:
        raise QuickSaleError('Esta línea lleva varios equipos: recíbela desde el pedido.', field='order_item')
    method = _method(body)
    row = _device(body)
    _refuse_cost(body, authority)
    duplicate = _duplicate(company, row, line.product, actor=actor)
    if duplicate is not None:
        raise duplicate
    unit_row = {key: value for key, value in row.items() if value}
    if body.get('cost') not in (None, ''):
        unit_row['cost'] = body.get('cost')
    try:
        supply_services.receive_line(line, actor=actor, units=[unit_row], company=company, request=request,
                                     method=method)
    except supply_services.SupplyError as exc:
        if getattr(exc, 'unit_field', '') in ('imei', 'imei2', 'serial_number'):
            found = _duplicate(company, row, line.product, actor=actor)
            if found is not None:
                raise found from None
        status = {'already_received': 409, 'return_open': 409, 'not_found': 404}.get(exc.code, 400)
        if exc.code == 'costs_forbidden':
            status = 403
        raise QuickSaleError(exc.detail, code=exc.code or 'invalid', field=getattr(exc, 'unit_field', '') or
                             (exc.field or ''), status=status) from None
    record = (OrderUnit.objects.filter(order=line.order, product_id=line.product_id)
              .order_by('-identified_at', '-pk').select_related('unit').first())
    unit = record.unit
    return {
        'mode': 'order', 'branch_id': unit.branch_id, 'model_id': line.product.product_model_id,
        'product_id': line.product_id, 'product_created': False, 'unit_id': unit.pk,
        'movement_ids': list(StockMovement.objects.filter(order=line.order, product_id=line.product_id)
                             .order_by('pk').values_list('pk', flat=True)),
        'label_batch_ids': [], 'supplier_id': unit.supplier_id, 'price_changed': False,
        'order_id': line.order_id, 'order_item_id': line.pk, 'method': method,
    }


def present(company, branch, record: dict, *, authority: Authority, replayed: bool = False) -> dict:
    """
    The answer for a record — the device and its product as the till's unit lookup shows them (never a cost).

    POS-ORDER-ARRIVAL-01: read at the branch where the device ENTERED (`record['branch_id']`: the till's for plain
    stock, the order's dispatch branch for an order line), which the answer names; and with the method it was read
    by (a record written before this said nothing: by hand).
    """
    from . import unit_identification

    unit = StockUnit.objects.select_related('product', 'cosmetic_grade', 'supplier').get(
        company=company, pk=record['unit_id'])
    entered = Branch.objects.filter(company=company, pk=record.get('branch_id')).first() or branch
    answer = pos_payloads.unit_lookup_payload(unit, entered, record.get('method') or unit_identification.MANUAL)
    answer.update({
        'branch': {'id': entered.pk, 'name': entered.name},
        'replayed': replayed,
        'mode': record['mode'],
        'created_variant': bool(record['product_created']),
        'price_changed': bool(record['price_changed']),
        'order': None if record['mode'] != 'order' else {'id': record['order_id'], 'item_id': record['order_item_id']},
        'warnings': [] if record['mode'] == 'order' or unit.product.price > 0 else [
            'Esta variante no tiene precio de venta: la caja no la cobrará hasta que un usuario autorizado lo fije.'],
    })
    if authority.open_suppliers or authority.view_costs:
        answer['supplier'] = None if unit.supplier_id is None else {'id': unit.supplier_id,
                                                                    'name': unit.supplier.name}
    return answer


# ---------------------------------------------------------------------------
# The idempotency trail
# ---------------------------------------------------------------------------

def lock_key(company_id, actor_id, key) -> None:
    if connection.vendor != 'postgresql':
        return
    digest = hashlib.sha256(f'pos-device|{company_id}|{actor_id}|{key}'.encode()).digest()[:8]
    with connection.cursor() as cursor:
        cursor.execute('SELECT pg_advisory_xact_lock(%s)', [int.from_bytes(digest, 'big', signed=True)])


def previous(company, actor, key):
    return (AdminAuditLog.objects
            .filter(company=company, actor=actor, action=AUDIT_ACTION, metadata__idempotency_key=key)
            .order_by('-pk').first())


def record_trail(*, company, actor, request, key, fingerprint, record) -> None:
    AdminAuditLog.log(
        actor=actor, action=AUDIT_ACTION, target_type='stock_unit', target_id=record['unit_id'],
        metadata={'idempotency_key': key, 'fingerprint': fingerprint, 'record': record},
        request=request, company=company,
    )


@transaction.atomic
def receive_once(*, company, branch, body, actor, request, authority: Authority, key: str, fingerprint: str,
                 origin: str = ORIGIN_POS, present_with=None):
    """(answer, created) — the first call receives; the same key and body again answer the same, without writing."""
    lock_key(company.pk, actor.pk, key)
    earlier = previous(company, actor, key)
    if earlier is not None:
        if earlier.metadata.get('fingerprint') != fingerprint:
            raise QuickSaleError('Esa clave ya registró otro equipo. Vuelve a empezar.', code='idempotency_conflict',
                                 status=409)
        return (present_with or present)(company, branch, earlier.metadata['record'], authority=authority,
                                          replayed=True), False
    record = receive(company=company, branch=branch, body=body, actor=actor, request=request, authority=authority,
                     origin=origin)
    record_trail(company=company, actor=actor, request=request, key=key, fingerprint=fingerprint, record=record)
    return (present_with or present)(company, branch, record, authority=authority), True
