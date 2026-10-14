"""
CI runs this suite in shards. These tests are the proof that the shards ARE the suite.

`.github/workflows/backend-postgres-validation.yml` does not run one
`manage.py test`: it runs several side by side, each with the labels that
`backend/scripts/ci_shards.py` deals to it. The danger of any such split is the
quiet one — a new test file that no shard asks for, and a green check that
stopped meaning "everything passed".

So the split is checked from inside the thing it splits, on every run:

  * every test the runner discovers is in exactly one shard, for any number of
    shards, with or without the weights file;
  * asking `manage.py test` for a shard's labels loads exactly the tests the
    plan counted, and the shards together load the suite, test by test;
  * the classes that flush the database run as one chain, in one shard, in
    the order the whole suite runs them;
  * adding or removing a test moves no other test to a different shard;
  * what cannot be dealt faithfully stops the plan instead of being left out;
  * a shard whose log does not show every test it was dealt is not accepted.

This module is itself discovered, so it sits in one of the shards it describes.
What the workflow does with the shards is pinned in `test_ci_workflow.py`.
"""
import importlib.util
import unittest
from collections import Counter
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.test import SimpleTestCase, TestCase, TransactionTestCase
from django.utils.module_loading import import_string

BACKEND = Path(settings.BASE_DIR)


def script(name):
    """A file of backend/scripts/, loaded by path: the folder is not a package."""
    spec = importlib.util.spec_from_file_location(f'_ci_{name}', BACKEND / 'scripts' / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ci_shards = script('ci_shards')


class WithTheSuite(SimpleTestCase):
    """The discovered suite, found once per class. (No tests of its own: it is not collected.)"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.suite = ci_shards.discover()


class EveryTestIsInExactlyOneShardTest(WithTheSuite):
    def test_the_suite_was_found(self):
        self.assertGreater(len(self.suite.ids), 6000)
        self.assertIn(f'{__name__}.{type(self).__name__}', self.suite.labels)
        self.assertIn('store.tests', {label.rsplit('.', 1)[0] for label in self.suite.labels})

    def test_any_number_of_shards_is_the_whole_suite_and_no_class_is_in_two(self):
        for total in (1, 2, 3, 4, 5, 6, 7, 8, 12, 16):
            with self.subTest(total=total):
                shards = ci_shards.assign(self.suite, total)
                dealt = [label for shard in shards for label in shard]
                self.assertEqual(len(shards), total)
                self.assertEqual(Counter(dealt), Counter(self.suite.labels))
                self.assertEqual(sum(self.suite.count(shard) for shard in shards), len(self.suite.ids))
                ci_shards.verify(self.suite, shards)

    def test_the_weights_only_move_classes_between_shards(self):
        """No file, an empty file, a file about classes that no longer exist, a file that is wrong: the same suite."""
        for weights in (
            {},
            {'seconds_per_test': 1.0, 'classes': {}},
            {'seconds_per_test': 0.0, 'classes': {}, 'flushing': []},
            {'seconds_per_test': 0.3, 'classes': {'store.tests.ThisClassWasDeleted': 900.0}},
            {'seconds_per_test': 0.3, 'classes': {self.suite.labels[0]: 10_000.0}},
            {'seconds_per_test': 0.3, 'classes': {self.suite.labels[0]: 1.0}, 'flushing': [self.suite.labels[0], 'gone.Gone']},
        ):
            with self.subTest(weights=weights):
                shards = ci_shards.assign(self.suite, 6, weights)
                ci_shards.verify(self.suite, shards)
                self.assertNotIn('store.tests.ThisClassWasDeleted', [label for shard in shards for label in shard])

    def test_the_same_tree_gives_the_same_shards(self):
        self.assertEqual(ci_shards.assign(self.suite, 6), ci_shards.assign(ci_shards.discover(), 6))

    def test_adding_or_removing_a_test_moves_no_other_test(self):
        """
        The dealing is a function of the weights file and of each class's own
        name. If it depended on the rest of the suite, every new test would
        put different tests side by side, and a hidden dependence between two
        of them would surface in somebody else's pull request.
        """
        def where(suite):
            return {label: number for number, shard in enumerate(ci_shards.assign(suite, 6)) for label in shard}

        before = where(self.suite)

        grown = ci_shards.Suite()
        grown.flushing = list(self.suite.flushing)
        grown.tests = {'store.test_brand_new.FirstTest': ['a', 'b'], **self.suite.tests, 'store.tests.ZzNewTest': ['c']}
        after = where(grown)
        self.assertEqual({label: after[label] for label in before}, before)
        self.assertEqual(after.keys() - before.keys(), {'store.test_brand_new.FirstTest', 'store.tests.ZzNewTest'})

        shrunk = ci_shards.Suite()
        shrunk.flushing = list(self.suite.flushing)
        shrunk.tests = dict(list(self.suite.tests.items())[::2])
        after = where(shrunk)
        self.assertEqual(after, {label: before[label] for label in shrunk.tests})

    def test_a_class_the_weights_do_not_know_is_dealt_by_its_name(self):
        # A class that flushes the database goes with the rest of its kind, whatever
        # its name (pinned by TheClassesThatFlushTheDatabaseStayTogetherTest).
        unknown = [
            label for label in self.suite.labels
            if label not in ci_shards.read_weights()['classes'] and label not in self.suite.flushing
        ]
        self.assertIn(f'{__name__}.{type(self).__name__}', unknown)
        shards = ci_shards.assign(self.suite, 6)
        for label in unknown:
            self.assertIn(label, shards[ci_shards._home(label, 6)])
        # The name alone, and the same answer on every machine and every run.
        self.assertEqual(ci_shards._home('store.tests.CheckoutTest', 6), 0)
        self.assertEqual(
            [ci_shards._home(f'store.tests.Test{number}', 6) for number in range(8)],
            [4, 4, 2, 3, 5, 0, 2, 5],
        )

    def test_a_shard_keeps_the_order_of_the_whole_suite(self):
        position = {label: index for index, label in enumerate(self.suite.labels)}
        for shard in ci_shards.assign(self.suite, 6):
            self.assertEqual(shard, sorted(shard, key=position.__getitem__))

    def test_no_shard_is_dealt_far_more_than_the_rest(self):
        weight = ci_shards.weigh(self.suite, ci_shards.read_weights())
        loads = [sum(weight[label] for label in shard) for shard in ci_shards.assign(self.suite, 6)]
        self.assertLess(max(loads), 1.25 * (sum(loads) / len(loads)))


class TheLabelsOfAShardLoadExactlyItsTestsTest(WithTheSuite):
    def test_the_shards_load_the_suite_test_by_test(self):
        for total in (2, 3, 6, 8):
            with self.subTest(total=total):
                loaded = []
                for number in range(1, total + 1):
                    labels = ci_shards.shard_labels(self.suite, number, total)
                    self.assertTrue(labels, f'shard {number} is empty: with no labels the whole suite would run')
                    shard = ci_shards.load(labels)
                    self.assertEqual(shard.tests, {label: self.suite.tests[label] for label in labels})
                    loaded += shard.ids
                self.assertEqual(len(loaded), len(self.suite.ids))
                self.assertEqual(sorted(loaded), sorted(self.suite.ids))

    def test_a_shard_runs_its_tests_in_the_order_the_whole_suite_runs_them(self):
        """After the runner's own reordering (TestCase, SimpleTestCase, then the rest)."""
        whole = [test.id() for test in ci_shards.runner().build_suite([str(BACKEND)])]
        position = {test: index for index, test in enumerate(whole)}
        for number in range(1, 7):
            shard = [test.id() for test in ci_shards.runner().build_suite(ci_shards.shard_labels(self.suite, number, 6))]
            self.assertEqual(shard, sorted(shard, key=position.__getitem__))

    def test_a_shard_number_that_does_not_exist_is_refused(self):
        for number in (0, 7, -1):
            with self.assertRaises(ci_shards.SuiteError):
                ci_shards.shard_labels(self.suite, number, 6)
        with self.assertRaises(ci_shards.SuiteError):
            ci_shards.assign(self.suite, 0)


class TheClassesThatFlushTheDatabaseStayTogetherTest(WithTheSuite):
    """
    A TransactionTestCase runs on what the previous one left behind, and five
    of this suite's set `reset_sequences`: they restart every id at 1 and only
    work because an earlier class of the kind has already emptied the tables
    the migrations seed. `C15InitialRaceTest`, dealt first of its kind to a
    shard, failed on company 1 the first time the shards were run. Spread over
    shards the chain is a different chain in each; in one shard, in order, it
    is the chain of the whole suite.
    """

    def test_they_are_the_transaction_test_cases_and_nothing_else(self):
        flushing = [
            label for label in self.suite.labels
            if issubclass(import_string(label), TransactionTestCase) and not issubclass(import_string(label), TestCase)
        ]
        self.assertEqual(self.suite.flushing, flushing)
        self.assertIn('store.tests.C15InitialRaceTest', flushing)
        self.assertGreater(len(flushing), 20)

    def test_the_last_shard_holds_all_of_them_in_the_order_of_the_whole_suite(self):
        for total in (1, 2, 3, 6, 8, 12):
            with self.subTest(total=total):
                shards = ci_shards.assign(self.suite, total)
                self.assertEqual([label for label in shards[-1] if label in self.suite.flushing], self.suite.flushing)
                for shard in shards[:-1]:
                    self.assertFalse(set(shard) & set(self.suite.flushing))

    def test_it_is_the_class_that_says_whether_it_flushes_and_not_the_file(self):
        """A file that forgot them all, or that names a class that never flushed: the chain is the same."""
        weights = ci_shards.read_weights()
        for flushing in ([], [self.suite.labels[0]]):
            shards = ci_shards.assign(self.suite, 6, {**weights, 'flushing': flushing})
            ci_shards.verify(self.suite, shards)
            self.assertEqual([label for label in shards[-1] if label in self.suite.flushing], self.suite.flushing)
            self.assertFalse(any(set(shard) & set(self.suite.flushing) for shard in shards[:-1]))

    def test_their_tests_run_there_exactly_as_they_run_in_the_whole_suite(self):
        def chain(labels):
            return [
                test.id() for test in ci_shards.runner().build_suite(labels)
                if isinstance(test, TransactionTestCase) and not isinstance(test, TestCase)
            ]

        whole = chain([str(BACKEND)])
        ran = [chain(shard) for shard in ci_shards.assign(self.suite, 6)]
        self.assertEqual([found for found in ran if found], [whole])
        self.assertEqual(len(whole), self.suite.count(self.suite.flushing))

    def test_the_committed_file_knows_which_they_are(self):
        """Not needed to keep them together — only to give the last shard a fair share of the rest."""
        self.assertEqual(sorted(ci_shards.read_weights()['flushing']), sorted(self.suite.flushing))


class WhatCannotBeDealtStopsThePlanTest(SimpleTestCase):
    def test_a_module_that_does_not_import(self):
        broken = unittest.defaultTestLoader.loadTestsFromName('store.test_this_module_does_not_exist')
        with self.assertRaisesMessage(ci_shards.SuiteError, 'could not be loaded'):
            ci_shards.collect(broken)

    def test_a_test_found_twice(self):
        test = type(self)('test_a_test_found_twice')
        with self.assertRaisesMessage(ci_shards.SuiteError, 'found twice'):
            ci_shards.collect([test, test])

    def test_a_class_no_label_can_name(self):
        class Hidden(SimpleTestCase):
            def test_it(self):
                pass

        with self.assertRaisesMessage(ci_shards.SuiteError, 'does not name its class'):
            ci_shards.collect([Hidden('test_it')])

    def test_the_command_line_fails_instead_of_printing_a_shorter_list(self):
        with mock.patch.object(ci_shards, 'discover', side_effect=ci_shards.SuiteError('no')), \
                mock.patch('sys.stderr'), mock.patch('sys.stdout') as out:
            self.assertEqual(ci_shards.main(['labels', '--shard', '1', '--total', '6']), 2)
        out.write.assert_not_called()

    def test_an_empty_shard_is_an_error_because_no_labels_means_every_test(self):
        empty = ci_shards.Suite()
        empty.tests['store.tests.Only'] = ['store.tests.Only.test_it']
        with mock.patch.object(ci_shards, 'discover', return_value=empty), mock.patch('sys.stderr'), \
                mock.patch('sys.stdout') as out:
            self.assertEqual(ci_shards.main(['labels', '--shard', '2', '--total', '2']), 2)
        out.write.assert_not_called()


class TheLogOfAShardTest(SimpleTestCase):
    GREEN = 'Found 3 test(s).\n...\n' + '-' * 70 + '\nRan 3 tests in 12.345s\n\nOK (skipped=1)\n'
    RED = (
        'ERROR: test_a (store.tests.X.test_a)\nFAIL: test_b (store.tests.X.test_b)\n'
        + '-' * 70 + '\nRan 3 tests in 1.000s\n\nFAILED (failures=1, errors=1)\n'
    )

    def test_a_green_shard_that_ran_what_it_was_dealt(self):
        ok, summary = ci_shards.check_log(self.GREEN, 3, 2, 6)
        self.assertTrue(ok)
        self.assertIn('planned: **3** tests · ran: **3**', summary)

    def test_a_green_shard_that_ran_fewer_tests_is_not_accepted(self):
        ok, summary = ci_shards.check_log(self.GREEN, 4, 2, 6)
        self.assertFalse(ok)
        self.assertIn('did not run its part of the suite', summary)

    def test_a_red_shard_names_what_broke(self):
        ok, summary = ci_shards.check_log(self.RED, 3, 2, 6)
        self.assertFalse(ok)
        self.assertIn('ERROR: test_a (store.tests.X.test_a)', summary)
        self.assertIn('FAIL: test_b (store.tests.X.test_b)', summary)

    def test_a_run_that_never_printed_its_total_is_not_accepted(self):
        for text in ('', 'Found 3 test(s).\n..'):
            ok, summary = ci_shards.check_log(text, 3, 2, 6)
            self.assertFalse(ok)
            self.assertIn('did not finish', summary)


class TheWeightsComeFromARealRunTest(SimpleTestCase):
    LOG = (
        'Slowest test durations\n' + '-' * 70 + '\n'
        '0.505s     test_a (store.tests.A.test_a)\n'
        '0.100s     test_b (store.tests.A.test_b)\n'
        '1.250s     test_x (store.test_m.B.test_x)\n'
        '\n(durations < 0.001s were hidden; use -v to show these durations)\n'
        + '-' * 70 + '\nRan 5 tests in 2.100s\n\nOK\n'
    )

    def test_a_class_weighs_the_sum_of_its_tests(self):
        weights = ci_shards.weights_from_log(self.LOG, flushing=['store.test_m.B'])
        self.assertEqual(weights['classes'], {'store.test_m.B': 1.2, 'store.tests.A': 0.6})
        self.assertEqual(weights['seconds_per_test'], 0.371)
        self.assertEqual(weights['flushing'], ['store.test_m.B'])

    def test_a_log_without_durations_is_refused_instead_of_emptying_the_file(self):
        with self.assertRaises(ci_shards.SuiteError):
            ci_shards.weights_from_log('Ran 5 tests in 2.100s\n\nOK\n')

    def test_the_committed_file_is_read_and_names_classes_of_this_suite(self):
        weights = ci_shards.read_weights()
        self.assertGreater(weights['seconds_per_test'], 0)
        known = set(ci_shards.discover().labels)
        self.assertGreater(len(known & set(weights['classes'])), 500)
