"""
DEVICE-COMMERCE-01 · DC3 — proveedores y venta bajo pedido.

Lo que estas pruebas fijan:

  * stock 0 NO es vendible: sólo con el modo «Bajo pedido» del producto Y una
    oferta de proveedor vigente, activa, confirmada y con saldo que cubra la
    línea entera (nunca mezcla stock propio y pedido en una línea);
  * la oferta se elige siempre igual (precio comercial más bajo, luego costo
    pactado, luego la que vence antes, luego id);
  * el pago de una línea bajo pedido NO mueve stock: compromete la oferta bajo
    bloqueo; si la oferta venció o se agotó, el pago se registra igual y la
    línea queda «abastecimiento por confirmar»;
  * recibir del proveedor escribe la entrada (al costo congelado) y la salida
    de venta de esa línea: el estante queda igual y el Kardex lo cuenta;
  * las instantáneas (origen, oferta, plazo, costo) no cambian después;
  * todo es por empresa y el costo sólo lo ve `costs.view`.
"""
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

from django.core.cache import cache
from django.db import transaction
from django.test import TestCase
from django.utils import timezone

from store import checkout_services as checkout
from store import inventory_services as inventory
from store import supply_services as supply
from store.models import (
    AdminAuditLog, CartItem, Order, OrderItem, PaymentTransaction, Product, StockMovement, StockUnit,
    Supplier, SupplierOffer,
)
from store.tests import _p2d_member, _p3_company, _prod

D = Decimal


def today():
    return timezone.localdate()


class SupplyShop:
    """A shop with an accessory sold on order, a supplier and an offer for it."""

    def build(self, slug='dc3-tienda'):
        cache.clear()
        self.company = _p3_company(slug, 'Tienda DC3')
        self.branch = self.company.default_inventory_branch
        self.host = f'{slug}.tienda.pe'
        self.staff, _ = _p2d_member(self.company, f'{slug}_staff', [
            'company.view', 'products.view', 'products.manage', 'inventory.view', 'inventory.adjust',
            'sales.orders.view', 'sales.orders.manage', 'costs.view'])
        self.case = _prod(self.company, 'Cargador 20W', f'{slug}-cargador', price='120.00', inventory=0)
        Product.objects.filter(pk=self.case.pk).update(sale_mode=Product.SALE_MODE_STOCK_OR_ORDER)
        self.case.refresh_from_db()
        self.supplier = supply.create_supplier(company=self.company, name='Mayorista Lima', actor=self.staff)
        self.offer = self.make_offer()

    def make_offer(self, product=None, **extra):
        values = dict(agreed_cost='70.00', commercial_price='110.00', confirmed_quantity=5,
                      valid_from=today() - timedelta(days=1), valid_until=today() + timedelta(days=6),
                      lead_time_days=4)
        values.update(extra)
        return supply.create_offer(company=self.company, supplier_id=self.supplier.pk,
                                   product_id=(product or self.case).pk, actor=self.staff,
                                   may_view_costs=True, **values)

    def stock(self, product, n):
        inventory.create_stock_movement(branch=self.branch, product_id=product.pk,
                                        movement_type=StockMovement.PURCHASE_ENTRY, quantity=n, reason='Compra',
                                        unit_cost=D('60'))

    def shelf(self, product):
        return inventory.branch_quantity(self.branch, product)

    def add_on_order(self, product=None, quantity=1, session='carrito-a'):
        return CartItem.objects.create(session_key=session, product=product or self.case, quantity=quantity,
                                       supply_origin=CartItem.SUPPLY_SUPPLIER)

    def checkout(self, session='carrito-a'):
        items = list(CartItem.objects.filter(session_key=session).select_related('product'))
        lines = checkout.lines_from_cart(self.company, self.branch, session, items)
        subtotal = checkout.validate_lines_and_subtotal(self.branch, lines)
        pricing = checkout.price_checkout(self.company, subtotal)
        return checkout.create_pending_order(
            company=self.company, branch=self.branch, lines=lines, pricing=pricing,
            details=checkout.CustomerDetails(
                name='Ana', email='ana@example.com', phone='987654321', document_type='DNI',
                document_number='12345678', delivery_method='pickup', receipt_type='boleta',
                accepted_terms=True, accepted_warranty_policy=True),
            cart_session_key=session,
        )

    def pay(self, order):
        """The real `_confirm`, with a gateway answer that passed its checks."""
        from store.views import _SignedNotificationMixin

        attempt = PaymentTransaction.objects.create(
            order=order, provider='izipay', transaction_id=f'T{order.pk:019d}', amount=order.total, currency='PEN')
        result = SimpleNamespace(signed=True, response_code='00', pay_method='CARD', authorization_code='A1',
                                 reference_number='R1', unique_id=f'U{order.pk}')
        outcome = _SignedNotificationMixin()._confirm(attempt, result)
        order.refresh_from_db()
        return outcome


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------

class AvailabilityMatrixTest(SupplyShop, TestCase):
    """stock / no stock × mode × offer state."""

    def setUp(self):
        self.build()

    def answer(self, quantity=1):
        return supply.availability(self.case, self.branch, quantity)

    def test_own_stock_always_sells_from_the_shelf(self):
        self.stock(self.case, 3)
        result = self.answer(3)
        self.assertEqual((result.origin, result.offer, result.price), ('own', None, D('120.00')))

    def test_no_stock_with_mode_and_a_valid_offer_is_on_order(self):
        result = self.answer(2)
        self.assertEqual((result.origin, result.offer, result.lead_time_days, result.price),
                         ('supplier', self.offer, 4, D('110.00')))

    def test_no_stock_without_the_mode_is_not_sellable(self):
        Product.objects.filter(pk=self.case.pk).update(sale_mode=Product.SALE_MODE_STOCK)
        self.case.refresh_from_db()
        self.assertIsNone(self.answer().origin)

    def test_offers_that_do_not_count(self):
        cases = {
            'expired': dict(valid_until=today() - timedelta(days=1), valid_from=today() - timedelta(days=9)),
            'not_yet': dict(valid_from=today() + timedelta(days=1)),
            'unconfirmed': dict(confirmed_quantity=None),
            'inactive': dict(is_active=False),
        }
        for label, change in cases.items():
            with self.subTest(label):
                SupplierOffer.objects.filter(pk=self.offer.pk).update(**{
                    'valid_from': today() - timedelta(days=1), 'valid_until': today() + timedelta(days=6),
                    'confirmed_quantity': 5, 'is_active': True, **change})
                self.assertIsNone(self.answer().origin)
        SupplierOffer.objects.filter(pk=self.offer.pk).update(
            valid_from=today() - timedelta(days=1), valid_until=today(), confirmed_quantity=5, is_active=True)
        self.assertEqual(self.answer().origin, 'supplier', 'valid through its last day')

    def test_an_exhausted_offer_or_one_that_does_not_cover_the_line_does_not_count(self):
        SupplierOffer.objects.filter(pk=self.offer.pk).update(committed_quantity=5)
        self.assertIsNone(self.answer().origin)
        SupplierOffer.objects.filter(pk=self.offer.pk).update(committed_quantity=3)
        self.assertEqual(self.answer(2).origin, 'supplier')
        self.assertIsNone(self.answer(3).origin)

    def test_an_inactive_supplier_sells_nothing(self):
        Supplier.objects.filter(pk=self.supplier.pk).update(is_active=False)
        self.assertIsNone(self.answer().origin)

    def test_partial_own_stock_is_never_mixed_the_whole_line_goes_on_order(self):
        self.stock(self.case, 1)
        result = self.answer(3)
        self.assertEqual(result.origin, 'supplier')
        self.assertEqual(result.own_stock, 1)

    def test_the_offer_is_chosen_the_same_way_every_time(self):
        cheap = self.make_offer(commercial_price='100.00', agreed_cost='80.00', lead_time_days=9)
        tie_cheaper_cost = self.make_offer(commercial_price='100.00', agreed_cost='75.00', lead_time_days=7)
        self.assertEqual(self.answer().offer, tie_cheaper_cost)
        SupplierOffer.objects.filter(pk=tie_cheaper_cost.pk).update(agreed_cost=D('80.00'),
                                                                   valid_until=today() + timedelta(days=9))
        self.assertEqual(self.answer().offer, cheap)          # same price and cost: the one that ends first
        SupplierOffer.objects.filter(pk=tie_cheaper_cost.pk).update(valid_until=today() + timedelta(days=6))
        self.assertEqual(self.answer().offer, cheap)          # all equal: the lowest id

    def test_another_companys_offer_never_counts(self):
        other = _p3_company('dc3-otra', 'Otra')
        foreign = _prod(other, 'Cargador 20W', 'dc3-otra-cargador', price='120.00', inventory=0)
        with self.assertRaises(supply.SupplyError):
            supply.create_offer(company=other, supplier_id=self.supplier.pk, product_id=foreign.pk,
                                agreed_cost='1', commercial_price='1', confirmed_quantity=1,
                                valid_from=today(), valid_until=today(), lead_time_days=1, actor=None)
        with self.assertRaises(supply.SupplyError):
            supply.create_offer(company=self.company, supplier_id=self.supplier.pk, product_id=foreign.pk,
                                agreed_cost='1', commercial_price='1', confirmed_quantity=1,
                                valid_from=today(), valid_until=today(), lead_time_days=1, actor=None)


class OfferRulesTest(SupplyShop, TestCase):
    def setUp(self):
        self.build()

    def test_bad_offers_are_refused(self):
        bad = [
            dict(valid_until=today() - timedelta(days=2)),            # ends before it starts
            dict(commercial_price='0'),
            dict(commercial_price='-1'),
            dict(agreed_cost='-5'),
            dict(confirmed_quantity=-1),
            dict(lead_time_days=-1),
            dict(lead_time_days=400),
        ]
        for change in bad:
            with self.subTest(change), self.assertRaises(supply.SupplyError):
                self.make_offer(**change)

    def test_without_costs_view_the_cost_is_not_written(self):
        offer = supply.create_offer(
            company=self.company, supplier_id=self.supplier.pk, product_id=self.case.pk, agreed_cost='50.00',
            commercial_price='100.00', confirmed_quantity=1, valid_from=today(), valid_until=today(),
            lead_time_days=2, actor=self.staff, may_view_costs=False)
        self.assertIsNone(offer.agreed_cost)

    def test_the_confirmed_quantity_cannot_drop_below_what_was_sold(self):
        SupplierOffer.objects.filter(pk=self.offer.pk).update(committed_quantity=3)
        self.offer.refresh_from_db()
        with self.assertRaises(supply.SupplyError):
            supply.update_offer(self.offer, actor=self.staff, confirmed_quantity=2, may_view_costs=True)
        supply.update_offer(self.offer, actor=self.staff, confirmed_quantity=3, may_view_costs=True)

    def test_history_is_audited_and_a_used_offer_is_never_deleted(self):
        supply.update_offer(self.offer, actor=self.staff, commercial_price='105.00', may_view_costs=True)
        supply.deactivate_offer(self.offer, actor=self.staff, reason='Proveedor sin stock')
        actions = list(AdminAuditLog.objects.filter(target_type='supplier_offer', target_id=str(self.offer.pk))
                       .order_by('pk').values_list('action', flat=True))
        self.assertEqual(actions, ['supplier_offer_created', 'supplier_offer_updated', 'supplier_offer_deactivated'])
        self.offer.refresh_from_db()
        self.assertFalse(self.offer.is_active)

    def test_a_used_variant_is_never_sold_on_order(self):
        Product.objects.filter(pk=self.case.pk).update(condition_line=Product.CONDITION_USED)
        self.case.refresh_from_db()
        with self.assertRaises(supply.SupplyError):
            supply.set_sale_mode(self.case, Product.SALE_MODE_STOCK_OR_ORDER, actor=self.staff)


# ---------------------------------------------------------------------------
# Checkout, payment, reception
# ---------------------------------------------------------------------------

class OnOrderSaleTest(SupplyShop, TestCase):
    def setUp(self):
        self.build()

    def test_checkout_freezes_origin_offer_lead_time_cost_and_price_without_committing(self):
        self.add_on_order(quantity=2)
        order = self.checkout()
        [line] = order.items.all()
        self.assertEqual((line.supply_origin, line.supplier_offer_id, line.lead_time_days),
                         ('supplier', self.offer.pk, 4))
        self.assertEqual((line.price, line.unit_cost, line.cost_source), (D('110.00'), D('70.0000'), 'supplier_offer'))
        self.assertEqual(order.total, D('220.00'))
        self.offer.refresh_from_db()
        self.assertEqual(self.offer.committed_quantity, 0)        # a checkout commits nothing

    def test_checkout_refuses_an_on_order_line_without_a_valid_offer(self):
        self.add_on_order(quantity=6)                             # the offer has 5
        with self.assertRaises(checkout.CheckoutError):
            self.checkout()
        CartItem.objects.all().delete()
        self.add_on_order(quantity=1)
        SupplierOffer.objects.filter(pk=self.offer.pk).update(is_active=False)
        with self.assertRaises(checkout.CheckoutError):
            self.checkout()
        self.assertFalse(Order.objects.exists())

    def test_a_shelf_line_without_stock_is_refused_even_with_an_offer(self):
        """Stock 0 does not mean sellable: the buyer has to take the line on order."""
        CartItem.objects.create(session_key='carrito-a', product=self.case, quantity=1)
        with self.assertRaises(checkout.CheckoutError):
            self.checkout()

    def test_payment_commits_the_offer_and_moves_no_stock(self):
        self.add_on_order(quantity=2)
        order = self.checkout()
        self.assertEqual(self.pay(order), 'paid')
        self.assertEqual(order.status, Order.Status.PAID)
        [line] = order.items.all()
        self.assertEqual(line.supply_status, 'pending')
        self.offer.refresh_from_db()
        self.assertEqual(self.offer.committed_quantity, 2)
        self.assertFalse(StockMovement.objects.filter(product=self.case).exists())
        self.assertEqual(self.shelf(self.case), 0)
        # Not a shortfall: nothing was owed from the shelf.
        self.assertEqual(inventory.order_stock_shortfall(order), [])
        # A replayed notification commits nothing twice.
        from store.views import _SignedNotificationMixin
        attempt = order.payment_transactions.get()
        _SignedNotificationMixin()._confirm(attempt, SimpleNamespace(
            signed=True, response_code='00', pay_method='CARD', authorization_code='A1', reference_number='R1',
            unique_id='U'))
        self.offer.refresh_from_db()
        self.assertEqual(self.offer.committed_quantity, 2)

    def test_an_offer_that_expired_before_the_payment_keeps_the_money_and_asks_to_confirm(self):
        self.add_on_order(quantity=1)
        order = self.checkout()
        SupplierOffer.objects.filter(pk=self.offer.pk).update(valid_until=today() - timedelta(days=1),
                                                              valid_from=today() - timedelta(days=5))
        self.assertEqual(self.pay(order), 'paid')
        self.assertTrue(order.paid)
        [line] = order.items.all()
        self.assertEqual(line.supply_status, 'to_confirm')
        self.assertIn('abastecimiento por confirmar', order.payment_error.lower())
        self.offer.refresh_from_db()
        self.assertEqual(self.offer.committed_quantity, 0)
        self.assertTrue(AdminAuditLog.objects.filter(action='order_supply_to_confirm', target_id=str(order.pk)).exists())
        self.assertFalse(StockMovement.objects.filter(product=self.case).exists())

    def test_an_exhausted_offer_at_payment_asks_to_confirm(self):
        self.add_on_order(quantity=2)
        order = self.checkout()
        SupplierOffer.objects.filter(pk=self.offer.pk).update(committed_quantity=4)
        self.pay(order)
        self.assertEqual(order.items.get().supply_status, 'to_confirm')
        self.offer.refresh_from_db()
        self.assertEqual(self.offer.committed_quantity, 4)

    def test_staff_reconfirm_a_line_against_a_valid_offer(self):
        self.add_on_order(quantity=1)
        order = self.checkout()
        SupplierOffer.objects.filter(pk=self.offer.pk).update(is_active=False)
        self.pay(order)
        line = order.items.get()
        fresh = self.make_offer(agreed_cost='72.00', commercial_price='115.00', lead_time_days=6)
        supply.confirm_line(line, actor=self.staff)
        line.refresh_from_db()
        fresh.refresh_from_db()
        self.assertEqual((line.supply_status, line.supplier_offer_id, line.unit_cost, line.lead_time_days),
                         ('pending', fresh.pk, D('72.0000'), 6))
        self.assertEqual(line.price, D('110.00'), 'the buyer paid what was paid')
        self.assertEqual(fresh.committed_quantity, 1)

    def test_receiving_writes_the_entry_at_the_frozen_cost_and_the_sale_exit(self):
        self.stock(self.case, 2)                      # the shelf has other units of its own
        CartItem.objects.all().delete()
        Product.objects.filter(pk=self.case.pk).update()
        self.add_on_order(quantity=3)                 # 2 on the shelf do not cover 3: the line is on order
        order = self.checkout()
        self.pay(order)
        SupplierOffer.objects.filter(pk=self.offer.pk).update(agreed_cost=D('999'))   # the offer changes later
        line = order.items.get()
        supply.receive_line(line, actor=self.staff)
        line.refresh_from_db()
        self.assertEqual((line.supply_status, line.unit_cost), ('received', D('70.0000')))
        entry = StockMovement.objects.get(product=self.case, order=order, movement_type='purchase_entry')
        exit_ = StockMovement.objects.get(product=self.case, order=order, movement_type='sale_exit')
        self.assertEqual((entry.quantity, entry.unit_cost, exit_.quantity), (3, D('70.0000'), 3))
        self.assertEqual(self.shelf(self.case), 2, 'net zero on the shelf')
        self.assertEqual(inventory.order_stock_shortfall(order), [])
        with self.assertRaises(supply.SupplyError):
            supply.receive_line(line, actor=self.staff)            # once
        # A reprocess of the order's exits does not sell the line again from the shelf.
        inventory.reprocess_order_stock_exit(order, actor=self.staff)
        self.assertEqual(StockMovement.objects.filter(order=order, movement_type='sale_exit').count(), 1)
        self.assertEqual(self.shelf(self.case), 2)

    def test_a_failed_supply_releases_the_offer_and_flags_the_order(self):
        self.add_on_order(quantity=2)
        order = self.checkout()
        self.pay(order)
        line = order.items.get()
        with self.assertRaises(supply.SupplyError):
            supply.fail_line(line, actor=self.staff, reason='')
        supply.fail_line(line, actor=self.staff, reason='El proveedor no tiene stock')
        line.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(line.supply_status, 'failed')
        self.assertIn('El proveedor no tiene stock', line.supply_note)
        self.offer.refresh_from_db()
        self.assertEqual(self.offer.committed_quantity, 0)
        self.assertEqual(order.status, Order.Status.PAID, 'the refund goes through the existing flow')
        self.assertIn('reembols', order.payment_error.lower())
        with self.assertRaises(supply.SupplyError):
            supply.receive_line(line, actor=self.staff)
        self.assertFalse(StockMovement.objects.filter(product=self.case).exists())

    def test_the_snapshot_does_not_follow_the_offer(self):
        self.add_on_order(quantity=1)
        order = self.checkout()
        self.pay(order)
        supply.update_offer(self.offer, actor=self.staff, agreed_cost='10.00', commercial_price='10.00',
                            lead_time_days=30, may_view_costs=True)
        line = order.items.get()
        self.assertEqual((line.unit_cost, line.price, line.lead_time_days), (D('70.0000'), D('110.00'), 4))

    def test_a_line_of_another_company_cannot_be_received_by_this_one(self):
        self.add_on_order(quantity=1)
        order = self.checkout()
        self.pay(order)
        other = _p3_company('dc3-ajena', 'Ajena')
        stranger, _ = _p2d_member(other, 'dc3_ajeno', ['inventory.adjust', 'sales.orders.manage'])
        with self.assertRaises(supply.SupplyError):
            supply.receive_line(order.items.get(), actor=stranger, company=other)


class MarginWithSupplierCostTest(SupplyShop, TestCase):
    def setUp(self):
        self.build()

    def test_the_margin_counts_the_frozen_supplier_cost(self):
        from rest_framework.test import APIClient
        self.add_on_order(quantity=2)
        order = self.checkout()
        self.pay(order)
        Order.objects.filter(pk=order.pk).update(paid_at=timezone.now())
        client = APIClient()
        admin, _ = _p2d_member(self.company, 'dc3_margen', ['company.view', 'sales.analytics.view', 'costs.view'])
        client.force_authenticate(user=admin)
        res = client.get('/api/admin/sales/margin/?days=7')
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        self.assertEqual(D(body['cost_of_goods_sold']), D('140.00'))
        self.assertEqual(body['pending_cost']['lines'], 0)
