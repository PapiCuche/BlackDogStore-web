"""
Guard: a test module must not delete a module-level temporary directory in tearDownModule.

`manage.py test --parallel N` forks its workers after the test modules are imported, so a directory created at import
time (`_ROOT = tempfile.mkdtemp(...)`) is shared by every worker. unittest runs tearDownModule once per worker that ran
part of the module: the first worker to finish deleted the directory while another worker's test was still using it,
and that test failed with «el archivo desapareció del almacenamiento» depending on timing (Phase 1 final rehearsal,
2026-11-21). The fix: `atexit.register(shutil.rmtree, _ROOT, True)`, which only the creating process runs.
"""
import re
from pathlib import Path

from django.test import SimpleTestCase

HERE = Path(__file__).resolve().parent
MODULE_TEMPDIR = re.compile(r'^(\w+) = tempfile\.mkdtemp\(', re.M)
TEARDOWN = re.compile(r'^def tearDownModule\(\):\n((?:[ \t]+.*\n|\n)+)', re.M)


def offenders(root=HERE):
    found = []
    for path in sorted(root.glob('test*.py')):
        text = path.read_text(encoding='utf-8')
        names = MODULE_TEMPDIR.findall(text)
        if not names:
            continue
        for body in TEARDOWN.findall(text):
            for name in names:
                if re.search(rf'rmtree\(\s*{re.escape(name)}\b', body):
                    found.append(f'{path.name}: tearDownModule borra {name}')
    return found


class ParallelTempdirGuardTest(SimpleTestCase):
    def test_no_module_deletes_a_shared_tempdir_in_teardown(self):
        self.assertEqual(offenders(), [], 'usar atexit.register(shutil.rmtree, _ROOT, True) en vez de tearDownModule')

    def test_the_guard_catches_the_old_pattern(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, 'test_bad.py').write_text(
                "import shutil\nimport tempfile\n_ROOT = tempfile.mkdtemp(prefix='x-')\n\n\n"
                "def tearDownModule():\n    shutil.rmtree(_ROOT, ignore_errors=True)\n", encoding='utf-8')
            Path(tmp, 'test_good.py').write_text(
                "import atexit\nimport shutil\nimport tempfile\n_ROOT = tempfile.mkdtemp(prefix='x-')\n"
                "atexit.register(shutil.rmtree, _ROOT, True)\n", encoding='utf-8')
            self.assertEqual(offenders(Path(tmp)), ['test_bad.py: tearDownModule borra _ROOT'])
