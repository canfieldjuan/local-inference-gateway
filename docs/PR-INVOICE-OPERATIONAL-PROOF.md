# Invoice Processor Operational Gateway Proof

### Contract

Root cause:

- The merged gateway advertises and executes `invoice.extract.batch@1`, and the merged Invoice
  Processor has an explicit gateway client, but the repeatable network proof still models only the
  Email Watcher and Document Summarizer credentials.
- The existing proof therefore cannot establish that the third application has an independent
  credential, sees only its task, is refused from every other task, or completes the same
  replay/acknowledgement lifecycle through the real HTTPS process.
- Unit coverage and a task-policy merge are not operational evidence that the installed Ollama
  worker, HTTPS gateway, and application client compose successfully.

Required change surface:

1. Extend the existing operational proof with one required owner-private Invoice Processor token.
2. Add `invoice.extract.batch@1` to the synthetic request catalog with temperature 0.0 and a small
   closed JSON response schema. Do not use a customer invoice or promote model quality from this
   transport proof.
3. Require authenticated health to expose exactly the task set granted to each of the three
   credentials.
4. Generate the complete cross-credential denial matrix: each credential is refused from every
   task it does not own before worker dispatch.
5. Exercise invoice completion, exact replay, and acknowledgement through the same HTTPS process as
   the existing tasks.
6. Preserve the document-only restart proof; this slice proves the new credential/task path and does
   not multiply restart scenarios.
7. Update the runbook command and claims to the implemented four-task, three-credential proof.
8. After the proof tool passes against real Ollama, exercise the merged Invoice Processor gateway
   client with a synthetic generated PDF and owner-private token/CA files.

Explicit non-scope:

- No gateway runtime, worker, task-policy, schema language, queue, lifecycle, TLS, firewall,
  database, or credential-provisioning mechanism change.
- No Email Watcher or Document Summarizer application change.
- No Invoice Processor prompt, extraction schema, ledger, Connect provider, UI, packaging, or
  direct-loopback fallback removal.
- No customer document, model benchmark, model promotion, fallback runtime, private-LAN deployment,
  system-service installation, or Windows proof.

Assumptions and blockers:

- `qwen3-30b-a3b:latest` is installed in the already-running loopback Ollama process.
- Development proof credentials and certificates are generated into an owner-private temporary
  directory and are never printed or committed.
- The live application proof may reveal a client/provider contract defect; such a defect is fixed in
  the owning repository, not hidden by weakening the proof.

Verification plan:

- Focused tests prove exact health lists, all four successful task paths, the full denial matrix,
  three distinct credential requirement, and unchanged restart behavior.
- Boundary probes reject a missing/reused invoice credential and reject every unowned task in both
  directions.
- Run Ruff lint/format, mypy, full pytest, package build, and diff whitespace checks.
- Run the four-task proof against a temporary loopback HTTPS gateway backed by the installed Ollama
  model using synthetic text only.
- Run the merged Invoice Processor CLI against the same gateway using a generated synthetic invoice
  PDF and confirm successful output plus gateway acknowledgement without printing private inputs,
  generated content, or credentials.

### Acceptance criteria

1. Proof startup requires three distinct owner-private credentials.
2. Email health exposes only `email.analyze@1` and `email.schedule.extract@1`; document health exposes
   only `document.summary.step@1`; invoice health exposes only `invoice.extract.batch@1`.
3. Each of the four tasks completes, exact replay returns the same result without duplicate worker
   work, and acknowledgement removes retained output.
4. Every credential/task pair outside the declared ownership sets returns the stable forbidden
   envelope.
5. The existing document restart prepare/reconcile path remains unchanged and usable with the added
   required invoice credential.
6. The runbook invokes the proof with all three token files and describes four tasks without
   exposing private material.
7. A real loopback HTTPS/Ollama run emits only bounded operational status events and completes all
   four synthetic tasks.
8. The merged Invoice Processor gateway client processes a generated synthetic invoice through the
   authenticated gateway and acknowledges the completion after its output is delivered.

### Implementation summary

- Added the Invoice Processor credential and `invoice.extract.batch@1` to the existing operational
  proof without changing gateway runtime behavior.
- Replaced the partial hand-written denial list with the complete complement of each credential's
  declared grants across the four-task catalog.
- Updated exact credential-scoped health expectations and required three distinct token files.
- Updated the runbook commands and claims to the three-credential, four-task proof.
- Exercised the proof through a real temporary loopback HTTPS gateway backed by the installed
  `qwen3-30b-a3b:latest` Ollama model using synthetic text only.
- Exercised merged Invoice Processor commit `6007096` with a generated synthetic PDF through that
  gateway; its private JSON output validated as `InvoiceRecord`, and the gateway ledger showed its
  result acknowledged with ciphertext cleared.

### Cold diff audit

- `scripts/prove_operational_gateway.py` adds one required private token, one synthetic invoice task,
  one exact health expectation, one successful lifecycle, and the generated cross-scope denial
  complement. It does not alter the gateway server or worker.
- `tests/test_operational_proof.py` adds the third fixture credential, exact invoice health/grant
  behavior, all eight forbidden credential/task pairs, duplicate-token rejection, and output
  redaction while preserving the document restart path.
- `README.md` passes the invoice token to normal and restart proof commands and now describes the
  implemented four-task proof.
- Untraced or forbidden changes: none.

### Gap audit

DONE for the Invoice Processor operational gateway proof.

- Focused proof tests: 16 passed.
- Full local suite: 155 passed and 1 intentionally skipped live marker.
- Ruff lint and format, mypy over 7 source files, source distribution, wheel build, and diff
  whitespace passed.
- Live proof: health available; all eight forbidden pairs refused; all four tasks completed, replayed,
  and acknowledged through loopback HTTPS against Ollama.
- Merged-client proof: generated synthetic invoice output was schema-valid and owner-private; the
  gateway ledger contained two acknowledged `invoice.extract.batch` requests with both retained
  results cleared (one proof-tool request and one real Invoice Processor request).
- Model quality promotion, permanent appliance deployment, application-default cutover, customer
  data, Windows, and signing remain deferred exactly as contracted.
