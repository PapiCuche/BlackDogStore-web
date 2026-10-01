import type { BranchScope } from '@/app/admin/lib/internal-api';
import {
  canEditBranchScopeOf,
  hasCompanyWideScope,
  ownBranchScopePayload,
  reachesBranch,
} from '@/app/admin/lib/branch-authority';

/**
 * UI-SCOPE-01 helpers: mirrors of `tenancy.has_company_wide_scope` and
 * `tenancy.can_delegate_branch_scope`. They read the server's `branch_scope`
 * and never widen it.
 */

const A = { id: 1, name: 'A' };
const B = { id: 2, name: 'B' };
const scope = (mode: BranchScope['mode'], branches = [A]): BranchScope => ({
  mode, default_branch: null, branches,
});

describe('hasCompanyWideScope', () => {
  it.each(['platform', 'all', 'legacy'] as const)('%s reaches the whole company', (mode) => {
    expect(hasCompanyWideScope(scope(mode))).toBe(true);
  });

  it.each(['selected', 'none'] as const)('%s does not', (mode) => {
    expect(hasCompanyWideScope(scope(mode))).toBe(false);
  });

  it('an unknown scope means no', () => {
    expect(hasCompanyWideScope(null)).toBe(false);
    expect(hasCompanyWideScope(undefined)).toBe(false);
  });

  it('is never inferred from holding every visible branch', () => {
    expect(hasCompanyWideScope(scope('selected', [A, B]))).toBe(false);
  });
});

describe('reachesBranch', () => {
  it('SELECTED reaches only its own branches', () => {
    expect(reachesBranch(scope('selected'), A.id)).toBe(true);
    expect(reachesBranch(scope('selected'), B.id)).toBe(false);
  });

  it('company-wide reaches any branch', () => {
    expect(reachesBranch(scope('all', []), B.id)).toBe(true);
  });
});

describe('canEditBranchScopeOf', () => {
  it('SELECTED edits someone inside its reach', () => {
    expect(canEditBranchScopeOf(scope('selected'), { mode: 'selected', grantedIds: [A.id] })).toBe(true);
  });

  it('SELECTED does not touch someone who reaches further', () => {
    expect(canEditBranchScopeOf(scope('selected'), { mode: 'selected', grantedIds: [A.id, B.id] })).toBe(false);
    expect(canEditBranchScopeOf(scope('selected'), { mode: 'all', grantedIds: [] })).toBe(false);
  });

  it('company-wide edits anyone; no scope edits no one', () => {
    expect(canEditBranchScopeOf(scope('all'), { mode: 'all', grantedIds: [] })).toBe(true);
    expect(canEditBranchScopeOf(scope('none'), { mode: 'selected', grantedIds: [] })).toBe(false);
    expect(canEditBranchScopeOf(null, { mode: 'selected', grantedIds: [] })).toBe(false);
  });
});

describe('ownBranchScopePayload', () => {
  it('company-wide keeps the server default', () => {
    expect(ownBranchScopePayload(scope('all'))).toEqual({});
  });

  it('SELECTED names exactly its own branches', () => {
    expect(ownBranchScopePayload(scope('selected', [A, B]))).toEqual({
      branch_scope: 'selected', branches: [A.id, B.id],
    });
  });
});
