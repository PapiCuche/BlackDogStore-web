#!/usr/bin/env python3
"""
Splits the Django suite into N shards, so that CI can run them side by side.

    python scripts/ci_shards.py plan --total 6
    python scripts/ci_shards.py labels --shard 2 --total 6
    python manage.py test --noinput $(python scripts/ci_shards.py labels --shard 2 --total 6)
    python scripts/ci_shards.py check-log --shard 2 --total 6 test.log
    python manage.py test --noinput --durations 0 2>&1 | python scripts/ci_shards.py weights > scripts/ci_shard_weights.json

WHAT A SHARD IS
---------------
A list of test classes, written as the labels `manage.py test` already takes
(`store.tests.CheckoutTest`). Nothing else changes: the same settings, the same
`TEST_RUNNER`, the same database, one process. A class is never cut in two, so
its `setUpTestData` runs once, as it does in the whole suite.

WHY NO TEST CAN BE LEFT OUT
---------------------------
There is no list of tests in this file, and none to keep up to date. Every run
asks the project's own runner what the suite is (`build_suite`, the call behind
a bare `manage.py test`) and deals out ALL of it: a new module or a new class is
in a shard the moment it exists. `store/test_ci_shards.py` proves, inside the
suite itself, that the shards are disjoint and that together they are the suite.

What cannot be dealt out stops the plan instead of being skipped: a module that
does not import, a class that a label cannot name, a test found twice.

WHY THE WEIGHTS CANNOT HIDE A TEST EITHER
-----------------------------------------
`ci_shard_weights.json` holds how long each class took the last time somebody
measured. It decides only WHICH shard a class goes to. A class missing from the
file is weighed by its number of tests; a class in the file that no longer exists
is ignored. A stale file makes the shards uneven, never incomplete.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
WEIGHTS_FILE = Path(__file__).with_name('ci_shard_weights.json')


class SuiteError(Exception):
    """The suite cannot be split without changing what runs."""


class Suite:
    """The discovered suite: its classes in the order found, and their tests."""

    def __init__(self):
        self.tests: dict[str, list[str]] = {}

    @property
    def labels(self) -> list[str]:
        return list(self.tests)

    @property
    def ids(self) -> list[str]:
        return [test for tests in self.tests.values() for test in tests]

    def count(self, labels=None) -> int:
        return sum(len(self.tests[label]) for label in (self.tests if labels is None else labels))


def _setup_django():
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.settings')
    import django
    from django.apps import apps

    if not apps.ready:
        django.setup()


def runner():
    """The project's runner (`TEST_RUNNER`), silent. Building a suite touches no database."""
    _setup_django()
    from django.conf import settings
    from django.test.utils import get_runner

    return get_runner(settings)(verbosity=0, enable_faulthandler=False)


def _label_of(test) -> str:
    cls = type(test)
    label = f'{cls.__module__}.{cls.__qualname__}'
    target = sys.modules.get(cls.__module__)
    for part in cls.__qualname__.split('.'):
        target = getattr(target, part, None)
    if target is not cls:
        raise SuiteError(
            f'{test.id()}: the label {label} does not name its class, so no shard could ask for it. '
            f'Define the test class at module level, under its own name.'
        )
    return label


def collect(tests) -> Suite:
    """Group an iterable of tests by class. Refuses what a shard could not run faithfully."""
    suite = Suite()
    seen = set()
    for test in tests:
        if isinstance(test, unittest.loader._FailedTest):
            raise SuiteError(
                f'{test.id()} could not be loaded, so the suite cannot be split:\n'
                f'{getattr(test, "_exception", "")}'
            )
        test_id = test.id()
        if test_id in seen:
            raise SuiteError(
                f'{test_id} was found twice. A test class imported into another test module runs '
                f'once per module in the whole suite and once in a shard: import the module, not the class.'
            )
        seen.add(test_id)
        suite.tests.setdefault(_label_of(test), []).append(test_id)
    return suite


def discover() -> Suite:
    """The whole suite, as a bare `manage.py test` run from backend/ finds it."""
    return collect(runner().build_suite([str(BACKEND)]))


def load(labels) -> Suite:
    """What `manage.py test <labels>` would run."""
    return collect(runner().build_suite(list(labels))) if labels else Suite()


def read_weights(path: Path = WEIGHTS_FILE) -> dict:
    if not path.exists():
        return {'seconds_per_test': 1.0, 'classes': {}}
    data = json.loads(path.read_text(encoding='utf-8'))
    return {'seconds_per_test': float(data.get('seconds_per_test', 1.0)), 'classes': dict(data.get('classes', {}))}


def weigh(suite: Suite, weights: dict) -> dict[str, float]:
    per_test = weights['seconds_per_test']
    known = weights['classes']
    return {
        label: float(known[label]) if label in known else per_test * len(tests)
        for label, tests in suite.tests.items()
    }


def assign(suite: Suite, total: int, weights: dict | None = None) -> list[list[str]]:
    """
    Deal every class of the suite into `total` shards of similar weight.

    Heaviest first, each to the lightest shard so far; ties go to the lower
    shard and to the label that sorts first, so the same tree always gives the
    same answer on every machine. Inside a shard the classes keep the order of
    the whole suite.
    """
    if total < 1:
        raise SuiteError(f'--total must be 1 or more, not {total}')
    weight = weigh(suite, read_weights() if weights is None else weights)
    position = {label: index for index, label in enumerate(suite.labels)}
    shards: list[list[str]] = [[] for _ in range(total)]
    loads = [0.0] * total
    for label in sorted(suite.labels, key=lambda item: (-weight[item], item)):
        lightest = min(range(total), key=lambda index: (loads[index], index))
        shards[lightest].append(label)
        loads[lightest] += weight[label]
    for shard in shards:
        shard.sort(key=position.__getitem__)
    return shards


def shard_labels(suite: Suite, shard: int, total: int, weights: dict | None = None) -> list[str]:
    if not 1 <= shard <= total:
        raise SuiteError(f'--shard must be between 1 and {total}, not {shard}')
    return assign(suite, total, weights)[shard - 1]


def verify(suite: Suite, shards: list[list[str]]) -> None:
    """Disjoint, and together the whole suite. Raises SuiteError otherwise."""
    dealt = [label for shard in shards for label in shard]
    if len(dealt) != len(set(dealt)):
        twice = sorted({label for label in dealt if dealt.count(label) > 1})
        raise SuiteError(f'in more than one shard: {twice[:5]}')
    if set(dealt) != set(suite.labels):
        missing = sorted(set(suite.labels) - set(dealt))
        extra = sorted(set(dealt) - set(suite.labels))
        raise SuiteError(f'not the whole suite: missing {missing[:5]}, unknown {extra[:5]}')
    if sum(suite.count(shard) for shard in shards) != len(suite.ids):
        raise SuiteError('the shards do not add up to the suite')


RAN = re.compile(r'^Ran (\d+) tests? in ([\d.]+)s', re.MULTILINE)
VERDICT = re.compile(r'^(OK|FAILED)(?: \((.*)\))?\s*$', re.MULTILINE)
BROKEN = re.compile(r'^(ERROR|FAIL): (.+)$', re.MULTILINE)


def read_log(text: str) -> dict:
    """What a `manage.py test` log says it did. `ran` is None when it never finished."""
    ran = RAN.findall(text)
    verdict = VERDICT.findall(text)
    return {
        'ran': int(ran[-1][0]) if ran else None,
        'seconds': float(ran[-1][1]) if ran else None,
        'verdict': verdict[-1][0] if verdict else None,
        'detail': verdict[-1][1] if verdict else '',
        'broken': [f'{kind}: {name}' for kind, name in BROKEN.findall(text)],
    }


def check_log(text: str, expected: int, shard: int, total: int) -> tuple[bool, str]:
    """Did the shard run every test it was dealt? Returns (ok, a Markdown summary)."""
    found = read_log(text)
    lines = [f'### Django shard {shard}/{total}', '']
    if found['ran'] is None:
        lines.append(f'The run did not finish: {expected} tests were planned and no total was printed.')
        return False, '\n'.join(lines) + '\n'
    lines.append(f'- planned: **{expected}** tests · ran: **{found["ran"]}** in {found["seconds"]:.0f} s')
    lines.append(f'- result: **{found["verdict"] or "unknown"}**' + (f' ({found["detail"]})' if found['detail'] else ''))
    same = found['ran'] == expected
    if not same and found['verdict'] == 'OK':
        lines.append(f'- **{found["ran"]} tests ran and {expected} were dealt to this shard**: it did not run its part of the suite')
    elif not same:
        lines.append(f'- {expected - found["ran"]} planned test(s) did not run (a class whose set-up fails runs none of its tests)')
    if found['broken']:
        lines += ['', '```', *found['broken'][:200], '```']
    return same and found['verdict'] == 'OK', '\n'.join(lines) + '\n'


DURATION = re.compile(r'^(\d+\.\d+)s\s+\S+ \(([\w.]+)\.\w+\)\s*$', re.MULTILINE)


def weights_from_log(text: str) -> dict:
    """
    The weights file, from the log of a whole run with `--durations 0`.

    unittest prints one line per test that took a millisecond or more
    (`0.412s  test_name (module.Class.test_name)`); a class weighs the sum of
    its lines. Class fixtures are not in those lines, which is fine for a
    weight: in this suite the time is in the tests.
    """
    classes: dict[str, float] = {}
    for seconds, label in DURATION.findall(text):
        classes[label] = classes.get(label, 0.0) + float(seconds)
    found = read_log(text)
    if not classes or not found['ran']:
        raise SuiteError('no durations in that log: run the whole suite with `--durations 0`')
    return {
        'how': 'python manage.py test --noinput --durations 0 2>&1 | python scripts/ci_shards.py weights',
        'seconds_per_test': round(sum(classes.values()) / found['ran'], 3),
        'classes': {label: round(seconds, 1) for label, seconds in sorted(classes.items())},
    }


def _plan(suite: Suite, total: int) -> str:
    weight = weigh(suite, read_weights())
    shards = assign(suite, total)
    verify(suite, shards)
    lines = ['shard  classes  tests  weight(s)']
    for index, shard in enumerate(shards, start=1):
        lines.append(f'{index:>5}  {len(shard):>7}  {suite.count(shard):>5}  {sum(weight[label] for label in shard):>9.0f}')
    lines.append(f'total  {len(suite.labels):>7}  {len(suite.ids):>5}  {sum(weight.values()):>9.0f}')
    unweighed = len(set(suite.labels) - set(read_weights()['classes']))
    lines.append(f'{unweighed} class(es) weighed by their number of tests (not in {WEIGHTS_FILE.name})')
    return '\n'.join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0].strip())
    sub = parser.add_subparsers(dest='command', required=True)
    for name, text in (
        ('plan', 'show the size of every shard and check that they are the suite'),
        ('labels', 'print the test labels of one shard'),
        ('check-log', 'check that a test log ran exactly the tests of one shard'),
    ):
        command = sub.add_parser(name, help=text)
        command.add_argument('--total', type=int, required=True)
        if name != 'plan':
            command.add_argument('--shard', type=int, required=True)
        if name == 'check-log':
            command.add_argument('log', type=Path)
    sub.add_parser('weights', help='turn the log of a whole run with --durations 0 (stdin) into the weights file')
    args = parser.parse_args(argv)

    try:
        if args.command == 'weights':
            print(json.dumps(weights_from_log(sys.stdin.read()), indent=1, ensure_ascii=False))
            return 0
        suite = discover()
        if args.command == 'plan':
            print(_plan(suite, args.total))
            return 0
        labels = shard_labels(suite, args.shard, args.total)
        if args.command == 'labels':
            if not labels:
                raise SuiteError(
                    f'shard {args.shard}/{args.total} is empty: with no labels `manage.py test` would run '
                    f'the whole suite. Use fewer shards.'
                )
            print('\n'.join(labels))
            print(
                f'shard {args.shard}/{args.total}: {len(labels)} classes, {suite.count(labels)} of '
                f'{len(suite.ids)} tests',
                file=sys.stderr,
            )
            return 0
        text = args.log.read_text(encoding='utf-8', errors='replace') if args.log.exists() else ''
        ok, summary = check_log(text, suite.count(labels), args.shard, args.total)
        print(summary)
        # On GitHub the same lines go to the job's summary page.
        page = os.environ.get('GITHUB_STEP_SUMMARY')
        if page:
            with open(page, 'a', encoding='utf-8') as handle:
                handle.write(summary + '\n')
        return 0 if ok else 1
    except SuiteError as error:
        print(f'ci_shards: {error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
