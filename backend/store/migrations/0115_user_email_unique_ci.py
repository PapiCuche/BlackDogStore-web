"""
REGISTER-RACE-01 · one e-mail address, one account — enforced by the database.

`auth_user.email` had no uniqueness at all. Registration asked «is it taken?»
and then inserted; two requests at the same instant both heard «no».

THE INDEX IS ON UPPER(email), AND THAT IS NOT A DETAIL. Every lookup in this
code is `email__iexact`, which PostgreSQL runs as UPPER(email) = UPPER(%s). An
index on LOWER(email) is a different rule on a UTF-8 database: «ınfo@» and
«info@» are one address for every lookup (UPPER of both is «INFO@») and two for
LOWER — two accounts under what the code treats as one address, which is the
very thing this exists to prevent. The rule and the lookups share one
expression, and the duplicates are counted with it too.

Accounts WITHOUT an address are left out: there may be many.

IF DUPLICATES ALREADY EXIST THIS MIGRATION STOPS. It merges nothing and deletes
nothing: which of two accounts stays is a person's decision — one may hold the
orders and the other the password somebody remembers.
`python manage.py list_duplicate_emails` lists them. What it says when it stops
is how many, never which: this text ends up in a deployment log.

The user model is Django's own, so the rule is SQL here and not in a model. A
future migration that REBUILDS `auth_user` on SQLite (development only) would
drop it without a word; PostgreSQL alters in place.
"""
from django.db import DatabaseError, migrations

INDEX = 'store_user_email_ci_uniq'
KEY = 'UPPER(email)'

_STOPPED = (
    'REGISTER-RACE-01: {how_many} tienen más de una cuenta. La regla de unicidad no se puede crear y esta '
    'migración no fusiona ni borra nada. Lista las cuentas con «python manage.py list_duplicate_emails», '
    'decide cuál se conserva y vuelve a migrar.'
)


def _repeated(schema_editor) -> int:
    """How many addresses have more than one account, counted as the rule will compare them."""
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            f"SELECT COUNT(*) FROM (SELECT {KEY} FROM auth_user WHERE email <> '' "
            f"GROUP BY {KEY} HAVING COUNT(*) > 1) repeated"
        )
        return cursor.fetchone()[0]


def create_rule(apps, schema_editor):
    repeated = _repeated(schema_editor)
    if repeated:
        raise RuntimeError(_STOPPED.format(how_many=f'{repeated} correo(s)'))
    try:
        schema_editor.execute(f"CREATE UNIQUE INDEX {INDEX} ON auth_user ({KEY}) WHERE email <> ''")
    except DatabaseError:
        # A duplicate committed between the count and the index: the previous
        # release keeps serving while this runs. The database's own message
        # quotes the address, so it is not passed on (`from None`).
        raise RuntimeError(_STOPPED.format(how_many='uno o más correos')) from None


def drop_rule(apps, schema_editor):
    schema_editor.execute(f'DROP INDEX {INDEX}')


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0114_product_is_published_online'),
        ('auth', '0012_alter_user_first_name_max_length'),
    ]

    operations = [
        migrations.RunPython(create_rule, drop_rule),
    ]
