# Ollama Primary Request Lifecycle

### Contract

Root cause:

- Email Watcher has a versioned inference-gateway client envelope, but no shared gateway service
  exists. Applications would otherwise keep binding directly to Ollama or LM Studio and would each
  rediscover authentication, admission, idempotency, result retention, and worker selection.
- Ollama's OpenAI-compatible completion API does not provide an authoritative lookup for a request
  after a lost response. Blindly repeating an ambiguous attempt can duplicate inference work.
- The gateway contract requires generated output awaiting application receipt to be durable and
  encrypted, while prompts and worker input remain memory-only.

Required change surface:

- Create a Python ASGI service with unauthenticated `GET /health/live`, authenticated
  `GET /v1/health`, authenticated `POST /v1/inference`, and owner-authenticated
  `POST /v1/inference/{request_id}/ack`.
- Admit only protocol v1 `email.analyze@1` text-to-JSON requests under a bounded task policy. Reject
  runtime/model selection, unsupported requirements, malformed canonical UUIDv4 identities,
  invalid expiries, oversized request bodies, and unauthorized task access before worker dispatch.
- Load owner-private static credential hashes and a separate encryption key from files. Never store
  or log bearer tokens, prompts, generated plaintext, subjects, senders, filenames, or application
  database identifiers.
- Reserve the request identity, owner hash, canonical request digest, immutable expiry, and worker
  attempt identity in SQLite before dispatch. Exact repeats join active in-process work or replay
  the same protected result; conflicting request reuse fails permanently.
- Encrypt completed output with AES-GCM before SQLite persistence. A same-owner acknowledgement
  atomically records `persisted` or `application_rejected`, removes ciphertext, and retains a
  metadata-only tombstone. Conflicting acknowledgement, wrong-owner access, and early
  acknowledgement fail closed.
- Validate generated JSON against the caller's bounded Draft 2020-12 subset before persistence.
  Require an object root and permit only the nested scalar/object constraints needed by
  `email.analyze@1`, including one non-nested nullable union; reject references, regex patterns,
  and open-ended combinators before dispatch.
- Serialize expiry, completion, and acknowledgement. Expired requests never dispatch; late worker
  output is discarded. Scheduled maintenance removes idle expired ciphertext without relying on
  later request traffic. A restart preserves the producing deployment/policy provenance alongside
  completed replay and acknowledgement state. A restart that interrupts an in-flight Ollama
  attempt exposes an unresolved state and never resubmits it.
- Implement one bounded in-process worker lane and bounded durable admission for this first
  single-task proof. A new request beyond capacity receives a stable retryable error; exact repeats
  do not consume a second reservation.
- Add CI, focused unit/integration tests, a loopback-only runnable entrypoint, and concise operator
  documentation. Exercise a development smoke against the already-local Ollama candidate without
  calling that model promoted.

Explicit non-scope:

- No LM Studio fallback, fallback admission, model loading/eviction, or worker switching.
- No Email Watcher, Document Summarizer, Invoice Processor, Local Connect, or Atlas changes.
- No application cutover and no claim that the Qwen profile is promoted before human blind review.
- No client-network exposure, TLS certificate automation, firewall changes, installer, systemd
  deployment, administrator UI, cloud service, multi-tenant SaaS, or external identity provider.
- No document or invoice task, attachment/vision input, arbitrary task registration, model selector,
  chat endpoint, fair multi-task scheduler, or unbounded queue.
- No persistence of prompts or worker inputs and no diagnostic mode that can enable it.
- No retry of an ambiguous in-flight Ollama attempt after gateway restart.
- No multi-process gateway deployment against one SQLite file; the deployment slice must enforce
  the documented single-process service topology before exposing the gateway on a network.

Assumptions/blockers:

- Python 3.12+ is available on the intended Linux inference host.
- This milestone runs exactly one gateway process. Multiple application credentials share that
  process; multiple gateway processes do not share this SQLite lifecycle.
- Ollama remains gateway-private and loopback-bound. Network TLS termination belongs to the later
  appliance-operations slice.
- The exact Qwen artifact is still a candidate until the separate blinded human review is scored.
  Live development execution is evidence for transport/lifecycle only, not model promotion.
- Ollama offers no request-status reconciliation API for OpenAI-style completions. The safe restart
  behavior is therefore explicit unresolved state until expiry, not duplicate dispatch.

Verification plan:

- Unit tests: strict request/credential/config validation, canonical digest stability, encryption
  round trip, and stable error envelopes.
- Integration tests through the ASGI entrypoint: liveness privacy, scoped health, authentication,
  supported inference, exact replay, concurrent exact-repeat join, identity collision, admission
  bounds, wrong-owner isolation, acknowledgement idempotency/conflict, encrypted-at-rest output,
  expiry/late-output serialization, completed-result restart replay, and ambiguous restart behavior.
- Boundary probes: request size limit minus/at/above boundary; expiry past/inside/above policy;
  queue capacity; malformed and boolean protocol/version values; partial credentials; same identity
  with same/different content.
- Static checks: Ruff lint and format check plus mypy.
- Build check: build wheel/sdist and install the wheel into an isolated environment.
- Manual check: development-only ASGI smoke through the configured loopback Ollama candidate, with
  no prompt/output logged and no production-promotion claim.

### Acceptance criteria

1. A valid authenticated `email.analyze@1` request is reserved before exactly one worker call and
   returns the client-compatible completed envelope.
2. An exact repeat before acknowledgement returns the same output without another worker call;
   conflicting reuse of the identity is rejected.
3. SQLite contains only ciphertext for retained generated output and contains no request prompt,
   worker input, bearer token, or generated plaintext.
4. Only the owning credential can replay or acknowledge a request. Acknowledgement deletes retained
   ciphertext and exact-repeat acknowledgement is idempotent; a conflicting disposition fails.
5. Expired requests do not dispatch, late completion cannot persist output, and an interrupted
   in-flight attempt is not dispatched again after restart.
6. Admission is bounded within the single supported gateway process and an exact concurrent repeat
   joins the original in-process work.
7. Health responses expose only process liveness or the calling credential's task availability;
   they do not expose model, runtime, GPU, queue, or other-client details.
8. Tests, Ruff, mypy, package build/install, and the bounded live Ollama development smoke pass.

### Implementation summary

- Added a strict FastAPI boundary for liveness, scoped health, `email.analyze@1` inference, and
  owner-authenticated acknowledgement. Authentication happens before bounded body parsing; task,
  request identity, expiry, and generation-policy checks happen before worker dispatch.
- Added an owner-scoped SQLite lifecycle with transactional admission, attempt identity, exact
  replay, restart ambiguity, scheduled idle expiry cleanup, acknowledgement tombstones, persisted
  producer provenance, and AES-GCM result encryption. Prompts, bearer tokens, and generated
  plaintext are excluded from persistence.
- Added an Ollama adapter that streams bounded identity-encoded responses under an absolute
  cancellable deadline, inserts the configured model only at the private worker boundary, rejects
  non-standard JSON or output that violates the declared bounded Draft 2020-12 subset, and
  separates proven worker unavailability from ambiguous outcomes.
- Added owner-private credential/key loading without symlink following, constant-time token-digest
  comparison, loopback-only URL/bind validation, public-repository CI, operator documentation, and
  an opt-in synthetic real-Ollama lifecycle proof.

### Cold diff audit

- Contract match: every runtime path is part of the single `email.analyze@1` lifecycle. Tests cover
  authentication and disclosure boundaries, exact replay, identity collision, cross-owner access,
  admission, expiry, late output, restart ambiguity, acknowledgement, encrypted retention, bounded
  parsing, configuration boundaries, worker response limits/deadlines, strict finite JSON, response
  schema enforcement, idle scheduled cleanup, producer provenance replay, and wheel installation.
- Effect trace: the client request never contains a model selector; `OllamaWorker` adds the pinned
  configured model only to its loopback worker request. The public health envelope exposes the
  task state but not the model, runtime, GPU, queue, or other credentials.
- Boundary probe: past and exact-now expiries reject while a valid lifetime succeeds; request bytes
  at the limit succeed and one byte over fails; boolean protocol/version values, invalid UUID text,
  encoded worker output, non-finite JSON constants, a never-ending periodic worker response,
  invalid, referencing, regex, or open-ended schemas, schema-invalid generated output, corrupted
  retained ciphertext, missing persisted schema columns, maintenance intervals outside both
  boundaries, symlinked private configuration, conflicting identities, duplicate credentials,
  early/conflicting acknowledgements, and above-capacity admission all fail closed.
- Untraced or forbidden changes: none. No fallback, application repository, Connect contract,
  remote bind, installer, deployment service, or model-promotion state changed.
- Diff size: this is the initial service bootstrap, including the lockfile, runtime, full lifecycle
  test harness, CI, and operator documentation. Splitting those artifacts would leave the new public
  repository with an unreviewable or unverified partial security boundary.

### Gap audit

DONE for the implementation contract.

- `uv run pytest -q`: 75 passed, 1 skipped; the skipped check is the intentionally opt-in live
  worker test.
- `RUN_OLLAMA_SMOKE=1 GATEWAY_OLLAMA_MODEL=qwen3-30b-a3b:latest uv run pytest -q -m live
  tests/test_live_ollama.py`: 1 passed against the installed loopback Ollama model.
- Ruff format and lint passed; mypy reported no issues in 7 source files.
- The source distribution and wheel built, the wheel installed into an isolated Python 3.12
  environment, and the installed package imported successfully.

GitHub CI and reviewer reconciliation remain landing gates, not missing implementation.
