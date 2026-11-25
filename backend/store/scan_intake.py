"""
SCAN-TO-STOCK-01 — «Registrar equipo por escaneo» (docs/adr-scan-to-stock.md, docs/carga-stock-real.md §13).

    THIS MODULE WRITES NOTHING OF ITS OWN. IT IS NOT A SECOND REGISTRATION ENGINE.

A clerk registers one physical device by reading its barcodes with a keyboard-wedge scanner. Two questions, and the
services that already answer them:

  «what did I just read?»   `check` — `scan_classifier.classify` says what the code IS (IMEI, EID, MEID, commercial
                            code, internal label, serial, or «not sure»), then the code is looked up INSIDE THE
                            CALLER'S COMPANY ONLY: a device that already holds it (`StockUnit`, any status but voided —
                            the rule of `stock_unit_services._already_in_stock`) and a product whose commercial code it
                            is (`unit_codes.product_code_holding`, LABEL-01/02). Another company's devices and codes are
                            never read, so nothing about them can be told. A device in a branch the caller does not
                            reach is said to exist (the reception would refuse it anyway) without its branch or product.

  «register it»             `receive_once` — a NEW device goes through the till's «Llegó el equipo»
                            (`pos_device_services.receive_once`, origin `scan`): the variant found or created from the
                            model's palette, the paid order that waits for that variant presented before free stock
                            (POS-ORDER-ARRIVAL-01, 409 `pending_order_line`), `receive_units`, the Kardex line, the
                            label batch and the idempotency trail. A USED device goes through
                            `stock_unit_services.receive_units` into an existing used variant, with the gates of
                            «Registrar equipos» (`stock_unit_views`): its price and its warranty need `products.manage`,
                            its cost `costs.view`; it enters pending inspection and unpublished, whoever receives it.

Nothing is confirmed by a scan: the screen sends `receive_once` only when the clerk presses «Confirmar ingreso».
"""
from __future__ import annotations

import hashlib
import re

from django.db import connection, transaction
from django.db.models import Q

from . import label_batches, scan_classifier
from .cost_services import PRICES_FORBIDDEN, states_a_cost
from . import pos_device_services as quick
from . import stock_unit_services as units
from .inventory_services import InventoryError
from .models import AdminAuditLog, Branch, Product, StockMovement, StockUnit

NEW, USED = 'new', 'used'
AUDIT_ACTION = 'scan_intake_used_received'

#: The keys of a used device's row this screen sends on to `receive_units` (its own cleaners decide each one).
USED_KEYS = ('serial_number', 'imei', 'imei2', 'cost', 'notes', 'battery_health', 'cosmetic_grade', 'functional_state',
             'defects', 'accessories', 'sale_price', 'warranty_days', 'public_notes')

ScanIntakeError = quick.QuickSaleError


# ---------------------------------------------------------------------------
# «What did I just read?»
# ---------------------------------------------------------------------------

def _standing(unit) -> tuple[str, str]:
    """(code, sentence) of a device that already holds an identifier — why it cannot enter again."""
    name = unit.label_code or f'#{unit.pk}'
    if unit.status == StockUnit.Status.SOLD:
        return 'sold', (f'Ese identificador es del equipo {name}, que se vendió. No se vuelve a registrar: si el cliente '
                        'lo devolvió, entra con la devolución de su pedido.')
    if unit.status == StockUnit.Status.WRITTEN_OFF:
        return 'written_off', (f'Ese identificador es del equipo {name}, dado de baja: la baja no libera su IMEI ni su '
                               'serie. Si vuelve a la tienda, se resuelve en su ficha, no con un ingreso nuevo.')
    return 'registered', f'Ese identificador ya está registrado en el equipo {name} ({unit.get_status_display().lower()}).'


ELSEWHERE = 'Ese identificador ya está registrado en un equipo de otra sucursal de la empresa.'


def _unit_brief(unit, visible_ids) -> dict:
    if unit.branch_id not in visible_ids:
        # Review B7 — in the company, outside the caller's branches: it exists (the reception refuses it). Not its
        # status (sold, written off), its product or its branch.
        return {'standing': 'elsewhere', 'detail': ELSEWHERE}
    code, detail = _standing(unit)
    return {'standing': code, 'status': unit.status, 'status_label': unit.get_status_display(), 'detail': detail,
            'id': unit.pk, 'label_code': unit.label_code or '', 'product_id': unit.product_id,
            'product_name': unit.product.name, 'branch_name': unit.branch.name}


class _AllBranches:
    """Every branch id: what a company-wide actor reaches (review R3)."""

    def __contains__(self, _branch_id) -> bool:
        return True


def _product_brief(product) -> dict:
    return {'id': product.pk, 'name': product.name, 'model_id': product.product_model_id,
            'condition_line': product.condition_line, 'is_serialized': product.is_serialized,
            'requires_imei': product.requires_imei, 'is_active': product.is_active,
            'archived': product.archived_at is not None,
            'option_ids': sorted(product.variant_options.values_list('option_id', flat=True))}


def check(*, company, user, code, product_id=None, choice=None, expected='') -> dict:
    """
    What `code` is and what THIS company already has under it. Reads only.

    `choice` (`imei`, `serial` or `gtin`): what the clerk said a doubtful code is — checked by the reception's own
    rules (`scan_classifier.as_identifier`), never taken on trust. `product_id`: the variant being registered, so a
    serial is judged as the reception judges it (unique within a variant; in another variant, a warning).
    `expected`: the slot the screen waits for (`scan_classifier.SLOTS`), which alone makes fifteen Luhn digits a sure
    IMEI (review B3).

    Products and devices of branches the caller does not reach are never named (review B7/B9).
    """
    from . import unit_codes

    if expected not in ('', None, *scan_classifier.SLOTS):
        raise ScanIntakeError('Casilla esperada no válida.', field='expected')
    reading = scan_classifier.classify(code, expected=expected or '')
    if choice not in (None, ''):
        if choice not in scan_classifier.CHOICES:
            raise ScanIntakeError('Elige si el código es el IMEI, la serie o el código comercial.', field='choice')
        if choice == scan_classifier.GTIN:
            value = reading.value or str(code or '').strip().upper()
            reading = scan_classifier.Reading(raw=reading.raw, value=value, kind=scan_classifier.GTIN, certain=True,
                                              detail=scan_classifier.DETAIL[scan_classifier.GTIN])
        else:
            try:
                value = scan_classifier.as_identifier(code, choice)
            except scan_classifier.ScanError as exc:
                raise ScanIntakeError(str(exc), code='invalid_choice', field=exc.field or 'choice') from None
            reading = scan_classifier.Reading(raw=reading.raw, value=value, kind=choice, certain=True,
                                              detail=scan_classifier.DETAIL[choice])

    answer = {'reading': reading.as_dict(), 'product': None, 'unit': None, 'conflict': None, 'serial_elsewhere': '',
              'serial_elsewhere_hidden': False}
    value = reading.value
    # Review B8: an EID (or a MEID / IMEISV nobody could call a serial or a product code) names nothing here.
    could_be_other = {scan_classifier.SERIAL, scan_classifier.GTIN} & set(reading.alternatives)
    if not value or (reading.kind in (scan_classifier.EID, scan_classifier.IMEISV, scan_classifier.MEID)
                     and not could_be_other):
        return answer

    holder = unit_codes.product_code_holding(company.pk, value)
    if holder is not None:
        # A commercial code of this company names its product, whatever its shape: the till looks products up first.
        answer['product'] = _product_brief(holder.product)
        if reading.kind != scan_classifier.GTIN:
            answer['conflict'] = {
                'code': 'product_code',
                'detail': f'«{value}» es un código del producto «{holder.product.name}»: identifica la variante, '
                          'nunca un equipo.'}
        return answer
    if reading.kind == scan_classifier.GTIN:
        return answer

    # Review R3: masked only for an actor restricted to some branches; a company-wide one sees every branch.
    reach = quick.reachable_branch_ids(company, user)
    visible_ids = reach if reach is not None else _AllBranches()
    live = (StockUnit.objects.filter(company=company).exclude(status=StockUnit.Status.VOIDED)
            .select_related('product', 'branch'))
    found = None
    if reading.kind == scan_classifier.LABEL_CODE:
        found = live.filter(label_code=value).first()
    if found is None and reading.kind == scan_classifier.IMEI:
        found = live.filter(Q(imei=value) | Q(imei2=value)).order_by('pk').first()
    could_be_serial = reading.kind == scan_classifier.SERIAL or scan_classifier.SERIAL in reading.alternatives
    if found is None and could_be_serial:
        labelled = live.filter(label_code=value).first() if reading.kind != scan_classifier.LABEL_CODE else None
        if labelled is not None:
            # Review R2: out of the caller's reach, only that the code exists — not what it is of.
            answer['conflict'] = {'code': 'label_code', 'detail': (
                f'«{value}» es el código interno de otro equipo de esta empresa.' if labelled.branch_id in visible_ids
                else f'«{value}» ya existe en la empresa como identificador de otro equipo: revisa el dato.')}
            return answer
        same = live.filter(serial_number=value)
        product = Product.objects.filter(company=company, pk=product_id).first() if product_id else None
        if product is not None:
            found = same.filter(product=product).order_by('pk').first()
            others = same.exclude(product=product)
        else:
            # No variant chosen yet: a serial is unique per variant, so another device with it is a warning, not a no.
            others = same
        if found is None:
            # INV-SERIAL-01's warning, naming the variant only when the device is in a branch the caller reaches (B9).
            named = others if reach is None else others.filter(branch_id__in=reach)
            seen = named.order_by('pk').values_list('product__name', flat=True).first()
            answer['serial_elsewhere'] = seen or ''
            answer['serial_elsewhere_hidden'] = not seen and others.exists()
    if found is not None:
        answer['unit'] = _unit_brief(found, visible_ids)
    return answer


# ---------------------------------------------------------------------------
# What the screen offers for a model's USED devices
# ---------------------------------------------------------------------------

def used_variants(company, model) -> list[dict]:
    """The model's used-line variants a device can enter: active, not archived. A draft is said, not hidden."""
    rows = (Product.objects.filter(company=company, product_model=model, condition_line=Product.CONDITION_USED,
                                   is_active=True, archived_at__isnull=True)
            .prefetch_related('variant_options').order_by('name', 'pk'))
    return [{'id': p.pk, 'name': p.name, 'is_serialized': p.is_serialized, 'requires_imei': p.requires_imei,
             'pending_draft': p.is_pending_draft,
             'option_ids': sorted(link.option_id for link in p.variant_options.all())} for p in rows]


# ---------------------------------------------------------------------------
# «Register it» — a used device
# ---------------------------------------------------------------------------

def _used_variant(company, body):
    raw = body.get('product_id')
    product = Product.objects.filter(company=company, pk=quick._pk(raw, 'product_id', label='Variante')).first()
    if product is None:
        raise ScanIntakeError('Variante no encontrada.', code='not_found', field='product_id', status=404)
    if product.condition_line != Product.CONDITION_USED:
        raise ScanIntakeError(f'«{product.name}» no es una variante de seminuevos.', code='not_used_line',
                              field='product_id')
    if product.archived_at is not None:
        from .variant_lifecycle import archived_refusal

        raise ScanIntakeError(archived_refusal(product), code='variant_archived', field='product_id', status=409)
    if not product.is_active:
        raise ScanIntakeError(f'«{product.name}» está inactivo: actívalo antes de registrar equipos.',
                              code='variant_inactive', field='product_id', status=409)
    if not product.is_serialized:
        raise ScanIntakeError(f'«{product.name}» no se controla por número de serie: actívalo en Inventario › Equipos '
                              'antes de registrar equipos.', code='not_serialized', field='product_id', status=409)
    return product


def receive_used(*, company, branch, body, actor, request=None, authority: quick.Authority) -> dict:
    """A used device into an existing used variant. The caller holds the transaction. Returns the RECORD."""
    if not authority.receive:
        raise ScanIntakeError(quick.RECEIVE_FORBIDDEN, code='receive_forbidden', status=403)
    if body.get('order_item') not in (None, ''):
        # The till's delivery to an order takes the identifiers only; a used device's ficha would be lost.
        raise ScanIntakeError('Un seminuevo pedido al proveedor se recibe desde su pedido, con su ficha completa.',
                              code='used_order_line', field='order_item')
    method = quick._method(body)
    confirmed = quick._confirmation(body)
    product = _used_variant(company, body)
    row = {key: body[key] for key in USED_KEYS if body.get(key) not in (None, '')}
    row['condition'] = StockUnit.Condition.USED
    identifiers = quick._device(body)
    row.update({key: value for key, value in identifiers.items() if value})
    if states_a_cost(row.get('cost')) and not authority.view_costs:
        raise ScanIntakeError(quick.COSTS_FORBIDDEN, code='costs_forbidden', field='cost', status=403)
    if row.get('sale_price') is not None and not authority.set_price:
        raise ScanIntakeError('No tienes permiso para fijar precios de venta: registra el equipo sin precio.',
                              code=PRICES_FORBIDDEN, field='sale_price', status=403)
    supplier = quick._supplier(company, body, authority)
    duplicate = quick.duplicate(company, identifiers, product, actor=actor)
    if duplicate is not None:
        raise duplicate
    # Review B10 — POS-ORDER-ARRIVAL-01's rule on this door too: a paid order waiting here for this variant is
    # presented (409 with its lines) before the device may enter as free stock, which then needs a reason.
    waiting = quick._refuse_over_waiting(company, actor, product, branch, confirmed)
    try:
        received = units.receive_units(
            branch=branch, product=product, units=[row], actor=actor, reason=quick.REASONS[quick.ORIGIN_SCAN],
            request=request, movement_type=StockMovement.PURCHASE_ENTRY, supplier=supplier,
            # IMPORT-WARRANTY-AUTHORITY (#432): a warranty is the price's authority.
            may_set_warranty=authority.set_price,
        )
    except InventoryError as exc:
        found = quick.duplicate(company, identifiers, product, actor=actor)
        if found is not None and getattr(exc, 'field', None) in ('imei', 'imei2', 'serial_number', None):
            raise found from None
        code = getattr(exc, 'code', None) or 'invalid'
        status = getattr(exc, 'status', None) or 400
        if code == PRICES_FORBIDDEN:
            status = 403
        raise ScanIntakeError(str(exc), code=code, field=getattr(exc, 'field', None) or '', status=status) from None
    batch = label_batches.create_for_movement(received.movement, units=received, actor=actor)
    unit = received[0]
    if waiting:
        # Ids and the reason, never the customer (the till's audit line).
        AdminAuditLog.log(
            actor=actor, action=quick.OVER_PENDING_AUDIT_ACTION, target_type='stock_unit', target_id=unit.pk,
            request=request, company=company,
            metadata={'reason': confirmed, 'product_id': product.pk, 'branch_id': branch.pk,
                      'order_item_ids': [line['order_item'] for line in waiting],
                      'order_ids': [line['order_id'] for line in waiting]})
    return {'mode': 'stock', 'branch_id': branch.pk, 'model_id': product.product_model_id, 'product_id': product.pk,
            'product_created': False, 'unit_id': unit.pk, 'movement_ids': [received.movement.pk],
            'label_batch_ids': [batch.pk] if batch and batch is not label_batches.FAILED else [],
            'label_failed': batch is label_batches.FAILED, 'supplier_id': supplier.pk if supplier else None,
            'price_changed': False, 'order_id': None, 'order_item_id': None, 'method': method,
            'over_pending_order_items': [line['order_item'] for line in waiting], 'origin': quick.ORIGIN_SCAN,
            'condition': USED}


SERIAL_NOT_A_SERIAL = 'serial_not_a_serial'


def refuse_serial_that_is_not_one(body: dict) -> None:
    """
    Review B2 — on the scan door the serial slot never takes another identifier. An EID, a sure commercial code, or
    this device's own IMEI: always refused. Fifteen Luhn digits, an IMEISV or a MEID: refused unless the clerk said
    «Es la serie» when asked (`serial_confirmed: true`) — one numeric serial in ten passes Luhn.
    """
    raw = body.get('serial_number')
    if not isinstance(raw, str) or not raw.strip():
        return
    reading = scan_classifier.classify(raw)
    own = {re.sub(r'[\s.\-/]', '', str(body.get(k) or '')) for k in ('imei', 'imei2')} - {''}
    confirmed = body.get('serial_confirmed') is True
    refused = (reading.kind == scan_classifier.EID
               or (reading.kind == scan_classifier.GTIN and reading.certain)
               or reading.value in own
               or (reading.kind in (scan_classifier.IMEI, scan_classifier.IMEISV, scan_classifier.MEID)
                   and not confirmed))
    if refused:
        what = 'es el IMEI de este mismo equipo' if reading.value in own else _NOT_A_SERIAL[reading.kind]
        raise ScanIntakeError(f'Eso no es la serie: {what}. Lee la serie del equipo o márcala «No tiene».',
                              code=SERIAL_NOT_A_SERIAL, field='serial_number')


_NOT_A_SERIAL = {
    scan_classifier.IMEI: 'tiene la forma de un IMEI (si de verdad es la serie, elige «Es la serie» al leerla)',
    scan_classifier.IMEISV: 'parece un IMEISV', scan_classifier.MEID: 'parece un MEID',
    scan_classifier.EID: 'es el EID de la eSIM', scan_classifier.GTIN: 'es un código comercial (UPC/EAN)',
}


# ---------------------------------------------------------------------------
# The answer, and the idempotency of a used device (a new one keeps the till's trail)
# ---------------------------------------------------------------------------

def present(company, branch, record: dict, *, authority: quick.Authority, replayed: bool = False) -> dict:
    """The device that entered, as «Inventario › Equipos» shows it, with its label batch. Never a cost without
    `costs.view`; the supplier only to who sees suppliers or costs (D-F-3)."""
    from . import stock_unit_views
    from .models import LabelBatch

    unit = (StockUnit.objects.select_related('product', 'branch', 'supplier', 'cosmetic_grade')
            .prefetch_related('photos').get(company=company, pk=record['unit_id']))
    payload = stock_unit_views.unit_payload(unit, include_cost=authority.view_costs,
                                            include_supplier=authority.open_suppliers or authority.view_costs)
    ids = record.get('label_batch_ids') or []
    batch = LabelBatch.objects.filter(pk=ids[0]).first() if ids else (
        label_batches.FAILED if record.get('label_failed') else None)
    used = unit.condition == StockUnit.Condition.USED
    warnings = []
    if used:
        warnings.append('El seminuevo quedó pendiente de inspección y sin publicar: no se ofrece como listo hasta '
                        'aprobar su inspección (fotos reales y estado en Inventario › Seminuevos).')
    elif record['mode'] != 'order' and unit.product.price <= 0:
        warnings.append('Esta variante no tiene precio de venta: la caja no la cobrará hasta que un usuario '
                        'autorizado lo fije.')
    entered = Branch.objects.filter(company=company, pk=record.get('branch_id')).first() or branch
    return {
        'replayed': replayed, 'mode': record['mode'], 'condition': USED if used else NEW,
        'unit': payload, 'branch': {'id': entered.pk, 'name': entered.name},
        'created_variant': bool(record.get('product_created')),
        'price_changed': bool(record.get('price_changed')),
        'order': None if record['mode'] != 'order' else {'id': record['order_id'], 'item_id': record['order_item_id']},
        'method': record.get('method') or 'manual',
        'warnings': warnings,
        **label_batches.answer(batch),
    }


def _lock_key(company_id, actor_id, key) -> None:
    if connection.vendor != 'postgresql':
        return
    digest = hashlib.sha256(f'scan-intake|{company_id}|{actor_id}|{key}'.encode()).digest()[:8]
    with connection.cursor() as cursor:
        cursor.execute('SELECT pg_advisory_xact_lock(%s)', [int.from_bytes(digest, 'big', signed=True)])


def _present_for(company, branch, record, *, authority, replayed=False):
    return present(company, branch, record, authority=authority, replayed=replayed)


@transaction.atomic
def receive_once(*, company, branch, body, actor, request, authority: quick.Authority, key: str, fingerprint: str):
    """
    (answer, created). `condition` `new` (default) → the till's reception; `used` → `receive_used`. The same key and
    body again answer the same without writing; the same key with another body is 409.
    """
    if not isinstance(body, dict):
        raise ScanIntakeError('Datos no válidos.')
    condition = body.get('condition') or NEW
    if condition not in (NEW, USED):
        raise ScanIntakeError('Indica si el equipo es nuevo o seminuevo.', code='invalid_condition', field='condition')
    refuse_serial_that_is_not_one(body)
    if condition == NEW:
        forwarded = {k: v for k, v in body.items() if k not in ('condition', 'serial_confirmed')}
        return quick.receive_once(company=company, branch=branch, body=forwarded, actor=actor, request=request,
                                  authority=authority, key=key, fingerprint=fingerprint, origin=quick.ORIGIN_SCAN,
                                  present_with=_present_for)
    _lock_key(company.pk, actor.pk, key)
    earlier = (AdminAuditLog.objects.filter(company=company, actor=actor, action=AUDIT_ACTION,
                                            metadata__idempotency_key=key).order_by('-pk').first())
    if earlier is not None:
        if earlier.metadata.get('fingerprint') != fingerprint:
            raise ScanIntakeError('Esa clave ya registró otro equipo. Vuelve a empezar.', code='idempotency_conflict',
                                  status=409)
        return present(company, branch, earlier.metadata['record'], authority=authority, replayed=True), False
    record = receive_used(company=company, branch=branch, body=body, actor=actor, request=request,
                          authority=authority)
    # Ids and flags, never an identifier or an amount.
    AdminAuditLog.log(actor=actor, action=AUDIT_ACTION, target_type='stock_unit', target_id=record['unit_id'],
                      metadata={'idempotency_key': key, 'fingerprint': fingerprint, 'record': record},
                      request=request, company=company)
    return present(company, branch, record, authority=authority), True
