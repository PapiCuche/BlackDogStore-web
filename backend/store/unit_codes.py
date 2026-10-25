"""
LABEL-02 — how a device is identified, kept apart from how a product is
(docs/adr-inventory-labels.md D4).

Three things that never mix:

  the commercial code   a `ProductBarcode` of the model or variant (LABEL-01);
  the unit's identity   its `StockUnit` row;
  what is scanned       one of that row's identifiers: `imei`, `imei2`,
                        `serial_number`, or the internal `label_code`.

THE TILL LOOKS UP PRODUCTS FIRST. A product code equal to a device's IMEI or
serial would therefore steal that device's scan. The rule holds from both sides:
`product_codes` refuses a product code that is a unit identifier, and this
module refuses a unit identifier that is a product code (`product_code_holding`,
called by `stock_unit_services._clean_row`, which every reception and the bulk
upload's preview go through).

Comparisons are case-insensitive where the stored forms allow a difference:
IMEIs are digits, serials and label codes are stored upper-cased, product codes
keep the capitals they were given (so they are matched with `iexact`).
"""
from __future__ import annotations

from django.db.models import Q

from .models import ProductBarcode, StockUnit


def product_code_holding(company_id, value: str):
    """The `ProductBarcode` of the company (any product, active or not) whose code is `value`, or None."""
    if not value:
        return None
    return (ProductBarcode.objects.filter(company_id=company_id, code__iexact=value)
            .select_related('product').first())


def is_unit_identifier(company, code: str) -> bool:
    """
    Whether `code` is a device's IMEI, second IMEI or serial in `company`.

    IMEIs are stored as digits and serials upper-cased (`device_identity`), so
    comparing the IMEI exactly and the serial upper-cased IS the case-insensitive
    comparison, and it keeps the (company, …) indexes usable. Every unit counts,
    sold or not: `find_unit` resolves them all.
    """
    return StockUnit.objects.filter(company=company).filter(
        Q(imei=code) | Q(imei2=code) | Q(serial_number=code.upper()),
    ).exists()
