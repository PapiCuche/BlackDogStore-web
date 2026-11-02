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
amount, legend or QR is an option, and the statements of what a paper is
(«Documento de control interno, sin validez tributaria», «No es una serie
fiscal», the receipt's «No válido como comprobante electrónico SUNAT») are
printed whatever the design says. The fiscal papers (boleta, factura, their
80 mm tickets) do not read this module at all: whether they may, and whether a
design must be frozen when a document is issued, is pending a decision.

THE DEFAULTS SAY WHAT THE PAPERS SAID. A company without a row prints the
logo in the box it always had, the same contact fields on each paper (the A4
with its e-mail, the roll without), no footer line, no message and every
column. What changed for everybody with DOC-DESIGN-03 is typography only: the
80 mm ticket's larger type and bolder hierarchy, the A4's 8 pt serial/IMEI and
aligned totals. The closing message is EMPTY for every company: a platform
default would put one shop's sentence on every other shop's paper.

THE TEXTS ARE WHAT WILL PRINT. They are normalised (NFC, one space between
words, trimmed) and refused — not silently altered — when they carry markup
(`<`, `>`) or a character the built-in font cannot draw (`document_style.printable`
would turn it into «?»). What is saved is exactly what the paper says.
"""
from __future__ import annotations

import unicodedata
from dataclasses import asdict, dataclass, replace

FOOTER_MAX = 200
THANK_YOU_MAX = 120
LIMITS = {'footer_text': FOOTER_MAX, 'thank_you_message': THANK_YOU_MAX}

LOGO_SIZES = ('small', 'medium', 'large')
#: (A4 box in cm, 80 mm ticket box in mm). `medium` is the box documents always had.
_LOGO_BOXES = {
    'small': ((3.8, 1.5), (30, 11)),
    'medium': ((5.4, 2.1), (42, 16)),
    'large': ((7.4, 2.9), (60, 22)),
}

CONTACT_FIELDS = ('show_phone', 'show_email', 'show_address', 'show_website', 'show_email_on_roll')
TEXT_FIELDS = ('footer_text', 'thank_you_message')
FIELDS = ('logo_size', *CONTACT_FIELDS, *TEXT_FIELDS, 'hide_zero_columns')


@dataclass(frozen=True)
class DocumentDesign:
    logo_size: str = 'medium'
    show_phone: bool = True
    show_email: bool = True
    show_address: bool = True
    show_website: bool = False
    show_email_on_roll: bool = False
    footer_text: str = ''
    thank_you_message: str = ''
    hide_zero_columns: bool = False

    @property
    def a4_logo_box_cm(self) -> tuple[float, float]:
        return _LOGO_BOXES.get(self.logo_size, _LOGO_BOXES['medium'])[0]

    @property
    def ticket_logo_box_mm(self) -> tuple[float, float]:
        return _LOGO_BOXES.get(self.logo_size, _LOGO_BOXES['medium'])[1]

    def as_dict(self) -> dict:
        return asdict(self)

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


DEFAULT = DocumentDesign()


# ---------------------------------------------------------------------------
# The texts
# ---------------------------------------------------------------------------

def normalise(value: str) -> str:
    """NFC, every run of whitespace (newlines and tabs included) one space, trimmed."""
    return ' '.join(unicodedata.normalize('NFC', value).split())


def text_problem(value, limit: int) -> str:
    """Why `value` cannot be printed as typed, in words for a person; `''` when it can."""
    from . import document_style as style

    if value is None:
        return ''
    text = normalise(str(value))
    if len(text) > limit:
        return f'Máximo {limit} caracteres (tiene {len(text)}).'
    if '<' in text or '>' in text:
        return 'Sólo texto plano: sin los signos < ni >.'
    unprintable = []
    for char in text:
        if style.printable(char) != char and char not in unprintable:
            unprintable.append(char)
    if unprintable:
        return ('El papel no puede imprimir: ' + ' '.join(unprintable)
                + '. Escríbelo con letras o quítalo (saldría como «?»).')
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
        default_error_messages = {'invalid': 'Se envía como texto.'}

        def __init__(self, *, limit: int, **kwargs):
            self.limit = limit
            super().__init__(**kwargs)

        def to_internal_value(self, data):
            if not isinstance(data, str):
                self.fail('invalid')
            problem = text_problem(data, self.limit)
            if problem:
                raise serializers.ValidationError(problem)
            return normalise(data)

        def to_representation(self, value):
            return value

    class DocumentDesignSerializer(serializers.Serializer):
        logo_size = serializers.ChoiceField(choices=LOGO_SIZES, error_messages={
            'invalid_choice': 'Elige pequeño, mediano o grande.'})
        show_phone = StrictBoolean()
        show_email = StrictBoolean()
        show_address = StrictBoolean()
        show_website = StrictBoolean()
        show_email_on_roll = StrictBoolean()
        footer_text = PlainText(limit=FOOTER_MAX)
        thank_you_message = PlainText(limit=THANK_YOU_MAX)
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
    return DocumentDesign(**{field: getattr(row, field) for field in FIELDS})


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
            setattr(row, field, wanted[field])
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
                setattr(row, field, wanted[field])
            row.full_clean(exclude=['company'])
            row.save()
        after = from_row(row)
        AdminAuditLog.log(
            actor=actor, action='document_design_updated', target_type='company_document_design',
            target_id=row.pk,
            metadata={'changed_fields': changed,
                      'old': {field: getattr(before, field) for field in changed},
                      'new': {field: getattr(after, field) for field in changed}},
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
        'closing_message': design.thank_you_message,
        'footer_note': design.footer_text,
        'hide_zero_columns': design.hide_zero_columns,
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
