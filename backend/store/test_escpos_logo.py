"""
DOC-ESCPOS-LOGO — the logotype on the thermal roll.

  * THE SALES NOTE prints the company's document logo, the one frozen with the
    note, in the box its design gives the 80 mm ticket (`ticket_logo_box_mm`, at
    8 dots per millimetre: a 203 dpi head) and with the design's alignment. The
    box never passes 24 mm (192 dots) of height, whatever the logo's shape.
  * THE FISCAL TICKET prints the logo frozen with the document in the same box
    and alignment of the design (FISCAL-ESCPOS-DESIGN; it was 48 mm wide, and a
    square logo printed 48 × 48 mm before the 24 mm cap). Not a word of it
    changes, nor the QR: `test_fiscal_escpos_design`.
  * A LOGO NEVER STOPS A PRINT. No logo, an unreadable one or one with nothing to
    print: no image, and the rest of the roll as it was.
  * READABLE ON A THERMAL HEAD. A transparent background is paper, never ink; a
    pale logo is stretched to black; a photograph is dithered, a flat logo is not.
"""
import io
import re

from django.test import SimpleTestCase, override_settings
from PIL import Image

from store.models import CompanyDocumentDesign
from store.printing import escpos
from store.printing.escpos import MAX_IMAGE_HEIGHT_DOTS, Receipt
from store.test_document_design import DocumentBase
from store.test_document_goldens import escpos_lines
from store.test_fiscal_logo import _ROOT as LOGO_ROOT, FiscalLogoBase

RASTER = b'\x1dv0\x00'


def png(size, *, colour=(0, 0, 0, 255), background=(0, 0, 0, 0), margin=0, mode='RGBA') -> bytes:
    """A logo of `size` px: a `colour` block on a `background` (transparent by default)."""
    image = Image.new(mode, size, background)
    for x in range(margin, size[0] - margin):
        for y in range(margin, size[1] - margin):
            image.putpixel((x, y), colour)
    out = io.BytesIO()
    image.save(out, 'PNG')
    return out.getvalue()


def raster(data: bytes) -> tuple[int, int, bytes, int]:
    """(width in dots, height in dots, the bitmap, the alignment byte in force) of the only image in `data`."""
    assert data.count(RASTER) == 1, data.count(RASTER)
    at = data.index(RASTER)
    width_bytes = data[at + 4] | data[at + 5] << 8
    height = data[at + 6] | data[at + 7] << 8
    align = data.rindex(b'\x1ba', 0, at)
    return width_bytes * 8, height, data[at + 8:at + 8 + width_bytes * height], data[align + 2]


class RasterSizeTest(SimpleTestCase):
    """The box is a maximum; the logo keeps its proportions inside it."""

    def size(self, logo, **box):
        width, height, _bits, _align = raster(Receipt().image(logo, **box).to_bytes())
        return width, height

    def test_a_wide_logo_fills_the_width_of_its_box(self):
        self.assertEqual(self.size(png((1040, 352)), max_dots=336, max_height=128), (336, 114))

    def test_a_square_logo_is_held_by_the_height_of_its_box(self):
        self.assertEqual(self.size(png((500, 500)), max_dots=336, max_height=128), (128, 128))

    def test_a_tall_logo_is_held_by_the_height_of_its_box(self):
        self.assertEqual(self.size(png((300, 600)), max_dots=336, max_height=128), (64, 128))

    def test_no_box_is_taller_than_24_mm(self):
        self.assertEqual(MAX_IMAGE_HEIGHT_DOTS, 24 * escpos.DOTS_PER_MM)
        self.assertEqual(self.size(png((500, 500)), max_dots=576, max_height=1000), (192, 192))
        self.assertEqual(self.size(png((300, 600)), max_dots=576), (96, 192))

    def test_the_default_box_is_the_fiscal_one_48_mm_wide_and_24_mm_tall(self):
        # Before DOC-ESCPOS-LOGO the square one printed 384 × 384 dots: 48 × 48 mm.
        self.assertEqual(self.size(png((500, 500))), (192, 192))
        self.assertEqual(self.size(png((1040, 352))), (384, 130))

    def test_a_small_logo_is_not_enlarged_unless_asked(self):
        self.assertEqual(self.size(png((64, 16))), (64, 16))
        self.assertEqual(self.size(png((60, 20)), max_dots=336, max_height=128, enlarge=True), (336, 112))

    def test_the_paper_is_the_last_limit(self):
        roll = Receipt(paper_width_mm=58)
        width, _height, _bits, _align = raster(roll.image(png((1040, 352)), max_dots=576, enlarge=True).to_bytes())
        self.assertEqual(width, 384)


class RasterAlignmentTest(SimpleTestCase):
    def test_the_alignment_is_set_right_before_the_image(self):
        for align, byte in (('left', 0), ('center', 1), ('right', 2)):
            with self.subTest(align):
                data = Receipt().image(png((64, 16)), align=align).to_bytes()
                at = data.index(RASTER)
                self.assertEqual(data[at - 3:at], b'\x1ba' + bytes([byte]))
                self.assertEqual(raster(data)[3], byte)

    def test_centred_by_default_as_it_always_was(self):
        self.assertEqual(raster(Receipt().image(png((64, 16))).to_bytes())[3], 1)

    def test_an_unknown_alignment_is_centred(self):
        self.assertEqual(raster(Receipt().image(png((64, 16)), align='justify').to_bytes())[3], 1)


class RasterRobustnessTest(SimpleTestCase):
    def test_no_logo_or_an_unreadable_one_prints_nothing_and_does_not_fail(self):
        bare = Receipt().line('Hola').to_bytes()
        for logo in (None, b'', b'not an image', png((64, 16))[:40]):
            with self.subTest(logo=logo[:12] if logo else logo):
                with self.assertLogs('store.printing.escpos', 'WARNING') if logo else _nothing():
                    data = Receipt().image(logo).line('Hola').to_bytes()
                self.assertNotIn(RASTER, data)
                self.assertEqual(data, bare)

    def test_a_logo_with_nothing_to_print_sends_no_empty_image(self):
        for logo in (png((64, 16), colour=(255, 255, 255, 255)), png((64, 16), colour=(0, 0, 0, 0))):
            self.assertNotIn(RASTER, Receipt().image(logo).to_bytes())

    def test_a_transparent_background_is_paper_even_when_its_hidden_colour_is_black(self):
        # A PNG with alpha often stores black under its transparent pixels: read as grey, it would print a block.
        width, height, bits, _align = raster(Receipt().image(
            png((64, 32), colour=(0, 0, 0, 255), background=(0, 0, 0, 0), margin=8)).to_bytes())
        self.assertEqual((width, height), (64, 32))
        rows = [bits[y * 8:(y + 1) * 8] for y in range(height)]
        self.assertEqual(set(rows[0]) | set(rows[-1]), {0})                       # the transparent margin
        self.assertEqual(rows[16][1:7], b'\xff' * 6)                              # the shape
        self.assertEqual((rows[16][0], rows[16][7]), (0, 0))

    def test_a_palette_or_grey_png_prints_too(self):
        for mode, colour, background in (('L', 0, 255), ('P', 1, 0), ('1', 0, 1), ('LA', (0, 255), (0, 0))):
            with self.subTest(mode):
                image = Image.new(mode, (32, 8), background)
                if mode == 'P':
                    image.putpalette([255, 255, 255, 0, 0, 0])
                for x in range(32):
                    image.putpixel((x, 4), colour)
                out = io.BytesIO()
                image.save(out, 'PNG')
                _w, _h, bits, _a = raster(Receipt().image(out.getvalue()).to_bytes())
                self.assertEqual(bits[4 * 4:5 * 4], b'\xff' * 4)
                self.assertEqual(bits[:4], b'\x00' * 4)

    def test_a_pale_logo_is_stretched_to_black_instead_of_vanishing(self):
        _w, _h, bits, _a = raster(Receipt().image(
            png((64, 16), colour=(205, 205, 205, 255), background=(255, 255, 255, 255), margin=4)).to_bytes())
        self.assertEqual(bits[8 * 8 + 1:8 * 8 + 7], b'\xff' * 6)

    def test_a_flat_logo_is_thresholded_and_a_photograph_dithered(self):
        flat = raster(Receipt().image(png((64, 16), colour=(40, 40, 40, 255), margin=4)).to_bytes())[2]
        self.assertTrue(set(flat) <= {0x00, 0xFF, 0x0F, 0xF0})                     # solid block, clean edges

        ramp = Image.new('L', (256, 32))
        for x in range(256):
            for y in range(32):
                ramp.putpixel((x, y), x)
        out = io.BytesIO()
        ramp.save(out, 'PNG')
        width, height, bits, _a = raster(Receipt().image(out.getvalue(), max_dots=256).to_bytes())
        black = [sum(bin(bits[y * (width // 8) + xb]).count('1') for y in range(height)) for xb in range(width // 8)]
        self.assertGreater(black[0], black[-1])                                   # darker on the dark side
        middle = black[len(black) // 2]
        self.assertTrue(0 < middle < 8 * height, middle)                          # grey is a mix, not a block


class DitherDecisionTest(SimpleTestCase):
    def test_a_small_thin_line_logo_enlarged_is_thresholded_not_dithered(self):
        # 1-px diagonal strokes on 30 × 10 px: no mid-tone at all. Enlarged ×4 with LANCZOS, the strokes' soft
        # edges are ~40 % of the pixels; measured AFTER the resize, the logo would be wrongly dithered.
        from PIL import ImageOps

        image = Image.new('RGB', (30, 10), (255, 255, 255))
        for x in range(30):
            for y in range(10):
                if (x + y) % 3 == 0:
                    image.putpixel((x, y), (0, 0, 0))
        out = io.BytesIO()
        image.save(out, 'PNG')

        _w, _h, bits, _a = raster(Receipt().image(out.getvalue(), max_dots=120, max_height=40, enlarge=True).to_bytes())

        grey = ImageOps.autocontrast(image.convert('L')).resize((120, 40), Image.LANCZOS)
        thresholded = grey.point(lambda v: 0 if v < 128 else 255, mode='1').tobytes('raw', '1;I')
        self.assertEqual(bits, thresholded)


class _nothing:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@override_settings(EVIDENCE_STORAGE_BACKEND='filesystem', EVIDENCE_STORAGE_ROOT=LOGO_ROOT)
class SalesNoteLogoTest(DocumentBase):
    def logo(self, size):
        from store import storefront_media
        from store.models import CompanySettings

        image = storefront_media.store(company=self.company, actor=self.staff, raw=png(size), audit=False)
        CompanySettings.objects.filter(company=self.company).update(
            document_logo_url=storefront_media.payload(image)['url'])

    def roll(self, **design):
        if design:
            CompanyDocumentDesign.objects.update_or_create(company=self.company, defaults=design)
        note = self.note_for(self.sale([(self.case, 1)]))
        return escpos.sales_note_ticket(note)

    def test_a_note_without_a_logo_prints_no_image(self):
        self.assertNotIn(RASTER, self.roll())

    def test_the_logo_heads_the_roll_in_the_box_of_the_80_mm_ticket(self):
        self.logo((240, 80))
        lines = escpos_lines(self.roll())
        # medium box 42 × 16 mm = 336 × 128 dots; a 3:1 logo fills its width.
        self.assertEqual(lines[1], '<C><IMAGEN 336x112>')
        self.assertTrue(lines[2].startswith('<C><B>'))                           # then the company's name

    def test_the_three_sizes_and_a_width_in_millimetres(self):
        self.logo((240, 80))
        for design, expected in (({'logo_size': 'small'}, (240, 80)),          # 30 × 11 mm = 240 × 88
                                 ({'logo_size': 'large'}, (480, 160)),         # 60 × 22 mm = 480 × 176
                                 ({'logo_ticket_width_mm': 50}, (400, 133)),   # 50 × 19 mm
                                 ({'logo_ticket_width_mm': 72}, (576, 192))):  # 72 × 24 mm (the cap)
            with self.subTest(design):
                self.assertEqual(raster(self.roll(**{'logo_size': 'medium', 'logo_ticket_width_mm': 0, **design}))[:2],
                                 expected)

    def test_a_square_logo_never_prints_taller_than_24_mm(self):
        self.logo((500, 500))
        self.assertEqual(raster(self.roll(logo_ticket_width_mm=72))[:2], (192, 192))
        self.assertEqual(raster(self.roll(logo_ticket_width_mm=0, logo_size='medium'))[:2], (128, 128))

    def test_a_tall_logo_keeps_its_proportions(self):
        self.logo((300, 600))
        self.assertEqual(raster(self.roll(logo_ticket_width_mm=72))[:2], (96, 192))

    def test_the_alignment_of_the_design(self):
        self.logo((240, 80))
        for align, byte in (('left', 0), ('center', 1), ('right', 2)):
            with self.subTest(align):
                self.assertEqual(raster(self.roll(logo_ticket_align=align))[3], byte)

    def test_the_note_prints_the_logo_it_was_issued_with(self):
        from store.models import CompanySettings

        self.logo((240, 80))
        note = self.note_for(self.sale([(self.case, 1)]))
        CompanySettings.objects.filter(company=self.company).update(document_logo_url='')
        self.assertEqual(raster(escpos.sales_note_ticket(note))[:2], (336, 112))

    def test_the_logo_adds_one_line_and_changes_nothing_else(self):
        def roll():           # two notes differ in their numbers, and in the minute when a minute ends between them
            return [re.sub(r'NV-\d+|#\d+|\d{2}/\d{2}/\d{4} \d{2}:\d{2}', '#', line)
                    for line in escpos_lines(self.roll())]

        plain = roll()
        self.logo((240, 80))
        branded = roll()
        self.assertEqual(branded[1], '<C><IMAGEN 336x112>')
        self.assertEqual(branded[:1] + branded[2:], plain)


class FiscalTicketLogoTest(FiscalLogoBase):
    def ticket(self, size=None):
        if size:
            self.set_logo(self.upload(png(size)))
        return escpos.fiscal_ticket(self.issue())

    def test_a_square_logo_takes_the_height_of_the_design_box(self):
        # FISCAL-ESCPOS-DESIGN: the medium box of the 80 mm ticket, 42 × 16 mm (was 192 × 192, the 24 mm cap).
        self.assertEqual(raster(self.ticket((500, 500)))[:2], (128, 128))

    def test_a_wide_logo_takes_the_width_of_the_design_box(self):
        # Was 384 × 130: the fixed 48 mm of width the fiscal roll had before its design.
        self.assertEqual(raster(self.ticket((1040, 352)))[:2], (336, 114))

    def test_only_the_image_line_changes_text_and_qr_stay(self):
        self.set_logo(self.upload(png((500, 500))))
        document = self.issue()
        branded = escpos_lines(escpos.fiscal_ticket(document))
        document.logo_storage_key = ''                                    # the same document, without its logo
        plain = escpos_lines(escpos.fiscal_ticket(document))
        self.assertEqual(branded[1], '<C><IMAGEN 128x128>')
        self.assertEqual(branded[:1] + branded[2:], plain)
        self.assertEqual(sum(line.startswith('<C><QR ') for line in branded), 1)
