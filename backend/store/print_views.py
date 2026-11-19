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
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from .models import AdminAuditLog, Order, PrintAgent, Printer, PrintJob
from .printing import services as printing
from .printing.escpos import PRINT_WIDTHS, PRINT_WIDTHS_BY_ROLL, printable_dots
from .settings_views import _settings_context
from .tenancy import has_capability, visible_branches
from .throttles import AdminUsersThrottle

logger = logging.getLogger(__name__)

CAP_CONFIGURE = 'company.manage'
CAP_OPERATE = ('sales.pos.use', 'sales.orders.view')
_NOT_FOUND = 'No encontrado.'
#: Lo que es una red de un local: las privadas de IPv4, el enlace local, y sus
#: equivalentes de IPv6. Nada más.
_LOCAL_NETWORKS = tuple(ipaddress.ip_network(n) for n in (
    '10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '169.254.0.0/16',
    'fc00::/7', 'fe80::/10',
))
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
        # WIN-003C: lo que el agente sabe imprimir. Sólo filtra dentro de la
        # empresa y la sucursal de SU token; un agente anterior no dice nada y
        # recibe, como siempre, sólo impresoras de red.
        try:
            connections, printer_ids = printing.parse_claim(request.data)
        except printing.PrintError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        # Un documento que ya no se puede dibujar no puede atascar la cola: se
        # marca como fallido y se entrega el siguiente.
        for _ in range(10):
            job = printing.claim_next(agent, connections, printer_ids)
            if job is None:
                return Response({'job': None})
            try:
                payload = printing.render(job)
            except Exception:  # noqa: BLE001
                logger.exception('Trabajo de impresión %s: no se pudo preparar.', job.pk)
                printing.release_unrenderable(
                    job, 'El documento no se pudo preparar para la impresora.')
                continue
            return Response({'job': {
                'id': job.pk,
                'uid': job.uid.hex,
                'claim_token': job.claim_token,
                'kind': job.kind,
                'attempt': job.attempts,
                'printer': {
                    'name': job.printer.name, 'host': job.printer.host, 'port': job.printer.port,
                    # WIN-003C, aditivo: el id estable con el que el agente busca
                    # la impresora en SU configuración, y cómo se llega a ella.
                    'id': job.printer.pk, 'connection': job.printer.connection,
                    # LABEL-04, aditivo: un agente sólo manda un lote de etiquetas a una de etiquetas.
                    'role': job.printer.role,
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
        outcome = data.get('outcome')
        outcome = outcome if outcome in printing.FINAL_OUTCOMES else ''
        try:
            job = printing.complete(
                agent, pk, str(data.get('claim_token') or ''),
                ok=data.get('ok') is True, error=str(data.get('error') or '')[:1000],
                outcome=outcome)
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
    # REDES ESCRITAS A MANO, no `is_private`: lo que esa propiedad considera
    # privado cambia entre versiones de Python (una dirección IPv4 metida en
    # una IPv6, por ejemplo, pasaba por privada en algunas).
    if any(address in network for network in _LOCAL_NETWORKS):
        return str(address)
    return None


def _printer_payload(printer: Printer, agents=None) -> dict:
    """`agents`: los agentes en línea ya leídos (una consulta para toda la lista)."""
    if agents is None:
        agents = printing.online_agents(branch=printer.branch)
    dots = printable_dots(printer.paper_width_mm, printer.print_width_dots)
    return {
        'id': printer.pk, 'branch': printer.branch_id, 'branch_name': printer.branch.name,
        'name': printer.name, 'host': printer.host, 'port': printer.port,
        'paper_width_mm': printer.paper_width_mm, 'encoding': printer.encoding,
        'auto_print': printer.auto_print, 'is_active': printer.is_active,
        # THERMAL-512, aditivo: el ancho declarado (0 = según el rollo) y el que se imprime.
        'print_width_dots': printer.print_width_dots,
        'printable_dots': dots,
        'columns': PRINT_WIDTHS[dots],
        # WIN-003C, aditivo.
        'connection': printer.connection,
        'agent_online': printing.printer_has_agent(printer, agents),
        # LABEL-04, aditivo: tickets o etiquetas, y el rollo de etiquetas cargado.
        'role': printer.role,
        'label_roll': printer.label_roll,
    }


def _connection_fields(data, current=None):
    """
    (campos, errores) de `connection` y `host`, mirando cómo QUEDA la impresora.

    `network`: dirección de la red del local, obligatoria. `local_queue`: sin
    dirección; el nombre de la cola vive en el equipo del agente, no aquí.
    """
    fields, errors = {}, {}
    connection = data.get('connection', current.connection if current else Printer.Connection.NETWORK)
    if connection not in Printer.Connection.values:
        return {}, {'connection': ['Elige «red» o «en el equipo del agente».']}
    fields['connection'] = connection
    if connection == Printer.Connection.LOCAL_QUEUE:
        if str(data.get('host') or '').strip():
            errors['host'] = ['Una impresora del equipo del agente no lleva dirección de red.']
        fields['host'] = ''
        return fields, errors
    if 'host' in data or current is None or current.connection != connection:
        host = _local_host(data.get('host'))
        if host is None:
            errors['host'] = ['Escribe la dirección de la impresora en la red del local '
                              '(por ejemplo 192.168.1.50).']
        else:
            fields['host'] = host
    return fields, errors


def _printer_fields(request, company, data, *, partial=False, current=None):
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
    if 'host' in data or 'connection' in data or not partial:
        more, problems = _connection_fields(data, current)
        fields.update(more)
        errors.update(problems)
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
    if ('print_width_dots' in data or 'paper_width_mm' in data) and 'paper_width_mm' not in errors:
        # THERMAL-512: lo que la impresora imprime de ancho, mirando el rollo con el que QUEDA. Con un rollo
        # inválido no hay con qué compararlo: basta su error (revisión de #361: una lista era un 500).
        paper = fields.get('paper_width_mm', current.paper_width_mm if current else 80)
        width = data.get('print_width_dots', current.print_width_dots if current else 0)
        if type(width) is not int or width not in PRINT_WIDTHS_BY_ROLL.get(paper, ()):
            errors['print_width_dots'] = [
                'Ese ancho no es de este rollo: en 80 mm, 48 columnas (576 puntos) o 42 (512 puntos); '
                'en 58 mm, 32 columnas (384 puntos).']
        fields['print_width_dots'] = width
    if 'encoding' in data:
        from .printing.escpos import CODE_PAGES

        if not isinstance(data['encoding'], str) or data['encoding'] not in CODE_PAGES:
            errors['encoding'] = ['Página de códigos no admitida.']
        fields['encoding'] = data['encoding']
    for flag in ('auto_print', 'is_active'):
        if flag in data:
            if not isinstance(data[flag], bool):
                errors[flag] = ['Debe ser verdadero o falso.']
            fields[flag] = data[flag]
    # LABEL-04: el rol se elige al crearla y no cambia (sus trabajos son de ese tipo).
    if current is None:
        role = data.get('role', Printer.Role.TICKET)
        if role not in Printer.Role.values:
            errors['role'] = ['Elige «tickets» o «etiquetas».']
        fields['role'] = role
    elif 'role' in data and data['role'] != current.role:
        errors['role'] = ['El tipo de una impresora no se cambia: crea otra para el otro tipo.']
    role = fields.get('role', current.role if current else Printer.Role.TICKET)
    if 'label_roll' in data:
        roll = data['label_roll']
        if role != Printer.Role.LABEL and roll not in ('', None):
            errors['label_roll'] = ['Sólo una impresora de etiquetas tiene rollo de etiquetas.']
        elif roll not in ('', None) and (not isinstance(roll, str) or not _LABEL_ROLL.match(roll)):
            errors['label_roll'] = ['Escribe el tamaño del rollo como ancho x alto en mm, por ejemplo 30x20.']
        fields['label_roll'] = roll or ''
    return fields, errors


#: LABEL-04: el tamaño de un rollo de etiquetas, «30x20» (mm).
_LABEL_ROLL = re.compile(r'^(1\d|[2-9]\d|1\d\d|200)x(1\d|[2-9]\d|1\d\d|200)$')


_ONE_AUTO = {'auto_print': ['Este local ya tiene una impresora automática de este tipo (tickets o '
                            'etiquetas). Desactívala primero o crea ésta sin impresión automática.']}


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
        agents = printing.online_agents(company=company)
        return Response({'results': [_printer_payload(p, agents) for p in printers]})

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
            target_id=printer.pk,
            metadata={'branch_id': printer.branch_id, 'connection': printer.connection},
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
        fields, errors = _printer_fields(request, company, data, partial=True, current=printer)
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
        printing.deactivate_printer(printer)
        AdminAuditLog.log(
            actor=request.user, action='printer_deactivated', target_type='printer',
            target_id=printer.pk, metadata={}, request=request, company=company)
        return Response(status=status.HTTP_204_NO_CONTENT)


def _agent_payload(agent: PrintAgent) -> dict:
    recent = timezone.now() - timedelta(seconds=printing.AGENT_ONLINE_SECONDS)
    return {
        'id': agent.pk, 'branch': agent.branch_id, 'branch_name': agent.branch.name,
        'name': agent.name, 'token_hint': agent.token_hint, 'is_active': agent.is_active,
        'last_seen_at': agent.last_seen_at,
        # WIN-003C, aditivo: en línea ahora y qué clase de impresoras atiende.
        'online': bool(agent.is_active and agent.last_seen_at and agent.last_seen_at >= recent),
        'connections': list(agent.connections),
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
        # WIN-003C, aditivo: falló sin saberse si el papel salió, y el
        # identificador que se ve (se manda de vuelta al confirmar «no salió»).
        'uncertain': printing.is_uncertain(job),
        'uid': job.uid.hex,
        # LABEL-04, aditivo: un lote de etiquetas no tiene pedido; tiene su lote y su rollo.
        'label_batch': job.label_batch_id,
        'label_size': job.label_size,
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

        # LABEL-04: un ticket sólo va a una impresora de TICKETS del local.
        printers = Printer.objects.filter(
            company=company, branch=order.fulfillment_branch, is_active=True, role=Printer.Role.TICKET)
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
        except printing.PrintConflict as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_409_CONFLICT)
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
        data = request.data if isinstance(request.data, dict) else {}
        try:
            job = printing.retry(
                job, actor=request.user, confirm_not_printed=data.get('confirm_not_printed') is True,
                expected_uid=str(data.get('expected_uid') or '')[:64])
        except printing.PrintNotFound:
            return Response({'detail': _NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)
        except printing.PrintConflict as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_409_CONFLICT)
        except printing.PrintError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        AdminAuditLog.log(
            actor=request.user, action='print_job_retried', target_type='print_job',
            target_id=job.pk,
            # Quien confirma que un ticket incierto NO salió queda registrado; el
            # dato sale de la fila bloqueada, no de lo que se leyó antes.
            metadata={'order_id': job.order_id,
                      'confirmed_not_printed': bool(getattr(job, 'was_uncertain', False))},
            request=request, company=company)
        return Response(_job_payload(job))


class AdminLabelBatchPrintView(APIView):
    """
    POST /api/admin/printing/label-batches/<id>/print/ — LABEL-04: manda un lote de
    etiquetas a la impresora de etiquetas de su local (por ejemplo, tras cambiar el rollo).

    Cuerpo opcional: `printer` (una de etiquetas del local; si no, la automática o la única) y
    `size` («50x25»; si no, el rollo cargado). Cabecera `Idempotency-Key`: un doble clic no
    imprime dos veces. Un lote de otra empresa o de un local al que no se llega es 404.
    """

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminUsersThrottle]

    def post(self, request, pk):
        from .models import LabelBatch
        from .unit_codes import CAP_LABELS_PRINT

        # La empresa sale del lote (el «siguiente paso» de un ingreso no la sabe), y quien pide tiene
        # que poder imprimir etiquetas EN ella y llegar a su local: si no, es como si no existiera.
        batch = LabelBatch.objects.select_related('branch', 'company').filter(pk=pk).first()
        if batch is None or not has_capability(request.user, batch.company, CAP_LABELS_PRINT)                 or not visible_branches(request.user, batch.company).filter(pk=batch.branch_id).exists():
            return Response({'detail': _NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)
        company = batch.company
        data = request.data if isinstance(request.data, dict) else {}
        printers = Printer.objects.filter(
            company=company, branch=batch.branch, is_active=True, role=Printer.Role.LABEL)
        if data.get('printer') not in (None, ''):
            try:
                printer = printers.filter(pk=int(data['printer'])).first()
            except (TypeError, ValueError):
                printer = None
        else:
            printer = printers.filter(auto_print=True).first() or printers.first()
        if printer is None:
            return Response({'detail': 'Este local no tiene una impresora de etiquetas.'},
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            job = printing.enqueue_label_batch_manual(
                batch=batch, printer=printer, key=request.headers.get('Idempotency-Key') or '',
                actor=request.user, size=str(data.get('size') or '')[:9])
        except printing.PrintConflict as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_409_CONFLICT)
        except printing.PrintError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        AdminAuditLog.log(
            actor=request.user, action='label_batch_printed', target_type='print_job', target_id=job.pk,
            metadata={'label_batch_id': batch.pk, 'printer_id': printer.pk, 'size': job.label_size},
            request=request, company=company)
        return Response(_job_payload(job), status=status.HTTP_201_CREATED)
