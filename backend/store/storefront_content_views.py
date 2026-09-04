"""
M12F — panel de contenido del escaparate.

AUTORIDAD REUTILIZADA, NO INVENTADA. Estas vistas usan `_settings_context`, el
mismo ayudante que ya autoriza la configuración de la empresa. Trae resuelto lo
que importa y está auditado desde la Fase 3:

  * el `?company=` es UNTRUSTED y sólo SELECCIONA entre las empresas que quien
    llama ya alcanza — nunca amplía acceso;
  * una empresa ajena responde igual que una inexistente (404), porque un 403
    confirmaría que ese id existe;
  * el master de plataforma tiene que nombrar la empresa. Sin `?company=` y sin
    membresías obtiene un error, NO «todas»: `company` vacío jamás significa la
    plataforma entera.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import StorefrontCampaign
from .settings_views import _settings_context
from .storefront_content_services import (
    CONTENT_CAPABILITY,
    CONTENT_VIEW_CAPABILITY,
    LIST_CONTENT_MODELS,
    admin_list_payload,
    delete_list_row,
    list_content_rows,
    save_list_row,
    archive_campaign,
    create_campaign,
    publish_campaign,
    update_campaign,
    update_page_settings,
    _public_payload,
)

_NOT_FOUND = 'Campaña no encontrada.'


def _admin_payload(campaign) -> dict:
    """
    Lo que ve quien administra: todo lo público MÁS el estado y la programación.

    Se construye sobre `_public_payload` a propósito, para que un campo nuevo no
    pueda aparecer en el panel y faltar en la web —o al revés— por haberse
    añadido a una lista y no a la otra.
    """
    data = dict(_public_payload(campaign))
    data.update({
        'id': campaign.pk,
        'status': campaign.status,
        'priority': campaign.priority,
        'starts_at': campaign.starts_at,
        'ends_at': campaign.ends_at,
        'published_at': campaign.published_at,
        'is_active': campaign.is_active(),
        'product_id': campaign.product_id,
        'updated_at': campaign.updated_at,
    })
    return data


def _campaign_or_404(company, pk):
    """
    Buscar SIEMPRE dentro de la empresa resuelta.

    Filtrar por `pk` y comprobar la empresa después deja una ventana en la que
    el objeto ajeno ya está cargado; filtrar por las dos a la vez no la deja.
    """
    return StorefrontCampaign.objects.filter(pk=pk, company=company).first()


class AdminStorefrontCampaignListView(APIView):
    """
    GET  /api/admin/storefront/campaigns/?company=<id>  — `company.view`
    POST /api/admin/storefront/campaigns/?company=<id>  — `company.manage`

    El listado incluye borradores y archivadas: es el panel, no el escaparate.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        company, _row, error = _settings_context(request, CONTENT_VIEW_CAPABILITY)
        if error:
            return error
        qs = StorefrontCampaign.objects.for_company(company).select_related('product')
        status_filter = request.query_params.get('status')
        if status_filter:
            qs = qs.filter(status=status_filter)
        return Response({
            'company': {'id': company.pk, 'slug': company.slug, 'name': company.name},
            'slots': [
                {'value': v, 'label': l} for v, l in StorefrontCampaign.Slot.choices
            ],
            'results': [_admin_payload(c) for c in qs],
        })

    def post(self, request):
        company, _row, error = _settings_context(request, CONTENT_CAPABILITY)
        if error:
            return error
        try:
            campaign = create_campaign(
                company=company, actor=request.user,
                data=request.data, request=request,
            )
        except DjangoValidationError as exc:
            return Response(
                {'detail': 'Datos inválidos.', 'errors': _errors(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(_admin_payload(campaign), status=status.HTTP_201_CREATED)


class AdminStorefrontCampaignDetailView(APIView):
    """
    GET    — `company.view`
    PATCH  — `company.manage`. Editar NO publica.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, pk):
        company, _row, error = _settings_context(request, CONTENT_VIEW_CAPABILITY)
        if error:
            return error
        campaign = _campaign_or_404(company, pk)
        if campaign is None:
            return Response({'detail': _NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)
        return Response(_admin_payload(campaign))

    def patch(self, request, pk):
        company, _row, error = _settings_context(request, CONTENT_CAPABILITY)
        if error:
            return error
        campaign = _campaign_or_404(company, pk)
        if campaign is None:
            return Response({'detail': _NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)
        try:
            campaign = update_campaign(
                campaign=campaign, actor=request.user,
                data=request.data, request=request,
            )
        except DjangoValidationError as exc:
            return Response(
                {'detail': 'Datos inválidos.', 'errors': _errors(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(_admin_payload(campaign))


class AdminStorefrontCampaignActionView(APIView):
    """
    POST .../<pk>/publish/   — `company.manage`
    POST .../<pk>/archive/   — `company.manage`

    Acciones con NOMBRE. Publicar no es un efecto secundario de guardar: guardar
    un borrador lo deja en borrador, y sólo esta llamada lo pone en la portada.
    """

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk, action):
        company, _row, error = _settings_context(request, CONTENT_CAPABILITY)
        if error:
            return error
        campaign = _campaign_or_404(company, pk)
        if campaign is None:
            return Response({'detail': _NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)

        handler = {'publish': publish_campaign, 'archive': archive_campaign}.get(action)
        if handler is None:
            return Response(
                {'detail': 'Acción desconocida.'}, status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            campaign = handler(campaign=campaign, actor=request.user, request=request)
        except DjangoValidationError as exc:
            return Response(
                {'detail': 'Datos inválidos.', 'errors': _errors(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(_admin_payload(campaign))


class AdminStorefrontCampaignPreviewView(APIView):
    """
    GET .../<pk>/preview/ — `company.view`

    LA VISTA PREVIA NO PUBLICA. Devuelve exactamente el payload que el
    escaparate recibiría —el mismo `_public_payload`, no una copia— para que lo
    que se ve al previsualizar sea lo que se verá al publicar.

    Requiere autoridad sobre la empresa: no hay enlace público de vista previa,
    porque un token que enseña borradores a quien lo tenga es una publicación
    con otro nombre.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, pk):
        company, _row, error = _settings_context(request, CONTENT_VIEW_CAPABILITY)
        if error:
            return error
        campaign = _campaign_or_404(company, pk)
        if campaign is None:
            return Response({'detail': _NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)
        response = Response({
            'slot': campaign.slot,
            'preview': _public_payload(campaign),
            'would_be_visible_now': campaign.is_active(),
        })
        # Una previsualización de contenido no publicado no se cachea en ningún
        # sitio intermedio.
        response['Cache-Control'] = 'no-store'
        return response


class AdminStorefrontPageView(APIView):
    """
    GET   /api/admin/storefront/page/?company=<id>  — `company.view`
    PATCH — `company.manage`
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        from .storefront_content_services import public_page_settings

        company, _row, error = _settings_context(request, CONTENT_VIEW_CAPABILITY)
        if error:
            return error
        return Response({
            'company': {'id': company.pk, 'slug': company.slug, 'name': company.name},
            'page': public_page_settings(company),
        })

    def patch(self, request):
        from .storefront_content_services import public_page_settings

        company, _row, error = _settings_context(request, CONTENT_CAPABILITY)
        if error:
            return error
        try:
            update_page_settings(
                company=company, actor=request.user,
                data=request.data, request=request,
            )
        except DjangoValidationError as exc:
            return Response(
                {'detail': 'Datos inválidos.', 'errors': _errors(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        company.refresh_from_db()
        return Response({'page': public_page_settings(company)})


def _errors(exc) -> dict:
    """Mensajes por campo, sin filtrar internals."""
    if hasattr(exc, 'message_dict'):
        return exc.message_dict
    return {'__all__': list(getattr(exc, 'messages', [str(exc)]))}


class AdminStorefrontListContentView(APIView):
    """
    GET    /api/admin/storefront/<kind>/?company=<id>       — `company.view`
    POST   /api/admin/storefront/<kind>/?company=<id>       — `company.manage`
    PATCH  /api/admin/storefront/<kind>/<pk>/?company=<id>  — `company.manage`
    DELETE /api/admin/storefront/<kind>/<pk>/?company=<id>  — `company.manage`

    `kind` es servicios, preguntas o métricas. UNA vista y no tres: los tres
    tienen la misma vida, y tres copias del mismo CRUD son tres sitios donde
    arreglar el mismo fallo — con dos de ellos olvidados.

    `kind` se valida contra el mapa; un valor desconocido es 404 y no una
    excepción.
    """

    permission_classes = [permissions.IsAuthenticated]

    def _kind(self, kind):
        return kind if kind in LIST_CONTENT_MODELS else None

    def get(self, request, kind):
        if self._kind(kind) is None:
            return Response({'detail': _NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)
        company, _row, error = _settings_context(request, CONTENT_VIEW_CAPABILITY)
        if error:
            return error
        return Response({
            'company': {'id': company.pk, 'slug': company.slug, 'name': company.name},
            'results': [
                admin_list_payload(kind, row) for row in list_content_rows(kind, company)
            ],
        })

    def post(self, request, kind):
        if self._kind(kind) is None:
            return Response({'detail': _NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)
        company, _row, error = _settings_context(request, CONTENT_CAPABILITY)
        if error:
            return error
        try:
            row = save_list_row(
                kind=kind, company=company, actor=request.user,
                data=request.data, request=request,
            )
        except DjangoValidationError as exc:
            return Response(
                {'detail': 'Datos inválidos.', 'errors': _errors(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(admin_list_payload(kind, row), status=status.HTTP_201_CREATED)


class AdminStorefrontListContentDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def _row(self, kind, company, pk):
        if kind not in LIST_CONTENT_MODELS:
            return None
        # SIEMPRE dentro de la empresa resuelta: filtrar por `pk` y comprobar la
        # empresa después deja una ventana en la que el objeto ajeno ya está
        # cargado.
        return list_content_rows(kind, company).filter(pk=pk).first()

    def patch(self, request, kind, pk):
        company, _row, error = _settings_context(request, CONTENT_CAPABILITY)
        if error:
            return error
        row = self._row(kind, company, pk)
        if row is None:
            return Response({'detail': _NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)
        try:
            row = save_list_row(
                kind=kind, company=company, actor=request.user,
                data=request.data, row=row, request=request,
            )
        except DjangoValidationError as exc:
            return Response(
                {'detail': 'Datos inválidos.', 'errors': _errors(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(admin_list_payload(kind, row))

    def delete(self, request, kind, pk):
        company, _row, error = _settings_context(request, CONTENT_CAPABILITY)
        if error:
            return error
        row = self._row(kind, company, pk)
        if row is None:
            return Response({'detail': _NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)
        delete_list_row(
            kind=kind, company=company, actor=request.user, row=row, request=request,
        )
        return Response(status=status.HTTP_204_NO_CONTENT)
