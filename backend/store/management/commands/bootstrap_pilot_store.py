"""
Prepare the pilot store's data on a NEW production database, before the real
catalogue is loaded.

    python manage.py bootstrap_pilot_store            # says what it would do
    python manage.py bootstrap_pilot_store --apply    # does it

WHY THIS IS A COMMAND AND NOT A MIGRATION

Migration 0002 wrote three sample products when the shop was a prototype, and
0025 gave them stock in the first branch. A database created today still gets
them: a shop published as it comes would offer 45 units that do not exist, with
no Kardex line behind them. A migration that has run is not edited, and a new
one that deleted them would also run on every development and test database.
So retiring them is a step somebody takes, once, on the database that is about
to become the real one.

WHAT IT DOES

  1. Retires the three sample products and their stock — only while each one is
     EXACTLY what the migration wrote, nothing points at it and the database has
     never been used.
  2. Leaves the pilot company with its approved categories. The ones that exist
     are kept as they are; the missing ones are created.

WHEN IT STOPS, AND CHANGES NOTHING AT ALL

  * DEBUG is on. It deletes rows; it runs where a shop is about to open and
    nowhere else, and a development database is not that place.
  * One or two of the three sample products are there. A new database has all
    three and a prepared one has none: anything in between is a database
    somebody has been in.
  * A sample product was edited, its stock moved, or something points at it.
  * The database has been used and there is still something to write. It
    prepares a launch; it does not keep a working shop's configuration in line.
    A category that shop removed stays removed.

It is safe to run again: once the sample catalogue is gone and the categories
are there, it has nothing to do, whatever the shop has sold since.

WHAT IT IS NOT

Not a default of the platform. Everything here belongs to ONE company, named by
its slug, frozen below like the pilot migrations do. Another company gets
nothing from it and loses nothing to it: `company_provisioning` is where a new
company's neutral defaults live, and it copies no catalogue.

Not a way to create an account. The first user of a new shop is made by hand:
`python manage.py createsuperuser`.
"""
from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from store.models import (
    BranchStock, CartItem, Category, Company, Customer, Device, FiscalDocument,
    InventoryCount, Order, PaymentTransaction, Product, RepairOrder, Review,
    SalesNote, StockMovement, StockTransfer, StockUnit, StorefrontCampaign,
)

PILOT_SLUG = 'black-dog-store'

# What migration 0002 wrote, and the quantity 0025 moved into the first branch.
SAMPLE_PRODUCTS = (
    {
        'slug': 'iphone-15-pro', 'name': 'iPhone 15 Pro', 'category': 'iphone',
        'description': 'El smartphone más avanzado de Apple.',
        'price': Decimal('5599.00'), 'quantity': 10,
    },
    {
        'slug': 'apple-watch-series-9', 'name': 'Apple Watch Series 9', 'category': 'accesorios',
        'description': 'Reloj inteligente con health tracking.',
        'price': Decimal('2499.00'), 'quantity': 15,
    },
    {
        'slug': 'airpods-pro-2', 'name': 'AirPods Pro (2.ª gen)', 'category': 'accesorios',
        'description': 'Cancelación de ruido activa y audio espacial.',
        'price': Decimal('999.00'), 'quantity': 20,
    },
)

# The pilot's categories, approved by its owner, in the order the shop shows them.
APPROVED_CATEGORIES = (
    ('iPhone', 'iphone'),
    ('Mac', 'mac'),
    ('iPad', 'ipad'),
    ('Apple Watch', 'apple-watch'),
    ('Accesorios', 'accesorios'),
)

# A row in any of these means somebody has already worked with this database.
ACTIVITY = (
    Order, PaymentTransaction, StockMovement, SalesNote, RepairOrder, Customer,
    Device, CartItem, Review, StockTransfer, InventoryCount, FiscalDocument, StockUnit,
)


def _edited(product, sample) -> list[str]:
    """Every way `product` differs from what the migration wrote."""
    expected = {
        'name': sample['name'], 'description': sample['description'], 'price': sample['price'],
        'inventory': sample['quantity'], 'image_url': '', 'is_active': True,
        'is_serialized': False, 'requires_imei': False,
    }
    found = [field for field, value in expected.items() if getattr(product, field) != value]
    category = product.category.slug if product.category_id else None
    if category != sample['category']:
        found.append('category')
    return found


def _stock_moved(product, sample, company) -> bool:
    levels = list(BranchStock.objects.filter(product=product).select_related('branch'))
    if len(levels) != 1:
        return True
    level = levels[0]
    return (
        level.branch.company_id != company.pk
        or level.quantity != sample['quantity']
        or any((level.minimum_stock, level.target_stock, level.safety_stock, level.lead_time_days))
    )


def _references(product) -> list[str]:
    """
    Whatever points at `product`, apart from its own stock level.

    Asked of the model itself, not of a list written here: a relation added
    tomorrow is counted the day it exists.
    """
    found = []
    for relation in Product._meta.related_objects:
        model = relation.related_model
        if model is BranchStock:
            continue
        count = model._base_manager.filter(**{relation.field.name: product}).count()
        if count:
            found.append(f'{model.__name__} ({count})')
    return found


class Command(BaseCommand):
    help = (
        'Prepara los datos de la tienda piloto en una base de producción nueva: retira '
        'el catálogo de ejemplo y deja sus categorías aprobadas. Sin --apply sólo informa.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply', action='store_true',
            help='Aplica los cambios. Sin esta opción no se escribe nada.',
        )

    @transaction.atomic
    def handle(self, *args, **options):
        if settings.DEBUG:
            raise CommandError(
                'Este comando retira datos y sólo se ejecuta con DEBUG=False, sobre la base '
                'que va a ser la de producción. Aquí DEBUG está activo: no se cambia nada.'
            )
        company = Company.objects.filter(slug=PILOT_SLUG).first()
        if company is None:
            raise CommandError(f'No existe la empresa «{PILOT_SLUG}»: no hay nada que preparar.')

        by_slug = {sample['slug']: sample for sample in SAMPLE_PRODUCTS}
        samples = list(
            Product.objects.select_for_update(of=('self',))
            .filter(company=company, slug__in=by_slug).select_related('category').order_by('pk')
        )

        missing, unordered = [], []
        for position, (name, slug) in enumerate(APPROVED_CATEGORIES, start=1):
            category = Category.objects.filter(company=company, slug=slug).first()
            if category is None:
                missing.append((position, name, slug))
            elif category.home_order == 0:
                # 0 is the column default: nobody has arranged the shop's menu yet.
                unordered.append((position, category))

        if not samples and not missing and not unordered:
            self.stdout.write('Nada que hacer: el catálogo de ejemplo no está y las categorías aprobadas sí.')
            self._report(company)
            return

        # From here on something would be written, so everything has to be as a
        # new database leaves it — checked before the first write, and in full.
        self._refuse_unless_new(company, samples, by_slug)

        apply = options['apply']
        self.stdout.write('' if apply else 'SIMULACIÓN: no se escribe nada. Con --apply se haría esto:')
        for product in samples:
            self.stdout.write(f'  retirar producto de ejemplo  {product.slug}  (y su stock de {by_slug[product.slug]["quantity"]} unidades)')
        for position, name, _slug in missing:
            self.stdout.write(f'  crear categoría              {name}  (posición {position})')
        for position, category in unordered:
            self.stdout.write(f'  ordenar categoría            {category.name}  (posición {position})')
        if not apply:
            return

        BranchStock.objects.filter(product__in=samples).delete()
        Product.objects.filter(pk__in=[product.pk for product in samples]).delete()
        for position, name, slug in missing:
            Category.objects.create(company=company, name=name, slug=slug, home_order=position)
        for position, category in unordered:
            category.home_order = position
            category.save(update_fields=['home_order'])
        self._report(company)

    def _refuse_unless_new(self, company, samples, by_slug):
        """Nothing is written unless EVERYTHING is as a new database leaves it."""
        problems = []
        if samples and len(samples) != len(SAMPLE_PRODUCTS):
            absent = sorted(set(by_slug) - {product.slug for product in samples})
            problems.append(
                f'quedan {len(samples)} de {len(SAMPLE_PRODUCTS)} productos de ejemplo (faltan: {", ".join(absent)}): '
                'una base nueva los tiene todos y una ya preparada, ninguno'
            )
        for product in samples:
            sample = by_slug[product.slug]
            edited = _edited(product, sample)
            if edited:
                problems.append(f'{product.slug}: alguien lo modificó ({", ".join(edited)})')
            if _stock_moved(product, sample, company):
                problems.append(f'{product.slug}: su stock ya no es el de ejemplo')
            references = _references(product)
            if references:
                problems.append(f'{product.slug}: tiene referencias: {", ".join(references)}')
        activity = [f'{model.__name__} ({count})' for model in ACTIVITY if (count := model._base_manager.count())]
        if activity:
            problems.append(f'la base ya tiene actividad: {", ".join(activity)}')
        if problems:
            raise CommandError(
                'No se cambia nada:\n  - '
                + '\n  - '.join(problems)
                + '\nRevísalo a mano: esta base no es una base nueva.'
            )

    def _report(self, company):
        categories = Category.objects.filter(company=company).order_by('home_order', 'name')
        self.stdout.write(
            f'{company.name}: Product={Product.objects.filter(company=company).count()}'
            f' | BranchStock={BranchStock.objects.filter(branch__company=company).count()}'
            f' | StockMovement={StockMovement.objects.filter(company=company).count()}'
            f' | categorías: {", ".join(categories.values_list("name", flat=True))}'
            f' | campañas publicadas: {StorefrontCampaign.objects.filter(company=company, status="published").count()}'
        )
