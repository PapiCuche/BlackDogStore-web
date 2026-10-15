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
it never activates or deactivates one that exists. It also adds «Identificador»
(`<company id>-<product id>`), which is what lets a product WITHOUT A CODE be
recognised after it was renamed: without it the name was all it had, and a
reload created it a second time. Loaded into the SAME shop it changes nothing: a product matches
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
Text that a spreadsheet would run as a formula is written as a STRING cell
marked as text (`xlsx_reader.mark_as_text`) — the very text, with no apostrophe
in front — and the importers read it back as it is. Codes, serials and IMEIs
are text. Nothing a user typed reaches a file name. There is
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
    DESTINATION_INTERNAL, DESTINATION_LABELS, DESTINATION_ONLINE, IDENTIFIER_HEADER, STATE_HEADER, STATE_LABELS,
    product_identifier,
)
from .models import BranchStock, Product, StockUnit
from .xlsx_reader import looks_like_formula, mark_as_text

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
PRODUCT_RELOAD_HEADERS = import_exports.PRODUCT_TEMPLATE_HEADERS + [STATE_HEADER, IDENTIFIER_HEADER]
PRODUCT_REPORT_HEADERS = import_exports.PRODUCT_TEMPLATE_HEADERS + [
    'Activo', 'Tipo', 'Stock total', 'Publicado', 'Problemas para publicar',
]
STOCK_REPORT_HEADERS = ['Código', 'Código de barras', 'Producto', 'Categoría', 'Tipo', 'Sucursal', 'Cantidad',
                        'Costo promedio']
UNIT_REASON_HEADER = 'Motivo / referencia'
#: The report says what happened to each device instead of asking why it came in.
UNIT_REPORT_HEADERS = [header for header in unit_import_services.HEADERS if header != UNIT_REASON_HEADER] + [
    'Estado', 'Fecha de ingreso', 'Pedido', 'Notas',
]

#: ROWS ARE WRITTEN BY HEADER, NEVER BY POSITION. Each row is a {header: value}
#: projected onto its header list (`_project`), so a column the template gains
#: can never shift «Estado» or «Identificador» under another heading — which is
#: what happened when DC2 grew the templates and a reload lost its identities.
#: The template columns a download leaves EMPTY on purpose; every other one is
#: filled (`test_inventory_exports.TemplateDriftTest` holds both lists to it).
PRODUCT_LEFT_EMPTY = ('Imagen principal', 'Imágenes')
UNIT_LEFT_EMPTY = (UNIT_REASON_HEADER,)
#: Identifiers: text cells whatever they look like.
PRODUCT_TEXT_HEADERS = ('Código de barras', 'Código', IDENTIFIER_HEADER)
UNIT_TEXT_HEADERS = ('Código', 'Número de serie', 'IMEI', 'IMEI 2')

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
    #: Whether any cost column carries data: only for a caller holding `costs.view` (DC1).
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
    """The text of a cell, without what a workbook cannot hold. None when empty."""
    from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE

    return ILLEGAL_CHARACTERS_RE.sub('', str(value or '')) or None


def _workbook_bytes(title: str, headers, rows, *, text_columns=(), widths=()) -> bytes:
    """
    ONE sheet, written in write-only mode: rows are streamed to the file as
    they come instead of being held as cell objects.

    `text_columns` are the indexes of identifiers (codes, serials, IMEIs): their
    cells are text whatever they look like, so no zero is lost and no IMEI
    becomes 4.90154E+14. Every other string is text too; numbers stay numbers.

    A string that STARTS LIKE A FORMULA is written as a string cell marked as
    text — never as a formula, and with no apostrophe in its value.
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
        if looks_like_formula(cell.value):
            mark_as_text(cell)
        cell.font, cell.fill = bold, fill
        head.append(cell)
    sheet.append(head)

    text_columns = set(text_columns)
    for row in rows:
        cells = []
        for index, value in enumerate(row):
            if isinstance(value, str):
                value = _text(value)
            risky = looks_like_formula(value)
            if value is not None and (risky or index in text_columns):
                cell = WriteOnlyCell(sheet, value=value)
                if risky:
                    mark_as_text(cell)
                if index in text_columns:
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


def _project(headers, values: dict) -> list:
    """A row in the order of `headers`. A header the row does not fill is empty."""
    return [values.get(header) for header in headers]


def _columns_of(headers, names) -> tuple:
    return tuple(index for index, header in enumerate(headers) if header in names)


# =============================================================================
# DC2 — model, options, line, grade: written so that they READ BACK the same
# =============================================================================

def _folded(text) -> str:
    """How `iexact` compares, after the importer's own whitespace cleaning."""
    return ' '.join(str(text or '').split()).casefold()


class _VariantCells:
    """
    «Modelo», «Capacidad», «Color» and «Línea» of a product, as the importers
    will read them back (`device_catalog.resolve_variant_spec`).

    The four are written ONLY when reading them back names exactly the variant
    the product is. Otherwise they stay empty — and an empty «Modelo» leaves a
    product's variant as it is — because a near miss would be worse than
    nothing: a model name two models share (`iexact`), an option label two
    attributes share, or a product with options the two columns cannot hold
    would re-link it, or fail its row, on reload.
    """

    def __init__(self, company):
        from collections import Counter, defaultdict

        from .models import ProductModel, ProductVariantOption, VariantAttribute, VariantOption

        self.model_names = Counter(_folded(name) for name in
                                   ProductModel.objects.filter(company=company).values_list('name', flat=True))
        self.option_labels = Counter(_folded(label) for label in
                                     VariantOption.objects.filter(company=company).values_list('label', flat=True))
        self.options = defaultdict(list)
        for product_id, label, kind in ProductVariantOption.objects.filter(company=company).values_list(
                'product_id', 'option__label', 'attribute__kind'):
            self.options[product_id].append((label, kind))
        self.colour_kind = VariantAttribute.KIND_COLOR
        self.lines = dict(Product.CONDITION_LINE_CHOICES)

    def _reads_back(self, text) -> bool:
        return ' '.join(str(text).split()) == text

    def __call__(self, product) -> dict:
        model = product.product_model
        if model is None:
            return {}
        name = model.name
        if not self._reads_back(name) or self.model_names[_folded(name)] != 1:
            return {}
        capacity, colour = [], []
        for label, kind in self.options.get(product.pk, ()):
            if not self._reads_back(label) or self.option_labels[_folded(label)] != 1:
                return {}
            (colour if kind == self.colour_kind else capacity).append(label)
        if len(capacity) > 1 or len(colour) > 1:
            return {}
        return {
            'Modelo': name,
            'Capacidad': capacity[0] if capacity else None,
            'Color': colour[0] if colour else None,
            'Línea': self.lines.get(product.condition_line),
        }


class _GradeCells:
    """
    «Estado estético» as `stock_unit_services.clean_grade` will read it back:
    the grade's label, or its code when the label would name another grade.
    An inactive grade is refused on load, so the file to reload leaves it empty.
    """

    def __init__(self, company):
        from .models import CosmeticGrade

        self.grades = list(CosmeticGrade.objects.filter(company=company))
        self.active = [grade for grade in self.grades if grade.is_active]

    def _resolves(self, text):
        """`clean_grade`, in memory."""
        text = str(text).strip()
        if text.isdigit():
            found = next((grade for grade in self.active if grade.pk == int(text)), None)
            if found is not None:
                return found
        folded = text.casefold()
        return (next((grade for grade in self.active if grade.code.casefold() == folded), None)
                or next((grade for grade in self.active if grade.label.casefold() == folded), None))

    def __call__(self, grade_id, *, report: bool):
        grade = next((grade for grade in self.grades if grade.pk == grade_id), None) if grade_id else None
        if grade is None:
            return None
        if report:
            return grade.label
        for text in (grade.label, grade.code):
            if text and self._resolves(text) == grade:
                return text
        return None


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
    «Estado» and «Identificador», each value written under ITS header (DC2
    grew the template; a row built by position put the identifier under
    «Capacidad»). The image columns are left EMPTY: an empty cell changes nothing
    on import, and a picture travels as a file, not as a cell. Active and
    inactive products, unless `is_active` narrows it.

    Existence (`stock`) is the company's total here: the catalogue has no branch.
    """
    queryset = _products(company, filters).select_related('category', 'product_model')
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
    variant_cells = _VariantCells(company)
    headers = PRODUCT_REPORT_HEADERS if report else PRODUCT_RELOAD_HEADERS
    rows = []
    for product in queryset.iterator(chunk_size=2000):
        slot = codes.get(product.pk, {})
        # «Imagen principal» and «Imágenes» stay empty (PRODUCT_LEFT_EMPTY).
        values = {
            'Código de barras': slot.get('external', ''),
            'Código': slot.get('internal', ''),
            'Nombre': product.name,
            'Descripción': product.description,
            'Precio de venta': product.price,
            'Categoría': product.category.name if product.category_id else '',
            'Destino': DESTINATION_LABELS[
                DESTINATION_ONLINE if product.is_published_online else DESTINATION_INTERNAL],
            # DC1 — only for `costs.view`; empty otherwise, and an empty cell
            # changes nothing when the file comes back.
            'Costo de referencia': product.reference_cost if filters.include_cost else None,
            # DC2 — the variant, only when it reads back as itself.
            **variant_cells(product),
        }
        if report:
            problems = product_publication.publication_problems(product, require_image=require_image)
            values.update({
                'Activo': 'Sí' if product.is_active else 'No',
                'Tipo': TYPE_LABELS[product.is_serialized],
                'Stock total': product.inventory,
                'Publicado': 'Sí' if product.is_published_online else 'No',
                'Problemas para publicar': ' '.join(labels[code] for code in problems),
            })
        else:
            values.update({
                STATE_HEADER: STATE_LABELS[product.is_active],
                IDENTIFIER_HEADER: product_identifier(company.pk, product.pk),
            })
        rows.append(_project(headers, values))

    template_widths = [22, 19, 42, 47, 18, 28, 31, 46, 28, 20, 30, 16, 16, 12]
    if report:
        payload = _workbook_bytes(
            import_exports.REPORT_SHEETS['products'], headers, rows,
            text_columns=_columns_of(headers, PRODUCT_TEXT_HEADERS),
            widths=template_widths + [10, 16, 14, 12, 60],
        )
    else:
        payload = _workbook_bytes(
            PRODUCT_SHEET, headers, rows, text_columns=_columns_of(headers, PRODUCT_TEXT_HEADERS),
            widths=template_widths + [12, 16],
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
    quantities, averages = {}, {}
    for branch_id, product_id, quantity, average in BranchStock.objects.filter(branch__in=branches) \
            .values_list('branch_id', 'product_id', 'quantity', 'average_cost'):
        quantities[(branch_id, product_id)] = quantity
        averages[(branch_id, product_id)] = average
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
                # DC1 — the branch's weighted average, `costs.view` only.
                averages.get((branch.pk, product.pk)) if filters.include_cost else None,
            ])
    payload = _workbook_bytes(
        import_exports.REPORT_SHEETS['stock'], STOCK_REPORT_HEADERS, rows, text_columns=(0, 1),
        widths=[16, 18, 46, 28, 16, 24, 12, 16],
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
    queryset = queryset.select_related('product', 'product__product_model', 'branch').order_by(
        'product__name', 'product_id', 'branch__name', 'serial_number', 'pk',
    )
    _check_rows(queryset.count())

    codes = import_exports.product_codes(company)
    conditions = dict(StockUnit.Condition.choices)
    statuses = dict(StockUnit.Status.choices)
    variant_cells, grade_cell = _VariantCells(company), _GradeCells(company)
    headers = UNIT_REPORT_HEADERS if report else unit_import_services.HEADERS
    rows = []
    for unit in queryset.iterator(chunk_size=2000):
        slot = codes.get(unit.product_id, {})
        used = unit.product.condition_line == Product.CONDITION_USED
        # «Motivo / referencia» stays empty (UNIT_LEFT_EMPTY): it is the reason
        # of the reception that loads the file.
        values = {
            'Código': slot.get('internal') or slot.get('external', ''),
            'Producto': unit.product.name,
            'Sucursal': unit.branch.name,
            'Número de serie': unit.serial_number,
            'IMEI': unit.imei or '',
            'IMEI 2': unit.imei2 or '',
            'Condición': conditions.get(unit.condition, unit.condition),
            'Costo': unit.cost if filters.include_cost else None,
            # DC2 — the variant, its grade and its battery.
            **variant_cells(unit.product),
            'Estado estético': grade_cell(unit.cosmetic_grade_id, report=report),
            'Batería %': unit.battery_health,
            # What THIS used device sells for. A price, not a cost: it is what
            # the web shows any buyer, so it is not behind `costs.view`. Only
            # for the used line: a new device sells at its variant's price,
            # and the device load would only warn that it ignores one.
            'Precio de venta': unit.sale_price if used or report else None,
        }
        if report:
            values.update({
                'Estado': statuses.get(unit.status, unit.status),
                'Fecha de ingreso': timezone.localtime(unit.received_at).strftime('%Y-%m-%d %H:%M'),
                'Pedido': unit.order_id,
                'Notas': unit.notes,
            })
        rows.append(_project(headers, values))

    widths = {'Código': 20, 'Producto': 43, 'Sucursal': 25, 'Número de serie': 27, 'IMEI': 22, 'IMEI 2': 22,
              'Condición': 23, 'Costo': 18, UNIT_REASON_HEADER: 48, 'Modelo': 30, 'Estado estético': 20,
              'Precio de venta': 18, 'Fecha de ingreso': 20, 'Notas': 48}
    payload = _workbook_bytes(
        import_exports.REPORT_SHEETS['units'] if report else unit_import_services.SHEET, headers, rows,
        text_columns=_columns_of(headers, UNIT_TEXT_HEADERS),
        widths=[widths.get(header, 16) for header in headers],
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
        '  - En la misma tienda un producto se reconoce por la columna «Identificador», aunque',
        '    le hayan cambiado el nombre. No la modifique. En otra tienda ese identificador no',
        '    vale: se ignora (con un aviso por fila) y el producto se reconoce por su código o',
        '    su código de barras. Sin ninguno, por su nombre; si dos productos sin código se',
        '    llaman igual, la carga pide asignarles uno.',
        '  - Si un producto tiene varios códigos, viaja uno de cada tipo.',
        '  - «Modelo», «Capacidad», «Color» y «Línea» dicen de qué variante es cada producto y',
        '    cada equipo. Van vacíos cuando al volver a leerlos no nombrarían exactamente esa',
        '    variante (un nombre de modelo repetido, una opción que comparten dos atributos); un',
        '    «Modelo» vacío no cambia la variante de un producto que ya existe.',
        '  - Un texto que empieza por = + - o @ (una descripción con guiones, un teléfono con',
        '    +) va en una celda de texto: se ve tal cual y Excel no lo ejecuta como fórmula.',
        '  - El catálogo no tiene campo de marca: no hay columna de marca.',
        '  - Las columnas de costo sólo traen datos si quien descargó puede ver costos (permiso «Ver costos y márgenes»).',
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
