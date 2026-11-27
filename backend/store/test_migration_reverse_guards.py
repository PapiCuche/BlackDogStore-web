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

NODES = ('0199_trade_in_quote_print', '0198_trade_in_quote', '0197_trade_in_assessment', '0196_trade_in_eligibility', '0195_trade_in_rules', '0194_trade_in_tariff', '0193_unit_import_terms_approval', '0192_unit_photo_provenance', '0191_trade_in_quote',
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


#: TRADE-IN VALUATION PRO · 5b: from 0199 on, `_v2` refuses trade-in quote tickets too.
TRADE_IN_NODES = ('0199_trade_in_quote_print',)
TRADE_IN_ROOT = ('store', TRADE_IN_NODES[0])


def trade_in_guarded_nodes(graph, root=TRADE_IN_ROOT):
    """The store nodes that must carry the trade-in guard: `root` and every node that DEPENDS on it in the graph.

    Selected by the graph, not by name: a sibling numbered after `root` that does not depend on it (another line's
    migration, before both are merged) is not below it, so a reverse never runs through it while tickets exist."""
    seen, todo = set(), [graph.node_map[root]]
    while todo:
        node = todo.pop()
        if node.key not in seen:
            seen.add(node.key)
            todo.extend(node.children)
    return sorted(name for app, name in seen if app == 'store')


class TradeInTicketGuardTest(SimpleTestCase):
    """Every node from 0199 down the graph carries `refuse_reverse_with_trade_in_tickets` SECOND to last (the quote
    guard stays last)."""

    def test_second_to_last_on_every_node_that_depends_on_0199(self):
        from django.db.migrations.loader import MigrationLoader

        loader = MigrationLoader(None, ignore_no_migrations=True)
        nodes = trade_in_guarded_nodes(loader.graph)
        self.assertTrue(set(TRADE_IN_NODES) <= set(nodes))
        for node in nodes:
            with self.subTest(node=node):
                second = loader.disk_migrations[('store', node)].operations[-2]
                self.assertIsInstance(second, migrations.RunPython)
                self.assertIs(second.code, migrations.RunPython.noop)
                self.assertEqual(getattr(second.reverse_code, '__name__', ''), 'refuse_reverse_with_trade_in_tickets')

    def test_the_nodes_are_chosen_by_the_graph_not_by_their_number(self):
        from django.db.migrations.graph import MigrationGraph

        graph = MigrationGraph()
        names = ('0198_base', '0199_trade_in_quote_print', '0200_unrelated_sibling', '0201_after_trade_in',
                 '0202_merge')
        for name in names:
            graph.add_node(('store', name), None)
        graph.add_node(('other', '0001_initial'), None)
        graph.add_dependency(None, ('store', '0199_trade_in_quote_print'), ('store', '0198_base'))
        graph.add_dependency(None, ('store', '0200_unrelated_sibling'), ('store', '0198_base'))
        graph.add_dependency(None, ('store', '0201_after_trade_in'), ('store', '0199_trade_in_quote_print'))
        graph.add_dependency(None, ('store', '0202_merge'), ('store', '0201_after_trade_in'))
        graph.add_dependency(None, ('store', '0202_merge'), ('store', '0200_unrelated_sibling'))
        graph.add_dependency(None, ('other', '0001_initial'), ('store', '0199_trade_in_quote_print'))
        self.assertEqual(trade_in_guarded_nodes(graph),
                         ['0199_trade_in_quote_print', '0201_after_trade_in', '0202_merge'])

    def test_it_refuses_while_a_trade_in_ticket_exists_and_names_the_fix(self):
        for node in TRADE_IN_NODES:
            with self.subTest(node=node):
                apps, PrintJob = _apps_with(1)
                with self.assertRaises(RuntimeError) as caught:
                    _module(node).refuse_reverse_with_trade_in_tickets(apps, None)
                PrintJob.objects.filter.assert_called_once_with(kind='trade_in_quote_ticket')
                self.assertIn("DELETE FROM store_printjob WHERE kind='trade_in_quote_ticket'", str(caught.exception))
                apps, _ = _apps_with(0)
                self.assertIsNone(_module(node).refuse_reverse_with_trade_in_tickets(apps, None))
