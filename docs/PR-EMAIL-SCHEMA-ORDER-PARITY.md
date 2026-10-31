# Email decoder order parity

## Contract

### Root cause

Email worker serialization reuses sorted canonical JSON. JSON Schema property
order influences constrained generation: the isolated four-arm ablation reproduced
all saved direct/gateway differences by changing only property order. Dictionary
equality hid the change. Initial sorted dispatch originates in `872fba9`; the
passage-schema correction `5df8119` retained it for ordinary email schemas.
The source owner is `contracts.py` decoder-order policy, consumed at the final
`worker.py` request serialization. Canonical request identity is a separate owner.

### Validated inventory

Evidence aliases: `field-order-ablation-report` SHA256
`12fb2e4f5d2eca4a252c587ab20462a5c1576ad88d6127bd2fac8328969fa560`;
public manifest `8fd7aa87def8ec9531b0e367589ef69f9e646af2791fabf8d4f29f0559979268`;
private manifest `5df29a02eaf2f9f1ff49c9e02efe43f613aefb8935ae35a409ab1260619432f8`.
Artifacts live outside worktrees. The frozen `prepare.py`, `run.py`, `analyze.py`
and private jobs/requests/responses are the proof specification, not a prompt to
redesign. App source is `7338985b2a7501a3a4e3513457396601a8dd4b23`.

- Model: `hf.co/Canfield/Qwen3.5-9B-Q4_K_M-GGUF:latest`, weights
  `cd76ec205963b3b33350093e6904d9de16c4e666fd104e1f632d25c7f15f2a13`.
- Ollama0.24.0 binary `b2e45ade9cb754a079f74645e1183d613f582d98f7354b05f4f9a5bd81f8e0c9`;
  closed-think template `790aae76aa0a2bbdb8bde833cf46a7169825353d3bc3ebafa174d7f6fb6a3980`.
- Context32768, top_k1, top_p1, presence_penalty0; request temperature0.1,
  max_tokens500, no seed, stream false. Exact prompts and inputs are the frozen
  post-fix requests for both email corpora; do not print or commit them.
- 22 fixtures, three repetitions, four variants:264 valid calls, every variant
  stable. Order-only changed21 fixtures; name-only changed none. Controls matched
  all66 saved direct and66 saved gateway complete parsed JSON answers.
- Validated direct field order: category, priority, summary, action_required,
  suggested_action, deadline_text, deadline_iso, confidence. Its `required`
  array has exactly that same sequence and contains every field.

### Required change surface

Extend the existing decoder-order policy to exactly `email.analyze@1`. Reuse
required-first order, then sorted optional fields, recursively through admitted
schema properties/items/alternatives. One named policy in `contracts.py` must
control both decoder projection and disabling final transport sorting in
`worker.py`. Keep passage schemas on their existing policy. Remove the worker's
passage-only policy check rather than adding a second serialization layer.

Use synthetic wire-level tests for Ollama and inherited LM Studio behavior,
reordered input dictionaries, nested fields, optional fields, missing/empty
required arrays, and unchanged sibling tasks. Prove equal canonical identities
produce equal decoder bytes for reordered input mappings; a changed required
sequence must change identity and decoder order. Preserve exact-number encoding,
validation, fallback and durable replay semantics.

### Differences from the validated experiment

- The production-ready implementation derives order from the explicit required
  arrays, rather than trusting dictionary insertion order. This reuses the
  gateway's existing supported mechanism and preserves canonical retry identity.
  It yields the exact frozen direct order because all its properties are required
  in that sequence. Tests cover optional/unordered inputs; no corpus redesign.
- Calls pass through corrected `OllamaWorker.infer`, including schema admission,
  request serialization and output validation, through an isolated in-process
  production ASGI app rather than posting the frozen OpenAI payload directly.
  Temporary credentials and encrypted state belong only to the experiment; fresh
  request IDs and expiry timestamps replace old envelope metadata. Acknowledge
  each saved completion. The API transport is in-process (not TLS deployment). Compare emitted bytes structurally and by field order
  before live dispatch; runtime parameters/messages/schema values must match.
- Retain gateway schema name `email_analyze_v1`; the ablation proved it inert on
  these fixtures. Keep other JSON object-key ordering separate from schema
  property order; prove the actual outgoing request.
- Replay uses current gateway main plus this fix, not the older installed
  snapshot. Inventory source differences and do not deploy it. Use the installed
  Python environment with isolated source imports for live worker execution.
- Reuse the ablation's isolated-server guard, model pins and frozen corpus scorer;
  reduce to66 corrected-worker calls, three repeats, no retries. No new model,
  prompt, schema, cap, seed, fixture or oracle experiments.

### Explicit non-scope

No production deployment/restart, database/credential/config changes, canonical
identity migration, task/version addition, model promotion, wider schema language,
capacity change, retry, prompt tuning, schema requiredness/date-length change,
or qualification waiver. Scheduling, invoice and non-passage document requests
keep existing transport behavior. Existing stored results retain replay behavior;
new jobs after any future deployment require distinct IDs/deployment provenance.
Neither ordering is declared universally better.

### Assumptions and blockers

Operator's Continue authorizes implementation and isolated parity proof. Production
remains untouched. GPU admission is a fresh one-shot evaluation-lock/ownership/RAM
check; stop without automatic retry if another owner or resource floor blocks it.
An exact-head independent review remains required for landing; no subagents.

### Verification plan

Baseline local gate before edits:283 passed,2 intentional live skips; Ruff lint/
format and mypy passed. Declare the new wire-order probe before its expected
failure; preserve the failure outside worktrees. Then run the regression, worker,
contract/passage/replay callers, required local gate and package build. Use a
cold diff and effects/identity audit before publication.

For live parity, reuse the bounded runner: loopback-only private server, private
HOME/model metadata, verified blob links, existing evaluation lock, unknown-GPU-
owner STOP,20GiB admission RAM,11000MiB free GPU,4GiB live RAM/disk floors,
120s per request,1200s total,66-call cap. Retain raw exact outgoing requests and
responses privately, including any failure. Compare every complete parsed output
and app-validated result to the frozen direct control; require66/66. Preserve
existing benchmark failures. Verify owned process cleanup, production pins and
source hashes afterward. No inference retry after a guard STOP.

## Implementation summary

One shared decoder-order policy now includes exactly email.analyze@1. Both worker
projection and transport consume it. The passage-only worker check is replaced;
canonical hashing, schema admission and stored replay code are unchanged. Tests
exercise actual streamed wire requests for both adapters, nested schemas,
reordered mappings, explicit order identity, optional fields and unchanged
scheduling/invoice transport. README documents the policy and rollout boundary.

## Cold diff audit

- contracts.py: uses_decoder_field_order owns task/schema selection; decoder_schema
  reuses the existing required-first recursive projection. No new parser or schema
  admission rule, no mutable request state.
- worker.py: _payload and _infer use that owner for projection/final serialization;
  unchanged routing, capacity, cancellation and output validation.
- test_email_decoder_order.py: fail-before/pass-after real wire assertions,
  canonical identity invariance, changed-required identity, optional/missing order,
  input non-mutation, nested objects/arrays/nullable branches, sibling task bytes.
- README.md: documents precise order policy and no deployment.
- This contract: pre-code inventory, declared departures and honest partial gate.

boundary-probe: intended email task follows required order; scheduling/invoice
retain sorted bytes; missing/empty/partial required arrays produce explicit stable
optional order; reordered dictionary inputs retain identity and exact wire bytes.
Existing passage/contract/admission tests remain green. No cap or validator changed.

effect-trace: frozen email decoder order | shared decoder policy plus final worker
serializer |4 wire regressions fail before/pass after;66 offline actual worker
captures match required order;5 live completed controls match, remainder blocked.

## Gap audit

NOT DONE for the complete parity/landing slice.

Locally verified:123 focused tests;294 full-suite passes with2 intentional live
skips; Ruff lint/format, mypy and package build pass. A test-only cold-audit
strengthening then passes all11 new tests; runtime source remains unchanged.
Offline real-ASGI capture passes66/66 requests, validations and acknowledgements.
Live replay stopped after5 matching completions on6 offers when another GPU
process appeared and the ownership guard failed closed on unreadable ancestry.
The in-flight API request returned504; owned processes were cleaned up. Preserve
this STOP receipt and do not retry automatically. Full66/66 live parity,
independent exact-head review and CI remain required. Production is unchanged.

Durable diagnostic report alias: `email-schema-order-parity-partial-report`,
SHA256 `7cd548f03cbe30a961277186ff56e83e040f7fd1e3b3222804702efa95b35070`.
The private raw packet and public source/pin/STOP receipts are outside worktrees;
no private input/output is included in this repository.
