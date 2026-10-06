#!/usr/bin/env python3
"""
Ensayo de producción — lo que la tienda hace, a través de Caddy.

Lo llama `deploy/rehearsal.sh`. Como `rehearsal_media.py`, sólo usa la biblioteca
estándar y habla con la tienda por el mismo camino que un navegador.

QUÉ DEMUESTRA
-------------
  · límites   ninguna ruta acepta un cuerpo sin tope; las que reciben archivos
              admiten lo que su pantalla acepta, y no más;
  · pagos     la notificación de la pasarela configurada no acepta nada sin firma;
              la del otro producto de Izipay no existe; sin credenciales el pago no
              se abre y nada queda pagado;
  · equipos   un producto con serie: registrar un equipo sube el stock y el tablero
              en uno; venderlo lo deja «vendido», baja el stock en uno y su serie e
              IMEI salen en la nota de venta; no se puede vender dos veces;
  · seguimiento  el enlace de una reparación abre sin sesión, enmascara el IMEI y
              alterado no dice nada;
  · WhatsApp y Google apagados no rompen nada y lo dicen.

Uso:
    rehearsal_flows.py run       recorre todo y escribe el estado
    rehearsal_flows.py verify    vuelve a comprobar lo que debe sobrevivir a un
                                 reinicio, una reconstrucción o una restauración
    rehearsal_flows.py mail      registro, verificación y recuperación de contraseña
                                 por SMTP, contra el servidor de correo del ensayo
    rehearsal_flows.py mail-down la tienda con ese servidor caído o mal configurado
    rehearsal_flows.py console   un MASTER pasa el correo a Configuración › Integraciones,
                                 lo cambia, lo apaga y lo devuelve al entorno, sin
                                 reiniciar nada; nadie más puede, y nada se filtra
    rehearsal_flows.py logs <archivo>
                                 busca en los registros de la tienda lo que el
                                 recorrido usó y no debe quedar escrito

Variables: las de `rehearsal_media.py`. El estado va a `<R_STATE>.flows`.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import socket
import sys
import time
import uuid
import zlib

from rehearsal_media import ADDR, CTX, DOMAIN, PORT, SLUG, STATE, Client, check, png, rows_of
import rehearsal_media

FLOWS_STATE = STATE + '.flows'
KB = 1024
MB = 1024 * KB
INTERNAL = f'/api/v1/internal/{SLUG}'


def must(client, method, path, payload=None, expected=(200, 201)):
    res, body = client.json(method, path, payload)
    if res.status not in expected:
        raise RuntimeError(f'{method} {path} → {res.status} {str(body)[:200]}')
    return body


def imei(seed: int) -> str:
    """Un IMEI con su dígito de control. No es el de ningún equipo."""
    body = f'35{seed:012d}'[-14:]
    total = 0
    for index, char in enumerate(reversed(body)):
        digit = int(char)
        if index % 2 == 0:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return body + str((10 - total % 10) % 10)


def noise_png(edge: int) -> bytes:
    """Un PNG RGBA válido que pesa lo que mide: sin comprimir, `edge`² × 4 bytes."""
    import struct

    row = b'\x00' + os.urandom(edge * 4)
    raw = row * edge

    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))

    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', edge, edge, 8, 6, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(raw, 0)) + chunk(b'IEND', b''))


def declared(path, length, sent=64 * KB, content_type='application/json', cookies=None):
    """
    Anuncia un cuerpo de `length` bytes y envía sólo `sent`. Devuelve el estado
    con el que la tienda contesta SIN haber recibido el resto, o 0 si se queda
    esperándolo.

    Quien rechaza un cuerpo cierra la conexión con datos sin leer, y el sistema
    puede entonces descartar la respuesta antes de que esta prueba la lea. Eso
    es una carrera de la red, no una respuesta: se vuelve a preguntar. Quedarse
    esperando (20 s sin contestar) sí es una respuesta, y no se repite.
    """
    head = (f'POST {path} HTTP/1.1\r\nHost: {DOMAIN}\r\nOrigin: https://{DOMAIN}\r\n'
            f'Content-Type: {content_type}\r\nContent-Length: {length}\r\nConnection: close\r\n')
    if cookies:
        head += 'Cookie: ' + '; '.join(f'{k}={v}' for k, v in cookies.items()) + '\r\n'
        if 'csrftoken' in cookies:
            head += f"X-CSRFToken: {cookies['csrftoken']}\r\nReferer: https://{DOMAIN}/\r\n"
    for _attempt in range(4):
        sock = CTX.wrap_socket(socket.create_connection((ADDR, PORT), 20), server_hostname=DOMAIN)
        sock.settimeout(20)
        try:
            try:
                sock.sendall(head.encode() + b'\r\n' + b'x' * min(sent, length))
            except (BrokenPipeError, ConnectionResetError):
                pass  # contestó y cerró antes de que terminara de enviar: se lee lo que dijo
            line = sock.recv(64).split(b'\r\n', 1)[0].split()
            if len(line) > 1:
                return int(line[1])
        except socket.timeout:
            return 0
        except (OSError, ValueError):
            pass
        finally:
            sock.close()
        time.sleep(0.5)
    return 0


def pdf_text(pdf: bytes) -> str:
    """El texto de un PDF de reportlab: Flate + ASCII85 y los operadores de texto."""
    out = []
    for body in re.findall(rb'[^d]stream\r?\n(.*?)endstream', pdf, re.S):
        body = body.strip(b'\r\n')
        if body.endswith(b'~>'):
            try:
                body = base64.a85decode(body[:-2])
            except ValueError:
                continue
        try:
            body = zlib.decompress(body)
        except zlib.error:
            pass
        if b'BT' not in body:
            continue
        for piece in re.findall(rb'\((?:\\.|[^\\()])*\)', body):
            text = re.sub(rb'\\([0-7]{1,3})', lambda m: bytes([int(m.group(1), 8) & 0xFF]), piece[1:-1])
            out.append(text.replace(b'\\(', b'(').replace(b'\\)', b')').decode('latin-1').replace('\xa0', ' '))
    return ' '.join(out)


# --- límites ------------------------------------------------------------------

def limits(admin, company):
    anonymous = Client()
    # Caddy compara lo que la petición anuncia con el tope de su ruta y contesta
    # sin esperar el cuerpo ni llamar a ninguna aplicación.
    check('límite · un JSON de 2 MiB al inicio de sesión se rechaza por lo que anuncia',
          declared('/api/auth/login', 2 * MB) == 413)
    check('límite · 300 MiB anunciados a una ruta cualquiera, sin sesión',
          declared('/api/cart/add', 300 * MB) == 413)
    check('límite · 2 MiB anunciados a una página de la tienda', declared('/', 2 * MB) == 413)
    check('límite · 300 MiB anunciados a la carga masiva de productos',
          declared(f'/api/admin/products/import/preview?company={company}', 300 * MB,
                   content_type='multipart/form-data; boundary=x', cookies=admin.cookies) == 413)
    check('límite · una notificación de pago de 200 KiB',
          declared('/api/payments/izipay/notification', 200 * KB) == 413)
    # Lo que no se anuncia tampoco pasa: un cuerpo real mayor que el tope se corta.
    res, body = anonymous.request('POST', '/api/cart/add', b'x' * (2 * MB), {'Content-Type': 'application/json'})
    check('límite · un cuerpo real de 2 MiB recibe 413 y una respuesta que la pantalla entiende',
          res.status == 413 and b'demasiado grande' in body, res.status)
    res, _ = anonymous.request('POST', '/api/auth/login', b'{"username": "nadie", "password": "x"}',
                               {'Content-Type': 'application/json'})
    check('límite · una petición normal sigue pasando', res.status in (400, 401), res.status)

    # Lo legítimo cabe: más que el tope general, menos que el de su ruta.
    target = f'/api/admin/storefront/images?company={company}'
    big = noise_png(900)                                   # ≈ 3,1 MiB
    res, body = admin.upload(target, 'file', 'grande.png', big, 'image/png')
    check(f'límite · una imagen de {len(big) // MB} MiB se acepta', res.status == 201, res.status)
    check('límite · una imagen de 10 MiB se rechaza sin esperar a recibirla',
          declared(target, 10 * MB, content_type='multipart/form-data; boundary=x', cookies=admin.cookies) == 413)
    order = rows_of(must(admin, 'GET', f'{INTERNAL}/service/orders'))[0]['id']
    base = f'{INTERNAL}/service/orders/{order}/evidence'
    photo = noise_png(1650)                                # ≈ 10,4 MiB: más que el tope de una imagen de tienda
    res, body = admin.upload(base, 'image', 'equipo.png', photo, 'image/png',
                             extra={'stage': 'intake'}, headers={'Idempotency-Key': uuid.uuid4().hex})
    check(f'límite · una foto de evidencia de {len(photo) // MB} MiB se acepta', res.status in (200, 201),
          f'{res.status} {str(body)[:120]}')
    check('límite · una evidencia de 30 MiB no',
          declared(base, 30 * MB, content_type='multipart/form-data; boundary=x', cookies=admin.cookies) == 413)


def slow_bodies():
    """
    Nueve peticiones que anuncian un cuerpo y no terminan de enviarlo. Django
    atiende con ocho hilos: si cada una ocupara el suyo esperando, la novena
    —y cualquier cliente de verdad— se quedaría sin respuesta.

    Se prueba en cada ruta cuya vista lee el cuerpo sin que nadie haya probado
    quién es: una cualquiera de la API, la notificación de pago y el webhook de
    WhatsApp.
    """
    for label, path in (
        ('una ruta cualquiera', '/api/cart/add'),
        ('la notificación de pago', '/api/payments/izipay/notification'),
        ('el webhook de WhatsApp', f'/api/v1/webhooks/whatsapp/{SLUG}'),
    ):
        held = []
        try:
            for _ in range(9):
                sock = CTX.wrap_socket(socket.create_connection((ADDR, PORT), 10), server_hostname=DOMAIN)
                sock.sendall((f'POST {path} HTTP/1.1\r\nHost: {DOMAIN}\r\nOrigin: https://{DOMAIN}\r\n'
                              'Content-Type: application/json\r\nContent-Length: 2000\r\n\r\n'
                              '{"session_key": "').encode())
                held.append(sock)
            time.sleep(2)
            started = time.monotonic()
            try:
                conn = rehearsal_media.Conn(DOMAIN, timeout=8)
                conn.request('GET', '/api/categories')
                status = conn.getresponse().status
                conn.close()
            except OSError:
                status = 0
            elapsed = time.monotonic() - started
            check(f'cuerpos lentos · nueve a medio enviar a {label} no dejan a la API sin hilos',
                  status == 200 and elapsed < 5, f'{status} en {elapsed:.1f} s')
        finally:
            for sock in held:
                try:
                    sock.close()
                except OSError:
                    pass


# --- pagos --------------------------------------------------------------------

def izipay_signature(payload_http: str) -> str:
    """base64(HMAC-SHA256(clave hash, payloadHttp)): lo que firma la pasarela."""
    key = os.environ['R_IZIPAY_HASH_KEY'].encode()
    return base64.b64encode(hmac.new(key, payload_http.encode(), hashlib.sha256).digest()).decode()


def payments(admin, company):
    """
    La pasarela del ensayo no existe: sus claves se generaron al empezar y no son
    de Izipay. Sirven para lo único que se puede ensayar sin ella: que la
    notificación sólo crea lo que viene firmado, y que firmado tampoco paga un
    pedido que la tienda no abrió.
    """
    anonymous = Client()
    target = '/api/payments/izipay/notification'

    def orders():
        return len(rows_of(must(admin, 'GET', f'/api/admin/orders/?company={company}')))

    def notify(payload_http, signature):
        body = {'code': '00', 'message': 'OK', 'payloadHttp': payload_http, 'transactionId': 'ENSAYO0001'}
        if signature is not None:
            body['signature'] = signature
        res, data = anonymous.json('POST', target, body)
        return res.status

    payload = json.dumps({
        'code': '00', 'message': 'Operación exitosa', 'transactionId': 'ENSAYO0001',
        'response': {
            'payMethod': 'CARD',
            'merchant': {'merchantCode': os.environ['R_IZIPAY_MERCHANT'], 'facilitatorCode': ''},
            'order': [{'currency': 'PEN', 'amount': '999.00', 'orderNumber': 'ENSAYO0001',
                       'codeAuth': '831000', 'stateMessage': 'Autorizado', 'uniqueId': '1'}],
        },
    })
    before = orders()
    check('pagos · una notificación sin firma se rechaza', notify(payload, None) == 400)
    check('pagos · con una firma inventada se rechaza', notify(payload, base64.b64encode(b'x' * 32).decode()) == 400)
    check('pagos · firmada, con el importe cambiado después de firmar, se rechaza',
          notify(payload.replace('999.00', '1.00'), izipay_signature(payload)) == 400)
    first = notify(payload, izipay_signature(payload))
    check('pagos · bien firmada, de un pago que la tienda no abrió: no paga nada',
          first in (200, 400, 404, 409) and orders() == before, f'{first}')
    again = notify(payload, izipay_signature(payload))
    check('pagos · la misma notificación otra vez responde igual', again == first and orders() == before, f'{again}')
    res, _ = anonymous.request(
        'POST', '/api/payments/micuentaweb/notification',
        b'kr-answer=%7B%7D&kr-hash=00&kr-hash-algorithm=sha256_hmac&kr-hash-key=password',
        {'Content-Type': 'application/x-www-form-urlencoded'})
    check('pagos · la notificación del producto que no está configurado no existe', res.status == 404, res.status)
    res, body = anonymous.json('GET', '/api/storefront/config')
    text = json.dumps(body)
    check('pagos · la configuración pública no trae ninguna clave',
          res.status == 200 and os.environ['R_IZIPAY_HASH_KEY'] not in text
          and not re.search(r'(?i)api_key|hash_key|password|secret', text))


# --- equipos con serie ----------------------------------------------------------

def equipment(admin, company):
    q = f'?company={company}'
    mark = uuid.uuid4().hex[:8].upper()
    serial, device_imei = f'ENSAYO{mark}', imei(int(mark, 16))
    product = must(admin, 'POST', f'/api/admin/products/{q}', {
        'name': f'Equipo de ensayo {mark}', 'price': '999.00', 'description': '', 'is_active': True,
    })['id']
    must(admin, 'POST', f'/api/admin/inventory/units/products/{product}/serialization/{q}',
         {'is_serialized': True, 'requires_imei': True})
    branches = rows_of(must(admin, 'GET', f'/api/admin/inventory/branches/{q}'))
    branch = branches[0]['id']

    def stock():
        rows = rows_of(must(admin, 'GET', f'/api/admin/inventory/stock/{q}&branch=all&product={product}'))
        return sum(r['quantity'] for r in rows)

    def available():
        return must(admin, 'GET', f'/api/me/internal-dashboard/{q}')['inventory']['equipment_available']

    def units():
        return rows_of(must(admin, 'GET', f'/api/admin/inventory/units/{q}&branch=all&product={product}'))

    before = available()
    res, body = admin.json('POST', f'/api/admin/inventory/movements/{q}', {
        'product_id': product, 'movement_type': 'manual_entry', 'quantity': 5, 'reason': 'ensayo',
    })
    check('equipos · el stock de un producto con serie no se ajusta por cantidad', res.status == 400, res.status)

    res, body = admin.json('POST', f'/api/admin/inventory/units/{q}', {
        'product_id': product, 'branch': branch, 'reason': f'Ensayo {mark}',
        'units': [{'serial_number': serial, 'imei': device_imei}],
    })
    check('equipos · registrar un equipo con su serie y su IMEI', res.status in (200, 201), f'{res.status} {str(body)[:120]}')
    check('equipos · stock +1', stock() == 1, stock())
    check('equipos · tablero +1', available() == before + 1, f'{before} → {available()}')
    res, _ = admin.json('POST', f'/api/admin/inventory/units/{q}', {
        'product_id': product, 'branch': branch, 'reason': f'Ensayo {mark}',
        'units': [{'serial_number': serial + 'B', 'imei': device_imei}],
    })
    check('equipos · el mismo IMEI no entra dos veces', res.status in (400, 409), res.status)

    sale = {
        'branch': branch, 'items': [{'product': product, 'quantity': 1}], 'payment_method': 'cash',
        'idempotency_key': uuid.uuid4().hex, 'terms_confirmed': True, 'receipt_type': 'sales_note',
        'amount_received': '999.00',
    }
    res, sold = admin.json('POST', f'/api/admin/pos/sales/{q}', sale)
    check('equipos · venta en caja', res.status == 201, f'{res.status} {str(sold)[:160]}')
    order = (sold or {}).get('order_id')
    rows = units()
    check('equipos · el equipo queda «vendido»', len(rows) == 1 and rows[0]['status'] == 'sold',
          [r.get('status') for r in rows])
    check('equipos · stock −1', stock() == 0, stock())
    check('equipos · tablero −1', available() == before, f'{before} → {available()}')
    res, again = admin.json('POST', f'/api/admin/pos/sales/{q}', {**sale, 'idempotency_key': uuid.uuid4().hex})
    check('equipos · un equipo no se vende dos veces', res.status == 409 and (again or {}).get('code') == 'insufficient_stock',
          f'{res.status} {str(again)[:100]}')
    res, repeated = admin.json('POST', f'/api/admin/pos/sales/{q}', sale)
    check('equipos · repetir la misma venta no crea otra', (repeated or {}).get('order_id') == order, res.status)

    documents = {}
    for label, suffix in (('nota de venta A4', ''), ('ticket de 80 mm', '&formato=ticket80')):
        res, pdf = admin.request('GET', f'/api/admin/orders/{order}/sales-note/pdf/{q}{suffix}')
        text = pdf_text(pdf) if res.status == 200 else ''
        check(f'documentos · {label}: PDF con la serie y el IMEI del equipo vendido',
              res.status == 200 and pdf[:4] == b'%PDF' and serial in text and device_imei in text,
              f"{res.status} {res.headers.get('Content-Type')}")
        documents[label] = len(pdf)
    res, pdf = admin.request('GET', f'/api/admin/orders/{order}/receipt-pdf/{q}')
    check('documentos · comprobante de pedido', res.status == 200 and pdf[:4] == b'%PDF', res.status)

    # Una foto de producto subida desde el panel: tiene que sobrevivir a todo.
    res, image = admin.upload(f'/api/admin/products/{product}/images/{q}', 'file', 'equipo.png', png(), 'image/png')
    url = (image or {}).get('url') or ((image or {}).get('image') or {}).get('url', '')
    check('productos · subir una foto de producto', res.status in (200, 201) and bool(url), f'{res.status} {str(image)[:120]}')
    res, _ = admin.json('GET', f'/api/admin/inventory/units/{q}&branch=all&search={device_imei}')
    check('equipos · el equipo se encuentra por su IMEI', res.status == 200, res.status)
    return {'product': product, 'order': order, 'serial': serial, 'imei': device_imei, 'product_image': url}


# --- seguimiento ----------------------------------------------------------------

def tracking(admin, company):
    mark = uuid.uuid4().hex[:8].upper()
    device_imei = imei(int(mark, 16) + 7)
    context = must(admin, 'GET', f'{INTERNAL}/service/context/')
    customer = must(admin, 'POST', f'/api/admin/customers/?company={company}', {
        'customer_type': 'person', 'first_name': 'Ensayo', 'last_name': f'Seguimiento {mark}', 'notes': 'ensayo',
    })
    device = must(admin, 'POST', f'{INTERNAL}/service/devices/', {
        'customer_id': customer['id'], 'device_type': 'phone', 'brand': 'Ensayo', 'model': f'Seguimiento {mark}',
        'serial_number': f'SEG{mark}', 'imei': device_imei, 'notes': 'ensayo',
    })
    order = must(admin, 'POST', f'{INTERNAL}/service/orders/', {
        'customer_id': customer['id'], 'device_id': device['id'],
        'branch_id': context['available_branches'][0]['id'],
        'reported_issue': f'Ensayo {mark}: no enciende', 'physical_condition': '', 'received_accessories': '',
    })
    check('WhatsApp apagado · la orden se crea igual', bool(order.get('id')))
    base = f"{INTERNAL}/service/orders/{order['id']}"
    status = must(admin, 'GET', f'{base}/tracking-link/')
    check('seguimiento · el estado del enlace no trae el enlace',
          status.get('active') is True and 'seguimiento' not in json.dumps(status))
    link = must(admin, 'POST', f'{base}/tracking-link/reveal/')
    path = link['path']
    token = path.rsplit('/', 1)[-1]

    visitor = Client()
    res, html = visitor.request('GET', path, origin=False)
    check('seguimiento · la página abre sin sesión', res.status == 200, res.status)
    check('seguimiento · no se indexa ni entrega su dirección',
          b'noindex' in html and res.headers.get('Referrer-Policy') == 'no-referrer',
          res.headers.get('Referrer-Policy'))
    res, data = visitor.json('GET', f'/api/v1/tracking/{token}/')
    text = json.dumps(data or {})
    check('seguimiento · la orden se lee con el enlace', res.status == 200 and order['number'] in text, res.status)
    check('seguimiento · el IMEI viaja enmascarado', device_imei not in text and device_imei[-4:] in text)
    middle = len(token) - 20
    altered = token[:middle] + ('B' if token[middle] == 'A' else 'A') + token[middle + 1:]
    res, _ = visitor.json('GET', f'/api/v1/tracking/{altered}/')
    check('seguimiento · un enlace alterado no dice nada', res.status == 404, res.status)
    # Lo que el personal escribe en un buscador viaja en la URL: tiene que salir del registro.
    res, _ = admin.json('GET', f'{INTERNAL}/service/devices/lookup/?imei={device_imei}')
    check('seguimiento · la recepción reconoce un equipo que ya estuvo', res.status == 200, res.status)
    return {'tracking_token': token, 'tracking_path': path, 'tracking_imei': device_imei}


# --- WhatsApp y Google sin credenciales -----------------------------------------

def integrations(admin):
    res, body = admin.json('GET', f'{INTERNAL}/messaging/whatsapp/')
    body = body or {}
    check('WhatsApp apagado · la pantalla dice que no está listo y qué falta',
          res.status == 200 and body.get('ready') is False and bool(body.get('missing')),
          f"{res.status} ready={body.get('ready')}")
    check('WhatsApp apagado · la respuesta no trae ningún token de Meta',
          not re.search(r'EAA[A-Za-z0-9]{20,}', json.dumps(body)))
    anonymous = Client()
    res, body = anonymous.json('GET', '/api/auth/google/config')
    check('Google sin configurar · se anuncia apagado', res.status == 200 and (body or {}).get('enabled') is False,
          f'{res.status} {body}')
    res, _ = anonymous.json('POST', '/api/auth/google', {'credential': 'no-es-un-token', 'nonce': 'x'})
    check('Google sin configurar · entrar con Google no existe', res.status in (400, 403, 404), res.status)
    res, _ = anonymous.json('POST', '/api/auth/login', {'username': 'nadie', 'password': 'x'})
    check('Google sin configurar · el acceso con usuario sigue ahí', res.status in (400, 401), res.status)


# --- correo real por SMTP -------------------------------------------------------

def sink_messages(container=None):
    """Lo que la tienda entregó a un servidor de correo del ensayo, mensaje a mensaje."""
    import subprocess

    out = subprocess.run(
        ['docker', 'exec', container or os.environ['R_MAIL_CONTAINER'], 'sh', '-c',
         'for f in /tmp/mail/*.eml; do [ -f "$f" ] && cat "$f" && printf "\n=====FIN=====\n"; done'],
        capture_output=True, text=True, timeout=30).stdout
    return [m.strip().replace('=\n', '').replace('=3D', '=') for m in out.split('=====FIN=====') if m.strip()]


def mail_for(address, needle, container=None):
    return [m for m in sink_messages(container) if address in m.splitlines()[0] and needle in m]


def link_token(message, path):
    match = re.search(re.escape(f'https://{DOMAIN}{path}?token=') + r'([A-Za-z0-9_\-]+)', message)
    return match.group(1) if match else ''


def mail():
    """
    La tienda envía por SMTP de verdad —a un servidor de correo del ensayo, que
    guarda los mensajes y no los reenvía—: el registro pide verificar el correo,
    el enlace que llega funciona, la contraseña se recupera por correo, y ningún
    enlace queda en el registro del servidor.
    """
    mark = uuid.uuid4().hex[:8]
    address = f'correo-{mark}@example.invalid'
    password, changed = f'Ensayo-{uuid.uuid4().hex[:10]}-A1', f'Ensayo-{uuid.uuid4().hex[:10]}-B2'
    visitor = Client()
    res, body = visitor.json('POST', '/api/auth/register', {
        'username': f'correo_{mark}', 'email': address, 'first_name': 'Ensayo', 'last_name': 'Correo',
        'password': password, 'password_confirm': password,
    })
    check('correo · registrarse pide verificar el correo',
          res.status == 201 and (body or {}).get('requires_verification') is True, f'{res.status} {str(body)[:100]}')
    res, _ = visitor.json('POST', '/api/auth/login', {'username': f'correo_{mark}', 'password': password})
    check('correo · sin verificar no se entra', res.status in (400, 401, 403), res.status)

    time.sleep(1)
    received = mail_for(address, '/auth/verify-email')
    check('correo · el mensaje de verificación llegó al servidor de correo', len(received) == 1, len(received))
    token = link_token(received[0], '/auth/verify-email') if received else ''
    check('correo · sale con el remitente configurado',
          bool(received) and os.environ['R_MAIL_FROM'] in received[0].splitlines()[0])
    res, _ = visitor.json('POST', '/api/auth/verify-email', {'token': token})
    check('correo · el enlace recibido verifica la cuenta', res.status == 200, res.status)
    res, _ = visitor.json('POST', '/api/auth/verify-email', {'token': token})
    check('correo · el enlace vale una sola vez', res.status in (400, 404, 410), res.status)

    res, _ = visitor.json('POST', '/api/auth/password-reset/request', {'email': address})
    check('correo · pedir recuperar la contraseña', res.status == 200, res.status)
    time.sleep(1)
    received = mail_for(address, '/auth/reset-password')
    check('correo · el mensaje de recuperación llegó', len(received) == 1, len(received))
    reset = link_token(received[0], '/auth/reset-password') if received else ''
    res, _ = visitor.json('POST', '/api/auth/password-reset/confirm', {'token': reset, 'new_password': changed})
    check('correo · el enlace recibido cambia la contraseña', res.status == 200, res.status)
    res, _ = Client().json('POST', '/api/auth/login', {'username': f'correo_{mark}', 'password': changed})
    check('correo · se entra con la contraseña nueva', res.status == 200, res.status)
    res, _ = visitor.json('POST', '/api/auth/password-reset/request', {'email': f'nadie-{mark}@example.invalid'})
    check('correo · pedirlo para un correo que no existe responde igual', res.status == 200, res.status)
    check('correo · y no envía nada', mail_for(f'nadie-{mark}@example.invalid', '') == [])
    return {'mail_address': address, 'mail_tokens': [token, reset], 'mail_passwords': [password, changed]}


# --- la consola de integraciones ---------------------------------------------------

CONSOLE = '/api/admin/integrations'


def _register(mark, name):
    address = f'{name}-{mark}@example.invalid'
    password = f'Ensayo-{uuid.uuid4().hex[:10]}-C3'
    res, _ = Client().json('POST', '/api/auth/register', {
        'username': f'{name}_{mark}', 'email': address, 'first_name': 'Ensayo', 'last_name': 'Consola',
        'password': password, 'password_confirm': password,
    })
    return res.status, address


def console():
    """
    Lo que la fase promete, en la pila de producción: lo que un MASTER activa en
    la consola es lo que la tienda usa en el siguiente envío, sin reiniciar nada.

    Hay dos servidores de correo: el del entorno (las variables `EMAIL_*`) y otro,
    en la misma máquina que el backend, con otro usuario y otra contraseña, que
    sólo conoce quien los escriba en la consola. Dónde llega cada mensaje dice
    qué configuración se usó. El correo empieza en el entorno y termina en él.
    """
    mark = uuid.uuid4().hex[:8]
    env_secret, typed = os.environ['R_MAIL_PASSWORD'], os.environ['R_CONSOLE_MAIL_PASSWORD']
    theirs = os.environ['R_CONSOLE_MAIL_CONTAINER']
    admin = Client()
    check('consola · inicio de sesión del MASTER', admin.login(os.environ['R_USER'], os.environ['R_PASSWORD']) == 200)

    res, listing = admin.json('GET', f'{CONSOLE}/')
    rows = {row['id']: row for row in (listing or {}).get('results', [])}
    check('consola · un MASTER ve las integraciones registradas',
          res.status == 200 and {'smtp', 'izipay_checkout', 'izipay_micuentaweb', 'whatsapp_cloud', 'google', 'sunat'} <= set(rows),
          f'{res.status} {sorted(rows)}')
    check('consola · el almacén de secretos tiene su clave raíz', (listing or {}).get('store_available') is True)
    check('consola · el correo figura activo y «configurado mediante entorno»',
          (rows.get('smtp', {}).get('state'), rows.get('smtp', {}).get('source')) == ('ACTIVE', 'env'),
          f"{rows.get('smtp', {}).get('state')} {rows.get('smtp', {}).get('source')}")
    check('consola · la pasarela figura activa por entorno, y el otro producto sin configurar',
          rows.get('izipay_checkout', {}).get('source') == 'env'
          and rows.get('izipay_micuentaweb', {}).get('state') == 'NOT_CONFIGURED')
    check('consola · SUNAT y Google, sin configurar',
          rows.get('sunat', {}).get('state') == 'NOT_CONFIGURED' and rows.get('google', {}).get('state') == 'NOT_CONFIGURED')

    outsider = Client()
    outsider.login(os.environ['R_OUTSIDER'], os.environ['R_OUTSIDER_PASSWORD'])
    for label, client in (('una persona sin empresa', outsider), ('nadie sin sesión', Client())):
        res, _ = client.json('GET', f'{CONSOLE}/')
        check(f'consola · {label} no la ve', res.status in (401, 403), res.status)
        res, _ = client.json('PUT', f'{CONSOLE}/smtp/draft/', {'public': {'host': 'mal.example.invalid'}, 'secrets': {}})
        check(f'consola · {label} no puede escribir en ella', res.status in (401, 403), res.status)

    # El correo del ensayo va sin cifrar a otro contenedor. El entorno lo admite;
    # la consola no guarda una contraseña que viajaría en claro a otro equipo.
    res, body = admin.json('POST', f'{CONSOLE}/smtp/import-env/', {})
    check('consola · no copia del entorno un correo sin cifrar hacia otro equipo',
          res.status == 400 and 'security' in ((body or {}).get('errors') or {}), f'{res.status} {str(body)[:120]}')

    sender = f'Consola {mark}'
    public = {'host': '127.0.0.1', 'port': 2526, 'security': 'none', 'username': 'consola',
              'from_email': f'consola@{DOMAIN}', 'from_name': sender, 'timeout': 4}
    res, body = admin.json('PUT', f'{CONSOLE}/smtp/draft/', {'public': public, 'secrets': {'password': typed}})
    draft = (body or {}).get('draft') or {}
    check('consola · se guarda como borrador', res.status == 200 and bool(draft), f'{res.status} {str(body)[:120]}')
    check('consola · la contraseña queda «configurada» y no viaja de vuelta',
          draft.get('secrets', {}).get('password', {}).get('configured') is True and typed not in json.dumps(body))

    res, _ = admin.json('POST', f'{CONSOLE}/smtp/activate/', {'version': draft.get('version')})
    check('consola · sin probarlo no se activa', res.status == 409, res.status)
    status, before = _register(mark, 'antes')
    time.sleep(1)
    check('consola · un borrador no cambia nada: el correo sigue saliendo por el servidor del entorno',
          status == 201 and len(mail_for(before, '/auth/verify-email')) == 1
          and mail_for(before, '', theirs) == [], status)

    res, body = admin.json('POST', f'{CONSOLE}/smtp/test/', {})
    check('consola · «Probar conexión» entra al servidor de correo: Correcto',
          res.status == 200 and (body or {}).get('status') == 'ok', f"{res.status} {(body or {}).get('status')}")
    check('consola · probar no envía ningún mensaje', sink_messages(theirs) == [])
    tested = (((body or {}).get('integration') or {}).get('draft') or {}).get('version')
    res, _ = admin.json('POST', f'{CONSOLE}/smtp/activate/', {'version': draft.get('version')})
    check('consola · lo que se activa es lo que se probó: la versión de antes de la prueba ya no vale', res.status == 409, res.status)
    res, body = admin.json('POST', f'{CONSOLE}/smtp/activate/', {'version': tested})
    check('consola · se activa', res.status == 200 and (body or {}).get('source') == 'panel',
          f"{res.status} {(body or {}).get('source')}")

    status, after = _register(mark, 'despues')
    time.sleep(1)
    received = mail_for(after, '/auth/verify-email', theirs)
    check('consola · el siguiente registro sale por el servidor de la consola, con su remitente, sin reiniciar nada',
          status == 201 and len(received) == 1 and sender in received[0] and mail_for(after, '') == [],
          f'{status} {len(received)}')
    token = link_token(received[0], '/auth/verify-email') if received else ''
    res, _ = Client().json('POST', '/api/auth/verify-email', {'token': token})
    check('consola · el enlace que llegó por ahí verifica la cuenta', res.status == 200, res.status)

    # Una contraseña mala no llega a sustituir a la que funciona.
    res, body = admin.json('PUT', f'{CONSOLE}/smtp/draft/', {'public': public, 'secrets': {'password': f'mala-{mark}'}})
    res, body = admin.json('POST', f'{CONSOLE}/smtp/test/', {})
    check('consola · una contraseña equivocada no pasa la prueba', (body or {}).get('status') == 'auth_failed',
          (body or {}).get('status'))
    version = (((body or {}).get('integration') or {}).get('draft') or {}).get('version')
    res, _ = admin.json('POST', f'{CONSOLE}/smtp/activate/', {'version': version})
    check('consola · y no sustituye a la configuración sana', res.status == 409, res.status)

    res, _ = admin.json('POST', f'{CONSOLE}/smtp/disable/', {})
    res, _ = Client().json('POST', '/api/auth/password-reset/request', {'email': after})
    time.sleep(1)
    check('consola · apagado en la consola no sale ningún correo, ni por ella ni por el entorno',
          res.status == 200 and mail_for(after, '/auth/reset-password') == []
          and mail_for(after, '/auth/reset-password', theirs) == [])

    res, body = admin.json('POST', f'{CONSOLE}/smtp/revoke/', {})
    check('consola · revocar pide confirmación', res.status == 400, res.status)
    res, body = admin.json('POST', f'{CONSOLE}/smtp/revoke/', {'confirm': 'REVOCAR'})
    check('consola · revocada, el correo vuelve a ser el del entorno',
          res.status == 200 and ((body or {}).get('state'), (body or {}).get('source')) == ('ACTIVE', 'env'),
          f"{res.status} {(body or {}).get('state')} {(body or {}).get('source')}")
    res, _ = Client().json('POST', '/api/auth/password-reset/request', {'email': after})
    time.sleep(1)
    check('consola · y el mismo aviso, pedido otra vez, sale por el servidor del entorno',
          len(mail_for(after, '/auth/reset-password')) == 1 and mail_for(after, '/auth/reset-password', theirs) == [])

    res, text = admin.request('GET', f'{CONSOLE}/')
    check('consola · ninguna respuesta trajo una contraseña de correo ni la clave de la pasarela',
          not any(value.encode() in text for value in (env_secret, typed, os.environ['R_IZIPAY_HASH_KEY'])))
    return {'console_secrets': [typed, f'mala-{mark}']}


def mail_down(state):
    """El servidor de correo no está, o rechaza la contraseña: la tienda sigue contestando."""
    started = time.monotonic()
    res, _ = Client().json('POST', '/api/auth/password-reset/request', {'email': state['mail_address']})
    elapsed = time.monotonic() - started
    check('correo · con el servidor de correo caído o mal configurado, la tienda contesta igual y sin colgarse',
          res.status == 200 and elapsed < 15, f'{res.status} en {elapsed:.1f} s')


def mail_stall(state):
    """
    El servidor de correo acepta la conexión y deja de responder. La tienda
    espera lo que dice EMAIL_TIMEOUT —ni se rinde al instante ni se queda
    colgada— y contesta.
    """
    timeout = float(os.environ['R_MAIL_TIMEOUT'])
    started = time.monotonic()
    res, _ = Client().json('POST', '/api/auth/password-reset/request', {'email': state['mail_address']})
    elapsed = time.monotonic() - started
    check(f'correo · con el servidor de correo mudo, la tienda espera {timeout:.0f} s y contesta',
          res.status == 200 and timeout - 1 <= elapsed < timeout + 4, f'{res.status} en {elapsed:.1f} s')


def verify(state):
    anonymous = Client()
    res, data = anonymous.request('GET', state['product_image'], origin=False)
    check('foto de producto · sigue sirviéndose',
          res.status == 200 and res.headers.get('Content-Type', '').startswith('image/'), res.status)
    res, data = anonymous.json('GET', f"/api/v1/tracking/{state['tracking_token']}/")
    check('seguimiento · el enlace sigue abriendo', res.status == 200, res.status)


def logs(state, path):
    """Lo que la tienda escribió en sus registros durante el recorrido."""
    with open(path, encoding='utf-8', errors='replace') as handle:
        text = handle.read()
    if len(sys.argv) < 4:
        check('registros · hay registro de acceso que revisar', '/api/v1/tracking/' in text and 'login_' in text)
    for label, needle in (
        ('el enlace de seguimiento', state.get('tracking_token')),
        ('el IMEI de la orden', state.get('tracking_imei')),
        ('el IMEI del equipo vendido', state.get('imei')),
        ('la contraseña del administrador', os.environ.get('R_PASSWORD')),
        ('la clave hash de la pasarela', os.environ.get('R_IZIPAY_HASH_KEY')),
        ('la contraseña del servidor de correo', os.environ.get('R_MAIL_PASSWORD')),
    ):
        check(f'registros · no guardan {label}', bool(needle) and needle not in text)
    for index, typed in enumerate(state.get('console_secrets', []), 1):
        check(f'registros · no guardan la contraseña {index} escrita en la consola', bool(typed) and typed not in text)
    for index, token in enumerate(state.get('mail_tokens', []), 1):
        check(f'registros · no guardan el enlace de correo {index}', bool(token) and token not in text)
    for index, password in enumerate(state.get('mail_passwords', []), 1):
        check(f'registros · no guardan la contraseña {index} del registro de prueba', password not in text)
    if state.get('mail_tokens'):
        check('registros · los correos no se escriben en el registro', 'Para verificar tu cuenta' not in text)
    check('registros · no guardan ningún token de sesión', not re.search(r'eyJ[A-Za-z0-9_-]{20,}\.', text))


def main():
    mode = sys.argv[1]
    if mode in ('mail', 'mail-down', 'mail-stall', 'console'):
        state = {}
        if os.path.exists(FLOWS_STATE):
            with open(FLOWS_STATE) as handle:
                state = json.load(handle)
        try:
            if mode == 'mail':
                state.update(mail())
                with open(FLOWS_STATE, 'w') as handle:
                    json.dump(state, handle)
            elif mode == 'console':
                state.update(console())
                with open(FLOWS_STATE, 'w') as handle:
                    json.dump(state, handle)
            elif mode == 'mail-down':
                mail_down(state)
            else:
                mail_stall(state)
        except Exception as exc:
            check(f'{mode} · el recorrido termina', False, f'{type(exc).__name__}: {str(exc)[:200]}')
        failed = rehearsal_media.FAILED
        print(f"  {'TODO OK' if not failed else 'FALLOS: ' + '; '.join(failed)}")
        sys.exit(1 if failed else 0)
    if mode == 'logs':
        with open(FLOWS_STATE) as handle:
            logs(json.load(handle), sys.argv[2])
        failed = rehearsal_media.FAILED
        print(f"  {'TODO OK' if not failed else 'FALLOS: ' + '; '.join(failed)}")
        sys.exit(1 if failed else 0)
    if mode == 'run':
        admin = Client()
        check('inicio de sesión del administrador',
              admin.login(os.environ['R_USER'], os.environ['R_PASSWORD']) == 200)
        company = next(c['id'] for c in rows_of(must(admin, 'GET', '/api/admin/companies')) if c.get('slug') == SLUG)
        state = {}
        for name, flow in (
            ('límites', lambda: limits(admin, company)),
            ('cuerpos lentos', slow_bodies),
            ('pagos', lambda: payments(admin, company)),
            ('equipos', lambda: state.update(equipment(admin, company))),
            ('seguimiento', lambda: state.update(tracking(admin, company))),
            ('integraciones', lambda: integrations(admin)),
        ):
            try:
                flow()
            except Exception as exc:  # un flujo roto no debe callar a los demás
                check(f'{name} · el recorrido termina', False, f'{type(exc).__name__}: {str(exc)[:200]}')
        with open(FLOWS_STATE, 'w') as handle:
            json.dump(state, handle)
    else:
        with open(FLOWS_STATE) as handle:
            state = json.load(handle)
    if state.get('product_image') and state.get('tracking_token'):
        verify(state)
    failed = rehearsal_media.FAILED
    print(f"  {'TODO OK' if not failed else 'FALLOS: ' + '; '.join(failed)}")
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
