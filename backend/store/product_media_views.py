"""
PRODUCT-MEDIA — la galería de un producto, desde el panel.

    GET    /api/admin/products/<id>/images/            la galería        products.view
    POST   /api/admin/products/<id>/images/            sube una imagen   products.manage
    PATCH  /api/admin/products/<id>/images/<image>/    texto / principal products.manage
    DELETE /api/admin/products/<id>/images/<image>/    la quita          products.manage
    POST   /api/admin/products/<id>/images/order/      reordena          products.manage

LA EMPRESA SALE DEL CONTEXTO RESUELTO, nunca del cuerpo. Un producto de otra
empresa responde igual que uno que no existe, y una imagen de otro producto
igual que una que no existe.
"""
from django.core.exceptions import ValidationError
from rest_framework import permissions, status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from . import product_media, storefront_media
from .admin_views import (
    CAP_PRODUCTS_MANAGE, CAP_PRODUCTS_VIEW, _LEGACY_MANAGE_CATALOG_ROLES,
    _LEGACY_VIEW_CATALOG_ROLES, _company_context,
)
from .models import Product
from .throttles import AdminProductsThrottle, AdminProductWriteThrottle

_NOT_FOUND = {'detail': 'No encontrado.'}


def _bad(message, code=status.HTTP_400_BAD_REQUEST):
    return Response({'detail': str(message)}, status=code)


class _ProductGalleryView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get_throttles(self):
        if self.request.method in ('GET', 'HEAD', 'OPTIONS'):
            return [AdminProductsThrottle()]
        return [AdminProductWriteThrottle()]

    def product(self, request, pk, *, write):
        """El producto, ya dentro de la empresa y con la autoridad comprobada."""
        company, error = (
            _company_context(request, CAP_PRODUCTS_MANAGE, _LEGACY_MANAGE_CATALOG_ROLES)
            if write else
            _company_context(request, CAP_PRODUCTS_VIEW, _LEGACY_VIEW_CATALOG_ROLES)
        )
        if error:
            return None, error
        product = Product.objects.filter(company=company, pk=pk).first()
        if product is None:
            return None, Response(_NOT_FOUND, status=status.HTTP_404_NOT_FOUND)
        return product, None

    @staticmethod
    def listing(product):
        return {'results': [product_media.payload(image) for image in product_media.gallery(product)]}


class AdminProductImageListView(_ProductGalleryView):
    def get(self, request, pk):
        product, error = self.product(request, pk, write=False)
        if error:
            return error
        return Response(self.listing(product))

    def post(self, request, pk):
        product, error = self.product(request, pk, write=True)
        if error:
            return error
        uploaded = request.FILES.get('file')
        if uploaded is None:
            return _bad('Falta el archivo de la imagen.')
        if uploaded.size > storefront_media.max_upload_bytes():
            megabytes = max(1, storefront_media.max_upload_bytes() // (1024 * 1024))
            return _bad(
                f'La imagen pesa más de {megabytes} MB.', status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )
        try:
            image = product_media.add_image(
                product=product, actor=request.user, uploaded=uploaded,
                alt_text=request.data.get('alt_text', ''), request=request,
            )
        except (product_media.ProductMediaError, storefront_media.StorefrontImageError) as exc:
            return _bad(exc)
        return Response(product_media.payload(image), status=status.HTTP_201_CREATED)


class AdminProductImageDetailView(_ProductGalleryView):
    def image(self, request, pk, image_id):
        product, error = self.product(request, pk, write=True)
        if error:
            return None, error
        image = product_media.gallery(product).filter(pk=image_id).first()
        if image is None:
            return None, Response(_NOT_FOUND, status=status.HTTP_404_NOT_FOUND)
        return image, None

    def patch(self, request, pk, image_id):
        image, error = self.image(request, pk, image_id)
        if error:
            return error
        is_primary = request.data.get('is_primary')
        if is_primary is not None and not isinstance(is_primary, bool):
            return _bad('`is_primary` debe ser verdadero o falso.')
        alt_text = request.data.get('alt_text')
        if alt_text is not None and not isinstance(alt_text, str):
            return _bad('El texto alternativo debe ser texto.')
        try:
            image = product_media.update_image(
                image=image, actor=request.user, alt_text=alt_text, is_primary=is_primary,
                request=request,
            )
        except product_media.ProductImageGone:
            return Response(_NOT_FOUND, status=status.HTTP_404_NOT_FOUND)
        except product_media.ProductMediaError as exc:
            return _bad(exc)
        return Response(product_media.payload(image))

    def delete(self, request, pk, image_id):
        image, error = self.image(request, pk, image_id)
        if error:
            return error
        try:
            product_media.remove_image(image=image, actor=request.user, request=request)
        except product_media.ProductImageGone:
            # Dos personas quitando la misma imagen: la segunda ya no la encuentra.
            return Response(_NOT_FOUND, status=status.HTTP_404_NOT_FOUND)
        return Response(status=status.HTTP_204_NO_CONTENT)


class AdminProductImageOrderView(_ProductGalleryView):
    def post(self, request, pk):
        product, error = self.product(request, pk, write=True)
        if error:
            return error
        try:
            product_media.reorder(
                product=product, order=request.data.get('order'), actor=request.user,
                request=request,
            )
        except (product_media.ProductMediaError, ValidationError) as exc:
            return _bad(exc)
        return Response(self.listing(product))
