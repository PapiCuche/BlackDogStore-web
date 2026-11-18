"""
DOC-DESIGN-03 — the per-company design of the INTERNAL papers.

    design_for(company)            -> DocumentDesign      read-only, one query
    draft(company, data)           -> DocumentDesign      validated, NEVER stored
    update(company, data, actor=…) -> DocumentDesign      stored + AdminAuditLog

WHAT A DESIGN IS. A handful of presentation choices: the size of the logo,
which of the company's EXISTING contact fields the header prints (phone,
e-mail, address, web), a footer line on every page, a closing message and
whether the A4 hides the «P. lista» and «Dscto.» columns when no line has a
discount. The e-mail has two switches: `show_email` for the A4 papers (on, as
they always printed it) and `show_email_on_roll` for the 80 mm ticket and the
thermal print (off, as they never printed it). It is plain data (`DocumentDesign`, frozen): the renderers receive it
inside their context and never query anything.

WHAT IT IS NOT. Not identity, not fiscal data: no tax id, legal name, number,
amount, legend or QR is an option. The order receipt's «No válido como
comprobante electrónico SUNAT» is printed whatever the design says; the sales
note carries no legend at all (`document_legends`, DOC-LEGEND-01). The fiscal papers (boleta, factura, their
80 mm PDF tickets) take only the logo's size and alignment, the thank-you message and the footer, printed after
the mandatory block (FISCAL-DESIGN-01, `fiscal_pdf_services`); the current design is read at each print, and
whether it must be frozen when a document is issued is pending the tax adviser.

THE DEFAULTS SAY WHAT THE PAPERS SAID. A company without a row prints the
logo in the box it always had, the same contact fields on each paper (the A4
with its e-mail, the roll without), no footer line, no message and every
column. What changed for everybody with DOC-DESIGN-03 is typography only: the
80 mm ticket's larger type and bolder hierarchy, the A4's 8 pt serial/IMEI and
aligned totals. The closing message is EMPTY for every company: a platform
default would put one shop's sentence on every other shop's paper.

DOC-DESIGN-04 (sales note only; the order receipt keeps DOC-DESIGN-03 plus the
logo's alignment). A commercial title (never the name of a fiscal document:
`title_problem`), the logo in millimetres per format with a height cap and its
alignment, switches for the optional data (seller, order, channel, requested
receipt, counts, amount in words, the customer's phone, delivery address), a
post-sale and a commercial message (several lines) after the closing one, the
warranty as full text or summary + URL, the tax breakdown detailed / summarised
/ hidden (the amounts are the sale's, never recalculated: `tax_rows`), density,
the emphasis of the total and the order of the informative blocks (stored and
validated; the current layouts keep theirs). Every default prints what the
papers printed. The legal identification of a paper is not a design option and
no design field writes in it.

THE TEXTS ARE WHAT WILL PRINT. They are normalised (NFC, one space between
words, trimmed) and refused — not silently altered — when they carry markup
(`<`, `>`) or a character the built-in font cannot draw (`document_style.printable`
would turn it into «?»), or an emoji. The messages keep their line breaks
(`normalise_message`, at most `MESSAGE_MAX_LINES`). What is saved is exactly
what the paper says.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass, replace

FOOTER_MAX = 200
#: DOC-DESIGN-04: 120 → 300, and up to `MESSAGE_MAX_LINES` lines.
THANK_YOU_MAX = 300
POST_SALE_MAX = 400
COMMERCIAL_MAX = 300
WARRANTY_SUMMARY_MAX = 300
TITLE_MAX = 40
LIMITS = {
    'document_title': TITLE_MAX, 'footer_text': FOOTER_MAX, 'thank_you_message': THANK_YOU_MAX,
    'post_sale_message': POST_SALE_MAX, 'commercial_message': COMMERCIAL_MAX,
    'warranty_summary': WARRANTY_SUMMARY_MAX,
}
#: The texts that keep their line breaks (each line normalised). The others are one line.
MULTILINE_FIELDS = ('thank_you_message', 'post_sale_message', 'commercial_message')
MESSAGE_MAX_LINES = 6

LOGO_SIZES = ('small', 'medium', 'large')
#: (A4 box in cm, 80 mm ticket box in mm). `medium` is the box documents always had.
_LOGO_BOXES = {
    'small': ((3.8, 1.5), (30, 11)),
    'medium': ((5.4, 2.1), (42, 16)),
    'large': ((7.4, 2.9), (60, 22)),
}
#: DOC-DESIGN-04: a width in millimetres per format instead of the three sizes (0 = use the size).
#: The box keeps the proportions of the medium one, and its height never passes the cap.
LOGO_A4_WIDTH_MM = (20, 90)
LOGO_TICKET_WIDTH_MM = (20, 72)
LOGO_A4_MAX_HEIGHT_MM = 30
LOGO_TICKET_MAX_HEIGHT_MM = 24
_A4_RATIO = 2.1 / 5.4
_TICKET_RATIO = 16 / 42
LOGO_ALIGNS = ('left', 'center', 'right')

WARRANTY_MODES = ('full', 'summary')
#: How the totals show the tax already computed for the sale. `detailed` is the paper as it was.
TAX_BREAKDOWN_MODES = ('detailed', 'summary', 'hidden')
DENSITIES = ('compact', 'normal', 'airy')
_SPACING = {'compact': 0.75, 'normal': 1.0, 'airy': 1.3}

#: The informative blocks of a sales note, in the order the A4 prints them today. The legal
#: identification of the paper is NOT a block: no design moves it, hides it or writes in it.
#: Two groups, each ordered by `block_order` on its own (DOC-DESIGN-04):
#:   * HEAD_BLOCKS, before the lines: the A4 prints them side by side, in this order. The rolls keep
#:     the sale data under the note's number (date and order belong with the number on a roll) and
#:     the customer after them, whatever the order;
#:   * CLOSING_BLOCKS, after the totals, in this order on the A4, the 80 mm ticket and the thermal print.
BLOCKS = ('customer', 'sale', 'notes', 'warranty', 'thank_you', 'post_sale', 'commercial')
HEAD_BLOCKS = ('customer', 'sale')
CLOSING_BLOCKS = ('notes', 'warranty', 'thank_you', 'post_sale', 'commercial')

CONTACT_FIELDS = ('show_phone', 'show_email', 'show_address', 'show_website', 'show_email_on_roll')
#: DOC-DESIGN-04: optional data of the sales note, all printed by default.
INFO_FIELDS = ('show_seller', 'show_order', 'show_channel', 'show_requested_receipt', 'show_counts',
               'show_amount_words', 'show_customer_contact', 'show_delivery_address')
TEXT_FIELDS = ('document_title', 'footer_text', 'thank_you_message', 'post_sale_message', 'commercial_message',
               'warranty_summary')
FIELDS = ('logo_size', 'logo_a4_width_mm', 'logo_ticket_width_mm', 'logo_a4_align', 'logo_ticket_align',
          *CONTACT_FIELDS, *INFO_FIELDS, *TEXT_FIELDS, 'warranty_mode', 'tax_breakdown_mode', 'density',
          'emphasize_total', 'block_order', 'hide_zero_columns')


@dataclass(frozen=True)
class DocumentDesign:
    logo_size: str = 'medium'
    logo_a4_width_mm: int = 0
    logo_ticket_width_mm: int = 0
    logo_a4_align: str = 'left'
    logo_ticket_align: str = 'center'
    show_phone: bool = True
    show_email: bool = True
    show_address: bool = True
    show_website: bool = False
    show_email_on_roll: bool = False
    show_seller: bool = True
    show_order: bool = True
    show_channel: bool = True
    show_requested_receipt: bool = True
    show_counts: bool = True
    show_amount_words: bool = True
    show_customer_contact: bool = True
    show_delivery_address: bool = True
    document_title: str = 'Nota de venta'
    footer_text: str = ''
    thank_you_message: str = ''
    post_sale_message: str = ''
    commercial_message: str = ''
    warranty_summary: str = ''
    warranty_mode: str = 'full'
    tax_breakdown_mode: str = 'detailed'
    density: str = 'normal'
    emphasize_total: bool = True
    block_order: tuple = BLOCKS
    hide_zero_columns: bool = False

    def __post_init__(self):
        # A list from JSON or `as_dict` is the same order: kept as a tuple, so designs compare and hash.
        object.__setattr__(self, 'block_order', tuple(self.block_order))

    @property
    def a4_logo_box_cm(self) -> tuple[float, float]:
        if self.logo_a4_width_mm:
            width = self.logo_a4_width_mm
            return width / 10, min(width * _A4_RATIO, LOGO_A4_MAX_HEIGHT_MM) / 10
        return _LOGO_BOXES.get(self.logo_size, _LOGO_BOXES['medium'])[0]

    @property
    def ticket_logo_box_mm(self) -> tuple[float, float]:
        if self.logo_ticket_width_mm:
            width = self.logo_ticket_width_mm
            return width, min(width * _TICKET_RATIO, LOGO_TICKET_MAX_HEIGHT_MM)
        return _LOGO_BOXES.get(self.logo_size, _LOGO_BOXES['medium'])[1]

    @property
    def spacing(self) -> float:
        """What the gaps between the blocks are multiplied by. `normal` is 1: the paper as it was."""
        return _SPACING.get(self.density, 1.0)

    def as_dict(self) -> dict:
        data = asdict(self)
        data['block_order'] = list(self.block_order)
        return data

    def contact(self, *, phone='', email='', address='', city='', branch_address='', website='',
                roll: bool = False) -> dict:
        """
        The contact fields this design prints; a switched-off one comes back empty.
        `roll` = the 80 mm ticket or the thermal print, whose e-mail has its own switch.
        """
        return {
            'phone': phone if self.show_phone else '',
            'email': email if (self.show_email_on_roll if roll else self.show_email) else '',
            'address': address if self.show_address else '',
            'city': city if self.show_address else '',
            'branch_address': branch_address if self.show_address else '',
            'website': website if self.show_website else '',
        }

    # -- DOC-DESIGN-04: what the renderers of the sales note ask -------------

    def warranty_note(self, full_text: str, url: str = '') -> str:
        """
        The warranty as this design presents it. `summary`: the company's summary and the URL of
        its policy; with neither, the full text (a paper never loses its warranty to a setting).
        """
        if self.warranty_mode != 'summary':
            return full_text
        parts = [self.warranty_summary] if self.warranty_summary else []
        if url:
            parts.append(f'Condiciones completas: {url}')
        return ' '.join(parts) or full_text

    def tax_rows(self, tax: dict) -> list[tuple[str, object]]:
        """
        The rows of the tax breakdown between the discount and the total, with the amounts the
        sale already computed (`sales_note_services.tax_context_from_breakdown`). Nothing is
        recalculated: `summary` and `hidden` only print less.
        """
        if self.tax_breakdown_mode == 'hidden':
            return []
        if self.tax_breakdown_mode == 'summary':
            if tax['shows_tax']:
                return [(f"Incluye {tax['tax_label']}", tax['tax_amount'])]
            return [(tax['base_label'], tax['taxable_amount'])]
        rows = [(tax['base_label'], tax['taxable_amount'])]
        if tax['shows_tax']:
            rows.append((tax['tax_label'], tax['tax_amount']))
        return rows

    @property
    def head_order(self) -> tuple[str, ...]:
        return tuple(key for key in self.block_order if key in HEAD_BLOCKS)

    @property
    def closing_order(self) -> tuple[str, ...]:
        """The blocks after the totals in print order. The default is the order every paper had."""
        return tuple(key for key in self.block_order if key in CLOSING_BLOCKS)

    def message_lines(self, block: str) -> list[str]:
        """The lines of the message block `thank_you`, `post_sale` or `commercial`; `[]` when it is empty."""
        text = getattr(self, f'{block}_message', '')
        return text.split('\n') if text else []


DEFAULT = DocumentDesign()


# ---------------------------------------------------------------------------
# The texts
# ---------------------------------------------------------------------------

def normalise(value: str) -> str:
    """NFC, every run of whitespace (newlines and tabs included) one space, trimmed."""
    return ' '.join(unicodedata.normalize('NFC', value).split())


def normalise_message(value: str) -> str:
    """
    A text that keeps its lines (DOC-DESIGN-04): each line normalised like `normalise`, at most
    one empty line between two written ones, none at the start or the end.
    """
    lines = []
    for line in unicodedata.normalize('NFC', value).splitlines():
        line = ' '.join(line.split())
        if line or (lines and lines[-1]):
            lines.append(line)
    while lines and not lines[-1]:
        lines.pop()
    return '\n'.join(lines)


def _is_emoji(char: str) -> bool:
    code = ord(char)
    return (code >= 0x1F000 or 0x2600 <= code <= 0x27BF or 0x2B00 <= code <= 0x2BFF
            or code in (0xFE0F, 0x200D, 0x20E3) or 0x1F1E6 <= code <= 0x1F1FF)


def text_problem(value, limit: int, *, multiline: bool = False) -> str:
    """
    Why `value` cannot be printed as typed, in words for a person; `''` when it can.

    What passes prints the same on the PDF (WinAnsi, `document_style.printable`) and on the
    thermal head (cp858, `printing.escpos.Receipt._encode`, which writes the few WinAnsi
    characters cp858 lacks as their plain reading). An emoji has its own message.
    """
    from . import document_style as style

    if value is None:
        return ''
    text = normalise_message(str(value)) if multiline else normalise(str(value))
    if len(text) > limit:
        return f'Máximo {limit} caracteres (tiene {len(text)}).'
    if multiline and text.count('\n') + 1 > MESSAGE_MAX_LINES:
        return f'Máximo {MESSAGE_MAX_LINES} renglones (tiene {text.count(chr(10)) + 1}).'
    if '<' in text or '>' in text:
        return 'Sólo texto plano: sin los signos < ni >.'
    emojis = []
    for char in text:
        if _is_emoji(char) and char not in emojis and char not in ('\ufe0f', '\u200d', '\u20e3'):
            emojis.append(char)
    if emojis or any(_is_emoji(char) for char in text):
        shown = (' ' + ' '.join(emojis)) if emojis else ''
        return (f'Los emoji no se pueden imprimir{shown}. Quítalos: la impresora los cambiaría por «?».')
    unprintable = []
    for char in text:
        if char == '\n' and multiline:
            continue
        if style.printable(char) != char and char not in unprintable:
            unprintable.append(char)
    if unprintable:
        return ('El papel no puede imprimir: ' + ' '.join(unprintable)
                + '. Escríbelo con letras o quítalo (saldría como «?»).')
    return ''


#: Names of fiscal documents (SUNAT) no text of a sales note may carry (the owner's rule: the note is
#: told apart by what it is, not by a legend). Compared after `_fold`, so «Fáctura», «B.O.L.E.T.A»,
#: «f a c t u r a», «B0LETA», «Bøleta», «ƒactura» or «Bolet@» are caught.
_FISCAL_NAMES = (
    'factura', 'boleta', 'comprobante', 'reciboporhonorarios', 'reciboxhonorarios', 'recibohonorarios',
    'ticketfiscal', 'liquidaciondecompra', 'liquidacioncompra', 'notadecredito', 'notacredito',
    'notadedebito', 'notadebito',
)

#: What may stand for each letter among the characters a design text can hold: printable ASCII and
#: WinAnsi (`text_problem` refuses the rest), lower-cased, after NFKD and without accents (so «ª»,
#: «º», «¹» already read «a», «o», «1»). «1» stands for «i» AND «l».
_LOOKALIKES = {
    'a': 'a4@ª', 'b': 'b8ß', 'c': 'c¢©', 'e': 'e3€', 'f': 'fƒ', 'i': 'i1l|!¡¦', 'l': 'l1i|!¡¦',
    'o': 'o0øðº°', 's': 's5$§', 't': 't7+†', 'u': 'uµμ', 'r': 'r®', 'x': 'x×',
}
_LOOKALIKE_SYMBOLS = frozenset(char for chars in _LOOKALIKES.values() for char in chars
                               if not (char.isascii() and char.isalnum()))


def _fold(text: str) -> str:
    """Lower case, no accents; letters, digits and look-alike symbols kept, anything else a space."""
    plain = ''.join(char for char in unicodedata.normalize('NFKD', str(text).lower())
                    if not unicodedata.combining(char))
    return ''.join(char if (char.isascii() and char.isalnum()) or char in _LOOKALIKE_SYMBOLS else ' '
                   for char in plain)


def _letter(char: str) -> str:
    return '[' + re.escape(_LOOKALIKES.get(char, char)) + ']'


#: Each name starts a word (so «manufactura» is not «factura») and may be spelled with spaces or
#: punctuation between its letters.
_FISCAL_PATTERNS = tuple(re.compile(r'(?<![a-z0-9])' + ' *'.join(_letter(char) for char in name))
                         for name in _FISCAL_NAMES)


def names_fiscal_document(text) -> bool:
    """Whether `text` names a fiscal document, however it is spelled."""
    folded = _fold(text)
    return any(pattern.search(folded) for pattern in _FISCAL_PATTERNS)


#: How the panel calls each text, for the messages.
FIELD_NAMES = {
    'document_title': 'El título', 'footer_text': 'El pie de página', 'thank_you_message': 'El mensaje final',
    'post_sale_message': 'El mensaje posventa', 'commercial_message': 'El mensaje comercial',
    'warranty_summary': 'El resumen de la garantía',
}


def fiscal_word_problem(field: str) -> str:
    return (f'{FIELD_NAMES.get(field, "Este texto")} no puede nombrar un comprobante de pago ni otro documento '
            'tributario (factura, boleta, comprobante, recibo por honorarios, ticket fiscal, liquidación de '
            'compra, nota de crédito o débito): la nota de venta no lleva esas palabras.')


def design_text_problem(field: str, value) -> str:
    """
    Why `value` cannot be the design text `field`; `''` when it can. Every text printed on the
    note: the printable-text rules (`text_problem`) and no name of a fiscal document.
    """
    if value is None:
        return ''
    problem = text_problem(value, LIMITS[field], multiline=field in MULTILINE_FIELDS)
    if problem:
        return problem
    if field == 'document_title' and not normalise(str(value)):
        return 'Escribe el título del documento (por ejemplo, «Nota de venta»).'
    if names_fiscal_document(value):
        problem = fiscal_word_problem(field)
        return problem + (' Usa, por ejemplo, «Nota de venta».' if field == 'document_title' else '')
    return ''


def title_problem(value) -> str:
    """Why `value` cannot be the title of the sales note; `''` when it can."""
    return design_text_problem('document_title', value)


def block_order_problem(value) -> str:
    """Why `value` is not an order of the informative blocks; `''` when it is."""
    if not isinstance(value, (list, tuple)) or not all(isinstance(key, str) for key in value):
        return 'El orden se envía como una lista de bloques.'
    if sorted(value) != sorted(BLOCKS):
        return 'El orden debe incluir cada bloque una sola vez: ' + ', '.join(BLOCKS) + '.'
    return ''


def width_problem(value, limits: tuple[int, int]) -> str:
    low, high = limits
    if isinstance(value, bool) or not isinstance(value, int) or not (value == 0 or low <= value <= high):
        return f'Escribe 0 (según el tamaño elegido) o un ancho entero entre {low} y {high} mm.'
    return ''


# ---------------------------------------------------------------------------
# Validation (the same for a save and for a draft)
# ---------------------------------------------------------------------------

class DesignError(Exception):
    """The fields that are wrong and why, as DRF writes them: `{field: [message]}`."""

    def __init__(self, errors: dict):
        super().__init__('Invalid document design')
        self.errors = errors


def _serializer_class():
    from rest_framework import serializers

    class StrictBoolean(serializers.Field):
        default_error_messages = {'invalid': 'Se envía como verdadero o falso (true / false).'}

        def to_internal_value(self, data):
            if isinstance(data, bool):
                return data
            self.fail('invalid')

        def to_representation(self, value):
            return bool(value)

    class PlainText(serializers.Field):
        """A design text: `design_text_problem` (printable, its length and lines, no fiscal name)."""
        default_error_messages = {'invalid': 'Se envía como texto.'}

        def __init__(self, *, field: str, **kwargs):
            self.design_field = field
            super().__init__(**kwargs)

        def to_internal_value(self, data):
            if not isinstance(data, str):
                self.fail('invalid')
            problem = design_text_problem(self.design_field, data)
            if problem:
                raise serializers.ValidationError(problem)
            return normalise_message(data) if self.design_field in MULTILINE_FIELDS else normalise(data)

        def to_representation(self, value):
            return value

    class Millimetres(serializers.Field):
        def __init__(self, *, limits, **kwargs):
            self.limits = limits
            super().__init__(**kwargs)

        def to_internal_value(self, data):
            problem = width_problem(data, self.limits)
            if problem:
                raise serializers.ValidationError(problem)
            return data

        def to_representation(self, value):
            return value

    class BlockOrder(serializers.Field):
        def to_internal_value(self, data):
            problem = block_order_problem(data)
            if problem:
                raise serializers.ValidationError(problem)
            return tuple(data)

        def to_representation(self, value):
            return list(value)

    def choice(choices, message):
        return serializers.ChoiceField(choices=choices, error_messages={'invalid_choice': message})

    class DocumentDesignSerializer(serializers.Serializer):
        logo_size = choice(LOGO_SIZES, 'Elige pequeño, mediano o grande.')
        logo_a4_width_mm = Millimetres(limits=LOGO_A4_WIDTH_MM)
        logo_ticket_width_mm = Millimetres(limits=LOGO_TICKET_WIDTH_MM)
        logo_a4_align = choice(LOGO_ALIGNS, 'Elige izquierda, centro o derecha.')
        logo_ticket_align = choice(LOGO_ALIGNS, 'Elige izquierda, centro o derecha.')
        show_phone = StrictBoolean()
        show_email = StrictBoolean()
        show_address = StrictBoolean()
        show_website = StrictBoolean()
        show_email_on_roll = StrictBoolean()
        show_seller = StrictBoolean()
        show_order = StrictBoolean()
        show_channel = StrictBoolean()
        show_requested_receipt = StrictBoolean()
        show_counts = StrictBoolean()
        show_amount_words = StrictBoolean()
        show_customer_contact = StrictBoolean()
        show_delivery_address = StrictBoolean()
        document_title = PlainText(field='document_title')
        footer_text = PlainText(field='footer_text')
        thank_you_message = PlainText(field='thank_you_message')
        post_sale_message = PlainText(field='post_sale_message')
        commercial_message = PlainText(field='commercial_message')
        warranty_summary = PlainText(field='warranty_summary')
        warranty_mode = choice(WARRANTY_MODES, 'Elige texto completo o resumen con enlace.')
        tax_breakdown_mode = choice(TAX_BREAKDOWN_MODES, 'Elige detallado, resumido u oculto.')
        density = choice(DENSITIES, 'Elige compacto, normal o amplio.')
        emphasize_total = StrictBoolean()
        block_order = BlockOrder()
        hide_zero_columns = StrictBoolean()

    return DocumentDesignSerializer


def validated(data) -> dict:
    """The known fields of `data`, validated and normalised. Unknown keys are ignored. Raises `DesignError`."""
    if not isinstance(data, dict):
        raise DesignError({'non_field_errors': ['El diseño se envía como un objeto.']})
    serializer = _serializer_class()(data=data, partial=True)
    if not serializer.is_valid():
        raise DesignError({field: [str(message) for message in messages]
                           for field, messages in serializer.errors.items()})
    return dict(serializer.validated_data)


# ---------------------------------------------------------------------------
# Reading, drafting, saving
# ---------------------------------------------------------------------------

def from_row(row) -> DocumentDesign:
    values = {field: getattr(row, field) for field in FIELDS}
    # Stored as `customer,sale,…` (a plain column with a database default); a design holds a tuple.
    values['block_order'] = tuple(key for key in (values['block_order'] or '').split(',') if key) or BLOCKS
    return DocumentDesign(**values)


def _to_column(field: str, value):
    return ','.join(value) if field == 'block_order' else value


def design_for(company) -> DocumentDesign:
    """The company's design, or the defaults. ONE read; never writes."""
    from .models import CompanyDocumentDesign

    if company is None or getattr(company, 'pk', None) is None:
        return DEFAULT
    row = CompanyDocumentDesign.objects.filter(company=company).first()
    return DEFAULT if row is None else from_row(row)


def draft(company, data) -> DocumentDesign:
    """The saved design with `data` on top, validated like a save. NOTHING is stored."""
    return replace(design_for(company), **validated(data))


def update(company, data, *, actor, request=None) -> DocumentDesign:
    """
    Change the company's design (`company.manage`, decided by the caller).

    The first change creates the row. Leaves `AdminAuditLog('document_design_updated')`
    with the fields that changed, before and after. A change of nothing writes nothing.
    """
    from django.db import IntegrityError, transaction

    from .models import AdminAuditLog, CompanyDocumentDesign

    wanted = validated(data)
    with transaction.atomic():
        row = CompanyDocumentDesign.objects.select_for_update().filter(company=company).first()
        before = DEFAULT if row is None else from_row(row)
        changed = sorted(field for field, value in wanted.items() if getattr(before, field) != value)
        if not changed:
            return before
        if row is None:
            row = CompanyDocumentDesign(company=company)
        for field in changed:
            setattr(row, field, _to_column(field, wanted[field]))
        row.full_clean(exclude=['company'])
        try:
            with transaction.atomic():
                row.save()
        except IntegrityError:
            # A concurrent first change created the row: apply this one on top of it.
            row = CompanyDocumentDesign.objects.select_for_update().get(company=company)
            before = from_row(row)
            changed = sorted(field for field, value in wanted.items() if getattr(before, field) != value)
            if not changed:
                return before
            for field in changed:
                setattr(row, field, _to_column(field, wanted[field]))
            row.full_clean(exclude=['company'])
            row.save()
        after = from_row(row)
        AdminAuditLog.log(
            actor=actor, action='document_design_updated', target_type='company_document_design',
            target_id=row.pk,
            metadata={'changed_fields': changed,
                      'old': {field: before.as_dict()[field] for field in changed},
                      'new': {field: after.as_dict()[field] for field in changed}},
            request=request, company=company,
        )
    return after


# ---------------------------------------------------------------------------
# What a design adds to the description of an A4 paper
# ---------------------------------------------------------------------------

def a4_slots(design: DocumentDesign) -> dict:
    """The keys of `document_layout.render_a4` a design fills. The defaults add nothing it did not draw."""
    return {
        'logo_box_cm': design.a4_logo_box_cm,
        'logo_align': design.logo_a4_align,
        'closing_message': design.thank_you_message,
        'footer_note': design.footer_text,
        'hide_zero_columns': design.hide_zero_columns,
    }


def sales_note_a4_slots(design: DocumentDesign) -> dict:
    """
    DOC-DESIGN-04: what only the SALES NOTE's A4 takes from a design (the order receipt does not):
    the messages after the closing one, the spacing and the emphasis of the total.
    """
    return {
        # `(style, text)`: `render_a4` draws each where `closing_order` puts it, lines kept.
        'messages': [(kind, text) for kind, text in (('post_sale', design.post_sale_message),
                                                     ('commercial', design.commercial_message)) if text],
        'closing_order': list(design.closing_order),
        'spacing': design.spacing,
        'plain_total': not design.emphasize_total,
    }


def a4_identity(ctx: dict, design: DocumentDesign) -> dict:
    """The `identity` of `render_a4` for a paper built from a sales-note or order-receipt context."""
    contact = design.contact(
        phone=ctx['store_phone'], email=ctx['store_email'], address=ctx['store_address'],
        city=ctx['store_city'], branch_address=ctx['branch_address'], website=ctx.get('store_website', ''),
    )
    return {
        'name': ctx['store_name'], 'legal_name': ctx['store_legal_name'], 'tax_id': ctx['store_ruc'],
        'branch': ctx['branch_name'], **contact,
    }
