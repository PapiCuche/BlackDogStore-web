"""
R13 rehearsal finding: reversing 0192 → 0186 stopped halfway at 0189 (CheckViolation on
`print_job_target_matches_kind`) whenever a `sales_quote_ticket` print job existed, after 0192–0190 had already
been undone. Every node from 0189 to the leaf (0192) now ends with a guard that refuses the reverse BEFORE anything
is undone and names the DELETE to run.
"""
from importlib import import_module
from unittest import mock

from django.db import migrations
from django.test import SimpleTestCase

NODES = ('0199_fiscal_submission_unknown', '0198_trade_in_quote', '0197_trade_in_assessment', '0196_trade_in_eligibility', '0195_trade_in_rules', '0194_trade_in_tariff', '0193_unit_import_terms_approval', '0192_unit_photo_provenance', '0191_trade_in_quote',
         '0190_supplier_margin', '0189_sales_quote')


def _module(node):
    return import_module(f'store.migrations.{node}')


def _apps_with(tickets):
    PrintJob = mock.Mock()
    PrintJob.objects.filter.return_value.count.return_value = tickets
    apps = mock.Mock()
    apps.get_model.return_value = PrintJob
    return apps, PrintJob


class ReverseGuardIsTheLastOperationTest(SimpleTestCase):
    def test_the_guard_runs_first_on_reverse(self):
        for node in NODES:
            with self.subTest(node=node):
                module = _module(node)
                last = module.Migration.operations[-1]
                self.assertIsInstance(last, migrations.RunPython)
                self.assertIs(last.code, migrations.RunPython.noop)
                self.assertIs(last.reverse_code, module.refuse_reverse_with_quote_tickets)


class ReverseGuardRefusesQuoteTicketsTest(SimpleTestCase):
    def test_refuses_while_any_quote_ticket_exists_and_names_the_fix(self):
        for node in NODES:
            with self.subTest(node=node):
                apps, PrintJob = _apps_with(2)
                with self.assertRaises(RuntimeError) as caught:
                    _module(node).refuse_reverse_with_quote_tickets(apps, None)
                PrintJob.objects.filter.assert_called_once_with(kind='sales_quote_ticket')
                message = str(caught.exception)
                self.assertIn(node[:4], message)
                self.assertIn("DELETE FROM store_printjob WHERE kind='sales_quote_ticket'", message)

    def test_lets_the_reverse_go_on_without_quote_tickets(self):
        for node in NODES:
            with self.subTest(node=node):
                apps, _ = _apps_with(0)
                self.assertIsNone(_module(node).refuse_reverse_with_quote_tickets(apps, None))


class EveryNodeSinceTheQuotesCarriesTheGuardTest(SimpleTestCase):
    """A reverse that stops part-way (e.g. at 0190) and resumes later must still refuse first: each node from
    0189 to the current leaf carries the guard, so no node is undone while a quote ticket exists."""

    def test_no_store_node_after_0189_lacks_the_guard(self):
        from django.db.migrations.loader import MigrationLoader

        loader = MigrationLoader(None, ignore_no_migrations=True)
        nodes = sorted(name for app, name in loader.disk_migrations if app == 'store' and name >= '0189')
        self.assertTrue(nodes)
        for node in nodes:
            with self.subTest(node=node):
                last = loader.disk_migrations[('store', node)].operations[-1]
                self.assertIsInstance(last, migrations.RunPython)
                self.assertEqual(getattr(last.reverse_code, '__name__', ''), 'refuse_reverse_with_quote_tickets')
