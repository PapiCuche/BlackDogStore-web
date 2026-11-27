"""
DISCOUNT-SHARE-01 — characterization of the per-line promotion discount.

`fiscal_services._promotion_line_discounts` decides how much of a sale's
automatic promotion each line carries (Catálogo N.º 53, code `00`). DISCOUNT-
SHARE-01 moves that computation into a pure function so the internal sales
note can print the same per-line discount later. It is a PURE REFACTOR: the
fiscal XML must stay byte-identical.

These tests were written BEFORE the extraction and pin, for each scenario:
  - the per-line gross discount `_promotion_line_discounts` returns;
  - every number of every `Line` of the `InvoiceData` and its global allowance;
  - the SHA-256 of the unsigned UBL XML the builder writes from it
    (`paid_at` is fixed, so the bytes are deterministic);
  - the exact error each fail-closed case raises.

If one of these changes, the refactor changed the fiscal output.
"""

import hashlib
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.core.cache import cache
from django.test import TestCase, override_settings

from .fiscal import builder
from .fiscal_services import FiscalError, _order_to_invoice_data, _promotion_line_discounts
from .models import (
    AppliedPromotion, Coupon, Customer, FiscalDocumentType, FiscalSeries, Order, PaymentMethod,
    Product, Promotion,
)
from .tests import (
    _C1_POS, _C12_DISCOUNT, _beta_issuing_settings, _c1_product, _c1_sale, _c1_stock, _c13_combo, _p2d_member,
    _p3_company,
)

_PAID_AT = datetime(2026, 3, 14, 10, 30, tzinfo=ZoneInfo('America/Lima'))


#: scenario -> (per-line gross promotion discounts, line fingerprints, global allowances, sha256)
PINNED = {
    'fixed_combo_boleta': (
        ('33.33', '16.67'),
        (('1', '84.7400000000', '66.6700000000', '56.50', '10.17', '18.00', (('00', '28.24', '84.74'),)),
         ('1', '42.3800000000', '33.3300000000', '28.25', '5.08', '18.00', (('00', '14.13', '42.38'),))),
        (),
        '7378c5af91f423f08d8a0a64219b94d77a3ac80356a70b805f6e97bcd690c1e2'),
    # A pre-attribution snapshot rebuilds the same attribution: SAME bytes.
    'legacy_snapshot': (
        ('33.33', '16.67'),
        (('1', '84.7400000000', '66.6700000000', '56.50', '10.17', '18.00', (('00', '28.24', '84.74'),)),
         ('1', '42.3800000000', '33.3300000000', '28.25', '5.08', '18.00', (('00', '14.13', '42.38'),))),
        (),
        '7378c5af91f423f08d8a0a64219b94d77a3ac80356a70b805f6e97bcd690c1e2'),
    'percent_combo_factura': (
        ('10.00', '5.00'),
        (('1', '84.7400000000', '90.0000000000', '76.27', '13.73', '18.00', (('00', '8.47', '84.74'),)),
         ('1', '42.3800000000', '45.0000000000', '38.14', '6.86', '18.00', (('00', '4.24', '42.38'),))),
        (),
        'd496c068e6b4c309af0aef9cc5d172d17397b245b172b0f14370d47123ef56ea'),
    'multiple_applications': (
        ('80.00', '20.00'),
        (('4', '84.7450000000', '80.0000000000', '271.18', '48.82', '18.00', (('00', '67.80', '338.98'),)),
         ('2', '42.3750000000', '40.0000000000', '67.80', '12.20', '18.00', (('00', '16.95', '84.75'),))),
        (),
        '16ecee5637fdf62c4c917072e002e55db307082e0102f0476ae3101ef325a20a'),
    'two_promotions_mixed': (
        ('300.00', '33.33', '16.67', '0.00'),
        (('1', '2542.3700000000', '2700.0000000000', '2288.13', '411.87', '18.00', (('00', '254.24', '2542.37'),)),
         ('1', '84.7400000000', '66.6700000000', '56.50', '10.17', '18.00', (('00', '28.24', '84.74'),)),
         ('1', '42.3800000000', '33.3300000000', '28.25', '5.08', '18.00', (('00', '14.13', '42.38'),)),
         ('2', '25.4250000000', '30.00', '50.85', '9.15', '18.00', ())),
        (),
        'c8f266aabae67d701e0a22231108f06ae081adfa4e0842a58cdc02195512ecdb'),
    'partial_line': (
        ('33.33', '16.67', '0.00'),
        (('2', '84.7450000000', '83.3350000000', '141.25', '25.42', '18.00', (('00', '28.24', '169.49'),)),
         ('1', '42.3800000000', '33.3300000000', '28.25', '5.08', '18.00', (('00', '14.13', '42.38'),)),
         ('1', '25.4200000000', '30.00', '25.42', '4.58', '18.00', ())),
        (),
        '720b555e9024cfcaad42cf55e7d9165aaa72c93839b21c2d460c0e16879bdb37'),
    'cent_remainders': (
        ('142.86', '4.76', '2.38'),
        (('1', '2542.3700000000', '2857.1400000000', '2421.30', '435.84', '18.00', (('00', '121.07', '2542.37'),)),
         ('1', '84.7400000000', '95.2400000000', '80.71', '14.53', '18.00', (('00', '4.03', '84.74'),)),
         ('1', '42.3800000000', '47.6200000000', '40.36', '7.26', '18.00', (('00', '2.02', '42.38'),))),
        (),
        '39cc7262ea0c061f575c106accdfeb25d456531a4d6bce048b0492720c7b06af'),
    'odd_rounding': (
        ('47.29', '0.00', '14.19', '28.23'),
        (('1', '84.7500000000', '52.7100000000', '44.67', '8.04', '18.00', (('00', '40.08', '84.75'),)),
         ('1', '42.3700000000', '50.00', '42.37', '7.63', '18.00', ()),
         ('1', '25.4300000000', '15.8100000000', '13.40', '2.41', '18.00', (('00', '12.03', '25.43'),)),
         ('3', '16.8633333333', '10.4900000000', '26.67', '4.80', '18.00', (('00', '23.92', '50.59'),))),
        (),
        '9458a7ed3db91e0c1bb7456aed3bf1a5324b4c7efa3a9283b93dd16afc69a592'),
    'coupon_factura': (
        None,
        (('1', '84.7500000000', '100.00', '84.75', '15.25', '18.00', ()),
         ('1', '42.3700000000', '50.00', '42.37', '7.63', '18.00', ())),
        (('02', '12.71', '127.12'),),
        '8e0a59aa29a7959b589dcea46ef83ca302fd5ce03ba1da7c04c89c755e2581c5'),
    'manual_boleta': (
        None,
        (('1', '84.7500000000', '100.00', '84.75', '15.25', '18.00', ()),
         ('1', '42.3700000000', '50.00', '42.37', '7.63', '18.00', ())),
        (('02', '16.95', '127.12'),),
        '37485fa09a0487778462e7ec4df97d3a63bdf0cfb4aa56e4d1152a53b0aee644'),
    'no_discount': (
        None,
        (('1', '84.7400000000', '100.00', '84.74', '15.26', '18.00', ()),
         ('2', '16.8650000000', '19.90', '33.73', '6.07', '18.00', ())),
        (),
        'e5e9452b2f89ef88c1a3d9f3bc22f3f3eb730e25931976a0946c5b531995c083'),
}


@override_settings(**_beta_issuing_settings('20100077701'))   # the company's RUC
class PromotionLineShareCharacterizationTest(TestCase):
    def setUp(self):
        cache.clear()
        self.company = _p3_company(
            'ds-share', 'Empresa Reparto', tax_id='20100077701',
            legal_name='EMPRESA REPARTO SAC')
        self.branch = self.company.default_inventory_branch
        self.series = {
            Order.ReceiptType.FACTURA: FiscalSeries.objects.create(
                company=self.company, document_type=FiscalDocumentType.INVOICE, series='F001'),
            Order.ReceiptType.BOLETA: FiscalSeries.objects.create(
                company=self.company, document_type=FiscalDocumentType.RECEIPT, series='B001'),
        }
        self.phone = _c1_product(self.company, 'Teléfono DS', '3000.00')
        self.case = _c1_product(self.company, 'Funda DS', '100.00')
        self.glass = _c1_product(self.company, 'Vidrio DS', '50.00')
        self.cable = _c1_product(self.company, 'Cable DS', '30.00')
        self.odd = _c1_product(self.company, 'Adaptador DS', '19.90')
        for product in (self.phone, self.case, self.glass, self.cable, self.odd):
            _c1_stock(self.branch, product, 50)
        self.seller, _ = _p2d_member(
            self.company, 'ds_seller',
            ['company.view', _C1_POS, _C12_DISCOUNT, 'sales.fiscal.issue'],
        )
        self.ruc_customer = Customer.objects.create(
            company=self.company, customer_type=Customer.TYPE_BUSINESS,
            business_name='CLIENTE DS SAC', document_type=Order.DocumentType.RUC,
            document_number='20000000001')

    # -- helpers -----------------------------------------------------------

    def _sell(self, items, receipt_type=Order.ReceiptType.BOLETA, **kw):
        if receipt_type == Order.ReceiptType.FACTURA:
            kw.setdefault('customer', self.ruc_customer.pk)
        order, _ = _c1_sale(
            actor=self.seller, company=self.company, branch=self.branch,
            items=[{'product': p.pk, 'quantity': q} for p, q in items],
            payment_method=PaymentMethod.CARD, receipt_type=receipt_type, **kw,
        )
        Order.objects.filter(pk=order.pk).update(paid_at=_PAID_AT)
        order.refresh_from_db()
        return order

    def _fingerprint(self, order):
        data = _order_to_invoice_data(order, self.series[order.receipt_type], 7)
        lines = tuple(
            (str(ln.quantity), str(ln.unit_price), str(ln.unit_price_with_tax),
             str(ln.line_amount), str(ln.tax_amount), str(ln.tax_percent),
             tuple((a.reason_code, str(a.amount), str(a.base_amount)) for a in ln.allowances))
            for ln in data.lines
        )
        allowances = tuple(
            (a.reason_code, str(a.amount), str(a.base_amount)) for a in data.allowances)
        digest = hashlib.sha256(builder.build_invoice_xml(data)).hexdigest()
        promo = None
        if order.discount_source == 'promotion':
            items = list(order.items.select_related('product').order_by('pk'))
            promo = tuple(str(d) for d in _promotion_line_discounts(order, items))
        return promo, lines, allowances, digest

    def _check(self, name, order):
        got = self._fingerprint(order)
        if name not in PINNED:
            self.fail(f'UNPINNED {name!r}: {got!r}')
        self.assertEqual(got, PINNED[name])

    # -- promotions (Catálogo 53, code 00, per line) ------------------------

    def test_fixed_combo_on_a_boleta(self):
        _c13_combo(self.company, [(self.case, 1), (self.glass, 1)], fixed_price='100.00')
        self._check('fixed_combo_boleta', self._sell([(self.case, 1), (self.glass, 1)]))

    def test_percent_combo_on_a_factura(self):
        _c13_combo(self.company, [(self.case, 1), (self.glass, 1)], percent='10.00')
        self._check('percent_combo_factura', self._sell(
            [(self.case, 1), (self.glass, 1)], Order.ReceiptType.FACTURA))

    def test_multiple_applications(self):
        _c13_combo(self.company, [(self.case, 2), (self.glass, 1)], fixed_price='200.00')
        self._check('multiple_applications', self._sell([(self.case, 4), (self.glass, 2)]))

    def test_two_promotions_and_an_untouched_line(self):
        _c13_combo(self.company, [(self.case, 1), (self.glass, 1)], fixed_price='100.00', name='A')
        _c13_combo(self.company, [(self.phone, 1)], percent='10.00', name='B')
        self._check('two_promotions_mixed', self._sell(
            [(self.phone, 1), (self.case, 1), (self.glass, 1), (self.cable, 2)],
            Order.ReceiptType.FACTURA))

    def test_a_partially_promoted_line(self):
        _c13_combo(self.company, [(self.case, 1), (self.glass, 1)], fixed_price='100.00')
        self._check('partial_line', self._sell([(self.case, 2), (self.glass, 1), (self.cable, 1)]))

    def test_cent_remainders_across_three_lines(self):
        _c13_combo(self.company, [(self.phone, 1), (self.case, 1), (self.glass, 1)],
                   fixed_price='3000.00')
        self._check('cent_remainders', self._sell(
            [(self.phone, 1), (self.case, 1), (self.glass, 1)], Order.ReceiptType.FACTURA))

    def test_odd_prices_and_an_odd_combo_price(self):
        _c13_combo(self.company, [(self.case, 1), (self.cable, 1), (self.odd, 3)],
                   fixed_price='99.99')
        self._check('odd_rounding', self._sell(
            [(self.case, 1), (self.cable, 1), (self.odd, 3), (self.glass, 1)]))

    def test_a_legacy_snapshot_without_attribution(self):
        _c13_combo(self.company, [(self.case, 1), (self.glass, 1)], fixed_price='100.00')
        order = self._sell([(self.case, 1), (self.glass, 1)])
        row = AppliedPromotion.objects.get(order=order)
        legacy = [{k: v for k, v in c.items() if k not in ('regular_amount', 'discount_amount')}
                  for c in row.metadata['components']]
        AppliedPromotion.objects.filter(pk=row.pk).update(metadata={'components': legacy})
        self._check('legacy_snapshot', order)

    # -- global discounts and no discount (the promotion share is not used) --

    def test_a_coupon_on_a_factura(self):
        Coupon.objects.create(company=self.company, code='DIEZ', discount_percent=10)
        self._check('coupon_factura', self._sell(
            [(self.case, 1), (self.glass, 1)], Order.ReceiptType.FACTURA, coupon_code='DIEZ'))

    def test_a_manual_amount_on_a_boleta(self):
        self._check('manual_boleta', self._sell(
            [(self.case, 1), (self.glass, 1)],
            may_apply_manual_discount=True, manual_discount_type='amount',
            manual_discount_value='20.00', discount_reason='Rayón en la caja'))

    def test_no_discount(self):
        self._check('no_discount', self._sell([(self.case, 1), (self.odd, 2)]))

    # -- fail closed: the same error, word for word -------------------------

    def _promo_order(self):
        _c13_combo(self.company, [(self.case, 1), (self.glass, 1)], fixed_price='100.00',
                   name='Combo Cerrado')
        return self._sell([(self.case, 1), (self.glass, 1)])

    def _raises(self, order):
        with self.assertRaises(FiscalError) as caught:
            _order_to_invoice_data(order, self.series[order.receipt_type], 7)
        return str(caught.exception)

    def test_error_without_an_applied_promotion(self):
        order = self._promo_order()
        AppliedPromotion.objects.filter(order=order).delete()
        self.assertEqual(self._raises(order), (
            'La venta declara un descuento por promoción automática y no conserva '
            'ninguna promoción aplicada. Sin ese snapshot no hay a qué atribuir '
            'la rebaja.'))

    def test_error_when_the_applied_rows_do_not_add_up(self):
        order = self._promo_order()
        AppliedPromotion.objects.filter(order=order).update(discount_amount=Decimal('49.99'))
        self.assertEqual(
            self._raises(order),
            'Las promociones aplicadas suman 49.99 de descuento y la venta declara 50.00.')

    def _tamper(self, order, index, regular_total, **changes):
        """Edit one frozen component COHERENTLY, so only the fiscal check can catch it."""
        row = AppliedPromotion.objects.get(order=order)
        components = [dict(c) for c in row.metadata['components']]
        components[index].update(changes)
        AppliedPromotion.objects.filter(pk=row.pk).update(
            metadata={'components': components}, regular_amount=Decimal(regular_total))

    def test_error_when_the_frozen_price_is_not_the_line_price(self):
        order = self._promo_order()
        self._tamper(order, 0, '149.00', unit_price='99.00', regular_amount='99.00')
        self.assertEqual(self._raises(order), (
            f'La promoción congeló el producto {self.case.pk} a 99.00 y la línea de '
            f'la venta lo vendió a 100.00.'))

    def test_error_when_the_component_cannot_be_explained(self):
        order = self._promo_order()
        AppliedPromotion.objects.filter(order=order).update(metadata={'components': []})
        self.assertEqual(self._raises(order), (
            'La promoción «Combo Cerrado» de esta venta no se puede explicar '
            'componente a componente: Una promoción sin componentes no se puede '
            'explicar: no hay a qué atribuir su descuento.'))

    def test_error_when_a_product_is_not_among_the_lines(self):
        order = self._promo_order()
        self._tamper(order, 1, '130.00', product_id=self.cable.pk, unit_price='30.00',
                     regular_amount='30.00')
        self.assertEqual(self._raises(order), (
            f'La promoción rebaja el producto {self.cable.pk}, que no está entre las '
            f'líneas de esta venta.'))

    def test_error_when_more_units_are_consumed_than_sold(self):
        order = self._promo_order()
        self._tamper(order, 0, '350.00', quantity_used=3, regular_amount='300.00')
        self.assertEqual(self._raises(order), (
            f'La promoción consume 3 unidad(es) del producto {self.case.pk} y la '
            f'venta sólo tiene 1.'))

    def test_error_when_the_product_belongs_to_another_company(self):
        order = self._promo_order()
        other = _p3_company('ds-ajena-producto', 'Empresa Ajena Producto', tax_id='20100077703')
        Product.objects.filter(pk=self.case.pk).update(company=other)
        self.assertEqual(self._raises(order), (
            f'El producto {self.case.pk} de la promoción no pertenece a la empresa de '
            f'la venta.'))

    def test_error_when_the_promotion_belongs_to_another_company(self):
        order = self._promo_order()
        other = _p3_company('ds-ajena', 'Empresa Ajena', tax_id='20100077702')
        Promotion.objects.filter(applications__order=order).update(company=other)
        self.assertEqual(
            self._raises(order),
            'La promoción «Combo Cerrado» no pertenece a la empresa de la venta.')


# ---------------------------------------------------------------------------
# The pure function itself: its contract, without a database.
# ---------------------------------------------------------------------------

from django.test import SimpleTestCase  # noqa: E402

from .promotion_services import (  # noqa: E402
    FrozenPromotion, PromotionLineDiscount, PromotionLineShareError, SoldLine,
    share_promotion_discount,
)


def _attributed(product_id, quantity, unit_price, discount):
    regular = (Decimal(unit_price) * quantity).quantize(Decimal('0.01'))
    return {'product_id': product_id, 'quantity_used': quantity, 'unit_price': unit_price,
            'regular_amount': str(regular), 'discount_amount': discount}


class SharePromotionDiscountContractTest(SimpleTestCase):
    def setUp(self):
        self.lines = (
            SoldLine(product_id=1, product_company_id=9, quantity=1, unit_price=Decimal('100.00')),
            SoldLine(product_id=2, product_company_id=9, quantity=2, unit_price=Decimal('50.00')),
            SoldLine(product_id=3, product_company_id=9, quantity=1, unit_price=Decimal('30.00')),
        )
        self.combo = FrozenPromotion(
            name='Combo', company_id=9, regular_amount=Decimal('150.00'),
            discount_amount=Decimal('50.00'),
            components=[_attributed(1, 1, '100.00', '33.33'), _attributed(2, 1, '50.00', '16.67')],
        )

    def test_one_result_per_line_in_line_order(self):
        result = share_promotion_discount(
            company_id=9, order_discount=Decimal('50.00'), lines=self.lines, applied=[self.combo])

        self.assertEqual(result, (
            PromotionLineDiscount(product_id=1, quantity=1, regular_unit_price=Decimal('100.00'),
                                  regular_amount=Decimal('100.00'), discount_amount=Decimal('33.33'),
                                  promotion_names=('Combo',)),
            PromotionLineDiscount(product_id=2, quantity=2, regular_unit_price=Decimal('50.00'),
                                  regular_amount=Decimal('100.00'), discount_amount=Decimal('16.67'),
                                  promotion_names=('Combo',)),
            PromotionLineDiscount(product_id=3, quantity=1, regular_unit_price=Decimal('30.00'),
                                  regular_amount=Decimal('30.00'), discount_amount=Decimal('0.00'),
                                  promotion_names=()),
        ))

    def test_what_the_sales_note_will_read(self):
        result = share_promotion_discount(
            company_id=9, order_discount=Decimal('50.00'), lines=self.lines, applied=[self.combo])

        self.assertEqual(
            [line.for_sales_note() for line in result],
            [
                {'regular_unit_price': Decimal('100.00'), 'discount_amount': Decimal('33.33'),
                 'discount_reason': 'Combo'},
                {'regular_unit_price': Decimal('50.00'), 'discount_amount': Decimal('16.67'),
                 'discount_reason': 'Combo'},
                {'regular_unit_price': Decimal('30.00'), 'discount_amount': Decimal('0.00'),
                 'discount_reason': ''},
            ])
        self.assertEqual(result[0].amount_after_discount, Decimal('66.67'))

    def test_two_promotions_on_one_line_are_both_named_once(self):
        other = FrozenPromotion(
            name='Otra', company_id=9, regular_amount=Decimal('50.00'),
            discount_amount=Decimal('5.00'), components=[_attributed(2, 1, '50.00', '5.00')])
        again = FrozenPromotion(
            name='Combo', company_id=9, regular_amount=Decimal('100.00'),
            discount_amount=Decimal('10.00'), components=[_attributed(1, 1, '100.00', '10.00')])
        lines = (
            SoldLine(product_id=1, product_company_id=9, quantity=2, unit_price=Decimal('100.00')),
            SoldLine(product_id=2, product_company_id=9, quantity=2, unit_price=Decimal('50.00')),
        )

        result = share_promotion_discount(
            company_id=9, order_discount=Decimal('65.00'), lines=lines,
            applied=[self.combo, other, again])

        self.assertEqual([r.discount_amount for r in result], [Decimal('43.33'), Decimal('21.67')])
        self.assertEqual([r.for_sales_note()['discount_reason'] for r in result],
                         ['Combo', 'Combo, Otra'])

    def test_it_fails_closed_with_its_own_error(self):
        with self.assertRaises(PromotionLineShareError):
            share_promotion_discount(
                company_id=9, order_discount=Decimal('49.99'), lines=self.lines,
                applied=[self.combo])
        with self.assertRaises(PromotionLineShareError):
            share_promotion_discount(
                company_id=9, order_discount=Decimal('50.00'), lines=self.lines, applied=[])

    def test_it_does_not_touch_its_inputs(self):
        before = [dict(c) for c in self.combo.components]
        share_promotion_discount(
            company_id=9, order_discount=Decimal('50.00'), lines=self.lines, applied=[self.combo])
        self.assertEqual(list(self.combo.components), before)
