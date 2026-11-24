"""
STOCK-REAL-READY — simulación completa de la carga del stock real (equipos nuevos y seminuevos), con datos sintéticos.

Recorre, con los servicios y las vistas REALES (APIClient con permisos reales), lo que el propietario hará con su stock:

     1. primera carga de inventario inicial (nuevos y seminuevos) con las dos plantillas que genera el servidor;
     2. segunda carga del mismo modelo (inventario inicial por tandas);
     3. volver a subir el mismo archivo: ningún equipo se duplica (ni con dos vistas previas antes de registrar);
     4. IMEI repetido, dentro del archivo y contra lo que ya está registrado: rechazado;
     5. variante que no existe pero que la paleta del modelo admite: error de la fila que dice cómo arreglarlo (la carga
        nunca crea variantes); creada en el catálogo, el mismo archivo entra;
     6. variante que la paleta del modelo no admite: rechazada;
     7. equipo nuevo recibido de un proveedor (recepción de mercadería);
     8. venta en caja de ESE equipo (se elige el equipo exacto, no el más antiguo);
     9. un seminuevo cargado queda pendiente de inspección: ni en la web ni en caja;
    10. inspección aprobada + foto real confirmada + precio + publicación explícita: aparece en la web;
    11. venta en la web del equipo exacto elegido por el comprador;
    12. el stock de cada estante es igual a sus equipos disponibles, y el Kardex lo explica (check_unit_stock = 0);
    13. aislamiento: un usuario de la sucursal A no carga ni ve la B; otra empresa no ve nada.

Cada escenario compara el stock (BranchStock) y el Kardex (StockMovement) antes y después con lo esperado.

EL MOTOR DE LA SIMULACIÓN ES PORTÁTIL. Todo lo que hay entre las marcas «PORTABLE DRIVER» sólo usa código de
producción (nada de `store.tests`): se copia tal cual al ejecutor `sim_stock_real.py`, que lo corre dentro de la imagen
de producción contra una COPIA aislada de la base, con una empresa sintética única y deshaciendo todo al final.

IMEI sintéticos: empiezan por 3500000, pasan el dígito de control (Luhn) y no son de ningún equipo real.
Con `-v 2` imprime la bitácora legible de cada paso.
"""
# ======================================================================================================== PORTABLE DRIVER
import io
import itertools
import re
from decimal import Decimal
from types import SimpleNamespace

from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db.models import Count, Sum
from django.utils.text import slugify
from rest_framework.test import APIClient

from store import checkout_services, device_catalog, model_palette
from store import stock_unit_services
from store.company_provisioning import provision_company_access_defaults
from store.device_identity import luhn_check_digit
from store.models import (
    Branch, BranchStock, CartItem, Company, CompanyRole, Membership, MembershipBranchAccess,
    MembershipRoleAssignment, Order, OrderUnit, PaymentTransaction, Product, StockMovement, StockUnit, Supplier,
    UnitPhoto, UserProfile,
)
from store.views import _SignedNotificationMixin

XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
API = '/api/admin/inventory'
LOADER_CAPS = ['company.view', 'inventory.view', 'inventory.adjust', 'costs.view', 'products.view', 'products.manage']
INSPECTOR_CAPS = ['company.view', 'inventory.view', 'inventory.units.inspect']
CASHIER_CAPS = ['company.view', 'sales.pos.use']
BRANCH_ONLY_CAPS = ['company.view', 'inventory.view', 'inventory.adjust', 'products.view']
#: The storefront resolves the company from the first label of the host; `.invalid` never resolves on a network.
HOST_SUFFIX = 'sim.invalid'


class SimulationFailure(AssertionError):
    """A check of the simulation did not hold. The message names the scenario and what was expected."""


def synthetic_imei(n: int) -> str:
    """A Luhn-valid IMEI that is obviously synthetic: 3500000 + 7 digits + check digit. Never a real device's."""
    body = f'3500000{n:07d}'
    return body + luhn_check_digit(body)


def png_bytes() -> bytes:
    """A small real PNG (the server decodes it: the name and the declared type decide nothing)."""
    from PIL import Image

    out = io.BytesIO()
    Image.new('RGBA', (64, 64), (40, 90, 200, 255)).save(out, format='PNG')
    return out.getvalue()


class StockRealSimulation:
    """
    The owner's real-stock journey with synthetic data. `tag` makes every name unique (company slugs, users), so the
    same driver runs in a test database and on an isolated copy of production without touching anybody's data.
    `log` receives one readable line per step.
    """

    def __init__(self, tag='simstock', log=print, imei_base=0):
        self.tag = slugify(tag)[:30]
        self.log = log
        self._imei = (synthetic_imei(imei_base + n) for n in itertools.count(1))
        self.results = []
        self.scenario = ''
        self.storage_root = ''

    # ------------------------------------------------------------------ checks and logging
    def check(self, condition, message):
        if not condition:
            raise SimulationFailure(f'[{self.scenario}] {message}')

    def step(self, text):
        self.log(f'    · {text}')

    def ok(self, response, status, what):
        body = getattr(response, 'content', b'')[:600]
        self.check(response.status_code == status,
                   f'{what}: se esperaba HTTP {status} y llegó {response.status_code}: {body!r}')
        return response

    # ------------------------------------------------------------------ fixtures (production code only)
    def company(self, suffix, name):
        company = Company.objects.create(name=name, slug=f'{self.tag}-{suffix}'[:50], legal_name='', tax_id='')
        provision_company_access_defaults(company)
        company.refresh_from_db()
        return company

    def member(self, company, suffix, caps, branches=None):
        user = User.objects.create_user(username=f'{self.tag}-{suffix}'[:150], password=None)
        membership = Membership.objects.create(
            user=user, company=company, role=UserProfile.ROLE_CUSTOMER,
            branch_access_mode=(Membership.ACCESS_MODE_SELECTED if branches is not None
                                else Membership.ACCESS_MODE_ALL))
        role = CompanyRole.objects.create(company=company, name=f'Simulación {suffix}', slug=f'sim-{suffix}',
                                          capabilities=sorted(caps))
        MembershipRoleAssignment.objects.create(membership=membership, role=role)
        for branch in branches or []:
            MembershipBranchAccess.objects.create(membership=membership, branch=branch)
        client = APIClient()
        client.force_authenticate(user=user)
        return user, client

    def catalogue(self, company, admin):
        """Attributes with their roles, a model with a palette and the variants a shop would have created."""
        opt = device_catalog.create_option
        cap = device_catalog.create_attribute(company=company, name='Capacidad', role='capacity', sort_order=0)
        col = device_catalog.create_attribute(company=company, name='Color', kind='color', role='color', sort_order=1)
        sim = device_catalog.create_attribute(company=company, name='Tipo de SIM', role='sim', sort_order=2)
        reg = device_catalog.create_attribute(company=company, name='Región', role='region', sort_order=3)
        o = {
            '128': opt(company=company, attribute_id=cap.pk, label='128 GB', sort_order=0),
            '256': opt(company=company, attribute_id=cap.pk, label='256 GB', sort_order=1),
            'negro': opt(company=company, attribute_id=col.pk, label='Negro', swatch_hex='#111111', sort_order=0),
            'azul': opt(company=company, attribute_id=col.pk, label='Azul', swatch_hex='#1d4ed8', sort_order=1),
            # In the company's vocabulary, but NOT in the model's palette (scenario 6).
            'rojo': opt(company=company, attribute_id=col.pk, label='Rojo', swatch_hex='#b91c1c', sort_order=2),
            'esim': opt(company=company, attribute_id=sim.pk, label='eSIM', sort_order=0),
            'usa': opt(company=company, attribute_id=reg.pk, label='USA', sort_order=0),
        }
        model = device_catalog.create_model(company=company, name='Teléfono Sim')
        model_palette.set_palette(model, option_ids=[o[k].pk for k in ('128', '256', 'negro', 'azul')], actor=admin)
        pro = device_catalog.create_model(company=company, name='Teléfono Sim Pro')
        model_palette.set_palette(pro, option_ids=[o[k].pk for k in ('256', 'negro', 'esim', 'usa')], actor=admin)
        v = {
            'new128': self.variant(company, model, 'Teléfono Sim 128 GB Negro', '3000.00', [o['128'], o['negro']],
                                   admin),
            'new256': self.variant(company, model, 'Teléfono Sim 256 GB Negro', '3500.00', [o['256'], o['negro']],
                                   admin),
            # A used variant carries no price of its own: each device has its own (placeholder 1.00, never sold).
            'used128': self.variant(company, model, 'Teléfono Sim 128 GB Azul seminuevo', '1.00',
                                    [o['128'], o['azul']], admin, line='used'),
            'pro': self.variant(company, pro, 'Teléfono Sim Pro 256 GB Negro eSIM USA', '5200.00',
                                [o['256'], o['negro'], o['esim'], o['usa']], admin),
        }
        return model, pro, o, v

    def variant(self, company, model, name, price, options, admin, line='new'):
        product = Product.objects.create(
            company=company, name=name, slug=slugify(name)[:48], price=Decimal(price),
            inventory=0, is_active=True)
        stock_unit_services.set_serialized(product, serialized=True, requires_imei=True, actor=admin)
        return device_catalog.set_variant(product, model_id=model.pk, condition_line=line,
                                          option_ids=[x.pk for x in options], actor=admin)

    def build(self):
        self.log(f'Preparación: empresa sintética «{self.tag}-a», dos sucursales, catálogo con paleta, proveedor y '
                 f'usuarios con permisos reales')
        self.a = self.company('a', 'Tienda Simulación A')
        self.main = self.a.default_inventory_branch
        self.north = Branch.objects.create(company=self.a, name='Sucursal Norte Sim', is_active=True)
        self.loader, self.loader_api = self.member(self.a, 'cargador', LOADER_CAPS)
        self.inspector, self.inspector_api = self.member(self.a, 'inspector', INSPECTOR_CAPS)
        self.cashier, self.cashier_api = self.member(self.a, 'cajero', CASHIER_CAPS)
        self.main_only, self.main_only_api = self.member(self.a, 'solo-principal', BRANCH_ONLY_CAPS,
                                                         branches=[self.main])
        self.model, self.pro, self.opt, self.v = self.catalogue(self.a, self.loader)
        self.supplier = Supplier.objects.create(company=self.a, name='Proveedor Sintético SAC',
                                                document_number='20000000001')
        self.host = f'{self.a.slug}.{HOST_SUFFIX}'
        # A second company, same catalogue names (its own rows): what it may see of the first is tested in 10 and 13.
        self.b = self.company('b', 'Tienda Simulación B')
        self.loader_b, self.api_b = self.member(self.b, 'cargador-b', LOADER_CAPS)
        # B's inspector HOLDS `inventory.units.inspect`: a 403 would stop at the permission, never reach the tenant.
        _inspector_b, self.inspector_api_b = self.member(self.b, 'inspector-b', INSPECTOR_CAPS)
        _model_b, _pro_b, _o_b, self.v_b = self.catalogue(self.b, self.loader_b)
        self.host_b = f'{self.b.slug}.{HOST_SUFFIX}'
        self.step(f'sucursales: «{self.main.name}» y «{self.north.name}»; variantes: '
                  + ', '.join(p.name for p in self.v.values()))

    # ------------------------------------------------------------------ the API, as the panel calls it
    def q(self, company=None):
        return f'?company={(company or self.a).pk}'

    def template(self, line, client=None, company=None):
        response = (client or self.loader_api).get(f'{API}/units/import/template/{self.q(company)}', {'line': line})
        self.ok(response, 200, f'plantilla {line}')
        return response.content

    def filled(self, line, rows, client=None, company=None, name=None):
        """The template the server generates, with `rows` ({header: value}) from row 3 (row 2 is the example)."""
        from openpyxl import load_workbook

        book = load_workbook(io.BytesIO(self.template(line, client, company)))
        sheet = book.worksheets[0]
        headers = [cell.value for cell in sheet[1]]
        for offset, row in enumerate(rows):
            for header, value in row.items():
                self.check(header in headers, f'la plantilla {line} no tiene la columna «{header}»')
                sheet.cell(row=3 + offset, column=headers.index(header) + 1, value=value)
        out = io.BytesIO()
        book.save(out)
        return out.getvalue()

    def preview(self, line, data, *, client=None, company=None, entry_kind='opening',
                reason='Inventario inicial (simulación)', expect=201):
        upload = SimpleUploadedFile(f'stock-{line}.xlsx', data, content_type=XLSX)
        response = (client or self.loader_api).post(
            f'{API}/units/import/preview/{self.q(company)}',
            {'file': upload, 'line': line, 'entry_kind': entry_kind, 'reason': reason}, format='multipart')
        self.ok(response, expect, f'vista previa {line}')
        return response.json()

    def apply(self, job, *, client=None, company=None, expect=200):
        response = (client or self.loader_api).post(f'{API}/units/import/{job["id"]}/apply/{self.q(company)}')
        self.ok(response, expect, f'registrar la carga #{job["id"]}')
        return response.json()

    @staticmethod
    def row_errors(job):
        return {row['row']: ' '.join(row['errors']) for row in job['rows'] if row['errors']}

    def new_row(self, serial, *, branch=None, model='Teléfono Sim', capacity='128 GB', colour='Negro', **extra):
        row = {'Sucursal': (branch or self.main).name, 'Modelo': model, 'Capacidad': capacity, 'Color': colour,
               'IMEI 1': next(self._imei), 'Serie': serial, 'Proveedor': self.supplier.name, 'Costo': 2500,
               'Fecha de ingreso': '2026-11-02', 'Observaciones (notas internas)': 'Vitrina 1 (simulación)'}
        row.update(extra)
        return row

    def used_row(self, serial, price, **extra):
        row = self.new_row(serial, colour='Azul', **{'Costo': 1200, 'Precio de venta': price,
                                                     'Estado estético': 'Excelente', 'Salud de batería (%)': 88,
                                                     'Estado funcional': 'Sin fallas',
                                                     'Accesorios': 'Caja y cable', 'Garantía (días)': 90,
                                                     'Detalles para el comprador': 'Rayón leve en el marco.',
                                                     'Defectos': '', **extra})
        return row

    # ------------------------------------------------------------------ stock and Kardex, before and after
    def shelves(self, company=None):
        """(branch, product) -> (BranchStock.quantity, available devices), for the company's serialized products."""
        company = company or self.a
        found = {}
        for branch, product, quantity in BranchStock.objects.filter(
                branch__company=company, product__is_serialized=True).values_list(
                'branch__name', 'product__name', 'quantity'):
            found[(branch, product)] = [quantity, 0]
        for row in StockUnit.objects.filter(company=company, status='available').values(
                'branch__name', 'product__name').annotate(n=Count('id')):
            found.setdefault((row['branch__name'], row['product__name']), [0, 0])[1] = row['n']
        return {key: tuple(value) for key, value in found.items()}

    def kardex(self, company=None):
        """movement type -> (lines, units), for the company."""
        return {row['movement_type']: (row['n'], row['q']) for row in StockMovement.objects.filter(
            branch__company=company or self.a).values('movement_type').annotate(n=Count('id'), q=Sum('quantity'))}

    def snapshot(self):
        return self.shelves(), self.kardex()

    def deltas(self, before, after):
        shelves_b, kardex_b = before
        shelves_a, kardex_a = after
        stock = {}
        for key in sorted(set(shelves_b) | set(shelves_a)):
            b, a = shelves_b.get(key, (0, 0)), shelves_a.get(key, (0, 0))
            if a[0] != b[0]:
                stock[key] = (b[0], a[0])
        kardex = {}
        for kind in sorted(set(kardex_b) | set(kardex_a)):
            b, a = kardex_b.get(kind, (0, 0)), kardex_a.get(kind, (0, 0))
            if a != b:
                kardex[kind] = (a[0] - b[0], a[1] - b[1])
        return stock, kardex

    def expect(self, before, *, stock=None, kardex=None):
        """Every change of stock and Kardex since `before` is exactly the expected one; and the invariant holds."""
        after = self.snapshot()
        got_stock, got_kardex = self.deltas(before, after)
        named = {(branch.name, product.name): change for (branch, product), change in (stock or {}).items()}
        wanted_stock = {key: (before[0].get(key, (0, 0))[0], before[0].get(key, (0, 0))[0] + change)
                        for key, change in named.items() if change}
        self.check(got_stock == wanted_stock, f'stock: se esperaba {wanted_stock} y cambió {got_stock}')
        wanted_kardex = dict(kardex or {})
        got_units = {kind: units for kind, (_lines, units) in got_kardex.items()}
        want_units = {kind: units for kind, (_lines, units) in wanted_kardex.items()}
        self.check(got_units == want_units, f'Kardex (unidades): se esperaba {want_units} y cambió {got_units}')
        for kind, (lines, _units) in wanted_kardex.items():
            if lines is not None:
                self.check(got_kardex[kind][0] == lines,
                           f'Kardex (líneas de {kind}): se esperaban {lines} y hay {got_kardex[kind][0]}')
        self.invariant()
        for (branch, product), (b, a) in got_stock.items():
            self.step(f'stock {branch} · {product}: {b} → {a}')
        for kind, (lines, units) in got_kardex.items():
            self.step(f'Kardex {kind}: +{lines} línea(s), {units:+d} unidad(es)')
        if not got_stock and not got_kardex:
            self.step('stock y Kardex sin cambios')
        return got_stock, got_kardex

    def invariant(self, company=None):
        for (branch, product), (quantity, available) in self.shelves(company).items():
            self.check(quantity == available,
                       f'el estante {branch} · {product} dice {quantity} y tiene {available} equipo(s) disponible(s)')

    def check_unit_stock(self, company=None):
        out = io.StringIO()
        try:
            call_command('check_unit_stock', '--company', (company or self.a).slug, stdout=out)
        except CommandError as exc:
            raise SimulationFailure(f'[{self.scenario}] check_unit_stock salió con {exc.returncode}: '
                                    f'{out.getvalue()} {exc}') from None
        return out.getvalue().strip().splitlines()[-1]

    # ------------------------------------------------------------------ scenarios
    def run(self, number, title, body):
        self.scenario = f'{number}. {title}'
        cache.clear()       # request limits live in THIS process's memory: one scenario never throttles the next
        self.log(f'\n[{number}] {title}')
        before = self.snapshot()
        result = {'n': number, 'title': title, 'ok': False}
        try:
            body(before, result)
        except SimulationFailure as exc:
            result['error'] = str(exc)
            self.results.append(result)
            self.log(f'    FALLA: {exc}')
            raise
        result['ok'] = True
        self.results.append(result)
        self.log('    OK')

    def run_all(self):
        """
        The thirteen scenarios under the driver's OWN settings, whoever runs it: photos go to a temporary folder that
        is deleted at the end (never to the evidence volume — a rolled-back database does not take a file back), the
        synthetic hosts are allowed, nothing redirects to https, and nothing leaves the machine (no SUNAT, e-mail or
        WhatsApp).
        """
        import shutil
        import tempfile

        from django.conf import settings
        from django.test.utils import override_settings

        self.storage_root = tempfile.mkdtemp(prefix='sim-stock-real-')
        try:
            with override_settings(
                    EVIDENCE_STORAGE_BACKEND='filesystem', EVIDENCE_STORAGE_ROOT=self.storage_root,
                    ALLOWED_HOSTS=[*settings.ALLOWED_HOSTS, f'.{HOST_SUFFIX}', 'testserver'],
                    SECURE_SSL_REDIRECT=False, FISCAL_ENABLED=False, WHATSAPP_PROVIDER='fake',
                    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend'):
                self.log(f'Fotos en una carpeta temporal ({self.storage_root}), que se borra al terminar; '
                         f'SUNAT, correo y WhatsApp apagados.')
                self.build()
                for number, (title, body) in enumerate(self.scenarios(), start=1):
                    self.run(number, title, body)
        finally:
            shutil.rmtree(self.storage_root, ignore_errors=True)
        return self.results

    def scenarios(self):
        return [
            ('Primera carga de inventario inicial (nuevos y seminuevos)', self.s1_first_load),
            ('Segunda carga del mismo modelo', self.s2_second_load),
            ('El mismo archivo otra vez: sin duplicados', self.s3_reimport),
            ('IMEI repetido en el archivo y contra lo registrado', self.s4_repeated_imei),
            ('Variante inexistente que la paleta admite', self.s5_missing_variant),
            ('Variante que la paleta no admite', self.s6_incompatible_variant),
            ('Equipo nuevo recibido de un proveedor', self.s7_supplier_receipt),
            ('Venta en caja de ese equipo exacto', self.s8_pos_sale),
            ('Seminuevo pendiente de inspección: ni web ni caja', self.s9_used_pending),
            ('Inspección + foto real + precio + publicación: en la web', self.s10_publish_used),
            ('Venta en la web del seminuevo exacto', self.s11_web_sale),
            ('Stock y Kardex de todo el recorrido', self.s12_ledger),
            ('Aislamiento entre sucursales y entre empresas', self.s13_isolation),
        ]

    def s1_first_load(self, before, result):
        v = self.v
        new_rows = [self.new_row('SIMN-0001'), self.new_row('SIMN-0002'), self.new_row('SIMN-0003'),
                    self.new_row('SIMN-0004', capacity='256 GB', **{'IMEI 2': next(self._imei)}),
                    self.new_row('SIMN-0005', branch=self.north, model='Teléfono Sim Pro', capacity='256 GB',
                                 SIM='eSIM', **{'Versión (región / país de venta)': 'USA'})]
        used_rows = [self.used_row('SIMU-0001', 1999), self.used_row('SIMU-0002', 1500),
                     self.used_row('SIMU-0003', 1700, **{'Estado funcional': 'Con fallas',
                                                        'Defectos': 'Face ID no funciona'})]
        self.first_new_file = self.filled('new', new_rows)
        new_job = self.preview('new', self.first_new_file)
        used_job = self.preview('used', self.filled('used', used_rows))
        for job, count, line in ((new_job, 5, 'nuevos'), (used_job, 3, 'seminuevos')):
            counts = job['counts']
            self.check((counts['create'], counts['skip'], counts['error']) == (count, 1, 0),
                       f'vista previa de {line}: {counts} {self.row_errors(job)}')
            simulation = job['summary']['simulation']
            self.check(simulation['units'][StockMovement.INITIAL_STOCK] == count, f'simulación {simulation}')
            self.step(f'vista previa {line}: {count} a registrar, 1 omitida (fila de ejemplo), 0 errores; simulación: '
                      + '; '.join(f'{s["branch"]} · {s["product"]} {s["stock_before"]} → {s["stock_after"]}'
                                  for s in simulation['shelves']))
        self.check(not StockUnit.objects.filter(company=self.a).exists(), 'la vista previa escribió equipos')
        self.expect(before)                                   # previews write nothing
        self.first_new_job = self.apply(new_job)
        self.first_used_job = self.apply(used_job)
        self.expect(before, stock={(self.main, v['new128']): 3, (self.main, v['new256']): 1,
                                   (self.north, v['pro']): 1, (self.main, v['used128']): 3},
                    # One Kardex line per reception (branch × product × supplier × day): 3 + 1 + 1 + 3 devices.
                    kardex={StockMovement.INITIAL_STOCK: (4, 8)})
        unit = StockUnit.objects.get(company=self.a, serial_number='SIMN-0001')
        self.check((unit.supplier_id, unit.cost, str(unit.received_at.date()), unit.notes)
                   == (self.supplier.pk, Decimal('2500.00'), '2026-11-02', 'Vitrina 1 (simulación)'),
                   'proveedor, costo, fecha de ingreso y observaciones del equipo nuevo')
        self.check(StockUnit.objects.get(company=self.a, serial_number='SIMN-0004').imei2, 'IMEI 2 guardado')
        used = StockUnit.objects.get(company=self.a, serial_number='SIMU-0001')
        self.check((used.sale_price, used.battery_health, used.cosmetic_grade.label, used.functional_state,
                    used.accessories, used.warranty_days, used.public_notes)
                   == (Decimal('1999.00'), 88, 'Excelente', 'full', 'Caja y cable', 90, 'Rayón leve en el marco.'),
                   'detalles del seminuevo')
        faulty = StockUnit.objects.get(company=self.a, serial_number='SIMU-0003')
        self.check((faulty.functional_state, faulty.defects) == ('with_faults', 'Face ID no funciona'), 'defectos')
        for unit in StockUnit.objects.filter(company=self.a, product__condition_line='used'):
            self.check((unit.inspection_status, unit.published_online) == ('pending', False),
                       f'{unit.serial_number} entró {unit.inspection_status}/{unit.published_online}')
        self.step('seminuevos: los 3 «pendiente de inspección» y sin publicar; nada inspecciona ni publica desde Excel')
        self.step(self.check_unit_stock())
        result.update(units=8)

    def s2_second_load(self, before, result):
        rows = [self.new_row('SIMN-0101'), self.new_row('SIMN-0102')]
        self.second_new_file = self.filled('new', rows)
        job = self.preview('new', self.second_new_file, reason='Inventario inicial, segunda tanda')
        self.check(job['counts']['create'] == 2 and job['counts']['error'] == 0, f'{self.row_errors(job)}')
        shelf = job['summary']['simulation']['shelves'][0]
        self.check((shelf['stock_before'], shelf['stock_after']) == (3, 5), f'simulación {shelf}')
        self.second_new_job = self.apply(job)
        self.expect(before, stock={(self.main, self.v['new128']): 2}, kardex={StockMovement.INITIAL_STOCK: (1, 2)})

    def s3_reimport(self, before, result):
        again = self.preview('new', self.second_new_file, reason='Inventario inicial, segunda tanda')
        errors = self.row_errors(again)
        self.check(again['counts']['create'] == 0 and len(errors) == 2, f'reimportación: {again["counts"]}')
        self.check(all('ya está registrado' in text for text in errors.values()), f'mensajes {errors}')
        self.step(f'el mismo archivo otra vez: 2 errores («{next(iter(errors.values()))[:90]}…»)')
        refused = self.apply(again, expect=400)
        self.step(f'registrar con errores: 400 «{refused["detail"][:80]}»')
        replay = self.apply(self.second_new_job)                  # applying an applied job changes nothing
        self.check(replay['status'] == 'applied', f'segundo registro de la carga aplicada: {replay["status"]}')
        # Two previews of ONE new file before anybody registers: the second registration is refused under lock.
        data = self.filled('new', [self.new_row('SIMN-0201')])
        first, second = self.preview('new', data), self.preview('new', data)
        self.apply(first)
        late = self.apply(second, expect=400)
        self.step(f'dos vistas previas del mismo archivo: la 1.ª entra, la 2.ª se rechaza («{late["detail"][:80]}»)')
        self.expect(before, stock={(self.main, self.v['new128']): 1}, kardex={StockMovement.INITIAL_STOCK: (1, 1)})
        self.check(StockUnit.objects.filter(company=self.a, serial_number='SIMN-0201').count() == 1, 'duplicado')

    def s4_repeated_imei(self, before, result):
        existing = StockUnit.objects.get(company=self.a, serial_number='SIMN-0001').imei
        another = StockUnit.objects.get(company=self.a, serial_number='SIMN-0002').imei
        twice = next(self._imei)
        rows = [self.new_row('SIMN-0301', **{'IMEI 1': twice}), self.new_row('SIMN-0302', **{'IMEI 1': twice}),
                self.new_row('SIMN-0303', **{'IMEI 1': existing}),
                self.new_row('SIMN-0304', **{'IMEI 2': another}),
                self.new_row('SIMN-0305', **{'IMEI 1': '350000000000001'})]   # bad check digit
        job = self.preview('new', self.filled('new', rows))
        errors = self.row_errors(job)
        self.check(job['counts']['create'] == 1 and len(errors) == 4, f'{job["counts"]} {errors}')
        self.check('repetido en el archivo (fila 3)' in errors[4], errors)
        self.check('ya está registrado en otro equipo' in errors[5], errors)
        # As an IMEI 2 too: it is the IMEI 1 of another registered device.
        self.check('ya está registrado en otro equipo' in errors[6] and another in errors[6], errors)
        self.check('dígito de control' in errors[7], errors)
        for row, text in sorted(errors.items()):
            self.step(f'fila {row}: {text[:100]}')
        self.apply(job, expect=400)
        self.expect(before)

    def s5_missing_variant(self, before, result):
        rows = [self.new_row('SIMN-0401', capacity='256 GB', colour='Azul')]
        data = self.filled('new', rows)
        job = self.preview('new', data)
        errors = self.row_errors(job)
        self.check(job['counts']['create'] == 0 and 'no tiene esa variante' in errors.get(3, '')
                   and 'Créala en Catálogo › Modelos' in errors.get(3, ''), f'{errors}')
        self.step(f'fila 3: {errors[3]}')
        self.expect(before)
        # The fix the message names: create the variant in the catalogue (from the palette), count it by unit.
        created = device_catalog.create_variant_product(
            self.a, self.model, condition_line='new', option_ids=[self.opt['256'].pk, self.opt['azul'].pk],
            actor=self.loader, request=None)
        self.ok(self.loader_api.post(f'{API}/units/products/{created.pk}/serialization/{self.q()}',
                                     {'is_serialized': True, 'requires_imei': True}, format='json'), 200,
                'controlar la variante nueva por número de serie')
        self.v['new256azul'] = Product.objects.get(pk=created.pk)
        self.step(f'variante creada en el catálogo: «{created.name}» (sin precio, sin publicar, sin stock)')
        job = self.preview('new', data)
        self.check(job['counts']['create'] == 1 and not self.row_errors(job), f'{self.row_errors(job)}')
        self.apply(job)
        self.expect(before, stock={(self.main, self.v['new256azul']): 1}, kardex={StockMovement.INITIAL_STOCK: (1, 1)})
        self.step('el mismo archivo entra ahora; la variante sigue sin precio ni publicación: se habilita aparte')

    def s6_incompatible_variant(self, before, result):
        job = self.preview('new', self.filled('new', [self.new_row('SIMN-0501', colour='Rojo')]))
        errors = self.row_errors(job)
        self.check(job['counts']['create'] == 0 and 'no admite' in errors.get(3, '') and 'Rojo' in errors.get(3, ''),
                   f'{errors}')
        self.step(f'fila 3: {errors[3]}')
        self.apply(job, expect=400)
        self.expect(before)

    def s7_supplier_receipt(self, before, result):
        value = next(self._imei)
        body = {'branch': self.main.pk, 'supplier': self.supplier.pk, 'document_ref': 'F001-SIM0007',
                'notes': 'Compra sintética', 'lines': [{'kind': 'units', 'product': self.v['new128'].pk,
                                                         'units': [{'serial_number': 'SIMR-0001', 'imei': value,
                                                                    'cost': '2450.00'}]}]}
        response = self.loader_api.post(f'{API}/receipts/confirm/{self.q()}', body, format='json',
                                        HTTP_IDEMPOTENCY_KEY=f'{self.tag}-rcp-1')
        self.ok(response, 201, 'confirmar la recepción')
        self.received = StockUnit.objects.get(company=self.a, imei=value)
        self.check((self.received.supplier_id, self.received.status, self.received.cost)
                   == (self.supplier.pk, 'available', Decimal('2450.00')), 'equipo recibido')
        self.step(f'recibido {self.received.serial_number} de «{self.supplier.name}», documento F001-SIM0007')
        self.expect(before, stock={(self.main, self.v['new128']): 1}, kardex={StockMovement.PURCHASE_ENTRY: (1, 1)})
        # From now on, what arrives for this product at this branch is a purchase, not opening stock.
        job = self.preview('new', self.filled('new', [self.new_row('SIMN-0601')]))
        self.check(job['counts']['error'] == 1 and 'no es inventario inicial' in self.row_errors(job).get(3, ''),
                   f'{job["counts"]} {self.row_errors(job)}')
        self.step(f'inventario inicial después de una compra: {self.row_errors(job)[3][:110]}')

    def s8_pos_sale(self, before, result):
        body = {'branch': self.main.pk, 'payment_method': 'cash', 'amount_received': '10000.00',
                'terms_confirmed': True, 'idempotency_key': f'{self.tag}-pos-1',
                'items': [{'product': self.v['new128'].pk, 'quantity': 1,
                           'units': [{'id': self.received.pk, 'method': 'scanner'}]}]}
        response = self.ok(self.cashier_api.post(f'/api/admin/pos/sales/{self.q()}', body, format='json'), 201,
                           'venta en caja')
        self.received.refresh_from_db()
        record = OrderUnit.objects.get(unit=self.received)
        oldest = StockUnit.objects.get(company=self.a, serial_number='SIMN-0001')
        self.check((self.received.status, record.order_id, oldest.status) == ('sold', self.received.order_id,
                                                                               'available'),
                   'se vendió el equipo exacto, no el más antiguo')
        self.check(Decimal(response.json()['total']) == Decimal('3000.00'), 'precio de la variante')
        self.step(f'vendido {self.received.serial_number} (el recibido, no el más antiguo) a S/ 3000.00, '
                  f'pedido #{record.order_id}')
        self.expect(before, stock={(self.main, self.v['new128']): -1}, kardex={StockMovement.SALE_EXIT: (1, 1)})

    def s9_used_pending(self, before, result):
        public = APIClient(HTTP_HOST=self.host)
        page = self.ok(public.get(f'/api/catalog/models/{self.model.slug}/'), 200, 'ficha pública del modelo').json()
        self.check(page['used_units'] == [], f'seminuevos en la web antes de inspeccionar: {page["used_units"]}')
        pending = StockUnit.objects.get(company=self.a, serial_number='SIMU-0001')
        held = public.post('/api/cart/add/', {'session_key': f'{self.tag}-carrito', 'product': self.v['used128'].pk,
                                              'unit': str(pending.public_id)}, format='json')
        self.check(held.status_code == 404, f'carrito con un seminuevo pendiente: {held.status_code}')
        body = {'branch': self.main.pk, 'payment_method': 'cash', 'amount_received': '10000.00',
                'terms_confirmed': True, 'idempotency_key': f'{self.tag}-pos-used',
                'items': [{'product': self.v['used128'].pk, 'quantity': 1,
                           'units': [{'id': pending.pk, 'method': 'scanner'}]}]}
        refused = self.cashier_api.post(f'/api/admin/pos/sales/{self.q()}', body, format='json')
        self.check((refused.status_code, refused.json().get('code')) == (409, 'unit_not_inspected'),
                   f'caja: {refused.status_code} {refused.content[:200]!r}')
        publish = self.loader_api.post(f'{API}/units/{pending.pk}/publication/{self.q()}', {'published': True},
                                       format='json')
        self.check(publish.status_code == 409, f'publicar sin inspección ni foto: {publish.status_code}')
        self.step('web: no aparece; carrito: 404; caja: 409 unit_not_inspected; publicar: 409 '
                  f'({", ".join(publish.json().get("problems") or publish.json().get("reasons") or [])})')
        self.expect(before)

    def s10_publish_used(self, before, result):
        unit = StockUnit.objects.get(company=self.a, serial_number='SIMU-0001')
        self.ok(self.inspector_api.post(f'{API}/units/{unit.pk}/inspection/{self.q()}', {'decision': 'approved'},
                                        format='json'), 200, 'inspección aprobada')
        upload = SimpleUploadedFile('equipo.png', png_bytes(), content_type='image/png')
        self.ok(self.loader_api.post(f'{API}/units/{unit.pk}/photos/{self.q()}', {'file': upload, 'view': 'front'},
                                     format='multipart'), 200, 'subir la foto real')
        photo = UnitPhoto.objects.get(unit=unit)
        early = self.loader_api.post(f'{API}/units/{unit.pk}/publication/{self.q()}', {'published': True},
                                     format='json')
        self.check(early.status_code == 409, f'publicar con la foto sin confirmar: {early.status_code}')
        self.ok(self.inspector_api.post(f'{API}/units/{unit.pk}/photos/{photo.pk}/attestation/{self.q()}',
                                        {'attested': True}, format='json'), 200, 'confirmar la foto')
        self.check(unit.sale_price == Decimal('1999.00'), 'precio del equipo')
        self.ok(self.loader_api.post(f'{API}/units/{unit.pk}/publication/{self.q()}', {'published': True},
                                     format='json'), 200, 'publicar')
        unit.refresh_from_db()
        self.check((unit.inspection_status, unit.published_online) == ('approved', True), 'inspeccionado y publicado')
        cards = self.ok(APIClient(HTTP_HOST=self.host).get(f'/api/catalog/models/{self.model.slug}/'), 200,
                        'ficha pública').json()['used_units']
        self.check([card['id'] for card in cards] == [str(unit.public_id)], f'web: {cards}')
        self.check(unit.imei not in str(cards), 'la web no muestra el IMEI')
        self.step('inspección aprobada → foto subida (sin confirmar: publicar 409) → foto confirmada → publicado; '
                  'la web muestra SÓLO este equipo, sin su IMEI')
        self.web_unit = unit
        # The other company's web, on a model of the same name and address: not a trace of this device.
        other = self.ok(APIClient(HTTP_HOST=self.host_b).get(f'/api/catalog/models/{self.model.slug}/'), 200,
                        'ficha pública en la empresa B')
        self.check(other.json()['used_units'] == [] and str(unit.public_id) not in other.content.decode(),
                   'la web de la empresa B muestra un seminuevo de A')
        self.step('la web de la empresa B (mismo nombre de modelo) no lo muestra')
        self.expect(before)

    def s11_web_sale(self, before, result):
        public = APIClient(HTTP_HOST=self.host)
        session = f'{self.tag}-web'
        held = public.post('/api/cart/add/', {'session_key': session, 'product': self.v['used128'].pk,
                                              'unit': str(self.web_unit.public_id)}, format='json')
        self.ok(held, 200, 'el comprador elige el equipo')
        # The checkout VIEW is skipped: it opens a payment with the provider. Its services build the order, and the
        # payment is confirmed by the real `_confirm` of a gateway notification that passed its checks.
        branch = checkout_services.resolve_fulfillment_branch(self.a)
        self.check(branch == self.main, f'sucursal de despacho de la web: {branch}')
        items = list(CartItem.objects.filter(session_key=session).select_related('product'))
        lines = checkout_services.lines_from_cart(self.a, branch, session, items)
        subtotal = checkout_services.validate_lines_and_subtotal(branch, lines)
        pricing = checkout_services.price_checkout(self.a, subtotal)
        order = checkout_services.create_pending_order(
            company=self.a, branch=branch, lines=lines, pricing=pricing,
            details=checkout_services.CustomerDetails(
                name='Cliente Sintético', email='cliente.sintetico@example.com', phone='900000000',
                document_type='DNI', document_number='00000000', delivery_method='pickup', receipt_type='boleta',
                accepted_terms=True, accepted_warranty_policy=True),
            cart_session_key=session)
        attempt = PaymentTransaction.objects.create(
            order=order, provider='izipay', transaction_id=f'SIM{order.pk:015d}', amount=order.total, currency='PEN')
        confirmed = _SignedNotificationMixin()._confirm(attempt, SimpleNamespace(
            signed=True, response_code='00', pay_method='CARD', authorization_code='SIM', reference_number='SIM',
            unique_id=f'SIM{attempt.pk}'))
        order.refresh_from_db()
        self.check((confirmed, order.paid, order.status) == ('paid', True, Order.Status.PAID),
                   f'confirmación del pago: {confirmed} {order.status}')
        self.web_unit.refresh_from_db()
        line = order.items.get()
        self.check((self.web_unit.status, line.stock_unit_id, line.price) == ('sold', self.web_unit.pk,
                                                                              Decimal('1999.00')),
                   'vendido el seminuevo elegido a su precio')
        self.check(OrderUnit.objects.get(order=order).unit_id == self.web_unit.pk, 'OrderUnit del equipo exacto')
        others = StockUnit.objects.filter(company=self.a, product=self.v['used128']).exclude(pk=self.web_unit.pk)
        self.check(set(others.values_list('status', flat=True)) == {'available'}, 'los otros seminuevos siguen')
        after = self.ok(public.get(f'/api/catalog/models/{self.model.slug}/'), 200, 'ficha pública').json()
        self.check(after['used_units'] == [], 'un seminuevo vendido sigue en la web')
        self.step(f'pedido web #{order.pk}: {self.web_unit.serial_number} a S/ 1999.00; los otros 2 siguen en stock')
        self.expect(before, stock={(self.main, self.v['used128']): -1},
                    # The cart's hold takes it off the shelf (manual_exit); the payment gives the hold back
                    # (manual_entry) and sells it (sale_exit): net -1, every step a Kardex line.
                    kardex={StockMovement.MANUAL_EXIT: (1, 1), StockMovement.MANUAL_ENTRY: (1, 1),
                            StockMovement.SALE_EXIT: (1, 1)})

    def s12_ledger(self, before, result):
        """The whole journey: each shelf is its available devices, and the Kardex adds up to it."""
        self.invariant()
        self.step(self.check_unit_stock())
        for (branch, product), (quantity, available) in sorted(self.shelves().items()):
            entries = StockMovement.objects.filter(branch__company=self.a, branch__name=branch,
                                                   product__name=product)
            net = 0
            for kind, quantity_moved in entries.values_list('movement_type', 'quantity'):
                net += quantity_moved * (1 if kind in StockMovement.ENTRY_TYPES else -1)
            self.check(net == quantity, f'Kardex de {branch} · {product}: suma {net}, stock {quantity}')
            self.step(f'{branch} · {product}: stock {quantity} = {available} equipo(s) = Kardex neto {net}')
        self.expect(before)

    def s13_isolation(self, before, result):
        # Branch scope: a user of the main branch only.
        template = self.template('new', client=self.main_only_api)
        from openpyxl import load_workbook

        book = load_workbook(io.BytesIO(template))
        formulas = ' '.join(rule.formula1 or '' for rule in book.worksheets[0].data_validations.dataValidation)
        example = ' '.join(str(cell.value) for cell in book.worksheets[0][2])
        self.check(self.main.name in formulas and self.north.name not in formulas + example,
                   f'la plantilla del usuario de una sucursal ofrece: {formulas}')
        rows = [self.new_row('SIMN-0701', branch=self.north, model='Teléfono Sim Pro', capacity='256 GB',
                             SIM='eSIM', **{'Versión (región / país de venta)': 'USA', 'Costo': None,
                                            'Proveedor': None})]
        job = self.preview('new', self.filled('new', rows, client=self.main_only_api), client=self.main_only_api)
        self.check('No tienes acceso a la sucursal' in self.row_errors(job).get(3, ''), f'{self.row_errors(job)}')
        self.step(f'usuario de «{self.main.name}» carga en «{self.north.name}»: {self.row_errors(job)[3]}')
        self.check(self.main_only_api.get(f'{API}/units/import/{self.first_new_job["id"]}/report/{self.q()}')
                   .status_code == 404, 'el usuario de una sucursal ve una carga que incluye otra')
        listed = self.ok(self.main_only_api.get(f'{API}/units/{self.q()}', {'page_size': 200}), 200, 'equipos')
        branches_seen = {row['branch_name'] for row in listed.json()['results']}
        self.check(branches_seen == {self.main.name}, f'equipos que ve el usuario de una sucursal: {branches_seen}')
        self.step('su plantilla sólo ofrece su sucursal; la carga que toca la otra: 404; su lista de equipos: '
                  'sólo su sucursal')

        # Another company: sees nothing of this one, and its own load knows nothing of it.
        b, api_b, v_b = self.b, self.api_b, self.v_b
        b_before = (self.shelves(b), self.kardex(b))
        for path in (f'units/import/{self.first_new_job["id"]}/report/', f'units/import/{self.first_new_job["id"]}/void/'):
            self.check(api_b.get(f'{API}/{path}{self.q(b)}').status_code == 404, f'la empresa B ve {path}')
        self.check(api_b.patch(f'{API}/units/{self.received.pk}/{self.q(b)}', {'notes': 'x'}, format='json')
                   .status_code == 404, 'la empresa B edita un equipo de A')
        pending_a = StockUnit.objects.get(company=self.a, serial_number='SIMU-0002')
        inspected = self.inspector_api_b.post(f'{API}/units/{pending_a.pk}/inspection/{self.q(b)}',
                                              {'decision': 'approved'}, format='json')
        self.check(inspected.status_code == 404, f'un inspector de B inspecciona un equipo de A: {inspected.status_code}')
        pending_a.refresh_from_db()
        self.check(pending_a.inspection_status == 'pending', 'la inspección de B cambió un equipo de A')
        self.check(api_b.post(f'{API}/units/import/{self.first_new_job["id"]}/apply/{self.q(b)}').status_code == 404,
                   'la empresa B registra una carga de A')
        self.check(api_b.get(f'{API}/units/{self.q(b)}').json()['results'] == [], 'la empresa B ve equipos')
        # «Sucursal principal» is also B's own default branch name; «Sucursal Norte Sim» exists only in A.
        foreign = [{'Sucursal': self.north.name, 'Modelo': 'Teléfono Sim', 'Capacidad': '128 GB', 'Color': 'Negro',
                    'IMEI 1': next(self._imei), 'Serie': 'SIMB-0001'}]
        job = self.preview('new', self.filled('new', foreign, client=api_b, company=b), client=api_b, company=b)
        self.check('No existe la sucursal' in self.row_errors(job).get(3, ''), f'{self.row_errors(job)}')
        # The SAME IMEI as a device of company A: B is not told it exists anywhere.
        shared = StockUnit.objects.get(company=self.a, serial_number='SIMN-0002').imei
        own = [{'Sucursal': b.default_inventory_branch.name, 'Modelo': 'Teléfono Sim', 'Capacidad': '128 GB',
                'Color': 'Negro', 'IMEI 1': shared, 'Serie': 'SIMB-0002'}]
        job = self.preview('new', self.filled('new', own, client=api_b, company=b), client=api_b, company=b)
        self.check(job['counts']['create'] == 1 and not self.row_errors(job),
                   f'la empresa B se entera de un IMEI de A: {self.row_errors(job)}')
        self.apply(job, client=api_b, company=b)
        self.check(self.deltas(b_before, (self.shelves(b), self.kardex(b)))[0]
                   == {(b.default_inventory_branch.name, v_b['new128'].name): (0, 1)}, 'stock de B')
        self.invariant(b)
        self.step(self.check_unit_stock(b) + f' (empresa B)')
        self.step('empresa B: cargas, equipos y fichas de A → 404; su archivo no encuentra la sucursal de A; carga '
                  'un IMEI que A también tiene sin enterarse (su web ya se comprobó en el escenario 10)')
        self.expect(before)                              # company A: nothing changed
# ==================================================================================================== END PORTABLE DRIVER

import sys  # noqa: E402
import tempfile  # noqa: E402
from pathlib import Path  # noqa: E402

from django.test import TestCase, override_settings  # noqa: E402


def _verbose() -> bool:
    argv = ' '.join(sys.argv)
    return bool(re.search(r'(-v|--verbosity)[ =]?[23]\b', argv))


class StockRealSimulationTest(TestCase):
    """The thirteen scenarios, in order, on one synthetic company (and a second one for isolation)."""

    def test_the_real_stock_journey(self):
        """
        The driver brings its own settings: run as in the production image (evidence storage pointing at the REAL
        volume, no synthetic hosts allowed), it writes no photo there and leaves no folder of its own behind.
        """
        lines = []
        with tempfile.TemporaryDirectory(prefix='stock-real-volume-') as volume, override_settings(
                EVIDENCE_STORAGE_BACKEND='filesystem', EVIDENCE_STORAGE_ROOT=volume, ALLOWED_HOSTS=['testserver']):
            simulation = StockRealSimulation('simstock-test', log=lines.append)
            try:
                results = simulation.run_all()
            finally:
                if _verbose():
                    sys.stderr.write('\n' + '\n'.join(lines) + '\n')
            self.assertEqual([path for path in Path(volume).rglob('*')], [], 'la simulación escribió en el volumen')
        self.assertEqual([r['n'] for r in results if r['ok']], list(range(1, 14)))
        self.assertTrue(simulation.storage_root)
        self.assertFalse(Path(simulation.storage_root).exists(), 'la carpeta temporal de fotos quedó')


class TemplateExampleImeiTest(TestCase):
    """The example row of both stock templates carries an IMEI that is valid AND obviously synthetic (3500000…)."""

    def test_the_example_imei_is_luhn_valid_and_synthetic(self):
        from openpyxl import load_workbook

        from store import unit_import_services
        from store.device_identity import clean_imei

        for line in ('new', 'used'):
            book = load_workbook(io.BytesIO(unit_import_services.stock_template_bytes(line, ['Tienda'])))
            sheet = book.worksheets[0]
            headers = [cell.value for cell in sheet[1]]
            value = str(sheet.cell(row=2, column=headers.index('IMEI 1') + 1).value)
            self.assertEqual(clean_imei(value), value)
            self.assertTrue(value.startswith('3500000'), value)
