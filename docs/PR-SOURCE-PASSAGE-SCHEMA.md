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
  schema ordering. Permit root-only `$defs` with one or two named string-enum
  leaves, each with 1..8192 nonempty strings of at most 240 characters. Definition
  names are ASCII identifiers of at most 64 characters. References are exact
  single-key local `#/$defs/name` nodes; no siblings, chains, cycles, nested
  definitions or external resolution. At most 16 reference nodes. All definitions,
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

## Implementation summary

Pending implementation.

## Cold diff audit

Pending final diff and evidence.

## Gap audit

NOT DONE. Implementation, tests, live transport proof and review remain.
