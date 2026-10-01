"""
The automatic promotion engine — Commercial Phase C1.3.

WHAT IT DOES
------------
Given a basket, works out which configured promotions it qualifies for, how many
times each applies, and how much comes off. It changes MONEY and nothing else:
the order still contains the real articles, and the same units still leave the
same shelves.

WHY IT LIVES HERE AND NOT IN THE BROWSER
----------------------------------------
The till has to show a total before charging it, so the temptation is to compute
the combo in React and let the server agree. That produces two implementations
of one rule, and the day they disagree is the day a customer is standing at the
counter watching the number change. There is one implementation; the POS asks it
through `/pos/preview/` and gets back what it will be charged.

OVERLAPPING PROMOTIONS: DETERMINISTIC, NOT OPTIMAL
--------------------------------------------------
Two promotions can want the same unit. Picking the combination that saves the
customer most is a set-packing problem — NP-hard, and worse, unstable: adding one
cable to a basket could reshuffle every discount on it, which is impossible for
a shopkeeper to explain to the person paying.

So the rule is simple and boring on purpose:

    order by priority DESC, then id ASC
    each promotion consumes the units it needs
    a unit consumed by one promotion is gone for the next

The result is always the same for the same basket, an admin controls the outcome
by setting priority, and the reason any given promotion did not apply is always
"something above it took the units".
"""

from __future__ import annotations

from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

from django.utils import timezone

from .models import Promotion

CENT = Decimal('0.01')


def _money(value) -> Decimal:
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


class PromotionAllocationError(ValueError):
    """
    Un snapshot de promoción que no se puede explicar componente a componente.

    Es un error de DOMINIO COMERCIAL, no fiscal: quien lo reciba decide cómo
    presentarlo. El generador de comprobantes lo convierte en `FiscalError`; el
    punto de venta, en un rechazo de la venta. Aquí sólo se afirma que los
    números no cierran y se dice cuáles.
    """


#: Las tres claves que describen un componente de una venta CONGELADA. La forma
#: del catálogo (`combo_availability`) usa `quantity`/`price`/`available` y NO
#: entra aquí: son cosas distintas y confundirlas explicaría una venta con
#: precios de hoy. Se exigen éstas y se falla cerrado ante cualquier otra.
_FROZEN_COMPONENT_KEYS = ('product_id', 'quantity_used', 'unit_price')


def allocate_component_discounts(regular_total, discount_total, components):
    """
    Reparte el descuento YA CALCULADO de una promoción entre sus componentes.

    NO DECIDE CUÁNTO SE DESCUENTA. `_bundle_amounts` ya lo decidió y el cliente
    ya lo pagó; esta función sólo EXPLICA qué parte de esa rebaja corresponde a
    cada artículo consumido. Cambiar el total aquí sería cambiar el precio
    después de cobrarlo.

    POR QUÉ HACE FALTA. Un combo descuenta «150,00 sobre 3 150,00» y eso basta
    para cobrar. No basta para declarar: un comprobante electrónico necesita el
    descuento atribuido a la línea que lo recibió, y una nota de crédito futura
    necesita saber qué se rebajó de qué. Sin una regla explícita cada consumidor
    inventaría la suya.

    LA REGLA, proporcional al valor regular consumido:

        component_regular_i = unit_price_i × quantity_used_i
        ideal_i            = discount_total × component_regular_i / regular_total
        floor_i            = ROUND_DOWN(ideal_i) a céntimos
        los céntimos que falten van a los mayores residuos, uno cada uno

    DESEMPATE ESTABLE POR `product_id` ASCENDENTE. No por el orden en que llegó
    la lista: un queryset sin `order_by` explícito puede devolver otro orden
    mañana, y entonces el mismo snapshot daría dos explicaciones distintas de la
    misma venta. Depende de que el producto sea único dentro de la promoción
    —lo garantiza `UniqueConstraint(promotion, product)`— y esta función lo
    vuelve a comprobar, porque un snapshot legacy es JSON y esa restricción no
    lo protegía.

    TODO CIERRA AL CÉNTIMO O NO SE DEVUELVE NADA. No hay tolerancia de 0,01: el
    algoritmo existe precisamente para cerrar exacto, así que una diferencia no
    es redondeo, es un snapshot corrupto.

    Devuelve una lista nueva con cada componente más `regular_amount` y
    `discount_amount`. No muta la entrada. Sin ORM, sin fiscal, sin SUNAT.
    """
    rows = list(components or ())
    if not rows:
        raise PromotionAllocationError(
            'Una promoción sin componentes no se puede explicar: no hay a qué '
            'atribuir su descuento.'
        )

    regular_total = _money(regular_total)
    discount_total = _money(discount_total)
    if regular_total < 0:
        raise PromotionAllocationError(
            f'El valor regular de la promoción es negativo ({regular_total}).')
    if discount_total < 0:
        raise PromotionAllocationError(
            f'El descuento de la promoción es negativo ({discount_total}).')
    if discount_total > regular_total:
        raise PromotionAllocationError(
            f'El descuento ({discount_total}) supera el valor regular '
            f'({regular_total}): no se puede rebajar más de lo que costaba.')

    parsed = []
    vistos = set()
    for raw in rows:
        if not isinstance(raw, dict):
            raise PromotionAllocationError(
                'Componente con forma desconocida: se esperaba un mapa con '
                f'{", ".join(_FROZEN_COMPONENT_KEYS)}.')
        faltan = [k for k in _FROZEN_COMPONENT_KEYS if k not in raw]
        if faltan:
            raise PromotionAllocationError(
                f'Componente sin {", ".join(faltan)}. Un componente de venta '
                f'congelada lleva {", ".join(_FROZEN_COMPONENT_KEYS)}; la forma '
                f'del catálogo de combos no sirve para explicar una venta.')

        product_id = raw['product_id']
        if isinstance(product_id, bool) or not isinstance(product_id, int):
            raise PromotionAllocationError(
                f'`product_id` no es un entero: {product_id!r}.')
        if product_id in vistos:
            raise PromotionAllocationError(
                f'El producto {product_id} aparece dos veces en el mismo '
                f'snapshot. El desempate del reparto exige que sea único.')
        vistos.add(product_id)

        quantity = raw['quantity_used']
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
            raise PromotionAllocationError(
                f'`quantity_used` del producto {product_id} debe ser un entero '
                f'mayor que cero; llegó {quantity!r}.')

        try:
            unit_price = Decimal(str(raw['unit_price']))
        except (ArithmeticError, TypeError, ValueError):
            raise PromotionAllocationError(
                f'`unit_price` del producto {product_id} no es un importe: '
                f'{raw["unit_price"]!r}.') from None
        if unit_price < 0:
            raise PromotionAllocationError(
                f'`unit_price` del producto {product_id} es negativo.')

        parsed.append((product_id, quantity, _money(unit_price * quantity), raw))

    suma_regular = sum((p[2] for p in parsed), Decimal('0.00'))
    if suma_regular != regular_total:
        raise PromotionAllocationError(
            f'Los componentes suman {suma_regular} de valor regular y la '
            f'promoción declara {regular_total}. El snapshot es incoherente y '
            f'no se corrige aquí.')

    if regular_total == 0:
        # Todo el conjunto vale cero. Es coherente sólo si tampoco se descontó
        # nada; repartir un descuento sobre una base nula no tiene solución.
        if discount_total > 0:
            raise PromotionAllocationError(
                f'La promoción descuenta {discount_total} sobre un valor '
                f'regular de cero: no hay base sobre la que repartirlo.')
        return [
            {**raw, 'regular_amount': regular, 'discount_amount': Decimal('0.00')}
            for _pid, _qty, regular, raw in parsed
        ]

    ideales = [discount_total * regular / regular_total for _p, _q, regular, _r in parsed]
    pisos = [i.quantize(CENT, rounding=ROUND_DOWN) for i in ideales]
    residuos = [ideales[k] - pisos[k] for k in range(len(parsed))]

    faltante = discount_total - sum(pisos, Decimal('0.00'))
    centimos = int((faltante / CENT).to_integral_value(rounding=ROUND_HALF_UP))
    if centimos < 0 or centimos > len(parsed):
        raise PromotionAllocationError(
            f'El reparto del descuento no cierra: faltan {faltante} sobre '
            f'{len(parsed)} componente(s), fuera de la cota 0..{len(parsed)}.')

    # Mayor residuo primero; a igualdad, `product_id` ascendente.
    orden = sorted(range(len(parsed)), key=lambda k: (-residuos[k], parsed[k][0]))
    con_centimo = set(orden[:centimos])

    salida = []
    for k, (product_id, _qty, regular, raw) in enumerate(parsed):
        descuento = pisos[k] + (CENT if k in con_centimo else Decimal('0.00'))
        if descuento < 0 or descuento > regular:
            raise PromotionAllocationError(
                f'El descuento atribuido al producto {product_id} '
                f'({descuento}) no cabe en su valor regular ({regular}).')
        salida.append({**raw, 'regular_amount': regular,
                       'discount_amount': descuento})

    suma_descuento = sum((c['discount_amount'] for c in salida), Decimal('0.00'))
    if suma_descuento != discount_total:
        raise PromotionAllocationError(
            f'El reparto suma {suma_descuento} y la promoción descuenta '
            f'{discount_total}.')
    return salida


def live_promotions(company, branch=None, *, at=None):
    """
    The promotions in force here, right now, in evaluation order.

    Filtered in Python for the window and branch rather than in SQL: the
    conditions involve a nullable window and a scope enum whose SELECTED case
    needs a join, and the set per company is small. Clarity wins over a query
    that would have to be read twice.
    """
    at = at or timezone.now()
    rows = (
        Promotion.objects
        .filter(company=company, is_active=True)
        .prefetch_related('items__product', 'branches')
        .order_by('-priority', 'pk')
    )
    return [p for p in rows if p.is_live(at) and p.applies_to_branch(branch)]


def _applications_possible(promotion, available: dict[int, int]) -> int:
    """
    How many complete sets of this combo the remaining units can form.

    Bounded by the scarcest component: a combo needing two cables and one
    charger, against four cables and one charger, applies once — the second
    cable pair has no charger to go with it.
    """
    items = list(promotion.items.all())
    if not items:
        return 0
    possible = None
    for item in items:
        have = available.get(item.product_id, 0)
        if have < item.quantity:
            return 0
        count = have // item.quantity
        possible = count if possible is None else min(possible, count)
    return possible or 0


def _bundle_amounts(promotion, prices: dict[int, Decimal], applications: int):
    """
    What one set costs normally, and what this promotion charges instead.

    Returns `(regular, discount)` for ALL applications together.
    """
    regular_per_set = Decimal('0.00')
    for item in promotion.items.all():
        regular_per_set += prices[item.product_id] * item.quantity
    regular_per_set = _money(regular_per_set)

    if promotion.promotion_type == Promotion.BUNDLE_FIXED_PRICE:
        combo_price = _money(promotion.fixed_price or Decimal('0.00'))
        # A "combo" priced above the parts is not a discount. It is almost
        # certainly a configuration mistake, and charging MORE for buying
        # together would be indefensible — so it simply does not fire.
        discount_per_set = max(regular_per_set - combo_price, Decimal('0.00'))
    else:
        percent = Decimal(promotion.discount_percent or 0)
        discount_per_set = _money(regular_per_set * percent / Decimal('100'))

    return (
        _money(regular_per_set * applications),
        _money(discount_per_set * applications),
    )


def evaluate(company, branch, items, prices, *, at=None):
    """
    Apply every qualifying promotion to this basket.

    `items`  : normalised `[{'product': id, 'quantity': n}, ...]`
    `prices` : `{product_id: Decimal}` — the SERVER's prices, never the client's

    Returns:
        {
          'applied':  [ {promotion, applications, regular_amount,
                         discount_amount, components}, ... ],
          'discount': Decimal,   total across all of them
        }

    Units are consumed as promotions are applied, so no unit is ever discounted
    twice and a leftover article is charged at its normal price — the second
    cable in a basket of two, when the combo only needed one, costs what a cable
    costs.
    """
    available = {i['product']: i['quantity'] for i in items}
    applied = []
    total_discount = Decimal('0.00')

    for promotion in live_promotions(company, branch, at=at):
        possible = _applications_possible(promotion, available)
        if possible <= 0:
            continue
        if promotion.max_applications_per_order:
            possible = min(possible, promotion.max_applications_per_order)
        if possible <= 0:
            continue

        regular, discount = _bundle_amounts(promotion, prices, possible)
        if discount <= 0:
            # Nothing to give away — a misconfigured combo, or a percentage that
            # rounds to zero on a very cheap set. Consuming units for a discount
            # of nothing would block a promotion below it for no benefit.
            continue

        components = []
        for item in promotion.items.all():
            used = item.quantity * possible
            available[item.product_id] = available.get(item.product_id, 0) - used
            components.append({
                'product_id': item.product_id,
                'product_name': item.product.name,
                'quantity_per_application': item.quantity,
                'quantity_used': used,
                'unit_price': str(prices[item.product_id]),
            })

        # QUÉ PARTE DE LA REBAJA LE TOCA A CADA ARTÍCULO.
        #
        # El descuento ya está decidido —`_bundle_amounts` lo calculó y es el que
        # se cobra—; esto sólo lo explica componente a componente para que la
        # venta se pueda declarar y auditar línea por línea. No cambia el total,
        # ni las aplicaciones, ni qué promoción gana.
        components = allocate_component_discounts(regular, discount, components)

        applied.append({
            'promotion': promotion,
            'applications': possible,
            'regular_amount': regular,
            'discount_amount': discount,
            'components': components,
        })
        total_discount += discount

    return {'applied': applied, 'discount': _money(total_discount)}


def combo_availability(company, branch, *, at=None, limit=20):
    """
    Combos this branch could actually sell right now, for the POS shortcut.

    Each carries how many complete sets the CURRENT stock allows, because
    offering a one-tap combo that then fails at the till for want of a screen
    protector is worse than not offering it. A partial bundle is never sold.
    """
    from . import inventory_services

    out = []
    for promotion in live_promotions(company, branch, at=at)[:limit]:
        items = list(promotion.items.all())
        if not items:
            continue
        possible = None
        components = []
        for item in items:
            on_hand = inventory_services.branch_quantity(branch, item.product)
            sets = on_hand // item.quantity if item.quantity else 0
            possible = sets if possible is None else min(possible, sets)
            components.append({
                'product_id': item.product_id,
                'product_name': item.product.name,
                'quantity': item.quantity,
                'available': on_hand,
                'price': str(item.product.price),
            })
        regular = _money(sum(
            (Decimal(str(i.product.price)) * i.quantity for i in items),
            Decimal('0.00'),
        ))
        prices = {i.product_id: Decimal(str(i.product.price)) for i in items}
        _reg, discount = _bundle_amounts(promotion, prices, 1)
        out.append({
            'id': promotion.pk,
            'name': promotion.name,
            'promotion_type': promotion.promotion_type,
            'components': components,
            'regular_amount': str(regular),
            'discount_amount': str(discount),
            'combo_amount': str(_money(regular - discount)),
            'available_sets': possible or 0,
        })
    return out


#: Las dos claves que añade la atribución. Su presencia distingue un snapshot
#: escrito desde que existe el reparto de uno anterior.
_ATTRIBUTION_KEYS = ('regular_amount', 'discount_amount')


def frozen_component_discounts(regular_amount, discount_amount, components):
    """
    La atribución de una promoción YA VENDIDA.

    EL SNAPSHOT CONGELADO ES LA AUTORIDAD HISTÓRICA. Si los componentes ya traen
    su atribución, se VALIDA y se devuelve **tal cual**: no se recalcula. El día
    que la regla de reparto cambie, una venta de hoy tiene que seguir
    explicándose con la regla de hoy, y eso sólo se consigue leyendo lo que se
    guardó. Recalcular y comparar no protege la historia: la reinterpreta con la
    regla vigente y sólo avisa cuando ya difieren.

    UN SNAPSHOT ANTERIOR a que existiera la atribución sólo conserva
    `quantity_used` y `unit_price`. Ése se reconstruye EN MEMORIA con la regla
    actual, porque no hay nada guardado que respetar. No se escribe nada: la fila
    histórica sigue diciendo exactamente lo que dijo.

    MEZCLAR LOS DOS FORMATOS EN UN MISMO SNAPSHOT ES CORRUPCIÓN, no una
    migración a medias que haya que adivinar. Si un componente trae atribución y
    otro no, no hay forma de saber cuál manda, y se falla cerrado.

    NUNCA consulta la `Promotion` viva ni su precio de hoy: todo sale de los
    argumentos.
    """
    rows = list(components or ())
    if not rows:
        raise PromotionAllocationError(
            'Una promoción sin componentes no se puede explicar: no hay a qué '
            'atribuir su descuento.')
    for raw in rows:
        if not isinstance(raw, dict):
            raise PromotionAllocationError(
                'Componente con forma desconocida: se esperaba un mapa con '
                f'{", ".join(_FROZEN_COMPONENT_KEYS)}.')

    con_atribucion = [all(k in raw for k in _ATTRIBUTION_KEYS) for raw in rows]
    if not any(con_atribucion):
        # Snapshot anterior a la atribución: se reconstruye con la regla vigente.
        return allocate_component_discounts(regular_amount, discount_amount, rows)
    if not all(con_atribucion):
        sin_ella = [raw.get('product_id') for raw, tiene in zip(rows, con_atribucion)
                    if not tiene]
        raise PromotionAllocationError(
            f'El snapshot está congelado a medias: los componentes {sin_ella} no '
            f'traen atribución y otros sí. No se adivina cuál manda.')

    # --- snapshot congelado: se valida, no se recalcula ---------------------
    regular_total = _money(regular_amount)
    discount_total = _money(discount_amount)
    salida, vistos = [], set()
    for raw in rows:
        faltan = [k for k in _FROZEN_COMPONENT_KEYS if k not in raw]
        if faltan:
            raise PromotionAllocationError(
                f'Componente congelado sin {", ".join(faltan)}.')

        product_id = raw['product_id']
        if isinstance(product_id, bool) or not isinstance(product_id, int):
            raise PromotionAllocationError(
                f'`product_id` no es un entero: {product_id!r}.')
        if product_id in vistos:
            raise PromotionAllocationError(
                f'El producto {product_id} aparece dos veces en el mismo '
                f'snapshot.')
        vistos.add(product_id)

        quantity = raw['quantity_used']
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
            raise PromotionAllocationError(
                f'`quantity_used` del producto {product_id} debe ser un entero '
                f'mayor que cero; llegó {quantity!r}.')

        try:
            unit_price = Decimal(str(raw['unit_price']))
            regular = _money(Decimal(str(raw['regular_amount'])))
            descuento = _money(Decimal(str(raw['discount_amount'])))
        except (ArithmeticError, TypeError, ValueError):
            raise PromotionAllocationError(
                f'El componente {product_id} tiene importes ilegibles en el '
                f'snapshot.') from None

        if unit_price < 0:
            raise PromotionAllocationError(
                f'`unit_price` del producto {product_id} es negativo.')
        if regular < 0 or descuento < 0:
            raise PromotionAllocationError(
                f'El componente {product_id} declara importes negativos.')
        if descuento > regular:
            raise PromotionAllocationError(
                f'El componente {product_id} descuenta {descuento} sobre un '
                f'valor regular de {regular}.')
        # El valor regular congelado tiene que seguir siendo el producto de sus
        # propios factores: si no, alguien editó una cifra y no la otra.
        esperado = _money(unit_price * quantity)
        if regular != esperado:
            raise PromotionAllocationError(
                f'El componente {product_id} declara un valor regular de '
                f'{regular}, pero {quantity} × {unit_price} = {esperado}.')

        salida.append({**raw, 'regular_amount': regular,
                       'discount_amount': descuento})

    suma_regular = sum((c['regular_amount'] for c in salida), Decimal('0.00'))
    if suma_regular != regular_total:
        raise PromotionAllocationError(
            f'Los componentes congelados suman {suma_regular} de valor regular '
            f'y la promoción declara {regular_total}.')
    suma_descuento = sum((c['discount_amount'] for c in salida), Decimal('0.00'))
    if suma_descuento != discount_total:
        raise PromotionAllocationError(
            f'Los componentes congelados suman {suma_descuento} de descuento y '
            f'la promoción declara {discount_total}.')
    return salida


def _jsonable_components(components):
    """
    Los componentes, listos para un `JSONField`.

    `Decimal` NO es serializable a JSON —`bulk_create` falla con
    `TypeError`— y convertirlo a `float` perdería céntimos, que es justo lo que
    este reparto existe para no perder. Se guardan como TEXTO, igual que
    `unit_price` ya se guardaba, y quien los lee los devuelve a `Decimal` con
    `Decimal(str(...))`.

    La conversión vive aquí, en el borde de la persistencia, y no en el reparto:
    `evaluate()` sigue devolviendo `Decimal` para quien lo consume en memoria
    —la previsualización del mostrador, la frontera fiscal— y sólo el JSON que
    se archiva lleva texto.
    """
    return [
        {clave: (str(valor) if isinstance(valor, Decimal) else valor)
         for clave, valor in componente.items()}
        for componente in components
    ]


def freeze(order, evaluation):
    """
    Write the snapshot rows for a completed sale.

    Called inside the sale's transaction. What it stores is deliberately
    redundant with the `Promotion` rows — the name, the type, the amounts and
    the components — because the promotion will be edited, renamed and
    eventually switched off, and this receipt must keep saying what this
    customer was charged and why.
    """
    from .models import AppliedPromotion

    rows = []
    for entry in evaluation['applied']:
        promotion = entry['promotion']
        rows.append(AppliedPromotion(
            company_id=order.company_id,
            order=order,
            promotion=promotion,
            promotion_name_snapshot=promotion.name,
            promotion_type_snapshot=promotion.promotion_type,
            applications=entry['applications'],
            regular_amount=entry['regular_amount'],
            discount_amount=entry['discount_amount'],
            metadata={'components': _jsonable_components(entry['components'])},
        ))
    if rows:
        # bulk_create skips clean(); assert the tenant invariant over the set.
        AppliedPromotion.assert_all_match_company(rows)
        AppliedPromotion.objects.bulk_create(rows)
    return rows
