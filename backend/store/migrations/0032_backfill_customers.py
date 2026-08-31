"""
Phase 4, step 2 of 2 — BACKFILL.

Builds CRM records from orders that already exist, and attaches those orders to
them. Runs only on data this installation already has; a fresh database does
nothing here.

WHAT THIS REFUSES TO DO
-----------------------
It does not merge people by name, by email or by phone. Those look like identity
and are not: households share an inbox, offices share a landline, and two people
called "Juan Pérez" are two people. A wrong merge here is not a cosmetic defect —
it publishes one client's address, purchase history and internal notes inside
another client's file, permanently, in a migration nobody will re-read.

So the rule is: attribute an order only when the evidence is conclusive, and
leave it unattributed otherwise. An unlinked order is visibly incomplete and
fixable by hand later. A wrongly linked one looks correct forever.

THE TWO CONCLUSIVE KEYS
-----------------------
  A. The account.  `Order.user` is not null → group by (company, user).
     The buyer authenticated. There is nothing stronger.

  B. The document. `Order.document_number` is not empty → group by
     (company, document_type, document_number). Checkout has validated this
     field since Phase 4.0, so it is a real document rather than free text.

  C. Anything else → `Order.customer` stays NULL, counted and reported.

A ORDER'S PRIORITY: an order with both keys is grouped by ACCOUNT, because the
account is the identity the person proved. Its document is then offered to that
customer record, and accepted only if no other record in the company already
holds it.

WHY DOCUMENTS CAN CLASH WITH ACCOUNTS
-------------------------------------
One account may have bought twice with two different documents (their own DNI,
then their employer's RUC). Rule A puts both orders on one customer, and that
customer can only carry one document. It keeps the FIRST it can claim without
colliding; the other is not recorded on the record, and no data is lost because
every order keeps its own snapshot.

The rule is deliberately "first wins" rather than "most recent wins". The latter
was tried and produced a duplicate: refreshing the record from a later RUC order
turned a person into a business and released their DNI, which the next anonymous
order carrying that DNI then used to create a second file on the same person.
"""

from django.db import migrations


def _split_name(raw):
    name = (raw or '').strip()
    first, _, last = name.partition(' ')
    return first, last.strip()


def _norm_doc(value):
    return (value or '').strip().upper()


def _norm_email(value):
    return (value or '').strip().lower()


def _norm_phone(value):
    raw = (value or '').strip()
    if not raw:
        return ''
    plus = '+' if raw.startswith('+') else ''
    return f'{plus}{"".join(ch for ch in raw if ch.isdigit())}'


def _identity_from_order(order):
    """Name fields for a new record, derived from the order's own snapshot."""
    is_business = (order.document_type or '') == 'ruc'
    name = (order.customer_name or '').strip()
    if is_business:
        return {'customer_type': 'business', 'business_name': name,
                'first_name': '', 'last_name': ''}
    first, last = _split_name(name)
    return {'customer_type': 'person', 'business_name': '',
            'first_name': first, 'last_name': last}


def _has_identity(fields, document_number):
    """Mirror of the `customer_has_some_identity` check constraint."""
    return bool(
        fields['first_name'] or fields['last_name']
        or fields['business_name'] or document_number
    )


def backfill(apps, schema_editor):
    Order = apps.get_model('store', 'Order')
    Customer = apps.get_model('store', 'Customer')

    stats = {
        'by_user': 0,
        'by_document': 0,
        'orders_linked': 0,
        'orders_ambiguous': 0,
        'documents_dropped': 0,
    }

    orders = list(
        Order.objects
        .filter(company__isnull=False, customer__isnull=True)
        .order_by('pk')
    )
    if not orders:
        return

    # (company_id, user_id) -> customer_id
    by_user: dict[tuple, int] = {}
    # (company_id, document_type, document_number) -> customer_id
    by_document: dict[tuple, int] = {}

    # ---- Pass A: accounts ------------------------------------------------
    #
    # Ordered by pk so an account's FIRST order establishes the record and later
    # ones only fill in what it left empty.
    for order in orders:
        if order.user_id is None:
            continue
        key = (order.company_id, order.user_id)
        fields = _identity_from_order(order)
        document = _norm_doc(order.document_number)

        if key in by_user:
            customer = Customer.objects.get(pk=by_user[key])
            # FILL GAPS ONLY. A later order never RESHAPES a record it did not
            # create: it may supply a phone number that was missing, it may not
            # turn a person into a business.
            #
            # The rejected rule was "the most recent order wins". It reads as
            # obviously right and is not: one buyer who paid once with their own
            # DNI and once with their employer's RUC came out reclassified as a
            # company named after themselves — and, worse, released their own DNI,
            # which the next anonymous order with that same DNI then used to
            # create a second record for the same person. The tidier rule
            # manufactured exactly the duplicate this migration exists to avoid.
            for name, value in (
                ('phone', _norm_phone(order.customer_phone)),
                ('email', _norm_email(order.customer_email)),
                ('address_line', order.address_line or ''),
                ('district', order.district or ''),
                ('city', order.city or ''),
            ):
                if value and not getattr(customer, name):
                    setattr(customer, name, value)

            # Name fields are filled only from an order of the SAME shape. A
            # person's record must not acquire a `business_name` because they
            # later bought under their employer's RUC — that is not a gap being
            # filled, it is the other kind of record leaking into this one.
            if fields['customer_type'] == customer.customer_type:
                for name in ('first_name', 'last_name', 'business_name'):
                    if fields[name] and not getattr(customer, name):
                        setattr(customer, name, fields[name])
            # A document is claimed once. If this record already carries one, a
            # second document presented later is simply not recorded here — the
            # order keeps its own snapshot of what was shown that day.
            if not customer.document_number:
                _try_claim_document(
                    Customer, customer, order, document, by_document, stats,
                )
            elif document and (
                customer.document_type != (order.document_type or '')
                or customer.document_number != document
            ):
                stats['documents_dropped'] += 1
            customer.save()
            continue

        document_type = order.document_type or ''
        claimable = bool(document) and (
            (order.company_id, document_type, document) not in by_document
        )
        if not _has_identity(fields, document if claimable else ''):
            # Nothing to identify this record by. Rather than write a row the
            # check constraint would reject, fall through to pass B / ambiguous.
            continue

        customer = Customer.objects.create(
            company_id=order.company_id,
            user_id=order.user_id,
            document_type=document_type if claimable else '',
            document_number=document if claimable else '',
            phone=_norm_phone(order.customer_phone),
            email=_norm_email(order.customer_email),
            address_line=order.address_line or '',
            district=order.district or '',
            city=order.city or '',
            notes='',
            is_active=True,
            **fields,
        )
        by_user[key] = customer.pk
        stats['by_user'] += 1
        if claimable:
            by_document[(order.company_id, document_type, document)] = customer.pk
        elif document:
            stats['documents_dropped'] += 1

    # ---- Pass B: documents ----------------------------------------------
    for order in orders:
        if order.user_id is not None and (order.company_id, order.user_id) in by_user:
            continue
        document = _norm_doc(order.document_number)
        document_type = order.document_type or ''
        if not document or not document_type:
            continue

        key = (order.company_id, document_type, document)
        if key in by_document:
            continue

        existing = Customer.objects.filter(
            company_id=order.company_id,
            document_type=document_type,
            document_number=document,
        ).first()
        if existing is not None:
            by_document[key] = existing.pk
            continue

        fields = _identity_from_order(order)
        customer = Customer.objects.create(
            company_id=order.company_id,
            user_id=None,
            document_type=document_type,
            document_number=document,
            phone=_norm_phone(order.customer_phone),
            email=_norm_email(order.customer_email),
            address_line=order.address_line or '',
            district=order.district or '',
            city=order.city or '',
            notes='',
            is_active=True,
            **fields,
        )
        by_document[key] = customer.pk
        stats['by_document'] += 1

    # ---- Pass C: attach --------------------------------------------------
    for order in orders:
        customer_id = None
        if order.user_id is not None:
            customer_id = by_user.get((order.company_id, order.user_id))
        if customer_id is None:
            document = _norm_doc(order.document_number)
            if document and order.document_type:
                customer_id = by_document.get(
                    (order.company_id, order.document_type, document)
                )
        if customer_id is None:
            stats['orders_ambiguous'] += 1
            continue
        Order.objects.filter(pk=order.pk).update(customer_id=customer_id)
        stats['orders_linked'] += 1

    # Counts only. Deliberately no names, documents, emails or phone numbers:
    # migration output ends up in deploy logs, which is not a place for PII.
    print(
        f'\n  Phase 4 backfill — clientes por cuenta: {stats["by_user"]}, '
        f'por documento: {stats["by_document"]}, '
        f'pedidos vinculados: {stats["orders_linked"]}, '
        f'pedidos sin identidad concluyente: {stats["orders_ambiguous"]}, '
        f'documentos no reclamados por colisión: {stats["documents_dropped"]}'
    )


def _try_claim_document(Customer, customer, order, document, by_document, stats):
    """
    Give an account-grouped customer a document, if it is free.

    Called only for a record that has none yet. An account that bought under two
    documents keeps the FIRST; the second is counted as dropped. No order loses
    anything — each keeps its own snapshot of what was presented that day.

    A claimed key is never released. Releasing it was the bug: it handed the
    account holder's own document back to the pool, where the next anonymous
    order carrying it created a duplicate of that same person.
    """
    if not document or not order.document_type:
        return
    key = (order.company_id, order.document_type, document)
    owner = by_document.get(key)
    if owner is not None and owner != customer.pk:
        stats['documents_dropped'] += 1
        return
    if owner is None and Customer.objects.filter(
        company_id=order.company_id,
        document_type=order.document_type,
        document_number=document,
    ).exclude(pk=customer.pk).exists():
        stats['documents_dropped'] += 1
        return
    customer.document_type = order.document_type
    customer.document_number = document
    by_document[key] = customer.pk


def unlink(apps, schema_editor):
    """
    Reverse: detach orders and delete the records this migration created.

    Only safe because it runs immediately after `backfill` in a rollback. Any
    customer edited or created by hand afterwards would also be removed, so the
    reverse deletes ONLY rows with no notes and no `created_by` — the shape this
    migration writes and a human-entered record does not have.
    """
    Order = apps.get_model('store', 'Order')
    Customer = apps.get_model('store', 'Customer')
    Order.objects.filter(customer__isnull=False).update(customer=None)
    Customer.objects.filter(notes='', created_by__isnull=True).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0031_customers'),
    ]

    operations = [
        migrations.RunPython(backfill, unlink),
    ]
