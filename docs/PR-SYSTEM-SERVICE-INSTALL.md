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
   checkout, missing `git`/`uv`/systemd prerequisites, or an unsupported existing install shape.
2. Create the existing `local-inference-gateway` system identity when absent and keep the shipped
   unit's single-process, dedicated-user, protected-state topology unchanged.
3. Export locked production constraints and install the current clean Git revision into an immutable
   release directory under `/opt/local-inference-gateway/releases/<full-sha>`.
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

- The target is a systemd Linux appliance with root access, `git`, and `uv` available.
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

1. A non-root caller, dirty tracked checkout, missing prerequisite, or regular file/directory at the
   activation path is rejected before changing the active release.
2. The installed release path contains the full source commit identity and is populated completely
   before the active symlink changes.
3. A rerun for the same clean commit is idempotent and reuses only a valid installed executable.
4. The installer creates no secret-bearing file and never reads secret values or application data.
5. The installer performs daemon reload but contains no service start, stop, restart, enable, or
   disable operation.
6. The installed unit still runs exactly one process as the dedicated identity with its existing
   private configuration, state, and systemd hardening boundaries.
7. README commands distinguish package installation from explicit private provisioning and service
   activation.

### Implementation summary

NOT DONE.

### Cold diff audit

NOT DONE.

### Gap audit

NOT DONE.
