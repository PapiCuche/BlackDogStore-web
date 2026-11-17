"""
P2 · F5c — control de afirmaciones (Manual V4 §21) en el panel.

Lo que estas pruebas fijan:

  * `claims_check.find(text)` encuentra las frases vetadas o condicionadas, sin distinguir mayúsculas ni tildes y
    con límites de palabra: «originales» en «cargadores originales» sí, «original» dentro de otra palabra no;
  * devuelve la frase TAL COMO SE ESCRIBIÓ, la regla y el consejo en español con la referencia al manual;
  * `POST /api/admin/claims/check/` exige una capacidad de quien escribe textos de la tienda, limita el tamaño y el
    ritmo, y NUNCA bloquea: guardar un texto con una frase vetada sigue funcionando.

Nombres y textos inventados: nada aquí es de la tienda piloto.
"""
from unittest import mock

from django.core.cache import cache
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIClient

from store import claims_check
from store.models import ProductModel, StorefrontCampaign
from store.tests import _p2d_member, _p3_company

URL = '/api/admin/claims/check/'


def phrases(text):
    return [w['phrase'] for w in claims_check.find(text)]


def rules(text):
    return [w['rule'] for w in claims_check.find(text)]


class FindPositivesTest(SimpleTestCase):
    def test_official_and_authorised(self):
        self.assertEqual(rules('Somos distribuidor oficial de la marca.'), [claims_check.RULE_OFFICIAL])
        self.assertEqual(phrases('Somos distribuidor oficial de la marca.'), ['distribuidor oficial'])
        self.assertEqual(phrases('Tienda autorizada por el fabricante'), ['Tienda autorizada por'])
        self.assertEqual(phrases('Equipos autorizados por la marca'), ['autorizados por'])
        self.assertEqual(phrases('Personal autorizado'), [])
        self.assertEqual(phrases('Servicio técnico autorizado'), ['Servicio técnico autorizado'])
        self.assertEqual(phrases('Lo vendemos oficialmente'), ['oficialmente'])
        self.assertEqual(rules('Repuestos OFICIALES'), [claims_check.RULE_OFFICIAL])

    def test_condition_claims(self):
        for text, phrase in (
            ('Batería al 100 %', 'Batería al 100 %'),
            ('bateria al 100%', 'bateria al 100%'),
            ('Batería 100% de salud', 'Batería 100%'),
            ('Equipo impecable', 'impecable'),
            ('Están como nuevos', 'como nuevos'),
            ('Llega sin fallas', 'sin fallas'),
            ('En perfecto estado', 'perfecto'),
        ):
            with self.subTest(text=text):
                self.assertEqual(phrases(text), [phrase])
                self.assertEqual(rules(text), [claims_check.RULE_CONDITION])

    def test_guarantee_before_diagnosis(self):
        self.assertEqual(phrases('Reparación garantizada en 24 h'), ['Reparación garantizada'])
        self.assertEqual(phrases('Resultado garantizado'), ['garantizado'])
        self.assertEqual(phrases('Te lo garantizamos'), ['garantizamos'])
        self.assertEqual(rules('Resultado garantizado'), [claims_check.RULE_GUARANTEE])

    def test_origin_needs_proof(self):
        self.assertEqual(phrases('Usamos piezas originales'), ['piezas originales'])
        self.assertEqual(phrases('Calidad original'), ['Calidad original'])
        # The owner's example: «originales» after another noun is still a claim of origin.
        self.assertEqual(phrases('Incluye cargadores originales'), ['originales'])
        self.assertEqual(rules('Incluye cargadores originales'), [claims_check.RULE_ORIGIN])

    def test_warranty_terms(self):
        for text, phrase in (
            ('1 año de garantía', '1 año de garantía'),
            ('Con 6 meses de garantía', '6 meses de garantía'),
            ('Un año de garantia', 'Un año de garantia'),
            ('Garantía de 12 meses', 'Garantía de 12 meses'),
        ):
            with self.subTest(text=text):
                self.assertEqual(phrases(text), [phrase])
                self.assertEqual(rules(text), [claims_check.RULE_WARRANTY_TERM])

    def test_absolutes(self):
        self.assertEqual(phrases('Siempre el mejor precio'), ['Siempre'])
        self.assertEqual(phrases('Para todos los modelos y todas las marcas'), ['todos', 'todas'])
        self.assertEqual(phrases('Somos el número uno'), ['número uno'])
        self.assertEqual(phrases('La tienda #1 de la ciudad'), ['#1'])
        self.assertEqual(rules('Somos el numero 1'), [claims_check.RULE_ABSOLUTE])

    def test_markdown_does_not_hide_a_claim(self):
        self.assertEqual(phrases('## Estado\n\n- **Impecable**\n- _como nueva_'), ['Impecable', 'como nueva'])

    def test_several_rules_in_text_order(self):
        text = 'Distribuidor oficial. Batería al 100 %. Reparación garantizada. 1 año de garantía.'
        self.assertEqual(rules(text), [
            claims_check.RULE_OFFICIAL, claims_check.RULE_CONDITION, claims_check.RULE_GUARANTEE,
            claims_check.RULE_WARRANTY_TERM,
        ])


class FindNegativesTest(SimpleTestCase):
    def test_clean_text_has_no_warning(self):
        self.assertEqual(claims_check.find('Equipo revisado, batería al 87 %, con detalles leves en el marco.'), [])
        self.assertEqual(claims_check.find(''), [])
        self.assertEqual(claims_check.find(None), [])

    def test_word_boundaries(self):
        for text in (
            'Un diseño con mucha originalidad',
            'Originalmente venía en caja',
            'Arte aborigen y aboriginal',
            'La oficialía de partes',
            'Funciona perfectamente',
            'Imperfecto en el borde',
            'Batería al 1000 %',
            'Siempreviva',
            'Petodos',
            'Garantía según la política publicada',
            'Garantizar el envío',  # infinitive: not a promise made to the buyer
            '#12 en la lista',
        ):
            with self.subTest(text=text):
                self.assertEqual(claims_check.find(text), [])


class FindShapeTest(SimpleTestCase):
    def test_case_and_accents_are_ignored(self):
        self.assertEqual(rules('BATERÍA AL 100 %'), [claims_check.RULE_CONDITION])
        self.assertEqual(rules('reparacion garantizada'), [claims_check.RULE_GUARANTEE])
        self.assertEqual(rules('IMPECABLE'), [claims_check.RULE_CONDITION])
        self.assertEqual(rules('Número Uno'), [claims_check.RULE_ABSOLUTE])

    def test_each_warning_has_phrase_rule_and_advice_with_the_manual(self):
        (warning,) = claims_check.find('Batería al 100 %')
        self.assertEqual(set(warning), {'phrase', 'rule', 'advice'})
        self.assertIn('Manual V4 §21', warning['advice'])
        self.assertIn('inspeccionado', warning['advice'])

    def test_every_rule_has_spanish_advice_with_the_reference(self):
        self.assertEqual({r.code for r in claims_check.RULES}, {
            claims_check.RULE_OFFICIAL, claims_check.RULE_CONDITION, claims_check.RULE_GUARANTEE,
            claims_check.RULE_ORIGIN, claims_check.RULE_WARRANTY_TERM, claims_check.RULE_ABSOLUTE,
        })
        for rule in claims_check.RULES:
            self.assertIn('(Manual V4 §21)', rule.advice)

    def test_a_repeated_phrase_is_listed_once(self):
        self.assertEqual(phrases('Impecable. Muy impecable. IMPECABLE.'), ['Impecable'])

    def test_whitespace_inside_a_phrase_is_collapsed(self):
        self.assertEqual(phrases('batería al   100 %'), ['batería al 100 %'])

    def test_text_is_never_changed_and_nothing_is_raised_on_odd_input(self):
        text = '\x00 ** __ [enlace](https://x.example) ' * 50
        self.assertEqual(claims_check.find(text), [])
        self.assertEqual(claims_check.find(12345), [])


class ClaimsCheckApiTest(TestCase):
    def setUp(self):
        cache.clear()
        self.company = _p3_company('afirmaciones-tienda', 'Tienda Afirmaciones')
        self.other = _p3_company('afirmaciones-otra', 'Otra Tienda')
        self.catalog, _ = _p2d_member(self.company, 'af_catalogo', ['company.view', 'products.view', 'products.manage'])
        self.content, _ = _p2d_member(self.company, 'af_contenido', ['company.view', 'company.manage'])
        self.devices, _ = _p2d_member(self.company, 'af_equipos', ['inventory.view', 'inventory.adjust'])
        self.reader, _ = _p2d_member(self.company, 'af_lector', ['company.view', 'products.view'])
        self.outsider, _ = _p2d_member(self.other, 'af_ajeno', ['company.view', 'products.manage'])

    def post(self, user, body, company=None):
        client = APIClient()
        if user is not None:
            client.force_authenticate(user=user)
        query = f'?company={company.pk}' if company is not None else ''
        return client.post(URL + query, body, format='json')

    def test_returns_the_warnings(self):
        res = self.post(self.catalog, {'text': 'Equipo impecable, batería al 100 %'})
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual([w['phrase'] for w in res.json()['warnings']], ['impecable', 'batería al 100 %'])
        self.assertTrue(all('Manual V4 §21' in w['advice'] for w in res.json()['warnings']))

    def test_a_clean_text_returns_an_empty_list(self):
        res = self.post(self.catalog, {'text': 'Equipo revisado con detalles leves.'})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), {'warnings': []})

    def test_every_writer_of_shop_texts_may_check(self):
        for user in (self.catalog, self.content, self.devices):
            with self.subTest(user=user.username):
                self.assertEqual(self.post(user, {'text': 'impecable'}).status_code, 200)

    def test_permission(self):
        self.assertIn(self.post(None, {'text': 'impecable'}).status_code, (401, 403))
        self.assertEqual(self.post(self.reader, {'text': 'impecable'}).status_code, 403)
        # Naming a company the caller does not belong to never answers.
        self.assertIn(self.post(self.outsider, {'text': 'impecable'}, company=self.company).status_code, (403, 404))
        self.assertEqual(self.post(self.outsider, {'text': 'impecable'}, company=self.other).status_code, 200)

    def test_only_post(self):
        client = APIClient()
        client.force_authenticate(user=self.catalog)
        self.assertEqual(client.get(URL).status_code, 405)

    def test_the_text_must_be_text(self):
        self.assertEqual(self.post(self.catalog, {}).status_code, 400)
        self.assertEqual(self.post(self.catalog, {'text': ['impecable']}).status_code, 400)
        self.assertEqual(self.post(self.catalog, {'text': ''}).json(), {'warnings': []})

    def test_size_limit(self):
        at_limit = 'a ' * (claims_check.MAX_TEXT_LENGTH // 2)
        self.assertEqual(len(at_limit), claims_check.MAX_TEXT_LENGTH)
        self.assertEqual(self.post(self.catalog, {'text': at_limit}).status_code, 200)
        res = self.post(self.catalog, {'text': at_limit + 'x'})
        self.assertEqual(res.status_code, 413)
        self.assertIn('20', res.json()['detail'])

    def test_rate_limit(self):
        from store.throttles import AdminClaimsCheckThrottle

        with mock.patch.object(AdminClaimsCheckThrottle, 'THROTTLE_RATES', {'admin_claims_check': '2/min'}):
            self.assertEqual(self.post(self.catalog, {'text': 'a'}).status_code, 200)
            self.assertEqual(self.post(self.catalog, {'text': 'a'}).status_code, 200)
            self.assertEqual(self.post(self.catalog, {'text': 'a'}).status_code, 429)

    def test_checking_writes_nothing(self):
        from store.models import AdminAuditLog

        before = AdminAuditLog.objects.count()
        self.post(self.catalog, {'text': 'Distribuidor oficial'})
        self.assertEqual(AdminAuditLog.objects.count(), before)


class ClaimsNeverBlockTest(TestCase):
    """A flagged phrase is advice: every save that accepted the text before still accepts it."""

    TEXT = 'Distribuidor oficial. Batería al 100 %, impecable. Reparación garantizada. 1 año de garantía.'

    def setUp(self):
        cache.clear()
        self.company = _p3_company('afirmaciones-guardar', 'Tienda Guardar')
        self.user, _ = _p2d_member(self.company, 'af_guardar', [
            'company.view', 'company.manage', 'products.view', 'products.manage'])
        self.api = APIClient()
        self.api.force_authenticate(user=self.user)
        self.q = f'?company={self.company.pk}'

    def test_a_model_description_with_claims_is_saved(self):
        res = self.api.post(f'/api/admin/catalog/models/{self.q}', {'name': 'Equipo Z', 'description': self.TEXT},
                            format='json')
        self.assertEqual(res.status_code, 201, res.content)
        model = ProductModel.objects.get(company=self.company, name='Equipo Z')
        self.assertEqual(model.description, self.TEXT)
        res = self.api.patch(f'/api/admin/catalog/models/{model.pk}/{self.q}', {'description': self.TEXT + ' Siempre.'},
                             format='json')
        self.assertEqual(res.status_code, 200, res.content)

    def test_a_campaign_with_claims_is_saved(self):
        res = self.api.post(f'/api/admin/storefront/campaigns/{self.q}', {
            'slot': StorefrontCampaign.Slot.HOME_PROMO, 'title': 'Distribuidor oficial', 'body': self.TEXT,
        }, format='json')
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(StorefrontCampaign.objects.get(company=self.company).body, self.TEXT)

    def test_a_grade_with_claims_is_saved(self):
        res = self.api.post(f'/api/admin/catalog/grades/{self.q}', {
            'code': 'impecable', 'label': 'Impecable', 'description': 'Como nuevo, sin fallas.',
        }, format='json')
        self.assertEqual(res.status_code, 201, res.content)
