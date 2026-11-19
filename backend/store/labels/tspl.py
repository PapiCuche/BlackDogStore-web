"""
LABEL-04 — the stickers of a plan as TSPL, the language of most thermal label printers.

Each sticker is the SAME design the PDF draws (`compose`), rasterised at the head's resolution and
sent as one `BITMAP`: logo, accents and bars come out exactly as designed, whatever the printer's
built-in fonts. Verified on paper on an HL80 (203 dpi): `SIZE` + `GAP` stop on the next label, a 0 bit
prints black, one `PRINT` per sticker keeps a batch in step, and the bars scan.

Pure: no database, no clock. The text uses Bitstream Vera and Vera Bold, which `reportlab` (already a
dependency) ships with its licence: real bold, and the accents and Ñ that Pillow's own font lacks (a
preview showed «rápida» and «Ñandú» as boxes). The plan measured the text with Helvetica; a line that
would come out wider than its room is drawn smaller, never past the edge.
"""
from __future__ import annotations

import os

import reportlab
from PIL import Image, ImageDraw, ImageFont

#: A 203 dpi head: 8 dots per millimetre (the labels' own `Layout.dpi`).
DOTS_PER_MM = 8
#: The gap between labels on the rolls in use (verified: the printer stops on the next label).
GAP_MM = 2
#: Print orientation. 0: the label reads the right way up as it leaves the HL80 (verified on paper;
#: with 1 it came out upside down).
DIRECTION = 0
#: Kept clear between a line of text and the logo beside it.
LOGO_GAP_MM = 1.0
#: Kept clear on every side when a line has to shrink (the head wanders about 1 mm on a roll).
EDGE_MM = 1.0

_FONT_DIR = os.path.join(os.path.dirname(reportlab.__file__), 'fonts')
_FONT_FILES = {False: 'Vera.ttf', True: 'VeraBd.ttf'}
_fonts: dict[tuple[int, bool], ImageFont.FreeTypeFont] = {}


def _font(px: int, bold: bool = False):
    if (px, bold) not in _fonts:
        _fonts[(px, bold)] = ImageFont.truetype(os.path.join(_FONT_DIR, _FONT_FILES[bold]), px)
    return _fonts[(px, bold)]


def size_key(width_mm: float, height_mm: float) -> str:
    """'30x20': how a roll and a job name a label size."""
    return f'{round(width_mm)}x{round(height_mm)}'


def raster(sticker) -> Image.Image:
    """The sticker as a 1-bit image at the head's resolution (0 = black, as Pillow keeps it)."""
    width, height = round(sticker.width_mm * DOTS_PER_MM), round(sticker.height_mm * DOTS_PER_MM)
    image = Image.new('1', (width, height), 1)
    draw = ImageDraw.Draw(image)

    def y_px(y_mm: float) -> int:          # the plan's y grows upwards, like the PDF's
        return round((sticker.height_mm - y_mm) * DOTS_PER_MM)

    edge = round(EDGE_MM * DOTS_PER_MM)
    logo = sticker.logo
    for run in sticker.texts:
        x = round(run.x_mm * DOTS_PER_MM)
        px = max(6, round(run.size_pt * 203 / 72))
        smallest = max(6, round(px * 0.7))          # smaller than this is not read any more
        room = width - edge - x
        # A line beside the logo stops before it, with a gap (verified on a preview: with the logo on
        # the right, «rápida» touched it).
        if logo is not None and logo.x_mm > run.x_mm and \
                logo.y_mm - run.size_pt * 0.36 <= run.y_mm <= logo.y_mm + logo.height_mm:
            room = min(room, round((logo.x_mm - LOGO_GAP_MM) * DOTS_PER_MM) - x)
        text = run.text
        bold = 'Bold' in run.font
        while px > smallest and draw.textlength(text, font=_font(px, bold)) > room:
            px -= 1
        while len(text) > 1 and draw.textlength(text, font=_font(px, bold)) > room:
            text = text[:-1]                         # never past the edge: the plan already wraps,
        #                                              this only catches a font that runs wider
        draw.text((x, y_px(run.y_mm)), text, font=_font(px, bold), fill=0, anchor='ls')
    if sticker.bars is not None:
        bars = sticker.bars
        x = round(bars.x_mm * DOTS_PER_MM)
        top, bottom = y_px(bars.y_mm + bars.height_mm), y_px(bars.y_mm)
        for index, modules in enumerate(bars.symbol.pattern):
            run = modules * bars.symbol.module_dots
            if index % 2 == 0:                 # bars and spaces alternate, starting with a bar
                draw.rectangle([x, top, x + run - 1, bottom - 1], fill=0)
            x += run
    if sticker.logo is not None:
        box = sticker.logo
        logo = box.image.convert('1').resize(
            (max(1, round(box.width_mm * DOTS_PER_MM)), max(1, round(box.height_mm * DOTS_PER_MM))))
        image.paste(logo, (round(box.x_mm * DOTS_PER_MM), y_px(box.y_mm + box.height_mm)))
    return image


def render(stickers) -> bytes:
    """
    One TSPL job: the size of the first sticker, then one CLS/BITMAP/PRINT per sticker. Every sticker
    of a job has the same size (a job is one roll); a different one is refused, never printed.
    """
    stickers = list(stickers)
    if not stickers:
        raise ValueError('No hay etiquetas que imprimir.')
    width_mm, height_mm = stickers[0].width_mm, stickers[0].height_mm
    if any(size_key(s.width_mm, s.height_mm) != size_key(width_mm, height_mm) for s in stickers):
        raise ValueError('Un trabajo de etiquetas es de un solo tamaño de rollo.')
    out = bytearray()
    out += (f'SIZE {round(width_mm)} mm,{round(height_mm)} mm\r\n'
            f'GAP {GAP_MM} mm,0 mm\r\nDIRECTION {DIRECTION}\r\nREFERENCE 0,0\r\nDENSITY 8\r\nSPEED 4\r\n').encode('ascii')
    for sticker in stickers:
        image = raster(sticker)
        out += b'CLS\r\n'
        out += f'BITMAP 0,0,{(image.width + 7) // 8},{image.height},0,'.encode('ascii')
        out += image.tobytes()               # rows packed, high bit first, 0 = black: TSPL's own
        out += b'\r\nPRINT 1,1\r\n'
    return bytes(out)
