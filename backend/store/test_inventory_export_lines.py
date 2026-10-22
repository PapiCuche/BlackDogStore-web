"""
INV-EXP-01 — download only the new devices, only the used ones, only the
accessories, or any mix (`line=new,used`). A device is a product that belongs
to a model or is counted by serial; everything else on the new line is an
accessory; the used line is the used line. The filter only chooses ROWS: every
file keeps its headers, so a reimport file still goes back in.
"""
from store.models import Product
from store.test_inventory_exports import _Base


class _Lines(_Base):
    def setUp(self):
        super().setUp()
        self.used_phone = self.product('Teléfono X seminuevo', 'telefono-x-semi', '3200.00', self.phones,
                                       is_serialized=True, requires_imei=True,
                                       condition_line=Product.CONDITION_USED)

    def names(self, kind, **query):
        response = self.get(kind, **query)
        return sorted({row.get('Nombre') or row.get('Producto') for row in self.records(response)})


class LineFilterTest(_Lines):
    def test_only_the_new_devices(self):
        self.assertEqual(self.names('products', line='new'), ['Teléfono X'])

    def test_only_the_used_ones(self):
        self.assertEqual(self.names('products', line='used'), ['Teléfono X seminuevo'])

    def test_only_the_accessories(self):
        self.assertEqual(self.names('products', line='accessory'), ['Batería vieja', 'Cable', 'Funda'])

    def test_new_and_used_together(self):
        self.assertEqual(self.names('products', line='new,used'), ['Teléfono X', 'Teléfono X seminuevo'])

    def test_without_the_filter_it_is_everything_as_before(self):
        self.assertEqual(self.names('products'),
                         ['Batería vieja', 'Cable', 'Funda', 'Teléfono X', 'Teléfono X seminuevo'])

    def test_stock_and_devices_follow_the_same_filter(self):
        self.assertEqual(self.names('stock', mode='report', line='accessory'), ['Batería vieja', 'Cable', 'Funda'])
        units = self.records(self.get('units', mode='report', line='accessory'))
        self.assertEqual(units, [])
        self.assertEqual(len(self.records(self.get('units', mode='report', line='new'))), 3)

    def test_a_reimport_file_keeps_its_headers(self):
        _title, every, _rows = self.sheet(self.get('products'))
        _title, only_new, rows = self.sheet(self.get('products', line='new'))
        self.assertEqual(only_new, every)
        self.assertEqual(len(rows), 1)

    def test_an_unknown_line_is_refused_not_ignored(self):
        for value in ('nuevo', 'new,', ',', 'new,foo', 'NEW;USED'):
            with self.subTest(value=value):
                response = self.get('products', line=value)
                self.assertEqual(response.status_code, 400, response.content[:200])

    def test_the_audit_says_which_lines(self):
        self.get('products', line='used,new')
        self.assertEqual(self.exported().latest('pk').metadata['filters']['lines'], ['new', 'used'])

    def test_another_companys_products_never_appear(self):
        self.assertNotIn('Producto ajeno', self.names('products', line='accessory'))
