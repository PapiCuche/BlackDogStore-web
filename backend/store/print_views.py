"""
La cola de impresión, por sus dos puertas.

LA DEL AGENTE (`/api/v1/print-agent/…`). No hay usuario: hay un token que vale
para UNA sucursal. Con él se recogen trabajos y se confirma cómo salieron, y
nada más. No da acceso a ningún dato de venta: lo que el agente recibe son los
bytes del ticket y la dirección de la impresora.

LA DEL PANEL (`/api/admin/printing/…`). Dar de alta impresoras y agentes es
configurar la empresa (`company.manage`). Ver la cola y reimprimir es operar la
caja (`sales.pos.use` o `sales.orders.view`). En ambos casos sólo dentro de las
sucursales que quien pide ya alcanza.
"""
from __future__ import annotations

import base64
import ipaddress
import logging
import re

from django.db import IntegrityError, transaction
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from .models import AdminAuditLog, Order, PrintAgent, Printer, PrintJob
from .printing import services as printing
from .settings_views import _settings_context
from .tenancy import has_capability, visible_branches
from .throttles import AdminUsersThrottle

logger = logging.getLogger(__name__)

CAP_CONFIGURE = 'company.manage'
CAP_OPERATE = ('sales.pos.use', 'sales.orders.view')
_NOT_FOUND = 'No encontrado.'
_LOCAL_NAME = re.compile(r'^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.local$', re.IGNORECASE)


class PrintAgentThrottle(AnonRateThrottle):
    """Por dirección. Un agente pregunta cada pocos segundos; esto deja margen."""
    scope = 'print_agent'


# ---------------------------------------------------------------------------
# La puerta del agente
# ---------------------------------------------------------------------------

def _agent(request):
    header = request.META.get('HTTP_AUTHORIZATION', '')
    scheme, _, token = header.partition(' ')
    if scheme != 'PrintAgent':
        return None
    return printing.authenticate_agent(token.strip())


def _unauthorised():
    return Response({'detail': 'Agente no autorizado.'}, status=status.HTTP_401_UNAUTHORIZED)


class PrintAgentClaimView(APIView):
    """POST /api/v1/print-agent/jobs/claim/ — el siguiente trabajo de SU sucursal."""

    authentication_classes = []
    permission_classes = [permissions.AllowAny]
    throttle_classes = [PrintAgentThrottle]

    def post(self, request):
        agent = _agent(request)
        if agent is None:
            return _unauthorised()

        # Un documento que ya no se puede dibujar no puede atascar la cola: se
        # marca como fallido y se entrega el siguiente.
        for _ in range(10):
            job = printing.claim_next(agent)
            if job is None:
                return Response({'job': None})
            try:
                payload = printing.render(job)
            except Exception as exc:  # noqa: BLE001
                logger.warning('Trabajo de impresión %s imposible de dibujar: %s', job.pk, exc)
                PrintJob.objects.filter(pk=job.pk).update(
                    status=PrintJob.Status.FAILED, claim_token='', lease_expires_at=None,
                    last_error='El documento no se pudo preparar para la impresora.')
                continue
            return Response({'job': {
                'id': job.pk,
                'claim_token': job.claim_token,
                'kind': job.kind,
                'attempt': job.attempts,
                'printer': {
                    'name': job.printer.name, 'host': job.printer.host, 'port': job.printer.port,
                },
                'payload_base64': base64.b64encode(payload).decode('ascii'),
                'lease_seconds': printing.LEASE_SECONDS,
            }})
        return Response({'job': None})


class PrintAgentResultView(APIView):
    """POST /api/v1/print-agent/jobs/<id>/result/ — cómo terminó una entrega."""

    authentication_classes = []
    permission_classes = [permissions.AllowAny]
    throttle_classes = [PrintAgentThrottle]

    def post(self, request, pk):
        agent = _agent(request)
        if agent is None:
            return _unauthorised()
        data = request.data if isinstance(request.data, dict) else {}
        try:
            job = printing.complete(
                agent, pk, str(data.get('claim_token') or ''),
                ok=data.get('ok') is True, error=str(data.get('error') or ''))
        except printing.PrintNotFound:
            return Response({'detail': _NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)
        except printing.PrintConflict as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_409_CONFLICT)
        return Response({'id': job.pk, 'status': job.status})


# ---------------------------------------------------------------------------
# La puerta del panel
# ---------------------------------------------------------------------------

def _context(request, capabilities):
    """(company, error). `capabilities`: basta con tener una."""
    if isinstance(capabilities, str):
        capabilities = (capabilities,)
    company, _row, error = _settings_context(request, capabilities[0])
    if error is not None and error.status_code == status.HTTP_403_FORBIDDEN and len(capabilities) > 1:
        for capability in capabilities[1:]:
            company, _row, error = _settings_context(request, capability)
            if error is None:
                break
    return company, error


def _branch(request, company, raw):
    """La sucursal `raw`, sólo si es de esta empresa y quien pide la alcanza."""
    try:
        return visible_branches(request.user, company).filter(pk=int(raw)).first()
    except (TypeError, ValueError):
        return None


def _local_host(value) -> str | None:
    """
    Una dirección de la red del local, o None.

    El agente abre una conexión hacia lo que aquí se guarde. Por eso sólo se
    admite una dirección privada o un nombre `.local`: una cuenta del panel no
    debe poder apuntar los agentes de las tiendas hacia Internet.
    """
    host = str(value or '').strip()
    if _LOCAL_NAME.match(host):
        return host.lower()
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return None
    if address.is_private and not address.is_loopback and not address.is_unspecified:
        return str(address)
    return None


def _printer_payload(printer: Printer) -> dict:
    return {
        'id': printer.pk, 'branch': printer.branch_id, 'branch_name': printer.branch.name,
        'name': printer.name, 'host': printer.host, 'port': printer.port,
        'paper_width_mm': printer.paper_width_mm, 'encoding': printer.encoding,
        'auto_print': printer.auto_print, 'is_active': printer.is_active,
    }


def _printer_fields(request, company, data, *, partial=False):
    """(campos validados, errores). Nunca toma la empresa del cuerpo."""
    fields, errors = {}, {}
    if 'branch' in data or not partial:
        branch = _branch(request, company, data.get('branch'))
        if branch is None:
            errors['branch'] = ['Sucursal no encontrada o sin acceso.']
        else:
            fields['branch'] = branch
    if 'name' in data or not partial:
        name = str(data.get('name') or '').strip()
        if not name:
            errors['name'] = ['Ponle un nombre a la impresora.']
        fields['name'] = name[:120]
    if 'host' in data or not partial:
        host = _local_host(data.get('host'))
        if host is None:
            errors['host'] = ['Escribe la dirección de la impresora en la red del local '
                              '(por ejemplo 192.168.1.50).']
        else:
            fields['host'] = host
    if 'port' in data:
        try:
            port = int(data['port'])
        except (TypeError, ValueError):
            port = 0
        if not 1 <= port <= 65535:
            errors['port'] = ['Puerto inválido.']
        fields['port'] = port
    if 'paper_width_mm' in data:
        if data['paper_width_mm'] not in (58, 80):
            errors['paper_width_mm'] = ['El rollo es de 58 u 80 mm.']
        fields['paper_width_mm'] = data['paper_width_mm']
    if 'encoding' in data:
        from .printing.escpos import CODE_PAGES

        if data['encoding'] not in CODE_PAGES:
            errors['encoding'] = ['Página de códigos no admitida.']
        fields['encoding'] = data['encoding']
    for flag in ('auto_print', 'is_active'):
        if flag in data:
            if not isinstance(data[flag], bool):
                errors[flag] = ['Debe ser verdadero o falso.']
            fields[flag] = data[flag]
    return fields, errors


_ONE_AUTO = {'auto_print': ['Este local ya tiene una impresora automática. '
                            'Desactívala primero o crea ésta sin impresión automática.']}


class AdminPrinterListView(APIView):
    """GET, POST /api/admin/printing/printers/?company=<id>"""

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminUsersThrottle]

    def get(self, request):
        company, error = _context(request, (CAP_CONFIGURE, *CAP_OPERATE))
        if error:
            return error
        printers = Printer.objects.select_related('branch').filter(
            company=company, branch__in=visible_branches(request.user, company))
        return Response({'results': [_printer_payload(p) for p in printers]})

    def post(self, request):
        company, error = _context(request, CAP_CONFIGURE)
        if error:
            return error
        data = request.data if isinstance(request.data, dict) else {}
        fields, errors = _printer_fields(request, company, data)
        if errors:
            return Response(errors, status=status.HTTP_400_BAD_REQUEST)
        try:
            with transaction.atomic():
                printer = Printer.objects.create(company=company, **fields)
        except IntegrityError:
            return Response(_ONE_AUTO if fields.get('auto_print', True) else {
                'name': ['Ya hay una impresora con ese nombre en este local.']},
                status=status.HTTP_400_BAD_REQUEST)
        AdminAuditLog.log(
            actor=request.user, action='printer_created', target_type='printer',
            target_id=printer.pk, metadata={'branch_id': printer.branch_id},
            request=request, company=company)
        return Response(_printer_payload(printer), status=status.HTTP_201_CREATED)


class AdminPrinterDetailView(APIView):
    """PATCH, DELETE /api/admin/printing/printers/<id>/?company=<id>"""

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminUsersThrottle]

    def _printer(self, request, company, pk):
        return Printer.objects.select_related('branch').filter(
            pk=pk, company=company, branch__in=visible_branches(request.user, company)).first()

    def patch(self, request, pk):
        company, error = _context(request, CAP_CONFIGURE)
        if error:
            return error
        printer = self._printer(request, company, pk)
        if printer is None:
            return Response({'detail': _NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)
        data = request.data if isinstance(request.data, dict) else {}
        fields, errors = _printer_fields(request, company, data, partial=True)
        if errors:
            return Response(errors, status=status.HTTP_400_BAD_REQUEST)
        for name, value in fields.items():
            setattr(printer, name, value)
        try:
            with transaction.atomic():
                printer.save()
        except IntegrityError:
            return Response(_ONE_AUTO, status=status.HTTP_400_BAD_REQUEST)
        AdminAuditLog.log(
            actor=request.user, action='printer_updated', target_type='printer',
            target_id=printer.pk, metadata={'changed_fields': sorted(fields)},
            request=request, company=company)
        return Response(_printer_payload(printer))

    def delete(self, request, pk):
        """No se borra: se desactiva. Sus trabajos impresos siguen siendo historia."""
        company, error = _context(request, CAP_CONFIGURE)
        if error:
            return error
        printer = self._printer(request, company, pk)
        if printer is None:
            return Response({'detail': _NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)
        printer.is_active = False
        printer.save(update_fields=['is_active', 'updated_at'])
        AdminAuditLog.log(
            actor=request.user, action='printer_deactivated', target_type='printer',
            target_id=printer.pk, metadata={}, request=request, company=company)
        return Response(status=status.HTTP_204_NO_CONTENT)


def _agent_payload(agent: PrintAgent) -> dict:
    return {
        'id': agent.pk, 'branch': agent.branch_id, 'branch_name': agent.branch.name,
        'name': agent.name, 'token_hint': agent.token_hint, 'is_active': agent.is_active,
        'last_seen_at': agent.last_seen_at,
    }


class AdminPrintAgentListView(APIView):
    """GET, POST /api/admin/printing/agents/?company=<id>"""

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminUsersThrottle]

    def get(self, request):
        company, error = _context(request, CAP_CONFIGURE)
        if error:
            return error
        agents = PrintAgent.objects.select_related('branch').filter(
            company=company, branch__in=visible_branches(request.user, company))
        return Response({'results': [_agent_payload(a) for a in agents]})

    def post(self, request):
        company, error = _context(request, CAP_CONFIGURE)
        if error:
            return error
        data = request.data if isinstance(request.data, dict) else {}
        branch = _branch(request, company, data.get('branch'))
        name = str(data.get('name') or '').strip()
        errors = {}
        if branch is None:
            errors['branch'] = ['Sucursal no encontrada o sin acceso.']
        if not name:
            errors['name'] = ['Ponle un nombre al agente.']
        if errors:
            return Response(errors, status=status.HTTP_400_BAD_REQUEST)
        agent, token = printing.create_agent(
            company=company, branch=branch, name=name, actor=request.user)
        AdminAuditLog.log(
            actor=request.user, action='print_agent_created', target_type='print_agent',
            target_id=agent.pk, metadata={'branch_id': branch.pk},
            request=request, company=company)
        # EL TOKEN SALE AQUÍ Y SÓLO AQUÍ. En la base queda su huella.
        return Response({**_agent_payload(agent), 'token': token}, status=status.HTTP_201_CREATED)


class AdminPrintAgentDetailView(APIView):
    """DELETE /api/admin/printing/agents/<id>/?company=<id> — lo revoca."""

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminUsersThrottle]

    def delete(self, request, pk):
        company, error = _context(request, CAP_CONFIGURE)
        if error:
            return error
        agent = PrintAgent.objects.filter(
            pk=pk, company=company, branch__in=visible_branches(request.user, company)).first()
        if agent is None:
            return Response({'detail': _NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)
        agent.is_active = False
        agent.save(update_fields=['is_active'])
        AdminAuditLog.log(
            actor=request.user, action='print_agent_revoked', target_type='print_agent',
            target_id=agent.pk, metadata={}, request=request, company=company)
        return Response(status=status.HTTP_204_NO_CONTENT)


def _job_payload(job: PrintJob) -> dict:
    return {
        'id': job.pk, 'kind': job.kind, 'reason': job.reason, 'status': job.status,
        'order': job.order_id, 'branch': job.branch_id,
        'printer': job.printer_id, 'printer_name': job.printer.name,
        'attempts': job.attempts, 'last_error': job.last_error,
        'created_at': job.created_at, 'printed_at': job.printed_at,
    }


class AdminPrintJobListView(APIView):
    """
    GET  /api/admin/printing/jobs/?company=<id>[&order=<id>] — la cola reciente.
    POST /api/admin/printing/jobs/?company=<id> — reimprimir un pedido pagado.
         Cuerpo: {"order": id[, "printer": id]}. Cabecera `Idempotency-Key`.
    """

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminUsersThrottle]

    def get(self, request):
        company, error = _context(request, CAP_OPERATE)
        if error:
            return error
        jobs = PrintJob.objects.select_related('printer').filter(
            company=company, branch__in=visible_branches(request.user, company))
        order = request.query_params.get('order')
        if order and order.isdigit():
            jobs = jobs.filter(order_id=int(order))
        return Response({'results': [_job_payload(j) for j in jobs.order_by('-pk')[:50]]})

    def post(self, request):
        company, error = _context(request, CAP_OPERATE)
        if error:
            return error
        data = request.data if isinstance(request.data, dict) else {}
        branches = visible_branches(request.user, company)
        try:
            order = Order.objects.filter(
                pk=int(data.get('order')), company=company,
                fulfillment_branch__in=branches).first()
        except (TypeError, ValueError):
            order = None
        if order is None:
            return Response({'detail': _NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)

        printers = Printer.objects.filter(
            company=company, branch=order.fulfillment_branch, is_active=True)
        if data.get('printer') not in (None, ''):
            try:
                printer = printers.filter(pk=int(data['printer'])).first()
            except (TypeError, ValueError):
                printer = None
        else:
            printer = printers.filter(auto_print=True).first() or printers.first()
        if printer is None:
            return Response(
                {'detail': 'Este local no tiene una impresora disponible.'},
                status=status.HTTP_400_BAD_REQUEST)

        key = (request.headers.get('Idempotency-Key') or '').strip()
        if not key:
            return Response(
                {'detail': 'Falta la cabecera Idempotency-Key.'},
                status=status.HTTP_400_BAD_REQUEST)
        existed = PrintJob.objects.filter(
            company=company, idempotency_key=f'manual:{key}'[:120]).exists()
        try:
            job = printing.enqueue_manual(order=order, printer=printer, key=key, actor=request.user)
        except printing.PrintError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        if not existed:
            AdminAuditLog.log(
                actor=request.user, action='print_job_requested', target_type='print_job',
                target_id=job.pk, metadata={'order_id': order.pk, 'printer_id': printer.pk},
                request=request, company=company)
        return Response(
            _job_payload(job),
            status=status.HTTP_200_OK if existed else status.HTTP_201_CREATED)


class AdminPrintJobRetryView(APIView):
    """POST /api/admin/printing/jobs/<id>/retry/?company=<id>"""

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminUsersThrottle]

    def post(self, request, pk):
        company, error = _context(request, CAP_OPERATE)
        if error:
            return error
        job = PrintJob.objects.select_related('printer', 'order').filter(
            pk=pk, company=company, branch__in=visible_branches(request.user, company)).first()
        if job is None:
            return Response({'detail': _NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)
        try:
            job = printing.retry(job, actor=request.user)
        except printing.PrintError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        AdminAuditLog.log(
            actor=request.user, action='print_job_retried', target_type='print_job',
            target_id=job.pk, metadata={'order_id': job.order_id},
            request=request, company=company)
        return Response(_job_payload(job))
