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
        self.results.append((url, body))
        return 200, {'status': 'printed' if body['ok'] else 'pending'}


class AgentTestBase(SimpleTestCase):
    def setUp(self):
        self.server = FakeServer()
        self.sent = []
        directory = tempfile.mkdtemp(prefix='print-thermal-')
        self.journal_path = os.path.join(directory, 'journal.json')

    def agent(self, **extra):
        config = {'server': 'https://tienda.invalid', 'token': TOKEN,
                  'journal': self.journal_path, **extra}
        return Agent(config, http=self.server.http,
                     printer=lambda *args: self.sent.append(('tcp', *args)))


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
