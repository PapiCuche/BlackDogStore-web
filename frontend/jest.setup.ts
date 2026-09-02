/**
 * Loaded before every suite.
 *
 * `@testing-library/jest-dom` adds the DOM matchers — `toBeInTheDocument`,
 * `toBeDisabled` — that make an assertion read like the thing it checks.
 */
import '@testing-library/jest-dom';

/**
 * `next/navigation` needs the App Router, and jsdom has no router.
 *
 * Every internal component reaches `usePathname()` (the sidebar decides which
 * link is current with it), so without this the first render throws inside
 * `InternalSidebar` and the failure looks like a component bug rather than a
 * missing environment. Mocked once, here, because it is a fact about the
 * ENVIRONMENT rather than about any one test.
 *
 * A suite that cares about routing overrides these per test; a suite that does
 * not gets a router that answers plausibly and records nothing.
 */
jest.mock('next/navigation', () => ({
  usePathname: () => '/admin',
  useRouter: () => ({
    push: jest.fn(),
    replace: jest.fn(),
    refresh: jest.fn(),
    back: jest.fn(),
    forward: jest.fn(),
    prefetch: jest.fn(),
  }),
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
  redirect: jest.fn(),
  notFound: jest.fn(),
}));
