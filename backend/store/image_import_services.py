"""
Pictures in bulk, for products that already exist. INVENTORY-UNIFIED-01, part 4.

    IT ADDS PICTURES TO GALLERIES. IT CREATES NO PRODUCT AND EDITS NONE.

Many image files and/or one ZIP, and no product workbook. Which product each
picture belongs to is said in one of two ways:

  BY THE FILE'S NAME (the default). `<código>.<ext>` is a picture of the product
  with that code. A trailing `_N`, `-N` or ` (N)` — N of one to three digits —
  is its ORDER: `FUNDA.png`, `FUNDA_2.png`, `FUNDA-3.jpg`, `FUNDA (4).webp`.
  The picture with the lowest order (a name with no number counts as 0) is the
  main one. `<código>` is looked up as the product load looks up «Código» and
  «Código de barras» — the internal code and every barcode, exact, through the
  same `CatalogueIndex` — and NEVER by the product's name.

      A code may itself end in `-2`. So the WHOLE name is tried as a code and,
      separately, the name without its trailing number. One product: that is
      it. Two different products: the file is not loaded and says so. None:
      the same. Nothing is guessed.

  BY A SMALL WORKBOOK. One sheet «Imágenes» with `Código`, `Imagen principal`,
  `Imágenes` — read exactly as the product load reads those columns
  (`import_media.split_references`). With a workbook the file names decide
  nothing.

TWO MODES, CHOSEN ON PURPOSE
----------------------------
`add` appends. A picture whose content is already in that gallery is not added
again. The main picture changes only for a gallery that has none, or when
«usar como principal» was ticked.

`replace` makes the gallery of each product IN THE LOAD exactly the uploaded
set. The preview says how many pictures each product loses, and apply asks for
the word `REEMPLAZAR`. A product that is not in the load is not touched.

THE SAME PIPELINE AS EVERY OTHER PICTURE
----------------------------------------
The bundle is `import_media.collect` (so every protection of the ZIP applies),
each picture waits as an unplaced image of the job's company (`Stager`), and
apply places it through `product_media` — which keeps `Product.image_url`, the
primary flag and the reference count that protects a placed image from the
daily sweep. What a replacement removes goes through `product_media` too.

Apply is all or nothing, once, with the job and then every product locked in
id order, and it stops if the catalogue or a gallery (its pictures, their order
or its main one) is not what the preview saw.
"""

from __future__ import annotations

import re

from django.db import transaction
from django.utils import timezone

from . import import_exports, import_media, product_media, xlsx_reader
from .import_formats import normalize_header
from .import_services import CatalogueIndex, ImportError_, _place_row_images, _safe_filename, _sha256
from .models import AdminAuditLog, BulkImportJob, BulkImportRow, Product, ProductImage

MODE_ADD, MODE_REPLACE = 'add', 'replace'
MODES = (MODE_ADD, MODE_REPLACE)
#: What must be typed to apply a replacement.
CONFIRM_WORD = 'REEMPLAZAR'

SOURCE_NAMES, SOURCE_WORKBOOK = 'names', 'workbook'

SHEET = 'Imágenes'
HEADERS = ['Código', 'Imagen principal', 'Imágenes']

#: `<code>_N`, `<code>-N`, `<code> (N)`: N is the order, one to three digits.
_ORDER_SUFFIX = re.compile(r'^(?P<code>.+?)(?:[_-](?P<plain>\d{1,3})| \((?P<bracketed>\d{1,3})\))$')

NO_PRODUCT, AMBIGUOUS_CODE = 'no_product', 'ambiguous_code'


class ImageImportError(Exception):
    """The load, or the job, cannot be used. The message is for the operator."""


def naming_rule() -> dict:
    """The grammar and the limits, for the screen and the docs: said once, here."""
    return {
        'pattern': '<código>.<ext>',
        'suffixes': ['_2', '-2', ' (2)'],
        'examples': ['FUNDA.png', 'FUNDA_2.png', 'FUNDA-3.jpg', 'FUNDA (4).webp'],
        'confirm_word': CONFIRM_WORD,
        'workbook': {'sheet': SHEET, 'headers': list(HEADERS)},
        'max_per_product': product_media.max_per_product(),
    }


def interpretations(filename) -> list[tuple[str, int]]:
    """
    The `(code, order)` readings of a file name, the whole name first.

    `FUNDA_2.png` -> `[('FUNDA_2', 0), ('FUNDA', 2)]`. Only the last extension
    comes off, and folders are not part of the name.
    """
    name = import_media.display_name(filename)
    stem = name.rsplit('.', 1)[0] if '.' in name else name
    readings = [(stem, 0)]
    suffix = _ORDER_SUFFIX.match(stem)
    if suffix and suffix['code'].strip():
        readings.append((suffix['code'], int(suffix['plain'] or suffix['bracketed'])))
    return readings


def _owner_by_name(index: CatalogueIndex, filename: str):
    """`(product id, code, order)` for a file, or `(None, reason, sentence)`."""
    hits = [
        (index.owner_of(code), code, order)
        for code, order in interpretations(filename) if code.strip()
    ]
    hits = [hit for hit in hits if hit[0]]
    if not hits:
        return None, NO_PRODUCT, (
            f'«{filename}»: ningún producto de esta empresa tiene ese código. No se carga.'
        )
    if len({product_id for product_id, _code, _order in hits}) > 1:
        codes = ' y '.join(f'«{code}»' for _product_id, code, _order in hits)
        return None, AMBIGUOUS_CODE, (
            f'«{filename}»: el nombre vale para dos productos ({codes}). No se carga: '
            f'renómbralo o usa el libro «{SHEET}».'
        )
    return hits[0]


# =============================================================================
# Preview
# =============================================================================

def preview_images(*, company, actor, images, upload=None, filename='', mode=MODE_ADD,
                   use_main=False) -> BulkImportJob:
    """
    Stage a load of pictures. Places NOTHING in any gallery.

    `images` is the `import_media.Bundle` of the request; `upload` the optional
    «Imágenes» workbook. The pictures some product will use are stored now, as
    unplaced images of `company`, so that apply finds them.
    """
    if mode not in MODES:
        raise ImageImportError('Modo de carga de imágenes no válido.')
    if images is None or len(images) == 0:
        raise ImageImportError('Adjunta al menos una imagen (PNG, JPG o WEBP), suelta o en un ZIP.')
    stager = import_media.Stager(company=company, actor=actor, bundle=images)
    try:
        return _preview(
            company=company, actor=actor, bundle=images, upload=upload, filename=filename,
            mode=mode, use_main=bool(use_main), stager=stager,
        )
    except BaseException:
        # The transaction took the rows back; the files already written to
        # storage are still there, with nothing pointing at them.
        stager.abandon()
        raise


def _plans_from_names(bundle, index):
    """`(plans, unmatched)` — which files go to which product, by their names."""
    plans: dict[int, dict] = {}
    unmatched = []
    for key in sorted(bundle.names()):
        name = bundle.display(key)
        product_id, code, order = _owner_by_name(index, name)
        if product_id is None:
            unmatched.append({'file': name, 'reason': code, 'sentence': order})
            continue
        plan = plans.setdefault(product_id, {'code': code, 'barcode': '', 'refs': [], 'errors': []})
        plan['refs'].append((order, import_media.normalize_name(name), name))
    for plan in plans.values():
        plan['refs'] = [name for _order, _key, name in sorted(plan['refs'])]
    # Stable for whoever reads the preview: by code.
    ordered = sorted(plans.items(), key=lambda item: item[1]['code'])
    return [dict(plan, product_id=product_id, row_number=number)
            for number, (product_id, plan) in enumerate(ordered, start=1)], unmatched


def _pick_sheet(workbook) -> str:
    names = list(workbook.sheetnames)
    for name in names:
        if normalize_header(name) == normalize_header(SHEET):
            return name
    for name in names:
        if not import_exports.is_help_sheet(name):
            return name
    raise ImageImportError('El archivo no tiene una hoja de imágenes.')


def _plans_from_workbook(upload, filename, index):
    """`(plans, sheet name, sha256)` — one plan per row, as the workbook says."""
    data = xlsx_reader.check_upload(upload, filename=filename)
    workbook, _notes = xlsx_reader.load_workbook(data)
    sheet = _pick_sheet(workbook)
    refusal = import_exports.report_sheet_refusal(sheet)
    if refusal:
        raise ImageImportError(refusal)
    headers, rows, _truncated = xlsx_reader.read_rows(workbook, sheet, header_row=1, mode=xlsx_reader.FULL_IMPORT)
    columns = {normalize_header(header): position for position, header in reversed(list(enumerate(headers)))}
    wanted = {field: columns.get(key) for field, key in (
        ('code', 'codigo'), ('barcode', 'codigo de barras'), ('main', 'imagen principal'), ('many', 'imagenes'),
    )}
    if (wanted['code'] is None and wanted['barcode'] is None) or (wanted['main'] is None and wanted['many'] is None):
        raise ImageImportError(
            f'El libro de imágenes lleva las columnas «{HEADERS[0]}», «{HEADERS[1]}» e «{HEADERS[2]}». '
            'Sin el código y sin una columna de imágenes no se puede saber de qué producto es cada una.'
        )

    plans, seen = [], {}
    for row_number, values, _numeric in rows:
        cells = {field: values.get(position) if position is not None else None for field, position in wanted.items()}
        if not any(cells.values()):
            continue
        plan = {'row_number': row_number, 'product_id': None, 'refs': [], 'errors': []}
        if any(value is xlsx_reader.FORMULA for value in cells.values()):
            plan.update(code='', barcode='', errors=['La fila tiene fórmulas: escribe los valores.'])
            plans.append(plan)
            continue
        text = {field: str(value or '').strip() for field, value in cells.items()}
        plan.update(code=text['code'], barcode=text['barcode'])
        if not (text['code'] or text['barcode']):
            plan['errors'].append('Falta el código del producto.')
        else:
            # By code and by barcode, exact — the product load's own reading. Never by name.
            product_id, match_error, _matched_by, _warnings = index.match(barcode=text['barcode'], code=text['code'])
            if match_error:
                plan['errors'].append(match_error)
            elif product_id is None:
                plan['errors'].append('No existe un producto con ese código en esta empresa.')
            elif product_id in seen:
                plan['errors'].append(f'Este producto ya aparece en la fila {seen[product_id]}. Deja una sola fila por producto.')
            else:
                seen[product_id] = row_number
                plan['product_id'] = product_id
        plan['refs'] = import_media.split_references(text['main'], text['many'])
        if not plan['refs']:
            plan['errors'].append('La fila no indica ninguna imagen.')
        plans.append(plan)
    return plans, sheet, _sha256(data)


@transaction.atomic
def _preview(*, company, actor, bundle, upload, filename, mode, use_main, stager):
    index = CatalogueIndex(company)
    if upload is not None:
        source = SOURCE_WORKBOOK
        plans, sheet, sha256 = _plans_from_workbook(upload, filename, index)
        unmatched = []
    else:
        source, sheet, sha256 = SOURCE_NAMES, '', ''
        plans, unmatched = _plans_from_names(bundle, index)

    job = BulkImportJob.objects.create(
        company=company, import_type=BulkImportJob.PRODUCT_IMAGES,
        original_filename=_safe_filename(filename) if filename else '', file_sha256=sha256,
        mapping_snapshot={'sheet_name': sheet, 'headers': list(HEADERS) if upload is not None else []},
        options_snapshot={'mode': mode, 'use_main': use_main, 'source': source},
        created_by=actor,
    )

    products = {
        product.pk: product for product in Product.objects.filter(
            company=company, pk__in=[plan['product_id'] for plan in plans if plan['product_id']],
        )
    }
    limit = product_media.max_per_product()
    staged, counts = [], dict(create=0, update=0, no_change=0, skip=0, error=0)
    to_add = to_remove = already_present = 0

    for plan in plans:
        errors, warnings = list(plan['errors']), []
        product = products.get(plan['product_id'])
        data = {
            'product_id': plan['product_id'], 'name': product.name if product else '',
            'code': plan['code'], 'barcode': plan['barcode'],
            'images': [], 'gallery_count': 0, 'gallery_state': [], 'will_add': 0, 'will_remove': 0, 'main': '',
        }
        if product is not None:
            gallery = list(
                ProductImage.objects.filter(company=company, product=product)
                .order_by('sort_order', 'pk').values('pk', 'source_sha256', 'is_primary')
            )
            in_gallery = {image['source_sha256'] for image in gallery if image['source_sha256']}
            current_main = next((image['source_sha256'] for image in gallery if image['is_primary']), '')
            images, hashes = [], set()
            for name in plan['refs']:
                found = stager.resolve(name)
                if 'error' in found:
                    errors.append(f'Imagen «{name}»: {found["error"]}')
                    continue
                if found['sha256'] in hashes:
                    warnings.append(f'«{name}» es la misma imagen que otra de este producto; se usa una vez.')
                    continue
                hashes.add(found['sha256'])
                # Replacing rebuilds the gallery from the upload: nothing in it is «already there».
                existing = mode == MODE_ADD and found['sha256'] in in_gallery
                images.append({
                    'name': name, 'address': found['address'], 'sha256': found['sha256'],
                    'primary': False, 'existing': existing,
                })
            will_add = sum(1 for image in images if not image['existing'])
            will_remove = len(gallery) if mode == MODE_REPLACE else 0
            total = will_add + len(gallery) - will_remove
            if total > limit:
                errors.append(f'Un producto admite hasta {limit} imágenes; «{product.name}» quedaría con {total}.')

            # Which one becomes the main picture, if any does. Always the FIRST:
            # by name it is the lowest order, and in a workbook `split_references`
            # puts «Imagen principal» in front.
            chosen = images[0] if images else None
            if chosen is not None and (mode == MODE_REPLACE or use_main or not gallery):
                # Already the main one: there is nothing to move.
                if not (chosen['existing'] and chosen['sha256'] == current_main):
                    chosen['primary'] = True
                    data['main'] = chosen['name']
            data.update(
                images=images, gallery_count=len(gallery),
                # The gallery as the preview saw it: which pictures, in which
                # order, and which is the main one. Apply compares all three.
                gallery_state=[[image['pk'], image['is_primary']] for image in gallery],
                will_add=will_add, will_remove=will_remove,
            )
            already_present += sum(1 for image in images if image['existing'])

        if errors:
            action = BulkImportRow.ERROR
        elif data['will_add'] or data['will_remove'] or data['main']:
            action = BulkImportRow.UPDATE
            to_add += data['will_add']
            to_remove += data['will_remove']
        else:
            action = BulkImportRow.NO_CHANGE
        counts[action] += 1
        staged.append(BulkImportRow(
            job=job, sheet_name=sheet, row_number=plan['row_number'], action=action,
            match_key=(plan['code'] or plan['barcode'])[:120], normalized_data=data,
            errors=errors, warnings=warnings,
        ))

    # Files nobody could be given: listed, never loaded. They do not stop the rest.
    for offset, item in enumerate(unmatched, start=len(plans) + 1):
        counts['skip'] += 1
        staged.append(BulkImportRow(
            job=job, sheet_name=sheet, row_number=offset, action=BulkImportRow.SKIP,
            match_key=item['file'][:120], normalized_data={'file': item['file'], 'reason': item['reason']},
            warnings=[item['sentence']],
        ))

    # ONLY WHAT WILL BE PLACED KEEPS WAITING. A job with errors cannot be
    # applied, so nothing of it has a future; and in a job that can, a picture
    # that is already in its gallery (or belongs to a row that changes nothing)
    # was stored only to learn what it is. Removed now, not left for the sweep.
    needed = set() if counts['error'] else {
        image['address']
        for row in staged if row.action == BulkImportRow.UPDATE
        for image in row.normalized_data.get('images', []) if not image['existing']
    }
    stager.keep_only(needed)
    for row in staged:
        for image in (row.normalized_data or {}).get('images', []):
            if image['address'] not in needed:
                image['address'] = None

    summary = {
        'media': {**stager.summary(), 'already_present': already_present},
        'products': sum(1 for plan in plans if plan['product_id']),
        'images_to_add': to_add, 'images_to_remove': to_remove,
        'unmatched': len(unmatched),
        'confirm_word': CONFIRM_WORD if mode == MODE_REPLACE else '',
        'naming': naming_rule(),
    }
    return _finish(job, staged, counts, summary)


def _finish(job, staged, counts, summary) -> BulkImportJob:
    BulkImportRow.objects.bulk_create(staged, batch_size=500)
    job.rows_total = len(staged)
    job.rows_update = counts['update']
    job.rows_no_change = counts['no_change']
    job.rows_skip = counts['skip']
    job.rows_error = counts['error']
    job.summary = summary
    job.save(update_fields=['rows_total', 'rows_update', 'rows_no_change', 'rows_skip', 'rows_error', 'summary'])
    return job


# =============================================================================
# Apply
# =============================================================================

_STALE_CATALOGUE = 'El catálogo cambió desde la previsualización (fila {row}, «{name}»). Vuelve a previsualizar antes de aplicar.'
_STALE_GALLERY = 'La galería de «{name}» cambió desde la previsualización. Vuelve a previsualizar antes de aplicar.'


@transaction.atomic
def apply_images(*, job, actor, confirmation='', request=None):
    """
    Place the staged pictures. ALL OF THE LOAD OR NONE OF IT, and once.

    Returns `(job, changed)`; a second apply returns the first result.
    """
    job = BulkImportJob.objects.select_for_update().get(pk=job.pk)
    if job.status == BulkImportJob.APPLIED:
        return job, False
    if job.import_type != BulkImportJob.PRODUCT_IMAGES:
        raise ImageImportError('Este trabajo no es de imágenes.')
    if job.rows_error:
        raise ImageImportError(
            f'Hay {job.rows_error} fila(s) con error. Corrige los archivos y vuelve a subirlos: '
            'no se aplica una carga a medias.'
        )
    options = job.options_snapshot or {}
    mode = options.get('mode', MODE_ADD)
    if mode == MODE_REPLACE and confirmation != CONFIRM_WORD:
        # Typed, and exactly: a replacement removes pictures from galleries.
        raise ImageImportError(f'Para reemplazar las galerías escribe {CONFIRM_WORD}.')

    company = job.company
    rows = sorted(
        job.rows.filter(action__in=[BulkImportRow.UPDATE, BulkImportRow.NO_CHANGE]),
        key=lambda row: row.normalized_data.get('product_id') or 0,
    )
    wanted = [row.normalized_data.get('product_id') for row in rows]
    # EVERY product of the load, locked lowest id first: two loads over the same
    # products take turns instead of each holding what the other needs.
    products = {
        product.pk: product
        for product in Product.objects.select_for_update().filter(company=company, pk__in=wanted).order_by('pk')
    }

    # The whole load is checked against the catalogue as it stands NOW, before
    # anything is written.
    index = CatalogueIndex(company)
    for row in rows:
        data = row.normalized_data
        product = products.get(data.get('product_id'))
        owner, match_error, _by, _warnings = index.match(barcode=data.get('barcode', ''), code=data.get('code', ''))
        if product is None or match_error or owner != product.pk:
            raise ImageImportError(_STALE_CATALOGUE.format(row=row.row_number, name=data.get('name', '')))
        now = [list(pair) for pair in ProductImage.objects.filter(company=company, product=product)
               .order_by('sort_order', 'pk').values_list('pk', 'is_primary')]
        if now != data.get('gallery_state', []):
            raise ImageImportError(_STALE_GALLERY.format(name=product.name))

    added = removed = made_main = touched = 0
    for row in rows:
        if row.action != BulkImportRow.UPDATE:
            continue
        data = row.normalized_data
        product = products[data['product_id']]
        if mode == MODE_REPLACE:
            # The main one last: removing it first would promote another one
            # only to remove that one too.
            for image in sorted(product_media.gallery(product), key=lambda image: image.is_primary):
                product_media.remove_image(image=image, actor=actor, request=request)
                removed += 1
        elif data.get('main') and data.get('gallery_count'):
            made_main += 1
        try:
            added += _place_row_images(row, product, data.get('images') or [], actor)
        except ImportError_ as exc:
            raise ImageImportError(str(exc)) from None
        touched += 1

    job.status = BulkImportJob.APPLIED
    job.applied_by = actor
    job.applied_at = timezone.now()
    applied = {'products': touched, 'images_added': added, 'images_removed': removed, 'made_main': made_main}
    job.summary = {**(job.summary or {}), 'applied': applied}
    job.save(update_fields=['status', 'applied_by', 'applied_at', 'summary'])

    if added or removed or made_main:
        # ONE line for the load: counts and how it was run. No file name, and
        # nothing of anybody's. Each picture keeps its own gallery line.
        AdminAuditLog.log(
            actor=actor, action='product_images_import_applied',
            target_type='bulk_import_job', target_id=job.pk,
            metadata={
                'job_id': job.pk, 'mode': mode, 'source': options.get('source', SOURCE_NAMES),
                **applied, 'unmatched': (job.summary or {}).get('unmatched', 0),
            },
            request=request, company=company,
        )
    return job, True
