"""
OPS-STATUS: what needs a person, in one read-only pass.

    python manage.py ops_status

One line per thing looked at: `OK`, `NOTA` (worth knowing, an ordinary day) or
`ATENCIÓN`. Exit status 1 only when a line says ATENCIÓN: a scheduler can run it
and send those.

It reads counts. It writes nothing, and no line carries a customer, a phone, an
order number or a token: its output goes to a log and to whoever is on call.

What it cannot see — whether the containers are up, whether the site answers,
whether last night's backup exists — is `deploy/healthcheck.sh`, which runs on
the host and calls this.
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import connection
from django.db.models import Q
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

from store.integrations import health as integration_health
from store.integrations import registry
from store.models import ConversionDelivery, NotificationDelivery, PaymentTransaction

#: How far back a failure is still news.
RECENT = timedelta(hours=24)
#: A buyer finishes a card form in minutes. A payment still waiting after this
#: long is one whose notification may never have arrived.
PAYMENT_WAIT = timedelta(minutes=45)
#: The sender runs every minute. A message due this long ago means it does not.
WHATSAPP_WAIT = timedelta(minutes=15)
#: This many final failures with not one message delivered is not one customer's
#: number: it is the token, the sender or the templates.
SYSTEMIC_FAILURES = 3
#: The conversion sender runs every five minutes; the longest wait between two
#: attempts is scheduled, and is not this.
CONVERSION_WAIT = timedelta(minutes=30)


def _provider_label(provider_id: str) -> str:
    provider = registry.get(provider_id)
    return provider.label if provider is not None else provider_id


def pending_migrations() -> list:
    executor = MigrationExecutor(connection)
    plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
    return [f'{migration.app_label}.{migration.name}' for migration, _backwards in plan]


def _plural(count: int, one: str, many: str) -> str:
    return f'{count} {one if count == 1 else many}'


class Command(BaseCommand):
    help = 'Lo que necesita atención: migraciones, pagos, avisos de WhatsApp, conversiones e integraciones. Sólo lee.'

    def handle(self, *args, **options):
        now = timezone.now()
        problems = 0

        def report(area: str, findings: list, all_right: str, notes=()):
            nonlocal problems
            for note in notes:
                self.stdout.write(f'NOTA  {area}: {note}')
            if not findings:
                if not notes:
                    self.stdout.write(f'OK    {area}: {all_right}')
                return
            problems += len(findings)
            for finding in findings:
                self.stdout.write(f'ATENCIÓN {area}: {finding}')

        unapplied = pending_migrations()
        report('migraciones', [
            f'{len(unapplied)} sin aplicar ({", ".join(unapplied[:3])}{"…" if len(unapplied) > 3 else ""}). '
            'Aplícalas con `migrate` antes de seguir.'
        ] if unapplied else [], 'todas aplicadas')

        payments = []
        tampered = PaymentTransaction.objects.filter(
            status=PaymentTransaction.Status.INTEGRITY_FAILED, created_at__gte=now - RECENT,
        ).count()
        if tampered:
            payments.append(
                f'{_plural(tampered, "notificación", "notificaciones")} de pago no '
                f'{"coincidía" if tampered == 1 else "coincidían"} con lo que la tienda abrió, en las últimas 24 h. '
                'Revisa el registro de auditoría.'
            )
        # A payment still waiting is NOT an alarm. Every checkout opens one, and a
        # buyer who closes the card form leaves it waiting for ever; from here
        # that cannot be told apart from a notification that never arrived
        # (PAY-RECONCILE). It is said, for the day a customer claims to have paid.
        payment_notes = []
        waiting = PaymentTransaction.objects.filter(
            status=PaymentTransaction.Status.PENDING,
            created_at__lt=now - PAYMENT_WAIT, created_at__gte=now - RECENT,
        ).count()
        if waiting:
            payment_notes.append(
                f'{_plural(waiting, "pago", "pagos")} sin respuesta de la pasarela en las últimas 24 h. '
                'Lo habitual es un comprador que no terminó; si alguien dice que pagó, '
                'compruébalo en el panel de la pasarela: la tienda no la consulta por su cuenta.'
            )
        report('pagos', payments, 'sin notificaciones rechazadas ni pagos esperando', payment_notes)

        whatsapp, whatsapp_notes = [], []
        deliveries = NotificationDelivery.objects.filter(channel=NotificationDelivery.Channel.WHATSAPP)
        recent = deliveries.filter(created_at__gte=now - RECENT)
        failed = recent.filter(
            status=NotificationDelivery.Status.FAILED, next_attempt_at__isnull=True,
        ).count()
        got_through = recent.filter(status__in=NotificationDelivery.DONE_STATUSES).count()
        if failed >= SYSTEMIC_FAILURES and got_through == 0:
            # Not one customer's number: nothing is getting through.
            whatsapp.append(
                f'{_plural(failed, "mensaje", "mensajes")} sin entregar en las últimas 24 h y ninguno '
                'entregado. Revisa el token, el número y las plantillas en Administración › Mensajería.'
            )
        elif failed:
            whatsapp_notes.append(
                f'{_plural(failed, "mensaje", "mensajes")} no se pudo entregar en las últimas 24 h '
                'y no se reintentará solo. El motivo está en cada orden.'
            )
        # A message nobody has tried to send has no next attempt scheduled: the
        # sender finds it by its age. One that failed and can be retried has one.
        overdue = deliveries.filter(
            Q(status=NotificationDelivery.Status.PENDING, created_at__lt=now - WHATSAPP_WAIT)
            | Q(status=NotificationDelivery.Status.FAILED, next_attempt_at__lt=now - WHATSAPP_WAIT)
        ).count()
        if overdue:
            whatsapp.append(
                f'{_plural(overdue, "mensaje", "mensajes")} sin enviar desde hace más de '
                f'{int(WHATSAPP_WAIT.total_seconds() // 60)} minutos. '
                '¿Se está ejecutando `send_pending_notifications` cada minuto?'
            )
        report('WhatsApp', whatsapp, 'sin mensajes fallidos ni atrasados', whatsapp_notes)

        # ANALYTICS-MARKETING. The purchase conversions the server sends to Google,
        # Meta and TikTok. The kind of failure and the provider, never an order.
        conversions, conversion_notes = [], []
        sales = ConversionDelivery.objects.filter(created_at__gte=now - RECENT)
        refused = sales.filter(status=ConversionDelivery.Status.FAILED)
        if refused.exists():
            count = refused.count()
            if count >= SYSTEMIC_FAILURES and not sales.filter(status=ConversionDelivery.Status.SENT).exists():
                conversions.append(
                    f'{count} conversiones de compra sin enviar en las últimas 24 h y ninguna enviada. '
                    'Revisa los tokens en Configuración › Integraciones › Analítica y marketing.')
            else:
                kinds = sorted({f'{_provider_label(row["provider"])}: {row["failure_kind"] or "error"}'
                                for row in refused.values('provider', 'failure_kind')})
                conversion_notes.append(
                    f'{_plural(count, "conversión de compra no se pudo enviar", "conversiones de compra no se pudieron enviar")} '
                    f'en las últimas 24 h ({", ".join(kinds)}). La venta no se ve afectada.')
        waiting = ConversionDelivery.objects.filter(
            Q(status=ConversionDelivery.Status.PENDING, next_attempt_at__isnull=True, created_at__lt=now - CONVERSION_WAIT)
            | Q(status=ConversionDelivery.Status.PENDING, next_attempt_at__lt=now - CONVERSION_WAIT)
        ).count()
        if waiting:
            conversions.append(
                f'{_plural(waiting, "conversión de compra", "conversiones de compra")} sin enviar desde hace más de '
                f'{int(CONVERSION_WAIT.total_seconds() // 60)} minutos. '
                '¿Se está ejecutando `send_pending_conversions`?')
        report('conversiones', conversions, 'sin conversiones fallidas ni atrasadas', conversion_notes)

        # What a master configured in Configuración › Integraciones, and the two
        # integrations a shop cannot work without. Labels and states, never values.
        findings, summary = integration_health.report()
        report('integraciones', findings, summary, [summary] if findings else [])

        if problems:
            raise SystemExit(1)
