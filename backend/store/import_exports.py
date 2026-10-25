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
    # DC1. Optional; read as `Product.reference_cost`. Last, so files made with
    # the previous template keep every other column where it was.
    'Costo de referencia',
    # DC2. Optional, after everything else for the same reason: which model the
    # product is a variant of, its options and its line.
    'Modelo',
    'Capacidad',
    'Color',
    'Línea',
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
    'Opcional. «Publicar en e-commerce» o «Solo stock interno». Vacío: lo nuevo queda interno y lo existente no cambia.',
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


#: INVENTORY-UNIFIED-01 — the sheets of the REPORTS (`store.inventory_exports`).
#: A report carries columns that are only read; uploading one would silently
#: drop them, so every importer refuses these sheets by name and points at the
#: file that IS made to be loaded.
REPORT_SHEETS = {
    'products': 'Reporte de productos',
    'stock': 'Reporte de stock',
    'units': 'Reporte de equipos',
}


def report_sheet_refusal(name) -> str:
    """Why `name` cannot be imported, when it is the sheet of a report. Empty otherwise."""
    from .import_formats import normalize_header

    if normalize_header(str(name or '')) not in {normalize_header(title) for title in REPORT_SHEETS.values()}:
        return ''
    return (
        f'«{name}» es un reporte: sirve para leer y no se importa. '
        'Descarga el archivo «para volver a cargar» y sube ese.'
    )


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
            'en la tienda en línea. Vacío: lo nuevo queda como «Solo stock interno» y lo existente no cambia. '
            'No es lo mismo que «activo».'
        ),
        'Costo de referencia': (
            'Opcional. Lo que esperas pagar por una unidad, sin IGV, con punto decimal: 85.50. Sólo se usa '
            'cuando una sucursal todavía no tiene costo promedio. Vacío: no cambia el que hay.'
        ),
        'Modelo': (
            'Opcional. El modelo del catálogo de equipos del que este producto es una variante, tal como se '
            'llama en Catálogo › Modelos. Vacío: el producto no cambia de modelo.'
        ),
        'Capacidad': 'Opcional, con «Modelo»: una opción que ya existe (por ejemplo «256 GB»).',
        'Color': 'Opcional, con «Modelo»: una opción de color que ya existe.',
        'Línea': 'Opcional, con «Modelo»: «Nuevo» o «Seminuevo». Vacío = Nuevo.',
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
    _autosize(sheet, [22, 19, 42, 47, 18, 28, 31, 46, 28, 20])
    text_columns(sheet, ('A', 'B'))

    number_columns(sheet, ('E', 'J'))

    add_cell_list(sheet, 'F', cell_list(_company_category_names(company)))
    add_cell_list(sheet, 'I', cell_list(_destination_labels()))

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


#: LABEL-MIGRATION-01 — the historical template. Its own columns («SKU antiguo»,
#: «… original», «Id externo») are what `import_formats` recognises it by.
HISTORY_TEMPLATE_HEADERS = [
    'Código de barras original',
    'Código interno original',
    'SKU antiguo',
    'Nombre',
    'Categoría',
    'Marca',
    'Modelo',
    'Variante',
    'Precio comercial',
    'Id externo',
    # MIGRATION-B — optional, last, so the columns above keep their letters.
    'Capacidad',
    'Color',
    'SIM',
    'Región',
    'Referencia de origen',
    'Costo histórico',
    'Cantidad anterior',
]

_HISTORY_HEADER_NOTES = {
    'Código de barras original': (
        'Opcional. El código del fabricante o el que la tienda ya tenía impreso, TAL CUAL. Como texto: '
        'una celda numérica pierde los ceros y se marca para revisión.'
    ),
    'Código interno original': 'Opcional. El código interno del sistema anterior, como texto. Nunca se trata como EAN.',
    'SKU antiguo': (
        'Opcional. Sólo se guarda como código escaneable si al cargar se activa «el SKU antiguo también se '
        'escanea»; si no, queda en el informe de conciliación.'
    ),
    'Nombre': 'Obligatorio. En un producto que ya existe y se reconoce por código, no se cambia: se marca para revisión.',
    'Categoría': 'Opcional. Una categoría de tu empresa, con su nombre exacto.',
    'Marca': 'Opcional. El catálogo no tiene marcas: se guarda sólo en el informe de conciliación.',
    'Modelo': 'Opcional. Sólo se usa si existe en Catálogo › Modelos; si no, queda en el informe.',
    'Variante': 'Opcional. Se guarda sólo en el informe de conciliación.',
    'Precio comercial': 'Obligatorio para productos nuevos. Punto decimal: 149.90',
    'Id externo': 'Opcional. El identificador del sistema anterior, para conciliar. No se guarda en el producto.',
    'Capacidad': 'Opcional, con «Modelo»: una opción que ya existe. Si no existe, queda en el informe.',
    'Color': 'Opcional, con «Modelo»: una opción que ya existe. Si no existe, queda en el informe.',
    'SIM': 'Opcional. Se usa como opción de variante sólo si la empresa la definió; si no, queda en el informe.',
    'Región': 'Opcional. Igual que SIM.',
    'Referencia de origen': 'Opcional. Cualquier referencia del sistema anterior, para conciliar.',
    'Costo histórico': 'Opcional. Sólo se lee con permiso de costos y sólo va al informe: no cambia ningún costo.',
    'Cantidad anterior': (
        'Opcional. Lo que el sistema anterior decía que había. SÓLO REFERENCIA para un recuento posterior: '
        'nunca se aplica al stock. Vacío = no informado; 0 = cero. Puede haber una columna por almacén '
        '(«Cantidad anterior - Centro»).'
    ),
}


def history_template_bytes(company=None) -> bytes:
    """
    The «base histórica» template: one sheet, its headers, codes as TEXT.

    Every code column — and the external id, which is often all digits — is
    formatted as text for the whole column, so that a code typed into it keeps
    its zeros. It carries no stock: quantities have their own upload.
    """
    workbook = _workbook()
    sheet = workbook.active
    sheet.title = 'Productos'
    _header_row(sheet, HISTORY_TEMPLATE_HEADERS)
    header_notes(sheet, _HISTORY_HEADER_NOTES)
    sheet.freeze_panes = 'A2'
    _autosize(sheet, [26, 24, 18, 42, 24, 18, 22, 22, 18, 18, 14, 14, 12, 12, 22, 16, 18])
    text_columns(sheet, ('A', 'B', 'C', 'J', 'O'))
    number_columns(sheet, ('I', 'P'))
    add_cell_list(sheet, 'E', cell_list(_company_category_names(company)))
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def history_report_csv(job, *, include_cost=False) -> str:
    """
    LABEL-MIGRATION-01 — the reconciliation report: every row of a historical job.

    What the old system called it (external id, old SKU, brand, model, variant),
    what happened to the row, which product it is in this catalogue and which
    codes it keeps. Guarded like the error report: it repeats the upload.

    The old quantities are a REFERENCE for a later count, never stock; blank
    means «not provided». The historical cost only goes to `costs.view`.
    """
    from .xlsx_reader import guard_text

    def cell(value):
        return guard_text(str(value if value is not None else ''))

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([
        'Fila', 'Acción', 'Clasificación', 'Id externo', 'Referencia de origen', 'SKU antiguo', 'Nombre del archivo',
        'Nombre del catálogo', 'Producto', 'Marca', 'Modelo', 'Capacidad', 'Color', 'SIM', 'Región', 'Variante',
        'Códigos', 'Cantidades anteriores (referencia)', *(['Costo histórico'] if include_cost else []),
        'Marcas de revisión', 'Error', 'Advertencia',
    ])
    for row in job.rows.order_by('row_number', 'pk'):
        data = row.normalized_data or {}
        history = data.get('history') or {}
        writer.writerow([
            row.row_number, cell(row.action), cell(history.get('classification')), cell(history.get('external_id')),
            cell(history.get('source_reference')), cell(history.get('legacy_code')),
            cell(data.get('name')), cell(history.get('catalogue_name')),
            cell(history.get('product_id') or data.get('product_id') or ''),
            cell(history.get('brand')), cell(history.get('model')), cell(history.get('capacity')),
            cell(history.get('colour')), cell(history.get('sim')), cell(history.get('region')),
            cell(history.get('variant')),
            cell(' | '.join(f"{c.get('code')} ({c.get('symbology')}, {c.get('status')})"
                            for c in history.get('codes') or [])),
            cell(' | '.join(f"{q.get('column')}: {q.get('value')}" for q in history.get('old_quantities') or [])),
            *([cell(data.get('historical_cost'))] if include_cost else []),
            cell(', '.join(history.get('flags') or [])),
            cell(' · '.join(row.errors or [])), cell(' · '.join(row.warnings or [])),
        ])
    return '\ufeff' + buffer.getvalue()


INVENTORY_EXPORT_SHEET = 'Inventario'
INVENTORY_EXPORT_HEADERS = ['CODIGO', 'CODIGO EAN', 'NOMBRE']


def product_codes(company) -> dict[int, dict[str, str]]:
    """
    product id -> `{'internal': code, 'external': code}`, the active codes only.

    One code of each kind: the first in the model's own order (the primary
    one, then by code). What an export writes under «Código» and under
    «Código de barras» is decided HERE, for every file.
    """
    codes: dict[int, dict[str, str]] = {}
    for product_id, code, symbology in ProductBarcode.objects.filter(
        company=company, is_active=True,
    ).values_list('product_id', 'code', 'symbology'):
        slot = codes.setdefault(product_id, {})
        if symbology == ProductBarcode.INTERNAL:
            slot.setdefault('internal', code)
        else:
            slot.setdefault('external', code)
    return codes


def inventory_export_table(*, company, branches, include_quantities: bool, products=None):
    """
    The inventory sheet as data: `(headers, rows)`.

    `CODIGO · CODIGO EAN · NOMBRE` plus one `ALMACEN <branch name>` column per
    branch — the shape the stock importer reads. A row is
    `[internal code, external code, name, quantity per branch…]`; a quantity is
    None when the sheet is asked for blank.

    `products` narrows which products are listed (a queryset of this company's
    products). Without it: the active ones, as this export always did.
    """
    branches = list(branches)
    # DC1 — NO cost column here, on purpose. This sheet answers as it always
    # did (`LegacyInventoryExportTest`), and a file that comes back must not
    # re-state what things cost. The stock importer READS an optional
    # «Costo unitario» column when somebody adds one (`import_formats.UNIT_COST_HEADERS`).
    headers = INVENTORY_EXPORT_HEADERS + [f'ALMACEN {b.name}' for b in branches]
    if products is None:
        products = Product.objects.filter(company=company, is_active=True)
    products = list(products.order_by('name'))
    codes = product_codes(company)
    quantities = {
        (b, p): q
        for b, p, q in BranchStock.objects
        .filter(branch__in=branches)
        .values_list('branch_id', 'product_id', 'quantity')
    }
    rows = []
    for product in products:
        slot = codes.get(product.pk, {})
        rows.append([
            slot.get('internal', ''), slot.get('external', ''), product.name,
            *[
                quantities.get((branch.pk, product.pk), 0) if include_quantities else None
                for branch in branches
            ],
        ])
    return headers, rows


def inventory_export_bytes(*, company, branches, include_quantities: bool) -> bytes:
    """
    The catalogue with one column per branch, for counting and re-uploading.

    The header row is `CODIGO · CODIGO EAN · NOMBRE`, matching the shape the
    importer already recognises, plus one `<branch name>` column per branch. The
    result re-imports through the same detector as any other file.

    The rows are `inventory_export_table`'s; this is how they are laid out for
    `GET /api/admin/inventory/export/`, which answers as it always did.
    """
    from openpyxl.styles import Font, PatternFill

    branches = list(branches)
    workbook = _workbook()
    sheet = workbook.active
    sheet.title = INVENTORY_EXPORT_SHEET

    headers, rows = inventory_export_table(
        company=company, branches=branches, include_quantities=include_quantities,
    )
    header_fill = PatternFill('solid', fgColor='1F2937')
    for column, title in enumerate(headers, start=1):
        cell = sheet.cell(row=1, column=column, value=title)
        cell.font = Font(bold=True, color='FFFFFF')
        cell.fill = header_fill

    from .xlsx_reader import looks_like_formula, mark_as_text

    def text(row, column, value, *, as_code=False):
        cell = sheet.cell(row=row, column=column, value=value)
        if looks_like_formula(value):
            mark_as_text(cell)
        if as_code:
            cell.number_format = '@'

    for index, (internal, external, name, *quantities) in enumerate(rows, start=2):
        # Written as TEXT, always. A code stored as a number is how the owner's
        # own file lost the ability to keep a leading zero, and re-exporting the
        # same trap would repeat the damage on every round trip.
        #
        # And NEVER A FORMULA: a name or a code that starts like one (`=`, `+`,
        # `-`, `@`) was written as a live formula cell. It is a string cell
        # marked as text, with the text it had; the stock importer reads it back.
        text(index, 1, internal, as_code=True)
        text(index, 2, external, as_code=True)
        text(index, 3, name)
        for offset, quantity in enumerate(quantities):
            cell = sheet.cell(row=index, column=4 + offset)
            if quantity is not None:
                cell.value = quantity
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
    from .xlsx_reader import guard_text

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(['Hoja', 'Fila', 'Identificador', 'Producto', 'Error', 'Advertencia'])
    for row in job.rows.exclude(errors=[]).order_by('row_number', 'pk'):
        data = row.normalized_data or {}
        # Every text column is GUARDED. This file repeats what the upload
        # carried — a sheet name, a code, a product name — and it is opened in
        # a spreadsheet: `=HYPERLINK(…)` in a cell of a CSV is a formula.
        writer.writerow([
            guard_text(str(row.sheet_name or '')), row.row_number, guard_text(str(row.match_key or '')),
            guard_text(str(data.get('name', '') or '')),
            guard_text(' · '.join(row.errors or [])),
            guard_text(' · '.join(row.warnings or [])),
        ])
    return '﻿' + buffer.getvalue()
