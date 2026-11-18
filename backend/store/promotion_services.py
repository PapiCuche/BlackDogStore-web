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

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
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
                # SERIAL-PICK-01: a part sold device by device still has to be
                # identified; the till reads it here to ask for it.
                'is_serialized': item.product.is_serialized,
                'requires_imei': item.product.requires_imei,
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


# ---------------------------------------------------------------------------
# DISCOUNT-SHARE-01 — the promotion discount of a SOLD order, line by line.
# ---------------------------------------------------------------------------

class PromotionLineShareError(ValueError):
    """
    The frozen promotions of a sale do not describe its lines.

    A COMMERCIAL domain error, like `PromotionAllocationError`: whoever calls
    `share_promotion_discount` decides how to present it. The fiscal generator
    turns it into a `FiscalError` with the same words.
    """


@dataclass(frozen=True)
class SoldLine:
    """
    One line of a sold order, as `share_promotion_discount` reads it.

    `unit_price` is the price the line was SOLD at, with tax — `OrderItem.price`.
    `product_company_id` is None for a line whose product is gone.
    """

    product_id: int | None
    product_company_id: int | None
    quantity: int
    unit_price: Decimal

    @classmethod
    def from_item(cls, item) -> 'SoldLine':
        """Adapter for an `OrderItem` (its `product` already loaded)."""
        product = item.product
        return cls(
            product_id=item.product_id,
            product_company_id=None if product is None else product.company_id,
            quantity=item.quantity,
            unit_price=Decimal(str(item.price)),
        )


@dataclass(frozen=True)
class FrozenPromotion:
    """
    One `AppliedPromotion` snapshot of the sale, as plain data.

    `company_id` is the company of the PROMOTION (not of the snapshot row): a
    promotion of another tenant cannot explain this sale's discount.
    `components` is the frozen `metadata['components']` list, untouched.
    """

    name: str
    company_id: int
    regular_amount: Decimal
    discount_amount: Decimal
    components: Sequence[Mapping] | None

    @classmethod
    def from_applied(cls, row) -> 'FrozenPromotion':
        """Adapter for an `AppliedPromotion` (its `promotion` already loaded)."""
        return cls(
            name=row.promotion_name_snapshot,
            company_id=row.promotion.company_id,
            regular_amount=row.regular_amount,
            discount_amount=row.discount_amount,
            components=(row.metadata or {}).get('components'),
        )


@dataclass(frozen=True)
class PromotionLineDiscount:
    """
    How much the sale's promotions took off ONE line, gross (tax included).

    `regular_unit_price` is the unit price the line was sold at, which for a
    promoted line is also the price the promotion froze (it is checked);
    `regular_amount` = money(regular_unit_price × quantity), the line before
    the promotion; `discount_amount` is 0.00 for a line no promotion touched;
    `promotion_names` lists, once each and in the order applied, the
    promotions that took something off this line.
    """

    product_id: int | None
    quantity: int
    regular_unit_price: Decimal
    regular_amount: Decimal
    discount_amount: Decimal
    promotion_names: tuple[str, ...] = field(default=())

    @property
    def amount_after_discount(self) -> Decimal:
        return self.regular_amount - self.discount_amount

    @property
    def discount_reason(self) -> str:
        return ', '.join(self.promotion_names)

    def for_sales_note(self) -> dict:
        """
        What the internal sales note will print for this line (not used yet).

        The same figures the fiscal document declares per line, so the two
        papers of one sale never disagree about a promotion.
        """
        return {
            'regular_unit_price': self.regular_unit_price,
            'discount_amount': self.discount_amount,
            'discount_reason': self.discount_reason,
        }


def share_promotion_discount(
    *, company_id: int, order_discount: Decimal,
    lines: Sequence[SoldLine], applied: Iterable[FrozenPromotion],
) -> tuple[PromotionLineDiscount, ...]:
    """
    How much the automatic promotions of a SOLD order took off each line.

    PURE: no ORM, no clock, no settings. Everything comes from the arguments,
    which are snapshots of the sale (`SoldLine.from_item`,
    `FrozenPromotion.from_applied`, or `order_promotion_line_discounts` to load
    them). Never the live `Promotion`: the March promotion was edited in April.

    CONTRACT
      in:  the order's company and its declared `discount_amount`; its lines in
           their declared order; its applied promotions in their applied order.
      out: one `PromotionLineDiscount` per line, in the SAME order, each with
           the regular unit price, the line's regular amount and the GROSS
           promotion discount (Decimal, 2 dp, ROUND_HALF_UP, as `tax_services`).
           Σ discount_amount == money(order_discount), exactly.

    THE SHARE is the one `frozen_component_discounts` explains for each
    promotion (the frozen attribution, or, for a snapshot older than it, the
    same rule rebuilt in memory), added up per product.

    THE IDS OF THE JSON ARE NOT TRUSTED. Every attributed product has to be one
    of the sale's lines, belong to the sale's company, not consume more units
    than were sold and have been frozen at the same price the line sold at.
    Anything else is a snapshot that does not describe this sale, and it fails
    closed with `PromotionLineShareError`, correcting nothing.

    This is the computation the fiscal document declares line by line
    (Catálogo N.º 53, code `00`), extracted from `fiscal_services` unchanged.
    """
    applied = list(applied)
    if not applied:
        raise PromotionLineShareError(
            'La venta declara un descuento por promoción automática y no conserva '
            'ninguna promoción aplicada. Sin ese snapshot no hay a qué atribuir '
            'la rebaja.'
        )
    declared = sum((row.discount_amount for row in applied), Decimal('0.00'))
    if declared != _money(order_discount):
        raise PromotionLineShareError(
            f'Las promociones aplicadas suman {declared} de descuento y la venta '
            f'declara {_money(order_discount)}.'
        )

    by_product: dict[int, Decimal] = {}
    units: dict[int, int] = {}
    unit_prices: dict[int, set] = {}
    names: dict[int, list[str]] = {}
    for row in applied:
        if row.company_id != company_id:
            raise PromotionLineShareError(
                f'La promoción «{row.name}» no pertenece a la empresa de la venta.'
            )
        try:
            components = frozen_component_discounts(
                row.regular_amount, row.discount_amount, row.components,
            )
        except PromotionAllocationError as exc:
            raise PromotionLineShareError(
                f'La promoción «{row.name}» de esta venta no se puede explicar '
                f'componente a componente: {exc}'
            ) from exc
        for component in components:
            pid = component['product_id']
            by_product[pid] = by_product.get(pid, Decimal('0.00')) + component['discount_amount']
            units[pid] = units.get(pid, 0) + component['quantity_used']
            unit_prices.setdefault(pid, set()).add(Decimal(str(component['unit_price'])))
            if row.name not in names.setdefault(pid, []):
                names[pid].append(row.name)

    # One line per product: `unique_order_line_per_product` guarantees it in
    # the database, so the map cannot lose a position.
    positions = {line.product_id: index for index, line in enumerate(lines)}
    grosses = [_money(line.unit_price * line.quantity) for line in lines]

    discounts = [Decimal('0.00')] * len(lines)
    reasons: list[tuple[str, ...]] = [()] * len(lines)
    for pid, amount in by_product.items():
        index = positions.get(pid)
        if index is None:
            raise PromotionLineShareError(
                f'La promoción rebaja el producto {pid}, que no está entre las '
                f'líneas de esta venta.'
            )
        line = lines[index]
        if line.product_company_id is None or line.product_company_id != company_id:
            raise PromotionLineShareError(
                f'El producto {pid} de la promoción no pertenece a la empresa de '
                f'la venta.'
            )
        if units[pid] > line.quantity:
            raise PromotionLineShareError(
                f'La promoción consume {units[pid]} unidad(es) del producto {pid} '
                f'y la venta sólo tiene {line.quantity}.'
            )
        if unit_prices[pid] != {line.unit_price}:
            frozen = ', '.join(str(p) for p in sorted(unit_prices[pid]))
            raise PromotionLineShareError(
                f'La promoción congeló el producto {pid} a {frozen} y la línea de '
                f'la venta lo vendió a {line.unit_price}.'
            )
        if amount > grosses[index]:
            raise PromotionLineShareError(
                f'La promoción rebaja {amount} del producto {pid} y la línea sólo '
                f'vale {grosses[index]}.'
            )
        discounts[index] = amount
        reasons[index] = tuple(names[pid])

    return tuple(
        PromotionLineDiscount(
            product_id=line.product_id,
            quantity=line.quantity,
            regular_unit_price=line.unit_price,
            regular_amount=gross,
            discount_amount=discount,
            promotion_names=reason,
        )
        for line, gross, discount, reason in zip(lines, grosses, discounts, reasons)
    )


def order_promotion_line_discounts(order, items) -> tuple[PromotionLineDiscount, ...]:
    """
    `share_promotion_discount` for a stored order: loads its snapshots and calls it.

    `items` are the order's `OrderItem`s in the order their lines are declared
    (the fiscal document uses `pk`), with `product` loaded. The applied
    promotions are read for THIS order and THIS company only, in `pk` order.
    Raises `PromotionLineShareError`.
    """
    from .models import AppliedPromotion

    applied = (
        AppliedPromotion.objects
        .filter(company_id=order.company_id, order=order)
        .select_related('promotion').order_by('pk')
    )
    return share_promotion_discount(
        company_id=order.company_id,
        order_discount=order.discount_amount,
        lines=[SoldLine.from_item(item) for item in items],
        applied=[FrozenPromotion.from_applied(row) for row in applied],
    )


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
