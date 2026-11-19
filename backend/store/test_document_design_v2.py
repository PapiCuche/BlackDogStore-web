"""
DOC-DESIGN-04 — the second round of the per-company design of the SALES NOTE.

What is pinned here (on top of `test_document_design_settings`, DOC-DESIGN-03):

  * the new options — the title, the logo in millimetres and its alignment,
    the switches of the optional data, the post-sale and commercial messages,
    the warranty presentation, the tax breakdown, density, the emphasis of the
    total and the order of the informative blocks — are validated on the
    server (lengths, lines, characters a PDF font or a cp858 thermal head
    cannot print, emojis, words that would make the note pass for a fiscal
    document) and saved per company, audited, with `company.manage`;
  * their DEFAULTS draw exactly what the papers drew before (the goldens of
    `test_document_goldens` do not change; here: the same drawing operators);
  * every switch hides its block on the A4, the 80 mm ticket and the thermal
    print; the messages go AFTER the totals on the three, and a long one wraps
    without losing a character (inside the 72 mm of the roll, inside 48 columns);
  * the preview draws an UNSAVED draft of all of it;
  * the migration only adds columns with database defaults and reverses.
"""
import re
from pathlib import Path
from unittest import mock

from django.db import connection
from django.test import TestCase

from store import document_design, document_preview, document_layout
from store.document_design import DEFAULT, DocumentDesign
from store.models import AdminAuditLog, CompanyDocumentDesign
from store.printing.escpos import sales_note_ticket
from store.sales_note_services import generate_sales_note_pdf, sales_note_document
from store.test_document_design import media_box, page_streams, text_of
from store.test_document_design_settings import DesignBase, URL, image_widths
from store.test_document_robustness import RecordingCanvas
from store.ticket_services import generate_sales_note_ticket_pdf, ticket_pdf_from_ctx

#: Made up for the tests. Never a real shop's sentences.
POST_SALE = 'Conserve este papel y la caja del equipo.\nCambios dentro de los 7 días con este papel.'
COMMERCIAL = 'Síguenos en redes: @tiendadeprueba · 10% en accesorios con este papel.'
LONG_WORD = 'https://tienda.example/garantia/condiciones-completas-del-servicio-tecnico-autorizado-para-equipos'
LONG_MESSAGE = ('Visite nuestro taller para el mantenimiento preventivo de su equipo cada seis meses; '
                'la revisión es gratuita durante el primer año. ' + LONG_WORD)

MIGRATION = '0177_document_design_v2'


def squashed(text: str) -> str:
    return re.sub(r'\s+', '', text)


_ESCPOS_CODES = re.compile(rb'\x1b@|\x1b[taEd].|\x1d!.|\x1dV.\x00?|\x1d\(k.*?\n', re.S)


def thermal_lines(raw: bytes) -> list[str]:
    """The printed lines of a thermal ticket: the ESC/POS orders removed, decoded as the head does."""
    return _ESCPOS_CODES.sub(b'', raw).decode('cp858').split('\n')


class FullRecordingCanvas(RecordingCanvas):
    """`RecordingCanvas` that also accepts the rules, dashes and images of a whole ticket."""

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


class V2Base(DesignBase):
    def sample_ctx(self, design=None, **overrides):
        ctx = document_preview._sales_note_ctx(self.company, document_preview._lines(0), design or DEFAULT)
        ctx.update(overrides)
        return ctx

    def papers_of(self, ctx) -> dict:
        """The three papers of one sales-note context, as text."""
        with mock.patch('store.sales_note_services.build_sales_note_context', return_value=ctx):
            thermal = sales_note_ticket(None)
        return {
            'a4': text_of(document_layout.render_a4(sales_note_document(ctx))),
            'ticket': text_of(ticket_pdf_from_ctx(ctx)),
            'thermal': '\n'.join(thermal_lines(thermal)),
        }

    def thermal_of(self, ctx) -> bytes:
        with mock.patch('store.sales_note_services.build_sales_note_context', return_value=ctx):
            return sales_note_ticket(None)


# ---------------------------------------------------------------------------
# Defaults: the papers as they were
# ---------------------------------------------------------------------------

class DefaultsReproduceThePaperTest(V2Base):
    def test_the_model_defaults_are_the_pure_defaults_and_they_print_what_was_printed(self):
        row = CompanyDocumentDesign(company=self.company)
        self.assertEqual(document_design.from_row(row), DEFAULT)
        self.assertEqual(DEFAULT.document_title, 'Nota de venta')
        self.assertEqual((DEFAULT.logo_a4_width_mm, DEFAULT.logo_ticket_width_mm), (0, 0))
        self.assertEqual((DEFAULT.logo_a4_align, DEFAULT.logo_ticket_align), ('left', 'center'))
        self.assertEqual((DEFAULT.warranty_mode, DEFAULT.tax_breakdown_mode, DEFAULT.density),
                         ('full', 'detailed', 'normal'))
        self.assertTrue(DEFAULT.emphasize_total)
        for field in document_design.INFO_FIELDS:
            self.assertTrue(getattr(DEFAULT, field), field)
        for field in ('post_sale_message', 'commercial_message', 'warranty_summary', 'thank_you_message'):
            self.assertEqual(getattr(DEFAULT, field), '', field)
        self.assertEqual(DEFAULT.block_order, document_design.BLOCKS)

    def test_a_saved_row_of_defaults_draws_the_same_operators_as_no_row(self):
        self.with_logo()
        note = self.note()
        before = (page_streams(generate_sales_note_pdf(note)), page_streams(generate_sales_note_ticket_pdf(note)),
                  sales_note_ticket(note))
        CompanyDocumentDesign.objects.create(company=self.company)
        after = (page_streams(generate_sales_note_pdf(note)), page_streams(generate_sales_note_ticket_pdf(note)),
                 sales_note_ticket(note))
        self.assertEqual(before, after)

    def test_the_new_options_at_their_defaults_change_nothing_on_the_sample(self):
        explicit = DocumentDesign(**{**DEFAULT.as_dict(), 'logo_a4_width_mm': 0, 'density': 'normal'})
        for kind in ('sales_note', 'ticket80_sales_note'):
            with self.subTest(kind):
                self.assertEqual(
                    page_streams(document_preview.render_preview(kind, company=self.company, design=DEFAULT)),
                    page_streams(document_preview.render_preview(kind, company=self.company, design=explicit)))

    def test_no_migration_seeds_a_message_and_no_shop_is_written_into_the_code(self):
        root = Path(__file__).resolve().parent
        migration = (root / 'migrations' / f'{MIGRATION}.py').read_text(encoding='utf-8').lower()
        self.assertNotIn('runpython', migration)
        self.assertNotIn('runsql', migration)
        for module in ('document_design.py', 'document_design_views.py', f'migrations/{MIGRATION}.py'):
            source = (root / module).read_text(encoding='utf-8').lower()
            for forbidden in ('black dog', 'blackdog', 'manada', 'gracias por confiar', 'gracias por tu compra'):
                self.assertNotIn(forbidden, source, f'{module}: {forbidden}')


# ---------------------------------------------------------------------------
# The API: payload, save, audit, permissions, tenants
# ---------------------------------------------------------------------------

EVERYTHING_V2 = {
    'document_title': 'Orden de venta',
    'logo_a4_width_mm': 70, 'logo_ticket_width_mm': 50, 'logo_a4_align': 'center', 'logo_ticket_align': 'left',
    'show_seller': False, 'show_order': False, 'show_channel': False, 'show_requested_receipt': False,
    'show_counts': False, 'show_amount_words': False, 'show_customer_contact': False,
    'show_delivery_address': False,
    'thank_you_message': '¡Gracias por elegirnos!\nVuelve pronto.',
    'post_sale_message': POST_SALE, 'commercial_message': COMMERCIAL,
    'warranty_mode': 'summary', 'warranty_summary': 'Garantía de 12 meses por defecto de fábrica.',
    'tax_breakdown_mode': 'summary', 'density': 'airy', 'emphasize_total': False,
    'block_order': ['sale', 'customer', 'commercial', 'post_sale', 'thank_you', 'warranty', 'notes'],
}


class ApiTest(V2Base):
    def test_the_payload_carries_the_new_fields_their_options_and_limits(self):
        body = self.api.get(URL).json()

        self.assertEqual(set(body['design']), set(document_design.FIELDS))
        self.assertEqual(body['design']['block_order'], list(document_design.BLOCKS))
        self.assertEqual(body['limits']['thank_you_message'], document_design.THANK_YOU_MAX)
        self.assertGreater(document_design.THANK_YOU_MAX, 120)
        for key in ('post_sale_message', 'commercial_message', 'warranty_summary', 'document_title'):
            self.assertIn(key, body['limits'])
        self.assertEqual([o['value'] for o in body['logo_aligns']], ['left', 'center', 'right'])
        self.assertEqual([o['value'] for o in body['warranty_modes']], ['full', 'summary'])
        self.assertEqual([o['value'] for o in body['tax_breakdown_modes']], ['detailed', 'summary', 'hidden'])
        self.assertEqual([o['value'] for o in body['densities']], ['compact', 'normal', 'airy'])
        self.assertEqual([o['value'] for o in body['blocks']], list(document_design.BLOCKS))
        self.assertEqual(body['logo_width_mm'], {'a4': [20, 90], 'ticket': [20, 72]})
        self.assertEqual(body['max_lines'], document_design.MESSAGE_MAX_LINES)
        self.assertIn('warranty_url', body['contact'])

    def test_everything_is_saved_and_audited_with_before_and_after(self):
        response = self.patch(**EVERYTHING_V2)

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['design']['block_order'], EVERYTHING_V2['block_order'])
        design = document_design.design_for(self.company)
        for field, value in EVERYTHING_V2.items():
            expected = tuple(value) if isinstance(value, list) else value
            self.assertEqual(getattr(design, field), expected, field)
        log = AdminAuditLog.objects.get(action='document_design_updated')
        self.assertEqual(log.company_id, self.company.pk)
        self.assertEqual(log.metadata['changed_fields'], sorted(EVERYTHING_V2))
        self.assertEqual(log.metadata['old']['document_title'], 'Nota de venta')
        self.assertEqual(log.metadata['new']['block_order'], EVERYTHING_V2['block_order'])
        self.assertEqual(log.metadata['new']['post_sale_message'], POST_SALE)

    def test_company_view_cannot_change_it(self):
        self.only('company.view')
        response = self.patch(post_sale_message=POST_SALE)
        self.assertEqual(response.status_code, 403)
        self.assertFalse(CompanyDocumentDesign.objects.exists())

    def test_company_b_neither_reads_nor_writes_company_as_design(self):
        theirs = CompanyDocumentDesign.objects.create(company=self.other, commercial_message='Promoción ajena')

        self.assertEqual(self.api.get(URL + f'?company={self.other.pk}').status_code, 404)
        response = self.patch(f'?company={self.other.pk}', commercial_message='Cambiado', document_title='Otro')
        self.assertEqual(response.status_code, 404)
        theirs.refresh_from_db()
        self.assertEqual((theirs.commercial_message, theirs.document_title), ('Promoción ajena', 'Nota de venta'))
        self.assertNotIn('Promoción ajena', text_of(generate_sales_note_pdf(self.note())))
        self.assertNotIn('Promoción ajena', text_of(self.pdf('sales_note')))

    def test_multi_line_messages_keep_their_lines_normalised(self):
        self.patch(post_sale_message='  Línea   uno \r\n\n\n\n  línea\tdos  \n\n', footer_text='uno\ndos')
        row = CompanyDocumentDesign.objects.get()
        self.assertEqual(row.post_sale_message, 'Línea uno\n\nlínea dos')
        self.assertEqual(row.footer_text, 'uno dos')          # the footer stays one line


class ValidationTest(V2Base):
    def assert_refused(self, field, value, *, says=''):
        response = self.patch(**{field: value})
        self.assertEqual(response.status_code, 400, f'{field}={value!r}: {response.content[:300]}')
        self.assertIn(field, response.json())
        if says:
            self.assertIn(says, ' '.join(response.json()[field]))
        self.assertFalse(CompanyDocumentDesign.objects.exists())
        self.assertFalse(AdminAuditLog.objects.filter(action='document_design_updated').exists())
        return response

    def test_lengths_are_bounded(self):
        for field in ('thank_you_message', 'post_sale_message', 'commercial_message', 'warranty_summary',
                      'document_title'):
            with self.subTest(field):
                limit = document_design.LIMITS[field]
                self.assert_refused(field, 'x' * (limit + 1), says=f'Máximo {limit}')
                self.assertEqual(self.patch(**{field: 'y' * limit}).status_code, 200)
                CompanyDocumentDesign.objects.all().delete()
                AdminAuditLog.objects.all().delete()

    def test_messages_have_a_bounded_number_of_lines(self):
        many = '\n'.join(f'línea {n}' for n in range(document_design.MESSAGE_MAX_LINES + 1))
        self.assert_refused('post_sale_message', many, says='renglones')
        fine = '\n'.join(f'línea {n}' for n in range(document_design.MESSAGE_MAX_LINES))
        self.assertEqual(self.patch(post_sale_message=fine).status_code, 200)

    def test_emojis_are_refused_with_their_own_message(self):
        for field in ('thank_you_message', 'commercial_message', 'document_title', 'footer_text'):
            with self.subTest(field):
                self.assert_refused(field, 'Gracias 🐾', says='emoji')
        self.assert_refused('commercial_message', 'Oferta ❤️', says='emoji')
        self.assert_refused('commercial_message', 'Ok 👍🏽', says='emoji')

    def test_characters_neither_the_pdf_font_nor_the_thermal_head_print_are_refused(self):
        for value in ('Ω omega', 'Ir → aquí', 'Casi ≈ igual', 'CO₂', '中文'):
            with self.subTest(value):
                self.assert_refused('post_sale_message', value, says='no puede imprimir')

    def test_what_is_accepted_prints_on_the_thermal_head_without_a_question_mark(self):
        text = '¡Ñandú! Cámbialo en 7 días — € 5 · “oferta” … ™ ü'
        self.assertEqual(self.patch(commercial_message=text).status_code, 200)
        ctx = self.sample_ctx(document_design.design_for(self.company))
        printed = '\n'.join(thermal_lines(self.thermal_of(ctx)))
        self.assertNotIn('?', printed)
        for kept in ('¡Ñandú!', 'Cámbialo', '€ 5', 'ü'):
            self.assertIn(kept, printed)

    def test_markup_is_refused_in_the_new_texts(self):
        self.assert_refused('commercial_message', 'Promo <b>ya</b>', says='texto plano')
        self.assert_refused('document_title', 'Nota <de> venta')

    def test_the_title_cannot_name_a_fiscal_document(self):
        for title in ('Factura', 'FACTURA ELECTRÓNICA', 'Fáctura', 'Boleta de venta', 'boleta', 'B.O.L.E.T.A',
                      'Comprobante de pago', 'comprobante electrónico', 'Comprobante', 'Recibo por honorarios',
                      'RECIBO X HONORARIOS', 'Ticket fiscal', 'Liquidación de compra', 'liquidacion de compra',
                      'Nota de crédito', 'NOTA DE DEBITO', 'Nota de débito', 'Nota crédito', 'F a c t u r a'):
            with self.subTest(title):
                self.assert_refused('document_title', title, says='comprobante')

    def test_look_alike_characters_do_not_get_a_fiscal_name_through(self):
        for title in ('B0LETA', 'COMPR0BANTE', 'BØLETA ELECTRÓNICA', 'Bøleta', 'ƒactura', 'FACTUR4',
                      'Nota de crédit0', 'Bolet@', 'Bo|eta', 'BO1ETA', 'BOIETA', 'T1CKET F1SCAL', 'TICKET FlSCAL',
                      'Nota de déb1to', 'L1QU1DAC1ÓN DE COMPRA', 'Rec1bo x honorar1os', 'C0MPR0BANT3',
                      'Fa¢tura', 'Bolet4', 'factur@', 'Recibo por h0n0rari0s', 'n0ta de cred1t0', 'ßoleta',
                      'B ø l e t a', 'F.A.C.T.U.R.A', 'Fa©tura', 'Factµra', '7icket fiscal', 'Bole+a', 'Bol3ta',
                      'Compr0b4nt3', 'Bo¦eta', 'Fac7ura', 'Boleta¡'):
            with self.subTest(title):
                self.assert_refused('document_title', title, says='comprobante')

    def test_a_fiscal_word_inside_another_word_is_not_a_fiscal_name(self):
        self.assertEqual(self.patch(document_title='Manufactura propia').status_code, 200)
        self.assertEqual(self.patch(warranty_summary='Cubre defectos de manufactura.').status_code, 200)

    def test_no_text_printed_on_the_note_may_name_a_fiscal_document(self):
        labels = {
            'footer_text': 'El pie de página', 'thank_you_message': 'El mensaje final',
            'post_sale_message': 'El mensaje posventa', 'commercial_message': 'El mensaje comercial',
            'warranty_summary': 'El resumen de la garantía', 'document_title': 'El título',
        }
        for field, label in labels.items():
            for value in ('Pide tu factura', 'Cambios con su boleta', 'Guarde su c0mpr0bante',
                          'Emitimos nota de crédito', 'Ticket fiscal aparte'):
                with self.subTest(field=field, value=value):
                    response = self.assert_refused(field, value, says='comprobante de pago')
                    self.assertIn(label, ' '.join(response.json()[field]))

    def test_a_multi_line_message_is_screened_across_its_lines(self):
        self.assert_refused('post_sale_message', 'Pida su\nboleta en caja', says='comprobante de pago')

    def test_ordinary_titles_are_accepted_and_an_empty_one_is_not(self):
        self.assert_refused('document_title', '   ')
        for title in ('Nota de venta', 'Orden de venta', 'Nota de entrega', 'Pedido de mostrador'):
            with self.subTest(title):
                self.assertEqual(self.patch(document_title=title).status_code, 200)

    def test_the_logo_width_is_zero_or_inside_its_range_and_an_integer(self):
        self.assert_refused('logo_a4_width_mm', 19)
        self.assert_refused('logo_a4_width_mm', 91)
        self.assert_refused('logo_ticket_width_mm', 73)
        for value in ('50', 50.5, True, None, -1):
            with self.subTest(value=value):
                self.assert_refused('logo_ticket_width_mm', value)
        self.assertEqual(self.patch(logo_a4_width_mm=90, logo_ticket_width_mm=20).status_code, 200)
        self.assertEqual(self.patch(logo_a4_width_mm=0).status_code, 200)

    def test_the_choices_are_closed(self):
        for field, value in (('logo_a4_align', 'justify'), ('logo_ticket_align', ''), ('warranty_mode', 'qr'),
                             ('tax_breakdown_mode', 'full'), ('density', 'huge'), ('density', 2)):
            with self.subTest(field=field, value=value):
                self.assert_refused(field, value)
        for field in document_design.INFO_FIELDS + ('emphasize_total',):
            with self.subTest(field):
                self.assert_refused(field, 'true')

    def test_the_block_order_is_a_permutation_of_the_known_blocks(self):
        blocks = list(document_design.BLOCKS)
        for value in (blocks[:-1], blocks + ['legal'], [*blocks[:-1], blocks[0]], 'customer,sale', None,
                      [1, 2], [*blocks[:-1], 'legal']):
            with self.subTest(value=value):
                self.assert_refused('block_order', value)
        self.assertEqual(self.patch(block_order=list(reversed(blocks))).status_code, 200)
        self.assertEqual(document_design.design_for(self.company).block_order, tuple(reversed(blocks)))

    def test_no_block_is_the_legal_identification(self):
        self.assertNotIn('legal', document_design.BLOCKS)
        self.assertNotIn('control_line', document_design.BLOCKS)

    def test_the_model_validates_the_same_rules(self):
        from django.core.exceptions import ValidationError

        for fields in ({'document_title': 'Factura'}, {'commercial_message': 'Hola 🐾'},
                       {'commercial_message': 'Pide tu b0leta'}, {'footer_text': 'Factura aparte'},
                       {'logo_a4_width_mm': 5}, {'block_order': 'customer'}, {'density': 'huge'}):
            with self.subTest(fields):
                with self.assertRaises(ValidationError):
                    CompanyDocumentDesign(company=self.company, **fields).full_clean()


# ---------------------------------------------------------------------------
# What the options do on the three papers
# ---------------------------------------------------------------------------

def _design(**fields) -> DocumentDesign:
    return DocumentDesign(**{**DEFAULT.as_dict(), **fields})


class SwitchesTest(V2Base):
    #: what each switch hides, as each paper writes it (`None`: that paper never printed it).
    SHOWN = {
        'show_seller': {'a4': 'Atendido por', 'ticket': 'Atendido por:', 'thermal': 'Atendido por:'},
        'show_order': {'a4': 'Pedido', 'ticket': 'Pedido:', 'thermal': 'Pedido:'},
        'show_channel': {'a4': 'Mostrador', 'ticket': None, 'thermal': None},
        # DOC-LEGEND-01: the note never names the requested receipt, whatever the switch says.
        'show_requested_receipt': {'a4': None, 'ticket': None, 'thermal': None},
        'show_counts': {'a4': 'Unidades:', 'ticket': 'Productos / unidades', 'thermal': 'Productos / unidades'},
        'show_amount_words': {'a4': 'SON:', 'ticket': 'SON:', 'thermal': 'SON:'},
        'show_customer_contact': {'a4': '999 999 999', 'ticket': None, 'thermal': None},
        'show_delivery_address': {'a4': 'Calle Falsa 742', 'ticket': 'Calle Falsa 742',
                                  'thermal': 'Calle Falsa 742'},
    }

    def test_each_switch_hides_its_block_on_every_paper_that_prints_it(self):
        for field, shown in self.SHOWN.items():
            with self.subTest(field):
                on = self.papers_of(self.sample_ctx(full_address='Calle Falsa 742, Cercado'))
                off = self.papers_of(self.sample_ctx(_design(**{field: False}),
                                                     full_address='Calle Falsa 742, Cercado'))
                for paper, words in shown.items():
                    if words is None:
                        continue
                    self.assertIn(words, on[paper], f'{field} on {paper}')
                    self.assertNotIn(words, off[paper], f'{field} off {paper}')
                for paper in ('a4', 'ticket', 'thermal'):
                    # Who buys, what and how much never go.
                    for kept in ('CLIENTE DE MUESTRA', 'TOTAL', 'Funda'):
                        self.assertIn(kept, off[paper], f'{field} off {paper}: {kept}')

    def test_hiding_the_order_does_not_hide_the_number_of_the_note(self):
        papers = self.papers_of(self.sample_ctx(_design(show_order=False)))
        for paper, text in papers.items():
            self.assertIn(document_preview.SAMPLE_NUMBER, text, paper)

    def test_switches_work_on_a_real_note(self):
        note = self.note()
        self.design(show_seller=False, show_requested_receipt=False, show_counts=False)
        a4, ticket = text_of(generate_sales_note_pdf(note)), text_of(generate_sales_note_ticket_pdf(note))
        thermal = '\n'.join(thermal_lines(sales_note_ticket(note)))
        for name, text in (('a4', a4), ('ticket', ticket), ('thermal', thermal)):
            self.assertNotIn('Comprobante solicitado', text, name)
            self.assertNotIn('Atendido por', text, name)
            self.assertIn('María Fernanda Quispe', text, name)


class MessagesTest(V2Base):
    def positions(self, text: str, total_word: str, *needles):
        total = text.rfind(total_word)
        self.assertGreater(total, -1)
        return total, [text.find(needle) for needle in needles]

    def test_the_messages_go_after_the_totals_on_the_three_papers_in_their_order(self):
        design = _design(thank_you_message='Gracias de prueba', post_sale_message=POST_SALE,
                         commercial_message=COMMERCIAL)
        papers = self.papers_of(self.sample_ctx(design))
        for paper, total_word in (('a4', 'IMPORTE TOTAL'), ('ticket', 'TOTAL'), ('thermal', 'TOTAL')):
            text = papers[paper]
            total, (thanks, post_sale, commercial) = self.positions(
                text, total_word, 'Gracias de prueba', 'Conserve este papel', 'Síguenos en redes')
            self.assertGreater(thanks, total, paper)
            self.assertGreater(post_sale, thanks, paper)
            self.assertGreater(commercial, post_sale, paper)
            self.assertIn('Cambios dentro de los 7 días', text, paper)

    def test_without_messages_nothing_is_printed_in_their_place(self):
        papers = self.papers_of(self.sample_ctx())
        for paper, text in papers.items():
            self.assertNotIn('Conserve', text, paper)

    def test_a_long_message_wraps_inside_the_roll_without_losing_a_character(self):
        from reportlab.lib.units import mm

        from store.ticket_services import TICKET_MARGIN_MM, TICKET_WIDTH_MM, _lay_out

        ctx = self.sample_ctx(_design(post_sale_message=LONG_MESSAGE, commercial_message=LONG_WORD,
                                      thank_you_message=LONG_WORD))
        canvas = FullRecordingCanvas()
        left = float(TICKET_MARGIN_MM) * mm
        width = float(TICKET_WIDTH_MM - 2 * TICKET_MARGIN_MM) * mm
        _lay_out(canvas, ctx, width, left, 2000.0)
        for x0, x1, _y, text in canvas.drawn:
            self.assertGreaterEqual(x0, left - 0.01, text)
            self.assertLessEqual(x1, left + width + 0.01, text)
        drawn = squashed(''.join(entry[3] for entry in canvas.drawn))
        self.assertIn(squashed(LONG_MESSAGE), drawn)
        self.assertEqual(drawn.count(squashed(LONG_WORD)), 3)
        self.assertIn(squashed(LONG_MESSAGE), squashed(text_of(ticket_pdf_from_ctx(ctx))))

    def test_a_long_message_wraps_inside_48_columns_without_losing_a_character(self):
        ctx = self.sample_ctx(_design(post_sale_message=LONG_MESSAGE, commercial_message=LONG_WORD))
        lines = thermal_lines(self.thermal_of(ctx))
        for line in lines:
            self.assertLessEqual(len(line), 48, line)
        printed = squashed(''.join(lines))
        self.assertIn(squashed(LONG_MESSAGE), printed)
        self.assertIn(squashed(LONG_WORD), printed)

    def test_a_long_message_wraps_on_the_a4(self):
        ctx = self.sample_ctx(_design(post_sale_message=LONG_MESSAGE, commercial_message=LONG_WORD))
        text = text_of(document_layout.render_a4(sales_note_document(ctx)))
        self.assertIn(squashed(LONG_MESSAGE), squashed(text))

    def test_each_line_of_a_message_is_its_own_line_on_the_roll(self):
        ctx = self.sample_ctx(_design(post_sale_message='Primera línea\nSegunda línea'))
        lines = [line.strip() for line in thermal_lines(self.thermal_of(ctx))]
        self.assertIn('Primera línea', lines)
        self.assertIn('Segunda línea', lines)
        canvas = FullRecordingCanvas()
        from store.ticket_services import _lay_out
        _lay_out(canvas, ctx, 204.1, 11.34, 2000.0)
        drawn = [entry[3] for entry in canvas.drawn]
        self.assertIn('Primera línea', drawn)
        self.assertIn('Segunda línea', drawn)

    def test_a_real_note_prints_the_saved_messages(self):
        self.design(post_sale_message=POST_SALE, commercial_message=COMMERCIAL)
        note = self.note()
        for name, text in (('a4', text_of(generate_sales_note_pdf(note))),
                           ('ticket', text_of(generate_sales_note_ticket_pdf(note))),
                           ('thermal', '\n'.join(thermal_lines(sales_note_ticket(note))))):
            self.assertIn('Conserve este papel', text, name)
            self.assertIn('Síguenos en redes', text, name)


class WarrantyTest(V2Base):
    FULL = 'Garantía de 12 meses por defecto de fábrica.'

    def test_full_is_the_policy_text_as_before(self):
        for paper, text in self.papers_of(self.sample_ctx()).items():
            self.assertIn(self.FULL, text, paper)

    def test_summary_prints_the_summary_and_the_url_instead_of_the_text(self):
        from store.models import CompanySettings

        CompanySettings.objects.filter(company=self.company).update(
            warranty_policy_url='https://tienda.example/garantia')
        self.company.refresh_from_db()
        design = _design(warranty_mode='summary', warranty_summary='12 meses de garantía.')
        for paper, text in self.papers_of(self.sample_ctx(design)).items():
            self.assertNotIn(self.FULL, text, paper)
            self.assertIn('12 meses de garantía.', text, paper)
            self.assertIn('tienda.example/garantia', squashed(text), paper)

    def test_summary_without_a_summary_nor_a_url_keeps_the_full_text(self):
        for paper, text in self.papers_of(self.sample_ctx(_design(warranty_mode='summary'))).items():
            self.assertIn(self.FULL, text, paper)


class TitleTest(V2Base):
    def test_the_title_is_printed_in_capitals_on_the_three_papers_and_in_the_a4_footer(self):
        papers = self.papers_of(self.sample_ctx(_design(document_title='Orden de venta')))
        for paper, text in papers.items():
            self.assertIn('ORDEN DE VENTA', text, paper)
            self.assertNotIn('NOTA DE VENTA', text, paper)
        self.assertIn(f'Orden de venta {document_preview.SAMPLE_NUMBER}', papers['a4'])

    def test_a_real_note_uses_the_saved_title(self):
        self.design(document_title='Orden de venta')
        note = self.note()
        self.assertIn('ORDEN DE VENTA', text_of(generate_sales_note_pdf(note)))
        self.assertIn('ORDEN DE VENTA', text_of(generate_sales_note_ticket_pdf(note)))
        self.assertIn('ORDEN DE VENTA', '\n'.join(thermal_lines(sales_note_ticket(note))))


class TaxBreakdownTest(V2Base):
    def test_detailed_is_the_paper_as_it_was(self):
        for paper, text in self.papers_of(self.sample_ctx()).items():
            self.assertIn('Op. gravada', text, paper)
            self.assertIn('IGV (18%)', text, paper)

    def test_summary_prints_one_line_with_the_same_tax(self):
        ctx = self.sample_ctx(_design(tax_breakdown_mode='summary'))
        amount = f"{ctx['tax']['tax_amount']:.2f}"
        for paper, text in self.papers_of(ctx).items():
            self.assertNotIn('Op. gravada', text, paper)
            self.assertIn('Incluye IGV (18%)', text, paper)
            self.assertIn(amount, text, paper)

    def test_hidden_prints_no_breakdown_and_the_total_is_untouched(self):
        ctx = self.sample_ctx(_design(tax_breakdown_mode='hidden'))
        total = f"{ctx['tax']['total']:.2f}"
        for paper, text in self.papers_of(ctx).items():
            self.assertNotIn('Op. gravada', text, paper)
            self.assertNotIn('IGV', text, paper)
            self.assertIn('Subtotal', text, paper)
            self.assertIn(total, text, paper)


class StyleTest(V2Base):
    def test_density_changes_the_length_of_the_roll_within_bounds(self):
        heights = {density: media_box(ticket_pdf_from_ctx(self.sample_ctx(_design(density=density))))[1]
                   for density in ('compact', 'normal', 'airy')}
        self.assertLess(heights['compact'], heights['normal'])
        self.assertLess(heights['normal'], heights['airy'])
        self.assertLess(heights['airy'], heights['normal'] * 1.3)

    def test_density_changes_the_a4_and_normal_is_the_default(self):
        streams = {density: page_streams(document_layout.render_a4(
            sales_note_document(self.sample_ctx(_design(density=density)))))
            for density in ('compact', 'normal', 'airy')}
        self.assertNotEqual(streams['compact'], streams['normal'])
        self.assertNotEqual(streams['airy'], streams['normal'])
        self.assertEqual(streams['normal'], page_streams(document_layout.render_a4(
            sales_note_document(self.sample_ctx()))))

    def test_airy_adds_blank_lines_on_the_thermal_print(self):
        normal = thermal_lines(self.thermal_of(self.sample_ctx()))
        airy = thermal_lines(self.thermal_of(self.sample_ctx(_design(density='airy'))))
        self.assertGreater(airy.count(''), normal.count(''))

    def test_the_emphasis_of_the_total_can_be_turned_off(self):
        from store.test_document_design import SHADE_FILL

        plain = self.sample_ctx(_design(emphasize_total=False))
        a4_on = b''.join(page_streams(document_layout.render_a4(sales_note_document(self.sample_ctx()))))
        a4_off = b''.join(page_streams(document_layout.render_a4(sales_note_document(plain))))
        self.assertLess(a4_off.count(SHADE_FILL), a4_on.count(SHADE_FILL))
        self.assertIn('IMPORTE TOTAL', text_of(document_layout.render_a4(sales_note_document(plain))))

        ticket_on = b''.join(page_streams(ticket_pdf_from_ctx(self.sample_ctx())))
        ticket_off = b''.join(page_streams(ticket_pdf_from_ctx(plain)))
        total_at_12 = rb'/F\d+ 12 Tf [\d.]+ TL ET\s*BT 1 0 0 1 [\d.]+ [\d.]+ Tm \(TOTAL\) Tj'
        self.assertRegex(ticket_on, total_at_12)
        self.assertNotRegex(ticket_off, total_at_12)

        large = b'\x1d!\x11'
        on = self.thermal_of(self.sample_ctx())
        off = self.thermal_of(plain)
        self.assertIn(large + b'TOTAL', on)
        self.assertNotIn(large + b'TOTAL', off)
        self.assertIn(b'TOTAL', off)


class LogoTest(V2Base):
    def test_millimetres_override_the_three_sizes_on_each_format(self):
        self.with_logo()
        # DOC-DESIGN-FREEZE: a note keeps the design it was issued with, so each design gets its own note.
        self.design(logo_size='small', logo_a4_width_mm=80, logo_ticket_width_mm=65)
        note = self.note()
        a4 = image_widths(generate_sales_note_pdf(note))[0]
        ticket = image_widths(generate_sales_note_ticket_pdf(note))[0]
        self.design(logo_size='large', logo_a4_width_mm=0, logo_ticket_width_mm=0)
        note = self.note()
        self.assertGreater(a4, image_widths(generate_sales_note_pdf(note))[0])
        self.assertGreater(ticket, image_widths(generate_sales_note_ticket_pdf(note))[0])

    def test_every_box_has_a_height_cap(self):
        for width in range(20, 91):
            box = _design(logo_a4_width_mm=width).a4_logo_box_cm
            self.assertLessEqual(box[1] * 10, document_design.LOGO_A4_MAX_HEIGHT_MM + 1e-9)
            self.assertAlmostEqual(box[0], width / 10)
        for width in range(20, 73):
            box = _design(logo_ticket_width_mm=width).ticket_logo_box_mm
            self.assertLessEqual(box[1], document_design.LOGO_TICKET_MAX_HEIGHT_MM + 1e-9)
            self.assertLessEqual(box[0], 72)
        for size in document_design.LOGO_SIZES:
            self.assertLessEqual(_design(logo_size=size).ticket_logo_box_mm[1], document_design.LOGO_TICKET_MAX_HEIGHT_MM)
            self.assertLessEqual(_design(logo_size=size).a4_logo_box_cm[1] * 10, document_design.LOGO_A4_MAX_HEIGHT_MM)

    def test_the_alignment_moves_the_logo_on_the_a4_and_the_ticket(self):
        self.with_logo()

        def logo_x(pdf):
            stream = b''.join(page_streams(pdf))
            # A4: a table cell translates, then scales; the roll draws at its position.
            nested = re.search(rb'1 0 0 1 ([\d.-]+) [\d.-]+ cm\s*q\s*[\d.]+ 0 0 [\d.]+ 0 0 cm\s*/FormXob', stream)
            if nested:
                return float(nested.group(1))
            return float(re.search(rb'[\d.]+ 0 0 [\d.]+ ([\d.-]+) [\d.-]+ cm\s*/FormXob\.\w+ Do', stream).group(1))

        positions = {}
        for align in ('left', 'center', 'right'):
            self.design(logo_a4_align=align, logo_ticket_align=align)
            note = self.note()                  # DOC-DESIGN-FREEZE: the design is the note's, from its issue
            positions[align] = (logo_x(generate_sales_note_pdf(note)), logo_x(generate_sales_note_ticket_pdf(note)))
        for paper in (0, 1):
            self.assertLess(positions['left'][paper], positions['center'][paper])
            self.assertLess(positions['center'][paper], positions['right'][paper])


class BlockOrderTest(V2Base):
    MARKS = {
        'notes': 'Observaciones de muestra.', 'warranty': 'Garantía de 12 meses',
        'thank_you': 'Gracias de prueba', 'post_sale': 'Mensaje posventa de prueba',
        'commercial': 'Mensaje comercial de prueba',
    }

    def design_with(self, order=None):
        fields = {'thank_you_message': self.MARKS['thank_you'], 'post_sale_message': self.MARKS['post_sale'],
                  'commercial_message': self.MARKS['commercial']}
        if order is not None:
            fields['block_order'] = order
        return _design(**fields)

    def sequence(self, text, keys):
        positions = {key: text.find(self.MARKS[key]) for key in keys}
        for key, position in positions.items():
            self.assertGreater(position, -1, key)
        return sorted(keys, key=positions.get)

    def test_the_default_order_is_the_order_each_paper_always_had(self):
        closing = ['notes', 'warranty', 'thank_you', 'post_sale', 'commercial']
        for paper, text in self.papers_of(self.sample_ctx(self.design_with())).items():
            self.assertEqual(self.sequence(text, closing), closing, paper)

    def test_a_new_order_moves_the_closing_blocks_on_the_three_papers(self):
        order = ['customer', 'sale', 'commercial', 'post_sale', 'thank_you', 'warranty', 'notes']
        papers = self.papers_of(self.sample_ctx(self.design_with(order)))
        for paper, text in papers.items():
            self.assertEqual(self.sequence(text, order[2:]), order[2:], paper)

    def test_the_closing_blocks_stay_after_the_totals_and_hidden_ones_leave_no_trace(self):
        order = ['sale', 'customer', 'thank_you', 'notes', 'commercial', 'warranty', 'post_sale']
        design = _design(block_order=order, thank_you_message=self.MARKS['thank_you'])
        papers = self.papers_of(self.sample_ctx(design))
        for paper, total_word in (('a4', 'IMPORTE TOTAL'), ('ticket', 'TOTAL'), ('thermal', 'TOTAL')):
            text = papers[paper]
            total = text.rfind(total_word)
            self.assertEqual(self.sequence(text, ['thank_you', 'notes', 'warranty']), ['thank_you', 'notes', 'warranty'])
            self.assertGreater(text.find(self.MARKS['thank_you']), total, paper)
            self.assertNotIn('Mensaje posventa de prueba', text)

    def test_customer_and_sale_swap_their_columns_on_the_a4(self):
        def first(order):
            text = self.papers_of(self.sample_ctx(_design(block_order=order)))['a4']
            return 'customer' if text.find('CLIENTE') < text.find('DATOS DE LA VENTA') else 'sale'

        self.assertEqual(first(list(document_design.BLOCKS)), 'customer')
        self.assertEqual(first(['sale', 'customer', *document_design.BLOCKS[2:]]), 'sale')

    def test_the_rolls_keep_the_sale_data_under_the_number_whatever_the_order(self):
        for order in (list(document_design.BLOCKS), ['sale', 'customer', *document_design.BLOCKS[2:]]):
            papers = self.papers_of(self.sample_ctx(_design(block_order=order)))
            for paper in ('ticket', 'thermal'):
                text = papers[paper]
                self.assertLess(text.find('Fecha:'), text.find('Cliente:'), paper)

    def test_a_real_note_follows_the_saved_order(self):
        order = ['customer', 'sale', 'warranty', 'notes', 'thank_you', 'post_sale', 'commercial']
        self.design(block_order=','.join(order), thank_you_message='Gracias de prueba')
        note = self.note_for(self.sale([(self.case, 1)], notes='Observaciones de muestra.'))
        for name, text in (('a4', text_of(generate_sales_note_pdf(note))),
                           ('ticket', text_of(generate_sales_note_ticket_pdf(note))),
                           ('thermal', '\n'.join(thermal_lines(sales_note_ticket(note))))):
            self.assertEqual(self.sequence(text, ['warranty', 'notes', 'thank_you']),
                             ['warranty', 'notes', 'thank_you'], name)


# ---------------------------------------------------------------------------
# The preview draws an unsaved draft
# ---------------------------------------------------------------------------

class DraftPreviewTest(V2Base):
    def test_a_draft_with_every_new_option_is_drawn_and_never_stored(self):
        draft = {**EVERYTHING_V2, 'thank_you_message': 'á' * document_design.THANK_YOU_MAX,
                 'post_sale_message': 'é' * document_design.LIMITS['post_sale_message'],
                 'commercial_message': 'ñ' * document_design.LIMITS['commercial_message'],
                 'warranty_summary': 'ó' * document_design.LIMITS['warranty_summary'],
                 'footer_text': 'ú' * document_design.LIMITS['footer_text']}
        for kind in ('sales_note', 'ticket80_sales_note'):
            with self.subTest(kind):
                response = self.preview(kind, design=draft)
                self.assertEqual(response.status_code, 200, response.content[:300])
                text = text_of(response.content)
                self.assertIn('ORDEN DE VENTA', text)
                self.assertIn('ééééé', text)
                self.assertNotIn('Atendido por', text)
        self.assertFalse(CompanyDocumentDesign.objects.exists())
        self.assertFalse(AdminAuditLog.objects.filter(action='document_design_updated').exists())

    def test_a_reordered_draft_is_drawn_in_its_order(self):
        order = ['customer', 'sale', 'thank_you', 'warranty', 'notes', 'post_sale', 'commercial']
        for kind in ('sales_note', 'ticket80_sales_note'):
            with self.subTest(kind):
                response = self.preview(kind, design={'block_order': order, 'thank_you_message': 'Gracias de prueba'})
                text = text_of(response.content)
                self.assertLess(text.find('Gracias de prueba'), text.find('Garantía de 12 meses'))
                self.assertLess(text.find('Garantía de 12 meses'), text.find('Observaciones de muestra.'))
        self.assertFalse(CompanyDocumentDesign.objects.exists())

    def test_an_invalid_draft_is_refused_like_a_save(self):
        response = self.preview('sales_note', design={'document_title': 'Boleta'})
        self.assertEqual(response.status_code, 400)
        self.assertIn('document_title', response.json()['design'])
        response = self.preview('sales_note', design={'block_order': ['legal']})
        self.assertEqual(response.status_code, 400)

    def test_the_fiscal_samples_take_only_the_logo_and_the_messages_of_the_draft(self):
        # FISCAL-DESIGN-01: a boleta or factura takes the logo's size and alignment, the thank-you message and
        # the footer. Every other option of the sales note leaves its drawing exactly as it was.
        from collections import Counter

        fiscal_options = ('logo_a4_width_mm', 'logo_ticket_width_mm', 'logo_a4_align', 'logo_ticket_align',
                          'thank_you_message', 'footer_text')
        note_only = {key: value for key, value in EVERYTHING_V2.items() if key not in fiscal_options}
        for kind in ('boleta', 'ticket80_factura'):
            with self.subTest(kind):
                plain = self.preview(kind).content
                self.assertEqual(page_streams(self.preview(kind, design=note_only).content), page_streams(plain))
                drafted = self.preview(kind, design=EVERYTHING_V2).content
                words = Counter(text_of(drafted).split())
                words.subtract(Counter(text_of(plain).split()))
                self.assertEqual(+words, Counter(EVERYTHING_V2['thank_you_message'].split()))
                self.assertEqual(-words, Counter())                     # nothing printed before is lost


# ---------------------------------------------------------------------------
# The migration
# ---------------------------------------------------------------------------

class MigrationTest(TestCase):
    def test_it_only_adds_columns_with_database_defaults_and_widens_the_message(self):
        from importlib import import_module

        from django.db import migrations

        module = import_module(f'store.migrations.{MIGRATION}')
        operations = module.Migration.operations
        self.assertTrue(operations)
        for operation in operations:
            self.assertIsInstance(operation, (migrations.AddField, migrations.AlterField))
            if isinstance(operation, migrations.AddField):
                self.assertEqual(operation.model_name, 'companydocumentdesign')
                self.assertIsNot(operation.field.db_default, operation.field.__class__().db_default,
                                 operation.name)
        altered = [op.name for op in operations if isinstance(op, migrations.AlterField)]
        self.assertEqual(altered, ['thank_you_message'])

    def test_it_goes_back_and_forth_keeping_the_rows(self):
        from django.db.migrations.executor import MigrationExecutor

        from store.models import Company

        company = Company.objects.create(name='Migración DD04', slug='migracion-dd04')
        CompanyDocumentDesign.objects.create(company=company, thank_you_message='Hola', footer_text='Pie')
        previous = ('store', '0176_serial_transfer_units')

        def columns():
            with connection.cursor() as cursor:
                return {col.name for col in connection.introspection.get_table_description(
                    cursor, CompanyDocumentDesign._meta.db_table)}

        with connection.cursor() as cursor:
            cursor.execute('SET CONSTRAINTS ALL IMMEDIATE')     # no pending trigger before an ALTER TABLE
        executor = MigrationExecutor(connection)
        executor.migrate([previous])
        self.assertNotIn('post_sale_message', columns())
        self.assertNotIn('document_title', columns())

        executor = MigrationExecutor(connection)
        executor.migrate([('store', MIGRATION)])
        self.assertIn('post_sale_message', columns())
        with connection.cursor() as cursor:
            cursor.execute(f'SELECT thank_you_message, footer_text, document_title, show_seller, block_order '
                           f'FROM {CompanyDocumentDesign._meta.db_table} WHERE company_id = %s', [company.pk])
            row = cursor.fetchone()
        self.assertEqual(row, ('Hola', 'Pie', 'Nota de venta', True, ','.join(document_design.BLOCKS)))
