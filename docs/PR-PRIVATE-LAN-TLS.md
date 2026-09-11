# Private-LAN TLS Appliance Listener

### Contract

Root cause:

- The merged gateway accepts only loopback bind addresses and starts Uvicorn without TLS. A second
  workstation therefore cannot use the shared on-prem inference host without either bypassing the
  configuration guard or sending its bearer token and private request content over plaintext HTTP.
- The runnable entrypoint relies on Uvicorn's implicit process defaults. The durable SQLite and
  in-memory worker-lane contract requires exactly one process, so the appliance service must encode
  that topology rather than leave it to operator memory.

Required change surface:

- Preserve loopback HTTP as the default development mode.
- Add an explicit private-LAN mode that accepts one concrete private IPv4 or IPv6 bind address only
  when a TLS certificate and owner-private TLS key are configured as a pair. Reject wildcard,
  public, multicast, unspecified, and non-IP host values.
- Keep the Ollama worker restricted to loopback regardless of gateway listener mode.
- Pass the validated TLS files to Uvicorn, disable proxy-header trust for the direct listener, and
  explicitly run one worker process.
- Add a hardened example systemd unit that runs one installed gateway process, reads configuration
  from an owner-controlled environment file, gives write access only to the gateway state
  directory, and does not expose or generate secrets.
- Document certificate/SAN, firewall, service-account, and client-trust prerequisites without
  mutating the host firewall or certificate stores.

Explicit non-scope:

- No reverse proxy, Caddy, Nginx, Tailscale dependency, public-Internet bind, wildcard bind, TLS
  certificate issuance/renewal automation, firewall mutation, or client certificate authentication.
- No bearer-token provisioning protocol, administrator UI, remote credential rotation, app cutover,
  Connect changes, additional inference tasks, worker fallback, model promotion, or queue redesign.
- No multi-process gateway topology and no direct LAN exposure of Ollama.
- No service installation or host mutation merely to prove the repository artifacts.

Assumptions/blockers:

- The appliance has a stable private address and operator-managed DNS or IP naming.
- The operator supplies a certificate whose SAN matches the client-facing name and installs the
  issuing CA in each client trust store.
- Host firewall policy is operator-owned. The gateway can prevent unsafe bind classes but cannot
  determine which private source devices are authorized.
- Bearer tokens remain distinct per application credential. TLS protects them in transit; the
  existing owner-scoped authorization remains the application boundary.

Verification plan:

- Configuration tests prove loopback HTTP remains accepted; partial TLS, non-loopback plaintext,
  wildcard/public/multicast bind addresses, symlinked TLS files, and group/world-readable private
  keys fail closed; a concrete private address with a regular certificate and owner-private key is
  accepted.
- Entrypoint tests prove Uvicorn receives the exact bind, certificate, key, disabled proxy headers,
  and `workers=1`.
- A focused integration test generates an ephemeral test certificate, starts the real ASGI app over
  HTTPS, and receives the privacy-safe liveness response through the TLS listener.
- Existing Ruff, mypy, lock, unit/integration, package, and live loopback-Ollama gates remain green.
- Static inspection verifies the systemd example starts one process, keeps Ollama loopback-only,
  and contains no credential, key, digest, hostname, or application data.

### Acceptance criteria

1. With no TLS variables and the default loopback bind, current development startup behavior is
   unchanged.
2. A non-loopback listener cannot be configured without both TLS files and a concrete private IP.
3. TLS file checks reject symlinks and unsafe private-key permissions before Uvicorn starts.
4. The real Uvicorn path serves `GET /health/live` over HTTPS with the same non-sensitive envelope.
5. The production example encodes one gateway process and a private writable state directory; it
   does not make Ollama remotely reachable or modify firewall/certificate trust.
6. Existing request identity, encryption, expiry, acknowledgement, worker selection, and task
   contracts are unchanged.

### Implementation summary

- Added paired TLS certificate/key settings and an explicit listener policy: loopback HTTP remains
  the default; non-loopback requires a concrete RFC1918 IPv4 or ULA IPv6 address plus validated TLS
  files. Ollama URL validation remains loopback-only.
- The runnable entrypoint passes the validated TLS paths to Uvicorn, disables proxy-header trust,
  and explicitly selects one worker process.
- Added a hardened single-process systemd example and an operator runbook for certificate SAN,
  client trust, firewall ownership, service state, logs, and TLS liveness inspection.
- Added configuration boundary tests, entrypoint wiring coverage, a real ephemeral-certificate HTTPS
  liveness proof, and a static deployment-artifact guard.

### Cold diff audit

- `src/local_inference_gateway/config.py` owns the only new admission decision: TLS files must be
  paired and safe; non-loopback binds must be concrete RFC1918/ULA addresses with TLS. Existing
  worker URL validation is unchanged and still accepts loopback HTTP only.
- `src/local_inference_gateway/__main__.py` carries the admitted TLS settings to the real Uvicorn
  entrypoint while fixing the topology at one worker and rejecting proxy-header interpretation.
- `deploy/systemd/local-inference-gateway.service` encodes one unprivileged process, owner-private
  state, read-only system paths, and no gateway/worker bind values or secrets.
- `README.md` documents the exact operator-owned prerequisites and safe inspection commands without
  claiming certificate issuance, firewall automation, service installation, or app cutover.
- `tests/test_config.py`, `tests/test_entrypoint.py`, and `tests/test_deployment.py` cover both sides
  of the listener gate, TLS file boundaries, environment wiring, actual HTTPS reachability, and the
  one-process deployment artifact.
- Boundary probe: loopback plaintext and concrete RFC1918/ULA TLS pass; partial TLS, wildcard,
  public, multicast, link-local, documentation-range, hostname, symlink, writable certificate, and
  non-private-key inputs fail before Uvicorn starts.
- Untraced or forbidden changes: none. Request schemas, SQLite lifecycle, task policy, worker
  selection, application repositories, host firewall, certificate stores, and live service state
  did not change.

### Gap audit

DONE

- Focused TLS/deployment tests: 37 passed.
- Full local gate: 106 passed, 1 intentionally skipped live check; Ruff format/lint, mypy, and lock
  validation passed.
- Opt-in loopback Ollama smoke: 1 passed.
- Source distribution and wheel built successfully.
- Installing the service, selecting the appliance hostname/address, supplying its CA-issued
  certificate, installing client trust, and applying firewall policy remain operator deployment
  work by contract, not implementation gaps.
