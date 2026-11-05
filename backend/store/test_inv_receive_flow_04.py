"""
INV-RECEIVE-FLOW-04 — ONE way to receive a pasted list, and every used device keeps the price typed for it.

    match -> receipts/preview -> receipts/confirm -> GET /api/admin/inventory/units/?line=used

The owner's case: a WhatsApp list of used phones, a different selling price for each one, received once. What
these tests hold, across the REAL endpoints:

  * each device is stored with ITS price (never the cost, never a neighbour's, never the article's);
  * the device's own record says that price afterwards, and «sin precio» lists only the one left unpriced;
  * the price travels only with `products.manage` (a person who only receives is refused, `prices_forbidden`, and nothing is written);
  * the second path that registered devices from the list WITHOUT a price (`…/text-preview/units/preview/`) is
    gone: the list is received only through «Recibir mercadería».

Everything fictitious: invented serials, IMEI built for the test, made-up amounts.
"""
from decimal import Decimal

from store.models import StockMovement, StockUnit
from store.test_receive_list_flow import CONFIRM, PREVIEW, _Flow
from store.test_registration import _client
from store.tests import _p2d_member

UNITS = '/api/admin/inventory/units/'
OLD_PATH = '/api/admin/inventory/text-preview/units/preview/'
PRICES = ['1499.00', '1549.00', '1899.00', '2999.00', '899.00', '1699.00', '2199.00']


class EightPricesTest(_Flow):
    def eight(self):
        """Eight devices of the list's articles: seven priced one by one, the eighth left without a price."""
        rows, answers = self.resolve_everything()
        products = [answers[r['key']]['options'][0]['product_id'] for r in rows]
        units = []
        for n in range(8):
            unit = self.unit_row()
            unit.pop('cosmetic_grade')
            if n < 7:
                unit['sale_price'] = PRICES[n]
            units.append((products[n % len(products)], unit))
        lines = {}
        for product, unit in units:
            lines.setdefault(product, []).append(unit)
        body = {'branch': self.branch.pk, 'supplier': None, 'document_ref': 'LISTA-8', 'notes': '',
                'lines': [{'kind': 'units', 'product': pk, 'units': us} for pk, us in lines.items()]}
        return body, [unit for _product, unit in units]

    def test_each_device_keeps_its_own_price_and_its_record_says_so(self):
        body, sent = self.eight()
        self.assertEqual(self.api.post(PREVIEW, body, format='json').status_code, 200)
        res = self.api.post(CONFIRM, body, format='json', HTTP_IDEMPOTENCY_KEY='flow04-eight')
        self.assertEqual(res.status_code, 201, res.content)

        for unit in sent:
            stored = StockUnit.objects.get(company=self.company, serial_number=unit['serial'])
            self.assertEqual(stored.sale_price, Decimal(unit['sale_price']) if 'sale_price' in unit else None, unit['serial'])
            # No cost was sent (this person does not see costs): the price never stands in for it.
            self.assertIsNone(stored.cost)

        # The device's own record, as «Equipos seminuevos» reads it.
        listed = self.api.get(UNITS, {'line': 'used', 'branch': 'all', 'priced': 'yes'}).json()
        rows = listed['results'] if isinstance(listed, dict) else listed
        prices = {row['serial_number']: row['sale_price'] for row in rows}
        for unit in sent[:7]:
            self.assertEqual(Decimal(prices[unit['serial']]), Decimal(unit['sale_price']))
        unpriced = self.api.get(UNITS, {'line': 'used', 'branch': 'all', 'priced': 'no'}).json()
        unpriced_rows = unpriced['results'] if isinstance(unpriced, dict) else unpriced
        self.assertEqual({row['serial_number'] for row in unpriced_rows}, {sent[7]['serial']})

        # A replay of the same confirmation adds nothing.
        before = (StockUnit.objects.filter(company=self.company).count(),
                  StockMovement.objects.filter(product__company=self.company).count())
        again = self.api.post(CONFIRM, body, format='json', HTTP_IDEMPOTENCY_KEY='flow04-eight')
        self.assertLess(again.status_code, 300, again.content)
        self.assertEqual((StockUnit.objects.filter(company=self.company).count(),
                          StockMovement.objects.filter(product__company=self.company).count()), before)

    def test_a_person_who_only_receives_cannot_send_a_price(self):
        body, sent = self.eight()
        clerk, _ = _p2d_member(self.company, 'flow04_clerk', ['company.view', 'products.view', 'inventory.view', 'inventory.adjust'])
        res = _client(clerk).post(CONFIRM, body, format='json', HTTP_IDEMPOTENCY_KEY='flow04-clerk')
        self.assertEqual((res.status_code, res.json().get('code')), (400, 'prices_forbidden'), res.content)
        self.assertFalse(StockUnit.objects.filter(company=self.company, serial_number__in=[u['serial'] for u in sent]).exists())

    def test_the_second_path_that_registered_the_list_without_a_price_is_gone(self):
        res = self.api.post(OLD_PATH, {'branch': self.branch.pk, 'reason': 'x', 'rows': []}, format='json')
        self.assertEqual(res.status_code, 404)
