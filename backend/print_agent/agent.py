#!/usr/bin/env python3
"""
Agente de impresión de la tienda.

QUÉ ES. Un programa pequeño que se deja encendido en un equipo DENTRO de la red
del local (una PC de caja, una Raspberry Pi). Cada pocos segundos pregunta al
servidor si hay un ticket para su sucursal; si lo hay, lo entrega a la impresora
térmica por la red (puerto 9100) y confirma.

POR QUÉ HACE FALTA. El servidor está en Internet y la impresora en la red del
local: el servidor no puede llegar a ella, y no debe. El teléfono que cobra
tampoco tiene una impresora térmica. El agente es el único que ve a los dos, y
es él quien llama al servidor, nunca al revés: no hay que abrir ningún puerto
del local.

QUÉ PUEDE HACER SU TOKEN. Recoger trabajos de impresión de UNA sucursal y decir
cómo salieron. Nada más: no abre el panel ni lee ventas.

SÓLO LA BIBLIOTECA ESTÁNDAR. Se copia un archivo y se ejecuta con Python 3.9+.

    python3 agent.py --config config.json

EL PAPEL NO SALE DOS VECES. El servidor entrega cada trabajo «al menos una
vez»: si la confirmación se pierde, lo vuelve a entregar. El agente apunta en un
diario lo que ya imprimió, y ante una segunda entrega del mismo trabajo confirma
sin volver a imprimir.
"""
from __future__ import annotations

import argparse
import base64
import http.client
import ipaddress
import json
import logging
import os
import re
import shlex
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger('print_agent')

JOURNAL_LIMIT = 500
#: Lo que es una red de un local. Escrito a mano y no con `is_private`: lo que
#: esa propiedad considera privado cambia entre versiones de Python.
_LOCAL_NETWORKS = tuple(ipaddress.ip_network(n) for n in (
    '10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '169.254.0.0/16',
    'fc00::/7', 'fe80::/10',
))
_LOOPBACK = tuple(ipaddress.ip_network(n) for n in ('127.0.0.0/8', '::1/128'))
_LOCAL_NAME = re.compile(r'^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.local$', re.IGNORECASE)


class AgentError(Exception):
    """La configuración no permite arrancar."""


# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------

def load_config(path: str) -> dict:
    with open(path, encoding='utf-8') as handle:
        config = json.load(handle)
    return validate_config(config)


def validate_config(config: dict) -> dict:
    server = str(config.get('server', '')).rstrip('/')
    token = str(config.get('token', ''))
    if not token:
        raise AgentError('Falta "token": lo entrega el panel al dar de alta el agente.')
    # El token viaja en cada petición. Sin TLS lo leería cualquiera en el camino.
    # Se compara el NOMBRE DE HOST entero: `http://localhost.otro.sitio` empieza
    # igual que `http://localhost` y no es este equipo.
    parsed = urllib.parse.urlsplit(server)
    insecure_local = parsed.scheme == 'http' and parsed.hostname in ('localhost', '127.0.0.1')
    if parsed.scheme != 'https' and not insecure_local:
        raise AgentError('"server" tiene que empezar por https://')
    if not parsed.hostname:
        raise AgentError('"server" no es una dirección válida.')
    return {
        'server': server,
        'token': token,
        'poll_seconds': max(1.0, float(config.get('poll_seconds', 3))),
        'printer_timeout': max(1.0, float(config.get('printer_timeout', 10))),
        'journal': str(config.get('journal', 'print-agent-journal.json')),
        # Sólo para pruebas: una impresora simulada en este mismo equipo.
        'allow_loopback': bool(config.get('allow_loopback', False)),
        # Cuánto esperar a que CUPS saque un trabajo de una cola `cups_raw`.
        'cups_wait_seconds': _cups_wait(config.get('cups_wait_seconds', 30)),
        'printers': _validate_printers(config.get('printers')),
    }


#: La espera a CUPS tiene que caber en el plazo de la entrega (90 s) junto con
#: las comprobaciones de la cola, `lp` y la última consulta: si no, el servidor
#: volvería a entregar el trabajo mientras el agente aún espera.
CUPS_WAIT_MAX = 45.0


def _cups_wait(raw) -> float:
    try:
        wait = float(raw)
    except (TypeError, ValueError):
        wait = -1.0
    if not 1.0 <= wait <= CUPS_WAIT_MAX:
        raise AgentError(
            f'"cups_wait_seconds" tiene que estar entre 1 y {CUPS_WAIT_MAX:.0f} segundos.')
    return wait


#: Un nombre de cola de CUPS sin nada que `lp` pudiera leer como otra cosa: sin
#: espacios, barras, `#`, comillas ni un guion delante (sería una opción).
_CUPS_QUEUE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,126}$')
TRANSPORTS = ('tcp', 'cups_raw')


def valid_queue_name(queue) -> bool:
    return isinstance(queue, str) and bool(_CUPS_QUEUE.fullmatch(queue))


def _validate_printers(raw) -> dict:
    """
    Cómo llega este agente a cada impresora, por el NOMBRE que tiene en el panel.

        "printers": {"Caja": {"transport": "cups_raw", "queue": "POS80_RAW"}}

    `tcp` (lo de siempre, y lo que vale si no se dice nada): la dirección y el
    puerto que manda el servidor. `cups_raw`: una cola RAW de CUPS de ESTE
    equipo, para una térmica USB. Una impresora que no está aquí va por red.
    """
    if raw in (None, {}):
        return {}
    if not isinstance(raw, dict):
        raise AgentError('"printers" tiene que ser un objeto: {"Nombre en el panel": {...}}.')
    printers = {}
    for name, entry in raw.items():
        entry = entry or {}
        if not isinstance(entry, dict):
            raise AgentError(f'"printers" › "{name}" tiene que ser un objeto.')
        transport = str(entry.get('transport') or 'tcp')
        if transport not in TRANSPORTS:
            raise AgentError(
                f'"printers" › "{name}": transporte "{transport}" desconocido. '
                f'Use "tcp" o "cups_raw".')
        if transport == 'tcp':
            printers[str(name)] = {'transport': 'tcp'}
            continue
        queue = entry.get('queue')
        if not valid_queue_name(queue):
            raise AgentError(
                f'"printers" › "{name}": "cups_raw" necesita "queue" con el nombre de una cola '
                f'RAW de CUPS (letras, números, "_", "-" o "."; por ejemplo POS80_RAW).')
        printers[str(name)] = {'transport': 'cups_raw', 'queue': queue}
    return printers


# ---------------------------------------------------------------------------
# El diario: qué se imprimió ya y qué falta por confirmar
# ---------------------------------------------------------------------------

class Journal:
    """
    `printed`: claves de los trabajos que ya se entregaron y NO se vuelven a
    mandar: los que salieron por la impresora y los que quedaron en una cola de
    CUPS sin constar impresos.
    `held`: de esos, los que quedaron en CUPS, con el resultado que se repite al
    servidor (`queued_not_printed` y su motivo) cada vez que lo vuelve a entregar.
    `unconfirmed`: lo que el servidor todavía no sabe, con el número de trabajo,
    el token de la entrega y, si no salió, el resultado y el motivo.
    """

    def __init__(self, path: str):
        self.path = path
        self.printed: list[str] = []
        self.held: dict[str, tuple] = {}
        self.unconfirmed: dict[str, tuple] = {}
        try:
            with open(path, encoding='utf-8') as handle:
                data = json.load(handle)
            self.printed = [str(key) for key in data.get('printed', [])]
            self.held = {
                str(key): (str(value[0]), str(value[1]))
                for key, value in data.get('held', {}).items()}
            # Un diario de antes de PRINT-THERMAL-01 sólo tiene (trabajo, token).
            self.unconfirmed = {
                str(key): tuple(value) for key, value in data.get('unconfirmed', {}).items()
                if len(value) >= 2}
        except (OSError, ValueError, TypeError, IndexError):
            pass

    def _save(self):
        keep = set(self.unconfirmed)
        overflow = len(self.printed) - JOURNAL_LIMIT
        if overflow > 0:
            self.printed = [
                key for index, key in enumerate(self.printed) if index >= overflow or key in keep]
        alive = set(self.printed)
        self.held = {key: value for key, value in self.held.items() if key in alive}
        temporary = self.path + '.tmp'
        with open(temporary, 'w', encoding='utf-8') as handle:
            json.dump({'printed': self.printed, 'held': self.held,
                       'unconfirmed': self.unconfirmed}, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.path)

    def was_printed(self, key) -> bool:
        return str(key) in self.printed

    def record_printed(self, key, job_id, claim_token: str):
        if str(key) not in self.printed:
            self.printed.append(str(key))
        self.unconfirmed[str(key)] = (job_id, claim_token)
        self._save()

    def held_outcome(self, key):
        """(resultado, motivo) si este trabajo quedó en una cola de CUPS; si no, None."""
        return self.held.get(str(key))

    def record_held(self, key, job_id, claim_token: str, outcome: str, error: str):
        """Quedó en CUPS: no se vuelve a mandar, y el servidor tiene que saberlo."""
        if str(key) not in self.printed:
            self.printed.append(str(key))
        self.held[str(key)] = (outcome, error)
        self.unconfirmed[str(key)] = (job_id, claim_token, outcome, error)
        self._save()

    def needs_confirmation(self, key, job_id, claim_token: str, outcome: str = '',
                           error: str = ''):
        self.unconfirmed[str(key)] = (
            (job_id, claim_token, outcome, error) if outcome else (job_id, claim_token))
        self._save()

    def confirmed(self, key):
        if self.unconfirmed.pop(str(key), None) is not None:
            self._save()


# ---------------------------------------------------------------------------
# Red
# ---------------------------------------------------------------------------

def is_local_address(host: str, *, allow_loopback: bool = False) -> bool:
    """
    ¿Está esta dirección dentro de una red local?

    El servidor dice a qué impresora mandar cada ticket. El agente no se fía a
    ciegas: sólo abre conexiones hacia direcciones privadas o nombres `.local`.
    Así, aunque alguien cambiara la dirección de una impresora en el panel, el
    agente no se convierte en una puerta desde la red del local hacia fuera.
    """
    host = (host or '').strip()
    if _LOCAL_NAME.match(host):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    if any(address in network for network in _LOOPBACK):
        return allow_loopback
    return any(address in network for network in _LOCAL_NETWORKS)


# ---------------------------------------------------------------------------
# Sólo ESC/POS
# ---------------------------------------------------------------------------

#: Los documentos que el servidor manda a una térmica. Otro tipo no se imprime.
KNOWN_KINDS = frozenset({'fiscal_ticket', 'sales_note_ticket', 'test_ticket'})
_LEADING_NOISE = b' \t\r\n\x00\x04\x0c'
_BOMS = (b'\xef\xbb\xbf', b'\xfe\xff', b'\xff\xfe')
_HEADER_WINDOW = 128


def payload_problem(payload: bytes) -> str:
    """
    Por qué estos bytes NO se pueden mandar a una térmica ESC/POS, o '' si se puede.

    Copia de `store/printing/escpos.py::payload_problem` (el agente se instala
    como un archivo suelto). Un ticket empieza por `ESC @`; un PDF, por `%PDF`;
    un PostScript, por `%!` — quizá tras blancos, una marca de orden de bytes o
    un envoltorio PJL. Una térmica imprimiría esos dos como texto.
    """
    payload = bytes(payload or b'')
    head = payload[:_HEADER_WINDOW]
    stripped = head
    while True:
        before = stripped
        stripped = stripped.lstrip(_LEADING_NOISE)
        for bom in _BOMS:
            if stripped.startswith(bom):
                stripped = stripped[len(bom):]
        if stripped == before:
            break
    if stripped.startswith(b'%PDF') or b'%PDF-' in head:
        return 'Son los bytes de un PDF, no un ticket ESC/POS: no se envían a la térmica.'
    if stripped.startswith(b'%!') or b'%!PS-Adobe' in head:
        return 'Son los bytes de un PostScript, no un ticket ESC/POS: no se envían a la térmica.'
    if not payload.startswith(b'\x1b@'):
        return 'No es un ticket ESC/POS (no empieza por ESC @): no se envía a la térmica.'
    return ''


def send_to_printer(host: str, port: int, payload: bytes, timeout: float) -> None:
    """Entrega los bytes a la impresora. Levanta OSError si no se pudo."""
    with socket.create_connection((host, port), timeout=timeout) as connection:
        connection.settimeout(timeout)
        connection.sendall(payload)
        try:
            connection.shutdown(socket.SHUT_WR)
        except OSError:
            pass


#: Lo que delata un controlador que convierte el trabajo en PostScript o PDF.
_DOCUMENT_DRIVERS = ('postscript', 'pdf')
_CUPS_TIMEOUT = 10.0


class CupsNotPrinted(Exception):
    """
    CUPS aceptó el trabajo (o pudo aceptarlo) y no consta que haya salido.

    `lp` termina bien en cuanto el trabajo ENTRA en la cola, no cuando sale
    por la impresora. Si sigue en la cola al acabar la espera, o si `lp` no
    contestó, el papel puede salir más tarde: reintentar solo imprimiría dos
    veces. El agente lo dice al servidor como `queued_not_printed` y una
    persona decide. No es un OSError a propósito: no es un fallo reintentable.
    """


#: Salida de `lpstat` y `lp` en inglés, la misma en cualquier Mac.
_C_LOCALE = {'LC_ALL': 'C', 'LANG': 'C'}
_REQUEST_ID = re.compile(r'request id is (\S+)')


def _cups(args: list, timeout: float, payload: bytes | None = None):
    """Una orden de CUPS: lista de argumentos, sin intérprete, con tiempo máximo y en C."""
    return subprocess.run(
        args, input=payload, capture_output=True, timeout=timeout, check=False,
        env={**os.environ, **_C_LOCALE})


def _text(done) -> str:
    return (done.stdout or b'').decode('utf-8', 'replace')


def _status_after(text: str, prefix: str):
    """Lo que sigue a `prefix` en la línea que empieza por él, o None."""
    for line in text.splitlines():
        if line.startswith(prefix):
            return line[len(prefix):].strip().lower()
    return None


def _queue_ready(queue: str) -> None:
    """Levanta OSError si la cola está detenida o no acepta trabajos. Nada se envió."""
    try:
        printer = _cups(['lpstat', '-p', queue], _CUPS_TIMEOUT)
        accepting = _cups(['lpstat', '-a', queue], _CUPS_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise OSError('lpstat no respondió') from None
    # Se lee el estado DETRÁS del nombre de la cola: un nombre puede contener
    # «disabled» o «accepting». En C: `printer <cola> is idle.  enabled since …`
    # o `printer <cola> disabled since …`; `<cola> accepting requests since …` o
    # `<cola> not accepting requests since …`.
    state = _status_after(_text(printer), f'printer {queue} ')
    if printer.returncode != 0 or state is None:
        raise OSError(f'la cola de CUPS {queue} no existe o CUPS no contesta')
    if state.startswith('disabled'):
        raise OSError(f'la cola de CUPS {queue} está detenida (disabled): cupsenable {queue}')
    answer = _status_after(_text(accepting), f'{queue} ')
    if accepting.returncode != 0 or answer is None or not answer.startswith('accepting'):
        raise OSError(f'la cola de CUPS {queue} no acepta trabajos: cupsaccept {queue}')


def _still_queued(queue: str, job_id: str) -> bool:
    """¿Sigue el trabajo en la cola? Si no se puede saber, se considera que sí."""
    try:
        done = _cups(['lpstat', '-W', 'not-completed', '-o', queue], _CUPS_TIMEOUT)
    except (subprocess.TimeoutExpired, OSError):
        # `lp` ya lo aceptó: no saber es «sigue ahí», nunca un fallo reintentable.
        return True
    if done.returncode != 0:
        return True
    return any(line.split()[0] == job_id for line in _text(done).splitlines() if line.split())


def send_to_cups_queue(queue: str, payload: bytes, timeout: float, wait: float = 30.0, *,
                       sleep=time.sleep, clock=time.monotonic, poll: float = 0.5) -> str:
    """
    Entrega los bytes a una cola RAW de CUPS de este equipo y espera a que salgan.

    1. `lpstat -p` y `lpstat -a`: la cola está activa y acepta trabajos. Si no,
       OSError: no se envió nada y el reintento es seguro.
    2. `lp -d <cola> -o raw` (sin convertir nada). Si `lp` falla, OSError.
    3. `lpstat -W not-completed -o <cola>` hasta que el trabajo ya no está, con
       un máximo de `wait` segundos. Si sigue ahí, o si `lp` no contestó o no
       dio número de trabajo: `CupsNotPrinted`, que NO se reintenta solo.

    Devuelve el número de trabajo de CUPS.
    """
    if not valid_queue_name(queue):
        raise OSError(f'nombre de cola de CUPS no válido: {queue!r}')
    _queue_ready(queue)
    try:
        done = _cups(['lp', '-d', queue, '-o', 'raw'], timeout, payload)
    except subprocess.TimeoutExpired:
        raise CupsNotPrinted(
            f'lp no respondió en {timeout:.0f} s: el trabajo pudo quedar en la cola {queue}. '
            f'Revísela antes de reenviar.') from None
    if done.returncode != 0:
        detail = (done.stderr or b'').decode('utf-8', 'replace').strip()[:150]
        raise OSError(f'lp terminó con {done.returncode}: {detail}')
    match = _REQUEST_ID.search(_text(done))
    if not match:
        raise CupsNotPrinted(
            f'lp no dio número de trabajo: no se sabe si quedó en la cola {queue}. '
            f'Revísela antes de reenviar.')
    job_id = match.group(1)
    deadline = clock() + max(0.0, wait)
    while _still_queued(queue, job_id):
        if clock() >= deadline:
            raise CupsNotPrinted(
                f'CUPS aceptó {job_id} pero sigue en la cola {queue} tras {wait:.0f} s: puede '
                f'salir más tarde. Revise la cola antes de reenviar.')
        sleep(poll)
    return job_id


def read_queue_model(queue: str) -> str:
    """
    El controlador de una cola de CUPS («make and model»), con `lpoptions -p`.

    Sólo lee. Levanta OSError si la cola no existe o CUPS no contesta.
    """
    if not valid_queue_name(queue):
        raise OSError(f'nombre de cola de CUPS no válido: {queue!r}')
    try:
        done = _cups(['lpoptions', '-p', queue], _CUPS_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise OSError('lpoptions no respondió') from None
    if done.returncode != 0:
        detail = (done.stderr or b'').decode('utf-8', 'replace').strip()[:150]
        raise OSError(f'lpoptions terminó con {done.returncode}: {detail}')
    text = (done.stdout or b'').decode('utf-8', 'replace')
    try:
        words = shlex.split(text)
    except ValueError:
        words = text.split()
    for word in words:
        key, _, value = word.partition('=')
        if key == 'printer-make-and-model':
            return value
    return ''


def check_raw_queue(queue: str, model_of=read_queue_model) -> None:
    """
    Arrancar con `cups_raw` sólo sobre una cola RAW. Levanta AgentError si no lo es.

    PRINT-THERMAL-01. Una cola con un controlador PostScript o PDF convierte lo
    que recibe; una térmica ESC/POS imprime el resultado como texto.
    """
    try:
        model = model_of(queue)
    except OSError as exc:
        raise AgentError(
            f'La cola de CUPS "{queue}" no existe o CUPS no contesta ({exc}). Créala como cola '
            f"RAW: lpadmin -p {queue} -E -v '<URI usb:// de la térmica>' -m raw") from None
    if any(word in (model or '').lower() for word in _DOCUMENT_DRIVERS):
        raise AgentError(
            f'La cola de CUPS "{queue}" usa el controlador «{model}», que convierte los '
            f'trabajos en PostScript o PDF: la térmica los imprimiría como texto. Usa una '
            f"cola RAW (lpadmin -p POS80_RAW -E -v '<la misma URI usb://>' -m raw) y pon "
            f'su nombre en "queue". Mira el README del agente.')


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """
    El agente no sigue redirecciones.

    `urllib` las seguiría llevándose la cabecera `Authorization` a otro host, o
    de https a http: el token saldría hacia donde el servidor no dijo. Una
    redirección aquí es una configuración equivocada, y se trata como un error.
    """

    def redirect_request(self, *args, **kwargs):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def http_json(method: str, url: str, token: str, body=None, timeout: float = 20.0):
    """(estado, cuerpo JSON o None). No levanta por un estado de error."""
    data = None if body is None else json.dumps(body).encode('utf-8')
    request = urllib.request.Request(url, data=data, method=method, headers={
        'Authorization': f'PrintAgent {token}',
        'Content-Type': 'application/json',
        'Accept': 'application/json',
    })
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            raw, code = response.read(), response.status
    except urllib.error.HTTPError as exc:
        raw, code = exc.read(), exc.code
    except http.client.HTTPException as exc:
        # Una respuesta cortada o mal formada es un fallo de red como otro.
        raise OSError(f'respuesta inválida del servidor ({type(exc).__name__})') from None
    try:
        return code, json.loads(raw.decode('utf-8') or 'null')
    except ValueError:
        return code, None


# ---------------------------------------------------------------------------
# El agente
# ---------------------------------------------------------------------------

class Agent:
    def __init__(self, config: dict, *, http=http_json, printer=send_to_printer, journal=None,
                 cups_printer=send_to_cups_queue, queue_model=read_queue_model):
        self.config = validate_config(config)
        self.http = http
        self.printer = printer
        self.cups_printer = cups_printer
        # Antes de recoger nada: una cola `cups_raw` que no es RAW no arranca.
        for route in self.config['printers'].values():
            if route['transport'] == 'cups_raw':
                check_raw_queue(route['queue'], queue_model)
        self.journal = journal or Journal(self.config['journal'])

    def _url(self, path: str) -> str:
        return f"{self.config['server']}/api/v1/print-agent/{path}"

    def _report(self, job_id, claim_token: str, *, ok: bool, error: str = '',
                outcome: str = '') -> bool:
        """True si el servidor cerró (o ya había cerrado) esta entrega."""
        try:
            code, _body = self.http(
                'POST', self._url(f'jobs/{job_id}/result/'), self.config['token'],
                {'claim_token': claim_token, 'ok': ok, 'error': error[:300],
                 # `refused` y `queued_not_printed`: el servidor lo cierra como
                 # fallido sin volver a encolarlo. PRINT-THERMAL-01.
                 **({'outcome': outcome} if outcome else {})})
        except OSError as exc:
            logger.warning('No se pudo confirmar el trabajo %s (%s).', job_id, type(exc).__name__)
            return False
        # 409 y 404: esa entrega ya no es la vigente o el trabajo ya no es
        # nuestro. No hay nada más que este agente pueda decir sobre ella.
        return code in (200, 404, 409)

    def _flush_unconfirmed(self):
        for key, entry in list(self.journal.unconfirmed.items()):
            job_id, claim_token = entry[0], entry[1]
            outcome, error = (entry[2], entry[3]) if len(entry) >= 4 else ('', '')
            if outcome:
                reported = self._report(
                    job_id, claim_token, ok=False, error=error, outcome=outcome)
            else:
                reported = self._report(job_id, claim_token, ok=True)
            if reported:
                self.journal.confirmed(key)

    def run_once(self) -> str:
        """
        Un ciclo. Devuelve qué pasó: idle, printed, failed, refused,
        queued_not_printed, skipped, offline, unauthorised, error.
        """
        self._flush_unconfirmed()
        try:
            code, body = self.http('POST', self._url('jobs/claim/'), self.config['token'], {})
        except OSError as exc:
            logger.warning('Sin conexión con el servidor (%s).', type(exc).__name__)
            return 'offline'
        if code == 401:
            logger.error('El servidor no reconoce este agente: token revocado o mal copiado.')
            return 'unauthorised'
        if code != 200 or not isinstance(body, dict):
            # Una dirección mal escrita, una redirección, un límite, un fallo del
            # servidor: nada de eso es «no hay trabajo». Se dice.
            logger.warning('El servidor respondió %s al pedir trabajo; se reintenta.', code)
            return 'error'
        job = body.get('job')
        if not job:
            return 'idle'

        job_id, claim_token = job['id'], job['claim_token']
        # El número de trabajo se repite si la base se restaura o si el agente
        # cambia de servidor; el identificador estable, no.
        printed_key = str(job.get('uid') or f"{self.config['server']}#{job_id}")

        held = self.journal.held_outcome(printed_key)
        if held is not None:
            # Ya está en una cola de CUPS: mandarlo otra vez podría imprimirlo
            # dos veces. Se repite al servidor lo que se sabe. PRINT-THERMAL-01.
            outcome, error = held
            logger.info('Trabajo %s ya está en la cola de CUPS: no se vuelve a enviar.', job_id)
            if self._report(job_id, claim_token, ok=False, error=error, outcome=outcome):
                self.journal.confirmed(printed_key)
            else:
                self.journal.needs_confirmation(
                    printed_key, job_id, claim_token, outcome, error)
            return 'skipped'

        if self.journal.was_printed(printed_key):
            # Ya salió por la impresora; lo que se perdió fue la confirmación.
            logger.info('Trabajo %s ya impreso: se confirma sin volver a imprimir.', job_id)
            if self._report(job_id, claim_token, ok=True):
                self.journal.confirmed(printed_key)
            else:
                self.journal.needs_confirmation(printed_key, job_id, claim_token)
            return 'skipped'

        try:
            payload = base64.b64decode(job.get('payload_base64') or '', validate=True)
        except ValueError as exc:
            logger.warning('Trabajo %s: los bytes llegaron dañados (%s).', job_id, exc)
            self._report(job_id, claim_token, ok=False, error=f'ValueError: {exc}'[:200])
            return 'failed'
        # SÓLO ESC/POS, diga lo que diga el servidor. Un PDF o un PostScript en
        # una térmica sale como metros de código fuente. PRINT-THERMAL-01.
        problem = (
            payload_problem(payload) if job.get('kind') in KNOWN_KINDS
            else f"Tipo de documento desconocido ({job.get('kind')}): no se imprime.")
        if problem:
            logger.error('Trabajo %s rechazado sin enviarlo: %s', job_id, problem)
            self._report(job_id, claim_token, ok=False, error=problem, outcome='refused')
            return 'refused'

        printer = job.get('printer') or {}
        route = self.config['printers'].get(str(printer.get('name') or ''), {'transport': 'tcp'})
        host, port = str(printer.get('host', '')), int(printer.get('port') or 9100)
        if route['transport'] == 'cups_raw':
            # Una térmica USB de este equipo: la dirección del panel no se usa.
            target = f"cola CUPS {route['queue']}"
            deliver = lambda: self.cups_printer(  # noqa: E731
                route['queue'], payload, self.config['printer_timeout'],
                self.config['cups_wait_seconds'])
        else:
            if not is_local_address(host, allow_loopback=self.config['allow_loopback']):
                logger.error(
                    'Trabajo %s: la impresora no está en la red local; no se imprime.', job_id)
                self._report(job_id, claim_token, ok=False,
                             error='La dirección de la impresora no es de la red local.')
                return 'failed'
            target = host
            deliver = lambda: self.printer(  # noqa: E731
                host, port, payload, self.config['printer_timeout'])

        try:
            deliver()
        except CupsNotPrinted as exc:
            # Pudo salir o salir más tarde. PRIMERO el diario: este uid no vuelve
            # a CUPS aunque el aviso al servidor se pierda y el plazo venza. Una
            # persona que lo reenvía desde el panel crea otro uid.
            reason = str(exc)[:300]
            logger.error('Trabajo %s: %s', job_id, reason)
            self.journal.record_held(
                printed_key, job_id, claim_token, 'queued_not_printed', reason)
            if self._report(job_id, claim_token, ok=False, error=reason,
                            outcome='queued_not_printed'):
                self.journal.confirmed(printed_key)
            return 'queued_not_printed'
        except (OSError, ValueError) as exc:
            reason = f'{type(exc).__name__}: {exc}'[:200]
            logger.warning('Trabajo %s: la impresora %s no respondió (%s).', job_id, target, reason)
            self._report(job_id, claim_token, ok=False, error=reason)
            return 'failed'

        # PRIMERO el diario, DESPUÉS el servidor. Si se va la luz entre los dos,
        # al volver el diario dice que ya se imprimió.
        self.journal.record_printed(printed_key, job_id, claim_token)
        if self._report(job_id, claim_token, ok=True):
            self.journal.confirmed(printed_key)
        logger.info('Trabajo %s impreso en %s.', job_id, printer.get('name') or host)
        return 'printed'

    def safe_cycle(self) -> str:
        """Un ciclo que no puede tumbar al agente, pase lo que pase dentro."""
        try:
            return self.run_once()
        except Exception:  # noqa: BLE001 - un programa desatendido no se cae
            logger.exception('Ciclo interrumpido por un error inesperado.')
            return 'error'

    def pause_after(self, outcome: str) -> float:
        """Cuánto esperar antes del siguiente ciclo."""
        if outcome in ('printed', 'skipped'):
            return 0.0                              # puede haber otro esperando
        if outcome == 'unauthorised':
            return 60.0
        if outcome in ('failed', 'refused', 'queued_not_printed', 'error', 'offline'):
            # Con la impresora apagada o el servidor caído, insistir sin pausa
            # sólo quema los intentos del trabajo.
            return max(10.0, self.config['poll_seconds'] * 3)
        return self.config['poll_seconds']

    def run_forever(self):
        logger.info('Agente de impresión en marcha contra %s.', self.config['server'])
        while True:
            pause = self.pause_after(self.safe_cycle())
            if pause:
                time.sleep(pause)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Agente de impresión de la tienda.')
    parser.add_argument('--config', default='config.json')
    parser.add_argument('--once', action='store_true', help='Un solo ciclo y termina.')
    arguments = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    try:
        agent = Agent(load_config(arguments.config))
    except (AgentError, OSError, ValueError) as exc:
        print(f'No se puede arrancar: {exc}', file=sys.stderr)
        return 2
    if arguments.once:
        print(agent.run_once())
        return 0
    try:
        agent.run_forever()
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == '__main__':
    sys.exit(main())
