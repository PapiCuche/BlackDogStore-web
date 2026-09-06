"""
La frontera con SUNAT. El dominio no conoce SOAP.

POR QUÉ UN ADAPTADOR Y NO LLAMADAS DIRECTAS
-------------------------------------------
Mañana esto puede ir por SUNAT directo, por un PSE o por un OSE. Si el dominio
supiera de sobres SOAP, cambiar de proveedor obligaría a tocar el dominio — y el
dominio es lo que no debe cambiar cuando cambia el transporte.

Hacia afuera se devuelve `ProviderResult`: un objeto plano con el resultado ya
interpretado. Nunca un `Response`, nunca un árbol XML de SUNAT. Quien lo recibe
no debería tener que saber qué es un `faultstring`.

UN ERROR DE TRANSPORTE NO ES UN RECHAZO
---------------------------------------
Es la distinción que gobierna este módulo. Un timeout, un 500 o una conexión
cortada dejan la venta en un estado INCIERTO: puede que SUNAT la haya recibido.
Tratarlos como rechazo llevaría a emitir un segundo documento por una venta que
quizá ya está registrada — y un correlativo gastado no se recicla.

Por eso `ProviderOutcome` separa `TRANSPORT_ERROR` de `REJECTED`, y sólo hay
rechazo cuando SUNAT lo dice con un CDR o con un código de su rango.

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


class ProviderOutcome(str, Enum):
    """
    Lo que pasó, ya interpretado.

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
    """El resultado de un envío, sin rastro de SOAP ni de secretos."""

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


class FiscalProvider:
    """
    Lo que el dominio necesita de un proveedor, y nada más.

    Implementarla no exige hablar SOAP: un PSE con API REST encajaría igual, que
    es justamente el motivo de que esta clase exista.
    """

    def submit_invoice(self, *, filename: str, zip_bytes: bytes) -> ProviderResult:
        raise NotImplementedError


class SunatSoapProvider(FiscalProvider):
    """
    SUNAT por SOAP, con WS-Security UsernameToken.

    El endpoint entra por el constructor y NO se acepta desde una petición: un
    inquilino que pudiera elegir la URL de SUNAT tendría un SSRF servido. Sólo
    lo fija la configuración del servidor.
    """

    def __init__(self, *, endpoint: str, ruc: str, sol_user: str, sol_password: str,
                 timeout: float = 60.0):
        if not endpoint.startswith('https://'):
            raise ValueError('El endpoint fiscal debe ser HTTPS.')
        self._endpoint = endpoint
        self._username = f'{ruc}{sol_user}'
        self._password = sol_password
        self._timeout = timeout

    def _envelope(self, filename: str, zip_bytes: bytes) -> bytes:
        """
        El sobre SOAP. La contraseña entra aquí y no sale de aquí.

        El sobre se construye con lxml en vez de con una plantilla de texto
        porque el nombre de archivo y el base64 son datos: interpolarlos en una
        cadena es cómo se inyecta XML.
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
        call = etree.SubElement(body, f'{{{SERVICE_NS}}}sendBill')
        etree.SubElement(call, 'fileName').text = filename
        etree.SubElement(call, 'contentFile').text = base64.b64encode(zip_bytes).decode()
        return etree.tostring(env, xml_declaration=True, encoding='UTF-8')

    def submit_invoice(self, *, filename: str, zip_bytes: bytes) -> ProviderResult:
        import requests

        envelope = self._envelope(filename, zip_bytes)
        # La huella se calcula sobre el ZIP, no sobre el sobre: el sobre lleva la
        # contraseña dentro y su hash no debe existir en ninguna parte.
        request_hash = _sha(zip_bytes)

        try:
            response = requests.post(
                self._endpoint, data=envelope,
                headers={'Content-Type': 'text/xml; charset=utf-8',
                         'SOAPAction': 'urn:sendBill'},
                timeout=self._timeout,
            )
        except Exception as exc:  # noqa: BLE001 — cualquier fallo de red es incierto
            return ProviderResult(
                outcome=ProviderOutcome.TRANSPORT_ERROR,
                safe_message=f'{type(exc).__name__} al contactar con el servicio',
                request_sha256=request_hash,
            )

        return self._interpret(response.content, response.status_code, request_hash)

    def _interpret(self, body: bytes, status: int, request_hash: str) -> ProviderResult:
        """
        Traduce la respuesta a un resultado del dominio.

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
            doc = etree.fromstring(body)
        except etree.XMLSyntaxError:
            return ProviderResult(
                outcome=ProviderOutcome.UNKNOWN_RESPONSE,
                safe_message='La respuesta no es XML', **base,
            )

        cdr_b64 = next(
            (e.text for e in doc.iter() if e.tag.endswith('applicationResponse')), None,
        )
        if cdr_b64:
            return self._read_cdr(base64.b64decode(cdr_b64), **base)

        fault = next((e.text for e in doc.iter() if e.tag.endswith('faultstring')), None)
        if fault is not None:
            code = next((e.text for e in doc.iter() if e.tag.endswith('faultcode')), '')
            # `soap-env:Client.3244` → el código numérico va tras el punto.
            numeric = (code or '').rsplit('.', 1)[-1]
            return ProviderResult(
                outcome=self._classify(numeric),
                response_code=numeric,
                safe_message=_sanitize(fault), **base,
            )

        return ProviderResult(
            outcome=ProviderOutcome.UNKNOWN_RESPONSE,
            safe_message='Respuesta sin CDR ni fault reconocible', **base,
        )

    @staticmethod
    def _classify(code: str) -> ProviderOutcome:
        """
        Los rangos, según el Manual del programador y la hoja de códigos de
        retorno: 0100-1999 excepción (reintentable), 2000-3999 rechazo,
        4000+ observación.

        Un código que no encaje en ningún rango es `UNKNOWN_RESPONSE`, no un
        rechazo: inventarse la semántica de un código desconocido es cómo un
        error transitorio se convierte en un documento tirado a la basura.
        """
        if not code.isdigit():
            return ProviderOutcome.UNKNOWN_RESPONSE
        value = int(code)
        if 100 <= value <= 1999:
            return ProviderOutcome.TRANSPORT_ERROR
        if 2000 <= value <= 3999:
            return ProviderOutcome.REJECTED
        if value >= 4000:
            return ProviderOutcome.ACCEPTED_WITH_OBSERVATION
        return ProviderOutcome.UNKNOWN_RESPONSE

    @staticmethod
    def _read_cdr(cdr_zip: bytes, **base) -> ProviderResult:
        """
        Interpreta el CDR: `ResponseCode` `0` es aceptado; con `cbc:Note`, con
        observaciones.
        """
        try:
            name, xml = extract_cdr(cdr_zip)
        except (ValueError, Exception) as exc:  # noqa: BLE001
            return ProviderResult(
                outcome=ProviderOutcome.UNKNOWN_RESPONSE,
                safe_message=f'CDR ilegible: {type(exc).__name__}', **base,
            )

        doc = etree.fromstring(xml)
        code = next((e.text for e in doc.iter() if e.tag.endswith('ResponseCode')), '')
        description = next(
            (e.text for e in doc.iter() if e.tag.endswith('Description')), '',
        )
        notes = tuple(
            _sanitize(e.text, limit=200) for e in doc.iter()
            if e.tag.endswith('}Note') and e.text
        )

        if code == '0':
            outcome = (ProviderOutcome.ACCEPTED_WITH_OBSERVATION if notes
                       else ProviderOutcome.ACCEPTED)
        else:
            outcome = SunatSoapProvider._classify(code or '')

        return ProviderResult(
            outcome=outcome, response_code=code or '',
            safe_message=_sanitize(description or ''), notes=notes,
            cdr_xml=xml, cdr_filename=name, **base,
        )
