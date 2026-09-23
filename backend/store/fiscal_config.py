"""
Quién decide el ambiente, la serie, las credenciales y el proveedor.

POR QUÉ ESTO ES UN MÓDULO Y NO CUATRO `if` REPARTIDOS
-----------------------------------------------------
Las cuatro decisiones son de SERVIDOR. Ninguna puede llegar en una petición: si
el navegador pudiera elegir el ambiente, «producción» estaría a un campo JSON de
distancia; si pudiera elegir el endpoint, tendríamos un SSRF servido.

Juntarlas aquí las hace auditables de una vez: un solo fichero responde «¿contra
qué SUNAT hablamos y con qué serie?».

LO QUE ESTABA MAL ANTES
-----------------------
La serie se elegía con `.filter(...).order_by('pk').first()`. Eso convierte «el
id más bajo» en política tributaria: bastaba con que la primera serie creada
fuese de producción para que una venta de pruebas la usara. Y no había ningún
filtro de ambiente.
"""

from __future__ import annotations

from django.conf import settings

from .models import FiscalDocumentType, FiscalEnvironment, FiscalSeries

#: El único ambiente que esta fase sabe operar. Producción exige un certificado
#: acreditado, credenciales reales por empresa y una revisión que no se ha hecho.
ONLY_SUPPORTED_ENVIRONMENT = FiscalEnvironment.BETA


class FiscalConfigError(Exception):
    """
    La emisión no puede proceder por configuración. Nunca lleva un secreto.

    Se distingue de `FiscalError` —que es una regla de negocio incumplida—
    porque la respuesta HTTP es distinta: aquí no hay nada que el usuario pueda
    corregir en su venta.
    """


def fiscal_enabled() -> bool:
    """
    ¿Está encendida la emisión electrónica en este servidor?

    Apagada por defecto. Una instalación que no ha configurado nada fiscal no
    debe poder mandar nada a SUNAT por el hecho de tener el código instalado.
    """
    return bool(getattr(settings, 'FISCAL_ENABLED', False))


def resolve_environment() -> str:
    """
    Contra qué SUNAT se habla. LO DECIDE EL SERVIDOR, punto.

    FALLA CERRADO ANTE PRODUCCIÓN. No porque el código no sepa formar la URL
    —la sabe—, sino porque habilitarla es una decisión que exige certificado
    acreditado y credenciales por empresa, y ninguna de las dos cosas existe.
    Que el fallo sea explícito evita que «funcione por accidente» el día que
    alguien copie una variable de entorno de un sitio a otro.
    """
    configured = getattr(settings, 'FISCAL_ENVIRONMENT', ONLY_SUPPORTED_ENVIRONMENT)
    if configured != ONLY_SUPPORTED_ENVIRONMENT:
        raise FiscalConfigError(
            f'El ambiente fiscal «{configured}» no está habilitado en esta '
            f'versión. Sólo se opera «{ONLY_SUPPORTED_ENVIRONMENT}».'
        )
    return configured


def resolve_series(company, *, branch=None,
                   document_type: str = FiscalDocumentType.INVOICE) -> FiscalSeries:
    """
    La serie que corresponde a esta venta. DETERMINISTA O NO HAY SERIE.

    Reglas, en orden:

    1. De la empresa de la venta. Nunca de otra.
    2. Del ambiente que decide el servidor. Nunca de producción en esta fase.
    3. Activa.
    4. Si la venta tiene sucursal y existe una serie de ESA sucursal, se usa esa.
       Si no, la serie de empresa (`branch IS NULL`).

    AMBIGÜEDAD = FALLO. Si tras aplicar todo eso quedan dos series igualmente
    válidas, se levanta en vez de elegir. Un desempate improvisado —«la más
    antigua», «la última»— se convierte en la política tributaria de la empresa
    sin que nadie la haya decidido, y sale a la luz cuando SUNAT recibe dos
    documentos de series distintas para el mismo mostrador.
    """
    environment = resolve_environment()

    candidates = FiscalSeries.objects.filter(
        company=company, document_type=document_type,
        environment=environment, is_active=True,
    )

    # La serie de la sucursal manda sobre la de empresa cuando existe: es la
    # única lectura que respeta que SUNAT admite series por establecimiento.
    if branch is not None:
        de_sucursal = list(candidates.filter(branch=branch))
        if len(de_sucursal) == 1:
            return de_sucursal[0]
        if len(de_sucursal) > 1:
            raise FiscalConfigError(
                f'La sucursal «{branch}» tiene {len(de_sucursal)} series de '
                f'factura activas y no hay regla para elegir entre ellas. '
                f'Desactive las que no correspondan.'
            )

    de_empresa = list(candidates.filter(branch__isnull=True))
    if len(de_empresa) == 1:
        return de_empresa[0]
    if len(de_empresa) > 1:
        raise FiscalConfigError(
            f'La empresa tiene {len(de_empresa)} series de factura activas sin '
            f'sucursal y no hay regla para elegir entre ellas. Desactive las que '
            f'no correspondan o asígnelas a una sucursal.'
        )

    raise FiscalConfigError(
        'No hay una serie de factura activa para esta empresa en el ambiente '
        f'«{environment}».'
    )


#: La letra con la que empieza la serie de una nota, según el comprobante que
#: modifica: una nota de factura lleva serie F; una de boleta, B. SUNAT no cambia
#: el prefijo por ser nota —lo hereda del original— (Anexo N.º 1/N.º 2).
_NOTE_SERIE_PREFIX = {FiscalDocumentType.INVOICE: 'F', FiscalDocumentType.RECEIPT: 'B'}


def resolve_note_series(company, *, branch=None, note_type: str,
                        original_type: str) -> FiscalSeries:
    """
    La serie de una nota (07/08) que corresponde al comprobante original.

    Igual de DETERMINISTA que `resolve_series`, y por las mismas razones, con una
    llave más: el PREFIJO. Un mismo `document_type` de nota (07) admite dos series
    legítimas —una F para notas de factura, una B para notas de boleta— y elegir
    entre ellas por «la primera» convertiría un accidente de orden en la política
    de la empresa. Se filtra por el prefijo que hereda del original y, si tras eso
    quedan dos, se levanta en vez de desempatar.
    """
    prefix = _NOTE_SERIE_PREFIX.get(original_type)
    if prefix is None:
        raise FiscalConfigError(
            f'El comprobante original tiene un tipo que no admite notas en esta '
            f'fase: {original_type!r}.'
        )
    environment = resolve_environment()
    candidates = FiscalSeries.objects.filter(
        company=company, document_type=note_type, environment=environment,
        is_active=True, series__startswith=prefix,
    )

    if branch is not None:
        de_sucursal = list(candidates.filter(branch=branch))
        if len(de_sucursal) == 1:
            return de_sucursal[0]
        if len(de_sucursal) > 1:
            raise FiscalConfigError(
                f'La sucursal «{branch}» tiene {len(de_sucursal)} series de nota '
                f'{note_type} con prefijo {prefix} activas y no hay regla para '
                f'elegir. Desactive las que no correspondan.'
            )

    de_empresa = list(candidates.filter(branch__isnull=True))
    if len(de_empresa) == 1:
        return de_empresa[0]
    if len(de_empresa) > 1:
        raise FiscalConfigError(
            f'La empresa tiene {len(de_empresa)} series de nota {note_type} con '
            f'prefijo {prefix} activas sin sucursal y no hay regla para elegir.'
        )

    raise FiscalConfigError(
        f'No hay una serie de nota {note_type} con prefijo {prefix} activa para '
        f'esta empresa en el ambiente «{environment}».'
    )


def resolve_credentials(company) -> dict:
    """
    Las credenciales de esta empresa. VIENEN DEL ENTORNO, no de la base de datos.

    Hoy son las mismas para todas porque el único ambiente es el de pruebas de
    SUNAT, que publica credenciales comunes. La firma recibe `company` de todas
    formas: el día que cada empresa tenga las suyas, cambia esta función y no
    cambia nadie más.

    NO SE GUARDAN EN NINGÚN MODELO. Una columna existe para llenarse, y un campo
    `sol_password` acaba apareciendo en un serializer, en un volcado o en una
    bitácora. Aquí no hay dónde.

    Devuelve las claves en crudo porque el proveedor las necesita; quien llame a
    esto no debe registrar el resultado.
    """
    if not fiscal_enabled():
        raise FiscalConfigError(
            'La emisión electrónica no está habilitada en este servidor.'
        )

    ruc = (getattr(settings, 'FISCAL_SOL_RUC', '') or '').strip()
    user = (getattr(settings, 'FISCAL_SOL_USER', '') or '').strip()
    password = getattr(settings, 'FISCAL_SOL_PASSWORD', '') or ''
    if not (ruc and user and password):
        raise FiscalConfigError(
            'Faltan credenciales SOL en la configuración del servidor.'
        )

    cert_pem, key_pem = _resolve_signing_material()

    return {
        'ruc': ruc, 'sol_user': user, 'sol_password': password,
        'cert_pem': cert_pem,
        'key_pem': key_pem,
    }


def _resolve_signing_material() -> tuple[bytes, bytes]:
    """
    El material de firma: PKCS#12 O el par PEM heredado, NUNCA ambos.

    Precedencia documentada y FAIL-CLOSED: si están configurados a la vez el
    contenedor P12 y el par PEM, se rechaza en vez de adivinar cuál gana —
    configuración ambigua es un error de servidor, no un desempate silencioso.
    El P12 se carga y convierte a PEM en memoria; el PEM heredado sigue
    funcionando igual para tests, BETA, fixtures y CI.
    """
    p12_path = (getattr(settings, 'FISCAL_CERT_P12_PATH', '') or '').strip()
    pem_cert = getattr(settings, 'FISCAL_CERT_PEM', '') or ''
    pem_key = getattr(settings, 'FISCAL_KEY_PEM', '') or ''
    has_p12 = bool(p12_path)
    has_pem = bool(pem_cert and pem_key)

    if has_p12 and has_pem:
        raise FiscalConfigError(
            'Configuración de certificado ambigua: hay PKCS#12 y PEM a la vez. '
            'Configure sólo uno.'
        )

    if has_p12:
        from .fiscal.certificate import CertificateError, signing_material_from_pkcs12

        try:
            with open(p12_path, 'rb') as handle:
                data = handle.read()
        except OSError as exc:
            # El path o el tipo de error se puede decir; el contenido no existe
            # aún, así que no hay secreto que filtrar.
            raise FiscalConfigError(
                f'No se pudo leer el certificado PKCS#12 ({type(exc).__name__}).'
            ) from None

        p12_password = getattr(settings, 'FISCAL_CERT_P12_PASSWORD', '') or ''
        try:
            material = signing_material_from_pkcs12(
                data, p12_password.encode('utf-8') if p12_password else None,
            )
        except CertificateError as exc:
            raise FiscalConfigError(str(exc)) from None
        return material.cert_pem, material.key_pem

    if has_pem:
        return (
            pem_cert.encode('utf-8') if isinstance(pem_cert, str) else pem_cert,
            pem_key.encode('utf-8') if isinstance(pem_key, str) else pem_key,
        )

    raise FiscalConfigError(
        'Falta el certificado de firma en la configuración del servidor.'
    )


def inspect_signing_certificate():
    """
    Metadatos NO secretos del certificado configurado, para diagnóstico local.

    Devuelve (CertificateMetadata, validity) donde validity ∈ {valid,
    expired, not_yet_valid}. NO expone la clave privada ni la contraseña. Sólo
    aplica al contenedor PKCS#12 configurado (el PEM heredado se inspecciona con
    herramientas estándar). Levanta FiscalConfigError si no hay P12 configurado
    o no se puede abrir.
    """
    from .fiscal.certificate import CertificateError, inspect_pkcs12

    p12_path = (getattr(settings, 'FISCAL_CERT_P12_PATH', '') or '').strip()
    if not p12_path:
        raise FiscalConfigError('No hay un certificado PKCS#12 configurado.')
    try:
        with open(p12_path, 'rb') as handle:
            data = handle.read()
    except OSError as exc:
        raise FiscalConfigError(
            f'No se pudo leer el certificado PKCS#12 ({type(exc).__name__}).'
        ) from None

    p12_password = getattr(settings, 'FISCAL_CERT_P12_PASSWORD', '') or ''
    try:
        meta = inspect_pkcs12(
            data, p12_password.encode('utf-8') if p12_password else None,
        )
    except CertificateError as exc:
        raise FiscalConfigError(str(exc)) from None
    return meta, meta.validity()


def resolve_provider(company):
    """
    El proveedor con el que hablar. El endpoint NO es configurable por nadie.

    Se elige de una tabla del código a partir del ambiente que decidió el
    servidor. Ni el navegador ni la empresa pueden escribir una URL: eso sería
    entregar una petición saliente arbitraria desde nuestro servidor.
    """
    from .fiscal.provider import BETA_ENDPOINT, SunatSoapProvider

    environment = resolve_environment()
    endpoints = {FiscalEnvironment.BETA: BETA_ENDPOINT}
    endpoint = endpoints.get(environment)
    if endpoint is None:
        raise FiscalConfigError(
            f'No hay endpoint declarado para el ambiente «{environment}».'
        )

    credentials = resolve_credentials(company)
    return SunatSoapProvider(
        endpoint=endpoint, ruc=credentials['ruc'],
        sol_user=credentials['sol_user'],
        sol_password=credentials['sol_password'],
    )


def fiscal_consult_enabled() -> bool:
    """
    ¿Está habilitada la CONSULTA/RECONCILIACIÓN real contra SUNAT?

    CAPACIDAD SEPARADA de la emisión (ERP-FISCAL-3 §16). `billConsultService`
    —donde vive `getStatusCdr`— sólo existe en producción según el Manual del
    programador: consultarlo de verdad es tocar producción, y ésa es una decisión
    distinta de la de emitir. Apagada por defecto: en esta versión la
    reconciliación se implementa y se prueba con un proveedor inyectado (mock),
    pero NO sale a la red.

    Encenderla NO habilita `sendBill` producción: son banderas y resolutores
    distintos. `resolve_environment()`/`resolve_provider()` siguen fijados en BETA
    pase lo que pase con ésta. Emitir en producción y consultar en producción son
    capacidades separadas, y ninguna se enciende de rebote por la otra.
    """
    return bool(getattr(settings, 'FISCAL_CONSULT_ENABLED', False))


def resolve_consult_provider(company):
    """
    El proveedor de CONSULTA (`getStatusCdr`). Endpoint fijo del código, jamás de
    una petición: un inquilino que eligiera la URL tendría un SSRF servido.

    FALLA CERRADO si la consulta real no está habilitada (§16/§53). En
    ERP-FISCAL-3 lo está por defecto: la reconciliación se prueba con un proveedor
    inyectado, no con éste. Enciende esta capacidad una fase de producción, con su
    revisión propia — no un descuido de configuración.
    """
    from .fiscal.provider import PRODUCTION_CONSULT_ENDPOINT, SunatConsultProvider

    if not fiscal_consult_enabled():
        raise FiscalConfigError(
            'La consulta/reconciliación en línea con SUNAT no está habilitada en '
            'esta versión. Requiere revisión de producción.'
        )
    # El endpoint por defecto es el de producción documentado; se permite
    # sustituirlo por configuración de servidor (nunca por petición) para pruebas
    # de integración controladas.
    endpoint = getattr(settings, 'FISCAL_CONSULT_ENDPOINT', PRODUCTION_CONSULT_ENDPOINT)
    credentials = resolve_credentials(company)
    return SunatConsultProvider(
        endpoint=endpoint, ruc=credentials['ruc'],
        sol_user=credentials['sol_user'],
        sol_password=credentials['sol_password'],
    )
