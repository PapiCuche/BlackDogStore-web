"""
CATALOG-COMMERCIAL-01 · A2-1 — the HTTP surface of the supplier vocabulary (DEC-CATALOG-ALIAS-01).

GET    /api/admin/catalog/aliases/?scope=&q=     products.view    the company's aliases
POST   /api/admin/catalog/aliases/               products.manage  {scope, alias, model_id?|option_id?|replacement?}
PATCH  /api/admin/catalog/aliases/<pk>/          products.manage  {alias?, model_id?, option_id?, replacement?}
DELETE /api/admin/catalog/aliases/<pk>/          products.manage
GET    /api/admin/catalog/vocabulary/            products.view    model words, models, options by role (ETag)
POST   /api/admin/catalog/resolve/               products.view    {rows: [{key, model_text, line?, capacity_text?,
                                                                   colour_text?, sim_text?, region_text?}]}

The company is the caller's, resolved on the server; an alias, a model or an option of another company
answers 404, like one that does not exist. Writes are throttled and audited with the company. `resolve`
is READ-ONLY: no audit, no row written; an amount, a quantity, a cost, an IMEI or pasted text is refused
(400), never carried; a model or colour holding an identifier is not searched nor echoed back.
"""
from __future__ import annotations

from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from . import catalog_aliases, configuration_resolver, device_catalog
from .admin_views import (
    CAP_PRODUCTS_MANAGE, CAP_PRODUCTS_VIEW, _LEGACY_MANAGE_CATALOG_ROLES, _LEGACY_VIEW_CATALOG_ROLES,
    AdminProductWriteThrottle, _company_context,
)
from .models import CatalogAlias
from .text_preview_match import IDENTIFIER_REASON, has_identifier
from .throttles import AdminOrdersThrottle

_NOT_FOUND = {'detail': 'No encontrado.'}
MAX_ROWS = 300
#: Every field a row may carry, with its length. Anything else is refused by name (400).
ROW_FIELDS = {'key': 40, 'model_text': 160, 'line': 20, 'capacity_text': 60, 'colour_text': 120, 'sim_text': 60,
              'region_text': 60}


def _view(request):
    return _company_context(request, CAP_PRODUCTS_VIEW, _LEGACY_VIEW_CATALOG_ROLES)


def _manage(request):
    return _company_context(request, CAP_PRODUCTS_MANAGE, _LEGACY_MANAGE_CATALOG_ROLES)


def _error(exc: device_catalog.CatalogError) -> Response:
    code = status.HTTP_404_NOT_FOUND if exc.code == 'not_found' else status.HTTP_400_BAD_REQUEST
    return Response(exc.body(), status=code)


def _bad(detail, field=None) -> Response:
    body = {'detail': detail, 'code': 'invalid'}
    if field:
        body['field'] = field
    return Response(body, status=status.HTTP_400_BAD_REQUEST)


class _Writes(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get_throttles(self):
        return [AdminProductWriteThrottle()] if self.request.method not in ('GET', 'HEAD', 'OPTIONS') else []


class AdminCatalogAliasListView(_Writes):
    def get(self, request):
        company, error = _view(request)
        if error:
            return error
        try:
            rows = catalog_aliases.list_aliases(company, scope=request.query_params.get('scope') or '',
                                                q=(request.query_params.get('q') or '')[:60])
        except device_catalog.CatalogError as exc:
            return _error(exc)
        return Response({'results': rows, 'count': len(rows)})

    def post(self, request):
        company, error = _manage(request)
        if error:
            return error
        if not isinstance(request.data, dict):
            return _bad('Envía un objeto.')
        data = request.data
        try:
            row = catalog_aliases.create_alias(
                company=company, scope=data.get('scope'), alias=data.get('alias'), model_id=data.get('model_id'),
                option_id=data.get('option_id'), replacement=data.get('replacement'), actor=request.user,
                request=request)
        except device_catalog.CatalogError as exc:
            return _error(exc)
        return Response(catalog_aliases.payload(row), status=status.HTTP_201_CREATED)


class AdminCatalogAliasDetailView(_Writes):
    def _row(self, company, pk):
        return (CatalogAlias.objects.filter(company=company, pk=pk)
                .select_related('company', 'model', 'option__attribute').first())

    def patch(self, request, pk):
        company, error = _manage(request)
        if error:
            return error
        row = self._row(company, pk)
        if row is None:
            return Response(_NOT_FOUND, status=status.HTTP_404_NOT_FOUND)
        if not isinstance(request.data, dict):
            return _bad('Envía un objeto.')
        data = request.data
        if 'scope' in data and data.get('scope') != row.scope:
            return _bad('El ámbito de un alias no cambia: bórralo y crea otro.', 'scope')
        try:
            row = catalog_aliases.update_alias(
                row, alias=data.get('alias'), model_id=data.get('model_id'), option_id=data.get('option_id'),
                replacement=data.get('replacement'), actor=request.user, request=request)
        except device_catalog.CatalogError as exc:
            return _error(exc)
        return Response(catalog_aliases.payload(row))

    def delete(self, request, pk):
        company, error = _manage(request)
        if error:
            return error
        row = self._row(company, pk)
        if row is None:
            return Response(_NOT_FOUND, status=status.HTTP_404_NOT_FOUND)
        catalog_aliases.delete_alias(row, actor=request.user, request=request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class AdminCatalogVocabularyView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        company, error = _view(request)
        if error:
            return error
        body, etag = catalog_aliases.vocabulary_payload(company)
        if request.headers.get('If-None-Match') == etag:
            response = Response(status=status.HTTP_304_NOT_MODIFIED)
        else:
            response = Response(body)
        response['ETag'] = etag
        response['Cache-Control'] = 'private, no-cache'
        return response


def _clean_rows(rows):
    """[(key, fields)] or a 400 message. Unknown fields (an amount, a quantity, an IMEI…) are refused by name."""
    if not isinstance(rows, list) or not rows or len(rows) > MAX_ROWS:
        return None, f'Envía entre 1 y {MAX_ROWS} filas.'
    out, keys = [], set()
    for raw in rows:
        if not isinstance(raw, dict):
            return None, 'Cada fila es un objeto.'
        extra = set(raw) - set(ROW_FIELDS)
        if extra:
            return None, f'Campos no admitidos: {", ".join(sorted(extra))[:80]}.'
        fields = {}
        for name, limit in ROW_FIELDS.items():
            value = raw.get(name)
            if value is None:
                value = ''
            if not isinstance(value, str) or len(value) > limit:
                return None, f'«{name}» no es válido (texto de hasta {limit} caracteres).'
            fields[name] = ' '.join(value.split())
        if not fields['key']:
            return None, 'Falta «key».'
        if fields['key'] in keys:
            return None, 'Hay claves repetidas.'
        keys.add(fields['key'])
        out.append(fields)
    return out, None


class AdminCatalogResolveView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminOrdersThrottle]

    def post(self, request):
        company, error = _view(request)
        if error:
            return error
        body = request.data if isinstance(request.data, dict) else {}
        extra = set(body) - {'rows'}
        if extra:
            return _bad(f'Campos no admitidos: {", ".join(sorted(extra))[:80]}.')
        rows, problem = _clean_rows(body.get('rows'))
        if problem:
            return _bad(problem, 'rows')
        vocabulary = configuration_resolver.Vocabulary(company)
        results, summary = [], {name: 0 for name in configuration_resolver.STATUSES}
        for fields in rows:
            key = fields.pop('key')
            if has_identifier(fields['model_text']) or has_identifier(fields['colour_text']):
                results.append({'key': key, 'model': None, 'line': None, 'configurations': [], 'readings': [],
                                'error': IDENTIFIER_REASON})
                continue
            result = configuration_resolver.resolve_configuration(vocabulary, **fields)
            for config in result['configurations']:
                summary[config['status']] += 1
            results.append({'key': key, **result})
        return Response({'rows': results, 'summary': summary})
