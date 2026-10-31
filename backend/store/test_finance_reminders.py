"""
FIN-REMIND-01 — internal reminders of fixed payments (docs/adr-finance-obligations.md §7.1).

What these tests pin:

  * a reminder is a NOTICE: no run, read or snooze ever changes a balance, a state or a payment;
  * one event per (obligation, schedule point, due date): a second run, a retried cron or two runs at once never repeat it;
  * after a gap in the cron only the LATEST point already passed is sent, and overdue notices are paced and capped;
  * only open, owed, unpaused PAYABLES are reminded (paid, voided, paused, receivables are not; a dispute still is);
  * recipients hold `finance.view`, with the obligation's branch (or company-wide scope for a general one), in
    THEIR company; the platform master is never addressed;
  * the company's own local date decides «hoy»; the policy decides the points; e-mail is opt-in and its failure
    loses nothing.
"""
from datetime import date, datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from unittest import mock

from django.core import mail
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from store import finance_test_support as fin
from store import notification_services
from store.finance import reminders, services
from store.models import (
    FinanceReminderPolicy, Notification, NotificationDelivery, NotificationEvent, Obligation,
    ObligationReminderSnooze,
)
from store.notification_events import FINANCE_OBLIGATION_DUE

D = Decimal
TODAY = date(2026, 11, 10)


def due_in(days):
    return TODAY + timedelta(days=days)


class RemindersBase(TestCase):
    def setUp(self):
        self.company = fin.company('rem-a', 'Remind A')
        self.main = fin.branch(self.company, 'Principal')
        self.annex = fin.branch(self.company, 'Anexo')
        self.admin = fin.member(self.company, 'rem_admin', fin.ALL_FINANCE)
        self.viewer = fin.member(self.company, 'rem_viewer', ('finance.view',))
        self.outsider = fin.member(self.company, 'rem_none', ('sales.pos.use',))   # no finance.view
        self.annex_only = fin.member(self.company, 'rem_annex', ('finance.view',), branches=[self.annex])
        self.main_only = fin.member(self.company, 'rem_main', ('finance.view',), branches=[self.main])

    def owe(self, days, *, concept='Alquiler del local', amount='1500.00', branch='main', owner=None, **extra):
        branch = {'main': self.main, 'annex': self.annex, None: None}.get(branch, branch)
        return services.create_obligation(
            self.company, actor=self.admin, concept=concept, amount=D(amount), due_date=due_in(days),
            branch=branch, owner=owner, today=TODAY, payee_name='Inmobiliaria Sur', **extra)

    def run_on(self, day_offset=0, company=None):
        return reminders.run_company(company or self.company, today=due_in(day_offset))

    def notices(self, user=None, obligation=None):
        rows = Notification.objects.filter(event__event_type=FINANCE_OBLIGATION_DUE)
        if user is not None:
            rows = rows.filter(user=user)
        if obligation is not None:
            rows = rows.filter(target_type='finance_obligation', target_id=obligation.pk)
        return rows

    def events(self):
        return NotificationEvent.objects.filter(event_type=FINANCE_OBLIGATION_DUE)


class WhenAPaymentIsDueTest(RemindersBase):
    def test_a_payment_a_week_away_is_announced_once_to_everyone_who_may_see_it(self):
        ob = self.owe(7)
        result = self.run_on()
        self.assertEqual(result['emitted'], 1)
        names = sorted(n.user.username for n in self.notices(obligation=ob))
        self.assertEqual(names, ['rem_admin', 'rem_main', 'rem_viewer'])
        notice = self.notices(user=self.viewer).get()
        self.assertIn('Alquiler del local', notice.title)
        self.assertIn('1,500.00', notice.body)
        self.assertIn('Inmobiliaria Sur', notice.body)
        self.assertEqual((notice.target_type, notice.target_id), ('finance_obligation', ob.pk))

    def test_a_second_run_and_a_retried_cron_repeat_nothing(self):
        ob = self.owe(7)
        self.run_on()
        again = self.run_on()
        self.assertEqual(again['emitted'], 0)
        self.assertEqual(self.events().count(), 1)
        self.assertEqual(self.notices(obligation=ob).count(), 3)

    def test_two_runs_racing_for_the_same_point_make_one_event(self):
        ob = self.owe(7)
        key = reminders.reminder_key(ob, 'before', 7)
        NotificationEvent.objects.create(company=self.company, event_type=FINANCE_OBLIGATION_DUE, event_key=key,
                                         target_type='finance_obligation', target_id=ob.pk)
        result = self.run_on()                 # the other process won: this one is silent, not an error
        self.assertEqual((result['emitted'], result['errors']), (0, 0))
        self.assertEqual(self.events().count(), 1)

    def test_each_point_of_the_schedule_fires_once_as_the_date_approaches(self):
        ob = self.owe(7)
        sent = []
        for offset in range(0, 8):             # a run every day from a week before to the due day
            self.run_on(offset)
            sent.append(self.events().count())
        self.assertEqual(sent, [1, 1, 1, 1, 2, 2, 3, 4])          # 7 days, 3 days, 1 day and the day itself
        self.assertEqual(self.notices(user=self.viewer, obligation=ob).count(), 4)

    def test_after_a_gap_only_the_latest_point_already_passed_is_sent(self):
        self.owe(7)
        result = self.run_on(5)                # the cron slept: 2 days to go, points 7 and 3 already passed
        self.assertEqual(result['emitted'], 1)
        self.assertTrue(self.events().get().event_key.endswith(f':3@{due_in(7)}'))

    def test_the_due_day_says_it_is_today_and_asks_for_action(self):
        self.owe(0)
        self.run_on()
        notice = self.notices(user=self.viewer).get()
        self.assertIn('hoy', notice.title.lower())
        self.assertEqual(notice.priority, Notification.Priority.ACTION)

    def test_further_than_the_first_point_nothing_is_sent(self):
        self.owe(20)
        self.assertEqual(self.run_on()['emitted'], 0)


class OverdueTest(RemindersBase):
    def test_overdue_is_announced_the_day_after_and_then_paced(self):
        self.owe(-1)
        counts = []
        for day in range(0, 25):
            self.run_on(day)
            counts.append(self.events().count())
        # day 0: due yesterday -> 1st overdue; then every 7 days (day 7 -> -8 days overdue is point 1), capped at 4.
        self.assertEqual(counts[0], 1)
        self.assertEqual(counts[6], 1)
        self.assertEqual(counts[7], 2)
        self.assertEqual(counts[14], 3)
        self.assertEqual(counts[21], 4)
        self.assertEqual(counts[24], 4)

    def test_overdue_notices_are_capped(self):
        self.owe(-100)
        self.run_on()
        self.assertEqual(self.events().count(), 0)       # point 14 of 4: long past the cap, nothing is sent

    def test_the_first_overdue_run_after_a_gap_sends_one_notice_not_all(self):
        self.owe(-9)
        self.assertEqual(self.run_on()['emitted'], 1)
        self.assertEqual(self.events().count(), 1)

    def test_overdue_priority_is_a_warning(self):
        self.owe(-1)
        self.run_on()
        self.assertEqual(self.notices(user=self.viewer).get().priority, Notification.Priority.WARNING)


class WhoIsRemindedAboutWhatTest(RemindersBase):
    def test_paid_voided_and_paused_payments_are_not_reminded(self):
        paid = self.owe(1, concept='Pagada')
        services.record_payment(paid, amount=D('1500.00'), method='transfer', actor=self.admin, paid_on=services.company_today(self.company))
        voided = self.owe(1, concept='Anulada')
        services.void(voided, reason='error de carga', actor=self.admin)
        paused_rule = fin.rule(self.company, self.admin, due_day=11, concept='Internet', today=date(2026, 11, 1))
        services.pause_rule(paused_rule, reason='sin servicio', actor=self.admin, today=date(2026, 11, 1))
        self.run_on()
        reminded = {n.target_id for n in self.notices()}
        self.assertEqual(reminded, set())

    def test_a_partly_paid_payment_is_still_reminded_with_what_remains(self):
        ob = self.owe(1)
        services.record_payment(ob, amount=D('500.00'), method='cash', actor=self.admin, paid_on=services.company_today(self.company))
        self.run_on()
        self.assertIn('1,000.00', self.notices(user=self.viewer).get().body)

    def test_a_disputed_payment_is_still_reminded(self):
        ob = self.owe(1)
        services.mark_disputed(ob, reason='cobro duplicado', actor=self.admin)
        self.assertEqual(self.run_on()['emitted'], 1)

    def test_receivables_are_never_reminded_as_payments(self):
        from store.finance import receivables
        from store.models import Customer

        customer = Customer.objects.create(company=self.company, first_name='Ana', last_name='Quispe')
        receivables.create_receivable(self.company, actor=self.admin, customer=customer, concept='Venta a crédito',
                                      amount=D('300.00'), due_date=due_in(1), reason='acuerdo de pago', today=TODAY)
        self.assertEqual(self.run_on()['emitted'], 0)

    def test_a_payment_without_a_due_date_is_not_reminded(self):
        ob = self.owe(1)
        Obligation.objects.filter(pk=ob.pk).update(due_date=None)
        self.assertEqual(self.run_on()['emitted'], 0)

    def test_a_disabled_policy_sends_nothing(self):
        self.owe(1)
        FinanceReminderPolicy.objects.update_or_create(company=self.company, defaults={'enabled': False})
        self.assertEqual(self.run_on()['emitted'], 0)

    def test_a_custom_schedule_is_obeyed(self):
        self.owe(10)
        FinanceReminderPolicy.objects.update_or_create(company=self.company, defaults={'offsets': [10, 2]})
        self.assertEqual(self.run_on()['emitted'], 1)
        self.assertEqual(self.run_on(7)['emitted'], 0)         # 3 days to go: not a point of THIS company
        self.assertEqual(self.run_on(8)['emitted'], 1)         # 2 days to go


class RecipientsTest(RemindersBase):
    def test_branch_scope_decides_who_hears_about_a_branch_payment(self):
        self.owe(1, branch='annex')
        self.run_on()
        heard = {n.user.username for n in self.notices()}
        self.assertEqual(heard, {'rem_admin', 'rem_viewer', 'rem_annex'})     # not rem_main (other branch)

    def test_a_general_payment_reaches_only_company_wide_scope(self):
        self.owe(1, branch=None)
        self.run_on()
        heard = {n.user.username for n in self.notices()}
        self.assertEqual(heard, {'rem_admin', 'rem_viewer'})                   # not the branch-limited viewers

    def test_the_owner_without_finance_view_is_not_sent_amounts(self):
        self.owe(1, owner=self.outsider)
        self.run_on()
        self.assertFalse(self.notices(user=self.outsider).exists())

    def test_the_platform_master_is_never_addressed(self):
        master = get_user_model().objects.create_superuser('rem_master', 'm@example.com', 'Pass123!')
        self.owe(1)
        self.run_on()
        self.assertFalse(self.notices(user=master).exists())

    def test_another_company_neither_receives_nor_causes_notices(self):
        other = fin.company('rem-b', 'Remind B')
        other_user = fin.member(other, 'rem_b_admin', fin.ALL_FINANCE)
        mine = self.owe(1)
        theirs = services.create_obligation(other, actor=other_user, concept='Ajeno', amount=D('99.00'),
                                            due_date=due_in(1), today=TODAY)
        self.run_on()
        self.assertFalse(self.notices(user=other_user).exists())
        self.run_on(company=other)
        self.assertEqual({n.target_id for n in self.notices(user=other_user)}, {theirs.pk})
        self.assertTrue(self.notices(obligation=mine).exclude(company=self.company).count() == 0)

    def test_an_inactive_membership_is_not_addressed(self):
        from store.models import Membership

        Membership.objects.filter(user=self.viewer, company=self.company).update(is_active=False)
        self.owe(1)
        self.run_on()
        self.assertFalse(self.notices(user=self.viewer).exists())


class ANoticeNeverMovesMoneyTest(RemindersBase):
    def test_running_reading_and_snoozing_change_no_figure_state_or_payment(self):
        ob = self.owe(1)
        before = services.figures(Obligation.objects.get(pk=ob.pk))
        self.run_on()
        notification_services.mark_read(self.notices(user=self.viewer))
        reminders.snooze(ob, user=self.viewer, days=3, today=TODAY)
        ob.refresh_from_db()
        self.assertEqual(services.figures(ob), before)
        self.assertEqual(ob.lifecycle, Obligation.LIFECYCLE_OPEN)
        self.assertEqual(ob.payments.count(), 0)

    def test_no_whatsapp_row_is_ever_written_for_a_payment_reminder(self):
        self.owe(1)
        self.run_on()
        self.assertFalse(NotificationDelivery.objects.filter(channel='whatsapp').exists())


class SnoozeTest(RemindersBase):
    def test_a_snoozed_person_is_silent_until_the_date_and_the_others_still_hear(self):
        ob = self.owe(7)
        reminders.snooze(ob, user=self.viewer, days=2, today=TODAY)
        self.run_on()
        self.assertFalse(self.notices(user=self.viewer).exists())
        self.assertTrue(self.notices(user=self.admin).exists())

    def test_after_the_date_one_postponed_notice_arrives_and_only_once(self):
        ob = self.owe(7)
        reminders.snooze(ob, user=self.viewer, days=2, today=TODAY)
        self.run_on(1)                                           # still snoozed
        self.assertFalse(self.notices(user=self.viewer).exists())
        self.run_on(3)                                           # snooze over (2 days from day 0 = through day 2)
        mine = self.notices(user=self.viewer)
        self.assertEqual(mine.count(), 1)
        self.assertIn('pospuesto', mine.get().title.lower())
        self.run_on(3)
        self.assertEqual(self.notices(user=self.viewer, obligation=ob).filter(title__icontains='pospuesto').count(), 1)
        self.assertIsNotNone(ObligationReminderSnooze.objects.get(user=self.viewer, obligation=ob).fired_at)

    def test_a_snooze_ending_on_a_paid_obligation_sends_nothing(self):
        ob = self.owe(7)
        reminders.snooze(ob, user=self.viewer, days=1, today=TODAY)
        services.record_payment(ob, amount=D('1500.00'), method='cash', actor=self.admin, paid_on=services.company_today(self.company))
        self.run_on(2)
        self.assertFalse(self.notices(user=self.viewer).exists())

    def test_snoozing_is_bounded_and_replaces_the_previous_one(self):
        ob = self.owe(7)
        with self.assertRaises(services.FinanceError):
            reminders.snooze(ob, user=self.viewer, days=0, today=TODAY)
        with self.assertRaises(services.FinanceError):
            reminders.snooze(ob, user=self.viewer, days=reminders.MAX_SNOOZE_DAYS + 1, today=TODAY)
        reminders.snooze(ob, user=self.viewer, days=2, today=TODAY)
        reminders.snooze(ob, user=self.viewer, days=5, today=TODAY)
        self.assertEqual(ObligationReminderSnooze.objects.filter(obligation=ob, user=self.viewer).count(), 1)
        self.assertEqual(ObligationReminderSnooze.objects.get(obligation=ob, user=self.viewer).until, due_in(5))


class LocalDateTest(RemindersBase):
    def test_the_companys_own_date_decides_what_today_is(self):
        # 2026-11-11 03:00 UTC is still 10 November in Lima (22:00) and already 11 November in Madrid.
        instant = datetime(2026, 11, 11, 3, 0, tzinfo=dt_timezone.utc)
        self.owe(0)                                              # due 10 November
        with mock.patch('django.utils.timezone.now', return_value=instant):
            lima = reminders.run_company(self.company)
        self.assertEqual(lima['emitted'], 1)
        self.assertIn('hoy', self.notices(user=self.viewer).get().title.lower())

        self.events().delete()
        self.company.settings.timezone = 'Europe/Madrid'
        self.company.settings.save()
        with mock.patch('django.utils.timezone.now', return_value=instant):
            madrid = reminders.run_company(self.company)         # 11 November there: overdue by one day
        self.assertEqual(madrid['emitted'], 1)
        self.assertIn('vencido', self.notices(user=self.admin).latest('pk').title.lower())


class EmailChannelTest(RemindersBase):
    def setUp(self):
        super().setUp()
        for user in (self.admin, self.viewer, self.main_only, self.annex_only):
            user.email = f'{user.username}@example.com'
            user.save()
        FinanceReminderPolicy.objects.update_or_create(
            company=self.company, defaults={'email_enabled': True, 'email_offsets': [1, 0], 'email_overdue': True})

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_mail_is_opt_in(self):
        FinanceReminderPolicy.objects.filter(company=self.company).update(email_enabled=False)
        self.owe(1)
        with self.captureOnCommitCallbacks(execute=True):
            self.run_on()
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(self.notices(user=self.viewer).count(), 1)         # the panel notice is there regardless

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_only_the_chosen_points_are_mailed_and_only_to_those_who_may_see_the_payment(self):
        self.owe(1, concept='Cercano')
        self.owe(7, concept='Lejano')                      # point 7 is not in the mailed points [1, 0]
        with self.captureOnCommitCallbacks(execute=True):
            self.run_on()
        self.assertEqual(sorted(m.to[0] for m in mail.outbox),
                         ['rem_admin@example.com', 'rem_main@example.com', 'rem_viewer@example.com'])
        self.assertTrue(all('Lejano' not in m.subject + m.body for m in mail.outbox))
        self.assertTrue(all(n.title for n in self.notices()))               # both reminders are in the panel

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_a_failing_mail_loses_neither_the_notice_nor_the_obligation_and_is_recorded(self):
        ob = self.owe(1)
        with mock.patch.object(notification_services, '_send', side_effect=RuntimeError('smtp caído')):
            with self.captureOnCommitCallbacks(execute=True):
                result = self.run_on()
        self.assertEqual(result['errors'], 0)
        self.assertEqual(self.notices(obligation=ob).count(), 3)
        self.assertEqual(NotificationDelivery.objects.filter(channel='email', status='failed').count(), 3)
        self.assertTrue(Obligation.objects.filter(pk=ob.pk).exists())

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_a_recipient_without_an_address_is_skipped_not_chased(self):
        self.viewer.email = ''
        self.viewer.save()
        self.owe(1)
        with self.captureOnCommitCallbacks(execute=True):
            self.run_on()
        row = NotificationDelivery.objects.get(channel='email', notification__user=self.viewer)
        self.assertEqual(row.status, 'skipped')


class ARunThatBreaksOnOneObligationGoesOnTest(RemindersBase):
    def test_one_failure_is_recorded_and_the_rest_still_get_their_notices(self):
        bad, good = self.owe(1, concept='Mala'), self.owe(1, concept='Buena')
        real = reminders._send_reminder

        def flaky(obligation, *args, **kwargs):
            if obligation.pk == bad.pk:
                raise RuntimeError('boom')
            return real(obligation, *args, **kwargs)

        with mock.patch.object(reminders, '_send_reminder', side_effect=flaky):
            result = self.run_on()
        self.assertEqual((result['emitted'], result['errors']), (1, 1))
        self.assertTrue(self.notices(obligation=good).exists())
        policy = FinanceReminderPolicy.objects.get(company=self.company)
        self.assertIsNotNone(policy.last_run_at)
        self.assertTrue(policy.last_error)
        self.assertNotIn('1,500', policy.last_error)             # an error line never carries an amount


class ScheduleCommandTest(RemindersBase):
    def test_the_command_generates_periods_and_reminds_for_every_company_in_its_own_date(self):
        from django.core.management import call_command

        self.owe(1)
        call_command('run_finance_schedule', '--today', TODAY.isoformat(), stdout=mock.MagicMock())
        self.assertEqual(self.events().count(), 1)
        call_command('run_finance_schedule', '--today', TODAY.isoformat(), stdout=mock.MagicMock())
        self.assertEqual(self.events().count(), 1)

    def test_a_company_that_is_inactive_is_skipped(self):
        self.owe(1)
        type(self.company).objects.filter(pk=self.company.pk).update(is_active=False)
        from django.core.management import call_command

        call_command('run_finance_schedule', '--today', TODAY.isoformat(), stdout=mock.MagicMock())
        self.assertEqual(self.events().count(), 0)


# ---------------------------------------------------------------------------
# Independent review (P1/P2/P3): scale, lost points, lost snoozes, scope, duplicates
# ---------------------------------------------------------------------------

class AQuietPassIsCheapTest(RemindersBase):
    def test_a_pass_that_emits_nothing_does_not_resolve_recipients_for_what_was_already_sent(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        for n in range(30):
            self.owe(1, concept=f'Pago {n}')
        self.run_on()                                            # everything announced
        with CaptureQueriesContext(connection) as quiet:
            again = self.run_on()
        self.assertEqual(again['emitted'], 0)
        self.assertLess(len(quiet), 60, f'{len(quiet)} queries for 30 payments already announced')


class APointNobodyReceivedIsNotConsumedTest(RemindersBase):
    def test_nobody_to_tell_leaves_the_point_free_for_when_someone_can_see_it(self):
        from store.models import Membership

        Membership.objects.filter(company=self.company, user__in=[self.admin, self.viewer, self.main_only,
                                                                  self.annex_only]).update(is_active=False)
        self.owe(7)
        self.assertEqual(self.run_on()['emitted'], 0)
        self.assertEqual(self.events().count(), 0)               # no empty event eating the point
        Membership.objects.filter(user=self.viewer, company=self.company).update(is_active=True)
        self.assertEqual(self.run_on()['emitted'], 1)
        self.assertEqual(self.notices(user=self.viewer).count(), 1)


class SnoozeNeverLosesANoticeTest(RemindersBase):
    def test_a_snooze_ending_while_the_payment_is_only_temporarily_not_remindable_is_retried(self):
        ob = self.owe(7)
        reminders.snooze(ob, user=self.viewer, days=1, today=TODAY)
        with mock.patch.object(services, 'state', return_value=services.STATE_PAUSED):
            self.run_on(2)
        row = ObligationReminderSnooze.objects.get(obligation=ob, user=self.viewer)
        self.assertIsNone(row.fired_at)                          # not claimed: it can still be told
        self.run_on(2)
        self.assertEqual(self.notices(user=self.viewer, obligation=ob).filter(title__icontains='pospuesto').count(), 1)

    def test_one_broken_snooze_does_not_stop_the_others(self):
        a, b = self.owe(7, concept='Una'), self.owe(7, concept='Otra')
        reminders.snooze(a, user=self.viewer, days=1, today=TODAY)
        reminders.snooze(b, user=self.viewer, days=1, today=TODAY)
        real = reminders._text

        def flaky(obligation, *args, **kwargs):
            if obligation.pk == a.pk and kwargs.get('postponed'):
                raise RuntimeError('boom')
            return real(obligation, *args, **kwargs)

        with mock.patch.object(reminders, '_text', side_effect=flaky):
            result = self.run_on(2)
        self.assertEqual(result['errors'], 1)
        self.assertTrue(self.notices(user=self.viewer, obligation=b).filter(title__icontains='pospuesto').exists())

    def test_the_day_a_snooze_ends_the_person_gets_one_notice_not_two(self):
        ob = self.owe(10)
        reminders.snooze(ob, user=self.viewer, days=6, today=TODAY)          # silent through day 6
        self.run_on(7)                                           # 3 days to go: a new point AND the snooze ends
        self.assertEqual(self.notices(user=self.viewer, obligation=ob).count(), 1)
        self.assertTrue(self.notices(user=self.admin, obligation=ob).exists())


class ARunIsBoundedAndNeverOverlapsTest(RemindersBase):
    def test_a_run_past_its_time_budget_defers_the_rest_to_the_next_pass(self):
        self.owe(1)
        result = reminders.run_company(self.company, today=TODAY, deadline=0)
        self.assertEqual((result['emitted'], result['deferred']), (0, 1))
        self.assertEqual(self.run_on()['emitted'], 1)            # the next pass picks it up

    def test_a_second_run_while_one_is_in_progress_does_nothing(self):
        self.owe(1)
        with mock.patch.object(reminders, '_try_lock', return_value=False):
            totals = reminders.run_all(today=TODAY)
        self.assertTrue(totals['skipped'])
        self.assertEqual(self.events().count(), 0)

    def test_the_command_fails_loudly_when_a_pass_had_errors(self):
        from django.core.management import call_command
        from django.core.management.base import CommandError

        self.owe(1)
        with mock.patch.object(reminders, '_send_reminder', side_effect=RuntimeError('boom')):
            with self.assertRaises(CommandError):
                call_command('run_finance_schedule', '--today', TODAY.isoformat(), stdout=mock.MagicMock())

    def test_fired_snoozes_are_purged_after_a_while(self):
        ob = self.owe(7)
        old = ObligationReminderSnooze.objects.create(company=self.company, obligation=ob, user=self.viewer,
                                                      until=due_in(-200), fired_at=datetime(2026, 1, 1, tzinfo=dt_timezone.utc))
        self.run_on()
        self.assertFalse(ObligationReminderSnooze.objects.filter(pk=old.pk).exists())


class ThePolicyIsCoherentAndAuditedTest(RemindersBase):
    def test_changing_the_points_alone_keeps_only_the_mailed_points_that_still_exist(self):
        policy = reminders.update_policy(self.company, actor=self.admin, offsets=[14, 7, 1])
        self.assertEqual(policy.email_offsets, [1])

    def test_mailing_a_point_that_is_not_a_reminder_point_is_refused(self):
        with self.assertRaises(services.FinanceError):
            reminders.update_policy(self.company, actor=self.admin, offsets=[7], email_offsets=[1])

    def test_the_audit_keeps_what_changed_before_and_after(self):
        from store.models import AdminAuditLog

        reminders.update_policy(self.company, actor=self.admin, email_enabled=True, offsets=[10, 2], email_offsets=[2])
        entry = AdminAuditLog.objects.filter(company=self.company, action='finance_reminder_policy_updated').latest('pk')
        self.assertEqual(entry.metadata['changes']['email_enabled'], {'from': False, 'to': True})
        self.assertEqual(entry.metadata['changes']['offsets'], {'from': [7, 3, 1, 0], 'to': [10, 2]})


class ThePanelAndTheMailReadWellTest(RemindersBase):
    def test_the_mail_gets_its_button_and_one_paragraph_per_line(self):
        from store.mail import builders

        context = builders.notification(
            company='Remind A', title='Pago por vencer: Internet', body='Beneficiario: X\nPendiente: S/ 200.00\nVence: 12/11/2026',
            audience='internal', event_type='finance.obligation.due', target_type='finance_obligation', target_id=4,
            link='https://panel.example/admin/finance?obligation=4')
        self.assertEqual(len(context['parrafos']), 3)
        self.assertEqual(context['boton']['url'], 'https://panel.example/admin/finance?obligation=4')

    def test_the_reminders_counter_counts_reminders_not_snooze_notices(self):
        ob = self.owe(7)
        reminders.snooze(ob, user=self.viewer, days=1, today=TODAY)
        self.run_on()                                            # one reminder (to the others)
        self.run_on(2)                                           # the snooze ends: one postponed notice
        detail = fin.client_for(self.admin).get(f'/api/admin/finance/obligations/{ob.pk}/').json()
        self.assertEqual(detail['reminders']['sent'], 1)
