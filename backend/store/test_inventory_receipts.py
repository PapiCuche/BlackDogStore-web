"""
INV-OPS-01 — «asistente de recepción unificado»: ONE confirmed operation receives merchandise.

    POST /api/admin/inventory/receipts/preview/    (read-only)
    POST /api/admin/inventory/receipts/confirm/    header `Idempotency-Key`

A receipt is a branch, an optional supplier, a document reference, notes and several lines. A line is
either a QUANTITY of an existing non-serialized product or INDIVIDUAL UNITS of an existing serialized
one. THE ENDPOINT WRITES NOTHING OF ITS OWN: it composes the services every other screen calls —
`stock_unit_services.receive_units` and `inventory_services.apply_manual_stock_movement`
(purchase entries), then LABEL-A (`label_batches.create_for_movement`) — in ONE transaction.

What these tests hold:

  * the acceptance cases of the owner (one used device, three new devices, +50 accessories on 100,
    a mixed receipt) end with exactly the stock, Kardex lines and label batches they should;
  * the preview writes NOTHING (no unit, movement, code, batch, audit line, staged job);
  * a repeated key with the same body replays the stored answer and adds nothing; another body is 409;
  * one bad line refuses the whole receipt, and a failure of the labels never undoes a reception;
  * costs need `costs.view` and are never echoed; supplier and document travel in the Kardex reason
    and the audit trail, never the serial, the IMEI or an amount;
  * authority, tenancy and branch scope are the server's; a malformed body is a 400, never a 500.
"""
import json
import re
from pathlib import Path
from threading import Barrier, BrokenBarrierError, Thread
from unittest import mock

from django.core.cache import cache
from django.db import OperationalError, connection, transaction
from django.test import TestCase, TransactionTestCase
from rest_framework.test import APIClient

from store import inventory_services, label_batches
from store import stock_unit_services as units_service
from store.models import (
    AdminAuditLog, BranchStock, InternalSequence, LabelBatch, LabelBatchItem, Product, ProductBarcode,
    StockMovement, StockUnit, Supplier,
)
from store.test_device_commerce import assert_no_amount
from store.test_registration import IMEIS, _client
from store.models import MembershipBranchAccess
from store.tests import _m7_stock, _p2d_branch, _p2d_member, _p3_company, _prod

PREVIEW = '/api/admin/inventory/receipts/preview/'
CONFIRM = '/api/admin/inventory/receipts/confirm/'
ACTION = 'inventory_receipt_confirmed'


def unit(serial, imei=None, **extra):
    return {'serial': serial, 'imei': imei, 'condition': 'new', **extra}


class ReceiptBase(TestCase):
    def setUp(self):
        cache.clear()
        self.company = _p3_company('ops-tienda', 'Tienda Recepción')
        self.other = _p3_company('ops-otra', 'Otra Recepción')
        self.centro = self.company.default_inventory_branch
        self.norte = _p2d_branch(self.company, 'Norte Recepción')
        self.foreign_branch = self.other.default_inventory_branch

        self.phone = self.serialized('iPhone Recepción', 'iphone-recepcion', imei=True)
        self.laptop = self.serialized('MacBook Recepción', 'macbook-recepcion', imei=False)
        self.cable = _prod(self.company, 'Cable USB-C', 'cable-recepcion', price='25.00', inventory=0)
        self.case = _prod(self.company, 'Funda', 'funda-recepcion', price='30.00', inventory=0)
        _m7_stock(self.centro, self.cable, 100)
        self.foreign_cable = _prod(self.other, 'Cable ajeno', 'cable-ajeno', inventory=0)
        self.foreign_phone = self.serialized('iPhone ajeno', 'iphone-ajeno', imei=True, company=self.other)

        self.supplier = Supplier.objects.create(company=self.company, name='Importadora Lima')
        self.foreign_supplier = Supplier.objects.create(company=self.other, name='Proveedor Ajeno')

        caps = ['inventory.view', 'inventory.adjust']
        self.clerk, _ = _p2d_member(self.company, 'ops_clerk', caps)
        self.costed, _ = _p2d_member(self.company, 'ops_costed', caps + ['costs.view'])
        self.viewer, _ = _p2d_member(self.company, 'ops_viewer', ['inventory.view', 'products.view'])
        self.centro_only, _ = _p2d_member(self.company, 'ops_centro', caps + ['costs.view'],
                                          branches=[self.centro])
        self.outsider, _ = _p2d_member(self.other, 'ops_outsider', caps + ['costs.view'])
        self._key = 0

    # -- fixtures ------------------------------------------------------------

    def serialized(self, name, slug, *, imei, company=None):
        product = _prod(company or self.company, name, slug, price='4500.00', inventory=0)
        Product.objects.filter(pk=product.pk).update(is_serialized=True, requires_imei=imei)
        product.refresh_from_db()
        return product

    # -- builders ------------------------------------------------------------

    def key(self):
        self._key += 1
        return f'ops-key-{self._key}'

    def qty(self, product, quantity, cost=None):
        return {'kind': 'quantity', 'product': product.pk, 'quantity': quantity, 'unit_cost': cost}

    def dev(self, product, *rows):
        return {'kind': 'units', 'product': product.pk, 'units': list(rows)}

    def body(self, *lines, **extra):
        return {'branch': self.centro.pk, 'supplier': None, 'document_ref': '', 'notes': '',
                'lines': list(lines), **extra}

    def preview(self, user, body, expect=None):
        res = _client(user).post(PREVIEW, body, format='json')
        if expect is not None:
            self.assertEqual(res.status_code, expect, res.content)
        return res

    def confirm(self, user, body, *, key=None, expect=None):
        headers = {} if key is False else {'HTTP_IDEMPOTENCY_KEY': key or self.key()}
        res = _client(user).post(CONFIRM, body, format='json', **headers)
        if expect is not None:
            self.assertEqual(res.status_code, expect, res.content)
        return res

    # -- observation ---------------------------------------------------------

    def snapshot(self):
        """Everything a reception could write, for «nothing changed»."""
        return {
            'units': StockUnit.objects.count(),
            'movements': StockMovement.objects.count(),
            'stock': sorted(BranchStock.objects.values_list('branch_id', 'product_id', 'quantity')),
            'batches': LabelBatch.objects.count(),
            'items': LabelBatchItem.objects.count(),
            'audit': AdminAuditLog.objects.count(),
            'sequences': sorted(
                (row['pk'], row['next_value']) for row in InternalSequence.objects.values('pk', 'next_value')),
            'labels': sorted(StockUnit.objects.values_list('pk', 'label_code')),
            'products': Product.objects.count(),
        }

    def shelf(self, product, branch=None):
        row = BranchStock.objects.filter(branch=branch or self.centro, product=product).first()
        return row.quantity if row else 0


# ---------------------------------------------------------------------------
# The owner's acceptance cases
# ---------------------------------------------------------------------------

class AcceptanceTest(ReceiptBase):
    def test_one_used_device_is_one_unit_and_one_label(self):
        res = self.confirm(self.clerk, self.body(self.dev(
            self.phone, unit('USADO00001', IMEIS[0], condition='used', battery_health=87,
                             notes='Pantalla impecable'))), expect=201)
        data = res.json()

        [created] = StockUnit.objects.filter(company=self.company)
        self.assertEqual((created.condition, created.battery_health, created.status),
                         ('used', 87, StockUnit.Status.AVAILABLE))
        self.assertEqual((created.serial_number, created.imei, created.branch), ('USADO00001', IMEIS[0], self.centro))
        self.assertTrue(created.label_code)
        self.assertEqual(self.shelf(self.phone), 1)
        [movement] = StockMovement.objects.filter(product=self.phone)
        self.assertEqual((movement.movement_type, movement.quantity), (StockMovement.PURCHASE_ENTRY, 1))

        self.assertEqual(data['totals'], {'lines': 1, 'quantity_units': 0, 'devices': 1, 'labels': 1})
        [line] = data['lines']
        self.assertEqual((line['index'], line['kind'], line['received'], line['units'], line['movement_id']),
                         (0, 'units', 1, 1, movement.pk))
        self.assertEqual(line['label_batch']['labels_total'], 1)
        self.assertFalse(line['label_error'])
        self.assertFalse(data['label_error'])
        self.assertEqual(len(data['label_batches']), 1)
        self.assertEqual(LabelBatch.objects.get().movement_id, movement.pk)

    def test_three_new_devices_with_different_identities_are_three_units_and_three_labels(self):
        res = self.confirm(self.clerk, self.body(self.dev(
            self.phone, unit('NUEVO00001', IMEIS[1]), unit('NUEVO00002', IMEIS[2]),
            unit('NUEVO00003', IMEIS[3]))), expect=201)

        created = StockUnit.objects.filter(company=self.company)
        self.assertEqual(created.count(), 3)
        self.assertEqual(len({u.label_code for u in created}), 3)
        self.assertEqual(self.shelf(self.phone), 3)
        self.assertEqual(StockMovement.objects.filter(product=self.phone).count(), 1)
        self.assertEqual(res.json()['label_batches'][0]['labels_total'], 3)
        self.assertEqual(res.json()['totals']['labels'], 3)

    def test_fifty_accessories_on_a_hundred_make_a_hundred_and_fifty_and_a_fifty_copy_batch(self):
        before = Product.objects.filter(company=self.company).count()
        res = self.confirm(self.clerk, self.body(self.qty(self.cable, 50)), expect=201)

        self.assertEqual(self.shelf(self.cable), 150)
        self.assertEqual(Product.objects.filter(company=self.company).count(), before)
        movement = StockMovement.objects.get(product=self.cable)
        self.assertEqual((movement.movement_type, movement.quantity, movement.stock_before, movement.stock_after),
                         (StockMovement.PURCHASE_ENTRY, 50, 100, 150))
        batch = LabelBatch.objects.get()
        self.assertEqual(batch.movement_id, movement.pk)
        self.assertEqual(label_batches.summary(batch)['labels_total'], 50)
        [line] = res.json()['lines']
        self.assertEqual((line['received'], line['units'], line['label_batch']['labels_total']), (50, 0, 50))

    def test_a_mixed_receipt_is_one_confirmation_with_a_movement_and_a_batch_per_line(self):
        res = self.confirm(self.clerk, self.body(
            self.qty(self.cable, 50), self.qty(self.case, 7),
            self.dev(self.phone, unit('MIXTO00001', IMEIS[4]), unit('MIXTO00002', IMEIS[5])),
            self.dev(self.laptop, unit('LAPTOP00001', None)),
            supplier=self.supplier.pk, document_ref='F001-123', notes='Pedido semanal'), expect=201)
        data = res.json()

        self.assertEqual(data['totals'], {'lines': 4, 'quantity_units': 57, 'devices': 3, 'labels': 60})
        self.assertEqual([l['index'] for l in data['lines']], [0, 1, 2, 3])
        self.assertEqual(StockMovement.objects.count(), 4)
        self.assertEqual(LabelBatch.objects.count(), 4)
        self.assertEqual(len(data['label_batches']), 4)
        self.assertEqual((self.shelf(self.cable), self.shelf(self.case), self.shelf(self.phone),
                          self.shelf(self.laptop)), (150, 7, 2, 1))
        self.assertEqual(data['supplier'], {'id': self.supplier.pk, 'name': 'Importadora Lima'})
        self.assertEqual(data['branch'], {'id': self.centro.pk, 'name': self.centro.name})
        self.assertEqual(data['document_ref'], 'F001-123')
        self.assertTrue(data['receipt_id'])

    def test_kardex_and_branch_stock_reconcile_after_a_mixed_receipt(self):
        self.confirm(self.clerk, self.body(
            self.qty(self.cable, 25), self.dev(self.phone, unit('RECON00001', IMEIS[6]), unit('RECON00002', IMEIS[7]))),
            expect=201)
        for product in (self.cable, self.phone):
            moved = sum(m.quantity for m in StockMovement.objects.filter(product=product, branch=self.centro))
            opening = 100 if product == self.cable else 0
            self.assertEqual(self.shelf(product), opening + moved, product.name)
            last = StockMovement.objects.filter(product=product).order_by('-pk').first()
            self.assertEqual(last.stock_after, self.shelf(product))
        self.assertEqual(self.shelf(self.phone), StockUnit.objects.filter(
            product=self.phone, branch=self.centro, status=StockUnit.Status.AVAILABLE).count())

    def test_a_cost_is_stored_where_the_services_keep_it(self):
        self.confirm(self.costed, self.body(
            self.qty(self.cable, 50, '12.50'),
            self.dev(self.phone, unit('COSTO00001', IMEIS[8], unit_cost='3800.00'))), expect=201)
        self.assertEqual(str(StockMovement.objects.get(product=self.cable).unit_cost), '12.5000')
        self.assertEqual(str(StockUnit.objects.get(serial_number='COSTO00001').cost), '3800.00')

    def test_the_row_contract_of_registrar_equipos_is_accepted_too(self):
        """`serial_number` and `cost` are the names `receive_units` uses; `serial` and `unit_cost` alias them."""
        self.confirm(self.costed, self.body(self.dev(self.phone, {
            'serial_number': 'ALIAS000001', 'imei': IMEIS[9], 'condition': 'new', 'cost': '3000.00'})),
            expect=201)
        created = StockUnit.objects.get(serial_number='ALIAS000001')
        self.assertEqual(str(created.cost), '3000.00')

    def test_a_user_without_products_manage_can_receive_existing_products(self):
        self.confirm(self.clerk, self.body(self.qty(self.case, 3)), expect=201)


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------

class PreviewTest(ReceiptBase):
    def full_body(self):
        return self.body(
            self.qty(self.cable, 50), self.dev(self.phone, unit('PREV00001', IMEIS[10]), unit('PREV00002', IMEIS[11])),
            supplier=self.supplier.pk, document_ref='B001-9')

    def test_it_writes_nothing_at_all(self):
        before = self.snapshot()
        res = self.preview(self.clerk, self.full_body(), expect=200)
        self.assertTrue(res.json()['ok'])
        self.assertEqual(self.snapshot(), before)
        # Not a staged job of any kind either.
        from store.models import BulkImportJob
        self.assertEqual(BulkImportJob.objects.count(), 0)

    def test_it_answers_what_would_happen(self):
        data = self.preview(self.clerk, self.full_body(), expect=200).json()
        self.assertEqual(data['branch'], {'id': self.centro.pk, 'name': self.centro.name})
        self.assertEqual(data['supplier'], {'id': self.supplier.pk, 'name': 'Importadora Lima'})
        self.assertEqual(data['document_ref'], 'B001-9')
        self.assertEqual(data['totals'], {'lines': 2, 'quantity_units': 50, 'devices': 2, 'labels': 52})
        first, second = data['lines']
        self.assertEqual((first['index'], first['kind'], first['status'], first['will_receive'], first['labels']),
                         (0, 'quantity', 'ok', 50, 50))
        self.assertEqual((second['kind'], second['status'], second['will_receive'], second['labels']),
                         ('units', 'ok', 2, 2))
        self.assertEqual(first['product']['id'], self.cable.pk)
        self.assertFalse(first['product']['is_serialized'])
        self.assertTrue(second['product']['is_serialized'])
        for line in data['lines']:
            self.assertEqual((line['errors'], line['warnings'], line['duplicates']), ([], [], []))

    def test_it_never_echoes_what_was_sent(self):
        text = self.preview(self.clerk, self.full_body(), expect=200).content.decode()
        for secret in ('PREV00001', IMEIS[10], IMEIS[11]):
            self.assertNotIn(secret, text)

    def test_a_cost_is_refused_without_costs_view_and_never_echoed_with_it(self):
        body = self.body(self.qty(self.cable, 5, '12.50'))
        refused = self.preview(self.clerk, body, expect=400)
        self.assertEqual(refused.json()['code'], 'costs_forbidden')
        ok = self.preview(self.costed, body, expect=200)
        assert_no_amount(self, ok.json(), '12.50', '12.5')
        self.assertNotIn('cost', ok.content.decode().lower().replace('costs_', ''))

    def test_a_kind_that_does_not_fit_the_product_says_which_one_does(self):
        data = self.preview(self.clerk, self.body(
            self.qty(self.phone, 2), self.dev(self.cable, unit('X0000001', None))), expect=200).json()
        self.assertFalse(data['ok'])
        wrong_quantity, wrong_units = data['lines']
        self.assertEqual((wrong_quantity['status'], wrong_units['status']), ('error', 'error'))
        self.assertIn('equipos', ' '.join(wrong_quantity['errors']))
        self.assertIn('cantidad', ' '.join(wrong_units['errors']))
        self.assertEqual((wrong_quantity['will_receive'], wrong_units['will_receive']), (0, 0))
        self.assertEqual(data['totals']['labels'], 0)

    def test_an_inactive_product_is_an_error_line(self):
        Product.objects.filter(pk=self.case.pk).update(is_active=False)
        data = self.preview(self.clerk, self.body(self.qty(self.case, 2)), expect=200).json()
        self.assertEqual(data['lines'][0]['status'], 'error')
        self.assertFalse(data['ok'])

    def test_duplicates_against_stock_and_inside_the_request_are_flagged_per_line(self):
        self.confirm(self.clerk, self.body(self.dev(self.phone, unit('YAESTA0001', IMEIS[12]))), expect=201)
        data = self.preview(self.clerk, self.body(
            self.dev(self.phone, unit('YAESTA0001', IMEIS[13]), unit('NUEVA00001', IMEIS[12]),
                     unit('NUEVA00002', IMEIS[14]), unit('NUEVA00003', IMEIS[14])),
        ), expect=200).json()
        [line] = data['lines']
        self.assertEqual(line['status'], 'error')
        self.assertGreaterEqual(len(line['duplicates']), 3)
        issues = {(i['unit'], i['kind']) for i in line['unit_issues']}
        self.assertEqual(issues, {(0, 'duplicate'), (1, 'duplicate'), (3, 'duplicate')})
        self.assertEqual(line['will_receive'], 0)

    def test_the_same_serial_under_the_same_product_in_two_lines_is_a_duplicate(self):
        data = self.preview(self.clerk, self.body(
            self.dev(self.laptop, unit('IGUAL000001')), self.dev(self.laptop, unit('IGUAL000001')),
        ), expect=200).json()
        self.assertEqual([l['status'] for l in data['lines']], ['ok', 'error'])

    def test_another_companys_stock_is_neither_a_duplicate_nor_visible(self):
        from store.stock_unit_services import receive_units
        receive_units(branch=self.foreign_branch, product=self.foreign_phone, actor=self.outsider,
                      units=[{'serial_number': 'AJENO00001', 'imei': IMEIS[15]}], reason='Compra')
        data = self.preview(self.clerk, self.body(
            self.dev(self.phone, unit('AJENO00001', IMEIS[15]))), expect=200).json()
        self.assertTrue(data['ok'])
        self.assertEqual(data['lines'][0]['duplicates'], [])

    def test_the_same_product_twice_is_allowed_with_a_warning(self):
        data = self.preview(self.clerk, self.body(self.qty(self.cable, 2), self.qty(self.cable, 3)),
                            expect=200).json()
        self.assertTrue(data['ok'])
        self.assertTrue(all(l['warnings'] for l in data['lines']))

    def test_a_cellular_device_without_imei_is_an_error_naming_the_unit(self):
        data = self.preview(self.clerk, self.body(self.dev(self.phone, unit('SINIMEI001', None))),
                            expect=200).json()
        [line] = data['lines']
        self.assertEqual(line['status'], 'error')
        self.assertIn('IMEI', ' '.join(line['errors']))
        self.assertEqual(line['unit_issues'][0]['unit'], 0)
        self.assertEqual(line['unit_issues'][0]['field'], 'imei')

    def test_the_price_of_a_product_is_shown_and_no_cost_ever(self):
        data = self.preview(self.costed, self.body(self.qty(self.cable, 1)), expect=200).json()
        self.assertEqual(data['lines'][0]['product']['price'], '25.00')
        self.assertNotIn('reference_cost', json.dumps(data))


# ---------------------------------------------------------------------------
# All or nothing
# ---------------------------------------------------------------------------

class AllOrNothingTest(ReceiptBase):
    def test_one_invalid_line_receives_nothing_and_says_which(self):
        before = self.snapshot()
        res = self.confirm(self.clerk, self.body(
            self.qty(self.cable, 50), self.dev(self.phone, unit('OK000001', IMEIS[16])),
            self.qty(self.phone, 3)), expect=400)
        data = res.json()
        self.assertEqual(data['code'], 'invalid_lines')
        self.assertFalse(data['ok'])
        self.assertEqual([l['status'] for l in data['lines']], ['ok', 'ok', 'error'])
        self.assertEqual(self.snapshot(), before)

    def test_a_duplicate_imei_against_stock_receives_nothing(self):
        self.confirm(self.clerk, self.body(self.dev(self.phone, unit('PRIMERO001', IMEIS[17]))), expect=201)
        before = self.snapshot()
        self.confirm(self.clerk, self.body(
            self.qty(self.cable, 5), self.dev(self.phone, unit('SEGUNDO001', IMEIS[17]))), expect=400)
        self.assertEqual(self.snapshot(), before)

    def test_a_duplicate_imei_inside_the_request_across_two_lines_receives_nothing(self):
        before = self.snapshot()
        self.confirm(self.clerk, self.body(
            self.dev(self.phone, unit('REPE00001', IMEIS[18])),
            self.dev(self.phone, unit('REPE00002', IMEIS[18]))), expect=400)
        self.assertEqual(self.snapshot(), before)

    def test_a_service_that_fails_midway_undoes_the_lines_already_written(self):
        # Created last, so its product id sorts AFTER the cable: the quantity is written first.
        late = self.serialized('Equipo tardío', 'equipo-tardio', imei=False)
        before = self.snapshot()
        boom = units_service.StockUnitError('Uno de estos equipos acaba de ser registrado por otra persona.')
        with mock.patch.object(units_service, 'receive_units', side_effect=boom) as receive:
            res = self.confirm(self.clerk, self.body(
                self.qty(self.cable, 50), self.dev(late, unit('MITAD00001', None))))
        self.assertEqual(receive.call_count, 1)
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(res.json()['line'], 1)

    def test_a_refused_attempt_leaves_the_key_free(self):
        key = self.key()
        self.confirm(self.clerk, self.body(self.qty(self.phone, 1)), key=key, expect=400)
        self.confirm(self.clerk, self.body(self.qty(self.cable, 1)), key=key, expect=201)

    def test_lines_are_written_in_product_order_not_request_order(self):
        """Two receipts that list the same shelves in opposite order must not deadlock each other."""
        self.confirm(self.clerk, self.body(self.qty(self.case, 1), self.qty(self.cable, 1)), expect=201)
        pks = list(StockMovement.objects.order_by('pk').values_list('product_id', flat=True))
        self.assertEqual(pks, sorted(pks))


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------

class IdempotencyTest(ReceiptBase):
    def body_for_replay(self):
        return self.body(self.qty(self.cable, 10),
                         self.dev(self.phone, unit('REPLAY00001', IMEIS[20])), document_ref='F001-1')

    def test_the_key_is_required_and_at_most_sixty_four_characters(self):
        body = self.body(self.qty(self.cable, 1))
        self.assertEqual(self.confirm(self.clerk, body, key=False).status_code, 400)
        self.assertEqual(self.confirm(self.clerk, body, key='   ').status_code, 400)
        self.assertEqual(self.confirm(self.clerk, body, key='k' * 65).status_code, 400)
        self.assertEqual(self.confirm(self.clerk, body, key='k' * 64).status_code, 201)

    def test_the_same_key_and_body_replays_the_stored_answer_and_adds_nothing(self):
        key, body = self.key(), self.body_for_replay()
        first = self.confirm(self.clerk, body, key=key, expect=201)
        before = self.snapshot()
        again = self.confirm(self.clerk, body, key=key, expect=200)

        self.assertEqual(self.snapshot(), before)
        self.assertTrue(again.json()['replayed'])
        self.assertNotIn('replayed', first.json())
        self.assertEqual({k: v for k, v in again.json().items() if k != 'replayed'}, first.json())
        self.assertEqual(self.shelf(self.cable), 110)

    def test_a_replay_with_the_body_written_differently_but_equal_is_still_a_replay(self):
        key = self.key()
        self.confirm(self.clerk, self.body(self.qty(self.cable, 4), notes='  hola   mundo '), key=key, expect=201)
        self.confirm(self.clerk, self.body(self.qty(self.cable, 4), notes='hola mundo'), key=key, expect=200)

    def test_the_same_key_with_another_body_is_a_conflict(self):
        key = self.key()
        self.confirm(self.clerk, self.body(self.qty(self.cable, 10)), key=key, expect=201)
        before = self.snapshot()
        res = self.confirm(self.clerk, self.body(self.qty(self.cable, 11)), key=key, expect=409)
        self.assertEqual(res.json()['code'], 'idempotency_conflict')
        self.assertEqual(self.snapshot(), before)

    def test_the_branch_is_part_of_the_body(self):
        key = self.key()
        self.confirm(self.clerk, self.body(self.qty(self.cable, 10)), key=key, expect=201)
        other_branch = self.body(self.qty(self.cable, 10), branch=self.norte.pk)
        self.confirm(self.clerk, other_branch, key=key, expect=409)

    def test_another_author_with_the_same_key_is_another_receipt(self):
        key = self.key()
        self.confirm(self.clerk, self.body(self.qty(self.cable, 1)), key=key, expect=201)
        self.confirm(self.costed, self.body(self.qty(self.cable, 1)), key=key, expect=201)
        self.assertEqual(self.shelf(self.cable), 102)

    def test_the_trail_is_one_audit_line_with_the_key_and_no_identifier(self):
        key = self.key()
        self.confirm(self.clerk, self.body_for_replay(), key=key, expect=201)
        [row] = AdminAuditLog.objects.filter(action=ACTION)
        self.assertEqual(row.metadata['idempotency_key'], key)
        self.assertTrue(row.metadata['fingerprint'])


# ---------------------------------------------------------------------------
# The labels never undo a reception
# ---------------------------------------------------------------------------

class LabelFailureTest(ReceiptBase):
    def test_a_labelling_failure_keeps_the_stock_and_says_so(self):
        body = self.body(self.qty(self.cable, 50), self.dev(self.phone, unit('ETIQ00001', IMEIS[21])))
        with mock.patch.object(label_batches, '_quantity_items', side_effect=RuntimeError('boom')), \
                mock.patch.object(label_batches, '_unit_items', side_effect=RuntimeError('boom')):
            res = self.confirm(self.clerk, body, key='labels-fail', expect=201)
        data = res.json()

        self.assertEqual((self.shelf(self.cable), self.shelf(self.phone)), (150, 1))
        self.assertEqual(StockMovement.objects.count(), 2)
        self.assertTrue(data['label_error'])
        for line in data['lines']:
            self.assertTrue(line['label_error'])
            self.assertEqual(line['label_batch']['status'], 'error')
        self.assertEqual(LabelBatch.objects.count(), 0)

        replay = self.confirm(self.clerk, body, key='labels-fail', expect=200).json()
        self.assertTrue(replay['label_error'])
        self.assertEqual(StockMovement.objects.count(), 2)

    def test_one_line_failing_to_label_does_not_mark_the_others(self):
        body = self.body(self.qty(self.cable, 50), self.dev(self.phone, unit('ETIQ00002', IMEIS[22])))
        with mock.patch.object(label_batches, '_quantity_items', side_effect=RuntimeError('boom')):
            data = self.confirm(self.clerk, body, expect=201).json()
        self.assertEqual([l['label_error'] for l in data['lines']], [True, False])
        self.assertTrue(data['label_error'])
        self.assertEqual(LabelBatch.objects.count(), 1)


# ---------------------------------------------------------------------------
# Costs
# ---------------------------------------------------------------------------

class CostTest(ReceiptBase):
    def test_a_cost_without_costs_view_is_refused_everywhere_and_nothing_is_written(self):
        for line in (self.qty(self.cable, 5, '12.50'),
                     self.dev(self.laptop, unit('SINPERMISO1', None, unit_cost='100.00')),
                     self.dev(self.laptop, {'serial_number': 'SINPERMISO2', 'cost': '100.00'})):
            before = self.snapshot()
            res = self.confirm(self.clerk, self.body(line))
            self.assertEqual(res.status_code, 400, res.content)
            self.assertEqual(res.json()['code'], 'costs_forbidden')
            self.assertEqual(self.snapshot(), before)

    def test_an_empty_cost_is_not_a_cost(self):
        self.confirm(self.clerk, self.body(self.qty(self.cable, 1, ''), self.qty(self.case, 1, None)), expect=201)

    def test_no_response_carries_an_amount(self):
        body = self.body(self.qty(self.cable, 50, '12.50'),
                         self.dev(self.laptop, unit('VALOR000001', None, unit_cost='4321.00')))
        key = self.key()
        first = self.confirm(self.costed, body, key=key, expect=201).json()
        replay = self.confirm(self.costed, body, key=key, expect=200).json()
        for payload in (first, replay):
            assert_no_amount(self, payload, '12.50', '4321.00', '4321')
            self.assertNotIn('cost', json.dumps(payload))

    def test_the_audit_trail_and_reason_carry_no_amount(self):
        self.confirm(self.costed, self.body(self.qty(self.cable, 50, '12.50')), expect=201)
        trail = json.dumps(AdminAuditLog.objects.get(action=ACTION).metadata)
        self.assertNotIn('12.5', trail)
        self.assertNotIn('12.5', StockMovement.objects.get(product=self.cable).reason)


# ---------------------------------------------------------------------------
# Authority, tenancy, scope
# ---------------------------------------------------------------------------

class AuthorityTest(ReceiptBase):
    def test_anonymous_is_refused(self):
        for url in (PREVIEW, CONFIRM):
            res = APIClient().post(url, self.body(self.qty(self.cable, 1)), format='json')
            self.assertIn(res.status_code, (401, 403), url)

    def test_inventory_adjust_is_required_for_both(self):
        before = self.snapshot()
        body = self.body(self.qty(self.cable, 1))
        self.assertEqual(self.preview(self.viewer, body).status_code, 403)
        self.assertEqual(self.confirm(self.viewer, body).status_code, 403)
        self.assertEqual(self.snapshot(), before)

    def test_another_companys_branch_product_and_supplier_answer_not_found(self):
        before = self.snapshot()
        cases = {
            'branch': self.body(self.qty(self.cable, 1), branch=self.foreign_branch.pk),
            'product': self.body(self.qty(self.foreign_cable, 1)),
            'device': self.body(self.dev(self.foreign_phone, unit('AJENO0009', IMEIS[23]))),
            'supplier': self.body(self.qty(self.cable, 1), supplier=self.foreign_supplier.pk),
            'missing product': self.body({**self.qty(self.cable, 1), 'product': 999999}),
            'missing supplier': self.body(self.qty(self.cable, 1), supplier=999999),
            'missing branch': self.body(self.qty(self.cable, 1), branch=999999),
        }
        for name, body in cases.items():
            for call in (self.preview, self.confirm):
                res = call(self.clerk, body)
                self.assertEqual(res.status_code, 404, f'{name} {res.content}')
        self.assertEqual(self.snapshot(), before)

    def test_a_user_of_another_company_cannot_receive_into_this_one(self):
        res = self.confirm(self.outsider, self.body(self.qty(self.cable, 1)))
        self.assertEqual(res.status_code, 404, res.content)
        self.assertEqual(self.shelf(self.cable), 100)

    def test_a_branch_outside_the_callers_grants_is_not_found(self):
        before = self.snapshot()
        body = self.body(self.qty(self.cable, 1), branch=self.norte.pk)
        self.assertEqual(self.preview(self.centro_only, body).status_code, 404)
        self.assertEqual(self.confirm(self.centro_only, body).status_code, 404)
        self.assertEqual(self.snapshot(), before)
        self.confirm(self.centro_only, self.body(self.qty(self.cable, 1)), expect=201)

    def test_an_inactive_branch_is_not_found(self):
        self.norte.is_active = False
        self.norte.save(update_fields=['is_active'])
        res = self.confirm(self.clerk, self.body(self.qty(self.cable, 1), branch=self.norte.pk))
        self.assertEqual(res.status_code, 404, res.content)

    def test_the_branch_defaults_to_the_callers_when_omitted(self):
        body = self.body(self.qty(self.cable, 2))
        del body['branch']
        res = self.confirm(self.centro_only, body, expect=201)
        self.assertEqual(res.json()['branch']['id'], self.centro.pk)

    def test_both_endpoints_are_throttled_like_the_other_inventory_writes(self):
        from store.receipt_views import InventoryReceiptConfirmView, InventoryReceiptPreviewView
        from store.throttles import AdminInventoryReportsThrottle, AdminStockMovementsThrottle

        self.assertEqual(InventoryReceiptConfirmView.throttle_classes, [AdminStockMovementsThrottle])
        self.assertEqual(InventoryReceiptPreviewView.throttle_classes, [AdminInventoryReportsThrottle])


# ---------------------------------------------------------------------------
# Supplier, document and notes
# ---------------------------------------------------------------------------

class ReasonAndTrailTest(ReceiptBase):
    def confirm_full(self, **extra):
        return self.confirm(self.costed, self.body(
            self.qty(self.cable, 50, '12.50'),
            self.dev(self.phone, unit('TRAZA000001', IMEIS[24], unit_cost='3800.00')),
            supplier=self.supplier.pk, document_ref='F001-777', notes='Pedido de octubre', **extra), expect=201)

    def test_every_kardex_line_carries_supplier_document_and_notes(self):
        self.confirm_full()
        reasons = list(StockMovement.objects.values_list('reason', flat=True))
        self.assertEqual(len(reasons), 2)
        for reason in reasons:
            self.assertEqual(reason, 'Recepción F001-777 · proveedor Importadora Lima · Pedido de octubre')

    def test_a_reason_without_supplier_or_notes_is_just_the_document(self):
        self.confirm(self.clerk, self.body(self.qty(self.cable, 1), document_ref='G-1'), expect=201)
        self.assertEqual(StockMovement.objects.get().reason, 'Recepción G-1')
        self.confirm(self.clerk, self.body(self.qty(self.case, 1)), expect=201)
        self.assertEqual(StockMovement.objects.get(product=self.case).reason, 'Recepción')

    def test_a_long_note_is_cut_to_the_limit_of_the_reason(self):
        self.confirm(self.clerk, self.body(
            self.dev(self.laptop, unit('LARGO000001')), supplier=self.supplier.pk, document_ref='D' * 60,
            notes='n' * 300), expect=201)
        reason = StockMovement.objects.get().reason
        self.assertLessEqual(len(reason), 300)
        self.assertTrue(reason.startswith('Recepción ' + 'D' * 60 + ' · proveedor Importadora Lima'))

    def test_the_audit_line_says_who_where_and_how_much_but_never_an_identifier(self):
        data = self.confirm_full().json()
        row = AdminAuditLog.objects.get(action=ACTION)
        self.assertEqual(row.company, self.company)
        self.assertEqual(row.actor, self.costed)
        self.assertEqual(row.target_type, 'inventory_receipt')
        meta = row.metadata
        self.assertEqual((meta['branch_id'], meta['supplier_id'], meta['document_ref']),
                         (self.centro.pk, self.supplier.pk, 'F001-777'))
        self.assertEqual(data['receipt_id'], f'rcp-{row.pk}')
        self.assertEqual([(l['product_id'], l['kind']) for l in meta['record']['lines']],
                         [(self.cable.pk, 'quantity'), (self.phone.pk, 'units')])
        dumped = json.dumps(meta)
        for secret in ('TRAZA000001', IMEIS[24], '12.5', '3800'):
            self.assertNotIn(secret, dumped)

    def test_the_document_and_notes_are_cleaned_and_bounded(self):
        for field, size in (('document_ref', 61), ('notes', 301)):
            res = self.confirm(self.clerk, self.body(self.qty(self.cable, 1), **{field: 'x' * size}))
            self.assertEqual(res.status_code, 400, field)
        self.confirm(self.clerk, self.body(self.qty(self.cable, 1), document_ref='  F 1 \n'), expect=201)
        self.assertIn('Recepción F 1', StockMovement.objects.get().reason)

    def test_an_inactive_supplier_of_the_company_is_still_found_by_id_but_refused(self):
        Supplier.objects.filter(pk=self.supplier.pk).update(is_active=False)
        res = self.confirm(self.clerk, self.body(self.qty(self.cable, 1), supplier=self.supplier.pk))
        self.assertEqual(res.status_code, 400, res.content)


# ---------------------------------------------------------------------------
# Hostile bodies
# ---------------------------------------------------------------------------

class MalformedBodyTest(ReceiptBase):
    def bodies(self):
        cable, phone = self.cable.pk, self.phone.pk

        def one(**line):
            return {'branch': self.centro.pk, 'lines': [{'kind': 'quantity', 'product': cable, 'quantity': 1, **line}]}

        def device(**row):
            return {'branch': self.centro.pk, 'lines': [{'kind': 'units', 'product': phone,
                                                          'units': [{'serial': 'FUZZ0001', 'imei': IMEIS[25], **row}]}]}

        base = {'branch': self.centro.pk}
        return [
            [], 'texto', 5, None, {}, {'lines': 'x', **base}, {'lines': [], **base}, {'lines': {}, **base},
            {'lines': [None], **base}, {'lines': [[]], **base}, {'lines': ['x'], **base},
            {'lines': [{'kind': 'otro', 'product': cable}], **base}, {'lines': [{'product': cable}], **base},
            {'lines': [{'kind': 'quantity', 'product': 'abc', 'quantity': 1}], **base},
            {'lines': [{'kind': 'quantity', 'product': True, 'quantity': 1}], **base},
            {'lines': [{'kind': 'quantity', 'product': 10 ** 30, 'quantity': 1}], **base},
            {'lines': [{'kind': 'quantity', 'product': -1, 'quantity': 1}], **base},
            {'lines': [{'kind': 'quantity', 'product': 1.5, 'quantity': 1}], **base},
            {'lines': [{'kind': 'quantity', 'product': [cable], 'quantity': 1}], **base},
            one(quantity='5'), one(quantity=5.5), one(quantity=True), one(quantity=0), one(quantity=100001),
            one(quantity=-3), one(quantity=10 ** 40), one(quantity=None), one(quantity=[1]), one(quantity={'a': 1}),
            one(unit_cost='NaN'), one(unit_cost='1e5'), one(unit_cost='１２'), one(unit_cost=-1),
            one(unit_cost='12.345'), one(unit_cost={'a': 1}), one(unit_cost=[1]), one(unit_cost=True),
            one(unit_cost=12.5), one(unit_cost='99999999999'), one(unit_cost=' 1 2 '),
            {'lines': [{'kind': 'units', 'product': phone, 'units': 'x'}], **base},
            {'lines': [{'kind': 'units', 'product': phone, 'units': []}], **base},
            {'lines': [{'kind': 'units', 'product': phone, 'units': [None]}], **base},
            {'lines': [{'kind': 'units', 'product': phone, 'units': [[]]}], **base},
            {'lines': [{'kind': 'units', 'product': phone, 'units': [{}]}], **base},
            device(serial={'a': 1}), device(serial=['x']), device(serial=12345678), device(imei=356938035643809),
            device(imei=['1']), device(imei2=True), device(battery_health='abc'), device(battery_health=[1]),
            device(battery_health=10 ** 30), device(battery_health=True), device(condition={'x': 1}),
            device(condition='roto'), device(condition=5), device(cosmetic_grade={'a': 1}),
            device(cosmetic_grade=10 ** 30), device(notes=['x']), device(notes=123),
            device(unit_cost='NaN'), device(unit_cost='-5'), device(unit_cost='12.345'),
            {**base, 'branch': 'x', 'lines': one()['lines']}, {**base, 'branch': True, 'lines': one()['lines']},
            {**base, 'branch': 10 ** 30, 'lines': one()['lines']}, {**base, 'branch': -1, 'lines': one()['lines']},
            {**base, 'branch': [1], 'lines': one()['lines']}, {**base, 'branch': 1.5, 'lines': one()['lines']},
            {**one(), 'supplier': 'x'}, {**one(), 'supplier': True}, {**one(), 'supplier': 10 ** 30},
            {**one(), 'supplier': -1}, {**one(), 'supplier': [1]}, {**one(), 'supplier': 0},
            {**one(), 'document_ref': 123}, {**one(), 'document_ref': ['a']}, {**one(), 'document_ref': 'x' * 61},
            {**one(), 'notes': ['x']}, {**one(), 'notes': 'x' * 301}, {**one(), 'notes': {'a': 1}},
            {**one(), 'notes': 'a\x00b'}, device(notes='x\x00'),
            device(serial='AB\x00CD'), device(imei='٣٥٦٩٣٨٠٣٥٦٤٣٨٠٩'), device(cosmetic_grade='9' * 30),
            {**one(), 'lines': one()['lines'] * 51},
            {**base, 'lines': [{'kind': 'units', 'product': phone, 'units': [{'serial': f'S{n:07d}'} for n in range(201)]}]},
            {**base, 'lines': [{'kind': 'units', 'product': phone,
                                'units': [{'serial': f'S{n:07d}'} for n in range(120)]}] * 2},
        ]

    def test_no_malformed_body_is_a_500_or_writes_anything(self):
        before = self.snapshot()
        bodies = self.bodies()
        self.assertGreaterEqual(len(bodies), 30)
        for body in bodies:
            cache.clear()  # the throttle is not what this test is about
            res = self.preview(self.costed, body)
            # A row the services refuse (an unknown condition…) is a LINE error: 200 with `ok: false`.
            self.assertIn(res.status_code, (200, 400, 404), f'preview {str(body)[:140]} -> {res.status_code}')
            if res.status_code == 200:
                self.assertFalse(res.json()['ok'], str(body)[:140])
            res = self.confirm(self.costed, body)
            self.assertIn(res.status_code, (400, 404), f'confirm {str(body)[:140]} -> {res.status_code}')
        self.assertEqual(self.snapshot(), before)

    def test_raw_json_that_is_not_json_or_not_finite_is_a_400(self):
        before = self.snapshot()
        for raw in ('{', 'NaN', '{"lines": NaN}', '{"lines": [{"kind": "quantity", "quantity": Infinity}]}',
                    '[1,', '', '{"lines": [' * 200, '9' * 5000, '[' * 100000,
                    '{"lines": [{"kind": "quantity", "product": 1, "quantity": 1}], "notes": "\\ud800"}',
                    '{"lines": [{"kind": "quantity", "product": 1, "quantity": 1}], "notes": "a\\u0000b"}'):
            for url, headers in ((PREVIEW, {}), (CONFIRM, {'HTTP_IDEMPOTENCY_KEY': self.key()})):
                cache.clear()
                res = _client(self.costed).post(url, data=raw, content_type='application/json', **headers)
                self.assertIn(res.status_code, (400,), f'{raw[:30]!r} {res.status_code}')
        self.assertEqual(self.snapshot(), before)

    def test_a_form_encoded_body_is_a_400(self):
        res = _client(self.costed).post(CONFIRM, {'lines': 'x'}, HTTP_IDEMPOTENCY_KEY=self.key())
        self.assertEqual(res.status_code, 400)

    def test_the_worst_case_body_is_far_below_the_default_body_limit(self):
        """This route has no upload tier: the 1 MB default of `request_limits` must cover its largest legal body."""
        from store import request_limits

        row = {'serial': 'S' * 40, 'imei': IMEIS[0], 'imei2': IMEIS[1], 'condition': 'refurbished',
               'battery_health': 100, 'cosmetic_grade': 'A' * 40, 'notes': 'n' * 2000, 'unit_cost': '99999999.99'}
        worst = {'branch': self.centro.pk, 'supplier': self.supplier.pk, 'document_ref': 'D' * 60, 'notes': 'n' * 300,
                 'lines': [self.qty(self.cable, 100000, '99999999.99') for _ in range(49)]
                          + [self.dev(self.laptop, *[row] * units_service.MAX_BATCH)]}
        size = len(json.dumps(worst))
        self.assertLess(size, 600 * 1024)
        for url in (PREVIEW, CONFIRM):
            self.assertEqual(request_limits.limit_for(url), request_limits.default_max_bytes())
            self.assertLess(size, request_limits.limit_for(url))
        # …and a body over the limit is refused before it is read (the middleware, not this view).
        from store.test_request_limits import _call
        response, reached = _call(CONFIRM, str(request_limits.default_max_bytes() + 1))
        self.assertEqual((response.status_code, reached), (413, False))

    def test_the_most_a_request_may_carry_is_accepted(self):
        lines = [self.qty(self.cable, 1) for _ in range(50)]
        self.preview(self.clerk, self.body(*lines), expect=200)
        many = [unit(f'MUCHO{n:06d}', None) for n in range(units_service.MAX_BATCH)]
        data = self.preview(self.clerk, self.body(self.dev(self.laptop, *many)), expect=200).json()
        self.assertTrue(data['ok'])


# ---------------------------------------------------------------------------
# The writers stay the writers
# ---------------------------------------------------------------------------

class ComposesTheServicesTest(ReceiptBase):
    STORE = Path(__file__).resolve().parent

    def test_the_receipt_modules_never_write_stock_themselves(self):
        for name in ('receipt_services.py', 'receipt_views.py'):
            source = (self.STORE / name).read_text()
            self.assertIsNone(re.search(r'(StockUnit|StockMovement|BranchStock)\.objects\.(create|bulk_create|update)',
                                        source), name)
            self.assertNotRegex(source, r'\.save\(', name)
            self.assertNotIn('select_for_update', source, name)

    def test_the_services_it_composes_are_the_ones_called(self):
        with mock.patch.object(units_service, 'receive_units', wraps=units_service.receive_units) as receive, \
                mock.patch.object(inventory_services, 'apply_manual_stock_movement',
                                  wraps=inventory_services.apply_manual_stock_movement) as manual:
            self.confirm(self.clerk, self.body(
                self.qty(self.cable, 3), self.dev(self.phone, unit('SERV0001', IMEIS[26]))), expect=201)
        self.assertEqual(receive.call_count, 1)
        self.assertEqual(manual.call_count, 1)
        self.assertEqual(manual.call_args.kwargs['movement_type'], StockMovement.PURCHASE_ENTRY)
        self.assertEqual(receive.call_args.kwargs['movement_type'], StockMovement.PURCHASE_ENTRY)


# ---------------------------------------------------------------------------
# Two confirms at once
# ---------------------------------------------------------------------------

class TwoConfirmsAtOnceTest(TransactionTestCase):
    def test_two_confirms_with_one_key_receive_once(self):
        cache.clear()
        company = _p3_company('ops-carrera', 'Tienda Carrera')
        branch = company.default_inventory_branch
        cable = _prod(company, 'Cable carrera', 'cable-carrera', inventory=0)
        phone = _prod(company, 'iPhone carrera', 'iphone-carrera', inventory=0)
        Product.objects.filter(pk=phone.pk).update(is_serialized=True, requires_imei=True)
        user, _ = _p2d_member(company, 'ops_race', ['inventory.view', 'inventory.adjust'])
        body = {'branch': branch.pk, 'supplier': None, 'document_ref': 'R-1', 'notes': '', 'lines': [
            {'kind': 'quantity', 'product': cable.pk, 'quantity': 5, 'unit_cost': None},
            {'kind': 'units', 'product': phone.pk,
             'units': [{'serial': 'CARRERA0001', 'imei': IMEIS[27], 'condition': 'new'}]}]}

        barrier, statuses = Barrier(2), []

        def post():
            try:
                client = APIClient()
                client.force_authenticate(user=user)
                barrier.wait(timeout=10)
                res = client.post(CONFIRM, body, format='json', HTTP_IDEMPOTENCY_KEY='race-key')
                statuses.append((res.status_code, bool(res.json().get('replayed'))))
            finally:
                connection.close()

        threads = [Thread(target=post) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)

        self.assertEqual(sorted(statuses), [(200, True), (201, False)], statuses)
        self.assertEqual(StockUnit.objects.filter(company=company).count(), 1)
        self.assertEqual(StockMovement.objects.filter(company=company).count(), 2)
        self.assertEqual(LabelBatch.objects.filter(company=company).count(), 2)
        self.assertEqual(AdminAuditLog.objects.filter(company=company, action=ACTION).count(), 1)
        self.assertEqual(BranchStock.objects.get(branch=branch, product=cable).quantity, 5)


# ---------------------------------------------------------------------------
# Review of #203 — one physical identifier, whatever column or alias carries it
# ---------------------------------------------------------------------------

def _world(slug):
    """A company with one cellular product and a clerk, for the tests that need real commits."""
    cache.clear()
    company = _p3_company(slug, f'Tienda {slug}')
    phone = _prod(company, f'iPhone {slug}', f'iphone-{slug}', inventory=0)
    Product.objects.filter(pk=phone.pk).update(is_serialized=True, requires_imei=True)
    user, _ = _p2d_member(company, f'{slug}_clerk', ['inventory.view', 'inventory.adjust'])
    return company, company.default_inventory_branch, phone, user


def _confirm_body(branch, phone, row):
    return {'branch': branch.pk, 'supplier': None, 'document_ref': '', 'notes': '', 'lines': [
        {'kind': 'units', 'product': phone.pk, 'units': [row]}]}


class _Meet:
    """Make two threads reach the duplicate check together; a thread kept out by a lock simply times out."""

    def __init__(self, real):
        self.real, self.barrier = real, Barrier(2)

    def __call__(self, *args, **kwargs):
        found = self.real(*args, **kwargs)
        try:
            self.barrier.wait(timeout=2)
        except BrokenBarrierError:
            pass
        return found


class IdentifierRaceTest(TransactionTestCase):
    def run_pair(self, calls):
        results = []

        def worker(call):
            try:
                results.append(call())
            finally:
                connection.close()

        meet = _Meet(units_service._already_in_stock)
        with mock.patch.object(units_service, '_already_in_stock', side_effect=meet):
            threads = [Thread(target=worker, args=(call,)) for call in calls]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=60)
        return results

    def post(self, user, body, key):
        client = APIClient()
        client.force_authenticate(user=user)
        return lambda: client.post(CONFIRM, body, format='json', HTTP_IDEMPOTENCY_KEY=key).status_code

    def test_one_imei_as_imei_and_as_imei2_is_received_once(self):
        company, branch, phone, user = _world('ops-alias')
        first = _confirm_body(branch, phone, {'serial': 'ALIAS10001', 'imei': IMEIS[0]})
        second = _confirm_body(branch, phone, {'serial': 'ALIAS10002', 'imei': IMEIS[1], 'imei2': IMEIS[0]})
        statuses = self.run_pair([self.post(user, first, 'alias-1'), self.post(user, second, 'alias-2')])

        self.assertEqual(sorted(statuses), [201, 400], statuses)
        holders = StockUnit.objects.filter(company=company)
        self.assertEqual(holders.count(), 1)
        self.assertEqual(BranchStock.objects.get(branch=branch, product=phone).quantity, 1)
        self.assertEqual(StockMovement.objects.filter(company=company).count(), 1)

    def test_receive_units_serialises_the_same_imei_in_both_columns(self):
        company, branch, phone, user = _world('ops-direct')
        phone.refresh_from_db()

        def receive(row):
            return lambda: _outcome(units_service.receive_units, branch=branch, product=phone, units=[row],
                                    actor=user, reason='Compra')

        results = self.run_pair([
            receive({'serial_number': 'DIRECT10001', 'imei': IMEIS[2]}),
            receive({'serial_number': 'DIRECT10002', 'imei': IMEIS[3], 'imei2': IMEIS[2]}),
        ])
        self.assertEqual(sorted(results), ['StockUnitError', 'ok'], results)
        self.assertEqual(StockUnit.objects.filter(company=company).count(), 1)

    def test_the_same_serial_at_once_is_received_once(self):
        company, branch, phone, user = _world('ops-serial')
        one = _confirm_body(branch, phone, {'serial': 'SAMESER001', 'imei': IMEIS[4]})
        two = _confirm_body(branch, phone, {'serial': 'SAMESER001', 'imei': IMEIS[5]})
        statuses = self.run_pair([self.post(user, one, 'ser-1'), self.post(user, two, 'ser-2')])
        self.assertEqual(sorted(statuses), [201, 400], statuses)
        self.assertEqual(StockUnit.objects.filter(company=company).count(), 1)


def _outcome(call, **kwargs):
    try:
        call(**kwargs)
    except units_service.StockUnitError:
        return 'StockUnitError'
    return 'ok'


# ---------------------------------------------------------------------------
# Review of #203 — P3s
# ---------------------------------------------------------------------------

class _Driver(Exception):
    """What psycopg2 attaches as `__cause__`: an exception that knows its SQLSTATE."""

    def __init__(self, pgcode):
        super().__init__(pgcode)
        self.pgcode = pgcode


class BusyTest(ReceiptBase):
    def deadlock(self, code='40P01'):
        error = OperationalError('deadlock detected')
        error.__cause__ = _Driver(code)
        return error

    def test_a_deadlock_or_serialization_failure_is_a_409_busy_and_the_key_stays_free(self):
        for code in ('40P01', '40001'):
            key, before = self.key(), self.snapshot()
            body = self.body(self.qty(self.cable, 5))
            with mock.patch('store.receipt_services.confirm', side_effect=self.deadlock(code)):
                res = self.confirm(self.clerk, body, key=key)
            self.assertEqual(res.status_code, 409, res.content)
            self.assertEqual(res.json()['code'], 'busy')
            self.assertEqual(self.snapshot(), before)
            self.confirm(self.clerk, body, key=key, expect=201)
            self.shelf(self.cable)

    def test_any_other_database_error_is_not_disguised_as_busy(self):
        error = OperationalError('connection lost')
        error.__cause__ = _Driver('08006')
        with mock.patch('store.receipt_services.confirm', side_effect=error):
            with self.assertRaises(OperationalError):
                self.confirm(self.clerk, self.body(self.qty(self.cable, 5)))


class NoEchoTest(ReceiptBase):
    def test_a_serial_that_is_a_product_code_is_refused_without_repeating_it(self):
        ProductBarcode.objects.create(company=self.company, product=self.case, code='CODE12345')
        text = self.preview(self.clerk, self.body(self.dev(self.laptop, unit('CODE12345', None))),
                            expect=200).content.decode()
        self.assertIn('Equipo 1', text)
        self.assertNotIn('CODE12345', text)
        res = self.confirm(self.clerk, self.body(self.dev(self.laptop, unit('CODE12345', None))), expect=400)
        self.assertNotIn('CODE12345', res.content.decode())

    def test_a_serial_that_is_another_devices_label_code_is_refused_without_repeating_it(self):
        self.confirm(self.clerk, self.body(self.dev(self.laptop, unit('PRIMERA0001', None))), expect=201)
        code = StockUnit.objects.get(serial_number='PRIMERA0001').label_code
        res = self.preview(self.clerk, self.body(self.dev(self.laptop, unit(code, None))), expect=200)
        self.assertFalse(res.json()['ok'])
        self.assertNotIn(code, res.content.decode())

    def test_a_refusal_raised_while_writing_never_repeats_what_was_sent(self):
        """The race the check cannot see: the service refuses, and its words carry the serial."""
        self.confirm(self.clerk, self.body(self.dev(self.laptop, unit('YAESTA9999', None))), expect=201)
        with mock.patch.object(units_service, 'find_conflict', return_value=('', '')):
            res = self.confirm(self.clerk, self.body(self.dev(self.laptop, unit('YAESTA9999', None))))
        self.assertEqual(res.status_code, 400, res.content)
        self.assertNotIn('YAESTA9999', res.content.decode())
        self.assertIn('Equipo 1', res.json()['detail'])


class AliasTest(ReceiptBase):
    def test_two_spellings_of_one_field_that_disagree_are_refused_naming_the_field(self):
        cases = (
            ({'serial': 'UNO0000001', 'serial_number': 'DOS0000002'}, 'serial'),
            ({'serial': 'UNO0000001', 'unit_cost': '5.00', 'cost': '6.00'}, 'unit_cost'),
        )
        for extra, field in cases:
            row = {'imei': None, 'condition': 'new', **extra}
            for call in (self.preview, self.confirm):
                res = call(self.costed, self.body(self.dev(self.laptop, row)))
                self.assertEqual(res.status_code, 400, res.content)
                self.assertEqual(res.json()['code'], 'invalid')
                self.assertTrue(res.json()['field'].endswith(field), res.json())

    def test_two_spellings_with_the_same_value_are_fine(self):
        row = {'serial': 'IGUAL00001', 'serial_number': 'IGUAL00001', 'unit_cost': '5.00', 'cost': '5',
               'imei': None, 'condition': 'new'}
        self.confirm(self.costed, self.body(self.dev(self.laptop, row)), expect=201)
        self.assertEqual(str(StockUnit.objects.get(serial_number='IGUAL00001').cost), '5.00')

    def test_an_empty_spelling_does_not_hide_the_other(self):
        row = {'serial': '', 'serial_number': 'OTRO000001', 'unit_cost': '', 'cost': '7.50',
               'imei': None, 'condition': 'new'}
        self.confirm(self.costed, self.body(self.dev(self.laptop, row)), expect=201)
        unit_ = StockUnit.objects.get(serial_number='OTRO000001')
        self.assertEqual(str(unit_.cost), '7.50')


class ReadAfterCommitTest(TransactionTestCase):
    """The summaries are read AFTER the transaction: a failing read can neither undo nor fake a reception."""

    def broken_read(self, company, ids):
        try:
            with connection.cursor() as cursor:
                cursor.execute('SELECT 1/0')
        except Exception:  # noqa: BLE001 — what `summaries` itself does with a failing read
            return [dict(label_batches._ERROR_SUMMARY)]
        return []

    def test_a_read_that_breaks_the_connection_state_does_not_turn_a_write_into_a_phantom_201(self):
        company, branch, phone, user = _world('ops-read')
        cable = _prod(company, 'Cable lectura', 'cable-lectura', inventory=0)
        _m7_stock(branch, cable, 100)
        body = {'branch': branch.pk, 'lines': [{'kind': 'quantity', 'product': cable.pk, 'quantity': 50}]}
        client = APIClient()
        client.force_authenticate(user=user)
        with mock.patch.object(label_batches, 'summaries', side_effect=self.broken_read):
            res = client.post(CONFIRM, body, format='json', HTTP_IDEMPOTENCY_KEY='read-1')
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(BranchStock.objects.get(branch=branch, product=cable).quantity, 150)
        self.assertTrue(res.json()['label_error'])
        self.assertEqual(AdminAuditLog.objects.filter(company=company, action=ACTION).count(), 1)

    def test_a_summary_read_that_raises_still_answers_honestly(self):
        company, branch, phone, user = _world('ops-read2')
        cable = _prod(company, 'Cable lectura 2', 'cable-lectura-2', inventory=0)
        body = {'branch': branch.pk, 'lines': [{'kind': 'quantity', 'product': cable.pk, 'quantity': 5}]}
        client = APIClient()
        client.force_authenticate(user=user)
        with mock.patch.object(label_batches, 'summaries', side_effect=RuntimeError('boom')):
            res = client.post(CONFIRM, body, format='json', HTTP_IDEMPOTENCY_KEY='read-2')
        self.assertEqual(res.status_code, 201, res.content)
        self.assertTrue(res.json()['label_error'])
        self.assertEqual(BranchStock.objects.get(branch=branch, product=cable).quantity, 5)


# ---------------------------------------------------------------------------
# A replay is a read of the receipt: it needs the caller's CURRENT reach over the receipt's branch
# ---------------------------------------------------------------------------

class ReplayAuthorizationTest(ReceiptBase):
    def operator(self, name, branches):
        user, membership = _p2d_member(self.company, name, ['inventory.view', 'inventory.adjust'], branches=branches)
        return user, membership

    def revoke(self, membership, *branches):
        MembershipBranchAccess.objects.filter(membership=membership, branch__in=branches).delete()
        cache.clear()

    def history(self):
        """Every row the receipt left: a revoked grant must not touch any of them."""
        return {
            **self.snapshot(),
            'movement_rows': sorted(StockMovement.objects.values_list('pk', 'branch_id', 'quantity', 'reason')),
            'unit_rows': sorted(StockUnit.objects.values_list('pk', 'branch_id', 'serial_number', 'status')),
            'audit_rows': sorted((r.pk, json.dumps(r.metadata, sort_keys=True))
                                 for r in AdminAuditLog.objects.filter(action=ACTION)),
            'batch_rows': sorted(LabelBatch.objects.values_list('pk', 'branch_id', 'movement_id')),
        }

    def receipt_body(self, explicit=True):
        body = self.body(self.qty(self.cable, 10), self.dev(self.phone, unit('REVOCA0001', IMEIS[28])),
                         supplier=self.supplier.pk, document_ref='F009-1')
        if not explicit:
            del body['branch']
        return body

    def assert_nothing_of_the_receipt(self, res):
        self.assertEqual(res.status_code, 404, res.content)
        text = res.content.decode()
        self.assertEqual(res.json()['code'], 'not_found')
        for leaked in ('receipt_id', 'rcp-', 'F009-1', 'Importadora Lima', self.cable.name, self.phone.name,
                       'movement_id', 'label_batch', 'REVOCA0001', 'replayed'):
            self.assertNotIn(leaked, text)

    def test_a_valid_replay_still_answers_the_stored_receipt(self):
        user, _ = self.operator('ops_replay_ok', [self.centro, self.norte])
        key, body = self.key(), self.receipt_body()
        first = self.confirm(user, body, key=key, expect=201).json()
        before = self.history()
        again = self.confirm(user, body, key=key, expect=200).json()
        self.assertTrue(again['replayed'])
        self.assertEqual({k: v for k, v in again.items() if k != 'replayed'}, first)
        self.assertEqual(self.history(), before)

    def test_a_revoked_branch_denies_the_replay_with_the_explicit_branch(self):
        user, membership = self.operator('ops_replay_explicit', [self.centro, self.norte])
        key, body = self.key(), self.receipt_body()
        self.confirm(user, body, key=key, expect=201)
        self.revoke(membership, self.centro)
        self.assert_nothing_of_the_receipt(self.confirm(user, body, key=key))

    def test_a_revoked_branch_denies_the_replay_with_the_implicit_default_branch(self):
        user, membership = self.operator('ops_replay_implicit', [self.centro])
        key, body = self.key(), self.receipt_body(explicit=False)
        self.confirm(user, body, key=key, expect=201)
        # Another grant makes ANOTHER branch the caller's current default: the stored one is what counts.
        MembershipBranchAccess.objects.create(membership=membership, branch=self.norte)
        self.revoke(membership, self.centro)
        self.assert_nothing_of_the_receipt(self.confirm(user, body, key=key))

    def test_a_branch_deactivated_later_denies_the_replay(self):
        user, _ = self.operator('ops_replay_inactive', [self.centro, self.norte])
        key, body = self.key(), self.receipt_body(explicit=False)
        body['branch'] = self.norte.pk
        self.confirm(user, body, key=key, expect=201)
        self.norte.is_active = False
        self.norte.save(update_fields=['is_active'])
        cache.clear()
        self.assert_nothing_of_the_receipt(self.confirm(user, body, key=key))

    def test_a_different_body_with_the_same_key_does_not_reveal_that_the_key_exists(self):
        user, membership = self.operator('ops_replay_other_body', [self.centro, self.norte])
        key = self.key()
        self.confirm(user, self.receipt_body(), key=key, expect=201)
        self.revoke(membership, self.centro)
        different = self.body(self.qty(self.case, 3))
        self.assert_nothing_of_the_receipt(self.confirm(user, different, key=key))

    def test_revoking_access_leaves_the_historical_record_untouched_for_an_authorized_admin(self):
        user, membership = self.operator('ops_replay_history', [self.centro, self.norte])
        key, body = self.key(), self.receipt_body()
        first = self.confirm(user, body, key=key, expect=201).json()
        before = self.history()
        self.revoke(membership, self.centro)
        self.confirm(user, body, key=key, expect=404)
        self.assertEqual(self.history(), before)
        # Somebody who still reaches the branch (the same author, restored) gets the same stored answer.
        MembershipBranchAccess.objects.create(membership=membership, branch=self.centro)
        cache.clear()
        again = self.confirm(user, body, key=key, expect=200).json()
        self.assertEqual({k: v for k, v in again.items() if k != 'replayed'}, first)
        self.assertEqual(self.history(), before)

    def test_another_companys_operator_and_a_user_without_the_permission_are_unchanged(self):
        user, _ = self.operator('ops_replay_company', [self.centro])
        key, body = self.key(), self.receipt_body()
        self.confirm(user, body, key=key, expect=201)
        self.assertEqual(self.confirm(self.outsider, body, key=key).status_code, 404)
        self.assertEqual(self.confirm(self.viewer, body, key=key).status_code, 403)
        # The same key by the same author but a different body is still 409 while the access stands.
        self.confirm(user, self.body(self.qty(self.case, 3)), key=key, expect=409)

    def test_a_damaged_stored_branch_is_an_explicit_denial_never_a_500_or_the_default_branch(self):
        user, _ = self.operator('ops_replay_damaged', [self.centro, self.norte])
        key, body = self.key(), self.receipt_body(explicit=False)
        self.confirm(user, body, key=key, expect=201)
        trail = AdminAuditLog.objects.get(action=ACTION)
        original = json.loads(json.dumps(trail.metadata))
        damages = {
            'no record': lambda m: m.pop('record'),
            'null record': lambda m: m.update(record=None),
            'no branch_id': lambda m: m['record'].pop('branch_id'),
            'branch_id null': lambda m: m['record'].update(branch_id=None),
            'branch_id text': lambda m: m['record'].update(branch_id='abc'),
            'branch_id huge': lambda m: m['record'].update(branch_id=10 ** 30),
            'branch_id bool': lambda m: m['record'].update(branch_id=True),
            'branch_id list': lambda m: m['record'].update(branch_id=[self.centro.pk]),
            'branch_id zero': lambda m: m['record'].update(branch_id=0),
            'branch gone': lambda m: m['record'].update(branch_id=999999),
        }
        for name, damage in damages.items():
            metadata = json.loads(json.dumps(original))
            damage(metadata)
            AdminAuditLog.objects.filter(pk=trail.pk).update(metadata=metadata)
            res = self.confirm(user, body, key=key)
            self.assertEqual(res.status_code, 404, f'{name}: {res.content}')
            self.assert_nothing_of_the_receipt(res)
        AdminAuditLog.objects.filter(pk=trail.pk).update(metadata=original)
        self.confirm(user, body, key=key, expect=200)
