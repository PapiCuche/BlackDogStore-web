#!/usr/bin/env python3
"""
Ensayo de producción — archivos subidos, a través de Caddy.

Lo llama `deploy/rehearsal.sh`. Sólo usa la biblioteca estándar: corre en el
equipo que hace el ensayo, no dentro de un contenedor, y habla con la tienda por
el mismo camino que un navegador.

QUÉ DEMUESTRA
-------------
Las imágenes de la tienda y las fotos de evidencia viven en el mismo almacén y
en el mismo volumen. No comparten autorización:

  · una imagen de la tienda es PÚBLICA: cualquiera la ve, con su transparencia;
  · una evidencia es PRIVADA: sin sesión, o con la sesión de quien no trabaja en
    la empresa, no se entrega;
  · ningún archivo del almacén se alcanza por una ruta de archivos (`/media/…`,
    `/private-media/…`): sólo por la API, que es quien decide.

Uso:
    rehearsal_media.py upload          sube, coloca y comprueba; escribe el estado
    rehearsal_media.py verify [claves] vuelve a comprobar lo que dejó `upload`
                                       (tras reiniciar, reconstruir o restaurar);
                                       `claves` son claves reales del almacén,
                                       separadas por comas

Variables: R_DOMAIN, R_ADDR, R_PORT, R_USER, R_PASSWORD, R_OUTSIDER,
R_OUTSIDER_PASSWORD, R_SLUG, R_STATE (archivo de estado).
"""
import http.client
import json
import os
import socket
import ssl
import struct
import sys
import uuid
import zlib

DOMAIN = os.environ.get('R_DOMAIN', 'tienda.test')
ADDR = os.environ.get('R_ADDR', '127.0.0.1')
PORT = int(os.environ.get('R_PORT', '18443'))
SLUG = os.environ.get('R_SLUG', 'black-dog-store')
STATE = os.environ['R_STATE']

# Certificado interno de Caddy: el ensayo no usa una autoridad pública.
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

FAILED = []


def check(label, ok, detail=''):
    print(f"  {'OK   ' if ok else 'FALLO'} {label}{(' — ' + str(detail)) if detail != '' else ''}")
    if not ok:
        FAILED.append(label)


class Conn(http.client.HTTPSConnection):
    def connect(self):
        sock = socket.create_connection((ADDR, PORT), self.timeout)
        self.sock = CTX.wrap_socket(sock, server_hostname=DOMAIN)


class Client:
    """Un navegador mínimo: cookies, origen propio y token CSRF."""

    def __init__(self):
        self.cookies = {}

    def request(self, method, path, body=None, headers=None, origin=True):
        headers = dict(headers or {})
        if self.cookies:
            headers['Cookie'] = '; '.join(f'{k}={v}' for k, v in self.cookies.items())
        if origin:
            headers.setdefault('Origin', f'https://{DOMAIN}')
            headers.setdefault('Referer', f'https://{DOMAIN}/')
        if method not in ('GET', 'HEAD') and 'csrftoken' in self.cookies:
            headers['X-CSRFToken'] = self.cookies['csrftoken']
        conn = Conn(DOMAIN, timeout=60)
        conn.request(method, path, body=body, headers=headers)
        res = conn.getresponse()
        data = res.read()
        for value in res.headers.get_all('Set-Cookie') or []:
            name, _, rest = value.partition('=')
            self.cookies[name.strip()] = rest.split(';', 1)[0]
        conn.close()
        return res, data

    def json(self, method, path, payload=None):
        body = json.dumps(payload).encode() if payload is not None else None
        res, data = self.request(method, path, body, {'Content-Type': 'application/json'})
        try:
            return res, json.loads(data or b'null')
        except ValueError:
            return res, None

    def login(self, username, password):
        self.request('GET', '/api/auth/csrf')
        res, _ = self.json('POST', '/api/auth/login', {'username': username, 'password': password})
        return res.status

    def upload(self, path, field, filename, content, content_type, extra=None, headers=None):
        boundary = uuid.uuid4().hex
        parts = []
        for key, value in (extra or {}).items():
            parts.append(
                f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
            f'Content-Type: {content_type}\r\n\r\n'.encode() + content + b'\r\n')
        parts.append(f'--{boundary}--\r\n'.encode())
        merged = {'Content-Type': f'multipart/form-data; boundary={boundary}'}
        merged.update(headers or {})
        res, data = self.request('POST', path, b''.join(parts), merged)
        try:
            return res, json.loads(data or b'null')
        except ValueError:
            return res, None


def png(width=40, height=30, ink=(200, 30, 30, 255)):
    """Un PNG RGBA: borde transparente, centro opaco. Sin dependencias."""
    rows = b''
    for y in range(height):
        rows += b'\x00'
        for x in range(width):
            inside = 8 <= x < width - 8 and 6 <= y < height - 6
            rows += bytes(ink) if inside else b'\x00\x00\x00\x00'

    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))

    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b''))


def png_alpha(data):
    """(tipo de color, alfa de la esquina, alfa del centro) de un PNG RGBA de 8 bits."""
    if data[:8] != b'\x89PNG\r\n\x1a\n':
        return None
    pos, idat, width, height, colour, interlace = 8, b'', 0, 0, -1, 0
    while pos < len(data):
        length, kind = struct.unpack('>I4s', data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + length]
        if kind == b'IHDR':
            width, height, _depth, colour, _c, _f, interlace = struct.unpack('>IIBBBBB', body)
        elif kind == b'IDAT':
            idat += body
        pos += 12 + length
    if colour != 6 or interlace:
        return (colour, None, None)
    raw, stride, prev, rows = zlib.decompress(idat), width * 4, bytearray(width * 4), []
    for y in range(height):
        start = y * (stride + 1)
        kind, line = raw[start], bytearray(raw[start + 1:start + 1 + stride])
        for i in range(stride):
            a = line[i - 4] if i >= 4 else 0
            b = prev[i]
            c = prev[i - 4] if i >= 4 else 0
            if kind == 1:
                line[i] = (line[i] + a) & 255
            elif kind == 2:
                line[i] = (line[i] + b) & 255
            elif kind == 3:
                line[i] = (line[i] + (a + b) // 2) & 255
            elif kind == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        rows.append(line)
        prev = line
    return (colour, rows[0][3], rows[height // 2][(width // 2) * 4 + 3])


def rows_of(payload):
    return payload if isinstance(payload, list) else (payload or {}).get('results', [])


def public_checks(urls):
    anonymous = Client()
    for slot, url in urls.items():
        res, data = anonymous.request('GET', url, origin=False)
        alpha = png_alpha(data) if res.status == 200 else None
        check(f'imagen pública · {slot}: 200, PNG, transparente',
              res.status == 200 and res.headers.get('Content-Type') == 'image/png'
              and alpha == (6, 0, 255),
              f"{res.status} {res.headers.get('Content-Type')} alfa={alpha}")
        check(f'imagen pública · {slot}: caché inmutable y nosniff',
              'immutable' in (res.headers.get('Cache-Control') or '')
              and res.headers.get('X-Content-Type-Options') == 'nosniff')
    res, config = anonymous.json('GET', '/api/storefront/config')
    page = (config or {}).get('page', {})
    check('la portada pública anuncia las imágenes colocadas',
          res.status == 200 and page.get('hero_image_url') == urls['hero']
          and page.get('services_image_url') == urls['servicio']
          and page.get('location_image_url') == urls['ubicación'] and page.get('hero_variant') == 'light')
    res, html = anonymous.request('GET', '/', origin=False)
    check('la portada se sirve con el hero claro', res.status == 200 and b'data-hero-variant="light"' in html)


def private_checks(state):
    content = state['evidence_content']
    anonymous = Client()
    res, _ = anonymous.request('GET', content, origin=False)
    check('evidencia privada · sin sesión no se entrega', res.status in (401, 403), res.status)
    res, _ = anonymous.request('GET', state['evidence_customer'], origin=False)
    check('evidencia privada · ruta de cliente sin sesión', res.status in (401, 403), res.status)

    outsider = Client()
    status = outsider.login(os.environ['R_OUTSIDER'], os.environ['R_OUTSIDER_PASSWORD'])
    res, _ = outsider.request('GET', content)
    check('evidencia privada · con sesión de quien no trabaja en la empresa',
          status == 200 and res.status in (403, 404), f'login {status}, evidencia {res.status}')
    res, _ = outsider.request('GET', state['evidence_customer'])
    check('evidencia privada · con sesión de un cliente ajeno a la orden',
          res.status in (403, 404), res.status)

    staff = Client()
    staff.login(os.environ['R_USER'], os.environ['R_PASSWORD'])
    res, _ = staff.request('GET', content)
    check('evidencia privada · quien trabaja en la empresa sí la ve',
          res.status == 200 and res.headers.get('Content-Type', '').startswith('image/'), res.status)
    check('evidencia privada · no se guarda en cachés compartidas',
          'public' not in (res.headers.get('Cache-Control') or ''), res.headers.get('Cache-Control'))

    # Ninguna ruta de archivos: el almacén sólo se alcanza por la API.
    for key in state.get('storage_keys', []):
        kind = 'evidencia' if '/storefront/' not in key else 'imagen de tienda'
        for prefix in ('/media/', '/private-media/', '/app/private-media/', '/api/media/', '/static/'):
            res, _ = anonymous.request('GET', prefix + key, origin=False)
            served = res.status == 200 and res.headers.get('Content-Type', '').startswith('image/')
            check(f'sin ruta de archivos · {prefix}<{kind}>', not served, res.status)


def upload():
    admin = Client()
    check('inicio de sesión del administrador', admin.login(os.environ['R_USER'], os.environ['R_PASSWORD']) == 200)
    _res, companies = admin.json('GET', '/api/admin/companies')
    company = next(c['id'] for c in rows_of(companies) if c.get('slug') == SLUG)
    target = f'/api/admin/storefront/images?company={company}'

    urls = {}
    for slot in ('hero', 'categoría', 'servicio', 'ubicación', 'campaña'):
        res, body = admin.upload(target, 'file', f'{slot}.png', png(), 'image/png')
        check(f'subir imagen · {slot}', res.status == 201 and (body or {}).get('has_alpha') is True, res.status)
        urls[slot] = (body or {}).get('url', '')

    res, _ = admin.json('PATCH', f'/api/admin/storefront/page?company={company}', {
        'hero_variant': 'light', 'hero_image_url': urls['hero'],
        'services_image_url': urls['servicio'], 'location_image_url': urls['ubicación'],
    })
    check('colocar hero, servicio y ubicación', res.status == 200, res.status)
    _res, categories = admin.json('GET', f'/api/admin/categories?company={company}')
    res, _ = admin.json('PATCH', f"/api/admin/categories/{rows_of(categories)[0]['id']}?company={company}",
                        {'image_url': urls['categoría']})
    check('colocar imagen de categoría', res.status == 200, res.status)
    res, body = admin.json('POST', f'/api/admin/storefront/campaigns?company={company}', {
        'slot': 'home_promo', 'title': 'Ensayo', 'image_url': urls['campaña'],
    })
    check('crear campaña con imagen', res.status == 201, f'{res.status} {body}')

    # --- lo que NO se acepta -------------------------------------------------
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    gif = (b'GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,'
           b'\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;')
    for label, name, content, kind in (
        ('SVG con script', 'logo.svg', svg, 'image/svg+xml'),
        ('SVG disfrazado de PNG', 'logo.png', svg, 'image/png'),
        ('HTML disfrazado de PNG', 'pagina.png', b'<html><script>alert(1)</script></html>', 'image/png'),
        ('GIF', 'animado.gif', gif, 'image/gif'),
        ('PNG truncado', 'roto.png', png()[:60], 'image/png'),
        ('archivo de 9 MB', 'grande.png', png() + b'\x00' * (9 * 1024 * 1024), 'image/png'),
    ):
        res, _ = admin.upload(target, 'file', name, content, kind)
        check(f'rechazo · {label}', res.status in (400, 413), res.status)
    res, body = admin.upload(target, 'file', '../../../etc/passwd.png', png(), 'image/png')
    url = (body or {}).get('url', '')
    check('nombre con ruta: se acepta la imagen y el nombre no llega a la dirección',
          res.status == 201 and '..' not in url and 'passwd' not in url, res.status)
    res, _ = Client().upload(target, 'file', 'x.png', png(), 'image/png')
    check('subir sin sesión', res.status in (401, 403), res.status)
    res, _ = Client().request('GET', '/api/storefront/images/' + '0' * 32, origin=False)
    check('imagen inexistente', res.status == 404, res.status)

    # --- una evidencia privada en el mismo almacén ---------------------------
    _res, orders = admin.json('GET', f'/api/v1/internal/{SLUG}/service/orders')
    order = rows_of(orders)[0]['id']
    base = f'/api/v1/internal/{SLUG}/service/orders/{order}/evidence'
    res, body = admin.upload(base, 'image', 'equipo.png', png(120, 90, (20, 90, 160, 255)), 'image/png',
                             extra={'stage': 'intake'}, headers={'Idempotency-Key': uuid.uuid4().hex})
    evidence = (body or {}).get('id') or ((body or {}).get('evidence') or {}).get('id')
    check('subir evidencia privada', res.status in (200, 201) and bool(evidence), f'{res.status}')

    state = {
        'urls': urls, 'company': company,
        'evidence_content': f'{base}/{evidence}/content',
        'evidence_customer': f'/api/v1/customer/{SLUG}/repairs/{order}/evidence/{evidence}/content',
        'storage_keys': [],
    }
    with open(STATE, 'w') as handle:
        json.dump(state, handle)
    return state


def main():
    mode = sys.argv[1]
    if mode == 'upload':
        state = upload()
    else:
        with open(STATE) as handle:
            state = json.load(handle)
    if len(sys.argv) > 2:
        state['storage_keys'] = [key for key in sys.argv[2].split(',') if key]
        with open(STATE, 'w') as handle:
            json.dump(state, handle)
    public_checks(state['urls'])
    private_checks(state)
    print(f"  {'TODO OK' if not FAILED else 'FALLOS: ' + '; '.join(FAILED)}")
    sys.exit(1 if FAILED else 0)


if __name__ == '__main__':
    main()
