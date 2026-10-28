import { execFileSync } from "node:child_process";
import { test, expect, type APIRequestContext, type Page } from "@playwright/test";

/**
 * INV-QA-E · apoyo del recorrido de inventario primero.
 *
 * Todo lo que escribe esta prueba lleva una MARCA ÚNICA POR CORRIDA (`TAG`) en el nombre del modelo, así que una
 * segunda corrida no choca con la primera y `limpiar()` borra sólo lo marcado. Los IMEI son sintéticos (Luhn válido,
 * inventados): ninguno es un dato real. El backend se toca con `manage.py shell` sobre `E2E_BACKEND_DIR`, igual que
 * `serial-pick.spec.ts`; la interfaz y la API se recorren con la sesión de `dev_admin`.
 */

/** Sólo letras: una palabra con dígitos y 8+ caracteres parece una serie y el catálogo no busca ese modelo. */
export const RUN = Date.now().toString(36).replace(/\d/g, (digit) => "abcdefghij"[Number(digit)]).toUpperCase();
/** Una sola palabra: el reconocedor de texto la deja como parte del nombre del modelo. */
export const TAG = `Qae${RUN}`;
export const BACKEND_DIR = process.env.E2E_BACKEND_DIR ?? "../backend";
export const COMPANY_SLUG = "black-dog-store";
export const OTHER_SLUG = `qae-otra-${RUN.toLowerCase()}`;
export const OTHER_USER = `qae_otra_${RUN.toLowerCase()}`;
export const OTHER_PASSWORD = "Qae-Otra-2026!";

/** 14 dígitos inventados + dígito de control (Luhn). */
export function imei(seed: number): string {
  const body = `35${String(Date.now()).slice(-8)}${String(seed).padStart(4, "0")}`;
  let total = 0;
  for (let index = 0; index < body.length; index += 1) {
    let digit = Number(body[body.length - 1 - index]);
    if (index % 2 === 0) { digit *= 2; if (digit > 9) digit -= 9; }
    total += digit;
  }
  return body + String((10 - (total % 10)) % 10);
}

/** Ejecuta Python en el shell de Django; el programa imprime una línea `E2E_OUT {json}`. */
export function shell<T = unknown>(code: string, data: Record<string, unknown> = {}): T {
  const prelude = `
import json, os
from decimal import Decimal
data = json.loads(os.environ['E2E_DATA'])
def out(value):
    print('E2E_OUT ' + json.dumps(value, default=str))
`;
  const text = execFileSync("python3", ["manage.py", "shell", "-c", prelude + code], {
    cwd: BACKEND_DIR,
    stdio: ["ignore", "pipe", "pipe"],
    env: { ...process.env, E2E_DATA: JSON.stringify({ tag: TAG, slug: COMPANY_SLUG, other_slug: OTHER_SLUG, ...data }) },
    maxBuffer: 16 * 1024 * 1024,
  }).toString();
  const line = text.split("\n").find((row) => row.startsWith("E2E_OUT "));
  if (!line) throw new Error(`el programa no respondió: ${text.slice(-400)}`);
  return JSON.parse(line.slice("E2E_OUT ".length)) as T;
}

export type Seed = {
  branch: number;
  branchName: string;
  category: number;
  categoryName: string;
  cable: number; cableName: string; cableCode: string;
  idle: number; idleName: string; idleSlug: string;
  ready: number; readyName: string;
  other: { company: number; product: number; barcode: string; branch: number; name: string };
};

/**
 * El catálogo de la corrida: atributos Capacidad/Color (si faltan), el modelo A con una variante que YA existe, un
 * accesorio con 100 u., dos productos de la empresa listos para publicar y otra empresa con su usuario y su producto.
 */
export function sembrar(): Seed {
  return shell<Seed>(`
from django.contrib.auth import get_user_model
from django.db import transaction
from store import device_catalog, inventory_services, product_codes
from store.company_provisioning import provision_company_access_defaults
from store.models import (
    Category, Company, CompanyRole, Membership, MembershipRoleAssignment, Product, ProductBarcode, StockMovement,
    VariantAttribute, VariantOption,
)
User = get_user_model()
def ensure(product):
    with transaction.atomic():
        return product_codes.ensure_code(product)
company = Company.objects.get(slug=data['slug'])
branch = company.default_inventory_branch
actor = User.objects.get(username='dev_admin')
tag = data['tag']

capacity = VariantAttribute.objects.filter(company=company, name__iregex=r'^(capacidad|almacenamiento|capacity|storage)$').first() \\
    or device_catalog.create_attribute(company=company, name='Capacidad')
colour = VariantAttribute.objects.filter(company=company, kind='color').first() \\
    or device_catalog.create_attribute(company=company, name='Color', kind='color')
def option(attribute, label, **extra):
    return VariantOption.objects.filter(company=company, attribute=attribute, label__iexact=label).first() \\
        or device_catalog.create_option(company=company, attribute_id=attribute.pk, label=label, **extra)
gb128 = option(capacity, '128 GB'); option(capacity, '256 GB')
negro = option(colour, 'Negro', swatch_hex='#111111'); option(colour, 'Azul', swatch_hex='#1d4ed8')

category = Category.objects.filter(company=company).order_by('pk').first() \\
    or Category.objects.create(company=company, name='Accesorios', slug='accesorios-qae')

# Modelo A: existe, con una variante (128 GB Negro, nuevo) que la lista va a REUTILIZAR.
model = device_catalog.create_model(company=company, name='iPhone 13 ' + tag, is_published_online=False)
variant = Product.objects.create(
    company=company, name='iPhone 13 ' + tag + ' 128 GB Negro', slug=('iphone-13-' + tag + '-128-negro').lower(),
    price=Decimal('0.00'), inventory=0, is_active=True, is_serialized=True, requires_imei=True,
    is_published_online=False,
)
device_catalog.set_variant(variant, model_id=model.pk, condition_line='new', option_ids=[gb128.pk, negro.pk])
ensure(variant)

def plain(name, slug, price, stock):
    product = Product.objects.create(
        company=company, name=name, slug=slug, price=Decimal(price), inventory=0, is_active=True,
        is_published_online=False, category=category,
    )
    ensure(product)
    if stock:
        inventory_services.create_stock_movement(
            branch=branch, product_id=product.pk, movement_type=StockMovement.INITIAL_STOCK, quantity=stock,
            reason='[E2E] stock inicial de la prueba', actor=actor,
        )
    return product

cable = plain('Cable QAE ' + tag, ('cable-qae-' + tag).lower(), '49.90', 100)
idle = plain('Cargador QAE ' + tag + ' sin stock', ('cargador-qae-' + tag + '-sin-stock').lower(), '39.90', 0)
ready = plain('Funda QAE ' + tag + ' no marcada', ('funda-qae-' + tag + '-no-marcada').lower(), '29.90', 5)
cable_code = ProductBarcode.objects.filter(product=cable).values_list('code', flat=True).first() or ''

# Otra empresa: su sucursal, su usuario con permisos de inventario y un producto con un código propio.
other = Company.objects.create(name='QAE otra ' + tag, slug=data['other_slug'])
provision_company_access_defaults(other)
other.refresh_from_db()
other_branch = other.default_inventory_branch
user = User.objects.create_user(username=data['other_user'], password=data['other_password'])
membership = Membership.objects.create(user=user, company=other, role='admin', is_active=True)
role = CompanyRole.objects.get(company=other, slug='administrador')
MembershipRoleAssignment.objects.create(membership=membership, role=role, is_active=True)
oprod = Product.objects.create(
    company=other, name='Producto de otra empresa ' + tag, slug=('otra-' + tag).lower(), price=Decimal('10.00'),
    inventory=0, is_active=True, is_published_online=False,
)
ensure(oprod)
oprod_code = ProductBarcode.objects.filter(product=oprod).values_list('code', flat=True).first() or ''
inventory_services.create_stock_movement(
    branch=other_branch, product_id=oprod.pk, movement_type=StockMovement.INITIAL_STOCK, quantity=7,
    reason='[E2E] otra empresa', actor=user,
)
out({
    'branch': branch.pk, 'branchName': branch.name, 'category': category.pk, 'categoryName': category.name,
    'cable': cable.pk, 'cableName': cable.name, 'cableCode': cable_code,
    'idle': idle.pk, 'idleName': idle.name, 'idleSlug': idle.slug,
    'ready': ready.pk, 'readyName': ready.name,
    'other': {'company': other.pk, 'product': oprod.pk, 'barcode': oprod_code, 'branch': other_branch.pk, 'name': oprod.name},
})
`, { other_user: OTHER_USER, other_password: OTHER_PASSWORD });
}

export type Counts = {
  products: number; units: number; unit_imeis: number; movements: number; codes: number; batches: number;
  labels: number; movement_types: Record<string, number>;
};

/** Lo marcado con `TAG` en la empresa de la prueba: sólo lectura. */
export function contar(): Counts {
  return shell<Counts>(`
from store.models import LabelBatch, LabelBatchItem, Product, ProductBarcode, StockMovement, StockUnit
from django.db.models import Count, Sum
products = Product.objects.filter(company__slug=data['slug'], name__icontains=data['tag'])
units = StockUnit.objects.filter(product__in=products)
moves = StockMovement.objects.filter(product__in=products)
items = LabelBatchItem.objects.filter(batch__company__slug=data['slug'], product__in=products)
out({
    'products': products.count(), 'units': units.count(), 'unit_imeis': units.exclude(imei='').values('imei').distinct().count(),
    'movements': moves.count(), 'codes': ProductBarcode.objects.filter(product__in=products).count(),
    'batches': LabelBatch.objects.filter(pk__in=items.values('batch_id')).count(),
    'labels': items.aggregate(n=Sum('copies'))['n'] or 0,
    'movement_types': {row['movement_type']: row['n'] for row in moves.values('movement_type').annotate(n=Count('pk'))},
})
`);
}

export type Reconciliation = {
  products: number;
  mismatches: { product: string; branch: string; stock: number; kardex: number }[];
  serialized: { product: string; units: number; available: number; entries: number }[];
  inventory_field: { product: string; field: number; stock: number }[];
};

/** BranchStock == suma del Kardex por sucursal; unidades == entradas para los productos por equipo. */
export function conciliar(): Reconciliation {
  return shell<Reconciliation>(`
from django.db.models import Sum
from store.models import BranchStock, Product, StockMovement, StockUnit
products = list(Product.objects.filter(company__slug=data['slug'], name__icontains=data['tag']))
mismatches, serialized, field = [], [], []
for product in products:
    for stock in BranchStock.objects.filter(product=product).select_related('branch'):
        kardex = StockMovement.objects.filter(product=product, branch=stock.branch).aggregate(n=Sum('quantity'))['n'] or 0
        if stock.quantity != kardex:
            mismatches.append({'product': product.name, 'branch': stock.branch.name, 'stock': stock.quantity, 'kardex': kardex})
    total = BranchStock.objects.filter(product=product).aggregate(n=Sum('quantity'))['n'] or 0
    if product.inventory != total:
        field.append({'product': product.name, 'field': product.inventory, 'stock': total})
    if product.is_serialized:
        units = StockUnit.objects.filter(product=product)
        entries = StockMovement.objects.filter(product=product).aggregate(n=Sum('quantity'))['n'] or 0
        serialized.append({'product': product.name, 'units': units.count(),
                           'available': units.filter(status='available').count(), 'entries': entries})
out({'products': len(products), 'mismatches': mismatches, 'serialized': serialized, 'inventory_field': field})
`);
}

/** Borra lo marcado con `TAG`; la otra empresa de la corrida queda desactivada (una empresa no se borra). Devuelve lo que no pudo borrar. */
export function limpiar(): { removed: Record<string, number>; left: string[] } {
  return shell(`
from django.contrib.auth import get_user_model
from django.db import transaction
from store.models import *
removed, left = {}, []
def drop(label, queryset):
    try:
        with transaction.atomic():
            count = queryset.count()
            queryset.delete()
        removed[label] = count
    except Exception as exc:
        left.append(label + ': ' + exc.__class__.__name__ + ' ' + str(exc)[:120])
tag = data['tag']
products = Product.objects.filter(name__icontains=tag)
other = Company.objects.filter(slug=data['other_slug'])
drop('label_print_events', LabelPrintEvent.objects.filter(batch__items__product__in=products))
drop('label_batches', LabelBatch.objects.filter(items__product__in=products))
drop('label_batches_other', LabelBatch.objects.filter(company__in=other))
drop('units', StockUnit.objects.filter(product__in=products))
drop('movements', StockMovement.objects.filter(product__in=products))
drop('branch_stock', BranchStock.objects.filter(product__in=products))
drop('barcodes', ProductBarcode.objects.filter(product__in=products))
drop('images', ProductImage.objects.filter(product__in=products))
drop('products', products)
drop('models', ProductModel.objects.filter(name__icontains=tag))
drop('memberships', Membership.objects.filter(company__in=other))
drop('users', get_user_model().objects.filter(username=data['other_user']))
# A company cannot be deleted (sequences, settings and roles protect it): the throwaway one is only switched off.
removed['other_company_deactivated'] = other.update(is_active=False)
out({'removed': removed, 'left': left})
`, { other_user: OTHER_USER });
}

// ── Sesión y API ───────────────────────────────────────────────────────────────────────────────────────────────

export async function signIn(page: Page) {
  await page.goto("/auth", { waitUntil: "networkidle" });
  const card = page.locator("section").filter({ hasText: "Accesos de desarrollo" });
  if ((await card.count()) === 0) test.skip(true, "no hay tarjeta de accesos de desarrollo en este entorno");
  const use = card.locator("li").filter({ hasText: "dev_admin" }).getByRole("button", { name: "Usar cuenta" });
  if ((await use.count()) === 0) test.skip(true, "dev_admin no existe: siembra con seed_demo_users");
  await use.click();
  for (let intento = 0; intento < 3; intento++) {
    await page.getByRole("button", { name: /iniciar sesión/i }).first().click();
    try {
      await page.waitForURL((u) => !u.pathname.startsWith("/auth"), { timeout: 12_000 });
      return;
    } catch {
      if (intento === 2) throw new Error("no se pudo iniciar sesión");
      await page.waitForTimeout(62_000);
    }
  }
}

/** La API en el nombre de máquina de la página (ahí vive la sesión), sin la barra final. */
export function apiBase(): string {
  const raw = process.env.E2E_API_BASE ?? "http://127.0.0.1:8100/api";
  const url = new URL(raw);
  url.hostname = "localhost";
  return url.toString().replace(/\/$/, "");
}

export type Reply<T = unknown> = { status: number; body: T };

/** Una llamada a la API con la sesión del contexto (cookies) y el CSRF que el servidor pide para escribir. */
export async function call<T = unknown>(
  api: APIRequestContext, method: "GET" | "POST" | "PATCH", path: string, body?: unknown, extra: Record<string, string> = {},
): Promise<Reply<T>> {
  const headers: Record<string, string> = { ...extra };
  if (method !== "GET") {
    const csrf = (await api.storageState()).cookies.find((cookie) => cookie.name === "csrftoken")?.value;
    if (csrf) headers["X-CSRFToken"] = csrf;
    headers.Origin = new URL(process.env.E2E_BASE_URL ?? "http://localhost:3002").origin;
  }
  const res = await api.fetch(`${apiBase()}${path}`, {
    method, headers, ...(body === undefined ? {} : { data: body }),
  });
  const text = await res.text();
  let parsed: unknown = null;
  try { parsed = text ? JSON.parse(text) : null; } catch { parsed = text; }
  return { status: res.status(), body: parsed as T };
}

export async function pedirCsrf(api: APIRequestContext) {
  await api.get(`${apiBase()}/auth/csrf/`);
}

export function expectNoSensitive(text: string, ...secrets: string[]) {
  for (const secret of secrets.filter(Boolean)) expect(text, `se coló «${secret}»`).not.toContain(secret);
}

export type Card = {
  id: number; name: string; branch: number | null; stock: number; inventory: number;
  movements: { type: string; quantity: number; before: number; after: number }[];
  batches: { id: number; copies: number; reason: string }[];
};

/** El Kardex de un producto, su existencia por sucursal y las etiquetas que dejó: sólo lectura. */
export function ficha(productId: number): Card {
  return shell<Card>(`
from store.models import BranchStock, LabelBatch, LabelBatchItem, Product, StockMovement
from django.db.models import Sum
product = Product.objects.get(pk=data['product'])
rows = StockMovement.objects.filter(product=product).order_by('pk')
batches = LabelBatch.objects.filter(items__product=product).distinct().order_by('pk')
out({
    'id': product.pk, 'name': product.name, 'branch': None,
    'stock': BranchStock.objects.filter(product=product).aggregate(n=Sum('quantity'))['n'] or 0,
    'inventory': product.inventory,
    'movements': [{'type': m.movement_type, 'quantity': m.quantity, 'before': m.stock_before, 'after': m.stock_after} for m in rows],
    'batches': [{'id': b.pk, 'reason': b.reason,
                 'copies': LabelBatchItem.objects.filter(batch=b, product=product).aggregate(n=Sum('copies'))['n'] or 0} for b in batches],
})
`, { product: productId });
}

export type AdminRow = {
  id: number; name: string; slug: string; price: string; is_published_online: boolean; is_active: boolean;
  publication_problems: string[]; is_serialized: boolean; inventory: number; category_id: number | null;
};

/** Los productos de la empresa que lleven `text` en el nombre, vistos con la sesión del contexto. */
export async function adminProducts(api: APIRequestContext, text: string): Promise<AdminRow[]> {
  const res = await call<{ results: AdminRow[] }>(api, "GET", `/admin/products/?search=${encodeURIComponent(text)}&page_size=100`);
  expect(res.status).toBe(200);
  return res.body.results;
}

/** Un equipo y un lote de etiquetas del producto, para pedirlos desde otra empresa. */
export function ajenos(productId: number): { unit: number; batch: number; branch: number; movements: number } {
  return shell(`
from store.models import LabelBatch, StockMovement, StockUnit
unit = StockUnit.objects.filter(product_id=data['product']).order_by('pk').first()
batch = LabelBatch.objects.filter(items__product_id=data['product']).order_by('pk').first()
out({'unit': unit.pk if unit else 0, 'batch': batch.pk if batch else 0, 'branch': unit.branch_id if unit else 0,
     'movements': StockMovement.objects.filter(product_id=data['product']).count()})
`, { product: productId });
}
