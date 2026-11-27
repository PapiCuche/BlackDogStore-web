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
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.utils import timezone

from store import finance_test_support as fx
from store import trade_in_quote_documents as documents
from store.document_design import names_fiscal_document
from store.document_legends import TRADE_IN_RECEPTION_LEGEND, TRADE_IN_RECEPTION_TITLE
from store.models import Customer, Printer, PrintJob, SalesQuote, TradeInQuote
from store.printing import escpos
from store.printing import services as printing
from store.test_document_design import text_of
from store.test_trade_in_quote import QuoteBase

URL = '/api/admin/trade-in/'
OWNER_LEGEND = ('Cotización referencial válida durante cuatro días desde su emisión, sujeta a la verificación final '
                'del estado, identidad y procedencia del equipo. No constituye aceptación definitiva ni comprobante '
                'de pago.')


def flat(text: str) -> str:
    """The paper's words with every line break folded to one space (a long line wraps on the roll)."""
    return ' '.join(text.split())


def papers(quote_id) -> dict:
    row = TradeInQuote.objects.get(pk=quote_id)
    return {'a4': documents.render_a4_pdf(row), 'ticket80': documents.render_ticket_pdf(row),
            'escpos': documents.escpos_ticket(row)}


def readable(kind, paper: bytes) -> str:
    return flat(paper.decode('cp858', 'replace') if kind == 'escpos' else text_of(paper))


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
        fields = {'name': 'Caja', 'host': '192.168.1.50', 'port': 9100, **over}
        return Printer.objects.create(company=self.company, branch=branch or self.branch, **fields)


class PaperTest(DocumentsBase):
    def test_the_paper_says_what_the_customer_may_see_and_nothing_more(self):
        tariff_conditions = 'Equipo con su cargador original.'
        self.api(self.boss).put(f'{URL}policy/', {'quote_conditions': tariff_conditions}, format='json')
        check = self.clear_check()
        issued = self.issued(check=check, data={'offered_value': '800.00', 'adjustment_reason': 'Rayón profundo'})
        row = TradeInQuote.objects.get(pk=issued['id'])
        for kind, paper in papers(issued['id']).items():
            with self.subTest(kind=kind):
                text = readable(kind, paper)
                for expected in (TRADE_IN_RECEPTION_TITLE, 'CRE-000001', issued['valid_until_display'],
                                 f'****{check.imei_last4}', 'APTO CON DESCUENTOS', '800.00', tariff_conditions,
                                 OWNER_LEGEND, 'Válido por 4 días', 'Válida hasta', 'Evaluación'):
                    self.assertIn(expected, text, expected)
                for hidden in ('1777.77', '111.11', '222.22', '1200.00', 'Rayón profundo', check.imei_digest,
                               'Cliente Uno', '45678912'):
                    self.assertNotIn(hidden, text, hidden)
                self.assertNotIn('requiere', text.lower())
                self.assertFalse(names_fiscal_document(text.replace(OWNER_LEGEND, '')))
        self.assertEqual(row.status, 'issued')
        self.assertEqual(TRADE_IN_RECEPTION_LEGEND, OWNER_LEGEND)

    def test_a_customer_known_only_by_a_document_prints_no_document_number(self):
        nameless = Customer.objects.create(company=self.company, first_name='Temporal', document_type='dni',
                                           document_number='70123456')
        Customer.objects.filter(pk=nameless.pk).update(first_name='', last_name='')   # an older row, no name
        nameless.refresh_from_db()
        self.assertEqual(nameless.display_name, '70123456')
        issued = self.issued(customer=nameless.pk)
        for kind, paper in papers(issued['id']).items():
            with self.subTest(kind=kind):
                self.assertNotIn('70123456', readable(kind, paper))
                self.assertNotIn('Cliente', readable(kind, paper))

    def test_renaming_the_customer_leaves_every_paper_byte_identical(self):
        issued = self.issued()
        before = papers(issued['id'])
        Customer.objects.filter(pk=self.customer.pk).update(first_name='Otro', last_name='Nombre',
                                                            document_number='11111111')
        self.assertEqual(before, papers(issued['id']))

    def test_another_validity_prints_only_its_exact_end(self):
        from store import trade_in_tariff as tariff

        tariff.update_policy(actor=self.boss, company=self.company, data={'quote_validity_hours': 48})
        issued = self.issued()
        for kind, paper in papers(issued['id']).items():
            with self.subTest(kind=kind):
                text = readable(kind, paper)
                self.assertIn(issued['valid_until_display'], text)
                self.assertIn('No constituye aceptación definitiva ni comprobante de pago.', text)
                for wrong in ('cuatro días', '4 días'):
                    self.assertNotIn(wrong, text)

    def test_the_roll_keeps_its_accents(self):
        paper = papers(self.issued()['id'])['escpos']
        for word in ('Válida hasta', 'Evaluación', 'COTIZACIÓN'):
            self.assertIn(word.encode('cp858'), paper, word)

    def test_the_target_price_prints_only_for_whom_the_api_shows_it(self):
        maker = fx.member(self.company, 'tia_cotiza_doc', ['sales.tradein.view', 'sales.tradein.value',
                                                            'sales.quotes.manage', 'sales.quotes.view'])
        from store import trade_in_tariff as tariff

        target = SalesQuote.objects.create(company=self.company, branch=self.branch, number='COT-000001',
                                           sequence_value=1, status='approved',
                                           valid_until=tariff.company_today(self.company) + timedelta(days=5),
                                           total=Decimal('4000.00'))
        created = self.post('assessments/', {'device_check': self.clear_check().pk, 'tariff_entry': self.entry.pk,
                                             'customer': self.customer.pk, 'purpose': 'trade_towards',
                                             'target_sales_quote': target.pk}, user=maker)
        self.post(f'assessments/{created["id"]}/answers/', {'answers': {'pantalla': 'works', 'bateria': '90'}},
                  method='put', user=maker, expect=200)
        self.post(f'assessments/{created["id"]}/calculate/', {}, user=maker, expect=200)
        quote = self.post('quotes/', {'assessment': created['id']}, user=maker)
        self.act(quote['id'], 'issue', user=maker)
        for kind in ('a4', 'ticket80'):
            with self.subTest(kind=kind):
                self.assertNotIn('4000.00', text_of(self.pdf(quote['id'], kind, user=self.reader).content))
                self.assertIn('4000.00', text_of(self.pdf(quote['id'], kind, user=maker).content))
        self.printer()
        path = f'{URL}quotes/{quote["id"]}/print/'
        hidden = self.api(self.reader).post(path, {}, format='json', HTTP_IDEMPOTENCY_KEY='tiq-price-1').json()
        shown = self.api(maker).post(path, {}, format='json', HTTP_IDEMPOTENCY_KEY='tiq-price-2').json()
        self.assertNotIn(b'4000.00', printing.render(PrintJob.objects.get(pk=hidden['id'])))
        self.assertIn(b'4000.00', printing.render(PrintJob.objects.get(pk=shown['id'])))

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
        first = papers(issued['id'])
        # The tariff and the policy move afterwards: the revision's papers do not, in any of the three outputs.
        from store import trade_in_tariff as tariff

        tariff.propose_version(actor=self.admin, company=self.company, entry=self.entry,
                               data={'base_reception_value': '900.00', 'max_reception_value': '1100.00'})
        tariff.update_policy(actor=self.boss, company=self.company,
                             data={'quote_conditions': 'Otras condiciones', 'quote_validity_hours': 24})
        self.assertEqual(first, papers(issued['id']))
        self.assertEqual(self.pdf(issued['id'], 'a4').content, first['a4'])
        self.assertEqual(self.pdf(issued['id'], 'ticket80').content, first['ticket80'])

    def test_another_company_or_branch_has_no_paper(self):
        issued = self.issued()
        far = self.issued(check=self.clear_check(branch=self.far))
        for user, quote_id in ((self.stranger, issued['id']), (self.near_only, far['id'])):
            with self.subTest(user=user.username):
                self.assertEqual(self.api(user).get(f'{URL}quotes/{quote_id}/pdf/').status_code, 404)
                res = self.api(user).post(f'{URL}quotes/{quote_id}/print/', {}, format='json',
                                          HTTP_IDEMPOTENCY_KEY='tiq-print-404')
                self.assertEqual(res.status_code, 404)
        self.assertFalse(PrintJob.objects.exists())


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
        self.assertIn(OWNER_LEGEND, flat(payload.decode('cp858', 'replace')))  # the roll wraps the long line
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

    def test_a_key_already_used_for_another_quote_or_printer_is_a_conflict(self):
        first, second = self.issued(), self.issued()
        printer = self.printer()
        other_printer = self.printer(name='Caja 2', host='192.168.1.52', auto_print=False)
        once = self.api(self.reader).post(f'{URL}quotes/{first["id"]}/print/', {'printer': printer.pk},
                                          format='json', HTTP_IDEMPOTENCY_KEY='tiq-same')
        self.assertEqual(once.status_code, 201)
        for quote_id, chosen in ((second['id'], printer.pk), (first['id'], other_printer.pk)):
            with self.subTest(quote=quote_id, printer=chosen):
                res = self.api(self.reader).post(f'{URL}quotes/{quote_id}/print/', {'printer': chosen},
                                                 format='json', HTTP_IDEMPOTENCY_KEY='tiq-same')
                self.assertEqual(res.status_code, 409, res.content)
        self.assertEqual(PrintJob.objects.count(), 1)

    def test_a_key_lost_to_a_concurrent_request_for_another_quote_is_still_a_conflict(self):
        from django.db.models.query import QuerySet

        first, second = (TradeInQuote.objects.get(pk=self.issued()['id']) for _ in range(2))
        printer = self.printer()
        printing.enqueue_trade_in_quote_ticket(quote=first, printer=printer, key='race', actor=self.boss)
        real_first, seen = QuerySet.first, []

        def blind_once(queryset):
            # The other request has not committed yet when this one looks: the look-up misses, the insert loses.
            if not seen:
                seen.append(True)
                return None
            return real_first(queryset)

        with mock.patch.object(QuerySet, 'first', blind_once), self.assertRaises(printing.PrintConflict):
            printing.enqueue_trade_in_quote_ticket(quote=second, printer=printer, key='race', actor=self.boss)
        self.assertEqual(seen, [True])
        self.assertEqual(PrintJob.objects.get().trade_in_quote_id, first.pk)

    def test_the_queue_names_the_quote_not_an_order(self):
        issued = self.issued()
        self.printer()
        self.api(self.reader).post(f'{URL}quotes/{issued["id"]}/print/', {}, format='json',
                                   HTTP_IDEMPOTENCY_KEY='tiq-print-list')
        cashier = fx.member(self.company, 'tia_caja', ['sales.orders.view'])
        res = self.api(cashier).get('/api/admin/printing/jobs/', {'company': self.company.pk})
        self.assertEqual(res.status_code, 200, res.content)
        row = res.json()['results'][0]
        self.assertEqual((row['kind'], row['order'], row['trade_in_quote'], row['quote_label']),
                         ('trade_in_quote_ticket', None, issued['id'], 'CRE-000001'))

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

    def test_the_rollback_command_cancels_unfinished_and_failed_trade_in_tickets(self):
        issued = self.issued()
        self.printer()
        for key in ('tiq-print-z', 'tiq-print-failed'):
            self.api(self.reader).post(f'{URL}quotes/{issued["id"]}/print/', {}, format='json',
                                       HTTP_IDEMPOTENCY_KEY=key)
        PrintJob.objects.filter(idempotency_key='tradein:tiq-print-failed').update(status='failed')
        out = StringIO()
        call_command('cancel_trade_in_quote_print_jobs', stdout=out)
        self.assertIn('sin terminar o fallidos: 2', out.getvalue())
        out = StringIO()
        call_command('cancel_trade_in_quote_print_jobs', '--apply', stdout=out)
        self.assertIn('cancelados: 2', out.getvalue())
        self.assertEqual(set(PrintJob.objects.values_list('status', flat=True)), {'cancelled'})

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
