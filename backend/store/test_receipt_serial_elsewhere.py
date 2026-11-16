"""
INV-SERIAL-01 — a serial number already registered for ANOTHER product of the company is WARNED in the reception.

The database keys a serial per product (`unique_stock_unit_serial_per_product`), so a device without an IMEI (a Mac,
a Wi-Fi iPad, AirPods) could enter twice under two variants. Whether that must be refused is the owner's decision;
until then the reception says so, without echoing the serial, and the person decides.
"""
from store import test_inventory_receipts as base
from store.models import StockUnit

unit = base.unit


class SerialElsewhereTest(base.ReceiptBase):
    def setUp(self):
        super().setUp()
        self.confirm(self.clerk, self.body(self.dev(self.laptop, unit('C02XYZ0001'))), expect=201)
        self.other_laptop = self.serialized('MacBook Recepción 2', 'macbook-recepcion-2', imei=False)

    def test_the_preview_warns_and_never_echoes_the_serial(self):
        res = self.preview(self.clerk, self.body(self.dev(self.other_laptop, unit('C02XYZ0001'))), expect=200)
        data = res.json()
        self.assertTrue(data['ok'], data)
        [line] = data['lines']
        self.assertTrue(any('MacBook Recepción' in w and 'serie' in w for w in line['warnings']), line['warnings'])
        self.assertNotIn('C02XYZ0001', res.content.decode())

    def test_the_same_serial_in_two_products_of_one_reception_warns(self):
        a = self.serialized('iPad Wi-Fi A', 'ipad-a', imei=False)
        b = self.serialized('iPad Wi-Fi B', 'ipad-b', imei=False)
        data = self.preview(self.clerk, self.body(self.dev(a, unit('DMPXYZ0009')), self.dev(b, unit('DMPXYZ0009'))),
                            expect=200).json()
        self.assertTrue(any('serie' in w for w in data['lines'][1]['warnings']), data['lines'])

    def test_it_is_only_a_warning_the_reception_can_be_confirmed(self):
        self.confirm(self.clerk, self.body(self.dev(self.other_laptop, unit('C02XYZ0001'))), expect=201)
        self.assertEqual(StockUnit.objects.filter(serial_number='C02XYZ0001').count(), 2)

    def test_another_company_with_the_same_serial_does_not_warn(self):
        foreign_laptop = self.serialized('Mac ajeno', 'mac-ajeno', imei=False, company=self.other)
        StockUnit.objects.create(company=self.other, product=foreign_laptop, branch=self.foreign_branch,
                                 serial_number='C02FOREIGN1')
        data = self.preview(self.clerk, self.body(self.dev(self.other_laptop, unit('C02FOREIGN1'))), expect=200).json()
        self.assertFalse(any('serie' in w for w in data['lines'][0]['warnings']), data['lines'][0]['warnings'])
