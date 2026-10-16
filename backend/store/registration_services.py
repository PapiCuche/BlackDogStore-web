"""
COMMERCE-ADMIN-UX-01 — «Registrar artículo»: one article, from one screen.

    THIS MODULE WRITES NOTHING OF ITS OWN.

It puts in ONE transaction the services every other screen already calls, in
the order a person would walk through them:

    product          AdminProductWriteSerializer      (the product form's rules)
    reference cost   admin_views._clean_reference_cost (A1)
    model / variant  device_catalog.create_model / set_variant / find_variant (DC2)
    code / SKU       ProductBarcode                    (the barcode screen's rules)
    by serial        stock_unit_services.set_serialized
    devices          stock_unit_services.receive_units (IMEI check digit, duplicates)
    quantities       inventory_services.apply_initial_stock, with the unit cost (DC1)
    supplier, offer  supply_services.create_supplier / create_offer / set_sale_mode (DC3)

So stock only ever enters through the writers of the Kardex, a device only
through `receive_units`, and goods of a supplier never touch a shelf: an
article «Disponible por proveedor» gets an offer and «Bajo pedido», no stock.

AUTHORITY IS DECIDED BY THE CALLER (`registration_views`), BEFORE THIS RUNS, and
handed in as facts (`may_adjust`, `may_view_costs`, `branch_for`). What is
decided here is only what the article itself asks for: a device needs
`inventory.adjust` (being counted by serial is a stock decision), a quantity on
a shelf needs it too, and any cost needs `costs.view`.

PUBLICATION. «Mostrar en e-commerce» obeys `product_publication`: an article
that lacks something is created HIDDEN and the answer says what it lacks. It is
the rule that already gates every write of many products at once.

`register` returns a RECORD — ids and flags, never an amount — which is what
the idempotency trail keeps; `present` turns a record into the answer.
"""
from __future__ import annotations

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction

from . import device_catalog, product_publication, supply_services
from . import stock_unit_services as units_service
from .inventory_services import InventoryError, apply_initial_stock
from .models import (
    AdminAuditLog, Product, ProductBarcode, ProductModel, StockMovement, StockUnit, Supplier, SupplierOffer,
    normalize_barcode,
)

KIND_NEW_DEVICE = 'new_device'
KIND_USED_DEVICE = 'used_device'
KIND_ACCESSORY = 'accessory'
KIND_SUPPLIER = 'supplier'
KINDS = (KIND_NEW_DEVICE, KIND_USED_DEVICE, KIND_ACCESSORY, KIND_SUPPLIER)
DEVICE_KINDS = (KIND_NEW_DEVICE, KIND_USED_DEVICE)

#: What the Kardex line of a registration says.
REASON = 'Registro de artículo'

#: Most rows of quantities in one registration: one per branch is the use.
MAX_STOCK_ROWS = 50

ADJUST_REQUIRED = 'Registrar stock o equipos con serie e IMEI requiere el permiso «Ajustar inventario».'
COSTS_REQUIRED = 'Necesitas el permiso «Ver costos y márgenes» para indicar un costo.'


class RegistrationError(Exception):
    """Nothing was written. `step` is the screen's step the problem belongs to."""

    def __init__(self, detail: str, *, step: str, field: str = '', status: int = 400, line=None,
                 code: str = 'invalid'):
        super().__init__(detail)
        self.detail = detail
        self.step = step
        self.field = field
        self.status = status
        self.line = line
        self.code = code

    def body(self) -> dict:
        body = {'detail': self.detail, 'step': self.step, 'code': self.code}
        if self.field:
            body['field'] = self.field
        if self.line is not None:
            body['line'] = self.line
        return body


def _section(body, name, kind=dict):
    value = body.get(name)
    if value is None:
        return None
    if not isinstance(value, kind):
        raise RegistrationError('Datos no válidos.', step=_STEP_OF.get(name, 'product'), field=name)
    return value


_STEP_OF = {'product': 'product', 'model': 'model', 'options': 'model', 'units': 'stock', 'unit': 'stock',
            'stock': 'stock', 'supplier': 'supplier', 'offer': 'supplier'}


def _filled(value) -> bool:
    return value not in (None, '') and not (isinstance(value, str) and not value.strip())


def cost_fields(body: dict) -> list[str]:
    """Every cost the body carries, by its path. Empty means «no cost stated»."""
    found = []
    product = body.get('product') if isinstance(body.get('product'), dict) else {}
    if _filled(product.get('reference_cost')):
        found.append('product.reference_cost')
    for name, key in (('stock', 'unit_cost'), ('units', 'cost')):
        rows = body.get(name) if isinstance(body.get(name), list) else []
        if any(isinstance(row, dict) and _filled(row.get(key)) for row in rows):
            found.append(f'{name}.{key}')
    unit = body.get('unit') if isinstance(body.get('unit'), dict) else {}
    if _filled(unit.get('cost')):
        found.append('unit.cost')
    offer = body.get('offer') if isinstance(body.get('offer'), dict) else {}
    if _filled(offer.get('agreed_cost')):
        found.append('offer.agreed_cost')
    return found


def _ids(values, *, step, field) -> list[int]:
    if values is None:
        return []
    if not isinstance(values, list) or any(isinstance(v, bool) or not isinstance(v, int) for v in values):
        raise RegistrationError('Opciones no válidas.', step=step, field=field)
    return sorted(set(values))


def _pk(value, *, step, field):
    if value in (None, ''):
        return None
    if isinstance(value, bool):
        raise RegistrationError('Identificador no válido.', step=step, field=field)
    try:
        return int(value)
    except (TypeError, ValueError):
        raise RegistrationError('Identificador no válido.', step=step, field=field) from None


# ---------------------------------------------------------------------------
# What the body asks for, checked BEFORE anything is written
# ---------------------------------------------------------------------------

def check_request(body: dict, *, may_adjust: bool, may_view_costs: bool) -> dict:
    """The shape of the body for its kind, and the authority it needs. Raises; writes nothing."""
    kind = body.get('kind')
    if kind not in KINDS:
        raise RegistrationError('Elige qué vas a registrar.', step='kind', field='kind')
    product = _section(body, 'product')
    if not product:
        raise RegistrationError('Faltan los datos del producto.', step='product', field='product')
    units = _section(body, 'units', list) or []
    unit = _section(body, 'unit')
    stock = _section(body, 'stock', list) or []
    supplier = _section(body, 'supplier')
    offer = _section(body, 'offer')
    model = _section(body, 'model')

    if kind in DEVICE_KINDS and not model:
        raise RegistrationError('Un equipo pertenece a un modelo: elígelo o crea uno.', step='model', field='model')
    if kind == KIND_ACCESSORY and model:
        raise RegistrationError('Un accesorio o repuesto no lleva modelo de equipo.', step='model', field='model')
    if kind != KIND_NEW_DEVICE and units:
        raise RegistrationError('Sólo un equipo nuevo se registra por lotes de equipos.', step='stock',
                                field='units')
    if kind == KIND_USED_DEVICE and not unit:
        raise RegistrationError('Un seminuevo se registra equipo por equipo: indica sus datos.', step='stock',
                                field='unit')
    if kind != KIND_USED_DEVICE and unit:
        raise RegistrationError('Sólo un seminuevo se registra como un equipo suelto.', step='stock', field='unit')
    if kind != KIND_ACCESSORY and stock:
        raise RegistrationError(
            'Las cantidades por sucursal son de los accesorios y repuestos. Un equipo entra con su serie; '
            'lo de un proveedor no entra en el stock propio.', step='stock', field='stock')
    if kind == KIND_USED_DEVICE and (supplier or offer):
        raise RegistrationError('Un seminuevo se vende por equipo, nunca bajo pedido.', step='supplier',
                                field='supplier')
    if kind == KIND_SUPPLIER and not (supplier and offer):
        raise RegistrationError('Indica el proveedor y su oferta.', step='supplier',
                                field='offer' if supplier else 'supplier')
    if bool(supplier) != bool(offer):
        raise RegistrationError('Un proveedor se registra con su oferta.', step='supplier',
                                field='offer' if supplier else 'supplier')
    if len(stock) > MAX_STOCK_ROWS:
        raise RegistrationError(f'Hasta {MAX_STOCK_ROWS} filas de stock por registro.', step='stock', field='stock')

    # Authority the article needs, on top of `products.manage` (the caller's).
    if (kind in DEVICE_KINDS or stock) and not may_adjust:
        raise RegistrationError(ADJUST_REQUIRED, step='stock', status=403, code='forbidden')
    costs = cost_fields(body)
    if costs and not may_view_costs:
        raise RegistrationError(COSTS_REQUIRED, step='costs', field=costs[0], status=403, code='forbidden')
    return {'kind': kind, 'product': product, 'units': units, 'unit': unit, 'stock': stock,
            'supplier': supplier, 'offer': offer, 'model': model}


# ---------------------------------------------------------------------------
# The steps
# ---------------------------------------------------------------------------

def _model(company, wanted, *, category_id, show_online, actor, request):
    """(model, created) — by id within this company, or by name (found, else created)."""
    if not wanted:
        return None, False
    if 'id' in wanted and wanted.get('id') not in (None, ''):
        pk = _pk(wanted.get('id'), step='model', field='model')
        model = ProductModel.objects.filter(company=company, pk=pk).first()
        if model is None:
            raise RegistrationError('Modelo no encontrado.', step='model', field='model', code='not_found')
        return model, False
    name = ' '.join(str(wanted.get('name') or '').split())
    if not name:
        raise RegistrationError('Indica el modelo.', step='model', field='model.name')
    existing = ProductModel.objects.filter(company=company, name__iexact=name).first()
    if existing is not None:
        return existing, False
    try:
        model = device_catalog.create_model(
            company=company, name=name, category_id=category_id, is_published_online=show_online,
            actor=actor, request=request,
        )
    except device_catalog.CatalogError as exc:
        raise RegistrationError(exc.args[0], step='model', field=f'model.{exc.field or "name"}') from None
    return model, True


def _product_errors(errors) -> RegistrationError:
    field, messages = next(iter(errors.items()))
    message = messages[0] if isinstance(messages, list) and messages else str(messages)
    if isinstance(message, dict):
        message = next(iter(message.values()))
    if field == 'non_field_errors':
        return RegistrationError(str(message), step='product', field='product')
    if field == 'slug':
        field = 'name'
    return RegistrationError(str(message), step='product', field=f'product.{field}')


def _create_product(company, data, *, show_online, require_image):
    """The product form's own serializer. Published only if the publication rule lets it."""
    from .serializers import AdminProductWriteSerializer

    payload = {
        'name': data.get('name'), 'price': data.get('price'), 'category': data.get('category'),
        'description': data.get('description') or '',
        'is_active': data.get('is_active', True) is not False,
    }
    ser = AdminProductWriteSerializer(data=payload, context={'company': company})
    if not ser.is_valid():
        raise _product_errors(ser.errors)
    valid = ser.validated_data
    problems = product_publication.problems_of(
        is_active=valid.get('is_active', True), price=valid.get('price'), has_image=False,
        has_category=valid.get('category') is not None, require_image=require_image,
    ) if show_online else []
    product = ser.save(company=company, inventory=0, is_published_online=show_online and not problems)
    return product, problems


def _reference_cost(product, raw):
    from .admin_views import _clean_reference_cost

    value, error = _clean_reference_cost(raw)
    if error:
        raise RegistrationError(error, step='product', field='product.reference_cost')
    if value is not None:
        product.reference_cost = value
        product.save(update_fields=['reference_cost', 'updated_at'])


def _barcode(company, product, raw):
    code = normalize_barcode(str(raw or ''))
    if not code:
        return None
    clash = ProductBarcode.objects.filter(company=company, code=code).exclude(product=product) \
        .select_related('product').first()
    if clash is not None:
        raise RegistrationError(f'Ese código ya identifica a «{clash.product.name}» en esta empresa.',
                                step='product', field='sku', status=409, code='duplicate')
    try:
        with transaction.atomic():
            barcode = ProductBarcode(company=company, product=product, code=code, is_primary=True)
            barcode.save()
    except DjangoValidationError as exc:
        messages = getattr(exc, 'messages', None) or ['Código no válido.']
        raise RegistrationError(messages[0], step='product', field='sku') from None
    except IntegrityError:
        raise RegistrationError('Ese código ya existe.', step='product', field='sku', status=409,
                                code='duplicate') from None
    return barcode


def _unit_row(row, *, condition, keys):
    if not isinstance(row, dict):
        raise RegistrationError('Datos del equipo no válidos.', step='stock', field='units')
    cleaned = {key: row.get(key) for key in keys if key in row}
    cleaned['condition'] = condition
    return cleaned


_NEW_UNIT_KEYS = ('serial_number', 'imei', 'imei2', 'cost', 'notes')
_USED_UNIT_KEYS = ('serial_number', 'imei', 'imei2', 'cost', 'notes', 'battery_health', 'cosmetic_grade',
                   'sale_price', 'public_notes')


def _receive(*, branch, product, rows, created, actor, request):
    try:
        received = units_service.receive_units(
            branch=branch, product=product, units=rows, actor=actor, reason=REASON, request=request,
            movement_type=StockMovement.INITIAL_STOCK if created else StockMovement.PURCHASE_ENTRY,
        )
    except InventoryError as exc:
        field = getattr(exc, 'field', None) or ''
        raise RegistrationError(str(exc), step='stock', field=field, line=getattr(exc, 'line', None)) from None
    return received


def _quantity(value, line):
    if isinstance(value, bool):
        value = None
    try:
        quantity = int(value)
    except (TypeError, ValueError):
        quantity = 0
    if quantity <= 0 or str(value).strip() != str(quantity):
        raise RegistrationError('La cantidad es un número entero mayor que cero.', step='stock',
                                field='stock.quantity', line=line)
    return quantity


def _supplier(company, wanted, *, actor, request):
    if wanted.get('id') not in (None, ''):
        pk = _pk(wanted.get('id'), step='supplier', field='supplier')
        supplier = Supplier.objects.filter(company=company, pk=pk).first()
        if supplier is None:
            raise RegistrationError('Proveedor no encontrado.', step='supplier', field='supplier', code='not_found')
        return supplier, False
    name = ' '.join(str(wanted.get('name') or '').split())
    existing = Supplier.objects.filter(company=company, name__iexact=name).first() if name else None
    if existing is not None:
        return existing, False
    fields = {k: wanted[k] for k in ('name', 'document_type', 'document_number', 'contact_name', 'phone', 'email',
                                     'notes') if k in wanted}
    try:
        return supply_services.create_supplier(company=company, actor=actor, request=request, **fields), True
    except supply_services.SupplyError as exc:
        raise RegistrationError(exc.detail, step='supplier', field=f'supplier.{exc.field or "name"}') from None


_OFFER_KEYS = ('agreed_cost', 'commercial_price', 'confirmed_quantity', 'valid_from', 'valid_until',
               'lead_time_days')


def _offer(company, supplier, product, wanted, *, may_view_costs, actor, request):
    fields = {k: wanted[k] for k in _OFFER_KEYS if k in wanted}
    try:
        offer = supply_services.create_offer(
            company=company, actor=actor, request=request, may_view_costs=may_view_costs,
            supplier_id=supplier.pk, product_id=product.pk, **fields,
        )
        supply_services.set_sale_mode(product, Product.SALE_MODE_STOCK_OR_ORDER, actor=actor, request=request)
    except supply_services.SupplyError as exc:
        raise RegistrationError(exc.detail, step='supplier', field=f'offer.{exc.field}' if exc.field else 'offer',
                                code=exc.code) from None
    return offer


# ---------------------------------------------------------------------------
# Register
# ---------------------------------------------------------------------------

@transaction.atomic
def register(*, company, body: dict, actor, request=None, may_adjust: bool, may_view_costs: bool,
             branch_for) -> dict:
    """
    Register one article. All of it or none of it.

    `branch_for(branch_id)` resolves a branch WITHIN THE CALLER'S GRANTS or
    raises `RegistrationError` (the view's `resolve_branch_for_user`).

    Returns the record (ids and flags; never an amount).
    """
    asked = check_request(body, may_adjust=may_adjust, may_view_costs=may_view_costs)
    kind, data = asked['kind'], asked['product']
    show_online = data.get('show_online') is True
    line = Product.CONDITION_USED if kind == KIND_USED_DEVICE else Product.CONDITION_NEW
    warnings: list[str] = []

    # Every branch is resolved before anything is written: a branch outside
    # the caller's grants refuses the whole registration.
    device_rows = asked['units'] or ([asked['unit']] if asked['unit'] else [])
    device_branch = branch_for(body.get('branch')) if device_rows else None
    stock_rows, seen_branches = [], set()
    for index, row in enumerate(asked['stock'], start=1):
        if not isinstance(row, dict):
            raise RegistrationError('Fila de stock no válida.', step='stock', field='stock', line=index)
        branch = branch_for(row.get('branch'))
        if branch.pk in seen_branches:
            raise RegistrationError('Esa sucursal está dos veces.', step='stock', field='stock.branch', line=index)
        seen_branches.add(branch.pk)
        stock_rows.append((branch, _quantity(row.get('quantity'), index), row.get('unit_cost'), index))

    category_id = _pk(data.get('category'), step='product', field='product.category')
    model, model_created = _model(company, asked['model'], category_id=category_id, show_online=show_online,
                                  actor=actor, request=request)
    option_ids = _ids(body.get('options'), step='model', field='options')

    product, problems, created = None, [], True
    if model is not None:
        existing = device_catalog.find_variant(company, {
            'model_id': model.pk, 'condition_line': line, 'variant_key': device_catalog.variant_key(option_ids),
        })
        if existing is not None:
            product, created = existing, False
            warnings.append(
                f'«{existing.name}» ya es esa variante de «{model.name}»: se le agregan los equipos y la oferta; '
                'su nombre, precio, código y publicación no cambian.')

    if product is None:
        require_image = product_publication.requires_image(company) if show_online else False
        product, problems = _create_product(company, data, show_online=show_online, require_image=require_image)
        if _filled(data.get('reference_cost')):
            _reference_cost(product, data.get('reference_cost'))
        if model is not None:
            try:
                product = device_catalog.set_variant(product, model_id=model.pk, condition_line=line,
                                                     option_ids=option_ids, actor=actor, request=request)
            except device_catalog.CatalogError as exc:
                raise RegistrationError(exc.args[0], step='model', field=exc.field or 'options') from None
        _barcode(company, product, data.get('sku'))
    elif show_online and not product.is_published_online:
        problems = product_publication.publication_problems(product)

    if kind in DEVICE_KINDS or (kind == KIND_SUPPLIER and body.get('track_units') is True):
        requires_imei = body.get('requires_imei', True) is not False
        if not product.is_serialized or (created and product.requires_imei != requires_imei):
            if not may_adjust:
                raise RegistrationError(ADJUST_REQUIRED, step='stock', status=403, code='forbidden')
            try:
                product = units_service.set_serialized(product, serialized=True, requires_imei=requires_imei,
                                                       actor=actor, request=request)
            except InventoryError as exc:
                raise RegistrationError(str(exc), step='stock', field='is_serialized') from None

    received, movements = [], []
    if device_rows:
        condition = StockUnit.Condition.USED if kind == KIND_USED_DEVICE else StockUnit.Condition.NEW
        keys = _USED_UNIT_KEYS if kind == KIND_USED_DEVICE else _NEW_UNIT_KEYS
        rows = [_unit_row(row, condition=condition, keys=keys) for row in device_rows]
        received = _receive(branch=device_branch, product=product, rows=rows, created=created,
                            actor=actor, request=request)
        movements = list(
            StockMovement.objects.filter(product=product, reference_type='stock_units',
                                         metadata__unit_ids__contains=[received[0].pk])
            .values_list('pk', flat=True)
        )
    for branch, quantity, unit_cost, index in stock_rows:
        try:
            movement = apply_initial_stock(branch=branch, product=product, quantity=quantity, actor=actor,
                                           reason=REASON, request=request, unit_cost=unit_cost)
        except InventoryError as exc:
            raise RegistrationError(str(exc), step='stock', field='stock.unit_cost', line=index) from None
        movements.append(movement.pk)

    supplier = offer = None
    supplier_created = False
    if asked['supplier']:
        supplier, supplier_created = _supplier(company, asked['supplier'], actor=actor, request=request)
        offer = _offer(company, supplier, product, asked['offer'], may_view_costs=may_view_costs,
                       actor=actor, request=request)

    product.refresh_from_db()
    return {
        'kind': kind,
        'product_id': product.pk, 'product_created': created,
        'model_id': model.pk if model else None, 'model_created': model_created,
        'unit_ids': [u.pk for u in received],
        'movement_ids': movements,
        'supplier_id': supplier.pk if supplier else None, 'supplier_created': supplier_created,
        'offer_id': offer.pk if offer else None,
        'publication': {
            'requested': show_online,
            'published': product.is_published_online,
            'problems': problems if show_online and not product.is_published_online else [],
        },
        'warnings': warnings,
    }


def present(company, record: dict, *, dry_run: bool = False, replayed: bool = False) -> dict:
    """The answer for a record: what was registered, where to see it. Ids only; no amount."""
    product = Product.objects.filter(company=company, pk=record['product_id']).first()
    model = ProductModel.objects.filter(company=company, pk=record['model_id']).first() \
        if record.get('model_id') else None
    supplier = Supplier.objects.filter(company=company, pk=record['supplier_id']).first() \
        if record.get('supplier_id') else None
    created = record['product_created']

    storefront = None
    if product is not None and product.is_active and product.is_published_online:
        storefront = (f'/modelo/{model.slug}' if model is not None and model.is_published_online
                      else f'/product/{product.slug}')

    def real(pk, was_created):
        return None if dry_run and was_created else pk

    answer = {
        'dry_run': dry_run,
        'replayed': replayed,
        'kind': record['kind'],
        'product': {
            'id': real(record['product_id'], created),
            'existing_id': None if created else record['product_id'],
            'created': created,
            'name': product.name if product else '',
            'slug': product.slug if product else '',
            'is_active': bool(product and product.is_active),
            'is_published_online': bool(product and product.is_published_online),
            'is_serialized': bool(product and product.is_serialized),
            'sale_mode': product.sale_mode if product else Product.SALE_MODE_STOCK,
            'condition_line': product.condition_line if product else Product.CONDITION_NEW,
        },
        'model': None if model is None else {
            'id': real(model.pk, record['model_created']), 'created': record['model_created'],
            'name': model.name, 'slug': model.slug,
        },
        'units': [{'id': None if dry_run else pk} for pk in record['unit_ids']],
        'movements': [] if dry_run else list(record['movement_ids']),
        'supplier': None if supplier is None else {
            'id': real(supplier.pk, record['supplier_created']), 'created': record['supplier_created'],
            'name': supplier.name,
        },
        'offer': None if not record.get('offer_id') else {'id': None if dry_run else record['offer_id']},
        'publication': dict(record['publication']),
        'warnings': list(record['warnings']),
        'links': {'product': None, 'units': None, 'storefront': None},
    }
    if not dry_run and product is not None:
        answer['links'] = {
            'product': f'/admin/products/{product.pk}',
            'units': f'/admin/inventory/units?product={product.pk}' if record['unit_ids'] else None,
            'storefront': storefront,
        }
    return answer


# ---------------------------------------------------------------------------
# The idempotency trail
# ---------------------------------------------------------------------------

AUDIT_ACTION = 'article_registered'


def previous(company, actor, key):
    """The registration this author already made with this key, or None."""
    return (
        AdminAuditLog.objects
        .filter(company=company, actor=actor, action=AUDIT_ACTION, metadata__idempotency_key=key)
        .order_by('-pk').first()
    )


def record_trail(*, company, actor, request, key, fingerprint, record):
    AdminAuditLog.log(
        actor=actor, action=AUDIT_ACTION, target_type='product', target_id=record['product_id'],
        metadata={'idempotency_key': key, 'fingerprint': fingerprint, 'record': record},
        request=request, company=company,
    )

