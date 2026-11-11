"""
B0 — safe supplier-offer re-imports (docs/adr-catalog-commercial.md §7, §13).

What these tests pin (the owner's ten points):

  * every offer import carries the date of the supplier's MESSAGE: required, never
    in the future, kept on the job; a job without it is not applied;
  * the CONTENT fingerprint recognises a list already applied whatever its bytes
    (the WhatsApp draft is rebuilt every time), per company, never sent out, the
    cost only as a hash component; applying it again needs a reason and the
    whole company in scope, and is audited;
  * what was sold is never put back on sale: a repetition writes nothing; a new
    message subtracts what sold since its day began; unknown is never positive;
  * the validity is never renewed by a repetition or an older message;
  * an expired offer is reactivated only with an explicit, counted confirmation;
  * a cleared quantity on a confirmed offer is a decision;
  * `last_confirmed_at` / `last_confirmed_job` advance only on a verifiable
    confirmation, audited with the company and the job;
  * the complete-list mode keeps working with all of the above.
"""
from datetime import timedelta

from django.test import TestCase
from rest_framework.test import APIClient

from store import supplier_offer_import as offer_import
from store import supply_services as supply
from store.models import AdminAuditLog, BulkImportJob, Order, SupplierOffer
from store.test_supplier_offers import today
from store.test_supplier_weekly_import import WeeklyFile
from store.tests import _p2d_member

PREVIEW = '/api/admin/supplier-offers/import/preview/'
DRAFT = '/api/admin/inventory/text-preview/offer-draft/'
DRAFT_FILE = '/api/admin/inventory/text-preview/offer-draft/file/'
REASON = 'El proveedor reenvió la lista y lo confirmó por teléfono'
NOTE = 'Mensaje nuevo del proveedor de hoy, con las mismas cantidades'
#: B0 review (1): the same rows under a LATER message date are applied only with this explicit confirmation.
NEW_DECLARATION = {'confirm_new_declaration': True, 'new_declaration_note': NOTE}


class _Reimport(WeeklyFile, TestCase):
    slug = 'b0-tienda'

    def setUp(self):
        self.build_week(self.slug)
        self.sessions = 0

    def sell(self, quantity=1, *, paid_days_ago=0):
        """A REAL sale on order: cart, checkout and the gateway's confirmation (it commits the offer)."""
        self.sessions += 1
        session = f'b0-{self.sessions}'
        self.add_on_order(quantity=quantity, session=session)
        order = self.checkout(session)
        self.pay(order)
        if paid_days_ago:
            from django.utils import timezone
            Order.objects.filter(pk=order.pk).update(paid_at=timezone.now() - timedelta(days=paid_days_ago))
        return order

    def ok(self, response):
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def applied(self, rows, *, message_date=None, **body):
        job = self.ok(self.preview(rows, message_date=message_date))
        self.ok(self.apply(job['id'], **body))
        return job

    def member(self, name, perms, *, branches=False):
        user, _ = _p2d_member(self.company, name, perms,
                              branches=[self.company.branches.order_by('pk').first()] if branches else None)
        client = APIClient()
        client.force_authenticate(user)
        return client

    def offer_now(self):
        return SupplierOffer.objects.get(pk=self.offer.pk)


# ---------------------------------------------------------------------------
# 4 — the message date
# ---------------------------------------------------------------------------

class MessageDateTest(_Reimport):
    slug = 'b0-fecha'

    def test_the_preview_requires_the_message_date_and_stages_nothing_without_it(self):
        before = BulkImportJob.objects.count()
        response = self.client_.post(PREVIEW, {'file': self.file([self.same()])}, format='multipart')
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual((response.json()['code'], response.json()['field']), ('message_date_required', 'message_date'))
        self.assertIn('fecha del mensaje', response.json()['detail'])
        self.assertEqual(BulkImportJob.objects.count(), before)

    def test_a_message_date_in_the_future_or_unreadable_is_refused(self):
        future = self.preview([self.same()], message_date=today() + timedelta(days=1))
        self.assertEqual((future.status_code, future.json()['code']), (400, 'message_date_future'))
        response = self.client_.post(PREVIEW, {'file': self.file([self.same()]), 'message_date': 'mañana'},
                                     format='multipart')
        self.assertEqual((response.status_code, response.json()['code']), (400, 'message_date_invalid'))

    def test_the_job_keeps_the_message_date(self):
        day = today() - timedelta(days=2)
        body = self.ok(self.preview([self.same()], message_date=day))
        self.assertEqual(body['message_date'], day.isoformat())
        self.assertEqual(BulkImportJob.objects.get(pk=body['id']).message_date, day)

    def test_a_job_previewed_without_a_message_date_is_not_applied(self):
        body = self.ok(self.preview([self.same(**{'Cantidad confirmada': 9})]))
        BulkImportJob.objects.filter(pk=body['id']).update(message_date=None)      # previewed before B0
        refused = self.apply(body['id'])
        self.assertEqual((refused.status_code, refused.json()['code']), (400, 'message_date_required'))
        self.assertEqual(self.offer_now().confirmed_quantity, 5)

    def test_the_text_draft_requires_the_message_date(self):
        body = {'supplier_id': self.supplier.pk, 'valid_from': self.offer.valid_from.isoformat(),
                'valid_until': self.offer.valid_until.isoformat(), 'lead_time_days': 4,
                'rows': [{'key': '1', 'product_id': self.case.pk, 'commercial_price': '110',
                          'confirmed_quantity': 5}]}
        for url in (DRAFT, DRAFT_FILE):
            missing = self.client_.post(url, body, format='json')
            self.assertEqual(missing.status_code, 400, missing.content)
            self.assertIn('fecha del mensaje', missing.json()['detail'])
            future = self.client_.post(url, {**body, 'message_date': (today() + timedelta(days=3)).isoformat()},
                                       format='json')
            self.assertEqual(future.status_code, 400, future.content)


# ---------------------------------------------------------------------------
# 1, 2 — the content fingerprint
# ---------------------------------------------------------------------------

class ContentFingerprintTest(_Reimport):
    slug = 'b0-huella'

    def rows(self, reverse=False):
        rows = [self.same(**{'Cantidad confirmada': 4}),
                self.row(**{'Código/SKU del producto': self.cable.slug, 'Precio comercial': '39'})]
        return list(reversed(rows)) if reverse else rows

    def test_the_same_content_in_another_file_is_recognised_and_refused(self):
        first = self.applied(self.rows())
        again = self.ok(self.preview(self.rows(reverse=True)))       # other row order, other bytes
        self.assertEqual(again['already_applied_content']['job_id'], first['id'])
        self.assertEqual(again['already_applied_content']['message_date'], today().isoformat())
        self.assertEqual(again['already_applied_content']['applied_by'], self.staff.get_username())
        self.assertIn(f'n.º {first["id"]}', again['already_applied_content']['detail'])
        refused = self.apply(again['id'])
        self.assertEqual((refused.status_code, refused.json()['code']), (409, 'already_applied_content'))
        self.assertIn(self.staff.get_username(), refused.json()['detail'])
        self.assertEqual(BulkImportJob.objects.get(pk=again['id']).status, BulkImportJob.PREVIEWED)

    def test_another_message_date_is_other_content_but_warns_that_the_rows_were_applied(self):
        first = self.applied(self.rows(), message_date=today() - timedelta(days=1))
        again = self.ok(self.preview(self.rows()))
        self.assertIsNone(again['already_applied_content'])
        self.assertEqual(again['same_rows_applied']['job_id'], first['id'])
        self.assertEqual(again['same_rows_applied']['message_date'], (today() - timedelta(days=1)).isoformat())

    def test_another_companys_identical_content_never_counts(self):
        self.applied(self.rows())
        other = WeeklyFile()
        other.build_week('b0-otra-empresa')
        body = other.preview([other.same(**{'Cantidad confirmada': 4}),
                              other.row(**{'Código/SKU del producto': other.cable.slug, 'Precio comercial': '39'})]
                             ).json()
        self.assertIsNone(body['already_applied_content'])
        self.assertIsNone(body['same_rows_applied'])
        self.assertEqual(other.apply(body['id']).status_code, 200)

    def test_the_fingerprint_never_leaves_the_server(self):
        first = self.applied(self.rows())
        again = self.preview(self.rows())
        job = BulkImportJob.objects.get(pk=first['id'])
        self.assertEqual(len(job.content_fingerprint), 64)
        self.assertNotIn(job.content_fingerprint, again.content.decode())
        self.assertNotIn('content_fingerprint', again.content.decode())
        refused = self.apply(again.json()['id'])
        self.assertNotIn(job.content_fingerprint, refused.content.decode())
        for metadata in AdminAuditLog.objects.filter(company=self.company).values_list('metadata', flat=True):
            self.assertNotIn(job.content_fingerprint, str(metadata))

    def test_the_cost_is_a_hash_component_and_never_shown_without_costs_view(self):
        self.applied(self.rows())
        # The same rows with another agreed cost are other content (only who sees costs can state one).
        cost = self.ok(self.preview([self.same(**{'Cantidad confirmada': 4, 'Costo pactado': '71'}),
                                     self.row(**{'Código/SKU del producto': self.cable.slug,
                                                 'Precio comercial': '39'})]))
        self.assertIsNone(cost['already_applied_content'])
        buyer = self.member('b0-sin-costos', ['company.view', 'products.view', 'products.manage'])
        blank = [self.same(**{'Cantidad confirmada': 4, 'Costo pactado': ''})]
        body = buyer.post(PREVIEW, {'file': self.file(blank), 'message_date': today().isoformat()},
                          format='multipart')
        self.assertEqual(body.status_code, 200, body.content)
        text = body.content.decode()
        self.assertNotIn('agreed_cost', text)
        self.assertNotIn('70.00', text)
        self.assertNotIn(BulkImportJob.objects.get(pk=body.json()['id']).content_fingerprint, text)

    def test_registering_the_same_content_again_needs_a_reason_and_the_whole_company(self):
        first = self.applied(self.rows())
        again = self.ok(self.preview(self.rows()))
        no_reason = self.apply(again['id'], confirm_reapply=True)
        self.assertEqual((no_reason.status_code, no_reason.json()['code']), (400, 'reapply_reason_required'))
        limited = self.member('b0-sucursal', ['company.view', 'products.view', 'products.manage', 'costs.view'],
                              branches=True)
        forbidden = self.apply(again['id'], limited, confirm_reapply=True, reapply_reason=REASON)
        self.assertEqual((forbidden.status_code, forbidden.json()['code']), (403, 'reapply_forbidden'))
        done = self.ok(self.apply(again['id'], confirm_reapply=True, reapply_reason=REASON))
        self.assertEqual((done['repeated'], done['updated'], done['created']), (2, 0, 0))
        log = AdminAuditLog.objects.get(action='supplier_offers_reapply_confirmed')
        self.assertEqual((log.company_id, log.metadata['job_id'], log.metadata['previous_job_id'],
                          log.metadata['kind'], log.metadata['reason']),
                         (self.company.pk, again['id'], first['id'], 'content', REASON))

    def test_a_re_pasted_whatsapp_draft_is_flagged_before_and_after_the_upload(self):
        draft = {'supplier_id': self.supplier.pk, 'message_date': today().isoformat(),
                 'valid_from': self.offer.valid_from.isoformat(), 'valid_until': self.offer.valid_until.isoformat(),
                 'lead_time_days': 4, 'rows': [{'key': '1', 'product_id': self.case.pk, 'commercial_price': '110',
                                                'confirmed_quantity': 3, 'agreed_cost': '70'}]}
        from django.core.files.uploadedfile import SimpleUploadedFile

        def official(data):
            return self.ok(self.client_.post(PREVIEW, {'file': SimpleUploadedFile('borrador.xlsx', data),
                                                       'message_date': today().isoformat()}, format='multipart'))

        first_file = self.client_.post(DRAFT_FILE, draft, format='json').content
        first = official(first_file)
        self.ok(self.apply(first['id']))
        dry = self.ok(self.client_.post(DRAFT, draft, format='json'))
        self.assertEqual(dry['already_applied_content']['job_id'], first['id'])
        self.assertEqual(dry['rows'][0]['data']['declaration'], 'repetition')
        self.assertNotIn('id', dry)
        second = official(self.client_.post(DRAFT_FILE, draft, format='json').content)
        self.assertEqual(second['already_applied_content']['job_id'], first['id'])
        self.assertEqual(self.apply(second['id']).json()['code'], 'already_applied_content')


# ---------------------------------------------------------------------------
# 3 — never restore what was sold; unknown is never positive
# ---------------------------------------------------------------------------

class NeverRestoreTest(_Reimport):
    slug = 'b0-ventas'

    def test_a_repetition_after_real_sales_never_puts_them_back_on_sale(self):
        first = self.applied([self.same(**{'Cantidad confirmada': 4})])
        self.sell(1)
        offer = self.offer_now()
        self.assertEqual((offer.confirmed_quantity, offer.committed_quantity, offer.remaining), (4, 1, 3))
        self.assertEqual((offer.last_declared_quantity, offer.committed_at_declaration), (4, 0))
        again = self.ok(self.preview([self.same(**{'Cantidad confirmada': 4})]))
        row = again['rows'][0]
        self.assertEqual((row['action'], row['data']['declaration'], row['data']['available']),
                         ('no_change', 'repetition', 3), row)
        self.assertTrue(any('sigue descontado' in w for w in row['warnings']), row)
        self.assertEqual(again['already_applied_content']['job_id'], first['id'])
        self.ok(self.apply(again['id'], confirm_reapply=True, reapply_reason=REASON))
        offer = self.offer_now()
        # declared − (committed now − committed at the declaration) = 4 − (1 − 0) = 3.
        self.assertEqual((offer.confirmed_quantity, offer.committed_quantity, offer.remaining), (4, 1, 3))

    def test_a_newer_message_subtracts_what_sold_since_its_day_began(self):
        self.applied([self.same(**{'Cantidad confirmada': 4})], message_date=today() - timedelta(days=2))
        self.sell(1)                                                    # paid today, after the new message's cut-off
        body = self.ok(self.preview([self.same(**{'Cantidad confirmada': 4})]))
        row = body['rows'][0]
        self.assertEqual((row['data']['declaration'], row['data']['sold_since_message'], row['data']['available']),
                         ('new', 1, 3), row)
        self.ok(self.apply(body['id'], **NEW_DECLARATION))
        offer = self.offer_now()
        self.assertEqual((offer.confirmed_quantity, offer.committed_quantity, offer.remaining), (4, 1, 3))
        self.assertEqual((offer.last_declared_quantity, offer.declared_message_date, offer.committed_at_declaration),
                         (4, today(), 0))

    def test_sales_before_the_message_day_are_already_in_its_quantity(self):
        # Owner D1: what sold BEFORE the message is reflected in what the supplier says is left — but the SAME rows
        # under a later date put that sale back on sale, so (B0 review 1) only with the explicit confirmation.
        self.applied([self.same(**{'Cantidad confirmada': 4})], message_date=today() - timedelta(days=3))
        self.sell(1, paid_days_ago=2)
        body = self.ok(self.preview([self.same(**{'Cantidad confirmada': 4})]))
        self.assertEqual(body['rows'][0]['data']['would_restore'], 1)
        refused = self.apply(body['id'])
        self.assertEqual((refused.status_code, refused.json()['code']), (409, 'same_rows_new_date'))
        self.ok(self.apply(body['id'], **NEW_DECLARATION))
        offer = self.offer_now()
        self.assertEqual((offer.confirmed_quantity, offer.committed_quantity, offer.remaining), (5, 1, 4))
        self.assertEqual(offer.committed_at_declaration, 1)

    def test_more_sold_since_the_message_than_it_declared_leaves_nothing(self):
        self.applied([self.same(**{'Cantidad confirmada': 4})], message_date=today() - timedelta(days=1))
        self.sell(2)
        self.applied([self.same(**{'Cantidad confirmada': 1})])
        offer = self.offer_now()
        self.assertEqual((offer.confirmed_quantity, offer.committed_quantity, offer.remaining), (2, 2, 0))
        self.assertIsNone(supply.best_offer(self.case, 1))

    def test_a_new_offer_takes_off_what_sold_since_the_message_against_the_same_supplier(self):
        self.sell(1)                                                    # against today's offer
        start, end = self.offer.valid_until + timedelta(days=1), self.offer.valid_until + timedelta(days=7)
        body = self.ok(self.preview([self.same(**{'Cantidad confirmada': 5, 'Vigente desde': start.isoformat(),
                                                  'Vigente hasta': end.isoformat()})]))
        self.assertEqual((body['rows'][0]['action'], body['rows'][0]['data']['available']), ('create', 4))
        self.ok(self.apply(body['id']))
        created = SupplierOffer.objects.get(supplier=self.supplier, product=self.case, valid_from=start)
        self.assertEqual((created.confirmed_quantity, created.last_declared_quantity), (4, 5))

    def test_an_unknown_quantity_never_becomes_a_positive_one(self):
        self.sell(1)
        body = self.ok(self.preview([self.row(**{'Código/SKU del producto': self.cable.slug,
                                                'Cantidad confirmada': ''})]))
        self.assertIsNone(body['rows'][0]['data']['available'])
        self.ok(self.apply(body['id']))
        created = SupplierOffer.objects.get(product=self.cable)
        self.assertEqual((created.confirmed_quantity, created.availability_confirmed, created.remaining_known),
                         (None, False, None))
        self.assertEqual((created.last_declared_quantity, created.declared_message_date), (None, today()))
        again = self.ok(self.preview([self.row(**{'Código/SKU del producto': self.cable.slug,
                                                 'Cantidad confirmada': ''})]))
        self.assertEqual(again['rows'][0]['data']['declaration'], 'repetition')
        self.assertIsNone(supply.best_offer(self.cable, 1))


class QuantityClearedTest(_Reimport):
    slug = 'b0-vacia'

    def test_a_cleared_quantity_on_a_confirmed_offer_is_a_decision(self):
        body = self.ok(self.preview([self.same(**{'Cantidad confirmada': ''})]))
        row = body['rows'][0]
        self.assertTrue(row['data']['quantity_cleared'], row)
        self.assertEqual(body['summary']['quantity_cleared'], 1)
        refused = self.apply(body['id'])
        self.assertEqual((refused.status_code, refused.json()['code']), (409, 'quantity_decision_required'))
        self.assertEqual([r['offer_id'] for r in refused.json()['quantity_cleared']], [self.offer.pk])
        self.assertEqual(self.offer_now().confirmed_quantity, 5)
        bad = self.apply(body['id'], quantity_decisions={str(self.offer.pk): 'borrar'})
        self.assertEqual(bad.status_code, 400)
        self.ok(self.apply(body['id'], quantity_decisions={str(self.offer.pk): 'keep'}))
        offer = self.offer_now()
        self.assertEqual((offer.confirmed_quantity, offer.availability_confirmed, offer.remaining), (5, True, 5))
        self.assertTrue(AdminAuditLog.objects.filter(action='supplier_offer_quantity_kept', company=self.company,
                                                     metadata__job_id=body['id']).exists())
        # B0 review (4): keeping is the person's decision, not the supplier's confirmation: nothing is declared.
        self.assertEqual((offer.last_confirmed_at, offer.declared_message_date, offer.last_declared_quantity),
                         (None, None, None))

    def test_each_cleared_row_takes_its_own_decision(self):
        other = self.make_offer(product=self.cable, confirmed_quantity=3)
        body = self.ok(self.preview([self.same(**{'Cantidad confirmada': ''}),
                                     self.row(**{'Código/SKU del producto': self.cable.slug, 'Cantidad confirmada': '',
                                                 'Precio comercial': '110', 'Costo pactado': '70', 'Plazo (días)': 4,
                                                 'Vigente desde': other.valid_from.isoformat(),
                                                 'Vigente hasta': other.valid_until.isoformat()})]))
        self.assertEqual(body['summary']['quantity_cleared'], 2)
        partial = self.apply(body['id'], quantity_decisions={str(self.offer.pk): 'keep'})
        self.assertEqual((partial.status_code, partial.json()['code']), (409, 'quantity_decision_required'))
        self.assertEqual([r['offer_id'] for r in partial.json()['quantity_cleared']], [other.pk])
        self.ok(self.apply(body['id'], quantity_decisions={str(self.offer.pk): 'keep', str(other.pk): 'clear'}))
        kept, cleared = self.offer_now(), SupplierOffer.objects.get(pk=other.pk)
        self.assertEqual((kept.confirmed_quantity, kept.last_confirmed_at), (5, None))
        self.assertEqual((cleared.confirmed_quantity, cleared.availability_confirmed), (None, False))
        self.assertEqual((cleared.declared_message_date, cleared.last_declared_quantity), (today(), None))
        self.assertIsNotNone(cleared.last_confirmed_at)

    def test_clearing_it_on_purpose_stops_selling(self):
        body = self.ok(self.preview([self.same(**{'Cantidad confirmada': ''})]))
        self.ok(self.apply(body['id'], quantity_decisions={str(self.offer.pk): 'clear'}))
        offer = self.offer_now()
        self.assertEqual((offer.confirmed_quantity, offer.availability_confirmed), (None, False))
        self.assertIsNone(supply.best_offer(self.case, 1))


# ---------------------------------------------------------------------------
# 5, 6 — validity and expired offers
# ---------------------------------------------------------------------------

class ValidityTest(_Reimport):
    slug = 'b0-vigencia'

    def wider(self, **change):
        return self.same(**{'Vigente desde': today().isoformat(),
                            'Vigente hasta': (today() + timedelta(days=13)).isoformat(), **change})

    def test_a_repetition_with_new_days_never_renews_the_validity(self):
        self.applied([self.same()])
        window = (self.offer.valid_from, self.offer.valid_until)
        body = self.ok(self.preview([self.wider()]))     # the screen recomputed the days: the content is the same
        self.assertEqual(body['rows'][0]['data']['declaration'], 'repetition')
        self.ok(self.apply(body['id']))
        offer = self.offer_now()
        self.assertEqual((offer.valid_from, offer.valid_until), window)

    def test_an_older_message_changes_nothing(self):
        self.applied([self.same()])
        body = self.ok(self.preview([self.wider(**{'Cantidad confirmada': 9, 'Precio comercial': '99'})],
                                    message_date=today() - timedelta(days=1)))
        row = body['rows'][0]
        self.assertEqual((row['action'], row['data']['declaration']), ('no_change', 'older'), row)
        self.assertTrue(any('anterior a la declaración' in w for w in row['warnings']), row)
        result = self.ok(self.apply(body['id']))
        self.assertEqual(result['older'], 1)
        offer = self.offer_now()
        self.assertEqual((offer.confirmed_quantity, str(offer.commercial_price), offer.valid_until),
                         (5, '110.00', self.offer.valid_until))

    def test_a_correction_of_the_same_day_changes_the_quantity_but_never_the_validity(self):
        self.applied([self.same()])
        body = self.ok(self.preview([self.wider(**{'Cantidad confirmada': 2})]))
        row = body['rows'][0]
        self.assertEqual((row['action'], row['data']['declaration']), ('update', 'new'), row)
        self.assertNotIn('valid_until', row['data']['changes'])
        self.assertTrue(any('La vigencia no cambia' in w for w in row['warnings']), row)
        self.ok(self.apply(body['id']))
        offer = self.offer_now()
        self.assertEqual((offer.confirmed_quantity, offer.valid_until), (2, self.offer.valid_until))

    def test_a_later_message_moves_the_validity(self):
        self.applied([self.same()], message_date=today() - timedelta(days=1))
        self.applied([self.wider(**{'Cantidad confirmada': 5})], **NEW_DECLARATION)
        self.assertEqual(self.offer_now().valid_until, today() + timedelta(days=13))

    def test_an_expired_offer_is_not_revived_by_a_repetition(self):
        old_start, old_end = today() - timedelta(days=10), today() - timedelta(days=4)
        SupplierOffer.objects.filter(pk=self.offer.pk).update(valid_from=old_start, valid_until=old_end)
        rows = [self.same(**{'Vigente desde': old_start.isoformat(), 'Vigente hasta': old_end.isoformat()})]
        self.applied(rows, message_date=old_start)
        self.assertEqual(supply.expire_offers(company=self.company), 1)
        body = self.ok(self.preview([self.same(**{'Vigente desde': old_start.isoformat(),
                                                  'Vigente hasta': (today() + timedelta(days=3)).isoformat()})],
                                    message_date=old_start))
        row = body['rows'][0]
        self.assertEqual(row['data']['declaration'], 'repetition', row)
        self.assertNotIn('reactivation_requires_review', row['data'])
        self.assertTrue(any('no la reactiva' in w for w in row['warnings']), row)
        self.ok(self.apply(body['id']))
        self.assertFalse(self.offer_now().is_active)


# ---------------------------------------------------------------------------
# 8 — the last verifiable confirmation
# ---------------------------------------------------------------------------

class ConfirmationTest(_Reimport):
    slug = 'b0-confirmada'

    def confirmations(self, job_id):
        return list(AdminAuditLog.objects.filter(action='supplier_offer_confirmed', company=self.company,
                                                 metadata__job_id=job_id).values_list('metadata', flat=True))

    def test_a_new_declaration_confirms_at_the_message_day_with_its_job_and_an_audit(self):
        day = today() - timedelta(days=2)
        body = self.applied([self.same(**{'Cantidad confirmada': 3}),
                             self.row(**{'Código/SKU del producto': self.cable.slug})], message_date=day)
        offer = self.offer_now()
        self.assertEqual((offer.last_confirmed_at, offer.last_confirmed_job_id),
                         (offer_import.message_cutoff(day), body['id']))
        self.assertEqual(sorted(m['kind'] for m in self.confirmations(body['id'])), ['created', 'updated'])

    def test_a_new_declaration_that_changes_nothing_is_still_a_confirmation(self):
        body = self.applied([self.same()])
        self.assertEqual(body['rows'][0]['action'], 'no_change')
        self.assertEqual([m['kind'] for m in self.confirmations(body['id'])], ['no_change'])
        self.assertEqual(self.offer_now().last_confirmed_job_id, body['id'])

    def test_an_identical_re_paste_never_moves_the_confirmation(self):
        first = self.applied([self.same()], message_date=today() - timedelta(days=3))
        stamp = self.offer_now().last_confirmed_at
        again = self.ok(self.preview([self.same()], message_date=today() - timedelta(days=3)))
        self.ok(self.apply(again['id'], confirm_reapply=True, reapply_reason=REASON))
        offer = self.offer_now()
        self.assertEqual((offer.last_confirmed_at, offer.last_confirmed_job_id), (stamp, first['id']))
        self.assertEqual(self.confirmations(again['id']), [])

    def test_a_later_message_moves_it(self):
        self.applied([self.same()], message_date=today() - timedelta(days=3))
        later = self.applied([self.same()], message_date=today() - timedelta(days=1), **NEW_DECLARATION)
        offer = self.offer_now()
        self.assertEqual((offer.last_confirmed_at, offer.last_confirmed_job_id),
                         (offer_import.message_cutoff(today() - timedelta(days=1)), later['id']))

    def test_an_operators_explicit_confirmation_in_the_panel_is_one(self):
        url = f'/api/admin/supplier-offers/{self.offer.pk}/'
        self.assertEqual(self.client_.patch(url, {'commercial_price': '111'}, format='json').status_code, 200)
        self.assertIsNone(self.offer_now().last_confirmed_at)
        response = self.client_.patch(url, {'confirmed_quantity': 5, 'availability_confirmed': True}, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertIsNotNone(response.json()['last_confirmed_at'])
        log = AdminAuditLog.objects.get(action='supplier_offer_confirmed', target_id=str(self.offer.pk))
        self.assertEqual((log.company_id, log.metadata['source'], log.metadata['job_id']),
                         (self.company.pk, 'operator', None))


# ---------------------------------------------------------------------------
# The complete-list mode (INV-SNAPSHOT-01) with B0
# ---------------------------------------------------------------------------

class SnapshotInterplayTest(_Reimport):
    slug = 'b0-completa'

    def setUp(self):
        super().setUp()
        self.absent = self.make_offer(product=self.cable)

    def complete(self, rows):
        return self.ok(self.client_.post(PREVIEW, {'file': self.file(rows), 'complete_snapshot': 'true',
                                                   'message_date': today().isoformat(),
                                                   'supplier_id': str(self.supplier.pk)}, format='multipart'))

    def test_a_complete_list_repeated_changes_nothing_and_needs_its_review(self):
        first = self.complete([self.same(**{'Cantidad confirmada': 4})])
        self.ok(self.apply(first['id'], all_messages_included=True, confirm_deactivations=1))
        self.assertFalse(SupplierOffer.objects.get(pk=self.absent.pk).is_active)
        self.sell(1)
        again = self.complete([self.same(**{'Cantidad confirmada': 4})])
        self.assertEqual(again['already_applied_content']['job_id'], first['id'])
        self.assertEqual((again['snapshot']['deactivate_count'], again['rows'][0]['data']['declaration']),
                         (0, 'repetition'))
        refused = self.apply(again['id'], all_messages_included=True, confirm_deactivations=0)
        self.assertEqual(refused.json()['code'], 'already_applied_content')
        self.ok(self.apply(again['id'], all_messages_included=True, confirm_deactivations=0, confirm_reapply=True,
                           reapply_reason=REASON))
        self.assertEqual(self.offer_now().remaining, 3)

    def test_the_same_rows_as_a_partial_list_are_other_content_as_a_complete_one(self):
        partial = self.applied([self.same(**{'Cantidad confirmada': 4})])
        complete = self.complete([self.same(**{'Cantidad confirmada': 4})])
        self.assertIsNone(complete['already_applied_content'])
        self.assertEqual(complete['same_rows_applied'], None)      # the mode is part of the rows' identity too
        self.assertEqual(complete['rows'][0]['data']['declaration'], 'repetition')
        self.assertNotEqual(BulkImportJob.objects.get(pk=partial['id']).content_fingerprint,
                            BulkImportJob.objects.get(pk=complete['id']).content_fingerprint)


# ---------------------------------------------------------------------------
# B0 review (1) — the same rows under a later message date
# ---------------------------------------------------------------------------

class SameRowsNewDateTest(_Reimport):
    slug = 'b0-redatada'

    def setUp(self):
        super().setUp()
        self.first = self.applied([self.same(**{'Cantidad confirmada': 3})], message_date=today() - timedelta(days=3))
        self.sell(2, paid_days_ago=1)                       # before today's cut-off: not subtracted by the date
        self.assertEqual(self.offer_now().remaining, 1)

    def test_the_same_rows_re_dated_are_refused_saying_what_would_be_sold_again(self):
        body = self.ok(self.preview([self.same(**{'Cantidad confirmada': 3})]))
        self.assertEqual(body['same_rows_applied']['job_id'], self.first['id'])
        self.assertEqual(body['rows'][0]['data']['would_restore'], 2)
        self.assertEqual(body['same_rows_applied']['restores'],
                         [{'row_number': 2, 'offer_id': self.offer.pk, 'product_name': self.case.name, 'units': 2}])
        refused = self.apply(body['id'])
        self.assertEqual((refused.status_code, refused.json()['code']), (409, 'same_rows_new_date'))
        self.assertEqual(refused.json()['same_rows_applied']['restores'][0]['units'], 2)
        self.assertIn('2', refused.json()['detail'])
        self.assertNotIn('corrige la fecha', refused.json()['detail'].lower())
        self.assertEqual(self.offer_now().remaining, 1)
        no_note = self.apply(body['id'], confirm_new_declaration=True)
        self.assertEqual((no_note.status_code, no_note.json()['code']), (400, 'new_declaration_note_required'))
        self.assertEqual(self.offer_now().remaining, 1)

    def test_a_branch_limited_buyer_may_confirm_it_and_it_is_audited(self):
        limited = self.member('b0-semanal', ['company.view', 'products.view', 'products.manage', 'costs.view'],
                              branches=True)
        body = limited.post(PREVIEW, {'file': self.file([self.same(**{'Cantidad confirmada': 3})]),
                                      'message_date': today().isoformat()}, format='multipart').json()
        done = self.apply(body['id'], limited, **NEW_DECLARATION)
        self.assertEqual(done.status_code, 200, done.content)
        self.assertEqual(self.offer_now().remaining, 3)
        log = AdminAuditLog.objects.get(action='supplier_offers_new_declaration_confirmed')
        self.assertEqual((log.company_id, log.metadata['job_id'], log.metadata['previous_job_id'], log.metadata['note'],
                          log.metadata['restores'][0]['units'], log.actor.get_username()),
                         (self.company.pk, body['id'], self.first['id'], NOTE, 2, 'b0-semanal'))

    def test_other_rows_under_a_later_date_need_no_confirmation(self):
        body = self.ok(self.preview([self.same(**{'Cantidad confirmada': 4})]))
        self.assertIsNone(body['same_rows_applied'])
        self.ok(self.apply(body['id']))
        self.assertEqual(self.offer_now().remaining, 4)

    def test_the_explanation_never_advises_changing_the_message_date(self):
        again = self.ok(self.preview([self.same(**{'Cantidad confirmada': 3})], message_date=today() - timedelta(days=3)))
        self.assertNotIn('corrige la fecha', again['already_applied_content']['detail'].lower())
        refused = self.apply(again['id'])
        self.assertNotIn('corrige la fecha', refused.json()['detail'].lower())


# ---------------------------------------------------------------------------
# B0 review (2) — one declaration per supplier and variant, whatever the window
# ---------------------------------------------------------------------------

class PairDeclarationTest(_Reimport):
    slug = 'b0-par'

    def next_window(self, **change):
        start = self.offer.valid_until + timedelta(days=1)
        return self.same(**{'Vigente desde': start.isoformat(),
                            'Vigente hasta': (start + timedelta(days=6)).isoformat(), **change})

    def test_the_same_message_with_another_window_never_opens_a_second_offer(self):
        self.applied([self.same(**{'Cantidad confirmada': 3})])
        body = self.ok(self.preview([self.next_window(**{'Cantidad confirmada': 3})]))
        row = body['rows'][0]
        self.assertEqual((row['action'], row['data']['declaration'], row['data']['offer_id']),
                         ('no_change', 'repetition', self.offer.pk), row)
        self.ok(self.apply(body['id']))
        self.assertEqual(SupplierOffer.objects.filter(supplier=self.supplier, product=self.case).count(), 1)
        self.assertEqual(sum(o.remaining for o in supply.valid_offers(self.case)), 3)

    def test_an_older_message_for_another_window_changes_nothing(self):
        self.applied([self.same(**{'Cantidad confirmada': 3})])
        body = self.ok(self.preview([self.next_window(**{'Cantidad confirmada': 9})],
                                    message_date=today() - timedelta(days=2)))
        self.assertEqual((body['rows'][0]['action'], body['rows'][0]['data']['declaration']), ('no_change', 'older'))
        self.ok(self.apply(body['id']))
        self.assertEqual(SupplierOffer.objects.filter(supplier=self.supplier, product=self.case).count(), 1)

    def test_the_same_day_with_other_content_for_another_window_is_for_review(self):
        self.applied([self.same(**{'Cantidad confirmada': 3})])
        body = self.ok(self.preview([self.next_window(**{'Cantidad confirmada': 7})]))
        row = body['rows'][0]
        self.assertEqual(row['action'], 'error', row)
        self.assertIn('ya está registrado', ' '.join(row['errors']))
        self.assertEqual(self.apply(body['id']).status_code, 400)

    def test_a_declaration_registered_after_the_preview_makes_it_stale(self):
        other_window = self.ok(self.preview([self.next_window(**{'Cantidad confirmada': 3})]))
        self.assertEqual(other_window['rows'][0]['action'], 'create')
        self.applied([self.same(**{'Cantidad confirmada': 3})])        # the same message, the running window
        stale = self.apply(other_window['id'])
        self.assertEqual((stale.status_code, stale.json()['code']), (400, 'stale'), stale.content)
        self.assertEqual(SupplierOffer.objects.filter(supplier=self.supplier, product=self.case).count(), 1)

    def test_a_later_message_for_next_week_still_opens_next_weeks_offer(self):
        self.applied([self.same(**{'Cantidad confirmada': 3})], message_date=today() - timedelta(days=1))
        body = self.ok(self.preview([self.next_window(**{'Cantidad confirmada': 6})]))
        self.assertEqual((body['rows'][0]['action'], body['rows'][0]['data']['declaration']), ('create', 'new'))
        self.ok(self.apply(body['id']))
        self.assertEqual(SupplierOffer.objects.filter(supplier=self.supplier, product=self.case).count(), 2)


class CutoffTimezoneTest(_Reimport):
    slug = 'b0-zona'

    def test_the_message_day_starts_at_midnight_of_the_shops_timezone(self):
        from django.utils import timezone

        cutoff = offer_import.message_cutoff(today())
        local = timezone.localtime(cutoff)
        self.assertEqual((local.date(), local.hour, local.minute), (today(), 0, 0))
        self.assertEqual(str(local.tzinfo), str(timezone.get_current_timezone()))
