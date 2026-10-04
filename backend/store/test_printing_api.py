"""
La cola de impresión, por sus dos puertas — PAYMENT-FISCAL-PRINT-01.

  · LA DEL AGENTE. Sin sesión de usuario: un token que vale para una sucursal.
    Recoge trabajos y confirma, y nada más.
  · LA DEL PANEL. Dar de alta impresoras y agentes es configurar la empresa;
    reimprimir es operar la caja. Las dos respetan las sucursales de quien pide.
"""
import base64

from django.core.cache import cache
from rest_framework.test import APIClient

from store.models import AdminAuditLog, Branch, PrintAgent, Printer, PrintJob
from store.printing import services as printing
from store.test_fiscal_print import FiscalPrintBase
from store.tests import _p2d_member, _p3_company

CLAIM = '/api/v1/print-agent/jobs/claim/'
PRINTERS = '/api/admin/printing/printers/'
AGENTS = '/api/admin/printing/agents/'
JOBS = '/api/admin/printing/jobs/'


def result_url(job_id):
    return f'/api/v1/print-agent/jobs/{job_id}/result/'


class PrintApiBase(FiscalPrintBase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.branch = self.company.default_inventory_branch
        self.printer = Printer.objects.create(
            company=self.company, branch=self.branch, name='Caja', host='192.168.1.50')
        self.agent, self.token = printing.create_agent(
            company=self.company, branch=self.branch, name='Mostrador', actor=None)
        self.manager, _ = _p2d_member(
            self.company, 'print_gestor',
            ['company.view', 'company.manage', 'sales.orders.view', 'sales.pos.use'])
        self.seller, _ = _p2d_member(
            self.company, 'print_vendedor', ['sales.orders.view', 'sales.pos.use'])

    def as_agent(self, token=None):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'PrintAgent {self.token if token is None else token}')
        return client

    def as_user(self, user):
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    def q(self, company=None):
        return f'?company={(company or self.company).pk}'


class AgentDoorTest(PrintApiBase):
    def test_without_a_valid_token_nothing_is_delivered(self):
        self.issue()

        for client in (APIClient(), self.as_agent('bdpa_no-existe'), self.as_agent('Bearer x')):
            self.assertEqual(client.post(CLAIM).status_code, 401)
        self.assertEqual(PrintJob.objects.get().status, PrintJob.Status.PENDING)

    def test_a_user_session_is_not_an_agent(self):
        self.issue()

        self.assertEqual(self.as_user(self.manager).post(CLAIM).status_code, 401)

    def test_an_empty_queue_answers_with_no_job(self):
        res = self.as_agent().post(CLAIM)

        self.assertEqual(res.status_code, 200)
        self.assertIsNone(res.data['job'])
        self.agent.refresh_from_db()
        self.assertIsNotNone(self.agent.last_seen_at)

    def test_the_agent_gets_the_bytes_and_where_to_send_them(self):
        self.issue()

        res = self.as_agent().post(CLAIM)

        job = res.data['job']
        self.assertEqual(
            job['printer'], {'name': 'Caja', 'host': '192.168.1.50', 'port': 9100})
        payload = base64.b64decode(job['payload_base64'])
        self.assertTrue(payload.startswith(b'\x1b@'))
        self.assertIn(b'B001-1', payload)
        self.assertGreater(job['lease_seconds'], 0)
        # Nada de lo que no necesita para imprimir: ni cliente, ni importes, ni pedido.
        self.assertEqual(
            set(job), {'id', 'uid', 'claim_token', 'kind', 'attempt', 'printer',
                       'payload_base64', 'lease_seconds'})

    def test_confirming_closes_the_job_and_repeating_it_changes_nothing(self):
        self.issue()
        job = self.as_agent().post(CLAIM).data['job']
        body = {'claim_token': job['claim_token'], 'ok': True}

        first = self.as_agent().post(result_url(job['id']), body, format='json')
        second = self.as_agent().post(result_url(job['id']), body, format='json')

        self.assertEqual((first.status_code, second.status_code), (200, 200))
        self.assertEqual(first.data['status'], 'printed')
        self.assertEqual(second.data['status'], 'printed')
        self.assertIsNone(self.as_agent().post(CLAIM).data['job'])

    def test_a_failure_is_recorded_and_the_job_returns_to_the_queue(self):
        self.issue()
        job = self.as_agent().post(CLAIM).data['job']

        res = self.as_agent().post(
            result_url(job['id']),
            {'claim_token': job['claim_token'], 'ok': False, 'error': 'Connection refused'},
            format='json')

        self.assertEqual(res.data['status'], 'pending')
        self.assertEqual(PrintJob.objects.get().last_error, 'Connection refused')
        # Espera antes del siguiente intento; pasada la espera, se entrega otra vez.
        self.assertIsNone(self.as_agent().post(CLAIM).data['job'])
        PrintJob.objects.update(available_at=None)
        self.assertEqual(self.as_agent().post(CLAIM).data['job']['attempt'], 2)

    def test_a_stale_or_foreign_confirmation_is_refused(self):
        self.issue()
        job = self.as_agent().post(CLAIM).data['job']
        other = _p3_company('api-otra', 'Otra')
        _agent, other_token = printing.create_agent(
            company=other, branch=other.default_inventory_branch, name='Ajeno', actor=None)

        stale = self.as_agent().post(
            result_url(job['id']), {'claim_token': 'f' * 32, 'ok': True}, format='json')
        foreign = self.as_agent(other_token).post(
            result_url(job['id']), {'claim_token': job['claim_token'], 'ok': True}, format='json')

        self.assertEqual(stale.status_code, 409)
        self.assertEqual(foreign.status_code, 404)
        self.assertEqual(PrintJob.objects.get().status, PrintJob.Status.PRINTING)

    def test_a_document_that_can_no_longer_be_printed_fails_without_blocking_the_queue(self):
        first = self.issue()
        self.issue()
        first.signed_xml = ''
        first.save(update_fields=['signed_xml'])

        job = self.as_agent().post(CLAIM).data['job']

        # El primero no se puede dibujar: se aparta, con espera, y se entrega el siguiente.
        self.assertIn(b'B001-2', base64.b64decode(job['payload_base64']))
        apartado = PrintJob.objects.get(fiscal_document=first)
        self.assertEqual(apartado.status, PrintJob.Status.PENDING)
        self.assertIsNotNone(apartado.available_at)


class PrinterConfigTest(PrintApiBase):
    def test_a_manager_registers_a_printer_of_a_branch(self):
        south = Branch.objects.create(company=self.company, name='Sur')

        res = self.as_user(self.manager).post(PRINTERS + self.q(), {
            'branch': south.pk, 'name': 'Térmica Sur', 'host': '192.168.10.20',
        }, format='json')

        self.assertEqual(res.status_code, 201, res.data)
        printer = Printer.objects.get(pk=res.data['id'])
        self.assertEqual(
            (printer.company, printer.branch, printer.port, printer.paper_width_mm, printer.auto_print),
            (self.company, south, 9100, 80, True))
        self.assertTrue(AdminAuditLog.objects.filter(
            action='printer_created', company=self.company).exists())

    def test_the_address_must_be_inside_a_local_network(self):
        for host in ('8.8.8.8', 'tienda.example.com', 'http://192.168.1.5', '192.168.1.5; rm', ''):
            with self.subTest(host=host):
                res = self.as_user(self.manager).post(PRINTERS + self.q(), {
                    'branch': self.branch.pk, 'name': f'P {host}', 'host': host,
                    'auto_print': False,
                }, format='json')
                self.assertEqual(res.status_code, 400, res.data)

    def test_a_second_automatic_printer_in_the_same_branch_is_refused(self):
        res = self.as_user(self.manager).post(PRINTERS + self.q(), {
            'branch': self.branch.pk, 'name': 'Otra', 'host': '192.168.1.51',
        }, format='json')

        self.assertEqual(res.status_code, 400, res.data)

    def test_a_seller_cannot_configure_printers(self):
        res = self.as_user(self.seller).post(PRINTERS + self.q(), {
            'branch': self.branch.pk, 'name': 'X', 'host': '192.168.1.52', 'auto_print': False,
        }, format='json')

        self.assertEqual(res.status_code, 403)

    def test_a_branch_of_another_company_does_not_exist_here(self):
        other = _p3_company('cfg-otra', 'Otra')

        res = self.as_user(self.manager).post(PRINTERS + self.q(), {
            'branch': other.default_inventory_branch.pk, 'name': 'X', 'host': '192.168.1.53',
        }, format='json')

        self.assertEqual(res.status_code, 400, res.data)
        self.assertFalse(Printer.objects.filter(company=other).exists())

    def test_the_agent_token_is_returned_once_and_never_listed(self):
        created = self.as_user(self.manager).post(AGENTS + self.q(), {
            'branch': self.branch.pk, 'name': 'Trastienda',
        }, format='json')

        self.assertEqual(created.status_code, 201, created.data)
        token = created.data['token']
        self.assertEqual(printing.authenticate_agent(token).name, 'Trastienda')
        listed = self.as_user(self.manager).get(AGENTS + self.q())
        self.assertNotIn(token, str(listed.data))
        self.assertNotIn('token_hash', str(listed.data))

    def test_revoking_an_agent_stops_it(self):
        res = self.as_user(self.manager).delete(f'{AGENTS}{self.agent.pk}/{self.q()}')

        self.assertEqual(res.status_code, 204)
        self.assertEqual(self.as_agent().post(CLAIM).status_code, 401)
        self.assertFalse(PrintAgent.objects.get(pk=self.agent.pk).is_active)


class ReprintTest(PrintApiBase):
    def test_a_seller_reprints_a_paid_order_on_the_printer_of_its_branch(self):
        document = self.issue()
        client = self.as_user(self.seller)

        first = client.post(
            JOBS + self.q(), {'order': document.order_id}, format='json',
            HTTP_IDEMPOTENCY_KEY='reimpresion-0001')
        again = client.post(
            JOBS + self.q(), {'order': document.order_id}, format='json',
            HTTP_IDEMPOTENCY_KEY='reimpresion-0001')

        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(again.status_code, 200, again.data)
        self.assertEqual(first.data['id'], again.data['id'])
        self.assertEqual(PrintJob.objects.filter(reason='manual').count(), 1)

    def test_a_reprint_needs_an_idempotency_key(self):
        document = self.issue()

        res = self.as_user(self.seller).post(
            JOBS + self.q(), {'order': document.order_id}, format='json')

        self.assertEqual(res.status_code, 400)

    def test_an_order_of_another_company_cannot_be_reprinted_here(self):
        document = self.issue()
        other = _p3_company('rep-otra', 'Otra')
        stranger, _ = _p2d_member(other, 'rep_ajeno', ['sales.orders.view', 'sales.pos.use'])

        res = self.as_user(stranger).post(
            f'{JOBS}?company={other.pk}', {'order': document.order_id}, format='json',
            HTTP_IDEMPOTENCY_KEY='reimpresion-0002')

        self.assertEqual(res.status_code, 404)

    def test_the_list_shows_the_jobs_of_the_branches_the_caller_reaches(self):
        self.issue()

        res = self.as_user(self.seller).get(JOBS + self.q())

        self.assertEqual(res.status_code, 200)
        self.assertEqual([row['status'] for row in res.data['results']], ['pending'])
        self.assertNotIn('claim_token', str(res.data))


from store.tests import Ip1PosBase  # noqa: E402


class PosSaleQueuesItsTicketTest(Ip1PosBase):
    """
    Una venta en caja —también desde un teléfono— manda su ticket a la
    impresora del local. El navegador no abre ningún diálogo de impresión: la
    respuesta de la venta dice qué trabajo se encoló.
    """

    URL = '/api/v1/internal/ip1-tienda/sales/pos/sales/'

    def sell(self, **over):
        return self.client.post(self.URL, self.sale_body(**over), format='json')

    def test_the_sale_answers_with_the_job_it_queued(self):
        printer = Printer.objects.create(
            company=self.company, branch=self.branch_a, name='Caja', host='192.168.1.50')

        res = self.sell(receipt_type='sales_note')

        self.assertEqual(res.status_code, 201, res.data)
        job = PrintJob.objects.get()
        self.assertEqual(res.data['print_job'], {
            'id': job.pk, 'status': 'pending', 'printer': 'Caja', 'agent_online': False})
        self.assertEqual(
            (job.order_id, job.printer, job.branch, job.kind),
            (res.data['order_id'], printer, self.branch_a, PrintJob.Kind.SALES_NOTE_TICKET))

    def test_repeating_the_sale_request_queues_no_second_ticket(self):
        Printer.objects.create(
            company=self.company, branch=self.branch_a, name='Caja', host='192.168.1.50')

        first = self.sell(receipt_type='sales_note')
        again = self.sell(receipt_type='sales_note')

        self.assertEqual(again.data['order_id'], first.data['order_id'])
        self.assertEqual(PrintJob.objects.count(), 1)
        self.assertEqual(again.data['print_job']['id'], first.data['print_job']['id'])

    def test_a_branch_without_a_printer_answers_with_no_job(self):
        Printer.objects.create(
            company=self.company, branch=self.branch_b, name='Norte', host='192.168.2.50')

        res = self.sell(receipt_type='sales_note')

        self.assertEqual(res.status_code, 201, res.data)
        self.assertIsNone(res.data['print_job'])
        self.assertEqual(PrintJob.objects.count(), 0)


class ReviewHardeningApiTest(PrintApiBase):
    def test_only_addresses_of_a_local_network_are_accepted_whatever_their_form(self):
        from store.print_views import _local_host

        for host in ('192.168.1.50', '10.0.0.9', '172.16.4.2', '169.254.10.1', 'termica.local'):
            self.assertIsNotNone(_local_host(host), host)
        for host in ('8.8.8.8', '::ffff:8.8.8.8', '::ffff:127.0.0.1', '2002:808:808::1',
                     '127.0.0.1', '0.0.0.0', '::1', '3232235826', '0xC0A80132', 'a.b.local',
                     'localhost', '172.32.0.1'):
            self.assertIsNone(_local_host(host), host)

    def test_a_document_that_fails_to_render_is_retried_before_it_is_given_up(self):
        document = self.issue()
        document.signed_xml = ''
        document.save(update_fields=['signed_xml'])

        self.assertIsNone(self.as_agent().post(CLAIM).data['job'])

        job = PrintJob.objects.get()
        self.assertEqual((job.status, job.attempts), (PrintJob.Status.PENDING, 1))
        self.assertTrue(job.last_error)

    def test_the_claim_carries_the_stable_identifier_of_the_job(self):
        self.issue()

        job = self.as_agent().post(CLAIM).data['job']

        self.assertEqual(job['uid'], PrintJob.objects.get().uid.hex)

    def test_reusing_a_reprint_key_for_another_order_is_refused(self):
        first, second = self.issue(), self.issue()
        client = self.as_user(self.seller)
        client.post(JOBS + self.q(), {'order': first.order_id}, format='json',
                    HTTP_IDEMPOTENCY_KEY='clave-repetida')

        res = client.post(JOBS + self.q(), {'order': second.order_id}, format='json',
                          HTTP_IDEMPOTENCY_KEY='clave-repetida')

        self.assertEqual(res.status_code, 409)

    def test_deactivating_a_printer_from_the_panel_fails_its_waiting_jobs(self):
        self.issue()

        res = self.as_user(self.manager).delete(f'{PRINTERS}{self.printer.pk}/{self.q()}')

        self.assertEqual(res.status_code, 204)
        self.assertEqual(PrintJob.objects.get().status, PrintJob.Status.FAILED)


class PosSaysWhetherTheShopPrinterIsListeningTest(Ip1PosBase):
    URL = '/api/v1/internal/ip1-tienda/sales/pos/sales/'

    def test_the_sale_says_when_no_agent_is_connected(self):
        Printer.objects.create(
            company=self.company, branch=self.branch_a, name='Caja', host='192.168.1.50')

        res = self.client.post(self.URL, self.sale_body(receipt_type='sales_note'), format='json')

        self.assertEqual(res.data['print_job']['agent_online'], False)

    def test_the_sale_says_when_the_agent_is_listening(self):
        Printer.objects.create(
            company=self.company, branch=self.branch_a, name='Caja', host='192.168.1.50')
        agent, _token = printing.create_agent(
            company=self.company, branch=self.branch_a, name='Mostrador', actor=None)
        printing.claim_next(agent)

        res = self.client.post(self.URL, self.sale_body(receipt_type='sales_note'), format='json')

        self.assertEqual(res.data['print_job']['agent_online'], True)
