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
