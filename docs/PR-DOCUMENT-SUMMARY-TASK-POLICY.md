# Bounded Document Summary Step Task Policy

### Contract

Root cause:

- Document Summarizer already isolates inference behind `ModelRuntime`, but its production factories
  still construct application-owned Ollama or llama.cpp runtimes. The gateway cannot replace that
  boundary yet because it advertises only Email Watcher tasks.
- The current gateway contract caps every request at 1,500 output tokens. Current Document
  Summarizer requests require up to 4,096 tokens, while Email Watcher must retain its smaller bound.
  Raising one global effective cap would silently widen every task instead of expressing the actual
  per-task requirement.
- Current Document Summarizer structured outputs include closed, bounded root `anyOf` choices whose
  branches are objects. The gateway admits only an object root or a nullable union below it, so those
  requests fail before dispatch even though branch count, schema size, depth, arrays, enums, and
  generated output are already bounded.

Required change surface:

- Add exactly one inference task, `document.summary.step@1`, representing one model-dependent step
  inside Document Summarizer's application-owned pipeline. It is deliberately distinct from the
  Local Connect capability `document.summarize`; Connect discovery and model transport remain
  separate contracts.
- Make maximum output tokens an immutable per-task policy field. Preserve 1,500 for both Email
  Watcher tasks and admit up to 4,096 only for `document.summary.step@1`.
- Admit a root `anyOf` only when it contains 2 through 64 closed object branches. Each branch must
  pass the existing keyword, depth, node, enum, string, numeric, bounded-array, and nullable-union
  checks. Nested non-null choice unions, open branches, scalar branches, and more than 64 choices
  remain rejected.
- Permit root object choices at dispatch only for the document task. Email tasks retain their
  existing object-root policy even though the shared request parser can safely parse the bounded
  form before task authorization.
- Preserve the same credential scoping, durable reservation, one worker lane, encrypted result,
  exact replay, expiry, acknowledgement, and output validation lifecycle for the new task.

Explicit non-scope:

- No Document Summarizer source change, gateway client, database migration, request identity design,
  prompt change, UI change, model-profile change, or Connect change in this PR.
- No direct application runtime removal, LM Studio fallback, new worker, model promotion, appliance
  provisioning, credential-file mutation, or live customer-document inference.
- No arbitrary JSON Schema composition. `oneOf`, `allOf`, nested object-choice unions, open choice
  branches, references, regexes, tuple arrays, unbounded arrays, and unrestricted output sizes stay
  rejected.
- `uniqueItems` and decoder-incompatible high string ceilings remain an application wire-projection
  concern because the current Document Summarizer runtime already treats its own deterministic
  result validator as authoritative for those keywords.

Assumptions/blockers:

- Current Document Summarizer main is authoritative: its largest declared output limit is 4,096;
  its bounded structural synthesis choice can contain up to 64 closed object branches; and its
  `ModelRuntime` remains the future adapter seam.
- The coordinated Document Summarizer client slice must persist stable gateway request identities
  before handoff and prove every production output schema crosses the gateway wire projection. This
  task-policy slice does not claim that application cutover.

Verification plan:

- Contract boundary tests cover output-token limits at 1,500/1,501 for Email Watcher and
  4,096/4,097 for Document Summarizer without dispatching rejected work.
- Schema tests admit root object choices at 2 and 64 branches and reject 1, 65, scalar, open,
  mixed-root, and nested object-choice forms before dispatch.
- Service tests prove the document task is credential-scoped, health-visible, and uses the existing
  durable result/acknowledgement lifecycle while Email Watcher cannot use its expanded forms.
- Run the full gateway pytest, Ruff check/format, mypy, package build, and diff whitespace gates.

### Acceptance criteria

1. A credential explicitly granted `document.summary.step@1` sees it in authenticated health; an
   Email Watcher credential does not gain it.
2. `TaskPolicy` keeps both Email tasks at 1,500 output tokens and permits 4,096 only for the document
   task; a request one token above either task's cap fails before the worker call.
3. The request parser admits exactly bounded root object choices with 2 through 64 closed branches,
   while the service permits that form only for a task whose policy enables it.
4. Root choices with one or 65 branches, any non-object branch, an open object branch, a simultaneous
   root `type`, or a nested non-null object choice fail before worker dispatch.
5. The new task completes and acknowledges through the existing durable lifecycle with one worker
   call and task-policy provenance.
6. Existing Email Watcher task health, object-root schemas, 1,500-token admission, privacy,
   idempotency, expiry, replay, and acknowledgement tests remain unchanged and green.

### Implementation summary

- Added `document.summary.step@1` as an independently authorized task with temperature 0 and a
  4,096-token ceiling. Both Email Watcher tasks retain temperature 0.1 and a 1,500-token ceiling.
- Raised only the shared parser ceiling, then enforced the narrower effective limit through the
  selected immutable task policy before durable admission or worker dispatch.
- Added a bounded root object-choice schema form: exactly 2 through 64 closed object branches, with
  the existing recursive schema restrictions applied inside every branch. The document task opts
  into that form; Email Watcher tasks reject it at the service policy boundary.
- Added credential-scoped health, lifecycle/acknowledgement, per-task cap, schema boundary, and
  worker-output validation regression coverage. Updated the private configuration example with an
  independent Document Summarizer credential and task grant.

### Cold diff audit

- `contracts.py` broadens the syntactic parser only to the two proven Document Summarizer needs:
  a 4,096-token global parse ceiling and bounded closed-object root choices. Existing schema
  depth/node/byte/enum/array/keyword restrictions still recurse through every choice branch.
- `app.py` is the effective authorization choke point: its immutable task policy grants the higher
  token ceiling and root choice only to `document.summary.step@1`, before `RequestStore.admit` or
  `worker.infer` can run.
- The new task enters the unchanged reserve, single-lane dispatch, encrypted result, replay,
  provenance, expiry, and acknowledgement path. Store, worker transport, configuration parser,
  TLS, and deployment code are untouched.
- Tests exercise both sides of the new bounds: 1,500/1,501, 4,096/4,097, 2/64 accepted choices,
  and 1/65, scalar, open, mixed-root, nested-choice, unauthorized-task, and invalid generated-output
  rejection paths.
- README and this contract describe only the implemented gateway admission surface. Document
  Summarizer still has no gateway adapter, and no application cutover is claimed.
- Untraced or forbidden changes: none.

### Gap audit

LOCAL IMPLEMENTATION DONE; review and merge remain.

- Focused contract/task/worker proof: 43 passed.
- Full local gate: 132 passed and 1 intentionally skipped live test; Ruff lint and format, mypy over
  7 source files, source distribution, wheel build, and diff whitespace passed.
- The opt-in live Ollama smoke was not run because worker transport and model selection are
  unchanged.
- Exact-current Document Summarizer schema projection, durable request identities, gateway adapter,
  application acknowledgement, restart reconciliation, and end-to-end application acceptance are
  the coordinated client slice and remain NOT DONE.
