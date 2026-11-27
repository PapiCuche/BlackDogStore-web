"""
SUNAT-SERIES-PANEL (C3) — Configuración › Series fiscales: the series a company issues with.

    GET    /api/admin/fiscal-series/?company=<id>   the companies, and the series of one
    POST   /api/admin/fiscal-series/                create one
    PATCH  /api/admin/fiscal-series/<id>/           activate / deactivate; `next_number` while unused
    DELETE /api/admin/fiscal-series/<id>/           only a series nothing was ever issued with

PLATFORM MASTERS ONLY (`IsPlatformAdmin`), like the SUNAT console: which series a company
declares to SUNAT is tax configuration, not something a company role decides. Until now a
series could only be created from Django's admin (off in production) or a seeding command.

The rules are the ones issuing already depends on, enforced where the series is written:

- the name is SUNAT's (Anexo N.º 6, 1.8.2026): four characters, the first `F` for a factura
  and its notes, `B` for a boleta and its notes, then three letters or digits;
- BETA and PRODUCTION are separate rows: a production series is created as such, never
  relabelled from BETA (the uniqueness already includes the environment);
- one ACTIVE series per company or branch, document type, environment (and note prefix):
  `fiscal_config.resolve_series` refuses to choose between two, so two are not allowed in;
- `next_number` is set when the series is created and may be corrected only while nothing
  has been issued with it. After the first document it belongs to `_reserve`, under lock;
- a series that was used is never deleted (`PROTECT` keeps it too): it is deactivated.

Every change leaves its line in `AdminAuditLog`, under the company.
"""
import re

from django.db import IntegrityError, transaction
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import (
    FISCAL_NOTE_TYPES, AdminAuditLog, Branch, Company, FiscalDocument, FiscalDocumentType, FiscalEnvironment,
    FiscalSeries,
)
from .permissions import IsPlatformAdmin
from .throttles import AdminUsersThrottle

#: The correlativo of a document is at most eight digits (Anexo N.º 1, campo 8).
MAX_NUMBER = 99_999_999
_NAME = re.compile(r'^[FB][A-Z0-9]{3}$')
#: The letter each type's series starts with. Notes take the letter of what they modify.
_LETTERS = {
    FiscalDocumentType.INVOICE: ('F',), FiscalDocumentType.RECEIPT: ('B',),
    FiscalDocumentType.CREDIT_NOTE: ('F', 'B'), FiscalDocumentType.DEBIT_NOTE: ('F', 'B'),
}
_NOUN = {
    FiscalDocumentType.INVOICE: 'factura', FiscalDocumentType.RECEIPT: 'boleta',
    FiscalDocumentType.CREDIT_NOTE: 'nota de crédito', FiscalDocumentType.DEBIT_NOTE: 'nota de débito',
}


class _Invalid(Exception):
    def __init__(self, errors: dict):
        super().__init__('Revisa los campos señalados.')
        self.errors = errors


def _used(series: FiscalSeries) -> bool:
    return FiscalDocument.objects.filter(series_ref=series).exists()


def _describe(series: FiscalSeries, used: bool | None = None) -> dict:
    used = _used(series) if used is None else used
    return {
        'id': series.pk, 'company': series.company_id,
        'branch': {'id': series.branch_id, 'name': series.branch.name} if series.branch_id else None,
        'document_type': series.document_type, 'document_label': series.get_document_type_display(),
        'series': series.series, 'environment': series.environment,
        'next_number': series.next_number, 'is_active': series.is_active,
        'used': used, 'next_number_editable': not used, 'deletable': not used,
        'created_at': series.created_at.isoformat() if series.created_at else None,
    }


def _number(raw, errors: dict):
    if isinstance(raw, bool) or not isinstance(raw, (int, str)):
        errors['next_number'] = 'Tiene que ser un número entero.'
        return None
    try:
        value = int(str(raw).strip())
    except ValueError:
        errors['next_number'] = 'Tiene que ser un número entero.'
        return None
    if not 1 <= value <= MAX_NUMBER:
        errors['next_number'] = f'Va de 1 a {MAX_NUMBER}.'
        return None
    return value


def _conflict(company_id, branch_id, document_type, environment, name, *, exclude=None) -> FiscalSeries | None:
    """The other ACTIVE series `resolve_series` would have to choose between, or None."""
    rows = FiscalSeries.objects.filter(
        company_id=company_id, branch_id=branch_id, document_type=document_type, environment=environment,
        is_active=True)
    if document_type in FISCAL_NOTE_TYPES:
        rows = rows.filter(series__startswith=name[:1])
    if exclude is not None:
        rows = rows.exclude(pk=exclude)
    return rows.order_by('pk').first()


def _conflict_message(other: FiscalSeries) -> str:
    where = f'la sucursal «{other.branch.name}»' if other.branch_id else 'la empresa'
    return (f'Ya hay una serie de {_NOUN.get(other.document_type, "comprobante")} activa en {where} para '
            f'«{other.environment}» ({other.series}). Desactívala primero: con dos activas no se emite.')


def _audit(request, action, series: FiscalSeries, **metadata):
    AdminAuditLog.log(
        actor=request.user, action=action, target_type='fiscal_series', target_id=series.pk,
        metadata={'series': series.series, 'document_type': series.document_type,
                  'environment': series.environment, 'branch': series.branch_id, **metadata},
        request=request, company=series.company)


class _SeriesView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsPlatformAdmin]
    throttle_classes = [AdminUsersThrottle]

    def handle_exception(self, exc):
        if isinstance(exc, _Invalid):
            return Response({'detail': str(exc), 'errors': exc.errors}, status=status.HTTP_400_BAD_REQUEST)
        return super().handle_exception(exc)

    @staticmethod
    def body(request) -> dict:
        if not isinstance(request.data, dict):
            raise _Invalid({'__all__': 'El cuerpo de la petición tiene que ser un objeto JSON.'})
        return request.data


class FiscalSeriesListView(_SeriesView):
    http_method_names = ['get', 'post', 'options']

    def get(self, request):
        companies = [{'id': c.pk, 'name': c.name, 'slug': c.slug, 'tax_id': c.tax_id}
                     for c in Company.objects.filter(is_active=True).order_by('name')]
        data = {
            'companies': companies,
            'document_types': [{'value': v, 'label': label, 'letters': list(_LETTERS[v])}
                               for v, label in FiscalDocumentType.choices if v in _LETTERS],
            'environments': [{'value': v, 'label': label} for v, label in FiscalEnvironment.choices],
            'company': None, 'branches': [], 'series': [],
        }
        raw = request.query_params.get('company')
        if raw:
            company = Company.objects.filter(pk=int(raw) if str(raw).isdigit() else 0).first()
            if company is None:
                return Response({'detail': 'No encontrado.'}, status=status.HTTP_404_NOT_FOUND)
            used_ids = set(FiscalDocument.objects.filter(company=company).values_list('series_ref_id', flat=True)
                           .distinct())
            rows = (FiscalSeries.objects.filter(company=company).select_related('branch')
                    .order_by('environment', 'document_type', 'branch__name', 'series'))
            data.update(
                company={'id': company.pk, 'name': company.name, 'slug': company.slug, 'tax_id': company.tax_id},
                branches=[{'id': b.pk, 'name': b.name}
                          for b in Branch.objects.filter(company=company, is_active=True).order_by('name')],
                series=[_describe(row, row.pk in used_ids) for row in rows],
            )
        return Response(data)

    def post(self, request):
        data = self.body(request)
        errors = {}
        company = Company.objects.filter(pk=data.get('company') if isinstance(data.get('company'), int) else 0,
                                         is_active=True).first()
        if company is None:
            errors['company'] = 'Elige una empresa activa.'
        branch = None
        if data.get('branch') not in (None, ''):
            branch = Branch.objects.filter(
                pk=data.get('branch') if isinstance(data.get('branch'), int) else 0,
                company=company, is_active=True).first() if company else None
            if branch is None:
                errors['branch'] = 'La sucursal no es de esta empresa o no está activa.'
        document_type = data.get('document_type')
        if document_type not in _LETTERS:
            errors['document_type'] = 'Elige factura, boleta, nota de crédito o nota de débito.'
        environment = data.get('environment')
        if environment not in FiscalEnvironment.values:
            errors['environment'] = 'Elige BETA o producción.'
        name = str(data.get('series') or '').strip().upper()
        if not _NAME.match(name):
            errors['series'] = 'Cuatro caracteres: F o B y tres letras o dígitos (por ejemplo F001).'
        elif document_type in _LETTERS and name[0] not in _LETTERS[document_type]:
            letters = ' o '.join(_LETTERS[document_type])
            errors['series'] = f'Una serie de {_NOUN[document_type]} empieza por {letters}.'
        number = _number(data.get('next_number', 1), errors)
        active = data.get('is_active', True)
        if not isinstance(active, bool):
            errors['is_active'] = 'Tiene que ser sí o no.'
        if errors:
            raise _Invalid(errors)

        with transaction.atomic():
            # The company row orders two creations for the same slot: the second sees the first.
            Company.objects.select_for_update().get(pk=company.pk)
            duplicate = _Invalid({'series': (f'La empresa ya tiene la serie {name} de {_NOUN[document_type]} en '
                                             f'«{environment}».')})
            if FiscalSeries.objects.filter(company=company, document_type=document_type, series=name,
                                           environment=environment).exists():
                raise duplicate
            if active:
                other = _conflict(company.pk, branch.pk if branch else None, document_type, environment, name)
                if other is not None:
                    raise _Invalid({'is_active': _conflict_message(other)})
            try:
                with transaction.atomic():
                    series = FiscalSeries.objects.create(
                        company=company, branch=branch, document_type=document_type, series=name,
                        environment=environment, next_number=number, is_active=active)
            except IntegrityError:
                raise duplicate from None
            _audit(request, 'fiscal_series_created', series, next_number=number, is_active=active)
        return Response(_describe(series, False), status=status.HTTP_201_CREATED)


class FiscalSeriesDetailView(_SeriesView):
    http_method_names = ['patch', 'delete', 'options']

    def patch(self, request, pk):
        data = self.body(request)
        unknown = sorted(set(data) - {'is_active', 'next_number'})
        if unknown:
            raise _Invalid({name: 'Este campo no se cambia aquí: crea otra serie.' for name in unknown})
        with transaction.atomic():
            series = (FiscalSeries.objects.select_for_update(of=('self',)).select_related('branch', 'company')
                      .filter(pk=pk).first())
            if series is None:
                return Response({'detail': 'No encontrado.'}, status=status.HTTP_404_NOT_FOUND)
            used = _used(series)
            changes, errors = {}, {}
            if 'next_number' in data:
                number = _number(data['next_number'], errors)
                if used:
                    errors['next_number'] = ('Ya se emitió con esta serie: su correlativo lo lleva el sistema y no '
                                             'se edita.')
                elif number is not None and number != series.next_number:
                    changes['next_number'] = (series.next_number, number)
            if 'is_active' in data:
                if not isinstance(data['is_active'], bool):
                    errors['is_active'] = 'Tiene que ser sí o no.'
                elif data['is_active'] != series.is_active:
                    if data['is_active']:
                        other = _conflict(series.company_id, series.branch_id, series.document_type,
                                          series.environment, series.series, exclude=series.pk)
                        if other is not None:
                            errors['is_active'] = _conflict_message(other)
                    changes['is_active'] = (series.is_active, data['is_active'])
            if errors:
                raise _Invalid(errors)
            for field, (_before, after) in changes.items():
                setattr(series, field, after)
            if changes:
                series.save(update_fields=[*changes, 'updated_at'])
                _audit(request, 'fiscal_series_updated', series,
                       changes={field: {'from': before, 'to': after} for field, (before, after) in changes.items()})
        return Response(_describe(series, used))

    def delete(self, request, pk):
        with transaction.atomic():
            series = FiscalSeries.objects.select_for_update(of=('self',)).select_related('company').filter(pk=pk).first()
            if series is None:
                return Response({'detail': 'No encontrado.'}, status=status.HTTP_404_NOT_FOUND)
            if _used(series):
                return Response({'detail': 'Ya se emitió con esta serie: no se borra. Desactívala.'},
                                status=status.HTTP_409_CONFLICT)
            _audit(request, 'fiscal_series_deleted', series, next_number=series.next_number)
            series.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
