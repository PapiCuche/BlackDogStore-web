"""
B1-0b-2 · Amounts in the product and stock uploads (DEC-INV-MONEY-01, D-B1-0b-2a…h).

DRAFT for the implementation PR. Kept OUTSIDE the repository until the owner's GO.
Written first (TDD): run against master it must be RED exactly where the audit found
wrong reads, and GREEN where today's guarantees already hold.

Columns and what each one accepts:

    column              text dec.  number dec.  ceiling   currency
    Precio de venta     2          2            < 1e8     «S/», «s/», «S/.» only with a stored PEN
    Costo de referencia 2          2            < 1e10    same
    Costo unitario      2          4            < 1e10    same (stock upload; D-B1-0b-2h: text never > 2)
    Costo histórico     2          2            < 1e10    same; an error is a warning (report only)

Nothing is ever converted, rounded or guessed. Typed Decimals already stored are never re-read.
"""
import datetime
import io
from datetime import timedelta
from decimal import Decimal as D

import openpyxl
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from store import import_exports, import_services, stock_import_services
from store import inventory_services as inventory
from store.models import BranchStock, BulkImportJob, CompanySettings, Product, ProductBarcode, StockMovement
from store.test_cost_imports import XLSX, _client, _Shop
from store.tests import _p2d_member, _p3_company, _prod

P_HEADERS = ['Código', 'Nombre', 'Precio de venta', 'Costo de referencia']
S_HEADERS = ['ID', 'CODIGO', 'CODIGO EAN', 'NOMBRE', 'ALMACEN 1 - 11416', 'Costo unitario']
H_HEADERS = ['Código interno original', 'Nombre', 'Precio comercial', 'Costo histórico']

PRICE, REF, STOCK, HIST = 'price', 'reference_cost', 'unit_cost', 'historical_cost'
ALL = (PRICE, REF, STOCK, HIST)
TWO = (PRICE, REF, HIST)

# --------------------------------------------------------------------------------------------
# Expected reads. `None` = refused (row error; a warning for the historical cost).
# --------------------------------------------------------------------------------------------
ACCEPTED_TEXT = {           # same result in every column (PEN company)
    '5500': '5500', '5500,00': '5500', '5500.00': '5500', '1.999,90': '1999.90', '1,999.90': '1999.90',
    '1.234.567': '1234567', '0.50': '0.50', '0,50': '0.50', '0': '0',
    'S/ 1500': '1500', 'S/1500': '1500', 'S/.1500': '1500', 'S/. 1500': '1500', 's/ 1500': '1500',
    'S/1.500,00': '1500', 'S/. 1,500.00': '1500',
}
REFUSED_TEXT = [            # refused in every column, whatever the company currency
    '.50', ',50', '1500.', '1500,',                          # D-B1-0b-2f: explicit decimal
    '5.500', '5,500', '1800,2000', '1.2,3',                  # ambiguous / invalid grouping
    '1800 2000', '1800/2000', '1800-2000', '5 500', '1 5 0 0', '1 500', '1 500',
    '１５００', '١٥٠٠',  # other scripts' digits
    '15S/00', '1$500', '1500$', '1500S/', '1500 S/',        # embedded currency
    '$1500', '$ 1500', 'US$1500', 'USD 1500', '1500 USD', '€1500', 'EUR 1500', '1500 dólares',
    'PEN 1500', '1500 PEN', '1500 soles',                    # D-B1-0b-2c: words refused for now
    '-5', '+1500', '(1500)', '1500-', '1e3', 'NaN', 'Infinity', '1_500', "'1500", 'S/', 'S/.', 'abc',
]
PRECISION_TEXT = {          # column -> {text: expected}
    PRICE: {'5.55': '5.55', '5.5555': None, '13,1234': None, '1,999.905': None, '5.500,1234': None},
    STOCK: {'5.55': '5.55', '5.5': '5.5', '13.12345': None, '1.234,56789': None, '13.125': None,
            # D-B1-0b-2h (option B): TEXT never carries more than 2 decimals, not even the stock cost.
            '13.1234': None, '13,1234': None, '13.1250': None, '0.1234': None, '0,125': None,
            '1,999.905': None, '5.500,1234': None, '1.800,2000': None},
}
PRECISION_TEXT[REF] = PRECISION_TEXT[PRICE]
PRECISION_TEXT[HIST] = PRECISION_TEXT[PRICE]

NUMBER = {                  # column -> {cell value: expected}; a NUMBER cell is never «ambiguous»
    PRICE: {5500: '5500', 5500.5: '5500.50', 1500.0: '1500', 0.1 + 0.2: '0.30', 13.125: None,
            13.1234: None, 12.345: None, 0.00001: None, -5: None},
    STOCK: {5500: '5500', 0.1 + 0.2: '0.30', 13.125: '13.125', 13.1234: '13.1234', 12.345: '12.345',
            1234.5678: '1234.5678', 13.12345: None, 0.00001: None, -5: None},
}
NUMBER[REF] = NUMBER[PRICE]
NUMBER[HIST] = NUMBER[PRICE]

CEILINGS = {                # column -> (largest accepted, smallest refused)
    PRICE: ('99999999.99', '100000000'),
    REF: ('9999999999.99', '10000000000'),
    HIST: ('9999999999.99', '10000000000'),
    STOCK: ('9999999999.99', '10000000000'),       # as TEXT: 2 decimals (D-B1-0b-2h)
}


def book(headers, rows, title):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = title
    ws.append(headers)
    for row in rows:
        ws.append(row)
    out = io.BytesIO()
    wb.save(out)
    return SimpleUploadedFile('carga.xlsx', out.getvalue(), content_type=XLSX)


def _find(data, key):
    if isinstance(data, dict):
        if key in data:
            return data[key]
        for value in data.values():
            found = _find(value, key)
            if found is not None:
                return found
    return None


class _Money(_Shop):
    def set_currency(self, value, company=None):
        CompanySettings.objects.filter(company=company or self.company).update(currency=value)

    def preview_products(self, rows, headers=P_HEADERS, actor=None, company=None):
        return import_services.preview_products(company=company or self.company, actor=actor or self.owner,
                                                upload=book(headers, rows, 'Productos'), filename='p.xlsx')

    def preview_stock(self, cost, actor=None):
        return stock_import_services.preview_stock(
            company=self.company, actor=actor or self.owner, filename='s.xlsx', branch_map={4: self.branch.pk},
            mode=BulkImportJob.MODE_RECONCILE, upload=book(S_HEADERS, [[1, 'FUN-001', '', 'Funda', 10, cost]],
                                                           'Inventario'))

    def read(self, column, value):
        """(value as Decimal or None, row errors + relevant warnings) for one cell in one column."""
        if column == PRICE:
            row = self.preview_products([['FUN-001', 'Funda', value, '10']]).rows.get()
        elif column == REF:
            row = self.preview_products([['FUN-001', 'Funda', '59.90', value]]).rows.get()
        elif column == STOCK:
            row = self.preview_stock(value).rows.get()
        else:
            row = self.preview_products([['OLD-1', 'Funda vieja', '59.90', value]], headers=H_HEADERS).rows.get()
            warnings = [w for w in (row.warnings or []) if 'histórico' in w]
            found = _find(row.normalized_data, column)
            return (None if found in (None, '') else D(str(found))), list(row.errors) + warnings
        if row.errors:
            return None, list(row.errors)
        found = row.normalized_data.get(column)
        return (None if found in (None, '') else D(str(found))), []

    def check(self, column, value, expected):
        with self.subTest(column=column, value=value, expected=expected):
            got, problems = self.read(column, value)
            if expected is None:
                self.assertIsNone(got, f'{value!r} was read as {got} in {column}')
                self.assertTrue(problems, f'{value!r} refused without saying why in {column}')
            else:
                self.assertEqual(got, D(expected), problems)


# --------------------------------------------------------------------------------------------
# 1. What a cell says
# --------------------------------------------------------------------------------------------
class TextAmountTest(_Money):
    def test_unambiguous_text_is_read_exactly_in_every_column(self):
        for column in ALL:
            for text, expected in ACCEPTED_TEXT.items():
                self.check(column, text, expected)

    def test_everything_else_is_refused_and_never_repaired(self):
        for column in ALL:
            for text in REFUSED_TEXT:
                self.check(column, text, None)

    def test_precision_per_column_without_rounding(self):
        for column, cases in PRECISION_TEXT.items():
            for text, expected in cases.items():
                self.check(column, text, expected)


class ReviewFindingsTest(_Money):
    """Independent review of B1-0b-2 (2026-11-15)."""

    def test_a_very_long_text_amount_is_a_row_error_not_a_500(self):
        for column in ALL:
            for text in ('490154203237518490154203237518', '1' * 31, '1' * 40):
                self.check(column, text, None)

    def test_soles_dot_glued_to_one_or_two_digits_is_ambiguous(self):
        # «S/.50» may be «S/. 50» or «S/ .50» (0.50): a factor of 100, so it is refused, never guessed.
        for text in ('S/.50', 's/.5', 'S/.05'):
            self.check(PRICE, text, None)
        for text, expected in (('S/. 50', '50'), ('S/.1500', '1500'), ('S/.50,00', '50'), ('S/ 0.50', '0.50')):
            self.check(PRICE, text, expected)

    def test_a_tampered_job_is_refused_without_a_500(self):
        cases = (
            ({'unit_cost': '1_000'}, None),
            ({'unit_cost': ' 12'}, None),
            ({'unit_cost': '١٢'}, None),
            (None, 'nonsense'),
        )
        for patch, policy in cases:
            with self.subTest(patch=patch, policy=policy):
                job = self.preview_stock('12')
                if patch:
                    row = job.rows.get()
                    row.normalized_data = {**row.normalized_data, **patch}
                    row.save(update_fields=['normalized_data'])
                if policy:
                    BulkImportJob.objects.filter(pk=job.pk).update(summary={**job.summary, 'money_policy': policy})
                client = _client(self.owner)
                client.raise_request_exception = False
                res = client.post(f'/api/admin/inventory/import/{job.pk}/apply/')
                self.assertIn(res.status_code, (400, 409), res.content[:300])
                self.assertFalse(StockMovement.objects.filter(product=self.case).exists())

    def test_a_currency_refusal_does_not_repeat_the_cell(self):
        _got, problems = self.read(REF, 'mil quinientos soles')
        self.assertTrue(problems)
        self.assertNotIn('mil quinientos', ' '.join(problems))


class OwnerMandatoryCasesTest(_Money):
    """D-B1-0b-2h, the six cases the owner listed, and that every refusal says how to fix the entry."""

    def test_the_six_cases(self):
        self.check(STOCK, '13,1234', None)
        for column in ALL:
            self.check(column, '1800,2000', None)
            self.check(column, '0,50', '0.50')
            self.check(column, '.50', None)
        self.check(STOCK, 13.1234, '13.1234')
        self.check(PRICE, 13.1234, None)

    def test_each_refusal_says_how_to_fix_it(self):
        cases = (
            (STOCK, '13,1234', ('2 decimales', 'celda numérica')),
            (PRICE, '.50', ('0.50', '0,50')),
            (PRICE, 13.1234, ('13.1234', '2 decimales')),
            (PRICE, '$1500', ('sin',)),
            (PRICE, '100000000', ('99999999.99', '99 999 999,99', 'máximo')),
        )
        for column, value, hints in cases:
            with self.subTest(column=column, value=value):
                _got, problems = self.read(column, value)
                self.assertTrue(problems, f'{value!r} accepted in {column}')
                text = ' '.join(problems)
                self.assertTrue(any(h in text for h in hints), f'{value!r}: «{text}» does not say how to fix it')


class NumberCellTest(_Money):
    def test_a_number_cell_keeps_its_exact_value_or_is_refused_for_its_decimals(self):
        for column, cases in NUMBER.items():
            for value, expected in cases.items():
                self.check(column, value, expected)

    def test_a_number_cell_is_never_called_ambiguous(self):
        for column in ALL:
            with self.subTest(column=column):
                _got, problems = self.read(column, 13.125)
                self.assertFalse([p for p in problems if 'ambiguo' in p], problems)

    def test_dates_booleans_and_formulas_are_refused(self):
        for column in ALL:
            for value in (datetime.datetime(2026, 11, 15), True, '=100*2'):
                self.check(column, value, None)


class CeilingTest(_Money):
    def test_each_column_refuses_its_ceiling_in_the_preview(self):
        for column, (largest, refused) in CEILINGS.items():
            self.check(column, largest, largest)
            self.check(column, refused, None)
            self.check(column, float(refused), None)
            self.check(column, 1e15, None)

    def test_a_stock_number_cell_reaches_the_ceiling_with_four_decimals(self):
        self.check(STOCK, 9999999999.9999, '9999999999.9999')

    def test_rounding_never_crosses_a_ceiling(self):
        self.check(PRICE, '99999999.995', None)
        self.check(STOCK, '9999999999.99999', None)


# --------------------------------------------------------------------------------------------
# 2. Currency (D-B1-0b-2a…c)
# --------------------------------------------------------------------------------------------
class CurrencyTest(_Money):
    SOLES = ['S/ 1500', 'S/.1500', 's/ 1500']

    def test_soles_only_with_a_stored_pen(self):
        # (A trailing space cannot be stored: PostgreSQL trims the excess spaces of a varchar(3).)
        for currency in ('', 'USD', 'pen', 'Pen'):
            self.set_currency(currency)
            for column in ALL:
                for text in self.SOLES:
                    self.check(column, text, None)
                self.check(column, '1500', '1500')       # no symbol: the company's currency, as today

    def test_no_settings_row_refuses_soles(self):
        CompanySettings.objects.filter(company=self.company).delete()
        for column in ALL:
            self.check(column, 'S/ 1500', None)
            self.check(column, '1500', '1500')

    def test_dollars_are_refused_even_for_a_usd_company(self):
        self.set_currency('USD')
        for column in ALL:
            for text in ('$1500', 'US$1500', 'USD 1500'):
                self.check(column, text, None)

    def test_the_refusal_names_the_currency(self):
        _got, problems = self.read(PRICE, '$1500')
        self.assertTrue(any('$' in p or 'dólar' in p.lower() for p in problems), problems)


# --------------------------------------------------------------------------------------------
# 3. Permissions and companies (must stay as they are)
# --------------------------------------------------------------------------------------------
class AuthorityTest(_Money):
    def catalogue_user(self):
        user, _ = _p2d_member(self.company, 'b10b2_catalogo', ['company.view', 'products.view', 'products.manage'])
        return user

    def test_without_costs_view_no_new_shape_tells_anything_about_the_cell(self):
        client = _client(self.catalogue_user())
        shapes = set()
        for value in ['20.00', 'S/.1500', '13.1234', '$20', '１５００', '.50', '1e10', 'caro']:
            res = client.post('/api/admin/products/import/preview/', {
                'file': book(P_HEADERS, [['FUN-001', 'Funda', '59.90', value]], 'Productos'),
            }, format='multipart')
            self.assertEqual(res.status_code, 201, res.content)
            [row] = res.json()['rows']
            shapes.add(repr((row['action'], row['errors'], row['warnings'], sorted(row['data'].items()))))
        self.assertEqual(len(shapes), 1, shapes)

    def test_each_company_reads_with_its_own_currency(self):
        other = _p3_company('b10b2-otra', 'Otra tienda')
        CompanySettings.objects.update_or_create(company=other, defaults={'currency': 'USD'})
        self.set_currency('PEN')
        other_owner, _ = _p2d_member(other, 'b10b2_otra_duena', [
            'company.view', 'products.view', 'products.manage', 'costs.view'])
        theirs_case = _prod(other, 'Funda', 'funda-otra', price='10.00', inventory=0)
        ProductBarcode.objects.create(company=other, product=theirs_case, code='FUN-002',
                                      symbology=ProductBarcode.INTERNAL)
        # Control: the same row with a plain amount is fine for the other company, so a refusal below is the currency.
        plain = self.preview_products([['FUN-002', 'Funda', '1500', '10']], actor=other_owner,
                                      company=other).rows.get()
        self.assertEqual((plain.errors, D(plain.normalized_data['price'])), ([], D('1500')))
        mine = self.preview_products([['FUN-001', 'Funda', 'S/ 1500', '10']]).rows.get()
        theirs = self.preview_products([['FUN-002', 'Funda', 'S/ 1500', '10']], actor=other_owner,
                                       company=other).rows.get()
        self.assertEqual(D(mine.normalized_data['price']), D('1500'))
        self.assertTrue(theirs.errors)
        self.assertFalse(theirs.normalized_data.get('price'))

    def test_another_company_cannot_apply_my_job(self):
        job = self.preview_products([['FUN-001', 'Funda', '1500', '10']])
        other = _p3_company('b10b2-ajena', 'Ajena')
        stranger, _ = _p2d_member(other, 'b10b2_ajena', [
            'company.view', 'products.view', 'products.manage', 'costs.view'])
        res = _client(stranger).post(f'/api/admin/products/import/{job.pk}/apply/')
        self.assertEqual(res.status_code, 404, res.content)
        self.case.refresh_from_db()
        self.assertEqual(self.case.price, D('59.90'))


# --------------------------------------------------------------------------------------------
# 4. Previews, apply and what is already stored
# --------------------------------------------------------------------------------------------
class PreviewLifecycleTest(_Money):
    def test_an_expired_preview_is_refused(self):
        job = self.preview_products([['FUN-001', 'Funda', '1500', '10']])
        BulkImportJob.objects.filter(pk=job.pk).update(created_at=timezone.now() - timedelta(days=1))
        job.refresh_from_db()
        with self.assertRaises(import_services.ImportError_) as refused:
            import_services.apply_products(job=job, actor=self.owner)
        self.assertEqual(refused.exception.code, 'preview_expired')

    def test_a_preview_made_before_the_new_policy_is_refused_as_stale(self):
        for kind in ('products', 'stock'):
            with self.subTest(kind=kind):
                job = (self.preview_products([['FUN-001', 'Funda', '1500', '10']]) if kind == 'products'
                       else self.preview_stock('12'))
                summary = dict(job.summary or {})
                summary.pop('money_policy', None)
                BulkImportJob.objects.filter(pk=job.pk).update(summary=summary)
                job.refresh_from_db()
                apply = import_services.apply_products if kind == 'products' else stock_import_services.apply_stock
                error = import_services.ImportError_ if kind == 'products' else stock_import_services.StockImportError
                with self.assertRaises(error) as refused:
                    apply(job=job, actor=self.owner)
                self.assertEqual(refused.exception.code, 'preview_stale')

    def test_a_current_preview_records_its_policy_version_and_applies(self):
        job = self.preview_products([['FUN-001', 'Funda', '1500', '10']])
        self.assertEqual((job.summary or {}).get('money_policy'), 2)
        import_services.apply_products(job=job, actor=self.owner)
        self.case.refresh_from_db()
        self.assertEqual(self.case.price, D('1500'))

    def test_an_amount_over_the_ceiling_inside_a_job_is_never_a_500_and_writes_nothing(self):
        cases = (
            ('products', '/api/admin/products/import/{}/apply/', PRICE, '100000000'),
            ('products', '/api/admin/products/import/{}/apply/', REF, '10000000000'),
            ('stock', '/api/admin/inventory/import/{}/apply/', STOCK, '10000000000'),
        )
        for kind, url, column, amount in cases:
            with self.subTest(column=column):
                job = (self.preview_products([['NEW-1', 'Cable', '20', '5'], ['FUN-001', 'Funda', '30', '6']])
                       if kind == 'products' else self.preview_stock('12'))
                row = job.rows.order_by('row_number').last()
                row.normalized_data = {**row.normalized_data, column: amount}
                row.save(update_fields=['normalized_data'])
                client = _client(self.owner)
                client.raise_request_exception = False
                res = client.post(url.format(job.pk))
                self.assertIn(res.status_code, (400, 409), res.content[:300])
                self.assertFalse(Product.objects.filter(name='Cable').exists())
                self.case.refresh_from_db()
                self.assertEqual(self.case.price, D('59.90'))
                self.assertFalse(StockMovement.objects.filter(product=self.case).exists())

    def test_stock_cost_with_four_decimals_reaches_the_movement_and_the_average(self):
        job = self.preview_stock(13.1234)
        stock_import_services.apply_stock(job=job, actor=self.owner)
        movement = StockMovement.objects.filter(product=self.case).latest('pk')
        self.assertEqual(movement.unit_cost, D('13.1234'))
        self.assertEqual(BranchStock.objects.get(branch=self.branch, product=self.case).average_cost,
                         D('13.1234'))

    def test_what_is_stored_is_never_read_again(self):
        inventory.create_stock_movement(
            branch=self.branch, product_id=self.case.pk, movement_type=StockMovement.PURCHASE_ENTRY,
            quantity=3, reason='Compra', unit_cost=D('13.1234'))
        Product.objects.filter(pk=self.case.pk).update(reference_cost=D('20.00'))
        job = self.preview_products([['FUN-001', 'Funda', '61.00']], headers=P_HEADERS[:3])
        import_services.apply_products(job=job, actor=self.owner)
        self.case.refresh_from_db()
        self.assertEqual(self.case.reference_cost, D('20.00'))
        self.assertEqual(BranchStock.objects.get(branch=self.branch, product=self.case).average_cost,
                         D('13.1234'))


# --------------------------------------------------------------------------------------------
# 5. The official templates still load
# --------------------------------------------------------------------------------------------
class TemplateRoundTripTest(_Money):
    def test_the_product_template_filled_in_excel_loads_exactly(self):
        sheet_book = openpyxl.load_workbook(io.BytesIO(import_exports.product_template_bytes(self.company)))
        sheet = sheet_book.active
        headers = [cell.value for cell in sheet[1]]
        row = [None] * len(headers)
        row[headers.index('Código')] = 'FUN-001'
        row[headers.index('Nombre')] = 'Funda'
        row[headers.index('Precio de venta')] = 64.9
        row[headers.index('Costo de referencia')] = 21.5
        sheet.append(row)
        for column in ('Precio de venta', 'Costo de referencia'):
            sheet.cell(row=2, column=headers.index(column) + 1).number_format = '0.00'
        out = io.BytesIO()
        sheet_book.save(out)
        job = import_services.preview_products(
            company=self.company, actor=self.owner, filename='plantilla.xlsx',
            upload=SimpleUploadedFile('plantilla.xlsx', out.getvalue(), content_type=XLSX))
        data = job.rows.get().normalized_data
        self.assertEqual((D(data['price']), D(data['reference_cost'])), (D('64.90'), D('21.50')))
