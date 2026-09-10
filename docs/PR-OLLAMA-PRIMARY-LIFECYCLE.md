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
- Serialize expiry, completion, and acknowledgement. Expired requests never dispatch; late worker
  output is discarded. A restart preserves completed replay and acknowledgement state. A restart
  that interrupts an in-flight Ollama attempt exposes an unresolved state and never resubmits it.
- Implement one bounded worker lane and bounded durable admission for this first single-task proof.
  A new request beyond capacity receives a stable retryable error; exact repeats do not consume a
  second reservation.
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

Assumptions/blockers:

- Python 3.12+ is available on the intended Linux inference host.
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
6. Admission is bounded and an exact concurrent repeat joins the original in-process work.
7. Health responses expose only process liveness or the calling credential's task availability;
   they do not expose model, runtime, GPU, queue, or other-client details.
8. Tests, Ruff, mypy, package build/install, and the bounded live Ollama development smoke pass.

### Implementation summary

Pending implementation.

### Cold diff audit

Pending implementation.

### Gap audit

NOT DONE

The contract is written before code. The service, tests, verification, and PR remain to be built.
