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

#: El ambiente por defecto, y el único que se opera sin que nadie lo decida.
DEFAULT_ENVIRONMENT = FiscalEnvironment.BETA

#: Lo que dice una instalación que no permite producción, a quien intenta elegirla.
#: Nombra el GO y al operador: no hay nada que un MASTER pueda corregir desde la consola.
PRODUCTION_SWITCH_OFF = (
    'La emisión en PRODUCCIÓN no está habilitada en esta instalación. Hace falta, en este orden: '
    'el GO de despliegue de la versión, el GO escrito «GO activación SUNAT producción <empresa>» y que '
    'el operador encienda FISCAL_PRODUCTION_ENABLED en el servidor. Mientras tanto se opera BETA.'
)


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
    active = _console_row()
    if active is not None:
        return active.enabled
    return bool(getattr(settings, 'FISCAL_ENABLED', False))


#: El id de esta integración en el registro de proveedores de la consola.
CONSOLE_PROVIDER = 'sunat'


def _console_row():
    """
    Lo que un MASTER activó en Configuración › Integraciones › SUNAT, o None.

    CUANDO EXISTE, MANDA: sus credenciales y su certificado sustituyen a las
    variables `FISCAL_*`, y apagarla ahí apaga la emisión aunque el entorno diga
    `FISCAL_ENABLED=True`. Activarla es un acto explícito y confirmado; guardar
    un borrador o probarlo no enciende nada.
    """
    from .integrations import service

    return service.row(CONSOLE_PROVIDER)


def _console_secrets(active) -> dict:
    from .integrations import secret_store, service

    try:
        return service.open_secrets(active)
    except secret_store.SecretStoreError:
        raise FiscalConfigError(
            'No se pudo abrir la configuración fiscal guardada en la consola.'
        ) from None


def _console_credentials(active) -> dict:
    """Las credenciales y el material de firma de la consola, en memoria."""
    return _credentials_from(active.public, _console_secrets(active))


def _credentials_from(public: dict, secrets: dict) -> dict:
    """Las credenciales de una configuración de la consola (activa o borrador), en memoria."""
    from .fiscal.certificate import CertificateError, signing_material_from_pkcs12
    from .integrations.providers import sunat

    ruc = str(public.get('ruc') or '').strip()
    user = str(public.get('sol_user') or '').strip()
    password = secrets.get('sol_password') or ''
    if not (ruc and user and password):
        raise FiscalConfigError('Faltan credenciales SOL en la configuración del servidor.')
    if not secrets.get('certificate_p12'):
        raise FiscalConfigError('Falta el certificado de firma en la configuración del servidor.')
    try:
        material = signing_material_from_pkcs12(
            sunat.certificate_bytes(secrets), sunat.certificate_password(secrets),
        )
    except CertificateError as exc:
        raise FiscalConfigError(str(exc)) from None
    return {
        'ruc': ruc, 'sol_user': user, 'sol_password': password,
        'cert_pem': material.cert_pem, 'key_pem': material.key_pem,
    }


#: Lo que se dice cuando la consola SUNAT está en pausa y alguien pide un comprobante NUEVO.
PAUSED = (
    'La emisión de comprobantes nuevos está en PAUSA (Integraciones › SUNAT). Lo ya emitido se sigue '
    'enviando, consultando y conciliando, y los resúmenes y bajas siguen su curso. Reanúdala desde la consola.'
)


def new_documents_paused() -> bool:
    """
    ¿Está la consola SUNAT en PAUSA? Pausada no nace ningún comprobante nuevo (factura,
    boleta o nota), y TODO lo demás sigue: firmar lo ya creado, enviar, consultar,
    conciliar, el Resumen Diario y las bajas. Es la forma normal de detener producción:
    desactivar corta también los envíos pendientes, y un pendiente tiene plazo legal.
    """
    active = _console_row()
    return bool(active is not None and active.enabled and active.paused)


def refuse_new_document_while_paused() -> None:
    """Levanta `FiscalConfigError(PAUSED)` si la consola está en pausa."""
    if new_documents_paused():
        raise FiscalConfigError(PAUSED)


def production_enabled() -> bool:
    """
    ¿Permite ESTA INSTALACIÓN emitir en producción? `FISCAL_PRODUCTION_ENABLED`.

    Apagado por defecto. Es un interruptor del operador, en `deploy/.env.production`,
    que se enciende con su propio GO (docs/sunat-estado-2026-11.md, §6): ni la consola
    ni una petición lo cambian. Apagado, producción no existe aunque un MASTER la
    haya elegido: se falla cerrado, nunca se vuelve a BETA en silencio.
    """
    return bool(getattr(settings, 'FISCAL_PRODUCTION_ENABLED', False))


def console_environment(public: dict | None) -> str:
    """
    El ambiente que elige una configuración de la consola: `production` sólo si lo
    dice su campo `environment`. Una fila sin el campo —todas las anteriores a C3—
    es BETA.
    """
    word = str((public or {}).get('environment') or '').strip().lower()
    return FiscalEnvironment.PRODUCTION if word == FiscalEnvironment.PRODUCTION else FiscalEnvironment.BETA


def intended_environment() -> str:
    """
    El ambiente que se QUIERE operar, sin comprobar si se puede.

    Producción sólo se pide desde la consola (Integraciones › SUNAT, modo
    producción). `FISCAL_ENVIRONMENT` del entorno sigue admitiendo sólo `beta`: una
    variable copiada de otro servidor no puede abrir producción.
    """
    active = _console_row()
    if active is not None and console_environment(active.public) == FiscalEnvironment.PRODUCTION:
        return FiscalEnvironment.PRODUCTION
    configured = getattr(settings, 'FISCAL_ENVIRONMENT', DEFAULT_ENVIRONMENT)
    if configured != DEFAULT_ENVIRONMENT:
        raise FiscalConfigError(
            f'El ambiente fiscal «{configured}» no se elige por variable de entorno. '
            f'FISCAL_ENVIRONMENT sólo admite «{DEFAULT_ENVIRONMENT}»; producción se elige '
            f'en Integraciones › SUNAT y sólo con FISCAL_PRODUCTION_ENABLED encendido.'
        )
    return configured


def resolve_environment(company=None) -> str:
    """
    Contra qué SUNAT habla `company`. LO DECIDE EL SERVIDOR, punto.

    BETA, salvo que se haya pedido producción. Producción se devuelve SÓLO si:

    1. un MASTER activó la consola en modo producción —activarla exige el texto
       de confirmación y `fiscal_preflight --target production` en GO
       (`production_activation_problems`)—;
    2. la instalación lo permite: `FISCAL_PRODUCTION_ENABLED`;
    3. `company` es el emisor de esas credenciales: su RUC es el de la Clave SOL;
    4. la consulta de CDR (`FISCAL_CONSULT_ENABLED`) está encendida: en producción
       es la única salida ante un envío incierto, y se enciende JUNTO con la emisión.

    FALLA CERRADO. Si se pidió producción y falta cualquiera de las tres, levanta:
    NUNCA devuelve BETA en su lugar. Un comprobante que alguien quiso real y salió
    de pruebas es una venta sin comprobante; uno que salió real sin quererlo gasta
    numeración real. Ninguno de los dos se arregla después.

    Lo que el preflight comprueba de la cola y de los plazos NO se vuelve a exigir
    en cada emisión: una factura atrasada no puede impedir que se envíe a sí misma.
    El certificado (vigente, con el RUC, no autofirmado) lo exige `issuer_problems`
    en cada firma de producción.
    """
    intended = intended_environment()
    if intended != FiscalEnvironment.PRODUCTION:
        return intended
    if not production_enabled():
        raise FiscalConfigError(PRODUCTION_SWITCH_OFF)
    if company is None:
        raise FiscalConfigError(
            'El servidor opera SUNAT PRODUCCIÓN y no se indicó la empresa emisora: no se emite.')
    ruc = (getattr(company, 'tax_id', '') or '').strip()
    console_ruc = str(_console_row().public.get('ruc') or '').strip()
    issuer, _problem = production_issuer(console_ruc)
    # The RUC AND the company: a second company that copied the RUC is not the issuer, and
    # two active companies with it leave no issuer at all (review of #468).
    if not ruc or ruc != console_ruc or issuer is None or issuer.pk != getattr(company, 'pk', None):
        raise FiscalConfigError(
            'SUNAT PRODUCCIÓN está activa para otro contribuyente: esta empresa no emite con esas '
            'credenciales, ni en producción ni en BETA.'
        )
    if not fiscal_consult_enabled():
        raise FiscalConfigError(
            'SUNAT PRODUCCIÓN exige la consulta de CDR encendida (FISCAL_CONSULT_ENABLED): sin ella un envío '
            'incierto no tiene salida. No se emite hasta que el operador la encienda.'
        )
    return FiscalEnvironment.PRODUCTION


def production_issued() -> bool:
    """Whether anything was ever issued against SUNAT PRODUCTION from this installation."""
    from .models import FiscalDailySummary, FiscalDocument, FiscalVoidCommunication

    return any(model.objects.filter(environment=FiscalEnvironment.PRODUCTION).exists()
               for model in (FiscalDocument, FiscalDailySummary, FiscalVoidCommunication))


def tax_id_change_problem(company, new_tax_id) -> str:
    """
    Why `company` (None when it is being created) may NOT take `new_tax_id` now; '' if it may.

    The production issuer is the one active company with the RUC of the SOL credentials.
    While production is chosen, or once anything was issued there, no company may take
    that RUC or give it up: the first would make a second issuer (or none), the second
    would leave the issued documents without the company that has to send, consult or
    void them. Changing any other RUC is not this function's business.
    """
    old = (getattr(company, 'tax_id', '') or '').strip() if company is not None else ''
    new = (new_tax_id or '').strip()
    if old == new:
        return ''
    active = _console_row()
    console_ruc = str(active.public.get('ruc') or '').strip() if active is not None else ''
    if not console_ruc or console_ruc not in (old, new):
        return ''
    if console_environment(active.public) != FiscalEnvironment.PRODUCTION and not production_issued():
        return ''
    return ('El RUC de las credenciales SUNAT de producción no se asigna ni se quita mientras producción esté '
            'elegida o existan comprobantes de producción: cambiaría quién emite y quién responde por lo emitido.')


def production_issuer(ruc: str):
    """
    La empresa que emite con la Clave SOL de `ruc`, o `(None, motivo)`.

    Las credenciales de la consola son de la instalación (SCOPE_PLATFORM) y llevan un
    RUC: el emisor es la ÚNICA empresa activa con ese RUC. Dos con el mismo RUC no
    se desempatan.
    """
    from .models import Company

    ruc = (ruc or '').strip()
    if not ruc:
        return None, 'La configuración no tiene RUC: no hay emisor para producción.'
    companies = list(Company.objects.filter(tax_id=ruc, is_active=True).order_by('pk')[:2])
    if not companies:
        return None, 'Ninguna empresa activa tiene el RUC de estas credenciales: no hay emisor para producción.'
    if len(companies) > 1:
        return None, ('Hay más de una empresa activa con el RUC de estas credenciales: no se decide cuál '
                      'emite en producción.')
    return companies[0], ''


def production_activation_problems(public: dict, secrets: dict | None = None, *,
                                   staying: bool = False) -> list[str]:
    """
    Por qué esta configuración de la consola NO puede empezar a emitir en producción;
    `[]` si puede. Lo pregunta la consola al probar y al activar.

    1. `FISCAL_PRODUCTION_ENABLED` encendido en la instalación.
    2. Una sola empresa activa con el RUC de la Clave SOL.
    3. `fiscal_preflight --target production` en GO para esa empresa, evaluado con
       ESTAS credenciales y ESTE certificado (los del borrador que se va a activar;
       sin `secrets`, los de la fila activa).

    `staying`: la consola YA está en modo producción —activa, en pausa o desactivada—
    y se cambian sus claves (renovar el certificado) o se vuelve a encender. Entonces
    la cola y los plazos no bloquean: lo pendiente es justamente lo que necesita la
    emisión encendida para salir, y negarlo dejaría la instalación sin salida.

    Los motivos no llevan secretos ni el RUC de otra empresa: son líneas del preflight.
    """
    from . import fiscal_preflight

    if not production_enabled():
        return [PRODUCTION_SWITCH_OFF]
    company, problem = production_issuer(str((public or {}).get('ruc') or ''))
    if company is None:
        return [problem]
    credentials = None
    if secrets:
        try:
            credentials = _credentials_from(public, secrets)
        except FiscalConfigError as exc:
            return [str(exc)]
    checks = fiscal_preflight.run(company, target=FiscalEnvironment.PRODUCTION, credentials=credentials)
    skipped = {'cola', 'plazos'} if staying else set()
    stops = [check for check in checks if check.level == fiscal_preflight.NO_GO and check.area not in skipped]
    return [f'fiscal_preflight --company {company.slug} --target production está en NO-GO · '
            f'{check.area}: {check.detail}' for check in stops]


#: Cómo se llama cada tipo de comprobante cuando hay que decírselo a una
#: persona. Los mensajes de esta función los lee quien atiende un mostrador, no
#: un programador: decirle «no hay serie de factura» cuando lo que falta es la
#: serie de boleta lo manda a revisar la configuración equivocada.
#:
#: Se consulta con `.get(...)` y un nombre genérico de reserva. Indexar
#: directamente convertiría un tipo inesperado —un error de configuración, que
#: es lo que esta función informa— en un `KeyError` que oculta el diagnóstico
#: real detrás de otro fallo.
_DOCUMENT_NOUN = {
    FiscalDocumentType.INVOICE: 'factura',
    FiscalDocumentType.RECEIPT: 'boleta',
    FiscalDocumentType.CREDIT_NOTE: 'nota de crédito',
    FiscalDocumentType.DEBIT_NOTE: 'nota de débito',
}


def resolve_series(company, *, branch=None,
                   document_type: str = FiscalDocumentType.INVOICE) -> FiscalSeries:
    """
    La serie que corresponde a esta venta. DETERMINISTA O NO HAY SERIE.

    Reglas, en orden:

    1. De la empresa de la venta. Nunca de otra.
    2. Del ambiente que decide el servidor (`resolve_environment(company)`). Las
       series de BETA y las de producción son filas distintas y nunca se mezclan.
    3. Activa.
    4. Si la venta tiene sucursal y existe una serie de ESA sucursal, se usa esa.
       Si no, la serie de empresa (`branch IS NULL`).

    AMBIGÜEDAD = FALLO. Si tras aplicar todo eso quedan dos series igualmente
    válidas, se levanta en vez de elegir. Un desempate improvisado —«la más
    antigua», «la última»— se convierte en la política tributaria de la empresa
    sin que nadie la haya decidido, y sale a la luz cuando SUNAT recibe dos
    documentos de series distintas para el mismo mostrador.
    """
    environment = resolve_environment(company)
    noun = _DOCUMENT_NOUN.get(document_type, 'comprobante')

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
                f'{noun} activas y no hay regla para elegir entre ellas. '
                f'Desactive las que no correspondan.'
            )

    de_empresa = list(candidates.filter(branch__isnull=True))
    if len(de_empresa) == 1:
        return de_empresa[0]
    if len(de_empresa) > 1:
        raise FiscalConfigError(
            f'La empresa tiene {len(de_empresa)} series de {noun} activas sin '
            f'sucursal y no hay regla para elegir entre ellas. Desactive las que '
            f'no correspondan o asígnelas a una sucursal.'
        )

    raise FiscalConfigError(
        f'No hay una serie de {noun} activa para esta empresa en el ambiente '
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
    environment = resolve_environment(company)
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


def resolve_credentials(company, *, signing: bool, issuer_tax_id: str | None = None) -> dict:
    """
    Las credenciales de esta empresa: las de la consola, o las del entorno.

    `signing` dice si quien llama va a FIRMAR (emitir un comprobante, una nota, un
    resumen o una baja; la caja antes de vender). Sólo entonces se exige que el
    certificado esté vigente: enviar, consultar o reconciliar un documento YA
    firmado no usa el certificado, y un certificado que venció después no puede
    dejar sin salida a lo que se firmó mientras estaba vigente.

    `issuer_tax_id` es el RUC contra el que se comparan las credenciales cuando se
    actúa sobre un documento existente (su `issuer_tax_id`); por defecto, el RUC
    de la empresa.

    Si un MASTER activó SUNAT en Configuración › Integraciones, son ésas: cifradas
    en el almacén de secretos, nunca en una columna legible. Si no, las variables
    `FISCAL_*` del servidor, como siempre.

    Son de la instalación (una Clave SOL, un RUC). Por eso `issuer_problems` exige
    que su RUC sea el de la empresa: con dos empresas, sólo la del RUC de la Clave
    SOL emite, en BETA y en producción. La firma recibe `company`: el día que cada
    empresa tenga las suyas, cambia esta función y no cambia nadie más.

    NO HAY COLUMNA `sol_password`. Una columna existe para llenarse, y un campo
    así acaba apareciendo en un serializer, en un volcado o en una bitácora. Lo
    que guarda la consola es un bloque cifrado con una clave que no está en la
    base de datos, y ninguna API lo devuelve.

    Devuelve las claves en crudo porque el proveedor las necesita; quien llame a
    esto no debe registrar el resultado.
    """
    active = _console_row()
    enabled = active.enabled if active is not None else bool(getattr(settings, 'FISCAL_ENABLED', False))
    if not enabled:
        raise FiscalConfigError(
            'La emisión electrónica no está habilitada en este servidor.'
        )
    if active is not None:
        return _checked(_console_credentials(active), company, signing=signing, issuer_tax_id=issuer_tax_id)

    ruc = (getattr(settings, 'FISCAL_SOL_RUC', '') or '').strip()
    user = (getattr(settings, 'FISCAL_SOL_USER', '') or '').strip()
    password = getattr(settings, 'FISCAL_SOL_PASSWORD', '') or ''
    if not (ruc and user and password):
        raise FiscalConfigError(
            'Faltan credenciales SOL en la configuración del servidor.'
        )

    cert_pem, key_pem = _resolve_signing_material()

    return _checked({
        'ruc': ruc, 'sol_user': user, 'sol_password': password,
        'cert_pem': cert_pem,
        'key_pem': key_pem,
    }, company, signing=signing, issuer_tax_id=issuer_tax_id)


def _checked(credentials: dict, company, *, signing: bool, issuer_tax_id: str | None = None) -> dict:
    """
    El par de firma se lee y casa, y es de ESTA empresa, o no hay emisión.

    Quien pide credenciales lo hace ANTES de reservar un correlativo (H-01): un
    PEM roto o un certificado de otra clave tiene que fallar aquí, no al firmar
    con el número ya gastado (revisión de #176). Lo mismo vale para credenciales
    de otro contribuyente y para un certificado fuera de vigencia (SUNAT-ISSUER-GATE).
    """
    from .fiscal.certificate import CertificateError, check_signing_pair

    try:
        check_signing_pair(credentials['cert_pem'], credentials['key_pem'])
    except CertificateError as exc:
        raise FiscalConfigError(str(exc)) from None
    if company is None:
        # Sin empresa no hay emisor contra el que comparar: no hay credenciales.
        raise FiscalConfigError('No hay empresa emisora: no se resuelven credenciales fiscales.')
    problems = issuer_problems(company, credentials, environment=resolve_environment(company),
                               signing=signing, issuer_tax_id=issuer_tax_id)
    if problems:
        raise FiscalConfigError(problems[0])
    return credentials


#: Un RUC dentro de un valor del sujeto: once dígitos que no son parte de un número más largo.
_RUC_IN_TEXT = r'(?<!\d)\d{11}(?!\d)'


def issuer_problems(company, credentials: dict, *, environment: str, signing: bool = True,
                    issuer_tax_id: str | None = None, now=None) -> list[str]:
    """
    Por qué estas credenciales NO pueden emitir por `company`; `[]` si pueden.

    UNA EMPRESA NUNCA EMITE CON LAS CREDENCIALES NI EL CERTIFICADO DE OTRA
    (docs/sunat-estado-2026-11.md, G3). Hasta hoy las credenciales son de la
    instalación y nada comprobaba que su RUC fuese el de la empresa que vende: con
    dos empresas, la segunda firmaba y enviaba a nombre de la primera.

    1. La empresa tiene RUC, y es el RUC de la Clave SOL. El usuario SOAP es
       `RUC + usuario` (Anexo 6, 6.1.2) y el archivo lleva el RUC del emisor: los
       dos tienen que ser el mismo contribuyente, también en BETA (`RUCMODDATOS`).
    2. Sólo al FIRMAR (`signing`): el certificado está vigente. Uno vencido o aún
       no válido se descubría al final, con el correlativo gastado y el rechazo
       de SUNAT. Enviar o consultar lo ya firmado no lo exige.
    3. Sólo al firmar en PRODUCCIÓN: el RUC del emisor figura en el sujeto del
       certificado (Manual del programador, §3.1, lo pide en el campo OU; se
       acepta en cualquier atributo del sujeto). En BETA no: SUNAT no exige allí
       un certificado registrado (§2.3) y se firma con uno autofirmado.
    4. Sólo al firmar en PRODUCCIÓN: el certificado NO es autofirmado (C3). SUNAT
       exige uno emitido por una entidad acreditada a nombre del RUC (§3.1); uno
       autofirmado sólo sirve en BETA, y en producción se descubriría con el
       rechazo y el correlativo gastado.

    `issuer_tax_id`: el RUC del documento sobre el que se actúa, si existe; si
    no, el de la empresa.

    Los mensajes no llevan secretos ni el RUC de las credenciales: quien opera
    una empresa no tiene por qué leer el RUC de otra.
    """
    from .fiscal.certificate import (
        CertificateError, certificate_metadata_from_pem, is_self_signed_pem, subject_values,
    )

    import re

    from django.utils import timezone as dj_timezone

    ruc = (issuer_tax_id or getattr(company, 'tax_id', '') or '').strip()
    if not ruc:
        return ['La empresa no tiene RUC registrado: no se puede emitir a su nombre.']
    problems = []
    if (credentials.get('ruc') or '').strip() != ruc:
        problems.append(
            'Las credenciales SOL configuradas no son del RUC de esta empresa. Una '
            'empresa no emite con las credenciales de otro contribuyente.')
    if not signing:
        return problems
    try:
        meta = certificate_metadata_from_pem(credentials['cert_pem'])
    except CertificateError as exc:
        return [*problems, str(exc)]
    validity = meta.validity(now=now)
    # Las fechas, en la hora del servidor (Lima), que es la que lee quien opera.
    if validity == 'expired':
        problems.append(
            f'El certificado de firma venció el {dj_timezone.localtime(meta.not_after):%d/%m/%Y}.')
    elif validity == 'not_yet_valid':
        problems.append(
            f'El certificado de firma todavía no es válido: empieza el '
            f'{dj_timezone.localtime(meta.not_before):%d/%m/%Y}.')
    if environment == FiscalEnvironment.PRODUCTION:
        tokens = {token for value in subject_values(credentials['cert_pem'])
                  for token in re.findall(_RUC_IN_TEXT, value)}
        if ruc not in tokens:
            problems.append(
                'El RUC de esta empresa no figura en el sujeto del certificado de firma '
                '(SUNAT lo pide en el campo OU).')
        if is_self_signed_pem(credentials['cert_pem']):
            problems.append(
                'El certificado de firma es autofirmado: sirve para BETA, no para producción '
                '(SUNAT exige uno emitido a nombre del RUC y registrado en SOL).')
    return problems


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

    active = _console_row()
    if active is not None:
        from .integrations.providers import sunat

        secrets = _console_secrets(active)
        if not secrets.get('certificate_p12'):
            raise FiscalConfigError('No hay un certificado PKCS#12 configurado.')
        try:
            meta = inspect_pkcs12(sunat.certificate_bytes(secrets), sunat.certificate_password(secrets))
        except CertificateError as exc:
            raise FiscalConfigError(str(exc)) from None
        return meta, meta.validity()

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


def resolve_provider(company, *, issuer_tax_id: str | None = None, environment: str | None = None):
    """
    El proveedor con el que hablar. El endpoint NO es configurable por nadie.

    Se elige de una tabla del código a partir del ambiente que decidió el
    servidor. Ni el navegador ni la empresa pueden escribir una URL: eso sería
    entregar una petición saliente arbitraria desde nuestro servidor.

    `environment` es el ambiente del documento que se va a enviar (o cuyo ticket se
    consulta). Si no es el que opera el servidor, SE NIEGA: un comprobante BETA no
    sale nunca hacia producción, ni uno de producción hacia BETA. El nombre de un
    ambiente no es una URL: la URL sigue saliendo de la tabla.
    """
    from .fiscal.provider import BETA_ENDPOINT, PRODUCTION_ENDPOINT, SunatSoapProvider

    current = resolve_environment(company)
    if environment is not None and environment != current:
        raise FiscalConfigError(
            f'Este documento es del ambiente «{environment}» y el servidor opera «{current}»: '
            f'no se envía a otro ambiente de SUNAT.'
        )
    # UNA TABLA DEL CÓDIGO: ni el navegador, ni la empresa, ni una variable de
    # entorno escriben la URL de envío. La fuente oficial de cada constante está en
    # `fiscal/provider.py`.
    endpoints = {FiscalEnvironment.BETA: BETA_ENDPOINT, FiscalEnvironment.PRODUCTION: PRODUCTION_ENDPOINT}
    endpoint = endpoints.get(current)
    if endpoint is None or not endpoint.startswith('https://'):
        raise FiscalConfigError(
            f'No hay endpoint declarado para el ambiente «{current}».'
        )

    # Enviar no firma: lo que se envía ya está firmado (`signing=False`).
    credentials = resolve_credentials(company, signing=False, issuer_tax_id=issuer_tax_id)
    return SunatSoapProvider(
        endpoint=endpoint, ruc=credentials['ruc'],
        sol_user=credentials['sol_user'],
        sol_password=credentials['sol_password'],
        environment=current,
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
    distintos. `resolve_environment()`/`resolve_provider()` sólo operan producción
    con `FISCAL_PRODUCTION_ENABLED` y la consola activada en ese modo, pase lo que
    pase con ésta. Emitir en producción y consultar en producción son capacidades
    separadas, y ninguna se enciende de rebote por la otra (el preflight de
    producción, eso sí, exige las dos).
    """
    return bool(getattr(settings, 'FISCAL_CONSULT_ENABLED', False))


def consult_endpoint(environment: str) -> str:
    """
    El endpoint de `getStatusCdr` para documentos de `environment`. Lo decide el
    servidor; nunca una petición ni la empresa.

    - PRODUCCIÓN: SÓLO el del código (`PRODUCTION_CONSULT_ENDPOINT`). Un
      `FISCAL_CONSULT_ENDPOINT` configurado se NIEGA para producción (C3, revisión
      de #468): la consulta de documentos reales no puede apuntar a otra parte por
      una variable copiada. Vacío (H-14) es la constante.
    - BETA: SUNAT no tiene servicio de consulta en BETA (Manual del programador).
      Consultar un documento de pruebas contra el endpoint de producción sería
      hablar con producción con un documento que allí no existe, así que vacío
      FALLA CERRADO. Sólo un endpoint configurado explícitamente (pruebas de
      integración controladas) se usa para BETA.

    Un endpoint que no es HTTPS falla cerrado, sin repetir su valor.
    """
    from .fiscal.provider import PRODUCTION_CONSULT_ENDPOINT

    configured = (getattr(settings, 'FISCAL_CONSULT_ENDPOINT', '') or '').strip()
    if environment == FiscalEnvironment.PRODUCTION:
        if configured:
            raise FiscalConfigError(
                'FISCAL_CONSULT_ENDPOINT está definido: la consulta de comprobantes de producción sólo usa el '
                'endpoint oficial del código. Quite la variable.'
            )
        endpoint = PRODUCTION_CONSULT_ENDPOINT
    elif configured:
        endpoint = configured
    else:
        raise FiscalConfigError(
            'SUNAT no tiene servicio de consulta de CDR para documentos de pruebas '
            '(BETA): no se consulta producción por un documento BETA.'
        )
    if not endpoint.startswith('https://'):
        raise FiscalConfigError(
            'El endpoint de consulta configurado no es válido: debe ser HTTPS.'
        )
    return endpoint


def resolve_consult_provider(company, *, environment: str, issuer_tax_id: str | None = None):
    """
    El proveedor de CONSULTA (`getStatusCdr`) para documentos de `environment`.
    Endpoint de `consult_endpoint`, jamás de una petición: un inquilino que
    eligiera la URL tendría un SSRF servido.

    FALLA CERRADO si la consulta real no está habilitada (§16/§53). En
    ERP-FISCAL-3 lo está por defecto: la reconciliación se prueba con un proveedor
    inyectado, no con éste. Enciende esta capacidad una fase de producción, con su
    revisión propia — no un descuido de configuración.
    """
    from .fiscal.provider import SunatConsultProvider

    if not fiscal_consult_enabled():
        raise FiscalConfigError(
            'La consulta/reconciliación en línea con SUNAT no está habilitada en '
            'esta versión. Requiere revisión de producción.'
        )
    if environment != FiscalEnvironment.PRODUCTION and intended_environment() == FiscalEnvironment.PRODUCTION:
        # C3: con producción elegida, nada sale hacia un servicio de pruebas por un
        # documento BETA: la cola BETA quedó congelada al cambiar de modo.
        raise FiscalConfigError(
            'El servidor opera SUNAT PRODUCCIÓN: no se consultan documentos de pruebas (BETA).'
        )
    endpoint = consult_endpoint(environment)
    # Consultar no firma (`signing=False`); se compara con el RUC del documento.
    credentials = resolve_credentials(company, signing=False, issuer_tax_id=issuer_tax_id)
    return SunatConsultProvider(
        endpoint=endpoint, ruc=credentials['ruc'],
        sol_user=credentials['sol_user'],
        sol_password=credentials['sol_password'],
    )
