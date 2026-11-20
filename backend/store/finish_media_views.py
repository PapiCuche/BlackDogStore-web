"""
CATALOG-REFERENCE-01 · PR C — the finish galleries of a model (model + colour), from the panel.

    GET    /api/admin/catalog/models/<id>/finish-images/           every finish and its pictures   products.view
    POST   /api/admin/catalog/models/<id>/finish-images/           uploads one (multipart: file,   products.manage
                                                                   color_option_id, alt_text, source_kind,
                                                                   license, source_url, attribution)
    PATCH  /api/admin/catalog/models/<id>/finish-images/<image>/   text / primary / provenance /   products.manage
                                                                   license_verified
    DELETE /api/admin/catalog/models/<id>/finish-images/<image>/   removes it                      products.manage
    POST   /api/admin/catalog/models/<id>/finish-images/order/     {color_option_id, order}        products.manage

THE COMPANY COMES FROM THE RESOLVED CONTEXT, never from the body. A model of another company answers like one that
does not exist, a picture of another model like a missing one, and so does an option of another company.
"""
from django.core.exceptions import ValidationError
from rest_framework import permissions, status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from . import finish_media, storefront_media
from .admin_views import (
    CAP_PRODUCTS_MANAGE, CAP_PRODUCTS_VIEW, _LEGACY_MANAGE_CATALOG_ROLES, _LEGACY_VIEW_CATALOG_ROLES,
    _company_context,
)
from .models import ProductModel
from .throttles import AdminProductsThrottle, AdminProductWriteThrottle

_NOT_FOUND = {'detail': 'No encontrado.'}


def _refused(exc: finish_media.FinishMediaError) -> Response:
    code = status.HTTP_404_NOT_FOUND if exc.code == 'not_found' else status.HTTP_400_BAD_REQUEST
    return Response(exc.body(), status=code)


class _FinishGalleryView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get_throttles(self):
        if self.request.method in ('GET', 'HEAD', 'OPTIONS'):
            return [AdminProductsThrottle()]
        return [AdminProductWriteThrottle()]

    def model(self, request, pk, *, write):
        """The model, already inside the caller's company and with the authority checked."""
        company, error = (
            _company_context(request, CAP_PRODUCTS_MANAGE, _LEGACY_MANAGE_CATALOG_ROLES) if write else
            _company_context(request, CAP_PRODUCTS_VIEW, _LEGACY_VIEW_CATALOG_ROLES)
        )
        if error:
            return None, error
        model = ProductModel.objects.filter(company=company, pk=pk).first()
        if model is None:
            return None, Response(_NOT_FOUND, status=status.HTTP_404_NOT_FOUND)
        return model, None


class AdminModelFinishImageListView(_FinishGalleryView):
    def get(self, request, pk):
        model, error = self.model(request, pk, write=False)
        if error:
            return error
        return Response(finish_media.listing(model))

    def post(self, request, pk):
        model, error = self.model(request, pk, write=True)
        if error:
            return error
        uploaded = request.FILES.get('file')
        if uploaded is None:
            return Response({'detail': 'Falta el archivo de la imagen.', 'field': 'file', 'code': 'invalid'},
                            status=status.HTTP_400_BAD_REQUEST)
        if uploaded.size > storefront_media.max_upload_bytes():
            megabytes = max(1, storefront_media.max_upload_bytes() // (1024 * 1024))
            return Response({'detail': f'La imagen pesa más de {megabytes} MB.', 'field': 'file'},
                            status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
        provenance = {field: request.data.get(field, '') for field in finish_media.PROVENANCE_FIELDS}
        try:
            image = finish_media.add_image(
                model=model, color_option_id=request.data.get('color_option_id'), actor=request.user,
                uploaded=uploaded, provenance=provenance, alt_text=request.data.get('alt_text', ''),
                request=request,
            )
        except finish_media.FinishMediaError as exc:
            return _refused(exc)
        except storefront_media.StorefrontImageError as exc:
            return Response({'detail': str(exc), 'field': 'file', 'code': 'invalid'},
                            status=status.HTTP_400_BAD_REQUEST)
        except ValidationError as exc:
            # `claim`: the stored file vanished before it was placed (a concurrent sweep).
            return Response({'detail': '; '.join(exc.messages), 'field': 'file', 'code': 'invalid'},
                            status=status.HTTP_400_BAD_REQUEST)
        return Response(finish_media.payload(image), status=status.HTTP_201_CREATED)


class AdminModelFinishImageDetailView(_FinishGalleryView):
    def image(self, request, pk, image_id):
        model, error = self.model(request, pk, write=True)
        if error:
            return None, error
        image = finish_media.gallery(model).filter(pk=image_id).first()
        if image is None:
            return None, Response(_NOT_FOUND, status=status.HTTP_404_NOT_FOUND)
        return image, None

    def patch(self, request, pk, image_id):
        image, error = self.image(request, pk, image_id)
        if error:
            return error
        flags = {}
        for name in ('is_primary', 'license_verified'):
            value = request.data.get(name)
            if value is not None and not isinstance(value, bool):
                return Response({'detail': f'`{name}` debe ser verdadero o falso.', 'field': name},
                                status=status.HTTP_400_BAD_REQUEST)
            flags[name] = value
        alt_text = request.data.get('alt_text')
        if alt_text is not None and not isinstance(alt_text, str):
            return Response({'detail': 'El texto alternativo debe ser texto.', 'field': 'alt_text'},
                            status=status.HTTP_400_BAD_REQUEST)
        provenance = {}
        for field in finish_media.PROVENANCE_FIELDS:
            if field in request.data:
                if not isinstance(request.data[field], str):
                    return Response({'detail': 'La procedencia se escribe como texto.', 'field': field},
                                    status=status.HTTP_400_BAD_REQUEST)
                provenance[field] = request.data[field]
        try:
            image = finish_media.update_image(
                image=image, actor=request.user, alt_text=alt_text, provenance=provenance, request=request, **flags,
            )
        except finish_media.FinishMediaError as exc:
            return _refused(exc)
        return Response(finish_media.payload(image))

    def delete(self, request, pk, image_id):
        image, error = self.image(request, pk, image_id)
        if error:
            return error
        try:
            finish_media.remove_image(image=image, actor=request.user, request=request)
        except finish_media.FinishImageGone:
            return Response(_NOT_FOUND, status=status.HTTP_404_NOT_FOUND)
        return Response(status=status.HTTP_204_NO_CONTENT)


class AdminModelFinishImageOrderView(_FinishGalleryView):
    def post(self, request, pk):
        model, error = self.model(request, pk, write=True)
        if error:
            return error
        try:
            finish_media.reorder(model=model, color_option_id=request.data.get('color_option_id'),
                                 order=request.data.get('order'), actor=request.user, request=request)
        except finish_media.FinishMediaError as exc:
            return _refused(exc)
        except ValidationError as exc:
            return Response({'detail': '; '.join(exc.messages)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(finish_media.listing(model))
