"""
OPS-STATUS: what needs a person, in one read-only pass.

    python manage.py ops_status

One line per thing looked at. Exit status 0 when nothing needs attention, 1 when
something does: a scheduler can run it and send whatever it prints.

It reads counts. It writes nothing, and no line carries a customer, a phone, an
order number or a token: its output goes to a log and to whoever is on call.

What it cannot see — whether the containers are up, whether the site answers,
whether last night's backup exists — is `deploy/healthcheck.sh`, which runs on
the host and calls this.
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

from store.models import NotificationDelivery, PaymentTransaction

#: How far back a failure is still news.
RECENT = timedelta(hours=24)
#: A buyer finishes a card form in minutes. A payment still waiting after this
#: long is one whose notification may never have arrived.
PAYMENT_WAIT = timedelta(minutes=45)
#: The sender runs every minute. A message due this long ago means it does not.
WHATSAPP_WAIT = timedelta(minutes=15)


def pending_migrations() -> list:
    executor = MigrationExecutor(connection)
    plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
    return [f'{migration.app_label}.{migration.name}' for migration, _backwards in plan]


def _plural(count: int, one: str, many: str) -> str:
    return f'{count} {one if count == 1 else many}'


class Command(BaseCommand):
    help = 'Lo que necesita atención: migraciones, pagos y avisos de WhatsApp. Sólo lee.'

    def handle(self, *args, **options):
        now = timezone.now()
        problems = 0

        def report(area: str, findings: list, all_right: str):
            nonlocal problems
            if not findings:
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
        waiting = PaymentTransaction.objects.filter(
            status=PaymentTransaction.Status.PENDING,
            created_at__lt=now - PAYMENT_WAIT, created_at__gte=now - RECENT,
        ).count()
        if waiting:
            payments.append(
                f'{_plural(waiting, "pago", "pagos")} sin respuesta de la pasarela desde hace más de '
                f'{int(PAYMENT_WAIT.total_seconds() // 60)} minutos. Compruébalos en el panel de la pasarela: '
                'la tienda no la consulta por su cuenta.'
            )
        report('pagos', payments, 'sin notificaciones rechazadas ni pagos esperando')

        whatsapp = []
        deliveries = NotificationDelivery.objects.filter(channel=NotificationDelivery.Channel.WHATSAPP)
        failed = deliveries.filter(
            status=NotificationDelivery.Status.FAILED, next_attempt_at__isnull=True,
            created_at__gte=now - RECENT,
        ).count()
        if failed:
            whatsapp.append(
                f'{_plural(failed, "mensaje", "mensajes")} no se pudo entregar en las últimas 24 h '
                'y no se reintentará solo. El motivo está en cada orden.'
            )
        overdue = deliveries.filter(
            status__in=[NotificationDelivery.Status.PENDING, NotificationDelivery.Status.FAILED],
            next_attempt_at__lt=now - WHATSAPP_WAIT,
        ).count()
        if overdue:
            whatsapp.append(
                f'{_plural(overdue, "mensaje", "mensajes")} sin enviar desde hace más de '
                f'{int(WHATSAPP_WAIT.total_seconds() // 60)} minutos. '
                '¿Se está ejecutando `send_pending_notifications` cada minuto?'
            )
        report('WhatsApp', whatsapp, 'sin mensajes fallidos ni atrasados')

        if problems:
            raise SystemExit(1)
