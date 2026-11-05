import { shell, RUN, imei } from "./inventory-first-support";

/**
 * INV-COUNT-UX-04 · apoyo de `inventory-count-ux-04.spec.ts` (recuento físico que abre directo a contar).
 *
 * NO es un arnés paralelo: usa el `shell()` de `inventory-first-support` (con su GUARDA de base local y desechable) y la
 * MARCA `TAG` de la corrida. Todo lo que siembra lleva la marca en el nombre (sucursales, productos, roles) o el prefijo
 * `qacx_<corrida>` (usuarios), y `limpiarRecuento()` borra sólo eso. El IMEI del equipo es inventado (Luhn válido).
 *
 * Siembra, en la empresa `black-dog-store`:
 *   · dos sucursales NUEVAS, A y B (así «Pendientes» de A sólo puede listar lo de esta corrida);
 *   · accesorios con stock y código por `create_stock_movement` (el Kardex existe):
 *       Cable    → A 10, B 3      (se escanea dos veces en «Sumar 1»)
 *       Funda    → A 6            (se escribe un total)
 *       Cargador → A 4            (se deja en blanco; se le hace una salida manual a mitad del recuento)
 *       Protector→ B 5            (sólo en B: no debe salir en A)
 *   · un equipo serializado con código y UN equipo en A (no se lista; escanearlo avisa);
 *   · usuarios: approver, counter, viewer y single (counter restringido a la sucursal A).
 */

export const CX_PASSWORD = "Qae-Recuento-2026!";
const prefix = `qacx_${RUN.toLowerCase()}`;
export const CX_USERS = {
  approver: `${prefix}_apr`, // + inventory.counts.approve, todas las sucursales
  counter: `${prefix}_cnt`,  // inventory.adjust, todas las sucursales, SIN aprobar
  viewer: `${prefix}_ver`,   // sólo ver
  single: `${prefix}_one`,   // como counter, pero SÓLO la sucursal A
} as const;

export type Item = { id: number; name: string; code: string };
export type CxSeed = {
  branchA: { id: number; name: string };
  branchB: { id: number; name: string };
  cable: Item; funda: Item; cargador: Item; protector: Item; device: Item;
};

export function sembrarRecuento(): CxSeed {
  return shell<CxSeed>(`
from django.contrib.auth import get_user_model
from django.db import transaction
from store import inventory_services, product_codes
from store import stock_unit_services as units_service
from store.models import (
    Branch, Company, CompanyRole, Membership, MembershipBranchAccess, MembershipRoleAssignment, Product, ProductBarcode,
    StockMovement,
)
User = get_user_model()
company = Company.objects.get(slug=data['slug'])
tag = data['tag']
actor = User.objects.get(username='dev_admin')

a = Branch.objects.create(company=company, name='Sucursal A ' + tag)
b = Branch.objects.create(company=company, name='Sucursal B ' + tag)

def product(label, serialized=False):
    with transaction.atomic():
        p = Product.objects.create(
            company=company, name=label + ' CUX ' + tag, slug=(label + '-cux-' + tag).lower(), price=Decimal('19.90'),
            inventory=0, is_active=True, is_published_online=False,
            is_serialized=serialized, requires_imei=serialized,
        )
        product_codes.ensure_code(p)
    code = ProductBarcode.objects.filter(product=p).values_list('code', flat=True).first() or ''
    return p, {'id': p.pk, 'name': p.name, 'code': code}

def stock(branch, p, quantity):
    inventory_services.create_stock_movement(
        branch=branch, product_id=p.pk, movement_type=StockMovement.INITIAL_STOCK, quantity=quantity,
        reason='[E2E] stock inicial del recuento', actor=actor,
    )

cable, cable_out = product('Cable'); stock(a, cable, 10); stock(b, cable, 3)
funda, funda_out = product('Funda'); stock(a, funda, 6)
cargador, cargador_out = product('Cargador'); stock(a, cargador, 4)
protector, protector_out = product('Protector'); stock(b, protector, 5)
device, device_out = product('iPhone', serialized=True)
units_service.receive_units(
    branch=a, product=device, units=[{'serial_number': (tag + 'CUX01').upper(), 'imei': data['imei'], 'condition': 'new'}],
    actor=actor, reason='[E2E] equipo del recuento',
)

def make_user(username, caps, only=None):
    user = User.objects.create_user(username=username, password=data['password'])
    role = CompanyRole.objects.create(
        company=company, name='Rol ' + username + ' ' + tag, slug=(username + '-' + tag).lower(), capabilities=caps,
    )
    membership = Membership.objects.create(
        user=user, company=company, role='inventory', is_active=True,
        branch=only, branch_access_mode=Membership.ACCESS_MODE_SELECTED if only else Membership.ACCESS_MODE_ALL,
    )
    MembershipRoleAssignment.objects.create(membership=membership, role=role, is_active=True)
    if only:
        MembershipBranchAccess.objects.create(membership=membership, branch=only, is_active=True)

counter_caps = ['company.view', 'inventory.view', 'inventory.adjust', 'products.view']
make_user(data['users']['approver'], counter_caps + ['inventory.counts.approve'])
make_user(data['users']['counter'], counter_caps)
make_user(data['users']['viewer'], ['company.view', 'inventory.view'])
make_user(data['users']['single'], counter_caps, only=a)
out({
    'branchA': {'id': a.pk, 'name': a.name}, 'branchB': {'id': b.pk, 'name': b.name},
    'cable': cable_out, 'funda': funda_out, 'cargador': cargador_out, 'protector': protector_out, 'device': device_out,
})
`, { users: CX_USERS, password: CX_PASSWORD, imei: imei(4242) });
}

/** Una salida del Kardex (venta o salida manual) mientras el recuento está abierto. */
export function salida(branch: number, product: number, quantity: number, type: "sale_exit" | "manual_exit") {
  return shell<number>(`
from django.contrib.auth import get_user_model
from store import inventory_services
from store.models import Branch
movement = inventory_services.create_stock_movement(
    branch=Branch.objects.get(pk=data['branch']), product_id=data['product'], movement_type=data['type'],
    quantity=data['quantity'], reason='[E2E] salida durante el recuento',
    actor=get_user_model().objects.get(username='dev_admin'),
)
out(movement.pk)
`, { branch, product, quantity, type });
}

export type CountState = {
  id: number; status: string; branch: number; created_by: string;
  lines: { product: number; physical: number | null; start: number | null; approval: number | null; difference: number | null }[];
  movements: { product: number; type: string; quantity: number; before: number; after: number }[];
};

/** Un recuento con sus líneas y los movimientos que escribió: sólo lectura. */
export function recuento(id: number): CountState {
  return shell<CountState>(`
from store.models import InventoryCount, StockMovement
c = InventoryCount.objects.select_related('created_by').get(pk=data['id'])
out({
    'id': c.pk, 'status': c.status, 'branch': c.branch_id, 'created_by': c.created_by.username if c.created_by else '',
    'lines': [{'product': i.product_id, 'physical': i.physical_quantity, 'start': i.theoretical_at_start,
               'approval': i.theoretical_at_approval, 'difference': i.difference}
              for i in c.items.order_by('product_id')],
    'movements': [{'product': m.product_id, 'type': m.movement_type, 'quantity': m.quantity,
                   'before': m.stock_before, 'after': m.stock_after}
                  for m in StockMovement.objects.filter(inventory_count=c).order_by('pk')],
})
`, { id });
}

/** Existencia por sucursal (`{branchId: qty}`) y número de movimientos de cada producto: sólo lectura. */
export function existencias(products: number[]): Record<string, { stock: Record<string, number>; movements: number; inventory: number }> {
  return shell(`
from store.models import BranchStock, Product, StockMovement
result = {}
for pid in data['products']:
    p = Product.objects.get(pk=pid)
    result[str(pid)] = {
        'stock': {str(s.branch_id): s.quantity for s in BranchStock.objects.filter(product=p)},
        'movements': StockMovement.objects.filter(product=p).count(),
        'inventory': p.inventory,
    }
out(result)
`, { products });
}

/** Los recuentos de las sucursales de la corrida: sólo lectura. */
export function recuentosDe(branches: number[]): { id: number; status: string; branch: number; created_by: string }[] {
  return shell(`
from store.models import InventoryCount
out([{'id': c.pk, 'status': c.status, 'branch': c.branch_id, 'created_by': c.created_by.username if c.created_by else ''}
     for c in InventoryCount.objects.filter(branch_id__in=data['branches']).select_related('created_by').order_by('pk')])
`, { branches });
}

/** Borra lo de la corrida (marca o prefijo de usuario). Devuelve lo que no pudo borrar. */
export function limpiarRecuento(): { removed: Record<string, number>; left: string[] } {
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
        left.append(label + ': ' + exc.__class__.__name__ + ' ' + str(exc)[:160])
tag = data['tag']
branches = Branch.objects.filter(company__slug=data['slug'], name__icontains=tag)
products = Product.objects.filter(company__slug=data['slug'], name__icontains=tag)
counts = InventoryCount.objects.filter(branch__in=branches)
users = get_user_model().objects.filter(username__startswith=data['prefix'])
count_ids = list(counts.values_list('pk', flat=True))
drop('count_trails', AdminAuditLog.objects.filter(target_type='inventory_count', target_id__in=[str(i) for i in count_ids]))
drop('movements', StockMovement.objects.filter(product__in=products))
drop('count_items', InventoryCountItem.objects.filter(count__in=counts))
drop('counts', counts)
drop('units', StockUnit.objects.filter(product__in=products))
drop('branch_stock', BranchStock.objects.filter(product__in=products))
drop('barcodes', ProductBarcode.objects.filter(product__in=products))
drop('products', products)
drop('role_assignments', MembershipRoleAssignment.objects.filter(membership__user__in=users))
drop('branch_access', MembershipBranchAccess.objects.filter(membership__user__in=users))
drop('memberships', Membership.objects.filter(user__in=users))
drop('roles', CompanyRole.objects.filter(name__icontains=tag))
drop('users', users)
drop('branches', branches)
out({'removed': removed, 'left': left})
`, { prefix });
}
