"""
The real-scale PDF: one page per sticker, the page the size of the label.

Printed at «tamaño real / 100 %» it is the label; nothing is scaled to a
sheet. The bars are vector rectangles at the module the plan chose, so the
width the checks measured is the width on paper.

THIS DOES NOT PRINT. It returns bytes. Downloading them marks nothing as
printed and queues nothing (ADR D7: «Una descarga en PDF no confirma nada»).
"""
from __future__ import annotations

import io

from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas as pdf_canvas

from .compose import LabelPlan


class NothingToRender(ValueError):
    """No label of the request is ready: every one needs review."""


def _draw(page, sticker):
    if sticker.logo is not None:
        box = sticker.logo
        page.drawImage(ImageReader(box.image.convert('L')), box.x_mm * mm, box.y_mm * mm,
                       box.width_mm * mm, box.height_mm * mm)
    if sticker.bars is not None:
        bars = sticker.bars
        module = bars.symbol.module_mm
        x = bars.x_mm
        page.setFillColorRGB(0, 0, 0)
        for index, width in enumerate(bars.symbol.pattern):
            if index % 2 == 0:
                page.rect(x * mm, bars.y_mm * mm, width * module * mm, bars.height_mm * mm, stroke=0, fill=1)
            x += width * module
    page.setFillColorRGB(0, 0, 0)
    for run in sticker.texts:
        page.setFont(run.font, run.size_pt)
        page.drawString(run.x_mm * mm, run.y_mm * mm, run.text)


def render_pdf(plans: list[LabelPlan], *, compress: bool = True) -> bytes:
    """Every READY plan, `copies` times each. Plans in review are skipped."""
    ready = [plan for plan in plans if plan.ready]
    if not ready:
        raise NothingToRender('Ninguna etiqueta está lista: todas necesitan revisión.')
    out = io.BytesIO()
    first = ready[0].stickers[0]
    page = pdf_canvas.Canvas(out, pagesize=(first.width_mm * mm, first.height_mm * mm),
                             invariant=1, pageCompression=1 if compress else 0)
    page.setTitle('Etiquetas')
    page.setAuthor('')
    page.setCreator('')
    page.setProducer('')
    for plan in ready:
        for _copy in range(plan.copies):
            for sticker in plan.stickers:
                page.setPageSize((sticker.width_mm * mm, sticker.height_mm * mm))
                _draw(page, sticker)
                page.showPage()
    page.save()
    return out.getvalue()
