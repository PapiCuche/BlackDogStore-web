"""
INV-OPS-01 — «Recibir mercadería»: one confirmed operation receives several lines.

    THIS MODULE WRITES NOTHING OF ITS OWN.

A receipt is a branch, an optional supplier, a document reference, notes and 1–50 lines. A line is a
QUANTITY of an existing non-serialized product, or INDIVIDUAL UNITS of an existing serialized one. It
composes, in ONE transaction, the services every other screen already calls:

    devices       stock_unit_services.receive_units               (PURCHASE_ENTRY, IMEI / serial / duplicates)
    quantities    inventory_services.apply_manual_stock_movement  (PURCHASE_ENTRY, with the unit cost)
    labels        label_batches.create_for_movement               (LABEL-A: a batch per labelling movement)

so stock only ever enters through `inventory_services.create_stock_movement`, a device only through
`receive_units`, and a unit row is cleaned by `stock_unit_services.clean_unit` — the very rules of
«Registrar equipos». Creating a product or a variant is NOT part of this: a line that names an unknown
product is refused.

THE ORDER OF A CONFIRMATION (the view decides authority first):

    1. `normalise`   the body, with no database: types, bounds, ASCII ids and amounts. 400.
    2. lock + trail  one advisory lock per (company, author, key); the same key again replays or is 409.
    3. `resolve`     branch, supplier and products of THE CALLER'S company. A foreign id is 404.
    4. `check`       every line, by the same function the preview answers with. Any error → nothing written.
    5. `confirm`     the writes, in product order (two receipts over the same shelves queue, never deadlock),
                     the label batches (a savepoint each: a failure marks `label_error`, never undoes stock),
                     and the trail — an audit line with the key, a fingerprint of the normalised body and a
                     RECORD of ids and counts. Never a serial, an IMEI or an amount.

`present` turns a record into the answer, for the first call and for every replay alike.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import Counter
from decimal import Decimal

from django.db import connection

from . import inventory_services, label_batches
from . import stock_unit_services as units_service
from .inventory_services import InventoryError
from .models import AdminAuditLog, Product, ProductBarcode, StockMovement, Supplier

KIND_QUANTITY = 'quantity'
KIND_UNITS = 'units'
KINDS = (KIND_QUANTITY, KIND_UNITS)

MAX_LINES = 50
MAX_QUANTITY = 100_000
MAX_DOCUMENT = 60
MAX_NOTES = 300
MAX_KEY = 64
#: Primary keys are 32-bit: anything above cannot name a row, and some drivers raise instead of answering «none».
MAX_ID = 2_147_483_647
MAX_DEVICES = units_service.MAX_BATCH

AUDIT_ACTION = 'inventory_receipt_confirmed'
AUDIT_TARGET = 'inventory_receipt'

COSTS_REQUIRED = 'Necesitas el permiso «Ver costos y márgenes» para indicar un costo.'
_COST = re.compile(r'[0-9]{1,8}(\.[0-9]{1,2})?')
_DIGITS = re.compile(r'[0-9]{1,10}')

#: The row keys `receive_units` knows, and the names the wizard may use instead.
_ALIASES = {'serial_number': ('serial', 'serial_number'), 'cost': ('unit_cost', 'cost')}


class ReceiptError(Exception):
    """Nothing was written. Views render it as `body()` with `status`."""

    def __init__(self, detail: str, *, status: int = 400, code: str = 'invalid', field: str = '', line=None):
        super().__init__(detail)
        self.detail, self.status, self.code, self.field, self.line = detail, status, code, field, line

    def body(self) -> dict:
        body = {'detail': self.detail, 'code': self.code}
        if self.field:
            body['field'] = self.field
        if self.line is not None:
            body['line'] = self.line
        return body


class ReceiptInvalid(ReceiptError):
    """The body is well formed but at least one line cannot be received: `payload` is the preview's answer."""

    def __init__(self, payload: dict):
        super().__init__('Hay líneas que no se pueden recibir. No se recibió nada.', code='invalid_lines')
        self.payload = payload

    def body(self) -> dict:
        return {'detail': self.detail, 'code': self.code, **self.payload, 'ok': False}


# ---------------------------------------------------------------------------
# 1. The body, with no database
# ---------------------------------------------------------------------------

def _text(value, field: str, *, limit: int, ascii_only: bool = False) -> str:
    """A string, whitespace collapsed. None is empty; anything else than text is refused, never coerced."""
    if value is None:
        return ''
    if not isinstance(value, str):
        raise ReceiptError('Dato no válido.', field=field)
    try:
        value.encode('utf-8')  # a lone surrogate would blow up at the database, after the 200
    except UnicodeEncodeError:
        raise ReceiptError('Dato no válido.', field=field) from None
    text = ' '.join(value.split())
    if any(unicodedata.category(char) in ('Cc', 'Cs') for char in text) or (ascii_only and not text.isascii()):
        raise ReceiptError('Dato no válido.', field=field)
    if len(text) > limit:
        raise ReceiptError(f'Admite hasta {limit} caracteres.', field=field)
    return text


def _id(value, field: str, *, required: bool = False):
    """A primary key: a positive JSON integer, or the ASCII digits of one. Nothing else."""
    if value is None or value == '':
        if required:
            raise ReceiptError('Identificador no válido.', field=field)
        return None
    if isinstance(value, bool):
        raise ReceiptError('Identificador no válido.', field=field)
    if isinstance(value, str) and _DIGITS.fullmatch(value.strip()):
        value = int(value.strip())
    if not isinstance(value, int) or not 1 <= value <= MAX_ID:
        raise ReceiptError('Identificador no válido.', field=field)
    return value


def _cost(value, field: str, *, may_view_costs: bool):
    """An amount as ASCII digits with at most two decimals, or None. Refused without `costs.view`."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if not may_view_costs:
        raise ReceiptError(COSTS_REQUIRED, code='costs_forbidden', field=field)
    if isinstance(value, bool) or isinstance(value, float) or not isinstance(value, (int, str)):
        raise ReceiptError('El costo no es un importe válido.', field=field)
    text = str(value).strip()
    if not _COST.fullmatch(text):
        raise ReceiptError('El costo no es un importe válido (hasta 2 decimales).', field=field)
    return str(Decimal(text).quantize(Decimal('0.01')))


def _first(raw: dict, names):
    for name in names:
        if raw.get(name) is not None:
            return raw[name]
    return None


def _unit(raw, where: str, *, may_view_costs: bool) -> dict:
    if not isinstance(raw, dict):
        raise ReceiptError('Datos del equipo no válidos.', field=where)
    row = {}
    for key, limit in (('serial_number', 80), ('imei', 40), ('imei2', 40)):
        text = _text(_first(raw, _ALIASES.get(key, (key,))), f'{where}.{key}', limit=limit, ascii_only=True)
        if text:
            row[key] = text
    condition = _text(raw.get('condition'), f'{where}.condition', limit=20)
    if condition:
        row['condition'] = condition
    notes = _text(raw.get('notes'), f'{where}.notes', limit=2000)
    if notes:
        row['notes'] = notes

    battery = raw.get('battery_health')
    if battery is not None:
        if isinstance(battery, bool) or not isinstance(battery, (int, str)):
            raise ReceiptError('La batería se indica en % (0 a 100).', field=f'{where}.battery_health')
        battery = _text(str(battery), f'{where}.battery_health', limit=10, ascii_only=True)
        if battery:
            row['battery_health'] = battery

    grade = raw.get('cosmetic_grade')
    if grade is not None:
        if isinstance(grade, bool) or not isinstance(grade, (int, str)):
            raise ReceiptError('Estado estético no válido.', field=f'{where}.cosmetic_grade')
        grade = _text(str(grade) if isinstance(grade, int) else grade, f'{where}.cosmetic_grade',
                      limit=40, ascii_only=True)
        if grade.isdigit() and int(grade) > MAX_ID:
            raise ReceiptError('Estado estético no válido.', field=f'{where}.cosmetic_grade')
        if grade:
            row['cosmetic_grade'] = grade

    cost = _cost(_first(raw, _ALIASES['cost']), f'{where}.unit_cost', may_view_costs=may_view_costs)
    if cost is not None:
        row['cost'] = cost
    return row


def normalise(body, *, may_view_costs: bool) -> dict:
    """The body as the rest of the module reads it, or a `ReceiptError`. Touches no database."""
    if not isinstance(body, dict) or hasattr(body, 'getlist'):
        raise ReceiptError('El cuerpo de la petición debe ser un objeto JSON.')
    lines = body.get('lines')
    if not isinstance(lines, list) or not lines:
        raise ReceiptError('Indica al menos una línea.', field='lines')
    if len(lines) > MAX_LINES:
        raise ReceiptError(f'Se reciben hasta {MAX_LINES} líneas por vez.', field='lines')

    normalised = {
        'branch': _id(body.get('branch'), 'branch'),
        'supplier': _id(body.get('supplier'), 'supplier'),
        'document_ref': _text(body.get('document_ref'), 'document_ref', limit=MAX_DOCUMENT),
        'notes': _text(body.get('notes'), 'notes', limit=MAX_NOTES),
        'lines': [],
    }
    devices = 0
    for index, raw in enumerate(lines):
        where = f'lines[{index}]'
        if not isinstance(raw, dict):
            raise ReceiptError('Línea no válida.', field=where, line=index)
        kind = raw.get('kind')
        if kind not in KINDS:
            raise ReceiptError('El tipo de línea es «quantity» o «units».', field=f'{where}.kind', line=index)
        try:
            line = {'kind': kind, 'product': _id(raw.get('product'), f'{where}.product', required=True)}
            if kind == KIND_QUANTITY:
                quantity = raw.get('quantity')
                if isinstance(quantity, bool) or not isinstance(quantity, int) or not 1 <= quantity <= MAX_QUANTITY:
                    raise ReceiptError(f'La cantidad es un número entero entre 1 y {MAX_QUANTITY}.',
                                       field=f'{where}.quantity')
                line['quantity'] = quantity
                line['unit_cost'] = _cost(raw.get('unit_cost'), f'{where}.unit_cost', may_view_costs=may_view_costs)
            else:
                rows = raw.get('units')
                if not isinstance(rows, list) or not rows:
                    raise ReceiptError('Indica al menos un equipo.', field=f'{where}.units')
                devices += len(rows)
                if len(rows) > MAX_DEVICES or devices > MAX_DEVICES:
                    raise ReceiptError(f'Se reciben hasta {MAX_DEVICES} equipos por vez.', field=f'{where}.units')
                line['units'] = [
                    _unit(row, f'{where}.units[{n}]', may_view_costs=may_view_costs) for n, row in enumerate(rows)
                ]
        except ReceiptError as exc:
            exc.line = index
            raise
        normalised['lines'].append(line)
    return normalised


def fingerprint(normalised: dict) -> str:
    text = json.dumps(normalised, sort_keys=True, separators=(',', ':'), ensure_ascii=True)
    return hashlib.sha256(text.encode('ascii')).hexdigest()


# ---------------------------------------------------------------------------
# 3. The rows of THIS company
# ---------------------------------------------------------------------------

class Resolved:
    def __init__(self, branch, supplier, products):
        self.branch, self.supplier, self.products = branch, supplier, products


def resolve(company, normalised: dict, *, branch) -> Resolved:
    """
    The supplier and the products of `company`. `branch` was resolved by the view within the caller's grants.
    A row of another company answers exactly like one that does not exist.
    """
    supplier = None
    if normalised['supplier'] is not None:
        supplier = Supplier.objects.filter(company=company, pk=normalised['supplier']).first()
        if supplier is None:
            raise ReceiptError('Proveedor no encontrado.', status=404, code='not_found', field='supplier')
        if not supplier.is_active:
            raise ReceiptError('Ese proveedor está desactivado.', field='supplier')
    wanted = {line['product'] for line in normalised['lines']}
    products = {p.pk: p for p in Product.objects.filter(company=company, pk__in=wanted)}
    for index, line in enumerate(normalised['lines']):
        if line['product'] not in products:
            raise ReceiptError('Producto no encontrado.', status=404, code='not_found',
                               field=f'lines[{index}].product', line=index)
    return Resolved(branch, supplier, products)


# ---------------------------------------------------------------------------
# 4. One verdict per line (the preview's answer, and the gate of the confirmation)
# ---------------------------------------------------------------------------

def _codes(products) -> dict:
    return dict(
        ProductBarcode.objects.filter(product__in=list(products), is_primary=True, is_active=True)
        .values_list('product_id', 'code')
    )


def _product_payload(product, codes) -> dict:
    return {
        'id': product.pk, 'name': product.name, 'sku': codes.get(product.pk, ''),
        'is_serialized': product.is_serialized, 'price': str(product.price),
    }


def _issue(entry, unit_index, field, text, kind):
    message = f'Equipo {unit_index + 1}: {text}'
    entry['duplicates' if kind == 'duplicate' else 'errors'].append(message)
    entry['unit_issues'].append({'unit': unit_index, 'field': field or '', 'kind': kind, 'message': message})


def check(company, resolved: Resolved, normalised: dict) -> list[dict]:
    """
    A verdict for every line. Nothing is written: the cleaning is `stock_unit_services.clean_unit`, the
    conflicts are those `receive_units` will find again (and decide) under its lock.
    """
    codes = _codes(resolved.products.values())
    copies = Counter(line['product'] for line in normalised['lines'])
    seen_serials, seen_imeis, verdicts = set(), set(), []

    for index, line in enumerate(normalised['lines']):
        product = resolved.products[line['product']]
        entry = {
            'index': index, 'kind': line['kind'], 'product': _product_payload(product, codes), 'status': 'ok',
            'errors': [], 'warnings': [], 'will_receive': 0, 'labels': 0, 'duplicates': [], 'unit_issues': [],
        }
        if copies[product.pk] > 1:
            entry['warnings'].append(f'«{product.name}» aparece en más de una línea: cada línea es un ingreso aparte.')
        if not product.is_active:
            entry['errors'].append(f'«{product.name}» está desactivado: actívalo antes de recibirlo.')

        if line['kind'] == KIND_QUANTITY:
            if product.is_serialized:
                entry['errors'].append(
                    f'«{product.name}» se controla por número de serie: recíbelo como equipos '
                    '(kind «units»), uno por uno.')
            amount = line['quantity']
        else:
            if not product.is_serialized:
                entry['errors'].append(
                    f'«{product.name}» no se controla por número de serie: recíbelo por cantidad '
                    '(kind «quantity»).')
            elif product.is_active:
                for n, row in enumerate(line['units']):
                    _check_unit(company, product, row, n, entry, seen_serials, seen_imeis)
            amount = len(line['units'])

        if not entry['errors'] and not entry['duplicates']:
            entry['will_receive'], entry['labels'] = amount, amount
        else:
            entry['status'] = 'error'
        verdicts.append(entry)
    return verdicts


def _check_unit(company, product, row, n, entry, seen_serials, seen_imeis):
    try:
        cleaned = units_service.clean_unit(product, row)
    except InventoryError as exc:
        _issue(entry, n, getattr(exc, 'field', None), str(exc), 'error')
        return
    serial_key = (product.pk, cleaned['serial_number'])
    imeis = [value for value in (cleaned['imei'], cleaned['imei2']) if value]
    if serial_key in seen_serials:
        _issue(entry, n, 'serial_number', 'este número de serie está repetido en la misma recepción.', 'duplicate')
    if seen_imeis.intersection(imeis):
        _issue(entry, n, 'imei', 'este IMEI está repetido en la misma recepción.', 'duplicate')
    seen_serials.add(serial_key)
    seen_imeis.update(imeis)
    _, where = units_service.find_conflict(company, product, cleaned)
    if where == 'serial_number':
        _issue(entry, n, where, 'ese número de serie ya está registrado para este producto.', 'duplicate')
    elif where:
        _issue(entry, n, where, 'ese IMEI ya está registrado en otro equipo de la empresa.', 'duplicate')


def totals(verdicts: list[dict]) -> dict:
    quantity = sum(v['will_receive'] for v in verdicts if v['kind'] == KIND_QUANTITY)
    devices = sum(v['will_receive'] for v in verdicts if v['kind'] == KIND_UNITS)
    return {'lines': len(verdicts), 'quantity_units': quantity, 'devices': devices,
            'labels': sum(v['labels'] for v in verdicts)}


def _named(row):
    return None if row is None else {'id': row.pk, 'name': row.name}


def preview(company, resolved: Resolved, normalised: dict) -> dict:
    verdicts = check(company, resolved, normalised)
    return {
        'ok': all(v['status'] == 'ok' for v in verdicts),
        'branch': _named(resolved.branch), 'supplier': _named(resolved.supplier),
        'document_ref': normalised['document_ref'], 'totals': totals(verdicts), 'lines': verdicts,
    }


# ---------------------------------------------------------------------------
# 5. The writes
# ---------------------------------------------------------------------------

def reason_for(document_ref: str, supplier_name: str, notes: str) -> str:
    """The Kardex reason of every line: «Recepción [doc] · proveedor [name] · [notes]», cut to the field."""
    parts = ['Recepción' + (f' {document_ref}' if document_ref else '')]
    if supplier_name:
        parts.append(f'proveedor {supplier_name}')
    if notes:
        parts.append(notes)
    text = ' · '.join(parts)
    if len(text) > units_service.MAX_REASON:
        text = text[:units_service.MAX_REASON - 1].rstrip() + '…'
    return text


def confirm(*, company, resolved: Resolved, normalised: dict, actor, request=None, key: str, stamp: str):
    """
    Receive every line. ALL OF IT OR NONE: the caller runs this inside one transaction and an error leaves it.

    Returns `(record, trail)` — the record is ids and counts, never an identifier or an amount.
    """
    verdicts = check(company, resolved, normalised)
    if not all(v['status'] == 'ok' for v in verdicts):
        raise ReceiptInvalid(preview(company, resolved, normalised))

    branch = resolved.branch
    reason = reason_for(normalised['document_ref'], resolved.supplier.name if resolved.supplier else '',
                        normalised['notes'])
    lines = normalised['lines']
    written = {}
    # Shelves are locked in product order: two receipts that list them differently queue, never deadlock.
    for index in sorted(range(len(lines)), key=lambda i: (lines[i]['product'], i)):
        line, product = lines[index], resolved.products[lines[index]['product']]
        try:
            if line['kind'] == KIND_QUANTITY:
                movement = inventory_services.apply_manual_stock_movement(
                    branch=branch, product_id=product.pk, movement_type=StockMovement.PURCHASE_ENTRY,
                    quantity=line['quantity'], reason=reason, actor=actor, request=request,
                    unit_cost=line['unit_cost'])
                batch = label_batches.create_for_movement(movement, actor=actor)
                received, count = line['quantity'], 0
            else:
                created = units_service.receive_units(
                    branch=branch, product=product, units=line['units'], actor=actor, reason=reason,
                    request=request, movement_type=StockMovement.PURCHASE_ENTRY)
                movement = created.movement
                batch = label_batches.create_for_movement(movement, units=created, actor=actor)
                received = count = len(created)
        except InventoryError as exc:
            # The race the check could not see (another person received the same device a moment ago).
            raise ReceiptError(str(exc), status=getattr(exc, 'status', None) or 400,
                               code=getattr(exc, 'code', None) or 'invalid', field=getattr(exc, 'field', None) or '',
                               line=index) from None
        written[index] = {
            'index': index, 'kind': line['kind'], 'product_id': product.pk, 'product_name': product.name,
            'received': received, 'units': count, 'movement_id': movement.pk,
            'label_batch_id': batch.pk if batch else None,
            'label_error': batch is label_batches.FAILED,
        }

    record = {
        'branch_id': branch.pk, 'branch_name': branch.name,
        'supplier_id': resolved.supplier.pk if resolved.supplier else None,
        'supplier_name': resolved.supplier.name if resolved.supplier else '',
        'document_ref': normalised['document_ref'], 'totals': totals(verdicts),
        'lines': [written[i] for i in range(len(lines))],
    }
    trail = AdminAuditLog.log(
        actor=actor, action=AUDIT_ACTION, target_type=AUDIT_TARGET,
        target_id=record['lines'][0]['movement_id'],
        metadata={
            'idempotency_key': key, 'fingerprint': stamp, 'company_id': company.pk, 'branch_id': branch.pk,
            'supplier_id': record['supplier_id'], 'document_ref': record['document_ref'], 'record': record,
        },
        request=request, company=company,
    )
    return record, trail


# ---------------------------------------------------------------------------
# The answer, and the idempotency trail
# ---------------------------------------------------------------------------

def receipt_id(trail) -> str:
    return f'rcp-{trail.pk}'


def present(company, record: dict, trail, *, replayed: bool = False) -> dict:
    ids = [line['label_batch_id'] for line in record['lines'] if line['label_batch_id']]
    found = {item['id']: item for item in label_batches.summaries(company, ids)} if ids else {}
    lines, batches = [], []
    for line in record['lines']:
        summary = None
        failed = line['label_error']
        if failed:
            summary = label_batches.failures(1)[0]
        elif line['label_batch_id']:
            summary = found.get(line['label_batch_id'])
            if summary is None:  # the batch could not be read back: say so, never invent it
                summary, failed = label_batches.failures(1)[0], True
        if summary is not None:
            batches.append(summary)
        lines.append({
            'index': line['index'], 'kind': line['kind'],
            'product': {'id': line['product_id'], 'name': line['product_name']},
            'received': line['received'], 'movement_id': line['movement_id'], 'units': line['units'],
            'label_batch': summary, 'label_error': failed,
        })
    answer = {
        'receipt_id': receipt_id(trail),
        'branch': {'id': record['branch_id'], 'name': record['branch_name']},
        'supplier': (None if record['supplier_id'] is None
                     else {'id': record['supplier_id'], 'name': record['supplier_name']}),
        'document_ref': record['document_ref'], 'totals': record['totals'], 'lines': lines,
        'label_batches': batches, 'label_error': any(line['label_error'] for line in lines),
    }
    if replayed:
        answer['replayed'] = True
    return answer


def previous(company, actor, key):
    """The receipt this author already confirmed with this key, or None."""
    return (
        AdminAuditLog.objects
        .filter(company=company, actor=actor, action=AUDIT_ACTION, metadata__idempotency_key=key)
        .order_by('-pk').first()
    )


def lock(company_id, actor_id, key) -> None:
    """Two requests with one key take turns: held until the surrounding transaction ends."""
    if connection.vendor != 'postgresql':
        return
    digest = hashlib.sha256(f'inventory-receipt|{company_id}|{actor_id}|{key}'.encode()).digest()[:8]
    with connection.cursor() as cursor:
        cursor.execute('SELECT pg_advisory_xact_lock(%s)', [int.from_bytes(digest, 'big', signed=True)])
