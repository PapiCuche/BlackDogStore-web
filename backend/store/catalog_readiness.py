"""
CATALOG-READINESS-01 — «Preparación del catálogo»: what a company still lacks before it can register its whole
inventory (new devices, used devices, accessories) with the supplier-list assistant and «Recibir mercadería».

READ-ONLY. It writes nothing, audits nothing and never fixes anything: each check names what is missing and
the admin screen where a person fixes it. The company is the caller's, resolved by the view from the server
context; nothing of another company is read.

THREE QUERIES, whatever the size of the catalogue:
  1. the company's attributes (name, role);
  2. its models, each with «has a palette» and «has a published variant» as EXISTS subqueries;
  3. one row of scalar counts (active grades, active branches, active categories, accessories, aliases).

No price, no cost, no stock is read: a check only needs names, flags and counts.

Each check: `key`, `status` (ok / warning / missing), `kind` (required / recommended / optional / info), a
short Spanish `title` and `detail`, `counts`, up to five `samples` ({id, name, path}) and an `action`
(an internal admin path) with its `action_label`.
"""
from __future__ import annotations

from django.db.models import Count, Exists, IntegerField, OuterRef, Subquery, Value
from django.db.models.functions import Coalesce

from .device_catalog import normalize_model_name
from .models import (
    Branch, CatalogAlias, Category, Company, CosmeticGrade, ModelOption, Product, ProductModel, VariantAttribute,
)

OK, WARNING, MISSING = 'ok', 'warning', 'missing'
REQUIRED, RECOMMENDED, OPTIONAL, INFO = 'required', 'recommended', 'optional', 'info'
MAX_SAMPLES = 5

MODELS_PATH = '/admin/products/models'
ATTRIBUTES_PATH = f'{MODELS_PATH}#atributos'
GRADES_PATH = f'{MODELS_PATH}#escala'
VOCABULARY_PATH = f'{MODELS_PATH}#vocabulario'
BRANCHES_PATH = '/admin/branches'
CATEGORIES_PATH = '/admin/products/categories'
NEW_PRODUCT_PATH = '/admin/products/new'

#: The role a person most likely MEANT when naming an attribute, by the folded words of its name. Used only to
#: say «"Capacidad" no tiene el rol Capacidad»; it never assigns a role.
_ROLE_WORDS = {
    VariantAttribute.ROLE_CAPACITY: {'capacidad', 'capacidades', 'almacenamiento', 'memoria', 'storage', 'capacity'},
    VariantAttribute.ROLE_COLOR: {'color', 'colores', 'colour', 'tono', 'tonos'},
    VariantAttribute.ROLE_SIM: {'sim', 'esim', 'chip'},
    VariantAttribute.ROLE_REGION: {'region', 'regiones', 'pais', 'mercado'},
}
_ROLE_LABELS = dict(VariantAttribute.ROLE_CHOICES)


def _count(queryset) -> Coalesce:
    """A scalar subquery counting `queryset` (already filtered by `company=OuterRef('pk')`)."""
    counted = queryset.order_by().values('company').annotate(n=Count('pk')).values('n')
    return Coalesce(Subquery(counted, output_field=IntegerField()), Value(0))


def _plural(n, singular, plural) -> str:
    return f'{n} {singular if n == 1 else plural}'


def _check(key, status, kind, title, detail, action, action_label, counts, samples=()):
    return {
        'key': key, 'status': status, 'kind': kind, 'title': title, 'detail': detail,
        'action': action, 'action_label': action_label, 'counts': counts, 'samples': list(samples),
    }


def _names_like(attributes, role) -> list[str]:
    words = _ROLE_WORDS[role]
    return [
        name for name, attr_role in attributes
        if attr_role == VariantAttribute.ROLE_OTHER and words & set(normalize_model_name(name, limit=None).split())
    ]


def _role_check(attributes, role, *, required):
    label = _ROLE_LABELS[role]
    holder = next((name for name, attr_role in attributes if attr_role == role), None)
    similar = _names_like(attributes, role)
    counts = {'with_role': 1 if holder else 0, 'named_like': len(similar)}
    title = f'Rol {label}' if required else f'Rol {label} (opcional)'
    if holder:
        return _check(f'role_{role}', OK, REQUIRED if required else OPTIONAL, title,
                      f'«{holder}» tiene el rol {label}.', ATTRIBUTES_PATH, 'Ver atributos', counts)
    if similar:
        named = ', '.join(f'«{name}»' for name in similar)
        detail = (f'{named} no tiene el rol {label}. Cámbialo en «Atributos y opciones» para que el asistente '
                  f'de proveedores lo reconozca.')
    else:
        detail = (f'Ningún atributo tiene el rol {label}. Crea uno o asígnale el rol a uno existente en '
                  f'«Atributos y opciones».')
    if not required:
        what = 'SIM' if role == VariantAttribute.ROLE_SIM else 'región'
        detail = f'Sólo hace falta si vendes variantes por SIM/región. {detail}'
        return _check(f'role_{role}', WARNING, OPTIONAL, title, detail, ATTRIBUTES_PATH,
                      f'Revisar atributos ({what})', counts)
    return _check(f'role_{role}', MISSING, REQUIRED, title, detail, ATTRIBUTES_PATH, 'Revisar atributos', counts)


def _sample(row, anchor):
    return {'id': row['pk'], 'name': row['name'], 'path': f'{MODELS_PATH}/{row["pk"]}#{anchor}'}


def _palette_check(models):
    without = [row for row in models if not row['has_palette']]
    samples = [_sample(row, 'paleta') for row in without[:MAX_SAMPLES]]
    counts = {'without_palette': len(without), 'models': len(models)}
    title = 'Paleta de cada modelo'
    if not models:
        return _check('palette', MISSING, REQUIRED, title,
                      'Todavía no hay modelos. Crea los modelos de tus equipos y, en cada uno, elige su paleta '
                      '(capacidades, colores…).', MODELS_PATH, 'Crear modelos', counts)
    if not without:
        detail = 'El modelo tiene paleta.' if len(models) == 1 else f'Los {len(models)} modelos tienen paleta.'
        return _check('palette', OK, REQUIRED, title, detail, MODELS_PATH, 'Ver modelos', counts)
    status = MISSING if len(without) == len(models) else WARNING
    detail = (f'{len(without)} de {len(models)} modelos no tienen paleta: sin ella el asistente de proveedores '
              f'no puede validar capacidad ni color.')
    return _check('palette', status, REQUIRED, title, detail, samples[0]['path'], 'Elegir paleta', counts, samples)


def _published_check(models):
    published = [row for row in models if row['is_published_online']]
    empty = [row for row in published if not row['has_published_variant']]
    samples = [_sample(row, 'variantes') for row in empty[:MAX_SAMPLES]]
    counts = {'published_without_variant': len(empty), 'published_models': len(published)}
    title = 'Modelos publicados con variantes'
    if not empty:
        return _check('published_without_variant', OK, RECOMMENDED, title,
                      'Cada modelo publicado tiene al menos una variante publicada.', MODELS_PATH, 'Ver modelos',
                      counts)
    if len(empty) == 1:
        detail = '1 modelo publicado no tiene ninguna variante publicada: la tienda lo muestra sin nada que comprar.'
    else:
        detail = (f'{len(empty)} modelos publicados no tienen ninguna variante publicada: la tienda los muestra '
                  'sin nada que comprar.')
    return _check('published_without_variant', WARNING, RECOMMENDED, title, detail, samples[0]['path'],
                  'Revisar variantes', counts, samples)


def _grades_check(active):
    counts = {'active': active}
    title = 'Escala de estado (seminuevos)'
    if active:
        what = _plural(active, 'grado activo', 'grados activos')
        return _check('grades', OK, REQUIRED, title, f'{what} para describir equipos seminuevos.', GRADES_PATH,
                      'Ver escala', counts)
    return _check('grades', MISSING, REQUIRED, title,
                  'No hay grados activos: hacen falta para recibir equipos seminuevos. Usa «Crear escala sugerida» '
                  'o agrega los tuyos.', GRADES_PATH, 'Configurar escala', counts)


def _branches_check(active):
    counts = {'active': active}
    title = 'Sucursal activa'
    if active:
        what = _plural(active, 'sucursal activa', 'sucursales activas')
        return _check('branches', OK, REQUIRED, title, f'{what} para recibir mercadería.', BRANCHES_PATH,
                      'Ver sucursales', counts)
    return _check('branches', MISSING, REQUIRED, title,
                  'No hay ninguna sucursal activa: la mercadería se recibe en una sucursal.', BRANCHES_PATH,
                  'Configurar sucursales', counts)


def _categories_check(active):
    counts = {'active': active}
    title = 'Categorías'
    if active:
        what = _plural(active, 'categoría activa', 'categorías activas')
        return _check('categories', OK, REQUIRED, title, f'{what}.', CATEGORIES_PATH, 'Ver categorías', counts)
    return _check('categories', MISSING, REQUIRED, title,
                  'No hay categorías activas: crea al menos una para ordenar equipos y accesorios.',
                  CATEGORIES_PATH, 'Crear categorías', counts)


def _accessories_check(count):
    counts = {'accessories': count}
    title = 'Accesorios (opcional)'
    if count:
        what = _plural(count, 'producto sin modelo', 'productos sin modelo')
        return _check('accessories', OK, OPTIONAL, title, f'{what} (accesorios y otros).', NEW_PRODUCT_PATH,
                      'Nuevo producto', counts)
    return _check('accessories', WARNING, OPTIONAL, title,
                  'Todavía no hay accesorios (productos sin modelo). Sólo hace falta si los vendes.',
                  NEW_PRODUCT_PATH, 'Crear producto', counts)


def _aliases_check(count):
    counts = {'aliases': count}
    if count:
        detail = f'{_plural(count, "alias de proveedor guardado", "alias de proveedor guardados")}.'
    else:
        detail = 'Aún no hay alias. Agrégalos cuando un proveedor escriba un modelo o una opción de otra forma.'
    return _check('aliases', OK, INFO, 'Vocabulario de proveedores', detail, VOCABULARY_PATH,
                  'Ver vocabulario', counts)


def readiness(company) -> dict:
    """The checklist of `company`. Three queries; never writes."""
    attributes = list(
        VariantAttribute.objects.filter(company=company).order_by('sort_order', 'name', 'pk')
        .values_list('name', 'role')
    )

    published_variant = (
        Product.objects.filter(product_model=OuterRef('pk'), is_active=True, is_published_online=True)
        .exclude(Product.pending_draft_q())
    )
    models = list(
        ProductModel.objects.filter(company=company)
        .annotate(has_palette=Exists(ModelOption.objects.filter(model=OuterRef('pk'))),
                  has_published_variant=Exists(published_variant))
        .order_by('sort_order', 'name', 'pk')
        .values('pk', 'name', 'is_published_online', 'has_palette', 'has_published_variant')
    )

    counts = Company.objects.filter(pk=company.pk).values(
        n_grades=_count(CosmeticGrade.objects.filter(company=OuterRef('pk'), is_active=True)),
        n_branches=_count(Branch.objects.filter(company=OuterRef('pk'), is_active=True)),
        n_categories=_count(Category.objects.filter(company=OuterRef('pk'), is_active=True)),
        n_accessories=_count(Product.objects.filter(company=OuterRef('pk'), product_model__isnull=True,
                                                  is_active=True)),
        n_aliases=_count(CatalogAlias.objects.filter(company=OuterRef('pk'))),
    ).get()

    checks = [
        _role_check(attributes, VariantAttribute.ROLE_CAPACITY, required=True),
        _role_check(attributes, VariantAttribute.ROLE_COLOR, required=True),
        _palette_check(models),
        _published_check(models),
        _grades_check(counts['n_grades']),
        _branches_check(counts['n_branches']),
        _categories_check(counts['n_categories']),
        _role_check(attributes, VariantAttribute.ROLE_SIM, required=False),
        _role_check(attributes, VariantAttribute.ROLE_REGION, required=False),
        _accessories_check(counts['n_accessories']),
        _aliases_check(counts['n_aliases']),
    ]
    summary = {status: sum(1 for c in checks if c['status'] == status) for status in (OK, WARNING, MISSING)}
    return {'checks': checks, 'summary': summary, 'ready': summary[MISSING] == 0}
