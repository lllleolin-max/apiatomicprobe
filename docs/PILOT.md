# Local actual application pilot

The fixture is a real HTTP service committing SQLite orders/balances/inbox and
effect ledger. Business CSV supplies order requests; native full-table consumer
checks complete output and account/ledger conservation. Four target modes are
explicit: atomic implementation, intentionally faulty check-then-act target,
tenant-global key target, and early-TTL target. Faulty targets are mechanisms
under test, not deliberately seeded checker defects.

For each fixed history, concurrent waves and singleton-wave serial replay use
the same operations, payloads, fault knobs, balances and retention promise.
Serial operation counts equal concurrent counts, control counts can differ.
SQL requests BLOB rows independently prove actual server arrivals; durable client
intent counts are not confused with server arrival. Normal small histories and
no-race use cases preserve equivalent conclusions and possible added overhead.

No pricing model, customer savings or revenue is extrapolated. Parenthesized
prepare/collection timings include real SQLite locking, witnessing, HTTP
control, fsync, native data copies and bounded search; they are not universal
throughput claims. The correct atomic service remains valid with a lost ACK or
server completion after a client timeout because its terminal SQL is checked.
In the race, serial controls may pass while concurrent requests leave two orders
for one identity; checking response equality alone is inadequate evidence.

Porcupine is acknowledged rather than portrayed as lacking pending operations.
The initial baseline includes an independent factorial oracle for all16 pilot
policy histories. No Go/Porcupine execution is claimed unless recorded later.
