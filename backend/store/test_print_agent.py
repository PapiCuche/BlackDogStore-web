"""
El agente de impresión del local — PAYMENT-FISCAL-PRINT-01.

Se prueba el programa de verdad (`print_agent/agent.py`) contra una impresora
simulada —un socket que escucha en este mismo equipo— y contra un servidor
simulado en memoria. Lo que se fija:

  · entrega los bytes tal cual y confirma;
  · si la impresora no responde, lo dice y no apunta nada como impreso;
  · EL PAPEL NO SALE DOS VECES: si la confirmación se pierde y el servidor
    vuelve a entregar el trabajo, el agente confirma sin imprimir;
  · no abre conexiones fuera de la red del local, diga lo que diga el servidor;
  · no arranca contra un servidor sin HTTPS y no escribe su token en el registro.
"""
import base64
import os
import socket
import tempfile
import threading

from django.test import SimpleTestCase

from print_agent.agent import Agent, AgentError, Journal, is_local_address, validate_config

TOKEN = 'bdpa_token-de-prueba-que-no-abre-nada'
TICKET = b'\x1b@TICKET DE PRUEBA\n\x1dVB\x00'


class FakePrinter:
    """Una térmica de red: acepta una conexión, guarda lo que recibe."""

    def __init__(self):
        self.received = []
        self._server = socket.socket()
        self._server.bind(('127.0.0.1', 0))
        self._server.listen(5)
        self.port = self._server.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        while True:
            try:
                connection, _address = self._server.accept()
            except OSError:
                return
            chunks = []
            with connection:
                while True:
                    data = connection.recv(4096)
                    if not data:
                        break
                    chunks.append(data)
            self.received.append(b''.join(chunks))

    def close(self):
        self._server.close()


class FakeServer:
    """El servidor, visto desde el agente: una cola y las confirmaciones que recibe."""

    def __init__(self):
        self.queue = []
        self.results = []
        self.calls = []
        self.fail_results = 0
        self.unauthorised = False

    def job(self, job_id, *, host='127.0.0.1', port=9100, claim_token=None, payload=TICKET):
        self.queue.append({
            'id': job_id, 'claim_token': claim_token or f'entrega-{job_id}-{len(self.calls)}',
            'kind': 'fiscal_ticket', 'attempt': 1, 'lease_seconds': 90,
            'printer': {'name': 'Caja', 'host': host, 'port': port},
            'payload_base64': base64.b64encode(payload).decode(),
        })

    def http(self, method, url, token, body=None, timeout=20.0):
        self.calls.append((method, url, token, body))
        if self.unauthorised:
            return 401, {'detail': 'Agente no autorizado.'}
        if url.endswith('/jobs/claim/'):
            return 200, {'job': self.queue.pop(0) if self.queue else None}
        if self.fail_results:
            self.fail_results -= 1
            raise OSError('connection reset')
        self.results.append((url, body))
        return 200, {'status': 'printed' if body['ok'] else 'pending'}


class PrintAgentTest(SimpleTestCase):
    def setUp(self):
        self.printer = FakePrinter()
        self.addCleanup(self.printer.close)
        self.server = FakeServer()
        directory = tempfile.mkdtemp(prefix='print-agent-')
        self.journal_path = os.path.join(directory, 'journal.json')
        self.agent = self.new_agent()

    def new_agent(self):
        """Un agente recién arrancado: lee el diario del disco, como tras un reinicio."""
        return Agent(
            {'server': 'https://tienda.invalid', 'token': TOKEN, 'journal': self.journal_path,
             'allow_loopback': True, 'printer_timeout': 2},
            http=self.server.http)

    def wait_for_paper(self, count=1):
        for _ in range(200):
            if len(self.printer.received) >= count:
                return
            threading.Event().wait(0.01)

    def test_nothing_to_print_is_idle(self):
        self.assertEqual(self.agent.run_once(), 'idle')
        self.assertEqual(self.printer.received, [])

    def test_it_delivers_the_bytes_as_they_came_and_confirms(self):
        self.server.job(7, port=self.printer.port)

        outcome = self.agent.run_once()
        self.wait_for_paper()

        self.assertEqual(outcome, 'printed')
        self.assertEqual(self.printer.received, [TICKET])
        url, body = self.server.results[0]
        self.assertTrue(url.endswith('/api/v1/print-agent/jobs/7/result/'))
        self.assertEqual((body['ok'], body['claim_token']), (True, 'entrega-7-0'))

    def test_a_printer_that_does_not_answer_is_reported_and_nothing_is_recorded(self):
        self.printer.close()
        self.server.job(8, port=self.printer.port)

        outcome = self.agent.run_once()

        self.assertEqual(outcome, 'failed')
        _url, body = self.server.results[0]
        self.assertFalse(body['ok'])
        self.assertTrue(body['error'])
        self.assertEqual(Journal(self.journal_path).printed, [])

    def test_a_lost_confirmation_does_not_print_the_ticket_twice(self):
        self.server.job(9, port=self.printer.port, claim_token='primera')
        self.server.fail_results = 1            # la confirmación se pierde

        self.assertEqual(self.agent.run_once(), 'printed')
        self.wait_for_paper()
        self.assertEqual(self.server.results, [])

        # El servidor, sin confirmación, vuelve a entregar el mismo trabajo. El
        # agente se reinició entre medias: sólo le queda el diario.
        self.server.job(9, port=self.printer.port, claim_token='segunda')
        outcome = self.new_agent().run_once()

        self.assertEqual(outcome, 'skipped')
        self.assertEqual(len(self.printer.received), 1)
        self.assertTrue(all(body['ok'] for _url, body in self.server.results))
        self.assertIn('segunda', [body['claim_token'] for _url, body in self.server.results])

    def test_a_pending_confirmation_is_sent_on_the_next_cycle(self):
        self.server.job(10, port=self.printer.port)
        self.server.fail_results = 1

        self.agent.run_once()
        self.wait_for_paper()
        outcome = self.agent.run_once()

        self.assertEqual(outcome, 'idle')
        self.assertEqual(len(self.printer.received), 1)
        self.assertEqual([body['ok'] for _url, body in self.server.results], [True])
        self.assertEqual(Journal(self.journal_path).unconfirmed, {})

    def test_it_never_connects_outside_the_local_network(self):
        connections = []
        agent = Agent(
            {'server': 'https://tienda.invalid', 'token': TOKEN, 'journal': self.journal_path},
            http=self.server.http,
            printer=lambda *args: connections.append(args))

        for host in ('8.8.8.8', 'impresora.example.com', '127.0.0.1', '0.0.0.0', ''):
            with self.subTest(host=host):
                self.server.job(11, host=host)
                self.assertEqual(agent.run_once(), 'failed')
        self.assertEqual(connections, [])
        self.assertTrue(all(not body['ok'] for _url, body in self.server.results))

    def test_what_counts_as_a_local_address(self):
        for host in ('192.168.1.50', '10.0.0.9', '172.16.4.2', 'termica-caja.local'):
            self.assertTrue(is_local_address(host), host)
        for host in ('8.8.8.8', '172.32.0.1', 'example.com', '127.0.0.1', 'local', ''):
            self.assertFalse(is_local_address(host), host)

    def test_a_revoked_token_stops_the_agent_from_printing(self):
        self.server.unauthorised = True
        self.server.job(12, port=self.printer.port)

        self.assertEqual(self.agent.run_once(), 'unauthorised')
        self.assertEqual(self.printer.received, [])

    def test_it_refuses_to_start_without_https_or_without_a_token(self):
        with self.assertRaises(AgentError):
            validate_config({'server': 'http://tienda.example', 'token': TOKEN})
        with self.assertRaises(AgentError):
            validate_config({'server': 'https://tienda.example', 'token': ''})
        self.assertEqual(
            validate_config({'server': 'https://tienda.example/', 'token': TOKEN})['server'],
            'https://tienda.example')

    def test_the_token_goes_to_the_server_and_never_to_the_log(self):
        self.printer.close()
        self.server.job(13, port=self.printer.port)

        with self.assertLogs('print_agent', level='INFO') as logs:
            self.agent.run_once()

        self.assertTrue(all(call[2] == TOKEN for call in self.server.calls))
        self.assertNotIn(TOKEN, '\n'.join(logs.output))

    # -- lo que encontró la revisión -------------------------------------------

    def test_addresses_that_only_look_local_are_refused(self):
        for host in ('::ffff:8.8.8.8', '::ffff:127.0.0.1', '2002:808:808::1', '::1',
                     '3232235826', '0xC0A80132', 'a.b.local', 'localhost'):
            self.assertFalse(is_local_address(host), host)
        self.assertTrue(is_local_address('169.254.10.1'))

    def test_a_server_that_only_starts_like_localhost_is_not_localhost(self):
        with self.assertRaises(AgentError):
            validate_config({'server': 'http://localhost.evil.example', 'token': TOKEN})
        with self.assertRaises(AgentError):
            validate_config({'server': 'http://127.0.0.1.evil.example', 'token': TOKEN})
        validate_config({'server': 'http://localhost:8000', 'token': TOKEN})

    def test_an_unexpected_answer_from_the_server_is_an_error_not_silence(self):
        self.server.http = lambda *a, **k: (404, {'detail': 'Not found'})
        agent = Agent(
            {'server': 'https://tienda.invalid', 'token': TOKEN, 'journal': self.journal_path},
            http=self.server.http)

        with self.assertLogs('print_agent', level='WARNING') as logs:
            outcome = agent.run_once()

        self.assertEqual(outcome, 'error')
        self.assertIn('404', '\n'.join(logs.output))

    def test_the_journal_does_not_confuse_jobs_of_another_database(self):
        # El mismo número de trabajo, en otra base (pruebas y luego producción):
        # el identificador estable es distinto y el ticket SÍ se imprime.
        self.server.job(9, port=self.printer.port)
        self.server.queue[-1]['uid'] = 'a' * 32
        self.agent.run_once()
        self.wait_for_paper()

        self.server.job(9, port=self.printer.port)
        self.server.queue[-1]['uid'] = 'b' * 32
        outcome = self.new_agent().run_once()
        self.wait_for_paper(2)

        self.assertEqual(outcome, 'printed')
        self.assertEqual(len(self.printer.received), 2)

    def test_a_cycle_that_blows_up_does_not_kill_the_agent(self):
        calls = []

        def broken(*args, **kwargs):
            calls.append(1)
            raise RuntimeError('respuesta truncada')

        agent = Agent(
            {'server': 'https://tienda.invalid', 'token': TOKEN, 'journal': self.journal_path},
            http=broken)

        with self.assertLogs('print_agent', level='ERROR'):
            self.assertEqual(agent.safe_cycle(), 'error')
        self.assertEqual(len(calls), 1)

    def test_it_waits_after_a_failure_instead_of_hammering_the_printer(self):
        self.assertGreater(self.agent.pause_after('failed'), 0)
        self.assertGreater(self.agent.pause_after('error'), 0)
        self.assertEqual(self.agent.pause_after('printed'), 0)

