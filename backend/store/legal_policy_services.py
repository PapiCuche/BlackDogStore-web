"""
LEGAL-POLICIES-01 — the three public legal documents of a company.

ONE WRITER
----------
Everything that changes a `CompanyLegalPolicy` goes through this module, with the
company passed explicitly by the caller (resolved from the session, never from the
request body). The public page reads `published_text` only, through `public_policy`.

WHAT IS CHECKED
---------------
* the kind is one of the three;
* the text is plain text within `MAX_LENGTH` (control characters other than line
  breaks and tabs are refused: they are never part of a legal text, and a NUL
  cannot even be stored by PostgreSQL);
* publishing refuses an empty draft and a draft that still carries a template
  placeholder such as «[RUC]» or «[POR CONFIRMAR]»: a placeholder published as a
  legal statement is worse than no statement.

The audit log records WHO did WHAT to WHICH document, and the length — never the
text itself, like `company_settings_updated` never copies a policy into the log.
"""

from __future__ import annotations

import re

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import AdminAuditLog, CompanyLegalPolicy

KINDS = CompanyLegalPolicy.KINDS
MAX_LENGTH = CompanyLegalPolicy.MAX_LENGTH

#: Template placeholders («[RUC]», «[CORREO]», «[PENDIENTE]», «[POR CONFIRMAR]»…), in
#: any case. A KNOWN list, not «any word in brackets»: a real text may cite «[LPDP]»
#: or «[ANPD]», and refusing it would name an acronym as a missing field.
PLACEHOLDER = re.compile(
    r'\[\s*(?:RUC|CORREO|EMAIL|E-MAIL|PENDIENTE|POR\s+CONFIRMAR|POR\s+DEFINIR|COMPLETAR|'
    r'DIRECCI[OÓ]N|RAZ[OÓ]N\s+SOCIAL|NOMBRE(?:\s+COMERCIAL)?|TEL[EÉ]FONO|WHATSAPP|DNI|'
    r'FECHA|CIUDAD|PLAZO|URL|WEB|SITIO\s+WEB|EMPRESA|RESPONSABLE|X+|\.{3}|…)\s*\]',
    re.IGNORECASE,
)
_CONTROL = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')


def _check_kind(kind: str) -> str:
    if kind not in KINDS:
        raise ValidationError({'kind': 'Documento legal desconocido.'})
    return kind


def clean_text(value) -> str:
    """The text as it will be stored, or a ValidationError naming the problem."""
    if value is None:
        value = ''
    if not isinstance(value, str):
        raise ValidationError({'text': 'El texto debe ser texto plano.'})
    text = value.replace('\r\n', '\n').replace('\r', '\n')
    if _CONTROL.search(text):
        raise ValidationError({'text': 'El texto contiene caracteres de control no permitidos.'})
    text = text.strip()
    if len(text) > MAX_LENGTH:
        raise ValidationError({'text': f'El texto supera el máximo de {MAX_LENGTH} caracteres ({len(text)}).'})
    return text


def placeholders_in(text: str) -> list[str]:
    return sorted({match.group(0) for match in PLACEHOLDER.finditer(text or '')})


def policies_for(company) -> dict[str, CompanyLegalPolicy | None]:
    rows = {row.kind: row for row in CompanyLegalPolicy.objects.filter(company=company)}
    return {kind: rows.get(kind) for kind in KINDS}


def _row_for_update(company, kind):
    row, _ = CompanyLegalPolicy.objects.select_for_update().get_or_create(company=company, kind=kind)
    return row


def _audit(action, row, *, actor, request, extra=None):
    AdminAuditLog.log(
        actor=actor, action=action, target_type='company_legal_policy', target_id=row.pk,
        metadata={'company_id': row.company_id, 'kind': row.kind, **(extra or {})},
        request=request, company=row.company,
    )


@transaction.atomic
def save_draft(company, kind, text, *, actor, request=None) -> CompanyLegalPolicy:
    _check_kind(kind)
    text = clean_text(text)
    row = _row_for_update(company, kind)
    if row.draft_text != text:
        row.draft_text = text
        row.updated_by = actor
        row.save(update_fields=['draft_text', 'updated_by', 'updated_at'])
        _audit('legal_policy_draft_saved', row, actor=actor, request=request,
               extra={'length': len(text)})
    return row


@transaction.atomic
def publish(company, kind, *, actor, request=None) -> CompanyLegalPolicy:
    """Copy the saved draft into the published text. What is published is what was saved."""
    _check_kind(kind)
    row = _row_for_update(company, kind)
    text = row.draft_text
    if not text.strip():
        raise ValidationError({'text': 'Guarda un texto antes de publicarlo.'})
    found = placeholders_in(text)
    if found:
        raise ValidationError({'text': 'El texto todavía tiene campos sin completar: ' + ', '.join(found) + '.'})
    row.published_text = text
    row.published_at = timezone.now()
    row.published_by = actor
    row.save(update_fields=['published_text', 'published_at', 'published_by', 'updated_at'])
    _audit('legal_policy_published', row, actor=actor, request=request, extra={'length': len(text)})
    return row


@transaction.atomic
def unpublish(company, kind, *, actor, request=None) -> CompanyLegalPolicy:
    """Withdraw the public text. The draft stays as it is."""
    _check_kind(kind)
    row = _row_for_update(company, kind)
    if row.published_text:
        row.published_text = ''
        row.published_at = None
        row.published_by = None
        row.save(update_fields=['published_text', 'published_at', 'published_by', 'updated_at'])
        _audit('legal_policy_unpublished', row, actor=actor, request=request)
    return row


def public_policy(company, kind) -> dict | None:
    """The published text of this company's document, or None when there is none."""
    if kind not in KINDS or company is None:
        return None
    row = CompanyLegalPolicy.objects.filter(company=company, kind=kind).first()
    if row is None or not row.published_text.strip():
        return None
    return {
        'kind': kind,
        'text': row.published_text,
        'published_at': row.published_at.isoformat() if row.published_at else None,
    }
