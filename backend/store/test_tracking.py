"""
SERVICE-TRACKING — el seguimiento que el cliente abre sin que nadie le dé una cuenta.

Una orden de servicio nace con un ENLACE DE SEGUIMIENTO: una dirección que no se
puede adivinar ni enumerar, que muestra sólo lo que el cliente puede ver, y que
el taller puede cambiar o apagar. Es lo que se le entrega en el mostrador y lo
que irá dentro de cada aviso.

    /seguimiento/<token>   →   GET /api/v1/tracking/<token>/

Lo que se fija:

  * el token no es el número de la orden ni se deriva de él sin el secreto del
    servidor; uno inventado, uno alterado y uno apagado responden igual: 404;
  * por el enlace sale lo MISMO que ve un cliente con cuenta, y nada interno;
  * el IMEI y la serie salen enmascarados;
  * desde el enlace se puede responder la cotización, y queda dicho que fue por
    ahí;
  * quien tiene cuenta ve sus órdenes desde ella, y puede sumar a su cuenta la
    orden cuyo enlace tiene en la mano — que es la única prueba que se acepta.
"""
from django.core.cache import cache
from rest_framework.test import APIClient

from store import evidence_services, tracking_services as tracking
from store.models import (
    AdminAuditLog, Customer, Device, RepairEvidence, RepairQuoteDecision, RepairTrackingLink,
)
from store.test_device_identity import IMEI_A
from store.tests import (
    M9ServiceBase as _Base, _m7_login, _m7_user, _m8_service, _m8_url, _photo, _v1_customer,
)


def track(token, tail=''):
    return f'/api/v1/tracking/{token}/{tail}'


class TrackingBase(_Base):
    def setUp(self):
        super().setUp()
        cache.clear()
        Device.objects.filter(pk=self.device.pk).update(imei=IMEI_A, serial_number='F2LXK1ABC9')
        self.order.refresh_from_db()
        self.token = tracking.token_for(self.order)
        self.anon = APIClient()


class TrackingLinkTest(TrackingBase):
    def test_every_order_is_born_with_a_link_nobody_can_guess(self):
        other = self.make_order()
        first, second = tracking.token_for(self.order), tracking.token_for(other)

        self.assertNotEqual(first, second)
        self.assertGreaterEqual(len(first), 40)
        for obvious in (str(self.order.pk), self.order.number):
            self.assertNotIn(obvious, first)
        self.assertEqual(RepairTrackingLink.objects.filter(repair_order=self.order).count(), 1)
        # El mismo enlace cada vez: es lo que va en los avisos.
        self.assertEqual(tracking.token_for(self.order), first)

    def test_the_link_shows_the_order_as_the_customer_may_see_it(self):
        res = self.anon.get(track(self.token))

        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        self.assertEqual(body['order']['number'], self.order.number)
        self.assertEqual(body['order']['status'], 'diagnosing')
        self.assertTrue(body['order']['status_label'])
        self.assertEqual(body['company']['name'], 'Taller')
        self.assertEqual(
            sorted(body['company']),
            ['logo_url', 'name', 'phone', 'warranty_policy_text', 'warranty_policy_url', 'whatsapp_link'])
        self.assertEqual([step['status'] for step in body['timeline']], ['received', 'diagnosing'])
        self.assertEqual(res['Cache-Control'], 'private, max-age=0, no-store')

    def test_identifiers_are_masked_and_nothing_internal_travels(self):
        self.order.internal_notes = 'CLIENTE CONFLICTIVO'
        self.order.physical_condition = 'Rayones en la tapa'
        self.order.save(update_fields=['internal_notes', 'physical_condition'])
        Device.objects.filter(pk=self.device.pk).update(notes='NOTA INTERNA DEL EQUIPO')

        text = self.anon.get(track(self.token)).content.decode()
        body = self.anon.get(track(self.token)).json()

        self.assertEqual(body['device']['imei'], '•' * 11 + IMEI_A[-4:])
        self.assertEqual(body['device']['serial_number'], '•' * 6 + 'ABC9')
        for leaked in (IMEI_A, 'F2LXK1ABC9', 'CLIENTE CONFLICTIVO', 'NOTA INTERNA', 'Rayones',
                       'recepcion', '40404040', '999888777', 'storage_key', 'technician',
                       'branch', 'company_id', self.token):
            self.assertNotIn(leaked, text, leaked)

    def test_a_made_up_an_altered_and_a_truncated_token_are_all_not_found(self):
        tampered = self.token[:-1] + ('A' if self.token[-1] != 'A' else 'B')
        swapped = tracking.token_for(self.make_order())[:22] + self.token[22:]
        for bad in ('x' * 43, tampered, swapped, self.token[:20], str(self.order.pk), self.order.number):
            with self.subTest(bad):
                res = self.anon.get(track(bad))
                self.assertEqual(res.status_code, 404)
                self.assertEqual(res.json(), {'detail': 'No encontrado.'})

    def test_a_link_from_another_company_never_opens_this_order(self):
        foreign_order = _m8_service.create_repair_order(
            company=self.other, branch=self.foreign_branch, customer=self.foreign_customer,
            device=self.foreign_device, reported_issue='Otra cosa.', actor=self.staff,
        )
        foreign = self.anon.get(track(tracking.token_for(foreign_order))).json()
        self.assertEqual(foreign['order']['number'], foreign_order.number)
        self.assertEqual(foreign['company']['name'], 'Ajena')
        # Los números se repiten entre empresas (cada una cuenta los suyos):
        # lo que no puede cruzar es el contenido.
        self.assertNotIn('Taller', str(foreign))
        self.assertNotIn(IMEI_A[-4:], str(foreign))

    def test_revoking_turns_the_link_off_and_rotating_replaces_it(self):
        old = self.token
        new = tracking.rotate(self.order, actor=self.staff)

        self.assertNotEqual(new, old)
        self.assertEqual(self.anon.get(track(old)).status_code, 404)
        self.assertEqual(self.anon.get(track(new)).status_code, 200)

        tracking.revoke(self.order, actor=self.staff)
        self.assertEqual(self.anon.get(track(new)).status_code, 404)
        actions = set(AdminAuditLog.objects.filter(company=self.company).values_list('action', flat=True))
        self.assertTrue({'service_tracking_link_rotated', 'service_tracking_link_revoked'} <= actions)
        # Ni el registro ni la base guardan el enlace: se recalcula con el secreto.
        self.assertNotIn(new, str(list(AdminAuditLog.objects.values_list('metadata', flat=True))))
        self.assertFalse(RepairTrackingLink.objects.filter(uid=new).exists())

    def test_a_revoked_link_stays_revoked_until_somebody_decides_otherwise(self):
        """Listar las reparaciones o mandar un aviso no puede volver a abrir la orden."""
        tracking.revoke(self.order, actor=self.staff)

        self.assertIsNone(tracking.token_for(self.order))
        self.assertIsNone(tracking.path_for(self.order))
        self.assertIsNone(tracking.url_for(self.order))
        client = APIClient()
        client.force_authenticate(user=self.client_user)
        [row] = client.get(f'/api/account/repairs/?company_slug={self.company.slug}').json()['results']
        self.assertIsNone(row['tracking_path'])
        self.assertFalse(RepairTrackingLink.objects.filter(
            repair_order=self.order, revoked_at__isnull=True).exists())

    def test_opening_the_link_is_counted_without_identifying_anyone(self):
        self.anon.get(track(self.token))
        self.anon.get(track(self.token))
        link = RepairTrackingLink.objects.get(repair_order=self.order, revoked_at__isnull=True)
        self.assertEqual(link.view_count, 2)
        self.assertIsNotNone(link.last_viewed_at)


class TrackingEvidenceTest(TrackingBase):
    def setUp(self):
        super().setUp()
        self.internal = evidence_services.upload_evidence(
            repair_order=self.order, stage='diagnosis', content=_photo(900, 700),
            actor=self.staff, caption='Corrosión; no decir aún.',
        )
        self.shared = evidence_services.publish_to_customer(
            evidence=evidence_services.upload_evidence(
                repair_order=self.order, stage='intake', content=_photo(1000, 750),
                actor=self.staff, caption='Estado al recibirlo.',
            ), actor=self.staff,
        )

    def test_only_what_was_shared_is_listed_and_served(self):
        body = self.anon.get(track(self.token)).json()
        self.assertEqual([row['id'] for row in body['evidence']], [self.shared.pk])
        self.assertEqual(body['evidence'][0]['caption'], 'Estado al recibirlo.')
        self.assertNotIn('Corrosión', str(body))

        self.assertEqual(
            self.anon.get(track(self.token, f'evidence/{self.shared.pk}/content/')).status_code, 200)
        self.assertEqual(
            self.anon.get(track(self.token, f'evidence/{self.internal.pk}/content/')).status_code, 404)

    def test_the_link_of_one_order_does_not_serve_the_evidence_of_another(self):
        other = self.make_order()
        res = self.anon.get(track(tracking.token_for(other), f'evidence/{self.shared.pk}/content/'))
        self.assertEqual(res.status_code, 404)

    def test_hiding_or_voiding_takes_it_away_at_once(self):
        evidence_services.hide_from_customer(evidence=self.shared, actor=self.staff)
        self.assertEqual(self.anon.get(track(self.token)).json()['evidence'], [])
        self.assertEqual(
            self.anon.get(track(self.token, f'evidence/{self.shared.pk}/content/')).status_code, 404)
        self.assertEqual(RepairEvidence.objects.filter(pk=self.shared.pk).count(), 1)


class TrackingQuoteTest(TrackingBase):
    def setUp(self):
        super().setUp()
        self.quote = self.published_quote()

    def decide(self, decision, token=None, quote=None, **extra):
        return self.anon.post(
            track(token or self.token, f'quotes/{(quote or self.quote).pk}/decision/'),
            {'decision': decision, **extra}, format='json',
        )

    def test_the_quote_is_shown_with_its_lines_and_can_be_answered(self):
        body = self.anon.get(track(self.token)).json()
        self.assertEqual(body['quote']['status'], 'sent')
        self.assertEqual(body['quote']['total'], '120.00')
        self.assertEqual([line['description'] for line in body['quote']['items']], ['Mano de obra'])
        self.assertTrue(body['can_decide'])

    def test_a_draft_is_never_shown(self):
        other = self.make_order()
        _m8_service.transition_repair_order(repair_order=other, to_status='diagnosing', actor=self.staff)
        body = self.anon.get(track(tracking.token_for(other))).json()
        self.assertIsNone(body['quote'])
        self.assertFalse(body['can_decide'])

    def test_approving_from_the_link_settles_it_and_says_it_was_by_the_link(self):
        res = self.decide('approve')

        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['quote']['status'], 'approved')
        record = RepairQuoteDecision.objects.get(quote=self.quote)
        self.assertEqual(record.channel, 'tracking_link')
        self.assertEqual(record.source, 'customer')
        self.assertIsNone(record.user)
        self.assertIsNone(record.recorded_by)
        self.assertEqual(record.customer, self.customer)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, 'approved')
        self.assertFalse(self.anon.get(track(self.token)).json()['can_decide'])

    def test_the_same_answer_twice_is_one_and_the_opposite_is_a_conflict(self):
        self.assertEqual(self.decide('reject', reason='Muy caro').status_code, 200)
        self.assertEqual(self.decide('reject').status_code, 200)
        self.assertEqual(self.decide('approve').status_code, 409)
        self.assertEqual(RepairQuoteDecision.objects.filter(quote=self.quote).count(), 1)

    def test_a_link_cannot_answer_the_quote_of_another_order(self):
        other = self.make_order()
        res = self.decide('approve', token=tracking.token_for(other))
        self.assertEqual(res.status_code, 404)
        self.assertFalse(RepairQuoteDecision.objects.exists())

    def test_a_revoked_link_cannot_answer(self):
        tracking.revoke(self.order, actor=self.staff)
        self.assertEqual(self.decide('approve').status_code, 404)
        self.assertFalse(RepairQuoteDecision.objects.exists())


class StaffTrackingLinkTest(TrackingBase):
    def url(self, tail=''):
        return _m8_url('m8-taller', f'orders/{self.order.pk}/tracking-link/{tail}')

    def test_staff_reads_the_link_to_hand_it_over(self):
        res = self.client.get(self.url())
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['path'], f'/seguimiento/{self.token}')
        self.assertTrue(res.json()['active'])
        self.assertEqual(res.json()['view_count'], 0)

    def test_rotating_and_revoking_need_authority_over_the_order(self):
        from store.models import MembershipRoleAssignment
        MembershipRoleAssignment.objects.filter(membership=self.membership).delete()
        viewer = self.with_capabilities('service.orders.view', slug='solo-ver')

        self.assertEqual(viewer.get(self.url()).status_code, 200)
        self.assertEqual(viewer.post(self.url('rotate/')).status_code, 403)
        self.assertEqual(viewer.post(self.url('revoke/')).status_code, 403)
        self.assertEqual(tracking.token_for(self.order), self.token)

    def test_staff_rotates_and_revokes(self):
        rotated = self.client.post(self.url('rotate/'))
        self.assertEqual(rotated.status_code, 200, rotated.content)
        self.assertNotEqual(rotated.json()['path'], f'/seguimiento/{self.token}')

        revoked = self.client.post(self.url('revoke/'))
        self.assertEqual(revoked.status_code, 200)
        self.assertFalse(revoked.json()['active'])
        self.assertIsNone(revoked.json()['path'])

    def test_another_branch_or_company_does_not_reach_the_link(self):
        self.order.branch = self.branch_b
        self.order.save(update_fields=['branch'])
        restricted = self.restrict_to_branch_a()
        self.assertEqual(restricted.get(self.url()).status_code, 404)

        foreign = _m8_url('m8-otra', f'orders/{self.order.pk}/tracking-link/')
        self.assertIn(self.client.get(foreign).status_code, (403, 404))


class AccountRepairsTest(TrackingBase):
    """Quien tiene cuenta ve sus órdenes desde ella. Por sesión web, con cookies."""

    LIST = '/api/account/repairs/'
    CLAIM = '/api/account/repairs/claim/'

    def as_user(self, user):
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    def query(self):
        return f'?company_slug={self.company.slug}'

    def test_a_customer_sees_their_own_orders_with_their_links(self):
        res = self.as_user(self.client_user).get(self.LIST + self.query())

        self.assertEqual(res.status_code, 200, res.content)
        [row] = res.json()['results']
        self.assertEqual(row['number'], self.order.number)
        self.assertEqual(row['tracking_path'], f'/seguimiento/{self.token}')
        self.assertNotIn(IMEI_A, res.content.decode())

    def test_someone_else_sees_nothing_and_staff_is_not_a_customer(self):
        stranger = _m7_user('extrano_seguimiento')
        self.assertEqual(self.as_user(stranger).get(self.LIST + self.query()).json()['results'], [])
        self.assertEqual(self.as_user(self.staff).get(self.LIST + self.query()).json()['results'], [])
        self.assertIn(APIClient().get(self.LIST + self.query()).status_code, (401, 403))

    def test_an_order_of_another_customer_never_appears(self):
        other_order = self.make_order(customer=self.other_customer, device=Device.objects.create(
            company=self.company, customer=self.other_customer, brand='G', model='Z'))
        body = self.as_user(self.client_user).get(self.LIST + self.query()).content.decode()
        self.assertNotIn(other_order.number, body)

    def test_holding_the_link_lets_a_signed_in_person_add_the_order_to_their_account(self):
        walk_in = _v1_customer(self.company, None, first_name='Elena', last_name='SinCuenta')
        device = Device.objects.create(company=self.company, customer=walk_in, brand='G', model='W')
        order = self.make_order(customer=walk_in, device=device)
        user = _m7_user('elena_registrada')

        res = self.as_user(user).post(self.CLAIM, {'token': tracking.token_for(order)}, format='json')

        self.assertEqual(res.status_code, 200, res.content)
        walk_in.refresh_from_db()
        self.assertEqual(walk_in.user, user)
        listed = self.as_user(user).get(self.LIST + self.query()).json()['results']
        self.assertEqual([row['number'] for row in listed], [order.number])
        row = AdminAuditLog.objects.filter(action='customer_account_linked').get()
        self.assertEqual(row.company, self.company)
        self.assertEqual(row.metadata['via'], 'tracking_link')

    def test_a_customer_that_already_has_an_account_is_not_taken_over(self):
        thief = _m7_user('ladron_seguimiento')
        res = self.as_user(thief).post(self.CLAIM, {'token': self.token}, format='json')
        self.assertEqual(res.status_code, 409)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.user, self.client_user)

    def test_one_account_is_one_customer_per_company(self):
        walk_in = _v1_customer(self.company, None, first_name='Fede', last_name='Otro')
        device = Device.objects.create(company=self.company, customer=walk_in, brand='G', model='V')
        order = self.make_order(customer=walk_in, device=device)

        res = self.as_user(self.client_user).post(
            self.CLAIM, {'token': tracking.token_for(order)}, format='json')

        self.assertEqual(res.status_code, 409)
        walk_in.refresh_from_db()
        self.assertIsNone(walk_in.user)

    def test_a_bad_token_claims_nothing(self):
        user = _m7_user('sin_enlace')
        self.assertEqual(self.as_user(user).post(self.CLAIM, {'token': 'x' * 43}, format='json').status_code, 404)
        self.assertEqual(Customer.objects.filter(user=user).count(), 0)
