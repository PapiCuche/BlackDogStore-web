"""
CATALOG-REFERENCE-01 · PR F — venta rápida de equipos en caja (ADR §4.5, D-F-1/2/3).

Lo que estas pruebas fijan:

  * la caja usa el MISMO catálogo: modelo → capacidad → color → SIM/región, sólo combinaciones de la paleta
    menos las exclusiones; cada variante existente trae su stock propio en la sucursal de la caja y la
    disponibilidad bajo pedido (nunca el stock del proveedor como propio); sin `costs.view` no viaja un costo;
  * «Llegó el equipo» compone servicios existentes: la variante se encuentra o se crea (habilitada para la
    tienda física, NUNCA publicada en la web, y sólo con un equipo real recibido en la misma operación), el
    equipo entra por `receive_units` (IMEI 1, IMEI 2, serie, proveedor, costo con permiso) con su Kardex, y
    la venta de caja lo nombra (SERIAL-PICK) como a cualquier otro equipo;
  * una combinación excluida o inexistente se rechaza sin escribir nada; un IMEI repetido en la empresa es
    409 y dice QUÉ equipo lo tiene; un IMEI 2 nunca es una segunda SIM física;
  * idempotente con `Idempotency-Key` (misma clave y cuerpo = misma respuesta, sin duplicar equipos ni
    movimientos); dos recepciones del mismo IMEI a la vez: gana una;
  * permisos: sin «Mover inventario» se vende lo que hay y se pide a un usuario autorizado recibir; crear una
    variante exige además «Administrar productos»; el costo exige `costs.view`; otra empresa = 404.

Los nombres de prueba son inventados (AGENTS §2).
"""
from decimal import Decimal as D
from threading import Barrier, Thread

from django.core.cache import cache
from django.db import connection
from django.test import TestCase, TransactionTestCase
from rest_framework.test import APIClient

from store import device_catalog, model_palette
from store import inventory_services as inventory
from store import stock_unit_services as units
from store import supply_services as supply
from store.models import (
    AdminAuditLog, BranchStock, Order, OrderItem, OrderUnit, Product, StockMovement, StockUnit,
)
from store.test_device_commerce import assert_no_amount
from store.test_stock_units import imei
from store.tests import _c1_sale, _p2d_member, _p3_company, _p3_order, _prod, _saas_branch

IMEIS = [imei(f'35209811{n:06d}') for n in range(60)]

MANAGER_CAPS = ['company.view', 'sales.pos.use', 'products.view', 'products.manage', 'inventory.view',
                'inventory.adjust', 'costs.view', 'sales.orders.view', 'sales.orders.manage']
RECEIVER_CAPS = ['company.view', 'sales.pos.use', 'products.view', 'inventory.view', 'inventory.adjust']
SELLER_CAPS = ['company.view', 'sales.pos.use']


def _api(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


class QuickShop:
    """A shop whose model «Teléfono Q» has a full palette and one impossible SIM × region pair."""

    def build(self, slug='prf-tienda'):
        cache.clear()
        self.company = _p3_company(slug, 'Tienda PR F')
        self.branch = self.company.default_inventory_branch
        self.north = _saas_branch(self.company, 'Sucursal Norte')
        self.manager, _ = _p2d_member(self.company, f'{slug}_jefe', MANAGER_CAPS)
        self.receiver, _ = _p2d_member(self.company, f'{slug}_almacen', RECEIVER_CAPS)
        self.seller, _ = _p2d_member(self.company, f'{slug}_vendedor', SELLER_CAPS)
        c = self.company
        self.capacity = device_catalog.create_attribute(company=c, name='Capacidad', role='capacity', sort_order=0)
        self.colour = device_catalog.create_attribute(company=c, name='Color', kind='color', role='color',
                                                      sort_order=1)
        self.sim = device_catalog.create_attribute(company=c, name='Tipo de SIM', role='sim', sort_order=2)
        self.region = device_catalog.create_attribute(company=c, name='Región', role='region', sort_order=3)
        opt = device_catalog.create_option
        self.gb128 = opt(company=c, attribute_id=self.capacity.pk, label='128 GB', sort_order=0)
        self.gb256 = opt(company=c, attribute_id=self.capacity.pk, label='256 GB', sort_order=1)
        self.black = opt(company=c, attribute_id=self.colour.pk, label='Negro', swatch_hex='#111111', sort_order=0)
        self.blue = opt(company=c, attribute_id=self.colour.pk, label='Azul', swatch_hex='#1d4ed8', sort_order=1)
        self.red = opt(company=c, attribute_id=self.colour.pk, label='Rojo', swatch_hex='#b91c1c', sort_order=2)
        self.esim = opt(company=c, attribute_id=self.sim.pk, label='eSIM', sort_order=0)
        self.chip = opt(company=c, attribute_id=self.sim.pk, label='Chip', sort_order=1)
        self.lla = opt(company=c, attribute_id=self.region.pk, label='LL/A', sort_order=0)
        self.ja = opt(company=c, attribute_id=self.region.pk, label='J/A', sort_order=1)
        self.model = device_catalog.create_model(company=c, name='Teléfono Q')
        model_palette.set_palette(self.model, option_ids=[o.pk for o in (
            self.gb128, self.gb256, self.black, self.blue, self.esim, self.chip, self.lla, self.ja)],
            actor=self.manager)
        model_palette.add_exclusions(self.model, pairs=[(self.chip.pk, self.lla.pk)], actor=self.manager)
        # One variant that exists: 128 GB · Negro · eSIM · LL/A, with two devices here and one in the north.
        self.existing = _prod(c, 'Teléfono Q 128 GB Negro eSIM LL/A', f'{slug}-q-128', price='3000.00',
                              inventory=0)
        Product.objects.filter(pk=self.existing.pk).update(is_serialized=True, requires_imei=True,
                                                            reference_cost=D('2400.00'))
        self.existing = device_catalog.set_variant(
            Product.objects.get(pk=self.existing.pk), model_id=self.model.pk, condition_line='new',
            option_ids=[self.gb128.pk, self.black.pk, self.esim.pk, self.lla.pk])
        self.supplier = supply.create_supplier(company=c, name='Mayorista Q', actor=self.manager)
        self._imei = iter(IMEIS)
        self.here = units.receive_units(branch=self.branch, product=self.existing, actor=self.manager,
                                        reason='Compra', units=[self.row(), self.row()])
        units.receive_units(branch=self.north, product=self.existing, actor=self.manager, reason='Compra',
                            units=[self.row()])
        self.q = f'?company={c.pk}&branch={self.branch.pk}'

    def row(self, **extra):
        value = next(self._imei)
        return {'serial_number': f'SQ{value[-8:]}', 'imei': value, **extra}

    def config_url(self, model=None):
        return f'/api/admin/pos/models/{(model or self.model).pk}/configurations/{self.q}'

    def receive_url(self):
        return f'/api/admin/pos/devices/receive/{self.q}'

    def body(self, options=None, **extra):
        options = options if options is not None else [self.gb256, self.blue, self.esim, self.ja]
        body = {'model': self.model.pk, 'option_ids': [o.pk for o in options], 'imei': next(self._imei),
                'price': '3900.00'}
        body.update(extra)
        return body

    def post(self, user, body, key='k-1'):
        headers = {'HTTP_IDEMPOTENCY_KEY': key} if key else {}
        return _api(user).post(self.receive_url(), body, format='json', **headers)

    def counts(self):
        return (Product.objects.filter(company=self.company).count(),
                StockUnit.objects.filter(company=self.company).count(),
                StockMovement.objects.filter(branch__company=self.company).count())

    def assert_invariant(self):
        for row in BranchStock.objects.filter(branch__company=self.company, product__is_serialized=True):
            available = StockUnit.objects.filter(branch=row.branch, product=row.product, status='available').count()
            self.assertEqual(row.quantity, available, f'{row.product.name} @ {row.branch.name}')


# ---------------------------------------------------------------------------
# What the till reads
# ---------------------------------------------------------------------------

class ConfigurationsTest(QuickShop, TestCase):
    def setUp(self):
        self.build('prf-config')

    def test_the_roles_come_in_reading_order_with_their_options_in_the_companys_order(self):
        res = _api(self.seller).get(self.config_url())
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        self.assertEqual([a['role'] for a in body['attributes']], ['capacity', 'color', 'sim', 'region'])
        colours = next(a for a in body['attributes'] if a['role'] == 'color')
        # Only the palette's colours (red is the company's but not this model's), with their swatches.
        self.assertEqual([(o['label'], o['swatch_hex']) for o in colours['options']],
                         [('Negro', '#111111'), ('Azul', '#1D4ED8')])
        self.assertTrue(all(a['varies'] for a in body['attributes']))

    def test_only_valid_combinations_are_offered_the_excluded_pair_never(self):
        body = _api(self.seller).get(self.config_url()).json()
        self.assertEqual(len(body['combinations']), 2 * 2 * 2 * 2 - 2 * 2)
        for combo in body['combinations']:
            self.assertFalse({self.chip.pk, self.lla.pk} <= set(combo['option_ids']), combo)
        self.assertEqual(body['excluded_pairs'], [sorted([self.chip.pk, self.lla.pk])])

    def test_an_existing_variant_carries_its_own_stock_at_this_branch_only(self):
        body = _api(self.seller).get(self.config_url()).json()
        key = device_catalog.variant_key([self.gb128.pk, self.black.pk, self.esim.pk, self.lla.pk])
        combo = next(c for c in body['combinations'] if c['key'] == key)
        variant = combo['variant']
        self.assertEqual((variant['product_id'], variant['available'], variant['price'], variant['sellable']),
                         (self.existing.pk, 2, '3000.00', True))
        self.assertIsNone(variant['on_order'])
        others = [c for c in body['combinations'] if c['key'] != key]
        self.assertTrue(all(c['variant'] is None for c in others))

    def test_on_order_says_lead_time_and_price_never_the_suppliers_quantity_as_own_stock(self):
        Product.objects.filter(pk=self.existing.pk).update(sale_mode=Product.SALE_MODE_STOCK_OR_ORDER)
        from django.utils import timezone
        from datetime import timedelta
        today = timezone.localdate()
        supply.create_offer(company=self.company, supplier_id=self.supplier.pk, product_id=self.existing.pk,
                            agreed_cost='2500.00', commercial_price='3100.00', confirmed_quantity=40,
                            valid_from=today, valid_until=today + timedelta(days=5), lead_time_days=3,
                            may_view_costs=True)
        body = _api(self.seller).get(self.config_url()).json()
        variant = next(c['variant'] for c in body['combinations'] if c['variant'])
        self.assertEqual(variant['available'], 2)
        self.assertEqual(variant['on_order'], {'lead_time_days': 3, 'price': '3100.00'})
        assert_no_amount(self, body, '2500', '40', '2400')
        self.assertNotIn('suppliers', body)

    def test_costs_travel_only_to_costs_view(self):
        seller = _api(self.seller).get(self.config_url()).json()
        assert_no_amount(self, seller, '2400')
        manager = _api(self.manager).get(self.config_url()).json()
        variant = next(c['variant'] for c in manager['combinations'] if c['variant'])
        self.assertEqual(variant['reference_cost'], '2400.00')

    def test_what_each_caller_may_do_is_said_before_the_form_opens(self):
        seller = _api(self.seller).get(self.config_url()).json()
        self.assertEqual(seller['can'], {'receive': False, 'create_variant': False, 'set_price': False,
                                         'view_costs': False, 'choose_supplier': False})
        self.assertNotIn('suppliers', seller)
        receiver = _api(self.receiver).get(self.config_url()).json()
        self.assertEqual(receiver['can'], {'receive': True, 'create_variant': False, 'set_price': False,
                                           'view_costs': False, 'choose_supplier': True})
        self.assertEqual(receiver['suppliers'], [{'id': self.supplier.pk, 'name': 'Mayorista Q'}])
        manager = _api(self.manager).get(self.config_url()).json()
        self.assertTrue(all(manager['can'].values()))

    def test_a_role_with_one_option_does_not_vary(self):
        model_palette.set_palette(self.model, option_ids=[o.pk for o in (
            self.gb128, self.gb256, self.black, self.blue, self.esim, self.lla)], actor=self.manager)
        body = _api(self.seller).get(self.config_url()).json()
        varies = {a['role']: a['varies'] for a in body['attributes']}
        self.assertEqual(varies, {'capacity': True, 'color': True, 'sim': False, 'region': False})
        self.assertNotIn('excluded_pairs', body)

    def test_a_model_of_another_company_is_404_and_so_is_one_that_does_not_exist(self):
        other = _p3_company('prf-config-b', 'Otra')
        foreign = device_catalog.create_model(company=other, name='Teléfono Q')
        for model_id in (foreign.pk, 999999):
            res = _api(self.manager).get(f'/api/admin/pos/models/{model_id}/configurations/{self.q}')
            self.assertEqual(res.status_code, 404, model_id)

    def test_without_the_till_capability_nothing_is_read(self):
        clerk, _ = _p2d_member(self.company, 'prf-config-sin-caja', ['company.view', 'products.view'])
        self.assertEqual(_api(clerk).get(self.config_url()).status_code, 403)

    def test_the_model_search_finds_models_with_their_stock_here(self):
        res = _api(self.seller).get(f'/api/admin/pos/models/{self.q}&q=fono')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['results'], [
            {'id': self.model.pk, 'name': 'Teléfono Q', 'has_palette': True, 'variants': 1, 'available': 2}])
        self.assertEqual(_api(self.seller).get(f'/api/admin/pos/models/{self.q}&q=x').json()['results'], [])


# ---------------------------------------------------------------------------
# «Llegó el equipo»
# ---------------------------------------------------------------------------

class ReceiveNewVariantTest(QuickShop, TestCase):
    def setUp(self):
        self.build('prf-nueva')

    def test_a_new_combination_creates_the_variant_with_its_device_in_one_operation(self):
        products, devices, movements = self.counts()
        body = self.body(imei2=next(self._imei), serial_number='F2QNEW0001', supplier=self.supplier.pk,
                         cost='3100.00')
        res = self.post(self.manager, body)
        self.assertEqual(res.status_code, 201, res.content)
        answer = res.json()
        product = Product.objects.get(pk=answer['product']['id'])
        # D-F-1: enabled for the shop, hidden from the web, of this model with exactly these options.
        self.assertEqual((product.draft_origin, product.is_pending_draft, product.is_published_online,
                          product.is_active, product.is_serialized, product.requires_imei, product.price),
                         ('', False, False, True, True, True, D('3900.00')))
        self.assertEqual(product.product_model, self.model)
        self.assertEqual(product.variant_key,
                         device_catalog.variant_key([self.gb256.pk, self.blue.pk, self.esim.pk, self.ja.pk]))
        self.assertTrue(answer['created_variant'])
        unit = StockUnit.objects.get(pk=answer['unit']['unit_id'])
        self.assertEqual((unit.imei, unit.imei2, unit.serial_number, unit.status, unit.branch, unit.cost,
                          unit.supplier), (body['imei'], body['imei2'], 'F2QNEW0001', 'available', self.branch,
                                           D('3100.00'), self.supplier))
        self.assertEqual(answer['method'], 'manual')
        self.assertEqual(answer['product']['available'], 1)
        self.assertEqual(self.counts(), (products + 1, devices + 1, movements + 1))
        movement = StockMovement.objects.get(product=product)
        self.assertEqual((movement.quantity, movement.metadata['unit_ids']), (1, [unit.pk]))
        trail = AdminAuditLog.objects.get(action='pos_device_received', company=self.company)
        self.assertEqual(trail.metadata['record']['unit_id'], unit.pk)
        self.assertNotIn(body['imei'], str(trail.metadata))
        self.assert_invariant()

    def test_an_esim_only_configuration_still_takes_a_second_imei(self):
        """IMEI 2 is a second identity of the modem, never a second physical SIM slot."""
        body = self.body(imei2=next(self._imei))
        res = self.post(self.manager, body)
        self.assertEqual(res.status_code, 201, res.content)
        unit = StockUnit.objects.get(pk=res.json()['unit']['unit_id'])
        self.assertEqual((unit.imei, unit.imei2), (body['imei'], body['imei2']))
        sim = [o.label for o in device_catalog.variant_options_of(unit.product) if o.attribute_id == self.sim.pk]
        self.assertEqual(sim, ['eSIM'])

    def test_an_existing_variant_receives_the_device_without_a_new_product(self):
        products, devices, movements = self.counts()
        res = self.post(self.manager, self.body([self.gb128, self.black, self.esim, self.lla], price=None))
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual((res.json()['product']['id'], res.json()['created_variant']), (self.existing.pk, False))
        self.assertEqual(self.counts(), (products, devices + 1, movements + 1))
        self.assertEqual(inventory.branch_quantity(self.branch, self.existing), 3)
        self.assertEqual(inventory.branch_quantity(self.north, self.existing), 1)
        self.existing.refresh_from_db()
        self.assertEqual(self.existing.price, D('3000.00'))
        self.assert_invariant()

    def test_a_new_variant_needs_a_price(self):
        before = self.counts()
        for price in (None, '', '0', '-5'):
            res = self.post(self.manager, self.body(price=price), key=f'p-{price}')
            self.assertEqual(res.status_code, 400, (price, res.content))
            self.assertEqual(res.json()['field'], 'price')
        self.assertEqual(self.counts(), before)


class RefusalsTest(QuickShop, TestCase):
    def setUp(self):
        self.build('prf-rechazos')

    def refused(self, res, status, code=None, field=None):
        self.assertEqual(res.status_code, status, res.content)
        if code:
            self.assertEqual(res.json()['code'], code, res.content)
        if field:
            self.assertEqual(res.json()['field'], field, res.content)

    def test_an_incompatible_sim_and_region_is_refused_and_nothing_is_written(self):
        before = self.counts()
        self.refused(self.post(self.manager, self.body([self.gb256, self.blue, self.chip, self.lla])), 400,
                     'combination_excluded')
        self.assertEqual(self.counts(), before)

    def test_a_combination_outside_the_palette_or_incomplete_is_refused(self):
        before = self.counts()
        self.refused(self.post(self.manager, self.body([self.gb256, self.red, self.esim, self.ja]), key='a'),
                     400, 'option_not_in_palette')
        self.refused(self.post(self.manager, self.body([self.gb256, self.blue, self.esim]), key='b'),
                     400, 'variant_incomplete')
        self.refused(self.post(self.manager, self.body([self.gb256, self.gb128, self.esim, self.ja]), key='c'),
                     400, 'invalid_options')
        self.assertEqual(self.counts(), before)

    def test_an_imei_that_fails_its_check_digit_is_refused_on_its_field(self):
        before = self.counts()
        bad = IMEIS[50][:-1] + str((int(IMEIS[50][-1]) + 1) % 10)
        self.refused(self.post(self.manager, self.body(imei=bad)), 400, field='imei')
        self.refused(self.post(self.manager, self.body(imei2='123'), key='k-2'), 400, field='imei2')
        self.assertEqual(self.counts(), before)

    def test_a_duplicate_imei_in_the_company_is_409_naming_the_device_that_has_it(self):
        holder = self.here[0]
        before = self.counts()
        for field, value in (('imei', holder.imei), ('imei2', holder.imei)):
            body = self.body([self.gb128, self.black, self.esim, self.lla])
            if field == 'imei2':
                body['imei2'] = value
            else:
                body['imei'] = value
            res = self.post(self.manager, body, key=f'dup-{field}')
            self.refused(res, 409, 'duplicate_imei', field)
            self.assertEqual(res.json()['unit']['id'], holder.pk)
            self.assertEqual(res.json()['unit']['label_code'], holder.label_code)
            self.assertEqual(res.json()['unit']['product_name'], self.existing.name)
        # Also when the device sits in another branch of the company.
        north = StockUnit.objects.get(branch=self.north)
        res = self.post(self.manager, self.body(imei=north.imei), key='dup-north')
        self.refused(res, 409, 'duplicate_imei')
        self.assertEqual(res.json()['unit']['id'], north.pk)
        self.assertEqual(self.counts(), before)

    def test_another_companys_model_supplier_or_option_answers_404(self):
        other = _p3_company('prf-rechazos-b', 'Otra')
        foreign_model = device_catalog.create_model(company=other, name='Ajeno')
        foreign_supplier = supply.create_supplier(company=other, name='Ajeno SAC')
        attr = device_catalog.create_attribute(company=other, name='Capacidad', role='capacity')
        foreign_option = device_catalog.create_option(company=other, attribute_id=attr.pk, label='64 GB')
        before = self.counts()
        self.refused(self.post(self.manager, self.body(model=foreign_model.pk), key='m'), 404)
        self.refused(self.post(self.manager, self.body(supplier=foreign_supplier.pk), key='s'), 404)
        body = self.body()
        body['option_ids'][0] = foreign_option.pk
        self.refused(self.post(self.manager, body, key='o'), 404)
        self.assertEqual(self.counts(), before)

    def test_a_used_line_or_a_bad_body_is_400_never_500(self):
        for key, body in (('l', self.body(line='used')), ('x', {'model': 'abc'}), ('y', []),
                          ('z', self.body(option_ids='1,2'))):
            res = self.post(self.manager, body, key=key)
            self.assertIn(res.status_code, (400,), (key, res.content))

    def test_a_pending_draft_variant_is_not_received_at_the_till(self):
        draft = device_catalog.create_variant_product(
            self.company, self.model, condition_line='new',
            option_ids=[self.gb256.pk, self.black.pk, self.esim.pk, self.ja.pk], actor=self.manager, request=None)
        before = self.counts()
        res = self.post(self.manager, self.body([self.gb256, self.black, self.esim, self.ja]))
        self.refused(res, 409, 'variant_pending_draft')
        self.assertEqual(self.counts(), before)
        draft.refresh_from_db()
        self.assertTrue(draft.is_pending_draft)


class IdempotencyTest(QuickShop, TestCase):
    def setUp(self):
        self.build('prf-idem')

    def test_the_same_key_and_body_give_the_same_answer_without_a_second_device(self):
        body = self.body(supplier=self.supplier.pk)
        first = self.post(self.manager, body, key='retry-1')
        self.assertEqual(first.status_code, 201, first.content)
        after = self.counts()
        again = self.post(self.manager, body, key='retry-1')
        self.assertEqual(again.status_code, 200, again.content)
        self.assertTrue(again.json()['replayed'])
        self.assertEqual(again.json()['unit']['unit_id'], first.json()['unit']['unit_id'])
        self.assertEqual(again.json()['product']['id'], first.json()['product']['id'])
        self.assertEqual(self.counts(), after)
        self.assertEqual(AdminAuditLog.objects.filter(action='pos_device_received').count(), 1)

    def test_the_same_key_with_another_body_is_409(self):
        self.assertEqual(self.post(self.manager, self.body(), key='retry-2').status_code, 201)
        after = self.counts()
        res = self.post(self.manager, self.body(), key='retry-2')
        self.assertEqual((res.status_code, res.json()['code']), (409, 'idempotency_conflict'))
        self.assertEqual(self.counts(), after)

    def test_a_refused_attempt_leaves_the_key_free(self):
        body = self.body([self.gb256, self.blue, self.chip, self.lla])
        self.assertEqual(self.post(self.manager, body, key='retry-3').status_code, 400)
        self.assertEqual(self.post(self.manager, self.body(), key='retry-3').status_code, 201)

    def test_without_a_key_nothing_is_written(self):
        before = self.counts()
        res = self.post(self.manager, self.body(), key=None)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(self.counts(), before)


class PermissionsTest(QuickShop, TestCase):
    def setUp(self):
        self.build('prf-permisos')

    def test_a_seller_without_the_inventory_capability_is_told_to_ask_an_authorised_user(self):
        before = self.counts()
        res = self.post(self.seller, self.body([self.gb128, self.black, self.esim, self.lla], price=None))
        self.assertEqual((res.status_code, res.json()['code']), (403, 'receive_forbidden'))
        self.assertIn('usuario autorizado', res.json()['detail'])
        self.assertEqual(self.counts(), before)

    def test_a_receiver_without_products_manage_receives_into_an_existing_variant_only(self):
        ok = self.post(self.receiver, self.body([self.gb128, self.black, self.esim, self.lla], price=None),
                       key='r-1')
        self.assertEqual(ok.status_code, 201, ok.content)
        before = self.counts()
        res = self.post(self.receiver, self.body(), key='r-2')
        self.assertEqual((res.status_code, res.json()['code']), (403, 'create_forbidden'))
        self.assertEqual(self.counts(), before)

    def test_a_cost_needs_costs_view_and_a_price_change_needs_products_manage(self):
        existing = [self.gb128, self.black, self.esim, self.lla]
        before = self.counts()
        res = self.post(self.receiver, self.body(existing, price=None, cost='2000.00'), key='c-1')
        self.assertEqual((res.status_code, res.json()['code']), (403, 'costs_forbidden'))
        res = self.post(self.receiver, self.body(existing, price='3200.00'), key='c-2')
        self.assertEqual((res.status_code, res.json()['code']), (403, 'price_forbidden'))
        self.assertEqual(self.counts(), before)
        # The same price as today is not a change.
        res = self.post(self.receiver, self.body(existing, price='3000.00'), key='c-3')
        self.assertEqual(res.status_code, 201, res.content)

    def test_a_manager_changes_the_price_and_it_is_audited(self):
        res = self.post(self.manager, self.body([self.gb128, self.black, self.esim, self.lla], price='3200.00'))
        self.assertEqual(res.status_code, 201, res.content)
        self.existing.refresh_from_db()
        self.assertEqual(self.existing.price, D('3200.00'))
        self.assertTrue(res.json()['price_changed'])
        self.assertTrue(AdminAuditLog.objects.filter(action='product_updated', company=self.company,
                                                     target_id=str(self.existing.pk)).exists())

    def test_the_answer_carries_no_cost_for_a_caller_without_costs_view(self):
        res = self.post(self.receiver, self.body([self.gb128, self.black, self.esim, self.lla], price=None))
        assert_no_amount(self, res.json(), '2400')
        self.assertNotIn('cost', res.json()['unit'])

    def test_a_branch_outside_the_callers_grants_is_refused(self):
        limited, _ = _p2d_member(self.company, 'prf-permisos-norte', MANAGER_CAPS, branches=[self.north])
        res = self.post(limited, self.body())
        self.assertEqual(res.status_code, 400, res.content)


# ---------------------------------------------------------------------------
# Into the sale
# ---------------------------------------------------------------------------

class IntoTheSaleTest(QuickShop, TestCase):
    def setUp(self):
        self.build('prf-venta')

    def test_the_received_device_is_sold_by_the_till_and_the_kardex_tells_both(self):
        res = self.post(self.manager, self.body(supplier=self.supplier.pk))
        self.assertEqual(res.status_code, 201, res.content)
        answer = res.json()
        product = Product.objects.get(pk=answer['product']['id'])
        unit = StockUnit.objects.get(pk=answer['unit']['unit_id'])
        order, created = _c1_sale(actor=self.seller, company=self.company, branch=self.branch, items=[
            {'product': product.pk, 'quantity': 1, 'units': [{'id': unit.pk, 'method': answer['method']}]}])
        self.assertTrue(created)
        unit.refresh_from_db()
        self.assertEqual((unit.status, unit.order_id), ('sold', order.pk))
        record = OrderUnit.objects.get(order=order)
        self.assertEqual((record.unit_id, record.method, record.identified_by), (unit.pk, 'manual', self.seller))
        kinds = list(StockMovement.objects.filter(product=product).order_by('pk').values_list(
            'movement_type', 'quantity'))
        self.assertEqual(len(kinds), 2)
        self.assertEqual(kinds[1], (StockMovement.SALE_EXIT, 1))
        self.assertEqual(inventory.branch_quantity(self.branch, product), 0)
        # The customer's paper is the store's: the order is of this company; the supplier stays on the unit.
        self.assertEqual(order.company, self.company)
        self.assertEqual(unit.supplier, self.supplier)
        self.assert_invariant()

    def test_no_stock_ever_appears_without_a_device_behind_it(self):
        self.post(self.manager, self.body(), key='a')
        self.post(self.manager, self.body([self.gb128, self.black, self.esim, self.lla], price=None), key='b')
        self.post(self.manager, self.body([self.gb256, self.blue, self.chip, self.lla]), key='c')   # refused
        self.assert_invariant()
        stocked = BranchStock.objects.filter(branch__company=self.company, quantity__gt=0)
        for row in stocked:
            self.assertTrue(row.product.is_serialized)

    def test_an_on_order_line_is_received_through_its_supplier_line(self):
        from datetime import timedelta
        from django.utils import timezone

        Product.objects.filter(pk=self.existing.pk).update(sale_mode=Product.SALE_MODE_STOCK_OR_ORDER)
        today = timezone.localdate()
        supply.create_offer(company=self.company, supplier_id=self.supplier.pk, product_id=self.existing.pk,
                            agreed_cost='2500.00', commercial_price='3100.00', confirmed_quantity=5,
                            valid_from=today, valid_until=today + timedelta(days=5), lead_time_days=3,
                            may_view_costs=True)
        order, _ = _c1_sale(actor=self.manager, company=self.company, branch=self.branch, may_order_over_stock=True,
                            items=[{'product': self.existing.pk, 'quantity': 1, 'on_order': True,
                                    'supply_reason': 'El cliente lo quiere en su caja sellada'}])
        line = order.items.get()
        before_shelf = inventory.branch_quantity(self.branch, self.existing)
        value = next(self._imei)
        res = self.post(self.manager, {'order_item': line.pk, 'imei': value, 'serial_number': 'F2QORD0001'},
                        key='ord-1')
        self.assertEqual(res.status_code, 201, res.content)
        line.refresh_from_db()
        self.assertEqual(line.supply_status, OrderItem.SUPPLY_RECEIVED)
        unit = StockUnit.objects.get(imei=value)
        self.assertEqual((unit.status, unit.order_id, unit.supplier), ('sold', order.pk, self.supplier))
        self.assertTrue(OrderUnit.objects.filter(order=order, unit=unit).exists())
        self.assertEqual(inventory.branch_quantity(self.branch, self.existing), before_shelf)
        again = self.post(self.manager, {'order_item': line.pk, 'imei': value, 'serial_number': 'F2QORD0001'},
                          key='ord-1')
        self.assertEqual((again.status_code, again.json()['replayed']), (200, True))
        self.assertEqual(StockUnit.objects.filter(imei=value).count(), 1)
        self.assert_invariant()

    def test_an_order_line_of_another_company_is_404(self):
        other = _p3_company('prf-venta-b', 'Otra')
        foreign = _p3_order(other)
        item = OrderItem.objects.create(order=foreign, product=_prod(other, 'X', 'prf-x', inventory=0),
                                        quantity=1, price=D('1'))
        res = self.post(self.manager, {'order_item': item.pk, 'imei': next(self._imei)})
        self.assertEqual(res.status_code, 404, res.content)


class UnitSupplierVisibilityTest(QuickShop, TestCase):
    def setUp(self):
        self.build('prf-proveedor')
        self.post(self.manager, self.body(supplier=self.supplier.pk))

    def test_the_units_list_shows_the_supplier_only_to_who_sees_suppliers_or_costs(self):
        url = f'/api/admin/inventory/units/?company={self.company.pk}'
        row = next(r for r in _api(self.manager).get(url).json()['results'] if r.get('supplier'))
        self.assertEqual(row['supplier'], {'id': self.supplier.pk, 'name': 'Mayorista Q'})
        stock_only, _ = _p2d_member(self.company, 'prf-solo-inventario', ['company.view', 'inventory.view'])
        rows = _api(stock_only).get(url).json()['results']
        self.assertTrue(rows)
        self.assertTrue(all('supplier' not in r for r in rows))

    def test_the_inventory_receipt_keeps_its_supplier_on_each_device_too(self):
        value = next(self._imei)
        body = {'branch': self.branch.pk, 'supplier': self.supplier.pk, 'document_ref': 'F001-77', 'notes': '',
                'lines': [{'kind': 'units', 'product': self.existing.pk,
                           'units': [{'serial_number': 'F2QRCP0001', 'imei': value}]}]}
        res = _api(self.manager).post(f'/api/admin/inventory/receipts/confirm/?company={self.company.pk}', body,
                                      format='json', HTTP_IDEMPOTENCY_KEY='rcp-prf-1')
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(StockUnit.objects.get(imei=value).supplier, self.supplier)

    def test_the_supplier_must_be_of_the_company_and_active(self):
        unit = StockUnit.objects.filter(supplier__isnull=False).first()
        self.assertEqual(unit.supplier.company_id, self.company.pk)
        other = _p3_company('prf-proveedor-b', 'Otra')
        foreign = supply.create_supplier(company=other, name='Ajeno')
        with self.assertRaises(units.StockUnitError):
            units.receive_units(branch=self.branch, product=self.existing, units=[self.row()], actor=self.manager,
                                reason='Compra', supplier=foreign)


# ---------------------------------------------------------------------------
# The internal v1 surface
# ---------------------------------------------------------------------------

class V1SurfaceTest(QuickShop, TestCase):
    def setUp(self):
        self.build('prf-v1')
        self.base = f'/api/v1/internal/{self.company.slug}/sales/pos'

    def test_configurations_and_receipt_answer_like_the_web_till(self):
        web = _api(self.manager).get(self.config_url()).json()
        v1 = _api(self.manager).get(f'{self.base}/models/{self.model.pk}/configurations/?branch={self.branch.pk}')
        self.assertEqual(v1.status_code, 200, v1.content)
        self.assertEqual(v1.json(), web)
        res = _api(self.manager).post(f'{self.base}/devices/receive/?branch={self.branch.pk}', self.body(),
                                      format='json', HTTP_IDEMPOTENCY_KEY='v1-1')
        self.assertEqual(res.status_code, 201, res.content)
        search = _api(self.manager).get(f'{self.base}/models/?branch={self.branch.pk}&q=Tel')
        self.assertEqual([r['id'] for r in search.json()['results']], [self.model.pk])

    def test_another_companys_slug_is_404(self):
        other = _p3_company('prf-v1-b', 'Otra')
        res = _api(self.manager).get(
            f'/api/v1/internal/{other.slug}/sales/pos/models/{self.model.pk}/configurations/?branch={self.branch.pk}')
        self.assertEqual(res.status_code, 404)
        res = _api(self.seller).post(f'{self.base}/devices/receive/?branch={self.branch.pk}', self.body(),
                                     format='json', HTTP_IDEMPOTENCY_KEY='v1-2')
        self.assertEqual(res.status_code, 403)


# ---------------------------------------------------------------------------
# Two receipts of one device at once
# ---------------------------------------------------------------------------

class SameImeiRaceTest(QuickShop, TransactionTestCase):
    """Two tills receive the same IMEI at the same instant: one device enters, the other is told which."""

    def setUp(self):
        self.build('prf-carrera')

    def test_one_wins_and_the_other_gets_409(self):
        if connection.vendor != 'postgresql':
            self.skipTest('needs PostgreSQL row and advisory locks')
        value = next(self._imei)
        barrier = Barrier(2)
        results = []

        def run(key):
            try:
                barrier.wait()
                body = self.body(imei=value)
                body['imei'] = value
                results.append(self.post(self.manager, body, key=key).status_code)
            finally:
                connection.close()

        threads = [Thread(target=run, args=(f'race-{n}',)) for n in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sorted(results), [201, 409], results)
        self.assertEqual(StockUnit.objects.filter(company=self.company).filter(imei=value).count(), 1)
        self.assert_invariant()


class NoVocabularyInQuickSaleTest(TestCase):
    """AGENTS §2 / ADR §2: PR F's engine names no maker, model or colour (the same guard as the importer's)."""

    def test_the_service_and_views_name_nothing(self):
        import re
        from pathlib import Path

        from store import pos_device_services, pos_device_views
        from store import test_catalog_reference_import as guard

        words = guard.NoVocabularyInTheEngineTest.WORDS
        strip = guard.NoVocabularyInTheEngineTest.code
        for module in (pos_device_services, pos_device_views):
            found = re.search(words, strip(None, Path(module.__file__).read_text(encoding='utf-8')), re.I)
            self.assertIsNone(found, (module.__name__, found))
