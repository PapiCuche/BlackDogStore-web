"""
VARIANT-SAFE-01 — buscar, editar, desactivar, archivar y eliminar variantes sin perder datos.

Lo que estas pruebas fijan:

  * toda relación hacia `Product` está clasificada en `variant_lifecycle.RELATIONS` (bloquea / se borra con la
    variante / se ignora con motivo): una clave foránea nueva sin clasificar hace fallar la suite;
  * ninguna relación de historial o de datos comerciales borra en cascada (PROTECT, migración 0188), y un modelo con
    variantes no se borra (`product_model` PROTECT);
  * `GET …/deletion-check/` explica cada bloqueo en castellano; `DELETE` exige `products.manage` + `products.delete`,
    motivo y el nombre escrito; sólo elimina una variante sin historial, junto con sus opciones y códigos, y deja una
    auditoría con la foto de la variante; la auditoría anterior queda intacta;
  * archivar: fuera de tienda, caja, cargas, resolutores y del conjunto «existente» de la paleta; se rechaza con
    stock; la base impide reactivarla sin desarchivar; desarchivar vuelve inactiva y con motivo;
  * otra empresa = 404; sin `products.delete` = 403;
  * dos transacciones a la vez: una fila hija insertada durante la eliminación espera y falla, nunca queda huérfana.

Nada aquí es de una tienda concreta.
"""
from datetime import date, timedelta
from decimal import Decimal as D
from importlib import import_module
from threading import Event, Thread

from django.db import IntegrityError, connection, transaction
from django.db.models import ProtectedError
from django.test import SimpleTestCase, TestCase, TransactionTestCase
from django.utils import timezone

from store import (
    configuration_resolver, device_catalog, model_palette, pos_device_services, supplier_offer_import,
    text_preview_match, variant_lifecycle,
)
from store.models import (
    AccessoryCompatibility, AdminAuditLog, Branch, BranchStock, CartItem, Customer, Device, InventoryCount,
    InventoryCountItem, LabelBatch, LabelBatchItem, Order, OrderItem, PartUsage, Product, ProductBarcode, ProductImage,
    ProductModel, ProductVariantOption, Promotion, PromotionItem, ReceiptDraft, RepairExecution, RepairOrder,
    RepairQuote, RepairQuoteItem, Review, StockMovement, StockTransfer, StockTransferItem, StockUnit,
    StorefrontCampaign, Supplier, SupplierOffer, SupplierProductRef,
)
from store.tenancy import company_storefront_products
from store.test_catalog_variant_draft import DraftShop
from store.test_device_commerce import _client
from store.tests import _p2d_member, _p3_company, _prod


class _Undo(Exception):
    """Leaves a subtest's rows behind: the atomic block it escapes rolls them back."""


def _first_choice(model, field):
    return model._meta.get_field(field).choices[0][0]


def _row(table, **fields):
    """One row straight into the table (no `save()` rules): only the database constraints apply."""
    return table.objects.bulk_create([table(**fields)])[0]


class LifecycleShop(DraftShop):
    """`DraftShop` plus someone who may delete (`products.delete`) and a second company."""

    def build(self, slug='vs-tienda'):
        super().build(slug)
        self.deleter, _ = _p2d_member(self.company, f'{slug}_deleter', [
            'company.view', 'products.view', 'products.manage', 'products.delete'])
        self.delete_api = _client(self.deleter)
        self.other = _p3_company(f'{slug}-otra', 'Otra empresa')
        self.other_product = _prod(self.other, 'Producto ajeno', f'{slug}-ajeno')

    # -- API -------------------------------------------------------------------------------------------------
    def check(self, product, client=None):
        return (client or self.delete_api).get(f'/api/admin/products/{product.pk}/deletion-check/{self.q}')

    def delete(self, product, client=None, reason='Creada por error', confirm_name=None):
        return (client or self.delete_api).delete(
            f'/api/admin/products/{product.pk}/{self.q}',
            {'reason': reason, 'confirm_name': product.name if confirm_name is None else confirm_name},
            format='json')

    def archive(self, product, client=None, reason='Ya no se vende'):
        return (client or self.manage_api).post(f'/api/admin/products/{product.pk}/archive/{self.q}',
                                                {'reason': reason}, format='json')

    def unarchive(self, product, client=None, reason='Vuelve a venderse'):
        return (client or self.manage_api).post(f'/api/admin/products/{product.pk}/unarchive/{self.q}',
                                                {'reason': reason}, format='json')

    # -- one row of every blocking relation ------------------------------------------------------------------
    def second_branch(self):
        return Branch.objects.create(company=self.company, name='Sucursal dos')

    def repair(self):
        customer = _row(Customer, company=self.company, last_name='Cliente')
        device = _row(Device, company=self.company, customer=customer, brand='Marca', model='Modelo')
        order = _row(RepairOrder, company=self.company, branch=self.branch, customer=customer, device=device,
                     number='OT-1', sequence_value=1, reported_issue='No enciende')
        quote = _row(RepairQuote, company=self.company, repair_order=order, revision=1, currency='PEN')
        return order, quote

    def blocker_builders(self):
        """code → a function that gives `product` one row of that relation."""
        def movement(p):
            return _row(StockMovement, company=self.company, branch=self.branch, product=p,
                        movement_type=_first_choice(StockMovement, 'movement_type'), quantity=1,
                        stock_before=0, stock_after=1)

        def quote_item(p):
            _order, quote = self.repair()
            return _row(RepairQuoteItem, quote=quote, product=p, description='Pantalla', quantity=D('1'),
                        unit_price=D('10'), line_total=D('10'))

        def part_usage(p):
            order, quote = self.repair()
            item = _row(RepairQuoteItem, quote=quote, product=p, description='Pantalla', quantity=D('1'),
                        unit_price=D('10'), line_total=D('10'))
            execution = _row(RepairExecution, company=self.company, repair_order=order)
            return _row(PartUsage, company=self.company, repair_order=order, execution=execution, quote_item=item,
                        product=p, branch=self.branch, quantity=1, stock_movement=movement(p),
                        description='Pantalla')

        supplier = lambda: _row(Supplier, company=self.company, name='Proveedor')  # noqa: E731
        return {
            'order_items': lambda p: _row(OrderItem, order=Order.objects.create(company=self.company), product=p,
                                          price=D('10')),
            'stock_movements': movement,
            'branch_stock': lambda p: _row(BranchStock, branch=self.branch, product=p, quantity=0),
            'stock_units': lambda p: _row(StockUnit, company=self.company, branch=self.branch, product=p,
                                          serial_number='VS-ANULADO', status=StockUnit.Status.VOIDED,
                                          voided_at=timezone.now()),
            'transfer_items': lambda p: _row(StockTransferItem, product=p, quantity=1, transfer=_row(
                StockTransfer, company=self.company, source_branch=self.branch,
                destination_branch=self.second_branch())),
            'count_items': lambda p: _row(InventoryCountItem, product=p, count=_row(
                InventoryCount, company=self.company, branch=self.branch)),
            'label_items': lambda p: _row(LabelBatchItem, product=p, batch=_row(
                LabelBatch, company=self.company, branch=self.branch, reason='manual')),
            'cart_items': lambda p: _row(CartItem, session_key='sesion-vs', product=p),
            'reviews': lambda p: _row(Review, product=p, rating=5),
            'promotion_items': lambda p: _row(PromotionItem, product=p, promotion=_row(
                Promotion, company=self.company, name='Combo', promotion_type=_first_choice(Promotion,
                                                                                            'promotion_type'))),
            'campaigns': lambda p: _row(StorefrontCampaign, company=self.company, product=p, title='Campaña',
                                        slot=_first_choice(StorefrontCampaign, 'slot')),
            'repair_quote_items': quote_item,
            'part_usages': part_usage,
            'supplier_offers': lambda p: _row(SupplierOffer, company=self.company, supplier=supplier(), product=p,
                                              commercial_price=D('10'), valid_from=date.today(),
                                              valid_until=date.today() + timedelta(days=7), lead_time_days=3,
                                              availability_confirmed=False),
            'supplier_refs': lambda p: _row(SupplierProductRef, company=self.company, supplier=supplier(),
                                            supplier_code='PROV-1', product=p),
            'images': lambda p: _row(ProductImage, company=self.company, product=p,
                                     image_url='/api/storefront/images/1'),
            'accessory_links': lambda p: _row(AccessoryCompatibility, company=self.company, model=self.model,
                                              accessory=p),
            'receipt_drafts': lambda p: _row(ReceiptDraft, company=self.company, user=self.manager,
                                             payload={'lines': [{'key': 'a', 'product': {'id': p.pk}}]},
                                             expires_at=timezone.now() + timedelta(hours=1)),
        }


# ---------------------------------------------------------------------------
# The guard: every relation classified, none of history cascades
# ---------------------------------------------------------------------------

class RelationGuardTest(SimpleTestCase):
    def test_every_relation_to_product_is_classified(self):
        self.assertEqual(variant_lifecycle.unclassified_relations(), [],
                         'Una relación nueva hacia Product: clasifícala en variant_lifecycle.RELATIONS '
                         '(bloquea, se borra con la variante o se ignora con motivo).')
        # …and nothing classified is stale (a relation that no longer exists).
        live = {variant_lifecycle.relation_key(r) for r in Product._meta.related_objects}
        self.assertEqual(sorted(set(variant_lifecycle.RELATIONS) - live), [])

    def test_nothing_of_history_cascades(self):
        from django.db.models import CASCADE, PROTECT

        for relation in Product._meta.related_objects:
            key = variant_lifecycle.relation_key(relation)
            rule = variant_lifecycle.RELATIONS[key]
            if key == ('store.ProductVariantOption', 'product'):
                # The variant's own option rows: CASCADE on purpose, deleted explicitly by the service.
                self.assertIs(relation.on_delete, CASCADE, key)
                self.assertEqual(rule.kind, variant_lifecycle.DELETED_WITH_VARIANT)
            else:
                self.assertIs(relation.on_delete, PROTECT, key)
            if rule.kind != variant_lifecycle.BLOCKER:
                self.assertTrue(rule.why, f'{key}: explica por qué no bloquea')
        self.assertIs(Product._meta.get_field('product_model').remote_field.on_delete, PROTECT)

    def test_the_audit_log_has_no_foreign_key_to_a_product(self):
        targets = {f.related_model for f in AdminAuditLog._meta.concrete_fields if f.is_relation}
        self.assertNotIn(Product, targets)
        self.assertNotIn(ProductModel, targets)

    def test_the_django_admin_never_deletes_a_product(self):
        from django.contrib import admin

        self.assertFalse(admin.site._registry[Product].has_delete_permission(None))

    def test_every_blocker_has_a_spanish_label(self):
        codes = [rule.code for rule in variant_lifecycle.RELATIONS.values()]
        self.assertEqual(len(codes), len(set(codes)))
        for rule in variant_lifecycle.RELATIONS.values():
            self.assertTrue(rule.label and rule.label[0].isupper(), rule)


class MigrationTest(TestCase):
    MIGRATION = '0188_variant_safe'

    def test_the_on_delete_of_0188(self):
        from django.db.migrations.loader import MigrationLoader
        from django.db.models import CASCADE, PROTECT, SET_NULL

        loader = MigrationLoader(connection, ignore_no_migrations=True)
        before = loader.project_state(('store', '0187_used_unit_readiness')).apps
        after = loader.project_state(('store', self.MIGRATION)).apps
        changed = [('ProductImage', 'product'), ('CartItem', 'product'), ('Review', 'product'),
                   ('ProductBarcode', 'product'), ('AccessoryCompatibility', 'accessory'),
                   ('SupplierProductRef', 'product')]
        for model, field in changed:
            self.assertIs(before.get_model('store', model)._meta.get_field(field).remote_field.on_delete, CASCADE)
            self.assertIs(after.get_model('store', model)._meta.get_field(field).remote_field.on_delete, PROTECT)
        self.assertIs(before.get_model('store', 'Product')._meta.get_field('product_model').remote_field.on_delete,
                      SET_NULL)
        self.assertIs(after.get_model('store', 'Product')._meta.get_field('product_model').remote_field.on_delete,
                      PROTECT)
        self.assertIs(after.get_model('store', 'ProductVariantOption')._meta.get_field('product')
                      .remote_field.on_delete, CASCADE)

    def test_it_only_adds_nullable_or_db_default_columns_and_one_grant(self):
        from django.db import migrations
        from django.db.models import NOT_PROVIDED

        operations = import_module(f'store.migrations.{self.MIGRATION}').Migration.operations
        for operation in operations:
            if isinstance(operation, migrations.AddField):
                field = operation.field
                self.assertTrue(field.null or field.db_default is not NOT_PROVIDED, operation.name)
        runs = [op for op in operations if isinstance(op, migrations.RunPython)]
        self.assertEqual([op.code.__name__ for op in runs], ['grant'])
        self.assertNotIn('from store.capabilities import',
                         open(import_module(f'store.migrations.{self.MIGRATION}').__file__).read())

    def test_the_grant_goes_to_company_managers_only_and_is_idempotent(self):
        from django.apps import apps

        from store.models import CompanyRole

        module = import_module(f'store.migrations.{self.MIGRATION}')
        company = _p3_company('vs-grant', 'Grant SA')
        admin = CompanyRole.objects.create(company=company, name='Admin mig', slug='admin-mig',
                                           capabilities=['company.manage', 'products.manage'])
        catalog = CompanyRole.objects.create(company=company, name='Catálogo mig', slug='catalogo-mig',
                                             capabilities=['products.manage', 'products.view'])
        module.grant(apps, None)
        module.grant(apps, None)
        admin.refresh_from_db()
        catalog.refresh_from_db()
        self.assertEqual(admin.capabilities.count('products.delete'), 1)
        self.assertNotIn('products.delete', catalog.capabilities)

    def test_the_capability_is_in_the_catalogue_and_the_admin_preset_only(self):
        from store.capabilities import CAPABILITIES, DEFAULT_ADMIN_CAPABILITY_CODES
        from store.models import UserProfile
        from store.tenancy import LEGACY_ROLE_CAPABILITIES

        self.assertTrue(CAPABILITIES['products.delete'].is_assignable)
        self.assertIn('products.delete', DEFAULT_ADMIN_CAPABILITY_CODES)
        for role in (UserProfile.ROLE_SALES, UserProfile.ROLE_INVENTORY, UserProfile.ROLE_TECHNICIAN):
            self.assertNotIn('products.delete', LEGACY_ROLE_CAPABILITIES[role])


# ---------------------------------------------------------------------------
# Deleting
# ---------------------------------------------------------------------------

class DeletionCheckTest(LifecycleShop, TestCase):
    def setUp(self):
        self.build('vs-check')

    def test_a_fresh_palette_draft_is_deletable(self):
        product = self.draft()
        self.assertTrue(ProductBarcode.objects.filter(product=product).exists())   # born with a generated code
        res = self.check(product)
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        self.assertEqual((body['deletable'], body['archivable'], body['blockers']), (True, True, []))
        self.assertEqual((body['can_delete'], body['can_archive']), (True, True))

    def test_each_blocker_is_explained_and_refuses_the_delete(self):
        builders = self.blocker_builders()
        expected = {rule.code for rule in variant_lifecycle.RELATIONS.values()
                    if rule.kind == variant_lifecycle.BLOCKER} | {'receipt_drafts'}
        self.assertEqual(set(builders), expected, 'cada bloqueo tiene su prueba')
        for code, build in builders.items():
            with self.subTest(code=code):
                try:
                    with transaction.atomic():
                        self.one_blocker(code, build)
                        raise _Undo
                except _Undo:
                    pass

    def one_blocker(self, code, build):
        product = self.draft()
        build(product)
        body = self.check(product).json()
        self.assertFalse(body['deletable'])
        found = {b['code']: b for b in body['blockers']}
        self.assertIn(code, found)
        self.assertGreaterEqual(found[code]['count'], 1)
        self.assertTrue(found[code]['label'])
        res = self.delete(product)
        self.assertEqual((res.status_code, res.json()['code']), (409, 'has_dependencies'), res.content)
        self.assertIn(code, {b['code'] for b in res.json()['blockers']})
        self.assertTrue(Product.objects.filter(pk=product.pk).exists())
        self.assertFalse(AdminAuditLog.objects.filter(action='product_variant_deleted').exists())

    def test_an_expired_receipt_draft_does_not_block(self):
        product = self.draft()
        _row(ReceiptDraft, company=self.company, user=self.manager,
             payload={'lines': [{'product': {'id': product.pk}}]}, expires_at=timezone.now() - timedelta(hours=1))
        self.assertTrue(self.check(product).json()['deletable'])

    def test_a_product_of_another_company_is_a_404(self):
        self.assertEqual(self.check(self.other_product).status_code, 404)
        self.assertEqual(self.delete(self.other_product).status_code, 404)
        self.assertEqual(self.archive(self.other_product).status_code, 404)
        self.assertEqual(self.unarchive(self.other_product).status_code, 404)
        self.assertTrue(Product.objects.filter(pk=self.other_product.pk, is_active=True).exists())

    def test_a_product_without_model_is_not_a_variant(self):
        loose = _prod(self.company, 'Cable suelto', 'vs-cable')
        body = self.check(loose).json()
        self.assertFalse(body['deletable'])
        self.assertIn('not_a_variant', {b['code'] for b in body['blockers']})
        self.assertEqual(self.delete(loose).json()['code'], 'not_a_variant')
        self.assertEqual(self.archive(loose).json()['code'], 'not_a_variant')


class DeleteTest(LifecycleShop, TestCase):
    def setUp(self):
        self.build('vs-delete')
        self.product = self.draft()

    def test_it_deletes_the_variant_its_options_and_codes_and_audits_a_snapshot(self):
        pk, name = self.product.pk, self.product.name
        codes = sorted(ProductBarcode.objects.filter(product=self.product).values_list('code', flat=True))
        created_log = AdminAuditLog.objects.filter(action='product_variant_created_from_palette',
                                                   target_id=str(pk)).get()
        self.assertTrue(ProductVariantOption.objects.filter(product_id=pk).exists())

        res = self.delete(self.product, reason='  Creada por error en la paleta  ')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json(), {'deleted': True, 'product_id': pk, 'name': name})
        self.assertFalse(Product.objects.filter(pk=pk).exists())
        self.assertFalse(ProductVariantOption.objects.filter(product_id=pk).exists())
        self.assertFalse(ProductBarcode.objects.filter(product_id=pk).exists())

        log = AdminAuditLog.objects.get(action='product_variant_deleted', target_id=str(pk))
        self.assertEqual((log.company_id, log.actor_id), (self.company.pk, self.deleter.pk))
        self.assertEqual(log.metadata['reason'], 'Creada por error en la paleta')
        variant = log.metadata['variant']
        self.assertEqual((variant['name'], variant['variant_key'], variant['price'], variant['codes']),
                         (name, self.product.variant_key, '0.00', codes))
        self.assertEqual(sorted(o['option'] for o in variant['options']), ['256 GB', 'Azul'])
        self.assertEqual(variant['model_id'], self.model.pk)
        # The history that existed is untouched.
        self.assertTrue(AdminAuditLog.objects.filter(pk=created_log.pk).exists())
        # The combination is free again.
        self.assertIsNotNone(self.draft())

    def test_reason_and_typed_name_are_required(self):
        res = self.delete(self.product, reason='')
        self.assertEqual((res.status_code, res.json()['code']), (400, 'reason_required'))
        res = self.delete(self.product, confirm_name='otro nombre')
        self.assertEqual((res.status_code, res.json()['code']), (400, 'confirm_name'))
        self.assertTrue(Product.objects.filter(pk=self.product.pk).exists())

    def test_without_products_delete_it_is_a_403(self):
        res = self.delete(self.product, client=self.manage_api)
        self.assertEqual((res.status_code, res.json()['code']), (403, 'delete_forbidden'))
        self.assertEqual(self.delete(self.product, client=self.view_api).status_code, 403)
        self.assertTrue(Product.objects.filter(pk=self.product.pk).exists())
        body = self.check(self.product, client=self.manage_api).json()
        self.assertEqual((body['can_delete'], body['can_archive']), (False, True))
        body = self.check(self.product, client=self.view_api).json()
        self.assertEqual((body['can_delete'], body['can_archive']), (False, False))

    def test_products_delete_alone_is_not_enough(self):
        only, _ = _p2d_member(self.company, 'vs-delete_only', ['company.view', 'products.view', 'products.delete'])
        self.assertEqual(self.delete(self.product, client=_client(only)).status_code, 403)

    def test_an_archived_variant_without_history_may_still_be_deleted(self):
        self.assertEqual(self.archive(self.product).status_code, 200)
        self.product.refresh_from_db()
        self.assertEqual(self.delete(self.product).status_code, 200)

    def test_an_orm_delete_of_a_variant_with_history_is_refused(self):
        _row(CartItem, session_key='s', product=self.product)
        with self.assertRaises(ProtectedError), transaction.atomic():
            Product.objects.filter(pk=self.product.pk).delete()
        with self.assertRaises(ProtectedError), transaction.atomic():
            self.model.delete()     # a model with variants is never deleted
        self.assertTrue(Product.objects.filter(pk=self.product.pk).exists())


# ---------------------------------------------------------------------------
# Archiving
# ---------------------------------------------------------------------------

class ArchiveTest(LifecycleShop, TestCase):
    def setUp(self):
        self.build('vs-archive')

    def test_archive_and_unarchive(self):
        product = self.new256
        res = self.archive(product)
        self.assertEqual(res.status_code, 200, res.content)
        product.refresh_from_db()
        self.assertTrue(product.is_archived)
        self.assertFalse(product.is_active)
        self.assertEqual((product.archive_reason, product.archived_by_id), ('Ya no se vende', self.manager.pk))
        log = AdminAuditLog.objects.get(action='product_variant_archived', target_id=str(product.pk))
        self.assertEqual((log.company_id, log.metadata['reason'], log.metadata['variant']['name']),
                         (self.company.pk, 'Ya no se vende', product.name))
        self.assertEqual(self.archive(product).json()['code'], 'already_archived')

        res = self.unarchive(product)
        self.assertEqual(res.status_code, 200, res.content)
        product.refresh_from_db()
        self.assertIsNone(product.archived_at)
        self.assertEqual(product.archive_reason, '')
        self.assertFalse(product.is_active)         # comes back inactive
        self.assertTrue(AdminAuditLog.objects.filter(action='product_variant_unarchived',
                                                     target_id=str(product.pk), company=self.company).exists())
        self.assertEqual(self.unarchive(product).json()['code'], 'not_archived')

    def test_a_reason_is_required(self):
        res = self.archive(self.new256, reason='no')
        self.assertEqual((res.status_code, res.json()['code']), (400, 'reason_required'))

    def test_a_variant_with_stock_is_not_archived(self):
        self.receive(self.new128, 2)
        res = self.archive(self.new128)
        self.assertEqual((res.status_code, res.json()['code']), (409, 'stock_available'), res.content)
        self.assertEqual(res.json()['stock']['available_units'], 2)
        self.assertFalse(self.check(self.new128).json()['archivable'])
        self.new128.refresh_from_db()
        self.assertIsNone(self.new128.archived_at)

    def test_the_database_keeps_an_archived_variant_inactive(self):
        self.archive(self.new256)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Product.objects.filter(pk=self.new256.pk).update(is_active=True)
        res = self.manage_api.patch(f'/api/admin/products/{self.new256.pk}/{self.q}', {'is_active': True},
                                    format='json')
        self.assertEqual(res.status_code, 400, res.content)
        self.assertIn('is_active', res.json())
        # Other edits stay possible.
        res = self.manage_api.patch(f'/api/admin/products/{self.new256.pk}/{self.q}', {'price': '3400.00'},
                                    format='json')
        self.assertEqual(res.status_code, 200, res.content)

    def test_an_archived_variant_does_not_move_nor_is_enabled(self):
        product = self.priced()
        self.archive(product)
        with self.assertRaises(device_catalog.CatalogError) as caught:
            device_catalog.set_variant(product, model_id=self.model.pk, option_ids=[self.gb128.pk, self.blue.pk],
                                       actor=self.manager)
        self.assertEqual(caught.exception.code, 'variant_archived')
        res = self.enable(product)
        self.assertEqual(res.json()['code'], 'variant_archived', res.content)

    def test_the_combination_is_never_created_again(self):
        product = self.draft()
        self.archive(product)
        res = self.create([self.gb256, self.blue])
        self.assertEqual(res.status_code, 400, res.content)
        self.assertIn('archivada', res.json()['detail'])


class ArchivedIsOutEverywhereTest(LifecycleShop, TestCase):
    def setUp(self):
        self.build('vs-fuera')
        self.palette(self.gb128, self.gb256, self.black, self.blue)
        Product.objects.filter(pk=self.new256.pk).update(is_published_online=True)
        self.assertEqual(self.archive(self.new256).status_code, 200)
        self.new256.refresh_from_db()

    def test_the_shop(self):
        self.assertNotIn(self.new256, company_storefront_products(self.company))
        self.assertFalse(self.new256.is_sold_online)

    def test_the_till(self):
        with self.assertRaises(pos_device_services.QuickSaleError) as caught:
            pos_device_services._usable(self.new256)
        self.assertEqual(caught.exception.code, 'variant_archived')

    def test_the_supplier_list(self):
        product, error = supplier_offer_import._by_model(self.company, {
            'model': 'Teléfono X', 'line': '', 'capacity': '256 GB', 'colour': 'Negro', 'sim': '', 'region': ''})
        self.assertIsNone(product)
        self.assertIn('archivada', error)

    def test_the_text_preview(self):
        answer = text_preview_match.match_device(self.company, {
            'model': 'Teléfono X', 'capacity': 256, 'colour': 'Negro', 'line': 'new'})
        self.assertEqual(answer['status'], 'none')
        self.assertIn('archivada', answer['reason'])

    def test_the_configuration_resolver(self):
        vocabulary = configuration_resolver.Vocabulary(self.company)
        row = configuration_resolver.resolve_configuration(
            vocabulary, model_text='Teléfono X', line='new', capacity_text='256 GB', colour_text='Negro')
        statuses = [c['status'] for c in row['configurations']]
        self.assertEqual(statuses, [configuration_resolver.STATUS_ARCHIVED])
        self.assertFalse(row['configurations'][0]['complete'])
        self.assertIsNone(row['configurations'][0]['product_id'])

    def test_the_guided_registration(self):
        res = self.api.post(f'/api/admin/products/register/{self.q}', {
            'kind': 'new_device',
            'product': {'name': 'Otra 256 Negro', 'price': '3999.00', 'is_active': True},
            'model': {'id': self.model.pk}, 'options': [self.gb256.pk, self.black.pk], 'requires_imei': True,
            'branch': self.branch.pk, 'units': [{'serial_number': 'SNVS0001', 'imei': next(self._imei)}],
        }, format='json', HTTP_IDEMPOTENCY_KEY='vs-registro-archivada')
        self.assertEqual(res.status_code, 400, res.content)
        self.assertIn('archivada', str(res.json()))
        self.assertEqual(StockUnit.objects.filter(product=self.new256).count(), 0)

    def test_the_palette_matrix(self):
        answer = model_palette.payload(self.model)
        row = next(r for r in answer['variants'] if r['product_id'] == self.new256.pk)
        self.assertTrue(row['archived'])
        combination = next(c for c in answer['combinations']
                           if c['key'] == device_catalog.variant_key([self.gb256.pk, self.black.pk]))
        self.assertTrue(combination['archived'])
        self.assertEqual(combination['products'][0]['archived'], True)
        others = [c for c in answer['combinations'] if c['products'] and not c['archived']]
        self.assertTrue(others and not any(c['archived'] for c in others))
        self.assertEqual(answer['summary']['archived'], 1)
        self.assertEqual(answer['summary']['existing'], 2)         # 128 Negro (new) and 128 Azul (used)
        self.assertEqual(answer['summary']['valid_without_variant'], answer['summary']['combinations'] - 3)

    def test_the_model_detail_says_so(self):
        res = self.manage_api.get(f'/api/admin/catalog/models/{self.model.pk}/{self.q}')
        row = next(r for r in res.json()['variants'] if r['product_id'] == self.new256.pk)
        self.assertEqual((row['archived'], row['is_active'], row['archive_reason']), (True, False, 'Ya no se vende'))


class ModelActiveTest(LifecycleShop, TestCase):
    def setUp(self):
        self.build('vs-modelo')

    def test_a_deactivated_model_leaves_the_public_list(self):
        from store.device_catalog_views import _public_models

        self.assertIn(self.model, _public_models(self.company))
        res = self.manage_api.patch(f'/api/admin/catalog/models/{self.model.pk}/{self.q}', {'is_active': False},
                                    format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertFalse(res.json()['is_active'])
        self.assertNotIn(self.model, _public_models(self.company))
        res = self.manage_api.patch(f'/api/admin/catalog/models/{self.model.pk}/{self.q}', {'is_active': 'no'},
                                    format='json')
        self.assertEqual(res.status_code, 400)


# ---------------------------------------------------------------------------
# Two transactions at once
# ---------------------------------------------------------------------------

class DeleteAgainstAConcurrentInsertTest(TransactionTestCase):
    """
    The deletion has counted no blockers under the product's lock; another transaction adds a cart line to that
    product before the deletion commits. That insert waits for the lock and then fails: the product is gone and no
    cart line points at nothing.
    """

    def test_the_concurrent_insert_waits_and_fails(self):
        shop = LifecycleShop()
        shop.build('vs-carrera')
        product = shop.draft()
        counted, inserted, outcome = Event(), Event(), {}
        original = variant_lifecycle.blockers

        def blockers_then_wait(target):
            found = original(target)
            if connection.in_atomic_block and not counted.is_set():
                counted.set()
                inserted.wait(timeout=10)       # the other transaction is now blocked on our lock
            return found

        def insert():
            try:
                counted.wait(timeout=10)
                with transaction.atomic():
                    CartItem.objects.create(session_key='carrera', product_id=product.pk)
                    _blocked_signal(inserted)
                outcome['insert'] = 'committed'
            except IntegrityError:
                outcome['insert'] = 'failed'
            finally:
                inserted.set()
                connection.close()

        thread = Thread(target=insert)
        thread.start()
        from unittest import mock

        with mock.patch.object(variant_lifecycle, 'blockers', blockers_then_wait):
            try:
                variant_lifecycle.delete_variant(company=shop.company, pk=product.pk, reason='Creada por error',
                                                 confirm_name=product.name, actor=shop.deleter)
                outcome['delete'] = 'deleted'
            except variant_lifecycle.LifecycleError as exc:
                outcome['delete'] = exc.code
        thread.join(timeout=20)

        self.assertEqual(outcome, {'delete': 'deleted', 'insert': 'failed'})
        self.assertFalse(Product.objects.filter(pk=product.pk).exists())
        self.assertFalse(CartItem.objects.filter(session_key='carrera').exists())


def _blocked_signal(event):
    """
    Called inside the inserting transaction just before it commits. The FK check of a deferred constraint runs at
    COMMIT, so the commit itself is what waits; signal from a timer once this connection is waiting on a lock.
    """
    from threading import Timer

    def watch():
        event.set()

    # Give the commit a moment to reach the lock wait; the deletion only proceeds after this.
    Timer(0.5, watch).start()


# ---------------------------------------------------------------------------
# No importer uses, re-creates or reactivates an archived variant
# ---------------------------------------------------------------------------

class ArchivedImportersTest(LifecycleShop, TestCase):
    UNIT_HEADERS = ['Código', 'Producto', 'Sucursal', 'Número de serie', 'IMEI', 'IMEI 2', 'Condición', 'Costo',
                    'Motivo / referencia', 'Modelo', 'Capacidad', 'Color', 'SIM', 'Región', 'Línea']

    def setUp(self):
        self.build('vs-cargas')
        ProductBarcode.objects.create(company=self.company, product=self.new256, code='VS-TX256-NEGRO')
        self.assertEqual(self.archive(self.new256).status_code, 200)
        self.new256.refresh_from_db()

    def still_archived(self):
        self.new256.refresh_from_db()
        self.assertIsNotNone(self.new256.archived_at)
        self.assertFalse(self.new256.is_active)
        self.assertEqual(Product.objects.filter(company=self.company, product_model=self.model,
                                                variant_key=self.new256.variant_key,
                                                condition_line=self.new256.condition_line).count(), 1)

    # -- the device (unit) load --------------------------------------------------------------------------------
    def units_file(self, rows):
        from store import unit_import_services
        from store.test_cost_imports import upload

        job = unit_import_services.preview_units(
            company=self.company, actor=self.staff, upload=upload(self.UNIT_HEADERS, rows, title='Equipos'),
            filename='equipos.xlsx', reachable_branch_ids=[self.branch.pk], default_branch=self.branch.pk,
            default_reason='Compra')
        return job, list(job.rows.order_by('row_number'))

    def test_the_unit_load_refuses_an_archived_variant_by_model_and_by_code(self):
        job, (by_model, by_code) = self.units_file([
            ['', '', '', 'SERVSARCH1', next(self._imei), '', 'Nuevo', '', 'Compra', 'Teléfono X', '256 GB', 'Negro',
             '', '', 'Nuevo'],
            ['VS-TX256-NEGRO', '', '', 'SERVSARCH2', next(self._imei), '', 'Nuevo', '', 'Compra', '', '', '', '', '',
             ''],
        ])
        for row in (by_model, by_code):
            self.assertIn('archivada', ' '.join(row.errors), row.normalized_data)
        self.assertEqual(job.rows_error, 2)
        self.assertEqual(StockUnit.objects.filter(product=self.new256).count(), 0)
        self.still_archived()

    # -- the product load --------------------------------------------------------------------------------------
    def products_file(self, headers, rows):
        from store import import_services
        from store.test_cost_imports import upload

        job = import_services.preview_products(company=self.company, actor=self.staff, filename='p.xlsx',
                                               upload=upload(headers, rows))
        return job, list(job.rows.order_by('row_number'))

    def test_the_product_load_never_recreates_an_archived_combination(self):
        _job, [row] = self.products_file(
            ['Código', 'Nombre', 'Precio de venta', 'Modelo', 'Capacidad', 'Color', 'Línea'],
            [['VS-OTRO', 'Teléfono X 256 GB Negro otra vez', '3600', 'Teléfono X', '256 GB', 'Negro', 'Nuevo']])
        self.assertIn('archivada', ' '.join(row.errors))
        self.assertFalse(Product.objects.filter(company=self.company, name__endswith='otra vez').exists())
        self.still_archived()

    def test_the_product_load_never_reactivates_an_archived_variant(self):
        from store import import_services

        job, [row] = self.products_file(['Código', 'Nombre', 'Precio de venta', 'Estado'],
                                        [['VS-TX256-NEGRO', self.new256.name, '3450', 'Activo']])
        self.assertEqual(row.errors, [])
        self.assertIn('no activa ni desactiva', ' '.join(row.warnings))
        import_services.apply_products(job=job, actor=self.staff)
        self.still_archived()

    # -- the catalogue proposal (pasted text) ------------------------------------------------------------------
    def test_the_catalogue_proposal_marks_it_archived_and_never_approves_it(self):
        from rest_framework.test import APIClient

        api = APIClient()
        api.force_authenticate(self.staff)
        rows = [{'key': '1', 'model': 'Teléfono X', 'capacityGb': 256, 'colour': 'negro', 'line': 'new'}]
        res = api.post('/api/admin/inventory/text-preview/catalog-proposal/', {'rows': rows}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        [variant] = res.json()['plan']['variants']
        self.assertEqual((variant['status'], variant['product_id']), ('archived', self.new256.pk))
        self.assertIn('archivada', variant['archived'])
        before = Product.objects.filter(company=self.company).count()
        res = api.post('/api/admin/inventory/text-preview/catalog-proposal/apply/',
                       {'rows': rows, 'approve': {'models': [], 'variants': [variant['ref']]}}, format='json',
                       HTTP_IDEMPOTENCY_KEY='vs-propuesta-archivada')
        self.assertEqual((res.status_code, res.json()['code']), (409, 'variant_archived'), res.content)
        self.assertEqual(Product.objects.filter(company=self.company).count(), before)
        self.still_archived()

    # -- the supplier's Excel and the supplier-list resolver ---------------------------------------------------
    def test_the_supplier_excel_refuses_it(self):
        from store import supplier_offer_import, supply_services
        from store.test_cost_imports import upload
        from store.test_supplier_offers import OFFER_HEADERS

        supply_services.create_supplier(company=self.company, name='Proveedor VS', actor=self.staff)
        start = timezone.localdate()
        values = {'Proveedor': 'Proveedor VS', 'Código/SKU del producto': '', 'Modelo': 'Teléfono X',
                  'Capacidad': '256 GB', 'Color': 'Negro', 'Línea': 'Nuevo', 'Costo pactado': '',
                  'Precio comercial': '3600', 'Cantidad confirmada': 2, 'Vigente desde': start.isoformat(),
                  'Vigente hasta': (start + timedelta(days=6)).isoformat(), 'Plazo (días)': 3}
        job = supplier_offer_import.preview(
            company=self.company, actor=self.staff, filename='ofertas.xlsx', message_date=start,
            upload=upload(OFFER_HEADERS, [[values[h] for h in OFFER_HEADERS]], title='Ofertas de proveedor'))
        [row] = job.rows.all()
        self.assertIn('archivada', ' '.join(row.errors))
        self.assertFalse(SupplierOffer.objects.filter(product=self.new256).exists())

    def test_the_supplier_list_resolver_says_variante_archivada(self):
        res = self.manage_api.post(f'/api/admin/catalog/resolve/{self.q}', {'rows': [{
            'key': 'a', 'model_text': 'Teléfono X', 'line': 'nuevo', 'capacity_text': '256 GB',
            'colour_text': 'Negro', 'sim_text': '', 'region_text': ''}]}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        [config] = res.json()['rows'][0]['configurations']
        self.assertEqual((config['status'], config['complete'], config['product_id']),
                         ('variante_archivada', False, None))
        self.assertEqual(res.json()['summary']['variante_archivada'], 1)
