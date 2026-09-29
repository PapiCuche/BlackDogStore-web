"""
Reparto determinista del valor de venta por línea, anclado al snapshot.

VEN-02A. El snapshot de la venta (`tax_services`) es la AUTORIDAD monetaria:
`taxable`, `tax`, `total`. Las líneas del comprobante se RECONSTRUYEN para que su
suma sea EXACTAMENTE ese snapshot, en vez de una segunda cuenta que redondea por
su lado (el defecto VEN-02A: `sum(round(precio/1.18)·cantidad)` diverge de
`round(total/1.18)`).

Para una venta GRAVADA sin descuento:

  - bruto de línea  G_i = precio_con_igv_i × cantidad_i        (2 decimales exactos)
  - Σ G_i = total                                              (por construcción)
  - base ideal_i    = G_i / (1+tasa)                           (alta precisión)
  - Σ ideal_i = total/(1+tasa)                                 (exacto)
  - taxable = HALF_UP(total/(1+tasa)) = HALF_UP(Σ ideal_i)     (autoridad)

Cada base se trunca a 2 decimales (ROUND_DOWN) y el déficit `taxable − Σ floor`
se reparte de a un céntimo a las líneas con mayor resto (Hamilton / mayor
residuo), con desempate ESTABLE por índice. Entonces:

  - Σ base_i == taxable                                (por construcción)
  - line_tax_i = G_i − base_i ; Σ line_tax = total − taxable = tax   (automático)
  - cada base_i está a ≤ 1 céntimo de su ideal → coherente con cantidad×unitario

COTA DEMOSTRADA del déficit (§16). Con truncado hacia abajo, Σ floor ∈ (Σ ideal −
n·0.01, Σ ideal], y taxable = HALF_UP(Σ ideal) ∈ [Σ ideal − 0.005, Σ ideal +
0.005]. Luego déficit = taxable − Σ floor ∈ [0, n·0.01]; en céntimos, un entero
en 0..n. Un déficit fuera de [0, n] NO es redondeo — es un descuadre real
(descuento no declarado, caso no gravado) — y se FALLA CERRADO. Cada línea recibe
a lo sumo un céntimo, así que ninguna se aparta más de 0.01 de su ideal.

Módulo puro: sin Django, sin ORM. Sólo Decimal.
"""

from __future__ import annotations

from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

CENT = Decimal('0.01')
#: SUNAT admite el valor unitario a n(12,10) — hasta 10 decimales.
UNIT_QUANT = Decimal('0.0000000001')


class ReconciliationError(ValueError):
    """La suma de líneas no reconcilia con el snapshot sin forzar el cuadre."""


def money(value: Decimal) -> Decimal:
    """Importe a 2 decimales, media al alza. Misma política que `tax_services`."""
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def unit_value(base: Decimal, quantity: Decimal) -> Decimal:
    """Valor unitario SIN impuesto = base/cantidad, a 10 decimales (HALF_UP).

    Se deriva de la base RECONCILIADA para que `round(cantidad × unitario, 2)`
    devuelva la base de la línea — la relación que SUNAT valida.
    """
    return (Decimal(base) / Decimal(quantity)).quantize(
        UNIT_QUANT, rounding=ROUND_HALF_UP)


def allocate_line_bases(gross_line_totals, *, taxable, rate) -> list[Decimal]:
    """
    Bases (2dp) por línea cuya suma es EXACTAMENTE `taxable`.

    `gross_line_totals`: G_i (2dp, con impuesto) en orden estable. Determinista:
    el reparto del residuo depende sólo de (resto, índice). Levanta
    ReconciliationError si el déficit sale de la cota derivada [0, n].
    """
    grosses = [Decimal(g) for g in gross_line_totals]
    n = len(grosses)
    if n == 0:
        raise ReconciliationError('Un comprobante sin líneas no puede reconciliar.')

    proportion = Decimal('1') + Decimal(rate)
    ideals = [g / proportion for g in grosses]
    floors = [i.quantize(CENT, rounding=ROUND_DOWN) for i in ideals]
    remainders = [ideals[k] - floors[k] for k in range(n)]

    floor_sum = sum(floors, Decimal('0.00'))
    deficit = Decimal(taxable) - floor_sum
    cents = int((deficit / CENT).to_integral_value(rounding=ROUND_HALF_UP))

    if cents < 0 or cents > n:
        raise ReconciliationError(
            f'La suma de líneas no reconcilia con la base declarada: déficit '
            f'{deficit} sobre {n} línea(s), fuera de la cota derivada 0..{n}.'
        )

    # +0.01 a las `cents` líneas de mayor resto; desempate ESTABLE por índice.
    order_idx = sorted(range(n), key=lambda k: (-remainders[k], k))
    boosted = set(order_idx[:cents])
    return [floors[k] + (CENT if k in boosted else Decimal('0.00')) for k in range(n)]


def allocate_proportionally(total, weights) -> list[Decimal]:
    """
    Reparte `total` (2dp) entre posiciones en proporción a `weights`, cerrando
    EXACTO: ROUND_DOWN por posición y los céntimos que falten a los mayores
    restos, con desempate estable por índice.

    Es la misma disciplina de `allocate_line_bases` aplicada a otra pregunta:
    allí se reparte una base entre brutos; aquí, un descuento neto entre los
    descuentos brutos de cada línea. Una posición con peso cero recibe cero,
    siempre: los céntimos sobrantes son menos que las posiciones con resto
    positivo, y sólo un peso positivo deja resto.

    `total == 0` devuelve ceros. Todos los pesos a cero con `total > 0` no tiene
    solución y levanta `ReconciliationError`.
    """
    weights = [Decimal(w) for w in weights]
    total = Decimal(total)
    n = len(weights)
    if n == 0:
        raise ReconciliationError('No hay posiciones entre las que repartir.')
    if total < 0 or any(w < 0 for w in weights):
        raise ReconciliationError('Un reparto proporcional no admite negativos.')
    if total == 0:
        return [Decimal('0.00')] * n
    weight_sum = sum(weights, Decimal('0'))
    if weight_sum == 0:
        raise ReconciliationError(
            f'No se puede repartir {total} entre posiciones que pesan cero.')

    ideals = [total * w / weight_sum for w in weights]
    floors = [i.quantize(CENT, rounding=ROUND_DOWN) for i in ideals]
    remainders = [ideals[k] - floors[k] for k in range(n)]
    deficit = total - sum(floors, Decimal('0.00'))
    cents = int((deficit / CENT).to_integral_value(rounding=ROUND_HALF_UP))
    if cents < 0 or cents > n:
        raise ReconciliationError(
            f'El reparto proporcional no cierra: faltan {deficit} sobre {n} '
            f'posición(es).')
    order_idx = sorted(range(n), key=lambda k: (-remainders[k], k))
    boosted = set(order_idx[:cents])
    return [floors[k] + (CENT if k in boosted else Decimal('0.00')) for k in range(n)]
