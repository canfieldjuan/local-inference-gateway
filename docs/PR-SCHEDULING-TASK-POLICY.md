# Bounded Scheduling Extraction Task Policy

### Contract

Root cause:

- The merged gateway hard-codes one supported task, `email.analyze@1`, even though credentials
  already carry a set of authorized task identities. Email Watcher's durable scheduling extraction
  therefore cannot be authorized or reported independently.
- The gateway's schema evaluator rejects every array. The scheduling result is intrinsically a
  bounded collection of candidate times, attendees, evidence, and ambiguity reasons, so its
  fail-closed Pydantic schema cannot cross the current boundary.

Required change surface:

- Replace the single supported-task constant with an explicit immutable policy map and add only
  `email.schedule.extract@1` alongside `email.analyze@1`.
- Report every supported task authorized to the calling credential; keep unsupported or
  unauthorized tasks unavailable.
- Admit only arrays with a schema object in `items` and an explicit bounded integer `maxItems`;
  validate optional `minItems`, recurse through the item schema, and keep references, regexes,
  tuple schemas, unevaluated items, and unbounded arrays rejected.
- Preserve the same worker lane, request identity, expiry, encrypted result, replay, acknowledgement,
  and output validation lifecycle for both tasks.

Explicit non-scope:

- No Email Watcher change in this PR, no scheduling prompt or domain validation in the gateway, no
  model/runtime selection, no LM Studio fallback, no new transport, and no Connect behavior.
- No arbitrary JSON Schema support, `$ref`/`$defs`, regex evaluation, unbounded collection, multiple
  output media types, vision, or task discovery outside the authenticated health response.
- No credential-file mutation or appliance deployment change; the operator must authorize the new
  task explicitly in each applicable credential.

Assumptions/blockers:

- Email Watcher remains the owner of the scheduling prompt, output schema, deterministic validator,
  durable automation ledger, and application acknowledgement.
- A coordinated Email Watcher PR will inline its local non-recursive schema and switch scheduling
  extraction to `email.schedule.extract@1` only after this gateway policy lands.

Verification plan:

- Contract tests probe bounded nested arrays on both sides of `minItems`/`maxItems`, and reject
  missing items, missing bounds, malformed bounds, tuple items, references, and unsupported keywords.
- Service tests prove credential-scoped health and inference for both supported tasks while preserving
  forbidden and unsupported-task behavior.
- Existing request lifecycle, worker-output validation, concurrency, expiry, acknowledgement, TLS,
  package, lint, type, and lock gates remain green.

### Acceptance criteria

1. An authorized credential sees `email.analyze@1` and `email.schedule.extract@1` in health, while a
   credential sees neither task it was not granted.
2. The new task follows the same durable reserve/dispatch/result/acknowledgement path and produces
   provenance through the existing task-policy version.
3. A nested array schema with object items and explicit `maxItems` is accepted, and generated output
   remains validated by `Draft202012Validator` before storage.
4. An array without one schema-valued `items` or without a bounded integer `maxItems` is rejected
   before worker dispatch; `minItems < 0`, `minItems > maxItems`, and bounds above the gateway cap are
   also rejected.
5. `$ref`, `$defs`, regex patterns, tuple items, and other unsupported schema forms remain rejected.
6. Existing `email.analyze@1` clients and all request identity, privacy, expiry, replay, and
   acknowledgement behavior remain unchanged.

### Implementation summary

- Replaced the single task constant with an immutable task-policy map containing
  `email.analyze@1` and `email.schedule.extract@1`; authenticated health now reports every supported
  task granted to that credential.
- Added bounded array-schema admission with one recursive item schema, an explicit `maxItems` cap of
  100, and validated optional `minItems` while retaining the existing schema depth, node, byte,
  keyword, and output-validation bounds.
- Exercised scheduling inference through the existing durable request/result/acknowledgement
  lifecycle and kept unauthorized or declared-but-unsupported tasks fail-closed.
- Updated the credential example and security contract without adding credentials or changing the
  appliance deployment.

### Cold diff audit

- `app.py` changes only the task-policy lookup and credential-scoped health enumeration; both tasks
  still enter the same `GatewayService.infer` lifecycle and worker lane.
- `contracts.py` adds only bounded homogeneous arrays. It continues rejecting `$ref`, `$defs`,
  patterns, tuple schemas, open-ended combinators, unsupported types, excessive depth/nodes/bytes,
  and invalid numeric values before dispatch. The nullable-union admission state propagates through
  both property and item recursion so a container cannot reopen a nested union.
- Tests cover the array cap at 0/100/101, malformed/falsy bounds, missing or tuple `items`,
  `minItems` below zero and above `maxItems`, array keywords on a scalar, nested nullable-union
  bypasses through arrays and objects, credential isolation, unsupported tasks, nested generated-output
  rejection, and successful scheduling acknowledgement.
- README changes describe only the implemented task and schema admission. Worker selection, storage,
  TLS, application repositories, and deployment files are untouched.
- Untraced or forbidden changes: none.

### Gap audit

DONE for the gateway task-policy surface.

- Focused boundary/task tests: 14 passed; the array-output worker probe: 1 passed; the review-driven
  nullable-union recursion probe: 5 passed.
- Full local gate: 120 passed and 1 intentionally skipped live test; Ruff format/lint, mypy over 7
  source files, diff whitespace, source distribution, and wheel build passed.
- The opt-in live Ollama smoke was not rerun because worker transport and model selection are
  unchanged.
- Exact Email Watcher schema inlining, client task selection, credential deployment, and live
  scheduling acceptance remain the coordinated application PR; they are not represented as done by
  this gateway policy slice.
