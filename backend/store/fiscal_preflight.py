"""
SUNAT-PREFLIGHT (FP-1): what stands between one company and issuing, in one read.

    python manage.py fiscal_preflight --company <slug> [--target beta|production] [--json]

`docs/sunat-estado-2026-11.md`, G4. One line per check, `GO`, `AVISO` or `NO-GO`, and
exit status 1 when any line says NO-GO, so that the production activation checklist
(section 6 of that document) has a machine answer for the items code can see.

WHAT IT NEVER DOES
------------------
- It writes nothing. It only reads rows and opens the sealed configuration in memory,
  the same way issuing does.
- It never talks to the network. Whether SUNAT accepts the SOL password, or whether the
  certificate is registered in SOL, cannot be known without asking SUNAT, so it is not
  claimed.
- It never prints a secret: not the SOL password, not the certificate password, not the
  private key. It does not print the SOL user or the credentials' RUC either. The RUC
  check says only whether they match the company.

`--target production` always ends in NO-GO in this version, because the code only
operates BETA (`fiscal_config.ONLY_SUPPORTED_ENVIRONMENT`). The other lines still say
what would be missing once it does.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from .fiscal import rules
from .fiscal_config import (
    ONLY_SUPPORTED_ENVIRONMENT, FiscalConfigError, fiscal_consult_enabled, fiscal_enabled, resolve_environment,
)
from .models import (
    FISCAL_NOTE_TYPES, FiscalDailySummary, FiscalDailySummaryDocument, FiscalDocument, FiscalDocumentStatus,
    FiscalDocumentType, FiscalEnvironment, FiscalSeries, FiscalSubmissionAttempt, FiscalSummaryStatus,
    FiscalVoidCommunication, FiscalVoidStatus,
)

GO = 'GO'
WARN = 'AVISO'
NO_GO = 'NO-GO'

#: Factura and its linked notes: up to 3 calendar days from the day after issue
#: (RS 097-2012, art. 12.1 c), as worded by RS 000003-2023, art. 3). The warning comes one day earlier.
INVOICE_DEADLINE_DAYS = 3
INVOICE_WARNING_DAYS = 2
#: Boletas: the daily summary no later than the 7th calendar day after issue (RS 097-2012,
#: art. 21.1, as worded by RS 114-2019).
RECEIPT_DEADLINE_DAYS = 7
RECEIPT_WARNING_DAYS = 5
#: A summary in one of these states reached SUNAT, or may have: its boletas are informed
#: (or waiting on a verdict). GENERATED, SIGNED, SUBMISSION_ERROR and REJECTED inform nothing.
SUMMARY_REACHED_SUNAT = (
    FiscalSummaryStatus.SUBMITTED, FiscalSummaryStatus.ACCEPTED, FiscalSummaryStatus.ACCEPTED_WITH_OBSERVATION,
    FiscalSummaryStatus.SUBMISSION_UNKNOWN,
)
#: Certificate renewal takes time: under 60 days is worth knowing, under 30 blocks production.
CERT_WARNING_DAYS = 60
CERT_BLOCKING_DAYS = 30


@dataclass(frozen=True)
class Check:
    level: str
    area: str
    detail: str


def _credentials_for_diagnosis() -> tuple[dict | None, str, str]:
    """
    `(credentials, origin, problem)`: the configured credentials, read the way issuing
    reads them but WITHOUT the on/off switch, so a switched-off installation can still
    be diagnosed. Nothing of them leaves this module except yes/no answers and the
    certificate's public metadata.
    """
    from . import fiscal_config

    active = fiscal_config._console_row()
    if active is not None:
        try:
            return fiscal_config._console_credentials(active), 'consola de integraciones', ''
        except FiscalConfigError as exc:
            return None, 'consola de integraciones', str(exc)
    ruc = (getattr(settings, 'FISCAL_SOL_RUC', '') or '').strip()
    user = (getattr(settings, 'FISCAL_SOL_USER', '') or '').strip()
    password = getattr(settings, 'FISCAL_SOL_PASSWORD', '') or ''
    if not (ruc and user and password):
        return None, 'variables del servidor', 'Faltan credenciales SOL en la configuración del servidor.'
    try:
        cert_pem, key_pem = fiscal_config._resolve_signing_material()
    except FiscalConfigError as exc:
        return None, 'variables del servidor', str(exc)
    return {'ruc': ruc, 'sol_user': user, 'sol_password': password,
            'cert_pem': cert_pem, 'key_pem': key_pem}, 'variables del servidor', ''


def _switch(target: str) -> list[Check]:
    checks = [Check(GO if fiscal_enabled() else WARN, 'interruptor',
                    'emisión encendida' if fiscal_enabled() else 'emisión apagada en este servidor')]
    try:
        environment = resolve_environment()
        checks.append(Check(GO, 'ambiente', f'el servidor opera «{environment}»'))
    except FiscalConfigError as exc:
        checks.append(Check(NO_GO, 'ambiente', str(exc)))
    if target != ONLY_SUPPORTED_ENVIRONMENT:
        checks.append(Check(NO_GO, 'ambiente', (
            f'esta versión sólo opera «{ONLY_SUPPORTED_ENVIRONMENT}»: producción exige su propia fase '
            f'y el GO «GO activación SUNAT producción <empresa>»')))
    return checks


def _issuer(company) -> list[Check]:
    ruc = (company.tax_id or '').strip()
    checks = [Check(GO, 'emisor', 'RUC con formato válido') if rules.check_ruc(ruc)
              else Check(NO_GO, 'emisor', 'la empresa no tiene un RUC con formato válido')]
    checks.append(Check(GO, 'emisor', 'razón social registrada') if (company.legal_name or '').strip()
                  else Check(NO_GO, 'emisor', 'la empresa no tiene razón social'))
    return checks


def _credentials_and_certificate(company, target: str, now) -> list[Check]:
    from cryptography import x509

    from .fiscal.certificate import CertificateError, check_signing_pair, metadata_from_certificate

    credentials, origin, problem = _credentials_for_diagnosis()
    if credentials is None:
        return [Check(NO_GO, 'credenciales', f'{problem} (origen: {origin})')]
    checks = [Check(GO, 'credenciales', f'usuario y clave SOL configurados (origen: {origin})')]
    if credentials['ruc'] == (company.tax_id or '').strip():
        checks.append(Check(GO, 'credenciales', 'el RUC de la Clave SOL es el de la empresa'))
    else:
        checks.append(Check(NO_GO, 'credenciales', (
            'las credenciales SOL no son del RUC de esta empresa: una empresa no emite con las '
            'credenciales de otro contribuyente')))

    try:
        check_signing_pair(credentials['cert_pem'], credentials['key_pem'])
    except CertificateError as exc:
        return [*checks, Check(NO_GO, 'certificado', str(exc))]
    # `check_signing_pair` already proved that the PEM reads.
    meta = metadata_from_certificate(x509.load_pem_x509_certificate(credentials['cert_pem']))
    # Neither the subject nor the issuer is printed: they name people and other
    # companies. What the operator needs is whether the RUC is there (below), the
    # dates and a fingerprint to compare with the file in hand.
    checks.append(Check(GO, 'certificado', (
        f'se lee y casa con su clave; vigente del {meta.not_before:%d/%m/%Y} al {meta.not_after:%d/%m/%Y}, '
        f'huella SHA-256 {meta.fingerprint_sha256[:16]}…')))

    production = target == FiscalEnvironment.PRODUCTION
    validity = meta.validity(now=now)
    days_left = (meta.not_after - now).days
    if validity == 'expired':
        checks.append(Check(NO_GO, 'certificado', f'venció el {meta.not_after:%d/%m/%Y}'))
    elif validity == 'not_yet_valid':
        checks.append(Check(NO_GO, 'certificado', f'todavía no es válido: empieza el {meta.not_before:%d/%m/%Y}'))
    elif days_left < CERT_BLOCKING_DAYS:
        checks.append(Check(NO_GO if production else WARN, 'certificado', f'vence en {days_left} días'))
    elif days_left < CERT_WARNING_DAYS:
        checks.append(Check(WARN, 'certificado', f'vence en {days_left} días'))
    else:
        checks.append(Check(GO, 'certificado', f'vigente por {days_left} días más'))

    if meta.subject == meta.issuer:
        checks.append(Check(NO_GO if production else WARN, 'certificado', (
            'es autofirmado: sirve para BETA, no para producción (SUNAT exige uno emitido a nombre del RUC '
            'y registrado en SOL)')))
    ruc = (company.tax_id or '').strip()
    if ruc and ruc in re.findall(r'\d{11}', meta.subject):
        checks.append(Check(GO, 'certificado', 'RUC de la empresa presente en el certificado: sí'))
    else:
        checks.append(Check(NO_GO if production else WARN, 'certificado', (
            'RUC de la empresa presente en el certificado: no (SUNAT lo exige en el campo OU; BETA no)')))
    checks.append(Check(WARN, 'certificado', (
        'el registro del certificado en SOL no se puede comprobar desde aquí: lo atestigua el propietario')))
    return checks


_NOUN = {
    FiscalDocumentType.INVOICE: 'factura', FiscalDocumentType.RECEIPT: 'boleta',
    FiscalDocumentType.CREDIT_NOTE: 'nota de crédito', FiscalDocumentType.DEBIT_NOTE: 'nota de débito',
}


def _series(company, target: str) -> list[Check]:
    """One active series per type, branch (or company) and environment; the same rule `resolve_series` applies."""
    checks = []
    active = list(FiscalSeries.objects.filter(company=company, environment=target, is_active=True)
                  .values_list('document_type', 'series', 'branch_id'))
    groups: dict[tuple, list[str]] = {}
    for document_type, series, branch_id in active:
        prefix = series[:1] if document_type in FISCAL_NOTE_TYPES else ''
        groups.setdefault((document_type, prefix, branch_id), []).append(series)
    for (document_type, prefix, branch_id), names in sorted(groups.items(), key=lambda g: (g[0][0], g[0][1], g[0][2] or 0)):
        if len(names) > 1:
            where = f'la sucursal {branch_id}' if branch_id else 'la empresa'
            checks.append(Check(NO_GO, 'series', (
                f'{len(names)} series de {_NOUN[document_type]}{" " + prefix if prefix else ""} activas en {where} '
                f'({", ".join(sorted(names))}): no hay regla para elegir')))
    for document_type, prefix in ((FiscalDocumentType.INVOICE, ''), (FiscalDocumentType.RECEIPT, ''),
                                  (FiscalDocumentType.CREDIT_NOTE, 'F'), (FiscalDocumentType.DEBIT_NOTE, 'F')):
        names = sorted({series for t, series, _b in active
                        if t == document_type and (not prefix or series.startswith(prefix))})
        noun = _NOUN[document_type] + (' de factura' if prefix else '')
        if names:
            checks.append(Check(GO, 'series', f'{noun}: {", ".join(names)} ({target})'))
        else:
            # Production without a factura series is not an activation at all.
            blocking = target == FiscalEnvironment.PRODUCTION and document_type == FiscalDocumentType.INVOICE
            checks.append(Check(NO_GO if blocking else WARN, 'series',
                                f'sin serie de {noun} activa en «{target}»: no se emitirá'))
    return checks


def _queue(company, target: str, now) -> list[Check]:
    """What is waiting on SUNAT, and what is close to (or past) its legal deadline."""
    from .fiscal_services import STALE_ATTEMPT_MINUTES, submission_uncertainty

    checks = []
    today = timezone.localdate(now)
    documents = FiscalDocument.objects.filter(company=company, environment=target)
    production = target == FiscalEnvironment.PRODUCTION

    errors = list(documents.filter(status=FiscalDocumentStatus.SUBMISSION_ERROR)
                  .only('pk', 'status', 'document_type', 'series', 'issued_at'))
    uncertain_pks = {document.pk for document in errors if submission_uncertainty(document)}
    if errors:
        checks.append(Check(NO_GO if production else WARN, 'cola', (
            f'{len(errors)} comprobante(s) con error de envío; {len(uncertain_pks)} incierto(s): se reconcilian '
            f'por su CDR, no se reenvían')))
    attempts = FiscalSubmissionAttempt.objects.filter(
        document__company=company, document__environment=target, finished_at__isnull=True)
    stale = now - timedelta(minutes=STALE_ATTEMPT_MINUTES)
    abandoned = attempts.filter(started_at__lt=stale).count()
    running = attempts.filter(started_at__gte=stale).count()
    if abandoned:
        # The process died mid-send: SUNAT may have it. Same as an uncertain send.
        checks.append(Check(NO_GO if production else WARN, 'cola', (
            f'{abandoned} envío(s) abandonado(s) sin terminar: pudieron llegar a SUNAT, se reconcilian por su CDR')))
    if running:
        checks.append(Check(WARN, 'cola', f'{running} envío(s) en curso'))

    # Sent one by one: facturas and the notes of a factura. Not yet sent, or sent
    # and refused before SUNAT received them (a SUBMISSION_ERROR that is not
    # uncertain): the 3 days run for both. An uncertain one may already be in
    # SUNAT, and is reported above.
    individual = documents.filter(
        status__in=(FiscalDocumentStatus.PENDING, FiscalDocumentStatus.GENERATED, FiscalDocumentStatus.SIGNED,
                    FiscalDocumentStatus.SUBMISSION_ERROR),
    ).exclude(document_type=FiscalDocumentType.RECEIPT).exclude(
        document_type__in=FISCAL_NOTE_TYPES, series__startswith='B').exclude(pk__in=uncertain_pks)
    late = soon = 0
    for issued_at in individual.values_list('issued_at', flat=True):
        age = (today - timezone.localdate(issued_at)).days
        if age > INVOICE_DEADLINE_DAYS:
            late += 1
        elif age >= INVOICE_WARNING_DAYS:
            soon += 1
    if late:
        checks.append(Check(NO_GO, 'plazos', (
            f'{late} factura(s) o nota(s) sin enviar con más de {INVOICE_DEADLINE_DAYS} días: fuera del plazo '
            f'(RS 097-2012, art. 12.1 c), texto de la RS 000003-2023; decisión del contador)')))
    if soon:
        checks.append(Check(WARN, 'plazos', f'{soon} factura(s) o nota(s) sin enviar a punto de vencer su plazo'))

    # Informed by the daily summary. A boleta counts as informed only when its live
    # summary line belongs to a summary that reached SUNAT (or may have): one that
    # is only generated or signed, or that failed before leaving, informs nothing.
    in_summary = FiscalDailySummaryDocument.objects.filter(
        superseded=False, summary__status__in=SUMMARY_REACHED_SUNAT).values('document_id')
    pending = documents.filter(
        document_type=FiscalDocumentType.RECEIPT,
        status__in=(FiscalDocumentStatus.PENDING, FiscalDocumentStatus.GENERATED, FiscalDocumentStatus.SIGNED),
    ).exclude(pk__in=in_summary)
    late = soon = 0
    for issued_at in pending.values_list('issued_at', flat=True):
        age = (today - timezone.localdate(issued_at)).days
        if age > RECEIPT_DEADLINE_DAYS:
            late += 1
        elif age >= RECEIPT_WARNING_DAYS:
            soon += 1
    if late:
        checks.append(Check(NO_GO, 'plazos', (
            f'{late} boleta(s) sin resumen diario con más de {RECEIPT_DEADLINE_DAYS} días '
            f'(RS 097-2012, art. 21.1, texto de la RS 114-2019)')))
    if soon:
        checks.append(Check(WARN, 'plazos', f'{soon} boleta(s) sin resumen diario a punto de vencer su plazo'))

    summaries = FiscalDailySummary.objects.filter(company=company, environment=target)
    unsent = summaries.filter(status__in=(FiscalSummaryStatus.GENERATED, FiscalSummaryStatus.SIGNED)).count()
    if unsent:
        checks.append(Check(WARN, 'cola', f'{unsent} resumen(es) diario(s) generado(s) o firmado(s) sin enviar'))
    waiting = summaries.filter(status__in=(FiscalSummaryStatus.SUBMITTED, FiscalSummaryStatus.SUBMISSION_ERROR,
                                           FiscalSummaryStatus.SUBMISSION_UNKNOWN)).count()
    if waiting:
        checks.append(Check(WARN, 'cola', f'{waiting} resumen(es) diario(s) sin veredicto de SUNAT'))
    voids = FiscalVoidCommunication.objects.filter(
        company=company, environment=target,
        status__in=(FiscalVoidStatus.SUBMITTED, FiscalVoidStatus.SUBMISSION_ERROR,
                    FiscalVoidStatus.SUBMISSION_UNKNOWN)).count()
    if voids:
        checks.append(Check(WARN, 'cola', f'{voids} comunicación(es) de baja sin veredicto de SUNAT'))
    if not checks:
        checks.append(Check(GO, 'cola', 'nada pendiente con SUNAT ni cerca de su plazo'))
    return checks


def _consult(target: str) -> list[Check]:
    from urllib.parse import urlparse

    from .fiscal.provider import PRODUCTION_CONSULT_ENDPOINT

    production = target == FiscalEnvironment.PRODUCTION
    configured = (getattr(settings, 'FISCAL_CONSULT_ENDPOINT', '') or '').strip()
    endpoint = configured or PRODUCTION_CONSULT_ENDPOINT
    if not fiscal_consult_enabled():
        return [Check(NO_GO if production else WARN, 'consulta', (
            'la consulta de CDR (getStatusCdr) está apagada: es la única salida ante un envío incierto, y en '
            'producción se enciende junto con la emisión. BETA no tiene ese servicio'))]
    if not endpoint.startswith('https://'):
        return [Check(NO_GO, 'consulta', 'el endpoint de consulta configurado no es HTTPS')]
    origin = 'configurado' if configured else 'el del código'
    return [Check(GO, 'consulta', f'encendida contra {urlparse(endpoint).hostname} ({origin})')]


def _sealing() -> list[Check]:
    if getattr(settings, 'APP_CONFIG_ENCRYPTION_KEY', ''):
        return [Check(GO, 'cifrado', 'hay clave de cifrado de la configuración (APP_CONFIG_ENCRYPTION_KEY)')]
    return [Check(WARN, 'cifrado', (
        'no hay APP_CONFIG_ENCRYPTION_KEY: la consola de integraciones no puede guardar credenciales cifradas'))]


def run(company, *, target: str = FiscalEnvironment.BETA, now=None) -> list[Check]:
    """Every check for `company` against `target`. Reads only."""
    now = now or timezone.now()
    return [
        *_switch(target),
        *_issuer(company),
        *_credentials_and_certificate(company, target, now),
        *_series(company, target),
        *_queue(company, target, now),
        *_consult(target),
        *_sealing(),
    ]


def verdict(checks: list[Check]) -> str:
    return NO_GO if any(check.level == NO_GO for check in checks) else GO


def as_dict(company, target: str, checks: list[Check]) -> dict:
    return {'company': company.slug, 'target': target, 'verdict': verdict(checks),
            'checks': [asdict(check) for check in checks]}

