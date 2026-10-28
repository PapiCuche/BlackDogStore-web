"""
LABEL-A (LABEL-AUTO-01) — every CONFIRMED physical entry into internal stock leaves a persisted, idempotent
label batch ready to print. Printing, PDF and screens are LABEL-B; here is only the record.

  * devices: one item per newly received `StockUnit`; accessories: copies = the RECEIVED delta of the
    movement (never `BranchStock`), split at `MAX_COPIES`, never truncated;
  * the batch is made INSIDE the transaction of the event, in a savepoint of its own: a rolled-back
    reception has no batch, and a failure to label never undoes a confirmed reception;
  * idempotent by `source_key` (`auto:label:movement:<pk>` / `auto:label:job:<pk>:branch:<pk>`);
  * a list of ALLOWED entry points, pinned here, and a test per flow that must NOT label.
"""
import re
from pathlib import Path
from threading import Barrier, Thread
from unittest import mock

from django.db import IntegrityError, connection, transaction
from django.test import TestCase, TransactionTestCase
from rest_framework.test import APIClient

from store import inventory_services as inventory
from store import label_batches
from store import stock_unit_services as units
from store import unit_import_services as unit_import
from store.models import (
    AdminAuditLog, BulkImportJob, LabelBatch, LabelBatchItem, LabelPrintEvent, Product, ProductBarcode,
    StockMovement, StockUnit,
)
from store.test_registration import IMEIS, RegistrationFixture
from store.test_stock_units import IMEI_A, IMEI_B, IMEI_C, IMEI_D, UnitsBase, imei
from store.test_unit_import import ImportBase, apply_url
from store.tests import _m7_stock, _m7_url, _prod

MOVEMENTS_URL = '/api/admin/inventory/movements/'
UNITS_URL = '/api/admin/inventory/units/'
IMEI_E, IMEI_F = (imei(f'3569380356439{n}') for n in ('0', '1'))


def batches(company=None):
    rows = LabelBatch.objects.all()
    return rows.filter(company=company) if company is not None else rows


class LabelFixture(UnitsBase):
    def setUp(self):
        super().setUp()
        self.api = APIClient()
        self.api.force_authenticate(user=self.staff)

    def manual(self, movement_type, quantity, product=None, branch=None):
        return inventory.apply_manual_stock_movement(
            branch=branch or self.branch_a, product_id=(product or self.other_product).pk,
            movement_type=movement_type, quantity=quantity, reason='Ingreso de prueba', actor=self.staff)


# ---------------------------------------------------------------------------
# Devices: one label per unit received
# ---------------------------------------------------------------------------

class DeviceBatchTest(LabelFixture):
    def test_one_device_makes_one_label_with_the_code_of_the_unit(self):
        received = self.receive(self.row('SERIE00001', IMEI_A))
        batch = label_batches.create_for_movement(received.movement, units=received, actor=self.staff)

        self.assertEqual(batch.status, LabelBatch.READY)
        self.assertEqual(batch.reason, LabelBatch.AUTO)
        self.assertEqual((batch.company_id, batch.branch_id), (self.company.pk, self.branch_a.pk))
        self.assertEqual(batch.movement_id, received.movement.pk)
        self.assertEqual(batch.source_key, f'auto:label:movement:{received.movement.pk}')
        self.assertEqual(batch.created_by, self.staff)
        [item] = batch.items.all()
        self.assertEqual((item.unit_id, item.product_id, item.copies), (received[0].pk, self.phone.pk, 1))
        self.assertFalse(item.needs_review)
        self.assertTrue(StockUnit.objects.get(pk=item.unit_id).label_code)

    def test_three_refurbished_devices_make_three_labels_with_different_identities(self):
        received = self.receive(
            self.row('SERIE00001', IMEI_A, condition='refurbished'),
            self.row('SERIE00002', IMEI_B, condition='refurbished'),
            self.row('SERIE00003', IMEI_C, condition='refurbished'))
        batch = label_batches.create_for_movement(received.movement, units=received, actor=self.staff)

        items = list(batch.items.select_related('unit'))
        self.assertEqual(len(items), 3)
        self.assertEqual(len({i.unit_id for i in items}), 3)
        self.assertEqual(len({i.unit.label_code for i in items}), 3)
        self.assertEqual(label_batches.summary(batch)['labels_total'], 3)

    def test_the_units_are_read_from_the_movement_when_not_passed(self):
        received = self.receive(self.row('SERIE00001', IMEI_A), self.row('SERIE00002', IMEI_B))
        batch = label_batches.create_for_movement(received.movement, actor=self.staff)
        self.assertEqual(sorted(i.unit_id for i in batch.items.all()), sorted(u.pk for u in received))

    def test_repeating_the_same_movement_never_makes_a_second_batch(self):
        received = self.receive(self.row('SERIE00001', IMEI_A))
        first = label_batches.create_for_movement(received.movement, units=received, actor=self.staff)
        again = label_batches.create_for_movement(received.movement, units=received, actor=self.staff)

        self.assertEqual(first.pk, again.pk)
        self.assertEqual(batches().count(), 1)
        self.assertEqual(LabelBatchItem.objects.count(), 1)

    def test_old_stock_is_never_relabelled_by_a_new_reception(self):
        old = self.receive(self.row('SERIE00001', IMEI_A))
        label_batches.create_for_movement(old.movement, units=old, actor=self.staff)
        new = self.receive(self.row('SERIE00002', IMEI_B))
        batch = label_batches.create_for_movement(new.movement, units=new, actor=self.staff)

        self.assertEqual([i.unit_id for i in batch.items.all()], [new[0].pk])
        self.assertEqual(LabelBatchItem.objects.filter(unit=old[0]).count(), 1)

    def test_a_unit_that_cannot_be_labelled_is_flagged_not_dropped(self):
        received = self.receive(self.row('SERIE00001', IMEI_A), self.row('SERIE00002', IMEI_B))
        StockUnit.objects.filter(pk=received[1].pk).update(status=StockUnit.Status.SOLD)
        batch = label_batches.create_for_movement(received.movement, actor=self.staff)

        flagged = {i.unit_id: i.needs_review for i in batch.items.all()}
        self.assertEqual(flagged, {received[0].pk: False, received[1].pk: True})
        self.assertEqual(label_batches.summary(batch)['needs_review'], 1)

    def test_a_unit_of_another_company_is_never_put_in_the_batch(self):
        mine = self.receive(self.row('SERIE00001', IMEI_A))
        foreign = units.receive_units(
            branch=self.foreign_branch, product=self.foreign_phone, units=[self.row('AJENA00001', IMEI_D)],
            actor=self.staff, reason='Compra')
        batch = label_batches.create_for_movement(mine.movement, units=[*mine, *foreign], actor=self.staff)
        self.assertEqual([i.unit_id for i in batch.items.all()], [mine[0].pk])

    def test_a_reception_that_is_rolled_back_leaves_no_batch(self):
        with self.assertRaises(RuntimeError), transaction.atomic():
            received = self.receive(self.row('SERIE00001', IMEI_A))
            self.assertIsNotNone(
                label_batches.create_for_movement(received.movement, units=received, actor=self.staff))
            raise RuntimeError('the caller fails after the reception')
        self.assertEqual(batches().count(), 0)
        self.assertEqual(StockUnit.objects.count(), 0)

    def test_a_failure_to_label_does_not_undo_the_reception(self):
        received = self.receive(self.row('SERIE00001', IMEI_A))
        with mock.patch.object(LabelBatchItem.objects, 'bulk_create', side_effect=RuntimeError('boom')), \
                self.assertLogs('store.label_batches', level='ERROR'):
            result = label_batches.create_for_movement(received.movement, units=received, actor=self.staff)

        self.assertIs(result, label_batches.FAILED)
        self.assertEqual(batches().count(), 0, 'no half-made batch survives its savepoint')
        self.assertEqual(self.quantity(), 1)
        self.assertEqual(StockMovement.objects.filter(product=self.phone).count(), 1)

    def test_the_post_of_registrar_equipos_answers_the_batch(self):
        res = self.api.post(UNITS_URL, {
            'branch': self.branch_a.pk, 'product_id': self.phone.pk, 'reason': 'Compra',
            'units': [{'serial_number': 'SERIE00001', 'imei': IMEI_A},
                      {'serial_number': 'SERIE00002', 'imei': IMEI_B}]}, format='json')

        self.assertEqual(res.status_code, 201, res.content)
        body = res.json()
        self.assertEqual(len(body['results']), 2)
        summary = body['label_batch']
        self.assertEqual((summary['status'], summary['items'], summary['labels_total'], summary['needs_review']),
                         ('ready', 2, 2, 0))
        self.assertEqual(LabelBatch.objects.get(pk=summary['id']).company_id, self.company.pk)
        # Nothing about the identity of the devices rides in the summary.
        for secret in (IMEI_A, IMEI_B, 'SERIE00001', 'SERIE00002'):
            self.assertNotIn(secret, str(summary))


# ---------------------------------------------------------------------------
# Accessories: copies = what was received
# ---------------------------------------------------------------------------

class AccessoryBatchTest(LabelFixture):
    def test_fifty_chargers_with_a_hundred_already_in_stock_make_exactly_fifty_labels(self):
        _m7_stock(self.branch_a, self.other_product, 100)
        movement = self.manual(StockMovement.PURCHASE_ENTRY, 50)
        batch = label_batches.create_for_movement(movement, actor=self.staff)

        self.assertEqual(label_batches.summary(batch)['labels_total'], 50)
        [item] = batch.items.all()
        self.assertEqual((item.copies, item.unit_id, item.product_id), (50, None, self.other_product.pk))
        self.assertEqual(inventory.branch_quantity(self.branch_a, self.other_product), 150)

    def test_more_than_the_cap_is_split_into_several_items_never_truncated(self):
        movement = self.manual(StockMovement.PURCHASE_ENTRY, 1200)
        batch = label_batches.create_for_movement(movement, actor=self.staff)

        copies = [i.copies for i in batch.items.order_by('pk')]
        self.assertEqual(copies, [500, 500, 200])
        self.assertEqual(label_batches.summary(batch)['labels_total'], 1200)
        self.assertTrue(all(c <= label_batches.MAX_COPIES for c in copies))

    def test_the_cap_is_the_one_of_the_label_screen(self):
        from store import label_views
        self.assertEqual(label_batches.MAX_COPIES, label_views.MAX_COPIES)
        self.assertEqual(label_batches.LABELLABLE_UNIT_STATUSES, label_views.LABELLABLE_UNIT_STATUSES)

    def move(self, movement_type, quantity=7):
        return self.api.post(MOVEMENTS_URL, {
            'product_id': self.other_product.pk, 'movement_type': movement_type, 'quantity': quantity,
            'reason': 'Ingreso', 'branch': self.branch_a.pk}, format='json')

    def test_the_movements_endpoint_labels_a_purchase_and_answers_the_batch(self):
        res = self.move('purchase_entry')

        self.assertEqual(res.status_code, 201, res.content)
        summary = res.json()['label_batch']
        self.assertEqual((summary['status'], summary['labels_total']), ('ready', 7))
        self.assertFalse(res.json()['label_error'])

    def test_two_purchases_are_two_receptions_and_each_has_its_own_batch(self):
        first = self.move('purchase_entry', 4).json()['label_batch']['id']
        second = self.move('purchase_entry', 4).json()['label_batch']['id']
        self.assertNotEqual(first, second)  # a retry of ONE entry is the service test

    def test_a_manual_entry_is_not_a_purchase_and_never_labels(self):
        # Found stock and positive adjustments are often corrections (P2-1 of the review of #194).
        res = self.move('manual_entry')
        self.assertEqual(res.status_code, 201, res.content)
        self.assertIsNone(res.json()['label_batch'])
        self.assertFalse(res.json()['label_error'])
        self.assertEqual(batches().count(), 0)

    def test_the_v1_endpoint_labels_a_purchase_and_not_a_manual_entry(self):
        url = _m7_url('m7-shop', 'adjustments')
        body = {'branch_id': self.branch_a.pk, 'product_slug': self.other_product.slug, 'quantity': 6,
                'reason': 'Ingreso'}
        manual = self.client.post(url, {**body, 'movement_type': 'manual_entry'}, format='json')
        self.assertEqual(manual.status_code, 201, manual.content)
        self.assertIsNone(manual.json()['label_batch'])
        self.assertEqual(batches().count(), 0)

        purchase = self.client.post(url, {**body, 'movement_type': 'purchase_entry'}, format='json')
        self.assertEqual(purchase.status_code, 201, purchase.content)
        self.assertEqual(purchase.json()['label_batch']['labels_total'], 6)
        self.assertFalse(purchase.json()['label_error'])
        self.assertEqual(batches().count(), 1)

    def test_the_product_adjust_endpoint_only_makes_manual_entries_so_it_never_labels(self):
        res = self.api.post(f'/api/admin/products/{self.other_product.pk}/inventory-adjust/',
                            {'delta': 5, 'reason': 'Encontrado en el almacén', 'branch': self.branch_a.pk},
                            format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(StockMovement.objects.filter(movement_type='manual_entry').count(), 1)
        self.assertNotIn('label_batch', res.json())
        self.assertEqual(batches().count(), 0)

    def test_corrections_and_returns_by_hand_never_label(self):
        for movement_type in (StockMovement.CORRECTION_POSITIVE, StockMovement.RETURN_ENTRY):
            with self.subTest(movement_type):
                res = self.move(movement_type, 5)
                self.assertEqual(res.status_code, 201, res.content)
                self.assertIsNone(res.json()['label_batch'])
        self.assertEqual(batches().count(), 0)

    def test_an_exit_never_labels(self):
        _m7_stock(self.branch_a, self.other_product, 10)
        res = self.api.post(MOVEMENTS_URL, {
            'product_id': self.other_product.pk, 'movement_type': 'manual_exit', 'quantity': 2,
            'reason': 'Salida', 'branch': self.branch_a.pk}, format='json')
        self.assertEqual(res.status_code, 201, res.content)
        self.assertIsNone(res.json()['label_batch'])
        self.assertEqual(batches().count(), 0)

    def test_the_service_itself_refuses_a_type_outside_the_allowed_list(self):
        for movement_type in (StockMovement.CORRECTION_POSITIVE, StockMovement.MANUAL_ENTRY):
            movement = self.manual(movement_type, 5)
            self.assertIsNone(label_batches.create_for_movement(movement, actor=self.staff), movement_type)
        self.assertEqual(batches().count(), 0)

    def test_the_product_creation_with_opening_stock_answers_the_batch(self):
        self.with_capabilities('products.view', 'products.manage', 'inventory.view', 'inventory.adjust')
        admin = self.api
        res = admin.post('/api/admin/products/', {
            'name': 'Cargador 20W', 'slug': 'cargador-20w-lbl', 'price': '59.90', 'code': 'CARG20W-LBL',
            'is_active': True, 'inventory': 30}, format='json')

        self.assertEqual(res.status_code, 201, res.content)
        body = res.json()
        self.assertEqual(body['label_batch']['labels_total'], 30, body)
        product = Product.objects.get(pk=body['id'])
        self.assertEqual(batches().get().items.get().product_id, product.pk)

    def test_the_product_creation_without_stock_has_no_batch(self):
        self.with_capabilities('products.view', 'products.manage', 'inventory.view', 'inventory.adjust')
        admin = self.api
        res = admin.post('/api/admin/products/', {
            'name': 'Cargador 30W', 'slug': 'cargador-30w-lbl', 'price': '69.90', 'code': 'CARG30W-LBL',
            'is_active': True}, format='json')
        self.assertEqual(res.status_code, 201, res.content)
        self.assertIsNone(res.json().get('label_batch'))
        self.assertEqual(batches().count(), 0)


# ---------------------------------------------------------------------------
# «Registrar artículo»
# ---------------------------------------------------------------------------

class RegistrationBatchTest(RegistrationFixture):
    def test_devices_make_one_batch_with_one_item_per_unit_and_the_answer_names_it(self):
        data = self.post(self.clerk, self.new_device(), expect=201).json()

        [summary] = data['label_batches']
        self.assertEqual((summary['status'], summary['items'], summary['labels_total']), ('ready', 2, 2))
        batch = LabelBatch.objects.get(pk=summary['id'])
        self.assertEqual(sorted(i.unit_id for i in batch.items.all()), sorted(u['id'] for u in data['units']))
        self.assertEqual(batch.branch_id, self.centro.pk)

    def test_accessories_make_one_batch_per_branch_with_the_quantity_received(self):
        data = self.post(self.clerk, self.accessory(), expect=201).json()

        totals = sorted(
            (LabelBatch.objects.get(pk=s['id']).branch_id, s['labels_total']) for s in data['label_batches'])
        self.assertEqual(totals, sorted([(self.centro.pk, 5), (self.norte.pk, 3)]))

    def test_registering_more_of_an_existing_variant_labels_only_the_new_devices(self):
        first = self.post(self.clerk, self.new_device(), expect=201).json()
        again = self.post(self.clerk, self.new_device(units=[{'serial_number': 'SNREG009', 'imei': IMEIS[9]}]),
                          expect=201).json()

        self.assertFalse(again['product']['created'])
        [summary] = again['label_batches']
        self.assertEqual(summary['labels_total'], 1)
        self.assertNotEqual(summary['id'], first['label_batches'][0]['id'])
        new_unit = LabelBatch.objects.get(pk=summary['id']).items.get().unit
        self.assertEqual(new_unit.serial_number, 'SNREG009')

    def test_a_replay_with_the_same_key_shows_the_same_batch_and_makes_no_second(self):
        body = self.accessory()
        first = self.post(self.clerk, body, key='lbl-key', expect=201).json()
        count = batches().count()
        again = self.post(self.clerk, body, key='lbl-key', expect=200).json()

        self.assertTrue(again['replayed'])
        self.assertEqual([s['id'] for s in again['label_batches']], [s['id'] for s in first['label_batches']])
        self.assertEqual(again['label_batches'], first['label_batches'])
        self.assertEqual(batches().count(), count)

    def test_a_dry_run_makes_no_batch_and_shows_none(self):
        data = self.post(self.clerk, self.accessory(dry_run=True), key=False, expect=200).json()
        self.assertEqual(data['label_batches'], [])
        self.assertEqual(batches().count(), 0)

    def test_a_dry_run_does_not_even_try_to_label(self):
        with mock.patch.object(label_batches, 'create_for_movement') as spy:
            self.post(self.clerk, self.new_device(dry_run=True), key=False, expect=200)
            self.post(self.clerk, self.accessory(dry_run=True), key=False, expect=200)
        spy.assert_not_called()

    def test_a_refused_registration_leaves_no_batch(self):
        body = self.accessory(stock=[{'branch': self.centro.pk, 'quantity': 5}, {'branch': self.norte.pk,
                                                                                   'quantity': 0}])
        self.post(self.clerk, body, expect=400)
        self.assertEqual(batches().count(), 0)

    def test_an_on_order_article_has_no_stock_and_no_batch(self):
        data = self.post(self.clerk, self.on_order(), expect=201).json()
        self.assertEqual(data['label_batches'], [])
        self.assertEqual(batches().count(), 0)


# ---------------------------------------------------------------------------
# Bulk load of devices: one batch per job and branch
# ---------------------------------------------------------------------------

class ImportBatchTest(ImportBase):
    def stage(self, count=2, start=0):
        imeis = [IMEI_A, IMEI_B, IMEI_C, IMEI_D, IMEI_E, IMEI_F]
        rows = [self.phone_row(f'IMPORT{start + n:04d}', imeis[start + n]) for n in range(count)]
        return self.staged(rows)

    def apply(self, payload):
        return self.api.post(apply_url(payload['id']), format='json')

    def test_applying_a_job_makes_one_batch_keyed_by_the_job(self):
        job = self.stage(3)
        res = self.apply(job)

        self.assertEqual(res.status_code, 200, res.content)
        [summary] = res.json()['label_batches']
        self.assertEqual((summary['items'], summary['labels_total']), (3, 3))
        batch = LabelBatch.objects.get(pk=summary['id'])
        self.assertEqual(batch.source_key, f'auto:label:job:{job["id"]}:branch:{self.branch_a.pk}')
        self.assertEqual(batch.reason, LabelBatch.AUTO)
        self.assertIsNone(batch.movement_id)
        self.assertEqual(sorted(i.unit_id for i in batch.items.all()),
                         sorted(StockUnit.objects.values_list('pk', flat=True)))

    def test_applying_the_same_job_again_changes_nothing_and_shows_the_same_batch(self):
        job = self.stage(2)
        first = self.apply(job).json()['label_batches']
        again = self.apply(job)

        self.assertEqual(again.status_code, 200, again.content)
        self.assertEqual(again.json()['label_batches'], first)
        self.assertEqual(batches().count(), 1)
        self.assertEqual(LabelBatchItem.objects.count(), 2)

    def test_a_job_that_fails_to_apply_leaves_no_batch(self):
        job = self.stage(2)
        # One of the devices enters by another road before the apply: the whole file is refused.
        self.receive(self.row('OTRA00001', IMEI_A))
        res = self.apply(job)
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(batches().count(), 0)
        self.assertEqual(StockUnit.objects.count(), 1)

    def test_a_job_over_two_branches_makes_a_batch_for_each(self):
        rows = [self.phone_row('IMPORT0001', IMEI_A),
                self.phone_row('IMPORT0002', IMEI_B, **{'Sucursal': self.branch_b.name})]
        res = self.apply(self.staged(rows))
        self.assertEqual(res.status_code, 200, res.content)
        found = {(b.branch_id, b.items.count()) for b in batches()}
        self.assertEqual(found, {(self.branch_a.pk, 1), (self.branch_b.pk, 1)})

    def test_a_preview_alone_makes_no_batch(self):
        self.stage(2)
        self.assertEqual(batches().count(), 0)
        self.assertEqual(StockUnit.objects.count(), 0)

    def test_the_service_applied_twice_labels_once(self):
        job = BulkImportJob.objects.get(pk=self.stage(2)['id'])
        outcomes = [unit_import.apply_units(job=job, actor=self.staff)[1] for _ in range(2)]
        self.assertEqual(outcomes, [True, False])
        self.assertEqual(batches().count(), 1)
        self.assertEqual(LabelBatchItem.objects.count(), 2)


# ---------------------------------------------------------------------------
# Threads: one movement, two writers
# ---------------------------------------------------------------------------

class ConcurrentBatchTest(TransactionTestCase):
    def test_two_writers_for_one_movement_make_one_batch(self):
        from django.contrib.auth import get_user_model
        from store.models import Branch, Company

        company = Company.objects.create(
            name='Etiquetas conc', legal_name='Etiquetas conc SAC', tax_id='20922222222', slug='etiquetas-conc')
        branch = company.branches.first() or Branch.objects.create(company=company, name='Centro')
        actor = get_user_model().objects.create_user(username='etiquetera_conc', password='Pass123!')
        charger = Product.objects.create(company=company, name='Cargador', slug='cargador-conc', price='10.00')
        movement = inventory.create_stock_movement(
            branch=branch, product_id=charger.pk, movement_type=StockMovement.INITIAL_STOCK, quantity=40,
            reason='Alta', actor=actor)

        barrier, results = Barrier(2), []

        def write():
            try:
                barrier.wait(timeout=5)
                with transaction.atomic():
                    batch = label_batches.create_for_movement(movement, actor=actor)
                results.append(batch.pk if batch else None)
            finally:
                connection.close()

        threads = [Thread(target=write) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)

        self.assertEqual(len(results), 2)
        self.assertEqual(len(set(results)), 1, results)
        self.assertEqual(LabelBatch.objects.count(), 1)
        self.assertEqual(LabelBatchItem.objects.get().copies, 40)


# ---------------------------------------------------------------------------
# What must NOT label
# ---------------------------------------------------------------------------

class ExcludedFlowsTest(LabelFixture):
    def test_a_transfer_in_never_labels(self):
        _m7_stock(self.branch_a, self.product, 10)
        transfer = inventory.create_stock_transfer(
            company=self.company, source_branch=self.branch_a, destination_branch=self.branch_b,
            actor=self.staff)
        inventory.set_transfer_item(transfer, product=self.product, quantity=3)
        inventory.dispatch_transfer(transfer, actor=self.staff)
        inventory.receive_transfer(transfer, actor=self.staff)

        self.assertTrue(StockMovement.objects.filter(movement_type=StockMovement.TRANSFER_IN).exists())
        self.assertEqual(batches().count(), 0)

    def test_the_approval_of_a_count_that_adds_stock_never_labels(self):
        _m7_stock(self.branch_a, self.other_product, 5)
        count = inventory.create_inventory_count(company=self.company, branch=self.branch_a, actor=self.staff)
        inventory.set_count_item(count, product=self.other_product, physical_quantity=9, actor=self.staff)
        inventory.approve_inventory_count(count, actor=self.staff)

        self.assertTrue(StockMovement.objects.filter(movement_type=StockMovement.CORRECTION_POSITIVE).exists())
        self.assertEqual(inventory.branch_quantity(self.branch_a, self.other_product), 9)
        self.assertEqual(batches().count(), 0)

    def test_a_unit_changing_state_never_labels(self):
        [unit] = self.receive(self.row('SERIE00001', IMEI_A))
        before = StockMovement.objects.count()
        units.reserve(unit, reason='Apartado', actor=self.staff)
        units.release(unit, actor=self.staff)
        self.assertGreater(StockMovement.objects.count(), before)
        self.assertEqual(batches().count(), 0)

    def test_receive_units_alone_makes_no_batch_it_is_the_caller_who_decides(self):
        # `supply_services` (bajo pedido) calls `receive_units` too; it must stay unlabelled.
        self.receive(self.row('SERIE00001', IMEI_A))
        self.assertEqual(batches().count(), 0)

    def test_the_single_writer_never_labels(self):
        inventory.create_stock_movement(
            branch=self.branch_a, product_id=self.other_product.pk, movement_type=StockMovement.INITIAL_STOCK,
            quantity=10, reason='x', actor=self.staff)
        self.assertEqual(batches().count(), 0)


class SupplyAndReconcileExclusionsTest(TestCase):
    def test_the_stock_reconciliation_never_labels(self):
        from store import stock_import_services as stock_import
        from store.tests import (
            _c1_product, _c14_stock_workbook, _c14_upload, _p2d_branch, _p2d_member, _p3_company,
        )

        company = _p3_company('lbl-recon', 'Concilia Etiquetas')
        branch = _p2d_branch(company, 'Principal')
        user, _ = _p2d_member(company, 'lbl_recon_admin', ['company.view', 'inventory.adjust', 'inventory.view'])
        product = _c1_product(company, 'Cable Lightning', '49.90')
        ProductBarcode.objects.create(company=company, product=product, code='C000001',
                                      symbology=ProductBarcode.INTERNAL)
        payload = _c14_stock_workbook([{'ID': 1288488, 'CODIGO': 'C000001', 'NOMBRE': 'Cable Lightning',
                                        'ALMACEN 1 - 11416': 25}])
        job = stock_import.preview_stock(
            company=company, actor=user, upload=_c14_upload(payload), filename='stock.xlsx',
            branch_map={4: branch.pk}, mode=BulkImportJob.MODE_RECONCILE)
        stock_import.apply_stock(job=job, actor=user)

        self.assertEqual(inventory.branch_quantity(branch, product), 25)
        self.assertTrue(StockMovement.objects.filter(reference_type='bulk_import', branch=branch).exists())
        self.assertEqual(batches().count(), 0)


class SupplyReceptionExclusionTest(TestCase):
    def test_a_bajo_pedido_reception_never_labels(self):
        from store.test_supply_reception import ReceptionShop, as_client

        shop = type('Shop', (ReceptionShop, TestCase), {})('run')
        shop.build('lbl-supply')
        order = shop.paid_order(shop.watch, 1)
        res = as_client(shop.staff).post(shop.url(order), {'units': [{'serial_number': 'RLJ9001'}]},
                                         format='json')

        self.assertEqual(res.status_code, 200, res.content)
        self.assertTrue(StockUnit.objects.filter(product=shop.watch).exists())
        self.assertTrue(StockMovement.objects.filter(product=shop.watch, movement_type='purchase_entry').exists())
        self.assertEqual(batches().count(), 0)


# ---------------------------------------------------------------------------
# The allowed list, pinned
# ---------------------------------------------------------------------------

class AllowedListTest(TestCase):
    STORE = Path(__file__).resolve().parent

    def test_the_movement_types_that_label_are_exactly_these(self):
        self.assertEqual(
            label_batches.AUTO_ENTRY_TYPES,
            frozenset({StockMovement.PURCHASE_ENTRY, StockMovement.INITIAL_STOCK}))
        for never in (StockMovement.MANUAL_ENTRY, StockMovement.CORRECTION_POSITIVE, StockMovement.CORRECTION_NEGATIVE,
                      StockMovement.RETURN_ENTRY, StockMovement.TRANSFER_IN, StockMovement.SERVICE_RETURN,
                      StockMovement.ORDER_CANCEL_RETURN, StockMovement.CUSTOMER_RETURN):
            self.assertNotIn(never, label_batches.AUTO_ENTRY_TYPES)

    def test_only_these_modules_call_the_batch_service(self):
        callers = set()
        for path in self.STORE.glob('*.py'):
            if path.name.startswith('test') or path.name in ('label_batches.py',):
                continue
            if re.search(r'label_batches\.(create_for|summar)', path.read_text()):
                callers.add(path.name)
        self.assertEqual(callers, {
            'registration_services.py', 'stock_unit_views.py', 'unit_import_services.py',
            'import_views.py', 'admin_views.py', 'inventory_views.py', 'v1_inventory_views.py',
        })

    def test_the_single_writer_and_the_excluded_services_never_mention_it(self):
        for name in ('inventory_services.py', 'supply_services.py', 'stock_import_services.py',
                     'order_cancellation.py', 'order_returns.py', 'unit_identification.py',
                     'service_services.py', 'count_services.py'):
            self.assertNotIn('label_batch', (self.STORE / name).read_text(), name)


# ---------------------------------------------------------------------------
# Schema rules
# ---------------------------------------------------------------------------

class SchemaTest(LabelFixture):
    def batch(self, key='auto:label:test:1', company=None, branch=None):
        return LabelBatch.objects.create(
            company=company or self.company, branch=branch or self.branch_a, reason=LabelBatch.AUTO,
            source_key=key)

    def test_the_source_key_is_unique_per_company_only(self):
        self.batch()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.batch()
        self.batch(company=self.other, branch=self.foreign_branch)  # another company, same key

    def test_copies_are_at_least_one(self):
        batch = self.batch()
        with self.assertRaises(IntegrityError), transaction.atomic():
            LabelBatchItem.objects.create(batch=batch, product=self.other_product, copies=0)

    def test_a_device_row_is_one_copy_and_one_unit_once_per_batch(self):
        [unit] = self.receive(self.row('SERIE00001', IMEI_A))
        batch = self.batch()
        with self.assertRaises(IntegrityError), transaction.atomic():
            LabelBatchItem.objects.create(batch=batch, unit=unit, product=self.phone, copies=2)
        LabelBatchItem.objects.create(batch=batch, unit=unit, product=self.phone, copies=1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            LabelBatchItem.objects.create(batch=batch, unit=unit, product=self.phone, copies=1)

    def test_the_movement_and_the_unit_are_protected(self):
        received = self.receive(self.row('SERIE00001', IMEI_A))
        label_batches.create_for_movement(received.movement, units=received, actor=self.staff)
        from django.db.models import ProtectedError
        with self.assertRaises(ProtectedError):
            received.movement.delete()

    def test_a_print_event_belongs_to_a_batch_and_stores_no_identifier(self):
        batch = self.batch()
        event = LabelPrintEvent.objects.create(
            company=self.company, batch=batch, actor=self.staff, channel=LabelPrintEvent.PDF, copies=3)
        self.assertEqual(event.batch_id, batch.pk)
        names = {f.name for f in LabelPrintEvent._meta.get_fields()}
        self.assertFalse(names & {'imei', 'serial_number', 'serial', 'label_code'})
        names = {f.name for f in LabelBatchItem._meta.get_fields()}
        self.assertFalse(names & {'imei', 'serial_number', 'serial'})

    def test_a_deleted_actor_does_not_delete_the_history(self):
        from django.contrib.auth import get_user_model
        actor = get_user_model().objects.create_user(username='borrable', password='Pass123!')
        batch = LabelBatch.objects.create(company=self.company, branch=self.branch_a, reason=LabelBatch.AUTO,
                                          source_key='auto:label:test:actor', created_by=actor)
        actor.delete()
        batch.refresh_from_db()
        self.assertIsNone(batch.created_by)


# ---------------------------------------------------------------------------
# A failure to label is not «this movement does not label» (P2-2 of the review of #194)
# ---------------------------------------------------------------------------

def break_items():
    return mock.patch.object(LabelBatchItem.objects, 'bulk_create', side_effect=RuntimeError('boom'))


def bad_sql(*_args, **_kwargs):
    """A statement the database refuses: it aborts the surrounding transaction unless a savepoint holds it."""
    with connection.cursor() as cursor:
        cursor.execute('SELECT * FROM table_that_does_not_exist')


class FailureIsVisibleTest(LabelFixture):
    def test_the_service_returns_the_failed_marker_not_none(self):
        received = self.receive(self.row('SERIE00001', IMEI_A))
        with break_items(), self.assertLogs('store.label_batches', level='ERROR'):
            result = label_batches.create_for_movement(received.movement, units=received, actor=self.staff)
        self.assertIs(result, label_batches.FAILED)
        self.assertEqual(label_batches.summary_or_none(result), {
            'id': None, 'status': 'error', 'reason': 'auto', 'branch_id': None, 'items': 0, 'labels_total': 0,
            'needs_review': 0})
        self.assertEqual(label_batches.answer(result)['label_error'], True)
        self.assertEqual(label_batches.answer(None), {'label_batch': None, 'label_error': False})

    def test_registrar_equipos_says_the_labels_failed_and_keeps_the_devices(self):
        with break_items(), self.assertLogs('store.label_batches', level='ERROR'):
            res = self.api.post(UNITS_URL, {
                'branch': self.branch_a.pk, 'product_id': self.phone.pk, 'reason': 'Compra',
                'units': [{'serial_number': 'SERIE00001', 'imei': IMEI_A}]}, format='json')

        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.json()['label_batch']['status'], 'error')
        self.assertTrue(res.json()['label_error'])
        self.assertEqual(StockUnit.objects.count(), 1)
        self.assertEqual(self.quantity(), 1)
        self.assertEqual(batches().count(), 0)

    def test_a_purchase_entry_says_the_labels_failed_and_keeps_the_stock(self):
        with break_items(), self.assertLogs('store.label_batches', level='ERROR'):
            res = self.api.post(MOVEMENTS_URL, {
                'product_id': self.other_product.pk, 'movement_type': 'purchase_entry', 'quantity': 4,
                'reason': 'Compra', 'branch': self.branch_a.pk}, format='json')
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual((res.json()['label_batch']['status'], res.json()['label_error']), ('error', True))
        self.assertEqual(inventory.branch_quantity(self.branch_a, self.other_product), 4)

    def test_an_entry_that_does_not_label_says_so_differently(self):
        res = self.api.post(MOVEMENTS_URL, {
            'product_id': self.other_product.pk, 'movement_type': 'manual_entry', 'quantity': 4,
            'reason': 'Encontrado', 'branch': self.branch_a.pk}, format='json')
        self.assertEqual((res.json()['label_batch'], res.json()['label_error']), (None, False))


class RegistrationFailureTest(RegistrationFixture):
    def test_the_answer_and_its_replay_say_the_labels_failed(self):
        body = self.accessory()
        with break_items(), self.assertLogs('store.label_batches', level='ERROR'):
            first = self.post(self.clerk, body, key='lbl-fail', expect=201).json()

        self.assertTrue(first['label_error'])
        self.assertEqual({s['status'] for s in first['label_batches']}, {'error'})
        self.assertEqual(Product.objects.filter(company=self.company, name='Funda MagSafe').count(), 1)
        self.assertEqual(sum(m.quantity for m in StockMovement.objects.filter(company=self.company)), 8)
        again = self.post(self.clerk, body, key='lbl-fail', expect=200).json()
        self.assertTrue(again['label_error'])
        self.assertEqual(LabelBatch.objects.count(), 0)

    def test_a_good_registration_has_no_error(self):
        data = self.post(self.clerk, self.accessory(), expect=201).json()
        self.assertFalse(data['label_error'])


class ImportFailureTest(ImportBase):
    def test_a_failure_is_shown_on_the_apply_and_on_its_repeat(self):
        rows = [self.phone_row('IMPORT0001', IMEI_A)]
        job = self.staged(rows)
        with break_items(), self.assertLogs('store.label_batches', level='ERROR'):
            res = self.api.post(apply_url(job['id']), format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertTrue(res.json()['label_error'])
        self.assertEqual(StockUnit.objects.count(), 1)
        again = self.api.post(apply_url(job['id']), format='json')
        self.assertTrue(again.json()['label_error'])
        self.assertEqual(batches().count(), 0)

    def test_a_clean_apply_has_no_error(self):
        job = self.staged([self.phone_row('IMPORT0001', IMEI_A)])
        self.assertFalse(self.api.post(apply_url(job['id']), format='json').json()['label_error'])


class PreparationInsideTheSavepointTest(LabelFixture):
    """A database error while PREPARING must not leave the caller's transaction aborted."""

    def test_a_database_error_reading_the_units(self):
        received = self.receive(self.row('SERIE00001', IMEI_A))
        with mock.patch.object(label_batches, '_fresh_units', side_effect=bad_sql), \
                self.assertLogs('store.label_batches', level='ERROR'):
            result = label_batches.create_for_movement(received.movement, actor=self.staff)
        self.assertIs(result, label_batches.FAILED)
        self.assertEqual(StockUnit.objects.count(), 1)  # the caller's transaction is still usable

    def test_a_database_error_preparing_the_quantity_items(self):
        movement = self.manual(StockMovement.PURCHASE_ENTRY, 5)
        with mock.patch.object(label_batches, '_quantity_items', side_effect=bad_sql), \
                self.assertLogs('store.label_batches', level='ERROR'):
            result = label_batches.create_for_movement(movement, actor=self.staff)
        self.assertIs(result, label_batches.FAILED)
        self.assertEqual(StockMovement.objects.filter(pk=movement.pk).count(), 1)

    def test_a_database_error_grouping_the_units_of_a_job(self):
        received = self.receive(self.row('SERIE00001', IMEI_A))
        job = BulkImportJob.objects.create(company=self.company, import_type=BulkImportJob.UNITS)
        with mock.patch.object(label_batches, '_fresh_units', side_effect=bad_sql), \
                self.assertLogs('store.label_batches', level='ERROR'):
            result = label_batches.create_for_import_job(job, list(received), actor=self.staff)
        self.assertEqual(result, [label_batches.FAILED])
        self.assertEqual(StockUnit.objects.count(), 1)
