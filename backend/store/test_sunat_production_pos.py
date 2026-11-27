"""
SUNAT-PROD-C3 — the cash desk sells in the SUNAT environment it showed (review of #468, 🟡5).

The screen sends `expected_environment` with a fiscal sale. If the server would now issue in
another one (production switched on or off since the till loaded), the sale is a 409 and
nothing is written: no order, no number, no stock moved.
"""
from django.test import override_settings

from store.models import FiscalDocument, FiscalSeries, Order
from store.tests import _BETA_ISSUING, Ip1PosBase

URL = '/api/v1/internal/ip1-tienda/sales/pos/sales/'


@override_settings(**_BETA_ISSUING)
class TheTillSellsInTheEnvironmentItShowedTest(Ip1PosBase):
    def setUp(self):
        super().setUp()
        self.client = self.only_caps('sales.pos.use', 'sales.fiscal.issue', 'sales.notes.manage')
        self.company.tax_id = '20100066603'
        self.company.legal_name = 'EMPRESA PRUEBA SAC'
        self.company.save()
        for kind, series in [('01', 'F001'), ('03', 'B001')]:
            FiscalSeries.objects.create(company=self.company, document_type=kind, series=series)

    def sell(self, **over):
        return self.client.post(URL, self.sale_body(receipt_type='boleta', **over), format='json')

    def test_another_environment_than_the_one_shown_is_a_409_and_writes_nothing(self):
        before = self.stock()
        response = self.sell(expected_environment='production')
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual((response.data['code'], response.data['environment']), ('environment_changed', 'beta'))
        self.assertFalse(Order.objects.filter(company=self.company).exists())
        self.assertFalse(FiscalDocument.objects.exists())
        self.assertEqual(self.stock(), before)
        self.assertEqual(FiscalSeries.objects.get(company=self.company, series='B001').next_number, 1)

    def test_the_environment_shown_sells(self):
        response = self.sell(expected_environment='beta')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(FiscalDocument.objects.get(order_id=response.data['order_id']).environment, 'beta')

    def test_the_context_tells_the_screen_which_one_to_send(self):
        context = self.client.get('/api/v1/internal/ip1-tienda/sales/pos/context/').json()
        boleta = next(o for o in context['receipt_options'] if o['value'] == 'boleta')
        self.assertEqual(boleta['environment'], 'beta')
