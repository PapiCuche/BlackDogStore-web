"""
SALES-EMAIL-03 — send a COPY of a sale's document to an address that is not the customer's.

«Reenviar al correo del cliente» (`email_services.resend_order_confirmation_email`)
keeps doing what it did. This module is the other act: the owner types an
address — the buyer's accountant, a company mailbox — and one document of the
sale goes there, once. The customer's own e-mail is never read as the
destination and never written.

WHAT CAN GO, AND WHAT IT IS CALLED
----------------------------------
Three documents, named for what they ARE, never for what was asked for:

  confirmation  the confirmation e-mail of the order, with the «Constancia de
                pedido» PDF — an internal document, not a receipt.
  sales_note    the internal sales note (NV-…) PDF: «Documento de control
                interno, sin validez tributaria». Never called boleta/factura.
  fiscal        the printed representation of the order's ELECTRONIC receipt —
                only when one really exists: production environment, accepted by
                SUNAT and not voided. A beta (test) receipt has no tax validity
                and is not sent outside; SUNAT is in beta today, so in practice
                this option says why it is unavailable.

A document is only offered to whoever could download it already: the sales note
asks for the sales-note capability and the electronic receipt for
`sales.fiscal.view`, the same ones their own PDF routes ask for. A copy is not a
way round those.

THE ATTACHMENT IS THE POINT
---------------------------
The resend to the customer treats the PDF as best effort. A copy does not: if
the PDF cannot be drawn, NOTHING is sent (`CopyPdfError`) and the caller says
so. «Enviado con adjunto» when there was none would be a lie the operator
cannot see; «enviado sin adjunto» would be a different act from the one the
screen confirmed.

THE ADDRESS
-----------
`normalise_recipient` accepts ONE plain address and nothing else: no display
name, no list, no separator, no line break, nothing outside printable ASCII,
254 characters at most, and it must pass Django's `EmailValidator` and come
back unchanged from `email.utils.parseaddr`. What reaches `to=[...]` is a single
string the mail library cannot read as two recipients or as a header.

The full address is never logged or audited: `mask_email` keeps the first
letters of the local part and the domain, and `recipient_digest` is a keyed
hash that lets support confirm «was it this address?» without storing it.
"""
from __future__ import annotations

import hashlib
import hmac
import html as _html
import logging
from email.utils import parseaddr

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import EmailMultiAlternatives
from django.core.validators import EmailValidator

logger = logging.getLogger(__name__)

KIND_CONFIRMATION = 'confirmation'
KIND_SALES_NOTE = 'sales_note'
KIND_FISCAL = 'fiscal'
KINDS = (KIND_CONFIRMATION, KIND_SALES_NOTE, KIND_FISCAL)

#: RFC 5321 path limit, and what Django's own EmailField allows.
MAX_ADDRESS_LENGTH = 254
#: RFC 5321 §4.5.3.1.1: the part before the @. Django's validator does not check it.
MAX_LOCAL_LENGTH = 64

#: Characters that turn one address into something else: a list (`,` `;`), a
#: display name or a group (`<` `>` `"` `(` `)` `:`), a domain literal (`[` `]`)
#: or an escape (`\`). None of them belongs in the plain address a person types.
_FORBIDDEN = frozenset(',;<>"():[]\\')

#: How the receipt types read in a sentence. The paper prints them in capitals
#: (`fiscal_pdf_services._TITLE`); a subject line does not shout.
_FISCAL_NAMES = {
    '01': 'Factura electrónica',
    '03': 'Boleta de venta electrónica',
}

_SALES_NOTE_NOTE = 'Documento de control interno, sin validez tributaria. No es un comprobante electrónico.'
_RECEIPT_NOTE = 'Documento interno del pedido. No es un comprobante electrónico.'


class CopyError(Exception):
    """A copy that cannot be sent as asked. Views map it to 400."""


class CopyAddressError(CopyError):
    """The destination is not one plain address."""


class CopyUnavailable(CopyError):
    """The document asked for does not exist for this sale, or may not go out."""


class CopyPdfError(Exception):
    """The PDF could not be drawn. Nothing was sent."""


# ---------------------------------------------------------------------------
# The address
# ---------------------------------------------------------------------------

_validate = EmailValidator(message='Escribe un correo válido.')


def normalise_recipient(raw, confirmation=None) -> str:
    """
    The one address a copy goes to, or `CopyAddressError` saying what is wrong.

    `confirmation` is the same address typed a second time. Compared without
    regard to case: `Ana@Correo.pe` and `ana@correo.pe` reach the same mailbox,
    and refusing them would teach people to paste instead of read.
    """
    if not isinstance(raw, str):
        raise CopyAddressError('Escribe el correo de destino.')
    # Before stripping: a trailing line break is exactly what a header injection
    # looks like, and quietly removing it would hide the attempt.
    if any(ch in raw for ch in '\r\n\x00'):
        raise CopyAddressError('El correo no puede tener saltos de línea.')
    value = raw.strip()
    if not value:
        raise CopyAddressError('Escribe el correo de destino.')
    if len(value) > MAX_ADDRESS_LENGTH:
        raise CopyAddressError(f'El correo no puede pasar de {MAX_ADDRESS_LENGTH} caracteres.')
    if any(ch in _FORBIDDEN for ch in value) or value.count('@') != 1:
        raise CopyAddressError('Escribe una sola dirección de correo, sin nombre, comas ni punto y coma.')
    if any(not 33 <= ord(ch) <= 126 for ch in value):
        raise CopyAddressError('El correo no puede tener espacios, tildes ni caracteres especiales.')
    if len(value.split('@', 1)[0]) > MAX_LOCAL_LENGTH:
        raise CopyAddressError(f'La parte antes de la @ no puede pasar de {MAX_LOCAL_LENGTH} caracteres.')
    try:
        _validate(value)
    except ValidationError:
        raise CopyAddressError('Escribe un correo válido.') from None
    if parseaddr(value) != ('', value):
        raise CopyAddressError('Escribe una sola dirección de correo.')

    if confirmation is not None:
        if not isinstance(confirmation, str) or confirmation.strip().lower() != value.lower():
            raise CopyAddressError('Los dos correos no coinciden.')
    return value


def mask_email(address: str) -> str:
    """`juan.perez@gmail.com` → `ju***@gmail.com`. What the audit keeps."""
    local, _, domain = (address or '').rpartition('@')
    if not local:
        return '***'
    keep = local[:2] if len(local) > 3 else local[:1]
    return f'{keep}***@{domain}'


def recipient_digest(address: str) -> str:
    """
    A keyed fingerprint of the address, to answer «was it sent to X?» later.

    HMAC with the installation's secret, so the audit row does not become a
    dictionary of addresses anyone with a list of e-mails could reverse.
    """
    key = (settings.SECRET_KEY or '').encode()
    return hmac.new(key, (address or '').strip().lower().encode(), hashlib.sha256).hexdigest()[:24]


# ---------------------------------------------------------------------------
# What can be sent
# ---------------------------------------------------------------------------

def _sales_note(order):
    from .models import SalesNote

    return SalesNote.objects.filter(order=order).select_related('order', 'order__company').first()


def _fiscal_document(order):
    from .fiscal_services import original_fiscal_documents

    return original_fiscal_documents(order).order_by('-pk').first()


def _fiscal_refusal(document) -> str:
    """Why this receipt does not go out; '' when it may."""
    from .fiscal_void_services import is_voided
    from .models import FiscalEnvironment

    if document is None:
        return 'Esta venta no tiene comprobante electrónico.'
    if document.environment != FiscalEnvironment.PRODUCTION:
        return ('El comprobante es del entorno de pruebas de SUNAT: no tiene validez tributaria '
                'y no se envía fuera de la tienda.')
    if not document.is_accepted:
        return 'El comprobante todavía no fue aceptado por SUNAT.'
    if is_voided(document):
        return 'El comprobante fue dado de baja.'
    if document.document_type not in _FISCAL_NAMES:
        return 'Este tipo de comprobante no se envía desde aquí.'
    return ''


def _fiscal_filename(document) -> str:
    """The name `AdminFiscalDocumentPdfView` gives the same PDF."""
    import re

    return re.sub(
        r'[^A-Za-z0-9._-]', '',
        f'{document.issuer_tax_id}-{document.document_type}-{document.series}-{document.number}.pdf',
    )


def _entry(kind, label, *, attachment='', note='', refusal=''):
    return {
        'kind': kind,
        'label': label,
        'attachment_name': attachment if not refusal else '',
        'note': note,
        'available': not refusal,
        'unavailable_reason': refusal,
    }


def available_documents(order, *, may_sales_note: bool, may_fiscal: bool) -> list[dict]:
    """
    The documents of `order` a copy can carry, as the dialog lists them.

    A kind the caller may not download is LEFT OUT, not shown disabled: the
    screen does not offer what the server would refuse. A kind they may see but
    that does not exist for this sale stays, with the reason.
    """
    from .pdf_services import get_order_receipt_filename
    from .sales_note_services import get_sales_note_filename, is_void

    entries = [_entry(
        KIND_CONFIRMATION,
        f'Correo de confirmación del pedido #{order.pk}, con la constancia de pedido (PDF)',
        attachment=get_order_receipt_filename(order), note=_RECEIPT_NOTE,
    )]
    if may_sales_note:
        note = _sales_note(order)
        if note is None:
            entries.append(_entry(KIND_SALES_NOTE, 'Nota de venta interna (PDF)',
                                  refusal='Esta venta todavía no tiene nota de venta interna.'))
        else:
            entries.append(_entry(
                KIND_SALES_NOTE, f'Nota de venta interna {note.number} (PDF)',
                attachment=get_sales_note_filename(note), note=_SALES_NOTE_NOTE,
                refusal='La nota de venta interna está anulada.' if is_void(note) else '',
            ))
    if may_fiscal:
        document = _fiscal_document(order)
        refusal = _fiscal_refusal(document)
        if refusal:
            entries.append(_entry(KIND_FISCAL, 'Comprobante electrónico (PDF)', refusal=refusal))
        else:
            entries.append(_entry(
                KIND_FISCAL, f'{_FISCAL_NAMES[document.document_type]} {document.document_id} (PDF)',
                attachment=_fiscal_filename(document),
            ))
    return entries


def describe(order, kind: str, *, may_sales_note: bool, may_fiscal: bool) -> dict:
    """The entry for `kind`, or `CopyUnavailable` if it is not one that may go."""
    for entry in available_documents(order, may_sales_note=may_sales_note, may_fiscal=may_fiscal):
        if entry['kind'] == kind:
            if not entry['available']:
                raise CopyUnavailable(entry['unavailable_reason'])
            return entry
    raise CopyUnavailable('Ese documento no se puede enviar desde aquí.')


# ---------------------------------------------------------------------------
# The message
# ---------------------------------------------------------------------------

def _pdf(kind: str, order) -> bytes:
    """The PDF of `kind`, or `CopyPdfError`. Every failure is the same answer: nothing goes."""
    from . import pdf_services
    from .fiscal_pdf_services import generate_fiscal_pdf
    from .sales_note_services import generate_sales_note_pdf

    try:
        if kind == KIND_CONFIRMATION:
            return pdf_services.generate_order_receipt_pdf(order)
        if kind == KIND_SALES_NOTE:
            return generate_sales_note_pdf(_sales_note(order))
        return generate_fiscal_pdf(_fiscal_document(order))
    except Exception as exc:
        # The order id and the kind only: a traceback of a PDF builder can carry
        # the customer's name, and this line goes to the server log.
        logger.exception('Order %s: the %s PDF for a document copy could not be drawn', order.pk, kind)
        raise CopyPdfError(kind) from exc


def _document_title(label: str) -> str:
    """«Nota de venta interna NV-000012 (PDF)» → «Nota de venta interna NV-000012»."""
    return label.removesuffix(' (PDF)')


def _plain_bodies(*, store: str, title: str, order_id, internal: bool) -> tuple[str, str, str]:
    """(subject, text, html) for a company without the e-mail template. Everything escaped."""
    sender = store or 'La tienda'
    subject = f'{title} · Pedido N.º {order_id}' + (f' — {store}' if store else '')
    lines = [
        'Hola:',
        '',
        f'{sender} te envía una copia de: {title}, del pedido N.º {order_id}.',
        'El documento va adjunto en PDF.',
    ]
    if internal:
        lines += ['', _SALES_NOTE_NOTE]
    lines += ['', f'Recibes este correo porque el equipo de {sender} lo envió a esta dirección.']
    h = _html.escape
    notice = f'<p style="color:#6b7280;font-size:13px">{h(_SALES_NOTE_NOTE)}</p>' if internal else ''
    html_body = (
        '<!DOCTYPE html><html><head><meta charset="UTF-8"></head>'
        '<body style="font-family:Arial,sans-serif;color:#111827;max-width:600px;margin:0 auto;padding:24px">'
        f'<h1 style="font-size:20px;margin:0 0 12px">{h(title)}</h1>'
        f'<p>{h(sender)} te envía una copia de este documento del pedido N.º {h(str(order_id))}.</p>'
        '<p>El documento va adjunto en PDF.</p>'
        f'{notice}'
        f'<p style="color:#9ca3af;font-size:12px">Recibes este correo porque el equipo de {h(sender)} '
        'lo envió a esta dirección.</p>'
        '</body></html>'
    )
    return subject, '\n'.join(lines), html_body


def _copy_bodies(order, *, title: str, internal: bool) -> tuple[str, str, str]:
    """(subject, text, html) of a sales-note or receipt copy: the company's template, else the plain one."""
    from . import company_settings, mail

    store = (company_settings.order_identity(order).name or '').strip()
    company = getattr(order, 'company', None)
    try:
        if company is not None and mail.available(company):
            rendered = mail.render('document_copy', mail.builders.document_copy(
                store=store, document=title, order_id=order.pk, internal=internal,
            ), company=company)
            return rendered.subject, rendered.text, rendered.html
    except Exception:
        logger.exception('Order %s: the e-mail template could not be filled; sending the plain copy', order.pk)
    return _plain_bodies(store=store, title=title, order_id=order.pk, internal=internal)


def send_document_copy(order, kind: str, recipient: str, *, may_sales_note: bool, may_fiscal: bool) -> dict:
    """
    Send ONE copy of `kind` to `recipient` — already through `normalise_recipient`.

    Draws the PDF first: `CopyPdfError` means nothing was handed to the mail
    server. A failure of the mail server itself propagates (the view answers
    502). Writes nothing on the order: not the customer's address, not
    `confirmation_email_sent_at`, not `email_send_error` — those describe the
    customer's own mailbox, and this message did not go there.

    Returns the entry that was sent (`kind`, `label`, `attachment_name`).
    """
    from . import email_services
    from .models import Order

    if not order.paid or order.status != Order.Status.PAID:
        raise CopyUnavailable('Solo se pueden enviar documentos de ventas pagadas.')
    entry = describe(order, kind, may_sales_note=may_sales_note, may_fiscal=may_fiscal)
    pdf_bytes = _pdf(kind, order)

    if kind == KIND_CONFIRMATION:
        ctx = email_services.build_order_confirmation_context(order)
        subject, text_body, html_body = email_services._customer_bodies(order, ctx, receipt_attached=True)
    else:
        subject, text_body, html_body = _copy_bodies(
            order, title=_document_title(entry['label']), internal=kind == KIND_SALES_NOTE,
        )

    message = EmailMultiAlternatives(
        subject=subject, body=text_body, from_email=settings.DEFAULT_FROM_EMAIL, to=[recipient],
    )
    message.attach_alternative(html_body, 'text/html')
    message.attach(entry['attachment_name'], pdf_bytes, 'application/pdf')
    message.send(fail_silently=False)
    return entry
