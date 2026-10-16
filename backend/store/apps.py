from django.apps import AppConfig


class StoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'store'

    def ready(self):
        import store.checks  # noqa: F401  — registers the deployment checks
        import store.signals  # noqa: F401
        _teach_random_uuid_sqlite()


def _teach_random_uuid_sqlite():
    """
    Migration 0131 sets `StockUnit.public_id`'s database default to
    `RandomUUID()` (PostgreSQL's `gen_random_uuid()`). The local development
    database is SQLite, which lacks it, so `migrate` stopped there. An applied
    migration is not edited: SQLite gets its own form — 32 random hex digits,
    the way Django stores a UUIDField on SQLite. PostgreSQL is untouched.
    """
    from django.contrib.postgres.functions import RandomUUID

    # `as_sqlite` is inherited from SQLiteNumericMixin (a numeric cast that
    # still emits gen_random_uuid()); only RandomUUID's own definition counts.
    if 'as_sqlite' not in RandomUUID.__dict__:
        RandomUUID.as_sqlite = lambda self, compiler, connection, **extra: ('(lower(hex(randomblob(16))))', [])
