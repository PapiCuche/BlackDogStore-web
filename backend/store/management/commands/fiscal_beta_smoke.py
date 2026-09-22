"""
Prueba de humo controlada contra SUNAT BETA — OPT-IN, NUNCA en CI.

POR QUÉ EXISTE, Y POR QUÉ ES UN COMANDO Y NO UN TEST (ERP-FISCAL-3 §8-§10, §55)
------------------------------------------------------------------------------
La prueba de humo de ERP-FISCAL-2 envió DOS documentos a SUNAT BETA dentro de una
transacción que luego se DESHIZO, y ambos salieron como `F001-1`. Esa fue una
deuda del mecanismo de prueba (TEST-HARNESS-01), no un fallo de emisión:

    UNA TRANSACCIÓN DE BASE DE DATOS NO PUEDE DESHACER UN EFECTO YA OCURRIDO EN
    SUNAT. El `rollback` revierte NUESTRAS filas; el documento que SUNAT recibió
    sigue recibido. Usar `rollback` como «limpieza» de identidad fiscal es fingir
    que la llamada externa no pasó — y reutilizar el mismo `F001-1` una y otra vez.

Por eso esto:

- Es un COMANDO, no un `TestCase`: `manage.py test` no lo ejecuta. CI no habla con
  SUNAT jamás.
- PERSISTE todo localmente y NO revierte nada: el correlativo lo entrega la serie
  BETA real, es monótono y NO se recicla, así que CADA ejecución usa un
  identificador ÚNICO. No hay más `F001-1` perpetuo.
- Está APAGADO por defecto: exige `FISCAL_BETA_SMOKE_ENABLED=true`. Encenderlo es
  una decisión explícita del operador, no un descuido.
- Sólo BETA: `resolve_environment()` está fijado en pruebas; si algún día apunta a
  otro sitio, este comando se niega.
- No imprime secretos: ni Clave SOL, ni la clave del certificado, ni el sobre SOAP.

NO es la prueba de que «todo funciona»: es la única forma honesta de tocar SUNAT
de verdad sin ensuciar la base con identidades irreales ni mentir sobre lo que se
puede deshacer.
"""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from ...fiscal_config import fiscal_enabled, resolve_credentials, resolve_provider
from ...fiscal_services import (
    get_or_create_fiscal_document, sign_fiscal_document, submit_fiscal_document,
)
from ...models import (
    Company, FiscalDocumentType, FiscalEnvironment, FiscalSeries, Order,
    OrderItem, Product,
)


class Command(BaseCommand):
    help = ('Prueba de humo contra SUNAT BETA. --mode factura (por defecto) o '
            '--mode boleta-summary (boleta + resumen diario). Opt-in '
            '(FISCAL_BETA_SMOKE_ENABLED=true), sólo BETA, nunca en CI.')

    def add_arguments(self, parser):
        parser.add_argument('--company-tax-id', required=True,
                            help='RUC de una empresa YA existente con serie BETA.')
        parser.add_argument('--mode', choices=['factura', 'boleta-summary'],
                            default='factura')
        parser.add_argument('--price', default='118.00')
        parser.add_argument('--qty', type=int, default=1)
        parser.add_argument('--customer-ruc', default='20100066603')
        parser.add_argument('--customer-dni', default='12345678',
                            help='DNI del adquirente para la boleta (§66).')
        parser.add_argument('--poll-attempts', type=int, default=6)
        parser.add_argument('--poll-seconds', type=int, default=10)

    def handle(self, *args, **options):
        # 1) Puerta explícita. Apagado por defecto (§10).
        if not bool(getattr(settings, 'FISCAL_BETA_SMOKE_ENABLED', False)):
            raise CommandError(
                'FISCAL_BETA_SMOKE_ENABLED no está activo. Esta prueba habla con '
                'SUNAT de verdad y persiste lo que envía; se enciende a propósito.')
        if not fiscal_enabled():
            raise CommandError('La emisión fiscal está deshabilitada (FISCAL_ENABLED).')

        # 2) Sólo BETA. resolve_environment falla cerrado ante cualquier otra cosa.
        from ...fiscal_config import resolve_environment
        self.environment = resolve_environment()  # levanta si no es BETA

        try:
            self.company = Company.objects.get(tax_id=options['company_tax_id'])
        except Company.DoesNotExist:
            raise CommandError(
                f'No existe una empresa con RUC {options["company_tax_id"]}.')

        if options['mode'] == 'boleta-summary':
            self._run_boleta_summary(options)
        else:
            self._run_factura(options)

    def _paid_order(self, *, receipt_type, price, qty, doc_type, doc_number, name):
        gross = (Decimal(price) * qty).quantize(Decimal('0.01'))
        taxable = (gross / Decimal('1.18')).quantize(Decimal('0.01'))
        tax = gross - taxable
        product = Product.objects.create(
            company=self.company, name='ARTICULO DE PRUEBA BETA',
            slug=f'beta-smoke-{timezone.now().timestamp()}',
            price=Decimal(price), inventory=qty)
        order = Order.objects.create(
            company=self.company, customer_name=name,
            document_type=doc_type, document_number=doc_number,
            receipt_type=receipt_type,
            total=gross, discount_amount=Decimal('0.00'), subtotal_amount=gross,
            taxable_amount=taxable, tax_amount=tax, tax_rate=Decimal('0.18'),
            tax_treatment='taxed', currency='PEN',
            status=Order.Status.PAID, paid=True, paid_at=timezone.now(),
            fulfillment_branch=self.company.default_inventory_branch)
        OrderItem.objects.create(order=order, product=product,
                                 quantity=qty, price=Decimal(price))
        return order

    def _run_factura(self, options):
        if not FiscalSeries.objects.filter(
                company=self.company, document_type=FiscalDocumentType.INVOICE,
                environment=FiscalEnvironment.BETA, is_active=True).exists():
            raise CommandError('La empresa no tiene una serie de factura BETA activa.')

        order = self._paid_order(
            receipt_type=Order.ReceiptType.FACTURA, price=options['price'],
            qty=options['qty'], doc_type=Order.DocumentType.RUC,
            doc_number=options['customer_ruc'], name='CLIENTE DE PRUEBA SAC')

        # 4) Emitir → firmar → enviar. TODO se PERSISTE; NADA se revierte. El
        #    correlativo lo entrega la serie y es único: no hay F001-1 perpetuo.
        from ...fiscal_services import (
            get_or_create_fiscal_document, sign_fiscal_document, submit_fiscal_document,
        )
        document = self._issue_and_send(order)
        self._report_document(document)
        self.stdout.write(
            'Nota: este documento y su intento QUEDAN en la base. Un envío a SUNAT '
            'no se deshace con un rollback; el correlativo está gastado.')

    def _issue_and_send(self, order):
        document, _ = get_or_create_fiscal_document(order)
        creds = resolve_credentials(self.company)
        document = sign_fiscal_document(
            document, key_pem=creds['key_pem'], cert_pem=creds['cert_pem'])
        self.stdout.write(
            f'FIRMADO {document.document_id} (ambiente {self.environment}) '
            f'base={document.taxable_amount} IGV={document.tax_amount} '
            f'total={document.total}')
        return submit_fiscal_document(document, resolve_provider(self.company))

    def _report_document(self, document):
        self.stdout.write(self.style.SUCCESS(
            f'ENVIADO {document.document_id}\n'
            f'  estado    = {document.status}\n'
            f'  codigo    = {document.sunat_response_code!r}\n'
            f'  mensaje   = {document.sunat_response_message!r}\n'
            f'  cdr       = {"sí" if document.cdr_xml else "no"}'
            f' (sha256 {document.cdr_sha256 or "-"})'))

    def _run_boleta_summary(self, options):
        import time

        from ...fiscal_summary_services import (
            generate_daily_summaries, poll_daily_summary, sign_daily_summary,
            submit_daily_summary,
        )
        from ...models import FiscalSummaryStatus

        if not FiscalSeries.objects.filter(
                company=self.company, document_type=FiscalDocumentType.RECEIPT,
                environment=FiscalEnvironment.BETA, is_active=True).exists():
            raise CommandError('La empresa no tiene una serie de boleta BETA activa.')

        # 1) Boleta con receptor identificado (§66): se emite, firma y envía por
        #    resumen. NO se considera «aceptada» individualmente (§68).
        order = self._paid_order(
            receipt_type=Order.ReceiptType.BOLETA, price=options['price'],
            qty=options['qty'], doc_type=Order.DocumentType.DNI,
            doc_number=options['customer_dni'], name='CLIENTE DE PRUEBA')
        boleta, _ = get_or_create_fiscal_document(order)
        creds = resolve_credentials(self.company)
        boleta = sign_fiscal_document(
            boleta, key_pem=creds['key_pem'], cert_pem=creds['cert_pem'])
        self.stdout.write(
            f'BOLETA FIRMADA {boleta.document_id} total={boleta.total}')

        # 2) Resumen diario para hoy, firmado.
        reference_date = timezone.localdate()
        summaries = generate_daily_summaries(
            self.company, reference_date, self.environment)
        provider = resolve_provider(self.company)
        for summary in summaries:
            summary = sign_daily_summary(
                summary, key_pem=creds['key_pem'], cert_pem=creds['cert_pem'])
            self.stdout.write(f'RESUMEN FIRMADO {summary.identifier} '
                              f'({summary.lines.count()} línea/s)')

            # 3) sendSummary → ticket (persistido antes de consultar).
            summary = submit_daily_summary(summary, provider)
            self.stdout.write(
                f'  enviado: estado={summary.status} ticket={summary.ticket!r}')

            # 4) Polling ACOTADO del ticket (§45/§67): backoff simple, tope de
            #    intentos. No se sostiene ninguna transacción durante la espera.
            attempts = options['poll_attempts']
            for i in range(attempts):
                if summary.status != FiscalSummaryStatus.SUBMITTED:
                    break
                time.sleep(options['poll_seconds'])
                result = poll_daily_summary(summary, provider)
                summary = result.summary
                self.stdout.write(
                    f'  consulta {i + 1}/{attempts}: {result.action} '
                    f'estado={summary.status} codigo={summary.sunat_response_code!r}')

            self.stdout.write(self.style.SUCCESS(
                f'RESUMEN {summary.identifier}\n'
                f'  estado  = {summary.status}\n'
                f'  codigo  = {summary.sunat_response_code!r}\n'
                f'  mensaje = {summary.sunat_response_message!r}\n'
                f'  cdr     = {"sí" if summary.cdr_xml else "no"}'
                f' (sha256 {summary.cdr_sha256 or "-"})'))
        self.stdout.write(
            'Nota: boleta, resumen, ticket y CDR QUEDAN en la base. La aceptación '
            'es del RESUMEN, no de la boleta individual (§68).')
