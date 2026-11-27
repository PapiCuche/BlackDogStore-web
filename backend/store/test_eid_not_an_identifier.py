"""
D-SCAN-3 (owner, 2026-11-27) — an EID is never stored as an identifier of a device.

The EID (GSMA, 32 digits) names the eSIM chip, not the device. The owner decided it is never written as an IMEI and,
since nothing stores it today, it stays stored nowhere (docs/adr-scan-to-stock.md). The scan door already refused it
(`scan_intake.refuse_serial_that_is_not_one`); every other door shares `device_identity.clean_serial` /
`clean_imei`, so the rule lives there and holds on all of them:

  * «Registrar equipos», the bulk upload and «Recibir mercadería» — `stock_unit_services.receive_units` and its
    preview `clean_unit` (the upload calls it per row, the receipt per line);
  * «Llegó el equipo» of the till — `pos_device_services._device`;
  * a customer's device at the service desk — `device_identity.validate`.

An IMEI slot never took one (15 digits with a check digit); that is pinned here too, with the MEID shapes. A serial
that only looks like a MEID (14 hex characters, 18 digits) is still a serial outside the scan door: it is the shape
of real serials, and only the scan door can ask the person what it is.
"""
from django.test import SimpleTestCase

from store import device_identity as identity
from store import pos_device_services
from store import stock_unit_services as units_service
from store.models import StockMovement, StockUnit
from store.test_unit_codes import IMEI_A, Fixture

EID = '89049032007008882600154788932714'
EID_GROUPED = '8904 9032 0070 0888 2600 1547 8893 2714'


class CleanSerialTest(SimpleTestCase):
    def test_an_eid_is_never_a_serial_whatever_its_grouping(self):
        for value in (EID, EID_GROUPED, f'  {EID}\t', '8904-9032-0070-0888-2600-1547-8893-2714'):
            with self.subTest(value=value), self.assertRaises(identity.DeviceIdentityError) as refused:
                identity.clean_serial(value)
            self.assertIn('EID', str(refused.exception))

    def test_an_eid_is_never_an_imei_either(self):
        for value in (EID, EID_GROUPED):
            with self.subTest(value=value), self.assertRaises(identity.DeviceIdentityError):
                identity.clean_imei(value)

    def test_other_long_numbers_and_meid_shapes_stay_serials(self):
        # Only the EID's own shape (exactly 32 digits) is refused: a serial is not judged by a guess.
        for value in (EID[:31], EID + '1', 'A0000012345678', '270113177612345678', 'F2LXK1ABJG5J'):
            with self.subTest(value=value):
                self.assertEqual(identity.clean_serial(value), value)

    def test_the_service_desk_refuses_it_on_the_serial_field(self):
        with self.assertRaises(identity.DeviceIdentityError) as refused:
            identity.validate(device_type='phone', serial_number=EID, imei=IMEI_A)
        self.assertEqual(list(refused.exception.errors), ['serial_number'])


class TillQuickAddTest(SimpleTestCase):
    """«Llegó el equipo» (`pos_device_services._device`): the field says what is wrong, nothing is guessed."""

    def test_an_eid_in_the_serial_is_refused_on_that_field(self):
        with self.assertRaises(pos_device_services.QuickSaleError) as refused:
            pos_device_services._device({'imei': IMEI_A, 'serial_number': EID_GROUPED})
        self.assertEqual(refused.exception.field, 'serial_number')
        self.assertIn('EID', str(refused.exception))

    def test_an_eid_or_a_meid_in_an_imei_slot_is_refused(self):
        for field in ('imei', 'imei2'):
            for value in (EID, 'A0000012345678', '270113177612345678'):
                body = {'imei': IMEI_A, field: value} if field == 'imei2' else {field: value}
                with self.subTest(field=field, value=value), \
                        self.assertRaises(pos_device_services.QuickSaleError) as refused:
                    pos_device_services._device(body)
                self.assertEqual(refused.exception.field, field)


class ReceiveUnitsTest(Fixture):
    """The one door every stock device enters through, and the preview of the upload and the receipt."""

    def test_an_eid_as_serial_is_refused_and_nothing_enters(self):
        phone = self.product('Teléfono', serialized=True)
        with self.assertRaises(units_service.StockUnitError) as refused:
            self.receive(phone, {'serial_number': EID, 'imei': IMEI_A})
        self.assertEqual(refused.exception.field, 'serial_number')
        self.assertIn('EID', str(refused.exception))
        self.assertFalse(StockUnit.objects.filter(company=self.company).exists())
        self.assertFalse(StockMovement.objects.filter(product=phone).exists())

    def test_the_upload_and_receipt_preview_refuse_it_too(self):
        laptop = self.product('Portátil', serialized=True, requires_imei=False)
        with self.assertRaises(units_service.StockUnitError) as refused:
            units_service.clean_unit(laptop, {'serial_number': EID_GROUPED})
        self.assertEqual(refused.exception.field, 'serial_number')

    def test_an_eid_in_an_imei_field_is_refused(self):
        phone = self.product('Teléfono', serialized=True)
        for row, field in (({'imei': EID}, 'imei'), ({'imei': IMEI_A, 'imei2': EID}, 'imei2')):
            with self.subTest(field=field), self.assertRaises(units_service.StockUnitError) as refused:
                units_service.clean_unit(phone, row)
            self.assertEqual(refused.exception.field, field)
