"""
SALES-NOTE-CONTROL-01 · the platform master administers issued sales notes.

The owner's request (2026-10-14): «las primeras notas de venta fueron de prueba
y el usuario Master necesita administrarlas». What these tests fix:

  * VOIDING A NOTE IS THE MASTER'S ACT. «Master» in this codebase is the
    platform master (`is_platform_admin`, i.e. `is_superuser`) acting inside
    the company it names. A company administrator holding every capability is
    not the master and is refused.
  * A reason is mandatory, the act is audited, and the note stays: same number,
    same issue date, same lines, marked ANULADA on every paper.
  * Voiding a document is NOT cancelling the order, NOT reversing a payment,
    NOT a return, NOT a credit note and NOT a SUNAT void. Nothing of those moves.
  * Correlatives are never reused and «Próximo número» stays locked.
  * Internal remarks are append-only and never reach the printed note.
  * The list is tenant- and branch-scoped; «prueba» is a reason, not a guess:
    nothing marks an existing note automatically.
"""
from decimal import Decimal
from threading import Barrier, Thread

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient

from store import inventory_services as inventory
from store import order_cancellation as cancelling
from store import sales_note_control as control
from store.models import (
    AdminAuditLog, Branch, BranchStock, Company, FiscalDocument, FiscalDocumentType, FiscalEnvironment,
    FiscalSeries, InternalSequence, Membership, Order, OrderItem, PaymentTransaction, Product, SalesNote,
    SalesNoteRemark, StockMovement,
)
from store.sales_note_services import generate_sales_note_pdf, get_or_create_sales_note
from store.test_document_design import text_of
from store.tests import M7InventoryBase, _m7_stock, _p2d_member, _prod

User = get_user_model()

REASON = 'Nota emitida durante las pruebas de puesta en marcha'


def _master(username='master_ctl'):
    return User.objects.create_user(
        username=username, email=f'{username}@example.com', password='Pass123!',
        is_superuser=True, is_staff=True,
    )


class _Base(M7InventoryBase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.master = _master()
        self.boss = APIClient()
        self.boss.force_authenticate(self.master)
        # A company administrator holding EVERY sales capability — and still
        # not the master.
        self.manager, _ = _p2d_member(
            self.company, 'notas_ctl',
            ['company.view', 'sales.orders.view', 'sales.orders.manage', 'sales.notes.manage'])
        self.desk = APIClient()
        self.desk.force_authenticate(self.manager)
        self.q = f'company={self.company.pk}'
        self.cable = _prod(self.company, 'Cable USB-C', 'cable-ctl', price='49.00', inventory=0)
        _m7_stock(self.branch_a, self.cable, 20)
        _m7_stock(self.branch_b, self.cable, 20)

    def paid_order(self, *, branch=None, company=None, product=None, quantity=2, actor=None):
        company = company or self.company
        branch = branch or self.branch_a
        product = product or self.cable
        order = Order.objects.create(
            company=company, fulfillment_branch=branch, customer_name='Rosa Quispe',
            customer_email='rosa@example.com', total=product.price * quantity,
            status=Order.Status.PAID, paid=True, paid_at=timezone.now())
        OrderItem.objects.create(order=order, product=product, quantity=quantity, price=product.price)
        inventory.record_sale_stock_movements(order)
        order.refresh_from_db()
        return order

    def note(self, order=None, actor=None, **kwargs):
        order = order or self.paid_order(**kwargs)
        note, _ = get_or_create_sales_note(order, actor=actor or self.manager)
        return note

    def post_void(self, note, reason=REASON, reason_type='prueba', client=None, q=None):
        return (client or self.boss).post(
            f'/api/admin/sales-notes/{note.pk}/void/?{q or self.q}',
            {'reason': reason, 'reason_type': reason_type}, format='json')

    def post_remark(self, note, text='Revisado con contabilidad', client=None, q=None):
        return (client or self.boss).post(
            f'/api/admin/sales-notes/{note.pk}/remarks/?{q or self.q}', {'text': text}, format='json')

    def listing(self, client=None, q=None, **params):
        query = '&'.join([q or self.q, *(f'{k}={v}' for k, v in params.items())])
        return (client or self.boss).get(f'/api/admin/sales-notes/?{query}')


# -- who may void ----------------------------------------------------------------------

class WhoMayVoidTest(_Base):
    def test_the_master_voids_a_note_with_a_reason_and_it_is_audited(self):
        note = self.note()
        before = SalesNote.objects.values(
            'number', 'sequence_id', 'sequence_value', 'issued_at', 'created_by_id', 'order_id').get(pk=note.pk)

        response = self.post_void(note, reason=f'  {REASON}  ')

        self.assertEqual(response.status_code, 200, response.content)
        note.refresh_from_db()
        self.assertEqual(note.status, SalesNote.STATUS_VOID)
        self.assertEqual(note.void_reason_type, 'prueba')
        self.assertEqual(note.void_reason, REASON)
        self.assertEqual(note.voided_by, self.master)
        self.assertIsNotNone(note.voided_at)
        after = SalesNote.objects.values(*before).get(pk=note.pk)
        self.assertEqual(after, before)                      # number, date and issuer untouched
        body = response.json()
        self.assertEqual((body['status'], body['is_void'], body['number']), ('void', True, note.number))

        log = AdminAuditLog.objects.get(action='sales_note_voided')
        self.assertEqual((log.company, log.actor, log.target_type, log.target_id),
                         (self.company, self.master, 'sales_note', str(note.pk)))
        self.assertEqual(log.metadata['sales_note_number'], note.number)
        self.assertEqual(log.metadata['branch_id'], self.branch_a.pk)
        self.assertEqual(log.metadata['reason'], REASON)
        self.assertEqual(log.metadata['reason_type'], 'prueba')
        self.assertEqual(log.metadata['order_id'], note.order_id)

    def test_a_company_member_with_every_sales_capability_is_refused(self):
        note = self.note()
        response = self.post_void(note, client=self.desk)
        self.assertEqual(response.status_code, 403, response.content)
        note.refresh_from_db()
        self.assertEqual(note.status, SalesNote.STATUS_ISSUED)
        self.assertFalse(AdminAuditLog.objects.filter(action='sales_note_voided').exists())

    def test_a_legacy_company_admin_is_refused_too(self):
        admin = User.objects.create_user(username='admin_ctl', password='Pass123!')
        Membership.objects.create(user=admin, company=self.company, role='admin')
        client = APIClient()
        client.force_authenticate(admin)
        note = self.note()
        self.assertEqual(self.post_void(note, client=client).status_code, 403)
        note.refresh_from_db()
        self.assertEqual(note.status, SalesNote.STATUS_ISSUED)

    def test_anonymous_is_401(self):
        note = self.note()
        self.assertEqual(self.post_void(note, client=APIClient()).status_code, 401)

    def test_the_master_acting_in_another_company_does_not_find_the_note(self):
        note = self.note()
        response = self.post_void(note, q=f'company={self.other.pk}')
        self.assertEqual(response.status_code, 404, response.content)
        note.refresh_from_db()
        self.assertEqual(note.status, SalesNote.STATUS_ISSUED)

    def test_the_master_must_name_the_company(self):
        note = self.note()
        response = self.boss.post(
            f'/api/admin/sales-notes/{note.pk}/void/', {'reason': REASON, 'reason_type': 'prueba'}, format='json')
        self.assertIn(response.status_code, (400, 403), response.content)
        note.refresh_from_db()
        self.assertEqual(note.status, SalesNote.STATUS_ISSUED)

    def test_the_master_without_a_company_is_not_authorised(self):
        self.assertTrue(control.may_void(self.master, self.company))
        self.assertFalse(control.may_void(self.master, None))
        self.assertFalse(control.may_void(self.manager, self.company))

    def test_a_member_of_another_company_never_reaches_the_note(self):
        stranger, _ = _p2d_member(self.other, 'ajeno_ctl', ['company.view', 'sales.notes.manage'])
        client = APIClient()
        client.force_authenticate(stranger)
        note = self.note()
        self.assertIn(self.post_void(note, client=client).status_code, (403, 404))
        self.assertIn(
            self.post_void(note, client=client, q=f'company={self.other.pk}').status_code, (403, 404))
        self.assertIn(self.post_remark(note, client=client, q=f'company={self.other.pk}').status_code, (403, 404))
        note.refresh_from_db()
        self.assertEqual(note.status, SalesNote.STATUS_ISSUED)


class VoidRulesTest(_Base):
    def test_the_reason_is_mandatory_and_bounded(self):
        note = self.note()
        for reason in ('', '    ', 'abc', 'x' * 501):
            with self.subTest(reason=reason[:10]):
                response = self.post_void(note, reason=reason)
                self.assertEqual(response.status_code, 400, response.content)
        note.refresh_from_db()
        self.assertEqual(note.status, SalesNote.STATUS_ISSUED)
        self.assertEqual(self.post_void(note, reason='x' * 500).status_code, 200)

    def test_the_reason_type_is_one_of_the_three(self):
        note = self.note()
        for reason_type in ('', 'pedido_cancelado', 'borrar', None):
            with self.subTest(reason_type=reason_type):
                self.assertEqual(self.post_void(note, reason_type=reason_type).status_code, 400)
        for reason_type in ('prueba', 'error', 'otro'):
            self.assertIn(reason_type, control.MANUAL_REASON_TYPES)

    def test_a_void_note_cannot_be_voided_again(self):
        note = self.note()
        self.assertEqual(self.post_void(note).status_code, 200)
        note.refresh_from_db()
        first = note.voided_at

        response = self.post_void(note, reason='Segundo intento de anular', reason_type='error')

        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(response.json()['code'], 'already_void')
        note.refresh_from_db()
        self.assertEqual((note.voided_at, note.void_reason_type), (first, 'prueba'))
        self.assertEqual(AdminAuditLog.objects.filter(action='sales_note_voided').count(), 1)

    def test_the_note_of_a_cancelled_order_is_already_void(self):
        order = self.paid_order()
        note = self.note(order)
        cancelling.cancel_paid_order(order, self.manager, 'El cliente desistió de la compra')
        self.assertEqual(self.post_void(note).status_code, 409)


class OrderCancellationFillsTheSameFieldsTest(_Base):
    def test_cancelling_the_order_voids_its_note_with_the_same_record(self):
        order = self.paid_order()
        note = self.note(order)

        cancelling.cancel_paid_order(order, self.manager, 'El cliente desistió de la compra')

        note.refresh_from_db()
        self.assertEqual(note.status, SalesNote.STATUS_VOID)
        self.assertEqual(note.void_reason_type, control.REASON_ORDER_CANCELLED)
        self.assertEqual(note.void_reason, 'El cliente desistió de la compra')
        self.assertEqual(note.voided_by, self.manager)
        self.assertIsNotNone(note.voided_at)
        self.assertTrue(note.metadata['voided_by_order_cancellation'])     # #119's record is kept


# -- what voiding does NOT touch -----------------------------------------------------------

class VoidTouchesNothingElseTest(_Base):
    def fiscal_document(self, order):
        series = FiscalSeries.objects.create(
            company=self.company, document_type=FiscalDocumentType.RECEIPT, series='B001')
        return FiscalDocument.objects.create(
            order=order, company=self.company, series_ref=series, document_type=FiscalDocumentType.RECEIPT,
            series='B001', number=12, issued_at=timezone.now(), environment=FiscalEnvironment.BETA,
            issuer_tax_id='20800000001', issuer_legal_name='Inventario S.A.C.', customer_doc_type='1',
            customer_doc_number='40404040', customer_legal_name='ROSA QUISPE', currency='PEN',
            taxable_amount=Decimal('83.05'), tax_amount=Decimal('14.95'), total=Decimal('98.00'),
            tax_rate=Decimal('0.18'), status='accepted', signed_xml='<Invoice/>')

    def test_order_payments_stock_fiscal_documents_and_sequence_stay_as_they_were(self):
        order = self.paid_order()
        note = self.note(order)
        PaymentTransaction.objects.create(
            order=order, provider='izipay', transaction_id='CTL0001', order_number='CTL0001',
            amount=order.total, currency='PEN', status=PaymentTransaction.Status.AUTHORIZED, environment='test')
        document = self.fiscal_document(order)
        snapshot = {
            'order': Order.objects.values('status', 'paid', 'paid_at', 'total', 'fulfillment_status').get(pk=order.pk),
            'payments': list(PaymentTransaction.objects.filter(order=order).values()),
            'fiscal': list(FiscalDocument.objects.filter(order=order).values()),
            'movements': list(StockMovement.objects.order_by('pk').values_list('pk', flat=True)),
            'stock': list(BranchStock.objects.order_by('pk').values_list('pk', 'quantity')),
            'sequence': InternalSequence.objects.values('next_value', 'prefix', 'padding').get(pk=note.sequence_id),
        }

        self.assertEqual(self.post_void(note).status_code, 200)

        self.assertEqual(snapshot, {
            'order': Order.objects.values('status', 'paid', 'paid_at', 'total', 'fulfillment_status').get(pk=order.pk),
            'payments': list(PaymentTransaction.objects.filter(order=order).values()),
            'fiscal': list(FiscalDocument.objects.filter(order=order).values()),
            'movements': list(StockMovement.objects.order_by('pk').values_list('pk', flat=True)),
            'stock': list(BranchStock.objects.order_by('pk').values_list('pk', 'quantity')),
            'sequence': InternalSequence.objects.values('next_value', 'prefix', 'padding').get(pk=note.sequence_id),
        })
        document.refresh_from_db()
        self.assertEqual(document.status, 'accepted')

    def test_the_number_is_never_reused(self):
        first = self.note()
        self.post_void(first)

        second = self.note()

        self.assertEqual(second.sequence_id, first.sequence_id)
        self.assertEqual(second.sequence_value, first.sequence_value + 1)
        self.assertNotEqual(second.number, first.number)

    def test_proximo_numero_stays_locked_after_every_note_is_voided(self):
        note = self.note()
        self.post_void(note)
        sequence = InternalSequence.objects.get(pk=note.sequence_id)

        response = self.boss.patch(
            f'/api/admin/sequences/{sequence.pk}/?{self.q}', {'next_value': 1}, format='json')

        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn('next_value', response.json())
        sequence.refresh_from_db()
        self.assertEqual(sequence.next_value, note.sequence_value + 1)

    def test_the_issued_note_cannot_be_rewritten_through_the_api(self):
        note = self.note()
        url = f'/api/admin/sales-notes/{note.pk}/?{self.q}'
        for method in ('put', 'patch', 'delete'):
            with self.subTest(method):
                response = getattr(self.boss, method)(url, {'number': 'NV-9', 'status': 'issued'}, format='json')
                self.assertEqual(response.status_code, 405, response.content)
        note.refresh_from_db()
        self.assertEqual(note.status, SalesNote.STATUS_ISSUED)

    def test_the_dashboard_keeps_the_money_collected(self):
        note = self.note()
        url = f'/api/admin/sales/dashboard/?{self.q}'
        before = self.boss.get(url).json()

        self.post_void(note)

        after = self.boss.get(url).json()
        self.assertEqual(after['kpis'], before['kpis'])
        self.assertEqual(after['channels'], before['channels'])
        self.assertEqual(Decimal(after['kpis']['today']['revenue']), Decimal('98.00'))


class VoidPapersTest(_Base):
    def test_every_paper_of_a_voided_note_says_anulada(self):
        from store.printing import escpos
        from store.ticket_services import generate_sales_note_ticket_pdf

        note = self.note()
        self.assertNotIn('ANULADA', text_of(generate_sales_note_pdf(note)))
        self.post_void(note)
        note.refresh_from_db()

        self.assertIn('ANULADA', text_of(generate_sales_note_pdf(note)))
        self.assertIn('ANULADA', text_of(generate_sales_note_ticket_pdf(note)))
        self.assertIn(b'ANULADA', escpos.sales_note_ticket(note))
        self.assertIn(note.number, text_of(generate_sales_note_pdf(note)))

    def test_a_voided_note_is_not_reprinted_on_the_thermal_roll(self):
        from store.models import Printer
        from store.printing import services as printing

        note = self.note()
        printer = Printer.objects.create(
            company=self.company, branch=self.branch_a, name='Caja 1', is_active=True)
        self.post_void(note)

        with self.assertRaises(printing.PrintError):
            printing.enqueue_manual(order=note.order, printer=printer, key='k-ctl-1', actor=self.master)


# -- internal remarks ------------------------------------------------------------------------

class RemarksTest(_Base):
    def test_the_master_and_the_note_manager_add_remarks_kept_in_order(self):
        note = self.note()
        printed_before = text_of(generate_sales_note_pdf(note))

        first = self.post_remark(note, text='  Nota de prueba del arranque  ')
        second = self.post_remark(note, text='Confirmado con el propietario', client=self.desk)

        self.assertEqual((first.status_code, second.status_code), (201, 201), (first.content, second.content))
        detail = self.boss.get(f'/api/admin/sales-notes/{note.pk}/?{self.q}').json()
        self.assertEqual(
            [(r['text'], r['author']) for r in detail['remarks']],
            [('Nota de prueba del arranque', 'master_ctl'), ('Confirmado con el propietario', 'notas_ctl')])
        self.assertEqual(AdminAuditLog.objects.filter(action='sales_note_remark_added', company=self.company).count(), 2)
        note.refresh_from_db()
        self.assertEqual(note.status, SalesNote.STATUS_ISSUED)
        # Never printed: the paper says what it said.
        self.assertEqual(text_of(generate_sales_note_pdf(note)), printed_before)

    def test_a_remark_needs_text_and_the_capability(self):
        note = self.note()
        self.assertEqual(self.post_remark(note, text='  ').status_code, 400)
        self.assertEqual(self.post_remark(note, text='x' * 1001).status_code, 400)
        viewer, _ = _p2d_member(self.company, 'solo_pedidos_ctl', ['company.view', 'sales.orders.view'])
        client = APIClient()
        client.force_authenticate(viewer)
        self.assertEqual(self.post_remark(note, client=client).status_code, 403)
        self.assertFalse(SalesNoteRemark.objects.exists())

    def test_a_remark_is_append_only(self):
        note = self.note()
        self.post_remark(note)
        remark = SalesNoteRemark.objects.get()

        remark.text = 'Texto reescrito'
        with self.assertRaises(ValueError):
            remark.save()
        with self.assertRaises(ValueError):
            remark.delete()
        remark.refresh_from_db()
        self.assertEqual(remark.text, 'Revisado con contabilidad')

    def test_a_member_of_one_branch_cannot_remark_a_note_of_another(self):
        seller, _ = _p2d_member(
            self.company, 'sucursal_ctl', ['company.view', 'sales.notes.manage'], branches=[self.branch_a])
        client = APIClient()
        client.force_authenticate(seller)
        note = self.note(branch=self.branch_b)
        self.assertEqual(self.post_remark(note, client=client).status_code, 404)


# -- the list ------------------------------------------------------------------------------

class ListTest(_Base):
    def numbers(self, response):
        self.assertEqual(response.status_code, 200, response.content)
        return sorted(row['number'] for row in response.json()['results'])

    def test_status_filters_vigentes_anuladas_y_prueba(self):
        live = self.note()
        test_note = self.note()
        wrong = self.note()
        self.post_void(test_note, reason_type='prueba')
        self.post_void(wrong, reason_type='error', reason='Monto mal digitado en caja')

        self.assertEqual(self.numbers(self.listing(status='vigente')), [live.number])
        self.assertEqual(self.numbers(self.listing(status='anulada')), sorted([test_note.number, wrong.number]))
        self.assertEqual(self.numbers(self.listing(status='prueba')), [test_note.number])
        self.assertEqual(len(self.numbers(self.listing())), 3)
        self.assertEqual(self.listing(status='borrada').status_code, 400)

    def test_rows_carry_the_void_record_and_the_mark(self):
        note = self.note()
        self.post_void(note)
        row = self.listing().json()['results'][0]
        self.assertEqual(row['status_label'], 'Anulada')
        self.assertTrue(row['is_void'])
        self.assertEqual(row['void_reason'], REASON)
        self.assertEqual(row['void_reason_type'], 'prueba')
        self.assertEqual(row['voided_by_username'], 'master_ctl')
        self.assertEqual(row['branch_name'], self.branch_a.name)
        self.assertEqual(Decimal(row['order_total']), Decimal('98.00'))

    def test_date_branch_and_issuer_filters(self):
        mine = self.note(branch=self.branch_a, actor=self.manager)
        theirs = self.note(branch=self.branch_b, actor=self.master)
        old = self.note(branch=self.branch_a)
        SalesNote.objects.filter(pk=old.pk).update(issued_at=timezone.now() - timezone.timedelta(days=40))
        today = timezone.localdate().isoformat()

        self.assertEqual(self.numbers(self.listing(branch=self.branch_b.pk)), [theirs.number])
        self.assertEqual(self.numbers(self.listing(user=self.master.pk)), [theirs.number])
        self.assertEqual(self.numbers(self.listing(date_from=today)), sorted([mine.number, theirs.number]))
        self.assertEqual(self.numbers(self.listing(date_to=(timezone.localdate() - timezone.timedelta(days=1)).isoformat())),
                         [old.number])
        self.assertEqual(self.listing(date_from='ayer').status_code, 400)

    def test_tenant_and_branch_isolation(self):
        ours = self.note(branch=self.branch_a)
        north = self.note(branch=self.branch_b)
        foreign_branch_product = _prod(self.other, 'Cable ajeno', 'cable-ajeno-ctl', price='10.00', inventory=0)
        _m7_stock(self.foreign_branch, foreign_branch_product, 10)
        foreign = self.note(company=self.other, branch=self.foreign_branch, product=foreign_branch_product)

        self.assertNotIn(foreign.number + str(foreign.pk), [r['number'] + str(r['id']) for r in self.listing().json()['results']])
        self.assertEqual([r['id'] for r in self.listing(q=f'company={self.other.pk}').json()['results']], [foreign.pk])

        seller, _ = _p2d_member(
            self.company, 'sucursal_lista_ctl', ['company.view', 'sales.notes.manage'], branches=[self.branch_a])
        client = APIClient()
        client.force_authenticate(seller)
        self.assertEqual([r['id'] for r in self.listing(client=client).json()['results']], [ours.pk])
        # A branch outside their reach is refused, not silently emptied.
        self.assertEqual(self.listing(client=client, branch=self.branch_b.pk).status_code, 403)
        self.assertEqual(self.listing(client=client, branch=self.foreign_branch.pk).status_code, 403)
        self.assertEqual(
            client.get(f'/api/admin/sales-notes/{north.pk}/?{self.q}').status_code, 404)

    def test_who_may_void_is_said_by_the_server(self):
        self.note()
        self.assertTrue(self.listing().json()['can_void'])
        self.assertFalse(self.listing(client=self.desk).json()['can_void'])
        self.assertTrue(self.listing(client=self.desk).json()['can_remark'])

    def test_listing_needs_the_note_capability(self):
        viewer, _ = _p2d_member(self.company, 'solo_ver_ctl', ['company.view', 'sales.orders.view'])
        client = APIClient()
        client.force_authenticate(viewer)
        self.assertEqual(self.listing(client=client).status_code, 403)

    def test_existing_notes_are_never_marked_as_tests_automatically(self):
        self.note()
        self.note()
        self.assertEqual(self.listing(status='prueba').json()['count'], 0)
        self.assertEqual(self.listing(status='vigente').json()['count'], 2)


class OrderSalesNoteEndpointTest(_Base):
    def test_the_order_view_of_the_note_carries_the_void_record(self):
        order = self.paid_order()
        note = self.note(order)
        self.post_void(note)
        body = self.desk.get(f'/api/admin/orders/{order.pk}/sales-note/?{self.q}').json()
        self.assertEqual((body['status'], body['is_void'], body['void_reason']), ('void', True, REASON))
        self.assertEqual(body['void_reason_type_label'], 'Prueba')


# -- concurrency ---------------------------------------------------------------------------

class VoidAtTheSameInstantTest(TransactionTestCase):
    """Two masters, one note. The locks decide."""

    def setUp(self):
        self.company = Company.objects.create(
            name='Concurrente', legal_name='Concurrente SAC', tax_id='20911111113', slug='void-conc')
        self.branch = self.company.branches.first() or Branch.objects.create(company=self.company, name='Centro')
        self.manager, _ = _p2d_member(
            self.company, 'anula_conc', ['company.view', 'sales.orders.view', 'sales.orders.manage',
                                         'sales.notes.manage'])
        self.master = _master('master_conc')
        cable = Product.objects.create(company=self.company, name='Cable', slug='cable-void-conc', price=Decimal('10.00'))
        BranchStock.objects.update_or_create(branch=self.branch, product=cable, defaults={'quantity': 10})
        self.order = Order.objects.create(
            company=self.company, fulfillment_branch=self.branch, customer_email='x@example.com',
            total=Decimal('30.00'), status=Order.Status.PAID, paid=True, paid_at=timezone.now())
        OrderItem.objects.create(order=self.order, product=cable, quantity=3, price=Decimal('10.00'))
        inventory.record_sale_stock_movements(self.order)
        self.note, _ = get_or_create_sales_note(self.order, actor=self.manager)

    def race(self, *steps):
        barrier, outcomes = Barrier(len(steps)), {}

        def run(name, step):
            try:
                barrier.wait(timeout=5)
                outcomes[name] = step()
            except Exception as exc:  # noqa: BLE001 - the outcome IS the exception
                outcomes[name] = exc
            finally:
                connection.close()

        threads = [Thread(target=run, args=(name, step)) for name, step in steps]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
        return outcomes

    def void(self):
        return control.void_sales_note(
            SalesNote.objects.get(pk=self.note.pk), self.master, 'prueba', REASON)

    def test_two_voids_at_once_void_once(self):
        outcomes = self.race(('a', self.void), ('b', self.void))

        won = [o for o in outcomes.values() if isinstance(o, SalesNote)]
        lost = [o for o in outcomes.values() if isinstance(o, control.SalesNoteControlError)]
        self.assertEqual((len(won), len(lost)), (1, 1), outcomes)
        self.assertEqual(lost[0].code, 'already_void')
        self.assertEqual(AdminAuditLog.objects.filter(action='sales_note_voided').count(), 1)

    def test_a_void_and_an_order_cancellation_at_once_end_void_once(self):
        def cancel():
            return cancelling.cancel_paid_order(
                Order.objects.get(pk=self.order.pk), self.manager, 'El cliente desistió de la compra')

        outcomes = self.race(('void', self.void), ('cancel', cancel))

        self.assertFalse(any(isinstance(o, Exception) and not isinstance(o, control.SalesNoteControlError)
                             for o in outcomes.values()), outcomes)
        note = SalesNote.objects.get(pk=self.note.pk)
        self.assertEqual(note.status, SalesNote.STATUS_VOID)
        self.assertEqual(Order.objects.get(pk=self.order.pk).status, Order.Status.CANCELLED)


class DjangoAdminDoesNotBypassTheRecordTest(TestCase):
    """The Django admin is not a second, unaudited way to void or renumber a note."""

    def test_status_numbering_and_the_void_record_are_read_only_there(self):
        from django.contrib import admin as django_admin

        from store.models import SalesNoteRemark as Remark

        note_admin = django_admin.site._registry[SalesNote]
        for field in ('status', 'number', 'sequence', 'sequence_value', 'issued_at', 'created_by',
                      'voided_at', 'voided_by', 'void_reason_type', 'void_reason', 'metadata'):
            self.assertIn(field, note_admin.readonly_fields, field)
        self.assertNotIn(Remark, django_admin.site._registry)
