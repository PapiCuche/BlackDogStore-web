import { shell, RUN, TAG } from "./inventory-first-support";

/**
 * INV-RECEIVE-FLOW-04 · apoyo de `inventory-receive-flow-04.spec.ts` (la lista pegada en «Carga masiva» sigue en
 * «Recibir mercadería», con el precio de venta de cada seminuevo en su fila cerrada).
 *
 * NO es un arnés paralelo: usa el `shell()` de `inventory-first-support` (con su GUARDA de base local y desechable) y
 * la limpieza de `inventory-receive-support` (`limpiarRecepcion`), que borra los usuarios `qaer_<corrida>…`, los roles
 * con la marca y los productos y modelos marcados. Aquí sólo se siembra un modelo con dos variantes SEMINUEVAS y dos
 * usuarios con permisos exactos. Series e IMEI inventados (Luhn válido); precios y costos inventados.
 */

export const FLOW_PASSWORD = "Qae-Flujo-2026!";
const prefix = `qaer_${RUN.toLowerCase()}`;
export const FLOW_USERS = {
  manager: `${prefix}_fm`, // products.manage + costs.view + inventory.adjust: fija precios y ve costos
  plain: `${prefix}_fp`,   // inventory.adjust SIN products.manage ni costs.view: recibe, no fija precios
} as const;

export const FLOW_MODEL = `iPhone 12 ${TAG}`;
export const FLOW_VARIANTS = {
  black: { gb: 128, colour: "negro", name: `${FLOW_MODEL} 128 GB Negro Seminuevo` },
  blue: { gb: 256, colour: "azul", name: `${FLOW_MODEL} 256 GB Azul Seminuevo` },
} as const;

export type FlowSeed = { branchName: string; branchId: number; black: number; blue: number };

export function sembrarFlujo(): FlowSeed {
  return shell<FlowSeed>(`
from django.contrib.auth import get_user_model
from django.db import transaction
from store import device_catalog, product_codes
from store.models import Company, CompanyRole, Membership, MembershipRoleAssignment, Product, VariantAttribute, VariantOption
User = get_user_model()
company = Company.objects.get(slug=data['slug'])
branch = company.default_inventory_branch
tag = data['tag']

capacity = VariantAttribute.objects.filter(company=company, name__iregex=r'^(capacidad|almacenamiento|capacity|storage)$').first() \\
    or device_catalog.create_attribute(company=company, name='Capacidad')
colour = VariantAttribute.objects.filter(company=company, kind='color').first() \\
    or device_catalog.create_attribute(company=company, name='Color', kind='color')
def option(attribute, label, **extra):
    return VariantOption.objects.filter(company=company, attribute=attribute, label__iexact=label).first() \\
        or device_catalog.create_option(company=company, attribute_id=attribute.pk, label=label, **extra)
gb128 = option(capacity, '128 GB'); gb256 = option(capacity, '256 GB')
negro = option(colour, 'Negro', swatch_hex='#111111'); azul = option(colour, 'Azul', swatch_hex='#1d4ed8')

model = device_catalog.create_model(company=company, name=data['model'], is_published_online=False)
def variant(name, gb, col):
    product = Product.objects.create(
        company=company, name=name, slug=name.lower().replace(' ', '-'), price=Decimal('0.00'), inventory=0,
        is_active=True, is_serialized=True, requires_imei=True, is_published_online=False,
    )
    device_catalog.set_variant(product, model_id=model.pk, condition_line='used', option_ids=[gb.pk, col.pk])
    with transaction.atomic():
        product_codes.ensure_code(product)
    return product

black = variant(data['names']['black'], gb128, negro)
blue = variant(data['names']['blue'], gb256, azul)

def make_user(username, caps):
    user = User.objects.create_user(username=username, password=data['password'])
    role = CompanyRole.objects.create(company=company, name='Rol ' + username + ' ' + tag, slug=(username + '-' + tag).lower(), capabilities=caps)
    membership = Membership.objects.create(user=user, company=company, role='inventory', is_active=True,
                                           branch_access_mode=Membership.ACCESS_MODE_ALL)
    MembershipRoleAssignment.objects.create(membership=membership, role=role, is_active=True)

common = ['company.view', 'products.view', 'inventory.view', 'inventory.adjust', 'inventory.labels.print']
make_user(data['users']['manager'], common + ['products.manage', 'costs.view'])
make_user(data['users']['plain'], common)
out({'branchName': branch.name, 'branchId': branch.pk, 'black': black.pk, 'blue': blue.pk})
`, {
    users: FLOW_USERS, password: FLOW_PASSWORD, model: FLOW_MODEL,
    names: { black: FLOW_VARIANTS.black.name, blue: FLOW_VARIANTS.blue.name },
  });
}

export type StoredUnit = { serial: string; imei: string; product: number; status: string; price: string | null; cost: string | null; branch: number };

/** Los equipos de la empresa con esas series: precio de venta y costo, cada uno en su campo. Sólo lectura. */
export function equipos(serials: string[]): StoredUnit[] {
  return shell<StoredUnit[]>(`
from store.models import StockUnit
rows = StockUnit.objects.filter(company__slug=data['slug'], serial_number__in=data['serials']).order_by('pk')
out([{'serial': u.serial_number, 'imei': u.imei or '', 'product': u.product_id, 'status': u.status, 'branch': u.branch_id,
      'price': None if u.sale_price is None else str(u.sale_price), 'cost': None if u.cost is None else str(u.cost)}
     for u in rows])
`, { serials });
}

/** Movimientos de compra y lotes de etiquetas de un artículo: para probar que un doble clic registra una vez. */
export function entradas(productId: number): { entries: number; batches: number; units: number } {
  return shell(`
from store.models import LabelBatch, StockMovement, StockUnit
out({'entries': StockMovement.objects.filter(product_id=data['product'], movement_type='purchase_entry').count(),
     'batches': LabelBatch.objects.filter(items__product_id=data['product']).distinct().count(),
     'units': StockUnit.objects.filter(product_id=data['product']).count()})
`, { product: productId });
}
