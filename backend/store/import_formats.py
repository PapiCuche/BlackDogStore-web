"""
Recognising a spreadsheet's shape — Phase C1.4.

THE PROBLEM PRESETS SOLVE
-------------------------
The owner's inventory export has five columns and their product template has
eighteen. Mapping eighteen columns by hand is five minutes the first time and a
mistake every time after — and the mistake lands in stock.

THE PROBLEM PRESETS MUST NOT CREATE
-----------------------------------
The obvious shortcut is `if company.slug == 'black-dog-store'`. That is not a
feature, it is one customer welded into a platform: the second tenant with the
same accounting system gets nothing, and the branch of code that runs for one
customer is the branch nobody else's tests cover.

So recognition is by HEADER SIGNATURE — a hash of the normalised header row and
the sheet it came from. Any tenant whose system exports those columns is
recognised by the same preset. The owner's two formats are seeded as presets
because they are real formats produced by real software (the SUNAT-facing POS
that generated them), not because of whose files they are.

A signature is NOT the file's SHA256. The data changes with every upload; the
shape does not. Hashing the file would recognise nothing twice.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata

from .models import BulkImportJob


def normalize_header(text: str) -> str:
    """
    Fold a header to a comparable key.

    Case, accents and punctuation all vary between exports of the same report —
    `CODIGO EAN`, `Código EAN`, `codigo_ean` are one column. Digits are kept:
    `ALMACEN 1 - 11416` and `ALMACEN 2 - 11417` are NOT the same column, and
    collapsing them would silently merge two warehouses.
    """
    text = str(text or '').strip()
    text = unicodedata.normalize('NFKD', text)
    text = ''.join(c for c in text if not unicodedata.combining(c))
    text = text.lower()
    text = re.sub(r'[^a-z0-9]+', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()


def header_signature(import_type: str, sheet_name: str, headers) -> str:
    """
    A stable fingerprint of one sheet's shape.

    Order matters — two files with the same columns in different order need
    different mappings — and the sheet name is included because a workbook can
    hold several shapes at once.
    """
    parts = [import_type, normalize_header(sheet_name)]
    parts.extend(normalize_header(h) for h in headers)
    return hashlib.sha256('␟'.join(parts).encode('utf-8')).hexdigest()


# =============================================================================
# Field vocabularies
# =============================================================================
#
# What a mapping is allowed to point AT. Anything not here cannot be written by
# an import, which is the point: the set of writable fields is declared, not
# whatever a request happens to name.

PRODUCT_FIELDS = {
    'name':        {'label': 'Nombre',              'required': True},
    'code':        {'label': 'Código interno',      'required': False},
    'barcode':     {'label': 'Código de barras',    'required': False},
    'price':       {'label': 'Precio de venta',     'required': False},
    'description': {'label': 'Descripción',         'required': False},
    'category':    {'label': 'Categoría',           'required': False},
    'image_url':   {'label': 'URL de imagen',       'required': False},
    # BULK-MEDIA: nombres de ARCHIVO, no direcciones. Los archivos viajan con
    # el libro (sueltos o en un ZIP) y se casan por nombre.
    'image_main':  {'label': 'Imagen principal (archivo)', 'required': False},
    'image_files': {'label': 'Imágenes (archivos, separados por |)', 'required': False},
    'slug':        {'label': 'Slug',                'required': False},
    # «Activo» o «Inactivo». Sólo decide cómo NACE un producto nuevo: la carga
    # nunca activa ni desactiva uno que ya existe.
    'is_active':   {'label': 'Estado (Activo o Inactivo)', 'required': False},
    # PRODUCT-DESTINATION-01: «Publicar en e-commerce» o «Solo stock interno».
    'destination': {'label': 'Destino (e-commerce o sólo stock interno)', 'required': False},
    # «Identificador» de un archivo descargado para volver a cargar: dice de qué
    # producto de ESTA empresa es la fila aunque haya cambiado de nombre.
    'identifier':  {'label': 'Identificador (de un archivo descargado)', 'required': False},
    # DC1: el costo de referencia del catálogo. Vacío no borra el que hay.
    'reference_cost': {'label': 'Costo de referencia', 'required': False},
}

#: DC1 — the headers that ARE a cost column, already folded by `normalize_header`.
#: Looked for by header when the mapping that arrived does not name one, for the
#: same reason as the destination: a remembered mapping predates the column.
REFERENCE_COST_HEADERS = ('costo de referencia', 'costo referencia', 'precio costo')
UNIT_COST_HEADERS = ('costo unitario', 'costo', 'costo de compra')


def find_column(headers, wanted, taken=()) -> int | None:
    """The first column whose folded header is one of `wanted`, outside `taken`."""
    taken = set(taken)
    for key in wanted:
        for index, header in enumerate(headers):
            if index not in taken and normalize_header(header) == key:
                return index
    return None

# =============================================================================
# Where a product goes — PRODUCT-DESTINATION-01
# =============================================================================
#
# The two values the template offers in its drop-down, and what a cell is taken
# to mean. Read forgivingly — case, accents, spacing — because the cell is typed
# by a person; but only these, because a guess here publishes something on the
# web that somebody meant to keep off it.

DESTINATION_ONLINE, DESTINATION_INTERNAL = 'online', 'internal'
DESTINATION_LABELS = {
    DESTINATION_ONLINE: 'Publicar en e-commerce',
    DESTINATION_INTERNAL: 'Solo stock interno',
}
_DESTINATION_WORDS = {
    'publicar en e commerce': DESTINATION_ONLINE,
    'publicar en ecommerce': DESTINATION_ONLINE,
    'e commerce': DESTINATION_ONLINE,
    'ecommerce': DESTINATION_ONLINE,
    'solo stock interno': DESTINATION_INTERNAL,
    'stock interno': DESTINATION_INTERNAL,
}


#: A header is the destination column when it IS one of these, or starts with
#: the first: «Destino», «Destino (pendiente web)», «Destino web», «Destino:».
DESTINATION_HEADERS = ('destino', 'destino pendiente web')


def find_destination_column(headers, taken=()) -> int | None:
    """
    The column whose header says it is the destination, or None.

    WHY THIS IS NOT LEFT TO THE MAPPING. A mapping can arrive without it for
    reasons nobody chose: the screen remembers the one it used for this shape
    of file before the column existed; a format's preset never had it; somebody
    assigned columns by hand and skipped it. In every one of those the cells say
    «Solo stock interno», nobody reads them, and the product is published. So
    the column is looked for by its header whenever the mapping does not name one.
    """
    taken = set(taken)
    exact = None
    for index, header in enumerate(headers):
        if index in taken:
            continue
        key = normalize_header(header)
        if key in DESTINATION_HEADERS:
            return index
        if exact is None and (key == 'destino' or key.startswith('destino ')):
            exact = index
    return exact


def parse_destination(text) -> str | None:
    """'online', 'internal', '' for an empty cell — or None for anything else."""
    key = normalize_header(text)
    if not key:
        return ''
    return _DESTINATION_WORDS.get(key)

# =============================================================================
# Whether a product is in use — the «Estado» column of a file to reload
# =============================================================================

STATE_HEADER = 'Estado'
IDENTIFIER_HEADER = 'Identificador'


def product_identifier(company_id, product_id) -> str:
    """
    What the file to reload writes under «Identificador».

    Stable (a rename does not change it), readable, bound to the company and no
    secret: both numbers are already in the panel's own addresses. It grants
    nothing — the importer accepts it only for a product of the company the
    caller is acting on.
    """
    return f'{company_id}-{product_id}'


def parse_product_identifier(text):
    """`(company id, product id)` from an «Identificador» cell, or None when it is not one."""
    found = re.fullmatch(r'(\d{1,12})-(\d{1,12})', str(text or '').strip())
    return (int(found.group(1)), int(found.group(2))) if found else None
STATE_LABELS = {True: 'Activo', False: 'Inactivo'}
#: Read forgivingly (case, accents, spaces): an older «Activo» column holds
#: Sí/No, and a spreadsheet may hold 1/0 or TRUE/FALSE.
_STATE_WORDS = {
    'activo': True, 'inactivo': False,
    'si': True, 'no': False,
    '1': True, '0': False,
    'true': True, 'false': False,
    'verdadero': True, 'falso': False,
}


def parse_state(text):
    """True, False, '' for an empty cell — or None for anything else."""
    key = normalize_header(text)
    if not key:
        return ''
    return _STATE_WORDS.get(key)


STOCK_FIELDS = {
    'external_id': {'label': 'ID del sistema origen', 'required': False},
    'code':        {'label': 'Código interno',        'required': False},
    'barcode':     {'label': 'Código de barras',      'required': False},
    'name':        {'label': 'Nombre',                'required': False},
    # DC1: what one unit cost, for rows that INCREASE the stock. Optional.
    'unit_cost':   {'label': 'Costo unitario',        'required': False},
}

# Columns the presets below deliberately leave UNMAPPED, and why. Surfaced in
# the preview so an operator sees that a column was recognised and skipped
# rather than silently ignored.
UNMAPPED_NOTES = {
    'codigo sunat': 'Campo tributario: esta plataforma no emite comprobantes electrónicos.',
    'afectacion': 'Campo tributario: esta plataforma no emite comprobantes electrónicos.',
    'unidad de medida': 'No hay unidades de medida en el catálogo todavía.',
    'peso': 'El catálogo no registra peso.',
    'marca': 'No hay marcas en el catálogo todavía.',
    'stock minimo': 'El punto de reposición se configura por sucursal, no por producto.',
    'compra': 'El catálogo no distingue artículos de compra y de venta.',
    'venta': 'El catálogo no distingue artículos de compra y de venta.',
    'almacenable': 'Todo artículo del catálogo es almacenable.',
}


# =============================================================================
# Built-in presets
# =============================================================================

def _preset_products_sunat_pos():
    """
    The 18-column product template the owner's POS exports.

    Row 1 is a banner of merged group titles ('Información del producto' across
    A:I, and so on); row 2 holds the real headers. So `header_row` is 2 — and
    getting that wrong imports one product called "Información del producto".

    THE PRICE COLUMN IS THE INTERESTING ONE
    ---------------------------------------
    There is no plain "Precio de venta". The only sale price in the file is
    `Precio venta - 11834`, which is a price FOR ONE BRANCH — that POS keeps a
    price list per location. This catalogue has one price per product. Mapping
    it is still right (it is the only price there is) but it is a narrowing, so
    the preview says so rather than letting a per-branch price quietly become
    the price everywhere.

    `Stock inicial - ALMACEN 1 - 11416` is NOT mapped. §25: stock has its own
    importer, with its own preview and its own Kardex movements. A product
    import that also moved stock would write to the ledger from a screen whose
    warnings never mention it.
    """
    return {
        'key': 'products_sunat_pos_18',
        'import_type': BulkImportJob.PRODUCTS,
        'label': 'Productos — plantilla POS (18 columnas)',
        'sheet_name': 'Productos',
        'header_row': 2,
        'headers': [
            'Código de barras', 'Código', 'Unidad de medida', 'Nombre', 'Peso',
            'Código SUNAT', 'Descripción', 'Precio costo', 'Afectación',
            'Compra', 'Venta', 'Almacenable', 'Stock mínimo', 'Marca',
            'Stock inicial - ALMACEN 1 - 11416',
            'Area - black dog octavio - 817', 'Precio venta - 11834',
            'SUCURSAL 1 - 11357',
        ],
        # Matched on a SUBSET so the preset still fires when the branch-numbered
        # columns differ — those carry another tenant's warehouse ids.
        'match_headers': [
            'codigo de barras', 'codigo', 'unidad de medida', 'nombre', 'peso',
            'codigo sunat', 'descripcion', 'precio costo', 'afectacion',
        ],
        'mapping': {
            'barcode': 'codigo de barras',
            'code': 'codigo',
            'name': 'nombre',
            'description': 'descripcion',
            # DC1: what that POS calls its cost is the catalogue's reference cost.
            'reference_cost': 'precio costo',
        },
        # Filled in by `detect()` once the real headers are known, because the
        # branch numbers in them belong to whoever exported the file.
        'dynamic': {'price': r'^precio venta'},
        'notes': [
            'El precio de venta de esta plantilla es por sucursal («Precio venta - …»). '
            'El catálogo guarda un solo precio por producto, así que se usará ese valor '
            'como precio único.',
            'La columna de stock inicial NO se importa aquí: el inventario tiene su '
            'propio importador, que genera movimientos de Kardex.',
        ],
    }


def _preset_products_platform():
    """
    The platform's own product template (`import_exports.product_template_bytes`).

    It had no preset: whoever downloaded the template had to map its columns by
    hand before importing it back. Matched on the two columns no product file
    can do without, so a template with columns removed or reordered still fits.
    Listed AFTER the POS preset, which is the more specific of the two.
    """
    return {
        'key': 'products_platform',
        'import_type': BulkImportJob.PRODUCTS,
        'label': 'Productos — plantilla de la plataforma',
        'sheet_name': '',
        'header_row': 1,
        'headers': [],
        'match_headers': ['nombre', 'precio de venta'],
        'mapping': {
            'barcode': 'codigo de barras',
            'code': 'codigo',
            'name': 'nombre',
            'description': 'descripcion',
            'price': 'precio de venta',
            'category': 'categoria',
            'image_url': 'url de imagen',
            'image_main': 'imagen principal',
            'image_files': 'imagenes',
            # Both spellings: as the template names it now, and as the owner's
            # planning sheet named it while the feature did not exist.
            'destination': DESTINATION_HEADERS,
            # Only in the file to reload (`inventory_exports`); the template has none.
            'is_active': 'estado',
            'identifier': 'identificador',
            'reference_cost': ('costo de referencia', 'costo referencia'),
        },
        'notes': [],
    }


def _preset_stock_sunat_pos():
    """
    The 5-column inventory export: `ID · CODIGO · CODIGO EAN · NOMBRE · ALMACEN …`.

    The warehouse column is matched by PATTERN, not by its exact text, because
    `ALMACEN 1 - 11416` contains the exporting system's warehouse id. Another
    tenant's file says `ALMACEN 1 - 20993`, and a preset keyed to the literal
    string would recognise one shop in the world.

    Which branch that column means is NOT guessed from the number. `11416` is an
    id in somebody else's database; reading it as a `Branch.pk` here would map a
    warehouse onto whichever branch happened to have that primary key. The
    operator picks, once, and the profile remembers.
    """
    return {
        'key': 'stock_sunat_pos_5',
        'import_type': BulkImportJob.STOCK,
        'label': 'Inventario — export POS (ID/CÓDIGO/EAN/NOMBRE/ALMACÉN)',
        'sheet_name': None,
        'header_row': 1,
        'headers': ['ID', 'CODIGO', 'CODIGO EAN', 'NOMBRE', 'ALMACEN 1 - 11416'],
        'match_headers': ['id', 'codigo', 'codigo ean', 'nombre'],
        'mapping': {
            'external_id': 'id',
            'code': 'codigo',
            'barcode': 'codigo ean',
            'name': 'nombre',
        },
        'warehouse_pattern': r'^(almacen|deposito|sucursal|tienda|stock)\b',
        'notes': [
            'Las columnas de almacén se asignan a sucursales de forma explícita. '
            'El número que aparece en el encabezado es del sistema de origen y no '
            'se interpreta como identificador de esta plataforma.',
        ],
    }


PRESETS = [_preset_products_sunat_pos(), _preset_products_platform(), _preset_stock_sunat_pos()]


def warehouse_columns(headers, *, pattern=r'^(almacen|deposito|sucursal|tienda|stock)\b'):
    """
    Header indexes that look like a warehouse quantity column.

    Returns `[(index, original_header)]`. Detection is by prefix so today's
    single `ALMACEN 1 - 11416` and tomorrow's three warehouses go through
    exactly the same code — §40. Nothing is assigned to a branch here; that is
    the operator's decision.
    """
    found = []
    for index, header in enumerate(headers):
        key = normalize_header(header)
        if key and re.match(pattern, key):
            found.append((index, str(header)))
    return found


def detect(import_type: str, sheet_name: str, headers):
    """
    Find a preset matching this sheet, and build its concrete mapping.

    Returns `None` when nothing matches — an unrecognised file is mapped by
    hand, which is a normal outcome and not an error.
    """
    normalized = [normalize_header(h) for h in headers]
    present = set(normalized)

    for preset in PRESETS:
        if preset['import_type'] != import_type:
            continue
        if not set(preset['match_headers']).issubset(present):
            continue
        if preset['sheet_name'] and normalize_header(preset['sheet_name']) != normalize_header(sheet_name):
            continue

        mapping = {}
        for field, header_keys in preset['mapping'].items():
            # One header, or several spellings of it: the first that is there.
            for header_key in ([header_keys] if isinstance(header_keys, str) else header_keys):
                if header_key in normalized:
                    mapping[field] = normalized.index(header_key)
                    break

        for field, regex in (preset.get('dynamic') or {}).items():
            for index, key in enumerate(normalized):
                if re.match(regex, key):
                    mapping[field] = index
                    break

        result = {
            'preset': preset['key'],
            'label': preset['label'],
            'header_row': preset['header_row'],
            'mapping': mapping,
            'notes': list(preset.get('notes') or []),
        }
        if import_type == BulkImportJob.STOCK:
            result['warehouse_columns'] = warehouse_columns(
                headers, pattern=preset.get('warehouse_pattern'),
            )
        return result
    return None


def unmapped_notes(headers, mapping):
    """
    Explain the columns that will be ignored.

    An operator who uploads a file with a `Peso` column and sees nothing about
    it reasonably assumes weights were imported. Saying "recognised, not
    imported, here is why" costs one line and prevents that. (DC1: `Precio
    costo` used to be the example; it is now read as the reference cost.)
    """
    used = set(mapping.values())
    out = []
    for index, header in enumerate(headers):
        if index in used or not str(header).strip():
            continue
        key = normalize_header(header)
        elsewhere = _belongs_to_stock(key)
        reason = UNMAPPED_NOTES.get(key) or elsewhere
        if reason:
            note = {'column': str(header), 'reason': reason}
            if reason == elsewhere:
                # Somebody expects this to go in. Said in the open, not folded away.
                note['important'] = True
            out.append(note)
    return out


#: IMPORT-SPLIT-01 — a product file is the CATALOGUE. A column that counts
#: things or names a device is not read, and saying so sends it to the upload
#: that does read it instead of leaving somebody to think stock went in.
#:
#: WHAT COUNTS STOCK is one definition, shared with the device upload (which
#: refuses such a column whole): a header whose first word — past «Nº de»,
#: «Total» — is one of these, unless what follows says it counts something else.
#: «Stock mínimo» is a reorder point, «Unidades de medida» a unit, «Cantidad de
#: SIM» a property of the model, «Unidades por caja» a pack size.
_COUNT_WORDS = {
    'stock', 'cantidad', 'cantidades', 'cant', 'unidades', 'qty', 'quantity',
    'existencia', 'existencias', 'inventario', 'saldo',
}
_BEFORE_THE_WORD = {'n', 'no', 'nro', 'num', 'numero', 'total', 'de', 'del'}
_COUNTS_SOMETHING_ELSE = {
    'minimo', 'minima', 'maximo', 'maxima', 'min', 'max', 'medida', 'sim', 'sims', 'por',
    'interno', 'reposicion', 'seguridad',
}
#: Headers that name ONE device. Not a bare «Serie»: in a catalogue that is as
#: often a product line.
SERIAL_HEADERS = (
    'numero de serie', 'n de serie', 'no de serie', 'nro de serie', 'num de serie',
    'numero serie', 'n serie', 'no serie', 'nro serie', 's n',
)
_DEVICE_HEADERS = {*SERIAL_HEADERS, 'imei', 'imei 1', 'imei1', 'imei 2', 'imei2', 'segundo imei'}
#: The quantity columns of an inventory export: «ALMACEN Centro», «Depósito 2».
_WAREHOUSE = re.compile(r'^(almacen|deposito)\b')


def is_warehouse_header(key: str) -> bool:
    """Whether a header, already folded by `normalize_header`, is a quantity column of a warehouse."""
    return bool(_WAREHOUSE.match(key))


def counts_stock(key: str) -> bool:
    """Whether a header, already folded by `normalize_header`, states HOW MANY there are."""
    words = key.split(' ')
    while words and words[0] in _BEFORE_THE_WORD:
        words = words[1:]
    if not words or words[0] not in _COUNT_WORDS:
        return False
    after = [word for word in words[1:] if word not in ('de', 'del')]
    return not (after and after[0] in _COUNTS_SOMETHING_ELSE)
NO_STOCK_HERE = (
    'Esta carga no mueve stock: sólo crea y edita productos. Las cantidades se cargan en '
    'Inventario › Carga de stock, y los equipos con serie en Inventario › Carga de equipos.'
)
NO_DEVICES_HERE = (
    'Una serie o un IMEI es de un equipo, no del producto. Los equipos se cargan uno por fila '
    'en Inventario › Carga de equipos.'
)


def _belongs_to_stock(key: str) -> str:
    if key in _DEVICE_HEADERS:
        return NO_DEVICES_HERE
    if counts_stock(key) or is_warehouse_header(key):
        return NO_STOCK_HERE
    return ''
