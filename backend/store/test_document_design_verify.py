"""
DESIGN-VERIFY — an audit of every field of the per-company document design, end to end.

On top of `test_document_design_settings` (DOC-DESIGN-03), `test_document_design_v2`
(DOC-DESIGN-04) and `test_document_design_freeze` (DOC-DESIGN-FREEZE), what is pinned here:

  * EVERY field round-trips: a PATCH of a non-default value of each one is what
    the next GET answers, what the row holds and what a new note freezes;
  * PREVIEW FIDELITY: the sample drawn from an UNSAVED draft takes from the
    company and the design exactly what a real note issued after saving that
    draft takes: the same design, title, warranty, accent, identity and logo,
    and the same keys. With the sale's own data put in, the A4, the 80 mm
    ticket and the ESC/POS bytes are the same as the real note's;
  * FORWARD COMPATIBILITY of a frozen design: a value outside a closed set (an
    option that no longer exists) takes the default, and an order of blocks
    that lacks one (a block added later, or a damaged order) keeps it where the
    default order has it — a reprint never loses its warranty to a setting;
  * no shop's name, city or brand colour is written in the code that draws or
    configures the papers, and no migration seeds a design.

Concurrency: `test_document_design_concurrency`. The company data the papers
print: `test_company_printed_data`.
"""
from dataclasses import replace
from pathlib import Path

from store import document_design, document_layout, document_preview
from store.document_design import BLOCKS, DEFAULT, FIELDS
from store.models import Company, CompanyDocumentDesign, CompanySettings
from store.printing import escpos
from store.sales_note_services import build_sales_note_context, sales_note_document
from store.test_document_design import page_streams, page_texts
from store.test_document_design_settings import EVERYTHING_CHANGED, URL, DesignBase
from store.test_document_design_v2 import EVERYTHING_V2
from store.ticket_services import ticket_pdf_from_ctx

#: A non-default value for every field of the design.
EVERY_FIELD = {**EVERYTHING_CHANGED, **EVERYTHING_V2}


class EveryFieldTest(DesignBase):
    def test_the_fixture_changes_every_field(self):
        self.assertEqual(set(EVERY_FIELD), set(FIELDS))
        for field, value in EVERY_FIELD.items():
            with self.subTest(field=field):
                expected = list(getattr(DEFAULT, field)) if field == 'block_order' else getattr(DEFAULT, field)
                self.assertNotEqual(value, expected)

    def test_every_field_round_trips_one_by_one(self):
        for field, value in EVERY_FIELD.items():
            with self.subTest(field=field):
                CompanyDocumentDesign.objects.filter(company=self.company).delete()
                response = self.patch(**{field: value})
                self.assertEqual(response.status_code, 200, response.content[:300])
                self.assertEqual(response.json()['design'][field], value)
                read = self.api.get(URL).json()['design']
                self.assertEqual(read[field], value)
                # Every other field is still its default.
                self.assertEqual({key: read[key] for key in FIELDS if key != field},
                                 {key: DEFAULT.as_dict()[key] for key in FIELDS if key != field})
                self.assertEqual(document_design.design_for(self.company).as_dict()[field], value)

    def test_every_field_round_trips_at_once_and_a_new_note_freezes_all_of_them(self):
        self.assertEqual(self.patch(**EVERY_FIELD).status_code, 200)
        self.assertEqual(self.api.get(URL).json()['design'], {**DEFAULT.as_dict(), **EVERY_FIELD})
        note = self.note()
        self.assertEqual(note.metadata['design'], {**DEFAULT.as_dict(), **EVERY_FIELD})
        self.assertEqual(set(note.metadata['design']), set(FIELDS))


# ---------------------------------------------------------------------------
# Preview fidelity: an unsaved draft against the real note issued after saving it
# ---------------------------------------------------------------------------

#: What a paper takes from the company and its design, never from the sale.
DESIGN_KEYS = ('design', 'title', 'warranty_note', 'accent_color', 'logo_png', 'store_name', 'store_legal_name',
               'store_ruc', 'store_address', 'store_city', 'store_phone', 'store_email', 'store_website')


class PreviewFidelityTest(DesignBase):
    def setUp(self):
        super().setUp()
        self.with_logo()
        CompanySettings.objects.filter(company=self.company).update(
            accent_color='#1d4ed8', warranty_policy_url='https://tienda.example/garantia')

    def contexts(self, draft: dict):
        """(the sample's ctx with the unsaved draft, the real ctx of a note issued after saving it)."""
        company = Company.objects.get(pk=self.company.pk)
        drafted = document_design.draft(company, draft)
        sample = document_preview._sales_note_ctx(company, document_preview._lines(0), drafted)
        self.assertFalse(CompanyDocumentDesign.objects.filter(company=self.company).exists())

        self.assertEqual(self.patch(**draft).status_code, 200)
        note = self.note_for(self.sale([(self.case, 1)], seller_name_snapshot='Vendedora de prueba'))
        return sample, build_sales_note_context(note)

    def assert_same_papers(self, sample, real):
        # The sale's data is the real one; what comes from the company and the design is the sample's.
        mixed = {**real, **{key: sample[key] for key in DESIGN_KEYS}}
        a4 = (document_layout.render_a4(sales_note_document(mixed)),
              document_layout.render_a4(sales_note_document(real)))
        self.assertEqual(page_texts(a4[0]), page_texts(a4[1]))
        self.assertEqual(page_streams(a4[0]), page_streams(a4[1]))
        roll = ticket_pdf_from_ctx(mixed), ticket_pdf_from_ctx(real)
        self.assertEqual(page_texts(roll[0]), page_texts(roll[1]))
        self.assertEqual(page_streams(roll[0]), page_streams(roll[1]))
        self.assertEqual(escpos.sales_note_ticket_from_ctx(mixed), escpos.sales_note_ticket_from_ctx(real))

    def test_the_sample_has_every_key_the_real_note_has(self):
        sample, real = self.contexts({'footer_text': 'Pie.'})
        self.assertEqual(set(sample), set(real))

    def test_an_unsaved_draft_of_every_field_is_what_the_issued_note_prints(self):
        sample, real = self.contexts(EVERY_FIELD)
        for key in DESIGN_KEYS:
            with self.subTest(key=key):
                self.assertEqual(sample[key], real[key])
        self.assertEqual(real['design'], replace(DEFAULT, **{**EVERY_FIELD,
                                                             'block_order': tuple(EVERY_FIELD['block_order'])}))
        self.assertEqual(real['accent_color'], '#1d4ed8')
        self.assertTrue(real['logo_png'])
        self.assertIn('https://tienda.example/garantia', real['warranty_note'])
        self.assert_same_papers(sample, real)

    def test_the_default_design_is_previewed_as_it_is_issued(self):
        company = Company.objects.get(pk=self.company.pk)
        sample = document_preview._sales_note_ctx(company, document_preview._lines(0),
                                                  document_design.design_for(company))
        real = build_sales_note_context(self.note())
        for key in DESIGN_KEYS:
            with self.subTest(key=key):
                self.assertEqual(sample[key], real[key])
        self.assert_same_papers(sample, real)

    def test_texts_typed_with_extra_spaces_are_previewed_as_they_are_saved(self):
        draft = {'thank_you_message': '  Gracias   por   venir.\n\n\n  Vuelve  pronto. ',
                 'document_title': '  Orden   comercial ', 'footer_text': ' Pie   con  espacios '}
        sample, real = self.contexts(draft)
        self.assertEqual(sample['design'], real['design'])
        self.assertEqual(real['design'].thank_you_message, 'Gracias por venir.\n\nVuelve pronto.')
        self.assert_same_papers(sample, real)

    def test_the_thermal_preview_endpoint_prints_the_draft_as_the_real_note_will(self):
        from store.printing import escpos_reader

        draft = {'document_title': 'Orden comercial', 'show_email_on_roll': True,
                 'thank_you_message': 'Gracias de prueba.', 'density': 'airy', 'logo_ticket_align': 'left'}
        response = self.api.post('/api/admin/document-preview/thermal/', {'document': 'sales_note', 'design': draft},
                                 format='json')
        self.assertEqual(response.status_code, 200)
        company = Company.objects.get(pk=self.company.pk)
        sample = document_preview._sales_note_ctx(company, document_preview._lines(0),
                                                  document_design.draft(company, draft))
        self.assertEqual(response.json()['lines'],
                         escpos_reader.simulation(escpos.sales_note_ticket_from_ctx(sample)))


# ---------------------------------------------------------------------------
# A frozen design read years later
# ---------------------------------------------------------------------------

class ThawForwardCompatibilityTest(DesignBase):
    def test_a_value_outside_a_closed_set_takes_the_default(self):
        frozen = {**DEFAULT.as_dict(), 'logo_size': 'enorme', 'logo_a4_align': 'diagonal',
                  'logo_ticket_align': 'arriba', 'warranty_mode': 'nada', 'tax_breakdown_mode': 'otro',
                  'density': 'apretado', 'footer_text': 'Pie congelado.'}
        thawed = document_design.thaw(frozen)
        self.assertEqual(thawed, replace(DEFAULT, footer_text='Pie congelado.'))

    def test_a_negative_width_takes_the_default_and_a_valid_one_is_kept(self):
        self.assertEqual(document_design.thaw({'logo_a4_width_mm': -5}).logo_a4_width_mm, 0)
        self.assertEqual(document_design.thaw({'logo_ticket_width_mm': 50}).logo_ticket_width_mm, 50)

    def test_an_order_without_a_block_keeps_it_where_the_default_order_has_it(self):
        # An order frozen before a block existed, or a damaged one: the missing block is not lost.
        order = ['sale', 'customer', 'commercial', 'post_sale', 'thank_you', 'notes']      # no `warranty`
        thawed = document_design.thaw({'block_order': order})
        self.assertEqual(sorted(thawed.block_order), sorted(BLOCKS))
        self.assertEqual(thawed.block_order[:2], ('sale', 'customer'))
        self.assertIn('warranty', thawed.closing_order)
        # The blocks it had keep their relative order.
        self.assertEqual([key for key in thawed.block_order if key != 'warranty'], order)
        # Duplicates and unknown keys go.
        thawed = document_design.thaw({'block_order': ['notes', 'notes', 'qr', *BLOCKS]})
        self.assertEqual(sorted(thawed.block_order), sorted(BLOCKS))
        self.assertEqual(len(thawed.block_order), len(BLOCKS))

    def test_a_reprint_with_an_order_that_lacks_the_warranty_still_prints_it(self):
        from store.models import SalesNote
        from store.sales_note_services import generate_sales_note_pdf

        note = self.note()
        frozen = {**note.metadata['design'], 'block_order': ['customer', 'sale', 'notes', 'thank_you']}
        SalesNote.objects.filter(pk=note.pk).update(metadata={**note.metadata, 'design': frozen})
        note.refresh_from_db()
        text = ' '.join(' '.join(page_texts(generate_sales_note_pdf(note))).split())
        self.assertIn('Garantía de 12 meses', text)

    def test_every_current_field_round_trips_through_a_freeze(self):
        design = replace(DEFAULT, **{**EVERY_FIELD, 'block_order': tuple(EVERY_FIELD['block_order'])})
        self.assertEqual(document_design.thaw(design.as_dict()), design)


# ---------------------------------------------------------------------------
# No shop in the code
# ---------------------------------------------------------------------------

class NoShopInTheCodeTest(DesignBase):
    BACKEND = (
        'document_design.py', 'document_design_views.py', 'document_preview.py', 'document_preview_views.py',
        'document_layout.py', 'document_style.py', 'document_legends.py', 'note_layout.py',
        'sales_note_services.py', 'ticket_services.py', 'company_settings.py', 'fiscal_logo.py',
        'printing/escpos.py', 'migrations/0177_document_design_v2.py',
    )
    FRONTEND = (
        'app/admin/settings/documents/page.tsx', 'app/admin/settings/documents/DocumentDesignForm.tsx',
        'app/admin/settings/documents/DocumentPreview.tsx', 'app/admin/settings/documents/ThermalRoll.tsx',
        'app/admin/lib/document-design-api.ts', 'app/admin/lib/document-preview-api.ts',
    )
    #: The first shop's name, its club, its city and its gold (`0065_pilot_brand_palette`).
    FORBIDDEN = ('black dog', 'blackdog', 'la manada', 'arequipa', '#c8a45d', 'c8a45d')

    def test_no_shop_is_written_where_the_papers_are_drawn_or_designed(self):
        backend = Path(__file__).resolve().parent
        frontend = backend.parent.parent / 'frontend'
        sources = [backend / name for name in self.BACKEND]
        if frontend.is_dir():
            sources += [frontend / name for name in self.FRONTEND]
        for path in sources:
            source = path.read_text(encoding='utf-8').lower()
            for forbidden in self.FORBIDDEN:
                with self.subTest(path=path.name, forbidden=forbidden):
                    self.assertNotIn(forbidden, source)

    def test_no_migration_seeds_a_document_design(self):
        folder = Path(__file__).resolve().parent / 'migrations'
        for path in sorted(folder.glob('0*.py')):
            source = path.read_text(encoding='utf-8')
            if 'CompanyDocumentDesign' in source or 'companydocumentdesign' in source:
                with self.subTest(migration=path.name):
                    self.assertNotIn('RunPython', source)
                    self.assertNotIn('RunSQL', source)
