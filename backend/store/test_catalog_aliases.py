"""
CATALOG-COMMERCIAL-01 · A2-1 — alias de catálogo por empresa (DEC-CATALOG-ALIAS-01) y su uso en las
lecturas que ya existen (decisiones P6, P7, P8 y P9 del propietario, 2026-11-13).

Lo que estas pruebas fijan:

  * un alias tiene UN significado por empresa y ámbito (palabra de modelo, modelo, opción); nunca tapa
    una etiqueta real ni otro alias; sin cadenas; con auditoría que lleva la empresa;
  * leer y resolver piden `products.view`; crear, editar y borrar, `products.manage`; un alias, un modelo
    o una opción de otra empresa responde 404;
  * `resolve_variant_spec` lee etiquetas sin acentos, capacidades sin espacio y alias, y sigue igual para
    lo que ya encontraba; dice qué alias aplicó;
  * las vistas previas de productos y de ofertas usan los alias y lo dicen en la fila; un archivo antiguo
    carga igual; ninguna vista previa crea stock, ofertas ni variantes;
  * recepción y ofertas leen igual la línea 11: «incompleta», nunca se elige la variante que coincide en parte;
  * una oferta sobre un borrador pendiente lo avisa y no lo vuelve vendible;
  * el emparejador de texto admite SIM y región, y responde con la configuración del resolutor;
  * `catalog_proposal.propose` avisa cuando el nombre es un alias o trae opciones; `apply` no cambia.

Nada aquí es de Apple ni de la tienda piloto: nombres inventados, valores de cada empresa de prueba.
"""
import json
from datetime import timedelta
from threading import Barrier, Thread

from django.core.cache import cache
from django.db import IntegrityError, connection, transaction
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from store import catalog_aliases, catalog_proposal, device_catalog, supply_services
from store.models import (
    AdminAuditLog, BranchStock, CatalogAlias, Product, ProductModel, StockMovement, StockUnit, SupplierOffer,
    VariantOption,
)
from store.test_catalog_roles_io import NEW_HEADERS, OLD_HEADERS, RolesShop
from store.test_cost_imports import upload
from store.test_device_commerce import _client
from store.tests import _p2d_member, _p3_company, _prod

ALIASES = '/api/admin/catalog/aliases/'
VOCABULARY = '/api/admin/catalog/vocabulary/'
RESOLVE = '/api/admin/catalog/resolve/'
OFFER_HEADERS = ['Proveedor', 'Código/SKU del producto', 'Modelo', 'Capacidad', 'Color', 'SIM', 'Región', 'Línea',
                 'Costo pactado', 'Precio comercial', 'Cantidad confirmada', 'Vigente desde', 'Vigente hasta',
                 'Plazo (días)']


class AliasShop(RolesShop):
    """`RolesShop` («Teléfono X» with a four-role palette) plus readers and a second company."""

    def build(self, slug='a2-alias'):
        super().build(slug)
        self.viewer, _ = _p2d_member(self.company, f'{slug}_viewer', ['company.view', 'products.view'])
        self.nobody, _ = _p2d_member(self.company, f'{slug}_nobody', ['company.view'])
        self.other = _p3_company(f'{slug}-b', 'Otra empresa')
        other_attr = device_catalog.create_attribute(company=self.other, name='Región', role='region')
        self.other_option = device_catalog.create_option(company=self.other, attribute_id=other_attr.pk, label='LL/A')
        self.other_model = device_catalog.create_model(company=self.other, name='Teléfono X')
        self.other_staff, _ = _p2d_member(self.other, f'{slug}_other', ['company.view', 'products.view',
                                                                        'products.manage'])

    def alias(self, scope, alias, **target):
        return catalog_aliases.create_alias(company=self.company, scope=scope, alias=alias, actor=self.staff, **target)


# ---------------------------------------------------------------------------
# The service
# ---------------------------------------------------------------------------

class AliasServiceTest(AliasShop, TestCase):
    def setUp(self):
        self.build('a2-servicio')

    def test_create_each_scope_and_audit_with_the_company(self):
        word = self.alias('model_word', 'TLF', replacement='Teléfono')
        model = self.alias('model', 'Telf X', model_id=self.model.pk)
        option = self.alias('option', 'LLA', option_id=self.lla.pk)
        self.assertEqual((word.alias_key, word.replacement), ('tlf', 'telefono'))
        self.assertEqual((model.alias_key, model.model_id), ('telf x', self.model.pk))
        self.assertEqual((option.alias_key, option.option_id), ('lla', self.lla.pk))
        logs = AdminAuditLog.objects.filter(action='catalog_alias_created').order_by('pk')
        self.assertEqual(logs.count(), 3)
        self.assertTrue(all(log.company_id == self.company.pk for log in logs))
        self.assertEqual(logs.last().metadata['alias'], 'LLA')
        self.assertEqual(logs.last().metadata['option_id'], self.lla.pk)

    def test_one_meaning_per_company_and_scope(self):
        self.alias('option', 'LLA', option_id=self.lla.pk)
        for spelling in ('lla', ' LLA ', 'Llá'):
            with self.subTest(spelling), self.assertRaises(device_catalog.CatalogError) as caught:
                self.alias('option', spelling, option_id=self.ja.pk)
            self.assertEqual(caught.exception.code, 'duplicate')
        # Another company has its own meaning for the same text.
        catalog_aliases.create_alias(company=self.other, scope='option', alias='LLA', option_id=self.other_option.pk,
                                     actor=self.other_staff)

    def test_an_alias_never_hides_a_real_label_or_model_name(self):
        cases = [
            ('option', 'azul', {'option_id': self.black.pk}),          # a label of the same role
            ('option', 'chip', {'option_id': self.lla.pk}),            # a label of another role
            ('option', 'ESIM', {'option_id': self.esim.pk}),           # its own label: needs no alias
            ('model', 'telefono x', {'model_id': self.model.pk}),       # a model's name
            ('model_word', 'telefono', {'replacement': 'tlf'}),        # a word of the company's model names
        ]
        for scope, text, target in cases:
            with self.subTest(text), self.assertRaises(device_catalog.CatalogError) as caught:
                self.alias(scope, text, **target)
            self.assertEqual(caught.exception.code, 'conflict_with_label', text)
        self.assertFalse(CatalogAlias.objects.exists())

    def test_a_label_or_model_name_never_takes_an_aliases_text_later(self):
        self.alias('option', 'Celeste', option_id=self.blue.pk)
        with self.assertRaises(device_catalog.CatalogError) as caught:
            device_catalog.create_option(company=self.company, attribute_id=self.colour.pk, label='CELESTE',
                                         swatch_hex='#00AAFF')
        self.assertEqual(caught.exception.code, 'conflict_with_alias')
        with self.assertRaises(device_catalog.CatalogError) as caught:
            device_catalog.update_option(self.red, label='celeste')
        self.assertEqual(caught.exception.code, 'conflict_with_alias')
        self.alias('model', 'Telf X', model_id=self.model.pk)
        with self.assertRaises(device_catalog.CatalogError) as caught:
            device_catalog.create_model(company=self.company, name='TELF  X', actor=self.staff)
        self.assertEqual(caught.exception.code, 'conflict_with_alias')

    def test_no_chains_and_no_cycles(self):
        self.alias('model_word', 'tlf', replacement='telefono')
        with self.assertRaises(device_catalog.CatalogError) as caught:
            self.alias('model_word', 'tf', replacement='tlf')          # tf → tlf → telefono
        self.assertEqual(caught.exception.code, 'chain')
        with self.assertRaises(device_catalog.CatalogError) as caught:
            self.alias('model_word', 'telefon', replacement='telefon')  # itself
        self.assertEqual(caught.exception.code, 'invalid')
        self.alias('model_word', 'fono', replacement='movil')
        with self.assertRaises(device_catalog.CatalogError) as caught:
            self.alias('model_word', 'movil', replacement='fono')       # a cycle
        self.assertEqual(caught.exception.code, 'chain')

    def test_what_an_alias_text_may_be(self):
        bad = [
            ('option', '', {'option_id': self.lla.pk}),
            ('option', '   ', {'option_id': self.lla.pk}),
            ('option', '12345', {'option_id': self.lla.pk}),            # only digits
            ('option', '256gb', {'option_id': self.gb256.pk}),          # a capacity: normalised, never aliased
            ('option', 'azul y negro', {'option_id': self.blue.pk}),    # a separator in a colour alias
            ('option', 'x' * 61, {'option_id': self.lla.pk}),
            ('model_word', 'dos palabras', {'replacement': 'una'}),
            ('model_word', 'tlf', {'replacement': 'dos palabras'}),
            ('model', 'Teléfono X (eSIM LL/A)', {'model_id': self.model.pk}),  # options inside a model alias
        ]
        for scope, text, target in bad:
            with self.subTest(text), self.assertRaises(device_catalog.CatalogError) as caught:
                self.alias(scope, text, **target)
            self.assertIn(caught.exception.code, ('invalid', 'contains_options'), text)
        with self.assertRaises(device_catalog.CatalogError) as caught:
            self.alias('weird', 'x', option_id=self.lla.pk)
        self.assertEqual(caught.exception.field, 'scope')

    def test_a_target_of_another_company_is_not_found(self):
        for scope, target in (('option', {'option_id': self.other_option.pk}),
                              ('model', {'model_id': self.other_model.pk})):
            with self.subTest(scope), self.assertRaises(device_catalog.CatalogError) as caught:
                self.alias(scope, 'algo', **target)
            self.assertEqual(caught.exception.code, 'not_found')
        with self.assertRaises(ValueError):
            CatalogAlias.objects.create(company=self.company, scope='option', alias='x', alias_key='x',
                                        option=self.other_option)

    def test_the_database_holds_the_scope_rules(self):
        for fields in ({'scope': 'option', 'model': self.model}, {'scope': 'model', 'option': self.lla},
                       {'scope': 'model_word'}, {'scope': 'option', 'option': self.lla, 'alias_key': ''}):
            values = {'company': self.company, 'alias': 'x', 'alias_key': 'x', **fields}
            with self.subTest(fields), self.assertRaises(IntegrityError), transaction.atomic():
                CatalogAlias.objects.create(**values)

    def test_update_and_delete_are_audited_and_keep_the_rules(self):
        made = self.alias('option', 'LLA', option_id=self.lla.pk)
        changed = catalog_aliases.update_alias(made, alias='L.L.A', option_id=self.lla.pk, actor=self.staff)
        self.assertEqual(changed.alias_key, 'l . l . a')
        with self.assertRaises(device_catalog.CatalogError) as caught:
            catalog_aliases.update_alias(made, alias='Chip', actor=self.staff)
        self.assertEqual(caught.exception.code, 'conflict_with_label')
        catalog_aliases.delete_alias(made, actor=self.staff)
        self.assertFalse(CatalogAlias.objects.exists())
        self.assertEqual(list(AdminAuditLog.objects.filter(action__startswith='catalog_alias_')
                              .order_by('pk').values_list('action', 'company_id')),
                         [('catalog_alias_created', self.company.pk), ('catalog_alias_updated', self.company.pk),
                          ('catalog_alias_deleted', self.company.pk)])

    def test_odd_input_is_a_400_never_a_500(self):
        # Review findings 3, 4 and 13.
        for scope, text, target in (('model', 'Foo bar', {'model_id': '²'}), ('option', 'x', {'option_id': '²'}),
                                    ('option', 'Foo\x00', {'option_id': self.lla.pk}),
                                    ('option', 'zona\u200b', {'option_id': self.lla.pk}),
                                    ('option', '256-GB', {'option_id': self.gb256.pk}),
                                    ('option', [1], {'option_id': self.lla.pk})):
            with self.subTest(text=repr(text)), self.assertRaises(device_catalog.CatalogError):
                self.alias(scope, text, **target)
        self.assertFalse(CatalogAlias.objects.exists())

    def test_joiners_and_glued_words_never_hide_a_label_or_a_model(self):
        # Second review pass: «Azul-Marino» reads as «Azul Marino»; «Fono16» as «Fono 16».
        device_catalog.create_option(company=self.company, attribute_id=self.colour.pk, label='Azul Marino',
                                     swatch_hex='#000080')
        with self.assertRaises(device_catalog.CatalogError) as caught:
            self.alias('option', 'Azul-Marino', option_id=self.red.pk)
        self.assertEqual(caught.exception.code, 'conflict_with_label')
        with self.assertRaises(device_catalog.CatalogError) as caught:
            device_catalog.create_option(company=self.company, attribute_id=self.colour.pk, label='azul-marino',
                                         swatch_hex='#000080')
        self.assertEqual(caught.exception.code, 'duplicate')
        self.alias('option', 'Celeste-Cielo', option_id=self.blue.pk)
        with self.assertRaises(device_catalog.CatalogError) as caught:
            device_catalog.create_option(company=self.company, attribute_id=self.colour.pk, label='Celeste Cielo',
                                         swatch_hex='#00AAFF')
        self.assertEqual(caught.exception.code, 'conflict_with_alias')
        self.alias('model', 'Fono16', model_id=self.model.pk)
        with self.assertRaises(device_catalog.CatalogError) as caught:
            device_catalog.create_model(company=self.company, name='Fono 16', actor=self.staff)
        self.assertEqual(caught.exception.code, 'conflict_with_alias')

    def test_a_word_alias_is_checked_as_the_reader_rewrites(self):
        device_catalog.create_model(company=self.company, name='Teléfono 15')
        pro = device_catalog.create_model(company=self.company, name='Teléfono 15 Pro')
        self.alias('model', 'tlf15', model_id=pro.pk)          # glued: the reader reads «tlf 15»
        with self.assertRaises(device_catalog.CatalogError) as caught:
            self.alias('model_word', 'tlf', replacement='telefono')   # «tlf 15» → «Teléfono 15», another model
        self.assertEqual(caught.exception.code, 'conflict_with_alias')

    def test_a_cap_per_company(self):
        from unittest import mock

        with mock.patch.object(catalog_aliases, 'MAX_ALIASES_PER_COMPANY', 1):
            self.alias('option', 'LLA', option_id=self.lla.pk)
            with self.assertRaises(device_catalog.CatalogError) as caught:
                self.alias('option', 'japon', option_id=self.ja.pk)
        self.assertEqual(caught.exception.code, 'too_many')

    def test_the_target_going_away_takes_the_alias(self):
        self.alias('model', 'Telf X', model_id=self.model.pk)
        other = device_catalog.create_model(company=self.company, name='Modelo Efímero')
        self.alias('model', 'efimero', model_id=other.pk)
        other.delete()
        self.assertEqual(list(CatalogAlias.objects.values_list('alias', flat=True)), ['Telf X'])


class AliasConcurrencyTest(TransactionTestCase):
    """Two people create the same alias at once: one row, the other told `duplicate`."""

    reset_sequences = False

    def test_two_creates_of_one_alias(self):
        company = _p3_company('a2-carrera', 'Carrera')
        attr = device_catalog.create_attribute(company=company, name='Región', role='region')
        option = device_catalog.create_option(company=company, attribute_id=attr.pk, label='ZN/1')
        staff, _ = _p2d_member(company, 'a2-carrera-staff', ['products.view', 'products.manage'])
        barrier, results = Barrier(2), []

        def run():
            try:
                barrier.wait()
                catalog_aliases.create_alias(company=company, scope='option', alias='zona uno', option_id=option.pk,
                                             actor=staff)
                results.append('ok')
            except device_catalog.CatalogError as exc:
                results.append(exc.code)
            finally:
                connection.close()

        threads = [Thread(target=run) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sorted(results), ['duplicate', 'ok'])
        self.assertEqual(CatalogAlias.objects.filter(company=company).count(), 1)


# ---------------------------------------------------------------------------
# The HTTP surface
# ---------------------------------------------------------------------------

class AliasApiTest(AliasShop, TestCase):
    def setUp(self):
        self.build('a2-api')
        self.manager = _client(self.staff)
        self.reader = _client(self.viewer)

    def test_crud_with_capabilities(self):
        body = {'scope': 'option', 'alias': 'LLA', 'option_id': self.lla.pk}
        self.assertEqual(self.reader.post(ALIASES + self.q, body, format='json').status_code, 403)
        self.assertEqual(_client(self.nobody).get(ALIASES + self.q).status_code, 403)
        res = self.manager.post(ALIASES + self.q, body, format='json')
        self.assertEqual(res.status_code, 201, res.content)
        made = res.json()
        self.assertEqual((made['scope'], made['alias'], made['option_id'], made['option_label'], made['role']),
                         ('option', 'LLA', self.lla.pk, 'LL/A', 'region'))
        listed = self.reader.get(ALIASES + self.q + '&scope=option').json()
        self.assertEqual([a['id'] for a in listed['results']], [made['id']])
        self.assertEqual(self.reader.patch(f'{ALIASES}{made["id"]}/{self.q}', {'alias': 'L L A'},
                                           format='json').status_code, 403)
        res = self.manager.patch(f'{ALIASES}{made["id"]}/{self.q}', {'alias': 'L L A'}, format='json')
        self.assertEqual((res.status_code, res.json()['alias']), (200, 'L L A'), res.content)
        self.assertEqual(self.reader.delete(f'{ALIASES}{made["id"]}/{self.q}').status_code, 403)
        self.assertEqual(self.manager.delete(f'{ALIASES}{made["id"]}/{self.q}').status_code, 204)
        self.assertEqual(self.manager.delete(f'{ALIASES}{made["id"]}/{self.q}').status_code, 404)

    def test_errors_name_the_rule(self):
        res = self.manager.post(ALIASES + self.q, {'scope': 'option', 'alias': 'Azul', 'option_id': self.black.pk},
                                format='json')
        self.assertEqual((res.status_code, res.json()['code']), (400, 'conflict_with_label'))
        res = self.manager.post(ALIASES + self.q, {'scope': 'option', 'alias': 'x', 'option_id': 'nope'},
                                format='json')
        self.assertEqual(res.status_code, 400)
        res = self.manager.post(ALIASES + self.q, ['not', 'an', 'object'], format='json')
        self.assertEqual(res.status_code, 400)

    def test_another_companys_alias_model_or_option_is_404(self):
        foreign = catalog_aliases.create_alias(company=self.other, scope='option', alias='LLA',
                                               option_id=self.other_option.pk, actor=self.other_staff)
        res = self.manager.post(ALIASES + self.q, {'scope': 'option', 'alias': 'x', 'option_id': self.other_option.pk},
                                format='json')
        self.assertEqual(res.status_code, 404)
        res = self.manager.post(ALIASES + self.q, {'scope': 'model', 'alias': 'x', 'model_id': self.other_model.pk},
                                format='json')
        self.assertEqual(res.status_code, 404)
        self.assertEqual(self.manager.patch(f'{ALIASES}{foreign.pk}/{self.q}', {'alias': 'y'},
                                            format='json').status_code, 404)
        self.assertEqual(self.manager.delete(f'{ALIASES}{foreign.pk}/{self.q}').status_code, 404)
        self.assertEqual(self.manager.get(ALIASES + self.q).json()['results'], [])
        foreign.refresh_from_db()
        self.assertEqual(foreign.alias, 'LLA')

    def test_the_vocabulary_is_the_companys_and_has_no_costs(self):
        self.alias('option', 'LLA', option_id=self.lla.pk)
        self.alias('model_word', 'tlf', replacement='telefono')
        catalog_aliases.create_alias(company=self.other, scope='option', alias='zona otra',
                                     option_id=self.other_option.pk, actor=self.other_staff)
        res = self.reader.get(VOCABULARY + self.q)
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        text = json.dumps(body, ensure_ascii=False)
        self.assertNotIn('zona otra', text)
        self.assertNotIn(str(self.other_option.pk) + ',', text.replace(' ', ''))
        for word in ('cost', 'price', 'stock', 'quantity', 'product_id'):
            self.assertNotIn(word, text)
        self.assertEqual(body['model_words'], [{'id': body['model_words'][0]['id'], 'alias': 'tlf',
                                                'replacement': 'telefono'}])
        region = {o['label']: o for o in body['options']['region']}
        self.assertEqual([a['alias'] for a in region['LL/A']['aliases']], ['LLA'])
        self.assertEqual(region['LL/A']['model_ids'], [self.model.pk])
        etag = res.headers['ETag']
        self.assertEqual(self.reader.get(VOCABULARY + self.q, HTTP_IF_NONE_MATCH=etag).status_code, 304)
        self.alias('option', 'japon', option_id=self.ja.pk)
        self.assertEqual(self.reader.get(VOCABULARY + self.q, HTTP_IF_NONE_MATCH=etag).status_code, 200)
        self.assertEqual(_client(self.nobody).get(VOCABULARY + self.q).status_code, 403)

    def test_resolve_reads_and_writes_nothing(self):
        self.alias('option', 'LLA', option_id=self.lla.pk)
        before = (Product.objects.count(), ProductModel.objects.count(), VariantOption.objects.count(),
                  CatalogAlias.objects.count(), StockUnit.objects.count(), BranchStock.objects.count(),
                  StockMovement.objects.count(), SupplierOffer.objects.count(), AdminAuditLog.objects.count())
        res = self.reader.post(RESOLVE + self.q, {'rows': [
            {'key': 'a', 'model_text': 'telefono x', 'capacity_text': '256gb', 'colour_text': 'azul',
             'sim_text': 'esim', 'region_text': 'LLA'},
            {'key': 'b', 'model_text': 'Teléfono X', 'capacity_text': '128 GB', 'colour_text': 'azul, rojo'},
        ]}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        rows = {r['key']: r for r in res.json()['rows']}
        self.assertEqual(rows['a']['configurations'][0]['status'], 'variante_existente')
        self.assertEqual(rows['a']['configurations'][0]['product_id'], self.full.pk)
        # «Rojo» is a colour of the company outside this palette: outside wins over incomplete.
        self.assertEqual([c['status'] for c in rows['b']['configurations']], ['incompleta', 'fuera_de_paleta'])
        self.assertEqual(rows['b']['configurations'][0]['missing'], ['sim', 'region'])
        self.assertEqual((res.json()['summary']['incompleta'], res.json()['summary']['fuera_de_paleta']), (1, 1))
        after = (Product.objects.count(), ProductModel.objects.count(), VariantOption.objects.count(),
                 CatalogAlias.objects.count(), StockUnit.objects.count(), BranchStock.objects.count(),
                 StockMovement.objects.count(), SupplierOffer.objects.count(), AdminAuditLog.objects.count())
        self.assertEqual(before, after)

    def test_resolve_refuses_amounts_quantities_and_identifiers(self):
        for field in ('quantity', 'confirmed_quantity', 'amount', 'cost', 'agreed_cost', 'commercial_price', 'imei',
                      'price', 'text'):
            with self.subTest(field):
                res = self.reader.post(RESOLVE + self.q, {'rows': [{'key': 'a', 'model_text': 'Teléfono X',
                                                                    field: '100'}]}, format='json')
                self.assertEqual(res.status_code, 400, field)
        for field in ('model_text', 'colour_text', 'sim_text', 'region_text', 'capacity_text'):
            with self.subTest(field):
                # Review finding 2: an identifier in ANY field is never searched nor echoed back.
                res = self.reader.post(RESOLVE + self.q, {'rows': [{'key': 'a', 'model_text': 'Teléfono X',
                                                                    field: 'x 356938035643809'}]}, format='json')
                self.assertEqual(res.status_code, 200)
                self.assertTrue(res.json()['rows'][0]['error'])
                self.assertNotIn('356938035643809', json.dumps(res.json()))
        for rows in ([], [{'key': str(i), 'model_text': 'x'} for i in range(301)], 'nope',
                     [{'key': 'a'}, {'key': 'a'}], [{'model_text': 'x'}], [{'key': 'a', 'model_text': 'x' * 200}]):
            with self.subTest(rows=str(rows)[:40]):
                self.assertEqual(self.reader.post(RESOLVE + self.q, {'rows': rows}, format='json').status_code, 400)
        self.assertEqual(_client(self.nobody).post(RESOLVE + self.q, {'rows': [{'key': 'a', 'model_text': 'x'}]},
                                                   format='json').status_code, 403)

    def test_resolve_reads_many_models_in_bounded_queries(self):
        # Review finding 10: the palettes and variants of every model of the request in a few queries.
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        for n in range(30):
            device_catalog.create_model(company=self.company, name=f'Modelo {n}')

        def cost(count):
            rows = [{'key': str(n), 'model_text': f'Modelo {n}', 'colour_text': 'azul'} for n in range(count)]
            with CaptureQueriesContext(connection) as captured:
                self.assertEqual(self.reader.post(RESOLVE + self.q, {'rows': rows}, format='json').status_code, 200)
            return len(captured)

        self.assertEqual(cost(3), cost(30))

    def test_options_in_the_name_go_to_review(self):
        res = self.reader.post(RESOLVE + self.q, {'rows': [
            {'key': 'a', 'model_text': 'Teléfono X Azul', 'capacity_text': '256 GB', 'sim_text': 'eSIM',
             'region_text': 'LL/A'}]}, format='json')
        config = res.json()['rows'][0]['configurations'][0]
        self.assertEqual((config['status'], config['complete'], config['product_id']),
                         ('nombre_con_opciones', False, None))
        self.assertEqual([c['product_id'] for c in config['candidates']], [self.full.pk])
        self.assertEqual(res.json()['summary']['variante_existente'], 0)

    def test_resolve_in_another_company_sees_only_its_catalogue(self):
        self.alias('option', 'LLA', option_id=self.lla.pk)
        res = _client(self.other_staff).post(f'{RESOLVE}?company={self.other.pk}', {'rows': [
            {'key': 'a', 'model_text': 'Teléfono X', 'region_text': 'LLA', 'colour_text': 'azul'}]}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        config = res.json()['rows'][0]['configurations'][0]
        self.assertEqual(res.json()['rows'][0]['model']['model_id'], self.other_model.pk)
        self.assertEqual(config['status'], 'opcion_desconocida')
        ours = set(VariantOption.objects.filter(company=self.company).values_list('pk', flat=True))
        self.assertFalse(set(config['option_ids']) & ours)

    def test_list_marks_a_shadowed_alias(self):
        made = self.alias('option', 'Celeste', option_id=self.blue.pk)
        # A label written behind the service's back (an old release, a script) now equals the alias.
        VariantOption.objects.filter(pk=self.red.pk).update(label='Celeste')
        row = next(a for a in self.reader.get(ALIASES + self.q).json()['results'] if a['id'] == made.pk)
        self.assertTrue(row['shadowed'])


# ---------------------------------------------------------------------------
# The readers that already exist
# ---------------------------------------------------------------------------

class VariantSpecTest(AliasShop, TestCase):
    def setUp(self):
        self.build('a2-spec')

    def spec(self, model, *labels, notes=None):
        return device_catalog.resolve_variant_spec(self.company, model_name=model, labels=labels, notes=notes)

    def test_what_resolved_before_resolves_the_same(self):
        spec = self.spec('Teléfono X', '256 GB', 'Azul', 'eSIM', 'LL/A')
        self.assertEqual(spec['option_ids'], sorted([self.gb256.pk, self.blue.pk, self.esim.pk, self.lla.pk]))
        self.assertEqual(set(spec), {'model_id', 'model_name', 'condition_line', 'option_ids', 'variant_key'})

    def test_folded_labels_capacity_and_aliases_and_what_was_applied(self):
        self.alias('option', 'LLA', option_id=self.lla.pk)
        self.alias('model_word', 'tlf', replacement='telefono')
        notes = []
        spec = self.spec('TLF X', '256gb', 'AZÚL', 'esim', 'lla', notes=notes)
        self.assertEqual(spec['option_ids'], sorted([self.gb256.pk, self.blue.pk, self.esim.pk, self.lla.pk]))
        self.assertEqual(spec['model_id'], self.model.pk)
        said = ' '.join(notes)
        self.assertIn('«lla» se leyó como «LL/A» (alias de la empresa)', said)
        self.assertIn('«TLF X» se leyó como «Teléfono X»', said)

    def test_options_in_the_name_are_refused_never_a_new_model(self):
        with self.assertRaises(device_catalog.CatalogError) as caught:
            self.spec('Teléfono X (eSIM LL/A)', '256 GB')
        self.assertEqual((caught.exception.field, caught.exception.code), ('model', 'name_with_options'))
        self.assertIn('Teléfono X', str(caught.exception))

    def test_unknown_still_says_what_is_unknown(self):
        with self.assertRaises(device_catalog.CatalogError) as caught:
            self.spec('Teléfono X', 'lla')
        self.assertIn('No existe la opción «lla»', str(caught.exception))


class ProductImportTest(AliasShop, TestCase):
    def setUp(self):
        self.build('a2-carga')

    def test_an_old_template_loads_as_before_and_aliases_are_said(self):
        self.alias('option', 'LLA', option_id=self.lla.pk)
        job = self.preview(OLD_HEADERS, [['', 'Teléfono X 128 GB Negro', '3000', 'Teléfono X', '128 GB', 'Negro',
                                          'Nuevo']])
        row = job.rows.get()
        self.assertEqual(row.errors, [])
        self.assertEqual(row.normalized_data['variant']['option_ids'], sorted([self.gb128.pk, self.black.pk]))
        before = self.counts()
        job = self.preview(NEW_HEADERS, [['', 'Teléfono X 256 GB Azul eSIM LL/A', '3700', 'telefono x', '256gb',
                                          'azul', 'esim', 'lla', 'Nuevo']])
        row = job.rows.get()
        self.assertEqual(row.normalized_data['product_id'], self.full.pk, (row.errors, row.warnings))
        self.assertTrue(any('«lla» se leyó como «LL/A»' in w for w in row.warnings), row.warnings)
        self.assertEqual(self.counts(), before)


class OfferImportTest(AliasShop, TestCase):
    def setUp(self):
        self.build('a2-ofertas')
        self.staff_caps = _p2d_member(self.company, 'a2-ofertas-costs', [
            'company.view', 'products.view', 'products.manage', 'inventory.view', 'costs.view'])[0]
        self.client_ = _client(self.staff_caps)
        self.supplier = supply_services.create_supplier(company=self.company, name='Mayorista Uno', actor=self.staff)
        self.start = timezone.localdate()

    def row(self, model, capacity, colour, sim, region):
        return ['Mayorista Uno', '', model, capacity, colour, sim, region, 'Nuevo', '', '3900', '',
                self.start.isoformat(), (self.start + timedelta(days=5)).isoformat(), 3]

    def preview(self, rows):
        res = self.client_.post('/api/admin/supplier-offers/import/preview/', {
            'file': upload(OFFER_HEADERS, rows, title='Ofertas de proveedor'),
            'message_date': self.start.isoformat()}, format='multipart')
        self.assertEqual(res.status_code, 200, res.content)
        return res.json()['rows']

    def test_aliases_resolve_offers_and_are_said(self):
        self.alias('option', 'LLA', option_id=self.lla.pk)
        offers = SupplierOffer.objects.count()
        row = self.preview([self.row('TELEFONO X', '256gb', 'azul', 'esim', 'LLA')])[0]
        self.assertEqual(row['errors'], [])
        self.assertEqual(row['data']['product_id'], self.full.pk)
        self.assertTrue(any('«LLA» se leyó como «LL/A»' in w for w in row['warnings']), row['warnings'])
        self.assertEqual(SupplierOffer.objects.count(), offers)

    def test_a_partial_description_is_incomplete_and_never_picks_the_variant(self):
        """Line 11 (H3): one variant fits in part; the offer says what is missing and does not choose it."""
        row = self.preview([self.row('Teléfono X', '256 GB', 'Azul', 'eSIM', '')])[0]
        self.assertIsNone(row['data'].get('product_id'))
        self.assertTrue(any('Falta «Región»' in e and 'nunca se elige sola' in e for e in row['errors']), row['errors'])

    def test_an_offer_on_a_pending_draft_warns_and_never_sells_it(self):
        draft = device_catalog.create_variant_product(
            self.company, self.model, condition_line='new',
            option_ids=[self.gb128.pk, self.blue.pk, self.chip.pk, self.ja.pk], actor=self.staff, request=None)
        row = self.preview([self.row('Teléfono X', '128 GB', 'Azul', 'Chip', 'J/A')])[0]
        self.assertEqual(row['data']['product_id'], draft.pk, row)
        self.assertTrue(any('borrador' in w for w in row['warnings']), row['warnings'])
        draft.refresh_from_db()
        self.assertTrue(draft.is_pending_draft)
        self.assertFalse(supply_services.sells_on_order(draft))


class TextMatchTest(AliasShop, TestCase):
    URL = '/api/admin/inventory/text-preview/match/'

    def setUp(self):
        self.build('a2-texto')
        self.api_ = _client(self.staff)

    def one(self, **candidate):
        res = self.api_.post(self.URL, {'candidates': [{'key': 'c', 'kind': 'device', 'line': 'new', **candidate}]},
                             format='json')
        self.assertEqual(res.status_code, 200, res.content)
        return res.json()['matches']['c']

    def test_sim_and_region_are_read_through_aliases(self):
        self.alias('option', 'LLA', option_id=self.lla.pk)
        found = self.one(model='Teléfono X', capacityGb=256, colour='azul', sim='esim', region='LLA')
        self.assertEqual((found['status'], [o['product_id'] for o in found['options']]), ('exact', [self.full.pk]))
        self.assertEqual(found['configuration']['status'], 'variante_existente')

    def test_line_11_reads_alike_in_receiving_and_in_offers(self):
        found = self.one(model='Teléfono X', capacityGb=256, colour='azul', sim='esim')
        self.assertEqual(found['status'], 'ambiguous')          # to confirm by a person, never chosen
        self.assertEqual(found['configuration']['status'], 'incompleta')
        self.assertEqual(found['configuration']['missing'], ['region'])
        self.assertIsNone(found['configuration']['product_id'])

    def test_a_terabyte_capacity_reads_alike_in_both_answers(self):
        # Review finding 7: «1 TB» for 1024 GB in the match AND in its configuration.
        tb = device_catalog.create_option(company=self.company, attribute_id=self.capacity.pk, label='1 TB')
        found = self.one(model='Teléfono X', capacityGb=1024, colour='azul', sim='eSIM', region='LL/A')
        reasons = {(d['role'], d['reason']) for d in found['configuration']['doubts']}
        self.assertNotIn(('capacity', 'unknown'), reasons)
        self.assertIn(tb.pk, found['configuration']['option_ids'])

    def test_ram_is_still_refused(self):
        res = self.api_.post(self.URL, {'candidates': [{'key': 'c', 'model': 'Teléfono X', 'ramGb': 8}]},
                             format='json')
        self.assertEqual(res.status_code, 400)


class ProposalTest(AliasShop, TestCase):
    def setUp(self):
        self.build('a2-propuesta')

    def test_a_name_read_through_an_alias_or_with_options_is_flagged_and_apply_is_unchanged(self):
        self.alias('option', 'japon', option_id=self.ja.pk)
        self.alias('model_word', 'tlf', replacement='telefono')
        plan = catalog_proposal.propose(company=self.company, rows=[
            {'key': 'a', 'kind': 'device', 'line': 'new', 'model': 'TLF X', 'capacityGb': 128, 'colour': 'Negro'},
            {'key': 'b', 'kind': 'device', 'line': 'new', 'model': 'Teléfono X japon', 'capacityGb': 128,
             'colour': 'Negro'},
        ])
        rows = {r['key']: r for r in plan['rows']}
        self.assertTrue(any('«TLF X»' in w and '«Teléfono X»' in w for w in rows['a']['warnings']), rows['a'])
        self.assertTrue(any('«Teléfono X japon»' in w and 'J/A' in w for w in rows['b']['warnings']), rows['b'])
        models_before = ProductModel.objects.count()
        catalog_proposal.apply(company=self.company, rows=[
            {'key': 'b', 'kind': 'device', 'line': 'new', 'model': 'Teléfono X japon', 'capacityGb': 128,
             'colour': 'Negro'}], approve={'models': [], 'variants': []}, actor=self.staff)
        self.assertEqual(ProductModel.objects.count(), models_before)
