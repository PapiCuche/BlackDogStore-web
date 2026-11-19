#!/usr/bin/env python3
"""
Agente de impresión de la tienda.

QUÉ ES. Un programa pequeño que se deja encendido en un equipo DENTRO del local
(una PC de caja, una Raspberry Pi). Cada pocos segundos pregunta al servidor si
hay un ticket para su sucursal; si lo hay, lo entrega a la impresora térmica —por
la red (puerto 9100) o, en Windows, a una cola USB de este mismo equipo— y
confirma.

POR QUÉ HACE FALTA. El servidor está en Internet y la impresora en el local: el
servidor no puede llegar a ella, y no debe. El teléfono que cobra tampoco tiene
una impresora térmica. El agente es el único que ve a los dos, y es él quien
llama al servidor, nunca al revés: no hay que abrir ningún puerto del local.

QUÉ PUEDE HACER SU TOKEN. Recoger trabajos de impresión de UNA sucursal y decir
cómo salieron. Nada más: no abre el panel ni lee ventas.

SÓLO LA BIBLIOTECA ESTÁNDAR. Se copia un archivo y se ejecuta con Python 3.9+.

    python3 agent.py --config config.json          (macOS / Linux)
    py -3 agent.py --config C:\\ruta\\config.json    (Windows)

EL PAPEL NO SALE DOS VECES. El servidor entrega cada trabajo «al menos una
vez»: si la confirmación se pierde, lo vuelve a entregar. El agente apunta en un
diario lo que ya imprimió, y ante una segunda entrega del mismo trabajo confirma
sin volver a imprimir.

Y LO INCIERTO NO SE REPITE SOLO (WIN-003B). Antes de mandar los bytes, el
diario apunta el trabajo como «en vuelo». Si el equipo se apaga entre la entrega
y la anotación de «impreso», al volver no se sabe si el papel salió: ese trabajo
NO se vuelve a mandar. Se informa al servidor como fallido con el motivo, y una
persona que mira el mostrador decide reimprimirlo desde el panel (un trabajo
nuevo, con otro identificador).
"""
from __future__ import annotations

import argparse
import base64
import http.client
import ipaddress
import json
import logging
import logging.handlers
import os
import re
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger('print_agent')

AGENT_VERSION = '1.4-label04'
#: Con qué nombre se presenta al servidor. El de `urllib` por defecto
#: (`Python-urllib/3.x`) lo bloquea el cortafuegos web de producción
#: (Cloudflare 1010), y el agente no llegaba nunca a Django. WIN-003B.
USER_AGENT = f'BlackDogStore-PrintAgent/{AGENT_VERSION}'

JOURNAL_LIMIT = 500
#: Lo que es una red de un local. Escrito a mano y no con `is_private`: lo que
#: esa propiedad considera privado cambia entre versiones de Python.
_LOCAL_NETWORKS = tuple(ipaddress.ip_network(n) for n in (
    '10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '169.254.0.0/16',
    'fc00::/7', 'fe80::/10',
))
_LOOPBACK = tuple(ipaddress.ip_network(n) for n in ('127.0.0.0/8', '::1/128'))
_LOCAL_NAME = re.compile(r'^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.local$', re.IGNORECASE)

#: Cómo llega este agente a una impresora. `tcp`: la dirección y el puerto que
#: guarda el panel. `windows_raw`: una cola de impresión de ESTE equipo Windows
#: (una térmica USB), por su nombre, en modo RAW.
TRANSPORTS = ('tcp', 'windows_raw')
#: Qué clase de impresora del panel atiende cada transporte (WIN-003C).
_CONNECTION_OF = {'tcp': 'network', 'windows_raw': 'local_queue'}
#: Un ticket de 80 mm con logotipo ocupa unos KB. Más de 1 MiB no es un ticket.
MAX_PAYLOAD_BYTES = 1024 * 1024
MAX_PAYLOAD_BASE64 = 4 * ((MAX_PAYLOAD_BYTES + 2) // 3)
#: La espera a que Windows saque el trabajo de su cola tiene que caber, junto
#: con la entrega, en el plazo del servidor (90 s).
SPOOL_WAIT_MAX = 45.0
SPOOL_WAIT_DEFAULT = 20.0
IS_WINDOWS = sys.platform == 'win32'

UNCERTAIN_REASON = (
    'INCIERTO: el agente se interrumpió mientras entregaba este ticket y no sabe si '
    'salió. No se reimprime solo. Si no salió: pida una reimpresión nueva, o libérelo en la '
    'PC del agente (--release-held) y use Reintentar.')


class AgentError(Exception):
    """La configuración no permite arrancar."""


#: Cuánto se espera, además de `printer_timeout + spool_wait_seconds`, a que una
#: llamada a la cola de Windows vuelva. Por debajo del préstamo del servidor (90 s).
SPOOLER_DEADLINE_MARGIN = 20.0
#: Código de salida tras una llamada colgada: la tarea programada lo vuelve a arrancar.
EXIT_SPOOLER_HUNG = 3
#: El plazo nunca llega al préstamo del servidor (90 s): el aviso llega antes.
SPOOLER_DEADLINE_MAX = 80.0
#: Junto al diario: «una llamada a la cola se colgó». Mientras exista, el agente
#: no pide trabajo; `--check` lo borra cuando la cola vuelve a responder.
HUNG_MARKER_SUFFIX = '.cola-colgada'
#: Cada cuánto mira, mientras espera, si alguien borró el aviso.
HUNG_MARKER_POLL = 30.0


class SpoolerHung(Exception):
    """Una llamada a la cola de Windows no volvió a tiempo (controlador o spooler colgado)."""


def call_with_deadline(fn, seconds: float):
    """
    `fn()` con un plazo. Si no vuelve a tiempo, SpoolerHung; la llamada sigue
    colgada en su hilo (una llamada de Windows no se puede cortar desde fuera),
    así que quien la recibe tiene que dar el trabajo por incierto y terminar.
    """
    box = {}

    def run():
        try:
            box['result'] = fn()
        except BaseException as exc:  # noqa: BLE001 - se vuelve a lanzar en el hilo principal
            box['error'] = exc

    thread = threading.Thread(target=run, name='cola-windows', daemon=True)
    thread.start()
    thread.join(seconds)
    if thread.is_alive():
        raise SpoolerHung(f'la cola de Windows no respondió en {seconds:.0f} s')
    if 'error' in box:
        raise box['error']
    return box.get('result')


def _hard_exit(code: int):
    """
    Termina aunque un hilo siga dentro de una llamada de Windows colgada. En
    Windows, TerminateProcess: `os._exit` pasa por ExitProcess, que todavía
    avisa a las DLL y puede quedarse esperando al hilo colgado (revisión de #352).
    """
    logging.shutdown()
    if IS_WINDOWS:
        try:
            import ctypes

            kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel32.GetCurrentProcess.restype = ctypes.c_void_p
            kernel32.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
            kernel32.TerminateProcess(kernel32.GetCurrentProcess(), code)
        except (OSError, AttributeError):
            pass
    os._exit(code)


class DeliveryUncertain(Exception):
    """
    Los bytes pudieron llegar a la impresora, o llegar más tarde, y no consta.

    Mandarlos otra vez podría imprimir el ticket dos veces. El trabajo queda
    retenido en el diario y se informa como fallido con este motivo.
    """


# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------

def load_config(path: str) -> dict:
    path = os.path.abspath(path)
    with open(path, encoding='utf-8') as handle:
        config = json.load(handle)
    # Las rutas relativas (diario, registro) se leen junto al archivo de
    # configuración, no donde se lanzó el programa: el Programador de tareas
    # de Windows arranca en C:\Windows\System32.
    validated = validate_config(config, base_dir=os.path.dirname(path))
    # Antes de WIN-003B el diario relativo se buscaba en la carpeta de trabajo.
    # Si el de siempre está allí y no junto a config.json, arrancar con uno vacío
    # volvería a imprimir lo pendiente: se para y se explica.
    raw = str(config.get('journal', 'print-agent-journal.json'))
    legacy = os.path.abspath(os.path.expandvars(os.path.expanduser(raw)))
    if (not os.path.isabs(os.path.expanduser(raw)) and legacy != validated['journal']
            and os.path.exists(legacy) and not os.path.exists(validated['journal'])):
        raise AgentError(
            f'El diario está en {legacy}, pero ahora se busca en {validated["journal"]}. '
            f'Muévalo allí (con el agente detenido) o ponga su ruta completa en "journal".')
    return validated


def _absolute(path: str, base_dir: str | None) -> str:
    path = os.path.expandvars(os.path.expanduser(path))
    if not os.path.isabs(path) and base_dir:
        path = os.path.join(base_dir, path)
    return os.path.abspath(path)


def _spool_wait(raw) -> float:
    try:
        wait = float(raw)
    except (TypeError, ValueError):
        raise AgentError('"spool_wait_seconds" tiene que ser un número.') from None
    if not 1.0 <= wait <= SPOOL_WAIT_MAX:
        raise AgentError(
            f'"spool_wait_seconds" tiene que estar entre 1 y {SPOOL_WAIT_MAX:.0f} segundos.')
    return wait


def valid_windows_queue(queue) -> bool:
    """
    ¿Es un nombre de cola LOCAL de Windows, y nada más?

    `OpenPrinterW` entiende más que nombres: `\\\\servidor\\cola` (otro equipo),
    `nombre,Job 12` (un trabajo), `,XcvMonitor …` (la administración de un
    puerto). Ninguno de esos es una impresora de este mostrador: se rechazan
    las barras iniciales, las comas y los caracteres de control.
    """
    if not isinstance(queue, str) or not queue or len(queue) > 220 or queue != queue.strip():
        return False
    if queue.startswith(('\\', '/')) or ',' in queue:
        return False
    return not any(ord(c) < 32 or ord(c) == 127 for c in queue)


_ID_KEY = re.compile(r'^id:([1-9][0-9]{0,9})$')


def printers_by_id(printers: dict) -> bool:
    """¿La lista identifica las impresoras por su id del panel (`id:<n>`)?"""
    return bool(printers) and all(_ID_KEY.match(key) for key in printers)


def _validate_printers(raw) -> dict:
    """
    Cómo llega este agente a cada impresora del panel.

        "printers": {"id:12": {"transport": "windows_raw", "queue": "POS-80-Series"}}

    La clave es el ID de la impresora en el panel (`id:<n>`): no cambia aunque
    la impresora se renombre (WIN-003C). Por compatibilidad con un servidor
    anterior, que no manda ids, se admite el NOMBRE como clave, pero no mezclado
    con ids.

    Sin "printers", todas van por red (`tcp`), como siempre. CON "printers",
    sólo se imprime en las que están en la lista. El nombre de la cola sale
    SIEMPRE de este archivo, nunca del servidor.
    """
    if raw in (None, {}):
        return {}
    if not isinstance(raw, dict):
        raise AgentError('"printers" tiene que ser un objeto: {"id:<n>": {...}}.')
    by_id = [bool(_ID_KEY.match(str(key))) for key in raw]
    if any(by_id) and not all(by_id):
        raise AgentError('"printers": use sólo ids ("id:12") o sólo nombres, no los dos.')
    if not any(by_id) and any(str(key).lower().startswith('id:') for key in raw):
        raise AgentError('"printers": un id se escribe "id:" seguido del número (por ejemplo "id:12").')
    printers = {}
    for name, entry in raw.items():
        entry = entry or {}
        if not isinstance(entry, dict) or not str(name).strip():
            raise AgentError(f'"printers" › "{name}" tiene que ser un objeto con nombre.')
        transport = str(entry.get('transport') or 'tcp')
        if transport not in TRANSPORTS:
            raise AgentError(
                f'"printers" › "{name}": transporte "{transport}" desconocido. '
                f'Use "tcp" o "windows_raw".')
        if transport == 'tcp':
            printers[str(name)] = {'transport': 'tcp'}
            continue
        if not IS_WINDOWS:
            raise AgentError(f'"printers" › "{name}": "windows_raw" sólo funciona en Windows.')
        queue = entry.get('queue')
        if not valid_windows_queue(queue):
            raise AgentError(
                f'"printers" › "{name}": "windows_raw" necesita "queue" con el nombre exacto de '
                f'una cola de ESTE equipo (por ejemplo POS-80-Series), sin "\\\\", "/" ni comas.')
        printers[str(name)] = {'transport': 'windows_raw', 'queue': queue}
    return printers


def validate_config(config: dict, *, base_dir: str | None = None) -> dict:
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
    log_file = str(config.get('log_file') or '')
    return {
        'server': server,
        'token': token,
        'poll_seconds': max(1.0, float(config.get('poll_seconds', 3))),
        'printer_timeout': max(1.0, float(config.get('printer_timeout', 10))),
        'journal': _absolute(str(config.get('journal', 'print-agent-journal.json')), base_dir),
        'log_file': _absolute(log_file, base_dir) if log_file else '',
        'printers': _validate_printers(config.get('printers')),
        'spool_wait_seconds': _spool_wait(config.get('spool_wait_seconds', SPOOL_WAIT_DEFAULT)),
        # Sólo para pruebas: una impresora simulada en este mismo equipo.
        'allow_loopback': bool(config.get('allow_loopback', False)),
    }


# ---------------------------------------------------------------------------
# El diario: qué se imprimió ya y qué falta por confirmar
# ---------------------------------------------------------------------------

class JournalError(AgentError):
    """El diario existe y no se puede leer, o no se puede escribir."""


class Journal:
    """
    `printed`: claves de los trabajos que ya se entregaron y NO se vuelven a
    mandar: los que salieron y los retenidos.
    `held`: de esos, los RETENIDOS — los que pudieron salir o no —, con el
    motivo que se repite al servidor cada vez que lo vuelve a entregar.
    `unconfirmed`: lo que el servidor todavía no sabe: número de trabajo, token
    de la entrega y, si no salió, el motivo.

    Sólo identificadores y motivos técnicos: ni bytes del ticket, ni datos del
    cliente, ni el token del agente.
    """

    def __init__(self, path: str):
        self.path = path
        self.printed: list[str] = []
        self.held: dict[str, str] = {}
        self.unconfirmed: dict[str, tuple] = {}
        try:
            with open(path, encoding='utf-8') as handle:
                data = json.load(handle)
        except FileNotFoundError:
            return                                   # primer arranque
        except (OSError, ValueError) as exc:
            # Empezar de cero con un diario ilegible volvería a imprimir lo que
            # ya salió. Se para y se avisa.
            raise JournalError(
                f'El diario {path} no se puede leer ({type(exc).__name__}). Revíselo o '
                f'restaure su copia antes de arrancar.') from None
        printed = data.get('printed', []) if isinstance(data, dict) else None
        held = data.get('held', {}) if isinstance(data, dict) else None
        unconfirmed = data.get('unconfirmed', {}) if isinstance(data, dict) else None
        if not (isinstance(printed, list) and isinstance(held, dict)
                and isinstance(unconfirmed, dict)
                and all(isinstance(value, list) and len(value) >= 2
                        for value in unconfirmed.values())):
            raise JournalError(f'El diario {path} tiene un formato inesperado.')
        self.printed = [str(key) for key in printed]
        self.held = {str(key): str(value) for key, value in held.items()}
        # Un diario de antes de WIN-003B sólo tiene (trabajo, token).
        self.unconfirmed = {str(key): tuple(value) for key, value in unconfirmed.items()}

    def _save(self):
        keep = set(self.unconfirmed) | set(self.held)
        overflow = len(self.printed) - JOURNAL_LIMIT
        if overflow > 0:
            self.printed = [
                key for index, key in enumerate(self.printed) if index >= overflow or key in keep]
        temporary = self.path + '.tmp'
        with open(temporary, 'w', encoding='utf-8') as handle:
            json.dump({'printed': self.printed, 'held': self.held,
                       'unconfirmed': self.unconfirmed}, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.path)

    def ensure_writable(self):
        """Antes de imprimir nada: si el diario no se puede escribir, no se arranca."""
        directory = os.path.dirname(self.path) or '.'
        if not os.path.isdir(directory):
            raise JournalError(f'La carpeta del diario no existe: {directory}')
        try:
            self._save()
        except OSError as exc:
            raise JournalError(
                f'El diario {self.path} no se puede escribir ({type(exc).__name__}): '
                f'el agente no imprimiría sin poder recordar lo impreso.') from None

    def was_printed(self, key) -> bool:
        return str(key) in self.printed

    def held_reason(self, key):
        """El motivo si este trabajo está retenido; si no, None."""
        return self.held.get(str(key))

    def begin(self, key):
        """
        Justo ANTES de entregar los bytes: «en vuelo». Si el programa muere
        antes de `record_printed` o `abandon`, al volver queda retenido.
        """
        if str(key) not in self.printed:
            self.printed.append(str(key))
        self.held[str(key)] = UNCERTAIN_REASON
        self._save()

    def abandon(self, key):
        """No salió, con certeza: se puede volver a intentar."""
        self.held.pop(str(key), None)
        if str(key) in self.printed:
            self.printed.remove(str(key))
        self._save()

    def record_printed(self, key, job_id, claim_token: str):
        if str(key) not in self.printed:
            self.printed.append(str(key))
        self.held.pop(str(key), None)
        self.unconfirmed[str(key)] = (job_id, claim_token)
        self._save()

    def record_held(self, key, job_id, claim_token: str, reason: str):
        """Pudo salir o no: no se vuelve a mandar, y el servidor tiene que saberlo."""
        if str(key) not in self.printed:
            self.printed.append(str(key))
        self.held[str(key)] = reason
        self.unconfirmed[str(key)] = (job_id, claim_token, reason)
        self._save()

    def needs_confirmation(self, key, job_id, claim_token: str, reason: str = ''):
        self.unconfirmed[str(key)] = (
            (job_id, claim_token, reason) if reason else (job_id, claim_token))
        self._save()

    def confirmed(self, key):
        if self.unconfirmed.pop(str(key), None) is not None:
            self._save()

    def release(self, key) -> bool:
        """
        Una persona miró el mostrador: este trabajo retenido NO salió. Se
        olvida, y la próxima entrega del mismo trabajo se imprime.
        """
        key = str(key)
        if key not in self.held:
            return False
        self.held.pop(key)
        self.unconfirmed.pop(key, None)
        if key in self.printed:
            self.printed.remove(key)
        self._save()
        return True


class InstanceLock:
    """
    Un solo agente por diario. Dos procesos con el mismo diario se pisarían lo
    que apuntan, y el papel saldría dos veces.
    """

    def __init__(self, journal_path: str):
        self.path = journal_path + '.lock'
        self._handle = None

    def acquire(self):
        handle = open(self.path, 'a+', encoding='utf-8')
        try:
            if os.name == 'nt':
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            raise AgentError(
                f'Ya hay otro agente usando este diario ({self.path}). '
                f'Deténgalo antes de arrancar otro.') from None
        self._handle = handle
        return self

    def release(self):
        if self._handle is None:
            return
        try:
            if os.name == 'nt':
                import msvcrt

                self._handle.seek(0)
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        self._handle.close()
        self._handle = None


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


def send_to_printer(host: str, port: int, payload: bytes, timeout: float) -> None:
    """
    Entrega los bytes a una térmica de red.

    OSError: no se llegó a conectar; no salió nada. DeliveryUncertain: se
    conectó y la entrega se cortó a medias; pudo salir un trozo.
    """
    with socket.create_connection((host, port), timeout=timeout) as connection:
        try:
            connection.settimeout(timeout)
            connection.sendall(payload)
        except OSError as exc:
            raise DeliveryUncertain(
                f'La conexión con la impresora se cortó a mitad del envío ({type(exc).__name__}).'
            ) from None
        try:
            connection.shutdown(socket.SHUT_WR)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Windows: una cola de impresión de este equipo, en RAW
# ---------------------------------------------------------------------------

#: PRINTER_STATUS_* que dicen «esta cola no va a imprimir ahora».
_PRINTER_NOT_READY = {
    0x00000001: 'en pausa', 0x00000002: 'con error', 0x00000004: 'borrándose',
    0x00000008: 'atascada', 0x00000010: 'sin papel', 0x00000080: 'desconectada',
    0x00001000: 'no disponible', 0x00100000: 'necesita atención', 0x00400000: 'con la tapa abierta',
}
#: JOB_STATUS_*.
_JOB_PAUSED, _JOB_ERROR, _JOB_DELETING, _JOB_PRINTING = 0x1, 0x2, 0x4, 0x10
_JOB_OFFLINE, _JOB_PAPEROUT, _JOB_PRINTED, _JOB_DELETED = 0x20, 0x40, 0x80, 0x100
_JOB_BLOCKED, _JOB_INTERVENTION, _JOB_COMPLETE = 0x200, 0x400, 0x1000
_JOB_STUCK = _JOB_PAUSED | _JOB_ERROR | _JOB_OFFLINE | _JOB_PAPEROUT | _JOB_BLOCKED | _JOB_INTERVENTION
_ERROR_INVALID_PARAMETER = 87
_ERROR_INSUFFICIENT_BUFFER = 122
_JOB_CONTROL_DELETE = 5
_PRINTER_ACCESS_USE = 0x00000008
#: PRINTER_ATTRIBUTE_*: «imprimir después de que la última página esté en cola»
#: y «directamente a la impresora».
_PRINTER_ATTRIBUTE_QUEUED, _PRINTER_ATTRIBUTE_DIRECT = 0x1, 0x2


class WriteFailed(OSError):
    """WritePrinter falló. `accepted`: bytes que la cola ya había aceptado."""

    def __init__(self, *args, accepted: int):
        super().__init__(*args)
        self.accepted = accepted


class WindowsSpooler:
    """
    La API de impresión de Windows (`winspool.drv`) por `ctypes`, sin `pywin32`.

    Lo que hace y lo que NO afirma: Windows confirma que recibió el trabajo y
    que lo pasó al puerto de la impresora. Que el papel salió no lo sabe
    Windows; el estado «impreso» del servidor quiere decir eso, entregado.
    """

    def __init__(self):
        import ctypes
        from ctypes import wintypes

        self.ctypes, self.wintypes = ctypes, wintypes
        self.dll = ctypes.WinDLL('winspool.drv', use_last_error=True)

        class DOC_INFO_1(ctypes.Structure):
            _fields_ = [('pDocName', wintypes.LPWSTR), ('pOutputFile', wintypes.LPWSTR),
                        ('pDatatype', wintypes.LPWSTR)]

        class PRINTER_DEFAULTS(ctypes.Structure):
            _fields_ = [('pDatatype', wintypes.LPWSTR), ('pDevMode', ctypes.c_void_p),
                        ('DesiredAccess', wintypes.DWORD)]

        class PRINTER_INFO_5(ctypes.Structure):
            _fields_ = [('pPrinterName', wintypes.LPWSTR), ('pPortName', wintypes.LPWSTR),
                        ('Attributes', wintypes.DWORD), ('DeviceNotSelectedTimeout', wintypes.DWORD),
                        ('TransmissionRetryTimeout', wintypes.DWORD)]

        self.PRINTER_INFO_5 = PRINTER_INFO_5

        class JOB_INFO_1(ctypes.Structure):
            _fields_ = [('JobId', wintypes.DWORD), ('pPrinterName', wintypes.LPWSTR),
                        ('pMachineName', wintypes.LPWSTR), ('pUserName', wintypes.LPWSTR),
                        ('pDocument', wintypes.LPWSTR), ('pDatatype', wintypes.LPWSTR),
                        ('pStatus', wintypes.LPWSTR), ('Status', wintypes.DWORD),
                        ('Priority', wintypes.DWORD), ('Position', wintypes.DWORD),
                        ('TotalPages', wintypes.DWORD), ('PagesPrinted', wintypes.DWORD),
                        ('Submitted', wintypes.WORD * 8)]

        self.DOC_INFO_1, self.PRINTER_DEFAULTS, self.JOB_INFO_1 = (
            DOC_INFO_1, PRINTER_DEFAULTS, JOB_INFO_1)
        d = self.dll
        d.OpenPrinterW.argtypes = [wintypes.LPWSTR, ctypes.POINTER(wintypes.HANDLE),
                                   ctypes.POINTER(PRINTER_DEFAULTS)]
        d.OpenPrinterW.restype = wintypes.BOOL
        d.ClosePrinter.argtypes = [wintypes.HANDLE]
        d.StartDocPrinterW.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(DOC_INFO_1)]
        d.StartDocPrinterW.restype = wintypes.DWORD
        d.StartPagePrinter.argtypes = [wintypes.HANDLE]
        d.EndPagePrinter.argtypes = [wintypes.HANDLE]
        d.EndDocPrinter.argtypes = [wintypes.HANDLE]
        d.AbortPrinter.argtypes = [wintypes.HANDLE]
        d.WritePrinter.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                                   ctypes.POINTER(wintypes.DWORD)]
        d.GetPrinterW.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p,
                                  wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        d.GetJobW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                              wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        d.SetJobW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                              wintypes.DWORD]

    def _fail(self, what: str):
        code = self.ctypes.get_last_error()
        return OSError(code, f'{what} falló (error de Windows {code})')

    def open(self, queue: str):
        handle = self.wintypes.HANDLE()
        defaults = self.PRINTER_DEFAULTS(None, None, _PRINTER_ACCESS_USE)
        if not self.dll.OpenPrinterW(queue, self.ctypes.byref(handle), self.ctypes.byref(defaults)):
            raise self._fail(f'Abrir la cola «{queue}»')
        return handle

    def close(self, handle):
        self.dll.ClosePrinter(handle)

    def printer_status(self, handle) -> int:
        """
        PRINTER_INFO_6.dwStatus. Se pregunta primero el tamaño: el spooler pide
        más que el DWORD de la estructura (error 122 con un búfer de 4 bytes).
        """
        needed = self.wintypes.DWORD(0)
        self.dll.GetPrinterW(handle, 6, None, 0, self.ctypes.byref(needed))
        if needed.value < 4:
            raise self._fail('Leer el estado de la cola')
        buffer = self.ctypes.create_string_buffer(needed.value)
        if not self.dll.GetPrinterW(handle, 6, buffer, needed.value, self.ctypes.byref(needed)):
            raise self._fail('Leer el estado de la cola')
        return self.wintypes.DWORD.from_buffer_copy(buffer.raw[:4]).value

    def spools_whole_job(self, handle) -> bool:
        """
        True sólo si la cola guarda el trabajo ENTERO antes de mandarlo a la
        impresora (PRINTER_INFO_5.Attributes: QUEUED y no DIRECT). Con «empezar a
        imprimir inmediatamente» (lo habitual) o «directamente a la impresora»,
        los primeros bytes pueden salir mientras se escribe. Si no se puede leer,
        False: se supone lo que más protege de un segundo ticket.
        """
        attributes = self._attributes(handle)
        if attributes is None:
            return False
        return bool(attributes & _PRINTER_ATTRIBUTE_QUEUED) and not attributes & _PRINTER_ATTRIBUTE_DIRECT

    def prints_direct(self, handle) -> bool:
        """
        True si la cola escribe directamente en el puerto (DIRECT): un
        WritePrinter fallido pudo dejar bytes en la impresora aunque diga 0.
        Si no se puede leer, True (lo que más protege de un segundo ticket).
        """
        attributes = self._attributes(handle)
        return attributes is None or bool(attributes & _PRINTER_ATTRIBUTE_DIRECT)

    def _attributes(self, handle):
        """PRINTER_INFO_5.Attributes, o None si no se puede leer."""
        needed = self.wintypes.DWORD(0)
        self.dll.GetPrinterW(handle, 5, None, 0, self.ctypes.byref(needed))
        if needed.value < self.ctypes.sizeof(self.PRINTER_INFO_5):
            return None
        buffer = self.ctypes.create_string_buffer(needed.value)
        if not self.dll.GetPrinterW(handle, 5, buffer, needed.value, self.ctypes.byref(needed)):
            return None
        return self.ctypes.cast(buffer, self.ctypes.POINTER(self.PRINTER_INFO_5)).contents.Attributes

    def start(self, handle, document: str) -> int:
        doc = self.DOC_INFO_1(document, None, 'RAW')
        job_id = self.dll.StartDocPrinterW(handle, 1, self.ctypes.byref(doc))
        if not job_id:
            raise self._fail('Crear el trabajo RAW')
        return job_id

    def write(self, handle, payload: bytes):
        if not self.dll.StartPagePrinter(handle):
            raise WriteFailed(*self._fail('Empezar la página').args, accepted=0)
        view = memoryview(payload)
        accepted = 0
        while view:
            chunk = bytes(view[:65536])
            buffer = self.ctypes.create_string_buffer(chunk, len(chunk))
            written = self.wintypes.DWORD(0)
            if not self.dll.WritePrinter(handle, buffer, len(chunk), self.ctypes.byref(written)):
                raise WriteFailed(*self._fail('Escribir en la cola').args,
                                  accepted=accepted + written.value)
            if written.value == 0:
                raise WriteFailed('La cola no aceptó bytes.', accepted=accepted)
            accepted += written.value
            view = view[written.value:]
        self.dll.EndPagePrinter(handle)

    def abort(self, handle) -> bool:
        return bool(self.dll.AbortPrinter(handle))

    def end(self, handle):
        if not self.dll.EndDocPrinter(handle):
            raise self._fail('Cerrar el trabajo')

    def job_status(self, handle, job_id: int):
        """El estado del trabajo, o None si ya no está en la cola."""
        # El tamaño puede crecer entre la pregunta y la lectura (cambia el texto
        # de estado): se vuelve a intentar con el nuevo tamaño (error 122).
        needed = self.wintypes.DWORD(0)
        self.dll.GetJobW(handle, job_id, 1, None, 0, self.ctypes.byref(needed))
        for _ in range(3):
            if not needed.value:
                if self.ctypes.get_last_error() == _ERROR_INVALID_PARAMETER:
                    return None
                raise self._fail('Leer el trabajo')
            size = needed.value
            buffer = self.ctypes.create_string_buffer(size)
            if self.dll.GetJobW(handle, job_id, 1, buffer, size, self.ctypes.byref(needed)):
                return self.ctypes.cast(buffer, self.ctypes.POINTER(self.JOB_INFO_1)).contents.Status
            error = self.ctypes.get_last_error()
            if error == _ERROR_INVALID_PARAMETER:
                return None
            if error != _ERROR_INSUFFICIENT_BUFFER:
                break
        raise self._fail('Leer el trabajo')

    def delete(self, handle, job_id: int) -> bool:
        return bool(self.dll.SetJobW(handle, job_id, 0, None, _JOB_CONTROL_DELETE))


def _not_ready(status: int) -> str:
    return ', '.join(text for bit, text in _PRINTER_NOT_READY.items() if status & bit)


def send_to_windows_queue(queue: str, payload: bytes, timeout: float, wait: float, *,
                          spooler=None, clock=time.monotonic, sleep=time.sleep,
                          document: str = 'Ticket') -> None:
    """
    Entrega los bytes ESC/POS a una cola de Windows, en RAW, y espera a que
    salgan de ella.

    - OSError: con certeza no salió nada (cola en pausa o sin conexión, trabajo
      abortado o retirado de la cola). Se puede reintentar. Un fallo a medio
      escribir o al cerrar sólo es seguro si la cola no había aceptado ningún
      byte o si guarda el trabajo entero antes de imprimir (`spools_whole_job`);
      con «empezar a imprimir inmediatamente» es DeliveryUncertain (revisión
      final de #313, P2-1: un ticket parcial seguido de uno entero).
    - DeliveryUncertain: pudo salir, o salir más tarde. No se reintenta solo.
    - Nada: Windows pasó el trabajo al puerto de la impresora. NO es una
      confirmación de que el papel salió.
    """
    spooler = spooler or WindowsSpooler()
    handle = spooler.open(queue)
    try:
        status = spooler.printer_status(handle)
        problem = _not_ready(status)
        if problem:
            raise OSError(f'La cola «{queue}» está {problem}: no se le manda nada.')
        whole_job_first = spooler.spools_whole_job(handle)
        direct = spooler.prints_direct(handle)
        job_id = spooler.start(handle, document)
        try:
            spooler.write(handle, payload)
        except OSError as exc:
            # A medio escribir: AbortPrinter borra lo que queda en la cola. Que no
            # salió nada sólo es seguro si la cola no tenía ningún byte o si no
            # empieza a imprimir hasta tener el trabajo entero.
            if not spooler.abort(handle):
                raise DeliveryUncertain(
                    f'Windows no aceptó el ticket entero y no se pudo anular en «{queue}».') from None
            # En una cola DIRECT, WritePrinter va al puerto: puede fallar con
            # 0 aceptados y haber dejado bytes en la impresora.
            if whole_job_first or (getattr(exc, 'accepted', None) == 0 and not direct):
                raise
            raise DeliveryUncertain(
                f'Windows no aceptó el ticket entero en «{queue}» y pudo empezar a '
                f'imprimirlo; se anuló el resto ({exc}).') from None
        try:
            spooler.end(handle)
        except OSError as exc:
            if not spooler.delete(handle, job_id):
                raise DeliveryUncertain(
                    f'Windows no cerró el trabajo y no se pudo retirar de la cola ({exc}).') from None
            if whole_job_first:
                raise OSError(f'Windows no cerró el trabajo; se retiró de la cola ({exc}).') from None
            raise DeliveryUncertain(
                f'Windows no cerró el trabajo en «{queue}» y pudo empezar a imprimirlo; '
                f'se retiró de la cola ({exc}).') from None
        # DESDE AQUÍ el trabajo es de Windows: cualquier duda es INCIERTA, nunca
        # «seguro que no salió» (eso lo reimprimiría). Revisión WIN-003B, P1.
        try:
            _await_spooler(spooler, handle, job_id, queue, wait, clock, sleep)
        except (DeliveryUncertain, JobWithdrawn):
            raise
        except OSError as exc:
            raise DeliveryUncertain(
                f'Windows aceptó el ticket en «{queue}» y no se pudo seguir su estado '
                f'({type(exc).__name__}).') from None
    finally:
        spooler.close(handle)


def _await_spooler(spooler, handle, job_id, queue, wait, clock, sleep) -> None:
    """
    Espera a que Windows saque el trabajo de la cola.

    Vuelve si salió hacia la impresora. OSError SÓLO si se retiró antes de que
    empezara a enviarse; si alguna vez se vio enviándose, en error o cancelado
    por alguien, la duda es DeliveryUncertain.
    """
    deadline = clock() + wait
    seen = 0
    job_status = spooler.job_status(handle, job_id)
    while job_status is not None and not job_status & (_JOB_PRINTED | _JOB_COMPLETE):
        seen |= job_status
        if job_status & _JOB_STUCK and not job_status & _JOB_PRINTING:
            break
        if clock() >= deadline:
            break
        sleep(0.25)
        job_status = spooler.job_status(handle, job_id)
    if job_status is None or job_status & (_JOB_PRINTED | _JOB_COMPLETE):
        if seen & (_JOB_DELETING | _JOB_DELETED):
            # Alguien lo canceló en la cola de Windows: no se dice «impreso».
            raise DeliveryUncertain(f'El ticket se canceló en la cola «{queue}» mientras salía.')
        return
    seen |= job_status
    if seen & _JOB_PRINTING:
        # Los bytes ya iban hacia la impresora: retirarlo cortaría un ticket a
        # la mitad, y dejarlo puede imprimirlo. No se sabe.
        raise DeliveryUncertain(
            f'Windows empezó a enviar el ticket a «{queue}» y no terminó a tiempo.')
    if spooler.delete(handle, job_id):
        raise JobWithdrawn(
            f'El ticket no salió de la cola «{queue}» (estado {job_status:#x}); se retiró.')
    raise DeliveryUncertain(
        f'El ticket quedó en la cola «{queue}» y no se pudo retirar: puede salir más tarde.')


class JobWithdrawn(OSError):
    """Atascado antes de empezar a enviarse, y retirado de la cola: no salió."""


def check_windows_queue(queue: str, *, spooler=None) -> str:
    """Abre la cola y lee su estado, sin imprimir. '' si está lista; el motivo si no."""
    spooler = spooler or WindowsSpooler()
    handle = spooler.open(queue)
    try:
        return _not_ready(spooler.printer_status(handle))
    finally:
        spooler.close(handle)


# ---------------------------------------------------------------------------
# Sólo ESC/POS
# ---------------------------------------------------------------------------

#: Los documentos que el servidor manda a una térmica. Otro tipo no se imprime.
KNOWN_KINDS = frozenset({'fiscal_ticket', 'sales_note_ticket', 'test_ticket'})
#: LABEL-04: un lote de etiquetas, en TSPL, para una impresora de etiquetas.
LABEL_KINDS = frozenset({'label_batch'})
_LEADING_NOISE = b' \t\r\n\x00\x04\x0c'
_BOMS = (b'\xef\xbb\xbf', b'\xfe\xff', b'\xff\xfe')
_HEADER_WINDOW = 128


def payload_problem(payload: bytes) -> str:
    """
    Por qué estos bytes NO se pueden mandar a una térmica ESC/POS, o '' si se puede.

    Un ticket del servidor empieza siempre por `ESC @` (`escpos.Receipt`). Un PDF
    empieza por `%PDF`; un PostScript, por `%!` — quizá tras blancos, una marca de
    orden de bytes o un envoltorio PJL. Una térmica imprimiría esos como texto.
    (Misma regla que propone PRINT-THERMAL-01, PR #120.)
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
    # Sólo el comienzo: un ticket puede contener «%PDF-» como texto. Un PDF
    # envuelto en PJL no empieza por ESC @ y lo rechaza la última regla.
    if stripped.startswith(b'%PDF'):
        return 'Son los bytes de un PDF, no un ticket ESC/POS: no se envían a la térmica.'
    if stripped.startswith(b'%!'):
        return 'Son los bytes de un PostScript, no un ticket ESC/POS: no se envían a la térmica.'
    if stripped.lower().startswith((b'<!doctype', b'<html')):
        return 'Son los bytes de una página HTML, no un ticket ESC/POS: no se envían a la térmica.'
    if not payload.startswith(b'\x1b@'):
        return 'No es un ticket ESC/POS (no empieza por ESC @): no se envía a la térmica.'
    return ''


def label_payload_problem(payload: bytes) -> str:
    """
    LABEL-04: por qué estos bytes NO son un lote de etiquetas TSPL, o '' si lo son. Un lote del
    servidor empieza siempre por `SIZE` (`labels.tspl`); un ticket ESC/POS, un PDF o una página en
    una impresora de etiquetas saldrían como basura y gastarían etiquetas.
    """
    payload = bytes(payload or b'')
    if not payload.startswith(b'SIZE '):
        return 'No es un lote de etiquetas TSPL (no empieza por SIZE): no se envía a la impresora de etiquetas.'
    return ''


def kind_problem(kind, printer_role, payload: bytes) -> str:
    """Por qué este trabajo no se imprime en esta impresora, o ''. Cada tipo, sólo a su rol."""
    if kind in LABEL_KINDS:
        if printer_role != 'label':
            return 'Un lote de etiquetas sólo se imprime en una impresora de etiquetas.'
        return label_payload_problem(payload)
    if kind in KNOWN_KINDS:
        # Un servidor anterior a LABEL-04 no dice el rol: todas eran de tickets.
        if printer_role not in (None, 'ticket'):
            return 'Un ticket no se imprime en una impresora de etiquetas.'
        return payload_problem(payload)
    return f'Tipo de documento desconocido ({str(kind)[:40]}): no se imprime.'


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

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

#: Tope de una respuesta del servidor: un ticket de 1 MiB en base64 cabe de sobra.
MAX_RESPONSE_BYTES = 4 * 1024 * 1024


def _read_capped(response) -> bytes:
    raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise OSError(f'respuesta del servidor de más de {MAX_RESPONSE_BYTES} bytes')
    return raw


def http_json(method: str, url: str, token: str, body=None, timeout: float = 20.0):
    """(estado, cuerpo JSON o None). No levanta por un estado de error."""
    data = None if body is None else json.dumps(body).encode('utf-8')
    request = urllib.request.Request(url, data=data, method=method, headers={
        'Authorization': f'PrintAgent {token}',
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'User-Agent': USER_AGENT,
    })
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            raw, code = _read_capped(response), response.status
    except urllib.error.HTTPError as exc:
        raw, code = _read_capped(exc), exc.code
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
    def __init__(self, config: dict, *, http=http_json, printer=send_to_printer,
                 windows_printer=send_to_windows_queue, journal=None, write_journal=True):
        self.config = validate_config(config)
        self.http = http
        self.printer = printer
        self.windows_printer = windows_printer
        self.exit = _hard_exit
        self.journal = journal or Journal(self.config['journal'])
        # `write_journal=False` sólo para `--check` con el agente en marcha: el
        # diario es suyo, y reescribirlo con lo leído antes podría borrarle algo.
        if write_journal:
            self.journal.ensure_writable()

    def _url(self, path: str) -> str:
        return f"{self.config['server']}/api/v1/print-agent/{path}"

    def _report(self, job_id, claim_token: str, *, ok: bool, error: str = '',
                outcome: str = '') -> bool:
        """True si el servidor cerró (o ya había cerrado) esta entrega."""
        body = {'claim_token': claim_token, 'ok': ok, 'error': error[:300]}
        if outcome:
            # Un servidor que no lo conoce lo ignora y lo trata como un fallo.
            body['outcome'] = outcome
        try:
            code, _body = self.http('POST', self._url(f'jobs/{job_id}/result/'),
                                    self.config['token'], body)
        except OSError as exc:
            logger.warning('No se pudo confirmar el trabajo %s (%s).', job_id, type(exc).__name__)
            return False
        # 409 y 404: esa entrega ya no es la vigente o el trabajo ya no es
        # nuestro. No hay nada más que este agente pueda decir sobre ella.
        return code in (200, 404, 409)

    def _flush_unconfirmed(self):
        for key, value in list(self.journal.unconfirmed.items()):
            job_id, claim_token = value[0], value[1]
            reason = value[2] if len(value) > 2 else ''
            if reason:
                done = self._report(job_id, claim_token, ok=False, error=reason,
                                    outcome='queued_not_printed')
            else:
                done = self._report(job_id, claim_token, ok=True)
            if done:
                self.journal.confirmed(key)

    def _claim_body(self) -> dict:
        """
        Lo que este agente sabe imprimir (WIN-003C). Sólo FILTRA lo que el
        servidor le da dentro de su sucursal. Sin lista, `{}`: un servidor nuevo
        le da sólo impresoras de red; uno anterior lo ignora.
        """
        routes = self.config['printers']
        if not printers_by_id(routes):
            # Sin lista, o por NOMBRE (para un servidor anterior, que no manda
            # ids): se presenta como un agente anterior. Declarar `local_queue`
            # sin ids le traería las USB de otras PC de la sucursal, que no
            # podría imprimir. Una USB en un servidor nuevo va por `id:<n>`.
            return {}
        return {
            'connections': sorted({_CONNECTION_OF[r['transport']] for r in routes.values()}),
            'printers': sorted(int(_ID_KEY.match(key).group(1)) for key in routes),
        }

    def _spooler_deadline(self) -> float:
        """El plazo de una llamada a la cola de Windows (WIN-003D), por debajo del préstamo."""
        return min(SPOOLER_DEADLINE_MAX, self.config['printer_timeout']
                   + self.config['spool_wait_seconds'] + SPOOLER_DEADLINE_MARGIN)

    @property
    def hung_marker(self) -> str:
        return self.config['journal'] + HUNG_MARKER_SUFFIX

    def _mark_hung(self, reason: str):
        """
        Revisión de #352, P2: si el controlador se cuelga pero el spooler responde,
        cada arranque tomaría el siguiente ticket y lo dejaría incierto. Con el
        aviso no se pide nada hasta que una persona lo mire (`--check`).
        """
        try:
            with open(self.hung_marker, 'w', encoding='utf-8') as handle:
                handle.write(reason[:300])
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            logger.error('No se pudo dejar el aviso de cola colgada (%s).', type(exc).__name__)

    def _flush_safely(self):
        """Las confirmaciones pendientes no tocan la cola: se mandan aunque esté colgada."""
        try:
            self._flush_unconfirmed()
        except Exception:  # noqa: BLE001 - un programa desatendido no se cae
            logger.exception('No se pudieron enviar las confirmaciones pendientes.')

    def _windows_queues(self) -> list:
        return [(name, route['queue']) for name, route in self.config['printers'].items()
                if route['transport'] == 'windows_raw']

    def _queues_respond(self) -> bool:
        """
        Al arrancar, antes de pedir trabajo: ¿responde la cola? Si una llamada
        sigue colgada (por ejemplo, tras el reinicio por otra colgada), pedir
        trabajo sólo dejaría más tickets inciertos.
        """
        for _name, queue in self._windows_queues():
            try:
                call_with_deadline(lambda q=queue: check_windows_queue(q), self._spooler_deadline())
            except SpoolerHung as exc:
                logger.error('La cola «%s» no responde (%s): no se pide trabajo.', queue, exc)
                return False
            except Exception as exc:  # noqa: BLE001 - un sondeo que falla no tumba al agente
                # No está lista (en pausa, sin papel) o el sondeo falló: no está
                # colgada. Los trabajos fallarán y volverán a la cola, como siempre.
                logger.warning('La cola «%s» no está lista (%s).', queue, type(exc).__name__)
        return True

    def _route(self, printer: dict):
        """(transporte, destino) o (None, motivo) si este equipo no la atiende."""
        name = str(printer.get('name') or '')
        connection = printer.get('connection')
        routes = self.config['printers']
        if not routes:
            if connection not in (None, 'network'):
                return None, 'Es una impresora del equipo del agente y este equipo no tiene ninguna configurada.'
            return 'tcp', None
        if printers_by_id(routes):
            printer_id = printer.get('id')
            route = routes.get(f'id:{printer_id}') if type(printer_id) is int else None
            if route is None:
                return None, f'La impresora id {str(printer_id)[:12]} no está configurada en este equipo.'
            if connection is None:
                return None, 'El servidor no dijo cómo se llega a la impresora: no se imprime.'
        else:
            route = routes.get(name)
            if route is None:
                return None, f'La impresora «{name[:60]}» no está configurada en este equipo.'
        # Coherencia: una cola local sólo para una impresora local, y la red sólo
        # para una de red. Un servidor anterior no manda `connection`.
        if connection is not None and connection != _CONNECTION_OF[route['transport']]:
            return None, (f'La impresora es «{str(connection)[:20]}» en el panel y este equipo la '
                          f'tiene configurada como «{route["transport"]}»: no se imprime.')
        if route['transport'] == 'windows_raw':
            return 'windows_raw', route['queue']
        return 'tcp', None

    def run_once(self) -> str:
        """
        Un ciclo. Devuelve qué pasó: idle, printed, failed, refused, uncertain,
        hung, skipped, offline, unauthorised, error. `hung`: la llamada a la cola
        no volvió; el agente tiene que terminar (`run_forever` lo hace).
        """
        self._flush_unconfirmed()
        try:
            code, body = self.http('POST', self._url('jobs/claim/'), self.config['token'],
                                   self._claim_body())
        except OSError as exc:
            logger.warning('Sin conexión con el servidor (%s).', type(exc).__name__)
            return 'offline'
        if code == 401:
            logger.error('El servidor no reconoce este agente: token revocado o mal copiado.')
            return 'unauthorised'
        if code != 200 or not isinstance(body, dict):
            # Una dirección mal escrita, una redirección, un límite, un cortafuegos
            # web, un fallo del servidor: nada de eso es «no hay trabajo». Se dice.
            logger.warning('El servidor respondió %s al pedir trabajo; se reintenta.', code)
            return 'error'
        job = body.get('job')
        if not job:
            return 'idle'

        job_id, claim_token = job['id'], job['claim_token']
        # El número de trabajo se repite si la base se restaura o si el agente
        # cambia de servidor; el identificador estable, no.
        printed_key = str(job.get('uid') or f"{self.config['server']}#{job_id}")

        held = self.journal.held_reason(printed_key)
        if held is not None:
            # Pudo salir ya: mandarlo otra vez podría imprimirlo dos veces. Se
            # repite al servidor lo que se sabe.
            logger.warning('Trabajo %s (uid %s) retenido: no se vuelve a enviar.',
                           job_id, printed_key)
            if self._report(job_id, claim_token, ok=False, error=held,
                            outcome='queued_not_printed'):
                self.journal.confirmed(printed_key)
            else:
                self.journal.needs_confirmation(printed_key, job_id, claim_token, held)
            return 'skipped'

        if self.journal.was_printed(printed_key):
            # Ya salió por la impresora; lo que se perdió fue la confirmación.
            logger.info('Trabajo %s ya impreso: se confirma sin volver a imprimir.', job_id)
            if self._report(job_id, claim_token, ok=True):
                self.journal.confirmed(printed_key)
            else:
                self.journal.needs_confirmation(printed_key, job_id, claim_token)
            return 'skipped'

        encoded = job.get('payload_base64') or ''
        if not isinstance(encoded, str) or len(encoded) > MAX_PAYLOAD_BASE64:
            problem = 'El ticket es demasiado grande para una térmica: no se imprime.'
            logger.error('Trabajo %s rechazado sin enviarlo: %s', job_id, problem)
            self._report(job_id, claim_token, ok=False, error=problem, outcome='refused')
            return 'refused'
        try:
            payload = base64.b64decode(encoded, validate=True)
        except ValueError as exc:
            logger.warning('Trabajo %s: los bytes llegaron dañados (%s).', job_id, exc)
            self._report(job_id, claim_token, ok=False, error=f'ValueError: {exc}'[:200])
            return 'failed'
        # Tickets ESC/POS a impresoras de tickets; lotes TSPL a las de etiquetas (LABEL-04), diga lo
        # que diga el servidor.
        problem = kind_problem(job.get('kind'), (job.get('printer') or {}).get('role'), payload)
        if problem:
            logger.error('Trabajo %s rechazado sin enviarlo: %s', job_id, problem)
            self._report(job_id, claim_token, ok=False, error=problem, outcome='refused')
            return 'refused'

        printer = job.get('printer') or {}
        transport, target = self._route(printer)
        if transport is None:
            # Por id, el servidor sólo da impresoras declaradas: si no encaja, el
            # panel y config.json no coinciden y reintentarlo no lo arregla.
            # `refused` lo da por fallido de inmediato (revisión de #333). Por
            # nombre (como un agente anterior) llega toda la red de la sucursal:
            # una ajena puede ser de otro agente, y un fallo normal la devuelve
            # a la cola. Nada se envió: no es incierto.
            logger.error('Trabajo %s: %s', job_id, target)
            final = 'refused' if printers_by_id(self.config.get('printers') or {}) else ''
            self._report(job_id, claim_token, ok=False, error=target, outcome=final)
            return 'failed'
        if transport == 'windows_raw':
            # Una térmica de ESTE equipo: la dirección del panel no se usa, y
            # la cola sale de config.json, no del servidor.
            label = f'cola «{target}»'
            # Con plazo: una llamada de Windows colgada no deja al agente parado
            # para siempre (WIN-003D).
            deliver = lambda: call_with_deadline(  # noqa: E731
                lambda: self.windows_printer(
                    target, payload, self.config['printer_timeout'],
                    self.config['spool_wait_seconds'],
                    document=f"{'Etiquetas' if job.get('kind') in LABEL_KINDS else 'Ticket'} {job_id}"),
                self._spooler_deadline())
        else:
            host, port = str(printer.get('host', '')), int(printer.get('port') or 9100)
            if not is_local_address(host, allow_loopback=self.config['allow_loopback']):
                logger.error('Trabajo %s: la impresora no está en la red local; no se imprime.', job_id)
                self._report(job_id, claim_token, ok=False,
                             error='La dirección de la impresora no es de la red local.')
                return 'failed'
            label = host
            deliver = lambda: self.printer(  # noqa: E731
                host, port, payload, self.config['printer_timeout'])

        # PRIMERO el diario: «en vuelo». Si no se puede escribir, no se imprime.
        try:
            self.journal.begin(printed_key)
        except OSError as exc:
            logger.error('Trabajo %s: el diario no se puede escribir (%s); no se imprime.',
                         job_id, type(exc).__name__)
            self._report(job_id, claim_token, ok=False,
                         error='El agente no puede escribir su diario: no imprime.')
            return 'error'

        try:
            deliver()
        except SpoolerHung as exc:
            # La llamada sigue dentro de Windows y PUEDE imprimir todavía:
            # retenido, como cualquier incierto. Nunca se reintenta solo. Pase lo
            # que pase después, el resultado es `hung`: el agente tiene que parar.
            reason = f'INCIERTO: {exc}; puede salir más tarde'[:300]
            logger.error('Trabajo %s (uid %s): %s', job_id, printed_key, reason)
            self._mark_hung(reason)
            try:
                # `begin` ya lo dejó retenido en disco: esto sólo guarda el motivo.
                self.journal.record_held(printed_key, job_id, claim_token, reason)
                if self._report(job_id, claim_token, ok=False, error=reason,
                                outcome='queued_not_printed'):
                    self.journal.confirmed(printed_key)
            except OSError as journal_error:
                logger.error('Trabajo %s: el diario no guardó el motivo (%s); sigue retenido.',
                             job_id, type(journal_error).__name__)
            return 'hung'
        except DeliveryUncertain as exc:
            reason = f'INCIERTO: {exc}'[:300]
            logger.error('Trabajo %s (uid %s): %s', job_id, printed_key, reason)
            self.journal.record_held(printed_key, job_id, claim_token, reason)
            if self._report(job_id, claim_token, ok=False, error=reason,
                            outcome='queued_not_printed'):
                self.journal.confirmed(printed_key)
            return 'uncertain'
        except (OSError, ValueError) as exc:
            reason = f'{type(exc).__name__}: {exc}'[:200]
            self.journal.abandon(printed_key)
            logger.warning('Trabajo %s: la impresora %s no respondió (%s).', job_id, label, reason)
            self._report(job_id, claim_token, ok=False, error=reason)
            return 'failed'

        # El diario, DESPUÉS el servidor. Si se va la luz entre los dos, al
        # volver el diario dice que ya se entregó.
        self.journal.record_printed(printed_key, job_id, claim_token)
        if self._report(job_id, claim_token, ok=True):
            self.journal.confirmed(printed_key)
        logger.info('Trabajo %s entregado a %s (papel no confirmado por el equipo).', job_id, label)
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
        if outcome in ('failed', 'error', 'offline', 'refused', 'uncertain'):
            # Con la impresora apagada o el servidor caído, insistir sin pausa
            # sólo quema los intentos del trabajo.
            return max(10.0, self.config['poll_seconds'] * 3)
        return self.config['poll_seconds']

    def check(self) -> list:
        """
        Comprueba todo SIN imprimir y SIN recoger trabajos: [(qué, bien, detalle)].

        El servidor se prueba confirmando el trabajo 0, que no existe: un token
        válido recibe 404 de la aplicación; uno inválido, 401. No cambia nada.
        """
        results = [('diario', True, self.config['journal'])]
        for name, route in self.config['printers'].items():
            if route['transport'] != 'windows_raw':
                continue
            label = f"cola «{route['queue']}» ({name})"
            try:
                problem = call_with_deadline(
                    lambda q=route['queue']: check_windows_queue(q), self._spooler_deadline())
                results.append((label, not problem, problem or 'lista'))
            except SpoolerHung as exc:
                results.append((label, False, f'no respondió ({exc}): controlador o spooler colgado'))
            except OSError as exc:
                results.append((label, False, str(exc)))
        if os.path.exists(self.hung_marker):
            # Una persona mira la cola: si ahora responde, el agente vuelve a pedir trabajo.
            if all(ok for what, ok, _detail in results if what.startswith('cola')):
                try:
                    os.remove(self.hung_marker)
                    results.append(('aviso de cola colgada', True,
                                    'borrado: el agente vuelve a pedir trabajo'))
                except OSError as exc:
                    results.append(('aviso de cola colgada', False,
                                    f'no se pudo borrar ({type(exc).__name__})'))
            else:
                results.append(('aviso de cola colgada', False,
                                'sigue: la cola todavía no está lista; el agente no pide trabajo'))
        try:
            code, body = self.http('POST', self._url('jobs/0/result/'), self.config['token'],
                                   {'claim_token': 'check', 'ok': False, 'error': 'check'})
        except OSError as exc:
            results.append(('servidor', False, f'sin conexión ({type(exc).__name__})'))
        else:
            if code == 404 and isinstance(body, dict) and 'detail' in body:
                results.append(('servidor', True, 'token aceptado'))
            elif code == 401:
                results.append(('servidor', False, 'token rechazado (401)'))
            else:
                results.append(('servidor', False, f'respuesta inesperada {code}'))
        return results

    def run_forever(self):
        logger.info('Agente de impresión %s en marcha contra %s.', AGENT_VERSION,
                    self.config['server'])
        self._flush_safely()
        if os.path.exists(self.hung_marker):
            # Antes se colgó una llamada a la cola: no se pide nada hasta que una
            # persona lo compruebe. Los tickets esperan en el servidor sin gastar
            # intentos, y el panel muestra el agente «Desconectado».
            logger.error('Una llamada a la cola de Windows se colgó (aviso %s). No se pide '
                         'trabajo hasta comprobarla con --check.', self.hung_marker)
            while os.path.exists(self.hung_marker):
                time.sleep(HUNG_MARKER_POLL)
                self._flush_safely()
            logger.info('Aviso de cola colgada borrado: se vuelve a pedir trabajo.')
        if not self._queues_respond():
            self._mark_hung('La cola no respondió al arrancar.')
            self.exit(EXIT_SPOOLER_HUNG)
            return
        while True:
            outcome = self.safe_cycle()
            if outcome == 'hung':
                # Un hilo sigue dentro de Windows con la cola: seguir pidiendo
                # trabajo sólo haría más inciertos. Se termina; la tarea programada
                # lo vuelve a arrancar y, al arrancar, comprueba la cola primero.
                logger.error('El agente termina: la cola de Windows no responde. '
                             'Se volverá a arrancar solo.')
                self.exit(EXIT_SPOOLER_HUNG)
                return
            pause = self.pause_after(outcome)
            if pause:
                time.sleep(pause)


def _setup_logging(log_file: str):
    handlers = [logging.StreamHandler()]
    if log_file:
        # Rotado: un agente desatendido no llena el disco.
        handlers.append(logging.handlers.RotatingFileHandler(
            log_file, maxBytes=1_000_000, backupCount=5, encoding='utf-8'))
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s',
                        handlers=handlers)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Agente de impresión de la tienda.')
    parser.add_argument('--config', default='config.json')
    parser.add_argument('--once', action='store_true', help='Un solo ciclo y termina.')
    parser.add_argument('--check', action='store_true',
                        help='Comprueba configuración, diario, colas y servidor sin imprimir.')
    parser.add_argument('--list-held', action='store_true',
                        help='Lista los trabajos retenidos (no se sabe si salieron).')
    parser.add_argument('--release-held', metavar='UID',
                        help='Tras comprobar que NO salió: deja que ese trabajo se vuelva a imprimir.')
    arguments = parser.parse_args(argv)
    try:
        config = load_config(arguments.config)
    except (AgentError, OSError, ValueError) as exc:
        print(f'No se puede arrancar: {exc}', file=sys.stderr)
        return 2
    lock = InstanceLock(config['journal'])
    if arguments.list_held or arguments.release_held:
        # Con el agente DETENIDO: el candado impide tocar el diario de uno que corre.
        try:
            lock.acquire()
            journal = Journal(config['journal'])
            if arguments.release_held:
                done = journal.release(arguments.release_held.strip())
                print('Liberado.' if done else 'Ese uid no está retenido.')
                return 0 if done else 1
            for key, reason in journal.held.items():
                print(f'{key}\t{reason}')
            return 0
        except (AgentError, OSError) as exc:
            print(f'No se puede: {exc}', file=sys.stderr)
            return 2
        finally:
            lock.release()
    if arguments.check:
        # Sin imprimir, sin recoger y sin tocar el diario de un agente en marcha.
        try:
            try:
                lock.acquire()
                running = False
            except AgentError:
                running = True
            agent = Agent(config, write_journal=not running)
            results = agent.check()
        except (AgentError, OSError, ValueError) as exc:
            print(f'No se puede: {exc}', file=sys.stderr)
            return 2
        finally:
            lock.release()
        if running:
            results.insert(1, ('agente', True, 'en marcha (diario en uso; no se ha escrito)'))
        for what, ok, detail in results:
            print(f"{'OK ' if ok else 'MAL'} {what}: {detail}")
        return 0 if all(ok for _what, ok, _detail in results) else 1
    _setup_logging(config['log_file'])
    try:
        lock.acquire()
        agent = Agent(config)
    except (AgentError, OSError, ValueError) as exc:
        lock.release()
        print(f'No se puede arrancar: {exc}', file=sys.stderr)
        return 2
    try:
        if arguments.once:
            outcome = agent.run_once()
            print(outcome)
            return EXIT_SPOOLER_HUNG if outcome == 'hung' else 0
        try:
            agent.run_forever()
        except KeyboardInterrupt:
            return 0
        return 0
    finally:
        lock.release()


if __name__ == '__main__':
    sys.exit(main())
