# Proposed contract: email decoder order and app-schema dependency

Status: DRAFT, NOT ACCEPTED. This PR contains only this contract. It is based on
gateway main `f105bfd0fba0c8f0866902751c0ec7018171ea39`, not on the parked code.

## Authority and correction

Juan's decision, relayed verbatim on October 31, 2026:

> I don't approve the contract just approved he write it.

[Decision](https://github.com/canfieldjuan/local-inference-gateway/pull/36#issuecomment-6545458273).
I incorrectly treated "Continue" as acceptance and implemented and attempted live
replay before exact-revision acceptance. That claim in the previous document is
withdrawn. "Continue" authorized writing the contract only.

[PR36](https://github.com/canfieldjuan/local-inference-gateway/pull/36) and commit
`dd41cb54ab5dab0ce3b46337abfec41edb117951` remain parked unchanged as a draft.
No history rewrite, merge, further implementation, live test/replay, deployment,
GPU-availability retry, or production change is authorized. The old recommendation
to resume a fresh replay is superseded by this hold. Retain the code and evidence;
neither their existence nor green CI constitutes contract acceptance.

This contract stops at review and Juan's acceptance naming its exact full commit.
Record that acceptance verbatim before any later implementation. Oversight PASS,
automated checks, silence, and earlier general assent do not substitute for it.
Live inference and deployment each require their own explicit authorization and
applicable gates; accepting this text is not permission to run either now.

## Root cause

Sorted canonical JSON serves two different purposes: stable replay identity and
model-visible schema traversal. Gateway `worker.py::_infer` applies sorted encoding
to ordinary email schemas; this alphabetizes their properties. The existing
`contracts.py::decoder_schema` instead derives required-first order for passage
schemas. Reusing that policy for email can match direct output only when the
consumer's required sequence actually equals its declared property sequence.

[Oversight G1/G2](https://github.com/canfieldjuan/local-inference-gateway/pull/36#issuecomment-6545302704)
identify the missing app dependency and missing acceptance. Initial sorted dispatch
came from `872fba9`; `5df8119` retained it for non-passage schemas. The proposed
origin owner is one decoder-order policy in `contracts.py`, consumed by worker
projection and final serialization. Canonical identity remains a separate owner.

## Validated inventory and limits

The prior field-order ablation used app revision
`7338985b2a7501a3a4e3513457396601a8dd4b23`, the head of **unmerged Email Watcher
PR314**, not Email Watcher main. Exact frozen prompts, schema values, request
settings, fixtures and answers remain in the durable private packet. Do not print
or commit them. Reuse its `prepare.py`, `run.py` and `analyze.py` after separately
authorized work; do not invent a new oracle or tune inputs.

- Model: `hf.co/Canfield/Qwen3.5-9B-Q4_K_M-GGUF:latest`, weights
  `cd76ec205963b3b33350093e6904d9de16c4e666fd104e1f632d25c7f15f2a13`.
- Ollama0.24.0 binary
  `b2e45ade9cb754a079f74645e1183d613f582d98f7354b05f4f9a5bd81f8e0c9`;
  closed-think template
  `790aae76aa0a2bbdb8bde833cf46a7169825353d3bc3ebafa174d7f6fb6a3980`.
- Context32768, top_k1, top_p1, presence_penalty0; request temperature0.1,
  max_tokens500, no seed, stream false. The model's stored temperature0 is not
  the request temperature. Prompt framing and schema date bounds stay frozen.
- Two frozen email corpora,22 fixtures, three repetitions, four variants:
  264 valid calls. Order-only changed21 fixtures; name-only changed none.
  Controls matched66 saved direct and66 saved gateway parsed answers.
- The later, unaccepted implementation attempt completed5 matching outputs on6
  offers before an external GPU ownership STOP. It is retained evidence, not
  acceptance, full parity, or permission to resume. No partial-required model
  parity has been established.

Artifact aliases and SHA256:

| Alias | SHA256 |
| --- | --- |
| field-order-ablation-report | `12fb2e4f5d2eca4a252c587ab20462a5c1576ad88d6127bd2fac8328969fa560` |
| field-order-ablation-public-manifest | `8fd7aa87def8ec9531b0e367589ef69f9e646af2791fabf8d4f29f0559979268` |
| field-order-ablation-private-manifest | `5df29a02eaf2f9f1ff49c9e02efe43f613aefb8935ae35a409ab1260619432f8` |
| email-schema-order-parity-partial-report | `7cd548f03cbe30a961277186ff56e83e040f7fd1e3b3222804702efa95b35070` |
| email-order-contract-schema-inventory | `44ba0acae146ed3202a9a20c84a7d9c50c21495eb39d3ba738947c7201df86f1` |

All artifacts are durable outside worktrees. The last receipt is read-only schema
declaration inspection for this contract, not a test suite or inference run.

## G1: explicit dependency and partial-required behavior

**Dependency:** [canfieldjuan/eom-email-watcher PR314](https://github.com/canfieldjuan/eom-email-watcher/pull/314)
is open and unmerged at this contract's inventory. It makes the nullable analysis
fields required and supplies the decoder date-length bounds used by the frozen
experiment. It must independently satisfy its own qualification/acceptance/merge
requirements. This contract neither clears those gates nor authorizes its merge.

The directly declared property sequence at both inspected app revisions is:

`category, priority, summary, action_required, suggested_action, deadline_text, deadline_iso, confidence`

The proposed policy is explicit: at each admitted object-schema node, emit the
properties named in `required` in that array's order, then the remaining optional
properties alphabetically. Recurse through admitted properties/items/alternatives;
keep schema constraints, array order and the existing passage behavior unchanged.
Missing or empty `required` means all properties are optional and alphabetical.
This is deterministic required-first projection, not arbitrary mapping-order
preservation. It does not add required fields or reject a partial list at runtime.

| Consumer schema | Required sequence | Expected proposed worker property order | Direct parity |
| --- | --- | --- | --- |
| Email Watcher main `69b98e958eebb7f5941831b9c27373aa8d4b3fbf` | category, priority, summary, action_required, confidence | category, priority, summary, action_required, confidence, deadline_iso, deadline_text, suggested_action | **No. Deployment blocked.** |
| PR314 head `7338985b2a7501a3a4e3513457396601a8dd4b23` | category, priority, summary, action_required, suggested_action, deadline_text, deadline_iso, confidence | category, priority, summary, action_required, suggested_action, deadline_text, deadline_iso, confidence | Field order matches; full live parity still unproven. |

The partial-list sequence above is neither direct order nor the installed gateway's
alphabetical order (`action_required, category, confidence, deadline_iso,
deadline_text, priority, suggested_action, summary`). Stable does not mean qualified.
The real declarations are at main `src/eom_email_watcher/model.py:81-93` and PR314
`src/eom_email_watcher/model.py:81-95`; the receipt pins their source hashes.

**Future deployment precondition, not a claim that the parked code enforces it:**
PR314 must be merged and its intended schema behavior must be present in every
actual Email Watcher consumer to be routed to the changed gateway. Record the
installed consumer revision and emitted schema hash/order. Require every property
to appear in `required` exactly in the validated direct sequence above. A merged
PR alone, an uninstalled branch, a mixed fleet, a partial/unknown schema, or a
changed sequence is a STOP. Leave that consumer on the existing deployment; do
not silently use the third order, normalize its schema, or waive the dependency.
A later consumer revision must be re-bound to the inventory before qualification.
There is no gateway deployment or client cutover in this contract-only PR.

## Proposed change surface after acceptance

- Extend one decoder-order policy to exactly `email.analyze@1`, used by both schema
  projection and final wire serialization, including the inherited LM Studio path.
  Replace the passage-only transport decision rather than adding a downstream fix.
- Preserve canonical envelope hashing, exact-number handling, replay/conflict,
  expiry, acknowledgement, authorization, cancellation and capacity behavior.
  Required-array order already participates in identity; mapping order does not.
- Keep passage schemas unchanged; ordinary scheduling, invoice and non-passage
  document schemas retain their existing transport. This is a narrow email slice,
  not completion of [gateway issue35](https://github.com/canfieldjuan/local-inference-gateway/issues/35).
  Invoice adoption remains blocked on its separate whole-schema-order/parity work.
- Add the real partial-required main-shape regression and the full PR314-shape
  regression. Assert the literal sequences above on captured final wire bytes,
  not dictionary equality. The partial regression must explicitly assert inequality
  with direct order. It documents unsupported rollout; it does not qualify it.
- Document the dependency, expected partial behavior, explicit rollout STOP and
  retained existing results. Decide whether any parked code can be reused only
  after acceptance and review against this contract; do not cherry-pick it now.

## Differences from the validated experiment

1. Required-array projection replaces insertion-order reliance. It equals the
   validated direct order only under the complete-required dependency above.
2. Future qualification would exercise the corrected gateway API and worker with
   captured outgoing bytes, rather than direct OpenAI-shaped POSTs. Preserve
   messages, schema values, thinking framing, runtime/model pins and budgets.
3. Gateway schema name remains `email_analyze_v1`; the ablation measured no name
   effect on these fixtures. Compare schema property order separately from
   incidental ordering of other JSON object keys.
4. An isolated API process/transport, temporary credentials/encrypted state,
   fresh request IDs/expiry metadata and private worker endpoint are declared
   departures. They must be recorded, not confused with installed TLS qualification.
5. The app source is PR314's frozen schema, not current main. Refresh that binding
   after its independent merge; if inputs or settings differ, revise the inventory
   before proof. No partial-required or alternate-model experiment is approved.

## Verification plan after separate authorization

No tests or replay are executed as part of this contract-only publication.
After exact-revision acceptance, proposed implementation evidence must include:

- Fail-first actual-ingress-to-wire probes for full and partial real schema shapes,
  both adapters, nested objects/arrays/nullable branches, missing/empty/partial
  required arrays, reordered input mappings and no input mutation. Preserve the
  failure and source pins outside worktrees before applying an accepted fix.
- Equal canonical identities must produce identical decoder bytes for mapping-key
  reorderings; changing a required sequence changes both identity and wire order.
  Exercise stored replay/conflict, expiry/acknowledgement and unchanged sibling
  task/passage behavior. Preserve exact decimal/schema admission constraints.
- Required local gate and independent exact-head review; current code's earlier
  green checks do not transfer acceptance to this contract or an eventual diff.
- Only after explicit live-run authority: reuse the sealed frozen fixtures and
  bounded runner for a fresh66-call, three-repeat isolated replay. Require all
  complete parsed and app-validated outputs to match the frozen direct controls;
  retain scoring failures, rejections, partial outputs and cancellations. Do not
  promote5 matches to full parity or alter the oracle.
- Fresh evaluation lock and one-shot ownership/resource admission, loopback/private
  worker, identical model/runtime/profile pins,120s per request/1200s total,
  no retries, and owned-process cleanup. Any foreign owner or unreadable ownership
  signal stops the run without automatic retry. Preserve the old STOP packet.
- Installed gateway qualification and rollout remain separate and blocked on the
  complete-required consumer inventory, full parity, independent review and Juan's
  explicit deployment authorization. No production change is implied by unit green.

## Explicit non-scope

No implementation in this PR; no tests, dependency/config/workflow changes,
credentials, watchers/mailbox routing, database migration, model/capacity/prompt
change, schema requiredness/date-bound amendment, task expansion, field filtering,
relabelled expectations, replay-identity migration, production restart or cutover.
No approval review, merge, history rewrite or deletion of parked work.

## Implementation summary and cold diff audit

This proposal adds only this Markdown contract from gateway main. It corrects the
authorization statement, names the unmerged dependency, fixes the partial-required
expectation, and specifies future acceptance/qualification boundaries. Source,
tests, runtime settings and the parked implementation are unchanged. Verify the
publication diff contains this file alone; no code test suite is warranted for it.

## Gap audit and stop

NOT DONE for acceptance, implementation, parity or deployment. Contract preparation
and publication are the current work order. Oversight review and Juan's explicit
acceptance naming this contract's exact full commit remain outstanding. Stop here;
no next implementation or live-run phase is authorized by publishing this draft.
