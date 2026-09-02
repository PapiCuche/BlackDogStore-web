"""
P0-F — carry the previous gateway's identifiers into PaymentTransaction.

WHY THE OLD IDENTIFIERS ARE NOT SIMPLY DROPPED. They are the only record of how
a historical order was paid. If a customer disputes a charge from before this
migration, "which transaction was that" has exactly one answer and it is in
these two columns. Deleting them to make a `git grep` come out clean would be
tidying up the evidence.

So each historical order that carries one becomes a PaymentTransaction row with
`provider` set to the OLD provider's name. That is data about the past, not a
live integration: no code branches on the value, nothing imports that provider,
and a row saying a payment was taken through it in 2025 remains true.

`order_number` is left NULL for these. The old provider had no such concept, and
inventing one would put a fabricated number into the field that a future
reconciliation would read as real.
"""

from django.db import migrations

# The provider these historical rows were charged through. A STRING IN A DATA
# MIGRATION, not an import and not a constant anyone can call: the integration
# is gone, and this is only the name that was true when the row was written.
LEGACY_PROVIDER = 'stripe'


def carry_legacy_payments_forward(apps, schema_editor):
    Order = apps.get_model('store', 'Order')
    PaymentTransaction = apps.get_model('store', 'PaymentTransaction')

    carried = 0
    for order in Order.objects.exclude(
        stripe_session_id=None, stripe_payment_intent_id='',
    ).iterator():
        session_id = (order.stripe_session_id or '').strip()
        intent_id = (order.stripe_payment_intent_id or '').strip()
        identifier = session_id or intent_id
        if not identifier:
            continue

        # Idempotent: re-running must not create a second row for one payment.
        if PaymentTransaction.objects.filter(
            provider=LEGACY_PROVIDER, transaction_id=identifier,
        ).exists():
            continue

        PaymentTransaction.objects.create(
            order=order,
            provider=LEGACY_PROVIDER,
            transaction_id=identifier,
            order_number=None,
            amount=order.total,
            currency='PEN',
            # The order's own status is the truth about whether money arrived.
            # An order that was paid had an authorised payment behind it; one
            # that was not is left pending rather than guessed at.
            status='authorized' if order.paid else 'pending',
            reference_number=intent_id[:64],
            signature_verified=False,
            confirmed_at=order.paid_at,
            failure_reason='',
        )
        carried += 1

    if carried:
        print(f'\n  P0-F — {carried} pago(s) histórico(s) migrado(s) a PaymentTransaction.')


def rename_online_payment_method(apps, schema_editor):
    """
    `payment_method='stripe'` becomes `'online'`.

    The value named a company; the column means a CHANNEL. Every one of these
    rows is an order taken through the storefront's gateway, which is exactly
    what `online` says, so this loses nothing — and it stops the domain
    vocabulary carrying a provider's name into every report and receipt.
    """
    Order = apps.get_model('store', 'Order')
    renamed = Order.objects.filter(payment_method=LEGACY_PROVIDER).update(
        payment_method='online',
    )
    if renamed:
        print(f'\n  P0-F — {renamed} pedido(s) con método de pago renombrado a "online".')


def unrename_online_payment_method(apps, schema_editor):
    """
    Deliberately a no-op.

    `online` is not reversible to `stripe`: after this phase, an order paid
    through Izipay is also `online`, and sending every one of them back to a
    provider that never touched them would be inventing history to satisfy a
    downgrade nobody is performing.
    """


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0043_payment_transaction'),
    ]

    operations = [
        migrations.RunPython(
            carry_legacy_payments_forward,
            migrations.RunPython.noop,
        ),
        migrations.RunPython(
            rename_online_payment_method,
            unrename_online_payment_method,
        ),
    ]
