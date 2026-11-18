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
bytes decoded as the printer's code page (cp858), every ESC/GS command removed
(a QR or a logotype leaves one `<QR>` / `<IMAGEN w×h>` line in its place), one
entry per printed line. Same switch to regenerate it.
"""
import json
import os
import re
from datetime import datetime, timezone as dt_timezone
from pathlib import Path

from django.test import SimpleTestCase

from store import inventory_services as inventory
from store.document_layout import render_a4
from store.fiscal_pdf_services import (
    build_fiscal_context, generate_fiscal_pdf, generate_fiscal_ticket_pdf, render_fiscal_a4, render_fiscal_ticket,
)
from store.models import CompanySettings, Order
from store.pdf_services import build_order_pdf_context, generate_order_receipt_pdf, order_receipt_document
from store.sales_note_services import build_sales_note_context, generate_sales_note_pdf, sales_note_document
from store.test_document_design import DocumentBase, page_texts
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


def escpos_lines(data: bytes, encoding: str = 'cp858') -> list[str]:
    """
    What a thermal roll SAYS, line by line: `data` without its ESC/GS commands.

    Only the commands `printing.escpos.Receipt` emits are known; anything else
    fails here, so a new command is a decision of this function and never
    silently becomes text. A QR leaves `<QR>` and a raster image
    `<IMAGEN width×height>` (in dots) on their own line.
    """
    out, i, size = bytearray(), 0, len(data)
    while i < size:
        byte = data[i]
        command = data[i + 1:i + 2]
        if byte == 0x1B:                                           # ESC
            if command == b'@':
                i += 2
            elif command in (b't', b'a', b'E', b'd'):
                i += 3
            else:
                raise AssertionError(f'unknown ESC command {command!r} at byte {i}')
        elif byte == 0x1D:                                         # GS
            if command == b'!':
                i += 3
            elif command == b'V':
                i += 4
            elif command == b'(' and data[i + 2:i + 3] == b'k':      # QR: GS ( k pL pH cn fn …
                length = data[i + 3] | data[i + 4] << 8
                if data[i + 6] == 0x50:                            # store the payload
                    out += b'<QR>'
                i += 5 + length
            elif command == b'v' and data[i + 2:i + 3] == b'0':      # raster: GS v 0 m xL xH yL yH …
                width_bytes = data[i + 4] | data[i + 5] << 8
                height = data[i + 6] | data[i + 7] << 8
                out += f'<IMAGEN {width_bytes * 8}x{height}>'.encode()
                i += 8 + width_bytes * height
            else:
                raise AssertionError(f'unknown GS command {command!r} at byte {i}')
        else:
            out.append(byte)
            i += 1
    lines = out.decode(encoding).split('\n')
    while lines and lines[-1] == '':
        lines.pop()
    return lines


def check_escpos_golden(test, name: str, data: bytes, *, normalise=lambda text: text) -> None:
    """Compare the text of the ESC/POS `data` with the recorded golden `name` (or record it)."""
    got = [normalise(line) for line in escpos_lines(data)]
    goldens = (json.loads(ESCPOS_GOLDEN_FILE.read_text(encoding='utf-8'))
               if ESCPOS_GOLDEN_FILE.exists() else {})
    if UPDATE:
        goldens[name] = got
        ESCPOS_GOLDEN_FILE.write_text(
            json.dumps(goldens, ensure_ascii=False, indent=1, sort_keys=True) + '\n', encoding='utf-8')
        return
    test.assertIn(name, goldens, f'no ESC/POS golden recorded for {name!r}; record with DOCUMENT_GOLDENS_UPDATE=1')
    test.assertEqual(got, goldens[name], f'{name}: ESC/POS text')


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
    def test_commands_go_and_text_qr_and_image_stay_as_lines(self):
        from store.printing.escpos import Receipt
        from store.test_fiscal_logo import logo_png

        roll = Receipt()
        roll.image(logo_png()).line('Número', bold=True, large=True).row('A', 'B').qr('payload').rule()
        lines = escpos_lines(roll.cut().to_bytes())

        self.assertTrue(lines[0].startswith('<IMAGEN '))
        self.assertEqual(lines[1:3], ['Número', 'A' + ' ' * 46 + 'B'])
        self.assertEqual(lines[3:], ['<QR>', '-' * 48])

    def test_an_unknown_command_fails_instead_of_becoming_text(self):
        with self.assertRaises(AssertionError):
            escpos_lines(b'\x1bp\x00\x19\xfa')                    # ESC p: open the drawer


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
    def test_invoice_escpos(self):
        from store.printing import escpos

        document = self.invoice()
        check_escpos_golden(self, 'fiscal_factura_escpos', escpos.fiscal_ticket(document),
                            normalise=self.normaliser(document))

    def test_receipt_escpos(self):
        from store.printing import escpos

        document = self.receipt()
        check_escpos_golden(self, 'fiscal_boleta_escpos', escpos.fiscal_ticket(document),
                            normalise=self.normaliser(document))


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
