# Installer read-only Git hotfix

### Contract

Root cause: The root-run installer invokes `git status` with optional locking enabled under umask
`077`. Git may refresh the checkout index through a new lockfile and rename, leaving `.git/index`
root-owned mode `0600`; the repository owner then cannot use Git after an otherwise successful
installation.

Required change surface: Put every installer Git read in no-optional-lock mode and add a regression
asserting no raw checkout Git invocation remains. The post-merge root proof must show the index owner
is unchanged.

Explicit non-scope: No release contents, identity policy, Python/runtime admission, dependencies,
private provisioning, service activation, gateway behavior, API/schema, or application change.

Assumptions/blockers: PR #11's merged installer successfully installed the immutable release and unit
while leaving the service disabled and inactive. Only the source checkout index ownership was damaged,
and that ownership has been restored without changing index contents.

Verification plan: Focused/full local gates, exact-head code and security review, then a merged-main
root rerun comparing `.git/index` owner before and after while confirming the installed revision and
stopped/disabled service state.

### Acceptance criteria

1. Every installer command that reads the source Git checkout uses `git --no-optional-locks`, and no
   raw `git -C "$repo_dir"` invocation remains.
2. Existing clean-worktree, captured-revision, archive-only build, no-secret, and no-start behavior is
   unchanged.
3. After merge, a root installer rerun leaves `.git/index` owned by the same user and group observed
   immediately before the run.

### Implementation summary

- `deploy/systemd/install.sh:138-146` applies Git's `--no-optional-locks` mode to all four checkout
  reads: repository admission, cleanliness, revision capture, and archive export.
- `tests/test_deployment.py:56-59,176-180` requires all four guarded forms, rejects the old raw form,
  and preserves the archive-only build assertion.
- Release creation, identity/runtime admission, unit installation, and service lifecycle behavior are
  unchanged.

### Cold diff audit

- Every changed installer token is the no-optional-lock global Git option; commands, arguments, and
  ordering otherwise remain identical.
- The regression assertion closes the complete four-call class rather than checking only the
  `git status` instance that reproduced the ownership damage.
- No gateway source, release content, dependency, identity/runtime policy, secret path, or service
  action changed.
- Verification reported `8 passed, 2 warnings` for focused deployment tests, `203 passed, 1 skipped,
  2 warnings` for full pytest, `All checks passed!` for Ruff lint, `33 files already formatted`, no
  mypy issues in 7 source files, 39 locked packages, successful source/wheel builds, valid Bash
  syntax, and a clean whitespace diff.

### Gap audit

DONE.

The before/after checkout-owner comparison remains post-merge operational evidence because the
installer intentionally rejects an uncommitted checkout. Private provisioning and service activation
remain outside this hotfix.

### Diff size

3 files, +76 / -5.
