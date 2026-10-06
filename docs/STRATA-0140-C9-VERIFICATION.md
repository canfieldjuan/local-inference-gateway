# Strata 0.1.40: minimum C9 verification path

Inspected 2026-10-06. This is a verification runbook, not an amended qualification contract or permission to deploy, activate C9, merge PR116, change prompts/models, or relax gateway admission.

## Source receipt and current state

- Gateway main: `f105bfd0fba0c8f0866902751c0ec7018171ea39`; PR31 merged. Reviewed implementation: `3ca137aa71392e0d5b3b9dac4b4b03559e0018a4`.
- DocSum main: `62cc1ca69ea097a0fe87ab12a51c7251ac7c330e`.
- PR116 head: `e4a98cf1e57d079cccc6ece3a83bf3abf9107ec2`, branch `codex/summary-clause-verification`. Its body still names older implementation 42fc08c; use head source and PR-C9-SENTENCE-VERIFICATION.md. Ordinary builds remain gated; qualification uses cfg(test).
- Strata fix: `bcc39a1e4290729e1f36af52f9c3abde05b79ce3`. PR810 is closed with API merged=false; maintainer says incorporated for 0.1.40. The named upstream commit contains the fix. Verify installed source, not PR merge metadata or version alone.

Sources: [gateway PR31](https://github.com/canfieldjuan/local-inference-gateway/pull/31), [DocSum PR116](https://github.com/canfieldjuan/document-summarizer/pull/116), [Strata fix](https://github.com/Niko1221/Strata/commit/bcc39a1e4290729e1f36af52f9c3abde05b79ce3).

## What changed, and what did not

| Check | Upstream coverage | Remaining proof |
| --- | --- | --- |
| Object-only anyOf/oneOf roots, qualifying allOf/local refs | prepare_format accepts; non-object roots reject before engine load; mocked non-object responses fail | Installed Strata contains fix; actual C9 wire reaches correct endpoint |
| Gateway schema language/cost/authorization | Not covered by Strata | Existing gateway local tests plus installed receipt; oneOf/allOf/root refs remain forbidden |
| Schema correctness of generated output | Strata post-generation validation if jsonschema installed | Installed interpreter dependency receipt, live invalid-shape rejection, gateway strict validation |
| Grammar-constrained generation | NOT supplied | Blocked on Strata native engine: structured.py explicitly states it has no grammar decoder |
| Exact passages, relation truth, summary fidelity | Not covered | Frozen semantic experiment, complete app worker/delivery proof, independent unseen qualification |
| Output exhaustion/replay/ACK/provenance | Not established by root fix | Gateway regressions, installed identity and live transport receipts |

Strata structured.py prompts once and validates before delivery. Without jsonschema it checks only object shape. Even with jsonschema it is not constrained decoding. A 502 for an invalid generated relation is validation evidence, not proof the grammar prevented it. The accepted both-runtime grammar gate cannot be marked passed for this Strata engine. Stop semantic sampling until that prerequisite can actually pass or the operator accepts a separate contract change; do not silently substitute postvalidation or add retries.

Gateway remains bounded anyOf closed-object roots, optional bounded shared string-enum definitions and exact leaf refs. Preserve 250000 schema bytes, 16 physical refs, 32 possible selections (largest alternative), passage length 4096, output 4096 tokens and context 32768. Validate unused definitions/all branches. No oneOf/allOf/arbitrary root refs, enum inlining, wider caps, grant changes or schema rewriting.

## Exact reproducible source checks

Run in clean disposable checkouts on the verification host; paths below are checkout roots. No live service changes are implied.

Gateway:
```bash
git checkout --detach f105bfd0fba0c8f0866902751c0ec7018171ea39
test -z "$(git status --porcelain)"
uv run pytest -q tests/test_c9_admission.py tests/test_passage_schema.py tests/test_app.py tests/test_worker.py tests/test_store.py tests/test_task_capacity.py
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv build
git diff --check
```

Critical cases in test_c9_admission.py: test_c9_regression_root_choice_with_shared_definitions, test_c9_regression_alternative_budget_at_origin, test_c9_regression_whole_passage_over_old_limit, test_all_forbidden_forms_remain_rejected_in_choice_roots, test_document_v2_bad_output_cannot_complete_or_replay, test_choice_passages_do_not_escape_task_policy. The exhaustion test traverses real worker validation/app/store and database reopen; valid JSON with finish_reason length still fails.

DocSum:
```bash
git checkout --detach e4a98cf1e57d079cccc6ece3a83bf3abf9107ec2
test -z "$(git status --porcelain)"
cd src-tauri
cargo test --lib pipeline::summary::comparisons -- --nocapture
cargo test --all-features --test c9_production_gate
cargo test --lib pipeline::summary::service -- --nocapture
cargo fmt --all -- --check
cargo clippy --all-targets --all-features -- -D warnings
```

Include sentence_parent_cannot_hide_a_wrong_first_middle_or_last_sentence, sentence_document_admission_and_original_parent_bounds, sentence_plan_rejects_missing_duplicate_foreign_and_partial_ownership, sentences_preserve_context_ranges_identity_and_long_piece_catalogs, sentence_uncertainty_and_not_applicable_cannot_be_rescued_by_siblings, public_sentence_projection_preserves_frozen_instructions, and sentence_scorer_rejects_missing_relations_and_unsafe_approval. Historical saved-view and ordinary-library gate tests remain required.

Strata, using the SAME interpreter/environment as the installed service:
```bash
git merge-base --is-ancestor bcc39a1e4290729e1f36af52f9c3abde05b79ce3 HEAD
python -c 'import jsonschema; from importlib.metadata import version; print(version("jsonschema"))'
python -m unittest serve.test_structured
python -m unittest serve.test_server serve.test_security serve.test_lifecycle serve.test_detok serve.test_mcp serve.test_monitor serve.test_winjob
```

A rebased/backported install needs exact reviewed source/hash equivalence if ancestry fails. These commands establish source/dependency/unit behavior only. No claim is made that they ran here or against an installed host.

## Installed receipt before runtime proof

Use the existing controlled deployment contract: exclusive inference AND admission exclusion; zero active work; independently reviewed clean release with green checks; stop service before switching; retain old release/configuration; fresh deployment ID; start and inspect. Do not change frozen worker/model/settings or credentials. A Strata runtime/model change cannot be treated as an incidental rollout.

Safe host inspection commands:
```bash
readlink -f /opt/local-inference-gateway/venv
systemctl show local-inference-gateway.service -p MainPID -p ExecMainStartTimestamp -p FragmentPath -p ExecStart
sha256sum /opt/local-inference-gateway/venv/lib/python*/site-packages/local_inference_gateway/contracts.py /opt/local-inference-gateway/venv/lib/python*/site-packages/local_inference_gateway/app.py /opt/local-inference-gateway/venv/lib/python*/site-packages/local_inference_gateway/worker.py
```

Resolve actual module paths if the install differs. Compare hashes with installed release source/build receipt; do not merely save them. Capture worker executable/interpreter, structured.py hash, checkpoint/quantization/digest, actual context/thinking/seed/output budget and exclusive lane identity. Profile version is unchanged and does not identify corrected admission. Every fresh completion must match task-policy version 2 and receipt deployment ID; historical replay retains old provenance. Repeat process/release checks afterward. Do not print token files or environment contents.

## Zero-generation admission and ordered grammar packet

Set owner-private proof paths outside worktrees. These variables are supplied host paths, not guessed settings or credentials:
```bash
umask 077
export DOC_SUM_C9_GATEWAY_SETTINGS=/private/path/copied-model-settings.json
export DOC_SUM_QUALIFICATION_GGUF=/private/path/frozen-model.gguf
export C9_PROOF_LOCK=/private/path/shared-inference.lock
export DOC_SUM_C9_GRAMMAR_PACKET=/private/path/fresh-proof/grammar-packet.json
cargo test --lib export_actual_decoder_schema_and_independent_shapes -- --nocapture
```

The packet includes 80 independently specified shape cases and decoder_schema_json. Preserve that ordered string; reparsing/reserializing a JSON value can reorder fields. It is one representative control projection, not a complete sentence-projection live probe.

From src-tauri, run each route sequentially:
```bash
export DOC_SUM_C9_BOUNDARIES_ONLY=1
for DOC_SUM_C9_RUNTIME in gateway native; do
  export DOC_SUM_C9_RUNTIME
  export DOC_SUM_C9_SENTENCE_OUTPUT
  DOC_SUM_C9_SENTENCE_OUTPUT=$(mktemp -d /private/path/c9-admission.XXXXXX)
  flock -n "$C9_PROOF_LOCK" cargo test --lib sentence_candidate_admission_or_live -- --ignored --nocapture > "$DOC_SUM_C9_SENTENCE_OUTPUT/proof.log" 2>&1 || exit
  python -c 'import json,os; p=os.environ["DOC_SUM_C9_SENTENCE_OUTPUT"]; r=json.load(open(p+"/results.json")); assert r.get("gate_passed") is True and r.get("admission_only") is True and r.get("actual_calls")==0'
done
```

Require full A/B admission, every control's static containment, each actual sentence/parent-context projection, all four dimensions, 4096 accepted/4097 rejected sizing neighbors and 31/32/33 sentence budget boundaries. Current sentence contract records A 9 sentences/36 calls and B 24/96; compare the actual cost receipts, do not reset the 128-call document cap per parent.

Native converter acceptance/rejection of the 80 cases is still required, as are actual live native/gateway grammar probes with valid and prohibited shapes on sentence projections. No complete reusable live grammar driver is committed in these inspected heads. The sentence runner does not execute grammar probes. Do not invent a passing command or count converter-only results as live evidence. Under the current contract, Strata's documented lack of native grammar decoding is a blocker here.

## Conditional semantic run after ALL prerequisites pass

The accepted sentence contract supersedes the older dimension-only 48-call schedule. Preserve baseline instructions from 4c8b9dc, independent labels and original seeds. No failed replacement-prompt trial is reused. Nine parents A1 through B6 once; source-order sentences; stage/conditions/qualifiers/scope sequentially. Stop the entire experiment at first invalid response, runtime failure or parent-label disagreement. Only after worker-unit success, four original controls three times each. Maximum 180 semantic calls plus separately budgeted 10 actual grammar calls. No warm-up, retries replacing failures, relabeling, scope reductions or prompt changes.

The existing semantic driver is gateway-only and cfg(test):
```bash
unset DOC_SUM_C9_BOUNDARIES_ONLY
export DOC_SUM_C9_RUNTIME=gateway
export DOC_SUM_C9_SENTENCE_OUTPUT
DOC_SUM_C9_SENTENCE_OUTPUT=$(mktemp -d /private/path/c9-sentence-run.XXXXXX)
flock -n "$C9_PROOF_LOCK" cargo test --lib sentence_candidate_admission_or_live -- --ignored --nocapture > "$DOC_SUM_C9_SENTENCE_OUTPUT/proof.log" 2>&1
python -c 'import json,os; r=json.load(open(os.environ["DOC_SUM_C9_SENTENCE_OUTPUT"]+"/results.json")); assert r.get("gate_passed") is True and r.get("development_only") is True and r.get("fidelity_qualified") is False and r.get("actual_calls")==r.get("maximum_semantic_calls")==180'
```

Do NOT run now while grammar prerequisites remain blocked. A successful cargo exit alone is insufficient: the driver records a failure and returns without asserting final success, and admission failure also returns. Inspect results.json; unexecuted dimensions/parents never count as passes.

Retain all raw attempts privately, exact owned membership, relation validity, parent-verdict parity, recorded-reference containment, per-call/sentence/parent/document timings, decoded characters/UTF-8 bytes, selected-passage character totals and available transport provenance. ModelResponse exposes neither completion tokens nor finish reason: report unavailable unless an independently retained transport receipt supplies them. Never infer tokens from characters. Existing runner snapshots do not by themselves verify every completion against deployment identity.

## Remaining release proof

A clean sentence development result permits a separately accepted activation/delivery proposal, not activation. Ordinary-library gate remains. Full A/B proof must exercise DesktopJobManager new/start_pdf with copied installed settings, fresh database, persisted stage/artifact records and reopen through workspace::get_persisted_summary. Require comparison verification actually exercised: gated fallback alone cannot pass. Visual UI acceptance, independent semantic review and locked unseen qualification remain distinct.

Locate the existing app-worker opt-in test and its environment schema at the then-current source before issuing a live command; the sentence runner is not that test. This runbook does not fabricate unseen fixtures, fidelity thresholds or a release command. Exact-head CI/review and explicit operator clearance of discussion_r4196409571 remain mandatory. Neither Strata tests, gateway green tests nor development controls clear PR116.

## Verification of this runbook

Test names/environment variables were read from pinned source; command filters match committed test functions. No local GPU, installed process, credentials or private receipts were available in this session. No inference, deployment, qualification or source test execution is claimed. Runtime code/admission rules are unchanged.
