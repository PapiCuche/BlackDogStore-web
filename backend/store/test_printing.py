"""
La cola de impresión de la tienda — PAYMENT-FISCAL-PRINT-01.

  · UN TRABAJO POR DOCUMENTO. Confirmar dos veces la misma venta no imprime dos
    tickets: la clave del trabajo sale del documento, no de la petición.
  · SÓLO TRAS UNA CONFIRMACIÓN AUTORITATIVA. Un pedido que no está pagado no
    produce ningún trabajo, lo pida quien lo pida.
  · CADA LOCAL, LO SUYO. Un agente recoge los trabajos de su sucursal y ninguno
    más: ni los de otra sucursal de la misma empresa ni los de otra empresa.
  · AL MENOS UNA VEZ, Y CERRADO UNA SOLA. Un trabajo entregado y no confirmado
    vuelve a la cola; uno confirmado no se vuelve a entregar; una confirmación
    repetida no cambia nada.
"""
import re
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from store.models import Branch, PrintAgent, Printer, PrintJob
from store.printing import services as printing
from store.printing.escpos import Receipt
from store.sales_note_services import get_or_create_sales_note
from store.test_fiscal_print import FiscalPrintBase
from store.tests import _p3_company


class PrintingBase(FiscalPrintBase):
    def setUp(self):
        super().setUp()
        self.branch = self.company.default_inventory_branch
        self.printer = Printer.objects.create(
            company=self.company, branch=self.branch, name='Caja', host='192.168.1.50')
        self.agent, self.token = printing.create_agent(
            company=self.company, branch=self.branch, name='Mostrador', actor=None)


class EnqueueTest(PrintingBase):
    def test_a_signed_document_of_a_paid_order_queues_one_ticket(self):
        document = self.issue()

        job = PrintJob.objects.get()
        self.assertEqual(job.fiscal_document, document)
        self.assertEqual(
            (job.kind, job.reason, job.status, job.branch, job.printer),
            (PrintJob.Kind.FISCAL_TICKET, PrintJob.Reason.AUTO, PrintJob.Status.PENDING,
             self.branch, self.printer))

    def test_confirming_the_same_document_again_queues_nothing_new(self):
        document = self.issue()

        again = printing.enqueue_fiscal_ticket(document)
        third = printing.enqueue_fiscal_ticket(document)

        self.assertEqual(PrintJob.objects.count(), 1)
        self.assertEqual(again.pk, third.pk)

    def test_an_order_that_is_not_paid_prints_nothing(self):
        document = self.issue()
        PrintJob.objects.all().delete()
        document.order.status = 'pending_payment'
        document.order.paid = False
        document.order.save(update_fields=['status', 'paid'])
        document.refresh_from_db()

        self.assertIsNone(printing.enqueue_fiscal_ticket(document))
        self.assertEqual(PrintJob.objects.count(), 0)

    def test_an_unsigned_document_prints_nothing(self):
        document = self.issue()
        PrintJob.objects.all().delete()
        document.signed_xml = ''
        document.save(update_fields=['signed_xml'])

        self.assertIsNone(printing.enqueue_fiscal_ticket(document))

    def test_a_branch_without_an_automatic_printer_queues_nothing(self):
        self.printer.auto_print = False
        self.printer.save(update_fields=['auto_print'])

        self.issue()

        self.assertEqual(PrintJob.objects.count(), 0)

    def test_a_sales_note_queues_its_own_ticket_once(self):
        document = self.issue()
        PrintJob.objects.all().delete()
        note, _ = get_or_create_sales_note(document.order, actor=None)

        first = printing.enqueue_sales_note_ticket(note)
        second = printing.enqueue_sales_note_ticket(note)

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(first.kind, PrintJob.Kind.SALES_NOTE_TICKET)


class AgentTest(PrintingBase):
    def test_the_token_is_shown_once_and_only_its_hash_is_kept(self):
        self.assertTrue(self.token.startswith('bdpa_'))
        self.assertNotIn(self.token, self.agent.token_hash)
        self.assertEqual(len(self.agent.token_hash), 64)
        self.assertEqual(printing.authenticate_agent(self.token), self.agent)
        self.assertIsNone(printing.authenticate_agent(self.token + 'x'))
        self.assertIsNone(printing.authenticate_agent(''))

    def test_a_deactivated_agent_stops_working_at_once(self):
        self.agent.is_active = False
        self.agent.save(update_fields=['is_active'])

        self.assertIsNone(printing.authenticate_agent(self.token))

    def test_the_agent_receives_the_job_ready_for_the_printer(self):
        self.issue()

        job = printing.claim_next(self.agent)

        self.assertEqual(job.status, PrintJob.Status.PRINTING)
        self.assertEqual(job.attempts, 1)
        self.assertEqual(job.claimed_by, self.agent)
        self.assertTrue(job.claim_token)
        payload = printing.render(job)
        self.assertTrue(payload.startswith(b'\x1b@'))          # reinicio de la impresora
        self.assertIn(b'B001-1', payload)
        self.assertIn(b'\x1dV', payload)                        # corte de papel
        self.assertIsNone(printing.claim_next(self.agent))

    def test_a_confirmed_job_is_closed_and_never_delivered_again(self):
        self.issue()
        job = printing.claim_next(self.agent)

        done = printing.complete(self.agent, job.pk, job.claim_token, ok=True)

        self.assertEqual(done.status, PrintJob.Status.PRINTED)
        self.assertIsNotNone(done.printed_at)
        self.assertIsNone(printing.claim_next(self.agent))

    def test_confirming_twice_changes_nothing(self):
        self.issue()
        job = printing.claim_next(self.agent)
        first = printing.complete(self.agent, job.pk, job.claim_token, ok=True)

        second = printing.complete(self.agent, job.pk, job.claim_token, ok=True)

        self.assertEqual((second.status, second.printed_at), (first.status, first.printed_at))
        self.assertEqual(second.attempts, 1)

    def test_a_job_that_was_never_confirmed_goes_back_to_the_queue(self):
        self.issue()
        job = printing.claim_next(self.agent)
        PrintJob.objects.filter(pk=job.pk).update(
            lease_expires_at=timezone.now() - timedelta(seconds=1))

        again = printing.claim_next(self.agent)

        self.assertEqual(again.pk, job.pk)
        self.assertEqual(again.attempts, 2)
        self.assertNotEqual(again.claim_token, job.claim_token)
        # Quien llega tarde con la entrega antigua ya no puede cerrarlo.
        with self.assertRaises(printing.PrintConflict):
            printing.complete(self.agent, job.pk, job.claim_token, ok=True)

    def test_a_printer_failure_is_retried_and_then_given_up(self):
        self.issue()
        for attempt in range(1, printing.MAX_ATTEMPTS + 1):
            PrintJob.objects.update(available_at=None)      # pasó la espera
            job = printing.claim_next(self.agent)
            self.assertEqual(job.attempts, attempt)
            job = printing.complete(
                self.agent, job.pk, job.claim_token, ok=False, error='sin papel')

        self.assertEqual(job.status, PrintJob.Status.FAILED)
        self.assertEqual(job.last_error, 'sin papel')
        self.assertIsNone(printing.claim_next(self.agent))

    def test_a_failed_job_can_be_sent_again_by_a_person(self):
        self.issue()
        job = printing.claim_next(self.agent)
        PrintJob.objects.filter(pk=job.pk).update(status=PrintJob.Status.FAILED)

        printing.retry(PrintJob.objects.get(pk=job.pk), actor=None)

        self.assertEqual(printing.claim_next(self.agent).pk, job.pk)


class IsolationTest(PrintingBase):
    def setUp(self):
        super().setUp()
        self.second_branch = Branch.objects.create(company=self.company, name='Sucursal Sur')
        self.second_agent, self.second_token = printing.create_agent(
            company=self.company, branch=self.second_branch, name='Sur', actor=None)
        self.other = _p3_company('otra-impresa', 'Otra Tienda')
        self.other_branch = self.other.default_inventory_branch
        Printer.objects.create(
            company=self.other, branch=self.other_branch, name='Caja', host='192.168.1.50')
        self.other_agent, self.other_token = printing.create_agent(
            company=self.other, branch=self.other_branch, name='Ajeno', actor=None)

    def test_an_agent_of_another_branch_receives_nothing(self):
        self.issue()

        self.assertIsNone(printing.claim_next(self.second_agent))
        self.assertIsNotNone(printing.claim_next(self.agent))

    def test_an_agent_of_another_company_receives_nothing(self):
        self.issue()

        self.assertIsNone(printing.claim_next(self.other_agent))

    def test_an_agent_cannot_close_a_job_of_another_branch(self):
        self.issue()
        job = printing.claim_next(self.agent)

        for stranger in (self.second_agent, self.other_agent):
            with self.assertRaises(printing.PrintNotFound):
                printing.complete(stranger, job.pk, job.claim_token, ok=True)
        self.assertEqual(PrintJob.objects.get(pk=job.pk).status, PrintJob.Status.PRINTING)

    def test_the_job_goes_to_the_printer_of_the_branch_that_sold(self):
        Printer.objects.create(
            company=self.company, branch=self.second_branch, name='Caja Sur', host='10.0.0.9')

        self.issue()

        self.assertEqual(PrintJob.objects.get().printer, self.printer)

    def test_a_manual_reprint_cannot_use_a_printer_of_another_branch(self):
        document = self.issue()
        elsewhere = Printer.objects.create(
            company=self.company, branch=self.second_branch, name='Caja Sur', host='10.0.0.9')

        with self.assertRaises(printing.PrintError):
            printing.enqueue_manual(
                order=document.order, printer=elsewhere, key='reimpresion-1', actor=None)


class ReceiptFormatTest(PrintingBase):
    def test_accents_survive_the_code_page_and_odd_characters_do_not_break_it(self):
        receipt = Receipt(encoding='cp858')
        receipt.line('Representación impresa — «ñandú» · €')
        data = receipt.to_bytes()

        self.assertIn('Representación impresa - "ñandú" - €'.encode('cp858'), data)

    def test_a_long_line_is_wrapped_to_the_paper_width(self):
        receipt = Receipt(paper_width_mm=80)
        receipt.line('X' * 100)

        lines = receipt.to_bytes().split(b'\n')
        self.assertTrue(all(len(line.lstrip(b'\x1b@t\x13a\x00E\x1d!')) <= 48 for line in lines[1:-1]))

    def test_the_fiscal_ticket_says_what_the_pdf_says(self):
        document = self.issue(document_type='ce', document_number='001234567')
        job = printing.claim_next(self.agent)
        # Sin las órdenes de estilo y con los renglones unidos: lo que se lee.
        text = ' '.join(re.sub(
            rb'\x1b[atE].|\x1d!.|\x1b@', b'', printing.render(job),
        ).decode('cp858', 'replace').split())

        for expected in ('TIENDA IMPRESA SAC', f'RUC {document.issuer_tax_id}',
                         'BOLETA DE VENTA ELECTRÓNICA', 'C.E.:', '001234567',
                         'NIU', 'S/ 118.00', 'TOTAL', 'SON: CIENTO DIECIOCHO',
                         'Representación impresa de la boleta de venta electrónica',
                         document.digest_value):
            self.assertIn(expected, text)
        self.assertNotIn('RUC:', text)

    def test_the_logo_is_sent_once_as_a_plain_bitmap(self):
        import io

        from PIL import Image

        receipt = Receipt()
        image = Image.new('RGBA', (64, 16), (0, 0, 0, 255))
        out = io.BytesIO()
        image.save(out, 'PNG')
        data = receipt.image(out.getvalue()).to_bytes()

        self.assertEqual(data.count(b'\x1dv0'), 1)
        header = data.index(b'\x1dv0')
        self.assertEqual(data[header + 4:header + 8], bytes([8, 0, 16, 0]))
        self.assertEqual(set(data[header + 8:header + 8 + 8 * 16]), {0xFF})


class ReviewHardeningTest(PrintingBase):
    """Lo que encontró la revisión del código de la cola."""

    def test_control_characters_in_a_text_never_reach_the_printer_as_commands(self):
        # El nombre, la dirección y las notas los escribe el comprador. Un ESC
        # ahí dentro sería una orden para la impresora: abrir el cajón, cortar.
        receipt = Receipt()
        receipt.line('Rosa\x1bp\x00\x19\xfa Quispe\x1dV\x00 \x10\x14\x01')
        data = receipt.to_bytes()

        body = data[data.index(b'Rosa'):]
        for forbidden in (b'\x1b', b'\x1d', b'\x10', b'\x00'):
            self.assertNotIn(forbidden, body.rstrip(b'\n'))
        self.assertIn(b'Quispe', body)

    def test_a_failed_delivery_waits_before_the_next_attempt(self):
        self.issue()
        job = printing.claim_next(self.agent)
        printing.complete(self.agent, job.pk, job.claim_token, ok=False, error='apagada')

        # Con la impresora apagada, reintentar en el acto quemaría los cinco
        # intentos en un segundo y dejaría fallidos todos los tickets en cola.
        self.assertIsNone(printing.claim_next(self.agent))

        PrintJob.objects.filter(pk=job.pk).update(available_at=timezone.now())
        self.assertEqual(printing.claim_next(self.agent).pk, job.pk)

    def test_a_job_whose_order_is_no_longer_paid_is_cancelled_not_printed(self):
        document = self.issue()
        document.order.status = 'cancelled'
        document.order.paid = False
        document.order.save(update_fields=['status', 'paid'])

        self.assertIsNone(printing.claim_next(self.agent))
        self.assertEqual(PrintJob.objects.get().status, PrintJob.Status.CANCELLED)

    def test_an_old_backlog_is_not_printed_when_the_agent_comes_back(self):
        self.issue()
        PrintJob.objects.update(
            created_at=timezone.now() - timedelta(hours=printing.MAX_AGE_HOURS + 1))

        self.assertIsNone(printing.claim_next(self.agent))
        job = PrintJob.objects.get()
        self.assertEqual(job.status, PrintJob.Status.FAILED)
        self.assertIn('Caducó', job.last_error)

    def test_deactivating_a_printer_fails_what_was_waiting_for_it(self):
        self.issue()

        printing.deactivate_printer(self.printer)

        job = PrintJob.objects.get()
        self.assertEqual(job.status, PrintJob.Status.FAILED)
        self.assertFalse(Printer.objects.get(pk=self.printer.pk).is_active)

    def test_a_retry_moves_to_the_printer_the_branch_has_now(self):
        self.issue()
        printing.deactivate_printer(self.printer)
        replacement = Printer.objects.create(
            company=self.company, branch=self.branch, name='Caja nueva', host='192.168.1.60')

        job = printing.retry(PrintJob.objects.get(), actor=None)

        self.assertEqual((job.status, job.printer), (PrintJob.Status.PENDING, replacement))

    def test_the_same_reprint_key_for_another_order_is_a_conflict(self):
        first = self.issue()
        second = self.issue()
        printing.enqueue_manual(order=first.order, printer=self.printer, key='clave-1', actor=None)

        with self.assertRaises(printing.PrintConflict):
            printing.enqueue_manual(
                order=second.order, printer=self.printer, key='clave-1', actor=None)

    def test_the_branch_knows_whether_an_agent_is_listening(self):
        self.assertFalse(printing.agent_online(self.branch))

        printing.claim_next(self.agent)
        self.assertTrue(printing.agent_online(self.branch))

        PrintAgent.objects.update(last_seen_at=timezone.now() - timedelta(minutes=10))
        self.assertFalse(printing.agent_online(self.branch))

    def test_each_job_has_an_identifier_that_does_not_repeat_across_databases(self):
        self.issue()
        job = PrintJob.objects.get()

        self.assertEqual(len(job.uid.hex), 32)
