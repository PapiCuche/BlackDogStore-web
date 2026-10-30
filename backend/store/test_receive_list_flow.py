"""
INV-RECEIVE-UX-02 — the pasted list, from the catalogue to the shelf, across the REAL endpoints.

    match -> proposal -> apply (approved part only) -> match again -> receipts/preview -> receipts/confirm

The screen decides nothing here: every rule is the server's. What these tests hold, for the owner's case of
ELEVEN used phones (some variants shared, some missing):

  * the catalogue answers one question per combination and never sees an identifier;
  * what is missing is created ONCE (variants shared by several devices, no article per physical device) and
    only by who may manage products; the creation is hidden from the web;
  * the eleven devices enter in ONE idempotent confirmation: eleven units, one movement and one label batch per
    article, battery / grade / notes stored on each unit, and a replay or a double click adds nothing;
  * another company never sees these variants, a foreign product or branch answers like one that does not exist,
    a cost needs `costs.view`, and a repeated or already-registered IMEI refuses the whole receipt.

Everything fictitious: invented serials, IMEI built for the test, no commercial data.
"""
import itertools

from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

from store.models import (
    AdminAuditLog, CosmeticGrade, LabelBatch, Product, StockMovement, StockUnit,
)
from store.test_catalog_proposal import APPLY, MATCH, PROPOSE
from store.test_device_commerce import DeviceShop, assert_no_amount
from store.test_registration import imei, _client
from store.tests import _p2d_member, _p3_company

PREVIEW = '/api/admin/inventory/receipts/preview/'
CONFIRM = '/api/admin/inventory/receipts/confirm/'


def candidate(key, model, gb, colour, line='used'):
    return {'key': key, 'kind': 'device', 'model': model, 'capacityGb': gb, 'colour': colour, 'line': line}


#: eleven phones, five combinations: one exists (3), four do not (2 + 2 + 2 + 2).
ELEVEN = (
    [('Teléfono X', 128, 'azul', 'used')] * 3          # exists: used128
    + [('Teléfono X', 256, 'negro', 'used')] * 2       # the model and both options exist, the variant does not
    + [('Teléfono X', 128, 'negro', 'used')] * 2       # same
    + [('Teléfono Y', 128, 'negro', 'used')] * 2       # the MODEL does not exist
    + [('Teléfono Y', 256, 'azul', 'used')] * 2        # the same missing model, another variant of it
)


class _Flow(DeviceShop, TestCase):
    def setUp(self):
        self.build('rux-flow')
        self.api = _client(self.staff)
        self.imeis = (imei(f'35693803564{n:03d}') for n in itertools.count(100))

    # -- the catalogue --------------------------------------------------------

    def match(self, rows, client=None):
        res = (client or self.api).post(MATCH, {'candidates': rows}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        return res.json()['matches']

    def groups(self):
        """One candidate per combination, as the screen asks (never per device)."""
        seen, out = {}, []
        for model, gb, colour, line in ELEVEN:
            if (model, gb, colour, line) not in seen:
                seen[(model, gb, colour, line)] = str(len(seen) + 1)
                out.append(candidate(seen[(model, gb, colour, line)], model, gb, colour, line))
        return out

    def resolve_everything(self):
        """The screen's own sequence: ask, propose, approve everything, ask again."""
        rows = self.groups()
        first = self.match(rows)
        missing = [r for r in rows if first[r['key']]['status'] != 'exact']
        if missing:
            plan = self.api.post(PROPOSE, {'rows': missing}, format='json').json()['plan']
            approve = {'models': [m['ref'] for m in plan['models'] if m['status'] == 'missing'],
                       'variants': [v['ref'] for v in plan['variants'] if v['status'] == 'missing']}
            res = self.api.post(APPLY, {'rows': missing, 'approve': approve}, format='json', HTTP_IDEMPOTENCY_KEY='rux-1')
            self.assertEqual(res.status_code, 200, res.content)
        return rows, self.match(rows)

    # -- the receipt ----------------------------------------------------------

    def unit_row(self, **extra):
        value = next(self.imeis)
        return {'serial': f'RUX{value[-8:]}', 'imei': value, 'condition': 'used', 'battery_health': '91',
                'cosmetic_grade': self.grade.pk, 'notes': 'caja sin cargador', **extra}

    def receipt(self, answers, rows):
        """The receipt the screen would build: one line per article, one unit per physical device."""
        per_product = {}
        for model, gb, colour, line in ELEVEN:
            key = next(r['key'] for r in rows if (r['model'], r['capacityGb'], r['colour'], r['line']) == (model, gb, colour, line))
            product = answers[key]['options'][0]['product_id']
            per_product.setdefault(product, []).append(self.unit_row())
        return {'branch': self.branch.pk, 'supplier': None, 'document_ref': 'LISTA-11', 'notes': '',
                'lines': [{'kind': 'units', 'product': pk, 'units': units} for pk, units in per_product.items()]}


class ElevenPhonesTest(_Flow):
    def test_the_catalogue_answers_once_per_combination_and_only_one_is_exact_before_creating(self):
        rows = self.groups()
        self.assertEqual(len(rows), 5)
        answers = self.match(rows)
        self.assertEqual([answers[r['key']]['status'] for r in rows], ['exact', 'none', 'none', 'none', 'none'])
        self.assertEqual(answers['1']['options'][0]['product_id'], self.used128.pk)
        # What the screen needs to build a line comes in the same answer.
        self.assertEqual({k: answers['1']['options'][0][k] for k in ('is_serialized', 'requires_imei', 'is_active')},
                         {'is_serialized': True, 'requires_imei': True, 'is_active': True})

    def test_missing_variants_are_created_once_and_hidden_from_the_web(self):
        before = Product.objects.filter(company=self.company).count()
        rows, answers = self.resolve_everything()
        # Four combinations were missing: four articles, however many devices share each.
        self.assertEqual(Product.objects.filter(company=self.company).count(), before + 4)
        self.assertEqual([answers[r['key']]['status'] for r in rows], ['exact'] * 5)
        created = Product.objects.filter(pk__in=[answers[k]['options'][0]['product_id'] for k in ('2', '3', '4', '5')])
        self.assertEqual(created.count(), 4)
        self.assertFalse(created.filter(is_published_online=True).exists())
        self.assertTrue(all(p.is_serialized and p.requires_imei for p in created))

    def test_the_eleven_devices_enter_in_one_confirmation_with_their_own_data(self):
        rows, answers = self.resolve_everything()
        body = self.receipt(answers, rows)
        preview = self.api.post(PREVIEW, body, format='json')
        self.assertEqual(preview.status_code, 200, preview.content)
        self.assertTrue(preview.json()['ok'])
        self.assertEqual((preview.json()['totals']['devices'], preview.json()['totals']['labels']), (11, 11))
        self.assertEqual(StockUnit.objects.filter(company=self.company, created_by=self.staff).count(), 0)  # the preview wrote nothing

        res = self.api.post(CONFIRM, body, format='json', HTTP_IDEMPOTENCY_KEY='rux-receipt-1')
        self.assertEqual(res.status_code, 201, res.content)
        data = res.json()
        self.assertEqual(data['totals']['devices'], 11)
        self.assertEqual(len(data['lines']), 5)
        self.assertFalse(data['label_error'])

        units = StockUnit.objects.filter(company=self.company, created_by=self.staff)
        self.assertEqual(units.count(), 11)
        self.assertEqual(len({u.imei for u in units}), 11)
        self.assertEqual({u.battery_health for u in units}, {91})
        self.assertEqual({u.cosmetic_grade_id for u in units}, {self.grade.pk})
        self.assertEqual({u.notes for u in units}, {'caja sin cargador'})
        self.assertEqual({u.condition for u in units}, {'used'})
        # One Kardex movement and one label batch per article (five) — and exactly eleven labelled devices.
        movements = StockMovement.objects.filter(company=self.company, movement_type=StockMovement.PURCHASE_ENTRY)
        self.assertEqual(movements.count(), 5)
        self.assertEqual(sum(m.quantity for m in movements), 11)
        self.assertEqual(LabelBatch.objects.filter(company=self.company).count(), 5)
        self.assertEqual(sum(b.items.count() for b in LabelBatch.objects.filter(company=self.company)), 11)
        # The trail keeps ids and counts, never an identifier — and it belongs to the company (AGENTS.md §3).
        trail = AdminAuditLog.objects.get(company=self.company, action='inventory_receipt_confirmed')
        self.assertEqual(trail.company_id, self.company.pk)
        self.assertNotIn(body['lines'][0]['units'][0]['imei'], str(trail.metadata))
        self.assertNotIn(body['lines'][0]['units'][0]['serial'], str(trail.metadata))

    def test_a_double_click_or_a_lost_answer_adds_nothing(self):
        rows, answers = self.resolve_everything()
        body = self.receipt(answers, rows)
        first = self.api.post(CONFIRM, body, format='json', HTTP_IDEMPOTENCY_KEY='rux-receipt-2')
        again = self.api.post(CONFIRM, body, format='json', HTTP_IDEMPOTENCY_KEY='rux-receipt-2')
        self.assertEqual((first.status_code, again.status_code), (201, 200))
        self.assertTrue(again.json()['replayed'])
        self.assertEqual(again.json()['receipt_id'], first.json()['receipt_id'])
        self.assertEqual(StockUnit.objects.filter(company=self.company, created_by=self.staff).count(), 11)
        self.assertEqual(StockMovement.objects.filter(company=self.company, movement_type=StockMovement.PURCHASE_ENTRY).count(), 5)
        # The same key with another content is refused, and adds nothing either.
        other = self.receipt(answers, rows)
        conflict = self.api.post(CONFIRM, other, format='json', HTTP_IDEMPOTENCY_KEY='rux-receipt-2')
        self.assertEqual((conflict.status_code, conflict.json()['code']), (409, 'idempotency_conflict'))
        self.assertEqual(StockUnit.objects.filter(company=self.company, created_by=self.staff).count(), 11)

    def test_two_devices_of_one_variant_are_not_duplicates_but_a_repeated_imei_refuses_everything(self):
        rows, answers = self.resolve_everything()
        body = self.receipt(answers, rows)
        clash = body['lines'][0]['units'][1]
        clash['imei'] = body['lines'][0]['units'][0]['imei']
        res = self.api.post(CONFIRM, body, format='json', HTTP_IDEMPOTENCY_KEY='rux-receipt-3')
        self.assertEqual((res.status_code, res.json()['code']), (400, 'invalid_lines'))
        self.assertFalse(res.json()['ok'])
        issues = [i for line in res.json()['lines'] for i in line.get('unit_issues', [])]
        self.assertTrue(any(i['kind'] == 'duplicate' and i['field'] == 'imei' for i in issues))
        self.assertEqual(StockUnit.objects.filter(company=self.company, created_by=self.staff).count(), 0)
        self.assertEqual(StockMovement.objects.filter(company=self.company, movement_type=StockMovement.PURCHASE_ENTRY).count(), 0)

    def test_an_imei_already_in_stock_refuses_the_receipt_and_changes_nothing(self):
        rows, answers = self.resolve_everything()
        held = self.receive(self.used128)[0]
        body = self.receipt(answers, rows)
        body['lines'][0]['units'][0]['imei'] = held.imei
        res = self.api.post(CONFIRM, body, format='json', HTTP_IDEMPOTENCY_KEY='rux-receipt-4')
        self.assertEqual((res.status_code, res.json()['code']), (400, 'invalid_lines'), res.content)
        self.assertTrue(any(i['kind'] == 'duplicate' for line in res.json()['lines'] for i in line['unit_issues']))
        self.assertEqual(StockUnit.objects.filter(company=self.company).count(), 1)  # only the one that was already there

    def test_a_battery_out_of_range_or_an_unknown_grade_is_refused_with_the_device_it_names(self):
        rows, answers = self.resolve_everything()
        for field, value in (('battery_health', '140'), ('cosmetic_grade', 999999)):
            with self.subTest(field):
                body = self.receipt(answers, rows)
                body['lines'][0]['units'][0][field] = value
                res = self.api.post(PREVIEW, body, format='json')
                self.assertEqual(res.status_code, 200, res.content)
                self.assertFalse(res.json()['ok'])
                issue = res.json()['lines'][0]['unit_issues'][0]
                self.assertEqual((issue['unit'], issue['field']), (0, field))
        self.assertEqual(StockUnit.objects.filter(company=self.company).count(), 0)

    def test_more_than_two_hundred_devices_is_refused_whole_never_cut(self):
        rows, answers = self.resolve_everything()
        pk = answers['1']['options'][0]['product_id']
        # Valid in every other way: the cap is the ONLY reason to refuse (identifiers present, all distinct).
        units = [{'serial': f'BULK{n:04d}', 'imei': imei(f'35693803565{n:03d}'), 'condition': 'used'} for n in range(201)]
        body = {'branch': self.branch.pk, 'lines': [{'kind': 'units', 'product': pk, 'units': units}]}
        res = self.api.post(CONFIRM, body, format='json', HTTP_IDEMPOTENCY_KEY='rux-receipt-5')
        self.assertEqual(res.status_code, 400, res.content)
        self.assertIn('200 equipos', res.json()['detail'])
        self.assertEqual(StockUnit.objects.filter(company=self.company).count(), 0)
        # The same list without the last unit is accepted: the cap is exactly 200.
        ok = {**body, 'lines': [{**body['lines'][0], 'units': units[:200]}]}
        self.assertEqual(self.api.post(CONFIRM, ok, format='json', HTTP_IDEMPOTENCY_KEY='rux-receipt-6').status_code, 201)
        self.assertEqual(StockUnit.objects.filter(company=self.company).count(), 200)


class PermissionsAndTenancyTest(_Flow):
    def test_without_products_manage_the_catalogue_is_never_created_but_existing_variants_still_receive(self):
        reader, _ = _p2d_member(self.company, 'rux_reader', ['products.view', 'inventory.view', 'inventory.adjust'])
        client = _client(reader)
        rows = self.groups()
        before = Product.objects.filter(company=self.company).count()
        plan = client.post(PROPOSE, {'rows': rows[1:]}, format='json')
        self.assertEqual(plan.status_code, 200)  # reading a proposal writes nothing
        refs = {'models': [], 'variants': [v['ref'] for v in plan.json()['plan']['variants'] if v['status'] == 'missing']}
        denied = client.post(APPLY, {'rows': rows[1:], 'approve': refs}, format='json', HTTP_IDEMPOTENCY_KEY='rux-2')
        self.assertEqual(denied.status_code, 403, denied.content)
        self.assertEqual(Product.objects.filter(company=self.company).count(), before)
        # What exists can still be received by this person.
        body = {'branch': self.branch.pk, 'lines': [{'kind': 'units', 'product': self.used128.pk, 'units': [self.unit_row()]}]}
        self.assertEqual(client.post(CONFIRM, body, format='json', HTTP_IDEMPOTENCY_KEY='rux-3').status_code, 201)

    def test_creating_variants_needs_products_manage_and_inventory_adjust(self):
        manager, _ = _p2d_member(self.company, 'rux_manager', ['products.view', 'products.manage', 'inventory.view'])
        rows = self.groups()[1:]
        plan = _client(manager).post(PROPOSE, {'rows': rows}, format='json').json()['plan']
        refs = {'models': [], 'variants': [v['ref'] for v in plan['variants'] if v['status'] == 'missing']}
        before = Product.objects.filter(company=self.company).count()
        res = _client(manager).post(APPLY, {'rows': rows, 'approve': refs}, format='json', HTTP_IDEMPOTENCY_KEY='rux-9')
        self.assertEqual(res.status_code, 403, res.content)
        self.assertEqual(Product.objects.filter(company=self.company).count(), before)

    def test_without_inventory_adjust_nothing_is_received(self):
        viewer, _ = _p2d_member(self.company, 'rux_viewer', ['products.view', 'inventory.view'])
        body = {'branch': self.branch.pk, 'lines': [{'kind': 'units', 'product': self.used128.pk, 'units': [self.unit_row()]}]}
        res = _client(viewer).post(CONFIRM, body, format='json', HTTP_IDEMPOTENCY_KEY='rux-4')
        self.assertEqual(res.status_code, 403, res.content)
        self.assertEqual(StockUnit.objects.filter(company=self.company).count(), 0)

    def test_another_company_neither_matches_nor_receives_into_these_variants(self):
        other = _p3_company('rux-other', 'Otra tienda')
        stranger, _ = _p2d_member(other, 'rux_stranger', ['products.view', 'products.manage', 'inventory.view', 'inventory.adjust'])
        client = _client(stranger)
        answers = client.post(MATCH, {'candidates': self.groups()}, format='json').json()['matches']
        self.assertTrue(all(a['status'] == 'none' and a['options'] == [] for a in answers.values()))
        foreign = {'branch': other.default_inventory_branch.pk, 'lines': [
            {'kind': 'units', 'product': self.used128.pk, 'units': [self.unit_row()]}]}
        res = client.post(CONFIRM, foreign, format='json', HTTP_IDEMPOTENCY_KEY='rux-5')
        self.assertEqual((res.status_code, res.json()['code']), (404, 'not_found'))
        # And a foreign branch answers like one that does not exist for this company's own person.
        own = {'branch': other.default_inventory_branch.pk, 'lines': [
            {'kind': 'units', 'product': self.used128.pk, 'units': [self.unit_row()]}]}
        res = self.api.post(CONFIRM, own, format='json', HTTP_IDEMPOTENCY_KEY='rux-6')
        self.assertEqual(res.status_code, 404, res.content)
        self.assertEqual(StockUnit.objects.count(), 0)

    def test_a_cost_needs_the_cost_permission_and_is_never_echoed(self):
        body = {'branch': self.branch.pk, 'lines': [{'kind': 'units', 'product': self.used128.pk,
                                                      'units': [self.unit_row(unit_cost='2317.45')]}]}
        denied = self.api.post(CONFIRM, body, format='json', HTTP_IDEMPOTENCY_KEY='rux-7')
        self.assertEqual((denied.status_code, denied.json()['code']), (400, 'costs_forbidden'))
        self.assertEqual(StockUnit.objects.count(), 0)
        costed, _ = _p2d_member(self.company, 'rux_costed', ['products.view', 'inventory.view', 'inventory.adjust', 'costs.view'])
        res = _client(costed).post(CONFIRM, body, format='json', HTTP_IDEMPOTENCY_KEY='rux-8')
        self.assertEqual(res.status_code, 201, res.content)
        assert_no_amount(self, res.json(), '2317.45')
        cache.clear()
