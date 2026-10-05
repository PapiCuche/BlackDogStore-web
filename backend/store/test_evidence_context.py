"""
SERVICE-EVIDENCE-CONTEXT — lo que una foto del servicio dice de sí misma.

M12D dejó hecha la evidencia: privada, por etapa, interna al nacer, anulable y
nunca borrada. Faltaban dos cosas para que una galería explique un servicio de
principio a fin:

  * UNA NOTA por foto («golpe en la esquina inferior derecha al recibirlo»);
  * las etapas que el ciclo tiene y la lista no tenía: repuestos, listo para
    entrega y garantía/reingreso.

Lo que NO cambia se vuelve a fijar donde toca: la autoridad es la de la etapa,
otra empresa no encuentra nada, y el cliente sólo ve lo que se le compartió.
"""
from rest_framework.test import APIClient

from store import evidence_services as svc
from store.models import AdminAuditLog, RepairEvidence
from store.tests import (
    M12DEvidenceBase as _Base, _photo, _saas_company, _saas_user, _upload,
    provision_company_access_defaults,
)

Stage = RepairEvidence.Stage


class EvidenceContextBase(_Base):
    def _url(self, tail='', slug=None, order=None):
        return (f'/api/v1/internal/{slug or self.company.slug}/service/orders/'
                f'{order or self.order.pk}/evidence/{tail}')

    def _post(self, stage=Stage.INTAKE, content=None, **fields):
        data = {'stage': stage, 'image': _upload(content or self.photo)}
        data.update(fields)
        return self.client.post(self._url(), data, format='multipart')


class EvidenceCaptionTest(EvidenceContextBase):
    def test_a_photo_can_carry_a_note(self):
        res = self._post(caption='  Golpe en la esquina   inferior derecha.  ')

        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.json()['caption'], 'Golpe en la esquina inferior derecha.')
        self.assertEqual(
            RepairEvidence.objects.get(pk=res.json()['id']).caption,
            'Golpe en la esquina inferior derecha.',
        )

    def test_a_photo_without_a_note_is_still_a_photo(self):
        res = self._post()
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.json()['caption'], '')

    def test_a_note_has_a_length_limit(self):
        res = self._post(caption='x' * 301)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(RepairEvidence.objects.count(), 0)

    def test_the_note_can_be_corrected_and_the_correction_is_recorded(self):
        evidence = self.upload(caption='Pantaya rota')

        res = self.client.patch(
            self._url(f'{evidence.pk}/'), {'caption': 'Pantalla rota'}, format='json',
        )

        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['caption'], 'Pantalla rota')
        row = AdminAuditLog.objects.filter(action='service_evidence_caption_changed').get()
        self.assertEqual(row.company, self.company)
        self.assertEqual(row.metadata['caption'], {'old': 'Pantaya rota', 'new': 'Pantalla rota'})
        # La foto es la misma: corregir la nota no toca el archivo ni su huella.
        evidence.refresh_from_db()
        self.assertEqual(evidence.caption, 'Pantalla rota')
        self.assertIsNone(evidence.voided_at)

    def test_the_same_note_again_changes_nothing_and_records_nothing(self):
        evidence = self.upload(caption='Igual')
        res = self.client.patch(self._url(f'{evidence.pk}/'), {'caption': 'Igual'}, format='json')
        self.assertEqual(res.status_code, 200)
        self.assertFalse(AdminAuditLog.objects.filter(action='service_evidence_caption_changed').exists())

    def test_a_voided_evidence_keeps_the_note_it_had(self):
        evidence = svc.void_evidence(
            evidence=self.upload(caption='Original'), reason='Foto equivocada', actor=self.staff,
        )
        res = self.client.patch(self._url(f'{evidence.pk}/'), {'caption': 'Otra'}, format='json')
        self.assertEqual(res.status_code, 400)
        evidence.refresh_from_db()
        self.assertEqual(evidence.caption, 'Original')

    def test_correcting_the_note_needs_the_authority_of_its_stage(self):
        evidence = self.upload(stage=Stage.DELIVERY, caption='Entregado')
        self.client = self.only_capabilities(
            'service.orders.view', 'service.diagnostic.manage', slug='ctx-diag',
        )
        res = self.client.patch(self._url(f'{evidence.pk}/'), {'caption': 'Cambiada'}, format='json')
        self.assertEqual(res.status_code, 403)

    def test_another_company_cannot_correct_it(self):
        evidence = self.upload(caption='Mía')
        other = _saas_company('Ajena SA', 'ctx-ajena', tax_id='20780060099')
        provision_company_access_defaults(other)
        res = self.client.patch(
            self._url(f'{evidence.pk}/', slug=other.slug), {'caption': 'Suya'}, format='json',
        )
        self.assertEqual(res.status_code, 404)
        evidence.refresh_from_db()
        self.assertEqual(evidence.caption, 'Mía')

    def test_a_retry_with_the_same_key_returns_the_first_note(self):
        first = self.upload(caption='Primera', idempotency_key='clave-1')
        again = self.upload(caption='Segunda', idempotency_key='clave-1')
        self.assertEqual(again.pk, first.pk)
        self.assertEqual(again.caption, 'Primera')


class EvidenceStagesTest(EvidenceContextBase):
    def test_the_cycle_has_parts_ready_and_warranty(self):
        for stage in (Stage.PARTS, Stage.READY, Stage.WARRANTY):
            with self.subTest(stage):
                res = self._post(stage=stage, content=_photo(900 + len(stage), 700))
                self.assertEqual(res.status_code, 201, res.content)
                self.assertEqual(res.json()['stage'], stage)

    def test_each_new_stage_asks_for_the_authority_that_produces_it(self):
        self.assertEqual(svc.STAGE_CAPABILITY[Stage.PARTS], 'service.repair.manage')
        self.assertEqual(svc.STAGE_CAPABILITY[Stage.READY], 'service.delivery.manage')
        self.assertEqual(svc.STAGE_CAPABILITY[Stage.WARRANTY], 'service.orders.create')
        self.assertEqual(set(svc.STAGE_CAPABILITY), set(Stage.values))

    def test_a_repair_technician_may_photograph_parts_but_not_a_warranty_return(self):
        self.client = self.only_capabilities(
            'service.orders.view', 'service.repair.manage', slug='ctx-rep',
        )
        self.assertEqual(self._post(stage=Stage.PARTS).status_code, 201)
        self.assertEqual(self._post(stage=Stage.WARRANTY).status_code, 403)
        self.assertEqual(self._post(stage=Stage.READY).status_code, 403)

    def test_a_photo_does_not_move_the_order(self):
        before = self.order.status
        self._post(stage=Stage.READY)
        self._post(stage=Stage.WARRANTY, content=_photo(800, 600))
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, before)

    def test_new_evidence_never_replaces_the_original(self):
        original = self.upload(stage=Stage.INTAKE, caption='Estado al ingresar')
        self._post(stage=Stage.WARRANTY, content=_photo(800, 600), caption='Reingreso')
        original.refresh_from_db()
        self.assertIsNone(original.voided_at)
        self.assertEqual(RepairEvidence.objects.filter(repair_order=self.order).count(), 2)


class EvidenceGalleryTest(EvidenceContextBase):
    def test_the_gallery_counts_what_is_in_force_per_stage(self):
        self.upload(stage=Stage.INTAKE)
        self.upload(stage=Stage.INTAKE, content=_photo(1000, 750))
        svc.void_evidence(
            evidence=self.upload(stage=Stage.INTAKE, content=_photo(900, 700)),
            reason='Duplicada', actor=self.staff,
        )
        self.upload(stage=Stage.REPAIR_AFTER, content=_photo(800, 600))

        body = self.client.get(self._url()).json()

        self.assertEqual(body['count'], 4)
        self.assertEqual(body['stage_counts'], {'intake': 2, 'repair_after': 1})
        self.assertEqual(body['in_force'], 3)

    def test_the_stage_list_is_served_with_what_each_one_asks_for(self):
        body = self.client.get(self._url()).json()
        stages = {row['value']: row for row in body['stages']}
        self.assertEqual(list(stages), list(Stage.values))
        self.assertEqual(stages['warranty']['label'], 'Garantía / reingreso')
        self.assertEqual(stages['parts']['capability'], 'service.repair.manage')

    def test_listing_does_not_run_one_query_per_photo(self):
        for index in range(3):
            self.upload(content=_photo(700 + index * 10, 500))
        self.client.get(self._url())
        with self.assertNumQueries(self._queries(1)):
            self.client.get(self._url())

    def _queries(self, keep):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        rows = list(RepairEvidence.objects.filter(repair_order=self.order).order_by('pk'))
        hidden = rows[keep:]
        other = self._other_order()
        RepairEvidence.objects.filter(pk__in=[r.pk for r in hidden]).update(repair_order=other)
        with CaptureQueriesContext(connection) as captured:
            self.client.get(self._url())
        RepairEvidence.objects.filter(pk__in=[r.pk for r in hidden]).update(repair_order=self.order)
        return len(captured)

    def _other_order(self):
        from store import service_services
        return service_services.create_repair_order(
            company=self.company, branch=self.order.branch, customer=self.order.customer,
            device=self.order.device, reported_issue='Otra cosa.', actor=self.staff,
        )


class EvidenceCustomerNoteTest(EvidenceContextBase):
    def setUp(self):
        super().setUp()
        self.internal = self.upload(stage=Stage.DIAGNOSIS, caption='Placa con corrosión; no decir aún.')
        self.shared = svc.publish_to_customer(
            evidence=self.upload(stage=Stage.DELIVERY, content=_photo(1000, 750),
                                 caption='Equipo entregado funcionando.'),
            actor=self.staff,
        )
        self.customer_user = _saas_user('ctx_cliente')
        customer = self.order.customer
        customer.user = self.customer_user
        customer.save(update_fields=['user'])
        self.cclient = APIClient()
        self.cclient.force_authenticate(user=self.customer_user)

    def _curl(self, tail=''):
        return f'/api/v1/customer/{self.company.slug}/repairs/{self.order.pk}/evidence/{tail}'

    def test_the_customer_reads_the_note_of_what_was_shared_and_nothing_else(self):
        res = self.cclient.get(self._curl())
        self.assertEqual(res.status_code, 200)
        rows = res.json()['results']
        self.assertEqual([row['id'] for row in rows], [self.shared.pk])
        self.assertEqual(rows[0]['caption'], 'Equipo entregado funcionando.')
        self.assertEqual(
            set(rows[0]), {'id', 'stage', 'caption', 'width', 'height', 'created_at'},
        )
        self.assertNotIn('corrosión', res.content.decode('utf-8'))

    def test_hiding_takes_the_note_away_with_the_photo(self):
        svc.hide_from_customer(evidence=self.shared, actor=self.staff)
        self.assertEqual(self.cclient.get(self._curl()).json()['results'], [])


class EvidenceBranchScopeTest(EvidenceContextBase):
    """
    DÓNDE, además de QUÉ. Tener la capacidad de una etapa no da acceso a la
    orden de una sucursal a la que la persona no pertenece: ni a su galería, ni
    a una foto suya, ni a subirle o corregirle nada.

    Esto ya lo garantizaba el camino por el que se resuelve la orden; se fija
    aquí para que una galería que algún día se resuelva de otra forma no lo
    pierda sin que una prueba lo diga.
    """

    def setUp(self):
        super().setUp()
        self.evidence = self.upload(caption='De la otra sucursal')
        self.order.branch = self.branch_b
        self.order.save(update_fields=['branch'])
        self.client = self.restrict_to_branch_a()

    def test_the_gallery_of_another_branch_is_not_found(self):
        self.assertEqual(self.client.get(self._url()).status_code, 404)

    def test_a_photo_of_another_branch_is_not_served(self):
        for tail in (f'{self.evidence.pk}/', f'{self.evidence.pk}/content/'):
            with self.subTest(tail):
                self.assertEqual(self.client.get(self._url(tail)).status_code, 404)

    def test_nothing_can_be_added_or_changed_in_another_branch(self):
        self.assertEqual(self._post(content=_photo(800, 600)).status_code, 404)
        self.assertEqual(
            self.client.patch(self._url(f'{self.evidence.pk}/'), {'caption': 'x'}, format='json').status_code,
            404,
        )
        for action in ('publish-to-customer', 'hide-from-customer', 'void'):
            with self.subTest(action):
                res = self.client.post(
                    self._url(f'{self.evidence.pk}/{action}/'), {'reason': 'x'}, format='json',
                )
                self.assertEqual(res.status_code, 404)
        self.evidence.refresh_from_db()
        self.assertEqual(self.evidence.caption, 'De la otra sucursal')
        self.assertEqual(self.evidence.visibility, 'internal')
        self.assertIsNone(self.evidence.voided_at)
        self.assertEqual(RepairEvidence.objects.filter(repair_order=self.order).count(), 1)


class EvidenceThrottleTest(EvidenceContextBase):
    """
    EVIDENCE-THROTTLE. Mirar una galería no es cambiar el estado de una orden.

    Toda la superficie de evidencias —la lista, cada miniatura, cada subida—
    compartía el cupo de «cambios de estado de pedido»: 60 por minuto y por
    persona. Una galería de veinte fotos gastaba veintiuna peticiones sólo al
    abrirse; recargarla tres veces dejaba al técnico sin poder subir nada, y
    con miniaturas rotas.
    """

    def test_looking_at_a_gallery_does_not_spend_the_allowance_to_upload(self):
        evidence = self.upload()
        for _ in range(35):
            self.assertEqual(self.client.get(self._url()).status_code, 200)
            self.assertEqual(self.client.get(self._url(f'{evidence.pk}/content/')).status_code, 200)

        res = self._post(content=_photo(800, 600), caption='Después de mirar mucho')

        self.assertEqual(res.status_code, 201, res.content)

    def test_reading_and_writing_are_limited_separately_and_both_are_limited(self):
        from store import evidence_views as views

        reads = {t.scope for t in views.InternalEvidenceContentView().get_throttles()}
        list_view = views.InternalEvidenceListView()
        list_view.request = type('R', (), {'method': 'GET'})()
        listing = {t.scope for t in list_view.get_throttles()}
        list_view.request = type('R', (), {'method': 'POST'})()
        uploading = {t.scope for t in list_view.get_throttles()}

        self.assertEqual(reads, {'service_evidence_read'})
        self.assertEqual(listing, {'service_evidence_read'})
        self.assertEqual(uploading, {'service_evidence_write'})
        self.assertNotIn('admin_order_status_change', reads | uploading)
