"""
FISCAL-ESCPOS-DESIGN — the boleta and the factura on the thermal printer get the
design the 80 mm PDF got in FISCAL-DESIGN-01, AND NOTHING FISCAL MOVES.

What changes on the roll (`printing.escpos.fiscal_ticket`):

  * the logo frozen with the document prints in the box and with the alignment
    the company's design gives the 80 mm ticket (`ticket_logo_box_mm`, 8 dots per
    millimetre), as on the PDF ticket and the thermal sales note: a small logo
    grows to its box and never past it, never taller than 24 mm;
  * the hierarchy: the issuer's RUC and the items' header in bold;
  * the thank-you message and the footer, screened at print as on the PDF
    (`fiscal_pdf_services._messages`), AFTER the mandatory block and separated
    from it by a rule.

What is asserted not to move: the QR (payload, module size, presence), the
digest, the series and number, issuer and buyer, the amounts, the legend
«Representación impresa de la …», the status, the BETA lines, the legal date.
The frozen pre-design goldens (`goldens/fiscal_escpos_pre_design.json`) prove
that every word printed before is still printed, the same number of times. A
rule (a line of dashes) and the logo's `<IMAGEN w×h>` marker are not words.

Every line fits the 48 columns of the roll (24 when double width), whatever the
names, the amounts or the number of items: a line the printer wraps by itself
breaks the columns of the ticket.
"""
import json
import re
from collections import Counter
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from django.forms.models import model_to_dict
from django.test import SimpleTestCase, override_settings

from store import document_legends as legends
from store.document_design import DEFAULT
from store.fiscal_pdf_services import build_fiscal_context, render_fiscal_ticket
from store.models import CompanyDocumentDesign, CompanySettings, FiscalDocument, FiscalDocumentStatus, FiscalEnvironment
from store.printing import escpos, escpos_reader
from store.printing.escpos import Receipt, fiscal_ticket, fiscal_ticket_from_ctx
from store.test_document_goldens import ESCPOS_GOLDEN_FILE, UPDATE, check_escpos_golden
from store.test_escpos_logo import png, raster
from store.test_fiscal_design import (
    EVERYTHING, FOOTER, LONG, LONG_MESSAGES, MESSAGES, MM, NOT_FOR_FISCAL, THANKS, FiscalDesignBase, drawn_images,
)
from store.test_fiscal_logo import _ROOT as LOGO_ROOT

PRE_DESIGN_FILE = Path(__file__).resolve().parent / 'goldens' / 'fiscal_escpos_pre_design.json'

_STYLE = re.compile(r'^(?:<C>|<R>|<B>|<2X>)+')
_IMAGE = re.compile(r'^<IMAGEN \d+x\d+>$')
_RULE = re.compile(r'^[-=]+$')


def words(lines) -> Counter:
    """The words of a roll in the golden form: no code page, style markers, rules or image markers."""
    counted = Counter()
    for line in lines:
        if line.startswith('<CP '):
            continue
        text = _STYLE.sub('', line)
        if _IMAGE.match(text) or _RULE.match(text):
            continue
        counted.update(text.split())
    return counted


def as_printed(text: str) -> str:
    """What the code page of the roll writes for `text` (`·` is `-`, and so on)."""
    return '\n'.join(escpos_reader.text(Receipt().line(text).to_bytes()))


def message_words() -> Counter:
    return Counter(as_printed(THANKS.replace('\n', ' ')).split()) + Counter(as_printed(FOOTER).split())


def printed_lines(data: bytes) -> list:
    """Every `Line` of the roll with its text (images and QR left out)."""
    out = []
    for item in escpos_reader.read(data):
        if isinstance(item, escpos_reader.Line):
            text = ''.join(piece for piece in item.pieces if isinstance(piece, str))
            out.append((text, item))
    return out


class FiscalEscposBase(FiscalDesignBase):
    def roll(self, ctx) -> bytes:
        return fiscal_ticket_from_ctx(ctx)

    def text(self, data: bytes) -> list[str]:
        return escpos_reader.text(data)


# ---------------------------------------------------------------------------
# The goldens: the same words, the same QR
# ---------------------------------------------------------------------------

class FiscalEscposGoldenWordsTest(SimpleTestCase):
    def setUp(self):
        self.before = json.loads(PRE_DESIGN_FILE.read_text(encoding='utf-8'))
        self.now = json.loads(ESCPOS_GOLDEN_FILE.read_text(encoding='utf-8'))

    def test_the_pre_design_goldens_are_frozen(self):
        self.assertEqual(sorted(name for name in self.before if not name.startswith('_')),
                         ['fiscal_boleta_escpos', 'fiscal_boleta_escpos_logo', 'fiscal_factura_escpos'])
        self.assertIn('never regenerated', self.before['_about'])

    def test_every_pre_design_word_is_still_printed_the_same_number_of_times(self):
        for name in ('fiscal_boleta_escpos', 'fiscal_factura_escpos', 'fiscal_boleta_escpos_logo'):
            with self.subTest(name):
                self.assertEqual(words(self.now[name]), words(self.before[name]))

    def test_the_plain_rolls_say_the_same_lines_in_the_same_order(self):
        """Without a design only the emphasis moves (and the logo's box): every printed line, word for word."""
        def said(lines):
            return [_STYLE.sub('', line) for line in lines if not _IMAGE.match(_STYLE.sub('', line))]

        for name in ('fiscal_boleta_escpos', 'fiscal_factura_escpos', 'fiscal_boleta_escpos_logo'):
            with self.subTest(name):
                self.assertEqual(said(self.now[name]), said(self.before[name]))

    def test_the_qr_line_is_the_same(self):
        for name in ('fiscal_boleta_escpos', 'fiscal_factura_escpos', 'fiscal_boleta_escpos_logo'):
            with self.subTest(name):
                qr_before = [line for line in self.before[name] if '<QR ' in line]
                qr_now = [line for line in self.now[name] if '<QR ' in line]
                self.assertEqual(len(qr_before), 1)
                self.assertEqual(qr_now, qr_before)

    def test_the_designed_goldens_add_only_the_messages(self):
        for name, base in (('designed_fiscal_boleta_escpos', 'fiscal_boleta_escpos'),
                           ('designed_fiscal_factura_escpos', 'fiscal_factura_escpos')):
            with self.subTest(name):
                added = words(self.now[name]) - words(self.before[base])
                lost = words(self.before[base]) - words(self.now[name])
                self.assertEqual(added, message_words())
                self.assertEqual(lost, Counter())
                self.assertEqual([line for line in self.now[name] if '<QR ' in line],
                                 [line for line in self.before[base] if '<QR ' in line])

    def test_the_beta_lines_keep_their_emphasis_and_the_number_its_size(self):
        for name in ('fiscal_boleta_escpos', 'fiscal_factura_escpos', 'designed_fiscal_boleta_escpos'):
            with self.subTest(name):
                lines = self.now[name]
                for beta in legends.FISCAL_BETA_ROLL_LINES:
                    self.assertIn('<C><B>' + beta.replace('—', '-'), lines)
                self.assertTrue(any(re.fullmatch(r'<C><B><2X>[BF]001-1', line) for line in lines))


@override_settings(EVIDENCE_STORAGE_BACKEND='filesystem', EVIDENCE_STORAGE_ROOT=LOGO_ROOT)
class DesignedFiscalEscposGoldenTest(FiscalEscposBase):
    """The pinned boleta (with a frozen wide logo, 50 mm, left) and factura (no logo), with the messages."""

    def roll_normaliser(self, document):
        """As `test_document_goldens.EscposFiscalGoldenTest`: the PDF normaliser and the QR's ISO date."""
        normalise = self.normaliser(document)
        return lambda line: re.sub(r'\d{4}-\d{2}-\d{2}', '<FECHA>', normalise(line))

    def test_designed_receipt_escpos(self):
        from store import storefront_media

        image = storefront_media.store(company=self.company, actor=None, raw=png((240, 80)), audit=False)
        CompanySettings.objects.filter(company=self.company).update(
            document_logo_url=storefront_media.payload(image)['url'])
        self.save_design(self.company, **MESSAGES, logo_ticket_width_mm=50, logo_ticket_align='left')
        document = self.receipt()
        lines = check_escpos_golden(self, 'designed_fiscal_boleta_escpos', fiscal_ticket(document),
                                    normalise=self.roll_normaliser(document))
        if not UPDATE:
            self.assertEqual(lines[1], '<IMAGEN 400x133>')                 # 50 × 16.7 mm, on the left

    def test_designed_invoice_escpos(self):
        self.save_design(self.company, **MESSAGES, logo_ticket_width_mm=50)
        document = self.invoice()
        check_escpos_golden(self, 'designed_fiscal_factura_escpos', fiscal_ticket(document),
                            normalise=self.roll_normaliser(document))


# ---------------------------------------------------------------------------
# The layout
# ---------------------------------------------------------------------------

class FiscalEscposLayoutTest(FiscalEscposBase):
    def test_the_hierarchy_of_the_roll(self):
        document = self.invoice()
        ctx = build_fiscal_context(document)
        lines = escpos_reader.lines(fiscal_ticket(document))
        self.assertIn(f"<C><B>{ctx['issuer']['legal_name']}", lines)
        self.assertIn(f"<C><B>RUC {ctx['issuer']['tax_id']}", lines)
        self.assertIn(f"<C><B>{ctx['title']}", lines)
        self.assertIn(f"<C><B><2X>{ctx['identifier']}", lines)
        self.assertTrue(any(line.startswith('<B>Cant. Und. x P. unit.') and line.endswith('V. venta')
                            for line in lines), lines)
        self.assertTrue(any(line.startswith('<B><2X>TOTAL ') and line.endswith(f"S/ {ctx['total']:.2f}")
                            for line in lines), lines)

    def test_the_roll_feeds_before_the_cut(self):
        items = escpos_reader.read(fiscal_ticket(self.receipt()))
        self.assertIsInstance(items[-1], escpos_reader.Cut)
        self.assertIsInstance(items[-2], escpos_reader.Feed)
        self.assertGreaterEqual(items[-2].lines, 3)

    def big_ctx(self, count=35):
        ctx = self.ctx(self.receipt(), **LONG_MESSAGES)
        big = Decimal('9999999.99')
        line = dict(ctx['lines'][0], unit_price=big, unit_price_with_tax=big, amount=big, quantity=Decimal('999.50'))
        ctx['lines'] = [dict(line, description=LONG)] + [
            dict(line, description=f'Artículo de prueba {n:03d}') for n in range(count)]
        ctx.update(taxable_amount=big, tax_amount=big, total=big)
        ctx['issuer'] = dict(ctx['issuer'], legal_name='EMPRESA DE PRUEBA CON UNA RAZÓN SOCIAL MUY LARGA ' * 2,
                             trade_name='Nombre comercial de prueba ' * 3,
                             address='Avenida de prueba 1234, urbanización de prueba, distrito ' * 2)
        ctx['customer'] = dict(ctx['customer'], legal_name='Cliente de prueba con nombres y apellidos muy largos ' * 2)
        return ctx

    def test_every_line_fits_the_roll_with_long_names_big_amounts_and_many_items(self):
        ctx = self.big_ctx()
        ctx['logo_png'] = png((240, 80))
        data = self.roll(ctx)
        for text, line in printed_lines(data):
            with self.subTest(text=text):
                self.assertLessEqual(len(text), 24 if line.large else 48)

    def test_nothing_is_cut_or_lost(self):
        ctx = self.big_ctx()
        lines = self.text(self.roll(ctx))
        squeezed = ''.join(''.join(lines).split())
        for whole in (LONG, ctx['issuer']['legal_name'], ctx['issuer']['trade_name'], ctx['issuer']['address'],
                      ctx['customer']['legal_name'], *LONG_MESSAGES['thank_you_message'].split('\n'),
                      LONG_MESSAGES['footer_text'], f"Valor resumen: {ctx['digest_value']}", f"{ctx['legend']}."):
            self.assertIn(''.join(whole.split()), squeezed)
        # Every amount whole on one line: 2 per item row (price, amount) and the three totals.
        self.assertEqual(sum(line.count('S/ 9999999.99') for line in lines), 2 * 36 + 3)
        for n in range(35):
            self.assertEqual(sum(f'Artículo de prueba {n:03d}' in line for line in lines), 1)
        self.assertEqual(sum(line.count(' 999.5 NIU x S/ 9999999.99') for line in lines), 36)

    def test_the_items_keep_their_order_and_the_totals_follow_them(self):
        lines = self.text(self.roll(self.big_ctx()))
        positions = [next(i for i, line in enumerate(lines) if f'Artículo de prueba {n:03d}' in line)
                     for n in range(35)]
        self.assertEqual(positions, sorted(positions))
        total = next(i for i, line in enumerate(lines) if line.startswith('TOTAL '))
        self.assertGreater(total, positions[-1])

    def test_a_substituted_character_is_measured_as_printed(self):
        """`…` prints as `...`: a line is wrapped by what the printer gets, not by what was typed."""
        text = 'a' * 46 + '……'
        lines = escpos_reader.text(Receipt().line(text).to_bytes())
        self.assertTrue(all(len(line) <= 48 for line in lines), lines)
        self.assertEqual(''.join(lines), 'a' * 46 + '......')
        lines = escpos_reader.text(Receipt().row('‰' * 30, 'S/ 1.00').to_bytes())
        self.assertTrue(all(len(line) <= 48 for line in lines), lines)
        self.assertEqual(''.join(lines).count('o/oo'), 30)


# ---------------------------------------------------------------------------
# The logo
# ---------------------------------------------------------------------------

WIDE = png((240, 80))


class FiscalEscposLogoTest(FiscalEscposBase):
    def setUp(self):
        super().setUp()
        self.document = self.receipt()

    def size(self, logo, **design):
        return raster(self.roll(self.ctx(self.document, logo=logo, **design)))

    def test_the_logo_takes_the_box_of_the_80_mm_ticket(self):
        # medium box 42 × 16 mm = 336 × 128 dots.
        self.assertEqual(self.size(WIDE)[:2], (336, 112))
        self.assertEqual(self.size(png((500, 500)))[:2], (128, 128))
        self.assertEqual(self.size(png((300, 600)))[:2], (64, 128))

    def test_the_three_sizes_and_a_width_in_millimetres(self):
        for design, expected in (({'logo_size': 'small'}, (240, 80)),
                                 ({'logo_size': 'large'}, (480, 160)),
                                 ({'logo_ticket_width_mm': 50}, (400, 133)),
                                 ({'logo_ticket_width_mm': 72}, (576, 192))):
            with self.subTest(design):
                self.assertEqual(self.size(WIDE, **design)[:2], expected)

    def test_never_taller_than_24_mm_never_distorted(self):
        self.assertEqual(self.size(png((500, 500)), logo_ticket_width_mm=72)[:2], (192, 192))
        self.assertEqual(self.size(png((300, 600)), logo_ticket_width_mm=72)[:2], (96, 192))

    def test_a_small_logo_grows_to_its_box_and_not_past_it(self):
        self.assertEqual(self.size(png((60, 20)))[:2], (336, 112))
        self.assertEqual(self.size(png((30, 30)), logo_size='small')[:2], (88, 88))

    def test_the_same_box_as_the_pdf_ticket(self):
        for logo in (WIDE, png((500, 500)), png((300, 600))):
            for design in ({}, {'logo_size': 'large'}, {'logo_ticket_width_mm': 72}, {'logo_ticket_width_mm': 25}):
                with self.subTest(design=design):
                    ctx = self.ctx(self.document, logo=logo, **design)
                    width_pt, height_pt, _x, _y = drawn_images(render_fiscal_ticket(ctx))[0][0]
                    width, height, _bits, _align = raster(self.roll(ctx))
                    # DOC-TICKET-PREMIUM: the PDF page is the 72 mm printable strip, so its content is
                    # 64 mm; RAW prints on the whole head (576 dots, verified on paper). A box wider than
                    # the PDF's content is narrowed there, in proportion; up to 64 mm both are the same.
                    pdf_content_dots = 64 * escpos.DOTS_PER_MM
                    if width <= pdf_content_dots:
                        self.assertLess(abs(width - width_pt / MM * escpos.DOTS_PER_MM), 8)      # whole bytes
                        self.assertLessEqual(abs(height - height_pt / MM * escpos.DOTS_PER_MM), 1)
                    else:
                        self.assertLessEqual(width_pt / MM, 64 + 0.01)
                        self.assertAlmostEqual(width_pt / height_pt, width / height, delta=0.05)

    def test_the_alignment_of_the_design(self):
        for align, byte in (('left', 0), ('center', 1), ('right', 2)):
            with self.subTest(align):
                self.assertEqual(self.size(WIDE, logo_ticket_align=align)[3], byte)
        self.assertEqual(self.size(WIDE)[3], 1)                           # centred by default

    def test_no_logo_or_an_unreadable_one_prints_no_image_and_the_same_roll(self):
        plain = self.roll(self.ctx(self.document))
        self.assertNotIn(b'\x1dv0', plain)
        with self.assertLogs('store.printing.escpos', 'WARNING'):
            self.assertEqual(self.roll(self.ctx(self.document, logo=b'not an image')), plain)

    def test_the_logo_adds_one_line_and_changes_nothing_else(self):
        plain = escpos_reader.lines(self.roll(self.ctx(self.document)))
        branded = escpos_reader.lines(self.roll(self.ctx(self.document, logo=WIDE, logo_ticket_align='right')))
        self.assertEqual(branded[1], '<R><IMAGEN 336x112>')
        self.assertEqual(branded[:1] + branded[2:], plain)


# ---------------------------------------------------------------------------
# The messages
# ---------------------------------------------------------------------------

class FiscalEscposMessagesTest(FiscalEscposBase):
    def test_the_messages_come_after_the_mandatory_block_behind_a_rule(self):
        document = self.receipt()
        self.save_design(document.company, **MESSAGES)
        ctx = build_fiscal_context(document)
        lines = self.text(fiscal_ticket(document))
        mandatory = [lines.index(f"Estado: {ctx['status_text']}"),
                     lines.index(f"<QR {ctx['qr_payload']}>"),
                     lines.index(ctx['digest_value']),
                     lines.index('electrónica.'),
                     *(lines.index(beta.replace('—', '-')) for beta in legends.FISCAL_BETA_ROLL_LINES)]
        first, second = (as_printed(text) for text in THANKS.split('\n'))
        footer = as_printed(FOOTER)
        self.assertLess(max(mandatory), lines.index(first))
        self.assertEqual(lines[lines.index(first) - 1], '-' * 48)
        self.assertLess(lines.index(first), lines.index(second))
        self.assertLess(lines.index(second), lines.index(footer))
        self.assertEqual(lines[-1], footer)

    def test_the_thanks_are_bold_and_centred_and_the_footer_centred(self):
        document = self.receipt()
        self.save_design(document.company, **MESSAGES)
        lines = escpos_reader.lines(fiscal_ticket(document))
        for text in THANKS.split('\n'):
            self.assertIn('<C><B>' + as_printed(text), lines)
        self.assertEqual(lines[-1], '<C>' + as_printed(FOOTER))

    def test_the_messages_add_their_words_and_nothing_else(self):
        for kind in ('receipt', 'invoice'):
            with self.subTest(kind):
                document = getattr(self, kind)()
                CompanyDocumentDesign.objects.filter(company=document.company).delete()
                plain = fiscal_ticket(document)
                self.save_design(document.company, **MESSAGES)
                designed = fiscal_ticket(document)
                before, after = escpos_reader.lines(plain), escpos_reader.lines(designed)
                self.assertEqual(words(after) - words(before), message_words())
                self.assertEqual(words(before) - words(after), Counter())
                # The roll as it was, then the rule and the messages.
                self.assertEqual(after[:len(before)], before)

    def test_an_empty_design_adds_nothing(self):
        document = self.receipt()
        self.save_design(document.company, thank_you_message='', footer_text='')
        with_row = fiscal_ticket(document)
        CompanyDocumentDesign.objects.filter(company=document.company).delete()
        self.assertEqual(with_row, fiscal_ticket(document))

    def test_the_options_of_the_sales_note_never_reach_the_roll(self):
        document = self.receipt()
        self.assertEqual(self.roll(self.ctx(document, **NOT_FOR_FISCAL)), self.roll(self.ctx(document)))

    def test_a_stored_message_that_fails_the_screen_is_not_printed(self):
        document = self.receipt()
        self.save_design(document.company, thank_you_message='Aceptada por SUNAT', footer_text='Pie de prueba bueno')
        with self.assertLogs('store.fiscal_pdf_services', 'WARNING') as logs:
            lines = self.text(fiscal_ticket(document))
        self.assertFalse(any('Aceptada' in line for line in lines))
        self.assertEqual(lines[-1], 'Pie de prueba bueno')
        self.assertTrue(all('thank_you_message' in line for line in logs.output), logs.output)

        self.save_design(document.company, thank_you_message='Gracias de prueba', footer_text='RUC 20123456789')
        with self.assertLogs('store.fiscal_pdf_services', 'WARNING'):
            lines = self.text(fiscal_ticket(document))
        self.assertEqual(lines[-1], 'Gracias de prueba')
        self.assertFalse(any('20123456789' in line for line in lines))

    def test_a_clean_design_logs_nothing(self):
        document = self.receipt()
        self.save_design(document.company, **MESSAGES)
        with self.assertNoLogs('store.fiscal_pdf_services', 'WARNING'):
            fiscal_ticket(document)


# ---------------------------------------------------------------------------
# What a design never moves
# ---------------------------------------------------------------------------

class FiscalEscposDataUnchangedTest(FiscalEscposBase):
    def documents(self):
        return (('factura', self.invoice()), ('boleta', self.receipt()))

    def test_the_qr_is_printed_with_its_payload_and_size_whatever_the_design(self):
        for kind, document in self.documents():
            with self.subTest(kind):
                CompanyDocumentDesign.objects.filter(company=document.company).delete()
                plain = fiscal_ticket(document)
                self.save_design(document.company, **EVERYTHING)
                designed = fiscal_ticket(document)
                ctx = build_fiscal_context(document)
                for data in (plain, designed):
                    qrs = [piece for _text, line in printed_lines(data) for piece in line.pieces
                           if isinstance(piece, escpos_reader.QR)]
                    self.assertEqual(qrs, [escpos_reader.QR(ctx['qr_payload'])])
                    self.assertEqual(data.count(b'\x1d(k\x03\x00\x31\x43\x06'), 1)     # module 6
                    self.assertEqual(data.count(b'\x1d(k\x03\x00\x31\x45\x31'), 1)     # correction M
                    self.assertEqual(data.count(b'\x1d(k\x03\x00\x31\x51\x30'), 1)     # printed once

    def test_every_fiscal_text_is_printed_with_any_design(self):
        for kind, document in self.documents():
            self.save_design(document.company, **EVERYTHING)
            ctx = build_fiscal_context(document)
            with self.subTest(kind):
                text = '\n'.join(self.text(fiscal_ticket(document)))
                flat = ' '.join(text.split())
                for expected in (
                    ctx['identifier'], f"RUC {ctx['issuer']['tax_id']}", ctx['issuer']['legal_name'],
                    ctx['title'], ctx['customer']['doc_number'], ctx['customer']['legal_name'],
                    ctx['issued_at'].strftime('%d/%m/%Y %H:%M'),
                    f"S/ {ctx['taxable_amount']:.2f}", f"S/ {ctx['tax_amount']:.2f}", f"S/ {ctx['total']:.2f}",
                    'IGV (18%)', f"SON: {ctx['amount_in_words']}", f"{ctx['legend']}.",
                    f"Estado: {ctx['status_text']}", f"Valor resumen: {ctx['digest_value']}",
                    *(line.replace('—', '-') for line in legends.FISCAL_BETA_ROLL_LINES),
                ):
                    self.assertIn(expected, flat)
                for unwanted in ('Recibo de tienda', 'posventa', 'comercial de prueba', 'garantía'):
                    self.assertNotIn(unwanted, flat)

    def test_printing_with_a_design_changes_nothing_stored(self):
        for kind, document in self.documents():
            with self.subTest(kind):
                before = model_to_dict(FiscalDocument.objects.get(pk=document.pk))
                self.save_design(document.company, **EVERYTHING)
                fiscal_ticket(document)
                self.assertEqual(model_to_dict(FiscalDocument.objects.get(pk=document.pk)), before)

    def test_the_roll_is_drawn_from_the_context_alone(self):
        document = self.receipt()
        ctx = build_fiscal_context(document)
        with self.assertNumQueries(0):
            data = fiscal_ticket_from_ctx(ctx)
        self.assertEqual(data, fiscal_ticket(document))


class FiscalEscposWarningsTest(FiscalEscposBase):
    def set(self, document, **fields):
        FiscalDocument.objects.filter(pk=document.pk).update(**fields)
        return FiscalDocument.objects.get(pk=document.pk)

    def test_production_carries_no_beta_mark(self):
        document = self.set(self.receipt(), environment=FiscalEnvironment.PRODUCTION,
                            status=FiscalDocumentStatus.ACCEPTED)
        self.save_design(document.company, **EVERYTHING)
        text = '\n'.join(self.text(fiscal_ticket(document)))
        self.assertNotIn('SUNAT BETA', text)
        self.assertNotIn('SIN VALIDEZ', text.upper())
        self.assertIn('Estado: Aceptada por SUNAT', text)

    def test_the_beta_lines_sit_under_the_number_before_the_buyer(self):
        document = self.receipt()
        self.save_design(document.company, **EVERYTHING)
        lines = escpos_reader.lines(fiscal_ticket(document))
        number = lines.index('<C><B><2X>B001-1')
        self.assertEqual(lines[number + 1:number + 3],
                         ['<C><B>' + line.replace('—', '-') for line in legends.FISCAL_BETA_ROLL_LINES])
        self.assertLess(number + 2, next(i for i, line in enumerate(lines) if line.startswith('DNI:')))

    def test_rejected_and_pending_say_so_before_the_messages(self):
        for status, wording in ((FiscalDocumentStatus.REJECTED, legends.FISCAL_REJECTED_STATUS),
                                (FiscalDocumentStatus.SUBMISSION_ERROR, 'Pendiente de confirmación de SUNAT')):
            with self.subTest(status=status):
                document = self.set(self.receipt(), status=status)
                self.save_design(document.company, **EVERYTHING)
                text = '\n'.join(self.text(fiscal_ticket(document)))
                printed = as_printed(f'Estado: {wording}')
                self.assertIn(printed, text)
                self.assertNotIn('Aceptada por SUNAT', text)
                self.assertLess(text.index(printed), text.index(as_printed(THANKS.split('\n')[0])))


class DefaultDesignTest(SimpleTestCase):
    def test_the_default_design_has_no_messages(self):
        # The plain goldens are the rolls of a company without a design row: no message, no rule after the legend.
        self.assertEqual((DEFAULT.thank_you_message, DEFAULT.footer_text), ('', ''))
        self.assertEqual(replace(DEFAULT).ticket_logo_box_mm, (42, 16))
