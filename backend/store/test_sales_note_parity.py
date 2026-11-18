"""
DOC-SAFETY-01 — one sales note, three papers, the same facts.

A sales note is drawn three times from one context: the A4 PDF, the 80 mm PDF
and the ESC/POS bytes of the thermal printer. Each format has its own drawing
code, so nothing but a test keeps them saying the same thing. This one reads
the TEXT of each paper and checks every field the owner asked for, in every
format:

  number, date, branch, company and RUC, customer and document, seller,
  payment, each line (name, quantity, unit price, line total), IMEI/serial,
  discount, totals, warranty and the closing message.

And the money is the SALE's: every amount a paper prints equals, to the cent,
the value frozen on the order (C2.1). The frozen breakdown here is deliberately
one cent away from what a recalculation from the total would give (3898.30 +
701.70 instead of 3898.31 + 701.69), so a paper that recalculated instead of
reading the sale fails.
"""
import re
from decimal import Decimal

from store.models import CompanyDocumentDesign
from store.printing import escpos
from store.sales_note_services import build_sales_note_context, generate_sales_note_pdf
from store.test_document_design import page_texts
from store.test_document_goldens import PinnedSales, escpos_text
from store.test_stock_units import IMEI_A, IMEI_B
from store.ticket_services import generate_sales_note_ticket_pdf

SELLER = 'Lucía Ramos'
CLOSING = 'Mensaje final de prueba.'
#: Frozen on the order; a recalculation from the total would give 3898.31 / 701.69.
FROZEN = {
    'subtotal_amount': Decimal('4659.80'), 'discount_amount': Decimal('59.80'),
    'taxable_amount': Decimal('3898.30'), 'tax_amount': Decimal('701.70'),
    'tax_rate': Decimal('0.18'), 'tax_treatment': 'taxed', 'currency': 'PEN',
}

_AMOUNT = r'(-\s*)?S/\s*(\d+\.\d{2})'


def flat(text: str) -> str:
    """Whitespace collapsed: a roll pads its rows and wraps its lines, a PDF splits its runs."""
    return re.sub(r'\s+', ' ', text).strip()


class SalesNoteParityTest(PinnedSales):
    def setUp(self):
        super().setUp()
        CompanyDocumentDesign.objects.create(company=self.company, thank_you_message=CLOSING)

    def papers(self):
        self.receive(self.row('F2LXK1ABC1', IMEI_A, imei2=IMEI_B))
        fields = {key: value for key, value in FROZEN.items() if key != 'discount_amount'}
        order, note = self.pinned_sale(
            [(self.phone, 1), (self.case, 2)], discount_amount=str(FROZEN['discount_amount']),
            seller_name_snapshot=SELLER, **fields)
        papers = {
            'A4': flat(' '.join(page_texts(generate_sales_note_pdf(note)))),
            '80 mm': flat(' '.join(page_texts(generate_sales_note_ticket_pdf(note)))),
            'ESC/POS': flat(' '.join(escpos_text(escpos.sales_note_ticket(note)))),
        }
        return order, note, papers

    def test_every_field_of_the_note_is_on_every_paper(self):
        order, note, papers = self.papers()
        ctx = build_sales_note_context(note)
        units = ['Serie: F2LXK1ABC1', f'IMEI: {IMEI_A}', f'IMEI 2: {IMEI_B}']
        expected = {
            'number': note.number,
            'date': ctx['issued_at'],
            'branch': 'Tienda Centro',
            'company': 'Inventario S.A.C.',
            'RUC': 'RUC 20800000001',
            'customer': 'María Fernanda Quispe',
            'customer document': '40404040',
            'seller': SELLER,
            'payment': 'Tarjeta',
            'warranty': 'Garantía de 12 meses por defecto de fábrica.',
            'thanks': CLOSING,
            **{f'unit {n}': identifier for n, identifier in enumerate(units)},
            **{f'line {item.pk}': item.product.name for item in order.items.all()},
        }
        for paper, text in papers.items():
            for field, value in expected.items():
                with self.subTest(paper=paper, field=field):
                    self.assertIn(flat(value), text)

    def test_each_line_prints_its_quantity_unit_price_and_total_on_every_paper(self):
        order, _note, papers = self.papers()
        for item in order.items.order_by('pk'):
            name, quantity = re.escape(item.product.name), item.quantity
            price = Decimal(str(item.price))
            unit, total = f'{price:.2f}', f'{price * quantity:.2f}'
            patterns = {
                # Nº CÓDIGO DESCRIPCIÓN (identifiers) U.M. [P. LISTA DSCTO.] P. UNIT. CANT. IMPORTE
                'A4': rf'{name} (?:.*? )?UND (?:\d+\.\d{{2}} )*?{unit} {quantity} {total}',
                '80 mm': rf'{name} .*?{quantity} x S/ {unit} S/ {total}',
                'ESC/POS': rf'{name} .*?{quantity} x S/ {unit} S/ {total}',
            }
            for paper, text in papers.items():
                with self.subTest(paper=paper, line=item.product.name):
                    self.assertRegex(text, patterns[paper])

    def test_every_amount_is_the_frozen_value_of_the_sale_to_the_cent(self):
        order, _note, papers = self.papers()
        order.refresh_from_db()
        lines_total = sum(Decimal(str(i.price)) * i.quantity for i in order.items.all())
        self.assertEqual(lines_total, order.subtotal_amount)
        frozen = {
            'Subtotal': order.subtotal_amount,
            'Descuento': order.discount_amount,
            'Op. gravada': order.taxable_amount,
            r'IGV \(18%\)': order.tax_amount,
        }
        for paper, text in papers.items():
            total_label = 'IMPORTE TOTAL' if paper == 'A4' else 'TOTAL'
            for label, value in {**frozen, total_label: order.total}.items():
                with self.subTest(paper=paper, amount=label):
                    found = re.findall(rf'(?<![\w.]){label} {_AMOUNT}', text)
                    self.assertEqual(len(found), 1, f'{label!r} printed {len(found)} times')
                    sign, amount = found[0]
                    self.assertEqual(Decimal(amount), value)
                    self.assertEqual(bool(sign), label == 'Descuento')
            with self.subTest(paper=paper, amount='in words'):
                self.assertIn('SON: CUATRO MIL SEISCIENTOS CON 00/100 SOLES', text)
