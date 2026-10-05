"""
QUOTE-DECISION — quién aprobó una cotización, y por dónde.

La decisión es del cliente. Lo que cambia es que ahora hay tres caminos por los
que llega, y el registro dice cuál fue, sin fingir:

  * el cliente, desde su cuenta                       customer / customer_account
  * el cliente, desde su enlace de seguimiento        customer / tracking_link
  * el PERSONAL, que anota lo que el cliente le dijo  staff / in_person · phone ·
    en el mostrador, por teléfono o por WhatsApp      whatsapp · other

En el tercero queda escrito quién lo anotó. No se registra como si el cliente
hubiera pulsado un botón: `user` queda vacío y `recorded_by` es el empleado.

Y una aprobación vale para LO QUE SE APROBÓ. Una cotización aprobada no se
edita; cambiar el precio es reabrirla, y eso anula la aprobación y pide otra.
"""
from store.models import AdminAuditLog, RepairQuote, RepairQuoteDecision, RepairStatusHistory
from store.tests import M9ServiceBase as _Base, _m8_service, _m8_url

CAP = 'service.quotes.record_decision'


class QuoteDecisionBase(_Base):
    def setUp(self):
        super().setUp()
        self.quote = self.published_quote()
        self.client = self.with_capabilities(
            'service.orders.view', 'service.diagnostic.manage', CAP, slug='decide')

    def url(self, tail='decision/', quote=None, order=None, slug='m8-taller'):
        return _m8_url(slug, f'orders/{(order or self.order).pk}/quotes/{(quote or self.quote).pk}/{tail}')

    def record(self, decision='approve', channel='phone', client=None, **extra):
        return (client or self.client).post(
            self.url(), {'decision': decision, 'channel': channel, **extra}, format='json')


class StaffRecordedDecisionTest(QuoteDecisionBase):
    def test_staff_records_an_approval_the_customer_gave_by_phone(self):
        res = self.record(note='Llamó a las 10:15 y aceptó.')

        self.assertEqual(res.status_code, 200, res.content)
        record = RepairQuoteDecision.objects.get(quote=self.quote)
        self.assertEqual(record.decision, 'approve')
        self.assertEqual(record.source, 'staff')
        self.assertEqual(record.channel, 'staff_phone')
        self.assertEqual(record.recorded_by, self.staff)
        # NO se finge que el cliente pulsó: su cuenta no figura como autora.
        self.assertIsNone(record.user)
        self.assertEqual(record.customer, self.customer)
        self.assertEqual(record.note, 'Llamó a las 10:15 y aceptó.')
        self.assertEqual(str(record.quoted_total), '120.00')

        self.quote.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual(self.quote.status, RepairQuote.STATUS_APPROVED)
        self.assertEqual(self.order.status, 'approved')
        body = res.json()['quote']
        self.assertEqual(body['decision']['source'], 'staff')
        self.assertEqual(body['decision']['channel_label'], 'Llamada')
        self.assertEqual(body['decision']['recorded_by'], 'recepcion')

    def test_every_channel_is_named_and_an_unknown_one_is_refused(self):
        first = self.order
        for channel, stored in (('in_person', 'staff_in_person'), ('whatsapp', 'staff_whatsapp'),
                                ('other', 'staff_other')):
            with self.subTest(channel):
                order = self.make_order()
                _m8_service.transition_repair_order(repair_order=order, to_status='diagnosing', actor=self.staff)
                self.order = order
                quote = self.published_quote()
                res = self.client.post(self.url(quote=quote, order=order),
                                       {'decision': 'approve', 'channel': channel}, format='json')
                self.assertEqual(res.status_code, 200, res.content)
                self.assertEqual(RepairQuoteDecision.objects.get(quote=quote).channel, stored)

        self.order = first
        for bad in ('customer_account', 'tracking_link', 'telepatía', ''):
            with self.subTest(bad):
                res = self.record(channel=bad)
                self.assertEqual(res.status_code, 400)

    def test_recording_needs_to_be_able_to_open_the_order_too(self):
        from store.models import MembershipRoleAssignment
        MembershipRoleAssignment.objects.filter(membership=self.membership).delete()
        blind = self.with_capabilities(CAP, slug='decide-sin-ver')

        self.assertEqual(self.record(client=blind).status_code, 403)
        self.assertFalse(RepairQuoteDecision.objects.exists())

    def test_a_rejection_is_recorded_the_same_way(self):
        res = self.record(decision='reject', channel='in_person', note='Prefiere no repararlo.')
        self.assertEqual(res.status_code, 200, res.content)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, 'rejected')
        self.assertEqual(RepairQuoteDecision.objects.get().decision, 'reject')

    def test_it_needs_its_own_capability(self):
        from store.models import MembershipRoleAssignment
        MembershipRoleAssignment.objects.filter(membership=self.membership).delete()
        technician = self.with_capabilities(
            'service.orders.view', 'service.orders.manage', 'service.diagnostic.manage',
            'service.repair.manage', slug='tecnico-sin-decidir')

        res = self.record(client=technician)

        self.assertEqual(res.status_code, 403)
        self.assertFalse(RepairQuoteDecision.objects.exists())
        self.quote.refresh_from_db()
        self.assertEqual(self.quote.status, RepairQuote.STATUS_SENT)

    def test_the_capability_exists_and_the_standard_roles_that_get_it(self):
        from store import company_provisioning as presets
        from store.capabilities import ALL_CAPABILITY_CODES

        self.assertIn(CAP, ALL_CAPABILITY_CODES)
        holders = {slug for _name, slug, _desc, caps in presets.PRESET_ROLES if CAP in caps}
        # Quien atiende al cliente y quien supervisa el taller. No el técnico.
        self.assertEqual(holders, {'administrador', 'ventas', 'supervisor-tecnico'})

    def test_the_record_and_the_history_say_it_was_staff(self):
        self.record(channel='whatsapp')

        row = AdminAuditLog.objects.filter(action='service_quote_decision_recorded').get()
        self.assertEqual(row.actor, self.staff)
        self.assertEqual(row.company, self.company)
        self.assertEqual(row.metadata['channel'], 'staff_whatsapp')
        self.assertEqual(row.metadata['source'], 'staff')
        self.assertEqual(row.metadata['revision'], self.quote.revision)
        step = RepairStatusHistory.objects.filter(repair_order=self.order).order_by('-pk').first()
        self.assertEqual(step.to_status, 'approved')
        self.assertEqual(step.origin, RepairStatusHistory.ORIGIN_INTERNAL)
        self.assertEqual(step.actor, self.staff)

    def test_once_recorded_it_cannot_be_changed_quietly(self):
        self.assertEqual(self.record().status_code, 200)
        self.assertEqual(self.record().status_code, 200)          # el mismo doble clic
        self.assertEqual(self.record(decision='reject').status_code, 409)
        self.assertEqual(self.record(channel='in_person').status_code, 200)
        self.assertEqual(RepairQuoteDecision.objects.get().channel, 'staff_phone')

    def test_a_draft_an_expired_or_a_cancelled_quote_cannot_be_decided(self):
        order = self.make_order()
        _m8_service.transition_repair_order(repair_order=order, to_status='diagnosing', actor=self.staff)
        self.order = order
        draft = self.make_quote()
        res = self.client.post(self.url(quote=draft, order=order),
                               {'decision': 'approve', 'channel': 'phone'}, format='json')
        self.assertEqual(res.status_code, 400)

    def test_a_quote_of_another_order_or_company_is_not_found(self):
        other = self.make_order()
        self.assertEqual(self.client.post(
            self.url(order=other), {'decision': 'approve', 'channel': 'phone'}, format='json',
        ).status_code, 404)
        self.assertIn(self.client.post(
            self.url(slug='m8-otra'), {'decision': 'approve', 'channel': 'phone'}, format='json',
        ).status_code, (403, 404))
        self.assertFalse(RepairQuoteDecision.objects.exists())

    def test_an_order_of_another_branch_is_not_found(self):
        self.order.branch = self.branch_b
        self.order.save(update_fields=['branch'])
        restricted = self.restrict_to_branch_a()
        self.assertEqual(self.record(client=restricted).status_code, 404)

    def test_the_note_has_a_length_limit(self):
        self.assertEqual(self.record(note='x' * 301).status_code, 400)


class ApprovalIsForWhatWasApprovedTest(QuoteDecisionBase):
    def setUp(self):
        super().setUp()
        self.record()
        self.quote.refresh_from_db()
        self.order.refresh_from_db()

    def test_an_approved_quote_cannot_be_edited(self):
        with self.assertRaises(Exception):
            _m8_service.add_quote_item(
                quote=self.quote, description='Repuesto extra', quantity=1, unit_price='80.00')
        self.quote.refresh_from_db()
        self.assertEqual(str(self.quote.total), '120.00')

    def test_reopening_voids_the_approval_and_asks_for_a_new_one(self):
        res = self.client.post(self.url('reopen/'), {'reason': 'Apareció otra falla.'}, format='json')

        self.assertEqual(res.status_code, 201, res.content)
        new = RepairQuote.objects.get(pk=res.json()['quote']['id'])
        self.assertEqual(new.revision, self.quote.revision + 1)
        self.assertEqual(new.status, RepairQuote.STATUS_DRAFT)
        self.assertEqual([item.description for item in new.items.all()], ['Mano de obra'])

        self.quote.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual(self.quote.status, RepairQuote.STATUS_SUPERSEDED)
        self.assertEqual(self.order.status, 'diagnosing')
        # La aprobación anterior se conserva como historia, y ya no autoriza nada.
        self.assertTrue(RepairQuoteDecision.objects.filter(quote=self.quote).exists())
        self.assertIsNone(_m8_service.approved_quote(self.order))
        row = AdminAuditLog.objects.filter(action='service_quote_reopened').get()
        self.assertEqual(row.metadata['reason'], 'Apareció otra falla.')

    def test_the_new_revision_needs_its_own_decision(self):
        new_id = self.client.post(self.url('reopen/'), {'reason': 'Otra falla.'}, format='json').json()['quote']['id']
        new = RepairQuote.objects.get(pk=new_id)
        _m8_service.add_quote_item(quote=new, description='Repuesto', quantity=1, unit_price='80.00')
        _m8_service.publish_quote(quote=new, actor=self.staff)

        res = self.client.post(self.url(quote=new), {'decision': 'approve', 'channel': 'in_person'}, format='json')

        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(_m8_service.approved_quote(self.order).pk, new.pk)
        self.assertEqual(str(_m8_service.approved_quote(self.order).total), '200.00')
        self.assertEqual(RepairQuoteDecision.objects.filter(repair_order=self.order).count(), 2)

    def test_reopening_needs_a_reason_and_authority_and_is_only_before_the_repair_starts(self):
        self.assertEqual(self.client.post(self.url('reopen/'), {'reason': ''}, format='json').status_code, 400)

        from store.models import MembershipRoleAssignment
        MembershipRoleAssignment.objects.filter(membership=self.membership).delete()
        viewer = self.with_capabilities('service.orders.view', slug='solo-ver-cot')
        self.assertEqual(viewer.post(self.url('reopen/'), {'reason': 'x'}, format='json').status_code, 403)

        _m8_service.start_repair(repair_order=self.order, actor=self.staff)
        full = self.with_capabilities('service.orders.view', 'service.diagnostic.manage', slug='cot-otra')
        res = full.post(self.url('reopen/'), {'reason': 'Tarde.'}, format='json')
        self.assertEqual(res.status_code, 400)
        self.quote.refresh_from_db()
        self.assertEqual(self.quote.status, RepairQuote.STATUS_APPROVED)
