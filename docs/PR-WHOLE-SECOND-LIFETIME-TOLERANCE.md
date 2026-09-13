# Whole-second request lifetime compatibility

### Contract

Root cause:

- Gateway protocol v1 requires `request_expires_at` to be a whole-second RFC3339 timestamp, while
  Document Summarizer deliberately rounds a configured lifetime upward so serialization cannot
  shorten it. A 900-second request created at a fractional second can therefore arrive with a
  remaining lifetime just under 901 seconds.
- The gateway currently compares that remaining lifetime directly with its 900-second configured
  limit. It rejects the valid rounded request as `invalid_request` before durable reservation, so
  the merged Document Summarizer client cannot execute through the installed gateway.

Required change surface:

- Interpret the configured maximum as the unrounded client lifetime and derive the latest admitted
  whole-second expiry by rounding `now + request_max_lifetime_seconds` upward.
- Accept an expiry at that derived whole-second boundary and reject the immediately following whole
  second.
- Add a focused boundary test that proves the fractional valid side and the next-second invalid
  side without dispatching rejected work.

Explicit non-scope:

- No task policy, worker, credential, TLS, storage, acknowledgement, fallback, application setting,
  timeout, or configured lifetime change.
- No widening by a fixed extra second after the wire timestamp has already been normalized; the
  tolerance exists only because the protocol represents expiry at whole-second precision.
- No Document Summarizer source change. Its upward rounding preserves the already-reviewed full
  lifetime contract.

Assumptions and blockers:

- Client and gateway clocks are meaningfully synchronized; network delay only reduces the remaining
  lifetime. Cross-machine clock synchronization remains an operator responsibility.

Verification plan:

- Run the focused expiry policy tests, the full pytest suite, Ruff, mypy, package build, and
  whitespace validation.
- Reinstall/restart the merged gateway only after review, then rerun the exact-current Document
  Summarizer client proof through the production gateway runtime.

### Acceptance criteria

1. With a fractional gateway clock, a whole-second expiry equal to the ceiling of
   `now + configured maximum` is admitted and reaches the worker.
2. The immediately following whole-second expiry returns `invalid_request` and does not reach the
   worker.
3. Already-expired and exact-now requests retain their current rejection behavior.
4. Request identity, configured lifetime, task policy, durable lifecycle, and worker behavior are
   unchanged.

### Implementation summary

- The gateway now derives the latest admissible wire expiry by adding the configured lifetime to
  its current clock and rounding that result upward only when it contains a fractional second.
- Expiries at that protocol-normalized boundary are admitted; later expiries still fail as
  `invalid_request` before dispatch.
- A focused route-level boundary test exercises both sides from a fractional clock and retains the
  existing expired, exact-now, and true-901-second negative controls.

### Cold diff audit

- `src/local_inference_gateway/app.py:265` keeps the existing expired-request rejection, while
  `src/local_inference_gateway/app.py:267` derives and enforces the whole-second maximum before task
  policy checks or dispatch.
- `tests/test_app.py:580` retains the exact-clock negative boundaries, and
  `tests/test_app.py:609` proves a fractional 900-second ceiling reaches the worker while the next
  whole second is rejected without a second worker call.
- Contract match: the diff changes only expiry admission and its focused test. It does not change
  the configured lifetime, tasks, worker, storage, authorization, TLS, or application code.
- Boundary probe: expired, exact-now, exact-clock 901 seconds, fractional normalized maximum, and
  fractional normalized maximum plus one second are all exercised through the production route.
- Untraced or forbidden changes: none identified.

### Gap audit

DONE for the gateway compatibility blocker. Reinstalling the merged gateway and rerunning the exact
Document Summarizer client remain the next operational-proof step, not an unmerged runtime claim.

- Focused expiry proof passed with 2 selected tests.
- Full pytest passed with 204 tests and 1 intentional live-test skip.
- Ruff lint and format, mypy over 7 source files, package build, and whitespace validation passed.
