"""
La térmica no recibe nunca un PDF ni PostScript — PRINT-THERMAL-01.

CAUSA. Un PDF de 80 mm se imprimió desde el visor de macOS en una cola de CUPS
con el controlador «Generic PostScript Printer». CUPS lo convirtió en
PostScript y lo mandó tal cual a una impresora ESC/POS, que imprimió el código
fuente (`%!PS-Adobe-3.0 …`) como texto y gastó el rollo.

LO QUE SE FIJA AQUÍ, en las dos puntas de la cola:

  · el servidor no entrega, y el agente no envía, unos bytes que empiezan como
    un PDF o un PostScript, ni algo que no sea un ticket ESC/POS de un tipo de
    documento conocido; el trabajo queda fallido con el motivo escrito;
  · nada de esto abre una conexión ni llama a `lp`: todo está simulado.
"""
import base64
import os
import re
import tempfile

from django.test import SimpleTestCase

from print_agent import agent as agent_module
from print_agent.agent import Agent
from store.models import PrintJob
from store.printing import escpos
from store.printing import services as printing
from store.test_printing import PrintingBase
from store.test_printing_api import CLAIM, PrintApiBase

TOKEN = 'bdpa_token-de-prueba-que-no-abre-nada'
TICKET = b'\x1b@\x1bt\x13TICKET\n\x1dVB\x00'

#: Lo que la térmica de la tienda imprimió como texto, y sus parientes.
NOT_ESCPOS = {
    'pdf': b'%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj',
    'postscript': b'%!PS-Adobe-3.0\n%%Creator: cgpdftops',
    'postscript_bare': b'%!\n/Helvetica findfont',
    'pdf_after_bom_and_blanks': b'\xef\xbb\xbf \r\n\t%PDF-1.7',
    'postscript_after_ctrl_d': b'\x04%!PS-Adobe-3.0',
    'postscript_inside_pjl': (
        b'\x1b%-12345X@PJL JOB\r\n@PJL ENTER LANGUAGE=POSTSCRIPT\r\n%!PS-Adobe-3.0\n'),
    'plain_text': b'Hola, esto no es un ticket\n',
    'empty': b'',
}


class ServerPayloadGuardTest(PrintingBase):
    """El servidor: lo que no es un ticket ESC/POS no sale de la cola."""

    def test_a_pdf_or_postscript_is_never_an_escpos_ticket(self):
        for name, payload in NOT_ESCPOS.items():
            with self.subTest(name):
                self.assertTrue(escpos.payload_problem(payload))
        self.assertEqual(escpos.payload_problem(TICKET), '')

    def test_render_refuses_bytes_that_are_not_an_escpos_ticket(self):
        self.issue()
        job = printing.claim_next(self.agent)

        for name, payload in NOT_ESCPOS.items():
            with self.subTest(name), self.patch_ticket(payload):
                with self.assertRaises(printing.PrintPayloadRejected):
                    printing.render(job)

    def test_render_refuses_a_document_type_it_does_not_know(self):
        self.issue()
        job = printing.claim_next(self.agent)
        job.kind = 'pdf_a4'

        with self.assertRaises(printing.PrintPayloadRejected):
            printing.render(job)

    def patch_ticket(self, payload):
        from unittest import mock

        return mock.patch.object(escpos, 'fiscal_ticket', return_value=payload)


class ClaimRefusesNonEscposTest(PrintApiBase):
    """La puerta del agente: un trabajo así se cierra como fallido, con su motivo."""

    def test_a_pdf_payload_is_failed_with_its_reason_and_never_delivered(self):
        from unittest import mock

        self.issue()
        with mock.patch.object(escpos, 'fiscal_ticket', return_value=NOT_ESCPOS['pdf']):
            res = self.as_agent().post(CLAIM)

        self.assertEqual(res.status_code, 200)
        self.assertIsNone(res.data['job'])
        job = PrintJob.objects.get()
        self.assertEqual(job.status, PrintJob.Status.FAILED)
        self.assertIn('PDF', job.last_error)
        self.assertEqual(job.claim_token, '')

    def test_the_next_job_is_still_delivered(self):
        from unittest import mock

        self.issue()
        self.issue()
        real = escpos.fiscal_ticket
        calls = []

        def first_is_postscript(document, **options):
            calls.append(document.pk)
            if len(calls) == 1:
                return NOT_ESCPOS['postscript']
            return real(document, **options)

        with mock.patch.object(escpos, 'fiscal_ticket', side_effect=first_is_postscript):
            job = self.as_agent().post(CLAIM).data['job']

        self.assertIn(b'B001-2', base64.b64decode(job['payload_base64']))
        refused = PrintJob.objects.exclude(pk=job['id']).get()
        self.assertEqual(refused.status, PrintJob.Status.FAILED)
        self.assertIn('PostScript', refused.last_error)


class FakeServer:
    def __init__(self):
        self.queue, self.results = [], []
        #: Cuántas confirmaciones se pierden por el camino (la red se cae).
        self.fail_results = 0

    def job(self, job_id, payload, *, kind='sales_note_ticket', name='Caja',
            host='192.168.1.50', port=9100):
        self.queue.append({
            'id': job_id, 'uid': f'{job_id:032x}', 'claim_token': f'entrega-{job_id}',
            'kind': kind, 'attempt': 1, 'lease_seconds': 90,
            'printer': {'name': name, 'host': host, 'port': port},
            'payload_base64': base64.b64encode(payload).decode(),
        })

    def http(self, method, url, token, body=None, timeout=20.0):
        if url.endswith('/jobs/claim/'):
            return 200, {'job': self.queue.pop(0) if self.queue else None}
        if self.fail_results:
            self.fail_results -= 1
            raise OSError('connection reset')
        self.results.append((url, body))
        return 200, {'status': 'printed' if body['ok'] else 'pending'}


class AgentTestBase(SimpleTestCase):
    def setUp(self):
        self.server = FakeServer()
        self.sent = []
        directory = tempfile.mkdtemp(prefix='print-thermal-')
        self.journal_path = os.path.join(directory, 'journal.json')

    def agent(self, *, queue_model=None, cups_printer=None, **extra):
        """Un agente con todo lo que toca fuera simulado: red, `lp` y `lpoptions`."""
        config = {'server': 'https://tienda.invalid', 'token': TOKEN,
                  'journal': self.journal_path, **extra}

        def no_cups(*args):
            raise AssertionError('esta prueba no debía llegar a CUPS')

        return Agent(config, http=self.server.http,
                     printer=lambda *args: self.sent.append(('tcp', *args)),
                     cups_printer=cups_printer or no_cups,
                     queue_model=queue_model or no_cups)


class AgentPayloadGuardTest(AgentTestBase):
    """El agente: tampoco envía lo que no es un ticket, diga lo que diga el servidor."""

    def test_it_refuses_to_send_a_pdf_or_postscript_and_says_why(self):
        agent = self.agent()
        for index, (name, payload) in enumerate(NOT_ESCPOS.items(), start=1):
            with self.subTest(name):
                self.server.job(index, payload)
                with self.assertLogs('print_agent', level='ERROR'):
                    self.assertEqual(agent.run_once(), 'refused')
                _url, body = self.server.results[-1]
                self.assertFalse(body['ok'])
                self.assertTrue(body['error'])
        self.assertEqual(self.sent, [])

    def test_it_refuses_a_document_type_it_does_not_know(self):
        self.server.job(1, TICKET, kind='pdf_a4')

        with self.assertLogs('print_agent', level='ERROR'):
            self.assertEqual(self.agent().run_once(), 'refused')
        self.assertEqual(self.sent, [])

    def test_an_escpos_ticket_of_a_known_type_is_sent_untouched(self):
        for index, kind in enumerate(('fiscal_ticket', 'sales_note_ticket', 'test_ticket'), 1):
            self.server.job(index, TICKET, kind=kind)
            self.assertEqual(self.agent().run_once(), 'printed')
        self.assertEqual([call[3] for call in self.sent], [TICKET] * 3)

    def test_the_agent_and_the_server_agree_on_what_is_not_a_ticket(self):
        for name, payload in {**NOT_ESCPOS, 'ticket': TICKET}.items():
            with self.subTest(name):
                self.assertEqual(
                    bool(agent_module.payload_problem(payload)),
                    bool(escpos.payload_problem(payload)))

    def test_a_refusal_waits_like_a_failure(self):
        self.assertGreater(self.agent().pause_after('refused'), 0)


# ---------------------------------------------------------------------------
# Una térmica USB en un Mac: la cola RAW de CUPS
# ---------------------------------------------------------------------------

RAW = {'transport': 'cups_raw', 'queue': 'POS80_RAW'}


def completed(args, returncode=0, stdout=b'', stderr=b''):
    import subprocess

    return subprocess.CompletedProcess(args, returncode, stdout=stdout, stderr=stderr)


class AgentTransportConfigTest(AgentTestBase):
    def test_a_config_without_transport_keeps_printing_by_network(self):
        config = agent_module.validate_config({'server': 'https://t.invalid', 'token': TOKEN})
        self.assertEqual(config['printers'], {})

        config = agent_module.validate_config({
            'server': 'https://t.invalid', 'token': TOKEN, 'printers': {'Caja': {}}})
        self.assertEqual(config['printers'], {'Caja': {'transport': 'tcp'}})

    def test_cups_raw_needs_a_queue_with_a_plain_cups_name(self):
        for queue in ('', '-oraw', 'a b', 'POS/80', 'POS#80', 'x;rm -rf', 'cola\n', 'é'):
            with self.subTest(queue=queue):
                with self.assertRaises(agent_module.AgentError):
                    agent_module.validate_config({
                        'server': 'https://t.invalid', 'token': TOKEN,
                        'printers': {'Caja': {'transport': 'cups_raw', 'queue': queue}}})
        config = agent_module.validate_config({
            'server': 'https://t.invalid', 'token': TOKEN, 'printers': {'Caja': RAW}})
        self.assertEqual(config['printers']['Caja'], RAW)

    def test_an_unknown_transport_is_refused(self):
        with self.assertRaises(agent_module.AgentError):
            agent_module.validate_config({
                'server': 'https://t.invalid', 'token': TOKEN,
                'printers': {'Caja': {'transport': 'usb'}}})

    def test_it_refuses_to_start_on_a_queue_with_a_postscript_or_pdf_driver(self):
        for model in ('Generic PostScript Printer', 'Generic PDF Printer', 'HP postscript'):
            with self.subTest(model):
                with self.assertRaises(agent_module.AgentError) as caught:
                    self.agent(printers={'Caja': RAW}, queue_model=lambda queue, m=model: m)
                message = str(caught.exception)
                self.assertIn('POS80_RAW', message)
                self.assertIn('-m raw', message)

    def test_it_refuses_to_start_on_a_queue_that_does_not_exist(self):
        def missing(queue):
            raise OSError('lpoptions: Unknown destination')

        with self.assertRaises(agent_module.AgentError):
            self.agent(printers={'Caja': RAW}, queue_model=missing)

    def test_a_raw_queue_starts(self):
        seen = []
        self.agent(printers={'Caja': RAW},
                   queue_model=lambda queue: seen.append(queue) or 'Local Raw Printer')
        self.assertEqual(seen, ['POS80_RAW'])

    def test_a_network_only_config_never_asks_cups(self):
        def never(queue):
            raise AssertionError('no debía preguntar a CUPS')

        self.agent(queue_model=never)


class AgentCupsRawTest(AgentTestBase):
    def raw_agent(self):
        return self.agent(
            printers={'Caja': RAW}, queue_model=lambda queue: 'Local Raw Printer',
            cups_printer=lambda *args: self.sent.append(('cups', *args)))

    def test_a_ticket_for_a_raw_printer_goes_to_its_cups_queue(self):
        self.server.job(1, TICKET, name='Caja', host='192.168.1.50')

        self.assertEqual(self.raw_agent().run_once(), 'printed')

        self.assertEqual(len(self.sent), 1)
        transport, queue, payload, _timeout, wait = self.sent[0]
        self.assertEqual((transport, queue, payload, wait), ('cups', 'POS80_RAW', TICKET, 30.0))
        self.assertTrue(self.server.results[-1][1]['ok'])

    def test_a_printer_not_listed_still_prints_by_network(self):
        self.server.job(1, TICKET, name='Barra', host='192.168.1.60', port=9100)

        self.assertEqual(self.raw_agent().run_once(), 'printed')

        self.assertEqual(self.sent[0][:3], ('tcp', '192.168.1.60', 9100))

    def test_a_pdf_never_reaches_the_cups_queue_either(self):
        self.server.job(1, NOT_ESCPOS['pdf'], name='Caja')

        with self.assertLogs('print_agent', level='ERROR'):
            self.assertEqual(self.raw_agent().run_once(), 'refused')
        self.assertEqual(self.sent, [])

    def test_a_cups_failure_is_reported_and_nothing_is_recorded(self):
        def broken(*args):
            raise OSError('lp: The printer or class does not exist.')

        agent = self.agent(printers={'Caja': RAW}, queue_model=lambda q: 'Local Raw Printer',
                           cups_printer=broken)
        self.server.job(1, TICKET, name='Caja')

        self.assertEqual(agent.run_once(), 'failed')
        self.assertFalse(self.server.results[-1][1]['ok'])
        self.assertEqual(agent_module.Journal(self.journal_path).printed, [])


class CupsCommandsTest(SimpleTestCase):
    """Las dos órdenes de CUPS, con `subprocess` simulado: aquí no se ejecuta nada."""

    # `lp` y la espera a que el trabajo salga: CupsDeliveryTest, más abajo.

    def test_lp_is_never_called_with_a_bad_queue_name(self):
        from unittest import mock

        with mock.patch.object(agent_module.subprocess, 'run') as run:
            with self.assertRaises(OSError):
                agent_module.send_to_cups_queue('-oraw', TICKET, 5.0)
        run.assert_not_called()

    def test_the_queue_driver_is_read_with_lpoptions(self):
        from unittest import mock

        out = (b"copies=1 device-uri=usb://STMicroelectronics/USB%20POS%20Printer "
               b"printer-make-and-model='Generic PostScript Printer' printer-is-shared=false")
        with mock.patch.object(agent_module.subprocess, 'run',
                               return_value=completed([], 0, out)) as run:
            model = agent_module.read_queue_model('STMicroelectronics_USB_POS_Printer')

        self.assertEqual(model, 'Generic PostScript Printer')
        args, kwargs = run.call_args
        self.assertEqual(args[0], ['lpoptions', '-p', 'STMicroelectronics_USB_POS_Printer'])
        self.assertIn('timeout', kwargs)
        self.assertNotIn('shell', kwargs)

    def test_a_queue_that_does_not_exist_is_an_oserror(self):
        from unittest import mock

        with mock.patch.object(agent_module.subprocess, 'run',
                               return_value=completed([], 1, stderr=b'Unknown destination')):
            with self.assertRaises(OSError):
                agent_module.read_queue_model('POS80_RAW')


# ---------------------------------------------------------------------------
# La prueba de impresión: unas líneas antes del ticket entero
# ---------------------------------------------------------------------------

PRINTED_AT = __import__('datetime').datetime(2026, 10, 14, 9, 5)
RULER_80 = b'123456789012345678901234567890123456789012345678'


def readable(payload: bytes, encoding='cp858') -> str:
    """Lo que se lee en el papel: sin órdenes de estilo, renglones unidos."""
    import re

    text = re.sub(rb'\x1b[atEd].|\x1d!.|\x1b@|\x1dVB.', b'', payload)
    return ' '.join(text.decode(encoding, 'replace').split())


class TestTicketContentTest(SimpleTestCase):
    def ticket(self, **options):
        return escpos.test_print_ticket(
            company_name='Black Dog Store', printer_name='Caja', printed_at=PRINTED_AT, **options)

    def test_it_is_a_short_escpos_ticket_with_what_to_check(self):
        payload = self.ticket()

        self.assertEqual(escpos.payload_problem(payload), '')
        self.assertLessEqual(payload.count(b'\n'), 10)
        text = readable(payload)
        for expected in ('Black Dog Store', 'PRUEBA DE IMPRESIÓN', '14/10/2026 09:05', 'Caja',
                         'áéíóú ñ Ñ ¿¡ S/',
                         'Si ve texto técnico (%!PS, %PDF) apague la impresora y cancele la cola'):
            self.assertIn(expected, text)

    def test_the_ruler_fills_exactly_one_line_of_the_roll(self):
        # Tras las órdenes de estilo (letra normal), la regla entera y el salto.
        self.assertIn(b'\x1d!\x00' + RULER_80 + b'\n', self.ticket(paper_width_mm=80))
        narrow = self.ticket(paper_width_mm=58)
        self.assertIn(b'\x1d!\x00' + RULER_80[:32] + b'\n', narrow)
        self.assertNotIn(RULER_80[:33], narrow)

    def test_spanish_characters_use_the_printer_code_page(self):
        payload = self.ticket(encoding='cp858')

        self.assertTrue(payload.startswith(b'\x1b@\x1bt\x13'))     # ESC t 19 = cp858
        self.assertIn(bytes([0xA0, 0x82, 0xA1, 0xA2, 0xA3]), payload)   # áéíóú
        self.assertIn(b'\xa4 \xa5 \xa8\xad S/', payload)               # ñ Ñ ¿¡ S/

    def test_it_feeds_and_cuts_once_at_the_end(self):
        payload = self.ticket()

        self.assertEqual(payload.count(b'\x1dVB\x00'), 1)
        self.assertTrue(payload.endswith(b'\x1bd\x03\x1dVB\x00'))


class TestPrintQueueTest(PrintingBase):
    def test_a_test_ticket_is_queued_without_an_order_and_delivered(self):
        job = printing.enqueue_test(printer=self.printer, key='prueba-1', actor=None)

        self.assertEqual((job.kind, job.order_id, job.reason),
                         (PrintJob.Kind.TEST_TICKET, None, PrintJob.Reason.MANUAL))
        self.assertEqual(printing.enqueue_test(printer=self.printer, key='prueba-1', actor=None), job)
        claimed = printing.claim_next(self.agent)
        self.assertEqual(claimed, job)
        payload = printing.render(claimed)
        self.assertIn(b'PRUEBA DE IMPRESI', payload)
        self.assertIn(self.company.name.encode('cp858'), payload)

    def test_an_inactive_printer_gets_no_test(self):
        self.printer.is_active = False
        self.printer.save()

        with self.assertRaises(printing.PrintError):
            printing.enqueue_test(printer=self.printer, key='prueba-2', actor=None)

    def test_a_failed_test_can_be_sent_again(self):
        job = printing.enqueue_test(printer=self.printer, key='prueba-3', actor=None)
        PrintJob.objects.filter(pk=job.pk).update(status=PrintJob.Status.FAILED)
        job.refresh_from_db()

        self.assertEqual(printing.retry(job, actor=None).status, PrintJob.Status.PENDING)

    def test_only_a_test_ticket_may_have_no_order(self):
        from django.db import IntegrityError, transaction

        with self.assertRaises(IntegrityError), transaction.atomic():
            PrintJob.objects.create(
                company=self.company, branch=self.branch, printer=self.printer,
                kind=PrintJob.Kind.SALES_NOTE_TICKET, idempotency_key='sin-pedido')


class TestPrintApiTest(PrintApiBase):
    def url(self, printer=None):
        return f'/api/admin/printing/printers/{(printer or self.printer).pk}/test/' + self.q()

    def test_a_manager_queues_a_test_print_once_per_key(self):
        from store.models import AdminAuditLog

        client = self.as_user(self.manager)
        first = client.post(self.url(), {}, format='json', HTTP_IDEMPOTENCY_KEY='k-1')
        again = client.post(self.url(), {}, format='json', HTTP_IDEMPOTENCY_KEY='k-1')

        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual((first.data['kind'], first.data['order']), ('test_ticket', None))
        self.assertEqual((again.status_code, again.data['id']), (200, first.data['id']))
        self.assertEqual(AdminAuditLog.objects.filter(action='print_test_requested').count(), 1)

        job = self.as_agent().post(CLAIM).data['job']
        self.assertEqual(job['kind'], 'test_ticket')
        self.assertEqual(escpos.payload_problem(base64.b64decode(job['payload_base64'])), '')

    def test_it_needs_a_key_and_the_right_to_manage_printers(self):
        self.assertEqual(self.as_user(self.manager).post(self.url(), {}, format='json').status_code, 400)
        self.assertEqual(self.as_user(self.seller).post(
            self.url(), {}, format='json', HTTP_IDEMPOTENCY_KEY='k-2').status_code, 403)
        self.assertFalse(PrintJob.objects.exists())

    def test_a_printer_of_another_company_does_not_exist_here(self):
        from store.models import Printer
        from store.tests import _p3_company

        other = _p3_company('otra-termica', 'Otra')
        foreign = Printer.objects.create(
            company=other, branch=other.default_inventory_branch, name='Ajena', host='192.168.9.9')

        res = self.as_user(self.manager).post(
            self.url(foreign), {}, format='json', HTTP_IDEMPOTENCY_KEY='k-3')

        self.assertEqual(res.status_code, 404)

    def test_the_jobs_list_shows_a_test_without_an_order(self):
        printing.enqueue_test(printer=self.printer, key='k-4', actor=None)

        row = self.as_user(self.manager).get('/api/admin/printing/jobs/' + self.q()).data['results'][0]

        self.assertEqual((row['kind'], row['order']), ('test_ticket', None))


class TestPagePdfTest(PrintApiBase):
    URL = '/api/admin/printing/test-page/'

    def test_the_test_page_is_a_short_80_mm_pdf_with_the_same_lines(self):
        from store.test_document_design import TICKET_WIDTH, media_box, text_of

        res = self.as_user(self.manager).get(self.URL + self.q())

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res['Content-Type'], 'application/pdf')
        self.assertEqual(res['Content-Disposition'],
                         'attachment; filename="prueba-impresion-ticket80.pdf"')
        pdf = res.content
        self.assertTrue(pdf.startswith(b'%PDF'))
        width, height = media_box(pdf)
        self.assertAlmostEqual(width, TICKET_WIDTH, places=1)
        self.assertLess(height, 80 * 72 / 25.4)          # menos de 8 cm de papel
        self.assertEqual(pdf.count(b'/Type /Page\n') + pdf.count(b'/Type /Page '), 1)
        text = text_of(pdf)
        for expected in ('PRUEBA DE IMPRESIÓN', 'áéíóú', 'S/', '%!PS', 'cancele la cola'):
            self.assertIn(expected, text)

    def test_a_seller_can_download_it_and_an_outsider_cannot(self):
        from rest_framework.test import APIClient

        self.assertEqual(self.as_user(self.seller).get(self.URL + self.q()).status_code, 200)
        self.assertIn(APIClient().get(self.URL + self.q()).status_code, (401, 403))


# ---------------------------------------------------------------------------
# Lo que sale hoy por la térmica y en el PDF de 80 mm, fijado
# ---------------------------------------------------------------------------

#: Las órdenes de estilo que `Receipt` pone delante de un renglón.
_STYLE = __import__('re').compile(rb'\x1b@|\x1bt.|\x1ba.|\x1bE.|\x1d!.|\x1bd.|\x1dVB.')


def printed_lines(payload: bytes) -> list[bytes]:
    """Los renglones de texto, sin órdenes de estilo, QR ni imagen."""
    return [_STYLE.sub(b'', line) for line in payload.split(b'\n')
            if b'\x1d(k' not in line and b'\x1dv0' not in line]


class SalesNoteEscposPinTest(PrintingBase):
    """El ticket ESC/POS de la nota de venta: ancho, códigos, avance y corte."""

    def note(self, **issue):
        from store.sales_note_services import get_or_create_sales_note

        document = self.issue(**issue)
        return get_or_create_sales_note(document.order, actor=None)[0]

    def test_it_fits_the_roll_starts_clean_and_ends_with_feed_and_one_cut(self):
        note = self.note(name='Ñahui Peña Ávila')
        for width, columns in ((80, 48), (58, 32)):
            with self.subTest(width=width):
                payload = escpos.sales_note_ticket(note, paper_width_mm=width)

                self.assertTrue(payload.startswith(b'\x1b@\x1bt\x13'))   # init + cp858
                self.assertEqual(escpos.payload_problem(payload), '')
                self.assertNotIn(b'%PDF-', payload)
                self.assertNotIn(b'%!PS', payload)
                self.assertEqual(payload.count(b'\x1dVB\x00'), 1)
                self.assertTrue(payload.endswith(b'\x1bd\x03\x1dVB\x00'))
                too_wide = [line for line in printed_lines(payload) if len(line) > columns]
                self.assertEqual(too_wide, [])

    def test_spanish_text_and_the_sol_sign_use_the_code_page(self):
        payload = escpos.sales_note_ticket(self.note(name='Ñahui Peña Ávila'))

        self.assertIn('Ñahui Peña Ávila'.encode('cp858'), payload)
        self.assertIn(b'S/ 118.00', payload)

    def test_a_code_page_without_a_letter_drops_the_accent_not_the_letter(self):
        receipt = escpos.Receipt(encoding='cp437')
        receipt.line('Ávila Ñandú ¿¡')

        self.assertTrue(receipt.to_bytes().startswith(b'\x1b@\x1bt\x00'))
        self.assertIn(b'Avila \xa5and\xa3 \xa8\xad', receipt.to_bytes())
        self.assertTrue(escpos.Receipt(encoding='utf-8').to_bytes().startswith(b'\x1b@\x1bt\x13'))


class Ticket80PdfPinTest(PrintingBase):
    """El PDF de 80 mm de la nota: ancho, márgenes, una página y altura del contenido."""

    def note(self, notes=''):
        from store.models import Order
        from store.sales_note_services import get_or_create_sales_note

        document = self.issue()
        if notes:
            Order.objects.filter(pk=document.order_id).update(notes=notes)
        return get_or_create_sales_note(document.order, actor=None)[0]

    def measure(self, pdf: bytes):
        import re

        from store.test_document_design import media_box

        width, height = media_box(pdf)
        origins = [(float(x), float(y)) for x, y in re.findall(
            rb'1 0 0 1 ([\d.]+) ([\d.]+) Tm', self.content(pdf))]
        return width, height, origins

    @staticmethod
    def content(pdf: bytes) -> bytes:
        import base64 as b64
        import re
        import zlib

        out = b''
        for body in re.findall(rb'[^d]stream\r?\n(.*?)endstream', pdf, re.S):
            body = body.strip(b'\r\n')
            if body.endswith(b'~>'):
                body = b64.a85decode(body[:-2])
            try:
                body = zlib.decompress(body)
            except zlib.error:
                pass
            out += body
        return out

    def test_80_mm_wide_one_page_inside_the_margins_and_as_tall_as_its_content(self):
        from store.test_document_design import TICKET_WIDTH
        from store.ticket_services import (
            TICKET_MARGIN_MM, TICKET_WIDTH_MM, generate_sales_note_ticket_pdf,
        )

        self.assertEqual((TICKET_WIDTH_MM, TICKET_MARGIN_MM), (80, 4))
        margin = 4 * 72 / 25.4
        short = generate_sales_note_ticket_pdf(self.note())
        long = generate_sales_note_ticket_pdf(self.note(notes='Entregar en recepción. ' * 40))

        heights = []
        for pdf in (short, long):
            width, height, origins = self.measure(pdf)
            self.assertAlmostEqual(width, TICKET_WIDTH, places=1)     # 226.77 pt
            self.assertEqual(len(re.findall(rb'/Type /Page[^s]', pdf)), 1)
            self.assertTrue(origins)
            self.assertGreaterEqual(min(x for x, _y in origins), margin - 0.5)
            self.assertGreaterEqual(min(y for _x, y in origins), margin - 0.5)
            self.assertLessEqual(max(y for _x, y in origins), height - margin)
            heights.append(height)
        short_height, long_height = heights
        # Unos 10–20 cm para una venta de una línea; la altura crece con el texto.
        self.assertGreater(short_height, 100 * 72 / 25.4, short_height)
        self.assertLess(short_height, 200 * 72 / 25.4, short_height)
        self.assertGreater(long_height, short_height + 50)
        self.assertLess(long_height, 400 * 72 / 25.4, long_height)


class DownloadHeadersPinTest(PrintApiBase):
    """A4 y 80 mm se descargan con nombres que no se confunden."""

    def test_sales_note_a4_and_ticket80_are_distinct_pdf_downloads(self):
        from store.sales_note_services import get_or_create_sales_note
        from store.tests import _p2d_member

        clerk, _ = _p2d_member(self.company, 'print_notas', ['company.view', 'sales.notes.manage'])
        document = self.issue()
        get_or_create_sales_note(document.order, actor=None)
        url = f'/api/admin/orders/{document.order_id}/sales-note/pdf/{self.q()}'

        a4 = self.as_user(clerk).get(url)
        roll = self.as_user(clerk).get(url + '&formato=ticket80')

        for res in (a4, roll):
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res['Content-Type'], 'application/pdf')
            self.assertRegex(res['Content-Disposition'], r'^attachment; filename="[A-Za-z0-9._-]+\.pdf"$')
        self.assertNotIn('ticket80', a4['Content-Disposition'])
        self.assertIn('-ticket80.pdf"', roll['Content-Disposition'])

    def test_fiscal_a4_and_ticket80_are_distinct_pdf_downloads(self):
        from store.tests import _p2d_member

        viewer, _ = _p2d_member(self.company, 'print_fiscal', ['company.view', 'sales.fiscal.view'])
        document = self.issue()
        url = f'/api/admin/fiscal-documents/{document.pk}/pdf/{self.q()}'

        a4 = self.as_user(viewer).get(url)
        roll = self.as_user(viewer).get(url + '&formato=ticket80')

        for res in (a4, roll):
            self.assertEqual(res.status_code, 200, getattr(res, 'data', None))
            self.assertEqual(res['Content-Type'], 'application/pdf')
        self.assertNotIn('ticket80', a4['Content-Disposition'])
        self.assertIn('-ticket80.pdf"', roll['Content-Disposition'])


# ---------------------------------------------------------------------------
# Revisión independiente (PRINT-THERMAL-01, segunda vuelta)
# ---------------------------------------------------------------------------

class PayloadProblemCasesTest(SimpleTestCase):
    """Cada envoltorio, en las dos copias de la regla, con su clase de motivo."""

    CASES = {
        b'%PDF-1.7': 'PDF',
        b'\xef\xbb\xbf%PDF-1.7': 'PDF',
        b'\xfe\xff%PDF-1.7': 'PDF',
        b' \r\n\t\x00%PDF-1.4': 'PDF',
        b'\x1b@ esto parece un ticket %PDF-1.4 pero lleva un PDF dentro': 'PDF',
        b'%!PS-Adobe-3.0': 'PostScript',
        b'%!': 'PostScript',
        b'\x04%!PS-Adobe-3.0': 'PostScript',
        b'\x0c\x04 %!PS': 'PostScript',
        b'\x1b%-12345X@PJL ENTER LANGUAGE=POSTSCRIPT\r\n%!PS-Adobe-3.0\n': 'PostScript',
        b'\x1b%-12345X@PJL ENTER LANGUAGE=PDF\r\n%PDF-1.7\n': 'PDF',
        b'texto plano': 'ESC @',
        b'\x1bt\x13 sin reinicio': 'ESC @',
        b'': 'ESC @',
    }

    def test_each_wrapper_is_named_for_what_it_is_on_both_sides(self):
        for payload, kind in self.CASES.items():
            with self.subTest(payload=payload):
                for rule in (escpos.payload_problem, agent_module.payload_problem):
                    self.assertIn(kind, rule(payload))

    def test_a_ticket_that_mentions_the_words_later_is_still_a_ticket(self):
        ticket = b'\x1b@\x1bt\x13' + b'x' * 200 + b'Si ve texto (%!PS, %PDF) pare.\n\x1dVB\x00'
        for rule in (escpos.payload_problem, agent_module.payload_problem):
            self.assertEqual(rule(ticket), '')


class FakeCups:
    """`lpstat` y `lp` simulados: lo que CUPS contestaría, sin ejecutar nada."""

    def __init__(self, *, enabled=True, accepting=True, lp_code=0, request='POS80_RAW-12',
                 pending_polls=0, lp_hangs=False):
        self.enabled, self.accepting = enabled, accepting
        self.lp_code, self.request, self.lp_hangs = lp_code, request, lp_hangs
        self.pending_polls = pending_polls
        self.calls = []

    def __call__(self, args, **kwargs):
        import subprocess

        self.calls.append((args, kwargs))
        if args[:2] == ['lpstat', '-p']:
            state = 'is idle.  enabled since Tue 14 Oct' if self.enabled else 'disabled since Tue 14 Oct -'
            return completed(args, 0, f'printer {args[2]} {state}\n'.encode())
        if args[:2] == ['lpstat', '-a']:
            state = 'accepting requests' if self.accepting else 'not accepting requests'
            return completed(args, 0, f'{args[2]} {state} since Tue 14 Oct\n'.encode())
        if args[0] == 'lp':
            if self.lp_hangs:
                raise subprocess.TimeoutExpired(args, kwargs.get('timeout'))
            if self.lp_code:
                return completed(args, self.lp_code, stderr=b'lp: Unknown destination')
            out = f'request id is {self.request} (0 file(s))\n'.encode() if self.request else b''
            return completed(args, 0, out)
        if args[:3] == ['lpstat', '-W', 'not-completed']:
            if self.pending_polls:
                self.pending_polls -= 1
                return completed(args, 0, f'{self.request}  caja  1024  Tue 14 Oct\n'.encode())
            return completed(args, 0, b'')
        raise AssertionError(f'orden inesperada: {args}')


class CupsDeliveryTest(SimpleTestCase):
    """`lp` sólo pone en cola: el agente comprueba antes y espera a que salga."""

    def send(self, cups, *, wait=5.0):
        from unittest import mock

        clock = iter(float(n) for n in range(1000))
        with mock.patch.object(agent_module.subprocess, 'run', side_effect=cups):
            return agent_module.send_to_cups_queue(
                'POS80_RAW', TICKET, 5.0, wait=wait,
                sleep=lambda seconds: None, clock=lambda: next(clock))

    def test_it_checks_the_queue_sends_raw_and_waits_until_the_job_leaves(self):
        cups = FakeCups(pending_polls=2)

        self.assertEqual(self.send(cups), 'POS80_RAW-12')

        commands = [args for args, _kwargs in cups.calls]
        self.assertEqual(commands[:3], [
            ['lpstat', '-p', 'POS80_RAW'], ['lpstat', '-a', 'POS80_RAW'],
            ['lp', '-d', 'POS80_RAW', '-o', 'raw']])
        self.assertEqual(commands[3:], [['lpstat', '-W', 'not-completed', '-o', 'POS80_RAW']] * 3)
        for _args, kwargs in cups.calls:
            self.assertNotIn('shell', kwargs)
            self.assertIn('timeout', kwargs)
            self.assertEqual((kwargs['env']['LC_ALL'], kwargs['env']['LANG']), ('C', 'C'))

    def test_a_disabled_or_rejecting_queue_is_not_sent_anything(self):
        for cups in (FakeCups(enabled=False), FakeCups(accepting=False)):
            with self.subTest(enabled=cups.enabled, accepting=cups.accepting):
                with self.assertRaises(OSError):
                    self.send(cups)
                self.assertNotIn('lp', [args[0] for args, _kwargs in cups.calls])

    def test_lp_refusing_the_job_is_a_plain_failure(self):
        with self.assertRaises(OSError):
            self.send(FakeCups(lp_code=1))

    def test_a_job_that_stays_in_the_queue_is_reported_as_not_printed(self):
        with self.assertRaises(agent_module.CupsNotPrinted) as caught:
            self.send(FakeCups(pending_polls=999), wait=3.0)
        self.assertIn('POS80_RAW-12', str(caught.exception))

    def test_lp_hanging_or_without_a_job_number_is_unknown_not_a_failure(self):
        for cups in (FakeCups(lp_hangs=True), FakeCups(request='')):
            with self.subTest(hangs=cups.lp_hangs):
                with self.assertRaises(agent_module.CupsNotPrinted):
                    self.send(cups)


class AgentReportsFinalOutcomesTest(AgentTestBase):
    def test_a_job_left_in_the_cups_queue_is_reported_and_held_in_the_journal(self):
        def stuck(*args):
            raise agent_module.CupsNotPrinted('CUPS aceptó POS80_RAW-12 y no lo imprimió')

        agent = self.agent(printers={'Caja': RAW}, queue_model=lambda q: 'Local Raw Printer',
                           cups_printer=stuck)
        self.server.job(1, TICKET, name='Caja')

        with self.assertLogs('print_agent', level='ERROR'):
            self.assertEqual(agent.run_once(), 'queued_not_printed')

        body = self.server.results[-1][1]
        self.assertEqual((body['ok'], body['outcome']), (False, 'queued_not_printed'))
        self.assertIn('POS80_RAW-12', body['error'])
        # Retenido: este uid no vuelve a CUPS (segunda revisión), y el servidor ya lo sabe.
        journal = agent_module.Journal(self.journal_path)
        self.assertEqual(journal.held_outcome(f'{1:032x}')[0], 'queued_not_printed')
        self.assertEqual(journal.unconfirmed, {})
        self.assertGreater(agent.pause_after('queued_not_printed'), 0)

    def test_a_refusal_tells_the_server_it_is_final(self):
        self.server.job(1, NOT_ESCPOS['pdf'])

        with self.assertLogs('print_agent', level='ERROR'):
            self.agent().run_once()

        self.assertEqual(self.server.results[-1][1]['outcome'], 'refused')


class ServerRecordsFinalOutcomesTest(PrintApiBase):
    def test_a_refused_or_unconfirmed_delivery_fails_at_once_without_requeue(self):
        from store.printing.services import MAX_ATTEMPTS

        for outcome in ('refused', 'queued_not_printed'):
            with self.subTest(outcome):
                PrintJob.objects.all().delete()
                self.issue()
                job = self.as_agent().post(CLAIM).data['job']

                res = self.as_agent().post(f'/api/v1/print-agent/jobs/{job["id"]}/result/', {
                    'claim_token': job['claim_token'], 'ok': False, 'outcome': outcome,
                    'error': 'motivo del agente'}, format='json')

                self.assertEqual(res.status_code, 200)
                stored = PrintJob.objects.get(pk=job['id'])
                self.assertLess(stored.attempts, MAX_ATTEMPTS)
                self.assertEqual(stored.status, PrintJob.Status.FAILED)
                self.assertIn('motivo del agente', stored.last_error)
                self.assertIsNone(self.as_agent().post(CLAIM).data['job'])

    def test_an_ordinary_failure_still_goes_back_to_the_queue(self):
        self.issue()
        job = self.as_agent().post(CLAIM).data['job']

        self.as_agent().post(f'/api/v1/print-agent/jobs/{job["id"]}/result/', {
            'claim_token': job['claim_token'], 'ok': False, 'error': 'Connection refused'},
            format='json')

        self.assertEqual(PrintJob.objects.get(pk=job['id']).status, PrintJob.Status.PENDING)


class TestJobsStayShortLivedTest(PrintingBase):
    def test_an_unclaimed_test_print_expires_after_a_few_minutes(self):
        from datetime import timedelta

        from django.utils import timezone

        job = printing.enqueue_test(printer=self.printer, key='vieja', actor=None)
        PrintJob.objects.filter(pk=job.pk).update(
            created_at=timezone.now() - timedelta(seconds=printing.TEST_MAX_AGE_SECONDS + 5))

        self.assertIsNone(printing.claim_next(self.agent))
        job.refresh_from_db()
        self.assertEqual(job.status, PrintJob.Status.FAILED)
        self.assertIn('prueba', job.last_error.lower())

    def test_a_fresh_test_print_is_delivered(self):
        job = printing.enqueue_test(printer=self.printer, key='nueva', actor=None)

        self.assertEqual(printing.claim_next(self.agent), job)


class TestRetryNeedsConfigureTest(PrintApiBase):
    def test_a_seller_cannot_resend_a_test_print_but_a_manager_can(self):
        job = printing.enqueue_test(printer=self.printer, key='k-r', actor=None)
        PrintJob.objects.filter(pk=job.pk).update(status=PrintJob.Status.FAILED)
        url = f'/api/admin/printing/jobs/{job.pk}/retry/{self.q()}'

        self.assertEqual(self.as_user(self.seller).post(url, {}, format='json').status_code, 403)
        self.assertEqual(PrintJob.objects.get(pk=job.pk).status, PrintJob.Status.FAILED)
        self.assertEqual(self.as_user(self.manager).post(url, {}, format='json').status_code, 200)

    def test_a_seller_still_resends_a_sale_ticket(self):
        self.issue()
        PrintJob.objects.update(status=PrintJob.Status.FAILED)
        job = PrintJob.objects.get()

        res = self.as_user(self.seller).post(
            f'/api/admin/printing/jobs/{job.pk}/retry/{self.q()}', {}, format='json')

        self.assertEqual(res.status_code, 200)


# ---------------------------------------------------------------------------
# Segunda revisión: lo que CUPS ya tiene no se manda otra vez
# ---------------------------------------------------------------------------

class HeldInCupsTest(AgentTestBase):
    """Un `queued_not_printed` queda en el diario: su uid no vuelve a CUPS."""

    def stuck_agent(self):
        def stuck(*args):
            self.sent.append(('cups', *args))
            raise agent_module.CupsNotPrinted('CUPS aceptó POS80_RAW-12 y sigue en la cola')

        return self.agent(printers={'Caja': RAW}, queue_model=lambda q: 'Local Raw Printer',
                          cups_printer=stuck)

    def test_a_lost_report_is_resent_and_the_job_never_goes_to_cups_twice(self):
        agent = self.stuck_agent()
        self.server.job(1, TICKET, name='Caja')
        self.server.fail_results = 1               # el aviso al servidor se pierde

        with self.assertLogs('print_agent', level='ERROR'):
            self.assertEqual(agent.run_once(), 'queued_not_printed')
        self.assertEqual(self.server.results, [])
        self.assertEqual(len(self.sent), 1)

        # Siguiente ciclo, tras un reinicio: el aviso pendiente sale primero, y
        # cuando el servidor vuelve a entregar el mismo trabajo (su plazo venció)
        # no va otra vez a CUPS.
        agent = self.stuck_agent()
        self.server.job(1, TICKET, name='Caja')
        self.server.queue[-1]['claim_token'] = 'segunda-entrega'
        agent.run_once()

        self.assertEqual(len(self.sent), 1)
        outcomes = [(body['ok'], body.get('outcome'), body['claim_token'])
                    for _url, body in self.server.results]
        self.assertEqual(outcomes, [
            (False, 'queued_not_printed', 'entrega-1'),
            (False, 'queued_not_printed', 'segunda-entrega')])
        self.assertTrue(all('POS80_RAW-12' in body['error'] for _url, body in self.server.results))
        self.assertEqual(agent_module.Journal(self.journal_path).unconfirmed, {})

    def test_a_job_resent_by_a_person_has_a_new_uid_and_prints(self):
        agent = self.stuck_agent()
        self.server.job(1, TICKET, name='Caja')
        with self.assertLogs('print_agent', level='ERROR'):
            agent.run_once()

        printed = []
        agent = self.agent(printers={'Caja': RAW}, queue_model=lambda q: 'Local Raw Printer',
                           cups_printer=lambda *args: printed.append(args))
        self.server.job(1, TICKET, name='Caja')
        self.server.queue[-1]['uid'] = 'f' * 32            # «Reenviar» da otro uid

        self.assertEqual(agent.run_once(), 'printed')
        self.assertEqual(len(printed), 1)

    def test_an_old_journal_without_outcomes_still_loads(self):
        import json

        with open(self.journal_path, 'w', encoding='utf-8') as handle:
            json.dump({'printed': ['a'], 'unconfirmed': {'a': [3, 'tok']}}, handle)

        journal = agent_module.Journal(self.journal_path)

        self.assertTrue(journal.was_printed('a'))
        self.assertIsNone(journal.held_outcome('a'))
        self.assertEqual(journal.unconfirmed['a'][:2], (3, 'tok'))


class PollingErrorsTest(SimpleTestCase):
    def send(self, cups, *, wait=3.0):
        from unittest import mock

        clock = iter(float(n) for n in range(1000))
        with mock.patch.object(agent_module.subprocess, 'run', side_effect=cups):
            return agent_module.send_to_cups_queue(
                'POS80_RAW', TICKET, 5.0, wait=wait,
                sleep=lambda seconds: None, clock=lambda: next(clock))

    def test_lpstat_breaking_after_lp_is_not_printed_never_a_retryable_failure(self):
        cups = FakeCups()
        original = cups.__call__

        def broken_poll(args, **kwargs):
            if args[:3] == ['lpstat', '-W', 'not-completed']:
                raise FileNotFoundError('lpstat')
            return original(args, **kwargs)

        with self.assertRaises(agent_module.CupsNotPrinted):
            self.send(broken_poll)

    def test_a_queue_whose_name_contains_the_words_is_read_by_its_status(self):
        from unittest import mock

        def cups(args, **kwargs):
            if args[:2] == ['lpstat', '-p']:
                return completed(args, 0, b'printer disabled_accepting is idle.  enabled since Tue\n')
            if args[:2] == ['lpstat', '-a']:
                return completed(args, 0, b'disabled_accepting accepting requests since Tue\n')
            if args[0] == 'lp':
                return completed(args, 0, b'request id is disabled_accepting-3 (0 file(s))\n')
            return completed(args, 0, b'')

        with mock.patch.object(agent_module.subprocess, 'run', side_effect=cups):
            self.assertEqual(
                agent_module.send_to_cups_queue('disabled_accepting', TICKET, 5.0, wait=1.0,
                                                sleep=lambda s: None),
                'disabled_accepting-3')

        def stopped(args, **kwargs):
            if args[:2] == ['lpstat', '-p']:
                return completed(args, 0, b'printer accepting_q disabled since Tue -\n\treason\n')
            return completed(args, 0, b'accepting_q accepting requests since Tue\n')

        with mock.patch.object(agent_module.subprocess, 'run', side_effect=stopped):
            with self.assertRaises(OSError):
                agent_module.send_to_cups_queue('accepting_q', TICKET, 5.0, sleep=lambda s: None)


class CupsWaitBoundTest(SimpleTestCase):
    def test_the_wait_must_fit_inside_the_lease(self):
        base = {'server': 'https://t.invalid', 'token': TOKEN}
        self.assertEqual(agent_module.validate_config(base)['cups_wait_seconds'], 30.0)
        self.assertEqual(
            agent_module.validate_config({**base, 'cups_wait_seconds': 45})['cups_wait_seconds'], 45.0)
        for bad in (46, 600, 0, -1):
            with self.subTest(bad):
                with self.assertRaises(agent_module.AgentError):
                    agent_module.validate_config({**base, 'cups_wait_seconds': bad})


class ResendGetsANewUidTest(PrintingBase):
    def test_a_person_resending_a_failed_job_gives_it_a_new_uid(self):
        job = printing.enqueue_test(printer=self.printer, key='uid', actor=None)
        PrintJob.objects.filter(pk=job.pk).update(status=PrintJob.Status.FAILED)
        job.refresh_from_db()
        before = job.uid

        printing.retry(job, actor=None)

        job.refresh_from_db()
        self.assertNotEqual(job.uid, before)
