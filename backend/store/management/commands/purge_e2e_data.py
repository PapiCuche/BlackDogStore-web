"""
Borra lo que dejan las pruebas de navegador — SÓLO DESARROLLO.

POR QUÉ EXISTE
--------------
Las pruebas E2E recorren la aplicación de verdad: dan de alta un cliente, le
registran un equipo, reciben una orden, la asignan y suben una foto. Todo eso son
filas reales en la base de desarrollo. H4.1 no las limpiaba y la pantalla de
Personal acabó enseñando catorce invitaciones de prueba antes que a una sola
persona. Esto es la limpieza que faltaba.

QUÉ BORRA — Y SÓLO ESO
----------------------
Lo que una prueba MARCA al crearlo, dentro de UNA empresa:

    orden de servicio   `reported_issue` empieza por «[E2E]»
    equipo              `notes` empieza por «[E2E]» y ya no tiene órdenes
    cliente             `notes` empieza por «[E2E]», sin equipos, órdenes ni cuenta
    invitación          correo en el dominio reservado `e2e.invalid`

Con cada orden se van sus asignaciones, su historial, sus evidencias (fila y
archivo), sus notificaciones y los eventos que las generaron, y las entradas de
auditoría que la nombran. Nada sin marca se toca.

QUÉ SE NIEGA A BORRAR
---------------------
Una orden marcada que llegó más allá de la recepción — diagnóstico, cotización,
reparación, repuestos, calidad, cobro o entrega — detiene el comando. Ninguna
prueba E2E crea esas filas; si existen, alguien trabajó sobre esa orden y borrarla
sería destruir un registro, no limpiar una prueba.

Se niega a correr con `DEBUG=False`, sin bandera que lo fuerce: un
`--force-production` es exactamente como un borrado de pruebas acaba en una base
viva.

USO
---
    python manage.py purge_e2e_data --company-slug black-dog-store
    python manage.py purge_e2e_data --company-slug black-dog-store --dry-run
"""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import ProtectedError

MARKER = '[E2E]'
# `.invalid` está reservado (RFC 2606): ninguna persona real tiene este correo.
E2E_EMAIL_DOMAIN = '@e2e.invalid'


class Command(BaseCommand):
    help = (
        'Borra las órdenes de servicio, equipos y clientes marcados «[E2E]» por las '
        'pruebas de navegador. Sólo con DEBUG=True.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--company-slug', required=True)
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Cuenta lo que se borraría y no borra nada.',
        )

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError('Los datos de pruebas E2E sólo se borran en desarrollo.')

        from store import evidence_storage
        from store.models import (
            AdminAuditLog, Company, Customer, Device, Notification, NotificationEvent,
            RepairDelivery, RepairEvidence, RepairOrder, RepairStatusHistory,
            StaffInvitation, TechnicianAssignment,
        )

        company = Company.objects.filter(slug=options['company_slug']).first()
        if company is None:
            raise CommandError(f'No existe una empresa con slug "{options["company_slug"]}".')

        orders = RepairOrder.objects.filter(company=company, reported_issue__startswith=MARKER)
        worked_on = [
            order.number for order in orders
            if order.diagnostics.exists() or order.quotes.exists()
            or order.executions.exists() or order.part_usages.exists()
            or order.quality_checks.exists() or order.payments.exists()
            or RepairDelivery.objects.filter(repair_order=order).exists()
        ]
        if worked_on:
            raise CommandError(
                'Estas órdenes marcadas tienen trabajo registrado y no se borran: '
                + ', '.join(worked_on)
            )

        order_ids = list(orders.values_list('pk', flat=True))
        evidence = RepairEvidence.objects.filter(repair_order_id__in=order_ids)
        evidence_ids = list(evidence.values_list('pk', flat=True))
        storage_keys = list(evidence.values_list('storage_key', flat=True))

        plan = {
            'órdenes': len(order_ids),
            'evidencias': len(evidence_ids),
            'asignaciones': TechnicianAssignment.objects.filter(repair_order_id__in=order_ids).count(),
            'historial': RepairStatusHistory.objects.filter(repair_order_id__in=order_ids).count(),
            'notificaciones': Notification.objects.filter(
                company=company, target_type='repair_order', target_id__in=order_ids,
            ).count(),
            'invitaciones': StaffInvitation.objects.filter(
                company=company, email__endswith=E2E_EMAIL_DOMAIN,
            ).count(),
        }

        if options['dry_run']:
            for name, count in plan.items():
                self.stdout.write(f'  {name:<16}{count}')
            self.stdout.write(self.style.WARNING('\nSimulación: no se borró nada.'))
            return

        with transaction.atomic():
            NotificationEvent.objects.filter(
                company=company, target_type='repair_order', target_id__in=order_ids,
            ).delete()  # arrastra sus notificaciones
            Notification.objects.filter(
                company=company, target_type='repair_order', target_id__in=order_ids,
            ).delete()
            AdminAuditLog.objects.filter(
                company=company, target_type='repair_order',
                target_id__in=[str(pk) for pk in order_ids],
            ).delete()
            AdminAuditLog.objects.filter(
                company=company, target_type='repair_evidence',
                target_id__in=[str(pk) for pk in evidence_ids],
            ).delete()
            evidence.delete()
            TechnicianAssignment.objects.filter(repair_order_id__in=order_ids).delete()
            RepairStatusHistory.objects.filter(repair_order_id__in=order_ids).delete()
            orders.delete()

            devices = Device.objects.filter(
                company=company, notes__startswith=MARKER, repair_orders__isnull=True,
            )
            device_ids = [str(pk) for pk in devices.values_list('pk', flat=True)]
            AdminAuditLog.objects.filter(
                company=company, target_type='device', target_id__in=device_ids,
            ).delete()
            plan['equipos'] = devices.count()
            devices.delete()

            kept_customers = []
            removed_customers = 0
            for customer in Customer.objects.filter(
                company=company, notes__startswith=MARKER, user__isnull=True,
            ):
                try:
                    with transaction.atomic():
                        AdminAuditLog.objects.filter(
                            company=company, target_type='customer', target_id=str(customer.pk),
                        ).delete()
                        customer.delete()
                    removed_customers += 1
                except ProtectedError:
                    kept_customers.append(customer.pk)
            plan['clientes'] = removed_customers

            invitations = StaffInvitation.objects.filter(
                company=company, email__endswith=E2E_EMAIL_DOMAIN,
            )
            AdminAuditLog.objects.filter(
                company=company, target_type='staff_invitation',
                target_id__in=[str(pk) for pk in invitations.values_list('pk', flat=True)],
            ).delete()
            invitations.delete()

        # Los archivos, DESPUÉS de confirmar la transacción: si algo hubiera
        # fallado dentro, las filas seguirían ahí y sus fotos también.
        for key in storage_keys:
            evidence_storage.delete_quietly(key)

        for name, count in plan.items():
            self.stdout.write(f'  {name:<16}{count}')
        if kept_customers:
            self.stdout.write(self.style.WARNING(
                f'  Clientes marcados que siguen referenciados y se conservan: {kept_customers}'
            ))
        self.stdout.write(self.style.SUCCESS('\nDatos E2E eliminados.'))
