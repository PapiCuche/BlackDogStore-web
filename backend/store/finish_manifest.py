"""
CATALOG FASE 1.2 · Z — finish pictures loaded from a MANIFEST (`import_finish_images`), for photos whose licence the
company already holds: each row names a model, one of its colours, a file inside a folder and where the file comes
from. Written so that a manifest can be prepared per reference model and run, re-run or simulated without surprises.

THE RULES (owner's):
  * nothing is imported without provenance — `finish_media.clean_provenance`, exactly as the panel: a source kind
    (never `unknown`) and the licence or authorisation text unless it is the shop's own photo;
  * a licence is NEVER marked verified here: a person verifies it in the panel afterwards;
  * the colour is one of THAT model's colours (its palette when it has one; D-PRC-8 colour dimension), never another;
    the same content for two colours of one model is refused;
  * nothing existing is overwritten: pictures are appended; a row whose content that finish already shows is skipped,
    so re-running a manifest writes nothing new;
  * the model is the company's (by slug, or by the `ReferenceModel.key` it was imported from); another company's
    answers like a missing one; the company comes from the command, never from the manifest.

ALL OR NOTHING. Every row is validated first (decoder, size, path, provenance, colour, limits) and reported; any error
and nothing is written. Then the files are stored (`storefront_media.store`, the panel's re-encoding) and placed in ONE
transaction (`finish_media.place`, the panel's lock and checks; the primary through `finish_media.update_image`) with
one summary audit row. If placing fails, every file stored by the run is released (`storefront_media.release`), as
`finish_media.add_image` does for one.

IDEMPOTENCY WITHOUT A NEW COLUMN. A stored file is a re-encoding, so its bytes are not the source file's. A row is a
duplicate when its source hash or the hash of its re-encoding equals the hash of a file that finish already shows (read
through the storage API), or the source hash the summary audit recorded for that picture (which still holds if the
encoder's output changes between runs).
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from django.core.files import File
from django.db import transaction
from django.db.models import Q

from . import evidence_storage, finish_media, storefront_media
from .models import AdminAuditLog, ModelOption, ProductModel, VariantOption

FIELDS = ('model', 'color', 'file', 'view', 'alt_text', 'is_primary', 'sort', 'source_kind', 'source_url', 'license',
          'attribution')
MAX_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_ROWS = 5000
SUMMARY_ACTION = 'finish_images_imported'

OK, SKIP, ERROR = 'ok', 'skip-duplicate', 'error'
_TRUE = {'1', 'true', 'yes', 'y', 'si', 'sí', 'x'}
_FALSE = {'', '0', 'false', 'no', 'n'}
_DRIVE = re.compile(r'^[A-Za-z]:')


class ManifestError(Exception):
    """The manifest as a whole cannot be read (format, size, no rows)."""


class RowError(Exception):
    """One row cannot be imported; the message says why."""


@dataclass
class Row:
    number: int
    data: dict
    status: str = OK
    reason: str = ''
    model: ProductModel | None = None
    colour: VariantOption | None = None
    path: Path | None = None
    view: str = ''
    alt_text: str = ''
    is_primary: bool = False
    sort: int | None = None
    provenance: dict = field(default_factory=dict)
    sha256: str = ''

    @property
    def file(self) -> str:
        return str(self.data.get('file') or '')


@dataclass
class Report:
    manifest_sha256: str
    rows: list[Row]
    dry_run: bool
    imported: int = 0

    @property
    def errors(self) -> list[Row]:
        return [row for row in self.rows if row.status == ERROR]

    @property
    def skipped(self) -> int:
        return sum(row.status == SKIP for row in self.rows)

    @property
    def to_import(self) -> int:
        return sum(row.status == OK for row in self.rows)


# ---------------------------------------------------------------------------
# Reading the manifest
# ---------------------------------------------------------------------------

def read_manifest(path: Path) -> tuple[list[dict], str]:
    """The rows (dicts) and the sha256 of the manifest's bytes. `.json` (a list of objects) or `.csv` (a header)."""
    try:
        if path.stat().st_size > MAX_MANIFEST_BYTES:
            raise ManifestError(f'El manifiesto pesa más de {MAX_MANIFEST_BYTES // (1024 * 1024)} MB.')
        raw = path.read_bytes()
    except OSError:
        raise ManifestError('No se pudo leer el manifiesto.') from None
    digest = hashlib.sha256(raw).hexdigest()
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        raise ManifestError('El manifiesto debe estar en UTF-8.') from None
    suffix = path.suffix.lower()
    if suffix == '.json':
        try:
            body = json.loads(text)
        except ValueError:
            raise ManifestError('El manifiesto JSON no es válido.') from None
        if isinstance(body, dict):
            body = body.get('rows')
        if not isinstance(body, list) or not all(isinstance(item, dict) for item in body):
            raise ManifestError('El manifiesto JSON debe ser una lista de filas (objetos).')
        rows = body
    elif suffix == '.csv':
        reader = csv.DictReader(io.StringIO(text))
        rows = [dict(item) for item in reader]
    else:
        raise ManifestError('El manifiesto debe ser .json o .csv.')
    if not rows:
        raise ManifestError('El manifiesto no tiene filas.')
    if len(rows) > MAX_ROWS:
        raise ManifestError(f'El manifiesto admite hasta {MAX_ROWS} filas.')
    return rows, digest


# ---------------------------------------------------------------------------
# Validating one row
# ---------------------------------------------------------------------------

def _text(value) -> str:
    if value is None:
        return ''
    if isinstance(value, (dict, list)):
        raise RowError('Un campo trae una lista o un objeto donde se espera texto.')
    return str(value).strip()


def _bool(value) -> bool:
    if isinstance(value, bool):
        return value
    text = _text(value).casefold()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    raise RowError('is_primary debe ser verdadero o falso.')


def _sort(value) -> int | None:
    if isinstance(value, bool):
        raise RowError('sort debe ser un número entero desde 0.')
    text = _text(value)
    if not text:
        return None
    if not (text.isascii() and text.isdigit()) or int(text) > 100_000:
        raise RowError('sort debe ser un número entero desde 0.')
    return int(text)


def _view(value) -> str:
    text = _text(value)
    if text.casefold() == 'blank':
        return ''
    try:
        return finish_media.clean_view(text)
    except finish_media.FinishMediaError as exc:
        raise RowError(str(exc)) from None


class _Resolver:
    """Model and colour lookups of ONE company, cached per value."""

    def __init__(self, company):
        self.company = company
        self.dimension = finish_media.colour_attribute_ids(company.pk)
        self._models: dict[str, ProductModel] = {}
        self._colours: dict[int, list[VariantOption]] = {}

    def model(self, value: str) -> ProductModel:
        if not value:
            raise RowError('Falta el modelo.')
        if value not in self._models:
            found = list(ProductModel.objects.filter(company=self.company)
                         .filter(Q(slug=value) | Q(reference_key=value)).distinct()[:2])
            if not found:
                raise RowError(f'Modelo no encontrado en esta empresa: «{value}».')
            if len(found) > 1:
                raise RowError(f'«{value}» nombra a dos modelos (dirección de uno, clave de referencia de otro).')
            self._models[value] = found[0]
        return self._models[value]

    def _candidates(self, model) -> list[VariantOption]:
        if model.pk not in self._colours:
            palette = ModelOption.objects.filter(model=model).values('option_id')
            if ModelOption.objects.filter(model=model).exists():
                rows = list(VariantOption.objects.filter(company_id=model.company_id, pk__in=palette,
                                                         attribute_id__in=self.dimension))
            else:
                rows = [option for option in finish_media.colours_of(model, self.dimension)
                        if option.attribute_id in self.dimension]
            self._colours[model.pk] = rows
        return self._colours[model.pk]

    def colour(self, model, value: str) -> VariantOption:
        """By reference key (`negro-medianoche` or `color:negro-medianoche`), else by label, among THAT model's colours."""
        if not value:
            raise RowError('Falta el color.')
        candidates = self._candidates(model)
        matches = [option for option in candidates
                   if option.reference_key and option.reference_key in (value, f'color:{value}')]
        if not matches:
            folded = ' '.join(value.split()).casefold()
            matches = [option for option in candidates if ' '.join(option.label.split()).casefold() == folded]
        if not matches:
            raise RowError(f'«{value}» no es un color de la paleta de este modelo ({model.name}).')
        if len(matches) > 1:
            raise RowError(f'«{value}» nombra a más de un color de este modelo.')
        try:
            return finish_media.colour_for(model, matches[0].pk)
        except finish_media.FinishMediaError as exc:
            raise RowError(str(exc)) from None


def safe_path(root: Path, value: str) -> Path:
    """`value` as a file INSIDE `root`: relative, no `..`, and its real path (symlinks followed) still inside."""
    if not value:
        raise RowError('Falta el archivo.')
    if '\x00' in value or '\\' in value:
        raise RowError('El nombre del archivo no es válido.')
    if value.startswith('/') or _DRIVE.match(value) or PurePosixPath(value).is_absolute():
        raise RowError('El archivo debe ser una ruta relativa dentro de la carpeta.')
    if '..' in PurePosixPath(value).parts:
        raise RowError('El archivo no puede salir de la carpeta («..»).')
    base = root.resolve()
    try:
        real = (base / value).resolve(strict=True)
    except (OSError, RuntimeError):
        raise RowError(f'No existe el archivo «{value}» en la carpeta.') from None
    if not real.is_relative_to(base):
        raise RowError('El archivo apunta fuera de la carpeta.')
    if not real.is_file():
        raise RowError(f'«{value}» no es un archivo.')
    return real


def read_file(path: Path) -> bytes:
    """The bytes, with the panel's size cap applied BEFORE reading (`storefront_media.read_upload`)."""
    try:
        with path.open('rb') as handle:
            return storefront_media.read_upload(File(handle))
    except storefront_media.StorefrontImageError as exc:
        raise RowError(str(exc)) from None
    except OSError:
        raise RowError('No se pudo leer el archivo.') from None


def _hashes(raw: bytes) -> tuple[str, set[str]]:
    """The source's sha256 and every hash its stored form may have: the source's and its re-encoding's."""
    try:
        processed = storefront_media.process(raw)
    except storefront_media.StorefrontImageError as exc:
        raise RowError(str(exc)) from None
    source = hashlib.sha256(raw).hexdigest()
    return source, {source, hashlib.sha256(processed.content).hexdigest()}


def _recorded_sources(company) -> dict[str, set[str]]:
    """Address → source hashes earlier imports recorded for it (their summary audit rows)."""
    recorded: dict[str, set[str]] = {}
    for metadata in AdminAuditLog.objects.filter(company=company, action=SUMMARY_ACTION).values_list(
            'metadata', flat=True):
        for item in (metadata or {}).get('images') or []:
            if isinstance(item, dict) and item.get('address') and item.get('source_sha256'):
                recorded.setdefault(item['address'], set()).add(item['source_sha256'])
    return recorded


class _Known:
    """Per model: content hash → the colours that already show it (existing pictures and rows accepted so far)."""

    def __init__(self, company):
        self.company = company
        self._by_model: dict[int, dict[str, set[int]]] = {}
        self._recorded = None

    def of(self, model) -> dict[str, set[int]]:
        if model.pk not in self._by_model:
            if self._recorded is None:
                self._recorded = _recorded_sources(self.company)
            known: dict[str, set[int]] = {}
            images = list(finish_media.gallery(model))
            stored = finish_media.stored_of(images)
            for image in images:
                digests = set(self._recorded.get(image.image_url, ()))
                row = stored.get(storefront_media.managed_public_id(image.image_url))
                if row is not None:
                    try:
                        with evidence_storage.open_stream(row.storage_key) as handle:
                            digests.add(hashlib.sha256(handle.read()).hexdigest())
                    except (evidence_storage.EvidenceStorageError, OSError):
                        pass        # an unreadable file cannot be compared; the recorded hash may still be
                for digest in digests:
                    known.setdefault(digest, set()).add(image.color_option_id)
            self._by_model[model.pk] = known
        return self._by_model[model.pk]


def validate(company, rows: list[dict], root: Path) -> list[Row]:
    """Every row checked, in order; nothing is written. Duplicates and limits consider the rows accepted before."""
    resolver, known = _Resolver(company), _Known(company)
    counts: dict[tuple[int, int], int] = {}
    primaries: set[tuple[int, int]] = set()
    limit = finish_media.max_per_colour()
    report = []
    for number, data in enumerate(rows, start=1):
        row = Row(number=number, data=data)
        report.append(row)
        try:
            unknown = sorted(str(key) for key in data if key not in FIELDS)
            if unknown:
                raise RowError(f'Campos desconocidos: {", ".join(unknown)}.')
            row.model = resolver.model(_text(data.get('model')))
            row.colour = resolver.colour(row.model, _text(data.get('color')))
            row.view = _view(data.get('view'))
            row.is_primary = _bool(data.get('is_primary'))
            row.sort = _sort(data.get('sort'))
            try:
                row.alt_text = finish_media.clean_alt_text(_text(data.get('alt_text')))
                row.provenance = finish_media.clean_provenance(
                    {name: _text(data.get(name)) for name in finish_media.PROVENANCE_FIELDS})
            except finish_media.FinishMediaError as exc:
                raise RowError(str(exc)) from None
            row.path = safe_path(root, _text(data.get('file')))
            row.sha256, digests = _hashes(read_file(row.path))

            finish = (row.model.pk, row.colour.pk)
            seen = known.of(row.model)
            colours = set().union(*(seen.get(digest, set()) for digest in digests))
            if row.colour.pk in colours:
                row.status, row.reason = SKIP, 'ese acabado ya tiene esta foto'
            elif colours:
                raise RowError('Esa misma foto ya está en otro color de este modelo.')
            if row.is_primary:
                if finish in primaries:
                    raise RowError('Sólo una foto principal por acabado en el manifiesto.')
                primaries.add(finish)
            if row.status == SKIP:
                continue
            if finish not in counts:
                counts[finish] = finish_media.gallery(row.model, row.colour).count()
            if counts[finish] >= limit:
                raise RowError(f'Un acabado admite hasta {limit} imágenes (contando las que ya tiene).')
            counts[finish] += 1
            for digest in digests:
                seen.setdefault(digest, set()).add(row.colour.pk)
        except RowError as exc:
            row.status, row.reason = ERROR, str(exc)
    return report


# ---------------------------------------------------------------------------
# Importing
# ---------------------------------------------------------------------------

class ManifestChanged(Exception):
    """A file changed between validation and import."""


def run(*, company, manifest: Path, root: Path, actor, dry_run: bool) -> Report:
    rows, digest = read_manifest(manifest)
    report = Report(manifest_sha256=digest, rows=validate(company, rows, root), dry_run=dry_run)
    if dry_run or report.errors or not report.to_import:
        return report

    pending = sorted((row for row in report.rows if row.status == OK),
                     key=lambda row: (row.model.pk, row.colour.pk, row.sort is None, row.sort or 0, row.number))
    addresses: list[str] = []
    try:
        stored = []
        for row in pending:
            raw = read_file(row.path)
            if hashlib.sha256(raw).hexdigest() != row.sha256:
                raise ManifestChanged(f'El archivo de la fila {row.number} cambió durante la importación.')
            image = storefront_media.store(company=company, actor=actor, raw=raw, provenance=row.provenance)
            addresses.append(image.url)
            stored.append((row, image))
        with transaction.atomic():
            placed = []
            for row, image in stored:
                finish_image = finish_media.place(model=row.model, colour=row.colour, address=image.url, actor=actor,
                                                  alt_text=row.alt_text, view=row.view)
                if row.is_primary and not finish_image.is_primary:
                    finish_image = finish_media.update_image(image=finish_image, actor=actor, is_primary=True)
                placed.append({'row': row.number, 'image_id': finish_image.pk, 'model_id': row.model.pk,
                               'color_option_id': row.colour.pk, 'address': image.url, 'source_sha256': row.sha256})
            AdminAuditLog.log(
                actor=actor, action=SUMMARY_ACTION, target_type='company', target_id=company.pk, company=company,
                metadata={'manifest_sha256': digest, 'manifest_name': manifest.name[:120], 'rows': len(report.rows),
                          'imported': len(placed), 'skipped': report.skipped, 'images': placed},
            )
    except Exception:
        # Stored but never placed: nothing of a failed run is left behind.
        storefront_media.release(addresses, company=company, actor=actor, reason='never_placed')
        raise
    report.imported = len(placed)
    return report
