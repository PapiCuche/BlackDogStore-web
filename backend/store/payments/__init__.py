"""
Payments — the boundary between the commercial domain and whoever moves money.

The domain above this package speaks in orders, totals and
`PaymentTransaction`. It does not import a provider. When the provider changed
once already, the code that had to change was the code inside this package;
that is the property this package exists to keep.
"""

from . import izipay

__all__ = ['izipay']
