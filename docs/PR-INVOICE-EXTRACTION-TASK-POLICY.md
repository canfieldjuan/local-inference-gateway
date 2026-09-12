# Bounded Invoice Extraction Batch Task Policy

### Contract

Root cause:

- Invoice Processor already isolates model calls behind `ModelRuntime`, but its production runtime
  calls an OpenAI-compatible Ollama endpoint directly. The shared gateway cannot replace that
  boundary because it has no invoice task policy or independently scoped Invoice Processor
  credential.
- One `invoice.extract` Connect job may partition a PDF catalog into several model calls. The
  gateway task therefore represents one extraction batch, not the application capability or the
  whole invoice workflow.
- Invoice Processor currently reserves up to 12,288 output tokens for its bounded extraction
  schema. The gateway parser and existing task policies stop at 4,096, so an honest client cannot
  preserve the current model contract by configuration alone.
- Invoice Processor's Pydantic schema contains local `$defs` and `$ref` references. The gateway
  deliberately rejects references. The coordinated client must project that closed schema to the
  gateway's existing bounded wire subset; widening the gateway schema language is unnecessary.

Required change surface:

- Add exactly one inference task, `invoice.extract.batch@1`, representing one model-assisted batch
  inside Invoice Processor's application-owned deterministic pipeline. It is distinct from the
  Local Connect capability `invoice.extract`.
- Raise the shared syntactic output-token ceiling to 12,288, then grant that ceiling only to the
  invoice task through the immutable per-task policy. Preserve the Email Watcher limits at 1,500
  and Document Summarizer's limit at 4,096.
- Require temperature 0.0 and the existing structured JSON object response contract for the invoice
  task. Do not enable root object choices or any new JSON Schema keyword.
- Report the task only to a credential explicitly granted `invoice.extract.batch@1` and route it
  through the unchanged durable reservation, single worker lane, encrypted result, replay, expiry,
  acknowledgement, and worker-output validation lifecycle.
- Document a separate Invoice Processor token and task grant without committing credentials.

Explicit non-scope:

- No Invoice Processor source, prompt, schema, database, Connect provider, desktop host, or ledger
  behavior change in this PR.
- No direct-runtime removal, application fallback policy, client retries, client request identity,
  application acknowledgement, installed-app proof, or customer-invoice inference.
- No LM Studio or vLLM worker, model promotion, concurrency change, queue change, deployment change,
  remote-listener change, certificate automation, or credential-file mutation.
- No `$ref`, `$defs`, new JSON Schema composition, higher limits for existing tasks, or generic
  OpenAI-compatible pass-through endpoint.

Assumptions/blockers:

- Current Invoice Processor `origin/main` is authoritative: every model call handles one extraction
  batch, uses temperature 0.0, requires structured JSON, and reserves at most 12,288 output tokens.
- The coordinated Invoice Processor client contract will own wire-schema projection, stable request
  identity, response validation, durable receipt acknowledgement, and rollout fallback. This policy
  PR does not claim application cutover.
- No operator decision blocks this task-policy landing unit.

Verification plan:

- Add service tests proving an Invoice Processor credential sees and invokes only
  `invoice.extract.batch@1`, while Email Watcher and Document Summarizer credentials cannot invoke
  it and the invoice credential cannot invoke their tasks.
- Probe task output-token boundaries at 12,288/12,289 and confirm rejected work never reaches the
  worker. Reconfirm the existing 1,500/1,501 and 4,096/4,097 boundaries.
- Prove a successful invoice batch uses the existing result/replay/acknowledgement lifecycle with
  task-policy provenance and one worker call.
- Run full pytest, Ruff lint/format, mypy, package build, and whitespace validation.

### Acceptance criteria

1. Authenticated health returns `invoice.extract.batch@1` only for an independently configured
   Invoice Processor credential.
2. `invoice.extract.batch@1` admits exactly temperature 0.0, structured JSON object output, and no
   more than 12,288 output tokens; 12,289 is rejected before worker dispatch.
3. Existing Email Watcher and Document Summarizer task ceilings and schema permissions remain
   unchanged, settled by their existing boundary tests plus explicit cap assertions.
4. An invoice batch completes, exact replay does not repeat worker work, and owner acknowledgement
   removes the retained result through the existing lifecycle.
5. Cross-credential use in both directions fails before dispatch.
6. README configuration creates a separate token and grants only the invoice batch task; it does
   not expose secrets or claim an application cutover.

### Implementation summary

Not implemented. This commit establishes the independently reviewable behavioral contract.

### Cold diff audit

- This revision adds only the task-policy contract. Runtime code, tests, configuration examples,
  application repositories, and deployment artifacts are unchanged.
- Untraced or forbidden changes: none.

### Gap audit

NOT DONE. Runtime policy, tests, documentation, verification, application client, and installed-app
evidence remain to be implemented under their respective contracts.
