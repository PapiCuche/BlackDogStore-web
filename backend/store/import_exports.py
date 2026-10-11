"""
Files the platform hands BACK — Phase C1.4.

Two of them, and the second is the one that matters operationally:

  · a blank product template, so a new tenant is not asked to guess the columns;
  · the current inventory, so counting stock is "download, walk the shelves,
    type, upload" instead of transcribing six hundred lines by hand.

THE COUNT SHEET IS OFFERED BLANK ON PURPOSE
-------------------------------------------
Downloading the inventory with the current quantities already filled in is
convenient and is also how a physical count stops being a count: whoever holds
the sheet reads "14", sees fourteen-ish, and writes nothing. A blank column
forces an actual look at the shelf. Both are offered because pre-filled is right
for correcting a few known lines, and blank is right for a real stocktake — but
the operator picks, deliberately.

WHAT THE EXPORT MUST NOT DO
---------------------------
It must not emit `ALMACEN 1 - 11416`. That header belongs to the system that
produced the owner's file, and the number in it is that system's warehouse id.
Writing it for every tenant would bake one customer's data into everybody's
downloads. The export writes the branch's real name; the import still accepts
the original file, because a mapping profile is what reconciles the two.
"""

from __future__ import annotations

import csv
import io

from .models import BranchStock, Product, ProductBarcode

PRODUCT_TEMPLATE_HEADERS = [
    'Código de barras',
    'Código',
    'Nombre',
    'Descripción',
    'Precio de venta',
    'Categoría',
    'Imagen principal',
    'Imágenes',
    'Destino',
]

#: IMPORT-SPLIT-01 — the template is ONE sheet: these headers and nothing under
#: them. What follows (the help row, the example row, the names of the sheets
#: that only explain) is what the PREVIOUS template carried. It is kept because
#: people still upload files made with it, and none of it is a product.
PRODUCT_TEMPLATE_HELP = [
    'Se admite un EAN, un UPC o el código interno de la tienda.',
    'Código interno imprimible. Se registra como código de barras interno.',
    'Obligatorio.',
    'Opcional. Si se deja vacío no se borra la descripción existente.',
    'Obligatorio para productos nuevos. Usa punto decimal: 149.90',
    'Debe existir, salvo que se active «crear las que falten».',
    'Opcional. Nombre del archivo que verá el catálogo: telefono-frontal.webp',
    'Opcional. Nombres de archivo separados por | : frontal.webp|trasera.webp',
    'Opcional. «Publicar en e-commerce» o «Solo stock interno». Vacío: lo nuevo se publica y lo existente no cambia.',
]

#: How many rows below the example get the drop-downs and the text format.
_TEMPLATE_ROWS = 500

#: What the help row says under «Nombre». A file that still carries the help
#: row is the template being used as intended; that row is not a product.
_HELP_UNDER_NAME = 'Obligatorio.'

#: The example row of the first sheet. It shows the SHAPE of a filled row —
#: image names included — and it is never imported: see `is_template_example_row`.
PRODUCT_TEMPLATE_EXAMPLE = [
    '195949806575',
    'IP16PM-256-W',
    'iPhone 16 Pro Max 256GB White',
    'Pantalla de 6,9 pulgadas, 256 GB.',
    4999.00,
    'Teléfonos',
    'iphone16-front.webp',
    'iphone16-front.webp|iphone16-back.webp|iphone16-side.webp',
    'Publicar en e-commerce',
]

#: Sheets of the template that explain and are never read as products.
#: «Categorías» lists the company's categories for the drop-down: a list of
#: names, not of products.
HELP_SHEETS = frozenset({'instrucciones', 'ejemplo', 'ejemplos', 'ayuda', 'categorias'})


def is_template_help_row(fields: dict) -> bool:
    """
    The row under the headers that says what each column is.

    «Obligatorio.» as this template writes it, and «OBLIGATORIO» as a copy
    edited by hand does: a help row is recognised by what it says under «Nombre»,
    not by its punctuation. No product is called that.
    """
    from .import_formats import normalize_header

    return normalize_header(fields.get('name') or '') == 'obligatorio'


def is_template_example_row(fields: dict) -> bool:
    """
    The template's own example row, untouched.

    Only the EXACT row: same name and same code. A shop that really sells that
    phone and wrote its own code is importing a product, not an example.
    """
    return (
        (fields.get('name') or '').strip() == PRODUCT_TEMPLATE_EXAMPLE[2]
        and (fields.get('code') or '').strip() == PRODUCT_TEMPLATE_EXAMPLE[1]
    )


def is_help_sheet(name) -> bool:
    """«Instrucciones», «Ejemplo»… — a sheet that explains, whatever its case or accents."""
    from .import_formats import normalize_header

    return normalize_header(str(name or '')) in HELP_SHEETS


def image_rules() -> dict:
    """
    How images travel with a product file, FROM THE LIMITS THE SERVER APPLIES.

    One source for the instructions sheet and for the screen: a number written
    by hand in either place is a number that stops being true the day a setting
    changes.
    """
    from . import import_media, product_media, storefront_media

    megabyte = 1024 * 1024
    return {
        'separator': import_media.SEPARATOR,
        'formats': ['PNG', 'JPG', 'JPEG', 'WEBP'],
        'max_per_product': product_media.max_per_product(),
        'max_file_mb': storefront_media.max_upload_bytes() // megabyte,
        'max_files': import_media.max_files(),
        'max_total_mb': import_media.max_total_bytes() // megabyte,
    }


def _workbook():
    import openpyxl
    return openpyxl.Workbook()


def _autosize(sheet, widths):
    from openpyxl.utils import get_column_letter
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width


def _header_row(sheet, titles):
    from openpyxl.styles import Alignment, Font, PatternFill

    fill = PatternFill('solid', fgColor='1F2937')
    for column, title in enumerate(titles, start=1):
        cell = sheet.cell(row=1, column=column, value=title)
        cell.font = Font(bold=True, color='FFFFFF')
        cell.fill = fill
        cell.alignment = Alignment(vertical='center')


#: A cell list («data validation») is ONE text, split by commas, of at most
#: this many characters. A catalogue whose names do not fit gets no list.
_CELL_LIST_LIMIT = 255
#: Rows under the headers that carry the lists.
TEMPLATE_LAST_ROW = _TEMPLATE_ROWS + 1


def cell_list(names) -> str | None:
    """
    `names` as the formula of a cell list, or None when a cell cannot hold them.

    ONE SHEET means the choices live inside the validation itself, not in a
    second sheet to point at. That is ONE text, wrapped in quotes and split by
    commas, and PICKING an item is typing it. So a list is left out when a name:

      · carries a comma or a quote — it would split, or end the text;
      · starts like a formula (`=`, `+`, `-`, `@`) — picked, Excel would run it.
        These names are a tenant's own, and a template must not hand its users a
        cell that computes;
      · holds a control character or a line break — the workbook cannot be
        written with it at all;

    or when all of them together pass 255 characters as Excel counts them
    (UTF-16 units). What is not offered is still typed, and still checked when
    the file is uploaded.
    """
    names = [str(name) for name in names if str(name).strip()]
    if not names:
        return None
    for name in names:
        if ',' in name or '"' in name or name.lstrip()[:1] in ('=', '+', '-', '@'):
            return None
        if any(ord(char) < 32 or ord(char) == 127 for char in name):
            return None
    joined = ','.join(names)
    if len(joined.encode('utf-16-le')) // 2 > _CELL_LIST_LIMIT:
        return None
    return f'"{joined}"'


def add_cell_list(sheet, column: str, formula) -> None:
    """
    A drop-down for `column`, rows 2 to the last template row. Nothing when there is no `formula`.

    It OFFERS and never blocks, as in the owner's files. Excel compares what is
    typed letter by letter — «nuevo» is not «Nuevo» to it — while the upload
    reads both; and a category that does not exist yet is a legitimate thing to
    type. What is wrong is said per row when the file is previewed.
    """
    from openpyxl.worksheet.datavalidation import DataValidation

    if not formula:
        return
    rule = DataValidation(type='list', formula1=formula, allow_blank=True, showErrorMessage=False)
    sheet.add_data_validation(rule)
    rule.add(f'{column}2:{column}{TEMPLATE_LAST_ROW}')


def number_columns(sheet, letters) -> None:
    """Amounts with two decimals, for the whole column — as in the owner's files."""
    for letter in letters:
        sheet.column_dimensions[letter].number_format = '0.00'


def header_notes(sheet, notes: dict) -> None:
    """What goes under each header, as a note ON the header: it explains and is never a row."""
    from openpyxl.comments import Comment

    for cell in sheet[1]:
        text = notes.get(cell.value)
        if text:
            note = Comment(text, 'Sistema')
            note.width, note.height = 320, 30 + 16 * (1 + len(text) // 46)
            cell.comment = note


def text_columns(sheet, letters) -> None:
    """
    Identifiers as TEXT, for the whole column. A code that is all digits loses
    its zeros in a general cell, and a 15-digit IMEI turns into 4.90154E+14.
    Set on the column, not cell by cell: five hundred formatted empty cells are
    five hundred rows for the importer to read.
    """
    for letter in letters:
        sheet.column_dimensions[letter].number_format = '@'


def _company_category_names(company) -> list[str]:
    """The names a row may write under «Categoría»: this company's, active, in order. Never a fixed list."""
    from .models import Category

    if company is None:
        return []
    return list(
        Category.objects.filter(company=company, is_active=True).order_by('name').values_list('name', flat=True)
    )


def _destination_labels() -> tuple[str, str]:
    from .import_formats import DESTINATION_INTERNAL, DESTINATION_LABELS, DESTINATION_ONLINE

    return DESTINATION_LABELS[DESTINATION_ONLINE], DESTINATION_LABELS[DESTINATION_INTERNAL]


def _product_header_notes() -> dict:
    rules = image_rules()
    online, internal = _destination_labels()
    formats = ', '.join(rules['formats'])
    return {
        'Código de barras': 'Opcional. Un EAN, un UPC o el código interno de la tienda. Como texto.',
        'Código': (
            'Código interno imprimible. Una fila es un producto del catálogo. '
            'Esta carga no mueve stock: las cantidades y los equipos con serie se cargan en Inventario.'
        ),
        'Nombre': 'Obligatorio.',
        'Descripción': 'Opcional. Si se deja vacía no se borra la descripción existente.',
        'Precio de venta': 'Obligatorio para productos nuevos. Usa punto decimal: 149.90',
        'Categoría': (
            'Una categoría de tu empresa, con su nombre exacto. Debe existir, salvo que al cargar '
            'se active «crear las que falten» o se elija una para las filas que no traen.'
        ),
        'Imagen principal': (
            'Opcional. El NOMBRE del archivo, por ejemplo telefono-frontal.webp. La imagen no se pega '
            'en la celda: se adjunta al cargar. También se puede añadir o cambiar después, en la ficha del producto.'
        ),
        'Imágenes': (
            f'Opcional. Nombres de archivo separados por {rules["separator"]} : frontal.webp|trasera.webp. '
            f'Los archivos se adjuntan al cargar, sueltos o en un ZIP. Formatos: {formats}. '
            f'Hasta {rules["max_per_product"]} imágenes por producto, {rules["max_file_mb"]} MB cada una '
            f'y {rules["max_files"]} archivos por carga.'
        ),
        'Destino': (
            f'Opcional. «{online}» o «{internal}». '
            'Solo stock interno: sigue en inventario y se vende en caja y en servicio técnico, pero no aparece '
            'en la tienda en línea. Vacío: lo nuevo se publica y lo existente no cambia. No es lo mismo que «activo».'
        ),
    }


def product_template_bytes(company=None) -> bytes:
    """
    The product template: ONE sheet, «Productos», its headers and nothing under them.

    IMPORT-SPLIT-01. There used to be a help row, an example row and three more
    sheets. They are gone from what we hand out: an empty file is exactly what
    it looks like, and what explained lives where nobody can import it — on the
    screen, and in a note on each header.

    `company` is whose template it is: the «Categoría» list is THAT company's
    active categories, read when it is downloaded (never a fixed list), so a
    new category can still be typed for «crear las que falten». «Destino» lists
    its two answers. Both lists offer and neither blocks, as in the owner's file.

    This creates and edits the CATALOGUE. It carries no quantity and no serial:
    stock has its own uploads.

    The importer still reads the previous template and the owner's 18-column
    sheet. This is what we ASK for; those are what we ACCEPT.
    """
    workbook = _workbook()
    sheet = workbook.active
    sheet.title = 'Productos'

    _header_row(sheet, PRODUCT_TEMPLATE_HEADERS)
    header_notes(sheet, _product_header_notes())
    sheet.freeze_panes = 'A2'
    _autosize(sheet, [22, 19, 42, 47, 18, 28, 31, 46, 28])
    text_columns(sheet, ('A', 'B'))

    number_columns(sheet, ('E',))

    add_cell_list(sheet, 'F', cell_list(_company_category_names(company)))
    add_cell_list(sheet, 'I', cell_list(_destination_labels()))

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def inventory_export_bytes(*, company, branches, include_quantities: bool) -> bytes:
    """
    The catalogue with one column per branch, for counting and re-uploading.

    The header row is `CODIGO · CODIGO EAN · NOMBRE`, matching the shape the
    importer already recognises, plus one `<branch name>` column per branch. The
    result re-imports through the same detector as any other file.
    """
    from openpyxl.styles import Font, PatternFill

    branches = list(branches)
    workbook = _workbook()
    sheet = workbook.active
    sheet.title = 'Inventario'

    headers = ['CODIGO', 'CODIGO EAN', 'NOMBRE'] + [f'ALMACEN {b.name}' for b in branches]
    header_fill = PatternFill('solid', fgColor='1F2937')
    for column, title in enumerate(headers, start=1):
        cell = sheet.cell(row=1, column=column, value=title)
        cell.font = Font(bold=True, color='FFFFFF')
        cell.fill = header_fill

    products = list(
        Product.objects.filter(company=company, is_active=True).order_by('name')
    )
    codes: dict[int, dict[str, str]] = {}
    for product_id, code, symbology in ProductBarcode.objects.filter(
        company=company, is_active=True,
    ).values_list('product_id', 'code', 'symbology'):
        slot = codes.setdefault(product_id, {})
        if symbology == ProductBarcode.INTERNAL:
            slot.setdefault('internal', code)
        else:
            slot.setdefault('external', code)

    quantities = {
        (b, p): q
        for b, p, q in BranchStock.objects
        .filter(branch__in=branches)
        .values_list('branch_id', 'product_id', 'quantity')
    }

    for index, product in enumerate(products, start=2):
        slot = codes.get(product.pk, {})
        # Written as TEXT, always. A code stored as a number is how the owner's
        # own file lost the ability to keep a leading zero, and re-exporting the
        # same trap would repeat the damage on every round trip.
        sheet.cell(row=index, column=1, value=slot.get('internal', '')).number_format = '@'
        sheet.cell(row=index, column=2, value=slot.get('external', '')).number_format = '@'
        sheet.cell(row=index, column=3, value=product.name)
        for offset, branch in enumerate(branches):
            cell = sheet.cell(row=index, column=4 + offset)
            if include_quantities:
                cell.value = quantities.get((branch.pk, product.pk), 0)
            # else: left EMPTY, which the importer reads as "do not change" —
            # so a sheet that comes back with only twelve lines filled in moves
            # exactly twelve stocks.

    sheet.freeze_panes = 'A2'
    _autosize(sheet, [16, 18, 46] + [18] * len(branches))

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def error_report_csv(job) -> str:
    """
    The rows that failed, as CSV (§63).

    CSV rather than XLSX because this file exists to be read next to the
    original: it is opened, scanned, and thrown away. UTF-8 with a BOM, because
    without it Excel on Windows renders every accented product name as mojibake
    and the report becomes unreadable in the one program that will open it.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(['Hoja', 'Fila', 'Identificador', 'Producto', 'Error', 'Advertencia'])
    for row in job.rows.exclude(errors=[]).order_by('row_number', 'pk'):
        data = row.normalized_data or {}
        writer.writerow([
            row.sheet_name, row.row_number, row.match_key,
            data.get('name', ''),
            ' · '.join(row.errors or []),
            ' · '.join(row.warnings or []),
        ])
    return '﻿' + buffer.getvalue()
