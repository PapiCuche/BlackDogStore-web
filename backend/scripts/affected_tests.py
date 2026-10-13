#!/usr/bin/env python3
"""
Which backend tests are worth running FIRST for the files you changed.

    python3 scripts/affected_tests.py                 # against origin/master, plus what is not committed
    python3 scripts/affected_tests.py --base HEAD~1
    python3 scripts/affected_tests.py --files backend/store/pos_services.py
    python3 scripts/affected_tests.py --run           # run them (`manage.py test <labels> --noinput`)
    python3 scripts/affected_tests.py --wide          # also the tests one import away

A CONVENIENCE FOR THE DESK, NOT A GATE
--------------------------------------
This guesses, from the text of the tests, which classes touch what changed. It
is quick feedback while writing code. It can miss a test — one that reaches the
changed code through a signal, a model method or a third module — so it decides
nothing: CI runs the WHOLE suite on every pull request and on `master`, and
nothing in `.github/` calls this file.

HOW IT GUESSES (no database, a couple of seconds)
-------------------------------------------------
1. A changed test file: the classes the change touches (the module, if the
   change is outside any class).
2. A changed module `store/x.py`: the classes that name it — an import, a name
   imported from it, a `'store.x.…'` string given to `patch`.
3. The classes that call a URL whose view lives in that module (`'/api/…'`
   literals are resolved with the project's own URLconf).
4. With `--wide`, the classes that call a view whose module imports it.
5. A file read by a test from outside `backend/` (EXTERNAL_INPUTS below).

`models.py`, the migrations, the settings and the requirements reach every
test: for those it says so and selects the whole suite.

Exit status: 0 with labels printed · 10 nothing to run · 20 run the whole suite.
"""
from __future__ import annotations

import argparse
import ast
import fnmatch
import os
import re
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
REPO = BACKEND.parent

#: Files OUTSIDE backend/ that a backend test reads, and who reads them (a test
#: module, or one class of it). A change to one of these can turn the backend
#: suite red, so each pattern is also a path filter of the backend workflow:
#: `store/test_ci_workflow.py` fails if one is missing there, and
#: `store/test_affected_tests.py` fails if a test module spells out a path that
#: is not here, or if a reader named here stopped existing. (The list comes from
#: running the whole suite under an audit hook on `open` and `os.scandir`.)
EXTERNAL_INPUTS: dict[str, tuple[str, ...]] = {
    'deploy/**': (
        'store.test_deploy_scripts',
        'store.test_preflight',
        'store.test_integrations_smtp',
        'store.test_request_limits.ProxyAgreesTest',
    ),
    'docker-compose.prod.yml': ('store.test_deploy_scripts.ComposeFileTest',),
    'docs/internal-parity-matrix.md': ('store.tests.Ip1ParityManifestTest',),
    'frontend/app/admin/**': ('store.tests.H412bFrontendLegacyRoleParityTest',),
    'frontend/app/auth/components/DevQuickLogin.tsx': ('store.tests.DemoAccountsSingleSourceTest',),
    '.github/workflows/backend-postgres-validation.yml': ('store.test_ci_workflow',),
}

#: Inside backend/, but reached by path instead of by import.
READ_BY_PATH: dict[str, tuple[str, ...]] = {
    'backend/scripts/*': ('store.test_ci_shards', 'store.test_affected_tests', 'store.test_ci_workflow'),
}

#: Reach every test: no selection is honest for these.
EVERYTHING = (
    'backend/store/models.py',
    'backend/store/migrations/*',
    'backend/backend/settings.py',
    'backend/requirements.txt',
    'backend/manage.py',
    'backend/store/suite_runner.py',
)

#: Above this share of the suite, the honest answer is "run it all".
TOO_WIDE = 0.5

NOTHING, WHOLE_SUITE = 10, 20


def changed_files(base: str) -> list[str]:
    """Committed since the merge base with `base`, plus staged, unstaged and untracked."""
    def git(*args):
        return subprocess.run(['git', *args], cwd=REPO, capture_output=True, text=True, check=True).stdout

    names = set(git('diff', '--name-only', f'{base}...HEAD').splitlines())
    names.update(git('diff', '--name-only', 'HEAD').splitlines())
    names.update(git('ls-files', '--others', '--exclude-standard').splitlines())
    return sorted(name for name in names if name)


def changed_lines(base: str, name: str) -> set[int] | None:
    """Line numbers (new side) the change touches; None when the file is new or unknown to git."""
    done = subprocess.run(
        ['git', 'diff', '-U0', base, '--', name], cwd=REPO, capture_output=True, text=True,
    )
    if done.returncode != 0 or not done.stdout.strip():
        return None
    lines = set()
    for start, length in re.findall(r'^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@', done.stdout, re.MULTILINE):
        first, count = int(start), int(length or 1)
        lines.update(range(first, first + max(count, 1)))
    return lines


def module_of(name: str) -> str | None:
    """`backend/store/mail/service.py` → `store.mail.service`; a data file → the package it lives in."""
    path = Path(name)
    if path.parts[:1] != ('backend',) or len(path.parts) < 3:
        return None
    inside = Path(*path.parts[1:])
    if inside.suffix == '.py':
        parts = inside.with_suffix('').parts
        return '.'.join(parts[:-1] if parts[-1] == '__init__' else parts)
    folder = inside.parent
    while folder.parts and not (BACKEND / folder / '__init__.py').exists():
        folder = folder.parent
    return '.'.join(folder.parts) if len(folder.parts) > 1 else None


ROOTS = ('store', 'backend', 'print_agent')


def _ours(module: str) -> bool:
    """A module of this project, and not just the name of a root package."""
    return '.' in module and module.split('.')[0] in ROOTS


class TestClass:
    def __init__(self, label, first, last):
        self.label, self.first, self.last = label, first, last
        self.modules: set[str] = set()   # store.* modules its text names
        self.urls: set[str] = set()      # '/api/…' literals, placeholders filled
        self.text: set[str] = set()      # every string literal (for file names)
        self.bases: list[str] = []


def _bound(node, package: str) -> dict[str, str]:
    """The names an import statement binds → the project module each stands for."""
    names = {}
    if isinstance(node, ast.Import):
        for alias in node.names:
            if _ours(alias.name):
                names[(alias.asname or alias.name).split('.')[0]] = alias.name
        return names
    source = node.module or ''
    if node.level:
        base = package.split('.')
        source = '.'.join(base[:len(base) - (node.level - 1)] + ([node.module] if node.module else []))
    if source.split('.')[0] in ROOTS:
        for alias in node.names:
            names[alias.asname or alias.name] = f'{source}.{alias.name}'
    return names


class _Reader(ast.NodeVisitor):
    """What one class, or one function, names."""

    def __init__(self, package: str, imported: dict[str, set[str]], constants: dict[str, set[str]]):
        self.package, self.imported, self.constants = package, imported, constants
        self.modules, self.urls, self.text = set(), set(), set()

    def visit_Import(self, node):
        self.modules.update(_bound(node, self.package).values())

    visit_ImportFrom = visit_Import

    def visit_Name(self, node):
        self.modules.update(self.imported.get(node.id, ()))
        for value in self.constants.get(node.id, ()):
            self._string(value)

    def visit_Constant(self, node):
        if isinstance(node.value, str):
            self._string(node.value)

    def visit_JoinedStr(self, node):
        for filler in ('1', 'x'):
            self._string(''.join(
                part.value if isinstance(part, ast.Constant) and isinstance(part.value, str) else filler
                for part in node.values
            ))
        self.generic_visit(node)

    def _string(self, value: str):
        if len(value) > 400:
            return
        self.text.add(value)
        if value.startswith('/api/'):
            self.urls.add(value.split('?')[0])
        for dotted in re.findall(r'\b(?:store|print_agent)(?:\.[A-Za-z_]\w*)+', value):
            self.modules.add(dotted)


def read_test_module(path: Path) -> list[TestClass]:
    """The classes of one test module and what each one names."""
    module = '.'.join(path.relative_to(BACKEND).with_suffix('').parts)
    package = module.rsplit('.', 1)[0]
    tree = ast.parse(path.read_text(encoding='utf-8'))

    # What the names bound at the top of the module stand for.
    imported: dict[str, set[str]] = {}
    constants: dict[str, set[str]] = {}
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for name, target in _bound(node, package).items():
                imported.setdefault(name, set()).add(target)
        elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    constants.setdefault(target.id, set()).add(node.value.value)
    # A module-level helper stands for what it names: whoever calls it inherits that.
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            reader = _Reader(package, imported, constants)
            reader.visit(node)
            imported.setdefault(node.name, set()).update(reader.modules)
            constants.setdefault(node.name, set()).update(reader.text)

    classes: dict[str, TestClass] = {}
    for node in tree.body:
        reader = _Reader(package, imported, constants)
        if isinstance(node, ast.ClassDef):
            found = TestClass(f'{module}.{node.name}', node.lineno, node.end_lineno)
            found.bases = [base.id for base in node.bases if isinstance(base, ast.Name)]
            for decorator in node.decorator_list:
                found.first = min(found.first, decorator.lineno)
            reader.visit(node)
            found.modules, found.urls, found.text = reader.modules, reader.urls, reader.text
            classes[node.name] = found

    # A class also names what its bases in the same module name (shared set-up).
    def inherit(found: TestClass, seen: set[str]):
        for base in found.bases:
            if base in classes and base not in seen:
                seen.add(base)
                inherit(classes[base], seen)
                found.modules |= classes[base].modules
                found.urls |= classes[base].urls
                found.text |= classes[base].text

    for found in classes.values():
        inherit(found, {found.label.rsplit('.', 1)[1]})
    return list(classes.values())


_READ: dict[tuple, list[TestClass]] = {}
_RESOLVED: dict[str, str | None] = {}


def _view_module(url: str) -> str | None:
    from django.urls import Resolver404, resolve

    if url not in _RESOLVED:
        _RESOLVED[url] = None
        for candidate in (url, url.rstrip('/') + '/'):
            try:
                match = resolve(candidate)
            except Resolver404:
                continue
            view = getattr(match.func, 'view_class', None) or getattr(match.func, 'cls', None) or match.func
            _RESOLVED[url] = view.__module__
            break
    return _RESOLVED[url]


def view_modules(urls: set[str]) -> set[str]:
    """The modules of the views these paths reach, by the project's URLconf."""
    return {module for module in map(_view_module, urls) if module}


def imports_of(module: str) -> set[str]:
    """The project modules a module imports (one hop), read from its source."""
    path = BACKEND.joinpath(*module.split('.'))
    source = path.with_suffix('.py') if path.with_suffix('.py').exists() else path / '__init__.py'
    if not source.exists():
        return set()
    package = module if source.name == '__init__.py' else module.rsplit('.', 1)[0]
    found = set()
    for node in ast.walk(ast.parse(source.read_text(encoding='utf-8'))):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            found.update(_bound(node, package).values())
    return found


def readers_of(readers, suite) -> list[str]:
    """The discovered classes behind a tuple of readers (test modules or classes)."""
    return [
        label for label in suite.labels
        if any(label == reader or label.startswith(reader + '.') for reader in readers)
    ]


def _names(mentioned: set[str], changed: str) -> bool:
    """Is `changed` named — itself, something inside it, or the package it lives in?"""
    return any(
        item == changed or item.startswith(changed + '.') or changed.startswith(item + '.')
        for item in mentioned
    )


def select(files: list[str], suite, *, base: str | None = None, wide: bool = False):
    """
    → (labels, reasons, whole_suite_reason)

    `suite` is `ci_shards.discover()`: only classes the runner really finds are
    ever returned, so a label printed here is a label `manage.py test` takes.
    """
    known = set(suite.labels)
    whole = None
    reasons: dict[str, str] = {}

    def add(label, why):
        if label in known:
            reasons.setdefault(label, why)

    test_modules = {label.rsplit('.', 1)[0] for label in known}
    parsed = {}
    for module in sorted(test_modules):
        path = BACKEND.joinpath(*module.split('.')).with_suffix('.py')
        if path.exists():
            key = (path, path.stat().st_mtime_ns)
            if key not in _READ:
                _READ[key] = read_test_module(path)
            parsed[module] = _READ[key]

    relevant = [
        name for name in files
        if name.startswith('backend/') or any(fnmatch.fnmatch(name, pattern) for pattern in EXTERNAL_INPUTS)
    ]
    changed_modules: dict[str, str] = {}
    for name in relevant:
        for pattern in EVERYTHING:
            if fnmatch.fnmatch(name, pattern):
                whole = whole or f'{name} reaches every test'
        for pattern, readers in {**EXTERNAL_INPUTS, **READ_BY_PATH}.items():
            if fnmatch.fnmatch(name, pattern):
                for label in readers_of(readers, suite):
                    add(label, f'reads {pattern}')
        module = module_of(name)
        if module in parsed:
            classes = parsed[module]
            lines = changed_lines(base, name) if base else None
            inside = [found for found in classes if lines and any(found.first <= line <= found.last for line in lines)]
            outside = lines is None or any(
                not any(found.first <= line <= found.last for found in classes) for line in lines
            )
            for found in (classes if outside else inside):
                add(found.label, f'{name} changed')
        elif module:
            changed_modules[module] = name
        # A file a test names in a string (a template, a Dockerfile, an example).
        if not name.endswith('.py'):
            for classes in parsed.values():
                for found in classes:
                    if any(Path(name).name in text or name.split('/', 1)[-1] in text for text in found.text):
                        add(found.label, f'names {Path(name).name}')

    if changed_modules:
        _django()
        importers = {
            module: {view for view in _all_view_modules(parsed) if _names(imports_of(view), module)}
            for module in (changed_modules if wide else ())
        }
        for classes in parsed.values():
            for found in classes:
                reached = None
                for module, name in changed_modules.items():
                    command = module.rpartition('.management.commands.')[2] if '.management.commands.' in module else None
                    if _names(found.modules, module) or (command and command in found.text):
                        add(found.label, f'names {module}')
                        continue
                    reached = view_modules(found.urls) if reached is None else reached
                    if _names(reached, module):
                        add(found.label, f'calls a view of {module}')
                    elif wide and reached & importers[module]:
                        add(found.label, f'calls a view that imports {module}')

    labels = [label for label in suite.labels if label in reasons]
    if not whole and labels and suite.count(labels) > TOO_WIDE * len(suite.ids):
        whole = f'the change reaches {suite.count(labels)} of {len(suite.ids)} tests'
    return labels, reasons, whole


_VIEW_MODULES: set[str] | None = None


def _all_view_modules(parsed) -> set[str]:
    global _VIEW_MODULES
    if _VIEW_MODULES is None:
        urls = set()
        for classes in parsed.values():
            for found in classes:
                urls |= found.urls
        _VIEW_MODULES = view_modules(urls)
    return _VIEW_MODULES


def _django():
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.settings')
    import django
    from django.apps import apps

    if not apps.ready:
        django.setup()


def _ci_shards():
    import importlib.util

    spec = importlib.util.spec_from_file_location('ci_shards', Path(__file__).with_name('ci_shards.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0].strip())
    parser.add_argument('--base', default='origin/master', help='compare with this commit (default: origin/master)')
    parser.add_argument('--files', nargs='+', help='use these paths (from the repository root) instead of git')
    parser.add_argument('--wide', action='store_true', help='also the tests one import away')
    parser.add_argument('--run', action='store_true', help='run the selection with manage.py test')
    parser.add_argument('--why', action='store_true', help='say why each class was chosen')
    args = parser.parse_args(argv)

    files = args.files or changed_files(args.base)
    suite = _ci_shards().discover()
    labels, reasons, whole = select(files, suite, base=None if args.files else args.base, wide=args.wide)

    say = lambda text: print(text, file=sys.stderr)  # noqa: E731
    say(f'{len(files)} changed file(s); {len(labels)} of {len(suite.labels)} classes, '
        f'{suite.count(labels)} of {len(suite.ids)} tests')
    say('A convenience, not a gate: CI runs the whole suite.')
    if whole:
        say(f'WHOLE SUITE: {whole}.')
        if args.run:
            return subprocess.call([sys.executable, 'manage.py', 'test', '--noinput'], cwd=BACKEND)
        return WHOLE_SUITE
    if not labels:
        say('Nothing in the backend suite names what changed.')
        return NOTHING
    for label in labels:
        print(f'{label}  # {reasons[label]}' if args.why else label)
    if args.run:
        return subprocess.call([sys.executable, 'manage.py', 'test', *labels, '--noinput'], cwd=BACKEND)
    return 0


if __name__ == '__main__':
    sys.exit(main())
