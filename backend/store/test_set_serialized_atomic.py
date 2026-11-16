"""
INV-S5-01 follow-up — `set_serialized` runs in its OWN transaction.

INV-S5-01 inserted `holds_stock` between `@transaction.atomic` and `set_serialized`, so the decorator moved to the
new function and `set_serialized` lost it. Its `select_for_update` then needs a transaction the caller may not
have: the tracking endpoint (`stock_unit_views`, the «Equipos» switch) calls it bare, and Django refuses
«select_for_update cannot be used outside of a transaction» — a 500 in production. A `TestCase` hides it (every test
runs inside a transaction), so this one is a `TransactionTestCase`.
"""
from django.test import TransactionTestCase

from store import stock_unit_services as units
from store.models import Product
from store.tests import _p3_company, _prod


class SetSerializedOwnsItsTransactionTest(TransactionTestCase):
    def test_it_can_be_called_without_a_surrounding_transaction(self):
        company = _p3_company('s5-atomic', 'Tienda atómica')
        product = _prod(company, 'Funda atómica', 'funda-atomica', price='10.00', inventory=0)
        updated = units.set_serialized(product, serialized=True, requires_imei=False)
        self.assertTrue(updated.is_serialized)
        self.assertTrue(Product.objects.get(pk=product.pk).is_serialized)
