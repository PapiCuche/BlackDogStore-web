"""
PRINT-SETUP-01 — what a print agent's token is for, so the PC installer only asks for the token.

`GET /api/v1/print-agent/me/` answers, for the token's agent: its company, its branch and the active
printers of THAT branch (id, name, connection). Read-only: it never marks the agent online, never
reveals a token or a hash, and a token of another company or branch sees nothing of theirs.
"""
from rest_framework.test import APIClient

from store.models import Printer, PrintAgent
from store.printing import services as printing
from store.test_printing_api import PrintApiBase
from store.tests import _p3_company, _saas_branch

ME = '/api/v1/print-agent/me/'


class AgentSetupInfoTest(PrintApiBase):
    def setUp(self):
        super().setUp()
        self.usb = Printer.objects.create(company=self.company, branch=self.branch, name='USB caja',
                                          host='', connection='local_queue', auto_print=False)
        Printer.objects.create(company=self.company, branch=self.branch, name='Vieja', host='192.168.1.9',
                               is_active=False, auto_print=False)
        other_branch = _saas_branch(self.company, 'Otra sucursal')
        Printer.objects.create(company=self.company, branch=other_branch, name='De la otra', host='192.168.1.70')
        other = _p3_company('otra-setup', 'Otra Empresa')
        Printer.objects.create(company=other, branch=other.default_inventory_branch, name='Ajena',
                               host='192.168.1.80')

    def test_the_token_says_its_company_branch_and_printers(self):
        res = self.as_agent().get(ME)

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['company'], self.company.name)
        self.assertEqual(res.data['branch'], self.branch.name)
        self.assertEqual(res.data['agent'], 'Mostrador')
        self.assertEqual(
            [(p['id'], p['name'], p['connection']) for p in res.data['printers']],
            [(self.printer.pk, 'Caja', 'network'), (self.usb.pk, 'USB caja', 'local_queue')])

    def test_without_a_valid_token_nothing_is_said(self):
        for client in (APIClient(), self.as_agent('bdpa_no-existe'), self.as_agent('Bearer x')):
            self.assertEqual(client.get(ME).status_code, 401)
        self.assertEqual(self.as_user(self.manager).get(ME).status_code, 401)

    def test_a_revoked_agent_is_refused(self):
        printing.revoke_agent(self.agent, actor=None) if hasattr(printing, 'revoke_agent') else \
            PrintAgent.objects.filter(pk=self.agent.pk).update(is_active=False)

        self.assertEqual(self.as_agent().get(ME).status_code, 401)

    def test_asking_does_not_mark_the_agent_online_nor_reveal_secrets(self):
        res = self.as_agent().get(ME)

        self.agent.refresh_from_db()
        self.assertIsNone(self.agent.last_seen_at)
        body = str(res.data)
        self.assertNotIn(self.agent.token_hash, body)
        self.assertNotIn(self.token, body)

    def test_it_is_read_only(self):
        self.assertEqual(self.as_agent().post(ME, {}, format='json').status_code, 405)
