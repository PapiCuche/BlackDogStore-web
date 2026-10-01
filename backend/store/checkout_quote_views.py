"""
Cuánto costará esta compra y cuánto de eso es impuesto — ANTES de pagar.

POR QUÉ HACE FALTA UNA RUTA PARA ESTO
-------------------------------------
El resumen del carrito lo pintaba el navegador: sumaba los precios en
JavaScript y restaba el cupón. Para un total eso pasa —el servidor vuelve a
calcularlo y manda él—, pero el desglose tributario no puede nacer ahí.

Si el navegador dividiera el total entre 1,18 por su cuenta habría DOS
autoridades de cálculo: una en Python con `Decimal` y otra en JavaScript con
coma flotante. El día que difieran un céntimo, el cliente verá un IGV en
pantalla y otro en el papel que se lleva. Y `0.1 + 0.2 !== 0.3` en JavaScript
no es una curiosidad: es exactamente este error.

Así que el desglose se pregunta. La respuesta sale del MISMO
`price_checkout` que usará el cobro y del MISMO `tax_services` que congelará la
orden.

LO QUE NO HACE
--------------
No crea nada, no reserva stock, no cobra. Es una pregunta. Y no es un
comprobante: nada de lo que devuelve puede presentarse como emitido.
"""

from __future__ import annotations

from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from . import checkout_services as checkout
from .tenancy import resolve_storefront_company, storefront_cart_items
from .throttles import CheckoutQuoteThrottle
from .tax_services import breakdown_from_total


class CheckoutQuoteView(APIView):
    """
    POST /api/checkout/quote/ — el desglose del carrito actual.

    El carrito se lee del servidor, NO del cuerpo de la petición: un cliente que
    pudiera enviar sus propias líneas se cotizaría los precios que quisiera. Del
    cuerpo se aceptan sólo la clave de sesión —que identifica QUÉ carrito, igual
    que en el cobro— y el cupón, que es lo que el comprador sí escribe.
    """

    #: CUBO PROPIO, no el del cobro.
    #:
    #: Compartir `CheckoutThrottle` con `payments/create-checkout-session/`
    #: convertía mirar el checkout en un motivo para no poder comprar: doce
    #: cotizaciones agotaban el presupuesto y la creación de la sesión de pago
    #: respondía 429 sin llegar a ejecutarse. Sigue limitado porque recalcula
    #: precios y stock, que es trabajo real contra la base de datos.
    throttle_classes = [CheckoutQuoteThrottle]

    permission_classes = [permissions.AllowAny]

    def post(self, request):
        # El tenant sale del ESCAPARATE, nunca del cuerpo. Es la misma regla que
        # la creación de la sesión de pago, y por el mismo motivo.
        company = resolve_storefront_company(request)
        if company is None:
            return Response(
                {'detail': 'No se pudo determinar la tienda. Inténtelo nuevamente.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        session_key = str(request.data.get('session_key') or '')
        if not session_key:
            return Response(
                {'detail': 'Falta la clave de sesión del carrito.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            branch = checkout.resolve_fulfillment_branch(company)
            cart_items = list(
                storefront_cart_items(request, session_key).select_related('product')
            )
            if not cart_items:
                raise checkout.CheckoutError('El carrito está vacío.')

            lines = [
                checkout.CheckoutLine(product=item.product, quantity=item.quantity)
                for item in cart_items
            ]
            subtotal = checkout.validate_lines_and_subtotal(branch, lines)
            pricing = checkout.price_checkout(
                company, subtotal, str(request.data.get('coupon_code') or ''),
            )
        except checkout.CheckoutError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        from .company_settings import get_company_settings

        settings_row = get_company_settings(company)
        result = breakdown_from_total(
            total=pricing.total,
            subtotal=pricing.subtotal,
            discount_amount=pricing.discount_amount,
            currency=(getattr(settings_row, 'currency', '') or 'PEN'),
            company=company,
        )

        percent = (result.tax_rate * 100).normalize()
        return Response({
            **result.as_dict(),
            'coupon_code': pricing.coupon.code if pricing.coupon else '',
            # Rótulos servidos, no compuestos en el navegador: la etiqueta y la
            # cifra tienen que venir de la misma cabeza.
            'tax_label': f'IGV ({percent:f}%)',
            'base_label': {
                'taxed': 'Op. gravada',
                'exempt': 'Op. exonerada',
                'unaffected': 'Op. inafecta',
            }.get(result.tax_treatment, 'Op. gravada'),
            # AQUÍ NO SE HA EMITIDO NADA. El comprobante se solicita; su emisión
            # ante SUNAT es otra cosa y todavía no existe.
            'notice': (
                'Precios con impuestos incluidos. El comprobante solicitado se '
                'emite por separado; este resumen no es un comprobante.'
            ),
        })
