from django.db import connection
from django.test import skipUnlessDBFeature
from django.test.utils import CaptureQueriesContext

from store.inventory_services import TransferError, set_transfer_item
from store.models import StockTransfer, StockTransferItem
from store.tests import Ip1TransferBase, _ip1t_url


class TransferLineLockTest(Ip1TransferBase):
    """
    INV-LEGACY-V1-F2 — una línea no se edita sobre una transferencia ya despachada.

    `set_transfer_item` decidía si el documento era editable mirando el objeto
    que le pasaban, sin volver a leerlo ni bloquearlo. Entre que la vista cargaba
    la transferencia y escribía la línea, otro podía despacharla: la línea se
    guardaba igual, sobre un documento cuyas unidades ya habían salido, y el
    papel dejaba de coincidir con el Kardex.

    Lo que se prueba es la carrera sin hilos: el objeto en la mano es el de
    antes del despacho.
    """

    def _dispatched_behind_a_stale_copy(self):
        transfer_id = self.draft()
        self.assertEqual(self.line(transfer_id, quantity=3).status_code, 200)
        stale = StockTransfer.objects.get(pk=transfer_id)
        self.assertTrue(stale.is_editable)

        res = self.client.post(
            _ip1t_url('ip1-cadena', f'{transfer_id}/dispatch/'), {}, format='json',
        )
        self.assertEqual(res.status_code, 200, res.data)
        return stale

    def _line(self, transfer):
        return StockTransferItem.objects.get(transfer=transfer, product=self.product)

    def test_a_stale_draft_cannot_change_a_line_after_dispatch(self):
        stale = self._dispatched_behind_a_stale_copy()

        with self.assertRaises(TransferError):
            set_transfer_item(stale, product=self.product, quantity=7)

        self.assertEqual(self._line(stale).quantity, 3)

    def test_a_stale_draft_cannot_remove_a_line_after_dispatch(self):
        stale = self._dispatched_behind_a_stale_copy()

        with self.assertRaises(TransferError):
            set_transfer_item(stale, product=self.product, quantity=0)

        self.assertEqual(self._line(stale).quantity, 3)

    def test_a_stale_draft_cannot_add_a_line_after_dispatch(self):
        stale = self._dispatched_behind_a_stale_copy()
        from store.tests import _prod
        other = _prod(self.company, 'Funda', 'funda-t', price='20.00')

        with self.assertRaises(TransferError):
            set_transfer_item(stale, product=other, quantity=1)

        self.assertFalse(
            StockTransferItem.objects.filter(transfer=stale, product=other).exists())

    @skipUnlessDBFeature('has_select_for_update')
    def test_editing_a_line_locks_the_transfer_row(self):
        transfer = StockTransfer.objects.get(pk=self.draft())

        with CaptureQueriesContext(connection) as queries:
            set_transfer_item(transfer, product=self.product, quantity=2)

        locking = [
            q['sql'] for q in queries
            if 'FOR UPDATE' in q['sql'] and 'store_stocktransfer"' in q['sql']
        ]
        self.assertTrue(locking, [q['sql'][:90] for q in queries])

    def test_a_draft_is_still_editable(self):
        transfer_id = self.draft()
        self.assertEqual(self.line(transfer_id, quantity=4).status_code, 200)
        self.assertEqual(self.line(transfer_id, quantity=0).status_code, 200)
        self.assertFalse(StockTransferItem.objects.filter(transfer_id=transfer_id).exists())


class TransferListPageSizeTest(Ip1TransferBase):
    """
    INV-LEGACY-V1-F3 — un `page_size` absurdo no tumba el listado.

    `page_size=-1` llegaba tal cual al corte del queryset y Django respondía con
    una excepción («Negative indexing is not supported»): un 500 a cambio de un
    parámetro de la URL.
    """

    def _list(self, **params):
        return self.client.get(_ip1t_url('ip1-cadena'), params)

    def test_a_negative_page_size_answers_a_page(self):
        self.draft()
        res = self._list(page_size=-1)
        self.assertEqual(res.status_code, 200, getattr(res, 'data', None))
        self.assertGreaterEqual(res.data['page_size'], 1)
        self.assertEqual(len(res.data['results']), 1)

    def test_a_zero_page_size_answers_a_page(self):
        self.draft()
        res = self._list(page_size=0)
        self.assertEqual(res.status_code, 200)
        self.assertGreaterEqual(res.data['page_size'], 1)
        self.assertEqual(len(res.data['results']), 1)

    def test_the_ceiling_still_holds(self):
        res = self._list(page_size=100000)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['page_size'], 100)
