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
