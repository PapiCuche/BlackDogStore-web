"""
B1-0 — the supply amounts (`supply_services.clean_money`) never read an ambiguous text in silence.

`clean_money` reads every supplier amount the system writes: the commercial price and the agreed cost of an
offer (screen, guided registration, supplier file), the real cost when receiving an on-order line and the real
cost recorded afterwards. Before B1-0 it replaced every comma with a dot, so «5,500» was saved as 5.50 — a
price a hundred times lower, without a word.

Policy (the same rule as the product import's `import_services.clean_price`, §34, made stricter):
  * numbers (int, Decimal, finite float) are read as before;
  * «5500», «5500.00», «5500,00», «5,50» keep their meaning;
  * ONE separator followed by exactly three digits («5,500», «5.500») is AMBIGUOUS: refused, never guessed;
  * both separators, or a repeated one, are accepted only with exact thousands groups («5,500.00»,
    «5.500,00», «1.234.567»); a wrong grouping is not an amount;
  * two amounts in one text («1800/2000», «5400-5500», «4390 4500») are refused naming it;
  * a currency symbol, letters, an exponent, NaN, Infinity, a sign, a negative or a huge value are refused;
  * what is stored is never re-read: only new input is parsed.
"""
from datetime import timedelta
from decimal import Decimal as D

from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from store import supply_services
from store.models import SupplierOffer
from store.supply_services import SupplyError, clean_money
from store.test_supplier_offers import SupplyShop


def money(value, **kwargs):
    return clean_money(value, 'commercial_price', 'el precio comercial', **kwargs)


class CleanMoneyPolicyTest(SimpleTestCase):
    def test_unambiguous_texts_keep_their_meaning(self):
        for text, expected in (('5500', '5500.00'), ('5500.00', '5500.00'), ('5500,00', '5500.00'),
                               ('5,50', '5.50'), ('5.5', '5.50'), ('0,99', '0.99'), (' 4390 ', '4390.00'),
                               ('5,500.00', '5500.00'), ('5.500,00', '5500.00'), ('1.234.567', '1234567.00'),
                               ('1,234,567.89', '1234567.89'), ('12', '12.00')):
            with self.subTest(text):
                self.assertEqual(money(text), D(expected))

    def test_numbers_are_read_as_before(self):
        for value, expected in ((5500, '5500.00'), (D('5500.5'), '5500.50'), (5.5, '5.50'), (0, '0.00')):
            with self.subTest(value):
                self.assertEqual(money(value), D(expected))

    def test_one_separator_and_three_digits_is_ambiguous_never_guessed(self):
        for text in ('5,500', '5.500', '12.345', '999,999', '1,000'):
            with self.subTest(text), self.assertRaises(SupplyError) as caught:
                money(text)
            self.assertEqual((caught.exception.code, caught.exception.field), ('ambiguous_amount', 'commercial_price'))
            self.assertIn('ambiguo', caught.exception.detail)

    def test_two_amounts_in_one_text_are_refused_naming_it(self):
        for text in ('1800/2000', '5400-5500', '4390 4500', '4390 / 4500', '1800;2000', '5400 - 5500'):
            with self.subTest(text), self.assertRaises(SupplyError) as caught:
                money(text)
            self.assertEqual(caught.exception.code, 'several_amounts')

    def test_what_is_not_an_amount_is_refused(self):
        for value in ('S/ 5500', '$1500', '5500 soles', 'USD 10', 'abc', '1e3', '1E3', 'NaN', 'nan', 'Infinity',
                      '-Infinity', '+5', '-5', '-5,00', '5,50,0', '5.500.00', '5,5000.00', '55,00.00', '5..5',
                      ',5', '5,', '.', ',', float('nan'), float('inf'), -1, D('-0.01'), True, [5], {'a': 1},
                      '10000000000', 10 ** 12, '1' * 400):
            with self.subTest(value=repr(value)[:30]), self.assertRaises(SupplyError) as caught:
                money(value)
            self.assertIn(caught.exception.code, ('invalid', 'ambiguous_amount', 'several_amounts'))

    def test_decimals_rules_stay(self):
        with self.assertRaises(SupplyError):
            money('5.555')          # three decimals after a dot, ambiguous too
        with self.assertRaises(SupplyError):
            money(D('5.555'))       # a number with three decimals: «a lo sumo dos decimales»
        # Review of B1-0: four or more digits after one separator are never decimals in silence.
        for text in ('5.5000', '1800,2000', '4390,4500', '5400,5500', '1,0000', '12,0000', '0.500', '5500.000',
                     '70.0000'):
            with self.subTest(text), self.assertRaises(SupplyError) as caught:
                money(text)
            self.assertEqual(caught.exception.code, 'invalid')
            self.assertIn('dos decimales', caught.exception.detail)

    def test_space_grouping_is_one_amount_refused_with_its_reason(self):
        for text in ('1 234,56', '1\u00a0234,56', '1\u202f234', '1\u2009234', '12 500', '550 600'):
            with self.subTest(text), self.assertRaises(SupplyError) as caught:
                money(text)
            self.assertEqual(caught.exception.code, 'invalid')
            # Both readings are named: following the advice must never turn two cheap prices into one (review).
            self.assertIn('espacios', caught.exception.detail)
            self.assertIn('importes distintos', caught.exception.detail)

    def test_required_and_positive_as_before(self):
        self.assertIsNone(money(''))
        self.assertIsNone(money(None))
        with self.assertRaises(SupplyError):
            money('', required=True)
        with self.assertRaises(SupplyError):
            money('0', positive=True)
        self.assertEqual(money('0'), D('0.00'))


class OfferAmountsTest(SupplyShop, TestCase):
    """The flows that read through `clean_money`: never «5,500» as 5.50, and stored values never re-read."""

    def setUp(self):
        self.build('b1-0-ofertas')

    def test_an_offer_refuses_an_ambiguous_price_and_cost_and_writes_nothing(self):
        before = SupplierOffer.objects.count()
        for field in ('commercial_price', 'agreed_cost'):
            with self.subTest(field), self.assertRaises(SupplyError) as caught:
                self.make_offer(**{field: '5,500'})
            self.assertEqual((caught.exception.code, caught.exception.field), ('ambiguous_amount', field))
        self.assertEqual(SupplierOffer.objects.count(), before)

    def test_the_screen_answers_400_naming_the_field(self):
        from rest_framework.test import APIClient

        api = APIClient()
        api.force_authenticate(self.staff)
        res = api.patch(f'/api/admin/supplier-offers/{self.offer.pk}/?company={self.company.pk}',
                        {'commercial_price': '5.500'}, format='json')
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json().get('field'), 'commercial_price')
        self.offer.refresh_from_db()
        self.assertEqual(self.offer.commercial_price, D('110.00'))

    def test_unambiguous_texts_still_save_and_stored_values_are_untouched(self):
        offer = self.make_offer(product=self.case, commercial_price='5.500,00', agreed_cost='4390,50',
                                valid_from=timezone.localdate() + timedelta(days=20),
                                valid_until=timezone.localdate() + timedelta(days=25))
        offer.refresh_from_db()
        self.assertEqual((offer.commercial_price, offer.agreed_cost), (D('5500.00'), D('4390.50')))
        self.offer.refresh_from_db()
        self.assertEqual(self.offer.commercial_price, D('110.00'))   # an old row is never re-read
        updated = supply_services.update_offer(self.offer, actor=self.staff, may_view_costs=True,
                                               lead_time_days=6)
        self.assertEqual(updated.commercial_price, D('110.00'))


class OfferFileAmountsTest(SupplyShop, TestCase):
    """The supplier file: a NUMBER cell is read as a number, a TEXT cell by the text policy."""

    def setUp(self):
        self.build('b1-0-archivo')
        from rest_framework.test import APIClient

        self.api = APIClient()
        self.api.force_authenticate(self.staff)

    def preview(self, price, cost):
        from store.test_cost_imports import upload
        from store.test_supplier_offers import OFFER_HEADERS

        start = timezone.localdate()
        row = {'Proveedor': 'Mayorista Lima', 'Código/SKU del producto': self.case.slug, 'Modelo': '', 'Capacidad': '',
               'Color': '', 'Línea': '', 'Costo pactado': cost, 'Precio comercial': price, 'Cantidad confirmada': '',
               'Vigente desde': start.isoformat(), 'Vigente hasta': (start + timedelta(days=5)).isoformat(),
               'Plazo (días)': 4}
        res = self.api.post('/api/admin/supplier-offers/import/preview/', {
            'file': upload(OFFER_HEADERS, [[row[h] for h in OFFER_HEADERS]], title='Ofertas de proveedor'),
            'message_date': start.isoformat()}, format='multipart')
        self.assertEqual(res.status_code, 200, res.content)
        return res.json()['rows'][0]

    def test_a_text_cell_that_hides_two_prices_is_a_row_error(self):
        row = self.preview('1800,2000', '1,0000')
        self.assertTrue(any('dos decimales' in e for e in row['errors']), row['errors'])
        self.assertIsNone(row['data'].get('commercial_price'))

    def test_a_text_cell_with_an_ambiguous_price_is_a_row_error(self):
        row = self.preview('5,500', '')
        self.assertTrue(any('ambiguo' in e for e in row['errors']), row['errors'])

    def test_a_number_cell_is_a_number(self):
        row = self.preview(12.345, '')
        self.assertTrue(any('dos decimales' in e for e in row['errors']), row['errors'])
        self.assertFalse(any('ambiguo' in e for e in row['errors']), row['errors'])
        row = self.preview(5500.5, 4390)
        self.assertEqual(row['errors'], [])
        self.assertEqual(row['data']['commercial_price'], '5500.50')
