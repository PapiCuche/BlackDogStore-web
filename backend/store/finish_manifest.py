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
one summary audit row. If anything fails — an interruption included — every file stored by the run is released
(`storefront_media.release`), as `finish_media.add_image` does for one. Under each model's lock the duplicate check is
repeated, so a picture placed meanwhile (another run, the panel) is skipped there, or refuses the run if it is the same
photo in another colour.

FILES. Every component of a row's path is opened from a descriptor of `--dir` WITHOUT FOLLOWING LINKS (`O_NOFOLLOW`):
there is no window between checking a path and opening it. The file's identity (device, inode) and hash are remembered
and checked again when it is read for storing.

IDEMPOTENCY WITHOUT A NEW COLUMN. A stored file is a re-encoding, so its bytes are not the source file's. A row is a
duplicate when its source hash or the hash of its re-encoding equals the hash of a file that finish already shows (read
through the storage API), or the source hash the summary audit recorded for that picture (which still holds if the
encoder's output changes between runs).
"""
from __future__ import annotations

import csv
import errno
import hashlib
import io
import json
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

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
    identity: tuple[int, int] | None = None
    digests: set = field(default_factory=set)
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


def _parts(value: str) -> tuple[str, ...]:
    """`value` as path components inside `--dir`: relative, no `..`, no backslash."""
    if not value:
        raise RowError('Falta el archivo.')
    if '\x00' in value or '\\' in value:
        raise RowError('El nombre del archivo no es válido.')
    if value.startswith('/') or _DRIVE.match(value) or PurePosixPath(value).is_absolute():
        raise RowError('El archivo debe ser una ruta relativa dentro de la carpeta.')
    parts = PurePosixPath(value).parts
    if '..' in parts:
        raise RowError('El archivo no puede salir de la carpeta («..»).')
    if not parts:
        raise RowError('Falta el archivo.')
    return parts


def _open_error(exc: OSError, value: str) -> RowError:
    if exc.errno in (errno.ELOOP, errno.EMLINK):
        return RowError('El archivo pasa por un enlace simbólico: no se siguen enlaces.')
    if exc.errno == errno.ENOENT:
        return RowError(f'No existe el archivo «{value}» en la carpeta.')
    if exc.errno == errno.ENOTDIR:
        return RowError(f'No existe el archivo «{value}» en la carpeta (o pasa por un enlace simbólico).')
    return RowError('No se pudo leer el archivo.')


def open_inside(root: Path, value: str) -> int:
    """
    A descriptor of the regular file `value` INSIDE `root`, opened component by component from a descriptor of `root`
    with `O_NOFOLLOW`: no link is followed (in or out of the folder), so nothing can be swapped between a check and
    the open. Non-blocking, so a FIFO is refused instead of waiting.
    """
    parts = _parts(value)
    nofollow = getattr(os, 'O_NOFOLLOW', 0)
    directory = getattr(os, 'O_DIRECTORY', 0)
    try:
        current = os.open(root, os.O_RDONLY | directory)
    except OSError:
        raise RowError('No se pudo abrir la carpeta.') from None
    try:
        for part in parts[:-1]:
            following = os.open(part, os.O_RDONLY | directory | nofollow, dir_fd=current)
            os.close(current)
            current = following
        descriptor = os.open(parts[-1], os.O_RDONLY | nofollow | getattr(os, 'O_NONBLOCK', 0), dir_fd=current)
    except OSError as exc:
        raise _open_error(exc, value) from None
    finally:
        os.close(current)
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise RowError(f'«{value}» no es un archivo.')
    return descriptor


class _Sized:
    """What `storefront_media.read_upload` reads: the size from `fstat` (cap before reading) and `read`."""

    def __init__(self, handle, size):
        self.size, self.read = size, handle.read


def read_inside(root: Path, value: str) -> tuple[bytes, tuple[int, int]]:
    """The bytes (with the panel's size cap applied BEFORE reading) and the file's identity (device, inode)."""
    descriptor = open_inside(root, value)
    with os.fdopen(descriptor, 'rb') as handle:
        info = os.fstat(handle.fileno())
        try:
            raw = storefront_media.read_upload(_Sized(handle, info.st_size))
        except storefront_media.StorefrontImageError as exc:
            raise RowError(str(exc)) from None
        except OSError:
            raise RowError('No se pudo leer el archivo.') from None
    return raw, (info.st_dev, info.st_ino)


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


UNREADABLE = ('No se pudo leer una foto que ya tiene este modelo para compararla con esta: revisa su galería en el '
              'panel antes de importar.')


class _Known:
    """
    Per model: content hash → the colours that already show it (existing pictures and rows accepted so far).

    A picture of the model whose file cannot be read, and that no earlier import recorded, cannot be compared: every
    row of that model is refused (`UNREADABLE`) rather than risk a duplicate or another colour's photo.
    """

    def __init__(self, company):
        self.company = company
        self._by_model: dict[int, dict[str, set[int]]] = {}
        self._unreadable: set[int] = set()
        self._recorded = None

    def of(self, model) -> dict[str, set[int]]:
        if model.pk in self._unreadable:
            raise RowError(UNREADABLE)
        if model.pk not in self._by_model:
            if self._recorded is None:
                self._recorded = _recorded_sources(self.company)
            known: dict[str, set[int]] = {}
            images = list(finish_media.gallery(model))
            stored = finish_media.stored_of(images)
            for image in images:
                recorded = set(self._recorded.get(image.image_url, ()))
                digests = set(recorded)
                row = stored.get(storefront_media.managed_public_id(image.image_url))
                read = False
                if row is not None:
                    try:
                        with evidence_storage.open_stream(row.storage_key) as handle:
                            digests.add(hashlib.sha256(handle.read()).hexdigest())
                        read = True
                    except (evidence_storage.EvidenceStorageError, OSError):
                        pass
                if not read and not recorded:
                    self._unreadable.add(model.pk)
                    raise RowError(UNREADABLE)
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
            raw, row.identity = read_inside(root, _text(data.get('file')))
            row.sha256, row.digests = _hashes(raw)
            del raw

            finish = (row.model.pk, row.colour.pk)
            seen = known.of(row.model)
            if _duplicate(seen, row):
                row.status, row.reason = SKIP, 'ese acabado ya tiene esta foto'
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
            _remember(seen, row)
        except RowError as exc:
            row.status, row.reason = ERROR, str(exc)
    return report


def _duplicate(seen: dict[str, set[int]], row: Row) -> bool:
    """True when that finish already shows this content; another colour of the model showing it is an error."""
    colours = set().union(*(seen.get(digest, set()) for digest in row.digests))
    if row.colour.pk in colours:
        return True
    if colours:
        raise RowError('Esa misma foto ya está en otro color de este modelo.')
    return False


def _remember(seen: dict[str, set[int]], row: Row) -> None:
    for digest in row.digests:
        seen.setdefault(digest, set()).add(row.colour.pk)


# ---------------------------------------------------------------------------
# Importing
# ---------------------------------------------------------------------------

class _Refused(Exception):
    """A row that stopped being importable after validation: the run is undone and that row reported."""

    def __init__(self, row: Row, reason: str):
        super().__init__(reason)
        self.row, self.reason = row, reason


def _read_again(root: Path, row: Row) -> bytes:
    """The row's file again — the same file (device, inode) with the same bytes it had when validated."""
    try:
        raw, identity = read_inside(root, row.file)
    except RowError as exc:
        raise _Refused(row, str(exc)) from None
    if identity != row.identity or hashlib.sha256(raw).hexdigest() != row.sha256:
        raise _Refused(row, 'El archivo cambió después de validarlo: vuelve a ejecutar el manifiesto.')
    return raw


def _place_under_lock(company, stored, actor) -> tuple[list[dict], list[str]]:
    """
    Places the stored files, one model at a time: lock the model, compute its gallery's hashes AGAIN (a picture may
    have been placed since validation) and skip a row that finish now shows, or refuse the run when another colour
    does. Returns the placed pictures and the addresses of the files stored for rows skipped here.
    """
    placed, skipped = [], []
    known = _Known(company)
    locked: set[int] = set()
    for row, image in stored:
        if row.model.pk not in locked:
            ProductModel.objects.select_for_update().get(pk=row.model.pk, company=company)
            locked.add(row.model.pk)
        try:
            seen = known.of(row.model)
            if _duplicate(seen, row):
                row.status, row.reason = SKIP, 'ese acabado ya tenía esta foto al importar'
                skipped.append(image.url)
                continue
        except RowError as exc:
            raise _Refused(row, str(exc)) from None
        _remember(seen, row)
        finish_image = finish_media.place(model=row.model, colour=row.colour, address=image.url, actor=actor,
                                          alt_text=row.alt_text, view=row.view)
        if row.is_primary and not finish_image.is_primary:
            finish_image = finish_media.update_image(image=finish_image, actor=actor, is_primary=True)
        placed.append({'row': row.number, 'image_id': finish_image.pk, 'model_id': row.model.pk,
                       'color_option_id': row.colour.pk, 'address': image.url, 'source_sha256': row.sha256})
    return placed, skipped


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
            raw = _read_again(root, row)
            image = storefront_media.store(company=company, actor=actor, raw=raw, provenance=row.provenance)
            addresses.append(image.url)
            stored.append((row, image))
        with transaction.atomic():
            placed, skipped = _place_under_lock(company, stored, actor)
            if placed:
                AdminAuditLog.log(
                    actor=actor, action=SUMMARY_ACTION, target_type='company', target_id=company.pk,
                    company=company,
                    metadata={'manifest_sha256': digest, 'manifest_name': manifest.name[:120],
                              'rows': len(report.rows), 'imported': len(placed), 'skipped': report.skipped,
                              'images': placed},
                )
    except _Refused as refused:
        storefront_media.release(addresses, company=company, actor=actor, reason='never_placed')
        refused.row.status, refused.row.reason = ERROR, refused.reason
        return report
    except BaseException:
        # Stored but never placed — an interruption included: nothing of a failed run is left behind.
        storefront_media.release(addresses, company=company, actor=actor, reason='never_placed')
        raise
    # Files stored for rows that turned out to be duplicates under the lock: nothing shows them.
    storefront_media.release(skipped, company=company, actor=actor, reason='never_placed')
    report.imported = len(placed)
    return report
