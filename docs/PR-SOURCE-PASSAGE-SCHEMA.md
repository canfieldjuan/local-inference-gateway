# Bounded source-passage schema support (gateway issue 28)

## Contract

### Root cause

`Generation.validate_generation_shape` and `_validate_supported_schema` in
`contracts.py` deliberately reject `$defs`/`$ref` and cap scalar enums at 100.
DocSum C9 constructs two complete exact-passage enums and refers to them across
its comparison dimensions. Its request therefore fails before dispatch; the
client has already corrected its erroneous assumption that byte admission proves
protocol compatibility. This gateway slice adds the missing capability.

A second origin is `worker.py::_infer`: canonical `encode_json_bytes` sorts every
object key, including schema properties. C9's decoder sequence depends on the
schema's ordered required fields. Request identity and decoder serialization need
different ordering policies; changing canonical identity would break replay.

### Required change surface

- `contracts.py`: one owner for bounded shared-passage validation and decoder
  schema ordering. Permit `$defs` only on an explicit object root, with one or two named string-enum
  leaves, each with 1..8192 nonempty strings of at most 240 characters. Definition
  names are ASCII identifiers of at most 64 characters. References are exact
  single-key local `#/$defs/name` nodes; no siblings, chains, cycles, nested
  definitions or external resolution. At most 16 reference nodes and 32 maximum
  passage values in one generated object, accounting for the product of every
  enclosing array's maxItems and summing properties/alternative branches. All definitions,
  including unused definitions, must pass validation. Existing schema byte,
  depth, total-node and ordinary inline-enum limits still apply.
- `app.py`: add `document.summary.step@2` with an explicit shared-passage policy,
  temperature 0 and the existing 4096 output-token cap. Existing task versions
  cannot dispatch the new schema form. Authorization and health remain scoped
  by exact task grant; no existing credential gains version 2 implicitly.
- `worker.py`: for shared-passage schemas, derive property sequence from each
  object's required array and preserve it through the final wire bytes. Other
  schemas keep existing transport bytes. Canonical identity remains sorted and
  numerically exact. Both primary and fallback transports consume the same
  admitted schema and final serializer.
- Tests: minimal admission and wire-order regressions; valid/invalid references,
  empty/mixed definitions, both sides of every cap, ordinary task rejection,
  authentication/authorization before store and worker effects, concurrent exact
  replay, changed-schema identity conflict, output membership rejection, expiry
  and acknowledgement; opt-in real configured-worker proof with private receipts.
- README and the earlier document-task contract link to this capability contract.

### Explicit non-scope

No arbitrary reference graph, enum truncation, schema inlining, general cap raise,
model/context/temperature changes, new thinking experiment, model promotion,
credential/configuration mutation, installed gateway deployment, client cutover,
storage migration, lifecycle rewrite, dependency update, PR25 disconnect changes,
or unseen-document tuning. DocSum PR116 remains held and its safe fallback remains
in place until a reviewed gateway deployment and coordinated client proof.

### Assumptions and blockers

Main `2fee4b5a0fb6d0e76df51e06f0c4bdbefc7e0adc` and installed `bb0b60d` have
these schema and serializer boundaries. Shared definitions must remain enum
leaves: this makes recursion and resolver I/O impossible and bounds traversal.
The configured worker is the qualification target for transport proof, not an
implicit claim of C9 semantic fidelity. A model failure is retained and reported;
it must not trigger relaxed parsing, prompt tuning or repeated attempts until pass.

### Verification plan

1. Run the repository's baseline local gate before runtime edits.
2. Fail first: a minimal valid shared-passage schema must be admitted but currently
   fails on `$defs`; decoder-wire required order must fail under sorted transport.
3. Implement the stated origin boundaries and run focused contract/app/worker tests.
4. Run the repository-required full pytest, Ruff lint/format, mypy and package build.
5. Freeze the code and run one isolated gateway proof against the configured
   loopback worker under the inference lock, using synthetic/public inputs. Keep
   raw outputs outside worktrees with owner-only permissions; log metadata only.
6. Cold diff and boundary/effect audit, then publish for independent review.
   Deployment, coordinated DocSum app proof and unseen fidelity remain separate.

### Admission revision: array multiplication

The first bounded-leaf implementation admitted nested arrays with 8 by 8
passage references. The regression failed because no ValidationError was raised.
Leaf-only references prevent graph recursion, but do not alone bound repeated
large-enum validation. The same schema traversal now carries array multiplicity
and enforces a total of 32 possible passage values, sufficient for C9's four
dimensions with two lists of four spans. No downstream output filter substitutes
for this origin admission bound.

## Implementation summary

Implementation commit `89434d1977a23a33897577f70b301ca73b313af5` adds the explicit
version-2 policy, bounded shared leaf definitions, and required-field decoder
ordering. Request digest serialization, output validation, installed credentials,
service configuration and DocSum's version-1 fallback remain unchanged.

### Reproduce, isolate, explain, fix, prove, prevent regression

1. **Reproduce:** the minimal admission test rejected a valid shared schema on
   unsupported `$defs`; the wire test received alphabetical properties instead
   of its required sequence. Both failed before the origin changes.
2. **Isolate:** `Generation.validate_generation_shape` owns admission;
   `OllamaWorker._infer` owns the final worker bytes. The fixtures need no client
   documents and reproduce the two independent boundaries.
3. **Explain:** the previous schema subset intentionally had no reference support;
   the transport reused canonical identity sorting for an ordered decoder schema.
   These predate this slice. This is not a model-label repair.
4. **Fix:** shared definitions are validated once at admission, then exact local
   references consume that validated mapping. The worker derives order from the
   admitted required arrays and preserves it through the final encoder. No enum
   clipping, inlining, downstream rescan or schema bypass is added. There are no
   earlier gateway passage-specific patches to remove; the generic admission and
   serialization owners are changed directly.
5. **Prove:** the final local gate reports **220 passed, 2 skipped**, Ruff lint and
   format pass, mypy passes for 7 source files, and sdist/wheel build succeeds.
   All four retained C9 schemas are admitted with exact frozen decoder-byte
   parity. Local Python is 3.13.11; CI covers the configured 3.12 environment.
   The real-worker proof is prepared but unrun because another application owns
   the GPU. Local success is not a live transport or semantic qualification.
6. **Prevent regression:** public minimal tests cover admission, final wire
   order, definitions/references and all new bounds, authorization before durable
   work, concurrent exact replay, changed-schema collision, expiry, ACK cleanup,
   and rejection of foreign passage output by the actual worker validator.

During implementation, three defects in the new code were caught locally:
missing array multiplication, a nullable branch losing that count, and annotation
data being treated as schemas. Their failing evidence is retained and each has a
regression. The mock stream fixture and a static type annotation were also
corrected. No published follow-up rounds were needed for these local findings.

### Boundary and effect evidence

`boundary-probe: valid large enums and exact cap values pass; empty, mixed,
falsy, over-cap, dangling/external/cyclic/sibling references and unauthorized
task versions fail; nested array/object/nullable paths retain the comparison
budget; worker output consumes the admitted schema and rejects foreign text.`

`effect-trace: admit C9 passage schemas and preserve evidence-before-relation
order | Generation admission plus OllamaWorker final serialization | minimal
fail-before/pass-after regressions and four retained decoder-byte matches.`

The proof procedure is in README's retained source-passage transport section.
Frozen qualification must not be presented as model promotion or application
cutover. Evidence aliases and SHA-256 receipts are published in the PR; raw
prompts and outputs remain private outside worktrees.

## Cold diff audit

- `app.py:54,68,285`: explicit policy capability and pre-admission rejection;
  exact grants, no-effects rejection and health/lifecycle tests cover it.
- `contracts.py:111,216,249,281,461`: shared-leaf bounds, recursive comparison
  budget and separate decoder order; admission/boundary/annotation/identity
  regressions cover these owners. Ordinary schemas retain their existing limits.
- `worker.py:153,187`: use the admitted ordering policy at final serialization;
  actual HTTP payload and foreign-output tests cover both directions.
- `tests/test_passage_schema.py:25`: public minimal and sibling boundary tests.
- `tests/test_live_passages.py:94`: opt-in real-worker path, private receipts,
  replay and ACK; skipped until the exclusive GPU lane is available.
- `README.md`: capability and operational proof instructions only.
- `docs/PR-DOCUMENT-SUMMARY-TASK-POLICY.md:3`: historical version-1 scope and link.
- This contract records the new capability, resource revision, evidence and limits.

No dependency, deployment, credential, database, client, prompt, model, or unrelated
PR25 changes appear in the diff. This implementation audit is not an independent
PR review.

## Gap audit

NOT DONE. Real-worker transport proof, remote CI and independent exact-head review
remain. The implementation is ready for a draft PR while GPU ownership is pending.
Installed deployment, client negotiation/cutover, full-document app proof and
unseen fidelity are subsequent gates; DocSum PR116 remains held.
