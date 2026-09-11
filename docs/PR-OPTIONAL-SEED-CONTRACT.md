# Backward-compatible gateway seed contract

### Contract

Root cause:

- Gateway protocol v1 omits the application generation seed, so a consumer cannot preserve its
  run-derived reproducibility contract through the shared gateway.
- Adding a required field or including an absent optional field in the canonical digest would make
  current Email Watcher requests conflict with durable identities admitted before an upgrade.

Required change surface:

- Add one optional strict integer `generation.seed` field bounded from zero through signed 64-bit
  maximum.
- Include the seed in canonical identity and worker dispatch when present.
- Exclude an absent seed from canonical serialization so existing request bytes and durable digests
  retain their protocol-v1 meaning.
- Prove that absent, zero, maximum, changed, boolean, negative, and above-maximum forms obey those
  boundaries and that the private Ollama request receives the exact admitted value.

Explicit non-scope:

- No new task, task-policy change, runtime selection, fallback, application change, deployment
  change, model promotion, request state change, or protocol-v2 design.
- No seed default. Existing clients that omit the field retain their current gateway behavior.

Assumptions/blockers:

- Ollama documents `seed` as a supported `/v1/chat/completions` request field for reproducible
  output.
- Signed 64-bit maximum matches Document Summarizer's existing direct-runtime admission boundary.

Verification plan:

- Focused contract tests prove backward-compatible absent-field digest stability and both seed
  boundaries.
- Worker tests inspect the exact private Ollama request for absent and present seeds.
- Run full pytest, Ruff lint/format, mypy, package build, and diff whitespace checks.

### Acceptance criteria

1. A request without `generation.seed` validates and has the same canonical digest as the existing
   protocol-v1 shape.
2. Seeds zero and signed 64-bit maximum validate; booleans, negatives, and maximum plus one fail.
3. Two otherwise identical seeded requests with different values have different canonical digests.
4. The worker omits `seed` for an old request and transmits the exact integer for a seeded request.
5. Existing request lifecycle, identity, expiry, acknowledgement, authorization, and task-policy
   behavior remains green.

### Implementation summary

- Added optional strict `generation.seed` admission through signed 64-bit maximum.
- Canonical request hashing excludes only absent optional fields, preserving the pinned digest for
  the existing unseeded protocol-v1 request while binding every present seed value.
- The private Ollama adapter omits the field for old callers and forwards the exact admitted seed
  for new callers.

### Cold diff audit

- `contracts.py` is the single admission and identity choke point: it validates the optional seed
  and uses `exclude_none=True` so old request identity is byte-semantically unchanged.
- `worker.py` adds only the admitted non-null seed to the private OpenAI-compatible Ollama request.
- Contract tests pin the pre-change unseeded digest and probe absent, zero, maximum, changed,
  boolean, negative, and maximum-plus-one values. The worker test observes both omitted and exact
  transmitted forms.
- No app route, task policy, store, acknowledgement, authorization, expiry, configuration,
  deployment, or application repository changed.
- Untraced or forbidden changes: none.

### Gap audit

DONE for the bounded compatibility contract.

- Focused contract/worker proof: 70 passed.
- Full local pytest: 137 passed and 1 intentionally skipped live test.
- Ruff lint passed; Ruff format reported 24 files already formatted.
- Mypy reported no issues in 7 source files.
- Source distribution and wheel built successfully.
- Live Ollama execution is not required for this compatibility slice; the exact worker request is
  asserted through the production adapter boundary and Ollama documents `seed` for this endpoint.
