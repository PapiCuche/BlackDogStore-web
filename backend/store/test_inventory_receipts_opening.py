"""
INV-OPENING-01 — «Recibir mercadería» says what the entry IS in the Kardex.

    purchase (default)  stock bought from a supplier                    → PURCHASE_ENTRY (as before)
    opening             stock the shop already had when it started here → INITIAL_STOCK

Opening stock is a statement that nothing was on that shelf before: the same rule the stock import enforces
(`stock_import_services._has_history`, §31). It is refused, per line and with a code, when the product already has a
Kardex history in that branch or holds stock nobody can account for. Other authorised entries keep their own door
(Movimientos: manual entry, returns, corrections). Nothing already written is touched: no migration, no history rewrite.
"""
from decimal import Decimal

from store.models import BranchStock, StockMovement, StockUnit
from store.test_inventory_receipts import ReceiptBase, unit
from store.test_registration import IMEIS
from store.tests import _prod


class OpeningBase(ReceiptBase):
    def setUp(self):
        super().setUp()
        # A shelf with nothing and no history: what an opening count is for.
        self.adapter = _prod(self.company, 'Adaptador 20W', 'adaptador-apertura', price='79.00', inventory=0)

    def kinds(self):
        return list(StockMovement.objects.order_by('pk').values_list('movement_type', flat=True))


class EntryKindTest(OpeningBase):
    def test_without_entry_kind_a_reception_is_still_a_purchase(self):
        res = self.confirm(self.costed, self.body(self.qty(self.adapter, 3, cost='40.00')), expect=201)
        movement = StockMovement.objects.filter(product=self.adapter).get()
        self.assertEqual(movement.movement_type, StockMovement.PURCHASE_ENTRY)
        self.assertEqual(res.json().get('entry_kind'), 'purchase')

    def test_an_opening_count_by_quantity_is_initial_stock_with_its_cost(self):
        body = self.body(self.qty(self.adapter, 5, cost='35.00'), entry_kind='opening')
        preview = self.preview(self.costed, body, expect=200).json()
        self.assertTrue(preview['ok'], preview)
        self.assertEqual(preview.get('entry_kind'), 'opening')
        res = self.confirm(self.costed, body, expect=201)
        movement = StockMovement.objects.filter(product=self.adapter).get()
        self.assertEqual((movement.movement_type, movement.quantity), (StockMovement.INITIAL_STOCK, 5))
        self.assertEqual(movement.unit_cost, Decimal('35.00'))
        self.assertEqual(BranchStock.objects.get(branch=self.centro, product=self.adapter).quantity, 5)
        self.assertEqual(res.json().get('entry_kind'), 'opening')

    def test_an_opening_count_of_devices_is_initial_stock(self):
        body = self.body(self.dev(self.phone, unit('OPN-SER-1', IMEIS[0])), entry_kind='opening')
        self.confirm(self.costed, body, expect=201)
        self.assertEqual(StockUnit.objects.filter(product=self.phone).count(), 1)
        self.assertEqual(StockMovement.objects.filter(product=self.phone).get().movement_type,
                         StockMovement.INITIAL_STOCK)

    def test_an_unknown_entry_kind_is_a_400_on_its_field(self):
        res = self.preview(self.costed, self.body(self.qty(self.adapter, 1), entry_kind='gift'))
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json().get('field'), 'entry_kind')

    def test_the_entry_kind_is_part_of_the_idempotency_fingerprint(self):
        key = self.key()
        self.confirm(self.costed, self.body(self.qty(self.adapter, 2)), key=key, expect=201)
        # Same key, same lines, another kind: not a replay of the first one.
        res = self.confirm(self.costed, self.body(self.qty(self.adapter, 2), entry_kind='opening'), key=key)
        self.assertNotEqual(res.status_code, 201, res.content)


class OpeningPreconditionTest(OpeningBase):
    def assert_opening_refused(self, product, line):
        before = self.snapshot()
        body = self.body(line, entry_kind='opening')
        data = self.preview(self.costed, body, expect=200).json()
        self.assertFalse(data['ok'], data)
        self.assertIn('opening_has_history', data['lines'][0].get('codes', []), data)
        self.assertTrue(any('compra' in e for e in data['lines'][0]['errors']), data['lines'][0]['errors'])
        res = self.confirm(self.costed, body, expect=400)
        self.assertEqual(res.json()['code'], 'invalid_lines')
        self.assertEqual(self.snapshot(), before)

    def test_a_shelf_with_kardex_history_cannot_take_an_opening_count(self):
        self.confirm(self.costed, self.body(self.qty(self.adapter, 1)), expect=201)
        self.assert_opening_refused(self.adapter, self.qty(self.adapter, 4))

    def test_stock_with_no_kardex_at_all_is_refused_too(self):
        # The cable holds 100 units placed without a Kardex line: provenance unknown (§31).
        self.assertFalse(StockMovement.objects.filter(product=self.cable, branch=self.centro).exists())
        self.assert_opening_refused(self.cable, self.qty(self.cable, 4))

    def test_history_in_another_branch_does_not_block_this_one(self):
        self.confirm(self.costed, self.body(self.qty(self.adapter, 1)), expect=201)
        body = self.body(self.qty(self.adapter, 2), entry_kind='opening', branch=self.norte.pk)
        self.confirm(self.costed, body, expect=201)
        self.assertEqual(StockMovement.objects.filter(product=self.adapter, branch=self.norte).get().movement_type,
                         StockMovement.INITIAL_STOCK)

    def test_a_purchase_is_never_refused_for_history(self):
        self.confirm(self.costed, self.body(self.qty(self.adapter, 1)), expect=201)
        self.confirm(self.costed, self.body(self.qty(self.adapter, 1)), expect=201)
        self.assertEqual(self.kinds().count(StockMovement.PURCHASE_ENTRY), 2)

    def test_history_written_after_the_preview_still_refuses_the_confirmation(self):
        body = self.body(self.qty(self.adapter, 3), entry_kind='opening')
        self.assertTrue(self.preview(self.costed, body, expect=200).json()['ok'])
        self.confirm(self.costed, self.body(self.qty(self.adapter, 1)), expect=201)  # somebody else, meanwhile
        res = self.confirm(self.costed, body, expect=400)
        self.assertEqual(res.json()['code'], 'invalid_lines')
        self.assertFalse(StockMovement.objects.filter(movement_type=StockMovement.INITIAL_STOCK).exists())
