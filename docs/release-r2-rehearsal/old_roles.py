"""Run with the OLD code (6834bc2): `python3 manage.py shell < old_roles.py`.
What the old role editor does with a role that holds a capability code it does not know
(0148/0149/0152 add five): reads it, saves it with its stored list (what the old panel sends),
and saves it without touching the capabilities. Prints status codes only."""
import os
from django.conf import settings
settings.ALLOWED_HOSTS = list(settings.ALLOWED_HOSTS) + ['testserver']
from rest_framework.test import APIClient
from store.models import Company, CompanyRole
from store.tenancy import has_capability
from django.contrib.auth import get_user_model

company = Company.objects.get(slug=os.environ.get('COMPANY_SLUG', 'black-dog-store'))
role = CompanyRole.objects.get(company=company, slug='inventario')
c = APIClient(raise_request_exception=False)
c.post('/api/auth/login/', {'username': os.environ.get('SMOKE_USER', 'dev_admin'), 'password': os.environ.get('SMOKE_PASS', 'Demo123!')}, format='json')
res = {'list': c.get('/api/admin/roles/').status_code,
       'detail': c.get(f'/api/admin/roles/{role.pk}/').status_code}
stored = list(CompanyRole.objects.get(pk=role.pk).capabilities)
r = c.patch(f'/api/admin/roles/{role.pk}/', {'capabilities': stored}, format='json')
res['save_with_stored_list'] = (r.status_code, str(r.content[:140], 'utf-8', 'replace'))
r = c.patch(f'/api/admin/roles/{role.pk}/', {'description': 'editada en el ensayo'}, format='json')
res['save_without_capabilities'] = r.status_code
res['codes_kept'] = sorted(set(CompanyRole.objects.get(pk=role.pk).capabilities) - set(stored)) or 'same'
user = get_user_model().objects.get(username=os.environ.get('SMOKE_USER', 'dev_inventory'))
res['old_has_capability_inventory.adjust'] = has_capability(user, company, 'inventory.adjust')
print('OLD_ROLES', res)
