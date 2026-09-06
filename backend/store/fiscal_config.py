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

    certificate = getattr(settings, 'FISCAL_CERT_PEM', '') or ''
    key = getattr(settings, 'FISCAL_KEY_PEM', '') or ''
    if not (certificate and key):
        raise FiscalConfigError(
            'Falta el certificado de firma en la configuración del servidor.'
        )

    return {
        'ruc': ruc, 'sol_user': user, 'sol_password': password,
        'cert_pem': certificate.encode('utf-8') if isinstance(certificate, str)
        else certificate,
        'key_pem': key.encode('utf-8') if isinstance(key, str) else key,
    }


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
