"""
DEVICE-IDENTITY — qué identifica a un equipo, y cuándo es obligatorio.

Un equipo se reconoce por su número de serie y, si es celular, por su IMEI. La
regla NO es «IMEI obligatorio»: una laptop o una tablet Wi-Fi no tienen, y
obligarlo llenaría la base de «N/A» y de quinces inventados. Es por tipo:

  * serie    obligatoria en lo que normalmente la lleva (todo menos «otro»);
  * IMEI     obligatorio en un teléfono; opcional en lo demás;
  * IMEI 2   opcional, para doble SIM o eSIM.

Y hay una salida honesta para el equipo que llega sin poder leerse (no
enciende, etiqueta borrada): se dice POR QUÉ falta. Un campo vacío con motivo
es un dato; un «000000000000000» es una mentira que luego parece un duplicado.

Lo que no está es un marcador: «N/A», «no tiene» y un IMEI que no supera su
dígito de control se rechazan.
"""
from store import device_identity as identity
from store.models import AdminAuditLog, Device
from store.tests import M8ServiceBase as _Base, _m8_service, _m8_url, _v1_customer


def imei(body: str) -> str:
    """Un IMEI válido a partir de sus 14 primeros dígitos."""
    return body + identity.luhn_check_digit(body)


IMEI_A = imei('35693803564380')
IMEI_B = imei('49015420323751')
IMEI_C = imei('86753009876543')


class IdentityRulesTest(_Base):
    """Las reglas, sin HTTP."""

    def test_an_imei_is_fifteen_digits_with_a_valid_check_digit(self):
        self.assertEqual(identity.clean_imei(IMEI_A), IMEI_A)
        self.assertEqual(identity.clean_imei(f' {IMEI_A[:2]} {IMEI_A[2:8]}-{IMEI_A[8:]} '), IMEI_A)
        self.assertEqual(identity.clean_imei(''), '')
        self.assertEqual(identity.clean_imei(None), '')

    def test_what_is_not_an_imei_is_refused(self):
        wrong_check = IMEI_A[:-1] + str((int(IMEI_A[-1]) + 1) % 10)
        for value in (IMEI_A[:-1], IMEI_A + '1', 'ABCDEFGHIJKLMNO', wrong_check,
                      '000000000000000', '111111111111111', 'N/A', 'no tiene'):
            with self.subTest(value):
                with self.assertRaises(identity.DeviceIdentityError):
                    identity.clean_imei(value)

    def test_a_serial_is_normalised_and_placeholders_are_refused(self):
        self.assertEqual(identity.clean_serial('  c02xk1 abjg5j '), 'C02XK1ABJG5J')
        self.assertEqual(identity.clean_serial(''), '')
        for value in ('N/A', 'na', 'No tiene', 'sin serie', 's/n', '-', '0000000', 'xxx', 'AB'):
            with self.subTest(value):
                with self.assertRaises(identity.DeviceIdentityError):
                    identity.clean_serial(value)

    def test_identifiers_are_masked_for_the_customer(self):
        self.assertEqual(identity.mask(IMEI_A), '•' * 11 + IMEI_A[-4:])
        self.assertEqual(identity.mask('AB12'), '••12')
        self.assertEqual(identity.mask(''), '')

    def test_what_each_kind_of_device_needs(self):
        self.assertTrue(identity.requires_imei('phone'))
        for kind in ('tablet', 'laptop', 'desktop', 'console', 'wearable', 'other'):
            self.assertFalse(identity.requires_imei(kind), kind)
        for kind in ('phone', 'tablet', 'laptop', 'desktop', 'console', 'wearable'):
            self.assertTrue(identity.requires_serial(kind), kind)
        self.assertFalse(identity.requires_serial('other'))


class DeviceIntakeTest(_Base):
    def setUp(self):
        super().setUp()
        self.client = self.full_service_role()

    def _create(self, **fields):
        body = {'customer_id': self.customer.pk, 'device_type': 'phone',
                'brand': 'Genérica', 'model': 'X200'}
        body.update(fields)
        return self.client.post(_m8_url('m8-taller', 'devices/'), body, format='json')

    def test_a_phone_is_registered_with_serial_and_imei(self):
        res = self._create(serial_number=' f2lxk1abc9 ', imei=f'{IMEI_A[:8]} {IMEI_A[8:]}')

        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.json()['serial_number'], 'F2LXK1ABC9')
        self.assertEqual(res.json()['imei'], IMEI_A)
        self.assertEqual(res.json()['imei2'], '')

    def test_a_phone_without_imei_is_refused_and_says_which_field(self):
        res = self._create(serial_number='F2LXK1ABC9')
        self.assertEqual(res.status_code, 400)
        self.assertIn('imei', res.json())
        self.assertFalse(Device.objects.filter(model='X200').exists())

    def test_a_phone_that_cannot_be_read_is_registered_with_the_reason(self):
        res = self._create(identifiers_pending_reason='No enciende y no tiene bandeja SIM.')

        self.assertEqual(res.status_code, 201, res.content)
        device = Device.objects.get(pk=res.json()['id'])
        self.assertEqual((device.serial_number, device.imei), ('', ''))
        self.assertEqual(device.identifiers_pending_reason, 'No enciende y no tiene bandeja SIM.')
        row = AdminAuditLog.objects.filter(action='service_device_created').latest('pk')
        self.assertTrue(row.metadata['identifiers_pending'])

    def test_a_laptop_needs_a_serial_and_no_imei(self):
        self.assertEqual(self._create(device_type='laptop').status_code, 400)
        res = self._create(device_type='laptop', serial_number='C02XK1ABJG5J')
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.json()['imei'], '')

    def test_a_tablet_or_a_watch_may_or_may_not_have_an_imei(self):
        wifi = self._create(device_type='tablet', serial_number='DMPXK1ABC1', model='Tab Wi-Fi')
        cellular = self._create(device_type='tablet', serial_number='DMPXK1ABC2', imei=IMEI_B,
                                model='Tab Celular')
        watch = self._create(device_type='wearable', serial_number='FH7XK1ABC3', model='Reloj')
        self.assertEqual([wifi.status_code, cellular.status_code, watch.status_code], [201, 201, 201])
        self.assertEqual(cellular.json()['imei'], IMEI_B)

    def test_something_else_needs_no_identifier(self):
        self.assertEqual(self._create(device_type='other', model='Parlante').status_code, 201)

    def test_a_second_imei_is_optional_and_must_be_a_different_one(self):
        ok = self._create(serial_number='F2LXK1ABD1', imei=IMEI_A, imei2=IMEI_B)
        self.assertEqual(ok.status_code, 201, ok.content)
        self.assertEqual(ok.json()['imei2'], IMEI_B)

        same = self._create(serial_number='F2LXK1ABD2', imei=IMEI_C, imei2=IMEI_C, model='X300')
        self.assertEqual(same.status_code, 400)
        self.assertIn('imei2', same.json())

        alone = self._create(device_type='tablet', serial_number='F2LXK1ABD3', imei2=IMEI_C,
                             model='X400')
        self.assertEqual(alone.status_code, 400)
        self.assertIn('imei2', alone.json())

    def test_placeholders_and_bad_numbers_never_reach_the_table(self):
        for fields in ({'imei': '000000000000000'}, {'imei': 'N/A'}, {'imei': IMEI_A[:-1] + '0'},
                       {'imei': IMEI_A, 'serial_number': 'no tiene'}):
            with self.subTest(fields):
                res = self._create(serial_number=fields.pop('serial_number', 'F2LXK1ABE1'), **fields)
                self.assertEqual(res.status_code, 400, res.content)
        self.assertFalse(Device.objects.filter(model='X200').exists())

    def test_the_same_device_is_not_registered_twice_for_the_same_customer(self):
        first = self._create(serial_number='F2LXK1ABF1', imei=IMEI_A).json()

        again = self._create(serial_number='OTRASERIE1', imei=IMEI_A, model='Otro nombre')

        self.assertEqual(again.status_code, 409, again.content)
        self.assertEqual(again.json()['existing_device']['id'], first['id'])
        self.assertEqual(Device.objects.filter(imei=IMEI_A).count(), 1)

    def test_a_device_that_changed_hands_is_registered_with_a_warning(self):
        first = self._create(serial_number='F2LXK1ABG1', imei=IMEI_A).json()
        order = _m8_service.create_repair_order(
            company=self.company, branch=self.branch_a, customer=self.customer,
            device=Device.objects.get(pk=first['id']), reported_issue='Pantalla rota.',
            actor=self.staff,
        )

        res = self._create(customer_id=self.other_customer.pk, serial_number='F2LXK1ABG1', imei=IMEI_A)

        self.assertEqual(res.status_code, 201, res.content)
        [known] = res.json()['possible_duplicates']
        self.assertEqual(known['id'], first['id'])
        self.assertEqual(known['customer_name'], 'Ana Cliente')
        self.assertEqual(known['repair_orders_count'], 1)
        self.assertEqual(known['last_repair_order']['number'], order.number)


class DeviceLookupTest(_Base):
    """«Este equipo ya estuvo aquí», antes de registrarlo otra vez."""

    def setUp(self):
        super().setUp()
        self.client = self.full_service_role()
        self.known = Device.objects.create(
            company=self.company, customer=self.customer, device_type='phone',
            brand='Genérica', model='X200', serial_number='F2LXK1ABH1', imei=IMEI_A, imei2=IMEI_B,
        )
        self.order = self.make_order(device=self.known)
        # La otra empresa tiene un equipo con los MISMOS identificadores.
        Device.objects.create(
            company=self.other, customer=self.foreign_customer, device_type='phone',
            brand='Ajena', model='Z', serial_number='F2LXK1ABH1', imei=IMEI_A,
        )

    def _lookup(self, client=None, **query):
        return (client or self.client).get(_m8_url('m8-taller', 'devices/lookup/'), query)

    def test_history_from_a_branch_the_caller_cannot_see_is_not_counted_or_named(self):
        elsewhere = self.make_order(device=self.known, branch=self.branch_b)
        # Quien alcanza las dos sucursales ve las dos órdenes.
        [whole] = self._lookup(imei=IMEI_A).json()['results']
        self.assertEqual(whole['repair_orders_count'], 2)

        restricted = self.restrict_to_branch_a()

        [found] = self._lookup(client=restricted, imei=IMEI_A).json()['results']
        self.assertEqual(found['repair_orders_count'], 1)
        self.assertEqual(found['last_repair_order']['number'], self.order.number)

        detail = restricted.get(_m8_url('m8-taller', f'devices/{self.known.pk}/')).json()
        self.assertEqual([o['number'] for o in detail['repair_orders']], [self.order.number])
        self.assertNotIn(elsewhere.number, str(detail))

    def test_it_finds_the_device_by_any_of_its_identifiers(self):
        for query in ({'serial_number': 'f2lxk1abh1'}, {'imei': IMEI_A}, {'imei': IMEI_B},
                      {'imei2': IMEI_A}):
            with self.subTest(query):
                res = self._lookup(**query)
                self.assertEqual(res.status_code, 200, res.content)
                [found] = res.json()['results']
                self.assertEqual(found['id'], self.known.pk)
                self.assertEqual(found['customer_name'], 'Ana Cliente')
                self.assertEqual(found['repair_orders_count'], 1)
                self.assertEqual(found['last_repair_order']['number'], self.order.number)

    def test_it_never_shows_what_another_company_holds(self):
        body = self._lookup(imei=IMEI_A).content.decode()
        self.assertNotIn('Ajena', body)
        self.assertNotIn('Carla', body)
        self.assertEqual(len(self._lookup(imei=IMEI_A).json()['results']), 1)

    def test_nothing_to_look_for_finds_nothing(self):
        self.assertEqual(self._lookup().json()['results'], [])
        self.assertEqual(self._lookup(imei=IMEI_C).json()['results'], [])

    def test_it_needs_the_capability_to_see_devices(self):
        client = self.only_view_orders()
        self.assertEqual(self._lookup(client, imei=IMEI_A).status_code, 403)

    def only_view_orders(self):
        from store.models import MembershipRoleAssignment
        MembershipRoleAssignment.objects.filter(membership=self.membership).delete()
        return self.with_capabilities('service.orders.view', slug='solo-ordenes')

    def test_the_device_shows_every_time_it_has_been_here(self):
        second = self.make_order(device=self.known)

        res = self.client.get(_m8_url('m8-taller', f'devices/{self.known.pk}/'))

        self.assertEqual(res.status_code, 200)
        self.assertEqual(
            [row['number'] for row in res.json()['repair_orders']], [second.number, self.order.number],
        )

    def test_another_companys_device_is_not_found(self):
        foreign = Device.objects.get(company=self.other, imei=IMEI_A)
        self.assertEqual(self.client.get(_m8_url('m8-taller', f'devices/{foreign.pk}/')).status_code, 404)

    def test_a_device_of_another_customer_with_the_same_serial_does_not_block_reuse(self):
        other = _v1_customer(self.company, None, first_name='Dora', last_name='Tercera')
        res = self.client.post(_m8_url('m8-taller', 'devices/'), {
            'customer_id': other.pk, 'device_type': 'laptop', 'brand': 'Genérica',
            'model': 'L5', 'serial_number': 'F2LXK1ABH1',
        }, format='json')
        self.assertEqual(res.status_code, 201, res.content)
