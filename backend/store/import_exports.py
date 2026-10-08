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


def _instructions_sheet(workbook):
    from openpyxl.styles import Alignment, Font, PatternFill

    rules = image_rules()
    sheet = workbook.create_sheet('Instrucciones')
    sheet.sheet_view.showGridLines = False
    _autosize(sheet, [4, 34, 78])

    def title(row, text):
        cell = sheet.cell(row=row, column=2, value=text)
        cell.font = Font(bold=True, size=14)
        sheet.merge_cells(start_row=row, start_column=2, end_row=row, end_column=3)

    def step(row, number, heading, body):
        sheet.cell(row=row, column=1, value=number).font = Font(bold=True)
        sheet.cell(row=row, column=2, value=heading).font = Font(bold=True)
        cell = sheet.cell(row=row, column=3, value=body)
        cell.alignment = Alignment(wrap_text=True, vertical='top')
        sheet.cell(row=row, column=2).alignment = Alignment(wrap_text=True, vertical='top')
        sheet.row_dimensions[row].height = max(30, 15 * (1 + len(body) // 75))

    title(1, 'Cómo cargar productos, con su categoría, su destino y sus imágenes')
    note = sheet.cell(
        row=2, column=2,
        value='Esta hoja, «Categorías» y «Ejemplo» son de ayuda: no se importan. '
              'Los productos se escriben en la hoja «Productos».',
    )
    note.font = Font(italic=True, color='6B7280')
    sheet.merge_cells(start_row=2, start_column=2, end_row=2, end_column=3)

    separator = rules['separator']
    formats = ', '.join(rules['formats'])
    from .import_formats import DESTINATION_INTERNAL, DESTINATION_LABELS, DESTINATION_ONLINE

    steps = [
        ('«Categoría» se elige de una lista',
         'La hoja «Categorías» trae las de tu empresa. Una categoría que no está en la lista '
         'debe crearse antes en Productos → Categorías, o activar «crear las categorías que '
         'falten» al importar.'),
        (f'«Destino»: {DESTINATION_LABELS[DESTINATION_ONLINE]}',
         'El producto aparece en la tienda en línea y se puede comprar por la web.'),
        (f'«Destino»: {DESTINATION_LABELS[DESTINATION_INTERNAL]}',
         'El producto NO aparece en la tienda en línea ni se puede comprar por la web. Sigue '
         'en inventario y se vende en caja y en servicio técnico, según los permisos de cada '
         'persona.'),
        ('«Destino» vacío no cambia nada',
         'Un producto nuevo se publica en la tienda en línea. Uno que ya existe se queda como '
         'estaba. Después se cambia desde la ficha del producto.'),
        ('Las imágenes son opcionales',
         'Puedes dejar «Imagen principal» e «Imágenes» vacías y añadir o cambiar las fotos '
         'después, desde la ficha del producto.'),
        ('Las imágenes NO se pegan dentro de Excel',
         'Una imagen pegada en una celda no se puede leer. No insertes fotos en este archivo.'),
        ('Excel guarda sólo los NOMBRES de archivo',
         'En las columnas de imagen se escribe cómo se llama cada archivo, con su extensión: '
         'iphone16-front.webp'),
        ('«Imagen principal» lleva UN nombre',
         'Es la foto que verá el catálogo. Ejemplo: iphone16-front.webp'),
        (f'«Imágenes» lleva varios, separados por {separator}',
         f'Todas las fotos del producto, en el orden en que deben verse: '
         f'iphone16-front.webp{separator}iphone16-back.webp{separator}iphone16-side.webp'),
        ('Las imágenes se adjuntan DESPUÉS, junto al Excel',
         'Al subir este archivo, la pantalla pide los archivos de imagen: sueltos, o todos '
         'dentro de un ZIP.'),
        ('Los nombres deben coincidir',
         'El nombre escrito aquí y el del archivo adjunto tienen que ser el mismo, con su '
         'extensión. No importan las mayúsculas. La previsualización dice cuál falta.'),
        ('Formatos admitidos', f'{formats}. Otros (GIF, SVG, HEIC, PDF) se rechazan.'),
        ('Máximo por producto', f'{rules["max_per_product"]} imágenes por producto.'),
        ('Tamaño máximo',
         f'{rules["max_file_mb"]} MB por imagen. En una misma carga, hasta '
         f'{rules["max_files"]} archivos y {rules["max_total_mb"]} MB en total.'),
        ('Ejemplo completo',
         'La hoja «Ejemplo» trae tres productos llenos y la lista de archivos que habría que '
         'adjuntar con ellos.'),
    ]
    for index, (heading, body) in enumerate(steps, start=1):
        step(3 + index, index, heading, body)

    last = 3 + len(steps) + 2
    sheet.cell(row=last, column=2, value='En una fila queda así').font = Font(bold=True)
    box = PatternFill('solid', fgColor='F3F4F6')
    for offset, (label, value) in enumerate([
        ('Código', PRODUCT_TEMPLATE_EXAMPLE[1]),
        ('Nombre', PRODUCT_TEMPLATE_EXAMPLE[2]),
        ('Imagen principal', PRODUCT_TEMPLATE_EXAMPLE[6]),
        ('Imágenes', PRODUCT_TEMPLATE_EXAMPLE[7]),
    ], start=1):
        left = sheet.cell(row=last + offset, column=2, value=label)
        right = sheet.cell(row=last + offset, column=3, value=value)
        left.fill = right.fill = box
        left.font = Font(bold=True)
    sheet.cell(
        row=last + 6, column=2,
        value='Y se adjuntan estos tres archivos: iphone16-front.webp, iphone16-back.webp, '
              'iphone16-side.webp',
    )
    sheet.merge_cells(start_row=last + 6, start_column=2, end_row=last + 6, end_column=3)


def _example_sheet(workbook):
    from openpyxl.styles import Font

    separator = image_rules()['separator']
    sheet = workbook.create_sheet('Ejemplo')
    _header_row(sheet, PRODUCT_TEMPLATE_HEADERS)
    rows = [
        PRODUCT_TEMPLATE_EXAMPLE,
        ['', 'FUNDA-16PM-N', 'Funda de silicona negra', 'Para iPhone 16 Pro Max.', 79.90,
         'Accesorios', 'funda-negra.png', f'funda-negra.png{separator}funda-negra-lateral.png'],
        ['', 'CABLE-USBC-1M', 'Cable USB-C de 1 metro', '', 49.90, 'Accesorios', '', ''],
    ]
    for offset, values in enumerate(rows, start=2):
        for column, value in enumerate(values, start=1):
            sheet.cell(row=offset, column=column, value=value)
    _autosize(sheet, [20, 18, 42, 40, 16, 20, 34, 58])

    start = len(rows) + 4
    sheet.cell(row=start, column=2, value='Archivos que se adjuntan con este Excel').font = Font(bold=True)
    files = ['iphone16-front.webp', 'iphone16-back.webp', 'iphone16-side.webp',
             'funda-negra.png', 'funda-negra-lateral.png']
    for offset, name in enumerate(files, start=1):
        sheet.cell(row=start + offset, column=2, value=name)
    sheet.cell(
        row=start + len(files) + 2, column=2,
        value='El tercer producto no lleva imagen: sus dos columnas quedan vacías.',
    ).font = Font(italic=True, color='6B7280')
    sheet.cell(
        row=start + len(files) + 3, column=2,
        value='Esta hoja es un ejemplo y no se importa.',
    ).font = Font(italic=True, color='6B7280')


def _company_category_names(company) -> list[str]:
    """The names a row may write under «Categoría»: this company's, active, in order. Never a fixed list."""
    from .models import Category

    if company is None:
        return []
    return list(
        Category.objects.filter(company=company, is_active=True).order_by('name').values_list('name', flat=True)
    )


def _categories_sheet(workbook, names):
    from openpyxl.styles import Alignment, Font

    sheet = workbook.create_sheet('Categorías')
    _header_row(sheet, ['CATEGORÍAS DE TU EMPRESA', 'CÓMO SE USAN'])
    sheet.cell(row=2, column=1, value='Nombre exacto de la categoría').font = Font(size=8, italic=True, color='6B7280')
    note = sheet.cell(row=2, column=2, value=(
        'Son las categorías activas de tu empresa el día que descargaste esta plantilla. '
        'En la hoja «Productos», la columna «Categoría» las ofrece en una lista. '
        'Para una categoría nueva, créala antes en Productos → Categorías, o escribe su nombre '
        'y activa «crear las categorías que falten» al importar. Esta hoja no se importa.'
    ))
    note.font = Font(size=8, italic=True, color='6B7280')
    note.alignment = Alignment(wrap_text=True, vertical='top')
    for offset, name in enumerate(names):
        cell = sheet.cell(row=3 + offset, column=1, value=name)
        # A name is text whatever it looks like: «=1+1» is a category somebody
        # typed, not a formula for Excel to run when the file is opened.
        cell.data_type = 's'
    if not names:
        sheet.cell(row=3, column=2, value='Tu empresa todavía no tiene categorías.').font = Font(italic=True, color='6B7280')
    sheet.freeze_panes = 'A3'
    _autosize(sheet, [36, 90])


def _product_drop_downs(sheet, category_count: int):
    """
    The lists of «Categoría» and «Destino», and codes kept as text.

    The category list HELPS and does not forbid: a name that is not in it is
    how «crear las que falten» is used. The destination list does forbid: it
    has two values and the importer accepts no third.
    """
    from openpyxl.worksheet.datavalidation import DataValidation

    from .import_formats import DESTINATION_INTERNAL, DESTINATION_LABELS, DESTINATION_ONLINE

    first, last = 4, 3 + _TEMPLATE_ROWS
    if category_count:
        categories = DataValidation(
            type='list', formula1=f"'Categorías'!$A$3:$A${2 + category_count}",
            allow_blank=True, showErrorMessage=False,
        )
        categories.add(f'F{first}:F{last}')
        sheet.add_data_validation(categories)

    choices = ','.join((DESTINATION_LABELS[DESTINATION_ONLINE], DESTINATION_LABELS[DESTINATION_INTERNAL]))
    destination = DataValidation(
        type='list', formula1=f'"{choices}"', allow_blank=True, showErrorMessage=True,
        errorTitle='Destino', error='Elige «Publicar en e-commerce» o «Solo stock interno», o deja la celda vacía.',
    )
    destination.add(f'I{first}:I{last}')
    sheet.add_data_validation(destination)

    # A barcode read as a number loses its leading zeros and gains an «E+11».
    # On the COLUMN, not cell by cell: five hundred formatted empty cells are
    # five hundred «fila vacía» in every preview.
    for column in ('A', 'B'):
        sheet.column_dimensions[column].number_format = '@'


def product_template_bytes(company=None) -> bytes:
    """
    A blank, VALID product template — that also explains how images travel.

    `company` is whose template it is: its «Categorías» sheet lists THAT
    company's categories, read when it is downloaded. Without one the sheet is
    there and empty.

    Deliberately not a copy of the owner's 18-column sheet. That file carries
    two rows of headers, twenty data validations openpyxl cannot read back, and
    ten columns this catalogue has nowhere to put — including tax fields for
    electronic invoicing this platform does not do. Reproducing it would hand
    people a template whose columns are mostly ignored.

    The importer still reads that file. This is what we ASK for; that is what we
    ACCEPT.

    FOUR SHEETS, AND ONLY THE FIRST IS DATA. «Productos» comes first because
    a preview without a chosen sheet reads the first one. Its help row and its
    example row are recognised and skipped, and «Categorías», «Instrucciones»
    and «Ejemplo» are never offered as product sheets: uploading the template
    exactly as it was downloaded creates nothing.
    """
    from openpyxl.styles import Alignment, Font

    workbook = _workbook()
    sheet = workbook.active
    sheet.title = 'Productos'

    _header_row(sheet, PRODUCT_TEMPLATE_HEADERS)
    for column, note in enumerate(PRODUCT_TEMPLATE_HELP, start=1):
        cell = sheet.cell(row=2, column=column, value=note)
        cell.font = Font(size=8, italic=True, color='6B7280')
        cell.alignment = Alignment(wrap_text=True, vertical='top')
    for column, value in enumerate(PRODUCT_TEMPLATE_EXAMPLE, start=1):
        cell = sheet.cell(row=3, column=column, value=value)
        # Grey italics: it reads as an example, and it is skipped on import.
        cell.font = Font(italic=True, color='6B7280')

    sheet.freeze_panes = 'A3'
    _autosize(sheet, [20, 18, 42, 40, 16, 24, 34, 58, 26])

    categories = _company_category_names(company)
    _product_drop_downs(sheet, len(categories))
    _categories_sheet(workbook, categories)
    _instructions_sheet(workbook)
    _example_sheet(workbook)

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
