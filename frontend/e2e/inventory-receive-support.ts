import { type APIRequestContext, type Browser, type BrowserContext, type Page } from "@playwright/test";
import { TAG, RUN, COMPANY_SLUG, OTHER_USER, limpiar, shell, apiBase } from "./inventory-first-support";

/**
 * INV-OPS-01 · apoyo del recorrido «Recibir mercadería» (`inventory-receive.spec.ts`).
 *
 * NO es un arnés paralelo: usa el `shell()` del recorrido de inventario primero (con su GUARDA de base local y
 * desechable), la MARCA `TAG` de la corrida y su `limpiar()`. Sólo añade lo que la recepción necesita y el otro
 * recorrido no: productos por equipo, un proveedor, una segunda sucursal, usuarios con permisos exactos y lecturas
 * del estado (equipos, Kardex, lotes, huella de auditoría) para comparar «antes» y «después».
 *
 * Todo lleva la marca en el nombre (productos, proveedor, roles, sucursal) o el prefijo `qaer_<corrida>` (usuarios).
 * Los IMEI y las series son sintéticos. La limpieza extra va ANTES de `limpiar()` (que borra por nombre de producto).
 */

export const RX_PASSWORD = "Qae-Recibir-2026!";
const prefix = `qaer_${RUN.toLowerCase()}`;
export const RX_USERS = {
  viewer: `${prefix}_ver`,      // inventory.view, sin inventory.adjust
  adjust: `${prefix}_aju`,      // inventory.adjust, SIN costs.view
  costs: `${prefix}_cos`,       // inventory.adjust + costs.view
  scoped: `${prefix}_suc`,      // inventory.adjust, SÓLO la sucursal principal
  noproducts: `${prefix}_sinp`, // inventory.adjust pero SIN products.view (el buscador respondería 403)
} as const;

export type Named = { id: number; name: string };
export type RxSeed = {
  /** Equipos con serie e IMEI (cada uno con su propio nombre para que el buscador lo encuentre solo). */
  used: Named; newA: Named; newB: Named; idem: Named; idem2: Named; ux1: Named; ux2: Named; dup: Named; race: Named;
  /** Un accesorio por cantidad al que se le va a borrar la fila después de elegirlo (404 manejado). */
  ephemeral: Named;
  supplier: Named;
  branch2: Named;
  /** Producto serializado de la OTRA empresa, con un equipo cuyo IMEI es `foreignImei`. */
  foreign: { product: number; name: string; imei: string; unitId: number };
  roleAdjust: number;
};

/** Series inventadas y únicas por corrida. El servidor las guarda en MAYÚSCULAS: se escriben ya así. */
export const serial = (label: string, n = 1) => `${TAG}${label}${String(n).padStart(2, "0")}`.toUpperCase();

export function sembrarRecepcion(): RxSeed {
  return shell<RxSeed>(`
from django.contrib.auth import get_user_model
from django.db import transaction
from store import product_codes
from store import stock_unit_services as units_service
from store.models import (
    Branch, CompanyRole, Company, Membership, MembershipBranchAccess, MembershipRoleAssignment, Product, StockMovement,
    Supplier,
)
User = get_user_model()
company = Company.objects.get(slug=data['slug'])
branch = company.default_inventory_branch
tag = data['tag']

def ensure(product):
    with transaction.atomic():
        return product_codes.ensure_code(product)

def phone(label, condition_line='new'):
    product = Product.objects.create(
        company=company, name=label + ' ' + tag, slug=(label + '-' + tag).lower().replace(' ', '-'),
        price=Decimal('0.00'), inventory=0, is_active=True, is_serialized=True, requires_imei=True,
        is_published_online=False,
    )
    ensure(product)
    return {'id': product.pk, 'name': product.name}

used = phone('iPhone 12 Usado')
newA = phone('iPhone 13 Pro Recibo')
newB = phone('iPhone 14 Recibo')
idem = phone('iPhone 11 Reintento')
idem2 = phone('iPhone SE Clave')
ux1 = phone('iPhone XR Movil')
ux2 = phone('iPhone XS Escritorio')
dup = phone('iPhone 8 Duplicado')
race = phone('iPhone 7 Carrera')

eph = Product.objects.create(
    company=company, name='Accesorio efimero ' + tag, slug=('accesorio-efimero-' + tag).lower(), price=Decimal('5.00'),
    inventory=0, is_active=True, is_published_online=False,
)
ensure(eph)

supplier = Supplier.objects.create(company=company, name='Proveedor QAE ' + tag)
branch2 = Branch.objects.create(company=company, name='Sucursal QAE ' + tag)

def make_user(username, caps, *, only_main=False):
    user = User.objects.create_user(username=username, password=data['password'])
    role = CompanyRole.objects.create(
        company=company, name='Rol ' + username + ' ' + tag, slug=(username + '-' + tag).lower(), capabilities=caps,
    )
    membership = Membership.objects.create(
        user=user, company=company, role='inventory', is_active=True,
        branch=branch if only_main else None,
        branch_access_mode=Membership.ACCESS_MODE_SELECTED if only_main else Membership.ACCESS_MODE_ALL,
    )
    MembershipRoleAssignment.objects.create(membership=membership, role=role, is_active=True)
    if only_main:
        MembershipBranchAccess.objects.create(membership=membership, branch=branch, is_active=True)
    return role

common = ['company.view', 'products.view', 'inventory.view']
make_user(data['users']['viewer'], common)
role_adjust = make_user(data['users']['adjust'], common + ['inventory.adjust', 'inventory.labels.print'])
make_user(data['users']['costs'], common + ['inventory.adjust', 'inventory.labels.print', 'costs.view'])
make_user(data['users']['scoped'], common + ['inventory.adjust', 'inventory.labels.print'], only_main=True)
make_user(data['users']['noproducts'], ['company.view', 'inventory.view', 'inventory.adjust', 'inventory.labels.print'])

# The other company: a serialized product with ONE device already in its stock.
other = Company.objects.get(slug=data['other_slug'])
other_branch = other.default_inventory_branch
other_user = User.objects.get(username=data['other_user'])
fprod = Product.objects.create(
    company=other, name='Equipo ajeno ' + tag, slug=('equipo-ajeno-' + tag).lower(), price=Decimal('0.00'),
    inventory=0, is_active=True, is_serialized=True, requires_imei=True, is_published_online=False,
)
created = units_service.receive_units(
    branch=other_branch, product=fprod,
    units=[{'serial_number': tag + 'AJENO01', 'imei': data['foreign_imei'], 'condition': 'new'}], actor=other_user,
    reason='[E2E] otra empresa',
)
out({
    'used': used, 'newA': newA, 'newB': newB, 'idem': idem, 'idem2': idem2, 'ux1': ux1, 'ux2': ux2, 'dup': dup,
    'race': race, 'ephemeral': {'id': eph.pk, 'name': eph.name},
    'supplier': {'id': supplier.pk, 'name': supplier.name}, 'branch2': {'id': branch2.pk, 'name': branch2.name},
    'foreign': {'product': fprod.pk, 'name': fprod.name, 'imei': data['foreign_imei'], 'unitId': created[0].pk},
    'roleAdjust': role_adjust.pk,
})
`, {
    users: RX_USERS, password: RX_PASSWORD, other_user: OTHER_USER, foreign_imei: foreignImei(),
  });
}

/** Luhn válido, inventado, que NO coincide con ningún otro de la corrida (prefijo 49 en vez de 35). */
export function foreignImei(): string {
  const body = `49${String(Date.now()).slice(-8)}9999`;
  let total = 0;
  for (let index = 0; index < body.length; index += 1) {
    let digit = Number(body[body.length - 1 - index]);
    if (index % 2 === 0) { digit *= 2; if (digit > 9) digit -= 9; }
    total += digit;
  }
  return body + String((10 - (total % 10)) % 10);
}

/** Quita o devuelve `inventory.adjust` al rol del usuario «aju» (para provocar un 403 con la pantalla ya abierta). */
export function setAdjust(roleId: number, on: boolean) {
  shell(`
from store.models import CompanyRole
role = CompanyRole.objects.get(pk=data['role'])
caps = [c for c in role.capabilities if c != 'inventory.adjust']
if data['on']:
    caps.append('inventory.adjust')
role.capabilities = caps
role.save(update_fields=['capabilities'])
out(True)
`, { role: roleId, on });
}

export function deleteProduct(productId: number) {
  shell(`
from store.models import ProductBarcode, Product
ProductBarcode.objects.filter(product_id=data['product']).delete()
Product.objects.filter(pk=data['product']).delete()
out(True)
`, { product: productId });
}

export type UnitRow = { serial: string; imei: string; condition: string; status: string; branch: number; cost: string | null };
export type Estado = {
  units: UnitRow[];
  movements: { type: string; quantity: number; before: number; after: number; reason: string; unit_cost: string | null; branch: number }[];
  batches: { id: number; copies: number }[];
  stock: Record<string, number>;
  inventory: number;
};

/** Todo lo que la recepción deja de un producto, sólo lectura. */
export function estado(productId: number): Estado {
  return shell<Estado>(`
from django.db.models import Sum
from store.models import BranchStock, LabelBatch, LabelBatchItem, Product, StockMovement, StockUnit
product = Product.objects.get(pk=data['product'])
batches = LabelBatch.objects.filter(items__product=product).distinct().order_by('pk')
out({
    'units': [{'serial': u.serial_number, 'imei': u.imei or '', 'condition': u.condition, 'status': u.status,
               'branch': u.branch_id, 'cost': None if u.cost is None else str(u.cost)}
              for u in StockUnit.objects.filter(product=product).order_by('pk')],
    'movements': [{'type': m.movement_type, 'quantity': m.quantity, 'before': m.stock_before, 'after': m.stock_after,
                   'reason': m.reason, 'unit_cost': None if m.unit_cost is None else str(m.unit_cost), 'branch': m.branch_id}
                  for m in StockMovement.objects.filter(product=product).order_by('pk')],
    'batches': [{'id': b.pk, 'copies': LabelBatchItem.objects.filter(batch=b, product=product).aggregate(n=Sum('copies'))['n'] or 0}
                for b in batches],
    'stock': {str(s.branch_id): s.quantity for s in BranchStock.objects.filter(product=product)},
    'inventory': product.inventory,
})
`, { product: productId });
}

/** Recepciones confirmadas en la empresa (la huella de auditoría): para comprobar que un reintento no suma una. */
export function recibos(): number {
  return shell<number>(`
from store.models import AdminAuditLog
out(AdminAuditLog.objects.filter(company__slug=data['slug'], action='inventory_receipt_confirmed').count())
`);
}

/** Cuántos productos hay en la empresa (todos) y cuántos llevan la marca. */
export function productos(): { all: number; tagged: number } {
  return shell(`
from store.models import Product
out({'all': Product.objects.filter(company__slug=data['slug']).count(),
     'tagged': Product.objects.filter(company__slug=data['slug'], name__icontains=data['tag']).count()})
`);
}

/** Existencias de la otra empresa: total y movimientos (para probar que la recepción de A no la toca). */
export function otraEmpresa(): { stock: number; movements: number; units: number; receipts: number } {
  return shell(`
from django.db.models import Sum
from store.models import AdminAuditLog, BranchStock, Company, StockMovement, StockUnit
other = Company.objects.get(slug=data['other_slug'])
out({'stock': BranchStock.objects.filter(branch__company=other).aggregate(n=Sum('quantity'))['n'] or 0,
     'movements': StockMovement.objects.filter(branch__company=other).count(),
     'units': StockUnit.objects.filter(company=other).count(),
     'receipts': AdminAuditLog.objects.filter(company=other, action='inventory_receipt_confirmed').count()})
`);
}

/** Lo extra que esta prueba sembró; se borra ANTES de `limpiar()`. Devuelve lo que no pudo borrar. */
export function limpiarRecepcion(): { removed: Record<string, number>; left: string[] } {
  const extra = shell<{ removed: Record<string, number>; left: string[] }>(`
from django.contrib.auth import get_user_model
from django.db import transaction
from store.models import AdminAuditLog, Branch, CompanyRole, Membership, MembershipBranchAccess, MembershipRoleAssignment, Supplier
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
users = get_user_model().objects.filter(username__startswith=data['prefix'])
# Receipt trails that name this run's products (the audit line keeps product names in its record).
ids = [log.pk for log in AdminAuditLog.objects.filter(action='inventory_receipt_confirmed') if tag in str(log.metadata)]
drop('receipt_trails', AdminAuditLog.objects.filter(pk__in=ids))
drop('role_assignments', MembershipRoleAssignment.objects.filter(membership__user__in=users))
drop('branch_access', MembershipBranchAccess.objects.filter(membership__user__in=users))
drop('memberships', Membership.objects.filter(user__in=users))
drop('roles', CompanyRole.objects.filter(name__icontains=tag))
drop('users', users)
out({'removed': removed, 'left': left})
`, { prefix });
  const base = limpiar();
  // The supplier and the second branch are referenced by nothing once the movements are gone.
  const tail = shell<{ removed: Record<string, number>; left: string[] }>(`
from django.db import transaction
from store.models import Branch, Supplier
removed, left = {}, []
for label, queryset in (('suppliers', Supplier.objects.filter(name__icontains=data['tag'])),
                        ('branches', Branch.objects.filter(name__icontains=data['tag']))):
    try:
        with transaction.atomic():
            removed[label] = queryset.count()
            queryset.delete()
    except Exception as exc:
        left.append(label + ': ' + exc.__class__.__name__ + ' ' + str(exc)[:120])
out({'removed': removed, 'left': left})
`);
  return {
    removed: { ...extra.removed, ...base.removed, ...tail.removed },
    left: [...extra.left, ...base.left, ...tail.left],
  };
}

// ── Sesiones ───────────────────────────────────────────────────────────────────────────────────────────────────

/** Entra por la API con `username` (la misma ruta que la pantalla) y devuelve el contexto con su sesión. */
export async function sessionFor(browser: Browser, username: string, password = RX_PASSWORD): Promise<{ context: BrowserContext; page: Page }> {
  const context = await browser.newContext();
  const login = await context.request.post(`${apiBase()}/auth/login/`, { data: { username, password } });
  if (login.status() !== 200) throw new Error(`INV-OPS-01: no pude iniciar sesión como ${username}: ${login.status()} ${(await login.text()).slice(0, 160)}`);
  await context.request.get(`${apiBase()}/auth/csrf/`); // la cookie CSRF que el servidor pide para escribir
  return { context, page: await context.newPage() };
}

export async function jsonCall<T = unknown>(
  api: APIRequestContext, path: string, body: unknown, headers: Record<string, string> = {},
): Promise<{ status: number; body: T }> {
  const csrf = (await api.storageState()).cookies.find((cookie) => cookie.name === "csrftoken")?.value;
  const res = await api.fetch(`${apiBase()}${path}`, {
    method: "POST", data: body,
    headers: { ...(csrf ? { "X-CSRFToken": csrf } : {}), Origin: new URL(process.env.E2E_BASE_URL ?? "http://localhost:3002").origin, ...headers },
  });
  const text = await res.text();
  let parsed: unknown = null;
  try { parsed = text ? JSON.parse(text) : null; } catch { parsed = text; }
  return { status: res.status(), body: parsed as T };
}

export { TAG, COMPANY_SLUG };
