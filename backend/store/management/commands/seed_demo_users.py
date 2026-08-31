"""
Development-only demo users — TEMPORARY.

WHY THIS EXISTS
---------------
While the platform is mid-transition, exercising a role by hand means creating a
user, a profile role, a Membership, a CompanyRole and an assignment every time.
This command does that once, reproducibly, so `/auth` can offer quick logins
during development.

WHAT IT IS NOT
--------------
These accounts are NOT part of the SaaS product and must never reach production.
They are ordinary users in every respect: they authenticate through the real
login, get real JWT cookies, pass CSRF, and are subject to exactly the same
permission checks as anyone else. There is no bypass anywhere in this file.

The command refuses to run when settings.DEBUG is False, and offers no flag to
override that — a `--force-production` escape hatch is exactly how development
fixtures end up in a live database.

DEVELOPMENT BRIDGE / LEGACY TRANSITION
--------------------------------------
Internal demo users deliberately carry BOTH authority systems:

    UserProfile.role          (legacy — still authorises the commercial endpoints)
    Membership + CompanyRole  (SaaS — authorises the new company endpoints)

That duplication is a symptom of the transition, not the target architecture.
Once Product/Order/Inventory are tenantised, the legacy half goes away.

REMOVAL
-------
    python manage.py seed_demo_users --purge
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

# Not a secret. Development fixtures only.
DEMO_PASSWORD = 'Demo123!'  # noqa: S105 — DEVELOPMENT ONLY

# `.invalid` is reserved by RFC 2606 and can never be a real address. It is the
# signature that marks an account as created by this command: a username may
# collide with a real user, an address in this domain cannot.
DEMO_EMAIL_DOMAIN = 'example.invalid'

# username, legacy UserProfile.role, company role slug, company area slug
DEMO_INTERNAL_USERS = (
    ('dev_sales', 'sales', 'ventas', 'ventas'),
    ('dev_inventory', 'inventory', 'inventario', 'inventario'),
    ('dev_technician', 'technician', 'servicio-tecnico', 'servicio-tecnico'),
    ('dev_admin', 'admin', 'administrador', 'administracion'),
)

DEMO_CUSTOMER_USERNAME = 'dev_customer'
DEMO_MASTER_USERNAME = 'dev_master'

ALL_DEMO_USERNAMES = (
    DEMO_CUSTOMER_USERNAME,
    *(u for u, _r, _rs, _a in DEMO_INTERNAL_USERS),
    DEMO_MASTER_USERNAME,
)


def demo_email(username: str) -> str:
    return f'{username}@{DEMO_EMAIL_DOMAIN}'


class Command(BaseCommand):
    help = (
        'Crea usuarios demo de DESARROLLO para probar roles. '
        'Solo funciona con DEBUG=True. Usar --purge para eliminarlos.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--company-slug',
            help='Slug de la empresa activa donde crear las membresías internas.',
        )
        parser.add_argument(
            '--purge', action='store_true',
            help='Elimina los usuarios demo en lugar de crearlos.',
        )

    # -- guards ---------------------------------------------------------------

    def _require_debug(self):
        if not settings.DEBUG:
            raise CommandError('Los usuarios demo solo pueden crearse en desarrollo.')

    def _get_user_model(self):
        from django.contrib.auth import get_user_model
        return get_user_model()

    def _claim_or_abort(self, User, username):
        """
        Return the existing demo user for `username`, or None if it is free.

        Aborts if the username is taken by an account that this command did not
        create. Silently rewriting a real user's password and role because their
        name happens to start with `dev_` would be a genuine account takeover.
        """
        existing = User.objects.filter(username=username).first()
        if existing is None:
            return None
        if (existing.email or '').lower() != demo_email(username):
            raise CommandError(
                f'El usuario "{username}" ya existe con otra identidad '
                f'(email: {existing.email or "sin email"}). '
                f'No se modificará. Renombre esa cuenta o elija otro entorno.'
            )
        return existing

    # -- entry point ----------------------------------------------------------

    def handle(self, *args, **options):
        self._require_debug()
        if options['purge']:
            return self._purge()
        company_slug = options.get('company_slug')
        if not company_slug:
            raise CommandError(
                'Se requiere --company-slug. Ejemplo: '
                '--company-slug mi-empresa'
            )
        return self._seed(company_slug)

    # -- purge ----------------------------------------------------------------

    @transaction.atomic
    def _purge(self):
        from store.models import Membership, MembershipRoleAssignment

        User = self._get_user_model()
        removed, skipped = [], []

        for username in ALL_DEMO_USERNAMES:
            user = User.objects.filter(username=username).first()
            if user is None:
                continue
            if (user.email or '').lower() != demo_email(username):
                # Same name, different identity — never ours to delete.
                skipped.append(username)
                continue

            MembershipRoleAssignment.objects.filter(membership__user=user).delete()
            Membership.objects.filter(user=user).delete()
            user.delete()
            removed.append(username)

        for username in removed:
            self.stdout.write(self.style.SUCCESS(f'  eliminado  {username}'))
        for username in skipped:
            self.stdout.write(self.style.WARNING(
                f'  OMITIDO    {username} — existe con otra identidad, no se toca'
            ))
        self.stdout.write(self.style.SUCCESS(
            f'\nUsuarios demo eliminados: {len(removed)}.'
        ))

    # -- seed -----------------------------------------------------------------

    @transaction.atomic
    def _seed(self, company_slug):
        from store.company_provisioning import provision_company_access_defaults
        from store.models import (
            Branch, Company, CompanyArea, CompanyRole, Membership,
            MembershipRoleAssignment,
        )

        User = self._get_user_model()

        company = Company.objects.filter(slug=company_slug).first()
        if company is None:
            raise CommandError(f'No existe una empresa con slug "{company_slug}".')
        if not company.is_active:
            raise CommandError(f'La empresa "{company_slug}" está desactivada.')

        # Reuse the single provisioning service — no second copy of the presets.
        provision_company_access_defaults(company)

        # --- 1. external customer: no Membership at all ---
        customer = self._upsert_user(User, DEMO_CUSTOMER_USERNAME, legacy_role='customer')
        Membership.objects.filter(user=customer).delete()

        # --- 2-5. internal staff ---
        for username, legacy_role, role_slug, area_slug in DEMO_INTERNAL_USERS:
            user = self._upsert_user(User, username, legacy_role=legacy_role)

            membership, _ = Membership.objects.get_or_create(
                user=user, company=company,
                defaults={'role': legacy_role, 'is_active': True},
            )
            if membership.role != legacy_role or not membership.is_active:
                membership.role = legacy_role
                membership.is_active = True
                membership.save()

            role = CompanyRole.objects.filter(company=company, slug=role_slug).first()
            area = CompanyArea.objects.filter(company=company, slug=area_slug).first()
            if role is None:
                raise CommandError(
                    f'La empresa "{company_slug}" no tiene el rol preset "{role_slug}".'
                )

            assignment, created = MembershipRoleAssignment.objects.get_or_create(
                membership=membership, role=role, area=area,
                defaults={'is_active': True},
            )
            if not created and not assignment.is_active:
                assignment.is_active = True
                assignment.save()

        # --- 5b. the storefront needs somewhere to ship from (Phase 2D) ---
        #
        # Stock lives in branches now. A development company with no fulfillment
        # branch shows an empty catalogue and refuses every checkout, which reads
        # as a broken environment rather than as missing configuration — so the
        # seeder makes sure there is one and says which.
        from store.tenancy import company_fulfillment_branch

        if company_fulfillment_branch(company) is None:
            first = Branch.objects.filter(
                company=company, is_active=True,
            ).order_by('pk').first()
            if first is None:
                raise CommandError(
                    f'La empresa "{company_slug}" no tiene ninguna sucursal activa. '
                    f'Cree una antes de sembrar usuarios demo: sin sucursal no hay '
                    f'stock, y sin stock la tienda no vende.'
                )
            company.default_inventory_branch = first
            company.save(update_fields=['default_inventory_branch', 'updated_at'])

        # --- 6. platform master ---
        # Authority comes from is_superuser ALONE. No Membership is created:
        # a Membership would suggest company authority is what makes a master.
        master = self._upsert_user(
            User, DEMO_MASTER_USERNAME,
            legacy_role='superadmin',  # legacy compatibility only
            is_superuser=True, is_staff=True,
        )
        Membership.objects.filter(user=master).delete()

        self._report(company)

    def _upsert_user(self, User, username, *, legacy_role,
                     is_superuser=False, is_staff=False):
        """Create or refresh one demo account, never touching a foreign one."""
        user = self._claim_or_abort(User, username)
        if user is None:
            user = User.objects.create_user(
                username=username, email=demo_email(username), password=DEMO_PASSWORD,
            )

        user.email = demo_email(username)
        user.is_superuser = is_superuser
        user.is_staff = is_staff
        user.set_password(DEMO_PASSWORD)
        user.save()

        # The post_save signal creates the profile; set the legacy role on it.
        profile = user.profile
        if profile.role != legacy_role:
            profile.role = legacy_role
            profile.save(update_fields=['role', 'updated_at'])

        return user

    def _report(self, company):
        rows = [
            ('dev_customer', 'Cliente / E-commerce', '—'),
            ('dev_sales', 'Ventas', company.slug),
            ('dev_inventory', 'Inventario', company.slug),
            ('dev_technician', 'Servicio Técnico', company.slug),
            ('dev_admin', 'Admin de empresa', company.slug),
            ('dev_master', 'PLATFORM MASTER (is_superuser)', '—'),
        ]
        self.stdout.write(self.style.SUCCESS(
            f'\nUsuarios demo listos en la empresa "{company.name}".\n'
        ))
        self.stdout.write(f'  {"usuario":<16}{"perfil":<34}empresa')
        self.stdout.write(f'  {"-" * 16}{"-" * 34}{"-" * 20}')
        for username, label, scope in rows:
            self.stdout.write(f'  {username:<16}{label:<34}{scope}')
        company.refresh_from_db()
        branch = company.default_inventory_branch
        self.stdout.write(
            f'\n  Sucursal de despacho: {branch.name if branch else "(sin configurar)"}'
        )
        self.stdout.write(
            '  Alcance de sucursales del personal demo: todas '
            '(Membership.branch_access_mode = "all")'
        )
        self.stdout.write(self.style.WARNING(
            f'\n  Contraseña para todos: {DEMO_PASSWORD}'
        ))
        self.stdout.write(self.style.WARNING(
            '  SOLO DESARROLLO — eliminar con: '
            'python manage.py seed_demo_users --purge\n'
        ))
