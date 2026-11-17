"""
Impresoras del equipo del agente (USB) — WIN-003C, PRINT-USB-MODEL.

  · UNA USB NO LLEVA IP. `local_queue` no tiene dirección y una de red siempre
    la tiene; lo exige la base, no sólo la API.
  · CADA AGENTE, LO QUE SABE IMPRIMIR. El agente declara al pedir trabajo qué
    conexiones atiende y, si quiere, qué impresoras. Eso SÓLO filtra dentro de
    la empresa y la sucursal de su token. Un agente anterior (que no declara
    nada) recibe sólo impresoras de red, como siempre. Lo que no es para un
    agente sigue en la cola sin gastar intentos.
  · LO INCIERTO NO VUELVE SOLO. Un trabajo que pudo salir se da por fallido de
    inmediato; reenviarlo exige confirmar que NO salió, queda auditado y se
    reenvía con otro identificador.
"""
import base64

from django.db import IntegrityError, transaction
from django.test import SimpleTestCase

from store.models import AdminAuditLog, Printer, PrintJob
from store.printing import services as printing
from store.test_printing_api import AGENTS, CLAIM, JOBS, PRINTERS, PrintApiBase, result_url
from store.tests import _p2d_member, _p3_company, _saas_branch


class UsbModelBase(PrintApiBase):
    def setUp(self):
        super().setUp()
        # La de red existente no recibe los automáticos: cada prueba elige.
        self.printer.auto_print = False
        self.printer.save(update_fields=['auto_print'])
        self.usb = Printer.objects.create(
            company=self.company, branch=self.branch, name='USB caja', host='',
            connection=Printer.Connection.LOCAL_QUEUE, auto_print=False)

    def job_for(self, printer, key):
        document = getattr(self, '_document', None) or self.issue()
        self._document = document
        return printing.enqueue_manual(order=document.order, printer=printer, key=key, actor=None)

    def claim(self, body=None, token=None):
        return self.as_agent(token).post(CLAIM, body if body is not None else {}, format='json')


class ModelTest(UsbModelBase):
    def test_a_usb_printer_has_no_address_and_a_network_one_always_has(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Printer.objects.create(company=self.company, branch=self.branch, name='USB con IP',
                                   host='192.168.1.9', connection='local_queue')
        with self.assertRaises(IntegrityError), transaction.atomic():
            Printer.objects.create(company=self.company, branch=self.branch, name='Red sin IP',
                                   host='', connection='network')

    def test_existing_printers_stay_network(self):
        self.assertEqual(self.printer.connection, Printer.Connection.NETWORK)
        self.assertEqual(Printer._meta.get_field('connection').default, 'network')

    def test_the_reverse_migration_refuses_while_usb_printers_exist(self):
        import importlib

        from django.apps import apps

        migration = importlib.import_module('store.migrations.0172_printer_local_queue')
        with self.assertRaises(RuntimeError):
            migration.refuse_reverse_with_local_printers(apps, None)
        Printer.objects.filter(connection='local_queue').delete()
        migration.refuse_reverse_with_local_printers(apps, None)


class CapabilityTest(UsbModelBase):
    def test_an_old_agent_gets_network_jobs_as_always(self):
        job = self.job_for(self.printer, 'red-1')

        res = self.claim()

        self.assertEqual(res.data['job']['id'], job.pk)
        self.assertEqual(res.data['job']['printer']['connection'], 'network')

    def test_an_old_agent_never_gets_a_usb_job_and_burns_no_attempt(self):
        job = self.job_for(self.usb, 'usb-1')

        for _ in range(3):
            self.assertIsNone(self.claim().data['job'])

        job.refresh_from_db()
        self.assertEqual((job.status, job.attempts), (PrintJob.Status.PENDING, 0))

    def test_a_new_network_agent_gets_network_jobs(self):
        job = self.job_for(self.printer, 'red-2')

        res = self.claim({'connections': ['network'], 'printers': [self.printer.pk]})

        self.assertEqual(res.data['job']['id'], job.pk)

    def test_a_windows_agent_gets_its_usb_job_with_a_stable_id_and_no_address(self):
        job = self.job_for(self.usb, 'usb-2')

        res = self.claim({'connections': ['local_queue'], 'printers': [self.usb.pk]})

        printer = res.data['job']['printer']
        self.assertEqual(res.data['job']['id'], job.pk)
        self.assertEqual((printer['id'], printer['connection'], printer['host']),
                         (self.usb.pk, 'local_queue', ''))
        self.assertTrue(base64.b64decode(res.data['job']['payload_base64']).startswith(b'\x1b@'))
        self.agent.refresh_from_db()
        self.assertEqual(self.agent.connections, ('local_queue',))

    def test_two_agents_with_different_capabilities_share_a_branch(self):
        network_job = self.job_for(self.printer, 'red-3')
        usb_job = self.job_for(self.usb, 'usb-3')
        _windows, windows_token = printing.create_agent(
            company=self.company, branch=self.branch, name='PC USB', actor=None)

        usb = self.claim({'connections': ['local_queue']}, token=windows_token).data['job']
        network = self.claim().data['job']

        self.assertEqual((usb['id'], network['id']), (usb_job.pk, network_job.pk))
        self.assertIsNone(self.claim({'connections': ['local_queue']}, token=windows_token).data['job'])
        self.assertIsNone(self.claim().data['job'])

    def test_naming_printers_only_narrows_inside_the_branch(self):
        self.job_for(self.usb, 'usb-4')
        other_usb = Printer.objects.create(
            company=self.company, branch=self.branch, name='USB 2', host='',
            connection='local_queue', auto_print=False)

        res = self.claim({'connections': ['local_queue'], 'printers': [other_usb.pk]})

        self.assertIsNone(res.data['job'])
        self.assertEqual(PrintJob.objects.get(idempotency_key='manual:usb-4').attempts, 0)

    def test_a_printer_id_of_another_branch_or_company_gives_nothing(self):
        job = self.job_for(self.usb, 'usb-5')
        other_branch = _saas_branch(self.company, 'Sucursal Norte USB')
        _norte, norte_token = printing.create_agent(
            company=self.company, branch=other_branch, name='Norte', actor=None)
        other = _p3_company('usb-ajena', 'Ajena')
        _foreign, foreign_token = printing.create_agent(
            company=other, branch=other.default_inventory_branch, name='Ajena', actor=None)

        for token in (norte_token, foreign_token):
            res = self.claim({'connections': ['local_queue', 'network'], 'printers': [self.usb.pk]},
                             token=token)
            self.assertIsNone(res.data['job'])
        job.refresh_from_db()
        self.assertEqual((job.status, job.attempts), (PrintJob.Status.PENDING, 0))

    def test_an_invalid_declaration_is_refused_without_delivering(self):
        self.job_for(self.usb, 'usb-6')
        for body in (
            {'connections': 'local_queue'}, {'connections': []}, {'connections': ['usb']},
            {'connections': ['local_queue', 5]}, {'connections': ['network'] * 3},
            {'connections': ['local_queue'], 'printers': 'todas'},
            {'connections': ['local_queue'], 'printers': [0]},
            {'connections': ['local_queue'], 'printers': [True]},
            {'connections': ['local_queue'], 'printers': ['1']},
            {'connections': ['local_queue'], 'printers': list(range(1, 60))},
            # Re-revisión P3-1 y P3-2: vacía no es «todas»; fuera de bigint, no.
            {'connections': ['local_queue'], 'printers': []},
            {'connections': ['local_queue'], 'printers': [2 ** 63]},
        ):
            with self.subTest(body=body):
                self.assertEqual(self.claim(body).status_code, 400)
        self.assertEqual(PrintJob.objects.get(idempotency_key='manual:usb-6').attempts, 0)

    def test_a_company_or_branch_in_the_body_is_not_authority(self):
        other = _p3_company('usb-ajena-2', 'Ajena 2')
        self.job_for(self.usb, 'usb-7')

        res = self.claim({'connections': ['local_queue'], 'company': other.pk, 'branch': 999999})

        self.assertEqual(res.data['job']['printer']['id'], self.usb.pk)

    def test_a_deactivated_usb_printer_gives_nothing_and_fails_its_jobs(self):
        job = self.job_for(self.usb, 'usb-8')

        printing.deactivate_printer(self.usb)

        self.assertIsNone(self.claim({'connections': ['local_queue']}).data['job'])
        job.refresh_from_db()
        self.assertEqual(job.status, PrintJob.Status.FAILED)

    def test_online_is_per_printer_when_the_agent_named_its_printers(self):
        # Revisión P3: la USB de otra PC no aparece «con agente» porque haya uno
        # en línea que atiende otra USB.
        other_usb = Printer.objects.create(
            company=self.company, branch=self.branch, name='USB otra PC', host='',
            connection='local_queue', auto_print=False)

        self.claim({'connections': ['local_queue'], 'printers': [self.usb.pk]})

        self.assertTrue(printing.agent_online(self.branch, printer=self.usb))
        self.assertFalse(printing.agent_online(self.branch, printer=other_usb))
        listed = {p['id']: p['agent_online'] for p in
                  self.as_user(self.manager).get(PRINTERS + self.q()).data['results']}
        self.assertEqual((listed[self.usb.pk], listed[other_usb.pk]), (True, False))

    def test_the_forward_migration_stops_on_a_network_printer_without_address(self):
        import importlib

        from django.apps import apps

        migration = importlib.import_module('store.migrations.0172_printer_local_queue')
        migration.require_addresses_on_network_printers(apps, None)       # hoy, ninguna
        with self.assertRaises(IntegrityError), transaction.atomic():
            Printer.objects.filter(pk=self.printer.pk).update(host='')     # la base ya lo impide

    def test_the_till_knows_whether_a_compatible_agent_listens(self):
        self.claim()                                          # agente de red, en línea

        self.assertTrue(printing.agent_online(self.branch, 'network'))
        self.assertFalse(printing.agent_online(self.branch, 'local_queue'))
        self.claim({'connections': ['local_queue', 'network']})
        self.assertTrue(printing.agent_online(self.branch, 'local_queue'))


class UncertainOnTheServerTest(UsbModelBase):
    def deliver(self, job_key):
        job = self.job_for(self.usb, job_key)
        claimed = self.claim({'connections': ['local_queue']}).data['job']
        return job, claimed

    def test_an_uncertain_result_fails_at_once_and_is_never_offered_again(self):
        job, claimed = self.deliver('inc-1')

        res = self.as_agent().post(result_url(job.pk), {
            'claim_token': claimed['claim_token'], 'ok': False,
            'error': 'no consta', 'outcome': 'queued_not_printed'}, format='json')

        self.assertEqual(res.data['status'], PrintJob.Status.FAILED)
        job.refresh_from_db()
        self.assertTrue(job.last_error.startswith('INCIERTO'))
        self.assertEqual(job.attempts, 1)
        self.assertIsNone(self.claim({'connections': ['local_queue']}).data['job'])

    def test_the_text_alone_does_not_make_a_job_uncertain(self):
        job, claimed = self.deliver('inc-2')

        self.as_agent().post(result_url(job.pk), {
            'claim_token': claimed['claim_token'], 'ok': False,
            'error': 'INCIERTO: falso', 'outcome': 'otra-cosa'}, format='json')

        job.refresh_from_db()
        self.assertEqual(job.status, PrintJob.Status.PENDING)
        self.assertFalse(job.last_error.startswith('INCIERTO'))

    def test_resending_an_uncertain_job_needs_confirmation_and_gets_a_new_identity(self):
        job, claimed = self.deliver('inc-3')
        self.as_agent().post(result_url(job.pk), {
            'claim_token': claimed['claim_token'], 'ok': False, 'error': 'no consta',
            'outcome': 'queued_not_printed'}, format='json')
        listed = self.as_user(self.seller).get(JOBS + self.q()).data['results'][0]
        self.assertTrue(listed['uncertain'])
        old_uid = job.uid

        blind = self.as_user(self.seller).post(f'{JOBS}{job.pk}/retry/{self.q()}', {}, format='json')
        self.assertEqual(blind.status_code, 400)
        confirmed = self.as_user(self.seller).post(
            f'{JOBS}{job.pk}/retry/{self.q()}', {'confirm_not_printed': True}, format='json')

        self.assertEqual(confirmed.status_code, 200, confirmed.data)
        job.refresh_from_db()
        self.assertEqual(job.status, PrintJob.Status.PENDING)
        self.assertNotEqual(job.uid, old_uid)
        log = AdminAuditLog.objects.filter(action='print_job_retried').last()
        self.assertEqual((log.actor, log.company, log.metadata['confirmed_not_printed']),
                         (self.seller, self.company, True))
        again = self.claim({'connections': ['local_queue']}).data['job']
        self.assertEqual(again['uid'], job.uid.hex)

    def test_two_confirmations_at_once_resend_only_once(self):
        # Revisión P2: con dos clics a la vez y una copia vieja del trabajo, el
        # segundo reenvío no puede volver a cambiar el uid (el agente imprimiría
        # dos veces).
        job, claimed = self.deliver('inc-5')
        self.as_agent().post(result_url(job.pk), {
            'claim_token': claimed['claim_token'], 'ok': False, 'error': 'no consta',
            'outcome': 'queued_not_printed'}, format='json')
        stale_a = PrintJob.objects.get(pk=job.pk)
        stale_b = PrintJob.objects.get(pk=job.pk)

        first = printing.retry(stale_a, actor=self.seller, confirm_not_printed=True)
        with self.assertRaises(printing.PrintError):
            printing.retry(stale_b, actor=self.seller, confirm_not_printed=True)

        job.refresh_from_db()
        self.assertEqual(job.uid, first.uid)
        self.assertEqual(job.status, PrintJob.Status.PENDING)

    def make_uncertain(self, key):
        job, claimed = self.deliver(key)
        self.as_agent().post(result_url(job.pk), {
            'claim_token': claimed['claim_token'], 'ok': False, 'error': 'no consta',
            'outcome': 'queued_not_printed'}, format='json')
        job.refresh_from_db()
        return job

    def test_a_late_confirmation_of_an_already_resent_ticket_is_refused(self):
        # Re-revisión S-1: lo que se confirma es el ticket que se vio.
        job = self.make_uncertain('inc-7')
        seen = self.as_user(self.seller).get(JOBS + self.q()).data['results'][0]['uid']
        url = f'{JOBS}{job.pk}/retry/{self.q()}'
        first = self.as_user(self.seller).post(
            url, {'confirm_not_printed': True, 'expected_uid': seen}, format='json')
        self.assertEqual(first.status_code, 200)
        # Se reenvía, vuelve a quedar incierto; llega la confirmación vieja.
        claimed = self.claim({'connections': ['local_queue']}).data['job']
        self.as_agent().post(result_url(job.pk), {
            'claim_token': claimed['claim_token'], 'ok': False, 'error': 'otra vez',
            'outcome': 'queued_not_printed'}, format='json')
        job.refresh_from_db()
        uid_now = job.uid

        late = self.as_user(self.seller).post(
            url, {'confirm_not_printed': True, 'expected_uid': seen}, format='json')

        self.assertEqual(late.status_code, 409)
        job.refresh_from_db()
        self.assertEqual((job.uid, job.status), (uid_now, PrintJob.Status.FAILED))

    def test_the_audit_says_what_the_locked_row_was(self):
        # Re-revisión P3-3: la vista leyó el trabajo cuando aún no era incierto.
        job = self.make_uncertain('inc-8')
        stale = PrintJob.objects.get(pk=job.pk)
        stale.uncertain = False

        resent = printing.retry(stale, actor=self.seller, confirm_not_printed=True)

        self.assertTrue(resent.was_uncertain)

    def test_a_job_that_vanished_before_the_lock_answers_404(self):
        job = self.make_uncertain('inc-9')
        ghost = PrintJob.objects.get(pk=job.pk)
        ghost.pk = 999999999

        with self.assertRaises(printing.PrintNotFound):
            printing.retry(ghost, actor=self.seller, confirm_not_printed=True)

    def test_uncertainty_is_a_field_set_only_by_the_declared_outcome(self):
        job, claimed = self.deliver('inc-6')
        self.as_agent().post(result_url(job.pk), {
            'claim_token': claimed['claim_token'], 'ok': False,
            'error': 'INCIERTO: falso', 'outcome': 'refused'}, format='json')

        job.refresh_from_db()
        self.assertEqual((job.status, job.uncertain), (PrintJob.Status.FAILED, False))
        self.assertFalse(printing.is_uncertain(job))

    def test_a_plain_failure_still_resends_without_confirmation(self):
        job, claimed = self.deliver('inc-4')
        PrintJob.objects.filter(pk=job.pk).update(status='failed', last_error='La cola está en pausa.')

        res = self.as_user(self.seller).post(f'{JOBS}{job.pk}/retry/{self.q()}', {}, format='json')

        self.assertEqual(res.status_code, 200)
        job.refresh_from_db()
        self.assertEqual(job.uid.hex, claimed['uid'])


class PanelTest(UsbModelBase):
    def post_printer(self, **data):
        body = {'branch': self.branch.pk, 'name': 'Nueva', 'auto_print': False, **data}
        return self.as_user(self.manager).post(PRINTERS + self.q(), body, format='json')

    def test_a_usb_printer_is_registered_without_any_address(self):
        res = self.post_printer(connection='local_queue')

        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual((res.data['connection'], res.data['host']), ('local_queue', ''))
        log = AdminAuditLog.objects.filter(action='printer_created').last()
        self.assertEqual((log.company, log.metadata['connection']), (self.company, 'local_queue'))

    def test_a_usb_printer_refuses_an_address(self):
        res = self.post_printer(connection='local_queue', host='192.168.1.70')

        self.assertEqual(res.status_code, 400)
        self.assertIn('host', res.data)

    def test_a_network_printer_still_needs_a_local_address(self):
        for host in ('', '8.8.8.8', 'impresora.example.com'):
            with self.subTest(host=host):
                self.assertEqual(self.post_printer(host=host).status_code, 400)
        self.assertEqual(self.post_printer(host='192.168.1.71').data['connection'], 'network')

    def test_an_unknown_connection_is_refused(self):
        self.assertEqual(self.post_printer(connection='bluetooth').status_code, 400)

    def test_switching_a_printer_between_network_and_usb(self):
        url = f'{PRINTERS}{self.printer.pk}/{self.q()}'
        manager = self.as_user(self.manager)

        to_usb = manager.patch(url, {'connection': 'local_queue'}, format='json')
        self.assertEqual((to_usb.status_code, to_usb.data['host']), (200, ''))
        back = manager.patch(url, {'connection': 'network'}, format='json')
        self.assertEqual(back.status_code, 400)
        back = manager.patch(url, {'connection': 'network', 'host': '192.168.1.80'}, format='json')
        self.assertEqual((back.status_code, back.data['host']), (200, '192.168.1.80'))

    def test_editing_an_existing_network_printer_keeps_its_address(self):
        res = self.as_user(self.manager).patch(
            f'{PRINTERS}{self.printer.pk}/{self.q()}', {'name': 'Caja 1'}, format='json')

        self.assertEqual((res.status_code, res.data['host'], res.data['connection']),
                         (200, '192.168.1.50', 'network'))

    def test_another_company_cannot_touch_a_usb_printer(self):
        other = _p3_company('usb-panel-ajena', 'Ajena panel')
        stranger, _ = _p2d_member(other, 'usb_ajeno', ['company.view', 'company.manage'])

        res = self.as_user(stranger).patch(
            f'{PRINTERS}{self.usb.pk}/?company={other.pk}', {'name': 'mía'}, format='json')

        self.assertEqual(res.status_code, 404)

    def test_the_panel_says_which_agent_is_online_and_what_it_serves(self):
        self.claim({'connections': ['local_queue']})

        agents = self.as_user(self.manager).get(AGENTS + self.q()).data['results']
        printers = {p['id']: p for p in self.as_user(self.manager).get(PRINTERS + self.q()).data['results']}

        self.assertEqual((agents[0]['online'], agents[0]['connections']), (True, ['local_queue']))
        self.assertNotIn('token', agents[0])
        self.assertTrue(printers[self.usb.pk]['agent_online'])
        self.assertFalse(printers[self.printer.pk]['agent_online'])


class ParseClaimTest(SimpleTestCase):
    def test_what_an_agent_may_declare(self):
        self.assertEqual(printing.parse_claim({}), (('network',), None))
        self.assertEqual(printing.parse_claim(None), (('network',), None))
        self.assertEqual(printing.parse_claim({'connections': ['local_queue', 'local_queue']}),
                         (('local_queue',), None))
        self.assertEqual(printing.parse_claim({'connections': ['network'], 'printers': [3, 3, 4]}),
                         (('network',), (3, 4)))
