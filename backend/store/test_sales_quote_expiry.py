"""
SALES-QUOTE-01 · cron — `manage.py expire_sales_quotes`, before its crontab line is installed.

What these tests hold: the hourly pass stamps «Vencida» only on offers that were still live (approved, sent,
accepted) and whose last valid day has passed in the shop's time zone; it never touches a draft, a decided, a
converted or a superseded quote, never deletes anything, writes one audit row per quote it stamps, and a second
pass — or two at once, or one against a conversion holding the quote — changes nothing more. It prints one line
for its log and exits non-zero when it fails.
"""
import threading
import time
from datetime import datetime, timedelta
from io import StringIO
from unittest import mock
from zoneinfo import ZoneInfo

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection, transaction
from django.test import TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from store import sales_quote_services as quotes
from store.models import AdminAuditLog, Order, PrintJob, SalesQuote, SalesQuoteItem
from store.test_sales_quotes import QuoteBase
from store.tests import _saas_branch, _saas_company

LIMA = ZoneInfo('America/Lima')
NOT_LIVE = sorted(set(dict(SalesQuote.STATUS_CHOICES)) - SalesQuote.LIVE_STATUSES - {SalesQuote.STATUS_EXPIRED})


def run(*args):
    out = StringIO()
    call_command('expire_sales_quotes', *args, stdout=out)
    return out.getvalue()


def expired_audits():
    return AdminAuditLog.objects.filter(action='sales_quote_expired')


def targets(audits):
    """The quotes these audit rows name (`target_id` is stored as text)."""
    return {int(pk) for pk in audits.values_list('target_id', flat=True)}


def snapshot():
    return list(SalesQuote.objects.order_by('pk').values_list('pk', 'status', 'expired_at', 'updated_at'))


class ExpiryCommandTest(QuoteBase):
    def quote_in(self, status, *, valid_until=None):
        quote = self.approved()
        SalesQuote.objects.filter(pk=quote['id']).update(
            status=status, valid_until=valid_until or timezone.localdate() - timedelta(days=1))
        return SalesQuote.objects.get(pk=quote['id'])

    def test_only_live_quotes_past_their_validity_expire_and_nothing_is_deleted(self):
        live = {status: self.quote_in(status) for status in sorted(SalesQuote.LIVE_STATUSES)}
        others = {status: self.quote_in(status) for status in NOT_LIVE}
        already = self.quote_in(SalesQuote.STATUS_EXPIRED)
        today = self.quote_in(SalesQuote.STATUS_SENT, valid_until=timezone.localdate())
        before = {q.pk: q for q in SalesQuote.objects.all()}
        rows = (SalesQuote.objects.count(), SalesQuoteItem.objects.count(), PrintJob.objects.count(),
                Order.objects.count())
        audits = list(AdminAuditLog.objects.values_list('pk', flat=True))

        self.assertEqual(run(), 'Cotizaciones vencidas: 3\n')

        for status, quote in live.items():
            stamped = SalesQuote.objects.get(pk=quote.pk)
            self.assertEqual(stamped.status, SalesQuote.STATUS_EXPIRED, status)
            self.assertIsNotNone(stamped.expired_at)
        for quote in [*others.values(), already, today]:
            untouched = SalesQuote.objects.get(pk=quote.pk)
            self.assertEqual((untouched.status, untouched.expired_at, untouched.updated_at),
                             (before[quote.pk].status, before[quote.pk].expired_at, before[quote.pk].updated_at))
        self.assertEqual((SalesQuote.objects.count(), SalesQuoteItem.objects.count(), PrintJob.objects.count(),
                          Order.objects.count()), rows)
        # Every audit row that existed is still there; one more per stamped quote, in its company.
        self.assertEqual(AdminAuditLog.objects.filter(pk__in=audits).count(), len(audits))
        stamped = expired_audits()
        self.assertEqual(targets(stamped), {q.pk for q in live.values()})
        for status, quote in live.items():
            row = stamped.get(target_id=str(quote.pk))
            self.assertEqual((row.company_id, row.actor_id, row.metadata['status']), (self.company.pk, None, 'expired'))
            self.assertEqual(row.metadata['valid_until'], (timezone.localdate() - timedelta(days=1)).isoformat())
            # What it was before, so a pass on a wrong clock can be undone exactly (RUNBOOK).
            self.assertEqual(row.metadata['previous_status'], status)

    def test_a_second_pass_changes_nothing(self):
        self.quote_in(SalesQuote.STATUS_APPROVED)
        self.quote_in(SalesQuote.STATUS_ACCEPTED)
        self.assertEqual(run(), 'Cotizaciones vencidas: 2\n')
        after_first, audits = snapshot(), expired_audits().count()
        self.assertEqual(run(), 'Cotizaciones vencidas: 0\n')
        self.assertEqual(snapshot(), after_first)
        self.assertEqual(expired_audits().count(), audits)

    def test_the_last_valid_day_ends_at_midnight_in_lima_not_in_utc(self):
        last_day = timezone.localdate()
        quote = self.quote_in(SalesQuote.STATUS_SENT, valid_until=last_day)
        late_evening = datetime(last_day.year, last_day.month, last_day.day, 23, 30, tzinfo=LIMA)
        with mock.patch('django.utils.timezone.now', return_value=late_evening):    # 04:30 UTC, the next day
            self.assertEqual(run(), 'Cotizaciones vencidas: 0\n')
        self.assertEqual(SalesQuote.objects.get(pk=quote.pk).status, SalesQuote.STATUS_SENT)
        with mock.patch('django.utils.timezone.now', return_value=late_evening + timedelta(hours=1)):
            self.assertEqual(run(), 'Cotizaciones vencidas: 1\n')
        self.assertEqual(SalesQuote.objects.get(pk=quote.pk).status, SalesQuote.STATUS_EXPIRED)

    def test_company_limits_the_pass_to_that_company(self):
        ours = self.quote_in(SalesQuote.STATUS_APPROVED)
        theirs = SalesQuote.objects.create(
            company=self.other, branch=self.foreign_branch, number='COT-AJENA', sequence_value=1,
            status=SalesQuote.STATUS_APPROVED, valid_until=timezone.localdate() - timedelta(days=1))

        self.assertEqual(run('--company', self.other.slug), 'Cotizaciones vencidas: 1\n')
        self.assertEqual(SalesQuote.objects.get(pk=theirs.pk).status, SalesQuote.STATUS_EXPIRED)
        self.assertEqual(SalesQuote.objects.get(pk=ours.pk).status, SalesQuote.STATUS_APPROVED)
        self.assertEqual(expired_audits().get().company_id, self.other.pk)

        with self.assertRaisesMessage(CommandError, 'Empresa no encontrada.'):
            run('--company', 'no-existe')
        self.assertEqual(SalesQuote.objects.get(pk=ours.pk).status, SalesQuote.STATUS_APPROVED)

        self.assertEqual(run(), 'Cotizaciones vencidas: 1\n')
        self.assertEqual(SalesQuote.objects.get(pk=ours.pk).status, SalesQuote.STATUS_EXPIRED)
        self.assertEqual(sorted(expired_audits().values_list('company_id', flat=True)),
                         sorted([self.company.pk, self.other.pk]))

    def test_dry_run_counts_and_writes_nothing(self):
        self.quote_in(SalesQuote.STATUS_APPROVED)
        self.quote_in(SalesQuote.STATUS_SENT)
        before, audits = snapshot(), AdminAuditLog.objects.count()
        self.assertEqual(run('--dry-run'), 'Cotizaciones por vencer: 2 (--dry-run: no se escribió nada)\n')
        self.assertEqual(run('--dry-run', '--company', self.other.slug),
                         'Cotizaciones por vencer: 0 (--dry-run: no se escribió nada)\n')
        self.assertEqual((snapshot(), AdminAuditLog.objects.count()), (before, audits))
        self.assertEqual(run(), 'Cotizaciones vencidas: 2\n')

    def test_a_failure_halfway_keeps_what_was_done_and_the_next_pass_finishes(self):
        first = self.quote_in(SalesQuote.STATUS_APPROVED)
        second = self.quote_in(SalesQuote.STATUS_APPROVED)
        original, calls = quotes._audit, []

        def audit_then_fail(**kwargs):
            calls.append(kwargs['quote'].pk)
            if len(calls) == 2:
                raise RuntimeError('database went away')
            return original(**kwargs)

        with mock.patch.object(quotes, '_audit', audit_then_fail), self.assertRaises(RuntimeError):
            run()
        done, failed = calls
        self.assertEqual(SalesQuote.objects.get(pk=done).status, SalesQuote.STATUS_EXPIRED)
        # The quote whose audit row could not be written was not stamped either: one transaction each.
        self.assertEqual(SalesQuote.objects.get(pk=failed).status, SalesQuote.STATUS_APPROVED)
        self.assertEqual(run(), 'Cotizaciones vencidas: 1\n')
        self.assertEqual(targets(expired_audits()), {first.pk, second.pk})

    def test_the_lock_takes_the_quote_row_and_nothing_joined(self):
        self.quote_in(SalesQuote.STATUS_APPROVED)
        with CaptureQueriesContext(connection) as captured:
            run()
        locks = [q['sql'] for q in captured.captured_queries if 'FOR UPDATE' in q['sql']]
        self.assertEqual(len(locks), 1, locks)
        self.assertIn('FROM "store_salesquote"', locks[0])
        self.assertNotIn('JOIN', locks[0])


# ---------------------------------------------------------------------------
# Concurrency (PostgreSQL row locks)
# ---------------------------------------------------------------------------

WAIT = 15


class ExpiryRaceTest(TransactionTestCase):
    """Two passes at once, and a pass against a conversion holding the quote: nothing stamped twice or wrongly."""

    def setUp(self):
        if connection.vendor != 'postgresql':
            self.skipTest('Only PostgreSQL has the row locks these tests are about.')
        self.company = _saas_company('Carrera', 'sq-race', tax_id='20900095101')
        self.branch = _saas_branch(self.company, 'Principal')
        self.yesterday = timezone.localdate() - timedelta(days=1)

    def quote(self, n, status=SalesQuote.STATUS_APPROVED):
        return SalesQuote.objects.create(company=self.company, branch=self.branch, number=f'COT-R{n:04d}',
                                         sequence_value=n, status=status, valid_until=self.yesterday)

    def in_threads(self, **tasks):
        outcomes, pids, done = {}, {}, {name: threading.Event() for name in tasks}

        def body(name, task):
            try:
                with connection.cursor() as cursor:
                    cursor.execute('SELECT pg_backend_pid()')
                    pids[name] = cursor.fetchone()[0]
                outcomes[name] = task()
            except Exception as exc:  # noqa: BLE001 — the test reads it
                outcomes[name] = exc
            finally:
                done[name].set()
                connection.close()

        threads = [threading.Thread(target=body, args=item, daemon=True) for item in tasks.items()]
        for thread in threads:
            thread.start()
        return threads, outcomes, pids, done

    def blocked(self, pids, done, name):
        deadline = time.monotonic() + WAIT
        while time.monotonic() < deadline:
            if done[name].is_set():
                return False
            if name in pids:
                with connection.cursor() as cursor:
                    cursor.execute('SELECT EXISTS (SELECT 1 FROM pg_locks WHERE pid = %s AND NOT granted)',
                                   [pids[name]])
                    if cursor.fetchone()[0]:
                        return True
            time.sleep(0.02)
        raise AssertionError(f'{name} neither waited nor finished')

    def test_a_conversion_holding_the_quote_wins_and_the_pass_leaves_it_alone(self):
        quote = self.quote(1)
        locked, release = threading.Event(), threading.Event()

        def conversion():
            # What `convert_quote` does first: lock the quote; then it writes the sale and marks it converted.
            with transaction.atomic():
                SalesQuote.objects.select_for_update().get(pk=quote.pk)
                locked.set()
                if not release.wait(WAIT):
                    raise AssertionError('never released')
                SalesQuote.objects.filter(pk=quote.pk).update(status=SalesQuote.STATUS_CONVERTED,
                                                              converted_at=timezone.now())
            return 'converted'

        threads, outcomes, pids, done = self.in_threads(conversion=conversion)
        self.assertTrue(locked.wait(WAIT))
        more, outcomes2, pids2, done2 = self.in_threads(cron=lambda: quotes.expire_due())
        self.assertTrue(self.blocked(pids2, done2, 'cron'), 'the pass did not wait for the conversion')
        release.set()
        for thread in threads + more:
            thread.join(WAIT)
        self.assertEqual((outcomes['conversion'], outcomes2['cron']), ('converted', 0))
        stored = SalesQuote.objects.get(pk=quote.pk)
        self.assertEqual((stored.status, stored.expired_at), (SalesQuote.STATUS_CONVERTED, None))
        self.assertFalse(expired_audits().exists())

    def test_two_passes_at_once_stamp_each_quote_once(self):
        rows = [self.quote(n) for n in range(1, 6)]
        barrier = threading.Barrier(2)

        def cron():
            barrier.wait(WAIT)
            return quotes.expire_due()

        threads, outcomes, _pids, _done = self.in_threads(first=cron, second=cron)
        for thread in threads:
            thread.join(WAIT * 2)
        self.assertTrue(all(isinstance(value, int) for value in outcomes.values()), outcomes)
        self.assertEqual(outcomes['first'] + outcomes['second'], len(rows))
        self.assertEqual(set(SalesQuote.objects.values_list('status', flat=True)), {SalesQuote.STATUS_EXPIRED})
        self.assertEqual(targets(expired_audits()), {r.pk for r in rows})
        self.assertEqual(expired_audits().count(), len(rows))
