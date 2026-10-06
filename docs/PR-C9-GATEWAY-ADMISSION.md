# Gateway admission for constrained C9 comparisons

Status: PROPOSED on October 5, 2026 (America/Chicago). Operator acceptance
is required before implementation. This document must be committed first.

Direction: [DocSum PR116 discussion](https://github.com/canfieldjuan/document-summarizer/pull/116#discussion_r4190858584).
Gateway base: `6e5bb8be38f0e89e8ec61cd2bc30ac3121581744`, currently both remote
main and the installed release. Branch: `codex/c9-gateway-contract`.
DocSum remains on `4c8b9dc75f1b38f39eb8a9b681b8c04ff8413da6`, branch
`codex/verification-dimension-contract`. Its verifier schema is an input to this
work, not a surface to reshape. PR116 remains fidelity-held.

Acceptance authorizes the changes, review, controlled gateway deployment and C9
resumption specified below. It does not authorize promoting C9 or merging PR116.

## Root cause

All gateway citations below are against the pinned base in
`src/local_inference_gateway/contracts.py`, unless another file is named.

| Finding | What the code actually does | Required correction |
| --- | --- | --- |
| Alternative budget over-count: confirmed | Lines 316-321 mutate shared reference and passage counters. Lines 389-392 pass the same counters through every alternative. Values from alternatives are added as if one response contains every alternative. | Bound possible passage selections by the largest alternative, with independent validation of every branch. |
| Shared definitions plus root alternatives: confirmed | Lines 233-242 require a root `type: object` when `$defs` exists. Lines 415-426 require a root choice to contain only `anyOf`. Lines 150-151 check the unprojected root again. Neither accepted form composes with the other. | Validate the root definitions and the closed-object choice as parts of one admitted schema. |
| Whole-sentence passage rejection: confirmed | Line 28 sets the per-value character cap to 240; lines 253-262 apply it to every passage enum value. | Set that specific cap to 4096, matching DocSum's existing combined claim/context input bound. |

The retained reproduction rejects the actual C9 schema at the definitions/root
check. An isolated call into its branch validator then reproduces the passage
budget failure. A valid object with one 240-character passage is admitted; the
same shape with 241 characters is rejected. These are admission results with no
model calls, not findings about model fidelity. Evidence alias:
`verification-dimension-contract-20261005/gateway-installed-schema-admission.json`,
SHA-256 `a62a5f3964d3f2349b2b167e87df89a611e7c344a05cf941164f2ad2d4bd03b3`.

The originating source-passage contract explicitly summed alternatives and tied
definitions to an object root. I carried those restrictions into this arc; the
gateway must replace them at admission. No downstream DocSum filter, alternate
schema or retry can correct that owner.

## Required change surface

### Alternative accounting

Change `_validate_supported_schema` so a possible response's passage cost is
computed compositionally: add simultaneously present properties; multiply by
each enclosing array's `maxItems`; take the maximum cost across admitted
alternative branches. Reject if any branch exceeds the unchanged limit of 32
possible passage values. Carry surrounding multiplicity and sibling cost through
the calculation; do not reset them at a choice or stop checking after a valid
branch. Nullable choices use the same accounting rule.

Keep structural limits distinct from possible output size. The total encoded
schema, total JSON nodes/depth and physical reference-node count still include
every serialized branch. The 16-reference-node limit remains a structural cap;
do not replace it with a per-branch allowance. This preserves the original
bounded grammar surface while fixing the erroneous sum of passage selections.

The discussion mentions `anyOf`/`oneOf`. Current code admits bounded `anyOf` forms
and rejects `oneOf` as an unknown keyword. This contract fixes admitted choices;
it does not add `oneOf`, nested object choices or another combinator. The generic
largest-branch rule must not be interpreted as new schema-language permission.

### Root choices with shared definitions

Admit the existing root `anyOf` of 2 through 64 complete closed object branches
with one root `$defs` mapping containing one or two named string-enum leaves.
Keep existing object-root definitions and choices without definitions working.
Use one root-shape classification consistently in `Generation` and task-policy
validation, rather than separate permissive exceptions.

Validate every definition, including unused ones, and every alternative. Keep
exact single-key `#/$defs/name` references to admitted leaves only. Reject
unknown keywords, missing/invalid definitions, reference siblings, remote and
relative references, dangling targets, chains, recursion, nested definitions,
open or non-object branches, unsupported nested choices, and excess depth/nodes.
Do not introduce resolver I/O or dereference arbitrary input.

### Passage value length and retained limits

Change only `MAX_PASSAGE_CHARACTERS` from 240 to 4096 Unicode code points. Empty,
non-string and duplicate enum values remain invalid. The new length is per enum
value; it does not claim the gateway has parsed or enforced DocSum's combined
claim/context limit. DocSum keeps that existing combined limit independently.

Retain all other numeric caps: 250000 schema bytes, 1000000 request bytes,
500000 characters per message, depth 32, 20000 JSON nodes, 100 ordinary enum
values, 100 array items, 64 root alternatives, two passage definitions, 8192
values per passage enum, 16 reference nodes and 32 possible selected passages.
Document task output stays 4096 tokens and context stays 32768. No truncation,
enum inlining, text normalization, label changes or output allowance increase.

### Owners and superseded rules

- `contracts.py`: root-shape owner, passage-length limit and compositional
  passage accounting. Replace the object-only definitions prerequisite, the
  raw-root-only choice check, additive alternative selection cost and old length
  assertion. Retain strict leaf references and all structural caps.
- `app.py`: consume the same root-shape classification at the task-policy gate;
  preserve exact grants and rejection before store admission/worker effects.
  Increment `TASK_POLICY_VERSION` from 1 to 2 to identify corrected admission.
- `tests/test_passage_schema.py`, `test_contracts.py`, `test_app.py`,
  `test_worker.py`, `test_task_capacity.py` and `test_store.py`: only the affected
  admission, transport, isolation, identity and replay proofs. Add a dedicated
  public synthetic fixture/helper if required; no real contract excerpts or raw
  model output is committed.
- `README.md` and the source-passage contract: document the revised admission and
  controlled deployment procedure; mark their superseded rules explicitly.
- `worker.py`, storage and the installer are inspection/proof surfaces. Change
  them only if a reproduced failure requires revising this contract first.

## Identity, deployment and consumer effect

Protocol version 1, task `document.summary.step@2`, profile version 1 and its
existing feature string remain unchanged. A profile-version/feature change
alone would be rejected by DocSum's strict profile reader
(`gateway_client.rs:786-800` at the pinned DocSum head); do not work around it in
the verifier. Instead identify the corrected gateway by all of:

- the reviewed gateway Git commit and installed module hashes;
- task policy version 2 in newly completed response provenance;
- a fresh deployment ID `canfieldjuan-desktop-c9-<reviewed-commit-prefix>` using
  the first 12 hexadecimal characters of the reviewed implementation commit.

`app.py:324-334` returns the producing policy/deployment stored with each completed
request. Preserve that behavior: historical results replay with their original
provenance, bytes and digest, not the new process's identity. The package version
alone is not evidence of which validator is running.

After independent review and green required checks, deploy only the clean exact
reviewed gateway revision using the existing release installer. Coordinate the
exclusive inference lane and admission exclusion, verify zero active requests,
then stop the service before switching the release. Retain the old release and
owner-private configuration receipt; change only the deployment identity needed
for this rollout, then start the new service. Do not change model, model digest,
context, thinking, seed, output budget, endpoint/TLS, credential grants or stores.
The existing installer does not itself start the service, and changing its
symlink does not prove the running process uses the new code.

Before either a DocSum development run or qualification run, record and verify
the installed release path, source/module hashes, running process/start identity,
expected deployment ID and policy version, credential-scoped task-v2 profile and
frozen model/settings receipt. Prove readiness with the public grammar/admission
probe. Use fresh run owners, request IDs and output directories. Check every
completed response's provenance against that receipt and recheck the process and
release after the run. A mismatch, stale replay, process change or unavailable
profile stops the run; it cannot silently use the old validator. Do not reuse a
pre-upgrade request owner or alter DocSum's production schema to fit the gateway.

Known consumer effect, from inspected source:

- DocSum is the inspected task-v2 client and gains the three admitted forms.
- Invoice Processor at `85ae9de619d545fbf5f09fb82188666c7cbc8273` selects
  `invoice.extract.batch@1` (`src/invoice_processor/model.py:53-54`).
- Email Watcher at `e482914f421ea7cb663a309e7b95fdeee194adf3` selects
  `email.analyze@1` and `email.schedule.extract@1`
  (`src/eom_email_watcher/model.py:432-436`).

Only document task v2 has the passage-definition policy
(`app.py:64-86,300-309`). Shared `Generation` validation still runs for all tasks,
so negative route tests must prove that the other tasks cannot dispatch the new
forms and that their valid ordinary schemas still work. Global policy version 2
and the new deployment ID appear on new completions for every task; content,
authorization, output limits and old replay provenance remain unchanged. Before
deployment, inventory installed task-v2 grants without printing credentials and
record anonymized client counts. No other task-v2 consumer was found in the
inspected clients; an additional deployed consumer requires a recorded impact
check before cutover, not an assumed compatibility claim.

## Explicit non-scope

No new task/version, arbitrary JSON Schema support, `oneOf`, wider structural
caps, remote resolver, dependency bump, database migration, credential/grant
change, runtime fallback, model change, inference retry or application UI change.
No DocSum verifier/prompt/schema reshaping, gateway-specific workaround, control
relabeling or held-out tuning. DocSum issues #124 and #123 remain separate.
No PR116 merge or fidelity release is authorized by this contract.

## Assumptions and blockers

The installed source must still match the pinned gateway when implementation
begins; otherwise reconcile the exact diff before edits. The contract is pending
operator acceptance. Deployment needs the existing host privileges and an idle
admission lane. Runtime grammar support and C9 semantic performance remain
unproven under this gateway revision until their gates actually run.

## Verification plan

1. After acceptance, run the repository-required baseline local gate. Declare
   fail-first regressions before edits: the branch-cost over-count, actual root
   choice plus shared enums, and a 241-character whole passage. Isolate the
   branch-cost owner while the root-shape check still rejects first. Preserve
   failing commands, outputs and source hashes outside worktrees.
2. Fix the owners and prove both directions: alternatives whose sum exceeds 32
   but largest branch fits pass; any branch with 33 selections fails, including
   a valid first branch followed by an invalid one. Cover 31/32/33 costs,
   simultaneous sibling sums, array products, nullable choices, zero maxItems,
   mixed empty/nonempty lists, branch-order invariance and duplicate alternatives.
   Preserve the physical 15/16/17 reference-node boundary and full traversal.
3. Root-shape tests: old object definitions, choices without definitions and the
   unchanged C9 schema pass. Probe 1/2/64/65 alternatives, 0/1/2/3 definitions,
   malformed and unused leaves, every forbidden reference form, unknown keywords,
   open/mixed branches, nested choices, and depth/node boundaries. Neither a
   valid sibling nor an unreachable branch can hide invalid schema content.
4. Length tests: 0/1/239/240/241/4095/4096/4097 code points, including multibyte
   text. Mixed valid/invalid enums fail. Preserve total schema bytes at
   249999/250000/250001, enum counts, request-size and structural caps. Probe
   falsy/default defeats with 0, empty string, false and null where applicable;
   never default an invalid supplied bound into validity.
5. Exercise the application and actual worker serializer: authorized v2 reaches
   dispatch; ungranted/wrong tasks do not touch store or worker. Preserve exact
   required-field order and decoder bytes, shared enums, final output validation,
   rejection of foreign passages and contradictory relation/list shapes, finite
   numbers, timeout/output-limit behavior, simultaneous identical-request replay,
   changed-schema identity conflict, expiry and ACK. Prove old completions keep
   old provenance across the policy/deployment change.
6. Run focused tests followed by the contract-required full pytest, Ruff lint and
   format checks, strict mypy and package build; CI verifies its wheel import.
   Record `boundary-probe` and `effect-trace` against the controlling admission
   and dispatch code. Publish for independent exact-head PR review, reconcile
   findings at their origin, then merge/deploy only with green required checks
   and resolved review. No implementation starts on this proposed contract alone.
7. Verify the corrected installed gateway identity as above, then resume C9 on
   its existing branch with the accepted gates unchanged: recheck static coverage
   on the frozen splitter; measure actual gateway and native maximum admitted
   claim/source sizes with all four projections and rejected neighbors; report
   all six saved public B inputs; run grammar/converter probes on both runtimes,
   including prohibited combinations. No controls start unless all prerequisites
   pass. Native converter-only results are not a substitute for the live probes.
8. Freeze both source revisions, driver/scorer, original references, model and
   settings. Execute three repetitions of each of the four approved gateway
   controls with fresh owners: 12 control runs, 48 planned dimension calls.
   Retain every attempted and unexecuted dimension. Every run requires exact
   recorded-verdict parity, exact owned membership, valid relations and all
   recorded passages contained in one selected piece on the original side and
   dimension. Keep zero wrong approvals mandatory; report latency and actual
   calls separately. No averaging, replacing failed attempts, tuning or IDs.
9. Freeze and report the result. A failed prerequisite stops before controls;
   a failed control fails the candidate. Full A/B worker proof, independent
   review and unseen qualification remain before PR116's hold can be revisited.

## Implementation summary

Only this proposed contract is added. No gateway or DocSum runtime/test source,
installed configuration, model, credential or PR state is changed by drafting it.
The existing candidate and all failed-run evidence remain preserved.

## Cold diff audit

This document is the only intended tracked diff from the pinned gateway base.
Its three admission changes map to the three observed owner failures. The
identity/cutover, other-consumer checks and C9 sequence implement the cited
discussion's remaining requirements. Verify the documentation-only diff and
identical `src`, `tests` and dependency trees before presenting its commit.
Code tests are not rerun for a contract-only change.

## Gap audit

DONE for the proposal once the documentation-only commit is verified.

NOT DONE for implementation, deployment or C9 qualification. Operator acceptance
of this committed contract is the next gate. No live run or review-thread
resolution follows from drafting it.
