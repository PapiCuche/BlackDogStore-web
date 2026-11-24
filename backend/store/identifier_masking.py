"""
STOCK-TEMPLATE-EXPORT — IMEIs and serial numbers hidden in a download, and never loaded back.

    A HIDDEN IDENTIFIER IS FOR READING. NO LOAD ACCEPTS IT.

An inventory file that carries devices carries their IMEI and serial number in full only for a caller who may LOAD
stock (`inventory.adjust`); anybody else who reads inventory gets the last characters and the rest hidden
(`mask`). The owner's rule (2026-11-24): a full list of IMEIs is the shop's most sensitive inventory data, and a
review of the shelf does not need it.

A masked file is marked twice, so it is never mistaken for one that loads:

  · its sheet: the review copy of a stock template is named `masked_sheet_title(<template sheet>)`, and every load
    refuses that sheet by name (`sheet_refusal`, through `import_exports.report_sheet_refusal`);
  · its cells: whatever the sheet is called, the device load refuses a file whose IMEI or serial column holds a
    hidden character (`cells_refusal`) — a masked row pasted into a real template is refused whole, not row by row.

The rows themselves would fail anyway (an IMEI is 15 digits, a serial only letters, digits and dashes), but «row 7:
IMEI inválido» a thousand times does not tell anybody that the file is a review copy.
"""

from __future__ import annotations

#: What hides a character (`device_identity.mask`). Never valid in an IMEI or a serial, so a real file never holds it.
MASK = '•'

MASKED_PREFIX = 'Revisión · '


def mask(value) -> str:
    """
    `value` with all but its last characters hidden — the platform's one way of hiding a device identifier
    (`device_identity.mask`: at most the last four, never more than half). Empty in, empty out.
    """
    from .device_identity import mask as hide

    return hide(str(value or '').strip())


def is_masked(value) -> bool:
    return isinstance(value, str) and MASK in value


def masked_sheet_title(sheet: str) -> str:
    """The review copy's sheet. Within Excel's 31 characters for both stock templates (held by a test)."""
    return f'{MASKED_PREFIX}{sheet}'


def _masked_titles() -> set:
    # Lazy: the importer imports this module.
    from .import_formats import normalize_header
    from .unit_import_services import STOCK_TEMPLATES

    return {normalize_header(masked_sheet_title(spec['sheet'])) for spec in STOCK_TEMPLATES.values()}


def sheet_refusal(name) -> str:
    """Why `name` cannot be loaded, when it is the sheet of a masked review copy. Empty otherwise."""
    from .import_formats import normalize_header

    if normalize_header(str(name or '')) not in _masked_titles():
        return ''
    return (f'«{name}» es una copia de revisión con los IMEI y las series ocultos: sirve para revisar y no se carga. '
            'Para cargar equipos usa la plantilla vacía o la exportación con identificadores completos.')


def cells_refusal(rows, columns) -> str:
    """
    Why a device file cannot be loaded, when one of `columns` (IMEI, IMEI 2, serial) holds a hidden identifier in any
    of `rows` (`xlsx_reader.read_rows`). Empty otherwise.
    """
    wanted = [column for column in columns if column is not None]
    for row_number, values, _numeric in rows:
        if any(is_masked(values.get(column)) for column in wanted):
            return (f'La fila {row_number} trae un IMEI o una serie oculta (con «{MASK}»): el archivo es una '
                    'copia de revisión y no se carga. Para cargar equipos usa la plantilla vacía o la exportación con '
                    'identificadores completos.')
    return ''
