"""
FINANCE-OBLIGATIONS-01 — fixed payments, obligations and their ledger.

`docs/adr-finance-obligations.md`. `calendar` is pure date arithmetic;
`services` is every write, each one locked, idempotent and audited. Nothing in
this package sends a message to anybody (FINANCE-MANUAL-REMINDERS-01).
"""
