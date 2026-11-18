"""
What a thermal roll SAYS, read back from its ESC/POS bytes (DOC-PREVIEW-ESCPOS).

The goldens of the rolls (`test_document_goldens`, DOC-SAFETY-01) and the thermal
preview of Ajustes › Documentos read the bytes with this one reader: what the
preview shows is what the printer receives, line by line, with its style.

    read(data)        -> [CodePage | Line | Feed | Cut]
    lines(data)       -> the golden form: one string per printed line, markers for the style
    text(data)        -> the printed words only
    simulation(data)  -> JSON-ready blocks for the preview (text, image as a 1-bit PNG, QR, feed, cut)

STRICT ON PURPOSE. Only the commands `escpos.Receipt` emits are known. Anything
else, or a command cut short, raises `EscposReadError`: a new command is a
decision of this module and never silently becomes text. A QR stored and never
printed (or printed with nothing stored) is an error too.
"""
from __future__ import annotations

import base64
import io
import re
from dataclasses import dataclass

ALIGNS = {0: 'left', 1: 'center', 2: 'right'}


class EscposReadError(ValueError):
    """The bytes hold something `escpos.Receipt` never emits."""


@dataclass(frozen=True)
class Raster:
    """A `GS v 0` image: `width` in dots (whole bytes), `bits` packed rows, 1 = ink."""
    width: int
    height: int
    bits: bytes

    def png(self) -> bytes:
        from PIL import Image

        image = Image.frombytes('1', (self.width, self.height), self.bits, 'raw', '1;I')
        out = io.BytesIO()
        image.save(out, 'PNG', optimize=True)
        return out.getvalue()


@dataclass(frozen=True)
class QR:
    payload: str


@dataclass(frozen=True)
class Line:
    """One printed line: text and/or images, in the style in force when its first piece was printed."""
    pieces: tuple
    align: str = 'left'
    bold: bool = False
    large: bool = False


@dataclass(frozen=True)
class CodePage:
    number: int


@dataclass(frozen=True)
class Feed:
    lines: int


@dataclass(frozen=True)
class Cut:
    pass


def read(data: bytes, encoding: str = 'cp858') -> list:
    try:
        return _read(data, encoding)
    except IndexError as exc:
        raise EscposReadError('a command is cut short') from exc


def _read(data: bytes, encoding: str) -> list:
    items: list = []
    pieces: list = []
    text = bytearray()
    style = None                          # the style of the current line, once it has content
    align, bold, large = 'left', False, False
    qr = None                             # a payload stored and not printed yet

    def put(piece=None, raw: bytes = b'') -> None:
        nonlocal style
        if style is None:
            style = (align, bold, large)
        if raw:
            text.extend(raw)
        else:
            flush()
            pieces.append(piece)

    def flush() -> None:
        if text:
            pieces.append(text.decode(encoding))
            text.clear()

    def end_line() -> None:
        nonlocal style
        flush()
        line_align, line_bold, line_large = style or ('left', False, False)
        items.append(Line(tuple(pieces), line_align, line_bold, line_large))
        pieces.clear()
        style = None

    i, size = 0, len(data)
    while i < size:
        byte = data[i]
        command = data[i + 1:i + 2]
        if byte == 0x1B:                                           # ESC
            if command == b'@':
                i += 2
            elif command == b't':
                items.append(CodePage(data[i + 2]))
                i += 3
            elif command == b'a':
                if data[i + 2] not in ALIGNS:
                    raise EscposReadError(f'unknown alignment {data[i + 2]} at byte {i}')
                align = ALIGNS[data[i + 2]]
                i += 3
            elif command == b'E':
                bold = bool(data[i + 2])
                i += 3
            elif command == b'd':
                items.append(Feed(data[i + 2]))
                i += 3
            else:
                raise EscposReadError(f'unknown ESC command {command!r} at byte {i}')
        elif byte == 0x1D:                                         # GS
            if command == b'!':
                large = bool(data[i + 2])
                i += 3
            elif command == b'V':
                if i + 4 > size:
                    raise EscposReadError(f'a cut at byte {i} is cut short')
                items.append(Cut())
                i += 4
            elif command == b'(' and data[i + 2:i + 3] == b'k':      # QR: GS ( k pL pH cn fn …
                length = data[i + 3] | data[i + 4] << 8
                function = data[i + 6]
                if i + 5 + length > size:
                    raise EscposReadError(f'a QR command at byte {i} is cut short')
                if function == 0x50:                               # store the payload
                    if qr is not None:
                        raise EscposReadError(f'a QR stored at byte {i} over one never printed')
                    qr = bytes(data[i + 8:i + 5 + length]).decode('utf-8')
                elif function == 0x51:                             # print it
                    if qr is None:
                        raise EscposReadError(f'a QR printed at byte {i} with no payload stored')
                    put(QR(qr))
                    qr = None
                i += 5 + length
            elif command == b'v' and data[i + 2:i + 3] == b'0':      # raster: GS v 0 m xL xH yL yH …
                width_bytes = data[i + 4] | data[i + 5] << 8
                height = data[i + 6] | data[i + 7] << 8
                end = i + 8 + width_bytes * height
                if end > size:
                    raise EscposReadError(f'an image at byte {i} is cut short')
                put(Raster(width_bytes * 8, height, bytes(data[i + 8:end])))
                i = end
            else:
                raise EscposReadError(f'unknown GS command {command!r} at byte {i}')
        elif byte == 0x0A:                                         # end of a line
            end_line()
            i += 1
        else:
            put(raw=bytes([byte]))
            i += 1
    if qr is not None:
        raise EscposReadError('a QR was stored and never printed')
    if text or pieces:
        end_line()
    return items


# ---------------------------------------------------------------------------
# The golden form
# ---------------------------------------------------------------------------

_MARK = {'center': '<C>', 'right': '<R>'}


def _marker(piece, encoding: str) -> str:
    if isinstance(piece, Raster):
        return f'<IMAGEN {piece.width}x{piece.height}>'
    if isinstance(piece, QR):
        # As the printer's code page can write it: what the goldens always recorded.
        return f'<QR {piece.payload}>'.encode(encoding, 'replace').decode(encoding)
    return piece


def lines(data: bytes, encoding: str = 'cp858') -> list[str]:
    """
    What a thermal roll SAYS, line by line, and how: `data` with its ESC/POS
    commands turned into readable markers.

      <CP n>              `ESC t n`: the code page, on its own line
      <C> <R>             the line is centred / right-aligned (left: nothing)
      <B>                 the line is bold
      <2X>                the line is double width and height
      <QR payload>        a QR the printer stores (fn 0x50) AND prints (fn 0x51)
      <IMAGEN w×h>        a raster image, in dots

    A line's markers are the style in force when its first piece is printed
    (`Receipt` sets the style once per line). Feeds and the cut are not lines.
    """
    out: list[str] = []
    for item in read(data, encoding):
        if isinstance(item, CodePage):
            out.append(f'<CP {item.number}>')
        elif isinstance(item, Line):
            markers = ''
            if item.pieces:                                        # an empty line has no style to show
                markers = _MARK.get(item.align, '') + ('<B>' if item.bold else '') + ('<2X>' if item.large else '')
            out.append(markers + ''.join(_marker(piece, encoding) for piece in item.pieces))
    return out


_MARKERS = re.compile(r'^(?:<C>|<R>|<B>|<2X>)+')


def text(data: bytes) -> list[str]:
    """The printed words only: `lines` without code-page lines and style markers."""
    return [_MARKERS.sub('', line) for line in lines(data) if not line.startswith('<CP ')]


# ---------------------------------------------------------------------------
# The preview
# ---------------------------------------------------------------------------

def simulation(data: bytes, encoding: str = 'cp858') -> list[dict]:
    """
    The roll as blocks a screen can draw, in order:

      {'type': 'text', 'text', 'align', 'bold', 'large'}   one printed line (`large`: double width and height)
      {'type': 'image', 'align', 'width', 'height', 'png'}  the raster itself, dot for dot, as a base64 1-bit PNG
      {'type': 'qr', 'align', 'payload'}                    a QR the printer draws
      {'type': 'feed', 'lines'}                             blank lines the paper advances
      {'type': 'cut'}                                       the cutter
    """
    blocks: list[dict] = []
    for item in read(data, encoding):
        if isinstance(item, Feed):
            blocks.append({'type': 'feed', 'lines': item.lines})
        elif isinstance(item, Cut):
            blocks.append({'type': 'cut'})
        elif isinstance(item, Line):
            only = item.pieces[0] if len(item.pieces) == 1 else None
            if isinstance(only, Raster):
                blocks.append({'type': 'image', 'align': item.align, 'width': only.width, 'height': only.height,
                               'png': base64.b64encode(only.png()).decode('ascii')})
            elif isinstance(only, QR):
                blocks.append({'type': 'qr', 'align': item.align, 'payload': only.payload})
            else:
                blocks.append({'type': 'text', 'text': ''.join(_marker(piece, encoding) for piece in item.pieces),
                               'align': item.align, 'bold': item.bold, 'large': item.large})
    return blocks
