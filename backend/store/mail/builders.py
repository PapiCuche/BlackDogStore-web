"""
One function per kind of message. Each returns the data of `ejemplos/*.json`.

A builder decides WHAT a message says; it does not know how it looks. It gets
plain values — names, links, amounts — and gives back the dict the template is
filled with. What it does not have, it leaves out: `contract.clean()` drops an
empty key, and a block without its key is not shown.

Nothing here names a shop. The name a message carries is the one it is given.
"""
from decimal import Decimal

from . import format as fmt


def _greeting(name: str) -> str:
    name = (name or '').strip()
    return f'Hola, {name}:' if name else 'Hola:'


def verify_email(*, brand: str, name: str, link: str) -> dict:
    """Somebody registered an account with this address. 01-confirmar-correo, by link."""
    return {
        'asunto': f'Confirma tu correo · {brand}',
        'preheader': 'Un paso más para activar tu cuenta y seguir tus pedidos y servicios.',
        'etiqueta': 'Cuenta web',
        'titulo': 'Confirma tu correo',
        'saludo': _greeting(name),
        'parrafos': [
            'Creaste una cuenta en nuestra web. Confirma que este correo es tuyo para que '
            'puedas seguir tus pedidos, garantías y servicios técnicos.',
            'El enlace vence en 24 horas. Si no creaste esta cuenta, ignora este correo.',
        ],
        'boton': {'texto': 'Confirmar mi correo', 'url': link, 'mostrar_enlace': True},
        'motivo': f'Recibes este correo porque se registró una cuenta con esta dirección en la web de {brand}.',
        'anio': fmt.year(),
    }


def password_reset(*, brand: str, name: str, link: str) -> dict:
    return {
        'asunto': f'Restablece tu contraseña · {brand}',
        'preheader': 'Usa este enlace para elegir una contraseña nueva. Vence en 1 hora.',
        'etiqueta': 'Cuenta web',
        'titulo': 'Restablece tu contraseña',
        'saludo': _greeting(name),
        'parrafos': [
            'Recibimos una solicitud para restablecer la contraseña de tu cuenta. '
            'Usa el botón para elegir una nueva.',
        ],
        'aviso': {
            'etiqueta': 'Vigencia',
            'texto': 'El enlace vence en 1 hora y sirve una sola vez. Si no lo pediste, ignora '
                     'este correo: tu contraseña no cambiará.',
        },
        'boton': {'texto': 'Elegir nueva contraseña', 'url': link, 'mostrar_enlace': True},
        'motivo': f'Recibes este correo porque se pidió restablecer la contraseña de una cuenta de {brand} con esta dirección.',
        'anio': fmt.year(),
    }


def staff_invitation(*, company: str, first_name: str, role: str, area: str, expires_at, link: str) -> dict:
    """A company invites somebody to work in it. The link is the invitation."""
    expires = fmt.date(expires_at)
    return {
        'asunto': f'Invitación para unirte a {company}',
        'preheader': f'Te invitaron al equipo de {company}. Configura tu acceso antes del {expires}.',
        'etiqueta': 'Equipo',
        'titulo': f'Te invitaron al equipo de {company}',
        'saludo': _greeting(first_name),
        'parrafos': [
            'Abre el enlace para crear tu cuenta o iniciar sesión con la que ya tengas. '
            'La contraseña la eliges tú: nadie más la ve ni la decide.',
        ],
        'datos': {
            'titulo': 'Tu invitación',
            'campos': [
                {'nombre': 'Empresa', 'valor': company},
                {'nombre': 'Rol', 'valor': role},
                {'nombre': 'Área', 'valor': area},
                {'nombre': 'Vence', 'valor': expires},
            ],
        },
        'aviso': {
            'etiqueta': 'Vigencia',
            'texto': f'El enlace caduca el {expires}. Si no esperabas este correo, puedes ignorarlo.',
        },
        'boton': {'texto': 'Configurar mi acceso', 'url': link, 'mostrar_enlace': True},
        'motivo': f'Recibes este correo porque {company} te invitó a formar parte de su equipo.',
        'anio': fmt.year(),
    }


def _order_lines(ctx: dict) -> list:
    return [
        {
            'nombre': item['product_name'],
            'nota': f'Cant. {item["quantity"]} · {fmt.money(item["price"])} c/u',
            'valor': fmt.money(item['subtotal']),
        }
        for item in ctx['items']
    ]


def _order_summary(ctx: dict) -> list:
    rows = []
    if ctx['discount_amount'] and Decimal(str(ctx['discount_amount'])) > 0:
        coupon = f' ({ctx["coupon_code"]})' if ctx.get('coupon_code') else ''
        rows.append({'nombre': f'Descuento{coupon}', 'valor': fmt.money(-Decimal(str(ctx['discount_amount'])))})
    rows.append({'nombre': 'Entrega', 'valor': ctx['delivery_label']})
    rows.append({'nombre': 'Comprobante', 'valor': ctx['receipt_label']})
    return rows


def _document(ctx: dict) -> str:
    return ' '.join(part for part in (ctx['document_label'], ctx['document_number']) if part)


def order_confirmation(ctx: dict, *, orders_url: str, receipt_attached: bool) -> dict:
    """
    The buyer's confirmation, from `email_services.build_order_confirmation_context`.

    02-confirmacion-compra. `ctx` is plain data and carries no gateway
    identifier; this adds none.
    """
    store = ctx['store_name']
    pickup = ' — '.join(part for part in (ctx['pickup_name'], ctx['pickup_address']) if part)
    paragraphs = ['Tu pago está confirmado. Nuestro equipo se comunicará contigo para coordinar la entrega.']
    if receipt_attached:
        paragraphs.append('Adjuntamos el resumen de tu pedido en PDF.')
    return {
        'asunto': f'Recibimos tu pedido N.º {ctx["order_id"]}',
        'preheader': 'Tu compra está confirmada. Aquí tienes el detalle de tu pedido.',
        'etiqueta': f'Pedido · N.º {ctx["order_id"]}',
        'titulo': 'Tu compra está confirmada',
        'saludo': _greeting(ctx['customer_name']),
        'parrafos': paragraphs,
        'detalle': {
            'titulo': 'Detalle del pedido',
            'items': _order_lines(ctx),
            'resumen': _order_summary(ctx),
            'total': {'nombre': 'Total pagado', 'valor': fmt.money(ctx['total'])},
        },
        'datos': {
            'titulo': 'Entrega',
            'campos': [
                {'nombre': 'Método', 'valor': ctx['delivery_label']},
                {'nombre': 'Documento', 'valor': _document(ctx)},
            ],
            'destacados': [
                {'nombre': 'Dirección', 'valor': ctx['full_address']},
                {'nombre': 'Referencia', 'valor': ctx['reference'] if ctx['full_address'] else ''},
                {'nombre': 'Punto de retiro', 'valor': '' if ctx['full_address'] else pickup},
                {'nombre': 'Tus notas', 'valor': ctx['notes']},
            ],
        },
        'aviso': {
            'etiqueta': 'Garantía',
            'texto': ' '.join(part for part in (ctx['warranty_note'], ctx['warranty_url']) if part),
        },
        'boton': {'texto': 'Ver mis pedidos', 'url': orders_url},
        'motivo': f'Recibes este correo porque realizaste una compra en {store}.',
        'anio': fmt.year(),
    }


def internal_order(ctx: dict, *, admin_url: str) -> dict:
    """The shop's own notice of a paid order. It goes to the shop, not to the buyer."""
    return {
        'asunto': f'Nueva venta pagada · Pedido N.º {ctx["order_id"]}',
        'preheader': f'Pedido N.º {ctx["order_id"]} pagado. Cliente, entrega y productos, en este correo.',
        'etiqueta': f'Venta · N.º {ctx["order_id"]}',
        'titulo': 'Nueva venta pagada',
        'parrafos': ['Se confirmó el pago de un pedido de la tienda en línea.'],
        'detalle': {
            'titulo': 'Productos',
            'items': _order_lines(ctx),
            'resumen': _order_summary(ctx),
            'total': {'nombre': 'Total', 'valor': fmt.money(ctx['total'])},
        },
        'datos': {
            'titulo': 'Cliente y entrega',
            'campos': [
                {'nombre': 'Cliente', 'valor': ctx['customer_name']},
                {'nombre': 'Teléfono', 'valor': ctx['customer_phone']},
                {'nombre': 'Documento', 'valor': _document(ctx)},
                {'nombre': 'Entrega', 'valor': ctx['delivery_label']},
            ],
            'destacados': [
                {'nombre': 'Correo', 'valor': ctx['customer_email']},
                {'nombre': 'Dirección', 'valor': ctx['full_address']},
                {'nombre': 'Referencia', 'valor': ctx['reference'] if ctx['full_address'] else ''},
                {'nombre': 'Notas del cliente', 'valor': ctx['notes']},
            ],
        },
        'boton': {'texto': 'Abrir en el panel', 'url': admin_url} if admin_url else None,
        'motivo': f'Recibes este correo porque esta dirección recibe los avisos de pedidos de {ctx["store_name"]}.',
        'anio': fmt.year(),
    }


# --- notices -----------------------------------------------------------------
#
# A notice is «something happened, go and look». Its words were written where
# the event was emitted, from material that is safe to send; this adds a label,
# where to look, and — for the customer — which step that is. It reads nothing
# of the order or the repair: the detail stays behind its own authorisation.

# (audience, what it is about) → (label, button, where the detail lives)
_WHERE = {
    ('customer', 'repair_order'): ('Servicio técnico', 'Ver mi equipo', '/repairs'),
    ('customer', 'order'): ('Pedido · N.º {id}', 'Ver mi pedido', '/orders'),
    ('internal', 'repair_order'): ('Servicio técnico', 'Abrir en el panel', '/admin/service/orders/{id}'),
    ('internal', 'order'): ('Pedido · N.º {id}', 'Abrir en el panel', '/admin/orders'),
    ('internal', 'announcement'): ('Comunicado', 'Leer el comunicado', '/admin/communications/{id}'),
}

# event → the steps a customer sees, and which one this is. Only steps that are
# true whatever happened before: a device can be ready without a repair.
_STEPS = {
    'service.quote.available': (('Equipo recibido', 'Cotización por revisar'), 1),
    'service.ready_for_pickup': (('Equipo recibido', 'Listo para recoger', 'Entregado'), 1),
    'commerce.fulfillment.ready': (('Pedido recibido', 'Listo para recoger', 'Entregado'), 1),
    'commerce.fulfillment.shipped': (('Pedido recibido', 'Enviado', 'Entregado'), 1),
}

_ABOUT = {
    'repair_order': 'tienes un servicio técnico en {company}',
    'order': 'tienes un pedido en {company}',
}

# What is said instead of «go and look» to somebody who has nowhere to look.
_IT_IS = {
    'repair_order': 'Es un aviso sobre tu servicio técnico.',
    'order': 'Es un aviso sobre tu pedido.',
}


def _preheader(text: str, filler: str) -> str:
    """The line beside the subject in an inbox: 40 to 90 characters."""
    text = ' '.join(text.split())
    if len(text) < 40:
        text = f'{text} {filler}'.strip()
    return text if len(text) <= 90 else text[:89].rstrip() + '…'


def _progress(event_type: str):
    steps, current = _STEPS.get(event_type, ((), 0))
    if not steps:
        return None
    return {
        'titulo': 'Estado',
        'pasos': [
            {'texto': text, 'hecho' if position < current else 'actual' if position == current else 'pendiente': True}
            for position, text in enumerate(steps)
        ],
    }


def notification(*, company: str, title: str, body: str, audience: str, event_type: str,
                 target_type: str, target_id, site: str, has_account: bool) -> dict:
    """
    One notice of `notification_services`, for one recipient. 10-aviso.

    `has_account` is whether the customer can open the page the button leads
    to: those pages list what belongs to an account, and somebody who left a
    device at the counter or bought without registering has none. They get the
    notice without a button, not a button to a login screen and an empty list.
    """
    customer = audience == 'customer'
    label, button, path = _WHERE.get((audience, target_type), ('', '', ''))
    mapped = bool(path) and target_id is not None
    can_open = mapped and bool(site) and (has_account or not customer)
    body = (body or '').strip()
    if customer:
        reason = _ABOUT.get(target_type, 'eres cliente de {company}').format(company=company)
        look = 'Entra a tu cuenta para ver el detalle.' if can_open else _IT_IS.get(target_type, f'Es un aviso de {company}.')
    else:
        reason = f'formas parte del equipo de {company}'
        look = 'Entra al panel para ver el detalle.'
    return {
        'asunto': f'{title} · {company}',
        'preheader': _preheader(body or title, look),
        'etiqueta': label.format(id=target_id) if mapped else '',
        'titulo': title,
        'parrafos': [body or look],
        'progreso': _progress(event_type) if customer and mapped else None,
        'boton': {'texto': button, 'url': f'{site.rstrip("/")}{path.format(id=target_id)}'} if can_open else None,
        'motivo': f'Recibes este correo porque {reason}.',
        'anio': fmt.year(),
    }


def smtp_test(*, brand: str) -> dict:
    """What a MASTER sends to see the mail server works. Nothing to press. 11-prueba-de-correo."""
    return {
        'asunto': f'Prueba de correo · {brand}',
        'preheader': 'Este mensaje comprueba que la tienda puede enviar correo.',
        'etiqueta': 'Configuración',
        'titulo': 'Prueba de correo',
        'parrafos': [
            'Este mensaje comprueba que la plataforma puede enviar correo con esta configuración.',
            'No contiene ningún enlace de acceso ni requiere ninguna acción.',
        ],
        'aviso': {'etiqueta': 'Así se ven', 'texto': 'Los correos de la tienda llevan este mismo diseño.'},
        'motivo': f'Recibes este correo porque alguien que administra {brand} pidió una prueba a esta dirección.',
        'anio': fmt.year(),
    }
