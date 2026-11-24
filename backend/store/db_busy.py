"""
INV-LOAD-01 review — two writers waiting on each other's locks.

PostgreSQL resolves a deadlock (40P01) or a serialization failure (40001) by aborting one of the transactions. Every
stock door runs its writes in one transaction, so nothing of the losing request was written: the honest answer is
«somebody else was using this stock, try again» (409 `busy`), never a 500.
"""
from __future__ import annotations

from functools import wraps

from django.db import OperationalError
from rest_framework import status
from rest_framework.response import Response

BUSY = 'busy'
BUSY_DETAIL = 'Otra operación estaba usando este stock. Vuelve a intentarlo.'


def is_busy(error) -> bool:
    """Whether a database error is a deadlock or a serialization failure: the other request won, retry."""
    cause = getattr(error, '__cause__', None)
    code = getattr(cause, 'pgcode', None) or getattr(cause, 'sqlstate', None)
    return code in ('40P01', '40001')


def busy_response(detail: str = BUSY_DETAIL) -> Response:
    return Response({'detail': detail, 'code': BUSY}, status=status.HTTP_409_CONFLICT)


def answers_busy(view):
    """A view (method or function) whose deadlock is a 409 `busy`; any other database error still raises."""

    @wraps(view)
    def wrapper(*args, **kwargs):
        try:
            return view(*args, **kwargs)
        except OperationalError as exc:
            if not is_busy(exc):
                raise
            return busy_response()

    return wrapper
