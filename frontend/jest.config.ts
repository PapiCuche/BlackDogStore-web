/**
 * The Web test runner — H3.
 *
 * ONE RUNNER, CHOSEN FROM WHAT IS ACTUALLY INSTALLED. Next 16.3.4 ships
 * `next/jest`, which configures the SWC transform this app is already built
 * with, reads the `@/*` alias straight out of tsconfig, stubs CSS and static
 * imports, and loads `.env` files the same way the dev server does. Vitest
 * would need a React plugin, a tsconfig-paths resolver, a jsdom environment and
 * its own CSS handling to arrive at the same place — four moving parts instead
 * of one, in a repository that had none.
 *
 * WHAT THIS BASELINE IS FOR, said plainly: proving the runner works and that
 * the pieces the console depends on can be rendered and asserted on. It does
 * NOT retro-cover the Web. Every phase from here adds its own tests; nobody has
 * to build the harness first.
 */
import type { Config } from 'jest';
import nextJest from 'next/jest.js';

const createJestConfig = nextJest({ dir: './' });

const config: Config = {
  testEnvironment: 'jsdom',
  setupFilesAfterEnv: ['<rootDir>/jest.setup.ts'],
  // Only OUR tests. `next build` output and dependencies are not this suite's
  // business, and a runner that wanders into them fails for reasons nobody
  // wrote.
  // `__tests__/support/` holds fixtures, not suites. Without this Jest picks
  // them up as empty test files and fails the run for having nothing to run.
  testMatch: ['<rootDir>/__tests__/**/*.test.{ts,tsx}'],
  testPathIgnorePatterns: ['<rootDir>/.next/', '<rootDir>/node_modules/'],
  // THE ALIAS HAS TO BE STATED TWICE, and it took a failing suite to see why.
  // `next/jest` hands the SWC transform the `jsc.paths` from tsconfig, so every
  // real `import '@/…'` is rewritten before Jest ever resolves it — which is
  // why a dynamic import of an aliased module works with no mapper at all.
  // `jest.mock('@/…')` is a STRING ARGUMENT, not an import specifier: SWC
  // leaves it alone, and Jest's resolver has never heard of `@`. So the mapper
  // below is not a duplicate of tsconfig; it is the half SWC cannot do.
  moduleNameMapper: {
    '^@/(.*)$': '<rootDir>/$1',
  },
  moduleDirectories: ['node_modules', '<rootDir>/'],
  clearMocks: true,
  // Half the cores, not Jest's default (all but one). On a 16 GB, 12-core machine the default 11 jsdom workers push
  // the box into swap: in 6 full runs at the default, the two with 3–4 M swap-ins took 74–99 s and failed 3 tests each
  // (5 s test timeout, 1 s `findBy*` wait, a keystroke-timing check) in suites that pass alone; at 50 % three runs took
  // 26–29 s with 0.1–0.3 M swap-ins, all green. CI runs `test:ci` (`--runInBand`), so this only changes local runs.
  maxWorkers: '50%',
};

export default createJestConfig(config);
