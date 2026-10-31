# Email decoder-order implementation

## Contract

Implements the contract accepted at
`16e34b8591ebaf2116a06298d5085ef0248280ef`, preserved by the acceptance record and
merged in PR37. Its inventory, declared differences, dependency and qualification
limits remain authoritative. Juan's subsequent direction was "lets start the next
slice"; this slice is implementation and offline verification only. No live
inference/replay, deployment or production change is authorized.

### Root cause

`worker.py::_infer` used the passage-only condition to choose final JSON encoding.
Ordinary email requests consequently sorted schema properties, even when the
consumer supplied a complete required sequence. Initial sorted dispatch came from
`872fba9`; `5df8119` retained that behavior outside passage schemas. The full app
schema fails the actual ingress-to-wire regression before this change.

The parked PR36 extended ordering to all email schemas, including partial ones.
That does not implement the accepted contract. No parked commit is cherry-picked:
this implementation extends the existing projection, replacing the passage-only
transport decision with one shared eligibility policy.

### Required change surface

- `contracts.py::uses_decoder_field_order` owns eligibility: passage behavior is
  retained; email.analyze@1 is eligible only when every admitted schema node has
  complete property coverage in required. Traverse properties, items and anyOf,
  never annotations. This is an ordering decision, not a new admission validator.
- `decoder_schema` and final worker serialization consume that policy. Both
  policy and projection require an explicit task; all callers are updated.
  LM Studio inherits the same dispatch path.
- Pinned public app schema fixtures and offline ingress-to-wire regressions cover
  complete, partial and nested shapes, identity, replay, expiry and acknowledgement.
  Passage test call sites change only to pass their explicit task.

### Explicit non-scope

No schema admission/requiredness changes, canonical identity changes, new task,
model, prompt, budget, dependency or workflow changes. Scheduling, invoice and
ordinary document transport stay sorted; passage ordering stays unchanged.
PR36 stays parked and issue35 stays open. PR314 independently gates the measured
direct-order claim; partial consumers retain existing bytes. Scheduling parity
remains unmeasured. Offline wire correctness is not live model parity or installed
qualification.

### Assumptions/blockers

The policy consumes admitted schemas; existing admission owns malformed required
arrays, unsupported schema forms, and bounds. Coverage is set inclusion, not list
length. Live parity and installed qualification remain separately gated.

### Verification plan

Run the repository local gate before runtime edits and after implementation.
First reproduce the complete pinned schema's sorted wire through actual ingress
with both mocked adapters. Preserve failure evidence outside worktrees.
Then test literal property sequences and exact legacy bytes for every partial
branch, nested coverage, annotations, mapping permutations, required-order identity,
omitted task, malformed required arrays, empty/single/large objects, sibling tasks,
and the existing passage/lifecycle/capacity/concurrency suites. Exercise regression
strength against an isolated copy with unconditional email ordering; no live calls.

## Implementation summary and cold diff audit

The source changes are the shared policy and its two worker consumers. All
projection callers now supply task identity. The existing projection algorithm,
canonical encoding, schema admission and durable lifecycle are unchanged.
The new test module and pinned schema fixtures exercise the accepted behavior;
the fixture README records their public source revisions. Existing passage/C9
tests and the opt-in passage runner only gain the mandatory task argument. The
live runner was not executed.

Offline evidence:

- Baseline local gate: 283 passed, 2 skipped; lint, format, types and build passed.
- Reproduction: 4 full-schema ingress-to-wire failures on the baseline, covering
  both adapters and both mapping orders, with alphabetical rather than direct
  property sequence. The regression passes with this change.
- The 25 partial-schema compatibility cases also pass against the untouched base
  package in a fresh process, confirming the legacy-byte oracle independently of
  this implementation.
- Final local gate: 339 passed, 2 skipped; lint, format, types and build passed.
  Existing expiry, authorization, concurrency, replay, capacity,
  exact-number and passage tests are included. Live tests remain skipped.
- In an isolated package copy, removing the eligibility check produces 25 expected
  order/legacy-byte failures out of 31 selected cases, with no unrelated errors.
  This catches unconditional email ordering, including each nested route. The
  evidence wrapper initially assumed pytest's byte-comparison message included
  the exception class name; it was corrected to check the exact assertion trace
  in the saved XML. No production change was made for that wrapper error.
- An initial new replay assertion used the wrong error label. Existing
  `app.py::infer` conflict handling and its original tests establish
  `invalid_request`/409; the new test was corrected without changing the API.

`boundary-probe:` full and partial schemas through both adapters, all admitted
nested routes, missing/empty/malformed required, coverage rather than length,
annotations, and empty/single/large valid objects; the whole-request fallback and
eligible ordering pass. Invalid required arrays fail before dispatch.

`effect-trace:` preserve direct required order only for complete email schemas |
the shared policy controls both projection and final encoding | actual API
ingress with mocked HTTP captures confirms the literal direct sequence on the
full pinned schema and exact legacy bytes on every partial case.

## Gap audit

DONE for implementation and the offline local gate. NOT DONE for independent
exact-head review, CI, merge, live model parity, installed qualification or
deployment. The latter live/installed stages require separate operator authority.
