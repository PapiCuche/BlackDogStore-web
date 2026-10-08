"""
MAIL-TEMPLATE-01 · one template for every e-mail a shop sends.

`store/mail/plantilla/plantilla-maestra.html` is one shop's design: its footer
carries that shop's address, phone and social accounts. What these tests hold
the code to:

  * THE TEMPLATE IS ITS OWNER'S AND NOBODY ELSE'S. Another company keeps the
    neutral e-mail it had, and so does the platform unless this installation
    serves the owner's storefront. A second tenant never signs with somebody
    else's address.
  * The data is the contract of `ejemplos/*.json`: a block is shown when its
    key is there, so what is not known is LEFT OUT, never sent empty.
  * A customer's text never reaches the HTML unescaped, and a link only goes
    where ours go.
  * The plain-text version says the same and carries the same links.
  * A template that cannot be filled never costs the e-mail: the plain one goes.
"""
import datetime
import json
import logging
import re
from decimal import Decimal
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core import mail as outbox_module
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from store import mail
from store.company_provisioning import provision_company_access_defaults
from store.mail import builders, contract, format as fmt, skin as mail_skin
from store.mail.service import render_with
from store.models import Company, CompanyRole, StaffInvitation
from store.staff_services import create_invitation

User = get_user_model()

PILOT = 'black-dog-store'
SITE = 'https://tienda.test'
EXAMPLES = Path(mail.__file__).resolve().parent / 'ejemplos'
TEMPLATE = Path(mail.__file__).resolve().parent / 'plantilla' / 'plantilla-maestra.html'
PILOT_MARKS = ('Octavio Muñoz Najar', '936 449 536', 'blackdogstore_pe', '20610159886')

# The piece of markup that only one block prints.
BLOCK = {
    'imagen': 'border-radius:10px;font-family:\'Archivo\', Arial, Helvetica, sans-serif;font-size:16px;color:#2E2E2E;">',
    'codigo': 'letter-spacing:8px',
    'progreso': 'border-radius:50%',
    'detalle': 'border-top:1px solid #0D0D0D',
    'datos': 'bgcolor="#0D0D0D" style="background-color:#0D0D0D;border-radius:10px;"',
    'aviso': 'bgcolor="#D9D9D9" style="background-color:#D9D9D9;border-radius:10px;"',
    'boton': 'class="btn"',
    'secundario': 'font-weight:600;text-decoration:underline;',
}


def example(name: str) -> dict:
    return json.loads((EXAMPLES / f'{name}.json').read_text(encoding='utf-8'))


def minimal(**extra) -> dict:
    return {
        'asunto': 'Asunto', 'titulo': 'Título', 'parrafos': ['Un párrafo.'],
        'motivo': 'Recibes este correo por una prueba.', 'anio': '2026', **extra,
    }


def other_company(slug='otra-tienda', name='Otra Tienda'):
    row = Company.objects.create(name=name, slug=slug, legal_name=f'{name} S.A.C.', tax_id='20999999991')
    provision_company_access_defaults(row)
    return row


@override_settings(
    FRONTEND_URL=SITE, DEFAULT_STOREFRONT_COMPANY_SLUG=PILOT,
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    DEFAULT_FROM_EMAIL='no-reply@tienda.test',
)
class _Base(TestCase):
    def setUp(self):
        cache.clear()
        outbox_module.outbox = []
        self.pilot = Company.objects.get(slug=PILOT)

    def html(self, data, company=None):
        return render_with(mail_skin.skin_for(company or self.pilot), data).html

    def text(self, data, company=None):
        return render_with(mail_skin.skin_for(company or self.pilot), data).text


class OwnershipTest(_Base):
    """Whose template it is."""

    def test_it_dresses_its_owner(self):
        self.assertTrue(mail.available(self.pilot))
        self.assertEqual(mail.brand(self.pilot), 'Black Dog Store')

    def test_another_company_has_no_template(self):
        self.assertFalse(mail.available(other_company()))
        with self.assertRaises(LookupError):
            mail.render('password_reset', minimal(), company=other_company('otra-mas', 'Otra Más'))

    def test_the_platform_wears_it_only_where_the_owner_is_the_storefront(self):
        self.assertTrue(mail.available())
        for slug in ('', 'otra-tienda', 'no-existe'):
            with self.subTest(slug), override_settings(DEFAULT_STOREFRONT_COMPANY_SLUG=slug):
                if slug == 'otra-tienda':
                    other_company()
                self.assertFalse(mail.available())

    def test_an_inactive_storefront_company_dresses_nothing(self):
        Company.objects.filter(slug=PILOT).update(is_active=False)
        self.assertFalse(mail.available())

    def test_without_one_of_its_fixed_values_it_is_not_used(self):
        """A footer with «[RUC]» printed in it is worse than the plain e-mail."""
        for label, change in (
            ('no site', lambda: override_settings(FRONTEND_URL='')),
            ('no legal name', lambda: Company.objects.filter(slug=PILOT).update(legal_name='')),
            ('no tax id', lambda: Company.objects.filter(slug=PILOT).update(tax_id='')),
        ):
            with self.subTest(label):
                saved = Company.objects.filter(slug=PILOT).values('legal_name', 'tax_id').get()
                context = change()
                try:
                    if context is not None and hasattr(context, '__enter__'):
                        context.__enter__()
                    with self.assertLogs('store.mail.skin', level='ERROR'):
                        self.assertFalse(mail.available(Company.objects.get(slug=PILOT)))
                finally:
                    if context is not None and hasattr(context, '__exit__'):
                        context.__exit__(None, None, None)
                    Company.objects.filter(slug=PILOT).update(**saved)

    def test_the_file_is_the_designers_and_its_fixed_values_are_put_in_when_loaded(self):
        source = TEMPLATE.read_text(encoding='utf-8')
        for placeholder in ('[URL pública del logo]', '[URL del sitio web]', '[URL del canal de YouTube]', '[Razón social]', '[RUC]'):
            self.assertIn(placeholder, source)
        self.assertNotIn('{{{', source)

        html = self.html(minimal())
        self.assertNotRegex(html, r'\[(URL|RUC|Razón)')
        self.assertIn(f'src="{SITE}/assets/branding/logo-horizontal-on-light.png"', html)
        self.assertIn(f'href="{SITE}"', html)
        self.assertIn('https://www.youtube.com/@BlackDogStorePer%C3%BA', html)
        self.assertIn('CMAU CORP E.I.R.L. · RUC 20610159886', html)

    def test_what_the_company_wrote_about_itself_is_escaped_too(self):
        Company.objects.filter(slug=PILOT).update(legal_name='Acme <b>& Hijos</b> "S.A.C."')
        html = self.html(minimal(), Company.objects.get(slug=PILOT))
        self.assertIn('Acme &lt;b&gt;&amp; Hijos&lt;/b&gt; &quot;S.A.C.&quot;', html)
        self.assertNotIn('<b>& Hijos', html)

    def test_what_the_company_wrote_about_itself_never_becomes_template(self):
        """
        The fixed values are put into the SOURCE, before it is rendered. A legal
        name with Mustache in it must print as it was typed, not run.
        """
        attack = {'detalle': None, 'datos': {'titulo': 'Ficha', 'campos': [{'nombre': 'Cliente', 'valor': '<img src=x onerror=alert(1)>'}]},
                  'boton': {'texto': 'Ir', 'url': f'{SITE}/auth/reset-password?token=SECRETO'}}
        for written in (
            'ACME {{ S.A.C.', 'ACME }} S.A.C.', '{{#datos.campos}}{{{valor}}}{{/datos.campos}}',
            '{{boton.url}}', '{{=<% %>=}}', '{{> x}}', '{{{motivo}}}',
        ):
            for field in ('legal_name', 'tax_id'):
                if len(written) > Company._meta.get_field(field).max_length:
                    continue
                with self.subTest(written=written, field=field):
                    saved = Company.objects.filter(slug=PILOT).values('legal_name', 'tax_id').get()
                    try:
                        Company.objects.filter(slug=PILOT).update(**{field: written})
                        html = self.html(minimal(**attack), Company.objects.get(slug=PILOT))
                    finally:
                        Company.objects.filter(slug=PILOT).update(**saved)
                    self.assertNotIn('<img src=x', html)
                    self.assertEqual(html.count('token=SECRETO'), 2)          # the button, twice (Outlook's and everybody's)
                    self.assertIn(written.replace('{', '&#123;').replace('}', '&#125;').replace('>', '&gt;').replace('<', '&lt;'), html)

    def test_the_text_footer_says_what_the_template_prints(self):
        """
        The template's footer is fixed. The text version reads the same three
        facts from `marca.json`, and they have to be IN the template: what the
        company later types in its settings changes neither.
        """
        from store.models import CompanySettings

        source = TEMPLATE.read_text(encoding='utf-8')
        brand = json.loads((TEMPLATE.parent / 'marca.json').read_text(encoding='utf-8'))
        self.assertIn(f'Equipo {brand["nombre"]}</p>', source)
        self.assertIn(f'>{brand["direccion"]}</p>', source)
        self.assertIn(f'>{brand["whatsapp"]}</a>', source)

        Company.objects.filter(slug=PILOT).update(name='Otro Nombre S.A.')
        CompanySettings.objects.filter(company=self.pilot).update(whatsapp_number='51900000000', legal_address='Otra calle 1')
        pilot = Company.objects.get(slug=PILOT)
        text = self.text(minimal(), pilot)
        self.assertIn(f'Equipo {brand["nombre"]}\n', text)
        self.assertIn(f'{brand["nombre"]}\n{brand["direccion"]}\nWhatsApp {brand["whatsapp"]}\n', text)
        for typed_later in ('Otro Nombre', '51900000000', 'Otra calle 1'):
            self.assertNotIn(typed_later, text)
        self.assertEqual(mail.brand(pilot), brand['nombre'])


class ExamplesTest(_Base):
    """Every example of the contract can be shown."""

    def test_each_example_renders_whole(self):
        from store.mail_preview_views import _with_working_links

        names = sorted(path.stem for path in EXAMPLES.glob('*.json'))
        self.assertGreaterEqual(len(names), 11)
        for name in names:
            with self.subTest(name):
                data = _with_working_links(example(name))
                rendered = render_with(mail_skin.skin_for(self.pilot), data)
                self.assertNotIn('{{', rendered.html)
                self.assertTrue(data['titulo'])
                self.assertIn(f'>{data["titulo"]}</h1>', rendered.html)
                self.assertTrue(rendered.html.rstrip().endswith('</html>'))
                self.assertIn(data['motivo'], rendered.text)

    def test_the_gold_moment_changes_the_rule_and_the_button(self):
        gold = self.html({**minimal(), 'momento_oro': True, 'boton': {'texto': 'Ir', 'url': f'{SITE}/x'}})
        plain = self.html({**minimal(), 'boton': {'texto': 'Ir', 'url': f'{SITE}/x'}})
        self.assertIn('fillcolor="#B38845"', gold)
        self.assertNotIn('fillcolor="#B38845"', plain)
        self.assertIn('fillcolor="#0D0D0D"', plain)


class ContractTest(_Base):
    def test_a_block_without_its_key_is_not_there(self):
        bare = self.html(minimal())
        for block, mark in BLOCK.items():
            self.assertNotIn(mark, bare, block)

        full = self.html({
            **minimal(),
            'imagen': {'url': f'{SITE}/i.png', 'alt': 'Alt', 'enlace': f'{SITE}/'},
            'codigo': {'etiqueta': 'Tu código', 'valor': '123456'},
            'progreso': {'titulo': 'Estado', 'pasos': [{'texto': 'Recibido', 'fecha': '01/02', 'hecho': True}]},
            'detalle': {'titulo': 'Detalle', 'items': [{'nombre': 'Uno', 'valor': 'S/ 1.00'}]},
            'datos': {'titulo': 'Datos', 'campos': [{'nombre': 'A', 'valor': 'B'}]},
            'aviso': {'etiqueta': 'Aviso', 'texto': 'Texto'},
            'boton': {'texto': 'Ir', 'url': f'{SITE}/x'},
            'secundario': {'texto': '¿Dudas?', 'enlace': 'Escríbenos', 'url': 'https://wa.me/51900000000'},
        })
        for block, mark in BLOCK.items():
            self.assertIn(mark, full, block)

    def test_what_says_nothing_is_dropped_not_sent_empty(self):
        cleaned = contract.clean({
            'titulo': '  Título  ', 'saludo': '', 'etiqueta': None, 'momento_oro': False,
            'aviso': {'etiqueta': '', 'texto': ''},
            'datos': {'titulo': 'T', 'campos': [{'nombre': 'A', 'valor': ''}, {'nombre': 'B', 'valor': 'b'}], 'destacados': []},
            'boton': None,
        })
        # «A» had a value and has none left: the whole row goes, not just its value.
        self.assertEqual(cleaned, {'titulo': 'Título', 'datos': {'titulo': 'T', 'campos': [{'nombre': 'B', 'valor': 'b'}]}})

    def test_an_empty_block_does_not_print_an_empty_box(self):
        html = self.html({**minimal(), 'aviso': {'etiqueta': '', 'texto': ''}, 'saludo': '', 'boton': None})
        self.assertNotIn(BLOCK['aviso'], html)
        self.assertNotIn(BLOCK['boton'], html)

    def test_what_would_print_wrong_is_refused(self):
        for label, data in (
            ('a key the template does not know', minimal(pie='x')),
            ('a row key at the top', minimal(nombre='x')),
            ('no title', {k: v for k, v in minimal().items() if k != 'titulo'}),
            ('no paragraph', minimal(parrafos=[])),
            ('four paragraphs', minimal(parrafos=['a', 'b', 'c', 'd'])),
            ('an amount nobody formatted', minimal(detalle={'titulo': 'D', 'items': [{'nombre': 'A', 'valor': Decimal('10')}]})),
            ('a number', minimal(codigo={'etiqueta': 'C', 'valor': 123456})),
            ('two steps in progress', minimal(progreso={'titulo': 'E', 'pasos': [
                {'texto': 'a', 'actual': True}, {'texto': 'b', 'actual': True}]})),
            ('a step with no state', minimal(progreso={'titulo': 'E', 'pasos': [{'texto': 'a'}]})),
        ):
            with self.subTest(label), self.assertRaises(contract.ContractError):
                contract.validate(contract.clean(data))

    def test_a_link_only_goes_where_ours_go(self):
        for bad in (
            'javascript:alert(1)', 'data:text/html,x', 'http://evil.test/x', '//evil.test', '/relativa',
            'https://', 'https://evil.test/a b', 'https://evil.test/a\nb', 'ftp://x', 'HTTPS://x', 'https:evil.test',
            'http://tienda.test.evil.test/x', f'{SITE.replace("https", "http")}/x',
            # https, and still not an address anybody can be sure of
            'https://tienda.test@evil.test/x', 'https://ana:clave@evil.test/x', 'https:///evil.test', 'https://\\evil.test',
            'https://tienda.test\\@evil.test', 'https://[', 'https://[::1', 'https://:443/x', 'https://evil.test:puerto/x',
            'https://?x=1', 'https://#x', 'https://evil.test/a\\b', 'https://evil..test/x', 'https://.evil.test/x', 'https://evil_.test%/x',
        ):
            for field in ({'boton': {'texto': 'Ir', 'url': bad}}, {'baja_url': bad},
                          {'secundario': {'texto': 't', 'enlace': 'e', 'url': bad}},
                          {'imagen': {'url': bad, 'alt': 'a', 'enlace': f'{SITE}/'}}):
                with self.subTest(bad=bad, field=next(iter(field))), self.assertRaises(contract.ContractError):
                    contract.validate(contract.clean(minimal(**field)))
        # Another site is allowed when it is https: a courier's tracking page, WhatsApp.
        for good in (f'{SITE}/auth/reset-password?token=abc&next=%2Fx', SITE, 'https://wa.me/51900000000',
                     'https://rastreo.courier.test:8443/guia/123?x=1#estado', 'https://www.youtube.com/@BlackDogStorePer%C3%BA'):
            contract.validate(contract.clean(minimal(boton={'texto': 'Ir', 'url': good})))

    @override_settings(FRONTEND_URL='http://localhost:3000')
    def test_the_sites_own_address_is_trusted_as_the_operator_wrote_it(self):
        contract.validate(contract.clean(minimal(boton={'texto': 'Ir', 'url': 'http://localhost:3000/auth'})))
        with self.assertRaises(contract.ContractError):
            contract.validate(contract.clean(minimal(boton={'texto': 'Ir', 'url': 'http://localhost:3001/auth'})))


class EscapingTest(_Base):
    def test_a_customers_text_never_reaches_the_html_raw(self):
        nasty = '<script>alert("x")</script> & <img src=x onerror=1>'
        html = self.html({
            **minimal(titulo=nasty, saludo=nasty, parrafos=[nasty], etiqueta=nasty, preheader=nasty),
            'detalle': {'titulo': nasty, 'items': [{'nombre': nasty, 'nota': nasty, 'valor': nasty}],
                        'resumen': [{'nombre': nasty, 'valor': nasty}], 'total': {'nombre': nasty, 'valor': nasty}},
            'datos': {'titulo': nasty, 'campos': [{'nombre': nasty, 'valor': nasty}], 'destacados': [{'nombre': nasty, 'valor': nasty}]},
            'aviso': {'etiqueta': nasty, 'texto': nasty},
            'codigo': {'etiqueta': nasty, 'valor': nasty, 'nota': nasty},
            'progreso': {'titulo': nasty, 'pasos': [{'texto': nasty, 'fecha': nasty, 'actual': True}]},
            'boton': {'texto': nasty, 'url': f'{SITE}/x'},
            'secundario': {'texto': nasty, 'enlace': nasty, 'url': f'{SITE}/y'},
            'firma': {'cierre': nasty, 'nombre': nasty},
            'motivo': nasty, 'asunto': nasty,
        })
        self.assertNotIn('<script>alert', html)
        self.assertNotIn('<img src=x', html)
        self.assertGreater(html.count('&lt;script&gt;'), 20)

    def test_a_quote_cannot_close_an_attribute(self):
        html = self.html(minimal(imagen={'url': f'{SITE}/i.png', 'alt': 'x" onload="alert(1)', 'enlace': f'{SITE}/'}))
        self.assertNotIn('onload="alert', html)
        self.assertIn('x&quot; onload=&quot;alert(1)', html)


class PlainTextTest(_Base):
    def test_it_says_the_same_and_carries_the_same_links(self):
        data = {
            **minimal(etiqueta='Pedido · N.º 7', saludo='Hola, Ana:'),
            'codigo': {'etiqueta': 'Tu código', 'valor': '123456', 'nota': 'Vence pronto.'},
            'progreso': {'titulo': 'Estado', 'pasos': [
                {'texto': 'Recibido', 'fecha': '01/02', 'hecho': True},
                {'texto': 'En reparación', 'fecha': '02/02', 'actual': True},
                {'texto': 'Listo', 'pendiente': True}]},
            'detalle': {'titulo': 'Detalle', 'items': [{'nombre': 'iPhone', 'nota': 'Cant. 1', 'valor': 'S/ 4,299.00'}],
                        'resumen': [{'nombre': 'Envío', 'valor': 'S/ 15.00'}], 'total': {'nombre': 'Total', 'valor': 'S/ 4,314.00'}},
            'datos': {'titulo': 'Ficha', 'campos': [{'nombre': 'Modelo', 'valor': 'iPhone 15'}],
                      'destacados': [{'nombre': 'IMEI', 'valor': '356789012345678'}]},
            'aviso': {'etiqueta': 'Vigencia', 'texto': 'Hasta el viernes.'},
            'boton': {'texto': 'Ver mi pedido', 'url': f'{SITE}/orders'},
            'secundario': {'texto': '¿Dudas?', 'enlace': 'Escríbenos', 'url': 'https://wa.me/51900000000'},
            'firma': {'cierre': 'Gracias,', 'nombre': 'Carla'},
            'baja_url': f'{SITE}/baja',
        }
        text = self.text(data)
        for expected in (
            'PEDIDO · N.º 7', 'Título', 'Hola, Ana:', 'Un párrafo.', 'Tu código: 123456', 'Vence pronto.',
            '[x] Recibido — 01/02', '[>] En reparación (en curso) — 02/02', '[ ] Listo',
            'iPhone (Cant. 1): S/ 4,299.00', 'Envío: S/ 15.00', 'TOTAL: S/ 4,314.00',
            'Modelo: iPhone 15', 'IMEI: 356789012345678', 'VIGENCIA: Hasta el viernes.',
            f'Ver mi pedido:\n{SITE}/orders', 'https://wa.me/51900000000', 'Gracias,\nCarla\nEquipo Black Dog Store',
            'Recibes este correo por una prueba.', f'Dejar de recibir estos correos: {SITE}/baja',
        ):
            self.assertIn(expected, text)
        self.assertNotIn('<', text)

    def test_it_is_not_escaped_it_is_text(self):
        self.assertIn('Tom & Jerry <3', self.text(minimal(parrafos=['Tom & Jerry <3'])))


def shape(value):
    """The keys of a piece of data, all the way down. Lists are the union of their rows."""
    if isinstance(value, dict):
        return {key: shape(item) for key, item in value.items()}
    if isinstance(value, list):
        merged = {}
        for item in value:
            if isinstance(item, dict):
                for key, sub in shape(item).items():
                    merged.setdefault(key, sub)
        return [merged] if merged else []
    return None


def within(mine, theirs, path=''):
    """Every key of `mine` that the example does not have."""
    extra = []
    if isinstance(mine, dict) and isinstance(theirs, dict):
        for key, sub in mine.items():
            if key not in theirs:
                extra.append(f'{path}{key}')
            else:
                extra += within(sub, theirs[key], f'{path}{key}.')
    elif isinstance(mine, list) and isinstance(theirs, list) and mine and theirs:
        extra += within(mine[0], theirs[0], path)
    return extra


ORDER_CTX = {
    'order_id': 1234, 'customer_name': 'Ana Torres', 'customer_email': 'ana@correo.test',
    'customer_phone': '987654321', 'document_label': 'DNI', 'document_number': '12345678',
    'receipt_label': 'Boleta', 'delivery_label': 'Delivery Arequipa',
    'full_address': 'Av. Ejército 100, Cayma, Arequipa', 'reference': 'Frente al parque', 'notes': 'Tocar el timbre',
    'items': [
        {'product_name': 'iPhone 15 Pro', 'quantity': 1, 'price': Decimal('4299'), 'subtotal': Decimal('4299')},
        {'product_name': 'Funda', 'quantity': 2, 'price': Decimal('49.5'), 'subtotal': Decimal('99')},
    ],
    'total': Decimal('4348.00'), 'discount_amount': Decimal('50'), 'coupon_code': 'BIENVENIDA',
    'paid_at': None, 'store_name': 'Black Dog Store', 'store_address': 'Calle 1', 'store_city': 'Arequipa',
    'store_phone': '936449536', 'store_whatsapp_link': 'https://wa.me/51936449536',
    'store_whatsapp_number': '51936449536', 'store_ruc': '20610159886', 'store_legal_name': 'CMAU CORP E.I.R.L.',
    'store_email': '', 'warranty_note': 'Nuevos 1 año, seminuevos 6 meses.', 'warranty_url': '',
    'pickup_name': '', 'pickup_address': '', 'pickup_is_branch': False,
}


class BuildersTest(_Base):
    """What each kind of message says, in the shape of its example."""

    def built(self):
        expires = timezone.make_aware(timezone.datetime(2026, 3, 9, 15, 0))
        return {
            ('verify_email', '01-confirmar-correo'): builders.verify_email(brand='Black Dog Store', name='Ana', link=f'{SITE}/auth/verify-email?token=T'),
            ('password_reset', '07-restablecer-contrasena'): builders.password_reset(brand='Black Dog Store', name='Ana', link=f'{SITE}/auth/reset-password?token=T'),
            ('staff_invitation', '08-invitacion-personal'): builders.staff_invitation(
                company='Black Dog Store', first_name='Ana', role='Ventas', area='Tienda', expires_at=expires, link=f'{SITE}/invitacion?token=T'),
            ('order_confirmation', '02-confirmacion-compra'): builders.order_confirmation(ORDER_CTX, orders_url=f'{SITE}/orders', receipt_attached=True),
            ('internal_order', '09-aviso-interno-pedido'): builders.internal_order(ORDER_CTX, admin_url=f'{SITE}/admin/orders/1234'),
            ('notification', '10-aviso'): builders.notification(
                company='Black Dog Store', title='Tu equipo está listo para recoger', body='Puedes pasar a retirarlo. Orden ST-000042.',
                audience='customer', event_type='service.ready_for_pickup', target_type='repair_order', target_id=42,
                link=f'{SITE}/seguimiento/TOKEN'),
            ('smtp_test', '11-prueba-de-correo'): builders.smtp_test(brand='Black Dog Store'),
        }

    def test_every_kind_that_is_sent_has_a_builder_and_an_example(self):
        self.assertEqual({kind for kind, _ in self.built()}, set(mail.KINDS))

    def test_each_one_is_valid_and_stays_inside_its_example(self):
        for (kind, name), data in self.built().items():
            with self.subTest(kind):
                cleaned = contract.validate(contract.clean(data))
                self.assertEqual(within(shape(cleaned), shape(example(name))), [])
                self.assertEqual(cleaned['anio'], str(timezone.localdate().year))
                self.assertTrue(40 <= len(cleaned['preheader']) <= 90, (len(cleaned['preheader']), cleaned['preheader']))
                mail.render(kind, data, company=self.pilot)

    def test_money_and_dates_are_written_as_the_template_prints_them(self):
        self.assertEqual(fmt.money(Decimal('4299')), 'S/ 4,299.00')
        self.assertEqual(fmt.money('1234567.5'), 'S/ 1,234,567.50')
        self.assertEqual(fmt.money(Decimal('-50')), '– S/ 50.00')
        self.assertEqual(fmt.money(0), 'S/ 0.00')
        late = datetime.datetime(2026, 3, 10, 3, 30, tzinfo=datetime.timezone.utc)          # still the 9th in Lima
        self.assertEqual(fmt.date(late), '09/03/2026')
        self.assertEqual(fmt.short_date(late), '09/03')
        self.assertEqual(fmt.date(None), '')

    def test_the_buyers_confirmation_says_what_was_bought_and_where_it_goes(self):
        data = contract.clean(builders.order_confirmation(ORDER_CTX, orders_url=f'{SITE}/orders', receipt_attached=True))
        self.assertEqual(data['detalle']['items'][1], {'nombre': 'Funda', 'nota': 'Cant. 2 · S/ 49.50 c/u', 'valor': 'S/ 99.00'})
        self.assertEqual(data['detalle']['resumen'][0], {'nombre': 'Descuento (BIENVENIDA)', 'valor': '– S/ 50.00'})
        self.assertEqual(data['detalle']['total'], {'nombre': 'Total pagado', 'valor': 'S/ 4,348.00'})
        self.assertIn({'nombre': 'Dirección', 'valor': 'Av. Ejército 100, Cayma, Arequipa'}, data['datos']['destacados'])
        self.assertIn('Adjuntamos el resumen de tu pedido en PDF.', data['parrafos'])
        self.assertEqual(data['aviso']['texto'], 'Nuevos 1 año, seminuevos 6 meses.')

    def test_it_does_not_say_a_receipt_is_attached_when_it_is_not(self):
        data = builders.order_confirmation(ORDER_CTX, orders_url=f'{SITE}/orders', receipt_attached=False)
        self.assertNotIn('Adjuntamos', ' '.join(data['parrafos']))

    def test_what_an_order_does_not_have_is_left_out(self):
        ctx = {**ORDER_CTX, 'full_address': '', 'reference': 'x', 'notes': '', 'discount_amount': Decimal('0'),
               'pickup_name': 'Tienda principal', 'pickup_address': 'Calle 1', 'warranty_url': ''}
        data = contract.clean(builders.order_confirmation(ctx, orders_url=f'{SITE}/orders', receipt_attached=False))
        names = [row['nombre'] for row in data['datos']['destacados']]
        self.assertEqual(names, ['Punto de retiro'])
        self.assertNotIn('Descuento', str(data['detalle']['resumen']))

    def test_a_person_without_a_name_is_still_greeted(self):
        self.assertEqual(builders.password_reset(brand='B', name='', link=f'{SITE}/x')['saludo'], 'Hola:')

    def test_a_kind_nobody_declared_is_a_mistake(self):
        with self.assertRaises(ValueError):
            mail.render('otra_cosa', minimal(), company=self.pilot)


class SendersTest(_Base):
    """The e-mails the application already sent, now dressed — for the owner only."""

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user('ana.torres', 'ana@correo.test', 'x', first_name='Ana')

    def one(self):
        self.assertEqual(len(outbox_module.outbox), 1)
        return outbox_module.outbox[0]

    def assert_dressed(self, message, *, link):
        html = message.alternatives[0][0]
        self.assertEqual(message.alternatives[0][1], 'text/html')
        self.assertIn('logo-horizontal-on-light.png', html)
        self.assertIn(link.replace('&', '&amp;'), html)
        self.assertIn(link, message.body)                      # the text carries it too
        self.assertEqual(message.from_email, 'no-reply@tienda.test')

    def test_the_account_emails_wear_the_template_where_the_owner_is_the_storefront(self):
        from store.emails import send_password_reset_email, send_verification_email

        send_verification_email(self.user, 'TOKENVERIFICA')
        message = self.one()
        self.assertEqual(message.subject, 'Confirma tu correo · Black Dog Store')
        self.assertEqual(message.to, ['ana@correo.test'])
        self.assert_dressed(message, link=f'{SITE}/auth/verify-email?token=TOKENVERIFICA')
        self.assertIn('Hola, Ana:', message.body)

        outbox_module.outbox = []
        send_password_reset_email(self.user, 'TOKENRESET', next_path='/invitacion?token=' + 'a' * 20)
        message = self.one()
        self.assertEqual(message.subject, 'Restablece tu contraseña · Black Dog Store')
        self.assert_dressed(message, link=f'{SITE}/auth/reset-password?token=TOKENRESET&next=%2Finvitacion%3Ftoken%3D' + 'a' * 20)

    def test_without_that_setting_they_are_the_neutral_ones_they_were(self):
        from store.emails import send_password_reset_email, send_verification_email

        with override_settings(DEFAULT_STOREFRONT_COMPANY_SLUG=''):
            send_verification_email(self.user, 'T1')
            send_password_reset_email(self.user, 'T2')
        self.assertEqual(len(outbox_module.outbox), 2)
        for message in outbox_module.outbox:
            self.assertEqual(message.alternatives, [])
            blob = (message.subject + message.body).lower()
            for mark in ('black dog', 'cmau', 'octavio'):
                self.assertNotIn(mark, blob)
        self.assertIn('Verifica tu cuenta', outbox_module.outbox[0].subject)

    def invite(self, company, email):
        role = CompanyRole.objects.get(company=company, slug='ventas')
        admin = User.objects.create_user(f'admin_{company.slug}', f'admin@{company.slug}.test', 'x')
        from store.staff_views import _send_invitation_email

        invitation, raw, _ = create_invitation(
            company=company, email=email, first_name='Luis', last_name='Paz', role_id=role.pk, invited_by=admin)
        _send_invitation_email(invitation, raw)
        return invitation, raw

    def test_the_owners_staff_invitation_wears_it(self):
        invitation, raw = self.invite(self.pilot, 'luis@correo.test')
        message = self.one()
        self.assertEqual(message.subject, 'Invitación para unirte a Black Dog Store')
        self.assert_dressed(message, link=f'{SITE}/invitacion?token={raw}')
        self.assertIn(f'caduca el {fmt.date(invitation.expires_at)}', message.body)
        self.assertIn('Rol: Ventas', message.body)

    def test_another_companys_invitation_carries_nothing_of_the_owner(self):
        """The reason the template is not simply «the» template."""
        other = other_company()
        _invitation, raw = self.invite(other, 'luis@otra.test')
        message = self.one()
        self.assertEqual(message.alternatives, [])
        self.assertIn(f'{SITE}/invitacion?token={raw}', message.body)
        blob = message.subject + message.body
        self.assertIn('Otra Tienda', blob)
        for mark in ('Black Dog', *PILOT_MARKS):
            self.assertNotIn(mark, blob)

    def test_a_template_that_cannot_be_filled_does_not_cost_the_email(self):
        from unittest import mock

        from store.emails import send_password_reset_email, send_verification_email

        with mock.patch('store.mail.service.chevron.render', side_effect=RuntimeError('boom')), \
                self.assertLogs('store.emails', level='ERROR') as captured:
            send_password_reset_email(self.user, 'TOKENSECRETO')
            send_verification_email(self.user, 'TOKENSECRETO')
        self.assertEqual(len(outbox_module.outbox), 2)
        for message, path in zip(outbox_module.outbox, ('reset-password', 'verify-email')):
            self.assertEqual(message.alternatives, [])
            self.assertEqual(message.to, ['ana@correo.test'])
            self.assertIn(f'{SITE}/auth/{path}?token=TOKENSECRETO', message.body)
        self.assertNotIn('TOKENSECRETO', '\n'.join(captured.output))

    @override_settings(ALLOWED_HOSTS=['tienda.test'])
    def test_an_address_that_cannot_be_read_is_not_taken_for_the_owners(self):
        from django.test import RequestFactory

        from store.emails import send_verification_email

        send_verification_email(self.user, 'T1', request=RequestFactory().get('/', HTTP_HOST='tienda.test'))
        self.assertEqual(len(self.one().alternatives), 1)

        outbox_module.outbox = []
        send_verification_email(self.user, 'T2', request=RequestFactory().get('/', HTTP_HOST='otra cosa.test'))
        message = self.one()
        self.assertEqual(message.alternatives, [])
        self.assertIn(f'{SITE}/auth/verify-email?token=T2', message.body)

    def test_an_invitation_that_cannot_be_dressed_goes_plain(self):
        from unittest import mock

        with mock.patch('store.mail.service.chevron.render', side_effect=RuntimeError('boom')), \
                self.assertLogs('store.staff_views', level='ERROR') as captured:
            _invitation, raw = self.invite(self.pilot, 'luis@correo.test')
        message = self.one()
        self.assertEqual(message.alternatives, [])
        self.assertEqual(message.to, ['luis@correo.test'])
        self.assertIn(f'{SITE}/invitacion?token={raw}', message.body)
        self.assertNotIn(raw, '\n'.join(captured.output))

    def test_an_email_the_server_did_not_take_is_tried_once_not_twice(self):
        """
        «Could not be sent» is not «could not be dressed». A server that took
        the message and then failed to say so has already delivered it: sending
        the plain one as well puts the same link in two e-mails, and doubles the
        wait of a request when the server is down.
        """
        from unittest import mock

        from django.core.mail.message import EmailMessage

        from store.emails import send_password_reset_email, send_verification_email

        for label, act, logger_name in (
            ('verify', lambda: send_verification_email(self.user, 'TOKENSECRETO'), 'store.emails'),
            ('reset', lambda: send_password_reset_email(self.user, 'TOKENSECRETO'), 'store.emails'),
            ('invitation', lambda: self.invite(self.pilot, 'luis@correo.test'), 'store.staff_views'),
        ):
            with self.subTest(label):
                with mock.patch.object(EmailMessage, 'send', autospec=True, side_effect=TimeoutError('sin respuesta')) as attempt, \
                        self.assertLogs(logger_name, level='ERROR') as captured:
                    act()
                self.assertEqual(attempt.call_count, 1)
                self.assertEqual(len(attempt.call_args.args[0].alternatives), 1)      # the dressed one
                logged = '\n'.join(captured.output)
                self.assertNotIn('TOKENSECRETO', logged)
                self.assertNotIn('token=', logged)

    def test_no_link_is_written_to_a_log(self):
        """Every way it can go wrong, with every logger listening."""
        from unittest import mock

        from django.core.mail.message import EmailMessage

        from store.emails import send_password_reset_email, send_verification_email

        records = []

        class Keep(logging.Handler):
            def emit(self, record):
                records.append(self.format(record) + repr(record.args))

        handler = Keep(level=logging.DEBUG)
        handler.setFormatter(logging.Formatter('%(message)s\n%(exc_text)s'))
        watched = [logging.getLogger()] + [
            logger for logger in logging.root.manager.loggerDict.values() if isinstance(logger, logging.Logger)]
        for logger in watched:
            logger.addHandler(handler)
        raws = []
        try:
            for broken in (
                mock.patch('store.mail.service.chevron.render', side_effect=RuntimeError('boom')),
                mock.patch.object(EmailMessage, 'send', side_effect=TimeoutError('sin respuesta')),
                mock.patch('store.mail.contract.validate', side_effect=contract.ContractError('boton.url: no es una dirección')),
            ):
                with broken:
                    send_verification_email(self.user, 'TOKENQUENOSEREGISTRA')
                    send_password_reset_email(self.user, 'TOKENQUENOSEREGISTRA', next_path='/invitacion?token=' + 'b' * 20)
                    raws.append(self.invite(self.pilot, f'luis{len(raws)}@correo.test')[1])
                    User.objects.filter(username__startswith='admin_').delete()
        finally:
            for logger in watched:
                logger.removeHandler(handler)
        logged = '\n'.join(records)
        self.assertGreaterEqual(len(records), 9)                    # it did go wrong, and it was said
        self.assertNotIn('TOKENQUENOSEREGISTRA', logged)
        self.assertNotIn('token=', logged)
        self.assertNotIn('token%3D', logged)
        for raw in raws:
            self.assertNotIn(raw, logged)


@override_settings(
    FRONTEND_URL=SITE, DEFAULT_STOREFRONT_COMPANY_SLUG=PILOT, REQUIRE_EMAIL_VERIFICATION=True,
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend', ALLOWED_HOSTS=['*'],
)
class OtherStorefrontTest(TestCase):
    """
    An account e-mail asked for from ANOTHER company's address.

    The setting says which storefront this installation serves by default; a
    request's host can say otherwise. Somebody who registers at another shop's
    address did not register «en la web de» the template's owner, and must not
    be written to with its name, its address and its phone.
    """

    OTHER_HOST = 'otra-tienda.plataforma.test'
    OWNER_HOST = f'{PILOT}.plataforma.test'

    def setUp(self):
        cache.clear()
        outbox_module.outbox = []
        other_company()

    def ask(self, host):
        """Register, ask for the verification again, ask for a reset: the three account e-mails."""
        from rest_framework.test import APIClient

        client = APIClient()
        name = f'u{abs(hash(host)) % 10**8}'
        email = f'{name}@correo.test'
        res = client.post('/api/auth/register/', {
            'username': name, 'email': email, 'password': 'Clave-larga-2026', 'password_confirm': 'Clave-larga-2026',
            'first_name': 'Ana', 'last_name': 'Torres',
        }, format='json', HTTP_HOST=host)
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(client.post('/api/auth/resend-verification/', {'email': email}, format='json', HTTP_HOST=host).status_code, 200)
        cache.clear()
        self.assertEqual(client.post('/api/auth/password-reset/request/', {'email': email}, format='json', HTTP_HOST=host).status_code, 200)
        self.assertEqual(len(outbox_module.outbox), 3)
        return outbox_module.outbox

    def test_from_another_shops_address_they_are_neutral(self):
        for message in self.ask(self.OTHER_HOST):
            self.assertEqual(message.alternatives, [])
            blob = message.subject + message.body
            for mark in ('Black Dog', 'logo-horizontal-on-light', *PILOT_MARKS):
                self.assertNotIn(mark, blob)
            self.assertIn(f'{SITE}/auth/', message.body)

    def test_from_the_owners_address_or_from_no_shops_they_wear_it(self):
        for host in (self.OWNER_HOST, 'plataforma.test', 'www.plataforma.test'):
            with self.subTest(host):
                outbox_module.outbox = []
                cache.clear()
                for message in self.ask(host):
                    self.assertEqual(len(message.alternatives), 1)
                    self.assertIn('· Black Dog Store', message.subject)


class OrderEmailsTest(_Base):
    """A paid order: the buyer's confirmation and the shop's own notice."""

    def order(self, company, **extra):
        from store.tests import _p3_order

        return _p3_order(company, customer_name='Ana <b>Torres</b>', **extra)

    def test_the_owners_confirmation_wears_the_template_and_keeps_its_receipt(self):
        from store.email_services import send_order_confirmation_email

        order = self.order(self.pilot)
        self.assertTrue(send_order_confirmation_email(order))
        message = outbox_module.outbox[0]
        html = message.alternatives[0][0]
        self.assertEqual(message.subject, f'Recibimos tu pedido N.º {order.pk}')
        self.assertEqual(message.to, ['cliente-p3@example.invalid'])
        self.assertIn('logo-horizontal-on-light.png', html)
        self.assertIn('Tu compra está confirmada', html)
        self.assertIn('S/ 100.00', html)
        self.assertIn('Ana &lt;b&gt;Torres&lt;/b&gt;', html)                # the buyer's name, escaped
        self.assertNotIn('<b>Torres', html)
        self.assertIn('TOTAL PAGADO: S/ 100.00', message.body)
        self.assertIn(f'{SITE}/orders', message.body)
        self.assertEqual([name.endswith('.pdf') for name, _content, _type in message.attachments], [True])
        self.assertIn('Adjuntamos el resumen de tu pedido en PDF.', message.body)

    def test_without_the_receipt_it_does_not_claim_one(self):
        from unittest import mock

        from store.email_services import send_order_confirmation_email

        order = self.order(self.pilot)
        with mock.patch('store.email_services._pdf_services.generate_order_receipt_pdf', side_effect=RuntimeError('boom')), \
                self.assertLogs('store.email_services', level='ERROR'):
            self.assertTrue(send_order_confirmation_email(order))
        message = outbox_module.outbox[0]
        self.assertEqual(message.attachments, [])
        self.assertNotIn('Adjuntamos', message.body)
        order.refresh_from_db()
        self.assertTrue(order.email_send_error.startswith('pdf_skip: '))

    def test_another_companys_order_is_the_neutral_one_with_nothing_of_the_owner(self):
        from store.email_services import send_internal_order_notification, send_order_confirmation_email
        from store.models import CompanySettings

        other = other_company()
        CompanySettings.objects.filter(company=other).update(order_notification_email='ventas@otra.test')
        order = self.order(Company.objects.get(pk=other.pk))                  # its settings, as just written
        send_order_confirmation_email(order)
        send_internal_order_notification(order)
        self.assertEqual(len(outbox_module.outbox), 2)
        for message in outbox_module.outbox:
            blob = message.subject + message.body + str(message.alternatives)
            for mark in ('Black Dog', 'logo-horizontal-on-light', *PILOT_MARKS):
                self.assertNotIn(mark, blob)
        self.assertIn('Confirmación de pedido', outbox_module.outbox[0].subject)

    def test_the_shops_own_notice_wears_it_too(self):
        from store.email_services import send_internal_order_notification
        from store.models import CompanySettings

        CompanySettings.objects.filter(company=self.pilot).update(order_notification_email='ventas@tienda.test')
        order = self.order(self.pilot)
        self.assertTrue(send_internal_order_notification(order))
        message = outbox_module.outbox[0]
        self.assertEqual(message.to, ['ventas@tienda.test'])
        self.assertEqual(message.subject, f'Nueva venta pagada · Pedido N.º {order.pk}')
        self.assertIn(f'{SITE}/admin/orders/{order.pk}', message.body)
        self.assertIn('Nueva venta pagada', message.alternatives[0][0])

    def test_a_template_that_cannot_be_filled_does_not_cost_the_confirmation(self):
        from unittest import mock

        from store.email_services import send_order_confirmation_email

        order = self.order(self.pilot)
        with mock.patch('store.mail.service.chevron.render', side_effect=RuntimeError('boom')), \
                self.assertLogs('store.email_services', level='ERROR'):
            self.assertTrue(send_order_confirmation_email(order))
        message = outbox_module.outbox[0]
        self.assertIn('Confirmación de pedido', message.subject)             # the plain one went
        self.assertEqual(len(message.attachments), 1)

    def test_a_template_that_cannot_be_filled_does_not_cost_the_shops_notice(self):
        from unittest import mock

        from store.email_services import send_internal_order_notification
        from store.models import CompanySettings

        CompanySettings.objects.filter(company=self.pilot).update(order_notification_email='ventas@tienda.test')
        order = self.order(Company.objects.get(slug=PILOT))
        with mock.patch('store.mail.service.chevron.render', side_effect=RuntimeError('boom')), \
                self.assertLogs('store.email_services', level='ERROR'):
            self.assertTrue(send_internal_order_notification(order))
        self.assertEqual(len(outbox_module.outbox), 1)
        message = outbox_module.outbox[0]
        self.assertEqual(message.to, ['ventas@tienda.test'])
        self.assertEqual(message.alternatives, [])
        self.assertIn(str(order.pk), message.subject)

    def test_sending_the_confirmation_again_wears_it_too(self):
        from store.email_services import resend_order_confirmation_email

        order = self.order(self.pilot)
        self.assertEqual(resend_order_confirmation_email(order), {'had_pdf': True})
        self.assertEqual(len(outbox_module.outbox), 1)
        message = outbox_module.outbox[0]
        self.assertEqual(message.to, ['cliente-p3@example.invalid'])
        self.assertEqual(message.subject, f'Recibimos tu pedido N.º {order.pk}')
        self.assertIn('Tu compra está confirmada', message.alternatives[0][0])
        self.assertEqual(len(message.attachments), 1)


class PreviewTest(_Base):
    """A page to look at them, for whoever is developing — and for nobody else."""

    def test_it_does_not_exist_in_production(self):
        for path in ('/api/dev/mail-preview/', '/api/dev/mail-preview/01-confirmar-correo/'):
            self.assertEqual(self.client.get(path).status_code, 404, path)

    @override_settings(DEBUG=True)
    def test_while_developing_it_lists_and_shows_every_example(self):
        index = self.client.get('/api/dev/mail-preview/')
        self.assertEqual(index.status_code, 200)
        names = re.findall(r'href="([\w-]+)/"', index.content.decode())
        self.assertEqual(names, sorted(path.stem for path in EXAMPLES.glob('*.json')))
        for name in names:
            with self.subTest(name):
                page = self.client.get(f'/api/dev/mail-preview/{name}/')
                self.assertEqual(page.status_code, 200)
                self.assertIn('Compra con respaldo.', page.content.decode())
                text = self.client.get(f'/api/dev/mail-preview/{name}/', {'formato': 'texto'})
                self.assertEqual(text['Content-Type'], 'text/plain; charset=utf-8')
                self.assertTrue(text.content.decode().startswith('Asunto: '))

    @override_settings(DEBUG=True)
    def test_it_sends_nothing_and_shows_only_what_is_in_the_examples(self):
        self.client.get('/api/dev/mail-preview/02-confirmacion-compra/')
        self.assertEqual(outbox_module.outbox, [])
        self.assertEqual(self.client.get('/api/dev/mail-preview/no-existe/').status_code, 404)
        self.assertEqual(self.client.get('/api/dev/mail-preview/..%2F..%2Fsettings/').status_code, 404)
