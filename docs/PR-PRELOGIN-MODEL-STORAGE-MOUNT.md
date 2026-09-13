# Pre-login model-storage mount

### Contract

Root cause:

- The persistent Ollama user service is enabled for a lingering user, but its model filesystem is
  currently mounted by the logged-in desktop through `udisks2`. That mount does not exist before
  login, so the worker's fail-closed mount preflight cannot pass after a headless boot.
- The filesystem has no `/etc/fstab` entry or native systemd unit. The durable worker therefore
  exists, but model storage availability still depends on an interactive desktop session.
- Current systemd documentation explicitly prefers native mount units over `/etc/fstab` editing for
  tooling. A generated native unit is the recoverable boundary for this repository.

Required change surface:

- Add one root-run installer that accepts an existing filesystem UUID, current absolute mount point,
  existing model directory, and local owner account.
- Validate the clean reviewed checkout, block-device identity, mounted filesystem type, exact current
  mount, model-directory containment, local owner UID/GID, safe path syntax, absence of conflicting
  `/etc/fstab` entries, and managed-unit path before mutation.
- Generate the mount unit name with `systemd-escape`, pin `What=` to `/dev/disk/by-uuid/<uuid>`, keep
  the current `ntfs3` ownership and safety options, add `nofail` so boot cannot be blocked, and bound
  the mount command with `TimeoutSec=`.
- Atomically install the mode-0644 native mount unit and reload the system manager. The installer may
  not start, stop, enable, disable, mount, unmount, or edit `/etc/fstab`.
- Document the explicit post-review enablement and verification path, including the relationship to
  the already-enabled lingering user worker.
- Add deterministic deployment tests plus Bash and systemd unit verification.

Explicit non-scope:

- No gateway, task, model, application, database, API, schema, credential, or inference-policy change.
- No `/etc/fstab` edit, filesystem formatting, partitioning, UUID change, data move, directory
  creation on the model disk, package installation, or boot/reboot command.
- No change to the persistent Ollama user unit, unsafe vendor system Ollama unit, gateway service,
  desktop `udisks2` configuration, or LM Studio fallback.
- No automatic lifecycle action in the reviewable installer. Installation and enablement happen only
  from the merged revision and remain distinct commands.

Assumptions and blockers:

- The target is a systemd host with an existing `ntfs3` filesystem already mounted at the intended
  path and containing the configured Ollama model directory.
- The existing mount owner is a local account with stable numeric UID/GID. This slice preserves that
  ownership model rather than changing storage sharing policy.
- A full machine reboot is intentionally not performed by the implementation or tests. Exact boot
  acceptance remains an explicit operator action after the reviewed unit is installed and enabled;
  deterministic evidence must prove the native unit is enabled from `local-fs.target`, uses `nofail`,
  and the lingering worker remains enabled.

Verification plan:

- Expected fail-before evidence: the current mount reports `uhelper=udisks2`, its native mount unit
  has no fragment, and `findmnt --fstab` has no matching entry.
- Focused tests inspect syntax, root admission, checkout/device/mount/owner/containment/conflict gates,
  exact generated unit properties, atomic installation, and absence of mount/service lifecycle or
  `/etc/fstab` mutation.
- Render the reviewed template with deterministic fixture values and verify the resulting native unit
  without modifying `/etc`; source-level assertions cover the fail-closed installer gates whose real
  inputs are host-owned block devices and system configuration.
- Run `systemd-analyze verify` against the rendered unit, the full pytest suite, Ruff lint/format,
  mypy, package build, Bash syntax, and whitespace validation.
- After merge, run the installer against the observed UUID and paths, inspect the installed unit,
  enable it explicitly, and verify systemd reports it enabled, loaded, active at the current mount,
  and wanted by `local-fs.target`; verify the lingering Ollama worker and authenticated gateway remain
  healthy. Do not reboot without a separate explicit operator command.

### Acceptance criteria

1. The installer derives a mount-unit filename from the exact mount path and pins the unit to the
   verified existing block device through its filesystem UUID.
2. The installed unit uses `Type=ntfs3`, preserves the observed owner UID/GID and non-privileged
   mount options, includes `nofail`, and bounds the mount operation without altering model data.
3. Missing, mismatched, unmounted, unsafe, symlinked, non-local-owner, or fstab-conflicting inputs fail
   before the managed unit is replaced.
4. The installer consumes only a clean captured Git revision, installs atomically with root ownership
   and mode `0644`, reloads systemd, and performs no activation, mount, unmount, or fstab write.
5. README operations make installation, inspection, explicit enablement, current-session health, and
   the still-separate real reboot proof unambiguous.
6. Gateway/application behavior and the existing loopback-only worker boundary remain unchanged.

### Implementation summary

- Added a reviewed native mount-unit template whose `nofail` option keeps normal boot independent of
  model storage, while a 30-second mount timeout and read-write-only behavior fail clearly.
- Added a root installer that validates the live UUID-backed `ntfs3` mount, owner identity, current
  safety/ownership options, model containment, fstab conflicts, clean source revision, and managed
  unit ownership before rendering the template from the captured commit.
- The installer validates the rendered unit under its path-derived filename, atomically installs it
  with root ownership and mode `0644`, and reloads systemd without changing mount or service state.
- Documented discovery, reviewed installation, explicit enablement, and the separate maintenance-
  reboot acceptance boundary.

### Cold diff audit

- `deploy/systemd/local-inference-model-storage.mount.in` contains only the UUID/path/owner
  placeholders and the native `ntfs3`, `nofail`, bounded, read-write mount contract.
- `deploy/systemd/install-model-mount.sh` implements the declared root, path, local-owner, block-
  device, live-mount, fstab, clean-checkout, reviewed-template, managed-target, verification, and
  atomic-install gates. It contains no mount, unmount, lifecycle, or fstab-write command.
- `tests/test_deployment.py` renders and verifies the template, checks both non-root rejection and
  installer admission/lifecycle invariants, and asserts the explicit root-path guard precedes the
  generic syntax guard.
- `README.md` replaces only the unresolved boot-mount note with the reviewed native-unit operation
  and explicitly withholds real reboot acceptance until a separate operator maintenance action.
- `docs/PR-PRELOGIN-MODEL-STORAGE-MOUNT.md` is this slice's pre-code contract and final audit.
- Boundary probe: malformed UUID, root mount, model directory outside the mount, root owner, and an
  absent UUID were rejected before mutation. Valid current-host inputs reached the clean-checkout
  gate and stopped because the implementation was still uncommitted.
- Effect trace: the installer derives the systemd mount-unit filename from the canonical live path,
  renders `What=` from the verified UUID, and installs the unit wanted by `local-fs.target`; `nofail`
  is the systemd control that prevents the mount from delaying or failing normal boot.

### Gap audit

DONE for the reviewable implementation contract.

- Focused deployment tests reported 12 passed; the full suite reported 208 passed and one intentional
  live-test skip. Ruff lint and formatting, mypy over seven source files, package build, Bash syntax,
  rendered-unit verification, boundary probes, and whitespace validation passed.
- GitHub review/landing, installing and enabling the merged unit, and observing the next separately
  authorized maintenance reboot remain landing/operational evidence rather than missing PR behavior.
