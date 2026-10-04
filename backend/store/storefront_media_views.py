"""Subir una imagen de la tienda desde el panel, y servirla al público."""

from __future__ import annotations

import re

from django.http import FileResponse
from rest_framework import permissions, status
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from . import evidence_storage as storage
from . import storefront_media
from .models import StorefrontImage
from .settings_views import _settings_context
from .storefront_content_services import CONTENT_CAPABILITY
from .throttles import AdminProductWriteThrottle

_PUBLIC_ID = re.compile(r'^[0-9a-f]{32}$')


class AdminStorefrontImageUploadView(APIView):
    """
    POST /api/admin/storefront/images/?company=<id> — multipart, campo `file`.

    Exige `company.manage`, lo mismo que editar la portada: subir la imagen del
    hero es editar la portada. La empresa sale del contexto resuelto, nunca del
    cuerpo de la petición.

    Devuelve la dirección de la imagen. Esa dirección es lo que se guarda en el
    hueco que corresponda (hero, categoría, campaña); subir no coloca nada.
    """

    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser]
    throttle_classes = [AdminProductWriteThrottle]

    def post(self, request):
        company, _row, error = _settings_context(request, CONTENT_CAPABILITY)
        if error:
            return error

        uploaded = request.FILES.get('file')
        if uploaded is None:
            return Response(
                {'detail': 'Falta el archivo de la imagen.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            image = storefront_media.upload(
                company=company, actor=request.user, uploaded=uploaded, request=request,
            )
        except storefront_media.StorefrontImageError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(storefront_media.payload(image), status=status.HTTP_201_CREATED)


class StorefrontImageView(APIView):
    """
    GET /api/storefront/images/<id>/ — pública, como la portada que la muestra.

    El identificador no cambia nunca de contenido, así que el navegador puede
    guardarla sin volver a preguntar. `nosniff` hace que se trate como lo que el
    servidor dice que es, que siempre es una imagen recodificada aquí.
    """

    authentication_classes = []
    permission_classes = [permissions.AllowAny]

    def get(self, request, public_id):
        image = None
        if _PUBLIC_ID.match(public_id or ''):
            # Sólo de empresas activas: una tienda desactivada deja de existir
            # para el público, y sus imágenes con ella.
            image = StorefrontImage.objects.filter(
                public_id=public_id, company__is_active=True,
            ).first()
        if image is None:
            return Response({'detail': 'Imagen no encontrada.'}, status=status.HTTP_404_NOT_FOUND)
        try:
            stream = storage.open_stream(image.storage_key)
        except storage.EvidenceStorageError:
            return Response({'detail': 'Imagen no encontrada.'}, status=status.HTTP_404_NOT_FOUND)

        response = FileResponse(stream, content_type=image.mime_type)
        response['Cache-Control'] = 'public, max-age=31536000, immutable'
        response['X-Content-Type-Options'] = 'nosniff'
        response['Content-Length'] = str(image.byte_size)
        return response
