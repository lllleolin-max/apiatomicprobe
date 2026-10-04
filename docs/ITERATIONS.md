# Review and correction history

Initial feature implementation is one complete runnable baseline. Protocol,
model, raw capture, real targets, SDK/CLI, independent consumer and CI configuration
are built before its first commit. Initial tests or feature assembly do not
count as defect-fix cycles. No real post-baseline correction is recorded yet.
At least three genuine supported counterexamples must be independently retained
and fixed; no-finding reviews, docs/harness changes and target fixture defects do
not satisfy that requirement.

## Round1: native witness clock omitted non-final observations

Before `4d07330c452cdc9fd6bbac6a912f18754ccd3c3c`, exact ordinary installed
`probe_witness_clock.py` executed two real concurrent requests: index0 delayed
800ms, index1 returned early. Only the witness captured_ns was changed to between
the early observation and later real response. Raw request/response bytes,
operation clocks, native state and plan were unchanged. SDK and actual registered
CLI incorrectly accepted PASS/0, even though the witness precedes a completed
operation. Root first suggested this hypothesis; implementer confirmed it on the
immutable wheel and retained real failure.

Correction validates every control request/response and causal clock, the initial
quiesce bound, all operation observations, resume-before-invocation, and the
terminal quiesce-before-native-capture bound. The unsupported clock refuses via
InputError/CLI2. Targeted actual-child witness nanosecond+1/equality/-1 verifies
the causal boundary. Unchanged original probe after ordinary wheel is retained
outside repo with exact after SHA and full logs.

## Round2: completion declaration was not parsed

Before first baseline4d (also replayed after round1), actual one-order collection
produced its SQL effect and legitimate completion operations1/waves1/end_ns.
`probe_complete_count.py` changed only complete.operations to0, preserving every
real clock/body/state and all other receipt bytes. SDK/registered CLI still
returned PASS/0 because only the marker's existence was checked. Root suggested
the review hypothesis, implementation independently confirmed ordinary-wheel
FAIL. No request timing is invented by this original probe.

Correction reads strict completion schema/types, matches declared total calls
and waves, requires end_ns after all checked native captures, and keeps an
over-total-time completion UNKNOWN. An interrupted malformed marker also stays
UNKNOWN. Actual collection-derived tests cover counts±1/bool and hypothetical
completion time deadline±1/equality without portraying edited timing as a lab
speed result. The original unchanged before/after probe is recorded externally.

## Round3: known unsupported protocol was mistaken for lost ACK

Before baseline4d (and round2 immutable ordinary wheel),
`probe_response_cap.py` submits a real order with max_response_bytes1. The actual
native cached201 body is72 bytes. Client detects its known oversized HTTP
Content-Length and records InputError framing/byte allowance unsupported. No
record is modified, and an order actually commits. Reanalysis incorrectly treats
this known unsupported-protocol observation as mere network ACK loss, invents a
legal201 from native SQL, and returns PASS/CLI0. The original unsupported-response
UNKNOWN promise is violated; this finding was implementation self-review.

Correction keeps known InputError/framing parser failures UNKNOWN rather than
an unconstrained pending response. Genuine timeout/socket ACK loss remains
pending and can still PASS with a matching witnessed completion. Completed
responses cannot also claim a failure. Actual response bytes-1/equal/+1 tests
and unchanged original72-byte/1-cap ordinary-wheel probe verify the distinction.

## Round4: independently plausible prefixes had incompatible pending choices

After the three input/observation fixes, deeper model review found a core issue.
`probe_joint_witness.py` uses a genuinely faulty HTTP application: first dropped
ACK still commits order1 amount100; its native wave0 witness records that order.
The second same-key amount200 request wrongly rewrites the existing order,
inbox/effect/balance and returns201, with conservation preserved. No client
receipt/timestamp/body is edited. Baseline ordinary wheel (and round3 replay)
incorrectly PASS: first prefix explains pending as completed100, final prefix
changes that same pending choice to omitted and calls the second request a fresh
creation200. One history cannot make both native views true under the model.

Correction places every captured native view in the **same** linearization as
a read event with its quiesce-return/capture interval. Past reads constrain the
state and pending disposition across later prefixes. Operation and read event
real-time precedence share one node budget. The standalone factorial oracle also
enumerates mandatory read events without importing the product search. The
original real-target probe now requires COUNTEREXAMPLE/CLI3; ordinary wheel
before/after and all prior probes are preserved externally. The model unit test
is additional coverage, not the original discovery evidence or a separate round.
