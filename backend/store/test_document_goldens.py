"""
DOC-UX-02 (PR-B) — GOLDENS of the papers, recorded BEFORE the render functions
were extracted so that the extraction can be proven to change nothing.

What a golden is here: for one document built from fixed data, the text of each
page, the page count and the MediaBox of each page. Together they fix what the
paper SAYS and how much room it takes (an 80 mm roll's height is a fingerprint
of its whole layout: one line wrapped differently and the roll is taller).

What they are not: byte comparisons. A PDF carries its creation date inside, so
two identical papers never are.

THE FIXTURES ARE PINNED. Order ids, note numbers, the instants a document
prints and the digest value of a signed invoice would otherwise change on every
run; each is fixed or normalised below, and ONLY those.

REGENERATING. A change of wording or layout that is meant (a new line on the
note) is recorded with

    DOCUMENT_GOLDENS_UPDATE=1 python3 manage.py test store.test_document_goldens

and the diff of `store/goldens/documents.json` is the review of that change.
A refactor must never need it.

THE THERMAL ROLL TOO (DOC-SAFETY-01). The ESC/POS bytes of the sales note and
of the fiscal ticket are recorded as text in `store/goldens/escpos.json`: the
bytes decoded as the printer's code page (cp858), one entry per printed line,
each ESC/GS command turned into a marker (`escpos_lines`: code page, alignment,
bold, double size, the QR with its payload, images). Same switch to regenerate it.
The reader is `store.printing.escpos_reader` (DOC-PREVIEW-ESCPOS).
"""
import json
import os
import re
from datetime import datetime, timezone as dt_timezone
from pathlib import Path

from django.test import SimpleTestCase, override_settings

from store import inventory_services as inventory
from store.document_layout import render_a4
from store.fiscal_pdf_services import (
    build_fiscal_context, generate_fiscal_pdf, generate_fiscal_ticket_pdf, render_fiscal_a4, render_fiscal_ticket,
)
from store.models import CompanySettings, Order
from store.pdf_services import build_order_pdf_context, generate_order_receipt_pdf, order_receipt_document
from store.printing import escpos_reader
from store.sales_note_services import build_sales_note_context, generate_sales_note_pdf, sales_note_document
from store.test_document_design import DocumentBase, page_texts
from store.test_fiscal_logo import _ROOT as LOGO_ROOT
from store.test_fiscal_print import FiscalPrintBase
from store.test_stock_units import IMEI_A, IMEI_B
from store.tests import _prod
from store.ticket_services import generate_sales_note_ticket_pdf, ticket_pdf_from_ctx

GOLDEN_FILE = Path(__file__).resolve().parent / 'goldens' / 'documents.json'
ESCPOS_GOLDEN_FILE = Path(__file__).resolve().parent / 'goldens' / 'escpos.json'
UPDATE = bool(os.environ.get('DOCUMENT_GOLDENS_UPDATE'))

#: 09/03/2026 21:30 in Lima. Every instant a document prints is this one.
FIXED_INSTANT = datetime(2026, 3, 10, 2, 30, tzinfo=dt_timezone.utc)


def media_boxes(pdf: bytes) -> list[list[float]]:
    return [[round(float(w), 2), round(float(h), 2)]
            for w, h in re.findall(rb'/MediaBox \[ ?0 0 ([\d.]+) ([\d.]+) ?\]', pdf)]


def snapshot(pdf: bytes, *, normalise=lambda text: text) -> dict:
    texts = [normalise(text) for text in page_texts(pdf)]
    return {'pages': len(texts), 'media_boxes': media_boxes(pdf), 'texts': texts}


def _load() -> dict:
    return json.loads(GOLDEN_FILE.read_text(encoding='utf-8')) if GOLDEN_FILE.exists() else {}


def check_golden(test, name: str, pdf: bytes, *, normalise=lambda text: text) -> None:
    """Compare `pdf` with the recorded golden `name` (or record it when asked to)."""
    got = snapshot(pdf, normalise=normalise)
    goldens = _load()
    if UPDATE:
        goldens[name] = got
        GOLDEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN_FILE.write_text(
            json.dumps(goldens, ensure_ascii=False, indent=1, sort_keys=True) + '\n', encoding='utf-8')
        return
    test.assertIn(name, goldens, f'no golden recorded for {name!r}; record with DOCUMENT_GOLDENS_UPDATE=1')
    want = goldens[name]
    test.assertEqual(got['pages'], want['pages'], f'{name}: page count')
    test.assertEqual(got['media_boxes'], want['media_boxes'], f'{name}: MediaBox')
    for number, (have, expected) in enumerate(zip(got['texts'], want['texts']), start=1):
        test.assertEqual(have, expected, f'{name}: text of page {number}')


#: DOC-PREVIEW-ESCPOS: the reader moved to production (`printing.escpos_reader`), where the thermal preview of
#: Ajustes › Documentos uses it too. The goldens read the bytes exactly as before.
escpos_lines = escpos_reader.lines
escpos_text = escpos_reader.text


def check_escpos_golden(test, name: str, data: bytes, *, normalise=lambda text: text) -> None:
    """Compare the text of the ESC/POS `data` with the recorded golden `name` (or record it)."""
    got = [normalise(line) for line in escpos_lines(data)]
    goldens = (json.loads(ESCPOS_GOLDEN_FILE.read_text(encoding='utf-8'))
               if ESCPOS_GOLDEN_FILE.exists() else {})
    if UPDATE:
        goldens[name] = got
        ESCPOS_GOLDEN_FILE.write_text(
            json.dumps(goldens, ensure_ascii=False, indent=1, sort_keys=True) + '\n', encoding='utf-8')
        return got
    test.assertIn(name, goldens, f'no ESC/POS golden recorded for {name!r}; record with DOCUMENT_GOLDENS_UPDATE=1')
    test.assertEqual(got, goldens[name], f'{name}: ESC/POS text')
    return got


class PinnedSales(DocumentBase):
    """A sale whose order id, note number and instants are fixed, so what it prints is the same every run."""

    def pinned_sale(self, lines, **fields):
        order = self.sale(lines, id=7001, **fields)
        Order.objects.filter(pk=order.pk).update(created_at=FIXED_INSTANT, paid_at=FIXED_INSTANT)
        order.refresh_from_db()
        note = self.note_for(order)
        type(note).objects.filter(pk=note.pk).update(issued_at=FIXED_INSTANT)
        note.refresh_from_db()
        return order, note

    def device_sale(self):
        self.receive(self.row('F2LXK1ABC1', IMEI_A, imei2=IMEI_B))
        return self.pinned_sale([(self.phone, 1), (self.case, 2)], discount_amount='59.80')


class SalesNoteGoldenTest(PinnedSales):
    def test_sales_note_a4(self):
        _order, note = self.device_sale()
        check_golden(self, 'sales_note_a4', generate_sales_note_pdf(note))

    def test_sales_note_a4_with_many_lines_breaks_pages(self):
        lines = []
        for n in range(70):
            product = _prod(self.company, f'Accesorio de prueba número {n:02d}', f'acc-gold-{n}',
                            price='10.00', inventory=0)
            inventory.create_stock_movement(
                branch=self.branch_a, product_id=product.pk, movement_type='initial_stock',
                quantity=5, reason='Inicial', actor=self.staff)
            lines.append((product, 1))
        _order, note = self.pinned_sale(lines)
        check_golden(self, 'sales_note_a4_many_lines', generate_sales_note_pdf(note))

    def test_order_receipt_a4(self):
        order, _note = self.device_sale()
        check_golden(self, 'order_receipt_a4', generate_order_receipt_pdf(order))

    def test_sales_note_ticket80(self):
        _order, note = self.device_sale()
        check_golden(self, 'sales_note_ticket80', generate_sales_note_ticket_pdf(note))

    def test_sales_note_ticket80_with_long_texts(self):
        name = 'MacBookProM4Max16Pulgadas1TBNegroEspacialConCargadorMagSafe140W' * 2
        product = _prod(self.company, name, 'largo-gold', price='10.00', inventory=0)
        inventory.create_stock_movement(
            branch=self.branch_a, product_id=product.pk, movement_type='initial_stock',
            quantity=5, reason='Inicial', actor=self.staff)
        CompanySettings.objects.filter(company=self.company).update(
            warranty_policy_text='Garantía de doce meses. ' * 6)
        _order, note = self.pinned_sale([(product, 1)], notes='Entregar en recepción. ' * 12)
        check_golden(self, 'sales_note_ticket80_long', generate_sales_note_ticket_pdf(note))


class EscposSalesNoteGoldenTest(PinnedSales):
    def test_sales_note_escpos(self):
        from store.printing import escpos

        _order, note = self.device_sale()
        check_escpos_golden(self, 'sales_note_escpos', escpos.sales_note_ticket(note))

    def test_sales_note_escpos_with_long_texts(self):
        from store.printing import escpos

        name = 'MacBookProM4Max16Pulgadas1TBNegroEspacialConCargadorMagSafe140W' * 2
        product = _prod(self.company, name, 'largo-gold', price='10.00', inventory=0)
        inventory.create_stock_movement(
            branch=self.branch_a, product_id=product.pk, movement_type='initial_stock',
            quantity=5, reason='Inicial', actor=self.staff)
        CompanySettings.objects.filter(company=self.company).update(
            warranty_policy_text='Garantía de doce meses. ' * 6)
        _order, note = self.pinned_sale([(product, 1)], notes='Entregar en recepción. ' * 12)
        check_escpos_golden(self, 'sales_note_escpos_long', escpos.sales_note_ticket(note))


class EscposNormaliserTest(SimpleTestCase):
    def test_commands_become_markers_and_text_qr_and_image_stay_as_lines(self):
        from store.printing.escpos import Receipt
        from store.test_fiscal_logo import logo_png

        roll = Receipt()
        roll.image(logo_png()).line('Número', bold=True, large=True).row('A', 'B').qr('pay|load').rule()
        lines = escpos_lines(roll.cut().to_bytes())

        self.assertEqual(lines[0], '<CP 19>')
        self.assertRegex(lines[1], r'^<C><IMAGEN \d+x\d+>$')
        self.assertEqual(lines[2:4], ['<B><2X>Número', 'A' + ' ' * 46 + 'B'])
        self.assertEqual(lines[4:], ['<C><QR pay|load>', '-' * 48])

    def test_a_line_that_loses_its_bold_is_a_different_line(self):
        from store.printing.escpos import Receipt

        bold = escpos_lines(Receipt().line('SIN VALIDEZ', align='center', bold=True).to_bytes())
        plain = escpos_lines(Receipt().line('SIN VALIDEZ', align='center').to_bytes())
        self.assertEqual(bold[1:], ['<C><B>SIN VALIDEZ'])
        self.assertNotEqual(bold, plain)

    def test_an_unknown_command_fails_instead_of_becoming_text(self):
        with self.assertRaises(escpos_reader.EscposReadError):
            escpos_lines(b'\x1bp\x00\x19\xfa')                    # ESC p: open the drawer

    def test_a_qr_stored_and_never_printed_fails(self):
        from store.printing.escpos import Receipt

        data = Receipt().qr('payload').to_bytes()
        printed = b'\x1d(k\x03\x00\x31\x51\x30'
        self.assertIn(printed, data)
        with self.assertRaises(escpos_reader.EscposReadError):
            escpos_lines(data.replace(printed, b''))
        with self.assertRaises(escpos_reader.EscposReadError):      # printed with nothing stored
            escpos_lines(b'\x1d(k\x03\x00\x31\x51\x30\n')


class FiscalFixtures(FiscalPrintBase):
    @staticmethod
    def normaliser(document):
        """The instant of the XML and its digest are the only things that change from run to run."""
        digest = document.digest_value

        def normalise(text: str) -> str:
            text = text.replace(digest, '<DIGEST>')
            return re.sub(r'\d{2}/\d{2}/\d{4} \d{2}:\d{2}', '<FECHA>', text)
        return normalise

    def invoice(self):
        return self.issue(
            receipt='factura', document_type='ruc', document_number='20000000001',
            name='CLIENTE DE PRUEBA SAC', quantity=3)

    def receipt(self):
        return self.issue()


class FiscalGoldenTest(FiscalFixtures):
    def test_invoice_a4(self):
        document = self.invoice()
        check_golden(self, 'fiscal_factura_a4', generate_fiscal_pdf(document),
                     normalise=self.normaliser(document))

    def test_receipt_a4(self):
        document = self.receipt()
        check_golden(self, 'fiscal_boleta_a4', generate_fiscal_pdf(document),
                     normalise=self.normaliser(document))

    def test_invoice_ticket80(self):
        document = self.invoice()
        check_golden(self, 'fiscal_factura_ticket80', generate_fiscal_ticket_pdf(document),
                     normalise=self.normaliser(document))

    def test_receipt_ticket80(self):
        document = self.receipt()
        check_golden(self, 'fiscal_boleta_ticket80', generate_fiscal_ticket_pdf(document),
                     normalise=self.normaliser(document))


class EscposFiscalGoldenTest(FiscalFixtures):
    def roll_normaliser(self, document):
        """The PDF normaliser, plus the ISO issue date the QR payload carries."""
        normalise = self.normaliser(document)
        return lambda line: re.sub(r'\d{4}-\d{2}-\d{2}', '<FECHA>', normalise(line))

    def test_invoice_escpos(self):
        from store.printing import escpos

        document = self.invoice()
        check_escpos_golden(self, 'fiscal_factura_escpos', escpos.fiscal_ticket(document),
                            normalise=self.roll_normaliser(document))

    def test_receipt_escpos(self):
        from store.printing import escpos

        document = self.receipt()
        check_escpos_golden(self, 'fiscal_boleta_escpos', escpos.fiscal_ticket(document),
                            normalise=self.roll_normaliser(document))


class EscposLogoGoldenTest(PinnedSales):
    """
    DOC-ESCPOS-LOGO: the thermal sales note with the company's logo, in a box and alignment of its design. The
    golden fixes the image's place and size in dots (`<IMAGEN w×h>`); its bits are fixed by test_escpos_logo.
    """

    def test_sales_note_escpos_with_logo(self):
        from store.models import CompanyDocumentDesign
        from store.printing import escpos

        self.with_logo()                                                     # 240 × 80 px
        CompanyDocumentDesign.objects.create(company=self.company, logo_ticket_width_mm=50, logo_ticket_align='left')
        _order, note = self.device_sale()
        check_escpos_golden(self, 'sales_note_escpos_logo', escpos.sales_note_ticket(note))


@override_settings(EVIDENCE_STORAGE_BACKEND='filesystem', EVIDENCE_STORAGE_ROOT=LOGO_ROOT)
class EscposFiscalLogoGoldenTest(FiscalFixtures):
    """
    DOC-ESCPOS-LOGO: a square logo on the fiscal roll, in the medium box of the design (42 × 16 mm, FISCAL-ESCPOS-DESIGN;
    never taller than 24 mm). Its text and QR are the plain golden's.
    """

    def test_receipt_escpos_with_a_square_logo(self):
        from store import storefront_media
        from store.printing import escpos
        from store.test_escpos_logo import png

        image = storefront_media.store(company=self.company, actor=None, raw=png((500, 500)), audit=False)
        CompanySettings.objects.filter(company=self.company).update(
            document_logo_url=storefront_media.payload(image)['url'])
        document = self.receipt()
        lines = check_escpos_golden(self, 'fiscal_boleta_escpos_logo', escpos.fiscal_ticket(document),
                                    normalise=EscposFiscalGoldenTest.roll_normaliser(self, document))
        if not UPDATE:
            plain = json.loads(ESCPOS_GOLDEN_FILE.read_text(encoding='utf-8'))['fiscal_boleta_escpos']
            self.assertEqual(lines[1], '<C><IMAGEN 128x128>')
            self.assertEqual(lines[:1] + lines[2:], plain)


class PureRenderingTest(PinnedSales):
    """
    The render functions take a plain `ctx` and touch nothing else: no query, no
    write, no clock. That is what lets a preview use them with made-up data.
    """

    def test_a_sales_note_and_its_ticket_render_from_the_ctx_with_zero_queries(self):
        _order, note = self.device_sale()
        ctx = build_sales_note_context(note)
        stamped = type(note).objects.get(pk=note.pk).pdf_generated_at

        with self.assertNumQueries(0):
            a4 = render_a4(sales_note_document(ctx))
            roll = ticket_pdf_from_ctx(ctx)

        self.assertEqual(page_texts(a4), page_texts(generate_sales_note_pdf(note)))
        self.assertEqual(page_texts(roll), page_texts(generate_sales_note_ticket_pdf(note)))
        self.assertIsNone(stamped)                                # drawing from a ctx stamps nothing
        note.refresh_from_db()
        self.assertIsNotNone(note.pdf_generated_at)               # the real generators still do

    def test_an_order_receipt_renders_from_the_ctx_with_zero_queries(self):
        order, _note = self.device_sale()
        ctx = build_order_pdf_context(order)

        with self.assertNumQueries(0):
            pdf = render_a4(order_receipt_document(ctx))

        self.assertEqual(page_texts(pdf), page_texts(generate_order_receipt_pdf(order)))


class PureFiscalRenderingTest(FiscalFixtures):
    def test_the_ctx_carries_the_tax_rate_and_both_fiscal_papers_render_from_it_with_zero_queries(self):
        document = self.invoice()
        ctx = build_fiscal_context(document)
        self.assertEqual(ctx['tax_rate'], document.tax_rate)

        with self.assertNumQueries(0):
            a4 = render_fiscal_a4(ctx)
            roll = render_fiscal_ticket(ctx)

        self.assertEqual(page_texts(a4), page_texts(generate_fiscal_pdf(document)))
        self.assertEqual(page_texts(roll), page_texts(generate_fiscal_ticket_pdf(document)))
        self.assertIn('IGV (18%)', ' '.join(page_texts(a4)))


class SampleContextsMirrorTheRealOnesTest(PinnedSales):
    """
    The preview builds its contexts by hand. A key added to a REAL context and read by a render function would
    answer 500 on the preview only; this fails first, in CI, instead.
    """

    def test_the_sales_note_and_receipt_samples_carry_every_key_of_the_real_contexts(self):
        from store import document_preview as preview

        order, note = self.device_sale()
        lines = preview._lines(0)
        self.assertLessEqual(set(build_sales_note_context(note)), set(preview._sales_note_ctx(order.company, lines)))
        self.assertLessEqual(set(build_order_pdf_context(order)), set(preview._receipt_ctx(order.company, lines)))


class FiscalSampleContextMirrorsTheRealOneTest(FiscalFixtures):
    def test_the_fiscal_samples_carry_every_key_of_the_real_context(self):
        from store import document_preview as preview

        for kind, document in (('factura', self.invoice()), ('boleta', self.receipt())):
            with self.subTest(kind):
                self.assertLessEqual(set(build_fiscal_context(document)),
                                     set(preview._fiscal_ctx(document.company, kind, preview._lines(0))))


class DesignedPaperGoldenTest(PinnedSales):
    """
    DOC-DESIGN-03 — the same pinned sale, drawn with a company design that changes
    every option (small logo box, no phone/e-mail/address, the web, a footer line,
    a closing message, hidden zero-discount columns). Made-up texts: never a real
    shop's sentence.
    """

    DESIGN = {
        'logo_size': 'small', 'show_phone': False, 'show_email': False, 'show_address': False,
        'show_website': True, 'footer_text': 'Pie de página de prueba · soporte A&B',
        'thank_you_message': 'Mensaje final de prueba.', 'hide_zero_columns': True,
    }

    def setUp(self):
        super().setUp()
        from store.models import CompanyDocumentDesign

        CompanySettings.objects.filter(company=self.company).update(website_url='https://tienda.example')
        CompanyDocumentDesign.objects.create(company=self.company, **self.DESIGN)

    def test_designed_sales_note_a4(self):
        _order, note = self.device_sale()
        check_golden(self, 'designed_sales_note_a4', generate_sales_note_pdf(note))

    def test_designed_sales_note_ticket80(self):
        _order, note = self.device_sale()
        check_golden(self, 'designed_sales_note_ticket80', generate_sales_note_ticket_pdf(note))

    def test_designed_order_receipt_a4(self):
        order, _note = self.device_sale()
        check_golden(self, 'designed_order_receipt_a4', generate_order_receipt_pdf(order))
