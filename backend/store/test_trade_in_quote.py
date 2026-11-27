"""
TRADE-IN VALUATION PRO · Phase 5a — the trade-in quote, its states and its exact expiry (adr §3.5, §5.3).

What these tests hold: a quote is generated only from a calculated or approved assessment that is eligible, and
freezes it; approving and issuing refuse a stale snapshot; an issued quote holds until an exact instant (96 hours by
default) and is refused as expired even before the cron stamps it; the cron stamps once; a revision recomputes with
what is in force and never rewrites the previous one; internal estimates stay internal; another company or branch
does not exist for you; nothing is bought, received, paid or credited.
"""
import json
import threading
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import connection
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from store import finance_test_support as fx
from store import trade_in_quote as quotes
from store import trade_in_tariff as tariff
from store.capabilities import DEFAULT_ADMIN_CAPABILITY_CODES, EXPLICIT_GRANT_CODES
from store.models import (
    AdminAuditLog,
    FiscalDocument,
    Obligation,
    Order,
    PaymentTransaction,
    StockMovement,
    StockUnit,
    TradeInAssessment,
    TradeInQuote,
)
from store.test_trade_in_assessment import APPROVE, VALUE, VIEW, AssessmentBase

D = Decimal
APPROVE_OWN = 'sales.tradein.approve_own'
URL = '/api/admin/trade-in/'


class QuoteBase(AssessmentBase):
    def quote(self, answers=None, data=None, expect=201, **over):
        done = self.calculated(answers, data, **over)
        return self.post('quotes/', {'assessment': done['id']}, expect=expect)

    def act(self, quote_id, action, user=None, expect=200, **data):
        return self.post(f'quotes/{quote_id}/{action}/', data, user=user, expect=expect)


class GenerateTest(QuoteBase):
    def test_an_eligible_assessment_becomes_an_approved_quote_and_is_frozen(self):
        created = self.quote()
        self.assertEqual((created['number'], created['status'], created['offered_value']),
                         ('CRE-000001', 'approved', '850.00'))
        assessment = TradeInAssessment.objects.get(pk=created['assessment'])
        self.assertEqual(assessment.status, 'quoted')
        res = self.post(f'assessments/{assessment.pk}/answers/', {'answers': {'bateria': '99'}}, method='put',
                        expect=409)
        self.assertEqual(res['code'], 'not_editable')
        row = TradeInQuote.objects.get(pk=created['id'])
        self.assertEqual((row.approval_kind, row.max_reception_value, row.tariff_version_id),
                         ('not_needed', D('1200.00'), assessment.tariff_version_id))

    def test_what_needs_approval_waits_and_an_approved_assessment_carries_its_approval(self):
        pending = self.quote({'pantalla': 'not_working', 'bateria': '90'})
        self.assertEqual(pending['status'], 'pending_approval')
        done = self.calculated({'pantalla': 'not_working', 'bateria': '90'})
        self.post(f'assessments/{done["id"]}/approve/', {}, user=self.approver, expect=200)
        carried = self.post('quotes/', {'assessment': done['id']})
        self.assertEqual((carried['status'], carried['approval_kind'], carried['approved_by']),
                         ('approved', 'assessment', 'tia_aprueba'))

    def test_not_eligible_pending_or_stale_assessments_are_not_quoted(self):
        from store import trade_in_eligibility as eligibility

        blocked = self.clear_check()
        eligibility.answer_blocker(actor=self.valuer, company=self.company, check=blocked,
                                   data={'code': 'activation_lock', 'answer': 'blocked'})
        self.assertEqual(self.quote(check=blocked, expect=409)['code'], 'not_eligible')
        self.assertEqual(self.quote(check=self.clear_check(clean=False), expect=409)['code'],
                         'pending_verification')
        done = self.calculated()
        tariff.propose_version(actor=self.admin, company=self.company, entry=self.entry,
                               data={'base_reception_value': '900.00', 'max_reception_value': '1100.00'})
        res = self.post('quotes/', {'assessment': done['id']}, expect=409)
        self.assertEqual((res['code'], res['reasons']), ('calculation_stale', ['tariff']))


class ApprovalTest(QuoteBase):
    def test_another_person_approves_or_the_author_with_approve_own_and_a_reason(self):
        pending = self.quote({'pantalla': 'not_working', 'bateria': '90'})
        self.assertEqual(self.act(pending['id'], 'approve', user=self.valuer, expect=403)['detail'],
                         'No tienes permisos para esta operación.')
        both = fx.member(self.company, 'tiq_ambos', [VIEW, VALUE, APPROVE])
        own = fx.member(self.company, 'tiq_propio', [VIEW, VALUE, APPROVE, APPROVE_OWN])
        mine = self.quote({'pantalla': 'not_working', 'bateria': '90'})
        TradeInQuote.objects.filter(pk=mine['id']).update(created_by=both)
        self.assertEqual(self.act(mine['id'], 'approve', user=both, expect=403)['code'], 'own_change')
        TradeInQuote.objects.filter(pk=mine['id']).update(created_by=own)
        self.assertEqual(self.act(mine['id'], 'approve', user=own, expect=400)['code'], 'reason_required')
        done = self.act(mine['id'], 'approve', user=own, note='Equipo pequeño')
        self.assertEqual(done['approval_kind'], 'self')
        self.assertEqual(self.act(pending['id'], 'approve', user=self.approver)['approval_kind'], 'approver')

    def test_reject_needs_a_reason(self):
        pending = self.quote({'pantalla': 'not_working', 'bateria': '90'})
        self.act(pending['id'], 'reject', user=self.approver, expect=400)
        self.assertEqual(self.act(pending['id'], 'reject', user=self.approver, note='Muy alto')['status'], 'rejected')

    def test_approve_own_also_opens_the_assessment_approval(self):
        own = fx.member(self.company, 'tiq_propio2', [VIEW, VALUE, APPROVE, APPROVE_OWN])
        created = self.post('assessments/', {'device_check': self.clear_check().pk, 'tariff_entry': self.entry.pk,
                                             'customer': self.customer.pk}, user=own)
        self.post(f'assessments/{created["id"]}/answers/', {'answers': {'pantalla': 'not_working', 'bateria': '90'}},
                  method='put', user=own, expect=200)
        self.post(f'assessments/{created["id"]}/calculate/', {}, user=own, expect=200)
        self.assertEqual(self.post(f'assessments/{created["id"]}/approve/', {}, user=own, expect=400)['code'],
                         'reason_required')
        self.assertEqual(self.post(f'assessments/{created["id"]}/approve/', {'note': 'Solo yo hoy'}, user=own,
                                   expect=200)['status'], 'approved')
        self.assertTrue(AdminAuditLog.objects.get(action='trade_in_assessment_approved').metadata['self_approval'])


class IssueAndExpiryTest(QuoteBase):
    def test_an_issued_quote_holds_exactly_the_companys_hours(self):
        created = self.quote()
        issued = self.act(created['id'], 'issue')
        row = TradeInQuote.objects.get(pk=created['id'])
        self.assertEqual(row.valid_until - row.issued_at, timedelta(hours=96))
        self.assertEqual(issued['status'], 'issued')
        self.assertTrue(issued['valid_until_display'])
        tariff.update_policy(actor=self.boss, company=self.company, data={'quote_validity_hours': 2})
        other = self.quote()
        self.act(other['id'], 'issue')
        row = TradeInQuote.objects.get(pk=other['id'])
        self.assertEqual(row.valid_until - row.issued_at, timedelta(hours=2))

    def test_the_instant_is_absolute_and_the_zone_only_displays_it(self):
        from store.models import CompanySettings

        CompanySettings.objects.update_or_create(company=self.company, defaults={'timezone': 'Asia/Tokyo'})
        self.company.refresh_from_db()
        created = self.quote()
        self.act(created['id'], 'issue')
        row = TradeInQuote.objects.get(pk=created['id'])
        self.assertEqual(row.valid_until - row.issued_at, timedelta(hours=96))
        shown = quotes.display_valid_until(self.company, row)
        tokyo = timezone.localtime(row.valid_until, tariff.company_timezone(self.company))
        self.assertEqual(shown, tokyo.strftime('%d/%m/%Y %H:%M'))

    def test_an_expired_quote_is_refused_before_the_cron_and_the_cron_stamps_it_once(self):
        created = self.quote()
        self.act(created['id'], 'issue')
        later = timezone.now() + timedelta(hours=97)
        with mock.patch('django.utils.timezone.now', return_value=later):
            res = self.act(created['id'], 'accept', expect=409)
            self.assertEqual(res['code'], 'expired')
            self.assertEqual(self.api(self.reader).get(f'{URL}quotes/{created["id"]}/').json()['status'], 'expired')
            self.assertEqual(TradeInQuote.objects.get(pk=created['id']).status, 'issued')   # a read wrote nothing
            out = StringIO()
            call_command('expire_trade_in_quotes', '--dry-run', stdout=out)
            self.assertIn('por vencer: 1', out.getvalue())
            out = StringIO()
            call_command('expire_trade_in_quotes', stdout=out)
            self.assertIn('vencidas: 1', out.getvalue())
            out = StringIO()
            call_command('expire_trade_in_quotes', stdout=out)
            self.assertIn('vencidas: 0', out.getvalue())
        self.assertEqual(TradeInQuote.objects.get(pk=created['id']).status, 'expired')
        self.assertEqual(AdminAuditLog.objects.filter(action='trade_in_quote_expired').count(), 1)

    def test_issuing_refuses_a_stale_snapshot(self):
        created = self.quote()
        tariff.propose_version(actor=self.admin, company=self.company, entry=self.entry,
                               data={'base_reception_value': '900.00', 'max_reception_value': '1100.00'})
        res = self.act(created['id'], 'issue', expect=409)
        self.assertEqual((res['code'], res['reasons']), ('calculation_stale', ['tariff']))

    def test_answers_and_cancellation_follow_the_states(self):
        created = self.quote()
        self.act(created['id'], 'accept', expect=409)
        self.act(created['id'], 'issue')
        self.assertEqual(self.act(created['id'], 'accept', note='Trae el equipo mañana')['status'], 'accepted')
        self.assertEqual(self.act(created['id'], 'cancel', reason='x', expect=409)['code'], 'not_cancellable')
        other = self.quote()
        self.assertEqual(self.act(other['id'], 'cancel', reason='Se arrepintió')['status'], 'cancelled')


class RevisionTest(QuoteBase):
    def test_a_revision_recomputes_and_the_previous_keeps_its_snapshot(self):
        created = self.quote()
        self.act(created['id'], 'issue')
        tariff.propose_version(actor=self.admin, company=self.company, entry=self.entry,
                               data={'base_reception_value': '900.00', 'max_reception_value': '1100.00'})
        revised = self.act(created['id'], 'revise')
        self.assertEqual((revised['number'], revised['revision'], revised['offered_value']),
                         ('CRE-000001-R2', 2, '760.00'))          # 900 − 10 % − 50
        old = TradeInQuote.objects.get(pk=created['id'])
        self.assertEqual((old.status, old.offered_value), ('superseded', D('850.00')))
        self.assertEqual(revised['source_quote'], created['id'])
        self.assertEqual([r['revision'] for r in revised['revisions']], [1, 2])

    def test_revising_an_accepted_quote_needs_a_reason(self):
        created = self.quote()
        self.act(created['id'], 'issue')
        self.act(created['id'], 'accept')
        self.assertEqual(self.act(created['id'], 'revise', expect=400)['code'], 'reason_required')
        revised = self.act(created['id'], 'revise', revision_reason='El cliente pidió revisar la batería')
        self.assertEqual(revised['revision_reason'], 'El cliente pidió revisar la batería')

    def test_a_snapshot_is_never_rewritten_nor_deleted(self):
        created = self.quote()
        row = TradeInQuote.objects.get(pk=created['id'])
        row.offered_value = D('1.00')
        with self.assertRaises(ValidationError):
            row.save(update_fields=['offered_value'])
        row.refresh_from_db()
        row.status = 'accepted'
        with self.assertRaises(ValidationError):
            row.save(update_fields=['status'])               # approved → accepted is not a move
        with self.assertRaises(ValidationError):
            row.delete()


class VisibilityTest(QuoteBase):
    def test_internal_estimates_only_for_the_tariff_or_costs(self):
        created = self.quote()
        self.assertNotIn('internal', created)
        text = json.dumps(created)
        for internal in ('1777.77', '111.11', '222.22'):
            self.assertNotIn(internal, text)
        insider = fx.member(self.company, 'tiq_tarifa', [VIEW, 'sales.tradein.tariff.view'])
        seen = self.api(insider).get(f'{URL}quotes/{created["id"]}/').json()
        self.assertEqual(seen['internal']['resale_value'], '1777.77')

    def test_another_company_or_branch_answers_404_and_doors_ask_their_capability(self):
        created = self.quote(check=self.clear_check(branch=self.far))
        for user in (self.stranger, self.near_only):
            with self.subTest(user=user.username):
                self.assertEqual(self.api(user).get(f'{URL}quotes/{created["id"]}/').status_code, 404)
                self.assertEqual(self.api(user).post(f'{URL}quotes/{created["id"]}/issue/', {},
                                                     format='json').status_code, 404)
        self.assertEqual(self.api(self.reader).post(f'{URL}quotes/{created["id"]}/issue/', {},
                                                    format='json').status_code, 403)

    def test_approve_own_is_an_explicit_grant_with_its_repair(self):
        from importlib import import_module

        self.assertIn(APPROVE_OWN, EXPLICIT_GRANT_CODES)
        self.assertNotIn(APPROVE_OWN, DEFAULT_ADMIN_CAPABILITY_CODES)
        self.assertEqual(import_module('store.migrations.0198_trade_in_quote').CODES, {APPROVE_OWN})


class GuardsTest(QuoteBase):
    def test_nothing_is_bought_received_paid_or_credited(self):
        counts = (Order.objects.count(), StockMovement.objects.count(), StockUnit.objects.count(),
                  PaymentTransaction.objects.count(), Obligation.objects.count(), FiscalDocument.objects.count())
        created = self.quote()
        self.act(created['id'], 'issue')
        self.act(created['id'], 'accept')
        self.assertEqual(counts, (Order.objects.count(), StockMovement.objects.count(), StockUnit.objects.count(),
                                  PaymentTransaction.objects.count(), Obligation.objects.count(),
                                  FiscalDocument.objects.count()))
        self.assertFalse(AdminAuditLog.objects.filter(action__startswith='trade_in_quote',
                                                      company__isnull=True).exclude(action='trade_in_quote_expired')
                         .exists())

    def test_the_engine_names_no_maker_or_shop(self):
        import pathlib

        root = pathlib.Path(quotes.__file__).parent
        for name in ('trade_in_quote.py', 'trade_in_quote_views.py'):
            text = (root / name).read_text(encoding='utf-8').lower()
            for word in ('iphone', 'apple', 'samsung', 'black dog', 'blackdog', 'icloud'):
                with self.subTest(file=name, word=word):
                    self.assertNotIn(word, text)


class ReviewFindingsTest(QuoteBase):
    """Review of #465: audit of revisions, policy, cancel without dead end, frozen print columns, filters, stale paths."""

    def test_a_revision_audits_the_status_it_had(self):
        created = self.quote()
        self.act(created['id'], 'issue')
        self.act(created['id'], 'revise')
        audit = AdminAuditLog.objects.get(action='trade_in_quote_revised')
        self.assertEqual(audit.metadata['previous_status'], 'issued')

    def test_the_policy_shows_and_audits_the_quote_and_customer_settings(self):
        res = self.api(self.boss).put(f'{URL}policy/', {'quote_validity_hours': 48, 'quote_conditions': 'Equipo con '
                                                        'cargador.', 'require_identified_customer': False},
                                      format='json')
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        self.assertEqual((body['quote_validity_hours'], body['quote_conditions'], body['require_identified_customer']),
                         (48, 'Equipo con cargador.', False))
        audit = AdminAuditLog.objects.filter(action='trade_in_policy_updated').latest('pk')
        self.assertEqual((audit.metadata['before']['quote_validity_hours'], audit.metadata['after']['quote_validity_hours']),
                         (96, 48))
        self.assertNotIn('quote_conditions', audit.metadata['after'])
        for hours in (0, 721, 'mucho'):
            with self.subTest(hours=hours):
                res = self.api(self.boss).put(f'{URL}policy/', {'quote_validity_hours': hours}, format='json')
                self.assertEqual((res.status_code, res.json()['field']), (400, 'quote_validity_hours'))

    def test_cancelling_a_quote_frees_the_device_check(self):
        check = self.clear_check()
        created = self.quote(check=check)
        self.act(created['id'], 'cancel', reason='El cliente se arrepintió')
        assessment = TradeInAssessment.objects.get(pk=created['assessment'])
        self.assertEqual((assessment.status, assessment.cancel_reason), ('cancelled', 'El cliente se arrepintió'))
        self.assertTrue(AdminAuditLog.objects.filter(action='trade_in_assessment_cancelled',
                                                     metadata__because='quote_cancelled').exists())
        self.opened(check=check)                                   # a new assessment on the same device check

    def test_a_quoted_assessment_is_cancelled_only_by_that_narrow_save(self):
        created = self.quote()
        row = TradeInAssessment.objects.get(pk=created['assessment'])
        row.status = 'cancelled'
        with self.assertRaises(ValidationError):
            row.save()                                            # a full save would rewrite everything
        with self.assertRaises(ValidationError):
            row.save(update_fields=['status', 'offered_value'])
        row.save(update_fields=['status', 'cancelled_at', 'cancel_reason', 'updated_at'])

    def test_documents_have_a_frozen_verdict_that_never_asks_for_approval(self):
        done = self.calculated({'pantalla': 'not_working', 'bateria': '90'})
        self.post(f'assessments/{done["id"]}/approve/', {}, user=self.approver, expect=200)
        created = self.post('quotes/', {'assessment': done['id']})
        row = TradeInQuote.objects.get(pk=created['id'])
        self.assertEqual((row.eligibility, row.printable_eligibility, row.needs_approval),
                         ('requires_approval', 'eligible', True))
        plain = TradeInQuote.objects.get(pk=self.quote()['id'])
        self.assertEqual((plain.printable_eligibility, plain.needs_approval), ('eligible_with_deductions', False))

    def test_a_self_approved_assessment_stays_a_self_approval_on_its_quote(self):
        own = fx.member(self.company, 'tiq_propio3', [VIEW, VALUE, APPROVE, APPROVE_OWN])
        created = self.post('assessments/', {'device_check': self.clear_check().pk, 'tariff_entry': self.entry.pk,
                                             'customer': self.customer.pk}, user=own)
        self.post(f'assessments/{created["id"]}/answers/', {'answers': {'pantalla': 'not_working', 'bateria': '90'}},
                  method='put', user=own, expect=200)
        self.post(f'assessments/{created["id"]}/calculate/', {}, user=own, expect=200)
        self.post(f'assessments/{created["id"]}/approve/', {'note': 'Solo yo'}, user=own, expect=200)
        quote = self.post('quotes/', {'assessment': created['id']}, user=own)
        self.assertEqual(quote['approval_kind'], 'self')

    def test_the_status_filter_derives_issued_and_expired(self):
        created = self.quote()
        self.act(created['id'], 'issue')
        later = timezone.now() + timedelta(hours=97)

        def listed(status):
            return [q['id'] for q in self.api(self.reader).get(f'{URL}quotes/', {'status': status}).json()['results']]

        self.assertEqual(listed('issued'), [created['id']])
        with mock.patch('django.utils.timezone.now', return_value=later):
            self.assertEqual(listed('issued'), [])
            self.assertEqual(listed('expired'), [created['id']])

    def test_stale_at_approval_and_security_or_price_at_issue(self):
        pending = self.quote({'pantalla': 'not_working', 'bateria': '90'})
        tariff.propose_version(actor=self.admin, company=self.company, entry=self.entry,
                               data={'base_reception_value': '900.00', 'max_reception_value': '1100.00'})
        res = self.act(pending['id'], 'approve', user=self.approver, expect=409)
        self.assertEqual((res['code'], res['reasons']), ('calculation_stale', ['tariff']))

    def test_security_and_target_price_make_an_issue_stale(self):
        from store import trade_in_eligibility as eligibility
        from store.models import Product

        check = self.clear_check()
        created = self.quote(check=check)
        eligibility.record_identity(actor=self.valuer, company=self.company, check=check,
                                    data={'slot': 'imei', 'result': 'reported', 'source': self.source.pk})
        self.assertEqual(self.act(created['id'], 'issue', expect=409)['reasons'], ['security'])
        product = Product.objects.create(company=self.company, name='Nuevo', slug='nuevo-q', price='4500.00',
                                         inventory=0)
        trading = self.quote(purpose='trade_towards', target_product=product.pk)
        Product.objects.filter(pk=product.pk).update(price=D('4000.00'))
        self.assertEqual(self.act(trading['id'], 'issue', expect=409)['reasons'], ['target_price'])

    def test_free_texts_refuse_the_devices_identifiers_and_never_reach_the_audit(self):
        from store.device_identity import luhn_check_digit

        check = self.clear_check()
        full = f'{35693803564300 + self.counter}'
        full += luhn_check_digit(full)
        created = self.quote({'pantalla': 'not_working', 'bateria': '90'}, check=check)
        self.assertEqual(self.act(created['id'], 'reject', user=self.approver, note=f'IMEI {full}',
                                  expect=400)['code'], 'identifier_in_text')
        self.assertEqual(self.act(created['id'], 'cancel', reason=full[:11], expect=400)['code'],
                         'identifier_in_text')
        self.act(created['id'], 'approve', user=self.approver, note='Revisado en mostrador')
        self.act(created['id'], 'issue')
        self.act(created['id'], 'accept', note='Trae caja y cargador')
        self.assertEqual(self.act(created['id'], 'revise', revision_reason=f'serie {full}', expect=400)['code'],
                         'identifier_in_text')
        texts = json.dumps(list(AdminAuditLog.objects.filter(company=self.company).values_list('metadata',
                                                                                         flat=True)))
        for secret in (full, check.imei_digest, 'Revisado en mostrador', 'Trae caja y cargador'):
            self.assertNotIn(secret, texts)

    def test_the_cron_can_be_limited_to_a_company(self):
        created = self.quote()
        self.act(created['id'], 'issue')
        later = timezone.now() + timedelta(hours=97)
        with mock.patch('django.utils.timezone.now', return_value=later):
            out = StringIO()
            call_command('expire_trade_in_quotes', '--company', self.other.slug, stdout=out)
            self.assertIn('vencidas: 0', out.getvalue())
            out = StringIO()
            call_command('expire_trade_in_quotes', '--company', self.company.slug, stdout=out)
            self.assertIn('vencidas: 1', out.getvalue())

    def test_approval_and_issue_are_written_once(self):
        created = self.quote()
        self.act(created['id'], 'issue')
        row = TradeInQuote.objects.get(pk=created['id'])
        row.valid_until = row.valid_until + timedelta(days=30)
        with self.assertRaises(ValidationError):
            row.save(update_fields=['valid_until'])
        row.refresh_from_db()
        row.approved_at = timezone.now()
        with self.assertRaises(ValidationError):
            row.save(update_fields=['approved_at'])


class TwoExpiryPassesAtOnceTest(TransactionTestCase):
    """Two cron passes at the same instant stamp each quote once (row locks), with one audit entry."""

    def setUp(self):
        if connection.vendor != 'postgresql':
            self.skipTest('Only PostgreSQL has the row locks this test is about.')
        base = QuoteBase('setUp')
        base.setUp()
        self.base = base

    def test_stamped_once(self):
        base = self.base
        created = base.quote()
        quotes.issue(actor=base.valuer, company=base.company, quote=TradeInQuote.objects.get(pk=created['id']))
        later = timezone.now() + timedelta(hours=97)
        start = threading.Barrier(2)
        counts = []

        def run():
            try:
                start.wait(5)
                counts.append(quotes.expire_due(now=later))
            finally:
                connection.close()

        threads = [threading.Thread(target=run) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        self.assertEqual(sorted(counts), [0, 1])
        self.assertEqual(AdminAuditLog.objects.filter(action='trade_in_quote_expired').count(), 1)
