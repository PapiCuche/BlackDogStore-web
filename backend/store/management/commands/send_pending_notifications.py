"""
Send the customer notices that have not gone out yet.

    python manage.py send_pending_notifications [--limit 200]

The first attempt of a WhatsApp message happens right after the transaction
that caused it. This is everything else: a message whose first attempt failed
and is due for another, and one left pending because the process died before
trying. Run it from a timer (every minute is enough); it is safe to run twice
at once, because each row is locked while it is sent.

It never sends a message twice and never raises for one bad row.
"""
from django.core.management.base import BaseCommand

from store import whatsapp_services as wa
from store.models import NotificationDelivery


class Command(BaseCommand):
    help = 'Envía los avisos por WhatsApp pendientes o por reintentar.'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=200)

    def handle(self, *args, **options):
        ids = list(wa.due().values_list('pk', flat=True)[: max(1, options['limit'])])
        sent = failed = 0
        for delivery_id in ids:
            try:
                row = wa.deliver(delivery_id)
            except Exception as exc:  # noqa: BLE001 — one row never stops the pass
                failed += 1
                self.stderr.write(f'entrega {delivery_id}: {type(exc).__name__}')
                continue
            if row.status in NotificationDelivery.DONE_STATUSES:
                sent += 1
            elif row.status == NotificationDelivery.Status.FAILED:
                failed += 1
        self.stdout.write(f'WhatsApp: {len(ids)} por enviar, {sent} enviados, {failed} fallidos.')
