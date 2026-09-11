# Local Inference Gateway

A private, on-prem inference boundary for Local Connect applications. Applications submit a
versioned task; the gateway owns worker selection and model identity. This keeps application code
independent of Ollama, LM Studio, GPU placement, and future runtime changes.

Current milestone: one authenticated, durable `email.analyze@1` lifecycle backed by Ollama.
LM Studio fallback, application cutover, and network-appliance deployment are intentionally not in
this first slice.

## Security model

- Ollama is gateway-private and must listen only on loopback.
- This milestone's gateway listener is also loopback-only. It is a development/service proof, not
  yet a LAN endpoint; TLS termination and firewall policy land separately.
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
openssl rand -base64 32 > "$HOME/.local/state/local-inference-gateway/result.key"
sha256sum "$HOME/.local/state/local-inference-gateway/email-watcher.token"
```

Copy only the printed digest into an owner-private credential file:

```json
{
  "version": 1,
  "credentials": [
    {
      "id": "email-watcher",
      "token_sha256": "<64-character lowercase SHA-256 digest>",
      "tasks": [{"id": "email.analyze", "version": 1}]
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

The canonical lifecycle and non-goals are in `docs/PR-OLLAMA-PRIMARY-LIFECYCLE.md`.
