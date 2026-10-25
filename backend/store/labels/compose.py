"""
From data + layout to a PLAN: what each sticker carries and where, or why the
label has to be reviewed by a person before it can be printed.

NOTHING IS CUT. A text that does not fit first gets smaller, down to the floor
its field allows; then it wraps, up to the field's lines; then an optional
field («sólo si cabe») is dropped and the plan says so; then, on a layout that
allows it (the technical one), the text continues on a second sticker. If none
of that is enough, the label is NOT drawn: it goes to review with the reason.
There is no ellipsis anywhere in this package.

A label in review has no stickers: `render_pdf` skips it, and a preview shows
its reasons instead.
"""
from __future__ import annotations

import io
import logging
from dataclasses import dataclass, field

from reportlab.pdfbase.pdfmetrics import stringWidth

from ..document_style import money
from . import symbols
from .data import AccessoryData, DeviceData, DeviceOptions, LabelDataError
from .fields import DEVICE_TECHNICAL, GRAPHIC_KEYS, FieldSpec
from .layout import ACCESSORY_30X20, DEVICE_COMMERCIAL_50X25, DEVICE_TECHNICAL_60X40, Layout

logger = logging.getLogger(__name__)

#: The built-in PDF fonts. They encode WinAnsi (cp1252) only: Spanish, «», €
#: and – print; an emoji or another alphabet does not, and its width would be
#: measured wrong. Such text sends the label to review (`_unprintable`).
FONT = 'Helvetica'
FONT_BOLD = 'Helvetica-Bold'
FONT_ENCODING = 'cp1252'
PT_MM = 25.4 / 72
LINE = 1.18          # line height, in font sizes
STEP_PT = 0.5        # how much a text shrinks per try
GAP_MM = 0.6         # between the text block and the barcode block
LOGO_GAP_MM = 1.0
BATTERY_UNKNOWN = 'Batería no informada'

READY = 'ready'
REVIEW = 'review'


class SensitiveLabelRefused(PermissionError):
    """A label that would show an IMEI or a serial, asked without the permission."""


# --- What a plan is made of ---------------------------------------------

@dataclass(frozen=True)
class TextRun:
    x_mm: float
    y_mm: float          # baseline
    text: str
    font: str
    size_pt: float
    #: The field it prints (`code_text` for the code under the bars). Lets a
    #: caller recognise an identifier even when it wraps over several lines.
    field: str = ''


@dataclass(frozen=True)
class LogoBox:
    image: object        # PIL image, mode '1', at the layout's dpi
    x_mm: float
    y_mm: float
    width_mm: float
    height_mm: float


@dataclass(frozen=True)
class BarsBox:
    symbol: symbols.Symbol
    x_mm: float          # left edge of the first bar (the quiet zone is to its left)
    y_mm: float
    height_mm: float


@dataclass
class Sticker:
    width_mm: float
    height_mm: float
    texts: list[TextRun] = field(default_factory=list)
    logo: LogoBox | None = None
    bars: BarsBox | None = None

    def text_lines(self) -> list[str]:
        return [t.text for t in self.texts]


@dataclass
class LabelPlan:
    kind: str                       # 'accessory' | 'device'
    ref: int                        # product id or unit id
    title: str
    layout: Layout
    copies: int
    reasons: list[str]
    warnings: list[str]
    symbol: symbols.Symbol | None
    stickers: list[Sticker]
    sensitive: bool

    @property
    def status(self) -> str:
        return REVIEW if self.reasons else READY

    @property
    def ready(self) -> bool:
        return not self.reasons and bool(self.stickers)

    @property
    def pages(self) -> int:
        return self.copies * len(self.stickers) if self.ready else 0


# --- The logo -------------------------------------------------------------

class Logo:
    """The company's document logo, flattened on white once per batch."""

    def __init__(self, image):
        self._image = image
        self._fits = {}

    @classmethod
    def from_png(cls, raw: bytes | None) -> tuple['Logo | None', str | None]:
        """(logo, warning). No logo is no warning; an unreadable one is."""
        if not raw:
            return None, None
        try:
            from PIL import Image

            picture = Image.open(io.BytesIO(raw))
            picture.load()
            picture = picture.convert('RGBA')
            paper = Image.new('RGB', picture.size, (255, 255, 255))
            paper.paste(picture, mask=picture.getchannel('A'))
            return cls(paper), None
        except Exception:  # noqa: BLE001 - a logo never stops a label
            logger.warning('Logotipo ilegible para etiquetas; se imprimen sin él.', exc_info=True)
            return None, 'No se pudo leer el logotipo de la empresa; las etiquetas salen sin él.'

    def fit(self, max_w_mm: float, max_h_mm: float, dpi: int):
        """The logo at whole dots of `dpi`, inside the box, in 1 bit (as the thermal head prints it)."""
        key = (round(max_w_mm, 3), round(max_h_mm, 3), dpi)
        if key not in self._fits:
            from PIL import Image

            w, h = self._image.size
            box_w = max_w_mm * dpi / 25.4
            box_h = max_h_mm * dpi / 25.4
            scale = min(box_w / w, box_h / h)
            dots_w, dots_h = max(1, int(w * scale)), max(1, int(h * scale))
            small = self._image.resize((dots_w, dots_h), Image.LANCZOS)
            # Same threshold as the ticket printer (`printing.escpos.image`).
            bits = small.convert('L').point(lambda v: 0 if v < 128 else 255, mode='1')
            self._fits[key] = (bits, dots_w * 25.4 / dpi, dots_h * 25.4 / dpi)
        return self._fits[key]


# --- Text -----------------------------------------------------------------

def _width_mm(text: str, font: str, size: float) -> float:
    return stringWidth(text, font, size) * PT_MM


def _break_word(word: str, font: str, size: float, width: float) -> list[str]:
    pieces, current = [], ''
    for ch in word:
        if current and _width_mm(current + ch, font, size) > width:
            pieces.append(current)
            current = ch
        else:
            current += ch
    if current:
        pieces.append(current)
    return pieces


def wrap(text: str, font: str, size: float, width: float) -> list[str]:
    """Every character of `text`, in lines no wider than `width`. Never drops one."""
    lines, current = [], ''
    for word in text.split(' '):
        pieces = [word] if _width_mm(word, font, size) <= width else _break_word(word, font, size, width)
        for piece in pieces:
            candidate = f'{current} {piece}' if current else piece
            if _width_mm(candidate, font, size) <= width:
                current = candidate
            else:
                if current:
                    lines.append(current)
                current = piece
    if current:
        lines.append(current)
    return lines


@dataclass
class _Member:
    spec: FieldSpec
    text: str
    #: None = required; 'warn' = optional, dropped with a warning;
    #: 'review' = may be dropped, but then the label needs review.
    drop: str | None


@dataclass
class _Group:
    members: list[_Member]

    @property
    def spec(self) -> FieldSpec:
        return self.members[0].spec

    @property
    def text(self) -> str:
        return ' · '.join(m.text for m in self.members)

    @property
    def font(self) -> str:
        return FONT_BOLD if self.spec.bold else FONT


def _battery_text(data) -> tuple[str | None, str | None]:
    if not isinstance(data, DeviceData) or not data.is_used:
        return None, None              # a new device never shows a battery
    if data.battery_health is None:
        return BATTERY_UNKNOWN, 'review'
    return f'Batería {int(data.battery_health)} %', None


def field_text(key: str, data) -> tuple[str | None, str | None]:
    """(text, drop policy) of one field for this data; None text = nothing to print."""
    if key == 'battery':
        return _battery_text(data)
    if key == 'price':
        return (money(data.currency, data.price) if data.price is not None else None), None
    if isinstance(data, AccessoryData):
        return {'name': data.name or None, 'variant': data.variant or None}.get(key), None
    values = {
        'model': data.model or None,
        'attributes': ' · '.join(value for _name, value in data.attributes) or None,
        'condition': data.condition_label or None,
        'grade': data.grade or None,
        'imei': f'IMEI 1: {data.imei}' if data.imei else None,
        'imei2': f'IMEI 2: {data.imei2}' if data.imei2 else None,
        'serial': f'Serie: {data.serial}' if data.serial else None,
        'label_code': f'Código interno: {data.label_code}' if data.label_code else None,
    }
    if key.startswith('attribute:'):
        wanted = key.split(':', 1)[1].strip().casefold()
        return next((v for n, v in data.attributes if n.casefold() == wanted), None), None
    return values.get(key), None


def _groups(layout: Layout, data) -> list[_Group]:
    groups: list[_Group] = []
    for spec in layout.fields:
        if spec.key in GRAPHIC_KEYS:
            continue
        text, drop = field_text(spec.key, data)
        if text is None:
            continue
        member = _Member(spec, text, drop or ('warn' if spec.optional else None))
        if spec.inline and groups:
            groups[-1].members.append(member)
        else:
            groups.append(_Group([member]))
    return groups


def _fit_groups(groups, width, height, *, unlimited_lines):
    """
    Sizes and lines for every group so the block fits `height`, or the reason
    it cannot. Returns (rows, total_height, problem) where rows is
    [(group, size, lines)] and problem names a group that does not fit its width.
    """
    sizes = []
    for group in groups:
        size = group.spec.size_pt
        while True:
            lines = wrap(group.text, group.font, size, width)
            if unlimited_lines or len(lines) <= group.spec.max_lines:
                break
            if size - STEP_PT < group.spec.floor_pt - 1e-9:
                return None, 0, group
            size -= STEP_PT
        sizes.append(size)

    def measure():
        rows, total = [], 0.0
        for group, size in zip(groups, sizes):
            lines = wrap(group.text, group.font, size, width)
            rows.append((group, size, lines))
            total += len(lines) * size * LINE * PT_MM
        return rows, total

    rows, total = measure()
    while total > height + 1e-9:
        # Shrink the largest text that can still shrink and keep its lines.
        shrunk = False
        order = sorted(range(len(groups)), key=lambda i: -sizes[i])
        for i in order:
            smaller = sizes[i] - STEP_PT
            if smaller < groups[i].spec.floor_pt - 1e-9:
                continue
            lines = wrap(groups[i].text, groups[i].font, smaller, width)
            if not unlimited_lines and len(lines) > groups[i].spec.max_lines:
                continue
            sizes[i] = smaller
            shrunk = True
            break
        if not shrunk:
            break
        rows, total = measure()
    return rows, total, None


def _drop_one(groups, only=None) -> _Member | None:
    """Remove the last droppable member (of `only`, if given); optional ones before review ones."""
    for policy in ('warn', 'review'):
        for gi in range(len(groups) - 1, -1, -1):
            if only is not None and groups[gi] is not only:
                continue
            members = groups[gi].members
            for mi in range(len(members) - 1, -1, -1):
                if members[mi].drop == policy:
                    member = members.pop(mi)
                    if not members:
                        groups.pop(gi)
                    return member
    return None


@dataclass
class _Fitted:
    rows: list | None
    x: float
    width: float
    logo_box: LogoBox | None
    reasons: list[str]
    warnings: list[str]

    @property
    def clean(self) -> bool:
        return self.rows is not None and not self.reasons and not self.warnings

    @property
    def cost(self) -> int:
        return 10 * len(self.reasons) + len(self.warnings)


def _fit_text(layout: Layout, data, logo: Logo | None, top: float, area_h: float) -> _Fitted:
    m = layout.margin_mm
    inner_w = layout.width_mm - 2 * m
    x, width, logo_box = m, inner_w, None
    if logo is not None:
        bits, w_mm, h_mm = logo.fit(layout.logo_max_w_mm, min(layout.logo_max_h_mm, area_h), layout.dpi)
        logo_box = LogoBox(bits, m, top - h_mm, w_mm, h_mm)
        x, width = m + w_mm + LOGO_GAP_MM, inner_w - w_mm - LOGO_GAP_MM
    groups = _groups(layout, data)
    fitted = _Fitted(None, x, width, logo_box, [], [])
    while True:
        rows, total, problem = _fit_groups(groups, width, area_h, unlimited_lines=layout.continuation)
        if problem is None and (total <= area_h + 1e-9 or layout.continuation):
            fitted.rows = rows
            return fitted
        # A field too wide for its lines loses its optional part; a block too
        # tall loses the last optional field. Never a character of a field.
        dropped = _drop_one(groups, only=problem)
        if dropped is None:
            if problem is not None:
                fitted.reasons.append(f'{_label_of(problem.spec.key).capitalize()} no cabe en {layout.size_label}.')
            else:
                fitted.reasons.append(f'El texto no cabe en {layout.size_label}.')
            return fitted
        if dropped.drop == 'review':
            fitted.reasons.append(f'«{dropped.text}» no cabe en {layout.size_label}: hay que revisar la etiqueta.')
        else:
            fitted.warnings.append(f'Se omite {_label_of(dropped.spec.key)}: no cabe en {layout.size_label}.')


def _label_of(key: str) -> str:
    return {
        'name': 'el nombre', 'variant': 'la variante', 'model': 'el modelo', 'attributes': 'los atributos',
        'condition': 'la condición', 'battery': 'la batería', 'price': 'el precio', 'grade': 'el grado',
        'imei': 'el IMEI 1', 'imei2': 'el IMEI 2', 'serial': 'la serie', 'label_code': 'el código interno',
    }.get(key, key)


def _rows_to_runs(rows, x, top):
    runs, y = [], top
    for group, size, lines in rows:
        for line in lines:
            y -= size * LINE * PT_MM
            runs.append(TextRun(x, y + size * 0.24 * PT_MM, line, group.font, size, group.spec.key))
    return runs


# --- Composition ------------------------------------------------------------

def _unprintable(texts) -> list[str]:
    bad = []
    for text in texts:
        try:
            text.encode(FONT_ENCODING)
        except UnicodeEncodeError:
            bad.append(text)
    return bad


def _compose(layout: Layout, data, symbol, logo: Logo | None):
    """(stickers, reasons, warnings) for one label."""
    reasons, warnings = [], []
    printed = [member.text for group in _groups(layout, data) for member in group.members]
    if symbol is not None:
        printed.append(symbol.value)
    bad = _unprintable(printed)
    if bad:
        return [], [f'«{text}» no se puede imprimir con la fuente de la etiqueta '
                    f'(emoji u otro alfabeto): hay que corregir el texto.' for text in bad], []
    m = layout.margin_mm
    first = Sticker(layout.width_mm, layout.height_mm)

    bottom = m
    if symbol is not None:
        if layout.has('code_text'):
            size = layout.code_text_pt
            while _width_mm(symbol.value, FONT, size) > layout.width_mm - 2 * layout.barcode_margin_mm:
                size -= STEP_PT
                if size < 4:
                    return [], [f'El texto del código no cabe en {layout.size_label}.'], []
            first.texts.append(TextRun(
                (layout.width_mm - _width_mm(symbol.value, FONT, size)) / 2, bottom + size * 0.24 * PT_MM,
                symbol.value, FONT, size, 'code_text'))
            bottom += size * 1.1 * PT_MM + 0.3
        quiet_left = symbol.quiet[0] * symbol.module_mm
        first.bars = BarsBox(symbol, (layout.width_mm - symbol.width_mm) / 2 + quiet_left,
                             bottom, layout.barcode_height_mm)
        bottom += layout.barcode_height_mm + GAP_MM
    top = layout.height_mm - m
    area_h = top - bottom
    if area_h <= 0:
        return [], [f'El código no deja sitio al texto en {layout.size_label}.'], []

    use_logo = logo is not None and layout.has('logo')
    fitted = _fit_text(layout, data, logo if use_logo else None, top, area_h)
    if use_logo and not fitted.clean:
        # The logo is decoration: before dropping a field or sending the label
        # to review, try the whole width.
        bare = _fit_text(layout, data, None, top, area_h)
        if bare.rows is not None and (fitted.rows is None or bare.cost < fitted.cost):
            fitted = bare
            fitted.warnings.append('Sin logotipo: no cabe junto al texto.')
    reasons += fitted.reasons
    warnings += fitted.warnings
    if fitted.rows is None or reasons:
        return [], reasons, warnings
    rows, x = fitted.rows, fitted.x
    first.logo = fitted.logo_box

    # Lines, page by page: the first sticker has the text area above the bars;
    # a continuation sticker (technical only) has its whole height.
    stickers = [first]
    page_rows, used, current = [], 0.0, first
    page_top, page_h, page_x = top, area_h, x
    mark_pt = 5.0
    mark_h = mark_pt * LINE * PT_MM
    for group, size, lines in rows:
        line_h = size * LINE * PT_MM
        for line in lines:
            if used + line_h > page_h + 1e-9:
                if not layout.continuation:
                    return [], [f'El texto no cabe en {layout.size_label}.'], warnings
                current.texts += _rows_to_runs(page_rows, page_x, page_top)
                current = Sticker(layout.width_mm, layout.height_mm)
                stickers.append(current)
                page_rows, used = [], 0.0
                # The bottom line of a continuation sticker is its «(n/total)» mark.
                page_top, page_h, page_x = layout.height_mm - m, layout.height_mm - 2 * m - mark_h, m
            page_rows.append((group, size, [line]))
            used += line_h
    current.texts += _rows_to_runs(page_rows, page_x, page_top)
    if len(stickers) > 1:
        total = len(stickers)
        for n, sticker in enumerate(stickers[1:], start=2):
            mark = f'({n}/{total})'
            sticker.texts.append(TextRun(
                layout.width_mm - m - _width_mm(mark, FONT, mark_pt), m + mark_pt * 0.24 * PT_MM, mark, FONT, mark_pt,
                'page_mark'))
        warnings.append(f'La etiqueta continúa en {total} adhesivos.')
    return stickers, reasons, warnings


def plan_accessory(data: AccessoryData, layout: Layout = ACCESSORY_30X20, logo: Logo | None = None) -> LabelPlan:
    if layout.purpose != 'accessory':
        raise LabelDataError('Un accesorio se imprime con un diseño de accesorio.')
    symbol, reasons, warnings = symbols.choose_accessory(data.codes, layout)
    if layout.has('price') and data.price is None:
        reasons.append('El producto no tiene precio.')
    stickers = []
    if not reasons:
        stickers, more_reasons, more_warnings = _compose(layout, data, symbol, logo)
        reasons += more_reasons
        warnings += more_warnings
    return LabelPlan('accessory', data.product_id, data.name, layout, data.copies, reasons, warnings,
                     symbol, stickers if not reasons else [], sensitive=False)


def plan_device(data: DeviceData, layout: Layout = DEVICE_COMMERCIAL_50X25,
                options: DeviceOptions = DeviceOptions(), *, fallback: Layout | None = None,
                logo: Logo | None = None) -> LabelPlan:
    if layout.purpose == 'accessory':
        raise LabelDataError('Un equipo no se imprime con un diseño de accesorio.')
    technical = layout.purpose == DEVICE_TECHNICAL
    if technical and not options.sensitive_allowed:
        raise SensitiveLabelRefused('La etiqueta técnica muestra IMEI y serie: necesita el permiso de datos sensibles.')
    may_identify = options.sensitive_allowed if technical else options.may_print_identifiers
    symbol, reasons = symbols.choose_device(data, layout, may_identify=may_identify)
    warnings = []
    if (symbol is None and fallback is not None and fallback.purpose == DEVICE_TECHNICAL
            and options.sensitive_allowed and not technical
            and symbols.device_candidates(data, may_identify=may_identify)):
        moved = plan_device(data, fallback, options, logo=logo)
        moved.warnings.insert(0, f'El código no cabe en {layout.size_label}: se usa la etiqueta {fallback.name}.')
        return moved
    if layout.has('price') and data.price is None:
        reasons.append('Seminuevo sin precio propio: hay que revisarlo antes de etiquetar.'
                       if data.is_used else 'El producto no tiene precio.')
    stickers = []
    if not reasons:
        stickers, more_reasons, more_warnings = _compose(layout, data, symbol, logo)
        reasons += more_reasons
        warnings += more_warnings
    return LabelPlan('device', data.unit_id, data.model, layout, 1, reasons, warnings, symbol,
                     stickers if not reasons else [],
                     sensitive=technical or bool(symbol and symbol.sensitive))


def plan_labels(*, accessories=(), devices=(), options: DeviceOptions = DeviceOptions(),
                logo_png: bytes | None = None, accessory_layout: Layout = ACCESSORY_30X20,
                device_layout: Layout = DEVICE_COMMERCIAL_50X25,
                fallback: Layout | None = DEVICE_TECHNICAL_60X40) -> list[LabelPlan]:
    """
    One plan per product (with its copies) and one per unit. The logo is read
    once; an unreadable one is a warning on every label, never an error.
    """
    product_ids = [a.product_id for a in accessories]
    unit_ids = [d.unit_id for d in devices]
    if len(set(product_ids)) != len(product_ids):
        raise LabelDataError('Un producto repetido: sus copias se suman en una sola línea.')
    if len(set(unit_ids)) != len(unit_ids):
        raise LabelDataError('Un equipo repetido: cada equipo lleva una sola etiqueta.')
    logo, logo_warning = Logo.from_png(logo_png)
    plans = [plan_accessory(item, accessory_layout, logo) for item in accessories]
    plans += [plan_device(item, device_layout, options, fallback=fallback, logo=logo) for item in devices]
    if logo_warning:
        for plan in plans:
            plan.warnings.append(logo_warning)
    return plans
