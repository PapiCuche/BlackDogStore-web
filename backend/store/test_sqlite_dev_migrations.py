"""
DEVICE-COMMERCE-01 — the local development database is SQLite.

Migration 0131 gives `StockUnit.public_id` a database default of
`django.contrib.postgres.functions.RandomUUID()` (`gen_random_uuid()`), which
SQLite does not have: `migrate` stopped with «unknown function:
GEN_RANDOM_UUID()» on every SQLite database. An applied migration is not
edited; the app teaches that one function how to read on SQLite instead.
"""
import re
import sqlite3

from django.contrib.postgres.functions import RandomUUID
from django.test import SimpleTestCase


class RandomUuidOnSqliteTest(SimpleTestCase):
    def test_the_function_has_a_sqlite_form(self):
        self.assertIn('as_sqlite', RandomUUID.__dict__)

    def test_that_form_runs_on_sqlite_and_gives_a_new_32_hex_value_each_time(self):
        sql, params = RandomUUID().as_sqlite(None, None)
        db = sqlite3.connect(':memory:')
        first, second = (db.execute(f'SELECT {sql}', params).fetchone()[0] for _ in range(2))
        self.assertRegex(first, re.compile(r'^[0-9a-f]{32}$'))
        self.assertNotEqual(first, second)
