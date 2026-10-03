import { render } from '@testing-library/react';

import { Panel, TableWrap } from '@/app/admin/components/InventoryUi';

/**
 * ADMIN-INVENTORY-MOBILE-OVERFLOW
 *
 * The inventory dashboard places wide (640px min-width) tables inside Panel
 * grid items. Grid items default to min-width:auto, so without min-w-0 the
 * table's min-content width can force the whole panel wider than a phone even
 * though TableWrap itself is horizontally scrollable.
 *
 * This structural test protects the containment contract used by the browser
 * regression check: the panel and its content area may shrink, while the table
 * stays wide inside its own overflow-x-auto viewport.
 */
describe('inventory panel mobile containment', () => {
  it('lets a wide table scroll inside the panel instead of widening the page', () => {
    const { container } = render(
      <Panel title="Últimos movimientos">
        <TableWrap>
          <tbody>
            <tr>
              <td>Movimiento</td>
            </tr>
          </tbody>
        </TableWrap>
      </Panel>,
    );

    const panel = container.querySelector('section');
    const table = container.querySelector('table');
    const scroller = table?.parentElement;
    const panelBody = scroller?.parentElement;

    expect(panel).toHaveClass('min-w-0');
    expect(panelBody).toHaveClass('min-w-0');
    expect(scroller).toHaveClass('overflow-x-auto');
    expect(table).toHaveClass('min-w-[640px]');
  });
});
