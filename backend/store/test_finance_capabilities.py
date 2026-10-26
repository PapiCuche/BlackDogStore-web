"""
FIN-B1 — the finance capabilities, who receives them, and the migration chain
the preset tests walk (docs/adr-finance-obligations.md §8.2, owner's P3).
"""
import importlib

from django.test import TestCase

from store import finance_test_support as fx
from store.capabilities import (
    ASSIGNABLE_CAPABILITY_CODES, CAPABILITIES, DEFAULT_ADMIN_CAPABILITY_CODES, EXPLICIT_GRANT_CODES,
    STATUS_ACTIVE,
)

DEFAULTS = {'finance.view', 'finance.manage', 'finance.payments.record'}
EXPLICIT = {
    'finance.payments.reverse', 'finance.adjust', 'finance.void', 'finance.dispute',
    'finance.receivables.manual', 'finance.export', 'finance.messages.prepare',
    'finance.templates.manage', 'finance.automation.manage',
}


def finance_migration():
    return importlib.import_module('store.migrations.0157_finance_capabilities')


class CatalogueTest(TestCase):
    def test_finance_codes_are_active_and_assignable(self):
        for code in DEFAULTS | EXPLICIT:
            self.assertEqual(CAPABILITIES[code].status, STATUS_ACTIVE, code)
            self.assertEqual(CAPABILITIES[code].module, 'finance', code)
            self.assertIn(code, ASSIGNABLE_CAPABILITY_CODES)

    def test_the_explicit_ones_are_exactly_the_sensitive_ones(self):
        self.assertEqual(set(EXPLICIT_GRANT_CODES), EXPLICIT)
        self.assertEqual(DEFAULT_ADMIN_CAPABILITY_CODES, ASSIGNABLE_CAPABILITY_CODES - EXPLICIT_GRANT_CODES)

    def test_a_new_company_administrator_gets_only_the_defaults(self):
        company = fx.company('fin-caps', 'Capacidades')
        admin = set(company.roles.get(slug='administrador').capabilities)
        self.assertTrue(DEFAULTS <= admin)
        self.assertFalse(EXPLICIT & admin)


class MigrationTest(TestCase):
    def setUp(self):
        self.module = finance_migration()
        self.company = fx.company('fin-mig', 'Migración')

    def caps(self, slug):
        return set(self.company.roles.get(slug=slug).capabilities)

    def run_grant(self):
        from django.apps import apps

        self.module.grant(apps, None)

    def test_grants_the_defaults_to_holders_of_company_manage_only(self):
        roles = self.company.roles
        roles.filter(slug='administrador').update(capabilities=sorted(self.module.ADMIN_PREVIOUS))
        ventas_before = self.caps('ventas')
        self.run_grant()
        self.assertEqual(self.caps('administrador'), set(self.module.ADMIN_PREVIOUS) | DEFAULTS)
        self.assertEqual(self.caps('ventas'), ventas_before)
        self.assertFalse(EXPLICIT & self.caps('administrador'))

    def test_the_frozen_set_continues_the_chain_and_ends_at_the_preset(self):
        import inspect

        from store.test_unit_codes import _label_capabilities_migration

        labels = _label_capabilities_migration()
        self.assertEqual(labels.ADMIN_PREVIOUS | set(labels.NEW_CAPABILITIES), self.module.ADMIN_PREVIOUS)
        self.assertEqual(self.module.ADMIN_PREVIOUS | set(self.module.NEW_CAPABILITIES),
                         DEFAULT_ADMIN_CAPABILITY_CODES)
        self.assertNotIn('from store.capabilities import', inspect.getsource(self.module))
