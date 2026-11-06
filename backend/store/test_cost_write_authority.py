"""
COST-WRITE-AUTHORITY (decisión C del propietario) — quien no tiene autoridad
sobre los costos no los escribe por ninguna puerta.

    «Rechazar en servidor valores de costo enviados por usuarios sin
    autorización para gestionarlos. [...] Conservar recepción sin acceso a
    costos cuando esté permitida.»

La autoridad es `costs.view` («Ver costos y márgenes»): el sistema no tiene un
permiso aparte para gestionar costos, y es la misma que ya exigían la
recepción (`costs_forbidden`), la corrección del costo de un equipo y el costo
de referencia del producto.

  * «Registrar equipos» (`POST /api/admin/inventory/units/`): un costo no
    vacío sin `costs.view` → 400 `costs_forbidden`, sin escribir nada. Sin
    costo (vacío o ausente) se registra como siempre.
  * Cargas de Excel (equipos, stock, productos): en la PREVISUALIZACIÓN, una
    celda de costo llena sin `costs.view` es un error de esa fila — el mismo
    texto, diga lo que diga la celda — y una carga con errores no se aplica.
    Al APLICAR se vuelve a preguntar: un trabajo previsualizado con costos por
    quien sí puede no lo aplica con ellos quien no puede (400
    `costs_forbidden`, nada escrito, el trabajo sigue previsualizado).
  * Quien tiene `costs.view` sigue igual.
"""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase

from store import cost_services, import_services, stock_import_services, unit_import_services
from store import inventory_services as inventory
from store.models import BulkImportJob, Product, ProductBarcode, StockMovement, StockUnit
from store.test_cost_imports import _client, _Shop, upload
from store.test_stock_units import IMEI_A, IMEI_B, IMEI_C, UnitsBase

D = Decimal
UNITS = '/api/admin/inventory/units/'

#: A cost no id, code, serial or timestamp of a test run can contain, and every way a sheet or a sentence writes it.
SECRET_COST = '3711.37'
SECRET_FORMS = ('3711.37', '3,711.37', '3711,37', '3 711,37', '3711.3700')


def _texts(value):
    """Every text inside a JSON value (keys excluded): where an amount would travel."""
    if isinstance(value, dict):
        for item in value.values():
            yield from _texts(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _texts(item)
    elif value is not None:
        yield str(value)


def assert_cost_absent(test, *parts):
    """`SECRET_COST`, in none of its written forms, inside the given row data / errors / warnings / detail."""
    for text in (t for part in parts for t in _texts(part)):
        for form in SECRET_FORMS:
            test.assertNotIn(form, text)


# ---------------------------------------------------------------------------
# 1 · «Registrar equipos»
# ---------------------------------------------------------------------------

class RegisterUnitsCostAuthorityTest(UnitsBase):
    """`self.staff` holds the legacy inventory role: it moves stock, it does not hold `costs.view`."""

    def setUp(self):
        super().setUp()
        from rest_framework.test import APIClient

        self.api = APIClient()
        self.api.force_authenticate(user=self.staff)

    def post(self, *rows):
        return self.api.post(UNITS, {
            'product_id': self.phone.pk, 'branch': self.branch_a.pk, 'reason': 'Compra',
            'units': list(rows),
        }, format='json')

    def test_a_cost_without_costs_view_is_refused_and_nothing_is_written(self):
        movements = StockMovement.objects.count()
        res = self.post(self.row('SERIEC0001', IMEI_A, cost=SECRET_COST))
        self.assertEqual(res.status_code, 400, res.content)
        body = res.json()
        self.assertEqual(body['code'], 'costs_forbidden')
        self.assertEqual(body['field'], 'cost')
        self.assertIn('Ver costos y márgenes', body['detail'])
        assert_cost_absent(self, body)
        self.assertFalse(StockUnit.objects.filter(serial_number='SERIEC0001').exists())
        self.assertEqual(StockMovement.objects.count(), movements)
        self.assertEqual(self.quantity(), 0)

    def test_one_costed_row_refuses_the_whole_batch(self):
        res = self.post(self.row('SERIEC0002', IMEI_A), self.row('SERIEC0003', IMEI_B, cost='1'))
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json()['code'], 'costs_forbidden')
        self.assertFalse(StockUnit.objects.filter(serial_number__in=['SERIEC0002', 'SERIEC0003']).exists())

    def test_any_non_empty_cost_counts_even_zero_or_nonsense(self):
        for index, value in enumerate(['0', '-1', 'caro', 12]):
            res = self.post(self.row(f'SERIEC01{index}', IMEI_A, cost=value))
            self.assertEqual((res.status_code, res.json().get('code')), (400, 'costs_forbidden'), value)

    def test_receiving_without_a_cost_stays_possible(self):
        res = self.post(self.row('SERIEC0004', IMEI_A), self.row('SERIEC0005', IMEI_B, cost=''),
                        self.row('SERIEC0006', IMEI_C, cost=None))
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(
            list(StockUnit.objects.filter(serial_number__startswith='SERIEC000').values_list('cost', flat=True)),
            [None, None, None])
        self.assertEqual(self.quantity(), 3)

    def test_costs_view_still_registers_with_the_cost(self):
        from store.tests import _assign, _role

        _assign(self.membership, _role(self.company, 'Rol con costos', slug='rol-con-costos', capabilities=[
            'company.view', 'inventory.view', 'inventory.adjust', 'costs.view']))
        # Holding the authority, a malformed cost is still a malformed cost (the check the old
        # `refused(cost='-1')` of test_stock_units made for everybody).
        bad = self.post(self.row('SERIEC0009', IMEI_B, cost='-1'))
        self.assertEqual(bad.status_code, 400, bad.content)
        self.assertEqual(bad.json()['field'], 'cost')
        self.assertNotIn('code', bad.json())
        res = self.post(self.row('SERIEC0008', IMEI_A, cost='3700.00'))
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.json()['results'][0]['cost'], '3700.00')
        self.assertEqual(StockUnit.objects.get(serial_number='SERIEC0008').cost, D('3700.00'))


# ---------------------------------------------------------------------------
# 2 · Cargas de Excel
# ---------------------------------------------------------------------------

class _NoCostShop(_Shop):
    def setUp(self):
        super().setUp()
        # Moves stock and manages the catalogue, but does not hold `costs.view`.
        from store.tests import _p2d_member

        self.stocker, _ = _p2d_member(self.company, 'cwa_almacen', [
            'company.view', 'products.view', 'products.manage', 'inventory.view', 'inventory.adjust'])


class UnitImportCostAuthorityTest(_NoCostShop):
    HEADERS = ['Código', 'Producto', 'Sucursal', 'Número de serie', 'IMEI', 'IMEI 2', 'Condición', 'Costo',
               'Motivo / referencia']

    def setUp(self):
        super().setUp()
        from store.tests import _prod

        self.phone = _prod(self.company, 'iPhone 16', 'iphone-cwa', price='4500.00', inventory=0)
        Product.objects.filter(pk=self.phone.pk).update(is_serialized=True, requires_imei=True)
        ProductBarcode.objects.create(company=self.company, product=self.phone, code='IP16',
                                      symbology=ProductBarcode.INTERNAL)

    def file(self, cost, serial='SERCWA01', imei=IMEI_A):
        return upload(self.HEADERS, [['IP16', '', self.branch.name, serial, imei, '', 'Nuevo', cost, 'Compra']],
                      title='Equipos')

    def preview(self, cost, actor):
        return unit_import_services.preview_units(
            company=self.company, actor=actor, upload=self.file(cost), filename='equipos.xlsx',
            reachable_branch_ids=[self.branch.pk])

    def test_a_filled_cost_without_costs_view_is_a_row_error_and_the_file_does_not_apply(self):
        job = self.preview('3700', self.stocker)
        row = job.rows.get()
        self.assertEqual(row.action, 'error')
        self.assertIn(cost_services.COSTS_IN_FILE, row.errors)
        self.assertEqual(row.normalized_data.get('cost', ''), '')     # the value is not even staged
        with self.assertRaises(unit_import_services.UnitImportError):
            unit_import_services.apply_units(job=job, actor=self.stocker)
        self.assertFalse(StockUnit.objects.filter(serial_number='SERCWA01').exists())

    def test_whatever_the_cell_says_the_row_reads_the_same(self):
        shapes = {repr((row.action, row.errors, row.warnings)) for row in (
            self.preview(value, self.stocker).rows.get() for value in ['3700', '0', 'caro', '-1'])}
        self.assertEqual(len(shapes), 1, shapes)

    def test_a_blank_cost_column_loads_as_before(self):
        job = self.preview('', self.stocker)
        self.assertEqual(job.rows_error, 0, list(job.rows.values_list('errors', flat=True)))
        unit_import_services.apply_units(job=job, actor=self.stocker)
        self.assertIsNone(StockUnit.objects.get(serial_number='SERCWA01').cost)

    def test_the_preview_endpoint_says_why(self):
        res = _client(self.stocker).post('/api/admin/inventory/units/import/preview/', {
            'file': self.file(SECRET_COST),
        }, format='multipart')
        self.assertEqual(res.status_code, 201, res.content)
        [row] = res.json()['rows']
        self.assertIn(cost_services.COSTS_IN_FILE, row['errors'])
        assert_cost_absent(self, row['data'], row['errors'], row['warnings'], res.json().get('detail'))

    def test_a_job_previewed_with_costs_is_not_applied_by_somebody_without(self):
        job = self.preview('3700', self.owner)
        self.assertEqual(job.rows_error, 0)
        res = _client(self.stocker).post(f'/api/admin/inventory/units/import/{job.pk}/apply/', {}, format='json')
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json()['code'], 'costs_forbidden')
        self.assertFalse(StockUnit.objects.filter(serial_number='SERCWA01').exists())
        job.refresh_from_db()
        self.assertEqual(job.status, BulkImportJob.PREVIEWED)
        # Whoever holds the authority applies it, at the file's cost.
        unit_import_services.apply_units(job=job, actor=self.owner)
        self.assertEqual(StockUnit.objects.get(serial_number='SERCWA01').cost, D('3700.00'))


class StockImportCostAuthorityTest(_NoCostShop):
    HEADERS = ['ID', 'CODIGO', 'CODIGO EAN', 'NOMBRE', 'ALMACEN 1 - 11416', 'Costo unitario']

    def preview(self, cost, actor, quantity=10):
        return stock_import_services.preview_stock(
            company=self.company, actor=actor,
            upload=upload(self.HEADERS, [[1, 'FUN-001', '', 'Funda', quantity, cost]], title='Inventario'),
            filename='stock.xlsx', branch_map={4: self.branch.pk}, mode=BulkImportJob.MODE_RECONCILE,
        )

    def enter(self, quantity, cost):
        inventory.create_stock_movement(
            branch=self.branch, product_id=self.case.pk, movement_type=StockMovement.PURCHASE_ENTRY,
            quantity=quantity, reason='Compra', unit_cost=cost,
        )

    def test_a_filled_cost_without_costs_view_is_a_row_error_and_nothing_moves(self):
        self.enter(4, D('10'))
        lines = StockMovement.objects.count()
        job = self.preview('13', self.stocker)
        row = job.rows.get()
        self.assertEqual(row.action, 'error')
        self.assertIn(cost_services.COSTS_IN_FILE, row.errors)
        self.assertNotIn('unit_cost', row.normalized_data)
        with self.assertRaises(stock_import_services.StockImportError):
            stock_import_services.apply_stock(job=job, actor=self.stocker)
        self.assertEqual(StockMovement.objects.count(), lines)

    def test_a_decrease_with_a_cost_is_refused_too(self):
        self.enter(4, D('10'))
        row = self.preview('99', self.stocker, quantity=1).rows.get()
        self.assertIn(cost_services.COSTS_IN_FILE, row.errors)

    def test_whatever_the_cell_says_the_row_reads_the_same(self):
        self.enter(4, D('10'))
        shapes = {repr((row.action, row.errors, row.warnings)) for row in (
            self.preview(value, self.stocker).rows.get() for value in ['13', '10', '0', 'gratis'])}
        self.assertEqual(len(shapes), 1, shapes)

    def test_a_blank_cost_column_loads_as_before(self):
        self.enter(4, D('10'))
        job = self.preview('', self.stocker)
        self.assertEqual(job.rows_error, 0)
        stock_import_services.apply_stock(job=job, actor=self.stocker)
        line = StockMovement.objects.filter(product=self.case).order_by('-pk').first()
        self.assertEqual((line.quantity, line.unit_cost), (6, None))

    def test_a_job_previewed_with_costs_is_not_applied_by_somebody_without(self):
        self.enter(4, D('10'))
        job = self.preview('13', self.owner)
        self.assertEqual(job.rows_error, 0)
        lines = StockMovement.objects.count()
        res = _client(self.stocker).post(f'/api/admin/inventory/import/{job.pk}/apply/', {}, format='json')
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json()['code'], 'costs_forbidden')
        self.assertEqual(StockMovement.objects.count(), lines)
        job.refresh_from_db()
        self.assertEqual(job.status, BulkImportJob.PREVIEWED)


class ProductImportCostAuthorityTest(_NoCostShop):
    HEADERS = ['Código', 'Nombre', 'Precio de venta', 'Costo de referencia']

    def preview(self, rows, actor):
        return import_services.preview_products(
            company=self.company, actor=actor, upload=upload(self.HEADERS, rows), filename='carga.xlsx',
        )

    def test_a_filled_reference_cost_without_costs_view_is_a_row_error(self):
        Product.objects.filter(pk=self.case.pk).update(reference_cost=D('20.00'))
        job = self.preview([['FUN-001', 'Funda', '59.90', '35.00'], ['NEW-777', 'Cable', '20.00', '9.00']],
                           self.stocker)
        for row in job.rows.all():
            self.assertEqual(row.action, 'error')
            self.assertIn(cost_services.COSTS_IN_FILE, row.errors)
            self.assertNotIn('reference_cost', row.normalized_data)
        with self.assertRaises(import_services.ImportError_):
            import_services.apply_products(job=job, actor=self.stocker)
        self.case.refresh_from_db()
        self.assertEqual(self.case.reference_cost, D('20.00'))
        self.assertFalse(Product.objects.filter(name='Cable').exists())

    def test_a_blank_reference_cost_column_loads_as_before(self):
        job = self.preview([['NEW-777', 'Cable', '20.00', '']], self.stocker)
        self.assertEqual(job.rows_error, 0)
        import_services.apply_products(job=job, actor=self.stocker)
        self.assertIsNone(Product.objects.get(name='Cable').reference_cost)

    def test_a_job_previewed_with_costs_is_not_applied_by_somebody_without(self):
        Product.objects.filter(pk=self.case.pk).update(reference_cost=D('20.00'))
        job = self.preview([['FUN-001', 'Funda', '59.90', '35.00']], self.owner)
        res = _client(self.stocker).post(f'/api/admin/products/import/{job.pk}/apply/', {}, format='json')
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json()['code'], 'costs_forbidden')
        self.case.refresh_from_db()
        self.assertEqual((self.case.reference_cost, self.case.price), (D('20.00'), D('59.90')))
        job.refresh_from_db()
        self.assertEqual(job.status, BulkImportJob.PREVIEWED)


class ReceiptStaysTheReferenceTest(TestCase):
    """The reception's own refusal is the model this decision extends: its code is the shared one."""

    def test_the_code_is_shared(self):
        from store import receipt_services

        self.assertEqual(cost_services.COSTS_FORBIDDEN, 'costs_forbidden')
        self.assertEqual(receipt_services.COSTS_REQUIRED, cost_services.COSTS_REQUIRED)


# ---------------------------------------------------------------------------
# 3 · Proveedores (revisión de #242): la misma regla, sin «se ignora»
# ---------------------------------------------------------------------------

from store import supply_services as supply  # noqa: E402
from store.models import SupplierOffer  # noqa: E402
from store.test_supplier_offers import OfferFile, SupplyShop, today  # noqa: E402
from store.test_supply_reception import IMEIS, ReceptionShop, as_client  # noqa: E402


class SupplierReceptionCostAuthorityTest(ReceptionShop, TestCase):
    """«Recibir del proveedor»: the reception without a cost stays; a cost from who cannot is refused."""

    def setUp(self):
        self.build('cwa-recibe')

    def test_a_line_cost_without_costs_view_is_refused_and_nothing_moves(self):
        order = self.paid_order(self.case, 1)
        lines = StockMovement.objects.count()
        res = as_client(self.no_costs).post(self.url(order), {'unit_cost': '1.00'}, format='json')
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual((res.json()['code'], res.json()['field']), ('costs_forbidden', 'unit_cost'))
        self.assertEqual(order.items.get().supply_status, 'pending')
        self.assertEqual(StockMovement.objects.count(), lines)

    def test_a_device_cost_without_costs_view_is_refused_and_no_device_enters(self):
        order = self.paid_order(self.phone, 1)
        res = as_client(self.no_costs).post(self.url(order), {'units': [
            {'serial_number': 'CWA0201', 'imei': IMEIS[12], 'cost': '1.00'}]}, format='json')
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json()['code'], 'costs_forbidden')
        self.assertFalse(StockUnit.objects.filter(serial_number='CWA0201').exists())
        self.assertEqual(order.items.get().supply_status, 'pending')

    def test_the_service_refuses_it_too(self):
        order = self.paid_order(self.case, 1)
        with self.assertRaises(supply.SupplyError) as refused:
            supply.receive_line(order.items.get(), actor=self.no_costs, unit_cost='1.00')
        self.assertEqual(refused.exception.code, 'costs_forbidden')

    def test_receiving_without_a_cost_still_enters_at_the_agreed_one(self):
        order = self.paid_order(self.case, 1)
        res = as_client(self.no_costs).post(self.url(order), {'unit_cost': '  '}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        entry = StockMovement.objects.get(order=order, movement_type='purchase_entry')
        self.assertEqual(entry.unit_cost, D('70.0000'))

    def test_recording_the_real_cost_answers_the_shared_code(self):
        order = self.paid_order(self.case, 1)
        supply.receive_line(order.items.get(), actor=self.no_costs)
        res = as_client(self.no_costs).post(self.url(order, 'real-cost'), {'unit_cost': '65'}, format='json')
        self.assertEqual(res.status_code, 403, res.content)
        self.assertEqual(res.json()['code'], 'costs_forbidden')


class SupplierOfferCostAuthorityTest(SupplyShop, TestCase):
    def setUp(self):
        self.build('cwa-oferta')
        from store.tests import _p2d_member

        self.manager, _ = _p2d_member(self.company, 'cwa_oferta_sin_costos',
                                      ['company.view', 'products.view', 'products.manage'])

    def fields(self, **over):
        values = dict(supplier_id=self.supplier.pk, product_id=self.case.pk, commercial_price='100.00',
                      confirmed_quantity=1, valid_from=today() + timedelta(days=30),
                      valid_until=today() + timedelta(days=31), lead_time_days=2)
        values.update(over)
        return values

    def test_creating_an_offer_with_a_cost_without_costs_view_is_refused(self):
        before = SupplierOffer.objects.count()
        with self.assertRaises(supply.SupplyError) as refused:
            supply.create_offer(company=self.company, actor=self.manager, may_view_costs=False,
                                **self.fields(agreed_cost='50.00'))
        self.assertEqual((refused.exception.code, refused.exception.field), ('costs_forbidden', 'agreed_cost'))
        self.assertEqual(SupplierOffer.objects.count(), before)

    def test_editing_the_cost_without_costs_view_is_refused_and_it_stays(self):
        with self.assertRaises(supply.SupplyError) as refused:
            supply.update_offer(self.offer, actor=self.manager, may_view_costs=False, agreed_cost='1.00')
        self.assertEqual(refused.exception.code, 'costs_forbidden')
        self.offer.refresh_from_db()
        self.assertEqual(self.offer.agreed_cost, D('70.00'))

    def test_without_a_cost_the_offer_is_created_as_always(self):
        offer = supply.create_offer(company=self.company, actor=self.manager, may_view_costs=False,
                                    **self.fields(agreed_cost=''))
        self.assertIsNone(offer.agreed_cost)

    def test_the_api_answers_400_costs_forbidden(self):
        res = as_client(self.manager).post('/api/admin/supplier-offers/', self.fields(agreed_cost='50.00'),
                                           format='json')
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json()['code'], 'costs_forbidden')
        res = as_client(self.manager).patch(f'/api/admin/supplier-offers/{self.offer.pk}/', {'agreed_cost': '1'},
                                            format='json')
        self.assertEqual((res.status_code, res.json()['code']), (400, 'costs_forbidden'))


class SupplierOfferImportCostAuthorityTest(OfferFile, TestCase):
    def setUp(self):
        self.build_excel('cwa-semanal')
        from store.tests import _p2d_member

        manager, _ = _p2d_member(self.company, 'cwa_semanal_sin_costos',
                                 ['company.view', 'products.view', 'products.manage'])
        self.blind = as_client(manager)
        SupplierOffer.objects.filter(pk=self.offer.pk).delete()

    def test_a_filled_cost_is_a_row_error_and_the_file_does_not_apply(self):
        body = self.preview([self.row(**{'Costo pactado': SECRET_COST})], client=self.blind).json()
        [row] = body['rows']
        self.assertEqual(row['action'], 'error')
        self.assertIn(cost_services.COSTS_IN_FILE, row['errors'])
        assert_cost_absent(self, row['data'], row['errors'], row['warnings'], body.get('detail'))
        self.assertEqual(self.apply(body['id'], client=self.blind).status_code, 400)
        self.assertFalse(SupplierOffer.objects.filter(supplier=self.supplier).exists())

    def test_whatever_the_cell_says_the_row_reads_the_same(self):
        shapes = set()
        for value in ['68', '0', 'caro']:
            [row] = self.preview([self.row(**{'Costo pactado': value})], client=self.blind).json()['rows']
            shapes.add(repr((row['action'], row['errors'])))
        self.assertEqual(len(shapes), 1, shapes)

    def test_an_empty_cost_column_loads_as_before(self):
        body = self.preview([self.row(**{'Costo pactado': ''})], client=self.blind).json()
        self.assertEqual(body['rows_error'], 0, body['rows'])
        self.assertEqual(self.apply(body['id'], client=self.blind).status_code, 200)
        self.assertIsNone(SupplierOffer.objects.get(supplier=self.supplier).agreed_cost)

    def test_a_job_previewed_with_costs_is_not_applied_by_somebody_without(self):
        body = self.preview([self.row()]).json()          # staff holds costs.view
        self.assertEqual(body['rows_error'], 0)
        res = self.apply(body['id'], client=self.blind)
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json()['code'], 'costs_forbidden')
        self.assertFalse(SupplierOffer.objects.filter(supplier=self.supplier).exists())
        self.assertEqual(BulkImportJob.objects.get(pk=body['id']).status, BulkImportJob.PREVIEWED)


class TextDraftCostAuthorityTest(TestCase):
    """The pasted supplier list: a cost in the draft from who cannot state one is refused, not dropped."""

    def test_clean_refuses_a_cost_without_costs_view(self):
        from store import text_preview_draft

        with self.assertRaises(text_preview_draft.CostsForbidden):
            text_preview_draft.clean(None, {'supplier_id': 1, 'rows': [
                {'key': 'a', 'product_id': 1, 'agreed_cost': '99'}]}, with_cost=False)


# ---------------------------------------------------------------------------
# 4 · «Costo histórico», códigos y el importe en blanco
# ---------------------------------------------------------------------------

from store.test_label_migration_import import HEADERS as HISTORY_HEADERS, _Base as _HistoryBase  # noqa: E402
from store.test_label_migration_import import row as history_row, workbook as history_workbook  # noqa: E402


class HistoricalCostAuthorityTest(_HistoryBase):
    def test_a_filled_historical_cost_without_costs_view_is_the_same_row_error(self):
        data = history_workbook([[*history_row(code='CWA-01', name='Con costo'), '12.50']],
                                headers=[*HISTORY_HEADERS, 'Costo histórico'])
        body = self.ok_preview(data)
        line = self.by_row(body)[2]
        self.assertEqual(line['action'], 'error')
        self.assertIn(cost_services.COSTS_IN_FILE, line['errors'])
        self.assertNotIn('historical_cost', line['data'])


class CostRefusalCodeTest(UnitsBase):
    """Every cost refusal names `costs_forbidden`, whatever its HTTP status (400 new doors, 403 the older ones)."""

    def setUp(self):
        super().setUp()
        from rest_framework.test import APIClient

        self.api = APIClient()
        self.api.force_authenticate(user=self.staff)
        self.unit, = self.receive(self.row('SERCODE01', IMEI_A))

    def test_the_unit_cost_correction_answers_the_shared_code(self):
        res = self.api.patch(f'{UNITS}{self.unit.pk}/', {'cost': '1.00'}, format='json')
        self.assertEqual((res.status_code, res.json()['code']), (403, 'costs_forbidden'))

    def test_the_reference_cost_answers_the_shared_code(self):
        from store.tests import _assign, _role

        _assign(self.membership, _role(self.company, 'Rol catálogo', slug='rol-catalogo-cwa', capabilities=[
            'company.view', 'products.view', 'products.manage', 'inventory.view', 'inventory.adjust']))
        res = self.api.patch(f'/api/admin/products/{self.phone.pk}/', {'reference_cost': '1.00'}, format='json')
        self.assertEqual((res.status_code, res.json()['code']), (403, 'costs_forbidden'))

    def test_the_price_refusal_of_registrar_equipos_names_its_code(self):
        res = self.api.post(UNITS, {'product_id': self.phone.pk, 'branch': self.branch_a.pk, 'reason': 'Compra',
                                    'units': [self.row('SERCODE02', IMEI_B, sale_price='10')]}, format='json')
        self.assertEqual((res.status_code, res.json()['code']), (403, 'prices_forbidden'))

    def test_a_blank_cost_is_no_cost(self):
        res = self.api.post(UNITS, {'product_id': self.phone.pk, 'branch': self.branch_a.pk, 'reason': 'Compra',
                                    'units': [self.row('SERCODE03', IMEI_B, cost='   ')]}, format='json')
        self.assertEqual(res.status_code, 201, res.content)
        self.assertIsNone(StockUnit.objects.get(serial_number='SERCODE03').cost)
