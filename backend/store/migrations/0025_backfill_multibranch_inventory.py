"""
Phase 2D, step 2 of 3 — move the existing inventory into branches.

WHAT HISTORICAL DATA LOOKS LIKE
-------------------------------
Before this phase a product carried one integer and a Kardex line knew only its
product. Neither knows a location, because until now a company WAS a location.
Every row therefore has to be assigned to exactly one branch, and this migration
has to decide which — or refuse to.

THE RULE, IN ORDER
------------------
For each company that has stock, movements or orders:

  1. `settings.INVENTORY_MIGRATION_BRANCHES` names it explicitly:
         {'company-slug': 'Nombre de la sucursal'}
     The operator has answered; use their answer.
  2. The company has exactly ONE active branch.
     Unambiguous by construction — not "the first of several", but "the only one
     there is". This is the case for every single-shop installation, which is
     every installation that exists today.
  3. Anything else — several active branches, or none at all — RAISES.

WHY IT RAISES INSTEAD OF CHOOSING
---------------------------------
With two branches and one integer there is no fact in the database that says
where those units are. Splitting them, or picking the lowest id, would write a
number that looks authoritative and is fiction; every count, every report and
every replenishment decision downstream would inherit it, and nobody would ever
find out, because a wrong stock figure looks exactly like a right one.

Refusing is loud, happens once, at deploy time, in front of the person who can
answer, and the error names the companies and the setting to fill in. That is
the whole trade: a five-minute interruption instead of silent corruption.

WHAT IT WRITES
--------------
  BranchStock            one row per product with stock, in the chosen branch,
                         quantity = Product.inventory.
  StockMovement          company + branch on every historical line, using the
                         SAME branch, so a migrated Kardex still reconstructs
                         its balance.
  Order.fulfillment_branch   the same branch, so the webhook of an order paid
                         mid-deploy takes stock off the shelf it was sold from.
  Company.default_inventory_branch   set when it was unset, so the storefront
                         keeps checking out without manual configuration.
  Membership.branch_access_mode + MembershipBranchAccess
                         see `migrate_branch_access` below.

WHAT IT DOES NOT WRITE
----------------------
No `initial_stock` movements for the migrated balances. The Kardex already
contains the history that produced them; inventing an opening line per product
would double-count every unit and rewrite commercial history that is not this
migration's to rewrite. The pre-2D lines ARE the opening balance.

IDS, TIMESTAMPS, QUANTITIES, ACTORS AND ORDERS ARE PRESERVED. Nothing is
deleted, renumbered or recreated.
"""

from django.conf import settings
from django.db import migrations



def _configured_branch_names():
    """Operator overrides, keyed by company slug. Empty when unset."""
    raw = getattr(settings, 'INVENTORY_MIGRATION_BRANCHES', None) or {}
    return {str(k).strip().lower(): str(v).strip() for k, v in raw.items()}


def _resolve_historical_branch(company, Branch, overrides, problems):
    """
    The one branch a company's historical stock belongs to, or None + a problem.

    Never guesses. See the module docstring for why.
    """
    configured = overrides.get((company.slug or '').lower())
    if configured:
        branch = Branch.objects.filter(company=company, name=configured).first()
        if branch is None:
            problems.append(
                f'  - "{company.name}" (slug={company.slug}): '
                f'INVENTORY_MIGRATION_BRANCHES apunta a la sucursal '
                f'"{configured}", que no existe en esta empresa.'
            )
            return None
        return branch

    active = list(Branch.objects.filter(company=company, is_active=True).order_by('pk'))
    if len(active) == 1:
        return active[0]

    if not active:
        problems.append(
            f'  - "{company.name}" (slug={company.slug}): tiene inventario '
            f'histórico pero NINGUNA sucursal activa. Cree una sucursal antes '
            f'de migrar.'
        )
        return None

    names = ', '.join(f'"{b.name}"' for b in active)
    problems.append(
        f'  - "{company.name}" (slug={company.slug}): tiene {len(active)} '
        f'sucursales activas ({names}) y un único stock histórico sin ubicación. '
        f'Indique cuál lo tiene.'
    )
    return None


def _companies_needing_a_branch(Company, Product, StockMovement, Order):
    """
    Companies with something to place: stock, Kardex history or orders.

    A company with none of the three needs no branch decision — there is nothing
    to put anywhere — and must not block the migration just for existing.
    """
    with_stock = set(
        Product.objects.filter(inventory__gt=0).values_list('company_id', flat=True)
    )
    with_movements = set(
        StockMovement.objects.values_list('product__company_id', flat=True)
    )
    with_orders = set(Order.objects.values_list('company_id', flat=True))
    ids = {i for i in (with_stock | with_movements | with_orders) if i is not None}
    return list(Company.objects.filter(pk__in=ids).order_by('pk'))


def migrate_inventory_to_branches(apps, schema_editor):
    Branch = apps.get_model('store', 'Branch')
    BranchStock = apps.get_model('store', 'BranchStock')
    Company = apps.get_model('store', 'Company')
    Order = apps.get_model('store', 'Order')
    Product = apps.get_model('store', 'Product')
    StockMovement = apps.get_model('store', 'StockMovement')

    overrides = _configured_branch_names()
    problems = []
    chosen = {}

    for company in _companies_needing_a_branch(Company, Product, StockMovement, Order):
        branch = _resolve_historical_branch(company, Branch, overrides, problems)
        if branch is not None:
            chosen[company.pk] = branch

    if problems:
        raise RuntimeError(
            'No se puede migrar el inventario a sucursales sin adivinar dónde '
            'está el stock histórico.\n\n'
            + '\n'.join(problems)
            + '\n\nIndique la sucursal de cada empresa afectada en settings, por '
              'ejemplo:\n\n'
              "    INVENTORY_MIGRATION_BRANCHES = {\n"
              "        'mi-empresa': 'Tienda principal',\n"
              "    }\n\n"
              'y vuelva a ejecutar la migración. No se ha modificado nada.'
        )

    # --- 1. stock ---------------------------------------------------------
    to_create = []
    for product in Product.objects.filter(inventory__gt=0).iterator():
        branch = chosen.get(product.company_id)
        if branch is None:
            continue
        to_create.append(BranchStock(
            branch_id=branch.pk,
            product_id=product.pk,
            quantity=product.inventory,
            minimum_stock=0,
            target_stock=0,
        ))
    if to_create:
        BranchStock.objects.bulk_create(to_create, batch_size=500)

    # --- 2. Kardex --------------------------------------------------------
    # Same branch as the stock, so a migrated card still adds up. Updated per
    # company rather than per row: one UPDATE each instead of one per movement.
    for company_id, branch in chosen.items():
        StockMovement.objects.filter(product__company_id=company_id).update(
            company_id=company_id, branch_id=branch.pk,
        )

    # --- 3. orders --------------------------------------------------------
    for company_id, branch in chosen.items():
        Order.objects.filter(company_id=company_id, fulfillment_branch__isnull=True).update(
            fulfillment_branch_id=branch.pk,
        )

    # --- 4. the storefront's shipping branch ------------------------------
    # Only when unset. An operator who already chose keeps their choice.
    for company_id, branch in chosen.items():
        Company.objects.filter(
            pk=company_id, default_inventory_branch__isnull=True,
        ).update(default_inventory_branch_id=branch.pk)


def migrate_branch_access(apps, schema_editor):
    """
    Turn the old single `Membership.branch` into an explicit access mode.

      branch IS NULL      → mode ALL. Historically this meant "no particular
                            branch", which in practice meant company-wide scope:
                            nothing restricted these people, and this migration
                            must not start.
      branch IS NOT NULL  → mode SELECTED, with a grant for that branch. That
                            field always meant "this person works here"; making
                            it authority is the point of the phase.

    `Membership.branch` is LEFT IN PLACE and keeps pointing where it pointed. It
    is now the member's DEFAULT branch — which branch the internal control opens
    on — and no longer decides anything. Dropping it in the same migration that
    changes its meaning would make this step impossible to review.
    """
    Membership = apps.get_model('store', 'Membership')
    MembershipBranchAccess = apps.get_model('store', 'MembershipBranchAccess')

    Membership.objects.filter(branch__isnull=True).update(branch_access_mode='all')
    Membership.objects.filter(branch__isnull=False).update(branch_access_mode='selected')

    grants = [
        MembershipBranchAccess(
            membership_id=m.pk, branch_id=m.branch_id, is_active=True,
        )
        for m in Membership.objects.filter(branch__isnull=False).iterator()
        if not MembershipBranchAccess.objects.filter(
            membership_id=m.pk, branch_id=m.branch_id,
        ).exists()
    ]
    if grants:
        MembershipBranchAccess.objects.bulk_create(grants, batch_size=500)


def unmigrate_inventory(apps, schema_editor):
    """
    Reverse: DELETE NOTHING that existed before this phase.

    The BranchStock rows and the branch grants are creations of 0025, so they go.
    `Product.inventory` is left exactly as it is — it was never emptied, and the
    forward migration only mirrored it. The Kardex keeps every row; only the two
    columns this phase added are cleared. Nothing that predates Phase 2D is
    touched, so a rollback loses no history.
    """
    BranchStock = apps.get_model('store', 'BranchStock')
    Company = apps.get_model('store', 'Company')
    Membership = apps.get_model('store', 'Membership')
    MembershipBranchAccess = apps.get_model('store', 'MembershipBranchAccess')
    Order = apps.get_model('store', 'Order')
    StockMovement = apps.get_model('store', 'StockMovement')

    StockMovement.objects.update(company_id=None, branch_id=None)
    Order.objects.update(fulfillment_branch_id=None)
    Company.objects.update(default_inventory_branch_id=None)
    Membership.objects.update(branch_access_mode='all')
    MembershipBranchAccess.objects.all().delete()
    BranchStock.objects.all().delete()


def verify_no_orphan_movements(apps, schema_editor):
    """
    Refuse to continue if any Kardex line is still homeless.

    0026 is about to make these columns NOT NULL. Failing here, with a count and
    an explanation, beats failing three lines later with an integrity error that
    says nothing about why.
    """
    StockMovement = apps.get_model('store', 'StockMovement')
    orphans = StockMovement.objects.filter(branch__isnull=True).count()
    if orphans:
        raise RuntimeError(
            f'Quedan {orphans} movimientos de stock sin sucursal asignada. '
            f'Esto ocurre si un producto pertenece a una empresa que no pudo '
            f'resolverse. Revise INVENTORY_MIGRATION_BRANCHES y vuelva a '
            f'ejecutar la migración.'
        )


def noop(apps, schema_editor):
    """Nothing to undo: the check above writes nothing."""


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0024_multibranch_inventory_nullable'),
    ]

    operations = [
        migrations.RunPython(migrate_inventory_to_branches, unmigrate_inventory),
        migrations.RunPython(migrate_branch_access, migrations.RunPython.noop),
        migrations.RunPython(verify_no_orphan_movements, noop),
    ]
