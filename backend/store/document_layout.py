"""
DOCUMENT-DESIGN — the A4 page every internal document is laid out on.

One function, `render_a4`, takes a document as PLAIN DATA and draws it. The
sales note and the order receipt each describe themselves; neither draws a
table. That is what keeps two papers of the same shop looking like the same
shop — and what makes the next document (a quote on A4, a delivery slip) a
description instead of a fifth layout.

THE PAGE, TOP TO BOTTOM
-----------------------
  header     logotype and identity on the left; on the right a framed box with
             the tax id, WHAT this paper is, and its number — the largest text
             on the page after the total
  parties    who buys, and the facts of the sale, side by side
  lines      Nº · código · descripción · U.M. · P. lista · dscto. · P. unit. ·
             cant. · importe. A serialized unit is written under its line.
  totals     counts and the amount in words on the left, the figures on the
             right, the total in its own band. Kept together: a page never ends
             with the total orphaned on the next one.
  legal      the statement the document must carry, in a frame — or, instead,
             `control_line`: the same kind of statement as one plain line,
             no frame and no fill; then notes
  footer     on EVERY page: whose document, which number, page X of Y

DATA, NOT A SHOP. Nothing in this file knows a company. An empty field prints
nothing — no dash where a phone would be, no heading over an empty block.
"""

from __future__ import annotations

import io

from . import document_style as style

LINE_COLUMNS = ('Nº', 'CÓDIGO', 'DESCRIPCIÓN', 'U.M.', 'P. LISTA', 'DSCTO.', 'P. UNIT.', 'CANT.', 'IMPORTE')
#: Centimetres; they add up to the 18 cm between the margins.
_LINE_WIDTHS = (0.8, 2.4, 5.8, 1.1, 1.8, 1.4, 1.8, 1.1, 1.8)
_MARGIN_CM = 1.5


def _styles():
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT
    from reportlab.lib.styles import ParagraphStyle

    def make(name, **kwargs):
        kwargs.setdefault('fontName', style.FONT)
        kwargs.setdefault('textColor', colors.HexColor(style.BODY))
        return ParagraphStyle(name, **kwargs)

    ink = colors.HexColor(style.INK)
    muted = colors.HexColor(style.MUTED)
    return {
        'name': make('DName', fontName=style.FONT_BOLD, fontSize=14, leading=17, textColor=ink),
        'identity': make('DIdentity', fontSize=8.5, leading=11.5),
        'box_small': make('DBoxSmall', fontName=style.FONT_BOLD, fontSize=10, leading=13,
                          alignment=TA_CENTER, textColor=ink),
        'box_title': make('DBoxTitle', fontName=style.FONT_BOLD, fontSize=12, leading=15,
                          alignment=TA_CENTER, textColor=ink),
        'box_number': make('DBoxNumber', fontName=style.FONT_BOLD, fontSize=15, leading=19,
                           alignment=TA_CENTER, textColor=ink),
        'box_note': make('DBoxNote', fontSize=6.5, leading=8.5, alignment=TA_CENTER, textColor=muted),
        'box_stamp': make('DBoxStamp', fontName=style.FONT_BOLD, fontSize=13, leading=16,
                          alignment=TA_CENTER, textColor=ink),
        'heading': make('DHeading', fontName=style.FONT_BOLD, fontSize=7, leading=9, textColor=muted),
        'label': make('DLabel', fontName=style.FONT_BOLD, fontSize=8.5, leading=11.5, textColor=ink),
        'value': make('DValue', fontSize=8.5, leading=11.5),
        'cell': make('DCell', fontSize=8.5, leading=11),
        # DOC-DESIGN-03: a device's serial and IMEI are what its warranty hangs on. They were 7.5 pt in the
        # muted grey, the least legible text on the page; now 8 pt in the body colour.
        'cell_detail': make('DCellDetail', fontSize=8, leading=10.5),
        'summary': make('DSummary', fontSize=8.5, leading=12),
        'words': make('DWords', fontName=style.FONT_BOLD, fontSize=8.5, leading=12, textColor=ink),
        'total_label': make('DTotalLabel', fontSize=9, leading=12, alignment=TA_RIGHT),
        'legal': make('DLegal', fontName=style.FONT_BOLD, fontSize=8.5, leading=11.5,
                      alignment=TA_CENTER, textColor=ink),
        'control_line': make('DControlLine', fontSize=8.5, leading=11.5,
                             alignment=TA_CENTER, textColor=muted),
        'small': make('DSmall', fontSize=8, leading=10.5, textColor=muted),
        # DOC-DESIGN-03: the company's closing message, centred under everything else.
        'closing': make('DClosing', fontName=style.FONT_BOLD, fontSize=10.5, leading=14,
                        alignment=TA_CENTER, textColor=ink),
    }


#: The page footer: its text size, and the rule it sits under.
_FOOTER_SIZE = 7
_FOOTER_NOTE_LEADING = 9


def _footer_note_lines(note: str, width: float) -> list[str]:
    """The company's footer line, wrapped to the width between the margins (DOC-DESIGN-03)."""
    return style.wrap(note, style.FONT, _FOOTER_SIZE, width) if note else []


def _numbered_canvas(footer_left: str, footer_note: str = ''):
    """
    A canvas that knows the page count, so each page can say «Página X de Y».

    `footer_note` (DOC-DESIGN-03) is the company's own footer line: drawn on
    EVERY page, centred above the rule. The bottom margin of the page grows by
    its height (`_bottom_margin`), so it never meets the content.
    """
    from reportlab.lib import colors
    from reportlab.lib.units import cm
    from reportlab.pdfgen import canvas

    class NumberedCanvas(canvas.Canvas):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._pages = []

        def showPage(self):
            self._pages.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._pages)
            for state in self._pages:
                self.__dict__.update(state)
                self._draw_footer(total)
                super().showPage()
            super().save()

        def _draw_footer(self, total: int):
            width, _height = self._pagesize
            left, right, baseline = _MARGIN_CM * cm, width - _MARGIN_CM * cm, 1.0 * cm
            self.saveState()
            self.setStrokeColor(colors.HexColor(style.HAIRLINE))
            self.setLineWidth(0.5)
            self.line(left, baseline + 9, right, baseline + 9)
            self.setFillColor(colors.HexColor(style.MUTED))
            self.setFont(style.FONT, _FOOTER_SIZE)
            self.drawString(left, baseline, footer_left)
            self.drawRightString(right, baseline, f'Página {self._pageNumber} de {total}')
            lines = _footer_note_lines(footer_note, right - left)
            for index, line in enumerate(reversed(lines)):
                self.drawCentredString((left + right) / 2, baseline + 15 + index * _FOOTER_NOTE_LEADING, line)
            self.restoreState()

    return NumberedCanvas


def _bottom_margin(document: dict, usable: float) -> float:
    """1.9 cm, plus the height of the company's footer line when it has one."""
    from reportlab.lib.units import cm

    lines = _footer_note_lines(document.get('footer_note') or '', usable)
    return 1.9 * cm + (len(lines) * _FOOTER_NOTE_LEADING + 4 if lines else 0)


def _header(document: dict, styles, usable: float):
    from reportlab.lib import colors
    from reportlab.lib.units import cm
    from reportlab.platypus import Image, Paragraph, Spacer, Table, TableStyle

    identity = document['identity']
    box_width = 6.4 * cm
    left_width = usable - box_width - 0.4 * cm

    left = []
    if document.get('logo_png'):
        box = document.get('logo_box_cm') or style.LOGO_A4_CM
        width, height = style.fit(document['logo_png'], box[0] * cm, box[1] * cm)
        logo = Image(io.BytesIO(document['logo_png']), width=width, height=height)
        logo.hAlign = 'LEFT'
        left += [logo, Spacer(1, 5)]
    if identity.get('name'):
        left.append(Paragraph(style.markup(identity['name']), styles['name']))
    lines = []
    if identity.get('legal_name') and identity['legal_name'] != identity.get('name'):
        lines.append(style.markup(identity['legal_name']))
    address = ', '.join(part for part in (identity.get('address'), identity.get('city')) if part)
    if address:
        lines.append(style.markup(address))
    if identity.get('branch'):
        branch = identity['branch']
        if identity.get('branch_address') and identity['branch_address'] != identity.get('address'):
            branch = f"{branch} — {identity['branch_address']}"
        lines.append(f'<b>Sucursal:</b> {style.markup(branch)}')
    contact = [f"Tel. {style.markup(identity['phone'])}" if identity.get('phone') else '',
               style.markup(identity.get('email') or ''), style.markup(identity.get('website') or '')]
    contact = '  ·  '.join(part for part in contact if part)
    if contact:
        lines.append(contact)
    if lines:
        left.append(Paragraph('<br/>'.join(lines), styles['identity']))

    box_rows = []
    if identity.get('tax_id'):
        box_rows.append([Paragraph(f"RUC {style.markup(identity['tax_id'])}", styles['box_small'])])
    title_row = len(box_rows)
    box_rows.append([Paragraph(style.markup(document['title']), styles['box_title'])])
    box_rows.append([Paragraph(style.markup(document['number']), styles['box_number'])])
    if document.get('number_note'):
        box_rows.append([Paragraph(style.markup(document['number_note']), styles['box_note'])])
    stamp_row = None
    if document.get('stamp'):
        # ORDER-CANCEL-01: «ANULADA» where the paper says what it is, framed.
        stamp_row = len(box_rows)
        box_rows.append([Paragraph(style.markup(document['stamp']), styles['box_stamp'])])
    box = Table(box_rows, colWidths=[box_width])
    stamp_rules = [] if stamp_row is None else [
        ('BOX', (0, stamp_row), (-1, stamp_row), 1.6, colors.HexColor(style.RULE)),
    ]
    box.setStyle(TableStyle(stamp_rules + [
        ('BOX', (0, 0), (-1, -1), 1.1, colors.HexColor(style.RULE)),
        ('BACKGROUND', (0, title_row), (-1, title_row), colors.HexColor(style.SHADE)),
        ('LINEABOVE', (0, title_row), (-1, title_row), 0.5, colors.HexColor(style.HAIRLINE)),
        ('LINEBELOW', (0, title_row), (-1, title_row), 0.5, colors.HexColor(style.HAIRLINE)),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
    ]))

    header = Table([[left, box]], colWidths=[left_width + 0.4 * cm, box_width])
    header.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (0, 0), 0.4 * cm),
    ]))
    return header


def _parties(document: dict, styles, usable: float):
    """«Cliente» and «Datos de la venta», side by side, each a list of label/value pairs."""
    from reportlab.lib import colors
    from reportlab.lib.units import cm
    from reportlab.platypus import Paragraph, Table, TableStyle

    blocks = [(heading, [(label, value) for label, value in pairs if value])
              for heading, pairs in document.get('parties', [])]
    blocks = [(heading, pairs) for heading, pairs in blocks if pairs]
    if not blocks:
        return None

    column = usable / len(blocks)
    label_width = 3.7 * cm
    cells = []
    for heading, pairs in blocks:
        rows = [[Paragraph(style.markup(str(heading).upper()), styles['heading']), '']]
        rows += [[Paragraph(style.markup(label), styles['label']), Paragraph(style.markup(value), styles['value'])]
                 for label, value in pairs]
        inner = Table(rows, colWidths=[label_width, column - label_width - 0.5 * cm])
        inner.setStyle(TableStyle([
            ('SPAN', (0, 0), (-1, 0)),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 4),
            ('TOPPADDING', (0, 0), (-1, -1), 1.5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 1.5),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 4),
        ]))
        cells.append(inner)

    parties = Table([cells], colWidths=[column] * len(blocks))
    rules = [
        ('BOX', (0, 0), (-1, -1), 0.6, colors.HexColor(style.HAIRLINE)),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ('TOPPADDING', (0, 0), (-1, -1), 7),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 7),
    ]
    if len(blocks) > 1:
        rules.append(('LINEAFTER', (0, 0), (-2, -1), 0.6, colors.HexColor(style.HAIRLINE)))
    parties.setStyle(TableStyle(rules))
    return parties


def _hides_discount_columns(document: dict) -> bool:
    """
    DOC-DESIGN-03: «P. LISTA» and «DSCTO.» go only when the company asks AND no
    line has a discount — then the list price IS the unit price and both
    columns repeat or say zero. A discounted line always keeps them.
    """
    from decimal import Decimal

    if not document.get('hide_zero_columns'):
        return False
    return all(Decimal(str(line.get('discount') or 0)) == 0 for line in document['lines'])


#: The columns a hidden discount drops (P. LISTA, DSCTO.); their width goes to the description.
_DISCOUNT_COLUMNS = (4, 5)


def _lines(document: dict, styles):
    from reportlab.lib import colors
    from reportlab.lib.units import cm
    from reportlab.platypus import Paragraph, Table, TableStyle

    hide = _hides_discount_columns(document)
    rows = [list(LINE_COLUMNS)]
    for line in document['lines']:
        description = [Paragraph(style.markup(line['description']), styles['cell'])]
        for unit in line.get('units') or []:
            # Each identifier holds together; the line may wrap BETWEEN them.
            parts = [part.replace(' ', style.NBSP) for part in style.unit_identifiers(unit)]
            if parts:
                description.append(Paragraph(style.markup('   '.join(parts)), styles['cell_detail']))
        for detail in line.get('details') or []:
            description.append(Paragraph(style.markup(detail), styles['cell_detail']))
        rows.append([
            str(line['number']),
            Paragraph(style.markup(line.get('code') or ''), styles['cell']),
            description,
            style.printable(line.get('unit') or ''),
            style.plain(line['list_price']),
            style.plain(line['discount']),
            style.plain(line['unit_price']),
            style.quantity(line['quantity']),
            style.plain(line['amount']),
        ])

    widths = list(_LINE_WIDTHS)
    if hide:
        widths[2] += sum(widths[index] for index in _DISCOUNT_COLUMNS)
        rows = [[cell for index, cell in enumerate(row) if index not in _DISCOUNT_COLUMNS] for row in rows]
        widths = [width for index, width in enumerate(widths) if index not in _DISCOUNT_COLUMNS]
    table = Table(rows, colWidths=[width * cm for width in widths], repeatRows=1)
    table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, 0), style.FONT_BOLD),
        ('FONTSIZE', (0, 0), (-1, 0), 7),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.HexColor(style.INK)),
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor(style.SHADE)),
        ('LINEABOVE', (0, 0), (-1, 0), 0.9, colors.HexColor(style.RULE)),
        ('LINEBELOW', (0, 0), (-1, 0), 0.9, colors.HexColor(style.RULE)),
        ('FONTNAME', (0, 1), (-1, -1), style.FONT),
        ('FONTSIZE', (0, 1), (-1, -1), 8.5),
        ('TEXTCOLOR', (0, 1), (-1, -1), colors.HexColor(style.BODY)),
        ('LINEBELOW', (0, 1), (-1, -1), 0.4, colors.HexColor(style.HAIRLINE)),
        ('LINEBELOW', (0, -1), (-1, -1), 0.9, colors.HexColor(style.RULE)),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('ALIGN', (0, 0), (0, -1), 'CENTER'),
        ('ALIGN', (3, 0), (3, -1), 'CENTER'),
        ('ALIGN', (4, 0), (-1, -1), 'RIGHT'),
        ('TOPPADDING', (0, 0), (-1, -1), 4.5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4.5),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
    ]))
    return table


def _totals(document: dict, styles, usable: float):
    """Counts and words on the left, figures on the right, the total in its own band."""
    from reportlab.lib import colors
    from reportlab.lib.units import cm
    from reportlab.platypus import Paragraph, Table, TableStyle

    figures_width = 7.2 * cm
    summary = [Paragraph(style.markup(text), styles['summary']) for text in document.get('summary') or []]
    if document.get('amount_in_words'):
        summary.append(Paragraph(f"SON: {style.markup(document['amount_in_words'])}", styles['words']))

    # Plain table cells are not markup, but they are drawn with the same font.
    rows = [[style.printable(label), style.printable(value)] for label, value in document['totals']]
    rows.append([style.printable(document.get('total_label') or 'IMPORTE TOTAL'), style.printable(document['total'])])
    figures = Table(rows, colWidths=[figures_width - 3.0 * cm, 3.0 * cm])
    figures.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, -2), style.FONT),
        ('FONTSIZE', (0, 0), (-1, -2), 9),
        ('TEXTCOLOR', (0, 0), (-1, -1), colors.HexColor(style.INK)),
        ('ALIGN', (0, 0), (-1, -1), 'RIGHT'),
        ('TOPPADDING', (0, 0), (-1, -2), 2.5),
        ('BOTTOMPADDING', (0, 0), (-1, -2), 2.5),
        ('FONTNAME', (0, -1), (-1, -1), style.FONT_BOLD),
        ('FONTSIZE', (0, -1), (0, -1), 10),
        ('FONTSIZE', (1, -1), (1, -1), 13),
        ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor(style.SHADE)),
        ('LINEABOVE', (0, -1), (-1, -1), 1.1, colors.HexColor(style.RULE)),
        ('LINEBELOW', (0, -1), (-1, -1), 1.1, colors.HexColor(style.RULE)),
        ('TOPPADDING', (0, -1), (-1, -1), 7),
        ('BOTTOMPADDING', (0, -1), (-1, -1), 8),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        # DOC-DESIGN-03: 4 pt, the padding of the table's cells, so every amount ends on the same vertical.
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
    ]))

    block = Table([[summary, figures]], colWidths=[usable - figures_width, figures_width])
    block.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (0, 0), 0.5 * cm),
    ]))
    return block


def _page_marks(watermark):
    """The `onFirstPage`/`onLaterPages` callback that stamps `watermark` on EVERY page."""
    if watermark is None or not (watermark.lines or watermark.band):
        return None

    def stamp(canvas, _doc):
        from reportlab.lib.units import cm

        width, height = canvas._pagesize
        style.draw_diagonal(canvas, width, height, watermark.lines)
        # In the top margin: the content starts 1.5 cm down and never meets it.
        style.draw_band(canvas, watermark.band, _MARGIN_CM * cm, width - _MARGIN_CM * cm,
                        height - 1.1 * cm)

    return stamp


def render_a4(document: dict, *, watermark=None) -> bytes:
    """
    Draw `document` on A4 and return the PDF. Nothing is written to disk.

    `watermark` (a `document_style.Watermark`, optional) is drawn on every page
    by the renderer, not by the document: diagonal lines behind the content and
    a framed band in the top margin. It is how a preview is made unmistakable.

    `document` keys: `title`, `number`, `number_note`, `pdf_title`, `author`,
    `logo_png`, `identity{name, legal_name, tax_id, address, city, branch,
    branch_address, phone, email}`, `parties[(heading, [(label, value)])]`,
    `lines[{number, code, description, units, details, unit, list_price,
    discount, unit_price, quantity, amount}]`, `summary[str]`,
    `amount_in_words`, `totals[(label, value)]`, `total`, `total_label`,
    `legal`, `control_line`, `notes`, `footnotes[str]`, `footer`, `stamp`
    (a word such as «ANULADA», framed in the header box; absent → nothing).

    DOC-DESIGN-03, all optional and absent = as before: `logo_box_cm` (the box
    the logo fits in), `identity.website`, `footer_note` (a line on every page,
    above the footer rule), `closing_message` (centred, last) and
    `hide_zero_columns` (see `_hides_discount_columns`).
    """
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    styles = _styles()
    margin = _MARGIN_CM * cm
    usable = A4[0] - 2 * margin

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4, leftMargin=margin, rightMargin=margin,
        topMargin=margin, bottomMargin=_bottom_margin(document, usable),
        title=document.get('pdf_title') or document['number'], author=document.get('author') or '',
    )

    story = [_header(document, styles, usable), Spacer(1, 12)]
    parties = _parties(document, styles, usable)
    if parties is not None:
        story += [parties, Spacer(1, 12)]
    story += [_lines(document, styles), Spacer(1, 10)]

    closing = [_totals(document, styles, usable)]
    if document.get('legal'):
        legal = Table([[Paragraph(style.markup(document['legal']), styles['legal'])]], colWidths=[usable])
        legal.setStyle(TableStyle([
            ('BOX', (0, 0), (-1, -1), 0.8, colors.HexColor(style.RULE)),
            ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor(style.SHADE)),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ]))
        closing += [Spacer(1, 12), legal]
    if document.get('control_line'):
        # One discreet line, no frame and no fill (SALES-NOTE-DESIGN-01).
        closing += [Spacer(1, 10), Paragraph(style.markup(document['control_line']), styles['control_line'])]
    # The figures and the statement about what this paper is travel together.
    story.append(KeepTogether(closing))

    if document.get('notes'):
        story += [Spacer(1, 10), Paragraph('OBSERVACIONES', styles['heading']), Spacer(1, 2),
                  Paragraph(style.markup(document['notes']), styles['value'])]
    for footnote in document.get('footnotes') or []:
        story += [Spacer(1, 6), Paragraph(style.markup(footnote), styles['small'])]
    if document.get('closing_message'):
        story += [Spacer(1, 16), Paragraph(style.markup(document['closing_message']), styles['closing'])]

    marks = _page_marks(watermark)
    doc.build(story, canvasmaker=_numbered_canvas(document.get('footer') or document['number'],
                                                  document.get('footer_note') or ''),
              **({'onFirstPage': marks, 'onLaterPages': marks} if marks else {}))
    return buffer.getvalue()
