# Reboot-safe private Ollama worker

### Contract

Root cause:

- The operational gateway is durable, but its Ollama primary is a transient user unit stored under
  `/run/user/1000/systemd/transient`. That definition disappears across reboot, so the gateway can
  recover while its only primary worker does not.
- The installed vendor `ollama.service` is not a safe substitute: it binds `0.0.0.0`, carries a
  user-specific executable path, and runs under a separate system identity. Enabling it would break
  the gateway-private, loopback-only worker boundary.
- The selected model directory lives below a removable/non-root mount. Starting Ollama before that
  mount exists could create or use a same-named directory on the root filesystem. Worker startup
  must therefore fail closed and retry until both the expected mount and model directory exist.

Required change surface:

- Ship one persistent systemd user unit for the existing `/usr/local/bin/ollama serve` worker.
- Keep the worker loopback-bound, cloud-disabled, and bounded to the current context, loaded-model,
  and parallel-request limits through an owner-private environment file.
- Gate every start on the configured model mount being an active mount point and the configured
  model directory already existing. A failed preflight must remain retryable without creating the
  directory.
- Add one unprivileged installer that validates the Ollama executable, current mount/directory,
  user linger, and safe path syntax before atomically installing the environment and unit files.
  It may reload the user manager but must not start, stop, enable, disable, or mutate either Ollama
  service.
- Document the explicit transition from the transient worker to the persistent worker and the
  commands that verify listener privacy, worker health, model visibility, and gateway recovery.
- Add deterministic deployment tests plus shell and systemd-unit validation.

Explicit non-scope:

- No change to gateway runtime, worker selection, task policies, APIs, schemas, credentials,
  application repositories, or installed application configuration.
- No change to the vendor `/etc/systemd/system/ollama.service`, no remote worker bind, and no model
  download, promotion, replacement, or concurrency increase.
- No automatic filesystem mount, `/etc/fstab` edit, login-manager change, package installation,
  firewall change, GPU configuration, or LM Studio token/configuration work.
- No service activation in the reviewable installer. Current-host activation happens only after the
  reviewed revision lands and the existing transient worker is stopped explicitly.

Assumptions and blockers:

- The target uses systemd user services, `/usr/local/bin/ollama` is a root-owned executable not
  writable by group or other, and the intended service user has lingering enabled.
- The model mount may arrive after the user manager starts. The unit must safely retry; guaranteed
  headless availability before login still depends on separately provisioning that filesystem as a
  boot mount.
- The environment file contains no secret. Paths are restricted to an intentionally narrow safe
  character set so the installer never has to source or evaluate operator input.

Verification plan:

- Focused tests inspect the unit and installer boundaries, including positive and negative path,
  mount, executable, lifecycle, and listener controls.
- Run `bash -n` on the installer and `systemd-analyze --user verify` on the shipped unit with a
  temporary valid environment file.
- Probe systemd restart behavior with a disposable user unit whose failing preflight becomes
  successful, proving that `Restart=on-failure` covers pre-start failures.
- Run the full gateway pytest suite, Ruff lint/format, mypy, package build, and whitespace check.
- After merge, install the reviewed user unit, stop the transient worker, enable/start the
  persistent unit, and verify it survives a user-manager restart while remaining loopback-only.

### Acceptance criteria

1. The installed user unit persists outside `/run`, is enabled only by an explicit operator command,
   and uses the fixed reviewed Ollama executable rather than a user-controlled `PATH` lookup.
2. Its effective environment binds only `127.0.0.1:11434`, disables Ollama cloud access, uses the
   configured model mount/directory, and retains the bounded context/load/parallel settings.
3. Missing or non-mounted model storage prevents `ollama serve` from running, creates no model
   directory, and is retried without systemd start-limit exhaustion.
4. The installer rejects root, disabled linger, an unsafe/missing Ollama executable, unsafe path
   syntax, a non-mount model root, a missing model directory, a directory outside the mount, and
   symlinked managed targets before replacing installed configuration.
5. The installer atomically writes mode-0600 environment configuration, installs the reviewed unit,
   reloads the user manager, and contains no service lifecycle or host-mount mutation.
6. The unsafe vendor system service remains disabled and unchanged; the gateway and applications
   require no code or configuration change.

### Implementation summary

- Added a persistent, hardened systemd user unit whose pre-start gates require the configured model
  mount and existing model directory. Failed preflight starts remain retryable without start-limit
  exhaustion; the fixed command uses `/usr/local/bin/ollama` and never a user-controlled `PATH`.
- Added an unprivileged installer that validates the current identity, linger state, user-manager
  availability, root-controlled Ollama executable, mounted storage, path containment, clean checkout,
  and non-symlinked managed paths before atomically replacing the environment and unit files.
- The generated environment preserves the proven loopback-only, cloud-disabled, 8,192-context,
  one-loaded-model, one-parallel-request worker settings. The installer only reloads the user manager.
- Documented the explicit post-review transition and the remaining boot-mount dependency without
  enabling the unsafe vendor system unit or changing `/etc/fstab`.

### Cold diff audit

- `deploy/systemd/local-inference-ollama.service` adds only the private persistent worker lifecycle.
  The fixed executable, environment file, mount/directory preflights, unlimited retry window, and
  user-service hardening trace directly to acceptance criteria 1 through 3.
- `deploy/systemd/install-ollama-user.sh` installs only non-secret user configuration after all
  executable, identity, mount, containment, checkout, and managed-path checks. Atomic replacements
  and `daemon-reload` trace to acceptance criteria 4 and 5; no service lifecycle command exists.
- `tests/test_deployment.py` covers unit ordering/privacy/retry declarations, installer syntax and
  admission controls, fixed environment values, atomic targets, and lifecycle/mount non-mutation.
- `README.md` documents installation, explicit activation, loopback inspection, and the separate
  operator-owned headless boot-mount requirement.
- Boundary probe: unsafe path syntax, a directory that is not a mount point, and a models directory
  outside the configured mount all failed before mutation. Valid current storage reached the clean-
  checkout gate. A disposable transient unit recorded one failed preflight restart and became active
  after its prerequisite appeared, proving the service restart policy covers `ExecStartPre` failure.
- Effect trace: the persistent unit reads the installer's fixed environment; the two preflight
  commands occur before the sole `ollama serve` command; `StartLimitIntervalSec=0` plus
  `Restart=on-failure` keeps a late mount retryable. No gateway or application file changed.

### Gap audit

DONE for the reviewable implementation contract.

- Focused deployment tests reported 10 passed. The full suite reported 206 passed and one intentional
  live-test skip. Ruff lint and formatting, mypy over seven source files, package build, Bash syntax,
  systemd user-unit verification, and whitespace validation passed.
- GitHub review/landing and installing the merged unit on the current host remain landing and
  operational evidence steps, not missing implementation.
