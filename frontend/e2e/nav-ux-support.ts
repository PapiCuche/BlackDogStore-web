import { shell, RUN, TAG } from "./inventory-first-support";

/**
 * NAV-UX-01 · apoyo de `nav-ux.spec.ts`: tres usuarios de la empresa de la prueba con permisos distintos.
 *
 * Todo lleva la marca de la corrida: `limpiarRecepcion` (de `inventory-receive-support`) borra los usuarios
 * `qaer_<corrida>…` y los roles cuyo nombre lleva la marca. No se toca ningún dato que no sea de la prueba.
 */

export const NAV_PASSWORD = "Qae-Nav-2026!";
const prefix = `qaer_${RUN.toLowerCase()}`;
export const NAV_USERS = {
  admin: `${prefix}_na`,  // todo lo del catálogo y del inventario, con costos y reportes
  clerk: `${prefix}_nc`,  // ver y ajustar inventario, ver productos, imprimir etiquetas (sin gestionar productos ni costos)
  viewer: `${prefix}_nv`, // sólo ver el inventario
} as const;

const ADMIN_CAPS = [
  "company.view", "products.view", "products.manage", "inventory.view", "inventory.adjust", "inventory.reports",
  "inventory.labels.print", "costs.view",
];
const CLERK_CAPS = ["company.view", "products.view", "inventory.view", "inventory.adjust", "inventory.labels.print"];
const VIEWER_CAPS = ["company.view", "inventory.view"];

export function sembrarNavegacion(): void {
  shell(`
from django.contrib.auth import get_user_model
from store.models import Company, CompanyRole, Membership, MembershipRoleAssignment
User = get_user_model()
company = Company.objects.get(slug=data['slug'])
tag = data['tag']

def make_user(username, caps):
    user = User.objects.create_user(username=username, password=data['password'])
    role = CompanyRole.objects.create(company=company, name='Rol ' + username + ' ' + tag, slug=(username + '-' + tag).lower(), capabilities=caps)
    membership = Membership.objects.create(user=user, company=company, role='inventory', is_active=True,
                                           branch_access_mode=Membership.ACCESS_MODE_ALL)
    MembershipRoleAssignment.objects.create(membership=membership, role=role, is_active=True)

make_user(data['users']['admin'], data['caps']['admin'])
make_user(data['users']['clerk'], data['caps']['clerk'])
make_user(data['users']['viewer'], data['caps']['viewer'])
out({'ok': True})
`, {
    users: NAV_USERS, password: NAV_PASSWORD,
    caps: { admin: ADMIN_CAPS, clerk: CLERK_CAPS, viewer: VIEWER_CAPS },
  });
}

export { TAG };
