"""
LABEL-03 — the pure label package (`store/labels/`): layouts, symbols and PDF.

No database. The package receives data that is already resolved (a product's
codes, a unit's identifiers) and answers with plans and a real-scale PDF. What
these tests pin is what the owner decided (ADR §1.1) and what D5/D10 promise:

  * the page of the PDF IS the label: 30×20 mm, 50×25 mm, to the point;
  * N accessories are N copies of ONE label; N devices are N labels;
  * battery only on a used device with a measurement; never 100 % assumed;
  * no cost, margin, supplier or customer on any label;
  * the IMEI only with the company's `imei1` profile AND the sensitive flag;
  * a code that does not fit is refused, never shrunk below the module;
  * an IMEI or a serial is never cut;
  * a missing or broken logo never stops a label.
"""
import io
import re
from decimal import Decimal

from django.test import SimpleTestCase

from store import labels
from store.labels import code128, fields as label_fields
from store.labels.data import AccessoryData, DeviceData, DeviceOptions, LabelCode, LabelDataError
from store.labels.layout import (
    ACCESSORY_30X20, DEVICE_COMMERCIAL_50X25, DEVICE_TECHNICAL_60X40, LayoutError,
)

MM = 72 / 25.4


def luhn(body14: str) -> str:
    from store.device_identity import luhn_check_digit
    return body14 + luhn_check_digit(body14)


IMEIS = [luhn(f'3569380356438{n}') for n in range(5)]
IMEI_2 = luhn('35693803564399')


def media_boxes(pdf: bytes):
    return [tuple(float(v) for v in m) for m in re.findall(
        rb'/MediaBox \[\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s*\]', pdf)]


def page_count(pdf: bytes) -> int:
    return len(re.findall(rb'/Type /Page(?![s\w])', pdf))


def accessory(**extra):
    base = dict(
        product_id=1, name='Cable USB-C 1 m', price=Decimal('39.90'), currency='S/',
        codes=(LabelCode('C000701', 'internal', True),),
    )
    base.update(extra)
    return AccessoryData(**base)


def device(n=0, **extra):
    base = dict(
        unit_id=100 + n, product_id=7, model='Teléfono Ficticio X', price=Decimal('2999.00'),
        currency='S/', condition_line='new', condition_label='Nuevo',
        attributes=(('Capacidad', '256 GB'), ('Color', 'Azul')),
        imei=IMEIS[n % len(IMEIS)], serial=f'FICT{n:08d}', label_code=f'U48271930{n}{n}',
    )
    base.update(extra)
    return DeviceData(**base)


SENSITIVE = DeviceOptions(profile='imei1', sensitive_allowed=True)


def red_png(width=120, height=60, mode='RGBA'):
    from PIL import Image
    image = Image.new(mode, (width, height), (200, 20, 20, 255) if mode == 'RGBA' else (200, 20, 20))
    out = io.BytesIO()
    image.save(out, 'PNG')
    return out.getvalue()


class LayoutSizeTest(SimpleTestCase):
    def test_the_pdf_page_is_the_label_to_the_point(self):
        cases = [
            (labels.plan_accessory(accessory(), ACCESSORY_30X20), (30, 20)),
            (labels.plan_device(device(), DEVICE_COMMERCIAL_50X25, SENSITIVE), (50, 25)),
            (labels.plan_device(device(), DEVICE_TECHNICAL_60X40, SENSITIVE), (60, 40)),
        ]
        for plan, (w, h) in cases:
            self.assertEqual(plan.status, 'ready', plan.reasons)
            boxes = media_boxes(labels.render_pdf([plan]))
            self.assertTrue(boxes)
            for box in boxes:
                self.assertEqual(box[:2], (0.0, 0.0))
                self.assertAlmostEqual(box[2], w * MM, places=2)
                self.assertAlmostEqual(box[3], h * MM, places=2)

    def test_built_in_layouts_carry_the_owner_sizes_and_the_assumed_printer(self):
        self.assertEqual((ACCESSORY_30X20.width_mm, ACCESSORY_30X20.height_mm), (30, 20))
        self.assertEqual((DEVICE_COMMERCIAL_50X25.width_mm, DEVICE_COMMERCIAL_50X25.height_mm), (50, 25))
        self.assertGreater(DEVICE_TECHNICAL_60X40.width_mm, DEVICE_COMMERCIAL_50X25.width_mm)
        for layout in labels.BUILTIN_LAYOUTS.values():
            self.assertEqual(layout.dpi, 203)
            self.assertEqual(layout.min_module_dots, 2)
            self.assertGreaterEqual(layout.module_mm, code128.MIN_READABLE_MODULE_MM)

    def test_a_template_can_override_size_but_never_below_the_readable_module(self):
        bigger = ACCESSORY_30X20.with_overrides(width_mm=40, height_mm=30)
        self.assertEqual((bigger.width_mm, bigger.height_mm, bigger.purpose), (40, 30, 'accessory'))
        plan = labels.plan_accessory(accessory(), bigger)
        self.assertAlmostEqual(media_boxes(labels.render_pdf([plan]))[0][2], 40 * MM, places=2)
        with self.assertRaises(LayoutError):
            ACCESSORY_30X20.with_overrides(min_module_dots=1)   # 0,125 mm at 203 dpi
        with self.assertRaises(LayoutError):
            ACCESSORY_30X20.with_overrides(width_mm=0)


class CopiesTest(SimpleTestCase):
    def test_ten_accessories_are_ten_copies_of_one_label(self):
        plan = labels.plan_accessory(accessory(copies=10), ACCESSORY_30X20)
        self.assertEqual(plan.copies, 10)
        pdf = labels.render_pdf([plan], compress=False)
        self.assertEqual(page_count(pdf), 10)
        self.assertEqual(pdf.count(b'(Cable USB-C 1 m)'), 10)

    def test_five_devices_are_five_distinct_labels(self):
        plans = labels.plan_labels(
            devices=[device(n) for n in range(5)], options=SENSITIVE)
        self.assertEqual([p.copies for p in plans], [1] * 5)
        pdf = labels.render_pdf(plans, compress=False)
        self.assertEqual(page_count(pdf), 5)
        for imei in IMEIS:
            self.assertEqual(pdf.count(f'({imei})'.encode()), 1)

    def test_a_device_has_no_copies_and_a_unit_is_never_listed_twice(self):
        with self.assertRaises(LabelDataError):
            DeviceData.from_mapping({**device().as_mapping(), 'copies': 3})
        with self.assertRaises(LabelDataError):
            labels.plan_labels(devices=[device(0), device(0)], options=SENSITIVE)
        with self.assertRaises(LabelDataError):
            accessory(copies=0)


class BatteryTest(SimpleTestCase):
    def lines(self, plan):
        return [t for s in plan.stickers for t in s.text_lines()]

    def test_a_used_device_with_a_measurement_shows_it(self):
        plan = labels.plan_device(
            device(condition_line='used', condition_label='Seminuevo', battery_health=87),
            DEVICE_COMMERCIAL_50X25, SENSITIVE)
        self.assertEqual(plan.status, 'ready', plan.reasons)
        self.assertTrue(any('Batería 87 %' in line for line in self.lines(plan)))

    def test_a_used_device_without_a_measurement_says_so_and_never_assumes_100(self):
        plan = labels.plan_device(
            device(condition_line='used', condition_label='Seminuevo', battery_health=None),
            DEVICE_COMMERCIAL_50X25, SENSITIVE)
        self.assertEqual(plan.status, 'ready', plan.reasons)
        text = ' '.join(self.lines(plan))
        self.assertIn('Batería no informada', text)
        self.assertNotIn('100', text)

    def test_without_room_the_unmeasured_used_device_goes_to_review(self):
        cramped = DEVICE_COMMERCIAL_50X25.with_overrides(height_mm=15)
        plan = labels.plan_device(
            device(condition_line='used', condition_label='Seminuevo', battery_health=None),
            cramped, SENSITIVE)
        self.assertEqual(plan.status, 'review')
        self.assertTrue(any('Batería no informada' in r for r in plan.reasons), plan.reasons)

    def test_a_new_device_never_shows_a_battery(self):
        for health in (None, 100, 91):
            plan = labels.plan_device(device(battery_health=health), DEVICE_COMMERCIAL_50X25, SENSITIVE)
            self.assertFalse(any('Batería' in line for line in self.lines(plan)), health)


class SensitiveFieldsTest(SimpleTestCase):
    def test_commercial_registries_never_carry_cost_margin_supplier_or_customer(self):
        self.assertEqual(label_fields.ALLOWED['accessory'], frozenset({
            'logo', 'name', 'variant', 'price', 'barcode', 'code_text'}))
        self.assertEqual(label_fields.ALLOWED['device_commercial'], frozenset({
            'logo', 'model', 'attributes', 'attribute:*', 'condition', 'battery', 'price',
            'barcode', 'code_text'}))
        for purpose, allowed in label_fields.ALLOWED.items():
            self.assertFalse(allowed & label_fields.FORBIDDEN, purpose)
            self.assertFalse({'imei', 'imei2', 'serial'} & allowed and purpose != 'device_technical')

    def test_a_template_cannot_add_a_forbidden_or_technical_field(self):
        for key in ('cost', 'margin', 'supplier', 'customer', 'imei', 'serial'):
            with self.assertRaises(LayoutError, msg=key):
                ACCESSORY_30X20.with_overrides(fields=ACCESSORY_30X20.fields + (label_fields.spec(key),))
            with self.assertRaises(LayoutError, msg=key):
                DEVICE_COMMERCIAL_50X25.with_overrides(
                    fields=DEVICE_COMMERCIAL_50X25.fields + (label_fields.spec(key),))

    def test_the_data_contract_refuses_a_cost_or_a_supplier(self):
        for key in ('cost', 'reference_cost', 'supplier', 'customer_email'):
            with self.assertRaises(LabelDataError, msg=key):
                AccessoryData.from_mapping({**accessory().as_mapping(), key: 'x'})
            with self.assertRaises(LabelDataError, msg=key):
                DeviceData.from_mapping({**device().as_mapping(), key: 'x'})


class ImeiFlagTest(SimpleTestCase):
    def test_the_imei_is_the_symbol_only_with_the_profile_and_the_flag(self):
        unit = device()
        allowed = labels.plan_device(unit, DEVICE_COMMERCIAL_50X25, SENSITIVE)
        self.assertEqual((allowed.symbol.kind, allowed.symbol.value), ('imei', unit.imei))
        self.assertTrue(allowed.sensitive)
        self.assertIn(f'({unit.imei})'.encode(), labels.render_pdf([allowed], compress=False))

        for options in (DeviceOptions(), DeviceOptions(profile='imei1', sensitive_allowed=False),
                        DeviceOptions(profile='internal', sensitive_allowed=True)):
            plan = labels.plan_device(unit, DEVICE_COMMERCIAL_50X25, options)
            self.assertEqual(plan.symbol.kind, 'label_code', options)
            self.assertFalse(plan.sensitive)
            pdf = labels.render_pdf([plan], compress=False)
            self.assertNotIn(unit.imei.encode(), pdf)
            self.assertNotIn(unit.serial.encode(), pdf)

    def test_without_the_flag_and_without_a_label_code_the_device_goes_to_review(self):
        plan = labels.plan_device(device(label_code=None), DEVICE_COMMERCIAL_50X25, DeviceOptions())
        self.assertEqual(plan.status, 'review')
        self.assertIsNone(plan.symbol)
        with self.assertRaises(labels.NothingToRender):
            labels.render_pdf([plan])

    def test_the_technical_label_needs_the_flag(self):
        with self.assertRaises(labels.SensitiveLabelRefused):
            labels.plan_device(device(), DEVICE_TECHNICAL_60X40, DeviceOptions())


class BarcodeWidthTest(SimpleTestCase):
    """The widths of ADR D5, at 0,25 mm per module and 10 modules of quiet zone a side."""

    def test_the_adr_widths(self):
        module = ACCESSORY_30X20.module_mm
        self.assertAlmostEqual(code128.symbol_width_mm('C000701', 'code128', module), 27.5, delta=0.1)
        self.assertAlmostEqual(code128.symbol_width_mm('123456789012', 'code128', module), 30.2, delta=0.1)
        self.assertAlmostEqual(code128.symbol_width_mm(IMEIS[0], 'code128', module), 38.5, delta=0.1)
        self.assertAlmostEqual(code128.symbol_width_mm('4006381333931', 'ean13', module), 28.3, delta=0.1)

    def test_an_over_long_code_is_flagged_not_shrunk(self):
        plan = labels.plan_accessory(
            accessory(codes=(LabelCode('123456789012', 'internal', True),)), ACCESSORY_30X20)
        self.assertEqual(plan.status, 'review')
        self.assertIsNone(plan.symbol)
        self.assertTrue(any('No cabe en 30×20' in r for r in plan.reasons), plan.reasons)

    def test_the_shortest_code_that_fits_is_used_and_said(self):
        plan = labels.plan_accessory(accessory(codes=(
            LabelCode('123456789012', 'internal', True), LabelCode('C000701', 'internal', False),
        )), ACCESSORY_30X20)
        self.assertEqual(plan.status, 'ready', plan.reasons)
        self.assertEqual(plan.symbol.value, 'C000701')
        self.assertGreaterEqual(plan.symbol.module_dots, ACCESSORY_30X20.min_module_dots)
        self.assertTrue(plan.warnings)

    def test_a_valid_ean13_is_drawn_in_its_own_symbology_and_an_internal_never_as_gtin(self):
        ean = labels.plan_accessory(accessory(codes=(LabelCode('4006381333931', 'ean13', True),)), ACCESSORY_30X20)
        self.assertEqual((ean.status, ean.symbol.symbology), ('ready', 'ean13'))
        internal = labels.plan_accessory(
            accessory(codes=(LabelCode('4006381333931', 'internal', True),)), ACCESSORY_30X20)
        self.assertEqual(internal.status, 'review')

    def test_the_module_never_drops_below_the_minimum(self):
        for value in ('C000701', 'C0007', 'AB'):
            plan = labels.plan_accessory(accessory(codes=(LabelCode(value, 'internal', True),)), ACCESSORY_30X20)
            self.assertGreaterEqual(plan.symbol.module_mm, code128.MIN_READABLE_MODULE_MM)
            self.assertEqual(plan.symbol.module_dots in (2, 3), True)

    def test_the_pattern_is_what_reportlab_encodes(self):
        bars = code128.pattern('C000701', 'code128')
        self.assertEqual(sum(bars), 90)                 # 110 modules minus 20 of quiet zone
        self.assertEqual(len(code128.pattern('4006381333931', 'ean13')), 59)
        self.assertEqual(sum(code128.pattern('4006381333931', 'ean13')), 95)


class NeverTruncatedTest(SimpleTestCase):
    def test_an_imei_that_does_not_fit_moves_to_the_larger_layout(self):
        narrow = DEVICE_COMMERCIAL_50X25.with_overrides(width_mm=36)
        plan = labels.plan_device(device(label_code=None), narrow, SENSITIVE,
                                  fallback=DEVICE_TECHNICAL_60X40)
        self.assertEqual(plan.status, 'ready', plan.reasons)
        self.assertEqual(plan.layout.key, DEVICE_TECHNICAL_60X40.key)
        self.assertTrue(any('60×40' in w for w in plan.warnings), plan.warnings)
        self.assertIn(f'({IMEIS[0]})'.encode(), labels.render_pdf([plan], compress=False))

    def test_without_a_larger_layout_it_goes_to_review_never_cut(self):
        narrow = DEVICE_COMMERCIAL_50X25.with_overrides(width_mm=36)
        plan = labels.plan_device(device(label_code=None), narrow, SENSITIVE)
        self.assertEqual(plan.status, 'review')

    def test_a_long_serial_on_the_technical_label_continues_on_a_second_sticker(self):
        serial = 'SERIEFICTICIA' * 3 + 'Z9'          # 41 characters
        plan = labels.plan_device(
            device(serial=serial, imei2=IMEI_2, grade='Muy bueno', condition_line='used',
                   condition_label='Seminuevo', battery_health=88),
            DEVICE_TECHNICAL_60X40.with_overrides(height_mm=22), SENSITIVE)
        self.assertEqual(plan.status, 'ready', plan.reasons)
        self.assertGreaterEqual(len(plan.stickers), 2)
        joined = ''.join(t.replace(' ', '') for s in plan.stickers for t in s.text_lines())
        self.assertIn(serial, joined)
        self.assertIn(IMEIS[0], joined)
        self.assertIn(IMEI_2, joined)
        pdf = labels.render_pdf([plan], compress=False)
        self.assertEqual(page_count(pdf), len(plan.stickers))

    def test_a_name_that_cannot_fit_is_review_not_ellipsis(self):
        plan = labels.plan_accessory(accessory(name='Adaptador ' * 12), ACCESSORY_30X20)
        self.assertEqual(plan.status, 'review')
        self.assertFalse(any('…' in t for s in plan.stickers for t in s.text_lines()))


class LogoTest(SimpleTestCase):
    def test_no_logo_and_a_broken_logo_still_render(self):
        for logo in (None, b''):
            plans = labels.plan_labels(accessories=[accessory()], logo_png=logo)
            self.assertEqual(plans[0].status, 'ready', plans[0].reasons)
            self.assertIsNone(plans[0].stickers[0].logo)
            self.assertEqual(plans[0].warnings, [])
            self.assertTrue(labels.render_pdf(plans).startswith(b'%PDF'))
        with self.assertLogs('store.labels.compose', 'WARNING'):
            broken = labels.plan_labels(accessories=[accessory()], logo_png=b'not an image')[0]
        self.assertEqual(broken.status, 'ready', broken.reasons)
        self.assertIsNone(broken.stickers[0].logo)
        self.assertTrue(any('logotipo' in w for w in broken.warnings))
        self.assertTrue(labels.render_pdf([broken]).startswith(b'%PDF'))

    def test_the_logo_is_one_bit_at_the_printer_resolution(self):
        for mode in ('RGBA', 'RGB'):
            plan = labels.plan_labels(accessories=[accessory()], logo_png=red_png(mode=mode))[0]
            logo = plan.stickers[0].logo
            self.assertIsNotNone(logo)
            self.assertEqual(logo.image.mode, '1')
            self.assertLessEqual(logo.width_mm, ACCESSORY_30X20.logo_max_w_mm + 0.01)
            self.assertAlmostEqual(logo.width_mm, logo.image.width * 25.4 / ACCESSORY_30X20.dpi, places=3)

    def test_the_package_has_no_logo_of_its_own(self):
        import pathlib
        package = pathlib.Path(labels.__file__).parent
        for path in package.glob('*'):
            self.assertNotIn(path.suffix.lower(), {'.png', '.jpg', '.jpeg', '.svg', '.webp'}, path)
            if path.suffix == '.py':
                text = path.read_text()
                self.assertNotIn('Black Dog', text)
                self.assertNotIn('from store.models', text)
                self.assertNotIn('from ..models', text)


class PriceTest(SimpleTestCase):
    def test_a_used_device_without_its_own_price_goes_to_review(self):
        plan = labels.plan_device(
            device(condition_line='used', condition_label='Seminuevo', battery_health=90, price=None),
            DEVICE_COMMERCIAL_50X25, SENSITIVE)
        self.assertEqual(plan.status, 'review')
        self.assertTrue(any('precio' in r for r in plan.reasons))

    def test_the_price_is_the_commercial_one_in_the_company_currency(self):
        plan = labels.plan_accessory(accessory(currency='$', price=Decimal('5')), ACCESSORY_30X20)
        self.assertIn('$ 5.00', [t for t in plan.stickers[0].text_lines()])


class UnprintableTextTest(SimpleTestCase):
    """The built-in fonts are WinAnsi: a character they cannot encode is review, never a wrong width."""

    def test_emoji_or_cjk_go_to_review(self):
        for name in ('Cargador 🔌 rápido', '充电器'):
            plan = labels.plan_accessory(accessory(name=name), ACCESSORY_30X20)
            self.assertEqual(plan.status, 'review', name)
            self.assertTrue(any('no se puede imprimir' in r for r in plan.reasons), plan.reasons)
        plan = labels.plan_device(device(attributes=(('Color', 'Azul 💙'),)), DEVICE_COMMERCIAL_50X25, SENSITIVE)
        self.assertEqual(plan.status, 'review')

    def test_spanish_text_is_printable(self):
        plan = labels.plan_accessory(accessory(name='Cámara ñandú «Pro» – 2 €'), ACCESSORY_30X20)
        self.assertEqual(plan.status, 'ready', plan.reasons)


#: ISO/IEC 15417 symbol table: value → bar/space widths (6 elements, 11 modules).
_C128 = (
    '212222 222122 222221 121223 121322 131222 122213 122312 132212 221213 221312 231212 112232 122132 '
    '122231 113222 123122 123221 223211 221132 221231 213212 223112 312131 311222 321122 321221 312212 '
    '322112 322211 212123 212321 232121 111323 131123 131321 112313 132113 132311 211313 231113 231311 '
    '112133 112331 132131 113123 113321 133121 313121 211331 231131 213113 213311 213131 311123 311321 '
    '331121 312113 312311 332111 314111 221411 431111 111224 111422 121124 121421 141122 141221 112214 '
    '112412 122114 122411 142112 142211 241211 221114 413111 241112 134111 111242 121142 121241 114212 '
    '124112 124211 411212 421112 421211 212141 214121 412121 111143 111341 131141 114113 114311 411113 '
    '411311 113141 114131 311141 411131 211412 211214 211232'
).split()
_STOP = '2331112'


def decode_code128(widths):
    """An independent decoder: the standard table, the checksum and the code sets."""
    widths = ''.join(str(w) for w in widths)
    assert widths.endswith(_STOP), 'sin parada'
    body = widths[:-len(_STOP)]
    assert len(body) % 6 == 0
    values = [_C128.index(body[i:i + 6]) for i in range(0, len(body), 6)]
    for value in values:
        assert sum(int(w) for w in _C128[value]) == 11
    start, data, check = values[0], values[1:-1], values[-1]
    assert (start + sum(v * (i + 1) for i, v in enumerate(data))) % 103 == check, 'suma de control'
    code_set = {103: 'A', 104: 'B', 105: 'C'}[start]
    out = []
    for value in data:
        if code_set == 'C':
            if value in (100, 101):
                code_set = 'B' if value == 100 else 'A'
                continue
            out.append(f'{value:02d}')
            continue
        if value == 99 or (code_set == 'B' and value == 101) or (code_set == 'A' and value == 100):
            code_set = {99: 'C', 100: 'B', 101: 'A'}[value]
            continue
        out.append(chr(value + 32) if code_set == 'B' or value < 64 else chr(value - 64))
    return ''.join(out)


class IndependentCode128DecodeTest(SimpleTestCase):
    def test_what_the_package_draws_decodes_to_what_it_was_asked(self):
        for value in ('C000701', '123456789012', IMEIS[0], 'U4827193056', 'abc-XYZ 9', '7', '00', 'A1B2C3'):
            bars = code128.pattern(value, 'code128')
            self.assertEqual(len(bars) % 6, 1, value)          # n symbols of 6 + the 7-element stop
            self.assertEqual(decode_code128(bars), value)


class LabelCodePrefixTest(SimpleTestCase):
    def test_a_four_letter_prefix_fits_the_phone_label(self):
        plan = labels.plan_device(device(label_code='ABCD4827193056'), DEVICE_COMMERCIAL_50X25, DeviceOptions())
        self.assertEqual(plan.status, 'ready', plan.reasons)
        self.assertEqual((plan.symbol.kind, plan.symbol.value), ('label_code', 'ABCD4827193056'))
        self.assertLessEqual(plan.symbol.width_mm, 46)
        self.assertIn(b'(ABCD4827193056)', labels.render_pdf([plan], compress=False))


class TechnicalFallbackGuardTest(SimpleTestCase):
    def test_without_the_sensitive_flag_a_fallback_never_turns_technical(self):
        narrow = DEVICE_COMMERCIAL_50X25.with_overrides(width_mm=36)
        for options in (DeviceOptions(), DeviceOptions(profile='imei1', sensitive_allowed=False)):
            plan = labels.plan_device(device(), narrow, options, fallback=DEVICE_TECHNICAL_60X40)
            self.assertEqual(plan.status, 'review', options)
            self.assertEqual(plan.layout.purpose, 'device_commercial')
            self.assertFalse(plan.sensitive)
