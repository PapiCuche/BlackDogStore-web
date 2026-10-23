"""
INV-USED-EXT-01 (docs/adr-inv-used-ext.md) — external identified used devices.

  * A card is ONE device a supplier still has. It is never a StockUnit; it
    hangs off the supplier's one offer for that used variant, whose quantity
    is the number of live cards.
  * The generic used offer (no cards) still never sells.
  * Public (owner D3): model, variant, condition, price, lead time. Never the
    reported IMEI, serial, battery or grade, nor the supplier.
  * Reception (owner D4): the received device must be the card's. A mismatch
    writes nothing, opens ONE incident and blocks the line; only a superior
    resolves it (rejected); nothing is substituted; nothing fiscal moves.
"""
import json

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from store import device_catalog
from store import external_used
from store import supply_services as supply
from store.models import (
    AdminAuditLog, ExternalUsedCard, FiscalDocument, OrderItem, OrderUnit, Product, StockMovement, StockUnit,
    SupplierOffer, SupplyReceptionIncident,
)
from store.test_stock_units import imei
from store.test_supply_reception import ReceptionShop, as_client
from store.tests import _p2d_member, _p3_company, _prod

IMEIS = [imei(f'35693803588{n:03d}') for n in range(20)]
CARDS = '/api/admin/external-used-cards/'
INCIDENTS = '/api/admin/supply-incidents/'


class ExtShop(ReceptionShop):
    def build(self, slug='ext-tienda'):
        super().build(slug)
        capacity = device_catalog.create_attribute(company=self.company, name='Capacidad')
        self.gb128 = device_catalog.create_option(company=self.company, attribute_id=capacity.pk, label='128 GB')
        self.model = device_catalog.create_model(company=self.company, name='Teléfono U')
        used = _prod(self.company, 'Teléfono U 128 GB seminuevo', f'{slug}-u-128', price='1.00', inventory=0)
        Product.objects.filter(pk=used.pk).update(is_serialized=True, requires_imei=True)
        self.used = device_catalog.set_variant(used, model_id=self.model.pk, condition_line=Product.CONDITION_USED,
                                               option_ids=[self.gb128.pk])
        # Its mode stays «stock»: the generic used offer never sells; only live cards do (ADR).
        self.api = as_client(self.staff)

    def card(self, n=0, **extra):
        values = dict(supplier_id=self.supplier.pk, product_id=self.used.pk, reported_imei=IMEIS[n],
                      reported_serial=f'SERU{n:04d}', reported_battery=88, commercial_price='1500.00',
                      agreed_cost='1100.00', valid_from=str(self.start), valid_until=str(self.end), lead_time_days=3)
        values.update(extra)
        return external_used.create_card(company=self.company, actor=self.staff, may_view_costs=True, **values)

    def offer_of(self, card):
        return SupplierOffer.objects.get(pk=card.offer_id)

    def counts(self):
        return (StockUnit.objects.count(), StockMovement.objects.count(), OrderUnit.objects.count(),
                FiscalDocument.objects.count())

    def receive(self, order, *units, user=None):
        return as_client(user or self.staff).post(self.url(order), {'units': list(units)}, format='json')


class _Ext(ExtShop, TestCase):
    def setUp(self):
        from datetime import timedelta
        from store.test_supplier_offers import today

        self.start, self.end = today(), today() + timedelta(days=6)
        self.build()


# ---------------------------------------------------------------------------
# D2 · the card and its offer
# ---------------------------------------------------------------------------

class CardTest(_Ext):
    def test_the_first_card_opens_the_variants_offer_and_the_next_one_joins_it(self):
        first = self.card(0)
        offer = self.offer_of(first)
        self.assertEqual((offer.product_id, offer.confirmed_quantity, offer.committed_quantity), (self.used.pk, 1, 0))
        second = self.card(1, commercial_price=None, agreed_cost=None)
        self.assertEqual(second.offer_id, offer.pk)
        self.assertEqual(self.offer_of(second).confirmed_quantity, 2)
        self.assertEqual(StockUnit.objects.count(), 0)            # a card is never a device on a shelf

    def test_the_price_is_the_offers_not_the_cards(self):
        self.card(0)
        with self.assertRaises(external_used.CardError) as caught:
            self.card(1, commercial_price='1400.00')
        self.assertEqual(caught.exception.code, 'price_is_per_offer')

    def test_a_card_names_its_device(self):
        for bad in ({'reported_imei': '', 'reported_serial': ''}, {'reported_imei': '123'},
                    {'reported_imei': IMEIS[0][:-1] + str((int(IMEIS[0][-1]) + 1) % 10)},
                    {'reported_battery': 101}, {'reported_imei': ''}):   # this variant requires an IMEI
            with self.assertRaises(external_used.CardError, msg=bad):
                self.card(0, **bad)
        self.assertEqual(ExternalUsedCard.objects.count(), 0)

    def test_the_same_device_is_never_offered_twice_nor_one_already_ours(self):
        self.card(0)
        with self.assertRaises(external_used.CardError) as caught:
            self.card(0, reported_serial='OTRA0001')
        self.assertEqual(caught.exception.code, 'duplicate')
        from store import stock_unit_services as units

        units.receive_units(branch=self.branch, product=self.used, actor=self.staff, reason='Compra',
                            units=[{'serial_number': 'PROPIO01', 'imei': IMEIS[5]}])
        with self.assertRaises(external_used.CardError) as caught:
            self.card(5)
        self.assertEqual(caught.exception.code, 'already_ours')

    def test_another_companys_supplier_is_not_found_even_with_our_product(self):
        other = _p3_company('ext-otra3', 'Otra')
        foreign = supply.create_supplier(company=other, name='Ajeno', actor=None)
        with self.assertRaises(external_used.CardError) as caught:
            self.card(0, supplier_id=foreign.pk)
        self.assertEqual(caught.exception.code, 'not_found')

    def test_only_a_used_variant_takes_cards(self):
        with self.assertRaises(external_used.CardError):
            self.card(0, product_id=self.phone.pk)

    def test_withdrawing_an_available_card_takes_it_off_the_offer(self):
        card = self.card(0)
        self.card(1, commercial_price=None, agreed_cost=None)
        external_used.withdraw_card(card, actor=self.staff, reason='Lo vendió el proveedor')
        card.refresh_from_db()
        self.assertEqual((card.status, self.offer_of(card).confirmed_quantity), ('withdrawn', 1))
        with self.assertRaises(external_used.CardError):
            external_used.withdraw_card(card, actor=self.staff, reason='otra vez')

    def test_the_generic_used_offer_stays_forbidden(self):
        with self.assertRaises(supply.SupplyError):
            supply.create_offer(company=self.company, supplier_id=self.supplier.pk, product_id=self.used.pk,
                                commercial_price='1000', confirmed_quantity=5, valid_from=self.start,
                                valid_until=self.end, lead_time_days=3)
        card = self.card(0)
        with self.assertRaises(supply.SupplyError):
            supply.update_offer(self.offer_of(card), confirmed_quantity=9)

    def test_a_used_offer_without_live_cards_never_sells(self):
        self.assertFalse(supply.sells_on_order(self.used))
        card = self.card(0)
        self.assertTrue(supply.sells_on_order(self.used))
        self.assertEqual(supply.availability(self.used, self.branch).origin, 'supplier')
        ExternalUsedCard.objects.filter(pk=card.pk).update(status='withdrawn')   # quantity left behind
        self.assertIsNone(supply.availability(self.used, self.branch).origin)
        self.assertIsNone(supply.offer_for_line(self.used, 1))
        self.assertFalse(supply.sells_on_order(self.used))


class CardApiTest(_Ext):
    def test_create_list_and_withdraw_without_ever_showing_the_identifiers(self):
        res = self.api.post(CARDS, {
            'supplier_id': self.supplier.pk, 'product_id': self.used.pk, 'reported_imei': IMEIS[0],
            'reported_serial': 'SERU0000', 'reported_battery': 90, 'commercial_price': '1500.00',
            'agreed_cost': '1100.00', 'valid_from': str(self.start), 'valid_until': str(self.end),
            'lead_time_days': 3}, format='json')
        self.assertEqual(res.status_code, 201, res.content)
        body = res.json()
        self.assertEqual((body['status'], body['imei_hint'], body['pending_verification']),
                         ('available', '•••• ' + IMEIS[0][-4:], True))
        listed = self.api.get(CARDS).json()['results']
        text = json.dumps(listed)
        self.assertNotIn(IMEIS[0], text)
        self.assertNotIn('SERU0000', text)
        res = self.api.post(f'{CARDS}{body["id"]}/withdraw/', {'reason': 'Ya no lo tiene'}, format='json')
        self.assertEqual((res.status_code, res.json()['status']), (200, 'withdrawn'))

    def test_who_may_do_what(self):
        viewer = as_client(_p2d_member(self.company, 'ext-viewer', ['company.view', 'products.view'])[0])
        self.assertEqual(viewer.get(CARDS).status_code, 200)
        self.assertEqual(viewer.post(CARDS, {'supplier_id': self.supplier.pk}, format='json').status_code, 403)
        card = self.card(0)
        self.assertEqual(viewer.post(f'{CARDS}{card.pk}/withdraw/', {'reason': 'x'}, format='json').status_code, 403)
        self.assertNotIn('agreed_cost', json.dumps(viewer.get(CARDS).json()))
        self.assertIn('agreed_cost', json.dumps(self.api.get(CARDS).json()))

    def test_another_companys_rows_are_not_found(self):
        card = self.card(0)
        other = _p3_company('ext-otra', 'Otra')
        outsider = as_client(_p2d_member(other, 'ext-outsider', ['company.view', 'products.view',
                                                                 'products.manage'])[0])
        self.assertEqual(outsider.get(CARDS).json()['results'], [])
        self.assertEqual(outsider.post(f'{CARDS}{card.pk}/withdraw/', {'reason': 'x'}, format='json').status_code,
                         404)
        res = outsider.post(CARDS, {'supplier_id': self.supplier.pk, 'product_id': self.used.pk,
                                    'reported_imei': IMEIS[3], 'commercial_price': '1', 'valid_from': str(self.start),
                                    'valid_until': str(self.end), 'lead_time_days': 1}, format='json')
        self.assertEqual(res.status_code, 404)


# ---------------------------------------------------------------------------
# Selling: the existing on-order path, one card per device
# ---------------------------------------------------------------------------

class SaleTest(_Ext):
    def test_the_payment_reserves_a_card_for_the_line_and_never_sells_it_twice(self):
        card = self.card(0)
        first = self.paid_order(self.used, session='c1')
        line = first.items.get()
        card.refresh_from_db()
        self.assertEqual((line.supply_status, card.status, card.order_item_id), ('pending', 'reserved', line.pk))
        self.assertEqual(StockUnit.objects.count(), 0)
        # The only card is gone: the variant is not offered any more, and a cart taken before is «por confirmar».
        self.assertIsNone(supply.availability(self.used, self.branch).origin)

    def test_a_late_payment_finds_no_card_and_waits_for_a_person(self):
        self.card(0)
        self.add_on_order(self.used, session='c1')
        self.add_on_order(self.used, session='c2')
        early, late = self.checkout('c1'), self.checkout('c2')
        self.pay(early)
        self.pay(late)
        self.assertEqual(late.items.get().supply_status, 'to_confirm')
        self.assertEqual(ExternalUsedCard.objects.filter(status='reserved').count(), 1)

    def test_a_supplier_failure_withdraws_the_card_an_ordinary_release_gives_it_back(self):
        card = self.card(0)
        order = self.paid_order(self.used, session='c1')
        supply.fail_line(order.items.get(), actor=self.staff, reason='No lo tenía', cause='supplier_failure')
        card.refresh_from_db()
        self.assertEqual((card.status, self.offer_of(card).confirmed_quantity), ('withdrawn', 0))
        other = self.card(1)
        order = self.paid_order(self.used, session='c2')
        supply.fail_line(order.items.get(), actor=self.staff, reason='Decidimos no venderlo', cause='other')
        other.refresh_from_db()
        self.assertEqual((other.status, other.order_item_id), ('available', None))
        self.assertEqual(supply.availability(self.used, self.branch).origin, 'supplier')


@override_settings(ALLOWED_HOSTS=['.tienda.pe', 'testserver'])
class PublicTest(_Ext):
    ALLOWED = {'product_id', 'slug', 'name', 'options', 'price', 'lead_time_days', 'condition_label', 'available'}

    def test_the_public_page_says_model_variant_condition_price_and_lead_time_only(self):
        self.card(0, reported_battery=77, notes='Pantalla con rayón')
        body = APIClient(HTTP_HOST=self.host).get(f'/api/catalog/models/{self.model.slug}/').json()
        rows = body['used_on_order']
        self.assertEqual(len(rows), 1)
        self.assertEqual(set(rows[0]), self.ALLOWED)
        self.assertEqual((rows[0]['price'], rows[0]['lead_time_days']), ('1500.00', 3))
        text = json.dumps(body)
        for secret in (IMEIS[0], IMEIS[0][-4:], 'SERU0000', 'Pantalla', 'Mayorista', '1100', '"77"', 'battery'):
            self.assertNotIn(secret, text)
        self.assertFalse(body['sold_out'])

    def test_without_live_cards_there_is_nothing_on_order(self):
        card = self.card(0)
        external_used.withdraw_card(card, actor=self.staff, reason='Vendido')
        body = APIClient(HTTP_HOST=self.host).get(f'/api/catalog/models/{self.model.slug}/').json()
        self.assertEqual(body['used_on_order'], [])


# ---------------------------------------------------------------------------
# D4 · reception
# ---------------------------------------------------------------------------

class ReceptionTest(_Ext):
    def setUp(self):
        super().setUp()
        self.the_card = self.card(0)
        self.order = self.paid_order(self.used, session='c1')

    def superior(self):
        user, _ = _p2d_member(self.company, 'ext-jefe', ['company.view', 'company.manage', 'sales.orders.view',
                                                         'sales.orders.manage', 'inventory.adjust'])
        return as_client(user)

    def test_the_cards_device_is_received_and_named(self):
        res = self.receive(self.order, {'serial_number': 'SERU0000', 'imei': IMEIS[0]})
        self.assertEqual(res.status_code, 200, res.content)
        self.the_card.refresh_from_db()
        unit = StockUnit.objects.get(imei=IMEIS[0])
        self.assertEqual((self.the_card.status, self.the_card.received_unit_id), ('received', unit.pk))
        self.assertTrue(OrderUnit.objects.filter(order=self.order, unit=unit).exists())

    def test_another_device_writes_nothing_and_opens_one_audited_incident(self):
        before = self.counts()
        res = self.receive(self.order, {'serial_number': 'OTRO0001', 'imei': IMEIS[9]})
        self.assertEqual((res.status_code, res.json()['code']), (409, 'identity_mismatch'), res.content)
        self.assertEqual(self.counts(), before)
        self.assertNotIn(IMEIS[0], res.content.decode())                 # the check is blind
        incident = SupplyReceptionIncident.objects.get()
        self.assertEqual((incident.status, incident.order_item_id, incident.company_id),
                         ('open', self.order.items.get().pk, self.company.pk))
        self.assertEqual(res.json()['incident_id'], incident.pk)
        log = AdminAuditLog.objects.get(action='supply_reception_incident_opened')
        self.assertEqual(log.company_id, self.company.pk)
        self.assertNotIn(IMEIS[9], json.dumps(log.metadata))
        self.assertEqual(self.order.items.get().supply_status, 'pending')

    def test_an_open_incident_blocks_the_line_even_for_the_right_device(self):
        self.receive(self.order, {'serial_number': 'OTRO0001', 'imei': IMEIS[9]})
        again = self.receive(self.order, {'serial_number': 'OTRO0002', 'imei': IMEIS[8]})
        self.assertEqual((again.status_code, again.json()['code']), (409, 'incident_open'))
        right = self.receive(self.order, {'serial_number': 'SERU0000', 'imei': IMEIS[0]})
        self.assertEqual((right.status_code, right.json()['code']), (409, 'incident_open'))
        self.assertEqual(SupplyReceptionIncident.objects.count(), 1)
        self.assertEqual(StockUnit.objects.count(), 0)

    def test_only_a_superior_rejects_and_then_the_right_device_is_received(self):
        self.receive(self.order, {'serial_number': 'OTRO0001', 'imei': IMEIS[9]})
        incident = SupplyReceptionIncident.objects.get()
        url = f'{INCIDENTS}{incident.pk}/reject/'
        self.assertEqual(self.api.post(url, {'reason': 'No es el equipo'}, format='json').status_code, 403)
        boss = self.superior()
        self.assertEqual(boss.post(url, {'reason': ''}, format='json').status_code, 400)
        res = boss.post(url, {'reason': 'El proveedor mandó otro equipo: se devuelve'}, format='json')
        self.assertEqual((res.status_code, res.json()['status']), (200, 'rejected'), res.content)
        self.assertEqual(boss.post(url, {'reason': 'otra vez'}, format='json').status_code, 409)
        self.the_card.refresh_from_db()
        self.assertEqual((self.the_card.status, self.the_card.order_item_id), ('reserved', self.order.items.get().pk))
        self.assertTrue(AdminAuditLog.objects.filter(action='supply_reception_incident_rejected',
                                                     company=self.company).exists())
        self.assertEqual(self.receive(self.order, {'serial_number': 'SERU0000', 'imei': IMEIS[0]}).status_code, 200)

    def test_the_incident_list_is_the_companys_and_masks_what_arrived(self):
        self.receive(self.order, {'serial_number': 'OTRO0001', 'imei': IMEIS[9]})
        rows = self.api.get(INCIDENTS).json()['results']
        self.assertEqual(len(rows), 1)
        self.assertNotIn(IMEIS[9], json.dumps(rows))
        self.assertNotIn(IMEIS[0], json.dumps(rows))
        other = _p3_company('ext-otra2', 'Otra')
        outsider = as_client(_p2d_member(other, 'ext-out2', ['company.view', 'company.manage', 'sales.orders.view',
                                                              'sales.orders.manage'])[0])
        self.assertEqual(outsider.get(INCIDENTS).json()['results'], [])
        self.assertEqual(outsider.post(f'{INCIDENTS}{rows[0]["id"]}/reject/', {'reason': 'x'},
                                       format='json').status_code, 404)

    def test_nothing_fiscal_and_no_substitution_ever(self):
        fiscal = FiscalDocument.objects.count()
        self.receive(self.order, {'serial_number': 'OTRO0001', 'imei': IMEIS[9]})
        boss = self.superior()
        incident = SupplyReceptionIncident.objects.get()
        boss.post(f'{INCIDENTS}{incident.pk}/reject/', {'reason': 'Se devuelve'}, format='json')
        self.assertEqual(FiscalDocument.objects.count(), fiscal)
        self.assertFalse(StockUnit.objects.filter(imei=IMEIS[9]).exists())

    def test_a_wrong_reception_overtaken_by_the_right_one_opens_nothing(self):
        # The interleaving of a race, made deterministic: the wrong device was refused, and before its
        # incident could be opened the right one was received. Nothing is left to block.
        from unittest import mock

        self.assertEqual(self.receive(self.order, {'serial_number': 'SERU0000', 'imei': IMEIS[0]}).status_code, 200)
        line = self.order.items.get()
        self.assertIsNone(external_used.open_incident(line=line, actor=self.staff, card_ids=[self.the_card.pk],
                                                      received=[{'imei': IMEIS[9], 'serial_number': 'OTRO0001'}]))
        refused = external_used.IdentityMismatch('otro equipo')
        refused.card_ids, refused.received = [self.the_card.pk], [{'imei': IMEIS[9], 'serial_number': 'OTRO0001'}]
        with mock.patch.object(supply, '_receive_line', side_effect=refused):
            with self.assertRaises(supply.SupplyError) as caught:
                supply.receive_line(line, actor=self.staff, units=[{'serial_number': 'OTRO0001', 'imei': IMEIS[9]}])
        self.assertEqual(caught.exception.code, 'already_received')
        self.assertEqual(SupplyReceptionIncident.objects.count(), 0)

    def test_the_same_reception_twice_is_told_it_already_happened(self):
        self.assertEqual(self.receive(self.order, {'serial_number': 'SERU0000', 'imei': IMEIS[0]}).status_code, 200)
        again = self.receive(self.order, {'serial_number': 'SERU0000', 'imei': IMEIS[0]})
        self.assertEqual((again.status_code, again.json()['code']), (409, 'already_received'))
        self.assertEqual(StockUnit.objects.count(), 1)

    def test_a_card_reported_by_serial_only_is_matched_by_serial(self):
        Product.objects.filter(pk=self.used.pk).update(requires_imei=False)
        self.used.refresh_from_db()
        card = self.card(4, reported_imei='', reported_serial='SOLOSERIE1', commercial_price=None, agreed_cost=None)
        order = self.paid_order(self.used, session='c4')
        self.assertEqual(self.receive(order, {'serial_number': 'OTRASERIE1'}).json()['code'], 'identity_mismatch')
        boss = self.superior()
        boss.post(f'{INCIDENTS}{SupplyReceptionIncident.objects.get().pk}/reject/', {'reason': 'No'}, format='json')
        self.assertEqual(self.receive(order, {'serial_number': 'SOLOSERIE1'}).status_code, 200)
        card.refresh_from_db()
        self.assertEqual(card.status, 'received')


class SnapshotTest(_Ext):
    def test_a_complete_weekly_list_never_switches_off_card_offers(self):
        from store import supplier_offer_import

        card = self.card(0)
        snapshot = supplier_offer_import.snapshot_absent(self.company, self.supplier, set(), on=self.start)
        self.assertNotIn(card.offer_id, [row['offer_id'] for row in snapshot['absent']])
