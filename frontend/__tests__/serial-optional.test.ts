import { unitIdentifier } from '@/app/lib/unit-identifier';

/** SERIAL-OPTIONAL-01 — a device that entered by its IMEI alone is named by it, never by an empty serial. */
describe('unitIdentifier', () => {
  it('la serie cuando la hay', () => {
    expect(unitIdentifier({ serial_number: 'F2LX0001', imei: '356789012345672' })).toBe('F2LX0001');
  });

  it('el IMEI cuando el equipo entró sin serie', () => {
    expect(unitIdentifier({ serial_number: '', imei: '356789012345672' })).toBe('IMEI 356789012345672');
    expect(unitIdentifier({ serial_number: null, imei: '356789012345672' })).toBe('IMEI 356789012345672');
  });

  it('nunca «null» ni «undefined»', () => {
    expect(unitIdentifier({ serial_number: null, imei: null })).toBe('');
    expect(unitIdentifier({})).toBe('');
  });
});
