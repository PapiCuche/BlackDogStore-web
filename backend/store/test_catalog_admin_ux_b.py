"""
CATALOG-ADMIN-UX-B — el editor de modelos y variantes a escala.

Lo que estas pruebas fijan:

  * la lista de modelos busca en el servidor (nombre, dirección y alias de proveedor del modelo) y filtra por
    categoría, activo/inactivo, variantes incompletas, combinaciones válidas sin variante y modelos sin paleta;
    pagina cuando se le pide, y su número de consultas no crece con el número de modelos; sin parámetros responde
    lo de siempre (todas las filas en `results`), porque otras pantallas la leen así;
  * el detalle de un modelo lee el historial de TODAS sus variantes de una vez: 1 variante o 30 cuestan las
    mismas consultas, y el historial por lotes es idéntico al de `variant_history`;
  * «Crear variante» es por LÍNEA: si la combinación ya existe como seminuevo, todavía se puede crear la nueva
    (y al revés); la misma línea dos veces es `duplicate`. Nace borrador, oculta y a 0,00;
  * un modelo SIN paleta no crea variantes desde la paleta (409 `no_palette`): primero se define qué admite;
  * las exclusiones (pares imposibles) se añaden y se quitan por la API (`products.manage`, auditado, por empresa);
    añadir un par que ya es una variante existente se rechaza (409) nombrando esas variantes;
  * cada modelo ofrece SUS colores: dos modelos con paletas de colores distintas no se mezclan ni en la matriz ni
    al crear una variante.

Los nombres de prueba son inventados (AGENTS §2): nada es de un fabricante ni de la tienda piloto.
"""
from decimal import Decimal as D

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from store import catalog_aliases, device_catalog, model_palette
from store.models import (
    AdminAuditLog, BranchStock, Category, ModelOptionExclusion, Order, OrderItem, Product, StockUnit,
)
from store.test_device_commerce import _client
from store.test_model_palette import PaletteShop
from store.tests import _p2d_member, _p3_company, _p3_order


def queries(callable_):
    with CaptureQueriesContext(connection) as ctx:
        result = callable_()
    return len(ctx.captured_queries), result


def _history_before_the_batch(product):
    """`device_catalog.variant_history` as it read ONE variant before CATALOG-ADMIN-UX-B: the reference, frozen."""
    from django.db.models import Sum

    from store.models import StockMovement

    units = StockUnit.objects.filter(product_id=product.pk).exclude(status=StockUnit.Status.VOIDED)
    items = OrderItem.objects.filter(product_id=product.pk)
    return {
        'sold': items.count(),
        'units': units.exclude(status=StockUnit.Status.WRITTEN_OFF).count(),
        'reserved': units.filter(status=StockUnit.Status.RESERVED).count(),
        'stock': int(BranchStock.objects.filter(product_id=product.pk).aggregate(n=Sum('quantity'))['n'] or 0),
        'moved': StockMovement.objects.filter(product_id=product.pk).exists(),
        'open_orders': items.filter(order__status=Order.Status.PAID).exclude(
            order__fulfillment_status__in=[Order.FulfillmentStatus.DELIVERED, Order.FulfillmentStatus.CANCELLED],
        ).values('order_id').distinct().count(),
        'conditions': sorted(set(units.exclude(status=StockUnit.Status.WRITTEN_OFF).values_list('condition', flat=True))),
    }


class _Shop(PaletteShop):
    """`PaletteShop` («Teléfono X», sin paleta, con 3 variantes) y un catálogo con cada estado de la lista."""

    def catalogue(self, slug):
        self.build(slug)
        self.list_url = f'/api/admin/catalog/models/{self.q}'
        # Completo: una combinación, con su variante.
        self.full = device_catalog.create_model(company=self.company, name='Equipo Completo')
        self.palette(self.gb128, self.black, model=self.full)
        self.draft(self.full, self.gb128, self.black)
        # Pendiente: dos combinaciones válidas, ninguna variante.
        self.pending = device_catalog.create_model(company=self.company, name='Equipo Pendiente')
        self.palette(self.gb128, self.gb256, self.black, model=self.pending)
        # Incompleto: una variante que la paleta de hoy deja sin SIM.
        self.incomplete = device_catalog.create_model(company=self.company, name='Equipo Incompleto')
        loose = Product.objects.create(company=self.company, name='Equipo Incompleto 128 GB Negro',
                                       slug='equipo-incompleto-128-negro', price=D('10.00'))
        device_catalog.set_variant(loose, model_id=self.incomplete.pk, condition_line='new',
                                   option_ids=[self.gb128.pk, self.black.pk])
        self.palette(self.gb128, self.black, self.esim, model=self.incomplete)
        # Retirado: inactivo, sin paleta.
        self.retired = device_catalog.create_model(company=self.company, name='Equipo Retirado')
        device_catalog.update_model(self.retired, is_active=False)
        self.phones = Category.objects.create(company=self.company, name='Móviles', slug='moviles')
        device_catalog.update_model(self.full, category_id=self.phones.pk)
        catalog_aliases.create_alias(company=self.company, scope='model', alias='EQC ultra', model_id=self.full.pk)

    def draft(self, model, *options, line='new'):
        return device_catalog.create_variant_product(
            self.company, model, condition_line=line, option_ids=[o.pk for o in options], actor=self.staff,
            request=None)

    def names(self, query=''):
        res = self.api.get(f'{self.list_url}&{query}' if query else self.list_url)
        self.assertEqual(res.status_code, 200, res.content)
        return [row['name'] for row in res.json()['results']], res.json()


class ModelListSearchTest(_Shop, TestCase):
    def setUp(self):
        self.catalogue('ux-b-lista')

    def test_without_parameters_every_model_is_listed_as_before(self):
        names, body = self.names()
        self.assertEqual(sorted(names), sorted(['Teléfono X', 'Equipo Completo', 'Equipo Pendiente',
                                                'Equipo Incompleto', 'Equipo Retirado']))
        self.assertEqual(body['count'], 5)
        row = next(r for r in body['results'] if r['name'] == 'Teléfono X')
        self.assertEqual((row['variant_count'], row['has_palette']), (3, False))

    def test_search_reads_the_name_without_case_or_accents(self):
        self.assertEqual(self.names('search=telefono')[0], ['Teléfono X'])
        self.assertEqual(self.names('search=EQUIPO%20COMPLETO')[0], ['Equipo Completo'])

    def test_search_reads_the_address_and_the_model_aliases(self):
        self.assertEqual(self.names('search=equipo-pendiente')[0], ['Equipo Pendiente'])
        self.assertEqual(self.names('search=eqc')[0], ['Equipo Completo'])
        self.assertEqual(self.names('search=nada-de-esto')[0], [])

    def test_category_and_active_filters(self):
        self.assertEqual(self.names(f'category={self.phones.pk}')[0], ['Equipo Completo'])
        self.assertNotIn('Equipo Completo', self.names('category=none')[0])
        self.assertEqual(self.names('active=false')[0], ['Equipo Retirado'])
        self.assertNotIn('Equipo Retirado', self.names('active=true')[0])

    def test_the_palette_state_filters(self):
        self.assertEqual(self.names('incomplete=1')[0], ['Equipo Incompleto'])
        self.assertEqual(sorted(self.names('missing=1')[0]), ['Equipo Incompleto', 'Equipo Pendiente'])
        self.assertEqual(sorted(self.names('no_palette=1')[0]), ['Equipo Retirado', 'Teléfono X'])

    def test_each_row_says_its_palette_state(self):
        _names, body = self.names()
        rows = {r['name']: r for r in body['results']}
        self.assertEqual(rows['Equipo Pendiente']['valid_without_variant'], 2)
        self.assertEqual(rows['Equipo Completo']['valid_without_variant'], 0)
        self.assertEqual(rows['Equipo Incompleto']['incomplete_variant_count'], 1)
        self.assertIsNone(rows['Teléfono X']['valid_without_variant'])
        self.assertEqual(body['facets'], {'incomplete': 1, 'missing': 2, 'no_palette': 2})

    def test_the_list_counts_match_the_palette_screen(self):
        _names, body = self.names()
        for row in body['results']:
            if not row['has_palette']:
                continue
            summary = model_palette.payload(model_palette.ProductModel.objects.get(pk=row['id']))['summary']
            self.assertEqual(row['valid_without_variant'], summary['valid_without_variant'], row['name'])
            self.assertEqual(row['incomplete_variant_count'], summary['incomplete'], row['name'])

    def test_pagination_is_bounded_and_counted(self):
        res = self.api.get(f'{self.list_url}&page_size=2&page=2')
        body = res.json()
        self.assertEqual((body['count'], body['page'], body['page_size'], len(body['results'])), (5, 2, 2, 2))
        self.assertEqual(self.api.get(f'{self.list_url}&page_size=100000').json()['page_size'], 100)
        self.assertEqual(self.api.get(f'{self.list_url}&page_size=2&page=99').json()['results'], [])
        self.assertEqual(self.api.get(f'{self.list_url}&page_size=abc').status_code, 400)

    def test_another_company_never_appears_nor_matches(self):
        other = _p3_company('ux-b-otra', 'Otra')
        hidden = device_catalog.create_model(company=other, name='Equipo Ajeno')
        catalog_aliases.create_alias(company=other, scope='model', alias='TELEFONO', model_id=hidden.pk)
        self.assertNotIn('Equipo Ajeno', self.names()[0])
        self.assertEqual(self.names('search=ajeno')[0], [])
        self.assertEqual(self.names('search=telefono')[0], ['Teléfono X'])

    def test_the_query_count_does_not_grow_with_the_models(self):
        small, _ = queries(lambda: self.api.get(self.list_url + '&incomplete=1'))
        for n in range(8):
            model = device_catalog.create_model(company=self.company, name=f'Equipo Extra {n}')
            self.palette(self.gb128, self.gb256, self.black, self.blue, self.esim, model=model)
            self.draft(model, self.gb128, self.black, self.esim)
            self.draft(model, self.gb256, self.blue, self.esim, line='used')
            model_palette.add_exclusions(model, pairs=[(self.gb256.pk, self.blue.pk)], actor=self.staff)
        large, res = queries(lambda: self.api.get(self.list_url + '&incomplete=1'))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(small, large)
        self.assertLessEqual(large, 20)

    def test_view_only_may_read_and_a_stranger_may_not(self):
        reader, _ = _p2d_member(self.company, 'ux-b-lector', ['products.view'])
        self.assertEqual(_client(reader).get(self.list_url + '&search=equipo').status_code, 200)
        stranger, _ = _p2d_member(_p3_company('ux-b-ajena', 'Ajena'), 'ux-b-ajeno', ['products.view'])
        self.assertIn(_client(stranger).get(self.list_url).status_code, (403, 404))


class EditorQueryCountTest(_Shop, TestCase):
    def setUp(self):
        self.build('ux-b-detalle')
        self.palette(self.gb128, self.gb256, self.black, self.blue, self.red, self.esim, self.chip, self.lla, self.ja)
        self.big = device_catalog.create_model(company=self.company, name='Equipo Grande')
        self.palette(self.gb128, self.gb256, self.black, self.blue, self.red, self.esim, self.chip, self.lla, self.ja,
                     model=self.big)
        self.small = device_catalog.create_model(company=self.company, name='Equipo Chico')
        self.palette(self.gb128, self.black, self.esim, self.lla, model=self.small)
        self.draft(self.small, self.gb128, self.black, self.esim, self.lla)
        made = 0
        for line in ('new', 'used'):
            for cap in (self.gb128, self.gb256):
                for colour in (self.black, self.blue, self.red):
                    for sim in (self.esim, self.chip):
                        for region in (self.lla, self.ja):
                            if made < 30:
                                self.draft(self.big, cap, colour, sim, region, line=line)
                                made += 1
        self.assertEqual(self.big.variants.count(), 30)

    def detail(self, model):
        return queries(lambda: self.api.get(f'/api/admin/catalog/models/{model.pk}/{self.q}'))

    def test_one_variant_or_thirty_cost_the_same_queries(self):
        one, res_one = self.detail(self.small)
        thirty, res_thirty = self.detail(self.big)
        self.assertEqual((res_one.status_code, res_thirty.status_code), (200, 200))
        self.assertEqual(len(res_thirty.json()['variants']), 30)
        self.assertEqual(one, thirty)
        self.assertLessEqual(thirty, 25)

    def test_the_batch_history_is_the_history_of_each_variant(self):
        """
        Review of #411: compared with the PER-VARIANT body as it was before the batch (frozen below), never with
        `variant_history` itself (which now goes through the batch). Every unit status and condition, voided and
        written-off units, every order status and fulfilment, and orders with several of the variants.
        """
        products = [self.new128, self.used128, self.new256]
        statuses, conditions = list(StockUnit.Status.values), list(StockUnit.Condition.values)
        for index, product in enumerate(products):
            self.receive(product, len(statuses))
            for n, unit in enumerate(StockUnit.objects.filter(product=product).order_by('pk')):
                unit_status = statuses[(n + index) % len(statuses)]
                extra = {'voided_at': timezone.now()} if unit_status == StockUnit.Status.VOIDED else {}
                StockUnit.objects.filter(pk=unit.pk).update(
                    status=unit_status, condition=conditions[(n + 2 * index) % len(conditions)], **extra)
        BranchStock.objects.update_or_create(branch=self.branch, product=self.new128, defaults={'quantity': 4})
        BranchStock.objects.update_or_create(branch=self.branch, product=self.new256, defaults={'quantity': 0})
        fulfilments = list(Order.FulfillmentStatus.values)
        for n, order_status in enumerate(Order.Status.values * 2):
            order = _p3_order(self.company, status=order_status)
            Order.objects.filter(pk=order.pk).update(fulfillment_status=fulfilments[n % len(fulfilments)])
            for product in products[:1 + n % len(products)]:      # one, two or three variants per order
                OrderItem.objects.create(order=order, product=product, quantity=1 + n % 3, price=D('1.00'))
        batch = device_catalog.variant_histories([p.pk for p in products] + [987654321])
        for product in products:
            frozen = _history_before_the_batch(product)
            self.assertEqual(batch[product.pk], frozen, product.name)
            self.assertEqual(device_catalog.variant_history(product, lock=True), frozen, product.name)
        self.assertTrue(any(batch[p.pk]['open_orders'] for p in products))
        self.assertTrue(any(batch[p.pk]['reserved'] for p in products))
        self.assertEqual(batch[987654321], {'sold': 0, 'units': 0, 'reserved': 0, 'stock': 0, 'moved': False,
                                            'open_orders': 0, 'conditions': []})

    def test_the_payload_still_carries_each_variant_history_and_rules(self):
        self.receive(self.used128, 1)
        res = self.api.get(f'/api/admin/catalog/models/{self.model.pk}/{self.q}')
        row = next(v for v in res.json()['variants'] if v['product_id'] == self.used128.pk)
        self.assertEqual(row['history']['units'], 1)
        self.assertEqual(row['removal'], 'blocked')
        # The model's palette has SIM and region; this old variant has neither: reported, never corrected.
        self.assertEqual(row['palette_status'], 'incompleta')


class CreateVariantPerLineTest(_Shop, TestCase):
    def setUp(self):
        self.build('ux-b-linea')
        self.palette(self.gb128, self.gb256, self.black, self.blue)
        self.url = f'/api/admin/catalog/models/{self.model.pk}/variants/{self.q}'

    def post(self, line, *options):
        return self.api.post(self.url, {'condition_line': line, 'option_ids': [o.pk for o in options]}, format='json')

    def test_the_new_line_is_created_when_only_the_used_one_exists(self):
        # «Teléfono X 128 GB Azul» existe como seminuevo (DeviceShop): todavía falta la nueva.
        res = self.post('new', self.gb128, self.blue)
        self.assertEqual(res.status_code, 201, res.content)
        product = Product.objects.get(pk=res.json()['id'])
        self.assertEqual((product.condition_line, product.price, product.is_published_online),
                         ('new', D('0.00'), False))
        self.assertTrue(product.is_pending_draft)

    def test_the_used_line_is_created_when_only_the_new_one_exists(self):
        res = self.post('used', self.gb128, self.black)
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(Product.objects.get(pk=res.json()['id']).condition_line, 'used')

    def test_the_same_line_twice_is_a_duplicate(self):
        res = self.post('used', self.gb128, self.blue)
        # As every other duplicate of the catalogue: 400 `duplicate`, nothing written.
        self.assertEqual((res.status_code, res.json()['code']), (400, 'duplicate'))

    def test_the_summary_counts_what_is_missing_in_each_line(self):
        summary = model_palette.payload(self.model)['summary']
        # 4 combinaciones; nuevas: 128 Negro, 256 Negro → faltan 2; seminuevas: 128 Azul → faltan 3.
        self.assertEqual(summary['valid_without_variant_by_line'], {'new': 2, 'used': 3})
        self.post('new', self.gb128, self.blue)
        summary = model_palette.payload(self.model)['summary']
        self.assertEqual(summary['valid_without_variant_by_line'], {'new': 1, 'used': 3})


class NoPaletteTest(_Shop, TestCase):
    def setUp(self):
        self.build('ux-b-sin-paleta')

    def test_a_model_without_palette_creates_no_variant_from_the_palette(self):
        before = Product.objects.filter(company=self.company).count()
        res = self.api.post(f'/api/admin/catalog/models/{self.model.pk}/variants/{self.q}',
                            {'condition_line': 'new', 'option_ids': [self.gb256.pk, self.blue.pk]}, format='json')
        self.assertEqual((res.status_code, res.json()['code']), (409, 'no_palette'))
        self.assertEqual(Product.objects.filter(company=self.company).count(), before)

    def test_the_service_refuses_too(self):
        with self.assertRaises(device_catalog.CatalogError) as caught:
            device_catalog.create_variant_product(self.company, self.model, condition_line='new',
                                                  option_ids=[self.gb256.pk, self.blue.pk], actor=self.staff,
                                                  request=None)
        self.assertEqual(caught.exception.code, 'no_palette')

    def test_classifying_an_existing_product_keeps_working_as_before(self):
        loose = Product.objects.create(company=self.company, name='Suelto', slug='suelto', price=D('5.00'))
        device_catalog.set_variant(loose, model_id=self.model.pk, condition_line='new',
                                   option_ids=[self.gb256.pk, self.blue.pk])
        self.assertEqual(Product.objects.get(pk=loose.pk).product_model_id, self.model.pk)


class ExclusionsApiTest(_Shop, TestCase):
    def setUp(self):
        self.build('ux-b-excl')
        self.palette(self.gb128, self.gb256, self.black, self.blue, self.esim, self.chip, self.lla, self.ja)
        self.url = f'/api/admin/catalog/models/{self.model.pk}/exclusions/{self.q}'

    def test_a_pair_is_added_audited_and_the_matrix_drops_it(self):
        res = self.api.post(self.url, {'pairs': [[self.chip.pk, self.lla.pk]]}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        self.assertEqual(body['changed'], {'added': 1, 'total': 1})
        self.assertEqual(body['excluded_pairs'], [sorted([self.chip.pk, self.lla.pk])])
        self.assertTrue(AdminAuditLog.objects.filter(action='product_model_exclusions_added', company=self.company,
                                                     target_id=str(self.model.pk)).exists())

    def test_a_pair_is_removed_and_audited(self):
        model_palette.add_exclusions(self.model, pairs=[(self.chip.pk, self.lla.pk)], actor=self.staff)
        res = self.api.delete(self.url, {'pairs': [[self.lla.pk, self.chip.pk]]}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['changed'], {'removed': 1, 'total': 0})
        self.assertNotIn('excluded_pairs', res.json())
        self.assertFalse(ModelOptionExclusion.objects.filter(model=self.model).exists())
        self.assertTrue(AdminAuditLog.objects.filter(action='product_model_exclusions_removed', company=self.company,
                                                     target_id=str(self.model.pk)).exists())

    def test_a_pair_an_existing_variant_holds_is_refused_naming_it(self):
        variant = self.draft(self.model, self.gb128, self.blue, self.chip, self.lla)
        res = self.api.post(self.url, {'pairs': [[self.chip.pk, self.lla.pk]]}, format='json')
        self.assertEqual((res.status_code, res.json()['code']), (409, 'exclusion_held_by_variants'))
        self.assertEqual([v['product_id'] for v in res.json()['variants']], [variant.pk])
        self.assertIn(variant.name, res.json()['detail'])
        self.assertFalse(ModelOptionExclusion.objects.filter(model=self.model).exists())

    def test_bad_bodies_are_400(self):
        for body in ({}, {'pairs': 'x'}, {'pairs': [[self.chip.pk]]}, {'pairs': [[self.chip.pk, self.esim.pk]]}):
            with self.subTest(body):
                self.assertEqual(self.api.post(self.url, body, format='json').status_code, 400)
        self.assertEqual(self.api.post(self.url, {'pairs': [[self.chip.pk, self.red.pk]]}, format='json').json()['code'],
                         'option_not_in_palette')

    def test_view_only_may_not_write(self):
        reader, _ = _p2d_member(self.company, 'ux-b-excl-lector', ['products.view'])
        res = _client(reader).post(self.url, {'pairs': [[self.chip.pk, self.lla.pk]]}, format='json')
        self.assertEqual(res.status_code, 403)
        self.assertFalse(ModelOptionExclusion.objects.filter(model=self.model).exists())

    def test_another_company_is_not_found(self):
        other = _p3_company('ux-b-excl-otra', 'Otra')
        foreign = device_catalog.create_model(company=other, name='Equipo Ajeno')
        res = self.api.post(f'/api/admin/catalog/models/{foreign.pk}/exclusions/{self.q}',
                            {'pairs': [[self.chip.pk, self.lla.pk]]}, format='json')
        self.assertEqual(res.status_code, 404)
        sim = device_catalog.create_attribute(company=other, name='SIM', role='sim')
        alien = device_catalog.create_option(company=other, attribute_id=sim.pk, label='Doble')
        res = self.api.post(self.url, {'pairs': [[alien.pk, self.lla.pk]]}, format='json')
        self.assertEqual((res.status_code, res.json()['code']), (404, 'not_found'))


class ColoursPerModelTest(_Shop, TestCase):
    """«Un modelo muestra sus colores y otro los suyos»: la paleta de cada modelo, nunca la de otro."""

    def setUp(self):
        self.build('ux-b-colores')
        self.first = device_catalog.create_model(company=self.company, name='Equipo Uno')
        self.second = device_catalog.create_model(company=self.company, name='Equipo Dos')
        self.palette(self.gb128, self.black, self.blue, model=self.first)
        self.palette(self.gb128, self.red, model=self.second)

    def colours_offered(self, model):
        body = model_palette.payload(model)
        return {pk for combo in body['combinations'] for pk in combo['option_ids']} & {
            self.black.pk, self.blue.pk, self.red.pk}

    def test_each_matrix_offers_only_its_own_colours(self):
        self.assertEqual(self.colours_offered(self.first), {self.black.pk, self.blue.pk})
        self.assertEqual(self.colours_offered(self.second), {self.red.pk})

    def test_a_colour_of_another_model_is_refused_when_creating(self):
        res = self.api.post(f'/api/admin/catalog/models/{self.second.pk}/variants/{self.q}',
                            {'condition_line': 'new', 'option_ids': [self.gb128.pk, self.black.pk]}, format='json')
        self.assertEqual((res.status_code, res.json()['code']), (400, 'option_not_in_palette'))
        self.assertFalse(self.second.variants.exists())

    def test_the_detail_payload_never_mixes_them(self):
        self.draft(self.first, self.gb128, self.blue)
        self.draft(self.second, self.gb128, self.red)
        second = self.api.get(f'/api/admin/catalog/models/{self.second.pk}/{self.q}').json()
        labels = {o['label'] for v in second['variants'] for o in v['options']}
        self.assertEqual(labels, {'128 GB', 'Rojo'})


class InlineEditStaleTest(_Shop, TestCase):
    """
    Review of #411: the row's price and «Publicar» re-send the variant's identity as the editor READ it. Someone may
    have changed the variant meanwhile: with `expected_*` the server compares under the product's lock and answers
    409 `stale`, writing nothing — never silently putting back the old options, line or model.
    """

    def setUp(self):
        self.build('ux-b-stale')
        self.url = f'/api/admin/products/{self.new128.pk}/variant/{self.q}'
        self.read = {'model_id': self.model.pk, 'condition_line': 'new', 'option_ids': [self.gb128.pk, self.black.pk]}

    def inline(self, **fields):
        return self.api.put(self.url, {**self.read, 'expected_model_id': self.read['model_id'],
                                       'expected_condition_line': self.read['condition_line'],
                                       'expected_option_ids': self.read['option_ids'], **fields}, format='json')

    def test_a_current_editor_saves_the_price(self):
        res = self.inline(price='2999.00')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(Product.objects.get(pk=self.new128.pk).price, D('2999.00'))

    def test_a_stale_editor_gets_409_and_nothing_is_written(self):
        # Another person moved the variant to blue after this editor read it.
        device_catalog.set_variant(self.new128, model_id=self.model.pk, condition_line='new',
                                   option_ids=[self.gb128.pk, self.blue.pk])
        before = Product.objects.get(pk=self.new128.pk)
        res = self.inline(price='1.00', is_published_online=not before.is_published_online)
        self.assertEqual((res.status_code, res.json()['code']), (409, 'stale'))
        after = Product.objects.get(pk=self.new128.pk)
        self.assertEqual((after.price, after.is_published_online, after.variant_key),
                         (before.price, before.is_published_online, before.variant_key))
        self.assertEqual({o.pk for o in device_catalog.variant_options_of(after)}, {self.gb128.pk, self.blue.pk})

    def test_a_changed_line_or_model_is_stale_too(self):
        other = device_catalog.create_model(company=self.company, name='Equipo Otro')
        device_catalog.set_variant(self.new128, model_id=other.pk, condition_line='new',
                                   option_ids=[self.gb128.pk, self.black.pk])
        res = self.inline(price='1.00')
        self.assertEqual((res.status_code, res.json()['code']), (409, 'stale'))
        self.assertEqual(Product.objects.get(pk=self.new128.pk).product_model_id, other.pk)

    def test_bad_expected_values_are_400(self):
        for extra in ({'expected_option_ids': 'x'}, {'expected_model_id': 'abc'}, {'expected_condition_line': 'raro'}):
            with self.subTest(extra):
                self.assertEqual(self.inline(price='1.00', **extra).status_code, 400)
        self.assertEqual(Product.objects.get(pk=self.new128.pk).price, D('3000.00'))

    def test_without_expected_values_the_put_behaves_as_before(self):
        res = self.api.put(self.url, {**self.read, 'price': '2500.00'}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
