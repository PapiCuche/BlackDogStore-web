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
    'slug':        {'label': 'Slug',                'required': False},
    'is_active':   {'label': 'Activo',              'required': False},
}

STOCK_FIELDS = {
    'external_id': {'label': 'ID del sistema origen', 'required': False},
    'code':        {'label': 'Código interno',        'required': False},
    'barcode':     {'label': 'Código de barras',      'required': False},
    'name':        {'label': 'Nombre',                'required': False},
}

# Columns the presets below deliberately leave UNMAPPED, and why. Surfaced in
# the preview so an operator sees that a column was recognised and skipped
# rather than silently ignored.
UNMAPPED_NOTES = {
    'codigo sunat': 'Campo tributario: esta plataforma no emite comprobantes electrónicos.',
    'afectacion': 'Campo tributario: esta plataforma no emite comprobantes electrónicos.',
    'unidad de medida': 'No hay unidades de medida en el catálogo todavía.',
    'peso': 'El catálogo no registra peso.',
    'precio costo': 'El catálogo no registra costos; no se puede calcular margen.',
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


PRESETS = [_preset_products_sunat_pos(), _preset_stock_sunat_pos()]


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
        for field, header_key in preset['mapping'].items():
            if header_key in normalized:
                mapping[field] = normalized.index(header_key)

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

    An operator who uploads a file with a `Precio costo` column and sees nothing
    about it reasonably assumes costs were imported. Saying "recognised, not
    imported, here is why" costs one line and prevents that.
    """
    used = set(mapping.values())
    out = []
    for index, header in enumerate(headers):
        if index in used or not str(header).strip():
            continue
        key = normalize_header(header)
        reason = UNMAPPED_NOTES.get(key)
        if reason:
            out.append({'column': str(header), 'reason': reason})
    return out
