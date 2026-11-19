"""
La cola de impresión.

QUIÉN DECIDE QUÉ SE IMPRIME: el servidor, y sólo cuando la venta está
confirmada de forma autoritativa — el pedido figura pagado en la base y el
documento ya es imprimible. Ni el navegador ni el agente pueden pedir un ticket
de algo que no se cobró.

QUIÉN IMPRIME: el agente del local. Reclama un trabajo, recibe los bytes para
la impresora, los entrega y confirma. Si no confirma dentro del plazo, el
trabajo vuelve a la cola.

IDEMPOTENCIA, en sus dos mitades:
  · ENCOLAR. La clave sale del documento (`auto:fiscal:<id>`), así que confirmar
    dos veces la misma venta encuentra el trabajo que ya existe.
  · IMPRIMIR. Cada entrega lleva un `claim_token` nuevo. Confirmar dos veces con
    el mismo no cambia nada; confirmar con uno antiguo se rechaza. El papel no
    sale dos veces porque el agente recuerda qué entregas ya imprimió.
"""
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import timedelta

from django.db import IntegrityError, connection, transaction
from django.db.models import Q
from django.utils import timezone

from ..models import Order, PrintAgent, Printer, PrintJob

#: Cuánto tiene el agente para imprimir y confirmar antes de que el trabajo
#: vuelva a la cola.
LEASE_SECONDS = 90
#: Entregas antes de darlo por fallido. Después lo reenvía una persona.
MAX_ATTEMPTS = 5
#: Espera antes del siguiente intento, según cuántos van. Sin ella, una
#: impresora apagada quema los cinco intentos en un segundo y deja fallidos
#: todos los tickets de la cola.
RETRY_DELAYS = (10, 30, 60, 120)
#: Un ticket que nadie recogió en este tiempo ya no se imprime: el agente que
#: vuelve tras un fin de semana no debe vaciar el atraso sobre el mostrador.
MAX_AGE_HOURS = 12
#: Un agente que no pregunta desde hace más que esto se considera desconectado.
AGENT_ONLINE_SECONDS = 120
TOKEN_PREFIX = 'bdpa_'
#: Lo que un agente que no dice nada atiende: los anteriores a WIN-003C.
LEGACY_CONNECTIONS = (Printer.Connection.NETWORK,)
#: Cuántas impresoras puede nombrar un agente al pedir trabajo.
MAX_DECLARED_PRINTERS = 50
#: Resultados con los que el agente dice que el trabajo NO debe volver a
#: entregarse solo: `refused` (los bytes no son un ticket ESC/POS) y
#: `queued_not_printed` (pudo salir o no; repetirlo podría imprimirlo dos
#: veces). Mismo vocabulario que PR #120.
FINAL_OUTCOMES = ('refused', 'queued_not_printed')
UNCERTAIN_PREFIX = 'INCIERTO'


class PrintError(Exception):
    """El trabajo no se puede crear o mover."""


class PrintNotFound(PrintError):
    """No existe para quien pregunta. La misma respuesta que si no existiera."""


class PrintConflict(PrintError):
    """La entrega con la que se intenta cerrar ya no es la vigente."""


# -- agentes ---------------------------------------------------------------------

def _hash(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode('utf-8')).hexdigest()


def create_agent(*, company, branch, name: str, actor):
    """Da de alta un agente. El token se devuelve aquí y no se puede volver a leer."""
    if branch.company_id != company.pk:
        raise PrintError('Esa sucursal no es de esta empresa.')
    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    agent = PrintAgent.objects.create(
        company=company, branch=branch, name=name.strip()[:120],
        token_hash=_hash(raw), token_hint=raw[:10],
        created_by=actor if getattr(actor, 'is_authenticated', False) else None,
    )
    return agent, raw


def authenticate_agent(raw_token: str):
    """El agente dueño de este token, si sigue activo. None en cualquier otro caso."""
    if not raw_token or not raw_token.startswith(TOKEN_PREFIX):
        return None
    return (
        PrintAgent.objects.select_related('company', 'branch')
        .filter(token_hash=_hash(raw_token), is_active=True,
                branch__is_active=True, company__is_active=True)
        .first()
    )


# -- encolar ---------------------------------------------------------------------

CANCELLED_REFUSAL = 'El pedido está cancelado: su ticket no se imprime. Descarga el PDF, que lo marca como anulado.'
VOID_NOTE_REFUSAL = 'La nota de venta está anulada: su ticket no se imprime. Descarga el PDF, que la marca como anulada.'


def _note_is_void(job) -> bool:
    """Whether the job prints a sales note that was voided (SALES-NOTE-CONTROL-01)."""
    from ..models import SalesNote

    return bool(job.sales_note_id) and SalesNote.objects.filter(
        pk=job.sales_note_id, status=SalesNote.STATUS_VOID).exists()


def _is_authoritative(order) -> bool:
    return bool(order.paid) and order.status == Order.Status.PAID


def parse_claim(data) -> tuple:
    """
    (conexiones, ids de impresora o None) que un agente declara al pedir trabajo.

    Sin `connections`: un agente anterior a WIN-003C, que sólo atiende `network`.
    Lo declarado SÓLO FILTRA dentro de la empresa y la sucursal del token: no
    da acceso a nada más. PrintError si no tiene la forma esperada.
    """
    if not isinstance(data, dict):
        data = {}
    raw = data.get('connections')
    if raw is None:
        connections = LEGACY_CONNECTIONS
    else:
        valid = set(Printer.Connection.values)
        if (not isinstance(raw, list) or not raw or len(raw) > len(valid)
                or not all(isinstance(c, str) and c in valid for c in raw)):
            raise PrintError(
                f'"connections" tiene que ser una lista no vacía de: {", ".join(sorted(valid))}.')
        connections = tuple(dict.fromkeys(raw))
    raw = data.get('printers')
    if raw is None:
        return connections, None
    # No vacía: «ninguna impresora» no es una forma de pedir trabajo, y el panel
    # leería una lista vacía como «todas». Ids dentro de una clave bigint.
    if (not isinstance(raw, list) or not raw or len(raw) > MAX_DECLARED_PRINTERS
            or not all(type(p) is int and 0 < p < 2 ** 63 for p in raw)):
        raise PrintError(
            f'"printers" tiene que ser una lista de 1 a {MAX_DECLARED_PRINTERS} ids de impresora.')
    return connections, tuple(dict.fromkeys(raw))


def online_agents(*, branch=None, company=None) -> list:
    """Los agentes activos que preguntaron hace poco, de una sucursal o de una empresa."""
    recent = timezone.now() - timedelta(seconds=AGENT_ONLINE_SECONDS)
    agents = PrintAgent.objects.filter(is_active=True, last_seen_at__gte=recent)
    if branch is not None:
        agents = agents.filter(branch=branch)
    if company is not None:
        agents = agents.filter(company=company)
    return list(agents.only('branch_id', 'last_connections', 'last_printers'))


def printer_has_agent(printer, agents) -> bool:
    """¿Alguno de estos agentes en línea, de su sucursal, dijo atender ESTA impresora?"""
    return any(a.branch_id == printer.branch_id and a.serves(printer) for a in agents)


def agent_online(branch, connection=None, printer=None) -> bool:
    """
    ¿Hay algún agente de este local que haya preguntado hace poco?

    Con `printer`: y que haya dicho atender esa impresora (su clase de conexión
    y, si nombró impresoras, ésa). Con sólo `connection`: esa clase. Un agente
    anterior a WIN-003C (sin declarar nada) sólo atiende `network`.
    """
    if branch is None:
        return False
    agents = online_agents(branch=branch)
    if printer is not None:
        return printer_has_agent(printer, agents)
    if connection is None:
        return bool(agents)
    return any(connection in agent.connections for agent in agents)


def _retry_at(attempts: int):
    delay = RETRY_DELAYS[min(max(attempts, 1), len(RETRY_DELAYS)) - 1]
    return timezone.now() + timedelta(seconds=delay)


def auto_printer(branch):
    """La impresora que recibe los tickets automáticos de este local, si hay."""
    if branch is None:
        return None
    return Printer.objects.filter(branch=branch, is_active=True, auto_print=True).first()


def _get_or_create(*, key: str, **fields) -> PrintJob:
    company = fields['company']
    existing = PrintJob.objects.filter(company=company, idempotency_key=key).first()
    if existing is not None:
        return existing
    try:
        with transaction.atomic():
            return PrintJob.objects.create(idempotency_key=key, **fields)
    except IntegrityError:
        # Otra confirmación de la misma venta llegó a la vez y ganó.
        return PrintJob.objects.get(company=company, idempotency_key=key)


def enqueue_fiscal_ticket(document):
    """
    El ticket automático de un comprobante. None si no corresponde imprimir.

    No corresponde si el pedido no está pagado, si el comprobante aún no está
    firmado (sin firma no hay representación impresa) o si el local no tiene
    una impresora automática.
    """
    order = document.order
    if not _is_authoritative(order) or not document.signed_xml:
        return None
    printer = auto_printer(order.fulfillment_branch)
    if printer is None:
        return None
    return _get_or_create(
        key=f'auto:fiscal:{document.pk}', company=document.company,
        branch=printer.branch, printer=printer, kind=PrintJob.Kind.FISCAL_TICKET,
        reason=PrintJob.Reason.AUTO, order=order, fiscal_document=document,
    )


def enqueue_sales_note_ticket(sales_note):
    """El ticket automático de una nota de venta interna. None si no corresponde."""
    order = sales_note.order
    if not _is_authoritative(order):
        return None
    printer = auto_printer(order.fulfillment_branch)
    if printer is None:
        return None
    return _get_or_create(
        key=f'auto:note:{sales_note.pk}', company=order.company,
        branch=printer.branch, printer=printer, kind=PrintJob.Kind.SALES_NOTE_TICKET,
        reason=PrintJob.Reason.AUTO, order=order, sales_note=sales_note,
    )


def printable_document(order):
    """(kind, fiscal_document, sales_note) de lo que este pedido puede imprimir."""
    from ..models import FiscalDocument, SalesNote

    document = (
        FiscalDocument.objects.filter(order=order, original_document__isnull=True)
        .exclude(signed_xml='').order_by('pk').first()
    )
    if document is not None:
        return PrintJob.Kind.FISCAL_TICKET, document, None
    note = SalesNote.objects.filter(order=order).order_by('pk').first()
    if note is not None:
        return PrintJob.Kind.SALES_NOTE_TICKET, None, note
    return None, None, None


def enqueue_manual(*, order, printer, key: str, actor):
    """
    Una reimpresión pedida por una persona.

    La impresora tiene que ser del local que vendió: un ticket no se manda a
    otro local. `key` la pone quien llama (la cabecera `Idempotency-Key`), de
    modo que un doble clic no imprime dos veces.
    """
    if order.status == Order.Status.CANCELLED:
        # ORDER-CANCEL-01: the thermal roll does not get a cancelled order's
        # ticket. Its PDF (marked «ANULADA») is still downloadable.
        raise PrintError(CANCELLED_REFUSAL)
    if not _is_authoritative(order):
        raise PrintError('Sólo se imprime un pedido pagado.')
    if printer.company_id != order.company_id or not printer.is_active:
        raise PrintError('Esa impresora no está disponible.')
    if order.fulfillment_branch_id != printer.branch_id:
        raise PrintError('Esa impresora es de otro local.')
    kind, document, note = printable_document(order)
    if kind is None:
        raise PrintError('Este pedido todavía no tiene un documento que imprimir.')
    if note is not None and note.status == note.STATUS_VOID:
        # SALES-NOTE-CONTROL-01: like a cancelled order's, a void note is not
        # reprinted on the roll. Its PDF (marked «ANULADA») stays downloadable.
        raise PrintError(VOID_NOTE_REFUSAL)
    key = (key or '').strip()
    if not key:
        raise PrintError('Falta la clave de idempotencia.')
    earlier = PrintJob.objects.filter(
        company=order.company, idempotency_key=f'manual:{key}'[:120]).first()
    if earlier is not None and (earlier.order_id != order.pk or earlier.printer_id != printer.pk):
        # La misma clave para OTRA cosa no es una repetición: devolver el
        # trabajo anterior sería decir «hecho» sin imprimir nada.
        raise PrintConflict('Esa clave ya se usó para otra reimpresión.')
    return _get_or_create(
        key=f'manual:{key}'[:120], company=order.company, branch=printer.branch,
        printer=printer, kind=kind, reason=PrintJob.Reason.MANUAL, order=order,
        fiscal_document=document, sales_note=note,
        requested_by=actor if getattr(actor, 'is_authenticated', False) else None,
    )


# -- entregar --------------------------------------------------------------------

def _requeue_expired(branch, now) -> None:
    expired = PrintJob.objects.filter(
        branch=branch, status=PrintJob.Status.PRINTING, lease_expires_at__lt=now)
    expired.filter(attempts__gte=MAX_ATTEMPTS).update(
        status=PrintJob.Status.FAILED, claim_token='', lease_expires_at=None,
        last_error='La impresora no confirmó la impresión.', updated_at=now)
    expired.filter(attempts__lt=MAX_ATTEMPTS).update(
        status=PrintJob.Status.PENDING, claim_token='', lease_expires_at=None,
        available_at=None, updated_at=now)
    # Lo que lleva demasiado en la cola ya no se imprime.
    PrintJob.objects.filter(
        branch=branch, status=PrintJob.Status.PENDING,
        created_at__lt=now - timedelta(hours=MAX_AGE_HOURS),
    ).update(
        status=PrintJob.Status.FAILED, updated_at=now,
        last_error='Caducó sin imprimirse: nadie lo recogió a tiempo.')


def claim_next(agent, connections=LEGACY_CONNECTIONS, printer_ids=None):
    """
    El siguiente trabajo del local de este agente, ya marcado como entregado.

    Sólo los de SU sucursal. Con la fila bloqueada, de modo que dos agentes del
    mismo local no se llevan el mismo ticket.

    SÓLO LO QUE EL AGENTE PUEDE IMPRIMIR (WIN-003C). `connections` y
    `printer_ids` (lo que el agente declara) filtran dentro de su sucursal: un
    trabajo de una impresora USB no se le da a un agente de red, ni el de otra
    PC a éste. Lo que no es para él sigue en la cola, sin gastar intentos.

    LA VENTA SE VUELVE A MIRAR AL ENTREGAR. Un pedido que dejó de estar pagado
    entre que se encoló y ahora no se imprime: su trabajo se cancela.
    """
    now = timezone.now()
    locking = {'skip_locked': connection.features.has_select_for_update_skip_locked}
    if connection.features.has_select_for_update_of:
        # Sólo la fila del trabajo: el filtro por impresora une su tabla, y sin
        # esto PostgreSQL bloquearía también la impresora.
        locking['of'] = ('self',)
    connections = tuple(connections) or LEGACY_CONNECTIONS
    with transaction.atomic():
        declared = '' if connections == LEGACY_CONNECTIONS else ','.join(connections)
        PrintAgent.objects.filter(pk=agent.pk).update(
            last_seen_at=now, last_connections=declared,
            last_printers=','.join(str(p) for p in printer_ids) if printer_ids else '')
        _requeue_expired(agent.branch, now)
        while True:
            jobs = (
                PrintJob.objects.select_for_update(**locking)
                .filter(company=agent.company, branch=agent.branch,
                        status=PrintJob.Status.PENDING, printer__is_active=True,
                        printer__connection__in=connections)
                .filter(Q(available_at__isnull=True) | Q(available_at__lte=now))
            )
            if printer_ids is not None:
                jobs = jobs.filter(printer_id__in=printer_ids)
            job = jobs.order_by('pk').first()
            if job is None:
                return None
            if not _is_authoritative(job.order):
                job.status = PrintJob.Status.CANCELLED
                job.last_error = (
                    CANCELLED_REFUSAL if job.order.status == Order.Status.CANCELLED
                    else 'El pedido ya no está pagado.')
                job.save(update_fields=['status', 'last_error', 'updated_at'])
                continue
            if _note_is_void(job):
                # SALES-NOTE-CONTROL-01: a ticket queued before its note was voided.
                job.status = PrintJob.Status.CANCELLED
                job.last_error = VOID_NOTE_REFUSAL
                job.save(update_fields=['status', 'last_error', 'updated_at'])
                continue
            job.status = PrintJob.Status.PRINTING
            job.attempts += 1
            job.claimed_by = agent
            job.claim_token = secrets.token_hex(16)
            job.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
            job.save(update_fields=[
                'status', 'attempts', 'claimed_by', 'claim_token', 'lease_expires_at',
                'updated_at'])
            return job


def release_unrenderable(job, error: str) -> None:
    """
    El documento no se pudo preparar para la impresora en esta entrega.

    Puede ser pasajero (el almacén no respondió), así que se reintenta con
    espera; al quinto intento se da por fallido.
    """
    job.claim_token = ''
    job.lease_expires_at = None
    job.last_error = ' '.join(str(error).split())[:300]
    if job.attempts >= MAX_ATTEMPTS:
        job.status = PrintJob.Status.FAILED
    else:
        job.status = PrintJob.Status.PENDING
        job.available_at = _retry_at(job.attempts)
    job.save(update_fields=[
        'status', 'claim_token', 'lease_expires_at', 'last_error', 'available_at', 'updated_at'])


def deactivate_printer(printer) -> None:
    """
    Se da de baja una impresora, y con ella lo que esperaba por ella.

    Sin esto sus trabajos quedarían «en cola» para siempre: nadie los entrega
    y nadie avisa.
    """
    with transaction.atomic():
        printer.is_active = False
        printer.save(update_fields=['is_active', 'updated_at'])
        now = timezone.now()
        PrintJob.objects.filter(printer=printer, status=PrintJob.Status.PENDING).update(
            status=PrintJob.Status.FAILED, claim_token='', lease_expires_at=None,
            last_error='La impresora se desactivó antes de imprimirlo.', updated_at=now)
        # Uno que el agente ya tenía PUDO SALIR: incierto, como los que el agente
        # declara así. Reenviarlo pide confirmar que no salió y cambia el uid
        # (segunda revisión de #333: sin esto, el reintento iba a otra impresora
        # con el mismo uid y otro agente lo imprimía).
        PrintJob.objects.filter(printer=printer, status=PrintJob.Status.PRINTING).update(
            status=PrintJob.Status.FAILED, claim_token='', lease_expires_at=None,
            uncertain=True,
            last_error=f'{UNCERTAIN_PREFIX}: la impresora se desactivó mientras se imprimía; '
                       f'puede haber salido.',
            updated_at=now)

def render(job) -> bytes:
    """Los bytes que la impresora de este trabajo tiene que recibir."""
    from . import escpos

    printer = job.printer
    # THERMAL-512: laid out for the dots this printer prints across, whatever width the document was sold at.
    options = {'paper_width_mm': printer.paper_width_mm, 'encoding': printer.encoding,
               'dots': escpos.printable_dots(printer.paper_width_mm, printer.print_width_dots)}
    if job.kind == PrintJob.Kind.FISCAL_TICKET:
        return escpos.fiscal_ticket(job.fiscal_document, **options)
    return escpos.sales_note_ticket(job.sales_note, **options)


def is_uncertain(job) -> bool:
    """¿Falló porque no se sabe si el papel salió? Reenviarlo pide confirmación."""
    return job.status == PrintJob.Status.FAILED and job.uncertain


def complete(agent, job_id, claim_token: str, *, ok: bool, error: str = '', outcome: str = ''):
    """
    El agente dice cómo terminó una entrega.

    Repetir la misma confirmación no cambia nada. Confirmar con el token de una
    entrega anterior se rechaza: esa entrega caducó y el trabajo ya es de otra.

    `outcome` en FINAL_OUTCOMES: no se vuelve a entregar solo, se da por fallido
    ya. `queued_not_printed` es INCIERTO: el motivo lo dice siempre, para que el
    panel pida confirmación antes de reenviarlo.
    """
    with transaction.atomic():
        job = (
            PrintJob.objects.select_for_update()
            .filter(pk=job_id, company=agent.company, branch=agent.branch).first()
        )
        if job is None:
            raise PrintNotFound('Trabajo no encontrado.')
        if not claim_token or not secrets.compare_digest(job.claim_token or '', claim_token):
            raise PrintConflict('Esa entrega ya no está vigente.')
        if job.status == PrintJob.Status.PRINTED:
            return job
        if job.status != PrintJob.Status.PRINTING:
            raise PrintConflict('Esa entrega ya no está vigente.')

        now = timezone.now()
        job.lease_expires_at = None
        if ok:
            job.status = PrintJob.Status.PRINTED
            job.printed_at = now
            job.last_error = ''
        else:
            reason = ' '.join(str(error or 'La impresora no respondió.').split())
            if outcome == 'queued_not_printed' and not reason.startswith(UNCERTAIN_PREFIX):
                reason = f'{UNCERTAIN_PREFIX}: {reason}'
            elif outcome != 'queued_not_printed' and reason.startswith(UNCERTAIN_PREFIX):
                # Sólo el resultado declarado hace incierto un trabajo, no el texto.
                reason = reason[len(UNCERTAIN_PREFIX):].lstrip(' :') or 'La impresora no respondió.'
            job.last_error = reason[:300]
            # Lo marca el resultado declarado, nunca el texto del motivo.
            job.uncertain = outcome == 'queued_not_printed'
            if outcome in FINAL_OUTCOMES or job.attempts >= MAX_ATTEMPTS:
                job.status = PrintJob.Status.FAILED
            else:
                job.status = PrintJob.Status.PENDING
                job.claim_token = ''
                job.available_at = _retry_at(job.attempts)
        job.save(update_fields=[
            'status', 'printed_at', 'last_error', 'claim_token', 'lease_expires_at',
            'available_at', 'uncertain', 'updated_at'])
        return job


def retry(job, *, actor, confirm_not_printed: bool = False, expected_uid: str = ''):
    """
    Una persona vuelve a mandar un trabajo fallido o cancelado.

    UN TRABAJO INCIERTO (pudo salir) sólo se reenvía si quien lo pide confirma
    que miró y NO salió. Se reenvía con un identificador NUEVO: el agente
    recuerda el anterior como «pudo salir» y no lo volvería a imprimir.

    CON LA FILA BLOQUEADA y el estado vuelto a mirar: dos clics a la vez no
    reenvían dos veces (con un uid nuevo cada uno, el diario del agente no
    podría evitar el segundo papel). Revisión WIN-003C, P2.

    UNA CONFIRMACIÓN VALE PARA LO QUE SE VIO. `expected_uid`: el identificador
    del trabajo que la persona tenía delante. Si entre tanto se reenvió (y quizá
    volvió a quedar incierto), su «no salió» ya no habla de este papel: 409.

    El trabajo devuelto lleva `was_uncertain`, leído de la fila bloqueada, para
    la auditoría.
    """
    with transaction.atomic():
        locked = PrintJob.objects.select_for_update().filter(pk=job.pk).first()
        if locked is None:
            raise PrintNotFound('Trabajo no encontrado.')
        if expected_uid and str(expected_uid).replace('-', '').lower() != locked.uid.hex:
            raise PrintConflict(
                'Este ticket cambió desde que lo viste (ya se reenvió). Revisa la cola de nuevo.')
        if is_uncertain(locked) and not expected_uid:
            # Revisión de #333, P2: sin el uid que se vio, un «no salió» de una
            # pestaña vieja o de una llamada directa reenviaría a ciegas un
            # ticket que quizá ya se reenvió y salió.
            raise PrintError(
                'Para reenviar un ticket que pudo salir hace falta el identificador '
                'del ticket que viste (expected_uid). Revisa la cola de nuevo.')
        return _retry_locked(locked, actor=actor, confirm_not_printed=confirm_not_printed)


def _retry_locked(job, *, actor, confirm_not_printed):
    if job.status not in (PrintJob.Status.FAILED, PrintJob.Status.CANCELLED):
        raise PrintError('Sólo se reenvía un trabajo fallido o cancelado.')
    uncertain = is_uncertain(job)
    job.was_uncertain = uncertain
    if uncertain and not confirm_not_printed:
        raise PrintError(
            'No se sabe si este ticket salió. Revisa el mostrador y confirma que NO salió '
            'antes de reenviarlo.')
    if job.order.status == Order.Status.CANCELLED:
        raise PrintError(CANCELLED_REFUSAL)
    if _note_is_void(job):
        raise PrintError(VOID_NOTE_REFUSAL)
    if not _is_authoritative(job.order):
        raise PrintError('Sólo se imprime un pedido pagado.')
    if not job.printer.is_active:
        # La impresora de entonces ya no existe: va a la que el local tiene hoy.
        replacement = auto_printer(job.branch) or Printer.objects.filter(
            branch=job.branch, is_active=True).first()
        if replacement is None:
            raise PrintError('Este local no tiene una impresora disponible.')
        job.printer = replacement
    job.status = PrintJob.Status.PENDING
    job.attempts = 0
    job.claim_token = ''
    job.lease_expires_at = None
    job.available_at = None
    job.last_error = ''
    job.uncertain = False
    # Cuenta como recién pedido: si no, caducaría por la fecha del original.
    job.created_at = timezone.now()
    if getattr(actor, 'is_authenticated', False):
        job.requested_by = actor
    fields = ['printer', 'status', 'attempts', 'claim_token', 'lease_expires_at', 'available_at',
              'last_error', 'uncertain', 'created_at', 'requested_by', 'updated_at']
    if uncertain:
        job.uid = uuid.uuid4()
        fields.append('uid')
    job.save(update_fields=fields)
    return job
