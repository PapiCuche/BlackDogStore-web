"""
Bulk product import — Phase C1.4.

TWO PASSES, AND WHY THE SECOND ONE DOES NOT TRUST THE FIRST
-----------------------------------------------------------
`preview()` reads the workbook, resolves every row against the catalogue, and
writes `BulkImportRow` staging records. It touches no business table.

`apply()` reads those staging records back OUT OF THE DATABASE and writes. It
does not accept rows from the browser, and it re-resolves every match, because
between the two clicks somebody may have created the product this row was going
to create. A preview is a forecast, not a promise.

ALL OR NOTHING
--------------
One row in error and nothing is applied. A half-imported catalogue is worse than
a rejected one: the operator has no way to tell which of six hundred lines
landed, so the only safe recovery is to check all of them by hand. Partial
import is listed as future work precisely because doing it properly means
reporting exactly what landed, which is a bigger feature than it looks.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from . import (
    import_exports, import_formats, import_media, product_codes, product_media, product_publication,
    storefront_media, xlsx_reader,
)
from .models import (
    BulkImportJob,
    BulkImportRow,
    Category,
    Product,
    ProductBarcode,
    ProductImage,
    normalize_barcode,
)


class ImportError_(Exception):
    """The import cannot proceed as requested. `code`: machine-readable, when a policy gave one."""

    def __init__(self, detail='', *, code=''):
        super().__init__(detail)
        self.code = code


# =============================================================================
# Value normalisation
# =============================================================================

def clean_price(raw):
    """
    A price, as a `Decimal`, or an explanation of why it is not one.

    NEVER a float. `float('19.99') * 100` is 1998.9999999999998, and a catalogue
    whose prices are floats eventually charges somebody a cent that does not
    exist.

    AMBIGUOUS SEPARATORS ARE REFUSED, NOT GUESSED (§34)
    --------------------------------------------------
    `1.999,90` is unambiguous (comma decimal). `1,999.90` is unambiguous (dot
    decimal). `1,999` is NOT: it is one thousand nine hundred and ninety-nine in
    Peru and one point nine nine nine in a file exported with English settings. The
    difference is a factor of a thousand on a price tag, so the row errors and
    asks.
    """
    text = str(raw or '').strip()
    if not text:
        return None, None
    text = text.replace(' ', '').replace('S/', '').replace('$', '')
    if not re.fullmatch(r'-?[\d.,]+', text):
        return None, 'El precio no es un número.'

    has_dot, has_comma = '.' in text, ',' in text
    if has_dot and has_comma:
        # The LAST separator is the decimal one; the other groups thousands.
        decimal_sep = ',' if text.rfind(',') > text.rfind('.') else '.'
        thousands = '.' if decimal_sep == ',' else ','
        text = text.replace(thousands, '').replace(decimal_sep, '.')
    elif has_comma:
        head, _, tail = text.rpartition(',')
        if len(tail) == 3 and head:
            return None, (
                f'«{raw}» es ambiguo: puede ser {head}{tail} o {head}.{tail}. '
                f'Escribe el precio sin separador de miles.'
            )
        text = text.replace(',', '.')
    elif has_dot:
        head, _, tail = text.rpartition('.')
        if len(tail) == 3 and head and head.isdigit():
            return None, (
                f'«{raw}» es ambiguo: puede ser {head}{tail} o {head}.{tail}. '
                f'Escribe el precio sin separador de miles.'
            )

    try:
        value = Decimal(text).quantize(Decimal('0.01'))
    except (InvalidOperation, ValueError):
        return None, 'El precio no es un número.'
    if value < 0:
        return None, 'El precio no puede ser negativo.'
    return value, None


def _gs1_check_digit(body: str) -> int:
    total = 0
    for index, char in enumerate(reversed(body)):
        total += int(char) * (3 if index % 2 == 0 else 1)
    return (10 - total % 10) % 10


def detect_symbology(code: str) -> str:
    """
    Name the barcode's symbology only when the code proves it.

    A length alone proves nothing — the owner's own file holds 692 twelve-digit
    codes of which only 67 have a valid UPC-A check digit, which is what chance
    produces. They are an internally generated sequence, not retail barcodes, and
    labelling them `upca` would put a false claim in the catalogue and invite
    somebody to print one.

    So: the check digit must agree. Otherwise `unknown`, which is honest and
    costs nothing — scanning resolves by code, never by symbology.
    """
    if not code.isdigit():
        return ProductBarcode.UNKNOWN
    if len(code) == 13 and _gs1_check_digit(code[:12]) == int(code[12]):
        return ProductBarcode.EAN13
    if len(code) == 12 and _gs1_check_digit(code[:11]) == int(code[11]):
        return ProductBarcode.UPCA
    if len(code) == 8 and _gs1_check_digit(code[:7]) == int(code[7]):
        return ProductBarcode.EAN8
    return ProductBarcode.UNKNOWN


def barcode_warnings(code: str, *, cell_was_numeric: bool) -> list[str]:
    """
    Say when a code may have lost a leading zero — and never restore one.

    Excel stores `CODIGO EAN` as a number, so `0750123456789` comes back as
    `750123456789`. The missing digit is part of what identifies the physical
    article. Reconstructing it would be inventing the identity of a product, so
    the import warns and imports what the file actually says.

    WHY THE CELL TYPE, AND NOT "IT LOOKS LIKE DIGITS"
    ------------------------------------------------
    This used to test a "looks like digits" regex and then tell the operator
    that "Excel stored it as a number". That was a claim about the FILE inferred
    from the VALUE, and it is wrong for the commonest good case: a column
    correctly formatted as text, holding `750123456789`, is all digits and lost
    nothing. Telling that person their file may be damaged sends them to
    re-check a shelf for no reason, and an alarm that cries wolf gets ignored on
    the day it is right.

    So the caller passes what the reader actually observed about the cell.
    """
    notes = []
    if cell_was_numeric and code.isdigit() and len(code) in (7, 11, 12):
        notes.append(
            f'«{code}» tiene {len(code)} dígitos y en el Excel la celda es '
            f'numérica. Si el código original empezaba por cero, Excel ya lo '
            f'había perdido antes de subir el archivo; esta plataforma NO lo '
            f'repone, porque adivinar un dígito cambia de qué producto se '
            f'trata. Verifícalo contra la etiqueta física.'
        )
    return notes


def unique_slug(company, name: str, *, taken: set[str], current: str = '') -> str:
    """
    A slug that is unique inside the COMPANY, not globally.

    Two tenants may both sell "Cable Lightning". Scoping to the company is what
    makes the catalogue multi-tenant; a global slug would make the second shop's
    product unnameable because the first got there first.

    `taken` carries slugs claimed earlier in this same import, which the database
    cannot know about yet — six hundred rows written in one transaction would
    otherwise collide with each other.
    """
    base = slugify(name)[:40] or 'producto'
    if current and current == base:
        return current
    candidate = base
    suffix = 2
    existing = set(
        Product.objects.filter(company=company, slug__startswith=base)
        .values_list('slug', flat=True)
    )
    if current:
        existing.discard(current)
    while candidate in existing or candidate in taken:
        candidate = f'{base}-{suffix}'
        suffix += 1
    taken.add(candidate)
    return candidate


# =============================================================================
# Matching
# =============================================================================

#: What `CatalogueIndex.match` answers for a name two products carry.
AMBIGUOUS_NAME = (
    'Hay más de un producto con este nombre; añade el código o '
    'el código de barras para identificarlo.'
)


class CatalogueIndex:
    """
    Everything needed to resolve six hundred rows without six hundred queries.

    Built once per job. §74: the naive implementation issues a `filter()` per
    row per identifier, which for the owner's file is several thousand queries
    and a preview that takes minutes.

    TWO DIFFERENT QUESTIONS ABOUT A BARCODE
    ---------------------------------------
    `ProductBarcode` is unique on `(company, code)` with NO condition on
    `is_active`, so a deactivated code still RESERVES that string. That makes
    two distinct questions:

      OWNERSHIP    "does anything in this company already hold this code?"
                   Answered over ALL barcodes, active or not. This is what an
                   importer needs: writing a code that an inactive row already
                   holds is an IntegrityError, and identifying the product a
                   retired label belongs to is correct.

      SCANNING     "will this code resolve at a till?"
                   Answered over ACTIVE barcodes only. That is the POS's
                   question, and it is not this class's.

    Loading only the active ones — which is what this did — made the preview
    blind to exactly the rows that would explode at apply time.

    IDENTITY IS CASE-SENSITIVE, DELIBERATELY
    ----------------------------------------
    `normalize_barcode()` trims and does nothing else, because a barcode is a
    string of symbols and Code128 carries mixed case that a scanner reproduces
    faithfully. The POS resolves a scan with an exact `code=` lookup. This index
    used to upper-case its keys, which meant the importer could merge two
    products the till keeps apart — one semantics of identity for scanning and a
    different one for importing, which is one too many.
    """

    def __init__(self, company):
        self.company = company
        # code -> product_id, for EVERY barcode of the company (ownership).
        self.by_barcode: dict[str, int] = {}
        # the subset that is deactivated, so the preview can say so.
        self.inactive_codes: set[str] = set()
        # LABEL-MIGRATION-01: folded -> the exact codes, to SAY when two differ only
        # in case. Identity stays exact; this only feeds a review.
        self.folded: dict[str, set[str]] = {}
        # MIGRATION-B: digits without their leading zeros -> the exact codes, to catch
        # a file where Excel stripped them from a code the catalogue already holds.
        self.unpadded: dict[str, set[str]] = {}
        self.primary: set[str] = set()
        for code, product_id, is_active, is_primary in ProductBarcode.objects.filter(
            company=company,
        ).values_list('code', 'product_id', 'is_active', 'is_primary'):
            key = normalize_barcode(code)
            self.by_barcode[key] = product_id
            self.folded.setdefault(key.casefold(), set()).add(key)
            if key.isdigit() and key.startswith('0'):
                self.unpadded.setdefault(key.lstrip('0'), set()).add(key)
            if is_primary:
                self.primary.add(key)
            if not is_active:
                self.inactive_codes.add(key)

        self.by_name = {}
        self.ambiguous_names = set()
        # Every product of THIS company: what an «Identificador» may name.
        self.product_ids: set[int] = set()
        for product_id, name in Product.objects.filter(
            company=company,
        ).values_list('pk', 'name'):
            self.product_ids.add(product_id)
            key = ' '.join(str(name).split()).lower()
            if key in self.by_name:
                # Two products with the same name cannot be told apart by name.
                self.ambiguous_names.add(key)
            self.by_name[key] = product_id

        self.categories = {
            ' '.join(str(n).split()).lower(): pk
            for pk, n in Category.objects.filter(company=company)
            .values_list('pk', 'name')
        }

    def owner_of(self, code: str):
        """Which product holds this exact code, active or not."""
        return self.by_barcode.get(normalize_barcode(code))

    def is_inactive(self, code: str) -> bool:
        return normalize_barcode(code) in self.inactive_codes

    def case_twins(self, code: str) -> set[str]:
        """The company's codes that differ from this one ONLY in case."""
        key = normalize_barcode(code)
        return self.folded.get(key.casefold(), set()) - {key}

    def identified(self, identifier: str):
        """
        The product of THIS company an «Identificador» names, or None.

        Another company's identifier, one whose product is not this company's
        and one that is nobody's are the same None: the caller says one thing
        for all three, and never whether the product exists somewhere else.
        """
        parsed = import_formats.parse_product_identifier(identifier)
        if parsed is None or parsed[0] != self.company.pk or parsed[1] not in self.product_ids:
            return None
        return parsed[1]

    def match(self, *, barcode='', code='', name='', identifier='', legacy_code=''):
        """
        Resolve a row to a product, or explain the ambiguity.

        Returns `(product_id_or_None, error_or_None, matched_by, warnings)`.

        IDENTITY FIRST (owner's decision, 2026-10-13). An «Identificador» that
        names a product of this company DECIDES: the name of the row is then
        just a value to write, so a renamed product is updated, never created
        again. One that names nothing here is ignored, with a warning, and the
        row falls back to its codes. When it and a code point at two different
        products the row is an error: nothing is guessed.

        WITHOUT IDENTIFIER AND WITHOUT A CODE THAT IS HELD, THE NAME — and only
        a name ONE product of the company carries. Two products with that name:
        an error that asks for a code.

        §30 — when two identifiers on the SAME row point at DIFFERENT products,
        that is an error and not a preference. Choosing one would either update
        the wrong article or create a duplicate of an article that already
        exists, and both are silent.
        """
        hits = {}
        warnings: list[str] = []
        named = None
        if identifier:
            named = self.identified(identifier)
            if named is None:
                warnings.append(
                    f'El identificador «{str(identifier).strip()}» no es de un producto de esta empresa: '
                    f'se ignora y la fila se identifica por sus códigos.'
                )
        for label, value in (('barcode', barcode), ('code', code), ('legacy_code', legacy_code)):
            if not value:
                continue
            found = self.owner_of(value)
            if found:
                hits[label] = found
                if self.is_inactive(value):
                    warnings.append(
                        f'El código «{normalize_barcode(value)}» está '
                        f'desactivado para escaneo, pero sigue asignado a este '
                        f'producto. Se usa para identificarlo y NO se reactiva.'
                    )

        distinct = set(hits.values())
        if named is not None:
            if distinct - {named}:
                return None, (
                    'El identificador y los códigos de esta fila apuntan a productos distintos. '
                    'Corrige uno de los dos: no se elige por ti.'
                ), '', warnings
            return named, None, 'identifier', warnings
        if len(distinct) > 1:
            return None, (
                'Los identificadores de esta fila apuntan a productos distintos.'
            ), '', warnings
        if distinct:
            return distinct.pop(), None, ','.join(sorted(hits)), warnings

        if name:
            key = ' '.join(name.split()).lower()
            if key in self.ambiguous_names:
                return None, AMBIGUOUS_NAME, '', warnings
            found = self.by_name.get(key)
            if found:
                return found, None, 'name', warnings
        return None, None, '', warnings


# =============================================================================
# LABEL-MIGRATION-01 — the historical mode
# =============================================================================
#
# The SAME product load, stricter about codes. Nothing here writes: it reads
# the row and says what is wrong with it, what must be reviewed and what each
# code is. ADR `docs/adr-inventory-labels.md` D12.

#: The categories a historical preview counts, in the order the screen shows them.
HISTORY_COUNTS = (
    'matched', 'new', 'codes_kept', 'codes_existing', 'duplicates', 'conflicts',
    'ambiguous', 'rejected', 'review',
)

#: MIGRATION-B — the shape of `summary['history']` and of each row's `history`, so a
#: later wizard can persist and reload a preview and know what it is reading.
HISTORY_VERSION = 1

#: One bucket per row, first that applies: what the operator must look at first.
HISTORY_CLASSES = (
    'conflict', 'duplicate_code', 'ambiguous', 'insufficient', 'rejected', 'inactive', 'needs_review',
    'exact', 'secondary_code', 'model_new_variant', 'new_model', 'new',
)


def _quantity_branches(company, quantity_columns, wanted) -> dict:
    """`{column index: Branch}` for the old-quantity columns the person assigned. One branch per column."""
    from .models import Branch

    if not wanted:
        return {}
    try:
        pairs = {int(column): int(branch) for column, branch in dict(wanted).items()}
    except (TypeError, ValueError):
        raise ImportError_('Sucursal inválida para una columna de cantidades.') from None
    columns = {column for column, _header in quantity_columns}
    stray = sorted(set(pairs) - columns)
    if stray:
        raise ImportError_(f'La columna {stray[0] + 1} no es una columna de cantidades de este archivo.')
    if len(set(pairs.values())) != len(pairs):
        raise ImportError_('Dos columnas de cantidades no pueden ser de la misma sucursal.')
    branches = {b.pk: b for b in Branch.objects.filter(company=company, pk__in=set(pairs.values()))}
    if set(branches) != set(pairs.values()):
        # The view already answered 404 for another company's branch; this is the net.
        raise ImportError_('Sucursal inválida para una columna de cantidades.')
    return {column: branches[branch] for column, branch in pairs.items()}


def _history_match(product_id, matched_by, entries, index, model_class) -> str:
    """How the row reached its product: by identifier or its primary code, a secondary code, a name, or none."""
    if product_id:
        if matched_by == 'identifier':
            return 'exact'
        if matched_by == 'name':
            return 'name_only'
        held = [entry['code'] for entry in entries if entry['status'] == 'existing']
        return 'exact' if any(code in index.primary for code in held) else 'secondary_code'
    return model_class or 'new'


def _history_classification(flags, match, *, inactive) -> str:
    for flag, bucket in (('conflict', 'conflict'), ('duplicate', 'duplicate_code'), ('ambiguous', 'ambiguous'),
                         ('insufficient', 'insufficient'), ('rejected', 'rejected')):
        if flag in flags:
            return bucket
    if inactive:
        return 'inactive'
    if 'review' in flags or match == 'name_only':
        return 'needs_review'
    return match


#: What Excel writes for a number too long for its cell: `7.50123E+12`, `75E+3`.
#: A decimal separator or an explicit sign is required, so an alphanumeric code
#: such as `3E10` is not mistaken for one.
_SCIENTIFIC = re.compile(r'^\d+[.,]\d+[eE][+-]?\d+$|^\d+[eE][+-]\d+$')

#: Digits a spreadsheet number keeps exactly. Past 15, Excel has already rounded.
_EXACT_DIGITS = 15

_CODE_LABELS = {'barcode': 'código de barras', 'code': 'código interno', 'legacy_code': 'SKU antiguo'}


def history_code_damage(code: str, *, cell_was_numeric: bool) -> tuple[str, str]:
    """
    `(rejection, review)` for a code the file carries; '' when there is none.

    A code is text. When the CELL was a number, Excel decided what the code is:
    a leading zero may be gone, so ANY numeric cell is reviewed — not only the
    7, 11 or 12 digits `barcode_warnings` looks at. With a decimal part, in
    scientific notation or longer than 15 digits, digits are already lost and
    the row is rejected. Nothing is ever reconstructed.
    """
    if _SCIENTIFIC.match(code):
        return (f'«{code}» está en notación científica: Excel ya cambió el código. Escribe la columna '
                f'como texto y vuelve a exportar el archivo.'), ''
    if not cell_was_numeric:
        return '', ''
    if not code.isdigit():
        return (f'«{code}» viene de una celda numérica, de fecha o de hora (con decimales, signo o una '
                f'fecha): Excel ya cambió el código. Escribe la columna como texto.'), ''
    if len(code) > _EXACT_DIGITS:
        return (f'«{code}» tiene {len(code)} dígitos en una celda numérica: Excel sólo conserva '
                f'{_EXACT_DIGITS} y ya perdió el resto. Escribe la columna como texto.'), ''
    return '', (f'«{code}» viene de una celda numérica: si empezaba por cero, Excel ya lo perdió. '
                f'No se repone ningún dígito; compruébalo contra la etiqueta.')


def unit_identifiers(company) -> set[str]:
    """
    Every IMEI, second IMEI and serial of the company's devices.

    D1.3: a device identifier is never a PRODUCT code — the till looks for
    products first, so that code would take the scan away from the device.
    The same rule `product_codes.ensure_code` (LABEL-01) applies.
    """
    from .models import StockUnit

    found = set()
    for imei, imei2, serial in StockUnit.objects.filter(company=company).values_list(
        'imei', 'imei2', 'serial_number',
    ):
        found.update(value for value in (imei, imei2) if value)
        if serial:
            # Serials are stored upper-cased (`device_identity`): compared that way,
            # exactly as `product_codes.is_unit_identifier` does.
            found.add(serial.upper())
    return found


class _History:
    """What one historical preview has seen, across rows. Read-only on the catalogue."""

    def __init__(self, company, index):
        self.index = index
        self.units = unit_identifiers(company)
        self.seen_folded: dict[str, tuple[str, int]] = {}
        self.seen_external: dict[str, int] = {}
        self.seen_new_names: dict[str, int] = {}
        self.counts = dict.fromkeys((*HISTORY_COUNTS, 'without_code'), 0)
        self.classes: dict[str, int] = {}

    def codes(self, row_number, candidates, *, numeric_fields, product_id):
        """
        Check the codes this row WRITES. Returns `(errors, reviews, flags, entries)`.

        `candidates` is `[(field, code, symbology)]`. Each entry says what the
        code is and whether it is new (`kept`) or already this product's
        (`existing`). A code another product holds is reported by the caller.
        """
        errors, reviews, flags, entries = [], [], set(), []
        for field, code, symbology in candidates:
            label = _CODE_LABELS[field]
            rejection, review = history_code_damage(code, cell_was_numeric=field in numeric_fields)
            if rejection:
                errors.append(f'{label.capitalize()}: {rejection}')
                flags.add('rejected')
            if review:
                reviews.append(f'{label.capitalize()}: {review}')
            key = normalize_barcode(code)
            if key in self.units or key.upper() in self.units:
                errors.append(f'El {label} «{key}» es el IMEI o la serie de un equipo: identifica una unidad, '
                              f'no un producto.')
                flags.add('conflict')
            # Only a code nobody holds EXACTLY, and only against a code of ANOTHER product:
            # an exact code is its owner's, and two codes of one product that differ in
            # zeros are that product's (P1 of the #170 review).
            padded = set()
            if key.isdigit() and self.index.owner_of(key) is None:
                padded = {
                    held for held in self.index.unpadded.get(key.lstrip('0'), set()) - {key}
                    if self.index.owner_of(held) != product_id
                }
            if padded:
                errors.append(f'El {label} «{key}» parece «{sorted(padded)[0]}», que ya existe, sin sus ceros a la '
                              f'izquierda: Excel los quitó. No se reconstruye ni se crea otro producto: escribe la '
                              f'columna como texto con el código completo.')
                flags.add('ambiguous')
            twins = self.index.case_twins(key)
            if twins:
                reviews.append(f'El {label} «{key}» difiere sólo en mayúsculas de «{sorted(twins)[0]}», que ya '
                               f'existe: el lector o la impresora pueden confundirlos.')
            earlier = self.seen_folded.get(key.casefold())
            if earlier and earlier[0] != key:
                reviews.append(f'El {label} «{key}» difiere sólo en mayúsculas de «{earlier[0]}» (fila {earlier[1]}).')
            elif not earlier:
                self.seen_folded[key.casefold()] = (key, row_number)
            owner = self.index.owner_of(key)
            entries.append({
                'field': field, 'code': key, 'symbology': symbology,
                'status': 'existing' if owner is not None and owner == product_id else 'kept',
            })
        return errors, reviews, flags, entries

    def external(self, row_number, external_id):
        if not external_id:
            return ''
        earlier = self.seen_external.get(external_id)
        if earlier:
            return f'El id externo «{external_id}» ya aparece en la fila {earlier}.'
        self.seen_external[external_id] = row_number
        return ''

    def new_name(self, row_number, name):
        """Two NEW products with one name: one product with several codes goes in ONE row."""
        key = ' '.join(name.split()).lower()
        earlier = self.seen_new_names.get(key)
        if earlier:
            return (f'Otra fila nueva ({earlier}) ya se llama «{name}». Un producto con varios códigos va en '
                    f'UNA fila (código de barras, código interno y SKU antiguo): no se crean dos.')
        self.seen_new_names[key] = row_number
        return ''


def _history_candidates(barcode, code, legacy_code, options):
    """`[(field, code, symbology)]`, the codes a historical row writes: what `_barcodes_for` will write."""
    out = []
    if barcode:
        out.append(('barcode', barcode, detect_symbology(barcode)))
    if code and options['code_as_barcode'] and normalize_barcode(code) != normalize_barcode(barcode):
        out.append(('code', code, ProductBarcode.INTERNAL))
    if legacy_code and options.get('legacy_code_as_barcode') and normalize_barcode(legacy_code) not in {
        normalize_barcode(barcode), normalize_barcode(code),
    }:
        # Named by its check digit, as `product_codes` names it at apply: the preview
        # shows what will be stored.
        out.append(('legacy_code', legacy_code, detect_symbology(legacy_code)))
    return out


def _history_would_change(normalized, state, *, new_codes, row_images) -> bool:
    """
    Whether applying this row would change the product it matched.

    False makes it `no_change`: a historical file uploaded twice must say, the
    second time, that it changes nothing. When in doubt, True — a row
    reported as an update that turns out to change nothing is harmless.
    """
    if state is None or new_codes or row_images:
        return True
    variant = normalized.get('variant')
    if variant:
        from . import device_catalog

        if (variant['model_id'], variant['condition_line'], device_catalog.variant_key(variant['option_ids'])) != (
            state['product_model_id'], state['condition_line'], state['variant_key']
        ):
            return True
    price = normalized.get('price')
    if price and Decimal(price) != state['price']:
        return True
    if normalized.get('category') and normalized.get('category_id') is None:
        return True  # a category to create
    category_id = normalized.get('category_id')
    if category_id and category_id != state['category_id'] and not (
        normalized.get('category_default') and state['category_id']
    ):
        return True
    for field in ('description', 'image_url'):
        if normalized.get(field) and normalized[field] != state[field]:
            return True
    destination = normalized.get('destination')
    if destination in (import_formats.DESTINATION_ONLINE, import_formats.DESTINATION_INTERNAL) and (
        (destination == import_formats.DESTINATION_ONLINE) != state['is_published_online']
    ):
        return True
    cost = normalized.get('reference_cost')
    if cost and Decimal(cost) != state['reference_cost']:
        return True
    if not normalized.get('keep_name') and normalized.get('name') != state['name']:
        return True
    return False


# =============================================================================
# Preview
# =============================================================================

#: DC1 review — what a caller without `costs.view` reads on a row that brings
#: a «Costo de referencia»: the same sentence whatever the cell says, so no
#: value can be compared against the stored one.
COST_COLUMN_IGNORED = 'Columna «Costo de referencia» ignorada: requiere permiso de costos.'

DEFAULT_OPTIONS = {
    'mode': 'upsert',                    # or 'create_only'
    'create_missing_categories': False,  # §33 — default FALSE, deliberately
    'blank_means': 'skip_field',         # §37
    'code_as_barcode': True,             # §27
    # PRODUCT-DESTINATION-01: the category for rows that name none. One of the
    # company's own, checked before anything is staged. None = leave them without.
    'default_category_id': None,
    # LABEL-MIGRATION-01 — the «base histórica» mode. Off by default; the
    # historical template's own columns switch it on (`import_formats.HISTORY_PRESET`).
    'history_mode': False,
    # …and, inside it, whether the old SKU becomes one more scannable code
    # (`unknown`). Off by default: whether it must scan is the owner's call (P12).
    'legacy_code_as_barcode': False,
}


def _default_category(company, raw):
    """
    The category chosen on the screen for rows without one, or None.

    Checked against THIS company's categories. Another tenant's id, a number
    that is nobody's and a value that is not a number are one error, worded the
    same: the answer must not say that a category exists somewhere else.
    """
    if raw in (None, '', 0):
        return None
    try:
        if isinstance(raw, (bool, float)) and raw != int(raw):
            raise ValueError
        category_id = int(raw)
        if isinstance(raw, bool) or not 0 < category_id < 2 ** 31:
            raise ValueError
    except (TypeError, ValueError, OverflowError):
        category_id = None
    # Active, like the ones the template and the screen offer.
    category = (
        Category.objects.filter(company=company, pk=category_id, is_active=True).first() if category_id else None
    )
    if category is None:
        raise ImportError_('La categoría elegida para las filas sin categoría no existe en esta empresa.')
    return category


def _row_values(values, mapping, headers):
    out = {}
    formulas = []
    for field, index in mapping.items():
        raw = values.get(index)
        if raw is xlsx_reader.FORMULA:
            formulas.append(headers[index] if index < len(headers) else str(index))
            continue
        out[field] = '' if raw is None else str(raw).strip()
    return out, formulas


def _drop_foreign_variant_columns(company, headers, mapping) -> str:
    """
    DC2 review — the variant columns are read only when the file looks like
    the DC2 template (it has «Línea» or «Capacidad») AND the company has at
    least one device model. Otherwise they are dropped from `mapping`, and
    the one notice for the whole file is returned ('' = nothing dropped).
    """
    fields = [field for field in import_formats.VARIANT_HEADERS if field in mapping]
    if not fields:
        return ''
    from .models import ProductModel

    marker = import_formats.find_column(
        headers, (*import_formats.VARIANT_HEADERS['line'], *import_formats.VARIANT_HEADERS['capacity']))
    if marker is not None and ProductModel.objects.filter(company=company).exists():
        return ''
    names = ', '.join(f'«{str(headers[mapping[field]]).strip()}»' for field in fields)
    for field in fields:
        del mapping[field]
    return (f'Las columnas {names} no se leen como modelo y opciones de equipo: el archivo no tiene «Línea» '
            'ni «Capacidad», o la empresa no tiene modelos de equipo. El resto del archivo se carga como siempre.')


def preview_products(*, company, actor, upload, filename, sheet_name=None,
                     header_row=None, mapping=None, options=None, images=None, quantity_branches=None):
    """
    Parse, resolve and stage a product workbook. Writes NOTHING commercial.

    `images` is the `import_media.Bundle` of picture files that came with the
    workbook, or None. The pictures some row will use are stored now, as
    unplaced images of `company`, so that apply finds them; nothing is placed
    in any gallery until apply.

    `quantity_branches` (historical mode): `{column index: branch id}`, which
    branch each old-quantity column speaks of, chosen by the person — never
    read from a number in the header. The caller has checked the branches are
    this company's and within the actor's reach. Reference only: nothing moves.
    """
    stager = import_media.Stager(company=company, actor=actor, bundle=images)
    try:
        return _preview_products(
            company=company, actor=actor, upload=upload, filename=filename,
            sheet_name=sheet_name, header_row=header_row, mapping=mapping,
            options=options, stager=stager, quantity_branches=quantity_branches,
        )
    except BaseException:
        # The transaction took the rows back; the files it had already written
        # to storage are still there, with nothing pointing at them.
        stager.abandon()
        raise


class _GalleryIndex:
    """What each product already shows, read once per product."""

    def __init__(self, company):
        self.company = company
        self._cache: dict[int, dict] = {}

    def of(self, product_id) -> dict:
        if not product_id:
            return {'count': 0, 'hashes': set()}
        if product_id not in self._cache:
            rows = list(ProductImage.objects.filter(
                company=self.company, product_id=product_id,
            ).values_list('source_sha256', flat=True))
            self._cache[product_id] = {'count': len(rows), 'hashes': {h for h in rows if h}}
        return self._cache[product_id]


@transaction.atomic
def _preview_products(*, company, actor, upload, filename, sheet_name, header_row,
                      mapping, options, stager, quantity_branches=None):
    options = {**DEFAULT_OPTIONS, **(options or {})}
    # Decided here, from who is asking — never from the request's options.
    from .cost_services import user_may_view_costs

    costs_allowed = user_may_view_costs(actor, company)
    # Before the file is even opened: an option that names another company's
    # category refuses the whole request, and leaves no job behind.
    default_category = _default_category(company, options.get('default_category_id'))
    options['default_category_id'] = default_category.pk if default_category else None
    data = xlsx_reader.check_upload(upload, filename=filename)
    sha256 = _sha256(data)
    workbook, reader_notes = xlsx_reader.load_workbook(data)

    sheet_name = sheet_name or workbook.sheetnames[0]
    refusal = import_exports.report_sheet_refusal(sheet_name)
    if refusal:
        # INVENTORY-UNIFIED-01: a report is read, not loaded. Before anything is staged.
        raise ImportError_(refusal)
    if import_exports.is_help_sheet(sheet_name):
        # The «Instrucciones» and «Ejemplo» of the previous template. Refused before anything
        # is staged: an example must not become catalogue because somebody
        # picked the wrong tab.
        raise ImportError_(
            f'«{sheet_name}» es una hoja de ayuda de la plantilla y no se importa. '
            'Los productos van en la hoja «Productos».'
        )
    detected = None
    if mapping is None or header_row is None:
        # SAMPLE: format detection only needs the header row. Reading the whole
        # file here would also mean a too-large file was refused from a probe
        # rather than from the real read, with a confusing message.
        probe_headers, _rows, _t = xlsx_reader.read_rows(
            workbook, sheet_name, header_row=header_row or 1,
            limit=xlsx_reader.SAMPLE_ROWS, mode=xlsx_reader.SAMPLE,
        )
        detected = import_formats.detect(
            BulkImportJob.PRODUCTS, sheet_name, probe_headers,
        )
        if detected is None and header_row is None:
            # The banner-row layout: try row 2 before giving up, because the
            # owner's own template puts its headers there.
            probe2, _r2, _t2 = xlsx_reader.read_rows(
                workbook, sheet_name, header_row=2,
                limit=xlsx_reader.SAMPLE_ROWS, mode=xlsx_reader.SAMPLE,
            )
            detected = import_formats.detect(BulkImportJob.PRODUCTS, sheet_name, probe2)
        if detected:
            header_row = header_row or detected['header_row']
            mapping = mapping or detected['mapping']

    header_row = header_row or 1
    if not mapping:
        raise ImportError_(
            'No se reconoció el formato del archivo. Asigna las columnas a mano.'
        )
    mapping = {k: int(v) for k, v in mapping.items() if k in import_formats.PRODUCT_FIELDS}
    # LABEL-MIGRATION-01. Strictly a boolean from the request, or the historical
    # template recognised by its own columns. Outside the mode its columns are not read.
    history = options.get('history_mode') is True or (detected or {}).get('preset') == import_formats.HISTORY_PRESET
    options['history_mode'] = history
    options['legacy_code_as_barcode'] = history and options.get('legacy_code_as_barcode') is True
    if not history:
        mapping = import_formats.history_fields_outside_the_mode(mapping)
    if 'name' not in mapping:
        raise ImportError_('Falta asignar la columna del nombre del producto.')

    # FULL_IMPORT, and deliberately BEFORE the job is created: a file that is
    # too long must leave nothing behind, not a job somebody could later apply.
    headers, rows, _truncated = xlsx_reader.read_rows(
        workbook, sheet_name, header_row=header_row, mode=xlsx_reader.FULL_IMPORT,
    )

    # PRODUCT-DESTINATION-01. A «Destino» column is never left unread because
    # the mapping that arrived does not know it: see `find_destination_column`.
    destination_note = ''
    if 'destination' not in mapping:
        found = import_formats.find_destination_column(headers, taken=mapping.values())
        if found is not None:
            mapping['destination'] = found
            destination_note = (
                f'La columna «{str(headers[found]).strip()}» se lee como el destino de cada producto '
                f'(e-commerce o sólo stock interno).'
            )

    # DC1 — the same for the reference cost: a remembered mapping predates it.
    if 'reference_cost' not in mapping:
        found = import_formats.find_column(
            headers, import_formats.REFERENCE_COST_HEADERS, taken=mapping.values())
        if found is not None:
            mapping['reference_cost'] = found
    # DC2 — and for the variant columns.
    for field, wanted in import_formats.VARIANT_HEADERS.items():
        if field not in mapping:
            found = import_formats.find_column(headers, wanted, taken=mapping.values())
            if found is not None:
                mapping[field] = found
    # …but only for a file that IS the DC2 template, in a company that has
    # device models. A product file from before DC2 often has a «Modelo»
    # (a part number) or a «Color» column: read as variants, every row failed.
    # The variant texts as the file says them, for the report, even when the
    # columns are not read as the device catalogue (`_drop_foreign_variant_columns`).
    history_raw_columns = {
        field: mapping[field] for field in ('model', 'capacity', 'colour', 'line') if field in mapping
    } if history else {}
    variant_note = _drop_foreign_variant_columns(company, headers, mapping)
    # MIGRATION-B — the old quantities: REFERENCE ONLY, never stock.
    quantity_columns = import_formats.old_quantity_columns(headers, taken=mapping.values()) if history else []
    column_branches = _quantity_branches(company, quantity_columns, quantity_branches) if history else {}

    job = BulkImportJob.objects.create(
        company=company, import_type=BulkImportJob.PRODUCTS,
        original_filename=_safe_filename(filename),
        file_sha256=sha256,
        mapping_snapshot={
            'sheet_name': sheet_name, 'header_row': header_row,
            'mapping': mapping, 'headers': [str(h) for h in headers],
            'preset': (detected or {}).get('preset', ''),
            # MIGRATION-B: kept so a later wizard reloads the same choice.
            **({'quantity_branches': {str(c): b.pk for c, b in column_branches.items()}} if history else {}),
        },
        options_snapshot=options,
        created_by=actor,
    )

    index = CatalogueIndex(company)
    # Products that already have a category: the one chosen on the screen for
    # «rows without one» fills a gap and does not move these.
    already_filed = set(
        Product.objects.filter(company=company, category__isnull=False).values_list('pk', flat=True)
    ) if default_category is not None else set()
    galleries = _GalleryIndex(company)
    # INVENTORY-UNIFIED-01 — where each existing product stands, read once, to
    # say BEFORE apply which rows move a product onto the web or off it.
    standing = {
        state['pk']: state for state in Product.objects.filter(company=company).values(
            'pk', 'is_published_online', 'is_active', 'price', 'image_url', 'category_id',
            'reference_cost', 'name', 'description', 'product_model_id', 'condition_line', 'variant_key',
        )
    }
    # ONE rule for the bulk action and for this load (`product_publication`).
    require_image = product_publication.requires_image(company)
    to_web = to_internal = publication_refused = 0
    already_present = 0
    staged = []
    counts = dict(create=0, update=0, no_change=0, skip=0, error=0)
    seen_barcodes: dict[str, int] = {}
    seen_names: dict[str, int] = {}
    seen_identified: dict[int, int] = {}
    seen_variants: dict[tuple, int] = {}
    ledger = _History(company, index) if history else None
    review_rows = 0
    # LABEL-01: a NEW product without a code gets one at apply, from the company's
    # «Código de producto» series. Switched off, that would fail at apply: said here.
    from .models import InternalSequence

    code_series_off = InternalSequence.objects.filter(
        company=company, branch=None, document_type=InternalSequence.DOCUMENT_PRODUCT_CODE, is_active=False,
    ).exists()

    for row_number, values, numeric_columns in rows:
        if not values:
            staged.append(BulkImportRow(
                job=job, sheet_name=sheet_name, row_number=row_number,
                action=BulkImportRow.SKIP, normalized_data={},
                warnings=['Fila vacía.'],
            ))
            counts['skip'] += 1
            continue

        fields, formulas = _row_values(values, mapping, headers)
        # Only the row right under the headers: that is where a template has it.
        if row_number == header_row + 1 and import_exports.is_template_help_row(fields):
            staged.append(BulkImportRow(
                job=job, sheet_name=sheet_name, row_number=row_number,
                action=BulkImportRow.SKIP, normalized_data={},
                warnings=['Fila de ayuda de la plantilla.'],
            ))
            counts['skip'] += 1
            continue
        if import_exports.is_template_example_row(fields):
            staged.append(BulkImportRow(
                job=job, sheet_name=sheet_name, row_number=row_number,
                action=BulkImportRow.SKIP, normalized_data={},
                warnings=['Fila de ejemplo de la plantilla: no se importa. Reemplázala por tus productos.'],
            ))
            counts['skip'] += 1
            continue
        errors, warnings = [], []
        # LABEL-MIGRATION-01 — what kind of trouble this row is in, for the count.
        flags: set[str] = set()
        if formulas:
            errors.append(
                f'Las columnas {", ".join(formulas)} contienen fórmulas. '
                f'Conviértelas a valor antes de importar.'
            )

        name = fields.get('name', '').strip()
        code = fields.get('code', '').strip()
        barcode = fields.get('barcode', '').strip()
        legacy_code = fields.get('legacy_code', '').strip() if history else ''
        legacy_as_code = legacy_code if options['legacy_code_as_barcode'] else ''

        if not name:
            errors.append('Falta el nombre del producto.')
            flags.add('insufficient')

        price = None
        if 'price' in mapping:
            price, price_error = clean_price(fields.get('price'))
            if price_error:
                errors.append(price_error)

        # DC1 — the catalogue's reference cost. Blank changes nothing.
        reference_cost = None
        if 'reference_cost' in mapping and not costs_allowed:
            # Not read at all: neither its value nor whether it is valid.
            if str(fields.get('reference_cost') or '').strip():
                warnings.append(COST_COLUMN_IGNORED)
        elif 'reference_cost' in mapping:
            reference_cost, cost_error = clean_price(fields.get('reference_cost'))
            if cost_error:
                errors.append('Costo de referencia: ' + cost_error.replace('El precio', 'el costo'))

        if barcode:
            warnings.extend(barcode_warnings(
                barcode,
                cell_was_numeric=mapping.get('barcode') in numeric_columns,
            ))
            shape = _barcode_shape_errors(barcode, 'código de barras')
            errors.extend(shape)
            flags.update(['rejected'] if shape else [])
            previous = seen_barcodes.get(normalize_barcode(barcode))
            if previous:
                errors.append(
                    f'El código de barras «{barcode}» ya aparece en la fila {previous}.'
                )
                flags.add('duplicate')
            else:
                seen_barcodes[normalize_barcode(barcode)] = row_number
        if code and options['code_as_barcode']:
            shape = _barcode_shape_errors(code, 'código interno')
            errors.extend(shape)
            flags.update(['rejected'] if shape else [])
            previous = seen_barcodes.get(normalize_barcode(code))
            if previous and previous != row_number:
                errors.append(f'El código «{code}» ya aparece en la fila {previous}.')
                flags.add('duplicate')
            else:
                seen_barcodes[normalize_barcode(code)] = row_number
        if legacy_as_code:
            # D12: an old SKU that is not a valid code goes to the report, never errors the row.
            shape = _barcode_shape_errors(legacy_as_code, 'SKU antiguo')
            damage, _review = history_code_damage(
                legacy_as_code, cell_was_numeric=mapping.get('legacy_code') in numeric_columns)
            if shape or damage:
                warnings.append(f'SKU antiguo «{legacy_as_code}»: no se guarda como código y queda sólo en el '
                                f'informe. {(shape or [damage])[0]}')
                legacy_as_code = ''
        if legacy_as_code:
            previous = seen_barcodes.get(normalize_barcode(legacy_as_code))
            if previous and previous != row_number:
                errors.append(f'El SKU antiguo «{legacy_as_code}» ya aparece en la fila {previous}.')
                flags.add('duplicate')
            else:
                seen_barcodes[normalize_barcode(legacy_as_code)] = row_number

        category_id = None
        category_name = fields.get('category', '').strip()
        if category_name:
            key = ' '.join(category_name.split()).lower()
            category_id = index.categories.get(key)
            if category_id is None and not options['create_missing_categories']:
                errors.append(
                    f'La categoría «{category_name}» no existe. Créala primero o '
                    f'activa «crear categorías que falten».'
                )

        identifier = fields.get('identifier', '').strip()
        product_id, match_error, matched_by, match_warnings = index.match(
            barcode=barcode, code=code, name=name, identifier=identifier, legacy_code=legacy_as_code,
        )
        warnings.extend(match_warnings)
        if match_error:
            errors.append(match_error)
            flags.add('ambiguous' if match_error == AMBIGUOUS_NAME else 'conflict')
        if matched_by == 'identifier':
            previous = seen_identified.get(product_id)
            if previous:
                errors.append(f'El identificador «{identifier}» ya aparece en la fila {previous}.')
            else:
                seen_identified[product_id] = row_number

        # The category chosen on the screen, for a row that names none. Marked
        # as such: on a product that already has a category it changes nothing.
        # What the preview says is what apply will do: a product that already
        # has a category keeps it, so the row does not claim the chosen one.
        category_is_default = False
        if not category_name and default_category is not None and product_id not in already_filed:
            category_id = default_category.pk
            category_is_default = True

        # PRODUCT-DESTINATION-01. An empty cell decides nothing: what exists
        # stays where it is. And it publishes nothing (owner's decision,
        # 2026-10-13): a NEW product with no destination is born «solo stock
        # interno» — going onto the web is always something a row SAYS.
        destination = import_formats.parse_destination(fields.get('destination', ''))
        if destination is None:
            labels = import_formats.DESTINATION_LABELS
            errors.append(
                f'«{fields.get("destination", "").strip()}» no es un destino. Escribe '
                f'«{labels[import_formats.DESTINATION_ONLINE]}» o '
                f'«{labels[import_formats.DESTINATION_INTERNAL]}», o deja la celda vacía.'
            )
            destination = ''
        elif not destination and not product_id:
            destination = import_formats.DESTINATION_INTERNAL

        # «Estado», the column of a file to reload. It decides how a NEW product
        # is born and nothing else: see below, where the product is known.
        wants_active = import_formats.parse_state(fields.get('is_active', ''))
        if wants_active is None:
            # Unreadable. On a NEW product it would decide how it is born: an
            # error. On one that exists it is ignored anyway — a load never
            # activates or deactivates — so it is said, and the row applies.
            labels = import_formats.STATE_LABELS
            said = f'«{fields.get("is_active", "").strip()}» no es un estado'
            if product_id:
                warnings.append(f'{said}: se ignora, porque esta carga no activa ni desactiva productos.')
            else:
                errors.append(
                    f'{said}. Escribe «{labels[True]}» o «{labels[False]}» (o Sí / No), '
                    f'o deja la celda vacía.'
                )
            wants_active = ''

        # §18 — a code this row would WRITE that another product already holds.
        #
        # Ownership matching normally resolves this first: if a code is held,
        # `match()` returns its owner, and two identifiers held by two different
        # products come back as a conflict. This is the net under that, for the
        # case where the row resolved to one product while a code it also
        # carries belongs to another. The database would refuse the write —
        # unique on (company, code), with no exception for deactivated rows — so
        # without this the failure arrives as an IntegrityError from the middle
        # of apply, after the preview said the file was clean.
        if not match_error:
            for candidate in (barcode, code, legacy_as_code):
                if not candidate:
                    continue
                owner = index.owner_of(candidate)
                if owner is not None and owner != product_id:
                    errors.append(
                        f'El código «{normalize_barcode(candidate)}» ya '
                        f'pertenece a otro producto de esta empresa.'
                    )
                    flags.add('conflict')

        if not errors and product_id is None and not (barcode or code):
            # §31 — with no stable identifier, only creation is defensible.
            # An UPDATE matched on name alone would rewrite the wrong article
            # the first time two products are named similarly.
            key = ' '.join(name.split()).lower()
            previous = seen_names.get(key)
            if previous:
                errors.append(f'El nombre «{name}» ya aparece en la fila {previous}.')
                flags.add('ambiguous')
            else:
                seen_names[key] = row_number

        # LABEL-MIGRATION-01 — the codes as TEXT, the device identifiers, the
        # case twins, the reconciliation id and the rename that is never silent.
        reviews: list[str] = []
        history_data = None
        history_cost_value = None
        if history:
            numeric_fields = {field for field, column in mapping.items() if column in numeric_columns}
            code_errors, reviews, code_flags, entries = ledger.codes(
                row_number, _history_candidates(barcode, code, legacy_as_code, options),
                numeric_fields=numeric_fields, product_id=product_id,
            )
            errors.extend(code_errors)
            flags |= code_flags
            if legacy_code and not legacy_as_code:
                # Only in the report: say it when Excel touched it anyway.
                _rejected, review = history_code_damage(legacy_code, cell_was_numeric='legacy_code' in numeric_fields)
                if _rejected or review:
                    warnings.append(f'SKU antiguo (sólo informe): {_rejected or review}')
            external_id = fields.get('external_id', '').strip()
            repeated = ledger.external(row_number, external_id)
            if repeated:
                errors.append(repeated)
                flags.add('duplicate')
            if product_id is None and name and not match_error:
                twin = ledger.new_name(row_number, name)
                if twin:
                    errors.append(twin)
                    flags.add('ambiguous')
            catalogue_name = (standing.get(product_id) or {}).get('name', '') if product_id else ''
            keep_name = bool(
                product_id and matched_by not in ('identifier', 'name') and name and name != catalogue_name
            )
            if product_id and matched_by == 'name' and entries:
                # A code reaching a product by its NAME alone would be a merge by name (#170 review).
                errors.append(
                    f'Reconocida sólo por su nombre, «{name}», y trae códigos nuevos: no se le añaden a un '
                    f'producto por su nombre. Usa el «Identificador» de un archivo descargado, o un código que '
                    f'ya tenga.'
                )
                flags.add('conflict')
            elif product_id and matched_by == 'name':
                reviews.append(
                    f'Reconocida sólo por su nombre, «{name}»: ningún código la identifica. Confirma que es ese '
                    f'producto; no se une nada por un nombre parecido.'
                )
            if product_id and (standing.get(product_id) or {}).get('is_active') is False:
                reviews.append('El producto del catálogo está INACTIVO: la carga no lo reactiva. Confirma que es él.')
            if keep_name:
                reviews.append(
                    f'Reconocida por su código, pero el archivo la llama «{name}» y el catálogo «{catalogue_name}». '
                    f'Se conserva el nombre del catálogo: confirma que es el mismo producto.'
                )
            raw = {}
            for field, column in history_raw_columns.items():
                value = values.get(column)
                raw[field] = '' if value is None or value is xlsx_reader.FORMULA else str(value).strip()
            old_quantities, reference_quantities = [], []
            for column, header in quantity_columns:
                value = values.get(column)
                if value is xlsx_reader.FORMULA:
                    warnings.append(f'«{header}» es una fórmula: la cantidad anterior no se guarda.')
                    continue
                text = '' if value is None else str(value).strip()
                if not text:
                    continue  # blank = not provided; never a zero
                if not re.fullmatch(r'\d+(?:[.,]\d+)?', text):
                    warnings.append(f'«{header}»: «{text}» no es una cantidad; se guarda tal cual como referencia.')
                old_quantities.append({'column': header, 'value': text})
                branch = column_branches.get(column)
                if branch is None:
                    continue
                if re.fullmatch(r'\d+', text) and len(text) <= 9:
                    reference_quantities.append({'branch_id': branch.pk, 'quantity': int(text)})
                else:
                    warnings.append(f'«{header}»: «{text}» no es un entero mayor o igual a cero; no queda como '
                                    f'referencia de {branch.name}.')
            if 'historical_cost' in mapping and str(fields.get('historical_cost') or '').strip():
                if not costs_allowed:
                    warnings.append('Columna «Costo histórico» ignorada: requiere permiso de costos.')
                else:
                    historical_cost, cost_error = clean_price(fields.get('historical_cost'))
                    if cost_error:
                        warnings.append('Costo histórico (sólo informe): ' + cost_error.replace('El precio', 'el costo'))
                    else:
                        history_cost_value = str(historical_cost)
            history_data = {
                'version': HISTORY_VERSION,
                'codes': entries, 'legacy_code': legacy_code, 'external_id': external_id,
                'source_reference': fields.get('source_reference', '').strip(),
                'brand': fields.get('brand', '').strip(), 'model': raw.get('model', ''),
                'capacity': raw.get('capacity', ''), 'colour': raw.get('colour', ''), 'line': raw.get('line', ''),
                'sim': fields.get('sim', '').strip(), 'region': fields.get('region', '').strip(),
                'variant': fields.get('variant', '').strip(),
                # Reference for a later count. NEVER applied, never offered as counted.
                'old_quantities': old_quantities,
                # The contract with the count (docs/adr-migration-count.md §8): per branch, an
                # integer >= 0, absent when not provided. Reference only.
                'reference_quantities': reference_quantities,
                'catalogue_name': catalogue_name, 'keep_name': keep_name,
                'matched_by': matched_by,
                # Which product the row is. A new one gets its id when the job is applied.
                'product_id': product_id,
            }

        # BULK-MEDIA — the picture FILES this row names.
        #
        # A file name is matched against what came with the workbook; what is
        # wrong with a picture (missing, unreadable, two files with that name)
        # is an error of THIS row, with the file named, because that is where
        # the operator will fix it.
        image_url = fields.get('image_url', '')
        if image_url and storefront_media.managed_public_id(image_url):
            # Una imagen subida a la plataforma no se cita por su dirección: se
            # coloca con las columnas de archivo, que comprueban de quién es.
            # Escrita aquí, una empresa podía apuntar a la imagen de otra, y la
            # otra ya no podía borrarla porque quedaba «en uso».
            errors.append(
                'URL de imagen: una imagen subida a la plataforma no se cita por su '
                'dirección. Adjunta el archivo y usa «Imagen principal» o «Imágenes».'
            )
            image_url = ''
        main_name = import_media.normalize_name(fields.get('image_main', ''))
        row_images = []
        references = import_media.split_references(
            fields.get('image_main', ''), fields.get('image_files', ''),
        )
        gallery = galleries.of(product_id)
        if references:
            row_hashes: set[str] = set()
            for reference in references:
                found = stager.resolve(reference)
                if 'error' in found:
                    errors.append(f'Imagen «{reference}»: {found["error"]}')
                    continue
                if found['sha256'] in row_hashes:
                    warnings.append(
                        f'«{reference}» es la misma imagen que otra de esta fila; se usa una vez.'
                    )
                    continue
                row_hashes.add(found['sha256'])
                # The same picture already in THIS product's gallery: a file
                # imported twice must not double the gallery.
                existing = found['sha256'] in gallery['hashes']
                already_present += 1 if existing else 0
                row_images.append({
                    'name': reference, 'address': found['address'],
                    'sha256': found['sha256'],
                    'primary': import_media.normalize_name(reference) == main_name,
                    'existing': existing,
                })
            total = gallery['count'] + sum(1 for item in row_images if not item['existing'])
            if total > product_media.max_per_product():
                errors.append(
                    f'Un producto admite hasta {product_media.max_per_product()} imágenes; '
                    f'esta fila lo dejaría con {total}.'
                )
            if image_url:
                warnings.append(
                    'La fila trae archivos de imagen; la «URL de imagen» se ignora.'
                )
                image_url = ''
        elif image_url and gallery['count']:
            warnings.append(
                'El producto ya tiene galería; la «URL de imagen» se ignora.'
            )
            image_url = ''

        normalized = {
            'name': name, 'code': code, 'barcode': barcode,
            'category': category_name, 'category_id': category_id,
            'category_default': default_category.name if category_is_default else '',
            'destination': destination,
            'price': str(price) if price is not None else '',
            'description': fields.get('description', ''),
            'image_url': image_url,
            'images': row_images,
            'slug': fields.get('slug', ''),
            'product_id': product_id, 'matched_by': matched_by,
            # Kept so that apply resolves the row the same way the preview did.
            'identifier': identifier,
        }
        if history_data is not None:
            # The old SKU as a code (only when asked), and the name that is NOT written.
            normalized['legacy_code'] = legacy_as_code
            normalized['keep_name'] = history_data['keep_name']
            if history_cost_value is not None:
                # Top level, so a reader without `costs.view` never receives it (`import_views`).
                normalized['historical_cost'] = history_cost_value
        if reference_cost is not None:
            normalized['reference_cost'] = str(reference_cost)
            before = (standing.get(product_id) or {}).get('reference_cost')
            if product_id and before != reference_cost:
                # What it was is the company's knowledge: `costs.view` only
                # (stripped from the job for anybody else, `import_views`).
                normalized['reference_cost_before'] = str(before) if before is not None else ''
                warnings.append(f'Costo de referencia: pasa a {reference_cost}.')

        model_class = ''
        # DC2 — «Modelo» (+ «Capacidad», «Color», «Línea»): this product is
        # that variant. Checked now so apply cannot fail on it; without
        # «Modelo» the other three are not read and nothing changes.
        model_name = fields.get('model', '') if 'model' in mapping else ''
        if model_name:
            from . import device_catalog

            spec = None
            labels = [fields.get('capacity', ''), fields.get('colour', '')]
            if history:
                labels += [fields.get('sim', ''), fields.get('region', '')]
            try:
                spec = device_catalog.resolve_variant_spec(
                    company, model_name=model_name, line_text=fields.get('line', ''), labels=labels,
                )
            except device_catalog.CatalogError as exc:
                if history and exc.field == 'model':
                    # MIGRATION-B: a NEW model. Loaded without a variant; the model is created later.
                    warnings.append(f'{exc} El producto se carga sin variante; el modelo queda en el informe.')
                    model_class = 'new_model'
                elif history and exc.field == 'options':
                    # SIM and region are optional: the variant is tried again without them, and
                    # what was left out is said (#170 review).
                    optional = [v for v in (fields.get('sim', ''), fields.get('region', '')) if v.strip()]
                    spec = None
                    if optional:
                        try:
                            spec = device_catalog.resolve_variant_spec(
                                company, model_name=model_name, line_text=fields.get('line', ''), labels=labels[:2],
                            )
                        except device_catalog.CatalogError:
                            spec = None
                    if spec is not None:
                        warnings.append(
                            f'{exc} La variante se asigna sin {" ni ".join(f"«{v}»" for v in optional)}, '
                            f'que queda(n) en el informe.')
                    else:
                        reviews.append(f'{exc} No se asigna variante; la fila queda en el informe.')
                        model_class = 'new_model'
                else:
                    errors.append(str(exc))
            if spec is not None:
                model_class = 'model_new_variant'
                holder = device_catalog.find_variant(company, spec)
                key = (spec['model_id'], spec['condition_line'], spec['variant_key'])
                if holder is not None and holder.pk != product_id:
                    errors.append(f'«{holder.name}» ya es esa variante de «{spec["model_name"]}».')
                    flags.add('ambiguous')
                elif key in seen_variants:
                    errors.append(f'Otra fila ({seen_variants[key]}) ya es esa variante.')
                else:
                    seen_variants[key] = row_number
                    normalized['variant'] = {
                        'model_id': spec['model_id'], 'condition_line': spec['condition_line'],
                        'option_ids': spec['option_ids'],
                    }

        if errors:
            action = BulkImportRow.ERROR
        elif product_id:
            if options['mode'] == 'create_only':
                action = BulkImportRow.SKIP
                warnings.append('Ya existe; en modo «sólo crear» no se toca.')
            else:
                action = BulkImportRow.UPDATE
        else:
            if price is None and 'price' in mapping:
                errors.append('Un producto nuevo necesita precio.')
                flags.add('insufficient')
                action = BulkImportRow.ERROR
            elif 'price' not in mapping:
                errors.append('Falta asignar la columna de precio para crear productos.')
                flags.add('insufficient')
                action = BulkImportRow.ERROR
            elif code_series_off and not _barcodes_for(
                {'barcode': barcode, 'code': code, 'legacy_code': legacy_as_code}, options,
            ):
                errors.append(
                    'Este producto nuevo no trae código y la serie «Código de producto» de la empresa está '
                    'desactivada: no se le puede generar uno. Escribe un código en la fila o activa la serie.'
                )
                flags.add('insufficient')
                action = BulkImportRow.ERROR
            else:
                action = BulkImportRow.CREATE

        state = standing.get(product_id)
        # A LOAD NEVER ACTIVATES OR DEACTIVATES A PRODUCT THAT EXISTS (owner's
        # decision, 2026-10-13) — in either direction. A file downloaded last
        # month must not switch back on what was retired since, nor retire what
        # was switched on. When the file and the product disagree, the row says so.
        born_active = wants_active is not False
        if action == BulkImportRow.UPDATE and state is not None and wants_active != '' \
                and wants_active != state['is_active']:
            labels = import_formats.STATE_LABELS
            warnings.append(
                f'El archivo dice «{labels[wants_active]}» y el producto está '
                f'{"activo" if state["is_active"] else "inactivo"}: esta carga no activa ni desactiva '
                f'productos; se queda como está.'
            )
        # How a NEW product is born. None on a product that exists: not touched.
        normalized['is_active'] = born_active if action == BulkImportRow.CREATE else None

        # A ROW THAT ASKS FOR THE WEB FOR WHAT IS NOT ELIGIBLE (owner's decision,
        # 2026-10-13). Not an error: everything else the row says is applied,
        # and the product stays — or is born — «solo stock interno», with the
        # reasons on the row. Judged on the state the product WILL be left in,
        # counting what this same row brings (a price, a category, pictures).
        # A product that is already on the web is not taken off by this rule.
        refused = []
        asks_to_publish = (
            destination == import_formats.DESTINATION_ONLINE
            and action in (BulkImportRow.CREATE, BulkImportRow.UPDATE)
            and not (state is not None and state['is_published_online'])
        )
        if asks_to_publish:
            refused = product_publication.problems_of(
                is_active=state['is_active'] if state is not None else born_active,
                price=price if price is not None else (state['price'] if state is not None else None),
                has_image=bool(
                    (state is not None and state['image_url']) or gallery['count'] or row_images or image_url
                ),
                has_category=bool(
                    category_id or (state is not None and state['category_id'])
                    or (category_name and options['create_missing_categories'])
                ),
                require_image=require_image,
            )
        if refused:
            publication_refused += 1
            warnings.append(
                'No se publica en e-commerce: '
                + ' '.join(product_publication.PROBLEM_LABELS[code] for code in refused)
                + (' Sigue' if state is not None else ' Se crea')
                + ' como «Solo stock interno»; el resto de la fila se aplica.'
            )
            # What the row shows is what will happen.
            destination = '' if state is not None else import_formats.DESTINATION_INTERNAL
        normalized['destination'] = destination
        normalized['publication_refused'] = refused

        if action == BulkImportRow.UPDATE and state is not None:
            if destination == import_formats.DESTINATION_INTERNAL and state['is_published_online']:
                to_internal += 1
            elif destination == import_formats.DESTINATION_ONLINE and not state['is_published_online']:
                to_web += 1

        if history_data is not None:
            if action == BulkImportRow.UPDATE and not _history_would_change(
                normalized, state,
                new_codes=[entry for entry in history_data['codes'] if entry['status'] == 'kept'],
                row_images=row_images,
            ):
                action = BulkImportRow.NO_CHANGE
            if errors and not flags:
                flags.add('rejected')
            if reviews:
                # Written, so somebody confirms it; not written, it is only said.
                warnings.extend(reviews)
                if action in (BulkImportRow.CREATE, BulkImportRow.UPDATE):
                    flags.add('review')
                    review_rows += 1
            if action != BulkImportRow.ERROR:
                if product_id:
                    ledger.counts['matched'] += 1
                elif action == BulkImportRow.CREATE:
                    ledger.counts['new'] += 1
                for entry in history_data['codes']:
                    ledger.counts['codes_kept' if entry['status'] == 'kept' else 'codes_existing'] += 1
            for flag, count in (('duplicate', 'duplicates'), ('conflict', 'conflicts'),
                                ('ambiguous', 'ambiguous'), ('rejected', 'rejected')):
                if flag in flags:
                    ledger.counts[count] += 1
            history_data['flags'] = sorted(flags)
            history_data['match'] = _history_match(
                product_id, matched_by, history_data['codes'], index,
                model_class or ('new_model' if history_data['model'] else ''),
            )
            history_data['classification'] = _history_classification(
                flags, history_data['match'], inactive=bool(product_id) and (standing.get(product_id) or {}).get(
                    'is_active') is False)
            history_data['needs_code'] = action == BulkImportRow.CREATE and not history_data['codes']
            ledger.classes[history_data['classification']] = ledger.classes.get(history_data['classification'], 0) + 1
            ledger.counts['without_code'] += 1 if history_data['needs_code'] else 0
            normalized['history'] = history_data

        counts[action if action != BulkImportRow.NO_CHANGE else 'no_change'] += 1
        staged.append(BulkImportRow(
            job=job, sheet_name=sheet_name, row_number=row_number,
            action=action, match_key=barcode or code or name[:120],
            normalized_data=normalized, errors=errors, warnings=warnings,
        ))

    if counts['error']:
        # A job with errors cannot be applied (`is_applicable`), so the pictures
        # stored for it have no future. Removed now rather than left for the
        # daily sweep.
        stager.discard()
        for row in staged:
            for item in (row.normalized_data or {}).get('images', []):
                item['address'] = None
    media_summary = {**stager.summary(), 'already_present': already_present}

    BulkImportRow.objects.bulk_create(staged, batch_size=500)

    job.rows_total = len(staged)
    job.rows_create = counts['create']
    job.rows_update = counts['update']
    job.rows_no_change = counts['no_change']
    job.rows_skip = counts['skip']
    job.rows_error = counts['error']
    job.summary = {
        'reader_notes': reader_notes,
        'detected': (detected or {}).get('label', ''),
        'format_notes': [*(detected or {}).get('notes', []), *([destination_note] if destination_note else []),
                         *([variant_note] if variant_note else [])],
        'unmapped': [
            note for note in import_formats.unmapped_notes(headers, mapping)
            if note['column'] not in {header for _column, header in quantity_columns}
        ],
        'sheets': list(workbook.sheetnames),
        'media': media_summary,
        # How many EXISTING products this file moves onto the web or off it.
        # A new product is not a change of destination: it has none yet.
        'destination_changes': {'to_web': to_web, 'to_internal': to_internal},
        # Rows that asked for the web for a product that is not eligible: the
        # product is loaded, and not published.
        'publication_refused': publication_refused,
    }
    if history:
        if quantity_columns:
            job.summary['format_notes'].append(
                'Cantidades anteriores (' + ', '.join(f'«{header}»' for _c, header in quantity_columns) + '): se '
                'guardan sólo como referencia para un recuento posterior. No se aplican al stock; vacío = no informado.'
            )
        job.summary['history'] = {
            'version': HISTORY_VERSION, **ledger.counts, 'review': review_rows,
            'classes': {key: ledger.classes.get(key, 0) for key in HISTORY_CLASSES},
            'legacy_code_as_barcode': options['legacy_code_as_barcode'],
            # Reference only: which columns carried an old quantity. Never stock.
            'old_quantity_columns': [header for _column, header in quantity_columns],
            'quantity_columns': [
                {'index': column, 'column': header,
                 'branch_id': column_branches[column].pk if column in column_branches else None,
                 'branch_name': column_branches[column].name if column in column_branches else ''}
                for column, header in quantity_columns
            ],
        }
    job.save(update_fields=[
        'rows_total', 'rows_create', 'rows_update', 'rows_no_change',
        'rows_skip', 'rows_error', 'summary',
    ])
    return job


# =============================================================================
# Apply
# =============================================================================

@transaction.atomic
def apply_products(*, job, actor, confirm_review=None):
    """
    Write the staged rows. All or nothing, and idempotent on the job.

    LABEL-MIGRATION-01: a historical job with rows to review is applied only
    when `confirm_review` is HOW MANY they are, typed by whoever applies it.
    """
    job = BulkImportJob.objects.select_for_update().get(pk=job.pk)
    if job.status == BulkImportJob.APPLIED:
        # §52 — a second click returns the first result. Not an error: the
        # operator pressed a button twice, which is not a mistake worth
        # punishing with a duplicate catalogue.
        return job, False
    if job.import_type != BulkImportJob.PRODUCTS:
        raise ImportError_('Este trabajo no es de productos.')
    if job.rows_error:
        raise ImportError_(
            f'Hay {job.rows_error} fila(s) con error. Corrige el archivo y vuelve '
            f'a subirlo: no se aplica una importación a medias.'
        )

    options = {**DEFAULT_OPTIONS, **(job.options_snapshot or {})}
    history = options.get('history_mode') is True
    to_review = int(((job.summary or {}).get('history') or {}).get('review') or 0) if history else 0
    if to_review and str(confirm_review if confirm_review is not None else '').strip() != str(to_review):
        raise ImportError_(
            f'Hay {to_review} fila(s) a revisar. Revísalas y, para aplicar, escribe {to_review} en la '
            f'confirmación.'
        )
    company = job.company
    if history:
        # MIGRATION-B — two historical loads of one company apply ONE AFTER THE OTHER.
        # The second waits here and then re-checks against what the first wrote, so
        # it cannot create the same product twice. NO KEY UPDATE: rows that only
        # reference the company (orders, movements) are not blocked by it.
        from .models import Company

        list(Company.objects.select_for_update(no_key=True).filter(pk=company.pk).values_list('pk', flat=True))
    from .cost_services import user_may_view_costs

    costs_allowed = user_may_view_costs(actor, company)
    index = CatalogueIndex(company)
    taken_slugs: set[str] = set()
    created = updated = images_added = 0
    moved = []          # (product id, published): existing products this job sent to the web or took off it
    require_image = product_publication.requires_image(company)
    # Publications this job asked for and did not make: the ones the preview
    # already refused, plus what stopped being eligible since.
    not_published = 0

    rows = list(
        job.rows.filter(action__in=[BulkImportRow.CREATE, BulkImportRow.UPDATE])
        .order_by('row_number')
    )
    reconciled = []
    to_generate = []   # (row number, product) created without any code

    # §19 — VALIDATE THE WHOLE SET FIRST, then write.
    #
    # Preview recorded which product each row resolved to (or that it resolved
    # to none, meaning "create"). If that has changed, the file no longer
    # describes the catalogue in front of us, and there is no safe way to guess
    # what was intended: a row approved as "create a new article" whose code is
    # now held by something else would, if applied, rename and reprice that
    # other article. Nothing in the data distinguishes "the same product arrived
    # by another route" from "an unrelated product took this code" — only a
    # person can, by reading a fresh preview.
    #
    # Done in a pass of its own, against the catalogue as it stands BEFORE this
    # job writes anything. Interleaving it with the writes made the job trip
    # over itself: the second of two rows creating articles with the same name
    # would re-match the product the first row had just created, and abort a
    # perfectly valid import.
    for row in rows:
        data = row.normalized_data or {}
        product_id, match_error, _by, _warn = index.match(
            barcode=data.get('barcode', ''), code=data.get('code', ''),
            name=data.get('name', ''), identifier=data.get('identifier', ''),
            legacy_code=data.get('legacy_code', '') if history else '',
        )
        if match_error:
            raise ImportError_(f'Fila {row.row_number}: {match_error}')
        if data.get('product_id') != product_id:
            raise ImportError_(
                f'El catálogo cambió desde la previsualización (fila '
                f'{row.row_number}, «{data.get("name", "")}»). Vuelve a '
                f'previsualizar antes de aplicar.'
            )

    # EVERY PRODUCT THIS JOB EDITS, LOCKED NOW, LOWEST ID FIRST.
    #
    # The loop below used to take each product as its row came up — in FILE
    # order. The bulk visibility change and the picture load take theirs in id
    # order, so a file naming the higher id first and one of those, over the
    # same two products, waited on each other until the database killed one (a
    # 500). One order for everybody: whoever arrives second waits, and proceeds.
    # The per-row `select_for_update` further down then finds its row already held.
    edited = sorted({(row.normalized_data or {}).get('product_id') for row in rows} - {None})
    list(Product.objects.select_for_update().filter(company=company, pk__in=edited).order_by('pk').only('pk'))

    for row in rows:
        data = row.normalized_data or {}
        name = data.get('name', '')
        barcode = data.get('barcode', '')
        code = data.get('code', '')
        # The APPROVED resolution, re-verified above. Not re-derived here, so
        # products this same job creates cannot capture later rows.
        product_id = data.get('product_id')

        category_id = data.get('category_id')
        category_name = data.get('category', '')
        if category_name and category_id is None:
            if not options['create_missing_categories']:
                raise ImportError_(
                    f'Fila {row.row_number}: la categoría «{category_name}» no existe.'
                )
            key = ' '.join(category_name.split()).lower()
            category_id = index.categories.get(key)
            if category_id is None:
                category = Category.objects.create(
                    company=company, name=category_name,
                    slug=_unique_category_slug(company, category_name),
                )
                category_id = category.pk
                index.categories[key] = category_id

        price_text = data.get('price', '')
        price = Decimal(price_text) if price_text else None
        cost_text = data.get('reference_cost', '')
        # Whoever APPLIES needs `costs.view` too: a job previewed by somebody
        # who has it is not a way to write a cost for somebody who has not.
        reference_cost = Decimal(cost_text) if cost_text and costs_allowed else None

        if product_id:
            product = Product.objects.select_for_update().get(
                pk=product_id, company=company,
            )
            changes = []
            if price is not None and product.price != price:
                product.price = price
                changes.append('price')
            # DC1. A blank cell leaves the reference cost alone, like any other.
            if reference_cost is not None and product.reference_cost != reference_cost:
                product.reference_cost = reference_cost
                changes.append('reference_cost')
            # §37 — a blank cell means "leave it alone", never "erase it".
            # Wiping a description because a column was empty destroys work
            # nobody asked to destroy.
            # With a gallery —its own, or the one this row brings— the picture
            # address is the gallery's primary and is not typed over.
            owns_address = bool(data.get('images')) or product.images.exists()
            for field in ('description', 'image_url'):
                value = data.get(field, '')
                if field == 'image_url' and owns_address:
                    continue
                if value and getattr(product, field) != value:
                    setattr(product, field, value)
                    changes.append(field)
            # The screen's category for rows without one fills a gap; it does
            # not move a product somebody already filed.
            fills_a_gap = bool(data.get('category_default'))
            if category_id and product.category_id != category_id and not (fills_a_gap and product.category_id):
                product.category_id = category_id
                changes.append('category')
            # A job previewed before this column existed has no key: no change.
            wanted = data.get('destination', '')
            if wanted in (import_formats.DESTINATION_ONLINE, import_formats.DESTINATION_INTERNAL):
                publish = wanted == import_formats.DESTINATION_ONLINE
                # ASKED AGAIN, WITH THE PRODUCT IN HAND AND LOCKED. The preview
                # is a forecast: a product deactivated since is not published
                # because a row approved earlier said so.
                if publish and not product.is_published_online and product_publication.problems_of(
                    is_active=product.is_active, price=product.price,
                    has_image=bool(product.image_url or data.get('images') or product.images.exists()),
                    has_category=product.category_id is not None, require_image=require_image,
                ):
                    not_published += 1
                elif product.is_published_online != publish:
                    product.is_published_online = publish
                    changes.append('is_published_online')
                    moved.append((product.pk, publish))
            # LABEL-MIGRATION-01: a historical row recognised by its code keeps the
            # catalogue's name — it was shown for review, never written.
            # In the historical mode only a row matched by its «Identificador» renames:
            # a row matched by a code never writes a name, whatever the names were at
            # preview (#170 review).
            if name and product.name != name and not (history and data.get('matched_by') != 'identifier'):
                product.name = name
                changes.append('name')
            if changes:
                product.full_clean(exclude=['slug'])
                product.save(update_fields=[*changes, 'updated_at'])
                updated += 1
            counted = bool(changes)
        else:
            counted = True
            # Only what the approved row says. A row with no key (a job staged
            # before the column existed) asked for nothing: internal.
            publish = data.get('destination', '') == import_formats.DESTINATION_ONLINE
            # «Estado» of the file: only here, where the product is born.
            born_active = data.get('is_active') is not False
            if publish and product_publication.problems_of(
                is_active=born_active, price=price,
                has_image=bool(data.get('image_url') or data.get('images')),
                has_category=category_id is not None, require_image=require_image,
            ):
                publish = False
                not_published += 1
            product = Product(
                company=company, name=name,
                slug=unique_slug(company, data.get('slug') or name, taken=taken_slugs),
                description=data.get('description', ''),
                price=price if price is not None else Decimal('0.00'),
                image_url=data.get('image_url', ''),
                category_id=category_id,
                is_published_online=publish,
                is_active=born_active,
                reference_cost=reference_cost,
            )
            product.full_clean()
            product.save()
            created += 1
            key = ' '.join(name.split()).lower()
            index.by_name.setdefault(key, product.pk)

        # LABEL-01 — ONE code policy for every way a product is born (`product_codes`).
        # The row's codes are kept as they are; one this product already holds is
        # reused, never rewritten nor reactivated; one of another product or of a
        # device stops the whole job. Only a product CREATED here, that brought no
        # code, receives a generated one. A product that exists never does.
        supplied = [
            (normalize_barcode(candidate), symbology) for candidate, symbology in _barcodes_for(data, options)
            if normalize_barcode(candidate)
        ]
        try:
            # Never generates HERE: a generated number could be the code a later row
            # of this same file brings. Generation is a second pass, below (#170 review).
            outcome = product_codes.ensure_code(product, supplied=supplied, actor=actor, generate=False)
        except product_codes.ProductCodeError as exc:
            raise ImportError_(f'Fila {row.row_number}: {exc.detail}', code=exc.code) from None
        for barcode in outcome.added:
            index.by_barcode[barcode.code] = product.pk
        if product_id is None and not supplied:
            to_generate.append((row.row_number, product))

        if data.get('publication_refused'):
            not_published += 1

        # DC2 — the variant the preview approved. A product that already is
        # exactly that variant is left alone.
        wanted_variant = data.get('variant')
        if wanted_variant:
            from . import device_catalog

            current = (product.product_model_id, product.condition_line, product.variant_key)
            target = (wanted_variant['model_id'], wanted_variant['condition_line'],
                      device_catalog.variant_key(wanted_variant['option_ids']))
            if current != target:
                try:
                    device_catalog.set_variant(
                        product, model_id=wanted_variant['model_id'],
                        condition_line=wanted_variant['condition_line'],
                        option_ids=wanted_variant['option_ids'], actor=actor,
                    )
                except device_catalog.CatalogError as exc:
                    raise ImportError_(
                        f'Fila {row.row_number}: {exc} Vuelve a subir el archivo.'
                    ) from None
                if not counted:
                    updated += 1
                    counted = True

        placed = _place_row_images(row, product, data.get('images') or [], actor)
        images_added += placed
        if placed and not counted:
            updated += 1

        if history and isinstance(data.get('history'), dict):
            # For the reconciliation report: which product each external id became.
            data['history']['product_id'] = product.pk
            row.normalized_data = data
            reconciled.append(row)

    # One line per product that changed destination. «Applied an import» does
    # not say which products left the web; the panel's own edit does, and so
    # does this.
    from .models import AdminAuditLog

    for product_pk, published in moved:
        AdminAuditLog.log(
            actor=actor, action='product_destination_changed', target_type='product', target_id=product_pk,
            metadata={'is_published_online': published, 'import_job_id': job.pk}, company=company,
        )

    # SECOND PASS: every code the file brings is written; only now do the products
    # created without one get a generated code, which therefore skips them all.
    for row_number, product in to_generate:
        try:
            product_codes.ensure_code(product, actor=actor, generate=True)
        except product_codes.ProductCodeError as exc:
            raise ImportError_(f'Fila {row_number}: {exc.detail}', code=exc.code) from None

    if reconciled:
        BulkImportRow.objects.bulk_update(reconciled, ['normalized_data'], batch_size=500)

    job.status = BulkImportJob.APPLIED
    job.applied_by = actor
    job.applied_at = timezone.now()
    job.summary = {**(job.summary or {}), 'applied': {
        'created': created, 'updated': updated, 'images_added': images_added,
        'destination_changed': len(moved),
        'publication_refused': not_published,
    }}
    job.save(update_fields=['status', 'applied_by', 'applied_at', 'summary'])
    return job, True


def _place_row_images(row, product, images, actor) -> int:
    """
    Put the pictures a row approved into its product's gallery.

    Same transaction as the rest of apply, so a picture that cannot be placed
    takes the whole import back with it — a product created without the
    picture its row promised is the silent half-result this importer refuses.

    `claim` (inside `place`) is what makes the address in the staged row
    harmless: it must be an image that still exists AND belongs to the job's
    company. One that the daily sweep removed, or one of another company, stops
    the import with the file named.
    """
    from django.core.exceptions import ValidationError

    added = 0
    for item in images:
        name = item.get('name', '')
        if item.get('existing'):
            if item.get('primary'):
                current = product.images.filter(source_sha256=item.get('sha256', '')).first()
                if current is not None and not current.is_primary:
                    product_media.update_image(image=current, actor=actor, is_primary=True)
            continue
        try:
            product_media.place(
                product=product, address=item.get('address') or '', actor=actor,
                make_primary=bool(item.get('primary')), source='import',
                source_sha256=item.get('sha256', ''),
            )
        except ValidationError:
            raise ImportError_(
                f'Fila {row.row_number}: la imagen «{name}» ya no está disponible. '
                f'Vuelve a previsualizar con las imágenes adjuntas antes de aplicar.'
            ) from None
        except product_media.ProductMediaError as exc:
            raise ImportError_(f'Fila {row.row_number}: {exc}') from None
        added += 1
    return added


def _barcode_shape_errors(code: str, label: str) -> list[str]:
    """
    Reject a code the barcode table will not accept — AT PREVIEW.

    `validate_barcode` requires 4 to 64 printable characters. Without this check
    a two-character code sails through the preview, reports CREATE, and then
    raises a `ValidationError` from inside `apply()` — so the operator gets a
    stack trace instead of a row number, having already been told the file was
    fine. A rule enforced at the far end of the pipeline is a rule the preview
    lies about.
    """
    from django.core.exceptions import ValidationError

    from .models import validate_barcode

    try:
        validate_barcode(code)
    except ValidationError:
        return [
            f'El {label} «{code}» no es válido: debe tener entre 4 y 64 '
            f'caracteres imprimibles, sin espacios.'
        ]
    return []


def _barcodes_for(data, options):
    """
    Which codes on this row become scannable labels.

    §27 — `CODIGO` becomes an `internal` barcode rather than a new column on
    `Product`. The audit of the owner's file supports it: 696 rows, 696 distinct
    codes, none missing, and 692 of them printable `C000001`-style labels. It is
    exactly what `ProductBarcode` is for, and a product already routinely
    carries several codes.
    """
    out = []
    barcode = (data.get('barcode') or '').strip()
    if barcode:
        out.append((barcode, detect_symbology(barcode)))
    code = (data.get('code') or '').strip()
    if code and options.get('code_as_barcode') and normalize_barcode(code) != normalize_barcode(barcode):
        out.append((code, ProductBarcode.INTERNAL))
    # LABEL-MIGRATION-01: the old SKU, only when the job asked for it. Staged only then.
    legacy = (data.get('legacy_code') or '').strip()
    if legacy and options.get('history_mode') and options.get('legacy_code_as_barcode') and \
            normalize_barcode(legacy) not in {normalize_barcode(barcode), normalize_barcode(code)}:
        out.append((legacy, detect_symbology(legacy)))
    return out


def _unique_category_slug(company, name):
    base = slugify(name)[:40] or 'categoria'
    candidate, suffix = base, 2
    existing = set(
        Category.objects.filter(company=company, slug__startswith=base)
        .values_list('slug', flat=True)
    )
    while candidate in existing:
        candidate = f'{base}-{suffix}'
        suffix += 1
    return candidate


def _sha256(data: bytes) -> str:
    import hashlib
    return hashlib.sha256(data).hexdigest()


def _safe_filename(name: str) -> str:
    """
    Keep the name for the operator, keep the path traversal out of the database.
    """
    base = str(name or '').replace('\\', '/').split('/')[-1]
    return re.sub(r'[^\w\s.()\-]', '', base)[:255]
