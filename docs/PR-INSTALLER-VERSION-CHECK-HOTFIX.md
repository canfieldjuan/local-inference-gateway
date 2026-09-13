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

Pending.

### Cold diff audit

Pending.

### Gap audit

NOT DONE.
