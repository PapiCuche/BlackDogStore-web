"""
One e-mail template for everything a shop sends.

    from store import mail

    if mail.available(company):
        mail.send('staff_invitation', address, mail.builders.staff_invitation(...), company=company)

THE TEMPLATE IS A SHOP'S, NOT THE PLATFORM'S. `plantilla/plantilla-maestra.html`
carries one business's name, address, phone and social accounts in its footer,
and `plantilla/marca.json` says whose it is. It dresses that company's e-mails
and — on an installation that serves that company's storefront — the account
e-mails of the platform. Any other company keeps the neutral e-mails it had:
`available()` answers False and the caller sends what it sent before. A second
tenant never signs its orders with somebody else's address.

WHAT IS HERE

  skin       whose template it is, and the template with its fixed values in
  contract   the shape of the data a message is made of, and its checks
  format     money and dates as the template prints them
  text       the plain-text version, from the same data
  builders   one function per kind of message, returning that data
  service    render and send

The data is the contract in `ejemplos/*.json`: the template shows a block when
its key is there and hides it when it is not, so a builder OMITS what it does
not have. It never sends an empty string.
"""
from . import builders  # noqa: F401
from .service import KINDS, Rendered, available, brand, deliver, render, send  # noqa: F401
