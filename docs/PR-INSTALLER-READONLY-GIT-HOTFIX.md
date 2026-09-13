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

Pending.

### Cold diff audit

Pending.

### Gap audit

NOT DONE.
