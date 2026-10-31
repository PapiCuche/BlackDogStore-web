"""
SALES-EMAIL-03 — a copy of one document of a sale, to an address that is not the customer's.

What is pinned here:

  · it goes to exactly the typed address, once, with the right attachment, and
    the customer's e-mail and the order's mail flags stay as they were;
  · anything that is not ONE plain address is refused before anything is sent;
  · another company's or another branch's order is 404; without
    `sales.documents.send_copy` AND `sales.orders.manage` it is 403;
  · the copy has its own throttle;
  · the audit row has the address masked and a digest, never the address;
  · a PDF that cannot be drawn sends nothing;
  · an internal note is never called boleta or factura, and an electronic
    receipt only goes when a real one exists.
"""
import json
import smtplib
from decimal import Decimal
from unittest.mock import patch

from django.apps import apps
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from store import order_document_copy as copies
from store.models import (
    AdminAuditLog, FiscalDocument, FiscalDocumentStatus, FiscalDocumentType, FiscalEnvironment,
    FiscalSeries, Order, OrderItem, UserProfile,
)
from store.tests import (
    _c1_product, _make_paid_order_43, _make_user_43, _p2d_branch, _p2d_member, _p3_company,
)

COPY = 'sales.documents.send_copy'
MANAGE = 'sales.orders.manage'
ALL = (COPY, MANAGE, 'sales.orders.view', 'sales.notes.manage', 'sales.fiscal.view', 'company.view')
ISSUER_RUC = '20100066603'
ALTERNATE = 'contador@empresa-cliente.test'

_MAIL = override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    DEFAULT_FROM_EMAIL='noreply@tienda.test',
)


def _order(company, *, branch=None, **extra):
    fields = dict(
        customer_name='Rosa Quispe', customer_email='rosa.cliente@correo.test',
        customer_phone='987654321', document_type='dni', document_number='45678912',
        delivery_method='pickup_store', receipt_type='boleta',
        total=Decimal('118.00'), discount_amount=Decimal('0.00'), subtotal_amount=Decimal('118.00'),
        taxable_amount=Decimal('100.00'), tax_amount=Decimal('18.00'), tax_rate=Decimal('0.18'),
        tax_treatment='taxed', currency='PEN',
        status=Order.Status.PAID, paid=True, paid_at=timezone.now(),
        fulfillment_branch=branch or company.default_inventory_branch,
    )
    fields.update(extra)
    order = Order.objects.create(company=company, **fields)
    product = _c1_product(company, f'Funda {order.pk}', '118.00')
    OrderItem.objects.create(order=order, product=product, quantity=1, price=Decimal('118.00'))
    return order


@_MAIL
class CopyBase(TestCase):
    def setUp(self):
        cache.clear()
        mail.outbox = []
        self.company = _p3_company(
            'copia-a', 'Tienda Copia', tax_id=ISSUER_RUC, legal_name='TIENDA COPIA SAC')
        self.other = _p3_company('copia-b', 'Otra Tienda')
        self.order = _order(self.company)
        self.admin, _ = _p2d_member(self.company, 'copy_admin', ALL)
        self.url = f'/api/admin/orders/{self.order.pk}/document-copy/'

    def client_for(self, user):
        api = APIClient()
        api.force_authenticate(user)
        return api

    def post(self, user=None, **body):
        payload = {'document': 'confirmation', 'to': ALTERNATE, 'to_confirmation': ALTERNATE, **body}
        return self.client_for(user or self.admin).post(self.url, payload, format='json')

    def sales_note(self):
        from store.sales_note_services import get_or_create_sales_note

        return get_or_create_sales_note(self.order, actor=self.admin)[0]

    def fiscal(self, *, environment=FiscalEnvironment.PRODUCTION, status=FiscalDocumentStatus.ACCEPTED):
        from store.fiscal.testing import self_signed_pem
        from store.fiscal_services import get_or_create_fiscal_document, sign_fiscal_document

        FiscalSeries.objects.get_or_create(
            company=self.company, document_type=FiscalDocumentType.RECEIPT, series='B001')
        document, _ = get_or_create_fiscal_document(self.order)
        key, cert = self_signed_pem(ISSUER_RUC)
        document = sign_fiscal_document(document, key_pem=key, cert_pem=cert)
        # The beta switch is off in this suite: a production, accepted receipt is
        # written directly — what the copy has to recognise, not how SUNAT gets there.
        FiscalDocument.objects.filter(pk=document.pk).update(environment=environment, status=status)
        document.refresh_from_db()
        return document


class SendsToTheTypedAddressOnlyTest(CopyBase):
    def test_the_confirmation_goes_to_the_alternate_address_and_nowhere_else(self):
        before = Order.objects.filter(pk=self.order.pk).values(
            'customer_email', 'confirmation_email_sent_at', 'email_send_error').get()

        response = self.post(to_confirmation=ALTERNATE.upper())

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, [ALTERNATE])
        self.assertEqual(message.cc, [])
        self.assertEqual(message.bcc, [])
        self.assertEqual(message.recipients(), [ALTERNATE])
        self.assertIn(f'#{self.order.pk}', message.subject)
        self.assertEqual([(name, kind) for name, _, kind in message.attachments],
                         [(f'copia-a-pedido-{self.order.pk}.pdf', 'application/pdf')])
        self.assertTrue(message.attachments[0][1].startswith(b'%PDF'))
        self.assertEqual(response.json()['sent_to'], ALTERNATE)
        self.assertEqual(response.json()['attachment_name'], f'copia-a-pedido-{self.order.pk}.pdf')
        # The customer and the order's own mail record are untouched.
        after = Order.objects.filter(pk=self.order.pk).values(
            'customer_email', 'confirmation_email_sent_at', 'email_send_error').get()
        self.assertEqual(after, before)

    def test_the_sales_note_goes_as_an_internal_note_never_as_a_receipt(self):
        note = self.sales_note()

        response = self.post(document='sales_note')

        self.assertEqual(response.status_code, 200, response.content)
        message = mail.outbox[0]
        self.assertEqual(message.to, [ALTERNATE])
        self.assertIn(f'Nota de venta interna {note.number}', message.subject)
        name = message.attachments[0][0]
        self.assertTrue(name.startswith('copia-a-nota-venta-'), name)
        html = message.alternatives[0][0]
        for text in (message.subject, message.body, html, name, response.json()['document_label']):
            self.assertNotIn('boleta', text.lower())
            self.assertNotIn('factura', text.lower())
        self.assertIn('sin validez tributaria', message.body)
        self.assertEqual(Order.objects.get(pk=self.order.pk).customer_email, 'rosa.cliente@correo.test')

    def test_a_real_electronic_receipt_goes_with_its_own_name(self):
        document = self.fiscal()

        response = self.post(document='fiscal')

        self.assertEqual(response.status_code, 200, response.content)
        message = mail.outbox[0]
        self.assertEqual(message.to, [ALTERNATE])
        self.assertIn(f'Boleta de venta electrónica {document.document_id}', message.subject)
        self.assertEqual(message.attachments[0][0], f'{ISSUER_RUC}-03-B001-{document.number}.pdf')
        self.assertNotIn('sin validez tributaria', message.body)

    def test_the_tenant_name_in_the_body_is_escaped(self):
        from store.models import Company

        Company.objects.filter(pk=self.company.pk).update(name='<b>Tienda</b>')
        Order.objects.filter(pk=self.order.pk).update(company_snapshot={})
        self.sales_note()
        self.post(document='sales_note')
        html = mail.outbox[0].alternatives[0][0]
        self.assertNotIn('<b>Tienda</b>', html)


class WhatIsOfferedTest(CopyBase):
    def documents(self, user=None):
        response = self.client_for(user or self.admin).get(self.url)
        self.assertEqual(response.status_code, 200, response.content)
        return {entry['kind']: entry for entry in response.json()['documents']}

    def test_lists_what_exists_and_says_why_the_rest_does_not(self):
        docs = self.documents()
        self.assertTrue(docs['confirmation']['available'])
        self.assertIn('constancia de pedido', docs['confirmation']['label'].lower())
        self.assertIn('No es un comprobante electrónico', docs['confirmation']['note'])
        self.assertFalse(docs['sales_note']['available'])
        self.assertIn('todavía no tiene nota de venta', docs['sales_note']['unavailable_reason'])
        self.assertFalse(docs['fiscal']['available'])
        self.assertEqual(docs['fiscal']['unavailable_reason'], 'Esta venta no tiene comprobante electrónico.')
        for entry in docs.values():
            for word in ('boleta', 'factura'):
                self.assertNotIn(word, entry['label'].lower())

    def test_a_beta_receipt_is_not_offered_and_cannot_be_sent(self):
        self.fiscal(environment=FiscalEnvironment.BETA, status=FiscalDocumentStatus.SIGNED)
        entry = self.documents()['fiscal']
        self.assertFalse(entry['available'])
        self.assertIn('entorno de pruebas', entry['unavailable_reason'])
        self.assertEqual(entry['attachment_name'], '')

        response = self.post(document='fiscal')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(mail.outbox, [])

    def test_a_production_receipt_not_accepted_is_not_offered(self):
        self.fiscal(status=FiscalDocumentStatus.SUBMISSION_ERROR)
        self.assertIn('no fue aceptado', self.documents()['fiscal']['unavailable_reason'])
        self.assertEqual(self.post(document='fiscal').status_code, 400)
        self.assertEqual(mail.outbox, [])

    def test_a_voided_receipt_is_not_offered(self):
        self.fiscal()
        with patch('store.fiscal_void_services.is_voided', return_value=True):
            self.assertIn('dado de baja', self.documents()['fiscal']['unavailable_reason'])
            self.assertEqual(self.post(document='fiscal').status_code, 400)
        self.assertEqual(mail.outbox, [])

    def test_a_sales_note_that_does_not_exist_is_not_issued_by_asking(self):
        from store.models import SalesNote

        response = self.post(document='sales_note')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(SalesNote.objects.filter(order=self.order).exists())
        self.assertEqual(mail.outbox, [])

    def test_a_document_the_caller_cannot_download_is_neither_listed_nor_sent(self):
        narrow, _ = _p2d_member(self.company, 'copy_narrow', (COPY, MANAGE, 'company.view'))
        self.sales_note()
        self.fiscal()
        self.assertEqual(set(self.documents(narrow)), {'confirmation'})
        for kind in ('sales_note', 'fiscal'):
            self.assertEqual(self.post(narrow, document=kind).status_code, 400, kind)
        self.assertEqual(mail.outbox, [])

    def test_an_unknown_document_is_refused(self):
        for kind in ('boleta', '', None, ['confirmation']):
            with self.subTest(kind=kind):
                self.assertEqual(self.post(document=kind).status_code, 400)
        self.assertEqual(mail.outbox, [])

    def test_an_unpaid_order_sends_nothing(self):
        Order.objects.filter(pk=self.order.pk).update(status=Order.Status.PENDING_PAYMENT, paid=False)
        body = self.client_for(self.admin).get(self.url).json()
        self.assertFalse(body['can_send'])
        self.assertEqual(body['documents'], [])
        self.assertEqual(self.post().status_code, 400)
        self.assertEqual(mail.outbox, [])


class OneAddressOnlyTest(CopyBase):
    REFUSED = (
        '', '   ', 'sin-arroba', 'a@b', '@empresa.test', 'ana@', 'ana@@empresa.test',
        'ana@empresa.test,otro@empresa.test', 'ana@empresa.test;otro@empresa.test',
        'ana@empresa.test otro@empresa.test', '"Ana" <ana@empresa.test>', 'Ana <ana@empresa.test>',
        '<ana@empresa.test>', 'ana@empresa.test>', 'ana@empresa.test\r\nBcc: espia@fuera.test',
        'ana@empresa.test\nBcc: espia@fuera.test', 'ana@empresa.test\r', '\nana@empresa.test',
        'ana@empresa.test\x00', 'añá@empresa.test', 'ana@empresá.test', 'ana(x)@empresa.test',
        'ana@[127.0.0.1]', 'ana\\@empresa.test', 'grupo: ana@empresa.test;',
        ('a' * 245) + '@empresa.test',
    )

    def test_anything_but_one_plain_address_is_refused_and_nothing_is_sent(self):
        for raw in self.REFUSED:
            with self.subTest(raw=raw):
                cache.clear()
                response = self.post(to=raw, to_confirmation=raw)
                self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(mail.outbox, [])
        self.assertFalse(AdminAuditLog.objects.filter(action__startswith='order_document_copy').exists())

    def test_not_a_string_is_refused(self):
        for raw in (None, ['ana@empresa.test'], {'a': 'ana@empresa.test'}, 42):
            with self.subTest(raw=raw):
                cache.clear()
                self.assertEqual(self.post(to=raw, to_confirmation='ana@empresa.test').status_code, 400)
        self.assertEqual(mail.outbox, [])

    def test_the_two_addresses_must_match(self):
        self.assertEqual(self.post(to_confirmation='otro@empresa-cliente.test').status_code, 400)
        cache.clear()
        response = self.client_for(self.admin).post(
            self.url, {'document': 'confirmation', 'to': ALTERNATE}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['detail'], 'Los dos correos no coinciden.')
        self.assertEqual(mail.outbox, [])

    def test_the_rules_themselves(self):
        self.assertEqual(copies.normalise_recipient('  ana.perez+ventas@empresa.test '), 'ana.perez+ventas@empresa.test')
        longest = 'a' * 64 + '@' + ('b' * 60 + '.') * 3 + 'cccccc'
        self.assertEqual(len(longest), 254)
        self.assertEqual(copies.normalise_recipient(longest), longest)
        for too_long in ('a' * 64 + '@' + ('b' * 60 + '.') * 3 + 'ccccccc', 'a' * 65 + '@empresa.test'):
            with self.assertRaises(copies.CopyAddressError):
                copies.normalise_recipient(too_long)
        with self.assertRaises(copies.CopyAddressError):
            copies.normalise_recipient('ana@empresa.test', 'otra@empresa.test')
        self.assertEqual(copies.normalise_recipient('Ana@Empresa.test', 'ana@empresa.TEST'), 'Ana@Empresa.test')


class TenancyAndAuthorityTest(CopyBase):
    def test_another_companys_order_is_404_and_nothing_is_sent(self):
        intruder, _ = _p2d_member(self.other, 'copy_intruder', ALL)
        api = self.client_for(intruder)
        self.assertEqual(api.get(self.url).status_code, 404)
        self.assertEqual(self.post(intruder).status_code, 404)
        self.assertEqual(mail.outbox, [])

    def test_an_order_of_a_branch_the_caller_does_not_operate_is_404(self):
        elsewhere = _p2d_branch(self.company, 'Otra sucursal')
        member, _ = _p2d_member(self.company, 'copy_branch', ALL, branches=[elsewhere])
        self.assertEqual(self.post(member).status_code, 404)
        self.assertEqual(mail.outbox, [])

    def test_without_the_copy_capability_it_is_403(self):
        manager, _ = _p2d_member(self.company, 'copy_manager',
                                 tuple(code for code in ALL if code != COPY))
        self.assertEqual(self.client_for(manager).get(self.url).status_code, 403)
        self.assertEqual(self.post(manager).status_code, 403)
        self.assertEqual(mail.outbox, [])

    def test_the_copy_capability_alone_is_not_enough(self):
        lone, _ = _p2d_member(self.company, 'copy_lone', (COPY, 'company.view'))
        self.assertEqual(self.post(lone).status_code, 403)
        self.assertEqual(mail.outbox, [])

    def test_the_counter_preset_cannot_send_to_another_address(self):
        from store.models import Membership, MembershipRoleAssignment
        from store.tests import _saas_user

        clerk = _saas_user('copy_clerk')
        membership = Membership.objects.create(user=clerk, company=self.company, role=UserProfile.ROLE_CUSTOMER)
        MembershipRoleAssignment.objects.create(
            membership=membership, role=self.company.roles.get(slug='ventas'))
        self.assertEqual(self.post(clerk).status_code, 403)
        self.assertEqual(mail.outbox, [])

    def test_anonymous_is_401(self):
        self.assertEqual(APIClient().post(self.url, {}, format='json').status_code, 401)


@_MAIL
class LegacyBridgeTest(TestCase):
    """A pre-SaaS operator on the pilot: admin/superadmin, as for the resend."""

    def setUp(self):
        cache.clear()
        mail.outbox = []
        self.order = _make_paid_order_43()
        self.url = f'/api/admin/orders/{self.order.pk}/document-copy/'

    def post(self, role):
        api = APIClient()
        api.force_authenticate(_make_user_43(f'copy_{role}', role))
        return api.post(self.url, {'document': 'confirmation', 'to': ALTERNATE, 'to_confirmation': ALTERNATE},
                        format='json')

    def test_a_legacy_admin_may(self):
        self.assertEqual(self.post(UserProfile.ROLE_ADMIN).status_code, 200)
        self.assertEqual(mail.outbox[0].to, [ALTERNATE])
        self.assertEqual(Order.objects.get(pk=self.order.pk).customer_email, 'marco@example.com')

    def test_legacy_sales_inventory_and_technicians_may_not(self):
        for role in (UserProfile.ROLE_SALES, UserProfile.ROLE_INVENTORY, UserProfile.ROLE_TECHNICIAN,
                     UserProfile.ROLE_CUSTOMER):
            with self.subTest(role=role):
                self.assertEqual(self.post(role).status_code, 403)
        self.assertEqual(mail.outbox, [])


class ThrottleTest(CopyBase):
    def test_the_copy_has_its_own_limit_per_user(self):
        statuses = [self.post(to='no-es-correo', to_confirmation='no-es-correo').status_code for _ in range(6)]
        self.assertEqual(statuses[:5], [400] * 5)
        self.assertEqual(statuses[5], 429)
        # Reading what can be sent is not sending.
        self.assertEqual(self.client_for(self.admin).get(self.url).status_code, 200)
        # Another person keeps their own allowance.
        colleague, _ = _p2d_member(self.company, 'copy_colleague', ALL)
        self.assertEqual(self.post(colleague).status_code, 200)

    def test_there_is_an_hourly_ceiling_as_well(self):
        from django.conf import settings

        rates = settings.REST_FRAMEWORK['DEFAULT_THROTTLE_RATES']
        self.assertEqual(rates['admin_order_document_copy'], '5/min')
        self.assertEqual(rates['admin_order_document_copy_hour'], '30/hour')


class AuditTest(CopyBase):
    def test_the_row_names_actor_order_document_and_result_with_the_address_masked(self):
        self.post()
        row = AdminAuditLog.objects.get(action='order_document_copy_sent')
        self.assertEqual(row.actor, self.admin)
        self.assertEqual(row.company, self.company)
        self.assertEqual((row.target_type, row.target_id), ('order', str(self.order.pk)))
        self.assertEqual(row.metadata['document'], 'confirmation')
        self.assertEqual(row.metadata['result'], 'sent')
        self.assertEqual(row.metadata['recipient_masked'], 'co***@empresa-cliente.test')
        self.assertEqual(row.metadata['recipient_digest'], copies.recipient_digest(ALTERNATE.upper()))
        stored = json.dumps(row.metadata)
        self.assertNotIn(ALTERNATE, stored)
        self.assertNotIn('contador', stored)
        self.assertNotIn('rosa.cliente@correo.test', stored)

    def test_the_address_does_not_reach_the_server_log(self):
        with self.assertLogs('store', level='INFO') as logs, \
                patch('django.core.mail.EmailMultiAlternatives.send', side_effect=smtplib.SMTPException('down')):
            self.post()
        self.assertNotIn('contador', '\n'.join(logs.output))

    def test_masking(self):
        self.assertEqual(copies.mask_email('juan@gmail.com'), 'ju***@gmail.com')
        self.assertEqual(copies.mask_email('ana@gmail.com'), 'a***@gmail.com')
        self.assertEqual(copies.mask_email('x@empresa.pe'), 'x***@empresa.pe')
        self.assertEqual(copies.mask_email(''), '***')


class FailuresTest(CopyBase):
    def test_a_pdf_that_cannot_be_drawn_sends_nothing(self):
        with patch('store.pdf_services.generate_order_receipt_pdf', side_effect=RuntimeError('boom')):
            response = self.post()
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()['code'], 'pdf_failed')
        self.assertIn('No se envió ningún correo', response.json()['detail'])
        self.assertEqual(mail.outbox, [])
        row = AdminAuditLog.objects.get(action='order_document_copy_failed')
        self.assertEqual(row.metadata['result'], 'pdf_error')
        self.assertNotIn(ALTERNATE, json.dumps(row.metadata))
        # The customer's own record does not carry a failure of a mail it never got.
        self.assertEqual(Order.objects.get(pk=self.order.pk).email_send_error, self.order.email_send_error)

    def test_a_sales_note_pdf_that_fails_sends_nothing(self):
        self.sales_note()
        with patch('store.sales_note_services.generate_sales_note_pdf', side_effect=RuntimeError('boom')):
            response = self.post(document='sales_note')
        self.assertEqual(response.status_code, 500)
        self.assertEqual(mail.outbox, [])

    def test_a_mail_server_failure_is_502_and_audited(self):
        with patch('django.core.mail.EmailMultiAlternatives.send', side_effect=smtplib.SMTPException('pw=secret')):
            response = self.post()
        self.assertEqual(response.status_code, 502)
        self.assertNotIn(b'secret', response.content)
        self.assertEqual(AdminAuditLog.objects.get(action='order_document_copy_failed').metadata['result'],
                         'smtp_error')
        order = Order.objects.get(pk=self.order.pk)
        self.assertEqual((order.customer_email, order.confirmation_email_sent_at, order.email_send_error),
                         (self.order.customer_email, self.order.confirmation_email_sent_at,
                          self.order.email_send_error))


class CapabilityGrantTest(TestCase):
    """Who holds `sales.documents.send_copy` by default, and what 0161 does."""

    def setUp(self):
        cache.clear()

    def test_the_presets(self):
        company = _p3_company('copia-presets', 'Presets')
        holders = {role.slug for role in company.roles.all() if COPY in role.capabilities}
        self.assertEqual(holders, {'administrador'})

    def test_the_legacy_matrix(self):
        from store.tenancy import LEGACY_ROLE_CAPABILITIES

        for role in (UserProfile.ROLE_ADMIN, UserProfile.ROLE_SUPERADMIN):
            self.assertIn(COPY, LEGACY_ROLE_CAPABILITIES[role])
        for role in (UserProfile.ROLE_SALES, UserProfile.ROLE_INVENTORY,
                     UserProfile.ROLE_TECHNICIAN, UserProfile.ROLE_CUSTOMER):
            self.assertNotIn(COPY, LEGACY_ROLE_CAPABILITIES[role])

    def test_the_migration_grants_holders_of_company_manage_only(self):
        import importlib

        module = importlib.import_module('store.migrations.0161_document_copy_capability')
        company = _p3_company('copia-mig', 'Migración')
        roles = company.roles
        roles.filter(slug='administrador').update(capabilities=sorted(module.ADMIN_PREVIOUS))
        edited = sorted({'company.manage', 'sales.orders.view'})
        roles.filter(slug='inventario').update(capabilities=edited)
        ventas = set(roles.get(slug='ventas').capabilities)

        module.grant(apps, None)

        self.assertEqual(set(roles.get(slug='administrador').capabilities), module.ADMIN_PREVIOUS | {COPY})
        self.assertEqual(set(roles.get(slug='inventario').capabilities), set(edited) | {COPY})
        self.assertEqual(set(roles.get(slug='ventas').capabilities), ventas)
        self.assertNotIn(COPY, ventas)
