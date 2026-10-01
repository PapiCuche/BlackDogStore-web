"""
M12D — el dominio de las evidencias.

    UNA FOTO NO AVANZA EL ESTADO DE LA REPARACIÓN.

Subir, compartir o anular una evidencia no toca `RepairOrder.status` ni lo
consulta para decidir. Una evidencia describe algo que ocurrió; el ciclo de vida
lo mueven las operaciones que ya existen y que escriben las filas que le dan
sentido a cada estado.

    CORREGIR = ANULAR + SUBIR OTRA. NUNCA REEMPLAZAR.

    TODA EVIDENCIA NACE INTERNA.

Que una foto llegue al cliente es una decisión con autor, fecha y registro. No
hay una casilla en el formulario de subida que lo haga de paso, porque la
casilla que nadie mira es la que acaba publicando la foto de una placa abierta.
"""

from __future__ import annotations

import hashlib
import logging

from django.db import IntegrityError, transaction
from django.utils import timezone

from . import evidence_images as images
from . import evidence_storage as storage
from .models import AdminAuditLog, RepairEvidence, RepairOrder
from .tenancy import has_branch_access, has_capability

logger = logging.getLogger(__name__)


class EvidenceError(Exception):
    """Un rechazo que quien sube la foto puede leer y corregir."""


class EvidenceConflict(EvidenceError):
    """Misma clave de idempotencia, contenido distinto."""


#: QUÉ AUTORIDAD PIDE CADA ETAPA, y por qué no hay una capability nueva.
#
# La autoridad para fotografiar un momento es la misma que para producirlo:
# quien puede diagnosticar puede fotografiar el diagnóstico, quien puede
# entregar puede fotografiar la entrega. Inventar `service.evidence.manage`
# habría creado un permiso que se concede una vez y abre las siete etapas de
# golpe — justo lo contrario de lo que un catálogo de capacidades existe para
# permitir.
#
# `OTHER` pide `service.orders.manage`: es el cajón de sastre, así que exige la
# autoridad más amplia sobre la orden en lugar de la más barata.
STAGE_CAPABILITY = {
    RepairEvidence.Stage.INTAKE: 'service.orders.create',
    RepairEvidence.Stage.DIAGNOSIS: 'service.diagnostic.manage',
    RepairEvidence.Stage.REPAIR_BEFORE: 'service.repair.manage',
    RepairEvidence.Stage.REPAIR_DURING: 'service.repair.manage',
    RepairEvidence.Stage.REPAIR_AFTER: 'service.repair.manage',
    RepairEvidence.Stage.QUALITY: 'service.quality.manage',
    RepairEvidence.Stage.DELIVERY: 'service.delivery.manage',
    RepairEvidence.Stage.OTHER: 'service.orders.manage',
}

#: Ver la galería es leer la orden. Publicar o anular es otra cosa: son actos
#: sobre lo que el cliente verá y sobre el registro, así que piden la autoridad
#: de la etapa concreta.
VIEW_CAPABILITY = 'service.orders.view'


def capability_for_stage(stage: str) -> str:
    try:
        return STAGE_CAPABILITY[stage]
    except KeyError:
        raise EvidenceError('Etapa de evidencia desconocida.') from None


def may_act_on_stage(user, company, stage: str, *, branch=None) -> bool:
    """
    Dos ejes, como en el resto del proyecto: QUÉ puede hacer y DÓNDE.

    Tener `service.repair.manage` no da acceso a una orden de otra sucursal, y
    trabajar en la sucursal no da la capacidad. Se piden las dos.
    """
    if not has_capability(user, company, capability_for_stage(stage)):
        return False
    if branch is not None and not has_branch_access(user, branch):
        return False
    return True


def evidence_for_order(repair_order):
    """Todo lo de esta orden, anulado incluido. Historia, no estado."""
    return RepairEvidence.objects.filter(
        company_id=repair_order.company_id, repair_order=repair_order,
    ).select_related('uploaded_by')


def customer_evidence_for_order(repair_order):
    """
    Lo que el cliente puede ver, y la lista nace acotada.

    Nada de filtrar después según quién pregunte: este queryset no sabe producir
    una evidencia interna ni una anulada, así que ningún descuido aguas abajo
    puede convertirlo en una que sí.
    """
    return RepairEvidence.objects.filter(
        company_id=repair_order.company_id,
        repair_order=repair_order,
        visibility=RepairEvidence.Visibility.CUSTOMER,
        voided_at__isnull=True,
    )


def _fingerprint(*, repair_order_id: int, stage: str, source_sha256: str) -> str:
    """
    Qué hace que dos peticiones sean «la misma».

    La orden, la etapa y el archivo ORIGINAL. Deliberadamente no el hash final:
    el pipeline puede producir bytes distintos entre versiones de Pillow, y
    entonces un reintento legítimo del mismo archivo dejaría de reconocerse
    justo el día que se actualiza una dependencia.
    """
    material = f'{repair_order_id}:{stage}:{source_sha256}'
    return hashlib.sha256(material.encode()).hexdigest()


def upload_evidence(*, repair_order, stage: str, content: bytes, actor,
                    idempotency_key: str = '', request=None):
    """
    Normalizar la foto, guardarla y registrarla. En ese orden.

    EL OBJETO SE ESCRIBE ANTES QUE LA FILA, y esa asimetría hay que compensarla
    a mano: el bucket no participa en la transacción de PostgreSQL. Si el INSERT
    falla, el objeto recién escrito se borra; si el guardado falla, no se crea
    ninguna fila. Lo que nunca puede quedar es una fila apuntando a algo que no
    existe — una galería con un hueco es peor que una galería vacía, porque
    parece un fallo de red y no un dato perdido.

    LA COMPRESIÓN OCURRE AQUÍ, antes de tocar el storage. Subir 9 MB a R2 para
    después descargarlos, comprimirlos y volver a subirlos sería pagar tres
    veces por el mismo byte.
    """
    if stage not in RepairEvidence.Stage.values:
        raise EvidenceError('Etapa de evidencia desconocida.')

    key = (idempotency_key or '').strip()[:120]
    processed = images.process(content)
    fingerprint = _fingerprint(
        repair_order_id=repair_order.pk, stage=stage,
        source_sha256=processed.source_sha256,
    )

    if key:
        existing = RepairEvidence.objects.filter(
            company_id=repair_order.company_id, idempotency_key=key,
        ).first()
        if existing is not None:
            if existing.request_fingerprint != fingerprint:
                raise EvidenceConflict(
                    'Esa clave ya se usó para una imagen diferente.'
                )
            # El reintento de algo que funcionó no es un error, y no vuelve a
            # escribir el objeto.
            return existing

    storage_key = storage.build_key(
        company_id=repair_order.company_id,
        repair_order_id=repair_order.pk,
        extension=images.OUTPUT_EXTENSION,
    )
    stored = storage.save(storage_key, processed.content)

    try:
        with transaction.atomic():
            evidence = RepairEvidence.objects.create(
                company_id=repair_order.company_id,
                repair_order=repair_order,
                stage=stage,
                # INTERNA. No hay parámetro para nacer de otra forma.
                visibility=RepairEvidence.Visibility.INTERNAL,
                storage_key=stored.key,
                mime_type=processed.mime_type,
                byte_size=processed.byte_size,
                sha256=processed.sha256,
                width=processed.width,
                height=processed.height,
                source_sha256=processed.source_sha256,
                source_byte_size=processed.source_byte_size,
                uploaded_by=actor,
                idempotency_key=key,
                request_fingerprint=fingerprint if key else '',
            )
    except IntegrityError:
        storage.delete_quietly(stored.key)
        if key:
            # Dos peticiones con la misma clave a la vez: la perdedora devuelve
            # lo que escribió la ganadora en lugar de un error.
            winner = RepairEvidence.objects.filter(
                company_id=repair_order.company_id, idempotency_key=key,
            ).first()
            if winner is not None:
                if winner.request_fingerprint != fingerprint:
                    raise EvidenceConflict(
                        'Esa clave ya se usó para una imagen diferente.'
                    ) from None
                return winner
        raise EvidenceError('No se pudo registrar la evidencia.') from None
    except Exception:
        storage.delete_quietly(stored.key)
        raise

    AdminAuditLog.log(
        actor=actor, action='service_evidence_uploaded',
        target_type='repair_evidence', target_id=evidence.pk,
        metadata={
            'repair_order_id': repair_order.pk,
            'number': repair_order.number,
            'stage': stage,
            'byte_size': processed.byte_size,
            # NI la clave del objeto, NI una URL firmada, NI el hash del
            # original, NI nada del EXIF. Un registro de auditoría se guarda
            # mucho tiempo y se lee desde sitios que no son este.
        },
        request=request, company=repair_order.company,
    )
    return evidence


def publish_to_customer(*, evidence, actor, request=None):
    """
    Un acto explícito, con registro. No altera el archivo ni sus metadatos.
    """
    with transaction.atomic():
        locked = RepairEvidence.objects.select_for_update().get(pk=evidence.pk)
        if locked.is_voided:
            raise EvidenceError('Una evidencia anulada no se puede compartir.')
        if locked.visibility == RepairEvidence.Visibility.CUSTOMER:
            return locked
        locked.visibility = RepairEvidence.Visibility.CUSTOMER
        locked.save(update_fields=['visibility'])
        AdminAuditLog.log(
            actor=actor, action='service_evidence_published_to_customer',
            target_type='repair_evidence', target_id=locked.pk,
            metadata={'repair_order_id': locked.repair_order_id, 'stage': locked.stage},
            request=request, company=locked.company,
        )
    return locked


def hide_from_customer(*, evidence, actor, request=None):
    """
    Retirar el acceso, con efecto inmediato.

    No borra la evidencia ni el historial técnico: el cliente deja de poder
    abrirla en la siguiente petición porque su queryset filtra por visibilidad,
    no porque se haya destruido nada.
    """
    with transaction.atomic():
        locked = RepairEvidence.objects.select_for_update().get(pk=evidence.pk)
        if locked.visibility == RepairEvidence.Visibility.INTERNAL:
            return locked
        locked.visibility = RepairEvidence.Visibility.INTERNAL
        locked.save(update_fields=['visibility'])
        AdminAuditLog.log(
            actor=actor, action='service_evidence_hidden_from_customer',
            target_type='repair_evidence', target_id=locked.pk,
            metadata={'repair_order_id': locked.repair_order_id, 'stage': locked.stage},
            request=request, company=locked.company,
        )
    return locked


def void_evidence(*, evidence, reason: str, actor, request=None):
    """
    Retirar una evidencia de circulación conservando que existió.

    IDEMPOTENTE, y el motivo original NO se reescribe: quien pulsa dos veces
    porque la página no refrescó no debe poder cambiar en silencio la razón que
    quedó registrada la primera vez.
    """
    reason = (reason or '').strip()
    with transaction.atomic():
        locked = RepairEvidence.objects.select_for_update().get(pk=evidence.pk)
        if locked.is_voided:
            return locked
        if not reason:
            raise EvidenceError('Indica por qué se anula la evidencia.')
        locked.voided_at = timezone.now()
        locked.voided_by = actor
        locked.void_reason = reason[:300]
        # La visibilidad cae con ella: una evidencia anulada no puede seguir
        # siendo lo que el cliente ve de su reparación.
        locked.visibility = RepairEvidence.Visibility.INTERNAL
        locked.save(update_fields=[
            'voided_at', 'voided_by', 'void_reason', 'visibility',
        ])
        AdminAuditLog.log(
            actor=actor, action='service_evidence_voided',
            target_type='repair_evidence', target_id=locked.pk,
            metadata={
                'repair_order_id': locked.repair_order_id,
                'stage': locked.stage,
                'reason': locked.void_reason,
            },
            request=request, company=locked.company,
        )
    return locked
