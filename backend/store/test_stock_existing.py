"""
STOCK-EXISTING-01 — «Ingreso de inventario existente».

The opening balance (INITIAL_STOCK) of a shelf closes as soon as the shelf has any other Kardex line: a sale, a
purchase (including «Caja: llegó el equipo»), a transfer, a hold, a write-off. From then on the stock the shop ALREADY
HAD could only enter as «Entrada por compra», which reads as a fictitious purchase in the Kardex, the movement chart
and the import preview.

A third entry kind, `existing`, is offered on the three doors that already ask «compra o inventario inicial»:

    «Recibir mercadería»      receipt_services        entry_kind=existing
    «Registrar equipos»       stock_unit_views POST   entry_kind=existing
    Excel de equipos          unit_import_services    entry_kind=existing

It keeps writing PURCHASE_ENTRY (no migration: cost, labels, annulment and correction are untouched) with the mark
`metadata.entry_kind = 'existing_stock'`, and every reader that names the line says «Ingreso de inventario existente»
and counts it apart from purchases. It is allowed on a shelf whose opening is closed, with the authority of a purchase.
"""
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from store import inventory_services as inventory
from store import label_batches, pos_device_services, receipt_services
from store import stock_unit_services as units
from store import unit_import_batch
from store import unit_import_services as unit_import
from store.models import BranchStock, LabelBatch, Order, OrderItem, StockMovement, StockUnit
from store.serializers import StockMovementSerializer
from store.test_inv_load import LoadBase
from store.test_inventory_receipts import ReceiptBase, unit
from store.test_stock_units import UnitsBase, imei
from store.unit_stock_check import mismatches
from store.v1_inventory_serializers import V1StockMovementSerializer

#: Luhn-valid synthetic IMEIs, a range no other suite uses.
IMEIS = [imei(f'35693803691{n:03d}') for n in range(0, 80)]
LABEL = 'Ingreso de inventario existente'
MARK = 'existing_stock'


def _paid_order(company, branch, product, quantity=1):
    order = Order.objects.create(
        company=company, fulfillment_branch=branch, customer_email='cliente@example.com',
        total=product.price * quantity, status=Order.Status.PAID, paid=True, paid_at=timezone.now(),
    )
    OrderItem.objects.create(order=order, product=product, quantity=quantity, price=product.price)
    return order


def _is_existing(movement):
    return movement.movement_type == StockMovement.PURCHASE_ENTRY and (movement.metadata or {}).get('entry_kind') == MARK


# ---------------------------------------------------------------------------
# 1. The Kardex line itself
# ---------------------------------------------------------------------------

class KardexLabelTest(UnitsBase):
    def test_a_marked_purchase_entry_reads_as_existing_stock_everywhere(self):
        units.receive_units(branch=self.branch_a, product=self.phone, actor=self.staff, reason='Inventario existente',
                            units=[{'serial_number': 'EXK-0001', 'imei': IMEIS[0]}],
                            metadata=receipt_services.existing_stock_metadata())
        movement = StockMovement.objects.get(product=self.phone)
        self.assertEqual(movement.movement_type, StockMovement.PURCHASE_ENTRY)
        self.assertEqual(movement.metadata.get('entry_kind'), MARK)
        self.assertTrue(movement.is_existing_stock)
        self.assertEqual(movement.kardex_label, LABEL)
        self.assertEqual(StockMovementSerializer(movement).data['movement_type_label'], LABEL)
        self.assertEqual(V1StockMovementSerializer(movement).data['movement_type_label'], LABEL)
        # The unit ids the writer adds are still there, next to the mark.
        self.assertEqual(movement.metadata.get('unit_ids'), [StockUnit.objects.get(serial_number='EXK-0001').pk])

    def test_an_unmarked_purchase_is_still_a_purchase(self):
        self.receive(self.row('EXK-0101', IMEIS[1]))
        movement = StockMovement.objects.get(product=self.phone)
        self.assertFalse(movement.is_existing_stock)
        self.assertEqual(movement.kardex_label, 'Entrada por compra')

    def test_the_movement_chart_counts_existing_stock_apart_from_purchases(self):
        self.receive(self.row('EXK-0201', IMEIS[2]), self.row('EXK-0202', IMEIS[3]))
        units.receive_units(branch=self.branch_a, product=self.phone, actor=self.staff, reason='Inventario existente',
                            units=[{'serial_number': 'EXK-0203', 'imei': IMEIS[4]}],
                            metadata=receipt_services.existing_stock_metadata())
        rows = {r['label']: r['value'] for r in inventory.get_movement_type_distribution([self.branch_a])}
        self.assertEqual(rows.get('Entrada por compra'), 2)
        self.assertEqual(rows.get(LABEL), 1)
        # Another company's branches see none of it.
        self.assertEqual(inventory.get_movement_type_distribution([self.foreign_branch]), [])

    def test_the_kardex_filter_separates_existing_stock_from_purchases(self):
        from rest_framework.test import APIClient

        self.receive(self.row('EXK-0301', IMEIS[5]))
        units.receive_units(branch=self.branch_a, product=self.phone, actor=self.staff, reason='Inventario existente',
                            units=[{'serial_number': 'EXK-0302', 'imei': IMEIS[6]}],
                            metadata=receipt_services.existing_stock_metadata())
        api = APIClient()
        api.force_authenticate(user=self.staff)
        url = '/api/admin/inventory/movements/'

        def labels(**params):
            res = api.get(url, {'branch': self.branch_a.pk, 'product': self.phone.pk, **params})
            self.assertEqual(res.status_code, 200, res.content)
            body = res.json()
            rows = body['results'] if isinstance(body, dict) else body
            return sorted(row['movement_type_label'] for row in rows)

        self.assertEqual(labels(), sorted(['Entrada por compra', LABEL]))
        self.assertEqual(labels(movement_type='existing_stock'), [LABEL])
        self.assertEqual(labels(movement_type='purchase_entry'), ['Entrada por compra'])


# ---------------------------------------------------------------------------
# 2. «Recibir mercadería»
# ---------------------------------------------------------------------------

class ReceiptExistingTest(ReceiptBase):
    def setUp(self):
        super().setUp()
        self._imeis = iter(IMEIS[10:40])

    def closed_shelf(self):
        """The phone's shelf in Centro: a «Caja: llegó el equipo» entry and a sale. Its opening is closed."""
        units.receive_units(
            branch=self.centro, product=self.phone, actor=self.costed, reason=pos_device_services.REASON,
            units=[{'serial_number': 'EXR-CAJA-1', 'imei': next(self._imeis)},
                   {'serial_number': 'EXR-CAJA-2', 'imei': next(self._imeis)}])
        inventory.record_sale_stock_movements(_paid_order(self.company, self.centro, self.phone), strict=True)
        kinds = set(StockMovement.objects.filter(branch=self.centro, product=self.phone)
                    .values_list('movement_type', flat=True))
        self.assertEqual(kinds, {StockMovement.PURCHASE_ENTRY, StockMovement.SALE_EXIT})

    def test_a_closed_shelf_refuses_opening_but_takes_existing_with_its_mark(self):
        self.closed_shelf()
        opening = self.preview(self.costed, self.body(self.dev(self.phone, unit('EXR-0001', next(self._imeis))),
                                                      entry_kind='opening'), expect=200).json()
        self.assertFalse(opening['ok'])
        self.assertIn(receipt_services.OPENING_HAS_HISTORY, opening['lines'][0]['codes'])

        body = self.body(self.dev(self.phone, unit('EXR-0002', next(self._imeis), cost='3100.00')),
                         entry_kind='existing')
        preview = self.preview(self.costed, body, expect=200).json()
        self.assertTrue(preview['ok'], preview)
        self.assertEqual(preview['entry_kind'], 'existing')
        res = self.confirm(self.costed, body, expect=201).json()
        self.assertEqual(res['entry_kind'], 'existing')

        movement = StockMovement.objects.get(pk=res['lines'][0]['movement_id'])
        self.assertTrue(_is_existing(movement), movement.metadata)
        self.assertEqual(movement.kardex_label, LABEL)
        self.assertTrue(movement.reason.startswith('Inventario existente'), movement.reason)
        # Cost and labels as any entry: the device keeps its cost, a label batch is ready to print.
        device = StockUnit.objects.get(serial_number='EXR-0002')
        self.assertEqual(device.cost, Decimal('3100.00'))
        self.assertTrue(LabelBatch.objects.filter(pk=res['lines'][0]['label_batch']['id']).exists())
        self.assertEqual(mismatches(company=self.company)['mismatches'], [])

    def test_a_quantity_line_on_stock_no_kardex_explains_is_accepted_as_existing(self):
        # The cable holds 100 units placed without any Kardex line: not an opening, but existing stock it is.
        body = self.body(self.qty(self.cable, 4, cost='9.00'), entry_kind='existing')
        res = self.confirm(self.costed, body, expect=201).json()
        movement = StockMovement.objects.get(pk=res['lines'][0]['movement_id'])
        self.assertTrue(_is_existing(movement), movement.metadata)
        self.assertEqual((movement.quantity, movement.unit_cost), (4, Decimal('9.00')))
        self.assertEqual(self.shelf(self.cable), 104)

    def test_imei_rules_still_hold_for_existing_stock(self):
        live = next(self._imeis)
        units.receive_units(branch=self.centro, product=self.phone, actor=self.costed, reason='Compra',
                            units=[{'serial_number': 'EXR-LIVE-1', 'imei': live}])
        before = self.snapshot()
        # The IMEI of a live device.
        same = self.body(self.dev(self.phone, unit('EXR-0101', live)), entry_kind='existing')
        self.assertFalse(self.preview(self.costed, same, expect=200).json()['ok'])
        self.confirm(self.costed, same, expect=400)
        # R1 — a serial is never another live device's IMEI.
        crossed = self.body(self.dev(self.laptop, unit(live)), entry_kind='existing')
        self.assertFalse(self.preview(self.costed, crossed, expect=200).json()['ok'])
        self.confirm(self.costed, crossed, expect=400)
        # A phone that requires an IMEI without one is refused too.
        missing = self.body(self.dev(self.phone, unit('EXR-0102')), entry_kind='existing')
        self.assertFalse(self.preview(self.costed, missing, expect=200).json()['ok'])
        self.assertEqual(self.snapshot(), before)

    def test_the_authority_is_a_purchase_s(self):
        body = self.body(self.qty(self.case, 2), entry_kind='existing')
        self.assertEqual(self.confirm(self.viewer, body).status_code, 403)
        self.assertEqual(self.confirm(self.clerk, body).status_code, 201)

    def test_another_company_cannot_receive_into_this_shelf(self):
        before = self.snapshot()
        res = self.confirm(self.outsider, self.body(self.qty(self.case, 2), entry_kind='existing'))
        self.assertIn(res.status_code, (403, 404), res.content)
        self.assertEqual(self.snapshot(), before)

    def test_an_unknown_kind_is_still_refused(self):
        res = self.preview(self.costed, self.body(self.qty(self.case, 1), entry_kind='existing_stock'))
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json().get('field'), 'entry_kind')


# ---------------------------------------------------------------------------
# 3. «Registrar equipos»
# ---------------------------------------------------------------------------

class RegisterExistingTest(UnitsBase):
    URL = '/api/admin/inventory/units/'

    def setUp(self):
        super().setUp()
        from rest_framework.test import APIClient

        self.api = APIClient()
        self.api.force_authenticate(user=self.staff)
        self._imeis = iter(IMEIS[40:60])

    def post(self, serial, imei_value=None, **extra):
        body = {'product_id': self.phone.pk, 'branch': self.branch_a.pk, 'reason': 'Equipos de la vitrina',
                'units': [{'serial_number': serial, 'imei': imei_value or next(self._imeis)}], **extra}
        return self.api.post(self.URL, body, format='json')

    def test_after_a_sale_opening_is_refused_and_existing_is_taken(self):
        self.assertEqual(self.post('EXU-0001').status_code, 201)
        inventory.record_sale_stock_movements(_paid_order(self.company, self.branch_a, self.phone), strict=True)

        refused = self.post('EXU-0002', entry_kind='opening')
        self.assertEqual(refused.status_code, 400, refused.content)
        self.assertEqual(refused.json().get('code'), receipt_services.OPENING_HAS_HISTORY)

        res = self.post('EXU-0003', entry_kind='existing')
        self.assertEqual(res.status_code, 201, res.content)
        device = StockUnit.objects.get(serial_number='EXU-0003')
        movement = StockMovement.objects.get(metadata__unit_ids__contains=[device.pk])
        self.assertTrue(_is_existing(movement), movement.metadata)
        self.assertTrue(device.label_code)
        self.assertTrue(res.json().get('label_batch'), res.json())
        self.assertEqual(mismatches(company=self.company)['mismatches'], [])

    def test_an_existing_device_is_voided_like_any_entry(self):
        self.post('EXU-0101', entry_kind='existing')
        device = StockUnit.objects.get(serial_number='EXU-0101')
        voided = units.void_unit(device, reason='Se digitó mal', reason_code='typo', actor=self.staff)
        self.assertEqual(voided.status, StockUnit.Status.VOIDED)
        self.assertEqual(inventory.branch_quantity(self.branch_a, self.phone), 0)
        self.assertEqual(mismatches(company=self.company)['mismatches'], [])

    def test_r1_still_holds(self):
        live = next(self._imeis)
        self.assertEqual(self.post('EXU-0201', live).status_code, 201)
        res = self.api.post(self.URL, {'product_id': self.laptop.pk, 'branch': self.branch_a.pk,
                                       'reason': 'Equipos de la vitrina', 'entry_kind': 'existing',
                                       'units': [{'serial_number': live}]}, format='json')
        self.assertEqual(res.status_code, 400, res.content)
        self.assertFalse(StockUnit.objects.filter(product=self.laptop).exists())

    def test_a_product_of_another_company_is_404(self):
        res = self.api.post(self.URL, {'product_id': self.foreign_phone.pk, 'branch': self.branch_a.pk,
                                       'reason': 'Vitrina', 'entry_kind': 'existing',
                                       'units': [{'serial_number': 'EXU-0301', 'imei': next(self._imeis)}]},
                            format='json')
        self.assertEqual(res.status_code, 404, res.content)
        self.assertFalse(StockUnit.objects.filter(serial_number='EXU-0301').exists())


# ---------------------------------------------------------------------------
# 4. The device file
# ---------------------------------------------------------------------------

class ImportExistingTest(LoadBase):
    def setUp(self):
        super().setUp()
        self._imeis = iter(IMEIS[60:80])

    def test_a_closed_shelf_takes_an_existing_file_counted_apart(self):
        self.apply(self.upload([self.phone_cells('EXI-0001'), self.phone_cells('EXI-0002')]))      # a purchase
        inventory.record_sale_stock_movements(self.paid_order(), strict=True)

        opening = self.upload([self.phone_cells('EXI-0101')], entry_kind='opening')
        self.assertEqual(opening['counts']['error'], 1)

        payload = self.upload([self.phone_cells('EXI-0102'), self.phone_cells('EXI-0103')], entry_kind='existing')
        self.assertEqual(payload['counts']['error'], 0, payload['rows'])
        simulation = payload['summary']['simulation']
        self.assertEqual(simulation['entry_kind'], 'existing')
        self.assertEqual(simulation['movements'], {'purchase_entry': 0, 'initial_stock': 0, 'existing_stock': 1})
        self.assertEqual(simulation['units'], {'purchase_entry': 0, 'initial_stock': 0, 'existing_stock': 2})

        applied = self.apply(payload)
        self.assertEqual(applied['summary']['applied']['movements'], 1)
        movement = StockMovement.objects.filter(product=self.phone, branch=self.branch_a).latest('pk')
        self.assertTrue(_is_existing(movement), movement.metadata)
        self.assertEqual(LabelBatch.objects.filter(source_key=label_batches.job_key(payload['id'], self.branch_a.pk)).count(), 1)
        self.assertEqual(mismatches(company=self.company)['mismatches'], [])

        # The whole load is annulled like any other.
        from store.models import BulkImportJob

        job, changed = unit_import_batch.void_job(BulkImportJob.objects.get(pk=payload['id']), reason='Archivo errado',
                                                  actor=self.staff)
        self.assertTrue(changed)
        self.assertEqual(StockUnit.objects.filter(serial_number__in=['EXI-0102', 'EXI-0103'],
                                                  status=StockUnit.Status.VOIDED).count(), 2)
        self.assertEqual(mismatches(company=self.company)['mismatches'], [])

    def test_a_purchase_file_counts_no_existing_stock(self):
        payload = self.upload([self.phone_cells('EXI-0201')])
        self.assertEqual(payload['summary']['simulation']['movements'],
                         {'purchase_entry': 1, 'initial_stock': 0, 'existing_stock': 0})

    def test_imei_rules_still_hold_in_an_existing_file(self):
        live = next(self._imeis)
        units.receive_units(branch=self.branch_a, product=self.phone, actor=self.staff, reason='Compra',
                            units=[{'serial_number': 'EXI-LIVE', 'imei': live}])
        payload = self.upload([self.phone_cells('EXI-0301', IMEI=live),
                               self.laptop_cells(live)], entry_kind='existing')
        self.assertEqual(payload['counts']['error'], 2, payload['rows'])

    def test_the_service_refuses_an_unknown_kind(self):
        with self.assertRaises(unit_import.UnitImportError):
            unit_import._entry_kind('existing_stock')

    def test_a_job_of_another_company_never_marks_this_one(self):
        self.apply(self.upload([self.phone_cells('EXI-0401')], entry_kind='existing'))
        self.assertFalse(StockMovement.objects.filter(company=self.other, metadata__entry_kind=MARK).exists())
        self.assertFalse(BranchStock.objects.filter(branch__company=self.other, product=self.phone).exists())
