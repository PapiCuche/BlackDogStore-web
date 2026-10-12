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
