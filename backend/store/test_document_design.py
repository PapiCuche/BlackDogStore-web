"""
DOCUMENT-DESIGN — la nota de venta, su ticket, el comprobante de pedido y el
ticket de cotización, como papeles de UNA misma tienda.

Lo que estas pruebas fijan es lo que el papel DICE y de dónde lo saca:

  * la identidad es la de la empresa de la venta — nombre, razón social, RUC,
    dirección, sucursal, teléfono, correo y logotipo—, nunca una constante;
  * un equipo con serie imprime, bajo su línea, la serie y el IMEI del equipo
    REAL que salió del stock en esa venta, no un texto escrito a mano;
  * un producto sin serie no imprime identificadores;
  * totales, descuento, importe en letras y cantidad de productos salen de la
    venta;
  * una nota de venta interna NO es un comprobante SUNAT, y lo sigue diciendo.

Se lee el texto y la estructura del PDF. No se comparan bytes: un PDF lleva su
fecha de creación dentro y dos iguales nunca lo son.

Con `DOCUMENT_SAMPLES_DIR=<carpeta>` cada prueba deja ahí el PDF que generó,
para revisarlo con los ojos.
"""
import base64
import os
import re
import zlib
from decimal import Decimal
from pathlib import Path

from django.test import SimpleTestCase, override_settings

from store import inventory_services as inventory
from store import stock_unit_services as units
from store.models import CompanySettings, Order, OrderItem, Product, ProductBarcode
from store.pdf_services import generate_order_receipt_pdf
from store.sales_note_services import (
    SALES_NOTE_DISCLAIMER, build_sales_note_context, generate_sales_note_pdf,
    get_or_create_sales_note,
)
from store.test_fiscal_logo import _ROOT as LOGO_ROOT, embedded_images, logo_png
from store.test_stock_units import IMEI_A, IMEI_B, IMEI_C, UnitsBase
from store.tests import _prod
from store.ticket_services import generate_sales_note_ticket_pdf

A4 = (595.3, 841.9)
TICKET_WIDTH = 226.8            # 80 mm


def _literal(piece: bytes) -> str:
    """Una cadena `( … )` de un stream PDF, con sus escapes deshechos."""
    body = piece[1:-1]
    body = re.sub(rb'\\([0-7]{1,3})', lambda m: bytes([int(m.group(1), 8) & 0xFF]), body)
    body = body.replace(b'\\(', b'(').replace(b'\\)', b')').replace(b'\\\\', b'\\')
    # Un espacio de no separación se lee como lo que se ve: un espacio.
    return body.decode('latin-1').replace('\xa0', ' ')


def page_texts(pdf: bytes) -> list[str]:
    """El texto de cada página, en orden. Sin dependencias: Flate + ASCII85 y los operadores de texto."""
    pages = []
    for body in re.findall(rb'[^d]stream\r?\n(.*?)endstream', pdf, re.S):
        body = body.strip(b'\r\n')
        if body.endswith(b'~>'):
            try:
                body = base64.a85decode(body[:-2])
            except ValueError:
                continue
        try:
            body = zlib.decompress(body)
        except zlib.error:
            pass
        if b'BT' not in body:
            continue
        pages.append(' '.join(_literal(p) for p in re.findall(rb'\((?:\\.|[^\\()])*\)', body)))
    return pages


def text_of(pdf: bytes) -> str:
    return ' '.join(page_texts(pdf))


def media_box(pdf: bytes) -> tuple[float, float]:
    match = re.search(rb'/MediaBox \[ ?0 0 ([\d.]+) ([\d.]+) ?\]', pdf)
    return round(float(match.group(1)), 1), round(float(match.group(2)), 1)


def keep(name: str, pdf: bytes) -> bytes:
    folder = os.environ.get('DOCUMENT_SAMPLES_DIR')
    if folder:
        Path(folder).mkdir(parents=True, exist_ok=True)
        Path(folder, f'{name}.pdf').write_bytes(pdf)
    return pdf


@override_settings(EVIDENCE_STORAGE_BACKEND='filesystem', EVIDENCE_STORAGE_ROOT=LOGO_ROOT)
class DocumentBase(UnitsBase):
    def setUp(self):
        super().setUp()
        CompanySettings.objects.update_or_create(company=self.company, defaults={
            'legal_address': 'Av. Los Olivos 123, Cercado', 'city': 'Arequipa',
            'phone': '054 555 000', 'contact_email': 'ventas@inventario.example',
            'warranty_policy_text': 'Garantía de 12 meses por defecto de fábrica.',
        })
        type(self.branch_a).objects.filter(pk=self.branch_a.pk).update(
            name='Tienda Centro', address='Calle Mercaderes 200', phone='054 555 111')
        self.branch_a.refresh_from_db()
        ProductBarcode.objects.create(company=self.company, product=self.phone, code='IP16-256-N', is_primary=True)
        self.case = _prod(self.company, 'Funda de silicona', 'funda-doc', price='79.90', inventory=0)
        ProductBarcode.objects.create(company=self.company, product=self.case, code='FUNDA-16', is_primary=True)
        inventory.create_stock_movement(
            branch=self.branch_a, product_id=self.case.pk, movement_type='initial_stock',
            quantity=50, reason='Inicial', actor=self.staff)

    def sale(self, lines, **order_fields):
        """Una venta pagada cuyas salidas de stock pasaron por el Kardex de verdad."""
        total = sum(product.price * quantity for product, quantity in lines)
        discount = Decimal(str(order_fields.pop('discount_amount', '0.00')))
        order = Order.objects.create(
            company=self.company, fulfillment_branch=self.branch_a,
            customer_name='María Fernanda Quispe', customer_email='maria@example.com',
            customer_phone='987 654 321', document_type=Order.DocumentType.DNI, document_number='40404040',
            delivery_method=Order.DeliveryMethod.PICKUP_STORE, receipt_type=Order.ReceiptType.BOLETA,
            total=total - discount, discount_amount=discount, status=Order.Status.PAID, paid=True,
            paid_at=self.now(), payment_method='card', sales_channel='pos', **order_fields,
        )
        for product, quantity in lines:
            OrderItem.objects.create(order=order, product=product, quantity=quantity, price=product.price)
        inventory.record_sale_stock_movements(order, strict=True)
        return order

    def now(self):
        from django.utils import timezone
        return timezone.now()

    def note_for(self, order):
        return get_or_create_sales_note(order, actor=self.staff)[0]

    def with_logo(self, company=None):
        from store import storefront_media
        company = company or self.company
        image = storefront_media.store(company=company, actor=self.staff, raw=logo_png(), audit=False)
        CompanySettings.objects.filter(company=company).update(
            document_logo_url=storefront_media.payload(image)['url'])


class SalesNoteA4Test(DocumentBase):
    def test_the_header_is_the_tenants_identity_and_the_document_is_named(self):
        self.receive(self.row('F2LXK1ABC1', IMEI_A))
        note = self.note_for(self.sale([(self.phone, 1)]))

        pdf = keep('nota-venta-a4', generate_sales_note_pdf(note))

        self.assertTrue(pdf.startswith(b'%PDF'))
        self.assertEqual(media_box(pdf), A4)
        text = text_of(pdf)
        for expected in (
            'Inventario', 'Inventario S.A.C.', '20800000001', 'Av. Los Olivos 123', 'Arequipa',
            'Tienda Centro', '054 555 000', 'ventas@inventario.example',
            'NOTA DE VENTA', note.number,
            'María Fernanda Quispe', '40404040', '987 654 321',
            'Tarjeta', 'Recojo en tienda',
        ):
            self.assertIn(expected, text, expected)
        self.assertIn(SALES_NOTE_DISCLAIMER.split('.')[0], text)
        self.assertIn('No es una serie fiscal', text)
        self.assertIn('Página 1 de 1', text)

    def test_the_table_has_the_commercial_columns_and_real_values(self):
        note = self.note_for(self.sale([(self.case, 3)]))

        text = text_of(keep('nota-venta-producto-simple', generate_sales_note_pdf(note)))

        for column in ('CÓDIGO', 'DESCRIPCIÓN', 'U.M.', 'P. LISTA', 'DSCTO.', 'P. UNIT.', 'CANT.', 'IMPORTE'):
            self.assertIn(column, text, column)
        for value in ('FUNDA-16', 'Funda de silicona', 'UND', '79.90', '239.70'):
            self.assertIn(value, text, value)
        # Un producto sin serie no imprime identificadores.
        self.assertNotIn('Serie:', text)
        self.assertNotIn('IMEI', text)

    def test_a_serialized_line_prints_the_unit_that_was_actually_sold(self):
        self.receive(self.row('F2LXK1ABC1', IMEI_A), self.row('F2LXK1ABC2', IMEI_B, imei2=IMEI_C))
        self.receive(self.row('NOVENDIDO99', None), product=self.laptop)
        # Lo que diga la descripción del producto no es la identidad del equipo.
        Product.objects.filter(pk=self.phone.pk).update(description='NS: INVENTADO-000 IMEI: 000000000000000')
        note = self.note_for(self.sale([(self.phone, 2), (self.case, 1)]))

        text = text_of(keep('nota-venta-equipos-serie', generate_sales_note_pdf(note)))

        self.assertIn('Serie: F2LXK1ABC1', text)
        self.assertIn(f'IMEI: {IMEI_A}', text)
        self.assertIn('Serie: F2LXK1ABC2', text)
        self.assertIn(f'IMEI: {IMEI_B}', text)
        self.assertIn(f'IMEI 2: {IMEI_C}', text)
        self.assertEqual(text.count('Serie:'), 2)
        for absent in ('INVENTADO-000', '000000000000000', 'NOVENDIDO99'):
            self.assertNotIn(absent, text)

    def test_the_identifiers_come_from_the_units_of_the_sale(self):
        self.receive(self.row('F2LXK1ABC1', IMEI_A))
        order = self.sale([(self.phone, 1)])

        line = build_sales_note_context(self.note_for(order))['items'][0]

        self.assertEqual(line['units'], [{'serial_number': 'F2LXK1ABC1', 'imei': IMEI_A, 'imei2': ''}])
        self.assertEqual(line['code'], 'IP16-256-N')
        self.assertEqual(line['line_number'], 1)

    def test_a_reprint_after_a_return_still_names_the_unit_it_named(self):
        self.receive(self.row('F2LXK1ABC1', IMEI_A))
        note = self.note_for(self.sale([(self.phone, 1)]))
        sold = self.phone.stock_units.get()
        units.return_unit(sold, reason='Devolución en garantía', actor=self.staff)

        self.assertIn('Serie: F2LXK1ABC1', text_of(generate_sales_note_pdf(note)))

    def test_totals_discount_words_and_count_come_from_the_sale(self):
        order = self.sale([(self.case, 2)], discount_amount='9.80')
        note = self.note_for(order)

        text = text_of(keep('nota-venta-descuento', generate_sales_note_pdf(note)))

        self.assertIn('159.80', text)                       # subtotal 2 × 79.90
        self.assertIn('Descuento', text)
        self.assertIn('9.80', text)
        self.assertIn('S/ 150.00', text)                    # total
        self.assertIn('CIENTO CINCUENTA CON 00/100 SOLES', text)
        self.assertIn('Productos: 1', text)
        self.assertIn('Unidades: 2', text)
        self.assertNotIn('PEN', text)

    def test_the_logo_is_the_tenants_and_a_note_without_one_still_prints(self):
        plain = self.note_for(self.sale([(self.case, 1)]))
        self.assertEqual(embedded_images(generate_sales_note_pdf(plain)), 0)

        self.with_logo()
        branded = self.note_for(self.sale([(self.case, 1)]))

        self.assertEqual(embedded_images(keep('nota-venta-logo', generate_sales_note_pdf(branded))), 1)
        self.assertEqual(embedded_images(keep('nota-venta-ticket-logo', generate_sales_note_ticket_pdf(branded))), 1)

    def test_a_note_keeps_the_logo_it_was_issued_with(self):
        self.with_logo()
        note = self.note_for(self.sale([(self.case, 1)]))
        CompanySettings.objects.filter(company=self.company).update(document_logo_url='')

        self.assertEqual(embedded_images(generate_sales_note_pdf(note)), 1)

    def test_many_lines_break_pages_without_orphaning_the_totals(self):
        lines = []
        for n in range(70):
            product = _prod(self.company, f'Accesorio de prueba número {n:02d}', f'acc-doc-{n}', price='10.00', inventory=0)
            inventory.create_stock_movement(
                branch=self.branch_a, product_id=product.pk, movement_type='initial_stock',
                quantity=5, reason='Inicial', actor=self.staff)
            lines.append((product, 1))
        note = self.note_for(self.sale(lines))

        pages = page_texts(keep('nota-venta-varias-paginas', generate_sales_note_pdf(note)))

        self.assertGreaterEqual(len(pages), 2)
        for number, page in enumerate(pages, start=1):
            self.assertIn(f'Página {number} de {len(pages)}', page)
            self.assertIn(note.number, page)                # cada página dice de qué documento es
        self.assertTrue(all('DESCRIPCIÓN' in page for page in pages[:-1]))
        with_total = [n for n, page in enumerate(pages) if 'IMPORTE TOTAL' in page]
        self.assertEqual(len(with_total), 1)
        last = pages[with_total[0]]
        for together in ('Subtotal', 'SON:', 'S/ 700.00'):
            self.assertIn(together, last)

    def test_long_texts_wrap_instead_of_leaving_the_page(self):
        name = 'MacBookProM4Max16Pulgadas1TBNegroEspacialConCargadorMagSafe140W' * 3
        product = _prod(self.company, name, 'largo-doc', price='10.00', inventory=0)
        inventory.create_stock_movement(
            branch=self.branch_a, product_id=product.pk, movement_type='initial_stock',
            quantity=5, reason='Inicial', actor=self.staff)
        order = self.sale([(product, 1)], notes='Entregar en recepción. ' * 40)
        Order.objects.filter(pk=order.pk).update(customer_name='Compañía Importadora y Exportadora ' * 4)
        order.refresh_from_db()
        note = self.note_for(order)

        for kind, pdf in (('a4', generate_sales_note_pdf(note)), ('ticket', generate_sales_note_ticket_pdf(note))):
            keep(f'nota-venta-textos-largos-{kind}', pdf)
            joined = text_of(pdf).replace(' ', '')
            self.assertIn('MagSafe140W', joined)
            self.assertIn('CargadorMag', joined)

    def test_another_company_prints_its_own_identity(self):
        other_branch = self.foreign_branch
        product = _prod(self.other, 'Cable ajeno', 'cable-ajeno-doc', price='20.00', inventory=0)
        inventory.create_stock_movement(
            branch=other_branch, product_id=product.pk, movement_type='initial_stock',
            quantity=5, reason='Inicial', actor=None)
        order = Order.objects.create(
            company=self.other, fulfillment_branch=other_branch, customer_name='Otro cliente',
            customer_email='otro@example.com', total=Decimal('20.00'), status=Order.Status.PAID,
            paid=True, paid_at=self.now())
        OrderItem.objects.create(order=order, product=product, quantity=1, price=product.price)
        self.with_logo()                                   # el logotipo es de la PRIMERA empresa

        pdf = generate_sales_note_pdf(self.note_for(order))

        text = text_of(pdf)
        self.assertIn('Ajena S.A.C.', text)
        self.assertIn('20800000002', text)
        for foreign in ('Inventario S.A.C.', '20800000001', 'Tienda Centro', 'ventas@inventario.example'):
            self.assertNotIn(foreign, text)
        self.assertEqual(embedded_images(pdf), 0)


    def test_the_delivery_label_names_the_tenants_city_not_a_fixed_one(self):
        order = self.sale([(self.case, 1)])
        Order.objects.filter(pk=order.pk).update(delivery_method=Order.DeliveryMethod.DELIVERY_AREQUIPA)
        CompanySettings.objects.filter(company=self.company).update(city='Cusco')
        order.refresh_from_db()

        text = text_of(generate_sales_note_pdf(self.note_for(order)))

        self.assertIn('Delivery Cusco', text)
        self.assertNotIn('Delivery Arequipa', text)


class SalesNoteTicketTest(DocumentBase):
    def test_the_ticket_is_80_mm_and_says_the_same_as_the_a4(self):
        self.receive(self.row('F2LXK1ABC1', IMEI_A, imei2=IMEI_B))
        note = self.note_for(self.sale([(self.phone, 1), (self.case, 2)], discount_amount='59.80'))

        pdf = keep('nota-venta-ticket80', generate_sales_note_ticket_pdf(note))

        width, height = media_box(pdf)
        self.assertAlmostEqual(width, TICKET_WIDTH, places=1)
        self.assertGreater(height, 200)
        text = text_of(pdf)
        for expected in (
            'Inventario', '20800000001', 'Tienda Centro', note.number, 'María Fernanda Quispe',
            'Serie: F2LXK1ABC1', f'IMEI: {IMEI_A}', f'IMEI 2: {IMEI_B}', 'IP16-256-N',
            'Tarjeta', 'Descuento', '59.80', 'S/ 4600.00', 'CUATRO MIL SEISCIENTOS CON 00/100 SOLES',
            'No es una serie fiscal', 'SUNAT',
        ):
            self.assertIn(expected, text, expected)
        self.assertEqual(text.count('Serie:'), 1)

    def test_the_thermal_print_carries_the_same_identifiers(self):
        from store.printing.escpos import sales_note_ticket

        self.receive(self.row('F2LXK1ABC1', IMEI_A))
        note = self.note_for(self.sale([(self.phone, 1)]))

        raw = sales_note_ticket(note).decode('cp858', 'replace')

        self.assertIn('Serie: F2LXK1ABC1', raw)
        self.assertIn(f'IMEI: {IMEI_A}', raw)
        self.assertIn('Tienda Centro', raw)


class OrderReceiptTest(DocumentBase):
    def test_the_order_receipt_shares_the_layout_and_claims_nothing_fiscal(self):
        self.receive(self.row('F2LXK1ABC1', IMEI_A))
        order = self.sale([(self.phone, 1), (self.case, 1)])
        self.with_logo()

        pdf = keep('comprobante-pedido-a4', generate_order_receipt_pdf(order))

        self.assertEqual(media_box(pdf), A4)
        self.assertEqual(embedded_images(pdf), 1)
        text = text_of(pdf)
        for expected in ('Inventario S.A.C.', '20800000001', 'Tienda Centro', f'#{order.pk}',
                         'DESCRIPCIÓN', 'IMPORTE TOTAL', 'Serie: F2LXK1ABC1', 'Página 1 de 1', 'SUNAT'):
            self.assertIn(expected, text, expected)
        for claim in ('BOLETA DE VENTA ELECTR', 'FACTURA ELECTR'):
            self.assertNotIn(claim, text)


class DocumentDatesTest(DocumentBase):
    def test_dates_are_printed_in_the_shops_time_not_in_utc(self):
        """
        DOC-TIMEZONE. The database keeps instants in UTC and the documents printed
        them as stored: a sale at 21:30 in Lima came out dated 02:30 of the next
        day, on the note, on its ticket and on the order receipt.
        """
        from datetime import datetime, timezone as dt_timezone

        from store.models import SalesNote
        from store.printing.escpos import sales_note_ticket

        evening_in_lima = datetime(2026, 3, 10, 2, 30, tzinfo=dt_timezone.utc)   # 09/03 21:30 en Lima
        order = self.sale([(self.case, 1)])
        Order.objects.filter(pk=order.pk).update(paid_at=evening_in_lima, created_at=evening_in_lima)
        order.refresh_from_db()
        note = self.note_for(order)
        SalesNote.objects.filter(pk=note.pk).update(issued_at=evening_in_lima)
        note.refresh_from_db()

        documents = {
            'la nota A4': text_of(generate_sales_note_pdf(note)),
            'el ticket': text_of(generate_sales_note_ticket_pdf(note)),
            'la salida térmica': sales_note_ticket(note).decode('cp858', 'replace'),
            'el comprobante de pedido': text_of(generate_order_receipt_pdf(order)),
        }
        for name, text in documents.items():
            self.assertIn('09/03/2026 21:30', text, name)
            self.assertNotIn('10/03/2026', text, name)


class SharedDesignTest(SimpleTestCase):
    def test_a_stamp_takes_whatever_the_models_hold(self):
        from datetime import date, datetime

        from store.document_style import local_stamp

        self.assertEqual(local_stamp(None), '—')
        self.assertEqual(local_stamp(date(2026, 3, 9), '%d/%m/%Y'), '09/03/2026')
        self.assertEqual(local_stamp(datetime(2026, 3, 9, 21, 30)), '09/03/2026 21:30')

    def test_amounts_are_spelled_the_way_a_peruvian_document_spells_them(self):
        from store.document_style import amount_in_words

        self.assertEqual(amount_in_words(Decimal('150.00'), 'PEN'), 'CIENTO CINCUENTA CON 00/100 SOLES')
        self.assertEqual(amount_in_words(Decimal('4600.50'), 'PEN'), 'CUATRO MIL SEISCIENTOS CON 50/100 SOLES')
        self.assertEqual(amount_in_words(Decimal('21.00'), 'PEN'), 'VEINTIUNO CON 00/100 SOLES')
        self.assertEqual(amount_in_words(Decimal('100.00'), 'USD'), 'CIEN CON 00/100 USD')

    def test_no_tenant_is_written_into_the_document_code(self):
        """Todo sale de Company, Branch, Order o la configuración: ninguna tienda vive en el código."""
        root = Path(__file__).resolve().parent
        for module in ('document_style.py', 'document_layout.py', 'sales_note_services.py',
                       'ticket_services.py', 'pdf_services.py', 'quote_ticket.py'):
            source = (root / module).read_text(encoding='utf-8').lower()
            for forbidden in ('black dog', 'blackdog', '936449536'):
                self.assertNotIn(forbidden, source, f'{module}: {forbidden}')
