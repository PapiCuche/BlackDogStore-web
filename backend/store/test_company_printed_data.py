"""
DESIGN-VERIFY — the company data the papers print, refused when it would not print.

The sales note, the order receipt, the 80 mm ticket and the thermal print draw
the company's name, legal name, address, city, phone and warranty text with a
font (WinAnsi) and a thermal head (cp858) that cannot draw an emoji or an
ideogram: `document_style.printable` prints «?» in its place. The design's own
texts were already refused for that (`document_design.text_problem`); the
company data is refused now too, by `PATCH /api/admin/company-settings/` — only
when it CHANGES, so a form that sends an old value back still saves.
"""
from store.models import CompanySettings
from store.test_document_preview import PreviewBase


SETTINGS_URL = '/api/admin/company-settings/'


class CompanyPrintedDataTest(PreviewBase):
    def setUp(self):
        super().setUp()
        self.only('company.manage', 'company.view')

    def save(self, **body):
        return self.api.patch(SETTINGS_URL, body, format='json')

    def test_what_the_paper_would_print_as_a_question_mark_is_refused(self):
        for field, value in (('warranty_policy_text', 'Garantía de 12 meses 😀'), ('phone', '054 555 000 ☎'),
                             ('legal_address', 'Av. Siempre Viva 742 🏠'), ('city', 'Ciudad 中')):
            with self.subTest(field=field):
                response = self.save(**{field: value})
                self.assertEqual(response.status_code, 400, response.content[:300])
                self.assertIn(field, response.json())
                self.assertIn('«?»', ' '.join(response.json()[field]))
        for field, value in (('name', 'Tienda 😀'), ('legal_name', 'Tienda 😀 S.A.C.')):
            with self.subTest(field=field):
                response = self.save(**{field: value})
                self.assertEqual(response.status_code, 400, response.content[:300])
                self.assertIn('«?»', str(response.json()))
        self.assertNotIn('😀', CompanySettings.objects.get(company=self.company).warranty_policy_text)

    def test_what_the_paper_prints_is_accepted_line_breaks_and_accents_included(self):
        text = 'Garantía de 12 meses.\nNo cubre golpes ni humedad — consulte «condiciones» (n.º 1).'
        response = self.save(warranty_policy_text=text, phone='+51 054 555 000', city='Cusco')
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertEqual(CompanySettings.objects.get(company=self.company).warranty_policy_text, text)

    def test_an_old_value_sent_back_unchanged_does_not_block_the_save(self):
        CompanySettings.objects.filter(company=self.company).update(warranty_policy_text='Antigua 😀')
        response = self.save(warranty_policy_text='Antigua 😀', phone='054 000 000')
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertEqual(CompanySettings.objects.get(company=self.company).phone, '054 000 000')
