"""
«¿Qué archivo tengo?» — which of the bulk loads a workbook belongs to.
INVENTORY-UNIFIED-01, part 3.

    IT SAYS WHERE A FILE GOES. IT DOES NOT LOAD IT.

Decided ONLY from sheet names and header rows, with the vocabulary the loads
already share — nothing here is a second definition of what a serial column or
a quantity column is:

  · a serial header (`import_formats.SERIAL_HEADERS`)       -> devices
  · a count or a warehouse column (`counts_stock`)          -> stock
  · name and price (`import_formats.detect`, the presets)   -> products
  · a code and picture columns, no name and no price        -> images
  · a «Reporte de …» sheet (`import_exports.REPORT_SHEETS`) -> a report: not loaded

NOT MIXED BY CONVENIENCE. A file that is two loads at once — serials AND
quantities, product columns AND serials, two sheets of two loads — is not sent
to whichever would swallow it: the answer is `unknown`, with the sentence that
says why. The one exception is the one the product load already makes and
explains itself: a catalogue file with a stock column is a catalogue file, and
the answer says its stock is not loaded there.

A row is never read. No job, no profile, nothing is written.
"""

from __future__ import annotations

from . import import_exports, import_formats, unit_import_services
from .models import BulkImportJob

PRODUCTS, STOCK, UNITS = BulkImportJob.PRODUCTS, BulkImportJob.STOCK, BulkImportJob.UNITS
IMAGES, REPORT, UNKNOWN, FORBIDDEN = 'images', 'report', 'unknown', 'forbidden'
#: What `classify` may answer. `forbidden` is the VIEW's answer, never this module's.
TYPES = (PRODUCTS, STOCK, UNITS, IMAGES, REPORT, UNKNOWN)

#: reason code -> the sentence the screen shows.
REASONS = {
    'product_columns': 'Tiene nombre y precio de venta: es la carga de productos.',
    'product_columns_with_stock': (
        'Tiene nombre y precio de venta: es la carga de productos. Sus columnas de cantidad no se '
        'cargan ahí: el stock tiene su propia carga.'
    ),
    'warehouse_columns': 'Tiene columnas de cantidad por almacén: es la carga de stock.',
    'quantity_column': 'Tiene una columna de cantidad: es la carga de stock.',
    'serial_column': 'Tiene columna de número de serie: es la carga de equipos.',
    'image_columns': 'Tiene código e imágenes, sin nombre ni precio: es la carga de imágenes.',
    'serial_and_quantity': (
        'Tiene columna de número de serie y columna de cantidad. Un archivo de equipos lleva una fila '
        'por equipo y ninguna cantidad; uno de stock lleva cantidades y ninguna serie. Sepáralo en dos archivos.'
    ),
    'product_and_serial': (
        'Tiene columnas de producto (nombre y precio) y de equipo (número de serie). Son dos cargas '
        'distintas: sepáralo en dos archivos.'
    ),
    'several_kinds': 'Trae hojas de cargas distintas. Cada carga recibe su propio archivo.',
    'not_recognised': (
        'No se reconocen sus columnas. Descarga la plantilla de la carga que necesitas y copia ahí tus datos.'
    ),
}

#: What somebody who may not run that load is told. It does not name the load.
NO_PERMISSION = 'Este archivo es de una carga para la que no tienes permiso.'

_CODE_HEADERS = {'codigo', 'codigo de barras'}
_PICTURE_HEADERS = {'imagen principal', 'imagenes'}
#: Headers that say WHICH product a row is about. A row that counts things or
#: carries serials and names no product is not the header row of a load — it
#: is a banner («Stock por almacén» above the real headers) or somebody's notes.
_IDENTIFIER_HEADERS = {
    'id', 'codigo ean', *unit_import_services._ALIASES['code'], *unit_import_services._ALIASES['name'],
}


def _of_headers(sheet_name: str, headers) -> tuple[str, str] | None:
    """`(type, reason)` for ONE header row, or None when it says nothing."""
    keys = [import_formats.normalize_header(header) for header in headers]
    present = {key for key in keys if key}
    if not present:
        return None

    serial = bool(present & set(import_formats.SERIAL_HEADERS))
    warehouse = any(import_formats.is_warehouse_header(key) for key in present)
    quantity = any(import_formats.counts_stock(key) for key in present)
    product = import_formats.detect(PRODUCTS, sheet_name, headers) is not None
    if not product and not present & _IDENTIFIER_HEADERS:
        return None

    if serial and (warehouse or quantity):
        return UNKNOWN, 'serial_and_quantity'
    if serial and product:
        return UNKNOWN, 'product_and_serial'
    if serial:
        return UNITS, 'serial_column'
    if product:
        return PRODUCTS, 'product_columns_with_stock' if (warehouse or quantity) else 'product_columns'
    if warehouse:
        return STOCK, 'warehouse_columns'
    if quantity:
        return STOCK, 'quantity_column'
    if present & _CODE_HEADERS and present & _PICTURE_HEADERS:
        return IMAGES, 'image_columns'
    return None


def classify(sheets) -> dict:
    """
    `{'type', 'reason', 'detail', 'sheet'}` for a workbook.

    `sheets` is `[(sheet name, [header row, header row…])]`: the candidate
    header rows of each sheet, most likely first (the owner's product export
    carries a banner above its headers, so the second row is tried too).
    """
    def answer(kind, reason, sheet='', detail=None):
        return {'type': kind, 'reason': reason, 'detail': detail or REASONS[reason], 'sheet': sheet}

    for name, _rows in sheets:
        refusal = import_exports.report_sheet_refusal(name)
        if refusal:
            return answer(REPORT, 'report_sheet', str(name), refusal)

    found = []      # (sheet, type, reason), one per sheet that says something
    for name, rows in sheets:
        if import_exports.is_help_sheet(name):
            # «Instrucciones», «Ejemplo», «Categorías»: they explain, they are not a load.
            continue
        for headers in rows:
            verdict = _of_headers(str(name), headers)
            if verdict is not None:
                found.append((str(name), *verdict))
                break

    if not found:
        return answer(UNKNOWN, 'not_recognised')
    if len({kind for _sheet, kind, _reason in found}) > 1:
        return answer(UNKNOWN, 'several_kinds')
    sheet, kind, reason = found[0]
    return answer(kind, reason, sheet)
