from django.db import migrations

#: The delivery record's mode for each delivery method an order can have. Frozen
#: here: a migration says what was true the day it ran, whatever the code says later.
MODE_BY_METHOD = {
    'pickup_store': 'pickup_store',
    'delivery_arequipa': 'local_delivery',
    'national_shipping': 'national_address',
}
BATCH = 500


def record_existing_deliveries(apps, schema_editor):
    """
    One `OrderDelivery` for every ONLINE order that already says how it is
    delivered, copied from the order's own columns exactly as a new checkout
    writes it: the mode from the delivery method, the recipient from the buyer
    of the order, the address as typed.

    Not for a sale at the till (it has no delivery), nor for an order with no
    delivery method (there is nothing to copy). In batches; an order that
    already has a record keeps it, so the step can run twice.
    """
    Order = apps.get_model('store', 'Order')
    OrderDelivery = apps.get_model('store', 'OrderDelivery')

    orders = (
        Order.objects
        .filter(sales_channel='online', delivery_method__in=list(MODE_BY_METHOD), delivery_record__isnull=True)
        .order_by('pk')
        .values('pk', 'company_id', 'delivery_method', 'customer_name', 'customer_phone',
                'document_type', 'document_number', 'address_line', 'district', 'city', 'reference')
    )
    batch = []
    for row in orders.iterator(chunk_size=BATCH):
        batch.append(OrderDelivery(
            company_id=row['company_id'], order_id=row['pk'], mode=MODE_BY_METHOD[row['delivery_method']],
            recipient_name=row['customer_name'] or '', recipient_phone=row['customer_phone'] or '',
            recipient_document_type=row['document_type'] or '',
            recipient_document_number=row['document_number'] or '',
            address_line=row['address_line'] or '', district=row['district'] or '',
            city=row['city'] or '', reference=row['reference'] or '',
        ))
        if len(batch) >= BATCH:
            OrderDelivery.objects.bulk_create(batch, ignore_conflicts=True)
            batch = []
    if batch:
        OrderDelivery.objects.bulk_create(batch, ignore_conflicts=True)


class Migration(migrations.Migration):
    """
    The records of the orders that already existed, AFTER the tables (0119) and
    apart from them: creating `OrderDelivery` adds a foreign key to
    `store_order`, and filling the table in that same transaction would keep
    that lock while every existing order is read. Not atomic, so each batch is
    its own short transaction; the step skips what it already wrote and can be
    run again after an interruption.

    BACKWARDS IT DOES NOTHING. Once the shop works with these records they hold
    what staff wrote — carriers, waybills, freight — and «un-apply the backfill»
    must not mean «delete all of it». Un-applying 0119 drops the table; that is
    the step that removes the records, and it says so by what it is.
    """

    atomic = False

    dependencies = [
        ('store', '0120_client_sec_verified_backfill'),
    ]

    operations = [
        migrations.RunPython(record_existing_deliveries, migrations.RunPython.noop),
    ]
