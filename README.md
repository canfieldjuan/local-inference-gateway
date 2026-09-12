# Local Inference Gateway

A private, on-prem inference boundary for Local Connect applications. Applications submit a
versioned task; the gateway owns worker selection and model identity. This keeps application code
independent of Ollama, LM Studio, GPU placement, and future runtime changes.

Current milestone: authenticated, durable `email.analyze@1`, `email.schedule.extract@1`, and
`document.summary.step@1` lifecycles backed by Ollama, with an explicit TLS-only private-LAN
listener for a single-process Linux appliance. LM Studio fallback, remaining application cutovers,
certificate automation, and public-Internet deployment remain deferred.

## Security model

- Ollama is gateway-private and must listen only on loopback.
- The gateway defaults to loopback HTTP. A LAN listener requires one concrete private IP and a
  configured TLS certificate/key pair; wildcard, public, multicast, and link-local binds fail
  closed. Host firewall policy must restrict the listener to authorized private source devices.
- Run exactly one gateway process. Multiple application credentials share that process; using
  multiple Uvicorn workers or processes against the same SQLite file is not supported by this
  milestone.
- Bearer tokens are stored by clients. The gateway credential file contains only SHA-256 token
  digests and task grants.
- Prompts and worker inputs remain in memory. SQLite stores request metadata and AES-GCM encrypted
  results until the owning client acknowledges durable receipt or the request expires. A scheduled
  maintenance task removes expired ciphertext even while the gateway is otherwise idle.
- Exact retries replay one result. An attempt interrupted after worker submission remains ambiguous
  until expiry because Ollama provides no authoritative request-status lookup.
- Task response schemas accept an object root and a deliberately bounded nested subset:
  scalar/object/array `type`, `properties`, single-schema `items`, explicit capped `maxItems`,
  `required`, boolean `additionalProperties`, scalar `enum`, numeric/string bounds, annotations,
  and one non-nested nullable `anyOf`. The document task additionally admits one root `anyOf` with
  2 through 64 closed object branches. References, nested object choices, open branches, tuple or
  unbounded arrays, regex patterns, and open-ended combinators are rejected before worker dispatch
  so validation cannot monopolize the worker lane.
- Output-token admission is task-specific: the Email Watcher tasks remain capped at 1,500 and the
  document summary step is capped at 4,096. Raising the parser's global ceiling does not grant a
  credential more capacity for another task.
- Worker JSON rejects duplicate object keys and integer or fractional numbers outside the supported
  finite-binary64 range. Schema and output numbers share one exact validation domain, and
  token-limited completions are never persisted as successful results.

The public repository grants no access to a running gateway, its credentials, or private data. No
software license has been selected yet.

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- Ollama listening on an explicit loopback address
- The operator-selected model already installed in Ollama

Install development dependencies:

```bash
uv sync --locked
```

## Private configuration

Create an owner-private directory outside the repository. Generate the client token and result key
without printing either value:

```bash
install -d -m 700 "$HOME/.local/state/local-inference-gateway"
umask 077
python -c 'from pathlib import Path; import secrets; (Path.home() / ".local/state/local-inference-gateway/email-watcher.token").write_text(secrets.token_hex(32), encoding="ascii")'
python -c 'from pathlib import Path; import secrets; (Path.home() / ".local/state/local-inference-gateway/document-summarizer.token").write_text(secrets.token_hex(32), encoding="ascii")'
openssl rand -base64 32 > "$HOME/.local/state/local-inference-gateway/result.key"
sha256sum "$HOME/.local/state/local-inference-gateway/email-watcher.token"
sha256sum "$HOME/.local/state/local-inference-gateway/document-summarizer.token"
```

Copy only the printed digest into an owner-private credential file:

```json
{
  "version": 1,
  "credentials": [
    {
      "id": "email-watcher",
      "token_sha256": "<64-character lowercase SHA-256 digest>",
      "tasks": [
        {"id": "email.analyze", "version": 1},
        {"id": "email.schedule.extract", "version": 1}
      ]
    },
    {
      "id": "document-summarizer",
      "token_sha256": "<different 64-character lowercase SHA-256 digest>",
      "tasks": [
        {"id": "document.summary.step", "version": 1}
      ]
    }
  ]
}
```

Save it as `~/.local/state/local-inference-gateway/credentials.json` and run
`chmod 600` on the file. Never place the token, digest file, result key, or application data in this
repository.

## Run

```bash
export GATEWAY_DATABASE_FILE="$HOME/.local/state/local-inference-gateway/gateway.sqlite3"
export GATEWAY_CREDENTIALS_FILE="$HOME/.local/state/local-inference-gateway/credentials.json"
export GATEWAY_ENCRYPTION_KEY_FILE="$HOME/.local/state/local-inference-gateway/result.key"
export GATEWAY_OLLAMA_URL="http://127.0.0.1:11434"
export GATEWAY_OLLAMA_MODEL="qwen3-30b-a3b:latest"
export GATEWAY_DEPLOYMENT_ID="development-host"
uv run local-inference-gateway
```

Expiry maintenance runs every 30 seconds by default. Operators may set
`GATEWAY_MAINTENANCE_INTERVAL_SECONDS` to a bounded interval between 0.01 and 3600 seconds.

Safe unauthenticated liveness check:

```bash
curl --fail --silent http://127.0.0.1:8787/health/live
```

Authenticated health uses `GET /v1/health` with the client's bearer token. It returns only the
tasks visible to that credential and never identifies the runtime, model, GPU, queue, or other
clients.

## Private-LAN appliance mode

The gateway can listen on one stable private address so several workstations can share the same
inference host. Ollama remains on `127.0.0.1`; only the authenticated gateway is exposed. Before
enabling this mode:

1. Give the appliance a stable private IP.
2. Supply a certificate whose SAN matches the exact DNS name or IP clients use.
3. Install the issuing CA certificate in each authorized workstation's trust store.
4. Restrict the gateway port at the host/network firewall to the authorized private source range.

Set the listener and TLS files in the service environment:

```bash
GATEWAY_BIND_HOST=192.168.1.50
GATEWAY_BIND_PORT=8787
GATEWAY_TLS_CERTIFICATE_FILE=/etc/local-inference-gateway/tls/gateway.crt
GATEWAY_TLS_KEY_FILE=/etc/local-inference-gateway/tls/gateway.key
```

The certificate must be a regular file that is not group/world-writable. The private key must be a
regular owner-private file. Symlinked TLS inputs are rejected on the Linux appliance. The gateway
does not generate certificates, install trust roots, or change firewall rules.

The example unit at `deploy/systemd/local-inference-gateway.service` encodes the supported topology:
one unprivileged service user, one gateway process, a protected state directory, and configuration
under `/etc/local-inference-gateway`. Install the package into the unit's dedicated virtual
environment and keep the database, credential file, and result key under paths readable only by the
service account. Do not add Uvicorn workers or start a second unit against the same SQLite file.

After the operator installs the unit and its private configuration, inspect it without exposing
credentials:

```bash
systemctl status local-inference-gateway --no-pager
journalctl -u local-inference-gateway --since today --no-pager
curl --fail --silent --cacert /path/to/issuing-ca.crt \
  https://gateway.internal.example:8787/health/live
```

The liveness route is intentionally unauthenticated and returns only protocol version plus process
state. Use the existing per-application bearer token for authenticated health and inference.

## Verify

```bash
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv build
```

The real-worker smoke is opt-in, uses synthetic content, and does not qualify or promote a model:

```bash
RUN_OLLAMA_SMOKE=1 \
GATEWAY_OLLAMA_MODEL=qwen3-30b-a3b:latest \
uv run pytest -q -m live tests/test_live_ollama.py
```

### Operational HTTPS proof

After configuring a loopback HTTPS gateway with the two application credentials shown above, run
the network proof with synthetic content only:

```bash
install -d -m 700 /private/path/https-proof
mkcert -cert-file /private/path/https-proof/server.pem \
  -key-file /private/path/https-proof/server-key.pem 127.0.0.1
install -m 0644 "$(mkcert -CAROOT)/rootCA.pem" /private/path/https-proof/gateway-ca.pem
chmod 600 /private/path/https-proof/server-key.pem
export GATEWAY_BIND_HOST=127.0.0.1
export GATEWAY_TLS_CERTIFICATE_FILE=/private/path/https-proof/server.pem
export GATEWAY_TLS_KEY_FILE=/private/path/https-proof/server-key.pem
```

This `mkcert` certificate is for a development-host proof only, not a private-LAN appliance.
Start the gateway with its normal private configuration, then run:

```bash
uv run python scripts/prove_operational_gateway.py run \
  --base-url https://127.0.0.1:8787 \
  --ca-file /private/path/gateway-ca.pem \
  --email-token-file /private/path/email-watcher.token \
  --document-token-file /private/path/document-summarizer.token
```

The command verifies credential-scoped health, forbidden cross-credential task use, all three
current task policies, exact replay, and acknowledgement. It prints statuses, request identities,
and elapsed times; it never prints credentials, prompts, or generated content.

To prove completed-result continuity across an actual gateway restart, use an owner-private state
directory and run the two phases around the restart:

```bash
install -d -m 700 /private/path/restart-proof
uv run python scripts/prove_operational_gateway.py prepare-restart \
  --base-url https://127.0.0.1:8787 \
  --ca-file /private/path/gateway-ca.pem \
  --email-token-file /private/path/email-watcher.token \
  --document-token-file /private/path/document-summarizer.token \
  --restart-state-file /private/path/restart-proof/request.json

# Stop and start the same single gateway service against the same database and encryption key.

uv run python scripts/prove_operational_gateway.py reconcile-restart \
  --base-url https://127.0.0.1:8787 \
  --ca-file /private/path/gateway-ca.pem \
  --email-token-file /private/path/email-watcher.token \
  --document-token-file /private/path/document-summarizer.token \
  --restart-state-file /private/path/restart-proof/request.json
```

This proves the network-process gateway contract, not system-service installation, installed-app UI
acceptance, private-LAN firewall/certificate deployment, or model promotion.

## Failure map

- Configuration startup failure: confirm every required environment variable, owner-only file
  permissions, the exact loopback URL, and installed model identifier.
- `worker_unavailable`: confirm Ollama is running and the configured model appears in its local
  model list; retry with the same request identity.
- `capacity_limited`: honor `retry_after_seconds` and retry the same admitted identity.
- `inference_timeout`: do not create a new identity. The outcome may be ambiguous and blind
  resubmission could duplicate work.
- `request_expired`: create a new request identity only for a new application-level attempt.
- `acknowledgement_conflict`: reconcile the client's durable result state; do not overwrite the
  earlier disposition.

The canonical request lifecycle is in `docs/PR-OLLAMA-PRIMARY-LIFECYCLE.md`. The private-LAN listener
and appliance boundary are in `docs/PR-PRIVATE-LAN-TLS.md`.
