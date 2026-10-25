"""
THE PROVISIONAL DECISIONS, IN ONE PLACE (ADR §15, P1–P4).

The owner has not answered P1–P4 yet. The coordinator set these defaults for
ACCOUNTING-02 and asked that they live here and nowhere else, so that the
answer, when it comes, is a change to this file and its tests — not a hunt
through the query layer. Every response of the accounting API repeats them
under `policy`, marked as pending, so nobody reads a provisional rule as a
settled one.

    P1  service revenue      by COLLECTION: the repair payments received in the
                             period, on the day they were received. A reversal
                             is its own NEGATIVE row on the day it was made, so
                             reversing a payment later never rewrites a past
                             period. Not the approved quote.
    P2  sales period         `Order.paid_at`. An order that is not paid is not
                             income: it is listed in view C (pending), never in
                             view A.
    P3  who sees it          `accounting.view` and `accounting.export` go to the
                             roles that already hold `company.manage` (data
                             migration 0149, which freezes its own copy of this
                             rule). The platform master holds them through the
                             existing resolution. No «Contador» role yet.
    P4  TEST payments        an online order whose every authorised payment was
                             taken with TEST keys is EXCLUDED from income and
                             reported apart. An attempt older than the column
                             (`environment=''`) cannot be classified: it counts,
                             like the dashboard counts it, and carries a visible
                             flag.
"""

#: P1 — how a repair becomes income. The only value implemented.
SERVICE_REVENUE_BASIS = 'collection'

#: P2 — the column that puts a sale in a period. The only value implemented.
SALE_PERIOD_FIELD = 'paid_at'

#: P3 — the capability whose holders received the accounting capabilities.
#: Informational here; the data migration keeps a frozen copy, as migrations must.
GRANT_TO_HOLDERS_OF = 'company.manage'

#: P4 — whether an order paid only with TEST keys stays out of income.
EXCLUDE_TEST_PAYMENTS = True

#: What `PaymentTransaction.environment` says, as `integrations.payments.environment_of`
#: writes it. Empty = an attempt older than the column: unknown.
PAYMENT_ENV_TEST = 'test'
PAYMENT_ENV_UNKNOWN = ''

PENDING_OWNER_DECISION = ('P1', 'P2', 'P3', 'P4')


def policy() -> dict:
    """The provisional rules, as every response states them."""
    return {
        'status': 'provisional',
        'pending_owner_decision': list(PENDING_OWNER_DECISION),
        'service_revenue_basis': SERVICE_REVENUE_BASIS,
        'sale_period_field': SALE_PERIOD_FIELD,
        'granted_to_holders_of': GRANT_TO_HOLDERS_OF,
        'exclude_test_payments': EXCLUDE_TEST_PAYMENTS,
    }


#: Printed with every answer (ADR §0). Plain words: a person reads it.
SCOPE_NOTICE = (
    'Centro contable de ventas e ingresos. No incluye compras, gastos, bancos ni '
    'asientos contables. Una nota de venta interna no tiene validez tributaria. '
    'Nada de lo que aquí aparece es una declaración presentada ante SUNAT.'
)
