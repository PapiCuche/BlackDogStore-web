"""
USED-UNIT-OWN-GATE-01 — a used device without a price blocks ITSELF, never its siblings.

The owner's rule: each used device (seminuevo) has its own price, inspection, publication, photos, availability,
reservation and sale. A device nobody has priced is not sold anywhere until somebody prices it, and nothing else
waits for it: the siblings of its variant that meet every requirement are sold at the till and offered on the web.

What these tests hold:

  * «Habilitar para la venta» (A1b-2) of a used draft asks for at least ONE available device with its own price, not
    for a price on every available device (`device_catalog._draft_problems`); with none priced it is still not ready,
    and the price is never the product's;
  * once enabled, the till sells the priced and approved sibling and refuses the unpriced one by itself; the web
    offers only the ready sibling, and a cart cannot hold the unpriced one;
  * removing the price of a device a cart holds sends the hold back to the shelf (Kardex balanced) and the checkout
    says the cart changed; after the checkout, the payment is not opened and, if the money arrives anyway, the device
    is not sold and the line is flagged — the sibling held by another buyer is untouched;
  * two buyers racing for the last eligible sibling: one gets it, the other is told it is taken; nobody gets the
    unpriced sibling; a price removed while a buyer holds the device never leaves a held device without a price;
  * a web sale that sets the oldest device aside (a used product without a model) takes only a priced and approved
    one, and the checkout counts only those;
  * the till's «available» for a product priced by device counts only the devices it may sell on THAT shelf;
  * another company's devices, prices and variants never count; nothing here moves stock.

Nada aquí es de Apple ni de la tienda piloto.
"""
from decimal import Decimal as D
from threading import Barrier, Thread

from django.db import connection
from django.test import TestCase, TransactionTestCase, override_settings

from store import checkout_services, device_catalog, inventory_services, pos_payloads, pos_services
from store import stock_unit_services as units
from store import unit_hold_services as holds
from store.models import AdminAuditLog, BranchStock, CartItem, Order, Product, ProductModel, StockMovement, StockUnit
from store.test_catalog_variant_draft import DraftShop, _unit
from store.test_device_commerce import SaleShop
from store.test_pos_unit_price import UsedBase
from store.test_stock_units import IMEI_A, IMEI_B, IMEI_C, IMEI_D
from store.used_unit_test_support import approve, ready_used


def assert_shelf_matches_units(case, product, branch):
    """`BranchStock.quantity == count(AVAILABLE)`: the invariant every test here keeps."""
    row = BranchStock.objects.filter(branch=branch, product=product).first()
    case.assertEqual(row.quantity if row else 0,
                     StockUnit.objects.filter(product=product, branch=branch, status=StockUnit.Status.AVAILABLE).count())


# ---------------------------------------------------------------------------
# Enabling a used draft: one priced device is enough, and it blocks nobody
# ---------------------------------------------------------------------------

class UsedDraftShop(DraftShop):
    def used_draft(self, options=None):
        product = self.priced(*(options or (self.gb256, self.blue)), line=Product.CONDITION_USED, price='0.00')
        Product.objects.filter(pk=product.pk).update(is_serialized=True, requires_imei=True)
        product.refresh_from_db()
        return product

    def two_siblings(self, product):
        """One device priced, one without a price — received in that order."""
        priced, = self.receive(product, 1, cost='800.00', sale_price='1500.00')
        unpriced, = self.receive(product, 1, cost='800.00')
        return priced, unpriced


@override_settings(ALLOWED_HOSTS=['.tienda.pe', 'testserver'])
class SiblingEnableTest(UsedDraftShop, TestCase):
    def setUp(self):
        self.build('own-gate-habilitar')
        ProductModel.objects.filter(pk=self.model.pk).update(is_published_online=True)
        self.model.refresh_from_db()

    def test_one_unpriced_device_does_not_block_enabling_its_priced_sibling(self):
        product = self.used_draft()
        self.two_siblings(product)
        res = self.enable(product)
        self.assertEqual(res.status_code, 200, res.content)
        product.refresh_from_db()
        self.assertFalse(product.is_pending_draft)
        # The product's own price is still not a price: nothing was invented to enable it.
        self.assertEqual(product.price, D('0.00'))

    def test_with_no_priced_device_it_is_still_not_ready(self):
        product = self.used_draft()
        self.receive(product, 2, cost='800.00')
        res = self.enable(product)
        self.assertEqual((res.status_code, res.json()['problems']), (400, ['no_price']))
        self.assertIn('equipo', res.json()['labels']['no_price'])
        # A zero written before the rule is not a price either.
        StockUnit.objects.filter(product=product).update(sale_price=D('0.00'))
        self.assertEqual(self.enable(product).json()['problems'], ['no_price'])
        product.refresh_from_db()
        self.assertTrue(product.is_pending_draft)

    def test_only_an_available_priced_device_counts(self):
        product = self.used_draft()
        priced, _unpriced = self.two_siblings(product)
        units.write_off(priced, reason='Pantalla rota')
        self.assertEqual(self.enable(product).json()['problems'], ['no_price'])

    def test_after_enabling_the_till_sells_the_priced_sibling_and_refuses_only_the_unpriced_one(self):
        product = self.used_draft()
        priced, unpriced = self.two_siblings(product)
        approve(priced)
        approve(unpriced)
        self.assertEqual(self.enable(product).status_code, 200)
        movements = StockMovement.objects.filter(product=product).count()
        with self.assertRaises(pos_services.PosValidationError) as caught:
            pos_services.build_pos_sale(operator=self.staff, company=self.company, branch=self.branch,
                                        items=[{'product': product.pk, 'quantity': 1, 'units': [_unit(unpriced)]}],
                                        validate_cash=False)
        self.assertIn('no tiene precio de venta', str(caught.exception))
        sale = pos_services.build_pos_sale(operator=self.staff, company=self.company, branch=self.branch,
                                           items=[{'product': product.pk, 'quantity': 1, 'units': [_unit(priced)]}],
                                           validate_cash=False)
        self.assertEqual(sale['total'], D('1500.00'))
        # The till's «available» counts the one it may sell.
        self.assertEqual(pos_payloads.product_payload(product, self.branch)['available'], 1)
        self.assertEqual(StockMovement.objects.filter(product=product).count(), movements)   # a preview moves nothing

    def test_after_enabling_the_web_offers_only_the_ready_sibling(self):
        product = self.used_draft()
        priced, unpriced = self.two_siblings(product)
        ready_used(priced)
        ready_used(unpriced)          # approved and photographed, but without a price it is never published
        self.assertEqual(self.enable(product, publish=True).status_code, 200)
        page = device_catalog.public_model(ProductModel.objects.get(pk=self.model.pk), self.branch)
        self.assertEqual([row['id'] for row in page['used_units']], [str(priced.public_id)])
        self.assertFalse(page['sold_out'])
        with self.assertRaises(holds.HoldError) as caught:
            holds.hold(product=Product.objects.get(pk=product.pk), branch=self.branch, session_key='carrito-x',
                       unit_public_id=str(unpriced.public_id))
        self.assertEqual(caught.exception.code, 'not_found')
        holds.hold(product=Product.objects.get(pk=product.pk), branch=self.branch, session_key='carrito-x',
                   unit_public_id=str(priced.public_id))
        self.assertEqual(CartItem.objects.get(session_key='carrito-x', product=product).quantity, 1)
        unpriced.refresh_from_db()
        self.assertEqual(unpriced.status, 'available')
        assert_shelf_matches_units(self, product, self.branch)

    def test_enabling_moves_no_stock(self):
        product = self.used_draft()
        self.two_siblings(product)
        before = (StockMovement.objects.filter(product=product).count(),
                  BranchStock.objects.get(branch=self.branch, product=product).quantity)
        self.assertEqual(self.enable(product).status_code, 200)
        self.assertEqual((StockMovement.objects.filter(product=product).count(),
                          BranchStock.objects.get(branch=self.branch, product=product).quantity), before)
        assert_shelf_matches_units(self, product, self.branch)


class SiblingEnableIsolationTest(UsedDraftShop, TestCase):
    """Another company's priced devices never enable this company's draft, and its draft is a 404 here."""

    def setUp(self):
        self.build('own-gate-aislada')
        self.foreign = UsedDraftShop()
        self.foreign.build('own-gate-ajena')

    def test_a_foreign_priced_device_does_not_count_and_a_foreign_draft_is_not_found(self):
        mine = self.used_draft()
        self.receive(mine, 1, cost='800.00')                       # unpriced
        theirs = self.foreign.used_draft()
        self.foreign.receive(theirs, 1, cost='800.00', sale_price='1500.00')
        self.assertEqual(self.enable(mine).json()['problems'], ['no_price'])
        # Our manager cannot enable theirs, nor read it as anything but «not found».
        res = self.manage_api.post(f'/api/admin/products/{theirs.pk}/enable-sale/{self.q}', {}, format='json')
        self.assertEqual(res.status_code, 404)
        theirs.refresh_from_db()
        self.assertTrue(theirs.is_pending_draft)
        self.assertEqual(self.foreign.enable(theirs).status_code, 200)
        mine.refresh_from_db()
        self.assertTrue(mine.is_pending_draft)
        self.assertFalse(AdminAuditLog.objects.filter(action='product_variant_enabled', company=self.company).exists())


# ---------------------------------------------------------------------------
# A held device loses its price
# ---------------------------------------------------------------------------

@override_settings(ALLOWED_HOSTS=['.tienda.pe', 'testserver'])
class PriceRemovedWhileHeldTest(SaleShop, TestCase):
    """`used_a` and `used_b` are siblings of one used variant, both ready. A buyer holds `used_a`."""

    def setUp(self):
        super().setUp()
        holds.hold(product=self.used128, branch=self.branch, session_key='carrito-a',
                   unit_public_id=str(self.used_a.public_id))

    def remove_price(self, unit):
        return units.update_unit(StockUnit.objects.get(pk=unit.pk), sale_price=None, actor=self.staff)

    def test_removing_the_price_releases_the_hold_and_the_checkout_says_the_cart_changed(self):
        self.remove_price(self.used_a)
        self.used_a.refresh_from_db()
        self.assertEqual((self.used_a.status, self.used_a.hold_key, self.used_a.sale_price), ('available', '', None))
        self.assertTrue(AdminAuditLog.objects.filter(action='stock_unit_web_hold_released',
                                                     target_id=str(self.used_a.pk), company=self.company).exists())
        assert_shelf_matches_units(self, self.used128, self.branch)
        orders = Order.objects.count()
        with self.assertRaises(checkout_services.CheckoutError) as caught:
            self.checkout()
        self.assertEqual(caught.exception.code, 'cart_changed')
        self.assertEqual(Order.objects.count(), orders)

    def test_the_sibling_held_by_another_buyer_is_untouched_and_still_sells(self):
        holds.hold(product=self.used128, branch=self.branch, session_key='carrito-b',
                   unit_public_id=str(self.used_b.public_id))
        self.remove_price(self.used_a)
        self.used_b.refresh_from_db()
        self.assertEqual((self.used_b.status, self.used_b.hold_key), ('reserved', holds.key_of('carrito-b')))
        order = self.checkout('carrito-b')
        self.pay(order)
        order.refresh_from_db()
        self.used_b.refresh_from_db()
        self.assertEqual((self.used_b.status, self.used_b.order_id), ('sold', order.pk))
        self.assertEqual(inventory_services.order_stock_shortfall(order), [])
        assert_shelf_matches_units(self, self.used128, self.branch)

    def test_a_price_change_keeps_the_hold_and_the_checkout_asks_to_review(self):
        units.update_unit(StockUnit.objects.get(pk=self.used_a.pk), sale_price='1800.00', actor=self.staff)
        self.used_a.refresh_from_db()
        self.assertEqual(self.used_a.status, 'reserved')
        with self.assertRaises(checkout_services.CheckoutError):
            self.checkout()

    def test_after_the_checkout_the_payment_is_not_opened(self):
        order = self.checkout()
        self.remove_price(self.used_a)
        self.used_a.refresh_from_db()
        # Bound to an order: the hold is left to it (as an unpublication), and the payment refuses to start.
        self.assertEqual((self.used_a.status, self.used_a.hold_order_id), ('reserved', order.pk))
        with self.assertRaises(checkout_services.CheckoutError) as caught:
            checkout_services.start_payment_attempt(order, credentials=None)
        self.assertEqual((caught.exception.code, caught.exception.status_code), ('unit_withdrawn', 409))
        # The order cannot be paid any more: it fails (as a payment that could not open), and the withdrawn device
        # goes back to the shelf at once, with its Kardex line — it is not left reserved until the hold runs out.
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.FAILED)
        self.used_a.refresh_from_db()
        self.assertEqual((self.used_a.status, self.used_a.hold_key, self.used_a.hold_order_id), ('available', '', None))
        self.assertTrue(AdminAuditLog.objects.filter(action='stock_unit_web_hold_released',
                                                     target_id=str(self.used_a.pk), company=self.company).exists())
        assert_shelf_matches_units(self, self.used128, self.branch)

    def test_a_withdrawn_device_fails_its_order_but_the_sibling_in_the_same_cart_is_bought_again(self):
        holds.hold(product=self.used128, branch=self.branch, session_key='carrito-a',
                   unit_public_id=str(self.used_b.public_id))
        order = self.checkout()
        self.assertEqual(order.items.count(), 2)
        self.remove_price(self.used_a)
        with self.assertRaises(checkout_services.CheckoutError):
            checkout_services.start_payment_attempt(order, credentials=None)
        self.used_b.refresh_from_db()
        # The sibling stays this cart's, as after any payment that could not open.
        self.assertEqual((self.used_b.status, self.used_b.hold_key), ('reserved', holds.key_of('carrito-a')))
        again = self.checkout()
        self.assertEqual([item.stock_unit_id for item in again.items.all()], [self.used_b.pk])
        self.pay(again)
        self.used_b.refresh_from_db()
        self.assertEqual((self.used_b.status, self.used_b.order_id), ('sold', again.pk))
        assert_shelf_matches_units(self, self.used128, self.branch)

    def test_if_the_money_arrives_anyway_the_device_is_not_sold_and_the_line_is_flagged(self):
        order = self.checkout()
        self.remove_price(self.used_a)
        self.pay(order)
        order.refresh_from_db()
        self.used_a.refresh_from_db()
        self.assertNotEqual(self.used_a.status, 'sold')
        self.assertIsNone(self.used_a.order_id)
        self.assertEqual([r['product_id'] for r in inventory_services.order_stock_shortfall(order)], [self.used128.pk])
        self.assertFalse(StockMovement.objects.filter(order=order, movement_type='sale_exit').exists())
        self.used_b.refresh_from_db()
        self.assertEqual(self.used_b.status, 'available')


# ---------------------------------------------------------------------------
# Races
# ---------------------------------------------------------------------------

@override_settings(ALLOWED_HOSTS=['.tienda.pe', 'testserver'])
class SiblingRaceTest(TransactionTestCase):
    """Real transactions and threads: two buyers, one eligible sibling; a price removed while a buyer holds."""

    def setUp(self):
        self.shop = SaleShop()
        self.shop.setUp()
        # `used_b` loses its price: `used_a` is the last eligible sibling of the variant.
        units.update_unit(StockUnit.objects.get(pk=self.shop.used_b.pk), sale_price=None)

    def run_together(self, *targets):
        barrier, outcomes = Barrier(len(targets)), []

        def wrap(target):
            def go():
                try:
                    barrier.wait(timeout=5)
                    outcomes.append(target())
                except holds.HoldError as exc:
                    outcomes.append(exc.code)
                finally:
                    connection.close()
            return go

        threads = [Thread(target=wrap(target)) for target in targets]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        return outcomes

    def test_two_buyers_for_the_last_eligible_sibling_one_gets_it_nobody_gets_the_unpriced_one(self):
        shop = self.shop

        def buy(session):
            def go():
                holds.hold(product=Product.objects.get(pk=shop.used128.pk), branch=shop.branch, session_key=session,
                           unit_public_id=str(shop.used_a.public_id))
                return 'held'
            return go

        outcomes = self.run_together(buy('carrito-1'), buy('carrito-2'))
        self.assertEqual(sorted(outcomes), ['held', 'unit_taken'])
        used_a = StockUnit.objects.get(pk=shop.used_a.pk)
        used_b = StockUnit.objects.get(pk=shop.used_b.pk)
        self.assertEqual(used_a.status, 'reserved')
        self.assertIn(used_a.hold_key, {holds.key_of('carrito-1'), holds.key_of('carrito-2')})
        self.assertEqual((used_b.status, used_b.hold_key), ('available', ''))
        # The loser asking for the unpriced sibling is refused too.
        with self.assertRaises(holds.HoldError) as caught:
            holds.hold(product=Product.objects.get(pk=shop.used128.pk), branch=shop.branch, session_key='carrito-3',
                       unit_public_id=str(shop.used_b.public_id))
        self.assertEqual(caught.exception.code, 'not_found')
        assert_shelf_matches_units(self, shop.used128, shop.branch)

    def test_a_price_removed_while_a_buyer_holds_never_leaves_a_held_device_without_a_price(self):
        shop = self.shop

        def hold():
            holds.hold(product=Product.objects.get(pk=shop.used128.pk), branch=shop.branch, session_key='carrito-1',
                       unit_public_id=str(shop.used_a.public_id))
            return 'held'

        def unprice():
            units.update_unit(StockUnit.objects.get(pk=shop.used_a.pk), sale_price=None)
            return 'unpriced'

        outcomes = self.run_together(hold, unprice)
        self.assertIn('unpriced', outcomes)
        used_a = StockUnit.objects.get(pk=shop.used_a.pk)
        self.assertIsNone(used_a.sale_price)
        # Whoever went first, the device ends on the shelf, not held without a price.
        self.assertEqual((used_a.status, used_a.hold_key), ('available', ''))
        self.assertFalse(CartItem.objects.filter(session_key='carrito-1', product=shop.used128).exists()
                         and holds.held_units('carrito-1', shop.company).exists())
        assert_shelf_matches_units(self, shop.used128, shop.branch)


# ---------------------------------------------------------------------------
# A web sale that sets the oldest device aside, and the till's count
# ---------------------------------------------------------------------------

class SetAsideAndTillCountTest(UsedBase):
    """A used product without a model: sold online by quantity, the payment sets devices aside."""

    def setUp(self):
        super().setUp()
        Product.objects.filter(pk=self.used.pk).update(is_published_online=True, price=D('2000.00'))
        self.used.refresh_from_db()
        # The OLDEST device is approved but has no price; the younger is priced and approved.
        self.oldest, self.priced = self.receive_used(
            self.row('ASIDE00001', IMEI_A, condition='used', cost='1000.00'),
            self.row('ASIDE00002', IMEI_B, condition='used', cost='1000.00', sale_price='2000.00'),
        )

    def order(self, quantity=1):
        order = Order.objects.create(company=self.company, total=D('2000.00') * quantity,
                                     fulfillment_branch=self.branch_a)
        order.items.create(product=self.used, quantity=quantity, price=D('2000.00'))
        return order

    def test_the_payment_sets_aside_only_a_priced_and_approved_device(self):
        order = self.order()
        inventory_services.create_stock_movement(
            branch=self.branch_a, product_id=self.used.pk, movement_type=StockMovement.SALE_EXIT, quantity=1,
            reason='Venta web', actor=self.staff, order=order, assign_units_for_sale=True)
        self.oldest.refresh_from_db()
        self.priced.refresh_from_db()
        self.assertEqual((self.oldest.status, self.priced.status), ('available', 'sold'))
        with self.assertRaises(inventory_services.InsufficientStockError):
            inventory_services.create_stock_movement(
                branch=self.branch_a, product_id=self.used.pk, movement_type=StockMovement.SALE_EXIT, quantity=1,
                reason='Venta web', actor=self.staff, order=self.order(), assign_units_for_sale=True)
        self.oldest.refresh_from_db()
        self.assertEqual(self.oldest.status, 'available')
        assert_shelf_matches_units(self, self.used, self.branch_a)

    def paid_with_one_set_aside(self):
        order = self.order()
        Order.objects.filter(pk=order.pk).update(status=Order.Status.PAID, paid=True)
        order.refresh_from_db()
        inventory_services.create_stock_movement(
            branch=self.branch_a, product_id=self.used.pk, movement_type=StockMovement.SALE_EXIT, quantity=1,
            reason='Venta web', actor=self.staff, order=order, assign_units_for_sale=True)
        return order

    def test_the_hand_over_does_not_swap_in_an_unpriced_shelf_device(self):
        from store import unit_identification

        order = self.paid_with_one_set_aside()
        self.priced.refresh_from_db()
        self.assertEqual((self.priced.status, self.priced.order_id), ('sold', order.pk))
        # Approved but without a price: the till would refuse it, the payment never sets it aside — nor does the
        # hand-over take it in place of the priced one.
        for attempt in (unit_identification.candidate_for_order, unit_identification.identify_for_order):
            with self.subTest(attempt.__name__), self.assertRaises(unit_identification.UnitIdentificationError) as caught:
                kwargs = {'actor': self.staff} if attempt is unit_identification.identify_for_order else {}
                attempt(order=order, code=IMEI_A, method='scanner', **kwargs)
            self.assertEqual((caught.exception.code, caught.exception.http_status), ('no_price', 409))
            self.assertIn('precio', caught.exception.detail)
        self.oldest.refresh_from_db()
        self.priced.refresh_from_db()
        self.assertEqual((self.oldest.status, self.priced.status, self.priced.order_id), ('available', 'sold', order.pk))
        # The one set aside is still named as always.
        unit_identification.identify_for_order(order=order, code=IMEI_B, method='scanner', actor=self.staff)
        assert_shelf_matches_units(self, self.used, self.branch_a)

    def test_the_hand_over_after_a_return_does_not_take_an_unpriced_shelf_device(self):
        from store import unit_identification

        order = self.paid_with_one_set_aside()
        # The device set aside came back before the hand-over (its order still waits for one): the second exit
        # takes one from the shelf — never the unpriced one.
        units.return_unit(StockUnit.objects.get(pk=self.priced.pk), reason='Volvió antes de entregarse')
        with self.assertRaises(unit_identification.UnitIdentificationError) as caught:
            unit_identification.identify_for_order(order=order, code=IMEI_A, method='scanner', actor=self.staff)
        self.assertEqual(caught.exception.code, 'no_price')
        self.oldest.refresh_from_db()
        self.assertEqual(self.oldest.status, 'available')
        assert_shelf_matches_units(self, self.used, self.branch_a)

    def test_the_checkout_counts_only_the_devices_it_may_sell(self):
        line = checkout_services.CheckoutLine(product=self.used, quantity=1)
        self.assertEqual(checkout_services.validate_lines_and_subtotal(self.branch_a, [line]), D('2000.00'))
        with self.assertRaises(checkout_services.CheckoutError) as caught:
            checkout_services.validate_lines_and_subtotal(
                self.branch_a, [checkout_services.CheckoutLine(product=self.used, quantity=2)])
        self.assertIn('Disponible: 1', ' '.join(caught.exception.errors))

    def test_the_tills_available_counts_only_what_it_may_sell_on_this_shelf(self):
        pending, = self.receive_used(self.row('ASIDE00003', IMEI_C, condition='used', sale_price='2100.00'),
                                     inspected=False)
        self.assertEqual(BranchStock.objects.get(branch=self.branch_a, product=self.used).quantity, 3)
        self.assertEqual(pos_payloads.product_payload(self.used, self.branch_a)['available'], 1)
        # A priced and approved device in ANOTHER branch is not on this shelf.
        other, = self.receive_used(self.row('ASIDE00004', IMEI_D, condition='used', sale_price='1900.00'),
                                   branch=self.branch_b)
        self.assertEqual(pos_payloads.product_payload(self.used, self.branch_a)['available'], 1)
        self.assertEqual(pos_payloads.product_payload(self.used, self.branch_b)['available'], 1)
        # Pricing the oldest makes it count, by itself.
        units.update_unit(self.oldest, sale_price='1950.00')
        self.assertEqual(pos_payloads.product_payload(self.used, self.branch_a)['available'], 2)
        # A new or unserialized product keeps its shelf count.
        self.assertEqual(pos_payloads.product_payload(self.cable, self.branch_a)['available'],
                         inventory_services.branch_quantity(self.branch_a, self.cable))
        self.assertIsNotNone(pending)
        self.assertIsNotNone(other)
