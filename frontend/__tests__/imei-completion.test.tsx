import { fireEvent, render, screen } from '@testing-library/react';

import { ImeiCompletionHint } from '@/app/admin/components/ImeiCompletionHint';
import { imeiCompletion, imeiPairProblem, imeiProblem } from '@/app/admin/lib/unit-entry';

/**
 * INV-ID-01 · con 14 dígitos exactos se ofrece el decimoquinto (Luhn) y se
 * muestra el número completo; con 15 sólo se valida, nunca se cambia el
 * último. Luhn no prueba que el IMEI sea de ese equipo: lo decide el servidor.
 */

const BODY = '35693803564380'; // 14 digits of a test IMEI; its check digit is 9 (device_identity.luhn_check_digit)
const FULL = `${BODY}9`;

it('con 14 dígitos calcula el que falta', () => {
  expect(imeiCompletion(BODY)).toBe(FULL);
  expect(imeiProblem(FULL)).toBeNull();
});

it('acepta separadores al escribir, y completa sólo los dígitos', () => {
  expect(imeiCompletion('35 693803 564380')).toBe(FULL);
});

it('con 15 dígitos no propone nada, aunque el último esté mal', () => {
  expect(imeiCompletion(FULL)).toBeNull();
  expect(imeiCompletion(BODY + '1')).toBeNull();
  expect(imeiProblem(BODY + '1')).toBe('Ese IMEI no es válido: revisa los dígitos.');
});

it('con otra cantidad de dígitos, o un relleno, no propone nada', () => {
  for (const value of ['', '3569380356438', '356938035643801', '00000000000000', 'abcdefghijklmn']) {
    expect(imeiCompletion(value)).toBeNull();
  }
});

it('el segundo IMEI no puede repetir el primero', () => {
  expect(imeiPairProblem(FULL, FULL)).toBe('El segundo IMEI es distinto del primero.');
  expect(imeiPairProblem(FULL, '')).toBeNull();
  expect(imeiPairProblem('', FULL)).toBe('Indica primero el IMEI principal.');
});

it('el aviso muestra el número completo y lo completa sólo si se pide', () => {
  const onComplete = jest.fn();
  const { rerender } = render(<ImeiCompletionHint value={BODY} onComplete={onComplete} />);
  expect(screen.getByRole('status')).toHaveTextContent(`Falta un dígito: ¿es ${FULL}?`);
  fireEvent.click(screen.getByRole('button', { name: `Completar con ${FULL.slice(-1)}` }));
  expect(onComplete).toHaveBeenCalledWith(FULL);
  rerender(<ImeiCompletionHint value={FULL} onComplete={onComplete} />);
  expect(screen.queryByRole('status')).toBeNull();
});
