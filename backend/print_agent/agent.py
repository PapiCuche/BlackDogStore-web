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
import socket
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
    }


# ---------------------------------------------------------------------------
# El diario: qué se imprimió ya y qué falta por confirmar
# ---------------------------------------------------------------------------

class Journal:
    """
    `printed`: claves de los trabajos que ya salieron por la impresora.
    `unconfirmed`: los impresos cuya confirmación todavía no llegó al servidor,
    con el número de trabajo y el token de la entrega para volver a intentarlo.
    """

    def __init__(self, path: str):
        self.path = path
        self.printed: list[str] = []
        self.unconfirmed: dict[str, tuple] = {}
        try:
            with open(path, encoding='utf-8') as handle:
                data = json.load(handle)
            self.printed = [str(key) for key in data.get('printed', [])]
            self.unconfirmed = {
                str(key): (value[0], value[1]) for key, value in data.get('unconfirmed', {}).items()}
        except (OSError, ValueError, TypeError, IndexError):
            pass

    def _save(self):
        keep = set(self.unconfirmed)
        overflow = len(self.printed) - JOURNAL_LIMIT
        if overflow > 0:
            self.printed = [
                key for index, key in enumerate(self.printed) if index >= overflow or key in keep]
        temporary = self.path + '.tmp'
        with open(temporary, 'w', encoding='utf-8') as handle:
            json.dump({'printed': self.printed, 'unconfirmed': self.unconfirmed}, handle)
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

    def needs_confirmation(self, key, job_id, claim_token: str):
        self.unconfirmed[str(key)] = (job_id, claim_token)
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


def send_to_printer(host: str, port: int, payload: bytes, timeout: float) -> None:
    """Entrega los bytes a la impresora. Levanta OSError si no se pudo."""
    with socket.create_connection((host, port), timeout=timeout) as connection:
        connection.settimeout(timeout)
        connection.sendall(payload)
        try:
            connection.shutdown(socket.SHUT_WR)
        except OSError:
            pass


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
    def __init__(self, config: dict, *, http=http_json, printer=send_to_printer, journal=None):
        self.config = validate_config(config)
        self.http = http
        self.printer = printer
        self.journal = journal or Journal(self.config['journal'])

    def _url(self, path: str) -> str:
        return f"{self.config['server']}/api/v1/print-agent/{path}"

    def _report(self, job_id, claim_token: str, *, ok: bool, error: str = '') -> bool:
        """True si el servidor cerró (o ya había cerrado) esta entrega."""
        try:
            code, _body = self.http(
                'POST', self._url(f'jobs/{job_id}/result/'), self.config['token'],
                {'claim_token': claim_token, 'ok': ok, 'error': error[:300]})
        except OSError as exc:
            logger.warning('No se pudo confirmar el trabajo %s (%s).', job_id, type(exc).__name__)
            return False
        # 409 y 404: esa entrega ya no es la vigente o el trabajo ya no es
        # nuestro. No hay nada más que este agente pueda decir sobre ella.
        return code in (200, 404, 409)

    def _flush_unconfirmed(self):
        for key, (job_id, claim_token) in list(self.journal.unconfirmed.items()):
            if self._report(job_id, claim_token, ok=True):
                self.journal.confirmed(key)

    def run_once(self) -> str:
        """Un ciclo. Devuelve qué pasó: idle, printed, failed, skipped, offline, unauthorised."""
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

        if self.journal.was_printed(printed_key):
            # Ya salió por la impresora; lo que se perdió fue la confirmación.
            logger.info('Trabajo %s ya impreso: se confirma sin volver a imprimir.', job_id)
            if self._report(job_id, claim_token, ok=True):
                self.journal.confirmed(printed_key)
            else:
                self.journal.needs_confirmation(printed_key, job_id, claim_token)
            return 'skipped'

        printer = job.get('printer') or {}
        host, port = str(printer.get('host', '')), int(printer.get('port') or 9100)
        if not is_local_address(host, allow_loopback=self.config['allow_loopback']):
            logger.error('Trabajo %s: la impresora no está en la red local; no se imprime.', job_id)
            self._report(job_id, claim_token, ok=False,
                         error='La dirección de la impresora no es de la red local.')
            return 'failed'

        try:
            payload = base64.b64decode(job['payload_base64'], validate=True)
            self.printer(host, port, payload, self.config['printer_timeout'])
        except (OSError, ValueError) as exc:
            reason = f'{type(exc).__name__}: {exc}'[:200]
            logger.warning('Trabajo %s: la impresora %s no respondió (%s).', job_id, host, reason)
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
        if outcome in ('failed', 'error', 'offline'):
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
