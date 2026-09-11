# Agent operations

Start with `README.md`, then read the active contract under `docs/` and run the local gate shown
there before changing runtime code. Current code and exercised commands are ground truth.

Security boundaries:

- Never commit or print bearer tokens, credential files, encryption keys, prompts, model output,
  or application data.
- Keep Ollama private to the gateway and loopback-bound.
- Keep the gateway loopback-bound until the separately reviewed TLS/network deployment slice.
- Preserve stable request identity, encrypted result replay, expiry, and acknowledgement cleanup.
- Do not add a new task, runtime fallback, remote bind, or application cutover as incidental work.

If you spend meaningful time discovering a reusable operational procedure, encode it in the
README, the active contract, or a focused runbook and verify it before finishing.
