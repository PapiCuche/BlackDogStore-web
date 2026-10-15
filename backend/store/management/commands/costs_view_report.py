"""
DEVICE-COMMERCE-01 · DC1 review — who can see costs, after `migrate`.

Migration 0126 grants `costs.view` only to an `Administrador` role nobody
edited (the rule of 0094/0095/0104). A company whose administrator role was
customised gets nothing, and from then on only a platform MASTER (or that
company's own role manager) can grant it. This command says HOW MANY companies
are in that situation, so the step «grant it by hand where needed» is not
forgotten after a deployment.

Read-only and counts only: no company is named in a terminal or a log, and
nothing is granted.
"""
from django.core.management.base import BaseCommand

from store.models import Company, CompanyRole

CAPABILITY = 'costs.view'
ADMIN_SLUG = 'administrador'


class Command(BaseCommand):
    help = 'Cuenta las empresas sin nadie con «Ver costos y márgenes» (costs.view). Sólo lectura.'

    def handle(self, *args, **options):
        companies = list(Company.objects.filter(is_active=True).values_list('pk', flat=True))
        roles: dict[int, list] = {}
        for company_id, slug, capabilities in CompanyRole.objects.filter(
            company_id__in=companies, is_active=True,
        ).values_list('company_id', 'slug', 'capabilities'):
            roles.setdefault(company_id, []).append((slug, set(capabilities or [])))

        admin_without = no_admin = nobody = 0
        for company_id in companies:
            mine = roles.get(company_id, [])
            admins = [caps for slug, caps in mine if slug == ADMIN_SLUG]
            if not admins:
                no_admin += 1
            elif not any(CAPABILITY in caps for caps in admins):
                admin_without += 1
            if not any(CAPABILITY in caps for _slug, caps in mine):
                nobody += 1

        self.stdout.write(f'Empresas activas: {len(companies)}')
        self.stdout.write(f'Rol «administrador» sin costs.view: {admin_without}')
        self.stdout.write(f'Sin rol «administrador»: {no_admin}')
        self.stdout.write(f'Sin ningún rol activo con costs.view: {nobody}')
        self.stdout.write(
            'No se cambió nada. Donde haga falta, otorga «Ver costos y márgenes» a mano '
            '(Accesos › Roles de la empresa, o un MASTER).'
        )
