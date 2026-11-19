"""
DOC-NV-PREMIUM — the sales note's own A4 composition.

`document_layout.render_a4` draws a document described with
`composition='premium'` with these pieces instead of its classic ones. Same
data, same keys, same page and footer; a quieter arrangement:

  header     the logotype and the company (name, then legal name and tax id,
             address, branch and contacts in small type) on the left; on the
             right a short mark, WHAT this paper is, its number and its date.
             No framed box: a boxed «RUC / title / number» is the look of a
             receipt, and the note is told apart by its structure.
  parties    «Cliente» and «Datos de la venta» side by side, each a small
             tracked heading over label / value rows. No frame.
  lines      the retail columns on light rules, with room between the rows;
             serial, IMEI and promotion under the description in grey. The
             heading row repeats on every page.
  totals     right-aligned: the figures, the referential breakdown under its
             own small heading, the total (banded when emphasised) and, after
             it, the cash received and the change. Kept together, and with
             the last `TAIL_ROWS` rows of the table (`_TailWithTotals`): a
             page never starts with the totals alone.
  closing    notes, warranty and the company's messages, in its order, all on
             the left margin. One group (`_Closing`): when it overflows, its
             spacing steps down a density; if it still does not fit, it moves
             to the next page whole. A block never splits (DOC-NV-PAGINATION).

THE ONE COLOUR. Grey type and rules carry the hierarchy, so the paper survives a
monochrome printer. The only colour is the short mark above the title, in the
company's own brand accent (`document['accent']`, `document_style.paper_accent`)
or in ink when it has none. Nothing here knows a company.
"""

from __future__ import annotations

import io

from reportlab.platypus import Flowable, KeepTogether, Spacer

from . import document_style as style

#: Retail order (`document_layout.RETAIL_LINE_COLUMNS`), in cm; 18 cm between the margins.
_RETAIL_WIDTHS = (0.7, 2.3, 7.6, 1.0, 1.1, 1.9, 1.9, 1.5)
#: The classic page's keys this composition never draws (`story`): the note carries no legend.
IGNORED_KEYS = ('legal', 'control_line', 'number_note')
#: DOC-NV-PAGINATION: the last rows of the table that travel with the totals, so a page never
#: starts with the totals alone.
TAIL_ROWS = 2
#: How far in from the right margin every amount ends (the total's band needs the room).
AMOUNT_INSET = 6
#: The accent mark above the title (width, height), in points.
ACCENT_MARK = (30, 2.6)
_CM = 28.3465


def _styles():
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT
    from reportlab.lib.styles import ParagraphStyle

    ink, body, muted = (colors.HexColor(value) for value in (style.INK, style.BODY, style.MUTED))

    def make(name, **kwargs):
        kwargs.setdefault('fontName', style.FONT)
        kwargs.setdefault('textColor', body)
        return ParagraphStyle(name, **kwargs)

    return {
        'name': make('PName', fontName=style.FONT_BOLD, fontSize=15, leading=18.5, textColor=ink),
        'identity': make('PIdentity', fontSize=8.5, leading=12.5),
        'identity_muted': make('PIdentityMuted', fontSize=8.5, leading=12.5, textColor=muted),
        'date': make('PDate', fontSize=9.5, leading=13, alignment=TA_RIGHT, textColor=ink),
        'stamp': make('PStamp', fontName=style.FONT_BOLD, fontSize=13, leading=16, alignment=TA_CENTER,
                      textColor=ink),
        'label': make('PLabel', fontSize=8, leading=12, textColor=muted),
        'value': make('PValue', fontSize=9, leading=12, textColor=ink),
        'cell_muted': make('PCellMuted', fontSize=8, leading=12, textColor=muted),
        'description': make('PDescription', fontSize=9, leading=12, textColor=ink),
        'cell_detail': make('PCellDetail', fontSize=7.5, leading=10.5, textColor=muted),
        'summary': make('PSummary', fontSize=8, leading=12, textColor=muted),
        'words': make('PWords', fontName=style.FONT_BOLD, fontSize=8, leading=12, textColor=body),
        'notes': make('PNotes', fontSize=8.5, leading=12),
        'small': make('PSmall', fontSize=8, leading=11, textColor=muted),
        # DOC-NV-PAGINATION: every block after the totals on the left margin, like the rest of the page.
        'closing': make('PClosing', fontName=style.FONT_BOLD, fontSize=12, leading=16, textColor=ink),
        'post_sale': make('PPostSale', fontSize=9, leading=12.5),
        'commercial': make('PCommercial', fontSize=8.5, leading=12, textColor=muted),
    }


# ---------------------------------------------------------------------------
# Two small flowables: letter-spaced type, and the accent mark
# ---------------------------------------------------------------------------

def _tracked_width(text: str, font: str, size: float, tracking: float) -> float:
    from reportlab.pdfbase.pdfmetrics import stringWidth

    return stringWidth(text, font, size) + tracking * max(len(text) - 1, 0)


def tracked_lines(text: str, font: str, size: float, tracking: float, width: float) -> list[str]:
    """`text` in lines of at most `width`, letter spacing included. A word longer than a line is cut."""
    lines, current = [], ''
    for word in style.printable(text).split():
        candidate = f'{current} {word}' if current else word
        if _tracked_width(candidate, font, size, tracking) <= width:
            current = candidate
            continue
        if current:
            lines.append(current)
        while _tracked_width(word, font, size, tracking) > width and len(word) > 1:
            cut = len(word) - 1
            while cut > 1 and _tracked_width(word[:cut], font, size, tracking) > width:
                cut -= 1
            lines.append(word[:cut])
            word = word[cut:]
        current = word
    if current:
        lines.append(current)
    return lines


def _tracked(text: str, *, font: str, size: float, colour: str, tracking: float = 0.0,
             align: str = 'LEFT', leading: float | None = None):
    """Letter-spaced type that wraps inside its column (a `Paragraph` has no letter spacing)."""
    from reportlab.lib import colors
    from reportlab.platypus import Flowable

    class Tracked(Flowable):
        def __init__(self):
            super().__init__()
            self.leading = leading or size * 1.25
            self.lines: list[str] = []

        def wrap(self, available_width, _available_height):
            self.width = available_width
            self.lines = tracked_lines(text, font, size, tracking, available_width)
            self.height = len(self.lines) * self.leading
            return self.width, self.height

        def draw(self):
            canvas = self.canv
            canvas.saveState()
            canvas.setFillColor(colors.HexColor(colour))
            for index, line in enumerate(self.lines):
                width = _tracked_width(line, font, size, tracking)
                x = {'RIGHT': self.width - width, 'CENTER': (self.width - width) / 2}.get(align, 0)
                baseline = self.height - (index + 1) * self.leading + (self.leading - size) / 2 + size * 0.2
                text_object = canvas.beginText()
                text_object.setFont(font, size)
                text_object.setCharSpace(tracking)
                text_object.setTextOrigin(x, baseline)
                text_object.textOut(line)
                canvas.drawText(text_object)
            canvas.restoreState()

    return Tracked()


def _mark(colour: str):
    """The short bar above the title, right-aligned: the paper's one touch of colour."""
    from reportlab.lib import colors
    from reportlab.platypus import Flowable

    class Mark(Flowable):
        def wrap(self, available_width, _available_height):
            self.width, self.height = available_width, ACCENT_MARK[1]
            return self.width, self.height

        def draw(self):
            self.canv.saveState()
            self.canv.setFillColor(colors.HexColor(colour))
            self.canv.rect(self.width - ACCENT_MARK[0], 0, ACCENT_MARK[0], self.height, stroke=0, fill=1)
            self.canv.restoreState()

    return Mark()


def _heading(text: str):
    return _tracked(str(text).upper(), font=style.FONT_BOLD, size=7, colour=style.MUTED, tracking=1.1, leading=9)


# ---------------------------------------------------------------------------
# The blocks
# ---------------------------------------------------------------------------

def _identity(identity: dict, styles) -> list:
    """The company: its name; legal name and tax id; address, branch and contacts in small grey type."""
    from reportlab.platypus import Paragraph, Spacer

    flow = []
    if identity.get('name'):
        flow += [Paragraph(style.markup(identity['name']), styles['name']), Spacer(1, 3)]
    first = []
    if identity.get('legal_name') and identity['legal_name'] != identity.get('name'):
        first.append(style.markup(identity['legal_name']))
    if identity.get('tax_id'):
        first.append(f"RUC {style.markup(identity['tax_id'])}")
    if first:
        flow.append(Paragraph('   ·   '.join(first), styles['identity']))
    rest = []
    address = ', '.join(part for part in (identity.get('address'), identity.get('city')) if part)
    if address:
        rest.append(style.markup(address))
    if identity.get('branch'):
        branch = identity['branch']
        if identity.get('branch_address') and identity['branch_address'] != identity.get('address'):
            branch = f"{branch} — {identity['branch_address']}"
        rest.append(f'Sucursal: {style.markup(branch)}')
    contact = [f"Tel. {style.markup(identity['phone'])}" if identity.get('phone') else '',
               style.markup(identity.get('email') or ''), style.markup(identity.get('website') or '')]
    contact = '   ·   '.join(part for part in contact if part)
    if contact:
        rest.append(contact)
    if rest:
        flow.append(Paragraph('<br/>'.join(rest), styles['identity_muted']))
    return flow


def _title_block(document: dict, styles) -> list:
    """Right-aligned: the accent mark, the title, the number and the date; a void stamp framed under them."""
    from reportlab.lib import colors
    from reportlab.platypus import Paragraph, Spacer, Table, TableStyle

    flow = [_mark(style.paper_accent(document.get('accent'))), Spacer(1, 9),
            _tracked(document['title'], font=style.FONT_BOLD, size=19, colour=style.INK, tracking=1.4,
                     align='RIGHT', leading=23),
            Spacer(1, 3),
            _tracked(document['number'], font=style.FONT_BOLD, size=12.5, colour=style.BODY, tracking=0.6,
                     align='RIGHT', leading=16)]
    if document.get('issued'):
        label, value = document['issued']
        flow += [Spacer(1, 7), Paragraph(
            f'<font size="7.5" color="{style.MUTED}">{style.markup(label)}</font>&nbsp;&nbsp;&nbsp;'
            f'{style.markup(value)}', styles['date'])]
    if document.get('stamp'):
        # ORDER-CANCEL-01: «ANULADA» where the paper says what it is, framed.
        stamp = Table([[Paragraph(style.markup(document['stamp']), styles['stamp'])]], colWidths=[4.2 * _CM])
        stamp.hAlign = 'RIGHT'
        stamp.setStyle(TableStyle([
            ('BOX', (0, 0), (-1, -1), 1.6, colors.HexColor(style.RULE)),
            ('TOPPADDING', (0, 0), (-1, -1), 4), ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ]))
        flow += [Spacer(1, 8), stamp]
    return flow


def _header(document: dict, styles, usable: float):
    from reportlab.platypus import Image, Spacer, Table, TableStyle

    right_width = 7.2 * _CM
    gutter = 0.6 * _CM
    align = document.get('logo_align') if document.get('logo_align') in ('center', 'right') else 'left'
    logo = None
    if document.get('logo_png'):
        box = document.get('logo_box_cm') or style.LOGO_A4_CM
        # The logo never leaves its column: a wide box (up to 90 mm) shrinks to the title column on
        # the right, to the identity column on the left; centred, it has the whole width.
        room = {'right': right_width, 'center': usable}.get(align, usable - right_width - gutter)
        width, height = style.fit(document['logo_png'], min(box[0] * _CM, room), box[1] * _CM)
        logo = Image(io.BytesIO(document['logo_png']), width=width, height=height)
    identity = _identity(document['identity'], styles)
    title = _title_block(document, styles)

    rules = [
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 0), ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (0, -1), gutter),
        # The title column is right-aligned: a framed void stamp sits under the date, flush right.
        ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
    ]
    if logo is None:
        rows = [[identity, title]]
    elif align == 'center':
        # DOC-DESIGN-04 alignment: the logo across the page, centred; identity and title under it.
        rows = [[logo, ''], [identity, title]]
        rules += [('SPAN', (0, 0), (-1, 0)), ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
                  ('RIGHTPADDING', (0, 0), (-1, 0), 0), ('BOTTOMPADDING', (0, 0), (-1, 0), 12)]
    elif align == 'right':
        rows = [[identity, [logo, Spacer(1, 12), *title]]]
    else:
        rows = [[[logo, Spacer(1, 12), *identity], title]]
    header = Table(rows, colWidths=[usable - right_width, right_width])
    header.setStyle(TableStyle(rules))
    return header


def _parties(document: dict, styles, usable: float):
    """«Cliente» and «Datos de la venta» side by side, each a heading over label / value rows. No frame."""
    from reportlab.lib import colors
    from reportlab.platypus import Paragraph, Table, TableStyle

    blocks = [(heading, [(label, value) for label, value in pairs if value])
              for heading, pairs in document.get('parties', [])]
    blocks = [(heading, pairs) for heading, pairs in blocks if pairs]
    if not blocks:
        return None

    gutter = 0.9 * _CM
    column = (usable - gutter * (len(blocks) - 1)) / len(blocks)
    label_width = 2.9 * _CM
    row, widths = [], []
    for index, (heading, pairs) in enumerate(blocks):
        rows = [[_heading(heading), '']]
        rows += [[Paragraph(style.markup(label), styles['label']), Paragraph(style.markup(value), styles['value'])]
                 for label, value in pairs]
        inner = Table(rows, colWidths=[label_width, column - label_width])
        inner.setStyle(TableStyle([
            ('SPAN', (0, 0), (-1, 0)),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 4),
            ('TOPPADDING', (0, 0), (-1, -1), 2), ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
            # The 8 pt label sits on the baseline of its 9 pt value.
            ('TOPPADDING', (0, 1), (0, -1), 2.8),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 6),
            ('LINEBELOW', (0, 0), (-1, 0), 0.5, colors.HexColor(style.HAIRLINE)),
            ('TOPPADDING', (0, 1), (-1, 1), 7), ('TOPPADDING', (0, 1), (0, 1), 7.8),
        ]))
        if index:
            row.append('')
            widths.append(gutter)
        row.append(inner)
        widths.append(column)
    parties = Table([row], colWidths=widths)
    parties.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 0), ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
    ]))
    return parties


def _lines(document: dict, styles, k: float):
    """
    The lines table as (head, tail, tail with heading): `head` is every row but the last
    `TAIL_ROWS` (None when there are no more), its heading repeated on every page; the tail is
    drawn by `_TailWithTotals`, which keeps it with the totals.
    """
    from decimal import Decimal

    from reportlab.platypus import Paragraph

    from .document_layout import (
        _DISCOUNT_COLUMNS, _LINE_WIDTHS, _RETAIL_DISCOUNT_COLUMNS, LINE_COLUMNS, RETAIL_LINE_COLUMNS,
        _hides_discount_columns,
    )

    hide = _hides_discount_columns(document)
    retail = document.get('line_layout') == 'retail'
    columns = list(RETAIL_LINE_COLUMNS if retail else LINE_COLUMNS)
    rows = [list(columns)]
    quiet = []                                   # the rows whose discount is zero: printed in grey
    for line in document['lines']:
        description = [Paragraph(style.markup(line['description']), styles['description'])]
        for unit in line.get('units') or []:
            # Each identifier holds together; the line may wrap BETWEEN them.
            parts = [part.replace(' ', style.NBSP) for part in style.unit_identifiers(unit)]
            if parts:
                description.append(Paragraph(style.markup('   '.join(parts)), styles['cell_detail']))
        for detail in line.get('details') or []:
            description.append(Paragraph(style.markup(detail), styles['cell_detail']))
        head = [
            str(line['number']),
            Paragraph(style.markup(line.get('code') or ''), styles['cell_muted']),
            description,
            style.printable(line.get('unit') or ''),
        ]
        if retail:
            row = head + [style.quantity(line['quantity']), style.plain(line['unit_price']),
                          style.plain(line['amount']), style.plain(line['discount'])]
        else:
            row = head + [style.plain(line['list_price']), style.plain(line['discount']),
                          style.plain(line['unit_price']), style.quantity(line['quantity']),
                          style.plain(line['amount'])]
        if Decimal(str(line.get('discount') or 0)) == 0:
            quiet.append(len(rows))
        rows.append(row)

    widths = list(_RETAIL_WIDTHS if retail else _LINE_WIDTHS)
    if hide:
        dropped = _RETAIL_DISCOUNT_COLUMNS if retail else _DISCOUNT_COLUMNS
        widths[2] += sum(widths[index] for index in dropped)
        rows = [[cell for index, cell in enumerate(row) if index not in dropped] for row in rows]
        widths = [width for index, width in enumerate(widths) if index not in dropped]
        columns = [column for index, column in enumerate(columns) if index not in dropped]
    amount = columns.index('IMPORTE')
    discount = columns.index('DSCTO.') if 'DSCTO.' in columns else None

    widths = [width * _CM for width in widths]
    header, body = rows[0], rows[1:]
    quiet = [row - 1 for row in quiet]                      # indices into `body`
    tail = min(TAIL_ROWS, len(body))
    split_at = len(body) - tail

    def table(first: int, last: int, *, heading: bool, closes: bool):
        return _line_table(header if heading else None, body[first:last],
                           [row - first for row in quiet if first <= row < last], widths, k,
                           amount=amount, discount=discount, closes=closes)

    head = table(0, split_at, heading=True, closes=False) if split_at else None
    # The last rows travel with the totals (`_TailWithTotals`): drawn without the heading when they
    # continue the table on the same page, with it when they open a page.
    return head, table(split_at, len(body), heading=head is None, closes=True), \
        table(split_at, len(body), heading=True, closes=True)


def _line_table(header, body, quiet, widths, k, *, amount, discount, closes):
    """
    One piece of the lines table. `header`: the heading row (repeated on every page this piece
    spans) or None. `closes`: this piece ends the table (the heavier rule under its last row).
    """
    from reportlab.lib import colors
    from reportlab.platypus import Table, TableStyle

    pad = 5.5 * k
    muted, ink = colors.HexColor(style.MUTED), colors.HexColor(style.INK)
    top = 1 if header else 0                                # the first body row
    rows = ([header] if header else []) + body
    rules = [
        ('FONTNAME', (0, top), (-1, -1), style.FONT),
        ('FONTSIZE', (0, top), (-1, -1), 8.5),
        ('LEADING', (0, top), (-1, -1), 12),
        ('TEXTCOLOR', (0, top), (-1, -1), colors.HexColor(style.BODY)),
        ('TEXTCOLOR', (0, top), (0, -1), muted),
        ('TEXTCOLOR', (amount, top), (amount, -1), ink),
        ('LINEBELOW', (0, top), (-1, -1), 0.5, colors.HexColor(style.FAINT)),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('ALIGN', (0, 0), (0, -1), 'LEFT'),
        ('ALIGN', (3, 0), (3, -1), 'CENTER'),
        ('ALIGN', (4, 0), (-1, -1), 'RIGHT'),
        ('TOPPADDING', (0, top), (-1, -1), pad), ('BOTTOMPADDING', (0, top), (-1, -1), pad),
        # A plain cell's first baseline sits higher than a paragraph's: these meet the description's.
        ('TOPPADDING', (0, top), (0, -1), pad + 0.5), ('TOPPADDING', (3, top), (-1, -1), pad + 0.5),
        ('TOPPADDING', (1, top), (1, -1), pad + 1),
        ('LEFTPADDING', (0, 0), (-1, -1), 4), ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (0, -1), 0), ('RIGHTPADDING', (-1, 0), (-1, -1), AMOUNT_INSET),
    ]
    if header:
        rules += [
            ('FONTNAME', (0, 0), (-1, 0), style.FONT_BOLD),
            ('FONTSIZE', (0, 0), (-1, 0), 7),
            ('TEXTCOLOR', (0, 0), (-1, 0), muted),
            ('LINEBELOW', (0, 0), (-1, 0), 0.8, colors.HexColor(style.RULE)),
            ('TOPPADDING', (0, 0), (-1, 0), 0), ('BOTTOMPADDING', (0, 0), (-1, 0), 6),
        ]
    if closes and len(rows) > top:
        rules.append(('LINEBELOW', (0, -1), (-1, -1), 0.8, colors.HexColor(style.RULE)))
    if discount is not None:
        rules += [('TEXTCOLOR', (discount, row + top), (discount, row + top), muted) for row in quiet]
    table = Table(rows, colWidths=widths, repeatRows=1 if header else 0)
    table.setStyle(TableStyle(rules))
    return table


def _totals(document: dict, styles, usable: float):
    """
    Counts and the amount in words on the left; on the right the figures, the
    referential breakdown under its own small heading (a row with no value),
    the total and, after it, the cash received and the change (`after_total`).
    """
    from reportlab.lib import colors
    from reportlab.pdfbase.pdfmetrics import stringWidth
    from reportlab.platypus import Paragraph, Table, TableStyle

    figures_width, value_width = 7.8 * _CM, 3.2 * _CM
    summary = [Paragraph(style.markup(text), styles['summary']) for text in document.get('summary') or []]
    if document.get('amount_in_words'):
        summary.append(Paragraph(f"SON: {style.markup(document['amount_in_words'])}", styles['words']))

    muted, body, ink = (colors.HexColor(value) for value in (style.MUTED, style.BODY, style.INK))
    plain = document.get('plain_total')
    # Every amount of the page ends on the same vertical, 6 pt in: room for the total's band.
    inset = AMOUNT_INSET
    rows, rules = [], [
        ('ALIGN', (0, 0), (-1, -1), 'RIGHT'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), inset),
        ('RIGHTPADDING', (0, 0), (0, -1), 10),
        ('TOPPADDING', (0, 0), (-1, -1), 2.5), ('BOTTOMPADDING', (0, 0), (-1, -1), 2.5),
    ]

    def row_style(index, size, label_colour, value_colour, bold=False):
        return [('FONTNAME', (0, index), (-1, index), style.FONT_BOLD if bold else style.FONT),
                ('FONTSIZE', (0, index), (-1, index), size),
                ('TEXTCOLOR', (0, index), (0, index), label_colour),
                ('TEXTCOLOR', (1, index), (1, index), value_colour)]

    reference = False
    for label, value in document['totals']:
        index = len(rows)
        rows.append([style.printable(label), style.printable(value)])
        if not value:
            # The heading of the referential breakdown: small, grey, set apart by a light rule.
            reference = True
            rules += row_style(index, 7, muted, muted, bold=True) + [
                ('LINEABOVE', (0, index), (-1, index), 0.5, colors.HexColor(style.FAINT)),
                ('TOPPADDING', (0, index), (-1, index), 6)]
        elif reference:
            rules += row_style(index, 8, muted, muted)
        else:
            rules += row_style(index, 9, body, ink)

    total = len(rows)
    rows.append([style.printable(document.get('total_label') or 'IMPORTE TOTAL'),
                 style.printable(document['total'])])
    # The total's figure steps down (never under 11 pt) until it fits its column; a figure that still does
    # not fit widens the column, and the block with it. S/ 99999999.99 never meets «IMPORTE TOTAL».
    figure = 11 if plain else 15
    while figure > 11 and stringWidth(rows[total][1], style.FONT_BOLD, figure) > value_width - AMOUNT_INSET:
        figure -= 0.5
    rules += row_style(total, 9.5, ink, ink, bold=True) + [
        ('FONTSIZE', (1, total), (1, total), figure),
        ('LINEABOVE', (0, total), (-1, total), 1.0, colors.HexColor(style.RULE)),
        # Label and figure on one baseline: a bottom-aligned cell sits its text `leading - size` up.
        ('VALIGN', (0, total), (-1, total), 'BOTTOM'),
        ('LEADING', (1, total), (1, total), figure * 1.2),
        ('LEADING', (0, total), (0, total), figure * 1.2 - (figure - 9.5)),
        ('TOPPADDING', (0, total), (-1, total), 8), ('BOTTOMPADDING', (0, total), (-1, total), 9),
        # DOC-DESIGN-04: without emphasis the total keeps its rule and bold type, not the band.
        *([] if plain else [('BACKGROUND', (0, total), (-1, total), colors.HexColor(style.SHADE))]),
    ]
    for offset, (label, value) in enumerate(document.get('after_total') or []):
        index = len(rows)
        rows.append([style.printable(label), style.printable(value)])
        rules += row_style(index, 9, muted, ink)
        if offset == 0:
            rules.append(('TOPPADDING', (0, index), (-1, index), 7))

    def widest(column, total_font, total_size):
        return max(stringWidth(row[column], total_font if index == total else style.FONT,
                               total_size if index == total else 9) for index, row in enumerate(rows))

    value_width = max(value_width, widest(1, style.FONT_BOLD, figure) + AMOUNT_INSET + 2)
    figures_width = min(max(figures_width, value_width + widest(0, style.FONT_BOLD, 9.5) + 12), usable - 4 * _CM)
    figures = Table(rows, colWidths=[figures_width - value_width, value_width])
    figures.setStyle(TableStyle(rules))

    block = Table([[summary, figures]], colWidths=[usable - figures_width, figures_width])
    block.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 0), ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (0, 0), 0.8 * _CM),
        ('TOPPADDING', (0, 0), (0, 0), 3),
    ]))
    return block


def story(document: dict, usable: float) -> list:
    """
    The flowables of a sales note on A4, top to bottom. The page and its footer are `render_a4`'s.

    NO LEGAL STATEMENT. The classic page's `legal` (framed statement), `control_line` and
    `number_note` are not drawn here: the sales note carries no legend (DOC-LEGEND-01) and
    `sales_note_services.sales_note_document` never sets them. A description that does is refused
    rather than printed without its statement.
    """
    from reportlab.lib import colors
    from reportlab.platypus import Spacer, Table, TableStyle

    from .document_layout import _CLOSING_ORDER

    carried = [key for key in IGNORED_KEYS if document.get(key)]
    if carried:
        raise ValueError(f'The premium composition draws no {", ".join(carried)}; use the classic page.')
    styles = _styles()
    k = document.get('spacing') or 1     # DOC-DESIGN-04 density
    rule = Table([['']], colWidths=[usable], rowHeights=[0.5])
    rule.setStyle(TableStyle([('LINEBELOW', (0, 0), (-1, -1), 0.6, colors.HexColor(style.HAIRLINE)),
                              ('TOPPADDING', (0, 0), (-1, -1), 0), ('BOTTOMPADDING', (0, 0), (-1, -1), 0)]))
    flow = [_header(document, styles, usable), Spacer(1, 14 * k), rule, Spacer(1, 14 * k)]
    parties = _parties(document, styles, usable)
    if parties is not None:
        flow += [parties, Spacer(1, 18 * k)]
    head, tail, tail_headed = _lines(document, styles, k)
    if head is not None:
        flow.append(head)
    # The figures travel together, and with the last rows of the table: a page never starts with the
    # totals alone, nor ends with them orphaned on the next one (DOC-NV-PAGINATION).
    flow.append(_TailWithTotals([tail, Spacer(1, 14 * k), _totals(document, styles, usable)], tail_headed))

    order = document.get('closing_order') or _CLOSING_ORDER
    flow.append(_Closing(lambda spacing: _closing_blocks(document, styles, spacing, order), _closing_steps(k)))
    return flow


def _closing_spacings() -> tuple[float, ...]:
    """The densities of `document_design` (their spacing factors), loosest first."""
    from .document_design import _SPACING

    return tuple(sorted(_SPACING.values(), reverse=True))


def _closing_steps(k: float) -> list[float]:
    """The company's spacing first, then each tighter density: what the closing group tries to fit in."""
    return [k] + [step for step in _closing_spacings() if step < k]


def _closing_blocks(document: dict, styles, k: float, order) -> list:
    """
    The blocks after the totals, in the company's order: each one a list of flowables with the
    space above it, kept together by `_Closing` so a block never splits across pages. ONE alignment for all of them: the left
    margin, like the rest of the page (DOC-NV-PAGINATION).
    """
    from reportlab.platypus import Paragraph, Spacer

    from .document_layout import _lines_markup

    blocks = {}
    if document.get('notes'):
        blocks['notes'] = [Spacer(1, 14 * k), _heading('Observaciones'), Spacer(1, 4),
                           Paragraph(style.markup(document['notes']), styles['notes'])]
    footnotes = document.get('footnotes') or []
    if footnotes:
        blocks['warranty'] = [part for footnote in footnotes
                              for part in (Spacer(1, 12 * k), Paragraph(style.markup(footnote), styles['small']))]
    if document.get('closing_message'):
        blocks['thank_you'] = [Spacer(1, 20 * k),
                               Paragraph(_lines_markup(document['closing_message']), styles['closing'])]
    for kind, text in document.get('messages') or []:
        blocks[kind] = [Spacer(1, 8 * k), Paragraph(_lines_markup(text), styles[kind])]
    return [blocks[key] for key in order if blocks.get(key)]


def _is_frame_break(flowable) -> bool:
    from reportlab.platypus.doctemplate import FrameBreak

    # `FrameBreak` is an instance (of `_FrameBreak`); what `KeepTogether` inserts is another one.
    return isinstance(flowable, type(FrameBreak))


class _TailWithTotals(KeepTogether):
    """
    The last rows of the table, the space and the totals, kept together. When they open a page
    (moved there whole, or simply starting it) the rows are drawn with the table's heading.
    """

    def __init__(self, flowables, headed):
        super().__init__(flowables)
        self._plain, self._headed = flowables[0], headed

    def split(self, available_width, available_height):
        pieces = super().split(available_width, available_height)
        frame = getattr(self, '_frame', None)
        opens = bool(pieces) and _is_frame_break(pieces[0])
        if opens or (frame is not None and getattr(frame, '_atTop', False)):
            pieces = [self._headed if piece is self._plain else piece for piece in pieces]
        return pieces


class _Closing(Flowable):
    """
    The closing blocks as one group. When they overflow the page, their spacing first steps down
    one density at a time (the type never shrinks); when they still do not fit, the whole group
    moves to the next page — never a single line — where it takes the loosest spacing that fits.
    A group that opens a page drops the space above its first block.

    Each block is kept together; a group taller than a page even at its tightest spacing flows
    block by block, and a single block taller than a page (notes longer than a sheet) flows too:
    its paragraph splits across the pages.
    """

    def __init__(self, build, spacings, *, opens_page=False):
        super().__init__()
        self._build, self._spacings, self._opens_page = build, spacings, opens_page
        self._placed: list = []

    def _blocks(self, spacing, *, at_top):
        blocks = self._build(spacing)
        if at_top and blocks and blocks[0] and isinstance(blocks[0][0], Spacer):
            blocks[0] = blocks[0][1:]
        return blocks

    def _at_top(self):
        frame = getattr(self, '_frame', None)
        return self._opens_page or bool(getattr(frame, '_atTop', False))

    @staticmethod
    def _measure(blocks, available_width):
        placed = []
        for block in blocks:
            for flowable in block:
                _width, height = flowable.wrap(available_width, 0x7fffffff)
                placed.append((flowable, height))
        return placed, sum(height for _flowable, height in placed)

    def wrap(self, available_width, available_height):
        self.width = available_width
        at_top = self._at_top()
        for spacing in self._spacings:
            self._placed, self.height = self._measure(self._blocks(spacing, at_top=at_top), available_width)
            if self.height <= available_height:
                break
        else:
            self._placed, self.height = self._measure(self._blocks(self._spacings[0], at_top=at_top),
                                                      available_width)
        return self.width, self.height

    def split(self, available_width, available_height):
        from reportlab.platypus.doctemplate import FrameBreak

        frame = getattr(self, '_frame', None)
        if frame is not None and not self._at_top():
            page = frame._height - frame._topPadding - frame._bottomPadding
            _placed, tightest = self._measure(self._blocks(self._spacings[-1], at_top=True), available_width)
            if tightest <= page:
                # The whole group on the next page, which picks the loosest spacing that fits there.
                return [FrameBreak(), _Closing(self._build, self._spacings, opens_page=True)]
        # Taller than a page even at its tightest: block by block, each one kept together (a block
        # taller than a page flows: its paragraph splits). The leading null action hands the blocks
        # back to the document one by one, as `KeepTogether.split` does.
        from reportlab.platypus.doctemplate import NullActionFlowable

        return [NullActionFlowable()] + [KeepTogether(block) for block in
                                         self._blocks(self._spacings[0], at_top=self._at_top()) if block]

    def draw(self):
        y = self.height
        for flowable, height in self._placed:
            y -= height
            flowable.drawOn(self.canv, 0, y)
