"""
BULK-MEDIA — los archivos de imagen que acompañan a una carga masiva.

DOS FORMAS DE LLEGAR, UN SOLO RESULTADO. Las imágenes viajan con el libro como
varios archivos sueltos o dentro de un ZIP. De cualquiera de las dos sale un
`Bundle`: nombres de archivo y la forma de leer cada uno. Aquí no se decide qué
producto lleva qué imagen —eso lo dice cada fila del libro— ni si un archivo es
de verdad una imagen: eso lo decide la tubería de imágenes al procesarlo.

EL NOMBRE ES LO ÚNICO QUE SE COMPARA. Una fila dice `telefono.png` y se busca un
archivo que se llame así, sin mirar mayúsculas ni la carpeta en que venga. Dos
archivos distintos con el mismo nombre no se adivinan: la fila que los cite
recibe un error.

EL ZIP NO SE EXTRAE. Nunca se escribe en disco con las rutas que trae: se lee
entrada por entrada, en memoria y con tope. Y antes de leer nada se revisa la
lista entera, porque lo peligroso de un ZIP se ve en su índice:

  * rutas que salen del archivo (`../`, absolutas, con unidad de disco);
  * enlaces simbólicos;
  * entradas cifradas;
  * demasiadas entradas;
  * un tamaño expandido que no guarda proporción con el comprimido.

Una sola de esas y se rechaza el ZIP completo: no es un error de una fila, es
un archivo que no debió llegar.
"""
from __future__ import annotations

import hashlib
import unicodedata
import zipfile
from dataclasses import dataclass, field
from typing import Callable

from django.conf import settings

from . import storefront_media

#: Lo que separa varios nombres en la columna «Imágenes».
SEPARATOR = '|'

#: Por extensión sólo se decide qué entra al lote; que sea una imagen lo dice
#: el decodificador.
IMAGE_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.webp')

#: Lo que los sistemas de archivos dejan dentro de un ZIP sin que nadie lo pida.
_JUNK_NAMES = {'.ds_store', 'thumbs.db', 'desktop.ini'}


class ImportMediaError(Exception):
    """El lote de imágenes no se puede usar. `too_large` -> 413."""

    def __init__(self, message: str, *, too_large: bool = False):
        super().__init__(message)
        self.too_large = too_large


def _setting(name: str, default: int) -> int:
    return int(getattr(settings, name, default))


def max_files() -> int:
    return _setting('IMPORT_IMAGES_MAX_FILES', 200)


def max_total_bytes() -> int:
    return _setting('IMPORT_IMAGES_MAX_TOTAL_BYTES', 100 * 1024 * 1024)


def zip_max_entries() -> int:
    return _setting('IMPORT_IMAGES_ZIP_MAX_ENTRIES', 400)


def zip_max_ratio() -> int:
    """Cuántas veces puede crecer una entrada al expandirse."""
    return _setting('IMPORT_IMAGES_ZIP_MAX_RATIO', 200)


def normalize_name(name) -> str:
    """El nombre con el que se comparan archivo y fila: sin carpeta ni mayúsculas."""
    text = unicodedata.normalize('NFC', str(name or '')).replace('\\', '/')
    return text.rsplit('/', 1)[-1].strip().casefold()


def display_name(name) -> str:
    text = unicodedata.normalize('NFC', str(name or '')).replace('\\', '/')
    return text.rsplit('/', 1)[-1].strip()


def split_references(main, many) -> list[str]:
    """
    Los archivos que una fila cita, en orden y sin repetir.

    La principal va primero aunque en «Imágenes» aparezca después: es la que la
    tienda mostrará en el catálogo.
    """
    names = [display_name(main)] if str(main or '').strip() else []
    names += [display_name(part) for part in str(many or '').split(SEPARATOR)]
    seen, ordered = set(), []
    for name in names:
        key = normalize_name(name)
        if key and key not in seen:
            seen.add(key)
            ordered.append(name)
    return ordered


@dataclass
class Source:
    """Un archivo del lote y cómo leerlo."""
    name: str
    declared_size: int
    reader: Callable[[int], bytes]

    def read(self) -> bytes:
        limit = storefront_media.max_upload_bytes()
        megabytes = max(1, limit // (1024 * 1024))
        if self.declared_size > limit:
            raise ImportMediaError(f'pesa más de {megabytes} MB.')
        # Un byte más de lo declarado: si aparece, la cabecera mentía.
        raw = self.reader(self.declared_size + 1)
        if len(raw) != self.declared_size:
            raise ImportMediaError('su tamaño no coincide con el que declara el archivo.')
        return raw


@dataclass
class Bundle:
    _by_name: dict[str, list[Source]] = field(default_factory=dict)
    #: Lo que venía en el lote y no es una imagen por su extensión.
    ignored: list[str] = field(default_factory=list)
    _digests: dict[int, str] = field(default_factory=dict)

    def add(self, source: Source) -> None:
        self._by_name.setdefault(normalize_name(source.name), []).append(source)

    def __len__(self) -> int:
        return sum(len(sources) for sources in self._by_name.values())

    def names(self) -> list[str]:
        return list(self._by_name)

    def sources(self, name) -> list[Source]:
        return self._by_name.get(normalize_name(name), [])

    def __contains__(self, name) -> bool:
        return normalize_name(name) in self._by_name

    def display(self, name) -> str:
        sources = self.sources(name)
        return sources[0].name if sources else display_name(name)

    def read(self, name) -> bytes:
        return self.sources(name)[0].read()

    def ambiguous(self, name) -> bool:
        """Dos archivos DISTINTOS con el mismo nombre. Dos copias iguales no lo son."""
        sources = self.sources(name)
        if len(sources) < 2:
            return False
        digests = set()
        for source in sources:
            try:
                digests.add(hashlib.sha256(source.read()).hexdigest())
            except ImportMediaError:
                return True
        return len(digests) > 1


def _is_image_name(name: str) -> bool:
    return normalize_name(name).endswith(IMAGE_EXTENSIONS)


def _is_junk(path: str) -> bool:
    parts = [part for part in path.replace('\\', '/').split('/') if part]
    if not parts:
        return True
    leaf = parts[-1].casefold()
    return '__macosx' in (part.casefold() for part in parts) or leaf in _JUNK_NAMES or leaf.startswith('._')


def _unsafe_path(path: str) -> bool:
    """Una ruta que, extraída, caería fuera de la carpeta de destino."""
    if not path or '\x00' in path:
        return True
    normalized = path.replace('\\', '/')
    if normalized.startswith('/') or (len(normalized) > 1 and normalized[1] == ':'):
        return True
    return any(part == '..' for part in normalized.split('/'))


def _uploaded_reader(uploaded):
    def read(limit: int) -> bytes:
        uploaded.seek(0)
        return uploaded.read(limit)
    return read


def _zip_reader(archive: zipfile.ZipFile, info: zipfile.ZipInfo):
    def read(limit: int) -> bytes:
        try:
            with archive.open(info) as member:
                return member.read(limit)
        except (zipfile.BadZipFile, RuntimeError, OSError, EOFError, NotImplementedError):
            raise ImportMediaError('no se pudo leer dentro del ZIP.') from None
    return read


def _megabytes(value: int) -> int:
    return max(1, value // (1024 * 1024))


def _add_zip(bundle: Bundle, uploaded, *, budget: int) -> int:
    """Añade las imágenes del ZIP. Devuelve los bytes expandidos que suman."""
    if (getattr(uploaded, 'size', 0) or 0) > max_total_bytes():
        raise ImportMediaError(
            f'El ZIP pesa más de {_megabytes(max_total_bytes())} MB.', too_large=True,
        )
    try:
        archive = zipfile.ZipFile(uploaded)
        members = archive.infolist()
    except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError, ValueError):
        raise ImportMediaError('El archivo ZIP no se puede leer. Vuelve a crearlo.') from None

    files = [info for info in members if not info.is_dir()]
    if len(files) > zip_max_entries():
        raise ImportMediaError(
            f'El ZIP trae {len(files)} archivos; se admiten hasta {zip_max_entries()}.'
        )

    total = 0
    accepted = []
    for info in files:
        if _unsafe_path(info.filename):
            raise ImportMediaError(
                'El ZIP contiene rutas que salen de su carpeta. No se usa ningún archivo de él.'
            )
        if (info.external_attr >> 16) & 0o170000 == 0o120000:
            raise ImportMediaError(
                'El ZIP contiene enlaces simbólicos. No se usa ningún archivo de él.'
            )
        if info.flag_bits & 0x1:
            raise ImportMediaError('El ZIP está protegido con contraseña. Súbelo sin cifrar.')
        if _is_junk(info.filename):
            continue
        if not _is_image_name(info.filename):
            bundle.ignored.append(display_name(info.filename))
            continue
        total += info.file_size
        if total > budget:
            raise ImportMediaError(
                f'Las imágenes del ZIP ocupan más de {_megabytes(max_total_bytes())} MB al '
                f'descomprimirse.', too_large=True,
            )
        # Una entrada de unos KB que dice ocupar megas no es una foto comprimida.
        if info.file_size > 1024 * 1024 and info.file_size > max(info.compress_size, 1) * zip_max_ratio():
            raise ImportMediaError(
                'El ZIP contiene un archivo que se expande de forma desproporcionada. '
                'No se usa ningún archivo de él.'
            )
        accepted.append(info)

    for info in accepted:
        bundle.add(Source(
            name=display_name(info.filename), declared_size=info.file_size,
            reader=_zip_reader(archive, info),
        ))
    return total


def collect(files, zip_upload=None) -> Bundle:
    """
    El lote de imágenes de una petición. Lanza `ImportMediaError` si el lote
    entero no se puede usar; lo que esté mal en UN archivo se descubre al leerlo.
    """
    bundle = Bundle()
    total = 0
    for uploaded in files or []:
        name = getattr(uploaded, 'name', '') or ''
        if not _is_image_name(name):
            bundle.ignored.append(display_name(name))
            continue
        size = int(getattr(uploaded, 'size', 0) or 0)
        total += size
        bundle.add(Source(name=display_name(name), declared_size=size, reader=_uploaded_reader(uploaded)))
        if len(bundle) > max_files():
            raise ImportMediaError(
                f'Se adjuntaron demasiadas imágenes; se admiten hasta {max_files()} por importación.'
            )
        if total > max_total_bytes():
            raise ImportMediaError(
                f'Las imágenes adjuntas pesan más de {_megabytes(max_total_bytes())} MB en total.',
                too_large=True,
            )

    if zip_upload is not None:
        total += _add_zip(bundle, zip_upload, budget=max_total_bytes() - total)
        if len(bundle) > max_files():
            raise ImportMediaError(
                f'Se adjuntaron demasiadas imágenes; se admiten hasta {max_files()} por importación.'
            )
    return bundle


class Stager:
    """
    Guarda, una sola vez, cada imagen que alguna fila va a usar.

    DÓNDE ESPERAN LAS IMÁGENES ENTRE PREVISUALIZAR Y APLICAR. En la misma
    tubería que cualquier imagen de la tienda, como imágenes «sin colocar» de la
    empresa del trabajo. No hay un almacén temporal aparte: aplicar las coloca
    en la galería del producto, y si el trabajo se abandona, la limpieza diaria
    de imágenes sin colocar las retira (24 horas por defecto).
    """

    def __init__(self, *, company, actor, bundle: Bundle | None):
        self.company = company
        self.actor = actor
        self.bundle = bundle
        self.staged: dict[str, storefront_media.StorefrontImage] = {}   # sha256 -> imagen
        self._by_name: dict[str, dict] = {}                              # nombre -> resultado
        self.referenced: set[str] = set()
        self.missing: set[str] = set()
        self.invalid: dict[str, str] = {}
        self.duplicates = 0

    def resolve(self, name: str) -> dict:
        """`{'sha256', 'address'}` o `{'error': motivo}` para el archivo `name`."""
        key = normalize_name(name)
        self.referenced.add(key)
        if key in self._by_name:
            return self._by_name[key]
        result = self._resolve(name, key)
        self._by_name[key] = result
        return result

    def _resolve(self, name: str, key: str) -> dict:
        if self.bundle is None or key not in self.bundle:
            self.missing.add(key)
            return {'error': 'no está entre los archivos adjuntos.'}
        if self.bundle.ambiguous(key):
            self.invalid[key] = 'ambiguo'
            return {'error': 'hay más de un archivo con ese nombre y no son iguales. Renómbralos.'}
        try:
            raw = self.bundle.read(key)
        except ImportMediaError as exc:
            self.invalid[key] = str(exc)
            return {'error': str(exc)}
        digest = hashlib.sha256(raw).hexdigest()
        if digest in self.staged:
            # El mismo contenido con otro nombre: se guarda una vez.
            self.duplicates += 1
            return {'sha256': digest, 'address': self.staged[digest].url}
        try:
            image = storefront_media.store(
                company=self.company, actor=self.actor, raw=raw, audit=False,
            )
        except storefront_media.StorefrontImageError as exc:
            reason = str(exc)
            reason = reason[0].lower() + reason[1:] if reason else 'no se pudo leer.'
            self.invalid[key] = reason
            return {'error': reason}
        self.staged[digest] = image
        return {'sha256': digest, 'address': image.url}

    def discard(self) -> None:
        """El trabajo no se podrá aplicar: lo guardado sobra desde ya."""
        if self.staged:
            storefront_media.release(
                [image.url for image in self.staged.values()], company=self.company,
                actor=self.actor, reason='import_not_applicable',
            )
        self.staged = {}
        for result in self._by_name.values():
            if 'address' in result:
                result['address'] = None

    def abandon(self) -> None:
        """La previsualización falló y su transacción se deshizo: quedan los archivos."""
        from . import evidence_storage
        for image in self.staged.values():
            evidence_storage.delete_quietly(image.storage_key)

    def summary(self) -> dict:
        attached = len(self.bundle) if self.bundle is not None else 0
        names = set(self.bundle.names()) if self.bundle is not None else set()
        orphans = sorted(names - self.referenced)
        display = (lambda key: self.bundle.display(key)) if self.bundle is not None else (lambda key: key)
        return {
            'attached': attached,
            'referenced': len(self.referenced),
            'valid': len(self.referenced) - len(self.missing) - len(self.invalid),
            'invalid': len(self.invalid),
            'missing': len(self.missing),
            'duplicates': self.duplicates,
            'orphans': len(orphans),
            'orphan_names': [display(key) for key in orphans[:25]],
            'ignored': len(self.bundle.ignored) if self.bundle is not None else 0,
            'ignored_names': (self.bundle.ignored[:25] if self.bundle is not None else []),
            'already_present': 0,
            'staged': len(self.staged),
            'separator': SEPARATOR,
        }
