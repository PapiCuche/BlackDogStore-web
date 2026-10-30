import { shell, RUN, TAG, imei as imeiAt } from "./inventory-first-support";

/**
 * INV-RECEIVE-UX-02 · apoyo de `inventory-receive-list.spec.ts` («Pegar una lista» dentro de «Recibir mercadería»).
 *
 * NO es un arnés paralelo: usa el `shell()` de `inventory-first-support` (con su GUARDA de base local y desechable),
 * la MARCA `TAG` de la corrida y la limpieza de `inventory-receive-support` (`limpiarRecepcion`), que ya borra los
 * usuarios `qaer_<corrida>…`, los roles con la marca, los productos y los modelos marcados. Aquí sólo se añade lo que
 * la lista pegada necesita y los otros recorridos no: un catálogo con variantes seminuevas, dos usuarios (con y sin
 * `products.manage`) y lecturas del estado por artículo (equipos, Kardex, lotes, publicación) para comparar.
 *
 * Todo lleva la marca en el nombre. Las series y los IMEI son inventados (Luhn válido), nunca datos comerciales.
 */

export const RL_PASSWORD = "Qae-Lista-2026!";
const prefix = `qaer_${RUN.toLowerCase()}`;
export const RL_USERS = {
  manager: `${prefix}_lm`, // products.manage + products.view + inventory.adjust (puede crear el catálogo)
  plain: `${prefix}_ls`,   // products.view + inventory.adjust, SIN products.manage
} as const;

/** Modelos de la corrida: cada escenario usa los suyos para que ninguno dependa de lo que dejó el anterior. */
export const MODELS = {
  existing: `iPhone 15 ${TAG}`, // tiene la variante seminueva 128 GB Negro
  mixed: `iPhone 16 ${TAG}`,    // tiene la variante NUEVA 256 GB Azul (no la seminueva)
} as const;
export const ACCESSORY = `Cargador Rapido ${TAG}`;

export type ListSeed = { branchName: string; branchId: number; existingVariant: number; mixedVariant: number; accessory: number };

export function sembrarLista(): ListSeed {
  return shell<ListSeed>(`
from django.contrib.auth import get_user_model
from django.db import transaction
from store import device_catalog, product_codes
from store.models import (
    Company, CompanyRole, Membership, Product, VariantAttribute, VariantOption, MembershipRoleAssignment,
)
User = get_user_model()
company = Company.objects.get(slug=data['slug'])
branch = company.default_inventory_branch
tag = data['tag']

def ensure(product):
    with transaction.atomic():
        return product_codes.ensure_code(product)

capacity = VariantAttribute.objects.filter(company=company, name__iregex=r'^(capacidad|almacenamiento|capacity|storage)$').first() \\
    or device_catalog.create_attribute(company=company, name='Capacidad')
colour = VariantAttribute.objects.filter(company=company, kind='color').first() \\
    or device_catalog.create_attribute(company=company, name='Color', kind='color')
def option(attribute, label, **extra):
    return VariantOption.objects.filter(company=company, attribute=attribute, label__iexact=label).first() \\
        or device_catalog.create_option(company=company, attribute_id=attribute.pk, label=label, **extra)
gb128 = option(capacity, '128 GB'); gb256 = option(capacity, '256 GB')
negro = option(colour, 'Negro', swatch_hex='#111111'); azul = option(colour, 'Azul', swatch_hex='#1d4ed8')
option(colour, 'Rojo', swatch_hex='#b91c1c'); option(capacity, '64 GB')

def variant(model_name, line, gb, col, suffix):
    model = device_catalog.create_model(company=company, name=model_name, is_published_online=False)
    product = Product.objects.create(
        company=company, name=model_name + ' ' + suffix, slug=(model_name + '-' + suffix).lower().replace(' ', '-'),
        price=Decimal('0.00'), inventory=0, is_active=True, is_serialized=True, requires_imei=True, is_published_online=False,
    )
    device_catalog.set_variant(product, model_id=model.pk, condition_line=line, option_ids=[gb.pk, col.pk])
    ensure(product)
    return product

existing = variant(data['models']['existing'], 'used', gb128, negro, '128 GB Negro Seminuevo')
mixed = variant(data['models']['mixed'], 'new', gb256, azul, '256 GB Azul Nuevo')
accessory = Product.objects.create(
    company=company, name=data['accessory'], slug=data['accessory'].lower().replace(' ', '-'), price=Decimal('19.90'),
    inventory=0, is_active=True, is_published_online=False,
)
ensure(accessory)

def make_user(username, caps):
    user = User.objects.create_user(username=username, password=data['password'])
    role = CompanyRole.objects.create(company=company, name='Rol ' + username + ' ' + tag, slug=(username + '-' + tag).lower(), capabilities=caps)
    membership = Membership.objects.create(user=user, company=company, role='inventory', is_active=True,
                                           branch_access_mode=Membership.ACCESS_MODE_ALL)
    MembershipRoleAssignment.objects.create(membership=membership, role=role, is_active=True)

common = ['company.view', 'products.view', 'inventory.view', 'inventory.adjust', 'inventory.labels.print']
make_user(data['users']['manager'], common + ['products.manage'])
make_user(data['users']['plain'], common)
out({'branchName': branch.name, 'branchId': branch.pk, 'existingVariant': existing.pk, 'mixedVariant': mixed.pk, 'accessory': accessory.pk})
`, { users: RL_USERS, password: RL_PASSWORD, models: MODELS, accessory: ACCESSORY });
}

export type Article = {
  id: number; name: string; published: boolean; active: boolean; line: string; serialized: boolean; requiresImei: boolean;
  model: string; inventory: number;
  units: { serial: string; imei: string; condition: string; status: string; battery: number | null; grade: string; notes: string; branch: number }[];
  movements: { type: string; quantity: number; before: number; after: number }[];
  batches: { id: number; copies: number }[];
};

/** Los artículos de la empresa cuyo nombre lleva `text` (más la marca de la corrida) con todo lo que la recepción deja. */
export function articulos(text: string): Article[] {
  return shell<Article[]>(`
from django.db.models import Sum
from store.models import LabelBatch, LabelBatchItem, Product, StockMovement, StockUnit
rows = []
for product in Product.objects.filter(company__slug=data['slug'], name__icontains=data['text']).order_by('pk'):
    batches = LabelBatch.objects.filter(items__product=product).distinct().order_by('pk')
    rows.append({
        'id': product.pk, 'name': product.name, 'published': product.is_published_online, 'active': product.is_active,
        'line': product.condition_line, 'serialized': product.is_serialized, 'requiresImei': product.requires_imei,
        'model': product.product_model.name if product.product_model_id else '', 'inventory': product.inventory,
        'units': [{'serial': u.serial_number, 'imei': u.imei or '', 'condition': u.condition, 'status': u.status,
                   'battery': u.battery_health, 'grade': u.cosmetic_grade.label if u.cosmetic_grade_id else '',
                   'notes': u.notes, 'branch': u.branch_id}
                  for u in StockUnit.objects.filter(product=product).select_related('cosmetic_grade').order_by('pk')],
        'movements': [{'type': m.movement_type, 'quantity': m.quantity, 'before': m.stock_before, 'after': m.stock_after}
                      for m in StockMovement.objects.filter(product=product).order_by('pk')],
        'batches': [{'id': b.pk, 'copies': LabelBatchItem.objects.filter(batch=b, product=product).aggregate(n=Sum('copies'))['n'] or 0}
                    for b in batches],
    })
out(rows)
`, { text });
}

/** Cuántos modelos y productos hay en la empresa (todos): para probar que quien no gestiona productos no crea nada. */
export function catalogoTotales(): { products: number; models: number; options: number } {
  return shell(`
from store.models import Product, ProductModel, VariantOption
out({'products': Product.objects.filter(company__slug=data['slug']).count(),
     'models': ProductModel.objects.filter(company__slug=data['slug']).count(),
     'options': VariantOption.objects.filter(company__slug=data['slug']).count()})
`);
}

/** IMEI inventados y únicos de la corrida (Luhn válido). */
let imeiSeed = 300;
export const nextImei = (): string => imeiAt(imeiSeed++);

/** El mismo IMEI con el dígito de control cambiado: nunca pasa la verificación. */
export function breakImei(value: string): string {
  const last = Number(value.at(-1));
  return value.slice(0, -1) + String((last + 3) % 10);
}

export { TAG };
