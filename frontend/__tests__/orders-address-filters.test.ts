/**
 * NAV-SALES-01 — «Pedidos y bajo pedido» abre el mismo listado con filtros distintos: `?supply=pending` (bajo pedido),
 * `?status=cancelled` (cancelaciones) y, desde Documentos, `?receipt=pending`. El listado toma esos filtros de la
 * dirección al abrir y cada vez que el menú cambia la consulta sin cambiar de página.
 */
import { addressQueryKey, filtersFromSearch } from '@/app/admin/lib/orders-address-filters';

describe('filtersFromSearch', () => {
  it('lee los filtros del menú', () => {
    expect(filtersFromSearch('?supply=pending')).toEqual({ alert: '', supply: 'pending', receipt: '', status: '' });
    expect(filtersFromSearch('status=cancelled')).toEqual({ alert: '', supply: '', receipt: '', status: 'cancelled' });
    expect(filtersFromSearch('?receipt=pending')).toEqual({ alert: '', supply: '', receipt: 'pending', status: '' });
    expect(filtersFromSearch('?alert=to_confirm')).toMatchObject({ alert: 'to_confirm' });
  });

  it('sin consulta, todo vacío: «Pedidos» vuelve al listado completo', () => {
    expect(filtersFromSearch('')).toEqual({ alert: '', supply: '', receipt: '', status: '' });
  });

  it('ignora valores que el listado no conoce', () => {
    expect(filtersFromSearch('?supply=x&status=borrado&receipt=done&alert=nope')).toEqual({ alert: '', supply: '', receipt: '', status: '' });
  });
});

describe('addressQueryKey', () => {
  it('la misma consulta escrita de dos maneras es la misma clave', () => {
    expect(addressQueryKey('?supply=pending')).toBe(addressQueryKey('supply=pending'));
    expect(addressQueryKey('')).toBe('');
    expect(addressQueryKey('?')).toBe('');
  });
});
