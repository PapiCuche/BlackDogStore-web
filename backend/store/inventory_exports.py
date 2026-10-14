"""
Downloads of the inventory. INVENTORY-UNIFIED-01, part 2.

    A FILE TO RELOAD IS NOT A REPORT, AND THE FILE SAYS WHICH IT IS.

Three workbooks, ONE sheet each:

  products  — the catalogue, one row per product;
  stock     — quantities, one column per branch;
  units     — devices, one row per physical unit;

and `full`: the three of them in a ZIP, with a note.

TWO MODES
---------
`reimport` is exactly what the matching importer asks for — same sheet name,
same headers, taken from the importers' own constants — so the file goes back
in unchanged. The catalogue adds «Estado» (Activo / Inactivo) to the template's
columns: active AND inactive products travel (owner's decision, 2026-10-13),
and the importer reads that column only to decide how a NEW product is born —
it never activates or deactivates one that exists. Loaded into the SAME shop it changes nothing: a product matches
itself, a quantity equals itself, and a device that is already registered is
refused row by row. That last one is the point: reloading can never make stock
twice.

`report` adds columns that are only read (state, totals, dates). Its sheet is
named «Reporte de …» and every importer refuses that sheet by name
(`import_exports.report_sheet_refusal`): a report half-imported is a trap.

WHAT A FILE TO RELOAD LEAVES OUT, ON PURPOSE
--------------------------------------------
  · stock:  products counted by serial number. The stock importer refuses a
            quantity for them, so a file that carried one would not go back in.
  · units:  devices that are sold or written off. Reloading them would put
            them back on the shelf.

THE FILES ARE A TENANT'S DATA ON SOMEBODY'S DESKTOP
---------------------------------------------------
Text that a spreadsheet would run as a formula is guarded
(`xlsx_reader.guard_text`) and read back the same by the importers. Codes,
serials and IMEIs are text. Nothing a user typed reaches a file name. There is
a cap on rows and on the size of the ZIP. Cost is written only for a caller
who may adjust inventory.

There is NO brand column: the catalogue has no brand field, and none is invented.
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass, field

from django.db.models import IntegerField, OuterRef, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from . import import_exports, product_publication, unit_import_services
from .import_formats import (
    DESTINATION_INTERNAL, DESTINATION_LABELS, DESTINATION_ONLINE, STATE_HEADER, STATE_LABELS,
)
from .models import BranchStock, Product, StockUnit
from .xlsx_reader import guard_text

KINDS = ('products', 'stock', 'units', 'full')
MODE_REIMPORT, MODE_REPORT = 'reimport', 'report'
MODES = (MODE_REIMPORT, MODE_REPORT)

#: Data rows in ONE workbook. Beyond it the download is refused with the count:
#: the workbook is built in memory, inside a request.
MAX_EXPORT_ROWS = 20000
#: The ZIP of the full download, also built in memory.
MAX_ZIP_BYTES = 25 * 1024 * 1024

XLSX_CONTENT_TYPE = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
ZIP_CONTENT_TYPE = 'application/zip'

#: (kind, mode) -> the name the browser saves. Fixed: nothing a user typed.
FILENAMES = {
    ('products', MODE_REIMPORT): 'productos-para-cargar.xlsx',
    ('products', MODE_REPORT): 'reporte-productos.xlsx',
    ('stock', MODE_REIMPORT): 'stock-para-cargar.xlsx',
    ('stock', MODE_REPORT): 'reporte-stock.xlsx',
    ('units', MODE_REIMPORT): 'equipos-para-cargar.xlsx',
    ('units', MODE_REPORT): 'reporte-equipos.xlsx',
    ('full', MODE_REIMPORT): 'inventario-completo.zip',
}
#: The names INSIDE the ZIP.
ZIP_PRODUCTS, ZIP_STOCK, ZIP_UNITS, ZIP_NOTE = 'Productos.xlsx', 'Stock.xlsx', 'Equipos.xlsx', 'LEEME.txt'

PRODUCT_SHEET = 'Productos'
#: The file to RELOAD: the owner's template plus what only a download knows.
#: The template itself does not gain these columns.
PRODUCT_RELOAD_HEADERS = import_exports.PRODUCT_TEMPLATE_HEADERS + [STATE_HEADER]
PRODUCT_REPORT_HEADERS = import_exports.PRODUCT_TEMPLATE_HEADERS + [
    'Activo', 'Tipo', 'Stock total', 'Publicado', 'Problemas para publicar',
]
STOCK_REPORT_HEADERS = ['Código', 'Código de barras', 'Producto', 'Categoría', 'Tipo', 'Sucursal', 'Cantidad']
#: The report says what happened to each device instead of asking why it came in.
UNIT_REPORT_HEADERS = unit_import_services.HEADERS[:-1] + ['Estado', 'Fecha de ingreso', 'Pedido', 'Notas']

TYPE_SERIALIZED, TYPE_QUANTITY = 'serialized', 'quantity'
TYPE_LABELS = {True: 'Por serie', False: 'Por cantidad'}
STOCK_IN, STOCK_OUT = 'in_stock', 'out_of_stock'
VISIBILITY_PUBLISHED, VISIBILITY_INTERNAL = 'published', 'internal'

#: A device that is physically in the shop. The others left: sold or written off.
ON_THE_SHELF = (StockUnit.Status.AVAILABLE, StockUnit.Status.RESERVED)

#: filter -> the values it takes. Anything else is refused, never ignored.
FILTER_CHOICES = {
    'type': (TYPE_SERIALIZED, TYPE_QUANTITY),
    'stock': (STOCK_IN, STOCK_OUT),
    'condition': tuple(value for value, _label in StockUnit.Condition.choices),
    'visibility': (VISIBILITY_PUBLISHED, VISIBILITY_INTERNAL),
    'is_active': ('true', 'false'),
}


class ExportError(Exception):
    """The download was refused. `code` is for the client, the message for the person."""

    code = 'export_refused'

    def payload(self) -> dict:
        return {'code': self.code, 'detail': str(self)}


class TooManyRows(ExportError):
    code = 'too_many_rows'

    def __init__(self, count: int):
        self.count, self.limit = count, MAX_EXPORT_ROWS
        super().__init__(
            f'Son {count} filas y una descarga admite hasta {self.limit}. '
            'Acota con los filtros (sucursal, categoría, tipo) y descarga por partes.'
        )

    def payload(self) -> dict:
        return {**super().payload(), 'count': self.count, 'limit': self.limit}


class TooLarge(ExportError):
    code = 'too_large'

    def __init__(self):
        super().__init__(
            'El archivo completo supera el tamaño admitido. Descarga productos, stock y equipos por separado.'
        )


@dataclass
class ExportFilters:
    """
    What to download, ALREADY VALIDATED by the view.

    `branches` are the caller's own (`tenancy.visible_branches`), narrowed by
    what they asked for; `category` is one of this company's. Nothing here is
    an id taken on trust.
    """

    branches: list = field(default_factory=list)
    category: object = None
    type: str = ''
    stock: str = ''
    condition: str = ''
    visibility: str = ''
    is_active: str = ''
    #: Whether «Costo» is written: only for a caller who may adjust inventory.
    include_cost: bool = False

    def audit(self) -> dict:
        return {
            'branches': sorted(branch.pk for branch in self.branches),
            'category': self.category.pk if self.category else None,
            'type': self.type, 'stock': self.stock, 'condition': self.condition,
            'visibility': self.visibility, 'is_active': self.is_active,
        }


# =============================================================================
# Writing a workbook
# =============================================================================

def _text(value) -> str | None:
    """A text cell: without what a workbook cannot hold, and never a formula."""
    from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE

    text = ILLEGAL_CHARACTERS_RE.sub('', str(value or ''))
    return guard_text(text) or None


def _workbook_bytes(title: str, headers, rows, *, text_columns=(), widths=()) -> bytes:
    """
    ONE sheet, written in write-only mode: rows are streamed to the file as
    they come instead of being held as cell objects.

    `text_columns` are the indexes of identifiers (codes, serials, IMEIs): their
    cells are text whatever they look like, so no zero is lost and no IMEI
    becomes 4.90154E+14. Every other string is text too; numbers stay numbers.
    """
    import openpyxl
    from openpyxl.cell import WriteOnlyCell
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter

    workbook = openpyxl.Workbook(write_only=True)
    sheet = workbook.create_sheet(title)
    # In write-only mode the layout is declared BEFORE the first row.
    sheet.freeze_panes = 'A2'
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width

    bold, fill = Font(bold=True, color='FFFFFF'), PatternFill('solid', fgColor='1F2937')
    head = []
    for title_ in headers:
        # A header can carry a tenant's text too («ALMACEN <branch name>»): the
        # same cleaning as any cell, or one control character is a 500.
        cell = WriteOnlyCell(sheet, value=_text(title_))
        cell.font, cell.fill = bold, fill
        head.append(cell)
    sheet.append(head)

    text_columns = set(text_columns)
    for row in rows:
        cells = []
        for index, value in enumerate(row):
            if isinstance(value, str):
                value = _text(value)
            if value is not None and index in text_columns:
                cell = WriteOnlyCell(sheet, value=value)
                cell.number_format = '@'
                cells.append(cell)
            else:
                cells.append(value)
        sheet.append(cells)

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _check_rows(count: int) -> None:
    if count > MAX_EXPORT_ROWS:
        raise TooManyRows(count)


# =============================================================================
# Which products
# =============================================================================

def _products(company, filters: ExportFilters):
    """This company's products, narrowed by every filter that is about a product."""
    queryset = Product.objects.filter(company=company)
    if filters.category is not None:
        queryset = queryset.filter(category=filters.category)
    if filters.type:
        queryset = queryset.filter(is_serialized=filters.type == TYPE_SERIALIZED)
    if filters.visibility:
        queryset = queryset.filter(is_published_online=filters.visibility == VISIBILITY_PUBLISHED)
    if filters.is_active:
        queryset = queryset.filter(is_active=filters.is_active == 'true')
    return queryset


def _in_branches(queryset, branches):
    """`in_branches`: how many units of each product the given branches hold."""
    held = (
        BranchStock.objects.filter(product_id=OuterRef('pk'), branch__in=branches)
        .order_by().values('product_id').annotate(total=Sum('quantity')).values('total')
    )
    return queryset.annotate(
        in_branches=Coalesce(Subquery(held, output_field=IntegerField()), Value(0, output_field=IntegerField())),
    )


# =============================================================================
# Products — the catalogue
# =============================================================================

def products_workbook(company, filters: ExportFilters, mode: str) -> tuple[bytes, int]:
    """
    The catalogue. `(bytes, rows)`.

    `reimport` is the product template — its sheet, its headers — plus
    «Estado». The image columns are left EMPTY: an empty cell changes nothing
    on import, and a picture travels as a file, not as a cell. Active and
    inactive products, unless `is_active` narrows it.

    Existence (`stock`) is the company's total here: the catalogue has no branch.
    """
    queryset = _products(company, filters).select_related('category')
    if filters.stock == STOCK_IN:
        queryset = queryset.filter(inventory__gt=0)
    elif filters.stock == STOCK_OUT:
        queryset = queryset.filter(inventory__lte=0)
    report = mode == MODE_REPORT
    if report:
        queryset = product_publication.with_gallery_flag(queryset)
    queryset = queryset.order_by('name', 'pk')
    _check_rows(queryset.count())

    codes = import_exports.product_codes(company)
    labels = product_publication.PROBLEM_LABELS
    require_image = product_publication.requires_image(company) if report else False
    rows = []
    for product in queryset.iterator(chunk_size=2000):
        slot = codes.get(product.pk, {})
        row = [
            slot.get('external', ''),
            slot.get('internal', ''),
            product.name,
            product.description,
            product.price,
            product.category.name if product.category_id else '',
            '',
            '',
            DESTINATION_LABELS[DESTINATION_ONLINE if product.is_published_online else DESTINATION_INTERNAL],
        ]
        if report:
            problems = product_publication.publication_problems(product, require_image=require_image)
            row += [
                'Sí' if product.is_active else 'No',
                TYPE_LABELS[product.is_serialized],
                product.inventory,
                'Sí' if product.is_published_online else 'No',
                ' '.join(labels[code] for code in problems),
            ]
        else:
            row += [STATE_LABELS[product.is_active]]
        rows.append(row)

    if report:
        payload = _workbook_bytes(
            import_exports.REPORT_SHEETS['products'], PRODUCT_REPORT_HEADERS, rows, text_columns=(0, 1),
            widths=[22, 19, 42, 47, 18, 28, 20, 20, 28, 10, 16, 14, 12, 60],
        )
    else:
        payload = _workbook_bytes(
            PRODUCT_SHEET, PRODUCT_RELOAD_HEADERS, rows, text_columns=(0, 1),
            widths=[22, 19, 42, 47, 18, 28, 31, 46, 28, 12],
        )
    return payload, len(rows)


# =============================================================================
# Stock — quantities by branch
# =============================================================================

def stock_workbook(company, filters: ExportFilters, mode: str) -> tuple[bytes, int]:
    """
    Quantities. `(bytes, rows)`.

    `reimport`: the inventory sheet the stock importer reads
    (`import_exports.inventory_export_table`), WITHOUT the products counted by
    serial number — the importer refuses a quantity for those, so with them the
    file would not go back in.

    `report`: one row per product AND branch, the serialized ones included
    with the count of their devices.

    Existence (`stock`) is counted in the branches being downloaded.
    """
    branches = sorted(filters.branches, key=lambda branch: (branch.name, branch.pk))
    queryset = _products(company, filters)
    report = mode == MODE_REPORT
    if not report:
        queryset = queryset.filter(is_serialized=False)
        if filters.stock:
            queryset = _in_branches(queryset, branches)
            queryset = queryset.filter(in_branches__gt=0) if filters.stock == STOCK_IN else queryset.filter(in_branches__lte=0)
        _check_rows(queryset.count())
        headers, rows = import_exports.inventory_export_table(
            company=company, branches=branches, include_quantities=True, products=queryset,
        )
        payload = _workbook_bytes(
            import_exports.INVENTORY_EXPORT_SHEET, headers, rows, text_columns=(0, 1),
            widths=[16, 18, 46] + [18] * len(branches),
        )
        return payload, len(rows)

    queryset = queryset.select_related('category').order_by('name', 'pk')
    _check_rows(queryset.count() * len(branches))
    codes = import_exports.product_codes(company)
    quantities = {
        (branch_id, product_id): quantity
        for branch_id, product_id, quantity in BranchStock.objects.filter(branch__in=branches)
        .values_list('branch_id', 'product_id', 'quantity')
    }
    rows = []
    for product in queryset.iterator(chunk_size=2000):
        slot = codes.get(product.pk, {})
        for branch in branches:
            quantity = quantities.get((branch.pk, product.pk), 0)
            if (filters.stock == STOCK_IN and quantity <= 0) or (filters.stock == STOCK_OUT and quantity > 0):
                continue
            rows.append([
                slot.get('internal', ''), slot.get('external', ''), product.name,
                product.category.name if product.category_id else '',
                TYPE_LABELS[product.is_serialized], branch.name, quantity,
            ])
    payload = _workbook_bytes(
        import_exports.REPORT_SHEETS['stock'], STOCK_REPORT_HEADERS, rows, text_columns=(0, 1),
        widths=[16, 18, 46, 28, 16, 24, 12],
    )
    return payload, len(rows)


# =============================================================================
# Units — one row per device
# =============================================================================

def units_workbook(company, filters: ExportFilters, mode: str) -> tuple[bytes, int]:
    """
    Devices, one row each. `(bytes, rows)`.

    `reimport` is the device template — its sheet, its headers — with the
    devices that are ON THE SHELF and nothing else: a sold or written-off
    device reloaded would be for sale again. «Motivo / referencia» is left for
    whoever loads the file: it is the reason of THAT reception.

    `report` lists every device, with its state, when it came in, the order it
    left in (its number, nothing about the buyer) and its notes.

    «Costo» is written only when `filters.include_cost`.
    """
    report = mode == MODE_REPORT
    queryset = StockUnit.objects.filter(
        company=company, branch__in=filters.branches, product__in=_products(company, filters),
    )
    if filters.condition:
        queryset = queryset.filter(condition=filters.condition)
    if not report or filters.stock == STOCK_IN:
        queryset = queryset.filter(status__in=ON_THE_SHELF)
    if filters.stock == STOCK_OUT:
        queryset = queryset.exclude(status__in=ON_THE_SHELF)
    queryset = queryset.select_related('product', 'branch').order_by(
        'product__name', 'product_id', 'branch__name', 'serial_number', 'pk',
    )
    _check_rows(queryset.count())

    codes = import_exports.product_codes(company)
    conditions = dict(StockUnit.Condition.choices)
    statuses = dict(StockUnit.Status.choices)
    rows = []
    for unit in queryset.iterator(chunk_size=2000):
        slot = codes.get(unit.product_id, {})
        row = [
            slot.get('internal') or slot.get('external', ''),
            unit.product.name,
            unit.branch.name,
            unit.serial_number,
            unit.imei or '',
            unit.imei2 or '',
            conditions.get(unit.condition, unit.condition),
            unit.cost if filters.include_cost else None,
        ]
        if report:
            row += [
                statuses.get(unit.status, unit.status),
                timezone.localtime(unit.received_at).strftime('%Y-%m-%d %H:%M'),
                unit.order_id,
                unit.notes,
            ]
        else:
            row.append('')
        rows.append(row)

    if report:
        payload = _workbook_bytes(
            import_exports.REPORT_SHEETS['units'], UNIT_REPORT_HEADERS, rows, text_columns=(0, 3, 4, 5),
            widths=[20, 43, 25, 27, 22, 22, 23, 18, 16, 20, 12, 48],
        )
    else:
        payload = _workbook_bytes(
            unit_import_services.SHEET, unit_import_services.HEADERS, rows, text_columns=(0, 3, 4, 5),
            widths=[20, 43, 25, 27, 22, 22, 23, 18, 48],
        )
    return payload, len(rows)


# =============================================================================
# Full — the three files to reload, in a ZIP
# =============================================================================

def readme_text() -> str:
    """
    The note inside the ZIP. It is the platform's: it names no shop.

    The limits it quotes are read from the importers, so the day one changes
    the note does not go on promising the old number.
    """
    from . import xlsx_reader

    return '\n'.join([
        'INVENTARIO COMPLETO',
        '===================',
        '',
        'Este ZIP trae tres archivos de Excel, cada uno con una sola hoja. Son archivos',
        '«para volver a cargar»: tienen exactamente las columnas que piden las cargas',
        'masivas del panel. No son reportes.',
        '',
        f'1. {ZIP_PRODUCTS}',
        '   El catálogo: una fila por producto (código de barras, código, nombre,',
        '   descripción, precio, categoría, destino y estado). Las columnas de imágenes',
        '   van vacías: las imágenes no viajan en este archivo.',
        '   Se carga en Productos > Carga de productos.',
        '',
        f'2. {ZIP_STOCK}',
        '   Las cantidades por sucursal de los productos que se cuentan por cantidad.',
        '   Los productos con número de serie NO están aquí: su stock son sus equipos.',
        '   Se carga en Inventario > Carga de stock.',
        '',
        f'3. {ZIP_UNITS}',
        '   Los equipos con número de serie que están en tienda: una fila por unidad',
        '   física, con su serie, IMEI, condición y sucursal. Los vendidos y los dados',
        '   de baja no están. La columna «Motivo / referencia» va vacía: se indica al cargar.',
        '   Se carga en Inventario > Carga de equipos.',
        '',
        'Incluye productos activos e inactivos (salvo que se haya filtrado), y el stock y los',
        'equipos de las sucursales activas a las que tiene acceso quien descargó.',
        'El «Stock total» del reporte de productos es otra cifra: suma toda la empresa,',
        'también lo que quede en una sucursal desactivada.',
        '',
        'La columna «Estado» dice si cada producto está «Activo» o «Inactivo». Una carga',
        'no activa ni desactiva un producto que ya existe: esa columna sólo decide cómo nace',
        'uno nuevo. Un producto «Inactivo» que se carga en otra tienda se crea inactivo y como',
        '«Solo stock interno».',
        '',
        'Para cargarlos en una tienda vacía, en este orden',
        '-------------------------------------------------',
        f'  1. {ZIP_PRODUCTS}. Si usa categorías, créelas antes o active «crear las que falten».',
        '  2. Marque «con número de serie» los productos que lo llevan (Inventario > Equipos).',
        f'  3. {ZIP_STOCK}, indicando a qué sucursal corresponde cada columna ALMACEN.',
        f'  4. {ZIP_UNITS}, con las sucursales ya creadas con el mismo nombre.',
        '',
        'Si los carga en la misma tienda',
        '-------------------------------',
        'Cargar estos archivos en la MISMA tienda no cambia nada: los productos ya existen',
        'y quedan igual, las cantidades coinciden y no generan movimientos, y cada equipo',
        'se rechaza porque su número de serie ya está registrado. No se duplica stock.',
        '',
        'Eso vale mientras nada haya cambiado. Estos archivos dicen cómo estaba la tienda al',
        'descargarlos, y una carga hace lo que dice su archivo:',
        f'  - {ZIP_STOCK}: si el stock se movió desde la descarga (una venta, un ingreso), la',
        '    carga vuelve a dejar cada cantidad como dice el archivo, con su movimiento de',
        '    Kardex. La vista previa lo muestra antes de aplicar.',
        f'  - {ZIP_PRODUCTS}: un producto que se ocultó de la web después de la descarga vuelve',
        '    a publicarse si su fila dice «Publicar en e-commerce». La vista previa cuenta',
        '    cuántos cambian de destino.',
        '',
        'Límites y avisos',
        '----------------',
        f'  - Cada carga admite hasta {xlsx_reader.MAX_DATA_ROWS} filas, y la de equipos hasta',
        f'    {unit_import_services.MAX_UNITS} por archivo. Si un archivo trae más, divídalo.',
        '  - Un producto se reconoce por su código o su código de barras. Sin ninguno, por su',
        '    nombre; si dos productos sin código se llaman igual, la carga pide asignarles uno.',
        '  - Si un producto tiene varios códigos, viaja uno de cada tipo.',
        '  - Un texto que empieza por = + - o @ lleva un apóstrofo delante para que Excel no lo',
        '    ejecute como fórmula. Al cargar el archivo se lee sin el apóstrofo.',
        '  - El catálogo no tiene campo de marca: no hay columna de marca.',
        '  - La columna «Costo» sólo trae datos si quien descargó puede ajustar inventario.',
        '',
    ])


def full_archive(company, filters: ExportFilters) -> tuple[bytes, dict]:
    """
    The three files to reload and the note, zipped in memory. `(bytes, row counts)`.

    Active and inactive products, unless `is_active` narrows it: the catalogue
    file says which is which («Estado») and a load never switches one on.
    """
    products, product_rows = products_workbook(company, filters, MODE_REIMPORT)
    stock, stock_rows = stock_workbook(company, filters, MODE_REIMPORT)
    units, unit_rows = units_workbook(company, filters, MODE_REIMPORT)

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(ZIP_NOTE, readme_text())
        for name, payload in ((ZIP_PRODUCTS, products), (ZIP_STOCK, stock), (ZIP_UNITS, units)):
            archive.writestr(name, payload)
    # Measured once it is closed: that is the size that would travel.
    payload = buffer.getvalue()
    if len(payload) > MAX_ZIP_BYTES:
        raise TooLarge()
    return payload, {'products': product_rows, 'stock': stock_rows, 'units': unit_rows}


def build(kind: str, mode: str, company, filters: ExportFilters) -> tuple[bytes, dict]:
    """`(bytes, {'<what>': rows})` for one download."""
    if kind == 'full':
        return full_archive(company, filters)
    builder = {'products': products_workbook, 'stock': stock_workbook, 'units': units_workbook}[kind]
    payload, rows = builder(company, filters, mode)
    return payload, {kind: rows}
