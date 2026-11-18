"""
DOC-PREVIEW-ESCPOS — `POST /api/admin/document-preview/thermal/`.

The thermal roll of the sales note, as the printer will print it, before saving:

  * it is READ BACK FROM THE BYTES the printer would receive (`escpos.sales_note_ticket_from_ctx`, the
    function that prints the real note) by the reader the goldens use (`printing.escpos_reader`); it is
    not a second layout. Each line keeps its alignment, bold and double size; the logo is the raster
    itself, as a 1-bit PNG;
  * the draft of the design is drawn without being saved, as in the PDF previews;
  * same rules as the PDF previews: `company.manage` in THIS company, the company resolved by the
    server, read-only, the body at most 8 KB, the same throttle budget, made-up data only.
"""
import base64
import io
import json
import re
from unittest import mock

from django.core.cache import cache
from django.db import connection
from django.test import SimpleTestCase
from django.test.utils import CaptureQueriesContext
from PIL import Image
from rest_framework.test import APIClient

from store.document_legends import SALES_NOTE_FORBIDDEN_WORDS
from store.document_preview import SAMPLE_NUMBER, SAMPLE_TEXT
from store.models import AdminAuditLog, CompanyDocumentDesign, CompanySettings, SalesNote
from store.printing import escpos_reader
from store.printing.escpos import Receipt
from store.test_document_preview import PreviewBase
from store.test_escpos_logo import png

URL = '/api/admin/document-preview/thermal/'


def texts(payload) -> list[str]:
    return [line['text'] for line in payload['lines'] if line['type'] == 'text']


class ReaderTest(SimpleTestCase):
    """The production reader: what the goldens and the preview read the bytes with."""

    def roll(self):
        return (Receipt().image(png((64, 16)), align='right').line('Tienda', align='center', bold=True)
                .line('NV-1', align='center', bold=True, large=True).row('A', 'B').rule().feed(2)
                .qr('pay|load').cut().to_bytes())

    def test_the_simulation_keeps_the_style_of_every_line(self):
        lines = escpos_reader.simulation(self.roll())

        blank = {'type': 'text', 'text': '', 'align': 'left', 'bold': False, 'large': False}
        self.assertEqual(lines[0], {'type': 'image', 'align': 'right', 'width': 64, 'height': 16,
                                    'png': lines[0]['png']})
        # The `\n` after an image or a QR feeds one blank line on the paper (Epson-compatible heads).
        self.assertEqual(lines[1], blank)
        self.assertEqual(lines[2:6], [
            {'type': 'text', 'text': 'Tienda', 'align': 'center', 'bold': True, 'large': False},
            {'type': 'text', 'text': 'NV-1', 'align': 'center', 'bold': True, 'large': True},
            {'type': 'text', 'text': 'A' + ' ' * 46 + 'B', 'align': 'left', 'bold': False, 'large': False},
            {'type': 'text', 'text': '-' * 48, 'align': 'left', 'bold': False, 'large': False},
        ])
        self.assertEqual(lines[6:], [
            {'type': 'feed', 'lines': 2},
            {'type': 'qr', 'align': 'center', 'payload': 'pay|load'},
            blank,
            {'type': 'feed', 'lines': 3},
            {'type': 'cut'},
        ])

    def test_the_image_is_the_raster_itself_as_a_one_bit_png(self):
        data = Receipt().image(png((64, 16), margin=4)).to_bytes()
        block = escpos_reader.simulation(data)[0]

        image = Image.open(io.BytesIO(base64.b64decode(block['png'])))
        self.assertEqual((image.mode, image.size), ('1', (64, 16)))
        at = data.index(b'\x1dv0\x00') + 8
        self.assertEqual(image.tobytes('raw', '1;I'), data[at:at + 8 * 16])        # dot for dot

    def test_the_golden_form_is_the_same_reader(self):
        from store.test_document_goldens import escpos_lines

        self.assertIs(escpos_lines, escpos_reader.lines)
        self.assertEqual(escpos_reader.lines(self.roll())[:3], ['<CP 19>', '<R><IMAGEN 64x16>', '<C><B>Tienda'])

    def test_the_golden_form_has_no_line_for_the_feed_after_an_image(self):
        lines = escpos_reader.lines(Receipt().image(png((64, 16))).line('X').to_bytes())
        self.assertEqual(lines, ['<CP 19>', '<C><IMAGEN 64x16>', 'X'])

    def test_a_raster_mode_other_than_normal_is_an_error(self):
        data = Receipt().image(png((64, 16))).to_bytes()
        at = data.index(b'\x1dv0\x00')
        for mode in (1, 2, 3, 48):
            with self.subTest(mode=mode), self.assertRaises(escpos_reader.EscposReadError):
                escpos_reader.read(data[:at + 3] + bytes([mode]) + data[at + 4:])

    def test_only_the_partial_cut_after_a_feed_is_known(self):
        self.assertIsInstance(escpos_reader.read(b'\x1dV\x42\x00')[0], escpos_reader.Cut)
        for form in (b'\x1dV\x41\x00', b'\x1dV\x00\x00', b'\x1dV\x01\x00'):
            with self.subTest(form=form), self.assertRaises(escpos_reader.EscposReadError):
                escpos_reader.read(form)

    def test_text_is_decoded_with_the_code_page_the_roll_selects(self):
        def first_text(data):
            return [item for item in escpos_reader.read(data) if isinstance(item, escpos_reader.Line)][0].pieces[0]

        self.assertEqual(first_text(b'\x1bt\x13\xd5\n'), '€')                    # 19: cp858
        self.assertEqual(first_text(b'\x1bt\x02\xd5\n'), 'ı')                    # 2: cp850
        self.assertEqual(first_text(b'\x1bt\x00\xd5\n'), '╒')                    # 0: cp437
        self.assertEqual(first_text(b'\xd5\n'), '€')                               # none chosen: the argument's
        with self.assertRaises(escpos_reader.EscposReadError):
            escpos_reader.read(b'\x1bt\x07\xd5\n')                                # a page Receipt never selects
        self.assertEqual(escpos_reader.text(Receipt(encoding='cp850').line('Año').to_bytes()), ['Año'])

    def test_esc_at_resets_alignment_bold_and_size(self):
        line = escpos_reader.read(b'\x1ba\x01\x1bE\x01\x1d!\x11\x1b@X\n')[0]
        self.assertEqual((line.pieces, line.align, line.bold, line.large), (('X',), 'left', False, False))

    def test_an_unknown_or_cut_short_command_is_an_error_not_text(self):
        for data in (b'\x1bp\x00\x19\xfa', b'\x1b', b'\x1dv0\x00\x08\x00\x10\x00\xff'):
            with self.subTest(data=data), self.assertRaises(escpos_reader.EscposReadError):
                escpos_reader.read(data)


class ThermalPreviewBase(PreviewBase):
    def thermal(self, query='', **body):
        return self.api.post(URL + query, {'document': 'sales_note', **body}, format='json')

    def payload(self, **body):
        response = self.thermal(**body)
        self.assertEqual(response.status_code, 200, response.content[:300])
        return response.json()


class ThermalPreviewTest(ThermalPreviewBase):
    def test_it_answers_the_roll_as_json_never_cached(self):
        response = self.thermal()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/json')
        self.assertEqual(response['Cache-Control'], 'no-store')
        body = response.json()
        self.assertEqual((body['document'], body['paper_width_mm'], body['columns'], body['dots'], body['dots_per_mm']),
                         ('sales_note', 80, 48, 576, 8))
        self.assertEqual(body['sample'], SAMPLE_TEXT)

    def test_it_is_what_the_printer_receives_for_the_sample(self):
        from store import document_preview
        from store.document_design import design_for
        from store.printing import escpos

        company = type(self.company).objects.get(pk=self.company.pk)       # as the view resolves it
        ctx = document_preview._sales_note_ctx(company, document_preview._lines(0), design_for(company))
        printed = escpos_reader.text(escpos.sales_note_ticket_from_ctx(ctx))

        lines = texts(self.payload())
        self.assertEqual(lines, printed)
        self.assertIn(SAMPLE_NUMBER, lines)
        self.assertIn('NOTA DE VENTA', lines)
        self.assertTrue(all(len(line) <= 48 for line in lines))

    def test_the_number_is_large_and_bold_and_the_title_bold_and_centred(self):
        lines = {line['text']: line for line in self.payload()['lines'] if line['type'] == 'text'}

        self.assertEqual({k: lines[SAMPLE_NUMBER][k] for k in ('align', 'bold', 'large')},
                         {'align': 'center', 'bold': True, 'large': True})
        self.assertEqual({k: lines['NOTA DE VENTA'][k] for k in ('align', 'bold', 'large')},
                         {'align': 'center', 'bold': True, 'large': False})

    def test_the_sales_note_names_no_receipt(self):
        text = ' '.join(texts(self.payload())).lower()
        for word in SALES_NOTE_FORBIDDEN_WORDS:
            self.assertNotIn(word.lower(), text)

    def test_the_real_note_and_its_context_are_never_read(self):
        with mock.patch('store.sales_note_services.build_sales_note_context', side_effect=AssertionError('real')), \
             mock.patch('store.printing.escpos.sales_note_ticket', side_effect=AssertionError('real')):
            self.payload()


class ThermalDraftTest(ThermalPreviewBase):
    def test_the_unsaved_draft_is_drawn_and_not_saved(self):
        before = CompanyDocumentDesign.objects.count()

        lines = texts(self.payload(design={'footer_text': 'Pie de borrador', 'document_title': 'Orden de salida',
                                           'show_order': False}))

        self.assertIn('Pie de borrador', lines)
        self.assertIn('ORDEN DE SALIDA', lines)
        self.assertFalse(any(line.startswith('Pedido:') for line in lines))
        self.assertEqual(CompanyDocumentDesign.objects.count(), before)
        self.assertNotIn('Pie de borrador', texts(self.payload()))

    def test_a_wrong_draft_is_400_under_design(self):
        response = self.thermal(design={'logo_ticket_align': 'justify'})

        self.assertEqual(response.status_code, 400)
        self.assertIn('logo_ticket_align', response.json()['design'])

    def test_the_logo_is_this_companys_with_the_drafts_size_and_alignment(self):
        self.assertEqual([line for line in self.payload()['lines'] if line['type'] == 'image'], [])
        self.with_logo(self.other)
        self.assertEqual([line for line in self.payload()['lines'] if line['type'] == 'image'], [])

        self.with_logo(self.company)                                         # 240 × 80 px
        image = self.payload(design={'logo_ticket_width_mm': 50, 'logo_ticket_align': 'left'})['lines'][0]
        self.assertEqual((image['type'], image['align'], image['width'], image['height']), ('image', 'left', 400, 133))
        image = self.payload()['lines'][0]
        self.assertEqual((image['align'], image['width'], image['height']), ('center', 336, 112))


class ThermalRulesTest(ThermalPreviewBase):
    def test_anonymous_is_401(self):
        self.assertEqual(APIClient().post(URL, {'document': 'sales_note'}, format='json').status_code, 401)

    def test_only_company_manage_opens_it(self):
        for capabilities, status in ((('company.view',), 403), (('sales.notes.manage', 'inventory.view'), 403),
                                     ((), 403), (('company.manage',), 200)):
            with self.subTest(capabilities):
                self.only(*capabilities)
                self.assertEqual(self.thermal().status_code, status)

    def test_only_post_is_allowed(self):
        self.assertEqual(self.api.get(URL).status_code, 405)

    def test_another_company_answers_like_one_that_does_not_exist(self):
        found = self.thermal(f'?company={self.other.pk}')
        missing = self.thermal('?company=99999999')

        self.assertEqual((found.status_code, missing.status_code), (404, 404))
        self.assertEqual(found.json(), missing.json())

    def test_only_the_sales_note_has_a_thermal_preview(self):
        for body in ({'document': 'boleta'}, {'document': ''}, {}, {'document': 7}, {'document': None}):
            with self.subTest(body=body):
                self.assertEqual(self.api.post(URL, body, format='json').status_code, 400)

    def test_a_body_over_eight_kilobytes_is_refused(self):
        big = json.dumps({'document': 'sales_note', 'padding': 'x' * 9000})
        self.assertEqual(self.api.post(URL, big, content_type='application/json').status_code, 413)

    def test_it_writes_nothing(self):
        self.payload()                                                       # warm caches read once
        before = (SalesNote.objects.count(), AdminAuditLog.objects.count(), CompanySettings.objects.count())
        with CaptureQueriesContext(connection) as queries:
            self.payload(design={'footer_text': 'Pie'})
        writes = [q['sql'][:90] for q in queries.captured_queries
                  if re.match(r'\s*(INSERT|UPDATE|DELETE)\b', q['sql'], re.I)]
        self.assertEqual(writes, [])
        self.assertEqual(before, (SalesNote.objects.count(), AdminAuditLog.objects.count(),
                                  CompanySettings.objects.count()))

    def test_it_shares_the_budget_of_the_pdf_previews(self):
        from store.throttles import DocumentPreviewThrottle

        with mock.patch.dict(DocumentPreviewThrottle.THROTTLE_RATES, {'document_preview': '2/min'}):
            cache.clear()
            codes = [self.preview('sales_note').status_code, self.thermal().status_code, self.thermal().status_code]
        cache.clear()
        self.assertEqual(codes, [200, 200, 429])
