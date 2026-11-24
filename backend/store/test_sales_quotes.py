"""
SALES-QUOTE-01 — commercial quotes, end to end through `/api/admin/sales/quotes/`.

What these tests hold, in the owner's words: a quote never charges, never moves stock and never
equals a fiscal document; it is numbered, revised (never rewritten) and duplicated; it is priced
exactly like the till; and converting it is a counter sale — idempotent, re-validating stock and
price — linked back to the quote, which stays as it was.
"""
import importlib
import pkgutil
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.apps import apps
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from store import sales_quote_services as quotes
from store.capabilities import CAPABILITIES, DEFAULT_ADMIN_CAPABILITY_CODES
from store.document_design import names_fiscal_document
from store.document_legends import SALES_QUOTE_LEGEND, SALES_QUOTE_TITLE
from store.models import (
    AdminAuditLog,
    BranchStock,
    CompanyRole,
    InternalSequence,
    Membership,
    MembershipBranchAccess,
    Order,
    PaymentTransaction,
    Printer,
    PrintJob,
    Product,
    SalesCommission,
    SalesQuote,
    SalesQuoteItem,
    StockMovement,
    StockUnit,
    UserProfile,
)
from store.test_pos_reconcile import _web_login
from store.tests import Ip1PosBase, _assign, _m7_user, _role

URL = '/api/admin/sales/quotes/'
QUOTE_CAPS = {'sales.quotes.view', 'sales.quotes.manage', 'sales.quotes.approve'}


def sales_quote_migration():
    """The migration of this PR, found by its suffix: renumbering it touches nothing here."""
    from store import migrations

    name = next(m.name for m in pkgutil.iter_modules(migrations.__path__) if m.name.endswith('_sales_quote'))
    return importlib.import_module(f'store.migrations.{name}')


def pdf_text(data: bytes) -> str:
    from store.test_document_design import text_of

    return text_of(data)


class QuoteBase(Ip1PosBase):
    """The till's fixture (two companies, two branches, a `Ventas` cashier) plus a supervisor who approves."""

    def setUp(self):
        super().setUp()
        self.boss = _m7_user('sq_jefe')
        membership = Membership.objects.create(user=self.boss, company=self.company, role='sales', is_active=True)
        _assign(membership, _role(self.company, 'Supervisor', slug='sq-supervisor', capabilities=sorted({
            'company.view', 'sales.pos.use', 'sales.quotes.view', 'sales.quotes.manage', 'sales.quotes.approve',
            'sales.discounts.apply', 'sales.pos.assign_seller', 'sales.orders.manage', 'sales.commissions.view',
        })))
        cache.clear()
        self.client = _web_login('ip1_caja')
        self.jefe = _web_login('sq_jefe')

    def only_caps(self, *capabilities, slug='ip1-exacto', username='ip1_caja'):
        super().only_caps(*capabilities, slug=slug, username=username)
        return _web_login(username)

    # -- helpers -------------------------------------------------------------

    def body(self, **over):
        body = {'branch': self.branch_a.pk, 'items': [{'product': self.cable.pk, 'quantity': 2}],
                'customer_name': 'Cliente Prospecto', 'conditions': 'Pago al contado.\nEntrega inmediata.'}
        body.update(over)
        return body

    def create(self, client=None, **over):
        res = (client or self.client).post(URL, self.body(**over), format='json')
        self.assertEqual(res.status_code, 201, res.content)
        return res.json()

    def act(self, quote_id, action, client=None, expect=200, **body):
        res = (client or self.jefe).post(f'{URL}{quote_id}/{action}/', body, format='json')
        self.assertEqual(res.status_code, expect, res.content)
        return res.json()

    def approved(self, **over):
        quote = self.create(**over)
        self.act(quote['id'], 'approve')
        return quote

    def convert(self, quote_id, key='sq-convert-0001', client=None, **over):
        body = {'payment_method': 'cash', 'amount_received': '1000.00', 'terms_confirmed': True, **over}
        return (client or self.client).post(f'{URL}{quote_id}/convert/', body, format='json',
                                            HTTP_IDEMPOTENCY_KEY=key)

    def counts(self):
        return (StockMovement.objects.count(), PaymentTransaction.objects.count(), Order.objects.count(),
                SalesCommission.objects.count(),
                list(BranchStock.objects.order_by('pk').values_list('quantity', flat=True)),
                list(StockUnit.objects.order_by('pk').values_list('status', flat=True)))


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------

class QuoteCapabilitiesTest(TestCase):
    def test_three_active_assignable_capabilities(self):
        for code in QUOTE_CAPS:
            self.assertTrue(CAPABILITIES[code].is_assignable, code)
            self.assertEqual(CAPABILITIES[code].status, 'active')
        self.assertTrue(QUOTE_CAPS <= DEFAULT_ADMIN_CAPABILITY_CODES)

    def test_the_counter_quotes_but_does_not_approve(self):
        from store.company_provisioning import PRESET_ROLES
        from store.tenancy import LEGACY_ROLE_CAPABILITIES

        presets = {slug: set(caps) for _name, slug, _desc, caps in PRESET_ROLES}
        self.assertTrue({'sales.quotes.view', 'sales.quotes.manage'} <= presets['ventas'])
        self.assertNotIn('sales.quotes.approve', presets['ventas'])
        self.assertFalse(QUOTE_CAPS & presets['inventario'])
        self.assertFalse(QUOTE_CAPS & presets['servicio-tecnico'])
        legacy = LEGACY_ROLE_CAPABILITIES[UserProfile.ROLE_SALES]
        self.assertTrue({'sales.quotes.view', 'sales.quotes.manage'} <= legacy)
        self.assertNotIn('sales.quotes.approve', legacy)

    def test_the_migration_grants_by_the_rule_and_continues_the_chain(self):
        from store.company_provisioning import provision_company_access_defaults
        from store.tests import _saas_company

        module = sales_quote_migration()
        previous = importlib.import_module('store.migrations.0187_used_unit_readiness')
        self.assertEqual(previous.ADMIN_PREVIOUS | set(previous.NEW_CAPABILITIES), module.ADMIN_PREVIOUS)
        self.assertEqual(module.ADMIN_PREVIOUS | set(module.NEW_CAPABILITIES), DEFAULT_ADMIN_CAPABILITY_CODES)

        company = _saas_company('Antes', 'sq-antes', tax_id='20900095009')
        provision_company_access_defaults(company)
        roles = company.roles
        roles.filter(slug='administrador').update(capabilities=sorted(module.ADMIN_PREVIOUS))
        for slug in ('ventas', 'inventario'):
            role = roles.get(slug=slug)
            role.capabilities = sorted(set(role.capabilities) - QUOTE_CAPS)
            role.save()
        module.grant(apps, None)

        def caps(slug):
            return set(roles.get(slug=slug).capabilities)

        self.assertTrue(QUOTE_CAPS <= caps('administrador'))
        self.assertEqual(caps('ventas') & QUOTE_CAPS, {'sales.quotes.view', 'sales.quotes.manage'})
        self.assertFalse(caps('inventario') & QUOTE_CAPS)


# ---------------------------------------------------------------------------
# Compose, number, price
# ---------------------------------------------------------------------------

class QuoteComposeTest(QuoteBase):
    def test_a_draft_is_numbered_from_its_own_company_series(self):
        first = self.create()
        second = self.create()
        self.assertEqual((first['number'], second['number']), ('COT-000001', 'COT-000002'))
        self.assertEqual(first['status'], 'draft')
        series = InternalSequence.objects.get(company=self.company, document_type='sales_quote', branch=None)
        self.assertEqual(series.next_value, 3)
        self.assertFalse(InternalSequence.objects.filter(company=self.other, document_type='sales_quote').exists())

    def test_a_quote_prefix_never_starts_like_a_fiscal_series(self):
        self.create()
        series = InternalSequence.objects.get(company=self.company, document_type='sales_quote')
        series.prefix = 'F-'
        with self.assertRaises(ValidationError):
            series.full_clean()

    def test_totals_and_igv_are_the_tills_to_the_cent(self):
        preview = self.client.post('/api/admin/pos/preview/', {
            'branch': self.branch_a.pk, 'items': [{'product': self.cable.pk, 'quantity': 2},
                                                  {'product': self.case.pk, 'quantity': 1}]}, format='json')
        self.assertEqual(preview.status_code, 200, preview.content)
        expected = preview.json()
        quote = self.create(items=[{'product': self.cable.pk, 'quantity': 2}, {'product': self.case.pk, 'quantity': 1}])
        self.assertEqual(quote['total'], expected['total'])
        self.assertEqual(quote['subtotal'], expected['subtotal'])
        self.assertEqual(quote['tax_amount'], expected['tax']['tax_amount'])
        self.assertEqual(quote['taxable_amount'], expected['tax']['taxable_amount'])
        self.assertEqual(Decimal(quote['taxable_amount']) + Decimal(quote['tax_amount']), Decimal(quote['total']))
        self.assertEqual([(i['description'], i['quantity'], i['unit_price']) for i in quote['items']],
                         [('Cable USB-C', 2, '50.00'), ('Funda', 1, '30.00')])

    def test_prices_are_never_read_from_the_request(self):
        quote = self.create(items=[{'product': self.cable.pk, 'quantity': 1, 'price': '1.00', 'unit_price': '1.00'}],
                            total='1.00')
        self.assertEqual(quote['total'], '50.00')

    def test_a_manual_discount_needs_the_discount_capability(self):
        res = self.client.post(URL, self.body(discount_type='amount', discount_value='10', discount_reason='Cliente'),
                               format='json')
        self.assertEqual(res.status_code, 403, res.content)
        self.assertFalse(SalesQuote.objects.exists())
        quote = self.create(client=self.jefe, discount_type='amount', discount_value='10', discount_reason='Cliente')
        self.assertEqual((quote['discount_amount'], quote['total'], quote['discount_source']), ('10.00', '90.00', 'manual'))
        self.assertEqual(quote['discount_authorized_by'], 'sq_jefe')

    def test_another_seller_needs_the_assign_capability(self):
        res = self.client.post(URL, self.body(seller=self.boss.pk), format='json')
        self.assertEqual(res.status_code, 403, res.content)

    def test_a_product_of_another_company_is_not_found(self):
        res = self.client.post(URL, self.body(items=[{'product': self.foreign_product.pk, 'quantity': 1}]),
                               format='json')
        self.assertEqual(res.status_code, 400, res.content)

    def test_the_customer_snapshot_is_frozen_from_the_crm(self):
        from store.models import Customer

        customer = Customer.objects.create(company=self.company, first_name='Ana', last_name='Ruiz',
                                           document_type='dni', document_number='40404040', phone='999111222')
        quote = self.create(customer=customer.pk, customer_name='ignorado')
        self.assertEqual((quote['customer_name'], quote['customer_document_number']), ('Ana Ruiz', '40404040'))
        Customer.objects.filter(pk=customer.pk).update(phone='000')
        self.assertEqual(self.client.get(f"{URL}{quote['id']}/").json()['customer_phone'], '999111222')
        row = self.client.get(URL).json()['results'][0]
        self.assertNotIn('40404040', row['customer_document'])

    def test_validity_is_bounded(self):
        today = timezone.localdate()
        self.assertEqual(self.create()['valid_until'], (today + timedelta(days=quotes.DEFAULT_VALIDITY_DAYS)).isoformat())
        res = self.client.post(URL, self.body(valid_until=(today - timedelta(days=1)).isoformat()), format='json')
        self.assertEqual(res.status_code, 400)
        res = self.client.post(URL, self.body(valid_until=(today + timedelta(days=400)).isoformat()), format='json')
        self.assertEqual(res.status_code, 400)

    def test_every_transition_is_audited_with_the_company(self):
        quote = self.create()
        self.act(quote['id'], 'approve')
        self.act(quote['id'], 'send')
        self.act(quote['id'], 'accept')
        actions = list(AdminAuditLog.objects.filter(target_type='sales_quote', target_id=str(quote['id']))
                       .values_list('action', 'company_id'))
        self.assertEqual([a for a, _c in actions].count('sales_quote_created'), 1)
        for action in ('sales_quote_created', 'sales_quote_approved', 'sales_quote_sent', 'sales_quote_accepted'):
            self.assertIn((action, self.company.pk), actions)


# ---------------------------------------------------------------------------
# Isolation and permissions
# ---------------------------------------------------------------------------

class QuoteIsolationTest(QuoteBase):
    def test_another_company_sees_nothing(self):
        quote = self.create()
        outsider = _m7_user('sq_ajeno')
        membership = Membership.objects.create(user=outsider, company=self.other, role='sales', is_active=True)
        _assign(membership, CompanyRole.objects.get(company=self.other, slug='ventas'))
        cache.clear()
        client = _web_login('sq_ajeno')
        self.assertEqual(client.get(f"{URL}{quote['id']}/").status_code, 404)
        self.assertEqual(client.get(f"{URL}{quote['id']}/pdf/").status_code, 404)
        self.assertEqual(client.post(f"{URL}{quote['id']}/duplicate/").status_code, 404)
        self.assertEqual(client.get(URL).json()['results'], [])

    def test_a_branch_out_of_reach_answers_like_nothing(self):
        quote = self.create(branch=self.branch_b.pk)
        Membership.objects.filter(pk=self.membership.pk).update(branch_access_mode=Membership.ACCESS_MODE_SELECTED)
        MembershipBranchAccess.objects.create(membership=self.membership, branch=self.branch_a)
        cache.clear()
        self.assertEqual(self.client.get(f"{URL}{quote['id']}/").status_code, 404)
        self.assertEqual([r['id'] for r in self.client.get(URL).json()['results']], [])
        res = self.client.post(URL, self.body(branch=self.branch_b.pk), format='json')
        self.assertEqual(res.status_code, 400)

    def test_permissions_per_action(self):
        quote = self.create()
        viewer = self.only_caps('company.view', 'sales.quotes.view')
        self.assertEqual(viewer.get(f"{URL}{quote['id']}/").status_code, 200)
        self.assertEqual(viewer.post(URL, self.body(), format='json').status_code, 403)
        self.assertEqual(viewer.patch(f"{URL}{quote['id']}/", {'notes': 'x'}, format='json').status_code, 403)
        self.assertEqual(viewer.post(f"{URL}{quote['id']}/approve/").status_code, 403)
        self.assertEqual(self.convert(quote['id'], client=viewer).status_code, 403)
        nobody = self.only_caps('company.view', 'sales.pos.use', slug='sq-solo-caja')
        self.assertEqual(nobody.get(URL).status_code, 403)

    def test_the_composer_cannot_approve_and_a_draft_cannot_be_sent(self):
        quote = self.create()
        self.assertEqual(self.client.post(f"{URL}{quote['id']}/approve/").status_code, 403)
        self.act(quote['id'], 'send', client=self.client, expect=409)
        self.assertEqual(self.convert(quote['id']).status_code, 409)


# ---------------------------------------------------------------------------
# Revisions, duplicates, immutability
# ---------------------------------------------------------------------------

class QuoteRevisionTest(QuoteBase):
    def test_a_draft_is_edited_in_place(self):
        quote = self.create()
        res = self.client.patch(f"{URL}{quote['id']}/", {'items': [{'product': self.case.pk, 'quantity': 3}]},
                                format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual((res.json()['id'], res.json()['revision'], res.json()['total']), (quote['id'], 1, '90.00'))
        self.assertEqual(SalesQuoteItem.objects.filter(quote_id=quote['id']).count(), 1)

    def test_editing_an_offered_quote_writes_a_revision_and_supersedes_the_old(self):
        quote = self.approved()
        self.act(quote['id'], 'send')
        res = self.client.patch(f"{URL}{quote['id']}/", {'items': [{'product': self.cable.pk, 'quantity': 3}]},
                                format='json')
        self.assertEqual(res.status_code, 201, res.content)
        new = res.json()
        self.assertEqual((new['number'], new['revision'], new['label'], new['status']),
                         (quote['number'], 2, f"{quote['number']}-R2", 'draft'))
        self.assertEqual(new['source_quote'], quote['id'])
        old = SalesQuote.objects.get(pk=quote['id'])
        self.assertEqual((old.status, old.total), ('superseded', Decimal('100.00')))
        self.assertEqual([h['revision'] for h in new['history']], [1, 2])
        # The old revision is evidence: neither it nor its lines change again.
        old.conditions = 'reescrito'
        with self.assertRaises(ValidationError):
            old.save()
        with self.assertRaises(ValidationError):
            old.items.first().save()
        self.act(quote['id'], 'revise', client=self.client, expect=409)
        self.assertEqual(self.client.patch(f"{URL}{quote['id']}/", {'notes': 'x'}, format='json').status_code, 409)

    def test_an_approved_quote_cannot_be_rewritten_even_directly(self):
        quote = self.approved()
        row = SalesQuote.objects.get(pk=quote['id'])
        row.total = Decimal('1.00')
        with self.assertRaises(ValidationError):
            row.save(update_fields=['total'])
        with self.assertRaises(ValidationError):
            SalesQuoteItem.objects.filter(quote=row).first().delete()

    def test_duplicate_is_a_new_quote_and_leaves_the_original(self):
        quote = self.approved()
        copy = self.act(quote['id'], 'duplicate', client=self.client, expect=201)
        self.assertNotEqual(copy['number'], quote['number'])
        self.assertEqual((copy['revision'], copy['status'], copy['origin'], copy['source_quote']),
                         (1, 'draft', 'duplicate', quote['id']))
        self.assertEqual(SalesQuote.objects.get(pk=quote['id']).status, 'approved')
        self.assertEqual([(i['product'], i['quantity']) for i in copy['items']], [(self.cable.pk, 2)])

    def test_an_unchanged_manual_discount_survives_an_edit_by_someone_without_the_capability(self):
        quote = self.create(client=self.jefe, discount_type='amount', discount_value='10', discount_reason='Cliente')
        res = self.client.patch(f"{URL}{quote['id']}/", {'conditions': 'Otra'}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual((res.json()['discount_amount'], res.json()['discount_authorized_by']), ('10.00', 'sq_jefe'))
        res = self.client.patch(f"{URL}{quote['id']}/", {'discount_value': '20'}, format='json')
        self.assertEqual(res.status_code, 403, res.content)

    def test_approving_a_stale_draft_asks_to_refresh_it(self):
        quote = self.create()
        Product.objects.filter(pk=self.cable.pk).update(price=Decimal('55.00'))
        res = self.jefe.post(f"{URL}{quote['id']}/approve/")
        self.assertEqual((res.status_code, res.json()['code']), (409, 'price_changed'))
        refreshed = self.client.patch(f"{URL}{quote['id']}/", {}, format='json').json()
        self.assertEqual(refreshed['total'], '110.00')
        self.act(quote['id'], 'approve')


# ---------------------------------------------------------------------------
# Nothing moves
# ---------------------------------------------------------------------------

class QuoteMovesNothingTest(QuoteBase):
    def test_no_quote_operation_moves_stock_charges_or_sells(self):
        printer = Printer.objects.create(company=self.company, branch=self.branch_a, name='Caja', host='192.168.1.50',
                                         port=9100)
        before = self.counts()
        quote = self.create()
        self.client.patch(f"{URL}{quote['id']}/", {'items': [{'product': self.cable.pk, 'quantity': 9}]}, format='json')
        self.act(quote['id'], 'approve')
        self.act(quote['id'], 'send')
        revision = self.client.patch(f"{URL}{quote['id']}/", {'notes': 'r2'}, format='json').json()
        self.act(revision['id'], 'approve')
        self.act(revision['id'], 'reject', note='Muy caro')
        copy = self.act(revision['id'], 'duplicate', client=self.client, expect=201)
        self.act(copy['id'], 'approve')
        self.act(copy['id'], 'accept')
        for kind in ('a4', 'ticket80'):
            self.assertEqual(self.client.get(f"{URL}{copy['id']}/pdf/", {'format': kind}).status_code, 200)
        res = self.client.post(f"{URL}{copy['id']}/print/", {'printer': printer.pk}, format='json',
                               HTTP_IDEMPOTENCY_KEY='sq-print-0001')
        self.assertEqual(res.status_code, 201, res.content)
        SalesQuote.objects.filter(pk=copy['id']).update(valid_until=timezone.localdate() - timedelta(days=1))
        call_command('expire_sales_quotes', stdout=StringIO())
        self.assertEqual(self.counts(), before)


# ---------------------------------------------------------------------------
# Conversion
# ---------------------------------------------------------------------------

class QuoteConvertTest(QuoteBase):
    def test_conversion_is_a_counter_sale_linked_to_the_quote(self):
        quote = self.approved()
        res = self.convert(quote['id'])
        self.assertEqual(res.status_code, 201, res.content)
        sale = res.json()['sale']
        order = Order.objects.get(pk=sale['order_id'])
        self.assertEqual((order.source_quote_id, order.total, order.sales_channel), (quote['id'], Decimal('100.00'), 'pos'))
        self.assertEqual(self.stock(), 8)
        stored = SalesQuote.objects.get(pk=quote['id'])
        self.assertEqual((stored.status, stored.converted_order_id), ('converted', order.pk))
        self.assertEqual(stored.items.count(), 1)       # the quote itself is kept as it was
        self.assertTrue(AdminAuditLog.objects.filter(action='sales_quote_converted', company=self.company).exists())

    def test_a_retry_with_the_same_key_answers_the_same_sale_and_another_key_is_refused(self):
        quote = self.approved()
        first = self.convert(quote['id'])
        again = self.convert(quote['id'])
        self.assertEqual((first.status_code, again.status_code), (201, 200))
        self.assertEqual(first.json()['sale']['order_id'], again.json()['sale']['order_id'])
        other = self.convert(quote['id'], key='sq-convert-0002')
        self.assertEqual((other.status_code, other.json()['code']), (409, 'already_converted'))
        self.assertEqual(other.json()['order_id'], first.json()['sale']['order_id'])
        self.assertEqual(Order.objects.filter(source_quote_id=quote['id']).count(), 1)
        self.assertEqual(self.stock(), 8)

    def test_a_price_change_is_a_409_with_both_totals_until_somebody_decides(self):
        quote = self.approved()
        Product.objects.filter(pk=self.cable.pk).update(price=Decimal('60.00'))
        res = self.convert(quote['id'])
        self.assertEqual(res.status_code, 409, res.content)
        body = res.json()
        self.assertEqual((body['code'], body['quoted_total'], body['current_total']), ('price_changed', '100.00', '120.00'))
        self.assertFalse(body['can_keep_quoted'])     # the cashier has no discount authority
        self.assertFalse(Order.objects.exists())
        self.assertEqual(SalesQuote.objects.get(pk=quote['id']).status, 'approved')
        # Keeping the quoted price is a discount: refused without the capability.
        res = self.convert(quote['id'], price_decision='quoted')
        self.assertEqual(res.status_code, 403, res.content)
        # Charging today's price, with the total read, goes through.
        res = self.convert(quote['id'], price_decision='current', expected_total='120.00')
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(Order.objects.get().total, Decimal('120.00'))

    def test_a_supervisor_may_keep_the_quoted_price_as_an_audited_discount(self):
        quote = self.approved()
        Product.objects.filter(pk=self.cable.pk).update(price=Decimal('60.00'))
        res = self.convert(quote['id'], client=self.jefe)
        self.assertTrue(res.json()['can_keep_quoted'])
        res = self.convert(quote['id'], client=self.jefe, price_decision='quoted')
        self.assertEqual(res.status_code, 201, res.content)
        order = Order.objects.get()
        self.assertEqual((order.total, order.discount_amount, order.discount_source), (Decimal('100.00'), Decimal('20.00'), 'manual'))
        self.assertIn(quote['number'], order.discount_reason)
        self.assertTrue(AdminAuditLog.objects.filter(action='pos_manual_discount_applied', target_id=str(order.pk)).exists())

    def test_a_quoted_manual_discount_is_honoured_and_credited_to_who_decided_it(self):
        quote = self.create(client=self.jefe, discount_type='amount', discount_value='10', discount_reason='Cliente')
        self.act(quote['id'], 'approve')
        res = self.convert(quote['id'])        # the cashier has no discount authority of their own
        self.assertEqual(res.status_code, 201, res.content)
        order = Order.objects.get()
        self.assertEqual((order.total, order.discount_authorized_by_id), (Decimal('90.00'), self.boss.pk))

    def test_stock_gone_refuses_the_sale_and_keeps_the_quote(self):
        quote = self.approved()
        BranchStock.objects.filter(branch=self.branch_a, product=self.cable).update(quantity=1)
        res = self.convert(quote['id'])
        self.assertEqual((res.status_code, res.json()['code']), (409, 'insufficient_stock'))
        self.assertFalse(Order.objects.exists())
        self.assertEqual(SalesQuote.objects.get(pk=quote['id']).status, 'approved')
        self.assertEqual(self.stock(), 1)

    def test_converting_needs_the_till_and_terms(self):
        quote = self.approved()
        res = self.convert(quote['id'], terms_confirmed=False)
        self.assertEqual(res.status_code, 400, res.content)
        res = self.client.post(f"{URL}{quote['id']}/convert/", {'terms_confirmed': True}, format='json')
        self.assertEqual(res.status_code, 400, res.content)     # no idempotency key

    def test_an_expired_quote_is_not_converted_and_reads_expired(self):
        quote = self.approved()
        SalesQuote.objects.filter(pk=quote['id']).update(valid_until=timezone.localdate() - timedelta(days=1))
        self.assertEqual(self.client.get(f"{URL}{quote['id']}/").json()['status'], 'expired')
        res = self.convert(quote['id'])
        self.assertEqual((res.status_code, res.json()['code']), (409, 'expired'))
        self.act(quote['id'], 'send', expect=409)


class QuoteUsedUnitTest(QuoteBase):
    def setUp(self):
        super().setUp()
        from store import stock_unit_services as units
        from store.test_stock_units import IMEI_A

        self.used = Product.objects.create(
            company=self.company, name='Equipo seminuevo', slug='equipo-seminuevo', price='0.00', inventory=0,
            is_serialized=True, requires_imei=True, condition_line=Product.CONDITION_USED)
        self.unit, = units.receive_units(branch=self.branch_a, product=self.used, actor=self.staff, reason='Compra',
                                         units=[{'serial_number': 'USADOSQ001', 'imei': IMEI_A, 'condition': 'used',
                                                 'cost': '900.00', 'sale_price': '1500.00'}])

    def inspected(self):
        """USED-READY-01: approve the device's inspection with the real service."""
        from store.used_unit_test_support import approve

        self.unit = approve(self.unit, self.boss)

    def test_a_used_device_is_quoted_by_the_device_and_masked(self):
        self.inspected()
        res = self.client.post(URL, self.body(items=[{'product': self.used.pk, 'quantity': 1}]), format='json')
        self.assertEqual(res.status_code, 400, res.content)
        quote = self.create(items=[{'product': self.used.pk, 'quantity': 1, 'unit': self.unit.pk}])
        self.assertEqual((quote['total'], quote['items'][0]['priced_by']), ('1500.00', 'unit'))
        self.assertNotIn(self.unit.imei, str(quote))
        self.assertEqual(StockUnit.objects.get(pk=self.unit.pk).status, 'available')   # named, never reserved

    def test_an_uninspected_used_device_is_not_quoted(self):
        res = self.client.post(URL, self.body(items=[{'product': self.used.pk, 'quantity': 1, 'unit': self.unit.pk}]),
                               format='json')
        self.assertEqual((res.status_code, res.json()['code']), (409, 'unit_not_inspected'))
        self.assertFalse(SalesQuote.objects.exists())

    def test_the_used_ready_rule_refuses_quoting_and_converting(self):
        self.inspected()
        quote = self.create(items=[{'product': self.used.pk, 'quantity': 1, 'unit': self.unit.pk}])
        self.act(quote['id'], 'approve')
        refusal = 'este seminuevo todavía no tiene la inspección aprobada.'
        with patch('store.sales_quote_services.used_unit_till_refusal', return_value=refusal):
            res = self.client.post(URL, self.body(items=[{'product': self.used.pk, 'quantity': 1, 'unit': self.unit.pk}]),
                                   format='json')
            self.assertEqual((res.status_code, res.json()['code']), (409, 'unit_not_inspected'))
            res = self.convert(quote['id'])
            self.assertEqual((res.status_code, res.json()['code']), (409, 'unit_not_inspected'))
        res = self.convert(quote['id'], amount_received='2000.00')
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(StockUnit.objects.get(pk=self.unit.pk).status, 'sold')

    def test_the_adapter_applies_the_used_ready_rule_of_the_till(self):
        from store import used_unit_services

        self.assertEqual(quotes.used_unit_till_refusal(self.unit), used_unit_services.till_refusal(self.unit))
        self.assertTrue(quotes.used_unit_till_refusal(self.unit))
        self.inspected()
        self.assertEqual(quotes.used_unit_till_refusal(self.unit), '')


# ---------------------------------------------------------------------------
# Expiry
# ---------------------------------------------------------------------------

class QuoteExpiryTest(QuoteBase):
    def test_the_command_stamps_only_live_quotes_past_their_validity(self):
        live = self.approved()
        draft = self.create()
        fresh = self.approved()
        yesterday = timezone.localdate() - timedelta(days=1)
        SalesQuote.objects.filter(pk__in=[live['id'], draft['id']]).update(valid_until=yesterday)
        out = StringIO()
        call_command('expire_sales_quotes', stdout=out)
        self.assertIn('1', out.getvalue())
        self.assertEqual(SalesQuote.objects.get(pk=live['id']).status, 'expired')
        self.assertEqual(SalesQuote.objects.get(pk=draft['id']).status, 'draft')
        self.assertEqual(SalesQuote.objects.get(pk=fresh['id']).status, 'approved')
        self.assertTrue(AdminAuditLog.objects.filter(action='sales_quote_expired', company=self.company).exists())
        call_command('expire_sales_quotes', stdout=StringIO())
        self.assertEqual(AdminAuditLog.objects.filter(action='sales_quote_expired').count(), 1)

    def test_an_expired_quote_is_renewed_by_revising_it(self):
        quote = self.approved()
        SalesQuote.objects.filter(pk=quote['id']).update(valid_until=timezone.localdate() - timedelta(days=1))
        new = self.act(quote['id'], 'revise', client=self.client, expect=201)
        self.assertEqual(new['status'], 'draft')
        self.assertGreaterEqual(new['valid_until'], timezone.localdate().isoformat())

    def test_the_list_filters_by_status(self):
        draft = self.create()
        approved = self.approved()
        rows = self.client.get(URL, {'status': 'approved'}).json()['results']
        self.assertEqual([r['id'] for r in rows], [approved['id']])
        rows = self.client.get(URL, {'status': 'draft'}).json()['results']
        self.assertEqual([r['id'] for r in rows], [draft['id']])
        self.assertEqual(self.client.get(URL, {'status': 'nada'}).status_code, 400)
        rows = self.client.get(URL, {'q': approved['number']}).json()['results']
        self.assertEqual([r['id'] for r in rows], [approved['id']])


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------

class QuoteDocumentTest(QuoteBase):
    def test_a4_and_ticket_say_what_they_are_and_name_no_fiscal_document(self):
        quote = self.approved()
        for kind in ('a4', 'ticket80'):
            res = self.client.get(f"{URL}{quote['id']}/pdf/", {'format': kind})
            self.assertEqual(res.status_code, 200)
            self.assertTrue(res.content.startswith(b'%PDF'))
            self.assertEqual(res['Cache-Control'], 'no-store')
            text = pdf_text(res.content)
            for expected in (SALES_QUOTE_TITLE, quote['number'], SALES_QUOTE_LEGEND, 'Cable USB-C', 'Pago al contado'):
                self.assertIn(expected, text, f'{kind}: {expected}')
            self.assertFalse(names_fiscal_document(text.replace(SALES_QUOTE_LEGEND, '')), kind)
        self.assertEqual(self.client.get(f"{URL}{quote['id']}/pdf/", {'format': 'xml'}).status_code, 400)

    def test_a_draft_says_it_is_a_draft(self):
        quote = self.create()
        text = pdf_text(self.client.get(f"{URL}{quote['id']}/pdf/").content)
        self.assertIn('BORRADOR', text)

    def test_the_internal_notes_never_reach_the_paper(self):
        quote = self.create(notes='Margen interno secreto')
        text = pdf_text(self.client.get(f"{URL}{quote['id']}/pdf/").content)
        self.assertNotIn('secreto', text)

    def test_the_thermal_ticket_goes_through_the_print_queue_once(self):
        from store.printing import escpos
        from store.printing import services as printing
        from store.sales_quote_documents import escpos_ticket

        quote = self.approved()
        printer = Printer.objects.create(company=self.company, branch=self.branch_a, name='Caja', host='192.168.1.50',
                                         port=9100)
        first = self.client.post(f"{URL}{quote['id']}/print/", {}, format='json', HTTP_IDEMPOTENCY_KEY='sq-print-0001')
        again = self.client.post(f"{URL}{quote['id']}/print/", {}, format='json', HTTP_IDEMPOTENCY_KEY='sq-print-0001')
        self.assertEqual((first.status_code, again.status_code), (201, 200), first.content)
        job = PrintJob.objects.get()
        self.assertEqual((job.kind, job.sales_quote_id, job.order_id, job.printer_id),
                         ('sales_quote_ticket', quote['id'], None, printer.pk))
        payload = printing.render(job)
        self.assertIn(b'COTIZACI', payload)
        self.assertIn(SALES_QUOTE_LEGEND.encode('cp858'), payload)
        self.assertEqual(payload, escpos_ticket(job.sales_quote, paper_width_mm=printer.paper_width_mm,
                                                encoding=printer.encoding,
                                                dots=escpos.printable_dots(printer.paper_width_mm,
                                                                                    printer.print_width_dots)))
        agent, _token = printing.create_agent(company=self.company, branch=self.branch_a, name='PC', actor=self.staff)
        claimed = printing.claim_next(agent)
        self.assertEqual(claimed.pk, job.pk)

    def test_the_document_preview_draws_a_sample_quote(self):
        from store import document_preview

        for kind in ('sales_quote', 'ticket80_sales_quote'):
            text = pdf_text(document_preview.render_preview(kind, company=self.company))
            self.assertIn(SALES_QUOTE_TITLE, text)
            self.assertIn(document_preview.SAMPLE_NUMBER, text)
            self.assertNotIn('000000000000001', text)       # the sample device is masked too
