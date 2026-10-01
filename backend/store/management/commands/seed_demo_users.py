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

FISCAL BETA (ERP-FISCAL-6)
--------------------------
    python manage.py seed_demo_users --company-slug <slug> --fiscal-beta

Prepares the DEMO fiscal series (F001 for facturas, B001 for boletas) in the
SUNAT BETA environment so the till can offer electronic receipts in development.
These codes are development data, NOT SaaS defaults: nothing in the model, the
series resolver or a migration assumes them. The flag requires DEBUG=True and
FISCAL_ENVIRONMENT="beta" and fails closed otherwise; it reuses a series that
already resolves, never touches an existing counter, and refuses to choose when
two series are equally valid. Enabling BETA does not send anything to SUNAT.
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

# H4.1.1 — una persona que COMPRA en la tienda y además TRABAJA en la empresa.
# Una cuenta puede ser las dos cosas a la vez, y la web tiene que ofrecerle las
# dos superficies. Cliente por su ficha `Customer`; técnico por su Membership y
# su rol de empresa. Ninguna de las dos se deduce de la otra.
DEMO_STAFF_CUSTOMER_USERNAME = 'dev_customer_technician'
DEMO_STAFF_CUSTOMER_ROLE = ('servicio-tecnico', 'servicio-tecnico')  # rol, área

ALL_DEMO_USERNAMES = (
    DEMO_CUSTOMER_USERNAME,
    *(u for u, _r, _rs, _a in DEMO_INTERNAL_USERS),
    DEMO_STAFF_CUSTOMER_USERNAME,
    DEMO_MASTER_USERNAME,
)


# E2E-02 — a worker the browser tests may break.
#
# The staff screen is tested by deactivating somebody, and deactivating retires
# role assignments on purpose. Doing that to one of the accounts above left it
# without capabilities for every spec that logs in with it. This one is created
# only with `--e2e-fixtures`, nobody logs in with it, it is NOT offered on the
# demo card (it is not in ALL_DEMO_USERNAMES) and re-seeding restores it.
DEMO_E2E_STAFF_USERNAME = 'dev_e2e_staff'
DEMO_E2E_STAFF_ROLE = ('ventas', 'ventas')  # rol, área


# SVC-FUNC-01 — a repair the browser tests may take a payment on.
#
# Proving that a technician can record a payment needs an order whose quote the
# CUSTOMER approved. A browser test cannot leave one behind: `purge_e2e_data`
# refuses to delete an order with a quote or a payment, because outside a test
# that is somebody's work. So the seed owns one, marked with a text of its own
# — deliberately NOT «[E2E]», which is the browser purge's marker.
E2E_SERVICE_FIXTURE_ISSUE = '[FIXTURE-E2E] Cobro de servicio técnico'
E2E_SERVICE_FIXTURE_NOTE = '[FIXTURE-E2E] creado por seed_demo_users --e2e-fixtures'
E2E_SERVICE_FIXTURE_TECHNICIAN = 'dev_technician'
E2E_SERVICE_FIXTURE_PRICE = '120.00'


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
        parser.add_argument(
            '--fiscal-beta', action='store_true',
            help=(
                'Prepara series fiscales DEMO (F001 factura, B001 boleta) en el '
                'ambiente SUNAT BETA para la empresa. Exige DEBUG=True y '
                'FISCAL_ENVIRONMENT=beta; reutiliza series existentes y nunca '
                'toca un correlativo.'
            ),
        )
        parser.add_argument(
            '--e2e-fixtures', action='store_true',
            help=(
                'Crea además un trabajador desechable (dev_e2e_staff) para las '
                'pruebas de navegador que desactivan personal. No es una cuenta '
                'de acceso ni aparece en la tarjeta de desarrollo.'
            ),
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
        self._seed(company_slug)
        if options.get('fiscal_beta'):
            self._seed_fiscal_beta(company_slug)
        if options.get('e2e_fixtures'):
            self._seed_e2e_fixtures(company_slug)

    # -- purge ----------------------------------------------------------------

    @transaction.atomic
    def _purge(self):
        from store.models import Customer, Membership, MembershipRoleAssignment

        User = self._get_user_model()
        removed, skipped = [], []
        fixtures = self._purge_e2e_service_fixture()
        if fixtures:
            self.stdout.write(self.style.SUCCESS(
                f'  eliminado  {fixtures} reparación(es) fixture de E2E'
            ))

        for username in (*ALL_DEMO_USERNAMES, DEMO_E2E_STAFF_USERNAME):
            user = User.objects.filter(username=username).first()
            if user is None:
                continue
            if (user.email or '').lower() != demo_email(username):
                # Same name, different identity — never ours to delete.
                skipped.append(username)
                continue

            MembershipRoleAssignment.objects.filter(membership__user=user).delete()
            Membership.objects.filter(user=user).delete()
            # Su ficha de cliente también. `Customer.user` es SET_NULL: borrar sólo
            # la cuenta dejaría un cliente huérfano con el nombre de la demo.
            Customer.objects.filter(user=user).delete()
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

        # --- 5c. cliente Y técnico (H4.1.1) ---
        staff_customer = self._upsert_user(
            User, DEMO_STAFF_CUSTOMER_USERNAME, legacy_role='customer',
        )
        membership, _ = Membership.objects.get_or_create(
            user=staff_customer, company=company,
            defaults={'role': 'technician', 'is_active': True},
        )
        if not membership.is_active:
            membership.is_active = True
            membership.save(update_fields=['is_active'])
        role_slug, area_slug = DEMO_STAFF_CUSTOMER_ROLE
        role = CompanyRole.objects.filter(company=company, slug=role_slug).first()
        area = CompanyArea.objects.filter(company=company, slug=area_slug).first()
        if role is None:
            raise CommandError(
                f'La empresa "{company_slug}" no tiene el rol preset "{role_slug}".'
            )
        assignment, created = MembershipRoleAssignment.objects.get_or_create(
            membership=membership, role=role, area=area, defaults={'is_active': True},
        )
        if not created and not assignment.is_active:
            assignment.is_active = True
            assignment.save()
        from store.models import Customer
        Customer.objects.get_or_create(
            company=company, user=staff_customer,
            defaults={
                'first_name': 'Cliente', 'last_name': 'y Técnico',
                'email': demo_email(DEMO_STAFF_CUSTOMER_USERNAME),
                'notes': 'Cuenta demo: compra en la tienda y trabaja en servicio técnico.',
            },
        )

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

    # -- fiscal beta ----------------------------------------------------------

    #: Las series DEMO, por tipo de comprobante. Sólo existen en este comando.
    @transaction.atomic
    def _seed_e2e_fixtures(self, company_slug):
        """
        The disposable worker of E2E-02. Idempotent: running it again puts the
        membership and its role back the way a test may have left them.
        """
        from store.models import (
            Company, CompanyArea, CompanyRole, Membership, MembershipRoleAssignment,
        )

        User = self._get_user_model()
        company = Company.objects.get(slug=company_slug)
        worker = self._upsert_user(User, DEMO_E2E_STAFF_USERNAME, legacy_role='customer')

        membership, _ = Membership.objects.get_or_create(
            user=worker, company=company, defaults={'role': 'sales', 'is_active': True},
        )
        if not membership.is_active:
            membership.is_active = True
            membership.save(update_fields=['is_active'])

        role_slug, area_slug = DEMO_E2E_STAFF_ROLE
        role = CompanyRole.objects.filter(company=company, slug=role_slug).first()
        area = CompanyArea.objects.filter(company=company, slug=area_slug).first()
        if role is None:
            raise CommandError(
                f'La empresa "{company_slug}" no tiene el rol preset "{role_slug}".'
            )
        assignment, created = MembershipRoleAssignment.objects.get_or_create(
            membership=membership, role=role, area=area, defaults={'is_active': True},
        )
        if not created and not assignment.is_active:
            assignment.is_active = True
            assignment.save()

        self.stdout.write(self.style.SUCCESS(
            f'  fixture E2E  {DEMO_E2E_STAFF_USERNAME} — trabajador desechable, '
            f'no es una cuenta de acceso'
        ))
        self._seed_e2e_service_fixture(company, decided_by=worker)

    def _seed_e2e_service_fixture(self, company, *, decided_by):
        """
        One approved repair, assigned to the demo technician, with money owed.

        Built through the SERVICES, the same ones the application calls: the
        order is received, assigned, quoted, published and approved, so every
        rule that guards a real repair guarded this one. Nothing of the shop is
        involved — no sale, no stock, no receipt.

        Idempotent while it is useful: a fixture that still owes money is kept;
        once the tests have paid it off, the next seed opens another.
        """
        from decimal import Decimal

        from store import service_services as service
        from store.models import (
            Branch, Customer, Device, RepairOrder, RepairQuoteItem, RepairStatusCode,
        )

        User = self._get_user_model()
        for order in RepairOrder.objects.filter(
            company=company, reported_issue=E2E_SERVICE_FIXTURE_ISSUE,
        ).order_by('-pk'):
            if service.service_payment_summary(order)['outstanding'] >= Decimal('1.00'):
                self.stdout.write(self.style.SUCCESS(
                    f'  fixture E2E  {order.number} — reparación aprobada con saldo'
                ))
                return order

        technician = User.objects.get(username=E2E_SERVICE_FIXTURE_TECHNICIAN)
        branch = Branch.objects.filter(company=company, is_active=True).order_by('pk').first()
        if branch is None:
            # A repair is received AT a branch. The disposable worker above does
            # not need one, so this is said and skipped rather than failed.
            self.stdout.write(self.style.WARNING(
                f'  fixture E2E  sin reparación: "{company.slug}" no tiene sucursal activa'
            ))
            return None

        # `.first()` and not `get_or_create`: the marker is a text, not a unique
        # key, and a second marked row must not stop the seed.
        marked = {'company': company, 'notes': E2E_SERVICE_FIXTURE_NOTE}
        customer = Customer.objects.filter(**marked).order_by('pk').first() or Customer.objects.create(
            customer_type=Customer.TYPE_PERSON, first_name='Fixture', last_name='Cobro E2E',
            **marked,
        )
        device = (
            Device.objects.filter(customer=customer, **marked).order_by('pk').first()
            or Device.objects.create(
                customer=customer, device_type=Device.TYPE_PHONE, brand='Fixture',
                model='Cobro E2E', **marked,
            )
        )

        order = service.create_repair_order_with_assignment(
            technician=technician, company=company, branch=branch, customer=customer,
            device=device, reported_issue=E2E_SERVICE_FIXTURE_ISSUE, actor=technician,
        )
        service.transition_repair_order(
            repair_order=order, to_status=RepairStatusCode.DIAGNOSING, actor=technician,
        )
        diagnostic = service.create_diagnostic(
            repair_order=order, actor=technician,
            description='Fixture de pruebas de navegador.',
            recommended_action='Servicio de prueba.',
        )
        quote = service.create_quote(repair_order=order, diagnostic=diagnostic, actor=technician)
        service.add_quote_item(
            quote=quote, item_type=RepairQuoteItem.TYPE_SERVICE,
            description='Servicio técnico de prueba', quantity=1,
            unit_price=E2E_SERVICE_FIXTURE_PRICE,
        )
        service.publish_quote(quote=quote, actor=technician)
        quote.refresh_from_db()
        service.record_quote_decision(
            quote=quote, customer=customer, user=decided_by, decision='approve',
        )

        order.refresh_from_db()
        self.stdout.write(self.style.SUCCESS(
            f'  fixture E2E  {order.number} — reparación aprobada, asignada a '
            f'{E2E_SERVICE_FIXTURE_TECHNICIAN}'
        ))
        return order

    def _purge_e2e_service_fixture(self):
        """
        Remove the repairs `_seed_e2e_service_fixture` created — and only those.

        `TechnicianAssignment.technician` is PROTECT, so the demo technician
        cannot be deleted while a fixture is assigned to them. These rows were
        written by this command and are found by its own exact marker; nothing a
        person created carries it.
        """
        from store.models import (
            AdminAuditLog, Customer, Device, Notification, NotificationEvent,
            RepairDelivery, RepairDiagnostic, RepairOrder, RepairPayment, RepairQuote,
            RepairQuoteDecision, RepairStatusHistory, TechnicianAssignment,
        )

        orders = RepairOrder.objects.filter(reported_issue=E2E_SERVICE_FIXTURE_ISSUE)
        order_ids = list(orders.values_list('pk', flat=True))
        NotificationEvent.objects.filter(
            target_type='repair_order', target_id__in=order_ids,
        ).delete()
        Notification.objects.filter(
            target_type='repair_order', target_id__in=order_ids,
        ).delete()
        AdminAuditLog.objects.filter(
            target_type='repair_order', target_id__in=[str(pk) for pk in order_ids],
        ).delete()
        # Somebody may have kept working on a fixture by hand. Everything that
        # hangs from the order goes with it, or the purge would stop half-way
        # and leave the demo accounts behind.
        for order in orders:
            order.part_usages.all().delete()
            order.quality_checks.all().delete()
            order.executions.all().delete()
        RepairDelivery.objects.filter(repair_order_id__in=order_ids).delete()
        RepairPayment.objects.filter(repair_order_id__in=order_ids).delete()
        RepairQuoteDecision.objects.filter(repair_order_id__in=order_ids).delete()
        RepairQuote.objects.filter(repair_order_id__in=order_ids).delete()
        RepairDiagnostic.objects.filter(repair_order_id__in=order_ids).delete()
        TechnicianAssignment.objects.filter(repair_order_id__in=order_ids).delete()
        RepairStatusHistory.objects.filter(repair_order_id__in=order_ids).delete()
        orders.delete()
        # A marked device or customer that somebody gave an order of their own
        # is no longer only the fixture's: it stays.
        Device.objects.filter(
            notes=E2E_SERVICE_FIXTURE_NOTE, repair_orders__isnull=True,
        ).delete()
        Customer.objects.filter(
            notes=E2E_SERVICE_FIXTURE_NOTE, devices__isnull=True, repair_orders__isnull=True,
        ).delete()
        return len(order_ids)

    DEMO_FISCAL_SERIES = (('01', 'F001', 'factura'), ('03', 'B001', 'boleta'))

    @transaction.atomic
    def _seed_fiscal_beta(self, company_slug):
        """
        Una serie de factura y una de boleta resolubles para la sucursal de
        despacho de la empresa, en BETA. Idempotente y no destructivo.

        Por cada tipo: si `resolve_series` ya devuelve una serie, se reutiliza
        tal cual —correlativo incluido—. Si no hay ninguna activa, se crea la
        DEMO a nivel de empresa. Si hay varias y ninguna resuelve, se PARA: la
        ambigüedad es exactamente lo que el resolutor se niega a decidir, y un
        comando de desarrollo no tiene más autoridad que él para hacerlo.
        """
        from store import fiscal_config
        from store.models import Company, FiscalEnvironment, FiscalSeries

        configured = getattr(settings, 'FISCAL_ENVIRONMENT', None)
        if configured != FiscalEnvironment.BETA:
            raise CommandError(
                f'--fiscal-beta sólo prepara el ambiente «{FiscalEnvironment.BETA}»; '
                f'este servidor tiene FISCAL_ENVIRONMENT={configured!r}. No se toca nada.'
            )
        try:
            environment = fiscal_config.resolve_environment()
        except fiscal_config.FiscalConfigError as exc:
            raise CommandError(str(exc))

        company = Company.objects.get(slug=company_slug)
        company.refresh_from_db()
        branch = company.default_inventory_branch

        self.stdout.write(self.style.SUCCESS(
            f'\nSeries fiscales DEMO ({environment}) para "{company.name}":'
        ))
        for document_type, demo_series, noun in self.DEMO_FISCAL_SERIES:
            try:
                existing = fiscal_config.resolve_series(
                    company, branch=branch, document_type=document_type,
                )
            except fiscal_config.FiscalConfigError:
                existing = None
            if existing is not None:
                self.stdout.write(
                    f'  reutilizada  {existing.series} ({noun}) — siguiente '
                    f'correlativo {existing.next_number}, sin cambios'
                )
                continue

            active = FiscalSeries.objects.filter(
                company=company, document_type=document_type,
                environment=environment, is_active=True,
            ).count()
            if active:
                raise CommandError(
                    f'La empresa tiene {active} serie(s) de {noun} activa(s) en '
                    f'«{environment}» y ninguna resuelve para «{branch}». Resuelva '
                    f'la ambigüedad a mano: este comando no elige ni desactiva series.'
                )
            if FiscalSeries.objects.filter(
                company=company, document_type=document_type,
                series=demo_series, environment=environment,
            ).exists():
                raise CommandError(
                    f'Ya existe una serie {demo_series} ({noun}) desactivada en '
                    f'«{environment}». Reactívela o elija otra a mano; este comando '
                    f'no reescribe series.'
                )
            created = FiscalSeries.objects.create(
                company=company, branch=None, document_type=document_type,
                series=demo_series, environment=environment,
            )
            self.stdout.write(self.style.SUCCESS(
                f'  creada       {created.series} ({noun}) — serie DEMO de empresa, '
                f'ambiente {environment}'
            ))

        if not company.tax_id:
            self.stdout.write(self.style.WARNING(
                '  La empresa no tiene RUC configurado: la caja podrá elegir el '
                'comprobante, pero la emisión fallará hasta que lo tenga.'
            ))
        self.stdout.write(self.style.WARNING(
            '  SOLO DESARROLLO/BETA — ninguna serie apunta a producción y nada '
            'se envía a SUNAT por preparar esto.\n'
        ))

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
        # REACTIVAR ES PARTE DE REFRESCAR.
        #
        # Esto no estaba, y el hueco era silencioso: un demo desactivado desde
        # el módulo de usuarios del panel se quedaba desactivado para siempre,
        # porque volver a sembrar refrescaba correo, flags y contraseña pero no
        # esto. El login respondía «No active account found with the given
        # credentials» — un mensaje que suena a contraseña incorrecta y manda a
        # buscar en el sitio equivocado.
        #
        # Una cuenta demo no tiene estado que preservar: existe para poder
        # entrar. Si no se puede entrar con ella, no está cumpliendo su única
        # función. Desactivarla de verdad es `--purge`.
        user.is_active = True
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
            ('dev_customer_technician', 'Cliente y técnico', company.slug),
            ('dev_master', 'PLATFORM MASTER (is_superuser)', '—'),
        ]
        self.stdout.write(self.style.SUCCESS(
            f'\nUsuarios demo listos en la empresa "{company.name}".\n'
        ))
        self.stdout.write(f'  {"usuario":<26}{"perfil":<34}empresa')
        self.stdout.write(f'  {"-" * 26}{"-" * 34}{"-" * 20}')
        for username, label, scope in rows:
            self.stdout.write(f'  {username:<26}{label:<34}{scope}')
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
