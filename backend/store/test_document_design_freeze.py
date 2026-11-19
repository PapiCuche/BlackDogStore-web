"""
DOC-DESIGN-FREEZE — a sales note keeps the design it was issued with.

Every download, e-mail copy and print job redraws the note. Its logo was
already frozen at issuance (`metadata.logo_storage_key`); its design (title,
messages, switches, logo box, block order…) was read live, so a change in
Ajustes › Documentos rewrote notes customers already held. Now the design is
frozen in `SalesNote.metadata['design']` when the note is issued and every
reprint (A4, 80 mm, ESC/POS, e-mail) reads it. A note issued before this has
no frozen design and keeps using the company's current one.
"""
import re

from django.test import TestCase

from store import document_design
from store.models import CompanyDocumentDesign, SalesNote
from store.order_document_copy import _pdf as copy_pdf
from store.printing import escpos
from store.sales_note_services import build_sales_note_context, generate_sales_note_pdf
from store.test_document_design import DocumentBase, page_texts
from store.test_document_goldens import escpos_text
from store.tests import _p3_company, _prod
from store.ticket_services import generate_sales_note_ticket_pdf

FIRST = dict(document_title='Nota comercial', thank_you_message='Gracias de la primera versión.',
             footer_text='Pie original.', show_seller=True, logo_size='small')
LATER = dict(document_title='Recibo de tienda', thank_you_message='Mensaje cambiado después.',
             footer_text='Pie nuevo.', show_seller=False, logo_size='large')


def flat(text: str) -> str:
    return re.sub(r'\s+', ' ', text).strip()


class FreezeBase(DocumentBase):
    def setUp(self):
        super().setUp()
        CompanyDocumentDesign.objects.create(company=self.company, **FIRST)

    def change_design(self, company=None, **fields):
        CompanyDocumentDesign.objects.filter(company=company or self.company).update(**fields)

    def papers(self, note):
        return {
            'A4': flat(' '.join(page_texts(generate_sales_note_pdf(note)))),
            '80 mm': flat(' '.join(page_texts(generate_sales_note_ticket_pdf(note)))),
            'ESC/POS': flat(' '.join(escpos_text(escpos.sales_note_ticket(note)))),
        }


class FrozenAtIssuanceTest(FreezeBase, TestCase):
    def test_the_note_stores_the_design_it_was_issued_with(self):
        note = self.note_for(self.sale([(self.case, 1)]))
        self.assertEqual(note.metadata['design'], document_design.design_for(self.company).as_dict())
        self.assertEqual(note.metadata['design']['thank_you_message'], FIRST['thank_you_message'])
        # The frozen logo stays where it was.
        self.assertIn('logo_storage_key', note.metadata)

    def test_a_reprint_after_the_design_changes_keeps_the_original_on_every_paper(self):
        note = self.note_for(self.sale([(self.case, 1)], seller_name_snapshot='Lucía Ramos'))
        self.change_design(**LATER)
        ctx = build_sales_note_context(note)
        self.assertEqual(ctx['design'], document_design.thaw(note.metadata['design']))
        self.assertEqual((ctx['title'], ctx['design'].logo_size, ctx['design'].show_seller),
                         ('Nota comercial', 'small', True))
        for paper, text in self.papers(note).items():
            with self.subTest(paper=paper):
                self.assertIn(FIRST['thank_you_message'], text)
                self.assertNotIn(LATER['thank_you_message'], text)
                self.assertIn('Lucía Ramos', text)            # the seller switch was on when issued
                self.assertNotIn('Recibo de tienda', text)

    def test_the_email_copy_uses_the_frozen_design(self):
        from store.order_document_copy import KIND_SALES_NOTE

        order = self.sale([(self.case, 1)])
        self.note_for(order)
        self.change_design(**LATER)
        text = flat(' '.join(page_texts(copy_pdf(KIND_SALES_NOTE, order))))
        self.assertIn(FIRST['thank_you_message'], text)
        self.assertNotIn(LATER['thank_you_message'], text)


class OldNoteTest(FreezeBase, TestCase):
    def test_a_note_issued_before_the_freeze_uses_the_current_design(self):
        note = self.note_for(self.sale([(self.case, 1)]))
        metadata = dict(note.metadata)
        metadata.pop('design')
        SalesNote.objects.filter(pk=note.pk).update(metadata=metadata)
        note.refresh_from_db()
        self.change_design(**LATER)
        self.assertEqual(build_sales_note_context(note)['design'], document_design.design_for(self.company))
        for paper, text in self.papers(note).items():
            with self.subTest(paper=paper):
                self.assertIn(LATER['thank_you_message'], text)

    def test_a_damaged_frozen_design_falls_back_to_the_current_one(self):
        note = self.note_for(self.sale([(self.case, 1)]))
        SalesNote.objects.filter(pk=note.pk).update(metadata={**note.metadata, 'design': 'roto'})
        note.refresh_from_db()
        self.change_design(**LATER)
        self.assertEqual(build_sales_note_context(note)['design'], document_design.design_for(self.company))


class ThawTest(TestCase):
    def test_a_frozen_design_round_trips(self):
        # A whole order, as a save can only store one. An order that lacks a block is completed
        # (DESIGN-VERIFY, `test_document_design_verify.ThawForwardCompatibilityTest`).
        order = ('sale', 'customer', 'notes', 'commercial', 'warranty', 'thank_you', 'post_sale')
        design = document_design.DocumentDesign(**{**FIRST, 'block_order': order})
        self.assertEqual(document_design.thaw(design.as_dict()), design)

    def test_unknown_keys_are_ignored_and_missing_ones_take_the_defaults(self):
        thawed = document_design.thaw({'thank_you_message': 'Hola', 'campo_futuro': 1})
        self.assertEqual(thawed, document_design.DocumentDesign(thank_you_message='Hola'))

    def test_a_value_of_the_wrong_type_takes_the_default(self):
        thawed = document_design.thaw({'show_seller': 'no', 'logo_a4_width_mm': True, 'footer_text': 3,
                                       'block_order': 'customer'})
        self.assertEqual(thawed, document_design.DEFAULT)

    def test_nothing_frozen_is_none(self):
        for value in (None, 'x', [], 3):
            with self.subTest(value=value):
                self.assertIsNone(document_design.thaw(value))


class TenantIsolationTest(FreezeBase, TestCase):
    def test_each_note_freezes_its_own_company_design_and_another_change_touches_nothing(self):
        note_a = self.note_for(self.sale([(self.case, 1)]))
        other = _p3_company('freeze-otra', 'Otra Tienda')
        CompanyDocumentDesign.objects.create(company=other, thank_you_message='Mensaje de la otra tienda.')
        self.assertEqual(note_a.metadata['design']['thank_you_message'], FIRST['thank_you_message'])
        self.change_design(company=other, thank_you_message='La otra tienda cambió.')
        ctx = build_sales_note_context(note_a)
        self.assertEqual(ctx['design'].thank_you_message, FIRST['thank_you_message'])
        text = self.papers(note_a)['A4']
        self.assertNotIn('otra tienda', text.lower())

    def test_the_other_company_note_freezes_the_other_design(self):
        from decimal import Decimal

        from store.models import Order, OrderItem
        from store.sales_note_services import get_or_create_sales_note

        other = _p3_company('freeze-otra-b', 'Otra Tienda B')
        CompanyDocumentDesign.objects.create(company=other, thank_you_message='Gracias de B.')
        product = _prod(other, 'Cable B', 'cable-b-freeze', price='10.00', inventory=0)
        order = Order.objects.create(company=other, total=Decimal('10.00'), status=Order.Status.PAID, paid=True,
                                     customer_name='Cliente B')
        OrderItem.objects.create(order=order, product=product, quantity=1, price=Decimal('10.00'))
        note_b, _ = get_or_create_sales_note(order)
        self.assertEqual(note_b.metadata['design']['thank_you_message'], 'Gracias de B.')
        self.assertNotEqual(note_b.metadata['design'], self.note_for(self.sale([(self.case, 1)])).metadata['design'])
