"""FIN-REMIND-01 — the panel's API for the reminder policy and «Posponer aviso». A notice never moves money."""
from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase

from store import finance_test_support as fin
from store.finance import reminders, services
from store.models import AdminAuditLog, FinanceReminderPolicy, Notification, Obligation, ObligationReminderSnooze

D = Decimal
BASE = '/api/admin/finance/'


class ApiBase(TestCase):
    def setUp(self):
        self.company = fin.company('remapi-a', 'Remind API A')
        self.main = fin.branch(self.company, 'Principal')
        self.annex = fin.branch(self.company, 'Anexo')
        self.manager = fin.member(self.company, 'api_manager', fin.ALL_FINANCE + ('finance.automation.manage',))
        self.viewer = fin.member(self.company, 'api_viewer', ('finance.view',))
        self.annex_only = fin.member(self.company, 'api_annex', ('finance.view',), branches=[self.annex])
        self.nobody = fin.member(self.company, 'api_none', ('sales.pos.use',))
        self.today = services.company_today(self.company)

    def owe(self, days=3, branch=None, **extra):
        return services.create_obligation(
            self.company, actor=self.manager, concept='Internet', amount=D('200.00'),
            due_date=self.today + timedelta(days=days), branch=branch or self.main, **extra)


class PolicyApiTest(ApiBase):
    def test_everyone_with_finance_view_reads_the_policy_and_is_told_if_they_may_change_it(self):
        read = fin.client_for(self.viewer).get(BASE + 'reminder-policy/')
        self.assertEqual(read.status_code, 200, read.content)
        data = read.json()
        self.assertEqual((data['enabled'], data['offsets'], data['can_manage'], data['email_enabled']),
                         (True, [7, 3, 1, 0], False, False))
        self.assertTrue(fin.client_for(self.manager).get(BASE + 'reminder-policy/').json()['can_manage'])
        self.assertEqual(fin.client_for(self.nobody).get(BASE + 'reminder-policy/').status_code, 403)

    def test_only_the_automation_capability_changes_it_and_the_change_is_audited(self):
        body = {'offsets': [14, 7, 1, 0], 'email_enabled': True, 'overdue_every_days': 3, 'overdue_max': 2}
        self.assertEqual(fin.client_for(self.viewer).patch(BASE + 'reminder-policy/', body, format='json').status_code, 403)
        res = fin.client_for(self.manager).patch(BASE + 'reminder-policy/', body, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['offsets'], [14, 7, 1, 0])
        policy = FinanceReminderPolicy.objects.get(company=self.company)
        self.assertEqual((policy.email_enabled, policy.overdue_every_days, policy.overdue_max), (True, 3, 2))
        self.assertTrue(AdminAuditLog.objects.filter(company=self.company,
                                                     action='finance_reminder_policy_updated').exists())

    def test_nonsense_is_refused_and_nothing_is_saved(self):
        api = fin.client_for(self.manager)
        for bad in ({'offsets': []}, {'offsets': [-1]}, {'offsets': [99]}, {'offsets': 'x'}, {'offsets': [True]},
                    {'overdue_every_days': 0}, {'overdue_max': 99}, {'enabled': 'yes'}, {'colour': 'red'},
                    {'offsets': list(range(12))}):
            res = api.patch(BASE + 'reminder-policy/', bad, format='json')
            self.assertEqual(res.status_code, 400, (bad, res.content))
        self.assertEqual(list(reminders.policy_for(self.company).offsets), [7, 3, 1, 0])

    def test_the_policy_of_one_company_never_leaks_to_another(self):
        other = fin.company('remapi-b', 'Remind API B')
        boss = fin.member(other, 'api_b_boss', fin.ALL_FINANCE + ('finance.automation.manage',))
        fin.client_for(boss).patch(BASE + 'reminder-policy/', {'offsets': [5]}, format='json')
        self.assertEqual(reminders.policy_for(self.company).offsets, [7, 3, 1, 0])
        self.assertEqual(reminders.policy_for(other).offsets, [5])


class SnoozeApiTest(ApiBase):
    def test_snoozing_is_personal_and_leaves_the_debt_alone(self):
        ob = self.owe()
        before = services.figures(Obligation.objects.get(pk=ob.pk))
        res = fin.client_for(self.viewer).post(f'{BASE}obligations/{ob.pk}/snooze/', {'days': 5}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['snoozed_until'], (self.today + timedelta(days=5)).isoformat())
        self.assertEqual(services.figures(Obligation.objects.get(pk=ob.pk)), before)
        mine = fin.client_for(self.viewer).get(f'{BASE}obligations/{ob.pk}/').json()
        theirs = fin.client_for(self.manager).get(f'{BASE}obligations/{ob.pk}/').json()
        self.assertEqual((mine['snoozed_until'], theirs['snoozed_until']), (res.json()['snoozed_until'], None))
        listed = fin.client_for(self.viewer).get(BASE + 'obligations/').json()['results'][0]
        self.assertEqual(listed['snoozed_until'], res.json()['snoozed_until'])

    def test_a_snooze_can_be_lifted(self):
        ob = self.owe()
        api = fin.client_for(self.viewer)
        api.post(f'{BASE}obligations/{ob.pk}/snooze/', {'days': 5}, format='json')
        res = api.delete(f'{BASE}obligations/{ob.pk}/snooze/')
        self.assertEqual((res.status_code, res.json()['snoozed_until']), (200, None))
        self.assertFalse(ObligationReminderSnooze.objects.exists())

    def test_bad_days_and_missing_authority_are_refused(self):
        ob = self.owe()
        api = fin.client_for(self.viewer)
        for days in (0, 31, -2, 'x', None, True, 2.5):
            self.assertEqual(api.post(f'{BASE}obligations/{ob.pk}/snooze/', {'days': days}, format='json').status_code,
                             400, days)
        self.assertEqual(fin.client_for(self.nobody).post(f'{BASE}obligations/{ob.pk}/snooze/', {'days': 2},
                                                          format='json').status_code, 403)
        self.assertFalse(ObligationReminderSnooze.objects.exists())

    def test_a_branch_the_caller_cannot_see_or_another_company_answers_404(self):
        ob = self.owe(branch=self.main)
        res = fin.client_for(self.annex_only).post(f'{BASE}obligations/{ob.pk}/snooze/', {'days': 2}, format='json')
        self.assertEqual(res.status_code, 404)
        other = fin.company('remapi-c', 'Remind API C')
        stranger = fin.member(other, 'api_c', ('finance.view',))
        self.assertEqual(fin.client_for(stranger).post(f'{BASE}obligations/{ob.pk}/snooze/', {'days': 2},
                                                       format='json').status_code, 404)
        self.assertEqual(fin.client_for(stranger).delete(f'{BASE}obligations/{ob.pk}/snooze/').status_code, 404)


class ReminderHistoryTest(ApiBase):
    def test_the_detail_tells_how_many_reminders_went_out_and_which_ones_were_mine(self):
        ob = self.owe(days=1)
        reminders.run_company(self.company, today=self.today)
        detail = fin.client_for(self.viewer).get(f'{BASE}obligations/{ob.pk}/').json()
        self.assertEqual(detail['reminders']['sent'], 1)
        self.assertEqual(len(detail['reminders']['mine']), 1)
        self.assertIn('Internet', detail['reminders']['mine'][0]['title'])

    def test_the_bell_inbox_carries_the_obligation_as_its_target_and_reading_it_changes_nothing(self):
        ob = self.owe(days=1)
        reminders.run_company(self.company, today=self.today)
        notice = Notification.objects.get(user=self.viewer, target_type='finance_obligation')
        self.assertEqual(notice.target_id, ob.pk)
        before = services.figures(Obligation.objects.get(pk=ob.pk))
        Notification.objects.filter(pk=notice.pk).update(read_at=self.today)
        self.assertEqual(services.figures(Obligation.objects.get(pk=ob.pk)), before)
