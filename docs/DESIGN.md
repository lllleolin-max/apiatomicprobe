# Fixed supported protocol and finite search

`order-lab/v1` uses POST /orders with X-Lab-Nonce, X-Tenant, Idempotency-Key and
canonical UTF8 JSON {amount,sku}. ASCII identifiers1..64, amount1..1000000.
Its permanent **session-window** model keys by (tenant,key), compares canonical
semantic body SHA256, and caches exact201 order response or402 insufficient
funds response. Different body409 key_conflict does not mutate state. Fresh201
atomically inserts a unique order and negative ledger effect, subtracts tenant
balance, and stores its response in inbox. Model includes all original business
rows, not just aggregate sums. This limited cash/order policy is not Stripe's
complete result/error/concurrency/expiry model.

Native schema lives in lab.py: metadata(protocol,nonce,retention_seconds),
accounts(tenant,balance), orders(order_id,tenant,idem_key,sku,amount),
effects(order_id,tenant,delta), inbox(tenant,idem_key,body_sha256,status,response,
created). created is target TTL telemetry, outside the permanent-session
business model. requests records every actual authorized request and raw BLOB
body for independent counters. A reader opens mode=ro/query_only, BEGIN, reads
the four complete business tables in one transaction, bounded1000 rows/table
and1MiB combined material. The dedicated endpoint /quiesce prevents new orders
and waits for active handlers before acknowledgement; /resume opens the next
wave. Session exclusive ownership and truthful quiescence are adapter premises.
Do not point this protocol at a shared production database or mixed writers.

Plans have exactly format='apiatomicprobe-plan/v1', waves, timeout_ms1..10000,
max_duration_ms1..60000, max_response_bytes1..1048576 and search_nodes1..1000000.
Each wave {pause_ms:0..1000,operations:[...]} holds nonempty operations with
tenant,key,sku,amount,drop_ack:boolean,delay_ms0..1000. At most12 operations,
12 waves. Fault knobs are explicit lab HTTP headers and do not change semantic
body identity. Only explicit http://127.0.0.1:port or http://[::1]:port origins;
no environment proxies, DNS resolution, redirect, URL credentials or fragments.
Source database and -wal/-shm/-journal/output ancestry collisions refuse before
mkdir or dispatch. The SDK authorization boolean and session nonce are required.

Client monotonic_ns invocation is captured before fsynced raw intent and HTTP
dispatch. Actual fully consumed HTTP response gets response_ns; timeout/socket
loss is pending, with a separate observation time. An HTTP close does not finish
the business operation. Independent SQL witness time after quiescence bounds
every pending operation: it may be omitted or legally complete at most once,
but cannot invent state outside the terminal accounts/inbox/orders/effects.
Completed return before another call constrains real-time order. Every captured
prefix is checked from the same initial state with **all** its prior SQL views
as mandatory read events in one shared linearization. Pending choices cannot
change incompatibly between individually plausible prefix explanations.
Control receipts are independently length/hash/identity checked: initial quiesce
precedes the initial view, resume precedes each wave invocation, every operation
observation precedes terminal quiesce, and its actual return precedes the SQL
capture. No final-index shortcut replaces the maximum of a concurrent wave.

Search is memoized exhaustive DFS over eligible operations and native read events, legal pending
alternatives and omitted pending branches. A completed operation must match its
actual complete response bytes. Unknown pending new order ids come only from
matching native terminal orders. Exact final full-table equality is required.
Every explored node consumes one declared search node; exhaustion returns
UNKNOWN, never an invalid-history proof. Work scales exponentially; no claim of
Porcupine's P-compositionality or large-cluster scalability. The standalone
example oracle separately enumerates pending subsets/permutations and uses
different state-update code, max8 operations.

Files are create-exclusive, individually flushed/fsynced. Interrupted runs keep
raw intents/responses and are UNKNOWN without complete quiesced terminal proof.
There is no retry after collector crash: rerun creates a new disposable session.
Byte/time limits are cooperative, not RSS/CPU/process or power-loss guarantees;
control drains may exceed per-request observation timeout. Existing captured
prefix counterexamples remain useful if later collection is incomplete. A PASS
requires the complete declared schedule and all terminal witness prefixes.
Completion records have strict operations/waves/end_ns members, integer counts
equal to the full preregistered plan and client end time after captures. Marker
existence alone is insufficient; malformed partial markers and over-allowance
completion are UNKNOWN, contradictory typed counts refuse.
An insufficient supported response (unexpected status/encoding) is UNKNOWN.
Known unsupported framing/byte-cap/parser observations are also UNKNOWN; they
are not erased into the transport-lost-ACK branch to invent a supported response
from SQL. A genuine network timeout/closed socket retains its pending semantics.

Trusted files/host may be corrupted detectably, but this is not a hostile
filesystem race sandbox or a signature-based evidence authenticity service.
Mutation permission applies only to the explicitly disposable application;
native witnessing itself never writes business tables. Final reports are
reviewable receipts, not future API guarantees or arbitrary effect discovery.
