"""
See every kind of e-mail in a browser, without sending any — DEVELOPMENT ONLY.

    GET /api/dev/mail-preview/                 the list
    GET /api/dev/mail-preview/<ejemplo>/       that example, as HTML
    GET /api/dev/mail-preview/<ejemplo>/?formato=texto   its plain-text version

With `DEBUG = False` every one of these answers 404: in production this surface
does not exist, like `/api/dev/demo-accounts/`.

It renders the examples of `store/mail/ejemplos/` — invented data, never a real
customer, a real order or a link that works — with the template of its owner,
whatever storefront this installation serves. A placeholder where a link goes
(«[URL de seguimiento]») is shown pointing at the site, because the template
only prints links that go somewhere allowed.
"""
import html
import json
from pathlib import Path

from django.conf import settings
from django.http import Http404, HttpResponse
from django.views import View

from .mail import skin as mail_skin
from .mail.service import render_with

_EXAMPLES = Path(__file__).resolve().parent / 'mail' / 'ejemplos'
_LINKS = (('imagen', 'url'), ('imagen', 'enlace'), ('boton', 'url'), ('secundario', 'url'), ('baja_url',))


def _examples() -> dict:
    return {path.stem: path for path in sorted(_EXAMPLES.glob('*.json'))}


def _owner_skin():
    from .models import Company

    owner = Company.objects.filter(slug=mail_skin.owner_slug()).first()
    return mail_skin.skin_for(owner) if owner is not None else None


def _with_working_links(data: dict) -> dict:
    """An example's «[URL …]» placeholders, pointed at the site so they can be shown."""
    site = (settings.FRONTEND_URL or '').rstrip('/')
    for path in _LINKS:
        holder = data
        for key in path[:-1]:
            holder = holder.get(key) if isinstance(holder, dict) else None
        if isinstance(holder, dict) and isinstance(holder.get(path[-1]), str):
            value = holder[path[-1]]
            if not value.startswith('https://') or '[' in value:
                holder[path[-1]] = f'{site}/#ejemplo'
    return data


class _DevOnly(View):
    def dispatch(self, request, *args, **kwargs):
        if not settings.DEBUG:
            raise Http404()
        return super().dispatch(request, *args, **kwargs)


class MailPreviewIndexView(_DevOnly):
    def get(self, request):
        rows = ''.join(
            f'<li><a href="{html.escape(name)}/">{html.escape(name)}</a> · '
            f'<a href="{html.escape(name)}/?formato=texto">texto</a></li>'
            for name in _examples()
        )
        note = '' if _owner_skin() is not None else (
            '<p><strong>No hay plantilla que mostrar:</strong> falta la empresa '
            f'«{html.escape(mail_skin.owner_slug())}» o alguno de sus datos fijos.</p>'
        )
        return HttpResponse(
            '<!doctype html><meta charset="utf-8"><title>Correos · vista previa</title>'
            '<h1>Correos · vista previa</h1><p>Sólo en desarrollo. No se envía nada.</p>'
            f'{note}<ul>{rows}</ul>'
        )


class MailPreviewView(_DevOnly):
    def get(self, request, example):
        path = _examples().get(example)
        skin = _owner_skin()
        if path is None or skin is None:
            raise Http404()
        rendered = render_with(skin, _with_working_links(json.loads(path.read_text(encoding='utf-8'))))
        if request.GET.get('formato') == 'texto':
            return HttpResponse(f'Asunto: {rendered.subject}\n\n{rendered.text}', content_type='text/plain; charset=utf-8')
        return HttpResponse(rendered.html)
