# Document task capacity and client profile contract

## Root cause
GatewayService.health reports only availability. Task version 2 admits passage schemas but provides no context guarantee to the consumer. DocSum therefore continues budgeting its version-1 constant (8192), which can reject full-clause synthesis before inference even though the installed 9B worker allocates 32768. Replacing only the client constant would assume capacity that the server does not promise or enforce.

## Required change surface
1. Gateway contracts: one document-task-v2 profile owns a bounded context guarantee (32768), existing output/schema limits and the bounded-source-passage capability. This is a task guarantee, not a claim to expose every token the model could allocate.
2. Gateway authenticated profile endpoint returns that profile only to credentials granted the exact task. The existing health response stays byte-shape compatible for strict legacy consumers.
3. Ollama availability and pre-dispatch admission for document task v2 require an explicit model num_ctx at least the guarantee. If the selected model is already resident, its actual context must also meet the guarantee. Missing, mixed, malformed, smaller or unverifiable capacity fails closed before inference. Native model design maximum is never used as effective capacity. The checks share the request deadline. Other tasks preserve existing behavior. The optional LM Studio fallback cannot serve this larger-capacity task without a verified capacity contract; its existing version-1 tasks remain eligible.
4. DocSum chooses advertised v2 via the authenticated profile, derives its budget and persisted snapshot from that profile, and uses v2 for every request in that run. Legacy version-1 snapshots/replays remain readable and retain their original task/budget. Do not reinterpret a saved run under a different task contract. C9 schemas become available only under v2. Invalid advertised profiles fail closed; absent v2 remains the existing safe v1 path.
5. Tests: fail-first missing profile and false-positive small-worker availability; both sides of context bounds, malformed/default values, authorization, effective resident allocation, no dispatch on mismatch, timeout classification, fallback, client negotiation/snapshot/resume and actual full-document path after reviewed deployment.

## Explicit non-scope
No prompt, C9 schema/rule/label change, new model, model/context configuration change, generic reference support, UI redesign, storage migration, existing-task schema expansion or unseen tuning. PR116 fidelity hold remains. No unreviewed deployment or task grant.

## Assumptions/blockers
PR29 merged at 5df811967c5c6216397a6adb5ad98efa3640ba4b. Installed 9B has a verified explicit num_ctx32768 and matching runtime allocation. Per-model num_ctx is the effective configured Ollama value for the existing OpenAI transport; resident allocation must not contradict it. A local administrator mutating the worker outside the gateway remains a deployment coordination responsibility; observed inconsistencies stop new work.

## Verification plan
Baseline gate already proved the unchanged merged source: 220 passed, 2 skipped, Ruff/mypy/build pass plus exact-head CI and 9B retained controls. Add minimal failures before runtime edits. Run affected tests and repository-required local gates. Freeze code before fresh live proof. Keep durable evidence private outside worktrees. Preserve exact control inputs/labels. Integration/full-app/unseen gates are separate from transport and parser controls.

## Implementation summary
Server slice implemented in contracts.py (profile/limits owner), app.py (authenticated profile route), and worker.py (task-aware availability plus recheck immediately before dispatch). Legacy health shape and task-v1 inference/replay are preserved. No downstream context override was added. The former task-blind availability check remains only for tasks without a capacity promise; no additional text scanner or unrelated special case was introduced.

Regression tests in test_task_capacity.py first reproduced profile404 and undersized-worker availabilitytrue, then passed after the origin fix. A second fail-first caught an error classification introduced during this implementation: malformed metadata raised InvalidWorkerOutput before inference. The submission phase now maps that case to WorkerUnavailable. Existing invalid generated-output coverage still passes. tests/test_passage_schema.py supplies task2 metadata to its actual worker test; tests/test_live_passages.py retains inference responses separately from metadata reads and proves the real profile.

Verification at a53c7dd40379d4922c1298b46b38b89912dd5f33: 229 passed, 2 skipped; Ruff check/format, mypy and package build passed. Opt-in live proof passed on the frozen 9B: advertised32768available, four controls each200/one worker call/exact replay/ACK cleanup. All four output strings equal the earlier approved9B controls. Source, model settings and input hashes stayed unchanged. Metadata receipt alias document-task-capacity-20261005/summary.json SHA256 913bb1398c3d34dd335ae7b0baef8b8ce583273dccd56a6854874525f49eeeef. Raw evidence is retained privately outside worktrees.

## Cold diff audit
- contracts.py::document_task_profile/required_context_tokens: one context promise consumed by both advertisement and worker admission; boundary tests cover minimum, maximum and malformed/default values.
- app.py::GatewayService.task_profile and route: exact grant before worker query; denied, unavailable, supported and legacy-health tests.
- worker.py::_context_availability/_infer and LMStudioWorker: bounded metadata reads within existing deadline; effective resident allocation cannot contradict configuration; pre-dispatch failures cannot trigger inference. Legacy tasks and post-dispatch ambiguity behavior retained. Existing worker/fallback tests passed.
- test_task_capacity.py: negative/positive, mixed/partial, deadline, fallback and error-direction regressions. No changes to model-output labels.
- test_passage_schema.py/test_live_passages.py: support and exercise capacity reads without changing frozen prompts, schemas or outputs.
- This contract: defines server and consumer integration boundaries and records which remain.

boundary-probe: valid32768 passes,32767 fails; invalid/zero/boolean/duplicate capacity fails; undersized resident fails; unauthorized profile fails; no inference on failed metadata; LMStudio v2 unavailable; legacy task paths pass.
effect-trace: gateway promises usable32768 context | contracts-owned minimum plus worker configuration/resident checks before dispatch | fail-first false availability changed to unavailable; real9B profile and all four controls pass.

## Gap audit
NOT DONE for the overall integration. Server profile/enforcement and isolated live controls are complete. Independent review/merge, reviewed deployment and task grant, DocSum negotiation/snapshot integration and full-app/unseen qualification remain. This PR covers only the server slice; it does not claim production deployment or release the PR116 fidelity hold.
