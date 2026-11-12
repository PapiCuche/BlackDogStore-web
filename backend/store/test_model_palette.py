"""
CATALOG-COMMERCIAL-01 · A1 — la paleta de opciones de cada modelo (ADR §5.7, §8, decisiones 1 y 2).

Lo que estas pruebas fijan:

  * un atributo tiene un ROL (capacidad, color, SIM, región u otro) por empresa; los nombres
    siguen siendo libres y un rol distinto de «otro» lo lleva un solo atributo de la empresa;
  * la paleta de un modelo (`ModelOption`) dice qué opciones admite; una variante de un modelo
    con paleta sólo lleva opciones de la paleta y una opción por cada atributo de la paleta;
  * dos variantes iguales (mismo modelo, misma línea, mismas opciones) se rechazan en el
    servicio y en la base, también cuando dos personas las crean a la vez;
  * lo que ya existe no se invalida hacia atrás: una variante con ventas, equipos o stock
    sigue siendo legible y vendible aunque la paleta cambie; se informa como «fuera_de_paleta»;
  * todo es por empresa: el modelo, la paleta o la opción de otra empresa responden 404;
  * la tienda pública no cambia: la respuesta de `/api/catalog/models/<slug>/` es la misma,
    campo por campo, con o sin paleta;
  * un cambio de catálogo nunca crea stock.

Nada aquí es de Apple ni de la tienda piloto: los nombres de prueba son inventados y los
valores salen de los datos de cada empresa de prueba.
"""
from decimal import Decimal as D
from threading import Barrier, Thread

from django.db import IntegrityError, connection, transaction
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from store import device_catalog, model_palette
from store.models import (
    AdminAuditLog, BranchStock, ModelOption, Order, OrderItem, Product, StockMovement, VariantAttribute,
)
from store.test_device_commerce import DeviceShop, _client
from store.tests import _p2d_member, _p3_company, _prod


class PaletteShop(DeviceShop):
    """`DeviceShop` plus SIM and region attributes with their roles, and a palette on the model."""

    def build(self, slug='a1-paleta'):
        super().build(slug)
        VariantAttribute.objects.filter(pk=self.capacity.pk).update(role='capacity')
        VariantAttribute.objects.filter(pk=self.colour.pk).update(role='color')
        self.sim = device_catalog.create_attribute(company=self.company, name='Tipo de SIM', role='sim', sort_order=2)
        self.region = device_catalog.create_attribute(company=self.company, name='Región', role='region', sort_order=3)
        self.esim = device_catalog.create_option(company=self.company, attribute_id=self.sim.pk, label='eSIM')
        self.chip = device_catalog.create_option(company=self.company, attribute_id=self.sim.pk, label='Chip')
        self.lla = device_catalog.create_option(company=self.company, attribute_id=self.region.pk, label='LL/A')
        self.ja = device_catalog.create_option(company=self.company, attribute_id=self.region.pk, label='J/A')
        self.red = device_catalog.create_option(company=self.company, attribute_id=self.colour.pk,
                                                label='Rojo', swatch_hex='#B91C1C')
        self.q = f'?company={self.company.pk}'
        self.api = _client(self.staff)

    def palette(self, *options, model=None):
        return model_palette.set_palette(model or self.model, option_ids=[o.pk for o in options], actor=self.staff)


class RoleTest(PaletteShop, TestCase):
    def setUp(self):
        self.build('a1-roles')

    def test_an_attribute_has_a_role_and_other_is_the_default(self):
        plain = device_catalog.create_attribute(company=self.company, name='Acabado')
        self.assertEqual(plain.role, VariantAttribute.ROLE_OTHER)
        self.assertEqual(self.sim.role, 'sim')
        with self.assertRaises(device_catalog.CatalogError) as caught:
            device_catalog.create_attribute(company=self.company, name='Raro', role='weight')
        self.assertEqual(caught.exception.field, 'role')

    def test_one_attribute_per_role_per_company_except_other(self):
        with self.assertRaises(device_catalog.CatalogError) as caught:
            device_catalog.create_attribute(company=self.company, name='Almacenamiento', role='capacity')
        self.assertEqual(caught.exception.code, 'role_taken')
        # «otro» se repite libremente…
        device_catalog.create_attribute(company=self.company, name='Acabado')
        device_catalog.create_attribute(company=self.company, name='Material')
        # …y la base lo impide aunque el servicio no esté.
        with self.assertRaises(IntegrityError), transaction.atomic():
            VariantAttribute.objects.create(company=self.company, name='Otra capacidad', role='capacity')
        # Otra empresa tiene su propio atributo de capacidad.
        other = _p3_company('a1-roles-b', 'Otra B')
        device_catalog.create_attribute(company=other, name='Capacidad', role='capacity')

    def test_the_role_changes_from_the_api_and_is_audited(self):
        extra = device_catalog.create_attribute(company=self.company, name='Acabado')
        res = self.api.patch(f'/api/admin/catalog/attributes/{extra.pk}/{self.q}', {'role': 'other', 'name': 'Terminado'},
                             format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual((res.json()['role'], res.json()['name']), ('other', 'Terminado'))
        res = self.api.patch(f'/api/admin/catalog/attributes/{extra.pk}/{self.q}', {'role': 'capacity'}, format='json')
        self.assertEqual((res.status_code, res.json()['code']), (400, 'role_taken'))
        self.assertTrue(AdminAuditLog.objects.filter(action='variant_attribute_updated', company=self.company,
                                                     target_id=str(extra.pk)).exists())

    def test_the_attribute_list_carries_the_role_and_creation_accepts_it(self):
        res = self.api.get(f'/api/admin/catalog/attributes/{self.q}')
        roles = {row['name']: row['role'] for row in res.json()['results']}
        self.assertEqual(roles, {'Capacidad': 'capacity', 'Color': 'color', 'Tipo de SIM': 'sim', 'Región': 'region'})
        res = self.api.post(f'/api/admin/catalog/attributes/{self.q}', {'name': 'Acabado', 'role': 'other'}, format='json')
        self.assertEqual((res.status_code, res.json()['role']), (201, 'other'))

    def test_another_companys_attribute_is_not_found_and_viewers_cannot_write(self):
        other = _p3_company('a1-roles-c', 'Otra C')
        foreign = device_catalog.create_attribute(company=other, name='Capacidad', role='capacity')
        res = self.api.patch(f'/api/admin/catalog/attributes/{foreign.pk}/{self.q}', {'role': 'other'}, format='json')
        self.assertEqual(res.status_code, 404)
        viewer, _ = _p2d_member(self.company, 'a1_viewer', ['company.view', 'products.view'])
        res = _client(viewer).patch(f'/api/admin/catalog/attributes/{self.sim.pk}/{self.q}', {'role': 'other'},
                                    format='json')
        self.assertEqual(res.status_code, 403)
        self.assertEqual(VariantAttribute.objects.get(pk=self.sim.pk).role, 'sim')


class PaletteWriteTest(PaletteShop, TestCase):
    def setUp(self):
        self.build('a1-paleta-w')

    def test_the_palette_is_a_set_of_the_companys_options(self):
        result = self.palette(self.gb128, self.gb256, self.black, self.esim, self.lla)
        self.assertEqual(result['added'], 5)
        self.assertEqual(set(ModelOption.objects.filter(model=self.model).values_list('option_id', flat=True)),
                         {self.gb128.pk, self.gb256.pk, self.black.pk, self.esim.pk, self.lla.pk})
        # Se reemplaza como conjunto: lo que falta se quita, lo nuevo entra, lo igual se queda.
        result = self.palette(self.gb128, self.blue, self.esim, self.lla)
        self.assertEqual((result['added'], result['removed']), (1, 2))
        self.assertEqual(ModelOption.objects.filter(model=self.model).count(), 4)
        self.assertTrue(AdminAuditLog.objects.filter(action='product_model_palette_set', company=self.company,
                                                     target_id=str(self.model.pk)).exists())

    def test_an_option_of_another_company_or_nonexistent_is_not_found(self):
        other = _p3_company('a1-paleta-b', 'Otra B')
        attr = device_catalog.create_attribute(company=other, name='Capacidad', role='capacity')
        foreign = device_catalog.create_option(company=other, attribute_id=attr.pk, label='128 GB')
        with self.assertRaises(device_catalog.CatalogError) as caught:
            self.palette(self.gb128, foreign)
        self.assertEqual(caught.exception.code, 'not_found')
        self.assertEqual(ModelOption.objects.filter(model=self.model).count(), 0)
        with self.assertRaises(device_catalog.CatalogError) as caught:
            model_palette.set_palette(self.model, option_ids=[self.gb128.pk, 999999], actor=self.staff)
        self.assertEqual(caught.exception.code, 'not_found')
        with self.assertRaises(device_catalog.CatalogError):
            model_palette.set_palette(self.model, option_ids=['x'], actor=self.staff)

    def test_the_database_refuses_a_palette_row_of_another_company(self):
        other = _p3_company('a1-paleta-c', 'Otra C')
        attr = device_catalog.create_attribute(company=other, name='Capacidad', role='capacity')
        foreign = device_catalog.create_option(company=other, attribute_id=attr.pk, label='128 GB')
        with self.assertRaises(ValueError):
            ModelOption.objects.create(company=self.company, model=self.model, option=foreign)
        with self.assertRaises(IntegrityError), transaction.atomic():
            ModelOption.objects.create(company=self.company, model=self.model, option=self.gb128)
            ModelOption.objects.create(company=self.company, model=self.model, option=self.gb128)

    def test_the_api_reads_and_writes_the_palette_with_the_right_permissions(self):
        url = f'/api/admin/catalog/models/{self.model.pk}/palette/{self.q}'
        res = self.api.get(url)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertFalse(res.json()['has_palette'])
        # Todos los atributos de la empresa, en el orden de sus roles, con cada opción y si está en la paleta.
        self.assertEqual([a['role'] for a in res.json()['attributes']], ['capacity', 'color', 'sim', 'region'])
        res = self.api.put(url, {'option_ids': [self.gb128.pk, self.black.pk, self.blue.pk]}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        self.assertTrue(body['has_palette'])
        colour = next(a for a in body['attributes'] if a['id'] == self.colour.pk)
        self.assertEqual({o['label']: o['in_palette'] for o in colour['options']},
                         {'Negro': True, 'Azul': True, 'Rojo': False})
        viewer, _ = _p2d_member(self.company, 'a1_viewer_w', ['company.view', 'products.view'])
        self.assertEqual(_client(viewer).get(url).status_code, 200)
        self.assertEqual(_client(viewer).put(url, {'option_ids': []}, format='json').status_code, 403)
        self.assertEqual(ModelOption.objects.filter(model=self.model).count(), 3)
        res = self.api.put(url, {'option_ids': 'no'}, format='json')
        self.assertEqual(res.status_code, 400)

    def test_another_companys_model_answers_404_even_with_its_id(self):
        other = _p3_company('a1-paleta-d', 'Otra D')
        theirs = device_catalog.create_model(company=other, name='Teléfono X')
        for method in ('get', 'put'):
            res = getattr(self.api, method)(f'/api/admin/catalog/models/{theirs.pk}/palette/{self.q}',
                                            {'option_ids': []}, format='json')
            self.assertEqual(res.status_code, 404, method)

    def test_a_palette_change_never_creates_stock(self):
        twin = _prod(self.company, 'Teléfono X 256 GB Azul', 'tx-256-azul', inventory=0)
        before = (BranchStock.objects.count(), StockMovement.objects.count(),
                  sum(BranchStock.objects.values_list('quantity', flat=True)))
        self.palette(self.gb128, self.gb256, self.black, self.blue)
        device_catalog.set_variant(twin, model_id=self.model.pk, option_ids=[self.gb256.pk, self.blue.pk])
        self.palette(self.gb128, self.black)
        after = (BranchStock.objects.count(), StockMovement.objects.count(),
                 sum(BranchStock.objects.values_list('quantity', flat=True)))
        self.assertEqual(before, after)


class VariantValidationTest(PaletteShop, TestCase):
    """Creating or editing a variant of a model WITH a palette."""

    def setUp(self):
        self.build('a1-valida')
        self.palette(self.gb128, self.gb256, self.black, self.blue, self.esim, self.chip, self.lla)

    def place(self, name, options, line='new', product=None):
        product = product or _prod(self.company, name, name.lower().replace(' ', '-'), inventory=0)
        return device_catalog.set_variant(product, model_id=self.model.pk, condition_line=line,
                                          option_ids=[o.pk for o in options], actor=self.staff)

    def test_a_combination_of_palette_options_is_accepted(self):
        product = self.place('Nuevo A', [self.gb128, self.blue, self.esim, self.lla])
        self.assertEqual(product.product_model_id, self.model.pk)
        self.assertEqual([o.label for o in device_catalog.variant_options_of(product)],
                         ['128 GB', 'Azul', 'eSIM', 'LL/A'])

    def test_an_option_outside_the_palette_is_refused(self):
        with self.assertRaises(device_catalog.CatalogError) as caught:
            self.place('Nuevo B', [self.gb128, self.red, self.esim, self.lla])        # Rojo no está en la paleta
        self.assertEqual((caught.exception.code, caught.exception.field), ('option_not_in_palette', 'options'))
        self.assertIn('Rojo', str(caught.exception))
        with self.assertRaises(device_catalog.CatalogError) as caught:
            self.place('Nuevo C', [self.gb128, self.blue, self.esim, self.ja])        # J/A tampoco
        self.assertEqual(caught.exception.code, 'option_not_in_palette')
        self.assertFalse(Product.objects.filter(name__in=['Nuevo B', 'Nuevo C'], product_model=self.model).exists())

    def test_every_attribute_of_the_palette_is_required(self):
        with self.assertRaises(device_catalog.CatalogError) as caught:
            self.place('Nuevo D', [self.gb128, self.blue])                              # sin SIM ni región
        self.assertEqual((caught.exception.code, caught.exception.field), ('variant_incomplete', 'options'))
        self.assertIn('Tipo de SIM', str(caught.exception))
        self.assertIn('Región', str(caught.exception))

    def test_a_nonexistent_option_is_still_not_found(self):
        product = _prod(self.company, 'Nuevo E', 'nuevo-e', inventory=0)
        with self.assertRaises(device_catalog.CatalogError) as caught:
            device_catalog.set_variant(product, model_id=self.model.pk, option_ids=[self.gb128.pk, 999999])
        self.assertEqual(caught.exception.code, 'not_found')

    def test_a_duplicate_combination_is_refused_with_its_own_code(self):
        self.place('Nuevo F', [self.gb128, self.blue, self.esim, self.lla])
        with self.assertRaises(device_catalog.CatalogError) as caught:
            self.place('Nuevo G', [self.lla, self.esim, self.blue, self.gb128])        # mismo conjunto, otro orden
        self.assertEqual(caught.exception.code, 'duplicate')
        # La misma combinación en la OTRA línea es otra variante.
        self.place('Nuevo H', [self.gb128, self.blue, self.esim, self.lla], line='used')

    def test_the_api_names_the_field_and_the_code(self):
        product = _prod(self.company, 'Nuevo I', 'nuevo-i', inventory=0)
        res = self.api.put(f'/api/admin/products/{product.pk}/variant/{self.q}', {
            'model_id': self.model.pk, 'condition_line': 'new',
            'option_ids': [self.gb128.pk, self.red.pk, self.esim.pk, self.lla.pk]}, format='json')
        self.assertEqual((res.status_code, res.json()['code'], res.json()['field']),
                         (400, 'option_not_in_palette', 'options'))

    def test_a_model_without_a_palette_accepts_any_option_of_the_company_as_before(self):
        free = device_catalog.create_model(company=self.company, name='Teléfono libre')
        product = _prod(self.company, 'Libre A', 'libre-a', inventory=0)
        product = device_catalog.set_variant(product, model_id=free.pk, option_ids=[self.gb128.pk, self.red.pk])
        self.assertEqual(product.product_model_id, free.pk)
        self.assertIsNone(model_palette.variant_status(free, [self.gb128.pk, self.red.pk]))

    def test_the_existing_variants_of_the_shop_keep_their_identity_when_only_the_price_changes(self):
        # `used128` lleva 128 GB + Azul, sin SIM ni región: está «incompleta» frente a la paleta, pero dejar su
        # identidad como está siempre se permite (sólo cambia el precio).
        res = self.api.put(f'/api/admin/products/{self.used128.pk}/variant/{self.q}', {
            'model_id': self.model.pk, 'condition_line': 'used',
            'option_ids': [self.gb128.pk, self.blue.pk], 'price': '1234.00'}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(Product.objects.get(pk=self.used128.pk).price, D('1234.00'))
        # Cambiarla a otra combinación sí pasa por la paleta.
        res = self.api.put(f'/api/admin/products/{self.used128.pk}/variant/{self.q}', {
            'model_id': self.model.pk, 'condition_line': 'used',
            'option_ids': [self.gb256.pk, self.blue.pk]}, format='json')
        self.assertEqual((res.status_code, res.json()['code']), (400, 'variant_incomplete'))


class DuplicateRaceTest(TransactionTestCase):
    """Two people save the same combination at the same instant: one wins, the other is told."""

    def test_only_one_product_becomes_the_variant(self):
        shop = PaletteShop()
        shop.build('a1-carrera')
        shop.palette(shop.gb128, shop.gb256, shop.black, shop.blue)
        first = _prod(shop.company, 'Carrera 1', 'carrera-1', inventory=0)
        second = _prod(shop.company, 'Carrera 2', 'carrera-2', inventory=0)
        barrier, outcomes = Barrier(2), []

        def save(product):
            try:
                barrier.wait(timeout=5)
                device_catalog.set_variant(Product.objects.get(pk=product.pk), model_id=shop.model.pk,
                                           option_ids=[shop.gb256.pk, shop.blue.pk])
                outcomes.append('saved')
            except device_catalog.CatalogError as exc:
                outcomes.append(exc.code)
            finally:
                connection.close()

        threads = [Thread(target=save, args=(p,)) for p in (first, second)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
        self.assertEqual(sorted(outcomes), ['duplicate', 'saved'])
        self.assertEqual(Product.objects.filter(product_model=shop.model, condition_line='new',
                                                variant_key=device_catalog.variant_key([shop.gb256.pk, shop.blue.pk]))
                         .count(), 1)


@override_settings(ALLOWED_HOSTS=['.tienda.pe', 'testserver'])
class HistoryPreservedTest(PaletteShop, TestCase):
    """A variant with sales, devices or stock survives any palette, readable and sellable."""

    def setUp(self):
        self.build('a1-historia')
        from store.tests import _m7_stock

        Product.objects.filter(pk=self.new128.pk).update(is_serialized=False, requires_imei=False)
        _m7_stock(self.branch, self.new128, 3)
        order = Order.objects.create(
            company=self.company, fulfillment_branch=self.branch, customer_email='c@example.com',
            total=self.new128.price, status=Order.Status.PAID, paid=True, paid_at=timezone.now(),
            fulfillment_status=Order.FulfillmentStatus.DELIVERED)
        OrderItem.objects.create(order=order, product=self.new128, quantity=1, price=self.new128.price)
        self.receive(self.new256, 1, cost='2500.00')
        self.public = _client(host=self.host)

    def test_a_palette_that_leaves_a_sold_variant_out_does_not_touch_it(self):
        # Negro ya no está en la paleta: new128 (128 GB Negro) y new256 (256 GB Negro) quedan fuera de ella.
        self.palette(self.gb128, self.gb256, self.blue, self.esim, self.lla)
        self.new128.refresh_from_db()
        self.assertEqual(self.new128.product_model_id, self.model.pk)
        self.assertEqual(self.new128.variant_key, device_catalog.variant_key([self.gb128.pk, self.black.pk]))
        self.assertEqual(OrderItem.objects.filter(product=self.new128).count(), 1)
        self.assertEqual(BranchStock.objects.get(branch=self.branch, product=self.new128).quantity, 3)
        # Se informa, no se corrige ni se borra.
        detail = self.api.get(f'/api/admin/catalog/models/{self.model.pk}/{self.q}').json()
        rows = {v['product_id']: v for v in detail['variants']}
        self.assertEqual(rows[self.new128.pk]['palette_status'], 'fuera_de_paleta')
        self.assertEqual(rows[self.new256.pk]['palette_status'], 'fuera_de_paleta')
        self.assertEqual(rows[self.used128.pk]['palette_status'], 'incompleta')        # Azul + 128 GB, sin SIM ni región
        # Y la tienda la sigue mostrando y vendiendo.
        body = self.public.get(f'/api/catalog/models/{self.model.slug}/').json()
        variants = {v['product_id']: v for v in body['variants']}
        self.assertTrue(variants[self.new128.pk]['available'])
        self.assertEqual(variants[self.new128.pk]['availability'], 'own_stock')

    def test_an_out_of_palette_variant_may_keep_its_identity_but_not_change_into_another_bad_one(self):
        self.palette(self.gb128, self.gb256, self.blue, self.esim, self.lla)
        boss, _ = _p2d_member(self.company, 'a1_jefe', ['company.view', 'company.manage', 'products.view',
                                                        'products.manage'])
        # Sólo precio: identidad intacta, permitido.
        res = _client(boss).put(f'/api/admin/products/{self.new128.pk}/variant/{self.q}', {
            'model_id': self.model.pk, 'condition_line': 'new',
            'option_ids': [self.gb128.pk, self.black.pk], 'price': '2999.00'}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        # Cambiarla a otra fuera de la paleta: no (aunque sea un superior con motivo).
        res = _client(boss).put(f'/api/admin/products/{self.new128.pk}/variant/{self.q}', {
            'model_id': self.model.pk, 'condition_line': 'new', 'reason': 'Corrección de color cargado mal',
            'option_ids': [self.gb128.pk, self.red.pk, self.esim.pk, self.lla.pk]}, format='json')
        self.assertEqual((res.status_code, res.json()['code']), (400, 'option_not_in_palette'))
        self.assertEqual(Product.objects.get(pk=self.new128.pk).variant_key,
                         device_catalog.variant_key([self.gb128.pk, self.black.pk]))


class StatusMatrixTest(PaletteShop, TestCase):
    """What the «Opciones de este modelo» screen shows: every combination of the palette and its state."""

    def setUp(self):
        self.build('a1-matriz')

    def test_combinations_and_their_statuses(self):
        self.palette(self.gb128, self.gb256, self.black, self.blue)
        # new128 = 128 GB Negro (existe); new256 = 256 GB Negro (existe); used128 = 128 GB Azul seminuevo (existe).
        # Una variante con una opción fuera de la paleta y otra incompleta, para ver sus estados.
        outside = _prod(self.company, 'Teléfono X 128 GB Rojo', 'tx-128-rojo', inventory=0)
        ModelOption.objects.create(company=self.company, model=self.model, option=self.red)
        device_catalog.set_variant(outside, model_id=self.model.pk, option_ids=[self.gb128.pk, self.red.pk])
        ModelOption.objects.filter(model=self.model, option=self.red).delete()
        half = _prod(self.company, 'Teléfono X 256 GB', 'tx-256', inventory=0)
        Product.objects.filter(pk=half.pk).update(product_model=self.model, variant_key=str(self.gb256.pk))
        from store.models import ProductVariantOption
        ProductVariantOption.objects.create(company=self.company, product=half, attribute=self.capacity, option=self.gb256)

        body = self.api.get(f'/api/admin/catalog/models/{self.model.pk}/palette/{self.q}').json()
        self.assertTrue(body['has_palette'])
        combos = {c['key']: c for c in body['combinations']}
        self.assertEqual(len(combos), 4)
        key = lambda *o: device_catalog.variant_key([x.pk for x in o])  # noqa: E731
        self.assertEqual(combos[key(self.gb128, self.black)]['status'], 'variante_existente')
        self.assertEqual([p['product_id'] for p in combos[key(self.gb128, self.black)]['products']], [self.new128.pk])
        self.assertEqual(combos[key(self.gb256, self.black)]['status'], 'variante_existente')
        self.assertEqual(combos[key(self.gb128, self.blue)]['status'], 'variante_existente')
        self.assertEqual(combos[key(self.gb128, self.blue)]['products'][0]['condition_line'], 'used')
        self.assertEqual(combos[key(self.gb256, self.blue)]['status'], 'combinacion_valida_sin_variante')
        self.assertEqual(combos[key(self.gb256, self.blue)]['products'], [])
        self.assertEqual(combos[key(self.gb128, self.black)]['labels'],
                         {str(self.capacity.pk): '128 GB', str(self.colour.pk): 'Negro'})
        statuses = {v['product_id']: v['status'] for v in body['variants']}
        self.assertEqual(statuses[outside.pk], 'fuera_de_paleta')
        self.assertEqual(statuses[half.pk], 'incompleta')
        self.assertEqual(statuses[self.new128.pk], 'variante_existente')
        self.assertEqual(body['summary'], {'combinations': 4, 'existing': 3, 'valid_without_variant': 1,
                                           'outside': 1, 'incomplete': 1})
        self.assertFalse(body['truncated'])

    def test_a_model_without_palette_has_no_combinations_and_no_statuses(self):
        body = self.api.get(f'/api/admin/catalog/models/{self.model.pk}/palette/{self.q}').json()
        self.assertFalse(body['has_palette'])
        self.assertEqual(body['combinations'], [])
        self.assertTrue(all(v['status'] is None for v in body['variants']))
        detail = self.api.get(f'/api/admin/catalog/models/{self.model.pk}/{self.q}').json()
        self.assertTrue(all(v['palette_status'] is None for v in detail['variants']))
        self.assertFalse(detail['has_palette'])

    def test_a_palette_with_too_many_combinations_is_cut_and_says_so(self):
        many = [device_catalog.create_option(company=self.company, attribute_id=self.capacity.pk, label=f'{n} GB')
                for n in range(1, 40)]
        colours = [device_catalog.create_option(company=self.company, attribute_id=self.colour.pk, label=f'Tono {n}',
                                                swatch_hex='#123456') for n in range(30)]
        model_palette.set_palette(self.model, option_ids=[o.pk for o in many + colours], actor=self.staff)
        body = self.api.get(f'/api/admin/catalog/models/{self.model.pk}/palette/{self.q}').json()
        self.assertTrue(body['truncated'])
        self.assertEqual(len(body['combinations']), model_palette.MAX_COMBINATIONS)
        self.assertEqual(body['summary']['combinations'], 39 * 30)


class TwoCompaniesTest(PaletteShop, TestCase):
    """The same names in two companies: each palette only sees its own options."""

    def setUp(self):
        self.build('a1-dos')
        self.other = _p3_company('a1-dos-b', 'Otra B')
        self.other_staff, _ = _p2d_member(self.other, 'a1_dos_b', ['company.view', 'products.view', 'products.manage'])
        self.b_capacity = device_catalog.create_attribute(company=self.other, name='Capacidad', role='capacity')
        self.b_colour = device_catalog.create_attribute(company=self.other, name='Color', kind='color', role='color')
        self.b_128 = device_catalog.create_option(company=self.other, attribute_id=self.b_capacity.pk, label='128 GB')
        self.b_black = device_catalog.create_option(company=self.other, attribute_id=self.b_colour.pk, label='Negro',
                                                    swatch_hex='#000000')
        self.b_model = device_catalog.create_model(company=self.other, name='Teléfono X')

    def test_palettes_do_not_cross(self):
        self.palette(self.gb128, self.black)
        model_palette.set_palette(self.b_model, option_ids=[self.b_128.pk, self.b_black.pk], actor=self.other_staff)
        # Empresa B no puede leer ni escribir la paleta de A, ni con su propio contexto.
        b_api = _client(self.other_staff)
        qb = f'?company={self.other.pk}'
        self.assertEqual(b_api.get(f'/api/admin/catalog/models/{self.model.pk}/palette/{qb}').status_code, 404)
        self.assertEqual(b_api.put(f'/api/admin/catalog/models/{self.model.pk}/palette/{qb}',
                                   {'option_ids': [self.b_128.pk]}, format='json').status_code, 404)
        # Las opciones de A, con la misma etiqueta, no entran en la paleta de B.
        res = b_api.put(f'/api/admin/catalog/models/{self.b_model.pk}/palette/{qb}',
                        {'option_ids': [self.b_128.pk, self.gb128.pk]}, format='json')
        self.assertEqual(res.status_code, 404)
        self.assertEqual(set(ModelOption.objects.filter(model=self.b_model).values_list('option_id', flat=True)),
                         {self.b_128.pk, self.b_black.pk})
        # Una variante de B con las opciones de B pasa; con las de A no existe.
        product = _prod(self.other, 'B 128 Negro', 'b-128-negro', inventory=0)
        device_catalog.set_variant(product, model_id=self.b_model.pk, option_ids=[self.b_128.pk, self.b_black.pk])
        with self.assertRaises(device_catalog.CatalogError) as caught:
            device_catalog.set_variant(product, model_id=self.b_model.pk, option_ids=[self.gb128.pk, self.b_black.pk])
        self.assertEqual(caught.exception.code, 'not_found')
        # La matriz de A sólo conoce productos de A.
        body = self.api.get(f'/api/admin/catalog/models/{self.model.pk}/palette/{self.q}').json()
        self.assertEqual({p['product_id'] for c in body['combinations'] for p in c['products']}, {self.new128.pk})


class ImportCompatibilityTest(PaletteShop, TestCase):
    """The spreadsheet importers resolve variants as before; a palette is a row error, never a silent write."""

    def setUp(self):
        self.build('a1-import')

    def test_without_a_palette_nothing_changes(self):
        spec = device_catalog.resolve_variant_spec(self.company, model_name='Teléfono X', labels=['128 GB', 'Rojo'])
        self.assertEqual(spec['option_ids'], sorted([self.gb128.pk, self.red.pk]))

    def test_with_a_palette_the_row_is_refused_before_anything_is_written(self):
        self.palette(self.gb128, self.gb256, self.black, self.blue)
        spec = device_catalog.resolve_variant_spec(self.company, model_name='Teléfono X', labels=['128 GB', 'Negro'])
        self.assertEqual(device_catalog.find_variant(self.company, spec).pk, self.new128.pk)
        with self.assertRaises(device_catalog.CatalogError) as caught:
            device_catalog.resolve_variant_spec(self.company, model_name='Teléfono X', labels=['128 GB', 'Rojo'])
        self.assertEqual(caught.exception.code, 'option_not_in_palette')
        with self.assertRaises(device_catalog.CatalogError) as caught:
            device_catalog.resolve_variant_spec(self.company, model_name='Teléfono X', labels=['128 GB'])
        self.assertEqual(caught.exception.code, 'variant_incomplete')

    def test_an_existing_variant_outside_the_palette_is_still_found_by_the_importers(self):
        # Lo que ya existe se encuentra aunque la paleta de hoy no lo admita: una oferta de proveedor o una
        # carga de equipos sobre esa variante sigue funcionando (no se retro-invalida).
        self.palette(self.gb128, self.gb256, self.blue)
        spec = device_catalog.resolve_variant_spec(self.company, model_name='Teléfono X', labels=['128 GB', 'Negro'])
        self.assertEqual(device_catalog.find_variant(self.company, spec).pk, self.new128.pk)


@override_settings(ALLOWED_HOSTS=['.tienda.pe', 'testserver'])
class PublicContractUnchangedTest(PaletteShop, TestCase):
    """The public page of a model is the same, field by field, with or without a palette."""

    def setUp(self):
        self.build('a1-publico')
        self.receive(self.new128, 1, cost='2500.00')
        self.public = _client(host=self.host)

    def detail(self):
        body = self.public.get(f'/api/catalog/models/{self.model.slug}/').json()
        body.pop('as_of')
        return body

    maxDiff = None

    def test_the_payload_is_byte_for_byte_the_known_shape(self):
        expected = {
            'slug': self.model.slug, 'name': 'Teléfono X', 'description': '', 'image_url': '',
            'category': None,
            'attributes': [
                {'id': self.capacity.pk, 'name': 'Capacidad', 'kind': 'text', 'sort_order': 0, 'options': [
                    {'id': self.gb128.pk, 'label': '128 GB', 'swatch_hex': '', 'sort_order': 0},
                    {'id': self.gb256.pk, 'label': '256 GB', 'swatch_hex': '', 'sort_order': 0}]},
                # Azul sale de la variante seminueva publicada (128 GB Azul), aunque no tenga equipos.
                {'id': self.colour.pk, 'name': 'Color', 'kind': 'color', 'sort_order': 0, 'options': [
                    {'id': self.blue.pk, 'label': 'Azul', 'swatch_hex': '#1D4ED8', 'sort_order': 0},
                    {'id': self.black.pk, 'label': 'Negro', 'swatch_hex': '#111111', 'sort_order': 0}]},
            ],
            'lines': {'new': {'label': 'Nuevos', 'count': 2}, 'used': {'label': 'Seminuevos', 'count': 0}},
            'variants': [
                {'product_id': self.new128.pk, 'slug': 'tx-128-negro', 'name': 'Teléfono X 128 GB Negro',
                 'options': {str(self.capacity.pk): self.gb128.pk, str(self.colour.pk): self.black.pk},
                 'price': '3000.00', 'image_url': '', 'images': [], 'available': True, 'availability': 'own_stock',
                 'on_order': None, 'sells_by_unit': True},
                {'product_id': self.new256.pk, 'slug': 'tx-256-negro', 'name': 'Teléfono X 256 GB Negro',
                 'options': {str(self.capacity.pk): self.gb256.pk, str(self.colour.pk): self.black.pk},
                 'price': '3500.00', 'image_url': '', 'images': [], 'available': False, 'availability': 'none',
                 'on_order': None, 'sells_by_unit': True},
            ],
            'used_units': [],
            'sold_out': False,
        }
        self.assertEqual(self.detail(), expected)
        before = self.detail()
        # Con paleta, y con variantes fuera de ella, la tienda responde exactamente lo mismo.
        self.palette(self.gb128, self.blue, self.esim, self.lla)
        self.assertEqual(self.detail(), before)
        self.assertNotIn('role', str(before))
        self.assertNotIn('palette', str(before))

    def test_the_public_list_is_unchanged_too(self):
        before = self.public.get('/api/catalog/models/').json()
        self.assertEqual(sorted(before['results'][0]), ['category', 'from_price', 'has_new', 'has_used', 'image_url',
                                                        'name', 'slug', 'sold_out'])
        self.palette(self.gb128, self.blue)
        self.assertEqual(self.public.get('/api/catalog/models/').json(), before)
