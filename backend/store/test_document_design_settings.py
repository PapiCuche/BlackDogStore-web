"""
DOC-DESIGN-03 — the per-company design of the INTERNAL papers.

    GET   /api/admin/document-design/?company=<id>   `company.view`
    PATCH /api/admin/document-design/?company=<id>   `company.manage`
    POST  /api/admin/document-preview/  {"document": …, "design": {…draft…}}

What is pinned here:

  * a company without a row prints what it printed before (the defaults), and
    reading the design writes nothing; the thank-you message is EMPTY for every
    company and no migration seeds one;
  * who may read and change it, inside which company, with an audit row that
    carries before/after; validation is server-side (length, plain text,
    printable characters, strict types);
  * what each option does on the A4 sales note, the order receipt, the 80 mm
    ticket and the thermal print — and that the control line («sin validez
    tributaria») and «No es una serie fiscal» are printed whatever the design;
  * the fiscal papers (boleta/factura A4 and 80 mm, real and sample) are drawn
    IDENTICALLY with any design: same drawing operators, page by page;
  * a draft sent to the preview is validated like a save and never stored.
"""
import re
from pathlib import Path
from unittest import mock

from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from store import document_design, document_preview
from store.document_design import DEFAULT, DocumentDesign
from store.fiscal_pdf_services import generate_fiscal_pdf, generate_fiscal_ticket_pdf
from store.models import AdminAuditLog, Company, CompanyDocumentDesign, CompanySettings
from store.pdf_services import generate_order_receipt_pdf
from store.printing.escpos import sales_note_ticket
from store.sales_note_services import SALES_NOTE_CONTROL_LINE, generate_sales_note_pdf
from store.test_document_design import page_streams, page_texts, text_of
from store.test_document_goldens import FiscalFixtures
from store.test_document_preview import PreviewBase
from store.test_stock_units import IMEI_A
from store.ticket_services import generate_sales_note_ticket_pdf

URL = '/api/admin/document-design/'
PREVIEW_URL = '/api/admin/document-preview/'
#: Made up for the tests. Never a real shop's sentence.
THANKS = 'Gracias por su compra en la tienda de prueba.'
FOOTER = 'Horario de atención: lunes a sábado. Soporte técnico A&B.'

EVERYTHING_CHANGED = {
    'logo_size': 'large', 'show_phone': False, 'show_email': False, 'show_address': False,
    'show_website': True, 'footer_text': FOOTER, 'thank_you_message': THANKS, 'hide_zero_columns': True,
    'show_email_on_roll': True,
}


def image_widths(pdf: bytes) -> list[float]:
    """The drawn width of every image on every page (`w 0 0 h x y cm /… Do`)."""
    widths = []
    for stream in page_streams(pdf):
        widths += [float(w) for w in re.findall(rb'([\d.]+) 0 0 [\d.]+ [\d.-]+ [\d.-]+ cm\s*/FormXob\.\w+ Do', stream)]
    return widths


class DesignBase(PreviewBase):
    def setUp(self):
        super().setUp()
        CompanySettings.objects.filter(company=self.company).update(website_url='https://tienda.example')

    def design(self, **fields):
        return CompanyDocumentDesign.objects.update_or_create(company=self.company, defaults=fields)[0]

    def patch(self, query='', **body):
        return self.api.patch(URL + query, body, format='json')

    def note(self):
        return self.note_for(self.sale([(self.case, 1)]))


# ---------------------------------------------------------------------------
# Defaults and the absence of a row
# ---------------------------------------------------------------------------

class DefaultsTest(DesignBase):
    def test_a_company_without_a_row_gets_the_defaults_and_reading_writes_nothing(self):
        with CaptureQueriesContext(connection) as queries:
            design = document_design.design_for(self.company)

        self.assertEqual(design, DEFAULT)
        self.assertEqual(design.thank_you_message, '')
        self.assertEqual(design.footer_text, '')
        self.assertEqual(design.logo_size, 'medium')
        self.assertFalse(CompanyDocumentDesign.objects.exists())
        self.assertFalse([q for q in queries.captured_queries if re.match(r'\s*(INSERT|UPDATE|DELETE)', q['sql'], re.I)])

    def test_the_default_box_of_the_logo_is_the_one_documents_always_had(self):
        from store import document_style as style

        self.assertEqual(DEFAULT.a4_logo_box_cm, style.LOGO_A4_CM)
        self.assertEqual(DEFAULT.ticket_logo_box_mm, style.LOGO_TICKET_MM)

    def test_the_model_defaults_equal_the_pure_defaults(self):
        row = CompanyDocumentDesign(company=self.company)
        self.assertEqual(document_design.from_row(row), DEFAULT)

    def test_the_get_of_a_company_without_a_row_answers_the_defaults_and_creates_nothing(self):
        response = self.api.get(URL)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['design'], DEFAULT.as_dict())
        self.assertEqual(response.json()['design']['thank_you_message'], '')
        self.assertFalse(CompanyDocumentDesign.objects.exists())

    def test_no_migration_seeds_a_design_or_a_message(self):
        folder = Path(__file__).resolve().parent / 'migrations'
        migration = next(folder.glob('*_company_document_design.py'))
        source = migration.read_text(encoding='utf-8')

        self.assertNotIn('RunPython', source)
        self.assertNotIn('RunSQL', source)
        for forbidden in ('gracias', 'manada', 'black dog', 'actitud'):
            self.assertNotIn(forbidden, source.lower())

    def test_a_new_company_has_no_message_on_its_papers(self):
        company = Company.objects.create(name='Empresa nueva', slug='empresa-nueva-dd03')
        self.assertEqual(document_design.design_for(company).thank_you_message, '')

    def test_no_shop_is_written_into_the_design_code(self):
        root = Path(__file__).resolve().parent
        for module in ('document_design.py', 'document_design_views.py'):
            source = (root / module).read_text(encoding='utf-8').lower()
            for forbidden in ('black dog', 'blackdog', 'manada', 'gracias por confiar', 'actitud'):
                self.assertNotIn(forbidden, source, f'{module}: {forbidden}')


# ---------------------------------------------------------------------------
# The API
# ---------------------------------------------------------------------------

class PermissionsTest(DesignBase):
    def test_anonymous_is_401(self):
        self.assertEqual(APIClient().get(URL).status_code, 401)
        self.assertEqual(APIClient().patch(URL, {}, format='json').status_code, 401)

    def test_company_view_reads_and_is_told_it_cannot_manage(self):
        self.only('company.view')
        response = self.api.get(URL)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['can_manage'])

    def test_company_manage_reads_and_is_told_it_can_manage(self):
        self.only('company.view', 'company.manage')
        self.assertTrue(self.api.get(URL).json()['can_manage'])

    def test_company_manage_alone_also_reads_it(self):
        self.only('company.manage')
        response = self.api.get(URL)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['can_manage'])

    def test_without_any_capability_the_get_is_403(self):
        self.only('sales.notes.manage')
        self.assertEqual(self.api.get(URL).status_code, 403)

    def test_company_view_alone_cannot_change_it_and_nothing_is_written(self):
        self.only('company.view')
        response = self.patch(thank_you_message=THANKS)

        self.assertEqual(response.status_code, 403)
        self.assertFalse(CompanyDocumentDesign.objects.exists())
        self.assertFalse(AdminAuditLog.objects.filter(action='document_design_updated').exists())

    def test_the_route_is_throttled_like_its_neighbours(self):
        from store.document_design_views import AdminDocumentDesignView
        from store.throttles import AdminUsersThrottle

        self.assertEqual(AdminDocumentDesignView.throttle_classes, [AdminUsersThrottle])


class TenantIsolationTest(DesignBase):
    def test_another_companys_design_answers_like_one_that_does_not_exist(self):
        CompanyDocumentDesign.objects.create(company=self.other, thank_you_message='Ajeno')
        found = self.api.get(URL + f'?company={self.other.pk}')
        missing = self.api.get(URL + '?company=99999999')

        self.assertEqual(found.status_code, 404)
        self.assertEqual(found.json(), missing.json())
        self.assertNotIn('Ajeno', found.content.decode())

    def test_a_patch_aimed_at_another_company_changes_nothing(self):
        theirs = CompanyDocumentDesign.objects.create(company=self.other, thank_you_message='Ajeno')

        response = self.patch(f'?company={self.other.pk}', thank_you_message='Cambiado')

        self.assertEqual(response.status_code, 404)
        theirs.refresh_from_db()
        self.assertEqual(theirs.thank_you_message, 'Ajeno')
        self.assertFalse(CompanyDocumentDesign.objects.filter(company=self.company).exists())

    def test_a_company_key_in_the_body_never_chooses_the_company(self):
        response = self.patch(company=self.other.pk, thank_you_message=THANKS)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(CompanyDocumentDesign.objects.get(company=self.company).thank_you_message, THANKS)
        self.assertFalse(CompanyDocumentDesign.objects.filter(company=self.other).exists())

    def test_one_companys_design_never_reaches_anothers_papers(self):
        CompanyDocumentDesign.objects.create(company=self.other, thank_you_message='Mensaje de la otra empresa')

        self.assertNotIn('Mensaje de la otra empresa', text_of(generate_sales_note_pdf(self.note())))
        self.assertNotIn('Mensaje de la otra empresa', text_of(self.pdf('sales_note')))


class SaveTest(DesignBase):
    def test_a_change_is_saved_and_audited_with_before_and_after(self):
        response = self.patch(thank_you_message=THANKS, logo_size='small')

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['design']['thank_you_message'], THANKS)
        row = CompanyDocumentDesign.objects.get(company=self.company)
        self.assertEqual((row.thank_you_message, row.logo_size), (THANKS, 'small'))
        log = AdminAuditLog.objects.get(action='document_design_updated')
        self.assertEqual(log.company_id, self.company.pk)
        self.assertEqual(log.metadata['changed_fields'], ['logo_size', 'thank_you_message'])
        self.assertEqual(log.metadata['old'], {'logo_size': 'medium', 'thank_you_message': ''})
        self.assertEqual(log.metadata['new'], {'logo_size': 'small', 'thank_you_message': THANKS})

    def test_a_patch_that_changes_nothing_writes_nothing(self):
        response = self.patch(logo_size='medium', show_phone=True)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(CompanyDocumentDesign.objects.exists())
        self.assertFalse(AdminAuditLog.objects.filter(action='document_design_updated').exists())

    def test_whitespace_is_normalised_before_it_is_stored(self):
        self.patch(footer_text='  Línea uno\n\n  línea\tdos  ')
        self.assertEqual(CompanyDocumentDesign.objects.get().footer_text, 'Línea uno línea dos')

    def test_the_message_can_be_emptied_again(self):
        self.design(thank_you_message=THANKS)
        self.patch(thank_you_message='')
        self.assertEqual(CompanyDocumentDesign.objects.get().thank_you_message, '')

    def test_the_payload_is_the_whitelist_and_nothing_fiscal(self):
        body = self.api.get(URL).json()

        self.assertEqual(set(body['design']), set(document_design.FIELDS))
        for fiscal in ('tax_id', 'ruc', 'legal_name', 'series', 'number', 'total', 'qr', 'digest'):
            self.assertNotIn(fiscal, body['design'])
        self.assertEqual(body['limits'], {'footer_text': 200, 'thank_you_message': 120})
        self.assertEqual([option['value'] for option in body['logo_sizes']], ['small', 'medium', 'large'])
        self.assertEqual(body['contact']['phone'], '054 555 000')
        self.assertEqual(body['contact']['website'], 'https://tienda.example')

    def test_unknown_keys_are_ignored(self):
        response = self.patch(tax_id='20999999999', legal_name='Otra S.A.C.', thank_you_message=THANKS)

        self.assertEqual(response.status_code, 200)
        self.assertNotIn('tax_id', response.json()['design'])


class ValidationTest(DesignBase):
    def assert_refused(self, field, value):
        response = self.patch(**{field: value})
        self.assertEqual(response.status_code, 400, f'{field}={value!r}: {response.content[:200]}')
        self.assertIn(field, response.json())
        self.assertFalse(CompanyDocumentDesign.objects.exists())
        self.assertFalse(AdminAuditLog.objects.filter(action='document_design_updated').exists())

    def test_lengths_are_bounded(self):
        self.assert_refused('footer_text', 'x' * 201)
        self.assert_refused('thank_you_message', 'x' * 121)
        self.assertEqual(self.patch(footer_text='x' * 200, thank_you_message='y' * 120).status_code, 200)

    def test_markup_is_refused(self):
        self.assert_refused('footer_text', 'Pie <b>negrita</b>')
        self.assert_refused('thank_you_message', 'Gracias >')

    def test_a_character_the_printer_cannot_draw_is_refused_instead_of_printing_a_question_mark(self):
        self.assert_refused('thank_you_message', 'Gracias 🐾')
        self.assert_refused('footer_text', 'CO₂ neutro')

    def test_the_logo_size_is_one_of_three(self):
        for value in ('huge', '', None, 3, ['large']):
            with self.subTest(value=value):
                self.assert_refused('logo_size', value)

    def test_switches_are_real_booleans(self):
        for value in ('yes', 'true', 1, None, 'false'):
            with self.subTest(value=value):
                self.assert_refused('show_phone', value)

    def test_texts_are_texts(self):
        for value in (7, ['x'], {'a': 1}, None):
            with self.subTest(value=value):
                self.assert_refused('footer_text', value)

    def test_the_model_validates_the_same_rules(self):
        from django.core.exceptions import ValidationError

        row = CompanyDocumentDesign(company=self.company, footer_text='<b>')
        with self.assertRaises(ValidationError):
            row.full_clean()


# ---------------------------------------------------------------------------
# What each option does on the internal papers
# ---------------------------------------------------------------------------

class InternalPapersTest(DesignBase):
    def test_the_thank_you_message_prints_on_every_internal_paper_only_when_set(self):
        note = self.note()
        papers = lambda: {   # noqa: E731
            'a4': text_of(generate_sales_note_pdf(note)),
            'ticket': text_of(generate_sales_note_ticket_pdf(note)),
            'receipt': text_of(generate_order_receipt_pdf(note.order)),
            'thermal': sales_note_ticket(note).decode('cp858', 'replace'),
        }
        for name, text in papers().items():
            self.assertNotIn('Gracias', text, name)

        self.design(thank_you_message=THANKS)
        for name, text in papers().items():
            self.assertIn(THANKS, text, name)

    def test_the_footer_is_on_every_page_of_a_long_note(self):
        from store import inventory_services as inventory
        from store.tests import _prod

        lines = []
        for n in range(60):
            product = _prod(self.company, f'Accesorio {n:02d}', f'acc-dd03-{n}', price='10.00', inventory=0)
            inventory.create_stock_movement(branch=self.branch_a, product_id=product.pk,
                                            movement_type='initial_stock', quantity=2, reason='Inicial',
                                            actor=self.staff)
            lines.append((product, 1))
        note = self.note_for(self.sale(lines))
        self.design(footer_text=FOOTER)

        pages = page_texts(generate_sales_note_pdf(note))
        self.assertGreaterEqual(len(pages), 2)
        for number, page in enumerate(pages, start=1):
            self.assertIn(FOOTER, page, f'page {number}')       # printed as typed: «A&B», not «A&amp;B»

    def test_contact_switches_choose_what_the_header_prints(self):
        note = self.note()
        a4, ticket = text_of(generate_sales_note_pdf(note)), text_of(generate_sales_note_ticket_pdf(note))
        for text in (a4, ticket):
            self.assertIn('054 555 000', text)
            self.assertIn('Av. Los Olivos 123', text)
            self.assertNotIn('tienda.example', text.replace('ventas@inventario.example', ''))
        self.assertIn('ventas@inventario.example', a4)
        self.assertNotIn('ventas@inventario.example', ticket)          # the roll's e-mail is its own switch

        self.design(show_phone=False, show_email=False, show_address=False, show_website=True)
        a4, ticket = text_of(generate_sales_note_pdf(note)), text_of(generate_sales_note_ticket_pdf(note))
        for name, text in (('a4', a4), ('ticket', ticket)):
            self.assertNotIn('054 555 000', text, name)
            self.assertNotIn('ventas@inventario.example', text, name)
            self.assertNotIn('Av. Los Olivos 123', text, name)
            self.assertIn('https://tienda.example', text, name)
            # Who sells is never a contact option: name, RUC and branch stay.
            for kept in ('Inventario S.A.C.', '20800000001', 'Tienda Centro'):
                self.assertIn(kept, text, f'{name}: {kept}')

    def test_a_switched_on_contact_without_data_prints_nothing_in_its_place(self):
        CompanySettings.objects.filter(company=self.company).update(website_url='')
        self.design(show_website=True)
        text = text_of(generate_sales_note_pdf(self.note()))
        self.assertNotIn('Web', text)
        self.assertNotIn('None', text)

    def test_the_logo_size_changes_the_drawn_logo_on_the_a4_and_the_ticket(self):  # noqa: D102
        self.with_logo()
        note = self.note()
        widths = {}
        for size in ('small', 'medium', 'large'):
            self.design(logo_size=size)
            widths[size] = (image_widths(generate_sales_note_pdf(note))[0],
                            image_widths(generate_sales_note_ticket_pdf(note))[0])
        for paper in (0, 1):
            self.assertLess(widths['small'][paper], widths['medium'][paper])
            self.assertLess(widths['medium'][paper], widths['large'][paper])
        # The ticket's logo never leaves the 72 mm the head reaches.
        self.assertLessEqual(widths['large'][1], 72 * 72 / 25.4)

    def test_the_zero_discount_columns_are_hidden_only_when_asked_and_only_when_empty(self):
        note = self.note()
        self.assertIn('P. LISTA', text_of(generate_sales_note_pdf(note)))

        self.design(hide_zero_columns=True)
        text = text_of(generate_sales_note_pdf(note))
        self.assertNotIn('P. LISTA', text)
        self.assertNotIn('DSCTO.', text)
        self.assertIn('P. UNIT.', text)
        self.assertIn('IMPORTE', text)

    def test_a_line_with_a_discount_keeps_its_columns_even_when_asked_to_hide_them(self):
        from store.document_layout import render_a4
        from store.test_document_robustness import _minimal_document

        document = _minimal_document()
        document['hide_zero_columns'] = True
        document['lines'][0]['discount'] = '5.00'
        self.assertIn('DSCTO.', text_of(render_a4(document)))

    def test_the_statement_of_what_the_paper_is_survives_any_design(self):
        self.design(**EVERYTHING_CHANGED)
        self.receive(self.row('F2LXK1ABC1', IMEI_A))
        note = self.note_for(self.sale([(self.phone, 1)]))

        for name, text in (('a4', text_of(generate_sales_note_pdf(note))),
                           ('ticket', text_of(generate_sales_note_ticket_pdf(note)))):
            self.assertIn(SALES_NOTE_CONTROL_LINE, text, name)
            self.assertIn('No es una serie fiscal', text, name)
            self.assertIn(f'IMEI: {IMEI_A}', text, name)
        self.assertIn('No válido como comprobante electrónico SUNAT', text_of(generate_order_receipt_pdf(note.order)))

    def test_the_texts_are_printed_as_typed_not_as_markup(self):
        self.design(footer_text='Soporte A&B', thank_you_message='Gracias & hasta pronto')
        note = self.note()
        for text in (text_of(generate_sales_note_pdf(note)), text_of(generate_sales_note_ticket_pdf(note))):
            self.assertIn('Soporte A&B', text)
            self.assertIn('Gracias & hasta pronto', text)
            self.assertNotIn('&amp;', text)


# ---------------------------------------------------------------------------
# The fiscal papers do not change
# ---------------------------------------------------------------------------

class FiscalPapersUnchangedTest(FiscalFixtures):
    def test_real_fiscal_pdfs_are_drawn_identically_with_any_design(self):
        for kind, document in (('factura', self.invoice()), ('boleta', self.receipt())):
            with self.subTest(kind):
                plain = (page_streams(generate_fiscal_pdf(document)), page_streams(generate_fiscal_ticket_pdf(document)))
                CompanyDocumentDesign.objects.update_or_create(
                    company=document.company, defaults=EVERYTHING_CHANGED)

                designed = (page_streams(generate_fiscal_pdf(document)),
                            page_streams(generate_fiscal_ticket_pdf(document)))

                self.assertEqual(designed, plain)
                CompanyDocumentDesign.objects.filter(company=document.company).delete()

    def test_the_fiscal_renderer_never_reads_the_design(self):
        root = Path(__file__).resolve().parent
        for module in ('fiscal_pdf_services.py', 'fiscal/representation.py', 'fiscal/qr.py'):
            path = root / module
            if path.exists():
                self.assertNotIn('document_design', path.read_text(encoding='utf-8'), module)

    def test_building_a_fiscal_paper_does_not_even_look_the_design_up(self):
        document = self.invoice()
        with mock.patch('store.document_design.design_for', side_effect=AssertionError('read')):
            generate_fiscal_pdf(document)
            generate_fiscal_ticket_pdf(document)


class FiscalSamplesUnchangedTest(DesignBase):
    def test_a_saved_or_drafted_design_leaves_the_fiscal_samples_as_they_were(self):
        kinds = ('boleta', 'factura', 'ticket80_boleta', 'ticket80_factura')
        plain = {kind: page_streams(self.pdf(kind)) for kind in kinds}

        self.design(**EVERYTHING_CHANGED)
        for kind in kinds:
            with self.subTest(kind=kind, source='saved'):
                self.assertEqual(page_streams(self.pdf(kind)), plain[kind])
            with self.subTest(kind=kind, source='draft'):
                response = self.preview(kind, design={**EVERYTHING_CHANGED, 'thank_you_message': 'Otro borrador'})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(page_streams(response.content), plain[kind])


# ---------------------------------------------------------------------------
# The draft preview
# ---------------------------------------------------------------------------

class DraftPreviewTest(DesignBase):
    def test_a_draft_is_drawn_on_the_sample_and_never_stored(self):
        before = (AdminAuditLog.objects.count(), CompanySettings.objects.count())
        with CaptureQueriesContext(connection) as queries:
            response = self.preview('sales_note', design={'thank_you_message': THANKS, 'footer_text': FOOTER})

        self.assertEqual(response.status_code, 200, response.content[:300])
        text = text_of(response.content)
        self.assertIn(THANKS, text)
        self.assertIn(FOOTER, text)
        self.assertGreaterEqual(text.count(document_preview.SAMPLE_TEXT), 2)        # still a sample
        self.assertFalse(CompanyDocumentDesign.objects.exists())
        self.assertEqual(before, (AdminAuditLog.objects.count(), CompanySettings.objects.count()))
        self.assertFalse([q for q in queries.captured_queries if re.match(r'\s*(INSERT|UPDATE|DELETE)', q['sql'], re.I)])

    def test_without_a_draft_the_sample_uses_the_saved_design(self):
        self.design(thank_you_message=THANKS)
        for kind in ('sales_note', 'order_receipt', 'ticket80_sales_note'):
            self.assertIn(THANKS, text_of(self.pdf(kind)), kind)

    def test_a_draft_overrides_the_saved_design_field_by_field(self):
        self.design(thank_you_message=THANKS, footer_text=FOOTER)
        text = text_of(self.preview('ticket80_sales_note', design={'thank_you_message': ''}).content)

        self.assertNotIn(THANKS, text)
        self.assertIn(FOOTER, text)
        self.assertEqual(CompanyDocumentDesign.objects.get().thank_you_message, THANKS)

    def test_an_invalid_draft_is_refused_with_the_same_rules_as_a_save(self):
        for draft in ({'thank_you_message': 'x' * 121}, {'footer_text': '<script>'}, {'logo_size': 'huge'},
                      {'show_email': 'no'}, {'thank_you_message': 'Gracias 🐾'}):
            with self.subTest(draft=draft):
                response = self.preview('sales_note', design=draft)
                self.assertEqual(response.status_code, 400)
                self.assertIn('design', response.json())

    def test_a_draft_that_is_not_an_object_is_refused(self):
        for draft in ('large', ['x'], 7):
            with self.subTest(draft=draft):
                self.assertEqual(self.preview('sales_note', design=draft).status_code, 400)

    def test_a_draft_still_needs_company_manage(self):
        self.only('company.view')
        self.assertEqual(self.preview('sales_note', design={'thank_you_message': THANKS}).status_code, 403)

    def test_a_draft_aimed_at_another_company_is_404(self):
        response = self.preview('sales_note', f'?company={self.other.pk}', design={'thank_you_message': THANKS})
        self.assertEqual(response.status_code, 404)


class SampleMarksTest(DesignBase):
    def test_the_diagonal_mark_is_lighter_and_the_band_is_drawn_in_full_ink(self):
        # Not a fiscal sample: under BETA it also carries the fiscal «SUNAT BETA» mark, which is not this one.
        for kind in ('sales_note', 'ticket80_sales_note', 'order_receipt'):
            pdf = self.pdf(kind)
            alphas = [float(a) for a in re.findall(rb'/ca ([\d.]+)', pdf)]
            self.assertTrue(alphas, kind)
            self.assertAlmostEqual(max(alphas), 0.13, places=3, msg=kind)
            # The band: spaced capitals (a character spacing, `Tc`) in the ink colour, on white.
            stream = page_streams(pdf)[0]
            self.assertRegex(stream, rb'[\d.]+ Tc', kind)
            self.assertIn(b'1 1 1 rg', stream, kind)


class DesignValueTest(DesignBase):
    def test_a_design_is_immutable_plain_data(self):
        with self.assertRaises(Exception):
            DEFAULT.thank_you_message = 'x'          # type: ignore[misc]
        self.assertEqual(DocumentDesign(**DEFAULT.as_dict()), DEFAULT)


# ---------------------------------------------------------------------------
# Review of DOC-DESIGN-03 (P2-1): the e-mail on the roll is opt-in
# ---------------------------------------------------------------------------

class RollEmailTest(DesignBase):
    EMAIL = 'ventas@inventario.example'

    def papers(self, note):
        return {
            'a4': text_of(generate_sales_note_pdf(note)),
            'ticket': text_of(generate_sales_note_ticket_pdf(note)),
            'thermal': sales_note_ticket(note).decode('cp858', 'replace'),
        }

    def test_without_a_row_the_roll_prints_no_email_as_before_and_the_a4_still_does(self):
        papers = self.papers(self.note())

        self.assertIn(self.EMAIL, papers['a4'])
        self.assertNotIn(self.EMAIL, papers['ticket'])
        self.assertNotIn(self.EMAIL, papers['thermal'])
        self.assertFalse(DEFAULT.show_email_on_roll)

    def test_the_roll_email_is_its_own_switch(self):
        note = self.note()
        self.design(show_email_on_roll=True)
        papers = self.papers(note)
        self.assertIn(self.EMAIL, papers['ticket'])
        self.assertIn(self.EMAIL, papers['thermal'])

        self.design(show_email=False, show_email_on_roll=True)
        papers = self.papers(note)
        self.assertNotIn(self.EMAIL, papers['a4'])
        self.assertIn(self.EMAIL, papers['ticket'])

        self.design(show_email=True, show_email_on_roll=False)
        papers = self.papers(note)
        self.assertIn(self.EMAIL, papers['a4'])
        self.assertNotIn(self.EMAIL, papers['ticket'])
        self.assertNotIn(self.EMAIL, papers['thermal'])

    def test_the_switch_is_in_the_payload_saved_and_audited(self):
        self.assertFalse(self.api.get(URL).json()['design']['show_email_on_roll'])
        response = self.patch(show_email_on_roll=True)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(CompanyDocumentDesign.objects.get().show_email_on_roll)
        self.assertEqual(AdminAuditLog.objects.get(action='document_design_updated').metadata['changed_fields'],
                         ['show_email_on_roll'])
        self.assertEqual(self.patch(show_email_on_roll='yes').status_code, 400)

    def test_a_draft_can_switch_it_on_the_sample_roll(self):
        self.assertNotIn(self.EMAIL, text_of(self.pdf('ticket80_sales_note')))
        response = self.preview('ticket80_sales_note', design={'show_email_on_roll': True})
        self.assertIn(self.EMAIL, text_of(response.content))


class ThermalCharsetTest(DesignBase):
    """What is saved is what the paper says: a character the design accepts never becomes «?» on the thermal print."""

    def test_every_accepted_character_survives_the_thermal_encoding(self):
        from store.document_design import normalise, text_problem
        from store.printing.escpos import Receipt

        accepted = []
        for code in range(0x20, 0x3000):
            char = normalise(chr(code))
            if char and char != '?' and not text_problem(char, 10):
                accepted.append(char)
        self.assertIn('•', accepted)
        for encoding in ('cp858', 'cp1252'):
            receipt = Receipt(encoding=encoding)
            lost = [char for char in accepted if b'?' in receipt._encode(char)]
            self.assertEqual(lost, [], encoding)

    def test_the_new_substitutes_read_as_their_meaning(self):
        from store.printing.escpos import Receipt

        receipt = Receipt()
        for char, plain in (('•', b'-'), ('‰', b'o/oo'), ('†', b'+'), ('‡', b'+'), ('Œ', b'OE'), ('œ', b'oe'),
                            ('ƒ', b'f')):
            self.assertEqual(receipt._encode(char), plain, char)


class EmailedCopyTest(DesignBase):
    """SALES-EMAIL-03 sends the note drawn by `render_sales_note_pdf`: the same paper, so the same design."""

    def test_the_emailed_copy_of_a_note_carries_the_companys_design_and_writes_nothing(self):
        from store.sales_note_services import render_sales_note_pdf

        note = self.note()
        self.assertNotIn(THANKS, text_of(render_sales_note_pdf(note)))
        self.design(thank_you_message=THANKS, footer_text=FOOTER)

        text = text_of(render_sales_note_pdf(note))
        self.assertIn(THANKS, text)
        self.assertIn(FOOTER, text)
        self.assertIn(SALES_NOTE_CONTROL_LINE, text)
        note.refresh_from_db()
        self.assertIsNone(note.pdf_generated_at)
