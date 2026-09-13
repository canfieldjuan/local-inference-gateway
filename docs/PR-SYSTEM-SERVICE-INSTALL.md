# Reproducible System-Service Installation

### Contract

Root cause:

- The repository ships a hardened systemd unit and documents its intended filesystem topology, but
  it does not provide the command that creates the dedicated service account, installs one immutable
  package snapshot, activates that snapshot atomically, and installs the unit.
- Reconstructing those commands by hand risks running a dirty checkout, updating a live virtual
  environment in place, putting application secrets in the repository, or starting a service before
  its private configuration is ready.
- The current Linux host has passwordless administrator access and needs a durable gateway before
  applications can be cut over from direct worker access.

Required change surface:

1. Add one root-run systemd installer that accepts no secret values and refuses a dirty tracked
   checkout, missing `git`/`uv`/Python/systemd prerequisites, an incompatible existing service
   identity, or an unsupported existing install shape.
2. Create the existing `local-inference-gateway` system identity when absent and keep the shipped
   unit's single-process, dedicated-user, protected-state topology unchanged.
3. Export locked production constraints, pin the isolated build environment, and install the current
   clean Git revision with a service-readable Python into an immutable release directory under
   `/opt/local-inference-gateway/releases/<full-sha>`.
4. Activate only a completely installed release through an atomic `venv` symlink. Never mutate the
   active virtual environment in place and never delete prior releases automatically.
5. Install the reviewed unit, create only the empty private configuration/state directories, and run
   `systemctl daemon-reload`. Do not enable, start, restart, or stop the service.
6. Document the operator sequence: install snapshot, provision owner-controlled files outside Git,
   validate configuration, enable/start explicitly, and inspect liveness/status/logs without
   exposing credentials.
7. Add deterministic tests for the installer contract and preserve the existing unit assertions.

Explicit non-scope:

- No credential, bearer-token, encryption-key, TLS certificate, CA, model, or environment-file
  generation; no copying of the temporary operational-proof secrets.
- No service start/restart/enable, firewall change, port exposure, DNS, certificate automation,
  runtime start/stop, model download, model promotion, GPU configuration, or package signing.
- No Email Watcher, Document Summarizer, Invoice Processor, Connect, task, request, result, schema,
  queue, routing, or fallback change.
- No application cutover. Each application receives its gateway credential and configuration in a
  separate vertical after this service is live and verified.
- No automatic release pruning or rollback command in this slice.

Assumptions and blockers:

- The target is a systemd Linux appliance with root access, `git`, `uv`, and a service-readable
  Python 3.12+ interpreter available.
- Private configuration will use `/etc/local-inference-gateway`; mutable gateway state will use
  `/var/lib/local-inference-gateway`; the package snapshot lives under `/opt`.
- The gateway must remain stopped until the operator supplies a complete private environment,
  credential digest file, result key, and any required TLS/LM Studio token files.
- Current-host provisioning after merge is possible, but application cutover remains independently
  reviewable and is not evidence for this installer contract.

Verification plan:

- Run shell syntax checking and deterministic static/integration tests against the installer and
  shipped unit.
- Run full pytest, Ruff lint/format, mypy, lock verification, package build, and diff whitespace.
- After merge, run the installer from clean `main`, verify the installed executable reports the
  merged package snapshot, and confirm the installer did not enable or start an unconfigured unit.

### Acceptance criteria

1. A non-root caller, dirty tracked checkout, missing prerequisite, non-local, default-NSS-mismatched,
   or supplementary-group service identity, interpreter outside a root-owned non-writable path,
   inaccessible Python, or regular file/directory at the activation path is rejected before changing
   the active release.
2. The installed release path contains the full source commit identity and is populated completely
   before the active symlink changes.
3. A rerun for the same clean commit is idempotent and reuses only a service-readable valid installed
   executable whose release symlinks traverse only root-owned non-writable paths and resolve inside
   the immutable tree or to root-owned non-writable regular files.
4. The installer creates no secret-bearing file and never reads secret values or application data.
5. The installer performs daemon reload but contains no service start, stop, restart, enable, or
   disable operation.
6. The installed unit still runs exactly one process as the dedicated identity with its existing
   private configuration, state, and systemd hardening boundaries.
7. README commands distinguish package installation from explicit private provisioning and service
   activation.

### Implementation summary

- `deploy/systemd/install.sh` now requires a local-files-only dedicated identity selected identically
  by default NSS with no supplementary groups and a service-readable interpreter whose full canonical
  path is root-owned and non-writable, captures the selected commit into a root-owned snapshot,
  installs that snapshot with locked runtime and build dependencies copied into a root-owned,
  service-readable commit-addressed release, rejects incomplete, mismatched, mutable, or externally
  redirected releases including every unsafe symlink traversal and target, rejects redirected managed
  paths, serializes installers, flips the active virtual-environment symlink only after an isolated
  service-user import resolves inside the release environment, installs the snapshot's reviewed unit,
  and performs only `daemon-reload`.
- `deploy/systemd/build-constraints.txt` pins the complete isolated Hatchling build environment used
  by the installer so one commit-addressed release does not resolve differently over time.
- `tests/test_deployment.py` now exercises shell syntax and non-root rejection and asserts the clean
  revision, immutable-release, identity-marker, atomic-activation, no-secret, and no-service-action
  boundaries while retaining the existing unit checks.
- `README.md` now documents the exact install, private provisioning, explicit activation, status,
  and log sequence, including the host-required absolute `UV_BIN` override.
- Gateway runtime, APIs, task schemas, routing, credentials, applications, and worker processes are
  intentionally unchanged.

### Cold diff audit

- `deploy/systemd/install.sh` defines fixed system paths and fail-closed admission for root,
  prerequisites, absolute executable `uv`/Python paths, a Git checkout, and a clean worktree; it
  creates or validates the local-files-only non-login system identity against the host's regular
  UID/GID boundary, verifies default NSS selects the same account and group, rejects supplementary
  group membership, and proves that identity can execute Python only after every canonical interpreter
  path component is root-owned and non-writable;
  it also rejects symlinked/non-directory private paths before changing their ownership and takes a
  host-wide nonblocking installer lock. This satisfies contract items 1 and 2 and is covered by
  `tests/test_deployment.py` plus `bash -n`.
- `deploy/systemd/install.sh` and `deploy/systemd/build-constraints.txt` derive the full commit
  identity, archive that immutable revision into a private snapshot, reject symlinked/incomplete or
  identity-mismatched releases, install locked runtime and build dependencies at their final path
  with copy mode so the release does not depend on the mutable `uv` cache, explicitly grant safe
  read/traverse permissions despite the installer's private default umask,
  reject release trees that are not root-owned and non-writable by group/other, require the installed
  entrypoint and revision marker to be regular in-release files rather than symlinks, validate every
  remaining symlink's lexical traversal and canonical target before allowing it to resolve inside the
  release or to a root-owned non-writable external regular file, and prove in an isolated environment
  that the service identity imports the package from the active release prefix;
  it retains the incomplete-release cleanup guard through that proof and atomically replaces the
  active symlink afterward. This satisfies contract items 3 and 4 and is covered by
  `tests/test_deployment.py`.
- `deploy/systemd/install.sh` installs the reviewed unit and reloads systemd without any service
  lifecycle action or secret provisioning. This satisfies contract item 5 and is covered by
  `tests/test_deployment.py` and the preserved unit assertions.
- `README.md:192-252` separates package installation, operator-owned private provisioning, explicit
  activation, and inspection. This satisfies contract item 6.
- Verification on the final implementation reported `7 passed, 2 warnings` for the focused
  deployment tests, `202 passed, 1 skipped, 2 warnings` for full pytest, `All checks passed!` for
  Ruff lint, `31 files already formatted`, no mypy issues in 7 source files, 39 locked packages,
  both wheel and source distribution built, valid Bash syntax, and a clean whitespace diff. Two-sided
  probes accepted `/usr/bin/python3.12`, an internal release link, and a root-controlled external
  interpreter link while rejecting writable `/tmp` ancestry and a writable external release target;
  default and files-only NSS both resolved root to UID 0 on the target host. A fresh `uv venv` under
  the private umask reproduced mode `700`; the installer's permission normalization changed both the
  virtual-environment root and `bin` directory to mode `755`.
- No untraced runtime, dependency, schema, routing, model, credential, application, or service-state
  change appears in the diff.

### Gap audit

DONE.

The after-merge root installation and stopped-service inspection remain deployment evidence, not a
gap in the reviewable installer implementation. Private provisioning and service activation remain
the next explicitly separate vertical.
