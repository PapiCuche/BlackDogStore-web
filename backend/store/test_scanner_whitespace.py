"""
D-SCAN-4 (owner, 2026-11-27) — accidental scanner whitespace is cleaned, identifiers are never altered.

A keyboard-wedge scanner, a paste from a spreadsheet or a chat may leave spaces, tabs, CR, LF or a NBSP (U+00A0) at
the EDGES of a code. Every door that takes an identifier trims them. INSIDE the code nothing changes beyond the rules
that already were (`device_identity`): an IMEI keeps only its digits (it is dictated in groups), a serial loses its
inner whitespace and is upper-cased; its dashes, dots and slashes stay.

Doors pinned here: the scan intake (`scan_classifier`), the till and every lookup of a device (`find_unit`, used by
the sale, SERIAL-PICK, transfers, returns and counts), the product barcode lookup (`normalize_barcode`), «Llegó el
equipo» (`pos_device_services._device`), «Registrar equipos» and the bulk upload (`clean_unit`, `normalize_scalar`
for a cell), «Recibir mercadería» (`receipt_services._unit`) and the service desk (`device_identity.validate`).
"""
from django.test import SimpleTestCase

from store import device_identity, pos_device_services, receipt_services, scan_classifier
from store import stock_unit_services as units_service
from store.models import normalize_barcode
from store.test_unit_codes import IMEI_A, Fixture
from store.unit_identification import find_unit
from store.xlsx_reader import normalize_scalar

NBSP = ' '
#: What a scanner or a paste may leave around a code.
EDGES = ((' ', ' '), ('\t', '\r\n'), (NBSP, NBSP), ('\r\n', '\t'), (f' {NBSP}\t', f'\r{NBSP} \n'))
SERIAL = 'F2L-XK1/AB.7'


def padded(value):
    return [f'{before}{value}{after}' for before, after in EDGES]


class PureDoorsTest(SimpleTestCase):
    def test_the_scan_classifier_reads_the_code_without_its_edges(self):
        for code in padded(IMEI_A):
            with self.subTest(code=code):
                reading = scan_classifier.classify(code, expected='imei')
                self.assertEqual((reading.kind, reading.value, reading.certain), ('imei', IMEI_A, True))
                self.assertEqual(scan_classifier.as_identifier(code, 'imei'), IMEI_A)
        for code in padded(SERIAL.lower()):
            with self.subTest(code=code):
                self.assertEqual(scan_classifier.classify(code).value, SERIAL)
                self.assertEqual(scan_classifier.as_identifier(code, 'serial'), SERIAL)

    def test_the_identity_rules_trim_the_edges_and_keep_dashes_dots_and_slashes(self):
        for code in padded(IMEI_A):
            with self.subTest(code=code):
                self.assertEqual(device_identity.clean_imei(code), IMEI_A)
        for code in padded(SERIAL):
            with self.subTest(code=code):
                self.assertEqual(device_identity.clean_serial(code), SERIAL)
        cleaned = device_identity.validate(device_type='phone', serial_number=f'{NBSP}{SERIAL}\r\n',
                                           imei=f'\t{IMEI_A}{NBSP}')
        self.assertEqual((cleaned['serial_number'], cleaned['imei']), (SERIAL, IMEI_A))

    def test_the_till_quick_add_trims_the_edges(self):
        for imei, serial in zip(padded(IMEI_A), padded(SERIAL)):
            with self.subTest(imei=imei, serial=serial):
                row = pos_device_services._device({'imei': imei, 'serial_number': serial})
                self.assertEqual((row['imei'], row['serial_number']), (IMEI_A, SERIAL))

    def test_a_receipt_line_trims_the_edges(self):
        for imei, serial in zip(padded(IMEI_A), padded(SERIAL)):
            with self.subTest(imei=imei, serial=serial):
                row = receipt_services._unit({'serial': serial, 'imei': imei}, 'lines[0].units[0]',
                                             may_view_costs=False)
                self.assertEqual((row['serial_number'], row['imei']), (SERIAL, IMEI_A))

    def test_an_upload_cell_and_a_product_barcode_lose_only_their_edges(self):
        for code in padded(IMEI_A):
            with self.subTest(code=code):
                self.assertEqual(normalize_scalar(code), IMEI_A)
        for code in padded('0012345-ab'):
            with self.subTest(code=code):
                self.assertEqual(normalize_barcode(code), '0012345-ab')


class DeviceDoorsTest(Fixture):
    def test_registering_a_device_trims_the_edges(self):
        phone = self.product('Teléfono', serialized=True)
        cleaned = units_service.clean_unit(phone, {'serial_number': f'{NBSP}{SERIAL}\t', 'imei': f'\r\n{IMEI_A}{NBSP}'})
        self.assertEqual((cleaned['serial_number'], cleaned['imei']), (SERIAL, IMEI_A))
        [unit] = self.receive(phone, {'serial_number': f'\t{SERIAL}\r\n', 'imei': f'{NBSP}{IMEI_A} '})
        self.assertEqual((unit.serial_number, unit.imei), (SERIAL, IMEI_A))

    def test_every_lookup_finds_the_device_whatever_the_scanner_left_around_the_code(self):
        phone = self.product('Teléfono', serialized=True)
        [unit] = self.receive(phone, {'serial_number': SERIAL, 'imei': IMEI_A})
        unit.refresh_from_db()
        self.assertTrue(unit.label_code)
        for code in (*padded(IMEI_A), *padded(SERIAL.lower()), *padded(unit.label_code.lower())):
            with self.subTest(code=code):
                self.assertEqual(find_unit(self.company, code).pk, unit.pk)
