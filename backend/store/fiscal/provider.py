"""
La frontera con SUNAT. El dominio no conoce SOAP.

POR QUÉ UN ADAPTADOR Y NO LLAMADAS DIRECTAS
-------------------------------------------
Mañana esto puede ir por SUNAT directo, por un PSE o por un OSE. Si el dominio
supiera de sobres SOAP, cambiar de proveedor obligaría a tocar el dominio — y el
dominio es lo que no debe cambiar cuando cambia el transporte.

Hacia afuera se devuelve un objeto plano con el resultado ya interpretado. Nunca
un `Response`, nunca un árbol XML de SUNAT. Quien lo recibe no debería tener que
saber qué es un `faultstring`.

UN ERROR DE TRANSPORTE NO ES UN RECHAZO
---------------------------------------
Es la distinción que gobierna este módulo. Un timeout, un 500 o una conexión
cortada dejan la venta en un estado INCIERTO: puede que SUNAT la haya recibido.
Tratarlos como rechazo llevaría a emitir un segundo documento por una venta que
quizá ya está registrada — y un correlativo gastado no se recicla.

Por eso `ProviderOutcome` separa `TRANSPORT_ERROR` de `REJECTED`, y sólo hay
rechazo cuando SUNAT lo dice con un CDR o con un código de su rango.

DOS SERVICIOS, DOS CONTRATOS (ERP-FISCAL-3)
-------------------------------------------
El Manual del programador expone DOS servicios SOAP distintos:

- `billService` — ENVÍO: `sendBill` (síncrono, devuelve el CDR), `sendSummary`
  (asíncrono, devuelve un ticket) y `getStatus(ticket)` (estado del proceso
  asíncrono, con el CDR cuando termina).
- `billConsultService` — CONSULTA: `getStatusCdr(ruc, tipo, serie, número)`,
  que recupera el CDR de un comprobante YA emitido.

Se modelan como contratos SEPARADOS —`FiscalProvider` (envío) frente a
`FiscalConsultProvider` (consulta)— aunque compartan la infraestructura (sobre
WS-Security, POST acotado, parseo endurecido, lectura del CDR). Enviar y
reconciliar son capacidades distintas: mezclarlas en un `sunat_request` genérico
haría que un cambio en una arrastrase a la otra.

QUÉ NO SALE DE AQUÍ
-------------------
Ni la Clave SOL, ni la contraseña del certificado, ni la cabecera WS-Security.
`safe_message` está saneado para poder guardarse en una bitácora.
"""

from __future__ import annotations

import base64
import hashlib
import re
from dataclasses import dataclass, field
from enum import Enum

from lxml import etree

from .packaging import extract_cdr
from .xmlsafe import MAX_UNTRUSTED_XML_BYTES, UntrustedXmlError, parse_untrusted

#: `http://service.sunat.gob.pe` es el espacio de nombres del servicio.
SERVICE_NS = 'http://service.sunat.gob.pe'
WSSE_NS = (
    'http://docs.oasis-open.org/wss/2004/01/'
    'oasis-200401-wss-wssecurity-secext-1.0.xsd'
)
SOAP_NS = 'http://schemas.xmlsoap.org/soap/envelope/'

#: Manual del programador, «Web Services». Producción NO se declara aquí a
#: propósito: habilitarla es una decisión de otra fase y no debe estar a un
#: cambio de literal de distancia.
BETA_ENDPOINT = 'https://e-beta.sunat.gob.pe/ol-ti-itcpfegem-beta/billService'

#: `billConsultService` — servicio de CONSULTA de `getStatusCdr`. El Manual del
#: programador lo publica SÓLO en producción; no hay endpoint beta documentado.
#: Se declara aquí para tenerlo en un solo sitio, pero ERP-FISCAL-3 NO lo invoca:
#: la consulta real vive tras su propia bandera (`fiscal_consult_enabled`), que
#: está apagada en esta fase. Ver ADR-22 y `fiscal_config.resolve_consult_provider`.
PRODUCTION_CONSULT_ENDPOINT = (
    'https://e-factura.sunat.gob.pe/ol-it-wsconscpegem/billConsultService'
)


class ProviderOutcome(str, Enum):
    """
    Lo que pasó al ENVIAR, ya interpretado.

    `TRANSPORT_ERROR` y `REJECTED` son estados distintos y la diferencia importa:
    del primero se sale reintentando EL MISMO documento; del segundo, corrigiendo
    y emitiendo otro.
    """

    ACCEPTED = 'accepted'
    ACCEPTED_WITH_OBSERVATION = 'accepted_with_observation'
    REJECTED = 'rejected'
    #: SUNAT contestó pero la respuesta no permite decidir. No es rechazo.
    UNKNOWN_RESPONSE = 'unknown_response'
    #: No hubo respuesta utilizable: timeout, corte, 5xx. Reintentable.
    TRANSPORT_ERROR = 'transport_error'


@dataclass(frozen=True)
class ProviderResult:
    """El resultado de un envío (o de un CDR ya leído), sin rastro de SOAP."""

    outcome: ProviderOutcome
    #: Código de SUNAT cuando lo hay. `'0'` es aceptado.
    response_code: str = ''
    #: Mensaje ya saneado, apto para bitácora.
    safe_message: str = ''
    #: Observaciones que no impiden la validez del comprobante.
    notes: tuple[str, ...] = field(default_factory=tuple)
    #: El XML del CDR tal cual llegó, para archivarlo. Puede ser `None`.
    cdr_xml: bytes | None = None
    cdr_filename: str = ''
    #: Huellas para correlacionar sin guardar los cuerpos.
    request_sha256: str = ''
    response_sha256: str = ''


class ReconcileOutcome(str, Enum):
    """
    Lo que dijo una CONSULTA de `getStatusCdr`, ya interpretado.

    Es un vocabulario DISTINTO del envío (ERP-FISCAL-3 §18): una consulta no
    «rechaza» ni «acepta» por sí misma; recupera —o no— el CDR que sí lo dice.

    - `RESOLVED`: llegó un CDR. El veredicto terminal (aceptado / con
      observaciones / rechazado) está en `.cdr`, leído con la MISMA lógica que
      `sendBill` (no hay un segundo intérprete de CDR).
    - `NOT_AVAILABLE`: SUNAT contestó pero no entregó CDR (no consta todavía, o
      la consulta no lo devuelve). NO es «no existe» afirmado: sin un código
      oficial que lo diga, no se concluye. El comprobante queda NO TERMINAL y no
      se reenvía nada.
    - `TRANSPORT_ERROR`: no hubo respuesta utilizable. No terminal.
    - `UNKNOWN_RESPONSE`: SUNAT contestó algo que no se sabe interpretar. No
      terminal. El código crudo se conserva para mirarlo.
    """

    RESOLVED = 'resolved'
    NOT_AVAILABLE = 'not_available'
    TRANSPORT_ERROR = 'transport_error'
    UNKNOWN_RESPONSE = 'unknown_response'


@dataclass(frozen=True)
class DocumentCdrResult:
    """El resultado de consultar el CDR de un comprobante ya emitido."""

    outcome: ReconcileOutcome
    #: El `statusCode` CRUDO de `getStatusCdr` (o el `faultcode`), sin traducir.
    status_code: str = ''
    safe_message: str = ''
    #: El CDR ya interpretado, presente SÓLO cuando `outcome == RESOLVED`.
    cdr: ProviderResult | None = None
    request_sha256: str = ''
    response_sha256: str = ''


class TicketStatus(str, Enum):
    """
    Estado de un proceso asíncrono (`getStatus(ticket)` sobre `billService`).

    Códigos oficiales del Manual del programador: `0` procesó correctamente,
    `98` en proceso, `99` proceso con errores. Se conservan crudos.

    Fundación para FISCAL-4 (Resumen Diario / Comunicación de Baja); hoy el
    contrato existe y se prueba con mocks, pero no se emite ningún resumen.
    """

    COMPLETED = 'completed'
    PROCESSING = 'processing'
    ERROR = 'error'
    TRANSPORT_ERROR = 'transport_error'
    UNKNOWN_RESPONSE = 'unknown_response'


@dataclass(frozen=True)
class TicketStatusResult:
    """El estado de un ticket asíncrono. `cdr` presente cuando el proceso acabó."""

    status: TicketStatus
    status_code: str = ''
    safe_message: str = ''
    cdr: ProviderResult | None = None
    request_sha256: str = ''
    response_sha256: str = ''


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sanitize(text: str, *, limit: int = 500) -> str:
    """
    Recorta y limpia un mensaje de SUNAT para poder guardarlo.

    Los `faultstring` traen un «ticket» y trazas internas. Se conservan porque
    sirven para correlacionar con SUNAT, pero se acota la longitud: un mensaje
    de kilobytes en una columna de bitácora no es evidencia, es ruido.
    """
    clean = re.sub(r'\s+', ' ', text or '').strip()
    return clean[:limit]


class _TransportError(Exception):
    """Interno: la red no dejó una respuesta utilizable. Nunca es un rechazo."""


class _SunatSoapClient:
    """
    Infraestructura SOAP compartida por envío y consulta.

    Comparten sobre WS-Security, POST acotado, hashing y lectura del CDR — pero
    NO comparten operación: cada subclase declara su propio contrato. El endpoint
    entra por el constructor y NUNCA desde una petición: un inquilino que pudiera
    elegir la URL de SUNAT tendría un SSRF servido.
    """

    def __init__(self, *, endpoint: str, ruc: str, sol_user: str, sol_password: str,
                 timeout: float = 60.0):
        if not endpoint.startswith('https://'):
            raise ValueError('El endpoint fiscal debe ser HTTPS.')
        self._endpoint = endpoint
        self._username = f'{ruc}{sol_user}'
        self._password = sol_password
        self._timeout = timeout

    def _envelope_skeleton(self) -> tuple[etree._Element, etree._Element]:
        """
        Devuelve (envelope, body) con la cabecera WS-Security ya puesta.

        Se construye con lxml y no con una plantilla de texto porque los datos
        que van dentro (nombre de archivo, base64, identificadores) son datos:
        interpolarlos en una cadena es cómo se inyecta XML. La contraseña entra
        aquí y no sale de aquí.
        """
        env = etree.Element(f'{{{SOAP_NS}}}Envelope', nsmap={
            'soapenv': SOAP_NS, 'ser': SERVICE_NS,
        })
        header = etree.SubElement(env, f'{{{SOAP_NS}}}Header')
        security = etree.SubElement(header, f'{{{WSSE_NS}}}Security',
                                    nsmap={'wsse': WSSE_NS})
        token = etree.SubElement(security, f'{{{WSSE_NS}}}UsernameToken')
        etree.SubElement(token, f'{{{WSSE_NS}}}Username').text = self._username
        etree.SubElement(token, f'{{{WSSE_NS}}}Password').text = self._password
        body = etree.SubElement(env, f'{{{SOAP_NS}}}Body')
        return env, body

    def _post(self, envelope: bytes, soap_action: str) -> tuple[bytes, int]:
        """
        POST del sobre, con lectura ACOTADA. Levanta `_TransportError` si la red
        no deja una respuesta utilizable o si la respuesta excede el tope.
        """
        import requests

        try:
            response = requests.post(
                self._endpoint, data=envelope,
                headers={'Content-Type': 'text/xml; charset=utf-8',
                         'SOAPAction': soap_action},
                timeout=self._timeout, stream=True,
            )
            # Lectura ACOTADA: la respuesta pesa kilobytes; no se carga a memoria
            # un cuerpo ilimitado. Se lee un byte de más para saber si excede el
            # tope, decodificando gzip si lo hubiera.
            with response:
                status_code = response.status_code
                body = response.raw.read(MAX_UNTRUSTED_XML_BYTES + 1, decode_content=True)
        except Exception as exc:  # noqa: BLE001 — cualquier fallo de red es incierto
            raise _TransportError(
                f'{type(exc).__name__} al contactar con el servicio') from exc
        if len(body) > MAX_UNTRUSTED_XML_BYTES:
            raise _TransportError('Respuesta del servicio demasiado grande')
        return body, status_code

    @staticmethod
    def _classify(code: str, *, from_cdr: bool) -> ProviderOutcome:
        """
        Traduce un código de SUNAT. `from_cdr` cambia lo que se puede concluir.

        Rangos, según el Manual del programador y la hoja de códigos de retorno:
        0100-1999 excepción reintentable · 2000-3999 rechazo · 4000+ observación.

        «ACEPTADA» EXIGE UN CDR. Un código 4000+ sólo significa «aceptada con
        observaciones» cuando viene DENTRO de una constancia de recepción: la
        constancia es la prueba de que SUNAT registró el comprobante. El mismo
        número llegando en un `faultstring`, sin constancia, no demuestra
        registro alguno — y afirmarlo pondría «ACEPTADA POR SUNAT» en una
        pantalla sobre un documento que quizá no existe para ellos.

        Sin CDR, un 4000+ es `UNKNOWN_RESPONSE`: hay que mirarlo, no celebrarlo.

        Un código que no encaje en ningún rango tampoco es rechazo: inventarse la
        semántica de un código desconocido convierte un error transitorio en un
        documento tirado a la basura.
        """
        if not code.isdigit():
            return ProviderOutcome.UNKNOWN_RESPONSE
        value = int(code)
        if 100 <= value <= 1999:
            return ProviderOutcome.TRANSPORT_ERROR
        if 2000 <= value <= 3999:
            return ProviderOutcome.REJECTED
        if value >= 4000:
            return (ProviderOutcome.ACCEPTED_WITH_OBSERVATION if from_cdr
                    else ProviderOutcome.UNKNOWN_RESPONSE)
        return ProviderOutcome.UNKNOWN_RESPONSE

    @staticmethod
    def _read_cdr(cdr_b64: str, **base) -> ProviderResult:
        """
        Interpreta el CDR: `ResponseCode` `0` es aceptado; con `cbc:Note`, con
        observaciones.

        Recibe el base64 crudo del `applicationResponse`/`content` y lo decodifica
        aquí, bajo la misma guarda que el ZIP y el XML: cualquier fallo (base64
        malformado, ZIP ilegible, XML rechazado) es UNKNOWN_RESPONSE, nunca una
        excepción que suba como 500.

        ES EL ÚNICO INTÉRPRETE DE CDR. Lo usan el envío (`sendBill`) y la consulta
        (`getStatusCdr`, `getStatus(ticket)`): un CDR significa lo mismo llegue por
        donde llegue (ERP-FISCAL-3 §23).
        """
        try:
            cdr_zip = base64.b64decode(cdr_b64)
            name, xml = extract_cdr(cdr_zip)
            doc = parse_untrusted(xml)
        except Exception as exc:  # noqa: BLE001 — CDR/ZIP externo no confiable
            return ProviderResult(
                outcome=ProviderOutcome.UNKNOWN_RESPONSE,
                safe_message=f'CDR ilegible: {type(exc).__name__}', **base,
            )
        code = next((e.text for e in doc.iter() if e.tag.endswith('ResponseCode')), '')
        description = next(
            (e.text for e in doc.iter() if e.tag.endswith('Description')), '',
        )
        notes = tuple(
            _sanitize(e.text, limit=200) for e in doc.iter()
            if e.tag.endswith('}Note') and e.text
        )

        if code == '0':
            # `ResponseCode 0` con observaciones sigue siendo válido
            # tributariamente, pero hay datos reparables: se distingue para que
            # la pantalla pueda decirlo en vez de callarlo.
            outcome = (ProviderOutcome.ACCEPTED_WITH_OBSERVATION if notes
                       else ProviderOutcome.ACCEPTED)
        else:
            outcome = _SunatSoapClient._classify(code or '', from_cdr=True)

        return ProviderResult(
            outcome=outcome, response_code=code or '',
            safe_message=_sanitize(description or ''), notes=notes,
            cdr_xml=xml, cdr_filename=name, **base,
        )


class FiscalProvider:
    """
    Lo que el dominio necesita para ENVIAR, y nada más.

    Implementarla no exige hablar SOAP: un PSE con API REST encajaría igual, que
    es justamente el motivo de que esta clase exista.
    """

    def submit_invoice(self, *, filename: str, zip_bytes: bytes) -> ProviderResult:
        raise NotImplementedError


class FiscalConsultProvider:
    """
    Lo que el dominio necesita para CONSULTAR el CDR de un comprobante emitido.

    Contrato SEPARADO del envío (ERP-FISCAL-3 §13): consultar no emite. Un mismo
    proveedor concreto puede implementar ambas, pero el dominio pide una u otra
    según lo que vaya a hacer, nunca «una llamada genérica a SUNAT».
    """

    def get_document_cdr(self, *, issuer_ruc: str, document_type: str,
                         series: str, number: int) -> DocumentCdrResult:
        raise NotImplementedError


class SunatSoapProvider(_SunatSoapClient, FiscalProvider):
    """
    SUNAT `billService` por SOAP, con WS-Security UsernameToken.

    Envía (`sendBill`) y consulta el estado de procesos asíncronos por ticket
    (`getStatus`). La consulta de CDR de un comprobante emitido NO vive aquí:
    ésa es `billConsultService`, otro servicio y otro contrato.
    """

    def _envelope(self, filename: str, zip_bytes: bytes) -> bytes:
        env, body = self._envelope_skeleton()
        call = etree.SubElement(body, f'{{{SERVICE_NS}}}sendBill')
        etree.SubElement(call, 'fileName').text = filename
        etree.SubElement(call, 'contentFile').text = base64.b64encode(zip_bytes).decode()
        return etree.tostring(env, xml_declaration=True, encoding='UTF-8')

    def submit_invoice(self, *, filename: str, zip_bytes: bytes) -> ProviderResult:
        envelope = self._envelope(filename, zip_bytes)
        # La huella se calcula sobre el ZIP, no sobre el sobre: el sobre lleva la
        # contraseña dentro y su hash no debe existir en ninguna parte.
        request_hash = _sha(zip_bytes)
        try:
            body, status = self._post(envelope, 'urn:sendBill')
        except _TransportError as exc:
            return ProviderResult(
                outcome=ProviderOutcome.TRANSPORT_ERROR,
                safe_message=str(exc), request_sha256=request_hash,
            )
        return self._interpret(body, status, request_hash)

    def _interpret(self, body: bytes, status: int, request_hash: str) -> ProviderResult:
        """
        Traduce la respuesta de `sendBill` a un resultado del dominio.

        Ante la duda NUNCA se devuelve `REJECTED`. Un rechazo obliga a corregir y
        volver a emitir; darlo por bueno sobre una respuesta que no lo demuestra
        produciría documentos duplicados por una venta que sí se registró.
        """
        response_hash = _sha(body)
        base = {'request_sha256': request_hash, 'response_sha256': response_hash}

        if status >= 500:
            return ProviderResult(
                outcome=ProviderOutcome.TRANSPORT_ERROR,
                safe_message=f'HTTP {status} del servicio', **base,
            )

        try:
            doc = parse_untrusted(body)
        except UntrustedXmlError:
            return ProviderResult(
                outcome=ProviderOutcome.UNKNOWN_RESPONSE,
                safe_message='La respuesta no es XML válido o fue rechazada', **base,
            )

        cdr_b64 = next(
            (e.text for e in doc.iter() if e.tag.endswith('applicationResponse')), None,
        )
        if cdr_b64:
            return self._read_cdr(cdr_b64, **base)

        fault = next((e.text for e in doc.iter() if e.tag.endswith('faultstring')), None)
        if fault is not None:
            code = next((e.text for e in doc.iter() if e.tag.endswith('faultcode')), '')
            # `soap-env:Client.3244` → el código numérico va tras el punto.
            numeric = (code or '').rsplit('.', 1)[-1]
            return ProviderResult(
                # Sin CDR: un fault no puede significar «aceptada».
                outcome=self._classify(numeric, from_cdr=False),
                response_code=numeric,
                safe_message=_sanitize(fault), **base,
            )

        return ProviderResult(
            outcome=ProviderOutcome.UNKNOWN_RESPONSE,
            safe_message='Respuesta sin CDR ni fault reconocible', **base,
        )

    # -- getStatus(ticket): fundación asíncrona para FISCAL-4 -----------------

    def get_ticket_status(self, *, ticket: str) -> TicketStatusResult:
        """
        Estado de un proceso asíncrono por ticket (Resumen Diario / Baja).

        Contrato reusable para FISCAL-4. Hoy no se emite ningún resumen, así que
        no hay tickets propios que consultar; existe y se prueba con mocks.
        """
        env, body = self._envelope_skeleton()
        call = etree.SubElement(body, f'{{{SERVICE_NS}}}getStatus')
        etree.SubElement(call, 'ticket').text = str(ticket)
        envelope = etree.tostring(env, xml_declaration=True, encoding='UTF-8')
        # El ticket no es secreto; sirve para correlacionar la consulta.
        request_hash = _sha(str(ticket).encode('utf-8'))
        try:
            resp, status = self._post(envelope, 'urn:getStatus')
        except _TransportError as exc:
            return TicketStatusResult(
                status=TicketStatus.TRANSPORT_ERROR,
                safe_message=str(exc), request_sha256=request_hash,
            )
        return self._interpret_ticket(resp, status, request_hash)

    def _interpret_ticket(self, body: bytes, status: int,
                          request_hash: str) -> TicketStatusResult:
        response_hash = _sha(body)
        base = {'request_sha256': request_hash, 'response_sha256': response_hash}
        if status >= 500:
            return TicketStatusResult(
                status=TicketStatus.TRANSPORT_ERROR,
                safe_message=f'HTTP {status} del servicio', **base)
        try:
            doc = parse_untrusted(body)
        except UntrustedXmlError:
            return TicketStatusResult(
                status=TicketStatus.UNKNOWN_RESPONSE,
                safe_message='La respuesta no es XML válido', **base)

        fault = next((e.text for e in doc.iter() if e.tag.endswith('faultstring')), None)
        if fault is not None:
            code = next((e.text for e in doc.iter() if e.tag.endswith('faultcode')), '')
            return TicketStatusResult(
                status=TicketStatus.TRANSPORT_ERROR,
                status_code=(code or '').rsplit('.', 1)[-1],
                safe_message=_sanitize(fault), **base)

        status_code = next(
            (e.text for e in doc.iter() if e.tag.endswith('statusCode')), '') or ''
        content = next(
            (e.text for e in doc.iter() if e.tag.endswith('content')), None)
        # Códigos oficiales: 0 procesó, 98 en proceso, 99 con errores. El CDR
        # llega en `content` con 0 (y con 99 para el resultado con errores).
        cdr = self._read_cdr(content, **base) if content else None
        if status_code == '0':
            estado = TicketStatus.COMPLETED
        elif status_code == '98':
            estado = TicketStatus.PROCESSING
        elif status_code == '99':
            estado = TicketStatus.ERROR
        else:
            estado = TicketStatus.UNKNOWN_RESPONSE
        return TicketStatusResult(
            status=estado, status_code=status_code, cdr=cdr,
            safe_message=_sanitize(next(
                (e.text for e in doc.iter()
                 if e.tag.endswith('statusMessage')), '') or ''),
            **base)


class SunatConsultProvider(_SunatSoapClient, FiscalConsultProvider):
    """
    SUNAT `billConsultService` por SOAP: `getStatusCdr`.

    Recupera el CDR de un comprobante YA emitido a partir de su identificador. Se
    usa para RECONCILIAR un envío cuyo resultado no conocemos con certeza (un
    timeout deja el estado incierto): consultar es un acto de lectura, no
    reenvía nada.
    """

    def get_document_cdr(self, *, issuer_ruc: str, document_type: str,
                         series: str, number: int) -> DocumentCdrResult:
        env, body = self._envelope_skeleton()
        call = etree.SubElement(body, f'{{{SERVICE_NS}}}getStatusCdr')
        etree.SubElement(call, 'rucComprobante').text = issuer_ruc
        etree.SubElement(call, 'tipoComprobante').text = document_type
        etree.SubElement(call, 'serieComprobante').text = series
        etree.SubElement(call, 'numeroComprobante').text = str(number)
        envelope = etree.tostring(env, xml_declaration=True, encoding='UTF-8')
        # La huella se calcula sobre el identificador (no secreto), NUNCA sobre el
        # sobre, que lleva la contraseña.
        request_hash = _sha(
            f'{issuer_ruc}-{document_type}-{series}-{number}'.encode('utf-8'))
        try:
            resp, status = self._post(envelope, 'urn:getStatusCdr')
        except _TransportError as exc:
            return DocumentCdrResult(
                outcome=ReconcileOutcome.TRANSPORT_ERROR,
                safe_message=str(exc), request_sha256=request_hash)
        return self._interpret_cdr(resp, status, request_hash)

    def _interpret_cdr(self, body: bytes, status: int,
                       request_hash: str) -> DocumentCdrResult:
        """
        Traduce `getStatusCdr` a un resultado de reconciliación.

        La verdad está en el CDR (`content`), no en el `statusCode`: el Manual del
        programador NO publica la tabla de códigos de `getStatusCdr`, así que NO
        se inventan sus significados. Si viene CDR, se lee con el mismo intérprete
        que `sendBill` (§23). Si no, el comprobante queda NO TERMINAL y se
        conserva el código crudo para mirarlo — jamás se concluye «no existe» ni
        se reenvía a ciegas (§24).
        """
        response_hash = _sha(body)
        base = {'request_sha256': request_hash, 'response_sha256': response_hash}
        if status >= 500:
            return DocumentCdrResult(
                outcome=ReconcileOutcome.TRANSPORT_ERROR,
                safe_message=f'HTTP {status} del servicio', **base)
        try:
            doc = parse_untrusted(body)
        except UntrustedXmlError:
            return DocumentCdrResult(
                outcome=ReconcileOutcome.UNKNOWN_RESPONSE,
                safe_message='La respuesta no es XML válido', **base)

        fault = next((e.text for e in doc.iter() if e.tag.endswith('faultstring')), None)
        if fault is not None:
            # Un fault en la consulta es SUNAT contestando, no un fallo de red;
            # pero sin tabla de códigos verificada no se concluye «no existe». Se
            # deja no terminal, con el código crudo, para que un humano lo mire.
            code = next((e.text for e in doc.iter() if e.tag.endswith('faultcode')), '')
            return DocumentCdrResult(
                outcome=ReconcileOutcome.UNKNOWN_RESPONSE,
                status_code=(code or '').rsplit('.', 1)[-1],
                safe_message=_sanitize(fault), **base)

        status_code = next(
            (e.text for e in doc.iter() if e.tag.endswith('statusCode')), '') or ''
        content = next(
            (e.text for e in doc.iter() if e.tag.endswith('content')), None)
        message = _sanitize(next(
            (e.text for e in doc.iter() if e.tag.endswith('statusMessage')), '') or '')

        if content:
            cdr = self._read_cdr(content, **base)
            return DocumentCdrResult(
                outcome=ReconcileOutcome.RESOLVED, status_code=status_code,
                safe_message=message, cdr=cdr, **base)

        return DocumentCdrResult(
            outcome=ReconcileOutcome.NOT_AVAILABLE, status_code=status_code,
            safe_message=message, **base)
