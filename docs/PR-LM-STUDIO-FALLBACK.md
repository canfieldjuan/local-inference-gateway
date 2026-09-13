# Fail-Closed LM Studio Fallback

### Contract

Root cause:

- The gateway's application boundary is runtime-neutral, but `create_app` constructs one
  `OllamaWorker` directly and `Settings.from_env` has no fallback configuration.
- The existing worker class maps a request onto the OpenAI-compatible chat-completions contract,
  but it cannot authenticate to LM Studio, request bounded JIT residency, or distinguish a safe
  pre-dispatch fallback decision from a failure after primary submission.
- The current gateway therefore stops when Ollama is unavailable even though the installed,
  authenticated LM Studio service can serve the same local model artifact.

Required change surface:

1. Preserve Ollama as the primary worker and all existing request reservation, identity, expiry,
   replay, acknowledgement, encryption, authorization, and task-policy behavior.
2. Add optional all-or-none LM Studio fallback configuration: explicit loopback HTTP origin, exact
   model identifier, owner-private bearer-token file, and bounded idle TTL. Applications continue
   to send only gateway task contracts and never select a runtime or model.
3. Share only the existing bounded OpenAI-compatible health/chat transport mechanics. LM Studio
   requests add its bearer token and idle TTL; Ollama remains unauthenticated on loopback.
4. Make task health worker-router aware without exposing worker names, runtime identity, model
   identity, tokens, or capacity details to applications.
5. Select LM Studio only before any Ollama inference submission, only for an explicitly eligible
   task, only when the primary model is unavailable, and only when Ollama's resident-model query
   proves no model owns primary runtime capacity.
6. Once Ollama inference is called, return its definitive, ambiguous, validation, or protocol
   outcome unchanged. Never retry that identity on LM Studio.
7. If primary state, resident capacity, fallback health, token safety, or fallback configuration is
   unknown or invalid, fail closed as worker unavailable.
8. Keep every currently published task fallback-eligible only after the exact shared model artifact
   and each task's existing structured-output validator are exercised through the fallback in the
   operational proof.
9. Update the runbook and service configuration contract, then exercise all four synthetic tasks,
   cross-credential denial, replay, and acknowledgement against LM Studio through the same HTTPS
   gateway proof. Prove the model was JIT-loaded in LM Studio while Ollama remained empty.

Explicit non-scope:

- No application repository change, application-visible runtime/model setting, Connect change, new
  task, schema expansion, queue change, or model prompt change.
- No cloud fallback, vLLM, standalone llama.cpp server, remote worker listener, direct client access
  to LM Studio, or weakening of bearer/TLS boundaries.
- No automatic Ollama process stop, model unload, LM Studio process start, token creation, model
  download, runtime update, certificate change, firewall change, or GPU probing framework.
- No fallback after primary submission, hedged requests, speculative parallel dispatch, automatic
  retry onto another worker, or claim that transport proof alone is a semantic model benchmark.
- No permanent appliance install, administrator UI, Windows proof, installer, or signing.

Assumptions and blockers:

- The gateway is the only process permitted to submit work to its loopback workers while this
  single-GPU decision is made.
- Ollama's local `/api/ps` response is the authoritative resident-model signal for this v0 worker;
  an unavailable, malformed, encoded, oversized, or non-empty response blocks fallback.
- LM Studio JIT loading, single-JIT-model retention, auto-eviction, and authentication are operator
  settings. The gateway verifies authenticated model availability and bounds JIT residency but
  does not mutate those server settings.
- Current-machine evidence shows LM Studio 0.4.16+2 with selected
  `llama.cpp-linux-x86_64-nvidia-cuda12-avx2@2.34.0`, an owner-private token, JIT model visibility,
  and the same 17984491520-byte Q4_K_S GGUF/SHA-256 artifact as Ollama. These are local proof
  inputs, not portable defaults or application-visible identifiers.

Verification plan:

- Focused configuration tests cover absent fallback, complete fallback, partial configuration,
  unsafe origins, unsafe token files, invalid TTL values, and environment parsing.
- Worker boundary tests prove primary-first routing, eligible fallback, noneligible refusal,
  occupied/unknown capacity refusal, authenticated fallback transport, TTL insertion, and no
  fallback after any primary inference call.
- Service tests prove task-specific health without disclosing workers and preserve the complete
  request lifecycle.
- Run full pytest, Ruff lint/format, mypy, package build, lock verification, and diff whitespace.
- Run the existing four-task operational HTTPS proof with a deliberately unavailable primary model,
  empty Ollama residency, authenticated LM Studio JIT, exact shared model identifier, and synthetic
  content only. Inspect only runtime/task/status metadata.

### Acceptance criteria

1. With no LM Studio variables, construction and behavior remain byte-for-byte compatible at the
   public gateway contract and Ollama is the only worker.
2. Partial or unsafe fallback configuration prevents gateway startup before any network request.
3. A healthy Ollama model receives the request and LM Studio is not queried for inference.
4. An unavailable primary model plus an empty canonical `/api/ps` model list permits one eligible
   request to use authenticated LM Studio with the configured TTL.
5. A resident Ollama model, unknown/malformed resident state, unavailable fallback, or noneligible
   task returns worker unavailable without calling LM Studio inference.
6. Any failure raised after `OllamaWorker.infer` begins is returned without LM Studio inference.
7. Application health remains task-scoped and exposes no runtime, model, token, or capacity detail.
8. Existing reservation, replay, expiry, acknowledgement, authorization, and encrypted result
   behavior remain unchanged and the full local suite passes.
9. The live synthetic proof completes all four task contracts through LM Studio, replays and
   acknowledges them, rejects every cross-credential pair, and observes no Ollama-resident model.

### Implementation summary

- Added an optional, all-or-none `LMStudioFallbackSettings` block with loopback-only URL
  validation, an exact model identifier, a bounded idle TTL, and an owner-private bearer-token
  file. With no fallback variables, `create_app` still constructs only `OllamaWorker`.
- Added `LMStudioWorker` on the existing bounded OpenAI-compatible transport. It authenticates with
  the configured bearer token and adds the configured JIT `ttl` to each completion request.
- Added `FallbackWorker` as a pre-dispatch router. Ollama remains primary; fallback is admitted only
  for published tasks when the primary model is proven unavailable, Ollama's canonical `/api/ps`
  response is exactly empty, and LM Studio reports the configured model. Unknown primary health is
  distinct from proven unavailability and fails closed. One monotonic deadline bounds every routing
  probe and the selected inference call, so probe time cannot permit a dispatch after request expiry.
  No exception after an Ollama infer call can route to LM Studio.
- Made health checks task-aware while preserving the existing task-only public response. Worker,
  model, token, and capacity identities remain private to the gateway.
- Documented the optional appliance configuration and added configuration, boundary, routing,
  health, and no-post-dispatch-fallback regression coverage.
- Exercised the existing HTTPS operational proof against the configured LM Studio fallback with a
  deliberately missing Ollama primary model. All four published tasks completed, replayed, and
  acknowledged; cross-credential requests were denied; Ollama stayed empty; and LM Studio unloaded
  the JIT-loaded fallback after its configured TTL.

### Cold diff audit

- `src/local_inference_gateway/config.py` owns fallback admission configuration and reads the token
  without following symlinks or accepting a non-private/non-regular file.
- `src/local_inference_gateway/worker.py` owns runtime selection and shares only payload/result
  validation. The primary is checked first with a tri-state availability result, the fallback
  capacity check is fail-closed, one remaining deadline crosses every probe and dispatch, and there
  is no exception handler around primary inference that could trigger a second dispatch.
- `src/local_inference_gateway/app.py` constructs the router only from a complete optional setting
  and reports availability per authorized task without adding worker identity to the response.
- `tests/` independently exercises the allowed and refused sides of configuration, capacity,
  task eligibility, unknown primary state, routing-deadline exhaustion on both worker paths,
  transport authentication/TTL, task health, and post-primary failure routing.
- `README.md` is the only operator-facing surface changed. No task, request, result, storage,
  authorization, replay, acknowledgement, application, or Connect contract changed.
- Effect trace: the fallback claim is controlled by `FallbackWorker._fallback_ready`; tests prove
  each prerequisite can independently refuse dispatch, and the live proof reaches LM Studio only
  after the real Ollama model and residency probes permit it.

### Gap audit

DONE. The diff implements the contracted optional fallback and operational proof without changing
application-facing contracts or routing after primary submission. Permanent appliance deployment,
Windows/signing, other runtimes, remote workers, and semantic model promotion remain deferred as
declared in non-scope.
