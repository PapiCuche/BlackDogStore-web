"""
What `.github/workflows/backend-postgres-validation.yml` does with the shards.

`test_ci_shards.py` proves that the shards are the suite. This pins the other
half: that the workflow runs every one of them, that a failure cannot hide
behind a pipe or a skipped job, that one job answers for all of them, and that
a change to any file a backend test reads starts the suite.

The file is read as text: the project has no YAML parser among its
dependencies and does not need one for this.
"""
import re
import unittest
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from store.test_affected_tests import affected_tests
from store.test_ci_shards import WithTheSuite, ci_shards

WORKFLOW = Path(settings.BASE_DIR).parent / '.github' / 'workflows' / 'backend-postgres-validation.yml'


def _workflow():
    if not WORKFLOW.exists():
        raise unittest.SkipTest('The workflow is not in this tree (an image built from backend/ alone).')
    return WORKFLOW.read_text(encoding='utf-8')


def _shards_in_the_workflow(text):
    return [int(number) for number in re.search(r'^\s+shard: \[([\d, ]+)\]\s*$', text, re.MULTILINE).group(1).split(',')]


class TheWorkflowRunsEveryShardBehindOneGateTest(SimpleTestCase):
    def setUp(self):
        self.text = _workflow()

    def test_the_matrix_is_one_to_n_and_n_comes_from_the_matrix(self):
        numbers = _shards_in_the_workflow(self.text)
        self.assertEqual(numbers, list(range(1, len(numbers) + 1)))
        self.assertGreaterEqual(len(numbers), 2)
        # The total is the size of the matrix, not a second number to keep in step.
        self.assertIn('      SHARD: ${{ matrix.shard }}\n', self.text)
        self.assertIn('      SHARD_TOTAL: ${{ strategy.job-total }}\n', self.text)
        self.assertEqual(self.text.count('--total "$SHARD_TOTAL"'), 3, 'plan, labels and check-log')
        self.assertEqual(self.text.count('--shard "$SHARD" --total "$SHARD_TOTAL"'), 2, 'labels and check-log')
        self.assertNotRegex(self.text, r'--(total|shard)\s+"?\d')

    def test_the_tests_run_with_the_labels_and_a_failure_cannot_hide_behind_the_pipe(self):
        step = self.text[self.text.index('- name: Run backend test suite'):self.text.index('- name: Check the shard')]
        self.assertIn('shell: bash', step)
        self.assertIn('set -eo pipefail', step)
        self.assertIn('python manage.py test --noinput $(cat "$RUNNER_TEMP/shard-labels.txt")', step)
        self.assertNotIn('continue-on-error', self.text)

    def test_one_job_fails_unless_every_shard_passed(self):
        gate = self.text[self.text.index('\n  backend:\n'):]
        self.assertIn('name: Django tests on PostgreSQL', gate)
        self.assertIn('needs: shards', gate)
        # `always()`: a gate that is skipped counts as passed for a required check.
        self.assertIn('if: always()', gate)
        self.assertIn('SHARDS: ${{ needs.shards.result }}', gate)
        self.assertIn('test "$SHARDS" = "success"', gate)
        self.assertIn('fail-fast: false', self.text)

    def test_a_newer_push_cancels_a_pull_request_run_and_never_one_on_master(self):
        self.assertIn("cancel-in-progress: ${{ github.event_name == 'pull_request' }}", self.text)
        self.assertIn(
            "group: backend-postgres-${{ github.event_name == 'pull_request' && github.ref || github.run_id }}",
            self.text,
        )

    def test_the_suite_still_runs_whole_on_pull_requests_and_on_master(self):
        triggers = self.text[:self.text.index('\npermissions:')]
        self.assertRegex(triggers, r'pull_request:\n    branches:\n      - master\n')
        self.assertRegex(triggers, r'push:\n    branches:\n      - master\n')
        self.assertNotIn('paths-ignore', triggers)


class TheShardsOfTheWorkflowAreTheSuiteTest(WithTheSuite):
    def test_the_shards_the_workflow_asks_for_load_the_suite_test_by_test(self):
        total = len(_shards_in_the_workflow(_workflow()))
        loaded = []
        for number in range(1, total + 1):
            loaded += ci_shards.load(ci_shards.shard_labels(self.suite, number, total)).ids
        self.assertEqual(len(loaded), len(self.suite.ids))
        self.assertEqual(sorted(loaded), sorted(self.suite.ids))


class WhatATestReadsStartsTheSuiteTest(SimpleTestCase):
    """A file outside backend/ that a test reads must be a path filter of the workflow."""

    def test_every_outside_file_is_a_path_filter_for_both_events(self):
        triggers = _workflow().split('\npermissions:')[0]
        self.assertIn('.github/workflows/backend-postgres-validation.yml', affected_tests.EXTERNAL_INPUTS)
        for pattern in ('backend/**', *affected_tests.EXTERNAL_INPUTS):
            with self.subTest(pattern=pattern):
                self.assertEqual(triggers.count(f'      - "{pattern}"\n'), 2, 'once for pull_request, once for push')


class NothingInCiSelectsTestsTest(SimpleTestCase):
    def test_no_workflow_calls_the_affected_tests_helper(self):
        for workflow in WORKFLOW.parent.glob('*.yml') if WORKFLOW.exists() else ():
            commands = [
                line for line in workflow.read_text(encoding='utf-8').splitlines()
                if not line.lstrip().startswith('#')
            ]
            self.assertNotIn('affected_tests', '\n'.join(commands), workflow.name)
