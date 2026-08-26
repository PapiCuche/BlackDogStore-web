"""
Seed the pilot tenant and backfill memberships — SaaS Phase 1.

This is a DATA migration on purpose. The pilot company's identity lives in the
database, not in a constant in the business layer, so the platform can host a
completely different business later without touching any code path.

What it does:
  1. Creates the pilot Company (idempotent, by slug).
  2. Creates its initial Branch from the address already used by the PDF and
     email services.
  3. Creates a Membership for every existing user whose UserProfile role is a
     staff role, mirroring that role.

What it deliberately does NOT do:
  - It does not create memberships for `customer` users. Customers are buyers of
    a storefront, not staff of a tenant; giving them a membership would turn a
    shopper into a company member the moment multi-tenant permissions go live.
  - It does not touch UserProfile. `get_user_role()` keeps working unchanged.
  - It does not add company_id to any business model.

FUTURE DEBT — neutral bootstrap:
  This seed is correct for THIS installation, whose historical data belongs to
  Black Dog Store. It is not a universal SaaS rule. A fresh installation sold to
  a third party must not silently acquire a "Black Dog Store" tenant, so a later
  phase needs a neutral bootstrap path — e.g. a management command that creates
  the first tenant from operator input, with this data migration guarded to run
  only when pre-existing store data is present. Not redesigned here on purpose:
  changing it now would alter the migration chain of the live installation.
"""

from django.db import migrations

# Values for the FIRST tenant only. Read once, at migration time, and written to
# the database. Nothing in the application layer may import these.
_PILOT = {
    'name': 'Black Dog Store',
    'legal_name': 'CMAU CORP E.I.R.L.',
    'tax_id': '20610159886',
    'slug': 'black-dog-store',
}
_PILOT_BRANCH = {
    'name': 'Tienda principal',
    'address': 'Octavio Muñoz Najar 238, Tienda 104, Arequipa, Perú',
    'phone': '+51 936 449 536',
    'email': '',
}

# Staff roles get a mirrored membership; 'customer' deliberately does not.
_STAFF_ROLES = ('sales', 'inventory', 'technician', 'admin', 'superadmin')


def seed_pilot_company(apps, schema_editor):
    Company = apps.get_model('store', 'Company')
    Branch = apps.get_model('store', 'Branch')
    Membership = apps.get_model('store', 'Membership')
    UserProfile = apps.get_model('store', 'UserProfile')

    company, _ = Company.objects.get_or_create(
        slug=_PILOT['slug'],
        defaults={
            'name': _PILOT['name'],
            'legal_name': _PILOT['legal_name'],
            'tax_id': _PILOT['tax_id'],
            'is_active': True,
        },
    )

    branch, _ = Branch.objects.get_or_create(
        company=company,
        name=_PILOT_BRANCH['name'],
        defaults={
            'address': _PILOT_BRANCH['address'],
            'phone': _PILOT_BRANCH['phone'],
            'email': _PILOT_BRANCH['email'],
            'is_active': True,
        },
    )

    # Backfill: mirror each existing staff role into a membership of the pilot.
    profiles = UserProfile.objects.filter(role__in=_STAFF_ROLES).select_related('user')
    for profile in profiles:
        Membership.objects.get_or_create(
            user_id=profile.user_id,
            company=company,
            defaults={'role': profile.role, 'branch': branch, 'is_active': True},
        )


def unseed_pilot_company(apps, schema_editor):
    """
    Reverse: undo ONLY what seed_pilot_company created.

    This reverse is deliberately conservative. Anything an operator added after
    the migration ran — a membership granted through the admin API, an extra
    branch, a second company — must survive untouched.

    Rules:
      - Only memberships that still carry the exact backfill signature are
        removed (pilot company + pilot branch + active + role identical to the
        user's UserProfile staff role). A membership whose role was changed, that
        was deactivated, that points at another branch, or that belongs to a
        non-staff user was NOT created here and is left alone.
      - The pilot branch is removed only when no membership still references it.
        Membership.branch uses SET_NULL, so deleting the branch while surviving
        memberships reference it would silently rewrite those rows to branch=NULL.
      - The pilot company is removed only when nothing at all is left hanging off
        it (no branches, no memberships, no audit logs).
      - Users are never touched. Other companies are never touched.
    """
    Company = apps.get_model('store', 'Company')
    Branch = apps.get_model('store', 'Branch')
    Membership = apps.get_model('store', 'Membership')
    UserProfile = apps.get_model('store', 'UserProfile')

    company = Company.objects.filter(slug=_PILOT['slug']).first()
    if not company:
        return

    branch = Branch.objects.filter(company=company, name=_PILOT_BRANCH['name']).first()

    # --- memberships: only the ones this migration itself created -------------
    staff_role_by_user = dict(
        UserProfile.objects
        .filter(role__in=_STAFF_ROLES)
        .values_list('user_id', 'role')
    )
    seeded_ids = [
        m.pk
        for m in Membership.objects.filter(
            company=company, branch=branch, is_active=True,
        )
        if staff_role_by_user.get(m.user_id) == m.role
    ]
    if seeded_ids:
        Membership.objects.filter(pk__in=seeded_ids).delete()

    # --- branch: only while nothing references it -----------------------------
    if branch is not None:
        if Membership.objects.filter(branch=branch).exists():
            return  # operator data survives; leave branch and company in place
        branch.delete()

    # --- company: only while it is completely empty ---------------------------
    if (
        Branch.objects.filter(company=company).exists()
        or Membership.objects.filter(company=company).exists()
        or company.audit_logs.exists()
    ):
        return

    company.delete()


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0014_saas_company_branch_membership'),
    ]

    operations = [
        migrations.RunPython(seed_pilot_company, unseed_pilot_company),
    ]
