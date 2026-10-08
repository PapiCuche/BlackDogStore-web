"""
REGISTER-RACE-01 · one e-mail address, one account — enforced by the database.

`auth_user.email` had no uniqueness at all. Registration asked «is it taken?»
and then inserted; two requests at the same instant both heard «no». The index
is on LOWER(email), because that is how an address is compared everywhere in
this code (`email__iexact`), and it leaves out accounts WITHOUT an address,
which may be many.

IF DUPLICATES ALREADY EXIST THIS MIGRATION STOPS. It merges nothing and deletes
nothing: which of two accounts stays is a person's decision — one may hold the
orders and the other the password somebody remembers.
`python manage.py list_duplicate_emails` lists them.
"""
from django.db import migrations

INDEX = 'store_user_email_ci_uniq'


def refuse_duplicates(apps, schema_editor):
    from collections import Counter

    User = apps.get_model('auth', 'User')
    seen = Counter(
        (email or '').strip().lower()
        for email in User.objects.exclude(email='').values_list('email', flat=True)
    )
    repeated = sum(1 for key, count in seen.items() if key and count > 1)
    if repeated:
        # How many, never which: this text ends up in a deployment log.
        raise RuntimeError(
            f'REGISTER-RACE-01: {repeated} correo(s) tienen más de una cuenta. La regla de unicidad no se '
            f'puede crear y esta migración no fusiona ni borra nada. Lista las cuentas con '
            f'«python manage.py list_duplicate_emails», decide cuál se conserva y vuelve a migrar.'
        )


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0114_product_is_published_online'),
        ('auth', '0012_alter_user_first_name_max_length'),
    ]

    operations = [
        migrations.RunPython(refuse_duplicates, migrations.RunPython.noop),
        migrations.RunSQL(
            sql=f"CREATE UNIQUE INDEX {INDEX} ON auth_user (LOWER(email)) WHERE email <> ''",
            reverse_sql=f'DROP INDEX {INDEX}',
        ),
    ]
