"""
DOC-LEGEND-01 — the sales note carries NO legend; its structure keeps it apart.

Owner decision (2026-11-18, definitive; docs/nota-de-venta-identificacion.md):
the heading is the commercial title and the NV- number, and no paper of the
note — A4, 80 mm PDF, ESC/POS, e-mail copy — says what it is not, names a
receipt or a SUNAT state. In place of a legend:

  * none of the words of a receipt appear on any of the four outputs, not even
    when the sale has an electronic receipt of its own;
  * the numbering never starts with F or B;
  * no QR and no digest;
  * a company title that names a receipt is never printed.

The fiscal papers keep their mandatory legends, from the same module.
"""
import re
from unittest import mock

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from store import document_legends as legends
from store.models import FiscalDocument, FiscalDocumentStatus, FiscalEnvironment, InternalSequence, SalesNote
from store.printing import escpos
from store.sales_note_services import (
    build_sales_note_context, generate_sales_note_pdf, get_or_create_sales_note,
)
from store.sequences import ensure_company_sequence
from store.test_document_goldens import FiscalFixtures, PinnedSales, escpos_lines, escpos_text
from store.test_document_design import page_texts
from store.test_fiscal_logo import embedded_images
from store.ticket_services import generate_sales_note_ticket_pdf

#: What none of the old or proposed legends may leave behind.
OLD_LEGENDS = ('no es comprobante de pago', 'se entrega aparte', 'no es una serie', 'número interno',
               'documento interno', 'no es un comprobante')


def flat(text: str) -> str:
    # `page_texts` reads PDF strings as latin-1: the em dash of WinAnsi comes out as \x97.
    return re.sub(r'\s+', ' ', text.replace('\x97', '—')).strip()


def papers(note) -> dict:
    return {
        'A4': flat(' '.join(page_texts(generate_sales_note_pdf(note)))),
        '80 mm': flat(' '.join(page_texts(generate_sales_note_ticket_pdf(note)))),
        'ESC/POS': flat(' '.join(escpos_text(escpos.sales_note_ticket(note)))),
    }


def email(order) -> dict:
    """The copy of the note by e-mail, as it would be sent: subject, text and HTML."""
    from store.order_document_copy import KIND_SALES_NOTE, _copy_bodies, _document_title, describe

    entry = describe(order, KIND_SALES_NOTE, may_sales_note=True, may_fiscal=True)
    subject, text, html = _copy_bodies(order, title=_document_title(entry['label']), notice=entry['note'])
    return {'e-mail subject': subject, 'e-mail text': text, 'e-mail html': html}


class NoteTitleTest(SimpleTestCase):
    def test_the_title_is_the_designs_or_the_default(self):
        self.assertEqual(legends.note_title(None), 'Nota de venta')
        self.assertEqual(legends.note_title(mock.Mock(document_title='  ')), 'Nota de venta')
        self.assertEqual(legends.note_title(mock.Mock(document_title='Orden de venta')), 'Orden de venta')

    def test_a_title_that_names_a_receipt_is_never_printed(self):
        for title in ('Factura', 'BOLETA de venta', 'Comprobante de pago', 'Ticket fiscal',
                      'Recibo por honorarios', 'Liquidacion de compra', 'Nota de crédito', 'nota de DÉBITO'):
            with self.subTest(title=title):
                self.assertTrue(legends.is_fiscal_denomination(title))
                self.assertEqual(legends.note_title(mock.Mock(document_title=title)), 'Nota de venta')
        self.assertFalse(legends.is_fiscal_denomination('Nota de venta'))


class SalesNoteCarriesNoLegendTest(PinnedSales):
    def renderings(self, order, note):
        from store import document_preview

        return {
            **papers(note),
            **email(order),
            'preview A4': flat(' '.join(page_texts(document_preview.render_preview(
                'sales_note', company=self.company)))),
            'preview 80 mm': flat(' '.join(page_texts(document_preview.render_preview(
                'ticket80_sales_note', company=self.company)))),
        }

    def test_the_heading_is_the_title_then_the_number_then_the_data(self):
        _order, note = self.device_sale()
        follows = {'A4': 'CLIENTE', '80 mm': 'Fecha:', 'ESC/POS': 'Fecha:'}
        for paper, text in papers(note).items():
            with self.subTest(paper=paper):
                self.assertIn(f'NOTA DE VENTA {note.number} {follows[paper]}', text)

    def test_no_fiscal_word_and_no_legend_on_any_rendering(self):
        order, note = self.device_sale()
        for name, text in self.renderings(order, note).items():
            lowered = text.lower()
            for word in (*legends.SALES_NOTE_FORBIDDEN_WORDS, *OLD_LEGENDS):
                with self.subTest(rendering=name, word=word):
                    self.assertNotIn(word, lowered)

    def test_a_void_note_has_no_fiscal_word_either(self):
        _order, note = self.device_sale()
        SalesNote.objects.filter(pk=note.pk).update(status=SalesNote.STATUS_VOID)
        note.refresh_from_db()
        for name, text in papers(note).items():
            self.assertIn('ANULADA', text)
            for word in legends.SALES_NOTE_FORBIDDEN_WORDS:
                with self.subTest(paper=name, word=word):
                    self.assertNotIn(word, text.lower())

    def test_no_qr_no_digest_no_image_but_the_logo(self):
        _order, note = self.device_sale()
        raw = escpos.sales_note_ticket(note)
        self.assertNotIn(b'\x1d(k', raw)                               # the printer's QR command
        self.assertFalse([line for line in escpos_lines(raw) if '<QR' in line or '<IMAGEN' in line])
        for paper, pdf in (('A4', generate_sales_note_pdf(note)), ('80 mm', generate_sales_note_ticket_pdf(note))):
            with self.subTest(paper=paper):
                self.assertEqual(embedded_images(pdf), 0)              # no logo configured: no image at all
                text = flat(' '.join(page_texts(pdf))).lower()
                for word in ('valor resumen', 'hash', 'digest'):
                    self.assertNotIn(word, text)

    def test_the_context_carries_no_legend(self):
        _order, note = self.device_sale()
        ctx = build_sales_note_context(note)
        self.assertEqual(ctx['title'], 'Nota de venta')
        for key in ('legal_line', 'control_line', 'number_note'):
            self.assertNotIn(key, ctx)


class SalesNoteNextToItsReceiptTest(FiscalFixtures):
    """A sale with its own boleta or factura: the note still names none, in any state."""

    def test_the_note_never_mentions_the_receipt_of_its_sale(self):
        for receipt, status in (('boleta', FiscalDocumentStatus.SIGNED),
                                ('factura', FiscalDocumentStatus.ACCEPTED),
                                ('boleta', FiscalDocumentStatus.REJECTED)):
            document = self.receipt() if receipt == 'boleta' else self.invoice()
            FiscalDocument.objects.filter(pk=document.pk).update(
                status=status, environment=FiscalEnvironment.PRODUCTION)
            note = get_or_create_sales_note(document.order)[0]
            renderings = {**papers(note), **email(document.order)}
            for name, text in renderings.items():
                with self.subTest(receipt=receipt, status=status, rendering=name):
                    self.assertNotIn(document.document_id, text)
                    for word in legends.SALES_NOTE_FORBIDDEN_WORDS:
                        self.assertNotIn(word, text.lower())


class SalesNoteNumberingTest(PinnedSales):
    """The NV- series never starts like a SUNAT electronic series (F factura, B boleta)."""

    def test_the_default_prefix_is_nv(self):
        _order, note = self.device_sale()
        self.assertTrue(note.number.startswith('NV-'))
        self.assertEqual(legends.sales_note_prefix_error('NV-'), '')

    def test_a_prefix_starting_with_f_or_b_is_refused(self):
        sequence = ensure_company_sequence(self.company)
        for prefix in ('F001-', 'B001-', 'f-', 'b', 'Boleta'):
            with self.subTest(prefix=prefix):
                sequence.prefix = prefix
                with self.assertRaises(ValidationError) as caught:
                    sequence.save()
                self.assertIn('prefix', caught.exception.message_dict)
                sequence.refresh_from_db()
                self.assertEqual(sequence.prefix, 'NV-')

    def test_through_the_api_too(self):
        from rest_framework.test import APIClient

        from store.tests import _p2d_member

        sequence = ensure_company_sequence(self.company)
        manager, _membership = _p2d_member(self.company, 'nv_prefix_manager', ['company.view', 'company.manage'])
        client = APIClient()
        client.force_authenticate(user=manager)
        response = client.patch(f'/api/admin/sequences/{sequence.pk}/', {'prefix': 'B01-'}, format='json')
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn('prefix', response.json())
        sequence.refresh_from_db()
        self.assertEqual(sequence.prefix, 'NV-')
        response = client.patch(f'/api/admin/sequences/{sequence.pk}/', {'prefix': 'NOTA-'}, format='json')
        self.assertEqual(response.status_code, 200, response.content)

    def test_other_series_keep_their_letters(self):
        repair = InternalSequence(company=self.company, document_type=InternalSequence.DOCUMENT_REPAIR_ORDER,
                                  prefix='B-', padding=6, next_value=1)
        repair.save()                                                  # only the sales note is restricted
        self.assertEqual(repair.prefix, 'B-')

    def test_a_new_branch_series_inherits_a_legacy_prefix_without_failing_the_sale(self):
        """
        Branch scope: the first sale of a branch creates its series from the
        company template. A legacy F/B prefix stored before the rule is
        inherited, not chosen — the sale must not fail (it would be a 500 and
        the whole POS sale rolled back).
        """
        from store.models import CompanySettings

        sequence = ensure_company_sequence(self.company)
        InternalSequence.objects.filter(pk=sequence.pk).update(prefix='BDS-')   # stored before this rule
        CompanySettings.objects.filter(company=self.company).update(
            sales_note_sequence_scope=CompanySettings.SEQUENCE_SCOPE_BRANCH)

        _order, note = self.device_sale()

        self.assertTrue(note.number.startswith('BDS-'), note.number)
        self.assertEqual(note.sequence.branch_id, self.branch_a.pk)

    def test_a_series_already_printing_with_an_old_prefix_keeps_numbering(self):
        sequence = ensure_company_sequence(self.company)
        InternalSequence.objects.filter(pk=sequence.pk).update(prefix='B-')   # stored before this rule
        _order, note = self.device_sale()
        self.assertTrue(note.number.startswith('B-'))


class FiscalLegendsUntouchedTest(FiscalFixtures):
    """The mandatory fiscal legends come from the module, with their exact words."""

    def test_beta_receipt_papers_carry_their_legends(self):
        from store.fiscal_pdf_services import generate_fiscal_pdf, generate_fiscal_ticket_pdf

        document = self.receipt()
        a4 = flat(' '.join(page_texts(generate_fiscal_pdf(document))))
        ticket = flat(' '.join(page_texts(generate_fiscal_ticket_pdf(document))))
        thermal = escpos_lines(escpos.fiscal_ticket(document))
        # The BETA lines keep their emphasis: centred and bold.

        self.assertIn(legends.FISCAL_BETA_WARNING, a4)
        for line in legends.FISCAL_BETA_DIAGONAL:
            self.assertIn(line, a4)
        for line in legends.FISCAL_BETA_ROLL_LINES:
            self.assertIn(line, ticket)
            self.assertIn('<C><B>' + line.replace('—', '-'), thermal)
        self.assertIn(legends.fiscal_representation_legend('Boleta de venta electrónica'), a4)

    def test_a_rejected_receipt_keeps_its_status_warning(self):
        from store.fiscal_pdf_services import generate_fiscal_pdf

        document = self.receipt()
        FiscalDocument.objects.filter(pk=document.pk).update(status=FiscalDocumentStatus.REJECTED)
        document.refresh_from_db()
        self.assertIn(legends.FISCAL_REJECTED_STATUS, flat(' '.join(page_texts(generate_fiscal_pdf(document)))))
