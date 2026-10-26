"""
FIN-E — receivables entered by hand, and adjusting, writing off, voiding and
disputing a receivable (docs/adr-finance-obligations.md §6.1 origin 3, §6.3).
"""
from datetime import date
from unittest import mock

from django.test import TestCase

from store import finance_test_support as fx
from store.finance import receivables, services
from store.models import AdminAuditLog, Obligation, ObligationPayment, RepairStatusCode

D = fx.D
LIST = '/api/admin/finance/obligations/'
SUMMARY = '/api/admin/finance/summary/'


def body(owner_of_debt, **over):
    data = {'direction': 'receivable', 'customer': owner_of_debt.pk, 'concept': 'Equipo prestado',
            'amount': '300.00', 'due_date': '2026-03-01', 'reason': 'El cliente no devolvió el equipo.'}
    data.update(over)
    return data


class CreateManualReceivableTest(TestCase):
    def setUp(self):
        self.w = fx.RepairWorld('fin-man', 'Taller Manual')
        self.api = fx.client_for(fx.member(self.w.company, 'fin_man_admin', fx.RECEIVABLES))

    def create(self, **over):
        return self.api.post(LIST, body(self.w.customer, **over), format='json')

    def test_creates_a_receivable_with_customer_reason_and_audit(self):
        response = self.create()
        self.assertEqual(response.status_code, 201, response.content)
        out = response.json()
        self.assertEqual((out['direction'], out['origin_type'], out['balance']), ('receivable', 'manual', '300.00'))
        self.assertEqual(out['customer']['id'], self.w.customer.pk)
        self.assertEqual(out['creation_reason'], 'El cliente no devolvió el equipo.')
        row = Obligation.objects.get(pk=out['id'])
        self.assertEqual((row.customer_id, row.supplier_id), (self.w.customer.pk, None))
        self.assertTrue(AdminAuditLog.objects.filter(company=self.w.company, target_id=str(row.pk),
                                                     action='finance_obligation_created').exists())

    def test_needs_its_own_capability(self):
        manager = fx.client_for(fx.member(self.w.company, 'fin_man_only', fx.ALL_FINANCE))
        response = manager.post(LIST, body(self.w.customer), format='json')
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Obligation.objects.filter(direction='receivable').exists())
        only_receivables = fx.client_for(fx.member(self.w.company, 'fin_man_rec',
                                                   ('finance.view', 'finance.receivables.manual')))
        self.assertEqual(only_receivables.post(LIST, body(self.w.customer), format='json').status_code, 201)
        payable = only_receivables.post(LIST, {'concept': 'Luz', 'amount': '10.00', 'due_date': '2026-03-01'},
                                        format='json')
        self.assertEqual(payable.status_code, 403)

    def test_the_reason_is_required(self):
        for reason in ('', '   ', None):
            self.assertEqual(self.create(reason=reason).status_code, 400, reason)
        payload = body(self.w.customer)
        del payload['reason']
        self.assertEqual(self.api.post(LIST, payload, format='json').status_code, 400)
        self.assertFalse(Obligation.objects.filter(direction='receivable').exists())

    def test_the_customer_is_required_and_belongs_to_the_company(self):
        payload = body(self.w.customer)
        del payload['customer']
        self.assertEqual(self.api.post(LIST, payload, format='json').status_code, 400)
        stranger = fx.RepairWorld('fin-man-x', 'Ajena').customer
        self.assertEqual(self.create(customer=stranger.pk).status_code, 404)
        self.assertFalse(Obligation.objects.filter(direction='receivable').exists())

    def test_a_link_to_a_repair_or_a_sale_is_refused(self):
        order = self.w.order('500.00')
        for key, value in (('repair_order', order.pk), ('repair_order_id', order.pk), ('repair', order.pk),
                           ('order', 1), ('order_id', 1), ('sale', 1), ('sale_id', 1),
                           ('origin_type', 'repair'), ('origin_key', f'repair:{order.pk}'),
                           ('origin', 'sale')):
            response = self.create(**{key: value})
            self.assertEqual(response.status_code, 400, (key, response.content))
        self.assertFalse(Obligation.objects.filter(origin_type='manual').exists())

    def test_it_cannot_restate_the_balance_of_an_open_repair_of_the_same_customer(self):
        order = self.w.order('500.00')
        for field in ('external_reference', 'concept'):
            response = self.create(**{field: f'Saldo {order.number}'})
            self.assertEqual(response.status_code, 409, (field, response.content))
        self.assertEqual(Obligation.objects.filter(origin_type='manual').count(), 0)

    def test_the_repair_reference_is_matched_whole_and_only_for_the_same_customer(self):
        order = self.w.order('500.00')
        other, _user = self.w.new_customer()
        self.assertEqual(self.api.post(LIST, body(other, external_reference=order.number), format='json')
                         .status_code, 201)
        # a different number that merely starts with the same text is not the same reference
        self.assertEqual(self.create(external_reference=f'{order.number}9').status_code, 201)

    def test_a_paid_or_cancelled_repair_no_longer_blocks_a_manual_receivable(self):
        paid = self.w.order('100.00')
        self.w.pay(paid, '100.00')
        cancelled = self.w.order('100.00')
        from store import service_services as svc

        svc.transition_repair_order(repair_order=cancelled, to_status=RepairStatusCode.CANCELLED,
                                    actor=self.w.staff)
        for order in (paid, cancelled):
            self.assertEqual(self.create(external_reference=order.number).status_code, 201, order.number)

    def test_the_service_refuses_the_same_things_without_the_view(self):
        order = self.w.order('500.00')
        with self.assertRaises(services.FinanceConflict):
            receivables.create_receivable(
                self.w.company, actor=None, customer=self.w.customer, concept='x', amount='10.00',
                due_date=date(2026, 5, 1), reason='motivo', external_reference=order.number)
        with self.assertRaises(services.FinanceError):
            receivables.create_receivable(
                self.w.company, actor=None, customer=self.w.customer, concept='x', amount='10.00',
                due_date=date(2026, 5, 1), reason='')

    def test_idempotency_key_replays_and_conflicts(self):
        first = self.api.post(LIST, body(self.w.customer), format='json', HTTP_IDEMPOTENCY_KEY='fin-man-1')
        again = self.api.post(LIST, body(self.w.customer), format='json', HTTP_IDEMPOTENCY_KEY='fin-man-1')
        self.assertEqual((first.status_code, again.status_code), (201, 201))
        self.assertEqual(first.json()['id'], again.json()['id'])
        different = self.api.post(LIST, body(self.w.customer, amount='301.00'), format='json',
                                  HTTP_IDEMPOTENCY_KEY='fin-man-1')
        self.assertEqual(different.status_code, 409)
        other_customer, _user = self.w.new_customer()
        elsewhere = self.api.post(LIST, body(other_customer), format='json', HTTP_IDEMPOTENCY_KEY='fin-man-1')
        self.assertEqual(elsewhere.status_code, 409)
        self.assertEqual(Obligation.objects.filter(origin_type='manual').count(), 1)

    def test_a_replay_is_not_re_judged_against_a_repair_that_appeared_later(self):
        first = self.api.post(LIST, body(self.w.customer, external_reference='RO-LATER'), format='json',
                              HTTP_IDEMPOTENCY_KEY='fin-man-late')
        self.assertEqual(first.status_code, 201)
        order = self.w.order('500.00')
        from store.models import RepairOrder

        RepairOrder.objects.filter(pk=order.pk).update(number='RO-LATER')
        again = self.api.post(LIST, body(self.w.customer, external_reference='RO-LATER'), format='json',
                              HTTP_IDEMPOTENCY_KEY='fin-man-late')
        self.assertEqual((again.status_code, again.json()['id']), (201, first.json()['id']))
        fresh = self.api.post(LIST, body(self.w.customer, external_reference='RO-LATER'), format='json')
        self.assertEqual(fresh.status_code, 409)

    def test_amount_and_due_date_are_validated(self):
        for over in ({'amount': '0'}, {'amount': '10.001'}, {'amount': 'abc'}, {'due_date': 'mañana'},
                     {'due_date': None}):
            self.assertEqual(self.create(**over).status_code, 400, over)

    def test_a_receivable_category_must_be_a_receivable_one(self):
        payable = services.create_category(self.w.company, direction='payable', name='Alquiler', actor=None)
        receivable = services.create_category(self.w.company, direction='receivable', name='Préstamos',
                                              actor=None)
        self.assertEqual(self.create(category=payable.pk).status_code, 400)
        self.assertEqual(self.create(category=receivable.pk).status_code, 201)

    def test_listing_filters_by_direction_and_totals_stay_per_currency(self):
        self.create()
        self.create(currency='USD', amount='50.00', concept='Dólares')
        self.api.post(LIST, {'concept': 'Luz', 'amount': '10.00', 'due_date': '2026-03-01'}, format='json')
        with mock.patch('store.finance.services.company_today', return_value=date(2026, 3, 5)):
            rows = self.api.get(f'{LIST}?direction=receivable').json()['results']
            self.assertEqual({r['concept'] for r in rows}, {'Equipo prestado', 'Dólares'})
            totals = self.api.get(f'{SUMMARY}?direction=receivable').json()
        self.assertEqual(totals['open'], {'PEN': '300.00', 'USD': '50.00'})
        self.assertEqual(totals['overdue'], {'PEN': '300.00', 'USD': '50.00'})


class AdjustWriteOffVoidDisputeTest(TestCase):
    def setUp(self):
        self.w = fx.RepairWorld('fin-adj', 'Taller Ajustes')
        self.admin = fx.member(self.w.company, 'fin_adj_admin', fx.RECEIVABLES)
        self.api = fx.client_for(self.admin)
        self.debt = receivables.create_receivable(
            self.w.company, actor=self.admin, customer=self.w.customer, concept='Deuda', amount='300.00',
            due_date=date(2026, 3, 1), reason='Servicio fuera de taller.')
        self.url = f'{LIST}{self.debt.pk}/'

    def post(self, suffix, payload, api=None):
        return (api or self.api).post(self.url + suffix, payload, format='json')

    def test_a_partial_collection_then_a_write_off_of_the_rest_settles_it(self):
        self.assertEqual(self.post('payments/', {'amount': '100.00', 'method': 'cash'}).status_code, 201)
        response = self.post('adjustments/', {'kind': 'write_off', 'amount': '200.00', 'reason': 'Incobrable.'})
        self.assertEqual(response.status_code, 201, response.content)
        out = response.json()['obligation']
        self.assertEqual((out['state'], out['balance'], out['effective']), ('paid', '0.00', '100.00'))
        self.assertTrue(AdminAuditLog.objects.filter(company=self.w.company, target_id=str(self.debt.pk),
                                                     action='finance_obligation_adjusted').exists())

    def test_adjustments_need_a_reason_and_the_capability(self):
        self.assertEqual(self.post('adjustments/', {'kind': 'decrease', 'amount': '10.00', 'reason': ''})
                         .status_code, 400)
        cashier = fx.client_for(fx.member(self.w.company, 'fin_adj_cashier',
                                          ('finance.view', 'finance.payments.record')))
        self.assertEqual(self.post('adjustments/', {'kind': 'decrease', 'amount': '10.00', 'reason': 'x'},
                                   api=cashier).status_code, 403)
        self.assertEqual(self.debt.adjustments.count(), 0)

    def test_a_decrease_and_an_increase_move_what_is_owed(self):
        self.post('adjustments/', {'kind': 'decrease', 'amount': '50.00', 'reason': 'Descuento'})
        out = self.post('adjustments/', {'kind': 'increase', 'amount': '20.00', 'reason': 'Mora'}).json()
        self.assertEqual((out['obligation']['effective'], out['obligation']['balance']), ('270.00', '270.00'))

    def test_void_needs_reason_and_capability_and_no_confirmed_payments(self):
        self.assertEqual(self.post('void/', {'reason': ''}).status_code, 400)
        no_void = fx.client_for(fx.member(self.w.company, 'fin_adj_novoid', ('finance.view',)))
        self.assertEqual(self.post('void/', {'reason': 'x'}, api=no_void).status_code, 403)
        payment = self.post('payments/', {'amount': '100.00', 'method': 'cash'}).json()['payment']
        blocked = self.post('void/', {'reason': 'Error de alta'})
        self.assertEqual(blocked.status_code, 409, blocked.content)
        self.debt.refresh_from_db()
        self.assertEqual(self.debt.lifecycle, Obligation.LIFECYCLE_OPEN)
        self.assertEqual(self.post(f'payments/{payment["id"]}/reverse/', {'reason': 'Cobro mal puesto'})
                         .status_code, 200)
        done = self.post('void/', {'reason': 'Error de alta'})
        self.assertEqual(done.status_code, 200, done.content)
        self.assertEqual(done.json()['state'], 'voided')
        self.assertTrue(AdminAuditLog.objects.filter(company=self.w.company, target_id=str(self.debt.pk),
                                                     action='finance_obligation_voided').exists())
        self.assertEqual(self.post('payments/', {'amount': '10.00', 'method': 'cash'}).status_code, 400)

    def test_dispute_needs_reason_and_capability_and_keeps_the_balance(self):
        self.assertEqual(self.post('dispute/', {'reason': ''}).status_code, 400)
        no_dispute = fx.client_for(fx.member(self.w.company, 'fin_adj_nodis', ('finance.view',)))
        self.assertEqual(self.post('dispute/', {'reason': 'x'}, api=no_dispute).status_code, 403)
        out = self.post('dispute/', {'reason': 'Dice que ya pagó'}).json()
        self.assertTrue(out['disputed'])
        self.assertEqual((out['balance'], out['due_date']), ('300.00', '2026-03-01'))
        self.assertEqual(self.post('dispute/resolve/', {'resolution': ''}).status_code, 400)
        resolved = self.post('dispute/resolve/', {'resolution': 'Se comprobó la deuda'}).json()
        self.assertFalse(resolved['disputed'])

    def test_a_payable_cannot_be_written_off_and_a_receivable_can(self):
        rule = fx.rule(self.w.company, self.admin, today=date(2026, 1, 1))
        period = rule.periods.first()
        response = self.api.post(f'{LIST}{period.pk}/adjustments/',
                                 {'kind': 'write_off', 'amount': '10.00', 'reason': 'x'}, format='json')
        self.assertEqual(response.status_code, 400)

    def test_a_manual_receivable_is_never_a_repair_and_keeps_its_amount_after_a_collection(self):
        self.post('payments/', {'amount': '100.00', 'method': 'cash'})
        self.assertEqual(ObligationPayment.objects.filter(obligation=self.debt).count(), 1)
        self.debt.refresh_from_db()
        self.assertEqual(self.debt.amount_original, D('300.00'))


class ReceivablesTenancyTest(TestCase):
    def setUp(self):
        self.mine = fx.RepairWorld('fin-ten-a', 'Mi taller')
        self.theirs = fx.RepairWorld('fin-ten-b', 'Su taller')
        self.api = fx.client_for(fx.member(self.mine.company, 'fin_ten_me', fx.RECEIVABLES))
        self.foreign_order = self.theirs.order('500.00')
        self.foreign = Obligation.objects.get(repair_order=self.foreign_order)
        self.foreign_manual = receivables.create_receivable(
            self.theirs.company, actor=None, customer=self.theirs.customer, concept='Ajena', amount='9.00',
            due_date=date(2026, 3, 1), reason='x')

    def test_another_company_s_receivables_are_404_everywhere_and_never_listed_or_totalled(self):
        for row in (self.foreign, self.foreign_manual):
            base = f'{LIST}{row.pk}/'
            self.assertEqual(self.api.get(base).status_code, 404)
            for suffix, payload in (('payments/', {'amount': '1.00', 'method': 'cash'}), ('void/', {'reason': 'x'}),
                                    ('dispute/', {'reason': 'x'}),
                                    ('adjustments/', {'kind': 'write_off', 'amount': '1.00', 'reason': 'x'})):
                self.assertEqual(self.api.post(base + suffix, payload, format='json').status_code, 404, suffix)
        self.assertEqual(self.api.get(LIST).json()['results'], [])
        self.assertEqual(self.api.get(SUMMARY).json()['open'], {})

    def test_a_customer_of_another_company_cannot_back_a_manual_receivable(self):
        response = self.api.post(LIST, body(self.theirs.customer), format='json')
        self.assertEqual(response.status_code, 404)

    def test_the_backfill_of_one_company_does_not_touch_another(self):
        counts = receivables.backfill_repair_receivables(company=self.mine.company)
        self.assertEqual(counts['created'], 0)
        self.assertTrue(Obligation.objects.filter(pk=self.foreign.pk).exists())


class ReceivablesBranchScopeTest(TestCase):
    def setUp(self):
        self.w = fx.RepairWorld('fin-brn', 'Taller Sucursales')
        self.north = self.w.branch
        self.south = fx.branch(self.w.company, 'Sur')
        self.north_only = fx.client_for(fx.member(self.w.company, 'fin_brn_n', fx.RECEIVABLES,
                                                  branches=[self.north]))
        self.everywhere = fx.client_for(fx.member(self.w.company, 'fin_brn_all', fx.RECEIVABLES))
        self.north_order = self.w.order('100.00')
        self.south_order = self.w.order('200.00', branch_=self.south)
        self.north_row = Obligation.objects.get(repair_order=self.north_order)
        self.south_row = Obligation.objects.get(repair_order=self.south_order)

    def test_the_repair_receivable_carries_the_branch_of_its_order(self):
        self.assertEqual((self.north_row.branch_id, self.south_row.branch_id), (self.north.pk, self.south.pk))

    def test_a_branch_restricted_member_sees_only_their_branch(self):
        ids = [r['id'] for r in self.north_only.get(LIST).json()['results']]
        self.assertEqual(ids, [self.north_row.pk])
        self.assertEqual(self.north_only.get(f'{LIST}{self.south_row.pk}/').status_code, 404)
        self.assertEqual(self.north_only.post(f'{LIST}{self.south_row.pk}/dispute/', {'reason': 'x'},
                                              format='json').status_code, 404)
        self.assertEqual(self.north_only.get(SUMMARY).json()['open'], {'PEN': '100.00'})
        self.assertEqual(self.everywhere.get(SUMMARY).json()['open'], {'PEN': '300.00'})

    def test_a_manual_receivable_in_an_unauthorised_branch_is_refused(self):
        response = self.north_only.post(LIST, body(self.w.customer, branch=self.south.pk), format='json')
        self.assertEqual(response.status_code, 404)
        general = self.north_only.post(LIST, body(self.w.customer), format='json')
        self.assertEqual(general.status_code, 403)
        ok = self.north_only.post(LIST, body(self.w.customer, branch=self.north.pk), format='json')
        self.assertEqual(ok.status_code, 201, ok.content)


class RepairTotalsPerCurrencyTest(TestCase):
    def test_pen_and_usd_repairs_are_never_added_together(self):
        w = fx.RepairWorld('fin-cur', 'Taller Monedas')
        api = fx.client_for(fx.member(w.company, 'fin_cur_admin', fx.RECEIVABLES))
        w.order('100.00')
        settings_row = w.company.settings
        settings_row.currency = 'USD'
        settings_row.save(update_fields=['currency'])
        w.order('40.00')
        self.assertEqual(api.get(SUMMARY).json()['open'], {'PEN': '100.00', 'USD': '40.00'})
