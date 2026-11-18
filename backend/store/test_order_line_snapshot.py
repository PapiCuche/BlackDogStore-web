"""
ORDERITEM-SNAPSHOT-01 — a sale line keeps what it sold.

Every surface that writes an `OrderItem` freezes, on the line, the product's
description, its variant in the catalogue's words (model, capacity, colour,
SIM, region, line new/used and the device's grade) and the catalogue price at
the moment of sale. The papers of the sale read the frozen description, so a
product renamed afterwards does not rewrite a note somebody already holds.
Lines written before this keep reading the live product.
"""
import re
from decimal import Decimal

from django.db import connection
from django.test import TestCase

from store import device_catalog
from store import order_line_snapshot as snapshot
from store import stock_unit_services as units
from store import unit_hold_services as holds
from store.email_services import build_order_confirmation_context
from store.models import CartItem, Order, OrderItem, Product, StockMovement, VariantAttribute
from store.printing import escpos
from store.sales_note_services import build_sales_note_context, generate_sales_note_pdf, get_or_create_sales_note
from store.serializers import AdminOrderItemSerializer
from store.test_device_commerce import SaleShop
from store.test_document_design import page_texts
from store.test_document_goldens import escpos_text
from store.test_supplier_offers import SupplyShop
from store.tests import _c1_sale, _p2d_member, _prod
from store.ticket_services import generate_sales_note_ticket_pdf

D = Decimal
MIGRATION = '0178_orderitem_line_snapshot'


def flat(text: str) -> str:
    return re.sub(r'\s+', ' ', text).strip()


class _Devices(SaleShop):
    """The DC2 shop, with its attributes carrying the roles the catalogue gives them."""

    def build(self, slug='snap-tienda'):
        super().build(slug)
        VariantAttribute.objects.filter(pk=self.capacity.pk).update(role=VariantAttribute.ROLE_CAPACITY)
        VariantAttribute.objects.filter(pk=self.colour.pk).update(role=VariantAttribute.ROLE_COLOR)
        self.case = _prod(self.company, 'Funda clara', 'snap-funda', price='59.90', inventory=0)
        from store import inventory_services as inventory
        inventory.create_stock_movement(branch=self.branch, product_id=self.case.pk,
                                        movement_type=StockMovement.PURCHASE_ENTRY, quantity=10, reason='Compra')

    def setUp(self):
        self.build()
        self.new_a, = self.receive(self.new128, 1)
        self.used_a, = self.receive(self.used128, 1)
        units.update_unit(self.used_a, sale_price='1999.00', cost='1100.00', cosmetic_grade=self.grade.pk)
        self.used_a.refresh_from_db()


class WebCheckoutSnapshotTest(_Devices, TestCase):
    def test_a_new_device_line_freezes_name_variant_and_catalogue_price(self):
        holds.hold(product=self.new128, branch=self.branch, session_key='carrito-a')
        order = self.checkout()
        line = order.items.get()
        self.assertEqual(line.description_snapshot, 'Teléfono X 128 GB Negro')
        self.assertEqual(line.variant_snapshot, {
            'model': 'Teléfono X', 'capacity': '128 GB', 'color': 'Negro',
            'condition': 'new', 'condition_label': 'Nuevo',
        })
        self.assertEqual(line.list_price_snapshot, D('3000.00'))

    def test_a_used_device_line_freezes_its_own_price_and_its_grade(self):
        holds.hold(product=self.used128, branch=self.branch, session_key='carrito-a',
                   unit_public_id=str(self.used_a.public_id))
        order = self.checkout()
        line = order.items.get()
        self.assertEqual(line.variant_snapshot['condition'], 'used')
        self.assertEqual(line.variant_snapshot['grade'], self.grade.label)
        self.assertEqual(line.list_price_snapshot, D('1999.00'))

    def test_an_accessory_line_freezes_its_name_and_price_and_no_variant(self):
        CartItem.objects.create(session_key='carrito-a', product=self.case, quantity=2)
        order = self.checkout()
        line = order.items.get()
        self.assertEqual((line.description_snapshot, line.variant_snapshot, line.list_price_snapshot),
                         ('Funda clara', {}, D('59.90')))


class WebOnOrderSnapshotTest(SupplyShop, TestCase):
    def setUp(self):
        self.build('snap-dc3')

    def test_an_on_order_line_freezes_the_catalogue_price_not_the_offer(self):
        self.add_on_order(quantity=2)
        line = self.checkout().items.get()
        self.assertEqual(line.price, D('110.00'))                       # sold at the offer's price
        self.assertEqual(line.list_price_snapshot, D('120.00'))         # the catalogue's, at that moment
        self.assertEqual(line.description_snapshot, 'Cargador 20W')

    def test_a_till_sale_on_order_freezes_too(self):
        cashier, _ = _p2d_member(self.company, 'snap_cajero_dc3', ['company.view', 'sales.pos.use'])
        order, _ = _c1_sale(actor=cashier, company=self.company, branch=self.branch,
                            items=[{'product': self.case.pk, 'quantity': 1, 'on_order': True}])
        line = order.items.get()
        self.assertEqual((line.description_snapshot, line.list_price_snapshot), ('Cargador 20W', D('120.00')))


class PosSnapshotTest(_Devices, TestCase):
    def setUp(self):
        super().setUp()
        self.cashier, _ = _p2d_member(self.company, 'snap_cajero', ['company.view', 'sales.pos.use'])

    def sell(self, items):
        order, _ = _c1_sale(actor=self.cashier, company=self.company, branch=self.branch, items=items)
        return order

    def test_every_till_line_freezes_its_snapshot(self):
        order = self.sell([
            {'product': self.case.pk, 'quantity': 2},
            {'product': self.new128.pk, 'quantity': 1, 'units': [{'id': self.new_a.pk, 'method': 'scanner'}]},
            {'product': self.used128.pk, 'quantity': 1, 'units': [{'id': self.used_a.pk, 'method': 'scanner'}]},
        ])
        lines = {line.product_id: line for line in order.items.all()}
        self.assertEqual((lines[self.case.pk].description_snapshot, lines[self.case.pk].variant_snapshot,
                          lines[self.case.pk].list_price_snapshot), ('Funda clara', {}, D('59.90')))
        self.assertEqual(lines[self.new128.pk].variant_snapshot['capacity'], '128 GB')
        self.assertEqual(lines[self.new128.pk].list_price_snapshot, D('3000.00'))
        used = lines[self.used128.pk]
        self.assertEqual((used.variant_snapshot['condition_label'], used.variant_snapshot['grade']),
                         ('Seminuevo', self.grade.label))
        self.assertEqual(used.list_price_snapshot, D('1999.00'))

    def test_an_unpriced_catalogue_line_has_no_list_price(self):
        free = _prod(self.company, 'Regalo', 'snap-regalo', price='0.00', inventory=0)
        self.assertIsNone(snapshot.fields_for(free)['list_price_snapshot'])


class OtherRoleTest(_Devices, TestCase):
    def test_an_attribute_without_a_role_is_kept_by_its_name(self):
        extra = device_catalog.create_attribute(company=self.company, name='Garantía extendida')
        option = device_catalog.create_option(company=self.company, attribute_id=extra.pk, label='24 meses')
        from store.models import ProductVariantOption
        ProductVariantOption.objects.create(company=self.company, product=self.new128, attribute=extra,
                                            option=option)
        variant = snapshot.fields_for(self.new128)['variant_snapshot']
        self.assertEqual(variant['other'], [{'name': 'Garantía extendida', 'value': '24 meses'}])


class ReprintKeepsTheLineTest(_Devices, TestCase):
    def setUp(self):
        super().setUp()
        self.cashier, _ = _p2d_member(self.company, 'snap_cajero_r', ['company.view', 'sales.pos.use'])

    def papers(self, note):
        return {
            'A4': flat(' '.join(page_texts(generate_sales_note_pdf(note)))),
            '80 mm': flat(' '.join(page_texts(generate_sales_note_ticket_pdf(note)))),
            'ESC/POS': flat(' '.join(escpos_text(escpos.sales_note_ticket(note)))),
        }

    def test_renaming_the_product_after_the_sale_does_not_change_the_reprinted_note(self):
        order, _ = _c1_sale(actor=self.cashier, company=self.company, branch=self.branch,
                            items=[{'product': self.case.pk, 'quantity': 1}])
        note, _ = get_or_create_sales_note(order, actor=self.cashier)
        Product.objects.filter(pk=self.case.pk).update(name='Funda renombrada', price=D('99.00'))
        self.assertEqual(build_sales_note_context(note)['items'][0]['name'], 'Funda clara')
        for paper, text in self.papers(note).items():
            with self.subTest(paper=paper):
                self.assertIn('Funda clara', text)
                self.assertNotIn('renombrada', text)
        context = build_order_confirmation_context(order)
        self.assertEqual(context['items'][0]['product_name'], 'Funda clara')

    def test_the_lines_of_the_note_carry_the_frozen_variant_and_list_price(self):
        order, _ = _c1_sale(actor=self.cashier, company=self.company, branch=self.branch, items=[
            {'product': self.new128.pk, 'quantity': 1, 'units': [{'id': self.new_a.pk, 'method': 'scanner'}]}])
        note, _ = get_or_create_sales_note(order, actor=self.cashier)
        [line] = build_sales_note_context(note)['items']
        self.assertEqual(line['variant']['model'], 'Teléfono X')
        self.assertEqual(line['list_price_snapshot'], D('3000.00'))

    def test_an_old_line_without_a_snapshot_still_prints_the_live_product(self):
        order = Order.objects.create(
            company=self.company, fulfillment_branch=self.branch, customer_name='Antiguo',
            total=D('59.90'), status=Order.Status.PAID, paid=True, payment_method='cash', sales_channel='pos')
        OrderItem.objects.create(order=order, product=self.case, quantity=1, price=D('59.90'))
        item = order.items.get()
        self.assertEqual((item.description_snapshot, item.variant_snapshot, item.list_price_snapshot),
                         ('', None, None))
        note, _ = get_or_create_sales_note(order, actor=self.cashier)
        Product.objects.filter(pk=self.case.pk).update(name='Funda hoy')
        [line] = build_sales_note_context(note)['items']
        self.assertEqual((line['name'], line['variant'], line['list_price_snapshot']), ('Funda hoy', None, None))
        for paper, text in self.papers(note).items():
            with self.subTest(paper=paper):
                self.assertIn('Funda hoy', text)
        # The amounts of an old sale are never touched.
        item.refresh_from_db()
        self.assertEqual(item.price, D('59.90'))


class AdminApiTest(_Devices, TestCase):
    def test_the_admin_line_shows_the_snapshot_read_only(self):
        CartItem.objects.create(session_key='carrito-a', product=self.case, quantity=1)
        line = self.checkout().items.get()
        data = AdminOrderItemSerializer(line).data
        self.assertEqual(data['description_snapshot'], 'Funda clara')
        self.assertEqual(data['variant_snapshot'], {})
        self.assertEqual(data['list_price_snapshot'], '59.90')
        self.assertTrue(set(AdminOrderItemSerializer.Meta.read_only_fields)
                        >= {'description_snapshot', 'variant_snapshot', 'list_price_snapshot'})


class MigrationTest(TestCase):
    def test_it_only_adds_nullable_or_constant_default_columns(self):
        from importlib import import_module

        from django.db import migrations

        operations = import_module(f'store.migrations.{MIGRATION}').Migration.operations
        self.assertEqual(sorted(op.name for op in operations),
                         ['description_snapshot', 'list_price_snapshot', 'variant_snapshot'])
        for operation in operations:
            self.assertIsInstance(operation, migrations.AddField)
            self.assertEqual(operation.model_name, 'orderitem')
            field = operation.field
            self.assertTrue(field.null or field.db_default == '', operation.name)

    def test_it_goes_back_and_forth_keeping_the_lines(self):
        from django.db.migrations.executor import MigrationExecutor

        from store.models import Company

        company = Company.objects.create(name='Migración línea', slug='migracion-linea')
        product = Product.objects.create(company=company, name='Cable', slug='cable-mig', price=D('10.00'))
        order = Order.objects.create(company=company, total=D('10.00'), status=Order.Status.PAID, paid=True)
        OrderItem.objects.create(order=order, product=product, quantity=1, price=D('10.00'))
        other = Product.objects.create(company=company, name='Funda', slug='funda-mig', price=D('10.00'))
        table = OrderItem._meta.db_table
        previous = ('store', '0177_document_design_v2')

        def columns():
            with connection.cursor() as cursor:
                return {col.name for col in connection.introspection.get_table_description(cursor, table)}

        with connection.cursor() as cursor:
            cursor.execute('SET CONSTRAINTS ALL IMMEDIATE')
        executor = MigrationExecutor(connection)
        executor.migrate([previous])
        self.assertNotIn('description_snapshot', columns())
        with connection.cursor() as cursor:
            # The previous release keeps inserting lines while this migrates.
            cursor.execute(f'INSERT INTO {table} (order_id, product_id, quantity, price) VALUES (%s, %s, 1, 10)',
                           [order.pk, other.pk])

        executor = MigrationExecutor(connection)
        executor.migrate([('store', MIGRATION)])
        self.assertTrue({'description_snapshot', 'variant_snapshot', 'list_price_snapshot'} <= columns())
        with connection.cursor() as cursor:
            cursor.execute(f'SELECT price, description_snapshot, variant_snapshot, list_price_snapshot '
                           f'FROM {table} WHERE order_id = %s ORDER BY id', [order.pk])
            rows = cursor.fetchall()
        self.assertEqual(rows, [(D('10.00'), '', None, None)] * 2)
