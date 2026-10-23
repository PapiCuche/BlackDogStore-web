"""
LABEL-01 — the single product-code policy (docs/adr-inventory-labels.md D1–D3).

    ensure_code(product, *, supplied=(), actor=None, request=None, generate=True)

is called INSIDE THE TRANSACTION of every product creation (admin create, the
guided registration, and the product import of LABEL-MIGRATION-01). It is the
only place a code is generated, and the frontend never generates one.

THE RULES, IN THE ORDER THEY RUN
--------------------------------
1. Every SUPPLIED code is normalised (`normalize_barcode`: the outside trimmed,
   nothing else — capitals, leading zeros and length are kept) and validated
   (`validate_barcode`). A code must arrive as TEXT: a JSON number has already
   lost its leading zeros, so it is refused rather than guessed back.
2. A supplied code that already belongs to ANOTHER product of the company,
   active or not, refuses the whole creation (409, `code_conflict`, naming that
   product). Nothing is generated to get around it. If it already belongs to
   THIS product it is reused untouched (never reactivated, never re-flagged).
3. A supplied code equal to a device's `imei`, `imei2` or `serial_number` in the
   company is refused (409, `unit_identifier_conflict`): the till looks up
   products before units, so that code would steal the device's scan. An IMEI
   identifies a device, never a model (owner decision 3).
4. The supplied codes that are missing are written. Nothing existing is ever
   overwritten or deactivated; the primary is set only if the product had none.
   A code that differs from another product's only in capitals is accepted —
   the database tells them apart — and comes back as a WARNING to review: image
   matching and some scanners do not.
5. ONLY IF no code was supplied and the product has NO code at all (active or
   inactive), one is generated. A product whose codes were all deactivated gets
   nothing: its identity belongs to whoever deactivated it.

A product therefore receives a generated code at most once in its life. Editing,
changing the price and (re)printing never call this.

THE GENERATED CODE
------------------
`<prefix><zero-padded number>` from `InternalSequence(document_type='product_code')`
at COMPANY scope, e.g. `P000123`. The row is the company's own and editable from
the sequences settings; `DEFAULT_PREFIX` is only the neutral seed written into it
at first use. Shape rules live in `models._product_code_series_errors`: A–Z/0–9
only, at least one letter (never a GTIN), at most `PRODUCT_CODE_MAX_LENGTH` (10)
characters. The default `P` + 6 digits is 27.5 mm wide in Code 128 at 0.25 mm per
module with quiet zones, which fits the 30x20 mm accessory label; a second
prefix letter (30.25 mm) does not. No check digit: Code 128 carries its own.

Each number taken is compared, case-insensitively, against EVERY code of the
company (active or not, any product, deactivated products included) and against
the device identifiers. An occupied one — an imported `P000700`, say — is spent
and skipped, up to `MAX_SKIPS`, then a clear error. Numbers are never recycled:
a gap is cheaper than a duplicate. `unique_barcode_per_company` is the last
barrier, and a race lost there just takes the next number.

CONCURRENCY AND RETRIES
-----------------------
Lock order: the PRODUCT row first, then the SEQUENCE row (inside `allocate`).
Nothing locks them the other way round. Two creations at once queue on the
sequence row and receive distinct numbers. Because the number is taken inside
the creation's transaction, a rolled-back creation (an error, a `dry_run`)
consumes nothing; a replayed registration returns its earlier record and never
runs again.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q

from . import sequences
from .models import (
    PRODUCT_CODE_MAX_LENGTH,
    AdminAuditLog,
    InternalSequence,
    Product,
    ProductBarcode,
    StockUnit,
    normalize_barcode,
    validate_barcode,
)

__all__ = [
    'DEFAULT_PADDING', 'DEFAULT_PREFIX', 'MAX_SKIPS', 'PRODUCT_CODE_MAX_LENGTH',
    'CodeResult', 'ProductCodeError', 'Supplied',
    'ensure_code', 'check_supplied', 'is_unit_identifier', 'product_code_sequence', 'rows',
]

#: The seed of a company's product-code series. Written once into the company's
#: own row (`product_code_sequence`); no allocation reads these constants.
DEFAULT_PREFIX = 'P'
DEFAULT_PADDING = 6

#: How many occupied numbers one generation may skip before it gives up.
MAX_SKIPS = 1000


class ProductCodeError(Exception):
    """
    Nothing was written. `code` is machine-readable, `status` the HTTP answer.

        invalid_code               400  the supplied text is not a valid code
        code_conflict              409  it already identifies another product
        unit_identifier_conflict   409  it is a device's IMEI or serial
        only_inactive_codes        409  (generate endpoint) all codes deactivated
        sequence_inactive          400  the company switched its series off, or it is invalid
        sequence_exhausted         400  no free number / longer than the maximum
        not_in_transaction         500  a programming error, never user input
    """

    def __init__(self, detail: str, *, code: str = 'invalid_code', status: int = 400, value: str = '',
                 existing_product: Product | None = None):
        super().__init__(detail)
        self.detail = detail
        self.code = code
        self.status = status
        self.value = value
        self.existing_product = existing_product

    def body(self, field: str = 'code') -> dict:
        body = {'detail': self.detail, 'field': field, 'code': self.code}
        if self.existing_product is not None:
            body['existing_product'] = self.existing_product.pk
        return body


@dataclass(frozen=True)
class Supplied:
    """A code somebody gave. `symbology=None` means «name it only if its check digit proves it»."""

    code: object
    symbology: str | None = None


@dataclass
class CodeResult:
    added: list = field(default_factory=list)      # supplied codes written now
    reused: list = field(default_factory=list)     # supplied codes the product already had
    generated: ProductBarcode | None = None
    warnings: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def rows(product) -> list[dict]:
    """The product's codes as the panel shows them (the model's order: primary first)."""
    return [
        {
            'id': b.pk,
            'code': b.code,
            'symbology': b.symbology,
            'symbology_label': b.get_symbology_display(),
            'is_primary': b.is_primary,
            'is_active': b.is_active,
        }
        for b in ProductBarcode.objects.filter(product=product)
    ]


def is_unit_identifier(company, code: str) -> bool:
    """
    Whether `code` is a device's IMEI, second IMEI or serial in `company`.

    IMEIs are stored as digits and serials upper-cased (`device_identity`), so
    comparing the IMEI exactly and the serial upper-cased IS the case-insensitive
    comparison, and it keeps the (company, …) indexes usable. Every unit counts,
    sold or not: `find_unit` resolves them all.
    """
    return StockUnit.objects.filter(company=company).filter(
        Q(imei=code) | Q(imei2=code) | Q(serial_number=code.upper()),
    ).exists()


def product_code_sequence(company) -> InternalSequence:
    """The company's product-code series, created with the neutral seed if absent. Idempotent."""
    return sequences.ensure_company_sequence(
        company, InternalSequence.DOCUMENT_PRODUCT_CODE, prefix=DEFAULT_PREFIX, padding=DEFAULT_PADDING,
    )


# ---------------------------------------------------------------------------
# Checking one supplied code
# ---------------------------------------------------------------------------

def _symbology_for(code: str, wanted) -> str:
    if wanted and wanted in dict(ProductBarcode.SYMBOLOGY_CHOICES) and wanted != ProductBarcode.UNKNOWN:
        return wanted
    # Lazy: `import_services` may import this module (LABEL-MIGRATION-01).
    from .import_services import detect_symbology

    return detect_symbology(code)


def check_supplied(company, product, raw, symbology=None):
    """
    Rules 1–3 for one supplied code, WITHOUT writing.

    Returns None for a blank value, else `(code, symbology, own_row, warnings)`
    where `own_row` is this product's existing row for that code (reused as is).
    Raises `ProductCodeError`.
    """
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ProductCodeError(
            'El código se envía como texto: como número pierde los ceros a la izquierda.',
            value=str(raw),
        )
    code = normalize_barcode(raw)
    if not code:
        return None
    try:
        validate_barcode(code)
    except DjangoValidationError as exc:
        raise ProductCodeError(exc.messages[0], value=code) from None

    holder = ProductBarcode.objects.filter(company=company, code=code).select_related('product').first()
    if holder is not None:
        if holder.product_id != product.pk:
            raise ProductCodeError(
                f'El código «{code}» ya identifica a «{holder.product.name}» en esta empresa. '
                'Revisa el código: no se genera otro en su lugar.',
                code='code_conflict', status=409, value=code, existing_product=holder.product,
            )
        return code, holder.symbology, holder, []

    if is_unit_identifier(company, code):
        raise ProductCodeError(
            f'«{code}» es el IMEI o la serie de un equipo de esta empresa. Un código de producto no puede '
            'repetirlo: la caja dejaría de reconocer el equipo.',
            code='unit_identifier_conflict', status=409, value=code,
        )

    warnings = []
    lookalike = (ProductBarcode.objects.filter(company=company, code__iexact=code)
                 .exclude(product=product).select_related('product').first())
    if lookalike is not None:
        warnings.append(
            f'«{code}» difiere sólo en mayúsculas de «{lookalike.code}» («{lookalike.product.name}»). '
            'Se guardó, pero revísalo: algunos lectores no distinguen mayúsculas.'
        )
    return code, _symbology_for(code, symbology), None, warnings


# ---------------------------------------------------------------------------
# The policy
# ---------------------------------------------------------------------------

def _as_supplied(item) -> Supplied:
    if isinstance(item, Supplied):
        return item
    if isinstance(item, (tuple, list)):
        return Supplied(*item)
    return Supplied(item)


def _audit(barcode, *, actor, request, generated: bool):
    AdminAuditLog.log(
        actor=actor, action='barcode_created', target_type='product_barcode', target_id=barcode.pk,
        metadata={
            'company_id': barcode.company_id, 'product_id': barcode.product_id,
            'symbology': barcode.symbology, 'generated': generated,
        },
        request=request, company=barcode.company,
    )


def ensure_code(product, *, supplied=(), actor=None, request=None, generate: bool = True) -> CodeResult:
    """
    Apply the policy to `product` (see the module docstring).

    `supplied` is an iterable of codes: `str`, `(code, symbology)` or `Supplied`.
    Blank values are ignored. `generate=False` applies rules 1–4 and never
    generates (e.g. an import row updating a product it found).

    MUST run inside the transaction that writes the product. Raises
    `ProductCodeError` before writing anything when a supplied code is refused;
    the caller lets it roll the transaction back.
    """
    if not transaction.get_connection().in_atomic_block:
        raise ProductCodeError('ensure_code() debe llamarse dentro de la transacción del alta.',
                               code='not_in_transaction', status=500)
    company = product.company
    # Lock order: product, then sequence (see the module docstring).
    Product.objects.select_for_update().filter(pk=product.pk).values_list('pk', flat=True).first()

    result = CodeResult()
    checked, seen = [], set()
    for item in supplied or ():
        wanted = _as_supplied(item)
        outcome = check_supplied(company, product, wanted.code, wanted.symbology)
        if outcome is None or outcome[0] in seen:
            continue
        seen.add(outcome[0])
        checked.append(outcome)

    has_primary = ProductBarcode.objects.filter(product=product, is_primary=True).exists()
    for code, symbology, own_row, warnings in checked:
        result.warnings.extend(warnings)
        if own_row is not None:
            result.reused.append(own_row)
            continue
        barcode = ProductBarcode(company=company, product=product, code=code, symbology=symbology,
                                 is_primary=not has_primary)
        try:
            with transaction.atomic():
                barcode.save()
        except IntegrityError:
            # Taken by a concurrent writer between the check and the insert.
            raise ProductCodeError(
                f'El código «{code}» acaba de registrarse para otro producto de esta empresa.',
                code='code_conflict', status=409, value=code,
            ) from None
        except DjangoValidationError as exc:
            raise ProductCodeError(exc.messages[0], value=code) from None
        has_primary = True
        result.added.append(barcode)
        _audit(barcode, actor=actor, request=request, generated=False)

    if generate and not checked and not ProductBarcode.objects.filter(product=product).exists():
        result.generated = _generate(product, primary=not has_primary)
        _audit(result.generated, actor=actor, request=request, generated=True)
    return result


def _code_taken(company, code: str) -> bool:
    return (ProductBarcode.objects.filter(company=company, code__iexact=code).exists()
            or is_unit_identifier(company, code))


def _generate(product, *, primary: bool) -> ProductBarcode:
    company = product.company
    sequence = product_code_sequence(company)
    for _ in range(MAX_SKIPS):
        try:
            _value, code = sequences.allocate(sequence)
        except sequences.SequenceError as exc:
            raise ProductCodeError(
                f'{exc} Activa la serie «Código de producto» o escribe un código.',
                code='sequence_inactive',
            ) from None
        except DjangoValidationError as exc:
            raise ProductCodeError(
                'La serie «Código de producto» no es válida: ' + ' '.join(exc.messages),
                code='sequence_inactive',
            ) from None
        if len(code) > PRODUCT_CODE_MAX_LENGTH:
            raise ProductCodeError(
                f'La serie «Código de producto» llegó a {code}, más de {PRODUCT_CODE_MAX_LENGTH} caracteres. '
                'Cambia su prefijo o sus dígitos.',
                code='sequence_exhausted',
            )
        if _code_taken(company, code):
            continue                                   # spent and skipped, never recycled
        barcode = ProductBarcode(company=company, product=product, code=code,
                                 symbology=ProductBarcode.INTERNAL, is_primary=primary)
        try:
            with transaction.atomic():
                barcode.save()
        except IntegrityError:
            continue                                   # a concurrent manual code took it
        return barcode
    raise ProductCodeError(
        f'No se encontró un código libre tras {MAX_SKIPS} números ocupados. Revisa la serie '
        '«Código de producto» (su próximo número o su prefijo).',
        code='sequence_exhausted',
    )
