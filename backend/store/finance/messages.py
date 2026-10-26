"""
FIN-F — FINANCE-MANUAL-REMINDERS-01: the text of a manual collection / payment message.
`docs/adr-finance-obligations.md` §7.3–§7.5.

The SERVER writes the message from the obligation's current rows; a person copies it. Nothing in this module
sends anything: no WhatsApp, Meta, SMS, e-mail or push, no outbound HTTP, no `wa.me` / `mailto:` link. It also
never writes an economic record. The only closed list of data a message may carry is `VARIABLES`.

Which templates an obligation is offered is decided by `AUDIENCES`, keyed by (direction, origin). The supplier
texts (`SUPPLIER`) are kept here ready, but no audience points at them until the supplier-document origin
(FIN-D) exists: adding `('payable', Obligation.ORIGIN_SUPPLIER_DOCUMENT): SUPPLIER` is all that enables them.
"""
from __future__ import annotations

import re

from ..models import Obligation
from ..tax_services import currency_symbol
from . import services
from .services import FinanceError

#: The closed list of variables a message may use (§7.3). Anything else is refused.
VARIABLES = (
    'empresa', 'cliente', 'proveedor', 'beneficiario', 'referencia', 'saldo', 'importe_pagado', 'vencimiento',
    'concepto', 'estado',
)
#: Without these a message is blocked with the reason; any other variable without data loses its sentence.
REQUIRED = ('saldo', 'referencia')
MAX_LENGTH = 1000

GUIDANCE = 'Contacta sólo al titular, en días hábiles de 07:00 a 20:00.'

# ---------------------------------------------------------------------------
# The owner's wording (literal; docs §7.5)
# ---------------------------------------------------------------------------

DEFAULT_BODIES = {
    'receivable.friendly': (
        'Hola, {{cliente}}. Te saludamos de {{empresa}}.\n\n'
        'Queremos recordarte que registramos un saldo pendiente de {{saldo}} correspondiente a {{referencia}}.\n\n'
        'Si ya realizaste el pago, por favor comunícanoslo para verificarlo y actualizar nuestros registros.\n\n'
        'Si necesitas alguna información adicional, estaremos encantados de ayudarte.\n\n'
        'Muchas gracias.'
    ),
    'receivable.upcoming': (
        'Hola, {{cliente}}.\n\n'
        'Te recordamos que el pago de {{saldo}}, correspondiente a {{referencia}}, tiene como fecha de '
        'vencimiento el {{vencimiento}}.\n\n'
        'Si ya realizaste el pago, puedes comunicárnoslo para verificarlo.\n\n'
        'Gracias por tu atención.\n\n'
        '{{empresa}}'
    ),
    'receivable.overdue': (
        'Hola, {{cliente}}. Buen día.\n\n'
        'Nos comunicamos de {{empresa}} para informarte que, según nuestros registros, permanece pendiente un '
        'saldo de {{saldo}} correspondiente a {{referencia}}, cuyo vencimiento fue el {{vencimiento}}.\n\n'
        'Agradeceremos que nos confirmes si ya efectuaste el pago o si necesitas coordinarlo con nosotros.\n\n'
        'Muchas gracias.'
    ),
    'receivable.partial': (
        'Hola, {{cliente}}.\n\n'
        'Hemos registrado un abono a tu cuenta correspondiente a {{referencia}}.\n\n'
        'Actualmente, el saldo pendiente es de {{saldo}}.\n\n'
        'Gracias por tu pago. Si ya completaste el importe restante, comunícanoslo para verificarlo.\n\n'
        '{{empresa}}'
    ),
    # Free text with the authorised data; the person edits it before copying.
    'receivable.custom': (
        'Hola, {{cliente}}. Te escribimos de {{empresa}} sobre {{referencia}}, con un saldo pendiente de '
        '{{saldo}}.'
    ),
    # Suppliers / creditors. Stored, not offered yet (see the module docstring).
    'payable.statement': (
        'Buen día, {{proveedor}}.\n\n'
        'Te saludamos de {{empresa}}.\n\n'
        'Estamos revisando nuestras obligaciones pendientes y agradeceremos que nos confirmes el estado de '
        'cuenta correspondiente a {{referencia}}.\n\n'
        'Según nuestros registros, el importe pendiente es de {{saldo}}.\n\n'
        'Gracias por tu apoyo.'
    ),
    'payable.coordinate': (
        'Buen día, {{proveedor}}.\n\n'
        'Te contactamos de {{empresa}} para coordinar el pago pendiente de {{saldo}}, relacionado con '
        '{{referencia}}.\n\n'
        'Agradeceremos que nos confirmes los datos necesarios para completar la operación.\n\n'
        'Muchas gracias.'
    ),
    # Only with a payment CONFIRMED in the system.
    'payable.paid': (
        'Buen día, {{proveedor}}.\n\n'
        'Te informamos que hemos registrado un pago por {{importe_pagado}} correspondiente a {{referencia}}.\n\n'
        'Agradeceremos confirmar su recepción.\n\n'
        'Saludos,\n'
        '{{empresa}}'
    ),
    # Fixed payments: the internal reminder, and the editable version to coordinate with the provider.
    'fixed.internal': (
        'Recordatorio de pago\n\n'
        'Concepto: {{concepto}}\n'
        'Beneficiario: {{beneficiario}}\n'
        'Importe pendiente: {{saldo}}\n'
        'Vencimiento: {{vencimiento}}\n'
        'Estado: {{estado}}\n'
        'Empresa: {{empresa}}'
    ),
}
# Same wording as the owner's supplier template B, addressed to the beneficiary of the fixed payment.
DEFAULT_BODIES['fixed.coordinate'] = DEFAULT_BODIES['payable.coordinate'].replace('{{proveedor}}', '{{beneficiario}}')

LABELS = {
    'receivable.friendly': 'Recordatorio amable',
    'receivable.upcoming': 'Próximo vencimiento',
    'receivable.overdue': 'Pago vencido',
    'receivable.partial': 'Pago parcial',
    'receivable.custom': 'Mensaje personalizado',
    'payable.statement': 'Solicitar estado de cuenta',
    'payable.coordinate': 'Coordinar pago',
    'payable.paid': 'Informar pago realizado',
    'fixed.internal': 'Recordatorio interno de pago',
    'fixed.coordinate': 'Coordinar el pago con el proveedor',
}

CUSTOMER = ('receivable.friendly', 'receivable.upcoming', 'receivable.overdue', 'receivable.partial',
            'receivable.custom')
FIXED = ('fixed.internal', 'fixed.coordinate')
SUPPLIER = ('payable.statement', 'payable.coordinate', 'payable.paid')

#: (direction, origin) -> the templates that obligation is offered, in the order they are listed.
AUDIENCES = {
    ('receivable', Obligation.ORIGIN_REPAIR): CUSTOMER,
    ('receivable', Obligation.ORIGIN_MANUAL): CUSTOMER,
    ('payable', Obligation.ORIGIN_FIXED): FIXED,
}
#: Offered templates that may be prepared while the obligation is disputed (they are internal).
INTERNAL = ('fixed.internal',)


def enabled_keys() -> tuple:
    seen = []
    for keys in AUDIENCES.values():
        for key in keys:
            if key not in seen:
                seen.append(key)
    return tuple(seen)


def audience(key: str) -> str:
    return 'customer' if key in CUSTOMER else 'fixed' if key in FIXED else 'supplier'


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def money(value, currency: str) -> str:
    """`S/ 1,234.50`: the obligation's own currency, never converted."""
    from decimal import Decimal

    return f'{currency_symbol(currency)} {Decimal(value).quantize(services.CENT):,.2f}'


def local_date(value) -> str:
    return value.strftime('%d/%m/%Y')


STATE_LABELS = {
    services.STATE_PENDING: 'Pendiente', services.STATE_DUE_SOON: 'Por vencer', services.STATE_DUE_TODAY: 'Vence hoy',
    services.STATE_OVERDUE: 'Vencido', services.STATE_PAID: 'Pagado', services.STATE_VOIDED: 'Anulado',
    services.STATE_PAUSED: 'En pausa', services.STATE_ORIGIN_CANCELLED: 'Cancelado',
    services.STATE_QUOTE_REVIEW: 'En revisión',
}


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

_PLACEHOLDER = re.compile(r'\{\{\s*([^{}]*?)\s*\}\}')
_SENTENCE_BREAK = re.compile(r'(?<=[.!?])\s+')
_CONTROL = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')
#: Anything that could send the reader elsewhere or carry an address: a scheme, `www.`, `wa.me`, an e-mail or a
#: bare domain. A message is plain text for a person to copy.
_LINK = re.compile(
    r'(?i)(\b[a-z][a-z0-9+.\-]*://|\bwww\.|\bwa\.me\b|\b(?:mailto|sms|tel|whatsapp):|[\w.+\-]+@[\w\-]+\.[\w.\-]+'
    r'|\b[\w\-]+\.(?:com|pe|net|org|io|me|ly|co|app|info|edu|gob)\b)')


def unknown_variables(text: str) -> list:
    return [name for name in _PLACEHOLDER.findall(text or '') if name not in VARIABLES]


def check(text) -> list:
    """
    Validate a template body or an edited text and return the variable names it uses. Refuses what is not
    text, an empty text, one over `MAX_LENGTH`, a link or address, a variable outside the closed list and a
    stray brace. Raises `FinanceError` (a 400).
    """
    if not isinstance(text, str):
        raise FinanceError('Se esperaba texto.')
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    if not text.strip():
        raise FinanceError('El mensaje no puede estar vacío.')
    if len(text) > MAX_LENGTH:
        raise FinanceError(f'El mensaje admite hasta {MAX_LENGTH} caracteres.')
    if _CONTROL.search(text):
        raise FinanceError('El mensaje tiene caracteres no permitidos.')
    if _LINK.search(text):
        raise FinanceError('El mensaje no puede llevar enlaces, correos ni direcciones web.')
    names = _PLACEHOLDER.findall(text)
    bad = sorted({name for name in names if name not in VARIABLES})
    if bad:
        shown = ', '.join('«%s»' % (name or ' ') for name in bad)
        raise FinanceError(f'Variable no permitida: {shown}. Permitidas: {", ".join(VARIABLES)}.')
    rest = _PLACEHOLDER.sub('', text)
    if '{{' in rest or '}}' in rest:
        raise FinanceError('Hay una variable mal escrita: usa {{nombre}} con sus dos pares de llaves.')
    return names


def _value(raw) -> str:
    """Data is substituted as one line of inert text: no braces, no line breaks."""
    return ' '.join(str(raw).replace('{', '').replace('}', '').split())


def render(text, facts: dict) -> str:
    """
    The final text: `text` (a template body, or an edited message) with its variables filled from `facts`
    ({variable: text or None}), which only the server builds. A required variable without data raises; any
    other loses the sentence (or line) that holds it. No `{{…}}` ever remains.
    """
    names = check(text)
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    for name in dict.fromkeys(names):
        if name in REQUIRED and not facts.get(name):
            raise FinanceError(f'No se puede preparar el mensaje: falta el dato «{name}».')
    missing = {name for name in names if not facts.get(name)}
    if missing:
        lines = []
        for line in text.split('\n'):
            if any(m.group(1) in missing for m in _PLACEHOLDER.finditer(line)):
                kept = [s for s in _SENTENCE_BREAK.split(line)
                        if not any(m.group(1) in missing for m in _PLACEHOLDER.finditer(s))]
                if not kept:
                    continue
                line = ' '.join(kept)
            lines.append(line)
        text = re.sub(r'\n{3,}', '\n\n', '\n'.join(lines)).strip()
    out = _PLACEHOLDER.sub(lambda m: _value(facts[m.group(1)]), text).strip()
    if not out:
        raise FinanceError('El mensaje quedó vacío: faltan datos de la obligación.')
    return out


# ---------------------------------------------------------------------------
# What an obligation is offered
# ---------------------------------------------------------------------------

def _counterparty(obligation):
    if obligation.supplier_id:
        return obligation.supplier.name
    return obligation.payee_name or None


def facts_for(obligation, company, numbers: dict, state_code: str) -> dict:
    """The data a message may carry, read from the obligation NOW. Strings or None; never client input."""
    currency = numbers['currency']
    receivable = obligation.direction == 'receivable'
    beneficiary = None if receivable else _counterparty(obligation)
    label = STATE_LABELS.get(state_code, state_code)
    if numbers['partially_paid'] and state_code in (services.STATE_PENDING, services.STATE_DUE_SOON,
                                                    services.STATE_DUE_TODAY, services.STATE_OVERDUE):
        label = f'{label} (pago parcial)'
    return {
        'empresa': company.name or None,
        'cliente': obligation.customer.display_name if receivable and obligation.customer_id else None,
        'proveedor': beneficiary,
        'beneficiario': beneficiary,
        'referencia': obligation.reference or None,
        'saldo': money(numbers['balance'], currency) if numbers['balance'] > 0 else None,
        'importe_pagado': money(numbers['paid'], currency) if numbers['paid'] > 0 else None,
        'vencimiento': local_date(obligation.due_date) if obligation.due_date else None,
        'concepto': obligation.concept or None,
        'estado': label,
    }


def window(obligation, numbers: dict, state_code: str, facts: dict) -> dict:
    """What the preparation window shows: the facts plus the original amount, what was paid and the state."""
    currency = numbers['currency']
    return {
        **facts,
        'original': money(numbers['effective'], currency),
        'paid': money(numbers['paid'], currency),
        'balance': money(numbers['balance'], currency),
        'currency': currency, 'state': state_code, 'disputed': obligation.is_disputed,
        'direction': obligation.direction,
    }


def plan(obligation, company, *, today) -> dict:
    """
    {'templates': [keys], 'blocked': reason or None, 'suggested': key or None, 'warnings': [...],
     'facts', 'data'} — everything decided from the obligation's current rows.
    """
    numbers = services.figures(obligation)
    state_code = services.state(obligation, today=today, figures_=numbers)
    facts = facts_for(obligation, company, numbers, state_code)
    result = {'templates': [], 'blocked': None, 'suggested': None, 'warnings': [], 'facts': facts,
              'data': window(obligation, numbers, state_code, facts)}
    offered = AUDIENCES.get((obligation.direction, obligation.origin_type))
    blocked = None
    if offered is None:
        blocked = 'Todavía no hay plantillas de mensaje para este tipo de obligación.'
    elif state_code == services.STATE_VOIDED:
        blocked = 'Esta obligación está anulada.'
    elif state_code == services.STATE_ORIGIN_CANCELLED:
        blocked = 'La reparación fue cancelada: no hay nada que cobrar.'
    elif state_code == services.STATE_PAID:
        blocked = 'Esta cuenta ya está pagada.'
    elif state_code == services.STATE_QUOTE_REVIEW:
        blocked = 'La cotización de la reparación se está rehaciendo: el saldo todavía no es definitivo.'
    elif state_code == services.STATE_PAUSED:
        blocked = 'Este período está en pausa: todavía no se debe.'
    if blocked is None and obligation.is_disputed:
        result['warnings'].append('Esta obligación está en disputa.')
        offered = tuple(key for key in offered if key in INTERNAL)
        if not offered:
            blocked = 'Esta cuenta está en disputa: no se preparan mensajes de cobro.'
    if blocked:
        result['blocked'] = blocked
        return result
    applicable = {
        'receivable.friendly': True,
        'receivable.upcoming': (obligation.due_date is not None
                                and state_code in (services.STATE_PENDING, services.STATE_DUE_SOON,
                                                   services.STATE_DUE_TODAY)),
        'receivable.overdue': state_code == services.STATE_OVERDUE,
        'receivable.partial': bool(numbers['partially_paid']),
        'receivable.custom': True,
        'fixed.internal': True,
        'fixed.coordinate': True,
    }
    result['templates'] = [key for key in offered if applicable.get(key)]
    for key in ('receivable.partial', 'receivable.overdue', 'receivable.upcoming', 'receivable.friendly'):
        if key in result['templates']:
            result['suggested'] = key
            break
    if result['suggested'] is None and result['templates']:
        result['suggested'] = result['templates'][0]
    return result


def body_for(company, key: str):
    """(body, customized): the company's own wording of `key` if it saved one, else the built-in default."""
    from ..models import FinanceMessageTemplate

    row = FinanceMessageTemplate.objects.filter(company=company, key=key).first()
    return (row.body, True) if row is not None else (DEFAULT_BODIES[key], False)
