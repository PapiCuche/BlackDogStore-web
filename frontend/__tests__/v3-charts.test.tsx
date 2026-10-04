import { render, screen } from '@testing-library/react';
import { DonutChart } from '@/app/admin/components/charts';

test('el gráfico de anillo usa el color del tema para sus datos', () => {
  render(<DonutChart series={[{ label: 'Publicados', value: 3 }]} centerLabel="Productos" centerValue={3} />);
  const chart = screen.getByRole('img');
  expect(chart.querySelector('circle[stroke-dasharray]')).toHaveAttribute('stroke', 'currentColor');
  expect(chart.querySelector('text')).toHaveAttribute('fill', 'currentColor');
});
