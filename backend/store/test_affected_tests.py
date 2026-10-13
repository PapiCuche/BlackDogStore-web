"""
`backend/scripts/affected_tests.py`: which tests to run first for what changed.

It is a convenience for whoever is writing code and it decides nothing — CI
runs the whole suite. What is pinned here is the little it promises: it only
ever names tests that exist, it says "everything" for what reaches everything,
and it knows which tests read a file from outside `backend/`.

That last list, EXTERNAL_INPUTS, is the one thing here CI does depend on:
`test_ci_workflow.py` checks the workflow's path filters against it.
"""
import ast
import fnmatch
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from store.test_ci_shards import WithTheSuite, ci_shards, script

BACKEND = Path(settings.BASE_DIR)
REPO = BACKEND.parent
affected_tests = script('affected_tests')


class AffectedTestsIsAConvenienceTest(WithTheSuite):
    def test_a_changed_test_module_selects_its_own_classes(self):
        labels, _reasons, whole = affected_tests.select(['backend/store/test_register_race.py'], self.suite)
        self.assertIsNone(whole)
        self.assertEqual(labels, [label for label in self.suite.labels if label.startswith('store.test_register_race.')])

    def test_what_reaches_every_test_selects_the_whole_suite(self):
        for name in ('backend/store/models.py', 'backend/store/migrations/0001_initial.py',
                     'backend/backend/settings.py', 'backend/requirements.txt'):
            with self.subTest(name=name):
                self.assertIsNotNone(affected_tests.select([name], self.suite)[2])

    def test_a_document_selects_nothing(self):
        self.assertEqual(affected_tests.select(['docs/NEXT_ACTION.md', 'README.md'], self.suite)[0], [])

    def test_a_module_selects_the_classes_that_name_it_and_the_ones_that_call_its_views(self):
        labels, reasons, _whole = affected_tests.select(['backend/store/delivery_views.py'], self.suite)
        self.assertIn('store.test_delivery_base.CarrierApiTest', labels)
        self.assertIn('calls a view of store.delivery_views', set(reasons.values()))
        self.assertLessEqual(set(labels), set(self.suite.labels))

    def test_a_file_read_from_outside_backend_selects_its_readers(self):
        for pattern, readers in affected_tests.EXTERNAL_INPUTS.items():
            name = pattern.replace('**', 'x').replace('*', 'x')
            with self.subTest(pattern=pattern):
                expected = affected_tests.readers_of(readers, self.suite)
                self.assertTrue(expected)
                self.assertLessEqual(set(expected), set(affected_tests.select([name], self.suite)[0]))
class WhatTheTestsReadOutsideTheBackendTest(SimpleTestCase):
    """EXTERNAL_INPUTS is the list the workflow's path filters are checked against (test_ci_workflow.py)."""

    def test_every_outside_file_exists_and_its_tests_exist(self):
        if not (REPO / 'deploy').exists():
            self.skipTest('Only backend/ is in this tree.')
        suite = ci_shards.discover()
        self.assertTrue(affected_tests.EXTERNAL_INPUTS)
        for pattern, readers in affected_tests.EXTERNAL_INPUTS.items():
            with self.subTest(pattern=pattern):
                self.assertTrue(list(REPO.glob(pattern)), 'nothing in the repository matches')
                self.assertTrue(readers)
                for reader in readers:
                    self.assertTrue(
                        any(label == reader or label.startswith(reader + '.') for label in suite.labels),
                        f'{reader} is not a test module or class of the suite any more',
                    )

    def test_every_outside_path_a_test_module_spells_out_is_listed(self):
        """
        Not a proof — a test can build a path this cannot see — but it reads
        what the tests actually write: a `'docs/x.md'` literal, a
        `ROOT / 'deploy' / 'backup.sh'`, an `os.path.join(root, 'frontend', 'app')`.
        Whatever of that exists in the repository, outside backend/, has to be
        covered by EXTERNAL_INPUTS: the whole suite run under an audit hook
        (CI-OPTIMIZATION-01) opened exactly these files and no other.
        """
        patterns = affected_tests.EXTERNAL_INPUTS
        unlisted = {}
        for path in sorted((BACKEND / 'store').glob('test*.py')):
            if path.name == Path(__file__).name:
                continue  # the paths written in this module are examples of changed files
            for name in _outside_backend(_paths_spelled_out(ast.parse(path.read_text(encoding='utf-8')))):
                covered = any(
                    fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(f'{name}/x', pattern)
                    or pattern.startswith(f'{name}/')
                    for pattern in patterns
                )
                if not covered:
                    unlisted.setdefault(path.name, set()).add(name)
        self.assertEqual(unlisted, {}, 'add these to EXTERNAL_INPUTS (scripts/affected_tests.py) and to the workflow')


def _paths_spelled_out(tree) -> set:
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and '/' in node.value:
            found.add(node.value)
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            parts = []
            while (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div)
                   and isinstance(node.right, ast.Constant) and isinstance(node.right.value, str)):
                parts.insert(0, node.right.value)
                node = node.left
            if parts:
                found.add('/'.join(parts))
        elif isinstance(node, ast.Call) and getattr(node.func, 'attr', '') == 'join':
            parts = [arg.value for arg in node.args if isinstance(arg, ast.Constant) and isinstance(arg.value, str)]
            if parts:
                found.add('/'.join(parts))
    return found


def _outside_backend(names):
    """Those that are a file or a folder of this repository, outside backend/."""
    for name in sorted(names):
        name = name.strip('/')
        if not name or len(name) > 200 or '..' in name or any(char in name for char in '*?[]<>{}\\\n'):
            continue
        try:
            target = REPO / name
            if not target.exists():
                continue
            target = target.resolve()
        except (OSError, ValueError):
            continue
        if REPO in target.parents and target != BACKEND and BACKEND not in target.parents:
            yield name
