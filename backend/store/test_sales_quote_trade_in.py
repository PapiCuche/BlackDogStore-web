"""
TRADE-IN-QUOTE-00 — a non-binding part-exchange valuation inside a commercial quote.

What these tests hold, in the owner's words: a new device at S/ 4,500, a used one offered at S/ 1,200, a
proposed balance of S/ 3,300. The sale price and the valuation stay SEPARATE: the valuation is never a
`manual_discount`, never touches the quote's totals or IGV, and never reaches the sale. Converting the quote
charges the FULL price: no credit, no purchase, no stock, no payment while D1–D12 are pending. The paper
says the valuation is referential and is not credit, payment, purchase or a receipt — and never prints a
full IMEI or an identity document.
"""
import inspect
from decimal import Decimal

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import DataError, IntegrityError, transaction

from store import pos_services
from store import sales_quote_services as quotes
from store.document_design import names_fiscal_document
from store.document_legends import SALES_QUOTE_LEGEND, TRADE_IN_QUOTE_HEADING, TRADE_IN_QUOTE_LEGEND
from store.models import (
    AdminAuditLog,
    CompanyRole,
    Membership,
    Order,
    PaymentMethod,
    PaymentTransaction,
    SalesQuote,
    SalesQuoteTradeIn,
    StockMovement,
)
from store.test_pos_reconcile import _web_login
from store.test_sales_quotes import URL, QuoteBase, pdf_text
from store.tests import _assign, _m7_stock, _m7_user, _prod


def flat(text: str) -> str:
    """A PDF's words on one line: a long legend may wrap on the 80 mm roll."""
    return ' '.join(text.split())


class TradeInBase(QuoteBase):
    """The till's fixture plus a new device at S/ 4,500 on the shelf of branch A."""

    def setUp(self):
        super().setUp()
        self.phone = _prod(self.company, 'Equipo nuevo 256 GB', 'equipo-nuevo-256', price='4500.00')
        _m7_stock(self.branch_a, self.phone, 5)

    def offered(self, **over):
        block = {
            'device_description': 'Equipo usado modelo anterior', 'capacity': '128 GB', 'color': 'Azul',
            'condition': 'good', 'battery_health': 86, 'accessories': 'Caja y cable',
            'imei_last4': '4321', 'customer_asking_price': '1500.00', 'estimated_valuation': '1200.00',
            'valuation_notes': 'Pantalla con rayón leve',
        }
        block.update(over)
        return block

    def phone_body(self, **over):
        return {'items': [{'product': self.phone.pk, 'quantity': 1}], **over}

    def with_trade_in(self, client=None, **block):
        return self.create(client=client, **self.phone_body(trade_in=self.offered(**block)))


# ---------------------------------------------------------------------------
# The owner's example and the rule of the balance
# ---------------------------------------------------------------------------

class TradeInValuationTest(TradeInBase):
    def test_the_owners_example_4500_1200_3300(self):
        plain = self.create(**self.phone_body())
        quote = self.with_trade_in()
        self.assertEqual(quote['total'], '4500.00')
        block = quote['trade_in']
        self.assertEqual((block['estimated_valuation'], block['estimated_balance']), ('1200.00', '3300.00'))
        self.assertEqual((block['condition'], block['condition_label'], block['battery_health']),
                         ('good', 'Buen estado', 86))
        self.assertEqual((block['imei_last4'], block['valued_by']), ('4321', 'ip1_caja'))
        self.assertIsNotNone(block['valued_at'])
        # The sale price and the valuation stay SEPARATE: nothing of the quote's money moved.
        for field in ('subtotal', 'discount_amount', 'discount_source', 'discount_type', 'discount_reason',
                      'taxable_amount', 'tax_amount', 'tax_rate', 'total'):
            self.assertEqual(quote[field], plain[field], field)
        stored = SalesQuote.objects.get(pk=quote['id'])
        self.assertEqual((stored.total, stored.discount_amount, stored.discount_source),
                         (Decimal('4500.00'), Decimal('0.00'), 'none'))
        # The balance is computed, never stored.
        self.assertNotIn('estimated_balance', {f.name for f in SalesQuoteTradeIn._meta.get_fields()})
        self.assertEqual(stored.trade_in.estimated_balance, Decimal('3300.00'))

    def test_a_quote_without_the_block_reads_none(self):
        quote = self.create(**self.phone_body())
        self.assertIsNone(quote['trade_in'])
        self.assertFalse(SalesQuoteTradeIn.objects.exists())

    def test_a_valuation_greater_than_the_total_is_refused(self):
        res = self.client.post(URL, self.body(**self.phone_body(trade_in=self.offered(estimated_valuation='4500.01'))),
                               format='json')
        self.assertEqual((res.status_code, res.json()['code']), (400, 'trade_in_exceeds_total'), res.content)
        self.assertIn('4500.00', res.json()['detail'])
        self.assertFalse(SalesQuote.objects.exists())
        self.assertFalse(SalesQuoteTradeIn.objects.exists())

    def test_a_valuation_equal_to_the_total_leaves_a_zero_balance(self):
        quote = self.with_trade_in(estimated_valuation='4500.00')
        self.assertEqual(quote['trade_in']['estimated_balance'], '0.00')

    def test_lines_that_drop_the_total_under_the_valuation_are_refused(self):
        quote = self.with_trade_in()
        res = self.client.patch(f"{URL}{quote['id']}/", {'items': [{'product': self.cable.pk, 'quantity': 2}]},
                                format='json')
        self.assertEqual((res.status_code, res.json()['code']), (400, 'trade_in_exceeds_total'), res.content)
        self.assertEqual(SalesQuote.objects.get(pk=quote['id']).total, Decimal('4500.00'))

    def test_only_the_last_four_digits_of_the_imei_and_never_in_free_text(self):
        full = '356789012345678'
        for over in ({'imei_last4': full}, {'imei_last4': '12a4'}, {'imei_last4': '123'},
                     {'device_description': f'Equipo IMEI {full}'}, {'accessories': 'DNI 45678912'},
                     {'valuation_notes': f'imei {full}'}):
            with self.subTest(over=over):
                res = self.client.post(URL, self.body(**self.phone_body(trade_in=self.offered(**over))),
                                       format='json')
                self.assertEqual(res.status_code, 400, res.content)
                self.assertNotIn(full, res.content.decode())
        self.assertFalse(SalesQuoteTradeIn.objects.exists())
        # Blank is allowed: the device may be described without it.
        self.assertEqual(self.with_trade_in(imei_last4='')['trade_in']['imei_last4'], '')

    def test_the_database_refuses_more_than_four_digits(self):
        quote = self.with_trade_in()
        for value in ('12ab', '123', '12345'):
            with self.subTest(value=value), self.assertRaises((IntegrityError, DataError)), transaction.atomic():
                SalesQuoteTradeIn.objects.filter(quote_id=quote['id']).update(imei_last4=value)

    def test_shape_of_the_block(self):
        for over, needle in (({'device_description': ''}, 'equipo'), ({'condition': 'nuevo'}, 'estado'),
                             ({'battery_health': 101}, 'batería'), ({'battery_health': -1}, 'batería'),
                             ({'estimated_valuation': '-1'}, 'valorización'), ({'estimated_valuation': ''}, 'valorización'),
                             ({'customer_asking_price': '-5'}, 'pide')):
            with self.subTest(over=over):
                res = self.client.post(URL, self.body(**self.phone_body(trade_in=self.offered(**over))),
                                       format='json')
                self.assertEqual(res.status_code, 400, res.content)
                self.assertIn(needle, res.json()['detail'].lower())
        res = self.client.post(URL, self.body(**self.phone_body(trade_in='sí')), format='json')
        self.assertEqual(res.status_code, 400, res.content)
        block = self.with_trade_in(battery_health=None, customer_asking_price=None)['trade_in']
        self.assertEqual((block['battery_health'], block['customer_asking_price']), (None, None))

    def test_creating_and_removing_the_block_is_audited_without_identifiers(self):
        quote = self.with_trade_in()
        res = self.client.patch(f"{URL}{quote['id']}/", {'trade_in': None}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertIsNone(res.json()['trade_in'])
        valued = AdminAuditLog.objects.get(action='sales_quote_trade_in_valued', company=self.company)
        self.assertEqual(valued.metadata['estimated_valuation'], '1200.00')
        self.assertTrue(AdminAuditLog.objects.filter(action='sales_quote_trade_in_removed',
                                                     company=self.company).exists())
        for row in AdminAuditLog.objects.filter(target_type='sales_quote'):
            self.assertNotIn('4321', str(row.metadata))


# ---------------------------------------------------------------------------
# Revisions, duplicates, immutability
# ---------------------------------------------------------------------------

class TradeInRevisionTest(TradeInBase):
    def test_a_revision_copies_the_block_and_who_valued_it(self):
        quote = self.with_trade_in()
        self.act(quote['id'], 'approve')
        res = self.jefe.patch(f"{URL}{quote['id']}/", {'conditions': 'Otra condición'}, format='json')
        self.assertEqual(res.status_code, 201, res.content)
        new = res.json()
        self.assertEqual(new['revision'], 2)
        old_block = SalesQuoteTradeIn.objects.get(quote_id=quote['id'])
        new_block = SalesQuoteTradeIn.objects.get(quote_id=new['id'])
        for field in ('device_description', 'capacity', 'color', 'condition', 'battery_health', 'accessories',
                      'imei_last4', 'customer_asking_price', 'estimated_valuation', 'valuation_notes',
                      'valued_by_id', 'valued_at'):
            self.assertEqual(getattr(new_block, field), getattr(old_block, field), field)
        self.assertEqual(new['trade_in']['valued_by'], 'ip1_caja')

    def test_changing_the_block_of_an_approved_quote_writes_a_revision(self):
        quote = self.with_trade_in()
        self.act(quote['id'], 'approve')
        res = self.jefe.patch(f"{URL}{quote['id']}/", {'trade_in': self.offered(estimated_valuation='1000.00')},
                              format='json')
        self.assertEqual(res.status_code, 201, res.content)
        new = res.json()
        self.assertEqual((new['revision'], new['status']), (2, 'draft'))
        self.assertEqual((new['trade_in']['estimated_valuation'], new['trade_in']['estimated_balance'],
                          new['trade_in']['valued_by']), ('1000.00', '3500.00', 'sq_jefe'))
        old = SalesQuote.objects.get(pk=quote['id'])
        self.assertEqual((old.status, old.trade_in.estimated_valuation), ('superseded', Decimal('1200.00')))

    def test_removing_the_block_in_a_revision_keeps_the_old_one(self):
        quote = self.with_trade_in()
        self.act(quote['id'], 'approve')
        new = self.jefe.patch(f"{URL}{quote['id']}/", {'trade_in': None}, format='json').json()
        self.assertIsNone(new['trade_in'])
        self.assertTrue(SalesQuoteTradeIn.objects.filter(quote_id=quote['id']).exists())

    def test_the_block_of_an_offered_quote_cannot_be_rewritten_directly(self):
        quote = self.with_trade_in()
        self.act(quote['id'], 'approve')
        block = SalesQuoteTradeIn.objects.get(quote_id=quote['id'])
        block.estimated_valuation = Decimal('10.00')
        with self.assertRaises(ValidationError):
            block.save()
        with self.assertRaises(ValidationError):
            block.delete()
        stored = SalesQuote.objects.get(pk=quote['id'])
        with self.assertRaises(ValidationError):
            SalesQuoteTradeIn(quote=stored, device_description='x', condition='good',
                              estimated_valuation=Decimal('1.00')).save()

    def test_a_duplicate_carries_the_block(self):
        quote = self.with_trade_in()
        copy = self.act(quote['id'], 'duplicate', client=self.client, expect=201)
        self.assertEqual((copy['trade_in']['estimated_valuation'], copy['trade_in']['device_description']),
                         ('1200.00', 'Equipo usado modelo anterior'))
        self.assertEqual(SalesQuoteTradeIn.objects.count(), 2)

    def test_approving_rechecks_the_valuation_against_the_total(self):
        quote = self.with_trade_in(estimated_valuation='4500.00')
        SalesQuoteTradeIn.objects.filter(quote_id=quote['id']).update(estimated_valuation=Decimal('4600.00'))
        res = self.jefe.post(f"{URL}{quote['id']}/approve/")
        self.assertEqual((res.status_code, res.json()['code']), (400, 'trade_in_exceeds_total'), res.content)
        self.assertEqual(SalesQuote.objects.get(pk=quote['id']).status, 'draft')


# ---------------------------------------------------------------------------
# Permissions and tenants
# ---------------------------------------------------------------------------

class TradeInAuthorityTest(TradeInBase):
    def test_a_viewer_reads_the_block_and_cannot_set_it(self):
        quote = self.with_trade_in()
        viewer = self.only_caps('company.view', 'sales.quotes.view')
        res = viewer.get(f"{URL}{quote['id']}/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['trade_in']['estimated_balance'], '3300.00')
        res = viewer.patch(f"{URL}{quote['id']}/", {'trade_in': self.offered(estimated_valuation='10.00')},
                           format='json')
        self.assertEqual(res.status_code, 403)
        self.assertEqual(SalesQuoteTradeIn.objects.get().estimated_valuation, Decimal('1200.00'))

    def test_the_till_alone_cannot_value(self):
        cashier = self.only_caps('company.view', 'sales.pos.use', slug='ti-solo-caja')
        res = cashier.post(URL, self.body(**self.phone_body(trade_in=self.offered())), format='json')
        self.assertEqual(res.status_code, 403)

    def test_another_company_sees_nothing_of_the_block(self):
        quote = self.with_trade_in()
        outsider = _m7_user('ti_ajeno')
        membership = Membership.objects.create(user=outsider, company=self.other, role='sales', is_active=True)
        _assign(membership, CompanyRole.objects.get(company=self.other, slug='ventas'))
        cache.clear()
        client = _web_login('ti_ajeno')
        for path in (f"{URL}{quote['id']}/", f"{URL}{quote['id']}/pdf/"):
            res = client.get(path)
            self.assertEqual(res.status_code, 404)
            self.assertNotIn('Equipo usado', res.content.decode(errors='ignore'))
        res = client.patch(f"{URL}{quote['id']}/", {'trade_in': None}, format='json')
        self.assertEqual(res.status_code, 404)
        self.assertTrue(SalesQuoteTradeIn.objects.filter(quote_id=quote['id']).exists())


# ---------------------------------------------------------------------------
# Conversion: the sale is charged whole
# ---------------------------------------------------------------------------

ORDER_MONEY = ('total', 'subtotal', 'discount_amount', 'discount_source', 'discount_reason', 'taxable_amount',
               'tax_amount', 'tax_rate', 'payment_method', 'amount_received', 'change_given')


class TradeInConvertTest(TradeInBase):
    def convert_full(self, quote_id, key):
        res = self.convert(quote_id, key=key, amount_received='4500.00')
        self.assertEqual(res.status_code, 201, res.content)
        return Order.objects.get(pk=res.json()['sale']['order_id'])

    def test_the_sale_is_exactly_the_one_without_trade_in(self):
        plain = self.create(**self.phone_body())
        offered = self.with_trade_in()
        self.act(plain['id'], 'approve')
        self.act(offered['id'], 'approve')
        payments_before = PaymentTransaction.objects.count()
        movements_before = StockMovement.objects.count()
        without = self.convert_full(plain['id'], 'ti-convert-plain-1')
        movements_without = StockMovement.objects.count() - movements_before
        payments_without = PaymentTransaction.objects.count() - payments_before
        with_block = self.convert_full(offered['id'], 'ti-convert-offer-1')
        for field in ORDER_MONEY:
            self.assertEqual(getattr(with_block, field, None), getattr(without, field, None), field)
        self.assertEqual(with_block.total, Decimal('4500.00'))
        self.assertEqual((with_block.discount_amount, with_block.discount_source), (Decimal('0.00'), 'none'))
        self.assertEqual(with_block.items.count(), without.items.count())
        # Stock: one exit each, nothing entered for the offered device.
        self.assertEqual(StockMovement.objects.count() - movements_before, 2 * movements_without)
        self.assertEqual(StockMovement.objects.filter(product=self.phone).count(), 2)
        self.assertFalse(StockMovement.objects.filter(reference_type__icontains='trade').exists())
        self.assertEqual(self.stock(product=self.phone), 3)
        self.assertEqual(PaymentTransaction.objects.count() - payments_before, 2 * payments_without)
        # The quote keeps the block for history.
        stored = SalesQuote.objects.get(pk=offered['id'])
        self.assertEqual((stored.status, stored.trade_in.estimated_valuation), ('converted', Decimal('1200.00')))
        audit = AdminAuditLog.objects.get(action='sales_quote_converted', target_id=str(offered['id']))
        self.assertEqual((audit.metadata['trade_in_applied'], audit.metadata['trade_in_valuation']),
                         (False, '1200.00'))

    def test_the_balance_in_cash_does_not_pay_the_sale(self):
        quote = self.with_trade_in()
        self.act(quote['id'], 'approve')
        res = self.convert(quote['id'], key='ti-convert-short-1', amount_received='3300.00')
        self.assertEqual(res.status_code, 400, res.content)
        self.assertFalse(Order.objects.exists())
        self.assertEqual(SalesQuote.objects.get(pk=quote['id']).status, 'approved')

    def test_no_manual_discount_ever_carries_the_trade_in(self):
        quote = self.with_trade_in()
        self.act(quote['id'], 'approve')
        self.convert_full(quote['id'], 'ti-convert-disc-1')
        for order in Order.objects.all():
            reason = (order.discount_reason or '').lower()
            for word in ('parte de pago', 'trade', 'valoriz', 'canje'):
                self.assertNotIn(word, reason)
        self.assertFalse(AdminAuditLog.objects.filter(action='pos_manual_discount_applied').exists())


class TradeInGuardTest(TradeInBase):
    """
    GUARDS UNTIL D1–D12 ARE DECIDED. If one of these fails, somebody is applying the part-exchange to a sale,
    a payment or the stock: that is TRADE-IN-01B/01C, which needs the owner's and the accountant's decisions
    (docs/decisiones-propietario-trade-in.md) and its own ADR-approved PR — not an edit of this test.
    """

    def test_order_has_no_trade_in_field(self):
        names = [field.name for field in Order._meta.get_fields()]
        self.assertFalse([n for n in names if 'trade' in n.lower()], names)

    def test_payment_method_has_no_trade_in_choice(self):
        for value, label in PaymentMethod.choices:
            for word in ('trade', 'parte de pago', 'canje', 'especie'):
                self.assertNotIn(word, value.lower())
                self.assertNotIn(word, str(label).lower())

    def test_stock_movements_have_no_trade_in_kind(self):
        for value, label in StockMovement._meta.get_field('movement_type').choices:
            self.assertNotIn('trade', value.lower())
            self.assertNotIn('parte de pago', str(label).lower())

    def test_the_till_and_the_conversion_never_read_the_block(self):
        self.assertNotIn('trade_in', inspect.getsource(pos_services))
        conversion = inspect.getsource(quotes.convert_quote)
        call = conversion[conversion.index('pos_services.create_pos_sale('):]
        call = call[:call.index(')\n')]
        self.assertNotIn('trade', call)


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------

class TradeInDocumentTest(TradeInBase):
    def test_a4_and_ticket_show_the_block_with_its_legend_and_a_masked_imei(self):
        quote = self.with_trade_in()
        self.act(quote['id'], 'approve')
        for kind in ('a4', 'ticket80'):
            with self.subTest(kind=kind):
                res = self.client.get(f"{URL}{quote['id']}/pdf/", {'format': kind})
                self.assertEqual(res.status_code, 200)
                text = flat(pdf_text(res.content))
                for expected in (TRADE_IN_QUOTE_HEADING, 'Equipo usado modelo anterior', '128 GB', 'Azul',
                                 'Buen estado', '86 %', 'Caja y cable', '****4321', 'Valorización estimada',
                                 'S/ 1200.00', 'Saldo estimado a pagar', 'S/ 3300.00', 'S/ 4500.00',
                                 TRADE_IN_QUOTE_LEGEND):
                    self.assertIn(expected, text, f'{kind}: {expected}')
                # Internal: what the customer asked and the valuer's notes never reach the paper.
                self.assertNotIn('1500.00', text)
                self.assertNotIn('rayón', text)
                self.assertFalse(names_fiscal_document(
                    text.replace(TRADE_IN_QUOTE_LEGEND, '').replace(SALES_QUOTE_LEGEND, '')), kind)

    def test_the_thermal_roll_carries_the_block(self):
        from store.sales_quote_documents import escpos_ticket

        quote = self.with_trade_in()
        payload = escpos_ticket(SalesQuote.objects.get(pk=quote['id']))
        for expected in ('Valorización estimada', 'Saldo estimado a pagar', '****4321', 'S/ 3300.00',
                         'crédito, pago ni compra'):
            self.assertIn(expected.encode('cp858'), payload)
        self.assertNotIn(b'1500.00', payload)

    def test_a_quote_without_the_block_prints_nothing_of_it(self):
        quote = self.create(**self.phone_body())
        for kind in ('a4', 'ticket80'):
            text = flat(pdf_text(self.client.get(f"{URL}{quote['id']}/pdf/", {'format': kind}).content))
            self.assertNotIn(TRADE_IN_QUOTE_HEADING, text)
            self.assertNotIn('Saldo estimado', text)
