"""
TRADE-IN VALUATION PRO · Phase 5b — a trade-in quote's papers and its print queue (docs/adr-trade-in-valuation.md §9).

What these tests hold: only an issued revision prints; the paper comes from that revision's frozen snapshot (a reprint
is the same paper), shows the exact validity in the company's zone, the device masked and the printable verdict
(never «requiere aprobación»), and never an internal estimate, a full identifier, a note or a fiscal document's
name; the roll goes through the shop's print queue with its idempotency, its branch and its printer role; the
database refuses a print job that targets the wrong thing; another company's papers do not exist for you.
"""
import json
from datetime import timedelta
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.utils import timezone

from store import finance_test_support as fx
from store import trade_in_quote as quotes
from store import trade_in_quote_documents as documents
from store.document_design import names_fiscal_document
from store.document_legends import TRADE_IN_RECEPTION_LEGEND, TRADE_IN_RECEPTION_TITLE
from store.models import Printer, PrintJob, TradeInQuote
from store.printing import escpos
from store.printing import services as printing
from store.test_document_design import text_of
from store.test_trade_in_quote import QuoteBase

URL = '/api/admin/trade-in/'


class DocumentsBase(QuoteBase):
    def issued(self, answers=None, **over):
        created = self.quote(answers, **over)
        if created['status'] == 'pending_approval':
            self.act(created['id'], 'approve', user=self.approver)
        return self.act(created['id'], 'issue')

    def pdf(self, quote_id, kind='a4', user=None, expect=200):
        res = self.api(user or self.reader).get(f'{URL}quotes/{quote_id}/pdf/', {'format': kind})
        self.assertEqual(res.status_code, expect, getattr(res, 'content', b'')[:300])
        return res

    def printer(self, branch=None, **over):
        return Printer.objects.create(company=self.company, branch=branch or self.branch, name='Caja',
                                      host='192.168.1.50', port=9100, **over)


class PaperTest(DocumentsBase):
    def test_the_paper_says_what_the_customer_may_see_and_nothing_more(self):
        tariff_conditions = 'Equipo con su cargador original.'
        self.api(self.boss).put(f'{URL}policy/', {'quote_conditions': tariff_conditions}, format='json')
        check = self.clear_check()
        issued = self.issued(check=check, data={'offered_value': '800.00', 'adjustment_reason': 'Rayón profundo'})
        row = TradeInQuote.objects.get(pk=issued['id'])
        for kind in ('a4', 'ticket80'):
            with self.subTest(kind=kind):
                text = text_of(self.pdf(issued['id'], kind).content)
                for expected in (TRADE_IN_RECEPTION_TITLE, 'CRE-000001', issued['valid_until_display'],
                                 f'****{check.imei_last4}', 'APTO CON DESCUENTOS', '800.00', tariff_conditions,
                                 TRADE_IN_RECEPTION_LEGEND):
                    self.assertIn(expected, text, expected)
                for hidden in ('1777.77', '111.11', '222.22', '1200.00', 'Rayón profundo', 'REQUIERE',
                               check.imei_digest):
                    self.assertNotIn(hidden, text, hidden)
                self.assertFalse(names_fiscal_document(text.replace(TRADE_IN_RECEPTION_LEGEND, '')))
        self.assertEqual(row.status, 'issued')

    def test_an_approved_requirement_prints_the_plain_verdict(self):
        issued = self.issued({'pantalla': 'not_working', 'bateria': '90'})
        text = text_of(self.pdf(issued['id'], 'ticket80').content)
        self.assertIn('APTO', text)
        self.assertNotIn('REQUIERE', text)

    def test_only_an_issued_revision_prints_and_its_stamp_follows_its_state(self):
        created = self.quote()
        self.assertEqual(self.pdf(created['id'], expect=409).json()['code'], 'not_issued')
        self.act(created['id'], 'issue')
        later = timezone.now() + timedelta(hours=97)
        with mock.patch('django.utils.timezone.now', return_value=later):
            self.assertIn('VENCIDA', text_of(self.pdf(created['id'], 'ticket80').content))
        revised = self.act(created['id'], 'revise')
        self.assertIn('REEMPLAZADA', text_of(self.pdf(created['id'], 'ticket80').content))
        self.assertEqual(self.pdf(revised['id'], expect=409).json()['code'], 'not_issued')

    def test_a_reprint_is_the_same_paper_from_the_frozen_snapshot(self):
        issued = self.issued()
        row = TradeInQuote.objects.get(pk=issued['id'])
        first = documents.escpos_ticket(row)
        # The tariff and the policy move afterwards: the revision's paper does not.
        from store import trade_in_tariff as tariff

        tariff.propose_version(actor=self.admin, company=self.company, entry=self.entry,
                               data={'base_reception_value': '900.00', 'max_reception_value': '1100.00'})
        tariff.update_policy(actor=self.boss, company=self.company, data={'quote_conditions': 'Otras condiciones'})
        self.assertEqual(first, documents.escpos_ticket(TradeInQuote.objects.get(pk=issued['id'])))

    def test_another_company_has_no_paper(self):
        issued = self.issued()
        self.assertEqual(self.api(self.stranger).get(f'{URL}quotes/{issued["id"]}/pdf/').status_code, 404)


class PrintQueueTest(DocumentsBase):
    def test_the_roll_goes_through_the_queue_once_per_key(self):
        issued = self.issued()
        printer = self.printer()
        path = f'{URL}quotes/{issued["id"]}/print/'
        first = self.api(self.reader).post(path, {}, format='json', HTTP_IDEMPOTENCY_KEY='tiq-print-0001')
        again = self.api(self.reader).post(path, {}, format='json', HTTP_IDEMPOTENCY_KEY='tiq-print-0001')
        self.assertEqual((first.status_code, again.status_code), (201, 200), first.content)
        job = PrintJob.objects.get()
        self.assertEqual((job.kind, job.trade_in_quote_id, job.order_id, job.printer_id),
                         ('trade_in_quote_ticket', issued['id'], None, printer.pk))
        payload = printing.render(job)
        self.assertIn(b'CRE-000001', payload)
        self.assertIn(b'No es comprobante de pago', payload)          # the roll wraps the long line
        self.assertEqual(payload, documents.escpos_ticket(
            job.trade_in_quote, paper_width_mm=printer.paper_width_mm, encoding=printer.encoding,
            dots=escpos.printable_dots(printer.paper_width_mm, printer.print_width_dots)))
        reprint = self.api(self.reader).post(path, {}, format='json', HTTP_IDEMPOTENCY_KEY='tiq-print-0002')
        self.assertEqual(reprint.status_code, 201)
        self.assertEqual(PrintJob.objects.count(), 2)
        agent, _token = printing.create_agent(company=self.company, branch=self.branch, name='PC', actor=self.boss)
        claimed = printing.claim_next(agent)
        self.assertEqual((claimed.pk, claimed.status), (job.pk, 'printing'))

    def test_a_quote_that_was_not_issued_another_branch_or_a_label_printer_are_refused(self):
        created = self.quote()
        self.printer()
        res = self.api(self.reader).post(f'{URL}quotes/{created["id"]}/print/', {}, format='json',
                                         HTTP_IDEMPOTENCY_KEY='tiq-print-x')
        self.assertEqual((res.status_code, res.json()['code']), (409, 'not_issued'))
        self.act(created['id'], 'issue')
        elsewhere = self.printer(branch=self.far)
        res = self.api(self.reader).post(f'{URL}quotes/{created["id"]}/print/', {'printer': elsewhere.pk},
                                         format='json', HTTP_IDEMPOTENCY_KEY='tiq-print-y')
        self.assertEqual(res.status_code, 400)
        row = TradeInQuote.objects.get(pk=created['id'])
        label = Printer.objects.create(company=self.company, branch=self.branch, name='Etiquetas', host='192.168.1.51',
                                       port=9100, role=Printer.Role.LABEL)
        with self.assertRaises(printing.PrintError):
            printing.enqueue_trade_in_quote_ticket(quote=row, printer=label, key='k', actor=self.boss)
        res = self.api(self.reader).post(f'{URL}quotes/{created["id"]}/print/', {}, format='json')
        self.assertEqual(res.status_code, 400)                      # no Idempotency-Key

    def test_the_database_refuses_a_job_with_the_wrong_target(self):
        issued = self.issued()
        printer = self.printer()
        row = TradeInQuote.objects.get(pk=issued['id'])
        with self.assertRaises(IntegrityError), transaction.atomic():
            PrintJob.objects.create(company=self.company, branch=self.branch, printer=printer,
                                    kind='trade_in_quote_ticket', reason='manual', idempotency_key='a')
        with self.assertRaises(IntegrityError), transaction.atomic():
            PrintJob.objects.create(company=self.company, branch=self.branch, printer=printer,
                                    kind='sales_quote_ticket', reason='manual', trade_in_quote=row,
                                    idempotency_key='b')

    def test_the_rollback_command_cancels_unfinished_trade_in_tickets(self):
        issued = self.issued()
        self.printer()
        self.api(self.reader).post(f'{URL}quotes/{issued["id"]}/print/', {}, format='json',
                                   HTTP_IDEMPOTENCY_KEY='tiq-print-z')
        out = StringIO()
        call_command('cancel_trade_in_quote_print_jobs', stdout=out)
        self.assertIn('sin terminar: 1', out.getvalue())
        out = StringIO()
        call_command('cancel_trade_in_quote_print_jobs', '--apply', stdout=out)
        self.assertIn('cancelados: 1', out.getvalue())
        self.assertEqual(PrintJob.objects.get().status, 'cancelled')

    def test_downloads_and_print_requests_are_audited_without_identifiers(self):
        from store.models import AdminAuditLog

        check = self.clear_check()
        issued = self.issued(check=check)
        self.printer()
        self.pdf(issued['id'])
        self.api(self.reader).post(f'{URL}quotes/{issued["id"]}/print/', {}, format='json',
                                   HTTP_IDEMPOTENCY_KEY='tiq-print-audit')
        actions = set(AdminAuditLog.objects.filter(company=self.company).values_list('action', flat=True))
        self.assertTrue({'trade_in_quote_pdf_downloaded', 'trade_in_quote_print_requested'} <= actions)
        text = json.dumps(list(AdminAuditLog.objects.filter(company=self.company).values_list('metadata', flat=True)))
        self.assertNotIn(check.imei_digest, text)
