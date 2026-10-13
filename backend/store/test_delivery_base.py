"""
DELIVERY-01, the base · carriers, a buyer's address book, and the delivery
record of an order.

ADDITIVE. `Order` keeps its own delivery columns, and the checkout, the prices,
the documents and the e-mails read them exactly as before. What is new sits
beside them:

  * A COMPANY'S CARRIERS — its own list, nothing seeded. Where a waybill can be
    followed is an https address with one placeholder and no other.
  * A BUYER'S ADDRESS BOOK in each shop: doors and agencies, who receives at
    each, one default. Another buyer's entry, or another shop's, does not exist.
  * THE DELIVERY RECORD OF AN ORDER, made with the order from the order's own
    columns and from then on where staff write the carrier, the agency, the
    freight, the waybill and where to follow it. A copy: it survives a renamed
    carrier and an edited address.

Freight is information. It is never part of an order's total.
"""
import json
from decimal import Decimal
from importlib import import_module
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.test import APIClient

from store import delivery_services as delivery
from store.models import AdminAuditLog, Carrier, Customer, CustomerAddress, Order, OrderDelivery, UserProfile
from store.tests import _order, _p2d_branch, _p2d_member, _pilot_company, _saas_company

User = get_user_model()

PASSWORD = 'Clave-larga-de-prueba-9'
CARRIERS = '/api/admin/carriers/'
VIEW, MANAGE, SETTINGS = 'sales.orders.view', 'sales.orders.manage', 'settings.manage'
DNI = '70000001'


def carrier_url(pk):
    return f'{CARRIERS}{pk}/'


def acting(user):
    cache.clear()
    client = APIClient()
    client.force_authenticate(user)
    return client


# =============================================================================
# 1 · a company's carriers
# =============================================================================

class TrackingTemplateRulesTest(SimpleTestCase):
    """Where a waybill is followed: https, one placeholder, nothing a browser would run."""

    def test_what_is_accepted(self):
        for typed, kept in (
            ('', ''), ('   ', ''),
            ('https://rastreo.ejemplo.test/guia/{guia}', 'https://rastreo.ejemplo.test/guia/{guia}'),
            (' https://rastreo.ejemplo.test/?n={guia}&x=1 ', 'https://rastreo.ejemplo.test/?n={guia}&x=1'),
            ('https://rastreo.ejemplo.test/consulta', 'https://rastreo.ejemplo.test/consulta'),      # a page to type it in
        ):
            with self.subTest(typed):
                self.assertEqual(delivery.clean_tracking_template(typed), kept)

    def test_what_is_refused(self):
        for typed in (
            'http://rastreo.ejemplo.test/{guia}',                 # not https
            'javascript:alert(1)//{guia}', 'JavaScript:alert(1)', 'data:text/html,{guia}',
            '//rastreo.ejemplo.test/{guia}', 'rastreo.ejemplo.test/{guia}', 'https:///{guia}',
            'https://{guia}.ejemplo.test/',                       # the waybill would choose the host
            'https://rastreo.ejemplo.test/{numero}',              # a placeholder nobody fills
            'https://rastreo.ejemplo.test/{guia}/{order}', 'https://rastreo.ejemplo.test/{', 'https://rastreo.ejemplo.test/}{guia}',
            'https://rastreo.ejemplo.test/ {guia}', 'https://rastreo.ejemplo.test/\n{guia}',
            'https://usuario:clave@rastreo.ejemplo.test/{guia}',
            'https://rastreo.ejemplo.test/' + 'x' * 300,
            None, 7,
        ):
            with self.subTest(typed), self.assertRaises(delivery.DeliveryError):
                delivery.clean_tracking_template(typed)

    def test_the_waybill_is_written_into_it_encoded(self):
        carrier = Carrier(name='Agencia', kind='agency', tracking_url_template='https://rastreo.ejemplo.test/g/{guia}?v={guia}')
        self.assertEqual(delivery.tracking_url_for(carrier, 'AB 12/34?x=1&y#z'),
                         'https://rastreo.ejemplo.test/g/AB%2012%2F34%3Fx%3D1%26y%23z?v=AB%2012%2F34%3Fx%3D1%26y%23z')
        self.assertEqual(delivery.tracking_url_for(carrier, '../../otra'), 'https://rastreo.ejemplo.test/g/..%2F..%2Fotra?v=..%2F..%2Fotra')
        for nothing in ('', '   ', None):
            self.assertEqual(delivery.tracking_url_for(carrier, nothing), '')
        self.assertEqual(delivery.tracking_url_for(Carrier(name='Sin plantilla', kind='courier'), 'AB12'), '')
        self.assertEqual(delivery.tracking_url_for(None, 'AB12'), '')

    def test_a_typed_tracking_address_is_https_or_nothing(self):
        self.assertEqual(delivery.clean_tracking_url(' https://rastreo.ejemplo.test/AB12 '), 'https://rastreo.ejemplo.test/AB12')
        self.assertEqual(delivery.clean_tracking_url(''), '')
        for typed in ('http://rastreo.ejemplo.test/AB12', 'javascript:alert(1)', 'rastreo.ejemplo.test', 'https://', 'https://a b.test/',
                      'https://usuario@rastreo.ejemplo.test/', 'https://rastreo.ejemplo.test/' + 'x' * 500, None):
            with self.subTest(typed), self.assertRaises(delivery.DeliveryError):
                delivery.clean_tracking_url(typed)


class _CompaniesBase(TestCase):
    def setUp(self):
        cache.clear()
        self.a = _saas_company('Tienda A', 'envios-a', tax_id='20999999990')
        self.b = _saas_company('Tienda B', 'envios-b', tax_id='20111111112')
        self.north = _p2d_branch(self.a, 'Norte')
        self.south = _p2d_branch(self.a, 'Sur')
        self.admin, _ = _p2d_member(self.a, 'admin_a', ['company.view', VIEW, MANAGE, SETTINGS])
        self.manager, _ = _p2d_member(self.a, 'gestora', ['company.view', VIEW, MANAGE])
        self.viewer, _ = _p2d_member(self.a, 'solo_ver', ['company.view', VIEW])
        self.outsider, _ = _p2d_member(self.a, 'sin_ventas', ['company.view'])
        self.northerner, _ = _p2d_member(self.a, 'solo_norte', ['company.view', VIEW, MANAGE, SETTINGS], branches=[self.north])
        self.admin_b, _ = _p2d_member(self.b, 'admin_b', ['company.view', VIEW, MANAGE, SETTINGS])

    def carrier(self, company=None, name='Agencia Uno', kind='agency', **extra):
        return Carrier.objects.create(company=company or self.a, name=name, kind=kind, **extra)


class CarrierApiTest(_CompaniesBase):
    def test_nothing_is_seeded(self):
        self.assertEqual(Carrier.objects.count(), 0)
        body = acting(self.viewer).get(CARRIERS).json()
        self.assertEqual(body, {'results': [], 'can_manage': False})

    def test_whoever_configures_the_company_adds_one(self):
        response = acting(self.admin).post(CARRIERS, {
            'name': '  Agencia Uno ', 'kind': 'agency', 'tracking_url_template': 'https://rastreo.ejemplo.test/{guia}',
        }, format='json')
        self.assertEqual(response.status_code, 201, response.content)
        carrier = Carrier.objects.get()
        self.assertEqual((carrier.company, carrier.name, carrier.kind, carrier.is_active), (self.a, 'Agencia Uno', 'agency', True))
        self.assertEqual(response.json(), {
            'id': carrier.pk, 'name': 'Agencia Uno', 'kind': 'agency', 'kind_label': 'Recojo en agencia',
            'tracking_url_template': 'https://rastreo.ejemplo.test/{guia}', 'is_active': True,
        })
        entry = AdminAuditLog.objects.get(action='carrier_created')
        self.assertEqual((entry.actor, entry.company, entry.target_type, entry.target_id), (self.admin, self.a, 'carrier', str(carrier.pk)))

        listed = acting(self.viewer).get(CARRIERS).json()
        self.assertEqual([row['name'] for row in listed['results']], ['Agencia Uno'])
        self.assertTrue(acting(self.admin).get(CARRIERS).json()['can_manage'])

    def test_one_name_per_company_whatever_the_case(self):
        self.carrier(name='Agencia Uno')
        again = acting(self.admin).post(CARRIERS, {'name': 'AGENCIA UNO', 'kind': 'courier'}, format='json')
        self.assertEqual(again.status_code, 400)
        self.assertIn('name', again.json())
        self.assertEqual(Carrier.objects.filter(company=self.a).count(), 1)
        # Another company's list is its own.
        theirs = acting(self.admin_b).post(CARRIERS, {'name': 'Agencia Uno', 'kind': 'agency'}, format='json')
        self.assertEqual(theirs.status_code, 201, theirs.content)
        self.assertEqual(Carrier.objects.get(company=self.b).name, 'Agencia Uno')

    def test_what_is_not_a_carrier_is_refused(self):
        for body, field in (
            ({'kind': 'agency'}, 'name'), ({'name': '  ', 'kind': 'agency'}, 'name'), ({'name': 'x' * 81, 'kind': 'agency'}, 'name'),
            ({'name': 'Uno'}, 'kind'), ({'name': 'Uno', 'kind': 'dron'}, 'kind'),
            ({'name': 'Uno\x00', 'kind': 'agency'}, 'name'),
            ({'name': 'Uno', 'kind': 'agency', 'tracking_url_template': 'http://rastreo.ejemplo.test/{guia}'}, 'tracking_url_template'),
            ({'name': 'Uno', 'kind': 'agency', 'tracking_url_template': 'javascript:alert(1)'}, 'tracking_url_template'),
            ({'name': 'Uno', 'kind': 'agency', 'tracking_url_template': 'https://rastreo.ejemplo.test/{otro}'}, 'tracking_url_template'),
        ):
            with self.subTest(body):
                response = acting(self.admin).post(CARRIERS, body, format='json')
                self.assertEqual(response.status_code, 400, response.content)
                self.assertIn(field, response.json())
        self.assertEqual(Carrier.objects.count(), 0)

    def test_it_is_changed_and_switched_off_never_deleted(self):
        carrier = self.carrier()
        response = acting(self.admin).patch(carrier_url(carrier.pk), {'name': 'Agencia Dos', 'is_active': False}, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        carrier.refresh_from_db()
        self.assertEqual((carrier.name, carrier.is_active, carrier.kind), ('Agencia Dos', False, 'agency'))
        entry = AdminAuditLog.objects.get(action='carrier_updated')
        self.assertEqual(entry.metadata, {'changed_fields': ['is_active', 'name']})
        self.assertEqual(entry.company, self.a)

        self.assertEqual(acting(self.admin).delete(carrier_url(carrier.pk)).status_code, 405)
        self.assertTrue(Carrier.objects.filter(pk=carrier.pk).exists())
        # Switched off, it is still listed for whoever keeps the list.
        self.assertEqual([row['is_active'] for row in acting(self.admin).get(CARRIERS).json()['results']], [False])

    def test_a_change_is_judged_like_a_new_one(self):
        carrier, other = self.carrier(), self.carrier(name='Agencia Dos')
        for body in ({'name': 'agencia dos'}, {'name': ''}, {'kind': 'dron'}, {'tracking_url_template': 'http://x.test/{guia}'}):
            with self.subTest(body):
                self.assertEqual(acting(self.admin).patch(carrier_url(carrier.pk), body, format='json').status_code, 400)
        carrier.refresh_from_db()
        self.assertEqual((carrier.name, carrier.kind, carrier.tracking_url_template), ('Agencia Uno', 'agency', ''))
        # Its own name, written another way, is not a clash with itself.
        self.assertEqual(acting(self.admin).patch(carrier_url(carrier.pk), {'name': 'AGENCIA UNO'}, format='json').status_code, 200)
        self.assertFalse(AdminAuditLog.objects.filter(action='carrier_updated', target_id=str(other.pk)).exists())

    def test_a_save_that_changes_nothing_is_not_written_down(self):
        carrier = self.carrier()
        acting(self.admin).patch(carrier_url(carrier.pk), {'name': 'Agencia Uno', 'kind': 'agency'}, format='json')
        self.assertFalse(AdminAuditLog.objects.filter(action='carrier_updated').exists())

    def test_reading_takes_seeing_orders_and_writing_takes_configuring(self):
        carrier = self.carrier()
        self.assertEqual(acting(self.outsider).get(CARRIERS).status_code, 403)
        self.assertEqual(APIClient().get(CARRIERS).status_code, 401)
        for user in (self.viewer, self.manager, self.outsider):
            with self.subTest(user.username):
                self.assertEqual(acting(user).post(CARRIERS, {'name': 'Otra', 'kind': 'agency'}, format='json').status_code, 403)
                self.assertEqual(acting(user).patch(carrier_url(carrier.pk), {'is_active': False}, format='json').status_code, 403)
        self.assertEqual(Carrier.objects.count(), 1)
        self.assertTrue(Carrier.objects.get().is_active)

    def test_a_list_for_the_whole_company_is_not_changed_from_one_branch(self):
        carrier = self.carrier()
        self.assertEqual(acting(self.northerner).get(CARRIERS).status_code, 200)
        self.assertFalse(acting(self.northerner).get(CARRIERS).json()['can_manage'])
        self.assertEqual(acting(self.northerner).post(CARRIERS, {'name': 'Otra', 'kind': 'agency'}, format='json').status_code, 403)
        self.assertEqual(acting(self.northerner).patch(carrier_url(carrier.pk), {'is_active': False}, format='json').status_code, 403)

    def test_another_companys_carriers_do_not_exist(self):
        theirs = self.carrier(self.b, name='De la otra')
        self.assertEqual(acting(self.admin).get(CARRIERS).json()['results'], [])
        self.assertEqual(acting(self.admin).patch(carrier_url(theirs.pk), {'is_active': False}, format='json').status_code, 404)
        self.assertEqual(acting(self.admin).get(CARRIERS + f'?company={self.b.pk}').status_code, 403)
        theirs.refresh_from_db()
        self.assertTrue(theirs.is_active)
        # A company named in the body is not read.
        acting(self.admin).post(CARRIERS, {'name': 'Mía', 'kind': 'agency', 'company': self.b.pk}, format='json')
        self.assertEqual(Carrier.objects.get(name='Mía').company, self.a)

    def test_the_pilots_operators_from_before_companies(self):
        pilot = _pilot_company()

        def legacy(username, role):
            user = User.objects.create_user(username, f'{username}@correo.test', PASSWORD)
            user.profile.role = role
            user.profile.save()
            return user

        sales, boss = legacy('ventas', UserProfile.ROLE_SALES), legacy('jefa', UserProfile.ROLE_ADMIN)
        self.assertEqual(acting(sales).get(CARRIERS).status_code, 200)
        self.assertEqual(acting(sales).post(CARRIERS, {'name': 'Uno', 'kind': 'agency'}, format='json').status_code, 403)
        self.assertEqual(acting(boss).post(CARRIERS, {'name': 'Uno', 'kind': 'agency'}, format='json').status_code, 201)
        self.assertEqual(Carrier.objects.get().company, pilot)


# =============================================================================
# 2 · a buyer's address book
# =============================================================================

from django.db import IntegrityError, transaction  # noqa: E402

from store.test_client_sec import v1_session, web_session  # noqa: E402

WEB_BOOK = '/api/account/addresses/'
ADDRESS_KEYS = {
    'id', 'label', 'kind', 'recipient_name', 'recipient_document_type', 'recipient_document_number',
    'recipient_phone', 'address_line', 'district', 'city', 'department', 'reference',
    'carrier', 'carrier_name', 'agency_name', 'agency_locality', 'is_default',
}


def app_book(slug, tail=''):
    return f'/api/v1/customer/{slug}/addresses/{tail}'


def door(**overrides):
    body = {
        'label': 'Casa', 'kind': 'address', 'recipient_name': 'Ana Torres', 'recipient_phone': '987654321',
        'address_line': 'Av. Siempre Viva 123', 'district': 'Cercado', 'city': 'Arequipa', 'department': 'Arequipa',
        'reference': 'Frente al parque',
    }
    body.update(overrides)
    return body


@override_settings(ALLOWED_HOSTS=['*'])
class _BookBase(_CompaniesBase):
    def setUp(self):
        super().setUp()
        self.ana = User.objects.create_user('ana', 'ana@correo.test', PASSWORD, first_name='Ana', last_name='Torres')
        self.luis = User.objects.create_user('luis', 'luis@correo.test', PASSWORD, first_name='Luis')
        self.web = web_session(self.ana)
        self.agency = self.carrier(name='Agencia Uno', kind='agency')

    def host(self, company=None):
        return {'HTTP_HOST': f'{(company or self.a).slug}.example.com'}

    def call(self, method, tail='', body=None, *, client=None, company=None):
        cache.clear()                                   # the write limit is its own test
        send = getattr(client or self.web, method)
        args = (WEB_BOOK + tail,) if body is None else (WEB_BOOK + tail, body)
        return send(*args, format='json', **self.host(company))

    def add(self, body=None, **kw):
        response = self.call('post', body=body or door(), **kw)
        self.assertEqual(response.status_code, 201, response.content)
        return CustomerAddress.objects.get(pk=response.json()['id'])

    def office(self, **overrides):
        body = {
            'label': 'Agencia', 'kind': 'agency', 'recipient_name': 'Rosa Quispe', 'recipient_document_type': 'dni',
            'recipient_document_number': DNI, 'recipient_phone': '912345678',
            'carrier': self.agency.pk, 'agency_name': 'Agencia Centro', 'agency_locality': 'Juliaca, Puno',
        }
        body.update(overrides)
        return body

    def book(self, **kw):
        return self.call('get', **kw).json()['results']


class AddressBookTest(_BookBase):
    def test_it_is_for_whoever_is_signed_in(self):
        guest = APIClient()
        for method, tail, body in (('get', '', None), ('post', '', door()), ('patch', '1/', {'label': 'x'}),
                                   ('delete', '1/', None), ('post', '1/default/', {})):
            with self.subTest(method=method, tail=tail):
                self.assertEqual(self.call(method, tail, body, client=guest).status_code, 401)
        self.assertEqual(CustomerAddress.objects.count(), 0)

    def test_an_empty_book_is_read_without_making_anybody_a_client(self):
        response = self.call('get')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json(), {
            'results': [], 'limit': delivery.MAX_ACTIVE_ADDRESSES,
            'agency_carriers': [{'id': self.agency.pk, 'name': 'Agencia Uno'}],
        })
        self.assertEqual(Customer.objects.count(), 0)
        self.assertIn('no-store', response['Cache-Control'])

    def test_the_first_entry_makes_the_buyers_record_with_the_accounts_name_only(self):
        response = self.call('post', body=door())
        self.assertEqual(response.status_code, 201, response.content)
        record = Customer.objects.get()
        self.assertEqual((record.company, record.user, record.first_name, record.last_name), (self.a, self.ana, 'Ana', 'Torres'))
        self.assertEqual((record.phone, record.document_number, record.address_line), ('', '', ''))

        entry = CustomerAddress.objects.get()
        self.assertEqual((entry.company, entry.customer, entry.kind, entry.is_active), (self.a, record, 'address', True))
        self.assertEqual((entry.recipient_name, entry.recipient_phone, entry.address_line, entry.district, entry.city),
                         ('Ana Torres', '987654321', 'Av. Siempre Viva 123', 'Cercado', 'Arequipa'))
        self.assertTrue(entry.is_default)                               # the only one is the default one
        body = response.json()
        self.assertEqual(set(body), ADDRESS_KEYS)
        self.assertEqual((body['id'], body['carrier'], body['carrier_name'], body['is_default']), (entry.pk, None, '', True))

    def test_an_account_without_a_name_is_asked_for_it_first(self):
        nameless = User.objects.create_user('sin_nombre', 'sin@correo.test', PASSWORD)
        response = self.call('post', body=door(), client=web_session(nameless))
        self.assertEqual(response.status_code, 400)
        self.assertIn('Mi cuenta', response.json()['detail'])
        self.assertEqual((Customer.objects.count(), CustomerAddress.objects.count()), (0, 0))

    def test_somebody_else_may_receive(self):
        entry = self.add(door(recipient_name='Rosa Quispe', recipient_document_type='dni',
                              recipient_document_number=DNI, recipient_phone='+51 912 345 678'))
        self.assertEqual((entry.recipient_name, entry.recipient_document_type, entry.recipient_document_number, entry.recipient_phone),
                         ('Rosa Quispe', 'dni', DNI, '+51912345678'))
        # The buyer's own record is not the recipient's.
        self.assertEqual(Customer.objects.get().document_number, '')

    def test_an_agency_entry_names_a_carrier_of_this_shop(self):
        entry = self.add(self.office())
        self.assertEqual((entry.kind, entry.carrier, entry.agency_name, entry.agency_locality),
                         ('agency', self.agency, 'Agencia Centro', 'Juliaca, Puno'))
        row = self.book()[0]
        self.assertEqual((row['carrier'], row['carrier_name']), (self.agency.pk, 'Agencia Uno'))

    def test_each_kind_asks_for_its_own_fields(self):
        courier = self.carrier(name='A domicilio', kind='courier')
        off = self.carrier(name='Apagada', kind='agency', is_active=False)
        theirs = self.carrier(self.b, name='De la otra', kind='agency')
        for body, field in (
            (door(address_line=''), 'address_line'), (door(district=' '), 'district'), (door(city=''), 'city'),
            (door(recipient_name=''), 'recipient_name'), (door(recipient_name='x' * 256), 'recipient_name'),
            (door(recipient_phone='12345'), 'recipient_phone'),
            (door(recipient_document_type='dni', recipient_document_number='123'), 'recipient_document_number'),
            (door(recipient_document_number=DNI), 'recipient_document_type'),
            (door(kind='casillero'), 'kind'), (door(label='x' * 61), 'label'),
            (door(address_line='Av. Uno\x00 1'), 'address_line'), (door(recipient_name='Ana\x00'), 'recipient_name'),
            (door(reference='línea\ndos'), 'reference'),
            (self.office(agency_name=''), 'agency_name'), (self.office(agency_locality=''), 'agency_locality'),
            (self.office(carrier=None), 'carrier'), (self.office(carrier=999999), 'carrier'),
            (self.office(carrier='uno'), 'carrier'),
            (self.office(carrier=courier.pk), 'carrier'), (self.office(carrier=off.pk), 'carrier'),
            (self.office(carrier=theirs.pk), 'carrier'),
        ):
            with self.subTest(field=field, body=body):
                response = self.call('post', body=body)
                self.assertEqual(response.status_code, 400, response.content)
                self.assertIn(field, response.json())
        self.assertEqual(CustomerAddress.objects.count(), 0)
        # Another shop's carrier is refused in the words of one that does not exist.
        self.assertEqual(self.call('post', body=self.office(carrier=theirs.pk)).json(),
                         self.call('post', body=self.office(carrier=999999)).json())

    def test_a_door_keeps_no_agency_and_an_agency_keeps_no_door(self):
        entry = self.add(door(carrier=self.agency.pk, agency_name='Agencia Centro', agency_locality='Puno'))
        self.assertEqual((entry.carrier, entry.agency_name, entry.agency_locality), (None, '', ''))
        office = self.add(self.office(address_line='Av. Otra 9', district='Cercado', city='Puno'))
        self.assertEqual((office.address_line, office.district), ('', ''))

    def test_an_agency_turned_into_a_door_drops_its_agency(self):
        entry = self.add(self.office())
        response = self.call('patch', f'{entry.pk}/', {'kind': 'address', 'address_line': 'Av. Uno 1', 'district': 'Cercado', 'city': 'Puno'})
        self.assertEqual(response.status_code, 200, response.content)
        entry.refresh_from_db()
        self.assertEqual((entry.kind, entry.carrier, entry.agency_name, entry.agency_locality), ('address', None, '', ''))
        self.assertEqual((response.json()['carrier'], response.json()['carrier_name']), (None, ''))

    def test_an_entry_is_corrected(self):
        entry = self.add()
        response = self.call('patch', f'{entry.pk}/', {'label': 'Oficina', 'reference': '', 'recipient_phone': '912345678'})
        self.assertEqual(response.status_code, 200, response.content)
        entry.refresh_from_db()
        self.assertEqual((entry.label, entry.reference, entry.recipient_phone, entry.address_line),
                         ('Oficina', '', '912345678', 'Av. Siempre Viva 123'))
        # Turning a door into an agency asks for what an agency needs.
        self.assertEqual(self.call('patch', f'{entry.pk}/', {'kind': 'agency'}).status_code, 400)
        changed = self.call('patch', f'{entry.pk}/', {'kind': 'agency', 'carrier': self.agency.pk,
                                                    'agency_name': 'Agencia Centro', 'agency_locality': 'Puno'})
        self.assertEqual(changed.status_code, 200, changed.content)
        entry.refresh_from_db()
        self.assertEqual((entry.kind, entry.carrier, entry.address_line), ('agency', self.agency, ''))

    def test_whose_it_is_and_which_is_the_default_are_not_fields_to_write(self):
        first, second = self.add(), self.add(door(label='Trabajo'))
        other = Customer.objects.create(company=self.a, user=self.luis, first_name='Luis')
        response = self.call('patch', f'{second.pk}/', {
            'label': 'Trabajo 2', 'customer': other.pk, 'customer_id': other.pk, 'company': self.b.pk,
            'is_default': True, 'is_active': False,
        })
        self.assertEqual(response.status_code, 200, response.content)
        second.refresh_from_db()
        self.assertEqual((second.label, second.customer.user, second.company, second.is_default, second.is_active),
                         ('Trabajo 2', self.ana, self.a, False, True))
        first.refresh_from_db()
        self.assertTrue(first.is_default)

    def test_one_default_and_choosing_another_moves_it(self):
        first, second, third = self.add(), self.add(door(label='Trabajo')), self.add(self.office())
        self.assertEqual([e.is_default for e in (first, second, third)], [True, False, False])

        response = self.call('post', f'{third.pk}/default/', {})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()['is_default'])
        self.assertEqual(list(CustomerAddress.objects.filter(is_default=True).values_list('pk', flat=True)), [third.pk])
        self.assertEqual([row['id'] for row in self.book()], [third.pk, first.pk, second.pk])   # the default first
        # Choosing it again changes nothing.
        self.assertEqual(self.call('post', f'{third.pk}/default/', {}).status_code, 200)
        self.assertEqual(CustomerAddress.objects.filter(is_default=True).count(), 1)

    def test_the_database_itself_keeps_one_default(self):
        first = self.add()
        with self.assertRaises(IntegrityError), transaction.atomic():
            CustomerAddress.objects.create(
                company=self.a, customer=first.customer, recipient_name='Otra', address_line='x', district='y', city='z',
                is_default=True,
            )
        # A default that was switched off does not count.
        CustomerAddress.objects.filter(pk=first.pk).update(is_active=False)
        CustomerAddress.objects.create(
            company=self.a, customer=first.customer, recipient_name='Otra', address_line='x', district='y', city='z',
            is_default=True,
        )

    def test_removing_an_entry_switches_it_off_and_the_default_moves_on(self):
        first, second = self.add(), self.add(door(label='Trabajo'))
        response = self.call('delete', f'{first.pk}/')
        self.assertEqual(response.status_code, 204)
        first.refresh_from_db()
        self.assertEqual((first.is_active, first.is_default), (False, False))      # kept, not deleted
        second.refresh_from_db()
        self.assertTrue(second.is_default)
        self.assertEqual([row['id'] for row in self.book()], [second.pk])
        # What is gone cannot be corrected, chosen or removed again.
        for method, tail, body in (('patch', f'{first.pk}/', {'label': 'x'}), ('post', f'{first.pk}/default/', {}),
                                   ('delete', f'{first.pk}/', None)):
            self.assertEqual(self.call(method, tail, body).status_code, 404)

    def test_a_book_has_a_limit_of_active_entries(self):
        self.assertEqual(delivery.MAX_ACTIVE_ADDRESSES, 10)
        entries = [self.add(door(label=f'Casa {n}')) for n in range(delivery.MAX_ACTIVE_ADDRESSES)]
        full = self.call('post', body=door(label='Una más'))
        self.assertEqual(full.status_code, 400)
        self.assertIn('10', full.json()['detail'])
        self.assertEqual(CustomerAddress.objects.count(), 10)
        # Removing one makes room: only active entries count.
        self.call('delete', f'{entries[-1].pk}/')
        self.add(door(label='Una más'))
        self.assertEqual(CustomerAddress.objects.filter(is_active=True).count(), 10)

    def test_it_is_written_down_with_names_and_no_values(self):
        entry = self.add(door(recipient_document_type='dni', recipient_document_number=DNI))
        self.call('patch', f'{entry.pk}/', {'label': 'Oficina', 'recipient_phone': '912345678'})
        self.call('patch', f'{entry.pk}/', {'label': 'Oficina'})                 # nothing changed: nothing written
        second = self.add(door(label='Trabajo'))
        self.call('post', f'{second.pk}/default/', {})
        self.call('delete', f'{entry.pk}/')

        entries = list(AdminAuditLog.objects.filter(action__startswith='customer_address_').order_by('pk'))
        self.assertEqual([e.action for e in entries], [
            'customer_address_created', 'customer_address_updated', 'customer_address_created',
            'customer_address_default_set', 'customer_address_deactivated',
        ])
        self.assertTrue(all((e.actor, e.company, e.target_type) == (self.ana, self.a, 'customer_address') for e in entries))
        self.assertEqual(entries[1].metadata, {'changed_fields': ['label', 'recipient_phone']})
        written = json.dumps([e.metadata for e in entries])
        for value in ('Siempre Viva', '987654321', '912345678', DNI, 'Ana', 'Arequipa', 'Oficina'):
            self.assertNotIn(value, written)
        # The record the first entry made is written down as well.
        self.assertTrue(AdminAuditLog.objects.filter(action='customer_profile_updated', metadata__created=True).exists())

    def test_writes_are_limited_per_account_and_reading_is_not(self):
        from store.throttles import AccountAddressWriteThrottle

        throttle = AccountAddressWriteThrottle()
        self.assertEqual((throttle.num_requests, throttle.duration), (60, 3600))
        entry = self.add()
        cache.clear()
        statuses = [
            self.web.patch(f'{WEB_BOOK}{entry.pk}/', {'label': f'Casa {n}'}, format='json', **self.host()).status_code
            for n in range(throttle.num_requests)
        ]
        self.assertEqual(set(statuses), {200})
        for method, tail, body in (('patch', f'{entry.pk}/', {'label': 'x'}), ('post', '', door()),
                                   ('post', f'{entry.pk}/default/', {}), ('delete', f'{entry.pk}/', None)):
            send = getattr(self.web, method)
            args = (WEB_BOOK + tail,) if body is None else (WEB_BOOK + tail, body)
            self.assertEqual(send(*args, format='json', **self.host()).status_code, 429)
        self.assertEqual(self.web.get(WEB_BOOK, **self.host()).status_code, 200)


class AddressBookOwnershipTest(_BookBase):
    """Another buyer's entry, or another shop's, answers like one that does not exist."""

    def setUp(self):
        super().setUp()
        self.mine = self.add()
        self.luis_web = web_session(self.luis)
        self.his = self.add(door(recipient_name='Luis'), client=self.luis_web)
        self.elsewhere = self.add(door(label='En la otra tienda'), company=self.b)

    def assert_untouched(self):
        for entry, label in ((self.mine, 'Casa'), (self.his, 'Casa'), (self.elsewhere, 'En la otra tienda')):
            entry.refresh_from_db()
            self.assertEqual((entry.label, entry.is_active, entry.is_default), (label, True, True))

    def test_each_buyer_reads_only_their_own_in_this_shop(self):
        self.assertEqual([row['id'] for row in self.book()], [self.mine.pk])
        self.assertEqual([row['id'] for row in self.book(client=self.luis_web)], [self.his.pk])
        self.assertEqual([row['id'] for row in self.book(company=self.b)], [self.elsewhere.pk])
        self.assertEqual(self.book(client=self.luis_web, company=self.b), [])

    def test_another_buyers_entry_does_not_exist_in_any_verb(self):
        for method, tail, body in (('patch', f'{self.his.pk}/', {'label': 'Mía'}), ('delete', f'{self.his.pk}/', None),
                                   ('post', f'{self.his.pk}/default/', {})):
            with self.subTest(method=method, tail=tail):
                self.assertEqual(self.call(method, tail, body).status_code, 404)
        self.assert_untouched()

    def test_my_entry_in_another_shop_does_not_exist_in_this_one(self):
        for method, tail, body in (('patch', f'{self.elsewhere.pk}/', {'label': 'Aquí'}), ('delete', f'{self.elsewhere.pk}/', None),
                                   ('post', f'{self.elsewhere.pk}/default/', {})):
            with self.subTest(method=method, tail=tail):
                self.assertEqual(self.call(method, tail, body, company=self.a).status_code, 404)
        self.assert_untouched()

    def test_my_entry_in_each_shop_is_mine_in_that_shop(self):
        # One person, two shops, two books: each is written where it lives.
        self.assertEqual(self.call('patch', f'{self.mine.pk}/', {'label': 'Aquí'}, company=self.a).status_code, 200)
        self.assertEqual(self.call('patch', f'{self.elsewhere.pk}/', {'label': 'Allá'}, company=self.b).status_code, 200)
        self.assertEqual(self.call('post', f'{self.elsewhere.pk}/default/', {}, company=self.b).status_code, 200)
        self.mine.refresh_from_db()
        self.elsewhere.refresh_from_db()
        self.assertEqual((self.mine.label, self.elsewhere.label), ('Aquí', 'Allá'))

    def test_staff_of_the_shop_have_no_door_into_a_buyers_book(self):
        client = web_session_for(self.admin)
        self.assertEqual(self.book(client=client), [])
        self.assertEqual(self.call('patch', f'{self.mine.pk}/', {'label': 'De la tienda'}, client=client).status_code, 404)
        self.assert_untouched()

    def test_a_shop_that_cannot_be_named_has_no_book(self):
        with override_settings(DEFAULT_STOREFRONT_COMPANY_SLUG='', DEBUG=False):
            self.assertEqual(self.web.get(WEB_BOOK, HTTP_HOST='nadie.example.com').status_code, 404)
            cache.clear()
            self.assertEqual(self.web.post(WEB_BOOK, door(), format='json', HTTP_HOST='nadie.example.com').status_code, 404)


def web_session_for(user):
    """A browser signed in as a member created by the panel's helpers (their password is the helpers')."""
    cache.clear()
    client = APIClient()
    client.force_authenticate(user)
    return client


class AddressBookFromTheAppTest(_BookBase):
    def setUp(self):
        super().setUp()
        _order(self.a, user=self.ana, total='120.00', paid=True)        # a client of shop A, by a purchase
        self.app = v1_session(self.ana)

    def app_call(self, method, tail='', body=None, *, slug='envios-a', client=None):
        cache.clear()
        send = getattr(client or self.app, method)
        args = (app_book(slug, tail),) if body is None else (app_book(slug, tail), body)
        return send(*args, format='json')

    def test_the_same_book_as_the_web(self):
        created = self.app_call('post', body=self.office())
        self.assertEqual(created.status_code, 201, created.content)
        entry = CustomerAddress.objects.get()
        self.assertEqual((entry.company, entry.customer.user, entry.kind, entry.is_default), (self.a, self.ana, 'agency', True))
        self.assertEqual(self.app_call('get').json(), self.call('get').json())
        self.assertEqual(set(created.json()), ADDRESS_KEYS)

        second = self.add()
        self.assertEqual(self.app_call('patch', f'{second.pk}/', {'label': 'Desde la app'}).status_code, 200)
        self.assertEqual(self.app_call('post', f'{second.pk}/default/', {}).status_code, 200)
        self.assertEqual(self.app_call('delete', f'{entry.pk}/').status_code, 204)
        second.refresh_from_db()
        entry.refresh_from_db()
        self.assertEqual((second.label, second.is_default, entry.is_active), ('Desde la app', True, False))
        self.assertEqual(AdminAuditLog.objects.filter(action__startswith='customer_address_', company=self.a).count(), 5)

    def test_it_refuses_what_the_web_refuses_in_the_same_words(self):
        for body in (door(city=''), self.office(carrier=999999), door(recipient_phone='123')):
            self.assertEqual(self.app_call('post', body=body).json(), self.call('post', body=body).json())
        self.assertEqual(CustomerAddress.objects.count(), 0)

    def test_whoever_is_not_a_client_of_the_shop_named_finds_nothing(self):
        mine = self.add()
        for slug in ('envios-b', 'no-existe'):
            for method, tail, body in (('get', '', None), ('post', '', door()), ('patch', f'{mine.pk}/', {'label': 'x'}),
                                       ('delete', f'{mine.pk}/', None), ('post', f'{mine.pk}/default/', {})):
                with self.subTest(slug=slug, method=method, tail=tail):
                    self.assertEqual(self.app_call(method, tail, body, slug=slug).status_code, 404)
        mine.refresh_from_db()
        self.assertEqual((mine.label, mine.is_active), ('Casa', True))

    def test_another_buyers_entry_does_not_exist_in_any_verb(self):
        _order(self.a, user=self.luis, total='50.00', paid=True)
        his = self.add(door(recipient_name='Luis'), client=web_session(self.luis))
        for method, tail, body in (('patch', f'{his.pk}/', {'label': 'Mía'}), ('delete', f'{his.pk}/', None),
                                   ('post', f'{his.pk}/default/', {})):
            with self.subTest(method=method):
                self.assertEqual(self.app_call(method, tail, body).status_code, 404)
        self.assertEqual(self.app_call('get').json()['results'], [])
        his.refresh_from_db()
        self.assertEqual((his.label, his.is_active, his.is_default), ('Casa', True, True))

    def test_only_a_bearer_session_opens_it(self):
        self.assertEqual(APIClient().get(app_book('envios-a')).status_code, 401)
        self.assertEqual(self.web.get(app_book('envios-a')).status_code, 401)


import threading  # noqa: E402

from django.db import connection  # noqa: E402
from django.test import TransactionTestCase  # noqa: E402


class TwoDefaultsAtOnceTest(TransactionTestCase):
    """
    Real commits and real row locks: what a `TestCase` cannot see. It needs
    nothing a migration seeds, so it does not ask for the database to be put back.
    """

    def setUp(self):
        cache.clear()
        self.company = _saas_company('Tienda A', 'envios-a', tax_id='20999999990')
        self.user = User.objects.create_user('ana', 'ana@correo.test', PASSWORD, first_name='Ana')
        self.entries = [
            delivery.create_address(company=self.company, user=self.user, data=door(label=f'Casa {n}'))
            for n in range(3)
        ]

    def together(self, jobs):
        barrier, errors = threading.Barrier(len(jobs)), []

        def run(job):
            try:
                barrier.wait(timeout=10)
                job()
            except Exception as failed:  # noqa: BLE001 — reported below
                errors.append(failed)
            finally:
                connection.close()

        threads = [threading.Thread(target=run, args=(job,)) for job in jobs]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        return errors

    def defaults(self):
        return list(CustomerAddress.objects.filter(customer__user=self.user, is_active=True, is_default=True)
                    .values_list('pk', flat=True))

    def test_two_requests_choosing_a_default_at_once_leave_exactly_one(self):
        _first, second, third = self.entries
        for _ in range(5):
            errors = self.together([
                lambda: delivery.set_default_address(company=self.company, user=self.user, pk=second.pk),
                lambda: delivery.set_default_address(company=self.company, user=self.user, pk=third.pk),
            ])
            self.assertEqual(errors, [])
            chosen = self.defaults()
            self.assertEqual(len(chosen), 1)
            self.assertIn(chosen[0], (second.pk, third.pk))

    def test_two_first_entries_at_once_make_one_record_and_one_default(self):
        other = User.objects.create_user('luis', 'luis@correo.test', PASSWORD, first_name='Luis')
        errors = self.together([
            lambda: delivery.create_address(company=self.company, user=other, data=door(label='Una')),
            lambda: delivery.create_address(company=self.company, user=other, data=door(label='Otra')),
        ])
        self.assertEqual(errors, [])
        self.assertEqual(Customer.objects.filter(company=self.company, user=other).count(), 1)
        book = CustomerAddress.objects.filter(customer__user=other)
        self.assertEqual((book.count(), book.filter(is_default=True).count()), (2, 1))


# =============================================================================
# 3 · the delivery record of an order
# =============================================================================

from store.models import CartItem, OrderRiskAssessment  # noqa: E402
from store.tests import (  # noqa: E402
    FAKE_SESSION_TOKEN, IZIPAY_TEST_SETTINGS, SESSION_TOKEN_PATH, _c1_sale, _m5_stock, _prod, _saas_branch,
)

WEB_CHECKOUT = '/api/payments/create-checkout-session/'
Mode = OrderDelivery.Mode


@override_settings(ALLOWED_HOSTS=['*'], **IZIPAY_TEST_SETTINGS)
class RecordedAtCheckoutTest(TestCase):
    """Made with the order, from the order's own columns — and only for an online order."""

    def setUp(self):
        cache.clear()
        self.company = _saas_company('Tienda', 'envios-caja', tax_id='20999999990')
        self.branch = self.company.branches.first() or _saas_branch(self.company)
        self.company.default_inventory_branch = self.branch
        self.company.save(update_fields=['default_inventory_branch'])
        self.product = _prod(self.company, 'Funda', 'funda-envios', price='80.00')
        _m5_stock(self.product, self.branch, 50)
        patcher = patch(SESSION_TOKEN_PATH, return_value=FAKE_SESSION_TOKEN)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.n = 0

    def web_checkout(self, client=None, **overrides):
        self.n += 1
        session = f'carrito-{self.n}'
        CartItem.objects.create(session_key=session, product=self.product, quantity=1)
        body = {
            'session_key': session, 'customer_name': 'Ana Torres', 'customer_email': 'ana@correo.test',
            'customer_phone': '987654321', 'document_type': 'dni', 'document_number': DNI,
            'delivery_method': 'national_shipping', 'address_line': 'Av. Siempre Viva 123', 'city': 'Puno',
            'district': 'Juliaca', 'reference': 'Frente al parque', 'receipt_type': 'boleta',
            'accepted_terms': True, 'accepted_warranty_policy': True,
        }
        body.update(overrides)
        cache.clear()
        return (client or APIClient()).post(WEB_CHECKOUT, body, format='json', HTTP_HOST='envios-caja.example.com')

    def record_of(self, response):
        self.assertIn(response.status_code, (200, 201), response.content)
        return OrderDelivery.objects.get(order_id=response.json()['order_id'])

    def test_a_guest_order_on_the_web(self):
        record = self.record_of(self.web_checkout())
        order = record.order
        self.assertEqual((record.company, record.mode), (self.company, Mode.NATIONAL_ADDRESS))
        self.assertEqual(
            (record.recipient_name, record.recipient_phone, record.recipient_document_type, record.recipient_document_number),
            ('Ana Torres', '987654321', 'dni', DNI),
        )
        self.assertEqual((record.address_line, record.district, record.city, record.reference),
                         ('Av. Siempre Viva 123', 'Juliaca', 'Puno', 'Frente al parque'))
        # Nothing a checkout does not know.
        self.assertEqual(
            (record.carrier, record.carrier_name, record.agency_name, record.freight_amount, record.freight_paid_by,
             record.waybill_number, record.tracking_code, record.tracking_url, record.tracking_url_typed,
             record.shipped_at, record.updated_by),
            (None, '', '', None, '', '', '', '', False, None, None),
        )
        # The order itself is what it always was.
        self.assertEqual((order.delivery_method, order.address_line, order.city), ('national_shipping', 'Av. Siempre Viva 123', 'Puno'))
        self.assertEqual(order.total, Decimal('80.00'))

    def test_the_mode_comes_from_the_orders_delivery_method(self):
        self.assertEqual(delivery.MODE_BY_METHOD, {
            'pickup_store': Mode.PICKUP_STORE, 'delivery_arequipa': Mode.LOCAL_DELIVERY, 'national_shipping': Mode.NATIONAL_ADDRESS,
        })
        self.assertEqual(set(delivery.MODE_BY_METHOD), set(Order.DeliveryMethod.values))
        for method, mode in delivery.MODE_BY_METHOD.items():
            with self.subTest(method):
                record = self.record_of(self.web_checkout(delivery_method=method))
                self.assertEqual((record.mode, record.order.delivery_method), (mode, method))

    def test_a_signed_in_order_and_one_from_the_app(self):
        user = User.objects.create_user('ana', 'ana@correo.test', PASSWORD, first_name='Ana')
        record = self.record_of(self.web_checkout(web_session(user), delivery_method='pickup_store'))
        self.assertEqual((record.mode, record.order.user), (Mode.PICKUP_STORE, user))

        client = v1_session(user)
        body = {
            'items': [{'product_slug': 'funda-envios', 'quantity': 1}], 'customer_name': 'Ana Torres',
            'customer_phone': '912345678', 'document_type': 'dni', 'document_number': DNI,
            'delivery_method': 'delivery_arequipa', 'address_line': 'Calle Dos 45', 'district': 'Yanahuara',
            'city': 'Arequipa',
            'receipt_type': 'boleta', 'accepted_terms': True, 'accepted_warranty_policy': True,
            'idempotency_key': 'pedido-app-0001',
        }
        cache.clear()
        response = client.post('/api/v1/customer/envios-caja/checkout/', body, format='json')
        record = self.record_of(response)
        self.assertEqual((record.mode, record.recipient_phone, record.address_line, record.district),
                         (Mode.LOCAL_DELIVERY, '912345678', 'Calle Dos 45', 'Yanahuara'))
        # The same request again is the same order and the same record.
        cache.clear()
        again = client.post('/api/v1/customer/envios-caja/checkout/', body, format='json')
        self.assertEqual(again.json()['order_id'], response.json()['order_id'])
        self.assertEqual(OrderDelivery.objects.filter(order_id=response.json()['order_id']).count(), 1)

    def test_the_checkout_answers_exactly_what_it_answered(self):
        response = self.web_checkout()
        self.assertEqual(response.status_code, 200)
        text = response.content.decode().lower()
        for word in ('delivery', 'carrier', 'freight', 'waybill', 'recipient'):
            self.assertNotIn(word, text)

    def test_a_record_that_cannot_be_written_never_costs_the_sale(self):
        def broken(order):
            # A refusal of the DATABASE, the kind that poisons a transaction.
            Customer.objects.bulk_create([Customer(company=order.company)])

        with patch('store.delivery_services._snapshot', side_effect=broken), self.assertLogs('store.delivery_services', level='ERROR') as logged:
            response = self.web_checkout()
        self.assertEqual(response.status_code, 200, response.content)
        order = Order.objects.get(pk=response.json()['order_id'])
        self.assertEqual((order.items.count(), order.status), (1, Order.Status.PENDING_PAYMENT))
        self.assertFalse(OrderDelivery.objects.exists())
        # The assessment made in the same transaction is still there.
        self.assertTrue(OrderRiskAssessment.objects.filter(order=order).exists())
        text = '\n'.join(logged.output)
        for value in ('ana@correo.test', '987654321', DNI, 'Ana Torres', 'Siempre Viva'):
            self.assertNotIn(value, text)

    def test_a_python_error_in_it_does_not_cost_the_sale_either(self):
        with patch('store.delivery_services._snapshot', side_effect=RuntimeError('Av. Siempre Viva 123')), \
                self.assertLogs('store.delivery_services', level='ERROR') as logged:
            response = self.web_checkout()
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(OrderDelivery.objects.exists())
        self.assertNotIn('Siempre Viva', '\n'.join(logged.output))

    def test_a_sale_at_the_till_has_no_delivery_record(self):
        cashier, _ = _p2d_member(self.company, 'caja_envios', ['company.view', 'sales.pos.use'])
        order, created = _c1_sale(actor=cashier, company=self.company, branch=self.branch,
                                  items=[{'product': self.product.pk, 'quantity': 1}])
        self.assertTrue(created)
        self.assertFalse(OrderDelivery.objects.filter(order=order).exists())
        # Not even if somebody asked for one: a sale at the till has no delivery.
        Order.objects.filter(pk=order.pk).update(delivery_method='pickup_store')
        order.refresh_from_db()
        self.assertIsNone(delivery.snapshot_for_order(order))
        self.assertFalse(OrderDelivery.objects.filter(order=order).exists())

    def test_an_order_is_recorded_once(self):
        record = self.record_of(self.web_checkout())
        with self.assertNoLogs('store.delivery_services', level='ERROR'):
            self.assertIsNone(delivery.snapshot_for_order(record.order))
        self.assertEqual(OrderDelivery.objects.count(), 1)

    def test_it_belongs_to_the_company_of_its_order(self):
        other = _saas_company('Otra', 'envios-otra', tax_id='20111111112')
        with self.assertRaises(ValueError):
            OrderDelivery.objects.create(company=other, order=_order(self.company))


class RecordsWhenTheTableArrivedTest(TestCase):
    """Migration 0119: every online order that already said how it is delivered gets its record."""

    def setUp(self):
        self.a = _saas_company('Tienda A', 'envios-a', tax_id='20999999990')
        self.b = _saas_company('Tienda B', 'envios-b', tax_id='20111111112')
        self.module = import_module('store.migrations.0121_delivery_records_backfill')

    def arrive(self):
        from django.apps import apps

        self.module.record_existing_deliveries(apps, None)

    def old(self, company=None, **extra):
        extra.setdefault('customer_name', 'Ana Torres')
        extra.setdefault('customer_phone', '987654321')
        extra.setdefault('document_type', 'dni')
        extra.setdefault('document_number', DNI)
        return _order(company or self.a, paid=True, **extra)

    def test_each_online_order_gets_the_record_a_checkout_would_have_made(self):
        shipped = self.old(delivery_method='national_shipping', address_line='Av. Uno 1', district='Juliaca', city='Puno', reference='Puerta azul')
        local = self.old(self.b, delivery_method='delivery_arequipa', address_line='Calle Dos 2', district='Cayma')
        pickup = self.old(delivery_method='pickup_store')
        self.arrive()

        record = OrderDelivery.objects.get(order=shipped)
        self.assertEqual((record.company, record.mode), (self.a, Mode.NATIONAL_ADDRESS))
        self.assertEqual((record.recipient_name, record.recipient_phone, record.recipient_document_type, record.recipient_document_number),
                         ('Ana Torres', '987654321', 'dni', DNI))
        self.assertEqual((record.address_line, record.district, record.city, record.reference), ('Av. Uno 1', 'Juliaca', 'Puno', 'Puerta azul'))
        self.assertEqual((OrderDelivery.objects.get(order=local).mode, OrderDelivery.objects.get(order=local).company), (Mode.LOCAL_DELIVERY, self.b))
        self.assertEqual(OrderDelivery.objects.get(order=pickup).mode, Mode.PICKUP_STORE)
        # Exactly what the live code writes for a new order.
        twin = self.old(delivery_method='national_shipping', address_line='Av. Uno 1', district='Juliaca', city='Puno', reference='Puerta azul')
        live = delivery.snapshot_for_order(twin)
        for field in ('mode', 'recipient_name', 'recipient_phone', 'recipient_document_type', 'recipient_document_number',
                      'address_line', 'district', 'city', 'department', 'reference', 'carrier_name', 'freight_amount'):
            self.assertEqual(getattr(live, field), getattr(record, field), field)

    def test_a_till_sale_and_an_order_that_says_nothing_get_none(self):
        till = self.old(sales_channel='pos', delivery_method='pickup_store')
        silent = self.old(delivery_method='')
        self.arrive()
        self.assertFalse(OrderDelivery.objects.filter(order__in=[till, silent]).exists())

    def test_it_can_run_twice_and_keeps_what_staff_already_wrote(self):
        order = self.old(delivery_method='national_shipping', address_line='Av. Uno 1')
        self.arrive()
        OrderDelivery.objects.filter(order=order).update(waybill_number='G-100', mode=Mode.AGENCY_PICKUP)
        self.arrive()
        record = OrderDelivery.objects.get(order=order)
        self.assertEqual((record.waybill_number, record.mode), ('G-100', Mode.AGENCY_PICKUP))
        self.assertEqual(OrderDelivery.objects.count(), 1)

    def test_it_works_through_more_orders_than_one_batch(self):
        with patch.object(self.module, 'BATCH', 3):
            orders = [self.old(delivery_method='pickup_store') for _ in range(8)]
            self.arrive()
        self.assertEqual(OrderDelivery.objects.filter(order__in=orders).count(), 8)

    def test_backwards_it_deletes_nothing(self):
        # The step back is not «delete every record»: by then staff have written waybills
        # into them. Un-applying 0119 drops the table; this step alone leaves it as it is.
        from django.apps import apps
        from django.db import migrations

        order = self.old(delivery_method='national_shipping', address_line='Av. Uno 1', city='Puno')
        self.arrive()
        OrderDelivery.objects.filter(order=order).update(waybill_number='G-100')
        [step] = self.module.Migration.operations
        self.assertIs(step.reverse_code, migrations.RunPython.noop)
        step.reverse_code(apps, None)
        self.assertEqual(OrderDelivery.objects.get(order=order).waybill_number, 'G-100')
        self.assertNotIn('.delete(', __import__('inspect').getsource(self.module))

    def test_the_step_reads_no_live_code_and_its_table_is_todays(self):
        import inspect

        source = inspect.getsource(self.module)
        self.assertNotIn('from store', source)
        self.assertNotIn('import store', source)
        self.assertEqual(self.module.MODE_BY_METHOD, {k: str(v) for k, v in delivery.MODE_BY_METHOD.items()})


# =============================================================================
# 4 · staff write what the order's columns never had
# =============================================================================

from django.utils import timezone  # noqa: E402
from django.utils.dateparse import parse_datetime  # noqa: E402

STAFF_KEYS = {
    'exists', 'can_edit', 'mode', 'mode_label', 'allowed_modes',
    'recipient_name', 'recipient_document_type', 'recipient_document_number', 'recipient_phone',
    'address_line', 'district', 'city', 'department', 'reference',
    'carrier', 'carrier_name', 'agency_name', 'agency_locality',
    'freight_amount', 'freight_paid_by', 'waybill_number', 'tracking_code', 'tracking_url', 'tracking_url_typed',
    'shipped_at', 'updated_by', 'updated_at',
}


def panel_delivery(pk):
    return f'/api/admin/orders/{pk}/delivery/'


def app_delivery(slug, pk):
    return f'/api/v1/internal/{slug}/orders/{pk}/delivery/'


class _StaffDeliveryBase(_CompaniesBase):
    def setUp(self):
        super().setUp()
        self.buyer = User.objects.create_user('compradora', 'compradora@correo.test', PASSWORD, first_name='Ana')
        self.order = self.online(self.a, self.north, user=self.buyer)
        self.southern = self.online(self.a, self.south)
        self.foreign = self.online(self.b, None)
        self.agency = self.carrier(name='Agencia Uno', kind='agency', tracking_url_template='https://rastreo.ejemplo.test/g/{guia}')
        self.courier = self.carrier(name='A domicilio', kind='courier')

    def online(self, company, branch, *, method='national_shipping', record=True, **extra):
        order = _order(
            company, total='900.00', paid=True, fulfillment_branch=branch, delivery_method=method,
            customer_name='Ana Torres', customer_email='ana.torres@correo.test', customer_phone='987654321',
            document_type='dni', document_number=DNI, address_line='Av. Siempre Viva 123', district='Juliaca',
            city='Puno', reference='Puerta azul', **extra,
        )
        if record:
            self.assertIsNotNone(delivery.snapshot_for_order(order))
        return order

    def url(self, order, surface, slug='envios-a'):
        return panel_delivery(order.pk) if surface == 'panel' else app_delivery(slug, order.pk)

    def read(self, user, order=None, surface='panel', **kw):
        return acting(user).get(self.url(order or self.order, surface, **kw))

    def write(self, user, body, order=None, surface='panel', **kw):
        return acting(user).patch(self.url(order or self.order, surface, **kw), body, format='json')

    def record(self, order=None):
        return OrderDelivery.objects.filter(order=order or self.order).first()

    def entries(self):
        return list(AdminAuditLog.objects.filter(action='order_delivery_updated').order_by('pk'))


class StaffReadDeliveryTest(_StaffDeliveryBase):
    def test_the_record_as_staff_read_it(self):
        for surface in ('panel', 'app'):
            with self.subTest(surface):
                response = self.read(self.viewer, surface=surface)
                self.assertEqual(response.status_code, 200, response.content)
                body = response.json()
                self.assertEqual(set(body), STAFF_KEYS)
                self.assertEqual(
                    (body['exists'], body['can_edit'], body['mode'], body['mode_label']),
                    (True, False, 'national_address', 'Envío nacional a domicilio'),
                )
                self.assertEqual(body['allowed_modes'], ['national_address', 'agency_pickup'])
                self.assertEqual((body['recipient_name'], body['recipient_phone'], body['recipient_document_number']),
                                 ('Ana Torres', '987654321', DNI))
                self.assertEqual((body['address_line'], body['district'], body['city']), ('Av. Siempre Viva 123', 'Juliaca', 'Puno'))
                self.assertEqual((body['carrier'], body['carrier_name'], body['freight_amount'], body['shipped_at'], body['updated_by']),
                                 (None, '', None, None, None))
                self.assertTrue(self.read(self.manager, surface=surface).json()['can_edit'])

    def test_an_order_without_a_record_answers_the_empty_shape(self):
        till = self.online(self.a, self.north, record=False, sales_channel='pos')
        silent = self.online(self.a, self.north, record=False, method='')
        for surface in ('panel', 'app'):
            with self.subTest(surface):
                body = self.read(self.manager, till, surface).json()
                self.assertEqual(set(body), STAFF_KEYS)
                self.assertEqual((body['exists'], body['can_edit'], body['mode'], body['allowed_modes'], body['recipient_name']),
                                 (False, False, '', [], ''))                     # a till sale has no delivery to write
                body = self.read(self.manager, silent, surface).json()
                self.assertEqual((body['exists'], body['can_edit'], body['mode']), (False, True, ''))
        self.assertFalse(OrderDelivery.objects.filter(order__in=[till, silent]).exists())      # reading made nothing

    def test_reading_writes_nothing(self):
        from django.test.utils import CaptureQueriesContext

        for surface in ('panel', 'app'):
            client = acting(self.manager)
            with CaptureQueriesContext(connection) as queries:
                client.get(self.url(self.order, surface))
                client.options(self.url(self.order, surface))
            writes = [q['sql'] for q in queries.captured_queries if q['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE'))]
            self.assertEqual(writes, [])

    def test_the_order_detail_carries_it_too(self):
        panel = acting(self.viewer).get(f'/api/admin/orders/{self.order.pk}/').json()
        app = acting(self.viewer).get(f'/api/v1/internal/envios-a/orders/{self.order.pk}/').json()
        self.assertEqual(panel['delivery'], self.read(self.viewer).json())
        self.assertEqual(app['delivery'], panel['delivery'])

    def test_who_may_read_it(self):
        for surface in ('panel', 'app'):
            with self.subTest(surface):
                self.assertEqual(self.read(self.outsider, surface=surface).status_code, 403)
                self.assertEqual(self.read(self.admin_b, surface=surface).status_code, 404)
                self.assertEqual(self.read(self.manager, self.foreign, surface).status_code, 404)
                self.assertEqual(self.read(self.northerner, self.southern, surface).status_code, 404)
                self.assertEqual(APIClient().get(self.url(self.order, surface)).status_code, 401)
        self.assertEqual(self.read(self.buyer, surface='panel').status_code, 403)
        self.assertEqual(self.read(self.buyer, surface='app').status_code, 404)
        self.assertEqual(self.read(self.admin_b, surface='app', slug='envios-b').status_code, 404)


class StaffWriteDeliveryTest(_StaffDeliveryBase):
    def test_an_order_sent_to_an_agency(self):
        when = '2026-10-12T15:30:00Z'
        response = self.write(self.manager, {
            'mode': 'agency_pickup', 'carrier': self.agency.pk, 'agency_name': 'Agencia Centro', 'agency_locality': 'Juliaca, Puno',
            'recipient_name': 'Rosa Quispe', 'recipient_document_type': 'dni', 'recipient_document_number': '70000002',
            'recipient_phone': '+51 912 345 678', 'freight_amount': '15.50', 'freight_paid_by': 'customer_at_destination',
            'waybill_number': ' G 100/7 ', 'tracking_code': 'CLAVE-9', 'shipped_at': when,
        })
        self.assertEqual(response.status_code, 200, response.content)
        record = self.record()
        self.assertEqual((record.mode, record.carrier, record.carrier_name, record.agency_name, record.agency_locality),
                         (Mode.AGENCY_PICKUP, self.agency, 'Agencia Uno', 'Agencia Centro', 'Juliaca, Puno'))
        self.assertEqual((record.recipient_name, record.recipient_document_number, record.recipient_phone),
                         ('Rosa Quispe', '70000002', '+51912345678'))
        self.assertEqual((record.freight_amount, record.freight_paid_by, record.waybill_number, record.tracking_code),
                         (Decimal('15.50'), 'customer_at_destination', 'G 100/7', 'CLAVE-9'))
        # No address was typed: it comes from the carrier's own page, with the waybill encoded.
        self.assertEqual(record.tracking_url, 'https://rastreo.ejemplo.test/g/G%20100%2F7')
        self.assertEqual((record.shipped_at, record.updated_by), (parse_datetime(when), self.manager))
        body = response.json()
        self.assertEqual(set(body), STAFF_KEYS)
        self.assertEqual((body['carrier_name'], body['freight_amount'], body['updated_by'], body['mode_label']),
                         ('Agencia Uno', '15.50', 'Personal de la tienda', 'Recojo en agencia'))

    def test_nothing_of_the_order_itself_moves(self):
        before = Order.objects.filter(pk=self.order.pk).values().get()
        self.write(self.manager, {'mode': 'agency_pickup', 'carrier': self.agency.pk, 'freight_amount': '250.00',
                                  'freight_paid_by': 'store', 'recipient_name': 'Rosa Quispe', 'shipped_at': '2026-10-12T15:30:00Z'})
        after = Order.objects.filter(pk=self.order.pk).values().get()
        self.assertEqual(after, before)                    # method, address, buyer, total, payment, dispatch: all of it
        self.assertEqual((after['delivery_method'], after['total'], after['fulfillment_status']),
                         ('national_shipping', Decimal('900.00'), 'pending'))

    def test_a_typed_tracking_address_wins_and_only_https_is_kept(self):
        typed = self.write(self.manager, {'carrier': self.agency.pk, 'waybill_number': 'G-1', 'tracking_url': 'https://otra.ejemplo.test/ver/G-1'})
        self.assertEqual(typed.status_code, 200, typed.content)
        self.assertEqual(self.record().tracking_url, 'https://otra.ejemplo.test/ver/G-1')
        for bad in ('http://otra.ejemplo.test/G-1', 'javascript:alert(1)', 'otra.ejemplo.test/G-1'):
            with self.subTest(bad):
                response = self.write(self.manager, {'tracking_url': bad})
                self.assertEqual(response.status_code, 400)
                self.assertIn('tracking_url', response.json())
        self.assertEqual(self.record().tracking_url, 'https://otra.ejemplo.test/ver/G-1')
        # A new waybill with no address typed is followed at the carrier's again.
        self.write(self.manager, {'waybill_number': 'G-2', 'tracking_url': ''})
        self.assertEqual(self.record().tracking_url, 'https://rastreo.ejemplo.test/g/G-2')
        # A carrier with no page to follow it leaves no link.
        self.write(self.manager, {'carrier': self.courier.pk, 'waybill_number': 'G-3', 'tracking_url': ''})
        self.assertEqual(self.record().tracking_url, '')

    def test_the_mode_moves_only_between_a_door_and_an_agency(self):
        self.assertEqual(self.write(self.manager, {'mode': 'agency_pickup'}).status_code, 200)
        self.assertEqual(self.write(self.manager, {'mode': 'national_address'}).status_code, 200)
        self.assertEqual(self.write(self.manager, {'mode': 'national_address'}).status_code, 200)      # the same one
        for wrong in ('pickup_store', 'local_delivery', 'dron', '', None):
            with self.subTest(wrong):
                response = self.write(self.manager, {'mode': wrong})
                self.assertEqual(response.status_code, 400)
                self.assertIn('mode', response.json())
        self.assertEqual(self.record().mode, Mode.NATIONAL_ADDRESS)

        for method in ('pickup_store', 'delivery_arequipa'):
            order = self.online(self.a, self.north, method=method)
            mode = self.record(order).mode
            body = self.read(self.manager, order).json()
            self.assertEqual(body['allowed_modes'], [mode])
            for target in ('national_address', 'agency_pickup'):
                with self.subTest(method=method, target=target):
                    self.assertEqual(self.write(self.manager, {'mode': target}, order).status_code, 400)
            self.assertEqual(self.write(self.manager, {'mode': mode, 'waybill_number': 'G-9'}, order).status_code, 200)
            self.assertEqual(self.record(order).mode, mode)
            order.refresh_from_db()
            self.assertEqual(order.delivery_method, method)

    def test_a_carrier_is_one_of_this_company_and_fits_the_mode(self):
        off = self.carrier(name='Apagada', kind='agency', is_active=False)
        theirs = self.carrier(self.b, name='De la otra', kind='agency')
        for body in ({'carrier': theirs.pk}, {'carrier': 999999}, {'carrier': off.pk}, {'carrier': 'uno'},
                     {'mode': 'agency_pickup', 'carrier': self.courier.pk}):
            with self.subTest(body):
                response = self.write(self.manager, body)
                self.assertEqual(response.status_code, 400, response.content)
                self.assertIn('carrier', response.json())
        self.assertEqual(self.write(self.manager, {'carrier': theirs.pk}).json(), self.write(self.manager, {'carrier': 999999}).json())
        self.assertIsNone(self.record().carrier)

        # To a door, either kind of carrier takes it.
        self.assertEqual(self.write(self.manager, {'carrier': self.courier.pk}).status_code, 200)
        self.assertEqual(self.record().carrier_name, 'A domicilio')
        # An agency pickup with a door-to-door carrier already on it is refused too.
        self.assertEqual(self.write(self.manager, {'mode': 'agency_pickup'}).status_code, 400)
        self.assertEqual(self.write(self.manager, {'carrier': None}).status_code, 200)
        record = self.record()
        self.assertEqual((record.carrier, record.carrier_name), (None, ''))

    def test_what_is_typed_is_judged(self):
        for body, field in (
            ({'recipient_name': ' '}, 'recipient_name'), ({'recipient_phone': '12345'}, 'recipient_phone'),
            ({'recipient_document_type': 'dni', 'recipient_document_number': '123'}, 'recipient_document_number'),
            ({'freight_amount': '-1'}, 'freight_amount'), ({'freight_amount': 'mucho'}, 'freight_amount'),
            ({'freight_amount': '1.999'}, 'freight_amount'), ({'freight_amount': '123456789.00'}, 'freight_amount'),
            ({'freight_paid_by': 'nadie'}, 'freight_paid_by'), ({'waybill_number': 'x' * 61}, 'waybill_number'),
            ({'tracking_code': 'x' * 81}, 'tracking_code'), ({'shipped_at': 'ayer'}, 'shipped_at'),
            ({'agency_name': 'x' * 121}, 'agency_name'),
        ):
            with self.subTest(body):
                response = self.write(self.manager, {**body, 'waybill_number': body.get('waybill_number', 'NO-GUARDADA')})
                self.assertEqual(response.status_code, 400, response.content)
                self.assertIn(field, response.json())
        record = self.record()
        self.assertEqual((record.waybill_number, record.recipient_name, record.updated_by), ('', 'Ana Torres', None))
        self.assertEqual(self.entries(), [])

    def test_freight_and_the_shipping_date_can_be_taken_off(self):
        self.write(self.manager, {'freight_amount': '0', 'freight_paid_by': 'store', 'shipped_at': '2026-10-12T15:30:00Z'})
        self.assertEqual(self.record().freight_amount, Decimal('0.00'))
        self.write(self.manager, {'freight_amount': None, 'freight_paid_by': '', 'shipped_at': None})
        record = self.record()
        self.assertEqual((record.freight_amount, record.freight_paid_by, record.shipped_at), (None, '', None))

    def test_what_is_not_staffs_to_write_here_is_not_read(self):
        entry = CustomerAddress.objects.create(
            company=self.a, customer=Customer.objects.create(company=self.a, first_name='Otra'),
            recipient_name='Otra', address_line='x', district='y', city='z',
        )
        response = self.write(self.manager, {
            'waybill_number': 'G-1', 'order': self.southern.pk, 'company': self.b.pk, 'address_line': 'Otra calle 9',
            'city': 'Lima', 'source_address': entry.pk, 'updated_by': self.viewer.pk, 'carrier_name': 'Inventada',
            'delivery_method': 'pickup_store', 'exists': False,
        })
        self.assertEqual(response.status_code, 200, response.content)
        record = self.record()
        self.assertEqual((record.order, record.company, record.address_line, record.city,
                          record.updated_by, record.carrier_name),
                         (self.order, self.a, 'Av. Siempre Viva 123', 'Puno', self.manager, ''))
        self.order.refresh_from_db()
        self.assertEqual(self.order.delivery_method, 'national_shipping')

    def test_an_order_without_a_record_gets_one_only_if_it_was_made_online(self):
        till = self.online(self.a, self.north, record=False, sales_channel='pos')
        silent = self.online(self.a, self.north, record=False, method='')
        for surface in ('panel', 'app'):
            refused = self.write(self.manager, {'waybill_number': 'G-1'}, till, surface)
            self.assertEqual(refused.status_code, 409)
            self.assertEqual(refused.json()['code'], 'not_online')
        self.assertIsNone(self.record(till))

        made = self.write(self.manager, {'mode': 'agency_pickup', 'carrier': self.agency.pk, 'waybill_number': 'G-1'}, silent)
        self.assertEqual(made.status_code, 200, made.content)
        record = self.record(silent)
        self.assertEqual((record.mode, record.recipient_name, record.address_line, record.waybill_number, record.company),
                         (Mode.AGENCY_PICKUP, 'Ana Torres', 'Av. Siempre Viva 123', 'G-1', self.a))
        self.assertTrue(self.entries()[-1].metadata['created'])

    def test_who_may_write_it(self):
        body = {'waybill_number': 'G-1'}
        for surface in ('panel', 'app'):
            with self.subTest(surface):
                self.assertEqual(self.write(self.viewer, body, surface=surface).status_code, 403)
                self.assertEqual(self.write(self.outsider, body, surface=surface).status_code, 403)
                self.assertEqual(self.write(self.admin_b, body, surface=surface).status_code, 404)
                self.assertEqual(self.write(self.manager, body, self.foreign, surface).status_code, 404)
                self.assertEqual(self.write(self.northerner, body, self.southern, surface).status_code, 404)
                self.assertEqual(APIClient().patch(self.url(self.order, surface), body, format='json').status_code, 401)
        self.assertEqual(self.write(self.buyer, body, surface='panel').status_code, 403)
        self.assertEqual(self.write(self.buyer, body, surface='app').status_code, 404)
        self.assertEqual(self.write(self.admin_b, body, surface='app', slug='envios-b').status_code, 404)
        self.assertFalse(OrderDelivery.objects.exclude(waybill_number='').exists())
        self.assertEqual(self.write(self.northerner, body, surface='app').status_code, 200)
        self.assertEqual(self.record().waybill_number, 'G-1')

    def test_the_pilots_operators_from_before_companies(self):
        order = self.online(_pilot_company(), None)

        def legacy(username, role):
            user = User.objects.create_user(username, f'{username}@correo.test', PASSWORD)
            user.profile.role = role
            user.profile.save()
            return user

        # Warehouse staff hand the parcel to the carrier: the waybill is theirs to write.
        warehouse = legacy('almacen', UserProfile.ROLE_INVENTORY)
        self.assertTrue(self.read(warehouse, order).json()['can_edit'])
        self.assertEqual(self.write(warehouse, {'waybill_number': 'G-1'}, order).status_code, 200)
        self.assertEqual(self.read(legacy('cliente', UserProfile.ROLE_CUSTOMER), order).status_code, 403)

    def test_it_is_written_down_with_names_and_no_values(self):
        self.write(self.manager, {'carrier': self.agency.pk, 'waybill_number': 'G-100', 'recipient_name': 'Rosa Quispe',
                                  'recipient_phone': '912345678', 'freight_amount': '15.50'})
        self.write(self.manager, {'waybill_number': 'G-100', 'recipient_name': 'Rosa Quispe'})          # nothing changed
        [entry] = self.entries()
        self.assertEqual((entry.actor, entry.company, entry.target_type, entry.target_id), (self.manager, self.a, 'order', str(self.order.pk)))
        self.assertEqual(entry.metadata, {
            'order_id': self.order.pk, 'created': False,
            'changed_fields': ['carrier', 'carrier_name', 'freight_amount', 'recipient_name', 'recipient_phone',
                               'tracking_url', 'waybill_number'],
        })
        written = json.dumps(entry.metadata)
        for value in ('G-100', 'Rosa', '912345678', '15.50', 'Agencia Uno', 'rastreo'):
            self.assertNotIn(value, written)

    def test_nothing_personal_reaches_a_log(self):
        with self.assertLogs(level='DEBUG') as captured:
            import logging
            logging.getLogger('store').debug('centinela')
            self.write(self.manager, {'recipient_name': 'Rosa Quispe', 'recipient_phone': '912345678', 'waybill_number': 'G-100'})
            self.write(self.manager, {'recipient_phone': '123'})
        text = '\n'.join(captured.output)
        for value in ('Rosa Quispe', '912345678', 'Siempre Viva'):
            self.assertNotIn(value, text)


class TheRecordIsACopyTest(_StaffDeliveryBase):
    """What an order left with is what its record says, whatever happens to the carrier or the address afterwards."""

    def setUp(self):
        super().setUp()
        self.write(self.manager, {'mode': 'agency_pickup', 'carrier': self.agency.pk, 'agency_name': 'Agencia Centro',
                                  'agency_locality': 'Juliaca', 'waybill_number': 'G-100'})

    def test_a_carrier_renamed_or_switched_off(self):
        acting(self.admin).patch(carrier_url(self.agency.pk), {'name': 'Agencia Nueva', 'is_active': False,
                                                              'tracking_url_template': 'https://nueva.ejemplo.test/{guia}'}, format='json')
        record = self.record()
        self.assertEqual((record.carrier, record.carrier_name, record.tracking_url),
                         (self.agency, 'Agencia Uno', 'https://rastreo.ejemplo.test/g/G-100'))
        self.assertEqual(self.read(self.viewer).json()['carrier_name'], 'Agencia Uno')
        # Other fields are still written; the carrier it left with is not asked for again.
        self.assertEqual(self.write(self.manager, {'tracking_code': 'CLAVE-1'}).status_code, 200)
        self.assertEqual(self.record().carrier_name, 'Agencia Uno')

    def test_a_carrier_that_is_gone(self):
        Carrier.objects.filter(pk=self.agency.pk).delete()
        record = self.record()
        self.assertEqual((record.carrier, record.carrier_name, record.agency_name, record.waybill_number),
                         (None, 'Agencia Uno', 'Agencia Centro', 'G-100'))
        self.assertEqual(self.read(self.viewer).json()['carrier_name'], 'Agencia Uno')

    def test_an_address_of_the_book_edited_removed_or_gone(self):
        customer = Customer.objects.create(company=self.a, user=self.buyer, first_name='Ana')
        entry = CustomerAddress.objects.create(
            company=self.a, customer=customer, recipient_name='Ana Torres', address_line='Av. Siempre Viva 123',
            district='Juliaca', city='Puno', is_default=True,
        )
        kept = OrderDelivery.objects.filter(order=self.order).values().get()

        delivery.update_address(company=self.a, user=self.buyer, pk=entry.pk, data={'address_line': 'Otra calle 9', 'city': 'Lima'})
        self.assertEqual(OrderDelivery.objects.filter(order=self.order).values().get(), kept)
        delivery.deactivate_address(company=self.a, user=self.buyer, pk=entry.pk)
        self.assertEqual(OrderDelivery.objects.filter(order=self.order).values().get(), kept)

        CustomerAddress.objects.filter(pk=entry.pk).delete()
        self.assertEqual(OrderDelivery.objects.filter(order=self.order).values().get(), kept)
        record = self.record()
        self.assertEqual((record.address_line, record.city), ('Av. Siempre Viva 123', 'Puno'))

    def test_the_record_points_at_no_entry_of_the_book(self):
        # A copy and nothing else: no code ever wrote such a link, so there is no column for one.
        linked = [field.name for field in OrderDelivery._meta.get_fields()
                  if getattr(field, 'related_model', None) is CustomerAddress]
        self.assertEqual(linked, [])
        self.assertNotIn('source_address', [field.name for field in OrderDelivery._meta.get_fields()])


# =============================================================================
# 5 · what the buyer sees of it
# =============================================================================

BUYER_KEYS = {'carrier_name', 'agency_name', 'agency_locality', 'waybill_number', 'tracking_url'}


@override_settings(ALLOWED_HOSTS=['*'])
class WhatTheBuyerSeesTest(_StaffDeliveryBase):
    def setUp(self):
        super().setUp()
        Customer.objects.create(company=self.a, user=self.buyer, first_name='Ana')
        self.buyer.set_password(PASSWORD)
        self.buyer.save()
        self.order.user = self.buyer
        self.order.save(update_fields=['user'])
        self.write(self.manager, {
            'mode': 'agency_pickup', 'carrier': self.agency.pk, 'agency_name': 'Agencia Centro', 'agency_locality': 'Juliaca, Puno',
            'recipient_name': 'Rosa Quispe', 'recipient_document_type': 'dni', 'recipient_document_number': '70000002',
            'recipient_phone': '912345678', 'freight_amount': '15.50', 'freight_paid_by': 'customer_at_destination',
            'waybill_number': 'G-100', 'tracking_code': 'CLAVE-SECRETA-9',
        })

    def web_rows(self):
        response = web_session(self.buyer).get('/api/orders/', HTTP_HOST='envios-a.example.com')
        self.assertEqual(response.status_code, 200, response.content)
        return response

    def app_rows(self):
        response = v1_session(self.buyer).get('/api/v1/customer/envios-a/orders/')
        self.assertEqual(response.status_code, 200, response.content)
        return response

    def test_their_own_order_says_where_it_went_and_how_to_follow_it(self):
        expected = {
            'carrier_name': 'Agencia Uno', 'agency_name': 'Agencia Centro', 'agency_locality': 'Juliaca, Puno',
            'waybill_number': 'G-100', 'tracking_url': 'https://rastreo.ejemplo.test/g/G-100',
        }
        for response in (self.web_rows(), self.app_rows()):
            row = next(row for row in response.json() if row['id'] == self.order.pk)
            self.assertEqual(row['delivery'], expected)
            self.assertEqual(set(row['delivery']), BUYER_KEYS)
        detail = v1_session(self.buyer).get(f'/api/v1/customer/envios-a/orders/{self.order.pk}/').json()
        self.assertEqual(detail['delivery'], expected)

    def test_never_the_freight_the_recipient_or_who_wrote_it(self):
        def walk(node, keys, values):
            if isinstance(node, dict):
                for key, value in node.items():
                    keys.add(key)
                    walk(value, keys, values)
            elif isinstance(node, list):
                for item in node:
                    walk(item, keys, values)
            else:
                values.add(node)
            return keys, values

        for response in (self.web_rows(), self.app_rows()):
            keys, values = walk(response.json(), set(), set())
            # By KEY and by VALUE, not by searching the text: «15.50» is also the tail of many a timestamp.
            hidden_keys = {key for key in keys if any(word in key for word in ('freight', 'recipient', 'tracking_code', 'updated_by', 'source_address'))}
            self.assertEqual(hidden_keys, set())
            texts = {value for value in values if isinstance(value, str)}
            for amount in ('15.50', '15.5', 15.5):
                self.assertNotIn(amount, values)
            for hidden in ('customer_at_destination', 'Rosa Quispe', '70000002', '912345678', 'CLAVE-SECRETA-9', 'gestora'):
                self.assertEqual([text for text in texts if hidden in text], [], hidden)

    def test_an_order_without_a_record_says_nothing(self):
        plain = self.online(self.a, self.north, record=False, method='', user=self.buyer)
        for response in (self.web_rows(), self.app_rows()):
            row = next(row for row in response.json() if row['id'] == plain.pk)
            self.assertIsNone(row['delivery'])

    def test_only_an_https_address_is_ever_given_as_a_link(self):
        for stored in ('http://rastreo.ejemplo.test/G-100', 'javascript:alert(1)', 'rastreo.ejemplo.test/G-100', ' https://x.test/'):
            OrderDelivery.objects.filter(order=self.order).update(tracking_url=stored)     # written past every rule
            for response in (self.web_rows(), self.app_rows()):
                row = next(row for row in response.json() if row['id'] == self.order.pk)
                self.assertEqual(row['delivery']['tracking_url'], '', stored)

    def test_nobody_else_reads_it(self):
        other = User.objects.create_user('luis', 'luis@correo.test', PASSWORD, first_name='Luis')
        _order(self.a, user=other, total='10.00', paid=True)
        web = web_session(other).get('/api/orders/', HTTP_HOST='envios-a.example.com')
        app = v1_session(other).get('/api/v1/customer/envios-a/orders/')
        for response in (web, app):
            self.assertNotIn(self.order.pk, [row['id'] for row in response.json()])
            self.assertNotIn('G-100', response.content.decode())
        self.assertEqual(v1_session(other).get(f'/api/v1/customer/envios-a/orders/{self.order.pk}/').status_code, 404)

    def test_what_a_buyer_reads_of_an_order_is_a_closed_list(self):
        from store.serializers import OrderSerializer
        from store.v1_customer_serializers import V1CustomerOrderSerializer

        self.assertEqual(V1CustomerOrderSerializer.Meta.fields, [
            'id', 'status', 'status_label', 'fulfillment_status', 'fulfillment_status_label',
            'total', 'discount_amount', 'coupon_code', 'delivery_method', 'delivery_method_label',
            'created_at', 'paid_at', 'items', 'delivery',
        ])
        self.assertEqual(OrderSerializer.Meta.fields, [
            'id', 'user', 'customer_name', 'customer_email', 'total', 'discount_amount',
            'coupon_code', 'status', 'paid', 'paid_at', 'created_at', 'items', 'delivery',
        ])
        self.assertEqual(set(delivery.buyer_payload(self.record())), BUYER_KEYS)

    def test_the_list_costs_no_query_per_order(self):
        from django.test.utils import CaptureQueriesContext

        for _ in range(3):
            self.online(self.a, self.north, user=self.buyer)
        client = v1_session(self.buyer)
        with CaptureQueriesContext(connection) as queries:
            client.get('/api/v1/customer/envios-a/orders/')
        self.assertEqual([q['sql'] for q in queries.captured_queries if 'FROM "store_orderdelivery"' in q['sql']], [])


# ---------------------------------------------------------------------------
# 7 · after the second independent review
# ---------------------------------------------------------------------------

from datetime import timedelta  # noqa: E402

from django.utils import timezone  # noqa: E402


class WhatCannotBeReadIsNotSavedTest(_StaffDeliveryBase):
    """P2-1 · a value the next reader would choke on is an answer about a field, never a row."""

    def readers_answer(self):
        self.assertEqual(self.read(self.manager).status_code, 200)
        self.assertEqual(v1_session(self.buyer).get('/api/v1/customer/envios-a/orders/').status_code, 200)

    def test_a_shipping_moment_is_inside_a_sane_window(self):
        now = timezone.now()
        for moment in (
            '9999-12-31T23:59:59-05:00', '9999-12-31T23:59:59Z', '1999-12-31T23:59:59Z', '0001-01-01T00:00:00+05:00',
            (now + timedelta(days=366)).isoformat(),
        ):
            with self.subTest(moment):
                response = self.write(self.manager, {'shipped_at': moment, 'waybill_number': 'NO-GUARDADA'})
                self.assertEqual(response.status_code, 400, response.content)
                self.assertIn('shipped_at', response.json())
        record = self.record()
        self.assertEqual((record.shipped_at, record.waybill_number), (None, ''))
        self.readers_answer()

    def test_the_edges_of_that_window_are_inside_it(self):
        self.assertEqual(self.write(self.manager, {'shipped_at': '2000-01-01T00:00:00Z'}).status_code, 200)
        soon = timezone.now() + timedelta(days=364)
        self.assertEqual(self.write(self.manager, {'shipped_at': soon.isoformat()}).status_code, 200)
        self.assertEqual(self.record().shipped_at, soon)
        self.readers_answer()

    def test_a_write_that_cannot_be_read_back_is_not_kept(self):
        client = acting(self.manager)
        client.raise_request_exception = False
        with patch.object(delivery, 'record_for', side_effect=OverflowError('date value out of range')):
            response = client.patch(self.url(self.order, 'panel'), {'waybill_number': 'G-1', 'recipient_name': 'Otra'}, format='json')
        self.assertEqual(response.status_code, 500)
        record = self.record()
        self.assertEqual((record.waybill_number, record.recipient_name, record.updated_by), ('', 'Ana Torres', None))
        self.assertEqual(self.entries(), [])
        self.readers_answer()

    def test_a_freight_no_column_holds_is_refused_not_an_error(self):
        for amount in ('1e30', '1E+30', '9e99999', 1e30, 'Infinity', '-Infinity', 'NaN', '100000000', '1e8'):
            with self.subTest(amount):
                response = self.write(self.manager, {'freight_amount': amount})
                self.assertEqual(response.status_code, 400, response.content)
                self.assertIn('freight_amount', response.json())
        self.assertIsNone(self.record().freight_amount)
        self.assertEqual(self.write(self.manager, {'freight_amount': '99999999.99'}).status_code, 200)
        self.assertEqual(self.record().freight_amount, Decimal('99999999.99'))
        self.assertEqual(self.write(self.manager, {'freight_amount': '1e3'}).status_code, 200)
        self.assertEqual(self.record().freight_amount, Decimal('1000.00'))

    def test_half_a_character_in_what_staff_type_is_refused(self):
        client = acting(self.manager)
        for field in ('recipient_name', 'agency_name', 'agency_locality', 'waybill_number', 'tracking_code'):
            with self.subTest(field):
                response = client.patch(self.url(self.order, 'panel'), data='{"%s": "Ana \\ud800"}' % field,
                                        content_type='application/json')
                self.assertEqual(response.status_code, 400, response.content)
                self.assertIn(field, response.json())
        self.assertEqual(self.entries(), [])

    def test_half_a_character_in_a_carriers_name_is_refused(self):
        response = acting(self.admin).post(CARRIERS, data='{"name": "Agencia \\udfff", "kind": "agency"}',
                                           content_type='application/json')
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn('name', response.json())
        self.assertEqual(Carrier.objects.filter(company=self.a).count(), 2)


class HalfACharacterInTheBookTest(_BookBase):
    """P2-1 · a lone surrogate is not text a database stores: refused where control characters are."""

    def test_it_is_refused_in_every_free_text_of_an_entry(self):
        for field in ('label', 'recipient_name', 'address_line', 'district', 'city', 'department', 'reference'):
            with self.subTest(field):
                body = json.dumps({**door(), field: 'Ana MARK'}).replace('MARK', '\\ud800')
                cache.clear()
                response = self.web.post(WEB_BOOK, data=body, content_type='application/json', **self.host())
                self.assertEqual(response.status_code, 400, response.content)
                self.assertIn(field, response.json())
        for field in ('agency_name', 'agency_locality'):
            with self.subTest(field):
                body = json.dumps({**self.office(), field: 'Centro MARK'}).replace('MARK', '\\udc00')
                cache.clear()
                response = self.web.post(WEB_BOOK, data=body, content_type='application/json', **self.host())
                self.assertEqual(response.status_code, 400, response.content)
                self.assertIn(field, response.json())
        self.assertFalse(CustomerAddress.objects.exists())

    def test_a_whole_character_outside_the_basic_plane_is_text(self):
        entry = self.add(door(recipient_name='Ana \U0001F600 Torres'))
        self.assertEqual(entry.recipient_name, 'Ana \U0001F600 Torres')


class TheTrackingLinkFollowsWhatItWasMadeFromTest(_StaffDeliveryBase):
    """
    P2-2 · a link the system DERIVED from carrier and waybill is theirs: it
    moves with them and is gone when one of them is. A link somebody TYPED is
    that person's: only the link itself being sent changes it.
    """

    def setUp(self):
        super().setUp()
        self.other = self.carrier(name='Otra Agencia', kind='agency', tracking_url_template='https://otra.ejemplo.test/t/{guia}')

    def link(self):
        return self.record().tracking_url

    def buyer_link(self):
        rows = v1_session(self.buyer).get('/api/v1/customer/envios-a/orders/').json()
        return [row['delivery'] for row in rows if row['id'] == self.order.pk][0]['tracking_url']

    def test_a_derived_link_follows_the_carrier(self):
        self.write(self.manager, {'carrier': self.agency.pk, 'waybill_number': 'G-1'})
        self.assertEqual((self.link(), self.record().tracking_url_typed), ('https://rastreo.ejemplo.test/g/G-1', False))
        self.assertEqual(self.write(self.manager, {'carrier': self.other.pk}).status_code, 200)    # the panel sends only what changed
        self.assertEqual((self.link(), self.buyer_link()), ('https://otra.ejemplo.test/t/G-1',) * 2)
        self.write(self.manager, {'carrier': self.courier.pk})                                     # a carrier with no page
        self.assertEqual((self.link(), self.buyer_link()), ('', ''))
        self.write(self.manager, {'carrier': self.agency.pk})
        self.assertEqual(self.link(), 'https://rastreo.ejemplo.test/g/G-1')
        self.write(self.manager, {'carrier': None})
        self.assertEqual((self.link(), self.buyer_link()), ('', ''))

    def test_a_derived_link_follows_the_waybill(self):
        self.write(self.manager, {'carrier': self.agency.pk, 'waybill_number': 'G-1'})
        self.write(self.manager, {'waybill_number': 'G-2'})
        self.assertEqual(self.link(), 'https://rastreo.ejemplo.test/g/G-2')
        self.write(self.manager, {'waybill_number': ''})
        self.assertEqual((self.link(), self.buyer_link()), ('', ''))
        self.write(self.manager, {'waybill_number': 'G-3'})
        self.assertEqual(self.link(), 'https://rastreo.ejemplo.test/g/G-3')

    def test_a_carrier_given_after_the_waybill_makes_the_link(self):
        self.write(self.manager, {'waybill_number': 'G-1'})
        self.assertEqual(self.link(), '')
        self.write(self.manager, {'carrier': self.agency.pk})
        self.assertEqual(self.link(), 'https://rastreo.ejemplo.test/g/G-1')

    def test_a_typed_link_is_only_changed_by_typing_it(self):
        typed = 'https://courier.ejemplo.test/mi-envio/abc'
        self.write(self.manager, {'carrier': self.courier.pk, 'waybill_number': 'W1', 'tracking_url': typed})
        self.assertEqual((self.link(), self.record().tracking_url_typed), (typed, True))
        for body in ({'waybill_number': 'W2'}, {'carrier': self.agency.pk}, {'waybill_number': ''}, {'carrier': None},
                     {'carrier': self.other.pk, 'waybill_number': 'W3'}, {'tracking_code': 'CLAVE'}):
            with self.subTest(body):
                self.assertEqual(self.write(self.manager, body).status_code, 200)
                self.assertEqual((self.link(), self.buyer_link()), (typed, typed))
        again = 'https://courier.ejemplo.test/mi-envio/xyz'
        self.write(self.manager, {'tracking_url': again})
        self.assertEqual((self.link(), self.record().tracking_url_typed), (again, True))

    def test_taking_a_typed_link_off_goes_back_to_the_carriers_own(self):
        self.write(self.manager, {'carrier': self.agency.pk, 'waybill_number': 'G-1', 'tracking_url': 'https://courier.ejemplo.test/x'})
        self.write(self.manager, {'tracking_url': ''})                      # nothing else is sent
        self.assertEqual((self.link(), self.record().tracking_url_typed), ('https://rastreo.ejemplo.test/g/G-1', False))
        self.write(self.manager, {'waybill_number': 'G-2'})
        self.assertEqual(self.link(), 'https://rastreo.ejemplo.test/g/G-2')
        self.write(self.manager, {'tracking_url': 'https://courier.ejemplo.test/x'})
        self.write(self.manager, {'tracking_url': None, 'carrier': self.courier.pk})
        self.assertEqual((self.link(), self.record().tracking_url_typed), ('', False))

    def test_typing_the_carriers_own_page_is_not_typing_a_link(self):
        # A client that sends the whole record back does not freeze the link.
        self.write(self.manager, {'carrier': self.agency.pk, 'waybill_number': 'G-1'})
        self.write(self.manager, {'tracking_url': 'https://rastreo.ejemplo.test/g/G-1', 'tracking_code': 'CLAVE'})
        self.assertFalse(self.record().tracking_url_typed)
        self.write(self.manager, {'waybill_number': 'G-2'})
        self.assertEqual(self.link(), 'https://rastreo.ejemplo.test/g/G-2')

    def test_a_derived_link_is_not_made_again_while_what_it_came_from_stays(self):
        # The record is a copy: a carrier that changes its page later does not rewrite orders that left.
        self.write(self.manager, {'carrier': self.agency.pk, 'waybill_number': 'G-1'})
        Carrier.objects.filter(pk=self.agency.pk).update(tracking_url_template='https://nueva.ejemplo.test/{guia}')
        self.write(self.manager, {'tracking_code': 'CLAVE', 'carrier': self.agency.pk, 'waybill_number': 'G-1'})
        self.assertEqual(self.link(), 'https://rastreo.ejemplo.test/g/G-1')

    def test_a_refused_link_changes_nothing(self):
        self.write(self.manager, {'carrier': self.agency.pk, 'waybill_number': 'G-1'})
        self.assertEqual(self.write(self.manager, {'tracking_url': 'http://otra.ejemplo.test/x', 'waybill_number': 'G-9'}).status_code, 400)
        record = self.record()
        self.assertEqual((record.tracking_url, record.tracking_url_typed, record.waybill_number),
                         ('https://rastreo.ejemplo.test/g/G-1', False, 'G-1'))


class TheBookOfAnArchivedFileTest(_BookBase):
    """P3 · a file the shop archived takes nothing new from its subject, and nothing says why."""

    def setUp(self):
        super().setUp()
        self.entry = self.add(door())
        Customer.objects.filter(user=self.ana).update(is_active=False)
        self.audits = AdminAuditLog.objects.count()

    def assert_quiet(self, response):
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(list(response.json()), ['detail'])
        for hint in ('archiv', 'is_active', 'inactiv', 'bloque'):
            self.assertNotIn(hint, response.content.decode().lower())

    def test_it_takes_no_new_entry(self):
        self.assert_quiet(self.call('post', body=door(label='Otra')))
        self.assert_quiet(self.call('post', body=self.office()))
        self.assertEqual(CustomerAddress.objects.count(), 1)
        self.assertEqual(AdminAuditLog.objects.count(), self.audits)

    def test_nor_a_new_address_written_over_an_old_one(self):
        kept = CustomerAddress.objects.filter(pk=self.entry.pk).values().get()
        self.assert_quiet(self.call('patch', f'{self.entry.pk}/', {'address_line': 'Otra calle 9'}))
        self.assertEqual(CustomerAddress.objects.filter(pk=self.entry.pk).values().get(), kept)
        self.assertEqual(AdminAuditLog.objects.count(), self.audits)

    def test_what_it_had_is_still_read(self):
        self.assertEqual([row['id'] for row in self.book()], [self.entry.pk])

    def test_an_account_with_no_file_is_not_mistaken_for_one(self):
        self.assertEqual(self.call('post', body=door(), client=web_session(self.luis)).status_code, 201)


class ABookDoesNotGrowWithoutEndTest(_BookBase):
    """P3 · removed entries stay as rows: a buyer holds a bounded number of them, kept or removed."""

    def test_there_is_a_ceiling_on_every_row_a_buyer_ever_made(self):
        self.assertEqual(delivery.MAX_ADDRESS_ROWS, 200)
        entry = self.add(door())
        customer = entry.customer
        CustomerAddress.objects.bulk_create([
            CustomerAddress(company=self.a, customer=customer, recipient_name='Ana', address_line='x', district='y',
                            city='z', is_active=False)
            for _ in range(delivery.MAX_ADDRESS_ROWS - 2)
        ])
        self.assertEqual(self.call('post', body=door(label='La última')).status_code, 201)
        self.assertEqual(CustomerAddress.objects.filter(customer=customer).count(), delivery.MAX_ADDRESS_ROWS)
        # At the ceiling a new entry takes the place of the OLDEST removed row: no row is added, nobody is refused.
        oldest = CustomerAddress.objects.filter(customer=customer, is_active=False).order_by('pk').first()
        CustomerAddress.objects.filter(pk=oldest.pk).update(
            label='Vieja', reference='Dato de antes', recipient_phone='911111111', department='Antes', is_default=False)
        response = self.call('post', body={'kind': 'address', 'recipient_name': 'Ana Torres', 'address_line': 'Av. Nueva 9',
                                           'district': 'Cayma', 'city': 'Arequipa'})
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()['id'], oldest.pk)
        self.assertEqual(CustomerAddress.objects.filter(customer=customer).count(), delivery.MAX_ADDRESS_ROWS)
        oldest.refresh_from_db()
        # Nothing of what the row used to say is left on it.
        self.assertEqual((oldest.is_active, oldest.label, oldest.reference, oldest.recipient_phone, oldest.department, oldest.address_line),
                         (True, '', '', '', '', 'Av. Nueva 9'))
        self.assertEqual(AdminAuditLog.objects.filter(action='customer_address_created', target_id=str(oldest.pk)).count(), 1)
        # What is there can still be corrected, made the default and removed.
        self.assertEqual(self.call('patch', f'{entry.pk}/', {'label': 'Corregida'}).status_code, 200)
        self.assertEqual(self.call('delete', f'{entry.pk}/').status_code, 204)

    def test_the_row_reused_is_the_buyers_own(self):
        other = Customer.objects.create(company=self.a, user=self.luis, first_name='Luis')
        theirs = CustomerAddress.objects.create(company=self.a, customer=other, recipient_name='Luis', address_line='Jr. Ajeno 1',
                                                district='y', city='z', is_active=False)          # older than every row of mine
        customer = Customer.objects.create(company=self.a, user=self.ana, first_name='Ana')
        CustomerAddress.objects.bulk_create([
            CustomerAddress(company=self.a, customer=customer, recipient_name='Ana', address_line='x', district='y',
                            city='z', is_active=False)
            for _ in range(delivery.MAX_ADDRESS_ROWS)
        ])
        response = self.call('post', body=door())
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(CustomerAddress.objects.get(pk=response.json()['id']).customer, customer)
        theirs.refresh_from_db()
        self.assertEqual((theirs.customer, theirs.is_active, theirs.address_line), (other, False, 'Jr. Ajeno 1'))

    def test_with_every_row_removed_the_buyer_is_not_locked_out(self):
        customer = Customer.objects.create(company=self.a, user=self.ana, first_name='Ana')
        CustomerAddress.objects.bulk_create([
            CustomerAddress(company=self.a, customer=customer, recipient_name='Ana', address_line='x', district='y',
                            city='z', is_active=False)
            for _ in range(delivery.MAX_ADDRESS_ROWS)
        ])
        response = self.call('post', body=door())
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(CustomerAddress.objects.filter(customer=customer).count(), delivery.MAX_ADDRESS_ROWS)
        [entry] = self.book()
        self.assertTrue(entry['is_default'])
        # The ten-entry limit is still what refuses.
        for n in range(delivery.MAX_ACTIVE_ADDRESSES - 1):
            self.assertEqual(self.call('post', body=door(label=f'Casa {n}')).status_code, 201)
        self.assertEqual(self.call('post', body=door(label='De más')).status_code, 400)

    def test_another_buyers_rows_do_not_count(self):
        other = Customer.objects.create(company=self.a, user=self.luis, first_name='Luis')
        CustomerAddress.objects.bulk_create([
            CustomerAddress(company=self.a, customer=other, recipient_name='Luis', address_line='x', district='y',
                            city='z', is_active=False)
            for _ in range(delivery.MAX_ADDRESS_ROWS)
        ])
        self.assertEqual(self.call('post', body=door()).status_code, 201)


class AnAgencyEntryOutlivesItsCarrierTest(_BookBase):
    """P3 · an entry that names an agency no longer offered can still be corrected; it cannot be GIVEN one."""

    def setUp(self):
        super().setUp()
        self.entry = self.add(self.office())
        Carrier.objects.filter(pk=self.agency.pk).update(is_active=False)

    def change(self, body):
        return self.call('patch', f'{self.entry.pk}/', body)

    def test_everything_but_the_carrier_can_be_corrected(self):
        for body in ({'label': 'Mi agencia'}, {'agency_name': 'Agencia Norte'}, {'recipient_phone': '987654321'},
                     {'label': 'Otra', 'carrier': self.agency.pk}):                # the same carrier sent back
            with self.subTest(body):
                self.assertEqual(self.change(body).status_code, 200, body)
        self.entry.refresh_from_db()
        self.assertEqual((self.entry.label, self.entry.agency_name, self.entry.carrier), ('Otra', 'Agencia Norte', self.agency))

    def test_a_carrier_is_judged_when_it_is_the_carrier_that_changes(self):
        other = self.carrier(name='Otra Agencia', kind='agency')
        off = self.carrier(name='Apagada', kind='agency', is_active=False)
        courier = self.carrier(name='A domicilio', kind='courier')
        foreign = self.carrier(company=self.b, name='Ajena', kind='agency')
        for bad in (off.pk, courier.pk, foreign.pk, 999999, None, True, str(self.agency.pk)):
            with self.subTest(bad):
                response = self.change({'carrier': bad})
                self.assertEqual(response.status_code, 400, response.content)
                self.assertIn('carrier', response.json())
        self.assertEqual(self.change({'carrier': other.pk}).status_code, 200)
        # Once left, the switched-off one cannot be chosen again.
        self.assertEqual(self.change({'carrier': self.agency.pk}).status_code, 400)

    def test_a_door_cannot_become_an_agency_by_keeping_no_carrier(self):
        house = self.add(door(label='Casa'))
        response = self.call('patch', f'{house.pk}/', {'kind': 'agency', 'agency_name': 'Centro', 'agency_locality': 'Puno'})
        self.assertEqual(response.status_code, 400)
        self.assertIn('carrier', response.json())

    def test_a_new_entry_cannot_name_it(self):
        self.assertEqual(self.call('post', body=self.office(label='Nueva')).status_code, 400)


class WhoWroteTheDeliveryTest(_StaffDeliveryBase):
    """P3 · the card names a person, or nobody in particular — never a login."""

    def test_someone_with_no_name_is_staff_of_the_shop(self):
        from store import order_risk

        self.manager.first_name = self.manager.last_name = ''
        self.manager.save()
        body = self.write(self.manager, {'waybill_number': 'G-1'}).json()
        self.assertEqual(body['updated_by'], order_risk.STAFF_WITHOUT_NAME)
        self.assertNotIn(self.manager.username, json.dumps(body))
        self.assertNotIn(self.manager.username, json.dumps(self.read(self.viewer).json()))

    def test_someone_with_a_name_is_named(self):
        self.manager.first_name, self.manager.last_name = 'Gema', 'Gestora'
        self.manager.save()
        self.assertEqual(self.write(self.manager, {'waybill_number': 'G-1'}).json()['updated_by'], 'Gema Gestora')


# ---------------------------------------------------------------------------
# 8 · after the third independent review
# ---------------------------------------------------------------------------


class ALinkSentBackIsNotALinkTypedTest(_StaffDeliveryBase):
    """F6 · a client that sends the record back with one field changed has not typed the link it was shown."""

    def setUp(self):
        super().setUp()
        self.write(self.manager, {'carrier': self.agency.pk, 'waybill_number': 'G-1'})
        self.shown = self.record().tracking_url
        self.assertEqual(self.shown, 'https://rastreo.ejemplo.test/g/G-1')

    def test_a_new_waybill_with_the_old_derived_link_sent_back(self):
        body = self.write(self.manager, {'waybill_number': 'G-2', 'tracking_url': self.shown}).json()
        record = self.record()
        self.assertEqual((record.tracking_url, record.tracking_url_typed), ('https://rastreo.ejemplo.test/g/G-2', False))
        self.assertEqual((body['tracking_url'], body['tracking_url_typed']), ('https://rastreo.ejemplo.test/g/G-2', False))

    def test_a_new_carrier_with_the_old_derived_link_sent_back(self):
        other = self.carrier(name='Otra Agencia', kind='agency', tracking_url_template='https://otra.ejemplo.test/t/{guia}')
        self.write(self.manager, {'carrier': other.pk, 'tracking_url': self.shown})
        record = self.record()
        self.assertEqual((record.tracking_url, record.tracking_url_typed), ('https://otra.ejemplo.test/t/G-1', False))

    def test_the_same_link_sent_back_alone_changes_nothing(self):
        Carrier.objects.filter(pk=self.agency.pk).update(tracking_url_template='https://nueva.ejemplo.test/{guia}')
        self.write(self.manager, {'tracking_url': self.shown, 'tracking_code': 'CLAVE'})
        record = self.record()
        self.assertEqual((record.tracking_url, record.tracking_url_typed), (self.shown, False))

    def test_a_typed_link_sent_back_is_still_the_typed_link(self):
        typed = 'https://courier.ejemplo.test/mi-envio/abc'
        self.write(self.manager, {'tracking_url': typed})
        body = self.write(self.manager, {'waybill_number': 'G-2', 'tracking_url': typed}).json()
        self.assertEqual((body['tracking_url'], body['tracking_url_typed']), (typed, True))
        self.assertTrue(self.read(self.viewer).json()['tracking_url_typed'])

    def test_another_link_typed_over_a_derived_one_is_typed(self):
        self.write(self.manager, {'waybill_number': 'G-2', 'tracking_url': 'https://courier.ejemplo.test/x'})
        record = self.record()
        self.assertEqual((record.tracking_url, record.tracking_url_typed), ('https://courier.ejemplo.test/x', True))


class HalfACharacterInALinkTest(_StaffDeliveryBase):
    """F7 · the two addresses that become links refuse half a character like every other text."""

    def test_the_orders_tracking_address(self):
        response = acting(self.manager).patch(self.url(self.order, 'panel'), data='{"tracking_url": "https://uno.test/\\ud800"}',
                                              content_type='application/json')
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn('tracking_url', response.json())
        self.assertEqual(self.record().tracking_url, '')

    def test_a_carriers_tracking_template(self):
        response = acting(self.admin).post(
            CARRIERS, data='{"name": "Uno", "kind": "courier", "tracking_url_template": "https://uno.test/\\udfff/{guia}"}',
            content_type='application/json')
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn('tracking_url_template', response.json())
        self.assertFalse(Carrier.objects.filter(name='Uno').exists())


class TheBookDoesNotFollowTheRecordToAnotherAccountTest(_BookBase):
    """F5 · when the shop unlinks an account from a record, the addresses that account wrote stop being anybody's book."""

    def test_unlinking_switches_the_book_off(self):
        from store import tracking_services as tracking

        first, second = self.add(door()), self.add(self.office())
        record = first.customer
        staff = self.admin
        tracking.unlink_account(record, actor=staff, reason='cuenta equivocada')

        self.assertEqual(self.book(), [])
        self.assertEqual(
            list(CustomerAddress.objects.filter(customer=record).order_by('pk').values_list('pk', 'is_active', 'is_default')),
            [(first.pk, False, False), (second.pk, False, False)],
        )
        # The next account linked to that record starts with an empty book.
        Customer.objects.filter(pk=record.pk).update(user=self.luis)
        self.assertEqual(self.book(client=web_session(self.luis)), [])
        entry = AdminAuditLog.objects.get(action='customer_account_unlinked')
        self.assertEqual(entry.metadata['addresses_removed'], 2)
        for value in ('Av. Siempre Viva 123', 'Ana Torres', '987654321'):
            self.assertNotIn(value, json.dumps(entry.metadata))

    def test_a_record_that_had_no_account_keeps_what_it_has(self):
        from store import tracking_services as tracking

        record = Customer.objects.create(company=self.a, first_name='Sin cuenta')
        row = CustomerAddress.objects.create(company=self.a, customer=record, recipient_name='x', address_line='x', district='y', city='z')
        tracking.unlink_account(record, actor=self.admin, reason='nada que desvincular')
        self.assertTrue(CustomerAddress.objects.get(pk=row.pk).is_active)

    def test_another_records_book_is_not_touched(self):
        from store import tracking_services as tracking

        mine = self.add(door())
        other = Customer.objects.create(company=self.a, user=self.luis, first_name='Luis')
        tracking.unlink_account(other, actor=self.admin, reason='otra ficha')
        self.assertTrue(CustomerAddress.objects.get(pk=mine.pk).is_active)
