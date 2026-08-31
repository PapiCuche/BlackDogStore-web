"""
Deployment checks — P0 stabilisation.

WHY THIS FILE EXISTS
--------------------
The runtime failure this phase fixed was not a bug in any of these modules. The
code was correct and its 1429 tests passed. The development database was simply
ten migrations behind, so `tenancy.py` asked for a column that did not exist and
three public pages answered 500.

The test suite could not have caught it, and never will: Django builds a FRESH
database for the test run and applies every migration to it. A green suite says
"this code is consistent with these migrations". It says nothing whatsoever
about the database the server is actually connected to.

So the gap is not a missing test. It is a missing check on the ENVIRONMENT, and
that is what this is: `runserver` now says the schema is behind at startup,
instead of the application discovering it three page-loads later as a 500 with a
column name in it.

WHAT THIS DELIBERATELY DOES NOT DO
----------------------------------
It does not apply anything. A check that silently migrated would be worse than
the failure it replaces — migrations are a deployment decision, some of them
carry data changes, and one of ours (0025) is designed to stop and ask rather
than guess where historical stock lives.

It also does not swallow the underlying error. If a query later fails because a
column is missing, that still raises. This only makes the cause visible first.
"""

from django.core.checks import Warning as CheckWarning
from django.core.checks import register


@register('database')
def check_pending_migrations(app_configs, **kwargs):
    """
    Warn when the connected database is behind the migrations in the code.

    Registered under the `database` tag, so it runs on `manage.py check
    --database default`, on `runserver`, and can be skipped where a database is
    genuinely not reachable (a build container, a collectstatic step).
    """
    from django.db import DEFAULT_DB_ALIAS, connections
    from django.db.migrations.executor import MigrationExecutor

    connection = connections[DEFAULT_DB_ALIAS]

    try:
        executor = MigrationExecutor(connection)
        targets = executor.loader.graph.leaf_nodes()
        plan = executor.migration_plan(targets)
    except Exception:
        # No database yet, no permission, or a broken connection. That is a
        # different problem with its own error, and guessing about it here would
        # produce a misleading warning on top of a real one.
        return []

    if not plan:
        return []

    pending = [f'{migration.app_label}.{migration.name}' for migration, _backwards in plan]

    listed = ', '.join(pending[:12])
    if len(pending) > 12:
        listed += f', … (+{len(pending) - 12})'

    return [
        CheckWarning(
            f'La base de datos tiene {len(pending)} migración(es) sin aplicar.',
            hint=(
                'El código espera tablas y columnas que la base de datos todavía '
                'no tiene, así que endpoints válidos van a responder 500 con '
                '"no such column" / "no such table".\n'
                f'Pendientes: {listed}\n'
                'Ejecuta: python manage.py migrate\n'
                'Antes de migrar en un entorno con datos reales, haz copia de '
                'seguridad. La migración 0025 se detiene y pide '
                'INVENTORY_MIGRATION_BRANCHES si una empresa tiene varias '
                'sucursales activas y su stock histórico es ambiguo — eso es '
                'deliberado, no un fallo.'
            ),
            id='store.W001',
        )
    ]
