"""
The plain-text version of a message, written from the same data as the HTML.

One function for every kind of message: it walks the blocks in the order the
template shows them and prints the ones that are there. Whoever reads their mail
as text — and every program that reads ours — gets the same facts and the same
links.
"""

_STATE = (('hecho', '[x]'), ('actual', '[>]'), ('pendiente', '[ ]'))


def _block(lines, *content):
    if lines and lines[-1] != '':
        lines.append('')
    lines.extend(content)


def render_text(data: dict, skin) -> str:
    lines = []
    if data.get('etiqueta'):
        lines.append(data['etiqueta'].upper())
    lines.append(data['titulo'])
    if data.get('saludo'):
        _block(lines, data['saludo'])
    for paragraph in data['parrafos']:
        _block(lines, paragraph)

    code = data.get('codigo')
    if code:
        _block(lines, f'{code.get("etiqueta", "Código")}: {code["valor"]}')
        if code.get('nota'):
            lines.append(code['nota'])

    progress = data.get('progreso')
    if progress:
        _block(lines, progress.get('titulo', 'Estado').upper())
        for step in progress.get('pasos', []):
            mark = next((sign for state, sign in _STATE if step.get(state)), '[ ]')
            current = ' (en curso)' if step.get('actual') else ''
            when = f' — {step["fecha"]}' if step.get('fecha') else ''
            lines.append(f'  {mark} {step["texto"]}{current}{when}')

    detail = data.get('detalle')
    if detail:
        _block(lines, detail.get('titulo', 'Detalle').upper())
        for item in detail.get('items', []):
            note = f' ({item["nota"]})' if item.get('nota') else ''
            lines.append(f'  {item["nombre"]}{note}: {item["valor"]}')
        for row in detail.get('resumen', []):
            lines.append(f'  {row["nombre"]}: {row["valor"]}')
        total = detail.get('total')
        if total:
            lines.append(f'  {total["nombre"].upper()}: {total["valor"]}')

    facts = data.get('datos')
    if facts:
        _block(lines, facts.get('titulo', 'Datos').upper())
        for row in facts.get('campos', []) + facts.get('destacados', []):
            lines.append(f'  {row["nombre"]}: {row["valor"]}')

    notice = data.get('aviso')
    if notice:
        _block(lines, f'{notice["etiqueta"].upper()}: {notice["texto"]}')

    button = data.get('boton')
    if button:
        _block(lines, f'{button["texto"]}:', button['url'])

    secondary = data.get('secundario')
    if secondary:
        _block(lines, f'{secondary["texto"]} {secondary["enlace"]}:', secondary['url'])

    signature = data.get('firma')
    if signature:
        _block(lines, signature.get('cierre', ''), signature.get('nombre', ''))
        lines.append(f'Equipo {skin.name}')
    else:
        _block(lines, f'Equipo {skin.name}')

    _block(lines, '--')
    lines.extend(part for part in (skin.name, skin.address, f'WhatsApp {skin.whatsapp}' if skin.whatsapp else '') if part)
    _block(lines, data['motivo'])
    if data.get('baja_url'):
        lines.append(f'Dejar de recibir estos correos: {data["baja_url"]}')
    return '\n'.join(line for line in lines if line is not None).strip() + '\n'
