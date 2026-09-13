# User-unit capability sandbox hotfix

### Contract

Root cause:

- The merged persistent Ollama unit applies `ProtectClock`, `ProtectKernelLogs`, and
  `ProtectKernelModules` in an unprivileged systemd user manager. On this host, each directive makes
  systemd fail before `ExecStartPre` with status `218/CAPABILITIES` because the user manager cannot
  perform the requested capability-bounding operation.
- The worker therefore never reaches its mount gate or Ollama process. The transient loopback worker
  had to be restored after the failed cutover.

Required change surface:

- Remove only the three user-manager-incompatible hardening directives from the Ollama user unit.
- Keep the fixed executable, owner-private environment, mount/directory gates, loopback/cloud/bounded
  settings, restart policy, and all compatible sandbox directives unchanged.
- Update deployment regression assertions to forbid reintroducing those three directives.
- Prove each removed directive fails independently and each retained directive executes under the
  current user manager; after merge, reinstall and exercise the real persistent worker.

Explicit non-scope:

- No gateway runtime, worker selection, API, task, model, application, credential, installer, README,
  vendor system unit, mount, GPU, LM Studio, or concurrency change.
- No replacement hardening that requires new kernel, namespace, or capability assumptions.

Assumptions and blockers:

- The current unprivileged identity already lacks the privileged clock, kernel-log, and module
  capabilities; removing directives that fail while trying to reduce those capabilities does not
  grant the worker a new privilege.
- The known-good transient worker remains active until the merged hotfix unit is installed.

Verification plan:

- Focused deployment test and full gateway test suite.
- Ruff lint/format, mypy, package build, Bash/unit validation, and whitespace check.
- Disposable user-unit property matrix proving the removed directives fail and retained directives
  succeed on this host.
- After merge: reinstall the committed unit, stop the transient worker, enable/start the persistent
  worker, then verify active state, loopback listener, configured model, and gateway task health.

### Acceptance criteria

1. The shipped user unit omits exactly the three independently reproduced incompatible directives.
2. Every existing functional, privacy, retry, mount, and compatible sandbox declaration remains.
3. Deployment tests fail if any incompatible directive returns or a required retained declaration
   disappears.
4. The merged persistent unit starts successfully on the current host and restores gateway task
   availability without enabling or changing the vendor system service.

### Implementation summary

- Removed only `ProtectClock`, `ProtectKernelLogs`, and `ProtectKernelModules` from the unprivileged
  Ollama user unit. All mount, listener, retry, executable, and compatible sandbox controls remain.
- Deployment tests now make reintroducing any of the three incompatible declarations a regression.

### Cold diff audit

- `deploy/systemd/local-inference-ollama.service` removes the three declarations independently proven
  to fail with status `218/CAPABILITIES`; no command, environment, or other sandbox line moved.
- `tests/test_deployment.py` forbids those exact declarations while retaining the existing positive
  assertions for the worker lifecycle and privacy boundary.
- A disposable user unit containing the two real mount gates and every retained sandbox property
  completed successfully. The independent property matrix failed only the three removed properties.
- Untraced or forbidden changes: none.

### Gap audit

DONE for the reviewable hotfix implementation.

- Focused deployment tests reported 10 passed; full pytest reported 206 passed and one intentional
  live-test skip. Ruff lint/format, mypy over seven source files, package build, systemd unit
  validation, the retained-property probe, and whitespace validation passed.
- GitHub review/landing and merged-unit activation remain landing and operational evidence steps.
