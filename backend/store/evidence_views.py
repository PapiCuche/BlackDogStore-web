"""
M12D — la API de evidencias.

    EL STORAGE KEY NO ES AUTORIZACIÓN.

Ninguna ruta traduce un id a un objeto sin comprobar antes empresa, sucursal,
autoridad, visibilidad y anulación. La clave del objeto no viaja nunca al
cliente: se envía un endpoint de contenido, y ese endpoint vuelve a preguntarlo
todo. Un diseño donde el frontend recibiera la ruta y la pidiera directamente
convertiría el bucket en la frontera de seguridad, y un bucket no sabe quién
está preguntando.

    EMPRESA A NO PUEDE SABER QUE EXISTE UN OBJETO DE EMPRESA B.

Todo lo que no es tuyo responde 404. Un 403 confirmaría el id, y confirmar ids
es exactamente lo que pide quien prueba números.
"""

from __future__ import annotations

from django.http import FileResponse
from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from . import evidence_images as images
from . import evidence_services as svc
from . import evidence_storage as storage
from . import service_services
from .models import RepairEvidence
from .throttles import AdminOrderStatusChangeThrottle
from .v1_customer_views import V1CustomerSurfaceMixin
from .v1_service_views import V1ServiceSurfaceMixin


def _internal(evidence) -> dict:
    """
    Lo que ve el personal.

    NO lleva `storage_key`, ni bucket, ni endpoint, ni una URL firmada
    persistente. Lleva la ruta del endpoint que sirve los bytes, y ese endpoint
    autoriza por su cuenta.
    """
    return {
        'id': evidence.pk,
        'stage': evidence.stage,
        'visibility': evidence.visibility,
        'mime_type': evidence.mime_type,
        'byte_size': evidence.byte_size,
        'width': evidence.width,
        'height': evidence.height,
        'created_at': evidence.created_at,
        'uploaded_by': getattr(evidence.uploaded_by, 'username', ''),
        'voided_at': evidence.voided_at,
        'void_reason': evidence.void_reason or None,
    }


def _customer(evidence) -> dict:
    """
    Lo que ve el cliente, escrito como allowlist en vez de heredado.

    Heredar del serializador interno y quitar campos es la forma de que el
    próximo campo que alguien añada arriba aparezca aquí sin que nadie lo
    decida. Esto es una lista de lo que SÍ sale: ni quién la subió, ni el motivo
    de una anulación, ni nada del storage, ni la idempotencia.
    """
    return {
        'id': evidence.pk,
        'stage': evidence.stage,
        'width': evidence.width,
        'height': evidence.height,
        'created_at': evidence.created_at,
    }


def _no_store(response):
    """Contenido privado: ningún CDN ni navegador compartido debe quedárselo."""
    response['Cache-Control'] = 'private, max-age=0, no-store'
    response['Pragma'] = 'no-cache'
    return response


def _serve(evidence):
    """
    Los bytes, o una redirección firmada y corta cuando el backend sabe firmar.

    La firma se pide en el momento y no se guarda en ninguna parte: una URL
    firmada persistida sobrevive a la revocación del acceso que la justificaba.
    """
    signed = storage.temporary_url(evidence.storage_key)
    if signed:
        response = Response(status=status.HTTP_302_FOUND)
        response['Location'] = signed
        return _no_store(response)
    stream = storage.open_stream(evidence.storage_key)
    return _no_store(FileResponse(stream, content_type=evidence.mime_type))


class _InternalEvidenceMixin(V1ServiceSurfaceMixin):
    throttle_classes = [AdminOrderStatusChangeThrottle]

    def scope(self, company_slug, pk):
        """Empresa, orden y sucursal, resueltas por el camino que ya existía."""
        company = self.get_internal_company()
        self.require_capability(company, svc.VIEW_CAPABILITY)
        order = self.get_order(company, pk)
        return company, order

    def owned(self, order, evidence_id):
        evidence = svc.evidence_for_order(order).filter(pk=evidence_id).first()
        if evidence is None:
            raise NotFound('No encontrado.')
        return evidence

    def require_stage(self, company, order, stage):
        if not svc.may_act_on_stage(
            self.request.user, company, stage, branch=order.branch,
        ):
            raise PermissionDenied('No tienes permiso para esta etapa.')


class InternalEvidenceListView(_InternalEvidenceMixin, APIView):
    """GET — la galería de la orden. POST — una imagen."""

    def get(self, request, company_slug=None, pk=None):
        _company, order = self.scope(company_slug, pk)
        rows = svc.evidence_for_order(order)
        return Response({
            'count': rows.count(),
            'results': [_internal(e) for e in rows],
        })

    def post(self, request, company_slug=None, pk=None):
        company, order = self.scope(company_slug, pk)
        stage = (request.data.get('stage') or '').strip()
        if stage not in RepairEvidence.Stage.values:
            return Response({'detail': 'Etapa de evidencia desconocida.'},
                            status=status.HTTP_400_BAD_REQUEST)
        # La autoridad se pide por ETAPA: quien diagnostica no adquiere por eso
        # la capacidad de fotografiar una entrega.
        self.require_stage(company, order, stage)

        upload = request.FILES.get('image')
        if upload is None:
            return Response({'detail': 'Adjunta una imagen.'},
                            status=status.HTTP_400_BAD_REQUEST)
        if upload.size > images.max_upload_bytes():
            return Response({'detail': 'La imagen es demasiado grande.'},
                            status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)

        try:
            evidence = svc.upload_evidence(
                repair_order=order, stage=stage, content=upload.read(),
                actor=request.user,
                idempotency_key=request.headers.get('Idempotency-Key', ''),
                request=request,
            )
        except svc.EvidenceConflict as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_409_CONFLICT)
        except (svc.EvidenceError, images.EvidenceImageError,
                storage.EvidenceStorageError) as exc:
            return Response({'detail': str(exc)},
                            status=status.HTTP_400_BAD_REQUEST)
        return Response(_internal(evidence), status=status.HTTP_201_CREATED)


class InternalEvidenceDetailView(_InternalEvidenceMixin, APIView):
    def get(self, request, company_slug=None, pk=None, evidence_id=None):
        _company, order = self.scope(company_slug, pk)
        return Response(_internal(self.owned(order, evidence_id)))


class InternalEvidenceContentView(_InternalEvidenceMixin, APIView):
    def get(self, request, company_slug=None, pk=None, evidence_id=None):
        _company, order = self.scope(company_slug, pk)
        return _serve(self.owned(order, evidence_id))


class InternalEvidencePublishView(_InternalEvidenceMixin, APIView):
    def post(self, request, company_slug=None, pk=None, evidence_id=None):
        company, order = self.scope(company_slug, pk)
        evidence = self.owned(order, evidence_id)
        self.require_stage(company, order, evidence.stage)
        try:
            updated = svc.publish_to_customer(
                evidence=evidence, actor=request.user, request=request,
            )
        except svc.EvidenceError as exc:
            return Response({'detail': str(exc)},
                            status=status.HTTP_400_BAD_REQUEST)
        return Response(_internal(updated))


class InternalEvidenceHideView(_InternalEvidenceMixin, APIView):
    def post(self, request, company_slug=None, pk=None, evidence_id=None):
        company, order = self.scope(company_slug, pk)
        evidence = self.owned(order, evidence_id)
        self.require_stage(company, order, evidence.stage)
        return Response(_internal(svc.hide_from_customer(
            evidence=evidence, actor=request.user, request=request,
        )))


class InternalEvidenceVoidView(_InternalEvidenceMixin, APIView):
    def post(self, request, company_slug=None, pk=None, evidence_id=None):
        company, order = self.scope(company_slug, pk)
        evidence = self.owned(order, evidence_id)
        self.require_stage(company, order, evidence.stage)
        try:
            voided = svc.void_evidence(
                evidence=evidence, reason=request.data.get('reason', ''),
                actor=request.user, request=request,
            )
        except svc.EvidenceError as exc:
            return Response({'detail': str(exc)},
                            status=status.HTTP_400_BAD_REQUEST)
        return Response(_internal(voided))


class _CustomerEvidenceMixin(V1CustomerSurfaceMixin):
    """
    La superficie del cliente, y NO tiene escritura.

    M12D es captura técnica. Un cliente que pudiera subir imágenes traería una
    fase entera de moderación, límites y abuso que esta no resuelve.
    """

    def order(self, pk):
        company = self.get_customer_company()
        order = service_services.customer_owned_repair_orders(
            self.request.user, company,
        ).filter(pk=pk).first()
        if order is None:
            # La reparación de otra persona, o de otra empresa, no existe.
            raise NotFound('No encontrado.')
        return order

    def visible(self, order, evidence_id):
        evidence = svc.customer_evidence_for_order(order).filter(
            pk=evidence_id,
        ).first()
        if evidence is None:
            raise NotFound('No encontrado.')
        return evidence


class CustomerEvidenceListView(_CustomerEvidenceMixin, APIView):
    def get(self, request, company_slug=None, pk=None):
        order = self.order(pk)
        rows = svc.customer_evidence_for_order(order)
        return Response({
            'count': rows.count(),
            'results': [_customer(e) for e in rows],
        })


class CustomerEvidenceContentView(_CustomerEvidenceMixin, APIView):
    def get(self, request, company_slug=None, pk=None, evidence_id=None):
        order = self.order(pk)
        return _serve(self.visible(order, evidence_id))
