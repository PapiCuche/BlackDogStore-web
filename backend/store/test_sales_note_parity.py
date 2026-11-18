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
from store.test_document_goldens import (
    FIXED_INSTANT, PinnedSales, check_escpos_golden, check_golden, escpos_text,
)
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
            'Descuento global': order.discount_amount,
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
                    self.assertEqual(bool(sign), label == 'Descuento global')
            with self.subTest(paper=paper, amount='in words'):
                self.assertIn('SON: CUATRO MIL SEISCIENTOS CON 00/100 SOLES', text)


# ---------------------------------------------------------------------------
# DOC-NV-DISCOUNT — the economic detail of the note, from the sale's engines.
# ---------------------------------------------------------------------------

PROMO = 'Combo Carga Rápida'


def money_of(text: str, label: str) -> list[tuple[str, str]]:
    """Every `label S/ x` (or `label - S/ x`) of a flattened paper."""
    return re.findall(rf'(?<![\w.]){re.escape(label)} {_AMOUNT}', text)


class DiscountSales(PinnedSales):
    """
    A promotion (per line), a coupon (global) and cash.

    A sale carries ONE discount source (the till refuses a promotion with a
    coupon), so the promotion and the coupon are two sales. Both are real POS
    sales: every figure was frozen by the engines that sold them. The note's
    instant is pinned, so what the papers print is the same every run but for
    the order id, which the goldens normalise.
    """

    def setUp(self):
        super().setUp()
        from store.tests import _C1_POS, _C12_DISCOUNT, _p2d_member

        self.design = CompanyDocumentDesign.objects.create(company=self.company, hide_zero_columns=True)
        self.cashier, _ = _p2d_member(self.company, 'nv_disc_cashier', ['company.view', _C1_POS, _C12_DISCOUNT])
        self.charger = self.stocked('Cargador USB-C 20 W', 'cargador-nv', '99.90')
        self.cable = self.stocked('Cable USB-C 1 m', 'cable-nv', '39.90')

    def stocked(self, name, slug, price):
        from store import inventory_services as inventory
        from store.tests import _prod

        product = _prod(self.company, name, slug, price=price, inventory=0)
        inventory.create_stock_movement(
            branch=self.branch_a, product_id=product.pk, movement_type='initial_stock',
            quantity=20, reason='Inicial', actor=self.staff)
        return product

    def pos_sale(self, lines, **kw):
        from store.tests import _c1_sale

        order, _ = _c1_sale(
            actor=self.cashier, company=self.company, branch=self.branch_a,
            items=[{'product': product.pk, 'quantity': quantity} for product, quantity in lines], **kw)
        order.refresh_from_db()
        note = self.note_for(order)
        type(note).objects.filter(pk=note.pk).update(issued_at=FIXED_INSTANT)
        note.refresh_from_db()
        return order, note

    def promotion_sale(self):
        from store.models import PaymentMethod
        from store.tests import _c13_combo

        _c13_combo(self.company, [(self.charger, 1), (self.cable, 1)], fixed_price='119.90', name=PROMO)
        return self.pos_sale([(self.charger, 1), (self.cable, 1), (self.case, 2)],
                             payment_method=PaymentMethod.CASH, amount_received='300.00')

    def coupon_sale(self):
        from store.models import Coupon, PaymentMethod

        Coupon.objects.create(company=self.company, code='DIEZ', discount_percent=10)
        return self.pos_sale([(self.case, 1), (self.charger, 1)], coupon_code='DIEZ',
                             payment_method=PaymentMethod.CASH, amount_received='200.00')

    def papers_of(self, note):
        return {
            'A4': flat(' '.join(page_texts(generate_sales_note_pdf(note)))),
            '80 mm': flat(' '.join(page_texts(generate_sales_note_ticket_pdf(note)))),
            'ESC/POS': flat(' '.join(escpos_text(escpos.sales_note_ticket(note)))),
        }



class SalesNoteDiscountParityTest(DiscountSales):
    """The three papers say the sale's own figures, to the cent."""

    def assert_amount(self, text, label, value, *, negative=False):
        found = money_of(text, label)
        self.assertEqual(len(found), 1, f'{label!r} printed {len(found)} times')
        sign, amount = found[0]
        self.assertEqual(Decimal(amount), value, label)
        self.assertEqual(bool(sign), negative, label)

    # -- promotion: per line ------------------------------------------------

    def test_the_engine_shares_the_promotion_and_the_sale_adds_up(self):
        from store.promotion_services import order_promotion_line_discounts

        order, _note = self.promotion_sale()
        self.assertEqual(order.discount_source, 'promotion')
        items = list(order.items.select_related('product').order_by('pk'))
        shares = order_promotion_line_discounts(order, items)
        self.assertEqual(sum(s.discount_amount for s in shares), order.discount_amount)
        self.assertEqual(sum(s.regular_amount for s in shares), order.subtotal_amount)
        self.assertEqual(sum(s.amount_after_discount for s in shares), order.total)
        # Lines in `pk` order: the case (untouched), then the two products of the combo.
        self.assertEqual([s.discount_amount > 0 for s in shares], [False, True, True])
        self.assertIsNotNone(order.amount_received)
        self.assertEqual(order.amount_received - order.total, order.change_amount)

    def test_each_promoted_line_prints_regular_price_discount_reason_and_net_on_every_paper(self):
        from store.fiscal.rounding import unit_value
        from store.promotion_services import order_promotion_line_discounts

        order, note = self.promotion_sale()
        papers = self.papers_of(note)
        items = list(order.items.select_related('product').order_by('pk'))
        for item, share in zip(items, order_promotion_line_discounts(order, items)):
            name, quantity = re.escape(item.product.name), item.quantity
            regular, gross = f'{share.regular_unit_price:.2f}', f'{share.regular_amount:.2f}'
            discount, net = f'{share.discount_amount:.2f}', f'{share.amount_after_discount:.2f}'
            if share.discount_amount:
                unit = f'{unit_value(share.amount_after_discount, quantity):.2f}'
                promo = re.escape(PROMO)
                patterns = {
                    # Nº CÓDIGO DESCRIPCIÓN (reason) U.M. P. LISTA DSCTO. P. UNIT. CANT. IMPORTE
                    'A4': rf'{name} Promoción: {promo} UND {regular} {discount} {unit} {quantity} {net}',
                    '80 mm': rf'{name} {quantity} x S/ {regular} S/ {gross} Dscto\. {promo} - S/ {discount} '
                             rf'Importe S/ {net}',
                    'ESC/POS': rf'{name} {quantity} x S/ {regular} S/ {gross} Dscto\. {promo} - S/ {discount} '
                               rf'Importe S/ {net}',
                }
            else:
                patterns = {
                    'A4': rf'{name} UND {regular} 0\.00 {regular} {quantity} {gross}',
                    '80 mm': rf'{name} .*?{quantity} x S/ {regular} S/ {gross}(?! Dscto)',
                    'ESC/POS': rf'{name} .*?{quantity} x S/ {regular} S/ {gross}(?! Dscto)',
                }
            for paper, text in papers.items():
                with self.subTest(paper=paper, line=item.product.name):
                    self.assertRegex(text, patterns[paper])

    def test_promotion_totals_desglose_and_cash_reconcile_to_the_cent_on_every_paper(self):
        order, note = self.promotion_sale()
        for paper, text in self.papers_of(note).items():
            with self.subTest(paper=paper):
                self.assert_amount(text, 'Subtotal', order.subtotal_amount)
                self.assert_amount(text, 'Descuento por promoción', order.discount_amount, negative=True)
                self.assertNotIn('Descuento global', text)
                self.assertEqual(text.count('Desglose referencial'), 1)
                self.assert_amount(text, 'Op. gravada', order.taxable_amount)
                self.assert_amount(text, 'IGV (18%)', order.tax_amount)
                self.assert_amount(text, 'IMPORTE TOTAL' if paper == 'A4' else 'TOTAL', order.total)
                self.assertEqual(order.taxable_amount + order.tax_amount, order.total)
                self.assert_amount(text, 'Efectivo recibido', order.amount_received)
                self.assert_amount(text, 'Vuelto', order.change_amount)
                self.assertIn('Efectivo', text)

    def test_each_promoted_line_says_what_the_receipt_of_the_same_sale_declares(self):
        """The note and the fiscal document of one sale read the same engine: same line, same cent."""
        from store.fiscal_services import _order_to_invoice_data
        from store.models import FiscalDocumentType, FiscalSeries

        order, note = self.promotion_sale()
        series = FiscalSeries.objects.create(
            company=self.company, document_type=FiscalDocumentType.RECEIPT, series='B001')
        declared = _order_to_invoice_data(order, series, 1).lines
        printed = build_sales_note_context(note)['items']
        self.assertEqual(len(declared), len(printed))
        for line, item in zip(declared, printed):
            with self.subTest(line=item['name']):
                self.assertEqual(line.line_amount + line.tax_amount, item['subtotal'])
                self.assertEqual(f'{line.unit_price_with_tax:.2f}', f"{item['unit_price']:.2f}")
                self.assertEqual(bool(line.allowances), item['discount'] > 0)

    def test_hide_zero_columns_keeps_the_discount_columns_of_a_promoted_sale(self):
        _order, note = self.promotion_sale()
        a4 = self.papers_of(note)['A4']
        self.assertIn('P. LISTA DSCTO. P. UNIT.', a4)

    # -- coupon: global -----------------------------------------------------

    def test_a_coupon_is_a_global_discount_and_the_lines_carry_none(self):
        order, note = self.coupon_sale()
        self.assertEqual(order.discount_source, 'coupon')
        papers = self.papers_of(note)
        for paper, text in papers.items():
            with self.subTest(paper=paper):
                self.assert_amount(text, 'Subtotal', order.subtotal_amount)
                self.assert_amount(text, 'Descuento global (DIEZ)', order.discount_amount, negative=True)
                self.assertNotIn('Descuento por promoción', text)
                self.assertNotIn('Dscto.', text)
                self.assert_amount(text, 'Op. gravada', order.taxable_amount)
                self.assert_amount(text, 'IGV (18%)', order.tax_amount)
                self.assert_amount(text, 'IMPORTE TOTAL' if paper == 'A4' else 'TOTAL', order.total)
                self.assert_amount(text, 'Efectivo recibido', order.amount_received)
                self.assert_amount(text, 'Vuelto', order.change_amount)
                for item in order.items.select_related('product'):
                    name, price = re.escape(item.product.name), Decimal(str(item.price))
                    unit, amount = f'{price:.2f}', f'{price * item.quantity:.2f}'
                    self.assertRegex(text, rf'{name} .*?UND {unit} {item.quantity} {amount}' if paper == 'A4'
                                     else rf'{name} .*?{item.quantity} x S/ {unit} S/ {amount}')
        # All-zero line discounts: the company's switch drops P. LISTA and DSCTO.
        self.assertNotIn('P. LISTA', papers['A4'])
        self.assertIn('P. UNIT.', papers['A4'])

    # -- the referential breakdown -----------------------------------------

    def test_the_breakdown_follows_the_companys_mode(self):
        order, note = self.promotion_sale()
        self.design.tax_breakdown_mode = 'summary'
        self.design.save()
        for paper, text in self.papers_of(note).items():
            with self.subTest(paper=paper, mode='summary'):
                self.assertEqual(text.count('Desglose referencial'), 1)
                self.assert_amount(text, 'Incluye IGV (18%)', order.tax_amount)
                self.assertNotIn('Op. gravada', text)
        self.design.tax_breakdown_mode = 'hidden'
        self.design.save()
        for paper, text in self.papers_of(note).items():
            with self.subTest(paper=paper, mode='hidden'):
                self.assertNotIn('Desglose referencial', text)
                self.assertNotIn('Op. gravada', text)
                self.assertNotIn('IGV', text)
                self.assert_amount(text, 'IMPORTE TOTAL' if paper == 'A4' else 'TOTAL', order.total)

    def test_a_sale_without_a_frozen_breakdown_prints_no_breakdown(self):
        from store.models import Order

        order, note = self.coupon_sale()
        Order.objects.filter(pk=order.pk).update(taxable_amount=None, tax_amount=None, tax_rate=None)
        note.refresh_from_db()
        for paper, text in self.papers_of(note).items():
            with self.subTest(paper=paper):
                self.assertNotIn('Desglose referencial', text)
                self.assertNotIn('Op. gravada', text)
                self.assertNotIn('IGV', text)
                self.assert_amount(text, 'IMPORTE TOTAL' if paper == 'A4' else 'TOTAL', order.total)

    # -- what is not there is not invented ---------------------------------

    def test_a_card_sale_prints_no_cash_rows(self):
        _order, _note, papers = SalesNoteParityTest.papers(self)
        for paper, text in papers.items():
            with self.subTest(paper=paper):
                self.assertNotIn('Efectivo recibido', text)
                self.assertNotIn('Vuelto', text)

    def test_a_promotion_the_engine_cannot_explain_is_printed_whole_and_unattributed(self):
        from store.models import AppliedPromotion

        order, note = self.promotion_sale()
        AppliedPromotion.objects.filter(order=order).delete()
        with self.assertLogs('store.sales_note_services', 'WARNING') as logged:
            papers = self.papers_of(note)
        self.assertIn('no se puede repartir por línea', logged.output[0])
        for paper, text in papers.items():
            with self.subTest(paper=paper):
                self.assert_amount(text, 'Descuento por promoción', order.discount_amount, negative=True)
                self.assertNotIn('Dscto.', text)
                self.assertNotIn('Promoción:', text)
                self.assert_amount(text, 'IMPORTE TOTAL' if paper == 'A4' else 'TOTAL', order.total)


def _order_id(text: str) -> str:
    """
    The POS gives the order its id; everything else on these papers is pinned. The thermal roll
    right-aligns the id, so the padding before it goes too.
    """
    return re.sub(r'\s*#\d+', ' #N', text)


class SalesNoteDiscountGoldenTest(DiscountSales):
    """DOC-NV-DISCOUNT goldens: a promoted sale and a coupon sale, paid in cash, on the three papers."""

    def test_promotion_sale(self):
        _order, note = self.promotion_sale()
        check_golden(self, 'sales_note_a4_promotion', generate_sales_note_pdf(note), normalise=_order_id)
        check_golden(self, 'sales_note_ticket80_promotion', generate_sales_note_ticket_pdf(note),
                     normalise=_order_id)
        check_escpos_golden(self, 'sales_note_escpos_promotion', escpos.sales_note_ticket(note),
                            normalise=_order_id)

    def test_coupon_sale(self):
        _order, note = self.coupon_sale()
        check_golden(self, 'sales_note_a4_coupon', generate_sales_note_pdf(note), normalise=_order_id)
        check_golden(self, 'sales_note_ticket80_coupon', generate_sales_note_ticket_pdf(note), normalise=_order_id)
        check_escpos_golden(self, 'sales_note_escpos_coupon', escpos.sales_note_ticket(note), normalise=_order_id)
