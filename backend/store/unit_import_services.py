"""
UNIT-IMPORT — the device upload: many devices in one file.

    ONE ROW = ONE PHYSICAL UNIT.

The stock importer writes QUANTITIES, and a serialized product has no quantity
anybody writes: it has units. That importer therefore refuses those products,
and until now the only way in was one device at a time on screen.

THIS IS NOT ANOTHER WRITER. Every row ends in
`stock_unit_services.receive_units` — the same function behind «Registrar
equipo». The serial and IMEI rules, the Kardex line and the invariant
(`BranchStock.quantity == available units`) are that function's, so a device
that arrives in a spreadsheet is exactly a device that arrived at the counter.

What this module adds is the rehearsal: stage every row, say what is wrong with
each, write nothing — and at apply time, all of the file or none of it.

NO QUANTITY COLUMN, ON PURPOSE. A row saying "iPhone 16, quantity 2" names no
device. A file that carries one is refused whole rather than read as two units
with one serial.
"""

from __future__ import annotations

import io
import math
from decimal import Decimal, InvalidOperation
from collections import OrderedDict

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from . import cost_services, device_catalog, label_batches, stock_unit_services, xlsx_reader
from .import_exports import _autosize, _header_row, _workbook, report_sheet_refusal
from .import_formats import SERIAL_HEADERS, counts_stock, normalize_header
from .import_services import CatalogueIndex, _safe_filename, _sha256
from .models import (
    AdminAuditLog, Branch, BranchStock, BulkImportJob, BulkImportRow, Product, StockMovement, StockUnit, Supplier,
)

TEMPLATE_FILENAME = 'carga-masiva-equipos.xlsx'
SHEET = 'Equipos'
HELP_SHEET = 'Instrucciones'

#: One file, one sitting. Above this the preview stops being something a person
#: reads, and the reception is better split by purchase document anyway.
MAX_UNITS = 1000

HEADERS = ['Código', 'Producto', 'Sucursal', 'Número de serie', 'IMEI', 'IMEI 2', 'Condición', 'Costo',
           'Motivo / referencia',
           # DC2 — optional, after everything else so older files keep their columns. A1b added «SIM» and
           # «Región» after «Color», optional too: columns are read by header, never by position.
           'Modelo', 'Capacidad', 'Color', 'SIM', 'Región', 'Línea', 'Estado estético', 'Batería %',
           'Precio de venta',
           # INV-LOAD-01 — optional, at the end too: who sold it, when it entered, and two kinds of notes.
           'Proveedor', 'Fecha de ingreso', 'Notas internas', 'Detalles para el comprador',
           # USED-READY-01 — optional, for used devices; after everything else, as always.
           'Garantía (días)', 'Estado funcional', 'Defectos', 'Accesorios']

#: field -> the headers it answers to, already folded by `normalize_header`.
_ALIASES = {
    'code': ('codigo', 'codigo de barras', 'codigo del producto', 'sku', 'codigo sku', 'codigo producto'),
    'name': ('producto', 'producto modelo', 'modelo', 'nombre', 'nombre del producto'),
    'branch': ('sucursal', 'tienda', 'almacen', 'local'),
    # «Nº de serie», «No. de serie», «Nro. de serie», «S/N»: one column, however it is headed.
    'serial_number': (*SERIAL_HEADERS, 'serie', 'serial', 'ns'),
    'imei': ('imei', 'imei 1', 'imei1'),
    'imei2': ('imei 2', 'imei2', 'segundo imei'),
    'condition': ('condicion', 'estado'),
    'cost': ('costo', 'costo unitario', 'costo de compra'),
    'reason': ('motivo referencia', 'motivo', 'referencia', 'documento', 'motivo o documento de ingreso',
               'documento de ingreso'),
    # DC2. «Modelo» is ALSO a heading of the product name in older files: when
    # it is the only one, it stays the name (and is tried as a model only if
    # no product has that name).
    'model': ('modelo', 'modelo catalogo'),
    'capacity': ('capacidad', 'almacenamiento'),
    'colour': ('color', 'colour'),
    'sim': ('sim', 'tipo de sim'),
    # INV-LOAD-02 — the stock templates head it «Versión (región / país de venta)»: only that heading, so a generic
    # «Versión» of an older file stays unread.
    'region': ('region', 'version region pais de venta', 'version region'),
    'line': ('linea', 'linea nuevo seminuevo', 'linea nuevo o seminuevo'),
    'grade': ('estado estetico', 'estado cosmetico', 'grado estetico'),
    'battery': ('bateria', 'bateria porcentaje', 'salud de bateria', 'salud de la bateria'),
    'sale_price': ('precio de venta', 'precio venta'),
    # INV-LOAD-01.
    # Only headers that name THESE columns: a generic «Fecha», «Notas» or «Observaciones» of an older file stays
    # unread (as before), so a file that loaded yesterday does not start failing — nor a footer row come alive.
    'supplier': ('proveedor', 'proveedor nombre o ruc', 'ruc del proveedor', 'ruc proveedor'),
    'received_on': ('fecha de ingreso', 'fecha ingreso', 'fecha de entrada'),
    # INV-LOAD-02 — «Observaciones (notas internas)» of the stock templates; a bare «Observaciones» stays unread.
    'notes': ('notas internas', 'nota interna', 'observaciones notas internas'),
    'public_notes': ('detalles para el comprador', 'detalles comprador', 'notas publicas'),
    # USED-READY-01. NO column inspects or publishes: every device enters pending and unpublished.
    'warranty_days': ('garantia dias', 'garantia', 'garantia en dias', 'dias de garantia'),
    'functional_state': ('estado funcional', 'funcionamiento'),
    'defects': ('defectos', 'fallas'),
    'accessories': ('accesorios', 'incluye'),
}
#: USED-READY-01 — the used-only columns: field -> the header named in a warning.
_USED_COLUMNS = {'warranty_days': 'Garantía (días)', 'functional_state': 'Estado funcional',
                 'defects': 'Defectos', 'accessories': 'Accesorios'}
#: A header that says HOW MANY refuses the file: «Cantidad», «Cantidad inicial»,
#: «Stock inicial - Almacén 1», «Nº de unidades». What counts stock is decided in
#: one place (`import_formats.counts_stock`); «Stock mínimo», «Unidades de
#: medida» or «Costo unitario» do not.

#: What marked the example row of the PREVIOUS template. The template is now one
#: sheet with nothing under its headers, but files made with the old one are
#: still uploaded, and that row is not a device.
EXAMPLE_SERIAL_PREFIX = 'EJEMPLO'


class UnitImportError(Exception):
    """The file, or the job, cannot be used. The message is for the operator; `code` when the screen acts on it."""

    def __init__(self, detail='', *, code=''):
        super().__init__(detail)
        self.code = code


# ---------------------------------------------------------------------------
# Template
# ---------------------------------------------------------------------------

def _conditions() -> dict[str, str]:
    """What a person may write in «Condición» -> the stored value."""
    known = {}
    for value, label in StockUnit.Condition.choices:
        known[normalize_header(label)] = value
        known[normalize_header(value)] = value
    return known


def _header_notes() -> dict:
    labels = ', '.join(label for _value, label in StockUnit.Condition.choices)
    return {
        'Código': (
            'Una fila es UN equipo físico: dos teléfonos iguales son dos filas. No hay columna de cantidad. '
            'Aquí va el código del producto (o su código de barras). El producto debe existir y estar marcado '
            '«con número de serie» en Inventario › Equipos.'
        ),
        'Producto': 'Sólo si no hay código: el nombre exacto del producto.',
        'Sucursal': 'La sucursal donde entra el equipo, con su nombre tal como aparece en el sistema.',
        'Número de serie': (
            'Obligatorio, salvo en un producto marcado «lleva IMEI» cuya fila trae un IMEI válido: ahí es opcional. '
            'No se repite dentro del mismo producto.'
        ),
        'IMEI': (
            '15 dígitos. Obligatorio si el producto está marcado «lleva IMEI»; en los demás, vacío '
            'salvo que el equipo tenga uno. No se repite en ningún equipo de la empresa.'
        ),
        'IMEI 2': 'Opcional, sólo para equipos con dos líneas y con el primer IMEI indicado. Distinto del primero.',
        'Condición': f'{labels}. Vacío = Nuevo.',
        'Costo': (
            'Opcional. Lo que costó ese equipo, sin IGV ni símbolo de moneda: es su costo específico, '
            'con el que se calcula el margen cuando se vende.'
        ),
        'Motivo / referencia': (
            'El documento con el que entró: factura de compra, guía, inventario inicial. '
            'Queda en el Kardex de cada equipo.'
        ),
        'Modelo': (
            'Opcional. En vez de «Código» o «Producto»: el modelo del catálogo de equipos, con «Capacidad», '
            '«Color», «SIM», «Región» y «Línea», dice de qué variante es el equipo.'
        ),
        'Capacidad': 'Opcional, con «Modelo»: una opción que ya existe (por ejemplo «128 GB»).',
        'Color': 'Opcional, con «Modelo»: una opción de color que ya existe.',
        'SIM': 'Opcional, con «Modelo»: la opción de SIM de la variante, si el modelo la lleva.',
        'Región': 'Opcional, con «Modelo»: la opción de región de la variante, si el modelo la lleva.',
        'Línea': 'Opcional: «Nuevo» o «Seminuevo». Vacío = Nuevo.',
        'Estado estético': 'Opcional, para seminuevos: uno de los estados de la escala de la empresa.',
        'Batería %': 'Opcional: la salud de la batería MEDIDA, de 0 a 100. Vacío = «no informado».',
        'Precio de venta': (
            'Sólo para seminuevos: el precio de ESTE equipo en la tienda. Un seminuevo sin precio no se '
            'ofrece en línea. Un equipo nuevo se vende al precio de su variante.'
        ),
        'Proveedor': (
            'Opcional: quien te vendió el equipo, con su nombre o su RUC tal como está en Proveedores. '
            'Un proveedor que no existe en tu empresa es un error de la fila.'
        ),
        'Fecha de ingreso': (
            'Opcional: el día en que el equipo entró a la tienda (AAAA-MM-DD o DD/MM/AAAA). '
            'Vacía = hoy. No puede ser futura ni anterior al año 2000.'
        ),
        'Notas internas': f'Opcional: sólo las ve el personal. Hasta {stock_unit_services.MAX_NOTES} caracteres.',
        'Detalles para el comprador': (
            'Opcional, sólo para seminuevos: lo que el comprador lee del equipo (rayones, accesorios). '
            'Hasta 500 caracteres.'
        ),
        'Garantía (días)': (
            'Opcional, para seminuevos: los días de garantía de ESTE equipo. Vacío = la política de garantía de '
            'la empresa; 0 = sin garantía.'
        ),
        'Estado funcional': 'Opcional, para seminuevos: «Sin fallas» o «Con fallas» (con «Defectos»).',
        'Defectos': 'Opcional, para seminuevos: lo que no funciona o está dañado. Lo lee el comprador.',
        'Accesorios': 'Opcional, para seminuevos: lo que trae (caja, cargador, cable…). Lo lee el comprador.',
    }


def template_bytes(branches=()) -> bytes:
    """
    The device template: ONE sheet, «Equipos», its headers and nothing under them.

    IMPORT-SPLIT-01. The example row and the sheet of instructions are gone from
    what we hand out; what they said is on the screen and in a note on each
    header. A file made with the previous template is still read (`_pick_sheet`,
    `EXAMPLE_SERIAL_PREFIX`).

    `branches`: the names offered under «Sucursal» — the ones THIS person can
    load into, so the list never names a branch they do not operate. «Condición»
    is the list the system knows. Both lists offer and neither blocks, as in the
    owner's file: the upload is what accepts or refuses a row.
    """
    from .import_exports import add_cell_list, cell_list, header_notes, number_columns, text_columns

    workbook = _workbook()
    sheet = workbook.active
    sheet.title = SHEET
    _header_row(sheet, HEADERS)
    header_notes(sheet, _header_notes())
    sheet.freeze_panes = 'A2'
    _autosize(sheet, [20, 43, 25, 27, 22, 22, 23, 18, 48, 30, 16, 16, 14, 12, 16, 20, 14, 18, 28, 18, 36, 40, 16, 20, 40, 32])
    text_columns(sheet, ('A', 'D', 'E', 'F'))

    from openpyxl.utils import get_column_letter

    def column(header):
        return get_column_letter(HEADERS.index(header) + 1)

    # By header, never by letter: A1b moved «Línea» and what follows it two columns to the right.
    number_columns(sheet, (column('Costo'), column('Batería %'), column('Precio de venta'), column('Garantía (días)')))
    text_columns(sheet, (column('Proveedor'),))     # a RUC is a code: its digits are kept as typed
    add_cell_list(sheet, column('Línea'), cell_list(['Nuevo', 'Seminuevo']))
    add_cell_list(sheet, column('Estado funcional'), cell_list(['Sin fallas', 'Con fallas']))
    add_cell_list(sheet, 'C', cell_list(branches))
    add_cell_list(sheet, 'G', cell_list(label for _value, label in StockUnit.Condition.choices))

    out = io.BytesIO()
    workbook.save(out)
    return out.getvalue()


# ---------------------------------------------------------------------------
# INV-LOAD-02 — the two stock templates: new devices and used ones
# ---------------------------------------------------------------------------
#
# Built from THIS module's column registry (`_ALIASES`): every header below is read by `_columns` as the field next to
# it, and every field the importer reads is either in the template of its line or in `NOT_IN_STOCK_TEMPLATES` with
# the reason. `test_stock_load` holds the three to each other, so a column the importer would silently ignore cannot
# enter a template, nor a new importer column stay out of one unnoticed.
#
# THE LINE IS THE TEMPLATE'S. Its data sheet is named after it («Equipos nuevos» / «Equipos seminuevos»): a row is
# looked up among the variants of that line only, and a row of the other line is an error of the row.

LINE_NEW, LINE_USED = Product.CONDITION_NEW, Product.CONDITION_USED
LINE_LABELS = {LINE_NEW: 'equipos nuevos', LINE_USED: 'seminuevos'}
_LINE_WORD = {LINE_NEW: 'Nuevo', LINE_USED: 'Seminuevo'}

_STOCK_BASE = [
    ('Sucursal', 'branch'), ('Modelo', 'model'), ('Capacidad', 'capacity'), ('Color', 'colour'), ('SIM', 'sim'),
    ('Versión (región / país de venta)', 'region'), ('IMEI 1', 'imei'), ('IMEI 2', 'imei2'), ('Serie', 'serial_number'),
    ('Proveedor', 'supplier'), ('Costo', 'cost'),
]
_STOCK_TAIL = [('Fecha de ingreso', 'received_on'), ('Observaciones (notas internas)', 'notes')]
_STOCK_USED = [
    ('Precio de venta', 'sale_price'), ('Estado estético', 'grade'), ('Salud de batería (%)', 'battery'),
    ('Estado funcional', 'functional_state'), ('Defectos', 'defects'), ('Accesorios', 'accessories'),
    ('Garantía (días)', 'warranty_days'), ('Detalles para el comprador', 'public_notes'),
]
STOCK_TEMPLATES = {
    LINE_NEW: {'filename': 'stock-equipos-nuevos.xlsx', 'sheet': 'Equipos nuevos',
               'title': 'Stock de equipos nuevos', 'columns': [*_STOCK_BASE, *_STOCK_TAIL]},
    LINE_USED: {'filename': 'stock-seminuevos.xlsx', 'sheet': 'Equipos seminuevos',
                'title': 'Stock de seminuevos', 'columns': [*_STOCK_BASE, *_STOCK_USED, *_STOCK_TAIL]},
}
#: The columns only the used template carries. A file of the NEW template that brings one is refused whole: what it
#: says would otherwise be ignored on every row («ignorado en un equipo nuevo»). A new device sells at its variant's
#: price, so «Precio de venta» is one of them.
USED_TEMPLATE_FIELDS = frozenset(field for _header, field in _STOCK_USED)
#: What the importer reads that neither stock template carries, and why.
NOT_IN_STOCK_TEMPLATES = {
    'code': 'El equipo se identifica por «Modelo» y sus opciones, no por un código de producto.',
    'name': '«Modelo» es el modelo del catálogo, nunca el nombre de un producto.',
    'condition': 'La condición es la de la línea de la variante (nuevo o usado): no se escribe.',
    'reason': 'El motivo o documento de ingreso se indica una vez, al subir el archivo.',
    'line': 'La línea la fija la plantilla.',
}
#: Sheet name (folded) -> the line it loads.
_TEMPLATE_SHEETS = {normalize_header(spec['sheet']): line for line, spec in STOCK_TEMPLATES.items()}

#: A Luhn-valid synthetic IMEI for the example row (never a real device's). The row is skipped by its «Serie».
_EXAMPLE_IMEI = '352819069999990'


def clean_line_option(value) -> str:
    """The line a caller asked for (`new` / `used`), or '' for none. Anything else is refused."""
    value = str(value or '').strip()
    if value and value not in STOCK_TEMPLATES:
        raise UnitImportError('La línea debe ser «new» (equipos nuevos) o «used» (seminuevos).')
    return value


def _stock_notes(line) -> dict:
    """field -> the note on its header in the stock template of `line`."""
    variant = 'NUEVA' if line == LINE_NEW else 'SEMINUEVA'
    return {
        'branch': 'Obligatoria (o elige una sucursal al subir el archivo): su nombre tal como aparece en el sistema.',
        'model': (f'Obligatorio: el modelo del catálogo (Catálogo › Modelos), por ejemplo «iPhone 16 Pro». Con las '
                  f'columnas siguientes dice de qué variante {variant} es el equipo. La variante debe existir.'),
        'capacity': 'La capacidad de la variante, si el modelo la lleva (por ejemplo «256 GB»).',
        'colour': 'El color de la variante, tal como está en el catálogo.',
        'sim': 'La opción de SIM de la variante, si el modelo la lleva (por ejemplo «eSIM»). Vacía si no.',
        'region': ('La versión de la variante: la región o el país de venta del equipo (por ejemplo «USA»), si el '
                   'modelo la distingue. Vacía si no.'),
        'imei': ('15 dígitos, sin espacios. Obligatorio si la variante está marcada «lleva IMEI». No se repite en '
                 'ningún equipo de la empresa.'),
        'imei2': 'Opcional: el segundo IMEI de un equipo con dos líneas. Distinto del primero.',
        'serial_number': ('El número de serie del fabricante. Opcional si la fila trae un IMEI válido y la variante '
                          '«lleva IMEI»; obligatorio en los demás. No se repite dentro de la misma variante.'),
        'supplier': ('Opcional: quien te vendió el equipo, con su nombre o su RUC tal como está en Proveedores. '
                     'Sólo puede llenarla quien ve proveedores.'),
        'cost': ('Opcional: lo que costó ESTE equipo, sin IGV ni símbolo de moneda (1234.50). Sólo puede llenarla '
                 'quien ve costos; si no, déjala vacía.'),
        'received_on': ('Opcional: el día en que el equipo entró a la tienda (AAAA-MM-DD o DD/MM/AAAA). Vacía = hoy. '
                        'No puede ser futura ni anterior al año 2000.'),
        'notes': (f'Opcional: notas internas, sólo para el personal (ubicación, observaciones). Hasta '
                  f'{stock_unit_services.MAX_NOTES} caracteres.'),
        'sale_price': ('El precio de ESTE equipo en la tienda, mayor que cero. Sin precio no se vende en línea ni en '
                       'caja. Sólo lo fija quien administra productos.'),
        'grade': 'Opcional: uno de los estados de la escala de la empresa (por ejemplo «Excelente»).',
        'battery': 'Opcional: la salud de la batería MEDIDA, de 0 a 100. Vacía = «no informado».',
        'functional_state': 'Opcional: «Sin fallas» o «Con fallas» (entonces llena «Defectos»).',
        'defects': 'Opcional: lo que no funciona o está dañado. Lo lee el comprador.',
        'accessories': 'Opcional: lo que trae (caja, cargador, cable…). Lo lee el comprador.',
        'warranty_days': ('Opcional: los días de garantía de ESTE equipo. Vacía = la política de garantía de la '
                          'empresa; 0 = sin garantía. Sólo la fija quien administra productos.'),
        'public_notes': 'Opcional: lo que el comprador lee del equipo (rayones, detalles). Hasta 500 caracteres.',
    }


def _stock_example(line, branches) -> dict:
    """The example row: skipped at upload by its «Serie» (`EXAMPLE_SERIAL_PREFIX`), and it says so twice."""
    row = {'branch': (list(branches) or ['Tienda principal'])[0], 'model': 'iPhone 16', 'capacity': '128 GB',
           'colour': 'Negro', 'imei': _EXAMPLE_IMEI, 'serial_number': f'{EXAMPLE_SERIAL_PREFIX}-BORRA-ESTA-FILA',
           'cost': 2500, 'received_on': '2026-01-15',
           'notes': 'FILA DE EJEMPLO: no se carga. Escribe tus equipos desde la fila 3.'}
    if line == LINE_USED:
        row.update(colour='Azul', sale_price=1999, grade='Excelente', battery=88, functional_state='Sin fallas',
                   accessories='Caja y cable', warranty_days=90, public_notes='Rayón leve en el marco.')
    return row


def _stock_help(line) -> list[list[str]]:
    """The «Instrucciones» sheet: read-only, never read by the upload (`_pick_sheet` skips it)."""
    spec = STOCK_TEMPLATES[line]
    other = STOCK_TEMPLATES[LINE_USED if line == LINE_NEW else LINE_NEW]['title']
    notes = _stock_notes(line)
    lines = [
        [spec['title']],
        [],
        ['UNA FILA = UN EQUIPO FÍSICO. Dos teléfonos iguales son dos filas, cada uno con su IMEI o su serie.'],
        [f'Esta plantilla carga sólo {LINE_LABELS[line]}. Para los otros usa «{other}».'],
        ['La fila 2 es un EJEMPLO: no se carga aunque la dejes. Escribe tus equipos desde la fila 3.'],
        [f'No cambies los encabezados ni el nombre de la hoja «{spec["sheet"]}»: el sistema los lee para saber qué '
         f'es cada columna y qué línea carga.'],
        ['El tipo de ingreso (inventario inicial o compra) y el motivo se eligen al subir el archivo.'],
        ['Subir el archivo sólo PREVISUALIZA: nada se registra hasta que confirmes, y entra todo el archivo o nada.'],
        [],
        ['Columna', 'Qué escribir'],
        *[[header, notes[field]] for header, field in spec['columns']],
    ]
    if line == LINE_NEW:
        lines += [[], ['¿Y el precio?', 'Un equipo nuevo se vende al precio de su variante (Catálogo › Modelos): '
                                        'esta plantilla no lleva «Precio de venta».']]
    else:
        lines += [
            [],
            ['DESPUÉS DE CARGAR (no son columnas: la carga nunca los cambia)'],
            ['Estado de inspección', 'Todo seminuevo entra «Pendiente de inspección». Quien tiene el permiso de '
                                     'inspeccionar lo aprueba o lo rechaza en la ficha del equipo. Sin inspección '
                                     'aprobada no se vende ni en caja ni en la web.'],
            ['Fotografías de la unidad', 'Se suben en la ficha del equipo (Inventario › Equipos). La web exige al '
                                         'menos una foto real del equipo.'],
            ['Estado de publicación', 'Todo seminuevo entra SIN PUBLICAR. Se publica en su ficha cuando tiene precio, '
                                      'inspección aprobada y al menos una foto.'],
        ]
    return lines


def stock_template_bytes(line, branches=(), grades=()) -> bytes:
    """
    INV-LOAD-02 — «Stock de equipos nuevos» or «Stock de seminuevos»: the data sheet (its name fixes the line) with a
    note on each header, ONE example row that the upload skips, the lists that are cheap to offer, and a read-only
    «Instrucciones» sheet. `branches`: the names THIS person may load into; `grades`: the company's cosmetic scale.
    """
    from openpyxl.styles import Font
    from openpyxl.utils import get_column_letter

    from .import_exports import add_cell_list, cell_list, header_notes, number_columns, text_columns

    spec = STOCK_TEMPLATES[line]
    fields = [field for _header, field in spec['columns']]
    notes = _stock_notes(line)

    workbook = _workbook()
    sheet = workbook.active
    sheet.title = spec['sheet']
    _header_row(sheet, [header for header, _field in spec['columns']])
    header_notes(sheet, {header: notes[field] for header, field in spec['columns']})
    example = _stock_example(line, branches)
    grey = Font(italic=True, color='6B7280')
    for index, field in enumerate(fields, start=1):
        sheet.cell(row=2, column=index, value=example.get(field)).font = grey
    sheet.freeze_panes = 'A2'
    widths = {'branch': 22, 'model': 26, 'region': 30, 'imei': 19, 'imei2': 19, 'serial_number': 28,
              'supplier': 26, 'notes': 40, 'defects': 32, 'accessories': 28, 'public_notes': 40,
              'received_on': 16, 'functional_state': 16, 'grade': 16, 'battery': 18, 'warranty_days': 16,
              'sale_price': 16}
    _autosize(sheet, [widths.get(field, 14) for field in fields])

    def column(field):
        return get_column_letter(fields.index(field) + 1)

    text_columns(sheet, [column(f) for f in ('imei', 'imei2', 'serial_number', 'supplier')])
    number_columns(sheet, [column(f) for f in ('cost', 'sale_price', 'battery', 'warranty_days') if f in fields])
    add_cell_list(sheet, column('branch'), cell_list(branches))
    if line == LINE_USED:
        add_cell_list(sheet, column('functional_state'), cell_list(['Sin fallas', 'Con fallas']))
        add_cell_list(sheet, column('grade'), cell_list(grades))

    help_sheet = workbook.create_sheet(HELP_SHEET)
    for row in _stock_help(line):
        help_sheet.append(row)
    help_sheet['A1'].font = Font(bold=True, size=13)
    help_sheet.column_dimensions['A'].width = 34
    help_sheet.column_dimensions['B'].width = 110
    help_sheet.protection.sheet = True          # read-only: it informs, it is never filled

    out = io.BytesIO()
    workbook.save(out)
    return out.getvalue()


def template_line_of(sheet_name) -> str:
    """The line a stock template's data sheet fixes, or '' for any other sheet."""
    return _TEMPLATE_SHEETS.get(normalize_header(sheet_name), '')


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------

def _columns(headers, *, template_line: str = '') -> dict[str, int]:
    """
    field -> column index, from the header row. Refuses a file that is not this template.

    `template_line` (INV-LOAD-02): the file is a stock template of that line. Its «Modelo» is the catalogue model only
    (never a product name), and the NEW template refuses a used-only column instead of ignoring it on every row.
    """
    folded = [normalize_header(header) for header in headers]
    columns = {}
    for field, aliases in _ALIASES.items():
        for index, key in enumerate(folded):
            if key in aliases:
                columns[field] = index
                break
    if template_line:
        if 'name' in columns and columns['name'] == columns.get('model'):
            del columns['name']
        if 'model' not in columns and 'code' not in columns and 'name' not in columns:
            raise UnitImportError('No se encontró la columna «Modelo». Descarga la plantilla y úsala tal cual.')
        if template_line == LINE_NEW:
            extra = [str(headers[columns[field]]) for field in _ALIASES
                     if field in USED_TEMPLATE_FIELDS and field in columns]
            if extra:
                raise UnitImportError(
                    f'La plantilla de equipos nuevos no lleva la columna «{extra[0]}»: es de seminuevos. Quítala, '
                    f'o carga esos equipos con la plantilla «{STOCK_TEMPLATES[LINE_USED]["title"]}».'
                )
    if 'serial_number' not in columns:
        raise UnitImportError(
            'No se encontró la columna «Número de serie». Descarga la plantilla de equipos y úsala tal cual.'
        )
    if 'code' not in columns and 'name' not in columns and not (template_line and 'model' in columns):
        raise UnitImportError('No se encontró la columna «Código» ni «Producto».')
    # An older file that heads the product name «Modelo» maps it to both: the
    # name is tried first, as it always was.
    extra = sorted({str(headers[i]) for i, key in enumerate(folded) if counts_stock(key)})
    if extra:
        raise UnitImportError(
            f'Esta plantilla no lleva la columna «{extra[0]}»: una fila es un equipo. '
            f'Para dos equipos iguales escribe dos filas, cada una con su número de serie.'
        )
    return columns


def _pick_sheet(workbook) -> str:
    names = list(workbook.sheetnames)
    for name in names:
        if normalize_header(name) == normalize_header(SHEET):
            return name
    for name in names:
        if normalize_header(name) != normalize_header(HELP_SHEET):
            return name
    raise UnitImportError('El archivo no tiene una hoja de equipos.')


ZERO_PRICE = 'El precio de venta debe ser mayor que cero; déjalo vacío si aún no tiene precio.'
_FLOAT_NOISE = 1e-9  # Excel noise is ~1e-13; a typed 1899.9999999 is not noise


def _sheet_price(text: str, *, numeric: bool) -> str:
    """
    INV-RECEIVE-PRICE-03 (review B, P2) — the sale price as the sheet MEANT it.

    Excel keeps amounts as binary floats, so a cell showing 1180 can hold 1179.9999999999998. When the source cell
    was a NUMBER and it is within float noise of two decimals, it is that amount («1180.00»); a real third decimal
    (1899.999) — or the same digits typed as text — is passed through as written and refused by the price rule.
    """
    if not numeric or not text:
        return text
    try:
        value = float(text)
    except ValueError:
        return text
    if math.isfinite(value) and abs(value - round(value, 2)) < _FLOAT_NOISE:
        return f'{round(value, 2):.2f}'
    return text


#: The device cost errors of a row, as `import_exports._COST_MESSAGE_PREFIXES` knows them (SEC-IMPORT-01).
COST_MESSAGE_PREFIX = 'Costo del equipo: '


def _sheet_cost(text: str, *, numeric: bool):
    """
    B1-0b-1 — the «Costo» cell as the sheet MEANT it. A TEXT cell is what a person typed: it goes on as text and
    `stock_unit_services._money` reads it by B1-0's policy («5.500» is ambiguous). A NUMBER cell is a number, never
    re-read as text («12.345» is twelve and a third decimal, not twelve thousand): within float noise of two decimals
    it is that amount; otherwise the float goes on and is refused for its third decimal, never rounded in silence.
    """
    if not numeric or not text:
        return text
    try:
        value = float(text)
    except ValueError:
        return text
    if math.isfinite(value) and abs(value - round(value, 2)) < _FLOAT_NOISE:
        return Decimal(f'{round(value, 2):.2f}') + Decimal('0.00')      # «-0» is zero
    return value


def _is_zero(text: str) -> bool:
    try:
        return Decimal(text.strip()) == 0
    except (InvalidOperation, ValueError):
        return False


ENTRY_PURCHASE, ENTRY_OPENING = 'purchase', 'opening'


def _entry_kind(value) -> str:
    if value in (None, ''):
        return ENTRY_PURCHASE
    if value not in (ENTRY_PURCHASE, ENTRY_OPENING):
        raise UnitImportError('Indica si es una compra o inventario inicial.')
    return value


def _folded(text) -> str:
    return ' '.join(str(text or '').split()).lower()


class _SupplierIndex:
    """
    INV-LOAD-01 — «Proveedor» as a person writes it: the supplier's name (case and spaces aside) or its document
    number (RUC/DNI). Only `company`'s suppliers are read, so one of another company is simply not found.
    """

    def __init__(self, company):
        self.by_name, self.by_document = {}, {}
        for supplier in Supplier.objects.filter(company=company).order_by('pk'):
            self.by_name.setdefault(_folded(supplier.name), supplier)
            document = ''.join(supplier.document_number.split())
            if document:
                self.by_document.setdefault(document, supplier)

    def find(self, text) -> tuple:
        """(supplier, '') or (None, the row's error)."""
        supplier = self.by_name.get(_folded(text)) or self.by_document.get(''.join(str(text).split()))
        if supplier is None:
            return None, (f'No existe el proveedor «{str(text)[:60]}» en esta empresa. Escríbelo como está en '
                          f'Proveedores (nombre o RUC) o déjalo vacío.')
        if not supplier.is_active:
            return None, f'El proveedor «{supplier.name}» está desactivado: actívalo o déjalo vacío.'
        return supplier, ''


def _reception_key(values: dict) -> tuple:
    """
    One reception per (branch, product, document, supplier, entry day): a Kardex line a person can read, and one
    supplier and one date per `receive_units` call. The preview's simulation and the apply group by THIS key, so the
    movements the preview announces are the movements the apply writes.
    """
    return (values.get('branch_id') or 0, values.get('product_id') or 0, values.get('reason') or '',
            values.get('supplier_id') or 0, values.get('received_on') or '')


def _chunks(count: int) -> int:
    return -(-count // stock_unit_services.MAX_BATCH)


def simulate(company, staged: list[dict], *, entry_kind: str) -> dict:
    """
    INV-LOAD-01 — what the file would do if it were registered now: per branch × product, the available devices and the
    shelf quantity before and after, and how many Kardex lines of each type it writes. Reads only.

    Built from the rows that WILL be registered, grouped exactly as `apply_units` groups them (`_reception_key`,
    `MAX_BATCH`): the test suite holds the two to the same numbers.
    """
    movement_type = StockMovement.INITIAL_STOCK if entry_kind == ENTRY_OPENING else StockMovement.PURCHASE_ENTRY
    receptions: dict[tuple, int] = {}
    shelves: dict[tuple, int] = {}
    names: dict[tuple, tuple] = {}
    for values in staged:
        receptions[_reception_key(values)] = receptions.get(_reception_key(values), 0) + 1
        shelf = (values.get('branch_id'), values.get('product_id'))
        shelves[shelf] = shelves.get(shelf, 0) + 1
        names.setdefault(shelf, (values.get('branch') or '', values.get('name') or ''))

    branch_ids = {branch for branch, _ in shelves}
    product_ids = {product for _, product in shelves}
    stock = {
        (row['branch_id'], row['product_id']): row['quantity']
        for row in BranchStock.objects.filter(
            branch__company=company, branch_id__in=branch_ids, product_id__in=product_ids,
        ).values('branch_id', 'product_id', 'quantity')
    }
    available: dict[tuple, int] = {}
    for branch_id, product_id in StockUnit.objects.filter(
            company=company, status=StockUnit.Status.AVAILABLE, branch_id__in=branch_ids,
            product_id__in=product_ids).values_list('branch_id', 'product_id'):
        available[(branch_id, product_id)] = available.get((branch_id, product_id), 0) + 1

    rows = []
    for (branch_id, product_id), adds in sorted(shelves.items(), key=lambda item: (names[item[0]], item[0])):
        units_before = available.get((branch_id, product_id), 0)
        stock_before = stock.get((branch_id, product_id), 0)
        rows.append({
            'branch_id': branch_id, 'branch': names[(branch_id, product_id)][0],
            'product_id': product_id, 'product': names[(branch_id, product_id)][1],
            'adds': adds, 'units_before': units_before, 'units_after': units_before + adds,
            'stock_before': stock_before, 'stock_after': stock_before + adds,
        })
    movements = sum(_chunks(count) for count in receptions.values())
    other = (StockMovement.PURCHASE_ENTRY if movement_type == StockMovement.INITIAL_STOCK
             else StockMovement.INITIAL_STOCK)
    return {
        'entry_type': movement_type,
        'movements': {movement_type: movements, other: 0},
        'units': {movement_type: len(staged), other: 0},
        'shelves': rows,
    }


def shelves_now(company, pairs) -> dict[tuple, dict]:
    """
    INV-LOAD-02 — (branch_id, product_id) -> {'units': available devices, 'stock': `BranchStock.quantity`}, as the
    database says NOW (inside the caller's transaction). Reads only.
    """
    pairs = set(pairs)
    branch_ids = {branch for branch, _ in pairs}
    product_ids = {product for _, product in pairs}
    found = {pair: {'units': 0, 'stock': 0} for pair in pairs}
    for row in BranchStock.objects.filter(branch__company=company, branch_id__in=branch_ids,
                                          product_id__in=product_ids).values('branch_id', 'product_id', 'quantity'):
        pair = (row['branch_id'], row['product_id'])
        if pair in found:
            found[pair]['stock'] = row['quantity']
    for pair in StockUnit.objects.filter(company=company, status=StockUnit.Status.AVAILABLE, branch_id__in=branch_ids,
                                         product_id__in=product_ids).values_list('branch_id', 'product_id'):
        if pair in found:
            found[pair]['units'] += 1
    return found


def _opening_problem(branch, product) -> str:
    # INV-OPENING-01 — the same rule as «Recibir mercadería» (lazy import: receipt_services imports this module's peers).
    from .receipt_services import opening_problem
    return opening_problem(branch, product)


SUPPLIERS_FORBIDDEN = ('No tienes permiso para ver proveedores: deja vacía la columna «Proveedor» (lo completa quien '
                       'los administra).')


@transaction.atomic
def preview_units(*, company, actor, upload, filename, reachable_branch_ids,
                  default_branch=None, default_reason: str = '', entry_kind=None,
                  suppliers_allowed: bool = False, line=None) -> BulkImportJob:
    """
    Stage a workbook of devices. Writes NO unit and NO movement.

    `reachable_branch_ids` are the branches this caller may write to. A row for
    any other branch of the company is an error on that row; a branch of another
    company is not found at all, because the lookup starts from this company.

    `suppliers_allowed` (D-F-3, INV-LOAD-01 review): whether this caller may read who sold a device. Without it a filled
    «Proveedor» is a row error with one sentence whatever it says, so a RUC is never turned into a name.

    `line` (INV-LOAD-02): the line the caller says the file loads (`new` / `used`). A stock template says it by its
    sheet; the two must agree. With a line, every row is looked up among the variants of that line only.
    """
    entry_kind = _entry_kind(entry_kind)
    line = clean_line_option(line)
    data = xlsx_reader.check_upload(upload, filename=filename)
    workbook, _notes = xlsx_reader.load_workbook(data)
    sheet_name = _pick_sheet(workbook)
    refusal = report_sheet_refusal(sheet_name)
    if refusal:
        # INVENTORY-UNIFIED-01: a report is read, not loaded. Before anything is staged.
        raise UnitImportError(refusal)
    try:
        headers, rows, _truncated = xlsx_reader.read_rows(
            workbook, sheet_name, header_row=1, limit=MAX_UNITS + 1, mode=xlsx_reader.FULL_IMPORT,
        )
    except xlsx_reader.XlsxError:
        raise UnitImportError(f'Se cargan hasta {MAX_UNITS} equipos por archivo. Divide el archivo.') from None
    template_line = template_line_of(sheet_name)
    if line and template_line and line != template_line:
        raise UnitImportError(
            f'Este archivo es la plantilla de {LINE_LABELS[template_line]} y elegiste cargar {LINE_LABELS[line]}. '
            f'Sube la plantilla que corresponde.'
        )
    line = line or template_line
    columns = _columns(headers, template_line=template_line)

    reachable = set(reachable_branch_ids)
    branches = {
        ' '.join(branch.name.split()).lower(): branch
        for branch in Branch.objects.filter(company=company)
    }
    fallback = None
    if default_branch is not None:
        fallback = next((b for b in branches.values() if b.pk == default_branch), None)
        if fallback is None:
            raise UnitImportError('La sucursal indicada no pertenece a esta empresa.')
    default_reason = ' '.join(str(default_reason or '').split())

    # DC2 review — a device's sale price is the catalogue's decision.
    prices_allowed = device_catalog.user_may_manage_products(actor, company)
    # COST-WRITE-AUTHORITY (owner's decision C) — a device's cost is written only by `costs.view`.
    costs_allowed = cost_services.user_may_view_costs(actor, company)
    index = CatalogueIndex(company)
    products = {product.pk: product for product in Product.objects.filter(company=company)}
    conditions = _conditions()
    # INV-UNIT-CORRECT-01: a voided device frees its identifiers.
    live_units = StockUnit.objects.filter(company=company).exclude(status=StockUnit.Status.VOIDED)
    in_stock_serials = set(live_units.values_list('product_id', 'serial_number'))
    # DC1 — a SOLD device is history: its sale was valued at its cost.
    sold_serials = set(StockUnit.objects.filter(
        company=company, status=StockUnit.Status.SOLD).values_list('product_id', 'serial_number'))
    # INV-SERIAL-01 / INV-LOAD-01 — a serial already on ANOTHER product of the company: a warning, as in the receipt.
    serial_products: dict[str, dict[int, str]] = {}
    for serial, product_pk, product_name in live_units.exclude(serial_number__isnull=True).exclude(
            serial_number='').values_list('serial_number', 'product_id', 'product__name'):
        serial_products.setdefault(serial, {})[product_pk] = product_name
    # INV-LOAD-01 — «Proveedor» by name or RUC, only among THIS company's suppliers.
    suppliers = _SupplierIndex(company)
    in_stock_imeis = set()
    for imei, imei2 in live_units.filter(
        Q(imei__isnull=False) | Q(imei2__isnull=False),
    ).values_list('imei', 'imei2'):
        in_stock_imeis.update(value for value in (imei, imei2) if value)

    job = BulkImportJob.objects.create(
        company=company, import_type=BulkImportJob.UNITS,
        original_filename=_safe_filename(filename), file_sha256=_sha256(data),
        mapping_snapshot={'sheet_name': sheet_name, 'mapping': columns,
                          'headers': [str(header) for header in headers]},
        options_snapshot={'default_branch': getattr(fallback, 'pk', None), 'default_reason': default_reason,
                          'entry_kind': entry_kind, 'line': line},
        created_by=actor,
    )

    staged, counts = [], dict(create=0, skip=0, error=0)
    seen_serials: dict[tuple[int, str], int] = {}
    unmarked = 0
    seen_imeis: dict[str, int] = {}
    seen_serial_products: dict[str, dict[int, str]] = {}
    branch_ids = set()

    def stage(row_number, action, *, key='', data=None, errors=None, warnings=None):
        staged.append(BulkImportRow(
            job=job, sheet_name=sheet_name, row_number=row_number, action=action, match_key=key[:120],
            normalized_data=data or {}, errors=errors or [], warnings=warnings or [],
        ))
        counts[action] += 1

    catalog_cache: dict = {}     # A2: models, palettes and the vocabulary, read once per pass
    for row_number, values, _numeric in rows:
        def cell(field):
            raw = values.get(columns.get(field)) if field in columns else None
            if raw is xlsx_reader.FORMULA:
                return raw
            return '' if raw is None else str(raw).strip()

        fields = {field: cell(field) for field in _ALIASES}
        if not any(value for value in fields.values() if value is not xlsx_reader.FORMULA):
            if any(value is xlsx_reader.FORMULA for value in fields.values()):
                stage(row_number, BulkImportRow.ERROR, errors=['La fila tiene fórmulas: escribe los valores.'])
            else:
                stage(row_number, BulkImportRow.SKIP, warnings=['Fila vacía.'])
            continue
        if any(value is xlsx_reader.FORMULA for value in fields.values()):
            stage(row_number, BulkImportRow.ERROR, errors=['La fila tiene fórmulas: escribe los valores.'])
            continue
        if fields['serial_number'].upper().startswith(EXAMPLE_SERIAL_PREFIX):
            stage(row_number, BulkImportRow.SKIP, key=fields['serial_number'],
                  warnings=['Fila de ejemplo de la plantilla: no se carga. Reemplázala por tus equipos.'])
            continue

        errors, warnings, imei_unmarked = [], [], False
        data = {'name': fields['name'] or fields['code']}

        # -- which product ----------------------------------------------------
        if template_line and not (fields['code'] or fields['name']):
            # INV-LOAD-02 — a stock template names the model and its options, never a product.
            product_id, match_error, match_warnings = None, None, []
            if not fields['model']:
                match_error = 'Indica el «Modelo» del equipo.'
        else:
            product_id, match_error, _matched_by, match_warnings = index.match(
                code=fields['code'], name=fields['name'],
            )
        line_text, errors_line = fields['line'], ''
        if line:
            # INV-LOAD-02 — the line of the template (or of the screen) decides; a «Línea» cell may only agree.
            try:
                if device_catalog.clean_line(fields['line'], default=line) != line:
                    errors_line = f'Este archivo carga {LINE_LABELS[line]}: la fila dice «{fields["line"][:20]}».'
            except device_catalog.CatalogError as exc:
                errors_line = str(exc)
            line_text = _LINE_WORD[line]
        # DC2 — no product by code or name, and the row names a model: the
        # variant of that model with these options in that line.
        if (product_id is None or match_error) and fields['model']:
            try:
                notes = []
                spec = device_catalog.resolve_variant_spec(
                    company, model_name=fields['model'], line_text=line_text,
                    labels=[fields['capacity'], fields['colour'], fields['sim'], fields['region']],
                    cache=catalog_cache, notes=notes,
                )
            except device_catalog.CatalogError as exc:
                if not match_error:
                    match_error = str(exc)
            else:
                variant = device_catalog.find_variant(company, spec)
                if variant is None:
                    match_error = (f'«{spec["model_name"]}» no tiene esa variante. '
                                   f'Créala en Catálogo › Modelos.')
                else:
                    # A2 (P8): what the company's vocabulary read (an alias, «256gb», a folded label) is said.
                    product_id, match_error, match_warnings = variant.pk, None, list(notes)
        warnings.extend(match_warnings)
        product = products.get(product_id)
        if errors_line:
            errors.append(errors_line)
        if match_error:
            errors.append(match_error)
        elif product is None:
            errors.append('No existe un producto con ese código o nombre en esta empresa.')
        elif not product.is_serialized:
            errors.append(
                f'«{product.name}» no se controla por número de serie. Actívalo en Inventario › Equipos '
                f'antes de cargar sus equipos.'
            )
            product = None
        elif line and product.condition_line != line:
            # INV-LOAD-02 — a code or a name that points at a variant of the OTHER line.
            word = _LINE_WORD.get(product.condition_line, product.condition_line)
            errors.append(f'«{product.name}» es de la línea «{word}»: este archivo carga {LINE_LABELS[line]}.')
            product = None
        if product is not None:
            data.update(product_id=product.pk, name=product.name)

        # -- which branch -----------------------------------------------------
        branch = None
        if fields['branch']:
            branch = branches.get(' '.join(fields['branch'].split()).lower())
            if branch is None:
                errors.append(f'No existe la sucursal «{fields["branch"][:60]}» en esta empresa.')
        elif fallback is not None:
            branch = fallback
        else:
            errors.append('Indica la sucursal del equipo.')
        if branch is not None:
            if not branch.is_active:
                errors.append(f'La sucursal {branch.name} está inactiva.')
                branch = None
            elif branch.pk not in reachable:
                errors.append(f'No tienes acceso a la sucursal {branch.name}.')
                branch = None
        if branch is not None and product is not None and entry_kind == ENTRY_OPENING:
            problem = _opening_problem(branch, product)
            if problem:
                errors.append(problem)
        if branch is not None:
            data.update(branch_id=branch.pk, branch=branch.name)
            # Counted even if the row ends in error: an error row still carries
            # the serial and the IMEI, and they belong to this branch.
            branch_ids.add(branch.pk)

        # -- the device itself ------------------------------------------------
        condition = ''
        if fields['condition']:
            condition = conditions.get(normalize_header(fields['condition']), '')
            if not condition:
                labels = ', '.join(label for _value, label in StockUnit.Condition.choices)
                errors.append(f'Condición desconocida: «{fields["condition"][:40]}». Usa {labels}.')

        if product is not None and fields['line']:
            try:
                wanted_line = device_catalog.clean_line(fields['line'])
            except device_catalog.CatalogError as exc:
                errors.append(str(exc))
            else:
                if wanted_line != product.condition_line:
                    errors.append(f'«{product.name}» no es de la línea «{fields["line"][:20]}».')
        sale_price = _sheet_price(fields['sale_price'], numeric=columns.get('sale_price') in _numeric)
        if sale_price and product is not None and product.condition_line != 'used':
            warnings.append('Precio de venta ignorado: un equipo nuevo se vende al precio de su variante.')
            sale_price = ''
        elif sale_price and not prices_allowed:
            warnings.append('Precio de venta ignorado: sólo quien administra productos fija precios.')
            sale_price = ''
        elif sale_price and _is_zero(sale_price):
            # The owner's rule: a price is greater than zero. An empty cell is «no price yet», never a zero.
            errors.append(ZERO_PRICE)
            sale_price = ''

        # INV-LOAD-01 — the buyer reads details only of a used device; a new one is described by its variant.
        public_notes = fields['public_notes']
        if public_notes and product is not None and product.condition_line != 'used':
            warnings.append('Detalles para el comprador ignorados: sólo se indican en equipos seminuevos.')
            public_notes = ''

        # INV-LOAD-01 — who sold it and when it entered.
        if fields['supplier'] and not suppliers_allowed:
            errors.append(SUPPLIERS_FORBIDDEN)
        elif fields['supplier']:
            supplier, problem = suppliers.find(fields['supplier'])
            if problem:
                errors.append(problem)
            else:
                data.update(supplier_id=supplier.pk, supplier=supplier.name)
        try:
            # A date cell arrives as its ISO text (`xlsx_reader.normalize_scalar`); a typed one as written.
            received_on = stock_unit_services.clean_received_on(fields['received_on'])
        except stock_unit_services.StockUnitError as exc:
            errors.append(str(exc))
        else:
            if received_on is not None:
                data['received_on'] = received_on.isoformat()

        # COST-WRITE-AUTHORITY — without `costs.view` a filled «Costo» is an error of the row, the same
        # sentence whatever it says: the value is not read, and a file with errors registers nothing.
        cost_cell = fields['cost']
        if cost_cell and not costs_allowed:
            errors.append(cost_services.COSTS_IN_FILE)
            cost_cell = ''
        cost_cell = _sheet_cost(cost_cell, numeric=columns.get('cost') in _numeric)

        # USED-READY-01 — warranty, functional state, defects and accessories are a used device's: in a new one they
        # are ignored and said, like its price.
        used_details = {name: fields[name] for name in _USED_COLUMNS if fields[name]}
        if used_details and product is not None and product.condition_line != 'used':
            warnings.append(f'{", ".join(_USED_COLUMNS[n] for n in used_details)}: ignorado en un equipo nuevo.')
            used_details = {}

        cleaned = None
        if product is not None:
            try:
                cleaned = stock_unit_services.clean_unit(product, {
                    **used_details,
                    'serial_number': fields['serial_number'], 'imei': fields['imei'], 'imei2': fields['imei2'],
                    # INV-INTAKE-GUARD-01: an empty «Condición» takes the variant's line («Usado» in a used one), and a
                    # condition that contradicts it is refused by the cleaning itself (a row error).
                    'condition': condition, 'cost': cost_cell,
                    # DC2 — optional; cleaned by the same rules as the screen.
                    'battery_health': fields['battery'], 'cosmetic_grade': fields['grade'],
                    'sale_price': sale_price,
                    # INV-LOAD-01 — cleaned by the same rules as the screen: too long is an error, never a cut.
                    'notes': fields['notes'], 'public_notes': public_notes,
                })
            except stock_unit_services.StockUnitError as exc:
                # B1-0b-1: a cost that cannot be read may repeat the cell («7,391» es ambiguo…). The prefix lets
                # `import_exports.cost_free_message` hide the amount from a reader without `costs.view`.
                errors.append(f'{COST_MESSAGE_PREFIX}{exc}' if exc.field == 'cost' else str(exc))
            else:
                warning = stock_unit_services.condition_line_warning(product, condition=cleaned['condition'])
                if warning:
                    warnings.append(warning[0].upper() + warning[1:])
                # IMPORT-SPLIT-01. The rule of the screen stands — an IMEI is
                # optional for a product not marked as carrying one — but a
                # file is where a row lands under the wrong product, so it says so.
                if not product.requires_imei and cleaned['imei']:
                    imei_unmarked = True
                    warnings.append(
                        f'«{product.name}» no está marcado «lleva IMEI»: el IMEI se guarda, pero no se exigirá '
                        f'al vender. Si la fila es de otro producto, corrígela antes de cargar.'
                    )
        elif not fields['serial_number'] and not fields['imei']:
            # The product is unknown (its own error says so): with an IMEI the serial may be optional once it is
            # found (SERIAL-OPTIONAL-01), so only a row with neither is told about the serial here.
            errors.append('Cada equipo lleva su número de serie.')

        if cleaned is not None:
            serial_key = (product.pk, cleaned['serial_number'])
            imeis = [value for value in (cleaned['imei'], cleaned['imei2']) if value]
            if not cleaned['serial_number']:
                # SERIAL-OPTIONAL-01 — a device that entered by its IMEI alone: the IMEI checks below tell it apart.
                pass
            elif serial_key in seen_serials:
                errors.append(
                    f'Este número de serie está repetido en el archivo (fila {seen_serials[serial_key]}).')
            elif serial_key in sold_serials:
                errors.append(
                    f'El equipo {cleaned["serial_number"]} ya fue vendido: no se vuelve a cargar y su costo '
                    f'no se puede cambiar.')
            elif serial_key in in_stock_serials:
                errors.append(
                    f'El número de serie {cleaned["serial_number"]} ya está registrado para este producto.')
            for value in imeis:
                if value in seen_imeis:
                    errors.append(f'El IMEI {value} está repetido en el archivo (fila {seen_imeis[value]}).')
                elif value in in_stock_imeis:
                    errors.append(f'El IMEI {value} ya está registrado en otro equipo de la empresa.')
            if cleaned['serial_number']:
                # INV-SERIAL-01 — the same serial on ANOTHER product (in stock, or earlier in this file): a WARNING.
                # The database keys a serial per product; a device without an IMEI could enter twice under two
                # variants, and only the person can tell whether it is the same device.
                serial = cleaned['serial_number']
                elsewhere = [name for pk, name in serial_products.get(serial, {}).items() if pk != product.pk]
                in_file = [name for pk, name in seen_serial_products.get(serial, {}).items() if pk != product.pk]
                if elsewhere:
                    warnings.append(f'Ese número de serie ya está registrado en «{elsewhere[0]}». Si es el mismo '
                                    f'equipo, no lo cargues otra vez.')
                elif in_file:
                    warnings.append(f'Ese número de serie aparece también en «{in_file[0]}» en este archivo.')
                seen_serial_products.setdefault(serial, {})[product.pk] = product.name
                seen_serials.setdefault(serial_key, row_number)
            for value in imeis:
                seen_imeis.setdefault(value, row_number)
            data.update(
                # INV-INTAKE-GUARD-01: the line this row was checked against (the apply tells a changed catalogue
                # from a job previewed before the rule).
                checked_line=product.condition_line,
                serial_number=cleaned['serial_number'], imei=cleaned['imei'] or '',
                imei2=cleaned['imei2'] or '', condition=cleaned['condition'],
                cost=str(cleaned['cost']) if cleaned['cost'] is not None else '',
            )
            if 'battery_health' in cleaned:
                data['battery_health'] = cleaned['battery_health']
            if cleaned.get('cosmetic_grade') is not None:
                data['cosmetic_grade'] = cleaned['cosmetic_grade'].pk
                data['cosmetic_grade_label'] = cleaned['cosmetic_grade'].label
            if cleaned.get('sale_price') is not None:
                data['sale_price'] = str(cleaned['sale_price'])
            if cleaned.get('notes'):
                data['notes'] = cleaned['notes']
            if cleaned.get('public_notes'):
                data['public_notes'] = cleaned['public_notes']
            for name in _USED_COLUMNS:
                if name in cleaned:
                    data[name] = cleaned[name]
        else:
            data.update(serial_number=fields['serial_number'][:64])

        # -- why it came in ---------------------------------------------------
        try:
            reason = stock_unit_services.clean_reason(fields['reason'] or default_reason)
            data['reason'] = reason
        except stock_unit_services.StockUnitError:
            errors.append('Indica el motivo o el documento de ingreso (en la columna o al subir el archivo).')

        if errors:
            stage(row_number, BulkImportRow.ERROR, key=fields['serial_number'], data=data,
                  errors=errors, warnings=warnings)
            continue
        stage(row_number, BulkImportRow.CREATE, key=cleaned['serial_number'] or cleaned['imei'] or '', data=data,
              warnings=warnings)
        unmarked += imei_unmarked            # only the rows that will be registered

    BulkImportRow.objects.bulk_create(staged, batch_size=500)
    simulation = simulate(company, [row.normalized_data for row in staged if row.action == BulkImportRow.CREATE],
                          entry_kind=entry_kind)
    job.rows_total = len(staged)
    job.rows_create = counts['create']
    job.rows_skip = counts['skip']
    job.rows_error = counts['error']
    job.mapping_snapshot = {**job.mapping_snapshot, 'branch_ids': sorted(branch_ids)}
    # How many rows carried an IMEI under a product not marked as carrying one:
    # kept, said on each row, and counted here so the load's audit line can say it.
    job.summary = {'units': counts['create'], 'imei_on_unmarked_products': unmarked, 'simulation': simulation}
    job.save(update_fields=['rows_total', 'rows_create', 'rows_skip', 'rows_error',
                            'mapping_snapshot', 'summary'])
    return job


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------

@transaction.atomic
def apply_units(*, job, actor, request=None):
    """
    Register every staged device. ALL OF THE FILE OR NONE OF IT.

    The preview is a photograph; the stock kept moving after it was taken. So
    nothing staged is trusted to still be true: each batch goes through
    `receive_units`, which checks every serial and IMEI again against what is
    in stock NOW. The first row that can no longer enter aborts the transaction
    and names its row, and the job stays previewed.

    Returns `(job, changed)`. A second apply of an applied job changes nothing.
    """
    job = BulkImportJob.objects.select_for_update().get(pk=job.pk)
    if job.status == BulkImportJob.APPLIED:
        return job, False
    if job.import_type != BulkImportJob.UNITS:
        raise UnitImportError('Este trabajo no es una carga de equipos.')
    if job.rows_error:
        raise UnitImportError(
            f'Hay {job.rows_error} fila(s) con error. Corrige el archivo y vuelve a subirlo.')
    # IMPORT-STALE-01 — the age is the only new check: every serial and IMEI is
    # re-validated below, under lock, by `receive_units`.
    from . import import_freshness

    import_freshness.refuse_if_expired(job, UnitImportError)

    rows = list(job.rows.filter(action=BulkImportRow.CREATE).order_by('row_number', 'pk'))
    if not rows:
        raise UnitImportError('El archivo no tiene equipos que registrar.')
    # COST-WRITE-AUTHORITY — asked again of whoever APPLIES: a job previewed with costs by somebody who
    # holds `costs.view` is not a way to write them for somebody who does not. Nothing is written.
    if not cost_services.user_may_view_costs(actor, job.company) and cost_services.staged_rows_carry_cost(
            rows, ('cost',)):
        raise UnitImportError(cost_services.COSTS_IN_JOB, code=cost_services.COSTS_FORBIDDEN)

    def data(row):
        return row.normalized_data or {}

    branches = {b.pk: b for b in Branch.objects.filter(
        company=job.company, is_active=True, pk__in={data(r).get('branch_id') for r in rows})}
    products = {p.pk: p for p in Product.objects.filter(
        company=job.company, pk__in={data(r).get('product_id') for r in rows})}
    # INV-LOAD-01 — the suppliers named by the file, still this company's and still active.
    suppliers = {s.pk: s for s in Supplier.objects.filter(
        company=job.company, is_active=True, pk__in={data(r).get('supplier_id') for r in rows} - {None})}

    # One reception per (branch, product, document): a Kardex line a person can
    # read. Sorted, so two uploads at once take their locks in the same order.
    groups: OrderedDict[tuple, list] = OrderedDict()
    for row in sorted(rows, key=lambda r: (_reception_key(data(r)), r.row_number)):
        values = data(row)
        if branches.get(values.get('branch_id')) is None or products.get(values.get('product_id')) is None or (
                values.get('supplier_id') and suppliers.get(values['supplier_id']) is None):
            raise UnitImportError(
                f'El catálogo o las sucursales cambiaron desde la previsualización (fila {row.row_number}). '
                f'Vuelve a subir el archivo.'
            )
        # INV-INTAKE-GUARD-01 — asked again: the variant's line may have changed since the preview, or the job was
        # previewed before the line rule existed (it has no `checked_line`): each is told as what it is.
        product = products[values['product_id']]
        problem = stock_unit_services.condition_line_problem(
            product, condition=values.get('condition') or StockUnit.Condition.NEW,
            graded=values.get('cosmetic_grade') not in (None, ''))
        if problem:
            if values.get('checked_line') in (None, product.condition_line):
                raise UnitImportError(
                    f'Las reglas de condición cambiaron desde la previsualización: vuelve a subir el archivo. '
                    f'No se cargó ningún equipo (fila {row.row_number}). {problem[1]}'
                )
            raise UnitImportError(
                f'No se cargó ningún equipo: el catálogo cambió desde la previsualización (fila {row.row_number}). '
                f'{problem[1]} Vuelve a subir el archivo.'
            )
        groups.setdefault(_reception_key(values), []).append(row)

    # INV-OPENING-01 — a job staged before the option has none: a purchase, as it was.
    opening = (job.options_snapshot or {}).get('entry_kind') == ENTRY_OPENING
    movement_type = StockMovement.INITIAL_STOCK if opening else StockMovement.PURCHASE_ENTRY
    if opening:
        # INV-LOAD-01 review — every shelf of the file is locked BEFORE the check (rows created where missing), so a
        # sale, a hold or a write-off cannot commit between the check and the INITIAL_STOCK lines.
        from .receipt_services import lock_shelves

        shelves = sorted({(key[0], key[1]) for key in groups})
        lock_shelves((branches[branch_id], products[product_id]) for branch_id, product_id in shelves)
        for branch_id, product_id in shelves:
            problem = _opening_problem(branches[branch_id], products[product_id])
            if problem:
                raise UnitImportError(f'No se cargó ningún equipo: el estante cambió desde la previsualización. {problem}')

    registered, movements, unmarked = 0, 0, 0
    received_units = []
    for (branch_id, product_id, reason, supplier_id, received_on), members in groups.items():
        for start in range(0, len(members), stock_unit_services.MAX_BATCH):
            chunk = members[start:start + stock_unit_services.MAX_BATCH]
            try:
                created = stock_unit_services.receive_units(
                    branch=branches[branch_id], product=products[product_id],
                    units=[{
                        'serial_number': data(row).get('serial_number'), 'imei': data(row).get('imei'),
                        'imei2': data(row).get('imei2'), 'condition': data(row).get('condition'),
                        'cost': data(row).get('cost'),
                        # DC2 — absent keys in a job staged before them: nothing.
                        'battery_health': data(row).get('battery_health'),
                        'cosmetic_grade': data(row).get('cosmetic_grade'),
                        'sale_price': data(row).get('sale_price'),
                        # INV-LOAD-01 — absent in a job staged before them: nothing, as before.
                        'notes': data(row).get('notes'), 'public_notes': data(row).get('public_notes'),
                        # USED-READY-01 — never an inspection or a publication: they enter pending, unpublished.
                        **{name: data(row)[name] for name in _USED_COLUMNS if name in data(row)},
                    } for row in chunk],
                    actor=actor, reason=reason, movement_type=movement_type, request=request,
                    supplier=suppliers.get(supplier_id), received_on=received_on or None,
                )
            except stock_unit_services.StockUnitError as exc:
                where = (
                    f'fila {chunk[exc.line - 1].row_number}' if exc.line and exc.line <= len(chunk)
                    else f'filas {chunk[0].row_number}–{chunk[-1].row_number}'
                )
                raise UnitImportError(
                    f'No se cargó ningún equipo: cambió algo desde la previsualización ({where}). {exc}'
                ) from None
            registered += len(created)
            received_units.extend(created)
            # Counted from what was STORED: «lleva IMEI» may have been switched since the preview.
            if not products[product_id].requires_imei:
                unmarked += sum(1 for unit in created if unit.imei)
            movements += 1

    # LABEL-A — one batch per branch, only here: a second apply returned above, on the APPLIED state.
    label_failed = sum(batch is label_batches.FAILED
                       for batch in label_batches.create_for_import_job(job, received_units, actor=actor))

    # INV-LOAD-02 — which devices this load created (the batch annulment acts on exactly these, never on a guess) and
    # what each shelf it touched holds right after it (the «after» of the before/after report).
    after = shelves_now(job.company, {(key[0], key[1]) for key in groups})
    job.status = BulkImportJob.APPLIED
    job.applied_by = actor
    job.applied_at = timezone.now()
    job.summary = {**(job.summary or {}), 'applied': {
        'units': registered, 'movements': movements, 'imei_on_unmarked_products': unmarked,
        'unit_ids': sorted(unit.pk for unit in received_units),
        'shelves': [{'branch_id': branch_id, 'product_id': product_id, **counts}
                    for (branch_id, product_id), counts in sorted(after.items())],
        # Only when something failed, so the answer of a repeated apply can say so too.
        **({'label_failed': label_failed} if label_failed else {})}}
    job.save(update_fields=['status', 'applied_by', 'applied_at', 'summary'])

    AdminAuditLog.log(
        actor=actor, action='stock_units_import_applied', target_type='bulk_import_job', target_id=job.pk,
        # How many and where. The identifiers are on the units themselves.
        metadata={'company_id': job.company_id, 'job_id': job.pk, 'units': registered,
                  'movements': movements, 'branch_ids': sorted({key[0] for key in groups}),
                  'file_sha256': job.file_sha256,
                  # IMEIs stored under a product not marked «lleva IMEI»: how many, never which.
                  'imei_on_unmarked_products': unmarked},
        request=request, company=job.company,
    )
    return job, True
