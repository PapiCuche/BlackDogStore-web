"""
INV-S5-01 — the FIRST reception decides that a product is counted by device (owner's decision S5, A1b-2).

A variant created from a model's palette (or by a product upload) is born NOT counted by device: «counting by unit is
a stock decision, taken by the first receipt with `inventory.adjust`» (`device_catalog.create_variant_product`). The
receipt never let that happen: a «units» line for such a product was refused («recíbelo por cantidad») and the
wizard offered only «Por cantidad», so iPhones could enter with no serial or IMEI, and once on the shelf the product
can no longer change how it is counted (`stock_unit_services.set_serialized`).

Now a «units» line for a product NOT counted by device is accepted ONLY while the product has nothing on any shelf
and no live device; confirming it switches the product to «por equipo» (under the product's lock, the same rule as
`set_serialized`) before the devices are received, all in the same transaction. A product with stock still refuses.
A «quantity» line for a model's variant that could still be counted by device warns, so it is never silent.
"""
from django.db import transaction

from store import test_inventory_receipts as base
from store.models import AdminAuditLog, BranchStock, Product, ProductModel, StockUnit
from store.test_registration import IMEIS
from store.tests import _prod

unit = base.unit


class FirstReceiptDecidesUnitsTest(base.ReceiptBase):
    def setUp(self):
        super().setUp()
        self.model = ProductModel.objects.create(company=self.company, name='iPhone 17 Recepción',
                                                 slug='iphone-17-recepcion')
        self.variant = _prod(self.company, 'iPhone 17 256 GB Negro', 'iphone-17-256-negro', price='0.00', inventory=0)
        Product.objects.filter(pk=self.variant.pk).update(product_model=self.model)
        self.variant.refresh_from_db()
        self.assertFalse(self.variant.is_serialized)

    def test_the_preview_accepts_devices_for_an_untracked_empty_product_and_says_what_will_change(self):
        before = self.snapshot()
        data = self.preview(self.clerk, self.body(self.dev(self.variant, unit('S5A00001', IMEIS[0]))), expect=200).json()
        self.assertTrue(data['ok'], data)
        [line] = data['lines']
        self.assertEqual((line['kind'], line['status'], line['will_receive']), ('units', 'ok', 1))
        self.assertTrue(any('por equipo' in w for w in line['warnings']), line['warnings'])
        self.assertEqual(self.snapshot(), before)
        self.variant.refresh_from_db()
        self.assertFalse(self.variant.is_serialized)

    def test_confirming_switches_the_product_to_devices_and_receives_them(self):
        self.confirm(self.clerk, self.body(self.dev(self.variant, unit('S5A00001', IMEIS[0]),
                                                    unit('S5A00002', IMEIS[1]))), expect=201)
        self.variant.refresh_from_db()
        self.assertTrue(self.variant.is_serialized)
        self.assertTrue(self.variant.requires_imei)          # every device came with its IMEI
        self.assertEqual(StockUnit.objects.filter(product=self.variant).count(), 2)
        self.assertEqual(self.shelf(self.variant), 2)
        self.assertTrue(AdminAuditLog.objects.filter(action='product_serialization_changed',
                                                     target_id=self.variant.pk).exists())

    def test_devices_without_imei_switch_to_serial_only(self):
        laptop = _prod(self.company, 'MacBook S5', 'macbook-s5', price='0.00', inventory=0)
        self.confirm(self.clerk, self.body(self.dev(laptop, unit('S5MAC0001'))), expect=201)
        laptop.refresh_from_db()
        self.assertEqual((laptop.is_serialized, laptop.requires_imei), (True, False))

    def test_a_product_with_stock_still_refuses_devices_and_nothing_changes(self):
        before = self.snapshot()
        data = self.preview(self.clerk, self.body(self.dev(self.cable, unit('S5C00001'))), expect=200).json()
        self.assertFalse(data['ok'])
        self.assertTrue(any('existencias' in e or 'stock' in e for e in data['lines'][0]['errors']), data)
        self.confirm(self.clerk, self.body(self.dev(self.cable, unit('S5C00001'))))
        self.assertEqual(self.snapshot(), before)
        self.cable.refresh_from_db()
        self.assertFalse(self.cable.is_serialized)

    def test_a_failed_receipt_leaves_the_product_untracked(self):
        # Two lines: the first would switch the variant, the second is refused → nothing at all is written.
        StockUnit.objects.none()
        before = self.snapshot()
        body = self.body(self.dev(self.variant, unit('S5A00009', IMEIS[2])), self.dev(self.cable, unit('S5C00009')))
        self.confirm(self.clerk, body)
        self.assertEqual(self.snapshot(), before)
        self.variant.refresh_from_db()
        self.assertFalse(self.variant.is_serialized)

    def test_stock_arriving_between_check_and_switch_is_refused_whole(self):
        # The switch re-checks the shelf under the product's lock (the race the preview cannot see).
        from store import receipt_services
        original = receipt_services.check

        def check_then_stock(*args, **kwargs):
            verdicts = original(*args, **kwargs)
            BranchStock.objects.update_or_create(branch=self.norte, product=self.variant, defaults={'quantity': 3})
            return verdicts

        receipt_services.check = check_then_stock
        try:
            res = self.confirm(self.clerk, self.body(self.dev(self.variant, unit('S5A00010', IMEIS[3]))))
        finally:
            receipt_services.check = original
        self.assertIn(res.status_code, (400, 409), res.content)
        self.variant.refresh_from_db()
        self.assertFalse(self.variant.is_serialized)
        self.assertFalse(StockUnit.objects.filter(product=self.variant).exists())

    def test_a_quantity_line_for_a_model_variant_warns_that_it_could_be_counted_by_device(self):
        data = self.preview(self.clerk, self.body(self.qty(self.variant, 2)), expect=200).json()
        [line] = data['lines']
        self.assertEqual(line['status'], 'ok')
        self.assertTrue(any('por equipo' in w for w in line['warnings']), line['warnings'])
        # An accessory without a model does not warn.
        plain = self.preview(self.clerk, self.body(self.qty(self.case, 2)), expect=200).json()
        self.assertEqual(plain['lines'][0]['warnings'], [])

    def test_another_company_cannot_switch_my_product(self):
        res = self.confirm(self.outsider, {**self.body(self.dev(self.variant, unit('S5X00001', IMEIS[4]))),
                                           'branch': self.foreign_branch.pk})
        self.assertIn(res.status_code, (400, 403, 404), res.content)
        self.variant.refresh_from_db()
        self.assertFalse(self.variant.is_serialized)
