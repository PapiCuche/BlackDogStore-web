"""
DOC-UX-02 (PR-E) — `POST /api/admin/document-preview/`.

A sample of each paper, drawn by the SAME functions as the real ones from a
fixed synthetic context. What is pinned here:

  * every page of every sample says «MUESTRA · NO FISCAL», however many pages it
    has, and the number, the PDF title and the file name begin with «MUESTRA»;
  * nothing is written — not a row, not `pdf_generated_at`, not a settings row
    — and the real generators that do write are never called;
  * a fiscal sample never carries the company's tax id or legal name, its QR is
    the text «MUESTRA – NO ESCANEAR» and not a SUNAT payload, and «SUNAT BETA»
    appears only under the BETA environment;
  * who may ask: `company.manage` in THIS company; another company's logo and
    another company's id never reach the sample;
  * the body is a whitelist of one key, small, and unknown keys are ignored.
"""
import json
import re
from unittest import mock

from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext, override_settings
from rest_framework.test import APIClient

from store import document_preview
from store.document_preview import KINDS, SAMPLE_NUMBER, SAMPLE_TEXT
from store.models import AdminAuditLog, CompanySettings, FiscalDocument, MembershipRoleAssignment, SalesNote
from store.test_document_design import DocumentBase, media_box, page_texts, text_of
from store.test_fiscal_logo import embedded_images
from store.tests import _assign, _role

URL = '/api/admin/document-preview/'
FISCAL_KINDS = ('boleta', 'factura', 'ticket80_boleta', 'ticket80_factura')
INTERNAL_KINDS = ('sales_note', 'order_receipt', 'ticket80_sales_note')
A4 = (595.3, 841.9)
TICKET_WIDTH = 226.8


def squashed(text: str) -> str:
    return text.replace(' ', '')


class PreviewBase(DocumentBase):
    def setUp(self):
        super().setUp()
        self.api = APIClient()
        self.api.force_authenticate(user=self.staff)
        self.only('company.manage')

    def only(self, *capabilities):
        """The member holds exactly these capabilities."""
        MembershipRoleAssignment.objects.filter(membership=self.membership).update(is_active=False)
        self.roles = getattr(self, 'roles', 0) + 1
        name = f"Rol {self.roles} " + ('+'.join(sorted(capabilities)) if capabilities else 'vacío')
        _assign(self.membership, _role(self.company, name, capabilities=list(capabilities), slug=None))

    def preview(self, kind, query='', **body):
        return self.api.post(URL + query, {'document': kind, **body}, format='json')

    def pdf(self, kind):
        response = self.preview(kind)
        self.assertEqual(response.status_code, 200, response.content[:300])
        return response.content


class MarkingTest(PreviewBase):
    def test_every_kind_answers_an_inline_pdf_that_is_never_cached_and_is_named_muestra(self):
        for kind in KINDS:
            with self.subTest(kind=kind):
                response = self.preview(kind)

                self.assertEqual(response.status_code, 200)
                self.assertEqual(response['Content-Type'], 'application/pdf')
                self.assertEqual(response['Cache-Control'], 'no-store')
                self.assertTrue(response['Content-Disposition'].startswith(f'inline; filename="MUESTRA-'))
                self.assertTrue(response.content.startswith(b'%PDF'))
                self.assertRegex(response.content, rb'/Title \(MUESTRA-000000')

    def test_every_page_of_every_kind_says_muestra_no_fiscal_twice_and_carries_the_fake_number(self):
        for kind in KINDS:
            with self.subTest(kind=kind):
                pdf = self.pdf(kind)

                pages = page_texts(pdf)
                self.assertTrue(pages)
                for number, page in enumerate(pages, start=1):
                    # The framed band AND the diagonal watermark; a roll has its band at
                    # the top AND at the bottom.
                    wanted = 3 if kind.startswith('ticket80_') else 2
                    self.assertGreaterEqual(page.count(SAMPLE_TEXT), wanted, f'{kind} page {number}')
                self.assertIn(SAMPLE_NUMBER, pages[0])

    def test_a_sample_of_several_pages_is_marked_on_every_page(self):
        company = self.company
        for kind in ('sales_note', 'order_receipt', 'boleta', 'factura'):
            with self.subTest(kind=kind):
                pdf = document_preview.render_preview(kind, company=company, extra_lines=90)

                pages = page_texts(pdf)
                self.assertGreaterEqual(len(pages), 3, kind)
                for number, page in enumerate(pages, start=1):
                    self.assertGreaterEqual(page.count(SAMPLE_TEXT), 2, f'{kind} page {number} of {len(pages)}')

    def test_a_long_roll_is_marked_at_the_top_the_bottom_and_along_the_way(self):
        for kind in ('ticket80_sales_note', 'ticket80_factura'):
            with self.subTest(kind=kind):
                pdf = document_preview.render_preview(kind, company=self.company, extra_lines=90)

                width, height = media_box(pdf)
                self.assertGreater(height, 3 * width)
                # Top band + bottom band + one diagonal group per two widths of roll.
                self.assertGreaterEqual(text_of(pdf).count(SAMPLE_TEXT), 4)

    def test_the_marks_are_drawn_by_the_renderer_not_by_the_document(self):
        from store.document_layout import render_a4
        from store.document_preview import SAMPLE_MARK
        from store.test_document_robustness import _minimal_document

        bare = render_a4(_minimal_document())
        marked = render_a4(_minimal_document(), watermark=SAMPLE_MARK)

        self.assertNotIn(SAMPLE_TEXT, text_of(bare))
        self.assertGreaterEqual(text_of(marked).count(SAMPLE_TEXT), 2)

    def test_page_sizes_are_those_of_the_real_papers(self):
        for kind in ('sales_note', 'order_receipt', 'boleta', 'factura'):
            self.assertEqual(media_box(self.pdf(kind)), A4, kind)
        for kind in ('ticket80_sales_note', 'ticket80_boleta', 'ticket80_factura'):
            self.assertAlmostEqual(media_box(self.pdf(kind))[0], TICKET_WIDTH, places=1)


class FictitiousDataTest(PreviewBase):
    def test_the_lines_are_made_up_and_break_a_layout(self):
        text = squashed(text_of(self.pdf('sales_note')))

        for expected in ('MUESTRA-SN-0001', 'IMEI:000000000000001', 'IMEI2:000000000000002',
                         'Funda de muestra', 'ConUnaPalabraSinEspaciosMuyLargaParaProbarElCorteDeLinea1234567890',
                         'Descuento', '13.00', 'NOTA DE VENTA', 'CLIENTE DE MUESTRA'):
            self.assertIn(squashed(expected), text, expected)

    def test_text_with_markup_characters_renders_as_typed_on_every_kind(self):
        for kind in KINDS:
            with self.subTest(kind=kind):
                self.assertIn('CargadorA&B<65W>demuestra', squashed(text_of(self.pdf(kind))))

    def test_no_real_customer_or_order_reaches_a_sample(self):
        order = self.sale([(self.case, 1)])
        self.note_for(order)

        for kind in KINDS:
            text = text_of(self.pdf(kind))
            for real in ('María Fernanda', '40404040', '987 654 321', 'maria@example.com', f'#{order.pk}'):
                self.assertNotIn(real, text, f'{kind}: {real}')

    def test_the_internal_papers_use_the_companys_own_header_and_say_they_are_not_fiscal(self):
        for kind in INTERNAL_KINDS:
            text = text_of(self.pdf(kind))
            self.assertIn('Inventario S.A.C.', text, kind)
            self.assertIn('20800000001', text, kind)
        self.assertIn('No es una serie fiscal', text_of(self.pdf('sales_note')))


class FiscalSampleTest(PreviewBase):
    def test_a_fiscal_sample_never_carries_the_companys_tax_id_or_legal_name(self):
        for kind in FISCAL_KINDS:
            with self.subTest(kind=kind):
                text = text_of(self.pdf(kind))

                self.assertIn('RUC 20000000000', text)
                self.assertIn('EMPRESA DE MUESTRA S.A.C.', text)
                for real in ('20800000001', 'Inventario S.A.C.', 'Av. Los Olivos', 'ventas@inventario.example'):
                    self.assertNotIn(real, text, real)

    def test_the_company_name_is_not_even_in_the_pdf_metadata_of_a_fiscal_sample(self):
        for kind in FISCAL_KINDS:
            pdf = self.pdf(kind)
            self.assertNotIn(b'20800000001', pdf)
            self.assertNotIn(b'Inventario', pdf)

    def test_the_qr_holds_the_words_muestra_no_escanear_and_is_not_a_sunat_payload(self):
        from store.fiscal.qr import render_qr_png

        for kind in FISCAL_KINDS:
            with self.subTest(kind=kind), mock.patch(
                    'store.fiscal.qr.render_qr_png', wraps=render_qr_png) as qr:
                self.pdf(kind)

                payload = qr.call_args.args[0]
                self.assertEqual(payload, 'MUESTRA – NO ESCANEAR')
                # The standard payload is ten fields separated by «|»: RUC, type,
                # series, number, IGV, total, date, customer type, customer id, hash.
                self.assertEqual(payload.count('|'), 0)
                self.assertLess(len(payload.split('|')), 10)
                self.assertFalse(re.match(r'^\d{11}\|', payload))

    def test_the_digest_is_the_word_muestra_and_the_status_never_claims_acceptance(self):
        text = text_of(self.pdf('factura'))

        self.assertIn('Valor resumen: MUESTRA', text)
        self.assertNotIn('Aceptada por SUNAT', text)
        self.assertIn('no emitido, sin validez', text)

    def test_sunat_beta_appears_only_when_the_server_operates_against_beta(self):
        for kind in FISCAL_KINDS:
            self.assertIn('SUNAT BETA', text_of(self.pdf(kind)), kind)
        with override_settings(FISCAL_ENVIRONMENT='production'):
            for kind in FISCAL_KINDS:
                self.assertNotIn('SUNAT BETA', text_of(self.pdf(kind)), kind)
                self.assertIn(SAMPLE_TEXT, text_of(self.pdf(kind)), kind)
        for kind in INTERNAL_KINDS:
            self.assertNotIn('SUNAT BETA', text_of(self.pdf(kind)), kind)

    def test_whole_quantities_print_without_decimals_on_the_sample_too(self):
        text = text_of(self.pdf('ticket80_factura'))

        self.assertIn('2 NIU x', text)
        self.assertNotIn('2.00 NIU', text)


class ReadOnlyTest(PreviewBase):
    def test_a_sample_issues_no_insert_update_or_delete(self):
        self.pdf('sales_note')                       # warm caches that are read once

        for kind in KINDS:
            with self.subTest(kind=kind):
                with CaptureQueriesContext(connection) as queries:
                    self.pdf(kind)

                writes = [q['sql'][:90] for q in queries.captured_queries
                          if re.match(r'\s*(INSERT|UPDATE|DELETE)\b', q['sql'], re.I)]
                self.assertEqual(writes, [])

    def test_a_sample_creates_no_rows_and_leaves_a_real_note_untouched(self):
        note = self.note_for(self.sale([(self.case, 1)]))
        before = (SalesNote.objects.count(), FiscalDocument.objects.count(), AdminAuditLog.objects.count(),
                  CompanySettings.objects.count())

        for kind in KINDS:
            self.pdf(kind)

        note.refresh_from_db()
        self.assertIsNone(note.pdf_generated_at)
        self.assertEqual(before, (SalesNote.objects.count(), FiscalDocument.objects.count(),
                                  AdminAuditLog.objects.count(), CompanySettings.objects.count()))

    def test_the_real_generators_are_never_called(self):
        with mock.patch('store.sales_note_services.generate_sales_note_pdf', side_effect=AssertionError('real')), \
             mock.patch('store.ticket_services.generate_sales_note_ticket_pdf', side_effect=AssertionError('real')), \
             mock.patch('store.pdf_services.generate_order_receipt_pdf', side_effect=AssertionError('real')), \
             mock.patch('store.fiscal_pdf_services.build_fiscal_context', side_effect=AssertionError('real')), \
             mock.patch('store.sales_note_services.build_sales_note_context', side_effect=AssertionError('real')), \
             mock.patch('store.pdf_services.build_order_pdf_context', side_effect=AssertionError('real')):
            for kind in KINDS:
                self.pdf(kind)

    def test_a_company_without_a_settings_row_still_previews_and_no_row_is_created(self):
        CompanySettings.objects.filter(company=self.company).delete()

        for kind in KINDS:
            self.pdf(kind)

        self.assertFalse(CompanySettings.objects.filter(company=self.company).exists())


class PermissionsTest(PreviewBase):
    def test_anonymous_is_401(self):
        self.assertEqual(APIClient().post(URL, {'document': 'sales_note'}, format='json').status_code, 401)

    def test_company_view_alone_is_403(self):
        self.only('company.view')
        self.assertEqual(self.preview('sales_note').status_code, 403)

    def test_sales_and_inventory_capabilities_alone_are_403(self):
        self.only('sales.notes.manage', 'inventory.view')
        self.assertEqual(self.preview('sales_note').status_code, 403)

    def test_a_member_with_no_capability_is_403(self):
        self.only()
        self.assertEqual(self.preview('sales_note').status_code, 403)

    def test_company_manage_opens_it(self):
        self.only('company.manage')
        self.assertEqual(self.preview('sales_note').status_code, 200)

    def test_only_post_is_allowed(self):
        self.assertEqual(self.api.get(URL).status_code, 405)

    def test_another_company_answers_like_one_that_does_not_exist(self):
        found = self.preview('sales_note', f'?company={self.other.pk}')
        missing = self.preview('sales_note', '?company=99999999')

        self.assertEqual(found.status_code, 404)
        self.assertEqual(found.status_code, missing.status_code)
        self.assertEqual(found.json(), missing.json())

    def test_a_non_numeric_company_is_400(self):
        self.assertEqual(self.preview('sales_note', '?company=abc').status_code, 400)


class LogoTest(PreviewBase):
    def test_the_sample_carries_this_companys_logo_and_never_another_ones(self):
        plain = {kind: embedded_images(self.pdf(kind)) for kind in KINDS}
        self.with_logo(self.other)                          # the OTHER company uploads a logo

        for kind in KINDS:
            self.assertEqual(embedded_images(self.pdf(kind)), plain[kind], f'{kind}: a foreign logo leaked')

        self.with_logo(self.company)
        for kind in KINDS:
            self.assertEqual(embedded_images(self.pdf(kind)), plain[kind] + 1, f'{kind}: the own logo is missing')


class BodyTest(PreviewBase):
    def test_an_unknown_missing_or_mistyped_document_is_400(self):
        for body in ({'document': 'invoice'}, {'document': ''}, {}, {'document': 7}, {'document': ['boleta']},
                     {'document': None}, {'document': 'ticket80_nota_de_credito'}):
            with self.subTest(body=body):
                self.assertEqual(self.api.post(URL, body, format='json').status_code, 400)

    def test_unknown_keys_are_ignored_and_change_nothing(self):
        plain = self.pdf('sales_note')

        response = self.preview(
            'sales_note', company=self.other.pk, design={'logo': 'x'}, order=1, tax_id='20999999999',
            number='REAL-1', watermark=False, lines=[{'name': 'x'}])

        self.assertEqual(response.status_code, 200)
        self.assertEqual(page_texts(response.content), page_texts(plain))

    def test_a_body_over_two_kilobytes_is_refused_before_it_is_read(self):
        big = json.dumps({'document': 'sales_note', 'padding': 'x' * 3000})

        response = self.api.post(URL, big, content_type='application/json')

        self.assertEqual(response.status_code, 413)

    def test_the_route_is_throttled_with_its_own_budget(self):
        from store.throttles import DocumentPreviewThrottle

        with mock.patch.dict(DocumentPreviewThrottle.THROTTLE_RATES, {'document_preview': '2/min'}):
            cache.clear()
            codes = [self.preview('sales_note').status_code for _n in range(3)]
        cache.clear()
        self.assertEqual(codes, [200, 200, 429])

    def test_the_configured_budget_is_a_real_one(self):
        from django.conf import settings

        self.assertEqual(settings.REST_FRAMEWORK['DEFAULT_THROTTLE_RATES']['document_preview'], '30/min')
