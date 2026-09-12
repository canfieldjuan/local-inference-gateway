# Operational Ollama-primary proof

### Contract

Root cause:

- The gateway lifecycle and application task policies are merged, but this host has no installed
  gateway unit and no private gateway state. The existing live test constructs the ASGI application
  in-process and exercises only `email.analyze@1`; it does not prove authenticated HTTPS transport,
  distinct application credentials, process restart, or the document task over the public listener.
- The installed Ollama primary and `qwen3-30b-a3b:latest` are available on loopback. Both current
  application gateway clients require authenticated HTTPS, so a direct HTTP smoke is not an
  application-compatible operational proof.

Required change surface:

- Add one bounded operator command that connects to an already-configured HTTPS gateway using
  owner-private credential and CA files.
- Exercise credential-scoped health, `email.analyze@1`, `email.schedule.extract@1`, and
  `document.summary.step@1` with synthetic content only.
- Prove exact replay uses the same request identity, acknowledgement succeeds, and the command does
  not print bearer tokens, prompts, or generated output.
- Support a two-phase restart proof: submit and retain one completed result, then replay and
  acknowledge that same identity after the operator restarts the gateway process.
- Prove the email credential cannot invoke the document task and the document credential cannot
  invoke an email task.
- Document the exact ephemeral HTTPS setup and proof command without committing private material.

Explicit non-scope:

- No gateway runtime, request schema, task policy, worker, database, encryption, or application
  behavior changes.
- No LM Studio fallback, vLLM worker, Invoice Processor task, public-Internet listener, certificate
  automation, firewall mutation, or model promotion claim.
- No installed Email Watcher or Document Summarizer UI acceptance in this PR. Their current client
  code is the next vertical after the shared HTTPS endpoint is proven.
- No dependence on the unsafe inactive Ollama system unit configuration. The proof uses the
  currently running loopback-only, cloud-disabled Ollama process and does not widen its listener.

Assumptions and blockers:

- `qwen3-30b-a3b:latest` remains installed and healthy on the current loopback Ollama worker.
- The proof gateway binds only to loopback and uses a temporary CA/certificate trusted explicitly
  by the proof client. Private-LAN client acceptance requires a separate stable address, firewall,
  and certificate deployment.

Verification plan:

- Focused tests cover argument and file validation, scoped-health validation, success/replay/ack,
  restart-state handoff, forbidden cross-scope invocation, and output redaction.
- Run Ruff, mypy, the full existing pytest suite, and package build.
- Execute the proof against a real temporary HTTPS gateway backed by the installed Ollama model.
- Stop and restart the gateway between the retained submit and reconcile phases; reconcile must
  return the original completed result before acknowledgement.

### Acceptance criteria

1. The loopback HTTPS listener reports liveness and credential-scoped task health without exposing
   the worker, model, other credentials, or customer content.
2. Synthetic requests for all three current task policies complete through the real Ollama worker,
   replay under their original identity, and acknowledge successfully.
3. Cross-credential task attempts return `forbidden` and do not become successful work.
4. A completed, unacknowledged request survives gateway process restart, replays under the same
   credential and identity, and is then acknowledged.
5. Proof output contains only task identifiers, phase/status, elapsed timing, and request identity;
   it contains no token, prompt, generated output, or private file contents.
6. Existing gateway tests and static/build gates remain green because the operational tool changes
   no runtime behavior.

### Implementation summary

- Added one loopback-HTTPS-only proof command with owner-private credential validation,
  credential-scoped health checks, synthetic requests for all three current task policies,
  forbidden cross-scope checks, exact replay, acknowledgement, and two-phase restart reconciliation.
- Added focused fixture tests for the full task path, restart identity reuse, unsafe endpoint and
  credential rejection, and output redaction.
- Documented the normal proof and restart procedure without embedding private paths or material.
- Exercised the command against the installed `qwen3-30b-a3b:latest` Ollama worker through a real
  loopback HTTPS Uvicorn listener. All task requests, exact replays, acknowledgements, credential
  isolation, and retained-result restart reconciliation succeeded.

### Cold diff audit

- Contract match: the new command and tests are operational clients only; gateway runtime, task
  policies, storage, worker selection, and application repositories are unchanged.
- Effect trace: the proof constructs application-scoped public request envelopes, sends them through
  the TLS listener, validates the returned synthetic JSON without printing it, and acknowledges
  only after validation. The restart phases persist the exact synthetic request before submission
  and reuse it after a process restart.
- Boundary probe: plain HTTP and a remote authority reject; public credential permissions reject;
  distinct credentials are required; cross-task authorization rejects; valid HTTPS, credential,
  replay, acknowledgement, and restart paths pass.
- Untraced or forbidden changes: none identified. LM Studio fallback, worker changes, model
  promotion, application UI, private-LAN deployment, and firewall policy remain untouched.

### Gap audit

DONE for the network-process gateway operational contract. System-service installation and
installed Email Watcher and Document Summarizer acceptance remain later vertical slices.

- Focused proof tests passed; the full suite passed with one intentional live-test skip. Ruff lint
  and format, mypy, package build, and whitespace validation passed.
- The final real HTTPS run completed all three task policies, exact replay, credential isolation,
  and acknowledgement. A fresh gateway process replayed and acknowledged the retained pre-restart
  document result under the same request identity.
