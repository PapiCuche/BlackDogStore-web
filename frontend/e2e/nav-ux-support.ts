import { shell, RUN, TAG } from "./inventory-first-support";
import { devices, type Browser, type BrowserContext, type Locator, type Page, expect } from "@playwright/test";
import { OTHER_USER, OTHER_PASSWORD } from "./inventory-first-support";
import { sessionFor } from "./inventory-receive-support";


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
    if User.objects.filter(username=username).exists():
        return
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

// ── Ayudas compartidas por nav-ux.spec.ts y nav-ux-02.spec.ts ─────────────────────────────────────────────────

export const SHOTS = process.env.NAV_SHOTS ?? "test-results/nav-shots";
export const NAV = "Módulos del control interno";
export const BASE = process.env.E2E_BASE_URL ?? "http://localhost:3032";

type State = Awaited<ReturnType<BrowserContext["storageState"]>>;
const states = new Map<string, State>();
export const observations: { env: string; metric: string; value: number | string }[] = [];

/** After the run's users are deleted their sessions are worth nothing. */
export function forgetStates() { states.clear(); }

export async function stateFor(browser: Browser, username: string, password: string): Promise<State> {
  const found = states.get(username);
  if (found) return found;
  for (let attempt = 0; ; attempt++) {
    try {
      const { context } = await sessionFor(browser, username, password);
      const state = await context.storageState();
      await context.close();
      states.set(username, state);
      return state;
    } catch (error) {
      if (attempt >= 2 || !/429/.test(String(error))) throw error;
      await new Promise((resolve) => setTimeout(resolve, 62_000)); // 5 inicios de sesión por minuto
    }
  }
}

export type Env = { name: string; touch: boolean; kind: "chromium" | "webkit"; options: Parameters<Browser["newContext"]>[0]; narrow: boolean };
function profile(name: string): Parameters<Browser["newContext"]>[0] {
  const { defaultBrowserType: _ignored, ...rest } = devices[name];
  void _ignored;
  return rest;
}
export const ENVS: Env[] = [
  { name: "chromium-1280x800", touch: false, kind: "chromium", narrow: false, options: { viewport: { width: 1280, height: 800 } } },
  { name: "chromium-768x1024", touch: false, kind: "chromium", narrow: true, options: { viewport: { width: 768, height: 1024 } } },
  { name: "chromium-iphone13-390x844", touch: true, kind: "chromium", narrow: true, options: profile("iPhone 13") },
  { name: "webkit-iphone13", touch: true, kind: "webkit", narrow: true, options: profile("iPhone 13") },
];

export async function openAs(browser: Browser, who: keyof typeof NAV_USERS | "other", env: Env): Promise<{ context: BrowserContext; page: Page }> {
  const username = who === "other" ? OTHER_USER : NAV_USERS[who];
  const password = who === "other" ? OTHER_PASSWORD : NAV_PASSWORD;
  const state = await stateFor(browser, username, password);
  const context = await browser.newContext({ ...env.options, storageState: state, baseURL: BASE });
  const page = await context.newPage();
  page.on("dialog", (dialog) => void dialog.accept());
  return { context, page };
}

/** El menú: a la vista en pantalla ancha; en pantalla estrecha, el cajón que abre el botón de la barra. */
export async function menu(page: Page, env: Env): Promise<Locator> {
  const nav = page.getByRole("navigation", { name: NAV });
  if (env.narrow && !(await nav.isVisible())) {
    await page.getByRole("button", { name: "Abrir menú de módulos" }).click();
  }
  await expect(nav).toBeVisible({ timeout: 60_000 });
  return nav;
}

export async function expectNoOverflow(page: Page, where: string) {
  const { over, culprits } = await page.evaluate(() => {
    const width = window.innerWidth;
    const found: string[] = [];
    document.querySelectorAll("body *").forEach((el) => {
      const r = el.getBoundingClientRect();
      if (r.width > 1 && r.right > width + 1 && !el.closest("[role=dialog]") && !el.closest(".sr-only") && found.length < 6) found.push(`${el.tagName.toLowerCase()}.${String(el.className).slice(0, 60)} → ${Math.round(r.right)}`);
    });
    return { over: document.documentElement.scrollWidth - document.documentElement.clientWidth, culprits: found };
  });
  expect(over, `desborde horizontal en ${where}: ${culprits.join(" | ")}`).toBeLessThanOrEqual(1);
}

export async function current(nav: Locator): Promise<string[]> {
  return nav.locator('[aria-current="page"]').evaluateAll((els) => els.map((el) => (el.textContent ?? "").trim()));
}

