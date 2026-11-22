"""
CATALOG-F11 — normalise ONE company's device catalogue after an import (`catalog_normalize`).

    PLAN WRITES NOTHING. APPLY ONLY GIVES THE CAPACITY AND COLOUR ROLES AND WRITES CAPACITIES THE CATALOGUE WAY.

What these tests pin:

  * the plan names the attribute that the fallbacks of `configuration_resolver.roles_of` already read as capacity and
    as colour, and gives it that role only when nobody holds it (a role a company assigned is never moved);
  * a capacity option whose label reads as a size («128Gb», «1TB») is relabelled «128 GB» / «1 TB» and ordered by its
    size (`sort_order` = GB); a label that does not read as a size, or whose new label another option or an alias
    already holds, is reported and left alone;
  * apply goes through `device_catalog` (its checks and its audit, with company and actor), keeps `reference_key`,
    never renames a `Product` and never touches another company; a second apply changes nothing and writes no audit;
  * apply refuses a plan that changed since it was read (`expected` fingerprint);
  * the option and attribute audits now say what the value was before.

The data is invented; AGENTS §2 (the engine names no manufacturer).
"""
import io
from decimal import Decimal

from django.core.management import CommandError, call_command
from django.test import TestCase

from store import catalog_normalize as normalize
from store import device_catalog
from store.configuration_resolver import phrase_key
from store.models import AdminAuditLog, CatalogAlias, Product, ProductVariantOption, VariantAttribute, VariantOption
from store.tests import _p2d_member, _p3_company

OTHER = VariantAttribute.ROLE_OTHER


class _Shop:
    def build(self, slug):
        company = _p3_company(slug, f'Tienda {slug}')
        cap = VariantAttribute.objects.create(company=company, name='Capacidad', role=OTHER, sort_order=0)
        colour = VariantAttribute.objects.create(company=company, name='Color', role=OTHER, sort_order=0,
                                                 kind=VariantAttribute.KIND_COLOR)
        opts = {}
        for label, order, key in (('128Gb', 0, 'capacity:128gb'), ('256Gb', 1, ''), ('1TB', 3, 'capacity:1024gb'),
                                  ('2 TB', 2048, ''), ('16 GB', 16, '')):
            opts[label] = VariantOption.objects.create(company=company, attribute=cap, label=label, sort_order=order,
                                                       reference_key=key)
        opts['Negro'] = VariantOption.objects.create(company=company, attribute=colour, label='Negro',
                                                     swatch_hex='#111111')
        return company, cap, colour, opts


class PlanTest(_Shop, TestCase):
    def setUp(self):
        self.company, self.cap, self.colour, self.opts = self.build('norm-a')

    def test_the_plan_reads_roles_and_sizes_and_writes_nothing(self):
        before = (list(VariantOption.objects.values_list('pk', 'label', 'sort_order')),
                  list(VariantAttribute.objects.values_list('pk', 'role')), AdminAuditLog.objects.count())
        plan = normalize.plan(self.company)
        self.assertEqual([(r['attribute_id'], r['role'], r['status']) for r in plan['roles']],
                         [(self.cap.pk, 'capacity', 'assign'), (self.colour.pk, 'color', 'assign')])
        caps = {c['option_id']: c for c in plan['capacities']}
        self.assertEqual((caps[self.opts['128Gb'].pk]['label'], caps[self.opts['128Gb'].pk]['sort_order']), ('128 GB', 128))
        self.assertEqual((caps[self.opts['256Gb'].pk]['label'], caps[self.opts['256Gb'].pk]['sort_order']), ('256 GB', 256))
        self.assertEqual((caps[self.opts['1TB'].pk]['label'], caps[self.opts['1TB'].pk]['sort_order']), ('1 TB', 1024))
        self.assertEqual(caps[self.opts['2 TB'].pk]['status'], 'unchanged')
        self.assertEqual(caps[self.opts['16 GB'].pk]['status'], 'unchanged')
        self.assertEqual(plan['changes'], 2 + 3)
        self.assertTrue(plan['fingerprint'])
        self.assertEqual(before, (list(VariantOption.objects.values_list('pk', 'label', 'sort_order')),
                                  list(VariantAttribute.objects.values_list('pk', 'role')),
                                  AdminAuditLog.objects.count()))

    def test_a_role_somebody_holds_is_never_moved(self):
        VariantAttribute.objects.create(company=self.company, name='Almacenamiento', role='capacity')
        plan = normalize.plan(self.company)
        roles = {r['role']: r for r in plan['roles']}
        self.assertEqual(roles['capacity']['status'], 'held')
        self.assertEqual(plan['capacities'], [])  # the capacity attribute is the holder, which has no option here

    def test_two_capacity_names_are_ambiguous(self):
        VariantAttribute.objects.create(company=self.company, name='Almacenamiento', role=OTHER)
        roles = {r['role']: r for r in normalize.plan(self.company)['roles']}
        self.assertEqual(roles['capacity']['status'], 'ambiguous')

    def test_a_label_that_is_not_a_size_is_reported_and_left(self):
        odd = VariantOption.objects.create(company=self.company, attribute=self.cap, label='Grande', sort_order=9)
        caps = {c['option_id']: c for c in normalize.plan(self.company)['capacities']}
        self.assertEqual(caps[odd.pk]['status'], 'unreadable')

    def test_a_new_label_another_option_holds_is_a_conflict(self):
        VariantOption.objects.create(company=self.company, attribute=self.cap, label='256 GB', sort_order=256)
        caps = {c['option_id']: c for c in normalize.plan(self.company)['capacities']}
        self.assertEqual(caps[self.opts['256Gb'].pk]['status'], 'conflict')

    def test_a_new_label_an_alias_holds_is_a_conflict(self):
        CatalogAlias.objects.create(company=self.company, scope=CatalogAlias.SCOPE_OPTION, alias='128 GB',
                                    alias_key=phrase_key('128 GB'), option=self.opts['256Gb'])
        caps = {c['option_id']: c for c in normalize.plan(self.company)['capacities']}
        self.assertEqual(caps[self.opts['128Gb'].pk]['status'], 'conflict')

    def test_two_options_that_would_get_the_same_label_are_both_conflicts(self):
        twin = VariantOption.objects.create(company=self.company, attribute=self.cap, label='1024 GB', sort_order=50)
        caps = {c['option_id']: c for c in normalize.plan(self.company)['capacities']}
        self.assertEqual((caps[self.opts['1TB'].pk]['status'], caps[twin.pk]['status']), ('conflict', 'conflict'))
        self.assertIn('1 TB', caps[twin.pk]['reason'])

    def test_two_colour_attributes_are_ambiguous(self):
        VariantAttribute.objects.create(company=self.company, name='Acabado', role=OTHER, kind=VariantAttribute.KIND_COLOR)
        roles = {r['role']: r for r in normalize.plan(self.company)['roles']}
        self.assertEqual(roles['color']['status'], 'ambiguous')

    def test_the_options_of_a_capacity_attribute_already_held_are_normalised(self):
        VariantAttribute.objects.filter(pk=self.cap.pk).update(role='capacity')
        plan = normalize.plan(self.company)
        self.assertEqual({r['role']: r['status'] for r in plan['roles']}['capacity'], 'held')
        caps = {c['option_id']: c for c in plan['capacities']}
        self.assertEqual((caps[self.opts['128Gb'].pk]['status'], caps[self.opts['128Gb'].pk]['label']), ('change', '128 GB'))


class ApplyTest(_Shop, TestCase):
    def setUp(self):
        self.company, self.cap, self.colour, self.opts = self.build('norm-b')
        self.actor, _ = _p2d_member(self.company, 'norm_b_staff', ['company.view', 'products.view', 'products.manage'])
        self.other, self.other_cap, _, self.other_opts = self.build('norm-c')
        self.variant = Product.objects.create(company=self.company, name='Equipo X 128Gb Negro', slug='equipo-x-128',
                                              price=Decimal('100.00'))
        ProductVariantOption.objects.create(company=self.company, product=self.variant, attribute=self.cap,
                                            option=self.opts['128Gb'])

    def test_apply_writes_through_the_catalogue_service_with_audit(self):
        body = normalize.apply(self.company, actor=self.actor)
        self.assertEqual(body['changes'], 5)
        self.cap.refresh_from_db()
        self.colour.refresh_from_db()
        self.assertEqual((self.cap.role, self.colour.role), ('capacity', 'color'))
        self.assertEqual(list(VariantOption.objects.filter(attribute=self.cap).order_by('sort_order')
                              .values_list('label', 'sort_order', 'reference_key')),
                         [('16 GB', 16, ''), ('128 GB', 128, 'capacity:128gb'), ('256 GB', 256, ''),
                          ('1 TB', 1024, 'capacity:1024gb'), ('2 TB', 2048, '')])
        audit = AdminAuditLog.objects.filter(company=self.company)
        self.assertEqual(audit.filter(action='variant_attribute_updated').count(), 2)
        self.assertEqual(audit.filter(action='variant_option_updated').count(), 3)
        summary = audit.get(action=normalize.APPLIED)
        self.assertEqual(summary.actor, self.actor)
        self.assertEqual(summary.metadata['changes'], 5)
        self.assertEqual(set(audit.values_list('actor_id', flat=True)), {self.actor.pk})
        renamed = audit.filter(action='variant_option_updated', target_id=str(self.opts['128Gb'].pk)).get()
        self.assertEqual((renamed.metadata['previous']['label'], renamed.metadata['label']), ('128Gb', '128 GB'))
        self.assertEqual((renamed.metadata['previous']['sort_order'], renamed.metadata['sort_order']), (0, 128))

    def test_variants_and_other_companies_are_untouched(self):
        normalize.apply(self.company, actor=self.actor)
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.name, 'Equipo X 128Gb Negro')
        self.assertEqual(list(self.variant.variant_options.values_list('option_id', flat=True)), [self.opts['128Gb'].pk])
        self.assertEqual(list(VariantOption.objects.filter(company=self.other).order_by('pk').values_list('label', flat=True)),
                         ['128Gb', '256Gb', '1TB', '2 TB', '16 GB', 'Negro'])
        self.assertEqual(VariantAttribute.objects.filter(company=self.other, role=OTHER).count(), 2)
        self.assertFalse(AdminAuditLog.objects.filter(company=self.other).exists())

    def test_a_second_apply_changes_nothing_and_writes_nothing(self):
        normalize.apply(self.company, actor=self.actor)
        rows = AdminAuditLog.objects.count()
        body = normalize.apply(self.company, actor=self.actor)
        self.assertEqual(body['changes'], 0)
        self.assertEqual(AdminAuditLog.objects.count(), rows)
        self.assertEqual(normalize.plan(self.company)['changes'], 0)

    def test_a_stale_plan_is_refused_and_nothing_is_written(self):
        seen = normalize.plan(self.company)['fingerprint']
        self.opts['16 GB'].label = '16Gb'
        self.opts['16 GB'].save()
        with self.assertRaises(normalize.StalePlan):
            normalize.apply(self.company, actor=self.actor, expected=seen)
        self.cap.refresh_from_db()
        self.assertEqual(self.cap.role, OTHER)
        self.assertFalse(AdminAuditLog.objects.filter(company=self.company).exists())

    def test_a_refusal_in_the_middle_undoes_everything(self):
        from unittest import mock

        real = device_catalog.update_option
        calls = []

        def second_fails(option, **kwargs):
            calls.append(option.pk)
            if len(calls) == 2:
                raise device_catalog.CatalogError('Otra persona escribió en medio.', field='label')
            return real(option, **kwargs)

        with mock.patch.object(normalize.device_catalog, 'update_option', side_effect=second_fails):
            with self.assertRaises(normalize.StalePlan):
                normalize.apply(self.company, actor=self.actor)
        self.cap.refresh_from_db()
        self.opts['128Gb'].refresh_from_db()
        self.assertEqual((self.cap.role, self.opts['128Gb'].label), (OTHER, '128Gb'))
        self.assertFalse(AdminAuditLog.objects.filter(company=self.company).exists())

    def test_conflicts_are_skipped_the_rest_applies(self):
        VariantOption.objects.create(company=self.company, attribute=self.cap, label='256 GB', sort_order=256)
        body = normalize.apply(self.company, actor=self.actor)
        self.assertEqual(body['skipped'], 1)
        self.opts['256Gb'].refresh_from_db()
        self.assertEqual(self.opts['256Gb'].label, '256Gb')


class CommandTest(_Shop, TestCase):
    def setUp(self):
        self.company, self.cap, self.colour, self.opts = self.build('norm-d')
        self.actor, _ = _p2d_member(self.company, 'norm_d_staff', ['company.view', 'products.view', 'products.manage'])
        self.viewer, _ = _p2d_member(self.company, 'norm_d_view', ['company.view', 'products.view'])

    def run_command(self, *args):
        out = io.StringIO()
        call_command('normalize_device_catalog', '--company', self.company.slug, *args, stdout=out)
        return out.getvalue()

    def test_the_default_is_a_dry_run(self):
        text = self.run_command()
        self.assertIn('simulación', text)
        self.assertIn('128Gb → 128 GB', text)
        self.cap.refresh_from_db()
        self.assertEqual(self.cap.role, OTHER)
        self.assertFalse(AdminAuditLog.objects.exists())

    def test_apply_needs_an_actor_who_may_manage_products(self):
        with self.assertRaises(CommandError):
            self.run_command('--apply')
        with self.assertRaises(CommandError):
            self.run_command('--apply', '--actor', str(self.viewer.pk))
        self.assertFalse(AdminAuditLog.objects.exists())

    def test_apply_needs_the_fingerprint_of_the_simulation(self):
        with self.assertRaises(CommandError):
            self.run_command('--apply', '--actor', str(self.actor.pk))
        self.assertFalse(AdminAuditLog.objects.exists())

    def test_apply_with_the_fingerprint_it_was_shown(self):
        fingerprint = normalize.plan(self.company)['fingerprint']
        text = self.run_command('--apply', '--actor', str(self.actor.pk), '--expect', fingerprint)
        self.assertIn('5 cambios', text)
        self.assertEqual(normalize.plan(self.company)['changes'], 0)

    def test_a_wrong_fingerprint_stops_the_command(self):
        with self.assertRaises(CommandError):
            self.run_command('--apply', '--actor', str(self.actor.pk), '--expect', 'x' * 16)
        self.assertFalse(AdminAuditLog.objects.exists())


class ServiceAuditTest(_Shop, TestCase):
    def setUp(self):
        self.company, self.cap, self.colour, self.opts = self.build('norm-e')

    def test_the_option_audit_says_what_it_was(self):
        device_catalog.update_option(self.opts['16 GB'], sort_order=17)
        row = AdminAuditLog.objects.get(action='variant_option_updated')
        self.assertEqual(row.metadata['previous'], {'label': '16 GB', 'sort_order': 16, 'swatch_hex': ''})
        self.assertEqual(row.metadata['sort_order'], 17)

    def test_a_negative_option_order_is_refused(self):
        for bad in (-1, '--5', '²', '1.5', True, 2_147_483_648):
            with self.assertRaises(device_catalog.CatalogError, msg=repr(bad)):
                device_catalog.update_option(self.opts['16 GB'], sort_order=bad)
        with self.assertRaises(device_catalog.CatalogError):
            device_catalog.create_option(company=self.company, attribute_id=self.cap.pk, label='4 TB', sort_order=-1)
        self.opts['16 GB'].refresh_from_db()
        self.assertEqual(self.opts['16 GB'].sort_order, 16)

    def test_the_attribute_audit_says_what_it_was(self):
        device_catalog.update_attribute(self.cap, role='capacity')
        row = AdminAuditLog.objects.get(action='variant_attribute_updated')
        self.assertEqual(row.metadata['previous'], {'name': 'Capacidad', 'role': OTHER, 'sort_order': 0})
