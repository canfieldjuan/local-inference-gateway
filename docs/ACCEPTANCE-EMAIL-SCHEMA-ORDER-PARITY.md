# Acceptance record: email decoder order contract

Accepted contract revision: `16e34b8591ebaf2116a06298d5085ef0248280ef`.

Accepted document: `docs/PR-EMAIL-SCHEMA-ORDER-PARITY.md`.

SHA-256 of the complete accepted document bytes:
`335999164cd973b6a6706d8dbe2602e31a4fbccfba1b4834a2f652abe6108738`.

## Operator acceptance

Juan's direct message in the implementation session on October 31, 2026,
quoted verbatim:

> I accept PR 37’s contract at 16e34b8591ebaf2116a06298d5085ef0248280ef. Let’s get the remaining review cleared. No live runs yet.

Oversight separately relayed Juan's direct acceptance, verbatim:

> accept 37 at 16e34b8

[Oversight acceptance relay](https://github.com/canfieldjuan/local-inference-gateway/pull/37#issuecomment-6545856907)
binds that short revision to the full accepted commit above.
[Oversight contract PASS](https://github.com/canfieldjuan/local-inference-gateway/pull/37#issuecomment-6545718915)
applies to that same accepted revision.

## Preservation and remaining gates

This record is additive. The accepted document is byte-identical to its contents
at the accepted commit; its historical draft/hold wording is preserved rather than
rewritten. This record establishes the subsequent acceptance without changing any
contract term, source file, test, dependency, configuration or runtime behavior.

The acceptance-record commit requires oversight delta review, connector review,
exact-head green CI, reconciled findings and no unresolved review threads before
any guarded merge commit. Acceptance alone does not clear those gates.

No live inference, replay, deployment or production change is authorized. PR314's
independent requirements remain unchanged. PR36 stays parked at
`dd41cb54ab5dab0ce3b46337abfec41edb117951`; its code lacks the accepted eligibility
rule and is not approved for reuse by this acceptance record. Any later
implementation is a separate PR reviewed against the accepted contract.
