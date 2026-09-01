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

from . import import_formats, xlsx_reader
from .models import (
    BulkImportJob,
    BulkImportRow,
    Category,
    Product,
    ProductBarcode,
    normalize_barcode,
)


class ImportError_(Exception):
    """The import cannot proceed as requested."""


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
        for code, product_id, is_active in ProductBarcode.objects.filter(
            company=company,
        ).values_list('code', 'product_id', 'is_active'):
            key = normalize_barcode(code)
            self.by_barcode[key] = product_id
            if not is_active:
                self.inactive_codes.add(key)

        self.by_name = {}
        self.ambiguous_names = set()
        for product_id, name in Product.objects.filter(
            company=company,
        ).values_list('pk', 'name'):
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

    def match(self, *, barcode='', code='', name=''):
        """
        Resolve a row to a product, or explain the ambiguity.

        Returns `(product_id_or_None, error_or_None, matched_by, warnings)`.

        §30 — when two identifiers on the SAME row point at DIFFERENT products,
        that is an error and not a preference. Choosing one would either update
        the wrong article or create a duplicate of an article that already
        exists, and both are silent.
        """
        hits = {}
        warnings: list[str] = []
        for label, value in (('barcode', barcode), ('code', code)):
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
        if len(distinct) > 1:
            return None, (
                'Los identificadores de esta fila apuntan a productos distintos.'
            ), '', warnings
        if distinct:
            return distinct.pop(), None, ','.join(sorted(hits)), warnings

        if name:
            key = ' '.join(name.split()).lower()
            if key in self.ambiguous_names:
                return None, (
                    'Hay más de un producto con este nombre; añade el código o '
                    'el código de barras para identificarlo.'
                ), '', warnings
            found = self.by_name.get(key)
            if found:
                return found, None, 'name', warnings
        return None, None, '', warnings


# =============================================================================
# Preview
# =============================================================================

DEFAULT_OPTIONS = {
    'mode': 'upsert',                    # or 'create_only'
    'create_missing_categories': False,  # §33 — default FALSE, deliberately
    'blank_means': 'skip_field',         # §37
    'code_as_barcode': True,             # §27
}


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


@transaction.atomic
def preview_products(*, company, actor, upload, filename, sheet_name=None,
                     header_row=None, mapping=None, options=None):
    """
    Parse, resolve and stage a product workbook. Writes NOTHING commercial.
    """
    options = {**DEFAULT_OPTIONS, **(options or {})}
    data = xlsx_reader.check_upload(upload, filename=filename)
    sha256 = _sha256(data)
    workbook, reader_notes = xlsx_reader.load_workbook(data)

    sheet_name = sheet_name or workbook.sheetnames[0]
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
    if 'name' not in mapping:
        raise ImportError_('Falta asignar la columna del nombre del producto.')

    # FULL_IMPORT, and deliberately BEFORE the job is created: a file that is
    # too long must leave nothing behind, not a job somebody could later apply.
    headers, rows, _truncated = xlsx_reader.read_rows(
        workbook, sheet_name, header_row=header_row, mode=xlsx_reader.FULL_IMPORT,
    )

    job = BulkImportJob.objects.create(
        company=company, import_type=BulkImportJob.PRODUCTS,
        original_filename=_safe_filename(filename),
        file_sha256=sha256,
        mapping_snapshot={
            'sheet_name': sheet_name, 'header_row': header_row,
            'mapping': mapping, 'headers': [str(h) for h in headers],
            'preset': (detected or {}).get('preset', ''),
        },
        options_snapshot=options,
        created_by=actor,
    )

    index = CatalogueIndex(company)
    staged = []
    counts = dict(create=0, update=0, no_change=0, skip=0, error=0)
    seen_barcodes: dict[str, int] = {}
    seen_names: dict[str, int] = {}

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
        errors, warnings = [], []
        if formulas:
            errors.append(
                f'Las columnas {", ".join(formulas)} contienen fórmulas. '
                f'Conviértelas a valor antes de importar.'
            )

        name = fields.get('name', '').strip()
        code = fields.get('code', '').strip()
        barcode = fields.get('barcode', '').strip()

        if not name:
            errors.append('Falta el nombre del producto.')

        price = None
        if 'price' in mapping:
            price, price_error = clean_price(fields.get('price'))
            if price_error:
                errors.append(price_error)

        if barcode:
            warnings.extend(barcode_warnings(
                barcode,
                cell_was_numeric=mapping.get('barcode') in numeric_columns,
            ))
            errors.extend(_barcode_shape_errors(barcode, 'código de barras'))
            previous = seen_barcodes.get(normalize_barcode(barcode))
            if previous:
                errors.append(
                    f'El código de barras «{barcode}» ya aparece en la fila {previous}.'
                )
            else:
                seen_barcodes[normalize_barcode(barcode)] = row_number
        if code and options['code_as_barcode']:
            errors.extend(_barcode_shape_errors(code, 'código interno'))
            previous = seen_barcodes.get(normalize_barcode(code))
            if previous and previous != row_number:
                errors.append(f'El código «{code}» ya aparece en la fila {previous}.')
            else:
                seen_barcodes[normalize_barcode(code)] = row_number

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

        product_id, match_error, matched_by, match_warnings = index.match(
            barcode=barcode, code=code, name=name,
        )
        warnings.extend(match_warnings)
        if match_error:
            errors.append(match_error)

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
            for candidate in (barcode, code):
                if not candidate:
                    continue
                owner = index.owner_of(candidate)
                if owner is not None and owner != product_id:
                    errors.append(
                        f'El código «{normalize_barcode(candidate)}» ya '
                        f'pertenece a otro producto de esta empresa.'
                    )

        if not errors and product_id is None and not (barcode or code):
            # §31 — with no stable identifier, only creation is defensible.
            # An UPDATE matched on name alone would rewrite the wrong article
            # the first time two products are named similarly.
            key = ' '.join(name.split()).lower()
            previous = seen_names.get(key)
            if previous:
                errors.append(f'El nombre «{name}» ya aparece en la fila {previous}.')
            else:
                seen_names[key] = row_number

        normalized = {
            'name': name, 'code': code, 'barcode': barcode,
            'category': category_name, 'category_id': category_id,
            'price': str(price) if price is not None else '',
            'description': fields.get('description', ''),
            'image_url': fields.get('image_url', ''),
            'slug': fields.get('slug', ''),
            'product_id': product_id, 'matched_by': matched_by,
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
                action = BulkImportRow.ERROR
            elif 'price' not in mapping:
                errors.append('Falta asignar la columna de precio para crear productos.')
                action = BulkImportRow.ERROR
            else:
                action = BulkImportRow.CREATE

        counts[action if action != BulkImportRow.NO_CHANGE else 'no_change'] += 1
        staged.append(BulkImportRow(
            job=job, sheet_name=sheet_name, row_number=row_number,
            action=action, match_key=barcode or code or name[:120],
            normalized_data=normalized, errors=errors, warnings=warnings,
        ))

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
        'format_notes': (detected or {}).get('notes', []),
        'unmapped': import_formats.unmapped_notes(headers, mapping),
        'sheets': list(workbook.sheetnames),
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
def apply_products(*, job, actor):
    """
    Write the staged rows. All or nothing, and idempotent on the job.
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
    company = job.company
    index = CatalogueIndex(company)
    taken_slugs: set[str] = set()
    created = updated = 0

    rows = list(
        job.rows.filter(action__in=[BulkImportRow.CREATE, BulkImportRow.UPDATE])
        .order_by('row_number')
    )

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
            name=data.get('name', ''),
        )
        if match_error:
            raise ImportError_(f'Fila {row.row_number}: {match_error}')
        if data.get('product_id') != product_id:
            raise ImportError_(
                f'El catálogo cambió desde la previsualización (fila '
                f'{row.row_number}, «{data.get("name", "")}»). Vuelve a '
                f'previsualizar antes de aplicar.'
            )

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

        if product_id:
            product = Product.objects.select_for_update().get(
                pk=product_id, company=company,
            )
            changes = []
            if price is not None and product.price != price:
                product.price = price
                changes.append('price')
            # §37 — a blank cell means "leave it alone", never "erase it".
            # Wiping a description because a column was empty destroys work
            # nobody asked to destroy.
            for field in ('description', 'image_url'):
                value = data.get(field, '')
                if value and getattr(product, field) != value:
                    setattr(product, field, value)
                    changes.append(field)
            if category_id and product.category_id != category_id:
                product.category_id = category_id
                changes.append('category')
            if name and product.name != name:
                product.name = name
                changes.append('name')
            if changes:
                product.full_clean(exclude=['slug'])
                product.save(update_fields=[*changes, 'updated_at'])
                updated += 1
        else:
            product = Product(
                company=company, name=name,
                slug=unique_slug(company, data.get('slug') or name, taken=taken_slugs),
                description=data.get('description', ''),
                price=price if price is not None else Decimal('0.00'),
                image_url=data.get('image_url', ''),
                category_id=category_id,
            )
            product.full_clean()
            product.save()
            created += 1
            key = ' '.join(name.split()).lower()
            index.by_name.setdefault(key, product.pk)

        for candidate, symbology in _barcodes_for(data, options):
            key = normalize_barcode(candidate)
            # Already held — by THIS product (the row matched on it, possibly a
            # deactivated label). Not rewritten and not reactivated: turning a
            # retired code back on is a decision somebody made once, and an
            # import is not the place to reverse it silently.
            if not key or key in index.by_barcode:
                continue
            ProductBarcode.objects.create(
                company=company, product=product, code=key,
                symbology=symbology,
                is_primary=not product.barcodes.filter(is_primary=True).exists(),
            )
            index.by_barcode[key] = product.pk

    job.status = BulkImportJob.APPLIED
    job.applied_by = actor
    job.applied_at = timezone.now()
    job.summary = {**(job.summary or {}), 'applied': {
        'created': created, 'updated': updated,
    }}
    job.save(update_fields=['status', 'applied_by', 'applied_at', 'summary'])
    return job, True


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
