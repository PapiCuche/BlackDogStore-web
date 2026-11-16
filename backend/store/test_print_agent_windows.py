"""
El agente de impresión en Windows, con una térmica USB — WIN-003B.

Lo que se fija, sin impresora y en cualquier sistema (la API de Windows se
sustituye por un spooler simulado):

  · LA COLA SALE DEL EQUIPO, NO DE INTERNET. El nombre de la cola Windows está
    en config.json; el servidor sólo dice el nombre de la impresora del panel.
    Una impresora que este equipo no tiene configurada no recibe nada.
  · SÓLO ESC/POS. Un PDF, un PostScript o una página HTML no llegan a la cola.
  · LO INCIERTO NO SE REPITE SOLO. Si el agente muere con un ticket «en vuelo»,
    o Windows no deja claro si salió, ese trabajo no se vuelve a mandar: se
    informa como fallido y una persona decide.
  · EL DIARIO. Ruta junto a config.json (no la carpeta en la que arrancó el
    Programador de tareas), escribible antes de imprimir, legible o no arranca,
    y un solo agente por diario.
  · CLOUDFLARE. El agente se presenta con su propio nombre: el de `urllib` lo
    bloquea el cortafuegos web de producción.

Y la cadena entera con las piezas de verdad: una venta de caja por la API (la
misma que usa un teléfono con datos móviles) → nota de venta → trabajo de
impresión → el agente lo recoge por HTTPS → cola Windows.
"""
import base64
import json
import os
import tempfile
from unittest import mock

from django.test import SimpleTestCase
from rest_framework.test import APIClient

from print_agent import agent as agent_module
from print_agent.agent import (
    UNCERTAIN_REASON, USER_AGENT, Agent, AgentError, DeliveryUncertain, InstanceLock, Journal,
    JournalError, load_config, payload_problem, send_to_windows_queue, valid_windows_queue,
    validate_config,
)
from store.models import PrintAgent, Printer, PrintJob
from store.printing import services as printing
from store.tests import Ip1PosBase

TOKEN = 'bdpa_token-de-prueba-que-no-abre-nada'
TICKET = b'\x1b@TICKET DE PRUEBA\n\x1dVB\x00'
QUEUE = 'POS-80-Series'


def windows():
    """Como si el agente corriera en Windows (la API real no se toca)."""
    return mock.patch.object(agent_module, 'IS_WINDOWS', True)


class PowerCut(BaseException):
    """El equipo se apaga a mitad de la entrega: nada del agente lo atrapa."""


class FakeQueue:
    """Una cola Windows: guarda lo que recibe y puede fallar como se le pida."""

    def __init__(self):
        self.received = []
        self.fail = None

    def __call__(self, queue, payload, timeout, wait, *, document='Ticket'):
        if self.fail is not None:
            raise self.fail
        self.received.append((queue, payload, document))


class FakeServer:
    def __init__(self):
        self.queue, self.results, self.calls = [], [], []

    def job(self, job_id, *, name='Caja', host='8.8.8.8', payload=TICKET, kind='sales_note_ticket',
            uid=None, claim_token=None):
        self.queue.append({
            'id': job_id, 'uid': uid or f'{job_id:032x}',
            'claim_token': claim_token or f'entrega-{job_id}-{len(self.calls)}',
            'kind': kind, 'attempt': 1, 'lease_seconds': 90,
            'printer': {'name': name, 'host': host, 'port': 9100},
            'payload_base64': base64.b64encode(payload).decode(),
        })

    def http(self, method, url, token, body=None, timeout=20.0):
        self.calls.append((method, url, body))
        if url.endswith('/jobs/claim/'):
            return 200, {'job': self.queue.pop(0) if self.queue else None}
        if url.endswith('/jobs/0/result/'):
            return 404, {'detail': 'No encontrado.'}
        self.results.append((url, body))
        return 200, {'status': 'printed' if body['ok'] else 'pending'}


class WindowsAgentBase(SimpleTestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix='win-agent-')
        self.journal_path = os.path.join(self.directory, 'journal.json')
        self.server = FakeServer()
        self.queue = FakeQueue()
        self.tcp = []
        patcher = windows()
        patcher.start()
        self.addCleanup(patcher.stop)

    def config(self, **over):
        config = {'server': 'https://tienda.invalid', 'token': TOKEN, 'journal': self.journal_path,
                  'printers': {'Caja': {'transport': 'windows_raw', 'queue': QUEUE}}}
        config.update(over)
        return config

    def new_agent(self, **over):
        """Un agente recién arrancado: lee el diario del disco, como tras un reinicio."""
        return Agent(self.config(**over), http=self.server.http,
                     printer=lambda *args: self.tcp.append(args), windows_printer=self.queue)


class WindowsRouteTest(WindowsAgentBase):
    def test_the_ticket_goes_to_the_configured_windows_queue(self):
        self.server.job(7)

        outcome = self.new_agent().run_once()

        self.assertEqual(outcome, 'printed')
        self.assertEqual(self.queue.received, [(QUEUE, TICKET, 'Ticket 7')])
        # La dirección que mandó el servidor no se usa: no hay conexión de red.
        self.assertEqual(self.tcp, [])
        _url, body = self.server.results[0]
        self.assertEqual((body['ok'], body['claim_token']), (True, 'entrega-7-0'))

    def test_a_printer_this_computer_does_not_know_receives_nothing(self):
        # Renombrada en el panel, u otra del mismo local: no se adivina.
        self.server.job(8, name='Caja 2')

        outcome = self.new_agent().run_once()

        self.assertEqual(outcome, 'failed')
        self.assertEqual((self.queue.received, self.tcp), ([], []))
        _url, body = self.server.results[0]
        self.assertFalse(body['ok'])
        self.assertIn('no está configurada en este equipo', body['error'])

    def test_the_server_cannot_choose_the_queue(self):
        # Nada del trabajo nombra una cola; un nombre de cola puesto como
        # nombre de impresora no está en la lista.
        for name in (QUEUE, '\\\\servidor\\cola', 'Microsoft Print to PDF'):
            with self.subTest(name=name):
                self.server.job(9, name=name)
                self.assertEqual(self.new_agent().run_once(), 'failed')
        self.assertEqual(self.queue.received, [])

    def test_without_a_printers_list_everything_goes_by_network_as_before(self):
        self.server.job(10, host='192.168.1.50')

        outcome = self.new_agent(printers=None).run_once()

        self.assertEqual(outcome, 'printed')
        self.assertEqual(self.queue.received, [])
        self.assertEqual(self.tcp[0][:2], ('192.168.1.50', 9100))

    def test_a_tcp_entry_still_refuses_addresses_outside_the_shop(self):
        self.server.job(11, host='8.8.8.8')

        outcome = self.new_agent(printers={'Caja': {'transport': 'tcp'}}).run_once()

        self.assertEqual(outcome, 'failed')
        self.assertEqual(self.tcp, [])


class OnlyEscPosTest(WindowsAgentBase):
    def test_what_is_not_an_escpos_ticket_never_reaches_the_queue(self):
        for label, payload in (
            ('pdf', b'%PDF-1.7\n...'),
            ('pdf tras BOM', b'\xef\xbb\xbf  %PDF-1.4'),
            ('postscript', b'%!PS-Adobe-3.0\n'),
            ('pjl + pdf', b'\x1b%-12345X@PJL\r\n%PDF-1.7'),
            ('html', b'<!DOCTYPE html><html>'),
            ('texto', b'hola'),
        ):
            with self.subTest(label):
                self.server.job(20, payload=payload)
                self.assertEqual(self.new_agent().run_once(), 'refused')
                self.assertTrue(payload_problem(payload))
        self.assertEqual(self.queue.received, [])

    def test_an_unknown_kind_of_document_is_not_printed(self):
        self.server.job(21, kind='label')

        self.assertEqual(self.new_agent().run_once(), 'refused')
        self.assertEqual(self.queue.received, [])

    def test_the_real_renderer_output_passes(self):
        from store.printing.escpos import Receipt

        ticket = Receipt().line('Ñandú S/ 10.00').cut().to_bytes()
        self.assertEqual(payload_problem(ticket), '')


class UncertainDeliveryTest(WindowsAgentBase):
    def test_a_power_cut_while_delivering_is_never_printed_again_by_itself(self):
        self.server.job(30, claim_token='primera')
        self.queue.fail = PowerCut()

        with self.assertRaises(PowerCut):
            self.new_agent().run_once()

        # Vuelve la luz. El servidor no tuvo respuesta y entrega otra vez.
        self.queue.fail = None
        self.server.job(30, claim_token='segunda')
        outcome = self.new_agent().run_once()

        self.assertEqual(outcome, 'skipped')
        self.assertEqual(self.queue.received, [])
        _url, body = self.server.results[-1]
        self.assertEqual((body['ok'], body['claim_token']), (False, 'segunda'))
        self.assertEqual(body['error'], UNCERTAIN_REASON[:300])

    def test_windows_not_knowing_is_reported_and_held(self):
        self.server.job(31)
        self.queue.fail = DeliveryUncertain('quedó en la cola y no se pudo retirar')

        self.assertEqual(self.new_agent().run_once(), 'uncertain')
        self.queue.fail = None
        self.server.job(31)
        self.assertEqual(self.new_agent().run_once(), 'skipped')

        self.assertEqual(self.queue.received, [])
        self.assertTrue(all(not body['ok'] for _url, body in self.server.results))
        self.assertTrue(all(body['error'].startswith('INCIERTO') for _u, body in self.server.results))

    def test_a_sure_failure_is_retried_normally(self):
        self.server.job(32)
        self.queue.fail = OSError('La cola «POS-80-Series» está en pausa')

        self.assertEqual(self.new_agent().run_once(), 'failed')
        self.assertEqual(Journal(self.journal_path).printed, [])

        self.queue.fail = None
        self.server.job(32)
        self.assertEqual(self.new_agent().run_once(), 'printed')
        self.assertEqual(len(self.queue.received), 1)

    def test_a_person_who_checked_the_counter_can_release_a_held_job(self):
        self.server.job(33)
        self.queue.fail = DeliveryUncertain('no consta')
        self.new_agent().run_once()

        journal = Journal(self.journal_path)
        self.assertTrue(journal.release(f'{33:032x}'))
        self.assertFalse(journal.release('no-existe'))

        self.queue.fail = None
        self.server.job(33)
        self.assertEqual(self.new_agent().run_once(), 'printed')
        self.assertEqual(len(self.queue.received), 1)

    def test_a_lost_confirmation_still_does_not_print_twice(self):
        self.server.job(34)
        calls = {'n': 0}
        original = self.server.http

        def lose_first_result(method, url, token, body=None, timeout=20.0):
            if '/result/' in url and calls['n'] == 0:
                calls['n'] += 1
                raise OSError('connection reset')
            return original(method, url, token, body, timeout)

        self.server.http = lose_first_result
        self.assertEqual(self.new_agent().run_once(), 'printed')
        self.server.job(34, claim_token='otra')
        self.assertEqual(self.new_agent().run_once(), 'skipped')
        self.assertEqual(len(self.queue.received), 1)


class JournalTest(WindowsAgentBase):
    def test_a_relative_journal_lives_next_to_the_config_not_where_it_started(self):
        config_path = os.path.join(self.directory, 'config.json')
        with open(config_path, 'w', encoding='utf-8') as handle:
            json.dump({'server': 'https://tienda.invalid', 'token': TOKEN,
                       'journal': 'diario.json', 'log_file': 'agente.log'}, handle)
        elsewhere = tempfile.mkdtemp(prefix='cwd-')
        previous = os.getcwd()
        os.chdir(elsewhere)
        self.addCleanup(os.chdir, previous)

        config = load_config(config_path)

        self.assertEqual(config['journal'], os.path.join(self.directory, 'diario.json'))
        self.assertEqual(config['log_file'], os.path.join(self.directory, 'agente.log'))
        self.assertTrue(os.path.isabs(config['journal']))

    def test_an_unwritable_journal_stops_the_agent_before_printing(self):
        missing = os.path.join(self.directory, 'no-existe', 'journal.json')

        with self.assertRaises(JournalError):
            self.new_agent(journal=missing)

    def test_a_journal_that_cannot_be_written_mid_run_prints_nothing(self):
        agent = self.new_agent()
        self.server.job(40)

        with mock.patch.object(Journal, '_save', side_effect=PermissionError('denied')):
            outcome = agent.run_once()

        self.assertEqual(outcome, 'error')
        self.assertEqual(self.queue.received, [])
        self.assertFalse(self.server.results[-1][1]['ok'])

    def test_an_unreadable_journal_is_not_silently_reset(self):
        with open(self.journal_path, 'w', encoding='utf-8') as handle:
            handle.write('{roto')

        with self.assertRaises(JournalError):
            self.new_agent()

    def test_a_journal_of_the_previous_version_still_counts(self):
        with open(self.journal_path, 'w', encoding='utf-8') as handle:
            json.dump({'printed': [f'{50:032x}'], 'unconfirmed': {}}, handle)
        self.server.job(50)

        self.assertEqual(self.new_agent().run_once(), 'skipped')
        self.assertEqual(self.queue.received, [])

    def test_the_journal_keeps_no_ticket_bytes_and_no_token(self):
        self.server.job(51, payload=b'\x1b@CLIENTE ROSA QUISPE DNI 45678912\n')
        self.new_agent().run_once()

        with open(self.journal_path, encoding='utf-8') as handle:
            text = handle.read()
        for secret in ('ROSA', '45678912', TOKEN, 'payload'):
            self.assertNotIn(secret, text)

    def test_held_jobs_survive_the_journal_trimming(self):
        journal = Journal(self.journal_path)
        journal.record_held('retenido', 1, 't', 'INCIERTO: x')
        for index in range(agent_module.JOURNAL_LIMIT + 20):
            journal.record_printed(f'k{index}', index, 't')
            journal.confirmed(f'k{index}')

        self.assertIsNotNone(Journal(self.journal_path).held_reason('retenido'))

    def test_only_one_agent_per_journal(self):
        first = InstanceLock(self.journal_path).acquire()
        self.addCleanup(first.release)

        with self.assertRaises(AgentError):
            InstanceLock(self.journal_path).acquire()
        first.release()
        InstanceLock(self.journal_path).acquire().release()


class ConfigTest(WindowsAgentBase):
    def test_only_plain_local_queue_names_are_accepted(self):
        for good in (QUEUE, 'Térmica Caja 1', 'HL80'):
            self.assertTrue(valid_windows_queue(good), good)
        for bad in ('', ' POS', '\\\\servidor\\cola', '//servidor/cola', 'POS,Job 12',
                    ',XcvMonitor Local Port', 'POS\x00', 'a' * 221, None, 5):
            self.assertFalse(valid_windows_queue(bad), bad)

    def test_windows_raw_needs_windows_and_a_queue(self):
        with self.assertRaises(AgentError):
            validate_config(self.config(printers={'Caja': {'transport': 'windows_raw'}}))
        with self.assertRaises(AgentError):
            validate_config(self.config(printers={'Caja': {'transport': 'lpt'}}))
        with mock.patch.object(agent_module, 'IS_WINDOWS', False), self.assertRaises(AgentError):
            validate_config(self.config())

    def test_the_spool_wait_fits_in_the_server_lease(self):
        for bad in (0, 46, 'x'):
            with self.assertRaises(AgentError):
                validate_config(self.config(spool_wait_seconds=bad))

    def test_the_agent_introduces_itself_and_never_as_python_urllib(self):
        seen = {}

        class Opener:
            def open(self, request, timeout):
                seen.update(request.header_items())
                raise agent_module.urllib.error.HTTPError(
                    request.full_url, 401, 'x', {}, __import__('io').BytesIO(b'{}'))

        with mock.patch.object(agent_module, '_OPENER', Opener()):
            code, _body = agent_module.http_json('POST', 'https://tienda.invalid/x', TOKEN, {})

        self.assertEqual(code, 401)
        self.assertEqual(seen.get('User-agent'), USER_AGENT)
        self.assertNotIn('urllib', USER_AGENT.lower())

    def test_check_never_claims_a_job(self):
        with mock.patch.object(agent_module, 'check_windows_queue', return_value=''):
            results = self.new_agent().check()

        self.assertTrue(all(ok for _what, ok, _detail in results), results)
        self.assertFalse(any(url.endswith('/claim/') for _m, url, _b in self.server.calls))


class FakeSpooler:
    """`winspool.drv`, simulado. `states`: lo que devuelve GetJob en cada consulta."""

    def __init__(self, *, printer_status=0, states=(None,), write_fails=False, end_fails=False,
                 can_delete=True):
        self.printer_status_value = printer_status
        self.states = list(states)
        self.write_fails, self.end_fails, self.can_delete = write_fails, end_fails, can_delete
        self.log = []

    def open(self, queue):
        self.log.append(('open', queue))
        return 'h'

    def close(self, handle):
        self.log.append(('close',))

    def printer_status(self, handle):
        return self.printer_status_value

    def start(self, handle, document):
        self.log.append(('start', document))
        return 42

    def write(self, handle, payload):
        if self.write_fails:
            raise OSError('Escribir en la cola falló')
        self.log.append(('write', payload))

    def abort(self, handle):
        self.log.append(('abort',))

    def end(self, handle):
        if self.end_fails:
            raise OSError('Cerrar el trabajo falló')
        self.log.append(('end',))

    def job_status(self, handle, job_id):
        return self.states.pop(0) if len(self.states) > 1 else self.states[0]

    def delete(self, handle, job_id):
        self.log.append(('delete', job_id))
        return self.can_delete


class SendToWindowsQueueTest(SimpleTestCase):
    def send(self, spooler, wait=5.0):
        ticks = iter(range(1000))
        return send_to_windows_queue(QUEUE, TICKET, 10, wait, spooler=spooler,
                                     clock=lambda: next(ticks), sleep=lambda _s: None)

    def test_a_job_that_leaves_the_queue_is_delivered(self):
        spooler = FakeSpooler(states=[0x8, 0x10, None])

        self.send(spooler)

        self.assertIn(('write', TICKET), spooler.log)
        self.assertNotIn(('delete', 42), spooler.log)
        self.assertEqual(spooler.log[-1], ('close',))

    def test_a_paused_or_disconnected_queue_gets_nothing(self):
        for status in (0x1, 0x80, 0x10, 0x2):
            with self.subTest(status=hex(status)):
                spooler = FakeSpooler(printer_status=status)
                with self.assertRaises(OSError):
                    self.send(spooler)
                self.assertFalse(any(entry[0] == 'start' for entry in spooler.log))

    def test_a_write_that_fails_aborts_the_whole_job(self):
        spooler = FakeSpooler(write_fails=True)

        with self.assertRaises(OSError):
            self.send(spooler)

        self.assertIn(('abort',), spooler.log)
        self.assertNotIn(('end',), spooler.log)

    def test_a_stuck_job_is_withdrawn_and_reported_as_a_sure_failure(self):
        spooler = FakeSpooler(states=[0x20])                  # offline, sin enviar

        with self.assertRaises(OSError) as raised:
            self.send(spooler)

        self.assertNotIsInstance(raised.exception, DeliveryUncertain)
        self.assertIn(('delete', 42), spooler.log)

    def test_a_stuck_job_that_cannot_be_withdrawn_is_uncertain(self):
        spooler = FakeSpooler(states=[0x20], can_delete=False)

        with self.assertRaises(DeliveryUncertain):
            self.send(spooler)

    def test_a_job_still_printing_at_the_deadline_is_uncertain_and_not_cut(self):
        spooler = FakeSpooler(states=[0x10])

        with self.assertRaises(DeliveryUncertain):
            self.send(spooler, wait=3)

        self.assertNotIn(('delete', 42), spooler.log)

    def test_a_job_that_did_not_close_is_withdrawn(self):
        spooler = FakeSpooler(end_fails=True)

        with self.assertRaises(OSError) as raised:
            self.send(spooler)

        self.assertNotIsInstance(raised.exception, DeliveryUncertain)
        self.assertIn(('delete', 42), spooler.log)

    def test_a_job_marked_printed_counts_as_delivered(self):
        self.send(FakeSpooler(states=[0x80]))


@mock.patch.object(agent_module, 'IS_WINDOWS', True)
class PhoneSaleToWindowsPrinterTest(Ip1PosBase):
    """
    Teléfono con datos móviles → API de caja → nota de venta → trabajo → agente
    Windows → cola USB. Nada de esto pasa por la red de la tienda ni por el
    navegador: el teléfono sólo habla con el servidor.
    """

    URL = '/api/v1/internal/ip1-tienda/sales/pos/sales/'

    def setUp(self):
        super().setUp()
        self.printer = Printer.objects.create(
            company=self.company, branch=self.branch_a, name='Caja', host='192.168.1.50')
        self.agent_row, self.token = printing.create_agent(
            company=self.company, branch=self.branch_a, name='PC caja', actor=None)
        self.queue = FakeQueue()

    def sell(self, **over):
        return self.client.post(self.URL, self.sale_body(**over), format='json')

    def windows_agent(self, token=None, *, journal=None):
        api = APIClient()

        def http(method, url, token, body=None, timeout=20.0):
            response = api.post(url.split('https://tienda.invalid', 1)[1], body or {},
                                format='json', HTTP_AUTHORIZATION=f'PrintAgent {token}')
            return response.status_code, response.data

        journal = journal or os.path.join(tempfile.mkdtemp(prefix='e2e-win-'), 'journal.json')
        return Agent(
            {'server': 'https://tienda.invalid', 'token': token or self.token, 'journal': journal,
             'printers': {'Caja': {'transport': 'windows_raw', 'queue': QUEUE}}},
            http=http, printer=lambda *a: self.fail('nunca por red'), windows_printer=self.queue)

    def test_a_till_sale_prints_on_the_usb_printer_without_anyone_clicking_print(self):
        before = self.stock()

        res = self.sell(receipt_type='sales_note')

        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data['print_job']['status'], 'pending')
        self.assertEqual(self.windows_agent().run_once(), 'printed')
        job = PrintJob.objects.get()
        self.assertEqual((job.status, job.branch, job.order_id),
                         (PrintJob.Status.PRINTED, self.branch_a, res.data['order_id']))
        (queue, payload, _doc), = self.queue.received
        self.assertEqual(queue, QUEUE)
        self.assertTrue(payload.startswith(b'\x1b@'))
        self.assertIn(b'Cable USB-C', payload)
        self.assertEqual(self.stock(), before - 2)

    def test_resending_the_sale_does_not_print_or_sell_twice(self):
        before = self.stock()
        agent = self.windows_agent()

        self.sell(receipt_type='sales_note')
        agent.run_once()
        self.sell(receipt_type='sales_note')                 # el teléfono reintenta

        self.assertEqual(agent.run_once(), 'idle')
        self.assertEqual(len(self.queue.received), 1)
        self.assertEqual(PrintJob.objects.count(), 1)
        self.assertEqual(self.stock(), before - 2)

    def test_agents_of_another_branch_or_company_get_nothing(self):
        self.sell(receipt_type='sales_note')
        _norte, norte_token = printing.create_agent(
            company=self.company, branch=self.branch_b, name='Norte', actor=None)
        _ajeno, foreign_token = printing.create_agent(
            company=self.other, branch=self.foreign_branch, name='Ajena', actor=None)

        self.assertEqual(self.windows_agent(norte_token).run_once(), 'idle')
        self.assertEqual(self.windows_agent(foreign_token).run_once(), 'idle')
        self.assertEqual(self.queue.received, [])
        self.assertEqual(PrintJob.objects.get().status, PrintJob.Status.PENDING)

    def test_two_agents_of_the_same_shop_never_print_the_same_job(self):
        self.sell(receipt_type='sales_note')
        _second, second_token = printing.create_agent(
            company=self.company, branch=self.branch_a, name='PC 2', actor=None)

        outcomes = [self.windows_agent().run_once(), self.windows_agent(second_token).run_once()]

        self.assertEqual(sorted(outcomes), ['idle', 'printed'])
        self.assertEqual(len(self.queue.received), 1)

    def test_a_sale_without_a_receipt_queues_no_ticket(self):
        res = self.sell()

        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(PrintJob.objects.count(), 0)
        self.assertEqual(self.windows_agent().run_once(), 'idle')

    def test_windows_offline_keeps_the_job_and_the_sale(self):
        res = self.sell(receipt_type='sales_note')
        self.queue.fail = OSError('La cola «POS-80-Series» está desconectada')

        self.assertEqual(self.windows_agent().run_once(), 'failed')

        job = PrintJob.objects.get()
        self.assertEqual(job.status, PrintJob.Status.PENDING)
        self.assertIn('desconectada', job.last_error)
        self.assertEqual(job.order.pk, res.data['order_id'])
        self.assertTrue(job.order.paid)

    def test_an_uncertain_ticket_ends_failed_and_a_new_reprint_prints(self):
        self.sell(receipt_type='sales_note')
        journal = os.path.join(tempfile.mkdtemp(prefix='e2e-win-'), 'journal.json')
        self.queue.fail = DeliveryUncertain('no consta')
        self.assertEqual(self.windows_agent(journal=journal).run_once(), 'uncertain')

        # El servidor lo vuelve a ofrecer hasta agotar los intentos; nunca sale.
        self.queue.fail = None
        job = PrintJob.objects.get()
        for _ in range(printing.MAX_ATTEMPTS):
            PrintJob.objects.filter(pk=job.pk).update(available_at=None)
            self.windows_agent(journal=journal).run_once()
        job.refresh_from_db()
        self.assertEqual(job.status, PrintJob.Status.FAILED)
        self.assertTrue(job.last_error.startswith('INCIERTO'))
        self.assertEqual(self.queue.received, [])

        # Una persona miró el mostrador y pide reimprimir: trabajo nuevo, sí sale.
        printing.enqueue_manual(order=job.order, printer=self.printer, key='rep-1', actor=None)
        self.assertEqual(self.windows_agent(journal=journal).run_once(), 'printed')
        self.assertEqual(len(self.queue.received), 1)

    def test_a_revoked_agent_stops_at_once(self):
        self.sell(receipt_type='sales_note')
        PrintAgent.objects.filter(pk=self.agent_row.pk).update(is_active=False)

        self.assertEqual(self.windows_agent().run_once(), 'unauthorised')
        self.assertEqual(self.queue.received, [])
