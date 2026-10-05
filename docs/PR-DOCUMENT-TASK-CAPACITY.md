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
Pending.

## Cold diff audit
Pending.

## Gap audit
NOT DONE. Server profile, worker enforcement, client negotiation and end-to-end proof remain.
