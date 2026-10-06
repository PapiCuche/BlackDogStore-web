"""
The backup and restore scripts, run for real against a stand-in for Docker.

`deploy/backup.sh` and `deploy/restore.sh` take the command they drive from the
`COMPOSE` variable (the rehearsal uses that to point them at its own project).
Here it points at a small script that plays PostgreSQL and the backend container,
so the scripts' own decisions can be tested: what they keep, what they refuse,
and in which order.

The full path — a real database dropped and restored, real files deleted and
brought back — is `sh deploy/rehearsal.sh`, steps 18 and 19.
"""
import gzip
import io
import os
import subprocess
import tarfile
import tempfile
from pathlib import Path
from unittest import skipUnless

from django.conf import settings
from django.test import SimpleTestCase

ROOT = Path(settings.BASE_DIR).parent
BACKUP = ROOT / 'deploy' / 'backup.sh'
RESTORE = ROOT / 'deploy' / 'restore.sh'
HEALTH = ROOT / 'deploy' / 'healthcheck.sh'
COMPOSE_FILE = ROOT / 'docker-compose.prod.yml'

#: Plays `docker compose`. Every call is appended to $STUB_LOG. The dump is a
#: complete one; the archive of stored files is whatever $STUB_FILES says.
STUB = r'''#!/bin/sh
echo "$*" >> "$STUB_LOG"
case "$*" in
  *"postgres sh -c"*pg_dump*)
    printf -- '-- data\n--\n-- PostgreSQL database dump complete\n--\n'
    n=0; while [ "$n" -lt "${STUB_TRAILER:-0}" ]; do printf '\\unrestrict x\n'; n=$((n + 1)); done ;;
  *"ps --format"*)
    printf 'postgres running healthy\nbackend running %s\nfrontend running healthy\ncaddy running \n' "${STUB_BACKEND:-healthy}" ;;
  *pg_isready*)
    exit "${STUB_DB:-0}" ;;
  *"manage.py ops_status"*)
    printf '%s\n' "${STUB_OPS:-OK    pagos: sin notificaciones rechazadas ni pagos esperando}"
    exit "${STUB_OPS_RC:-0}" ;;
  *"backend tar -czf"*)
    case "$STUB_FILES" in
      ok)      cat "$STUB_ARCHIVE" ;;
      cut)     head -c 40 "$STUB_ARCHIVE"; exit 1 ;;
      garbage) printf 'this is not an archive' ;;
    esac ;;
esac
exit 0
'''


def _archive() -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:gz') as tar:
        data = b'\x89PNG stand-in'
        info = tarfile.TarInfo('private-media/companies/1/storefront/a.png')
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


class _Scripts(SimpleTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)
        self.dest = self.work / 'backups'
        self.log = self.work / 'calls.log'
        self.log.write_text('')
        (self.work / 'stub.sh').write_text(STUB)
        (self.work / 'archive.tar.gz').write_bytes(_archive())
        (self.work / 'curl.sh').write_text('#!/bin/sh\nprintf "%s" "$STUB_HTTP"\n')

    def run_script(self, script, *args, files='ok', stdin='', **extra):
        env = {
            'PATH': os.environ['PATH'],
            'COMPOSE': f"sh {self.work / 'stub.sh'}",
            'BACKUP_DIR': str(self.dest),
            'STUB_LOG': str(self.log),
            'STUB_FILES': files,
            'STUB_ARCHIVE': str(self.work / 'archive.tar.gz'),
            # healthcheck.sh: the site is asked through this instead of curl.
            'HEALTH_CURL': f"sh {self.work / 'curl.sh'}",
            'SITE_DOMAIN': 'tienda.example',
            'STUB_HTTP': '200',
        }
        env.update(extra)
        return subprocess.run(
            ['sh', str(script), *args], cwd=ROOT, env=env, input=stdin,
            capture_output=True, text=True, timeout=60,
        )

    def kept(self, pattern):
        return sorted(p.name for p in self.dest.glob(pattern)) if self.dest.exists() else []

    def calls(self):
        return self.log.read_text()


@skipUnless(BACKUP.exists(), 'deploy/ is not part of this checkout')
class DeployScriptsTest(_Scripts):
    # -- backup ---------------------------------------------------------------

    def test_a_complete_backup_keeps_both_files_and_says_when(self):
        result = self.run_script(BACKUP)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.kept('db-*.sql.gz')), 1)
        self.assertEqual(len(self.kept('evidence-*.tar.gz')), 1)
        with tarfile.open(self.dest / self.kept('evidence-*.tar.gz')[0]) as tar:
            self.assertEqual(tar.getnames(), ['private-media/companies/1/storefront/a.png'])
        # What a monitor reads to know the last good backup: its time, nothing else.
        marker = self.dest / 'LAST_OK'
        self.assertTrue(marker.exists(), 'a successful backup must leave backups/LAST_OK')
        self.assertRegex(marker.read_text().strip(), r'^\d{8}-\d{6}$')

    def test_a_dump_with_a_longer_trailer_is_still_a_complete_dump(self):
        """
        pg_dump ends with its "dump complete" mark and then some lines of its
        own; how many depends on the version. Looking only at the very last ones
        would throw away every good dump the day a version adds one.
        """
        result = self.run_script(BACKUP, STUB_TRAILER='12')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.kept('db-*.sql.gz')), 1)

    def test_an_archive_of_files_that_was_cut_short_is_not_kept(self):
        result = self.run_script(BACKUP, files='cut')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.kept('evidence-*.tar.gz'), [], 'a truncated archive was left looking like a backup')
        self.assertFalse((self.dest / 'LAST_OK').exists())
        self.assertIn('ERROR', result.stderr)

    def test_an_archive_that_is_not_one_is_not_kept_either(self):
        result = self.run_script(BACKUP, files='garbage')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.kept('evidence-*.tar.gz'), [])
        self.assertFalse((self.dest / 'LAST_OK').exists())

    def test_a_failed_backup_does_not_erase_the_time_of_the_last_good_one(self):
        self.assertEqual(self.run_script(BACKUP).returncode, 0)
        first = (self.dest / 'LAST_OK').read_text()
        self.assertNotEqual(self.run_script(BACKUP, files='cut').returncode, 0)
        self.assertEqual((self.dest / 'LAST_OK').read_text(), first)

    # -- restore --------------------------------------------------------------

    def _dump(self):
        path = self.work / 'db-20260101-000000.sql.gz'
        path.write_bytes(gzip.compress(b'-- PostgreSQL database dump complete\n'))
        return path

    def test_a_restore_that_names_a_missing_archive_touches_nothing(self):
        """
        The archive used to be looked for AFTER the database had been dropped and
        replaced: a mistyped second argument left the shop stopped, with the old
        data gone and the files not restored.
        """
        result = self.run_script(RESTORE, str(self._dump()), str(self.work / 'no-existe.tar.gz'), stdin='RESTAURAR\n')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.calls(), '', 'the restore started before checking its inputs')

    def test_a_restore_that_names_a_damaged_archive_touches_nothing(self):
        damaged = self.work / 'evidence-x.tar.gz'
        damaged.write_bytes(b'not gzip')
        result = self.run_script(RESTORE, str(self._dump()), str(damaged), stdin='RESTAURAR\n')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.calls(), '')

    def test_a_restore_without_the_word_changes_nothing(self):
        result = self.run_script(RESTORE, str(self._dump()), str(self.work / 'archive.tar.gz'), stdin='si\n')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.calls(), '')

    def test_a_confirmed_restore_backs_up_first_then_replaces_database_and_files(self):
        result = self.run_script(RESTORE, str(self._dump()), str(self.work / 'archive.tar.gz'), stdin='RESTAURAR\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        order = [calls.index(marker) for marker in ('pg_dump', 'stop caddy frontend backend', 'DROP DATABASE', 'tar -xzf', 'up -d')]
        self.assertEqual(order, sorted(order), calls)


@skipUnless(BACKUP.exists(), 'deploy/ is not part of this checkout')
class HealthCheckTest(_Scripts):
    """
    `deploy/healthcheck.sh`: one pass over what can go wrong without anybody
    noticing. It prints one line per check and exits non-zero when any needs a
    person, so a scheduler can send what it prints.
    """

    def healthy(self, **extra):
        self.assertEqual(self.run_script(BACKUP).returncode, 0)
        return self.run_script(HEALTH, **extra)

    def test_everything_up_and_a_backup_from_today_is_all_right(self):
        result = self.healthy()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn('ATENCIÓN', result.stdout)
        for line in ('contenedor backend', 'contenedor caddy', 'https://tienda.example/api/categories',
                     'base de datos', 'pagos', 'copia de seguridad', 'disco'):
            self.assertIn(line, result.stdout)

    def test_a_container_that_is_not_healthy_is_reported(self):
        result = self.healthy(STUB_BACKEND='unhealthy')
        self.assertEqual(result.returncode, 1)
        self.assertIn('ATENCIÓN contenedor backend', result.stdout)

    def test_a_site_that_does_not_answer_200_is_reported(self):
        result = self.healthy(STUB_HTTP='502')
        self.assertEqual(result.returncode, 1)
        self.assertIn('ATENCIÓN https://tienda.example/ responde 502', result.stdout)

    def test_a_database_that_does_not_answer_is_reported(self):
        result = self.healthy(STUB_DB='1')
        self.assertEqual(result.returncode, 1)
        self.assertIn('ATENCIÓN la base de datos no responde', result.stdout)

    def test_what_the_application_reports_is_passed_on(self):
        result = self.healthy(STUB_OPS='ATENCIÓN pagos: 1 pago sin respuesta de la pasarela', STUB_OPS_RC='1')
        self.assertEqual(result.returncode, 1)
        self.assertIn('ATENCIÓN pagos: 1 pago sin respuesta de la pasarela', result.stdout)

    def test_an_application_that_cannot_be_asked_is_reported(self):
        result = self.healthy(STUB_OPS='Traceback (most recent call last):', STUB_OPS_RC='1')
        self.assertEqual(result.returncode, 1)
        self.assertIn('ATENCIÓN la aplicación no pudo informar de su estado', result.stdout)

    def test_the_domain_is_read_from_the_environment_file_whatever_its_quotes(self):
        env_file = self.work / 'env.production'
        for line in ('SITE_DOMAIN="tienda.example"', "SITE_DOMAIN='tienda.example'", 'SITE_DOMAIN=tienda.example\r'):
            env_file.write_text(f'POSTGRES_USER=x\n{line}\n')
            result = self.healthy(SITE_DOMAIN='', ENV_FILE=str(env_file))
            self.assertIn('OK    https://tienda.example/api/categories', result.stdout, line)

    def test_no_backup_at_all_is_reported(self):
        result = self.run_script(HEALTH)
        self.assertEqual(result.returncode, 1)
        self.assertIn('ATENCIÓN copia de seguridad: no hay ninguna copia completa', result.stdout)

    def test_a_backup_that_stopped_happening_is_reported(self):
        self.assertEqual(self.run_script(BACKUP).returncode, 0)
        marker = self.dest / 'LAST_OK'
        two_days_ago = marker.stat().st_mtime - 48 * 3600
        os.utime(marker, (two_days_ago, two_days_ago))
        result = self.run_script(HEALTH)
        self.assertEqual(result.returncode, 1)
        self.assertIn('ATENCIÓN copia de seguridad: la última copia completa tiene más de 26 horas', result.stdout)


@skipUnless(COMPOSE_FILE.exists(), 'docker-compose.prod.yml is not part of this checkout')
class ComposeFileTest(SimpleTestCase):
    def test_no_container_can_fill_the_disk_with_its_own_log(self):
        """
        LOG-ROTATION. Docker keeps what a container prints in one file that
        grows without limit. The backend prints a line per request and one per
        health check: left alone, the log is what fills the disk, and a full
        disk is a database that stops.
        """
        import re

        text = COMPOSE_FILE.read_text(encoding='utf-8')
        anchor = re.search(r'x-logging: &([\w-]+)\n((?:  .*\n)+)', text)
        self.assertIsNotNone(anchor, 'the compose file declares no shared log settings')
        self.assertRegex(anchor.group(2), r'max-size: "\d+m"')
        self.assertRegex(anchor.group(2), r'max-file: "\d+"')
        services = re.search(r'\nservices:\n(.*?)\nvolumes:\n', text, re.S).group(1)
        names = re.findall(r'(?m)^  (\w+):\n', services)
        self.assertEqual(sorted(names), ['backend', 'caddy', 'frontend', 'postgres'])
        self.assertEqual(services.count(f'logging: *{anchor.group(1)}'), len(names))


@skipUnless(BACKUP.exists(), 'deploy/ is not part of this checkout')
class CrontabExampleTest(SimpleTestCase):
    def test_every_job_can_write_its_log_on_a_server_that_never_ran_a_backup(self):
        """
        The shell opens a job's log before it runs the job. On a new server
        `backups/` does not exist until the first backup: every job failed on
        its redirect, the health check that would have said so included.
        """
        lines = [line for line in (ROOT / 'deploy' / 'crontab.example').read_text(encoding='utf-8').splitlines()
                 if line and not line.startswith('#') and 'cd $REPO' in line]
        self.assertEqual(len(lines), 5)
        for line in lines:
            self.assertIn('cd $REPO && mkdir -p backups && ', line)
            self.assertNotIn('%', line, 'cron reads % as a newline')
