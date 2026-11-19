"""
FISCAL-DESIGN-01 — the boleta and the factura (A4 and 80 mm PDF) get the design
of the company's papers, AND NOTHING FISCAL MOVES.

What a company design changes on a fiscal paper, and only that:

  * the size and the alignment of the logo (`logo_size`, `logo_a4_width_mm`,
    `logo_ticket_width_mm`, `logo_a4_align`, `logo_ticket_align`), inside the
    design's box and under its height cap, never distorted;
  * the thank-you message and the footer line, printed AFTER the mandatory
    block (totals, QR, legend, status, digest) and never inside it.

Everything else is asserted here not to move: the XML and its digest, the QR
(payload, presence and size), the series and number, issuer and buyer, the
amounts, the legend «Representación impresa de la …», the SUNAT status, the
BETA band and its diagonal on every page, the legal date. The layout itself
changed for every company (hierarchy, spacing, the items table): the frozen
pre-design goldens (`goldens/fiscal_pre_design.json`) prove that every token
printed before is still printed, the same number of times.
"""
import hashlib
import io
import json
import re
from collections import Counter
from dataclasses import replace
from pathlib import Path

from django.forms.models import model_to_dict
from PIL import Image

from store import document_legends as legends
from store.document_design import DEFAULT, LOGO_A4_MAX_HEIGHT_MM, LOGO_TICKET_MAX_HEIGHT_MM
from store.fiscal_pdf_services import (
    A4_ISSUER_COLUMN_CM, build_fiscal_context, generate_fiscal_pdf, generate_fiscal_ticket_pdf, render_fiscal_a4,
    render_fiscal_ticket,
)
from store.models import CompanyDocumentDesign, FiscalDocument, FiscalDocumentStatus, FiscalEnvironment
from store.test_document_design import page_streams, page_texts
from store.test_document_goldens import GOLDEN_FILE, FiscalFixtures, check_golden

PRE_DESIGN_FILE = Path(__file__).resolve().parent / 'goldens' / 'fiscal_pre_design.json'

MM = 72 / 25.4
CM = 10 * MM
A4_LEFT = 2 * CM
ROLL_LEFT = 4 * MM
ROLL_CONTENT = 64 * MM   # a 72 mm page (the printable strip) with 4 mm margins (DOC-TICKET-PREMIUM)
QR_A4 = 3.6 * CM
QR_ROLL = 28 * MM

#: Made-up texts, never a real shop's sentence. Two lines, and an «&» printed as typed.
THANKS = 'Mensaje de agradecimiento de prueba.\nSegunda línea del mensaje.'
FOOTER = 'Pie de prueba · soporte A&B'
MESSAGES = {'thank_you_message': THANKS, 'footer_text': FOOTER}
#: As long as a design allows (6 lines, ≤ 300 characters; a footer of ≤ 200).
LONG_MESSAGES = {
    'thank_you_message': '\n'.join(f'Renglón {n} del mensaje de prueba, que es largo.' for n in range(1, 7)),
    'footer_text': ('Pie de prueba con horario de atención, dirección de la tienda y teléfono de contacto, '
                    'escrito largo para ocupar dos renglones del papel A4 y empujar lo que venga después.'),
}

#: Every option of the design that is NOT the logo, the thank-you message or the footer: none of it reaches a
#: fiscal paper (contacts are not in the signed XML; titles, warranty and the rest belong to the sales note).
NOT_FOR_FISCAL = {
    'show_phone': False, 'show_email': False, 'show_address': False, 'show_website': True,
    'show_email_on_roll': True, 'show_seller': False, 'show_order': False, 'show_channel': False,
    'show_requested_receipt': False, 'show_counts': False, 'show_amount_words': False,
    'show_customer_contact': False, 'show_delivery_address': False, 'document_title': 'Recibo de tienda',
    'post_sale_message': 'Mensaje posventa de prueba', 'commercial_message': 'Mensaje comercial de prueba',
    'warranty_summary': 'Resumen de garantía de prueba', 'warranty_mode': 'summary',
    'tax_breakdown_mode': 'hidden', 'density': 'airy', 'emphasize_total': False, 'hide_zero_columns': True,
    'block_order': ('commercial', 'post_sale', 'thank_you', 'warranty', 'notes', 'sale', 'customer'),
}


def png(size, colour=(20, 20, 20)) -> bytes:
    out = io.BytesIO()
    Image.new('RGB', size, colour).save(out, format='PNG')
    return out.getvalue()


WIDE = png((240, 80))          # 3:1
SQUARE = png((200, 200))
TALL = png((80, 240))          # 1:3


def drawn_images(pdf: bytes) -> list[list[tuple[float, float, float, float]]]:
    """
    Per page, every image drawn: (width, height, x, y) in points ON THE PAGE, in drawing order.

    A table cell draws its content after moving the origin (`q … cm … Q`), so the matrix of the
    image alone is not where it lands: the transformation in force is followed through the stream.
    """
    number = rb'-?[\d.]+'
    # A string literal is consumed whole first: a «Q» inside one (a digest is base64: «/Q», «+Q») is text.
    op = re.compile(rb'\((?:\\.|[^\\()])*\)|(?<!\S)(q|Q)(?!\S)|((?:' + number + rb'\s+){6})cm|/(\S+) Do')

    def times(m, n):
        a, b, c, d, e, f = m
        a2, b2, c2, d2, e2, f2 = n
        return (a * a2 + b * c2, a * b2 + b * d2, c * a2 + d * c2, c * b2 + d * d2,
                e * a2 + f * c2 + e2, e * b2 + f * d2 + f2)

    pages = []
    for stream in page_streams(pdf):
        ctm, stack, images = (1, 0, 0, 1, 0, 0), [], []
        for match in op.finditer(stream):
            if match.group(1) == b'q':
                stack.append(ctm)
            elif match.group(1) == b'Q':
                ctm = stack.pop()
            elif match.group(2):
                ctm = times(tuple(float(v) for v in match.group(2).split()), ctm)
            elif match.group(3) and match.group(3).startswith(b'FormXob'):
                a, _b, _c, d, e, f = ctm
                images.append((round(a, 3), round(d, 3), round(e, 3), round(f, 3)))
        pages.append(images)
    return pages


def text_runs(pdf: bytes) -> list[list[tuple[str, float, float]]]:
    """
    Per page, every string drawn: (text, left, right) in points ON THE PAGE. The transformation in force
    (`cm`), the text matrix (`Tm`) and the moves (`Td`) are followed; the width is measured with the font
    and size set by `Tf` (each `/Fn` looked up in the PDF's font dictionaries).
    """
    from reportlab.pdfbase.pdfmetrics import stringWidth

    from store.test_document_design import _literal

    number = rb'-?[\d.]+'
    # String literals are consumed whole (with their `Tj`, or alone): an operator inside one is text.
    op = re.compile(rb'(\((?:\\.|[^\\()])*\))\s*Tj|\((?:\\.|[^\\()])*\)'
                    rb'|(?<!\S)(q|Q|BT|ET)(?!\S)|((?:' + number + rb'\s+){6})(cm|Tm)'
                    rb'|(' + number + rb')\s+(' + number + rb')\s+Td|/(F\d+)\s+(' + number + rb')\s+Tf')
    fonts = {name: base.decode() for base, name in
             re.findall(rb'/BaseFont /([\w-]+)[^>]*?/Name /(F\d+)', pdf)}
    pages = []
    for stream in page_streams(pdf):
        ctm, stack, runs = (1, 0, 0, 1, 0, 0), [], []
        x, font, size = 0.0, 'Helvetica', 10.0
        for match in op.finditer(stream):
            literal, word, matrix, kind = match.group(1), match.group(2), match.group(3), match.group(4)
            if literal:
                text = _literal(literal)
                left = ctm[4] + ctm[0] * x
                runs.append((text, round(left, 3), round(left + ctm[0] * stringWidth(text, font, size), 3)))
            elif word == b'q':
                stack.append(ctm)
            elif word == b'Q':
                ctm = stack.pop()
            elif word in (b'BT', b'ET'):
                x = 0.0
            elif matrix and kind == b'cm':
                a, b, c, d, e, f = (float(v) for v in matrix.split())
                a2, b2, c2, d2, e2, f2 = ctm
                ctm = (a * a2 + b * c2, a * b2 + b * d2, c * a2 + d * c2, c * b2 + d * d2,
                       e * a2 + f * c2 + e2, e * b2 + f * d2 + f2)
            elif matrix and kind == b'Tm':
                x = float(matrix.split()[4])
            elif match.group(5):
                x += float(match.group(5))
            elif match.group(7):
                font, size = fonts.get(match.group(7), 'Helvetica'), float(match.group(8))
        pages.append(runs)
    return pages


def joined(text: str) -> str:
    """
    A `Paragraph` draws an «&» as a text run of its own, and the text reader joins runs with a space:
    «A&B» reads «A & B». Only the reading is undone here; the paper says «A&B».
    """
    return re.sub(r' ?& ?', '&', text)


def flat(pdf: bytes) -> str:
    return joined(' '.join(page_texts(pdf)))


def tokens(text: str) -> Counter:
    return Counter(joined(text).split())


class FiscalDesignBase(FiscalFixtures):
    def ctx(self, document=None, *, logo=None, **design):
        ctx = build_fiscal_context(document or self.receipt())
        return {**ctx, 'logo_png': logo, 'design': replace(DEFAULT, **design)}

    def save_design(self, company, **fields):
        if 'block_order' in fields:                       # a plain column: `customer,sale,…`
            fields['block_order'] = ','.join(fields['block_order'])
        CompanyDocumentDesign.objects.update_or_create(company=company, defaults=fields)

    def readable(self, pdf_bytes) -> str:
        return joined(super().readable(pdf_bytes))


# ---------------------------------------------------------------------------
# The logo
# ---------------------------------------------------------------------------

class FiscalLogoSizeTest(FiscalDesignBase):
    def logo(self, render, logo, **design) -> tuple[float, float, float, float]:
        images = drawn_images(render(self.ctx(self.document, logo=logo, **design)))[0]
        self.assertEqual(len(images), 2, images)             # the logo, then the QR
        return images[0]

    def setUp(self):
        super().setUp()
        self.document = self.receipt()

    def assertSize(self, got, width, height):
        self.assertAlmostEqual(got[0], width, delta=0.05)
        self.assertAlmostEqual(got[1], height, delta=0.05)

    def test_without_a_design_the_logo_takes_the_medium_box_of_the_company_papers(self):
        # A4: 5.4 × 2.1 cm (was a fixed 5.0 × 1.9 cm); 80 mm: 42 × 16 mm, as it was.
        self.assertSize(self.logo(render_fiscal_a4, WIDE), 5.4 * CM, 1.8 * CM)
        self.assertSize(self.logo(render_fiscal_ticket, WIDE), 42 * MM, 14 * MM)

    def test_the_three_sizes_of_the_design(self):
        for size, a4_width, roll_width in (('small', 3.8, 30), ('medium', 5.4, 42), ('large', 7.4, 60)):
            with self.subTest(size):
                self.assertSize(self.logo(render_fiscal_a4, WIDE, logo_size=size), a4_width * CM, a4_width / 3 * CM)
                self.assertSize(self.logo(render_fiscal_ticket, WIDE, logo_size=size),
                                roll_width * MM, roll_width / 3 * MM)

    def test_a_width_in_millimetres_wins_over_the_size(self):
        self.assertSize(self.logo(render_fiscal_a4, WIDE, logo_size='small', logo_a4_width_mm=80),
                        80 * MM, 80 / 3 * MM)
        self.assertSize(self.logo(render_fiscal_ticket, WIDE, logo_size='small', logo_ticket_width_mm=60),
                        60 * MM, 20 * MM)

    def test_the_height_is_capped_and_the_logo_never_distorted(self):
        cases = ((render_fiscal_a4, {'logo_a4_width_mm': 90}, LOGO_A4_MAX_HEIGHT_MM),
                 (render_fiscal_ticket, {'logo_ticket_width_mm': 72}, LOGO_TICKET_MAX_HEIGHT_MM))
        for render, design, cap in cases:
            for name, logo, ratio in (('wide', WIDE, 3), ('square', SQUARE, 1), ('tall', TALL, 1 / 3)):
                with self.subTest(render=render.__name__, logo=name):
                    width, height, _x, _y = self.logo(render, logo, **design)
                    self.assertLessEqual(height, cap * MM + 0.05)
                    self.assertAlmostEqual(width / height, ratio, delta=0.01)
        # A square logo as wide as allowed: as tall as the cap, and as wide as tall.
        self.assertSize(self.logo(render_fiscal_a4, SQUARE, logo_a4_width_mm=90), 30 * MM, 30 * MM)
        self.assertSize(self.logo(render_fiscal_ticket, SQUARE, logo_ticket_width_mm=72), 24 * MM, 24 * MM)

    def test_the_a4_alignment_moves_the_logo_inside_the_issuer_column(self):
        left, centre, right = (self.logo(render_fiscal_a4, WIDE, logo_a4_align=align)
                               for align in ('left', 'center', 'right'))
        column = A4_ISSUER_COLUMN_CM * CM
        width = left[0]
        self.assertAlmostEqual(left[2], A4_LEFT, delta=0.05)
        self.assertAlmostEqual(centre[2], A4_LEFT + (column - width) / 2, delta=0.05)
        # The column ends before the fiscal box: the logo never runs into it.
        self.assertAlmostEqual(right[2] + width, A4_LEFT + column, delta=0.05)
        # The widest logo a design allows fits the column.
        widest = self.logo(render_fiscal_a4, WIDE, logo_a4_width_mm=90, logo_a4_align='right')
        self.assertGreaterEqual(widest[2], A4_LEFT - 0.05)

    def test_the_roll_alignment_left_centre_right(self):
        positions = {align: self.logo(render_fiscal_ticket, WIDE, logo_ticket_align=align)
                     for align in ('left', 'center', 'right')}
        width = positions['left'][0]
        self.assertAlmostEqual(positions['left'][2], ROLL_LEFT, delta=0.05)
        self.assertAlmostEqual(positions['center'][2], ROLL_LEFT + (ROLL_CONTENT - width) / 2, delta=0.05)
        self.assertAlmostEqual(positions['right'][2], ROLL_LEFT + ROLL_CONTENT - width, delta=0.05)
        # Centred by default, as it always was.
        self.assertEqual(self.logo(render_fiscal_ticket, WIDE)[2], positions['center'][2])

    def test_the_qr_keeps_its_size_whatever_the_logo(self):
        for design in ({}, {'logo_size': 'large'}, {'logo_a4_width_mm': 90, 'logo_ticket_width_mm': 72}):
            with self.subTest(design):
                a4 = drawn_images(render_fiscal_a4(self.ctx(self.document, logo=SQUARE, **design)))[-1][-1]
                roll = drawn_images(render_fiscal_ticket(self.ctx(self.document, logo=SQUARE, **design)))[-1][-1]
                self.assertAlmostEqual(a4[0], QR_A4, delta=0.05)
                self.assertAlmostEqual(a4[1], QR_A4, delta=0.05)
                self.assertAlmostEqual(roll[0], QR_ROLL, delta=0.05)
                self.assertAlmostEqual(roll[1], QR_ROLL, delta=0.05)

    def test_without_a_logo_a_design_draws_only_the_qr(self):
        for render in (render_fiscal_a4, render_fiscal_ticket):
            with self.subTest(render.__name__):
                pdf = render(self.ctx(self.document, logo_a4_width_mm=90, logo_ticket_width_mm=72,
                                      logo_a4_align='right', logo_ticket_align='right'))
                self.assertEqual(len(drawn_images(pdf)[0]), 1)


# ---------------------------------------------------------------------------
# The thank-you message and the footer
# ---------------------------------------------------------------------------

class FiscalMessagesTest(FiscalDesignBase):
    def test_the_messages_come_after_the_mandatory_block(self):
        document = self.invoice()
        self.save_design(document.company, **MESSAGES)
        for name, pdf in (('a4', generate_fiscal_pdf(document)), ('ticket', generate_fiscal_ticket_pdf(document))):
            with self.subTest(name):
                text = self.readable(pdf)
                last_mandatory = max(text.index(marker) for marker in (
                    'IMPORTE TOTAL' if name == 'a4' else 'TOTAL', 'SON:', 'Representación impresa de la factura',
                    'Estado:', document.digest_value))
                first, second = THANKS.split('\n')
                self.assertLess(last_mandatory, text.index(first))
                self.assertLess(text.index(first), text.index(second))
                self.assertLess(text.index(second), text.index(FOOTER))
                self.assertNotIn('&amp;', text)

    def test_the_messages_add_their_words_and_nothing_else(self):
        document = self.receipt()
        plain = (flat(generate_fiscal_pdf(document)), flat(generate_fiscal_ticket_pdf(document)))
        self.save_design(document.company, **MESSAGES)
        designed = (flat(generate_fiscal_pdf(document)), flat(generate_fiscal_ticket_pdf(document)))

        for before, after in zip(plain, designed):
            self.assertEqual(tokens(after) - tokens(before), tokens(THANKS) + tokens(FOOTER))
            self.assertEqual(tokens(before) - tokens(after), Counter())

    def test_an_empty_design_adds_no_text(self):
        document = self.receipt()
        self.save_design(document.company, thank_you_message='', footer_text='')
        with_row = (flat(generate_fiscal_pdf(document)), flat(generate_fiscal_ticket_pdf(document)))
        CompanyDocumentDesign.objects.filter(company=document.company).delete()
        without_row = (flat(generate_fiscal_pdf(document)), flat(generate_fiscal_ticket_pdf(document)))
        self.assertEqual(with_row, without_row)

    def test_the_options_of_the_sales_note_never_reach_a_fiscal_paper(self):
        document = self.receipt()
        plain = [page_streams(render(self.ctx(document))) for render in (render_fiscal_a4, render_fiscal_ticket)]
        designed = [page_streams(render(self.ctx(document, **NOT_FOR_FISCAL)))
                    for render in (render_fiscal_a4, render_fiscal_ticket)]
        self.assertEqual(designed, plain)


# ---------------------------------------------------------------------------
# What a design never moves
# ---------------------------------------------------------------------------

EVERYTHING = {
    **NOT_FOR_FISCAL, **MESSAGES, 'logo_size': 'large', 'logo_a4_width_mm': 90, 'logo_ticket_width_mm': 72,
    'logo_a4_align': 'right', 'logo_ticket_align': 'left',
}


class FiscalDataUnchangedTest(FiscalDesignBase):
    def documents(self):
        return (('factura', self.invoice()), ('boleta', self.receipt()))

    def test_printing_with_a_design_changes_nothing_stored(self):
        for kind, document in self.documents():
            with self.subTest(kind):
                before = model_to_dict(FiscalDocument.objects.get(pk=document.pk))
                xml_hash = hashlib.sha256(document.signed_xml.encode()).hexdigest()
                self.save_design(document.company, **EVERYTHING)
                generate_fiscal_pdf(document)
                generate_fiscal_ticket_pdf(document)
                after = FiscalDocument.objects.get(pk=document.pk)
                self.assertEqual(model_to_dict(after), before)
                self.assertEqual(hashlib.sha256(after.signed_xml.encode()).hexdigest(), xml_hash)

    def test_the_fiscal_data_of_the_context_does_not_depend_on_the_design(self):
        for kind, document in self.documents():
            with self.subTest(kind):
                CompanyDocumentDesign.objects.filter(company=document.company).delete()
                plain = build_fiscal_context(document)
                self.assertEqual(plain['design'], DEFAULT)
                self.save_design(document.company, **EVERYTHING)
                designed = build_fiscal_context(document)
                self.assertEqual(designed['design'], replace(DEFAULT, **EVERYTHING))
                plain.pop('design'), designed.pop('design')
                self.assertEqual(designed, plain)          # QR payload and image, digest, legend, amounts…

    def test_every_fiscal_text_is_printed_with_any_design(self):
        for kind, document in self.documents():
            self.save_design(document.company, **EVERYTHING)
            ctx = build_fiscal_context(document)
            payload = ctx['qr_payload'].split('|')
            self.assertEqual(payload[:4], [ctx['issuer']['tax_id'], document.document_type, document.series,
                                           str(document.number)])
            for name, pdf in (('a4', generate_fiscal_pdf(document)), ('ticket', generate_fiscal_ticket_pdf(document))):
                with self.subTest(kind=kind, paper=name):
                    text = self.readable(pdf)
                    for expected in (
                        ctx['identifier'], f"RUC {ctx['issuer']['tax_id']}", ctx['issuer']['legal_name'],
                        ctx['title'], ctx['customer']['doc_number'], ctx['customer']['legal_name'],
                        ctx['issued_at'].strftime('%d/%m/%Y %H:%M'),
                        f"S/ {ctx['taxable_amount']:.2f}", f"S/ {ctx['tax_amount']:.2f}", f"S/ {ctx['total']:.2f}",
                        'IGV (18%)', f"SON: {ctx['amount_in_words']}", f"{ctx['legend']}.",
                        f"Estado: {ctx['status_text']}", f"Valor resumen: {ctx['digest_value']}",
                        *legends.FISCAL_BETA_ROLL_LINES,
                    ):
                        self.assertIn(expected, text)
                    for unwanted in ('Recibo de tienda', 'posventa', 'comercial de prueba', 'garantía'):
                        self.assertNotIn(unwanted, text)
                    # The QR is there, at its size.
                    qr = drawn_images(pdf)[-1][-1]
                    self.assertAlmostEqual(qr[0], QR_A4 if name == 'a4' else QR_ROLL, delta=0.05)


class FiscalWarningsTest(FiscalDesignBase):
    def set(self, document, **fields):
        FiscalDocument.objects.filter(pk=document.pk).update(**fields)
        return FiscalDocument.objects.get(pk=document.pk)

    def test_beta_band_and_diagonal_with_a_design(self):
        document = self.receipt()
        self.save_design(document.company, **EVERYTHING)
        a4 = self.readable(generate_fiscal_pdf(document))
        self.assertIn(legends.FISCAL_BETA_WARNING, a4)
        self.assertEqual(page_texts(generate_fiscal_pdf(document))[0].count('SIN VALIDEZ TRIBUTARIA'), 2)
        roll = self.readable(generate_fiscal_ticket_pdf(document))
        for line in legends.FISCAL_BETA_ROLL_LINES:
            self.assertIn(line, roll)
        # The roll's warning sits under the number, before the buyer.
        self.assertLess(roll.index(legends.FISCAL_BETA_ROLL_LINES[1]), roll.index('DNI:'))

    def test_production_carries_no_beta_mark(self):
        document = self.set(self.receipt(), environment=FiscalEnvironment.PRODUCTION,
                            status=FiscalDocumentStatus.ACCEPTED)
        self.save_design(document.company, **EVERYTHING)
        for pdf in (generate_fiscal_pdf(document), generate_fiscal_ticket_pdf(document)):
            text = self.readable(pdf)
            self.assertNotIn('SUNAT BETA', text)
            self.assertNotIn('SIN VALIDEZ', text.upper())
            self.assertIn('Estado: Aceptada por SUNAT', text)

    def test_rejected_and_pending_say_so_before_the_messages(self):
        for status, wording in ((FiscalDocumentStatus.REJECTED, legends.FISCAL_REJECTED_STATUS),
                                (FiscalDocumentStatus.SUBMISSION_ERROR, 'Pendiente de confirmación de SUNAT')):
            document = self.set(self.receipt(), status=status)
            self.save_design(document.company, **EVERYTHING)
            for pdf in (generate_fiscal_pdf(document), generate_fiscal_ticket_pdf(document)):
                with self.subTest(status=status):
                    text = self.readable(pdf)
                    self.assertIn(f'Estado: {wording}', text)
                    self.assertNotIn('Aceptada por SUNAT', text)
                    self.assertLess(text.index(wording), text.index(THANKS.split('\n')[0]))


# ---------------------------------------------------------------------------
# What the messages that reach a fiscal paper may say
# ---------------------------------------------------------------------------

#: Words and data that belong to the fiscal block. A message printed under it must not echo them.
FISCAL_ECHOES = (
    'Consulta tu compra en SUNAT', 'Validado por la S.U.N.A.T', '5UNAT', 'Su compra fue aceptada',
    'Pedido ACEPTADO', 'Venta rechazada', 'Pedido anulado', 'Observada por error', 'Dar de baja el pedido',
    'Valor resumen: abc', 'Representación impresa', 'Representacion impresa', 'Tiene validez', 'V4lidez legal',
    'Documento tributario', 'Datos tributarios', 'Precios incluyen IGV', 'I.G.V. incluido',
    'Importe total S/ 10', 'Op. gravada', 'RUC 20123456789', 'Nuestro RUC: 10456789012', '15123456789',
    '17123456789',
)
#: Ordinary messages that must keep passing.
ORDINARY = (
    'Gracias por su compra', 'Bajamos precios cada semana', 'Trabaja con nosotros', 'Teléfono 987654321',
    'Pedido 20123456', 'Código 3012345678901', 'Abrimos de lunes a sábado',
)


class FiscalMessageScreenTest(FiscalDesignBase):
    def test_the_messages_that_reach_a_fiscal_paper_refuse_fiscal_echoes(self):
        from store.document_design import DesignError, design_text_problem, validated

        for field in ('thank_you_message', 'footer_text'):
            for text in FISCAL_ECHOES:
                with self.subTest(field=field, text=text):
                    self.assertIn('boletas y facturas', design_text_problem(field, text))
                    with self.assertRaises(DesignError) as refused:
                        validated({field: text})
                    self.assertIn(field, refused.exception.errors)
            for text in ORDINARY:
                with self.subTest(field=field, text=text):
                    self.assertEqual(design_text_problem(field, text), '')

    def test_the_texts_that_never_reach_a_fiscal_paper_keep_their_rules(self):
        from store.document_design import design_text_problem

        for field in ('post_sale_message', 'commercial_message', 'warranty_summary'):
            for text in ('Garantía con validez de 12 meses', 'Precios incluyen IGV'):
                with self.subTest(field=field, text=text):
                    self.assertEqual(design_text_problem(field, text), '')

    def test_a_stored_message_that_fails_the_screen_is_not_printed(self):
        document = self.receipt()
        # Rows saved before the screen existed are written straight to the table.
        self.save_design(document.company, thank_you_message='Aceptada por SUNAT', footer_text='Pie de prueba bueno')
        with self.assertLogs('store.fiscal_pdf_services', 'WARNING') as logs:
            texts = [self.readable(generate_fiscal_pdf(document)), self.readable(generate_fiscal_ticket_pdf(document))]
        for text in texts:
            self.assertNotIn('Aceptada', text)
            self.assertIn('Pie de prueba bueno', text)
        self.assertTrue(all('thank_you_message' in line for line in logs.output), logs.output)

        self.save_design(document.company, thank_you_message='Gracias de prueba', footer_text='RUC 20123456789')
        with self.assertLogs('store.fiscal_pdf_services', 'WARNING'):
            for text in (self.readable(generate_fiscal_pdf(document)),
                         self.readable(generate_fiscal_ticket_pdf(document))):
                self.assertIn('Gracias de prueba', text)
                self.assertNotIn('20123456789', text)

    def test_a_clean_design_logs_nothing(self):
        document = self.receipt()
        self.save_design(document.company, **MESSAGES)
        with self.assertNoLogs('store.fiscal_pdf_services', 'WARNING'):
            generate_fiscal_pdf(document)
            generate_fiscal_ticket_pdf(document)


# ---------------------------------------------------------------------------
# Many lines, long names
# ---------------------------------------------------------------------------

LONG = ('Accesorio de prueba con un nombre tan largo que tiene que partirse en varias líneas del papel '
        'y además ConUnaPalabraSinEspaciosMuyLargaParaProbarElCorteDeLinea1234567890')


class FiscalLayoutTest(FiscalDesignBase):
    def many(self, count=70, **design):
        ctx = self.ctx(self.receipt(), **design)
        line = ctx['lines'][0]
        ctx['lines'] = [dict(line, description=f'Artículo de prueba {n:03d}') for n in range(count)]
        return ctx

    def test_a_long_items_table_repeats_its_header_and_the_beta_mark_on_every_page(self):
        pdf = render_fiscal_a4(self.many(**MESSAGES))
        pages = page_texts(pdf)

        self.assertGreaterEqual(len(pages), 2)
        for number, text in enumerate(pages, start=1):
            with self.subTest(page=number):
                for header in ('Cant.', 'Und.', 'Descripción', 'V. unitario', 'P. unitario', 'Importe'):
                    if number < len(pages) or 'Artículo' in text:
                        self.assertIn(header, text)
                self.assertIn('SIN VALIDEZ TRIBUTARIA', text)
                self.assertIn('SUNAT BETA', text)
        everything = ' '.join(pages)
        for n in range(70):
            self.assertIn(f'Artículo de prueba {n:03d}', everything)
        # The mandatory block and then the messages close the LAST page.
        last = joined(pages[-1])
        self.assertIn('Valor resumen:', last)
        self.assertLess(last.index('Valor resumen:'), last.index(FOOTER))

    def test_the_qr_block_is_never_split_from_its_legend(self):
        for count in range(20, 46):
            pdf = render_fiscal_a4(self.many(count))
            pages = page_texts(pdf)
            images = drawn_images(pdf)
            qr_page = max(index for index, page in enumerate(images) if page)
            self.assertIn('Representación impresa', pages[qr_page], count)
            self.assertIn('Valor resumen:', pages[qr_page], count)

    def summary_on_first_page(self, pdf) -> bool:
        first = page_texts(pdf)[0]
        qr = [image for image in drawn_images(pdf)[0] if abs(image[0] - QR_A4) < 0.05]
        return all(marker in first for marker in ('IMPORTE TOTAL', 'Representación impresa', 'Valor resumen:')) \
            and bool(qr)

    def test_the_messages_never_push_the_fiscal_summary_off_a_page_where_it_fits(self):
        overflowed = 0
        for count in range(1, 26):
            with self.subTest(count=count):
                plain = render_fiscal_a4(self.many(count))
                if len(page_texts(plain)) > 1:
                    continue
                self.assertTrue(self.summary_on_first_page(plain))
                designed = render_fiscal_a4(self.many(count, **LONG_MESSAGES))
                self.assertTrue(self.summary_on_first_page(designed), count)
                if len(page_texts(designed)) > 1:
                    overflowed += 1
                    # Only the messages went on: the last page starts with them.
                    self.assertNotIn('IMPORTE TOTAL', page_texts(designed)[-1])
                    self.assertIn('Renglón 1', page_texts(designed)[-1])
        self.assertGreater(overflowed, 0, 'no count made the messages overflow: the test proves nothing')

    def test_a_long_header_and_the_widest_logo_keep_the_summary_on_page_one(self):
        overflowed = 0
        for count in range(1, 16):
            with self.subTest(count=count):
                ctx = self.many(count, logo_a4_width_mm=90, **LONG_MESSAGES)
                ctx['logo_png'] = SQUARE
                ctx['issuer'] = dict(ctx['issuer'], trade_name='Nombre comercial de prueba ' * 4,
                                     address='Avenida de prueba 1234, urbanización de prueba, distrito ' * 3)
                plain = render_fiscal_a4(dict(ctx, design=replace(DEFAULT, logo_a4_width_mm=90)))
                if len(page_texts(plain)) > 1:
                    continue
                designed = render_fiscal_a4(ctx)
                self.assertTrue(self.summary_on_first_page(designed), count)
                overflowed += len(page_texts(designed)) > 1
        self.assertGreater(overflowed, 0, 'no count made the messages overflow: the test proves nothing')

    def test_the_beta_diagonal_is_drawn_over_the_content_on_every_page(self):
        """Over the QR's white quiet zone too: drawn last, translucent, so nothing hides «SIN»."""
        pdf = render_fiscal_a4(self.many(70, **MESSAGES))
        streams = page_streams(pdf)
        self.assertGreaterEqual(len(streams), 2)
        for number, stream in enumerate(streams, start=1):
            with self.subTest(page=number):
                diagonal = stream.rfind(b'(SIN VALIDEZ TRIBUTARIA) Tj')
                self.assertGreater(diagonal, 0)
                self.assertGreater(diagonal, stream.rfind(b' Do'))
                self.assertTrue(page_texts(pdf)[number - 1].endswith('SIN VALIDEZ TRIBUTARIA SUNAT BETA'))
        # Same mark as ever: 34 pt, red at 16 %, 38 degrees, the two lines 42 pt apart.
        self.assertRegex(streams[0], rb'/F\d+ 34 Tf')
        self.assertIn(b'(SUNAT BETA) Tj', streams[0])

    def test_the_stream_readers_of_these_tests_ignore_operators_inside_text(self):
        """A digest is base64: «/Q», «+q» or a lone «Q» in it is text, not a graphics operator."""
        ctx = self.ctx(self.receipt(), logo=WIDE)
        ctx['digest_value'] = 'ab/Q+q Q q (Q) cm Do'
        pdf = render_fiscal_a4(ctx)
        images = drawn_images(pdf)[0]
        self.assertEqual(len(images), 2)
        self.assertAlmostEqual(images[-1][0], QR_A4, delta=0.05)
        self.assertIn('Valor resumen: ab/Q+q Q q (Q) cm Do', [run[0] for run in text_runs(pdf)[0]])

    def test_long_descriptions_wrap_without_losing_a_character(self):
        ctx = self.ctx(self.receipt())
        ctx['lines'] = [dict(ctx['lines'][0], description=LONG)]
        for render in (render_fiscal_a4, render_fiscal_ticket):
            with self.subTest(render.__name__):
                squeezed = ''.join(flat(render(ctx)).split())
                self.assertIn(''.join(LONG.split()), squeezed)

    def test_large_amounts_stay_inside_their_columns(self):
        """Every amount is drawn whole (one run, never wrapped) and inside its own column or the totals' figures."""
        from decimal import Decimal

        from store.fiscal_pdf_services import _A4_LINE_WIDTHS_CM

        ctx = self.ctx(self.receipt())
        big = Decimal('9999999.99')
        ctx['lines'] = [dict(ctx['lines'][0], unit_price=big, unit_price_with_tax=big, amount=big,
                             quantity=Decimal('999.50'))]
        ctx.update(taxable_amount=big, tax_amount=big, total=big)
        runs = [run for run in text_runs(render_fiscal_a4(ctx))[0] if run[0] == 'S/ 9999999.99']

        edges = [A4_LEFT]
        for width in _A4_LINE_WIDTHS_CM:
            edges.append(edges[-1] + width * CM)
        columns = [(edges[index], edges[index + 1]) for index in (3, 4, 5)]       # V. unitario, P. unitario, Importe
        figures = (A4_LEFT + 17 * CM - 3.5 * CM, A4_LEFT + 17 * CM)               # the totals' amounts
        self.assertEqual(len(runs), 6, runs)
        for text, left, right in runs:
            with self.subTest(left=left):
                self.assertTrue(any(low - 0.05 <= left and right <= high + 0.05 for low, high in [*columns, figures]),
                                (left, right, columns, figures))


# ---------------------------------------------------------------------------
# The goldens
# ---------------------------------------------------------------------------

class FiscalGoldenTokensTest(FiscalDesignBase):
    """The recorded fiscal goldens hold exactly the words of the pre-design ones: moved, never lost or added."""

    def test_every_pre_design_token_is_still_printed_the_same_number_of_times(self):
        before = json.loads(PRE_DESIGN_FILE.read_text(encoding='utf-8'))
        now = json.loads(GOLDEN_FILE.read_text(encoding='utf-8'))
        names = [name for name in before if name.startswith('fiscal_')]
        self.assertEqual(len(names), 4)
        for name in names:
            with self.subTest(name):
                self.assertEqual(now[name]['pages'], before[name]['pages'])
                self.assertEqual(tokens(' '.join(now[name]['texts'])), tokens(' '.join(before[name]['texts'])))

    def test_the_designed_goldens_add_only_the_messages(self):
        before = json.loads(PRE_DESIGN_FILE.read_text(encoding='utf-8'))
        now = json.loads(GOLDEN_FILE.read_text(encoding='utf-8'))
        for name, base in (('designed_fiscal_boleta_a4', 'fiscal_boleta_a4'),
                           ('designed_fiscal_factura_ticket80', 'fiscal_factura_ticket80')):
            with self.subTest(name):
                added = tokens(' '.join(now[name]['texts'])) - tokens(' '.join(before[base]['texts']))
                lost = tokens(' '.join(before[base]['texts'])) - tokens(' '.join(now[name]['texts']))
                self.assertEqual(added, tokens(THANKS) + tokens(FOOTER))
                self.assertEqual(lost, Counter())


class DesignedFiscalGoldenTest(FiscalDesignBase):
    """The pinned boleta and factura with the messages and a wider logo box (no logo image: none is frozen)."""

    def designed(self, document):
        self.save_design(document.company, **MESSAGES, logo_a4_width_mm=70, logo_ticket_width_mm=50)
        return document

    def test_designed_receipt_a4(self):
        document = self.designed(self.receipt())
        check_golden(self, 'designed_fiscal_boleta_a4', generate_fiscal_pdf(document),
                     normalise=self.normaliser(document))

    def test_designed_invoice_ticket80(self):
        document = self.designed(self.invoice())
        check_golden(self, 'designed_fiscal_factura_ticket80', generate_fiscal_ticket_pdf(document),
                     normalise=self.normaliser(document))
