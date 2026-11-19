"""
LABEL-04 — tickets and stickers, each to its own printer, on their own.

* A printer has a ROLE: tickets (ESC/POS) or labels (TSPL). Each branch has at most one automatic
  printer PER ROLE; a ticket never goes to the label printer, nor a batch of labels to the ticket one
  (checked when queueing, when claiming and again by the agent).
* Goods coming in make a label batch (LABEL-A); it now goes on its own to the branch's label printer,
  only the size of the ROLL LOADED, never more than `AUTO_LABEL_LIMIT` at once, and a failure to
  queue never undoes the goods coming in.
* The stickers are the panel's own design (`build_label_plans`) as TSPL: SIZE, GAP, one BITMAP and
  one PRINT per sticker — what printed, stayed in step and scanned on an HL80.
"""
from dataclasses import replace
from decimal import Decimal
from unittest import mock

from django.db import IntegrityError, transaction
from django.test import SimpleTestCase
from rest_framework.test import APIClient

from print_agent import agent as agent_module
from store import label_batches, label_printing, labels
from store.labels import tspl
from store.models import AdminAuditLog, Printer, PrintJob, ProductBarcode, StockMovement, StockUnit
from store.printing import services as printing
from store.test_label_batches import IMEI_A, IMEI_B, LabelFixture
from store.tests import _p2d_member

CLAIM = '/api/v1/print-agent/jobs/claim/'
PRINTERS = '/api/admin/printing/printers/'
EAN = '7751234000108'


def accessory_stickers(copies=1, code=EAN):
    plan = labels.plan_accessory(labels.AccessoryData(
        product_id=1, name='Funda silicona MagSafe', price=Decimal('59.90'), currency='PEN',
        codes=(labels.LabelCode(code, 'ean13', True),), copies=copies))
    assert plan.ready, plan.reasons
    return plan.stickers * copies


class TsplRenderTest(SimpleTestCase):
    def test_a_job_is_size_gap_and_one_bitmap_and_print_per_sticker(self):
        data = tspl.render(accessory_stickers(copies=3))

        self.assertTrue(data.startswith(b'SIZE 30 mm,20 mm\r\nGAP 2 mm,0 mm\r\n'))
        self.assertEqual(data.count(b'CLS\r\n'), 3)
        self.assertEqual(data.count(b'\r\nPRINT 1,1\r\n'), 3)
        # 240 x 160 dots: 30 bytes a row, 160 rows.
        self.assertEqual(data.count(b'BITMAP 0,0,30,160,0,'), 3)

    def test_copies_of_one_sticker_are_one_bitmap_printed_n_times(self):
        # Review of #367: one BITMAP per copy made a manual batch too big for the agent.
        [sticker] = accessory_stickers()
        data = tspl.render([(sticker, 3)])

        self.assertEqual(data.count(b'BITMAP 0,0,30,160,0,'), 1)
        self.assertEqual(data.count(b'\r\nPRINT 1,3\r\n'), 1)

    def test_a_job_never_passes_the_agents_payload_limit(self):
        # Distinct stickers are cut into parts of at most JOB_BYTES; a part is never empty.
        [sticker] = accessory_stickers()
        groups = [(sticker, 1)] * 7
        per_sticker = tspl.bitmap_bytes(sticker)
        parts = label_printing.split_parts(groups, budget=per_sticker * 3)
        self.assertEqual([len(part) for part in parts], [3, 3, 1])
        self.assertEqual(label_printing.split_parts([(sticker, 1)], budget=1), [[(sticker, 1)]])
        self.assertLess(label_printing.JOB_BYTES, agent_module.MAX_PAYLOAD_BYTES)

    def test_ink_is_a_zero_bit_as_tspl_prints_it(self):
        [sticker] = accessory_stickers()
        image = tspl.raster(sticker)

        self.assertEqual(image.size, (240, 160))
        self.assertEqual(image.mode, '1')
        black = sum(1 for value in image.getdata() if value == 0)
        self.assertGreater(black, 500)                       # text and bars are ink
        self.assertLess(black, 240 * 160 / 2)                # on a white label

    def test_a_job_is_one_roll_size(self):
        small = accessory_stickers()[0]
        big = mock.Mock(width_mm=50, height_mm=25, texts=[], bars=None, logo=None)
        with self.assertRaises(ValueError):
            tspl.render([small, big])
        with self.assertRaises(ValueError):
            tspl.render([])

    def test_a_long_line_shrinks_instead_of_leaving_the_label(self):
        [sticker] = accessory_stickers()
        sticker.texts[0] = replace(sticker.texts[0], text='W' * 60)
        image = tspl.raster(sticker)

        right_margin = image.crop((image.width - 8, 0, image.width, image.height))
        self.assertEqual(sum(1 for v in right_margin.getdata() if v == 0), 0)


def _logo_png(margin=200, mark=(100, 120)):
    """A logo file as they come: a small dark mark in the middle of a big light-grey square."""
    import io
    from PIL import Image, ImageDraw

    size = (mark[0] + 2 * margin, mark[1] + 2 * margin)
    image = Image.new('RGB', size, (208, 208, 208))
    ImageDraw.Draw(image).rectangle([margin, margin, margin + mark[0] - 1, margin + mark[1] - 1], fill=(0, 0, 0))
    buffer = io.BytesIO()
    image.save(buffer, 'PNG')
    return buffer.getvalue()


class StickerOnPaperTest(SimpleTestCase):
    """What the owner checked on the HL80 and asked for, pinned."""

    def plan(self, name='Cargador USB-C 20W carga rápida', logo=True):
        return labels.plan_labels(accessories=[labels.AccessoryData(
            product_id=1, name=name, variant='Blanco', price=Decimal('89.00'), currency='PEN',
            codes=(labels.LabelCode('7751234000115', 'ean13', True),))],
            logo_png=_logo_png() if logo else None)[0]

    def test_it_reads_the_right_way_up_on_the_hl80(self):
        self.assertIn(b'DIRECTION 0' + bytes([13, 10]), tspl.render(accessory_stickers()))

    def test_the_font_has_the_accents_and_the_enye(self):
        # Pillow's own font drew «á» and «Ñ» as boxes; reportlab's Vera has them.
        self.assertTrue(tspl._font(20).path.endswith('Vera.ttf'))
        self.assertTrue(tspl._font(20, bold=True).path.endswith('VeraBd.ttf'))
        for char in 'áéíóúÑñü':
            self.assertIsNotNone(tspl._font(20).getmask(char).getbbox(), char)

    def test_the_empty_margin_of_a_logo_file_is_trimmed(self):
        from store.labels.compose import Logo

        logo, warning = Logo.from_png(_logo_png())
        self.assertIsNone(warning)
        width, height = logo._image.size
        self.assertLess(width, 120)                  # the 100 x 120 mark and a small gap, not 500 x 520
        self.assertLess(height, 140)

    def test_the_logo_is_on_the_right_and_larger(self):
        self.assertEqual((labels.ACCESSORY_30X20.logo_align, labels.ACCESSORY_30X20.logo_max_w_mm), ('right', 9.0))
        self.assertEqual(labels.DEVICE_COMMERCIAL_50X25.logo_align, 'right')
        plan = self.plan()
        self.assertTrue(plan.ready, plan.reasons)
        [sticker] = plan.stickers
        self.assertGreater(sticker.logo.x_mm, sticker.width_mm / 2)
        self.assertTrue(all(run.x_mm < sticker.logo.x_mm for run in sticker.texts))

    def test_no_line_of_text_touches_the_logo(self):
        [sticker] = self.plan().stickers
        image = tspl.raster(sticker)
        logo = sticker.logo
        left = round((logo.x_mm - 0.8) * tspl.DOTS_PER_MM)
        right = round(logo.x_mm * tspl.DOTS_PER_MM) - 1
        top = round((sticker.height_mm - logo.y_mm - logo.height_mm) * tspl.DOTS_PER_MM)
        bottom = round((sticker.height_mm - logo.y_mm) * tspl.DOTS_PER_MM)
        gap = image.crop((left, top, right, bottom))
        self.assertEqual(sum(1 for value in gap.getdata() if value == 0), 0)


class AgentKindTest(SimpleTestCase):
    TICKET = b'\x1b@ticket'
    LABELS = b'SIZE 30 mm,20 mm\r\nGAP 2 mm,0 mm\r\n'

    def test_each_kind_only_to_its_role(self):
        problem = agent_module.kind_problem
        self.assertEqual(problem('label_batch', 'label', self.LABELS), '')
        self.assertEqual(problem('sales_note_ticket', 'ticket', self.TICKET), '')
        self.assertEqual(problem('sales_note_ticket', None, self.TICKET), '')     # a server before LABEL-04
        self.assertIn('etiquetas', problem('label_batch', 'ticket', self.LABELS))
        self.assertIn('etiquetas', problem('label_batch', None, self.LABELS))
        self.assertIn('etiquetas', problem('sales_note_ticket', 'label', self.TICKET))

    def test_the_bytes_must_be_of_the_kind(self):
        problem = agent_module.kind_problem
        self.assertIn('TSPL', problem('label_batch', 'label', self.TICKET))
        self.assertIn('ESC/POS', problem('fiscal_ticket', 'ticket', self.LABELS))
        self.assertIn('desconocido', problem('otro', 'label', self.LABELS))


class LabelRoutingTest(LabelFixture):
    def setUp(self):
        super().setUp()
        ProductBarcode.objects.create(company=self.company, product=self.other_product, code=EAN,
                                      symbology='ean13', is_primary=True)
        self.tickets = Printer.objects.create(
            company=self.company, branch=self.branch_a, name='Tickets', host='', connection='local_queue',
            role=Printer.Role.TICKET, auto_print=True)
        self.labels = Printer.objects.create(
            company=self.company, branch=self.branch_a, name='Etiquetas', host='', connection='local_queue',
            role=Printer.Role.LABEL, label_roll='30x20', auto_print=True)

    def goods_in(self, quantity=3):
        movement = self.manual(StockMovement.PURCHASE_ENTRY, quantity)
        return label_batches.create_for_movement(movement, actor=self.staff)

    def label_jobs(self):
        return PrintJob.objects.filter(kind=PrintJob.Kind.LABEL_BATCH)

    # -- roles -----------------------------------------------------------------

    def test_one_automatic_printer_per_role_not_per_branch(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Printer.objects.create(company=self.company, branch=self.branch_a, name='Otra de etiquetas',
                                   host='', connection='local_queue', role=Printer.Role.LABEL, auto_print=True)

    def test_tickets_never_go_to_the_label_printer(self):
        self.assertEqual(printing.auto_printer(self.branch_a), self.tickets)
        self.assertEqual(printing.auto_printer(self.branch_a, Printer.Role.LABEL), self.labels)
        Printer.objects.filter(pk=self.tickets.pk).update(auto_print=False)
        self.assertIsNone(printing.auto_printer(self.branch_a))          # never the label one instead

    # -- goods in ----------------------------------------------------------------

    def test_goods_in_print_their_labels_on_the_label_printer(self):
        batch = self.goods_in(3)

        [job] = self.label_jobs()
        self.assertEqual((job.printer, job.label_batch, job.label_size, job.order),
                         (self.labels, batch, '30x20', None))
        data = printing.render(job)
        self.assertTrue(data.startswith(b'SIZE 30 mm,20 mm'))
        self.assertEqual(data.count(b'BITMAP '), 1)
        self.assertEqual(data.count(b'\r\nPRINT 1,3\r\n'), 1)

    def test_the_same_batch_is_queued_once(self):
        batch = self.goods_in(2)
        printing.enqueue_label_batch(batch)

        self.assertEqual(self.label_jobs().count(), 1)

    def test_only_the_roll_that_is_loaded_prints_on_its_own(self):
        Printer.objects.filter(pk=self.labels.pk).update(label_roll='50x25')
        self.goods_in(2)
        self.assertEqual(self.label_jobs().count(), 0)

        Printer.objects.filter(pk=self.labels.pk).update(label_roll='')
        self.goods_in(2)
        self.assertEqual(self.label_jobs().count(), 0)

    def test_a_big_batch_waits_for_a_person(self):
        # Counted before anything is drawn (review of #367: it ran inside the goods-in transaction).
        with mock.patch.object(label_printing, 'batch_groups', side_effect=AssertionError('drawn')):
            self.goods_in(101)
        self.assertEqual(self.label_jobs().count(), 0)

    def test_without_a_label_printer_the_goods_still_come_in(self):
        Printer.objects.filter(pk=self.labels.pk).update(is_active=False)
        batch = self.goods_in(2)

        self.assertIsNotNone(batch)
        self.assertEqual(self.label_jobs().count(), 0)

    def test_a_failure_to_queue_never_undoes_the_goods_in(self):
        with mock.patch.object(printing, 'enqueue_label_batch', side_effect=RuntimeError('boom')):
            batch = self.goods_in(2)

        self.assertNotEqual(batch, label_batches.FAILED)
        self.assertEqual(batch.items.count(), 1)
        self.assertEqual(self.label_jobs().count(), 0)

    # -- the agent ---------------------------------------------------------------

    def claim(self, printer_ids, kinds=('fiscal_ticket', 'sales_note_ticket', 'label_batch')):
        _agent, token = printing.create_agent(company=self.company, branch=self.branch_a, name='PC', actor=None)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'PrintAgent {token}')
        body = {'connections': ['local_queue'], 'printers': printer_ids}
        if kinds is not None:
            body['kinds'] = list(kinds)
        return client.post(CLAIM, body, format='json')

    def test_the_agent_gets_the_tspl_and_the_role(self):
        self.goods_in(2)

        job = self.claim([self.labels.pk, self.tickets.pk]).data['job']

        self.assertEqual((job['kind'], job['printer']['id'], job['printer']['role']),
                         ('label_batch', self.labels.pk, 'label'))
        import base64
        self.assertTrue(base64.b64decode(job['payload_base64']).startswith(b'SIZE 30 mm,20 mm'))

    def test_a_label_job_on_a_ticket_printer_is_cancelled_not_printed(self):
        self.goods_in(2)
        self.label_jobs().update(printer=self.tickets)

        self.assertIsNone(self.claim([self.labels.pk, self.tickets.pk]).data['job'])
        [job] = self.label_jobs()
        self.assertEqual(job.status, PrintJob.Status.CANCELLED)

    def test_an_agent_that_does_not_say_it_prints_labels_never_gets_one(self):
        # An agent 1.3 refuses an unknown kind and the job would be lost: it is not given one.
        self.goods_in(2)

        self.assertIsNone(self.claim([self.labels.pk], kinds=None).data['job'])
        [job] = self.label_jobs()
        self.assertEqual((job.status, job.attempts), (PrintJob.Status.PENDING, 0))
        self.assertIsNotNone(self.claim([self.labels.pk]).data['job'])

    def test_a_job_for_another_roll_waits_until_that_roll_is_loaded(self):
        self.goods_in(2)
        Printer.objects.filter(pk=self.labels.pk).update(label_roll='50x25')

        self.assertIsNone(self.claim([self.labels.pk]).data['job'])
        [job] = self.label_jobs()
        self.assertEqual((job.status, job.attempts), (PrintJob.Status.PENDING, 0))
        Printer.objects.filter(pk=self.labels.pk).update(label_roll='30x20')
        self.assertIsNotNone(self.claim([self.labels.pk]).data['job'])

    def test_a_batch_with_nothing_left_to_print_is_cancelled_at_once(self):
        batch = self.goods_in(2)
        batch.items.update(needs_review=True)

        self.assertIsNone(self.claim([self.labels.pk]).data['job'])
        [job] = self.label_jobs()
        self.assertEqual(job.status, PrintJob.Status.CANCELLED)

    def test_a_part_past_the_end_is_cancelled(self):
        self.goods_in(2)
        self.label_jobs().update(label_part=1)

        self.assertIsNone(self.claim([self.labels.pk]).data['job'])
        self.assertEqual(self.label_jobs().get().status, PrintJob.Status.CANCELLED)

    # -- the panel ---------------------------------------------------------------

    def print_batch(self, batch, key='k-1', **body):
        return self.api.post(f'/api/admin/printing/label-batches/{batch.pk}/print/',
                             body, format='json', HTTP_IDEMPOTENCY_KEY=key)

    def test_a_person_sends_a_batch_after_changing_the_roll(self):
        Printer.objects.filter(pk=self.labels.pk).update(label_roll='50x25')
        batch = self.goods_in(2)

        res = self.print_batch(batch, size='30x20')

        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual((res.data['label_batch'], res.data['label_size']), (batch.pk, '30x20'))
        again = self.print_batch(batch, size='30x20')                                         # double click
        self.assertEqual((again.status_code, again.data['id']), (200, res.data['id']))
        self.assertEqual(
            AdminAuditLog.objects.filter(action='label_batch_printed', company=self.company).count(), 1)

    def test_a_manual_batch_has_a_limit(self):
        batch = self.goods_in(3)
        with mock.patch.object(label_printing, 'MANUAL_LABEL_LIMIT', 2), \
                mock.patch.object(label_printing, 'batch_groups', side_effect=AssertionError('drawn')):
            res = self.print_batch(batch, key='k-big')
        self.assertEqual(res.status_code, 400)
        self.assertIn('2', res.data['detail'])

    def test_a_big_manual_batch_is_cut_into_jobs_the_agent_takes(self):
        Printer.objects.filter(pk=self.labels.pk).update(auto_print=False)
        ProductBarcode.objects.create(company=self.company, product=self.product, code='7751234000115',
                                      symbology='ean13', is_primary=True)
        self.manual(StockMovement.PURCHASE_ENTRY, 1, product=self.product)
        batch = self.goods_in(2)
        batch.items.create(product=self.product, copies=1)
        with mock.patch.object(label_printing, 'JOB_BYTES', 5000):          # one sticker a job
            res = self.print_batch(batch, key='k-parts')
            jobs = list(self.label_jobs().order_by('label_part'))
            self.assertEqual((res.status_code, res.data.get('parts')), (201, 2), res.data)
            self.assertEqual([j.label_part for j in jobs], [0, 1])
            self.assertEqual([printing.render(j).count(b'BITMAP ') for j in jobs], [1, 1])

    def test_a_unit_that_left_the_branch_is_left_out_not_a_500(self):
        Printer.objects.filter(pk=self.labels.pk).update(label_roll='50x25', auto_print=False)
        received = self.receive(self.row('SERIE00001', IMEI_A), self.row('SERIE00002', IMEI_B))
        batch = label_batches.create_for_movement(received.movement, units=received, actor=self.staff)
        StockUnit.objects.filter(pk=received[0].pk).update(branch=self.branch_b)

        res = self.print_batch(batch, key='k-moved', size='50x25')
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(printing.render(self.label_jobs().get()).count(b'BITMAP '), 1)

    def test_a_retry_on_another_roll_is_refused(self):
        self.goods_in(2)
        job = self.label_jobs().get()
        self.label_jobs().update(status=PrintJob.Status.FAILED)
        Printer.objects.filter(pk=self.labels.pk).update(label_roll='50x25')
        operator, _ = _p2d_member(self.company, 'operador', ['company.view', 'sales.pos.use', 'sales.orders.view',
                                                            'inventory.labels.print'])
        self.api.force_authenticate(user=operator)

        res = self.api.post(f'/api/admin/printing/jobs/{job.pk}/retry/?company={self.company.pk}', {},
                            format='json')
        self.assertEqual(res.status_code, 400, res.data)
        self.assertIn('30x20', res.data['detail'])

    def test_retrying_labels_needs_the_labels_permission(self):
        self.goods_in(2)
        job = self.label_jobs().get()
        self.label_jobs().update(status=PrintJob.Status.FAILED)
        cashier, _ = _p2d_member(self.company, 'cajero', ['company.view', 'sales.pos.use', 'sales.orders.view'])
        self.api.force_authenticate(user=cashier)

        res = self.api.post(f'/api/admin/printing/jobs/{job.pk}/retry/?company={self.company.pk}', {},
                            format='json')
        self.assertEqual(res.status_code, 404)

    def test_a_size_the_batch_does_not_have_is_refused(self):
        batch = self.goods_in(2)
        self.assertEqual(self.print_batch(batch, size='50x25').status_code, 400)

    def test_without_a_label_printer_the_panel_says_so(self):
        batch = self.goods_in(2)
        Printer.objects.filter(pk=self.labels.pk).update(is_active=False)

        res = self.print_batch(batch, key='k-2')
        self.assertEqual(res.status_code, 400)
        self.assertIn('etiquetas', res.data['detail'])

    def test_a_ticket_printer_is_never_a_label_target(self):
        batch = self.goods_in(2)
        self.assertEqual(self.print_batch(batch, printer=self.tickets.pk).status_code, 400)

    def test_another_companys_batch_does_not_exist_here(self):
        movement = self.manual(StockMovement.PURCHASE_ENTRY, 2)
        batch = label_batches.create_for_movement(movement, actor=self.staff)
        batch.company = self.other
        type(batch).objects.filter(pk=batch.pk).update(company=self.other)

        self.assertEqual(self.print_batch(batch).status_code, 404)

    def test_the_intake_answer_says_the_labels_are_printing(self):
        batch = self.goods_in(2)
        self.assertEqual((label_batches.summary(batch)['printing'], label_batches.summary(batch)['printer_name']),
                         (True, 'Etiquetas'))
        self.assertTrue(label_batches.summary(batch)['label_printer'])
        Printer.objects.filter(pk=self.labels.pk).update(label_roll='50x25')
        other = self.goods_in(2)
        self.assertFalse(label_batches.summary(other)['printing'])
        # A failed job is not «printing»: the panel offers to send it again (review of #367).
        self.label_jobs().update(status=PrintJob.Status.FAILED)
        self.assertEqual((label_batches.summary(batch)['printing'], label_batches.summary(batch)['print_status']),
                         (False, 'failed'))
        Printer.objects.filter(pk=self.labels.pk).update(is_active=False)
        self.assertFalse(label_batches.summary(batch)['label_printer'])

    def test_someone_without_the_labels_permission_gets_a_404(self):
        batch = self.goods_in(2)
        outsider, _ = _p2d_member(self.company, 'sin_etiquetas', ['company.view'])
        self.api.force_authenticate(user=outsider)
        self.assertEqual(self.print_batch(batch, key='k-9').status_code, 404)

    def test_the_printer_api_sets_role_and_roll(self):
        manager, _ = _p2d_member(self.company, 'impresoras', ['company.view', 'company.manage'])
        self.api.force_authenticate(user=manager)
        url = f'{PRINTERS}?company={self.company.pk}'
        res = self.api.post(url, {'branch': self.branch_b.pk, 'name': 'Stickers', 'connection': 'local_queue',
                                  'role': 'label', 'label_roll': '50x25', 'auto_print': True}, format='json')
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual((res.data['role'], res.data['label_roll']), ('label', '50x25'))
        detail = f'{PRINTERS}{res.data["id"]}/?company={self.company.pk}'

        self.assertEqual(self.api.patch(detail, {'role': 'ticket'}, format='json').status_code, 400)
        self.assertEqual(self.api.patch(detail, {'label_roll': '30 x 20'}, format='json').status_code, 400)
        self.assertEqual(self.api.patch(detail, {'label_roll': '30x20'}, format='json').data['label_roll'], '30x20')
        ticket_detail = f'{PRINTERS}{self.tickets.pk}/?company={self.company.pk}'
        self.assertEqual(self.api.patch(ticket_detail, {'label_roll': '30x20'}, format='json').status_code, 400)
