"""
Backfill the existing catalogue onto the pilot tenant — SaaS Phase 2B.

WHY A DATA MIGRATION
--------------------
Every Category and Product in an existing installation belongs, historically, to
the business that has been running the store. Phase 2B gives them an owner; this
migration says who that owner is, once, in the database.

HOW THE PILOT TENANT IS IDENTIFIED
----------------------------------
By the SIGNATURE left by migration 0015, not by a hardcoded name: the pilot is
the OLDEST Company row (lowest primary key), because 0015 created it before any
company could be added through the API or the admin.

Using the oldest row rather than the slug "black-dog-store" keeps this migration
neutral — an installation whose first tenant is a different business backfills
onto ITS own first tenant, with no code change.

WHAT IT DOES NOT DO
-------------------
  - It does not create a Company. If none exists it refuses loudly rather than
    inventing a tenant to own someone's catalogue.
  - It does not touch Order, OrderItem, CartItem, StockMovement or Review. Those
    reach their tenant through Product and are tenantised in later phases.
  - It does not give a newly created Company any products: a new tenant starts
    with an empty catalogue.
"""

from django.db import migrations


def backfill_catalog_company(apps, schema_editor):
    Company = apps.get_model('store', 'Company')
    Category = apps.get_model('store', 'Category')
    Product = apps.get_model('store', 'Product')

    orphan_categories = Category.objects.filter(company__isnull=True)
    orphan_products = Product.objects.filter(company__isnull=True)

    if not orphan_categories.exists() and not orphan_products.exists():
        return  # nothing to adopt

    pilot = Company.objects.order_by('pk').first()
    if pilot is None:
        raise RuntimeError(
            'No existe ninguna empresa a la que asignar el catálogo existente. '
            'La migración 0015 debería haber creado la empresa piloto; '
            'restáurela antes de aplicar 0019.'
        )

    orphan_categories.update(company=pilot)
    orphan_products.update(company=pilot)


def unbackfill_catalog_company(apps, schema_editor):
    """
    Reverse: release the catalogue from the pilot again.

    Only clears rows that still point at the pilot, so a product an operator
    later moved to another tenant is left alone. Nothing is deleted — this
    migration never created a Category or a Product, so it never removes one.
    """
    Company = apps.get_model('store', 'Company')
    Category = apps.get_model('store', 'Category')
    Product = apps.get_model('store', 'Product')

    pilot = Company.objects.order_by('pk').first()
    if pilot is None:
        return

    Category.objects.filter(company=pilot).update(company=None)
    Product.objects.filter(company=pilot).update(company=None)


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0018_catalog_company_nullable'),
    ]

    operations = [
        migrations.RunPython(backfill_catalog_company, unbackfill_catalog_company),
    ]
