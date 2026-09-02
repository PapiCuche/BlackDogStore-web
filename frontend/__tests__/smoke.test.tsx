import { render, screen } from '@testing-library/react';

/**
 * The runner works. That is the whole claim.
 *
 * A baseline's first test is not about the product — it is about whether the
 * next person can write a test at all. If this fails, nothing below it means
 * anything, and the failure is about configuration rather than about the app.
 */
describe('the harness', () => {
  it('renders a component and finds it in the DOM', () => {
    render(<p>hola</p>);
    expect(screen.getByText('hola')).toBeInTheDocument();
  });

  it('has the jest-dom matchers loaded', () => {
    render(<button type="button" disabled>no</button>);
    expect(screen.getByRole('button')).toBeDisabled();
  });

  it('resolves the @/ alias out of tsconfig', async () => {
    // The alias is `@/*` -> `./*`. If next/jest did not read tsconfig.paths,
    // this import throws and the whole config is wrong in a way a component
    // test would report as something else entirely.
    const mod = await import('@/app/admin/lib/internal-modules');
    expect(Array.isArray(mod.INTERNAL_MODULES)).toBe(true);
  });
});
