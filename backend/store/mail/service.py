"""
Render and send: the one place a templated e-mail leaves from.

    mail.send(kind, recipient, data, company=company)

`kind` names what the message is (it is what a log line and the preview say);
`data` is what a builder returned. The same data gives the HTML and the plain
text, so the two cannot disagree.

A rendered body is never logged: several kinds carry a link that IS the access.
"""
import logging
from dataclasses import dataclass

import chevron
from django.conf import settings
from django.core.mail import EmailMultiAlternatives

from . import contract
from .skin import skin_for
from .text import render_text

logger = logging.getLogger(__name__)

# Every kind of message this code sends. A kind that is not here is a typo.
KINDS = frozenset({
    'verify_email', 'password_reset', 'staff_invitation',
    'order_confirmation', 'internal_order',
})
# Kinds nobody asked for by doing something: they must say how to stop them.
MARKETING_KINDS = frozenset()


@dataclass(frozen=True)
class Rendered:
    subject: str
    text: str
    html: str


def available(company=None) -> bool:
    """Whether `company`'s e-mail (or the platform's, with None) wears the template."""
    return skin_for(company) is not None


def brand(company=None) -> str:
    """The name the template's owner signs with. '' when there is no template."""
    skin = skin_for(company)
    return skin.name if skin is not None else ''


def render(kind: str, data: dict, *, company=None) -> Rendered:
    if kind not in KINDS:
        raise ValueError(f'tipo de correo desconocido: {kind}')
    skin = skin_for(company)
    if skin is None:
        raise LookupError('no hay plantilla para esta empresa: comprueba mail.available() antes')
    if kind in MARKETING_KINDS and not contract.clean(data).get('baja_url'):
        raise contract.ContractError('una promoción o invitación lleva baja_url')
    return render_with(skin, data)


def render_with(skin, data: dict) -> Rendered:
    """Fill `skin` with `data`. What `render` does once it knows whose template it is."""
    data = contract.validate(contract.clean(data))
    return Rendered(
        subject=data['asunto'],
        text=render_text(data, skin),
        html=chevron.render(skin.template, data),
    )


def deliver(rendered: Rendered, recipient: str, *, attachments=(), from_email=None) -> bool:
    """
    Hand a rendered message to the mail server. Raises if the server does not take it.

    Separate from `render` on purpose. «It could not be filled» has an answer —
    send the plain one. «It could not be sent» does not: the plain one goes to
    the same server, and a server that took the message and then failed to say
    so has already delivered it. Whoever falls back does it around `render`.

    `attachments` is a sequence of `(filename, content, mimetype)`.
    """
    message = EmailMultiAlternatives(
        subject=rendered.subject, body=rendered.text,
        from_email=from_email or settings.DEFAULT_FROM_EMAIL, to=[recipient],
    )
    message.attach_alternative(rendered.html, 'text/html')
    for filename, content, mimetype in attachments:
        message.attach(filename, content, mimetype)
    message.send(fail_silently=False)
    return True


def send(kind: str, recipient: str, data: dict, *, company=None, attachments=(), from_email=None) -> bool:
    """
    Render and deliver. Raises if it cannot be built or sent: each caller already
    decides what a failed e-mail means for what it was doing.
    """
    return deliver(render(kind, data, company=company), recipient, attachments=attachments, from_email=from_email)
