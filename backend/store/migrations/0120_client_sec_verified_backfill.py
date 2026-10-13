from django.db import migrations
from django.db.models import DateTimeField, F, Min, OuterRef, Subquery
from django.db.models.functions import Least


def record_verified_addresses(apps, schema_editor):
    """
    `UserProfile.email_verified_at` for the accounts whose mailbox was ALREADY
    proven when the column arrived. Everybody else stays null: unknown is not
    verified, and «active» was never the proof (an account is active wherever
    verification is not asked).

    Two proofs were written down before this column existed:

      * a link that was sent to the mailbox and USED — a verification link or
        code, or a recovery link — its `used_at`, on an account that is active
        today;
      * a Google identity — the day it was linked — while the account still has
        the address Google vouched for that day.

    The earliest of the two is kept. A profile that already has a date keeps it.

    ONE STATEMENT, IN ITS OWN MIGRATION. The column is added by 0118; filling it
    there would hold the `ALTER TABLE` lock of `store_userprofile` for as long
    as the fill takes, while the release before this one is still serving.
    """
    AccountToken = apps.get_model('store', 'AccountToken')
    ExternalIdentity = apps.get_model('store', 'ExternalIdentity')
    UserProfile = apps.get_model('store', 'UserProfile')

    used_link = Subquery(
        AccountToken.objects
        .filter(user_id=OuterRef('user_id'), used_at__isnull=False, user__is_active=True)
        .order_by().values('user_id').annotate(first=Min('used_at')).values('first'),
        output_field=DateTimeField(),
    )
    google = Subquery(
        ExternalIdentity.objects
        .filter(user_id=OuterRef('user_id'), provider='google')
        .exclude(email_at_link='').filter(email_at_link__iexact=F('user__email'))
        .order_by().values('user_id').annotate(first=Min('created_at')).values('first'),
        output_field=DateTimeField(),
    )
    # LEAST skips a proof that is not there, and is null when neither is.
    proven = Least(used_link, google, output_field=DateTimeField())
    (UserProfile.objects
     .filter(email_verified_at__isnull=True)
     .annotate(proven=proven).filter(proven__isnull=False)
     .update(email_verified_at=proven))


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0119_delivery_base'),
    ]

    operations = [
        # Backwards there is nothing to undo: the column goes with its dates (0118).
        migrations.RunPython(record_verified_addresses, migrations.RunPython.noop),
    ]
