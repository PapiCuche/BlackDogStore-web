"""
R13 rehearsal finding: reversing 0192 → 0186 stopped halfway at 0189 (CheckViolation on
`print_job_target_matches_kind`) whenever a `sales_quote_ticket` print job existed, after 0192–0190 had already
been undone. Both 0192 (first to reverse) and 0189 now end with a guard that refuses the reverse BEFORE anything
is undone and names the DELETE to run.
"""
from importlib import import_module
from unittest import mock

from django.db import migrations
from django.test import SimpleTestCase

NODES = ('0192_unit_photo_provenance', '0189_sales_quote')


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
