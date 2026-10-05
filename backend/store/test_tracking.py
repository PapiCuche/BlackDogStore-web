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

    def test_a_link_has_exactly_one_spelling(self):
        """
        32 bytes en base64 dejan dos bits sin usar en el último carácter, así
        que otros tres caracteres decodifican a los mismos bytes. Sólo vale el
        que se emitió: un enlace «parecido» no abre nada.
        """
        alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_'
        last = alphabet.index(self.token[-1])
        twins = [alphabet[(last & ~3) | low] for low in range(4) if (last & ~3) | low != last]
        self.assertEqual(len(twins), 3)
        for twin in twins:
            with self.subTest(twin):
                self.assertEqual(self.anon.get(track(self.token[:-1] + twin)).status_code, 404)
        self.assertEqual(self.anon.get(track(self.token)).status_code, 200)

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
    """
    QUIEN TIENE EL ENLACE PUEDE RESPONDER LA COTIZACIÓN COMO EL CLIENTE.

    Por eso verlo no es de quien puede abrir la orden: es de quien ya puede
    anotar la decisión del cliente (`service.quotes.record_decision`). Los
    demás saben que existe y cuántas veces se abrió, y nada más.
    """

    def setUp(self):
        super().setUp()
        from store.models import MembershipRoleAssignment
        MembershipRoleAssignment.objects.filter(membership=self.membership).delete()
        self.client = self.with_capabilities(
            'service.orders.view', 'service.orders.manage', 'service.quotes.record_decision',
            slug='entrega-enlace')

    def url(self, tail=''):
        return _m8_url('m8-taller', f'orders/{self.order.pk}/tracking-link/{tail}')

    def narrowed(self, *capabilities, slug):
        from store.models import MembershipRoleAssignment
        MembershipRoleAssignment.objects.filter(membership=self.membership).delete()
        return self.with_capabilities(*capabilities, slug=slug)

    def test_the_status_never_carries_the_link(self):
        res = self.client.get(self.url())

        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(sorted(res.json()), ['active', 'can_reveal', 'last_viewed_at', 'view_count'])
        self.assertTrue(res.json()['active'])
        self.assertTrue(res.json()['can_reveal'])
        self.assertNotIn(self.token, res.content.decode())

    def test_revealing_the_link_is_an_act_with_its_own_authority_and_its_record(self):
        res = self.client.post(self.url('reveal/'))

        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['path'], f'/seguimiento/{self.token}')
        row = AdminAuditLog.objects.filter(action='service_tracking_link_revealed').get()
        self.assertEqual(row.actor, self.staff)
        self.assertEqual(row.company, self.company)
        self.assertNotIn(self.token, str(row.metadata))

    def test_someone_who_quotes_cannot_take_the_link_and_approve_their_own_quote(self):
        """El hueco: ver la orden daba el enlace, y el enlace aprueba."""
        technician = self.narrowed(
            'service.orders.view', 'service.orders.manage', 'service.diagnostic.manage',
            slug='cotiza-sin-decidir')

        status = technician.get(self.url())
        self.assertEqual(status.status_code, 200)
        self.assertFalse(status.json()['can_reveal'])
        self.assertEqual(technician.post(self.url('reveal/')).status_code, 403)
        for tail in ('rotate/', 'revoke/'):
            self.assertNotIn('seguimiento', technician.post(self.url(tail)).content.decode())
        self.assertFalse(AdminAuditLog.objects.filter(action='service_tracking_link_revealed').exists())

    def test_rotating_and_revoking_need_authority_over_the_order(self):
        viewer = self.narrowed('service.orders.view', slug='solo-ver')

        self.assertEqual(viewer.get(self.url()).status_code, 200)
        self.assertEqual(viewer.post(self.url('rotate/')).status_code, 403)
        self.assertEqual(viewer.post(self.url('revoke/')).status_code, 403)
        self.assertEqual(tracking.token_for(self.order), self.token)

    def test_staff_rotates_and_revokes(self):
        rotated = self.client.post(self.url('rotate/'))
        self.assertEqual(rotated.status_code, 200, rotated.content)
        self.assertTrue(rotated.json()['active'])
        self.assertNotIn('path', rotated.json())
        self.assertNotEqual(self.client.post(self.url('reveal/')).json()['path'], f'/seguimiento/{self.token}')

        revoked = self.client.post(self.url('revoke/'))
        self.assertEqual(revoked.status_code, 200)
        self.assertFalse(revoked.json()['active'])
        self.assertEqual(self.client.post(self.url('reveal/')).status_code, 409)

    def test_an_order_has_its_link_from_the_moment_it_is_received(self):
        """Recepción lo copia nada más crear la orden: no hay un paso de «generar»."""
        fresh = self.make_order()
        self.assertEqual(RepairTrackingLink.objects.filter(repair_order=fresh).count(), 1)

        body = self.client.post(_m8_url('m8-taller', f'orders/{fresh.pk}/tracking-link/reveal/')).json()
        self.assertEqual(body['path'], f'/seguimiento/{tracking.token_for(fresh)}')

    def test_looking_at_the_status_writes_nothing(self):
        """Un GET no crea un enlace: ni para una orden que no tuviera ninguno."""
        RepairTrackingLink.objects.filter(repair_order=self.order).delete()

        self.assertFalse(self.client.get(self.url()).json()['active'])
        self.assertFalse(RepairTrackingLink.objects.filter(repair_order=self.order).exists())
        # Pedirlo, que es un acto, sí le da el suyo.
        self.assertTrue(self.client.post(self.url('reveal/')).json()['path'].startswith('/seguimiento/'))

    def test_orders_older_than_the_feature_got_their_link_in_the_migration(self):
        from importlib import import_module

        from django.apps import apps

        RepairTrackingLink.objects.all().delete()
        revoked = self.make_order()
        tracking.revoke(revoked, actor=self.staff)
        RepairTrackingLink.objects.filter(repair_order=self.order).delete()

        import_module('store.migrations.0109_backfill_tracking_links').backfill(apps, None)

        self.assertEqual(RepairTrackingLink.objects.filter(
            repair_order=self.order, revoked_at__isnull=True).count(), 1)
        self.assertEqual(self.anon.get(track(tracking.token_for(self.order))).status_code, 200)
        # Una orden cuyo enlace se desactivó no recibe otro.
        self.assertFalse(RepairTrackingLink.objects.filter(
            repair_order=revoked, revoked_at__isnull=True).exists())

    def test_another_branch_or_company_does_not_reach_the_link(self):
        self.order.branch = self.branch_b
        self.order.save(update_fields=['branch'])
        restricted = self.restrict_to_branch_a()
        self.assertEqual(restricted.get(self.url()).status_code, 404)
        self.assertEqual(restricted.post(self.url('reveal/')).status_code, 404)

        foreign = _m8_url('m8-otra', f'orders/{self.order.pk}/tracking-link/')
        self.assertIn(self.client.get(foreign).status_code, (403, 404))
        self.assertIn(self.client.post(foreign + 'reveal/').status_code, (403, 404))


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

    def walk_in(self, name, document='45678912'):
        customer = _v1_customer(
            self.company, None, first_name=name, last_name='SinCuenta',
            document_type='dni' if document else '', document_number=document)
        device = Device.objects.create(company=self.company, customer=customer, brand='G', model=name)
        return customer, self.make_order(customer=customer, device=device)

    def claim(self, user, order, **extra):
        return self.as_user(user).post(
            self.CLAIM, {'token': tracking.token_for(order), **extra}, format='json')

    def test_the_link_and_the_document_add_the_customer_to_the_account(self):
        walk_in, order = self.walk_in('Elena')
        user = _m7_user('elena_registrada')

        res = self.claim(user, order, document_number=' 4567-8912 ')

        self.assertEqual(res.status_code, 200, res.content)
        walk_in.refresh_from_db()
        self.assertEqual(walk_in.user, user)
        listed = self.as_user(user).get(self.LIST + self.query()).json()['results']
        self.assertEqual([row['number'] for row in listed], [order.number])
        row = AdminAuditLog.objects.filter(action='customer_account_linked').get()
        self.assertEqual(row.company, self.company)
        self.assertEqual(row.metadata['via'], 'tracking_link')

    def test_the_link_alone_does_not_hand_over_a_customer(self):
        """
        El enlace abre UNA orden y se reenvía con facilidad. Quedarse con el
        cliente entero —todas sus reparaciones, sus compras, el derecho a
        responder sus cotizaciones— pide además algo que el enlace no trae.
        """
        walk_in, order = self.walk_in('Gabi')
        stranger = _m7_user('reenviado')

        for extra in ({}, {'document_number': ''}, {'document_number': '99999999'}):
            with self.subTest(extra):
                res = self.claim(stranger, order, **extra)
                self.assertEqual(res.status_code, 403, res.content)
        walk_in.refresh_from_db()
        self.assertIsNone(walk_in.user)
        self.assertFalse(AdminAuditLog.objects.filter(action='customer_account_linked').exists())

    def test_a_customer_without_a_document_on_file_is_linked_by_the_shop_not_by_a_form(self):
        walk_in, order = self.walk_in('Hugo', document='')

        res = self.claim(_m7_user('hugo_registrado'), order, document_number='45678912')

        self.assertEqual(res.status_code, 409)
        self.assertIn('tienda', res.json()['detail'])
        walk_in.refresh_from_db()
        self.assertIsNone(walk_in.user)

    def test_guessing_the_document_runs_out_of_attempts(self):
        walk_in, order = self.walk_in('Iris')
        guesser = _m7_user('adivina')
        for guess in range(5):
            self.assertEqual(self.claim(guesser, order, document_number=f'1000000{guess}').status_code, 403)

        res = self.claim(guesser, order, document_number='45678912')

        self.assertEqual(res.status_code, 403)
        walk_in.refresh_from_db()
        self.assertIsNone(walk_in.user)

    def test_a_customer_that_already_has_an_account_is_not_taken_over(self):
        thief = _m7_user('ladron_seguimiento')
        res = self.as_user(thief).post(
            self.CLAIM, {'token': self.token, 'document_number': '40404040'}, format='json')
        self.assertEqual(res.status_code, 409)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.user, self.client_user)

    def test_one_account_is_one_customer_per_company(self):
        walk_in, order = self.walk_in('Fede')

        res = self.claim(self.client_user, order, document_number='45678912')

        self.assertEqual(res.status_code, 409)
        walk_in.refresh_from_db()
        self.assertIsNone(walk_in.user)

    def test_the_shop_can_undo_a_link(self):
        walk_in, order = self.walk_in('Juan')
        user = _m7_user('juan_registrado')
        self.claim(user, order, document_number='45678912')
        url = _m8_url('m8-taller', f'customers/{walk_in.pk}/unlink-account/')
        staff = self.with_capabilities('service.customers.manage', slug='desvincula')

        res = staff.post(url, {'reason': 'Se vinculó la cuenta equivocada.'}, format='json')

        self.assertEqual(res.status_code, 200, res.content)
        walk_in.refresh_from_db()
        self.assertIsNone(walk_in.user)
        self.assertEqual(self.as_user(user).get(self.LIST + self.query()).json()['results'], [])
        row = AdminAuditLog.objects.filter(action='customer_account_unlinked').get()
        self.assertEqual(row.metadata['reason'], 'Se vinculó la cuenta equivocada.')
        self.assertEqual(staff.post(url, {'reason': ''}, format='json').status_code, 400)
        foreign = _m8_url('m8-taller', f'customers/{self.foreign_customer.pk}/unlink-account/')
        self.assertEqual(staff.post(foreign, {'reason': 'x'}, format='json').status_code, 404)

    def test_a_bad_token_claims_nothing(self):
        user = _m7_user('sin_enlace')
        self.assertEqual(self.as_user(user).post(self.CLAIM, {'token': 'x' * 43}, format='json').status_code, 404)
        self.assertEqual(Customer.objects.filter(user=user).count(), 0)
