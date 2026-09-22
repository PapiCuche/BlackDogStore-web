"""
El nombre del archivo y el ZIP que espera SUNAT.

SUNAT NO ACEPTA UN NOMBRE CUALQUIERA. El archivo se llama de una forma exacta y
el servicio lo usa para identificar el documento antes siquiera de abrirlo. Un
`factura.xml` dentro de un `envio.zip` se rechaza sin llegar al validador.

    RUC(11) - TIPO(2) - SERIE(4) - CORRELATIVO(1..8) . XML   →  y el ZIP igual

Ejemplo del Manual del programador: `20100066603-01-F001-1.ZIP`.

Del Anexo N.º 6 vigente desde el 1.8.2026, numeral 6.1.3.b.1: posiciones 16-19 la
serie, «se espera que el primer carácter sea la constante F, B, C, L o G según
corresponda, seguido por tres caracteres alfanuméricos»; posiciones 21-28 el
correlativo, «mínimo 1 y máximo 8 dígitos».

EL CORRELATIVO VA SIN CEROS A LA IZQUIERDA en el nombre. El ejemplo oficial
termina en `-1`, no en `-00000001`. Rellenar a ocho dígitos produce un nombre que
SUNAT no reconoce como el mismo documento.
"""

from __future__ import annotations

import io
import re
import zipfile

#: Lo que SUNAT devuelve: el CDR viene en un ZIP y dentro se llama
#: `R-<nombre del archivo enviado sin extensión>.xml`.
CDR_PREFIX = 'R-'

#: Tope del tamaño DESCOMPRIMIDO de la entrada del CDR. Un CDR real es XML de
#: kilobytes; un ZIP externo (input no confiable) no debe poder inflar memoria
#: sin límite (zip bomb). Se valida `ZipInfo.file_size` ANTES de leer.
MAX_CDR_MEMBER_BYTES = 5 * 1024 * 1024

_NAME_RE = re.compile(r'^\d{11}-\d{2}-[A-Z0-9]{4}-\d{1,8}$')

#: Nombre del Resumen Diario (RC): `<RUC>-RC-<YYYYMMDD>-<correlativo>`. El Anexo
#: N.º 6 (6.1.3) admite correlativo de 1 a 5 dígitos, SIN relleno a un ancho fijo
#: (los ejemplos oficiales muestran `-1` y `-900`). La fecha es la de GENERACIÓN
#: del resumen.
_SUMMARY_NAME_RE = re.compile(r'^\d{11}-RC-\d{8}-\d{1,5}$')


def document_name(ruc: str, document_type: str, serie: str, correlativo: int) -> str:
    """
    El identificador del archivo, sin extensión.

    Se valida el resultado en vez de confiar en quien llama: este nombre viaja
    en la petición SOAP y un carácter de más lo convierte en un rechazo que
    llega por la red en vez de aquí.
    """
    name = f'{ruc}-{document_type}-{serie}-{correlativo}'
    if not _NAME_RE.fullmatch(name):
        raise ValueError(
            f'Nombre de archivo fuera de la nomenclatura del Anexo N.º 6: {name!r}'
        )
    return name


def summary_identifier(document_date, correlativo: int) -> str:
    """
    El `cbc:ID` del resumen: `RC-<YYYYMMDD>-<correlativo>`.

    `document_date` es la fecha de referencia (emisión de las boletas). El id y el
    nombre del archivo comparten esa fecha, y el correlativo es único por RUC y
    esa fecha. (SUNAT admite variantes que usan la fecha de generación; se elige
    la de referencia para que id, nombre y la restricción única concuerden, y se
    confirma contra BETA.)
    """
    return f'RC-{document_date.strftime("%Y%m%d")}-{correlativo}'


def summary_name(ruc: str, document_date, correlativo: int) -> str:
    """
    El nombre del archivo del Resumen Diario, sin extensión.

    Se valida el resultado en vez de confiar en quien llama: este nombre viaja en
    la petición SOAP y un carácter de más lo convierte en un rechazo que llega por
    la red en vez de aquí.
    """
    name = f'{ruc}-RC-{document_date.strftime("%Y%m%d")}-{correlativo}'
    if not _SUMMARY_NAME_RE.fullmatch(name):
        raise ValueError(
            f'Nombre de resumen fuera de la nomenclatura del Anexo N.º 6: {name!r}'
        )
    return name


def build_zip(name: str, xml: bytes) -> bytes:
    """
    El ZIP con UN solo XML dentro, sin carpetas.

    SIN RUTAS INTERNAS, y no por elegancia: un nombre con `../` dentro de un ZIP
    es la vulnerabilidad conocida como zip-slip, y aunque aquí el nombre lo
    generamos nosotros, la garantía tiene que estar del lado que escribe. El
    validador de `document_name` ya impide que un separador llegue hasta aquí.
    """
    if '/' in name or '\\' in name or '..' in name:
        raise ValueError(f'El nombre no puede contener rutas: {name!r}')

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f'{name}.XML', xml)
    return buffer.getvalue()


def extract_cdr(zip_bytes: bytes) -> tuple[str, bytes]:
    """
    Saca el XML del CDR del ZIP que devuelve SUNAT.

    Devuelve (nombre, contenido). Se rechaza lo que no encaje en vez de tomar el
    primer archivo que aparezca: un ZIP con varias entradas, o con una entrada
    que apunta fuera del directorio, no es un CDR — es algo que hay que mirar.
    """
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        names = [n for n in archive.namelist() if not n.endswith('/')]
        if len(names) != 1:
            raise ValueError(
                f'Se esperaba un solo archivo en el CDR; llegaron {len(names)}: {names}'
            )
        name = names[0]
        if '/' in name or '\\' in name or '..' in name:
            raise ValueError(f'Entrada de ZIP con ruta, se rechaza: {name!r}')
        # Tope de tamaño descomprimido ANTES de leer: un ZIP externo no debe
        # poder inflar memoria sin límite (zip bomb). El file_size del encabezado
        # se contrasta con lo realmente leído por si el encabezado mintiera.
        info = archive.getinfo(name)
        if info.file_size > MAX_CDR_MEMBER_BYTES:
            raise ValueError(
                f'Entrada de CDR demasiado grande: {info.file_size} bytes '
                f'(máximo {MAX_CDR_MEMBER_BYTES}).'
            )
        with archive.open(name) as member:
            data = member.read(MAX_CDR_MEMBER_BYTES + 1)
        if len(data) > MAX_CDR_MEMBER_BYTES:
            raise ValueError('Entrada de CDR excede el tope al descomprimir.')
        return name, data
