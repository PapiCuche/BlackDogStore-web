"""
SCAN-TO-STOCK-01 — the scan classifier (`store.scan_classifier`): what a scanned code IS, before anything is looked up.

A pure function. It never decides by keystroke timing, never names a manufacturer, and never guesses a model from an
IMEI. When it is not sure it says so (`certain=False`) and lists what the clerk may say the code is.

All identifiers here are synthetic (Luhn-valid where it matters); none is a real device's.
"""
from django.test import SimpleTestCase

from store import scan_classifier as sc
from store.test_stock_units import imei

IMEI_1 = imei('35000000000001')
IMEI_2 = imei('35000000000002')


def gtin(body: str) -> str:
    """`body` + its GS1 check digit (UPC-A, EAN-13, EAN-8, GTIN-14)."""
    return body + str(sc.gs1_check_digit(body))


def bad_luhn(value: str) -> str:
    return value[:-1] + str((int(value[-1]) + 1) % 10)


class ImeiTest(SimpleTestCase):
    def test_fifteen_digits_with_their_luhn_digit_are_an_imei_for_certain(self):
        reading = sc.classify(IMEI_1)
        self.assertEqual((reading.kind, reading.value, reading.certain), ('imei', IMEI_1, True))
        self.assertEqual(reading.problem, '')

    def test_a_typed_imei_with_spaces_or_dashes_is_the_same_imei(self):
        spaced = f'{IMEI_1[:2]} {IMEI_1[2:8]} {IMEI_1[8:14]}-{IMEI_1[14]}'
        self.assertEqual(sc.classify(spaced).value, IMEI_1)

    def test_fifteen_digits_that_fail_luhn_are_never_taken_as_an_imei(self):
        reading = sc.classify(bad_luhn(IMEI_1))
        self.assertFalse(reading.certain)
        self.assertEqual(reading.problem, 'imei_check_digit')
        self.assertNotIn('imei', reading.alternatives)
        self.assertIn('serial', reading.alternatives)
        self.assertIn('dígito de control', reading.detail)

    def test_all_the_same_digit_is_not_an_imei(self):
        reading = sc.classify('000000000000000')
        self.assertNotEqual((reading.kind, reading.certain), ('imei', True))


class NotAnImeiTest(SimpleTestCase):
    def test_an_eid_of_32_digits_is_an_eid_never_an_imei(self):
        reading = sc.classify('8904' + '0' * 12 + '1234567890123456')
        self.assertEqual((reading.kind, reading.certain), ('eid', True))
        self.assertEqual(reading.alternatives, ())
        self.assertIn('EID', reading.detail)

    def test_a_hex_meid_is_a_meid_never_an_imei(self):
        reading = sc.classify('A0000012345678')
        self.assertEqual(reading.kind, 'meid')
        self.assertNotIn('imei', reading.alternatives)

    def test_fourteen_digits_are_a_meid_never_an_imei_even_when_they_are_an_imei_without_its_last_digit(self):
        reading = sc.classify(IMEI_1[:14])
        self.assertEqual(reading.kind, 'meid')
        self.assertFalse(reading.certain)
        self.assertNotIn('imei', reading.alternatives)

    def test_an_eighteen_digit_decimal_meid_is_a_meid(self):
        self.assertEqual(sc.classify('270113177612345678').kind, 'meid')

    def test_sixteen_digits_are_an_imeisv_not_an_imei(self):
        reading = sc.classify(IMEI_1[:14] + '01')
        self.assertEqual(reading.kind, 'imeisv')
        self.assertNotIn('imei', reading.alternatives)


class CommercialCodeTest(SimpleTestCase):
    def test_a_upc_a_with_its_check_digit_is_a_commercial_code(self):
        code = gtin('19425300000')
        reading = sc.classify(code)
        self.assertEqual((reading.kind, reading.value, reading.certain), ('gtin', code, True))
        self.assertIn('variante', reading.detail)

    def test_an_ean_13_is_a_commercial_code(self):
        self.assertEqual(sc.classify(gtin('775000000001')).kind, 'gtin')

    def test_twelve_digits_without_a_valid_check_digit_are_not_a_commercial_code(self):
        code = gtin('19425300000')
        wrong = code[:-1] + str((int(code[-1]) + 1) % 10)
        reading = sc.classify(wrong)
        self.assertNotEqual(reading.kind, 'gtin')
        self.assertFalse(reading.certain)

    def test_an_ean_8_is_not_certain(self):
        reading = sc.classify(gtin('7750000'))
        self.assertEqual(reading.kind, 'gtin')
        self.assertFalse(reading.certain)
        self.assertIn('serial', reading.alternatives)


class SerialAndLabelTest(SimpleTestCase):
    def test_letters_and_digits_are_a_serial(self):
        reading = sc.classify('qz7k2m9xw1')
        self.assertEqual((reading.kind, reading.value, reading.certain), ('serial', 'QZ7K2M9XW1', True))

    def test_a_placeholder_is_not_a_serial(self):
        reading = sc.classify('N/A')
        self.assertEqual(reading.kind, 'unknown')
        self.assertEqual(reading.problem, 'placeholder')

    def test_a_numeric_code_of_another_length_is_only_maybe_a_serial(self):
        reading = sc.classify('1234567890')
        self.assertEqual(reading.kind, 'serial')
        self.assertFalse(reading.certain)

    def test_an_internal_label_code_is_recognised(self):
        from store.unit_codes import luhn_check_digit
        body = '482719305'
        code = f'U{body}{luhn_check_digit(body)}'
        reading = sc.classify(code.lower())
        self.assertEqual((reading.kind, reading.value), ('label_code', code))

    def test_a_url_or_text_is_unknown(self):
        for text in ('https://example.test/x?y=1', 'hola mundo', '', '   ', 'A' * 80):
            with self.subTest(text=text):
                self.assertEqual(sc.classify(text).kind, 'unknown')


class PrintedLabelTest(SimpleTestCase):
    """A QR or a typed text sometimes says what the number is: «IMEI2: …», «EID: …», «S/N: …»."""

    def test_the_printed_name_decides_the_slot(self):
        reading = sc.classify(f'IMEI2: {IMEI_2}')
        self.assertEqual((reading.kind, reading.value, reading.hint), ('imei', IMEI_2, 'imei2'))
        self.assertTrue(reading.certain)

    def test_a_named_serial_is_a_serial_even_when_numeric(self):
        reading = sc.classify('S/N: 1234567890')
        self.assertEqual((reading.kind, reading.value, reading.certain), ('serial', '1234567890', True))

    def test_a_named_imei_that_fails_luhn_is_still_refused(self):
        reading = sc.classify(f'IMEI: {bad_luhn(IMEI_1)}')
        self.assertEqual(reading.problem, 'imei_check_digit')

    def test_a_named_eid_is_an_eid(self):
        self.assertEqual(sc.classify('EID 8904' + '0' * 28).kind, 'eid')


class AsIdentifierTest(SimpleTestCase):
    """What the clerk says a doubtful code is, checked by the same rules the reception uses."""

    def test_the_clerk_may_call_a_numeric_code_a_serial(self):
        self.assertEqual(sc.as_identifier('1234567890', 'serial'), '1234567890')

    def test_the_clerk_cannot_turn_a_bad_check_digit_into_an_imei(self):
        with self.assertRaises(sc.ScanError):
            sc.as_identifier(bad_luhn(IMEI_1), 'imei')

    def test_an_eid_cannot_be_called_an_imei(self):
        with self.assertRaises(sc.ScanError):
            sc.as_identifier('8904' + '0' * 28, 'imei')

    def test_an_unknown_choice_is_refused(self):
        with self.assertRaises(sc.ScanError):
            sc.as_identifier('QZ7K2M9XW1', 'model')


class NoManufacturerTest(SimpleTestCase):
    def test_the_engine_names_no_brand(self):
        import inspect
        source = inspect.getsource(sc).lower()
        for brand in ('apple', 'iphone', 'samsung', 'xiaomi', 'motorola'):
            self.assertNotIn(brand, source)
