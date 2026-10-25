"""
ACCOUNTING-02 — the read-only accounting centre (`docs/adr-accounting.md`).

A QUERY LAYER, NOT A LEDGER. Nothing in this package writes to the database:
it reads orders, payments, internal notes, fiscal documents, cancellations,
refunds, returns and repair payments, and answers three questions (views A, B
and C of the ADR). There is no journal, no entry table and no copy of any
amount. It is not complete accounting either: purchases, expenses, banks and
journal entries do not exist in the platform, and every answer says so.
"""
