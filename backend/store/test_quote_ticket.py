"""
QUOTE-TICKET — el ticket de una cotización aprobada.

Es el papel que se entrega en el mostrador cuando el cliente dijo que sí: qué
se va a hacer, cuánto cuesta, sobre qué equipo, por dónde aceptó y quién lo
atendió. Sale por la impresora de 80 mm con el mismo trazado que los demás
tickets.

    SÓLO DE UNA COTIZACIÓN APROBADA, Y LO DICE EL SERVIDOR.

Un borrador, una enviada sin respuesta, una rechazada o una reemplazada no
imprimen "APROBADA" porque alguien pulsó un botón. Y no es un comprobante de
pago: no se llama boleta ni factura en ninguna parte.
"""
from store.models import AdminAuditLog, Device, RepairQuote
from store.tests import M9ServiceBase as _Base, _m8_service, _m8_url, _pdf_text

IMEI_A = '490154203237518'


class QuoteTicketTest(_Base):
    def setUp(self):
        super().setUp()
        Device.objects.filter(pk=self.device.pk).update(imei=IMEI_A, serial_number='F2LXK1ABC9')
        self.quote = self.published_quote()
        self.client = self.with_capabilities('service.orders.view', slug='imprime')

    def url(self, quote=None, order=None, slug='m8-taller', query=''):
        return _m8_url(
            slug, f'orders/{(order or self.order).pk}/quotes/{(quote or self.quote).pk}/ticket/',
        ) + query

    def approve(self, channel='phone'):
        return _m8_service.record_staff_quote_decision(
            quote=self.quote, actor=self.staff, decision='approve', channel=channel,
            note='Aceptó por llamada.',
        )

    def test_an_approved_quote_prints_what_was_agreed(self):
        self.approve()

        res = self.client.get(self.url())

        self.assertEqual(res.status_code, 200, res.content[:200])
        self.assertEqual(res['Content-Type'], 'application/pdf')
        self.assertIn('inline', res['Content-Disposition'])
        self.assertIn(f'cotizacion-{self.order.number}-r{self.quote.revision}', res['Content-Disposition'])
        self.assertEqual(res['Cache-Control'], 'private, max-age=0, no-store')
        text = _pdf_text(res.content)
        for expected in ('Taller', 'APROBADA', self.order.number, 'Mano de obra', '120.00',
                         'Llamada', 'recepcion', self.customer.first_name, IMEI_A[-4:], 'ABC9'):
            self.assertIn(expected, text, expected)

    def test_identifiers_are_masked_and_nothing_internal_is_printed(self):
        RepairQuote.objects.filter(pk=self.quote.pk).update(internal_notes='MARGEN 60%')
        self.approve()

        text = _pdf_text(self.client.get(self.url()).content)

        for leaked in (IMEI_A, 'F2LXK1ABC9', 'MARGEN', 'Aceptó por llamada'):
            self.assertNotIn(leaked, text, leaked)

    def test_it_carries_the_shops_identity_like_every_other_ticket(self):
        """La misma cabecera que el ticket de venta, y la nota escrita PARA el cliente con su rótulo."""
        from store.models import RepairDiagnostic
        from store.test_document_design import keep, text_of

        RepairQuote.objects.filter(pk=self.quote.pk).update(
            customer_notes='Cambio de pantalla con repuesto original.',
            discount_amount='20.00', total=self.quote.total - 20)
        RepairDiagnostic.objects.filter(repair_order=self.order).update(
            description='DIAGNOSTICO-TECNICO-INTERNO', internal_notes='NOTA-INTERNA-TALLER')
        self.approve()

        text = text_of(keep('cotizacion-ticket80', self.client.get(self.url()).content))

        for expected in ('Taller', f'Sucursal: {self.order.branch.name}', 'COTIZACIÓN DE SERVICIO',
                         'Detalle del trabajo', 'Cambio de pantalla', 'Descuento', '20.00', 'Atendido por:'):
            self.assertIn(expected, text, expected)
        for internal in ('DIAGNOSTICO-TECNICO-INTERNO', 'NOTA-INTERNA-TALLER'):
            self.assertNotIn(internal, text)

    def test_it_never_calls_itself_a_fiscal_document(self):
        self.approve()
        text = _pdf_text(self.client.get(self.url()).content).upper()
        self.assertNotIn('BOLETA', text)
        self.assertNotIn('FACTURA', text)
        self.assertIn('NO ES UN COMPROBANTE DE PAGO', text)

    def test_only_an_approved_quote_has_a_ticket(self):
        self.assertEqual(self.client.get(self.url()).status_code, 400)          # enviada

        _m8_service.record_staff_quote_decision(
            quote=self.quote, actor=self.staff, decision='reject', channel='phone')
        self.assertEqual(self.client.get(self.url()).status_code, 400)          # rechazada

    def test_a_superseded_approval_no_longer_prints(self):
        self.approve()
        _m8_service.reopen_approved_quote(quote=self.quote, actor=self.staff, reason='Cambió el repuesto.')

        res = self.client.get(self.url())

        self.assertEqual(res.status_code, 400)
        self.assertIn('aprobada', res.json()['detail'])

    def test_an_approval_from_the_customer_says_so(self):
        _m8_service.record_quote_decision(
            quote=self.quote, customer=self.customer, user=self.client_user, decision='approve')
        text = _pdf_text(self.client.get(self.url()).content)
        self.assertIn('Cuenta del cliente', text)

    def test_printing_reads_and_needs_the_order_to_be_visible(self):
        self.approve()
        before = AdminAuditLog.objects.count()

        self.assertEqual(self.client.get(self.url()).status_code, 200)
        # Imprimir es un GET: en la superficie interna una lectura no escribe.
        # Lo que importa ya está registrado: quién aprobó, por dónde y quién lo anotó.
        self.assertEqual(AdminAuditLog.objects.count(), before)

        self.assertIn(self.client.get(self.url(slug='m8-otra')).status_code, (403, 404))
        self.order.branch = self.branch_b
        self.order.save(update_fields=['branch'])
        self.assertEqual(self.restrict_to_branch_a().get(self.url()).status_code, 404)

    def test_an_unknown_format_is_refused_instead_of_guessed(self):
        self.approve()
        self.assertEqual(self.client.get(self.url(query='?formato=ticket80')).status_code, 200)
        self.assertEqual(self.client.get(self.url(query='?formato=a4')).status_code, 400)

    def test_the_quote_of_another_order_is_not_found(self):
        self.approve()
        other = self.make_order()
        self.assertEqual(self.client.get(self.url(order=other)).status_code, 404)
