import { mkdirSync } from "node:fs";
import { test, expect, type Page } from "@playwright/test";
import { TAG, shell, signIn } from "./inventory-first-support";

/**
 * Decisión D — búsqueda en el borrador de una transferencia, de punta a punta y SIN MOCKS (Chromium + API real).
 *
 * El administrador crea un borrador de la sucursal principal a una segunda sucursal, busca un producto y le pone 5,
 * busca otro (sin tildes ni mayúsculas) y le pone 2, limpia la búsqueda, revisa con «Sólo con cantidad», guarda,
 * despacha y recibe. Después se mira la BASE: las dos líneas, el estado y el stock que se movió de una sucursal a otra.
 *
 * Usa el `shell()` del recorrido de inventario primero (con su GUARDA de base local y desechable) y la MARCA `TAG` de la
 * corrida. Todo lo que crea lleva la marca y se borra al final (`E2E_KEEP=1` lo deja).
 */

const SHOTS = process.env.TRF_SHOTS ?? "test-results/trf-shots";
const MOBILE = { width: 390, height: 844 };
const DESKTOP = { width: 1280, height: 800 };

type TrfSeed = {
  principal: { id: number; name: string };
  branch2: { id: number; name: string };
  cable: { id: number; name: string; code: string };
  camara: { id: number; name: string; code: string };
  serial: { id: number; name: string };
};

type TrfState = {
  transfer: { id: number; status: string; items: Record<string, number> } | null;
  stock: Record<string, Record<string, number>>;
  movements: { product: number; branch: number; type: string; quantity: number }[];
};

function sembrarTransferencia(): TrfSeed {
  return shell<TrfSeed>(`
from django.contrib.auth import get_user_model
from django.db import transaction
from store import inventory_services, product_codes
from store.models import Branch, Company, Product, ProductBarcode, StockMovement
User = get_user_model()
company = Company.objects.get(slug=data['slug'])
principal = company.default_inventory_branch
actor = User.objects.get(username='dev_admin')
tag = data['tag']
branch2 = Branch.objects.create(company=company, name='Sucursal QTR ' + tag)

def product(name, slug, stock, serialized=False):
    p = Product.objects.create(
        company=company, name=name, slug=slug, price=Decimal('19.90'), inventory=0, is_active=True,
        is_published_online=False, is_serialized=serialized,
    )
    with transaction.atomic():
        product_codes.ensure_code(p)
    if stock:
        inventory_services.create_stock_movement(
            branch=principal, product_id=p.pk, movement_type=StockMovement.INITIAL_STOCK, quantity=stock,
            reason='[E2E] stock inicial de la prueba de transferencias', actor=actor,
        )
    return p

cable = product('Cable QTR ' + tag, ('cable-qtr-' + tag).lower(), 20)
camara = product('Cámara QTR ' + tag + ' Wi-Fi', ('camara-qtr-' + tag).lower(), 10)
serial = product('iPhone QTR ' + tag + ' 128 GB', ('iphone-qtr-' + tag).lower(), 0, serialized=True)
code = lambda p: ProductBarcode.objects.filter(product=p).values_list('code', flat=True).first() or ''
out({
    'principal': {'id': principal.pk, 'name': principal.name},
    'branch2': {'id': branch2.pk, 'name': branch2.name},
    'cable': {'id': cable.pk, 'name': cable.name, 'code': code(cable)},
    'camara': {'id': camara.pk, 'name': camara.name, 'code': code(camara)},
    'serial': {'id': serial.pk, 'name': serial.name},
})
`);
}

function estado(): TrfState {
  return shell<TrfState>(`
from store.models import BranchStock, Product, StockMovement, StockTransfer
tag = data['tag']
transfer = StockTransfer.objects.filter(reason__icontains=tag).order_by('-pk').first()
products = Product.objects.filter(name__icontains=tag)
stock = {}
for row in BranchStock.objects.filter(product__in=products):
    stock.setdefault(str(row.product_id), {})[str(row.branch_id)] = row.quantity
out({
    'transfer': None if transfer is None else {
        'id': transfer.pk, 'status': transfer.status,
        'items': {str(i.product_id): i.quantity for i in transfer.items.all()},
    },
    'stock': stock,
    'movements': [
        {'product': m.product_id, 'branch': m.branch_id, 'type': m.movement_type, 'quantity': m.quantity}
        for m in StockMovement.objects.filter(product__in=products).exclude(movement_type='initial_stock').order_by('pk')
    ],
})
`);
}

function limpiarTransferencia(): { removed: Record<string, number>; left: string[] } {
  return shell(`
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
drop('movements', StockMovement.objects.filter(product__in=products))
drop('transfers', StockTransfer.objects.filter(reason__icontains=tag))
drop('label_batches', LabelBatch.objects.filter(items__product__in=products))
drop('branch_stock', BranchStock.objects.filter(product__in=products))
drop('barcodes', ProductBarcode.objects.filter(product__in=products))
drop('products', products)
drop('branches', Branch.objects.filter(name__icontains=tag))
out({'removed': removed, 'left': left})
`);
}

async function shot(page: Page, name: string) {
  mkdirSync(SHOTS, { recursive: true });
  const prefix = (page.viewportSize()?.width ?? DESKTOP.width) <= 500 ? "m390" : "d1280";
  await page.screenshot({ path: `${SHOTS}/${prefix}-${name}.png`, fullPage: true });
}

/** El documento no se ensancha más que la ventana (mismo criterio que `inventory-receive.spec.ts`). */
async function expectNoOverflow(page: Page, what: string) {
  const size = await page.evaluate(() => {
    const inner = window.innerWidth;
    const scroll = document.scrollingElement!.scrollWidth;
    const offenders = scroll <= inner ? [] : [...document.body.querySelectorAll("*")]
      .filter((el) => el.getBoundingClientRect().right > inner + 1 && !el.parentElement?.closest("[class*='overflow-x-auto'], [class*='overflow-auto']"))
      .slice(0, 6)
      .map((el) => `${el.tagName.toLowerCase()}.${String((el as HTMLElement).className).slice(0, 60)} -> ${Math.round(el.getBoundingClientRect().right)}px`);
    return { scroll, inner, offenders };
  });
  expect(size.scroll, `${what}: la página mide ${size.scroll}px en una ventana de ${size.inner}px; se pasan: ${size.offenders.join(" | ")}`)
    .toBeLessThanOrEqual(size.inner);
}

test.describe.configure({ mode: "serial" });

let seed: TrfSeed;

test.beforeAll(() => {
  seed = sembrarTransferencia();
});

test.afterAll(() => {
  if (process.env.E2E_KEEP === "1") return;
  const result = limpiarTransferencia();
  expect(result.left, `no se pudo limpiar: ${result.left.join(" | ")}`).toEqual([]);
});

test("borrador: buscar, conservar cantidades, revisar, guardar, despachar y recibir", async ({ page }) => {
  test.setTimeout(240_000);
  const errors: string[] = [];
  page.on("pageerror", (err) => errors.push(err.message));
  page.on("dialog", (dialog) => void dialog.accept());
  await page.setViewportSize(DESKTOP);
  await signIn(page);

  // 1. Crear el borrador desde la lista.
  await page.goto("/admin/inventory/transfers", { waitUntil: "networkidle" });
  await page.getByLabel("Origen").selectOption(String(seed.principal.id));
  await page.getByLabel("Destino").selectOption(String(seed.branch2.id));
  await page.getByLabel("Motivo").fill(`Prueba de búsqueda ${TAG}`);
  await page.getByRole("button", { name: "Crear borrador" }).click();
  await expect.poll(() => estado().transfer?.status ?? null).toBe("draft");
  const transferId = estado().transfer!.id;
  await page.getByRole("link", { name: `#${transferId}`, exact: true }).click();
  await expect(page.getByRole("heading", { name: `Transferencia #${transferId}` })).toBeVisible();

  const search = page.getByRole("searchbox", { name: "Buscar producto" });
  const quantities = page.getByRole("spinbutton");
  const cable = page.getByRole("spinbutton", { name: seed.cable.name, exact: true });
  const camara = page.getByRole("spinbutton", { name: seed.camara.name, exact: true });
  await expect(search).toBeVisible();
  await expect(page.getByText(/^Mostrando \d+ de \d+$/)).toBeVisible();
  const total = Number((await page.getByText(/^Mostrando \d+ de \d+$/).textContent())!.match(/de (\d+)/)![1]);

  // 2. Por nombre: sólo el cable.
  await search.fill(`Cable ${TAG}`);
  await expect(quantities).toHaveCount(1);
  await cable.fill("5");
  // The name is readable next to its quantity (a `w-full` input once squeezed the label to 0 px).
  const label = page.locator(`label[for="ti-${seed.cable.id}"]`);
  await expect(label).toBeVisible();
  expect((await label.boundingBox())!.width).toBeGreaterThan(60);
  await shot(page, "01-busqueda-cable");

  // 3. Otro producto, sin tilde y en minúsculas: la cámara. El cable deja de verse pero conserva su 5.
  await search.fill(`camara ${TAG.toLowerCase()}`);
  await expect(quantities).toHaveCount(1);
  await expect(cable).toHaveCount(0);
  await camara.fill("2");

  // 4. Por código.
  if (seed.cable.code) {
    await search.fill(seed.cable.code);
    await expect(cable).toHaveValue("5");
  }

  // 5. Lo de la corrida: los dos por cantidad, nunca el equipo con serie (el servidor no lo transfiere).
  await search.fill(TAG);
  await expect(quantities).toHaveCount(2);
  await expect(page.getByRole("spinbutton", { name: seed.serial.name })).toHaveCount(0);

  // 6. Sin búsqueda, «Sólo con cantidad» muestra exactamente lo que se va a enviar.
  await page.getByRole("button", { name: "Limpiar búsqueda" }).click();
  await expect(search).toHaveValue("");
  await expect(page.getByText(`Mostrando ${total} de ${total}`)).toBeVisible();
  await expect(cable).toHaveValue("5");
  await expect(camara).toHaveValue("2");
  await page.getByRole("checkbox", { name: "Sólo con cantidad" }).check();
  await expect(quantities).toHaveCount(2);
  await expect(page.getByText(`Mostrando 2 de ${total}`)).toBeVisible();
  await expect(page.getByText("2 línea(s) con cantidad · 7 unidad(es)")).toBeVisible();
  await shot(page, "02-solo-con-cantidad");

  // 390 px: sin desbordar, con el buscador y el filtro a la vista.
  await page.setViewportSize(MOBILE);
  await expectNoOverflow(page, "borrador con «Sólo con cantidad»");
  await page.getByRole("checkbox", { name: "Sólo con cantidad" }).uncheck();
  await search.fill(`Cable ${TAG}`);
  await expect(quantities).toHaveCount(1);
  await expectNoOverflow(page, "borrador con búsqueda");
  expect((await label.boundingBox())!.width).toBeGreaterThan(60);
  await shot(page, "03-busqueda-movil");
  await search.fill("zzz-no-existe");
  await expect(page.getByText("Ningún producto coincide con «zzz-no-existe».")).toBeVisible();
  await expectNoOverflow(page, "borrador sin coincidencias");
  await shot(page, "04-sin-coincidencias-movil");
  await page.setViewportSize(DESKTOP);

  // 7. Guardar con la búsqueda puesta en otro producto: se envían las dos líneas, también la oculta.
  await search.fill(`camara ${TAG}`);
  await expect(quantities).toHaveCount(1);
  // Lo que cuenta es lo que guardó el servidor: se lee en la base, no en la respuesta.
  await page.getByRole("button", { name: "Guardar líneas" }).click();
  await expect.poll(() => estado().transfer?.items).toEqual({ [String(seed.cable.id)]: 5, [String(seed.camara.id)]: 2 });
  await expect(page.getByText("2 línea(s) · creada por")).toBeVisible();

  // 8. Despachar y recibir: las reglas de stock son las de siempre.
  await page.getByRole("button", { name: "Despachar" }).click();
  await expect.poll(() => estado().transfer?.status).toBe("in_transit");
  await expect(page.getByText(/El stock ya salió de/)).toBeVisible();
  await page.getByRole("button", { name: "Recibir" }).click();
  await expect.poll(() => estado().transfer?.status).toBe("received");
  await expect(page.getByRole("cell", { name: seed.cable.name, exact: true })).toBeVisible();
  await expect(page.getByRole("cell", { name: seed.camara.name, exact: true })).toBeVisible();
  await shot(page, "05-recibida");
  await page.setViewportSize(MOBILE);
  await expectNoOverflow(page, "transferencia recibida");
  await shot(page, "06-recibida-movil");

  // 9. La base: dos líneas, stock movido de la principal a la segunda sucursal, y un par salida/entrada por línea.
  const after = estado();
  expect(after.transfer).toEqual({
    id: transferId, status: "received",
    items: { [String(seed.cable.id)]: 5, [String(seed.camara.id)]: 2 },
  });
  const p = String(seed.principal.id);
  const b = String(seed.branch2.id);
  expect(after.stock[String(seed.cable.id)]).toEqual({ [p]: 15, [b]: 5 });
  expect(after.stock[String(seed.camara.id)]).toEqual({ [p]: 8, [b]: 2 });
  expect(after.stock[String(seed.serial.id)] ?? {}).toEqual({});
  const moves = after.movements.map((m) => `${m.product}@${m.branch}:${m.type}:${m.quantity}`).sort();
  expect(moves).toEqual([
    `${seed.cable.id}@${p}:transfer_out:5`,
    `${seed.cable.id}@${b}:transfer_in:5`,
    `${seed.camara.id}@${p}:transfer_out:2`,
    `${seed.camara.id}@${b}:transfer_in:2`,
  ].sort());
  expect(errors, `errores de la página: ${errors.join(" | ")}`).toEqual([]);
});
