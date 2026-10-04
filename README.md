# ApiAtomicProbe

ApiAtomicProbe records concurrent HTTP retries against an explicitly disposable
order/balance application and checks whether one finite history can obey its
declared idempotency model. Native SQLite accounts, orders, effects and inbox
rows at every captured prefix must agree with one admissible real-time linearization; identical HTTP
responses alone do not establish that only one order was created.

```shell
python -m pip install .
apiatomicprobe pilot new-pilot
python -I examples/verify_history.py new-pilot
apiatomicprobe analyze new-pilot/race/concurrent
```

The registered CLI returns0 for PASS,3 for COUNTEREXAMPLE,4 for UNKNOWN and2 for
controlled input refusal. Pilot returns0 after producing all cases, including
intentional target counterexamples. SDK `collect(url, database, nonce, plan,
output, authorized=True)` and `analyze(output)` provide the same evidence and
actions. See [DESIGN](docs/DESIGN.md) for the fixed adapter and plan format.

The actual pilot consumes business CSV, submits real overlapping HTTP orders,
then captures all business tables in one read-only transaction after server
quiescence. It tests an atomic SQLite inbox, a real check-then-act race, a tenant
scope defect, early key expiry, dropped ACK after commit, server work after a
client timeout, search exhaustion, and a no-race control. Each case also runs
the same operations serially. Full request/response bodies, SHA256, monotonic
call/return observations and native SQL request counters remain available.
The standalone consumer imports no project code and enumerates all legal
orders/pending choices for these small histories. Preparation, SQL witnessing,
fsync, control calls and search add real costs; timings are local lab results.

Version0.1.0 initially supports only `order-lab/v1`, a dedicated loopback HTTP
origin and a directly readable SQLite witness database. External adapters must
implement the same contract and explicit session nonce. No redirects, external
Internet targets, arbitrary SQL, production credentials, TTL model beyond the
declared test retention window, or hidden effects are supported. Submit only to
an authorized disposable deployment. Collection intentionally changes that
application's orders/balances; the offline analyzer never submits HTTP calls.

PASS means this captured finite history has a valid explanation. UNKNOWN means
the supported evidence/search budget is insufficient. A counterexample is the
earliest failing **captured wave prefix**, with its own native witness; it is not
an arbitrary-subset global minimum or a guarantee about future requests. Missing
or unsupported observations never prove correctness. The host, target adapter's
quiescence promise and witness truth are trusted. Records are integrity checked,
not cryptographically authenticated against an attacker rewriting everything.

[Stripe](https://docs.stripe.com/api/idempotent_requests) already specifies
idempotency/retry/result retention, and
[Porcupine](https://github.com/anishathalye/porcupine) already provides mature
linearizability checking. This tool combines a small executable model with real
concurrent adapter receipts and native business effects. It does not invent
idempotency, replace Porcupine/Jepsen, emulate all Stripe semantics, or promise
network exactly-once. MIT. Customers, revenue and production adoption unknown.
