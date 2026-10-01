"""
C2.2A.1 — dar las capacidades fiscales a los presets que nunca divergieron.

Octava vez que aparece este patrón. Lo distinto aquí es a QUIÉN llega, y es una
decisión de producto, no mecánica:

  · `Administrador` — las dos. Tiene todo por definición.
  · `Ventas` — SÓLO VER. El mostrador necesita saber si la factura de un cliente
    ya salió; declarar algo ante SUNAT es otra cosa. Concederle `issue` por
    defecto sería dar autoridad tributaria a todo el personal de caja sin que
    nadie lo haya decidido. Una tienda que lo quiera se lo da a un rol propio,
    que es para lo que existe un catálogo de capacidades.
  · `Inventario` — no. El stock no emite comprobantes.
  · `Servicio Técnico` y `Supervisor Técnico` — no. Reparar no es facturar.

LOS DISCRIMINADORES, como estableció 0053: igualdad exacta del conjunto para el
preset grande, y la conjunción completa —slug, nombre, una de las descripciones
de la plataforma y el conjunto exacto— para el pequeño.

El conjunto anterior de `Ventas` va escrito a mano y CONGELADO. Importarlo en
vivo compararía la base de datos contra lo que el preset significa hoy, en un
proceso cuyo código es siempre más nuevo que sus datos.

CUALQUIER OTRO ROL SE DEJA EN PAZ. Una empresa que personalizó sus permisos no
recibe capacidades nuevas en silencio.
"""

from django.db import migrations

ADMIN_CAPABILITIES = ('sales.fiscal.view', 'sales.fiscal.issue')
SALES_CAPABILITIES = ('sales.fiscal.view',)

SALES_NAME = 'Ventas'
SALES_SLUG = 'ventas'
#: Todas las descripciones que la plataforma ha escrito para este preset: las
#: concesiones que lo ampliaron nunca reescribieron la frase, y buscar sólo la
#: actual saltaría exactamente los roles que aquéllas repararon.
SALES_DESCRIPTIONS = (
    'Operación comercial: pedidos y notas de venta internas.',
    'Equivalente al rol legacy "sales".',
)
#: Lo que tenía `_SALES_CAPS` justo antes de C2.2A.1.
SALES_PREVIOUS = frozenset({
    'company.view', 'products.view', 'reports.view',
    'sales.orders.view', 'sales.orders.manage', 'sales.notes.manage',
    'sales.pos.use',
    'service.customers.view', 'service.customers.manage',
    'service.devices.view', 'service.devices.manage',
    'service.orders.create', 'service.orders.view',
    'service.payments.manage',
})

ALL_NEW = frozenset(ADMIN_CAPABILITIES)


def grant(apps, schema_editor):
    CompanyRole = apps.get_model('store', 'CompanyRole')

    from store.capabilities import ASSIGNABLE_CAPABILITY_CODES

    previous_admin_preset = frozenset(ASSIGNABLE_CAPABILITY_CODES) - ALL_NEW
    admins = 0
    sales = 0

    for role in CompanyRole.objects.all().iterator():
        held = frozenset(role.capabilities or [])

        if previous_admin_preset and held == previous_admin_preset:
            role.capabilities = sorted(previous_admin_preset | ALL_NEW)
            role.save(update_fields=['capabilities', 'updated_at'])
            admins += 1
            continue

        if (
            role.slug == SALES_SLUG
            and role.name == SALES_NAME
            and role.description in SALES_DESCRIPTIONS
            and held == SALES_PREVIOUS
        ):
            role.capabilities = sorted(held | set(SALES_CAPABILITIES))
            role.save(update_fields=['capabilities', 'updated_at'])
            sales += 1

    if admins or sales:
        print(
            f'\n  C2.2A.1 — capacidades fiscales otorgadas a {admins} rol(es) '
            f'administrador y sólo la de consulta a {sales} rol(es) de ventas '
            f'sin modificar'
        )


def revoke(apps, schema_editor):
    CompanyRole = apps.get_model('store', 'CompanyRole')
    for role in CompanyRole.objects.all().iterator():
        current = list(role.capabilities or [])
        remaining = [c for c in current if c not in ALL_NEW]
        if len(remaining) != len(current):
            role.capabilities = remaining
            role.save(update_fields=['capabilities', 'updated_at'])


class Migration(migrations.Migration):

    dependencies = [('store', '0081_fiscal_documents')]

    operations = [migrations.RunPython(grant, revoke)]
