"""
DOC-LEGEND-01 — every legend a document prints, in ONE place.

A legend is a sentence that says what a paper IS (or is not), as opposed to
what it carries (numbers, names, amounts). They used to be literals scattered
over the A4, the 80 mm ticket, the thermal print, the e-mail copy and the API,
and the sales note ended up saying the same thing twice with different words.
Each paper module now reads its legend from here.

THE SALES NOTE (owner decision, 2026-11-18, definitive)
--------------------------------------------------------
The sales note carries NO legend: not in its heading, its detail or its foot,
on any of its four outputs (A4, 80 mm PDF, ESC/POS, e-mail copy). Its heading
is the commercial title («NOTA DE VENTA», `note_title`) followed by the NV-
number and the data of the sale. What keeps it from passing for a receipt is
its STRUCTURE, each point tested (`test_document_legends`):

  * no fiscal vocabulary on the paper (`SALES_NOTE_FORBIDDEN_WORDS`);
  * its numbering never starts with F or B (`InternalSequence.clean`), the
    letters of SUNAT's electronic series;
  * no QR, no digest, no «representación impresa»;
  * a company title that is a fiscal denomination is never printed
    (`note_title`).

Legal basis and what an accountant still has to confirm:
`docs/nota-de-venta-identificacion.md`.

THE OTHER PAPERS
----------------
The order receipt, the quote ticket and the fiscal documents keep their texts
word for word; they only live here now. The fiscal ones are MANDATORY
(representación impresa, BETA, rejected) and their goldens prove they did not
move by a byte.
"""
from __future__ import annotations

import re
import unicodedata

# ---------------------------------------------------------------------------
# Sales note
# ---------------------------------------------------------------------------

#: The commercial title of the sales note when the company names none.
DEFAULT_NOTE_TITLE = 'Nota de venta'

#: Words no customer-facing rendering of a sales note (its papers, its e-mail
#: copy) may contain (lower case, accents as typed). Each one belongs to a
#: payment receipt or to the old legends; «interna» is how the staff screens
#: name the note, not what the customer reads.
SALES_NOTE_FORBIDDEN_WORDS = (
    'factura', 'boleta', 'comprobante', 'sunat', 'validez', 'serie fiscal',
    'control interno', 'registro interno', 'representación impresa', 'interna',
)

#: The first letters SUNAT reserves for electronic series (F factura, B boleta).
#: A sales-note prefix never starts with one of them.
FISCAL_SERIES_INITIALS = ('F', 'B')

#: For STAFF only (API `notice`, admin screens): never printed on the note.
SALES_NOTE_STAFF_NOTICE = (
    'La nota de venta no reemplaza al comprobante electrónico: '
    'emite la boleta o factura al cobrar o entregar.'
)

_FISCAL_DENOMINATIONS = re.compile(
    r'factura|boleta|comprobante|ticket\s+fiscal|recibo\s+por\s+honorarios'
    r'|liquidaci[oó]n\s+de\s+compra|nota\s+de\s+(cr[eé]dito|d[eé]bito)',
    re.IGNORECASE,
)


def is_fiscal_denomination(text: str) -> bool:
    """Whether `text` names a payment receipt (any case, with or without accents)."""
    plain = unicodedata.normalize('NFKD', text or '').encode('ascii', 'ignore').decode()
    return bool(_FISCAL_DENOMINATIONS.search(plain))


def note_title(design=None) -> str:
    """
    The heading of the sales note: the company's title, or «Nota de venta».

    A title that names a payment receipt is never printed, whatever was stored:
    the default is used instead.
    """
    title = (getattr(design, 'document_title', '') or '').strip()
    if not title or is_fiscal_denomination(title):
        return DEFAULT_NOTE_TITLE
    return title


def sales_note_prefix_error(prefix) -> str:
    """Why `prefix` may not number sales notes, or '' when it may."""
    if (prefix or '')[:1].upper() in FISCAL_SERIES_INITIALS:
        return ('El prefijo de las notas de venta no puede empezar por F ni por B: '
                'son las letras de las series de facturas y boletas electrónicas.')
    return ''


# ---------------------------------------------------------------------------
# Order receipt (constancia de pedido) — unchanged texts
# ---------------------------------------------------------------------------

#: The framed statement of the A4 order receipt.
ORDER_RECEIPT_DISCLAIMER = (
    "Documento interno de compra. "
    "No válido como comprobante electrónico SUNAT."
)
#: The sentence of the e-mail that carries a copy of the order receipt.
ORDER_RECEIPT_COPY_NOTE = 'Documento interno del pedido. No es un comprobante electrónico.'

# ---------------------------------------------------------------------------
# Service quote ticket — unchanged text
# ---------------------------------------------------------------------------

QUOTE_DISCLAIMER = 'No es un comprobante de pago ni un documento tributario.'

# ---------------------------------------------------------------------------
# Commercial quote (SALES-QUOTE-01)
# ---------------------------------------------------------------------------

#: The title every commercial quote prints, A4 and roll. Not configurable: it says what the paper IS.
SALES_QUOTE_TITLE = 'COTIZACIÓN'
#: The statement every commercial quote prints (owner's words): a quote is an offer, not a receipt.
SALES_QUOTE_LEGEND = 'No es comprobante de pago.'
#: The line under it: what the offer binds and until when is said by the validity on the paper.
SALES_QUOTE_LEGEND_DETAIL = ('Precios y disponibilidad sujetos a la vigencia indicada. '
                             'La venta se confirma al cobrar y se documenta por separado.')

#: TRADE-IN-QUOTE-00 — the heading of the part-exchange block of a quote, and the statement under it (owner's
#: words): the valuation is referential and is neither credit, payment, purchase nor a receipt.
TRADE_IN_QUOTE_HEADING = 'Equipo ofrecido en parte de pago (referencial)'
TRADE_IN_QUOTE_LEGEND = ('Valorización referencial sujeta a inspección; no es crédito, pago ni compra. '
                         'No es comprobante de pago.')


#: TRADE-IN VALUATION PRO · 5b — the trade-in quote's papers: what the shop OFFERS for a used device. Not a purchase,
#: not a receipt, not a credit (D1-D12); the value holds subject to checking the device when it is received.
TRADE_IN_RECEPTION_TITLE = 'COTIZACIÓN DE RECEPCIÓN DE EQUIPO USADO'
TRADE_IN_RECEPTION_LEGEND = 'No es comprobante de pago ni constancia de compra.'
TRADE_IN_RECEPTION_LEGEND_DETAIL = ('Valor sujeto a la verificación final del equipo al recibirlo. Vigente hasta la '
                                    'fecha y hora indicadas.')
TRADE_IN_DIFFERENCE_NOTE = 'La diferencia es referencial: no es un descuento ni un crédito.'


def quote_prefix_error(prefix) -> str:
    """Why `prefix` may not number commercial quotes, or '' when it may (never F or B, like the sales note)."""
    if (prefix or '')[:1].upper() in FISCAL_SERIES_INITIALS:
        return ('El prefijo de las cotizaciones no puede empezar por F ni por B: '
                'son las letras de las series de facturas y boletas electrónicas.')
    return ''

# ---------------------------------------------------------------------------
# Fiscal documents — MANDATORY, unchanged texts (fiscal goldens byte-identical)
# ---------------------------------------------------------------------------

#: The red band of an A4 boleta/factura of the BETA environment.
FISCAL_BETA_WARNING = 'AMBIENTE DE PRUEBAS — SUNAT BETA · SIN VALIDEZ TRIBUTARIA'
#: The diagonal watermark of every A4 page of the BETA environment, top to bottom.
FISCAL_BETA_DIAGONAL = ('SIN VALIDEZ TRIBUTARIA', 'SUNAT BETA')
#: The two lines under the identifier on the 80 mm ticket and the thermal print.
FISCAL_BETA_ROLL_LINES = ('AMBIENTE DE PRUEBAS — SUNAT BETA', 'SIN VALIDEZ TRIBUTARIA')
#: The «Estado:» of a receipt SUNAT rejected.
FISCAL_REJECTED_STATUS = 'RECHAZADA POR SUNAT — sin validez tributaria'


def fiscal_representation_legend(title: str) -> str:
    """Campos 62 (factura) y 58 (boleta): «Representación impresa de la …»."""
    return 'Representación impresa de la ' + title.lower()
