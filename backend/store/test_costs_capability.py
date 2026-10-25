"""
DEVICE-COMMERCE-01 · DC1 — `costs.view`, la autoridad de ver lo que costó algo.

Decisión del propietario (2026-10-14): ver costos y márgenes es una capacidad
propia, que reciben por defecto los roles de dueño/administración de la empresa
y nadie más. Mover inventario (`inventory.adjust`) NO la implica: quien pone un
equipo en la vitrina no por eso ve cuánto costó.
"""
from importlib import import_module

from django.apps import apps
from django.core.cache import cache
from django.test import TestCase

from store.capabilities import ASSIGNABLE_CAPABILITY_CODES, CAPABILITIES, STATUS_ACTIVE
from store.company_provisioning import PRESET_ROLES, provision_company_access_defaults
from store.models import UserProfile
from store.tenancy import LEGACY_ROLE_CAPABILITIES
from store.tests import _saas_company

CAP = 'costs.view'
MIGRATION = 'store.migrations.0126_costs_view_capability'


class CostsViewCatalogueTest(TestCase):
    def test_it_is_an_active_assignable_capability(self):
        self.assertIn(CAP, CAPABILITIES)
        self.assertEqual(CAPABILITIES[CAP].status, STATUS_ACTIVE)
        self.assertIn(CAP, ASSIGNABLE_CAPABILITY_CODES)

    def test_only_the_administration_preset_holds_it(self):
        holders = {slug for _name, slug, _desc, caps in PRESET_ROLES if CAP in caps}
        self.assertEqual(holders, {'administrador'})

    def test_the_legacy_bridge_gives_it_to_admin_and_superadmin_only(self):
        holders = {role for role, caps in LEGACY_ROLE_CAPABILITIES.items() if CAP in caps}
        self.assertEqual(holders, {UserProfile.ROLE_ADMIN, UserProfile.ROLE_SUPERADMIN})


class CostsViewMigrationTest(TestCase):
    def setUp(self):
        cache.clear()
        self.company = _saas_company('Costos', 'dc1-costos', tax_id='20900001261')
        provision_company_access_defaults(self.company)
        self.module = import_module(MIGRATION)

    def caps(self, slug):
        return set(self.company.roles.get(slug=slug).capabilities)

    def test_an_untouched_administrador_receives_it_and_nobody_else(self):
        self.company.roles.filter(slug='administrador').update(
            capabilities=sorted(self.module.ADMIN_PREVIOUS))
        inventory = self.caps('inventario')

        self.module.grant(apps, None)

        self.assertEqual(self.caps('administrador'), self.module.ADMIN_PREVIOUS | {CAP})
        self.assertEqual(self.caps('inventario'), inventory)

    def test_an_edited_administrador_is_left_alone(self):
        edited = sorted(self.module.ADMIN_PREVIOUS - {'reports.view'})
        self.company.roles.filter(slug='administrador').update(capabilities=edited)

        self.module.grant(apps, None)

        self.assertEqual(sorted(self.caps('administrador')), edited)

    def test_the_frozen_set_chains_from_the_previous_node_and_ends_at_the_catalogue(self):
        before = import_module('store.migrations.0104_grant_quote_decision')
        self.assertEqual(
            before.ADMIN_PREVIOUS | set(before.NEW_CAPABILITIES), self.module.ADMIN_PREVIOUS)
        # MIGRATION-C: 0148 then gives `inventory.counts.approve` to every role
        # holding `inventory.adjust` — Administrador among them.
        after = import_module('store.migrations.0148_inventory_counts_approve_capability')
        self.assertEqual(
            self.module.ADMIN_PREVIOUS | set(self.module.NEW_CAPABILITIES) | {after.NEW_CAPABILITY},
            frozenset(ASSIGNABLE_CAPABILITY_CODES),
        )


class CostsViewReportCommandTest(TestCase):
    """DC1 review — after `migrate`, who still has nobody able to see costs. Read-only, counts only."""

    def test_counts_the_companies_whose_administrator_lacks_it(self):
        import io

        from django.core.management import call_command
        from store.models import Company, CompanyRole

        def company(slug, caps, *, role_slug='administrador'):
            row = Company.objects.create(name=slug, legal_name=f'{slug} SAC', tax_id='20' + str(abs(hash(slug)))[:9],
                                         slug=slug)
            CompanyRole.objects.filter(company=row).delete()
            if caps is not None:
                CompanyRole.objects.create(company=row, name='Administrador', slug=role_slug, capabilities=caps)
            return row

        company('dc1-rep-ok', ['company.view', 'costs.view'])
        company('dc1-rep-edited', ['company.view', 'products.view'])
        company('dc1-rep-noadmin', None)
        company('dc1-rep-other', ['costs.view'], role_slug='gerencia')
        before = sorted(CompanyRole.objects.values_list('pk', 'capabilities'))

        out = io.StringIO()
        call_command('costs_view_report', stdout=out)
        text = out.getvalue()
        self.assertIn('Rol «administrador» sin costs.view: 1', text)
        self.assertIn('Sin rol «administrador»: 2', text)
        self.assertIn('Sin ningún rol activo con costs.view: 2', text)
        # Counts only, and nothing changed.
        self.assertNotIn('dc1-rep', text)
        self.assertEqual(sorted(CompanyRole.objects.values_list('pk', 'capabilities')), before)
