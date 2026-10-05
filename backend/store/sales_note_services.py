"""
Internal sales note service — Phase 6.0.

A SalesNote is an INTERNAL document for a paid order. It is NOT a SUNAT
electronic receipt, NOT fiscal numbering and carries no tax validity. Every PDF
must state that visibly.

Hard rules:
  - Only PAID orders get a note. pending_payment / failed / expired / cancelled
    are rejected.
  - One note per order (OneToOne) — get_or_create_sales_note is idempotent.
  - The internal correlativo is allocated by store.sequences, from the series
    that belongs to the order's own company (and branch, under branch scope).
  - The PDF never contains any payment-gateway identifier,
    payment_error, tokens, cookies or secrets.
  - Issuing a note never touches payment state and never touches inventory.
"""

from __future__ import annotations

import logging

from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from . import company_settings as _company_settings
from . import sequences as _sequences
from .models import Order, SalesNote
from .pdf_services import (
    _DOCUMENT_LABELS,
    _GENERIC_WARRANTY_NOTE,
    _RECEIPT_LABELS,
    _safe_slug,
    delivery_label,
    order_lines,
    order_presentation,
)

logger = logging.getLogger(__name__)

# Must appear visibly on every generated sales-note PDF.
SALES_NOTE_DISCLAIMER = (
    "Documento interno de venta. "
    "No válido como comprobante electrónico SUNAT."
)

_TITLE = "Nota de venta interna"


class SalesNoteError(Exception):
    """Business-rule violation. Views map this to HTTP 400."""


# ---------------------------------------------------------------------------
# Create / fetch
# ---------------------------------------------------------------------------

def get_or_create_sales_note(order: Order, actor=None) -> tuple[SalesNote, bool]:
    """
    Return (note, created). Idempotent — an order never gets a second note.

    Raises SalesNoteError if the order is not paid.
    """
    if not order.paid or order.status != Order.Status.PAID:
        raise SalesNoteError(
            'Solo se puede emitir una nota de venta interna para órdenes pagadas.'
        )

    existing = SalesNote.objects.filter(order=order).first()
    if existing:
        return existing, False

    with transaction.atomic():
        # LOCK ORDER: order first, sequence second. Every issuing path uses this
        # order and nothing uses the reverse — see store/sequences.py.
        #
        # The order lock is what makes issuance idempotent under concurrency:
        # two simultaneous requests for the SAME order both arrive here, one
        # waits, and the waiter finds the note already created and consumes NO
        # number. Allocating before this check would burn an ordinal on a note
        # that is never written.
        locked_order = Order.objects.select_for_update().get(pk=order.pk)
        existing = SalesNote.objects.filter(order=locked_order).first()
        if existing:
            return existing, False

        sequence = _sequences.resolve_sequence_for_order(locked_order)
        value, number = _sequences.allocate(sequence)

        note = SalesNote.objects.create(
            order=locked_order,
            sequence=sequence,
            sequence_value=value,
            number=number,
            status=SalesNote.STATUS_ISSUED,
            issued_at=timezone.now(),
            created_by=actor if getattr(actor, 'is_authenticated', False) else None,
            metadata={
                'order_id': locked_order.pk,
                'sequence_id': sequence.pk,
                'branch_id': sequence.branch_id,
                # The logotype the shop had when this note was issued, frozen
                # like the rest of its identity: a reprint keeps it. An empty
                # key says "issued without one", which is also a fact to keep.
                'logo_storage_key': _frozen_logo(locked_order.company),
            },
        )

    _queue_ticket(note)
    return note, True


def _frozen_logo(company) -> str:
    from . import fiscal_logo

    return fiscal_logo.snapshot(company).get('logo_storage_key', '')


def _queue_ticket(note) -> None:
    """
    La nota acaba de crearse sobre un pedido pagado: se encola su ticket.

    La cola decide si corresponde (local con impresora automática) y es
    idempotente. Un fallo al encolar no deshace la nota: se puede reimprimir.
    """
    try:
        from .printing import services as printing

        # Punto de guardado propio: si la cola falla, se deshace sólo esto y la
        # transacción de la venta sigue sana.
        with transaction.atomic():
            printing.enqueue_sales_note_ticket(note)
    except Exception:  # noqa: BLE001
        logger.warning('No se pudo encolar el ticket de la nota %s.', note.pk, exc_info=True)


def get_sales_note_filename(sales_note: SalesNote) -> str:
    """
    Safe ASCII filename for the download.

    Built from the company slug, filtered again here — the same rule as the
    order receipt. The tenant's free-text NAME never reaches a
    Content-Disposition header.
    """
    company = getattr(sales_note.order, 'company', None)
    slug = _safe_slug(getattr(company, 'slug', ''))
    number = _safe_slug(sales_note.number) or 'nota'
    return f'{slug}-nota-venta-{number}.pdf' if slug else f'nota-venta-{number}.pdf'


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------

def build_sales_note_context(sales_note: SalesNote) -> dict:
    """
    Plain-data dict for PDF rendering.

    Deliberately excludes every gateway identifier and payment_error — the PDF is
    handed to customers and staff and must not leak payment internals.
    """
    order = sales_note.order

    # Each line with its code and, for a serialized product, the devices that
    # actually left the stock in this sale.
    items = order_lines(order)

    address_parts = [p for p in (order.address_line, order.district, order.city) if p]

    # The seller on this note is the ORDER's company, frozen at sale time
    # (Phase 3), and `number` comes from that company's own series (Phase 2E).
    # Neither WHO issued this note nor WHAT it is numbered is a module constant.
    #
    # The stored string is read back verbatim, never re-derived from the series:
    # a prefix changed after issuance must not retroactively rewrite a document
    # somebody is already holding.
    identity = _company_settings.order_identity(order)
    pickup = _company_settings.order_pickup_location(order)
    tax = _tax_context(order)

    from .document_style import amount_in_words

    return {
        # Branch, payment, seller and the logotype frozen with this note. A
        # note issued before logotypes were frozen has no key and uses the
        # shop's current one.
        **order_presentation(
            order, frozen_logo_key=(sales_note.metadata or {}).get('logo_storage_key'),
        ),
        'product_count': len(items),
        'unit_count': sum(item['quantity'] for item in items),
        'amount_in_words': amount_in_words(tax['total'], tax['currency']),
        'title': _TITLE,
        'disclaimer': SALES_NOTE_DISCLAIMER,
        'number': sales_note.number,
        'issued_at': (
            sales_note.issued_at.strftime('%d/%m/%Y %H:%M') if sales_note.issued_at else '—'
        ),
        'order_id': order.pk,
        'customer_name': order.customer_name or '—',
        'customer_phone': order.customer_phone or '—',
        'customer_email': order.customer_email or '—',
        'document_label': _DOCUMENT_LABELS.get(order.document_type, '—'),
        'document_number': order.document_number or '—',
        'receipt_label': _RECEIPT_LABELS.get(order.receipt_type, '—'),
        'delivery_label': delivery_label(order.delivery_method, identity.city) or '—',
        'full_address': ', '.join(address_parts),
        'notes': order.notes or '',
        'items': items,
        'discount_amount': Decimal(str(order.discount_amount)),
        'total': Decimal(str(order.total)),
        # EL DESGLOSE SE LEE DE LA VENTA, NO SE RECALCULA. Un documento
        # reimpreso dentro de tres años tiene que seguir diciendo lo que dijo,
        # aunque para entonces la tasa vigente sea otra.
        'tax': tax,
        'store_name': identity.name,
        'store_legal_name': identity.legal_name,
        'store_ruc': identity.tax_id,
        'store_address': identity.legal_address,
        'store_city': identity.city,
        'store_phone': identity.phone,
        'store_email': identity.contact_email,
        'warranty_note': identity.warranty_policy_text or _GENERIC_WARRANTY_NOTE,
        'pickup_name': pickup.get('name', ''),
        'pickup_address': pickup.get('address', ''),
    }


def _tax_context(order) -> dict:
    """
    El desglose de esta venta, ya formateado para imprimir.

    UNA SOLA LÍNEA DE IMPUESTO, ROTULADA «IGV». El 18 % se compone por ley de
    IGV más Impuesto de Promoción Municipal, y la Ley N.º 32387 mueve ese
    reparto cada año hasta 2029 sin tocar el total. La representación impresa
    peruana muestra el tributo agregado, que es además lo que el comprador
    reconoce; separarlo obligaría a redondear dos veces y a que la suma dejara
    de cuadrar, a cambio de un detalle que el documento no necesita.
    """
    from .tax_services import TaxTreatment, breakdown_for_order, currency_symbol

    result = breakdown_for_order(order)
    percent = (result.tax_rate * Decimal('100')).normalize()
    labels = {
        TaxTreatment.TAXED: 'Op. gravada',
        TaxTreatment.EXEMPT: 'Op. exonerada',
        TaxTreatment.UNAFFECTED: 'Op. inafecta',
    }
    return {
        'currency': result.currency,
        # El código ISO es lo que se guarda; el símbolo es lo que se imprime.
        # Sin esto el documento mezclaba «S/ 999.00» en la tabla de productos con
        # «PEN 999.00» en los totales, tres líneas más abajo y en la misma hoja.
        'symbol': currency_symbol(result.currency),
        'subtotal': result.subtotal,
        'discount_amount': result.discount_amount,
        'taxable_amount': result.taxable_amount,
        'tax_amount': result.tax_amount,
        'tax_rate': result.tax_rate,
        'treatment': result.tax_treatment,
        'base_label': labels.get(result.tax_treatment, 'Op. gravada'),
        'tax_label': f'IGV ({percent:f}%)',
        # Una operación exonerada no lleva línea de impuesto: un «IGV S/ 0,00»
        # se lee como que se olvidaron de cobrarlo.
        'shows_tax': result.tax_treatment == TaxTreatment.TAXED,
        'total': result.total,
    }


def generate_sales_note_pdf(sales_note: SalesNote) -> bytes:
    """
    Build the internal sales-note PDF in memory. No file is written to disk.

    Raises SalesNoteError if the underlying order is no longer paid.
    """
    order = sales_note.order
    if not order.paid or order.status != Order.Status.PAID:
        raise SalesNoteError(
            f'No se puede generar el PDF de la nota {sales_note.number}: la orden no está pagada.'
        )

    from . import document_layout, document_style as style

    ctx = build_sales_note_context(sales_note)
    tax = ctx['tax']
    cur = tax['symbol']

    totals = [('Subtotal', style.money(cur, tax['subtotal']))]
    if tax['discount_amount'] > 0:
        totals.append(('Descuento', f"- {style.money(cur, tax['discount_amount'])}"))
    # El desglose va DESPUÉS del descuento porque el tributo se calcula sobre lo
    # que realmente se cobra, no sobre el precio de lista.
    totals.append((tax['base_label'], style.money(cur, tax['taxable_amount'])))
    if tax['shows_tax']:
        totals.append((tax['tax_label'], style.money(cur, tax['tax_amount'])))

    document = ' '.join(
        part for part in (ctx['document_label'], ctx['document_number']) if part and part != '—'
    )
    delivery = [('Entrega', ctx['delivery_label'])]
    if ctx['full_address']:
        delivery.append(('Dirección', ctx['full_address']))

    pdf_bytes = document_layout.render_a4({
        'title': ctx['title'].upper(),
        'number': ctx['number'],
        # No es una serie fiscal, y el papel lo dice junto al número.
        'number_note': 'Número interno del sistema. No es una serie fiscal.',
        'pdf_title': (
            f"{ctx['number']} — {ctx['store_name']}" if ctx['store_name'] else ctx['number']
        ),
        'author': ctx['store_name'] or '',
        'logo_png': ctx['logo_png'],
        'identity': {
            'name': ctx['store_name'], 'legal_name': ctx['store_legal_name'], 'tax_id': ctx['store_ruc'],
            'address': ctx['store_address'], 'city': ctx['store_city'],
            'branch': ctx['branch_name'], 'branch_address': ctx['branch_address'],
            'phone': ctx['store_phone'], 'email': ctx['store_email'],
        },
        'parties': [
            ('Cliente', [
                ('Nombre', ctx['customer_name']),
                ('Documento', document),
                ('Teléfono', ctx['customer_phone'] if ctx['customer_phone'] != '—' else ''),
                *delivery,
            ]),
            ('Datos de la venta', [
                ('Fecha de emisión', ctx['issued_at']),
                ('Pedido', f"#{ctx['order_id']}"),
                ('Tipo de pago', ctx['payment_label']),
                ('Canal', ctx['channel_label']),
                ('Atendido por', ctx['seller_name']),
                ('Comprobante solicitado', ctx['receipt_label'] if ctx['receipt_label'] != '—' else ''),
            ]),
        ],
        'lines': [{
            'number': item['line_number'], 'code': item['code'], 'description': item['name'],
            'units': item['units'], 'unit': item['unit'], 'list_price': item['list_price'],
            'discount': item['discount'], 'unit_price': item['unit_price'],
            'quantity': item['quantity'], 'amount': item['subtotal'],
        } for item in ctx['items']],
        'summary': [f"Productos: {ctx['product_count']}   ·   Unidades: {ctx['unit_count']}"],
        'amount_in_words': ctx['amount_in_words'],
        'totals': totals,
        'total': style.money(cur, tax['total']),
        # El aviso no es opcional: una nota de venta interna no es un
        # comprobante electrónico, y eso va enmarcado junto al total.
        'legal': ctx['disclaimer'],
        'notes': ctx['notes'],
        'footnotes': [ctx['warranty_note']] if ctx['warranty_note'] else [],
        'footer': '  ·  '.join(part for part in (
            ctx['store_name'], f"RUC {ctx['store_ruc']}" if ctx['store_ruc'] else '',
            f"{ctx['title']} {ctx['number']}",
        ) if part),
    })

    SalesNote.objects.filter(pk=sales_note.pk).update(pdf_generated_at=timezone.now())

    return pdf_bytes
