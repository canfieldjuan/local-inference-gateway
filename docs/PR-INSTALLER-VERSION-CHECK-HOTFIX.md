# Installer Python version-check hotfix

### Contract

Root cause: The root installer encodes runtime discovery as `raise <conditional expression>`. On a
supported interpreter the expression prints the paths, evaluates to `None`, and then Python raises
that `None`, so every valid install fails before any release or unit is installed.

Required change surface: Make the exact installer runtime probe executable as a supported Python
3.12+ program and add a regression test that extracts and runs that exact embedded probe.

Explicit non-scope: No installer trust-boundary expansion, identity changes, dependency changes,
private provisioning, service activation, gateway runtime behavior, API/schema change, or release
cleanup.

Assumptions/blockers: The live proof already established the service remains inactive and no release
or unit was installed; the compatible dedicated identity created before failure may be reused.

Verification plan: Execute the focused deployment tests, full pytest, Ruff lint/format, mypy, lock
verification, package build, Bash syntax, and whitespace validation; merge after exact-head review,
then rerun the root installer and verify the service remains disabled and stopped.

### Acceptance criteria

1. `tests/test_deployment.py` extracts the runtime probe embedded in `deploy/systemd/install.sh` and
   executes that exact code with the current supported isolated interpreter, producing absolute
   runtime paths without an exception.
2. The installer still rejects Python older than 3.12 and performs runtime-path admission before its
   first service-identity Python execution.
3. The diff changes no system-service lifecycle command, secret handling, identity policy, runtime
   path policy, gateway runtime code, or application behavior.

### Implementation summary

- `deploy/systemd/install.sh:224-233` expresses the version gate and path emission as an ordinary
  multiline Python program: unsupported versions exit before output, while supported versions print
  their isolated paths without raising the return value of `print`.
- `tests/test_deployment.py:149-168` extracts that exact embedded program and executes it with the
  supported isolated test interpreter, requiring successful absolute-path output.
- All existing runtime admission ordering and system-service lifecycle boundaries remain unchanged.

### Cold diff audit

- The installer diff changes only the malformed embedded Python expression into a named executable
  probe; it still runs through the same capability-free identity and feeds the same lexical,
  canonical, ownership, and unit-visibility checks.
- The regression test exercises the installer-owned probe itself rather than a duplicated equivalent.
- No gateway source, dependency, identity policy, runtime-path policy, secret path, or service action
  changed.
- Verification reported `8 passed, 2 warnings` for focused deployment tests, `203 passed, 1 skipped,
  2 warnings` for full pytest, `All checks passed!` for Ruff lint, `32 files already formatted`, no
  mypy issues in 7 source files, 39 locked packages, successful source/wheel builds, valid Bash
  syntax, and a clean whitespace diff.

### Gap audit

DONE.

The root installer rerun remains post-merge operational evidence because a dirty or unmerged checkout
is intentionally rejected. Private provisioning and service activation remain outside this hotfix.

### Diff size

3 files, +93 / -4.
