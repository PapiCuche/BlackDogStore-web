import { mkdirSync } from "node:fs";
import { test, expect, type Browser, type BrowserContext, type Page } from "@playwright/test";
import { RUN, TAG, shell } from "./inventory-first-support";
import { BASE, ENVS, SHOTS, expectNoOverflow, forgetStates, stateFor, type Env } from "./nav-ux-support";

/**
 * FIN-REMIND-01 · «Finanzas → Pagos fijos y vencimientos», de punta a punta y SIN MOCKS: navegador real, Django real
 * y una base desechable. La guarda de `inventory-first-guard` (vía `shell()`) se niega a correr contra otra cosa.
 *
 *   createdb blackdog_e2e_fin && python3 manage.py migrate && python3 manage.py seed_demo_users --company-slug black-dog-store
 *   (Django :8592 con DATABASE_URL=…/blackdog_e2e_fin, DEBUG=1, CORS/CSRF para http://localhost:3032)
 *   (npx next dev --webpack -p 3032 con NEXT_PUBLIC_API_URL=http://127.0.0.1:8592/api)
 *   E2E_BACKEND_DIR=<backend> E2E_BASE_URL=http://localhost:3032 E2E_API_BASE=http://127.0.0.1:8592/api \
 *     npx playwright test e2e/finance-reminders.spec.ts
 *
 * Datos SINTÉTICOS, todos con la marca de la corrida: un pago vencido, uno que vence hoy, uno por vencer, uno con
 * un abono parcial, uno en dólares y una regla semanal. Después se corre `reminders.run_company` para crear los avisos
 * de verdad. Se comprueba: la campana muestra el aviso y su enlace abre ese pago; la página no desborda a 1280, 768 y
 * 390 px (el calendario cede su sitio a la agenda); registrar un pago (una sola vez aunque se pulse dos); posponer un
 * aviso sin tocar la deuda; la política de avisos editable para quien la administra y de lectura para quien no.
 */

const PASSWORD = "Qae-Fin-2026!";
const ADMIN = `qaef_${RUN.toLowerCase()}_a`; // todo lo de finanzas, también revertir y los avisos
const READER = `qaef_${RUN.toLowerCase()}_r`; // sólo ver
const ADMIN_CAPS = ["company.view", "finance.view", "finance.manage", "finance.payments.record", "finance.payments.reverse", "finance.automation.manage"];
const READER_CAPS = ["company.view", "finance.view"];

type Seed = {
  today: string;
  ids: { overdue: number; today: number; soon: number; partial: number; usd: number };
  rule: number;
  categories: { rent: number; services: number };
  emitted: Record<string, number>;
  branch: string;
};
let seed: Seed;

const concept = (name: string) => `${name} ${TAG}`;

function sembrarFinanzas(): Seed {
  return shell<Seed>(`
from datetime import timedelta
from django.contrib.auth import get_user_model
from store.finance import reminders, services
from store.models import Company, CompanyRole, FinanceReminderPolicy, Membership, MembershipRoleAssignment
User = get_user_model()
company = Company.objects.get(slug=data['slug'])
branch = company.default_inventory_branch
actor = User.objects.get(username='dev_admin')
today = services.company_today(company)
tag = data['tag']
FinanceReminderPolicy.objects.filter(company=company).delete()   # la política de fábrica

def make_user(username, caps):
    if User.objects.filter(username=username).exists():
        return
    user = User.objects.create_user(username=username, password=data['password'])
    role = CompanyRole.objects.create(company=company, name='Rol ' + username + ' ' + tag, slug=(username + '-' + tag).lower(), capabilities=caps)
    membership = Membership.objects.create(user=user, company=company, role='inventory', is_active=True,
                                           branch_access_mode=Membership.ACCESS_MODE_ALL)
    MembershipRoleAssignment.objects.create(membership=membership, role=role, is_active=True)
make_user(data['admin'], data['admin_caps'])
make_user(data['reader'], data['reader_caps'])

rent = services.create_category(company, direction='payable', name='Alquiler ' + tag, actor=actor)
svc = services.create_category(company, direction='payable', name='Servicios ' + tag, actor=actor)
def mk(name, days, amount, category, payee, currency=None):
    return services.create_obligation(
        company, actor=actor, concept=name + ' ' + tag, amount=amount, due_date=today + timedelta(days=days),
        branch=branch, category=category, payee_name=payee, currency=currency)
over = mk('Alquiler vencido', -10, '2500.00', rent, 'Inmobiliaria Sol')
due_today = mk('Luz hoy', 0, '300.00', svc, 'Electro')
soon = mk('Internet pronto', 3, '120.00', svc, 'Fibra SA')
partial = mk('Hosting parcial', 5, '100.00', svc, 'Nube')
services.record_payment(partial, amount='60.00', method='transfer', actor=actor)
usd = mk('Licencia en dolares', -4, '50.00', svc, 'Vendor', currency='USD')
rule = services.create_rule(
    company, actor=actor, concept='Seguro fijo ' + tag, amount='1800.00', frequency='weekly', anchor_date=today,
    due_day=(today + timedelta(days=3)).weekday(), branch=branch, category=rent, payee_name='Aseguradora Andina')
emitted = reminders.run_company(company, today=today)
out({
    'today': today.isoformat(),
    'ids': {'overdue': over.pk, 'today': due_today.pk, 'soon': soon.pk, 'partial': partial.pk, 'usd': usd.pk},
    'rule': rule.pk, 'categories': {'rent': rent.pk, 'services': svc.pk}, 'emitted': emitted, 'branch': branch.name,
})
`, { password: PASSWORD, admin: ADMIN, reader: READER, admin_caps: ADMIN_CAPS, reader_caps: READER_CAPS });
}

type Facts = {
  payments: { amount: string; reversed: boolean }[];
  balance: string; state: string; snooze: string | null; offsets: number[]; enabled: boolean; notices: number;
};
function hechos(id: number, user = ADMIN): Facts {
  return shell<Facts>(`
from django.contrib.auth import get_user_model
from store.finance import reminders, services
from store.models import Company, Notification, Obligation, ObligationReminderSnooze
company = Company.objects.get(slug=data['slug'])
obligation = Obligation.objects.get(pk=data['id'])
numbers = services.figures(services.with_figures(Obligation.objects.filter(pk=obligation.pk)).get())
user = get_user_model().objects.get(username=data['user'])
snooze = ObligationReminderSnooze.objects.filter(obligation=obligation, user=user).first()
policy = reminders.policy_for(company)
out({
    'payments': [{'amount': str(p.amount), 'reversed': p.is_reversed} for p in obligation.payments.all()],
    'balance': str(numbers['balance']),
    'state': services.state(obligation, today=services.company_today(company)),
    'snooze': snooze.until.isoformat() if snooze else None,
    'offsets': list(policy.offsets), 'enabled': policy.enabled,
    'notices': Notification.objects.filter(user=user, target_type='finance_obligation', target_id=obligation.pk).count(),
})
`, { id, user });
}

function limpiarFinanzas(): { left: string[] } {
  return shell<{ left: string[] }>(`
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from store.models import *
left = []
def drop(label, queryset):
    try:
        with transaction.atomic():
            queryset.delete()
    except Exception as exc:
        left.append(label + ': ' + exc.__class__.__name__ + ' ' + str(exc)[:120])
tag = data['tag']
obligations = Obligation.objects.filter(concept__icontains=tag)
rules = RecurringObligationRule.objects.filter(concept__icontains=tag)
ids = list(Obligation.objects.filter(Q(concept__icontains=tag) | Q(rule__in=rules)).values_list('pk', flat=True))
users = get_user_model().objects.filter(username__startswith=data['prefix'])
events = NotificationEvent.objects.filter(target_type='finance_obligation', target_id__in=ids)
drop('notifications', Notification.objects.filter(event__in=events))
drop('events', events)
drop('snoozes', ObligationReminderSnooze.objects.filter(obligation_id__in=ids))
drop('payments', ObligationPayment.objects.filter(obligation_id__in=ids))
drop('adjustments', ObligationAdjustment.objects.filter(obligation_id__in=ids))
drop('obligations', Obligation.objects.filter(pk__in=ids))
drop('pauses', RulePause.objects.filter(rule__in=rules))
drop('rules', rules)
drop('categories', FinanceCategory.objects.filter(name__icontains=tag))
drop('policy', FinanceReminderPolicy.objects.filter(company__slug=data['slug']))
drop('role_assignments', MembershipRoleAssignment.objects.filter(membership__user__in=users))
drop('memberships', Membership.objects.filter(user__in=users))
drop('roles', CompanyRole.objects.filter(name__icontains=tag))
drop('users', users)
out({'left': left})
`, { prefix: `qaef_${RUN.toLowerCase()}` });
}

async function openAs(browser: Browser, username: string, env: Env): Promise<{ context: BrowserContext; page: Page }> {
  const state = await stateFor(browser, username, PASSWORD);
  const context = await browser.newContext({ ...env.options, storageState: state, baseURL: BASE });
  return { context, page: await context.newPage() };
}

const ENV_WIDE = ENVS[0];
const ENV_TABLET = ENVS[1];
const ENV_PHONE = ENVS[2];

async function openFinance(page: Page, query = "") {
  await page.goto(`/admin/finance${query}`, { waitUntil: "domcontentloaded" });
  await expect(page.getByRole("heading", { level: 1, name: "Pagos fijos y vencimientos" })).toBeVisible({ timeout: 90_000 });
  await expect(page.getByTestId("finance-list").or(page.getByTestId("finance-empty"))).toBeVisible({ timeout: 90_000 });
}

const row = (page: Page, name: string) => page.locator("li[data-obligation-id]").filter({ hasText: concept(name) });

/** The document itself must not scroll sideways, even with a dialog open (the helper below skips dialog contents). */
async function expectDocumentFits(page: Page, where: string) {
  const over = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(over, `desborde horizontal del documento en ${where}`).toBeLessThanOrEqual(1);
}

test.describe.configure({ mode: "serial" });

test.beforeAll(async ({ browser }) => {
  test.setTimeout(300_000);
  seed = sembrarFinanzas();
  expect(seed.emitted.errors, "los avisos se crearon sin errores").toBe(0);
  expect(seed.emitted.emitted, "se creó al menos un aviso por pago pendiente").toBeGreaterThanOrEqual(4);
  await stateFor(browser, ADMIN, PASSWORD);
  await stateFor(browser, READER, PASSWORD);
  mkdirSync(SHOTS, { recursive: true });
});

test.afterAll(async () => {
  if (process.env.E2E_KEEP === "1") return;
  forgetStates();
  const result = limpiarFinanzas();
  expect(result.left, `FIN-REMIND-01: la limpieza no pudo borrar: ${result.left.join(" | ")}`).toEqual([]);
});

for (const env of [ENV_WIDE, ENV_TABLET, ENV_PHONE]) {
  test(`1 · ${env.name}: la página, sus pestañas y el calendario caben sin desbordar`, async ({ browser }) => {
    test.setTimeout(240_000);
    const { context, page } = await openAs(browser, ADMIN, env);
    try {
      await openFinance(page);
      // El resumen: por moneda, sin mezclar soles y dólares.
      const overdue = page.locator('[data-summary="overdue"]');
      await expect(overdue).toContainText("S/");
      await expect(overdue.locator('[data-currency="USD"]')).toContainText("50");
      await expect(page.getByText(/Hoy es .* \(fecha de la empresa\)/)).toContainText(new RegExp(`${Number(seed.today.slice(8, 10))} de`));
      await expectNoOverflow(page, `${env.name} lista de vencidos`);
      await page.screenshot({ path: `${SHOTS}/finance-${env.name}-overdue.png`, fullPage: true });

      // Calendario en pantalla ancha, agenda en la estrecha: nunca los dos a la vez.
      const grid = page.getByTestId("finance-calendar-grid");
      const agenda = page.getByTestId("finance-calendar-agenda");
      if (env.name === ENV_PHONE.name) {
        await expect(agenda).toBeVisible();
        await expect(grid).toBeHidden();
      } else if (env.name === ENV_WIDE.name) {
        await expect(grid).toBeVisible();
        await expect(agenda).toBeHidden();
      }
      await expectNoOverflow(page, `${env.name} calendario`);

      for (const tab of ["Próximos", "Hoy", "Parciales", "Completados", "Reglas", "Avisos"]) {
        await page.getByRole("tab", { name: new RegExp(`^${tab}`) }).click();
        await page.waitForTimeout(250);
        await expectNoOverflow(page, `${env.name} pestaña ${tab}`);
      }
      await page.screenshot({ path: `${SHOTS}/finance-${env.name}-notices.png`, fullPage: true });

      // El detalle (panel lateral) y un diálogo encima: el documento tampoco desborda.
      await page.getByRole("tab", { name: /^Vencidos/ }).click();
      await row(page, "Alquiler vencido").getByRole("button", { name: /Ver obligación/ }).click();
      const drawer = page.getByRole("dialog", { name: concept("Alquiler vencido") });
      await expect(drawer).toBeVisible();
      await expect(drawer.getByText("Pagos registrados")).toBeVisible();
      await expectDocumentFits(page, `${env.name} detalle`);
      await page.screenshot({ path: `${SHOTS}/finance-${env.name}-detail.png` });
      await page.keyboard.press("Escape");
      await expect(drawer).toBeHidden();
      // El foco vuelve al botón que abrió el panel.
      await expect(row(page, "Alquiler vencido").getByRole("button", { name: /Ver obligación/ })).toBeFocused();

      await row(page, "Alquiler vencido").getByRole("button", { name: /Registrar pago/ }).click();
      const dialog = page.getByRole("dialog", { name: "Registrar pago" });
      await expect(dialog.getByLabel(/Importe/)).toHaveValue("2500.00");
      await expectDocumentFits(page, `${env.name} diálogo de pago`);
      await page.keyboard.press("Escape");
      await expect(dialog).toBeHidden();
    } finally {
      await context.close();
    }
  });
}

test("2 · la campana muestra el aviso y su enlace abre ese pago (1280)", async ({ browser }) => {
  test.setTimeout(240_000);
  const { context, page } = await openAs(browser, ADMIN, ENV_WIDE);
  try {
    await openFinance(page);
    await page.getByRole("button", { name: "Notificaciones" }).click();
    const reminder = page.getByRole("link", { name: new RegExp(`Pago vencido: ${concept("Alquiler vencido")}`) });
    await expect(reminder).toBeVisible({ timeout: 30_000 });
    await expect(reminder).toHaveAttribute("href", `/admin/finance?obligation=${seed.ids.overdue}`);
    await reminder.click();
    const drawer = page.getByRole("dialog", { name: concept("Alquiler vencido") });
    await expect(drawer).toBeVisible({ timeout: 30_000 });
    await expect(page).toHaveURL(new RegExp(`obligation=${seed.ids.overdue}$`));
    await expect(drawer.getByText("Avisos enviados")).toBeVisible();
    await expect(drawer.getByText(/Se enviaron \d+ aviso\(s\) de este pago/)).toBeVisible();
    await expect(drawer.getByText(/Pago vencido: /)).toBeVisible();
    await page.screenshot({ path: `${SHOTS}/finance-bell-deeplink.png` });
    // Cerrar limpia la dirección.
    await page.keyboard.press("Escape");
    await expect(page).toHaveURL(/\/admin\/finance$/);
    // Leer el aviso no tocó la deuda.
    expect(hechos(seed.ids.overdue).balance).toBe("2500.00");
  } finally {
    await context.close();
  }
});

test("3 · el enlace de un aviso funciona también en el teléfono (390) y un id ajeno se explica", async ({ browser }) => {
  test.setTimeout(240_000);
  const { context, page } = await openAs(browser, ADMIN, ENV_PHONE);
  try {
    await openFinance(page, `?obligation=${seed.ids.today}`);
    const drawer = page.getByRole("dialog", { name: concept("Luz hoy") });
    await expect(drawer).toBeVisible({ timeout: 30_000 });
    await expect(drawer.getByText("vence hoy").first()).toBeVisible();
    await expectDocumentFits(page, "390 detalle por enlace");
    await page.keyboard.press("Escape");

    await page.goto("/admin/finance?obligation=99999999", { waitUntil: "domcontentloaded" });
    const alert = page.getByRole("alert").filter({ hasText: "No encontramos este pago" });
    await expect(alert).toBeVisible({ timeout: 60_000 });
    await alert.getByRole("button", { name: "Volver a la lista" }).click();
    await expect(alert).toBeHidden();
  } finally {
    await context.close();
  }
});

test("4 · filtros: la categoría se pide al servidor; la búsqueda filtra lo cargado; el día del calendario filtra la lista", async ({ browser }) => {
  test.setTimeout(240_000);
  const { context, page } = await openAs(browser, ADMIN, ENV_WIDE);
  try {
    await openFinance(page);
    await expect(row(page, "Alquiler vencido")).toBeVisible();
    await expect(row(page, "Licencia en dolares")).toBeVisible();
    await page.getByLabel("Categoría").selectOption({ label: `Alquiler ${TAG}` });
    await expect(row(page, "Licencia en dolares")).toHaveCount(0);
    await expect(row(page, "Alquiler vencido")).toBeVisible();
    await page.getByLabel("Categoría").selectOption({ label: "Todas las categorías" });
    await expect(row(page, "Licencia en dolares")).toBeVisible();

    await page.getByRole("tab", { name: /^Próximos/ }).click();
    await page.getByLabel("Buscar").fill("fibra");
    await expect(row(page, "Internet pronto")).toBeVisible();
    await expect(row(page, "Hosting parcial")).toHaveCount(0);
    await page.getByLabel("Buscar").fill("");

    // El día de hoy está marcado y tiene el pago que vence hoy.
    await page.getByTestId("finance-calendar-grid").getByRole("button", { pressed: false, name: /vence hoy/ }).first().click();
    await expect(page.getByText(/^Vencen el /)).toBeVisible();
    await expect(row(page, "Luz hoy")).toBeVisible();
    await expect(row(page, "Alquiler vencido")).toHaveCount(0);
    await page.getByRole("button", { name: "Quitar filtro de día" }).click();
    await expect(page.getByText(/^Vencen el /)).toHaveCount(0);
  } finally {
    await context.close();
  }
});

test("5 · quien sólo ve: no hay «Registrar pago» ni edición de reglas, sí «Posponer aviso»; la política es de lectura", async ({ browser }) => {
  test.setTimeout(240_000);
  const { context, page } = await openAs(browser, READER, ENV_WIDE);
  try {
    await openFinance(page);
    const r = row(page, "Alquiler vencido");
    await expect(r.getByRole("button", { name: /Ver obligación/ })).toBeVisible();
    await expect(r.getByRole("button", { name: /Posponer aviso/ })).toBeVisible();
    await expect(r.getByRole("button", { name: /Registrar pago/ })).toHaveCount(0);
    await page.getByRole("tab", { name: /^Reglas/ }).click();
    await expect(page.getByText("Seguro fijo " + TAG)).toBeVisible();
    await expect(page.getByRole("button", { name: /Pausar regla|Terminar regla|Editar regla/ })).toHaveCount(0);
    await page.getByRole("tab", { name: /^Avisos/ }).click();
    const form = page.getByRole("form", { name: "Política de avisos" });
    await expect(form).toContainText("modo lectura");
    await expect(form.getByLabel("Días antes del vencimiento")).toBeDisabled();
    await expect(form.getByRole("button", { name: /Guardar política/ })).toHaveCount(0);
  } finally {
    await context.close();
  }
});

test("6 · registrar un pago: parcial deja saldo; completo lo salda; el doble clic registra UNA vez", async ({ browser }) => {
  test.setTimeout(240_000);
  const { context, page } = await openAs(browser, ADMIN, ENV_WIDE);
  try {
    await openFinance(page);
    // Completo, con doble clic sobre el botón.
    await page.getByRole("tab", { name: /^Hoy/ }).click();
    await row(page, "Luz hoy").getByRole("button", { name: /Registrar pago/ }).click();
    const dialog = page.getByRole("dialog", { name: "Registrar pago" });
    await expect(dialog.getByLabel(/Importe/)).toHaveValue("300.00");
    await dialog.getByLabel(/Referencia/).fill("OP-E2E-1");
    await dialog.getByRole("button", { name: "Registrar pago" }).dblclick();
    await expect(dialog).toBeHidden({ timeout: 30_000 });
    await expect(page.getByRole("status").filter({ hasText: /Pago registrado/ })).toBeVisible();
    await expect(row(page, "Luz hoy")).toHaveCount(0);
    await expect(page.locator('[data-summary="today"]')).toContainText("Nada");
    const paid = hechos(seed.ids.today);
    expect(paid.payments.map((p) => p.amount)).toEqual(["300.00"]);
    expect(paid.balance).toBe("0.00");
    expect(paid.state).toBe("paid");

    // Parcial: queda saldo y sigue en Parciales.
    await page.getByRole("tab", { name: /^Parciales/ }).click();
    await row(page, "Hosting parcial").getByRole("button", { name: /Registrar pago/ }).click();
    const second = page.getByRole("dialog", { name: "Registrar pago" });
    await expect(second.getByLabel(/Importe/)).toHaveValue("40.00");
    await second.getByLabel(/Importe/).fill("15.00");
    await second.getByLabel("Forma de pago").selectOption("cash");
    await second.getByRole("button", { name: "Registrar pago" }).click();
    await expect(second).toBeHidden({ timeout: 30_000 });
    await expect(row(page, "Hosting parcial")).toContainText("Parcial");
    const partial = hechos(seed.ids.partial);
    expect(partial.payments.map((p) => p.amount).sort()).toEqual(["15.00", "60.00"]);
    expect(partial.balance).toBe("25.00");

    // Un importe de más: el servidor lo rechaza y el diálogo lo dice sin cerrarse.
    await row(page, "Hosting parcial").getByRole("button", { name: /Registrar pago/ }).click();
    const third = page.getByRole("dialog", { name: "Registrar pago" });
    await third.getByLabel(/Importe/).fill("999.00");
    await third.getByRole("button", { name: "Registrar pago" }).click();
    await expect(third.getByRole("alert")).toContainText("saldo pendiente");
    await expect(third).toBeVisible();
    await page.keyboard.press("Escape");
    expect(hechos(seed.ids.partial).payments).toHaveLength(2);

    // Revertir desde el detalle (finance.payments.reverse), con motivo: el saldo vuelve.
    await row(page, "Hosting parcial").getByRole("button", { name: /Ver obligación/ }).click();
    const drawer = page.getByRole("dialog", { name: concept("Hosting parcial") });
    await drawer.getByRole("button", { name: "Revertir este pago" }).last().click();
    await drawer.getByLabel("Motivo de la reversión").fill("Importe digitado por error");
    await drawer.getByRole("button", { name: "Confirmar reversión" }).click();
    await expect(drawer.getByText(/Revertido: Importe digitado por error/)).toBeVisible({ timeout: 30_000 });
    expect(hechos(seed.ids.partial).payments.filter((p) => p.reversed)).toHaveLength(1);
    await page.screenshot({ path: `${SHOTS}/finance-payment-reversed.png` });
  } finally {
    await context.close();
  }
});

test("7 · posponer un aviso silencia mis avisos y no toca la deuda; «Quitar pospuesto» lo deshace", async ({ browser }) => {
  test.setTimeout(240_000);
  const { context, page } = await openAs(browser, ADMIN, ENV_WIDE);
  try {
    await openFinance(page);
    const before = hechos(seed.ids.overdue);
    await row(page, "Alquiler vencido").getByRole("button", { name: /Posponer aviso/ }).click();
    const dialog = page.getByRole("dialog", { name: "Posponer aviso" });
    await expect(dialog).toContainText("sólo silencia tus avisos");
    await expect(dialog).toContainText("No cambia el pago");
    await dialog.getByRole("button", { name: "7 días" }).click();
    await expect(dialog).toBeHidden({ timeout: 30_000 });
    await expect(row(page, "Alquiler vencido")).toContainText("Mis avisos pospuestos hasta");
    await expect(row(page, "Alquiler vencido")).toContainText("Vencido");
    const after = hechos(seed.ids.overdue);
    expect(after.snooze).not.toBeNull();
    expect(after.balance).toBe(before.balance);
    expect(after.state).toBe(before.state);
    expect(after.payments).toEqual(before.payments);
    // El aplazamiento es de ESTA persona: la otra no lo tiene.
    expect(hechos(seed.ids.overdue, READER).snooze).toBeNull();

    await row(page, "Alquiler vencido").getByRole("button", { name: /Posponer aviso/ }).click();
    await page.getByRole("dialog", { name: "Posponer aviso" }).getByRole("button", { name: "Quitar pospuesto" }).click();
    await expect(row(page, "Alquiler vencido")).not.toContainText("pospuestos");
    expect(hechos(seed.ids.overdue).snooze).toBeNull();
  } finally {
    await context.close();
  }
});

test("8 · la política de avisos: quien la administra la edita y el servidor la guarda; un dato malo se dice", async ({ browser }) => {
  test.setTimeout(240_000);
  const { context, page } = await openAs(browser, ADMIN, ENV_WIDE);
  try {
    await openFinance(page);
    await page.getByRole("tab", { name: /^Avisos/ }).click();
    const form = page.getByRole("form", { name: "Política de avisos" });
    await expect(form).toContainText("notificación interna");
    await expect(form).toContainText("No se envía nada por WhatsApp");
    await expect(form).not.toContainText("modo lectura");
    await expect(form.getByLabel("Días antes del vencimiento")).toHaveValue("7, 3, 1, 0");

    await form.getByLabel("Días antes del vencimiento").fill("7, tres");
    await form.getByRole("button", { name: /Guardar política/ }).click();
    await expect(form.getByRole("alert")).toContainText("«tres» no es un número de días");
    expect(hechos(seed.ids.soon).offsets).toEqual([7, 3, 1, 0]);

    await form.getByLabel("Días antes del vencimiento").fill("10, 2, 0");
    await form.getByLabel(/Si ya venció/).fill("5");
    await form.getByLabel(/Máximo de avisos/).fill("3");
    await form.getByLabel(/Enviar también por correo/).check();
    await form.getByRole("button", { name: /Guardar política/ }).click();
    await expect(page.getByText("Política de avisos guardada.").first()).toBeVisible({ timeout: 30_000 });
    expect(hechos(seed.ids.soon).offsets).toEqual([10, 2, 0]);

    await page.reload({ waitUntil: "domcontentloaded" });
    await page.getByRole("tab", { name: /^Avisos/ }).click();
    const again = page.getByRole("form", { name: "Política de avisos" });
    await expect(again.getByLabel("Días antes del vencimiento")).toHaveValue("10, 2, 0");
    await expect(again.getByLabel(/Máximo de avisos/)).toHaveValue("3");
    await expect(again.getByLabel(/Enviar también por correo/)).toBeChecked();
    await expect(again).toContainText("Última revisión automática");
    await page.screenshot({ path: `${SHOTS}/finance-policy-saved.png`, fullPage: true });
  } finally {
    await context.close();
  }
});

test("9 · una regla se pausa y se reanuda desde la pestaña Reglas", async ({ browser }) => {
  test.setTimeout(240_000);
  const { context, page } = await openAs(browser, ADMIN, ENV_WIDE);
  try {
    await openFinance(page);
    await page.getByRole("tab", { name: /^Reglas/ }).click();
    const rule = page.locator("li[data-rule-id]").filter({ hasText: concept("Seguro fijo") });
    await expect(rule).toContainText("Semanal");
    await expect(rule).toContainText("Activa");
    await rule.getByRole("button", { name: /Pausar regla/ }).click();
    const dialog = page.getByRole("dialog", { name: "Pausar regla" });
    await dialog.getByLabel("Motivo").fill("Local cerrado por obras");
    await dialog.getByRole("button", { name: "Pausar" }).click();
    await expect(dialog).toBeHidden({ timeout: 30_000 });
    await expect(rule).toContainText("Pausada");
    await rule.getByRole("button", { name: /Reanudar regla/ }).click();
    await page.getByRole("dialog", { name: "Reanudar regla" }).getByRole("button", { name: "Reanudar" }).click();
    await expect(rule).toContainText("Activa", { timeout: 30_000 });
    // Editar el importe (el calendario ya tiene períodos: el día queda fijo).
    await rule.getByRole("button", { name: /Editar regla/ }).click();
    const edit = page.getByRole("dialog", { name: "Editar regla" });
    await expect(edit.getByLabel("Día de la semana")).toBeDisabled();
    await edit.getByLabel(/Importe/).fill("1900.00");
    await edit.getByRole("button", { name: "Guardar regla" }).click();
    await expect(edit).toBeHidden({ timeout: 30_000 });
    await expect(rule).toContainText(/1,900\.00/);
  } finally {
    await context.close();
  }
});
