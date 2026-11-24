"""
SUPPLY-GUARDS-01 — what the offers screen and the weekly file say before an offer sells in a way nobody chose.

  * An offer of an INACTIVE supplier never sells (`valid_offer_q`), so it is never shown as «Vigente»: its state
    is `supplier_inactive`, the «Vigentes» filter leaves it out and `state=supplier_inactive` lists it.
  * A commercial price BELOW the agreed cost (a sale at a loss) is never saved in silence — not by the panel's
    create or edit, not by the weekly file's apply: 409 until the request says `confirm_below_cost` AND gives a
    reason (`below_cost_reason`, owner 2026-11-23; 400 `margin_reason_required` without one). The exception is
    audited with the actor, the offer, the price, the cost and the reason — the cost returned only to `costs.view`.
  * Who cannot see costs never learns the cost: not its amount and not whether a price is below it. Any answer
    that depended on the comparison would be an oracle (try prices until the answer flips). So for them the
    check is UNIFORM: changing the price of an offer that carries a cost they cannot see asks for the same
    generic confirmation, whatever the price.
  * A row of the weekly file whose supplier is inactive warns: the offer is recorded and sells nothing.
  * The export fills «Modelo», «Capacidad» and «Color» from the variant's option roles.
"""
import io
from datetime import timedelta
from decimal import Decimal

import openpyxl
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIClient

from store import supply_services as supply
from store.models import AdminAuditLog, BulkImportRow, SupplierOffer
from store.supplier_views import offer_state
from store.test_device_commerce import assert_no_amount
from store.test_supplier_offers import OFFER_HEADERS, SupplyShop, today
from store.tests import _p2d_member

D = Decimal
WHY = 'Liquidación de modelo anterior'


def exception_rows(**filters):
    """The audit rows of offers that carry a margin exception (second review of #402: never a row of its own)."""
    return AdminAuditLog.objects.filter(target_type='supplier_offer', metadata__has_key='margin_exception',
                                        **filters)


def exception_of(offer_pk, action):
    row = AdminAuditLog.objects.filter(target_type='supplier_offer', target_id=str(offer_pk),
                                       action=action).order_by('-pk').first()
    return row, row.metadata['margin_exception']


class GuardShop(SupplyShop):
    """The shop of DC3 (offer: cost 70, price 110), a manager without `costs.view` and API clients."""

    def build(self, slug='sg-tienda'):
        super().build(slug)
        self.seller, _ = _p2d_member(self.company, f'{slug}_sin_costos',
                                     ['company.view', 'products.view', 'products.manage'])
        self.api = self.as_(self.staff)
        self.blind = self.as_(self.seller)

    def as_(self, user):
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    def deactivate_supplier(self):
        supply.update_supplier(self.supplier, actor=self.staff, is_active=False)
        self.supplier.refresh_from_db()


# ---------------------------------------------------------------------------
# 1. The state of an offer of an inactive supplier
# ---------------------------------------------------------------------------

class SupplierInactiveStateTest(GuardShop, TestCase):
    def setUp(self):
        self.build()

    def ids(self, state):
        res = self.api.get(f'/api/admin/supplier-offers/?state={state}')
        self.assertEqual(res.status_code, 200, res.content)
        return [row['id'] for row in res.json()['results']]

    def test_an_offer_of_an_inactive_supplier_is_not_vigente(self):
        self.assertEqual(offer_state(self.offer), 'active')
        self.deactivate_supplier()
        self.offer.refresh_from_db()
        self.assertEqual(offer_state(self.offer), 'supplier_inactive')
        row = self.api.get(f'/api/admin/supplier-offers/{self.offer.pk}/').json()
        self.assertEqual(row['state'], 'supplier_inactive')
        # The one predicate of what sells agrees: nothing.
        self.assertFalse(SupplierOffer.objects.filter(supply.valid_offer_q(), pk=self.offer.pk).exists())

    def test_an_offer_switched_off_by_hand_stays_inactive_whatever_its_supplier(self):
        supply.deactivate_offer(self.offer, actor=self.staff, reason='Sin stock')
        self.deactivate_supplier()
        self.offer.refresh_from_db()
        self.assertEqual(offer_state(self.offer), 'inactive')

    def test_the_vigentes_filter_leaves_it_out_and_its_own_filter_lists_it(self):
        self.assertEqual(self.ids('active'), [self.offer.pk])
        self.assertEqual(self.ids('supplier_inactive'), [])
        self.deactivate_supplier()
        self.assertEqual(self.ids('active'), [])
        self.assertEqual(self.ids('supplier_inactive'), [self.offer.pk])
        # Switched off by hand: «Desactivada», not «Proveedor inactivo».
        supply.deactivate_offer(self.offer, actor=self.staff, reason='Sin stock')
        self.assertEqual(self.ids('supplier_inactive'), [])
        self.assertEqual(self.ids('inactive'), [self.offer.pk])

    def test_an_expired_offer_of_an_inactive_supplier_is_listed_once(self):
        # Review of #402: the filters follow `offer_state`'s order — switched on, supplier off: «Proveedor
        # inactivo», whatever its days; never also under «Vencidas».
        old = self.make_offer(valid_from=today() - timedelta(days=20), valid_until=today() - timedelta(days=10))
        self.assertEqual(self.ids('expired'), [old.pk])
        self.deactivate_supplier()
        old.refresh_from_db()
        self.assertEqual(offer_state(old), 'supplier_inactive')
        self.assertEqual(self.ids('expired'), [])
        self.assertEqual(sorted(self.ids('supplier_inactive')), sorted([self.offer.pk, old.pk]))

    def test_the_supplier_back_on_makes_it_vigente_again(self):
        self.deactivate_supplier()
        supply.update_supplier(self.supplier, actor=self.staff, is_active=True)
        self.assertEqual(self.ids('active'), [self.offer.pk])


# ---------------------------------------------------------------------------
# 2. A price below the cost — the panel
# ---------------------------------------------------------------------------

class BelowCostPanelTest(GuardShop, TestCase):
    def setUp(self):
        self.build()
        self.later = str(today() + timedelta(days=7))

    def new_offer(self, client, **extra):
        body = {'supplier_id': self.supplier.pk, 'product_id': self.case.pk, 'commercial_price': '100.00',
                'confirmed_quantity': 2, 'valid_from': self.later, 'valid_until': self.later,
                'lead_time_days': 3}
        body.update(extra)
        return client.post('/api/admin/supplier-offers/', body, format='json')

    def test_create_below_cost_asks_and_saves_only_when_confirmed(self):
        count = SupplierOffer.objects.count()
        res = self.new_offer(self.api, agreed_cost='120.00')
        self.assertEqual(res.status_code, 409, res.content)
        body = res.json()
        self.assertEqual(body['code'], 'below_cost_confirmation_required')
        self.assertEqual((body['commercial_price'], body['agreed_cost']), ('100.00', '120.00'))
        self.assertEqual(body['field'], 'commercial_price')
        self.assertEqual(SupplierOffer.objects.count(), count)
        res = self.new_offer(self.api, agreed_cost='120.00', confirm_below_cost=True, below_cost_reason=WHY)
        self.assertEqual(res.status_code, 201, res.content)
        made = SupplierOffer.objects.get(pk=res.json()['id'])
        self.assertEqual((made.commercial_price, made.agreed_cost), (D('100.00'), D('120.00')))
        audit, exception = exception_of(made.pk, 'supplier_offer_created')
        self.assertEqual((audit.actor, audit.company), (self.staff, self.company))
        self.assertEqual(exception, {'verdict': 'below_cost', 'reason': WHY, 'commercial_price': '100.00',
                                     'agreed_cost': '120.00', 'source': 'panel'})
        # Second review of #402: inside the offer's own audit row — no extra row, so no gap in the ids.
        self.assertEqual(AdminAuditLog.objects.filter(target_type='supplier_offer', target_id=str(made.pk)).count(), 1)

    def test_a_confirmation_without_a_reason_writes_nothing(self):
        count = SupplierOffer.objects.count()
        for reason in (None, '', '   ', 'abc', 'x' * 501):
            extra = {} if reason is None else {'below_cost_reason': reason}
            res = self.new_offer(self.api, agreed_cost='120.00', confirm_below_cost=True, **extra)
            self.assertEqual((res.status_code, res.json()['code'], res.json()['field']),
                             (400, 'margin_reason_required', 'below_cost_reason'), reason)
        self.assertEqual(SupplierOffer.objects.count(), count)
        url = f'/api/admin/supplier-offers/{self.offer.pk}/'
        res = self.api.patch(url, {'commercial_price': '60.00', 'confirm_below_cost': True}, format='json')
        self.assertEqual((res.status_code, res.json()['code']), (400, 'margin_reason_required'))
        res = self.blind.patch(url, {'commercial_price': '60.00', 'confirm_below_cost': True}, format='json')
        self.assertEqual((res.status_code, res.json()['code']), (400, 'margin_reason_required'))
        assert_no_amount(self, res.json(), '70')
        self.offer.refresh_from_db()
        self.assertEqual(self.offer.commercial_price, D('110.00'))
        self.assertFalse(exception_rows().exists())

    def test_a_price_at_or_above_the_cost_or_without_cost_is_saved_as_always(self):
        self.assertEqual(self.new_offer(self.api, agreed_cost='100.00').status_code, 201)
        SupplierOffer.objects.exclude(pk=self.offer.pk).delete()
        self.assertEqual(self.new_offer(self.api, agreed_cost=None).status_code, 201)
        self.assertFalse(exception_rows().exists())

    def test_edit_below_cost_by_price_or_by_cost_asks(self):
        url = f'/api/admin/supplier-offers/{self.offer.pk}/'
        res = self.api.patch(url, {'commercial_price': '60.00'}, format='json')
        self.assertEqual((res.status_code, res.json()['code']), (409, 'below_cost_confirmation_required'))
        self.assertEqual((res.json()['commercial_price'], res.json()['agreed_cost']), ('60.00', '70.00'))
        res = self.api.patch(url, {'agreed_cost': '200.00'}, format='json')
        self.assertEqual((res.status_code, res.json()['code']), (409, 'below_cost_confirmation_required'))
        self.offer.refresh_from_db()
        self.assertEqual((self.offer.commercial_price, self.offer.agreed_cost), (D('110.00'), D('70.00')))
        res = self.api.patch(url, {'commercial_price': '60.00', 'confirm_below_cost': True,
                                   'below_cost_reason': WHY}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.offer.refresh_from_db()
        self.assertEqual(self.offer.commercial_price, D('60.00'))
        audit, exception = exception_of(self.offer.pk, 'supplier_offer_updated')
        self.assertEqual(audit.actor, self.staff)
        self.assertEqual((exception['verdict'], exception['commercial_price'], exception['agreed_cost'],
                          exception['reason']), ('below_cost', '60.00', '70.00', WHY))

    def test_the_editor_resending_what_the_offer_holds_does_not_ask_again(self):
        # An offer already below its cost (confirmed before, or older than this guard): saving its quantity with
        # the same price and cost the form resends asks nothing — nothing about the margin changed.
        SupplierOffer.objects.filter(pk=self.offer.pk).update(commercial_price=D('60.00'))
        res = self.api.patch(f'/api/admin/supplier-offers/{self.offer.pk}/', {
            'commercial_price': '60.00', 'agreed_cost': '70.00', 'confirmed_quantity': 9}, format='json')
        self.assertEqual(res.status_code, 200, res.content)

    def test_without_costs_view_the_answer_never_depends_on_the_cost(self):
        url = f'/api/admin/supplier-offers/{self.offer.pk}/'
        below = self.blind.patch(url, {'commercial_price': '60.00'}, format='json')
        above = self.blind.patch(url, {'commercial_price': '150.00'}, format='json')
        # The same answer below and above the cost: no oracle.
        self.assertEqual((below.status_code, above.status_code), (409, 409))
        self.assertEqual(below.json(), above.json())
        self.assertEqual(below.json()['code'], 'cost_unseen_confirmation_required')
        self.assertEqual(below.json()['detail'], supply.COST_UNSEEN_TEXT)
        self.assertNotIn('agreed_cost', below.json())
        assert_no_amount(self, below.json(), '70', '60', '150')
        self.offer.refresh_from_db()
        self.assertEqual(self.offer.commercial_price, D('110.00'))
        res = self.blind.patch(url, {'commercial_price': '60.00', 'confirm_below_cost': True,
                                     'below_cost_reason': WHY}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertNotIn('agreed_cost', res.json())
        assert_no_amount(self, res.json(), '70')
        audit, exception = exception_of(self.offer.pk, 'supplier_offer_updated')
        # The cost lives in the audit row (owner, 2026-11-23), never in what this person is answered.
        self.assertEqual((audit.actor, exception['verdict'], exception['agreed_cost'], exception['reason']),
                         (self.seller, 'cost_unseen', '70.00', WHY))
        # Nothing else asks: the same price again, another field, or an offer without a cost.
        self.assertEqual(self.blind.patch(url, {'commercial_price': '60.00', 'lead_time_days': 6},
                                          format='json').status_code, 200)
        SupplierOffer.objects.filter(pk=self.offer.pk).update(agreed_cost=None)
        self.assertEqual(self.blind.patch(url, {'commercial_price': '61.00'}, format='json').status_code, 200)

    def test_without_costs_view_a_new_offer_has_no_cost_and_asks_nothing(self):
        self.assertEqual(self.new_offer(self.blind, commercial_price='1.00').status_code, 201)

    def test_the_history_hides_the_cost_and_the_comparison_from_who_cannot_see_costs(self):
        url = f'/api/admin/supplier-offers/{self.offer.pk}/'
        self.assertEqual(self.api.patch(url, {'commercial_price': '60.00', 'confirm_below_cost': True,
                                              'below_cost_reason': WHY}, format='json').status_code, 200)
        self.assertEqual(self.blind.patch(url, {'commercial_price': '65.00', 'confirm_below_cost': True,
                                                'below_cost_reason': WHY}, format='json').status_code, 200)
        history = f'{url}history/'
        seen = self.api.get(history).json()['results']
        verdicts = [row['metadata']['margin_exception']['verdict'] for row in seen
                    if 'margin_exception' in row['metadata']]
        self.assertEqual(verdicts, ['cost_unseen', 'below_cost'])
        self.assertIn('70.00', [row['metadata']['margin_exception']['agreed_cost'] for row in seen
                                if 'margin_exception' in row['metadata']])
        hidden = self.blind.get(history).json()['results']
        # The same rows, in the same order: nothing to count. Only the exception's details are gone.
        self.assertEqual([(row['action'], row['at']) for row in hidden], [(row['action'], row['at']) for row in seen])
        self.assertFalse(any('margin_exception' in row['metadata'] for row in hidden))
        assert_no_amount(self, hidden, '70')

    def test_the_audit_log_returns_the_cost_only_to_costs_view(self):
        url = f'/api/admin/supplier-offers/{self.offer.pk}/'
        self.api.patch(url, {'commercial_price': '60.00', 'confirm_below_cost': True, 'below_cost_reason': WHY},
                       format='json')
        self.blind.patch(url, {'commercial_price': '65.00', 'confirm_below_cost': True, 'below_cost_reason': WHY},
                         format='json')
        auditor, _ = _p2d_member(self.company, 'sg_auditor_sin_costos', ['company.view', 'memberships.view'])
        owner, _ = _p2d_member(self.company, 'sg_auditor_con_costos',
                               ['company.view', 'memberships.view', 'costs.view'])
        query = '/api/admin/audit-logs/?target_type=supplier_offer'
        blind = self.as_(auditor).get(query).json()['results']
        full = self.as_(owner).get(query).json()['results']
        # Second review of #402: the SAME rows (ids) for both — no hidden row, no gap in the ids to read.
        self.assertEqual([row['id'] for row in blind], [row['id'] for row in full])
        self.assertFalse(any('margin_exception' in row['metadata'] for row in blind), blind)
        assert_no_amount(self, blind, '70')
        exceptions = [row['metadata']['margin_exception'] for row in full if 'margin_exception' in row['metadata']]
        self.assertEqual([(e['verdict'], e['commercial_price'], e['agreed_cost']) for e in exceptions],
                         [('cost_unseen', '65.00', '70.00'), ('below_cost', '60.00', '70.00')])
        # No action of its own: nothing to search for either.
        self.assertEqual(self.as_(owner).get('/api/admin/audit-logs/?action=below_cost').json()['results'], [])

    def test_the_audit_log_hides_the_exception_per_company(self):
        # A reader of two companies: sees the exception where they hold `costs.view`, not where they do not.
        self.api.patch(f'/api/admin/supplier-offers/{self.offer.pk}/',
                       {'commercial_price': '60.00', 'confirm_below_cost': True, 'below_cost_reason': WHY},
                       format='json')
        other = GuardShop()
        other.build('sg-otra')
        other.api.patch(f'/api/admin/supplier-offers/{other.offer.pk}/',
                        {'commercial_price': '50.00', 'confirm_below_cost': True, 'below_cost_reason': WHY},
                        format='json')
        reader, _ = _p2d_member(self.company, 'sg_lector_dos', ['company.view', 'memberships.view', 'costs.view'])
        from store.models import Membership, UserProfile
        from store.tests import _assign, _role

        second = Membership.objects.create(user=reader, company=other.company, role=UserProfile.ROLE_CUSTOMER,
                                           branch_access_mode=Membership.ACCESS_MODE_ALL)
        _assign(second, _role(other.company, 'Rol lector sin costos', ['company.view', 'memberships.view'],
                              slug='rol-lector-sin-costos'))
        rows = self.as_(reader).get('/api/admin/audit-logs/?action=supplier_offer_updated').json()['results']
        self.assertEqual(sorted(row['target_id'] for row in rows), sorted([str(self.offer.pk), str(other.offer.pk)]))
        with_details = [row['target_id'] for row in rows if 'margin_exception' in row['metadata']]
        self.assertEqual(with_details, [str(self.offer.pk)], rows)

    def test_the_service_checks_the_margin_by_default(self):
        # Review of #402: no caller gets a below-cost offer by forgetting the flag (`registration_services` did).
        with self.assertRaises(supply.SupplyError) as refused:
            supply.create_offer(company=self.company, supplier_id=self.supplier.pk, product_id=self.case.pk,
                                actor=self.staff, may_view_costs=True, agreed_cost='120.00', commercial_price='100.00',
                                confirmed_quantity=1, valid_from=self.later, valid_until=self.later, lead_time_days=2)
        self.assertEqual(refused.exception.code, supply.BELOW_COST)
        with self.assertRaises(supply.SupplyError) as refused:
            supply.update_offer(self.offer, actor=self.staff, may_view_costs=True, commercial_price='60.00')
        self.assertEqual(refused.exception.code, supply.BELOW_COST)
        with self.assertRaises(supply.SupplyError) as refused:
            supply.update_offer(self.offer, actor=self.seller, commercial_price='120.00')
        self.assertEqual(refused.exception.code, supply.COST_UNSEEN)
        self.offer.refresh_from_db()
        self.assertEqual(self.offer.commercial_price, D('110.00'))
        # What does not touch the margin never asks.
        supply.update_offer(self.offer, actor=self.seller, lead_time_days=6)


# ---------------------------------------------------------------------------
# 3. The weekly file
# ---------------------------------------------------------------------------

class ImportGuardTest(GuardShop, TestCase):
    def setUp(self):
        self.build()
        self.start, self.end = self.offer.valid_from, self.offer.valid_until

    def file(self, rows, headers=OFFER_HEADERS):
        from store.test_cost_imports import upload
        return upload(headers, rows, title='Ofertas de proveedor')

    def row(self, **change):
        values = {'Proveedor': 'Mayorista Lima', 'Código/SKU del producto': self.case.slug, 'Modelo': '',
                  'Capacidad': '', 'Color': '', 'Línea': '', 'Costo pactado': '', 'Precio comercial': '110',
                  'Cantidad confirmada': 5, 'Vigente desde': self.start.isoformat(),
                  'Vigente hasta': self.end.isoformat(), 'Plazo (días)': 4}
        values.update(change)
        return [values[h] for h in OFFER_HEADERS]

    def preview(self, rows, client=None):
        res = (client or self.api).post('/api/admin/supplier-offers/import/preview/', {
            'file': self.file(rows), 'message_date': today().isoformat()}, format='multipart')
        self.assertEqual(res.status_code, 200, res.content)
        return res.json()

    def apply(self, job_id, client=None, **body):
        return (client or self.api).post(f'/api/admin/supplier-offers/import/{job_id}/apply/', body, format='json')

    # -- price below cost ------------------------------------------------------

    def test_a_new_offer_below_its_cost_warns_and_applies_only_when_confirmed(self):
        SupplierOffer.objects.filter(pk=self.offer.pk).delete()
        body = self.preview([self.row(**{'Costo pactado': '120', 'Precio comercial': '100'})])
        row = body['rows'][0]
        self.assertEqual(row['action'], 'create', row)
        self.assertEqual(row['data']['below_cost'], {'commercial_price': '100.00', 'agreed_cost': '120.00'})
        self.assertTrue(any('por debajo del costo' in text for text in row['warnings']), row['warnings'])
        self.assertNotIn('margin', row['data'])
        res = self.apply(body['id'])
        self.assertEqual((res.status_code, res.json()['code']), (409, 'below_cost_confirmation_required'),
                         res.content)
        self.assertEqual(res.json()['below_cost'][0]['row_number'], row['row_number'])
        self.assertEqual(res.json()['below_cost'][0]['agreed_cost'], '120.00')
        self.assertFalse(SupplierOffer.objects.exists())
        res = self.apply(body['id'], confirm_below_cost=True)
        self.assertEqual((res.status_code, res.json()['code']), (400, 'margin_reason_required'))
        self.assertFalse(SupplierOffer.objects.exists())
        res = self.apply(body['id'], confirm_below_cost=True, below_cost_reason=WHY)
        self.assertEqual(res.status_code, 200, res.content)
        made = SupplierOffer.objects.get()
        self.assertEqual((made.commercial_price, made.agreed_cost), (D('100.00'), D('120.00')))
        audit, exception = exception_of(made.pk, 'supplier_offer_created')
        self.assertEqual(audit.actor, self.staff)
        self.assertEqual(exception, {'verdict': 'below_cost', 'reason': WHY, 'commercial_price': '100.00',
                                     'agreed_cost': '120.00', 'source': 'import', 'job_id': body['id'],
                                     'row_number': row['row_number']})

    def test_an_update_below_the_existing_cost_asks_on_top_of_the_price_decision(self):
        body = self.preview([self.row(**{'Precio comercial': '60'})])
        self.assertIn('below_cost', body['rows'][0]['data'])
        res = self.apply(body['id'], accept_price_changes=True)
        self.assertEqual((res.status_code, res.json()['code']), (409, 'below_cost_confirmation_required'))
        self.offer.refresh_from_db()
        self.assertEqual(self.offer.commercial_price, D('110.00'))
        res = self.apply(body['id'], accept_price_changes=True, confirm_below_cost=True, below_cost_reason=WHY)
        self.assertEqual(res.status_code, 200, res.content)
        self.offer.refresh_from_db()
        self.assertEqual(self.offer.commercial_price, D('60.00'))

    def test_keeping_the_current_prices_needs_no_below_cost_confirmation(self):
        # Decided at apply, under the lock, on what is WRITTEN: kept prices are not below the cost.
        body = self.preview([self.row(**{'Precio comercial': '60', 'Cantidad confirmada': 9})])
        res = self.apply(body['id'], keep_prices=True)
        self.assertEqual(res.status_code, 200, res.content)
        self.offer.refresh_from_db()
        self.assertEqual(self.offer.commercial_price, D('110.00'))

    def test_a_price_above_the_cost_asks_nothing(self):
        body = self.preview([self.row(**{'Precio comercial': '90'})])
        self.assertNotIn('below_cost', body['rows'][0]['data'])
        self.assertEqual(self.apply(body['id'], accept_price_changes=True).status_code, 200)

    def test_without_costs_view_the_file_never_tells_the_cost_nor_the_comparison(self):
        below = self.preview([self.row(**{'Precio comercial': '60'})], client=self.blind)
        above = self.preview([self.row(**{'Precio comercial': '150'})], client=self.blind)
        for body in (below, above):
            row = body['rows'][0]
            self.assertNotIn('below_cost', row['data'])
            self.assertNotIn('margin', row['data'])
            self.assertTrue(row['data']['cost_unverified'])
            self.assertIn(supply.COST_UNSEEN_ROW_TEXT, row['warnings'])
            assert_no_amount(self, row, '70')
        self.assertEqual(below['rows'][0]['warnings'], above['rows'][0]['warnings'])
        answers = []
        for body in (below, above):
            res = self.apply(body['id'], client=self.blind, accept_price_changes=True)
            self.assertEqual((res.status_code, res.json()['code']), (409, 'cost_unseen_confirmation_required'))
            assert_no_amount(self, res.json(), '70')
            self.assertNotIn('below_cost', res.json())
            answers.append({k: v for k, v in res.json().items() if k != 'cost_checks'})
        self.assertEqual(answers[0], answers[1])
        res = self.apply(below['id'], client=self.blind, accept_price_changes=True, confirm_below_cost=True)
        self.assertEqual((res.status_code, res.json()['code']), (400, 'margin_reason_required'))
        res = self.apply(below['id'], client=self.blind, accept_price_changes=True, confirm_below_cost=True,
                         below_cost_reason=WHY)
        self.assertEqual(res.status_code, 200, res.content)
        assert_no_amount(self, res.json(), '70')
        self.offer.refresh_from_db()
        self.assertEqual(self.offer.commercial_price, D('60.00'))

    def test_a_reader_without_costs_view_of_a_job_previewed_with_them_sees_no_comparison(self):
        from store import supplier_offer_import
        from store.models import BulkImportJob

        body = self.preview([self.row(**{'Precio comercial': '60'})])
        job = BulkImportJob.objects.get(pk=body['id'])
        blind = supplier_offer_import.job_payload(job, include_cost=False)['rows'][0]
        self.assertNotIn('below_cost', blind['data'])
        self.assertTrue(blind['data']['cost_unverified'])
        self.assertFalse(any('por debajo del costo' in text for text in blind['warnings']))
        assert_no_amount(self, blind, '70')
        # Nothing about the margin is stored where a warning is: the sentence is built for each reader.
        staged = BulkImportRow.objects.get(job_id=body['id'])
        self.assertFalse(any('costo' in text for text in staged.warnings))

    # -- inactive supplier ---------------------------------------------------------

    def test_a_row_of_an_inactive_supplier_warns_that_it_will_not_sell(self):
        self.deactivate_supplier()
        body = self.preview([self.row(**{'Cantidad confirmada': 9})])
        row = body['rows'][0]
        self.assertEqual(row['action'], 'update', row)
        self.assertTrue(row['data']['supplier_inactive'])
        self.assertTrue(any('inactivo' in text for text in row['warnings']), row['warnings'])
        # A warning, not an error: it still applies.
        self.assertEqual(self.apply(body['id']).status_code, 200)

    def test_an_active_supplier_does_not_warn(self):
        row = self.preview([self.row(**{'Cantidad confirmada': 9})])['rows'][0]
        self.assertNotIn('supplier_inactive', row['data'])
        self.assertFalse(any('inactivo' in text for text in row['warnings']))


# ---------------------------------------------------------------------------
# 3b. «Registrar artículo» — never a below-cost offer (review of #402)
# ---------------------------------------------------------------------------

from store.test_registration import RegistrationFixture  # noqa: E402


class RegistrationBelowCostTest(RegistrationFixture):
    """
    The wizard has no confirmation step: a below-cost offer is refused here, with nothing written, and the
    message sends the person to the offers panel, where it is confirmed with a reason and audited.
    """

    def test_a_below_cost_offer_is_refused_and_nothing_is_written(self):
        body = self.on_order(offer=self.offer(agreed_cost='5000.00', commercial_price='4199.00'))
        before = self.counts()
        res = self.post(self.admin, body, expect=400).json()
        self.assertEqual((res['step'], res['code'], res['field']),
                         ('supplier', 'below_cost_use_offers_panel', 'offer.commercial_price'), res)
        self.assertIn('Ofertas', res['detail'])
        self.assertEqual(self.counts(), before)
        self.assertFalse(exception_rows().exists())

    def test_at_or_above_the_cost_it_registers_as_before(self):
        body = self.on_order(offer=self.offer(agreed_cost='4199.00', commercial_price='4199.00'))
        self.post(self.admin, body, expect=201)


# ---------------------------------------------------------------------------
# 4. The export names the variant
# ---------------------------------------------------------------------------

class ExportVariantColumnsTest(GuardShop, TestCase):
    def setUp(self):
        self.build()
        from store import device_catalog
        from store.models import VariantAttribute

        capacity = device_catalog.create_attribute(company=self.company, name='Capacidad',
                                                   role=VariantAttribute.ROLE_CAPACITY)
        colour = device_catalog.create_attribute(company=self.company, name='Color', kind=VariantAttribute.KIND_COLOR,
                                                 role=VariantAttribute.ROLE_COLOR)
        gb = device_catalog.create_option(company=self.company, attribute_id=capacity.pk, label='256 GB')
        blue = device_catalog.create_option(company=self.company, attribute_id=colour.pk, label='Azul',
                                           swatch_hex='#1E3A8A')
        model = device_catalog.create_model(company=self.company, name='Tableta Q')
        from store.tests import _prod

        self.variant = _prod(self.company, 'Tableta Q 256 GB Azul', 'tableta-q-256-azul', price='3000.00',
                             inventory=0)
        device_catalog.set_variant(self.variant, model_id=model.pk, option_ids=[gb.pk, blue.pk])
        self.make_offer(product=self.variant)

    def sheet(self):
        res = self.api.get('/api/admin/supplier-offers/export/')
        self.assertEqual(res.status_code, 200)
        return res.content, openpyxl.load_workbook(io.BytesIO(res.content)).active

    def test_model_capacity_and_colour_come_from_the_option_roles(self):
        _content, sheet = self.sheet()
        headers = [c.value for c in sheet[1]]
        rows = [dict(zip(headers, [c.value for c in line])) for line in sheet.iter_rows(min_row=2)]
        by_name = {row['Código/SKU del producto']: row for row in rows}
        variant = by_name['tableta-q-256-azul']
        self.assertEqual((variant['Modelo'], variant['Capacidad'], variant['Color'], variant['Línea']),
                         ('Tableta Q', '256 GB', 'Azul', 'Nuevo'))
        # A product that is not a model's variant keeps the columns empty.
        plain = by_name[self.case.slug]
        self.assertEqual((plain['Modelo'], plain['Capacidad'], plain['Color']), (None, None, None))

    def test_the_export_with_the_variant_columns_loads_back_unchanged(self):
        content, _sheet = self.sheet()
        upload = SimpleUploadedFile('ofertas.xlsx', content,
                                    content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        body = self.api.post('/api/admin/supplier-offers/import/preview/',
                             {'file': upload, 'message_date': today().isoformat()}, format='multipart').json()
        self.assertEqual((body['rows_no_change'], body['rows_error']), (2, 0), body['rows'])
